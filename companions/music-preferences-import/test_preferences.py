from copy import deepcopy
import unittest

from preferences import add_artist_candidates, build_preferences


def track(ident, artist="10", name="Same display name", **kwargs):
    return {"track_id": ident, "title": f"Synthetic {ident}", "artists": [name],
            "artist_refs": [{"artist_id": artist, "name": name}], **kwargs}


def sources(**overrides):
    result = {name: {"status": "available", "complete": True, "tracks": [],
                    "scope": name, "checked_at": "2026-11-13T00:00:00Z"}
              for name in ("liked", "weekly", "recent")}
    for name, value in overrides.items():
        result[name].update(value if isinstance(value, dict) else {"tracks": value})
    return result


class PreferenceTests(unittest.TestCase):
    def test_cross_source_is_one_song_not_three_plays(self):
        result = build_preferences(sources(
            liked=[track("1")], weekly=[track("1", platform_score=99, rank=1)],
            recent=[track("1", observed_at="2026-11-13T00:00:00Z")]))
        self.assertEqual(len(result["tracks"]), 1)
        song = result["tracks"][0]
        self.assertEqual(song["evidence_sources"], ["liked", "weekly", "recent"])
        self.assertEqual(len(song["evidence"]), 3)
        self.assertNotIn("play_count", song)
        self.assertEqual(song["evidence"][1]["raw_fields"]["platform_score"], 99)
        self.assertEqual(result["artists"][0]["signals"]["liked_weekly_overlap"], 1)
        self.assertFalse(result["ranking_policy"]["platform_score_is_play_count"])

    def test_same_names_different_artist_ids_are_separate(self):
        result = build_preferences(sources(liked=[track("1", "10"), track("2", "20")]))
        self.assertEqual({a["artist_id"] for a in result["artists"]}, {"10", "20"})
        self.assertTrue(all(len(a["supporting_tracks"]) == 1 for a in result["artists"]))

    def test_missing_artist_id_stays_unresolved(self):
        result = build_preferences(sources(liked=[track("1", None), track("2", None)]))
        self.assertEqual(result["artists"], [])
        self.assertEqual(len(result["unresolved_artists"]), 2)
        self.assertEqual({a["track_id"] for a in result["unresolved_artists"]}, {"1", "2"})

    def test_restricted_source_does_not_become_confirmed_empty(self):
        result = build_preferences(sources(liked=[track("1")], recent={
            "status": "auth_unknown", "complete": True, "tracks": [track("9")] }))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"]["recent"]["status"], "auth_unknown")
        self.assertFalse(result["sources"]["recent"]["complete"])
        self.assertEqual([t["track_id"] for t in result["tracks"]], ["1"])

    def test_confirmed_empty_is_distinct_from_unavailable(self):
        empty = build_preferences(sources())
        unavailable = build_preferences({})
        self.assertEqual(empty["status"], "no_evidence")
        self.assertEqual(unavailable["status"], "no_evidence")
        self.assertEqual(empty["no_evidence_reason"], "confirmed_empty")
        self.assertEqual(unavailable["no_evidence_reason"], "no_usable_sources")
        self.assertEqual(empty["sources"]["recent"]["status"], "available")
        self.assertEqual(unavailable["sources"]["recent"]["status"], "unavailable")

    def test_expansion_does_not_inherit_likes_and_deduplicates_sources(self):
        original = build_preferences(sources(liked=[track("1", "10"), track("2", "20")]))
        collaboration = track("3", "10")
        collaboration["artist_refs"].append({"artist_id": "20", "name": "Second"})
        expanded = add_artist_candidates(original, {
            "10": [track("1", "10"), collaboration], "20": [collaboration],
            "999": [track("99", "999")]})
        by_id = {t["track_id"]: t for t in expanded["tracks"]}
        self.assertEqual(set(by_id), {"1", "2", "3"})
        self.assertTrue(by_id["1"]["already_liked"])
        self.assertFalse(by_id["3"]["already_liked"])
        self.assertEqual(by_id["3"]["origin"], "artist_expansion")
        self.assertEqual(len(by_id["3"]["source_artists"]), 2)
        self.assertEqual(by_id["3"]["evidence"], [])
        self.assertEqual(len(original["tracks"]), 2)

    def test_artist_ranking_prefers_overlap_before_single_source_volume(self):
        result = build_preferences(sources(liked=[track("1", "10")],
            weekly=[track("1", "10"), track("2", "20"), track("3", "20")]))
        self.assertEqual([a["artist_id"] for a in result["artists"]], ["10", "20"])

    def test_recent_repeated_rows_do_not_inflate_artist_support(self):
        result = build_preferences(sources(recent=[
            track("1", observed_at="2026-11-12T00:00:00Z"),
            track("1", observed_at="2026-11-13T00:00:00Z"),
            track("1", observed_at="2026-11-13T00:00:00Z")]))
        self.assertEqual(len(result["tracks"][0]["evidence"]), 2)
        self.assertEqual(result["artists"][0]["signals"]["recent_tracks"], 1)
        self.assertEqual(result["artists"][0]["most_recent_observed_at"], "2026-11-13T00:00:00Z")

    def test_invalid_track_id_marks_incomplete_without_mutation(self):
        data = sources(liked=[track("1"), track(None)])
        before = deepcopy(data)
        result = build_preferences(data)
        self.assertEqual(data, before)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"]["liked"]["invalid_tracks"], 1)


if __name__ == "__main__":
    unittest.main()
