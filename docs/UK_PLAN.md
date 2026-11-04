# Plan: UK (London) support — 0.11.0

Status: built in 0.11.0 (2026-11-04). Decisions: Reed via its official API (no page-reading of Reed or
Totaljobs); documents are called "CV" for UK users; UK only for now (other countries can slot
into the same structure later).

## Core idea: one "market" setting

A new module, `src/applypilot/market.py`, describes each country (Australia, United Kingdom).
Every place that hard-codes Australia asks it instead. The market comes from the search's
**Country** (Setup → What jobs to look for), falling back to the profile's country.
Australian behaviour stays exactly as it is.

| | Australia (as now) | United Kingdom |
|---|---|---|
| Spelling | Australian English | British English (same -ise/-our rules; the spelling fixer serves both) |
| Currency, pay | AUD; base excluding 12% super | GBP; pension on top, not in the figure; London day rates |
| Hours | 38/week, ~230 working days | 37.5/week, ~225 working days |
| Document name | Resume | CV (files, PDF titles, Setup/Guide text, AI prompts) |
| Length | 2 pages (3 max) | 2 pages (1 for graduates; the Length setting works as is) |
| Phone | 04xx → +61 | 07xxx → +44 |
| Dates | DD/MM/YYYY | DD/MM/YYYY |

## Workstreams

1. **Market module + prompts**
   - `market.py`, and use it in: `scoring/context.py` (style line, candidate brief, salary
     wording), `scoring/scorer.py`, `scoring/tailor.py`, `scoring/cover_letter.py`,
     `scoring/voice.py`, `apply/prompt.py`, `apply/local_engine.py` (screening, salary, hours,
     phone, right to work).
   - UK scorer notes: "must have the right to work in the UK" isn't a dealbreaker for people who
     have it; "no sponsorship" is only for visas that need sponsorship; agency ads hide the
     employer; IR35 status matters for contracts. Competency-based questions (the UK's
     equivalent of selection criteria).

2. **UK work rights** (`profile_options.py`, `scoring/context.py`, `scoring/look.py`)
   - British or Irish citizen; Settled status / ILR; Pre-settled status; Skilled Worker visa
     (needs sponsorship); Graduate visa; Youth Mobility Scheme; Student visa; Other.
   - Header wording "Right to work in the UK" (or the visa name where it helps).
   - Youth Mobility Scheme is shown per job like the working holiday visa (contract/temp roles
     only); Student visa is left off by Automatic.

3. **London locations** (`config.location_accept_patterns`, scorer local areas, `scoring/facts.py`,
   `apply/answers.py`)
   - "London" also accepts Greater London, the City of London and common areas (Canary Wharf,
     Shoreditch, Westminster, Croydon, Stratford, King's Cross…); "United Kingdom" remote roles kept.
   - All of Greater London counts as local in scoring.
   - Profile: UK nations as the region list; postcode field.

4. **UK job sources** (`wizard/init.py` country sources, `config/sites.yaml`, new Reed API source)
   - Indeed UK and LinkedIn via JobSpy.
   - **Reed via its official API**: each user pastes a free key (Setup → API keys). Without a key,
     Reed is skipped with a note.
   - Page-reading: Hays UK and Randstad UK (both tested live).
   - Not used: Totaljobs and page-read Reed (terms), CV-Library, Jora UK, Adzuna (block automated
     browsers). Find a job (DWP) was down when tested; retest.
   - Job type filter and applicant counts carry over.

5. **Checks and UI**
   - ATS check: UK places count as a location; work-rights messages and spelling follow the market.
   - Setup: Country note; profile fields follow the market (postcode, right to work, London
     placeholder); Reed API key field.
   - Guide: "Applying in the UK" section.
   - Data folder unchanged; switching country affects new searches and documents.

## Testing and release

- Unit tests: market switching; UK work rights (display, scoring rules); London location
  matching; UK salary (pension excluded); Reed API parsing; board URL building; an Australian
  regression check (prompts and outputs unchanged).
- Live check of each page-read UK board for "data analyst" in London.
- Version 0.11.0; CI and Windows/Mac builds green before the release is published.
- One PR, one commit per workstream.
