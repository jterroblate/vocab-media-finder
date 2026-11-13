#!/usr/bin/env python3
"""Public video evidence adapters. No media downloads or student data persistence."""

import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from runtime_paths import cache_root

PARSER_VERSION = "2.0.0"
TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)*")
TIMING = re.compile(r"(?P<a>(?:\d+:)?\d{2}:\d{2}[.,]\d+)\s*-->\s*(?P<b>(?:\d+:)?\d{2}:\d{2}[.,]\d+)")
LABEL = re.compile(r"\[(?:music|applause|laughter|silence|inaudible|音楽|音乐|掌声)(?:[^\]]*)\]|#(?:Music|Laughter|Applause)#", re.I)
PUBLIC_FIELDS = {
    "id", "canonical_id", "platform", "url", "title", "english_title", "duration_seconds",
    "duration", "language", "creator", "channelid", "channelname", "category", "topic",
    "original_url", "original_id", "bvid", "cid", "page", "part", "source_url",
    "transcript_sha256", "parser_version", "is_auto", "last_checked", "status", "provider_level", "official_level_source",
}


class _SpokenHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "header", "footer", "aside", "head"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "header", "footer", "aside", "head"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _clean(text):
    text = html.unescape(str(text)).replace("\ufeff", "")
    text = re.sub(r"<(?:\d+:)?\d{2}:\d{2}[.,]\d+>", "", text)
    # Strip VTT voice/class tags and ordinary markup, but never parse page HTML as captions.
    parser = _SpokenHTML()
    parser.feed(text)
    text = "".join(parser.parts)
    text = LABEL.sub(" ", text)
    text = re.sub(r"^\s*>>\s*", "", text)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).replace("’", "'")).strip()


def _seconds(value):
    values = value.replace(",", ".").split(":")
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(values)))


def _timed_cues(raw):
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    cues = []
    index = 0
    while index < len(lines):
        timing = TIMING.search(lines[index])
        if not timing:
            index += 1
            continue
        start, end = _seconds(timing["a"]), _seconds(timing["b"])
        if end < start:
            raise ValueError("Subtitle cue ends before its start.")
        index += 1
        body = []
        while index < len(lines) and lines[index].strip() and not TIMING.search(lines[index]):
            body.append(lines[index])
            index += 1
        cues.append({"start": start, "end": end, "text": _clean(" ".join(body))})
    if not cues:
        raise ValueError("No timed subtitle cues were found.")
    return cues


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _ted_object(raw, source_url, video_id):
    if isinstance(raw, str):
        blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, re.S | re.I)
        roots = []
        for block in blocks:
            try:
                roots.append(json.loads(block))
            except ValueError:
                continue
        if not roots:
            try:
                roots = [json.loads(raw)]
            except ValueError as exc:
                raise ValueError("No usable TED JSON-LD was found.") from exc
    else:
        roots = [raw]
    candidates = [o for root in roots for o in _objects(root)
                  if isinstance(o.get("transcript"), str) and o["transcript"].strip()
                  and "VideoObject" in ([o.get("@type")] if isinstance(o.get("@type"), str) else o.get("@type", []))]
    if not candidates:
        raise ValueError("No VideoObject with a spoken transcript was found.")
    identities = {_canonical_url(source_url)} if source_url else set()
    if video_id:
        identities.add(str(video_id))
    selected = []
    for obj in candidates:
        refs = []
        for key in ("url", "@id", "mainEntityOfPage", "embedUrl", "contentUrl"):
            value = obj.get(key)
            if isinstance(value, dict):
                value = value.get("@id") or value.get("url")
            if isinstance(value, str):
                refs.extend((value, _canonical_url(value)))
        if identities and any(ref in identities for ref in refs):
            selected.append(obj)
    # An identity-less object is insufficient: a page can contain a recommended video.
    if len(selected) != 1:
        raise ValueError("TED video identity is missing, ambiguous, or does not match the requested URL.")
    return selected[0]


