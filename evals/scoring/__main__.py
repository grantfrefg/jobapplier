"""python -m evals.scoring: run the scoring eval with your saved AI settings, or compare two runs.

    python -m evals.scoring                      # 3 repeats, the app's batch size; saves to evals/scoring/results/
    python -m evals.scoring --repeats 1          # quicker and cheaper
    python -m evals.scoring --candidate backend_engineer
    python -m evals.scoring --dry-run            # check the dataset and count requests, no AI calls
    python -m evals.scoring --compare OLD.json NEW.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from applypilot import config
from evals.scoring import dataset, run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.scoring", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeats", type=int, default=3, help="score every job this many times (default 3)")
    p.add_argument("--batch-size", type=int, default=None,
                   help=f"jobs per AI request (default: the app's, {config.DEFAULTS['score_batch_size']})")
    p.add_argument("--candidate", action="append", help="only this candidate id (repeatable)")
    p.add_argument("--seed", type=int, default=0, help="shuffle seed for the order of jobs in each batch")
    p.add_argument("--name", default="", help="name for the saved files (default: the model)")
    p.add_argument("--out", type=Path, default=run.RESULTS_DIR, help="where to save results")
    p.add_argument("--dry-run", action="store_true", help="check the dataset and show the plan; no AI calls")
    p.add_argument("--compare", nargs=2, type=Path, metavar=("OLD", "NEW"), help="compare two saved runs")
    args = p.parse_args(argv)

    if args.compare:
        print(run.compare_files(*args.compare))
        return 0
    if args.repeats < 1:
        p.error("--repeats must be 1 or more")

    candidates = dataset.load(only=args.candidate)
    jobs = sum(len(c.jobs) for c in candidates)
    batch = max(1, args.batch_size or config.DEFAULTS["score_batch_size"])
    per_repeat = sum(-(-len(c.jobs) // batch) for c in candidates)
    print(f"{len(candidates)} candidate(s), {jobs} jobs, {args.repeats} repeat(s): "
          f"{per_repeat * args.repeats} AI request(s), prompt {run.prompt_version()}")
    unreviewed = [c.id for c in candidates if not c.labels_reviewed]
    if unreviewed:
        print(f"Labels not reviewed yet: {', '.join(unreviewed)} (see evals/scoring/README.md)")
    if args.dry_run:
        return 0

    config.load_env()  # your saved keys and AI choice (~/.applypilot/.env), as the app uses
    if config.get_llm_provider() is None:
        print("No AI is set up: choose one and save its key in the app's Setup tab first.", file=sys.stderr)
        return 1
    result = run.run(candidates, repeats=args.repeats, batch_size=args.batch_size, seed=args.seed)
    path = run.save(result, args.out, args.name)
    print()
    print(run.report(result))
    print(f"Saved {path} and {path.with_suffix('.md').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
