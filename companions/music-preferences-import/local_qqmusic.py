"""Read QQ Music's macOS metadata cache without credentials or guessed joins.

Known native schemas vary. A folder-to-song relation must be explicit in the
database; matching folder and song-group counts is deliberately not supported.
Windows and native recent-history schemas are not claimed as verified.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys

from qqmusic_api import normalize_qq_track, QQMusicError


DEFAULT_STORAGE = Path.home() / "Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac"
MAX_LOCAL_TRACKS = 1000
FAVORITE_NAMES = {"我喜欢", "我喜欢的音乐", "喜欢的歌曲", "liked songs", "my favorites", "favourites", "favorites"}


def _unavailable(scope, reason):
    return {"status": "unavailable", "scope": scope, "reason": reason, "tracks": [], "complete": False}


def _columns(con, table):
    return {r[1].lower(): r[1] for r in con.execute('PRAGMA table_info("' + table + '")')}


def _get(row, *names):
    lowered = {str(k).lower(): v for k, v in dict(row).items()}
    for name in names:
        if lowered.get(name.lower()) is not None:
            return lowered[name.lower()]
    return None


def _positive(value):
    text = str(value) if value is not None and not isinstance(value, bool) else ""
    return str(int(text)) if re.fullmatch(r"[0-9]+", text) and int(text) > 0 else None


def _normalise_local(row):
    singer = _get(row, "singer")
    if isinstance(singer, str):
        try:
            parsed = json.loads(singer)
            singer = parsed if isinstance(parsed, list) else [{"name": singer}]
        except ValueError:
            singer = [{"name": singer}] if singer.strip() else []
    elif not isinstance(singer, list):
        singer = []
    album = _get(row, "album")
    raw = {"id": _get(row, "id"), "mid": _get(row, "mid", "songmid", "song_mid", "K_SONG_RESERVE1"),
           "name": _get(row, "name"), "singer": singer,
           "album": album if isinstance(album, dict) else {"name": album}}
    result = normalize_qq_track(raw)
    # Native duration units differ by version; never label an unknown unit ms.
    result["provider_refs"]["local_duration_value"] = _get(row, "duration", "K_SONG_RESERVEINT21")
    return result


def _membership_column(con, folder_table, member_table, folder):
    columns = _columns(con, member_table)
    folder_columns = _columns(con, folder_table)
    foreign_keys = list(con.execute('PRAGMA foreign_key_list("' + member_table + '")'))
    explicit = []
    claimed_columns = {str(f[3]).lower() for f in foreign_keys}
    for foreign_key in foreign_keys:
        if str(foreign_key[2]).lower() != folder_table.lower():
            continue
        source, target = str(foreign_key[3]), str(foreign_key[4])
        if (source.lower() not in columns or target.lower() not in folder_columns
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target)):
            return None, None
        value = _get(folder, target)
        if value is None:
            return None, None
        count = con.execute('SELECT count(*) FROM "' + folder_table + '" WHERE "' + target + '"=?', (value,)).fetchone()[0]
        if count != 1:
            return None, None
        explicit.append((columns[source.lower()], value))
    if explicit:
        # Declared foreign keys outrank shared-name conventions; multiple
        # different possible parent relations are ambiguous, never guessed.
        return explicit[0] if len(set(explicit)) == 1 else (None, None)
    for name in ("folderid", "folder_id"):
        if name in columns and name not in claimed_columns:
            value = _get(folder, "folderid")
            if value is not None and con.execute('SELECT count(*) FROM "' + folder_table + '" WHERE folderid=?', (value,)).fetchone()[0] == 1:
                return columns[name], value
    if "seq" in columns and "seq" not in claimed_columns and "seq" in folder_columns:
        value = _get(folder, "seq")
        if value is not None and con.execute('SELECT count(*) FROM "' + folder_table + '" WHERE seq=?', (value,)).fetchone()[0] == 1:
            return columns["seq"], value
    return None, None


def _read_liked(con, is_user_db, discovery, checked_at):
    folder_table, member_table, song_table = (("USERFOLDERINFO", "USERFOLDERSONGINFO", "SONGINFO")
        if is_user_db else ("NEWFOLDERS", "NEWFOLDERSONGS", "SONGS"))
    folders = []
    for row in con.execute('SELECT * FROM "' + folder_table + '" LIMIT 1001'):
        name = _get(row, "folderName")
        # Both a known semantic label and the system-folder marker are required.
        if isinstance(name, str) and name.strip().lower() in FAVORITE_NAMES and str(_get(row, "foldertype")) == "1":
            folders.append(row)
    if len(folders) != 1:
        return _unavailable("device_local_qq_liked_snapshot", "liked_folder_ambiguous" if folders else "liked_folder_not_identified")
    folder = folders[0]
    discovery["local_folder_id"] = str(_get(folder, "folderid"))
    # A local folder ID is not necessarily the public disstid (nor dirid 201).
    discovery["playlist_id"] = _positive(_get(folder, "disstid", "dissid"))
    discovery["playlist_name"] = _get(folder, "folderName")
    column, value = _membership_column(con, folder_table, member_table, folder)
    if column is None:
        return _unavailable("device_local_qq_liked_snapshot", "favorite_membership_mapping_unverified")
    member_rows = list(con.execute('SELECT id FROM "' + member_table + '" WHERE "' + column + '"=? LIMIT ?',
                                   (value, MAX_LOCAL_TRACKS + 1)))
    identities = list(dict.fromkeys(str(row[0]) for row in member_rows))
    tracks, missing = [], []
    for ident in identities[:MAX_LOCAL_TRACKS]:
        row = con.execute('SELECT * FROM "' + song_table + '" WHERE id=?', (ident,)).fetchone()
        try:
            if row is None:
                raise QQMusicError("missing_cached_metadata")
            track = _normalise_local(row)
            track["raw_fields"] = {"membership_status": "cached_unverified", "metadata_source": "qq_client_cache"}
            tracks.append(track)
        except QQMusicError:
            missing.append(ident)
    declared = _get(folder, "folderCount")
    return {"status": "partial", "scope": "device_local_qq_liked_snapshot", "tracks": tracks,
            "checked_at": checked_at, "complete": False, "declared_total": declared,
            "cache_members_count": len(member_rows), "cache_unique_members_count": len(identities),
            "loaded_tracks": len(tracks), "missing_track_ids": missing,
            "truncated": len(member_rows) > MAX_LOCAL_TRACKS,
            "metadata_complete_for_cached_members": not missing and len(member_rows) <= MAX_LOCAL_TRACKS,
            "reason": "cached_membership_not_current_online_confirmation",
            "membership_mapping_basis": "explicit_database_relation"}


def read_local_qq_preferences(storage_root=None, verified_account=None, now=None):
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    checked_at = current.astimezone(timezone.utc).isoformat()
    root = Path(storage_root).expanduser() if storage_root is not None else DEFAULT_STORAGE
    discovery = {"platform": "qq", "adapter": "macos_qq_metadata_cache_read_only",
                 "storage_path": str(root), "database_found": False, "database_path": None,
                 "context_status": "not_found", "account_id": None, "playlist_id": None,
                 "authenticated": False, "authentication_checked": False, "credentials_read": False,
                 "checked_at": checked_at, "native_client_tested": False}
    if storage_root is None:
        discovery["app_installed"] = any(p.is_dir() for p in (
            Path("/Applications/QQMusic.app"), Path("/Applications/QQ音乐.app"),
            Path.home() / "Applications/QQMusic.app"))
    sources = {"liked": _unavailable("device_local_qq_liked_snapshot", "cache_not_found"),
               "recent": _unavailable("device_local_qq_recent_history", "qq_recent_schema_not_verified"),
               "weekly": _unavailable("qq_weekly_ranking", "qq_weekly_adapter_not_implemented")}
    result = {"discovery": discovery, "sources": sources}
    if storage_root is None and sys.platform != "darwin":
        discovery["context_status"] = "unsupported_os"
        sources["liked"]["reason"] = "qq_native_cache_adapter_macos_only"
        return result
    if root.is_file():
        accounts = [(root.parent.name if root.parent.name.isdecimal() else None, root)]
    else:
        user_root = root / "iUser"
        accounts = ([(entry.name, entry / "user.db") for entry in sorted(user_root.iterdir())
                     if entry.is_dir() and entry.name.isdecimal() and (entry / "user.db").is_file()]
                    if user_root.is_dir() else [])
        if not accounts and (root / "qqmusic.sqlite").is_file():
            accounts = [(None, root / "qqmusic.sqlite")]
    discovery["account_candidates"] = [{"account_id": account, "database_path": str(path)} for account, path in accounts]
    if verified_account is not None:
        accounts = [(account, path) for account, path in accounts if account == str(verified_account)]
        if not accounts:
            discovery["context_status"] = "verified_account_not_in_cache"
            sources["liked"]["reason"] = "verified_account_not_in_cache"
            return result
    if len(accounts) > 1:
        discovery["context_status"] = "ambiguous"
        sources["liked"]["reason"] = "multiple_cached_accounts_host_verification_required"
        return result
    if not accounts:
        return result
    account, database = accounts[0]
    discovery.update(database_found=True, database_path=str(database), account_id=account,
                     context_status=("host_verified_account" if verified_account is not None else
                                     "cached_owner_resolved" if account else "device_cache_only"))
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            sources["liked"] = _read_liked(con, database.name == "user.db", discovery, checked_at)
    except (sqlite3.Error, OSError):
        discovery["context_status"] = "cache_schema_unavailable_or_unreadable"
        sources["liked"] = _unavailable("device_local_qq_liked_snapshot", "cache_schema_unavailable_or_unreadable")
    return result
