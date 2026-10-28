# Configuration

All your settings and data live in `~/.applypilot/`. Set the environment variable `APPLYPILOT_DIR` to use a different folder. The web UI's **Setup** tab, or `applypilot init` in the terminal, writes these files for you, so editing them by hand is optional.

| File | Purpose |
|---|---|
| `.env` | AI provider, API keys, model, apply engine |
| `searches.yaml` | What and where to search, and which sources to use |
| `profile.json` | Your details and the answers used on application forms |
| `resume.txt` | Your master resume as plain text (what the AI reads) |
| `resume.pdf` | Optional; the tailored PDFs are generated from the text |
| `applypilot.db` | The job database (SQLite), including saved screening answers |
| `tailored_resumes/`, `cover_letters/` | Generated documents |
| `site_plans/` | How smart extract reads each site (delete a file to make that site re-learn) |
| `own_documents/` | Resumes and cover letters you uploaded for particular jobs (Apply tab → **use my own resume / cover letter**) |
| `~/ApplyPilot uploads/` | Outside `~/.applypilot`: the current application's resume and cover letter, for picking in a file dialog. Set `APPLYPILOT_UPLOAD_DIR` to move it |
| `local-playwright-profile/` | The local engine's browser profile (keeps job-site logins) |
| `logs/` | Run logs; `desktop.log` for the desktop app |

---

## `.env`

