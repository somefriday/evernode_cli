# evernode

`evernode` builds and operates isolated Everscale validator nodes in Docker on
Ubuntu and Debian. It wraps the established Ever-Validator wallet, DePool,
stake, and election scripts while keeping each node's database, keys,
containers, metrics, ports, and cron schedule separate.

Matching source inputs share one immutable node image. Node data is stored
root-only under `/var/lib/ever-validator`.

## Install

Clone this repository to the server, then install the command in its own
virtual environment:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
git clone https://github.com/somefriday/evernode_cli.git /root/evernode_cli

sudo install -d -m 0755 /opt/evernode
sudo python3 -m venv /opt/evernode/venv
sudo /opt/evernode/venv/bin/python -m pip install --upgrade pip
sudo /opt/evernode/venv/bin/python -m pip install /root/evernode_cli
sudo ln -sfn /opt/evernode/venv/bin/evernode /usr/local/bin/evernode

sudo evernode host setup --yes
sudo evernode host check
```

`host setup` detects Ubuntu or Debian, installs Docker Engine, Buildx, Compose,
and the reference-script dependencies. It does not modify the firewall. Allow
only each node's allocated ADNL UDP port through the host and provider firewall.

### Update an existing installation

After pulling a newer checkout, reinstall the local package into the existing
virtual environment. Confirm that the wallet command is available before
continuing an import flow.

```bash
cd /root/evernode_cli
git pull --ff-only
sudo /opt/evernode/venv/bin/python -m pip install --upgrade /root/evernode_cli
sudo evernode --version
sudo evernode wallet --help
```

Reinstalling the package does not update the scripts already saved in each
node's workspace, so an upgrade is two steps, not one:

```bash
cd /root/evernode_cli && git pull --ff-only
sudo /opt/evernode/venv/bin/python -m pip install --upgrade /root/evernode_cli
sudo evernode node sync-scripts --all --dry-run
sudo evernode node sync-scripts --all
```

Validators keep running throughout. You do not need to restart a node, stop and
restart its elections, or rebuild its image: scripts are replaced in place under
the container's existing mounts, and each node keeps the image it was created
with. A node that is validating stays in its set. On an ARM server, also re-run
`sudo evernode host setup --yes`, which replaces host tools that were installed
for the wrong architecture; on x86-64 it is optional and changes nothing.

## Images

`node create` shows the latest `master` source first, version tags from newest
to oldest, and available managed images. Use Up/Down and Enter to select;
press Tab to browse other branches. Selecting a source ref builds a new image
at the commit shown in the menu. The same built image can serve multiple
validators.

The source list comes from the default ever-node repository, or from the
repository passed with `--node-repo URL`. If the ref lookup fails, local images
remain selectable. Use `--build-new --node-ref REF` to specify a ref directly.

You can also build an image ahead of time:

```bash
sudo evernode image build --yes
sudo evernode image list
```

For scripted creation, use `--image IMAGE` or `--build-new` with `--yes` and
the required configuration flags; the menu requires a terminal. `--image`
accepts a tag from `image list`.

## New validator

`node create` first asks which kind of validator this is, and the answer
decides the rest of the setup:

- **DePool** — the stake sits in a DePool contract that other people can also
  stake into, and the node bids on the pool's behalf. This is the rest of this
  section.
- **Solo** — the node stakes its own money straight to the elector from its own
  Safe wallet, with no DePool and no participants. See
  [Solo validator](#solo-validator-no-depool).

Every fixed choice during creation — validator type, network, workchain, DePool
type, image — is a terminal menu, so nothing has to be spelled correctly from
memory. Each also has a flag (`--validator-type`, `--network`, `--workchain`,
`--depool-type`, `--image`) for scripted installs; a noninteractive run with no
`--validator-type` creates a DePool validator, which is what every node created
before this choice existed is.

Create and synchronize the node:

```bash
sudo evernode node create -n validator01
sudo evernode node sync -n validator01 --wait
```

`node create` creates the node only. After synchronization, `wallet create`
generates the new Safe phrases and prints every phrase and the wallet address
to the interactive terminal. It then requires `SAVED` before it records that
stage. Save the output before funding the wallet and running its deployment
command.

**Keep those phrases offline for the life of the validator.** They are the one
thing in this setup that cannot be recovered from anywhere else: the only other
copy lives in the node's root-only `keys/` directory, so losing the server
without them loses the money in the Safe. They are also what `wallet recover`
needs to rebuild the validator elsewhere. Nothing the CLI does deletes them —
`node remove --purge-data` clears only the database and logs — but nothing the
CLI does can replace them either.

```bash
sudo evernode wallet create -n validator01
sudo evernode wallet deploy -n validator01
sudo evernode depool prepare -n validator01
sudo evernode depool deploy -n validator01
sudo evernode depool stake-initial -n validator01
sudo evernode election start -n validator01
```

With this checkout's vendored scripts (`node create --project-source
/root/evernode_cli/vendor/ever-validator`), `depool stake-initial` confirms its assurance transaction with the required local
custodian keys and waits for the stake to appear in the DePool. Retrying reuses
a matching pending transaction or recognizes an already completed stake.

## Import an existing Safe wallet and DePool

Create a new node from a selected image and provide the existing on-chain
addresses. Phrases are requested through hidden terminal prompts and saved as
root-only files.

```bash
sudo evernode node create -n validator01 \
  --import-wallet \
  --wallet-address 0:YOUR_64_HEX_WALLET_ADDRESS \
  --depool-address 0:YOUR_64_HEX_DEPOOL_ADDRESS \
  --custodians 3 \
  --required-signatures 2

