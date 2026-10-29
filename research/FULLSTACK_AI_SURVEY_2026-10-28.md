# Is anyone building a "fullstack-ai" framework? A verified survey (2026-10-28)

**Question.** Groundworks could grow into a builder: one command scaffolds, builds and deploys a complete
service (auth, observability, backups, TLS) from versioned blueprints onto the owner's own estate, run by
the role team (architect, engineer, security, SRE) with gates that prove the deploy on a real environment,
remembered by the store, and kept current by an upgrade path for generated projects. Before building any
of it: who already does which part, and what should be adopted rather than rebuilt?

**Method.** Four research agents, one per category, on 2026-10-28. Every feature claim was read on the
product's official documentation or the repository's README, fetched that day; search-engine snippets
and blog roundups were pointers only. Star counts and last-push dates are from the GitHub REST API the
same day. Tags: CONFIRMED (read on the fetched page), INFERRED, UNKNOWN. General web search engines were
mostly unavailable to the agents (bot challenges, rate limits), so coverage came from the GitHub API,
vendor documentation indexes and direct fetches. **Absence below is "searched, not found", never proof.**

Each item was scored 0/1/? against four layers:

1. **Blueprint**: a self-hostable reference application with auth, observability and backups built in.
2. **Team and gate**: role agents whose definition of done is a deploy proven healthy on a real environment.
3. **Measured memory**: knowledge kept across builds whose benefit is measured, not asserted.
4. **Upgrade as diff**: an already-generated project receives later blueprint changes.

## Result in one table

No surveyed product scored 1 on more than one layer, and none scored 1 on layer 2. The parts that exist:

| layer | best existing piece | license | verdict |
|---|---|---|---|
| 1 | Coolify (one-click Keycloak, TLS, backups, health checks; not composed into an app blueprint; Docker, no Kubernetes); Kubero (Kubernetes, MicroK8s install page, OAuth2 SSO, metrics) | Apache-2.0; GPL-3.0 | parts exist, nobody composes them; GitHub search for a SaaS starter with Keycloak plus Prometheus and Grafana returned 0 starred repositories |
| 2 | Garden (Kubernetes Deploy action waits for resources "to become healthy", Test actions run after); OpenHands Software Factory (triage, developer, reviewer, watchdog roles gated at PR merge, not deploy) | MPL-2.0; MIT | no product has both roles and a live gate |
| 3 | ChatDev Experiential Co-Learning (ACL 2024): the one numbered result for build-type memory; Mem0, Zep/Graphiti, Letta, Devin, Replit, Copilot persist memory but measure conversation recall or nothing | Apache-2.0 | Groundworks' own ask evals already exceed the field's rigor here |
| 4 | Copier (`copier update` regenerates from the recorded template version, diffs, replays local edits with conflict markers, runs migrations); Cruft does the same over Cookiecutter; Projen regenerates managed files; Nx runs versioned codemods | MIT; MIT; Apache-2.0; MIT | solved; adopt Copier |

## Category A: hosted AI app builders

All deploy to their own cloud and none is self-hostable as a platform. CONFIRMED on each vendor's docs:

- **Lovable** (docs.lovable.dev): "The Lovable platform itself ... cannot be self-hosted"; Publish goes to
  Lovable hosting only; generated app is TanStack Start on a Supabase-based backend; auth offers OIDC
  "such as ... Keycloak"; daily DB snapshots about 14 days; two read-only subagents, no role team; a
  one-time agent-driven rewrite Vite to TanStack Start, not a blueprint diff.
- **Bolt.new** (support.bolt.new, github.com/stackblitz/bolt.new, MIT core): WebContainers, Node only;
  Bolt Cloud or Netlify hosting; Supabase-based database and auth.
- **v0** (v0.app/docs): deploys to Vercel; Next.js; DB integrations Upstash, Neon, Supabase, Blob; memory
  mentioned only as a workspace setting.
- **Replit Agent** (docs.replit.com): hosted on GCP; enterprise BYOC in preview; Image Exports give a Docker
  image, Terraform and an SBOM but "You supply pieces like authentication"; parallel worker chats, no roles;
  Memories unmeasured.
