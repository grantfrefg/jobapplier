# Changelog

All notable changes to this project (jobapplier, a fork of ApplyPilot) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries up to 0.3.0 are from the original ApplyPilot.

## [Unreleased]

### Fixed
- **Extra skills from your profile put into a job they weren't used in.** Skills you list in Your Profile but that your resume doesn't mention were real for the skills list, so the AI also wrote them into a role's bullets or its tech line ("Jan 2021 - Present | Python, Kubernetes"), and the fact-check then rejected the resume. Now they stay in the Skills section (and may be in the summary): they're taken out of each role's tech line automatically, and a bullet that names one goes back to the AI. The cover letter may name them as skills, not as something used at a particular employer. Soft skills (leadership, stakeholder management) aren't affected.

## [0.6.2] - 2026-10-30

### Fixed
- **"Experience header has employer '...', which isn't in the resume" for a real employer.** The check only recognised an employer on its own line or after "at", "|" or a comma. It now also accepts the usual layouts: followed by dates or a place ("Acme Corp    Jan 2020 - Present", "Acme Corp, Sydney NSW"), after a dash, slash or @ ("Engineer - Acme Corp"), with or without "Pty Ltd", "Limited" or "Group", in capitals, and "&" written as "and". A name that's only the start of a longer one ("Google" in "Google Cloud") still isn't an employer.

## [0.6.1] - 2026-10-30

