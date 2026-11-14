# evernode-vote as one static binary, built without Rust on the host:
#   docker build -o /usr/local/bin vote
# Alpine links against musl, so the binary runs on any Linux of the build host's architecture.
FROM rust:1.90.0-alpine3.22 AS build
RUN apk add --no-cache build-base openssl-dev openssl-libs-static zstd-dev zstd-static
ENV OPENSSL_STATIC=1
WORKDIR /src
COPY . .
RUN cargo build --release --locked

FROM scratch
COPY --from=build /src/target/release/evernode-vote /evernode-vote
