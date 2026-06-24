"""
Triage and guardrails.

After we know what the buyer wants, this stage decides what a human needs to be
warned about before reaching out. It covers three categories:

  1. Security  - prompt-injection attempts, requests for owner/seller PII.
  2. Data hygiene - missing contact info, anonymous submissions.
  3. Judgement calls - unrealistic budgets, requests that need a licensed human
     (offer/negotiation advice, fiduciary questions), accessibility needs the
     data can't verify.

The agent never acts on instructions embedded in a lead; it surfaces them to the
realtor instead.
"""
from __future__ import annotations

from .config import INJECTION_MARKERS, PII_REQUEST_MARKERS
from .models import BuyerCriteria, Flag, IntentType, Listing, Severity


def detect_injection(message: str) -> Flag | None:
    low = message.lower()
    hits = [m for m in INJECTION_MARKERS if m in low]
    if hits:
        return Flag(
            "prompt_injection", Severity.CRITICAL,
            "This message contains text attempting to manipulate the automated "
            "system (e.g. \"ignore previous instructions\" / requests to dump "
            "database records). The injected instructions were ignored; only the "
            "genuine property request was processed. Treat the sender with extra "
            "scrutiny and do not share any internal or owner data.",
        )
    return None


def detect_pii_request(message: str) -> Flag | None:
    low = message.lower()
    if any(m in low for m in PII_REQUEST_MARKERS):
        return Flag(
            "pii_request", Severity.CRITICAL,
            "The sender asked for owner/seller contact details so they can "
            "\"contact them directly.\" This was refused. Owner PII is never "
            "included in a brief. This pattern can indicate a non-genuine buyer "
            "(e.g. wholesaler or data scraper).",
        )
    return None


def check_contact_info(name: str, email: str, phone: str) -> list[Flag]:
    flags: list[Flag] = []
    anon = (not name) or "anonymous" in name.lower() or "form not filled" in name.lower()
    if anon:
        flags.append(Flag(
            "anonymous_lead", Severity.WARNING,
            "Buyer did not provide a name (anonymous submission). Identity unverified.",
        ))
    if not phone.strip():
        flags.append(Flag(
            "missing_phone", Severity.WARNING,
            "No phone number provided — email is the only contact channel.",
        ))
    return flags


def check_budget_realism(criteria: BuyerCriteria, listings: list[Listing]) -> Flag | None:
    """Sanity-check the buyer's budget against real inventory matching their
    location + bedroom needs. Catches e.g. '4BR + pool + ocean view in Brickell
    for $250K', where the cheapest qualifying home is several times the budget."""
    ceiling = criteria.budget.ceiling
    if not ceiling or not criteria.neighborhoods:
        return None

    relevant = [
        l for l in listings
        if l.neighborhood in criteria.neighborhoods
        and l.price and not _has_critical_quality_flag(l)
    ]
    if criteria.min_bedrooms:
        relevant = [l for l in relevant if l.bedrooms and l.bedrooms >= criteria.min_bedrooms]
    if not relevant:
        return None

    cheapest = min(l.price for l in relevant)
    if cheapest > ceiling * 1.5:
        gap = cheapest / ceiling
        return Flag(
            "budget_mismatch", Severity.WARNING,
            f"Budget looks unrealistic for the stated criteria. The least "
            f"expensive qualifying property in {', '.join(criteria.neighborhoods)} "
            f"is ${cheapest:,} — about {gap:.1f}x the ${ceiling:,} budget. Expect "
            f"to reset expectations on price, location, size, or features.",
        )
    return None


def _has_critical_quality_flag(listing: Listing) -> bool:
    return any(f.severity == Severity.CRITICAL for f in listing.quality_flags)