| Key | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Your Google Gemini key ([get one free](https://aistudio.google.com/apikey)). Check it with **Test key** in Setup |
| `OPENAI_API_KEY` | — | Your own paid OpenAI API key (default model `gpt-5-mini`) |
| `ANTHROPIC_API_KEY` | — | Your own paid Anthropic API key, for Claude (default model `claude-opus-5-5`; `claude-sonnet-5-5` or `claude-haiku-5-5` cost less) |
| `LLM_PROVIDER` | the first with a key | `gemini`, `openai`, `anthropic` or `local`: which AI scores jobs, writes documents and answers forms (Setup → **AI for scoring, resumes and form answers**) |
| `LLM_MODEL` | the provider's default | Model for every AI step; ignored if it isn't one of the chosen provider's models |
| `EXECUTION_ENGINE` | `claude_code` (`local_playwright` in the desktop app, until you choose and confirm another in Setup) | `local_playwright`: fills forms and **you** click Submit (with any provider above). `claude_code`: paid Claude Code CLI, submits automatically. `codex`: OpenAI's Codex CLI, submits automatically |
| `CODEX_MODEL` | Codex's default | Model for the `codex` engine |
| `AGENT_ENGINE_CONFIRMED` | — | Written by the desktop app when you confirm Claude Code or Codex in Setup; without it the desktop app uses `local_playwright` |
| `LLM_URL`, `LLM_API_KEY` | — | Use a local OpenAI-compatible server (llama.cpp, Ollama) instead |
| `GEMINI_DAILY_LIMIT` | `500` | Gemini requests per day for your model (Google's free-tier limit). Requests are counted per US Pacific day, when Google resets them |
| `GEMINI_APPLY_RESERVE` | `50` | Requests kept for applying: finding and preparing jobs stops when only this many are left |
| `CAPSOLVER_API_KEY` | — | CAPTCHA solving, `claude_code` engine only |
| `CHROME_PATH` | auto-detected | Browser for the apply engines (Chrome, Edge or Chromium) |

Put comments on their own lines. `KEY=   # note` makes the note the value.

---

## `searches.yaml`

Example, as generated for Sydney:

```yaml
country: "australia"             # also picks Indeed's country site

defaults:
  location: "Sydney, NSW"
  hours_old: 72                  # only jobs posted in the last N hours
  results_per_site: 50           # per board, per job title

locations:
  - location: "Sydney, NSW"
    remote: false
  - location: "Australia"        # added by "Also include remote jobs"
    remote: true

location_accept: ["Sydney", "NSW", "New South Wales"]

sites: [indeed, linkedin]        # JobSpy boards
smart_extract_sites: ["Jora"]    # sites from config/sites.yaml
workday_employers: []            # employers from config/employers.yaml ([] = off)
site_priority: [linkedin, indeed, jora]

exclude_titles: ["intern", "graduate"]

queries:
  - query: "Data Analyst"
    tier: 1
  - query: "Business Analyst"
    tier: 2
```

| Key | Meaning |
|---|---|
| `country` | Country for Indeed (`australia`, `usa`, `canada`, `uk`, `new zealand`, …). Default `usa` |
| `defaults.country_indeed` | Overrides `country` for Indeed only |
| `locations` | Each search runs once per location. `remote: true` limits that search to remote jobs |
| `location_accept` | Keep jobs whose location contains one of these words; remote jobs are always kept. Empty = keep everything not rejected |
| `location_reject_non_remote` | Drop non-remote jobs whose location contains one of these |
| `sites` (or `boards`) | JobSpy boards: `indeed`, `linkedin`, `zip_recruiter`, `google`, … Default `indeed, linkedin, zip_recruiter` |
| `smart_extract_sites` | Names from `config/sites.yaml` to scrape with smart extract. **Omit = all sites; `[]` = none** |
| `workday_employers` | Keys or names from `config/employers.yaml`. **Omit = all employers; `[]` = none** |
| `site_priority` | Which listing to keep when one role is on several sites. Default `linkedin, indeed, jora` |
| `exclude_titles` | Skip jobs whose title contains any of these, matched as whole words, with no AI request |
| `score_batch_size` | Jobs scored per AI request. Default 10 |
| `queries` | Job titles to search. `tier` 1–3 is the priority; Workday uses tiers ≤ `workday_max_tier` (default 2) |
| `proxy` | `host:port:user:pass` for scraping |

---

## `profile.json`

Sections, each a set of fields shown as a form on the Setup page. Fields with only a few sensible answers are drop-downs (Yes/No questions, state, country, work rights, security clearance, notice period, salary currency, education, and the diversity questions), so the wording is always one the app understands. Older or hand-typed values are mapped onto them when the profile loads ("true" becomes Yes, "Masters" becomes Master's degree); anything unrecognised is flagged to pick again.

| Section | Used for |
|---|---|
| `personal` | Name, email, phone, address, LinkedIn/GitHub/portfolio URLs, job-site password. **Required:** `full_name`, `email`, `phone` |
| `work_authorization` | Right to work, sponsorship, `work_permit_type` (e.g. Australian citizen, Permanent resident: shown in the resume header), `security_clearance` (Baseline, NV1, NV2, PV; blank = none). Add fields such as `tax_file_number` if forms ask; ID-like values are never sent to the AI |
| `availability` | Start date, notice period, full-time/contract, `willing_to_relocate`, `drivers_licence` |
| `compensation` | Salary expectation, currency and range: **base salary, excluding super** (forms asking for a package get 12% super added) |
| `experience` | Years, education, current title/company, target role (the prompts write for this role and seniority) |
| `skills_boundary` | Extra skills the tailor may mention when your resume doesn't name them. Template example values are ignored until `skills_confirmed` is true (Setup: **My skills list is accurate**). Skills in your resume are always allowed, in any common short or long form |
| `resume_facts` | Employers, projects, school and real metrics the tailor must keep exactly |
| `eeo_voluntary` | Answers to voluntary diversity questions, including Aboriginal or Torres Strait Islander status (default "Prefer not to say") |
| `skills_confirmed` | `true` once you've confirmed your skills list (a single value, not a section) |

What the AI sees (Gemini, OpenAI or Claude): everything except the password. Identifiers (name, email, phone, street address, postcode, URLs, ID numbers) are replaced by placeholders and filled in locally. See [HOW_IT_WORKS.md](HOW_IT_WORKS.md#privacy).

---

## Bundled registries (`src/applypilot/config/`)

| File | Contents |
|---|---|
| `sites.yaml` | Smart-extract sites, including Jora, plus the original Canadian/US boards; blocked sites; base URLs; ATSs that need manual applying |
| `employers.yaml` | Workday employers (the original set is Canadian/US) |
| `searches.example.yaml` | The original example search config |
