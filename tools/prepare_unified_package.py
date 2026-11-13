#!/usr/bin/env python3
"""Build one installable vocabulary-media skill from allowlisted public sources.

The archive has exactly one SKILL.md and no dependency on separately installed
video/music skills. This builder never reads music clients or runtime outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from prepare_package import (EXAMPLES, PUBLIC_SCRIPTS, REFERENCE_DATA,
                             REFERENCE_DOCS, _workbuddy_skill, skill_metadata)
from prepare_music_package import HOSTS, MUSIC_FILES, _read_file


UNIFIED_PATH = Path("unified")
ROOT_FILES = {
    "SKILL.md", "README.md", "README.en.md", "LICENSE", "NOTICE",
    "run.py", "demo.py", "runtime.py", "test_run.py", "test_demo.py",
    "adapters/bootstrap.md",
    "adapters/codex.md", "adapters/workbuddy.md", "adapters/deepseek.md",
    "adapters/generic.md", "references/workflow.md", "references/platform-support.md",
    "examples/demo-preview.md",
}
ROOT_REQUIRED = {"SKILL.md", "README.md", "run.py", "demo.py", "adapters/bootstrap.md"}
VIDEO_RUNTIME = {
    "match_vocab.py", "word_families.py", "video_sources.py", "video_pipeline.py",
    "setup_resources.py", "runtime_paths.py", "search_profiles.py", "demo.py",
}
MUSIC_RUNTIME = {
    "import_netease.py", "local_netease.py", "preferences.py", "song_match.py",
    "run_music_trial.py", "qqmusic_api.py", "local_qqmusic.py", "provider_runtime.py",
    "demo.py", "test_words.json",
}
MUSIC_ENGINE_FILES = {name for name in MUSIC_FILES
                      if "/" not in name and (name.endswith(".py") or name == "test_words.json")}


def _source_root(source):
    requested = Path(source).expanduser()
    if requested.is_symlink():
        raise ValueError("Repository source must not be a symlink.")
    root = requested.resolve()
    if (root / UNIFIED_PATH).is_symlink():
        raise ValueError("Unified source must not be a symlink.")
    return root


def _provenance(origin, version, files):
    return (json.dumps({"origin": origin, "version": version, "license": "MIT",
                        "role": "Internal engine; no separate skill installation required.",
                        "files": {name: hashlib.sha256(data).hexdigest()
                                  for name, data in sorted(files.items())}}, indent=2) + "\n").encode()


def _unified_video_workflow(data):
    text = data.decode("utf-8")
    old = ('`VVF` 指向实际 `SKILL.md` 所在目录，不依赖宿主名称或固定home路径。以下初始化示例假定已经进入技能根目录；随后可以从其他工作区用该绝对路径执行，输入和结果留在调用工作区。\n\n'
           '```sh\nVVF="$PWD"\npython3 "$VVF/scripts/setup_resources.py"\n```')
    new = ('`VVF` 指向统一版内部的视频引擎根目录，不是统一 `SKILL.md` 根。先进入统一技能根目录（源码时为 unified/），以下命令会自动区分源码与安装包布局；输入与结果仍留在调用工作区。\n\n'
           '```sh\nVVF="$(python3 -c \'from run import resolve_layout; print(resolve_layout()["video_scripts"].parent)\')"\npython3 "$VVF/scripts/setup_resources.py"\n```')
    if old not in text:
        raise ValueError("Video workflow initialisation changed; adapt it for the unified layout before packaging.")
    return text.replace(old, new, 1).encode("utf-8")


def unified_package_files(source, host="generic", author=None):
    if host not in HOSTS:
        raise ValueError("Unknown host.")
    root = _source_root(source)
    unified = root / UNIFIED_PATH
    text = _read_file(unified, "SKILL.md", required=True).decode("utf-8-sig")
    metadata = skill_metadata(text)
    if metadata["name"] != "vocab-media-finder" or metadata["version"] == "unversioned":
        raise ValueError("Expected a versioned vocab-media-finder unified entrypoint.")
    files = {}
    for name in sorted(ROOT_FILES):
        data = _read_file(unified, name, required=name in ROOT_REQUIRED)
        if data is not None:
            files[name] = data
    for name in ("LICENSE", "NOTICE"):
        files.setdefault(name, _read_file(root, name, required=True))
    video = {}
    for name in sorted(PUBLIC_SCRIPTS):
        data = _read_file(root, "scripts/" + name, required=name in VIDEO_RUNTIME)
        if data is not None:
            video["scripts/" + name] = data
    for name in sorted(EXAMPLES):
        video["examples/" + name] = _read_file(root, "examples/" + name, required=True)
    for name in sorted(REFERENCE_DOCS | REFERENCE_DATA):
        data = _read_file(root, "references/" + name, required=True)
        video["references/" + name] = _unified_video_workflow(data) if name == "workflow.md" else data
    for name in ("LICENSE", "NOTICE"):
        video[name] = _read_file(root, name, required=True)
    video_version = skill_metadata(_read_file(root, "SKILL.md", required=True).decode("utf-8-sig"))["version"]
    files.update({"video/" + name: data for name, data in video.items()})
    files["video/SOURCE.json"] = _provenance("vocab-video-finder/scripts", video_version, video)
    music_root = root / "companions/music-preferences-import"
    if (root / "companions").is_symlink() or music_root.is_symlink():
        raise ValueError("Music source must not be a symlink.")
    music = {}
    for name in sorted(MUSIC_ENGINE_FILES):
        data = _read_file(music_root, name, required=name in MUSIC_RUNTIME)
        if data is not None:
            music[name] = data
    for name in ("LICENSE", "NOTICE"):
        music[name] = _read_file(music_root, name, required=True)
    music_version = skill_metadata(_read_file(music_root, "SKILL.md", required=True).decode("utf-8-sig"))["version"]
    files.update({"music/" + name: data for name, data in music.items()})
    files["music/SOURCE.json"] = _provenance("companions/music-preferences-import", music_version, music)
    if host in {"codex", "source"}:
        data = _read_file(unified, "agents/openai.yaml")
        if data is not None:
            files["agents/openai.yaml"] = data
    if host == "workbuddy":
        _read_file(unified, "adapters/workbuddy/metadata.json")
        files["SKILL.md"] = _workbuddy_skill(unified, text, metadata, author).encode("utf-8")
    skill_entries = [name for name in files if Path(name).name == "SKILL.md"]
    if skill_entries != ["SKILL.md"]:
        raise ValueError("A unified package must contain exactly one root SKILL.md.")
    manifest = {"schema_version": 1, "name": metadata["name"], "version": metadata["version"],
                "host": host, "layout": "single-skill", "entrypoint": "SKILL.md",
                "separate_skill_installation_required": False, "private_data_included": False,
                "engines": {"video": video_version, "music": music_version},
                "files": {name: hashlib.sha256(data).hexdigest()
                          for name, data in sorted(files.items())}}
    files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files, manifest


def prepare_unified_package(source, output, *, host="generic", output_format="zip", author=None):
    root = _source_root(source)
    requested = Path(output).expanduser()
    if requested.is_symlink():
        raise ValueError("Output must not be a symlink.")
    destination = requested.resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError("Output must be outside the repository and cannot be its ancestor.")
    if output_format not in {"zip", "directory"}:
        raise ValueError("Format must be zip or directory.")
    if output_format == "zip" and destination.suffix.lower() != ".zip":
        raise ValueError("ZIP output must use a .zip path.")
    if destination.exists() and (output_format == "zip" or not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Output already exists; choose a new path or an empty directory.")
    files, manifest = unified_package_files(root, host, author)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "zip":
        with destination.open("xb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(files.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    info.create_system = 3
                    archive.writestr(info, data)
    else:
        with tempfile.TemporaryDirectory(prefix=".unified-package-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "package"
            staged.mkdir()
            for name, data in files.items():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if destination.exists():
                destination.rmdir()
            os.replace(staged, destination)
    return {"status": "prepared", "name": manifest["name"], "version": manifest["version"],
            "host": host, "format": output_format, "layout": "single-skill",
            "output": str(destination), "file_count": len(files), "skill_entry_count": 1,
            "separate_skill_installation_required": False, "private_data_included": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", choices=HOSTS, default="generic")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", dest="output_format", choices=("zip", "directory"), default="zip")
    parser.add_argument("--author", help="WorkBuddy display author")
    args = parser.parse_args(argv)
    try:
        result = prepare_unified_package(args.source, args.output, host=args.host,
                                         output_format=args.output_format, author=args.author)
    except (OSError, ValueError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
