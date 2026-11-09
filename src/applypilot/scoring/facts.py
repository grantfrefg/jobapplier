"""Fact checks: everything a tailored resume or cover letter claims must be in the real resume.

Prompts ask the AI not to invent things; these checks make sure. They compare
the generated text with the user's original resume (plus the skills they
listed in their profile) and either fix the output in code (skills that
aren't real are dropped, a made-up title is replaced by a real one) or
report errors that send it back for another attempt.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# Words that describe what someone *is* (a job title). A phrase built on one of
# these ("AI engineer", "data scientist") must be a title the candidate holds.
ROLE_NOUNS = ("engineer", "scientist", "developer", "architect", "analyst", "manager", "consultant",
              "director", "specialist", "researcher", "programmer", "administrator", "designer",
              "technician", "officer", "strategist", "practitioner",
              # marketing, sales, finance, health, admin and other fields
              "marketer", "coordinator", "executive", "lead", "accountant", "bookkeeper", "auditor", "controller",
              "nurse", "therapist", "pharmacist", "recruiter", "representative", "advisor", "adviser", "planner",
              "producer", "copywriter", "writer", "editor", "buyer", "assistant", "associate", "supervisor",
              "teacher", "lawyer", "solicitor", "paralegal")
# A role noun followed by one of these is describing something else, not a person: "lead generation",
# "executive team", "buyer personas", "associate degree"
_NOT_A_PERSON_AFTER = {"generation", "gen", "nurturing", "nurture", "scoring", "magnet", "magnets", "time", "times",
                       "list", "lists", "source", "sources", "quality", "volume", "volumes", "conversion",
                       "conversions", "flow", "pipeline", "team", "teams", "summary", "summaries", "leadership",
                       "level", "board", "committee", "sponsor", "sponsors", "stakeholder", "stakeholders",
                       "presentation", "presentations", "reporting", "report", "reports", "persona", "personas",
                       "journey", "journeys", "behaviour", "behavior", "intent", "degree", "briefing", "briefings"}

# Tools, languages and platforms an AI likes to add. Any of these in the
# generated text must also be in the original resume or the profile's skills.
TECH_TERMS = (
    # languages
    "python", "java", "javascript", "typescript", "c#", "c++", "golang", "rust", "ruby", "kotlin", "swift",
    "scala", "matlab", "php", "perl", "julia", "haskell", "elixir", "dart", "bash", "powershell",
    # cloud and infrastructure
    "aws", "amazon web services", "azure", "gcp", "google cloud", "kubernetes", "k8s", "docker", "terraform",
    "ansible", "helm", "openshift", "lambda", "sagemaker", "bedrock", "vertex ai", "databricks", "snowflake",
    "redshift", "bigquery", "synapse", "fabric", "ec2", "s3",
    # data and ml
    "spark", "pyspark", "hadoop", "kafka", "airflow", "dbt", "flink", "beam", "dataflow", "dataproc",
    "pytorch", "tensorflow", "keras", "jax", "scikit-learn", "sklearn", "xgboost", "lightgbm", "catboost",
    "hugging face", "huggingface", "langchain", "llamaindex", "openai", "mlflow", "kubeflow", "ray",
    "tableau", "power bi", "looker", "qlik", "excel", "sas", "spss", "stata", "r studio", "rstudio",
    # databases
    "postgresql", "postgres", "mysql", "mongodb", "redis", "cassandra", "dynamodb", "elasticsearch",
    "oracle", "sql server", "neo4j", "pinecone", "weaviate", "pgvector",
    # web and apps
    "react", "angular", "vue", "svelte", "next.js", "node.js", "nodejs", "express", "django", "flask",
    "fastapi", "spring", "rails", ".net", "graphql", "react native", "flutter",
    # practices and tools that are often padded
    "ci/cd", "jenkins", "github actions", "gitlab", "circleci", "jira", "confluence", "salesforce", "sap",
    "servicenow", "linux", "unix",
    # marketing, content and design
    "hubspot", "marketo", "pardot", "mailchimp", "klaviyo", "braze", "salesforce marketing cloud",
    "google analytics", "ga4", "adobe analytics", "looker studio", "google tag manager", "google ads",
    "search ads 360", "dv360", "meta ads", "facebook ads", "linkedin ads", "tiktok ads", "semrush", "ahrefs",
    "screaming frog", "hootsuite", "sprout social", "hotjar", "optimizely", "wordpress", "shopify",
    "contentful", "canva", "figma", "photoshop", "illustrator", "indesign", "premiere pro", "after effects",
    "adobe creative cloud", "adobe creative suite",
    # finance, sales, HR, health, operations and design tools
    "xero", "myob", "netsuite", "quickbooks", "microsoft dynamics", "dynamics 365", "successfactors", "bamboohr",
    "employment hero", "zoominfo", "linkedin sales navigator", "salesloft", "pipedrive", "zendesk", "intercom",
    "freshdesk", "cerner", "medical director", "asana", "monday.com", "smartsheet", "microsoft project", "primavera",
    "autocad", "revit", "solidworks", "archicad", "etabs",
    # certifications
    "certified", "certification", "certificate", "pmp", "scrum master", "safe agilist", "itil", "togaf",
    "cissp", "cpeng", "rpeq",
)

# Skills an ad asks for in words a recruiter searches on, in fields beyond software. Used for the
# ATS keyword check only (not the fact-check: rewording real work into the ad's words is allowed).
FIELD_SKILLS = (
    # marketing and communications
    "seo", "sem", "ppc", "paid search", "paid social", "social media", "content strategy", "content marketing",
    "copywriting", "email marketing", "marketing automation", "lead generation", "demand generation",
    "conversion rate optimisation", "brand strategy", "brand management", "campaign management", "media buying",
    "influencer marketing", "public relations", "media relations", "market research", "event management",
    "product marketing", "lifecycle marketing", "customer journey",
    # finance and accounting
    "month-end", "reconciliations", "accounts payable", "accounts receivable", "payroll", "bas", "gst",
    "budgeting", "forecasting", "financial modelling", "financial reporting", "management accounts", "ifrs",
    # sales and business development
    "account management", "business development", "prospecting", "negotiation", "pipeline management",
    "solution selling", "tenders",
    # people, operations and health
    "recruitment", "onboarding", "employee relations", "workforce planning", "procurement", "inventory management",
    "patient care", "wound care", "medication administration", "triage", "care planning",
    # any field
    "stakeholder management", "project management", "customer service", "data analysis", "agile", "scrum",
)

_STOP = {"and", "or", "the", "a", "an", "of", "for", "with", "in", "on", "to", "via", "using", "&", "incl",
         "including", "etc", "e", "g", "eg", "ie"}
# Words before a role noun that describe someone else ("your engineers") or
# no title at all ("with engineers")
_NOT_A_TITLE = _STOP | {"your", "our", "their", "other", "fellow", "team", "teams", "senior", "junior",
                        "lead", "principal", "staff", "head", "chief", "as", "is", "am", "be", "were", "was",
                        "my", "his", "her", "these", "those", "many", "several", "multiple", "partner",
                        "partnering", "partnered", "alongside", "between", "across", "mentoring", "mentored",
                        "hiring", "interviewing", "candidate", "candidates",
                        # describing words, not part of a title ("experienced engineer")
                        "experienced", "skilled", "seasoned", "versatile", "passionate", "dedicated", "motivated",
                        "proven", "accomplished", "talented", "capable", "strong", "hardworking", "hard-working",
                        "results-driven", "detail-oriented", "curious", "pragmatic", "collaborative", "reliable",
                        "creative", "analytical", "practical", "thoughtful", "driven", "high-performing"}


# Short and long forms of the same thing: a resume saying one counts for the other
# ("GCP" for "Google Cloud", "machine learning (ML)" for "ML").
ALIASES: list[set[str]] = [
    {"gcp", "google cloud", "google cloud platform"},
    {"aws", "amazon web services"},
    {"azure", "microsoft azure"},
    {"ml", "machine learning"},
    {"ai", "artificial intelligence"},
    {"llm", "llms", "large language model", "large language models"},
    {"nlp", "natural language processing"},
    {"genai", "gen ai", "generative ai"},
    {"k8s", "kubernetes"},
    {"ci/cd", "continuous integration", "continuous delivery"},
    {"etl", "extract transform load"},
    {"bi", "business intelligence"},
    {"api", "apis"},
    {"a/b test", "a/b testing", "a/b tests", "ab testing"},
    {"recsys", "recommender system", "recommender systems", "recommendation system", "recommendation systems"},
    {"postgres", "postgresql"},
    {"node.js", "nodejs"},
    {"react", "react.js", "reactjs"},
    {"vue", "vue.js", "vuejs"},
    {"next.js", "nextjs"},
    {"c#", "csharp"},
    {".net", "dotnet"},
    {"golang", "go lang"},
    {"sql server", "mssql", "microsoft sql server"},
    {"power bi", "powerbi"},
    {"excel", "microsoft excel", "ms excel"},
    {"scikit-learn", "sklearn", "scikit learn"},
    {"bigquery", "big query"},
    {"mlops", "ml ops"},
    {"rag", "retrieval augmented generation", "retrieval-augmented generation"},
    {"restful", "rest api", "rest apis"},
    {"tdd", "test-driven development", "test driven development"},
    {"iac", "infrastructure as code"},
    {"sre", "site reliability engineering"},
    {"oop", "object-oriented programming", "object oriented programming"},
    {"ux", "user experience"},
    {"google analytics", "google analytics 4", "ga4"},
    {"meta ads", "facebook ads", "meta ads manager", "facebook ads manager"},
    {"seo", "search engine optimisation", "search engine optimization"},
    {"sem", "search engine marketing"},
    {"cro", "conversion rate optimisation", "conversion rate optimization"},
    {"pr", "public relations"},
    {"bas", "business activity statement", "business activity statements"},
    {"ap", "accounts payable"},
    {"ar", "accounts receivable"},
    {"netsuite", "oracle netsuite"},
    {"microsoft dynamics", "dynamics 365"},
    {"linkedin sales navigator", "sales navigator"},
    {"microsoft project", "ms project"},
    {"ppc", "pay per click", "pay-per-click"},
    {"crm", "customer relationship management"},
    {"adobe creative cloud", "adobe creative suite"},
    {"qa", "quality assurance"},
    # other engineering disciplines
    {"cad", "computer-aided design", "computer aided design"},
    {"plc", "plcs", "programmable logic controller", "programmable logic controllers"},
    {"scada", "supervisory control and data acquisition"},
    {"bim", "building information modelling", "building information modeling"},
    {"fea", "finite element analysis"},
    {"cfd", "computational fluid dynamics"},
    {"hvac", "heating ventilation and air conditioning"},
    {"whs", "work health and safety"},
    {"ohs", "occupational health and safety"},
    {"rpeq", "registered professional engineer of queensland"},
    {"cpeng", "chartered professional engineer"},
]


def aliases_of(term: str) -> set[str]:
    """Other forms of the same thing ("amazon web services" for "AWS"); empty if none are known."""
    t = normalize(term).strip()
    for group in ALIASES:
        if t in group:
            return group - {t}
    return set()
# Words that describe how a skill is used without adding a new fact:
# "stakeholder management" is real if the resume shows stakeholder work.
GENERIC = {"management", "communication", "engagement", "collaboration", "skills", "skill", "practices",
           "techniques", "methods", "principles", "concepts", "fundamentals", "best", "strong", "advanced",
           "hands-on", "experience", "knowledge", "proficiency", "expertise"}


def normalize(text: str) -> str:
    """Lower case, accents and punctuation flattened, single spaces."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    plain = plain.replace("&", " and ")
    return " " + " ".join(re.findall(r"[a-z0-9+#./-]+", plain)) + " "


