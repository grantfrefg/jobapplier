const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let status = null;

// ---------- helpers ----------
async function api(path, opts = {}) {
  const headers = opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {};
  const r = await fetch(path, { ...opts, headers });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
  return data;
}
const post = (p, b = {}) => api(p, { method: "POST", body: b instanceof FormData ? b : JSON.stringify(b) });

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = bad ? "bad" : ""; t.style.display = "block";
  clearTimeout(t._h); t._h = setTimeout(() => (t.style.display = "none"), 3500);
}
async function guarded(fn, okMsg) {
  try { await fn(); if (okMsg) toast(okMsg); } catch (e) { toast(e.message, true); }
}
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) n.append(k instanceof Node ? k : document.createTextNode(k ?? ""));
  return n;
}
// Job links come from job boards: only ever make web addresses clickable
const webLink = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const human = (k) => k.replace(/_/g, " ").replace(/\b\w/, (c) => c.toUpperCase());

// ---------- tabs ----------
$$("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$("section.tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  try { localStorage.setItem("ap-tab", name); } catch {}
  if (name === "apply") { loadJobs(); ensureVnc(); }
  if (name === "find") loadChoose();
  if (name === "setup") loadAnswers().catch(() => {});
}

// Links that open another tab (Guide → Setup, ...), optionally scrolling to a card
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-goto]");
  if (!a) return;
  e.preventDefault();
  showTab(a.dataset.goto);
  const target = a.dataset.focus && document.getElementById(a.dataset.focus);
  if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  else window.scrollTo(0, 0);
});

// ---------- status ----------
async function refreshStatus() {
  try { status = await api("/api/status"); } catch { return; }
  showFailedSearches(status.discover_failed || { searches: 0, boards: [] });
  showSubscriptionCli();
  const local = status.engine === "local_playwright";
  const eb = $("#engine-badge");
  eb.textContent = local ? "Browser engine (" + (status.provider_name || "no AI key") + ", " + status.local_model + ")"
    : status.engine === "codex" ? "Codex engine" : "Claude Code engine";
  eb.className = "badge " + (local ? "ok" : "");
  const tb = $("#tier-badge");
  tb.textContent = "Tier " + status.tier + ": " + status.tier_label;
  tb.className = "badge " + (status.tier === 3 ? "ok" : "warn");
  $("#data-dir").textContent = status.data_dir;
  $$(".data-dir").forEach((c) => (c.textContent = status.data_dir));
  const b = status.budget, bb = $("#budget-badge");
  bb.style.display = b ? "" : "none";
  if (b) {
    bb.textContent = b.left + " of " + b.limit + " Gemini requests left today";
    bb.className = "badge " + (b.left === 0 ? "warn" : b.pipeline_left === 0 ? "warn" : "ok");
    bb.title = "Resets in about " + b.resets_in_hours + " hours (midnight US Pacific time)";
    $("#budget-detail").textContent = "Today: " + b.used + " used, " + b.pipeline_left +
      " left for finding and preparing jobs, " + b.left + " in total (" + b.apply_reserve +
      " kept for applying). Resets in about " + b.resets_in_hours + " hours.";
  }
  document.body.classList.toggle("desktop", status.desktop);
  $("#quit").style.display = status.desktop ? "" : "none";
  tb.style.display = status.desktop ? "none" : "";
  if (status.desktop) {
    $("#engine-sub").textContent = "Choose how applications get filled in, and the AI that reads job forms and " +
      "your resume. Gemini is free; OpenAI and Claude use your own paid key.";
  }

  const c = status.checks;
  const items = [
    [status.provider && status.provider.endsWith("_cli") ? "AI ready (" + status.provider_name + ")" : "AI key saved",
     c.llm_key],
    ["Resume text", c.resume_txt],
    ["Resume PDF", c.resume_pdf],
    ["Profile", c.profile],
    [status.industry ? "Industry: " + status.industry
       : "Industry: choose yours in Your profile (it sets your skill boxes)", c.profile && !!status.industry],
    [status.example_skills && status.example_skills.length
       ? "Skills list: remove the examples you don't use (" + status.example_skills.join(", ") +
         "), then tick 'My skills list is accurate'" : "Skills list",
     c.profile && !(status.example_skills || []).length],
    ["Job search", c.searches],
    local ? ["Browser ready", c.playwright && c.browser]
      : [(status.engine === "codex" ? "Codex" : "Claude Code") + " CLI found", c.agent_cli],
  ];
  $("#checklist").replaceChildren(...items.map(([t, ok]) => el("li", { class: ok ? "done" : "" }, t)));

  const s = status.stats;
  $("#stats").replaceChildren(...[
    ["Found", s.total], ["Scored", s.scored], ["Tailored", s.tailored],
    ["Cover letters", s.with_cover_letter], ["Ready", s.ready_to_apply], ["Applied", s.applied],
  ].map(([l, v]) => el("div", { class: "stat" }, el("b", {}, String(v)), el("span", {}, l))));
}

// ---------- setup: settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  $$("input[name=engine]").forEach((r) => (r.checked = r.value === s.EXECUTION_ENGINE));
  $("#provider").value = s.LLM_PROVIDER || (s.GEMINI_API_KEY.set ? "gemini" : s.OPENAI_API_KEY.set ? "openai"
    : s.ANTHROPIC_API_KEY.set ? "anthropic" : "gemini");
  $("#model").value = s.LLM_MODEL || "";
  $("#tailor-style").value = s.TAILOR_STYLE || "full";
  $("#recruiter-screen").value = s.RECRUITER_SCREEN || "auto";
  $("#look-layout").value = s.RESUME_LAYOUT || "modern";
  $("#look-accent").value = s.RESUME_ACCENT || "navy";
  $("#look-tagline").checked = s.RESUME_TAGLINE === "on";
  $("#look-tech").checked = s.RESUME_TECH_LINE === "on";
  $("#look-rights").value = s.SHOW_WORK_RIGHTS || "auto";
  $("#look-pages").value = s.RESUME_PAGES || "auto";
  refreshLookPreview();
  $("#codex-model").value = s.CODEX_MODEL || "";
  showProvider(); showEngine();
  $("#daily-limit").value = s.GEMINI_DAILY_LIMIT || "";
  $("#apply-reserve").value = s.GEMINI_APPLY_RESERVE || "";
  for (const [id, key] of [["gemini", "GEMINI_API_KEY"], ["openai", "OPENAI_API_KEY"],
                           ["anthropic", "ANTHROPIC_API_KEY"], ["capsolver", "CAPSOLVER_API_KEY"],
                           ["reed", "REED_API_KEY"]]) {
    $("#" + id).value = "";
    $("#" + id).placeholder = s[key].set ? "Saved " + s[key].hint + " (leave blank to keep)" : "Paste your key";
  }
}
$("#save-settings").addEventListener("click", () => {
  const engine = ($("input[name=engine]:checked") || {}).value || "claude_code";
  let confirmAgent = false;
  if ((engine === "claude_code" || engine === "codex") && status && engine !== status.engine) {
    confirmAgent = confirm((engine === "codex" ? "Codex" : "Claude Code") + " drives a Chrome window by itself, " +
      "fills in each application (signing up for employer accounts where needed, with the password in your " +
      "profile), and stops at the final Submit. You check everything in that window and submit it yourself. It " +
      "uses your " + (engine === "codex" ? "ChatGPT plan or OpenAI key" : "Claude subscription") + ".\n\nSwitch to it?");
    if (!confirmAgent) return;
  }
  saveSettings(engine, confirmAgent);
});
const saveSettings = (engine, confirmAgent) => guarded(async () => {
  await post("/api/settings", {
    confirm_agent: confirmAgent,
    EXECUTION_ENGINE: engine,
    LLM_PROVIDER: $("#provider").value, GEMINI_API_KEY: $("#gemini").value, OPENAI_API_KEY: $("#openai").value,
    ANTHROPIC_API_KEY: $("#anthropic").value, CODEX_MODEL: $("#codex-model").value,
    CAPSOLVER_API_KEY: $("#capsolver").value, LLM_MODEL: $("#model").value,
    GEMINI_DAILY_LIMIT: $("#daily-limit").value, GEMINI_APPLY_RESERVE: $("#apply-reserve").value,
  });
  await loadSettings(); await refreshStatus();
}, "Settings saved");

