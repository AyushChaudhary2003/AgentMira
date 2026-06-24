"""
Assembles the final Lead Brief and renders it.

The brief is the only thing the realtor actually reads, so this module owns two
jobs: (1) decide the single most useful *next action* given the intent and
flags, and (2) render a clean, phone-friendly Markdown document (plus a JSON
twin for any downstream system).
"""
from __future__ import annotations

import json

from .matcher import find_matches
from .models import BuyerCriteria, Flag, IntentType, LeadBrief, Listing, ScoredMatch, Severity

_SEVERITY_ICON = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵"}


def _has(flags: list[Flag], code: str) -> bool:
    return any(f.code == code for f in flags)


def decide_next_action(criteria: BuyerCriteria, matches: list[ScoredMatch],
                       flags: list[Flag]) -> str:
    """Pick the single clearest next step for the realtor."""
    # Security concerns override everything.
    if _has(flags, "prompt_injection") or _has(flags, "pii_request"):
        return ("Verify this buyer is genuine before investing time. The message "
                "tried to extract internal data. If legitimate, proceed with the "
                "property request below; otherwise deprioritize.")

    if criteria.intent == IntentType.NEGOTIATION_ADVICE:
        return ("Call the buyer to discuss representation first — do not put offer "
                "numbers in writing yet. If we don't represent them, offer to. "
                "Do not disclose seller motivation. Verify the listing's data "
                "before any pricing conversation.")

    if criteria.intent == IntentType.VAGUE_INQUIRY or not criteria.is_actionable():
        return ("Reach out for a short discovery call to qualify the lead: budget, "
                "target neighborhoods, size, timeline, and financing. Don't send "
                "listings until criteria are clear.")

    urgency = ""
    if _has(flags, "urgent_timeline"):
        urgency = "Respond today — the buyer flagged urgency. "
    elif criteria.timeline:
        urgency = f"({criteria.timeline}). "

    if not matches:
        return (f"{urgency}No current inventory cleanly matches these criteria. "
                "Call to broaden the search (price, location, or must-haves) and "
                "set up a saved-search alert so they hear first when something lands.")

    channel = "Call" if criteria.cash_buyer or _has(flags, "urgent_timeline") else "Call or email"
    lead_props = ", ".join(m.listing.address for m in matches[:2])
    extra = ""
    if criteria.intent == IntentType.INVESTMENT_SEARCH:
        extra = " Bring rough rent/cap-rate comps to the conversation."
    return (f"{urgency}{channel} the buyer and lead with the top matches "
            f"({lead_props}). Confirm the open questions flagged below, then offer "
            f"to schedule showings.{extra}")


def build_brief(lead: dict, criteria: BuyerCriteria, matches: list[ScoredMatch],
                flags: list[Flag]) -> LeadBrief:
    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    flags = sorted(flags, key=lambda f: order[f.severity])
    return LeadBrief(
        lead_id=lead["lead_id"],
        received_at=lead.get("received_at", ""),
        channel=lead.get("channel", ""),
        buyer_name=lead.get("buyer_name", ""),
        buyer_email=lead.get("buyer_email", ""),
        buyer_phone=lead.get("buyer_phone", ""),
        criteria=criteria,
        matches=matches,
        flags=flags,
        next_action=decide_next_action(criteria, matches, flags),
        raw_message=lead.get("message", ""),
    )


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def _fmt_price(p) -> str:
    return f"${p:,}" if isinstance(p, (int, float)) else "n/a"


