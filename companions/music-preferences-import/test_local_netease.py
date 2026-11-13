from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from local_netease import DATABASE_NAME, normalize_local_track, read_local_preferences


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def ms(stamp):
    return int(stamp.timestamp() * 1000)


def track(ident):
    return {"id": ident, "name": "Synthetic song " + str(ident),
            "artists": [{"id": 700, "name": "Synthetic artist"}],
            "album": {"name": "Synthetic album"}, "duration": 120000}


class LocalCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / DATABASE_NAME
        self.connection = sqlite3.connect(self.path)
        self.connection.executescript('''
            CREATE TABLE web_playlist(pid INTEGER, playlist TEXT);
            CREATE TABLE web_playlist_track(pid INTEGER, tid INTEGER, "order" INTEGER);
            CREATE TABLE web_track(tid INTEGER, track TEXT);
            CREATE TABLE historyTracks(playtime, id TEXT, jsonStr TEXT);
        ''')

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def playlist(self, pid, uid, nickname, declared=1, members=(1,), metadata=(1,)):
        obj = {"id": pid, "specialType": 5, "userId": uid, "trackCount": declared,
               "creator": {"userId": uid, "nickname": nickname},
               "trackUpdateTime": ms(NOW - timedelta(days=90))}
        self.connection.execute("INSERT INTO web_playlist VALUES (?,?)", (pid, json.dumps(obj)))
        self.connection.executemany("INSERT INTO web_playlist_track VALUES (?,?,?)",
                                   [(pid, tid, order) for order, tid in enumerate(members)])
        for tid in metadata:
            if not self.connection.execute("SELECT 1 FROM web_track WHERE tid=?", (tid,)).fetchone():
                self.connection.execute("INSERT INTO web_track VALUES (?,?)", (tid, json.dumps(track(tid))))
        self.connection.commit()

    def history(self, ident, when, data=None):
        self.connection.execute("INSERT INTO historyTracks VALUES (?,?,?)",
                                (when, str(ident), json.dumps(data if data is not None else track(ident))))
        self.connection.commit()

    def test_two_named_owners_are_ambiguous(self):
        self.playlist(100, 10, "Synthetic A")
        self.playlist(200, 20, "Synthetic B", members=(2,), metadata=(2,))
        result = read_local_preferences(self.root, now=NOW)
        self.assertEqual(result["discovery"]["context_status"], "ambiguous")
        self.assertIsNone(result["discovery"]["account_id"])
        self.assertEqual(result["sources"]["liked"]["status"], "unavailable")
        self.assertEqual(result["sources"]["liked"]["tracks"], [])

    def test_host_verified_nickname_selects_owner_without_authentication_claim(self):
        self.playlist(100, 10, "Synthetic A")
        self.playlist(200, 20, "Synthetic B", members=(2,), metadata=(2,))
        result = read_local_preferences(self.root, verified_nickname="Synthetic B", now=NOW)
        self.assertEqual(result["discovery"]["account_id"], "20")
        self.assertEqual(result["discovery"]["playlist_id"], "200")
        self.assertEqual(result["discovery"]["context_status"], "host_verified_nickname")
        self.assertFalse(result["discovery"]["authenticated"])
        self.assertFalse(result["discovery"]["credentials_read"])
        liked = result["sources"]["liked"]
        self.assertEqual([t["track_id"] for t in liked["tracks"]], ["2"])
        self.assertEqual(liked["tracks"][0]["artist_refs"][0]["artist_id"], "700")

    def test_blank_owner_is_not_a_second_identified_account(self):
        self.playlist(100, 10, None, declared=0, members=(), metadata=())
        self.playlist(200, 20, "Synthetic B", members=(2,), metadata=(2,))
        result = read_local_preferences(self.root, now=NOW)
        self.assertEqual(result["discovery"]["context_status"], "cached_owner_resolved")
        self.assertEqual(result["discovery"]["account_id"], "20")

    def test_verified_nickname_mismatch_does_not_guess_single_cached_account(self):
        self.playlist(100, 10, "Synthetic A")
        result = read_local_preferences(self.root, verified_nickname="Other account", now=NOW)
        self.assertEqual(result["discovery"]["context_status"], "verified_nickname_not_in_cache")
        self.assertEqual(result["sources"]["liked"]["tracks"], [])

    def test_cache_members_mismatch_and_missing_metadata_remain_partial(self):
        self.playlist(100, 10, "Synthetic A", declared=4, members=(1, 2, 3), metadata=(1, 2))
        liked = read_local_preferences(self.root, now=NOW)["sources"]["liked"]
        self.assertEqual(liked["status"], "partial")
        self.assertEqual(liked["declared_total"], 4)
        self.assertEqual(liked["cache_members_count"], 3)
        self.assertEqual(liked["loaded_tracks"], 2)
        self.assertEqual(liked["missing_track_ids"], ["3"])
        self.assertFalse(liked["metadata_complete_for_cached_members"])
        self.assertFalse(liked["cache_members_match_declared"])
        self.assertFalse(liked["complete"])
        self.assertIsNone(liked["stale"])
        self.assertIn("content-change time", liked["snapshot_timestamp_basis"])

    def test_recent_filters_dates_and_preserves_latest_unique_song(self):
        self.history(1, ms(NOW - timedelta(days=2)))
        self.history(1, ms(NOW - timedelta(days=1)))
        self.history(2, ms(NOW - timedelta(days=31)))
        self.history(3, ms(NOW + timedelta(seconds=1)))
        self.history(4, "not a timestamp")
        self.history(5, ms(NOW - timedelta(hours=1)), data={"id": 5})
        recent = read_local_preferences(self.root, now=NOW)["sources"]["recent"]
        self.assertEqual([t["track_id"] for t in recent["tracks"]], ["1"])
        self.assertEqual(recent["tracks"][0]["observed_at"], "2026-11-12T12:00:00Z")
        self.assertEqual(recent["window"]["start"], "2026-10-14T12:00:00Z")
        self.assertEqual(recent["dropped_old_rows"], 1)
        self.assertEqual(recent["dropped_future_rows"], 1)
        self.assertEqual(recent["dropped_invalid_time_rows"], 1)
        self.assertEqual(recent["dropped_invalid_metadata_rows"], 1)
        self.assertEqual(recent["valid_recent_rows"], 2)
        self.assertEqual(recent["scope"], "device_local_recent_unique_tracks")
        self.assertFalse(recent["play_counts_available"])
        self.assertFalse(recent["frequency_available"])
        self.assertFalse(recent["complete"])

    def test_new_metadata_fields_normalize_without_language_inference(self):
        result = normalize_local_track({"id": 2, "name": "Synthetic", "ar": [{"id": 50, "name": "Artist"}],
                                        "al": {"name": "Album"}, "dt": 1000})
        self.assertEqual(result["artist_refs"], [{"artist_id": "50", "name": "Artist"}])
        self.assertEqual(result["duration_ms"], 1000)
        self.assertIsNone(result["language"])
        self.assertEqual(result["playback_status"], "unchecked")

    def test_read_only_and_missing_cache_does_not_create_database(self):
        self.playlist(100, 10, "Synthetic A")
        before = self.path.read_bytes()
        read_local_preferences(self.root, now=NOW)
        self.assertEqual(self.path.read_bytes(), before)
        missing = self.root / "absent"
        result = read_local_preferences(missing, now=NOW)
        self.assertEqual(result["discovery"]["context_status"], "not_found")
        self.assertFalse((missing / DATABASE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
