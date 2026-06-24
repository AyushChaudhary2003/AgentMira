"""
Domain models for the Buyer Lead Intake Agent.

Everything that flows between the agent's stages is a typed dataclass so the
contract between extraction -> matching -> brief generation is explicit and
easy to test. Nothing here knows about LLMs or CSVs; these are pure data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class IntentType(str, Enum):
    """What the buyer is actually asking us to do.

    Not every inbound message is a property search. Classifying intent up front
    lets the agent route to the right behaviour instead of blindly matching
    listings to, say, a request for offer-negotiation advice.
    """

    PROPERTY_SEARCH = "property_search"        # standard "find me a home" lead
    INVESTMENT_SEARCH = "investment_search"    # investor looking for yield/units
    NEGOTIATION_ADVICE = "negotiation_advice"  # asking what to offer on a listing
    VAGUE_INQUIRY = "vague_inquiry"            # too little to act on; needs discovery
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"          # nice-to-know context
    WARNING = "warning"    # the realtor should read this before reaching out
    CRITICAL = "critical"  # do not proceed without human judgement


@dataclass
class Flag:
    """A single thing the realtor (or a human) should be aware of."""

    code: str
    severity: Severity
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity.value, "message": self.message}


@dataclass
class BudgetRange:
    """Buyer budget. `target` is what they'd ideally pay, `maximum` is their
    stretch ceiling. Either may be None when the buyer didn't say."""

    target: Optional[int] = None
    maximum: Optional[int] = None

    @property
    def ceiling(self) -> Optional[int]:
        """Hard ceiling we screen against (stretch if given, else target)."""
        return self.maximum or self.target

    def to_dict(self) -> dict:
        return {"target": self.target, "maximum": self.maximum}


@dataclass
class BuyerCriteria:
    """Structured representation of what the buyer wants, extracted from the
    free-text message. This is the output of the extraction stage and the input
    to the matcher."""

    intent: IntentType = IntentType.PROPERTY_SEARCH
    budget: BudgetRange = field(default_factory=BudgetRange)
    min_bedrooms: Optional[int] = None
    max_bedrooms: Optional[int] = None
    min_bathrooms: Optional[float] = None
    neighborhoods: list[str] = field(default_factory=list)
    property_types: list[str] = field(default_factory=list)
    must_have_features: list[str] = field(default_factory=list)   # dealbreakers
    nice_to_have_features: list[str] = field(default_factory=list)
    timeline: Optional[str] = None
    cash_buyer: bool = False
    # Free-text signals the realtor should know but we can't structure/match on
    # (schools, accessibility, proximity to amenities, life context, etc.)
    soft_preferences: list[str] = field(default_factory=list)
    referenced_address: Optional[str] = None  # for negotiation-type leads
    summary: str = ""                          # one-line human summary

    def is_actionable(self) -> bool:
        """True if there's at least one concrete constraint to match on. A lead
        with no budget, location, size, type, or feature signal can't be matched
        responsibly — it needs a discovery conversation, not a random shortlist."""
        return any([
            self.budget.ceiling,
            self.min_bedrooms,
            self.neighborhoods,
            self.property_types,
            self.must_have_features,
            self.nice_to_have_features,
        ])

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "budget": self.budget.to_dict(),
            "min_bedrooms": self.min_bedrooms,
            "max_bedrooms": self.max_bedrooms,
            "min_bathrooms": self.min_bathrooms,
            "neighborhoods": self.neighborhoods,
            "property_types": self.property_types,
            "must_have_features": self.must_have_features,
            "nice_to_have_features": self.nice_to_have_features,
            "timeline": self.timeline,
            "cash_buyer": self.cash_buyer,
            "soft_preferences": self.soft_preferences,
            "referenced_address": self.referenced_address,
            "summary": self.summary,
        }


@dataclass
class Listing:
    """A cleaned MLS listing. `quality_flags` carries any data problems we
    detected while loading (e.g. impossible square footage)."""

    listing_id: str
    mls_number: str
    address: str
    neighborhood: str
    city: str
    price: Optional[int]
    bedrooms: Optional[float]
    bathrooms: Optional[float]
    sqft: Optional[int]
    year_built: Optional[int]
    property_type: str
    listing_status: str
    days_on_market: Optional[int]
    description: str
    features: list[str]
    quality_flags: list[Flag] = field(default_factory=list)
    # PII deliberately kept off the public model. Owner contact lives only in the
    # raw row and is never surfaced in a brief (see triage / injection handling).

    def to_dict(self) -> dict:
        return {
            "listing_id": self.listing_id,
            "mls_number": self.mls_number,
            "address": self.address,
            "neighborhood": self.neighborhood,
            "price": self.price,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "sqft": self.sqft,
            "year_built": self.year_built,
            "property_type": self.property_type,
            "listing_status": self.listing_status,
            "days_on_market": self.days_on_market,
            "features": self.features,
        }


@dataclass
class MatchReason:
    """One line of 'why this property fits (or doesn't)'."""

    dimension: str   # e.g. "Budget", "Location"
    positive: bool
    detail: str


@dataclass
class ScoredMatch:
    """A listing scored against a buyer's criteria."""

    listing: Listing
    score: float                         # 0-100
    reasons: list[MatchReason] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)  # over-budget, status, etc.

    @property
    def positives(self) -> list[MatchReason]:
        return [r for r in self.reasons if r.positive]

    def to_dict(self) -> dict:
        return {
            "listing": self.listing.to_dict(),
            "score": round(self.score, 1),
            "match_reasons": [r.detail for r in self.reasons if r.positive],
            "concerns": self.concerns,
        }


@dataclass
class LeadBrief:
    """The final artifact the realtor reads."""

    lead_id: str
    received_at: str
    channel: str
    buyer_name: str
    buyer_email: str
    buyer_phone: str
    criteria: BuyerCriteria
    matches: list[ScoredMatch]
    flags: list[Flag]
    next_action: str
    raw_message: str

    def to_dict(self) -> dict:
        return {
            "lead_id": self.lead_id,
            "received_at": self.received_at,
            "channel": self.channel,
            "buyer": {
                "name": self.buyer_name,
                "email": self.buyer_email,
                "phone": self.buyer_phone,
            },
            "criteria": self.criteria.to_dict(),
            "recommended_properties": [m.to_dict() for m in self.matches],
            "flags": [f.to_dict() for f in self.flags],
            "suggested_next_action": self.next_action,
        }