sudo evernode node sync -n validator01 --wait
sudo evernode wallet recover -n validator01
sudo evernode wallet verify -n validator01
sudo evernode depool verify -n validator01
sudo evernode election start -n validator01
```

`wallet recover` uses the phrases entered during node creation, verifies their
public keys against the wallet's on-chain custodians, and does not print the
phrases again. `depool verify` checks that the DePool links to that wallet.
Wallet phrases do not restore the former node's ADNL, consensus, or console
keys. Fence the former validator before starting elections on a replacement.

## Solo validator (no DePool)

A solo validator sends its own stake straight to the elector from its own Safe
wallet. There is no DePool, so there are no participants, no proxies, no
validator assurance and no reward fraction — and no DePool round pipeline to
reason about. In exchange, every token staked is the operator's own, and the
whole stake plus its reward comes back to the Safe at the end of each round.

**The Safe has to be in the masterchain**, because that is the only workchain
the elector accepts a stake from. `wallet create` deploys it there for you.
This is separate from the workchain the node itself works in, which stays
whatever you chose during creation.

```bash
sudo evernode node create -n validator01 --validator-type solo
sudo evernode node sync -n validator01 --wait
sudo evernode wallet create -n validator01     # prints the -1: address and phrases
sudo evernode wallet deploy -n validator01     # fund that address first
sudo evernode stake set -n validator01 --mode balanced
sudo evernode election start -n validator01
```

DePool flags are refused for a solo node rather than silently ignored, and
`depool` commands say to use `stake` instead (and the reverse for a DePool
node), so the two modes cannot be mixed up on one node.

### Import an existing masterchain Safe

A solo validator can be rebuilt from the seed phrases of a Safe that already
exists, on a new server or a replacement node. The address must be a
masterchain one; a basechain Safe, including a DePool validator's, cannot
validate solo however it was created, so moving a DePool node to solo means a
new masterchain Safe rather than a reconfiguration.

```bash
sudo evernode node create -n validator01 \
  --validator-type solo \
  --import-wallet \
  --wallet-address -1:YOUR_64_HEX_WALLET_ADDRESS \
  --custodians 3 \
  --required-signatures 2

