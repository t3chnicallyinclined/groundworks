# Patterns

Designs that came out of running a real instance and that any instance can adopt. **None of them needs an engine change to adopt.** Each one is declared in `instance.yaml`: under `tables:`, `edges:` and `gates:`, and for assertions also `assertions:`. Declared tables and edges get schema, gate traversals, both-way probes, cleanup and per-role permissions from the same code as the built-in tables.

Each pattern gives the problem it solves, the config, what was verified, and its limits.

---

## Contentions: what someone asserts, and what the record answered

### The problem

An evidence-gated graph that holds only evidence **loses the question the evidence was gathered to answer**.

On the first real instance the owner kept saying *"we figured these out already, why did we lose them"*. It was treated as a memory problem for weeks. It was structural. Every argument began as something the owner had said. That statement got worked into a report, the report was superseded, and the statement went with it. The graph held facts, arguments and dead ends, but **nothing held "what this person asserts, and what the record answered"**.

The result was the same ground dug over and over, because nothing recorded that it had already been dug.

### The config

```yaml
tables:
  contention:
    fields:
      said:        string        # their own words, verbatim and short, so the row is provably theirs
      asserts:     string        # the same point restated in the record's terms
      disposition: {type: string, one_of: [verified, partly, open, not_in_record, ruled_out]}
      why:         {type: string, optional: true}   # what the record actually showed
      use_for:     {type: string, optional: true}   # what this row may be used to support
      never_for:   {type: string, optional: true}   # what it must never be used to support

edges:
  rests_on:   {from: contention, to: fact}         # the evidence behind a disposition
  raised_for: {from: contention, to: conclusion}   # the argument it was raised for

gates:
  - id: contention_verified_needs_a_confirmed_fact
    table: contention
    field: disposition
    value: verified
    requires: [{edge: rests_on, to: [fact], where: "status = 'confirmed'", min: 1}]
```

Grant `contention`, `rests_on` and `raised_for` to the roles that may write them in `access:`.

### Why each field is there

- **`said` is verbatim and short.** A paraphrase is ours, not theirs. Keeping the words short keeps them checkable.
- **`not_in_record` is a first-class result, not a failure.** A row that says `not_in_record` and records in `why` what was searched **stops the same ground being dug again**. It needs no evidence, which is correct: absence is the finding.
- **`not_in_record` and `ruled_out` are different findings.** One says the record is silent. The other says the record answers, and answers against. Merging them loses exactly the distinction this table exists to make.
- **`use_for` / `never_for` are what make a `partly` row usable.** This is measured, see below: `partly` is where most rows land. A partly-supported assertion is a hazard unless it says what it may and may not be relied on for.

### What was verified

On a throwaway SurrealDB 3.1.6, with the demo instance's vocabulary:

| Case | Result |
|---|---|
| Rests on a **proposed** fact → `verified` | **refused** |
| Rests on a **confirmed** fact → `verified` | accepted |
| A disposition outside the declared list | **refused** |
| `not_in_record` with a `why` and no evidence | written, correctly |
| Generated probes for the rule (`gates --apply`) | **4 refused, 4 accepted, 0 misbehaved**, and **red** when the gate is loosened |

The generated probe is only possible because of V2-18 (staged evidence, `docs/ROADMAP.md`). The rule's evidence, a *confirmed* fact, is itself behind a gate. Before that fix no probe could create it, and the rule was guarded by the committed baseline and hand-written probes alone.

### What it measured, the first time it was run

A harvest over **423 messages** from one person produced 174 raw contentions, merged to 124 canonical ones. Of the 106 written before the run was cut short:

| Disposition | Count |
|---|---|
| verified | 21 |
| **partly** | **70** |
| open | 6 |
| not_in_record | 4 |
| ruled_out | 5 |

About one in five of what the person insisted on was fully borne out. **Two thirds were borne out in part.** Fewer than one in ten were absent from the record or contradicted by it. `partly` does most of the work, which is why `use_for` / `never_for` belong in the pattern and not only in that instance.

**Caveat on those numbers.** At least one `ruled_out` row rested partly on an item being "not shown received" in evidence. That turned out to be wrong for thousands of items, because a rebuild had silently reverted a correction -- a defect this engine had too, see `docs/ROADMAP.md`. The `ruled_out` count is likely a slight overstatement. Treat it as measured on a graph that was then found to carry a defect.

### Limits