def _json_value(raw):
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def _filmmot_track(raw, video_id):
    value = _json_value(raw)
    if isinstance(value, dict) and "transcript" in value:
        return value
    if isinstance(value, list) and (not value or "t" in value[0]):
        return {"lang": "en", "isauto": False, "transcript": value}
    videos = value if isinstance(value, list) else [value]
    if video_id is not None:
        videos = [v for v in videos if str(v.get("id")) == str(video_id)]
    if len(videos) != 1:
        raise ValueError("Select exactly one Filmot video identity before normalizing.")
    tracks = [v for v in videos[0].get("subtitles", []) if re.match(r"^en(?:[-_]|$)", v.get("lang", ""))]
    if not tracks:
        raise ValueError("No English Filmot subtitle track is available.")
    return sorted(tracks, key=lambda v: bool(v.get("isauto")))[0]


def normalize_transcript(raw, format="text", source_url="", language="en", is_auto=False, video_id=None):
    """Normalize only the spoken body, retaining supplied cue times and character offsets.

    Filmot's getvideos supplies cue starts but not ends; missing ends remain None.
    Completeness is a parser diagnostic, never a guarantee of an uncut repost.
    """
    warnings = []
    cues = []
    if format in {"srt", "vtt"}:
        cues = _timed_cues(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    elif format == "ted_jsonld":
        obj = _ted_object(raw, source_url, video_id)
        text = _clean(obj["transcript"])
        if isinstance(obj.get("inLanguage"), str):
            language = obj["inLanguage"]
    elif format == "filmot":
        track = _filmmot_track(raw, video_id)
        language, is_auto = track.get("lang", language), bool(track.get("isauto", is_auto))
        for row in track.get("transcript", []):
            cues.append({"start": float(row["s"]), "end": float(row["end"]) if row.get("end") is not None else None,
                         "text": _clean(row["t"])})
        warnings.append("Filmot cue end times are unavailable unless explicitly supplied; no end times were estimated.")
    elif format == "bilibili":
        value = _json_value(raw)
        rows = value.get("body", []) if isinstance(value, dict) else value
        cues = [{"start": float(c["from"]), "end": float(c["to"]), "text": _clean(c["content"])} for c in rows]
    elif format == "text":
        text = _clean(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    else:
        raise ValueError("Unsupported transcript format: " + str(format))
    if format in {"srt", "vtt", "filmot", "bilibili"}:
        normalized = []
        previous = None
        for cue in cues:
            if not math.isfinite(cue["start"]) or cue["start"] < 0 or cue["end"] is not None and (not math.isfinite(cue["end"]) or cue["end"] < cue["start"]):
                raise ValueError("Subtitle cue contains invalid timing.")
            if previous and cue["start"] < previous["start"]:
                raise ValueError("Subtitle cue start times are not monotonic.")
            current_text = cue["text"]
            if previous and current_text:
                prev_tokens = list(TOKEN.finditer(previous["text"].lower()))
                curr_tokens = list(TOKEN.finditer(current_text.lower()))
                overlap = 0
                for width in range(min(len(prev_tokens), len(curr_tokens)), 0, -1):
                    if [t.group() for t in prev_tokens[-width:]] == [t.group() for t in curr_tokens[:width]]:
                        overlap = width
                        break
                if overlap:
                    if previous["end"] is not None and cue["start"] < previous["end"]:
                        current_text = current_text[curr_tokens[overlap - 1].end():].lstrip(" ,.;:!?-")
                    elif previous["end"] is None:
                        warnings.append("Adjacent cues may contain rolling overlap, but missing end times prevent safe automatic deduplication.")
            previous = cue
            if current_text:
                normalized.append({**cue, "text": current_text})
        cues = normalized
        offset = 0
        for cue in cues:
            cue["char_start"] = offset
            cue["char_end"] = offset + len(cue["text"])
            offset = cue["char_end"] + 1
        text = " ".join(c["text"] for c in cues)
    if not text.strip():
        raise ValueError("No spoken transcript body remains after cleaning.")
    return {"text": text, "cues": cues, "source_url": source_url, "language": language,
            "is_auto": bool(is_auto), "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "parser_version": PARSER_VERSION, "video_id": video_id,
            "completeness": {"body_parsed": True, "cue_count": len(cues), "last_cue_retained": True,
                             "end_times_complete": bool(cues) and all(c["end"] is not None for c in cues),
                             "full_video_verified": False}, "warnings": list(dict.fromkeys(warnings))}


def _cache_directory(cache_dir):
    return cache_root(cache_dir)


def _safe_public_url(value):
    parsed = urllib.parse.urlsplit(str(value))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Catalog URLs must be public http(s) URLs without credentials.")
    allowed = {"v", "p", "t", "view", "language", "lang"}
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k in allowed])
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


