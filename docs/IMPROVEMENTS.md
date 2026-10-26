# Improvement plan: fewer AI requests, better results

ApplyPilot's pipeline was designed around one AI call per small task with
free-text replies. On Gemini's free tier the limit that matters is
**requests per day** (about 500/day for `gemini-3.5-flash-lite`, the model
used for every stage), so this plan focuses on doing the same work in fewer
requests. In order: filter cheaply first, batch work into few calls, ask for
structured output, and reuse what the AI has already worked out.

Estimated effect on a typical day (200 jobs found, 20 good matches, 10
applications): **about 300 requests down to about 60–80**. These numbers are
estimates from the code paths, not measurements.

## Where requests go today

| Stage | How it works now | Requests (typical day) |
|---|---|---|
| Scoring | One request per job; the whole resume is resent each time | ~200 |
| Tailoring | Write, then a separate AI fact-check; up to 4 attempts | ~40–160 for 20 jobs |
| Cover letters | Separate request per job; retried on banned words | ~20–80 |
| Smart extract (Jora) | The AI works out how to read the page on every run | ~2 per job title per run |
| Applying | One request per form page; every field goes through the AI | ~10–60 |

## Status

| # | Improvement | Effort | Saving | Status |
|---|---|---|---|---|
| A1 | Free filters before any AI (`exclude_titles`) | Small | Varies with your filters | **Done** (#4) |
| A2 | Batch scoring | Small–medium | ~200 → ~20 requests | **Done** (#4) |
| A3 | Structured (JSON) output | Small | Retries from unreadable replies | Scoring **done** (#4). Writing: with B |
| B | One writing request per job | Medium | ~3–12 → ~1–2 per job | Planned |
| C | Remember how to read each site | Small | ~2 per title per run → ~0 | Planned |
| D | Daily request budget | Medium | Spends requests on the best jobs | Planned |
| E | Smarter form-filling | Large | Fewer requests; more consistent answers | Planned |

---

## A1. Free filters before any AI

**Problem:** every discovered job is scored by the AI, including obvious
mismatches. `searches.example.yaml` documents an `exclude_titles` list, but
the code never used it.

**Change:** jobs whose title contains an `exclude_titles` entry get score 0,
with the reason recorded and **no AI request**. Matching is whole-word, so
`intern` does not match "Internal Audit". The setup wizard and the web UI
("Skip jobs whose title contains") write this list.

**Possible follow-ups:**
- A seniority check (e.g. skip "Senior", "Principal" or "Head of" when the
  profile has fewer than N years of experience).
- A required-keyword check.

## A2. Batch scoring

**Problem:** `scorer.py` sends one request per job and resends the full
resume every time.

**Change:** score 10 jobs per request (`score_batch_size` in
`searches.yaml`), with the resume sent once per batch. Each job in the
reply is validated. Jobs with a missing or invalid score are left unscored
and retried on the next run.

**Bugs fixed at the same time:**
- A failed request used to store score 0 permanently, so those jobs were
  never scored again (for example, after a run with a bad API key). They're
  now left unscored, and old error zeros are reset automatically.
- The prompt gave the job board ("indeed") as the company.

## A3. Structured (JSON) output

**Problem:** replies were free text (`SCORE: 7`), or JSON found by searching
the reply text. An unreadable reply cost a whole retry.

**Change:** `LLMClient.chat_json()` asks the provider for JSON-only output.
Gemini native uses `responseMimeType`; OpenAI-compatible endpoints use
`response_format`. If an endpoint doesn't support it, the client falls back
to plain text with lenient parsing. Scoring uses it now; tailoring and cover
letters move to it in B.

## B. One writing request per job

**Problem:** for each good match, `tailor.py` writes the resume and then
runs a separate AI "judge" to fact-check it, retrying up to 4 times.
`cover_letter.py` then makes its own requests with the same context. That
comes to about 3–12 requests per job.

**Plan:**
- Generate the tailored resume **and** the cover letter in one JSON
  request. They share the job description, resume and profile.
- Keep the fast code checks (banned words, no invented employers or
  metrics) on every result.
- Call the AI judge **only when those checks flag something**, instead of
  every time.
- Retry only the part that failed.

**Expected:** about 1–2 requests per job.

## C. Remember how to read each site

**Problem:** smart extract (`discovery/smartextract.py`) asks the AI to
choose an extraction strategy (JSON-LD, intercepted API, or CSS selectors)
for every page on every run. A site's page structure rarely changes between
runs.

**Plan:**
- Save the working extraction plan per site (e.g. under
  `~/.applypilot/site_plans/`).
- Reuse it on later runs.
- Ask the AI again only when the saved plan suddenly extracts zero jobs.

**Expected:** about 0 requests per run for sites already learned.

## D. Daily request budget

**Problem:** nothing tracks requests against the daily limit. When the quota
runs out, the remaining work fails partway through a stage.

**Plan:**
- Count requests per model per day (Pacific time, when Google's quotas
  reset), stored in the database.
- Let each run spend what's left in priority order: score, then tailor the
  highest-scoring jobs first, then apply.
- Stop cleanly with a message when the budget is used up, instead of
  erroring.
- Show "requests left today" in the web UI.

## E. Smarter form-filling

**Problem:** the local engine sends every field on every page to the AI,
including name, email and phone, and answers the same screening questions
again on every application.

**Plan:**
- **Fill standard fields in code:** name, email, phone, LinkedIn, address,
  resume and cover letter uploads, matched by label. No AI needed.
- **Answer bank:** save each screening question with its answer. When you
  change an answer in the browser before clicking Submit, store your
  version so the same question is answered your way next time. The AI is
  only asked about questions it hasn't seen.
- **Site adapters:** dedicated handlers for SEEK, Greenhouse and Lever,
  whose forms are predictable.
- The human-approval step (you click Submit) stays exactly as it is.

**Expected:** fewer requests, consistent answers across applications, and
fewer wrong answers to fix by hand.

---

## Considered and not planned

- **Fully autonomous "computer-use" browser agents.** They handle odd forms
  better, but they're slower, use many more requests per application, and
  make the "you click Submit" guarantee harder to keep. E gets most of the
  benefit.
- **Prompt caching.** It saves tokens, but the free tier limits request
  count, so it doesn't help the main constraint.
- **A separate, stronger "writing model" (`gemini-3.8-flash`).** Tried in
  PR #3 and closed without merging: the model always showed as unavailable
  for our key. Every stage uses `gemini-3.5-flash-lite`.
