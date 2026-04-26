"""Persistent memory for the Discover Agent.

Storage is split across small Markdown files so the memory directory merges
cleanly in git:

- ``memory/files/<slug>.md`` — one file per analyzed source file. YAML
  frontmatter holds structured fields (path, content_sha, language, symbols,
  dependencies); the body holds the prose Purpose and Notes sections.
- ``memory/heuristics.md`` — bullet list of codebase-level heuristics
  rewritten on each reflection pass.

Per-record files mean concurrent branches modifying different source files
produce independent, non-conflicting diffs. Heuristics still rewrite as a
whole, but as a small Markdown bullet list they're easy to merge by hand.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# ---------------------------------------------------------------------------
# Filename slugging
# ---------------------------------------------------------------------------

def _slug(path: str) -> str:
    """Convert a relative source path into a safe, readable filename.

    The original path stays in the frontmatter so we never have to reverse
    this — the slug only has to be unique and filesystem-safe.
    """
    safe = path.replace("/", "__").replace("\\", "__")
    safe = re.sub(r"^\.+", "_", safe)
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", safe)
    return safe + ".md"


# ---------------------------------------------------------------------------
# File record render / parse
# ---------------------------------------------------------------------------

def _render_file_record(rec: FileRecord) -> str:
    front = {
        "path": rec.path,
        "content_sha": rec.content_sha,
        "language": rec.language,
        "analyzed_at": rec.analyzed_at,
        "key_symbols": list(rec.key_symbols),
        "dependencies": list(rec.dependencies),
    }
    front_yaml = yaml.safe_dump(front, sort_keys=True, default_flow_style=False)
    body = f"## Purpose\n\n{rec.purpose.strip()}\n"
    if rec.notes.strip():
        body += f"\n## Notes\n\n{rec.notes.strip()}\n"
    return f"---\n{front_yaml}---\n\n{body}"


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n+(.*)", re.DOTALL)


def _parse_file_record(text: str) -> FileRecord:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("Missing YAML frontmatter")
    front = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)

    purpose = ""
    notes = ""
    for section in re.split(r"^## ", body, flags=re.MULTILINE):
        if section.startswith("Purpose"):
            purpose = section[len("Purpose"):].strip()
        elif section.startswith("Notes"):
            notes = section[len("Notes"):].strip()

    return FileRecord(
        path=front["path"],
        content_sha=front["content_sha"],
        language=front.get("language", ""),
        purpose=purpose,
        key_symbols=list(front.get("key_symbols") or []),
        dependencies=list(front.get("dependencies") or []),
        notes=notes,
        analyzed_at=front.get("analyzed_at", _now()),
    )


# ---------------------------------------------------------------------------
# Heuristics render / parse
# ---------------------------------------------------------------------------

_HEURISTICS_HEADER = (
    "# Codebase Heuristics\n\n"
    "<!-- Rewritten on each reflection pass. Each bullet is one heuristic. "
    "Tags follow the bullet text in an HTML comment. -->\n\n"
)

_HEURISTIC_LINE_RE = re.compile(
    r"^-\s+(?P<text>.+?)(?:\s*<!--\s*tags:\s*(?P<tags>[^>]*?)\s*-->)?\s*$"
)


def _render_heuristics(items: list[Heuristic]) -> str:
    if not items:
        return _HEURISTICS_HEADER + "_(none yet)_\n"
    lines = [_HEURISTICS_HEADER.rstrip(), ""]
    for h in items:
        text = h.text.strip()
        if not text:
            continue
        if h.tags:
            tag_str = ", ".join(t.strip() for t in h.tags if t.strip())
            lines.append(f"- {text} <!-- tags: {tag_str} -->")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines) + "\n"


def _parse_heuristics(text: str) -> list[Heuristic]:
    items: list[Heuristic] = []
    for line in text.splitlines():
        m = _HEURISTIC_LINE_RE.match(line)
        if not m:
            continue
        body = m.group("text").strip()
        if not body or body.startswith("_("):
            continue
        tags_raw = m.group("tags") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        items.append(Heuristic(text=body, tags=tags))
    return items


# ---------------------------------------------------------------------------
# Memory facade
# ---------------------------------------------------------------------------

class Memory:
    def __init__(self, root: str | os.PathLike[str] = "memory") -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.heuristics_path = self.root / "heuristics.md"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, FileRecord] = {}
        self._heuristics: list[Heuristic] = []
        self._load()

    # -- IO ------------------------------------------------------------

    def _load(self) -> None:
        for md in sorted(self.files_dir.glob("*.md")):
            try:
                rec = _parse_file_record(md.read_text(encoding="utf-8"))
            except (ValueError, KeyError, yaml.YAMLError):
                continue
            self._files[rec.path] = rec
        if self.heuristics_path.exists():
            self._heuristics = _parse_heuristics(
                self.heuristics_path.read_text(encoding="utf-8")
            )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _file_path(self, rec_path: str) -> Path:
        return self.files_dir / _slug(rec_path)

    def _write_file_record(self, rec: FileRecord) -> None:
        self._atomic_write(self._file_path(rec.path), _render_file_record(rec))

    def save_heuristics(self) -> None:
        self._atomic_write(self.heuristics_path, _render_heuristics(self._heuristics))

    def save(self) -> None:
        """Flush heuristics. File records are written incrementally on upsert."""
        self.save_heuristics()

    # -- File records --------------------------------------------------

    def get_file(self, path: str) -> FileRecord | None:
        return self._files.get(path)

    def upsert_file(self, record: FileRecord) -> None:
        self._files[record.path] = record
        self._write_file_record(record)

    def has_unchanged(self, path: str, content_sha: str) -> bool:
        rec = self._files.get(path)
        return rec is not None and rec.content_sha == content_sha

    def all_files(self) -> list[FileRecord]:
        return list(self._files.values())

    # -- Heuristics ----------------------------------------------------

    def add_heuristic(self, text: str, tags: list[str] | None = None) -> None:
        self._heuristics.append(Heuristic(text=text, tags=tags or []))

    def heuristics(self) -> list[Heuristic]:
        return list(self._heuristics)

    def replace_heuristics(self, items: list[Heuristic]) -> None:
        self._heuristics = list(items)
        self.save_heuristics()

    # -- Stats ---------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "files": len(self._files),
            "heuristics": len(self._heuristics),
        }
