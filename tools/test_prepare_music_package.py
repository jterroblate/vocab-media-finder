"""Offline package behavior: privacy boundaries, paired skills and portable core."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from prepare_music_package import CORE_FILES, MUSIC_FILES, MUSIC_PATH, prepare_music_package


ROOT = Path(__file__).resolve().parents[1]


class MusicPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "public-repo"
        self.music = self.repo / MUSIC_PATH
        self.music.mkdir(parents=True)
        # These are allowlisted public source files, not local-client data.
        for name in MUSIC_FILES:
            original = ROOT / MUSIC_PATH / name
            if original.is_file():
                destination = self.music / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, destination)
        self.write("SKILL.md", '---\nname: vocab-video-finder\ndescription: "Video skill"\nmetadata:\n  version: "3.0.0"\n---\n\n# Video\n')
        self.write("LICENSE", (ROOT / "LICENSE").read_text())
        self.write("NOTICE", (ROOT / "NOTICE").read_text())
        for name in CORE_FILES:
            self.write("scripts/" + name, (ROOT / "scripts" / name).read_text())
        self.write("scripts/demo.py", 'print("synthetic demo")\n')
        self.write("companions/music-preferences-import/SKILL.md", '---\nname: music-preferences-import\ndescription: "Music preference candidates"\nmetadata:\n  version: "0.3.0-experimental"\n---\n\n# Music\nUse local cache adapters.\n')
        self.write("companions/music-preferences-import/adapters/workbuddy/metadata.json", json.dumps({
            "display_name": "音乐偏好", "display_name_en": "Music Preferences",
            "description_zh": "音乐元数据与词汇候选", "description_en": "Music metadata and vocabulary candidates",
            "author": "Contributors"}, ensure_ascii=False))
        for relative in (".env", "private.sqlite", "trial-output/profile.json", "student/preferences.json",
                         "companions/music-preferences-import/output/profile.json",
                         "companions/music-preferences-import/secrets/cookie.json",
                         "companions/music-preferences-import/lyrics/full-song.txt",
                         "companions/music-preferences-import/real-user-words.json",
                         "companions/music-preferences-import/local-cache.sqlite",
                         "companions/music-preferences-import/__pycache__/song.pyc"):
            self.write(relative, "PRIVATE_MUSIC_MARKER_MUST_NOT_SHIP\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def package(self, name="music", **options):
        destination = self.base / name
        result = prepare_music_package(self.repo, destination, **options)
        return destination, result

    def test_music_allowlist_and_core_provenance(self):
        destination, result = self.package()
        self.assertEqual(result["layout"], "music")
        self.assertFalse(result["private_data_included"])
        self.assertTrue((destination / "SKILL.md").is_file())
        for name in CORE_FILES:
            self.assertEqual((destination / "core" / name).read_bytes(), (ROOT / "scripts" / name).read_bytes())
        self.assertTrue((destination / "core/LICENSE").is_file())
        self.assertTrue((destination / "core/NOTICE").is_file())
        provenance = json.loads((destination / "core/SOURCE.json").read_text())
        self.assertEqual(provenance["license"], "MIT")
        self.assertEqual(provenance["video_skill_version"], "3.0.0")
        for relative in destination.rglob("*"):
            if relative.is_file():
                self.assertNotIn(b"PRIVATE_MUSIC_MARKER", relative.read_bytes())
        self.assertFalse((destination / "output").exists())
        self.assertFalse((destination / "local-cache.sqlite").exists())

    def test_workbuddy_is_single_music_root_and_uses_music_metadata(self):
        destination, result = self.package("workbuddy.zip", host="workbuddy", author="Public Author")
        self.assertEqual(result["format"], "zip")
        with zipfile.ZipFile(destination) as archive:
            self.assertIn("SKILL.md", archive.namelist())
            self.assertIn("core/word_families.py", archive.namelist())
            self.assertNotIn("music-preferences-import/SKILL.md", archive.namelist())
            text = archive.read("SKILL.md").decode()
        fields = {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
                  for line in text.split("---", 2)[1].strip().splitlines()}
        self.assertEqual(fields["name"], "music-preferences-import")
        self.assertEqual(fields["display_name"], "音乐偏好")
        self.assertEqual(fields["author"], "Public Author")
        self.assertEqual(fields["version"], "0.3.0-experimental")
        self.assertEqual(set(fields), {"name", "display_name", "display_name_en", "description",
                                      "description_zh", "description_en", "author", "version"})

    def test_source_is_a_clean_readable_music_zip(self):
        destination, result = self.package("source.zip", host="source")
        self.assertEqual(result["format"], "zip")
        with zipfile.ZipFile(destination) as archive:
            self.assertIn("run_music_trial.py", archive.namelist())
            self.assertIn("test_words.json", archive.namelist())
            self.assertIn("SKILL.md", archive.namelist())
            self.assertFalse(any(name.endswith(".sqlite") for name in archive.namelist()))

    def test_pair_has_two_skill_roots_and_no_parent_data(self):
        destination, result = self.package("pair.zip", host="codex", layout="pair", output_format="zip")
        self.assertEqual(result["layout"], "pair")
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
            self.assertIn("vocab-video-finder/SKILL.md", names)
            self.assertIn("music-preferences-import/SKILL.md", names)
            self.assertIn("music-preferences-import/core/video_sources.py", names)
            self.assertNotIn("SKILL.md", names)
            self.assertNotIn("private.sqlite", names)
            for name in names:
                self.assertNotIn(b"PRIVATE_MUSIC_MARKER", archive.read(name))
            manifest = json.loads(archive.read("bundle-manifest.json"))
        self.assertEqual(manifest["components"], {"vocab-video-finder": "3.0.0", "music-preferences-import": "0.3.0-experimental"})

    def test_workbuddy_pair_describes_its_distinct_import_contract(self):
        _, result = self.package("pair-workbuddy.zip", host="workbuddy", layout="pair")
        self.assertIn("not a single-skill", result["host_note"])

    def test_manifest_covers_package_content_without_absolute_source_paths(self):
        destination, _ = self.package()
        text = (destination / "package-manifest.json").read_text()
        self.assertNotIn(str(self.repo), text)
        manifest = json.loads(text)
        for relative, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((destination / relative).read_bytes()).hexdigest(), expected)

    def test_deterministic_zip(self):
        first, _ = self.package("first.zip", output_format="zip")
        second, _ = self.package("second.zip", output_format="zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_required_source_and_core_have_no_output_side_effect(self):
        for relative in ("companions/music-preferences-import/run_music_trial.py", "scripts/match_vocab.py"):
            with self.subTest(relative=relative):
                path = self.repo / relative
                content = path.read_bytes()
                path.unlink()
                output = self.base / "does-not-exist/out"
                with self.assertRaises(ValueError):
                    prepare_music_package(self.repo, output)
                self.assertFalse(output.parent.exists())
                path.write_bytes(content)

    def test_rejects_symlink_inputs_and_metadata_directory(self):
        path = self.music / "song_match.py"
        original = path.read_bytes()
        path.unlink()
        try:
            path.symlink_to(self.repo / ".env")
        except OSError:
            path.write_bytes(original)
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.package()
        path.unlink()
        path.write_bytes(original)
        metadata_dir = self.music / "adapters/workbuddy"
        outside = self.base / "outside-metadata"
        metadata_dir.rename(outside)
        metadata_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package("escaped.zip", host="workbuddy")

    def test_existing_outputs_and_repository_paths_are_protected(self):
        target = self.base / "existing.zip"
        target.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            prepare_music_package(self.repo, target, output_format="zip")
        self.assertEqual(target.read_bytes(), b"keep")
        for destination in (self.repo, self.base, self.music / "out.zip"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                prepare_music_package(self.repo, destination)

    def test_core_and_real_music_match_run_from_a_different_directory(self):
        destination, _ = self.package()
        script = (
            "import json,sys; from pathlib import Path; "
            "root=Path(sys.argv[1]); sys.path.insert(0,str(root)); "
            "from song_match import match_song; "
            "r=match_song('[00:01]I wrote and dreamed.\\n[00:02]My dreamer has written.',"
            "[{'word':'write','forms':['wrote','written']},{'word':'dream','forms':['dreamed'],'related':['dreamer']}],root/'core'); "
            "print(json.dumps({'families':r['family_coverage'],'occurrences':r['text_occurrences']}))"
        )
        working = self.base / "other-workspace"
        working.mkdir()
        result = subprocess.run([sys.executable, "-B", "-c", script, str(destination)],
                                cwd=working, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"families": 2, "occurrences": 4})

    def test_real_default_core_discovery_standalone_and_pair(self):
        standalone, _ = self.package()
        pair, _ = self.package("pair", layout="pair")
        working = self.base / "other-cwd"
        working.mkdir()
        script = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import run_music_trial as trial; "
            "print(json.dumps(str(trial.default_core_dir().resolve())))"
        )
        for skill, expected in ((standalone, standalone / "core"),
                                (pair / "music-preferences-import", pair / "vocab-video-finder/scripts")):
            with self.subTest(skill=skill):
                result = subprocess.run([sys.executable, "-B", "-c", script, str(skill)],
                                        cwd=working, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(json.loads(result.stdout)), expected.resolve())

    def test_relative_cli_source_and_destination_from_another_cwd(self):
        script = ROOT / "tools/prepare_music_package.py"
        result = subprocess.run([sys.executable, "-B", str(script), "--source", "public-repo",
                                 "--host", "deepseek", "--output", "project/.dsh/skills/music-preferences-import"],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.base / "project/.dsh/skills/music-preferences-import/SKILL.md").is_file())
        self.assertTrue((self.base / "project/.dsh/skills/music-preferences-import/core/word_families.py").is_file())


if __name__ == "__main__":
    unittest.main()