// Provider and engine choices show only the fields that apply
const PROVIDER_INFO = {
  gemini: ["gemini-3.5-flash-lite", "Free daily allowance from Google (about 500 requests a day)."],
  openai: ["gpt-5-mini", "Uses your OpenAI API credit (pay per use; separate from a ChatGPT subscription). " +
           "Roughly 2 requests per tailored job."],
  anthropic: ["claude-opus-5-5", "Uses your Anthropic API credit (pay per use; separate from a Claude subscription). " +
              "Defaults to Claude Opus 5.5; claude-sonnet-5-5 or claude-haiku-5-5 cost less per job."],
  codex_cli: ["Codex's own default", "No API key or bill: uses your ChatGPT plan, so it shares that plan's usage " +
              "limits (a big batch can use a few hours' worth), and each step takes a few seconds longer. When the " +
              "limit is reached, the run stops and the rest waits for next time."],
  claude_cli: ["Claude Code's own default", "No API key or bill: uses your Claude plan, so it shares that plan's " +
               "usage limits (a big batch can use a few hours' worth), and each step takes a few seconds longer. " +
               "When the limit is reached, the run stops and the rest waits for next time."],
};
function showProvider() {
  const p = $("#provider").value;
  $$("[data-provider]").forEach((n) => (n.style.display = n.dataset.provider === p ? "" : "none"));
  $("#model").placeholder = PROVIDER_INFO[p][0] + (p.endsWith("_cli") ? "" : " (default)");
  showSubscriptionCli();
  $("#provider-note").textContent = PROVIDER_INFO[p][1];
}
// Whether the CLI a subscription needs is installed (status.checks.codex_cli / claude_cli)
function showSubscriptionCli() {
  if (!status) return;
  $$(".sub-cli").forEach((n) => {
    const found = status.checks[n.dataset.cli + "_cli"];
    n.textContent = found ? "Found it on this computer. Save, then press Test key."
                          : "Not found yet: install it, then reopen this page.";
    n.style.color = found ? "var(--ok)" : "var(--warn)";
  });
}
function showEngine() {
  const e = ($("input[name=engine]:checked") || {}).value;
  $("#codex-model-row").style.display = e === "codex" ? "" : "none";
  $("#agent-help").style.display = e === "codex" || e === "claude_code" ? "" : "none";
  $("#agent-install").textContent = e === "codex" ? "npm install -g @openai/codex, then codex login"
    : "npm install -g @anthropic-ai/claude-code, then claude (to sign in)";
}
$("#provider").addEventListener("change", () => { $("#model").value = ""; showProvider(); });
$$("input[name=engine]").forEach((r) => r.addEventListener("change", showEngine));

$("#test-ai").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const r = await post("/api/settings/test-ai");
    toast(r.provider_name + (r.provider.endsWith("_cli") ? " works (" : " key works (") + (r.model || "default model") + ")");
  }
  catch (err) { toast(err.message, true); }
  e.target.disabled = false;
});

// ---------- setup: resume ----------
async function loadResume() {
  const r = await api("/api/resume");
  $("#resume-text").value = r.text;
  $("#pdf-state").textContent = r.has_pdf ? "(saved; choose a file to replace)" : "(none yet)";
  $("#docx-state").textContent = r.has_docx ? "(saved; choose a file to replace)" : "(none yet)";
  resumeFiles = r;
  lookNote();
}
$("#save-resume").addEventListener("click", () => guarded(async () => {
  const fd = new FormData();
  fd.append("text", $("#resume-text").value);
  if ($("#resume-pdf").files[0]) fd.append("pdf", $("#resume-pdf").files[0]);
  if ($("#resume-docx").files[0]) fd.append("docx", $("#resume-docx").files[0]);
  await post("/api/resume", fd);
  $("#resume-pdf").value = ""; $("#resume-docx").value = "";
  refreshLookPreview();
  await loadResume(); await refreshStatus();
}, "Resume saved"));

// ---------- setup: searches ----------
async function loadSearches() {
  const s = await api("/api/searches");
  $("#searches-yaml").value = s.yaml;
  if (s.exists && s.simple) {
    $("#s-location").value = s.simple.location;
    $("#s-country").value = s.simple.country;
    $("#s-remote").checked = s.simple.include_remote;
    $("#s-roles").value = s.simple.roles.join(", ");
    $("#s-exclude").value = (s.simple.exclude_titles || []).join(", ");
    $("#s-jobtype").value = s.simple.job_type || "";
    showCountry();
  }
}
function showCountry() {
  const uk = ["uk", "united kingdom"].includes($("#s-country").value);
  $("#uk-search").style.display = uk ? "" : "none";
  $("#s-location").placeholder = uk ? "e.g. London" : "e.g. Sydney, NSW";
}
$("#s-country").addEventListener("change", showCountry);
$("#save-searches").addEventListener("click", () => guarded(async () => {
  if ($("#reed").value.trim()) { await post("/api/settings", { REED_API_KEY: $("#reed").value.trim() }); }
  await post("/api/searches", {
    location: $("#s-location").value, country: $("#s-country").value, include_remote: $("#s-remote").checked,
    roles: $("#s-roles").value.split(","),
    exclude_titles: $("#s-exclude").value.split(","), job_type: $("#s-jobtype").value,
  });
  await loadSearches(); await refreshStatus();
}, "Job search saved"));
$("#save-yaml").addEventListener("click", () => guarded(async () => {
  await post("/api/searches", { yaml: $("#searches-yaml").value }); await refreshStatus();
}, "searches.yaml saved"));

// ---------- setup: profile (form generated from the template's shape) ----------
const REQUIRED = new Set(["personal.full_name", "personal.email", "personal.phone"]);
// What each part of the profile is for, and why it's asked (shown in the form; the Guide says more)
const PROFILE_SECTIONS = {
  personal: "Typed into application forms. Your name, city and state, email, phone and LinkedIn (plus GitHub or " +
    "a portfolio, if you add them) also head every resume. With the browser engine the AI only ever sees placeholders for your name, contact " +
    "details and address; the real values are typed in on this computer.",
  work_authorization: "Australian employers screen on this first. It answers the work-rights questions on forms, " +
    "goes on your resume (e.g. \"Australian Citizen\"), and makes jobs you can't take (citizens only, a " +
    "clearance you don't hold, sponsorship not offered) score low, so you don't waste time on them. If you " +
    "need sponsorship, cover letters say so in one plain sentence.",
  availability: "Answers notice-period, start-date, relocation and driver's licence questions. Full-time / " +
    "contract and relocation also feed the score: e.g. a contract-only role scores low if you don't want " +
    "contracts. A cover letter mentions your notice period or start date only when the ad asks, and says in " +
    "one plain sentence if you'd relocate for a role in another city.",
  compensation: "Base salary in AUD, excluding super. Answers salary questions and marks down ads that pay less " +
    "than your minimum. Never put on your resume or cover letter. See Guide → Salary.",
  experience: "Tells the scorer what level you're aiming at (far too senior or junior scores low). Your current " +
    "job title is the only title a resume headline may use, and the education level goes on the resume's " +
    "education line.",
  skills_boundary: "The most important list for tailoring. A tailored resume or cover letter may only name a " +
    "skill or tool that is in your resume or in this list. Add every one you really use, including ones " +
    "your resume doesn't mention, so tailored resumes can show them in the ad's own words (that's what " +
    "ATS keyword searches look for). Leave out anything you couldn't talk about in an interview.",
  resume_facts: "Checks that keep tailored resumes honest. Employers and the university listed here must " +
    "appear in every tailored resume (a draft that drops one is rejected); projects and real numbers " +
    "(\"cut costs 30%\") can be quoted. Use only what's in your resume; clear the examples if unsure.",
  eeo_voluntary: "Optional diversity questions some forms ask. \"Prefer not to say\" is always fine; these are " +
    "only used to answer those questions.",
};
const PROFILE_FIELDS = {
  "personal.password": "Only for the Claude Code and Codex engines, to sign in to or create employer accounts. " +
    "The browser engine never sends or types it: leave it blank if you use that one.",
  "personal.preferred_name": "Signs your cover letters, and answers \"preferred name\" questions.",
  "personal.address": "Only typed into forms that ask for it; never on your resume.",
  "personal.city": "With the state, goes on your resume and decides which jobs count as local.",
  "work_authorization.work_permit_type": "Shown on your resume, e.g. \"Australian Citizen\" or \"Permanent Resident\".",
  "work_authorization.security_clearance": "Jobs needing a clearance you don't hold score low. " +
    "\"Willing to obtain\" is answered yes only for citizens.",
  "availability.earliest_start_date": "e.g. Immediately, or a date (DD/MM/YYYY).",
  "compensation.salary_expectation": "What you ask for when the ad gives no pay.",
  "compensation.salary_range_min": "Your minimum: the lowest base salary you'd accept. Never answered below this, " +
    "and ads paying less score 5 at most.",
  "compensation.salary_range_max": "The top of the range, when a form asks for one.",
  "compensation.currency_conversion_note": "Optional: how to handle ads in another currency.",
  "experience.years_of_experience_total": "Answers \"years of experience\" and sets the seniority you're scored for.",
  "experience.current_job_title": "Your real current title. Resume headlines use this, never the job's title.",
  "experience.target_role": "The kind of role you want, e.g. Performance Marketing Manager or Senior Data " +
    "Engineer. Guides scoring.",
  "experience.field": "Only if your industry isn't listed (choose Other): your field in a few words, e.g. " +
    "Business analysis, Nursing, Law. Left empty, the AI works it out from your resume.",
  "resume_facts.real_metrics": "Numbers from your resume the writer may use, e.g. 30% cost reduction.",
};
const PROFILE_KEY_SECTIONS = new Set(["skills_boundary"]);

