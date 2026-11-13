#!/usr/bin/env python3
"""Recording-safe, offline two-provider demo using only authored synthetic data."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from run_music_trial import run, default_core_dir

WORDS = [{"word": "write", "forms": ["writes", "wrote", "written", "writing"],
          "related": [{"word": "writer", "confirmed": True}]},
         {"word": "dream", "forms": ["dreams", "dreamed", "dreamt", "dreaming"],
          "related": [{"word": "dreamer", "confirmed": True}]}]
LYRIC = ("[00:01.00]I wrote a story for you and dreamed of a day.\n"
         "[00:02.00]You said a writer is here, and I am a dreamer.")


class SyntheticProvider:
    def __init__(self, platform):
        self.platform = platform

    def track(self, ident):
        return {"platform": self.platform, "track_id": str(ident), "title": "Synthetic Story " + str(ident),
                "artists": ["Synthetic Artist"], "artist_refs": [{"artist_id": "7", "name": "Synthetic Artist"}],
                "play_url": "https://example.invalid/" + self.platform + "/" + str(ident),
                "playback_status": "synthetic_no_recording"}

    def discover(self):
        return {"discovery": {"platform": self.platform, "context_status": "synthetic_no_account",
                              "authenticated": False}, "sources": {"liked": {
                  "status": "available", "complete": True, "scope": "synthetic_fixture",
                  "tracks": [self.track(42)]}, "recent": {"status": "available", "complete": True,
                  "scope": "synthetic_fixture", "tracks": []}, "weekly": {
                  "status": "unavailable", "complete": False, "reason": "synthetic_demo", "tracks": []}}}

    def sources(self, local, snapshot=None):
        return deepcopy(local["sources"])

    def artist_tracks(self, ident):
        return [self.track(42), self.track(43)]

    def lyrics(self, track):
        return {"text": LYRIC, "source_url": "synthetic:self-authored", "checked_at": "synthetic"}


def run_demo(output, core_dir=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty demo output directory.")
    core = core_dir or default_core_dir()
    results = {name: run(core, output / name, provider=SyntheticProvider(name), words=WORDS,
                         max_artists=1, max_songs=2) for name in ("netease", "qq")}
    report = {"status": "synthetic_offline_demo_complete", "notice": "Synthetic data only; no real account, song or playable recommendation.",
              "web_requests": 0, "personal_data_read": False, "real_recommendations": 0,
              "expected_family_coverage_per_song": 2, "expected_text_occurrences_per_song": 4,
              "providers": results}
    (output / "demo-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run_demo(args.output, args.core_dir), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")
