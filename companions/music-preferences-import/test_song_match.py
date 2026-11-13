import os
import sys
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch
from song_match import lyric_lines, match_song


class LyricTests(unittest.TestCase):
    def test_same_time_duplicates_and_real_repeats(self):
        result = lyric_lines("[00:01.00]I dream.\n[00:01.00]I dream.\n[00:02.00]I dream.")
        self.assertEqual(len(result), 2)
        self.assertEqual([r["start"] for r in result], [1, 2])

    def test_multiple_times_and_credits(self):
        result = lyric_lines("[ar:Synthetic]\n[00:00.00]Written by: Synthetic\n"
                             "[00:01.00][00:03.00]I have written words.")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["text"], "I have written words.")

    def test_untimed_repetitions_not_deleted(self):
        self.assertEqual(len(lyric_lines("I dream.\nI dream.")), 2)

    def test_known_title_artist_credit_is_not_sung_body(self):
        raw = "[00:00]Dream - Synthetic Artist\n[00:01]I wrote for you.\n[00:02]Dream"
        lines = lyric_lines(raw, title="Dream", artists=["Synthetic Artist"])
        self.assertEqual([line["text"] for line in lines], ["I wrote for you.", "Dream"])

    @unittest.skipUnless(os.environ.get("VOCAB_VIDEO_CORE"), "Set VOCAB_VIDEO_CORE to the released core scripts directory")
    def test_reuses_real_family_core_without_lyric_export(self):
        result = match_song("[00:00]Written by: Synthetic\n[00:01]I wrote and dreamed.\n"
                            "[00:02]A dreamer has written.\n[00:02]A dreamer has written.",
                            [{"word": "write", "forms": ["wrote", "written"]},
                             {"word": "dream", "forms": ["dreamed"], "related": ["dreamer"]}],
                            Path(os.environ["VOCAB_VIDEO_CORE"]))
        self.assertEqual(result["family_coverage"], 2)
        self.assertEqual(result["text_occurrences"], 4)
        self.assertEqual(result["relation_counts"], {"inflection": 3, "derived": 1})
        self.assertEqual(result["context_review_status"], "pending")
        self.assertFalse(result["full_lyrics_exported"])
        self.assertNotIn("text", result)
        self.assertNotIn("occurrences", result)
        self.assertFalse(result["playback_verified"])

    @unittest.skipUnless(os.environ.get("VOCAB_VIDEO_CORE"), "Set VOCAB_VIDEO_CORE to test language hints")
    def test_latin_script_does_not_prove_english(self):
        result = match_song("[00:01]Je rêve de la vie et du soleil dans la nuit de mon amour.\n"
                            "[00:02]Le vent chante dans les arbres et le monde est beau, love.",
                            ["love"], os.environ["VOCAB_VIDEO_CORE"])
        self.assertEqual(result["family_coverage"], 1)
        self.assertFalse(result["english_text_candidate"])
        self.assertFalse(result["language_verified"])

    @unittest.skipUnless(os.environ.get("VOCAB_VIDEO_CORE"), "Set VOCAB_VIDEO_CORE for core selection")
    def test_explicit_core_does_not_reuse_other_preloaded_modules(self):
        fake = SimpleNamespace(expand_targets=lambda *args, **kw: (_ for _ in ()).throw(AssertionError("wrong core")))
        with patch.dict(sys.modules, {"word_families": fake}):
            result = match_song("I wrote it.", [{"word": "write", "forms": ["wrote"]}], os.environ["VOCAB_VIDEO_CORE"])
            self.assertEqual(result["text_occurrences"], 1)
            self.assertIs(sys.modules["word_families"], fake)

    @unittest.skipUnless(os.environ.get("VOCAB_VIDEO_CORE"), "Set VOCAB_VIDEO_CORE for relation review")
    def test_unconfirmed_derivation_is_diagnostic_not_main_count(self):
        result = match_song("I am helpful and you are helpful.", [{"word": "help", "related": [
            {"word": "helpful", "confirmed": False}]}], os.environ["VOCAB_VIDEO_CORE"])
        self.assertEqual(result["family_coverage"], 0)
        self.assertEqual(result["text_occurrences"], 0)
        self.assertEqual(result["unconfirmed_relation_occurrences"], 2)


if __name__ == "__main__":
    unittest.main()
