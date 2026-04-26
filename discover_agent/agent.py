"""DiscoverAgent — scans a codebase and stores structured findings to memory.

The agent self-improves by reflecting on accumulated analyses and writing back
heuristics that get injected into the system prompt on the next pass. Heuristics
shape future scans, so each run gets a sharper view of the codebase.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import anthropic

from .memory import FileRecord, Heuristic, Memory, sha256_text


MODEL = "claude-opus-4-7"

DEFAULT_INCLUDE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".scala",
    ".rb", ".php", ".cs", ".swift", ".m",
    ".c", ".h", ".cpp", ".hpp", ".cc",
    ".sh", ".bash", ".zsh",
    ".sql", ".html", ".css", ".scss",
    ".yaml", ".yml", ".toml", ".json",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "target", ".idea", ".vscode",
    "memory",
}

MAX_FILE_BYTES = 200_000


BASE_SYSTEM = """You are the Discover Agent, a code analyst.

Your job is to read one source file at a time and extract a compact, durable
summary of what it does and how it fits into the larger codebase.

For each file, return JSON with these fields:
- language: the programming language (lowercase short name, e.g. "python")
- purpose: one or two sentences on what this file does
- key_symbols: list of top-level functions, classes, or exports (just names)
- dependencies: list of imported modules/packages this file depends on
- notes: short notable patterns, smells, or anything that would be useful to
  remember when later analyzing related files

Be concise. Do not invent symbols or dependencies you cannot see in the file.
"""


REFLECTION_SYSTEM = """You are the Discover Agent's reflection module.

You will be shown a list of recent per-file analyses produced by the discover
agent. Your job is to extract durable, codebase-level heuristics that will
make future scans more accurate.

Good heuristics are:
- Specific to this codebase (not generic programming advice)
- Stable (still useful in a week, not tied to one file)
- Actionable when injected into the analyzer's system prompt

Examples of good heuristics:
- "The codebase follows a `core/` (logic) + `cli/` (entry) split. Treat files
  under `core/` as library code; files under `cli/` are thin shells that
  delegate to core."
- "Tests live alongside source files as `*_test.go`. When summarizing a test
  file, reference the file under test."
- "Functions prefixed with `_` are intentionally private; do not list them as
  key_symbols."

Return JSON: {"heuristics": [{"text": "...", "tags": ["..."]}, ...]}
Aim for 3-10 high-signal heuristics. Replace, don't append: every reflection
produces a fresh canonical list.
"""


FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "purpose": {"type": "string"},
        "key_symbols": {"type": "array", "items": {"type": "string"}},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["language", "purpose", "key_symbols", "dependencies", "notes"],
    "additionalProperties": False,
}


REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "heuristics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["heuristics"],
    "additionalProperties": False,
}


class DiscoverAgent:
    def __init__(
        self,
        memory: Memory,
        client: anthropic.Anthropic | None = None,
        model: str = MODEL,
        reflect_every: int = 10,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self.memory = memory
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.reflect_every = reflect_every
        self.max_file_bytes = max_file_bytes

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _system_blocks(self) -> list[dict]:
        """System prompt with cached base instructions and live heuristics.

        The base portion is stable across requests so we cache it. The
        heuristics section sits after the cache breakpoint — it changes as
        the agent learns, but the large stable preamble stays warm.
        """
        heuristics = self.memory.heuristics()
        if heuristics:
            heuristics_text = "\n\nLearned heuristics for this codebase:\n" + "\n".join(
                f"- {h.text}" for h in heuristics
            )
        else:
            heuristics_text = "\n\n(No learned heuristics yet — this is the first pass.)"

        return [
            {
                "type": "text",
                "text": BASE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": heuristics_text},
        ]

    # ------------------------------------------------------------------
    # File walking
    # ------------------------------------------------------------------

    def iter_source_files(
        self,
        root: str | os.PathLike[str],
        include_exts: set[str] | None = None,
        exclude_dirs: set[str] | None = None,
    ) -> Iterable[Path]:
        root_path = Path(root).resolve()
        include = include_exts or DEFAULT_INCLUDE_EXTS
        exclude = exclude_dirs or DEFAULT_EXCLUDE_DIRS

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in include:
                    yield p

    # ------------------------------------------------------------------
    # Per-file analysis
    # ------------------------------------------------------------------

    def analyze_file(self, path: Path, root: Path) -> FileRecord | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if len(raw) > self.max_file_bytes:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

        rel = str(path.relative_to(root))
        sha = sha256_text(text)

        if self.memory.has_unchanged(rel, sha):
            return self.memory.get_file(rel)

        prompt = (
            f"File path (relative to repo root): {rel}\n"
            f"--- BEGIN FILE ---\n{text}\n--- END FILE ---\n\n"
            "Return JSON matching the schema."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=self._system_blocks(),
            output_config={"format": {"type": "json_schema", "schema": FILE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )

        text_block = next((b.text for b in response.content if b.type == "text"), None)
        if text_block is None:
            return None
        data = json.loads(text_block)

        record = FileRecord(
            path=rel,
            content_sha=sha,
            language=data["language"],
            purpose=data["purpose"],
            key_symbols=data["key_symbols"],
            dependencies=data["dependencies"],
            notes=data["notes"],
        )
        self.memory.upsert_file(record)
        return record

    # ------------------------------------------------------------------
    # Reflection — the self-improvement loop
    # ------------------------------------------------------------------

    def reflect(self, sample_size: int = 30) -> list[Heuristic]:
        records = self.memory.all_files()
        if not records:
            return []

        sample = sorted(records, key=lambda r: r.analyzed_at, reverse=True)[:sample_size]
        payload = [
            {
                "path": r.path,
                "language": r.language,
                "purpose": r.purpose,
                "key_symbols": r.key_symbols,
                "dependencies": r.dependencies,
                "notes": r.notes,
            }
            for r in sample
        ]

        prompt = (
            "Here are recent per-file analyses from the discover agent. "
            "Produce a fresh canonical list of codebase-level heuristics.\n\n"
            f"{json.dumps(payload, indent=2)}"
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=REFLECTION_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": REFLECTION_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )

        text_block = next((b.text for b in response.content if b.type == "text"), None)
        if text_block is None:
            return []
        data = json.loads(text_block)

        new_heuristics = [
            Heuristic(text=h["text"], tags=h.get("tags", [])) for h in data["heuristics"]
        ]
        self.memory.replace_heuristics(new_heuristics)
        return new_heuristics

    # ------------------------------------------------------------------
    # Top-level scan
    # ------------------------------------------------------------------

    def scan(
        self,
        root: str | os.PathLike[str],
        include_exts: set[str] | None = None,
        exclude_dirs: set[str] | None = None,
    ) -> dict:
        root_path = Path(root).resolve()
        analyzed = 0
        skipped = 0
        since_reflect = 0

        for path in self.iter_source_files(root_path, include_exts, exclude_dirs):
            rel = str(path.relative_to(root_path))
            try:
                raw_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped += 1
                continue
            sha = sha256_text(raw_text)

            if self.memory.has_unchanged(rel, sha):
                skipped += 1
                continue

            record = self.analyze_file(path, root_path)
            if record is None:
                skipped += 1
                continue
            analyzed += 1
            since_reflect += 1

            if since_reflect >= self.reflect_every:
                self.memory.save()
                self.reflect()
                since_reflect = 0

        if analyzed and since_reflect > 0:
            self.reflect()

        self.memory.save()
        return {"analyzed": analyzed, "skipped": skipped, **self.memory.stats()}
