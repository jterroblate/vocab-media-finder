#!/usr/bin/env python3
"""Vocabulary-family candidates, bounded subtitle queries and strict occurrences.

Resource expansion is a proposal, not verification. This module never downloads
resources or treats a dictionary relationship as proof of an utterance's sense.
"""

import copy
import hashlib
import importlib
import math
import sys
from collections import defaultdict
from pathlib import Path

from match_vocab import key, match, normalize, surface
from runtime_paths import cache_root


KIND_ORDER = {"exact": 0, "inflection": 1, "derived": 2}
_POS = {"n": "NOUN", "noun": "NOUN", "v": "VERB", "verb": "VERB",
        "a": "ADJ", "adj": "ADJ", "adjective": "ADJ", "adv": "ADV",
        "adverb": "ADV"}


def _canonical(value):
    surface(value)
    return normalize(value.strip())


def _resource_environment():
    """Use the shared isolated runtime when installed; leave code self-contained."""
    cache = cache_root()
    packages = cache / "python" / f"py{sys.version_info.major}{sys.version_info.minor}"
    if packages.is_dir() and str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    return cache


def _resources(warnings):
    cache = _resource_environment()
    inflector = wordnet = None
    try:
        inflector = importlib.import_module("lemminflect")
    except (ImportError, OSError) as exc:
        warnings.append("LemmInflect unavailable; using explicit inflections only ("
                        + type(exc).__name__ + ").")
    try:
        nltk = importlib.import_module("nltk")
        data_dir = str(cache / "nltk_data")
        if data_dir not in nltk.data.path:
            nltk.data.path.insert(0, data_dir)
        wordnet = importlib.import_module("nltk.corpus").wordnet
        wordnet.ensure_loaded()
    except (ImportError, LookupError, OSError) as exc:
        warnings.append("WordNet unavailable; using explicit derivatives only ("
                        + type(exc).__name__ + ").")
        wordnet = None
    return inflector, wordnet


def _add(members, text, kind, source, confirmed, relation=None):
    text = _canonical(text)
    token_key = key(text)
    old = members.get(token_key)
    member = {"text": text, "kind": kind, "source": source,
              "confirmed": bool(confirmed)}
    if relation:
        member["relation"] = relation
    if old is None:
        members[token_key] = member
        return
    # One physical form per family. Source targets outrank their own derivations.
    if KIND_ORDER[kind] < KIND_ORDER[old["kind"]]:
        member["confirmed"] |= old["confirmed"]
        members[token_key] = member
    elif KIND_ORDER[kind] == KIND_ORDER[old["kind"]]:
        if confirmed and not old["confirmed"]:
            members[token_key] = member
        else:
            old["confirmed"] |= bool(confirmed)


def _dictionary_bases(inflector, lemma, upos=None):
    """Recover dictionary bases even when the supplied target is a participle."""
    bases = {lemma}
    if inflector is None or len(key(lemma)) != 1:
        return bases
    lemmatize = getattr(inflector, "getAllLemmas", None)
    if lemmatize is not None:
        lemmas = lemmatize(lemma, upos=upos)
        for variants in lemmas.values():
            bases.update(variants)
    return bases


def _dictionary_inflections(inflector, lemma, upos=None):
    """Dictionary lookup only; deliberately never call OOV/rule generation."""
    if inflector is None or len(key(lemma)) != 1:
        return []
    forms = set()
    for base in _dictionary_bases(inflector, lemma, upos):
        result = inflector.getAllInflections(base, upos=upos)
        if result:
            forms.add(base)
        forms.update(form for variants in result.values() for form in variants)
    return sorted(forms)


