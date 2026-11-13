#!/usr/bin/env python3
"""Keyless search profiles and conservative viewing-entry checks.

Difficulty labels are teaching estimates, not CEFR certification. This module
does not detect a VPN, request credentials, resolve links or make HTTP requests.
"""
import math
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse


PLATFORMS = ("bilibili", "douyin", "xiaohongshu", "youtube", "other")
NETWORK_PLATFORMS = {
    "domestic": ("bilibili", "douyin", "xiaohongshu"),
    "global": PLATFORMS,
}
LEVELS = {
    "starter": {
        "label": "启蒙", "cefr_hint": "Pre-A1–A1；教学估计，非认证",
        "min_duration": 20, "max_duration": 180,
        "query_hints": ["very easy English", "simple daily actions", "repetitive short story"],
    },
    "easy": {
        "label": "入门", "cefr_hint": "A1–A2；教学估计，非认证",
        "min_duration": 60, "max_duration": 360,
        "query_hints": ["A1 A2 English", "slow daily conversation", "graded short story"],
    },
    "standard": {
        "label": "日常进阶", "cefr_hint": "A2–B1；教学估计，非认证",
        "min_duration": 120, "max_duration": 600,
        "query_hints": ["everyday English", "short interview", "clear natural conversation"],
    },
    "advanced": {
        "label": "挑战", "cefr_hint": "B2及以上；教学估计，非认证",
        "min_duration": 180, "max_duration": 900,
        "query_hints": ["English documentary", "science talk", "natural interview"],
    },
    "any": {
        "label": "不限难度", "cefr_hint": "不限制难度；非认证分级",
        "min_duration": 20, "max_duration": 900,
        "query_hints": ["English video", "complete transcript", "natural conversation"],
    },
}
LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS) if name != "any"}


def _number(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name + " must be a finite number of seconds.")
    return value


def resolve_profile(config=None, *, network=None, level=None, platforms=None,
                    min_duration=None, max_duration=None):
    """Resolve explicit overrides, then config, then domestic/easy defaults.

    A resolved profile can be supplied again as ``config``. Computed display
    fields are always regenerated rather than accepted as difficulty evidence.
    """
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise ValueError("Profile configuration must be an object.")
    unknown = set(config) - {"network", "level", "platforms", "min_duration", "max_duration",
                             "label", "cefr_hint", "query_hints", "access_notes"}
    if unknown:
        raise ValueError("Unknown profile fields: " + ", ".join(sorted(map(str, unknown))))
    network = network if network is not None else config.get("network", "domestic")
    level = level if level is not None else config.get("level", "easy")
    if not isinstance(network, str) or network not in NETWORK_PLATFORMS:
        raise ValueError("network must be domestic or global.")
    if not isinstance(level, str) or level not in LEVELS:
        raise ValueError("level must be starter, easy, standard, advanced, or any.")
    requested = platforms if platforms is not None else config.get("platforms", NETWORK_PLATFORMS[network])
    if not isinstance(requested, (list, tuple)) or not requested:
        raise ValueError("platforms must be a nonempty array.")
    if any(not isinstance(p, str) or p not in PLATFORMS for p in requested):
        raise ValueError("platforms contains an unsupported platform.")
    requested = list(dict.fromkeys(requested))
    if any(p not in NETWORK_PLATFORMS[network] for p in requested):
        raise ValueError("The domestic network profile cannot require YouTube or other overseas viewing entries.")
    defaults = LEVELS[level]
    minimum = min_duration if min_duration is not None else config.get("min_duration", defaults["min_duration"])
    maximum = max_duration if max_duration is not None else config.get("max_duration", defaults["max_duration"])
    _number(minimum, "min_duration")
    _number(maximum, "max_duration")
    if not 0 <= minimum <= maximum <= 86400 or maximum <= 0:
        raise ValueError("Require 0 <= min_duration <= max_duration <= 86400, with max_duration > 0.")
    notes = config.get("access_notes", "")
    if not isinstance(notes, str):
        raise ValueError("access_notes must be a string.")
    return {"network": network, "level": level, "platforms": requested,
            "min_duration": minimum, "max_duration": maximum,
            "label": defaults["label"], "cefr_hint": defaults["cefr_hint"],
            "query_hints": list(defaults["query_hints"]), "access_notes": notes}


def _parsed_url(url):
    if not isinstance(url, str) or not url or re.search(r"\s", url):
        return None
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.port not in {None, 80, 443}:
            return None
        return parsed
    except ValueError:
        return None


def _short_url(url):
    parsed = _parsed_url(url)
    return bool(parsed and parsed.hostname.lower().rstrip(".") in {"v.douyin.com", "xhslink.com", "www.xhslink.com"})