def _stem(word: str) -> str:
    word = word.strip("./-")
    word = re.sub(r"(?<=[a-z])i([sz])(e|ed|es|ing|ation|ations)$", r"i\1\2", word).replace("iz", "is")
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9+#]+(?:[./][a-z0-9+#]+)*", normalize(text)) if t not in _STOP]


_SUFFIX_WORDS = r"pty|ltd|limited|inc|llc|plc|corp|corporation|co|company|group|holdings|australia"
_EMPLOYER_SUFFIX = re.compile(rf"(?:\s*,?\s+(?:{_SUFFIX_WORDS})\.?)+$", re.IGNORECASE)
# What can follow an employer's name on a resume line: a date, or where it is
_AFTER_EMPLOYER = (r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\d|present\b|current\b|remote\b|"
                   r"hybrid\b|sydney|melbourne|brisbane|perth|adelaide|canberra|hobart|darwin|gold coast|"
                   r"newcastle|wollongong|geelong|(nsw|vic|qld|wa|sa|tas|act|nt|nz|auckland|wellington)\b|"
                   r"london|manchester|birmingham|edinburgh|glasgow|leeds|bristol|cardiff|belfast|"
                   r"(uk|england|scotland|wales|northern ireland)\b|"
                   r"(new south wales|victoria|queensland|western australia|south australia|tasmania)\b")


