"""Read-only macOS NetEase preference cache adapter; never reads credentials.

Only four music metadata tables are queried. A cached owner or a nickname
verified by the host is identity context, not proof of an authenticated API
session. Local history is device history, not a complete personal play counter.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3


DEFAULT_STORAGE = Path.home() / "Library/Containers/com.netease.163music/Data/Documents/storage"
DATABASE_NAME = "sqlite_storage.sqlite3"
RECENT_DAYS = 30
MAX_HISTORY_ROWS = 10000


def _ident(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r"[0-9]+", text) and int(text) > 0 else None


def _date(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value.strip()):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        if number >= 100_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _json_object(raw):
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def normalize_local_track(raw, expected_id=None):
    """Convert legacy or current metadata without inferring language/playability."""
    if not isinstance(raw, dict):
        return None
    ident = _ident(raw.get("id"))
    if ident is None or (expected_id is not None and ident != _ident(expected_id)):
        return None
    title = raw.get("name")
    if not isinstance(title, str) or not title.strip():
        return None
    artists = raw.get("artists", raw.get("ar", []))
    if not isinstance(artists, list):
        artists = []
    refs = [{"artist_id": _ident(a.get("id")),
             "name": a.get("name") if isinstance(a.get("name"), str) else None}
            for a in artists if isinstance(a, dict)]
    album = raw.get("album", raw.get("al", {}))
    return {"platform": "netease", "track_id": ident, "title": title,
            "artists": list(dict.fromkeys(a["name"] for a in refs if a["name"])),
            "artist_refs": refs, "album": album.get("name") if isinstance(album, dict) else None,
            "duration_ms": raw.get("duration", raw.get("dt")),
            "play_url": "https://music.163.com/song?id=" + ident,
            "language": None, "playback_status": "unchecked"}


def _unavailable(scope, reason):
    return {"status": "unavailable", "scope": scope, "reason": reason,
            "tracks": [], "complete": False}


def _owner_candidates(connection):
    candidates, invalid = [], 0
    for pid, raw in connection.execute("SELECT pid, playlist FROM web_playlist"):
        playlist = _json_object(raw)
        if not playlist:
            invalid += 1
            continue
        if str(playlist.get("specialType")) != "5":
            continue
        creator = playlist.get("creator")
        creator = creator if isinstance(creator, dict) else {}
        name = creator.get("nickname")
        name = name.strip() if isinstance(name, str) else None
        owner = _ident(creator.get("userId")) or _ident(playlist.get("userId"))
        other_owner = _ident(playlist.get("userId"))
        if owner and other_owner and owner != other_owner:
            invalid += 1
            continue
        if name and owner and _ident(pid):
            candidates.append({"account_id": owner, "nickname": name,
                               "playlist_id": _ident(pid), "playlist": playlist})
    return candidates, invalid


def _resolve_owner(candidates, verified_nickname):
    eligible = candidates
    if verified_nickname is not None:
        if not isinstance(verified_nickname, str) or not verified_nickname.strip():
            raise ValueError("verified_nickname must be a nonempty host-verified string")
        eligible = [item for item in candidates if item["nickname"] == verified_nickname.strip()]
        if not eligible:
            return None, "verified_nickname_not_in_cache"
    owners = {item["account_id"] for item in eligible}
    if len(owners) > 1:
        return None, "ambiguous"
    if not owners:
        return None, "no_identified_cached_owner"
    playlists = {item["playlist_id"] for item in eligible}
    if len(playlists) > 1:
        return None, "playlist_ambiguous"
    return eligible[0], "host_verified_nickname" if verified_nickname is not None else "cached_owner_resolved"


def _read_liked(connection, owner, now):
    playlist = owner["playlist"]
    rows = list(connection.execute(
        'SELECT m.tid, t.track FROM web_playlist_track AS m '
        'LEFT JOIN web_track AS t ON m.tid=t.tid '
        'WHERE m.pid=? ORDER BY m."order", m.rowid', (owner["playlist_id"],)))
    unique_ids = list(dict.fromkeys(ident for ident, _ in
                                   ((_ident(tid), raw) for tid, raw in rows) if ident))
    tracks, missing = {}, []
    for tid, raw in rows:
        ident = _ident(tid)
        if ident is None or ident in tracks:
            continue
        normal = normalize_local_track(_json_object(raw), expected_id=ident)
        if normal is not None:
            tracks[ident] = normal
    missing = [ident for ident in unique_ids if ident not in tracks]
    timestamp_field = "trackUpdateTime" if _date(playlist.get("trackUpdateTime")) else "updateTime"
    stamp = _date(playlist.get(timestamp_field))
    age = (now - stamp).total_seconds() / 86400 if stamp and stamp <= now else None
    declared = playlist.get("trackCount")
    declared = declared if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0 else None
    return {"status": "partial", "scope": "device_local_liked_playlist_snapshot",
            "account_id": owner["account_id"], "playlist_id": owner["playlist_id"],
            "checked_at": _iso(now), "snapshot_at": _iso(stamp),
            "snapshot_timestamp_basis": "playlist." + timestamp_field + "; content-change time, not fetch time",
            "snapshot_age_days": age, "freshness": "not_verified_online", "stale": None,
            "declared_total": declared, "cache_members_count": len(rows),
            "cache_unique_members_count": len(unique_ids),
            "cache_duplicate_members_count": len(rows) - len(unique_ids),
            "cache_members_match_declared": declared == len(unique_ids) if declared is not None else None,
            "metadata_complete_for_cached_members": not missing,
            "loaded_tracks": len(tracks), "missing_track_ids": missing,
            "tracks": [tracks[ident] for ident in unique_ids if ident in tracks],
            "complete": False, "reason": "local_snapshot_is_not_current_online_membership"}


def _read_recent(connection, now):
    cutoff = now - timedelta(days=RECENT_DAYS)
    total = connection.execute("SELECT count(*) FROM historyTracks").fetchone()[0]
    rows = connection.execute(
        "SELECT playtime,id,jsonStr FROM historyTracks ORDER BY playtime DESC LIMIT ?", (MAX_HISTORY_ROWS,))
    latest = {}
    counts = {"history_rows_scanned": 0, "dropped_future_rows": 0,
              "dropped_invalid_time_rows": 0, "dropped_old_rows": 0,
              "dropped_invalid_metadata_rows": 0, "valid_recent_rows": 0}
    for playtime, row_id, raw in rows:
        counts["history_rows_scanned"] += 1
        stamp = _date(playtime)
        if stamp is None:
            counts["dropped_invalid_time_rows"] += 1
            continue
        if stamp > now:
            counts["dropped_future_rows"] += 1
            continue
        if stamp < cutoff:
            counts["dropped_old_rows"] += 1
            continue
        normal = normalize_local_track(_json_object(raw), expected_id=_ident(row_id))
        if normal is None:
            counts["dropped_invalid_metadata_rows"] += 1
            continue
        counts["valid_recent_rows"] += 1
        ident = normal["track_id"]
        if ident not in latest or stamp > latest[ident][0]:
            normal["observed_at"] = _iso(stamp)
            normal["raw_fields"] = {"history_table": "historyTracks", "source_playtime": playtime,
                                    "frequency_measured": False}
            latest[ident] = stamp, normal
    return {"status": "partial", "scope": "device_local_recent_unique_tracks",
            "account_scope": "device_local_not_account_bound", "checked_at": _iso(now),
            "window": {"days": RECENT_DAYS, "start": _iso(cutoff), "end": _iso(now),
                       "time_basis": "historyTracks.playtime"},
            "tracks": [item[1] for item in sorted(latest.values(), key=lambda item: item[0], reverse=True)],
            "loaded_tracks": len(latest), "complete": False,
            "history_rows_available": total, "scan_truncated": total > MAX_HISTORY_ROWS,
            "play_counts_available": False, "frequency_available": False,
            "reason": "deduplicated_device_history_does_not_measure_play_frequency", **counts}


def read_local_preferences(storage_root=None, verified_nickname=None, now=None):
    """Discover the macOS cache, resolve an owner, and return local evidence.

    ``storage_root`` is the storage directory (or explicit SQLite file) and is
    injectable for synthetic tests. ``verified_nickname`` must come from the
    host's observation of the current client, never from a guessed first row.
    ``now`` accepts a datetime or timestamp; naive datetime values mean UTC.
    """
    current = _date(now) if now is not None else datetime.now(timezone.utc)
    if current is None:
        raise ValueError("now must be a valid datetime or timestamp")
    root = Path(storage_root).expanduser() if storage_root is not None else DEFAULT_STORAGE
    database = root if root.suffix in {".sqlite3", ".sqlite", ".db"} else root / DATABASE_NAME
    discovery = {"platform": "netease", "adapter": "macos_client_cache_read_only",
                 "database_path": str(database), "database_found": database.is_file(),
                 "context_status": "not_found", "account_id": None, "nickname": None,
                 "playlist_id": None, "authenticated": False,
                 "authentication_checked": False, "credentials_read": False,
                 "checked_at": _iso(current)}
    if storage_root is None:
        discovery["app_installed"] = any(p.is_dir() for p in
            (Path("/Applications/NeteaseMusic.app"), Path.home() / "Applications/NeteaseMusic.app"))
    sources = {"liked": _unavailable("device_local_liked_playlist_snapshot", "cache_not_found"),
               "recent": _unavailable("device_local_recent_unique_tracks", "cache_not_found")}
    result = {"discovery": discovery, "sources": sources}
    if not database.is_file():
        return result
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            # One coherent read snapshot; no write PRAGMA or original-file copy.
            connection.execute("BEGIN")
            discovery["cache_file_modified_at"] = _iso(_date(database.stat().st_mtime))
            try:
                candidates, invalid = _owner_candidates(connection)
                discovery["invalid_playlist_rows"] = invalid
                discovery["owner_candidates"] = [{k: item[k] for k in
                    ("account_id", "nickname", "playlist_id")} for item in candidates]
                owner, context = _resolve_owner(candidates, verified_nickname)
                discovery["context_status"] = context
                if owner is not None:
                    discovery.update({k: owner[k] for k in ("account_id", "nickname", "playlist_id")})
                    discovery["context_basis"] = (
                        "host_observed_client_nickname_matches_cached_playlist_owner"
                        if verified_nickname is not None else "single_named_cached_heart_playlist_owner")
                    try:
                        sources["liked"] = _read_liked(connection, owner, current)
                    except sqlite3.Error:
                        sources["liked"] = _unavailable("device_local_liked_playlist_snapshot", "membership_schema_unavailable")
                else:
                    sources["liked"] = _unavailable("device_local_liked_playlist_snapshot", context)
            except sqlite3.Error:
                discovery["context_status"] = "playlist_schema_unavailable"
                sources["liked"] = _unavailable("device_local_liked_playlist_snapshot", "playlist_schema_unavailable")
            try:
                sources["recent"] = _read_recent(connection, current)
            except sqlite3.Error:
                sources["recent"] = _unavailable("device_local_recent_unique_tracks", "history_schema_unavailable")
    except (sqlite3.Error, OSError):
        discovery["context_status"] = "cache_read_failed"
        sources["liked"] = _unavailable("device_local_liked_playlist_snapshot", "cache_read_failed")
        sources["recent"] = _unavailable("device_local_recent_unique_tracks", "cache_read_failed")
    return result
