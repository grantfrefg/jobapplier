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

**Location filter:** a job is kept if its location matches one of the `location_accept` patterns, or it's remote. The setup generates those patterns from your city: "Sydney, NSW" becomes Sydney, NSW and New South Wales. Matching is whole-word, so "WA" doesn't match "Warsaw". With no patterns configured, everything that isn't explicitly rejected is kept.

## De-duplicate

The same role is often listed on Indeed, LinkedIn and Jora. Two listings count as one role if any of these hold:
- they link to the same application page on the employer's site;
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
3. **Deal-breakers.** The scorer also sees your target role, years, location, work rights and security clearance from your profile. A job you can't take (citizenship or a clearance you don't hold, onsite elsewhere when you won't relocate, far too senior or junior, a mandatory registration you don't have) scores 3 or lower.

The prompts are written for engineers (software first, other disciplines too) applying in Australia: Australian English, 2-page resumes, DD/MM/YYYY dates, AUD.

## Tailor

For jobs scoring at or above `--min-score` (default 7), the AI rewrites your resume for the role:
- it reorders, and rewords bullets where the job uses different terms for the same work;
- it leads with the must-haves the scorer found in your resume;
- it never adds a tool, skill or number that isn't in your resume or `skills_boundary`.

The tailored resume and a short, specific cover letter come from **one AI request**.

Two checks guard every result:
- **Code checks, always:** banned buzzwords, required sections, and no missing or invented employers, projects or skills (`resume_facts` and `skills_boundary` in your profile list what must be kept). The letter must also have the right format.
- **An AI fact-check ("judge"), only when needed:** it runs when code spots a likely invention anywhere in the resume or letter: a number that isn't in your original resume, or a tool from the watchlist you never mention.

If the resume fails, both documents are regenerated. If only the letter fails, just the letter is retried.

**Redo.** To rewrite documents that already exist (after changing your resume, profile or the prompts), press **Redo** on a job, or **Redo all documents** on the Apply tab (`applypilot redo` in the terminal, `--url` for one job).
- Only the jobs you redo are rewritten, whatever their score; nothing else in the queue is tailored.
- Nothing is deleted first: a job keeps its old resume and cover letter (and stays ready to apply) until the new ones pass the checks. If a rewrite doesn't pass, the old documents stay. If the day's Gemini budget runs out, the rest are rewritten on the next run.
- Jobs you've applied to keep theirs, as does a job open in the browser at that moment; jobs stuck "in progress" from an apply run that never finished are put back in the queue.
- Redo also re-picks which listing to use when a role is on several sites, by `site_priority` (LinkedIn, then Indeed, then Jora), so a role first found on Jora is rewritten for its LinkedIn or Indeed listing. If that rewrite fails, the Jora listing and its documents are used again.
- Each job costs about one Gemini request.

`--validation` controls how strict this is:
- `strict`: banned words are errors, and the AI fact-check always runs.
- `normal` (default): banned words are warnings, and the fact-check runs only when needed.
- `lenient`: never runs the AI fact-check.

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
| `local_playwright` (desktop app default) | Free: your Gemini key | **You** |
| `claude_code` (original) | Paid Claude Code subscription | The agent, automatically |

### The free local engine
1. Opens a **visible** browser with its own saved profile (`~/.applypilot/local-playwright-profile`). Log in to SEEK, LinkedIn or Workday there once and you stay logged in. It uses installed Chrome or Edge, or ApplyPilot's own Chromium (Setup → **Install browser**).
2. Reads every form field on the page, including inside embedded frames (Greenhouse).
3. Fills what it already knows **without the AI**: standard fields (name, email, phone, LinkedIn, address, location, current company, resume and cover letter uploads) from your profile, and screening questions from the **answer bank**. Fields that already hold a value are left alone.
   - **Searchable drop-downs** (Greenhouse, location autocompletes) are opened to read their options, and answers are picked by typing and clicking the matching suggestion. It never presses Enter, which could submit the form; if nothing matches, the "Your turn" panel says which field to pick.
   - **Site rules:** on SEEK it chooses "Upload a resumé" and uploads or writes the cover letter; on Lever the cover letter goes in "Additional information" when there's no cover letter field. Fields revealed by a choice are filled in a second pass.
