"""CLI entrypoint: ``discover-agent <path>`` (or ``python -m discover_agent``)."""

from __future__ import annotations

import argparse
import json
import sys

from .agent import DiscoverAgent
from .memory import Memory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discover-agent",
        description="Scan a codebase, store findings to memory, self-improve via reflection.",
    )
    parser.add_argument("path", help="Root directory to scan.")
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
        help="Skip scanning. Run reflection over existing memory only.",
    )
    args = parser.parse_args(argv)

    memory = Memory(args.memory_dir)
    agent = DiscoverAgent(memory=memory, reflect_every=args.reflect_every)

    if args.reflect_only:
        agent.reflect()
        print(json.dumps(
            {
                "heuristics_files": [str(p.relative_to(memory.heuristics_dir))
                                     for p in memory.heuristics_files()],
                **memory.stats(),
            },
            indent=2,
        ))
        return 0

    summary = agent.scan(args.path)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
