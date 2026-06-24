# Buyer Lead Intake Agent

Turns a free-text buyer inquiry into an actionable **Lead Brief** a realtor can
read on their phone before calling the buyer back. Built for the AgentMira
engineering case study.

Given a message like *"relocating to Miami, want a 2–3BR condo in Brickell
around $700K with a gym and balcony, move-in by August"*, the agent:

1. **Extracts** structured criteria (budget, beds, neighborhoods, property
   type, must-have vs. nice-to-have features, timeline, soft preferences).
2. **Triages** the lead for things a human needs to know — prompt-injection
   attempts, requests for owner PII, unrealistic budgets, missing contact info,
   questions that require a licensed agent (e.g. offer/negotiation advice).
3. **Matches** the criteria against the MLS dataset with hard filters + weighted
   scoring, and explains *why* each property is a match.
4. **Assembles** a Markdown + JSON brief with a recommended next action.

## Quick start

No third-party packages are required for the default offline run.

```bash
cd buyer-lead-intake-agent
python3 run.py
```

This script loads `data/miami_mls_listings.csv` + `data/sample_buyer_inquiries.json` and processes all 12 leads. 

**After running the script, navigate to the `output/` directory to view the generated lead briefs.** Inside, you will find:
- Individual Markdown and JSON briefs for each lead (e.g., `LEAD-2026-0XX.md`)
- A combined `all_briefs.md` file for easy reading

### Useful flags

```bash
python3 run.py --lead LEAD-2026-006     # one lead only
python3 run.py --trace                  # print the per-lead reasoning trace
python3 run.py --engine heuristic       # force the offline extractor
python3 run.py --no-files               # print to stdout, don't write files
python3 run.py --outdir /tmp/briefs     # custom output directory
```

### Run the tests

```bash
python3 tests/test_agent.py     # or: pytest tests/
```

## Extraction engines

The intent-extraction step sits behind a small `LLMClient` interface
(`agent/llm.py`) with two implementations:

- **`HeuristicLLMClient`** (default) — a deterministic, dependency-free
  regex/keyword extractor. The 12 briefs are fully reproducible with no API key
  and no network.
- **`GeminiLLMClient`** — calls Gemini (`gemini-3.5-flash`) for extraction,
  then **re-validates** every field against the same vocabularies the heuristic
  uses, and falls back to the heuristic on any error.

Engine selection is automatic: if `GEMINI_API_KEY` is set and the `google-genai`
package is installed, `--engine auto` uses Gemini; otherwise it uses the
heuristic. You can force either with `--engine`.

```bash
export GEMINI_API_KEY=your_key_here
pip install -U google-genai
python3 run.py --engine gemini
```

The deterministic tools (matching, scoring, guardrails, data validation) do not
change between engines — only how the buyer's words become structured criteria.

## Architecture

This is a **hybrid agent**, not a single LLM call. Deterministic Python tools do
the heavy lifting; the LLM only handles the fuzzy language task (intent
extraction + summary) and its output is bounded by validation. The orchestrator
runs an explicit pipeline and records a reasoning trace for each lead.

```
buyer message
   │
   ▼
extract  ──► criteria (budget, beds, neighborhoods, type, features, timeline)
   │
   ▼
triage   ──► flags (injection, PII request, budget realism, needs-human, …)
   │
   ▼
match    ──► hard filters → weighted scoring → top-N with reasons
   │           (returns no shortlist for vague / negotiation / unactionable)
   ▼
brief    ──► Markdown + JSON + suggested next action
```

| Module | Responsibility |
|---|---|
| `agent/models.py` | Dataclasses for criteria, listings, flags, matches, briefs. Owner PII is deliberately excluded from the `Listing` model. |
| `agent/config.py` | Vocabularies: neighborhood aliases/adjacency, property-type & feature synonyms, injection/PII markers. |
| `agent/data_loader.py` | Loads + cleans the CSV; flags impossible sqft, implausible price, bad price-per-sqft, missing bedrooms. |
| `agent/extract.py` | Heuristic extraction + intent classification + summary. |
| `agent/llm.py` | `LLMClient` interface; heuristic + Gemini implementations. |
| `agent/triage.py` | Risk/judgment flags with severity levels. |
| `agent/matcher.py` | Hard filters, weighted scoring, no-match diagnosis. |
| `agent/brief.py` | Next-action logic + Markdown/JSON rendering. |
| `agent/orchestrator.py` | Wires the pipeline together, returns `AgentResult`. |
| `run.py` | CLI entrypoint. |

## Design notes

- **Privacy first.** `owner_name` / `owner_phone` from the CSV are never loaded
  into the brief-facing model and never rendered. This is also the backbone of
  the prompt-injection defense (LEAD-006 asks for exactly this and is refused).
- **The agent knows when *not* to recommend.** Vague inquiries and
  negotiation-advice requests return no shortlist and route to a human instead
  of padding the brief with low-confidence matches.
- **Messy data is surfaced, not hidden.** Listings with critical data-quality
  problems are excluded from matches and the issue is reported (e.g. the
  50-sqft, $1.25M listing referenced by LEAD-005).

See `WRITEUP.md` for the full reasoning, per-lead walkthrough, and next steps.

## Project layout

```
buyer-lead-intake-agent/
├── agent/              # the agent package
├── data/               # copies of the provided CSV + JSON
├── output/             # generated briefs (.md + .json + all_briefs.md)
├── tests/              # test suite (15 tests, stdlib unittest)
├── run.py              # CLI entrypoint
├── README.md
├── WRITEUP.md          # the written explanation (start here for "why")
└── requirements.txt
```