class Source:
    """The real facts: the original resume, plus skills the user listed in their profile."""

    def __init__(self, resume_text: str, extra_skills: list[str] | None = None):
        self.raw = resume_text or ""
        self.text = normalize(resume_text)
        for group in ALIASES:  # a short or long form in the resume stands for all of them
            if any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", self.text) for a in group):
                self.text += " " + " ; ".join(sorted(group)) + " "
        self.extra = [normalize(s) for s in extra_skills or [] if str(s).strip()]
        self.words = set(_tokens(self.text))
        for s in extra_skills or []:
            self.words.update(_tokens(str(s)))

    def mentions(self, phrase: str) -> bool:
        """The phrase appears (as whole words) in the resume or the listed skills."""
        p = normalize(phrase).strip()
        if not p:
            return True
        pattern = rf"(?<![a-z0-9]){re.escape(p)}(?:e?s)?(?![a-z0-9])"
        return bool(re.search(pattern, self.text)) or any(re.search(pattern, e) for e in self.extra)

    def names_employer(self, name: str) -> bool:
        """The name appears as an employer, not as the start of a longer name ("Google" in
        "Google Cloud" isn't an employer). It may be followed by a legal suffix ("Pty Ltd"),
        dates, a place or punctuation, as resumes lay it out ("Acme Corp   Jan 2020 - Present")."""
        core = _EMPLOYER_SUFFIX.sub("", " ".join(name.split())).strip(" ,.") or name.strip()
        words = r"\s+".join(re.escape(w) for w in core.replace("&", " and ").split())
        if not words:
            return False
        pattern = re.compile(rf"(?<![\w&]){words}(?![\w&])((?:\s*,?\s+(?:{_SUFFIX_WORDS})\.?)*)(\s*)(\S*)",
                             re.IGNORECASE)
        for match in pattern.finditer(self.raw.replace("&", " and ")):
            gap, after = match.group(2), match.group(3)
            if (not after or not after[0].isalnum() or "\t" in gap or len(gap) >= 3 or after[0].islower()
                    or re.match(_AFTER_EMPLOYER, after, re.IGNORECASE)):
                return True
        return False

    def covers(self, phrase: str) -> bool:
        """Every meaningful word of the phrase appears somewhere in the real facts.

        Generic words ("management", "skills") don't count, but at least one
        word must be a real one: "stakeholder management" needs "stakeholder".
        """
        if self.mentions(phrase):
            return True
        words = _tokens(phrase)
        specific = [t for t in words if t not in GENERIC]
        return bool(specific) and all(t in self.words for t in specific)


