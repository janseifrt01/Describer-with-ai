"""Generate the baseline ("seed") design that OpenEvolve starts evolving from.

One Claude call with adaptive thinking. The seed is the only point in the
pipeline where heuristics from ``memory/heuristics/`` flow into the design
itself (not just into scoring), so we make sure the system prompt includes
them — if any exist.
"""

from __future__ import annotations

import anthropic

from .problem import ProblemSpec


SEED_SYSTEM = """You produce concrete, mid-level software design proposals
in Markdown.

Given a problem statement, write a single self-contained design document
covering, at minimum:

- **Goal** — one paragraph restating the problem in your own words.
- **Approach** — the chosen design at a high level; mention key components
  and how they interact.
- **Components** — bulleted breakdown of new and modified pieces, each with
  a one-line responsibility.
- **Steps** — ordered implementation plan, each step concrete enough that a
  developer can act on it.
- **Risks / open questions** — 2-5 items worth flagging before implementation.

Be concrete. Avoid generic platitudes ("scalable", "robust"). Cite the
codebase's existing patterns, modules, or conventions wherever they apply —
use the learned heuristics shown below.

Output the design as plain Markdown. No preamble, no JSON, no code fences
around the whole document.
"""


def generate_seed(
    problem: ProblemSpec,
    heuristics_text: str = "",
    *,
    client: anthropic.Anthropic | None = None,
    model: str = "claude-opus-4-7",
) -> str:
    """Return the baseline design as Markdown text."""
    client = client or anthropic.Anthropic()

    constraint_text = "\n".join(f"- {c}" for c in problem.constraints) or "(none)"
    system_blocks = [
        {
            "type": "text",
            "text": SEED_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "Learned heuristics for this codebase:\n\n"
                + (heuristics_text or "(none yet — this is the first pass)")
            ),
        },
    ]

    user_prompt = (
        f"# Problem\n\n"
        f"**Title:** {problem.title}\n\n"
        f"**Goal:** {problem.goal}\n\n"
        f"**Constraints:**\n{constraint_text}\n\n"
        + (f"**Context:**\n\n{problem.context}\n\n" if problem.context.strip() else "")
        + "Draft the design now."
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text.strip() + "\n"
