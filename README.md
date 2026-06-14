# Describer-with-ai

A self-improving Discover Agent that scans source code and remembers what it
learns. Each scan produces a structured per-file analysis; periodic reflection
turns those analyses into codebase-level heuristics that get fed back into the
next scan, so the agent gets sharper with every pass.

It also includes a **Solution Evolution layer**: given a user-stated problem,
an evolutionary search over candidate design proposals (Markdown) backed by
[OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)
produces a winning design plus top-K runners-up, scored by Claude as
LLM-as-judge against a rubric.

## How it works (scan)

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

Two top-level commands: **`scan`** (analyze a codebase) and **`evolve`**
(explore a solution design space). Legacy invocations like
`discover-agent /path/to/project` are routed to `scan` automatically.

### Scan a codebase

```bash
discover-agent scan /path/to/your/project
# legacy form still works:
discover-agent /path/to/your/project
```

Re-run reflection over existing memory without re-scanning:

```bash
discover-agent scan --reflect-only .
```

Customize the reflection cadence:

```bash
discover-agent scan --reflect-every 5 ./src
```

### Evolve a solution design

```bash
discover-agent evolve --slug payments-retry \
    --goal "Design a retry + idempotency layer for the payment API" \
    --constraint "Must reuse existing PostgreSQL outbox table" \
    --iterations 30
```

Outputs land under `memory/solutions/<slug>/`:

```
memory/solutions/payments-retry/
├── problem.md      # what you asked
├── rubric.md       # auto-generated; safe to hand-edit between runs
├── seed.md         # baseline design Claude wrote in one shot
├── winner.md       # top-scoring candidate after evolution
├── runners-up/     # 02.md, 03.md — runner-up designs for comparison
└── trace.jsonl     # per-evaluation score history
```

The codebase heuristics from `memory/heuristics/` are injected into the
seed, the mutator, and the judge — so the more you've scanned, the more
the agent's designs will match your project's conventions.

## CLAUDE.md bridge

The agent's memory connects to the broader Claude Code / Cursor / dev-notes
ecosystem in both directions.

**Seed the agent with what you (or Claude Code) already know:**

```bash
discover-agent scan --import-claude-md CLAUDE.md ./src
discover-agent scan --import-claude-md CLAUDE.md --import-claude-md docs/ARCH.md ./src
```

Each imported file lands in `memory/heuristics/imported__<name>.md` with a
provenance header. The next reflection pass reviews it and decides whether
to keep, integrate, or replace.

**Hand the agent's memory back to your next session:**

```bash
discover-agent scan --export-claude-md CLAUDE.md ./src
```

Writes a self-contained Markdown summary — current heuristics plus a
table of files analyzed grouped by language — that drops straight into a
project's `CLAUDE.md` so the next Claude Code session starts with the
agent's accumulated context.

Both flags can be combined with `--reflect-only`, or used with no scan
path at all (`discover-agent scan --import-claude-md FOO.md` just imports).

## As a library

```python
from discover_agent import (
    DiscoverAgent, Memory, ProblemSpec, evolve,
    import_claude_md, export_claude_md,
)

memory = Memory("memory")
agent = DiscoverAgent(memory=memory, reflect_every=10)

import_claude_md("CLAUDE.md", memory)        # seed
agent.scan("./src")                          # learn the codebase
export_claude_md("CLAUDE.md", memory)        # share back

# Run a solution-evolution pass:
winner = evolve(
    ProblemSpec(
        slug="payments-retry",
        title="Payments retry layer",
        goal="Design a retry + idempotency layer for the payment API",
        constraints=["Must reuse the existing PostgreSQL outbox table"],
    ),
    memory=memory,
    iterations=20,
)
print(winner.read_text())
```

## How it works (evolve)

```
                  ┌──────────────────────────────────┐
user problem ──▶  │  ProblemSpec + auto-Rubric       │
                  │  (rubric is hand-editable)       │
                  └────────────────┬─────────────────┘
                                   │
                                   ▼
                  ┌──────────────────────────────────┐
                  │  Seed design (1 Claude call)     │
                  └────────────────┬─────────────────┘
                                   │
                                   ▼
                  ┌──────────────────────────────────┐
                  │  OpenEvolve loop                 │
                  │  • Claude mutates the design     │
                  │  • LLM-as-judge scores it        │
                  │  • MAP-Elites + N islands        │
                  └────────────────┬─────────────────┘
                                   │
                                   ▼
        memory/solutions/<slug>/{winner.md, runners-up/, trace.jsonl}
```

Verification is **LLM-as-judge against a rubric**. The default rubric
has four criteria — correctness (0.40), completeness (0.25), coherence
(0.20), feasibility (0.15) — auto-generated per problem and persisted
to `rubric.md`. Hand-edit it between runs to steer the search; the next
run respects the edited weights and criteria.

## Layout

```
discover_agent/
├── __init__.py
├── __main__.py             # CLI: discover-agent scan | evolve
├── agent.py                # DiscoverAgent — scan + analyze_file + reflect
├── bridge.py               # import/export CLAUDE.md
├── memory.py               # Memory, FileRecord, safe_slug
└── solution_evolve/        # evolution layer
    ├── problem.py          # ProblemSpec
    ├── rubric.py           # Rubric + auto-generation
    ├── judge.py            # LLM-as-judge scorer
    ├── seed.py             # baseline design generator
    ├── openevolve_adapter.py  # builds the OpenEvolve Config
    └── runner.py           # end-to-end orchestration
memory/
├── files/                  # one Markdown file per analyzed source file
├── heuristics/             # owned by Claude via the Anthropic Memory tool
└── solutions/<slug>/       # per-problem evolution outputs
```

## Notes

- Uses `claude-opus-4-7` with adaptive thinking. Per-file analysis uses
  `output_config.format` for schema-validated JSON; reflection uses the
  Anthropic Memory tool (`memory_20250818`) via the SDK's
  `BetaLocalFilesystemMemoryTool` so Claude itself decides what to keep,
  refine, or discard.
- The evolution layer routes OpenEvolve through Anthropic's
  OpenAI-compatible endpoint at `https://api.anthropic.com/v1/`. Default
  mutator ensemble is Opus 4.7 (weight 0.6) + Sonnet 4.6 (weight 0.4);
  default judge is Opus 4.7.
- Files larger than 200KB or non-UTF-8 are skipped.
- Memory writes are atomic (write-to-temp + rename), so an interrupted scan
  won't corrupt the store.
