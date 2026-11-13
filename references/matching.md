# Transcript matching contract

This is the preserved **strict v1 mode**. The v2 family/retrieval/review pipeline is documented in [workflow.md](workflow.md). Derivatives remain excluded from this strict helper; v2 reports them explicitly after review.

Use the bundled standard-library Python helper for repeatable counts. It does not download captions, infer morphology, verify a video, or decide word senses.

## Inputs

`target.json` is an array of selected English vocabulary items, not the entire source worksheet unless the user requested that scope:

```json
[
  "ecosystem",
  {"word": "benefit", "forms": ["benefits", "benefited", "benefiting"]},
  {"word": "breed", "forms": ["bred", "breeding"]},
  {"word": "compete", "forms": ["competes", "competed", "competing"]}
]
```

The lemma itself is always included. Only add real inflections or spelling variants confirmed by context. Add observed inflections during review so they are not missed. Do not use invented suffix expansions, fuzzy matching, or stemming to claim a match. Do not map `survival` to `survive`, `effectively` to `effective`, or `unconventional` to `conventional` in this strict count; optionally report related derived words separately.

`transcript.txt` contains only the spoken transcript in UTF-8. Strip page navigation, descriptions, suggested videos, comments, AI summaries, subtitle timestamps and cue labels. Resolve overlapping rolling captions before counting occurrences. Preserve any authentic timecodes separately if timestamps will be provided to the user. Never estimate a timestamp from character offsets.

## Invocation

Run with the absolute path to the skill installed in the current environment, from any working directory:

```bash
python3 /absolute/path/to/vocab-video-finder/scripts/match_vocab.py \
  --words target.json --transcript transcript.txt --output report.json
```

Without `--output`, the report goes to stdout. Place task inputs and any needed reports in the active workspace, never in the global skill folder. No third-party Python packages are needed.

## Review the result

- `target_count`: deduplicated target lemmas; repeated entries merge their allowed forms.
- `exact_unique_count`: targets whose exact surface form appears.
- `with_explicit_forms_unique_count`: targets with an exact or explicitly supplied form, counted once per transcript.
- `matches`: actual forms, total mentions, and context excerpts with character offsets in normalized transcript text.
- `status` deliberately marks all results as lexical candidates. The agent must review evidence before reporting verified coverage.

Keep exact/inflected matches, related derivatives, and same-sense matches distinct. A word in an unexpected sense is still a lexical encounter but must not be described as reinforcement of the assigned sense. For example, verb `conditioned` is not evidence of the noun sense “conditions/environment.” When the user wants sense-specific review, count only reviewed matching senses and report the narrower basis.

For each shortlisted item, retain enough working evidence to reconstruct the result: selected vocabulary scope, video URL, transcript source URL/type, verified target forms, and any mismatch between original and repost. Label automatic/third-party transcripts and uncertain wording. A fetched video page is not proof of successful playback. Persist only the small audit record if useful or requested; avoid retaining full copyrighted transcripts unnecessarily.

After changing matching code:

```bash
python3 /absolute/path/to/vocab-video-finder/scripts/test_match_vocab.py
```

Then run the skill-creator validator for the skill folder and a CLI smoke test from a directory outside it. Add regressions for observed matching failures rather than broadening matching rules speculatively.
