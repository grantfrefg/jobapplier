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
| Tailoring | Write, then a separate AI fact-check; up to 4 attempts | ~40–160 for 20 jobs (now ~40: see B and F) |
| Cover letters | Separate request per job; retried on banned words | ~20–80 |
| Smart extract (Jora) | The AI works out how to read the page on every run | ~2 per job title per run |
| Applying | One request per form page; every field goes through the AI | ~10–60 |

## Status

| # | Improvement | Effort | Saving | Status |
|---|---|---|---|---|
| A1 | Free filters before any AI (`exclude_titles`) | Small | Varies with your filters | **Done** (#4) |
| A2 | Batch scoring | Small–medium | ~200 → ~20 requests | **Done** (#4) |
| A3 | Structured (JSON) output | Small | Retries from unreadable replies | Scoring **done** (#4). Writing: with B |
| B | One writing request per job | Medium | ~3–12 → ~1–2 per job | **Done** (#9) |
| C | Remember how to read each site | Small | ~2 per title per run → ~0 | **Done** (#12) |
| D | Daily request budget | Medium | Spends requests on the best jobs | **Done** (#13) |
| E1 | Standard fields in code + answer bank | Medium | ~1 per page → 0 on known pages | **Done** (#11) |
| E2 | Site adapters (SEEK, Greenhouse, Lever) | Large | Fewer wrong answers on those sites | **Done** (#15) |

Since the plan, the priority shifted from fewer requests to **better documents**: tailored resumes were
claiming job titles and skills the user doesn't have. The fact checks below cost one extra request per
tailored job (the AI fact-check now always runs), about 20 a day; they're worth it.

| # | Improvement | Requests | Status |
|---|---|---|---|
| F | Fact checks in code: titles, skills, tools, numbers, employers, education vs the real resume | +1 per tailored job (fact-check) | **Done** |
| G | Prompts for Australia, ATS and a human voice; Australian spelling and AI-phrasing checks in code | None | **Done** |
| H | ATS check of every tailored resume | None | **Done** |
| I | Where each job applies, sign-in pages, normal login window | None | **Done** (#20) |

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

**Change (done in #9):**
- `tailor_application()` generates the tailored resume **and** the cover
  letter in one JSON request. They share the job description, resume and
  profile.
- The code checks still run on both (structure, preserved employers and
  school, fabricated skills, banned words; the letter's format).
- The AI judge runs **only when code spots a likely fabrication**: numbers
  not in your original resume, or watchlist tools it never mentions,
  anywhere in the resume or letter. It now answers in JSON and also reviews
  the letter.
- A bad resume regenerates both; a bad letter alone gets a letter-only
  retry.

**Expected:** about 1–2 requests per job.

## C. Remember how to read each site

**Problem:** smart extract (`discovery/smartextract.py`) asks the AI to
choose an extraction strategy (JSON-LD, intercepted API, or CSS selectors)
for every page on every run. A site's page structure rarely changes between
runs.

**Change (done in #12):**
- The working plan (strategy plus its JSON paths or CSS selectors) is saved
  per site in `~/.applypilot/site_plans/<site>.json` and reused for every
  query on that site, on every later run: no AI requests.
- If the saved plan finds nothing, the AI re-learns the page; the new plan
  replaces the old one only if it actually finds jobs. A blocked page
  (captcha / rate limit) keeps the saved plan and makes no AI request.
- Delete a site's file to make it re-learn.

**Original plan:**
- Save the working extraction plan per site (e.g. under
  `~/.applypilot/site_plans/`).
- Reuse it on later runs.
- Ask the AI again only when the saved plan suddenly extracts zero jobs.

**Expected:** about 0 requests per run for sites already learned.

## D. Daily request budget

**Problem:** nothing tracks requests against the daily limit. When the quota
runs out, the remaining work fails partway through a stage.

**Change (done in #13):**
- `budget.py` counts Gemini requests per model per US Pacific day in the
  `llm_usage` table (the LLM client, the local apply engine and **Test key**).
- The pipeline keeps a reserve for applying (`GEMINI_APPLY_RESERVE`, default
  50 of `GEMINI_DAILY_LIMIT`, default 500). Scoring, tailoring and cover
  letters stop cleanly when they reach it and leave the remaining jobs for
  the next run (no failed attempts recorded). Tailoring already goes
  highest score first.
- A daily-quota 429 from Google is believed over the local count and isn't
  retried.
- The web UI header shows "N of 500 Gemini requests left today"; Setup →
  **Daily request limit** changes the numbers. The pipeline summary prints
  the same.

**Original plan:**
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

**Change (E1, done in #11):**
- Standard fields (name, email, phone, LinkedIn/GitHub/website, address,
  city, state, country, resume/CV and cover letter) are matched by label and
  filled in code. Fields that already have a value are left alone.
- The answer bank (`answers` table) remembers short screening answers
  (drop-downs, radio buttons, short text). The AI's answers are saved as
  "AI"; anything you type or change at the review step is saved as "you"
  and always wins. Long free-text answers and anything holding your
  personal details are never saved. View and delete them in Setup →
  **Saved answers**.
- Gemini only sees the fields still unanswered. A page where everything is
  known costs **no request**.

**Original plan:**
- **Fill standard fields in code:** name, email, phone, LinkedIn, address,
  resume and cover letter uploads, matched by label. No AI needed.
- **Answer bank:** save each screening question with its answer. When you
  change an answer in the browser before clicking Submit, store your
  version so the same question is answered your way next time. The AI is
  only asked about questions it hasn't seen.
- **Site adapters (E2, done in #15):** checked against live Greenhouse and
  Lever job forms. Most of the work was making the form scanner read these
  sites correctly, which helps every site:
  - Questions are read from their container (Lever's question cards,
    fieldsets), so radio groups are no longer named after their own "Yes"
    option and drop-down labels no longer include every option.
  - Searchable drop-downs (React-Select on Greenhouse, location
    autocompletes) are recognised; their options are read by opening them,
    and answers are picked by typing and clicking the matching suggestion.
    Hidden "required" duplicates are skipped.
  - Fields are also matched by their HTML id (`first_name`, `resume`,
    `cover_letter`, `candidate-location`), so "Attach" buttons and
    non-English labels still work. Phone fields with a country picker get
    `+61 …`. Current company/title and location come from the profile.
  - Choices that reveal more fields (SEEK's "Upload a resumé") get a second
    pass without the AI.
  - Picking a suggestion never presses Enter (that could submit the form);
    when nothing matches, you're told which field to pick yourself.
  - `apply/adapters.py`: SEEK's document step (upload the tailored resume;
    upload or write the cover letter) and Lever's "Additional information"
    (cover letter when there's no other place). SEEK's markup couldn't be
    checked live (it needs a login), so its rule matches the visible option
    text rather than page structure.
  - On the live forms, Greenhouse's standard section and Lever's needed no
    AI; only the employer's own questions went to Gemini.
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
