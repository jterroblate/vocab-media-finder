#!/usr/bin/env python3
"""Offline synthetic transcript demo. No network, fabricated recommendations or sending."""
import argparse
import json
from pathlib import Path
from video_sources import normalize_transcript
from word_families import expand_targets
from video_pipeline import make_match, review_template, review_match


def run_demo(output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty output directory.")
    root = Path(__file__).resolve().parents[1]
    examples = root / "examples"
    output.mkdir(parents=True, exist_ok=True)
    words = json.loads((examples / "words.json").read_text(encoding="utf-8"))
    video = json.loads((examples / "video.json").read_text(encoding="utf-8"))
    families = expand_targets(words, use_resources=False)
    doc = normalize_transcript((examples / "story.srt").read_text(encoding="utf-8"), format="srt")
    report = make_match(families, doc, video)
    review = review_template(report)
    for decision in review["decisions"].values():
        decision.update(action="accept", relation_confirmed=True, sense="same",
                        note="Reviewed synthetic example with explicitly supplied family forms.")
    reviewed = review_match(report, review)
    reviewed["demo_notice"] = "Synthetic text only. No real video, audio, access or playback was verified."
    for name, value in [("families.json", families), ("reviewed.json", reviewed)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["离线合成示例 / Offline synthetic demo", "不是视频推荐；没有真实观看链接。", "",
             f"词族：{reviewed['stats']['family_coverage']}；实际出现：{reviewed['stats']['total_occurrences']}次。"]
    for word, number in reviewed["stats"]["form_counts"].items():
        lines.append(f"{word} × {number}")
    (output / "demo-report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status":"synthetic_demo_complete", "families":reviewed["stats"]["family_coverage"],
            "occurrences":reviewed["stats"]["total_occurrences"], "web_requests":0,
            "real_video_recommendations":0, "report":str(output / "demo-report.txt")}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        print(json.dumps(run_demo(args.output), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        p.exit(2, str(exc) + "\n")
