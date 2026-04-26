"""Persistent memory for the Discover Agent.

Two stores backed by JSON files on disk:

- ``source_files.json`` — per-file analyses, keyed by relative path. Each entry
  records a ``content_sha`` so unchanged files are skipped on re-scans.
- ``heuristics.json`` — accumulated, free-form learnings about the codebase
  produced by the agent's reflection step. These get injected into the system
  prompt on subsequent scans, which is what makes the agent auto-improvable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FileRecord:
    path: str
    content_sha: str
    language: str
    purpose: str
    key_symbols: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    notes: str = ""
    analyzed_at: str = field(default_factory=_now)


@dataclass
class Heuristic:
    text: str
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


class Memory:
    def __init__(self, root: str | os.PathLike[str] = "memory") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_path = self.root / "source_files.json"
        self.heuristics_path = self.root / "heuristics.json"
        self._files: dict[str, FileRecord] = {}
        self._heuristics: list[Heuristic] = []
        self._load()

    def _load(self) -> None:
        if self.files_path.exists():
            raw = json.loads(self.files_path.read_text())
            self._files = {k: FileRecord(**v) for k, v in raw.items()}
        if self.heuristics_path.exists():
            raw = json.loads(self.heuristics_path.read_text())
            self._heuristics = [Heuristic(**h) for h in raw]

    def _atomic_write(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def save(self) -> None:
        self._atomic_write(
            self.files_path,
            {k: asdict(v) for k, v in self._files.items()},
        )
        self._atomic_write(
            self.heuristics_path,
            [asdict(h) for h in self._heuristics],
        )

    def get_file(self, path: str) -> FileRecord | None:
        return self._files.get(path)

    def upsert_file(self, record: FileRecord) -> None:
        self._files[record.path] = record

    def has_unchanged(self, path: str, content_sha: str) -> bool:
        rec = self._files.get(path)
        return rec is not None and rec.content_sha == content_sha

    def all_files(self) -> list[FileRecord]:
        return list(self._files.values())

    def add_heuristic(self, text: str, tags: list[str] | None = None) -> None:
        self._heuristics.append(Heuristic(text=text, tags=tags or []))

    def heuristics(self) -> list[Heuristic]:
        return list(self._heuristics)

    def replace_heuristics(self, items: list[Heuristic]) -> None:
        self._heuristics = list(items)

    def stats(self) -> dict[str, int]:
        return {
            "files": len(self._files),
            "heuristics": len(self._heuristics),
        }