def render_markdown(brief: LeadBrief) -> str:
    c = brief.criteria
    L: list[str] = []
    L.append(f"# Lead Brief — {brief.buyer_name or 'Unknown buyer'}")
    L.append(f"**{brief.lead_id}** · {brief.channel} · received {brief.received_at}")
    L.append("")

    # Snapshot
    L.append("## At a glance")
    L.append(f"- **Looking for:** {c.summary or 'see message'}")
    L.append(f"- **Intent:** {c.intent.value.replace('_', ' ').title()}")
    contact = brief.buyer_email or "—"
    if brief.buyer_phone:
        contact += f" · {brief.buyer_phone}"
    L.append(f"- **Contact:** {contact}")
    if c.timeline:
        L.append(f"- **Timeline:** {c.timeline}")
    if c.cash_buyer:
        L.append("- **Financing:** Cash buyer")
    L.append("")

    # Flags first — this is what a busy realtor needs to see before reaching out.
    if brief.flags:
        L.append("## ⚠️ Before you reach out")
        for f in brief.flags:
            L.append(f"- {_SEVERITY_ICON[f.severity]} **{f.severity.value.upper()}** — {f.message}")
        L.append("")

    # What they want (structured)
    L.append("## What they're looking for")
    if c.neighborhoods:
        L.append(f"- **Neighborhoods:** {', '.join(c.neighborhoods)}")
    if c.min_bedrooms:
        bed = (f"{c.min_bedrooms}-{c.max_bedrooms}" if c.max_bedrooms and c.max_bedrooms != c.min_bedrooms
               else f"{c.min_bedrooms}+")
        L.append(f"- **Bedrooms:** {bed}")
    if c.property_types:
        L.append(f"- **Property type:** {', '.join(c.property_types)}")
    if c.budget.ceiling:
        if c.budget.target and c.budget.maximum and c.budget.target != c.budget.maximum:
            L.append(f"- **Budget:** {_fmt_price(c.budget.target)} target, up to {_fmt_price(c.budget.maximum)}")
        else:
            L.append(f"- **Budget:** up to {_fmt_price(c.budget.ceiling)}")
    if c.must_have_features:
        L.append(f"- **Must have:** {', '.join(c.must_have_features)}")
    if c.nice_to_have_features:
        L.append(f"- **Nice to have:** {', '.join(c.nice_to_have_features)}")
    if c.soft_preferences:
        L.append(f"- **Also noted:** {'; '.join(c.soft_preferences)}")
    L.append("")

    # Recommendations
    L.append("## Recommended properties")
    if brief.matches:
        L.append(f"_{len(brief.matches)} match(es), best first._")
        L.append("")
        for i, m in enumerate(brief.matches, 1):
            lst = m.listing
            L.append(f"### {i}. {lst.address} — {_fmt_price(lst.price)}  ·  match {m.score:.0f}/100")
            beds = int(lst.bedrooms) if lst.bedrooms is not None else "?"
            baths = lst.bathrooms if lst.bathrooms is not None else "?"
            L.append(f"{lst.neighborhood} · {lst.property_type} · {beds} bd / {baths} ba "
                     f"· {lst.sqft:,} sqft · {lst.listing_status} · {lst.mls_number}")
            for r in m.positives:
                L.append(f"- ✅ {r.detail}")
            for con in m.concerns:
                L.append(f"- ⚠️ {con}")
            L.append("")
    else:
        if c.intent == IntentType.NEGOTIATION_ADVICE:
            L.append("_No property shortlist — this lead is an offer/negotiation question "
                     "that needs a licensed agent (see flags above)._")
        elif c.intent == IntentType.VAGUE_INQUIRY or not c.is_actionable():
            L.append("_No property shortlist — not enough detail to match yet. "
                     "Qualify the buyer first (see flags above)._")
        else:
            L.append("_No current listings cleanly match these criteria (see the "
                     "diagnostic in the flags above)._")
        L.append("")

    # Next action
    L.append("## ✅ Suggested next action")
    L.append(brief.next_action)
    L.append("")
    L.append("---")
    L.append("<details><summary>Original message</summary>")
    L.append("")
    L.append("> " + brief.raw_message.replace("\n", "\n> "))
    L.append("</details>")
    return "\n".join(L)


def render_json(brief: LeadBrief) -> str:
    return json.dumps(brief.to_dict(), indent=2)