def expand_targets(entries, use_resources=True):
    """Expand legacy targets into reviewable families without losing manual items.

    ``related`` accepts a string or an object with word, forms, confirmed and
    relation. Explicit relationships default to confirmed. Automatic proposals
    never merge two source families; only explicit confirmed shared forms do.
    """
    if not isinstance(entries, list) or not entries:
        raise ValueError("Vocabulary must be a nonempty list.")
    warnings = []
    inflector, wordnet = _resources(warnings) if use_resources else (None, None)
    nodes = {}
    for raw in entries:
        item = {"word": raw} if isinstance(raw, str) else copy.deepcopy(raw)
        if not isinstance(item, dict) or "word" not in item:
            raise ValueError("Each target must be a string or an object with word.")
        word = _canonical(item["word"])
        token_key = key(word)
        forms = item.get("forms", [])
        related = item.get("related", [])
        if not isinstance(forms, list) or not isinstance(related, list):
            raise ValueError("forms and related must be lists.")
        node = nodes.setdefault(token_key, {"targets": [], "members": {},
                                            "confirmed_explicit": set(),
                                            "inflections": set(),
                                            "resource_seeds": []})
        node["targets"].append(item)
        _add(node["members"], word, "exact", "explicit", True)
        node["confirmed_explicit"].add(token_key)
        node["inflections"].add(word)
        for form in forms:
            _add(node["members"], form, "inflection", "explicit", True)
            node["confirmed_explicit"].add(key(form))
            node["inflections"].add(_canonical(form))
        upos = _POS.get(str(item.get("pos", "")).lower().rstrip("."))
        node["resource_seeds"].append((word, "inflection", upos))
        for relation in related:
            relation = {"word": relation} if isinstance(relation, str) else relation
            if not isinstance(relation, dict) or "word" not in relation:
                raise ValueError("A related form must be a string or contain word.")
            derivatives = relation.get("forms", [])
            if not isinstance(derivatives, list):
                raise ValueError("Related forms must be a list.")
            confirmed = relation.get("confirmed", True)
            if not isinstance(confirmed, bool):
                raise ValueError("Relationship confirmed must be boolean.")
            label = relation.get("relation", "derivational")
            for form in [relation["word"], *derivatives]:
                _add(node["members"], form, "derived", "explicit", confirmed, label)
                if confirmed:
                    node["confirmed_explicit"].add(key(form))
            node["resource_seeds"].append((_canonical(relation["word"]),
                                            "derived", None))

    # Explicit overlap is a positive user-provided link. Automatic overlap is not.
    parents = {k: k for k in nodes}

    def find(k):
        while parents[k] != k:
            parents[k] = parents[parents[k]]
            k = parents[k]
        return k

    owner = {}
    for node_key, node in nodes.items():
        for form in node["confirmed_explicit"]:
            if form in owner:
                a, b = find(node_key), find(owner[form])
                parents[max(a, b)] = min(a, b)
            else:
                owner[form] = node_key

    if use_resources:
        for node in nodes.values():
            # At most one derivational edge; do not walk semantic/synonym closure.
            seeds = list(node["resource_seeds"])
            for lemma, kind, upos in seeds:
                try:
                    for form in _dictionary_inflections(inflector, lemma, upos):
                        _add(node["members"], form, kind, "lemminflect", False)
                        if kind == "inflection":
                            node["inflections"].add(_canonical(form))
                except (ValueError, LookupError, OSError) as exc:
                    warnings.append("Inflection lookup unavailable for a target ("
                                    + type(exc).__name__ + ").")
            if wordnet is not None:
                for lemma, kind, upos in seeds:
                    if kind != "inflection" or len(key(lemma)) != 1:
                        continue
                    try:
                        wn_pos = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "r"}.get(upos)
                        lexical_lemmas = []
                        for base in sorted(_dictionary_bases(inflector, lemma, upos)):
                            lexical_lemmas.extend(wordnet.lemmas(base, pos=wn_pos))
                        for wn_lemma in lexical_lemmas:
                            # Pertainyms such as verbal -> word express semantic
                            # relatedness, not a countable English derivation.
                            relations = wn_lemma.derivationally_related_forms()
                            for relative in relations:
                                derived = relative.name().replace("_", " ")
                                try:
                                    _add(node["members"], derived, "derived",
                                         "wordnet", False, "lexical_derivation")
                                    pos = _POS.get(relative.synset().pos())
                                    for form in _dictionary_inflections(inflector, derived, pos):
                                        _add(node["members"], form, "derived",
                                             "wordnet+lemminflect", False,
                                             "lexical_derivation")
                                except ValueError:
                                    # Lexicons can contain non-English punctuation.
                                    continue
                    except (LookupError, OSError) as exc:
                        warnings.append("Derivation lookup unavailable for a target ("
                                        + type(exc).__name__ + ").")

    merged = {}
    for node_key, node in nodes.items():
        out = merged.setdefault(find(node_key), {"targets": [], "members": {},
                                                 "target_forms": {}})
        out["targets"].extend(node["targets"])
        for target in node["targets"]:
            out["target_forms"][target["word"]] = sorted(node["inflections"])
        for member in node["members"].values():
            _add(out["members"], **{k: member[k] for k in
                 ["text", "kind", "source", "confirmed"]},
                 relation=member.get("relation"))
    families = []
    for family in merged.values():
        signature = "|".join(sorted({" ".join(key(t["word"]))
                                     for t in family["targets"]}))
        family_id = "family_" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        families.append({"family_id": family_id, "targets": family["targets"],
                         "target_forms": family["target_forms"],
                         "members": sorted(family["members"].values(),
                                           key=lambda x: (KIND_ORDER[x["kind"]], x["text"]))})
    return {"schema_version": 2, "families": families,
            "warnings": list(dict.fromkeys(warnings))}


