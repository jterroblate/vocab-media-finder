"""Behavioral tests for clean, explicit host-package preparation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from prepare_package import prepare_package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.write("SKILL.md", '---\nname: vocab-video-finder\ndescription: "A public skill"\nmetadata:\n  version: "3.0.0"\n---\n\n# A skill\nUse the local tools.\n')
        self.write("scripts/video_pipeline.py", 'print("public runtime")\n')
        self.write("scripts/test_video_pipeline.py", '"""Public tests."""\n')
        self.write("references/workflow.md", "Public workflow.\n")
        self.write("references/sources.json", '{"sources": []}\n')
        self.write("adapters/bootstrap.md", "Probe the tools actually available.\n")
        self.write("adapters/workbuddy.md", "Import the prepared package.\n")
        self.write("agents/openai.yaml", 'policy:\n  allow_implicit_invocation: true\n')
        self.write("adapters/workbuddy/metadata.json", json.dumps({
            "display_name": "词汇视频", "display_name_en": "Vocabulary Videos",
            "description_zh": "中文说明", "description_en": "English description",
            "author": "Contributors"}, ensure_ascii=False))
        for name in ("words.json", "story.srt", "video.json", "domestic-easy.json", "global-standard.json"):
            self.write("examples/" + name, "Synthetic public example.\n")
        self.write("LICENSE", "Public code license.\n")
        for name in (".env", ".env.production", ".git/config", ".cache/private.json",
                     "secrets/key.txt", "scripts/__pycache__/x.pyc", ".DS_Store",
                     "examples/student-private.json", "references/student-private.json",
                     "adapters/student-private.md", "docs/private-notes.md", "QA/private.txt", "references/student-private.md", "scripts/local_credentials.py"):
            self.write(name, "PRIVATE_MARKER_MUST_NOT_SHIP\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content):
        p = self.source / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_generic_clean_allowlist(self):
        destination = self.base / "generic"
        result = prepare_package(self.source, destination)
        self.assertEqual(result["status"], "prepared")
        paths = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
        self.assertIn("scripts/video_pipeline.py", paths)
        self.assertIn("references/sources.json", paths)
        self.assertIn("examples/story.srt", paths)
        self.assertIn("LICENSE", paths)
        self.assertNotIn("agents/openai.yaml", paths)
        self.assertNotIn("tools/prepare_package.py", paths)
        self.assertFalse(any("PRIVATE_MARKER" in p.read_text(encoding="utf-8")
                             for p in destination.rglob("*") if p.is_file()))

    def test_codex_includes_only_codex_metadata(self):
        destination = self.base / "codex"
        prepare_package(self.source, destination, host="codex")
        self.assertTrue((destination / "agents/openai.yaml").is_file())
        self.assertEqual((destination / "SKILL.md").read_bytes(), (self.source / "SKILL.md").read_bytes())

    def test_deepseek_keeps_standard_skill_and_explicit_target(self):
        destination = self.base / "project/.dsh/skills/vocab-video-finder"
        prepare_package(self.source, destination, host="deepseek")
        self.assertEqual((destination / "SKILL.md").read_bytes(), (self.source / "SKILL.md").read_bytes())
        self.assertFalse((destination / "agents").exists())

    def test_workbuddy_defaults_to_zip_with_root_entry_and_required_fields(self):
        destination = self.base / "workbuddy.zip"
        result = prepare_package(self.source, destination, host="workbuddy", author="An Author")
        self.assertEqual(result["format"], "zip")
        with zipfile.ZipFile(destination) as archive:
            self.assertIn("SKILL.md", archive.namelist())
            self.assertNotIn("vocab-video-finder/SKILL.md", archive.namelist())
            text = archive.read("SKILL.md").decode()
        header = text.split("---", 2)[1]
        fields = {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
                  for line in header.strip().splitlines()}
        self.assertEqual(set(fields), {"name", "display_name", "display_name_en", "description",
                                       "description_zh", "description_en", "version", "author"})
        self.assertEqual(fields["author"], "An Author")
        self.assertEqual(fields["version"], "3.0.0")
        self.assertIn("# A skill\nUse the local tools.", text)

    def test_workbuddy_author_quotes_and_unicode_are_valid_yaml_scalars(self):
        destination = self.base / "quoted.zip"
        author = '作者 "A"\nTeam'
        prepare_package(self.source, destination, host="workbuddy", author=author)
        with zipfile.ZipFile(destination) as archive:
            text = archive.read("SKILL.md").decode()
        author_line = next(x for x in text.splitlines() if x.startswith("author:"))
        self.assertEqual(json.loads(author_line.split(": ", 1)[1]), author)

    def test_manifest_hashes_and_no_absolute_source_path(self):
        destination = self.base / "manifest"
        prepare_package(self.source, destination)
        raw = (destination / "package-manifest.json").read_text()
        self.assertNotIn(str(self.source), raw)
        manifest = json.loads(raw)
        self.assertEqual(manifest["version"], "3.0.0")
        for relative, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((destination / relative).read_bytes()).hexdigest(), digest)

    def test_zip_is_reproducible_and_extracts_to_a_skill_root(self):
        first, second = self.base / "first.zip", self.base / "second.zip"
        prepare_package(self.source, first, host="generic", output_format="zip")
        prepare_package(self.source, second, host="generic", output_format="zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            archive.extractall(self.base / "unpacked")
        self.assertTrue((self.base / "unpacked/scripts/video_pipeline.py").exists())

    def test_refuses_existing_nonempty_directory_and_existing_zip(self):
        destination = self.base / "existing"
        destination.mkdir()
        keep = destination / "keep.txt"
        keep.write_text("unchanged")
        with self.assertRaises(ValueError):
            prepare_package(self.source, destination)
        self.assertEqual(keep.read_text(), "unchanged")
        archive = self.base / "existing.zip"
        archive.write_bytes(b"unchanged")
        with self.assertRaises(ValueError):
            prepare_package(self.source, archive, host="workbuddy")
        self.assertEqual(archive.read_bytes(), b"unchanged")

    def test_allows_explicit_empty_directory(self):
        destination = self.base / "empty"
        destination.mkdir()
        prepare_package(self.source, destination)
        self.assertTrue((destination / "SKILL.md").exists())

    def test_refuses_source_ancestors_and_descendants(self):
        for destination in (self.source, self.base, self.source / "out", self.source / "nested/out.zip"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                prepare_package(self.source, destination)

    def test_missing_source_entry_has_no_output_side_effect(self):
        (self.source / "SKILL.md").unlink()
        destination = self.base / "missing/target"
        with self.assertRaises(ValueError):
            prepare_package(self.source, destination)
        self.assertFalse(destination.parent.exists())

    def test_rejects_source_symlink_and_output_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("Symlinks unavailable")
        link = self.source / "scripts/runtime_paths.py"
        try:
            link.symlink_to(self.source / ".env")
        except OSError:
            self.skipTest("Creating symlinks is not permitted")
        with self.assertRaises(ValueError):
            prepare_package(self.source, self.base / "linked-output")
        link.unlink()
        target = self.base / "real-output"
        target.mkdir()
        output_link = self.base / "output-link"
        output_link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare_package(self.source, output_link)

    def test_relative_cli_destination_and_source(self):
        script = Path(__file__).with_name("prepare_package.py").resolve()
        result = subprocess.run([sys.executable, str(script), "--source", "source", "--host", "deepseek",
                                 "--output", "relative/.agents/skills/vocab-video-finder"],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.base / "relative/.agents/skills/vocab-video-finder/SKILL.md").exists())

    def test_workbuddy_metadata_cannot_escape_through_a_directory_symlink(self):
        metadata = self.source / "adapters/workbuddy/metadata.json"
        outside = self.base / "outside-metadata"
        outside.mkdir()
        (outside / "metadata.json").write_text(metadata.read_text())
        metadata.unlink()
        metadata.parent.rmdir()
        try:
            metadata.parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted")
        with self.assertRaises(ValueError):
            prepare_package(self.source, self.base / "escaped.zip", host="workbuddy")

    def test_cli_requires_an_explicit_output(self):
        script = Path(__file__).with_name("prepare_package.py").resolve()
        result = subprocess.run([sys.executable, str(script), "--host", "workbuddy"],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--output", result.stderr)

    def test_rejects_invalid_metadata_and_wrong_zip_suffix(self):
        with self.assertRaises(ValueError):
            prepare_package(self.source, self.base / "not-a-zip", host="workbuddy")
        with self.assertRaises(ValueError):
            prepare_package(self.source, self.base / "empty-author.zip", host="workbuddy", author=" ")
        self.write("SKILL.md", "# No frontmatter")
        with self.assertRaises(ValueError):
            prepare_package(self.source, self.base / "bad-skill")


if __name__ == "__main__":
    unittest.main()
