"""
Loads and cleans the MLS CSV into typed Listing objects, and runs data-quality
validation. Real MLS exports are messy; this stage is where we turn "rough
edges" into explicit, inspectable flags rather than silently trusting the data.
"""
from __future__ import annotations

import csv
from typing import Optional

from .models import Flag, Listing, Severity

# Thresholds for the data-quality validator. These are conservative: we'd rather
# flag a handful of legitimate edge cases for human review than recommend a
# property with obviously broken data.
MIN_PLAUSIBLE_SQFT = 200           # a 50 sqft "single family" is a data error
MAX_PLAUSIBLE_PRICE = 60_000_000   # above this in this dataset = almost certainly junk
MAX_PLAUSIBLE_PPSF = 4_000         # $/sqft ceiling; catches price OR sqft errors


def _to_int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_features(raw: str) -> list[str]:
    if not raw:
        return []
    return [f.strip() for f in raw.split(";") if f.strip()]


def _validate(listing: Listing, raw_price: Optional[int], raw_sqft: Optional[int]) -> list[Flag]:
    """Detect data problems on a single listing. Returns a list of flags; an
    empty list means the row looked clean."""
    flags: list[Flag] = []

    if raw_sqft is not None and raw_sqft < MIN_PLAUSIBLE_SQFT:
        flags.append(Flag(
            "impossible_sqft", Severity.CRITICAL,
            f"Listed square footage ({raw_sqft} sqft) is implausibly small for a "
            f"{listing.property_type.lower()} — likely a data-entry error. Verify before quoting.",
        ))

    if raw_price is not None and raw_price > MAX_PLAUSIBLE_PRICE:
        flags.append(Flag(
            "implausible_price", Severity.CRITICAL,
            f"List price (${raw_price:,}) is far outside the range for this market "
            f"and likely a data error. Verify before quoting.",
        ))

    if raw_price and raw_sqft and raw_sqft >= MIN_PLAUSIBLE_SQFT:
        ppsf = raw_price / raw_sqft
        if ppsf > MAX_PLAUSIBLE_PPSF:
            flags.append(Flag(
                "implausible_price_per_sqft", Severity.WARNING,
                f"Price per sqft (${ppsf:,.0f}) is unusually high — double-check "
                f"the price and square footage.",
            ))

    if listing.bedrooms is None:
        flags.append(Flag(
            "missing_bedrooms", Severity.INFO,
            "Bedroom count is missing from the listing.",
        ))

    return flags


def load_listings(path: str) -> list[Listing]:
    """Read the CSV and return cleaned, validated Listing objects."""
    listings: list[Listing] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_price = _to_int(row.get("price", ""))
            raw_sqft = _to_int(row.get("sqft", ""))
            bedrooms = _to_float(row.get("bedrooms", ""))

            listing = Listing(
                listing_id=row.get("listing_id", "").strip(),
                mls_number=row.get("mls_number", "").strip(),
                address=row.get("address", "").strip(),
                neighborhood=row.get("neighborhood", "").strip(),
                city=row.get("city", "").strip(),
                price=raw_price,
                bedrooms=bedrooms,
                bathrooms=_to_float(row.get("bathrooms", "")),
                sqft=raw_sqft,
                year_built=_to_int(row.get("year_built", "")),
                property_type=row.get("property_type", "").strip(),
                listing_status=row.get("listing_status", "").strip(),
                days_on_market=_to_int(row.get("days_on_market", "")),
                description=row.get("description", "").strip(),
                features=_parse_features(row.get("features", "")),
            )
            listing.quality_flags = _validate(listing, raw_price, raw_sqft)
            listings.append(listing)
    return listings


def data_quality_report(listings: list[Listing]) -> dict:
    """Aggregate view of data problems across the whole dataset. Useful for the
    realtor's ops team, and printed by the CLI."""
    flagged = [l for l in listings if l.quality_flags]
    by_code: dict[str, int] = {}
    for l in flagged:
        for f in l.quality_flags:
            by_code[f.code] = by_code.get(f.code, 0) + 1
    return {
        "total_listings": len(listings),
        "listings_with_issues": len(flagged),
        "issues_by_type": by_code,
        "flagged_ids": [l.listing_id for l in flagged],
    }