let skillLayouts = {};  // industry -> [[profile key, examples], ...] (applypilot/industries.py)
let templateSkills = new Set();  // the profile template's example skills (now or before), lower case

// The skill boxes for an industry: its own boxes, plus any other saved box that holds skills of yours
// (marked "from another field"), so switching industry never loses what you typed. A box from another
// field holding only the template's example skills (Python, React... before 0.13.1) is dropped: they
// were never yours. No industry: the boxes as saved.
function skillFields(industry, values) {
  const layout = skillLayouts[industry];
  if (!layout) return Object.entries(values).map(([key, value]) => [key, value, ""]);
  const out = layout.map(([key, examples]) => [key, values[key] || [], examples]);
  for (const [key, value] of Object.entries(values)) {
    if (layout.some(([k]) => k === key) || !value.length) continue;
    if (value.every((v) => templateSkills.has(v.toLowerCase()))) continue;
    out.push([key, value, "", true]);
  }
  return out;
}
function skillBoxes(industry, values, options, numbers) {
  return skillFields(industry, values).map(([key, value, examples, other]) => {
    const box = profileField("skills_boundary." + key, value, options, numbers, examples);
    if (other) {
      const label = box.querySelector("label");
      label.append(" ", el("span", { class: "muted" }, "(from another field) "),
        el("a", { href: "#", title: "Remove this box and its skills (when you save the profile)",
                  onclick: (e) => { e.preventDefault(); box.remove(); } }, "remove"));
    }
    return box;
  });
}

// Your industry, at the top of the skills: it sets the boxes, so it's asked for where you'll see that
function industryBlock(form) {
  const pick = form.querySelector('[data-path="experience.industry"]');
  if (!pick) return null;
  const pickRow = pick.closest("div"), fieldRow = form.querySelector('[data-path="experience.field"]')?.closest("div");
  const note = el("p", { class: "industry-note" });
  const block = el("div", { class: "industry-pick" }, el("b", {}, "Your industry"), note, pickRow, fieldRow || "");
  const show = () => {
    const none = !pick.value;
    block.classList.toggle("missing", none);
    note.textContent = none
      ? "Choose your industry first: the skill boxes below change to match it, with examples for your field."
      : "Sets the skill boxes below (with examples for your field), how tailored resumes group your skills, " +
        "and the field the scorer is told. Change it and the boxes change.";
    if (fieldRow) fieldRow.style.display = !pick.value || pick.value === "Other" ? "" : "none";
  };
  show();
  return { block, pick, show };
}

function profileField(path, value, options, numbers, examples = "") {
  const key = path.split(".")[1];
  const isList = Array.isArray(value);
  const type = key.includes("password") ? "password" : key === "email" ? "email" : "text";
  const id = "p-" + path.replace(".", "-");
  const label = el("label", { for: id }, human(key) + (REQUIRED.has(path) ? " *" : "") + (isList ? " (comma separated)" : ""));
  let input;
  if (options[path]) {
    // A fixed set of answers: a drop-down, so the wording is always one the app understands
    const current = String(value ?? "");
    const known = options[path].some((o) => o.value === current);
    input = el("select", { id, "data-path": path },
      ...(known ? [] : [el("option", { value: current, selected: true }, (current || "(empty)") + " (pick one)")]),
      ...options[path].map((o) => el("option", { value: o.value, selected: o.value === current }, o.label)));
  } else {
    input = el("input", { id, type: numbers.includes(path) ? "number" : type, "data-path": path,
                          "data-list": isList ? "1" : false, min: numbers.includes(path) ? "0" : false,
                          value: isList ? value.join(", ") : String(value ?? "") });
  }
  const help = PROFILE_FIELDS[path] || (examples ? "e.g. " + examples : "");
  return el("div", {}, label, input, help ? el("small", { class: "help" }, help) : "");
}

function currentSkills() {
  const out = {};
  for (const input of document.querySelectorAll('#profile-form [data-path^="skills_boundary."]')) {
    out[input.dataset.path.split(".")[1]] = input.value.split(",").map((v) => v.trim()).filter(Boolean);
  }
  return out;
}

async function loadProfile() {
  const { profile, options = {}, numbers = [], skills_confirmed = false, skill_layouts = {},
          template_skills = [] } = await api("/api/profile");
  skillLayouts = skill_layouts;
  templateSkills = new Set(template_skills);
  $("#skills-confirmed").checked = skills_confirmed;
  const form = $("#profile-form");
  form.replaceChildren();
  const industry = (profile.experience || {}).industry || "";
  for (const [section, fields] of Object.entries(profile)) {
    if (typeof fields !== "object" || Array.isArray(fields)) continue;
    const box = el("div", { class: "fields", id: "profile-" + section });
    if (section === "skills_boundary") box.append(...skillBoxes(industry, fields, options, numbers));
    else for (const [key, value] of Object.entries(fields)) box.append(profileField(section + "." + key, value, options, numbers));
    form.append(el("fieldset", { class: PROFILE_KEY_SECTIONS.has(section) ? "key" : "" },
      el("legend", {}, human(section) + (section === "skills_boundary" ? " (improves tailored resumes)" : "")),
      PROFILE_SECTIONS[section] ? el("p", { class: "why" }, PROFILE_SECTIONS[section]) : "", box));
  }
  // Your industry moves to the top of the skills (from Experience); choosing one shows its skill boxes,
  // and skills already typed move with their box or stay in theirs
  const ind = industryBlock(form), skills = document.getElementById("profile-skills_boundary");
  if (ind && skills) {
    skills.before(ind.block);
    ind.pick.addEventListener("change", () => {
      ind.show();
      skills.replaceChildren(...skillBoxes(ind.pick.value, currentSkills(), options, numbers));
    });
  }
}
$("#save-profile").addEventListener("click", () => guarded(async () => {
  const out = {};
  for (const input of $$("#profile-form input, #profile-form select")) {
    const [section, key] = input.dataset.path.split(".");
    out[section] ??= {};
    out[section][key] = input.dataset.list
      ? input.value.split(",").map((v) => v.trim()).filter(Boolean) : input.value.trim();
  }
  out.skills_confirmed = $("#skills-confirmed").checked;
  await post("/api/profile", out); await refreshStatus();
}, "Profile saved"));

// ---------- setup: your writing ----------
async function loadWriting() {
  const w = await api("/api/writing-sample");
  $("#writing-sample").value = w.text;
  $("#writing-state").textContent = w.text ? "Cover letters follow this voice." : "";
}
$("#save-writing").addEventListener("click", () => guarded(async () => {
  await post("/api/writing-sample", { text: $("#writing-sample").value }); await loadWriting();
}, "Saved"));
let resumeFiles = {};
const THUMB_NAMES = { modern: "Modern", classic: "Classic", compact: "Compact", traditional: "Traditional",
                      mine: "Match my resume", word: "My Word file" };
