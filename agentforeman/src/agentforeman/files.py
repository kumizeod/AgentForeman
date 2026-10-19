"""Atomic metadata writes and conservative source snapshots."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


IGNORED = {".git", ".venv", "venv", ".foreman", "__pycache__", ".pytest_cache"}


def inventory(root: Path) -> dict[str, str]:
    """Reject symlinks rather than archive or follow references outside a snapshot."""
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are unsupported: {path}")
        if path.is_file():
            result[rel.as_posix()] = digest(path)
        elif not path.is_dir():
            raise ValueError(f"Only regular files/directories are supported: {path}")
    return result


def snapshot(source: Path, target: Path) -> None:
    files = inventory(source)
    target.mkdir(parents=True, exist_ok=True)
    for rel in files:
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dst)


def replace_snapshot(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    snapshot(source, target)


def render_argv(command: list[str], **values: str) -> list[str]:
    # No shell and no interpolation of arbitrary braces in inline Python scripts.
    result = []
    for arg in command:
        for name, value in values.items():
            arg = arg.replace("{" + name + "}", str(value))
        result.append(arg)
    return result
