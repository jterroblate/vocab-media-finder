"""Offline behavioral tests for public evidence acquisition and normalization."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_sources import normalize_transcript, PublicCatalog, SourceClient


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(body, status=200, headers=None, url=None):
    result = {"status": status, "body": body, "headers": headers or {}}
    if url:
        result["url"] = url
    return result


class TranscriptTests(unittest.TestCase):
    def test_srt_final_cue_and_real_repetition(self):
        raw = "1\n00:00:00,000 --> 00:00:01,000\nBacteria grow.\n\n2\n00:00:02,000 --> 00:00:03,000\nBacteria grow."
        doc = normalize_transcript(raw, "srt")
        self.assertEqual(doc["text"], "Bacteria grow. Bacteria grow.")
        self.assertEqual(len(doc["cues"]), 2)
        last = doc["cues"][-1]
        self.assertEqual(doc["text"][last["char_start"]:last["char_end"]], "Bacteria grow.")
        self.assertEqual(last["end"], 3)

    def test_rolling_overlap_only_when_time_overlaps(self):
        doc = normalize_transcript("WEBVTT\n\n00:00.000 --> 00:03.000\nWe learn words\n\n00:01.000 --> 00:04.000\nlearn words today\n\n00:05.000 --> 00:06.000\nwords today", "vtt")
        self.assertEqual(doc["text"], "We learn words today words today")
        self.assertEqual(doc["cues"][1]["text"], "today")
        self.assertEqual(doc["cues"][1]["start"], 1)

    def test_vtt_inline_timestamps_and_voice_markup_are_removed(self):
        raw = "WEBVTT\n\n00:01.000 --> 00:03.000\n<v Speaker><00:01.100><c>We learn</c><00:02.000> words.</v>"
        self.assertEqual(normalize_transcript(raw, "vtt")["text"], "We learn words.")

    def test_filmot_start_is_retained_end_is_not_invented(self):
        doc = normalize_transcript({"lang": "en", "isauto": 1, "transcript": [{"s": 1, "t": "The lens"}, {"s": 2, "t": "lens bends light."}]}, "filmot")
        self.assertEqual(doc["cues"][0]["start"], 1)
        self.assertIsNone(doc["cues"][0]["end"])
        self.assertFalse(doc["completeness"]["end_times_complete"])
        self.assertIn("lens lens", doc["text"])
        self.assertTrue(any("rolling overlap" in w for w in doc["warnings"]))

    def test_labels_and_html_navigation_are_not_spoken_body(self):
        doc = normalize_transcript("<nav>Recommendations</nav><p>[Music] A lens bends light.</p><footer>Comments</footer>")
        self.assertEqual(doc["text"], "A lens bends light.")

    def test_ted_identity_not_first_video(self):
        url = "https://www.ted.com/talks/observed_talk?view=transcript"
        objs = [{"@type": "VideoObject", "url": "https://www.ted.com/talks/another", "transcript": "Wrong body."},
                {"@type": "VideoObject", "url": "https://www.ted.com/talks/observed_talk", "transcript": "Right body."}]
        doc = normalize_transcript(json.dumps(objs), "ted_jsonld", source_url=url)
        self.assertEqual(doc["text"], "Right body.")
        with self.assertRaises(ValueError):
            normalize_transcript(json.dumps(objs), "ted_jsonld", source_url="https://www.ted.com/talks/missing")

    def test_bilibili_timing_and_last_body(self):
        doc = normalize_transcript({"body": [{"from": 3.2, "to": 4.3, "content": "First."}, {"from": 5, "to": 7, "content": "Last."}]}, "bilibili")
        self.assertEqual(doc["text"], "First. Last.")
        self.assertEqual(doc["cues"][-1]["end"], 7)

    def test_filmot_selects_manual_english_only(self):
        raw = [{"id": "abcdefghijk", "subtitles": [
            {"lang": "zh", "isauto": 0, "transcript": [{"s": 0, "t": "wrong"}]},
            {"lang": "en", "isauto": 1, "transcript": [{"s": 0, "t": "Automatic."}]},
            {"lang": "en", "isauto": 0, "transcript": [{"s": 0, "t": "Manual."}]}]}]
        doc = normalize_transcript(raw, "filmot", video_id="abcdefghijk")
        self.assertEqual(doc["text"], "Manual.")
        self.assertFalse(doc["is_auto"])


class CatalogTests(unittest.TestCase):
    def test_only_public_fields_and_unordered_term_frequencies_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = PublicCatalog(temp)
            doc = normalize_transcript("A lens bends light. The lens is clear.", source_url="https://example.org/video?token=secret&lang=en")
            catalog.put_video({"id": "public-video", "title": "Lenses", "url": "https://example.org/video?api_key=secret", "student": "PRIVATE STUDENT", "targets": ["PRIVATE TARGET"], "review": "PRIVATE REVIEW", "message": "PRIVATE MESSAGE"}, doc)
            families = {"families": [{"members": [{"text": "lens"}]}]}
            hits = catalog.search(families)
            self.assertEqual(hits[0]["id"], "public-video")
            self.assertTrue(hits[0]["retrieval_only"])
            stored = catalog.db.execute("SELECT metadata FROM videos").fetchone()[0]
            self.assertNotIn("PRIVATE", stored)
            self.assertNotIn("secret", stored)
            self.assertEqual(catalog.db.execute("SELECT frequency FROM terms WHERE term='lens'").fetchone()[0], 2)
            self.assertNotIn(doc["text"], stored)
            catalog.close()
            reopened = PublicCatalog(temp)
            self.assertEqual(len(reopened.search(families)), 1)
            reopened.close()


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def client(self, responses=(), key="key-is-private"):
        t = Transport(responses)
        return SourceClient(self.temp.name, api_key=key, transport=t), t

    def test_missing_key_does_not_request_or_cache_targets(self):
        c, t = self.client(key="")
        result = c.search_filmot("private-target")
        self.assertEqual(result["status"], "missing_key")
        self.assertFalse(t.calls)
        self.assertFalse(list(Path(self.temp.name).rglob("*.json")))

    def test_filmot_jsonl_and_secret_not_persisted(self):
        c, t = self.client([response('{"id":"abcdefghijk","title":"A video","duration":200,"lang":"en","hits":[]}\n')])
        result = c.search_filmot("lens (blur | blurred)")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["videos"][0]["original_id"], "abcdefghijk")
        self.assertEqual(t.calls[0][1]["X-API-Key"], "key-is-private")
        self.assertNotIn("key-is-private", json.dumps(result))
        self.assertFalse(list(Path(self.temp.name).rglob("*.json")))
        self.assertIn("searchManualSubs=1", t.calls[0][0])
        c.search_filmot("lens (blur | blurred)")
        self.assertEqual(len(t.calls), 1)

    def test_credit_exhaustion_prevents_next_call(self):
        c, t = self.client([response("", headers={"X-API-Credits-Remaining": "0", "X-API-Credits-Reset": "2026-12-01"})])
        # Empty successful export means no matches, while quota still updates.
        c.search_filmot("lens")
        result = c.get_filmot_transcripts(["abcdefghijk"])
        self.assertEqual(result["status"], "quota_exhausted")
        self.assertEqual(result["quota"]["remaining"], 0)
        self.assertEqual(len(t.calls), 1)

    def test_quota_response_and_secret_echo_are_redacted(self):
        c, t = self.client([response({"error": "quota_exhausted", "remaining": 0, "limit": 100, "used": 100, "cost": 5, "echo": "key-is-private"}, 429)])
        result = c.search_filmot("lens")
        self.assertEqual(result["status"], "quota_exhausted")
        self.assertNotIn("key-is-private", json.dumps(result))

    def test_bilibili_cid_overrides_title_and_requested_page(self):
        body = {"code": 0, "data": {"bvid": "BV114411S7fT", "aid": 1, "title": "Subtitle collection", "pages": [{"cid": 110346866, "page": 35, "part": "p36 Glasses", "duration": 264}]}}
        c, t = self.client([response(body)])
        result = c.resolve_bilibili("BV114411S7fT", cid=110346866, page=36)
        self.assertEqual(result["video"]["page"], 35)
        self.assertTrue(result["video"]["url"].endswith("?p=35"))
        self.assertTrue(result["verification"]["part_mapping_checked"])
        self.assertFalse(result["verification"]["subtitle_body_checked"])
        self.assertFalse(result["verification"]["playback_checked"])

    def test_login_required_is_not_no_subtitles(self):
        c, t = self.client([response({"code": 0, "data": {"subtitle": {"need_login_subtitle": True, "subtitles": []}}})])
        result = c.inspect_bilibili_subtitles("BV114411S7fT", 110346866)
        self.assertEqual(result["status"], "login_required")
        self.assertFalse(result["subtitle_body_checked"])

    def test_top_level_bilibili_login_flag_is_respected(self):
        c, t = self.client([response({"code": 0, "data": {"need_login_subtitle": True, "subtitle": {"subtitles": []}}})])
        self.assertEqual(c.inspect_bilibili_subtitles("BV114411S7fT", 110346866)["status"], "login_required")

    def test_missing_filmot_body_is_explicit(self):
        c, t = self.client([response([{"id": "abcdefghijk", "subtitles": []}])])
        result = c.get_filmot_transcripts(["abcdefghijk"])
        self.assertEqual(result["status"], "transcript_unavailable")
        self.assertFalse(result["documents"])

    def test_key_is_not_forwarded_on_cross_origin_redirect(self):
        class FakeOpener:
            def __init__(self, handler):
                self.handler = handler
            def open(self, request, timeout):
                return self.handler.redirect_request(request, None, 302, "Moved", {}, "https://unrelated.example/")
        with patch("video_sources.urllib.request.build_opener", side_effect=lambda handler: FakeOpener(handler)):
            c = SourceClient(self.temp.name, api_key="key-is-private")
            result = c.search_filmot("lens")
        self.assertEqual(result["status"], "invalid_response")
        self.assertNotIn("key-is-private", json.dumps(result))

    def test_blocked_source_cools_down_different_urls(self):
        c, t = self.client([response("blocked", 412)])
        first = c.resolve_bilibili("BV114411S7fT", page=1)
        second = c.resolve_bilibili("BV1zt4y1z72D", page=1)
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(second["status"], "cooldown")
        self.assertEqual(len(t.calls), 1)

    def test_timeout_retries_once_and_memoizes_failure(self):
        c, t = self.client([TimeoutError(), TimeoutError()])
        result = c.fetch_ted("https://www.ted.com/talks/observed")
        self.assertEqual(result["status"], "timeout")
        c.fetch_ted("https://www.ted.com/talks/observed")
        self.assertEqual(len(t.calls), 2)

    def test_ted_redirect_identity_is_rejected(self):
        c, t = self.client([response("{}", url="https://www.ted.com/talks/different")])
        result = c.fetch_ted("https://www.ted.com/talks/observed")
        self.assertEqual(result["status"], "identity_mismatch")

    def test_public_http_cache_reuses_download_and_expires(self):
        body = '<script type="application/ld+json">' + json.dumps({"@type": "VideoObject", "url": "https://www.ted.com/talks/observed", "transcript": "One lens.", "name": "Observed"}) + '</script>'
        c, t = self.client([response(body)])
        self.assertEqual(c.fetch_ted("https://www.ted.com/talks/observed")["status"], "ok")
        other, no_calls = self.client()
        result = other.fetch_ted("https://www.ted.com/talks/observed")
        self.assertTrue(result["cache_hit"])
        self.assertFalse(no_calls.calls)
        cache = next(Path(self.temp.name).rglob("*.json"))
        stored = json.loads(cache.read_text())
        stored["expires_at"] = 0
        cache.write_text(json.dumps(stored))
        third, fresh = self.client([response(body)])
        self.assertFalse(cache.exists())
        third.fetch_ted("https://www.ted.com/talks/observed")
        self.assertEqual(len(fresh.calls), 1)

    def test_filmot_batches_ten_and_keeps_final_line(self):
        ids = [f"abcdefgh{i:03d}" for i in range(11)]
        def rows(chunk):
            return [{"id": i, "subtitles": [{"lang": "en", "isauto": 0, "transcript": [{"s": 0, "t": "First."}, {"s": 1, "t": "Last."}]}]} for i in chunk]
        c, t = self.client([response(rows(ids[:10])), response(rows(ids[10:]))])
        result = c.get_filmot_transcripts(ids)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["documents"]), 11)
        self.assertEqual(result["documents"][-1]["text"], "First. Last.")
        self.assertEqual(len(t.calls), 2)

    def test_filmot_public_document_cache_cross_client_expiry_and_privacy(self):
        raw = [{"id": "abcdefghijk", "echoed_request": "PRIVATE_QUERY", "api_key": "key-is-private",
                "review": "PRIVATE_REVIEW", "subtitles": [{"lang": "en", "isauto": 0,
                "transcript": [{"s": 0, "t": "A lens bends light."}]}]}]
        first, transport = self.client([response(raw)])
        result = first.get_filmot_transcripts(["abcdefghijk"])
        self.assertFalse(result["cache_hit"])
        cache = next((Path(self.temp.name) / "documents").glob("*.json"))
        stored_text = cache.read_text()
        for private in ("key-is-private", "PRIVATE_QUERY", "PRIVATE_REVIEW", "X-API-Key", "echoed_request"):
            self.assertNotIn(private, stored_text)
        stored = json.loads(stored_text)
        self.assertEqual(set(stored), {"expires_at", "document"})
        second, no_calls = self.client(key="")
        reused = second.get_filmot_transcripts(["abcdefghijk"])
        self.assertEqual(reused["status"], "ok")
        self.assertTrue(reused["cache_hit"])
        self.assertEqual(reused["documents"][0]["text"], "A lens bends light.")
        self.assertFalse(no_calls.calls)
        stored["expires_at"] = 0
        cache.write_text(json.dumps(stored))
        third, fresh_calls = self.client([response(raw)])
        self.assertFalse(cache.exists())
        self.assertFalse(third.get_filmot_transcripts(["abcdefghijk"])["cache_hit"])
        self.assertEqual(len(fresh_calls.calls), 1)


if __name__ == "__main__":
    unittest.main()