function buildLookThumbs() {
  const box = $("#look-thumbs"), accent = $("#look-accent").value, current = $("#look-layout").value;
  box.replaceChildren(...Object.entries(THUMB_NAMES).map(([value, name]) => {
    const frame = el("iframe", { title: name + " layout", loading: "lazy", tabindex: "-1",
      src: `/api/resume-preview?layout=${value}&accent=${accent}&t=${Date.now()}` });
    frame.addEventListener("load", () => lookMargins(frame));
    const button = el("button", { type: "button", class: "look-thumb", role: "radio",
      "aria-checked": String(value === current), title: "See " + name + " full size",
      onclick: () => showLookFull(value) },
      el("div", { class: "frame" }, frame), el("span", {}, name + (value === current ? " (in use)" : "")));
    return button;
  }));
  scaleLookThumbs();
}
// Every preview is the real page (794px = A4 at 96dpi) scaled to its box, so it looks like the PDF
function scaleLookThumbs() {
  for (const f of document.querySelectorAll(".look-thumb iframe, .look-page iframe, .look-full iframe")) {
    if (f.parentElement.clientWidth) f.style.transform = `scale(${f.parentElement.clientWidth / 794})`;
  }
}
// The page's real length (a two-page resume): its box grows to fit, so nothing is cut off
// The PDF's margins come from its @page rule, which a browser page ignores: apply them (the last rule wins)
function lookMargins(f) {
  try {
    const doc = f.contentDocument;
    let margin = "";
    for (const sheet of doc.styleSheets) for (const rule of sheet.cssRules)
      if (rule.type === CSSRule.PAGE_RULE && rule.style.margin) margin = rule.style.margin;
    if (margin) doc.body.style.padding = margin;
  } catch (e) { /* not loaded yet */ }
}
function fitLookPages(f) {
  lookMargins(f);
  try {
    const h = Math.max(1123, f.contentDocument.documentElement.scrollHeight);
    f.style.height = h + "px"; f.parentElement.style.aspectRatio = `794 / ${h}`;
  } catch (e) { /* not loaded yet */ }
  scaleLookThumbs();
}
// Also when the Setup tab is first shown (a hidden tab has no width to scale to)
if (window.ResizeObserver) {
  const watch = new ResizeObserver(scaleLookThumbs);
  watch.observe($("#look-thumbs")); watch.observe($("#look-page"));
} else window.addEventListener("resize", scaleLookThumbs);
$("#look-preview").addEventListener("load", () => fitLookPages($("#look-preview")));
// Full size: the layout in use (the preview), or a thumbnail's layout with "Use this layout"
function showLookFull(layout) {
  const current = $("#look-layout").value, picked = typeof layout === "string" ? layout : current;
  const src = picked === current && typeof layout !== "string" ? $("#look-preview").src
    : `/api/resume-preview?layout=${picked}&accent=${$("#look-accent").value}&t=${Date.now()}`;
  const frame = el("iframe", { title: "Resume preview, full size", tabindex: "-1", src });
  const close = () => { dialog.close(); dialog.remove(); };
  const use = picked === current ? el("span", { class: "muted" }, "In use")
    : el("button", { class: "btn", type: "button", onclick: () => {
        $("#look-layout").value = picked; $("#look-layout").dispatchEvent(new Event("change")); close();
      } }, "Use this layout");
  const dialog = el("dialog", { class: "look-full" },
    el("div", { class: "bar" }, el("b", {}, THUMB_NAMES[picked] || picked),
      el("div", { class: "row", style: "margin:0;gap:8px;align-items:center" }, use,
        el("button", { class: "btn secondary", type: "button", onclick: close }, "Close"))),
    el("span", { class: "frame" }, frame));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); });  // click outside the page
  dialog.addEventListener("close", () => dialog.remove());
  frame.addEventListener("load", () => fitLookPages(frame));
  document.body.append(dialog); dialog.showModal(); scaleLookThumbs();
}
$("#look-page").addEventListener("click", showLookFull);
$("#look-enlarge").addEventListener("click", showLookFull);
function refreshLookPreview() {
  $("#look-preview").src = "/api/resume-preview?t=" + Date.now(); lookNote(); buildLookThumbs();
}
function lookNote() {
  const layout = $("#look-layout").value, note = $("#look-note");
  const text = layout === "mine" ? (resumeFiles.has_style
      ? "Copies the fonts, sizes, colours, headings, rules, bullets and margins of the resume PDF you uploaded, as one "
        + "column. Close, not identical: a two-column or graphic design comes out as its one-column version."
      : "Upload your resume PDF (Your resume, above) first. Until then this looks like Modern.")
    : layout === "word" ? (resumeFiles.has_docx
      ? "Tailored summaries, skills and bullets are written into a copy of your Word file, so the design stays "
        + "exactly yours, and the .docx is what's uploaded (Seek, LinkedIn and most job sites take it). Titles, "
        + "employers, dates, education and your header stay as you wrote them. The preview shows the PDF copy, "
        + "which follows your PDF's design if you uploaded one."
      : "Upload your resume as a Word file (Your resume, above) first. Until then PDFs are used.")
    : "";
  note.textContent = text; note.style.display = text ? "" : "none";
  $("#look-accent").closest("label").style.display = ["mine", "word"].includes(layout) ? "none" : "";
}
for (const id of ["look-layout", "look-accent", "look-tagline", "look-tech", "look-rights", "look-pages"]) {
  $("#" + id).addEventListener("change", () => guarded(async () => {
    await post("/api/settings", {
      RESUME_LAYOUT: $("#look-layout").value, RESUME_ACCENT: $("#look-accent").value,
      RESUME_TAGLINE: $("#look-tagline").checked ? "on" : "off",
      RESUME_TECH_LINE: $("#look-tech").checked ? "on" : "off", SHOW_WORK_RIGHTS: $("#look-rights").value,
      RESUME_PAGES: $("#look-pages").value });
    refreshLookPreview();
  }, "Resume look saved (applies to new PDFs; Run a step again → PDF restyles the rest)"));
}
// ---------- setup: your stories ----------
let storyQuestions = {};
function storyRow(story = {}) {
  const question = el("select", { class: "story-question", style: "width:100%" },
    ...Object.entries(storyQuestions).map(([k, q]) => el("option", { value: k }, q)));
  question.value = story.question || "proud";
  const row = el("div", { class: "story", style: "border:1px solid var(--border);border-radius:8px;padding:10px;margin:8px 0" },
    el("input", { type: "text", class: "story-role", style: "width:100%",
                  placeholder: "Which job? e.g. Acme Freight (Operations Analyst)", value: story.role || "" }),
    question,
    el("textarea", { class: "story-answer", rows: "4", placeholder: "What happened, what you did, how it turned out. "
                     + "Specific details and real numbers help most." }, story.answer || ""),
    el("button", { class: "btn secondary", onclick: () => row.remove() }, "Remove"));
  return row;
}
async function loadStories() {
  const data = await api("/api/stories");
  storyQuestions = data.questions;
  $("#stories").replaceChildren(...(data.stories.length ? data.stories : [{}]).map(storyRow));
  $("#stories-state").textContent = data.stories.length
    ? `${data.stories.length} saved: letters and form answers can use them.` : "";
}
$("#add-story").addEventListener("click", () => $("#stories").append(storyRow()));
$("#save-stories").addEventListener("click", () => guarded(async () => {
  const stories = [...document.querySelectorAll("#stories .story")].map((r) => ({
    role: r.querySelector(".story-role").value, question: r.querySelector(".story-question").value,
    answer: r.querySelector(".story-answer").value }));
  await post("/api/stories", { stories }); await loadStories();
}, "Stories saved (used by the next cover letters and applications)"));
$("#tailor-style").addEventListener("change", () => guarded(() =>
  post("/api/settings", { TAILOR_STYLE: $("#tailor-style").value }),
  $("#tailor-style").value === "light" ? "Light touch: resumes keep your own words (applies to the next tailoring)"
                                       : "Full rewrite (applies to the next tailoring)"));
$("#recruiter-screen").addEventListener("change", () => guarded(() =>
  post("/api/settings", { RECRUITER_SCREEN: $("#recruiter-screen").value }),
  $("#recruiter-screen").value === "manual" ? "Recruiter screen: only when you press it on a job"
                                            : "Recruiter screen: after tailoring (2 AI requests per new job)"));

// ---------- setup: saved answers ----------
async function loadAnswers() {
  const rows = await api("/api/answers");
  $("#answers").replaceChildren(...rows.map((a) => el("tr", {},
    el("td", {}, a.question), el("td", {}, a.answer),
    el("td", { class: "muted" }, a.source === "user" ? "You" : "AI"),
    el("td", { class: "muted" }, String(a.uses || 0)),
    el("td", {}, el("button", { class: "btn secondary", onclick: () => guarded(async () => {
      await post("/api/answers/delete", { question_key: a.question_key }); loadAnswers(); }, "Answer deleted") },
      "Delete")))));
  $("#answers-empty").textContent = rows.length ? "" :
    "Nothing saved yet. Answers are remembered as you apply.";
}

// ---------- find jobs ----------
const STAGES = ["discover", "enrich", "score", "tailor", "cover", "pdf", "screen"];
const STAGE_HELP = {
  discover: "Search the job boards for new jobs (no AI)",
  enrich: "Get each job's full description and application link",
  score: "Rate each job 1-10 against your resume (1 AI request per 10 jobs)",
  tailor: "Write a tailored resume and cover letter for jobs at or above the minimum score (about 2 requests per job)",
  cover: "Cover letters for jobs tailored before letters came with the resume",
  pdf: "Make the A4 PDFs you upload (no AI)",
  screen: "Rate each new resume as an employer's AI screener might (2 AI requests per job; skipped when " +
          "Recruiter screen is set to only when you press it)",
};
$("#stage-boxes").replaceChildren(...STAGES.map((s) =>
  el("label", { class: "choice", style: "margin:0", title: STAGE_HELP[s] },
    el("input", { type: "checkbox", value: s, checked: true }), s === "screen" ? "Recruiter screen" : human(s))));
$$("#stage-boxes input").forEach((b) => b.addEventListener("change", () => (b.dataset.was = b.checked ? "on" : "off")));
// Choosing jobs yourself: Run doesn't tailor; "Tailor ticked jobs" does, for the ticked ones only
const CHOSEN_STAGES = ["tailor", "cover"];
function showChooseMode() {
  const on = $("#choose-mode").checked;
  for (const box of $$("#stage-boxes input")) {
    if (!CHOSEN_STAGES.includes(box.value)) continue;
    box.disabled = on;
    if (on) box.checked = false; else if (box.dataset.was !== "off") box.checked = true;
    box.parentElement.title = on ? "Off while you choose the jobs to tailor (below)" : STAGE_HELP[box.value];
  }
}
try { $("#choose-mode").checked = localStorage.getItem("ap-choose") !== "0"; } catch {}
$("#choose-mode").addEventListener("change", () => {
  try { localStorage.setItem("ap-choose", $("#choose-mode").checked ? "1" : "0"); } catch {}
  showChooseMode();
});
showChooseMode();

