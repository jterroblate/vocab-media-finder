"""Shared, host-independent paths; no agent SDK or private configuration."""
import os
from pathlib import Path


def cache_root(explicit=None):
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = os.environ.get("VOCAB_VIDEO_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "vocab-video-finder"