sudo evernode node sync -n validator01 --wait
sudo evernode wallet recover -n validator01
sudo evernode wallet verify -n validator01
sudo evernode stake set -n validator01 --mode balanced
sudo evernode election start -n validator01
```

`--depool-address` is not accepted — a solo import is complete with just its
Safe — and `wallet verify` checks the recovered phrases against the wallet's
on-chain custodians exactly as it does for a DePool node. `stake set` is part
of this flow, not optional: an imported node has no stake on record, and
`election start` refuses until it does.

### What this validator stakes

```bash
sudo evernode stake set -n validator01                     # menu, with both amounts shown
sudo evernode stake set -n validator01 --mode balanced
sudo evernode stake set -n validator01 --mode single
sudo evernode stake set -n validator01 --amount 100000
sudo evernode stake status -n validator01
```

**A stake is locked until its round finishes, and the next round's election
opens while this one is still running.** So validating *every* round means
having two stakes in flight at once — and two is the ceiling, because the
money from a finished round comes back in time to fund the election two rounds
out. That single fact is what the two modes are about:

| | Stake per election | Validates | Needs |
|---|---|---|---|
| `--mode balanced` | half the spendable balance | every round | both halves in flight |
| `--mode single` | all of it | every other round | one stake in flight |

**They earn the same.** A reward is proportional to the stake, so half the
stake every round and the full stake every other round come to the same thing
over two rounds. Balancing buys continuity, not yield. Choose `balanced` if
your stake is large enough that a single bid risks the elector's truncation
cap (it returns anything above `max_factor` times the smallest stake in the
set, earning nothing); choose `single` if halving would drop a bid near the
network's minimum stake or out of the elected set, because then balancing
turns half the rounds into none.

**A mode is arithmetic done once, not a rule that keeps adapting.** `stake set`
reads the balance and the chain's limits at the moment you run it, computes a
fixed number of tokens, prints the arithmetic, and writes that number. The
election scripts only ever read a fixed amount afterwards. Nothing re-sizes a
bid later, which is deliberate: a bid cannot be taken back, so sizing it
inside a live election window — on a balance that may still be waiting for a
recovery — is the one decision here with no way to correct it until the round
ends.

- `--amount N` names the number yourself.
- Run `stake set` with neither flag and it offers a menu with what each mode
  would stake on this wallet right now.
- `--all-balance` is a deprecated alias for `--mode single`.

`stake set` refuses a number the chain would reject — below the network's
minimum stake, above its maximum, or `--mode balanced` when half the balance
is under the minimum (it says to use `--mode single` instead). It warns, rather
than refuses, when the amount funds only one round at a time, when the wallet
cannot fund even one bid yet, and when part of the money is still at the
elector awaiting recovery.

`stake status` reports what this node is configured to stake, what the chain
says — the active election id, the wallet's balance and status, how much the
elector owes this wallet, any unsigned Safe transaction to it — and the two
derived numbers that matter: `spendable_tokens`, and `rounds_funded` with a
plain `funding` verdict of "funds consecutive rounds", "funds every other
round" or "funds no bid". Those are computed from the chain, not from what you
intended.

**Compounding is a decision, not a side effect.** The reward returns to the
Safe and stays there; re-running `stake set --mode balanced` re-reads the
balance and raises the number in one command. That is also how you take a
top-up into use. It is deliberately manual, because raising a stake can push
you over the truncation cap or change whether you win a slot.

`election start` refuses on a solo node with no stake on record, so a
validator cannot reach an election with nothing decided.

### Getting the stake back

A finished round's stake is **recovered automatically** by the scheduled
election cycle, before it places the next bid, so in normal operation there is
nothing to do. The manual commands exist for when the schedule is stopped:

```bash
sudo evernode stake recover -n validator01
sudo evernode stake confirm -n validator01 --transaction-id 7702496769499467587
```

`stake recover` asks the elector for everything it owes this wallet, signs with
every required local custodian key, and waits for the money to arrive before
reporting success. What the elector owes is recorded against the **wallet
address**, not the validator keys, so regenerating a node's keys — which
creating or importing a node does — never orphans a stake.

`stake confirm` adds this node's remaining signatures to one named pending
transaction, for a multi-custodian Safe where an attempt was interrupted
mid-signing. It is the same command as `depool confirm`, with the same rules:
the id is required, matched exactly, and bulk signing is refused.

**Check once that automatic recovery is working.** Compare
`recoverable_nanotokens` in `stake status` against the cron log after a round
has finished: the log should report a nonzero `recover_amount`. A node whose
workspace scripts predate this CLI's fix to the elector's recovery getter
reports `Nothing to recover` every cycle while the elector still owes it money,
and keeps bidding until the Safe runs dry. `node sync-scripts` delivers the
fix; `stake recover` collects what was left behind.

### The scheduled cycle

A solo node runs its own election sequence, `run-solo-election-sequence.sh`,
which `election status` names under `sequence`. It differs from the DePool one
on purpose. A DePool bid goes into the DePool that preparation ticks, so there
a failed preparation has to stop the cycle. A solo bid goes to the elector and
needs nothing preparation produces — preparation only asks for finished rounds
back — so a recovery that cannot run does not cancel the bid for the election
that is open now, which is the one thing the node cannot come back to later.
Each outcome is named in the cron log instead, and the last preparation output
is kept at `elections/last-preparation.log`.

That also means an interrupted attempt is recovered without help: preparation
stops at the half-signed request it finds, and the bid step finishes that one
instead of adding a second. The cycle waits for a recovery to reach the wallet
only when one was actually submitted, so a cycle with nothing to recover spends
none of the election window waiting.

## Operate a node

New nodes use `evernode-<validator-name>` and `statsd-<validator-name>` as their
Docker container names. Existing nodes keep their saved names when upgrading.
Use the validator name with CLI commands in either case.

One server can run several validators. Each gets its own workspace, keys,
database, container, statsd exporter, ADNL and metrics port, election schedule
and operation lock, so operating one never touches another. Ports are allocated
from what the other nodes already hold, so a clash is refused at creation
instead of discovered later — and each node's own ADNL UDP port has to be opened
in the firewall. Once more than one node exists, commands that act on a single
node require `-n NAME` and refuse rather than guess; `node list`,
`node resources` and `election list` report all of them, and `node stop`,
`node remove` and `node sync-scripts` accept `--all`.

```bash
sudo evernode node list
sudo evernode node status -n validator01
sudo evernode node logs -n validator01 --component node --follow
sudo evernode node resources -n validator01
sudo evernode election status -n validator01
sudo evernode election stop -n validator01
sudo evernode node stop -n validator01 --timeout 30
sudo evernode node start -n validator01
```

`node stop`, `node restart` and `node remove` refuse to act while the node is in
the current or next validator set, and `election status` shows that membership.
A current-set member misses the blocks it owes; a next-set member is also needed
for its successor consensus session, so taking one out can stall a small network
until it returns.

### Before you stop, restart or remove a validator

Work through this in order. The first two steps are what keep the network, and
your rewards, intact.

```bash
sudo evernode election stop -n validator01    # 1. stop bidding for a new set
sudo evernode election status -n validator01  # 2. wait until both sets say false
sudo evernode depool info -n validator01      # 3. resolve any unsigned transaction
sudo evernode node stop -n validator01        # 4. only now take it out
```

On a solo validator, step 3 is `stake status` and `stake confirm` instead of
`depool info` and `depool confirm`.

1. **Stop the schedule first**, so the node cannot win a place in the next set
   while you are waiting for the current one to end.
2. **Wait until the chain says it owes nothing.** In `election status`, both
   `chain.in_current_vset_p34` and `chain.in_next_vset_p36` must be false. That
   takes at most one round.
3. **Clear any unsigned Safe transaction.** An interrupted election attempt
   leaves one, it blocks later DePool operations, and the node you are about to
   stop is where you would finish it. `depool confirm --transaction-id` does it,
   or `stake confirm --transaction-id` on a solo validator. One holding fewer
   than its required signatures never executes and the Safe drops it after an
   hour, so nothing is lost either way — but until then the node will not bid.
4. **Then stop, restart or remove.**

`--force` skips the membership check. Use it when you are deliberately fencing a
validator you have already replaced, accepting that it loses the blocks it owes
for the rest of the round.

`election stop` accepts `--yes` like the other commands, though it never
prompts: stopping only makes a node bid less.

`node stop` also disables that node's election schedule, so a stopped node stops
bidding and leaves the sets after the current round. That is deliberate: after
`node start`, run `election start` again when you want it to resume bidding.

Each `node resources` report includes allocated workspace disk space (database,
logs, other and total). OOM warnings appear only when Docker reports an OOM
kill.

`election status` reports three separate things: whether the managed schedule
is installed, whether a cron daemon is actually running to execute it, and what
the chain says about this node under `chain`. An installed schedule alone proves
nothing about admission to a validator set, so `chain.validating` and
`chain.elected_for_next_round` are read from the node, not inferred.

## Inspect and move DePool funds

This section is about a DePool validator. A solo validator's stake is in
[Solo validator](#solo-validator-no-depool) instead.

```bash
sudo evernode depool info -n validator01
sudo evernode depool stake -n validator01 --amount 50000
sudo evernode depool withdraw -n validator01 --amount 1000
sudo evernode depool withdraw -n validator01 --cancel
sudo evernode depool withdraw -n validator01 --all
sudo evernode depool replenish -n validator01 --amount 20
```

`depool info` reads the DePool's own getters: its settings and proxies, every
round with its step, this node's participant stake, any pending withdrawal, and
any unsigned Safe transaction. Resolve unsigned transactions before an election
runs, because preparation deliberately stops while one is outstanding.

A multisig transaction holding fewer than its required signatures never
executes and blocks later DePool operations. That is the state an interrupted
election attempt leaves behind. Read it in `depool info` first — destination,
value and payload — then add this node's remaining signatures to that one
transaction:

```bash
sudo evernode depool confirm -n validator01 --transaction-id 7701911356660055489
```

The id is required and bulk signing is refused, because confirming a
transaction executes the transfer it carries. The id is matched exactly: an id
the wallet does not hold is refused by name, without sending anything, rather
than reported as a confirmation that did nothing. Signatures are added one at a
time and each is checked against the wallet's own record of which custodians
have signed, so the command ends either with the transfer executed or with a
statement of how many signatures the transaction still holds and which
custodians have yet to sign it elsewhere.

`depool stake` adds an ordinary stake from the Safe.

**Which round a stake works in.** A DePool runs four rounds at a time. One of
them is *collecting*: it takes new stakes, and at the next election it becomes
the round that bids while the next one starts collecting. So a stake bids in the
round that was collecting when you made it, and is then locked until that round
finishes.

**To validate every round, stake into every collecting round.** A single stake
bids once and is then locked, so the rounds that collect after it are empty and
the node sits out until that stake comes back. Adding a stake to each round as
it collects funds consecutive bids. Each of those rounds needs at least the
validator assurance to bid at all, so about twice the assurance is in play at
once. `depool info` shows which round is collecting and what each round holds.

A finished round's stake is paid back into whichever round is collecting at that
moment, on top of anything already there. That grows one round rather than
spreading across rounds, so reinvestment on its own does not cover the next
round.

If a round is elected with nothing in it, the election attempt says so by name:
it had nothing to bid with, and a retry cannot change that.

**Getting money back takes a round.** A DePool pays out only when the round
holding the stake finishes, so nothing moves immediately:

- `--amount N` asks for N tokens back. `depool info` shows the request as
  `own_pending_withdrawal_nanotokens`.
- `--all` asks for the **whole stake** back, not just the rewards. It works
  differently: rather than recording an amount, it switches reinvestment off,
  so when the round finishes the stake is paid out instead of rolled into the
  next round. `depool info` shows this as `own_reinvest: false` while the
  pending withdrawal stays `0` — that zero does not mean the request was lost.
- `--cancel` cancels either request and switches reinvestment back on.

Taking back the stake that backs this node's validator assurance ends its
validation.

`depool replenish` tops up the DePool's own operating balance from the Safe. A
DePool whose balance falls near its critical threshold stops operating, so
check `self_balance_tokens` in `depool info` before each round.

Each of these signs with every required local custodian key and holds the same
lock a scheduled election takes, so it cannot run beside an election attempt.

Each also waits for its own effect before reporting success: the stake present
in the DePool, the withdrawal recorded, the balance risen. Signing is not the
operation — the confirmation that executes a multisig transaction ends in an
internal message the DePool can still refuse — so a command that returns 0 has
seen the result on chain, and one that fails says which of "never reached the
wallet" and "still waiting for signatures" applies.

Every read and every send goes through the node's own console. There is no
remote-endpoint fallback, so a command refuses at once, naming the fix, when
the node is stopped or not yet synchronized, rather than appearing to work
against a chain that is not yours.

## Update a node's scripts after upgrading the CLI

Reinstalling the Python package does not replace the orchestration scripts
already saved in each node's workspace. After pulling a newer checkout, refresh
them from the node's recorded source:

```bash
sudo evernode node sync-scripts -n validator01 --dry-run
sudo evernode node sync-scripts -n validator01
sudo evernode node sync-scripts --all
```

The dry run lists exactly which files would be updated, added or removed. A real
run keeps a backup under the node's `.script-backups/<timestamp>/`, replaces
files in place so the node container's mounts stay valid, and waits for any
scheduled election run holding the node's election lock. Keys, the database,
elections state and logs are never touched.

`--all` evaluates each node separately: a node already current reports no
change, and only the ones that need files get them, each with its own backup.
That is also how a node created before a new script existed receives it — the
dry run lists it as an addition for that node alone.

For image maintenance, local database seeding, rollback, monitoring, and the
complete setup flow, read the [operator runbook](instruction.md).
`sudo evernode <group> --help` lists every command and flag.

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
