"""Small behavioral regressions for counts that affect recommendations."""

import unittest
from match_vocab import match


class MatcherTests(unittest.TestCase):
    def test_deduplicates_targets_and_repeated_mentions(self):
        result = match(["species", "Species", {"word": "benefit", "forms": ["benefits"]}],
                       "Species benefit. Species receive benefits.")
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["with_explicit_forms_unique_count"], 2)
        self.assertEqual([m["occurrence_count"] for m in result["matches"]], [2, 2])

    def test_requires_explicit_inflections_and_excludes_derivatives(self):
        words = ["effective", "survive", "conventional", "breed", "compete"]
        text = "Effectively, survival is unconventional. They bred animals competing."
        self.assertEqual(match(words, text)["with_explicit_forms_unique_count"], 0)
        result = match([{"word": "breed", "forms": ["bred"]},
                        {"word": "compete", "forms": ["competing"]}], text)
        self.assertEqual(result["exact_unique_count"], 0)
        self.assertEqual(result["with_explicit_forms_unique_count"], 2)

    def test_phrase_and_word_boundaries(self):
        result = match(["state", "in fact"], "Statement. In fact yes. In. Fact no.")
        self.assertEqual(result["with_explicit_forms_unique_count"], 1)
        self.assertEqual(result["matches"][0]["occurrence_count"], 1)

    def test_evidence_and_unicode(self):
        text = "They discuss a CHILD’S privacy and privacy again."
        result = match(["child's", "privacy"], text)
        self.assertEqual(result["exact_unique_count"], 2)
        self.assertEqual(result["matches"][1]["occurrence_count"], 2)
        self.assertIn("privacy", result["matches"][1]["evidence"][0]["context"])

    def test_malformed_input(self):
        for words in [[], {}, [""], [{"word": "benefit", "forms": "benefits"}], ["benefit 好处"]]:
            with self.subTest(words=words), self.assertRaises(ValueError):
                match(words, "A benefit.")
        with self.assertRaises(ValueError):
            match(["benefit"], "")


if __name__ == "__main__":
    unittest.main()
