from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from local_qqmusic import read_local_qq_preferences


NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def create_cache(root, account="10", mapping="folderid", user=True):
    p = root / "iUser" / account / "user.db" if user else root / "qqmusic.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    folder, members, songs = (("USERFOLDERINFO", "USERFOLDERSONGINFO", "SONGINFO") if user else
                               ("NEWFOLDERS", "NEWFOLDERSONGS", "SONGS"))
    extra = ", seq INTEGER" if mapping == "folderseq" else ""
    con.execute(f"CREATE TABLE {folder}(folderid INTEGER,folderName TEXT,folderCount INTEGER,foldertype INTEGER{extra})")
    con.execute(f"INSERT INTO {folder} VALUES (8,'我喜欢',1,1{',23' if extra else ''})")
    if mapping == "foreignkey":
        con.execute(f"CREATE TABLE {members}(seq INTEGER,id INTEGER,FOREIGN KEY(seq) REFERENCES {folder}(folderid))")
    else:
        column = "folderid" if mapping == "folderid" else "seq"
        con.execute(f"CREATE TABLE {members}({column} INTEGER,id INTEGER)")
    con.execute(f"INSERT INTO {members} VALUES (?,101)", (23 if mapping == "folderseq" else 8,))
    con.execute(f"CREATE TABLE {songs}(id INTEGER,name TEXT,singer TEXT,album TEXT,duration INTEGER,K_SONG_RESERVE1 TEXT)")
    con.execute(f"INSERT INTO {songs} VALUES (101,'Synthetic song','Synthetic artist','Synthetic album',123,'S00000000000101')")
    con.commit()
    con.close()
    return p


class QQLocalTests(unittest.TestCase):
    def test_explicit_folderid_relation_reads_only_metadata_as_cached_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            database = create_cache(root)
            before = database.read_bytes()
            report = read_local_qq_preferences(root, now=NOW)
            liked = report["sources"]["liked"]
            self.assertEqual(report["discovery"]["account_id"], "10")
            self.assertEqual(liked["status"], "partial")
            self.assertEqual(liked["loaded_tracks"], 1)
            self.assertFalse(liked["complete"])
            track = liked["tracks"][0]
            self.assertEqual(track["platform"], "qq")
            self.assertEqual(track["raw_fields"]["membership_status"], "cached_unverified")
            self.assertIsNone(track["artist_refs"][0]["artist_id"])
            self.assertIsNone(track["duration_ms"])
            self.assertEqual(before, database.read_bytes())
            self.assertFalse(report["discovery"]["credentials_read"])
            self.assertIsNone(report["discovery"]["playlist_id"])

    def test_seq_only_schema_never_guesses_by_matching_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            create_cache(root, mapping="seq")
            report = read_local_qq_preferences(root)
            self.assertEqual(report["sources"]["liked"]["status"], "unavailable")
            self.assertEqual(report["sources"]["liked"]["reason"], "favorite_membership_mapping_unverified")
            self.assertEqual(report["discovery"]["local_folder_id"], "8")

    def test_explicit_folder_seq_and_foreign_key_relations_work(self):
        for mapping in ("folderseq", "foreignkey"):
            with self.subTest(mapping=mapping), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                create_cache(root, mapping=mapping)
                report = read_local_qq_preferences(root)
                self.assertEqual(report["sources"]["liked"]["loaded_tracks"], 1)

    def test_multiple_accounts_require_host_verification(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            create_cache(root, "10")
            create_cache(root, "20")
            report = read_local_qq_preferences(root)
            self.assertEqual(report["discovery"]["context_status"], "ambiguous")
            self.assertEqual(report["sources"]["liked"]["tracks"], [])
            selected = read_local_qq_preferences(root, verified_account="20")
            self.assertEqual(selected["discovery"]["account_id"], "20")
            self.assertEqual(selected["discovery"]["context_status"], "host_verified_account")

    def test_declared_foreign_key_outranks_conflicting_shared_seq(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            database = create_cache(root, mapping="foreignkey", user=False)
            with sqlite3.connect(database) as con:
                con.execute("ALTER TABLE NEWFOLDERS ADD COLUMN seq INTEGER")
                con.execute("UPDATE NEWFOLDERS SET seq=23 WHERE folderid=8")
                con.execute("INSERT INTO NEWFOLDERS VALUES (23,'Another folder',1,2,8)")
                con.execute("INSERT INTO NEWFOLDERSONGS VALUES (23,102)")
                con.execute("INSERT INTO SONGS VALUES (102,'Another song','Another artist','Album',123,'S00000000000102')")
            report = read_local_qq_preferences(root)
            tracks = report["sources"]["liked"]["tracks"]
            self.assertEqual([t["provider_refs"]["song_id"] for t in tracks], ["101"])

    def test_download_library_without_a_liked_folder_is_not_liked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            database = create_cache(root, user=False)
            with sqlite3.connect(database) as con:
                con.execute("DELETE FROM NEWFOLDERS")
            report = read_local_qq_preferences(root)
            self.assertEqual(report["sources"]["liked"]["reason"], "liked_folder_not_identified")
            self.assertFalse(report["discovery"]["authenticated"])

    def test_metadata_missing_retains_incomplete_membership(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            database = create_cache(root)
            with sqlite3.connect(database) as con:
                con.execute("DELETE FROM SONGINFO")
            result = read_local_qq_preferences(root)["sources"]["liked"]
            self.assertEqual(result["missing_track_ids"], ["101"])
            self.assertEqual(result["loaded_tracks"], 0)
            self.assertFalse(result["metadata_complete_for_cached_members"])

    def test_unknown_recent_schema_does_not_report_verified_empty_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            create_cache(root)
            report = read_local_qq_preferences(root)
            self.assertEqual(report["sources"]["recent"]["status"], "unavailable")
            self.assertFalse(report["sources"]["recent"]["complete"])

    def test_unreadable_or_encrypted_cache_is_not_decrypted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qqmusic.sqlite").write_bytes(b"Synthetic encrypted/unreadable database marker")
            report = read_local_qq_preferences(root)
            self.assertEqual(report["discovery"]["context_status"], "cache_schema_unavailable_or_unreadable")
            self.assertEqual(report["sources"]["liked"]["tracks"], [])

    def test_wrong_verified_account_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            create_cache(root)
            report = read_local_qq_preferences(root, verified_account="99")
            self.assertEqual(report["discovery"]["context_status"], "verified_account_not_in_cache")


if __name__ == "__main__":
    unittest.main()
