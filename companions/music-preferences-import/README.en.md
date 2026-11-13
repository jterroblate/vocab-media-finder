# music-preferences-import

[中文](README.md) · `0.3.0-experimental` · Python 3.10+ · MIT

An experimental companion to `vocab-video-finder`, released as `music-v0.3.0`. It adds preference-aware English-song discovery while the video skill remains an independent component.

## Simple user flow

> Use my music preferences to find English songs containing these words.

On macOS, the host checks supported local data from NetEase Cloud Music and QQ Music. It should not ask a novice for playlist URLs, numeric account IDs, cookies, passwords, or new developer keys. The `auto` provider runs each service separately; an unavailable provider does not block the other.

When login or cache data is missing, the host should open the official client and ask the user to log in normally and allow it to sync. This companion does not implement its own QR-code login.

## Provider support

NetEase Cloud Music for macOS 3.1.12 has been tested with local favourites and a deduplicated 30-day device-history view. Public metadata, artist expansion, and lyric retrieval have also been tested.

For QQ Music, the keyless public path has been tested from playlist pagination through numeric song details, singer-MID catalogue expansion, and plain base64 LRC retrieval. The macOS cache adapter requires explicit folder relationships; equal track counts do not establish a favourites mapping. Native QQ Music was not installed or tested on the development machine. QQ recent-history schemas are not supported, and private favourites are not guaranteed to be available.

Windows local readers are not supported. A cache does not prove an API session is authenticated. An unverified empty history is recorded as `auth_unknown`, not “no plays”. History rows are preference signals, not precise listening-frequency measurements.

## Matching and review

The host prepares and reviews a vocabulary JSON using the shared video core or its language tools. Confirmed `forms/related` include inflections, irregular forms, participles, third-person singular forms, and derivations. A plain string list matches only the supplied forms; the CLI does not invent a reviewed word family.

Artist expansion uses favourites, recent-history signals and retrieved lyric-language evidence. Expanded songs are not labelled already liked; titles never count as lyric matches. Lyric completeness, recording version, language, sense, listening difficulty and playback still require review. Trial outputs set `ready_to_deliver=false` and omit full lyrics.

## Run and package

For a recording-safe offline demonstration:

```sh
python3 demo.py --output demo-output
```

The host runs a real trial internally:

```sh
python3 run_music_trial.py --provider auto --words '<workspace vocabulary.json>' --output '<new workspace directory>'
```

Omitting `--words` uses the general test vocabulary. The runner finds a bundled, adjacent, or source-repository matching core automatically. Paths and account-verification parameters are handled by the host, not the user.

The standalone music ZIP bundles the core. A paired ZIP contains two skill folders and is not a single WorkBuddy import: import the separate skills individually. Codex, WorkBuddy, DeepSeek Harness, generic and source packages include manifests and SHA256 checksums. WorkBuddy and DeepSeek package contracts are tested; native execution on those hosts is not.

The WorkBuddy starter archive contains the two individual ZIPs; extract it and import each ZIP separately.

See the [host adaptation prompt](adapters/bootstrap.md). User preferences, full lyrics, client databases, runtime outputs and credentials are excluded from public packages. This companion does not send messages or schedule deliveries.
