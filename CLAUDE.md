# Rules for sessions in this repo

1. **No client or matter data in this repo.** Not in code, docs, notes, tests, fixtures, commit messages or issue text. If a lesson comes from a private instance, record the lesson in generic form and the measured number, never the material.
2. **Verify before claiming.** A paper, repo, product or feature is described only after reading it (fetched page, cloned repo, official docs). Search summaries and aggregator pages are pointers, not sources. Tag claims CONFIRMED (read in a primary source or measured), INFERRED (reasoned from confirmed facts), or UNKNOWN.
3. **Never claim novelty on a search result alone.** Say what was searched and that absence is not proof.
4. **Gates live in the datastore and are tested both ways.** A probe suite must show the gate rejecting what it should and accepting what it should; a gate that only rejects, or only accepts, is broken.
5. **Checks that pass by default are the main enemy.** Errors raise; dry runs say "NOT EXECUTED"; a batch that returns fewer results than statements is a failure; report the unflattering number next to the flattering one.
6. **The evaluator is human-owned.** Agents may add regression entries; they may not remove, weaken or edit existing entries, gates, evidence ladders or privacy rules.
7. **Licensing.** Adapt only MIT or Apache-2.0 material, with NOTICE and LICENSE stating changes. AGPL, unlicensed, and non-commercial or no-derivatives material is for ideas only.
8. **Record decisions** in `docs/DECISIONS.md` and dated reasoning in `notes/`.
