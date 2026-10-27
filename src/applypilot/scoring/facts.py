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
              "technician", "officer", "strategist", "practitioner")

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
    # certifications
    "certified", "certification", "certificate", "pmp", "scrum master", "safe agilist", "itil", "togaf",
    "cissp", "cpeng", "rpeq",
)

_STOP = {"and", "or", "the", "a", "an", "of", "for", "with", "in", "on", "to", "via", "using", "&", "incl",
         "including", "etc", "e", "g", "eg", "ie"}
# Words before a role noun that describe someone else ("your engineers") or
# no title at all ("with engineers")
_NOT_A_TITLE = _STOP | {"your", "our", "their", "other", "fellow", "team", "teams", "senior", "junior",
                        "lead", "principal", "staff", "head", "chief", "as", "is", "am", "be", "were", "was",
                        "my", "his", "her", "these", "those", "many", "several", "multiple", "partner",
                        "partnering", "partnered", "alongside", "between", "across", "mentoring", "mentored",
                        "hiring", "interviewing", "candidate", "candidates"}


def normalize(text: str) -> str:
    """Lower case, accents and punctuation flattened, single spaces."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    plain = plain.replace("&", " and ")
    return " " + " ".join(re.findall(r"[a-z0-9+#./-]+", plain)) + " "


def _stem(word: str) -> str:
    word = word.strip("./-")
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9+#]+(?:[./][a-z0-9+#]+)*", normalize(text)) if t not in _STOP]


class Source:
    """The real facts: the original resume, plus skills the user listed in their profile."""

    def __init__(self, resume_text: str, extra_skills: list[str] | None = None):
        self.raw = resume_text or ""
        self.text = normalize(resume_text)
        self.extra = [normalize(s) for s in extra_skills or [] if str(s).strip()]
        self.words = set(_tokens(resume_text))
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
        """The name appears as an employer: on its own line or after "at", not inside a phrase
        ("Google" in "Google Cloud" isn't an employer)."""
        n = re.escape(" ".join(name.split()))
        for line in self.raw.splitlines():
            line = " ".join(line.split())
            if re.search(rf"(?:^|\bat |[|·,(]\s*){n}(?:\s*(?:$|[|·,()\-–.;:]|\bat\b))", line, re.IGNORECASE):
                return True
        return False

    def covers(self, phrase: str) -> bool:
        """Every meaningful word of the phrase appears somewhere in the real facts."""
        return all(t in self.words for t in _tokens(phrase))


# ── Skills from the profile ──────────────────────────────────────────────

@lru_cache(maxsize=1)
def _template_skills() -> frozenset[str]:
    """The example skills in the profile template; never treated as the user's own."""
    try:
        path = Path(__file__).resolve().parent.parent / "ui" / "profile_template.json"
        boundary = json.loads(path.read_text(encoding="utf-8")).get("skills_boundary", {})
    except (OSError, ValueError):
        return frozenset()
    return frozenset(str(s).strip().lower() for items in boundary.values() if isinstance(items, list)
                     for s in items)


def profile_skills(profile: dict) -> list[str]:
    """Skills listed in the profile, minus any still left from the template's examples."""
    out = []
    for items in (profile.get("skills_boundary") or {}).values():
        if isinstance(items, list):
            out += [str(s).strip() for s in items if str(s).strip()
                    and str(s).strip().lower() not in _template_skills()]
    return out


def template_skills_left(profile: dict) -> list[str]:
    """Example skills from the template still in the profile (the UI warns about these)."""
    found = []
    for items in (profile.get("skills_boundary") or {}).values():
        if isinstance(items, list):
            found += [str(s) for s in items if str(s).strip().lower() in _template_skills()]
    return found


def source_for(resume_text: str, profile: dict) -> Source:
    return Source(resume_text, profile_skills(profile))


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
            if not source.covers(base):
                dropped.append(item)
                continue
            real_parts = [p for p in parts if source.covers(p)]
            dropped += [p for p in parts if p not in real_parts]
            good.append(base.strip() + (f" ({', '.join(real_parts)})" if real_parts else ""))
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
        noun = w.rstrip("s") if w.rstrip("s") in ROLE_NOUNS else None
        if not noun or i == 0 or words[i - 1] in _NOT_A_TITLE:
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


def header_problems(header: str, source: Source) -> list[str]:
    """"Job Title at Employer": both parts must come from the resume."""
    title, sep, employer = (header or "").partition(" at ")
    problems = []
    if title.strip() and not source.mentions(title.strip()):  # a title must match exactly
        problems.append(f"job title '{title.strip()}'")
    if sep and employer.strip():
        # "Woolworths Group (WooliesX)": each name must appear as written
        names = [n.strip() for n in re.split(r"[()]", employer) if n.strip()]
        if not all(source.names_employer(n) for n in names):
            problems.append(f"employer '{employer.strip()}'")
    return problems
