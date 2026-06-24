"""
The matching engine.

Given structured BuyerCriteria and the cleaned listings, produce a ranked,
scored shortlist with human-readable reasons. Two-stage design:

  1. Hard filters (disqualifiers): things a buyer won't bend on — must-have
     features, an absolute budget ceiling, minimum bedrooms, and listings we
     don't trust (critical data-quality flags). These remove candidates.

  2. Weighted soft scoring: everything that's a matter of degree — how well the
     price/location/size/features line up — produces a 0-100 score used to rank
     the survivors.

We deliberately keep a few "slightly over budget" properties (with a concern
note) rather than hard-cutting at the exact ceiling, because in practice a great
home 5% over budget is worth showing a realtor.
"""
from __future__ import annotations

from .config import NEIGHBORHOOD_ADJACENCY
from .models import BuyerCriteria, IntentType, Listing, MatchReason, ScoredMatch, Severity

# Scoring weights (sum to 100).
W_LOCATION = 30
W_BUDGET = 25
W_BEDROOMS = 20
W_TYPE = 10
W_FEATURES = 15

BUDGET_TOLERANCE = 0.10   # allow up to 10% over the ceiling, flagged
MIN_RECOMMEND_SCORE = 45  # below this we don't call it a recommendation
TOP_N = 5

# property types that are reasonable substitutes for one another
TYPE_COMPATIBILITY = {
    "Condo": {"Townhouse"},
    "Townhouse": {"Condo", "Single Family"},
    "Single Family": {"Villa", "Townhouse"},
    "Villa": {"Single Family"},
    "Multi-Family": {"Condo"},
}


def _passes_hard_filters(listing: Listing, c: BuyerCriteria) -> bool:
    # Never recommend listings with critical data problems.
    if any(f.severity == Severity.CRITICAL for f in listing.quality_flags):
        return False
    if listing.price is None:
        return False

    # Budget ceiling (with tolerance).
    ceiling = c.budget.ceiling
    if ceiling and listing.price > ceiling * (1 + BUDGET_TOLERANCE):
        return False

    # Minimum bedrooms is a firm requirement.
    if c.min_bedrooms is not None:
        if listing.bedrooms is None or listing.bedrooms < c.min_bedrooms:
            return False

    # Must-have features are dealbreakers.
    listing_features = {f.lower() for f in listing.features}
    for feat in c.must_have_features:
        if feat.lower() not in listing_features:
            return False

    return True


def _score_location(listing: Listing, c: BuyerCriteria) -> tuple[float, MatchReason | None]:
    if not c.neighborhoods:
        return 1.0, None  # buyer is open; don't penalize
    if listing.neighborhood in c.neighborhoods:
        return 1.0, MatchReason("Location", True, f"In {listing.neighborhood} (requested)")
    # Adjacent neighborhood = partial credit.
    for want in c.neighborhoods:
        if listing.neighborhood in NEIGHBORHOOD_ADJACENCY.get(want, set()):
            return 0.5, MatchReason(
                "Location", True,
                f"In {listing.neighborhood}, adjacent to requested {want}")
    return 0.0, None


def _score_budget(listing: Listing, c: BuyerCriteria) -> tuple[float, MatchReason | None, str | None]:
    ceiling = c.budget.ceiling
    if not ceiling:
        return 1.0, None, None
    price = listing.price
    target = c.budget.target or ceiling
    concern = None

    if price <= target:
        score = 1.0
        reason = MatchReason("Budget", True,
                             f"${price:,} — within target budget (${target:,})")
    elif price <= ceiling:
        # between target and stretch max
        score = 0.85
        reason = MatchReason("Budget", True,
                             f"${price:,} — within stretch budget (${ceiling:,})")
    else:
        # within tolerance band above ceiling
        over = (price - ceiling) / ceiling
        score = max(0.0, 0.6 - over * 3)
        reason = MatchReason("Budget", True, f"${price:,}")
        concern = f"${price:,} is {over*100:.0f}% over the ${ceiling:,} budget ceiling."
    return score, reason, concern


def _score_bedrooms(listing: Listing, c: BuyerCriteria) -> tuple[float, MatchReason | None]:
    if c.min_bedrooms is None or listing.bedrooms is None:
        return 1.0, None
    beds = int(listing.bedrooms)
    lo = c.min_bedrooms
    hi = c.max_bedrooms or c.min_bedrooms
    if lo <= beds <= hi:
        return 1.0, MatchReason("Size", True, f"{beds} bedrooms (requested {lo}-{hi})")
    if beds > hi:
        # more rooms than asked — still good, mild diminishing return
        return 0.9, MatchReason("Size", True, f"{beds} bedrooms (more than requested)")
    return 0.6, MatchReason("Size", True, f"{beds} bedrooms")


