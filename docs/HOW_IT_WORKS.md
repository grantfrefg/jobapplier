# How it works

The pipeline runs in stages: **discover → enrich → score → tailor → cover → pdf**, then **apply**. Each stage picks up whatever the previous one left in the job database (`~/.applypilot/applypilot.db`). You can run them all (`applypilot run`, or **Find jobs → Run** in the web UI) or one at a time (`applypilot run score`).

## Discover

Where jobs come from depends on the `country` in `searches.yaml`.

**Australia:**

| Source | How | AI requests |
|---|---|---|
| Indeed Australia (au.indeed.com) | JobSpy | None |
| LinkedIn | JobSpy | None |
| Jora (au.jora.com) | Smart extract: a browser loads the search page and the AI works out how to read the listings, once; the plan is saved and reused | ~2 the first time, then 0 |

For other countries, every bundled source is used: ZipRecruiter, Canadian/US job boards and Workday employers, as in the original ApplyPilot. You can choose sources yourself with `sites`, `smart_extract_sites` and `workday_employers` (see [CONFIGURATION.md](CONFIGURATION.md)).

**Not included:**
- **SEEK:** its terms prohibit scraping and it has strong bot protection. Applying to SEEK jobs you found yourself works normally.
- **APS Jobs:** its listings don't render for automated browsers.
- **Glassdoor:** it blocks automated searches, and Glassdoor applications go through Glassdoor accounts.

**When a board blocks or fails:** a board that answers HTTP 429/403 is skipped for the rest of the run (the other boards carry on), and every search it missed, or that errored, is remembered with the boards involved. Jora pages that find nothing or fail are remembered the same way. **Run a step again → Discover: retry the searches that failed** (`applypilot rerun discover`) runs just those later; running Discover again repeats every search. Jobs already found are never stored twice.

**Location filter:** a job is kept if its location matches one of the `location_accept` patterns, or it's remote. The setup generates those patterns from your city: "Sydney, NSW" becomes Sydney, NSW and New South Wales. Matching is whole-word, so "WA" doesn't match "Warsaw". With no patterns configured, everything that isn't explicitly rejected is kept.

## De-duplicate

The same role is often listed on Indeed, LinkedIn and Jora. Two listings count as one role if any of these hold:
- they link to the same application page on the employer's site (tracking tags such as `utm_` ignored; a job board's sign-in page, which many jobs share, never counts);
- they have the same title and the same company (ignoring "Pty Ltd", "Group" and similar);
- they have the same title and near-identical descriptions (used when the company isn't shown, which is common on Jora).

Only one copy is kept:
1. a copy you've already applied to, if there is one;
2. otherwise a copy you've already tailored;
3. otherwise the copy from the first site in `site_priority` (default LinkedIn, then Indeed, then Jora);
4. otherwise the highest score.

The other copies are skipped by scoring, tailoring, cover letters and applying. In the web UI the kept job shows "Also on: …". This check runs before scoring and again before every application.

## Enrich

Visits each job page and extracts the full description and the application link. It tries, in order:
1. structured data (JSON-LD);
2. common page patterns;
3. the AI, only for pages the first two can't handle.

## Score

1. **Title skip list (free).** Jobs whose title contains an `exclude_titles` entry get score 0, with the reason recorded and no AI request.
2. **Batch scoring.** The rest are rated 1–10 against your resume, 10 jobs per request, as structured (JSON) output. A job that doesn't get a valid score stays unscored and is retried on the next run.
3. **Deal-breakers.** The scorer also sees your target role, years, location, work rights, security clearance, full-time/contract preference and minimum salary from your profile. A job you can't take (citizenship or a clearance you don't hold, onsite elsewhere when you won't relocate, far too senior or junior, a mandatory registration you don't have) scores 3 or lower; a contract-only role when you don't take contracts, 4 or lower; one whose stated pay is clearly below your minimum, 5 at most.

The prompts are written for engineers (software first, other disciplines too) applying in Australia: Australian English, 2-page resumes, DD/MM/YYYY dates, AUD.

## Tailor

