import json
import unittest
from import_netease import import_playlist, playlist_id


def track(i):
    return {"id": i, "name": "Synthetic title", "artists": [{"name": "Synthetic artist"}],
            "album": {"name": "Synthetic album"}, "duration": 1000}


class ImportTests(unittest.TestCase):
    def source(self, total=3, ids=(1, 2, 3), details=(2, 3), code=200):
        def fetch(path, params):
            if path.endswith("playlist/detail"):
                return {"code": 200, "playlist": {"name": "Synthetic", "trackCount": total,
                        "trackIds": [{"id": i} for i in ids], "tracks": [track(1)]}}
            return {"code": code, "songs": [track(i) for i in details]}
        return fetch

    def test_urls(self):
        self.assertEqual(playlist_id("https://music.163.com/#/playlist?id=123"), "123")
        self.assertEqual(playlist_id("https://music.163.com/playlist?id=123"), "123")
        for invalid in ("https://example.com/playlist?id=123", "https://music.163.com/song?id=123",
                        "https://music.163.com/playlist?id=1&id=2", "0"):
            with self.assertRaises(ValueError):
                playlist_id(invalid)

    def test_complete_and_unverified_states(self):
        result = import_playlist("123", self.source())
        self.assertTrue(result["complete"])
        self.assertEqual(result["loaded_tracks"], 3)
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["tracks"][0]["artists"], ["Synthetic artist"])
        self.assertFalse(result["playback_verified"])
        self.assertIsNone(result["tracks"][0]["language"])

    def test_partial_duplicate_and_extra_results(self):
        for kwargs in ({"details": (2,)}, {"details": (2, 2, 3)},
                       {"details": (2, 3, 4)}, {"ids": (1, 2, 2, 3)}, {"total": 4}):
            self.assertFalse(import_playlist("123", self.source(**kwargs))["complete"])

    def test_detail_login_failure_preserves_partial(self):
        result = import_playlist("123", self.source(code=301))
        self.assertFalse(result["complete"])
        self.assertEqual(result["loaded_tracks"], 1)
        self.assertEqual(result["missing_ids"], ["2", "3"])
        self.assertIn("301", result["detail_error"])

    def test_private_playlist_not_called_empty(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            import_playlist("123", lambda *args: {"code": 301})

    def test_nonobject_response_has_an_explicit_schema_failure(self):
        for malformed in ([], None, "service error"):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                import_playlist("123", lambda *args: malformed)

    def test_batches_metadata_to_avoid_upstream_truncation(self):
        batches = []
        def fetch(path, params):
            if path.endswith("playlist/detail"):
                return {"code": 200, "playlist": {"trackCount": 205,
                    "trackIds": [{"id": i} for i in range(1, 206)], "tracks": []}}
            ids = json.loads(params["ids"])
            batches.append(ids)
            # Model a server which silently returns at most 200 detail rows.
            return {"code": 200, "songs": [track(i) for i in ids[:200]]}
        result = import_playlist("123", fetch)
        self.assertTrue(result["complete"])
        self.assertEqual(result["loaded_tracks"], 205)
        self.assertEqual([len(b) for b in batches], [100, 100, 5])
        self.assertEqual(result["requests"], 4)


if __name__ == "__main__":
    unittest.main()
