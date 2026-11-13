import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from demo import run_demo
from run import resolve_layout


MUSIC_PY = ("demo.py", "run_music_trial.py", "preferences.py", "song_match.py", "local_netease.py",
            "import_netease.py", "provider_runtime.py", "qqmusic_api.py", "local_qqmusic.py")
VIDEO_PY = ("demo.py", "video_pipeline.py", "match_vocab.py", "word_families.py", "video_sources.py",
            "runtime_paths.py", "search_profiles.py")


def assert_combined(test, report):
    test.assertTrue(report["vocabulary"]["same_input_for_video_and_music"])
    test.assertEqual(report["vocabulary"]["entries"], 4)
    test.assertEqual(report["video"]["family_coverage"], 4)
    test.assertEqual(report["video"]["text_occurrences"], 14)
    test.assertEqual(report["web_requests"], 0)
    test.assertFalse(report["personal_data_read"])
    test.assertFalse(report["ready_to_deliver"])
    for platform in ("netease", "qq"):
        music = report["music"][platform]
        test.assertEqual(music["vocabulary_sha256"], report["video"]["vocabulary_sha256"])
        test.assertEqual(music["vocabulary_entries"], 4)
        test.assertEqual(len(music["songs"]), 2)
        for song in music["songs"]:
            test.assertEqual(song["family_coverage"], 2)
            test.assertEqual(song["text_occurrences"], 4)
            test.assertEqual(song["relation_counts"], {"inflection": 3, "derived": 1})
            test.assertFalse(song["playback_verified"])
            test.assertIsNone(song["real_play_url"])


class UnifiedDemoTests(unittest.TestCase):
    def test_one_demo_uses_same_vocabulary_for_both_real_matching_engines(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "demo"
            report = run_demo(output)
            assert_combined(self, report)
            self.assertTrue((output / "demo-report.md").is_file())
            self.assertEqual(json.loads((output / "words.json").read_text()), report["vocabulary"]["words"])
            for platform in ("netease", "qq"):
                trial = json.loads((output / "music" / platform / "trial-summary.json").read_text())
                self.assertEqual(trial["vocabulary_entries"], 4)
                self.assertFalse(trial["general_test_vocabulary"])

    def test_single_package_runs_from_unrelated_directory_without_global_skills(self):
        layout = resolve_layout()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "one-skill"
            (package / "video/scripts").mkdir(parents=True)
            (package / "video/examples").mkdir()
            (package / "music").mkdir()
            for name in ("run.py", "demo.py"):
                shutil.copy2(Path(__file__).parent / name, package / name)
            for name in VIDEO_PY:
                shutil.copy2(layout["video_scripts"] / name, package / "video/scripts" / name)
            for name in ("words.json", "video.json", "story.srt"):
                shutil.copy2(layout["video_examples"] / name, package / "video/examples" / name)
            for name in MUSIC_PY:
                shutil.copy2(layout["music"] / name, package / "music" / name)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            # These blocks are inherited by every child process, making any
            # accidental network or SQLite account read fail the demo.
            guard = root / "guard"
            guard.mkdir()
            (guard / "sitecustomize.py").write_text(
                "import socket,sqlite3\n"
                "def blocked(*a,**k): raise AssertionError('Offline demo attempted network or SQLite access')\n"
                "socket.socket.connect=blocked\nsocket.create_connection=blocked\nsqlite3.connect=blocked\n")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(guard)
            completed = subprocess.run([sys.executable, "-B", str(package / "run.py"), "demo", "--output", str(root / "output")],
                cwd=elsewhere, env=environment, capture_output=True, text=True, check=True)
            assert_combined(self, json.loads(completed.stdout))
            self.assertFalse((package / "SKILL.md").exists())  # Runtime does not route via another skill.

    def test_existing_outputs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "keep.txt").write_text("Keep")
            with self.assertRaises(ValueError):
                run_demo(root)
            self.assertEqual((root / "keep.txt").read_text(), "Keep")


if __name__ == "__main__":
    unittest.main()
