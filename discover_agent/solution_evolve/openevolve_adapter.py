"""Build an OpenEvolve ``Config`` tailored to our solution-design domain.

OpenEvolve assumes its "programs" are code by default. We coax it into
treating Markdown designs as the program text by overriding ``file_suffix``
to ``.md`` and ``language`` to ``markdown``, and by replacing the system
message with one written for designs (not algorithms).

OpenEvolve talks to LLMs over an OpenAI-compatible endpoint. Anthropic
ships such an endpoint at ``https://api.anthropic.com/v1/`` and accepts
Claude model strings directly, so we route the run through there using
the user's existing ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from openevolve.config import (
    Config,
    DatabaseConfig,
    LLMConfig,
    LLMModelConfig,
    PromptConfig,
)

from .problem import ProblemSpec
from .rubric import Rubric


ANTHROPIC_OPENAI_BASE = "https://api.anthropic.com/v1/"


EVOLVE_SYSTEM_TEMPLATE = """You evolve software design proposals written in Markdown.

You will be shown an existing candidate design and asked to propose a
strictly better one — same shape, but tighter, more concrete, or covering
a gap the current version misses.

Optimize for the rubric below; the rubric is the fitness function.

# Problem

**Title:** {title}
**Goal:** {goal}

**Constraints:**
{constraints}

# Rubric (what the judge scores)

{criteria}

# Codebase heuristics

{heuristics}

# Mutation guidance

- Keep the Markdown structure (Goal / Approach / Components / Steps / Risks).
- Prefer substantive edits (new sections, sharper steps, better risk
  analysis) over cosmetic rewrites.
- Don't trade away strengths to fix weaknesses — the judge scores all
  criteria. Improve weak ones while preserving strong ones.
- Stay specific to *this* codebase: cite the listed heuristics when they
  apply. Generic boilerplate scores poorly on coherence.
"""


def _fmt_constraints(problem: ProblemSpec) -> str:
    return "\n".join(f"- {c}" for c in problem.constraints) or "(none)"


def _fmt_criteria(rubric: Rubric) -> str:
    return "\n".join(
        f"- **{c.name}** (weight {c.weight:.2f}): {c.description}"
        for c in rubric.normalized().criteria
    )


def build_config(
    problem: ProblemSpec,
    rubric: Rubric,
    heuristics_text: str,
    *,
    iterations: int,
    output_dir: Path,
    mutator_model: str = "claude-opus-4-7",
    secondary_model: str = "claude-sonnet-4-6",
    api_key: Optional[str] = None,
    population_size: int = 50,
    num_islands: int = 3,
) -> Config:
    """Construct an OpenEvolve ``Config`` for this evolution run.

    Everything that could vary per problem lives here so the runner stays a
    thin orchestrator.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set — OpenEvolve needs API credentials to "
            "drive the mutation LLM."
        )

    system_message = EVOLVE_SYSTEM_TEMPLATE.format(
        title=problem.title,
        goal=problem.goal,
        constraints=_fmt_constraints(problem),
        criteria=_fmt_criteria(rubric),
        heuristics=(heuristics_text.strip() or "(no learned heuristics yet)"),
    )

    cfg = Config()
    cfg.max_iterations = iterations
    cfg.file_suffix = ".md"
    cfg.language = "markdown"
    cfg.log_dir = str(output_dir)
    cfg.diff_based_evolution = True
    cfg.checkpoint_interval = max(5, iterations // 6)

    cfg.llm = LLMConfig(
        api_base=ANTHROPIC_OPENAI_BASE,
        api_key=api_key,
        temperature=0.7,
        max_tokens=4096,
        models=[
            LLMModelConfig(name=mutator_model, weight=0.6),
            LLMModelConfig(name=secondary_model, weight=0.4),
        ],
    )

    cfg.prompt = PromptConfig(
        system_message=system_message,
        num_top_programs=3,
        num_diverse_programs=2,
        include_artifacts=True,
    )

    cfg.database = DatabaseConfig(
        population_size=population_size,
        num_islands=num_islands,
        archive_size=max(20, population_size // 3),
        # Use score + diversity as feature dimensions so the MAP-Elites grid
        # spreads candidates across quality and novelty, not just code length.
        feature_dimensions=["score", "diversity"],
    )

    return cfg
