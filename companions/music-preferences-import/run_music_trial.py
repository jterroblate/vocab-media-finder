#!/usr/bin/env python3
"""Local client discovery -> preference signals -> same-artist songs -> text matches.

No user IDs, share links, developer keys, credentials or browser login required.
Unavailable sources remain unavailable. Real playback and lyric review are not
claimed by this metadata/text experiment. User preferences stay in its output.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from import_netease import get_json, import_playlist, normalize_track
from local_netease import read_local_preferences
from preferences import build_preferences, add_artist_candidates
from song_match import match_song


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def default_core_dir():
    here = Path(__file__).resolve().parent
    for path in (here.parent / "vocab-video-finder/scripts", here / "core", here.parents[1] / "scripts"):
        if all((path / name).is_file() for name in
               ("word_families.py", "match_vocab.py", "video_sources.py", "runtime_paths.py")):
            return path
    return None


def identity(item, key="track_id"):
    return item.get("platform", "netease"), str(item[key])


class PreferenceUnavailable(ValueError):
    def __init__(self, message, local):
        super().__init__(message)
        self.discovery = deepcopy(local.get("discovery", {}))
        self.source_states = {name: {k: deepcopy(source[k]) for k in
            ("status", "reason", "scope", "complete") if k in source}
            for name, source in local.get("sources", {}).items()}


def refresh_sources(local, liked_report=None, fetch=get_json):
    sources = local["sources"]
    identity = local["discovery"]
    account = identity.get("account_id")
    playlist = identity.get("playlist_id")
    if not account or not playlist:
        sources["weekly"] = {"status": "unavailable", "tracks": [], "complete": False,
            "scope": "platform_weekly_ranking", "reason": "no_resolved_account_context"}
        return sources
    if liked_report is not None:
        correct_url = "https://music.163.com/playlist?id=" + str(playlist)
        if liked_report.get("source_url") != correct_url or str(liked_report.get("owner_id")) != str(account):
            raise ValueError("Online playlist snapshot does not belong to the discovered account.")
        online_tracks = deepcopy(liked_report["tracks"])
        known_members = {str(t["track_id"]) for t in online_tracks}
        known_members.update(str(t) for t in liked_report.get("missing_ids", []))
        for track in online_tracks:
            track.setdefault("raw_fields", {})["membership_status"] = "online_confirmed"
        cached = {str(t["track_id"]): t for t in sources["liked"].get("tracks", [])}
        loaded_ids = {str(t["track_id"]) for t in online_tracks}
        if not liked_report["complete"]:
            for ident, original in cached.items():
                if ident in loaded_ids:
                    continue
                track = deepcopy(original)
                track.setdefault("raw_fields", {}).update(
                    metadata_source="local_cache", membership_status=(
                        "online_confirmed" if ident in known_members else "cached_unverified"))
                online_tracks.append(track)
        cached_only = sum(t.get("raw_fields", {}).get("membership_status") == "cached_unverified"
                          for t in online_tracks)
        sources["liked"] = {"status": "available" if liked_report["complete"] else "partial",
            "scope": "online_favorites_playlist", "complete": liked_report["complete"],
            "declared_total": liked_report["declared_total"], "tracks": online_tracks,
            "checked_at": liked_report["checked_at_utc"],
            "reason": None if liked_report["complete"] else (
                "partial_online_with_unverified_cached_preferences" if cached_only else "online_metadata_incomplete")}
    else:
        sources["liked"]["reason"] = "using_local_snapshot_online_refresh_unavailable"
        for track in sources["liked"].get("tracks", []):
            track.setdefault("raw_fields", {})["membership_status"] = "cached_unverified"
    try:
        data = fetch("/api/v1/play/record", {"uid": account, "type": 1})
        if not isinstance(data, dict):
            raise ValueError("Weekly ranking response must be a JSON object.")
        rows = data.get("weekData")
        if data.get("code") == 200 and isinstance(rows, list) and rows:
            tracks = []
            for rank, row in enumerate(rows, 1):
                if isinstance(row, dict) and isinstance(row.get("song"), dict):
                    track = normalize_track(row["song"])
                    track.update(rank=rank, platform_score=row.get("score"))
                    tracks.append(track)
            sources["weekly"] = {"status": "partial", "tracks": tracks, "complete": False,
                "scope": "platform_weekly_ranking", "window": "last_week", "checked_at": now_iso(),
                "reason": "ranking_scope_not_complete_play_history"}
        else:
            # An anonymous 200/[] was observed; it is not evidence of no plays.
            sources["weekly"] = {"status": "auth_unknown", "tracks": [], "complete": False,
                "scope": "platform_weekly_ranking", "window": "last_week", "checked_at": now_iso(),
                "reason": "empty_or_unavailable_without_authenticated_account_context"}
    except (ValueError, KeyError, TypeError):
        sources["weekly"] = {"status": "unavailable", "tracks": [], "complete": False,
            "scope": "platform_weekly_ranking", "window": "last_week", "checked_at": now_iso(),
            "reason": "request_failed"}
    return sources


def choose_artists(profile, maximum):
    # Reserve attention for recent local evidence; volume remains unique songs,
    # never a claim that a particular artist was played most often.
    recent = [a for a in profile["artists"] if a["signals"]["recent_tracks"]]
    recent.sort(key=lambda a: str(a.get("most_recent_observed_at") or ""), reverse=True)
    result, seen = [], set()
    for artist in recent + profile["artists"]:
        key = identity(artist, "artist_id")
        if key not in seen:
            seen.add(key)
            result.append(artist)
        if len(result) == maximum:
            break
    return result


def title_hint(track):
    title = track.get("title") or ""
    return bool(re.search(r"[A-Za-z]{2}", title)) and not re.search(r"[\u4e00-\u9fff]", title)


def choose_text_trials(profile, artists, maximum):
    selected, seen = [], set()
    # Title wording is only a priority hint: keep arbitrary titles eligible.
    starters, seed_artists = [], set()
    for track in profile["tracks"]:
        if not track.get("already_liked") or not title_hint(track):
            continue
        artist_ids = {(track.get("platform", "netease"), a["artist_id"])
                      for a in track["artist_refs"] if a.get("artist_id")}
        if artist_ids and artist_ids <= seed_artists:
            continue
        starters.append(track)
        seed_artists.update(artist_ids)
        if len(starters) == 2:
            break
    for track in starters:
        selected.append(track)
        seen.add(identity(track))
        if len(selected) >= maximum:
            return selected
    for artist in artists:
        relevant = [t for t in profile["tracks"] if
                    t.get("platform", "netease") == artist.get("platform", "netease") and
                    any(a["artist_id"] == artist["artist_id"] for a in t["artist_refs"])]
        relevant.sort(key=lambda t: not title_hint(t))
        # One known favourite and one other work, when available, exercise both
        # preference paths. This is trial sampling, not a recommendation score.
        known = [t for t in relevant if t.get("already_liked")]
        other = [t for t in relevant if not t.get("already_liked")]
        for track in known[:1] + other[:1] + known[1:2]:
            if identity(track) not in seen:
                selected.append(track)
                seen.add(identity(track))
            if len(selected) >= maximum:
                return selected
    for track in sorted(profile["tracks"], key=lambda t: not title_hint(t)):
        if identity(track) not in seen:
            selected.append(track)
            seen.add(identity(track))
        if len(selected) >= maximum:
            break
    return selected


def analyse_track(track, entries, core_dir, fetch=get_json, lyric_loader=None):
    item = {k: track.get(k) for k in ("track_id", "title", "artists", "artist_refs", "provider_refs",
                                      "play_url", "origin", "already_liked", "liked_membership_status",
                                      "evidence_sources", "source_artists")}
    item["platform"] = track.get("platform", "netease")
    item["candidate_title_hint_only"] = True
    try:
        if lyric_loader is not None:
            lyric = lyric_loader(track)
            raw, source, checked = lyric["text"], lyric["source_url"], lyric["checked_at"]
        else:
            if item["platform"] != "netease":
                raise ValueError("A matching provider lyric adapter is required.")
            data = fetch("/api/song/lyric", {"id": track["track_id"], "tv": -1, "lv": -1, "rv": -1, "kv": -1})
            if not isinstance(data, dict) or data.get("code") != 200:
                raise ValueError("lyric_text_unavailable")
            raw = (data.get("lrc") or {}).get("lyric")
            source, checked = "https://music.163.com/api/song/lyric?id=" + track["track_id"], now_iso()
        item["match"] = match_song(raw, entries, core_dir, title=track.get("title"), artists=track.get("artists"))
        item["text_source"], item["text_checked_at"] = source, checked
    except (ValueError, KeyError, TypeError, OSError) as exc:
        item["match"] = {"text_status": "unavailable", "family_coverage": 0, "text_occurrences": 0,
                         "failure_reason": str(exc),
                         "playback_verified": False, "full_lyrics_exported": False}
    return item


def expand_english_seeds(profile, trials, entries, core_dir, fetch=get_json, max_artists=2,
                         artist_loader=None, lyric_loader=None):
    """Use observed English-text favourites/recent songs for the next pass.

    Genre and audio language remain unverified. This bounded second pass uses
    actual first-pass text evidence rather than assuming every prolific artist
    sings English, or equating an English title with English lyrics.
    """
    tracks = {identity(t): t for t in profile["tracks"]}
    seen_tracks = {identity(t) for t in trials}
    seeds = []
    for trial in trials:
        positive = (trial.get("already_liked") or trial.get("liked_membership_status") == "cached_unverified"
                    or set(trial.get("evidence_sources") or []) & {"recent", "weekly"})
        if positive and trial.get("match", {}).get("english_text_candidate"):
            seed = tracks.get(identity(trial), {})
            for artist in seed.get("artist_refs", []):
                ident = artist.get("artist_id")
                artist_key = (seed.get("platform", "netease"), ident)
                if ident and artist_key not in seeds:
                    seeds.append(artist_key)
    additions, checks, expanded = [], [], {}
    for platform, ident in seeds[:max_artists]:
        try:
            if artist_loader:
                songs = artist_loader(platform, ident)
            else:
                if platform != "netease":
                    raise ValueError("A matching artist provider is required.")
                data = fetch("/api/artist/top/song", {"id": ident})
                if not isinstance(data, dict) or data.get("code") != 200 or not isinstance(data.get("songs"), list):
                    raise ValueError("artist_metadata_unavailable")
                songs = [normalize_track(t) for t in data["songs"]]
            expanded[(platform, ident)] = songs
            checks.append({"platform": platform, "artist_id": ident, "status": "available", "songs": len(songs)})
        except (ValueError, KeyError, TypeError):
            checks.append({"platform": platform, "artist_id": ident, "status": "unavailable"})
    extended = add_artist_candidates(profile, expanded)
    for platform, ident in seeds[:max_artists]:
        candidates = [t for t in extended["tracks"] if not t.get("already_liked")
                      and t.get("platform", "netease") == platform and identity(t) not in seen_tracks
                      and any(a.get("artist_id") == ident for a in t["artist_refs"])]
        candidates.sort(key=lambda t: not title_hint(t))
        for track in candidates[:2]:
            seen_tracks.add(identity(track))
            additions.append(analyse_track(track, entries, core_dir, fetch, lyric_loader))
    return {"basis": "retrieved_english_text_from_observed_favorites_or_recent_songs",
            "artist_ids": [ident for _, ident in seeds[:max_artists]],
            "artist_keys": [{"platform": p, "artist_id": i} for p, i in seeds[:max_artists]],
            "artist_checks": checks, "new_song_trials": additions,
            "candidate_tracks_in_this_pool": len(extended["tracks"])}


def run(core_dir, output, verified_nickname=None, liked_snapshot=None, max_artists=3, max_songs=8,
        provider=None, words=None):
    if core_dir is None or not (Path(core_dir) / "word_families.py").is_file():
        raise ValueError("The host must locate the installed vocab-video-finder/scripts core before matching.")
    entries = (deepcopy(words) if isinstance(words, list) else
               json.loads(Path(words).read_text(encoding="utf-8")) if words is not None else
               json.loads(Path(__file__).with_name("test_words.json").read_text(encoding="utf-8")))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Vocabulary must be a nonempty JSON list of words or word/form objects.")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty trial output directory.")
    local = (provider.discover() if provider else
             read_local_preferences(verified_nickname=verified_nickname))
    platform = provider.platform if provider else "netease"
    online_error = None
    if provider:
        sources = provider.sources(local, liked_snapshot)
        def artist_loader(requested_platform, ident):
            if requested_platform != platform:
                raise ValueError("Provider identity mismatch.")
            return provider.artist_tracks(ident)
        lyric_loader = provider.lyrics
    else:
        playlist = local["discovery"].get("playlist_id")
        if not playlist and not local["sources"]["recent"].get("tracks"):
            raise PreferenceUnavailable("Local account context is unavailable or ambiguous; host inspection is required.", local)
        try:
            online = ((json.loads(Path(liked_snapshot).read_text(encoding="utf-8")) if liked_snapshot
                       else import_playlist(str(playlist))) if playlist else None)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            online = None
            online_error = type(exc).__name__
        sources = refresh_sources(local, online)
        artist_loader = lyric_loader = None
    profile = build_preferences(sources)
    if not profile["tracks"]:
        raise PreferenceUnavailable("No readable preference records; the host should inspect the client and supported cache.",
                                    {"discovery": local["discovery"], "sources": sources})
    artists = choose_artists(profile, max_artists)
    expanded, expansion_checks = {}, []
    for artist in artists:
        try:
            if artist_loader:
                songs = artist_loader(artist.get("platform", platform), artist["artist_id"])
            else:
                data = get_json("/api/artist/top/song", {"id": artist["artist_id"]})
                if not isinstance(data, dict) or data.get("code") != 200 or not isinstance(data.get("songs"), list):
                    raise ValueError("artist_metadata_unavailable")
                songs = [normalize_track(s) for s in data["songs"]]
            expanded[identity(artist, "artist_id")] = songs
            expansion_checks.append({"platform": platform, "artist_id": artist["artist_id"], "status": "available",
                                     "songs": len(songs)})
        except (ValueError, KeyError, TypeError):
            expansion_checks.append({"artist_id": artist["artist_id"], "status": "unavailable"})
    candidates = add_artist_candidates(profile, expanded)
    trials = []
    for track in choose_text_trials(candidates, artists, max_songs):
        trials.append(analyse_track(track, entries, core_dir, lyric_loader=lyric_loader))
    extension = expand_english_seeds(candidates, trials, entries, core_dir,
                                     artist_loader=artist_loader, lyric_loader=lyric_loader)
    trials.extend(extension.pop("new_song_trials"))
    trials.sort(key=lambda t: (-int(t["match"].get("english_text_candidate", False)),
        -t["match"]["family_coverage"],
        -sum(math.log1p(f["occurrences"]) for f in t["match"].get("per_family", []))))
    summary = {"experiment": "local_music_preferences_to_song_text_0.3", "platform": platform, "checked_at": now_iso(),
        "user_supplied_ids_or_links": False, "developer_key_required": False, "credentials_used": False,
        "account_context": local["discovery"], "online_refresh_error": online_error,
        "sources": profile["sources"], "unique_preference_tracks": len(profile["tracks"]),
        "supported_artists": len(profile["artists"]), "selected_artists": artists,
        "artist_expansion": expansion_checks, "candidate_tracks": extension["candidate_tracks_in_this_pool"],
        "english_seed_extension": extension,
        "vocabulary_entries": len(entries), "general_test_vocabulary": words is None, "song_trials": trials,
        "exact_play_frequency_available": False, "ready_to_deliver": False,
        "remaining_review": ["lyric_sense_and_completeness", "recording_version", "sung_difficulty", "playback"]}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("profile.json", profile), ("trial-summary.json", summary)):
        path = output / name
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        path.chmod(0o600)
    return {"liked_tracks": profile["sources"]["liked"]["loaded_tracks"],
            "recent_local_tracks": profile["sources"]["recent"]["loaded_tracks"],
            "weekly_status": profile["sources"]["weekly"]["status"],
            "candidate_tracks": extension["candidate_tracks_in_this_pool"], "tested_songs": len(trials),
            "english_text_matches": sum(t["match"].get("english_text_candidate", False) and
                                         t["match"]["family_coverage"] > 0 for t in trials),
            "full_lyrics_saved": False, "ready_to_deliver": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-dir", type=Path, default=default_core_dir(),
                   help="Vocabulary core directory; detect sibling, bundled core, or source repository")
    p.add_argument("--provider", choices=("auto", "netease", "qq"), default="auto")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--words", type=Path, help="User vocabulary JSON; omit only for the general test vocabulary")
    p.add_argument("--verified-nickname", help="Optional current nickname observed by the host, never requested from the user")
    p.add_argument("--liked-snapshot", type=Path, help="Reuse this account's recent online metadata snapshot")
    p.add_argument("--verified-qq-account", help="QQ account context observed by the host, never requested from the user")
    p.add_argument("--max-artists", type=int, choices=range(1, 6), default=3)
    p.add_argument("--max-songs", type=int, choices=range(1, 13), default=8)
    a = p.parse_args()
    try:
        from provider_runtime import run_selected
        print(json.dumps(run_selected(a.provider, a.output, a.core_dir, a.verified_nickname,
                                      a.verified_qq_account, a.liked_snapshot, a.max_artists, a.max_songs,
                                      words=a.words), ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        p.exit(2, str(exc) + "\n")
