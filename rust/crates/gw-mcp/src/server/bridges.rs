//! The idea-bridge tools (idea-bridges design 2026-10-30, slices B1-B3), on a router of their own so the
//! main tool list in `server.rs` stays as it is: `card_add` (B1); `bridge_add`, `bridge_advance`,
//! `bridge_list` (B2).

use std::sync::Arc;

use rmcp::handler::server::wrapper::Parameters;
use rmcp::{tool, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;

use super::{Groundworks, fail};
use gw_ops::store;

#[derive(Deserialize, JsonSchema)]
pub struct CardAddArgs {
    pub project: String,
    /// The card: kind, facets (x; y for a problem; a and mech for a capability), quote and confidence.
    #[serde(flatten)]
    pub card: gw_ops::card::CardIn,
    /// Which team member is writing.
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct BridgeAddArgs {
    /// The target project: the one with the problem.
    pub project: String,
    /// The problem card of the target project (`card:...`).
    pub target: String,
    /// The source project: shared, and on neither side of a wall.
    pub source: String,
    /// The capability card of the source project (`card:...`).
    pub card: String,
    /// The source project's problem card the chain went through, when found that way.
    pub via: Option<String>,
    /// How it was found (a bridge_propose arm, or `manual`).
    pub found_by: Option<String>,
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct BridgeAdvanceArgs {
    pub project: String,
    /// The bridge (`bridge:...`).
    pub bridge: String,
    #[serde(flatten)]
    pub step: gw_ops::bridge::AdvanceIn,
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct BridgeListArgs {
    pub project: String,
    /// At most this many of the project's own cards (default 50).
    pub limit: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct BridgeProposeArgs {
    pub project: String,
    /// The problem card of the project to find capabilities for (`card:...`).
    pub target: String,
    /// facet (default: goal against goal, blocker against blocker through the problem a capability
    /// solved), flat (whole card against whole card, the baseline) or random.
    pub arm: Option<String>,
    /// How many candidates (default 10, at most 50).
    pub k: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct FunnelBatchArgs {
    pub project: String,
    /// The problem card of the project (`card:...`).
    pub target: String,
    /// Only this source: its project and card (`card:...`).
    pub source: Option<String>,
    pub card: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct FunnelAnswerArgs {
    pub project: String,
    /// The problem card the batch was for.
    pub target: String,
    pub answers: Vec<gw_ops::funnel::AnswerIn>,
    pub by: Option<String>,
}

#[tool_router(router = bridge_router, vis = "pub(super)")]
impl Groundworks {
    #[tool(
        description = "The gate funnel for a problem card: the checks to answer (each source assumption, and novelty) for candidates above the score floor, at most 8 at a time, with both quotes as data; what the judgement cache already answered (reused until either quote changes); and what fell below the floor (never asked). Writes nothing."
    )]
    async fn funnel_batch(&self, args: Parameters<FunnelBatchArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let only = a.source.as_deref().zip(a.card.as_deref());
            gw_ops::funnel::batch(&home, &store, &a.project, &a.target, only)
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Answer funnel checks: [{id, verdict, confidence 0-1, why}]. Each is cached as a judgement at INFERRED (UNKNOWN for verdict unknown), never higher; a first answer under 0.6 confidence or unknown gets one second look. A cached verdict still goes onto the bridge through bridge_advance."
    )]
    async fn funnel_answer(&self, args: Parameters<FunnelAnswerArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::funnel::answer(
                &home,
                &store,
                &a.project,
                &a.target,
                &a.answers,
                a.by.as_deref().unwrap_or("agent"),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Propose candidate bridges for a problem card: capabilities of the shared, unwalled projects ranked by BM25 per facet (no model). Writes nothing; each candidate is a hypothesis, served as data, to keep with bridge_add."
    )]
    async fn bridge_propose(&self, args: Parameters<BridgeProposeArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::bridge::propose(
                &home,
                &store,
                &a.project,
                &a.target,
                a.arm.as_deref().unwrap_or("facet"),
                a.k.unwrap_or(10).clamp(1, 50),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Record an idea card: one problem (x the goal, y the blocker), capability (x what it achieves, mech how, a what it assumes, solves the problem card it got past), assumption or limitation of a project. The quote is {unit (key, locator, or path), lines [a, b]} copied by the program, or {fact, text}. Refused: a quote not word for word, a facet sharing no word with the quote or over 300 characters, secret-shaped text, CONFIRMED from a memory/instructions/agent/skill/command unit."
    )]
    async fn card_add(&self, args: Parameters<CardAddArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let project = gw_ops::registry::Registry::load(&home)?
                .project(&a.project)?
                .name
                .clone();
            gw_ops::card::add(&store, &project, &a.card, a.by.as_deref().unwrap_or("agent"))
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Record a candidate bridge: a capability card of another project (shared, not walled, never private or isolated) as a hypothesis for a problem card of this one. The bridge holds the source as project/card/hash only. Grade INFERRED until supported."
    )]
    async fn bridge_add(&self, args: Parameters<BridgeAddArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::bridge::add(
                &home,
                &store,
                &a.project,
                &a.target,
                &a.source,
                &a.card,
                a.via.as_deref(),
                a.found_by.as_deref().unwrap_or("manual"),
                a.by.as_deref().unwrap_or("agent"),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Move a bridge along its gate. Register the test first (test, test_command, test_expect, optional control_command expected to fail; once, before it runs). to=testable also needs checks (one per source assumption: {assumption: index, verdict holds|fails|unknown, quote of THIS project as for a card}), novelty_query with novelty_verdict not_made (the program runs the search and refuses not_made when this project already holds the link), and lens_verdicts with a skeptic proceed (to=candidate records verdicts or the test without moving). to=supported needs a green receipt of the registered command and a red one, both run after registration, from a credential other than the proposer's: the owner promotes with `groundworks owner bridge-support`. to=refuted needs a failing run of the registered command or a check whose verdict is fails."
    )]
    async fn bridge_advance(&self, args: Parameters<BridgeAdvanceArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::bridge::advance(
                &home,
                &store,
                &a.project,
                &a.bridge,
                &a.step,
                a.by.as_deref().unwrap_or("agent"),
                None,
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "A project's idea cards and bridges, each inside a data frame. Reach is re-checked: a bridge whose source is now walled, private or isolated is listed without it."
    )]
    async fn bridge_list(&self, args: Parameters<BridgeListArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || gw_ops::bridge::list(&home, &store, &a.project, a.limit.unwrap_or(50)))
            .await
            .map_err(fail)
    }
}
