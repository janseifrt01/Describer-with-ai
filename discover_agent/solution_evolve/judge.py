"""LLM-as-judge evaluator for a candidate solution design.

Used both directly (smoke testing, manual scoring) and as the OpenEvolve
fitness function. Returns a metrics dict with ``combined_score`` plus a
per-criterion breakdown, which OpenEvolve consumes as the evolutionary
fitness signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic

from .problem import ProblemSpec
from .rubric import Rubric


JUDGE_SYSTEM = """You are a software design reviewer scoring candidate
proposals against an explicit rubric.

You will receive:
1. The problem statement and constraints.
2. The rubric — a list of criteria with names, descriptions, and weights.
3. A codebase context block of learned heuristics about the project.
4. The candidate design (Markdown).

Score each criterion from 0.0 to 1.0:
- 0.0 = does not address this criterion at all
- 0.5 = partially addresses it; significant gaps
- 1.0 = fully addresses it; no obvious gaps

Then write 1-3 sentences of plain-English notes summarizing the candidate's
strengths and weaknesses. Be concrete — reference specific sections.

Return JSON matching the supplied schema. Do not include any prose outside
the JSON object.
"""


def _scoring_schema(rubric: Rubric) -> dict:
    """JSON-schema for the judge's structured output."""
    return {
        "type": "object",
        "properties": {
            "by_criterion": {
                "type": "object",
                "properties": {
                    c.name: {"type": "number"} for c in rubric.criteria
                },
                "required": [c.name for c in rubric.criteria],
                "additionalProperties": False,
            },
            "notes": {"type": "string"},
        },
        "required": ["by_criterion", "notes"],
        "additionalProperties": False,
    }


def _combined(by_criterion: dict[str, float], rubric: Rubric) -> float:
    total = 0.0
    norm = rubric.normalized()
    for c in norm.criteria:
        total += c.weight * float(by_criterion.get(c.name, 0.0))
    return max(0.0, min(1.0, total))


def score_candidate(
    candidate_path: str | Path,
    rubric: Rubric,
    problem: ProblemSpec,
    heuristics_text: str = "",
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Score a candidate Markdown design via LLM-as-judge.

    Returns a metrics dict in the shape OpenEvolve expects::

        {
            "combined_score": float in [0, 1],   # the fitness signal
            "by_criterion": {<name>: float, ...}, # per-criterion raw values
            "notes": str,                          # judge's prose
        }

    Per-criterion values are also surfaced as top-level keys so OpenEvolve's
    MAP-Elites can bin candidates along them.
    """
    client = client or anthropic.Anthropic()

    candidate = Path(candidate_path).read_text(encoding="utf-8")

    constraint_text = "\n".join(f"- {c}" for c in problem.constraints) or "(none)"
    criteria_text = "\n".join(
        f"- **{c.name}** (weight {c.weight:.2f}): {c.description}"
        for c in rubric.normalized().criteria
    )

    prompt = (
        f"# Problem\n\n"
        f"**Title:** {problem.title}\n\n"
        f"**Goal:** {problem.goal}\n\n"
        f"**Constraints:**\n{constraint_text}\n\n"
        f"# Rubric\n\n{criteria_text}\n\n"
        f"# Codebase heuristics\n\n"
        f"{heuristics_text or '_(none yet)_'}\n\n"
        f"# Candidate design\n\n"
        f"{candidate}\n\n"
        f"Score the candidate. Return JSON only."
    )

    response = client.messages.create(
        model=rubric.judge_model,
        max_tokens=1024,
        system=JUDGE_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _scoring_schema(rubric)}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if text_block is None:
        return {"combined_score": 0.0, "by_criterion": {}, "notes": "judge returned no text"}

    data = json.loads(text_block)
    by_criterion = {k: float(v) for k, v in data.get("by_criterion", {}).items()}
    combined = _combined(by_criterion, rubric)

    metrics: dict = {
        "combined_score": combined,
        "by_criterion": by_criterion,
        "notes": data.get("notes", ""),
    }
    # Surface each criterion as a top-level key so OpenEvolve MAP-Elites can
    # use them as feature dimensions.
    for name, val in by_criterion.items():
        metrics[name] = val
    return metrics