class PublicCatalog:
    """Unordered public word-frequency index. Student-specific fields are never saved."""
    def __init__(self, cache_dir=None):
        self.cache_dir = _cache_directory(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.cache_dir / "catalog.sqlite3", timeout=15)
        self.db.execute("CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS terms (video_id TEXT, term TEXT, frequency INTEGER, PRIMARY KEY(video_id,term))")
        self.db.execute("CREATE INDEX IF NOT EXISTS terms_by_word ON terms(term)")
        self.db.commit()

    def put_video(self, video, doc=None):
        public = {k: v for k, v in video.items() if k in PUBLIC_FIELDS and isinstance(v, (str, int, float, bool, list, type(None)))}
        for name in ("url", "original_url", "source_url"):
            if public.get(name):
                public[name] = _safe_public_url(public[name])
        public.setdefault("last_checked", time.time())
        if doc is not None:
            public.update(transcript_sha256=doc["sha256"], parser_version=doc.get("parser_version", PARSER_VERSION),
                          language=doc.get("language", "en"), is_auto=bool(doc.get("is_auto")))
            if doc.get("source_url"):
                public["source_url"] = _safe_public_url(doc["source_url"])
        identity = public.get("canonical_id") or public.get("id")
        if not identity:
            if public.get("bvid") and public.get("cid") is not None:
                identity = f"bilibili:{public['bvid']}:{public['cid']}"
            elif public.get("url"):
                identity = public["url"]
            else:
                raise ValueError("A public video identity is required.")
        public["id"] = str(identity)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO videos VALUES (?,?)", (str(identity), json.dumps(public, ensure_ascii=False)))
            if doc is not None:
                from collections import Counter
                counts = Counter(TOKEN.findall(unicodedata.normalize("NFKC", doc["text"]).lower().replace("’", "'")))
                self.db.execute("DELETE FROM terms WHERE video_id=?", (str(identity),))
                self.db.executemany("INSERT INTO terms VALUES (?,?,?)", ((str(identity), term, n) for term, n in counts.items()))
        return public

    def search(self, expanded, limit=30):
        families = expanded.get("families", [])
        family_terms = [{t for m in f.get("members", []) for t in TOKEN.findall(str(m.get("text", "")).lower())}
                        for f in families]
        terms = sorted(set().union(*family_terms)) if family_terms else []
        if not terms or limit < 1:
            return []
        matches = {}
        for first in range(0, len(terms), 500):
            chunk = terms[first:first + 500]
            for vid, term, n in self.db.execute("SELECT video_id,term,frequency FROM terms WHERE term IN (" + ",".join("?" for _ in chunk) + ")", chunk):
                matches.setdefault(vid, {})[term] = n
        results = []
        for vid, hits in matches.items():
            row = self.db.execute("SELECT metadata FROM videos WHERE id=?", (vid,)).fetchone()
            if row:
                metadata = json.loads(row[0])
                family_hits = [sum(hits.get(t, 0) for t in ts) for ts in family_terms]
                metadata["retrieval_score"] = sum(1 + math.log1p(min(n, 5)) for n in family_hits if n)
                metadata["retrieval_only"] = True
                results.append(metadata)
        return sorted(results, key=lambda v: (-v["retrieval_score"], str(v["id"])))[:limit]

    def close(self):
        self.db.close()