$("#run-pipeline").addEventListener("click", () => guarded(async () => {
  const stages = $$("#stage-boxes input:checked").map((i) => i.value);
  if (!stages.length) throw new Error("Tick at least one step");
  await post("/api/pipeline", {
    stages: stages.length === STAGES.length ? [] : stages,
    min_score: Number($("#min-score").value || 7), validation: $("#validation").value,
  });
  pollPipeline();
}));
$("#stop-pipeline").addEventListener("click", () => guarded(() => post("/api/pipeline/stop")));

let lastLogLen = -1, wasRunning = false, lastTask = null;
async function pollPipeline() {
  let t; try { t = await api("/api/pipeline"); } catch { return; }
  lastTask = t;
  $("#run-pipeline").disabled = t.running;
  $("#stop-pipeline").disabled = !t.running;
  $("#rerun-all").disabled = t.running;
  $("#pipeline-state").replaceChildren(t.running ? el("span", { class: "spinner" }) : "",
    t.running ? "Running: " + t.label : t.label ? (t.returncode === 0 ? "Finished" : "Stopped (exit " + t.returncode + ")") : "");
  const log = $("#pipeline-log");
  if (t.log.length !== lastLogLen) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.textContent = t.log.join("\n") || "Nothing has run yet.";
    if (atBottom) log.scrollTop = log.scrollHeight;
    lastLogLen = t.log.length;
  }
  if (wasRunning && !t.running) { refreshStatus(); loadJobs(); loadChoose(); }
  wasRunning = t.running;
  if (chooseJobs.length) chooseCounts();
}

// ---------- choose jobs to tailor ----------
const picks = new Map();  // url -> ticked, for jobs you ticked or unticked yourself
let chooseJobs = [];
const minScore = () => Number($("#min-score").value || 7);
const ticked = (j) => picks.has(j.url) ? picks.get(j.url) : $("#choose-view").value === "choose" && j.fit_score >= minScore();
function chooseCounts() {
  const n = chooseJobs.filter((j) => !j.redo_requested && ticked(j)).length;
  const skipped = $("#choose-view").value === "skipped";
  const t = $("#tailor-chosen");
  t.textContent = n ? "Tailor ticked jobs (" + n + ")" : "Tailor ticked jobs";
  t.disabled = !n || (lastTask && lastTask.running);
  t.title = n ? "About " + 2 * n + " AI requests" : "";
  $("#skip-chosen").textContent = skipped ? "Put back ticked" : "Skip ticked";
  $("#skip-chosen").disabled = !n;
}
async function loadChoose() {
  const view = $("#choose-view").value;
  try { chooseJobs = await api("/api/jobs?view=" + view); } catch (e) { return toast(e.message, true); }
  $("#choose-jobs").replaceChildren(...chooseJobs.map((j) => {
    const box = el("input", { type: "checkbox", disabled: !!j.redo_requested,
      onchange: () => { picks.set(j.url, box.checked); chooseCounts(); } });
    box.checked = !j.redo_requested && ticked(j);
    const notes = [];
    if (j.redo_requested) notes.push("Being tailored…");
    else if (TAILOR_FAILED[j.tailor_status]) notes.push("Tried before: " + TAILOR_FAILED[j.tailor_status]);
    if (j.own_cover) notes.push("Has your own cover letter");
    return el("tr", {},
      el("td", {}, box),
      el("td", { class: "score" }, j.fit_score ?? "–"),
      el("td", {}, el("a", { href: webLink(j.link), target: "_blank", rel: "noopener" }, j.title || j.link),
        el("div", { class: "muted" }, [j.company, j.site, j.location, j.salary, j.applicants].filter(Boolean).join(" · ")),
        notes.length ? el("div", { class: "muted", title: TAILOR_FAILED[j.tailor_status] ? tailorHelp(j) : "" },
          notes.join(" · ")) : ""),
      el("td", { class: "muted" }, j.score_reason || "",
        j.score_keywords ? el("div", { style: "margin-top:4px" }, el("b", {}, "Matches: "), j.score_keywords) : ""));
  }));
  $("#choose-empty").textContent = chooseJobs.length ? "" : view === "skipped" ? "No skipped jobs."
    : "Nothing to choose from yet. Run the Discover, Enrich and Score steps above first.";
  chooseCounts();
}
$("#choose-view").addEventListener("change", loadChoose);
$("#choose-refresh").addEventListener("click", loadChoose);
$("#min-score").addEventListener("change", loadChoose);
$$("[data-tick]").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  for (const j of chooseJobs)
    picks.set(j.url, a.dataset.tick === "all" || (a.dataset.tick === "min" && j.fit_score >= minScore()));
  loadChoose();
}));
const tickedUrls = () => chooseJobs.filter((j) => !j.redo_requested && ticked(j)).map((j) => j.url);
$("#tailor-chosen").addEventListener("click", () => guarded(async () => {
  const urls = tickedUrls();
  if (!confirm("Write a tailored resume and cover letter for " + urls.length + " job(s)? " +
               "This uses about " + 2 * urls.length + " AI requests.")) return;
  const r = await post("/api/jobs/tailor", { urls, validation: $("#validation").value });
  urls.forEach((u) => picks.delete(u));
  toast("Tailoring " + r.marked + " job(s). Follow along in the log above; they appear on the Apply tab when ready.");
  pollPipeline(); loadChoose();
}));
$("#skip-chosen").addEventListener("click", () => guarded(async () => {
  const urls = tickedUrls(), skip = $("#choose-view").value !== "skipped";
  await post("/api/jobs/skip", { urls, skip });
  urls.forEach((u) => picks.delete(u));
  toast(skip ? "Skipped " + urls.length + " job(s)" : "Put back " + urls.length + " job(s)");
  loadChoose(); refreshStatus();
}));

// ---------- apply ----------
const ACCOUNT_LABEL = { no: "no account", board: "job board login", yes: "account needed", unknown: "not sure" };
const ACCOUNT_HELP = {
  no: "Fill in and submit, no sign-in.",
  board: "Uses your login on that job board, which the app's browser remembers.",
  yes: "Create an account (or sign in) on the employer's site first, once per employer.",
};
// Your own resume / cover letter for a job, instead of the generated one
function ownDocLinks(j, only) {
  const links = [];
  for (const [kind, own, label, generated] of [["resume", j.own_resume, "resume", j.generated_resume],
                                              ["cover", j.own_cover, "cover letter", j.generated_cover]]) {
    if (only && kind !== only) continue;
    if (links.length) links.push(" · ");
    if (own) {
      links.push(el("a", { href: "#", title: generated ? "Remove your file and use the generated " + label + " again"
                                                     : "Remove your file (this job has no generated " + label + ")",
        onclick: (e) => { e.preventDefault(); guarded(async () => {
          await post("/api/jobs/own-document/clear", { url: j.url, kind }); loadJobs(); refreshStatus();
        }, generated ? "Using the generated " + label : "Removed your " + label); } },
        generated ? "use generated " + label : "remove my " + label));
    } else {
      links.push(el("a", { href: "#", title: "Upload your own " + label + " (PDF or Word) for this job. " +
        "Apply then uploads yours instead of the generated one.",
        onclick: (e) => { e.preventDefault(); pickOwnDoc(j.url, kind, label); } }, "use my own " + label));
    }
  }
  return links;
}
function pickOwnDoc(url, kind, label) {
  const input = el("input", { type: "file", accept: ".pdf,.docx" });
  input.addEventListener("change", () => guarded(async () => {
    if (!input.files.length) return;
    const form = new FormData();
    form.append("url", url); form.append("kind", kind); form.append("file", input.files[0]);
    await post("/api/jobs/own-document", form); loadJobs(); refreshStatus();
  }, "Your " + label + " will be used for this job"));
  input.click();
}

function atsBadge(a) {
  if (a.unreadable) return el("span", { class: "badge warn", style: "cursor:help", title:
    "No text could be read from your resume file. It may be a scanned image or a picture saved as PDF: " +
    "applicant tracking systems can't read it either. Export it from Word or Google Docs as a PDF instead." },
    "ATS: unreadable");
  const tone = a.score >= 80 ? "ok" : "warn";
  const lines = ["ATS check: " + a.score + "/100 (keyword match " + a.keyword_match + "%)"];
  if (a.issues && a.issues.length) lines.push("", "To look at:", ...a.issues.map((i) => "- " + i));
  if (a.removed_skills && a.removed_skills.length)
    lines.push("", "Removed from the skills (not in your resume or profile skills): " + a.removed_skills.join(", "));
  if (a.missing_gap && a.missing_gap.length)
    lines.push("", "The ad asks for (not in your resume, left out honestly): " + a.missing_gap.slice(0, 10).join(", "));
  return el("span", { class: "badge " + tone, title: lines.join("\n"), style: "cursor:help" }, "ATS " + a.score);
}

