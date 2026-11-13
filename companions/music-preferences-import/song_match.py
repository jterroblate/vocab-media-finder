"""Match retrieved platform lyric text via the existing vocabulary-family core.

Counts concern the retrieved text. Recording completeness, sense and playback
remain review steps. This module never exports full lyrics or text contexts.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path
import re
import sys

STAMP = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]")
CREDIT = re.compile(r"^(?:作词|作曲|编曲|制作|录音|混音|母带|监制|发行|出品|词曲|词|曲|"
                    r"lyricist|lyrics?(?:\s+by)?|music\s+by|written\s+by|compos(?:er|ed\s+by)|"
                    r"produc(?:er|ed\s+by)|arrang(?:er|ed\s+by))\s*[:：]", re.I)
ENGLISH_FUNCTION_WORDS = set("i i'm i've i'll i'd you your you're you've you'll we we're we've they their they're he his she her it its it's the this that these those and but with without from into because when where what which who why how don't doesn't didn't won't can't couldn't would should have has had was were been being are is not to of for on in my me our us them as if so then than".split())
ENGLISH_ANCHORS = set("i i'm i've i'll i'd you your you're you've you'll we we're we've they their they're she her he his my me our us them don't doesn't didn't won't can't couldn't would should".split())


@contextmanager
def matching_core(core_dir):
    names = ("word_families", "video_sources", "match_vocab", "runtime_paths")
    previous_modules = {name: sys.modules.get(name) for name in names}
    previous_path = list(sys.path)
    try:
        for name in names:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(core_dir))
        yield importlib.import_module("word_families"), importlib.import_module("video_sources")
    finally:
        sys.path[:] = previous_path
        for name in names:
            sys.modules.pop(name, None)
            if previous_modules[name] is not None:
                sys.modules[name] = previous_modules[name]


def lyric_lines(raw, title=None, artists=None):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("No lyric text was returned.")
    lines, seen = [], set()
    for raw_line in raw.splitlines():
        stamps = STAMP.findall(raw_line)
        text = STAMP.sub("", raw_line).strip()
        if not text or re.match(r"^\[[a-z]+:", text, re.I) or CREDIT.match(text):
            continue
        # Remove an explicit title/artist credit only when known track metadata
        # matches it. A sung line identical to the title alone remains eligible.
        if title and artists and stamps and min(int(m) * 60 + float(s) for m, s in stamps) <= 3:
            for artist in artists:
                header = re.fullmatch(re.escape(title.strip()) + r"\s+[-–—]\s+" + re.escape(artist.strip()), text, re.I)
                if header:
                    text = ""
                    break
            if not text:
                continue
        if stamps:
            for minute, second in stamps:
                start = int(minute) * 60 + float(second)
                key = (start, text)
                if key not in seen:
                    seen.add(key)
                    lines.append({"start": start, "text": text})
        else:
            # Without timing, duplicate lines may be genuine chorus repetitions.
            lines.append({"start": None, "text": text})
    return lines


def match_song(raw, entries, core_dir, title=None, artists=None):
    core_dir = Path(core_dir).resolve()
    if not (core_dir / "word_families.py").is_file():
        raise ValueError("core_dir must contain the vocab-video-finder core scripts.")
    lines = lyric_lines(raw, title=title, artists=artists)
    text = "\n".join(line["text"] for line in lines)
    if not text.strip():
        raise ValueError("Lyric body contains no usable lines.")
    with matching_core(core_dir) as (core, sources):
        doc = sources.normalize_transcript(text, format="text")
        families = core.expand_targets(entries, use_resources=False)
        matched = core.match_families(families, doc)
    occurrences = matched["occurrences"]
    labels = {f["family_id"]: [t["word"] for t in f["targets"]] for f in families["families"]}
    unique = {(o["start"], o["end"]): o for o in occurrences
              if not o["collision"] and o["relation_confirmed"] is True}
    by_family = Counter(o["family_id"] for o in unique.values())
    per_family = []
    for ident, count in by_family.items():
        actual = [o for o in unique.values() if o["family_id"] == ident]
        per_family.append({"targets": labels[ident], "occurrences": count,
                           "actual_forms": dict(Counter(o["form"] for o in actual)),
                           "relations": dict(Counter(o["kind"] for o in actual))})
    english = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    han = re.findall(r"[\u4e00-\u9fff]", text)
    lowered = [token.lower() for token in english]
    function_hits = sum(token in ENGLISH_FUNCTION_WORDS for token in lowered)
    distinct_function_words = set(lowered) & ENGLISH_FUNCTION_WORDS
    english_hint = (len(english) >= 15 and len(han) < len(english) / 4
                    and len(distinct_function_words) >= 4 and function_hits / len(english) >= .12
                    and len(set(lowered) & ENGLISH_ANCHORS) >= 2)
    return {"text_status": "platform_text_retrieved", "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text_completeness": "unverified_against_recording", "recording_version_match": "track_id_only",
            "english_text_candidate": english_hint,
            "language_detection_basis": "conservative_function_word_hint_requires_host_review",
            "language_verified": False, "playback_verified": False,
            "context_review_status": "pending", "full_lyrics_exported": False,
            "lyric_lines": len(lines), "family_coverage": len(by_family),
            "text_occurrences": len(unique), "relation_counts": dict(Counter(o["kind"] for o in unique.values())),
            "unresolved_collisions": sum(o["collision"] for o in occurrences),
            "unconfirmed_relation_occurrences": sum(o["relation_confirmed"] is not True for o in occurrences),
            "per_family": per_family}