# ── Skills from the profile ──────────────────────────────────────────────

# The example skills of the profile template before 0.13.1 (a software engineer's). Profiles started
# from it may still hold them, so they stay examples until the user confirms the list.
_OLD_TEMPLATE_SKILLS = frozenset({"python", "sql", "javascript", "fastapi", "flask", "react", "docker", "aws",
                                  "ci/cd", "postgresql", "mongodb", "git", "linux"})


@lru_cache(maxsize=1)
def _template_skills() -> frozenset[str]:
    """The example skills in the profile template (now or before); never treated as the user's own."""
    try:
        path = Path(__file__).resolve().parent.parent / "ui" / "profile_template.json"
        boundary = json.loads(path.read_text(encoding="utf-8")).get("skills_boundary", {})
    except (OSError, ValueError):
        return _OLD_TEMPLATE_SKILLS
    return _OLD_TEMPLATE_SKILLS | frozenset(str(s).strip().lower() for items in boundary.values()
                                            if isinstance(items, list) for s in items)


def profile_skills(profile: dict) -> list[str]:
    """Skills listed in the profile. Ones that are also the template's examples only count
    once the user has confirmed the list ("My skills list is accurate" in Setup)."""
    confirmed = bool(profile.get("skills_confirmed"))
    out = []
    for items in (profile.get("skills_boundary") or {}).values():
        if isinstance(items, list):
            out += [str(s).strip() for s in items if str(s).strip()
                    and (confirmed or str(s).strip().lower() not in _template_skills())]
    return out


