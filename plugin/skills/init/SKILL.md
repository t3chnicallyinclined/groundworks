---
name: init
description: Bring the current folder under Groundworks - survey it, register and intake it on the owner's go, record his answers as relayed (INFERRED), and prove its gate. Run only when the user invokes /groundworks:init.
disable-model-invocation: true
---

# Bring a folder under Groundworks

Work in the main session. Do not start other agents for this. Survey first, change nothing until the
owner says go, and never record the owner's words as CONFIRMED yourself.

1. **Survey, read-only.** Call `init_survey` with the folder's absolute path. It writes nothing.
   - `registered` names a project: call `project_show` for it, say it is already in, and stop unless
     the owner wants to change something.
   - `local_only` is true: say the folder is local-only (the reason is in `why`), that Groundworks does
     not walk it, and stop.
2. **One confirm card**, in one message, plain words, from the survey only:
   - the folder, its git state (branch, remote, uncommitted changes), the stack guess;
   - the gate candidates, each with where it came from (build files, CI, justfile);
   - its own Claude setup: instruction files, agents (and any that took a reserved team name, which
     will not be loaded), skills, how many memory files and sessions (sessions are counted, never read);
   - secret-named files (named only; never read, never ingested);
   - at most three questions: what the project is for and what state it is in; which command proves it
     works (offer the candidates; never run one the owner has not confirmed); where it belongs (shared,
     private: nothing leaves it, or isolated: nothing comes in or leaves).
   Then wait for the owner. A question he skips stays open; never guess an answer.
3. **Register, on the owner's go.** `project_register` with the name, the folder relative to the
   workspace, a one-line summary in the owner's words, and the stack only if he confirmed it. When his
   gate differs from the stack's, give him the `gate = [...]` lines for the registry file.
4. **Intake.** Call `intake` for the project and report its summary lines as they came back: units by
   kind, what was skipped and why (secret-shaped units are skipped and never stored), the project's own
   experts loaded and any reserved names not loaded.
5. **Record what the owner said, as relayed.** One `journal_add` per answer: kind `fact` (or `decision`
   for where it belongs), confidence `INFERRED`, source `owner in this session, <date>, relayed by the
   agent`. Do not pass `owner_statement` and do not use `CONFIRMED` for anything the owner told you: you
   are relaying his words, and only his own signature makes them his. A skipped question is a `status`
   entry, confidence `UNKNOWN`, saying it is open.
6. **Offer the signature.** Tell the owner that a relayed answer stays INFERRED until he signs it, and
   give him the exact command to run himself, in his own shell, one per answer he wants signed:
   `groundworks note <project> fact "<his answer>" --as-owner --confidence confirmed --owner-statement "<his answer>, <date>" --source "owner, <date>"`.
   It reads `GROUNDWORKS_OWNER_PASS` from his environment. Never run it, never ask for the password,
   never put it in a command.
7. **Prove the gate, once, after the owner says go.** Call `gate`. Report the result exactly as it came
   back (which step, which machine); a red gate is reported red. Record it with `journal_add` kind
   `status`, confidence `CONFIRMED`, `receipt` set to the gate's receipt.
8. **Finish** with `project_show` for the project and a `journal_add` kind `status`, confidence
   `INFERRED`: what is registered, what intake read, what the gate proved, which answers wait for the
   owner's signature, and what is still open.
