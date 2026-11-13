import copy
import unittest
from run_music_trial import refresh_sources, choose_artists, choose_text_trials, expand_english_seeds
from preferences import build_preferences


def synthetic_track(ident, artist):
    return {"track_id": str(ident), "title": "Synthetic title", "artists": ["Synthetic"],
            "artist_refs": [{"artist_id": str(artist), "name": "Synthetic"}]}


class TrialTests(unittest.TestCase):
    def test_anonymous_empty_weekly_does_not_prove_no_plays(self):
        local = {"discovery": {"account_id": "1", "playlist_id": "2"}, "sources": {
            "liked": {"status": "partial", "tracks": [], "complete": False},
            "recent": {"status": "partial", "tracks": [], "complete": False}}}
        sources = refresh_sources(local, fetch=lambda *args: {"code": 200, "weekData": []})
        self.assertEqual(sources["weekly"]["status"], "auth_unknown")
        self.assertFalse(sources["weekly"]["complete"])

    def test_rejects_other_accounts_cached_snapshot(self):
        local = {"discovery": {"account_id": "1", "playlist_id": "2"}, "sources": {}}
        with self.assertRaises(ValueError):
            refresh_sources(local, {"source_url": "https://music.163.com/playlist?id=2", "owner_id": "3"})

    def test_recent_evidence_is_not_lost_behind_old_like_volume(self):
        old = {"artist_id": "1", "signals": {"recent_tracks": 0}, "most_recent_observed_at": None}
        recent = {"artist_id": "2", "signals": {"recent_tracks": 1}, "most_recent_observed_at": "2026-11-13T00:00:00Z"}
        self.assertEqual(choose_artists({"artists": [old, recent]}, 1), [recent])

    def test_only_real_liked_english_text_can_seed_extension(self):
        p = build_preferences({"liked": {"status": "available", "complete": True,
            "tracks": [synthetic_track(1, 10), synthetic_track(2, 20)]}})
        requests = []
        def fetch(path, params):
            requests.append((path, params))
            return {"code": 200, "songs": []}
        trials = [{"track_id": "1", "already_liked": True, "match": {"english_text_candidate": False}},
                  {"track_id": "2", "already_liked": True, "match": {"english_text_candidate": True}}]
        result = expand_english_seeds(p, trials, [], ".", fetch)
        self.assertEqual(result["artist_ids"], ["20"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][1]["id"], "20")

    def test_chinese_title_remains_eligible_for_lyric_check(self):
        track = synthetic_track(1, 10)
        track["title"] = "中文标题"
        p = build_preferences({"liked": {"status": "available", "complete": True, "tracks": [track]}})
        picked = choose_text_trials(p, p["artists"], 8)
        self.assertEqual([t["track_id"] for t in picked], ["1"])

    def test_initial_language_probes_cover_different_known_artists(self):
        p = build_preferences({"liked": {"status": "available", "complete": True,
            "tracks": [synthetic_track(1, 10), synthetic_track(2, 10), synthetic_track(3, 20)]}})
        picked = choose_text_trials(p, p["artists"], 2)
        self.assertEqual([t["track_id"] for t in picked], ["1", "3"])

    def test_partial_online_refresh_preserves_cache_with_membership_basis(self):
        local = {"discovery": {"account_id": "1", "playlist_id": "2"}, "sources": {
            "liked": {"status": "partial", "complete": False,
                      "tracks": [synthetic_track(102, 10), synthetic_track(103, 10)]},
            "recent": {"status": "partial", "tracks": [], "complete": False}}}
        online = {"source_url": "https://music.163.com/playlist?id=2", "owner_id": "1",
                  "complete": False, "declared_total": 2, "tracks": [synthetic_track(101, 10)],
                  "missing_ids": ["102"], "checked_at_utc": "2026-11-13T00:00:00Z"}
        p = build_preferences(refresh_sources(local, online, lambda *args: {"code": 200, "weekData": []}))
        by_id = {t["track_id"]: t for t in p["tracks"]}
        self.assertEqual(set(by_id), {"101", "102", "103"})
        self.assertTrue(by_id["102"]["already_liked"])
        self.assertFalse(by_id["103"]["already_liked"])
        self.assertEqual(by_id["103"]["liked_membership_status"], "cached_unverified")

    def test_recent_only_needs_no_account_id(self):
        local = {"discovery": {"account_id": None, "playlist_id": None}, "sources": {
            "liked": {"status": "unavailable", "complete": False, "tracks": []},
            "recent": {"status": "partial", "complete": False, "tracks": [synthetic_track(103, 10)]}}}
        sources = refresh_sources(local, fetch=lambda *args: self.fail("No account API call is valid"))
        p = build_preferences(sources)
        self.assertEqual(p["tracks"][0]["track_id"], "103")
        self.assertEqual(p["sources"]["weekly"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