// Recruiter screen (simulated, scoring/screen.py): how an employer's AI screener might rate the resume.
// Run on request (two AI requests); it only reports and never changes the documents.
const SCREEN_VERDICT = { advance: "likely to advance", maybe: "a maybe", reject: "unlikely to advance" };
function screenBadge(j) {
  const s = j.screen;
  if (!s) return el("a", { href: "#", title: "Simulate an employer's AI screener: it rates your base resume and " +
    "this job's resume against the ad (two AI requests). Only a report: it never changes your documents.",
    onclick: (e) => { e.preventDefault(); runScreen(j); } }, "recruiter screen");
  const t = s.tailored, b = s.base;
  const lines = ["Recruiter screen (simulated): how an employer's AI screener might rate this resume, reading " +
    "only the ad and the resume. Real screeners vary: treat it as an estimate.", ""];
  if (b) lines.push("Your base resume: " + b.score + "/10 (" + SCREEN_VERDICT[b.verdict] + ")");
  lines.push((s.own ? "Your own resume for this job: " : "Tailored resume: ") + t.score + "/10 (" + SCREEN_VERDICT[t.verdict] + ")");
  if (t.not_shown.length) lines.push("", "Not shown in the resume:", ...t.not_shown.map((x) => "- " + x),
    "Only add one if it's true: tailoring won't invent it.");
  if (t.noticed.length) lines.push("", "A recruiter would notice:", ...t.noticed.map((x) => "- " + x));
  if (s.stale) lines.push("", "The resume changed since this screen: click to screen it again.");
  else lines.push("", "Click to screen it again (two AI requests).");
  const label = "Screen " + (b ? b.score + " → " : "") + t.score + (s.stale ? " (old)" : "");
  return el("span", { class: "badge " + (t.verdict === "advance" && !s.stale ? "ok" : "warn"), title: lines.join("\n"),
    style: "cursor:pointer", onclick: () => runScreen(j) }, label);
}
async function runScreen(j) {
  if (!confirm("Run the recruiter screen for \"" + (j.title || "this job") + "\"?\nAI use: two requests " +
               "(your base resume and this job's resume). It only reports; your documents don't change.")) return;
  toast("Screening… this takes a few seconds.");
  await guarded(async () => {
    const s = await post("/api/jobs/screen", { url: j.url });
    toast("Recruiter screen: " + (s.base ? s.base.score + " → " : "") + s.tailored.score + "/10");
    loadJobs();
  });
}

// "Reads human": patterns that make documents read as AI-written (scoring/voice.py, no AI)
function humanBadge(h) {
  const lines = ["Reads human: a check for what makes a resume or letter sound AI-written (no AI used)."];
  if (h.own_words != null) lines.push("", h.own_words + "% of the resume's bullets are in your own words.");
  if (h.issues.length) lines.push("", "To look at:", ...h.issues.map((i) => "- " + i),
    "", "Fix: Edit the text, Redo with Tailoring style = light touch, or add Your writing in Setup.");
  else lines.push("", "Nothing stands out.");
  return el("span", { class: "badge " + (h.issues.length ? "warn" : "ok"), title: lines.join("\n"),
    style: "cursor:help" }, h.issues.length ? "Reads human: " + h.issues.length + " to look at" : "Reads human");
}

// Quick edit: change a generated resume or letter yourself; the PDF is made again
function editLinks(j, only) {
  const out = [];
  for (const [kind, label, has] of [["resume", "resume", j.generated_resume && !j.own_resume],
                                    ["cover", "cover letter", j.generated_cover && !j.own_cover]]) {
    if (only && kind !== only) continue;
    if (has) out.push(" · ", el("a", { href: "#", title: "Change the generated " + label + " in your own words; its PDF is made again",
      onclick: (e) => { e.preventDefault(); editDoc(j, kind, label); } }, "edit " + label));
  }
  return out;
}
async function editDoc(j, kind, label) {
  let doc; try { doc = await api("/api/jobs/text?kind=" + kind + "&url=" + encodeURIComponent(j.url)); }
  catch (e) { return toast(e.message, true); }
  const box = el("textarea", { style: "min-height:55vh;width:100%" }); box.value = doc.text;
  const dialog = el("dialog", { style: "width:min(900px,95vw);border:1px solid var(--border);border-radius:12px;" +
                                       "background:var(--panel);color:var(--text)" },
    el("h2", { style: "margin-top:0" }, "Edit " + label + ": " + (j.title || "")),
    el("p", { class: "sub" }, kind === "resume"
      ? "Keep the section headings (SUMMARY, TECHNICAL SKILLS, EXPERIENCE, PROJECTS, EDUCATION) and \"- \" at the start " +
        "of each bullet. Your changes aren't fact-checked: keep them true. The PDF, ATS score and Reads human check " +
        "are made again when you save."
      : "Keep \"Dear ...,\" at the top and \"Kind regards,\" and your name at the end. The PDF is made again when you save."),
    box,
    el("div", { class: "row" },
      el("button", { class: "btn", onclick: () => guarded(async () => {
        await post("/api/jobs/text", { url: j.url, kind, text: box.value });
        dialog.close(); dialog.remove(); loadJobs();
      }, "Saved: the " + label + " PDF was made again") }, "Save and remake PDF"),
      el("button", { class: "btn secondary", onclick: () => { dialog.close(); dialog.remove(); } }, "Cancel")));
  document.body.append(dialog);
  dialog.showModal();
}

const TAILOR_FAILED = {
  failed_judge: "the fact-check rejected the drafts",
  failed_validation: "the drafts broke a resume rule",
  exhausted_retries: "the AI's replies couldn't be read",
  error: "an error stopped it",
};
function tailorHelp(j) {
  const lines = [];
  if (j.tailor_status === "failed_judge")
    lines.push("Every draft was compared with your real resume and something was invented or stretched,",
               "so none was used. Common when the job asks for more than your resume shows.");
  if (j.tailor_issues) lines.push("", "Last reason: " + j.tailor_issues);
  const left = 5 - j.tailor_attempts;
  lines.push("", left > 0 ? "Tried " + j.tailor_attempts + " of 5 times; it's tried again on the next run."
                          : "Tried 5 times; it won't be tried again automatically.",
             "Try again retries it now (with the Strictness set on Find jobs; lenient lets more through).",
             "More ways to get it tailored: Find jobs → What the log means → Getting more jobs tailored.");
  return lines.join("\n");
}

