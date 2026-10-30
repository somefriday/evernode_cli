# Live local-network deployment and recovery

This harness drives the installed `evernode` command against an existing local
`ever-node` network from a Linux manager container. The network itself is not
part of this repository. Read **What this cannot establish** at the end before
treating anything here as production configuration.

## Target a network

Everything network-specific comes from the environment, so the same harness
works against a regenerated network:

| Variable | Meaning |
| --- | --- |
| `E2E_BASE` | Private state directory, bind-mounted at the **same absolute path** inside and outside the manager |
| `E2E_ZEROSTATE` | Host path of the network's zerostate directory |
| `E2E_IMAGE` | Node image for created nodes, as built by `evernode image build` |
| `E2E_NEW_IP`, `E2E_IMPORT_IP` | Address each node takes on that bridge |
| `E2E_NEW_PORT`, `E2E_IMPORT_PORT` | ADNL port for each node |
| `E2E_NEW_NAME`, `E2E_IMPORT_NAME` | Validator names, so a rerun can coexist with containers an earlier run left behind |
| `E2E_DOCKER_NETWORK` | External Docker bridge the node containers join |
| `EVERNODE_E2E_NETWORK` | Any non-empty value makes `sitecustomize.py` install the Compose adapter |
| `E2E_REGISTRY` | Which registry `bootstrap.py` prepares (`fresh` or `import`) |
| `E2E_REFRESH_SOURCE=1` | Re-copy the checkout's vendored scripts over an existing source, the way a server operator's `git pull` would |
| `E2E_CHECKPOINT_SOURCE` | Only for a network too old to synchronize from its zerostate: a stopped observer's workspace to copy blockchain data from |
| `E2E_RELAY_CONTAINER`, `E2E_RELAY_CONSOLE` | Relay container and its console path, when the relay is used |
| `E2E_BUILD_STATE_DIR` | Where `image build` recorded its image, if not the default `/var/lib/ever-validator` |
| `E2E_RELAY_SENDMESSAGE=1` | Only if the managed node cannot deliver external messages: relay `sendmessage` through the container named in `E2E_RELAY_CONTAINER` |

The manager needs the Docker socket, this checkout at `/workspace`, the run's
`net/` at `/network`, ever-node upstream at `/upstream`, and `E2E_BASE` at its
own absolute path. Remove its `/.dockerenv` so the vendored `env.sh` uses Docker
host mode; node containers keep theirs.

## Build the manager

The manager is a Linux host stand-in, built from a bare `ubuntu:24.04` and
provisioned exactly as README.md's install section says. Nothing is reused from
an earlier image, so a run also tests that the documented install still works:

```sh
mkdir -p /tmp/evernode-manager-build/project
tar --exclude='.git' --exclude='__pycache__' --exclude='tests/e2e/results' -cf - . \
  | tar -xf - -C /tmp/evernode-manager-build/project
docker build -t evernode-prod:ubuntu24 \
  -f tests/e2e/manager.Dockerfile /tmp/evernode-manager-build
```

`evernode host setup` provides everything else, and needs an init system to
enable the docker, cron and chrony services it requires. Check that path in its
own container, because a manager sharing the host's Docker socket has no init:

```sh
docker build -t evernode-prod:ubuntu24-systemd \
  --build-arg BASE=evernode-prod:ubuntu24 \
  -f tests/e2e/manager-systemd.Dockerfile /tmp/evernode-manager-build
docker run -d --name evernode-hostsetup --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
  evernode-prod:ubuntu24-systemd
docker exec evernode-hostsetup evernode host setup --yes
docker exec evernode-hostsetup evernode host check
```

Run the manager itself with the host's Docker socket, this checkout at
`/workspace`, the run's `net/` at `/network`, ever-node upstream at `/upstream`
and `E2E_BASE` at its own absolute path, then `evernode host setup --yes` inside
it: it installs the same packages and stops only at service enablement, which
the container above has already covered. Start `cron` by hand there, so an
installed schedule really runs. Remove its `/.dockerenv` so the vendored
`env.sh` uses Docker host mode; node containers keep theirs.

## Build the node image

Node containers use an image this CLI builds itself, from source, through the
production recipe. Pin the node revision to the commit the target network runs,
or the node cannot join it:

```sh
docker exec <manager> evernode image build --node-ref <commit> --yes
```

`bootstrap.py` then copies that build's own record into the test registry, so
the registry states the real provenance instead of a stub.

## Run the cases

```sh
E2E_REGISTRY=fresh  python3 tests/e2e/bootstrap.py
E2E_REGISTRY=import python3 tests/e2e/bootstrap.py

python3 tests/e2e/readme_workflows.py new-wallet    # then fund the Safe
python3 tests/e2e/readme_workflows.py new-depool    # then fund the DePool
python3 tests/e2e/readme_workflows.py new-finish
python3 tests/e2e/withdrawal_cases.py new
python3 tests/e2e/confirm_case.py new
python3 tests/e2e/readme_election.py new --rounds 1 --keep-schedule
python3 tests/e2e/pipeline_stake.py new
python3 tests/e2e/readme_election.py new --rounds 2 --keep-schedule
python3 tests/e2e/readme_workflows.py import
python3 tests/e2e/readme_election.py import
python3 tests/e2e/readme_audit.py
```

Fund the generated Safe and DePool between phases with migration-verifier's
offline `wallet-send` and a console `sendmessage`, as the target network's
`AGENT.md` describes.

`withdrawal_cases.py` asserts on the DePool's own getters, because a withdrawal
is a request the contract settles when the round completes, not an immediate
transfer. The two forms differ: a partial withdrawal records a value, while
withdrawing everything clears reinvestment and records no value at all.

`confirm_case.py` induces the state an interrupted election attempt leaves - a
Safe transaction holding fewer than its required signatures - by withholding
the custodian keys this node would sign with, then completes it with `depool
confirm`.

`pipeline_stake.py` stakes into two consecutive collecting rounds and checks
that both carry it, which is what lets a node bid in consecutive elections. It
reads what each round holds from the contract, because a finished round's stake
is paid back into whichever round is collecting at that moment.

`readme_election.py` shortens only the generated test runner's waits, because
this network's election window is 120 s while production windows are hours:
`--sequence-delay`, `--launch-offset`, `--poll` and `--min-remaining`. Every
edit is recorded in the trace. Signing needs no such adaptation: each signature
is checked against the wallet's own record instead of waiting a fixed pause. The installed production
scheduler is never modified, and the cron daemon is kept stopped so attempts can
be observed one at a time.

`withdrawal_cases.py` asserts on the DePool's own getters, because a withdrawal
is a request the contract settles when the round completes, not an immediate
transfer.

Terminal prompts are driven by `terminal.py`; test mnemonics stay in the private
state directory and are redacted from logs. `export_readme_evidence.py` exports
public logs and evidence to `E2E_RESULTS`, refusing to copy anything containing
a known test phrase or secret field.

## What this cannot establish

Everything below is a local adaptation. None of it may be present in
production, where `--network main` or `--network devnet` selects the shipped
configuration.

- **Transport.** Node containers join an existing Docker bridge with a static
  address and do not publish their ADNL port. A real node publishes its
  allocated UDP port and the operator opens exactly that port.
- **Bootstrap.** A read-only zerostate directory is mounted and `-z /zerostate`
  appended to the node command, and the shipped global configuration and
  elector ABI are replaced with the local network's. Production nodes bootstrap
  from the global configuration.
- **Node settings.** `configs/rnode/default_config.json` takes its non-identity
  values from the local network, with a reduced cells-db cache and
  `sync_by_archives` disabled. A production node keeps the shipped defaults.
- **State directory.** A host path bind-mounted at the same absolute path
  inside and outside the manager, so node bind mounts resolve on the shared
  daemon. Production uses the default `/var/lib/ever-validator`.
- **Timing.** A local network runs 300-second rounds with a 120-second election
  window; production rounds last hours. Only the test runner's copy of the
  election sequence is shortened, never the installed one.

Four things no private network can exercise, and which therefore remain
unproven after a successful run: public-network behaviour (real peers, ADNL
reachability, mainnet gas and real DePool economics), cold synchronization into
a long-lived chain from archives rather than a fresh zerostate, competitive
stake sizes, and long-run stability.
