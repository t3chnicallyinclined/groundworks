//! The server a session gets when its folder is not under any Groundworks home. The plugin is
//! installed for every session, so this must start cleanly rather than exit: it answers with one
//! tool that says why Groundworks is inactive here, and costs almost nothing in context.

use std::sync::Arc;

use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::model::{Implementation, ServerCapabilities, ServerConfig};
use rmcp::{ServerHandler, tool, tool_handler, tool_router};

#[derive(Clone)]
pub struct Homeless {
    reason: Arc<String>,
    tool_router: ToolRouter<Self>,
}

#[tool_router]
impl Homeless {
    pub fn new(reason: String) -> Self {
        Self {
            reason: Arc::new(reason),
            tool_router: Self::tool_router(),
        }
    }

    #[tool(description = "Why Groundworks is not active in this folder, and how to bring it under Groundworks.")]
    async fn status(&self) -> String {
        format!(
            "Groundworks is not active here: {}\n\nOpen a folder under a Groundworks home (one with \
             .groundworks/groundworks.toml above it), or set GROUNDWORKS_HOME.",
            self.reason
        )
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for Homeless {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("groundworks", env!("CARGO_PKG_VERSION")))
            .with_instructions("Groundworks is not active in this folder; its only tool, status, says why.")
    }
}
