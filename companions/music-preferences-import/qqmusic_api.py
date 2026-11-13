"""Small read-only QQ Music public-metadata adapter, using only the standard library.

``fetch(module, method, parameters)`` injection returns the complete QQ RPC
envelope (top-level code plus req_1.code/data), not unwrapped data. Credentials,
audio URLs, lyric decryption and user-account endpoints are outside this module.
"""
import base64
import binascii
from datetime import datetime, timezone
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


RPC_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
MAX_RESPONSE_BYTES = 8_000_000
MAX_TRACKS = 1000
PAGE_SIZE = 100


class QQMusicError(ValueError):
    """Explicit transport, business, schema or identifier failure."""
    def __init__(self, reason, code=None):
        self.reason = reason
        self.code = code
        suffix = "" if code is None else f" (code {code})"
        super().__init__(reason + suffix)


def _positive_id(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r"[0-9]+", text) and int(text) > 0 else None


def _mid(value):
    return value if isinstance(value, str) and re.fullmatch(r"(?=.*[A-Za-z])[A-Za-z0-9]{10,30}", value) else None


def _utc():
    return datetime.now(timezone.utc).isoformat()


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.netloc != "u.y.qq.com":
            raise QQMusicError("unexpected_rpc_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_rpc(module, method, parameters):
    payload = {"comm": {"ct": 24, "cv": 0, "uin": 0},
               "req_1": {"module": module, "method": method, "param": parameters}}
    url = RPC_URL + "?" + urlencode({"data": json.dumps(payload, separators=(",", ":")), "format": "json"})
    request = Request(url, headers={"Referer": "https://y.qq.com/", "Accept": "application/json",
                                   "User-Agent": "vocab-music-public-metadata/0.3"})
    try:
        with build_opener(_OfficialRedirects()).open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise QQMusicError("rpc_response_too_large")
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise QQMusicError("rpc_response_not_json") from None
    except HTTPError as exc:
        raise QQMusicError("rpc_http_error", exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise QQMusicError("rpc_transport_error") from None


def normalize_qq_track(raw):
    if not isinstance(raw, dict):
        raise QQMusicError("song_metadata_not_object")
    song_id = _positive_id(raw.get("id", raw.get("songid")))
    song_mid = _mid(raw.get("mid", raw.get("songmid")))
    if not song_id and not song_mid:
        raise QQMusicError("song_identifier_missing")
    title = raw.get("name") or raw.get("title") or raw.get("songname")
    if not isinstance(title, str) or not title.strip():
        raise QQMusicError("song_title_missing")
    singers = raw.get("singer") or []
    if not isinstance(singers, list):
        raise QQMusicError("singer_metadata_not_list")
    refs = []
    for singer in singers:
        if not isinstance(singer, dict):
            continue
        singer_id = _positive_id(singer.get("id"))
        singer_mid = _mid(singer.get("mid"))
        name = singer.get("name")
        refs.append({"artist_id": singer_mid or ("id:" + singer_id if singer_id else None),
                     "name": name if isinstance(name, str) else None,
                     "provider_refs": {"singer_id": singer_id, "singer_mid": singer_mid}})
    interval = raw.get("interval")
    duration = (round(interval * 1000) if isinstance(interval, (int, float)) and not isinstance(interval, bool)
                and math.isfinite(interval) and interval >= 0 else None)
    album = raw.get("album")
    album_name = album.get("name") if isinstance(album, dict) else None
    play_url = "https://y.qq.com/n/ryqq/songDetail/" + song_mid if song_mid else None
    return {"platform": "qq", "track_id": song_mid or "id:" + song_id, "title": title.strip(),
            "artists": list(dict.fromkeys(a["name"] for a in refs if a["name"])), "artist_refs": refs,
            "provider_refs": {"song_id": song_id, "song_mid": song_mid}, "album": album_name,
            "duration_ms": duration, "language": None, "play_url": play_url,
            "playback_status": "unchecked", "language_verified": False, "playback_verified": False}


def _song_identity(track):
    return track["provider_refs"]["song_id"] or track["track_id"]


class QQMusicClient:
    def __init__(self, fetch=None):
        self.fetch = fetch or public_rpc

    def _data(self, module, method, parameters):
        payload = self.fetch(module, method, parameters)
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise QQMusicError("rpc_business_error", payload.get("code") if isinstance(payload, dict) else None)
        response = payload.get("req_1")
        if not isinstance(response, dict) or response.get("code") != 0:
            raise QQMusicError("rpc_method_error", response.get("code") if isinstance(response, dict) else None)
        data = response.get("data")
        if not isinstance(data, dict):
            raise QQMusicError("rpc_data_not_object")
        if data.get("code", 0) != 0:
            raise QQMusicError("rpc_data_error", data["code"])
        return data

    def playlist(self, playlist_id, max_tracks=MAX_TRACKS):
        ident = _positive_id(playlist_id)
        if not ident:
            raise QQMusicError("public_playlist_id_invalid")
        if not isinstance(max_tracks, int) or isinstance(max_tracks, bool) or not 1 <= max_tracks <= MAX_TRACKS:
            raise QQMusicError("max_tracks_out_of_range")
        tracks, total, offset, requests = {}, None, 0, 0
        duplicate_count = invalid_count = 0
        changed_total = False
        error = None
        title = None
        while offset < max_tracks:
            try:
                requests += 1
                data = self._data("music.srfDissInfo.DissInfo", "CgiGetDiss", {
                    "disstid": int(ident), "dirid": 0, "tag": True, "song_begin": offset,
                    "song_num": min(PAGE_SIZE, max_tracks - offset), "userinfo": True,
                    "orderlist": True, "onlysonglist": False})
                declared = data.get("total_song_num", data.get("total"))
                if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0:
                    if total is not None and total != declared:
                        changed_total = True
                    total = declared
                title = title or data.get("dissname") or data.get("name")
                rows = data.get("songlist")
                if not isinstance(rows, list):
                    raise QQMusicError("playlist_songlist_missing")
                before = len(tracks)
                for raw in rows[:max_tracks - offset]:
                    try:
                        track = normalize_qq_track(raw)
                    except QQMusicError:
                        invalid_count += 1
                        continue
                    key = _song_identity(track)
                    if key in tracks:
                        duplicate_count += 1
                    tracks[key] = track
                offset += len(rows)
                if not rows or len(tracks) == before:
                    if total != 0:
                        error = "playlist_no_progress"
                    break
                if data.get("hasmore") in (False, 0) or total is not None and offset >= total:
                    break
            except QQMusicError as exc:
                if not tracks:
                    raise
                error = exc.reason
                break
        complete = (total is not None and len(tracks) == total and not duplicate_count
                    and not invalid_count and not changed_total and error is None)
        return {"component": "music-preferences-import-0.3.0-experimental", "platform": "qq",
                "source_kind": "undocumented_public_rpc", "source_url": "https://y.qq.com/n/ryqq/playlist/" + ident,
                "playlist_id": ident, "playlist_title": title, "declared_total": total,
                "loaded_tracks": len(tracks), "tracks": list(tracks.values()), "complete": complete,
                "duplicate_tracks": duplicate_count, "invalid_tracks": invalid_count,
                "total_changed_during_read": changed_total, "detail_error": error, "requests": requests,
                "checked_at_utc": _utc(), "account_context": "unknown", "owner_id": "",
                "authentication_used": False, "key_required": False,
                "lyrics_verified": False, "playback_verified": False}

    def artist_tracks(self, artist_id, limit=50):
        singer_mid = _mid(artist_id)
        if not singer_mid:
            raise QQMusicError("singer_mid_required_not_numeric_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise QQMusicError("artist_limit_out_of_range")
        tracks, offset = {}, 0
        for _ in range(4):
            data = self._data("musichall.song_list_server", "GetSingerSongList", {
                "singerMid": singer_mid, "order": 1, "number": min(limit, 50), "begin": offset})
            rows = data.get("songList")
            if not isinstance(rows, list):
                raise QQMusicError("artist_songlist_missing")
            before = len(tracks)
            for row in rows:
                raw = row.get("songInfo") if isinstance(row, dict) else None
                track = normalize_qq_track(raw)
                if singer_mid not in {a["artist_id"] for a in track["artist_refs"]}:
                    continue
                tracks[_song_identity(track)] = track
                if len(tracks) >= limit:
                    return list(tracks.values())[:limit]
            offset += len(rows)
            total = data.get("totalNum")
            if not rows or len(tracks) == before or isinstance(total, int) and offset >= total:
                break
        return list(tracks.values())[:limit]

    def song_details(self, track):
        refs = track.get("provider_refs") or {}
        ident = _positive_id(refs.get("song_id"))
        if not ident and str(track.get("track_id", "")).startswith("id:"):
            ident = _positive_id(track["track_id"][3:])
        if not ident:
            raise QQMusicError("numeric_song_id_required_for_detail")
        data = self._data("music.pf_song_detail_svr", "get_song_detail_yqq", {"song_id": int(ident)})
        result = normalize_qq_track(data.get("track_info"))
        if result["provider_refs"]["song_id"] != ident:
            raise QQMusicError("song_detail_identifier_mismatch")
        return result

    def lyrics(self, track):
        refs = track.get("provider_refs") or {}
        ident = _positive_id(refs.get("song_id"))
        mid = _mid(refs.get("song_mid")) or _mid(track.get("track_id"))
        if not ident and str(track.get("track_id", "")).startswith("id:"):
            ident = _positive_id(track["track_id"][3:])
        if not ident and not mid:
            raise QQMusicError("lyric_song_identifier_missing")
        params = {"crypt": 0, "lrc_t": 0, "qrc": 0, "qrc_t": 0, "roma": 0,
                  "roma_t": 0, "trans": 0, "trans_t": 0, "type": 1}
        params["songId" if ident else "songMid"] = int(ident) if ident else mid
        data = self._data("music.musichallSong.PlayLyricInfo", "GetPlayLyricInfo", params)
        raw = data.get("lyric")
        if not isinstance(raw, str) or not raw.strip():
            raise QQMusicError("lyric_text_unavailable")
        if re.search(r"\[\d{1,3}:\d{2}", raw):
            text = raw
        else:
            try:
                text = base64.b64decode(raw, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                raise QQMusicError("lyric_not_plain_lrc_or_base64_utf8") from None
        if not text.strip() or "\x00" in text:
            raise QQMusicError("lyric_text_invalid")
        return {"text": text, "source_url": track.get("play_url") or RPC_URL,
                "source_kind": "qq_public_rpc_lyric", "checked_at": _utc(),
                "full_text_exported": False, "recording_version_verified": False}
