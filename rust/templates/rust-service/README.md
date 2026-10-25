# {{name}}

HTTP service on axum. `GET /healthz` answers `ok`.

```
BIND=127.0.0.1:8080 RUST_LOG=info cargo run
```

Gate (what "done" means): `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
Through Groundworks: the `gate` tool on {{name}}.
