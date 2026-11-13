# vocab-video-finder v3

[中文](README.md) · Python 3.10+ · MIT · No extra caption-service key

Find videos in which target vocabulary appears naturally. Give a tool-enabled agent a word list or article, and it follows this workflow:

**identify useful scenes → search at the right level → verify complete captions → assemble a complementary bundle**

Each result reports actual word forms, family coverage, frequencies, difficulty evidence, and verification limits. Bundle selection rewards both broader coverage and repeated exposure.

## Choose a level and viewing profile

| Band | Typical material | Default duration |
|---|---|---|
| `starter` | simple actions and repetitive stories | 20–180 seconds |
| `easy` (default) | clear everyday dialogue and graded stories | 1–6 minutes |
| `standard` | natural short conversations and interviews | 2–10 minutes |
| `advanced` | denser, abstract or specialized content | 3–15 minutes |

These are teaching estimates, not CEFR certification. Provider levels are stored separately. Short videos, children's content, and slow speech are not automatically easy. The [source catalog](references/sources.json) includes officially graded material such as British Council's A1/A2 Starting Out series.

The `domestic` viewer profile restricts delivery to Bilibili, Douyin and Xiaohongshu. `global` adds YouTube and other suitable video pages. An agent may use an overseas original transcript to verify a domestic copy without assuming the viewer can open the original.

## Match families, not just exact strings

Supported forms include third-person verbs, participles, irregulars, plurals and comparatives. LemmInflect and WordNet can propose inflections and derivations; even `written` or `competing` can recover their base before derivation.

For example, a family can include `compete`, `competes`, `competed`, `competing`, `competition` and `competitive`. Wider recall does not prove semantic suitability: actual occurrences and relations still need contextual review. Pure pertainyms such as `verbal → word` do not count. Synonyms help find scenes, but never count as target hits.

Core scripts use Python 3.10+ and the standard library. Optional dictionary resources enable automatic expansion. Without them, supply explicit `forms/related` for contextual review; the project does not claim complete automatic morphology without lexical resources.

## Try it offline

Use `python3` instead of `python` if that is your Python 3 command.

```sh
python scripts/demo.py --output demo-output
```

The synthetic story demonstrates four families and fourteen occurrences. It produces no real recommendation URLs, uses no network and sends nothing.

For live work:

```sh
python scripts/video_pipeline.py expand --words examples/words.json --output families.json
python scripts/video_pipeline.py discover --families families.json \
  --network global --level easy --scene "friendly competition" --output plan.json
```

An agent executes the resulting search plan using its actual host tools, reads complete transcripts, reviews context and selects videos. The CLI alone is not a fully automated live-web retriever. See [the workflow](references/workflow.md).

## Optional music companion

The experimental [music-preferences-import](companions/music-preferences-import/README.en.md) companion uses supported local macOS data from NetEase Cloud Music or QQ Music to find English songs containing target word families. It uses the same matching rules as the video skill, with an independent release and no sender or scheduler.

Download standalone music packages or a paired bundle from [music-v0.3.0](https://github.com/jterroblate/vocab-video-finder/releases/tag/music-v0.3.0). NetEase native reading has been tested; QQ native reading remains experimental. See the companion README for provider limits and a safe offline recording demo.

## Agent hosts

```sh
python tools/prepare_package.py --host workbuddy --author YourName --output ../workbuddy.zip
python tools/prepare_package.py --host deepseek --output ../my-project/.dsh/skills/vocab-video-finder
```

Codex automatic invocation remains supported. [Bootstrap instructions](adapters/bootstrap.md) inspect real tools before execution. WorkBuddy and DeepSeek package/document contracts are tested; their native runtimes were not tested here. A pure chat interface does not gain filesystem or web access. Host/model credentials are separate from the skill's lack of an additional caption-service key.

Douyin/Xiaohongshu access or captions may be unavailable in a given tool. Short links must resolve to a specific item; text notes are not videos. Access limits and unavailable transcripts are reported rather than bypassed.

No media downloads, automatic sending, student data, full third-party transcripts or credentials are included. See [contribution guidance](CONTRIBUTING.md). If this saves you time, a Star helps others find it; contributions to easier sources and better verification are welcome.
