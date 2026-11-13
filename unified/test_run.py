import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run


class EntryTests(unittest.TestCase):
    def test_source_and_single_package_layouts_resolve_without_global_installation(self):
        actual = run.resolve_layout()
        self.assertIn(actual["layout"], {"single_package", "source_repository"})
        self.assertTrue((actual["video_scripts"] / "word_families.py").is_file())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "video/scripts").mkdir(parents=True)
            (root / "video/scripts/video_pipeline.py").write_text("# synthetic layout")
            layout = run.resolve_layout(root)
            self.assertEqual(layout["layout"], "single_package")
            self.assertFalse(layout["music_available"])
            command = run.engine_command("video", ["--help"], root)
            self.assertNotIn("music", Path(command[2]).parts)
            with self.assertRaises(ValueError):
                run.engine_command("music", ["--help"], root)

    def test_music_forwarding_preserves_words_and_resolves_core(self):
        command = run.engine_command("music", ["--words", "my words.json", "--output", "my output"])
        self.assertEqual(command[-4:], ["--words", "my words.json", "--output", "my output"])
        self.assertIn("--core-dir", command)
        self.assertEqual(command[command.index("--core-dir") + 1], str(run.resolve_layout()["video_scripts"]))

    def test_video_route_never_starts_music(self):
        response = subprocess.CompletedProcess([], 0, stdout='{"status":"video_only"}\n', stderr="")
        with patch.object(run, "call_engine", return_value=response) as call:
            with patch("builtins.print"):
                self.assertEqual(run.main(["video", "expand", "--words", "only-video.json"]), 0)
        call.assert_called_once_with("video", ["expand", "--words", "only-video.json"])

    def test_plan_is_not_a_search_and_contains_one_shared_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            words = root / "input.json"
            entries = [{"word": "write", "forms": ["wrote"]}, {"word": "compete", "related": ["competition"]}]
            words.write_text(json.dumps(entries))
            result = run.create_plan(words, root / "out", use_resources=False, scenes=["school story"])
            self.assertEqual(result["mode"], "both")
            self.assertEqual(result["media"]["video"]["network"], "domestic")
            self.assertEqual(result["media"]["video"]["level"], "easy")
            self.assertFalse(result["web_search_executed"])
            self.assertFalse(result["client_read_executed"])
            self.assertFalse(result["ready_to_deliver"])
            self.assertEqual(result["media"]["video"]["found_videos"], [])
            self.assertTrue(result["media"]["video"]["queries"])
            music_args = result["media"]["music"]["command"]
            self.assertEqual(music_args[music_args.index("--words") + 1], result["shared_words_file"])
            self.assertEqual(json.loads(Path(result["shared_words_file"]).read_text()), entries)

    def test_video_only_plan_does_not_require_or_read_music(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            words = root / "words.json"
            words.write_text('["write"]')
            calls = []
            original = run.call_engine
            def video_only(kind, args, layout_root=None):
                self.assertEqual(kind, "video")
                calls.append(args[0])
                return original(kind, args, layout_root)
            with patch.object(run, "call_engine", side_effect=video_only):
                result = run.create_plan(words, root / "plan", mode="video", use_resources=False)
            self.assertEqual(set(result["media"]), {"video"})
            self.assertEqual(calls, ["expand", "queries"])

    def test_music_only_plan_does_not_claim_video_search(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            words = root / "words.json"
            words.write_text('["write"]')
            result = run.create_plan(words, root / "plan", mode="music", provider="qq", use_resources=False)
            self.assertEqual(set(result["media"]), {"music"})
            self.assertEqual(result["media"]["music"]["provider"], "qq")
            self.assertFalse(result["client_read_executed"])

    def test_plan_output_and_invalid_words_never_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            words = root / "words.json"
            words.write_text('["write"]')
            output = root / "output"
            output.mkdir()
            (output / "keep.txt").write_text("Keep")
            with self.assertRaises(ValueError):
                run.create_plan(words, output, use_resources=False)
            self.assertEqual((output / "keep.txt").read_text(), "Keep")
            words.write_text('{"word":"write"}')
            with self.assertRaises(ValueError):
                run.create_plan(words, root / "new", use_resources=False)
            self.assertFalse((root / "new").exists())


if __name__ == "__main__":
    unittest.main()
