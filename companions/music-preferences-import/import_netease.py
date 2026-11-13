#!/usr/bin/env python3
"""Experimental keyless public-playlist metadata import; no login, lyrics or audio."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

BASE = "https://music.163.com"
MAX_IDS = 1000
DETAIL_BATCH_SIZE = 100


def playlist_id(value):
    if re.fullmatch(r"[1-9][0-9]*", value):
        return value
    url = urlsplit(value)
    if url.scheme != "https" or url.netloc != "music.163.com":
        raise ValueError("Use a music.163.com HTTPS playlist link or a numeric playlist ID.")
    route = urlsplit(url.fragment) if url.fragment else url
    if route.path != "/playlist":
        raise ValueError("The link must identify one playlist.")
    found = parse_qs(route.query).get("id", [])
    if len(found) != 1 or not re.fullmatch(r"[1-9][0-9]*", found[0]):
        raise ValueError("Missing or invalid playlist ID.")
    return found[0]


def get_json(path, parameters):
    request = Request(BASE + path + "?" + urlencode(parameters),
                      headers={"User-Agent": "vocab-music-research/0.1", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.url).netloc != "music.163.com":
                raise ValueError("Unexpected destination; import stopped.")
            # Bound the response size; never save a raw platform response.
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Response exceeds the experimental size limit.")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Platform response must be a JSON object.")
            return value
    except HTTPError as exc:
        raise ValueError(f"Platform HTTP {exc.code}; no retry or login attempted.") from None
    except (URLError, TimeoutError):
        raise ValueError("Platform request failed; no retry or login attempted.") from None


def normalize_track(track):
    if not isinstance(track, dict) or isinstance(track.get("id"), bool) or not re.fullmatch(r"[1-9][0-9]*", str(track.get("id", ""))):
        raise ValueError("Invalid song metadata identity.")
    artists = track.get("ar", track.get("artists", []))
    album = track.get("al", track.get("album", {}))
    if not isinstance(artists, list) or any(not isinstance(a, dict) for a in artists) or not isinstance(album, dict):
        raise ValueError("Invalid song metadata structure.")
    return {"track_id": str(track["id"]), "title": track.get("name"),
            "artists": [a["name"] for a in artists if a.get("name")],
            "artist_refs": [{"artist_id": str(a["id"]) if a.get("id") else None,
                             "name": a.get("name")} for a in artists],
            "album": album.get("name"), "duration_ms": track.get("dt", track.get("duration")),
            "language": None, "play_url": BASE + "/song?id=" + str(track["id"]),
            "playback_status": "unchecked"}


def import_playlist(value, fetch=get_json):
    ident = playlist_id(value)
    payload = fetch("/api/v6/playlist/detail", {"id": ident, "n": MAX_IDS, "s": 0})
    if not isinstance(payload, dict):
        raise ValueError("Playlist response must be a JSON object.")
    if payload.get("code") != 200 or not isinstance(payload.get("playlist"), dict):
        raise ValueError(f"Playlist unavailable (code {payload.get('code')}); no login attempted.")
    playlist = payload["playlist"]
    if not isinstance(playlist.get("trackIds"), list) or not isinstance(playlist.get("tracks"), list):
        raise ValueError("Playlist membership or metadata schema is unavailable.")
    if any(not isinstance(item, dict) or not re.fullmatch(r"[1-9][0-9]*", str(item.get("id", "")))
           for item in playlist["trackIds"]):
        raise ValueError("Invalid playlist member identity.")
    raw_ids = [str(item["id"]) for item in playlist.get("trackIds", [])]
    expected = list(dict.fromkeys(raw_ids))
    wanted = expected[:MAX_IDS]
    metadata = {normalize_track(t)["track_id"]: t for t in playlist["tracks"]}
    missing = [i for i in wanted if i not in metadata]
    request_count = 1
    detail_error = None
    duplicate_detail_ids = 0
    unexpected_detail_ids = 0
    seen = set()
    for start in range(0, len(missing), DETAIL_BATCH_SIZE):
        batch = missing[start:start + DETAIL_BATCH_SIZE]
        request_count += 1
        try:
            details = fetch("/api/song/detail", {"ids": json.dumps([int(i) for i in batch])})
            if not isinstance(details, dict):
                raise ValueError("Track details response must be a JSON object.")
            if details.get("code") != 200 or not isinstance(details.get("songs"), list):
                raise ValueError(f"Track details unavailable (code {details.get('code')}).")
            for track in details["songs"]:
                key = normalize_track(track)["track_id"]
                if key in seen:
                    duplicate_detail_ids += 1
                seen.add(key)
                if key not in batch:
                    unexpected_detail_ids += 1
                    continue
                metadata[key] = track
        except (ValueError, KeyError, TypeError) as exc:
            detail_error = str(exc)
            break
    tracks = [normalize_track(metadata[i]) for i in wanted if i in metadata]
    missing_final = [i for i in expected if i not in metadata or i not in wanted]
    total = playlist.get("trackCount")
    complete = (isinstance(total, int) and len(raw_ids) == len(expected) == total
                and len(tracks) == total and not missing_final and not duplicate_detail_ids
                and not unexpected_detail_ids and detail_error is None)
    return {"component": "music-preferences-import-experimental-0.3", "platform": "netease",
            "source_kind": "undocumented_platform_http", "source_url": BASE + "/playlist?id=" + ident,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "playlist_title": playlist.get("name"), "declared_total": total,
            "owner_id": str(playlist.get("userId") or (playlist.get("creator") or {}).get("userId") or ""),
            "owner_nickname": (playlist.get("creator") or {}).get("nickname"),
            "returned_track_ids": len(raw_ids), "unique_track_ids": len(expected),
            "loaded_tracks": len(tracks), "missing_ids": missing_final,
            "duplicate_detail_ids": duplicate_detail_ids, "unexpected_detail_ids": unexpected_detail_ids,
            "complete": complete, "detail_error": detail_error, "requests": request_count,
            "key_required": False, "authentication_used": False,
            "private_favorites_verified": False, "lyrics_verified": False,
            "playback_verified": False, "recommendations_generated": False, "tracks": tracks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playlist", help="User-shared public playlist URL or ID")
    parser.add_argument("--output", type=Path, required=True, help="Workspace-only JSON output")
    args = parser.parse_args()
    try:
        report = import_playlist(args.playlist)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Existing imports may represent another batch; do not silently overwrite.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(json.dumps({k: report[k] for k in ("loaded_tracks", "declared_total", "complete", "requests")},
                         ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
