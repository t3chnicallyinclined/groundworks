//! The idea-bridge tools (idea-bridges design 2026-10-30, slices B1-B3), on a router of their own so the
//! main tool list in `server.rs` stays as it is: `card_add` (B1).

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

#[tool_router(router = bridge_router, vis = "pub(super)")]
impl Groundworks {
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
}
