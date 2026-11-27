FROM rust:1.90.0-bookworm AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-20250908-slim
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=build /app/target/release/benchmark-api-rust /usr/local/bin/benchmark-api-rust
USER 10001:10001
ENV PORT=8080 DB_POOL_MAX=10
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/benchmark-api-rust"]