def classify_video_url(url):
    """Recognize an entry's structure; this does not prove existence or playback.

    Short links retain their platform label, but ``profile_reasons`` requires a
    verified final content URL. XHS content may be a text note and needs a video
    format check independently of its URL.
    """
    parsed = _parsed_url(url)
    if parsed is None:
        return None
    host, path = parsed.hostname.lower().rstrip("."), parsed.path
    query = parse_qs(parsed.query)
    if host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        return "bilibili" if re.fullmatch(r"/video/(?:BV[A-Za-z0-9]{10}|av[0-9]+)/?", path) else None
    if host in {"douyin.com", "www.douyin.com"}:
        return "douyin" if re.fullmatch(r"/video/[0-9]+/?", path) else None
    if host == "v.douyin.com":
        return "douyin" if re.fullmatch(r"/[A-Za-z0-9_-]+/?", path) else None
    if host in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        return "xiaohongshu" if re.fullmatch(r"/(?:explore|discovery/item)/[A-Fa-f0-9]{24}/?", path) else None
    if host in {"xhslink.com", "www.xhslink.com"}:
        return "xiaohongshu" if re.fullmatch(r"/(?:[A-Za-z0-9_-]+/){0,2}[A-Za-z0-9_-]+/?", path) else None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        if host in {"youtu.be", "www.youtu.be"}:
            identifier = path.strip("/") if path.count("/") <= 2 else ""
        elif path == "/watch":
            values = query.get("v", [])
            identifier = values[0] if len(values) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})/?", path)
            identifier = match[1] if match else ""
        return "youtube" if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier) else None
    # Unknown sites are only possible video entries after a separate format
    # confirmation. Known listings and non-video resources are not candidates.
    if path in {"", "/"} or re.search(r"/(?:search|results|channels?|users?|playlists?|tags?|categories)(?:/|$)", path, re.I):
        return None
    if set(query) & {"q", "query", "keyword", "search", "search_query"}:
        return None
    if re.search(r"\.(?:pdf|txt|json|csv|zip|jpe?g|png|webp|gif|mp3|wav)/?$", path, re.I):
        return None
    return "other"


def platform_search_queries(platform, term):
    """Ordinary public-web queries, never platform credentials or API calls."""
    if platform not in PLATFORMS:
        raise ValueError("Unsupported search platform.")
    if not isinstance(term, str) or not term.strip():
        raise ValueError("Search term must be a nonempty string.")
    term = " ".join(term.split())[:160]
    templates = {
        "bilibili": ["site:bilibili.com/video {term} 英语 字幕"],
        "douyin": ["site:douyin.com/video {term} 英语 情景", "site:v.douyin.com {term} 英语"],
        "xiaohongshu": ["site:xiaohongshu.com/explore {term} 英语 视频", "site:xiaohongshu.com/discovery/item {term} 英语"],
        "youtube": ["site:youtube.com/watch {term} English subtitles", "site:youtu.be {term} English"],
        "other": ["{term} English video transcript official"],
    }
    return [template.format(term=term) for template in templates[platform]]


def profile_reasons(video, profile):
    """Return unmet viewing/profile requirements without claiming public access."""
    if not isinstance(video, Mapping):
        raise ValueError("Video metadata must be an object.")
    profile = resolve_profile(profile)
    reasons = []
    url = video.get("url", "")
    platform = classify_video_url(url)
    if platform is None:
        reasons.append("specific_video_url_required")
    elif platform not in profile["platforms"]:
        reasons.append("platform_not_allowed")
    if platform and platform not in NETWORK_PLATFORMS[profile["network"]]:
        reasons.append("network_platform_unavailable")
    resolved = video.get("resolved_url")
    if _short_url(url):
        if video.get("link_resolved") is not True or not resolved or _short_url(resolved) or classify_video_url(resolved) != platform:
            reasons.append("short_link_unresolved")
    elif video.get("link_resolved") is True and resolved and classify_video_url(resolved) != platform:
        reasons.append("resolved_link_platform_mismatch")
    if platform in {"xiaohongshu", "other"} and video.get("video_format_confirmed") is not True:
        reasons.append("video_format_unconfirmed")
    duration = video.get("duration_seconds", video.get("duration"))
    try:
        duration = _number(duration, "duration")
        if duration <= 0 or not profile["min_duration"] <= duration <= profile["max_duration"]:
            reasons.append("duration_outside_requested_range")
    except ValueError:
        reasons.append("duration_outside_requested_range")
    level = profile["level"]
    if level != "any":
        review = video.get("difficulty_review")
        if not isinstance(review, Mapping) or review.get("level") not in LEVEL_ORDER:
            reasons.append("difficulty_review_missing")
        else:
            basis, confidence = review.get("basis"), review.get("confidence")
            if not isinstance(basis, str) or not basis.strip() or confidence not in {"medium", "high"}:
                reasons.append("difficulty_review_unconfirmed")
            if LEVEL_ORDER[review["level"]] > LEVEL_ORDER[level]:
                reasons.append("difficulty_above_requested_level")
    # Subtitle login state belongs in a separate evidence field. Only an
    # observed restriction on the viewing entry itself excludes the entry here.
    if video.get("access_status") == "login_required":
        reasons.append("viewer_login_required")
    elif video.get("access_status") == "blocked":
        reasons.append("viewer_access_blocked")
    return list(dict.fromkeys(reasons))
