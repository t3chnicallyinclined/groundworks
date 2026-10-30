//! The attack tools (idea-bridges design 2026-10-30, amendment A2, slice 10), on a router of their own so the
//! main tool list in `server.rs` stays as it is: `attack_add`, `attack_judge`, `answer_add`, `attack_list`.

use std::sync::Arc;

use rmcp::handler::server::wrapper::Parameters;
use rmcp::{tool, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;

use super::{Groundworks, fail};
use gw_ops::store;

#[derive(Deserialize, JsonSchema)]
pub struct AttackAddArgs {
    pub project: String,
    #[serde(flatten)]
    pub attack: gw_ops::attack::AttackIn,
    /// Which team member is writing.
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct AttackJudgeArgs {
    pub project: String,
    /// The attack (`attack:...`, or the attacking `fact:...`).
    pub attack: String,
    /// fatal, damaging or survivable. Set once: never changed, lowered or withdrawn afterwards.
    pub verdict: String,
    /// Why this verdict.
    pub ruling: String,
}

#[derive(Deserialize, JsonSchema)]
pub struct AnswerAddArgs {
    pub project: String,
    #[serde(flatten)]
    pub answer: gw_ops::attack::AnswerIn,
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct AttackListArgs {
    pub project: String,
    /// One fact (`fact:...`: attacks on it and by it) or one attack (`attack:...`); default every attack.
    pub of: Option<String>,
    /// At most this many (default 100).
    pub limit: Option<usize>,
}

#[tool_router(router = attack_router, vis = "pub(super)")]
impl Groundworks {
    #[tool(
        description = "Attack a fact: record the attacking fact (as journal_add: quote checked word for word, CONFIRMED needs evidence) and its attack with a verdict, which is required: fatal, damaging, survivable (each with its ruling), or unjudged (read as fatal until judged). An attack is never withdrawn or deleted."
    )]
    async fn attack_add(&self, args: Parameters<AttackAddArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::attack::add(&home, &store, &a.project, &a.attack, a.by.as_deref().unwrap_or("agent"))
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Judge an unjudged attack: set its verdict (fatal, damaging or survivable) with the ruling. A verdict is set once; a judged attack cannot be changed, lowered or withdrawn, and a new attack reopens the question."
    )]
    async fn attack_judge(&self, args: Parameters<AttackJudgeArgs>) -> Result<String, String> {
        let a = args.0;
        let store = self.store().await?;
        store::blocking(move || gw_ops::attack::judge(&store, &a.project, &a.attack, &a.verdict, &a.ruling))
            .await
            .map_err(fail)
    }

    #[tool(
        description = "Answer an attack, once: rebutted (the answering fact quotes its evidence), fixed (it carries a receipt that ran green), narrowed (replaced_by names the narrower claim) or conceded. Give an answering fact already recorded (fact) or a new one (text, source, confidence, quote, receipt). An answer is itself a fact, so it can be attacked."
    )]
    async fn answer_add(&self, args: Parameters<AnswerAddArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            gw_ops::attack::answer(&home, &store, &a.project, &a.answer, a.by.as_deref().unwrap_or("agent"))
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "A project's attacks, oldest first: verdict, ruling, who judged, and every answer; for one fact (attacks on it and by it) or one attack. Unjudged reads as fatal until judged."
    )]
    async fn attack_list(&self, args: Parameters<AttackListArgs>) -> Result<String, String> {
        let a = args.0;
        let store = self.store().await?;
        store::blocking(move || gw_ops::attack::list(&store, &a.project, a.of.as_deref(), a.limit.unwrap_or(100)))
            .await
            .map_err(fail)
    }
}
