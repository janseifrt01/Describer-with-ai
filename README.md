# Describer-with-ai

A self-improving Discover Agent that scans source code and remembers what it
learns. Each scan produces a structured per-file analysis; periodic reflection
turns those analyses into codebase-level heuristics that get fed back into the
next scan, so the agent gets sharper with every pass.

## How it works

```
                        ┌─────────────────────────────┐
   walk source files ──▶│ analyze_file (Claude call)  │──▶ FileRecord
                        └─────────────────────────────┘         │
                                                                ▼
                                                   memory/files/<slug>.md
                                                                │
   every N files                                                │
        │                                                       │
        ▼                                                       │
┌──────────────────────────┐    edits via memory tool           │
│ reflect (tool-using      │◀──────────────────────────────────┘
│ Claude session +         │
│ Anthropic Memory tool)   │──▶ memory/heuristics/*.md
└──────────────────────────┘                │
                                            │
                                            ▼
                                injected into next scan's system prompt
```

- **`memory/files/<slug>.md`** — one Markdown file per analyzed source
  file. YAML frontmatter holds structured fields (path, content_sha,
  language, key_symbols, dependencies); the body holds the prose Purpose
  and Notes. Skipped on re-scan when `content_sha` matches.
- **`memory/heuristics/*.md`** — codebase-level patterns. Owned by Claude
  via the Anthropic Memory tool (`memory_20250818`); the model reads,
  edits, splits, and deletes files here as it reflects. Read into the
  system prompt on every per-file analysis.

Both directories live in git as small, hand-mergeable Markdown files.

## Setup

```bash
pip install -e .            # or `pip install -e ".[dev]"` for pytest + ruff
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
```

## Usage

Scan a directory:

```bash
discover-agent /path/to/your/project
# or, equivalently:
python -m discover_agent /path/to/your/project
```

Re-run reflection over existing memory without re-scanning:

```bash
discover-agent --reflect-only .
```

Customize the reflection cadence:

```bash
discover-agent --reflect-every 5 ./src
```

## CLAUDE.md bridge

The agent's memory connects to the broader Claude Code / Cursor / dev-notes
ecosystem in both directions.

**Seed the agent with what you (or Claude Code) already know:**

```bash
discover-agent --import-claude-md CLAUDE.md ./src
discover-agent --import-claude-md CLAUDE.md --import-claude-md docs/ARCH.md ./src
```

Each imported file lands in `memory/heuristics/imported__<name>.md` with a
provenance header. The next reflection pass reviews it and decides whether
to keep, integrate, or replace.

**Hand the agent's memory back to your next session:**

```bash
discover-agent --export-claude-md CLAUDE.md ./src
```

Writes a self-contained Markdown summary — current heuristics plus a
table of files analyzed grouped by language — that drops straight into a
project's `CLAUDE.md` so the next Claude Code session starts with the
agent's accumulated context.

Both flags can be combined with `--reflect-only`, or used with no scan
path at all (`discover-agent --import-claude-md FOO.md` just imports).

## As a library

```python
from discover_agent import DiscoverAgent, Memory, import_claude_md, export_claude_md

memory = Memory("memory")
agent = DiscoverAgent(memory=memory, reflect_every=10)

import_claude_md("CLAUDE.md", memory)        # seed
agent.scan("./src")                          # learn
export_claude_md("CLAUDE.md", memory)        # share back

for record in memory.all_files():
    print(record.path, "—", record.purpose)

# Heuristics are owned by Claude (via the Memory tool); read them as text:
print(memory.heuristics_text())
```

## Layout

```
discover_agent/
├── __init__.py
├── __main__.py    # CLI: discover-agent <path>
├── agent.py       # DiscoverAgent — scan + analyze_file + reflect
├── bridge.py      # import/export CLAUDE.md
└── memory.py      # Memory, FileRecord
memory/
├── files/         # one Markdown file per analyzed source file
└── heuristics/    # owned by Claude via the Anthropic Memory tool
```

## Notes

- Uses `claude-opus-4-7` with adaptive thinking. Per-file analysis uses
  `output_config.format` for schema-validated JSON; reflection uses the
  Anthropic Memory tool (`memory_20250818`) via the SDK's
  `BetaLocalFilesystemMemoryTool` so Claude itself decides what to keep,
  refine, or discard.
- Files larger than 200KB or non-UTF-8 are skipped.
- Memory writes are atomic (write-to-temp + rename), so an interrupted scan
  won't corrupt the store.
