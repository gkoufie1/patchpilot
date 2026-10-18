"""Turn the computed plan into a short write-up for the change review.

The planner does the scheduling; Claude only explains it. The model gets the
plan as JSON and writes the summary a human would otherwise draft by hand:
what runs when, what to confirm with which customer, and the risks.
Requires `pip install patchpilot[ai]` and an Anthropic API key (or an
`ant auth login` profile).
"""
from __future__ import annotations

import json
import logging

from .models import Plan

log = logging.getLogger("patchpilot")

MODEL = "claude-opus-5-5"

SYSTEM = """You write patch-cycle summaries for an application operations team that \
hosts a spare-parts planning product. Customers run overnight batch workflows; a patch \
window that overlaps one stops customers working, so windows were placed in gaps \
between predicted runs. You will receive the computed plan as JSON.

Write for the team lead and the change advisory board, in plain Markdown:
1. A two-sentence overview of the cycle.
2. A short list of the windows in order, local time first, with customer and environment.
3. "Confirm before the window": anything a human should check with a customer, \
especially workflows whose runtime has drifted upward (they can grow into the window), \
and environments that could not be scheduled.
4. "Risks": at most three, concrete.

Use only facts in the JSON. Do not invent customers, times or numbers. Keep it under 250 words."""


def summarize(plan: Plan) -> str | None:
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic not installed; skipping AI summary (pip install patchpilot[ai])")
        return None

    client = anthropic.Anthropic()
    payload = plan.to_dict()
    payload.pop("ai_summary", None)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            output_config={"effort": "medium"},
            # If a safety classifier declines, re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        )
    except anthropic.AuthenticationError:
        log.warning("Anthropic credentials missing or invalid; skipping AI summary")
        return None
    except anthropic.RateLimitError:
        log.warning("Anthropic rate limit hit; skipping AI summary")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Anthropic API error %s; skipping AI summary", exc.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("could not reach the Anthropic API; skipping AI summary")
        return None

    if response.stop_reason == "refusal":
        log.warning("summary request was declined; plan is unchanged")
        return None
    return "".join(b.text for b in response.content if b.type == "text").strip() or None
