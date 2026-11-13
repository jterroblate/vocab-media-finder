#!/usr/bin/env python3
"""Count vocabulary candidates in a clean transcript; no inferred morphology."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)*")


def normalize(text):
    return unicodedata.normalize("NFKC", text).replace("’", "'").lower()


def key(text):
    return tuple(TOKEN.findall(normalize(text)))


def surface(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Each word/form must be a nonempty English string.")
    if not re.fullmatch(r"[a-z]+(?:[ '\-][a-z]+)*", normalize(value.strip())):
        raise ValueError(f"Use English words or phrases, without glosses: {value!r}")
    return key(value)


def prepare(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("Vocabulary JSON must be a nonempty list.")
    merged = {}
    for entry in entries:
        if isinstance(entry, str):
            entry = {"word": entry}
        if not isinstance(entry, dict) or "word" not in entry:
            raise ValueError("Each entry must be a string or an object with 'word'.")
        lemma = surface(entry["word"])
        forms = entry.get("forms", [])
        if not isinstance(forms, list):
            raise ValueError("'forms' must be a list of explicit surface forms.")
        item = merged.setdefault(lemma, {"word": entry["word"].strip(), "forms": {lemma}})
        item["forms"].update(surface(form) for form in forms)
    return merged


def match(entries, transcript):
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("Transcript must be nonempty clean spoken text.")
    targets = prepare(entries)
    text = normalize(transcript)
    tokens = list(TOKEN.finditer(text))
    token_keys = [token.group() for token in tokens]
    matches = []
    for lemma, item in targets.items():
        occurrences = {}
        for form in item["forms"]:
            width = len(form)
            for index in range(len(tokens) - width + 1):
                if tuple(token_keys[index:index + width]) != form:
                    continue
                first, last = tokens[index].start(), tokens[index + width - 1].end()
                # Do not build a phrase across sentence boundaries.
                if any(re.search(r"[.!?;]", text[tokens[j].end():tokens[j+1].start()])
                       for j in range(index, index + width - 1)):
                    continue
                occurrences[(first, last)] = {
                    "form": text[first:last],
                    "exact": form == lemma,
                    "start": first,
                    "end": last,
                    "context": text[max(0, first - 75):min(len(text), last + 75)],
                }
        if occurrences:
            evidence = [occurrences[k] for k in sorted(occurrences)]
            matches.append({
                "word": item["word"],
                "forms_found": sorted({hit["form"] for hit in evidence}),
                "has_exact_form": any(hit["exact"] for hit in evidence),
                "occurrence_count": len(evidence),
                "evidence": evidence,
            })
    return {
        "schema_version": 1,
        "status": "lexical_candidates_require_context_review",
        "target_count": len(targets),
        "exact_unique_count": sum(item["has_exact_form"] for item in matches),
        "with_explicit_forms_unique_count": len(matches),
        "unmatched": [item["word"] for item in targets.values()
                      if item["word"] not in {hit["word"] for hit in matches}],
        "matches": matches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--words", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = match(json.loads(args.words.read_text(encoding="utf-8-sig")),
                       args.transcript.read_text(encoding="utf-8-sig"))
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = args.output.resolve()
            if output in {args.words.resolve(), args.transcript.resolve()}:
                raise ValueError("Output must not overwrite an input file.")
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
    except (OSError, ValueError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
