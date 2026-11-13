from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from preferences import build_preferences, add_artist_candidates
from provider_runtime import QQProvider, run_selected
from run_music_trial import choose_artists, choose_text_trials, expand_english_seeds, default_core_dir, run, PreferenceUnavailable


def track(platform, ident="42", artist="7"):
    return {"platform": platform, "track_id": ident, "title": "Synthetic Song", "artists": ["Synthetic Artist"],
        "artist_refs": [{"artist_id": artist, "name": "Synthetic Artist",
                         "provider_refs": {"singer_mid": "000SyntheticSinger"}}],
        "provider_refs": {"song_id": "100", "song_mid": "000SyntheticSong"}}


class ProviderTests(unittest.TestCase):
    def test_mid_and_platform_identity_survive_aggregation_and_expansion(self):
        p = build_preferences({"liked": {"status": "available", "complete": True,
            "tracks": [track("netease"), track("qq")]}})
        self.assertEqual(len(p["tracks"]), 2)
        self.assertEqual(len(choose_artists(p, 2)), 2)
        self.assertEqual(len(choose_text_trials(p, p["artists"], 2)), 2)
        qq = next(t for t in p["tracks"] if t["platform"] == "qq")
        self.assertEqual(qq["provider_refs"]["song_mid"], "000SyntheticSong")
        self.assertEqual(qq["artist_refs"][0]["provider_refs"]["singer_mid"], "000SyntheticSinger")
        expanded = add_artist_candidates(p, {("qq", "7"): [track("qq", "43")]})
        self.assertEqual(len(expanded["tracks"]), 3)
        self.assertFalse(next(t for t in expanded["tracks"] if t["track_id"] == "43")["already_liked"])

    def test_second_pass_keeps_provider_identity(self):
        p = build_preferences({"liked": {"status": "available", "complete": True,
            "tracks": [track("netease"), track("qq")]}})
        trials = [{"platform": plat, "track_id": "42", "already_liked": True,
                   "match": {"english_text_candidate": True}} for plat in ("netease", "qq")]
        calls = []
        def artist_loader(platform, artist):
            calls.append((platform, artist))
            return []
        result = expand_english_seeds(p, trials, [], default_core_dir(), artist_loader=artist_loader)
        self.assertEqual(calls, [("netease", "7"), ("qq", "7")])
        self.assertEqual(len(result["artist_keys"]), 2)

    def test_unavailable_provider_does_not_block_another_provider(self):
        def fake_run(core, output, **kwargs):
            if kwargs["provider"] is not None:
                raise ValueError("qq client not installed")
            return {"tested_songs": 2, "ready_to_deliver": False}
        with tempfile.TemporaryDirectory() as temp, patch("run_music_trial.run", side_effect=fake_run):
            report = run_selected("auto", Path(temp) / "out", default_core_dir())
            self.assertEqual(report["providers"]["netease"]["status"], "trial_complete_requires_review")
            self.assertEqual(report["providers"]["qq"]["status"], "unavailable")
            self.assertFalse(report["ready_to_deliver"])

    def test_qq_numeric_metadata_resolution_keeps_cache_evidence(self):
        class Client:
            def song_details(self, raw):
                result = track("qq", "000SyntheticSong", "000SyntheticSinger")
                return result
        local = {"sources": {"liked": {"status": "partial", "complete": False, "tracks": [{
            "platform": "qq", "track_id": "id:100", "title": "Synthetic", "artists": ["Synthetic"],
            "artist_refs": [], "provider_refs": {"song_id": "100"}}]},
            "recent": {"status": "unavailable", "complete": False, "tracks": []}}}
        sources = QQProvider(client=Client()).sources(deepcopy(local))
        p = build_preferences(sources)
        self.assertEqual(p["tracks"][0]["track_id"], "000SyntheticSong")
        self.assertEqual(p["tracks"][0]["liked_membership_status"], "cached_unverified")
        self.assertFalse(p["tracks"][0]["already_liked"])
        self.assertEqual(p["sources"]["weekly"]["status"], "unavailable")

    def test_ambiguous_cache_diagnostics_survive_runner(self):
        class Ambiguous:
            platform = "qq"
            def discover(self):
                return {"discovery": {"context_status": "ambiguous", "account_candidates": ["synthetic-a", "synthetic-b"]},
                    "sources": {name: {"status": "unavailable", "complete": False, "tracks": [],
                    "reason": "multiple_cached_accounts_host_verification_required"} for name in ("liked", "recent", "weekly")}}
            def sources(self, local, snapshot):
                return local["sources"]
            def artist_tracks(self, ident):
                self.fail("No artist call")
            def lyrics(self, track):
                self.fail("No lyric call")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(PreferenceUnavailable) as caught:
                run(default_core_dir(), Path(temp)/"out", provider=Ambiguous(), words=["dream"])
            self.assertEqual(caught.exception.discovery["context_status"], "ambiguous")
            with patch("run_music_trial.run", side_effect=caught.exception):
                report = run_selected("qq", Path(temp)/"reported", default_core_dir())
            self.assertEqual(report["providers"]["qq"]["discovery"]["context_status"], "ambiguous")
            self.assertIn("source_states", report["providers"]["qq"])
