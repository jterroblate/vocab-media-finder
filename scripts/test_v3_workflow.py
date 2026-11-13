"""Integrated viewer profiles, context-first search and honest offline demo."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from demo import run_demo
from search_profiles import resolve_profile
from test_video_pipeline import reviewed
from word_families import expand_targets
from video_pipeline import select_bundle, eligibility, web_search_plan, review_template, review_match, discover
from video_sources import PublicCatalog, normalize_transcript


class V3Tests(unittest.TestCase):
    def easy(self, text="Lens.", **changes):
        r = reviewed(["lens", "compete"], text)
        r["video"].update(duration_seconds=90, difficulty_review={"level":"easy","confidence":"high",
            "basis":"Short familiar sentences, clear dialogue and supporting visuals."}, **changes)
        return r

    def test_short_easy_video_passes_profile_despite_old_duration_default(self):
        r = self.easy()
        self.assertEqual(eligibility(r, profile=resolve_profile()), [])
        self.assertEqual(len(select_bundle([r], profile=resolve_profile())["items"]), 1)

    def test_difficult_dense_video_loses_before_frequency_ranking(self):
        easy = self.easy()
        hard = self.easy("Lens. " * 30)
        hard["video"]["difficulty_review"]["level"] = "advanced"
        self.assertEqual(select_bundle([hard, easy], profile=resolve_profile())["items"][0]["report_id"], easy["report_id"])

    def test_domestic_delivery_rejects_youtube_but_not_research_source(self):
        r = self.easy(url="https://www.youtube.com/watch?v=abcdefghijk")
        self.assertFalse(select_bundle([r], profile=resolve_profile())["items"])
        self.assertTrue(select_bundle([r], profile=resolve_profile(network="global"))["items"])

    def test_xhs_is_video_only_and_douyin_specific_url_is_supported(self):
        r = self.easy(url="https://www.xiaohongshu.com/explore/abcdef1234567890abcdef12")
        self.assertFalse(select_bundle([r], profile=resolve_profile())["items"])
        r["video"]["video_format_confirmed"] = True
        self.assertTrue(select_bundle([r], profile=resolve_profile())["items"])
        r["video"]["url"] = "https://www.douyin.com/video/1234567890123456789"
        self.assertTrue(select_bundle([r], profile=resolve_profile())["items"])

    def test_review_retains_actual_difficulty_and_access_evidence(self):
        r = self.easy()
        d = review_template(r)
        d["video_review"] = {"difficulty_review":r["video"]["difficulty_review"], "access_status":"login_required"}
        for decision in d["decisions"].values(): decision.update(action="accept", relation_confirmed=True)
        checked = review_match(r, d)
        self.assertFalse(select_bundle([checked], profile=resolve_profile())["items"])

    def test_scene_discovery_can_find_content_without_exact_target_in_query(self):
        f = expand_targets(["compete", "lens"], False)
        plan = web_search_plan(f, profile=resolve_profile(), scenes=["a friendly sports day"])
        self.assertEqual(plan[0]["kind"], "scene_first")
        self.assertIn("friendly sports day", plan[0]["query"])
        self.assertNotIn('"compete"', plan[0]["query"])
        self.assertIn("bilibili", plan[1]["query"])

    def test_official_level_is_exported_separately_from_estimate(self):
        from video_pipeline import render_bundle
        r = self.easy(provider_level="Official A1", official_level_source="https://example.org/official-level")
        text = render_bundle(select_bundle([r], profile=resolve_profile()))
        self.assertIn("Official A1", text)
        self.assertIn("https://example.org/official-level", text)

    def test_xhs_pseudo_content_paths_rejected(self):
        for identifier in ["search", "not-a-real-note", "z"*24]:
            r = self.easy(url="https://www.xiaohongshu.com/explore/"+identifier, video_format_confirmed=True)
            self.assertFalse(select_bundle([r], profile=resolve_profile())["items"])

    def test_zero_length_video_never_enters_scoring(self):
        r = self.easy()
        r["video"]["duration_seconds"] = 0
        self.assertFalse(select_bundle([r], profile=resolve_profile(min_duration=0))["items"])

    def test_domestic_research_retains_foreign_original_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = PublicCatalog(directory)
            catalog.put_video({"id":"original","url":"https://www.youtube.com/watch?v=abcdefghijk"},
                              normalize_transcript("Lens."))
            catalog.close()
            result = discover(expand_targets(["lens"], False), cache_dir=directory, profile=resolve_profile())
            self.assertEqual(len(result["local_candidates"]), 1)
            self.assertTrue(result["local_candidates"][0]["viewing_copy_lookup_required"])
            self.assertFalse(result["local_candidates"][0]["viewing_entry_allowed"])

    def test_demo_is_offline_synthetic_and_not_a_recommendation(self):
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            result = run_demo(Path(directory) / "result")
            self.assertEqual((result["families"], result["occurrences"]), (4,14))
            self.assertEqual(result["real_video_recommendations"], 0)
            with self.assertRaises(ValueError): run_demo(Path(directory) / "result")


if __name__ == "__main__": unittest.main()
