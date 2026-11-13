"""Single-skill installation, privacy and complete cross-directory execution."""
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

from prepare_unified_package import (EXAMPLES, MUSIC_ENGINE_FILES, PUBLIC_SCRIPTS,
    REFERENCE_DATA, REFERENCE_DOCS, ROOT_FILES, prepare_unified_package)


ROOT = Path(__file__).resolve().parents[1]
MARKER = "PRIVATE_UNIFIED_DATA_MUST_NOT_SHIP"


class UnifiedPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "public-repo"
        self.repo.mkdir()
        for name in ("SKILL.md", "LICENSE", "NOTICE"):
            self.copy(name)
        for name in PUBLIC_SCRIPTS:
            self.copy("scripts/" + name)
        for name in EXAMPLES:
            self.copy("examples/" + name)
        for name in REFERENCE_DATA | REFERENCE_DOCS:
            self.copy("references/" + name)
        for name in MUSIC_ENGINE_FILES | {"SKILL.md", "LICENSE", "NOTICE"}:
            self.copy("companions/music-preferences-import/" + name)
        for name in ROOT_FILES:
            self.copy("unified/" + name, required=False)
        self.write("unified/SKILL.md", '---\nname: vocab-media-finder\ndescription: "Video and music vocabulary discovery"\nmetadata:\n  version: "4.0.0-beta.1"\n---\n\n# Vocab Media Finder\n')
        self.write("unified/README.md", "# One vocabulary, video and music\n")
        self.write("unified/adapters/bootstrap.md", "# Adapt to the current host\n")
        self.write("unified/adapters/workbuddy/metadata.json", json.dumps({
            "display_name": "词汇影音发现", "display_name_en": "Vocabulary Media Finder",
            "description_zh": "同一词表的视频与歌曲", "description_en": "Video and music from one vocabulary",
            "author": "Contributors", "ignored_private_field": MARKER}, ensure_ascii=False))
        for relative in (".env", "profile.json", "private.db", "unified/output/profile.json",
                         "unified/.env", "unified/Cookies", "unified/data/client.sqlite",
                         "unified/full-song.lrc", "unified/test-real-user-words.json",
                         "scripts/not_allowlisted.py", "companions/music-preferences-import/output/profile.json",
                         "companions/music-preferences-import/__pycache__/private.pyc"):
            self.write(relative, MARKER)

    def tearDown(self):
        self.temporary.cleanup()

    def copy(self, relative, required=True):
        original = ROOT / relative
        if not original.is_file():
            if required:
                raise AssertionError("Required public source missing: " + relative)
            return
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)

    def write(self, relative, content):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def package(self, name="single.zip", **options):
        output = self.base / name
        result = prepare_unified_package(self.repo, output, **options)
        return output, result

    def test_single_root_entry_and_complete_internal_engines(self):
        output, result = self.package()
        self.assertEqual(result["format"], "zip")
        self.assertEqual(result["name"], "vocab-media-finder")
        self.assertEqual(result["version"], "4.0.0-beta.1")
        self.assertFalse(result["separate_skill_installation_required"])
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertEqual([name for name in names if Path(name).name == "SKILL.md"], ["SKILL.md"])
            for name in ("run.py", "demo.py", "video/scripts/video_pipeline.py", "video/scripts/word_families.py",
                         "video/examples/words.json", "music/provider_runtime.py", "music/demo.py", "music/qqmusic_api.py"):
                self.assertIn(name, names)
            self.assertNotIn("video/package-manifest.json", names)
            self.assertNotIn("music/package-manifest.json", names)
            self.assertNotIn("music/core/word_families.py", names)
            self.assertNotIn("video/README.md", names)
            self.assertNotIn("music/README.md", names)
            self.assertNotIn("video/adapters/workbuddy.md", names)

    def test_all_hosts_have_one_skill_and_workbuddy_uses_unified_metadata(self):
        for host in ("codex", "workbuddy", "deepseek", "generic", "source"):
            with self.subTest(host=host):
                output, _ = self.package(host + ".zip", host=host, author="Public Author")
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(sum(Path(n).name == "SKILL.md" for n in archive.namelist()), 1)
                    skill = archive.read("SKILL.md").decode()
                    self.assertIn("name: " + ('"vocab-media-finder"' if host == "workbuddy" else "vocab-media-finder"), skill)
                    if host == "workbuddy":
                        fields = {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
                                  for line in skill.split("---", 2)[1].strip().splitlines()}
                        self.assertEqual(fields["display_name"], "词汇影音发现")
                        self.assertEqual(fields["author"], "Public Author")
                        self.assertEqual(fields["version"], "4.0.0-beta.1")

    def test_allowlist_excludes_private_files_and_internal_skill_declarations(self):
        output, _ = self.package(host="workbuddy")
        with zipfile.ZipFile(output) as archive:
            for name in archive.namelist():
                self.assertNotIn(MARKER.encode(), archive.read(name), name)
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn("..", Path(name).parts)
                self.assertNotIn(Path(name).suffix, {".db", ".sqlite", ".sqlite3", ".lrc", ".mp3"})
            self.assertNotIn("adapters/workbuddy/metadata.json", archive.namelist())

    def test_manifest_and_engine_provenance_have_hashes_not_machine_paths(self):
        output, _ = self.package()
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("package-manifest.json"))
            self.assertEqual(manifest["layout"], "single-skill")
            self.assertEqual(manifest["entrypoint"], "SKILL.md")
            self.assertEqual(manifest["engines"], {"video": "3.0.0", "music": "0.3.0-experimental"})
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            for name in ("package-manifest.json", "video/SOURCE.json", "music/SOURCE.json"):
                self.assertNotIn(str(self.repo).encode(), archive.read(name))
                self.assertEqual(json.loads(archive.read(name)).get("license", "MIT"), "MIT")

    def test_zip_is_reproducible(self):
        first, _ = self.package("one.zip")
        second, _ = self.package("two.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_runtime_does_not_create_an_output_directory(self):
        for relative in ("unified/run.py", "scripts/word_families.py", "companions/music-preferences-import/qqmusic_api.py"):
            with self.subTest(relative=relative):
                path = self.repo / relative
                if not path.is_file():
                    self.fail("Unified runtime must be ready before this release test")
                original = path.read_bytes()
                path.unlink()
                destination = self.base / "new-parent/skill.zip"
                with self.assertRaises(ValueError):
                    prepare_unified_package(self.repo, destination)
                self.assertFalse(destination.parent.exists())
                path.write_bytes(original)

    def test_symlink_source_files_and_host_metadata_are_rejected(self):
        entry = self.repo / "unified/run.py"
        original = entry.read_bytes()
        entry.unlink()
        try:
            entry.symlink_to(self.repo / ".env")
        except OSError:
            entry.write_bytes(original)
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.package()
        entry.unlink()
        entry.write_bytes(original)
        metadata = self.repo / "unified/adapters/workbuddy"
        outside = self.base / "outside-metadata"
        metadata.rename(outside)
        metadata.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package(host="workbuddy")

    def test_existing_outputs_and_source_paths_are_preserved(self):
        output = self.base / "keep.zip"
        output.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            prepare_unified_package(self.repo, output)
        self.assertEqual(output.read_bytes(), b"keep")
        for destination in (self.repo / "output.zip", self.repo, self.base):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                prepare_unified_package(self.repo, destination, output_format="directory")

    def test_unified_source_requires_a_unified_name(self):
        self.write("unified/SKILL.md", '---\nname: vocab-video-finder\ndescription: "Old video entry"\nmetadata:\n  version: "3.0.0"\n---\n')
        with self.assertRaisesRegex(ValueError, "vocab-media-finder"):
            self.package()

    def test_real_demo_runs_after_extraction_with_an_empty_home_and_other_cwd(self):
        output, _ = self.package()
        skill = self.base / "installed"
        with zipfile.ZipFile(output) as archive:
            archive.extractall(skill)
        working = self.base / "another-workspace"
        working.mkdir()
        empty_home = self.base / "empty-home"
        empty_home.mkdir()
        env = {**os.environ, "HOME": str(empty_home), "USERPROFILE": str(empty_home),
               "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-B", str(skill / "run.py"), "demo", "--output", str(self.base / "demo")],
                                cwd=working, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["skill"], "vocab-media-finder")
        self.assertEqual(report["video"]["family_coverage"], 4)
        self.assertEqual(report["video"]["text_occurrences"], 14)
        self.assertTrue(report["vocabulary"]["same_input_for_video_and_music"])
        self.assertEqual(set(report["music"]), {"netease", "qq"})
        for platform in report["music"].values():
            self.assertEqual(platform["vocabulary_sha256"], report["vocabulary"]["sha256"])
            for song in platform["songs"]:
                self.assertEqual(song["family_coverage"], 2)
                self.assertEqual(song["text_occurrences"], 4)
        self.assertEqual(report["web_requests"], 0)
        self.assertFalse(report["personal_data_read"])
        self.assertFalse(report["ready_to_deliver"])
        self.assertTrue((self.base / "demo/demo-report.md").is_file())

    def test_extracted_root_help_and_internal_music_help_are_available(self):
        output, _ = self.package("installed", output_format="directory")
        for arguments in (["--help"], ["video", "--help"], ["music", "--help"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, "-B", str(output / "run.py"), *arguments],
                                        cwd=self.base, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_bundled_workflow_initialisation_resolves_the_internal_engine(self):
        import re
        output, _ = self.package("workflow-install", output_format="directory")
        text = (output / "video/references/workflow.md").read_text()
        command = re.search(r"```sh\n(.*?)\n```", text, re.S).group(1)
        env = dict(os.environ, HOME=str(self.base / "empty-home"),
                   VOCAB_VIDEO_CACHE_DIR=str(self.base / "resource-check-cache"))
        result = subprocess.run(["sh", "-c", command], cwd=output, env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sample_member_counts", json.loads(result.stdout))

    def test_real_video_plan_uses_bundled_dependencies_and_does_not_claim_search(self):
        output, _ = self.package("installed", output_format="directory")
        result = subprocess.run([sys.executable, "-B", str(output / "run.py"), "plan", "--no-resources",
            "--mode", "video", "--words", str(output / "video/examples/words.json"), "--output", str(self.base / "plan")],
            cwd=self.base, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["web_search_executed"])
        self.assertFalse(report["client_read_executed"])
        self.assertNotIn("music", report["media"])
        self.assertEqual(report["media"]["video"]["status"], "host_search_required")

    def test_packager_cli_supports_relative_source_from_other_cwd(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/prepare_unified_package.py"),
            "--source", "public-repo", "--host", "deepseek", "--output", "one-skill.zip"],
            cwd=self.base, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["skill_entry_count"], 1)


if __name__ == "__main__":
    unittest.main()