### Fixed
- **"Fabricated skill" for a skill you have.** The tailoring check flagged a fixed list of languages and claims (C++, C#, Rust, Swift, Vue, certifications and others) as invented even when your resume listed them, and found some inside other words ("scala" in "scalable", "rust" in "trust"). It now only flags one that isn't in your resume or your profile's skills, matching whole words (your resume's "VueJS" or "Vue.js" counts for Vue).

## [0.6.0] - 2026-10-30

### Added
- **Your ChatGPT or Claude subscription as the AI** (Setup → AI: *ChatGPT subscription (via the Codex CLI)* or *Claude subscription (via Claude Code)*; `LLM_PROVIDER=codex_cli` / `claude_cli`). No API key or bill: each AI step runs the signed-in CLI once as a single-purpose subagent with that step's instructions, no tools, no MCP servers and an empty working folder, and returns the same JSON as an API would. Setup shows whether the CLI is installed and how to sign in, and **Test key** checks it. A plan's usage limit stops the run cleanly; a signed-out CLI says how to sign in. Works for scoring, tailoring, cover letters, the fact-check and the browser engine's form answers.

## [0.5.3] - 2026-10-30

### Changed
- **You submit, with every engine.** Claude Code and Codex now fill in the whole application (accounts and all) and stop at the final Submit with `RESULT:READY`, instead of submitting by themselves; an email-only ad is left for you too. The launcher leaves their Chrome window open until you close it (up to an hour): check it, submit it yourself, then press **Mark applied**. The job shows as *Saved for later* until then. Setup's engine descriptions, the confirmation, the Guide and the docs say so.

### Fixed
- **OpenAI or Claude without API credit** was retried as if it were a rate limit (OpenAI answers HTTP 429 `insufficient_quota`). The run now stops at once with what to do (add billing or prepaid credit; a ChatGPT or Claude subscription doesn't include API credit), and **Test key** says so straight away.
- **Rate-limit messages named Gemini whatever the provider.** They now name the provider in use with a tip for it, and wait as long as OpenAI's reset headers say (up to a minute). Claude's rate limit after the SDK's retries gets a plain message too. The apply log no longer says "Gemini" for other providers.

## [0.5.2] - 2026-10-29

### Added
- **Tailoring style** (Find jobs; `TAILOR_STYLE`): *light touch* keeps your bullets word for word, only swapping in the ad's term for the same thing and reordering, and changes at most one sentence of your summary; a light-touch draft that rewrote most bullets is sent back. *Full rewrite* (the default) is unchanged.
- **Your writing** (Setup): paste a cover letter or a few paragraphs you wrote, and cover letters follow your voice (never its facts).
- **Letters don't repeat themselves:** each cover letter is told how recent letters opened, and one that opens like a recent one is written again.
- **Reads human** badge (Apply tab, no AI): flags stock phrases, AI-sounding constructions, long dashes, the same verb starting most bullets, bullets all the same length, strings of "X, Y and Z", letters where most sentences start with "I", long phrases copied from the ad, and a letter opening like another; hover for details and the share of bullets in your own words.
- **Edit** a generated resume or cover letter in the app (*edit resume* / *edit cover letter* under a job): the PDF, ATS score and Reads human check are made again. A text the PDF can't be made from leaves the old documents untouched.
- Guide: **Sounding like you, not an AI**.

## [0.5.1] - 2026-10-29

### Added
- **Profile explained:** every section of Setup → Your profile says what it's used for and why it's asked, with notes on the fields that need one (password, salary, current title...). The skills section is highlighted as the one that improves tailored resumes. The Guide has new **Your profile**, **Skills** and **Salary** sections.

### Changed
- **One salary minimum everywhere:** scoring, the browser engine and the Claude Code / Codex engines all treat `salary_range_min` as your minimum (the expected salary when there's no minimum). The agent engines used to treat the expected salary as the floor. Forms answer with the expected salary, never below the minimum. Setup refuses a minimum above the expected salary or the maximum.

### Fixed
- **Suburbs counted as another city:** a candidate in a suburb (e.g. Wentworthville) who won't relocate had Sydney CBD jobs scored as a location dealbreaker. The scorer now gets the places you search in (Setup → What jobs to look for) as your local area, and its rule says every suburb of a city is local. Use **Run a step again → Score again** to re-score jobs scored before this fix.
- **"Why this score" cut off:** the Choose jobs list shows the whole reason (and the must-haves the resume matched), and hovering a score on the Apply tab shows it too.
- The Guide said passwords are never typed; the profile's password is used by the Claude Code and Codex engines (the browser engine never sends or types it). It now says so, in the Guide and next to the field.

## [0.5.0] - 2026-10-28

The first public release of jobapplier. Covers everything since the original ApplyPilot, including what was
tagged v0.4.0.

### Added
- **Guide tab:** a user guide inside the web UI: what the app does, setting up, finding and choosing jobs, applying (with the app or yourself), Redo / Try again / Run again, getting more jobs tailored, the ATS score, costs, where files are, privacy, a typical week and troubleshooting. Links jump to the right tab; it opens first on a new install, and Setup links to it.
- **Retry failed searches:** discovery remembers the searches a board blocked or failed (LinkedIn and Indeed per board, Jora per page), and **Run a step again → Discover: retry the searches that failed** (`applypilot rerun discover`) runs just those; Find jobs says how many are waiting. **Discover: search every board again** repeats every search.
- **Run any step again:** **Run again…** on a job (Apply tab), **Run a step again for jobs already done** on Find jobs, or `applypilot rerun STEP [--url URL]`, for enrich, score, tailor, cover (letter only), review (the AI fact-check of the current documents), pdf and ats. A job keeps what it has until the new version is ready; a job the fact-check flags keeps its documents and shows what was found.
- **Choose which jobs get tailored:** after discovering and scoring, Find jobs lists the scored jobs (best match first, with the scorer's reason); tick the ones you want and **Tailor ticked jobs** writes a resume and cover letter for just those, whatever their score. On by default (**Let me choose which jobs get tailored**), which leaves tailoring out of **Run**. **Skip ticked** keeps a job out of tailoring for good, including automatic runs (`tailor_skipped`); the Skipped view puts it back.
- **Claude Code and Codex in the desktop app:** Setup offers them there too, after a confirmation that they submit
  applications by themselves (an engine left in a copied `.env` doesn't count; the app records
  `AGENT_ENGINE_CONFIRMED`). The app finds `claude`, `codex` and `npx` in their usual install folders, runs npm's
  `.cmd` shims by full path on Windows, passes Codex settings without double quotes, and drives Edge when Chrome
  isn't installed. Setup shows the one-line install command for the chosen CLI.
- **Your own paid AI keys:** choose the AI that scores jobs, writes resumes and cover letters and answers forms:
  Google Gemini (free), OpenAI (`OPENAI_API_KEY`, default `gpt-5-mini`) or Anthropic Claude (`ANTHROPIC_API_KEY`,
  default `claude-opus-5-5`, through the official `anthropic` SDK). Setup has a provider picker with a key field
  and **Test key** for each; the free browser engine fills forms with whichever is chosen. OpenAI's newer models get
  the request fields they need (`max_completion_tokens`, no temperature).
- **Codex engine** (`EXECUTION_ENGINE=codex`): OpenAI's Codex CLI applies like the Claude Code engine (same
  instructions, same Chrome through Playwright MCP, submits automatically), with its command runner in a read-only
  sandbox. `applypilot doctor` checks for it; a Codex that isn't signed in is reported as such.
- **ATS check reads the PDF back and knows synonyms:** each generated resume PDF's text is extracted the way a
  parser does it and checked (name first, email and phone readable, sections in order, every job keeps its dates);
  characters a parser may misread (ligatures, icon glyphs) are flagged. Another form of an ad's term ("AWS" for
  "Amazon Web Services") counts half and the badge says which word to use. More short and long forms are known,
  including other engineering disciplines (CAD, PLC, SCADA, BIM, FEA, CFD, HVAC, WHS, RPEQ, CPEng).
- **Your own resume or cover letter for a job:** on the Apply tab, "use my own resume / cover letter" takes a PDF or
  Word (.docx) file, and Apply uploads it instead of the generated one (you can mix). A job with your own resume
  isn't sent for tailoring (no Gemini requests), becomes ready to apply even if its drafts were rejected, and its
  ATS check reads your file (a scanned, unreadable PDF is flagged). "Use generated" switches back. Adds `pypdf`.
- **Why a job got no documents:** when every draft is rejected (`FAILED_JUDGE`, `FAILED_VALIDATION`), the log now
  says why and what happens next, the Apply tab's **All jobs** view shows the reason (hover) with a **Try again**
  button, and **Find jobs** has a "What the log means" guide to the log lines (searches, blocked boards, tailoring
  results, the daily budget) and a "How it works" guide to each step (also on hover over the step boxes).
- **Apply tab guides and Mark applied:** "How to use this tab" (applying with the app step by step, accounts, or
  applying yourself with the downloaded documents) and "What the ATS score means". A **Mark applied** button moves a
  job you applied to yourself to Applied.
- **Getting more jobs tailored:** the log guide explains what to do when many jobs are rejected (complete the
  resume and skills list, Try again, lenient strictness and its trade-off, skipping titles you can't match).
  **Try again** and **Redo** now use the Strictness picked on Find jobs.
- **Free local apply engine** (`EXECUTION_ENGINE=local_playwright`): a visible browser plus your Gemini key fills
  application forms, including multi-page ones. Personal identifiers are sent to Gemini only as placeholders.
  Automation never clicks Submit/Apply/Send: you review and submit yourself.
- **Web UI** (`applypilot ui`): Setup (keys with a **Test key** button, resume, profile, job search), Find jobs (live
  pipeline log), and Apply (job list, live browser view, and the review-and-submit step as buttons).
- **Desktop app** for Windows and Mac (friend mode), built and smoke-tested by the **Desktop app** workflow.
- **Dev container** with a virtual desktop, so the visible browser works on Windows/Mac hosts.
- **Australian job sources:** Indeed Australia, LinkedIn and Jora. `country`, `smart_extract_sites`,
  `workday_employers` and `site_priority` in `searches.yaml`.
- **Cross-site de-duplication:** one role listed on several boards is scored, tailored and applied to once.
- **Batch scoring and title filter** (`exclude_titles`, `score_batch_size`); JSON output mode in the LLM client.
- **Answer bank and standard fields:** the local engine fills name, contact, address and uploads in code and
  reuses screening answers (yours win over the AI's), so known pages cost no Gemini request. Setup → Saved answers.
- **Saved site plans:** smart extract (Jora) remembers how to read each site in `~/.applypilot/site_plans/`
  and only asks the AI again when that stops working.
- **Daily Gemini budget:** requests are counted per day; finding and preparing jobs stops cleanly and keeps a
  reserve for applying (`GEMINI_DAILY_LIMIT`, `GEMINI_APPLY_RESERVE`). Requests left today are shown in the UI.
- **Prompts tuned for engineers in Australia:** scoring sees deal-breakers (work rights, clearance, location,
  seniority); tailoring and the fact-check never add tools you don't have; Australian English, 2-page resumes and
  DD/MM/YYYY dates; truthful screening answers and legal questions left for you. New profile fields:
  `security_clearance`, `willing_to_relocate`, `notice_period`, `drivers_licence`.
- **Greenhouse, Lever and SEEK forms:** searchable drop-downs, correct question text for radio groups and
  drop-downs, matching by field id, +61 phone format, SEEK's document step and Lever's additional-information box
  are handled without the AI. Picking a suggestion never presses Enter.
- **Redo documents:** rewrite the resume and cover letter for one job or every job not yet applied to (Apply tab,
  or `applypilot redo`), re-picking the preferred site's listing (LinkedIn > Indeed > Jora). Old documents are kept
  until the new ones pass, and only the chosen jobs are rewritten, whatever their score.
- **Australian resume layout:** A4 PDFs, location and work rights in the header, title/employer/dates on one
  line, clickable links, empty sections left out; cover letter PDFs get a proper letter layout (they were rendered
  with the resume template and lost lines). Existing PDFs are re-rendered once.
- **Where each job applies:** "Check where to apply" (or `applypilot locate`) follows each ad to the employer's
  application system and labels it (e.g. Greenhouse, no account; Workday, account needed), with a view of the jobs
  that need no employer account. Sign-in and create-account pages get your email filled in (never a password)
  and a clear prompt, with no AI request.
- **Log in to sites:** opens the engine's browser profile as a normal (non-automated) window so you can sign in
  to Google, LinkedIn, SEEK or an employer site where sign-in is blocked in the automated window
  (`applypilot login`, or a job's Log in button).
- **ATS check:** every tailored resume gets a score (Apply tab badge, `applypilot ats`) for parsing, Australian
  knockout facts (work rights, location), the ad's keywords and spelling. No AI requests.
- **Prompts reviewed for Australia, ATS and a human voice:** the ad's exact words for real skills (missing ones
  added by code), Australian spelling enforced in code, AI-sounding words and phrasing rejected in summaries and
  letters, letters sign off "Kind regards," and address selection criteria, projects no longer required.
- **My skills list is accurate** (Setup): once ticked, everything in the profile's skills list counts as your own,
  including entries that match the template's examples (ignored until then).
- **Profile drop-downs:** Yes/No questions, state, country, work rights, clearance, notice period, currency,
  education and diversity questions are picked from a list (Setup and `applypilot init`); old wording is mapped.
- CI on every pull request (Python 3.11–3.13, browser tests under xvfb).

### Changed
- **Docs caught up** with recent changes: README (setup, Find jobs and Apply guides, your own documents, Mark
  applied, engine choice, AI provider, troubleshooting), HOW_IT_WORKS (fact-check, rejected jobs, form AI, scoring
  inputs, AI usage), CONFIGURATION, DESKTOP_APP, the READ_ME_FIRST files, IMPROVEMENTS and CONTRIBUTING.
- **Prompt review (2):**
  - Fact-check: checks against the original resume plus your confirmed profile skills (it used only the profile's
    skills list and `real_metrics`, so it could reject skills and numbers that are in your resume); must quote the
    unsupported phrase and passes when it can't (no more "when in doubt, FAIL"); sees the job ad so statements about
    the employer aren't treated as claims about you; no longer allows moving bullets between roles.
  - Form filling: sees the job description (specific "Why this role?" and selection criteria answers) and the job
    board ("How did you hear about us?"); salary figures are base excluding super, with 12% added when a form asks
    for the package; referral and "worked here before" questions are answered.
  - Scoring: knows whether you take contract or full-time work, and caps jobs paying clearly below your minimum.
  - Writer uses the ad's form of a term ("AWS" vs "Amazon Web Services"); the cover letter answers what the ad asks
    for (availability, a question) and mentions sponsorship or relocation in one sentence only when it applies, and
    may quote numbers from the ad.
  - Claude Code engine salary: Australian (base excluding super, 38-hour week, day rates), no fixed US$110K floor.
- Default model `gemini-3.5-flash-lite` for every stage (was `gemini-2.0-flash`).
- LLM errors include the provider's message (e.g. "Please pass a valid API key").
- Location filter shared by all sources, matching whole words (your resume's "VueJS" or "Vue.js" counts for Vue). State abbreviations are expanded (NSW → New South Wales).
- The setup wizard and UI ask for country and city instead of an (unused) search radius.

### Fixed
- **Setup layout:** the Browser card was never closed, so the Resume and job-search cards were drawn inside it.
- Drop-downs (Greenhouse country, location, screening questions) were sometimes left empty on a busy computer: the
  click waited for the box to stop moving and gave up after 3 seconds. It now clicks anyway. The apply log also says
  why an action failed.
- The Apply browser showed "You are using an unsupported command-line flag: --no-sandbox" in Chrome and Edge: the
  browser's sandbox is now left on (except on Linux, where containers can't use it).
- A job board that blocks the search (LinkedIn's "429 too many requests") was reported as an unnamed "response status
  code 429 (blocked: use a proxy)" and asked again for every job title. It's now named once ("LinkedIn: blocked: too
  many requests"), skipped for the rest of that run, and the run ends with a note to try again in a few hours.
- Job discovery (Jora), job-detail fetching and the locate check crashed where Playwright's headless shell isn't
  installed (the app's Install browser, CI); every background browser now falls back to the full Chromium, then
  Chrome / Edge (`applypilot/browser.py`).
- A lone upload box labelled "Attach" could get the cover letter when its section mentioned one; upload boxes now
  read only their own row, and a page's only upload box takes the resume.
- Template example values ("Your City", "Company A"...) are never used as facts and can't be saved.
- The fact check no longer reads describing words ("experienced engineer") as job titles.
- Only tabs the application opened are followed; a tab you open yourself is never filled.
- Real skills were removed from tailored resumes when the AI wrote them differently: an acronym spelt out
  ("Google Cloud Platform (GCP)" for "Google Cloud"), US spelling, or "stakeholder management" for stakeholder
  work. Short and long forms and spellings now match, and real tools in brackets are kept.
- Upload boxes labelled only "Upload" (PageUp, e.g. Virgin) got the resume twice and were re-uploaded on "Fill this
  page again". Boxes are now matched by their row or section, and the files to upload by hand are in a visible
  `~/ApplyPilot uploads` folder, listed on the "Your turn" panel.
- Different jobs whose apply link was a job board's sign-in page (every Jora quick-apply job, LinkedIn's sign-up
  page) or an employer link with the job ID in the query (PageUp) were treated as one role: all but one were hidden,
  and Redo skipped them. Sign-in pages no longer match, and only tracking parameters are ignored.
- Tailored resumes and cover letters could claim things that aren't true (a target-job title like "Forward Deployed
  AI Engineer" as the headline, skills from the profile template's examples). Code now checks every title, skill,
  tool, number, employer and qualification against the original resume, fixing or rejecting what doesn't match;
  the AI fact-check runs on every job and its final rejection is no longer saved; Setup warns about example skills.
- LinkedIn: the Easy Apply dialog is filled after you open it (only the dialog, never the page's search
  boxes), an Apply button that opens the employer's form in a new tab is followed, and LinkedIn's sign-up wall
  is no longer used as the apply link.
- Jora (and SEEK/Indeed/LinkedIn) job ads: the local engine opens the ad's Apply link to the employer's form instead
  of filling the job board's search box; relative apply links (Jora's `/job/rd/...`) are kept and made absolute
  instead of discarded.
- `applypilot apply --url` never matched jobs that hadn't been attempted (`apply_status` NULL).
- An empty location accept list discarded every non-remote job.
- Indeed searched the US site regardless of location.
- Scoring stored a permanent score 0 when the LLM call failed, so those jobs were never scored again.
- The scoring prompt gave the job board as the company.
- `.env.example` lines like `KEY=   # note` set the note as the value, which sent it as an API key.
- `searches.yaml` broke on job titles containing quotes.

## [0.2.0] - 2026-02-17

### Added
- **Parallel workers for discovery/enrichment** - `applypilot run --workers N` enables
  ThreadPoolExecutor-based parallelism for Workday scraping, smart extract, and detail
  enrichment. Default is sequential (1); power users can scale up.
- **Apply utility modes** - `--gen` (generate prompt for manual debugging), `--mark-applied`,
  `--mark-failed`, `--reset-failed` flags on `applypilot apply`
- **Dry-run mode** - `applypilot apply --dry-run` fills forms without clicking Submit
- **5 new tracking columns** - `agent_id`, `last_attempted_at`, `apply_duration_ms`,
  `apply_task_id`, `verification_confidence` for better apply-stage observability
- **Manual ATS detection** - `manual_ats` list in `config/sites.yaml` skips sites with
  unsolvable CAPTCHAs (e.g. TCS iBegin)
- **Qwen3 `/no_think` optimization** - automatically saves tokens when using Qwen models
- **`config.DEFAULTS`** - centralized dict for magic numbers (`min_score`, `max_apply_attempts`,
  `poll_interval`, `apply_timeout`, `viewport`)

### Fixed
- **Config YAML not found after install** - moved `config/` into the package at
  `src/applypilot/config/` so YAML files (employers, sites, searches) ship with `pip install`
- **Search config format mismatch** - wizard wrote `searches:` key but discovery code
  expected `queries:` with tier support. Aligned wizard output and example config
- **JobSpy install isolation** - removed python-jobspy from package dependencies due to
  broken numpy==1.26.3 exact pin in jobspy metadata. Installed separately with `--no-deps`
- **Scoring batch limit** - default limit of 50 silently left jobs unscored across runs.
  Changed to no limit (scores all pending jobs in one pass)
- **Missing logging output** - added `logging.basicConfig(INFO)` so per-job progress for
  scoring, tailoring, and cover letters is visible during pipeline runs

### Changed
- **Blocked sites externalized** - moved from hardcoded sets in launcher.py to
  `config/sites.yaml` under `blocked:` key
- **Site base URLs externalized** - moved from hardcoded dict in detail.py to
  `config/sites.yaml` under `base_urls:` key
- **SSO domains externalized** - moved from hardcoded list in prompt.py to
  `config/sites.yaml` under `blocked_sso:` key
- **Prompt improvements** - screening context uses `target_role` from profile,
  salary section includes `currency_conversion_note` and dynamic hourly rate examples
- **`acquire_job()` fixed** - writes `agent_id` and `last_attempted_at` to proper columns
  instead of misusing `apply_error`
- **`profile.example.json`** - added `currency_conversion_note` and `target_role` fields

## [0.1.0] - 2026-02-17

### Added
- 6-stage pipeline: discover, enrich, score, tailor, cover letter, apply
- Multi-source job discovery: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- Workday employer portal support (46 preconfigured employers)
- Direct career site scraping (28 preconfigured sites)
- 3-tier job description extraction cascade (JSON-LD, CSS selectors, AI fallback)
- AI-powered job scoring (1-10 fit scale with rationale)
- Resume tailoring with factual preservation (no fabrication)
- Cover letter generation per job
- Autonomous browser-based application submission via Playwright
- Interactive setup wizard (`applypilot init`)
- Cross-platform Chrome/Chromium detection (Windows, macOS, Linux)
- Multi-provider LLM support (Gemini, OpenAI, local models via OpenAI-compatible endpoints)
- Pipeline stats and HTML results dashboard
- YAML-based configuration for employers, career sites, and search queries
- Job deduplication across sources
- Configurable score threshold filtering
- Safety limits for maximum applications per run
- Detailed application results logging
