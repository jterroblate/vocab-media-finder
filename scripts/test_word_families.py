"""Behavioral regressions for explicit families, optional resources and cues."""

import copy
import unittest
from unittest.mock import patch

import word_families as wf


class FamiliesTests(unittest.TestCase):
    def test_explicit_derivative_inflections_and_source_metadata(self):
        words = [{"word": "compete", "gloss": "竞争", "forms": ["competes", "competed", "competing"],
                  "related": [{"word": "competition", "forms": ["competitions"]}, "competitive"]}]
        before = copy.deepcopy(words)
        expanded = wf.expand_targets(words, use_resources=False)
        self.assertEqual(words, before)
        self.assertEqual(expanded["families"][0]["targets"][0]["gloss"], "竞争")
        result = wf.match_families(expanded, {"text": "They competed. Competitions are competitive. Compete!"})
        self.assertEqual([o["kind"] for o in result["occurrences"]],
                         ["inflection", "derived", "derived", "exact"])
        self.assertEqual(result["candidate_family_count"], 1)
        self.assertEqual(result["candidate_unique_occurrence_count"], 4)

    def test_irregular_and_comparative_are_explicit(self):
        expanded = wf.expand_targets([{"word": "breed", "forms": ["bred"]},
                                      {"word": "dim", "forms": ["dimmer", "dimmest"]}], False)
        result = wf.match_families(expanded, {"text": "They bred horses in dimmer light."})
        self.assertEqual(len(result["occurrences"]), 2)
        self.assertTrue(all(o["kind"] == "inflection" for o in result["occurrences"]))
        self.assertIsNone(result["occurrences"][0]["time_start"])

    def test_manual_forms_survive_repeat_expansion(self):
        words = [{"word": "dim", "forms": ["dimmer", "dimmed"]},
                 {"word": "dim", "forms": ["dimmest"]}]
        first = wf.expand_targets(words, False)
        second = wf.expand_targets(words, False)
        self.assertEqual(first, second)
        self.assertEqual({m["text"] for m in first["families"][0]["members"]},
                         {"dim", "dimmed", "dimmer", "dimmest"})

    def test_confirmed_family_merge_and_unique_physical_occurrences(self):
        words = [{"word": "compete", "related": ["competition"]}, "competition"]
        expanded = wf.expand_targets(words, False)
        self.assertEqual(len(expanded["families"]), 1)
        result = wf.match_families(expanded, {"text": "Competition!"})
        self.assertEqual(len(result["occurrences"]), 1)
        self.assertEqual(result["occurrences"][0]["kind"], "exact")
        self.assertEqual(set(result["occurrences"][0]["targets"]), {"compete", "competition"})
        self.assertEqual(result["occurrences"][0]["target_relations"],
                         {"compete": "derived", "competition": "exact"})
        reversed_expanded = wf.expand_targets(list(reversed(words)), False)
        self.assertEqual(expanded["families"][0]["family_id"],
                         reversed_expanded["families"][0]["family_id"])

    def test_unconfirmed_links_do_not_merge_and_collision_is_visible(self):
        expanded = wf.expand_targets([
            {"word": "organ", "related": [{"word": "organization", "confirmed": False}]},
            "organization"], False)
        self.assertEqual(len(expanded["families"]), 2)
        result = wf.match_families(expanded, {"text": "Organization."})
        self.assertEqual(len(result["occurrences"]), 2)
        self.assertEqual(result["candidate_unique_occurrence_count"], 1)
        self.assertTrue(all(o["collision"] for o in result["occurrences"]))
        self.assertEqual({o["relation_confirmed"] for o in result["occurrences"]}, {True, False})

    def test_no_substring_stemming_or_unlisted_derivative(self):
        expanded = wf.expand_targets(["effective", "survive", "conventional"], False)
        result = wf.match_families(expanded, {"text": "Effectively, survival is unconventional."})
        self.assertFalse(result["occurrences"])

    def test_real_cues_and_unknown_end(self):
        expanded = wf.expand_targets(["lens", {"word": "blur", "forms": ["blurred"]}], False)
        document = {"text": "Lens is blurred.", "cues": [
            {"start": 2.5, "end": 3.1, "text": "Lens", "char_start": 0, "char_end": 4},
            {"start": 8.0, "end": None, "text": "is blurred.", "char_start": 5, "char_end": 16}]}
        out = wf.match_families(expanded, document)["occurrences"]
        self.assertEqual((out[0]["time_start"], out[0]["time_end"]), (2.5, 3.1))
        self.assertEqual((out[1]["time_start"], out[1]["time_end"]), (8.0, None))

    def test_unicode_normalization_does_not_shift_cue_mapping(self):
        expanded = wf.expand_targets(["lens"], False)
        document = {"text": "™ Lens", "cues": [
            {"start": 4.0, "end": 5.0, "char_start": 2, "char_end": 6, "text": "Lens"}]}
        occurrence = wf.match_families(expanded, document)["occurrences"][0]
        self.assertEqual((occurrence["time_start"], occurrence["time_end"]), (4.0, 5.0))

    def test_resource_candidates_do_not_override_manual_confirmation(self):
        class Inflector:
            @staticmethod
            def getAllInflections(lemma, upos=None):
                return {"JJR": ("dimmer",), "JJS": ("dimmest",)}
        with patch.object(wf, "_resources", return_value=(Inflector(), None)):
            out = wf.expand_targets([{"word": "dim", "forms": ["dimmer"]}])
        members = {m["text"]: m for m in out["families"][0]["members"]}
        self.assertTrue(members["dimmer"]["confirmed"])
        self.assertFalse(members["dimmest"]["confirmed"])
        self.assertEqual(members["dimmer"]["source"], "explicit")

    def test_optional_resource_absence(self):
        with patch.object(wf.importlib, "import_module", side_effect=ImportError):
            expanded = wf.expand_targets([{"word": "dim", "forms": ["dimmer"]}])
        self.assertEqual(len(expanded["warnings"]), 2)
        self.assertEqual(len(expanded["families"][0]["members"]), 2)

    def test_source_inflected_form_uses_dictionary_lemma(self):
        class Inflector:
            @staticmethod
            def getAllLemmas(word, upos=None):
                return {"VERB": ("breed",)} if word == "bred" else {}
            @staticmethod
            def getAllInflections(word, upos=None):
                return {"VB": ("breed",), "VBD": ("bred",), "VBG": ("breeding",)} if word == "breed" else {}
        with patch.object(wf, "_resources", return_value=(Inflector(), None)):
            expanded = wf.expand_targets(["bred"])
        by_text = {m["text"]: m for m in expanded["families"][0]["members"]}
        self.assertEqual(by_text["bred"]["kind"], "exact")
        self.assertEqual(by_text["breed"]["kind"], "inflection")
        self.assertEqual(by_text["breeding"]["kind"], "inflection")

    def test_participle_derivation_uses_dictionary_base_and_pos(self):
        class Inflector:
            @staticmethod
            def getAllLemmas(word, upos=None):
                return {"VERB": ("write",)} if word == "written" else {}
            @staticmethod
            def getAllInflections(word, upos=None):
                return {"VB": ("write",), "VBD": ("wrote",), "VBN": ("written",), "VBG": ("writing",)} if word == "write" else {}
        class Relative:
            def name(self): return "writer"
            def synset(self): return self
            def pos(self): return "n"
        class Lemma:
            def derivationally_related_forms(self): return [Relative()]
            def pertainyms(self): raise AssertionError("Semantic-only edges must not enter counts")
        class WordNet:
            def __init__(self): self.calls = []
            def lemmas(self, word, pos=None):
                self.calls.append((word, pos))
                return [Lemma()] if word == "write" else []
        wn = WordNet()
        with patch.object(wf, "_resources", return_value=(Inflector(), wn)):
            expanded = wf.expand_targets([{"word": "written", "pos": "v"}])
        members = {m["text"]: m for m in expanded["families"][0]["members"]}
        self.assertIn(("write", "v"), wn.calls)
        self.assertEqual(members["writer"]["kind"], "derived")
        self.assertFalse(members["writer"]["confirmed"])
        self.assertEqual(members["wrote"]["kind"], "inflection")

    def test_semantic_pertainym_is_not_a_derivative(self):
        class Lemma:
            def derivationally_related_forms(self): return []
            def pertainyms(self): raise AssertionError("Must not query pertainyms for match members")
        class WordNet:
            def lemmas(self, word, pos=None): return [Lemma()]
        with patch.object(wf, "_resources", return_value=(None, WordNet())):
            expanded = wf.expand_targets(["verbal"])
        result = wf.match_families(expanded, {"text": "In other words, word."})
        self.assertEqual(result["occurrences"], [])

    def test_query_batches_and_phrases(self):
        expanded = wf.expand_targets([{"word": "compete", "forms": ["competes"],
                                       "related": ["competition"]}, "reward", "lens", "blur", "in fact"], False)
        queries = wf.plan_queries(expanded, max_queries=2)
        self.assertEqual(len(queries), 2)
        self.assertIn("competes", queries[0])
        self.assertIn("competition", queries[0])
        self.assertEqual(queries[0].count("("), 2)
        self.assertNotIn(" AND ", queries[0])
        grouped = wf.plan_queries(expanded, groups={"scene": ["in fact", "lens", "blur", "reward"]})
        self.assertEqual(len(grouped), 2)
        self.assertIn('"in fact"', grouped[0])
        self.assertEqual(grouped[0].count("("), 3)

    def test_validation(self):
        for words in [[], {}, [""], [{"word": "a", "forms": "as"}],
                      [{"word": "a", "related": [{"word": "b", "confirmed": "yes"}]}]]:
            with self.subTest(words=words), self.assertRaises(ValueError):
                wf.expand_targets(words, False)


if __name__ == "__main__":
    unittest.main()
