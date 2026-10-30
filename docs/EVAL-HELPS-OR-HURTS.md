# EVAL-HELPS-OR-HURTS.md: does Groundworks help or hurt, measured, not argued

Written 2026-10-30 (W2 of the first-ingest-and-KPIs design, amendment A1.4). **Not run.** Running it is the
owner's go. The rules are in the pre-registration `evals/prereg/HH-1-helps-or-hurts.json`, fixed before any
session exists; this page says why the protocol is shaped as it is.

## Two kinds of evidence, kept apart

- **The trend** is `groundworks cost` (docs/METRICS.md, `cost`): per registered project, tokens per session
  and per human turn before Groundworks was first seen there and after. It is backfilled from Claude Code's
  transcripts, so it is free and covers all work, but it is **confounded**: the model mix changed over the
  same weeks (Opus 5 to Opus 5.5), the tasks changed, and the owner's habits changed. It is printed with
  those confounders and never called proof.
- **The proof** is a paired comparison: the same questions, Groundworks on and off, graded blind, with the
  pass rule written first. That is this protocol.

## The protocol, in short

1. **Task set.** A fresh set of 47 questions drawn with `groundworks eval-draw` from real asks of the last 30
   days. The owner approves or rewrites each line and sets its category (at least 6 false-premise traps, at
   least 6 two-source questions); `groundworks eval-check` proves every gold quote is in a tracked file and
   every trap's premise is absent. The set's sha256 goes into the prereg before the first session. The
   ask-v1/ask-v2 sets are not used: ask-v2 was used five times as a test set and is worn.
2. **Arms.** `groundworks eval-run --arms plain,groundworks` unchanged: each question in a fresh headless
   session started in an empty folder (no project memory, no CLAUDE.md), the plugin off, read tools only;
   the groundworks arm adds the Groundworks MCP read tools and its output style. Recording is off, so no
   answer leaves a fact a later question could find. Same model, effort and budget cap in both arms.
3. **Blind grading.** A third session with no tools grades each answer against the gold without knowing
   the arm (`correct | partial | wrong | abstained`, plus `confidently_wrong`). The owner re-grades 10 pairs
   blind and the agreement is reported.
4. **Pass rule, fixed first** (r2 rules: Wilson intervals, paired sign test, noise floor from repeated runs):
   - *helps*: at least 4 more correct than plain (the measured noise floor is +-4 on 47), no more confidently
     wrong, and not more tokens by the sign test;
   - *costs less*: fewer tokens on at least 32 of 47 pairs (one-sided sign test, p = 0.009) with correct
     within the floor and no more confidently wrong;
   - *hurts*: 4 or more fewer correct, or more confidently wrong, or more tokens on 32+ of 47 pairs at equal
     correctness;
   - anything else is **no evidence either way**, reported as such.
5. **Tokens wasted** (counted, not estimated): tokens Claude Code reports for a question an arm got wrong,
   plus tokens the groundworks arm spent above plain on pairs where both arms got the same verdict (help that
   changed nothing). Each is reported with its count, next to the tokens saved.

## What it does not cover

Answering questions about the owner's projects is a slice of the work. Coding sessions, builds and releases
are seen only by the meter's trend. A later protocol for paired coding tasks needs a task set with its own
pass check (a gate that can go red), which is not built here.
