//! Groundworks operations: the registry, journal, knowledge pages, secret references, running
//! commands and gates, and scaffolding. Shared by the MCP server and the CLI (`gw-mcp`).
//! Absorbed from an earlier standalone prototype, 2026-10-24.

pub mod exec;
pub mod home;
pub mod journal;
pub mod knowledge;
pub mod ops;
pub mod registry;
pub mod scaffold;
pub mod scan;
pub mod secrets;
pub mod store;