async function loadJobs() {
  const view = $("#job-view").value;
  const query = view === "quick" ? "ready&quick=true" : view;
  let jobs; try { jobs = await api("/api/jobs?view=" + query); } catch (e) { return toast(e.message, true); }
  const total = jobs.length;
  jobs = sortJobs(jobs.filter(kindMatches));
  const busy = applyState && applyState.busy;
  $("#jobs").replaceChildren(...jobs.map((j) => {
    const link = j.link;
    // Documents: one line per file, with what you can do to it beside it; Checks: what's been checked about them
    const docs = [];
    const canChange = !j.applied_at && !j.duplicate_of;
    for (const [kind, has, own, label] of [["resume", j.tailored, j.own_resume, "Resume"],
                                           ["cover", j.has_cover, j.own_cover, "Cover letter"]]) {
      if (!has && !(canChange && j.tailored)) continue;
      const file = has ? el("a", { href: "/api/jobs/file?kind=" + kind + "&url=" + encodeURIComponent(j.url), target: "_blank",
        title: own ? "Your own file, used instead of the generated " + label.toLowerCase() : "" },
        own ? "Your " + label.toLowerCase() : label) : el("span", { class: "muted" }, "No " + label.toLowerCase());
      const acts = canChange ? [...editLinks(j, kind), ...ownDocLinks(j, kind)].filter((x) => typeof x !== "string")
        .map((a) => { a.textContent = a.textContent.replace(/^remove my .*/, "remove mine").replace(/ (resume|cover letter)$/, ""); return a; }) : [];
      docs.push(el("div", { class: "doc-row" }, file, acts.length ? el("span", { class: "own-docs" }, ...acts) : ""));
    }
    const checks = [];
    if (j.ats) checks.push(atsBadge(j.ats));
    if (j.human) checks.push(humanBadge(j.human));
    if (j.tailored && !j.duplicate_of && (j.screen || (!j.applied_at && j.has_description))) checks.push(screenBadge(j));
    const statusText = j.redo_requested ? (j.tailored ? "Rewriting documents…" : "Writing documents…")
      : j.duplicate_of ? "Same role as the " + (j.duplicate_site || "other") + " listing"
      : j.applied_at ? "Applied" : j.apply_status === "manual" ? "Saved for later"
      : j.apply_status === "failed" ? "Failed: " + (j.apply_error || "") : j.apply_status || (j.tailored ?
        (j.tailor_status === "review_failed" && j.generated_resume ? "Ready, but the fact-check flagged it" : "Ready")
      : TAILOR_FAILED[j.tailor_status] ? "No documents: " + TAILOR_FAILED[j.tailor_status] : "Not tailored yet");
    const statusHelp = !j.tailored && TAILOR_FAILED[j.tailor_status] ? tailorHelp(j)
      : j.tailor_status === "review_failed" ? "Fact-check run again found: " + (j.tailor_issues || "") +
        "\nThe documents are kept. Read them, then Redo the job or apply as they are." : "";
    const actions = [];
    if (j.tailored && !j.applied_at && !j.duplicate_of) {
      actions.push(el("button", { class: "btn", disabled: busy, onclick: () => startApply(j.url) }, "Apply"));
    }
    if (j.tailored && !j.applied_at && !j.duplicate_of) {
      actions.push(" ", el("button", { class: "btn secondary", disabled: busy && applyState.job && applyState.job.url === j.url,
        title: "You applied to this one yourself (outside the app's browser): move it to Applied",
        onclick: () => confirm("Mark \"" + (j.title || "this job") + "\" as applied?") && guarded(async () => {
          await post("/api/jobs/mark-applied", { url: j.url }); loadJobs(); refreshStatus();
        }, "Marked as applied") }, "Mark applied"));
    }
    if (j.apply_account === "yes" && !j.applied_at && j.apply_site_url) {
      actions.push(" ", el("button", { class: "btn secondary", title: "Open this employer's site in a normal window " +
        "to create an account or sign in (e.g. with Google) before applying", onclick: () => openLogin(j.apply_site_url) },
        "Log in"));
    }
    if ((j.tailored || j.has_cover) && !j.applied_at) {
      actions.push(" ", el("button", { class: "btn secondary", title: "Write this job's resume and cover letter again",
        onclick: () => redo(j.url) }, "Redo"));
    } else if (TAILOR_FAILED[j.tailor_status] && !j.applied_at && !j.duplicate_of) {
      actions.push(" ", el("button", { class: "btn secondary", title: "Try writing this job's resume and cover letter again now",
        onclick: () => redo(j.url) }, "Try again"));
    }
    const steps = rerunSteps(j);
    if (steps.length) {
      const menu = el("select", { style: "width:auto", title: "Run one step again for this job",
        onchange: () => { const step = menu.value; menu.value = ""; if (step) rerunStep(step, j); } },
        el("option", { value: "" }, "Run again…"), ...steps.map((st) => el("option", { value: st }, RERUN_LABEL[st])));
      actions.push(" ", menu);
    }
    if (["manual", "failed"].includes(j.apply_status) && !j.applied_at) {
      actions.push(" ", el("button", { class: "btn secondary", onclick: () => guarded(async () => {
        await post("/api/jobs/reset", { url: j.url }); loadJobs(); }, "Job reset") }, "Reset"));
    }
    return el("tr", {},
      el("td", { class: "score", title: j.score_reason ? "Why this score: " + j.score_reason : "",
        style: j.score_reason ? "cursor:help" : "" }, j.fit_score ?? "–"),
      el("td", {}, el("a", { href: webLink(link), target: "_blank", rel: "noopener" }, j.title || link),
        el("div", { class: "muted" }, [j.kind !== "Not stated" ? j.kind : "", j.company, j.site, j.location,
          j.salary, j.applicants].filter(Boolean).join(" · ")),
        j.apply_site_name ? el("div", { class: "muted", title: ACCOUNT_HELP[j.apply_account] || "" },
          "Applies on " + j.apply_site_name + " · " + (ACCOUNT_LABEL[j.apply_account] || j.apply_account)) : "",
        j.also_on ? el("div", { class: "muted" }, "Also on: " + j.also_on) : ""),
      el("td", { class: "muted", title: statusHelp, style: statusHelp ? "cursor:help" : "" }, statusText),
      el("td", {}, docs.length ? docs : el("span", { class: "muted" }, "–")),
      el("td", {}, checks.length ? el("div", { class: "checks" }, ...checks) : el("span", { class: "muted" }, "–")),
      el("td", {}, el("div", { class: "job-actions" }, ...actions.filter((a) => a !== " "))));
  }));
  $("#jobs-empty").textContent = jobs.length ? "" :
    total ? "No " + $("#job-kind").selectedOptions[0].textContent.toLowerCase() + " jobs in this view (" + total +
      " of other types). Choose \"Any job type\" to see them."
    : $("#job-view").value === "ready" ? "No tailored jobs yet. Run \"Find jobs\" first."
    : $("#job-view").value === "quick" ? "None found yet. Press \"Check where to apply\" to look up where each job applies."
    : "Nothing here yet.";
}
$("#job-view").addEventListener("change", loadJobs);

// Job type filter and sort (Apply jobs). The type is read from each ad: see look.job_kind.
const KIND_ORDER = ["Permanent", "Temp to perm", "Fixed term", "Contract", "Temp", "Casual", "Not stated"];
const SHORT_KINDS = ["Contract", "Fixed term", "Temp", "Casual", "Temp to perm"];
function kindMatches(j) {
  const want = $("#job-kind").value;
  return !want || (want === "short" ? SHORT_KINDS.includes(j.kind) : j.kind === want);
}
function sortJobs(jobs) {
  const by = $("#job-sort").value, score = (j) => j.fit_score ?? -1;
  const keys = {
    kind: (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || score(b) - score(a),
    newest: (a, b) => String(b.discovered_at || "").localeCompare(String(a.discovered_at || "")),
    applicants: (a, b) => (a.applicants_count ?? Infinity) - (b.applicants_count ?? Infinity) || score(b) - score(a),
  };
  return keys[by] ? [...jobs].sort(keys[by]) : jobs;
}
for (const id of ["#job-kind", "#job-sort"]) {
  const box = $(id);
  try { const saved = localStorage.getItem("apply" + id); if (saved !== null) box.value = saved; } catch (e) {}
  if (box.selectedIndex < 0) box.selectedIndex = 0;
  box.addEventListener("change", () => { try { localStorage.setItem("apply" + id, box.value); } catch (e) {} loadJobs(); });
}

// Searches a job board blocked or failed last time: Discover → retry just those
const BOARD_NAMES = { linkedin: "LinkedIn", indeed: "Indeed", glassdoor: "Glassdoor", zip_recruiter: "ZipRecruiter" };
function showFailedSearches(f) {
  const boards = f.boards.map((b) => BOARD_NAMES[b] || b).join(", ");
  $("#rerun-discover").textContent = "Discover: retry the searches that failed" +
    (f.searches ? " (" + f.searches + ")" : " (none)");
  $("#discover-failed").textContent = f.searches ? f.searches + " search(es) on " + boards +
    " didn't finish last time: pick \"retry the searches that failed\"." : "";
}

// Run one step again (rerun.py): per job from the Apply list, or for all jobs from Find jobs
const RERUN_LABEL = {
  enrich: "Get the ad again", score: "Score again", cover: "Cover letter only",
  review: "Fact-check again", pdf: "Make the PDFs again", ats: "ATS check again",
  screen: "Recruiter screen again",
};
function rerunSteps(j) {
  if (j.applied_at || j.duplicate_of) return [];
  const steps = ["enrich"];
  if (j.has_description) steps.push("score");
  if (j.generated_resume && j.has_description) steps.push("cover");
  if (j.generated_resume) steps.push("review");
  if (j.generated_resume || j.generated_cover) steps.push("pdf");
  if (j.tailored) steps.push("ats");
  if (j.tailored && j.has_description) steps.push("screen");
  return steps;
}
async function rerunStep(step, job) {
  if (step === "discover_all") return guarded(async () => {
    if (!confirm("Search every job board again with all your searches? Jobs already found are skipped; " +
                 "LinkedIn and Indeed use no AI.")) return;
    await post("/api/pipeline", { stages: ["discover"], min_score: Number($("#min-score").value || 7) });
    pollPipeline();
  });
  await guarded(async () => {
    const body = { step, url: job ? job.url : null, min_score: Number($("#min-score").value || 7),
                   validation: $("#validation").value };
    const info = await post("/api/jobs/rerun", { ...body, dry_run: true });
    if (!info.count) return toast(job ? "That step can't run for this job yet."
      : step === "discover" ? "No failed searches: the last search finished on every board." : "No jobs for that step yet.", true);
    const what = job ? "\"" + (job.title || "this job") + "\""
      : step === "discover" ? info.count + " search(es) on " + info.boards.map((b) => BOARD_NAMES[b] || b).join(", ")
      : info.count + " job(s)";
    if (!confirm(info.what + (step === "discover" ? ": " : " for ") + what + "?\nAI use: " + info.cost + "." +
                 (step === "review" ? "\nThe documents are kept either way; a job that fails shows what was found." : "")))
      return;
    await post("/api/jobs/rerun", body);
    toast("Running it again. Follow along on the Find jobs tab.");
    showTab("find"); pollPipeline(); if (step !== "discover") loadJobs();
  });
}
$("#rerun-all").addEventListener("click", () => rerunStep($("#rerun-step").value, null));

async function redo(url) {
  const all = !url;
  if (all && !confirm("Rewrite the resume and cover letter for every job you haven't applied to? " +
                      "This uses about two AI requests per job (writing and fact-checking).")) return;
  await guarded(async () => {
    const r = await post("/api/jobs/redo", { url, validation: $("#validation").value });
    if (!r.marked) return toast("Nothing to rewrite.");
    toast("Rewriting " + (all ? r.marked + " job(s)" : "this job") +
          ". The old documents stay until the new ones are ready. Follow along on the Find jobs tab.");
    showTab("find"); pollPipeline(); loadJobs();
  });
}
$("#redo-all").addEventListener("click", () => redo(null));
$("#locate-jobs").addEventListener("click", () => guarded(async () => {
  await post("/api/jobs/locate", { min_score: Number($("#min-score").value || 7) });
  toast("Checking each job in the browser window. Follow along on the Find jobs tab.");
  showTab("find"); pollPipeline();
}));
$("#refresh-jobs").addEventListener("click", loadJobs);

async function startApply(url) {
  await guarded(async () => {
    const r = await post("/api/apply", { url });
    if (r.mode === "claude_code" || r.mode === "codex") {
      toast((r.mode === "codex" ? "Codex" : "Claude Code") + " is filling it in. When the log says it's ready, check " +
            "the Chrome window, submit it yourself, close the window, then press Mark applied.");
      showTab("find"); pollPipeline();
    } else { pollApply(); }
  });
}
$$("#gate-buttons button").forEach((b) => b.addEventListener("click", () =>
  guarded(async () => { await post("/api/apply/decision", { decision: b.dataset.decision }); pollApply(); })));

function ensureVnc() {
  const wrap = $("#vnc-wrap");
  if (wrap.dataset.ready || !status) return;
  wrap.dataset.ready = "1";
  if (status.vnc_url) {
    wrap.append(el("div", { class: "row" }, el("b", {}, "Live browser"),
      el("a", { href: status.vnc_url, target: "_blank", rel: "noopener", class: "muted" }, "open in a new tab")),
      el("iframe", { class: "vnc", src: status.vnc_url, title: "Live browser" }));
  } else {
    wrap.append(el("p", { class: "muted" }, "When you press Apply, a browser window opens on this computer's desktop."));
  }
}

const RESULT_TEXT = {
  applied: ["Marked as applied", "Nice work. Pick the next job when you're ready."],
  manual: ["Saved for later", "It's parked under \"Saved for later\". Press Reset to try again."],
  "failed:user_declined": ["Won't show this job again", ""],
};
let applyState = null, wasBusy = false;
let uploadsShown = "";
function renderUploads(s) {
  const key = JSON.stringify(s.files || []);
  if (key === uploadsShown) return;  // don't redraw (and lose focus) every poll
  uploadsShown = key;
  const box = $("#upload-files");
  if (!s.files || !s.files.length) return box.replaceChildren();
  const copy = (text) => guarded(() => navigator.clipboard.writeText(text), "Path copied");
  const link = (f) => "/api/jobs/file?kind=" + f.kind + "&url=" + encodeURIComponent(s.job.url);
  box.replaceChildren(
    el("p", { class: "sub", style: "margin:12px 0 4px" },
      "Uploading by hand? In the browser's file picker, open the folder ",
      el("b", {}, s.upload_dir), " (in your home folder). It holds just this job's files:"),
    el("ul", { style: "margin:0" }, ...s.files.map((f) => el("li", {},
      el("b", {}, f.kind === "cover" ? "Cover letter: " : "Resume: "), f.name, " ",
      el("a", { href: link(f), target: "_blank" }, "open"), " · ",
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); copy(f.path); } }, "copy full path")))));
}

