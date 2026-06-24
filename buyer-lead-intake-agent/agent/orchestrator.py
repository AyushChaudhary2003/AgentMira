"""
The orchestrator — the "agent" itself.

It runs a fixed pipeline of tools and records a trace of what it decided at each
step. The pipeline is deliberately explicit rather than a free-form LLM loop:
for lead intake, the sequence of operations is well understood, and a
deterministic orchestration with a swappable LLM reasoning step is easier to
trust, test, and debug than letting a model decide control flow.

Pipeline:
    extract -> triage -> match -> assemble brief

Each lead's trace makes the agent's reasoning auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .brief import build_brief, render_json, render_markdown
from .llm import LLMClient, get_llm_client
from .matcher import diagnose_no_matches, find_matches
from .models import Flag, IntentType, LeadBrief, Listing, Severity
from .triage import run_triage


@dataclass
class AgentResult:
    brief: LeadBrief
    markdown: str
    json: str
    trace: list[str] = field(default_factory=list)


class LeadIntakeAgent:
    def __init__(self, listings: list[Listing], llm: LLMClient | None = None,
                 llm_preference: str | None = None):
        self.listings = listings
        self.llm = llm or get_llm_client(prefer=llm_preference)

    def process(self, lead: dict) -> AgentResult:
        trace: list[str] = []
        message = lead.get("message", "")

        # 1. Extract structured intent (LLM or heuristic).
        criteria = self.llm.extract_criteria(message, context={"channel": lead.get("channel")})
        criteria.summary = self.llm.write_summary(criteria, message)
        trace.append(f"[extract:{self.llm.name}] intent={criteria.intent.value}, "
                     f"beds={criteria.min_bedrooms}, budget={criteria.budget.ceiling}, "
                     f"neighborhoods={criteria.neighborhoods}, "
                     f"must_have={criteria.must_have_features}")

        # 2. Triage / guardrails.
        flags = run_triage(
            message, criteria,
            lead.get("buyer_name", ""), lead.get("buyer_email", ""),
            lead.get("buyer_phone", ""), self.listings,
        )
        trace.append("[triage] flags=" + (", ".join(f.code for f in flags) or "none"))

        # 3. Match (skipped internally for non-search intents).
        matches = find_matches(criteria, self.listings)
        if criteria.intent in (IntentType.VAGUE_INQUIRY, IntentType.NEGOTIATION_ADVICE):
            trace.append(f"[match] skipped (intent={criteria.intent.value})")
        else:
            trace.append(f"[match] {len(matches)} recommendation(s) above threshold"
                         + (f"; top={matches[0].score:.0f}" if matches else ""))
            # If a genuine search found nothing, diagnose the binding constraint.
            if not matches:
                diagnosis = diagnose_no_matches(criteria, self.listings)
                if diagnosis:
                    flags.append(Flag("no_match_diagnosis", Severity.WARNING, diagnosis))
                    trace.append("[match] no-match diagnosis attached")

        # 4. Assemble brief.
        brief = build_brief(lead, criteria, matches, flags)
        trace.append("[brief] assembled; next_action set")

        return AgentResult(
            brief=brief,
            markdown=render_markdown(brief),
            json=render_json(brief),
            trace=trace,
        )
