use std::net::SocketAddr;

use anyhow::Result;
use axum::Router;
use axum::routing::get;
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with_writer(std::io::stderr)
        .init();
    let addr: SocketAddr = std::env::var("BIND")
        .unwrap_or_else(|_| "127.0.0.1:8080".to_owned())
        .parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(%addr, "listening");
    axum::serve(listener, router())
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

fn router() -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .layer(TraceLayer::new_for_http())
}

async fn healthz() -> &'static str {
    "ok"
}

async fn shutdown() {
    if let Err(e) = tokio::signal::ctrl_c().await {
        tracing::error!(error = %e, "could not listen for shutdown");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn healthz_says_ok() {
        assert_eq!(healthz().await, "ok");
    }
}
