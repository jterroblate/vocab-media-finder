"""Behavioral regressions for review, bundle selection and non-sending exports."""
import copy
import tempfile
import unittest
import subprocess
import sys
import json
import io
import os
from pathlib import Path
from unittest.mock import patch

from word_families import expand_targets
from video_sources import normalize_transcript, PublicCatalog
from video_pipeline import make_match, review_template, review_match, select_bundle, render_bundle, discover, web_search_plan, main


def video(id="example", **kwargs):
    return {"id": id, "title": "Example", "url": "https://www.bilibili.com/video/BV114411S7fT/?p=35",
            "duration_seconds": 264, "english_audio_confirmed": True, "natural_context": True,
            "source_identity_confirmed": True, "subtitle_available": True,
            "full_transcript_confirmed": True,
            "part_mapping_checked": True, **kwargs}


def reviewed(words, text, id="example"):
    doc = normalize_transcript(text, source_url="https://example.org/public-transcript")
    r = make_match(expand_targets(words, use_resources=False), doc, video(id))
    decisions = review_template(r)
    decisions["video_review"] = {}
    for x in decisions["decisions"].values():
        x.update(action="accept", relation_confirmed=True)
    return review_match(r, decisions)


class PipelineTests(unittest.TestCase):
    def test_word_family_spans_and_source_entries(self):
        r = reviewed([{"word": "compete", "forms": ["competed"], "related": ["competition"]},
                      "competition"], "They competed. Competition matters. Competition continues.")
        self.assertEqual(r["stats"]["family_coverage"], 1)
        self.assertEqual(r["stats"]["total_occurrences"], 3)
        self.assertEqual(r["source_target_hits"]["competition"]["exact"], 2)
        self.assertIn("compete → competition×2", render_bundle(select_bundle([r])))

    def test_new_sense_included_and_labelled(self):
        doc = normalize_transcript("Accommodation lets the lens focus.")
        r = make_match(expand_targets(["accommodation"], False), doc, video())
        d = review_template(r)
        d["video_review"] = {}
        for x in d["decisions"].values():
            x.update(action="accept", sense="new", note="eye focusing")
        checked = review_match(r, d)
        self.assertEqual(checked["stats"]["total_occurrences"], 1)
        self.assertEqual(checked["stats"]["new_sense_occurrences"], 1)
        self.assertIn("新义项", render_bundle(select_bundle([checked])))
        self.assertIn("eye focusing", render_bundle(select_bundle([checked])))

    def test_generated_relations_need_review_confirmation(self):
        r = reviewed([{"word": "compete", "related": [{"word": "competition", "confirmed": False}]}], "Competition.")
        d = review_template(r)
        for x in d["decisions"].values():
            x.update(action="accept", relation_confirmed=False)
        with self.assertRaises(ValueError):
            review_match(r, d)

    def test_pending_not_selectable(self):
        doc = normalize_transcript("Cats run.")
        r = make_match(expand_targets(["cats"], False), doc, video())
        self.assertEqual(select_bundle([r])["items"], [])

    def test_fragment_cannot_claim_full_video_frequency(self):
        r = reviewed(["lens"], "Lens.")
        r["video"]["full_transcript_confirmed"] = False
        self.assertEqual(select_bundle([r])["items"], [])
        text = render_bundle(select_bundle([r]))
        self.assertIn("缺少全片英文文稿确认", text)

    def test_export_preserves_scope_and_uncovered_family(self):
        r = reviewed(["lens", "ecosystem"], "Lens.")
        bundle = select_bundle([r])
        self.assertEqual(bundle["target_count"], 2)
        self.assertIn("本组未覆盖：ecosystem", render_bundle(bundle))

    def test_selection_values_breadth_over_one_repeated_word(self):
        words = ["bacteria", "lens", "blur", "layer", "crude"]
        narrow = reviewed(words, "bacteria " * 25, "narrow")
        broad = reviewed(words, "Lens blur layer crude.", "broad")
        self.assertEqual(select_bundle([narrow, broad], limit=1)["items"][0]["video"]["id"], "broad")

    def test_versions_not_counted_twice(self):
        a = reviewed(["lens"], "Lens.", "first")
        b = copy.deepcopy(a)
        b["report_id"] = "other"
        b["video"]["id"] = "other_bili_copy"
        self.assertEqual(len(select_bundle([a, b])["items"]), 1)

    def test_bili_requirement_and_english_audio_gate(self):
        r = reviewed(["lens"], "Lens.")
        r["video"]["url"] = "https://www.youtube.com/watch?v=12345678901"
        self.assertEqual(select_bundle([r], platform="bilibili")["items"], [])
        r["video"]["url"] = video()["url"]
        r["video"]["english_audio_confirmed"] = False
        self.assertEqual(select_bundle([r])["items"], [])

    def test_export_has_plain_links_and_no_sender_side_effect(self):
        bundle = select_bundle([reviewed([{"word": "compete", "related": ["competition"]}], "Competition twice? Competition.")])
        with tempfile.TemporaryDirectory() as p:
            receipt = Path(p) / "sender.json"
            receipt.write_text('{"status":"submitted"}')
            text = render_bundle(bundle)
            self.assertIn("派生关联", text)
            self.assertIn("https://www.bilibili.com/video/", text)
            self.assertNotIn("/Users/", text)
            self.assertEqual(receipt.read_text(), '{"status":"submitted"}')

    def test_scope_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            select_bundle([reviewed(["lens"], "Lens."), reviewed(["crude"], "Crude.")])

    def test_evidence_booleans_reject_strings(self):
        r = reviewed(["lens"], "Lens.")
        d = review_template(r)
        for decision in d["decisions"].values():
            decision.update(action="accept", relation_confirmed="false")
        with self.assertRaises(ValueError):
            review_match(r, d)
        d["video_review"]["english_audio_confirmed"] = "false"
        with self.assertRaises(ValueError):
            review_match(r, d)

    def test_invalid_video_links_not_delivered(self):
        r = reviewed(["lens"], "Lens.")
        for url in ["https://www.youtube.com/watch?v=", "https://www.youtube.com/watch?foo=v=123", "ftp://bilibili.com/video/BVbad"]:
            r["video"]["url"] = url
            self.assertEqual(select_bundle([r])["items"], [])

    def test_select_cli_never_overwrites_a_report(self):
        with tempfile.TemporaryDirectory() as p:
            target = Path(p) / "report.json"
            target.write_text(json.dumps(reviewed(["lens"], "Lens.")))
            before = target.read_bytes()
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("video_pipeline.py")),
                                     "select", "--reports", str(target), "--output", str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_bytes(), before)

    def test_discovery_requires_no_credentials_or_network_client(self):
        families = expand_targets([{"word": "compete", "related": ["competition"]}, "breed"], False)
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {"FILMOT_API_KEY": "never-use-this-value"}), \
                patch("video_pipeline.SourceClient", side_effect=AssertionError("No API client needed")), \
                patch.object(Path, "read_text", side_effect=AssertionError("Do not read credentials")):
            result = discover(families, cache_dir=folder)
        self.assertEqual(result["status"], "web_search_required")
        self.assertEqual(result["mode"], "keyless")
        self.assertEqual(result["api_calls"], 0)
        self.assertFalse(result["web_search_executed"])
        self.assertEqual(result["remote_candidates"], [])
        self.assertTrue(result["web_search_queries"])
        self.assertTrue(result["next_steps"])
        self.assertNotIn("never-use-this-value", json.dumps(result))

    def test_existing_public_candidates_do_not_stop_fresh_web_discovery(self):
        families = expand_targets([{"word": "compete", "private_student_label": "NOT_PUBLIC"}], False)
        with tempfile.TemporaryDirectory() as folder:
            catalog = PublicCatalog(folder)
            catalog.put_video({"id": "public-video", "title": "Community race", "url": "https://www.youtube.com/watch?v=abcdefghijk"},
                              normalize_transcript("People compete in a friendly race."))
            catalog.close()
            db = Path(folder) / "catalog.sqlite3"
            before = db.read_bytes()
            result = discover(families, cache_dir=folder)
            self.assertEqual(db.read_bytes(), before)
            self.assertNotIn(b"NOT_PUBLIC", db.read_bytes())
        self.assertTrue(result["local_candidates"])
        self.assertTrue(result["web_search_queries"])
        self.assertEqual(result["status"], "web_search_required")

    def test_web_queries_are_bounded_and_cover_requested_scene_groups(self):
        families = expand_targets([{"word": "compete", "related": ["competition"]}, "breed", "habit", "reward"], False)
        plan = web_search_plan(families, groups=[["habit", "reward"]])
        self.assertIn('"habit" "reward"', plan[0]["query"])
        self.assertTrue(all(set(x["target_words"]) == {"habit", "reward"} for x in plan))
        self.assertTrue(any("story" in x["query"] for x in plan))
        self.assertTrue(any("interview" in x["query"] for x in plan))
        self.assertTrue(all("site:ted.com" not in x["query"] and " | " not in x["query"] for x in plan))
        words = ["word" + chr(97 + i // 26) + chr(97 + i % 26) for i in range(200)]
        long_list = expand_targets(words, False)
        bounded = web_search_plan(long_list, max_queries=8)
        self.assertEqual(len(bounded), 8)
        self.assertTrue(all(len(item["query"]) <= 200 and len(item["family_ids"]) <= 2 for item in bounded))
        self.assertEqual(web_search_plan(long_list, max_queries=0), [])

    def test_platform_preference_defers_bilibili_lookup_until_title_is_known(self):
        with tempfile.TemporaryDirectory() as folder:
            result = discover(expand_targets(["compete"], False), cache_dir=folder, platform="bilibili")
        self.assertEqual(result["platform"], "bilibili")
        self.assertTrue(result["bilibili_lookup"]["required"])
        self.assertIn("{english_title}", result["bilibili_lookup"]["queries"][0])
        self.assertFalse(any("bilibili" in query for query in result["web_search_queries"]))

    def test_many_groups_keep_broad_and_actual_variant_queries_in_small_budget(self):
        entries = [{"word": word, "forms": [form]} for word, form in [
            ("compete", "competed"), ("breed", "bred"), ("build", "built"),
            ("run", "ran"), ("swim", "swam"), ("think", "thought"),
            ("grow", "grew"), ("drive", "drove"), ("speak", "spoke"),
            ("write", "wrote"), ("sing", "sang"), ("give", "gave")]]
        groups = [[entry["word"]] for entry in entries]
        plan = web_search_plan(expand_targets(entries, False), groups=groups, max_queries=8)
        self.assertEqual(len(plan), 8)
        self.assertEqual(plan[0]["target_words"], ["compete"])
        self.assertTrue(any(item["kind"] == "cooccurrence" for item in plan))
        self.assertTrue(any(item["kind"] == "broad_story" for item in plan))
        variants = [item for item in plan if item["kind"] == "variant_context"]
        self.assertTrue(variants)
        self.assertTrue(any('"competed"' in item["query"] for item in variants))
        self.assertEqual([item["group_index"] for item in plan[:3]], [0, 0, 0])

    def test_unchanged_forms_are_interview_queries_not_claimed_variants(self):
        plan = web_search_plan(expand_targets(["habit", "reward"], False))
        self.assertIn("interview_context", {item["kind"] for item in plan})
        self.assertNotIn("variant_context", {item["kind"] for item in plan})

    def test_catalog_only_does_not_claim_to_have_executed_web_search(self):
        with tempfile.TemporaryDirectory() as folder:
            result = discover(expand_targets(["lens"], False), cache_dir=folder, catalog_only=True)
        self.assertEqual(result["status"], "catalog_candidates_require_verification")
        self.assertEqual(result["web_search_queries"], [])
        self.assertFalse(result["web_search_executed"])

    def test_keyless_discovery_cli_accepts_platform(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "families.json").write_text(json.dumps(expand_targets(["compete"], False)))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("video_pipeline.py")),
                                     "discover", "--families", str(p / "families.json"), "--platform", "bilibili",
                                     "--cache-dir", str(p / "cache")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["platform"], "bilibili")
        self.assertEqual(value["status"], "web_search_required")
        self.assertEqual(value["api_calls"], 0)

    def test_public_transcript_cli_disables_client_credential_lookup(self):
        with patch.object(sys, "argv", ["video_pipeline.py", "transcript", "--url", "https://www.ted.com/talks/example"]), \
                patch("video_pipeline.SourceClient") as client, patch("sys.stdout", new_callable=io.StringIO):
            client.return_value.fetch_ted.return_value = {"status": "ok"}
            main()
            self.assertEqual(client.call_args.kwargs["api_key"], "")

    def test_local_transcript_does_not_instantiate_api_client(self):
        with tempfile.TemporaryDirectory() as folder:
            text = Path(folder) / "transcript.txt"
            text.write_text("They competed in a race.")
            with patch.object(sys, "argv", ["video_pipeline.py", "transcript", "--input", str(text)]), \
                    patch("video_pipeline.SourceClient", side_effect=AssertionError("No client needed")), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                main()
                self.assertIn("competed", json.loads(output.getvalue())["text"])


if __name__ == "__main__":
    unittest.main()
