"""Pure, local preference aggregation. Signals never imply verified play counts.

Inputs use the normalised track format from the music import component. A source
is one of liked/recent/weekly and carries its own availability and completeness.
No network requests, authentication, song-language or playback checks occur here.
"""

from copy import deepcopy
from datetime import datetime


SOURCE_NAMES = ("liked", "weekly", "recent")
SOURCE_STATUSES = {"available", "partial", "unavailable", "auth_unknown"}
USABLE_STATUSES = {"available", "partial"}
TRACK_METADATA = ("title", "album", "duration_ms", "play_url", "language", "provider_refs")


def _identifier(value):
    if value is None or isinstance(value, bool):
        return None
    result = str(value).strip()
    return result if result and result not in {"0", "-1"} else None


def _identity(track):
    return str(track.get("platform") or "netease"), _identifier(track.get("track_id"))


def _timestamp(value):
    """Comparison only; preserve the source's original timestamp in the output."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000 if abs(value) >= 1_000_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # A timezone-free string has no unambiguous global ordering.
            return result.timestamp() if result.tzinfo is not None else None
        except ValueError:
            return None
    return None


def _names(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return list(dict.fromkeys(n for n in value if isinstance(n, str) and n))
    return []


def _normalise(track):
    if not isinstance(track, dict):
        return None
    platform, ident = _identity(track)
    if ident is None:
        return None
    result = deepcopy(track)
    result.update(platform=platform, track_id=ident, artists=_names(track.get("artists")))
    refs = []
    for raw in track.get("artist_refs", []) or []:
        if isinstance(raw, dict):
            ref = {"artist_id": _identifier(raw.get("artist_id")),
                   "name": raw.get("name") if isinstance(raw.get("name"), str) else None}
            if isinstance(raw.get("provider_refs"), dict):
                ref["provider_refs"] = deepcopy(raw["provider_refs"])
            refs.append(ref)
    result["artist_refs"] = refs
    return result


def _raw_fields(track):
    result = deepcopy(track.get("raw_fields", {}))
    if not isinstance(result, dict):
        result = {"source_value": result}
    for name in ("observed_at", "rank", "platform_score"):
        if name in track:
            result[name] = deepcopy(track[name])
    return result


def _merge_metadata(current, incoming):
    for name in TRACK_METADATA:
        if current.get(name) is None and incoming.get(name) is not None:
            current[name] = deepcopy(incoming[name])
    current["artists"] = list(dict.fromkeys(current["artists"] + incoming["artists"]))
    for ref in incoming["artist_refs"]:
        if ref not in current["artist_refs"]:
            current["artist_refs"].append(deepcopy(ref))


def _most_recent(evidence):
    values = [(e["raw_fields"].get("observed_at"),
               _timestamp(e["raw_fields"].get("observed_at")))
              for e in evidence if e["source"] == "recent"]
    values = [(original, stamp) for original, stamp in values if stamp is not None]
    return max(values, key=lambda item: item[1])[0] if values else None


def _track_sort(track):
    signals = set(track["evidence_sources"])
    stamp = _timestamp(track.get("most_recent_observed_at")) or 0
    return (-int({"liked", "weekly"} <= signals), -int("weekly" in signals),
            -int("liked" in signals), -stamp, track["platform"], track["track_id"])


def build_preferences(sources):
    """Aggregate unique song/artist evidence without manufacturing play counts.

    Each source has ``status``, ``tracks``, ``complete`` and optional
    ``scope/window/checked_at/declared_total/reason``. In unavailable or unknown
    authentication states, even a supplied tracks array is not treated as known
    user evidence. Raw rank, score and timestamps remain source-attributed.
    """
    if not isinstance(sources, dict):
        raise ValueError("sources must be a dictionary")
    summaries, tracks = {}, {}
    for source_name in SOURCE_NAMES:
        source = sources.get(source_name, {"status": "unavailable", "reason": "not_supplied"})
        if not isinstance(source, dict) or source.get("status") not in SOURCE_STATUSES:
            raise ValueError(f"Invalid status for {source_name}")
        raw_tracks = source.get("tracks", [])
        if not isinstance(raw_tracks, list):
            raise ValueError(f"tracks must be a list for {source_name}")
        summary = {k: deepcopy(source[k]) for k in
                   ("status", "scope", "window", "checked_at", "declared_total", "reason", "error")
                   if k in source}
        summary["complete"] = source.get("complete") is True
        summary["received_tracks"] = len(raw_tracks)
        summary["loaded_tracks"] = 0
        summary["invalid_tracks"] = 0
        summaries[source_name] = summary
        if source["status"] not in USABLE_STATUSES:
            summary["complete"] = False
            continue
        source_keys = set()
        for raw in raw_tracks:
            track = _normalise(raw)
            if track is None:
                summary["invalid_tracks"] += 1
                summary["complete"] = False
                continue
            key = _identity(track)
            source_keys.add(key)
            if key not in tracks:
                tracks[key] = {"platform": key[0], "track_id": key[1],
                               "title": track.get("title"), "artists": track["artists"],
                               "artist_refs": track["artist_refs"], "origin": "observed_preference",
                               "evidence": []}
                tracks[key].update({name: deepcopy(track[name]) for name in TRACK_METADATA
                                    if name in track})
            else:
                _merge_metadata(tracks[key], track)
            evidence = {"source": source_name, "raw_fields": _raw_fields(track)}
            for name in ("scope", "window", "checked_at"):
                if name in source:
                    evidence[name] = deepcopy(source[name])
            if evidence not in tracks[key]["evidence"]:
                tracks[key]["evidence"].append(evidence)
        summary["loaded_tracks"] = len(source_keys)

    artists, unresolved = {}, []
    for key, track in tracks.items():
        track["evidence_sources"] = [name for name in SOURCE_NAMES
                                     if any(e["source"] == name for e in track["evidence"])]
        track["already_liked"] = any(e["source"] == "liked" and
            e["raw_fields"].get("membership_status") != "cached_unverified" for e in track["evidence"])
        track["liked_membership_status"] = ("confirmed_by_source" if track["already_liked"] else
            "cached_unverified" if "liked" in track["evidence_sources"] else "not_observed")
        track["most_recent_observed_at"] = _most_recent(track["evidence"])
        resolved_names = set()
        for ref in track["artist_refs"]:
            if ref["artist_id"] is None:
                continue
            artist_key = (track["platform"], ref["artist_id"])
            artist = artists.setdefault(artist_key, {"platform": artist_key[0],
                    "artist_id": artist_key[1], "names": [], "supporting_tracks": [],
                    "evidence_sources": [], "signals": {"liked_weekly_overlap": 0,
                    "weekly_tracks": 0, "liked_tracks": 0, "recent_tracks": 0},
                    "most_recent_observed_at": None})
            if ref["name"]:
                resolved_names.add(ref["name"])
                if ref["name"] not in artist["names"]:
                    artist["names"].append(ref["name"])
            identity = {"platform": key[0], "track_id": key[1]}
            if identity in artist["supporting_tracks"]:
                continue
            artist["supporting_tracks"].append(identity)
            signal_names = set(track["evidence_sources"])
            artist["signals"]["liked_weekly_overlap"] += int({"liked", "weekly"} <= signal_names)
            for signal in SOURCE_NAMES:
                if signal in signal_names:
                    artist["signals"][signal + "_tracks"] += 1
                    if signal not in artist["evidence_sources"]:
                        artist["evidence_sources"].append(signal)
            old = _timestamp(artist["most_recent_observed_at"])
            new = _timestamp(track["most_recent_observed_at"])
            if new is not None and (old is None or new > old):
                artist["most_recent_observed_at"] = track["most_recent_observed_at"]
        unknown_names = [ref["name"] for ref in track["artist_refs"]
                         if ref["artist_id"] is None and ref["name"]]
        unknown_names.extend(name for name in track["artists"] if name not in resolved_names)
        for name in dict.fromkeys(unknown_names):
            unresolved.append({"platform": key[0], "track_id": key[1], "name": name,
                               "reason": "missing_artist_id"})

    def artist_sort(artist):
        signals = artist["signals"]
        stamp = _timestamp(artist["most_recent_observed_at"]) or 0
        return (-signals["liked_weekly_overlap"], -signals["weekly_tracks"],
                -signals["liked_tracks"], -stamp, artist["platform"], artist["artist_id"])

    all_complete = all(s["status"] == "available" and s["complete"] for s in summaries.values())
    usable = [s for s in summaries.values() if s["status"] in USABLE_STATUSES]
    status = ("available" if all_complete else "partial") if tracks else "no_evidence"
    if tracks:
        no_evidence_reason = None
    elif all_complete:
        no_evidence_reason = "confirmed_empty"
    elif not usable:
        no_evidence_reason = "no_usable_sources"
    else:
        no_evidence_reason = "partial_sources_empty"
    return {"component": "music-preferences-signals-experimental-0.3", "status": status,
            "no_evidence_reason": no_evidence_reason, "sources": summaries,
            "tracks": sorted(tracks.values(), key=_track_sort),
            "artists": sorted(artists.values(), key=artist_sort), "unresolved_artists": unresolved,
            "ranking_policy": {"kind": "engineering_default_not_accuracy_estimate",
                "artist_order": ["liked_weekly_unique_track_overlap", "weekly_unique_tracks",
                                 "liked_unique_tracks", "latest_recent_observation"],
                "row_counts_are_play_counts": False,
                "platform_score_is_play_count": False},
            "language_verified": False, "playback_verified": False}


def add_artist_candidates(profile, artist_tracks):
    """Return a copy with unverified same-artist works added as candidates.

    ``artist_tracks`` maps an artist ID to normalised tracks (NetEase by default),
    or uses ``(platform, artist_id)`` keys for multiple platforms. Only artists
    already supported by this preference profile can expand the candidate set.
    Existing songs retain their observed origin; new songs use artist_expansion.
    """
    if not isinstance(artist_tracks, dict):
        raise ValueError("artist_tracks must be an artist-ID to track-list mapping")
    result = deepcopy(profile)
    candidates = {_identity(t): t for t in result.get("tracks", [])}
    supported = {(a["platform"], a["artist_id"]) for a in result.get("artists", [])}
    liked = {key for key, t in candidates.items() if t.get("already_liked") is True}
    for raw_artist, raw_tracks in artist_tracks.items():
        artist_key = raw_artist if isinstance(raw_artist, tuple) else ("netease", raw_artist)
        artist_key = (str(artist_key[0]), _identifier(artist_key[1]))
        if artist_key not in supported:
            continue
        if not isinstance(raw_tracks, list):
            raise ValueError("Each artist candidate value must be a track list")
        for raw in raw_tracks:
            incoming = _normalise(raw)
            if incoming is None or incoming["platform"] != artist_key[0]:
                continue
            # The adapter's mapping is the expansion provenance; a conflicting
            # explicit artist ID must not silently turn into same-artist proof.
            explicit_ids = {r["artist_id"] for r in incoming["artist_refs"] if r["artist_id"]}
            if explicit_ids and artist_key[1] not in explicit_ids:
                continue
            key = _identity(incoming)
            if key not in candidates:
                candidates[key] = {"platform": key[0], "track_id": key[1],
                    "title": incoming.get("title"), "artists": incoming["artists"],
                    "artist_refs": incoming["artist_refs"], "origin": "artist_expansion",
                    "evidence": [], "evidence_sources": [], "most_recent_observed_at": None}
                candidates[key].update({name: deepcopy(incoming[name]) for name in TRACK_METADATA
                                        if name in incoming})
            else:
                _merge_metadata(candidates[key], incoming)
            track = candidates[key]
            track["already_liked"] = key in liked
            source_artist = {"platform": artist_key[0], "artist_id": artist_key[1]}
            track.setdefault("source_artists", [])
            if source_artist not in track["source_artists"]:
                track["source_artists"].append(source_artist)
            track["language_verified"] = False
            track["playback_verified"] = False
    result["tracks"] = sorted(candidates.values(), key=_track_sort)
    return result
