# Build recipe for the single-nominator contract tools.
#
# These tools compile the vendored contract, derive its address and build the
# message bodies its owner and its validator wallet send. They need Node.js,
# which a validator host should not have to carry, so they live in this image
# instead and the host keeps only Docker and Python.
#
# Nothing here signs anything or talks to a chain. The node's Safe signs
# through the kit's own scripts and the node's console broadcasts, so this
# image never sees a key. The compile is pinned so its output is
# reproducible: the build fails if the contract's code hash is not the one
# recorded in PINNED.json.
FROM node:22-bookworm-slim AS compiler

COPY src/package.json src/package-lock.json /build/
WORKDIR /build
# A committed lockfile is what makes the compiler version, and therefore the
# contract's code hash, the same on every host.
RUN npm ci --no-audit --no-fund

FROM node:22-bookworm-slim

ENV NOMINATOR_HOME=/opt/nominator

COPY --from=compiler /build/node_modules ${NOMINATOR_HOME}/node_modules
COPY src/contracts ${NOMINATOR_HOME}/contracts
COPY src/tools/cells.cjs ${NOMINATOR_HOME}/tools/cells.cjs
COPY src/package.json src/PINNED.json ${NOMINATOR_HOME}/

WORKDIR ${NOMINATOR_HOME}

# Prove the pinned bytecode at build time rather than at staking time: a
# usable image is itself evidence that the contract compiles to the hash that
# was reviewed.
RUN set -eu; \
    compiled=$(node tools/cells.cjs compile /tmp/code.boc); \
    echo "$compiled"; \
    expected=$(node -e "console.log(require('./PINNED.json').codeHash)"); \
    actual=$(node -e "console.log(JSON.parse(process.argv[1]).codeHash)" "$compiled"); \
    [ "$actual" = "$expected" ] || { echo "code hash $actual != pinned $expected" >&2; exit 1; }; \
    rm -f /tmp/code.boc; \
    echo "pinned contract code verified"

ENTRYPOINT ["/bin/sh", "-c"]
