//! Groundworks operations: the registry, journal, knowledge pages, secret references, running
//! commands and gates, and scaffolding. Shared by the MCP server and the CLI (`gw-mcp`).
//! Absorbed from an earlier standalone prototype, 2026-10-24.

pub mod ask;
pub mod attack;
pub mod claude_setup;
pub mod eval;
pub mod eval_router;
pub mod eval_run;
pub mod exec;
pub mod frame;
pub mod home;
pub mod hook;
pub mod intake;
pub mod journal;
pub mod knowledge;
pub mod lens;
pub mod ops;
pub mod panel;
pub mod pulse;
pub mod refusal;
pub mod registry;
pub mod rerank;
pub mod scaffold;
pub mod scan;
pub mod secrets;
pub mod store;
pub mod supervise;
pub mod survey;
