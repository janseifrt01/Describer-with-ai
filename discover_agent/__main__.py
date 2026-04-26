"""CLI entrypoint: ``discover-agent <path>`` (or ``python -m discover_agent``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import DiscoverAgent
from .bridge import export_claude_md, import_claude_md
from .memory import Memory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discover-agent",
        description="Scan a codebase, store findings to memory, self-improve via reflection.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Root directory to scan. Omit to skip scanning (e.g. for pure import/export).",
    )
    parser.add_argument(
        "--memory-dir",
        default="memory",
        help="Where to read/write memory files (default: ./memory).",
    )
    parser.add_argument(
        "--reflect-every",
        type=int,
        default=10,
        help="Run a reflection pass after every N analyzed files (default: 10).",
    )
    parser.add_argument(
        "--reflect-only",
        action="store_true",
        help="Skip scanning. Only run reflection (and any --import/--export).",
    )
    parser.add_argument(
        "--import-claude-md",
        action="append",
        default=[],
        metavar="PATH",
        dest="imports",
        help="Import a CLAUDE.md (or similar Markdown notes file) into "
             "memory/heuristics/ before scanning. May be repeated.",
    )
    parser.add_argument(
        "--export-claude-md",
        metavar="PATH",
        dest="export",
        help="After scanning/reflection, write a CLAUDE.md-style summary here.",
    )
    args = parser.parse_args(argv)

    if not args.path and not args.reflect_only and not args.imports and not args.export:
        parser.error("Nothing to do — provide a path to scan, --reflect-only, "
                     "--import-claude-md, or --export-claude-md.")

    memory = Memory(args.memory_dir)
    agent = DiscoverAgent(memory=memory, reflect_every=args.reflect_every)

    result: dict = {}

    # 1. Imports first, so reflection sees the seed material.
    imported: list[str] = []
    for src in args.imports:
        dest = import_claude_md(src, memory)
        imported.append(str(dest.relative_to(memory.root)))
    if imported:
        result["imported"] = imported

    # 2. Scan or reflect.
    if args.reflect_only:
        agent.reflect()
    elif args.path:
        result["scan"] = agent.scan(args.path)

    # 3. Export last, so the summary reflects everything that just happened.
    if args.export:
        out = export_claude_md(args.export, memory)
        result["exported"] = str(out)

    result["stats"] = memory.stats()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
