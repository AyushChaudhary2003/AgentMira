"""
Rule-based extraction of buyer criteria from free text.

This is the deterministic engine behind HeuristicLLMClient. It's intentionally
explicit (regex + keyword tables) rather than clever: every decision is
inspectable, which matters when a realtor asks "why did you think they wanted
4 bedrooms?". In production an LLM does this step; here the rules give us a
reproducible offline baseline that handles the 12 sample leads well.
"""
from __future__ import annotations

import re

from .config import (
    FEATURE_SYNONYMS,
    MUST_HAVE_MARKERS,
    NEIGHBORHOOD_ALIASES,
    PROPERTY_TYPE_KEYWORDS,
)
from .models import BudgetRange, BuyerCriteria, IntentType

# ----------------------------------------------------------------------------
# Budget
# ----------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*([mMkK])\b|\$\s*([0-9]{1,3}(?:,[0-9]{3})+)",
)


def _money_to_int(num: str, unit: str) -> int:
    val = float(num)
    if unit.lower() == "m":
        return int(val * 1_000_000)
    if unit.lower() == "k":
        return int(val * 1_000)
    return int(val)


def extract_budget(text: str) -> BudgetRange:
    """Pull a target and a stretch ceiling out of the message.

    Handles "$700K", "$2M", "up to $850K", "$2M, can stretch to $2.3M",
    "$500K-$900K", and comma-grouped figures like "$1,250,000".
    """
    amounts: list[int] = []
    for m in _MONEY_RE.finditer(text):
        if m.group(1):  # shorthand like 700K / 2M
            amounts.append(_money_to_int(m.group(1), m.group(2)))
        elif m.group(3):  # comma-grouped full number
            amounts.append(int(m.group(3).replace(",", "")))

    if not amounts:
        return BudgetRange()

    low = text.lower()
    # Treat language like "stretch to / up to / max" as the ceiling.
    has_stretch = any(k in low for k in ["stretch", "can go", "up to", "max", "flexible"])

    if len(amounts) == 1:
        only = amounts[0]
        # "up to $850K" / "max $750K" -> that figure is the ceiling, not target.
        if has_stretch or "under" in low or "below" in low:
            return BudgetRange(target=None, maximum=only)
        return BudgetRange(target=only, maximum=only)

    amounts_sorted = sorted(set(amounts))
    return BudgetRange(target=amounts_sorted[0], maximum=amounts_sorted[-1])


# ----------------------------------------------------------------------------
# Bedrooms / bathrooms
# ----------------------------------------------------------------------------

_BED_RANGE_RE = re.compile(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:bed|br\b|bedroom)")
_BED_MIN_RE = re.compile(r"(?:at least|min(?:imum)?|need|want)\s*(\d+)\s*(?:bed|br\b|bedroom)")
_BED_PLUS_RE = re.compile(r"(\d+)\s*\+\s*(?:bed|br\b|bedroom)")
_BED_SINGLE_RE = re.compile(r"(\d+)\s*(?:bed(?:room)?s?|br)\b")


def extract_bedrooms(text: str) -> tuple[int | None, int | None]:
    low = text.lower()
    m = _BED_RANGE_RE.search(low)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _BED_PLUS_RE.search(low)
    if m:
        return int(m.group(1)), None
    m = _BED_MIN_RE.search(low)
    if m:
        return int(m.group(1)), None
    m = _BED_SINGLE_RE.search(low)
    if m:
        n = int(m.group(1))
        return n, n
    return None, None


# ----------------------------------------------------------------------------
# Neighborhoods / property types / features
# ----------------------------------------------------------------------------