4. Asks Gemini only about the fields still unanswered (none left means no request). The prompt includes hints for SEEK (e.g. right-to-work questions), Greenhouse, Lever and Workday.
5. Clicks only **Next / Continue / Save and continue / Review** to move through multi-page forms.

### Job ads and employer sites
Most jobs found on Jora, Indeed and LinkedIn are applied for on the employer's own application system. The engine opens the ad, follows its Apply link (navigation only) and fills the form there.

- **Check where to apply** (Apply tab, or `applypilot locate`) opens each ready job in the same visible browser, follows the ad to the application page and labels the job, e.g. "Applies on Greenhouse · no account" or "Workday · account needed". It fills nothing and uses no AI. The **Ready: no employer account** view lists the quick ones (no account, or just your SEEK / LinkedIn login). Jobs are also labelled when you apply.
- **Sign-in and create-account pages** (Workday, iCIMS, Taleo, SuccessFactors and other employer sites): the engine types your email, never a password, asks the AI nothing and clicks nothing. The "Your turn" panel says to sign in or create the account, then **Fill this page again**. The browser remembers the login for that employer. A long form that merely offers an optional password is filled as usual.

**Logging in (Google sign-in and friends).** Google, and some other sites, refuse to sign in while a browser is controlled by automation ("This browser or app may not be secure"). Setup → **Log in to sites** (or `applypilot login`, or a job's **Log in** button when it needs an employer account) opens the same browser and profile as a normal window with no automation. Sign in to Google, LinkedIn, SEEK or the employer there, then close the window with its X. The engine's browser then reuses those logins. The two can't be open at once, so close the login window before applying.

| Applies on | Account |
|---|---|
| Greenhouse, Lever, SmartRecruiters, Ashby, Workable, JobAdder, BambooHR | None |
| SEEK, LinkedIn Easy Apply, Indeed | Your job-board login (once) |
| Workday, iCIMS, Taleo, SuccessFactors, Oracle, PageUp | An account with each employer |

### Privacy
- Gemini never sees your name, email, phone, street address, postcode, profile URLs, or any profile field that looks like an ID number (tax file number, licence number, date of birth and so on). It sees `{{PLACEHOLDER}}` tokens, and the real values are filled in on your computer.
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

### Answer bank
- Short screening answers (drop-downs, radio buttons, short text such as "Notice period") are saved in the `answers` table and reused when the same question comes up again, on any site.
- Answers the AI gave are saved as **AI**. Answers you type or change in the browser while the app waits for you are saved as **you**, and an AI answer never replaces one of yours.
- Long free-text answers ("Why do you want this job?") and anything containing your personal details are never saved.
- Setup → **Saved answers** lists them; delete any you don't want reused.

## Gemini usage

Google's free tier limits **requests per day** per model. Everything uses `gemini-3.5-flash-lite` (about 500/day). Approximate usage:

| Stage | Requests |
|---|---|
| Score | ~1 per 10 jobs (skipped titles are free) |
| Tailor + cover letter | usually 1 per good match; 2 when the fact-check runs or the letter is retried |
| Jora | ~2 once, to learn the page; then 0 (re-learns only if the saved plan stops finding jobs) |
| Apply | ~1 per form page; 0 for pages where every field is a standard field or a saved answer |

### Daily budget
ApplyPilot counts every Gemini request against the daily limit (`GEMINI_DAILY_LIMIT`, default 500), per US Pacific day because that's when Google resets it. The header in the web UI shows how many are left.

- Finding and preparing jobs (scoring, tailoring, Jora) stops cleanly when only the apply reserve (`GEMINI_APPLY_RESERVE`, default 50) is left. Jobs it didn't get to are untouched and picked up on the next run. Tailoring works through the highest-scoring jobs first, so the budget goes to your best matches.
- Applying can use the reserve. When it's all gone, the app tells you on the "Your turn" panel and you can fill the page yourself; pages that need no AI still fill.
- If Google reports the daily quota is used up (for example the key is also used elsewhere), the app believes Google and stops straight away instead of retrying.
- OpenAI and local models aren't budgeted.

[IMPROVEMENTS.md](IMPROVEMENTS.md) is the plan to reduce this further.
