"""Evaluation rubric — how the LLM-as-judge scores a candidate design.

Persisted to ``memory/solutions/<slug>/rubric.md``. The rubric is
auto-generated from a ``ProblemSpec`` on first run, but the on-disk
Markdown file is the source of truth — humans can hand-edit it between
runs and the next call will respect those edits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import yaml

from .problem import ProblemSpec

DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


@dataclass
class Criterion:
    name: str
    description: str
    weight: float


@dataclass
class Rubric:
    criteria: list[Criterion] = field(default_factory=list)
    judge_model: str = DEFAULT_JUDGE_MODEL

    def normalized(self) -> "Rubric":
        """Return a copy with criterion weights summing to 1.0."""
        total = sum(c.weight for c in self.criteria)
        if total <= 0:
            return self
        return Rubric(
            criteria=[
                Criterion(c.name, c.description, c.weight / total)
                for c in self.criteria
            ],
            judge_model=self.judge_model,
        )

    def to_markdown(self) -> str:
        front = {
            "judge_model": self.judge_model,
            "criteria": [
                {"name": c.name, "weight": round(c.weight, 4), "description": c.description}
                for c in self.criteria
            ],
        }
        front_yaml = yaml.safe_dump(front, sort_keys=False, default_flow_style=False)
        body = (
            "## How candidates are scored\n\n"
            "Each criterion is scored from 0.0 to 1.0. The combined score is "
            "the weighted sum (weights above). The judge returns a JSON "
            "object with per-criterion scores plus short notes.\n"
        )
        return f"---\n{front_yaml}---\n\n{body}"


DEFAULT_CRITERIA = [
    Criterion(
        "correctness",
        "Does the design actually solve the stated problem? Does it cover the "
        "core mechanism end-to-end?",
        0.40,
    ),
    Criterion(
        "completeness",
        "Are edge cases, failure modes, and error handling addressed? Are "
        "necessary subcomponents identified?",
        0.25,
    ),
    Criterion(
        "coherence",
        "Is the design internally consistent and aligned with the codebase's "
        "existing conventions and heuristics?",
        0.20,
    ),
    Criterion(
        "feasibility",
        "Could a competent engineer actually build this in a reasonable amount "
        "of time with the codebase's existing tools?",
        0.15,
    ),
]


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n+(.*)", re.DOTALL)


def load_rubric(path: str | Path) -> Rubric:
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter")
    front = yaml.safe_load(match.group(1)) or {}
    raw_criteria = front.get("criteria") or []
    criteria = [
        Criterion(name=c["name"], description=c["description"], weight=float(c["weight"]))
        for c in raw_criteria
    ]
    return Rubric(
        criteria=criteria or list(DEFAULT_CRITERIA),
        judge_model=front.get("judge_model", DEFAULT_JUDGE_MODEL),
    )


RUBRIC_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["name", "description", "weight"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["criteria"],
    "additionalProperties": False,
}


_RUBRIC_SYSTEM = """You design evaluation rubrics for software design proposals.

Given a problem statement, output 4 to 6 scoring criteria. Always include the
four baseline criteria (correctness, completeness, coherence, feasibility) with
sensible weights, and optionally add 1-2 domain-specific criteria when the
problem clearly calls for them (e.g. "scalability" for high-throughput systems,
"security" for auth/credential flows, "observability" for production services).

Weights are positive floats and should roughly sum to 1.0; we will
normalize anyway. Keep descriptions short (one sentence) and concrete.
"""


def auto_generate_rubric(
    problem: ProblemSpec,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_JUDGE_MODEL,
) -> Rubric:
    """Ask Claude to draft a rubric tailored to this problem."""
    client = client or anthropic.Anthropic()
    prompt = (
        f"Problem title: {problem.title}\n"
        f"Goal: {problem.goal}\n"
        f"Constraints:\n"
        + "\n".join(f"- {c}" for c in problem.constraints)
        + "\n\nPropose the rubric as JSON matching the schema."
    )
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_RUBRIC_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": RUBRIC_GEN_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if text_block is None:
        return Rubric(criteria=list(DEFAULT_CRITERIA), judge_model=model)
    data = json.loads(text_block)
    criteria = [
        Criterion(name=c["name"], description=c["description"], weight=float(c["weight"]))
        for c in data.get("criteria") or []
    ]
    if not criteria:
        criteria = list(DEFAULT_CRITERIA)
    return Rubric(criteria=criteria, judge_model=model).normalized()
