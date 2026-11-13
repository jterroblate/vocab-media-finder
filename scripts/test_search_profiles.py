"""Offline regressions for network, difficulty and direct-entry selection."""
import copy
import unittest

from search_profiles import (classify_video_url, platform_search_queries,
                             profile_reasons, resolve_profile)


def video(**overrides):
    return {"url": "https://www.bilibili.com/video/BV114411S7fT/?p=35",
            "duration_seconds": 120,
            "difficulty_review": {"level": "easy", "basis": "Short everyday clauses and clear delivery.",
                                  "confidence": "medium"}, **overrides}


class ProfileTests(unittest.TestCase):
    def test_default_is_easy_and_domestic(self):
        p = resolve_profile()
        self.assertEqual((p["network"], p["level"]), ("domestic", "easy"))
        self.assertEqual(p["platforms"], ["bilibili", "douyin", "xiaohongshu"])
        self.assertEqual((p["min_duration"], p["max_duration"]), (60, 360))

    def test_global_adds_overseas_entries(self):
        p = resolve_profile(network="global")
        self.assertIn("youtube", p["platforms"])
        self.assertIn("other", p["platforms"])

    def test_explicit_overrides_win_and_do_not_mutate_config(self):
        config = {"network": "global", "level": "advanced", "platforms": ["bilibili"], "min_duration": 100}
        original = copy.deepcopy(config)
        p = resolve_profile(config, level="starter", min_duration=30)
        self.assertEqual((p["level"], p["min_duration"], p["max_duration"]), ("starter", 30, 180))
        self.assertEqual(config, original)

    def test_resolved_profiles_can_be_loaded_again(self):
        p = resolve_profile(level="standard", platforms=["douyin"])
        self.assertEqual(resolve_profile(p), p)

    def test_domestic_never_silently_adds_youtube(self):
        with self.assertRaises(ValueError):
            resolve_profile(network="domestic", platforms=["youtube"])

    def test_platform_array_validation(self):
        for platforms in ("bilibili", [], ["unknown"], [True]):
            with self.subTest(platforms=platforms), self.assertRaises(ValueError):
                resolve_profile(platforms=platforms)

    def test_profile_field_validation(self):
        for config in ({"network": "vpn"}, {"level": "A1"}, {"levle": "easy"}, {"access_notes": []}, []):
            with self.subTest(config=config), self.assertRaises(ValueError):
                resolve_profile(config)

    def test_duration_configuration_is_finite_and_ordered(self):
        for minimum, maximum in ((True, 5), (0, float("nan")), (-1, 5), (5, 4), (0, 0), (0, float("inf")), (0, 10 ** 1000)):
            with self.subTest(minimum=minimum, maximum=maximum), self.assertRaises(ValueError):
                resolve_profile(min_duration=minimum, max_duration=maximum)

    def test_bilibili_specific_parts_and_av_ids(self):
        self.assertEqual(classify_video_url(video()["url"]), "bilibili")
        self.assertEqual(classify_video_url("https://www.bilibili.com/video/av170001"), "bilibili")
        self.assertIsNone(classify_video_url("https://www.bilibili.com/video/BVbad"))

    def test_douyin_direct_and_short_entries(self):
        for url in ("https://www.douyin.com/video/7123456789012345678", "https://v.douyin.com/AbCD123/"):
            self.assertEqual(classify_video_url(url), "douyin")
        self.assertIsNone(classify_video_url("https://www.douyin.com/user/abc"))

    def test_xiaohongshu_direct_and_short_entries(self):
        for url in ("https://www.xiaohongshu.com/explore/abcdef1234567890abcdef12",
                    "https://www.xiaohongshu.com/discovery/item/abcdef1234567890abcdef12",
                    "http://xhslink.com/a/ABC123"):
            self.assertEqual(classify_video_url(url), "xiaohongshu")
        self.assertIsNone(classify_video_url("https://www.xiaohongshu.com/search_result?keyword=English"))

    def test_youtube_watch_shorts_and_short_host(self):
        for url in ("https://www.youtube.com/watch?v=abcdefghijk", "https://youtu.be/abcdefghijk?t=10",
                    "https://www.youtube.com/shorts/abcdefghijk"):
            self.assertEqual(classify_video_url(url), "youtube")

    def test_empty_misleading_or_duplicate_youtube_ids_rejected(self):
        for url in ("https://www.youtube.com/watch?v=", "https://www.youtube.com/watch?foo=v=abcdefghijk",
                    "https://www.youtube.com/watch?v=abcdefghijk&v=lmnopqrstuv", "https://youtu.be/abcdefghijk/extra"):
            self.assertIsNone(classify_video_url(url))

    def test_unsafe_or_listing_urls_rejected(self):
        for url in ("javascript:alert(1)", "ftp://bilibili.com/video/BV114411S7fT", "https://user:pass@example.org/watch/1",
                    "https://example.org/search?q=English", "https://example.org/channel/example", "https://example.org/file.pdf",
                    "https://example.org/", "https://www.youtube.com/@channel", "https://example.org/video/hello world"):
            with self.subTest(url=url):
                self.assertIsNone(classify_video_url(url))

    def test_other_sites_still_need_actual_video_confirmation(self):
        self.assertEqual(classify_video_url("https://player.vimeo.com/video/123456789"), "other")
        p = resolve_profile(network="global")
        r = profile_reasons(video(url="https://example.org/lesson/story"), p)
        self.assertIn("video_format_unconfirmed", r)

    def test_queries_are_public_bounded_and_platform_specific(self):
        expected = {"bilibili": "bilibili.com", "douyin": "douyin.com", "xiaohongshu": "xiaohongshu.com", "youtube": "youtube.com"}
        for platform, domain in expected.items():
            queries = platform_search_queries(platform, "family cooking " * 100)
            self.assertIn(domain, queries[0])
            self.assertTrue(all(len(q) < 230 and "\n" not in q for q in queries))

    def test_explicit_platform_preference_applied(self):
        p = resolve_profile(platforms=["xiaohongshu"])
        self.assertIn("platform_not_allowed", profile_reasons(video(), p))

    def test_network_blocks_youtube_even_if_video_is_easy(self):
        reasons = profile_reasons(video(url="https://youtu.be/abcdefghijk"), resolve_profile())
        self.assertIn("network_platform_unavailable", reasons)

    def test_easy_selection_requires_review_basis_and_confidence(self):
        p = resolve_profile()
        self.assertIn("difficulty_review_missing", profile_reasons(video(difficulty_review=None), p))
        self.assertIn("difficulty_review_unconfirmed", profile_reasons(video(difficulty_review={"level": "easy", "basis": "", "confidence": "high"}), p))
        self.assertIn("difficulty_review_unconfirmed", profile_reasons(video(difficulty_review={"level": "easy", "basis": "slow", "confidence": "low"}), p))

    def test_target_level_allows_easier_but_not_harder_videos(self):
        p = resolve_profile()
        self.assertEqual(profile_reasons(video(difficulty_review={"level": "starter", "basis": "Repeated simple actions.", "confidence": "high"}), p), [])
        self.assertIn("difficulty_above_requested_level", profile_reasons(video(difficulty_review={"level": "advanced", "basis": "Abstract idioms and rapid delivery.", "confidence": "high"}), p))

    def test_any_does_not_invent_or_require_difficulty(self):
        self.assertEqual(profile_reasons(video(difficulty_review=None), resolve_profile(level="any")), [])

    def test_video_duration_validation_rejects_non_numeric_and_nan(self):
        for value in (True, float("nan"), None, 0, 500):
            self.assertIn("duration_outside_requested_range", profile_reasons(video(duration_seconds=value), resolve_profile()))

    def test_short_link_requires_verified_same_platform_final_content(self):
        p = resolve_profile()
        original = video(url="https://v.douyin.com/ABC123/")
        self.assertIn("short_link_unresolved", profile_reasons(original, p))
        original.update(link_resolved=True, resolved_url="https://www.douyin.com/video/7123456789012345678")
        self.assertEqual(profile_reasons(original, p), [])
        original["resolved_url"] = "https://v.douyin.com/Another/"
        self.assertIn("short_link_unresolved", profile_reasons(original, p))
        original["resolved_url"] = "https://youtu.be/abcdefghijk"
        self.assertIn("short_link_unresolved", profile_reasons(original, p))

    def test_xiaohongshu_text_note_is_not_accepted_as_video(self):
        v = video(url="https://www.xiaohongshu.com/explore/abcdef1234567890abcdef12")
        self.assertIn("video_format_unconfirmed", profile_reasons(v, resolve_profile()))
        v["video_format_confirmed"] = True
        self.assertEqual(profile_reasons(v, resolve_profile()), [])

    def test_xhs_short_link_requires_both_resolution_and_video_format(self):
        v = video(url="http://xhslink.com/a/ABC123", link_resolved=True,
                  resolved_url="https://www.xiaohongshu.com/explore/abcdef1234567890abcdef12")
        self.assertIn("video_format_unconfirmed", profile_reasons(v, resolve_profile()))
        v["video_format_confirmed"] = True
        self.assertEqual(profile_reasons(v, resolve_profile()), [])

    def test_login_and_blocked_are_not_relabelled_public(self):
        self.assertIn("viewer_login_required", profile_reasons(video(access_status="login_required"), resolve_profile()))
        self.assertIn("viewer_access_blocked", profile_reasons(video(access_status="blocked"), resolve_profile()))
        # Caption login is separate from whether the video entry can be opened.
        v = video(subtitle_status="login_required")
        self.assertEqual(profile_reasons(v, resolve_profile()), [])
        self.assertNotIn("access_status", v)

    def test_declared_resolved_direct_url_cannot_switch_platform(self):
        v = video(link_resolved=True, resolved_url="https://youtu.be/abcdefghijk")
        self.assertIn("resolved_link_platform_mismatch", profile_reasons(v, resolve_profile()))


if __name__ == "__main__":
    unittest.main()
