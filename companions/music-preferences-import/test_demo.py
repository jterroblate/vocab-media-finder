import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from demo import run_demo
from run_music_trial import default_core_dir


class DemoTests(unittest.TestCase):
    def test_both_providers_use_synthetic_data_without_network_or_local_accounts(self):
        with tempfile.TemporaryDirectory() as temp, patch("urllib.request.urlopen", side_effect=AssertionError("No network")), \
                patch("local_netease.read_local_preferences", side_effect=AssertionError("No account reads")):
            output = Path(temp) / "demo"
            result = run_demo(output, default_core_dir())
            self.assertEqual(set(result["providers"]), {"netease", "qq"})
            self.assertEqual(result["web_requests"], 0)
            for platform in ("netease", "qq"):
                summary = json.loads((output / platform / "trial-summary.json").read_text())
                self.assertEqual(summary["platform"], platform)
                self.assertFalse(summary["general_test_vocabulary"])
                self.assertEqual(summary["vocabulary_entries"], 2)
                self.assertEqual(len(summary["song_trials"]), 2)
                for song in summary["song_trials"]:
                    self.assertEqual(song["platform"], platform)
                    self.assertEqual(song["match"]["family_coverage"], 2)
                    self.assertEqual(song["match"]["text_occurrences"], 4)
                    self.assertFalse(song["match"]["full_lyrics_exported"])
                self.assertFalse(summary["ready_to_deliver"])

    def test_existing_demo_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "keep.txt").write_text("Keep")
            with self.assertRaises(ValueError):
                run_demo(temp)
            self.assertEqual((Path(temp) / "keep.txt").read_text(), "Keep")