def template_skills_left(profile: dict) -> list[str]:
    """Example skills from the template still in an unconfirmed profile (the UI warns about these)."""
    if profile.get("skills_confirmed"):
        return []
    found = []
    for items in (profile.get("skills_boundary") or {}).values():
        if isinstance(items, list):
            found += [str(s) for s in items if str(s).strip().lower() in _template_skills()]
    return found


def source_for(resume_text: str, profile: dict) -> Source:
    return Source(resume_text, profile_skills(profile))


# Soft skills can describe any role, so they're never "only in the profile"
_SOFT = {"leadership", "communication", "teamwork", "team", "problem", "solving", "mentoring", "coaching",
         "presentation", "negotiation", "time", "organisation", "organization", "attention", "detail",
         "customer", "service", "planning", "critical", "thinking", "adaptability", "creativity", "writing",
         "interpersonal", "analytical", "research", "reporting", "budgeting", "training", "sales", "people",
         "stakeholder", "project", "conflict", "resolution", "decision", "making", "multitasking",
         "prioritisation", "prioritization", "verbal", "written", "relationship", "building", "self",
         "motivated", "work", "ethic", "independent", "agile"}


def profile_only_skills(resume_text: str, profile: dict) -> list[str]:
    """Profile skills the resume never mentions: real, but not tied to any role."""
    resume = Source(resume_text)
    soft = {_stem(w) for w in _SOFT | GENERIC}
    return [s for s in profile_skills(profile)
            if not resume.mentions(s) and not set(_tokens(s)) <= soft]


def skills_in(text: str, skills: list[str]) -> list[str]:
    """Which of these skills the text names (other forms count: "k8s" for Kubernetes)."""
    found = Source(text)
    return [s for s in skills if found.mentions(s)]


# ── Checks ────────────────────────────────────────────────────────────────

def _split_items(value: str) -> list[str]:
    """Split a skills line on commas and semicolons that aren't inside brackets."""
    items, depth, current = [], 0, ""
    for ch in str(value):
        depth += ch in "([" and 1 or 0
        depth -= ch in ")]" and 1 or 0
        if ch in ",;" and depth <= 0:
            items.append(current)
            current = ""
        else:
            current += ch
    items.append(current)
    return [i.strip() for i in items if i.strip()]


def filter_skills(skills: dict, source: Source) -> tuple[dict, list[str]]:
    """Drop every skill the candidate doesn't have. Returns (skills, dropped)."""
    kept: dict = {}
    dropped: list[str] = []
    for category, value in (skills or {}).items():
        good = []
        for item in _split_items(value if isinstance(value, str) else ", ".join(map(str, value or []))):
            base, _, inner = item.partition("(")
            parts = _split_items(inner.rstrip(")")) if inner else []
            real_parts = [p for p in parts if source.covers(p)]
            dropped += [p for p in parts if p not in real_parts]
            if source.covers(base):
                good.append(base.strip() + (f" ({', '.join(real_parts)})" if real_parts else ""))
            else:
                # Keep the real tools in brackets even when the name in front isn't real
                dropped.append(base.strip())
                good += real_parts
        if good:
            kept[category] = ", ".join(good)
    return kept, dropped


