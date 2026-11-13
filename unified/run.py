#!/usr/bin/env python3
"""One entry for vocabulary video planning, music discovery and an offline demo.

The host performs actual web searches and media review. A plan never claims
that searches, account reads, playback or delivery have already happened.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

VERSION = "4.0.0-beta.1"
MODES = ("both", "video", "music")


def resolve_layout(root=None):
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parent
    candidates = [
        {"root": root, "video_scripts": root / "video/scripts", "video_examples": root / "video/examples",
         "music": root / "music", "layout": "single_package"},
        {"root": root, "video_scripts": root.parent / "scripts", "video_examples": root.parent / "examples",
         "music": root.parent / "companions/music-preferences-import", "layout": "source_repository"},
    ]
    for layout in candidates:
        if (layout["video_scripts"] / "video_pipeline.py").is_file():
            layout["music_available"] = (layout["music"] / "run_music_trial.py").is_file()
            return layout
    raise ValueError("Cannot locate the bundled video core or source-repository layout.")


def engine_command(kind, arguments, root=None):
    layout = resolve_layout(root)
    arguments = list(arguments)
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if kind == "video":
        script = layout["video_scripts"] / "video_pipeline.py"
    elif kind == "music":
        if not layout["music_available"]:
            raise ValueError("The music module is unavailable; video planning can still run.")
        script = layout["music"] / "run_music_trial.py"
        if not any(arg == "--core-dir" or arg.startswith("--core-dir=") for arg in arguments):
            arguments = ["--core-dir", str(layout["video_scripts"]), *arguments]
    else:
        raise ValueError("Choose the video or music engine.")
    return [sys.executable, "-B", str(script), *arguments]


def call_engine(kind, arguments, root=None):
    """Subprocess isolation keeps each engine's modules and CLI scope separate."""
    return subprocess.run(engine_command(kind, arguments, root), text=True, capture_output=True)


def _engine_json(kind, arguments, root=None):
    completed = call_engine(kind, arguments, root)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or f"{kind} engine failed.")
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise ValueError(f"{kind} engine returned an invalid JSON response.") from None


def _write_private(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    Path(path).chmod(0o600)


def create_plan(words, output, *, mode="both", network="domestic", level="easy", provider="auto",
                scenes=None, use_resources=True, root=None):
    if mode not in MODES or network not in {"domestic", "global"}:
        raise ValueError("Invalid media mode or viewing network.")
    if level not in {"starter", "easy", "standard", "advanced", "any"} or provider not in {"auto", "netease", "qq"}:
        raise ValueError("Invalid difficulty or music provider.")
    entries = json.loads(Path(words).read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Vocabulary must be a nonempty JSON list.")
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Choose a new or empty plan directory.")
    layout = resolve_layout(root)
    # Validate and expand before creating any output directory. The expansion
    # result remains a proposal; the host still reviews forms and senses.
    expansion_args = ["expand", "--words", str(Path(words).resolve())]
    if not use_resources:
        expansion_args.append("--no-resources")
    families = _engine_json("video", expansion_args, root)
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(canonical).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    _write_private(output / "words.json", entries)
    _write_private(output / "families.json", families)
    media = {}
    if mode in {"both", "video"}:
        args = ["queries", "--families", str(output / "families.json"), "--network", network, "--level", level]
        for scene in scenes or []:
            args.extend(["--scene", scene])
        queries = _engine_json("video", args, root)
        media["video"] = {"status": "host_search_required", "network": network, "level": level,
            "queries": queries, "web_search_executed": False, "found_videos": [],
            "next_step": "Execute searches with the host's real search tool, then verify complete captions, difficulty and viewing links."}
    if mode in {"both", "music"}:
        media["music"] = {"status": "pending_local_preference_read" if layout["music_available"] else "module_unavailable",
            "provider": provider, "client_read_executed": False, "found_songs": [],
            "command": engine_command("music", ["--provider", provider, "--words", str(output / "words.json"),
                                                "--output", str(output / "music-trial")], root) if layout["music_available"] else None,
            "next_step": "Read supported local preferences, then verify lyric text, recording, listening difficulty and playback."}
    plan = {"skill": "vocab-media-finder", "version": VERSION, "status": "host_actions_required",
        "mode": mode, "vocabulary_sha256": fingerprint, "vocabulary_entries": len(entries),
        "shared_words_file": str(output / "words.json"), "shared_families_file": str(output / "families.json"),
        "word_family_review_required": True, "media": media, "web_search_executed": False,
        "client_read_executed": False, "ready_to_deliver": False, "messages_sent": False,
        "additional_service_key_required": False}
    _write_private(output / "plan.json", plan)
    return plan


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="vocab-media-finder " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run one offline, synthetic video-and-music demonstration")
    demo.add_argument("--output", required=True, type=Path)
    plan = sub.add_parser("plan", help="Prepare one vocabulary plan; the host executes real search and review")
    plan.add_argument("--words", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--mode", choices=MODES, default="both")
    plan.add_argument("--network", choices=("domestic", "global"), default="domestic")
    plan.add_argument("--level", choices=("starter", "easy", "standard", "advanced", "any"), default="easy")
    plan.add_argument("--provider", choices=("auto", "netease", "qq"), default="auto")
    plan.add_argument("--scene", action="append")
    plan.add_argument("--no-resources", action="store_true", help="Use supplied word-family forms without optional dictionaries")
    for kind in ("video", "music"):
        child = sub.add_parser(kind, add_help=False, help=f"Run the internal {kind} engine through this entry")
        child.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] and argv[0] in {"video", "music"}:
        completed = call_engine(argv[0], argv[1:] or ["--help"])
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        return completed.returncode
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            completed = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("demo.py")),
                                        "--output", str(args.output)], text=True, capture_output=True)
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
        result = create_plan(args.words, args.output, mode=args.mode, network=args.network,
            level=args.level, provider=args.provider, scenes=args.scene, use_resources=not args.no_resources)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