- **Devin** (docs.devin.ai): Outposts run tool execution on your machines while planning stays in Devin's
  cloud; "Deploying is not how Devin ships code to your infrastructure"; Markdown memory with daily
  consolidation, measurement "not mentioned".
- **OpenHands** (docs.openhands.dev, MIT): self-hostable; Software Factory use case with triage, developer,
  reviewer, watchdog automations; watchdog merges on passing acceptance statuses and Actions, a merge gate
  not a deploy gate; two-tier Markdown memory, "no benchmarks or quality metrics".
- **Base44, Create.xyz (now Anything), Emergent, Softgen, Atoms (ex-MGX)**: hosted; single agent or named
  roles without described gates; no self-host; no upgrade path.
- Dead or renamed since mid-2026: Databutton (redirects to riff.ai), GitHub Spark (no new users from
  2026-08-04), Firebase Studio (sunsetting 2027-03-22), GPT Pilot (unmaintained, supply-chain incident).

## Category B: multi-agent "software company" frameworks

- **MetaGPT** (github.com/FoundationAgents/MetaGPT, MIT, 71k stars, last push 2026-01-21): PM, architect,
  project manager, engineer roles; output is a repository in `./workspace`; no deploy, no cross-project
  memory, no upgrade. Effort moved to the hosted Atoms product (INFERRED).
- **ChatDev** (github.com/OpenBMB/ChatDev, Apache-2.0, 34.6k stars): CEO, CTO, programmer, reviewer; 2.0
  uses YAML workflows; no deploy. **Experiential Co-Learning** (arxiv.org/html/2312.17025v3): on 1,200 SRDD
  tasks with GPT-3.5, quality 0.4267 to 0.7304, completeness 0.6131 to 0.9497, executability 0.880 to
  0.965, duration 148.2 s to 122.8 s. Token cost not reported. The only numbered cross-build memory result.