class SourceClient:
    FILMOT_BASE = "https://apiv1.filmot.com/api/v1"

    def __init__(self, cache_dir=None, api_key=None, transport=None):
        self.cache_dir = _cache_directory(cache_dir)
        self.api_key = api_key if api_key is not None else os.environ.get("FILMOT_API_KEY", "")
        self.transport = transport or self._transport
        self.memo = {}
        self.cooldowns = set()
        self.quota = {}
        self.network_requests = 0
        # Remove expired bodies on the next use of the client, even if that URL is
        # never requested again. The persistent catalog never contains full text.
        for directory in ("http", "documents"):
            for file in (self.cache_dir / directory).glob("*.json"):
                try:
                    if json.loads(file.read_text()).get("expires_at", 0) <= time.time():
                        file.unlink()
                except (OSError, ValueError, AttributeError):
                    continue

    @staticmethod
    def _transport(url, headers, timeout):
        if urllib.parse.urlsplit(url).scheme not in {"https", "http"}:
            raise ValueError("Only public HTTP evidence URLs are supported.")
        class EvidenceRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                has_key = any(k.lower() == "x-api-key" for k in req.headers)
                if has_key and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
                    raise ValueError("Authenticated cross-origin redirects are disabled.")
                return super().redirect_request(req, fp, code, msg, hdrs, newurl)
        req = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.build_opener(EvidenceRedirect()).open(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            return {"status": error.code, "headers": dict(error.headers), "body": error.read(16384), "url": url}
        with response:
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type.startswith(("video/", "audio/")):
                raise ValueError("Media downloads are disabled.")
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Evidence response exceeds the 8 MiB limit.")
            return {"status": response.status, "headers": dict(response.headers), "body": body, "url": response.url}

    def _redact(self, text):
        return str(text).replace(self.api_key, "[REDACTED]") if self.api_key else str(text)

    def _public_result(self, response):
        return {k: copy.deepcopy(v) for k, v in response.items() if k != "body"}

    def _load_filmot_document(self, video_id):
        path = self.cache_dir / "documents" / (video_id + ".en.json")
        try:
            stored = json.loads(path.read_text())
            doc = stored["document"]
            valid = (stored["expires_at"] > time.time() and doc["video_id"] == video_id
                     and doc["parser_version"] == PARSER_VERSION
                     and re.match(r"^en(?:[-_]|$)", doc["language"])
                     and hashlib.sha256(doc["text"].encode()).hexdigest() == doc["sha256"])
            if valid:
                return doc
            path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

    def _save_filmot_document(self, doc):
        # Only a normalized public spoken document crosses runs. Never serialize
        # the authenticated response, request URL, search query, or caller fields.
        public = {k: copy.deepcopy(doc[k]) for k in ("text", "cues", "source_url", "language", "is_auto",
            "sha256", "parser_version", "video_id", "completeness", "warnings")}
        payload = json.dumps({"expires_at": time.time() + 86400, "document": public}, ensure_ascii=False)
        if self.api_key and self.api_key in payload:
            return
        directory = self.cache_dir / "documents"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (public["video_id"] + ".en.json")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _request(self, url, *, authenticated=False, persist=True, refresh=False, ttl=86400):
        origin = urllib.parse.urlsplit(url).netloc
        cache_key = hashlib.sha256(url.encode()).hexdigest()
        if not refresh and cache_key in self.memo:
            return {**copy.deepcopy(self.memo[cache_key]), "cache_hit": True}
        path = self.cache_dir / "http" / (cache_key + ".json")
        if persist and not refresh and path.exists():
            try:
                stored = json.loads(path.read_text())
                if stored["expires_at"] > time.time():
                    result = stored["response"]
                    self.memo[cache_key] = result
                    return {**result, "cache_hit": True}
                path.unlink()
            except (ValueError, KeyError, OSError):
                pass
        if origin in self.cooldowns:
            return {"status": "cooldown", "cache_hit": False, "warnings": ["This source was blocked or rate-limited earlier in this run."]}
        headers = {"User-Agent": "Mozilla/5.0 (vocab-video-finder evidence reader)"}
        if authenticated:
            headers["X-API-Key"] = self.api_key
        result = None
        for attempt in range(2):
            try:
                self.network_requests += 1
                raw = self.transport(url, headers=headers, timeout=15)
                if isinstance(raw, tuple):
                    code, response_headers, body = raw[:3]
                    final_url = raw[3] if len(raw) > 3 else url
                else:
                    code, response_headers, body = raw.get("status", 200), raw.get("headers", {}), raw.get("body", "")
                    final_url = raw.get("url", url)
                body = body.decode("utf-8-sig", errors="replace") if isinstance(body, bytes) else body
                body = json.dumps(body) if not isinstance(body, str) else body
                rh = {k.lower(): str(v) for k, v in response_headers.items()}
                for key, target in (("x-api-credits-remaining", "remaining"), ("x-api-credits-reset", "reset"), ("x-api-credit-cost", "last_cost")):
                    if key in rh:
                        try:
                            self.quota[target] = int(rh[key])
                        except ValueError:
                            self.quota[target] = self._redact(rh[key])
                status = "ok" if 200 <= int(code) < 300 else {
                    401: "unauthorized", 403: "blocked", 412: "blocked", 429: "rate_limited", 404: "not_found"}.get(int(code), "source_error")
                if int(code) == 429:
                    try:
                        quota_error = json.loads(body)
                        if quota_error.get("error") == "quota_exhausted":
                            status = "quota_exhausted"
                            self.quota.update({k: quota_error[k] for k in ("remaining", "reset_at", "limit", "used", "cost") if k in quota_error})
                    except (ValueError, AttributeError):
                        pass
                result = {"status": status, "http_status": int(code), "body": body if status == "ok" else "",
                          "resolved_url": self._redact(final_url), "cache_hit": False, "quota": copy.deepcopy(self.quota), "warnings": []}
                if status in {"blocked", "rate_limited", "quota_exhausted", "unauthorized"}:
                    self.cooldowns.add(origin)
                break
            except (TimeoutError, urllib.error.URLError, OSError) as error:
                is_timeout = isinstance(error, TimeoutError) or "timed out" in str(error).lower()
                if is_timeout and attempt == 0:
                    continue
                result = {"status": "timeout" if is_timeout else "network_error", "cache_hit": False,
                          "warnings": ["Evidence request timed out." if is_timeout else "Evidence source could not be reached."]}
                break
            except ValueError:
                result = {"status": "invalid_response", "cache_hit": False, "warnings": ["Evidence source returned an unsupported response."]}
                break
        self.memo[cache_key] = copy.deepcopy(result)
        if persist:
            path.parent.mkdir(parents=True, exist_ok=True)
            lifetime = ttl if result["status"] == "ok" else 3600
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
                json.dump({"expires_at": time.time() + lifetime, "response": result}, stream, ensure_ascii=False)
                temporary = Path(stream.name)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return result

    def _filmot(self, endpoint, params, cost):
        if not self.api_key:
            return {"status": "missing_key", "cache_hit": False, "quota": copy.deepcopy(self.quota),
                    "warnings": ["FILMOT_API_KEY is not configured; use the public catalog and web search fallback."]}
        remaining = self.quota.get("remaining")
        if isinstance(remaining, (int, float)) and remaining < cost:
            return {"status": "quota_exhausted", "cache_hit": False, "quota": copy.deepcopy(self.quota),
                    "warnings": ["The remaining Filmot credits are below this request's documented cost."]}
        # Search query text and authenticated payloads stay in run memory, never in the shared HTTP cache.
        return self._request(self.FILMOT_BASE + endpoint + "?" + urllib.parse.urlencode(params), authenticated=True, persist=False)

    @staticmethod
    def _records(body):
        try:
            value = json.loads(body)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return value.get("result", value.get("videos", [value]))
        except ValueError:
            return [json.loads(line) for line in body.splitlines() if line.strip()]
        raise ValueError("Unsupported record response.")

    def search_filmot(self, query, manual=True, min_duration=180, max_duration=600, limit=30):
        if not str(query).strip():
            raise ValueError("A nonempty subtitle query is required.")
        limit = max(1, min(int(limit), 500))
        response = self._filmot("/getsubtitlesearch", {"query": query, "lang": "en", "detectedLang": "en",
            "searchManualSubs": int(bool(manual)), "startDuration": int(min_duration), "endDuration": int(max_duration),
            "sortField": "bm25match", "page": 1, "perPageCount": limit, "format": "jsonl", "getSnippets": 1,
            "columns": "id,title,channelid,channelname,duration,lang,hits"}, 5)
        output = {**self._public_result(response), "videos": [], "quota": copy.deepcopy(self.quota)}
        if response["status"] != "ok":
            return output
        try:
            for row in self._records(response["body"]):
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(row.get("id", ""))):
                    continue
                output["videos"].append({"id": "youtube:" + row["id"], "original_id": row["id"], "platform": "youtube",
                    "url": "https://www.youtube.com/watch?v=" + row["id"], "title": row.get("title", ""),
                    "channelid": row.get("channelid"), "channelname": row.get("channelname"),
                    "duration_seconds": row.get("duration"), "language": row.get("lang", "en"),
                    "is_auto": not manual, "hits": row.get("hits", []), "retrieval_only": True})
        except (ValueError, TypeError, KeyError):
            output.update(status="invalid_response", warnings=["Filmot returned an unreadable subtitle-search result."])
        return output

    def get_filmot_transcripts(self, ids):
        ids = list(dict.fromkeys(str(i).removeprefix("youtube:") for i in ids))
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", i) for i in ids):
            raise ValueError("Use observed 11-character YouTube video IDs.")
        output = {"status": "ok", "documents": [], "warnings": [], "quota": copy.deepcopy(self.quota), "cache_hit": True}
        pending = []
        for video_id in ids:
            cached = self._load_filmot_document(video_id)
            if cached is not None:
                output["documents"].append(cached)
            else:
                pending.append(video_id)
        for start in range(0, len(pending), 10):
            batch = pending[start:start + 10]
            response = self._filmot("/getvideos", {"id": ",".join(batch), "lang": "en",
                "columns": "subtitles,autosubs,manualsubs,lastfetchdate"}, 2)
            output["cache_hit"] = output["cache_hit"] and response.get("cache_hit", False)
            output["quota"] = copy.deepcopy(self.quota)
            if response["status"] != "ok":
                output["status"] = "partial" if output["documents"] else response["status"]
                output["warnings"].extend(response.get("warnings", []))
                break
            try:
                for row in self._records(response["body"]):
                    vid = str(row.get("id", ""))
                    if vid not in batch:
                        continue
                    try:
                        doc = normalize_transcript(row, format="filmot", source_url="https://www.youtube.com/watch?v=" + vid, video_id=vid)
                    except ValueError:
                        output["warnings"].append("No usable English subtitle body for video " + vid)
                        continue
                    output["documents"].append(doc)
                    self._save_filmot_document(doc)
            except (ValueError, TypeError):
                output["status"] = "partial" if output["documents"] else "invalid_response"
                output["warnings"].append("Filmot returned an unreadable video response.")
        if ids and not output["documents"] and output["status"] == "ok":
            output["status"] = "transcript_unavailable"
        positions = {video_id: n for n, video_id in enumerate(ids)}
        output["documents"].sort(key=lambda doc: positions[doc["video_id"]])
        return output

    def fetch_ted(self, url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in {"www.ted.com", "ted.com"} or not parsed.path.startswith("/talks/"):
            raise ValueError("fetch_ted requires an observed official TED talk URL.")
        response = self._request(url)
        output = {**self._public_result(response), "document": None, "video": None}
        if response["status"] != "ok":
            return output
        if _canonical_url(response.get("resolved_url", url)).replace("https://ted.com/", "https://www.ted.com/") != _canonical_url(url).replace("https://ted.com/", "https://www.ted.com/"):
            return {**output, "status": "identity_mismatch", "warnings": ["TED redirected to a different video identity."]}
        try:
            obj = _ted_object(response["body"], url, None)
            doc = normalize_transcript(response["body"], format="ted_jsonld", source_url=url)
            output.update(document=doc, video={"id": _canonical_url(url), "platform": "ted", "url": url,
                "title": html.unescape(obj.get("name", "")), "language": doc["language"], "source_url": url})
            duration = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", obj.get("duration", ""))
            if duration:
                output["video"]["duration_seconds"] = sum(float(n or 0) * mul for n, mul in zip(duration.groups(), (3600, 60, 1)))
        except ValueError:
            output.update(status="identity_or_transcript_unavailable", warnings=["The TED VideoObject transcript could not be safely matched to this URL."])
        return output

    def resolve_bilibili(self, bvid, cid=None, page=None, refresh=False):
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
            raise ValueError("An observed Bilibili BV identifier is required.")
        response = self._request("https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid}), refresh=refresh)
        checks = {"page_exists": False, "part_mapping_checked": False, "subtitle_body_checked": False, "playback_checked": False}
        output = {**self._public_result(response), "video": None, "verification": checks}
        if response["status"] != "ok":
            return output
        try:
            data = json.loads(response["body"])
            if data.get("code") != 0:
                return {**output, "status": "not_found" if data.get("code") in {-404, 62002} else "source_error"}
            data = data["data"]
            if data.get("bvid") and data["bvid"] != bvid:
                return {**output, "status": "identity_mismatch"}
            parts = data.get("pages", [])
            checks["page_exists"] = True
            selected = next((x for x in parts if str(x.get("cid")) == str(cid)), None) if cid is not None else None
            if cid is None:
                selected = next((x for x in parts if x.get("page") == int(page)), None) if page is not None else (parts[0] if len(parts) == 1 else None)
            if selected is None:
                return {**output, "status": "part_not_found" if cid is not None or page is not None else "needs_part",
                        "parts": [{k: x.get(k) for k in ("cid", "page", "part", "duration")} for x in parts]}
            checks["part_mapping_checked"] = True
            video = {"id": f"bilibili:{bvid}:{selected['cid']}", "platform": "bilibili", "bvid": bvid, "aid": data.get("aid"),
                "cid": selected["cid"], "page": selected["page"], "title": data["title"], "part": selected.get("part", ""),
                "duration_seconds": selected.get("duration"), "url": f"https://www.bilibili.com/video/{bvid}/?p={selected['page']}",
                "last_checked": time.time(), "subtitle_claim": "metadata_only", "language": None}
            return {**output, "video": video}
        except (ValueError, KeyError, TypeError):
            return {**output, "status": "invalid_response"}

    def inspect_bilibili_subtitles(self, bvid, cid, aid=None, fetch_body=False):
        params = {"bvid": bvid, "cid": cid}
        if aid is not None:
            params["aid"] = aid
        response = self._request("https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(params), ttl=3600)
        output = {**self._public_result(response), "tracks": [], "document": None, "subtitle_body_checked": False}
        if response["status"] != "ok":
            return output
        try:
            payload = json.loads(response["body"])
            if payload.get("code") != 0:
                return {**output, "status": "login_required" if payload.get("code") in {-101, -400} else "source_error"}
            data = payload.get("data", {})
            subtitles = data.get("subtitle", {})
            if data.get("need_login_subtitle") or subtitles.get("need_login_subtitle"):
                return {**output, "status": "login_required"}
            tracks = subtitles.get("subtitles", subtitles.get("list", []))
            output["tracks"] = [{k: x.get(k) for k in ("id", "lan", "lan_doc")} for x in tracks]
            output["status"] = "available" if tracks else "unavailable_or_unknown"
            if fetch_body:
                track = next((x for x in tracks if re.match(r"^en(?:[-_]|$)", x.get("lan", "")) and x.get("subtitle_url")), None)
                if track:
                    url = track["subtitle_url"]
                    if url.startswith("//"):
                        url = "https:" + url
                    subtitle_response = self._request(url, persist=False)
                    if subtitle_response["status"] == "ok":
                        output["document"] = normalize_transcript(subtitle_response["body"], format="bilibili",
                            source_url=f"https://www.bilibili.com/video/{bvid}/", video_id=f"{bvid}:{cid}")
                        output["subtitle_body_checked"] = True
            return output
        except (ValueError, TypeError):
            return {**output, "status": "invalid_response"}
