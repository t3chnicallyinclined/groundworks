FROM rust:1.93-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN useradd --system --uid 10001 app
COPY --from=build /src/target/release/{{name}} /usr/local/bin/{{name}}
USER app
ENV BIND=0.0.0.0:8080
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/{{name}}"]
