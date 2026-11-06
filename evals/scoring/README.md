# Scoring eval

Measures how well the AI's fit scores (1-10) agree with a person's, using the app's real
scoring code (`scorer.score_batch`: the same prompt, batching and reply parsing). Run it before
and after changing the scoring prompt, or to compare AI providers and models. The unit tests
can't tell you this, because they use a fake AI.

## Quick start

From the repository root:

```bash
python -m evals.scoring --dry-run      # check the dataset, count AI requests; no AI calls
python -m evals.scoring                # run it with the AI and key saved in the app's Setup tab
python -m evals.scoring --compare evals/scoring/results/OLD.json evals/scoring/results/NEW.json
```

A run scores 27 jobs 3 times in batches of 10: 9 AI requests. With Gemini they count towards the
day's free-tier budget, like the app's own requests. Results go to `evals/scoring/results/` as JSON
(everything) and Markdown (the report). The dataset is made up, so saved results are fine to commit
as baselines.

Options: `--repeats N`, `--batch-size N`, `--candidate ID` (repeatable), `--seed N`, `--name NAME`.

## Before your first real run: review the labels

The labels in `cases/*.yaml` are a **first draft**. Every number the eval reports is agreement
with these labels, so they need to be yours. For each job, read the ad, decide your own score, and
fix any label you disagree with. Change the `why` too. Then set `labels_reviewed: true` in the
file. Until then the report says the labels are unreviewed.

How to label well:
- **Score as the app should**, using the rules in `scorer.SCORE_PROMPT`, not as you'd like it to.
  If you disagree with a rule, that's a prompt change: change the prompt, then measure it here.
- **Write down why.** When the AI disagrees, you compare its reasoning with yours. Sometimes the
  AI is right and the label should change.
- **Mark dealbreakers** (`work_rights`, `clearance`, `location`, `seniority`, `registration`,
  `work_type`). They're scored separately, because letting one through costs the user the most.
  The loader checks that a dealbreaker label is 3 or lower (4 for `work_type`), as the prompt says.
- **Spread the scores.** Every candidate needs strong matches, near misses and clear no's. A set
  of jobs that are all 7s can't show whether the AI ranks them in the right order.

## Reading the report

| Number | Means | Good |
|---|---|---|
| MAE | Average distance between the AI's score and yours | Under 1.0 |
| within 1 | Share of jobs the AI scored within 1 of you | Over 0.8 |
| rank corr. | Spearman rank correlation: does the AI put jobs in your order? This matters most, because the app tailors the top-scored jobs | Over 0.8 |
| dealbreakers caught | Labelled dealbreakers the AI scored at or under the cap | 1.0 |
| bias | Average signed error: + means the AI scores higher than you | Near 0 |
| unstable | Jobs whose repeats differ by 2 or more. Same input, different score: that's noise you can't fix with prompt wording alone | 0 |

The "Good" values are rough starting points, not standards: two careful people usually disagree
by about 1 point. Under **Biggest disagreements**, the report puts your reason next to the AI's for
the jobs it got most wrong. Read those first.

The AI's score for a job is the median of its repeats, so one odd answer doesn't decide it.
Jobs are shuffled into a new order on each repeat, so a job's position in the batch doesn't skew
the result.

## The workflow

1. Review the labels, then run once to get a **baseline**: `--name baseline`.
2. Change one thing: a prompt rule, the model, or the batch size.
3. Run again and `--compare` the two. Look at the headline numbers, then at the jobs that moved.
4. Keep the change if it helps without letting dealbreakers through.

Change one thing at a time, and use 3 or more repeats: with one repeat, a difference can be just
the AI's randomness.

## Adding cases

Copy a file in `cases/` and change it. Each file is one candidate: `resume`, a `profile` in the
app's `profile.json` format (only the fields the scorer reads), `local_areas` (where they search)
and `jobs`, each with a `label`. Run `--dry-run` to check it, and `pytest tests/test_eval_scoring.py`
(it checks the dataset too). Keep cases made up: never put a real person's resume here.

Good next cases: a UK candidate (`country: uk`), a career changer, a job whose pay is just under
the minimum, and real ads you've seen the app score badly.
