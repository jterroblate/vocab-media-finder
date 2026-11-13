#!/usr/bin/env python3
"""Prepare a clean skill package for an explicitly selected host and destination.

The tool does not discover credentials, guess installation directories, contact a
service, or publish a repository. WorkBuddy receives a ZIP for local-package
import; other hosts receive an explicit directory unless ZIP is requested.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile


HOSTS = ("codex", "deepseek", "workbuddy", "generic")
EXAMPLES = {
    "words.json", "story.srt", "video.json", "domestic-easy.json", "global-standard.json"
}
ADAPTER_DOCS = {"bootstrap.md", "workbuddy.md", "deepseek.md", "codex.md", "generic.md"}
REFERENCE_DATA = {"sources.json"}
REFERENCE_DOCS = {"workflow.md", "matching.md"}
PUBLIC_SCRIPTS = {"match_vocab.py", "word_families.py", "video_sources.py", "video_pipeline.py",
                  "setup_resources.py", "runtime_paths.py", "search_profiles.py", "demo.py",
                  "test_match_vocab.py", "test_word_families.py", "test_video_sources.py",
                  "test_video_pipeline.py", "test_search_profiles.py", "test_v3_workflow.py"}
FORBIDDEN_PARTS = {".git", ".cache", "cache", "secrets", "__pycache__", "node_modules"}


def _skill_parts(text):
    matched = re.fullmatch(r"---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, flags=re.S)
    if not matched:
        raise ValueError("Source SKILL.md must contain YAML frontmatter.")
    return matched.group(1), matched.group(2)


def _scalar(value):
    value = value.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ValueError("Use JSON-compatible double-quoted scalar metadata.") from exc
        if not isinstance(parsed, str):
            raise ValueError("Skill metadata must be strings.")
        return parsed
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if not value or value in {"|", ">", "|-", ">-"}:
        raise ValueError("Package metadata requires nonempty scalar fields.")
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def skill_metadata(text):
    header, _ = _skill_parts(text)
    fields = {}
    for key in ("name", "description", "version"):
        match = re.search(r"^" + key + r":\s*(.+)$", header, flags=re.M)
        if match:
            fields[key] = _scalar(match.group(1))
    if "version" not in fields:
        match = re.search(r"^metadata:\s*\r?\n((?:[ \t]+[^\n]*\r?\n?)*)", header, re.M)
        version = re.search(r"^[ \t]+version:\s*(.+)$", match.group(1), re.M) if match else None
        if version:
            fields["version"] = _scalar(version.group(1))
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fields.get("name", "")):
        raise ValueError("Skill name must use lowercase kebab-case.")
    if not fields.get("description"):
        raise ValueError("Skill description is required.")
    fields.setdefault("version", "unversioned")
    return fields


def _allowed(relative, host):
    parts = relative.parts
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in parts):
        return False
    if relative.as_posix() in {"SKILL.md", "LICENSE", "NOTICE"}:
        return True
    if len(parts) == 2 and parts[0] == "scripts":
        return relative.name in PUBLIC_SCRIPTS
    if len(parts) == 2 and parts[0] == "references":
        return relative.name in REFERENCE_DOCS | REFERENCE_DATA
    if len(parts) == 2 and parts[0] == "examples" and parts[1] in EXAMPLES:
        return True
    if len(parts) == 2 and parts[0] == "adapters" and parts[1] in ADAPTER_DOCS:
        return True
    return host == "codex" and relative.as_posix() == "agents/openai.yaml"


def _workbuddy_skill(source, text, metadata, author):
    path = source / "adapters" / "workbuddy" / "metadata.json"
    if (path.is_symlink() or
            any(p.is_symlink() for p in path.parents if p != source and source in p.parents) or
            source not in path.resolve().parents):
        raise ValueError("Adapter metadata must be a regular source file.")
    adapter = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(adapter, dict):
        raise ValueError("WorkBuddy metadata must be an object.")
    if metadata["version"] == "unversioned":
        raise ValueError("WorkBuddy packages require a source skill version.")
    fields = {
        "name": metadata["name"],
        "display_name": adapter.get("display_name", metadata["name"]),
        "display_name_en": adapter.get("display_name_en", metadata["name"]),
        "description": metadata["description"],
        "description_zh": adapter.get("description_zh", metadata["description"]),
        "description_en": adapter.get("description_en", metadata["description"]),
        "version": metadata["version"],
        "author": author if author is not None else adapter.get("author", "Contributors"),
    }
    for key, value in fields.items():
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("WorkBuddy " + key + " must be a nonempty string.")
    _, body = _skill_parts(text)
    # JSON strings are valid YAML double-quoted scalars, including Unicode and
    # embedded quotes/newlines; do not build unescaped YAML from CLI arguments.
    header = "\n".join(key + ": " + json.dumps(value, ensure_ascii=False)
                       for key, value in fields.items())
    return "---\n" + header + "\n---\n\n" + body.lstrip("\r\n")


def package_files(source, host, author=None):
    source = Path(source).expanduser().resolve()
    entry = source / "SKILL.md"
    if not entry.is_file() or entry.is_symlink():
        raise ValueError("Source must contain a regular SKILL.md file.")
    text = entry.read_text(encoding="utf-8-sig")
    metadata = skill_metadata(text)
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not _allowed(relative, host):
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != source and source in p.parents):
            raise ValueError("Allowed package inputs must not be symlinks: " + relative.as_posix())
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes()
    if host == "workbuddy":
        files["SKILL.md"] = _workbuddy_skill(source, text, metadata, author).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "name": metadata["name"],
        "version": metadata["version"],
        "host": host,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files, manifest


def prepare_package(source, output, *, host="generic", output_format=None, author=None):
    if host not in HOSTS:
        raise ValueError("Unknown host.")
    output_format = output_format or ("zip" if host == "workbuddy" else "directory")
    if output_format not in {"directory", "zip"}:
        raise ValueError("Format must be directory or zip.")
    source = Path(source).expanduser().resolve()
    requested = Path(output).expanduser()
    if requested.is_symlink():
        raise ValueError("Output must not be a symlink.")
    destination = requested.resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("Output must be outside the source tree and cannot be its ancestor.")
    if output_format == "zip" and destination.suffix.lower() != ".zip":
        raise ValueError("ZIP output must use a .zip path.")
    if destination.exists():
        if output_format == "zip" or not destination.is_dir() or any(destination.iterdir()):
            raise ValueError("Output already exists; choose a new path or an empty directory.")
    files, manifest = package_files(source, host, author)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "zip":
        # Exclusive creation prevents overwriting an output that appeared after
        # validation. Sorted files and fixed timestamps produce repeatable ZIPs.
        with destination.open("xb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(files.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    info.create_system = 3
                    archive.writestr(info, data)
    else:
        with tempfile.TemporaryDirectory(prefix=".skill-package-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "package"
            staged.mkdir()
            for name, data in files.items():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if destination.exists():
                destination.rmdir()  # Only succeeds if it is still empty.
            os.replace(staged, destination)
    return {"status": "prepared", "host": host, "format": output_format,
            "output": str(destination), "name": manifest["name"],
            "version": manifest["version"], "file_count": len(files)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=HOSTS, default="generic")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", dest="output_format", choices=("directory", "zip"))
    parser.add_argument("--author", help="WorkBuddy author; defaults to source metadata or Contributors.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        result = prepare_package(args.source, args.output, host=args.host,
                                 output_format=args.output_format, author=args.author)
    except (OSError, ValueError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
