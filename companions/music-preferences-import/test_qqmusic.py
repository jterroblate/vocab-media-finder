import base64
import unittest

from qqmusic_api import QQMusicClient, QQMusicError, normalize_qq_track


SINGER_MID = "A00000000000001"


def song(ident, singer_mid=SINGER_MID):
    return {"id": ident, "mid": f"S{ident:014d}", "name": "Synthetic song", "interval": 120,
            "singer": [{"id": 7, "mid": singer_mid, "name": "Synthetic artist"}],
            "album": {"id": 4, "mid": "B00000000000004", "name": "Synthetic album"}, "language": 5}


def envelope(data, code=0):
    return {"code": 0, "req_1": {"code": code, "data": data}}


class QQApiTests(unittest.TestCase):
    def test_normalization_retains_mid_numeric_identifiers_and_unknowns(self):
        result = normalize_qq_track(song(1))
        self.assertEqual(result["platform"], "qq")
        self.assertEqual(result["track_id"], "S00000000000001")
        self.assertEqual(result["provider_refs"], {"song_id": "1", "song_mid": "S00000000000001"})
        self.assertEqual(result["artist_refs"][0]["provider_refs"]["singer_id"], "7")
        self.assertEqual(result["duration_ms"], 120000)
        self.assertIsNone(result["language"])
        self.assertFalse(result["playback_verified"])

    def test_numeric_fallback_does_not_fabricate_mid_or_play_url(self):
        raw = song(1)
        del raw["mid"]
        del raw["singer"][0]["mid"]
        track = normalize_qq_track(raw)
        self.assertEqual(track["track_id"], "id:1")
        self.assertEqual(track["artist_refs"][0]["artist_id"], "id:7")
        self.assertIsNone(track["play_url"])

    def test_playlist_pagination_uses_actual_returned_rows(self):
        calls = []
        def fetch(module, method, parameters):
            calls.append(parameters)
            start = parameters["song_begin"]
            return envelope({"total_song_num": 4, "hasmore": start == 0,
                             "songlist": [song(start + 1), song(start + 2)]})
        report = QQMusicClient(fetch).playlist("123")
        self.assertTrue(report["complete"])
        self.assertEqual(report["loaded_tracks"], 4)
        self.assertEqual([c["song_begin"] for c in calls], [0, 2])
        self.assertFalse(report["authentication_used"])

    def test_playlist_limit_and_duplicates_cannot_be_complete(self):
        c = QQMusicClient(lambda *args: envelope({"total_song_num": 10, "hasmore": True,
                                                 "songlist": [song(1), song(2)]}))
        report = c.playlist("123", max_tracks=3)
        self.assertFalse(report["complete"])
        self.assertEqual(report["loaded_tracks"], 2)
        self.assertGreater(report["duplicate_tracks"], 0)

    def test_first_failure_is_explicit_but_later_failure_keeps_partial_metadata(self):
        def fail(*args):
            return envelope({}, code=301)
        with self.assertRaisesRegex(QQMusicError, "rpc_method_error"):
            QQMusicClient(fail).playlist(1)
        calls = []
        def later(module, method, params):
            calls.append(params)
            return (envelope({"total_song_num": 3, "hasmore": True, "songlist": [song(1)]})
                    if len(calls) == 1 else envelope({}, code=500))
        report = QQMusicClient(later).playlist(1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["loaded_tracks"], 1)
        self.assertEqual(report["requests"], 2)
        self.assertEqual(report["detail_error"], "rpc_method_error")

    def test_changed_total_and_invalid_songs_are_not_silently_complete(self):
        def fetch(module, method, params):
            return envelope({"total_song_num": 3 if params["song_begin"] == 0 else 2,
                "hasmore": params["song_begin"] == 0,
                "songlist": [song(1)] if params["song_begin"] == 0 else [song(2)]})
        report = QQMusicClient(fetch).playlist(1)
        self.assertFalse(report["complete"])
        self.assertTrue(report["total_changed_during_read"])

    def test_verified_empty_is_distinct_from_bad_schema(self):
        report = QQMusicClient(lambda *a: envelope({"total_song_num": 0, "songlist": [], "hasmore": False})).playlist(1)
        self.assertTrue(report["complete"])
        with self.assertRaisesRegex(QQMusicError, "playlist_songlist_missing"):
            QQMusicClient(lambda *a: envelope({"total_song_num": 0})).playlist(1)

    def test_artist_limit_is_applied_when_upstream_ignores_number(self):
        client = QQMusicClient(lambda *a: envelope({"totalNum": 30,
            "songList": [{"songInfo": song(i)} for i in range(1, 31)]}))
        self.assertEqual(len(client.artist_tracks(SINGER_MID, limit=2)), 2)
        with self.assertRaisesRegex(QQMusicError, "singer_mid_required"):
            client.artist_tracks("id:7")

    def test_artist_mismatches_are_not_same_artist_evidence(self):
        client = QQMusicClient(lambda *a: envelope({"totalNum": 1,
            "songList": [{"songInfo": song(1, "Z00000000000001") }]}))
        self.assertEqual(client.artist_tracks(SINGER_MID), [])

    def test_song_detail_resolves_numeric_identity_and_checks_response(self):
        client = QQMusicClient(lambda *a: envelope({"track_info": song(1)}))
        track = client.song_details({"track_id": "id:1", "provider_refs": {"song_id": "1"}})
        self.assertEqual(track["track_id"], "S00000000000001")
        with self.assertRaisesRegex(QQMusicError, "identifier_mismatch"):
            client.song_details({"track_id": "id:2"})

    def test_plain_base64_lyrics_are_decoded_only_in_memory(self):
        text = "[00:01.00]A synthetic dream.\n[00:02.00]We wrote this ourselves."
        calls = []
        def fetch(module, method, parameters):
            calls.append(parameters)
            return envelope({"lyric": base64.b64encode(text.encode()).decode()})
        result = QQMusicClient(fetch).lyrics(normalize_qq_track(song(1)))
        self.assertEqual(result["text"], text)
        self.assertEqual(calls[0]["crypt"], 0)
        self.assertEqual(calls[0]["qrc"], 0)
        self.assertFalse(result["full_text_exported"])

    def test_direct_lrc_and_bad_base64_have_distinct_outcomes(self):
        client = QQMusicClient(lambda *a: envelope({"lyric": "[00:01.00]Synthetic words."}))
        self.assertIn("Synthetic", client.lyrics(normalize_qq_track(song(1)))["text"])
        with self.assertRaisesRegex(QQMusicError, "lyric_not_plain"):
            QQMusicClient(lambda *a: envelope({"lyric": "not a base64 lyric !"})).lyrics(normalize_qq_track(song(1)))

    def test_invalid_playlist_id_is_rejected_before_network(self):
        client = QQMusicClient(lambda *a: self.fail("No request expected"))
        for value in ("https://example.com", "-1", True):
            with self.assertRaises(QQMusicError):
                client.playlist(value)


if __name__ == "__main__":
    unittest.main()
