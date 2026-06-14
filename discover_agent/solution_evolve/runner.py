"""End-to-end orchestration: ProblemSpec → seed → OpenEvolve → winner on disk.

This is the only module that knows the full pipeline. Everything else is a
piece that can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

import anthropic
from openevolve import run_evolution

from ..memory import Memory
from .judge import score_candidate
from .openevolve_adapter import build_config
from .problem import ProblemSpec
from .rubric import Rubric, auto_generate_rubric, load_rubric
from .seed import generate_seed


def evolve(
    problem: ProblemSpec,
    *,
    memory: Memory,
    iterations: int = 30,
    population_size: int = 50,
    num_islands: int = 3,
    runner_up_count: int = 2,
    client: Optional[anthropic.Anthropic] = None,
) -> Path:
    """Run a full solution-evolution pass and return the path to ``winner.md``.

    Side effects (all under ``memory/solutions/<slug>/``):

    - ``problem.md`` — persisted problem spec.
    - ``rubric.md`` — auto-generated rubric, OR the human-edited version if
      one already exists.
    - ``seed.md`` — baseline design produced by ``generate_seed``.
    - ``winner.md`` — top scoring candidate after evolution.
    - ``runners-up/02.md`` etc. — next-best candidates for inspiration.
    - ``trace.jsonl`` — one record per generation: best/mean score, gen time.
    - ``openevolve_run/`` — raw OpenEvolve scratch (gitignored).
    """
    client = client or anthropic.Anthropic()
    slug_dir = memory.solution_dir_for(problem.slug)
    runner_up_dir = slug_dir / "runners-up"
    runner_up_dir.mkdir(parents=True, exist_ok=True)
    oe_dir = slug_dir / "openevolve_run"
    oe_dir.mkdir(parents=True, exist_ok=True)

    heuristics = memory.heuristics_text()

    # --- problem -------------------------------------------------------
    (slug_dir / "problem.md").write_text(problem.to_markdown(), encoding="utf-8")

    # --- rubric (respect human edits) ----------------------------------
    rubric_path = slug_dir / "rubric.md"
    if rubric_path.exists():
        rubric = load_rubric(rubric_path)
    else:
        rubric = auto_generate_rubric(problem, client=client)
        rubric_path.write_text(rubric.to_markdown(), encoding="utf-8")

    # --- seed (skip if one already exists; supports resume) ------------
    seed_path = slug_dir / "seed.md"
    if not seed_path.exists():
        seed_text = generate_seed(problem, heuristics, client=client)
        seed_path.write_text(seed_text, encoding="utf-8")
    else:
        seed_text = seed_path.read_text(encoding="utf-8")

    # --- evaluator (Closure capturing rubric/problem/heuristics) -------
    trace_path = slug_dir / "trace.jsonl"
    # Truncate any prior trace so re-runs start clean.
    trace_path.write_text("", encoding="utf-8")
    eval_count = {"n": 0}
    last_gen_time = {"t": time.monotonic()}

    def evaluator(program_path: str) -> dict:
        metrics = score_candidate(
            candidate_path=program_path,
            rubric=rubric,
            problem=problem,
            heuristics_text=heuristics,
            client=client,
        )
        eval_count["n"] += 1
        now = time.monotonic()
        with trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "eval": eval_count["n"],
                "combined_score": metrics["combined_score"],
                "by_criterion": metrics["by_criterion"],
                "elapsed_s": round(now - last_gen_time["t"], 2),
            }) + "\n")
        last_gen_time["t"] = now
        return metrics

    # --- evolution -----------------------------------------------------
    cfg = build_config(
        problem=problem,
        rubric=rubric,
        heuristics_text=heuristics,
        iterations=iterations,
        output_dir=oe_dir,
        population_size=population_size,
        num_islands=num_islands,
    )

    result = run_evolution(
        initial_program=str(seed_path),
        evaluator=evaluator,
        config=cfg,
        iterations=iterations,
        output_dir=str(oe_dir),
        cleanup=False,  # keep checkpoints around for debugging
    )

    # --- persist winner + runners-up -----------------------------------
    winner_path = slug_dir / "winner.md"
    winner_path.write_text(result.best_code, encoding="utf-8")

    # Clear stale runners-up from prior runs.
    for old in runner_up_dir.glob("*.md"):
        old.unlink()
    _persist_runners_up(result, runner_up_dir, top_n=runner_up_count)

    return winner_path


def _persist_runners_up(result, runner_up_dir: Path, top_n: int) -> None:
    """Write up to ``top_n`` runner-up programs as ``02.md``, ``03.md``, …

    OpenEvolve's ``EvolutionResult`` only directly exposes the best program;
    runners-up live in the program database. We pull them out best-effort
    — if the database isn't reachable from the result (older OpenEvolve
    versions), we silently skip and leave only the winner.
    """
    try:
        db = result.best_program.database if result.best_program is not None else None
    except AttributeError:
        db = None
    if db is None:
        return
    try:
        ranked = sorted(
            (p for p in db.programs.values() if p.id != result.best_program.id),
            key=lambda p: -(p.metrics.get("combined_score", 0.0)),
        )
    except Exception:
        return

    for i, prog in enumerate(ranked[:top_n], start=2):
        try:
            (runner_up_dir / f"{i:02d}.md").write_text(prog.code, encoding="utf-8")
        except Exception:
            continue