- A contention's `said` is only as good as whoever extracted it. The table records a claim; it does not validate the extraction. A harvest benefits from the same label-agreement measurement as any other labelling (`groundworks labels agreement`).
- `partly` covers a wide range. Consider whether your instance needs it split.

---

## Assertions: a promoted row says what another row's field holds, and the graph checks it

### The problem

**A graph can contradict itself and say nothing.**

From beta testing on a real instance, in neutral words. An item's status was an **unusable** value. A **promoted** fact in the same graph said, in its text, that the item **was usable**. A second, **ruled-out** fact said the same. The person the graph served insisted for weeks that the item was usable, and was right. Nothing detected the contradiction.

`groundworks/consistency.py` already catches one kind: a promoted row whose own gate no longer holds over its evidence. It could not catch this one. Comparing a status with what a fact's **text** says means reading free text, and free text means domain words in engine code.

The fix is to **make the claim structured**. A fact can carry an **assertion**: "row R's field F holds value V". Comparing that with R.F is mechanical and needs no domain word.

### The config

```yaml
edges:
  asserts_state: {from: fact, to: unit}   # an ordinary declared edge; it declares no fields of its own

assertions:                               # human-owned when present: list it in human_owned
  asserts_state:
    fields: [status]                      # the target fields this edge may assert
    promoted_by: [confirmed_needs_source_grade]   # optional; default: every gate rule on `from`
    enforce: false                        # optional; default false (report only)
```

The asserting row writes one edge per claim: `RELATE fact:f->asserts_state->unit:u SET field = 'status', value = <a status value>`. Apply `assertions.surql(inst)` after the schema and gates.

**What is refused at config load, by name:**

| Declaration | Refused because |
|---|---|
| A field the target table does not have | the assertion could never be compared |
| A field with no enumerated values (not in the vocabulary, no `one_of`, not bool) | no asserted value could be checked as legal |
| An asserting table with no gate rule | none of its rows could ever count |
| `promoted_by` naming a rule that is not on the asserting table | it could never be met |
| A built-in edge (`cites` and so on) | it already means something else |
| An edge that declares `field`, `value`, their histories or `learned_at` | the engine owns them on an assertion edge |
| `assertions:` present but not in `human_owned` | which rows count, and enforcement, are the owner's bar |

**What the datastore refuses on every write**, root included: a `field` not in the list, and a `value` the target field cannot hold. `assertions.check(inst, edge, field, value)` makes the same check in Python and refuses by name.

### Which rows count

**Only rows standing at the gated value of a gate rule on their own table.** Nothing names a status. A row that has not passed a gate is a proposal, and its assertion is not a contradiction. A ruled-out row is not counted either: it may be the opposite signal, or it may be right, and only a human can tell which.

### What the check reports

`assertions.report(db, inst)`, also under the `assertions` key of `consistency.report` when the instance declares any:

- **counts:** `assertion_rows` (all), `counted` (promoted), `agree`, so "0 contradictions" over nothing counted reads as what it is;
- **each contradiction:** the asserting row, the target row, the field, the asserted value, the current value;
- **`changed_last`**, from the append-only history: `target` (the target moved after the assertion was made and promoted), `assertion` (it was made or promoted over the value the target already held), `same instant`, or `unknown`;
- the **last change on each side**: before, after, when, by whom;
- **`disagreeing_assertions`:** targets where promoted rows assert different values.

**It never says which side is right.** `changed_last: target` can mean the assertion is stale, or that the target was wrongly changed. A human decides.

History comes from `temporal.py` where it already tracks the field. For any other asserted or promoting field, and for the edge's own `field` and `value`, `assertions.surql` adds history of the same shape. Tracked fields get the identical statements, so the two can be applied in either order. **A side with no history is reported `unknown`**, never guessed.

**After a load**, `assertions.after_load(db, inst, counts)` returns:
- `caused_by_this_load`: contradictions on a (row, field) the load followed or accepted;
- `standing`: every contradiction after the load;
- `conflicts`: each held-value conflict, with the promoted assertions on that field and whether each agrees with the **held** value, the **source** value, or neither. That is evidence for the human deciding `accept_source`, not a decision.

**After a correction**, use `assertions.for_target(db, inst, "unit:u1", "status")`.

### Enforcement (opt-in)

`enforce: true` adds three datastore events. Each refuses only the write that would **create** a contradiction:

| Write | Refused when |
|---|---|
| Changing an asserted field on the target | a promoted assertion on that field disagrees with the new value |
| Promoting the asserting row | one of its assertions disagrees with its target |
| Creating or re-stating an assertion on a promoted row | it disagrees with the target |

