"""CLI entrypoint: ``discover-agent <command>`` (or ``python -m discover_agent``).

Two top-level commands:

- ``scan`` — analyze a codebase, write per-file records to memory, run
  reflection over heuristics. Default for legacy invocation.
- ``evolve`` — explore the solution space for a user-stated problem via
  OpenEvolve, persist the winning design under
  ``memory/solutions/<slug>/``.

Both share ``--memory-dir``.
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import DiscoverAgent
from .bridge import export_claude_md, import_claude_md
from .memory import Memory
from .solution_evolve import evolve as run_evolve
from .solution_evolve.problem import make_problem


def _add_scan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "path",
        nargs="?",
        help="Root directory to scan. Omit to skip scanning (e.g. pure import/export).",
    )
    p.add_argument(
        "--reflect-every", type=int, default=10,
        help="Run a reflection pass after every N analyzed files (default: 10).",
    )
    p.add_argument(
        "--reflect-only", action="store_true",
        help="Skip scanning. Only run reflection (and any --import/--export).",
    )
    p.add_argument(
        "--import-claude-md", action="append", default=[], metavar="PATH", dest="imports",
        help="Import a CLAUDE.md (or similar) into memory/heuristics/. May be repeated.",
    )
    p.add_argument(
        "--export-claude-md", metavar="PATH", dest="export",
        help="After scanning/reflection, write a CLAUDE.md-style summary here.",
    )


def _add_evolve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--slug", required=True, help="Filesystem-safe problem identifier.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--goal", help="One- or two-sentence goal (inline problem statement).")
    src.add_argument("--problem-file", help="Path to an existing problem.md to load.")
    p.add_argument("--title", help="Human-readable problem title (defaults to slug).")
    p.add_argument("--constraint", action="append", default=[], metavar="TEXT",
                   help="Hard constraint on the solution. May be repeated.")
    p.add_argument("--iterations", type=int, default=30,
                   help="OpenEvolve iterations (default: 30).")
    p.add_argument("--population", type=int, default=50,
                   help="Population size per island (default: 50).")
    p.add_argument("--islands", type=int, default=3,
                   help="Number of evolution islands (default: 3).")


def _run_scan(args) -> dict:
    memory = Memory(args.memory_dir)
    agent = DiscoverAgent(memory=memory, reflect_every=args.reflect_every)
    result: dict = {}

    imported: list[str] = []
    for src in args.imports:
        dest = import_claude_md(src, memory)
        imported.append(str(dest.relative_to(memory.root)))
    if imported:
        result["imported"] = imported

    if args.reflect_only:
        agent.reflect()
    elif args.path:
        result["scan"] = agent.scan(args.path)

    if args.export:
        out = export_claude_md(args.export, memory)
        result["exported"] = str(out)

    result["stats"] = memory.stats()
    return result


def _run_evolve(args) -> dict:
    memory = Memory(args.memory_dir)
    if args.problem_file:
        from .solution_evolve.problem import load_problem
        problem = load_problem(args.problem_file)
    else:
        problem = make_problem(
            slug=args.slug,
            goal=args.goal,
            title=args.title or args.slug,
            constraints=args.constraint,
        )
    winner = run_evolve(
        problem,
        memory=memory,
        iterations=args.iterations,
        population_size=args.population,
        num_islands=args.islands,
    )
    return {
        "winner": str(winner),
        "solution_dir": str(winner.parent),
        "stats": memory.stats(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discover-agent",
        description="Scan a codebase or evolve a solution design.",
    )
    parser.add_argument(
        "--memory-dir", default="memory",
        help="Where to read/write memory files (default: ./memory).",
    )
    subs = parser.add_subparsers(dest="command")

    scan_p = subs.add_parser("scan", help="Analyze a codebase (per-file + heuristics).")
    _add_scan_args(scan_p)

    evolve_p = subs.add_parser("evolve", help="Evolve a solution design via OpenEvolve.")
    _add_evolve_args(evolve_p)

    # Backward compatibility: if the user invokes pre-subcommand syntax
    # (e.g. `discover-agent ./src` or `discover-agent --reflect-only`),
    # inject ``scan`` so existing scripts keep working. We deliberately
    # exclude --help / -h and the subcommand names themselves so the
    # top-level parser still handles them.
    if argv is None:
        argv = sys.argv[1:]
    SUBCOMMANDS = {"scan", "evolve"}
    HELP_FLAGS = {"-h", "--help"}
    SCAN_ONLY_FLAGS = {"--reflect-every", "--reflect-only",
                       "--import-claude-md", "--export-claude-md"}
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in HELP_FLAGS:
        first = argv[0]
        if not first.startswith("-"):
            # Positional first arg → treat as scan path.
            argv = ["scan", *argv]
        elif any(a in SCAN_ONLY_FLAGS or a.split("=", 1)[0] in SCAN_ONLY_FLAGS
                 for a in argv):
            argv = ["scan", *argv]

    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("Specify a command: scan or evolve.")

    if args.command == "scan":
        if not args.path and not args.reflect_only and not args.imports and not args.export:
            parser.error("scan: provide a path, --reflect-only, "
                         "--import-claude-md, or --export-claude-md.")
        result = _run_scan(args)
    elif args.command == "evolve":
        result = _run_evolve(args)
    else:
        parser.error(f"Unknown command: {args.command}")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