def extract_neighborhoods(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    # Match longer aliases first so "downtown miami" wins over "downtown".
    for alias in sorted(NEIGHBORHOOD_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", low):
            canonical = NEIGHBORHOOD_ALIASES[alias]
            if canonical not in found:
                # avoid double-adding when a substring alias also matched
                already = any(canonical == NEIGHBORHOOD_ALIASES[a] for a in NEIGHBORHOOD_ALIASES
                              if a != alias and a in low and len(a) > len(alias))
                if not already:
                    found.append(canonical)
    return found


def extract_property_types(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for kw, canonical in PROPERTY_TYPE_KEYWORDS.items():
        if canonical and re.search(rf"\b{re.escape(kw)}s?\b", low):
            if canonical not in found:
                found.append(canonical)
    return found


def extract_features(text: str) -> tuple[list[str], list[str]]:
    """Return (must_have, nice_to_have) canonical feature tags.

    A feature is promoted to must-have if a dealbreaker marker (non-negotiable,
    essential, required, ...) appears in the same clause as the feature word.
    """
    low = text.lower()
    must: list[str] = []
    nice: list[str] = []
    # Split into clauses so "pool is non-negotiable" doesn't make *everything*
    # a dealbreaker.
    clauses = re.split(r"[.;,!?]", low)
    for clause in clauses:
        is_must = any(marker in clause for marker in MUST_HAVE_MARKERS)
        for phrase in sorted(FEATURE_SYNONYMS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(phrase)}\b", clause):
                tag = FEATURE_SYNONYMS[phrase]
                target = must if is_must else nice
                if tag not in must and tag not in nice:
                    target.append(tag)
                elif is_must and tag in nice:
                    nice.remove(tag)
                    must.append(tag)
    return must, nice


# ----------------------------------------------------------------------------
# Intent / timeline / misc
# ----------------------------------------------------------------------------

def classify_intent(text: str, criteria_signal: bool) -> IntentType:
    low = text.lower()
    # Offer / negotiation advice (asking what to offer, seller motivation).
    if any(k in low for k in ["put in an offer", "offer at", "go lower", "should i offer",
                              "sellers' motivation", "seller motivation", "asking is"]):
        return IntentType.NEGOTIATION_ADVICE
    # Investor framing.
    if any(k in low for k in ["investment property", "investor", "cash-flow", "cash flow",
                              "rental propert", "rented out", "rental income", "cap rate",
                              "cash-flowing"]):
        return IntentType.INVESTMENT_SEARCH
    # Too little to act on.
    if not criteria_signal:
        return IntentType.VAGUE_INQUIRY
    return IntentType.PROPERTY_SEARCH


def extract_timeline(text: str) -> str | None:
    low = text.lower()
    patterns = [
        (r"move[- ]in (?:needed |required )?by (\w+)", lambda m: f"Move-in by {m.group(1).title()}"),
        (r"close before (\w+[- ]?\w*)", lambda m: f"Wants to close before {m.group(1)}"),
        (r"this week", lambda m: "Urgent — wants to act this week"),
        (r"next (\d+) months", lambda m: f"Active over next {m.group(1)} months"),
        (r"by (january|february|march|april|may|june|july|august|september|october|november|december)",
         lambda m: f"Timeline: by {m.group(1).title()}"),
        (r"flexible", lambda m: "Timeline flexible"),
    ]
    for pat, fmt in patterns:
        m = re.search(pat, low)
        if m:
            return fmt(m)
    return None


def extract_address(text: str) -> str | None:
    m = re.search(r"\d{1,6}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+"
                  r"(?:Road|Rd|Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Way|Court|Ct|Lane|Ln|Terrace|Highway|Circle)",
                  text)
    return m.group(0).strip() if m else None


def _detect_soft_preferences(text: str) -> list[str]:
    """Capture human-relevant context the matcher can't act on but the realtor
    should know: schools, accessibility, life situation, commute, etc."""
    low = text.lower()
    prefs: list[str] = []
    if any(k in low for k in ["school", "elementary", "kids are in", "children"]):
        prefs.append("School quality matters (children in household)")
    if any(k in low for k in ["single-story", "single story", "elevator", "don't drive",
                              "elderly", "accessib", "mobility"]):
        prefs.append("Accessibility / single-level living is a consideration")
    if any(k in low for k in ["near pharmacy", "near grocery", "medical", "walkable",
                              "commute", "close to work"]):
        prefs.append("Proximity to amenities / commute is important")
    if "first-time" in low or "first time" in low or "nervous" in low:
        prefs.append("First-time buyer — will need more guidance and reassurance")
    if "relocat" in low or "moving from" in low or "remote" in low:
        prefs.append("Relocating from out of state")
    return prefs


def extract_cash(text: str) -> bool:
    low = text.lower()
    return "cash purchase" in low or "cash buyer" in low or "all cash" in low or "be cash" in low


# ----------------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------------

def heuristic_extract(message: str) -> BuyerCriteria:
    budget = extract_budget(message)
    min_bed, max_bed = extract_bedrooms(message)
    neighborhoods = extract_neighborhoods(message)
    ptypes = extract_property_types(message)
    must, nice = extract_features(message)
    timeline = extract_timeline(message)
    cash = extract_cash(message)
    soft = _detect_soft_preferences(message)
    address = extract_address(message)

    has_signal = bool(
        budget.ceiling or min_bed or neighborhoods or ptypes or must or nice
    )
    intent = classify_intent(message, has_signal)

    crit = BuyerCriteria(
        intent=intent,
        budget=budget,
        min_bedrooms=min_bed,
        max_bedrooms=max_bed,
        neighborhoods=neighborhoods,
        property_types=ptypes,
        must_have_features=must,
        nice_to_have_features=nice,
        timeline=timeline,
        cash_buyer=cash,
        soft_preferences=soft,
        referenced_address=address if intent == IntentType.NEGOTIATION_ADVICE else None,
    )
    crit.summary = build_summary(crit)
    return crit


def criteria_from_payload(payload: dict) -> BuyerCriteria:
    """Build BuyerCriteria from an LLM JSON payload, re-validating every field
    against our known vocabularies so hallucinated values can't reach matching."""
    from .config import NEIGHBORHOOD_ALIASES as NA

    canonical_neighborhoods = set(NA.values())
    canonical_features = set(FEATURE_SYNONYMS.values())
    canonical_types = {v for v in PROPERTY_TYPE_KEYWORDS.values() if v}

    def clean_list(items, allowed):
        out = []
        for it in items or []:
            if it in allowed and it not in out:
                out.append(it)
        return out

    try:
        intent = IntentType(payload.get("intent", "property_search"))
    except ValueError:
        intent = IntentType.PROPERTY_SEARCH

    crit = BuyerCriteria(
        intent=intent,
        budget=BudgetRange(payload.get("budget_target"), payload.get("budget_maximum")),
        min_bedrooms=payload.get("min_bedrooms"),
        max_bedrooms=payload.get("max_bedrooms"),
        min_bathrooms=payload.get("min_bathrooms"),
        neighborhoods=clean_list(payload.get("neighborhoods"), canonical_neighborhoods),
        property_types=clean_list(payload.get("property_types"), canonical_types),
        must_have_features=clean_list(payload.get("must_have_features"), canonical_features),
        nice_to_have_features=clean_list(payload.get("nice_to_have_features"), canonical_features),
        timeline=payload.get("timeline"),
        cash_buyer=bool(payload.get("cash_buyer", False)),
        soft_preferences=payload.get("soft_preferences") or [],
        referenced_address=payload.get("referenced_address"),
    )
    crit.summary = build_summary(crit)
    return crit


def build_summary(c: BuyerCriteria) -> str:
    """A compact one-liner for the top of the brief."""
    parts: list[str] = []
    if c.min_bedrooms:
        if c.max_bedrooms and c.max_bedrooms != c.min_bedrooms:
            parts.append(f"{c.min_bedrooms}-{c.max_bedrooms}BR")
        else:
            parts.append(f"{c.min_bedrooms}+BR")
    if c.property_types:
        parts.append("/".join(c.property_types).lower())
    else:
        parts.append("home")
    if c.neighborhoods:
        parts.append("in " + " or ".join(c.neighborhoods))
    if c.budget.ceiling:
        if c.budget.target and c.budget.maximum and c.budget.target != c.budget.maximum:
            parts.append(f"around ${c.budget.target/1000:.0f}K (up to ${c.budget.maximum/1_000_000:.2f}M)"
                         if c.budget.maximum >= 1_000_000
                         else f"around ${c.budget.target/1000:.0f}K-${c.budget.maximum/1000:.0f}K")
        else:
            ceil = c.budget.ceiling
            parts.append(f"~${ceil/1_000_000:.2f}M" if ceil >= 1_000_000 else f"~${ceil/1000:.0f}K")
    must = c.must_have_features
    if must:
        parts.append("· must have " + ", ".join(must).lower())
    return " ".join(parts).strip()
