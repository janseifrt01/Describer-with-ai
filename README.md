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
                                                       memory/source_files.json
                                                                │
   every N files                                                │
        │                                                       │
        ▼                                                       │
┌───────────────────┐    fresh canonical heuristics             │
│ reflect (Claude)  │◀──────────────────────────────────────────┘
└───────────────────┘
        │
        ▼
memory/heuristics.json ──▶ injected into next scan's system prompt
```

- **`memory/source_files.json`** — per-file analyses (language, purpose, key
  symbols, dependencies, notes), keyed by relative path. Files whose
  `content_sha` is unchanged are skipped on re-scan.
- **`memory/heuristics.json`** — codebase-level patterns the agent learned
  about *this* repo. Loaded into the system prompt on every analysis call;
  rewritten from scratch on each reflection pass.

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

## As a library

```python
from discover_agent import DiscoverAgent, Memory

memory = Memory("memory")
agent = DiscoverAgent(memory=memory, reflect_every=10)
agent.scan("./src")

for record in memory.all_files():
    print(record.path, "—", record.purpose)

for h in memory.heuristics():
    print("•", h.text)
```

## Layout

```
discover_agent/
├── __init__.py
├── __main__.py    # CLI: python -m discover_agent <path>
├── agent.py       # DiscoverAgent — scan + analyze_file + reflect
└── memory.py      # Memory, FileRecord, Heuristic
memory/            # written at runtime (git-ignored)
```

## Notes

- Uses `claude-opus-4-7` with adaptive thinking and `output_config.format` for
  schema-validated JSON. Prompt caching keeps the static system prompt warm
  across the per-file calls in a scan.
- Files larger than 200KB or non-UTF-8 are skipped.
- Memory writes are atomic (write-to-temp + rename), so an interrupted scan
  won't corrupt the JSON store.