- **CrewAI, AutoGen (archived in favour of Microsoft Agent Framework), Microsoft Agent Framework, OpenAI
  Agents SDK, LangGraph, Agency Swarm**: orchestration with per-thread or per-store memory; none generates
  or deploys applications; none measures memory benefit (LangGraph's docs say to use LangSmith yourself).
- **Coding agents**: gpt-engineer archived 2026-04-22 ("precursor to lovable.dev"); Aider has a local
  `--auto-test` loop, no deploy; SWE-agent is maintenance-only, mini-swe-agent has no persistent memory.
- **Memory layers**: Letta (memory blocks, no memory-vs-no-memory comparison on its leaderboard); Mem0
  (arxiv.org/abs/2504.19413: LOCOMO +26% LLM-judge vs OpenAI memory; the README's 2026 numbers are for the
  managed platform); Zep/Graphiti (arxiv.org/abs/2501.13956: DMR 94.8%). All conversational recall.
- Small GitHub repos closest to the vision on paper: autonomous-software-delivery-organization (Apache-2.0,
  2 stars, 7 commits; claims canary rollouts with SLO gates, OIDC, Kubernetes; maturity UNKNOWN);
  claude-code-app-studio (19 roles, gates on CI output, no deploy); auto-co-meta (DevOps agent deploys to
  Railway/Vercel, no verification gate). None has measured anything.

## Category C: template engines and SaaS starters

- **Copier** (copier.readthedocs.io, MIT): `copier update` reads `.copier-answers.yml`, regenerates a fresh
  project from the recorded template version, diffs it against the current one to capture local edits,
  renders the latest template, re-applies the diff, runs migrations; conflicts as inline git markers or
  `.rej`; `copier check-update` exits 2 when a newer tag exists. Needs the template tagged in git and a
  clean destination.
- **Cruft** over Cookiecutter (MIT): `.cruft.json` with the template commit; `cruft update`, `cruft check`
  (CI exit 1), `cruft diff --exit-code`, `cruft link` for existing projects.
- **Projen** (Apache-2.0): synthesized files are read-only and anti-tamper checked; upgrades by
  regeneration from `.projenrc`. **Nx** (MIT): `nx migrate` writes `migrations.json`, runs generator-based
  or prompt-based (AI) codemods per plugin.
- **Yeoman, Backstage scaffolder, Rails templates, create-t3-app**: one-shot; no recorded template version;
  Backstage's only re-run is "Start Over".
- **SaaS kits**: Pegasus (commercial; allauth, Sentry, Kamal; upgrade by a pristine `pegasus` branch and git
  merge, or a Claude Code skill), Bullet Train (MIT; Devise 2FA; gem bumps plus `git merge vX.Y.Z`),
  Makerkit and Supastarter (commercial; `git pull upstream`), Open SaaS (MIT; "We advise against merging
  the latest template changes"), ShipFast (removes the upstream remote by design). None ships Keycloak or
  metrics. Loco.rs (Apache-2.0, Rust): own JWT auth, Docker/nginx generators, manual CHANGELOG upgrades.
  Shuttle's repository was archived 2026-04-25.

## Category D: platform engineering and infra-from-code

- **Garden** (MPL-2.0): Build/Deploy/Test action graph on Kubernetes; Deploy waits for health (300 s
  default), Tests run after; result caching. The closest existing deploy-then-prove gate. No blueprint,
  no roles.
- **Humanitec** (commercial, from $2,199/month), **Kratix** (Apache-2.0, Promise revisions with
  pin-or-follow), **Crossplane** (Apache-2.0, CompositionRevisions): upgrade propagation solved for
  infrastructure definitions, not for application code stamped from a template.
- **Backstage** (Apache-2.0): scaffolder templates in and publish to SCM; no deploy or health action; no
  upgrade. **Radius, Score, Kusion, Nitric, SST, Encore, Devtron, Pulumi templates and Neo, Terraform
  Stacks (BUSL, HCP-only)**: plumbing, vendor targets, or plan-shaped approvals; none ships an app
  blueprint with auth and observability.
- **Coolify** (Apache-2.0): single VPS over SSH, Docker; automatic HTTPS, health checks, DB backups, 360+
  one-click services including Keycloak. **Dokploy** (Apache-2.0 plus a source-available folder): Swarm,
  Traefik, backups, metrics shipping. **Kubero** (GPL-3.0): Kubernetes, MicroK8s page, SSO, metrics,
  backups only via cron jobs.
- Small repos: backstage-platform-template (MIT, 7 stars: ArgoCD, Crossplane, Prometheus/Grafana/Loki/Tempo,
  optional "AI plane", no auth, no upgrade); scaffold-operator (0 stars) states "No updates: an already
  scaffolded repository is never migrated".

## Licensing notes for adoption (docs/LICENSING.md section 6)

- Adopt and adapt with notices: Copier (MIT), Cruft (MIT), Coolify service definitions (Apache-2.0) as
  Keycloak wiring reference, Garden (MPL-2.0) as unmodified tool or as a design reference.
- Ideas only: Kubero (GPL-3.0), Dokploy's proprietary folder, Terraform (BUSL-1.1), Humanitec and Porter
  (commercial).

## What this means for Groundworks

The gap is the composition, not the parts: a composed blueprint on the owner's estate, a role team with a
live gate, a measured memory, and a template delta. In-house state measured the same day: the registry
has 18 projects and 3 stacks; the two templates are a Rust CLI and an axum service with a Dockerfile and
nothing about Keycloak, metrics or Kubernetes; the web stack has no template; `bootstrap` is a one-shot
copy with no template version recorded. Order of work decided 2026-10-29: Copier for the template and its
version first (a template without an upgrade path should never get a second user), then the blueprint
composition derived from one live project as the oracle, then a Garden-style deploy gate in `gate`.

## Searches run

GitHub REST `search/repositories` (sorted by stars): "AI software team builds and deploys", "multi-agent
SaaS generator", "self-improving coding agent memory", "multi-agent software company", "autonomous devops
agent deploy kubernetes", "AI agent generates full stack app deploy", "fullstack ai framework", "lovable
alternative open source", "topic:ai-app-builder", "saas boilerplate keycloak", "fullstack template keycloak
prometheus grafana", "keycloak prometheus grafana template", "saas starter keycloak", "golden path keycloak
observability kubernetes", "AI agent platform engineering kubernetes golden path", and about twenty
rephrasings. Web engines tried and blocked or useless that day: Bing (dictionary noise), DuckDuckGo
(CAPTCHA), Brave (429), Startpage, Ecosia, Yandex, Marginalia, SearXNG instances. Pages fetched: about 120
documentation pages and READMEs, cited inline above by host.
