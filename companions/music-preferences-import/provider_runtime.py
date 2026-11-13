"""Provider boundaries; account data never crosses music services."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path


class QQProvider:
    platform = "qq"

    def __init__(self, client=None, verified_account=None, storage_root=None):
        from qqmusic_api import QQMusicClient
        self.client = client or QQMusicClient()
        self.verified_account = verified_account
        self.storage_root = storage_root

    def discover(self):
        from local_qqmusic import read_local_qq_preferences
        return read_local_qq_preferences(self.storage_root, self.verified_account)

    def sources(self, local, snapshot=None):
        if snapshot is not None:
            raise ValueError("NetEase snapshots cannot be used for QQ Music.")
        sources = deepcopy(local["sources"])
        # Native caches are preference evidence, not authenticated current likes.
        enriched, budget = {}, 12
        for name in ("liked", "recent"):
            source = sources.get(name, {"status": "unavailable", "tracks": [], "complete": False})
            if source.get("status") not in {"available", "partial"}:
                sources[name] = source
                continue
            tracks = []
            for raw in source.get("tracks", []):
                key = raw["track_id"]
                if key not in enriched:
                    track = deepcopy(raw)
                    refs = track.get("provider_refs") or {}
                    needs_ids = not refs.get("song_mid") or not any(
                        (a.get("provider_refs") or {}).get("singer_mid") for a in track.get("artist_refs", []))
                    if needs_ids and budget > 0:
                        budget -= 1
                        try:
                            resolved = self.client.song_details(track)
                            for field in ("observed_at", "raw_fields", "rank", "platform_score"):
                                if field in track:
                                    resolved[field] = deepcopy(track[field])
                            track = resolved
                        except (ValueError, KeyError, TypeError, OSError):
                            track.setdefault("raw_fields", {})["metadata_resolution"] = "unavailable"
                    enriched[key] = track
                track = deepcopy(enriched[key])
                if name == "liked":
                    track.setdefault("raw_fields", {})["membership_status"] = "cached_unverified"
                tracks.append(track)
            source["tracks"], source["complete"] = tracks, False
            source["status"] = "partial"
            sources[name] = source
        sources["weekly"] = {"status": "unavailable", "complete": False, "tracks": [],
            "scope": "qq_weekly_ranking", "reason": "no_verified_credentials_free_weekly_adapter"}
        return sources

    def artist_tracks(self, ident):
        return self.client.artist_tracks(ident, limit=50)

    def lyrics(self, track):
        return self.client.lyrics(track)


def run_selected(selection, output, core_dir, verified_nickname=None, verified_qq_account=None,
                 liked_snapshot=None, max_artists=3, max_songs=8, words=None):
    from run_music_trial import run
    if selection not in {"auto", "netease", "qq"}:
        raise ValueError("Unknown music provider.")
    if core_dir is None:
        raise ValueError("Matching core unavailable; host should locate the installed video skill or bundled core.")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty output directory.")
    platforms = ("netease", "qq") if selection == "auto" else (selection,)
    results = {}
    for name in platforms:
        try:
            provider = QQProvider(verified_account=verified_qq_account) if name == "qq" else None
            result = run(core_dir, output / name, verified_nickname=verified_nickname,
                         liked_snapshot=liked_snapshot if name == "netease" else None,
                         max_artists=max_artists, max_songs=max_songs, provider=provider, words=words)
            results[name] = {"status": "trial_complete_requires_review", **result}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            results[name] = {"status": "unavailable", "reason": str(exc), "ready_to_deliver": False,
                "next_step": "Host should inspect the official client and sign-in/sync state; do not ask a novice for IDs, links or credentials."}
            if hasattr(exc, "discovery"):
                results[name]["discovery"] = exc.discovery
                results[name]["source_states"] = exc.source_states
    report = {"component": "music-preferences-import", "version": "0.3.0-experimental",
              "checked_at": datetime.now(timezone.utc).isoformat(), "providers": results,
              "credentials_used": False, "personal_data_published": False,
              "ready_to_deliver": False}
    output.mkdir(parents=True, exist_ok=True)
    path = output / "providers.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    path.chmod(0o600)
    return report