def plan_queries(expanded, groups=None, max_queries=8):
    """Build bounded query batches; input/group order supplies search priority.

    Each query has at most three families. Budget-limited output is not a promise
    that every target has been searched; callers can pass remaining groups later.
    """
    if not isinstance(max_queries, int) or max_queries < 0:
        raise ValueError("max_queries must be a nonnegative integer.")
    families = expanded.get("families", [])
    lookup = {key(t["word"]): f for f in families for t in f["targets"]}
    if groups is None:
        batches = [families[i:i + 2] for i in range(0, len(families), 2)]
    else:
        groups = groups.values() if isinstance(groups, dict) else groups
        batches = []
        for group in groups:
            if not isinstance(group, (list, tuple)):
                raise ValueError("Each query group must be a list of source words.")
            seen = set()
            found = []
            for word in group:
                family = lookup.get(key(word))
                if family and family["family_id"] not in seen:
                    seen.add(family["family_id"])
                    found.append(family)
            batches.extend(found[i:i + 3] for i in range(0, len(found), 3))
    queries = []
    for batch in batches:
        blocks = []
        for family in batch:
            terms = []
            for member in family["members"]:
                term = member["text"]
                term = '"' + term + '"' if " " in term or "-" in term else term
                if term not in terms:
                    terms.append(term)
            if terms:
                blocks.append("(" + " | ".join(terms) + ")")
        query = " ".join(blocks)
        if query and query not in queries:
            queries.append(query)
    return queries[:max_queries]


def _time_span(document, start, end):
    related = []
    for cue in document.get("cues", []):
        left, right = cue.get("char_start"), cue.get("char_end")
        if not isinstance(left, int) or not isinstance(right, int):
            continue
        # Strict match offsets refer to NFKC-normalized text.
        text = document["text"]
        left = len(normalize(text[:left]))
        right = len(normalize(text[:right]))
        if left < end and start < right:
            related.append(cue)
    if not related:
        return None, None
    def valid(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    starts = [c.get("start") for c in related if valid(c.get("start"))]
    # Missing ends remain unknown; never infer one from the next cue.
    ends = [c.get("end") for c in related if valid(c.get("end"))]
    return (min(starts) if len(starts) == len(related) else None,
            max(ends) if len(ends) == len(related) else None)


def match_families(expanded, transcript_document):
    """Return unreviewed lexical occurrences, retaining collisions explicitly."""
    text = transcript_document.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Transcript document must contain nonempty text.")
    fingerprint = hashlib.sha256(normalize(text).encode()).hexdigest()
    occurrences = []
    for family in expanded.get("families", []):
        if not family.get("members"):
            continue
        members = {key(m["text"]): m for m in family["members"]}
        primary = family["targets"][0]["word"]
        result = match([{"word": primary,
                         "forms": [m["text"] for m in family["members"]]}], text)
        for entry in result["matches"]:
            for evidence in entry["evidence"]:
                member = members[key(evidence["form"])]
                start, end = evidence["start"], evidence["end"]
                span_seed = f"{fingerprint}:{start}:{end}"
                seed = family["family_id"] + ":" + span_seed
                time_start, time_end = _time_span(transcript_document, start, end)
                target_relations = {}
                observed = key(evidence["form"])
                for target in family["targets"]:
                    word = target["word"]
                    direct_forms = family.get("target_forms", {}).get(word,
                                  [word, *target.get("forms", [])])
                    target_relations[word] = (
                        "exact" if observed == key(word)
                        else "inflection" if observed in {key(f) for f in direct_forms}
                        else "derived")
                occurrences.append({
                    "occurrence_id": "occ_" + hashlib.sha256(seed.encode()).hexdigest()[:20],
                    "span_id": "span_" + hashlib.sha256(span_seed.encode()).hexdigest()[:20],
                    "family_id": family["family_id"],
                    "targets": list(dict.fromkeys(t["word"] for t in family["targets"])),
                    "target_relations": target_relations,
                    "form": evidence["form"], "kind": member["kind"],
                    "start": start, "end": end, "context": evidence["context"],
                    "time_start": time_start, "time_end": time_end,
                    "time_precision": "cue" if time_start is not None else None,
                    "relation_confirmed": member["confirmed"],
                })
    spans = defaultdict(set)
    for occurrence in occurrences:
        spans[occurrence["span_id"]].add(occurrence["family_id"])
    for occurrence in occurrences:
        collision = spans[occurrence["span_id"]]
        occurrence["collision"] = len(collision) > 1
        occurrence["collision_family_ids"] = sorted(collision) if len(collision) > 1 else []
    occurrences.sort(key=lambda x: (x["start"], x["end"], x["family_id"]))
    return {"schema_version": 2, "status": "family_candidates_require_context_review",
            "transcript_sha256": fingerprint,
            "offset_basis": "NFKC_lowercase_transcript",
            "occurrences": occurrences,
            "candidate_family_count": len({o["family_id"] for o in occurrences}),
            "candidate_unique_occurrence_count": len(spans)}
