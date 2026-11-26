# Build recipe for the nominator-pool contract tools (denominator validators).
#
# These tools compile the vendored pool contract, derive the pools' addresses,
# read a pool's storage and build the message bodies its validator, its stakers
# and anyone else may send. They need Node.js, which a validator host should not
# have to carry, so they live in this image and the host keeps only Docker and
# Python.
#
# Nothing here signs anything or talks to a chain. The node's Safe signs through
# the kit's own scripts and the node's console broadcasts, so this image never
# sees a key. The layout under /opt/nominator matches the single-nominator
# image's, so the kit reads a pool with the same commands. The build fails if
# the contract does not compile to the code hash recorded in PINNED.json.
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

# Prove the pinned bytecode at build time: a usable image is itself evidence
# that the contract compiles to the hash that was reviewed.
RUN set -eu; \
    compiled=$(node tools/cells.cjs compile /tmp/code.boc); \
    echo "$compiled"; \
    expected=$(node -e "console.log(require('./PINNED.json').codeHash)"); \
    actual=$(node -e "console.log(JSON.parse(process.argv[1]).codeHash)" "$compiled"); \
    [ "$actual" = "$expected" ] || { echo "code hash $actual != pinned $expected" >&2; exit 1; }; \
    rm -f /tmp/code.boc; \
    echo "pinned pool contract code verified"

ENTRYPOINT ["/bin/sh", "-c"]