async function pollApply() {
  try { applyState = await api("/api/apply"); } catch { return; }
  const s = applyState, job = s.job;
  const jobName = job ? (job.title || "") + (job.site ? " at " + job.site : "") : "";
  let title = "Pick a job below and press Apply", sub = "";
  const forJob = jobName ? ": " + jobName : "";
  if (s.state === "filling") { title = "Filling in the form" + forJob + "…"; sub = "Watch the browser."; }
  else if (s.state === "closing") { title = "Closing the browser…"; sub = jobName; }
  else if (s.state === "waiting") {
    title = "Your turn: review and submit" + forJob;
    sub = "Check the form in the browser and fix anything that's wrong. Submit buttons are outlined in red. " +
          "Click Submit in the browser yourself if it looks right, then tell us what you did.";
  } else if (s.state === "done") {
    const [t, d] = RESULT_TEXT[s.result] || ["Didn't finish", (s.result || "").replace(/^failed:/, "")];
    title = t; sub = d;
  } else if (s.state === "error") { title = "Couldn't start"; sub = s.message; }
  $("#apply-title").replaceChildren(s.busy && s.state !== "waiting" ? el("span", { class: "spinner" }) : "", title);
  $("#apply-sub").textContent = sub;
  $("#apply-panel").classList.toggle("gate", s.state === "waiting");
  $("#gate-buttons").style.display = s.state === "waiting" ? "flex" : "none";
  $("#needs").replaceChildren(...(s.state === "waiting" ? s.needs_attention : []).map((n) => el("li", {}, n)));
  renderUploads(s);
  if (wasBusy && !s.busy) { loadJobs(); refreshStatus(); }
  if (wasBusy !== s.busy) { wasBusy = s.busy; loadJobs(); }
}

// ---------- browser ----------
async function refreshBrowser() {
  let b; try { b = await api("/api/browser"); } catch { return; }
  const names = b.system.map((p) => p.split(/[\\/]/).pop().replace(/\.exe$/i, ""));
  $("#browser-state").textContent = b.system.length
    ? "Found: " + names.join(", ") + ". You're all set."
    : b.playwright_chromium ? "Jobapplier's own browser is installed. You're all set."
    : "No Chrome or Edge found. Install Google Chrome, or press Install browser.";
  $("#install-browser").style.display = (b.system.length || b.playwright_chromium) && !b.install ? "none" : "";
  const inst = b.install, log = $("#install-log");
  log.style.display = inst ? "block" : "none";
  $("#install-browser").disabled = !!(inst && inst.running);
  if (inst) { log.textContent = inst.log.join("\n"); log.scrollTop = log.scrollHeight; }
  $("#login-open").style.display = b.login_open ? "none" : "";
  $("#login-close").style.display = b.login_open ? "" : "none";
  $("#login-state").textContent = b.login_open
    ? "Login window open. Sign in, then close the window (its X) to save the logins." : "";
  return inst && inst.running;
}
async function openLogin(url) {
  await guarded(async () => {
    await post("/api/browser/login", url ? { url } : {});
    await refreshBrowser();
    toast("A normal browser window opened. Sign in there, then close it to save the logins.");
  });
}
$("#login-open").addEventListener("click", () => openLogin());
$("#login-close").addEventListener("click", () => guarded(async () => {
  await post("/api/browser/login/close"); await refreshBrowser();
}, "Login window closed"));
setInterval(() => { if ($("#login-close").style.display !== "none") refreshBrowser(); }, 3000);

$("#install-browser").addEventListener("click", () => guarded(async () => {
  await post("/api/browser/install");
  const t = setInterval(async () => { if (!(await refreshBrowser())) { clearInterval(t); refreshStatus(); } }, 1500);
}, "Downloading the browser…"));
$("#test-browser").addEventListener("click", async (e) => {
  e.target.disabled = true; toast("Opening a test window…");
  await guarded(() => post("/api/browser/test"), "The browser works.");
  e.target.disabled = false;
});
$("#quit").addEventListener("click", () => guarded(async () => {
  if (!confirm("Quit Jobapplier?")) return;
  await post("/api/shutdown");
  document.body.replaceChildren(el("main", {}, el("div", { class: "card" },
    el("h2", {}, "Jobapplier has quit"), el("p", { class: "sub" }, "You can close this tab. Open the app again any time."))));
}));

// ---------- boot ----------
(async () => {
  await refreshStatus();
  refreshBrowser();
  await Promise.all([loadSettings(), loadResume(), loadWriting(), loadStories(), loadSearches(), loadProfile(), loadAnswers()]).catch((e) => toast(e.message, true));
  // First launch (nothing set up, no tab remembered): start with the guide
  let tab = status && !status.checks.profile && !status.checks.resume_txt ? "guide" : "setup";
  try { tab = localStorage.getItem("ap-tab") || tab; } catch {}
  showTab(tab);
  pollPipeline(); pollApply();
  setInterval(pollPipeline, 1500);
  setInterval(pollApply, 1500);
  setInterval(refreshStatus, 6000);
})();
