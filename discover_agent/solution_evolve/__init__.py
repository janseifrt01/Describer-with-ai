"""Solution Evolution layer for the Discover Agent.

This package adds a second capability alongside the existing source-code
analysis: given a user-stated problem or design goal, run an evolutionary
search over candidate solution designs and surface the best one — plus
top-K runners-up — backed by Anthropic's Claude as the mutation LLM and
LLM-as-judge as the fitness function.

Public entrypoints:

- ``ProblemSpec`` / ``Rubric`` — typed inputs persisted as Markdown + YAML.
- ``score_candidate`` — the LLM-as-judge scoring function.
- ``generate_seed`` — baseline design produced via a single Claude call.
- ``evolve`` — top-level orchestration over OpenEvolve.
"""

from .judge import score_candidate
from .problem import ProblemSpec, load_problem
from .rubric import Criterion, Rubric, auto_generate_rubric, load_rubric
from .runner import evolve
from .seed import generate_seed

__all__ = [
    "Criterion",
    "ProblemSpec",
    "Rubric",
    "auto_generate_rubric",
    "evolve",
    "generate_seed",
    "load_problem",
    "load_rubric",
    "score_candidate",
]