def real_title(title: str, source: Source, fallback: str) -> str:
    """The headline title: the AI's choice only if it's a title from the resume."""
    title = (title or "").strip()
    if title and source.mentions(title):
        return title
    return fallback or title


def focus_problems(focus: str, source: Source) -> list[str]:
    """A headline focus ("Production ML & LLM Systems") mustn't be another job title or new tools."""
    problems = role_claims(focus, source)
    problems += [f"'{t}'" for t in new_tools(focus, source)]
    return problems


def role_claims(text: str, source: Source) -> list[str]:
    """Job titles the text gives the candidate that aren't in their resume ("AI engineer")."""
    claims = []
    words = re.findall(r"[a-z0-9+#./-]+", normalize(text))
    for i, w in enumerate(words):
        # "head of growth": a title built the other way round
        if w == "head" and i + 2 < len(words) and words[i + 1] == "of" and words[i + 2] not in _STOP:
            if not source.mentions(f"head of {words[i + 2]}"):
                claims.append(f"head of {words[i + 2]}")
            continue
        noun = w.rstrip("s") if w.rstrip("s") in ROLE_NOUNS else None
        if noun and w != noun:
            continue  # plural: other people ("account executives", "data engineers") or sales "leads"
        if not noun or i == 0 or words[i - 1] in _NOT_A_TITLE:
            continue
        if i + 1 < len(words) and words[i + 1] in _NOT_A_PERSON_AFTER:
            continue
        # the qualifier words just before the noun ("ai engineer", "machine learning engineer")
        j = i
        while j > 0 and i - j < 3 and words[j - 1] not in _NOT_A_TITLE and not words[j - 1].isdigit():
            j -= 1
        phrase = " ".join(words[j:i] + [noun])
        shortest = " ".join([words[i - 1], noun])
        if not source.mentions(shortest):
            claims.append(phrase)
    return sorted(set(claims))


_FIRST_PERSON_TITLE = re.compile(
    r"\b(?:as an?|i am an?|i'm an?|i've been an?|i have been an?|currently an?|working as an?)\s+"
    r"((?:[\w+#/-]+\s+){0,4}?(?:%s)s?)\b" % "|".join(ROLE_NOUNS), re.IGNORECASE)


# Words in an ad's title that grade the job rather than name it ("Senior", "Lead"), and what follows a
# separator ("Business Analyst - Salesforce", "Marketing Coordinator (6 month contract)")
_GRADE = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "graduate", "entry", "level",
          "entry-level", "mid", "mid-level", "experienced", "trainee", "intermediate"}
_NAMED_NOT_CLAIMED = re.compile(r"^\s*(?:roles?|positions?|jobs?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies)|"
                                r"team|teams|function|practice|vacancy)\b")
_SOMEONE_ELSE = {"the", "this", "your", "our", "their", "with", "new", "for", "to", "alongside", "supporting"}
_FIRST_PERSON = r"(?:as an?|i am an?|i'm an?|i've been an?|i have been an?|currently an?|working as an?|" \
                r"my (?:\w+ )?years as an?|experience as an?)"


def ad_title(job: dict | None) -> str:
    """The role the ad names, without grade words or qualifiers: "Senior Business Analyst - Salesforce"
    -> "business analyst". Empty if nothing is left."""
    title = re.split(r"\s[-–|]\s|[(\[,/]", str((job or {}).get("title") or ""))[0]
    words = [w for w in normalize(title).split() if w not in _GRADE and not w.isdigit()]
    return " ".join(words).strip()