Each refusal starts with `assertions.MARKER` and names the edge and the asserting row. A contradiction already standing when enforcement is switched on blocks **no** unrelated write, and the report still lists it. Setting `enforce: false` and re-applying removes the events.

**The ordering trap and how it is broken.** A stale promoted assertion blocks a legitimate correction to its target. Re-stating the assertion first is blocked too, because it would then disagree with the uncorrected target. **Moving the asserting row off its promoted value is never refused.** So the way through is:

1. demote the asserting row;
2. correct the target;
3. re-state or retract the assertion;
4. promote again, through its gate and through this check.

Every step lands in history. A reload that would contradict a promoted assertion stops with the refusal, and it has to be resolved the same way.

### What was verified

On a throwaway SurrealDB 3.1.6 with the demo vocabulary plus an assertion edge built in the test (`tests/test_assertions.py`):

| Case | Result |
|---|---|
| Unit at an unusable value, promoted fact asserts a usable one | **reported**, `changed_last: assertion` |
| The unit is corrected to the asserted value | **clean** (counted 1, agree 1) |
| The unit is changed again | **reported**, `changed_last: target`, history names who moved it |
| The same assertion from a proposed fact and from a ruled-out fact | not counted (2 rows, 0 counted) |
| Two promoted facts asserting different values | listed in `disagreeing_assertions` |
| Illegal value or field on a raw write | **refused** by the datastore |
| A reload changes an asserted unit | in `caused_by_this_load`; the conflict row says which side the assertion agrees with |
| Declared tables on both ends, a declared gate, no built-in field | same results, with history |
| No history applied | `changed_last: unknown`, never guessed |
| Enforcement: contradicting target change, promotion, assertion | **refused**; the deadlock resolves through demotion |

Every test was first made to fail against a named deliberate break of the code.

### Limits

- **It checks only what is written as structure.** A fact whose text makes a claim with no assertion edge is as invisible as before. Existing graphs need their claims turned into edges, by a human or by the judge below.
- **Enumerated fields only**: vocabulary values, `one_of` values and booleans. Free text, numbers without `one_of`, and dates are refused at load.
- **Equality only.** "F is one of {a, b}" or "F is not c" cannot be asserted yet.
- **A counted row whose own gate no longer holds** still counts here. `consistency.report` lists it separately; read the two together.
- **Histories are only as honest as `temporal.py`'s.** `learned_at` has a default, not a computed value, so a caller who sets it can move a side's time. History entries are append-only.
- **Enforcement assumes some value takes the asserting row off its gated value.** A declared table where every value is gated would have no way through except deleting the edge, which under `access:` only root may do.

### Unbuilt extension: a judge for free text

> **NOT BUILT.** This is a design. No code, config key or test exists for it.

**The case it covers.** An instance that will not or cannot write assertions still has only free-text facts. A **judge** reads a fact's text and proposes structured assertions.

**Interface.**
- The instance names a callable in config: `assertions: {<edge>: {judge: "module:function"}}`.
- Signature: `judge(row: dict, targets: list[dict], contract: dict) -> list[{"target": id, "field": f, "value": v, "quote": str, "reason": str}]`.
  - `row` is the asserting row.
  - `targets` are the candidate rows it may be about, supplied by the engine through the instance's existing edges (for example, the units the fact cites).
  - `contract` is the edge's `fields` and each field's domain.
- The engine, not the judge, then:
  - refuses by name any proposal that fails `assertions.check`;
  - checks `quote` verbatim against the row's text, the same verbatim rule as labels, and drops proposals that fail;
  - writes the rest to a **separate proposal table**, never to the assertion edge.

**What a judge's output may never do.**
- A proposal is **agent-grade**. It is not an assertion edge, so it is **never counted**, never enforced, and never satisfies a gate.
- It becomes an assertion only when a human, or a credential independent of the judge under `independent:` promotion, writes the edge.
- The report may list proposals that disagree with their target in a separate `proposed_contradictions` section. That list is **a lead for a human, not a finding**, and never feeds a count that a gate or health line reads.

**How it would be measured before adoption.**
- Plant known assertions in fact texts, with matching and contradicting target values, and measure the judge's recall and precision.
- Add a planted misquote, which must be dropped.
- It would be adopted only with that measurement in our own pipeline, per `docs/METHOD.md`.
