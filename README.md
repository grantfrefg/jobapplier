# jobapplier

**Find Australian jobs, tailor your resume for each one, and have the application form filled in for you, then check it and click Submit yourself.** Free to run, on your own computer.

jobapplier is a fork of [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) by Pickle-Pixel, reworked for Australian job hunting and for running on Google Gemini's free tier. The app and its commands are still called **ApplyPilot**.

What's different from the original:

- **Free.** Every AI step runs on your own Google Gemini key (`gemini-3.5-flash-lite`, free tier). Form-filling uses a local browser; there's no paid Claude Code subscription, though that engine is still available. Prefer a paid model? Use your own OpenAI or Anthropic (Claude) API key instead, and Codex can drive the browser like Claude Code.
- **You submit.** The app fills in the form and stops. Submit/Apply buttons are outlined in red, and you review and click them yourself.
- **Private.** Your name, contact details, address and ID numbers are sent to the AI only as placeholders and filled in locally.
- **Australian job sources.** Indeed Australia, LinkedIn and Jora, filtered to your city and state. Jora ads open the employer's own application form.
- **One application per role.** The same job on several boards is detected, and you apply once.
- **Written for engineers in Australia.** Scoring knows your deal-breakers (work rights, security clearance, location, seniority). Resumes and cover letters use Australian English and come out as ATS-friendly A4 PDFs with your location and work rights at the top.
- **Honest documents.** Every title, skill, tool, number, employer and qualification in a tailored resume or cover letter is checked against your real resume in code, and an AI fact-check reads every one. Nothing you haven't done gets in, including the job's own title.
- **ATS check.** Every tailored resume gets a score for how Australian employers' systems (Workday, SuccessFactors, PageUp, JobAdder...) will parse and search it, with what to fix.
- **Knows where each job applies.** Jobs are labelled with the employer's application system and whether it needs an account, so you can do the quick ones first. Sign-in pages and Google sign-in are handled.
- **Fewer AI requests.** Contact details, uploads and questions you've answered before are filled in without the AI; Greenhouse, Lever and SEEK forms are handled specially; a daily budget keeps you inside the free tier.
- **No terminal needed.** A web UI covers setup, finding jobs and applying, and there's a double-click desktop app for Windows and Mac.

---

## Getting started

Pick the option that suits you.

### 1. Desktop app (easiest, no installs)
Download the zip for your computer, unzip it, and double-click **ApplyPilot**. The page opens in your browser.

- **Windows:** `ApplyPilot-Windows.zip`
- **Mac:** `ApplyPilot-Mac-AppleSilicon.zip` or `ApplyPilot-Mac-Intel.zip`

The first time, your computer warns that the app isn't verified. The `READ_ME_FIRST` file in the zip shows the one-time click-through. To build the zips, see [docs/DESKTOP_APP.md](docs/DESKTOP_APP.md).

