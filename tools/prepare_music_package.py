#!/usr/bin/env python3
"""Build an allowlisted music skill, with an optional sibling video skill.

This tool never reads client caches, credentials or trial outputs. Inputs are
explicit public repository files. A WorkBuddy music ZIP has SKILL.md at its root;
a paired archive is a two-folder bundle, not a single-skill import archive.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from prepare_package import HOSTS as RUNTIME_HOSTS, package_files as video_package_files
from prepare_package import skill_metadata, _workbuddy_skill


HOSTS = (*RUNTIME_HOSTS, "source")
LAYOUTS = ("music", "pair")
MUSIC_PATH = Path("companions/music-preferences-import")
MUSIC_FILES = {
    "SKILL.md", "README.md", "README.en.md", "LICENSE", "NOTICE",
    "import_netease.py", "local_netease.py", "preferences.py", "song_match.py",
    "run_music_trial.py", "test_words.json", "qqmusic_api.py", "local_qqmusic.py",
    "provider_runtime.py", "demo.py", "test_demo.py", "test_import_netease.py",
    "test_local_netease.py", "test_preferences.py", "test_song_match.py", "test_trial.py",
    "test_qqmusic.py", "test_local_qqmusic.py", "test_provider_runtime.py",
    "references/qq-adapter.md", "references/netease-adapter.md",
    "references/platform-support.md", "references/workflow.md",
    "adapters/bootstrap.md", "adapters/codex.md", "adapters/workbuddy.md",
    "adapters/deepseek.md", "adapters/generic.md",
}
MUSIC_REQUIRED = {
    "SKILL.md", "README.md", "import_netease.py", "local_netease.py",
    "preferences.py", "song_match.py", "run_music_trial.py", "test_words.json",
    "qqmusic_api.py", "local_qqmusic.py", "provider_runtime.py", "demo.py",
}
CORE_FILES = {"word_families.py", "match_vocab.py", "video_sources.py", "runtime_paths.py"}


def _read_file(root, relative, *, required=False):
    """Read a named regular input; reject links even when they stay in root."""
    root = Path(root)
    path = root / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents
                               if p != root and root in p.parents):
        raise ValueError("Package inputs must not be symlinks: " + str(relative))
    if root.resolve() not in path.resolve().parents:
        raise ValueError("Package input escapes its source: " + str(relative))
    if not path.exists():
        if required:
            raise ValueError("Missing public package input: " + str(relative))
        return None
    if not path.is_file():
        raise ValueError("Package input must be a regular file: " + str(relative))
    return path.read_bytes()


def _manifest(files, **fields):
    return {"schema_version": 1, **fields,
            "files": {name: hashlib.sha256(data).hexdigest()
                      for name, data in sorted(files.items())}}


def music_package_files(repo, host="generic", author=None):
    repo = Path(repo).expanduser()
    if repo.is_symlink():
        raise ValueError("Repository source must not be a symlink.")
    repo = repo.resolve()
    source = repo / MUSIC_PATH
    if source.is_symlink() or (repo / "companions").is_symlink():
        raise ValueError("Music source must not be a symlink.")
    if host not in HOSTS:
        raise ValueError("Unknown host.")
    entry = _read_file(source, "SKILL.md", required=True)
    text = entry.decode("utf-8-sig")
    metadata = skill_metadata(text)
    if metadata["name"] != "music-preferences-import":
        raise ValueError("Expected the music-preferences-import companion skill.")
    files = {}
    for name in sorted(MUSIC_FILES):
        content = _read_file(source, name, required=name in MUSIC_REQUIRED)
        if content is not None:
            files[name] = content
    for name in ("LICENSE", "NOTICE"):
        root_content = _read_file(repo, name, required=True)
        files.setdefault(name, root_content)
        files["core/" + name] = root_content
    for name in sorted(CORE_FILES):
        files["core/" + name] = _read_file(repo, "scripts/" + name, required=True)
    provenance = {
        "origin": "vocab-video-finder/scripts", "license": "MIT",
        "video_skill_version": skill_metadata(_read_file(repo, "SKILL.md", required=True).decode("utf-8-sig"))["version"],
        "purpose": "Standard-library vocabulary-family and transcript matching core.",
        "files": {name: hashlib.sha256(files["core/" + name]).hexdigest() for name in sorted(CORE_FILES)},
    }
    files["core/SOURCE.json"] = (json.dumps(provenance, indent=2) + "\n").encode()
    if host in {"codex", "source"}:
        data = _read_file(source, "agents/openai.yaml")
        if data is not None:
            files["agents/openai.yaml"] = data
    if host == "workbuddy":
        # The established host adapter reads only its explicitly named metadata.
        _read_file(source, "adapters/workbuddy/metadata.json")
        files["SKILL.md"] = _workbuddy_skill(source, text, metadata, author).encode("utf-8")
    manifest = _manifest(files, name=metadata["name"], version=metadata["version"],
                         host=host, bundled_core=True, private_data_included=False)
    files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files, manifest


def bundle_files(repo, host="generic", layout="music", author=None):
    if layout not in LAYOUTS:
        raise ValueError("Layout must be music or pair.")
    music, music_manifest = music_package_files(repo, host, author)
    if layout == "music":
        return music, music_manifest
    video_host = "codex" if host == "source" else host
    video, video_manifest = video_package_files(repo, video_host, author)
    if video_manifest["name"] != "vocab-video-finder":
        raise ValueError("Expected the vocab-video-finder video skill.")
    files = {"music-preferences-import/" + name: data for name, data in music.items()}
    files.update({"vocab-video-finder/" + name: data for name, data in video.items()})
    manifest = _manifest(files, name="vocabulary-media-pair", version=music_manifest["version"],
                         host=host, layout="pair", private_data_included=False,
                         components={"music-preferences-import": music_manifest["version"],
                                     "vocab-video-finder": video_manifest["version"]})
    files["bundle-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files, manifest


def prepare_music_package(repo, output, *, host="generic", layout="music", output_format=None, author=None):
    requested_source = Path(repo).expanduser()
    if requested_source.is_symlink():
        raise ValueError("Repository source must not be a symlink.")
    repo = requested_source.resolve()
    requested = Path(output).expanduser()
    if requested.is_symlink():
        raise ValueError("Output must not be a symlink.")
    destination = requested.resolve()
    if destination == repo or repo in destination.parents or destination in repo.parents:
        raise ValueError("Output must be outside the repository and cannot be its ancestor.")
    output_format = output_format or ("zip" if host in {"workbuddy", "source"} else "directory")
    if output_format not in {"directory", "zip"}:
        raise ValueError("Format must be directory or zip.")
    if output_format == "zip" and destination.suffix.lower() != ".zip":
        raise ValueError("ZIP output must use a .zip path.")
    if destination.exists() and (output_format == "zip" or not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Output already exists; choose a new path or an empty directory.")
    files, manifest = bundle_files(repo, host, layout, author)
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
        with tempfile.TemporaryDirectory(prefix=".music-package-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "package"
            staged.mkdir()
            for name, data in files.items():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if destination.exists():
                destination.rmdir()
            os.replace(staged, destination)
    result = {"status": "prepared", "host": host, "layout": layout, "format": output_format,
              "output": str(destination), "name": manifest["name"], "version": manifest["version"],
              "file_count": len(files), "private_data_included": False}
    if layout == "pair" and host == "workbuddy":
        result["host_note"] = "This two-folder bundle is not a single-skill WorkBuddy import ZIP; import the two skills separately."
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Public vocab-video-finder repository containing companions/music-preferences-import")
    parser.add_argument("--host", choices=HOSTS, default="generic")
    parser.add_argument("--layout", choices=LAYOUTS, default="music")
    parser.add_argument("--format", dest="output_format", choices=("directory", "zip"))
    parser.add_argument("--author", help="WorkBuddy display author; defaults to companion metadata or Contributors")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_music_package(args.source, args.output, host=args.host, layout=args.layout,
                                       output_format=args.output_format, author=args.author)
    except (ValueError, OSError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