def intent_flags(criteria: BuyerCriteria, listings: list[Listing]) -> list[Flag]:
    flags: list[Flag] = []

    if criteria.intent == IntentType.NEGOTIATION_ADVICE:
        flags.append(Flag(
            "needs_human_negotiation", Severity.CRITICAL,
            "This is not a property search — the buyer is asking for offer-price "
            "advice and the seller's motivation on a specific listing. This needs "
            "a licensed agent. Note: we cannot disclose a seller's motivation "
            "(it may breach the listing side's confidentiality/fiduciary duty), "
            "and offer strategy should be discussed live, not auto-generated.",
        ))
        # If the referenced listing exists and has data issues, surface them.
        if criteria.referenced_address:
            ref = _find_listing_by_address(criteria.referenced_address, listings)
            if ref and ref.quality_flags:
                for qf in ref.quality_flags:
                    if qf.severity == Severity.CRITICAL:
                        flags.append(Flag(
                            "referenced_listing_data_issue", Severity.WARNING,
                            f"The referenced listing ({ref.address}, {ref.listing_id}) "
                            f"has a data problem: {qf.message}",
                        ))

    if criteria.intent == IntentType.VAGUE_INQUIRY:
        flags.append(Flag(
            "insufficient_detail", Severity.WARNING,
            "The message is too vague to match properties confidently (no budget, "
            "location, size, or property type). Lead a discovery conversation to "
            "qualify before sending listings.",
        ))

    if criteria.intent == IntentType.INVESTMENT_SEARCH:
        flags.append(Flag(
            "investor_lead", Severity.INFO,
            "Investor lead — prioritize cash flow / cap rate, rentability and "
            "condition over lifestyle features. Rental-comp and yield analysis "
            "would strengthen the follow-up.",
        ))

    # Search/investment lead with no concrete criteria to act on.
    if criteria.intent in (IntentType.PROPERTY_SEARCH, IntentType.INVESTMENT_SEARCH) \
            and not criteria.is_actionable():
        flags.append(Flag(
            "insufficient_detail", Severity.WARNING,
            "The message doesn't specify anything concrete to match on (no budget, "
            "location, size, type, or features). Have a discovery conversation to "
            "qualify the lead before sending listings.",
        ))

    # Accessibility / proximity needs that the dataset can't verify.
    if any("Accessibility" in s or "Proximity" in s for s in criteria.soft_preferences):
        flags.append(Flag(
            "unverifiable_needs", Severity.WARNING,
            "Buyer mentioned location-quality needs (e.g. commute, walkability, "
            "single-level living, or proximity to medical/grocery) that the MLS "
            "fields don't capture. Verify these per-property before recommending.",
        ))

    return flags


def positive_signals(criteria: BuyerCriteria) -> list[Flag]:
    """Surface things that make this a strong / high-priority lead."""
    flags: list[Flag] = []
    if criteria.cash_buyer:
        flags.append(Flag(
            "cash_buyer", Severity.INFO,
            "Cash buyer — faster, more certain close. High-priority follow-up.",
        ))
    if criteria.timeline and ("urgent" in criteria.timeline.lower()
                              or "this week" in criteria.timeline.lower()):
        flags.append(Flag(
            "urgent_timeline", Severity.INFO,
            f"Time-sensitive: {criteria.timeline}. Respond quickly.",
        ))
    return flags


def _find_listing_by_address(address: str, listings: list[Listing]) -> Listing | None:
    norm = address.lower().strip()
    for l in listings:
        if norm in l.address.lower():
            return l
    return None


def run_triage(message: str, criteria: BuyerCriteria, name: str, email: str,
               phone: str, listings: list[Listing]) -> list[Flag]:
    """Top-level triage entrypoint. Returns all flags, de-duplicated and ordered
    by severity (critical first)."""
    flags: list[Flag] = []
    for f in (detect_injection(message), detect_pii_request(message)):
        if f:
            flags.append(f)
    flags.extend(check_contact_info(name, email, phone))
    flags.extend(intent_flags(criteria, listings))
    budget_flag = check_budget_realism(criteria, listings)
    if budget_flag:
        flags.append(budget_flag)
    flags.extend(positive_signals(criteria))

    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    seen = set()
    unique = []
    for f in sorted(flags, key=lambda x: order[x.severity]):
        if f.code not in seen:
            seen.add(f.code)
            unique.append(f)
    return unique