### 2. Dev container (for working on the code, recommended on Windows)
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [VS Code](https://code.visualstudio.com/) with the **Dev Containers** extension.
2. Open this repo and choose **Reopen in Container**. The first build takes 5–10 minutes.
3. Open **http://localhost:8000**. The web UI starts automatically.
4. Watch the automated browser on the page itself, or at **http://localhost:6080** (password `vscode`).

If the page doesn't load, run `python -m applypilot ui` in VS Code's terminal (it runs inside the container).

### 3. Install with pip
```bash
pip install -e ".[ui]" -r packaging/requirements-desktop.txt
python -m playwright install chromium
applypilot ui        # opens the web UI at http://localhost:8000
```

---

## Using it

The web UI has three tabs.

**1. Setup**
- Paste your free [Gemini API key](https://aistudio.google.com/apikey) and press **Test key**. To use your own paid key instead, pick OpenAI or Anthropic (Claude) under **AI for scoring, resumes and form answers**, paste that key and press **Test key**. Paid API keys are billed per use by OpenAI or Anthropic, separately from a ChatGPT or Claude subscription.
- Paste your resume as plain text, and optionally upload the PDF.
- Choose your country, city and job titles, plus any title words to skip (e.g. *intern, graduate*).
- Fill in your profile: contact details, work rights, security clearance, notice period, relocation, salary and the other answers used on application forms. Questions with a fixed set of answers are drop-downs. Replace the example values ("Your City", "Company A"...): the profile won't save while any are left.
- Check the **Skills boundary** list: remove the example skills you don't use, add ones you do that your resume doesn't name, and tick **My skills list is accurate**. Resumes only ever mention skills from your resume or this list.
- **Log in to sites** (under Browser) opens the app's browser as a normal window so you can sign in to Google, LinkedIn and SEEK. Google blocks sign-in in the automated window; close this one when you're done and the app stays logged in.
- **Saved answers** lists the screening answers the app has remembered (yours win over the AI's); delete any you don't want reused.
- **Daily request limit** (under the Gemini key) sets the free-tier limit and how many requests to keep for applying. The header shows how many are left today.

**2. Find jobs**
- Press **Run**. It searches the job boards, scores each job against your resume, and writes a tailored resume and cover letter for the good matches, with a live log.

**3. Apply**
- Each job shows its **ATS** score (hover for what to fix, missing keywords and any skills that were removed).
- **Check where to apply** opens each job in the browser and labels where it applies, e.g. "Greenhouse · no account" or "Workday · account needed". The **Ready: no employer account** view lists the quick ones. Jobs needing an account get a **Log in** button.
- Press **Apply** next to a job. A browser opens, and the app fills the form, uploads your documents and moves through "Next" pages. On a job-board ad (Jora, SEEK, Indeed, LinkedIn) it follows the ad's Apply link to the real form first; for LinkedIn **Easy Apply**, click Easy Apply yourself, then **Fill this page again**.
- On a sign-in or create-account page it fills in your email (never a password) and tells you what to do.
- Fields it already knows (name, contact details, location, uploads, answers you've given before) are filled without the AI; Gemini is only asked about the rest. Any answer you type or change before submitting is remembered for next time.
- It stops at **"Your turn: review and submit"**. Check the form, fix anything, click Submit in the browser, then tell the app what you did. To upload a file by hand, the panel lists this job's resume and cover letter, which are in the `ApplyPilot uploads` folder in your home folder:
  - **I submitted it**
  - **Save for later**
  - **Fill this page again** (e.g. after you log in to SEEK)
  - **Never apply to this job**
- **Redo** on a job (or **Redo all documents**) rewrites its resume and cover letter, e.g. after you update your resume, profile or skills list. The old documents stay until the new ones pass the checks.

Everything is stored on your computer in `~/.applypilot/`.

---

## How it works

| Stage | What happens | AI requests |
|---|---|---|
| **Discover** | Searches Indeed Australia and LinkedIn (via JobSpy) and Jora. Keeps jobs in your area (plus remote, if you ask). How to read Jora's pages is learned once and saved | Jora only: ~2 the first time, then 0 |
| **De-duplicate** | Links repeat listings of the same role (same company and title, same employer link, or near-identical text). Keeps one, preferring LinkedIn, then Indeed, then Jora | None |
| **Enrich** | Fetches each job's full description and application link | Only for unusual pages |
| **Score** | Skips titles on your skip list for free, then rates the rest 1–10 against your resume, 10 jobs per request, like an engineering recruiter. Jobs you can't take (citizenship or a clearance you don't hold, onsite elsewhere, far too senior or junior) score 3 or lower | ~1 per 10 jobs |
| **Tailor + cover letter** | For each good match (score ≥ 7), rewrites your resume and writes a short, specific cover letter in one request, in Australian English, using the ad's own words for skills you have. Code checks every title, skill, tool, number, employer and qualification against your real resume (fixing or rejecting what doesn't match), then an AI fact-check reads it | Usually 2 per job |
| **PDF** | A4, one-column, ATS-friendly resume (location and work rights in the header, title, employer and dates on one line) and a plain business-letter cover letter | None |
| **ATS check** | Scores each tailored resume for parsing, Australian knockout facts (work rights, location), the ad's keywords and spelling | None |
| **Apply** | Fills the form in a visible browser and waits for you to submit. Known fields and saved answers need no AI; Greenhouse, Lever and SEEK forms get site-specific handling | 0–1 per form page |

Free-tier limits are per **request per day**: `gemini-3.5-flash-lite` allows about 500. A typical day of about 200 jobs found, about 20 good matches and about 10 applications uses roughly 80–100. Every request is counted (by US Pacific day, when Google resets the quota): finding and preparing jobs stops cleanly when only the apply reserve (50 by default) is left, and picks up where it left off the next day. [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md) lists what's been done to cut usage.

More detail on each stage, privacy and the safety gate: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

---

## Configuration

Everything can be set in the web UI, and it's all saved as files in `~/.applypilot/`:

| File | What's in it |
|---|---|
| `.env` | Gemini key and model, apply engine, daily request limit and apply reserve |
| `searches.yaml` | Country, location, job titles, title skip list, which job sources to use |
| `profile.json` | Your details and form answers, including work rights, security clearance, notice period, relocation and driver's licence |
| `resume.txt` / `resume.pdf` | Your master resume |
| `applypilot.db` | Jobs (with where each applies and its ATS check), saved screening answers and today's Gemini request count |
| `site_plans/` | How each smart-extract site (Jora) is read; delete a file to make it re-learn |
| `~/ApplyPilot uploads/` | The current application's resume and cover letter PDFs, for uploading by hand (outside `~/.applypilot`, so file pickers show it) |

Every setting is listed in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Command line

The web UI covers everything, but every step is also a command:

```
applypilot ui                      # web UI (http://localhost:8000)
applypilot init                    # setup wizard in the terminal
applypilot doctor                  # check what's installed and configured
applypilot run [stages...]         # discover enrich score tailor cover pdf (default: all)
applypilot run --min-score 8       # only tailor jobs scoring 8+
applypilot run --validation lenient    # fewer AI requests (skips the AI fact-check)
applypilot apply --url URL         # fill one application
applypilot apply --limit 5         # the next 5 best-scoring jobs
applypilot apply --reset-failed    # bring back skipped / saved-for-later jobs
applypilot redo [--url URL]        # rewrite resumes and cover letters (all, or one job)
applypilot ats [--url URL]         # ATS check of each tailored resume
applypilot locate                  # find where each ready job applies (opens the browser)
applypilot login [--url URL]       # normal browser window to sign in (Google, LinkedIn, SEEK...)
applypilot status                  # counts per stage
applypilot dashboard               # HTML report of all jobs
```

With the free engine, `applypilot apply` stops before each Submit and waits in the terminal:
- **Enter:** done; you're then asked whether you submitted.
- **r:** fill the page again.
- **x:** never apply to this job.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Find jobs only finds Jora jobs | The log shows `JobSpy crawl failed: No module named 'curl_cffi'`: run `pip install curl_cffi`, then run Find jobs again. LinkedIn sometimes blocks searches for a few hours ("LinkedIn: blocked: too many requests" in the log): the app skips it for the rest of that run; run Find jobs again later |
| "This browser or app may not be secure" (Google sign-in) | Use Setup → **Log in to sites**, sign in there and close the window |
| A job's resume doesn't change after **Redo** | Its new version failed the fact checks, so the old one was kept. The reasons are in `~/.applypilot/tailored_resumes/<job>_REPORT.json` (`validator` and `judge`) |
| A resume lists a skill you don't have, or drops one you do | Update **Skills boundary** in your profile, tick **My skills list is accurate**, then **Redo** |
| The page at localhost:8000 won't load (dev container) | Run `python -m applypilot ui` in VS Code's terminal |

---

## Development

- **Branches and PRs:** work on a branch, then open a pull request into `master`. CI runs the full test suite on Python 3.11–3.13 for every PR.
- **Tests:** `xvfb-run -a pytest tests` (the virtual display lets the browser tests run).
- **Desktop app builds:** the **Desktop app** GitHub Actions workflow. See [docs/DESKTOP_APP.md](docs/DESKTOP_APP.md).

Details are in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Credits and licence

Built on [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) by [Pickle-Pixel](https://github.com/Pickle-Pixel). The original project isn't affiliated with applypilot.app, useapplypilot.com or other products using the name, and neither is this fork.

Licensed under the [GNU Affero General Public License v3.0](LICENSE), like the original. If you give someone this software, or run a modified version as a service for others, you must make its source code available to them under the same licence.

Job sites have their own terms of use. This tool fills in forms for you to review and submit yourself; use it considerately.
