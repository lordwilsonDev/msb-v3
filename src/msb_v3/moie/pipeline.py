"""MoIE inversion pipeline (spec §2 AIL pipeline, §19).

    claim → assumption extraction → inversion → counterfactual →
    falsifiable prediction

Deterministic and honest: assumptions come from the claim's own signal
phrases (no invented context); every assumption gets an inversion ("what
if this is wrong?") and a plain-language risk; predictions are falsifiable
observables, not vibes.
"""

from __future__ import annotations

import re
from typing import List

from msb_v3.moie.models import Assumption

# Phrases that mark a claim as *assuming* something rather than stating it.
_SIGNAL_PHRASES = (
    "assume",
    "presumably",
    "obviously",
    "of course",
    "clearly",
    "standard practice",
    "everyone knows",
    "simply",
    "straightforward",
    "no reason to think",
    "guaranteed",
    "trust",
    "should be fine",
    "shouldn't matter",
    "won't be an issue",
    "best case",
    "in practice",
    "typical",
    "usually",
)

# Deontic modals: a "should/must/will" sentence asserts the subject is
# capable + willing + unconstrained — a load-bearing implicit assumption.
_DEONTIC = re.compile(r"\b(should|must|needs? to|has to|will|can)\b", re.IGNORECASE)

_EXPLICIT_KIND = {"assume", "presumably", "obviously", "of course", "clearly", "standard practice", "everyone knows", "no reason to think", "guaranteed", "trust", "best case"}


def _sentences(claim: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", claim.strip())
    return [p.strip() for p in parts if p.strip()]


def extract_assumptions(claim: str) -> List[Assumption]:
    """Extract the claim's assumptions from its own language.

    Explicit: sentences carrying a signal phrase like "assume" / "obviously".
    Implicit: sentences carrying a deontic modal ("should", "must", "will")
    — those assert capability + willingness without evidence.
    """
    found: List[Assumption] = []
    seen: set[str] = set()
    lowered = claim.lower()
    for sentence in _sentences(claim):
        text = sentence[:240]
        key = text.lower()[:80]
        if key in seen:
            continue
        hits = [p for p in _SIGNAL_PHRASES if p in lowered]
        if hits:
            seen.add(key)
            found.append(
                Assumption(
                    text=text,
                    kind="explicit" if any(h in _EXPLICIT_KIND for h in hits) else "implicit",
                    source="pipeline",
                    confidence=min(0.9, 0.55 + 0.1 * len(hits)),
                )
            )
        elif _DEONTIC.search(sentence):
            seen.add(key)
            found.append(
                Assumption(
                    text=text,
                    kind="implicit",
                    source="pipeline",
                    confidence=0.5,
                )
            )
    return found


def invert(assumption: Assumption, *, source: str, risk: str) -> Assumption:
    """Counterfactual inversion: 'what if this assumption is wrong?'."""
    assumption.source = source
    assumption.inverted = f"what if the opposite holds — '{assumption.text}' is wrong or only partially true?"
    assumption.risk = risk or "the plan proceeds on a belief that is not load-bearing or not verified"
    return assumption


def falsifiable_predictions(risks: List[str], claim: str) -> List[str]:
    """Turn each top risk into an observable we can check after execution."""
    predictions: List[str] = []
    for risk in risks[:4]:
        # Keep it deterministic: the prediction names the observable to
        # watch, derived from the risk phrase itself.
        predictions.append(
            f"if '{risk}' materializes, execution evidence will show it within the first post-mortem window (observable, not asserted)"
        )
    if not predictions:
        predictions.append(
            f"if the claim '{claim[:120]}' holds, the planned outcome is observable without contradiction from independent evidence"
        )
    return predictions


def causal_alternatives(risks: List[str]) -> List[str]:
    """Alternative explanations for the flagged risk (adversarial breadth)."""
    if not risks:
        return []
    head = risks[0]
    return [
        f"the flagged risk '{head}' could stem from a different cause than assumed (e.g. environment, timing, or a hidden dependency)",
        f"the absence of evidence for '{head}' is not evidence of absence — an unmonitored path may mask it",
    ][:2]


def keyword_hits(text: str, keywords) -> List[str]:
    """Case-insensitive keyword hits in order, deduplicated.

    Word-like keywords use boundaries so ``port`` does not flag the harmless
    word ``report``. Punctuation-bearing controls such as ``eval(`` and
    ``0.0.0.0`` retain literal matching because they are intentional tokens,
    not natural-language words.
    """
    lowered = text.lower()
    seen: set[str] = set()
    hits: List[str] = []
    for raw_kw in keywords:
        kw = str(raw_kw).lower()
        if kw in seen:
            continue
        if re.fullmatch(r"[a-z0-9_]+(?:[ -][a-z0-9_]+)*", kw):
            matched = re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", lowered) is not None
        else:
            matched = kw in lowered
        if matched:
            seen.add(kw)
            hits.append(kw)
    return hits
