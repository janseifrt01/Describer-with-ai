"""Problem specification: the user-stated goal an evolution run optimizes for.

Stored on disk as ``memory/solutions/<slug>/problem.md`` — YAML frontmatter
for structured fields, Markdown body for free-form goal text. Same pattern
as ``memory/files/<slug>.md`` so contributors recognise it on sight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml


@dataclass
class ProblemSpec:
    slug: str
    title: str
    goal: str                                # 1-3 sentences of intent
    constraints: list[str] = field(default_factory=list)
    context: str = ""                        # auto-injected codebase context

    def to_markdown(self) -> str:
        front = {
            "slug": self.slug,
            "title": self.title,
            "constraints": list(self.constraints),
        }
        front_yaml = yaml.safe_dump(front, sort_keys=True, default_flow_style=False)
        body = f"## Goal\n\n{self.goal.strip()}\n"
        if self.context.strip():
            body += f"\n## Context\n\n{self.context.strip()}\n"
        return f"---\n{front_yaml}---\n\n{body}"


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n+(.*)", re.DOTALL)


def _split_sections(body: str) -> dict[str, str]:
    """Split ``## Header`` Markdown into a header→text map."""
    out: dict[str, str] = {}
    for section in re.split(r"^## ", body, flags=re.MULTILINE):
        if not section.strip():
            continue
        head, _, rest = section.partition("\n")
        out[head.strip()] = rest.strip()
    return out


def load_problem(path: str | Path) -> ProblemSpec:
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter")
    front = yaml.safe_load(match.group(1)) or {}
    sections = _split_sections(match.group(2))
    return ProblemSpec(
        slug=front["slug"],
        title=front.get("title", front["slug"]),
        goal=sections.get("Goal", ""),
        constraints=list(front.get("constraints") or []),
        context=sections.get("Context", ""),
    )


def make_problem(
    slug: str,
    goal: str,
    *,
    title: str | None = None,
    constraints: Iterable[str] = (),
    context: str = "",
) -> ProblemSpec:
    return ProblemSpec(
        slug=slug,
        title=title or slug,
        goal=goal,
        constraints=list(constraints),
        context=context,
    )