For jobs scoring at or above `--min-score` (default 7), or only the jobs you pick, the AI rewrites your resume for the role.

**Choosing the jobs yourself.** In the web UI, **Find jobs → Let me choose which jobs get tailored** (on by default) leaves the tailor and cover steps out of **Run**, so it stops after scoring. **Choose jobs to tailor** then lists every scored job without documents (best match first, with the scorer's reason). **Tailor ticked jobs** marks the ticked ones and writes just those, whatever their score, the same way **Redo** does (`applypilot redo --pending`). **Skip ticked** sets `tailor_skipped` on a job: it leaves the list and is never tailored automatically, even by `applypilot run`; the **Skipped** view puts it back, and picking a skipped job un-skips it.

The tailoring itself:
- it reorders, and rewords bullets where the job uses different terms for the same work;
- it leads with the must-haves the scorer found in your resume;
- it changes order, emphasis and wording, **never facts**: every title, employer, date, tool, skill, number, qualification and achievement must be in your original resume.

The tailored resume and a short, specific cover letter come from **one AI request**.

Every result is checked against your original resume:
- **Fixed by code:** the line under your name is always a title you've held (normally your current one, from your profile), optionally followed by a short focus such as "Production ML & LLM Systems". A target-job title like "Forward Deployed AI Engineer" is replaced. Any skill that isn't in your resume (or in your profile's skills list) is removed.
- **Sent back by code** (the AI tries again; after the last try the document isn't used): the summary or letter calling you by a job title you haven't held; a tool, platform or certification you never mention; a number that isn't in your resume; an employer or job title in Experience that doesn't match; education that doesn't match. Plus banned buzzwords, required sections and format.
- **AI fact-check ("judge"):** reads every resume and letter (except with `--validation lenient`) for what code can't see, such as invented work or two separate facts joined into a new claim ("cut costs 50% while serving 10 million customers"). It checks against your original resume plus your confirmed skills list, sees an excerpt of the ad (so what the letter says about the employer isn't mistaken for a claim about you), and must quote the exact phrase it rejects; when it can't point to one, it passes. If it still rejects the last attempt, the resume isn't used.
- A cover letter that keeps claiming things your resume doesn't is dropped rather than saved.

If the resume fails, both documents are regenerated. If only the letter fails, just the letter is retried.

**When no draft passes.** The job gets no documents rather than invented ones. The log says why in plain words (e.g. `The fact-check rejected every draft: <reason>. No resume or cover letter is used for this job; it's tried again on the next run (4 of 5 tries left).`), and **Apply → All jobs** shows "No documents" with the reason on hover and a **Try again** button (which uses the Strictness picked on Find jobs). Each job is tried up to 5 times across runs. The rejected draft is kept as `…_REJECTED.txt`. What helps: a fuller resume and skills list, Try again, lenient strictness (read those resumes before sending), or skipping titles you can't honestly match.

**Australian, ATS-friendly and human.** The prompts ask for the ad's exact wording for skills you really have (ATS searches match literal words, so "AWS" when the ad says AWS even if your resume says Amazon Web Services), acronyms written out once ("machine learning (ML)"), titles, employers and dates exactly as in your resume, 2 pages, and no photo or date of birth. Must-have skills you have that the AI left out are added to the skills section by code. American spellings in what the AI wrote are changed to Australian ones (never inside names from your resume). Words and phrasings that read as AI-written ("leverage", "thrilled", "not just X but Y", exclamation marks) get the summary or cover letter rewritten. Cover letters are 200-300 words, sign off "Kind regards,", address selection criteria when the ad asks for them, answer anything else the ad asks the letter to cover (availability from your profile), and mention sponsorship or relocation in one sentence only when it applies.

**ATS check.** Every tailored resume gets a score out of 100 (the **ATS** badge on the Apply tab; hover for details, or `applypilot ats` in the terminal). Australian employers mostly use Workday or SAP SuccessFactors (banks, miners, retailers), PageUp (government, universities), JobAdder, Greenhouse, Lever and iCIMS, and SEEK applications are usually passed on into the employer's own system. None publish their rules, so the check mirrors what they all do:
- **Parsing:** standard section headings, email and phone in the header, every job with a readable date range, 1-3 pages.
- **Knockouts:** work rights and city/state stated (Australian employers filter on both); no date of birth or photo.
- **Keywords:** the ad's tools and must-haves that appear in the resume, in its own words. Ones you don't have are listed but don't lower the score: they can't be added honestly. Another form of the same term ("AWS" for "Amazon Web Services", "PLC" for "programmable logic controller") counts half and is listed with the ad's word to use: some systems match synonyms, but a recruiter's keyword search often only finds the exact word.
- **The PDF, read back:** the generated PDF's text is extracted the way a parser does it, then checked: your name first, email and phone readable, sections in order, every job's title, employer and dates together. Characters a parser may misread (ligatures such as "ﬁ", icon-font glyphs) are flagged.
- **Australian spelling.**
It uses no AI requests. It's a guide, not a prediction of any one vendor's ranking.

**Your skills list matters.** `skills_boundary` in your profile adds skills that aren't spelled out in your resume. Skills in your resume are always kept, including short or long forms (GCP / Google Cloud Platform) and US spellings of them. The template's example values (JavaScript, Docker, AWS, CI/CD, MongoDB, Git, Linux) are ignored until you tick **My skills list is accurate** under the profile in Setup: remove the ones you don't use and add any you do (e.g. MLOps), then tick it. Hover a job's ATS badge to see any skills removed from its resume.

**Redo.** To rewrite documents that already exist (after changing your resume, profile or the prompts), press **Redo** on a job, or **Redo all documents** on the Apply tab (`applypilot redo` in the terminal, `--url` for one job).
- Only the jobs you redo are rewritten, whatever their score; nothing else in the queue is tailored.
- Nothing is deleted first: a job keeps its old resume and cover letter (and stays ready to apply) until the new ones pass the checks. If a rewrite doesn't pass, the old documents stay. If the day's Gemini budget runs out, the rest are rewritten on the next run.
- Jobs you've applied to keep theirs, as does a job open in the browser at that moment; jobs stuck "in progress" from an apply run that never finished are put back in the queue.
- Redo also re-picks which listing to use when a role is on several sites, by `site_priority` (LinkedIn, then Indeed, then Jora), so a role first found on Jora is rewritten for its LinkedIn or Indeed listing. If that rewrite fails, the Jora listing and its documents are used again.
- Each job costs about two Gemini requests (writing plus the fact-check).

`--validation` controls how strict this is:
- `strict`: banned words are errors.
- `normal` (default): banned words are warnings.
- `lenient`: banned words are ignored and the AI fact-check is skipped. The code fact checks always run.

**Running one step again.** Each stage skips jobs it has already done, so `applypilot run` never repeats work. To repeat a single step, use **Run again…** on a job's row (Apply tab), **Find jobs → Run a step again for jobs already done** for every job not applied to yet, or `applypilot rerun STEP [--url URL] [--min-score N]`. Without a job named, steps after scoring only take jobs at or above the minimum score. `discover` isn't per job. What a job has is kept until the new version is ready:

| Step | What runs again | AI requests |
|---|---|---|
| `discover` | Only the job-board searches that failed or were blocked last time, on just those boards (recorded in `~/.applypilot/discover_failed.json`; a search that works is taken off the list). To repeat every search, run Discover on its own (`applypilot run discover`) | none for LinkedIn and Indeed; Jora only to re-learn a page |
| `enrich` | Reads the ad's page again for the description and application link; a page that fails keeps the description it had | only for pages it can't read |
| `score` | Scores the job again; the old score stays if the request fails | about 1 per 10 jobs |
| `tailor` | Resume and cover letter, the same as **Redo** | about 2 per job |
| `cover` | Just the cover letter, keeping the resume; a letter that fails its fact checks keeps the old one | 1 or 2 per job |
| `review` | The AI fact-check from tailoring, on the documents the job has now. A job it flags keeps its documents and shows "the fact-check flagged it" with what was found, so you can Redo it or apply as it is | 1 per job |
| `pdf` | Renders both PDFs again from their text, then the ATS check | none |
| `ats` | The ATS check (your own resume's file when you gave one) | none |

**Your own documents.** On the Apply tab, **use my own resume** / **use my own cover letter** under a job takes a PDF or Word (.docx) file you wrote. Applying uploads yours (as `Your_Name_Resume.docx`, for example) instead of the generated one, and the form-filling AI reads its text. A job with your own resume isn't tailored (so it costs no requests) and is ready to apply even if its generated drafts were rejected. Its ATS score checks your file; a PDF with no readable text (a scan) is flagged, since applicant tracking systems can't read it either. **use generated** (or **remove my resume**) switches back. The files are kept in `~/.applypilot/own_documents`.

## Cover letter and PDF

Cover letters are written together with the tailored resume (above). The separate cover-letter stage only writes letters for jobs tailored before that change. Both documents are rendered to PDF for uploading, laid out for Australian applicant tracking systems (Workday, SuccessFactors, PageUp, JobAdder, Taleo) and recruiters:
- **A4**, one column, real selectable text, no tables, images or icons; standard headings (Summary, Skills, Experience, Projects, Education); empty sections left out.
- **Header:** name, target title, city and state with your work rights (e.g. "Sydney, NSW | Australian Citizen", from `work_authorization`), then email, phone and profile links (clickable).
- **Each job:** "Title, Employer" with the dates on the same line ("Mar 2021 – Present"), the tech stack underneath, then bullets: the order ATS parsers read employment history in.
- **Cover letter:** a plain business letter with your name and contact details, the date ("27 October 2026"), every paragraph and your sign-off.
- When the layout changes, existing PDFs are rendered again from their text on the next PDF stage.

## Apply

`EXECUTION_ENGINE` picks how applications are filled:

| Engine | Cost | Who clicks Submit |
|---|---|---|
| `local_playwright` (desktop app default) | Free with Gemini, or your own OpenAI / Anthropic key | **You** |
| `claude_code` (original) | Paid Claude Code subscription | **You** (the agent fills everything in and stops at the final Submit) |
| `codex` | Your ChatGPT plan (`codex login`) or OpenAI key | **You** (the same) |

**You always submit.** Claude Code and Codex are told never to click the button that sends an application (and not to email one either). They fill in every page, check it against your profile and resume, and stop on the final page with `RESULT:READY`. The launcher then leaves that Chrome window open, with a note in the log, until you close it (up to an hour): check it, click Submit yourself, close the window, and press **Mark applied** (or `applypilot apply --mark-applied URL`). Until then the job shows as *Saved for later*.

**AI provider.** Scoring, tailoring, cover letters and the local engine's form answers all use the provider chosen in Setup: Google Gemini (free daily allowance), or your own paid OpenAI or Anthropic (Claude) API key. Paid keys have no daily budget in the app; each tailored job costs about two requests. Personal identifiers are sent to every provider only as placeholders.

**Your subscription instead of a key** (`LLM_PROVIDER=codex_cli` or `claude_cli`). Each AI request runs the CLI once as a single-purpose subagent: the step's own instructions and data go in on stdin, and the reply comes back as text (JSON when asked), so scoring, tailoring, the fact-check, cover letters and form answers work exactly as with a key. Codex runs as `codex exec` in a read-only sandbox with no MCP servers; Claude Code as `claude -p --tools ""` with no MCP servers and no saved session. Both run in an empty temporary folder. It uses the plan the CLI is signed in with (`codex login`; `claude` then `/login`): no API bill, but it counts towards that plan's usage limits, and each request takes a few seconds longer. A usage-limit message stops the run cleanly (the remaining jobs wait), and a signed-out CLI says how to sign in.

**Codex engine.** Works like the Claude Code engine: the same instructions and the same Chrome, driven through the Playwright MCP server, and it also stops at the final Submit for you. Codex's own command runner is kept in a read-only sandbox, so it acts only through the browser. Install it with `npm install -g @openai/codex` and run `codex login` once (or set `OPENAI_API_KEY`). **In the desktop app** both start on the engine where you click Submit; Claude Code or Codex can be chosen in Setup after a confirmation (a `.env` copied from elsewhere doesn't switch the desktop app). The app also looks for `claude`, `codex` and `npx` in their usual install folders (npm, Homebrew, `~/.local/bin`, nvm), since a double-clicked app doesn't get the terminal's PATH, and uses Edge when Chrome isn't installed.

### The free local engine
1. Opens a **visible** browser with its own saved profile (`~/.applypilot/local-playwright-profile`). Log in to SEEK, LinkedIn or Workday there once and you stay logged in. It uses installed Chrome or Edge, or ApplyPilot's own Chromium (Setup → **Install browser**).
2. Reads every form field on the page, including inside embedded frames (Greenhouse).
3. Fills what it already knows **without the AI**: standard fields (name, email, phone, LinkedIn, address, location, current company, resume and cover letter uploads) from your profile, and screening questions from the **answer bank**. Fields that already hold a value are left alone.
   - **Searchable drop-downs** (Greenhouse, location autocompletes) are opened to read their options, and answers are picked by typing and clicking the matching suggestion. It never presses Enter, which could submit the form; if nothing matches, the "Your turn" panel says which field to pick.
   - **Site rules:** on SEEK it chooses "Upload a resumé" and uploads or writes the cover letter; on Lever the cover letter goes in "Additional information" when there's no cover letter field. Fields revealed by a choice are filled in a second pass.
4. Asks the AI (the provider chosen in Setup) only about the fields still unanswered (none left means no request). It sees an excerpt of the job description, so "Why this role?" and selection-criteria answers are specific to the job, and the job board the ad came from ("How did you hear about us?"). Salary figures in your profile are treated as base excluding super: it gives your expected salary, never less than your minimum (`salary_range_min`), and adds the 12% super guarantee when a form asks for the package. The prompt includes hints for SEEK (e.g. right-to-work questions), Greenhouse, Lever and Workday.
5. Clicks only **Next / Continue / Save and continue / Review** to move through multi-page forms.

**Uploads.** Each upload box gets the resume or the cover letter from its label, or, when it only says "Upload" or "Attach", from the text of its own row (PageUp, for example); a page's only upload box gets the resume. The resume never goes in two boxes, and a box already showing one of the files isn't filled again. The current job's two PDFs are in a visible folder, `~/ApplyPilot uploads` (set `APPLYPILOT_UPLOAD_DIR` to move it), which holds only that job's files. The "Your turn" panel lists them with their full paths, for uploading by hand.

### Job ads and employer sites
Most jobs found on Jora, Indeed and LinkedIn are applied for on the employer's own application system. The engine opens the ad, follows its Apply link (navigation only) and fills the form there. It never types into the job board's own search boxes.

- **An Apply button that isn't a link** (LinkedIn Easy Apply, Jora quick apply): click it yourself, then **Fill this page again**. A form that opens in a pop-up (Easy Apply) is filled inside the pop-up only, including its own Next / Review buttons; Submit stays yours.
- **An Apply button that opens a new tab** (LinkedIn's employer-site Apply): the engine switches to the new tab.

- **Check where to apply** (Apply tab, or `applypilot locate`) opens each ready job in the same visible browser, follows the ad to the application page and labels the job, e.g. "Applies on Greenhouse · no account" or "Workday · account needed". It fills nothing and uses no AI. The **Ready: no employer account** view lists the quick ones (no account, or just your SEEK / LinkedIn login). Jobs are also labelled when you apply.
- **Sign-in and create-account pages** (Workday, iCIMS, Taleo, SuccessFactors and other employer sites): the engine types your email, never a password, asks the AI nothing and clicks nothing. The "Your turn" panel says to sign in or create the account, then **Fill this page again**. The browser remembers the login for that employer. A long form that merely offers an optional password is filled as usual.

**Logging in (Google sign-in and friends).** Google, and some other sites, refuse to sign in while a browser is controlled by automation ("This browser or app may not be secure"). Setup → **Log in to sites** (or `applypilot login`, or a job's **Log in** button when it needs an employer account) opens the same browser and profile as a normal window with no automation. Sign in to Google, LinkedIn, SEEK or the employer there, then close the window with its X. The engine's browser then reuses those logins. The two can't be open at once, so close the login window before applying.

| Applies on | Account |
|---|---|
| Greenhouse, Lever, SmartRecruiters, Ashby, Workable, JobAdder, BambooHR | None |
| SEEK, LinkedIn Easy Apply, Indeed | Your job-board login (once) |
| Workday, iCIMS, Taleo, SuccessFactors, Oracle, PageUp | An account with each employer |

### Privacy
- The AI (Gemini, OpenAI or Claude) never sees your name, email, phone, street address, postcode, profile URLs, or any profile field that looks like an ID number (tax file number, licence number, date of birth and so on). It sees `{{PLACEHOLDER}}` tokens, and the real values are filled in on your computer. (Claude Code and Codex are different: they work from the full application instructions, like the original ApplyPilot.)
- The same is done to your resume text before it's sent.
- Your job-site password is never sent.
- Your city, state, work rights and salary expectations **are** sent, because they're needed to answer eligibility questions.

### Safety gate
- Automation **never** clicks anything labelled Submit, Apply, Send, Finish or Confirm. ("Continue to apply" counts as Submit, because any of those words takes priority.)
- When it reaches the end, those buttons are outlined in red and the app waits for you, either on the **"Your turn"** panel in the web UI or at the terminal prompt.
- After you decide, the job is recorded as:

| Your answer | Recorded as | What happens next |
|---|---|---|
| I submitted it | `applied` | Done |
| Save for later | `manual` | Out of the queue; **Reset** (or `--reset-failed`) brings it back |
| Never apply to this job | `failed` (reason `user_declined`) | Never offered again |
| Fill this page again | (no change) | Re-reads and re-fills the current page, e.g. after you log in |

A job you applied to yourself, outside the app, can be recorded with **Mark applied** on the Apply tab.

### Answer bank
- Short screening answers (drop-downs, radio buttons, short text such as "Notice period") are saved in the `answers` table and reused when the same question comes up again, on any site.
- Answers the AI gave are saved as **AI**. Answers you type or change in the browser while the app waits for you are saved as **you**, and an AI answer never replaces one of yours.
- Long free-text answers ("Why do you want this job?") and anything containing your personal details are never saved.
- Setup → **Saved answers** lists them; delete any you don't want reused.

## AI usage

With Gemini (the default), Google's free tier limits **requests per day** per model. Everything uses `gemini-3.5-flash-lite` (about 500/day). With your own OpenAI or Anthropic key the counts below are the same, billed per use by that provider, with no daily limit in the app. Approximate usage:

| Stage | Requests |
|---|---|
| Score | ~1 per 10 jobs (skipped titles are free) |
| Tailor + cover letter | usually 2 per good match (writing + fact-check); more when an attempt is sent back |
| Jora | ~2 once, to learn the page; then 0 (re-learns only if the saved plan stops finding jobs) |
| Apply | ~1 per form page; 0 for pages where every field is a standard field or a saved answer |

### Daily budget
ApplyPilot counts every Gemini request against the daily limit (`GEMINI_DAILY_LIMIT`, default 500), per US Pacific day because that's when Google resets it. The header in the web UI shows how many are left.

- Finding and preparing jobs (scoring, tailoring, Jora) stops cleanly when only the apply reserve (`GEMINI_APPLY_RESERVE`, default 50) is left. Jobs it didn't get to are untouched and picked up on the next run. Tailoring works through the highest-scoring jobs first, so the budget goes to your best matches.
- Applying can use the reserve. When it's all gone, the app tells you on the "Your turn" panel and you can fill the page yourself; pages that need no AI still fill.
- If Google reports the daily quota is used up (for example the key is also used elsewhere), the app believes Google and stops straight away instead of retrying.
- OpenAI, Anthropic and local models aren't budgeted.

[IMPROVEMENTS.md](IMPROVEMENTS.md) is the plan to reduce this further.
