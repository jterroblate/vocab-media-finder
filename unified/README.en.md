# Vocab Media Finder

[中文](README.md) · **4.0.0-beta.1** · Python 3.10+ · MIT

Give a tool-enabled agent a learner's word list or an article. Vocab Media Finder helps it find comprehensible videos and optional English songs that naturally reuse the vocabulary, with transcript evidence and reviewed word families.

This is one self-contained skill: one `SKILL.md`, one `run.py`, and one install package. Video and music are internal modules; no second skill or global installation is required.

[Download the unified beta](https://github.com/jterroblate/vocab-video-finder/releases/tag/unified-v4.0.0-beta.1)

[Preview the synthetic report](examples/demo-preview.md), or ask the host to run the offline demo and show its Markdown report without accessing accounts.

## Try the offline demo

```sh
python3 run.py demo --output demo-output
```

From a full source checkout, use `python3 unified/run.py demo --output demo-output`; copying only the `unified/` directory omits the engine sources. The same four synthetic targets pass through the video module and both music-provider modes. Combined Markdown and JSON are generated without reading accounts, contacting services, publishing real links or copying third-party lyrics.

## Prepare a real search

```sh
python3 run.py plan --words words.json --output plan-output --mode both --network domestic --level easy
```

`plan` creates tasks for the host agent. It does not search the web; the host uses its actual tools to locate and verify candidates. No additional subtitle or music service API key is required. The host prepares and reviews word families, including inflections, irregulars, participles, third-person forms, comparatives and derivations. LemmInflect and WordNet are optional local resources; explicit reviewed forms remain available without them.

## Video and music

Choose `starter`, `easy` (default), `standard` or `advanced`. Domestic viewing uses Bilibili, Douyin and Xiaohongshu; global mode can add YouTube. These are selection bands, not CEFR certification. Real candidates require transcript, context, version, accessibility, difficulty and playback-scope checks.

On macOS, music discovery can inspect supported local clients rather than asking novices for playlist links, IDs, passwords or cookies. NetEase Cloud Music 3.1.12 favourites and deduplicated 30-day device history have been tested. QQ public metadata and lyrics have been tested; its explicit-schema local cache adapter remains experimental and native-client testing is outstanding. Windows local reading is unsupported.

New songs by a familiar artist are not assumed to be liked. History rows or platform scores are not exact play counts, and frequency does not establish learning mastery. Music trials remain `ready_to_deliver=false` until reviewed. Video-only tasks skip music access; unavailable music does not block video work.

`run.py video ...` and `run.py music ...` route to the internal engines through the same entry point. Titles never count as body evidence. Final output preserves actual forms, word-family coverage, frequency and verification state.

## Install once

Codex, WorkBuddy, DeepSeek Harness, generic and source packages each contain one root skill. WorkBuddy imports one ZIP. The package includes its runtime dependencies; no legacy video/music install is needed. Package contracts are tested, while native WorkBuddy/DeepSeek execution still needs real-host verification. See [host bootstrap notes](adapters/bootstrap.md).

Public packages exclude personal playlists, student word lists, client databases, credentials, full lyrics and generated user outputs. No sender, scheduler or contacts integration is included. Legacy video v3 and music v0.3 releases remain available; the unified beta is recommended for new trials.

If it saves you time finding learning materials, a Star helps others discover it. Contributions of simpler sources, client adapters and matching fixes are welcome.
