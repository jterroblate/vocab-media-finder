#!/usr/bin/env python3
"""Vocabulary video research pipeline. Exports text; never sends messages."""
import argparse
import collections
import copy
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs

from word_families import expand_targets, match_families
from video_sources import normalize_transcript, PublicCatalog, SourceClient
from search_profiles import (LEVELS, PLATFORMS, resolve_profile, classify_video_url,
                             platform_search_queries, profile_reasons)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def transcript_document(value):
    """Accept normalized documents or a successful one-video source response."""
    if "document" in value:
        value = value["document"]
    elif "documents" in value:
        documents = value["documents"]
        if len(documents) != 1:
            raise ValueError("Select exactly one transcript document before matching.")
        value = documents[0]
    if not isinstance(value, dict) or not value.get("text"):
        raise ValueError("No verified transcript body in the source response.")
    return value


def write_json(value, output=None, inputs=()):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        target = Path(output).resolve()
        if target in {Path(p).resolve() for p in inputs if p}:
            raise ValueError("Output must not overwrite an input.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def review_template(report):
    return {
        "report_id": report["report_id"],
        "video_review": {
            "english_audio_confirmed": False,
            "natural_context": False,
            "source_identity_confirmed": False,
            "subtitle_available": False,
            "full_transcript_confirmed": False,
            "part_mapping_checked": False,
            "audience_fit": "unknown",
            "difficulty_review": {"level": "unknown", "confidence": "low", "basis": "",
                                  "provider_level": "", "speech": "", "vocabulary": "",
                                  "sentence_structure": "", "visual_support": ""},
            "access_status": "unknown",
        },
        "decisions": {x["occurrence_id"]: {
            "action": "pending", "sense": "unspecified", "note": "",
            "relation_confirmed": x.get("relation_confirmed", False)
        } for x in report["occurrences"]},
    }


def review_match(report, review):
    if review.get("report_id") != report.get("report_id"):
        raise ValueError("Review belongs to a different transcript/target report.")
    result = copy.deepcopy(report)
    known = {x["occurrence_id"] for x in result["occurrences"]}
    decisions = review.get("decisions", {})
    if set(decisions) - known:
        raise ValueError("Review contains unknown occurrence IDs.")
    allowed = {"english_audio_confirmed", "natural_context", "source_identity_confirmed",
               "subtitle_available", "full_transcript_confirmed", "part_mapping_checked", "subtitle_body_checked",
               "playback_checked", "audience_fit", "review_note", "genre", "topic",
               "difficulty_review", "access_status", "access_note", "resolved_url", "link_resolved",
               "video_format_confirmed", "provider_level", "official_level_source"}
    boolean_checks = {"english_audio_confirmed", "natural_context", "source_identity_confirmed",
                      "subtitle_available", "full_transcript_confirmed", "part_mapping_checked", "subtitle_body_checked", "playback_checked", "link_resolved", "video_format_confirmed"}
    for k, v in review.get("video_review", {}).items():
        if k in boolean_checks and not isinstance(v, bool):
            raise ValueError("Video evidence flags must be true or false booleans.")
        if k == "difficulty_review" and not isinstance(v, dict):
            raise ValueError("difficulty_review must contain a level, confidence and evidence basis.")
        if k == "access_status" and v not in {"unknown", "public", "login_required", "blocked"}:
            raise ValueError("Invalid viewing access status.")
        if k in {"provider_level", "official_level_source"} and not isinstance(v, str):
            raise ValueError("Provider level and its source must be strings.")
    result["video"].update({k: v for k, v in review.get("video_review", {}).items()
                            if k in allowed})
    accepted, pending = [], 0
    for occurrence in result["occurrences"]:
        decision = decisions.get(occurrence["occurrence_id"], {"action": "pending"})
        action = decision.get("action", "pending")
        if action not in {"accept", "exclude", "uncertain", "pending"}:
            raise ValueError("Unknown review action.")
        confirmed = decision.get("relation_confirmed", occurrence.get("relation_confirmed", False))
        if not isinstance(confirmed, bool):
            raise ValueError("relation_confirmed must be a boolean.")
        if action == "accept" and confirmed is not True:
            raise ValueError("Accepting a generated relation requires relation_confirmed=true.")
        if decision.get("sense") not in {None, "same", "new", "unspecified"}:
            raise ValueError("sense must be same, new, or unspecified.")
        occurrence["review"] = decision
        if action == "accept":
            accepted.append(occurrence)
        elif action in {"pending", "uncertain"}:
            pending += 1
    spans = {}
    for x in accepted:
        span = (x["start"], x["end"])
        if span in spans and spans[span] != x["family_id"]:
            raise ValueError("One observed span has two accepted families; resolve the collision.")
        spans[span] = x["family_id"]
    unique = {(x["family_id"], x["start"], x["end"]): x for x in accepted}
    accepted = list(unique.values())
    counts = collections.Counter(x["family_id"] for x in accepted)
    direct = {x["family_id"] for x in accepted if x["kind"] in {"exact", "inflection"}}
    kinds = collections.Counter(x["kind"] for x in accepted)
    new_sense = [x for x in accepted if x["review"].get("sense") == "new"]
    result["status"] = "reviewed" if pending == 0 else "partially_reviewed"
    result["review_complete"] = pending == 0
    result["stats"] = {
        "family_coverage": len(counts), "direct_family_coverage": len(direct),
        "derived_only_family_coverage": len(set(counts) - direct),
        "total_occurrences": len(accepted), "family_counts": dict(counts),
        "form_counts": dict(collections.Counter(x["form"] for x in accepted)),
        "kind_counts": {k: kinds[k] for k in ["exact", "inflection", "derived"]},
        "repeated_family_count": sum(c >= 2 for c in counts.values()),
        "new_sense_occurrences": len(new_sense), "pending_occurrences": pending,
    }
    entry_hits = collections.defaultdict(lambda: {"exact": 0, "inflection": 0, "derived": 0})
    for x in accepted:
        for target, kind in x.get("target_relations", {}).items():
            entry_hits[target][kind] += 1
    result["source_target_hits"] = dict(entry_hits)
    return result


def video_identity(report):
    v = report["video"]
    return str(v.get("canonical_id") or v.get("original_id") or v.get("id") or
               report.get("document", {}).get("sha256") or v.get("url"))


def eligibility(report, platform="any", min_duration=180, max_duration=600, *, profile=None):
    if profile is not None:
        profile = resolve_profile(profile)
        min_duration, max_duration = profile["min_duration"], profile["max_duration"]
    v = report.get("video", {})
    reasons = []
    if report.get("status") != "reviewed" or not report.get("review_complete"):
        reasons.append("context_review_incomplete")
    if not report.get("stats", {}).get("total_occurrences"):
        reasons.append("no_confirmed_occurrences")
    for key in ["english_audio_confirmed", "natural_context", "source_identity_confirmed",
                "subtitle_available", "full_transcript_confirmed"]:
        if v.get(key) is not True:
            reasons.append(key + "_missing")
    duration = v.get("duration_seconds", v.get("duration"))
    if not isinstance(duration, (int, float)) or duration <= 0 or not min_duration <= duration <= max_duration:
        reasons.append("duration_outside_requested_range")
    if v.get("audience_fit") == "unsuitable":
        reasons.append("audience_unsuitable")
    if profile is not None:
        reasons.extend(profile_reasons(v, profile))
        if classify_video_url(v.get("url", "")) == "bilibili" and v.get("part_mapping_checked") is not True:
            reasons.append("bilibili_part_mapping_unchecked")
        return list(dict.fromkeys(reasons))
    parsed = urlparse(v.get("url", ""))
    bili = parsed.hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
    youtube = parsed.hostname in {"www.youtube.com", "youtube.com", "youtu.be"}
    yt_ids = parse_qs(parsed.query).get("v", []) if parsed.path == "/watch" else []
    yt_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else (yt_ids[0] if len(yt_ids) == 1 else "")
    specific = parsed.scheme in {"https", "http"} and ((bili and bool(re.fullmatch(
        r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed.path))) or (
        youtube and bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", yt_id))))
    if not specific:
        reasons.append("specific_video_url_required")
    if platform == "bilibili" and not bili:
        reasons.append("bilibili_entry_required")
    if bili and v.get("part_mapping_checked") is not True:
        reasons.append("bilibili_part_mapping_unchecked")
    return reasons


def select_bundle(reports, limit=8, platform="any", min_duration=180, max_duration=600, *, profile=None):
    if profile is not None:
        profile = resolve_profile(profile)
        min_duration, max_duration = profile["min_duration"], profile["max_duration"]
    scopes = {r.get("vocabulary_scope_sha256") for r in reports}
    if reports and (None in scopes or len(scopes) != 1):
        raise ValueError("Reports must use the same vocabulary scope; rematch all candidates with one family file.")
    candidates, excluded, identities = [], [], set()
    # Deduplicate versions by original identity or transcript fingerprint, not by title.
    for r in sorted(reports, key=lambda r: (
            0 if "bilibili.com/video/" in r.get("video", {}).get("url", "") else 1,
            0 if r.get("video", {}).get("subtitle_body_checked") else 1,
            str(r.get("report_id", "")))):
        reasons = eligibility(r, platform, min_duration, max_duration, profile=profile)
        identity = video_identity(r)
        fingerprint = r.get("document", {}).get("sha256")
        if identity in identities or fingerprint and fingerprint in identities:
            reasons.append("duplicate_original_or_transcript")
        if reasons:
            excluded.append({"report_id": r.get("report_id"), "reasons": reasons})
        else:
            identities.add(identity)
            if fingerprint:
                identities.add(fingerprint)
            candidates.append(r)
    selected, total = [], collections.Counter()
    while candidates and len(selected) < limit:
        def score(r):
            counts = r["stats"]["family_counts"]
            new = sum(w not in total for w in counts)
            gain = 3 * new + sum(math.log1p(total[w] + n) - math.log1p(total[w])
                                 for w, n in counts.items())
            v = r["video"]
            duration = v.get("duration_seconds", v.get("duration"))
            genre = v.get("genre")
            if selected and genre and all(x["video"].get("genre") != genre for x in selected):
                gain += 0.25
            return (gain, new, r["stats"]["repeated_family_count"],
                    r["stats"]["total_occurrences"] / duration, -duration)
        choice = max(candidates, key=score)
        value = score(choice)[0]
        candidates.remove(choice)
        selected.append({**choice, "selection_gain": round(value, 4)})
        total.update(choice["stats"]["family_counts"])
    vocabulary_scope = reports[0].get("vocabulary_scope", []) if reports else []
    return {"schema_version": 2, "status": "selected", "items": selected,
            "requested_limit": limit, "vocabulary_scope": vocabulary_scope,
            "target_count": len({t for f in vocabulary_scope for t in f["targets"]}),
            "target_family_count": len(vocabulary_scope),
            "platform": platform, "profile": profile, "family_coverage": len(total),
            "family_counts": dict(total), "total_occurrences": sum(total.values()),
            "excluded": excluded, "warnings": ([] if len(selected) >= min(6, limit) else
            ["Fewer suitable verified videos than requested; do not fill with unverified results."])}


def render_bundle(bundle):
    items = bundle.get("items", [])
    lines = [f"英语视频：{len(items)}条，覆盖{bundle['family_coverage']}个词族，"
             f"目标词及词族成员合计约{bundle['total_occurrences']}次。", ""]
    if bundle.get("profile"):
        p = bundle["profile"]
        lines.insert(0, f"观看档位：{p['label']}；网络范围：{p['network']}；平台：{', '.join(p['platforms'])}。")
    if bundle.get("vocabulary_scope"):
        lines.insert(0, f"本次范围：{bundle['target_count']}个源词条／{bundle['target_family_count']}个词族。")
        uncovered = ["／".join(f["targets"]) for f in bundle["vocabulary_scope"]
                     if f["family_id"] not in bundle.get("family_counts", {})]
        if uncovered:
            lines += ["本组未覆盖：" + "、".join(uncovered[:12]) +
                      (f"等{len(uncovered)}个词族。" if len(uncovered) > 12 else "。"), ""]
    if bundle.get("warnings"):
        lines += ["符合要求且证据充分的视频数量不足，按实际核验结果提供。"]
    if not items and bundle.get("excluded"):
        labels = {"full_transcript_confirmed_missing": "缺少全片英文文稿确认",
                  "context_review_incomplete": "命中上下文待复核",
                  "no_confirmed_occurrences": "无已确认正文命中",
                  "english_audio_confirmed_missing": "英语原声未确认",
                  "natural_context_missing": "自然语境未确认",
                  "source_identity_confirmed_missing": "原版对应未确认",
                  "subtitle_available_missing": "观看字幕可用性未确认",
                  "bilibili_part_mapping_unchecked": "B站实际分集未确认",
                  "specific_video_url_required": "缺少具体视频直达链接",
                  "bilibili_entry_required": "缺少B站观看入口",
                  "duration_outside_requested_range": "时长不符合要求",
                  "audience_unsuitable": "内容难度不适合",
                  "duplicate_original_or_transcript": "重复版本"}
        labels.update({"difficulty_review_missing": "缺少难度判断",
                       "difficulty_review_unconfirmed": "难度判断依据不足",
                       "difficulty_above_requested_level": "超出所选难度",
                       "platform_not_allowed": "不在所选平台内",
                       "network_platform_unavailable": "观看入口不符合网络范围",
                       "short_link_unresolved": "短链接未核对最终视频",
                       "video_format_unconfirmed": "未确认是视频内容",
                       "viewer_login_required": "观看需登录且未解决",
                       "viewer_access_blocked": "当前观看入口受阻"})
        reasons = collections.Counter(reason for r in bundle["excluded"] for reason in r["reasons"])
        lines += ["未入选原因：" + "；".join(f"{labels.get(reason, reason)}（{n}条）"
                                               for reason, n in reasons.most_common()), ""]
    for index, item in enumerate(items, 1):
        v, s = item["video"], item["stats"]
        seconds = int(v.get("duration_seconds", v.get("duration")))
        lines += [f"{index}. {v.get('title', '英语视频')}（{seconds//60}:{seconds%60:02d}）",
                  f"词族覆盖{s['family_coverage']}；原词／屈折直接命中{s['direct_family_coverage']}族，"
                  f"仅派生关联{s['derived_only_family_coverage']}族；合计约{s['total_occurrences']}次。"]
        difficulty = v.get("difficulty_review")
        if difficulty and difficulty.get("level") in LEVELS:
            lines.append("估计难度：" + LEVELS[difficulty["level"]]["label"] + "；依据：" + str(difficulty.get("basis", "")))
        provider_level = v.get("provider_level") or (difficulty or {}).get("provider_level")
        if provider_level:
            lines.append("来源原标级：" + str(provider_level) + "（与本工具估计分开记录）")
            if v.get("official_level_source"):
                lines.append("标级依据：" + v["official_level_source"])
        if bundle.get("profile"):
            lines.append("观看访问状态：" + v.get("access_status", "unknown") +
                         ("；" + v["access_note"] if v.get("access_note") else ""))
        categories = collections.defaultdict(collections.Counter)
        family_names = {}
        relations = collections.defaultdict(set)
        source_derivations = collections.defaultdict(collections.Counter)
        new_forms = set()
        sense_notes = collections.defaultdict(set)
        for x in item.get("occurrences", []):
            if x.get("review", {}).get("action") != "accept":
                continue
            categories[x["kind"]][x["form"]] += 1
            family_names[x["family_id"]] = "／".join(x.get("targets", []))
            if x["kind"] == "derived":
                relations[x["form"]].update(x.get("targets", []))
            for target, relation in x.get("target_relations", {}).items():
                if relation == "derived" and x["kind"] != "derived":
                    source_derivations[target][x["form"]] += 1
            if x["review"].get("sense") == "new":
                new_forms.add(x["form"])
                note = x["review"].get("note")
                if isinstance(note, str) and note.strip():
                    sense_notes[x["form"]].add(note.strip())
        lines.append("词族频次：" + "、".join(
            f"{family_names.get(w, w)}×{n}" for w, n in s["family_counts"].items()))
        for kind, label in [("exact", "原词"), ("inflection", "词形变化"), ("derived", "派生关联")]:
            if categories[kind]:
                forms = []
                for w, n in sorted(categories[kind].items()):
                    relation = "（关联" + "／".join(sorted(relations[w])) + "）" if relations[w] else ""
                    forms.append(f"{w}×{n}{relation}")
                lines.append(label + "：" + "、".join(forms))
        if source_derivations:
            lines.append("源词派生对应（已含在总数中）：" + "；".join(
                target + " → " + "、".join(f"{form}×{n}" for form, n in forms.items())
                for target, forms in source_derivations.items()))
        if new_forms:
            lines.append("含新义项：" + "、".join(
                form + ("（" + "；".join(sorted(sense_notes[form])) + "）" if sense_notes[form] else "")
                for form in sorted(new_forms)))
        if not v.get("subtitle_body_checked") or not v.get("playback_checked"):
            checks = []
            if not v.get("subtitle_body_checked"):
                checks.append("词频据对应英文文稿，观看版本字幕正文未取得")
            if not v.get("playback_checked"):
                checks.append("未检查实际播放")
            lines.append("核验范围：" + "；".join(checks) + "。")
        lines += [v["url"], ""]
    lines.append("次数按已复核英文文稿统计；部分视频需要在播放器中开启字幕。")
    return "\n".join(lines) + "\n"


def make_match(expanded, doc, video):
    matched = match_families(expanded, doc)
    digest = hashlib.sha256(json.dumps({"families": expanded, "text_sha": doc["sha256"],
                                      "video": video}, sort_keys=True).encode()).hexdigest()
    scope = hashlib.sha256(json.dumps(expanded["families"], sort_keys=True).encode()).hexdigest()
    return {**matched, "schema_version": 2, "report_id": digest, "vocabulary_scope_sha256": scope,
            "vocabulary_scope": [{"family_id": f["family_id"],
                                  "targets": list(dict.fromkeys(t["word"] for t in f["targets"]))}
                                 for f in expanded["families"]],
            "status": "requires_context_review", "video": video,
            "document": {k: doc.get(k) for k in ["source_url", "language", "is_auto", "sha256",
                         "parser_version", "completeness", "warnings"]},
            "review_complete": False}


def web_search_plan(expanded, groups=None, max_queries=8, *, profile=None, scenes=None):
    """Bounded ordinary web queries; vocabulary matching still uses full families.

    A search plan is not a web search result. The caller executes these with its
    existing search tool, inspects full transcripts, then locates viewing copies.
    """
    if not isinstance(max_queries, int) or max_queries < 0:
        raise ValueError("max_queries must be a nonnegative integer.")
    families = expanded.get("families", [])
    lookup = {str(t["word"]).strip().lower(): f for f in families for t in f["targets"]}
    if groups is None:
        ordered = [families[i:i + 2] for i in range(0, len(families), 2)]
    else:
        source_groups = groups.values() if isinstance(groups, dict) else groups
        ordered = []
        for group in source_groups:
            if not isinstance(group, (list, tuple)):
                raise ValueError("Each query group must be a list of source words.")
            selected, seen = [], set()
            for word in group:
                family = lookup.get(str(word).strip().lower())
                if family and family["family_id"] not in seen:
                    selected.append(family)
                    seen.add(family["family_id"])
            ordered.extend(selected[i:i + 2] for i in range(0, len(selected), 2))

    def search_term(value):
        # Never concatenate a whole long phrase or an entire word family into a
        # search query. Short prefixes help discovery only, not final matching.
        terms = [token for token in str(value).split()[:4] if len(token) <= 60]
        return " ".join(terms)[:80].rsplit(" ", 1)[0] if len(" ".join(terms)) > 80 else " ".join(terms)

    def terms_for(family):
        primary = search_term(family["targets"][0]["word"])
        alternatives = [search_term(m["text"]) for m in family.get("members", [])
                        if m.get("kind") == "derived"]
        alternatives += [search_term(m["text"]) for m in family.get("members", [])
                         if m.get("kind") == "inflection"]
        alternate = next((term for term in alternatives if term and term != primary), primary)
        return primary, alternate

    rounds = [[], [], []]
    for group_index, batch in enumerate(ordered):
        batch = [f for f in batch if terms_for(f)[0]]
        if not batch:
            continue
        pairs = [terms_for(f) for f in batch]
        scope = {"group_index": group_index,
                 "family_ids": [f["family_id"] for f in batch],
                 "target_words": [t["word"] for f in batch for t in f["targets"]]}
        primary = " ".join('"' + pair[0] + '"' for pair in pairs)
        alternate = " ".join('"' + pair[1] + '"' for pair in pairs)
        rounds[0].append({**scope, "kind": "cooccurrence", "query": primary + " English video transcript"})
        rounds[1].append({**scope, "kind": "broad_story", "query": pairs[0][0] + " English story transcript"})
        rounds[2].append({**scope, "kind": "variant_context" if alternate != primary else "interview_context",
                          "query": alternate + " English interview subtitles"})
    if profile is not None:
        profile = resolve_profile(profile)
        if scenes is not None and (not isinstance(scenes, list) or any(not isinstance(x, str) or not x.strip() for x in scenes)):
            raise ValueError("scenes must be a list of nonempty topic descriptions.")
        scene_names = scenes or (list(groups) if isinstance(groups, dict) else [])
        topic_round, platform_round = [], []
        hint = profile["query_hints"][0]
        for i, item in enumerate(rounds[0]):
            scene = " ".join(str(scene_names[i % len(scene_names)]).split())[:100] if scene_names else "daily life"
            topic_round.append({**item, "kind": "scene_first", "scene": scene,
                                "query": f"{scene} {hint} story complete transcript"})
            chosen_platform = profile["platforms"][i % len(profile["platforms"])]
            platform_round.append({**item, "kind": "platform_scene", "scene": scene,
                                   "platform": chosen_platform,
                                   "query": platform_search_queries(chosen_platform, scene + " " + hint)[0]})
        for item in rounds[2]:
            item["query"] = item["query"].replace("English interview subtitles", hint + " conversation subtitles")
        rounds = [topic_round, platform_round, rounds[0], rounds[2]]
    plan, seen = [], set()
    # Interleave strategies within each prioritized scene group. Exhausting all
    # cooccurrence queries first would remove broader/variant discovery from a
    # small budget whenever the input contains many groups.
    for group_items in zip(*rounds):
        for item in group_items:
            if item["query"] not in seen:
                seen.add(item["query"])
                plan.append(item)
                if len(plan) >= max_queries:
                    return plan[:max_queries]
    return plan[:max_queries]


def discover(expanded, groups=None, cache_dir=None, max_queries=8, limit=30,
             catalog_only=False, platform="any", *, profile=None, scenes=None):
    """Search the local public index and prepare a keyless web-search handoff."""
    if platform not in {"any", *PLATFORMS}:
        raise ValueError("platform must be any or bilibili.")
    catalog = PublicCatalog(cache_dir)
    try:
        local = catalog.search(expanded, limit=limit)
    finally:
        catalog.close()
    if profile is not None:
        profile = resolve_profile(profile)
        # A foreign original is still useful evidence for a domestic reupload.
        # Only the final viewing selection is restricted by viewer platforms.
        for candidate in local:
            candidate["viewing_entry_allowed"] = classify_video_url(candidate.get("url", "")) in profile["platforms"]
            candidate["viewing_copy_lookup_required"] = not candidate["viewing_entry_allowed"]
    plan = [] if catalog_only else web_search_plan(expanded, groups, max_queries, profile=profile, scenes=scenes)
    queries = [item["query"] for item in plan]
    result = {"schema_version": 2, "status": "catalog_candidates_require_verification" if catalog_only else "web_search_required",
              "mode": "keyless", "platform": platform,
              "profile": profile,
              "local_candidates": local, "remote_candidates": [], "source_results": [],
              "queries": queries, "web_search_queries": queries, "search_plan": plan,
              "api_calls": 0, "logical_search_calls": 0,
              "web_search_executed": False,
              "bilibili_lookup": {"stage": "after_original_title_and_transcript_identified",
                  "required": platform == "bilibili", "queries": [
                      'site:bilibili.com/video "{english_title}"',
                      'site:bilibili.com/video "{english_title}" "{creator}"',
                      'site:bilibili.com/video {chinese_title_or_topic} 英语 双语']},
              "next_steps": [] if catalog_only else [
                  "Execute web_search_queries with the available web-search tool; these are plans, not found videos.",
                  "Read complete English transcripts and verify actual family forms in context; do not count search snippets.",
                  "Use the observed title/creator to locate viewing copies, then verify Bilibili bvid+cid/page when required.",
                  "Continue fresh web discovery even when the public local index already has candidates."]}
    if profile is not None:
        result["platform_lookup"] = {p: platform_search_queries(p, "{observed_title_or_scene}") for p in profile["platforms"]}
        result["next_steps"] += ["Record each platform's actual search/access status; blocked or login-required is not searched successfully.",
                                  "Keep provider level separate from evidence-based difficulty_review; do not infer CEFR from short duration."]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["expand", "queries", "discover", "transcript", "match", "review", "select", "render", "bilibili", "catalog-add"]:
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path)
        if name in {"queries", "discover", "match"}:
            p.add_argument("--families", required=True, type=Path)
        if name in {"discover", "transcript", "bilibili", "catalog-add"}:
            p.add_argument("--cache-dir", type=Path)
        if name in {"queries", "discover"}:
            p.add_argument("--groups", type=Path)
            p.add_argument("--max-queries", type=int, default=8)
            p.add_argument("--platform", choices=["any", *PLATFORMS], default="any")
        if name == "expand":
            p.add_argument("--words", required=True, type=Path)
            p.add_argument("--no-resources", action="store_true")
        if name == "discover":
            p.add_argument("--limit", type=int, default=30)
            p.add_argument("--catalog-only", action="store_true")
        if name == "transcript":
            source = p.add_mutually_exclusive_group(required=True)
            source.add_argument("--input", type=Path)
            source.add_argument("--url")
            p.add_argument("--format", default="text", choices=["text", "srt", "vtt", "ted_jsonld", "filmot", "bilibili"])
            p.add_argument("--source-url", default="")
            p.add_argument("--language", default="en")
            p.add_argument("--auto", action="store_true")
        if name in {"match", "catalog-add"}:
            p.add_argument("--document", required=True, type=Path)
            p.add_argument("--video", required=True, type=Path)
        if name == "catalog-add":
            p.add_argument("--public", action="store_true", required=True,
                           help="Only use for a confirmed public video and public transcript.")
        if name == "review":
            p.add_argument("--report", required=True, type=Path)
            p.add_argument("--decisions", type=Path, help="Omit to export a pending review template.")
        if name == "select":
            p.add_argument("--reports", required=True, nargs="+", type=Path)
            p.add_argument("--limit", type=int, default=8)
            p.add_argument("--platform", choices=["any", *PLATFORMS], default="any")
        if name in {"queries", "discover", "select"}:
            p.add_argument("--profile", type=Path, help="Portable JSON profile; explicit flags override it.")
            p.add_argument("--network", choices=["domestic", "global"])
            p.add_argument("--level", "--difficulty", dest="level", choices=list(LEVELS))
            p.add_argument("--platforms", nargs="+", choices=PLATFORMS)
            p.add_argument("--min-duration", type=int)
            p.add_argument("--max-duration", type=int)
            if name in {"queries", "discover"}:
                p.add_argument("--scene", action="append", help="Topic/scene context; not a countable synonym.")
        if name == "render":
            p.add_argument("--bundle", required=True, type=Path)
            p.add_argument("--overwrite", action="store_true", help="Replace an existing TXT export.")
        if name == "bilibili":
            p.add_argument("--bvid", required=True)
            p.add_argument("--cid", type=int)
            p.add_argument("--page", type=int)
            p.add_argument("--refresh", action="store_true", help="Reconfirm the actual page before delivery.")
            p.add_argument("--inspect-subtitles", action="store_true")
            p.add_argument("--fetch-subtitles", action="store_true")
    args = parser.parse_args()
    try:
        c = args.command
        inputs = []
        for k, v in vars(args).items():
            if k == "output":
                continue
            if isinstance(v, Path):
                inputs.append(v)
            elif isinstance(v, list):
                inputs.extend(p for p in v if isinstance(p, Path))
        effective_profile = None
        if c in {"queries", "discover", "select"}:
            platforms = args.platforms if args.platforms is not None else ([args.platform] if args.platform != "any" else None)
            effective_profile = resolve_profile(read_json(args.profile) if args.profile else None,
                network=args.network, level=args.level, platforms=platforms,
                min_duration=args.min_duration, max_duration=args.max_duration)
        if c == "expand":
            value = expand_targets(read_json(args.words), use_resources=not args.no_resources)
        elif c == "queries":
            value = [item["query"] for item in web_search_plan(read_json(args.families),
                groups=read_json(args.groups) if args.groups else None, max_queries=args.max_queries,
                profile=effective_profile, scenes=args.scene)]
        elif c == "discover":
            value = discover(read_json(args.families), read_json(args.groups) if args.groups else None,
                             args.cache_dir, args.max_queries, args.limit, args.catalog_only, args.platform,
                             profile=effective_profile, scenes=args.scene)
        elif c == "transcript":
            if args.url:
                value = SourceClient(cache_dir=args.cache_dir, api_key="").fetch_ted(args.url)
            else:
                raw = args.input.read_text(encoding="utf-8-sig")
                if args.format in {"filmot", "bilibili"}:
                    raw = json.loads(raw)
                value = normalize_transcript(raw, format=args.format, source_url=args.source_url,
                                             language=args.language, is_auto=args.auto)
        elif c == "match":
            doc = transcript_document(read_json(args.document))
            value = make_match(read_json(args.families), doc, read_json(args.video))
        elif c == "review":
            report = read_json(args.report)
            value = review_match(report, read_json(args.decisions)) if args.decisions else review_template(report)
        elif c == "select":
            if args.limit < 1:
                raise ValueError("limit must be positive")
            value = select_bundle([read_json(p) for p in args.reports], args.limit, args.platform,
                                  profile=effective_profile)
        elif c == "render":
            value = render_bundle(read_json(args.bundle))
            if args.output:
                if args.output.suffix.lower() != ".txt":
                    raise ValueError("Render output must be TXT; research and sending records stay separate.")
                if args.output.exists() and not args.overwrite:
                    raise ValueError("TXT already exists; choose a new path or explicitly use --overwrite.")
                if args.output.resolve() in {p.resolve() for p in inputs}:
                    raise ValueError("Output must not overwrite an input.")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(value, encoding="utf-8")
            else:
                print(value, end="")
            return
        elif c == "bilibili":
            client = SourceClient(cache_dir=args.cache_dir, api_key="")
            value = client.resolve_bilibili(args.bvid, cid=args.cid, page=args.page, refresh=args.refresh)
            if value.get("video") and (args.inspect_subtitles or args.fetch_subtitles):
                v = value["video"]
                value["subtitle_check"] = client.inspect_bilibili_subtitles(
                    args.bvid, v["cid"], aid=v.get("aid"), fetch_body=args.fetch_subtitles)
        elif c == "catalog-add":
            PublicCatalog(args.cache_dir).put_video(read_json(args.video), transcript_document(read_json(args.document)))
            value = {"status": "public_video_indexed"}
        write_json(value, args.output, inputs)
    except (OSError, ValueError, KeyError, TypeError) as e:
        parser.exit(2, f"Error: {e}\n")


if __name__ == "__main__":
    main()