def _score_type(listing: Listing, c: BuyerCriteria) -> tuple[float, MatchReason | None]:
    if not c.property_types:
        return 1.0, None
    if listing.property_type in c.property_types:
        return 1.0, MatchReason("Type", True, f"{listing.property_type} (requested)")
    for want in c.property_types:
        if listing.property_type in TYPE_COMPATIBILITY.get(want, set()):
            return 0.5, MatchReason("Type", True,
                                    f"{listing.property_type} (similar to requested {want})")
    return 0.2, None


def _score_features(listing: Listing, c: BuyerCriteria) -> tuple[float, list[MatchReason]]:
    wanted = c.must_have_features + c.nice_to_have_features
    if not wanted:
        return 1.0, []
    listing_features = {f.lower() for f in listing.features}
    matched = [f for f in wanted if f.lower() in listing_features]
    reasons = []
    if matched:
        reasons.append(MatchReason("Features", True, "Has: " + ", ".join(matched)))
    return len(matched) / len(wanted), reasons


def score_listing(listing: Listing, c: BuyerCriteria) -> ScoredMatch:
    loc_s, loc_r = _score_location(listing, c)
    bud_s, bud_r, bud_concern = _score_budget(listing, c)
    bed_s, bed_r = _score_bedrooms(listing, c)
    typ_s, typ_r = _score_type(listing, c)
    feat_s, feat_rs = _score_features(listing, c)

    total = (loc_s * W_LOCATION + bud_s * W_BUDGET + bed_s * W_BEDROOMS
             + typ_s * W_TYPE + feat_s * W_FEATURES)

    reasons = [r for r in (loc_r, bud_r, bed_r, typ_r) if r] + feat_rs
    concerns: list[str] = []
    if bud_concern:
        concerns.append(bud_concern)
    if listing.listing_status != "Active":
        concerns.append(
            f"Status is '{listing.listing_status}' — may not be available; confirm first.")
    # surface non-critical data flags as concerns
    for qf in listing.quality_flags:
        if qf.severity != Severity.CRITICAL:
            concerns.append(qf.message)

    return ScoredMatch(listing=listing, score=total, reasons=reasons, concerns=concerns)


def find_matches(criteria: BuyerCriteria, listings: list[Listing],
                 top_n: int = TOP_N) -> list[ScoredMatch]:
    """Return the top scored matches above the recommendation threshold.

    Returns an empty list for intents where listing-matching isn't appropriate
    (vague inquiries, negotiation advice) — those are handled in the brief.
    """
    if criteria.intent in (IntentType.VAGUE_INQUIRY, IntentType.NEGOTIATION_ADVICE):
        return []
    if not criteria.is_actionable():
        return []

    candidates = [l for l in listings if _passes_hard_filters(l, criteria)]
    scored = [score_listing(l, criteria) for l in candidates]
    scored = [s for s in scored if s.score >= MIN_RECOMMEND_SCORE]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:top_n]


def diagnose_no_matches(criteria: BuyerCriteria, listings: list[Listing]) -> str | None:
    """When a search returns nothing, work out which constraint is binding by
    applying the buyer's filters one at a time and seeing where inventory drops
    to zero. This tells the realtor exactly what to renegotiate with the buyer."""
    pool = [l for l in listings
            if not any(f.severity == Severity.CRITICAL for f in l.quality_flags) and l.price]

    steps: list[tuple[str, callable]] = []
    if criteria.neighborhoods:
        steps.append((f"in {', '.join(criteria.neighborhoods)}",
                      lambda l: l.neighborhood in criteria.neighborhoods))
    if criteria.min_bedrooms:
        steps.append((f"{criteria.min_bedrooms}+ bedrooms",
                      lambda l: l.bedrooms and l.bedrooms >= criteria.min_bedrooms))
    for feat in criteria.must_have_features:
        steps.append((f"{feat}", lambda l, ft=feat: ft.lower() in {x.lower() for x in l.features}))

    current = pool
    survived_before_budget = current
    constraints_applied: list[str] = []
    for label, pred in steps:
        nxt = [l for l in current if pred(l)]
        if not nxt:
            return (f"No inventory matches all requirements. The constraint that "
                    f"eliminates everything is '{label}': "
                    f"{len(current)} listing(s) met the earlier criteria "
                    f"({', '.join(constraints_applied) or 'none'}) but none also have {label}.")
        constraints_applied.append(label)
        current = nxt
        survived_before_budget = nxt

    # Everything but budget passed — so budget is the binding constraint.
    ceiling = criteria.budget.ceiling
    if ceiling and survived_before_budget:
        cheapest = min(l.price for l in survived_before_budget)
        if cheapest > ceiling:
            return (f"{len(survived_before_budget)} listing(s) match all non-price "
                    f"requirements, but the cheapest is ${cheapest:,} — well above the "
                    f"${ceiling:,} budget. Budget is the binding constraint; reset price "
                    f"expectations or relax location/size/features.")
    return None