def ad_title_claims(text: str, job: dict | None, source: Source, first_person: bool = False) -> list[str]:
    """The ad's own job title given to the candidate when their resume never has it: the usual title
    fabrication in any field ("Experienced product owner with 6 years" for a business analyst).

    Naming the job ("the Product Owner role") or someone else ("worked with the product owner") is fine,
    and so are plurals (other people). With first_person (a cover letter), only "as a ...", "I'm a ..."
    and the like count."""
    title = ad_title(job)
    if not title or source.mentions(title):
        return []
    t = normalize(text)
    pattern = re.escape(title)
    for m in re.finditer(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", t):
        if t[m.end():m.end() + 1] == "s":
            continue  # plural: other people
        if _NAMED_NOT_CLAIMED.match(t[m.end():]):
            continue  # "the product owner role": the job, not the candidate
        before = t[:m.start()].split()
        if first_person:
            if re.search(rf"\b{_FIRST_PERSON}\s+(?:[\w-]+\s+){{0,3}}$", t[:m.start()]):
                return [title]
            continue
        if before and (before[-1] in _SOMEONE_ELSE
                       or before[-1] in ("a", "an") and len(before) > 1 and before[-2] in _SOMEONE_ELSE):
            continue  # "worked with (a) product owner": someone else
        return [title]
    return []


def letter_role_claims(letter: str, source: Source) -> list[str]:
    """In a cover letter, only first-person titles count ("as a ... engineer"), not "your engineers"."""
    claims = []
    for m in _FIRST_PERSON_TITLE.finditer(letter or ""):
        claims += role_claims(m.group(1), source)
    return sorted(set(claims))


def new_tools(text: str, source: Source) -> list[str]:
    """Tools, platforms and certifications in the text that the candidate never mentions."""
    t = normalize(text)
    found = []
    for term in TECH_TERMS:
        pattern = rf"(?<![a-z0-9]){re.escape(normalize(term).strip())}(?![a-z0-9])"
        if re.search(pattern, t) and not source.mentions(term):
            found.append(term)
    # "postgres" inside "postgresql" etc.: keep the longest
    return sorted(f for f in found if not any(f != g and f in g for g in found))


_YEARS = re.compile(r"\d+\s*\+?\s*(?:years?|yrs?)\b|\byears?\b", re.I)


def missing_terms(job: dict | None) -> list[str]:
    """The ad's must-haves the scorer found the resume lacks (scorer.py: missing_must_haves)."""
    raw = str((job or {}).get("missing_must_haves") or "")
    # Years of experience aren't a term a resume or an ATS search uses ("12+ years in business analysis")
    return list(dict.fromkeys(t.strip() for t in raw.split(",")
                              if 1 < len(t.strip()) <= 40 and not _YEARS.search(t)))


def claimed_missing(text: str, missing: list[str], source: Source) -> list[str]:
    """Must-haves the candidate lacks that the text claims anyway: in any field, the usual
    fabrication is the AI copying the ad's tools and skills. A term the resume or profile does
    mention (in any known form) is never flagged: then the scorer was wrong, not the writer."""
    t = normalize(text)
    found = []
    for term in missing:
        forms = {term, *aliases_of(term)}
        if any(source.mentions(f) for f in forms):
            continue
        if any(re.search(rf"(?<![a-z0-9]){re.escape(normalize(f).strip())}(?:e?s)?(?![a-z0-9])", t)
               for f in forms if normalize(f).strip()):
            found.append(term)
    return found


def merge_terms(*groups: list[str]) -> list[str]:
    """Terms from several checks, once each, leaving out any that's part of a longer one
    ("salesforce" when "Salesforce Administrator certification" is there too)."""
    terms = list(dict.fromkeys(t for g in groups for t in g))
    low = [t.lower() for t in terms]
    return [t for t, lt in zip(terms, low) if not any(lt != o and lt in o for o in low)]


def header_problems(header: str, source: Source) -> list[str]:
    """"Job Title at Employer": both parts must come from the resume."""
    title, sep, employer = (header or "").partition(" at ")
    problems = []
    if title.strip() and not source.mentions(title.strip()):  # a title must match exactly
        problems.append(f"job title '{title.strip()}'")
    if sep and employer.strip():
        # "Harbourline Retail Group (HarbourlineX)": each name must appear as written
        names = [n.strip() for n in re.split(r"[()]", employer) if n.strip()]
        if not all(source.names_employer(n) for n in names):
            problems.append(f"employer '{employer.strip()}'")
    return problems
