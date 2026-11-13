#!/usr/bin/env python3
"""Install optional public lexical resources in the current user's cache."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from runtime_paths import cache_root


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install", action="store_true", help="Install into user cache; no system Python changes.")
    args = p.parse_args()
    base = cache_root()
    packages = base / "python" / f"py{sys.version_info.major}{sys.version_info.minor}"
    corpus = base / "nltk_data" / "corpora" / "wordnet.zip"
    if args.install:
        packages.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--upgrade", "--target", str(packages), "numpy>=1.26,<2.5",
                        "lemminflect>=0.2.3,<0.3", "nltk>=3.9,<4"], check=True)
        # NLTK documents manual data installation. Fetch only these fixed official
        # HTTPS resources and verify its published checksum; do not relax pathsec.
        origin = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/"
        index = urllib.request.urlopen(origin + "index.xml", timeout=20).read()
        entry = next(x for x in ET.fromstring(index).iter("package") if x.get("id") == "wordnet")
        url = origin + "packages/corpora/wordnet.zip"
        if entry.get("url") != url or not entry.get("sha256_checksum"):
            raise ValueError("Official WordNet resource metadata changed; inspect before downloading.")
        expected = entry.get("sha256_checksum")
        if not corpus.exists() or hashlib.sha256(corpus.read_bytes()).hexdigest() != expected:
            data = urllib.request.urlopen(url, timeout=30).read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("WordNet checksum mismatch.")
            corpus.parent.mkdir(parents=True, exist_ok=True)
            corpus.write_bytes(data)
    sys.path.insert(0, str(packages))
    from word_families import expand_targets
    result = expand_targets(["compete", "breed", "dim"])
    print(json.dumps({"python": sys.executable, "package_dir": str(packages),
                      "wordnet_present": corpus.exists(), "warnings": result["warnings"],
                      "sample_member_counts": [len(x["members"]) for x in result["families"]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
