# {{name}}

HTTP service on axum. `GET /healthz` answers `ok`.

```
BIND=127.0.0.1:8080 RUST_LOG=info cargo run
```

Gate (what "done" means): `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
Through Groundworks: the `gate` tool on {{name}}.

Scaffolded by Groundworks from the `rust-service` template at commit `{{ _copier_answers._commit }}`;
`groundworks upgrade {{name}}` brings later template changes (commit your work first).
