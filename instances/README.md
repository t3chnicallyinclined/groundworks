# Instances

Each folder maps the general method onto one domain: the eight answers, the evidence model, the gates with reject and accept probes, the roles and lenses, the evaluator, a pipeline walkthrough, how to use it, a synthetic worked example, pitfalls, and which modules are reused.

**All examples in these specs are synthetic.** No client or matter data.

| Instance | Goal in one line | Status | Distinctive module or rule |
|---|---|---|---|
| [appeals](appeals/INSTANCE.md) | Find reversible error in a trial record | **built** (privately, instance 1) | Record status axis; readiness requires preservation and surviving attack |
| [litigation-ediscovery](litigation-ediscovery/INSTANCE.md) | Build the chronology and find the key documents in a production | spec | Privilege wall as a lens; Bates locators |
| [patent-prior-art](patent-prior-art/INSTANCE.md) | Invalidate or defend a patent claim | spec | Element-by-element claim chart gate; priority-date status |
| [due-diligence](due-diligence/INSTANCE.md) | Find deal-breakers in a data room | spec | "Clean" requires coverage proof |
| [systematic-review](systematic-review/INSTANCE.md) | Establish whether an intervention works | spec | GRADE certainty as the framework lens; risk-of-bias gate |
| [drug-safety](drug-safety/INSTANCE.md) | Detect and assess adverse-event signals | spec | Causality categories; no signal from a single report |
| [investigative-journalism](investigative-journalism/INSTANCE.md) | Establish what happened, publishably | spec | Two independent sources; source-protection lens |
| [forensic-accounting](forensic-accounting/INSTANCE.md) | Trace funds and establish misstatement or diversion | spec | Complete-trail gate; arithmetic re-computation evaluator |
| [threat-intelligence](threat-intelligence/INSTANCE.md) | Attribute and characterize an intrusion | spec | Admiralty grading as the ladder; no attribution on one indicator |
| [incident-postmortem](incident-postmortem/INSTANCE.md) | Establish root cause and contributing factors | spec | Reproduction gate; ruled-out causes kept |
| [reverse-engineering](reverse-engineering/INSTANCE.md) | Establish how a binary works | pattern proven (re_kb) | Code or reproduction confirms; outcomes on edges |
| [codebase-experts](codebase-experts/INSTANCE.md) | Keep project experts correct as code changes | spec (roadmap D5) | Staleness module: span hashes demote facts on commit |
| [legislative-history](legislative-history/INSTANCE.md) | Establish what a legislature meant or knew | spec | Official record grade; attribution to speaker and stage |
| [customer-research](customer-research/INSTANCE.md) | Establish why users behave as they do | spec | No generalization from one quote; segment lenses |
| [reproducibility-audit](reproducibility-audit/INSTANCE.md) | Establish whether published results hold | spec | "Replicates" only after a re-run |
| [personal-health-record](personal-health-record/INSTANCE.md) | Understand one person's condition over time | spec | Local-only privacy; dated values for any trend |
| [fiction-lore](fiction-lore/INSTANCE.md) | Build a consistent world from a canon | spec (Book Scholar lineage) | Canon tiers; contradiction as data |
| [cross-field-discovery](cross-field-discovery/INSTANCE.md) | Find where one field's method solves another's problem | spec (roadmap D1-D2) | Discovery module: cards, bridge gate, time-split score |
| [methods-brain](methods-brain/INSTANCE.md) | Learn how to build this system better from outside sources | spec (roadmap C5) | Vendor and aggregator cannot confirm; adoption needs reproduction |

## How to use these

- **To start a new domain:** ask the `scholar-architect` agent to run the `design-instance` skill. It interviews you through the eight questions, picks the closest instance here as a starting point, and writes `instances/<name>/INSTANCE.md` and `instance.yaml`.
- **To pilot any spec:** run one chunk end to end first:
  1. intake
  2. labels
  3. validator (planted-error self-test)
  4. load
  5. one gate with its probes
  6. one lens
  7. one role run

  Scale only after the label-agreement sample.

- **To add a node table or an edge type your domain needs:** put it in your own `instance.yaml` under
  `tables:` / `edges:` (shape and limits: `_template/instance.yaml`, worked example:
  `demo-incident/instance.yaml`). Do not edit engine code for it. Declared and built-in run the same
  code path: schema, gate clauses, probes in both directions, `access:` PERMISSIONS, probe cleanup and
  the generated role skills. Grant the new table or edge to a role in `access:` or nothing but root
  can write it.

- **Every instance owns a gate baseline, and it is per instance.** `instances/<name>/gate_baseline.json`
  pins that instance's own rules and the probe flags they earned; `tests/test_gate_baseline.py` reads
  every `instances/*/gate_baseline.json` there is, so an instance that ships one is checked on every
  run. An instance with no baseline is not checked at all. After any deliberate change to your gates,
  `tables:` or `edges:`, regenerate yours at a terminal:

      python -m xscholar.gates instances/<name>/instance.yaml --apply --url <throwaway db> --write-baseline instances/<name>/gate_baseline.json

  then read the diff (that review is the point of the file) and prove it can still fail: delete one
  clause from a copy of the config and check that `--baseline` exits 1 and names it. Only a human at a
  terminal may write a baseline; no script or CI job may.
