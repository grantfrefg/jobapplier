"""The scoring eval's labelled cases: candidates (resume + profile) and job ads with a human score.

Each YAML file in cases/ is one candidate with the jobs to score against them. See README.md for
the format and for how to label well.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASES_DIR = Path(__file__).parent / "cases"

# Why a job is a dealbreaker, matching the DEALBREAKERS section of scorer.SCORE_PROMPT
DEALBREAKERS = ("work_rights", "clearance", "location", "seniority", "registration", "work_type")


def dealbreaker_cap(kind: str) -> int:
    """The highest score the prompt allows for this dealbreaker (work type: 4, the rest: 3)."""
    return 4 if kind == "work_type" else 3


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    description: str
    score: int                # the human label, 1-10
    dealbreaker: str | None   # one of DEALBREAKERS, or None
    why: str

    def as_posting(self, candidate_id: str) -> dict:
        """The job as scorer.score_batch expects it (a unique url identifies it in the reply)."""
        return {"url": f"eval://{candidate_id}/{self.id}", "title": self.title, "company": self.company,
                "location": self.location, "full_description": self.description}


@dataclass
class Candidate:
    id: str
    country: str
    resume: str
    profile: dict
    local_areas: list[str]
    labels_reviewed: bool
    jobs: list[Job] = field(default_factory=list)
    path: Path | None = None


class DatasetError(ValueError):
    pass


def _job(raw: dict, where: str) -> Job:
    missing = [k for k in ("id", "title", "description", "label") if not raw.get(k)]
    if missing:
        raise DatasetError(f"{where}: missing {', '.join(missing)}")
    label = raw["label"]
    score = label.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
        raise DatasetError(f"{where}: label score must be a whole number from 1 to 10, got {score!r}")
    kind = label.get("dealbreaker")
    if kind is not None and kind not in DEALBREAKERS:
        raise DatasetError(f"{where}: dealbreaker must be one of {', '.join(DEALBREAKERS)} or null, got {kind!r}")
    if kind and score > dealbreaker_cap(kind):
        raise DatasetError(f"{where}: a {kind} dealbreaker scores {dealbreaker_cap(kind)} or lower, "
                           f"but the label is {score}")
    if not str(label.get("why") or "").strip():
        raise DatasetError(f"{where}: say why in label.why (it's what you check a disagreement against)")
    return Job(id=str(raw["id"]), title=str(raw["title"]), company=str(raw.get("company") or ""),
               location=str(raw.get("location") or ""), description=str(raw["description"]).strip(),
               score=score, dealbreaker=kind, why=str(label["why"]).strip())


def load_candidate(path: Path) -> Candidate:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("id", "resume", "profile", "jobs"):
        if not raw.get(key):
            raise DatasetError(f"{path.name}: missing {key}")
    jobs = [_job(j, f"{path.name} job {i + 1} ({j.get('id', '?')})") for i, j in enumerate(raw["jobs"])]
    ids = [j.id for j in jobs]
    if len(set(ids)) != len(ids):
        raise DatasetError(f"{path.name}: job ids must be unique")
    return Candidate(id=str(raw["id"]), country=str(raw.get("country") or "australia"),
                     resume=str(raw["resume"]).strip(), profile=raw["profile"],
                     local_areas=[str(a) for a in raw.get("local_areas") or []],
                     labels_reviewed=bool(raw.get("labels_reviewed")), jobs=jobs, path=path)


def load(cases_dir: Path = CASES_DIR, only: list[str] | None = None) -> list[Candidate]:
    """Every candidate in cases_dir (or just the ids in `only`), sorted by id."""
    candidates = [load_candidate(p) for p in sorted(cases_dir.glob("*.yaml"))]
    ids = [c.id for c in candidates]
    if len(set(ids)) != len(ids):
        raise DatasetError("candidate ids must be unique")
    if only:
        unknown = set(only) - set(ids)
        if unknown:
            raise DatasetError(f"no such candidate: {', '.join(sorted(unknown))} (have: {', '.join(ids)})")
        candidates = [c for c in candidates if c.id in only]
    return candidates
