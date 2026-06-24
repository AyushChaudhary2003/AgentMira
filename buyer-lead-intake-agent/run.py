#!/usr/bin/env python3
"""
CLI entrypoint for the Buyer Lead Intake Agent.

Examples
--------
    # Process all sample leads, write Markdown + JSON briefs to ./output
    python run.py

    # Use a custom data file and a single lead
    python run.py --inquiries data/sample_buyer_inquiries.json --lead LEAD-2026-006

    # Force the offline heuristic engine (default when no API key is set)
    python run.py --engine heuristic

    # Print the per-lead reasoning trace
    python run.py --trace

If GEMINI_API_KEY is set (and `google-genai` is installed) the extraction step
uses Gemini; otherwise it falls back to the deterministic heuristic engine, so
the command always runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agent import LeadIntakeAgent, data_quality_report, load_listings

DEFAULT_CSV = "data/miami_mls_listings.csv"
DEFAULT_INQUIRIES = "data/sample_buyer_inquiries.json"
DEFAULT_OUTDIR = "output"


def main() -> int:
    ap = argparse.ArgumentParser(description="Buyer Lead Intake Agent")
    ap.add_argument("--listings", default=DEFAULT_CSV)
    ap.add_argument("--inquiries", default=DEFAULT_INQUIRIES)
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--lead", default=None, help="Process only this lead_id")
    ap.add_argument("--engine", choices=["auto", "gemini", "heuristic"], default="auto")
    ap.add_argument("--trace", action="store_true", help="Print reasoning trace per lead")
    ap.add_argument("--no-files", action="store_true", help="Don't write output files")
    args = ap.parse_args()

    listings = load_listings(args.listings)
    dq = data_quality_report(listings)
    print(f"Loaded {dq['total_listings']} listings "
          f"({dq['listings_with_issues']} with data-quality flags: {dq['issues_by_type']})")

    with open(args.inquiries, encoding="utf-8") as fh:
        leads = json.load(fh)
    if args.lead:
        leads = [l for l in leads if l["lead_id"] == args.lead]
        if not leads:
            print(f"No lead found with id {args.lead}", file=sys.stderr)
            return 1

    prefer = None if args.engine == "auto" else args.engine
    agent = LeadIntakeAgent(listings, llm_preference=prefer)
    print(f"Engine: {agent.llm.name}\n")

    os.makedirs(args.outdir, exist_ok=True)
    combined: list[str] = []

    for lead in leads:
        result = agent.process(lead)
        n = len(result.brief.matches)
        crit_flags = sum(1 for f in result.brief.flags if f.severity.value == "critical")
        print(f"  {lead['lead_id']:<16} {result.brief.criteria.intent.value:<20} "
              f"matches={n}  critical_flags={crit_flags}")
        if args.trace:
            for step in result.trace:
                print("      " + step)

        if not args.no_files:
            base = os.path.join(args.outdir, lead["lead_id"])
            with open(base + ".md", "w", encoding="utf-8") as f:
                f.write(result.markdown)
            with open(base + ".json", "w", encoding="utf-8") as f:
                f.write(result.json)
            combined.append(result.markdown)

    if not args.no_files and combined:
        with open(os.path.join(args.outdir, "all_briefs.md"), "w", encoding="utf-8") as f:
            f.write("\n\n<div style='page-break-after: always'></div>\n\n".join(combined))
        print(f"\nWrote {len(leads)} briefs (.md + .json) and all_briefs.md to {args.outdir}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())



