#!/usr/bin/env python3
"""One recording-safe demo: the same authored word list across video and songs."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from run import VERSION, resolve_layout

SYNTHETIC_LYRIC = ("[00:01.00]I competed for you, and I wrote for us.\n"
                   "[00:02.00]The competition is here, and you have written it.")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _music_worker(output, words_file, root=None):
    """Executed only in its own process: legacy demo module names cannot collide."""
    layout = resolve_layout(root)
    sys.path.insert(0, str(layout["music"]))
    spec = importlib.util.spec_from_file_location("legacy_synthetic_music_demo", layout["music"] / "demo.py")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    from run_music_trial import run as music_run
    entries = _read(words_file)

    class SharedVocabularyProvider(legacy.SyntheticProvider):
        def lyrics(self, track):
            return {"text": SYNTHETIC_LYRIC, "source_url": "synthetic:self-authored-unified-demo",
                    "checked_at": "synthetic"}

    results = {name: music_run(layout["video_scripts"], Path(output) / name,
        provider=SharedVocabularyProvider(name), words=entries, max_artists=1, max_songs=2)
        for name in ("netease", "qq")}
    return {"providers": results, "web_requests": 0, "personal_data_read": False,
            "real_recommendations": 0, "ready_to_deliver": False}


def _subprocess_json(command):
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "Synthetic demo worker failed.")
    return json.loads(completed.stdout)


def run_demo(output, root=None):
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Choose a new or empty demo directory.")
    layout = resolve_layout(root)
    if not layout["music_available"]:
        raise ValueError("The complete unified demo requires its bundled music module.")
    words = _read(layout["video_examples"] / "words.json")
    output.mkdir(parents=True, exist_ok=True)
    words_file = output / "words.json"
    with words_file.open("x", encoding="utf-8") as stream:
        json.dump(words, stream, ensure_ascii=False, indent=2)
    fingerprint = hashlib.sha256(json.dumps(words, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    video_result = _subprocess_json([sys.executable, "-B", str(layout["video_scripts"] / "demo.py"),
                                    "--output", str(output / "video")])
    video = _read(output / "video/reviewed.json")
    music_result = _subprocess_json([sys.executable, "-B", str(Path(__file__).resolve()),
        "--_music-worker", "--output", str(output / "music"), "--words", str(words_file),
        "--layout-root", str(layout["root"])])
    music = {}
    for platform in ("netease", "qq"):
        report = _read(output / "music" / platform / "trial-summary.json")
        songs = []
        for track in report["song_trials"]:
            match = track["match"]
            songs.append({"track_id": track["track_id"], "title": track["title"],
                "family_coverage": match["family_coverage"], "text_occurrences": match["text_occurrences"],
                "relation_counts": match["relation_counts"], "per_family": match["per_family"],
                "context_review_status": match["context_review_status"], "playback_verified": False,
                "synthetic": True, "real_play_url": None})
        music[platform] = {"status": "synthetic_text_trial", "vocabulary_sha256": fingerprint,
            "vocabulary_entries": report["vocabulary_entries"], "songs": songs,
            "ready_to_deliver": False, "real_recommendations": 0}
    report = {"skill": "vocab-media-finder", "version": VERSION, "status": "synthetic_offline_demo_complete",
        "notice": "Self-authored synthetic data only. No real videos, songs, accounts or playable recommendations.",
        "vocabulary": {"words": words, "entries": len(words), "sha256": fingerprint,
                       "same_input_for_video_and_music": True},
        "video": {"status": "synthetic_transcript_reviewed", "vocabulary_sha256": fingerprint,
            "family_coverage": video["stats"]["family_coverage"],
            "text_occurrences": video["stats"]["total_occurrences"],
            "relation_counts": video["stats"]["kind_counts"], "form_counts": video["stats"]["form_counts"],
            "source_target_hits": video["source_target_hits"], "playback_verified": False,
            "ready_to_deliver": False, "real_recommendations": 0},
        "music": music, "web_requests": video_result["web_requests"] + music_result["web_requests"],
        "personal_data_read": False, "messages_sent": False, "ready_to_deliver": False}
    with (output / "demo-report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    lines = ["# Vocab Media Finder · 离线合成演示", "",
        "一份词表 → 视频与歌曲。仅使用自写合成数据；不联网、不读取账号、不提供真实推荐。", "",
        "词表：" + "、".join(item["word"] for item in words), "",
        "| 材料 | 词族覆盖 | 文本出现次数 | 状态 |", "|---|---:|---:|---|",
        f"| 合成视频 | {report['video']['family_coverage']} | {report['video']['text_occurrences']} | 合成文稿已复核；无真实播放 |"]
    for platform, result in music.items():
        for index, song in enumerate(result["songs"], 1):
            lines.append(f"| {platform} 合成歌曲 {index} | {song['family_coverage']} | {song['text_occurrences']} | 合成文本；待真实语境与播放复核 |")
    lines += ["", "所有媒介共用同一词表及词族规则；合成歌曲展示 compete/write，合成视频还展示 go/good。", "",
              "真实工作流仍需宿主执行搜索、读取可用偏好和文稿，并复核语言、版本、难度与播放。"]
    with (output / "demo-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--_music-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--words", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--layout-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = (_music_worker(args.output, args.words, args.layout_root) if args._music_worker
                  else run_demo(args.output, args.layout_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")
