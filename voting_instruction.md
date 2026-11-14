# Voting on network config changes

Validators change the network's configuration by vote. They can burn the config
master key (the "dictator"), set a config param, or replace the config or
elector code. `evernode vote` does it with `evernode-vote`, the engine built
from this checkout's `vote/` directory. It reaches the chain only through a
GraphQL endpoint.

A change goes like this:

1. One validator registers it and pays its price: `propose`.
2. Every validator who supports it joins it, the proposer too: `join`. A node
   evernode does not manage joins with its `config.json`: see
   [Vote with a node evernode does not manage](#vote-with-a-node-evernode-does-not-manage).
3. From then on evernode votes for it in every round from cron, with no one
   watching, until it is decided. It stops by itself.

```bash
sudo -E evernode vote propose -n validator01 --pay-from-safe --burn   # pays; prints the hash
sudo -E evernode vote join -n validator01 --proposal HASH              # votes now and in every round
sudo -E evernode vote status                                           # elections, round end, joined proposals
sudo evernode vote history                                          # every proposal joined here, and its votes
sudo evernode vote leave --proposal HASH                            # stop early
```

`--pay-from-safe` pays from a solo or nominator node's Safe. A DePool node's Safe
is in the node's workchain, 0 by default, and the config contract takes payment
only from the masterchain: pay with `--from FILE` or `--pay-from ADDRESS`
instead (see [Paying](#paying)).

## Before you start

Build the engine once, and again whenever `evernode vote` asks for a newer one:

```bash
docker build -o /usr/local/bin /root/evernode_cli/vote
evernode-vote --version
```

Name the GraphQL endpoint. A URL with a project key belongs in the environment,
where other users of the server cannot see it:

```bash
export EVERNODE_ENDPOINT=https://mainnet.evercloud.dev/<project>/graphql
sudo -E evernode vote status -n validator01 --keys
```

Plain `sudo` drops the variable: every command below that reads the chain is
written with `sudo -E`. `history` and `leave` read only this host's store and
need no endpoint.

A cron daemon must run: `join` installs one line, `/etc/cron.d/evernode_vote`
(`evernode_vote_<8 hex digits>` for another `--state-dir`, so two state
directories never touch each other's line), and once a proposal is joined
`status` warns when no daemon runs or the line is missing. `evernode host setup`
installs and enables cron.

## Rounds and wins

A round is the time one validator set is current: about 18 hours on mainnet. A
round is won when validators holding more than 3/4 of that set's weight have
voted. A vote counts only in the round it was cast in.

| Change | Won rounds needed | Lost rounds allowed | Rounds at most |
|---|---|---|---|
| normal param | 2 | 2 | 4 |
| critical param (p10), the master key, code | 4 | 2 | 8 |

A proposal can be voted in the round it is registered in and in `max_tot_rounds`
(p11: 3 normal, 7 critical) more. The losses allowed decide first: a critical
change that loses three rounds is gone, whatever rounds are left.

`status` shows when the current round ends and where the elections are, from
the chain's params 15, 34 and 36:

```text
round       ends about 2026-11-08 20:48:12Z (in 17h43m), p34's planned end; exact once the next set is elected
elections   open 2026-11-08 11:42:04Z (in 8h37m), close 2026-11-08 18:31:40Z
```

Register a proposal early in a round. `propose` warns once the elections for
the next round have closed: a round the proposal does not win uses up one of its
allowed losses.

## Burn the master key

Burning replaces the master key with 32 bytes that are not a valid key, so no
signature can ever verify against it. After that the configuration changes only
by validator vote. The key is made from public text, so anyone can check that
nobody chose it to match a secret:

```bash
evernode vote burn-key                     # the key, and why nobody can sign with it
evernode vote propose --burn --dry-run     # the proposal's hash and price; sends nothing
```

One validator pays: see [Paying](#paying). Then everyone joins:

```bash
sudo -E evernode vote propose -n validator01 --pay-from-safe --burn
sudo -E evernode vote join -n validator01 --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

The burn proposal's hash is the same on every network and every machine, as long
as the label is the default one. A validator can recompute it rather than trust
a hash someone posted.

`join` shows the proposal and asks before it votes:

```text
proposal 25276f46…a934: appoint dictator ff01a3b8…a280 — burned (not a curve point: no signature can ever verify)
         master key: 946a21a0…52ff → ff01a3b8…a280 — burned (not a curve point: no signature can ever verify)
         round 1: wins 0/4, losses 0/2, 7 round(s) left; 0 validator(s) voted
         the round ends 2026-11-08 03:55:33Z (planned; exact once the next set is elected)
         earliest acceptance 2026-11-08 04:05:33Z
Vote for 25276f466b97 with vdp1, vdp2 now and in every round until it is decided? [y/N]
```

Once the burn applies, `status` shows the new key as `burned`, and messages
signed with the old key are rejected.

## Set a config param

Start from the param's current value:

```bash
evernode vote param 17
```

Copy its JSON into `p17.json` and edit it. evernode refuses JSON that ever-node
would read differently from how it is written. Its JSON reader would otherwise
ignore an unknown field, wrap a number too big for its field, or read a
number's `_dec` twin instead of its hex form, all without an error. So:

- write each field as `param` prints it, or leave a number's hex field out and
  edit only its `_dec` field;
- a field that would be read differently is named, and nothing is paid.

```bash
evernode vote propose --param 17 --value-json "$(cat p17.json)" --dry-run
```

`propose` and `join` list the change field by field:

```text
change      set p17 = {...}
            max_stake_factor: 196608 → 262144
            min_stake: 10000000000000 → 20000000000000
```

- By default the change applies only if the param still holds today's value when
  it is accepted. It cannot overwrite a newer change.
- Params ever-node has no JSON for take a BOC: `--value-boc @file`. `param`
  prints a set one as a BOC; a new value comes from whatever tool made it.
- Changes that can take control away from validators need `--force`: params 0,
  1, 8–12, 31, the validator sets (32, 34, 36; their JSON loses the ADNL
  addresses, so give them as a BOC) and code. Every address in p31 must be an
  account in the masterchain: one without stops the masterchain.

## Join someone else's proposal

```bash
evernode vote status                                            # open proposals: hash and what they change
sudo -E evernode vote join -n validator01 --proposal HASH       # one managed node
sudo -E evernode vote join --all --proposal HASH                # every managed node there is now
sudo -E evernode vote join --keyring /PATH/TO/node_cfg/config.json --proposal HASH   # a node evernode does not manage
```

`join` reads the proposal from the chain and shows what it changes before it
asks, so you never vote for a hash blindly. Whoever registered it, it gets the
checks `propose` makes: a value ever-node cannot read as written, a weak master
key and a removal are refused; what `propose` needs `--force` or
`--allow-usable-key` for is shown as a WARNING. It votes at once, then cron
votes in every round. Joining a proposal again replaces its nodes, key rings and
endpoint, and says which ones vote now. `--all` skips a node that has no key ring yet,
but a join with no key ring at all is refused: start the nodes first.

With managed nodes, voting goes only through `join`. `cast`, `run` and `burn`
refuse `-n` and `--all` and print the `join` command to use instead.

## What evernode does from cron

- **Every minute** cron runs `evernode vote tick`. It does nothing until a
  joined proposal's next pass is due, and needs no network until then.
- **A pass** reads the chain and votes with each of the joined nodes' keys that
  is in the current validator set and not counted yet. It follows each vote
  until the config counts it, and sends it again if it was lost.
- **The next pass** is a minute later while a vote is missing, otherwise at the
  next round's start. A round's voting starts 40 s after the new validator set
  takes over.
- **A round where none of the nodes holds a key** (not elected, or a DePool
  between rounds) has nothing to vote with. The schedule waits for the next one.
- **The end:** once the proposal is accepted, expires or loses too many rounds,
  the pass records how it ended, `applied` or `not applied`. The cron line goes
  once nothing joined is open.

It is safe to leave alone:

- one pass runs at a time per state directory (`--state-dir`); a pass votes
  for one proposal with the keys of every node joined to it, and a pass that
  hangs is killed after 10 minutes;
- a crash, a reboot or an endpoint outage only delays voting by a minute or so;
- an endpoint whose newest block is over 60 s old, or that serves an older
  config than an earlier pass read, is never acted on: the pass waits;
- it only votes: it never registers or pays.

Everything is kept in one root-only file,
`/var/lib/ever-validator/voting/proposals.json`: every proposal joined on this
host, open and ended, with every vote sent (round, node, validator key,
transaction, when sent and when counted). The endpoint, which may hold a
project key, is kept there while the proposal is open and removed when it
ends; errors never show it. Cron's output goes to `voting/cron.log` next to it.

```bash
sudo evernode vote history                        # every proposal joined here and how it ended
sudo evernode vote history --proposal HASH        # its votes, round by round
```

```text
26ac0704…a07c: set p151: unset → cell 8ec0537a…c3c9
    joined 2026-11-08 04:21:11Z, applied 2026-11-08 04:30:01Z; nodes vdp1, vdp2; 1 vote(s); last read open: round 2: wins 1/2, …
    round 2: vdp1 as validator #1 (926aa2bd673d): sent 2026-11-08 04:29:01Z, counted 2026-11-08 04:29:05Z in transaction 5f2b8e06…
```

Each vote is recorded the moment the config counts it, even if the pass or
evernode itself is then killed. A vote a pass sent but never saw counted is not
in the history; the chain has it. A vote is recorded as counted only from the
config's state: one that lands after the proposal expired, or after other votes
accepted it, is not. When a proposal ended is when a pass on this host saw it
end, up to a round after the config decided. While a joined node cannot be read
(its key ring is being rewritten, say), the next pass is at most a minute away.

## Paying

Only whoever registers a proposal pays. `propose` shows the change and its
price, runs it on the config contract's real code, then asks, or takes `--yes`.
It asks in the same run that pays, so what you confirm is what is sent. A
proposal someone already registered is not paid for again: `propose` says so,
and you join it by its hash.

| Option | Pays from |
|---|---|
| `-n NODE --pay-from-safe` | the node's Safe and its custodians' key files, under the node's locks |
| `--pay-from ADDRESS` | any masterchain wallet; you type its seed phrase first, then see the price and answer |
| `--from FILE` | a wallet file (below) |

The price is set by the config contract: storage rent for the proposal,
`(bit_price × (bits + 1024) + cell_price × (refs + 2)) × seconds`. It uses the
voting rules (p11) and the size of the new value, and is charged for the
minimum storage time unless you pass `--ttl`. Before anything is sent, the dry
run checks that the contract keeps exactly the price shown. `propose` attaches
the price plus 2 EVER, and the contract sends back everything above the price:

```text
registered: the config kept 2.056000000 EVER of the 4.056000000 EVER paid and sent back 2.000000000 EVER; the price is 2.056000000 EVER
```

On mainnet, on 2026-11-08: a burn costs 22.8 EVER, a critical param about 22
EVER, a normal one about 2.6 EVER. The paying wallet needs the price plus
3 EVER.

A payment is made once. It is followed to the config's answer. It is sent
again only if the wallet's own message expired without executing, which means
nothing was paid; that is decided from the endpoint's own blocks, never from
the clock alone, and a message whose fate is unknown is looked up before
anything new is sent. A wallet that holds too little for the transfer is
refused before anything is sent.

### Wallet files

A wallet file must be in the masterchain (`-1:`), hold the price plus 3 EVER,
be readable only by its owner, and never be committed.

A new EverWallet. Its first payment deploys it:

```bash
(umask 077; evernode vote mint-wallet --name payer > payer.json)
jq -r .address payer.json              # send the price plus 3 EVER here
sudo -E evernode vote propose --from payer.json --burn
```

```json
{
  "name": "payer",
  "type": "ever_wallet",
  "address": "-1:<64 hex digits>",
  "public": "<64 hex digits>",
  "secret": "<64 hex digits>"
}
```

`mint-wallet` also writes `balance` and `boc`, which only test networks use.

A multisig (SafeMultisig, SetcodeMultisig or SurfMultisig), with its
custodians' key files next to it:

```json
{
  "name": "payer-safe",
  "type": "multisig",
  "address": "-1:<64 hex digits>",
  "keys": ["custodian1.keys.json", "custodian2.keys.json"]
}
```

Each key file is `{"public": "<64 hex digits>", "secret": "<64 hex digits>"}`,
as ever-cli and evernode write them. With fewer keys than the multisig
requires, `propose` stops at `UNCONFIRMED`. Each other custodian then runs the
same command with their own key file, which confirms the pending transaction.

## Validator setups

Voting is the same for solo, nominator and DePool validators. The config
contract checks a vote only against the key listed for the validator in the
current set, and a DePool validator's node is listed with its own key. Votes
go as external messages: free, and no wallet is involved.

Paying differs. The config takes payment only from the masterchain:

| Setup | Its Safe | Paying |
|---|---|---|
| solo, nominator | masterchain | `--pay-from-safe` works; with several custodians only for a burn or a small param (at most 4 cells of payload), not for code or a large param |
| DePool | the node's workchain, 0 by default | `--pay-from-safe` is refused: pay with `--from FILE` or `--pay-from ADDRESS` |

A DePool validator who proposes therefore makes a payer wallet, as above, and
funds it from another wallet: evernode has no command that sends from a Safe.
Joining needs no wallet at all.

## Other commands

| Command | What it does |
|---|---|
| `status [-n NODE \| --all] [--keys]` | The master key, the voting rules, the validator set, the round end and elections, every open proposal; with sudo, the proposals joined here. `--keys` shows which of the nodes' keys are in the current set. |
| `param ID` | A param's current value as JSON, its hash, and how many rounds a change to it must win. |
| `simulate CHANGE` | Plays a proposal's whole life offline on the contract's real code and state, with stand-in validators. |
| `propose CHANGE` | Checks, dry-runs, asks, pays. `--dry-run` stops after the checks. `--ttl SECONDS` keeps it open longer, for more. |
| `join`, `leave`, `history` | Above. |
| `burn-key [--label TEXT]` | The key a burn installs. Default label: `it is a fight club baby`. |
| `mint-wallet --name NAME` | A new EverWallet as a wallet file. |

The change is one of `--burn`, `--dictator KEY` (an ordinary key needs
`--allow-usable-key`; a weak key is always refused), `--param N` with
`--value-json` or `--value-boc`, `--config-code BOC` or `--elector-code BOC`
(both need `--force`). A BOC is base64 text, or `@file`.

### Upgrade the config or elector code

Simulate first: the simulation shows whether the network keeps running on the
new code.

```bash
evernode vote simulate --config-code @new_config.boc --force
sudo -E evernode vote propose --from payer.json --config-code @new_config.boc --force
sudo -E evernode vote join -n validator01 --proposal HASH
```

Mainnet's elector is FunC: new elector code must keep the FunC layout and the
elector's storage, and have an upgrade hook (method 1666) that succeeds.

### Vote from another machine

Without a managed node, `cast`, `run` and `burn` vote in a terminal with keys
named by file. They need no root, and nothing is recorded:

```bash
(umask 077; sudo jq '{validator_key_ring}' /var/lib/ever-validator/nodes/validator01/node_cfg/config.json > keyring.json)   # on the server
evernode vote cast --keyring keyring.json --proposal HASH --watch                                                           # every round while it runs
```

`keyring.json` holds the validator's secret keys: `umask 077` keeps it readable
by you alone, and `cast`, `run` and `burn` refuse a key file that other users
can read.

The node adds a key at every election: copy `keyring.json` again after each
one. A running `--watch` reads it afresh at every check, so a copy made in the
middle of a round is used at once. Without `--watch`, `cast` votes once with
every key it can read and ends; if a key file cannot be read, it says which,
after voting with the others, and exits with an error.

- `cast` takes the proposal's hash. `run` and `burn` take the change itself
  (`--burn`, `--param N …`). The proposal has to be registered first, with
  `propose`: they stop with "nobody pays" otherwise.
- `--control console.json` lets the node sign over its control server instead,
  where that server can be reached. An evernode-managed node's listens only
  inside its container: vote for it with its key ring.

### Vote with a node evernode does not manage

Every ever-node keeps its validator keys in `config.json`, in the directory it
runs with: `pgrep -af ever-node` shows it as `--configs DIR`. Install evernode
on the node's server as the [README](README.md#install) shows; `evernode host
setup` is not needed. Without Docker, build the engine with Rust 1.90 or newer:
`cargo install --locked --root /usr/local --path /root/evernode_cli/vote`.

Then join with the node's `config.json`. Every pass reads it again, so each
election's new key votes with no copy to refresh:

```bash
sudo chmod 600 /home/val/Ever-Validator/node_cfg/config.json   # join refuses a key file other users can read
sudo -E evernode vote join --keyring /home/val/Ever-Validator/node_cfg/config.json --proposal HASH
```

For a node in Docker, name the file inside its container, as `docker cp` does:
`--keyring CONTAINER:/ever-node/node_cfg/config.json`. Every pass reads it with
`docker exec`.

To vote from another machine instead, copy the key ring there, check it, and
vote:

```bash
(umask 077; sudo jq '{validator_key_ring}' /home/val/Ever-Validator/node_cfg/config.json > keyring.json)
evernode vote status --keyring keyring.json --keys   # "keyring.json holds [N]": validator #N's current key is in it
evernode vote cast --keyring keyring.json --proposal HASH --watch
```

The node makes a new key at every election. When `status` shows `holds []`,
copy the file again.

## When it does not move

| You see | It means |
|---|---|
| `no key of the listed nodes is in this round's validator set` | the node was not elected this round; the next pass is at the next round's start |
| `the round started … ago: votes go out 40 s into it` | the new validator set is settling |
| `pass failed: the endpoint's newest block is … old` | the endpoint is behind; the next minute tries again |
| `did not land: another message took the seqno first` | votes from several validators raced for the config's message number; it is sent again, and a pass that still misses retries a minute later |
| `the endpoint serves an older config` | a stale replica; nothing is decided from it |
| `WARNING no cron daemon is running` | joined proposals get no votes until one runs |
| `WARNING /etc/cron.d/evernode_vote is missing` | joined proposals get no passes: join one of them again |
| `validator #N cannot vote: its p34 entry has tag 0x74` | that validator's entry is one the config contract cannot read; its key is never voted with |
| a round lost though you voted | the voters held 3/4 of the weight or less: others must vote too |
| `CANNOT WIN` | validators whose entries the contract cannot read hold too much weight |
| `UNCONFIRMED` | the paying multisig waits for other custodians |
| `not applied` | it expired, lost too many rounds, or the param changed meanwhile |
