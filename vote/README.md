# evernode-vote

Changes Everscale's network configuration by validator vote. First of all it **burns the config master key**
(the "dictator"), so that from then on only validators can change the configuration. The same machinery
votes on **any config param** and on **config or elector code upgrades**.

Every proposal is checked against the config contract's rules and ever-node's own param parser. It is
dry-run on the contract's real code under ever-node's own executor before anything is paid for or sent, and
every vote is followed until the config counts it. A proposal's whole life can be played out offline
(`simulate`) against the live contract state.

The tool reaches the chain through a GraphQL endpoint. A validator of any type (single node, nominator,
DePool) votes with nothing else but that endpoint and its node's key.

It is the engine of evernode's `vote` group (see [evernode vote](#evernode-vote) and the runbook's §8),
which describes managed nodes and their Safes to it. On its own it works from a net file. It was developed
as `burn-dictator`: the test runs bd-a to bd-e below ran under that name, with `vote` for `cast` and `net/`
for `testnet/`.

## How the contract decides

Everscale's config contract (`-1:5555…5555`) is TON's FunC config contract (`res/ton-config-code.fc`; the
April 2020 source Everscale forked differs only in a getter). Its storage is `cfg_dict seqno public_key
vote_dict`, and `public_key` is the dictator. An external message signed with it can set any param, replace
the config or elector code, or appoint another key.

Validators can do the same by vote:

1. **Proposal.** An internal message from a masterchain account:
   `0x6e565052 query_id ttl ^[cfg_proposal#f3 param_id value:^Cell if_hash_equal:(Maybe uint256)] critical:Bool`,
   carrying the storage price `(bit_price·(bits+1024) + cell_price·(refs+2)) · ttl`. The bits and refs are
   counted over the value's distinct cells. The contract keeps exactly the price and returns the rest.
2. **Votes.** Each validator signs `"VotE" idx proposal_hash` with its key in the *current* p34. Any
   masterchain account delivers it: `0x566f7465 query_id signature "VotE" idx proposal_hash`. A round is won
   when the voters' weight exceeds 3/4 of the set's weight. The voter list resets when p34 changes.
3. **Acceptance.** After `min_wins` won rounds (p11; critical params from p10 use `critical_params`) within
   `max_tot_rounds`, and with at most `max_losses` lost rounds, the value is stored as param `param_id`.
   `-999` also sets the dictator key, `-1000` the config code, and `-1001` forwards `upgrade_code` to the
   elector.

On the template network the rules are: normal params need 2 won rounds; critical params need 4; both allow 2
lost rounds. A proposal starts with `max_tot_rounds` (3 normal, 7 critical) rounds left after the one it is
registered in, so it can be voted in at most 4 or 8 rounds.

### What to burn the key to

ever-vm checks signatures with ed25519-dalek 2's non-strict `verify`, which accepts small-order keys.
**Key 0** is what a `-999` proposal without a value would set, and it is such a point: a forged signature
`R = identity, s = 0` passes for about every fourth message. `emulate::tests::zero_dictator_key_is_forgeable_on_the_contract`
gets a forged dictator command **accepted by the real config contract under ever-vm**. Burning to 0 hands
the key to everybody.

The tool burns to a 32-byte string that **is not a curve point**: no signature can ever verify. On this
network the old key's messages then fail with exit 12 (a VM fatal error, because `CapsTvmBugfixes2022` is
off); with that capability on they fail with exit 34. The key is derived from a public label, so anyone can
check that nobody chose it:

```
$ evernode-vote burn-key
key:     ff01a3b8bd7503002aaf6f3f00bf2bddd2ae690bd401b360bf549d7b0c1ba280  (sha256(label ":" 0), not a point)
```

## What a vote needs

| Step | Needs |
|---|---|
| read the chain, send messages, follow them to their transactions | a GraphQL endpoint (`endpoint`) |
| vote signature | the validator's current key: read from the node's key ring (`keyring`), or the node signs over its control server (`control`; the key never leaves it) |
| vote delivery | nothing: an external message signed by the validator alone (the default); or a masterchain wallet (`relay`) |
| proposal | a masterchain wallet of any kind with the price + 2 EVER (`payer`) |

Nothing has to run on a validator's host. Validator keys change every election, so every validator signs
in every round: automatic with `control` and with a key ring read on the host; a copied key ring must be
refreshed after each election. On a network of independent operators, each runs `cast --watch` with their
own keys. The votes add up on chain.

## Install

With evernode, `sudo evernode host setup` builds it from the copy of this
directory the evernode package carries. On its own:

```sh
cargo install --locked --path .            # Rust 1.90 or newer
docker build -o /usr/local/bin .           # or: a static Linux binary, with no Rust on the host
```

## Commands

```sh
cargo build --release
B=target/release/evernode-vote

$B burn-key                                # the burn key and why it is unusable
$B status   --net NET [--keys]             # dictator, voting rules, p34 (and who holds which key), proposals by round
$B param    --net NET 17                   # a param as ever-node's JSON, its hash, mandatory/critical, rounds needed
$B simulate --net NET CHANGE               # the proposal's whole life offline, on the live contract state
$B propose  --net NET CHANGE [--dry-run] [--lock FILE]   # check, dry-run and register
$B cast     --net NET CHANGE|--proposal H [--watch]   # vote with every current key the listed nodes hold
$B run      --net NET CHANGE [--yes]       # vote every round until it takes effect; registers it first if nobody has
$B burn     --net NET                      # run --burn
$B follow   --net NET --proposal H [--dry-run]   # one pass for evernode's schedule: JSON lines on stdout
$B dictator-ping --net NET [--keys K] [--expect accepted|rejected]   # does a key still command the config? (or: not delivered)
$B dictator-set  --net NET --param N --value-json|--value-boc   # while a dictator exists: set a param directly
$B wrap-code     --net NET [--elector]     # code that behaves like the current config code, with another hash
$B boc-hash      --boc BOC                 # a value's hash, as if_hash_equal and code hashes compare it
$B mint-wallet   --name payer              # a new EverWallet file: fund its address, its first message deploys it
```

`CHANGE` is one of:
- `--burn [--label L]`
- `--dictator KEY [--allow-usable-key]`
- `--param N --value-json JSON|--value-boc BOC`. The JSON is ever-node's zerostate format
  (`{"p17": {...}}` or the value alone); start from `param` output. `--if-hash auto|current|none|HASH`
  defaults to the current value's hash.
- `--config-code BOC`
- `--elector-code BOC [--elector-data BOC]`

Changes to p0, p1, p8–p12, p32, p34, p36 and the code upgrades need `--force`. A JSON value must read back
exactly as written: ever-node's reader ignores unknown fields, prefers a number's `_dec` twin and wraps
numbers that do not fit, so the value it builds is written back with its own serializer and compared, field
by field. A number's hex twin may be left out. `propose` and `status` show a change field by field.

**Every vote is followed until the config counts it.** An external vote's own transaction, or the config's
transaction for the internal message a relay sent, must be found through the endpoint and succeed. A vote
is sent only if its dry run shows the config counting it (the validator among the round's voters) or
accepting the proposal with it: the config takes an external vote's seqno before it counts the vote, so a
vote that would count nothing is not sent. Then the config's state after the vote's transaction must list
the validator among the round's voters, or the proposal must be gone with a vote the dry run said accepts it,
landed before the proposal's expiry. Each vote prints that transaction's hash. A vote that is not counted is
signed and sent again at the next pass; once another message took the seqno, a vote is called lost only if
the config does not count its validator. `cast` without `--watch` ends once every key held is counted in the
current round, or the round is won without them. It fails, after voting with the rest, if a listed node
could not be asked for its keys, and it stops early if the contract refuses a vote in its dry run. Keys whose
p34 entry the contract cannot read are never voted with: the round line names them. `propose --dry-run`
exits 1 if the config would not register the proposal.

**A payment is made once.** The dry run checks that the config would keep exactly the price shown, and
`propose` asks before it pays, in the same run (`--yes` pays without asking; with no terminal and no
`--yes` it refuses), so the value confirmed is the value sent. `--lock FILE` locks that file (flock) after
the answer and before the payment, until the run ends; while someone else holds it, nothing is paid. A payment sent again because its message
expired unexecuted is not asked about again while the value is the one confirmed. A proposal already registered is not paid
for: `propose` says so and points to `--proposal`. A wallet message that may have reached the network,
because something failed after it was handed to the endpoint, is looked up before anything new is sent. It
counts as unexecuted only once the endpoint holds a block made after its expiry (plus 20 s for indexing),
never by the wall clock alone: a lagging endpoint must not make a late payment look lost. The internal
message that pays is followed to the config's answer; the answer line shows what the config kept, measured
as the value paid minus the value sent back. A wallet message is also refused if its dry run drops the
transfer: the wallets send with "ignore errors", so a transfer the balance does not cover would otherwise
"succeed" and send nothing.

**`follow`** is evernode's schedule's pass: one read of the chain, a vote with every key the listed nodes
hold that the config has not counted in the current round (each followed until counted, tried again after
a lost seqno race), and a report. Progress goes to stderr; stdout carries JSON lines: `{"vote": …}` for each
vote of ours the moment it is known counted, then the report (state `open`, `applied` or `not applied`,
progress, the next pass time). It keeps nothing: evernode hands back `known` (param, target, the config
version last read open) in the net description. With it, a missing proposal is closed; without it,
`follow` needs the proposal registered. A config older than `known`'s version is a stale endpoint and is
never acted on, and after voting a pass decides from the config's state that includes its own vote, never
from another read. A proposal voted on by its hash gets `propose`'s checks first. An elector upgrade that is
gone but not yet in the elector's code is read again for up to two minutes: the elector applies it in its
own transaction, after the config's. The result line starts with `CANNOT WIN` when the validators whose
entries the contract reads hold no more than 3/4 of the weight, and names any key held here that cannot
vote.

Campaigns print one line per change of the round state and keep running through transient errors. Options:
- `--from WALLET`: the wallet file that pays for the proposal (default: the net file's `payer`)
- `--relay WALLET`: carry votes as internal messages through this wallet. Without it, and without `relay` in
  the net file, votes go as external messages.
- `--external`: external messages even if the net file sets a relay
- `--settle 40`: wait after a new validator set starts before voting (see the test results)
- `--timeout`: off by default; on mainnet a critical change takes 4+ rounds of ~18 h

### The net file

```json
{
  "endpoint": "https://mainnet.evercloud.dev/<project>/graphql",
  "payer": "wallets/payer.json",
  "nodes": [
    {"name": "validator01", "keyring": "/var/lib/ever-validator/nodes/validator01/node_cfg/config.json"},
    {"name": "node2", "control": "keys/console-node2.json", "address": "127.0.0.1:3032"}
  ]
}
```

Paths are relative to the file. The file can be a pipe (`--net /dev/fd/N`), as evernode passes it.
- `endpoint`: the chain. Every command but `elect` needs it. Keep a URL that holds a project key out of shared
  places (`runs/` is git-ignored).
- `nodes`: the validators this operator votes for, by where their keys are. `keyring` is the node's config
  file; `control` is a `console.json` or its `config` section, and `address` overrides its server address.
- `payer` pays for proposals. `relay`, if set, carries votes as internal messages. Each is a wallet file's
  path, or the wallet file's content in its place.
- **A wallet that stakes should not carry votes.** Replay protection rejects a message once a newer one from
  the same wallet has executed, so two senders on one wallet lose messages.
- `dictator`, and a node's `wallet` and `exec`, exist only for the test network.

Wallet files:

```json
{"name": "payer", "type": "ever_wallet", "address": "-1:…", "public": "…", "secret": "…"}
{"name": "validator01", "type": "multisig", "address": "-1:…",
 "keys": ["/var/lib/ever-validator/nodes/validator01/keys/validator01_1.keys.json"]}
{"name": "surf", "address": "-1:…", "phrases": ["word1 … word12"]}
```

- `ever_wallet`: one key. `mint-wallet --name payer` makes a new one; send its address some EVER, and its
  first message deploys it.
- `multisig`: SafeMultisig, SetcodeMultisig or SurfMultisig (same transfer functions), with the custodian
  keys held on this machine: key files `{"public", "secret"}`, as ever-cli and evernode write them.
- `phrases`: seed phrases, from which the keys are derived as ever-cli and ever-sdk do: BIP39 (English, 12
  to 24 words, checksum checked), BIP32 on secp256k1 along `m/44'/396'/0'/0/0`, and that private key as the
  ed25519 secret. Checked against ever-cli 0.44 (`wallet::tests`).
- Without `type`, a wallet is the EverWallet of its key if the address is that wallet's, and a multisig
  otherwise.

## GraphQL

The tool reaches the chain only through a GraphQL endpoint: evercloud's public API, or q-server in front of
a local network (the ever-gql project).
- **Reads.** Account states (`blockchain.account.info.boc`). Every campaign pass first checks that the
  endpoint's newest masterchain block is less than 60 s old.
- **Sends.** `postRequests` with the message's hash, body and expiry. A dropped connection is retried; a
  message is identified by its hash, so a copy is harmless.
- **Follow-up.** `blockchain.transactions_by_in_msg` gives the transaction a message caused, with its exit
  code and the internal messages it sent (`out_messages { hash dst }`). A message with no transaction by its
  expiry (plus 20 s for indexing) did not land. A wallet sends its next message only after the previous
  one's transaction. A relayed vote is followed one step further, by the hash of the message the relay
  sent, into the config's own transaction (within 60 s).
- **Counted.** After a vote's transaction, the config's account state that includes it must list the
  validator among the round's voters.

Node control servers still sign (`control`). The test network's staking loop reads and sends through them,
so that the network does not depend on the endpoint.

**The local endpoint and evercloud are the same interface for this tool** (checked 2026-11-02: q-server
0.68.0 locally, 0.66.1 at evercloud):
- **Schema.** Every field, argument and input type the tool uses has the same type on both.
- **Answers.** The tool's queries, sent verbatim to both, come back in the same shapes: `lt` as `0x` hex,
  addresses as `wc:hex`, account BOCs in standard base64, `boc: null` for a missing account, `[]` for an
  unknown message hash, `dst: ""` for an outbound external message.
- **Mainnet run.** The current client ran `status` and `simulate --burn` there, read-only. Sending was
  tested locally only.
- **Behind the interface they differ.** Locally, sent messages go through Kafka to one feed node; if that
  node loses its Kafka consumer, posts are still accepted but never delivered (bd-e). At evercloud, heavy
  scans can fail with `INTERNAL_SERVER_ERROR`; the tool only looks things up by address or hash.

What mainnet looked like (read on 2026-11-02):
- **Same contract.** The config contract's code hash is `bac24be4…`, the same as the test network's, so
  everything verified here applies. Seqno 75, no open proposals.
- **The dictator key is live.** `ca53f93f…` is an ordinary ed25519 key.
- **Same rules.** Critical changes (including `-999`) need 4 won rounds, with 2 lost ones allowed, within at
  most 8 rounds; p11 is not critical.
  Rounds last 65,536 s (about 18 h), so a burn takes 3–4 days of voting.
- **Every validator can vote.** All 39 validators have `#53/#73` entries.
- **No signature id.** p8 does not set `CapSignatureWithId`, so votes are signed without a network id. The
  tool refuses a network whose p8 sets it: it signs and dry-runs without an id.
- **The burn simulates cleanly.** `simulate --burn --validators 39` on the live config state registers
  it for 22.8 EVER, accepts it in round 4, and the contract keeps tick-tocking.
- **Who stakes.** In the last day 48 addresses staked: 46 DePool proxies (code `c05938cd…`), 1 SafeMultisig
  (`80d6c47c…`, the code in `res/SafeMultisigWallet.tvc`), 1 EverWallet (`3ba6528a…`).
- **A different elector.** Mainnet runs a FunC elector in TON's layout (code `3f9e354f…`): its root is
  the FunC method table, as in mainnet's config. The test network runs the TON Labs Solidity port, so
  its elector findings do not carry over (see the audit). `status` reads neither elector: the round and
  the elections come from params 15, 34 and 36.

Nothing has been sent to mainnet.

## Who signs what

A vote has two parts.

**The vote itself is the same for every validator.** It is signed once, by the validator's key in p34:
the node's key, created by its `election-bid`. No wallet, owner or custodian key is involved, and no
multisig count applies.

```
internal:  0x566f7465 query_id:64 signature:512 | "VotE" idx:16 proposal:256     (signed part after |)
external:  signature:512 | "Vote" seqno:32 valid_until:32 idx:16 proposal:256
```

An internal vote signs nothing that changes between rounds except the key, so it can be signed early and
delivered by anyone. An external vote includes the contract's seqno, so it must be signed just before
sending.

**The carrier is whatever reaches the config.** A proposal must be paid for by a masterchain account. An
internal vote must come from a masterchain account. An external vote needs no account, but it names the
contract's seqno: only one external message can take each seqno, and a vote that loses the race is signed
again with the next one.

| Setup | Votes | Pays for a proposal |
|---|---|---|
| any validator | an external message signed by its key (the default) | |
| EverWallet (masterchain) | carries them (`relay`) | yes; a new one is deployed by its first message |
| SafeMultisig / SetcodeMultisig / SurfMultisig, 1 custodian (masterchain) | carries them | yes (`sendTransaction`) |
| the same, k of n custodians | carries them if k keys are here | yes: one custodian submits, the others confirm. Each runs the same command with their own keys, and it confirms the pending transaction. At most 4 cells of payload: a burn or a small param, not code |
| DePool validator | its wallet cannot: it is in workchain 0, and its stake proxy has no key | a masterchain EverWallet, funded from its own wallet |
| nominator | its hot wallet (a masterchain Safe) can; the nominator contract only through its cold owner | the hot wallet |

So the vote's signature never depends on the setup, and external votes need no wallet at all. The setup
only decides who can pay for the proposal, which one validator does once.

## evernode vote

evernode's `vote` group (`../evernode/voting.py`) runs this binary for its operator commands. It writes the
net description into a pipe (`--net /dev/fd/N`): the endpoint, absolute paths, and wallets in place, so that
a seed phrase or an endpoint's project key never shows in an argument or the engine's environment
(`EVERNODE_ENDPOINT` is not passed on), and the engine's errors never name the endpoint's URL. A joined
proposal's endpoint is kept in the root-only vote store while the proposal is open, and dropped when it
ends. `--endpoint` and `--yes` are evernode's own (`--yes` is passed on to a paying `propose`); every other
option passes through unchanged; `--net` and `--secret` are refused.

- **`-n NAME`, `--all`** (root): the node's key ring, `/var/lib/ever-validator/nodes/<node>/node_cfg/config.json`,
  read in place after evernode's checks (no symlink, root's, mode 0600). ever-node rewrites it at every
  election and keeps its mode, so it is always current. The control server listens on `127.0.0.1:5888`
  inside the node's container and is not published, so `control` cannot reach evernode nodes.
- **`--pay-from-safe`** (`propose`, with `-n`): the node's Safe, its address in `keys/<node>.addr` and
  every custodian's `<node>_<i>.keys.json`. evernode holds the node's operation lock while `propose` shows
  the price, asks and pays, and passes the node's election lock as `--lock`: it is taken after the answer,
  so a question left open holds up no election run, and a run holding it means nothing is paid. A solo or nominator validator's Safe is
  in the masterchain and can pay; a DePool validator's is in workchain 0 and is refused.
- **`--pay-from ADDRESS`** (`propose`): the wallet's seed phrase, typed at a hidden prompt first, goes into
  the pipe as `phrases`; then `propose` shows the price and asks before it pays.
- **`--keyring FILE`, `--control FILE`**: plain mode, from any machine, without root.
- **`join`, `leave`, `history`, `tick`** (`../evernode/vote_schedule.py`): evernode's store,
  `voting/proposals.json`, keeps every proposal joined on the host and every vote sent; cron's minute runs
  `follow` for each joined proposal whose next pass is due. With `-n` and `--all`, `cast`, `run` and `burn`
  are refused: managed nodes vote through `join`. Every payment goes through `propose`, which asks.
- evernode needs this binary's exact version (`ENGINE_VERSION` in `voting.py`; a test ties it to
  `Cargo.toml`).

## Checks before anything is sent

| Check | Why (verified in `emulate.rs` on the deployed code) |
|---|---|
| value parses with ever-node's `ConfigParamEnum` for that param and serializes back to the same cell | the network must read exactly what was voted for; unknown params get a warning |
| no removal (null value) | it registers and its price is paid, but every vote that would accept it fails with exit 7 (`idict_set_ref` with null). It can never be accepted |
| mandatory params (p9) carry `if_hash_equal` | without it the contract refuses to register (`0xcd506e6c`), whatever the value |
| `if_hash_equal` equals the current value's hash | otherwise registration fails (`0xe2646356`). If the value changes during the vote, the proposal is accepted **without effect** (reported as REMOVED) |
| critical flag from p10 | a critical param proposed as normal is refused (`0xc3726954`) |
| depth < 128, at most 1024 distinct cells | the contract refuses deeper values and cannot price bigger ones |
| dictator key is not weak, and not usable without `--allow-usable-key` | see above |
| dry run of the registration and of every vote | wrong key or index (exit 34), index outside p34 (exit 7), a p34 entry the contract cannot read (41) are never sent; a vote is sent only if the dry run's state counts it or the proposal is accepted with it, so an unknown or expired proposal (−1), an index already counted (−2) and a removal's winning vote (exit 7 after the seqno is taken) are not |
| dry run of every wallet message on the wallet's own code and state | a key that is not the wallet's or a custodian's (100), a confirmation already given (103), a payload too big for a multisig (−14), replay (52) and a transfer the balance does not cover (dropped by the action phase) are never sent |
| a proposal voted on by hash (`join`, `cast --proposal`) | `propose`'s checks: a value ever-node cannot read as written, a weak key and a removal are refused; what `propose` needs `--force` or `--allow-usable-key` for is a WARNING |
| payer balance | price + 2 EVER + fees |
| payer and relay in the masterchain | the config ignores anything else; checked before the campaign starts |

## Audit

### Contract behaviour and network risks
- **Burning to key 0 leaves the config open to anyone.** Verified on the deployed contract. The tool
  refuses weak keys.
- **Validators whose p34 entry has tag `0x93` (since-seqno) or `0x74` (BLS key) cannot vote.** The contract
  reads only `#53/#73` and their votes fail with exit 41. If such validators hold 1/4 of the weight or
  more, no round can be won, and only the dictator could fix it.
  `status` warns; campaigns and passes print `CANNOT WIN`, and never vote with such a key: they name it.
  **Check this on mainnet before burning.**
- **After the burn, validators can still appoint a new dictator** (`-999`, 4 critical rounds). That is
  inherent in the contract.
- **p11 (the voting rules) is not critical on this network**, so a normal 2-round vote could first lower
  the critical requirements.
- **The elector has no owner key.** `upgrade_code` is accepted only from the config address, in TON's
  FunC elector (`elector-code.fc`; mainnet's has the same layout) and in the Solidity port
  (`ton-labs-contracts/solidity/elector/Elector.sol`, the test network). After the burn, elector upgrades
  need a `-1001` vote.
- **Elector upgrades by vote work, if the new code has the elector's own layout.** On an accepted `-1001`
  the config sends `upgrade_code(code, data)` to the elector. The elector checks that the sender is the
  config, sets the new code and calls the new code's upgrade hook; if the hook throws, everything is
  rolled back. Validators voting on new elector code have to review its hook: one that does not throw but
  rewrites storage badly would be installed. Each elector looks for the hook in its own layout, so code in
  the other layout fails with exit 11:
  - **Mainnet's FunC elector** calls method 1666 of the new code's root method table, only if data
    follows the code (per `elector-code.fc`, a value holding only the code is installed unchecked;
    `evernode-vote` always sends a data cell). `simulate` on mainnet's state: the elector's own code with
    a NOP appended (`wrap-code --elector`) is installed, and the test network's Solidity elector code is
    refused (exit 11).
  - **The test network's Solidity elector** installs the new code's first reference (its function
    dictionary) as c3 and calls function 2. Equivalent Solidity-shaped code (`wrap-code --elector`) is
    installed with storage intact (`elector_upgrade_by_vote_keeps_its_storage`), and so it was live on
    bd-e (S17), where elections went on under the new code. bd-c's S17 refusal came from my FunC-shaped
    test code (exit 11). Its hook is not the published source's one-shot FunC → Solidity migration
    (`require(hash == 0xcb6eb3…)`): re-installing its own code succeeds.
- **Accepted values stay in the config.** Param `-999` (and `-1000`/`-1001` after an upgrade) is stored in
  the config dict; ever-node keeps running with it (live, more than an hour).
- **Rounds are counted lazily.** A round is booked at the next vote or random tick-tock scan, once,
  however many sets went by. `RoundView` reproduces this and matches the contract at every step of the
  tests.
- **A vote can be replayed by anyone.** The signature covers only `(idx, proposal_hash)`, so a later round
  in which the validator has the same key at the same index counts it again. Once keys rotate, a replay
  fails with exit 34 (`vote_replay_needs_the_same_key_at_the_same_index`). Elections rotate keys here, but
  a validator that reuses its key and keeps its index carries its vote forward.
- **Config code upgrades work by vote.** `-1000` replaces the config code (the `throw(0)` after `set_code`
  still applies it). Verified with code that delegates to the old code (`wrap-code`): after the upgrade
  the contract keeps tick-tocking, registering, counting votes and obeying the dictator, in emulation and
  live (S14).
- **The template's config code is byte-identical to Tycho's `cli/res/config_code.boc`** (hash `bac24be4…`).
  An "upgrade" to Tycho's config code is therefore a no-op, and `simulate` now says so.
- **Votes need no wallet.** The contract also takes votes as external messages
  (`sig "Vote" seqno valid_until idx phash`), so a validator can vote holding nothing but its key (the
  default without a relay; live in S13 and on bd-d). External votes share the seqno with the dictator, so
  only one lands per seqno.
- **A multisig with several custodians cannot carry a code upgrade.** SafeMultisig's `submitTransaction`
  measures the payload before it accepts the message, on the 10,000 gas an external message gets. From 5
  cells on it runs out of gas (exit −14) before its own 8-cell limit (121). A burn (3 cells) and small
  params fit; code upgrades and large params need an EverWallet or a single-custodian multisig, whose
  `sendTransaction` does not measure the payload. Verified on the real code
  (`several_custodians_carry_at_most_four_cells_and_no_stranger`).

### Defects found and fixed
- **"Never registered" was conflated with "gone".** `burn` reported a failed registration as "rejected".
  Campaigns now track both states.
- **`propose` re-registered a change already in effect.** Every campaign now checks the change first and
  answers "nothing to do".
- **Key-ring answers were cached across elections.** A node's negative answer stuck for the rest of the
  campaign. Answers are now cached per validator set and per node, failures are retried, and the key ring
  is read fresh.
- **`exportpub` treated any control-server error as "key not held"**, silently skipping votes. Only the
  node's `key not found` counts now.
- **The price was wrong for values with shared subcells.** Refs are now counted per distinct cell, as
  `CDATASIZE` counts them. Exact to the nanotoken (`price_matches_the_contract`).
- **All reads and sends went through the first node only.** They failed over across every node with a
  control key, until the GraphQL endpoint replaced nodes for both.
- **Expired or discarded proposals were still voted on.** The campaign now stops with the reason.
- **One failed node query ended a campaign.** Passes now retry transient errors; only contract-level
  outcomes end the campaign.
- **The default timeout was 6 h.** Mainnet needs days. It now runs until the outcome.
- **Unknown p34 descriptor tags made `status` fail.** All four tags ever-block writes are now parsed, each
  marked votable or not.
- **Votes were lost when sent right after a validator-set switch.** REMP starts new sessions a few seconds
  after `utime_since`. Campaigns now wait 40 s after a switch (`--settle`) and resend uncounted votes
  after 60 s.
- **ever-node's bundled `bin/Elector.abi.json` does not match the deployed elector** (the deployed
  `Member` has 5 fields, the ABI 7). Only `m_election_open` and `elect_at` are read.
- **A removed proposal was reported without a reason.** It now says expired, lost too many rounds, or
  accepted (without effect, when the value had changed or the elector refused), from the state last seen.
  One reason is given only when that state proves it (the same validator set, before the expiry); after a
  set switch or the expiry, both possibilities are named: other validators may have won the last round after
  the read.
- **Registering or voting at a set switch wasted a round.** Campaigns now keep out of a 20 s window around
  an expected switch; an overdue set does not block them.
- **The test network's staking helper could bid twice in an election, or give a lost bid up** (see the
  incidents); it now resends the same bid.
- **A wallet the config ignores was retried forever.** A payer or relay outside the masterchain is now
  refused before the campaign starts.
- **Wallet messages were ordered by a guess.** The relay check read EverWallet's stored timestamp, which
  other wallets do not keep in the same place. Every message is now followed to its transaction by hash,
  so a wallet's next message waits for the previous one, whatever the wallet.
- **A basechain stall stopped campaigns.** The freshness check read q-server's `info.blocksLatency`, which
  times the masterchain block that holds the newest basechain block. Everything the tool reads is in the
  masterchain, so the check now times the newest masterchain block.
- **A vote was done once it was sent.** A relayed vote was followed only into the relay's own transaction,
  never into the config's, and no vote was checked against the round's voters. A single `vote` (now `cast`
  without `--watch`) returned after its first pass even when its votes had failed, and exited 0. Every vote
  is now followed into the config's transaction and checked on the state after it; `cast` ends when every
  key held is counted, and a vote that is not counted is sent again at the next pass, so `--resend` is gone.
- **A node never elected broke `join --all`.** Its `config.json` holds `"validator_key_ring": null`, which
  failed to parse, so every pass reported a problem and ran again a minute later. It is now a node with no key.
- **`propose`'s dry run offered to register a change already in effect**, and evernode then asked to pay. It
  now answers "nothing to do" before the dry run.
- **A payer short of funds was retried for 400 s.** It now stops at once and says what to send.
- **The `rules` line priced the minimum storage time**, not `--ttl`, while the dry run priced `--ttl`.
- **An overdue round was looked at again every 10 minutes on any network.** It is now a tenth of a round,
  from 1 to 10 minutes, so on a test network's 10-minute rounds a set elected late is voted from its start.
- **At a due switch the late warning said "(0s) … proposing at the start of the next round avoids that".**
  It now says the next set is due and that a minute's wait avoids losing the round.

### Incidents during this audit
1. **bd-a stalled.** A staking-helper check that I added judged "the stake never left the wallet" from
   the wallet balance. The `recover_stake` refund landing at the same moment fooled it, and all three test
   validators bid a second time in one election with new keys. The elector seated six validators.
   ever-node uses one key per node from a set (`get_validator_key`: `find_map`), so only half the weight
   was online, and `bd-a` stalled for good at masterchain block 1336. Its burn results below had been
   recorded before that.
2. **bd-c double seats.** The replacement check (the wallet's stored last-message time against the bid's
   time) re-bid when a bid had not executed within 60 s. Two bids that were merely slow landed after the
   re-bid, and `bd-c` ran one round with two double seats at 70 % online weight (see its notes).
3. **bd-e elections stuck.** The fix for both, never bidding again, failed the other way. A storage stall
   on the Docker host lost node3's bid. With `min_validators` 3 of 3 the elector kept the election open,
   so validator sets, and with them voting rounds, stopped changing for 50 minutes.

The first two re-bid with a new key; the third gave a lost bid up. **The staking helper now bids with one
key per election:** a bid whose message expired unexecuted (plus 60 s) goes out again as the same signed
request, and the elector merges a second copy into the same seat. It also skips elections the node already
holds a key for, and uses its own wallet (votes go through separate relays).

## Code structure

| Module | Role |
|---|---|
| `config.rs` | the contract model: storage, proposals, `RoundView` (lazy round accounting), p9/p10/p11/p34, message bodies, answer codes |
| `proposal.rs` | `Change` kinds, the checks above, value JSON/BOC via ever-node's parser, "is it in effect" |
| `emulate.rs` | `Sandbox`: the config contract under ever-node's executor (dry runs, `simulate`); wallet dry runs and getters run locally; the contract tests |
| `graphql.rs` | the chain: account states, `postRequests`, the transaction a message caused, and the one a message it sent caused |
| `campaign.rs` | one loop for every change: propose once after a dry run, vote each round until each vote is counted, stop at the outcome |
| `follow.rs` | one pass for evernode's schedule: vote what is missing in this round, report as JSON, say when the next pass is due |
| `timeline.rs` | when the current round ends and where the elections are, from p15, p34 and p36 |
| `wallet/` | wallets that pay and carry: wallet files, inline wallets and seed phrases (`mod.rs`), `ever_wallet.rs` (one key, deployed by its first message; zerostate accounts), `multisig.rs` (the SafeMultisig family: submit and confirm across custodians) |
| `node.rs`, `control.rs` | the net file, signers (key ring or control server), the native control client |
| `report.rs` | `status`, plans, `simulate`, `dictator-ping`, `dictator-set` |
| `burn.rs`, `elector.rs`, `stake.rs` | the burn key, the open election, test-network staking |

To add a kind of change, add a `Change` variant with its `value()`, `describe()`, checks in `plan()` and
`in_effect()`. Registration, dry runs, voting, `simulate` and `status` then work for it unchanged. To add a
kind of wallet, add a `Kind` with its transfer; the rest of the tool only calls `Wallet::transfer`.

## Local test network

`testnet/gen.sh` makes 3 ever-node validators in Docker. It uses image `ever-node-local:e406862` (ever-node
`e4068621` unchanged) and the template in `testnet/template` (ever-node's `tests/test_run_net`). The network
has 300 s rounds, minted EverWallets (validators, a giver, `--relays N` vote relays), and a dictator key in
`keys/dictator.json`. It runs as compose project `evernode-vote` on `172.33.0.0/24`, with control servers
on `127.0.0.1:13031–13033` (fixed ports: one such network at a time). Runs go to `runs/` (git-ignored: they
hold wallet keys). Commands below run in `vote/`.

GraphQL comes from the ever-gql project (`../../ever-gql`): kafka, ArangoDB and q-server, fed by a full node
that joins this network's bridge. `net.json` points at its endpoint, `http://127.0.0.1:4000/graphql`
(`gen.sh --endpoint` for another).

```sh
testnet/gen.sh --out runs/bd-b
docker compose --env-file runs/bd-b/compose.env -f testnet/compose.yml up -d
for i in 1 2 3; do nohup $B elect --net runs/bd-b/net.json --node node$i >>runs/bd-b/elect-node$i.log 2>&1 & done
# GraphQL for this network; --purge first if the stack served another zerostate
(cd ../../ever-gql && scripts/up.sh && \
  scripts/attach.sh --run "$OLDPWD/runs/bd-b" --network evernode-vote_net --ip 172.33.0.20)
$B dictator-ping --net runs/bd-b/net.json --expect accepted
$B burn --net runs/bd-b/net.json
$B dictator-ping --net runs/bd-b/net.json --expect rejected
```

Stop it with `pkill -f "evernode-vote elect --net runs/bd-b"` and
`docker compose --env-file runs/bd-b/compose.env -f testnet/compose.yml down -v` (`-v` deletes the chain).

**When GraphQL stops moving, or posted messages expire unexecuted,** a Kafka hiccup has cost the sink or
the feed node its consumer group (`scripts/status.sh` shows the lag in blocks). Restart the sink, attach the
feed node again (same identity and data) and send a test transfer:

```sh
(cd ../../ever-gql && docker compose -f compose.yml restart sink && scripts/detach.sh && \
  scripts/attach.sh --run "$OLDPWD/runs/bd-b" --network evernode-vote_net --ip 172.33.0.20 && \
  scripts/smoke.sh --run "$OLDPWD/runs/bd-b" --send)
```

**Restarting from zerostate** follows the migration project's procedure (`docs/ever-node.md` §8). Wallets
and the dictator key keep their addresses and secrets, with balances reset. Node identities are always
new, because a validator key is bound to one zerostate's elections. The GraphQL stack holds one chain's
data, so it is purged and attached again.

```sh
pkill -f "evernode-vote elect --net runs/bd-b"
docker compose --env-file runs/bd-b/compose.env -f testnet/compose.yml down -v
(cd ../../ever-gql && scripts/detach.sh --purge && scripts/down.sh --purge && scripts/up.sh)
testnet/gen.sh --out runs/bd-c --keys-from runs/bd-b --relays 12
docker compose --env-file runs/bd-c/compose.env -f testnet/compose.yml up -d
(cd ../../ever-gql && scripts/attach.sh --run "$OLDPWD/runs/bd-c" --network evernode-vote_net --ip 172.33.0.20)
$B elect --net runs/bd-c/net.json --node node1 --stake 270000 &   # unequal weights for the scenarios
for i in 2 3; do $B elect --net runs/bd-c/net.json --node node$i & done
testnet/scenarios.sh runs/bd-c                                    # every voting scenario, ~65 min
```

`cargo test --release` runs 58 tests. 29 of them execute contract code under ever-vm: the template's config
contract (and the elector, for its upgrade), SafeMultisig (`res/SafeMultisigWallet.tvc`: deploy, getters,
submit, confirm, its limits, a transfer it drops) and EverWallet (deployed by its first message). Two more
derive keys from seed phrases (ever-cli's results) and read inline wallets.

### Test runs

**bd-a** (2026-11-01), the original burn:
- **Before:** the dictator key was accepted (seqno 0 → 1).
- **Burn:** 4 won rounds and accepted after 1,120 s. Votes were signed through the control server (nodes
  1, 2) and from a key ring (node 3).
- **After:** the old key was rejected. The network kept validating for more than an hour with param
  `-999` set.
- **End:** the staking-helper incident above.

**bd-b** (2026-11-02), after the audit changes. Three concurrent campaigns with separate relays:
- **p13** (normal: complaint deposit 100 → 200 EVER): registered for 2.108 EVER. Votes were relayed by
  `relay-2`. Applied after 2 rounds (00:08:30 → 00:18:38).
- **p17** (critical and mandatory: `min_stake` 1,000 → 2,000 EVER): `if_hash_equal` was set
  automatically, and the proposal registered for 22.2 EVER. Votes were relayed by the giver. Applied
  after 4 rounds (→ 00:28:45).
- **Burn**: registered for 22.8 EVER. Votes were relayed by `relay-1`. Applied after 4 rounds (→ 00:28:45).
- **Before / after**: the dictator ping was accepted before (seqno 0 → 1). After the burn it was rejected:
  exit 12 in the dry run, seqno unchanged on chain.
- **Network after the changes**: the next set switch went through. Each node bid exactly once in each of
  the four elections, under the new `min_stake` as well.
- **Lost votes**: in rounds 2–4 the first vote sent 21–28 s after the switch was lost and resent after
  60 s. This led to the 40 s settle and to sending every message through all nodes.

**bd-c** (2026-11-02), restarted from zerostate with `--keys-from runs/bd-b` (same wallets and dictator
key, new node identities). Every voting scenario, driven by `net/scenarios.sh`. Node1 staked 270k, the
others 100k, so round 0 weighs 1/1/1 and later rounds 57.4/21.3/21.3 %. Operator net files hold some of
the nodes: A = node1, B = node2+3, AB = node1+2.

| # | Scenario | Result |
|---|---|---|
| S01 | the test dictator key commands the config before any vote | PASS |
| S21 | a mandatory param without `if_hash_equal` is refused before anything is paid | PASS |
| S02 | normal param (p13): the config keeps exactly the price (10.000 → 12.108 EVER for 2.108), then 2 won rounds apply it (`propose`, then `vote --watch`) | PASS |
| S22 | registering an already registered proposal pays nothing again | PASS |
| S04 | multi-operator: a coordinator registers; A votes by the change, B only by the proposal's hash; neither holds 3/4 alone, together they apply it | PASS |
| S07 | the dictator changes p104 while a proposal for it is being voted on (`dictator-set`): accepted without effect | PASS |
| S08 | two proposals for one param: the first accepted applies, the other is accepted without effect | PASS |
| S19 | `run` for a change already in effect: nothing to do, nothing paid | PASS |
| S15 | weights, not counts: AB loses round 0 (2 of 3 equal validators, 66.7 %) and wins rounds 1–2 (78.7 %): applied with 1 loss | PASS |
| S05 | A alone loses 2 rounds (33.3 %, 57.4 %), then all validators win 2: applied with 2 losses | PASS |
| S06 | A alone loses 3 rounds: discarded | PASS |
| S03 | critical + mandatory param (p17 `min_stake` → 2,000 EVER), `if_hash_equal` automatic: 4 won rounds | PASS |
| S09 | burn: 4 won critical rounds; the old key is rejected afterwards | PASS |
| S11 | p11 changed by a *normal* vote (it is not critical): critical changes then need 2 wins, normal proposals may live 60 s | PASS |
| S10 | validators appoint a new dictator after the burn (`--dictator KEY --allow-usable-key`); its key then commands the config | PASS |
| S18 | a proposal not won within its ttl (90 s) expires | first run: the contract deleted it on time, but the tool only said "REMOVED", so the check failed. Fixed (removals now name their reason) and re-run as S18b: PASS, "REMOVED without effect: expired" |
| S12 | burn again: the re-appointed dictator is burned too; its key is rejected afterwards | PASS |
| S17 | elector code upgrade with FunC-shaped test code: accepted and forwarded; the elector refuses it (no Solidity function dictionary, exit 11) and keeps its code `883a642b…` | PASS (as expected then; see the audit for why, and for the upgrade that works) |
| S14 | config code upgrade (`wrap-code`): the config runs the new code `63a1030a…`, and the network keeps producing blocks on it | PASS (re-run after the driver stopped) |
| S13 | external votes (no wallet, seqno 5–10), on the upgraded config code, restore p11 | PASS |
| final | the dictator is burned, no proposal is left, every node validates | PASS |

All 27 checks in `scenarios/summary.log` passed: 25 on the first attempt; S14 on its re-run (the driver
had skipped its setup) and S18 as S18b (after the reporting fix).

Covered only in emulation (`cargo test`), on the template's contract code, because a live network cannot
produce them or the tool refuses to send them:
- validators with BLS (`0x74`) or since-seqno (`0x93`) entries cannot vote (exit 41)
- a "remove param" proposal is never accepted (exit 7 at every winning vote)
- votes with a foreign key (exit 34), an index outside p34 (exit 7), for an unknown proposal (−1), twice
  in one round (−2), or after expiry (−1, and the vote deletes it)
- a vote replayed in a later round counts only for the same key at the same index
- an external vote with a stale seqno is refused (exit 33)
- key 0 accepts forged dictator commands, the burn key none
- the contract's refusals of registrations without `if_hash_equal`, with the wrong critical flag, or with
  too little value

Notes on the run:
- **18 % of votes needed a resend.** Of 111 votes (validator × round × proposal), 20 were not counted
  within 60 s of their first delivery. All were counted after the resend. External votes needed none,
  because each waits until the contract's seqno moves.
  **Cause:** each campaign sent its three votes through one relay wallet, 2 s apart. One block can take a
  wallet's messages in any order. When a later vote was executed first, the wallet's replay protection
  rejected the earlier one (exit 52, e.g. `ccf185cb…` in masterchain block 964, where the next vote
  `f278dacf…` had just executed). A rarer second cause was messages waiting past their 60 s expiry
  (exit 57, in bursts). Other 52/57 rejections in the logs are harmless duplicate copies (every message
  goes through all nodes). **Fix:** a relay sends its next vote only after the wallet has executed the
  previous one, and wallet messages live 120 s. In a check run, S23 sent three votes through one relay
  and all were counted in the first pass with no rejection.
- **Wasted loss at the round boundary.** S10 and S18 were registered at the exact second round 6
  began. The config still showed round 5's set, so each started with one lost round. Campaigns now
  register and vote only outside a 20 s window around an expected set switch (a set that outlives its
  term does not block them). Built at 02:20, used by S12, S14, S17, S18b and S13.
- **Driver stopped mid-run (my mistake).** I edited `scenarios.sh` while it was running. Bash reads a
  script as it goes, so it resumed at a stale offset, skipped the `wrap-code` lines and stopped at an
  unset variable. S17 and S12-after were then checked by hand, and S14, S13 and the final checks were
  re-run by a continuation script. The script now runs inside `main()`, so bash reads all of it first.
- **Second staking incident.** The re-bid guard from `bd-a`'s fix re-bid when a wallet had not executed
  a bid within 60 s. In election `…7399` node2's and node3's original bids were just slow, so both bids
  landed and each node got two seats in a 5-seat set. 70 % of the weight was online, barely above
  BFT's 2/3, so the chain kept going. I stopped the loops seconds before the next re-check. The staking
  helper now **never bids twice in an election**: a timeout cannot tell lost from slow, and a missed round
  costs far less than a dark seat. It also skips an election the node already holds a key for (its
  config's `validator_keys`), so restarts are safe. The next sets had 3 seats again.

**bd-d** (2026-11-02), the first network with GraphQL: a fresh zerostate (`--keys-from runs/bd-c`) with the
ever-gql stack attached (feed node at `172.33.0.20`). Every read and send went through the endpoint.
- **The endpoint itself** (ever-gql's `smoke.sh --send`): blocks 4 s behind the node, ever-sdk query and
  subscription, the zerostate's giver, and a transfer posted with `postRequests` that executed. ever-cli
  0.44 deployed three SafeMultisig wallets through it.
- **dictator-ping:** accepted, and its transaction was found by the message's hash 6 s later.

| # | Setup | Result |
|---|---|---|
| L1 | p120: an EverWallet pays; only external votes (seqno 1–3, 14 s for all three) | APPLIED after 2 rounds |
| L2 | p121: a 1-of-1 SafeMultisig pays and carries every vote | APPLIED |
| L3 | p122: a 2-of-3 SafeMultisig with two of the keys here: submitted, confirmed, sent in one run | APPLIED |
| L4 | p123: the same multisig, custodians on separate machines. Custodian a's run submits and ends UNCONFIRMED (1 of 2); a second run sends nothing; custodian c's run finds the pending transaction and confirms it | registered, then accepted |
| L5 | p124: a DePool validator's operator. Its basechain Safe is refused as payer and as relay. A new EverWallet, funded from that Safe with ever-cli, is deployed by its first message and pays. node3 votes with nothing but the endpoint and its key ring; node1 and node2 vote by hash from another operator's net file | APPLIED |

Findings, all fixed:
- **Lost seqno races were slow.** Independent campaigns' external votes race for the seqno. A vote that
  lost waited out its validity (60 s, plus 20 s for indexing) before it was signed again. It is now given up
  as soon as the contract's seqno moves on.
- **A payer or relay outside the masterchain was retried forever.** It is now refused before the campaign
  starts.
- **q-server closes idle keep-alive connections**, and the first request on one fails. Requests are retried.

**bd-e** (2026-11-02), every voting scenario again, now through GraphQL only: a fresh zerostate
(`--keys-from runs/bd-d --relays 12`), the ever-gql stack purged and attached again, `net/scenarios.sh`
unchanged except for its endpoint check. 26 of 27 checks passed on the first attempt, from S01 to the final
health check. The burn (S09), the second burn (S12), the new dictator (S10), the config code upgrade (S14)
and the external votes on the upgraded code (S13) all went through the endpoint. The run sent 107 votes
and 17 proposals, with no retried GraphQL error.

The 27th check, S17, was out of date. Since bd-c, `wrap-code --elector` builds Solidity-shaped code, which
the elector accepts, while the check still expected bd-c's refusal. The upgrade was **applied live**. The
elector runs `5dfcee02…` (the wrapped code; before: `883a642b…`), an election opened under it 40 s later,
every node staked in it, and the sets it elected took over. This is the first live elector upgrade by vote.
S17's check now expects it.

One vote (S04a) was reported as "sent nothing" although its relay had sent it. The endpoint stores a
transaction and the messages it sent separately, and the transaction can arrive first. For a multisig this
could have led to a second submission, so a transaction now counts only once every message it sent is
there. Checked again on bd-e with the final build:
- a 2-of-3 SafeMultisig paid for p140 (submitted, confirmed by a second custodian, registered);
- a 1-of-1 SafeMultisig carried its six votes, and p140 was applied;
- an EverWallet relay carried the six votes for p142, which was applied too.

Left running, bd-e went through **two storage stalls on the Docker host** (12:40–12:52 and 13:42; the host
runs other projects' networks too):
- **GraphQL.** Kafka lost its partition leaders, and both of its consumers dropped out. The sink stopped
  writing, so GraphQL froze at 12:41:40 while the chain went on. The feed node stopped forwarding, so
  posted messages were accepted and lost. Restarting the sink caught up 876 blocks in 10 s; attaching the
  feed node again delivered the posts that were still valid.
- **Validators.** ever-node exited with code 0 on node1 (57 % of the weight) and later on node2, both
  after slow storage. Each restarted, and the chain paused for a few minutes.
- **Staking (incident 3).** node3's bid was lost, so the election could not close. The fixed helper sent
  node3's signed request again; the elector confirmed it, the next set took over 23 s later, and the
  elections after it ran normally.
- **Freshness.** After the second stall the basechain stood still for 8 minutes while the masterchain ran.
  q-server's `blocksLatency` showed 395 s, which is why the check now reads the masterchain.
- **W4** (p143, after the fixes): registered, won both rounds through a relay, applied. One vote reached
  the validators right after a set switch but was first tried 2 minutes later, past its expiry (exit 57);
  the tool sent it again.

**bd-f** (2026-11-04), the first network built from evernode_cli's `vote/testnet/`, run by this engine as
`evernode-vote`: `cast` instead of `vote`, and every vote followed until the config counts it. Relayed
votes printed the config's own transaction for each vote (`counted, 3 validator(s) voted in this round, the
round is won (transaction 5a20fa36…)`). evernode's `vote` group, live:
- **Plain mode** (macOS, an ordinary user, no node): `status --keyring … --control … --keys` found each
  validator's key in a key ring copy and two control servers. `propose --pay-from ADDRESS` ran in a
  terminal: the dry run showed the price, the question was answered, and the seed phrase was typed at a
  hidden prompt. The wallet, relay-13, holds the key of the 12-word BIP39 test phrase; it was recognized as
  an EverWallet by its address and paid. `cast` then sent three external votes (seqno 2–4), each counted,
  and ended `VOTED: 3 vote(s) counted in this round`.
- **`-n`** (a root Linux container with this checkout, the static binary from `vote/Dockerfile`, and an
  evernode state directory: `node.json` checked by evernode, node1's key ring, the address and three key
  files of a 2-of-3 SafeMultisig deployed on the network): `status -n --keys` found validator #0.
  `propose -n --pay-from-safe --yes` paid from the Safe under the node's locks: one custodian submitted, a
  second confirmed, the proposal registered. `cast -n` sent validator #0's vote, counted; `cast --all`
  found nothing left to vote.
- **A real managed node.** On ever-gql's feed node, which evernode from this branch had just created,
  `vote status -n gqlfeed` passed every check of its key ring (`node_cfg/` 0700, `config.json` 0600,
  root's) and stopped at the missing engine with the build hint.

bd-f's scenario run was cut short by the endpoint. 19 s after the first validator-set switch the sink
stopped writing and the feed node stopped delivering posts: GraphQL froze at masterchain block 145 while
the nodes went on. A Docker build of the engine was running on the same Docker host. Votes posted in that
window expired; the engine reported each and sent it again, and the freshness check refused the stale
state. S02, S04, S07, S08 and S19 missed their deadlines. Restarting the sink and attaching the feed node
again restored the endpoint, and the run was repeated on bd-g with nothing else running.

**bd-g** (2026-11-04), every voting scenario on a fresh zerostate (`--keys-from runs/bd-f --relays 12`),
with nothing else running on the Docker host: **27 of 27 checks passed on the first attempt**, from S01 to
the final health check, with no GraphQL stall. The run sent 95 votes, 90 carried by relays and 5 as
external messages (S13), and every one was reported counted, with the config's transaction. Four relayed
votes, all sent in one moment of round 0, expired undelivered; each was reported, sent again at the next
pass and counted.

**bd-i** (2026-11-08), unattended voting with DePool validators that evernode itself set up. bd-g's chain
and its GraphQL data were left in place: bd-i runs as compose project `evernode-vote-i` on `172.36.0.0/24`,
with its own GraphQL stack (`docker compose -p ever-gql-i`) and feed node `gqlfeedi` at `172.36.0.20`.
Three solo validators stake 20,000 EVER each through `elect`. Two DePool validators, `vdp1` (`172.36.0.21`)
and `vdp2` (`.22`), were made by evernode from this branch, through its e2e harness
(`tests/e2e/readme_workflows.py`: `node create`, `wallet create/deploy`, `depool prepare/deploy`,
`stake-initial`) in a manager container `evernode-vote-manager-i`. They stake 50,000 EVER each through their
DePools (`readme_election.py` runs evernode's election runner each election). With 62.5% of the weight, a
round in which the DePools are elected is won only with both DePools' votes; they were elected every other
round. Their votes went out from cron, as `evernode vote tick`.

| Proposal | Who voted | Outcome |
|---|---|---|
| a burn with another label, joined by the DePools only | DePools' votes counted, 62.5% < 75% | discarded after 3 lost rounds: `not applied` |
| p14 (critical), DePools left after round 1 and joined again | round lost while they were out; won when they voted | applied with 4 wins and 1 loss |
| p20 (normal), vdp1 joined a round late | round lost with vdp2 alone (68.75%) | applied |
| the burn, `it is a fight club baby` (`25276f46…`, the same hash as on mainnet) | won rounds where the DePools' votes decided | applied: the old key's messages are rejected |
| p151 (normal), the pass killed right after vdp1's vote | vdp2's vote, posted just before the kill, closed it | applied; vdp1's vote kept in the history from its JSON line |

Seen on the way: the first switch to a set with the new DePool validators stalled the chain for 125 s
(ever-node's validation countdown, half a catchain lifetime, for nodes that did not start from the
zerostate; they held over a third of the weight). After every switch to a set with the DePools, this
GraphQL stack fell 1.5 to 2.5 minutes behind; each pass refused it (`the endpoint's newest block is … old`)
and voted a minute or two later, inside the round. The checks before paying were seen live: a hex-only edit
refused, `--from FILE` cancelled without `--yes` after its dry run, a DePool Safe refused at once, and
`registered: the config kept 2.056000000 EVER, the price, and sent back 2.000000000 EVER`.

**bd-j** (2026-11-08), every scenario of `voting_instruction.md`, run from the guide and `--help` alone
(`runs/bd-j/scenarios.md`): compose project `evernode-vote-j` on `172.37.0.0/24`, GraphQL stack `ever-gql-j`,
feed node `gqlfeedj`, 600 s rounds. Three DePool validators made by evernode through its e2e harness on two
servers, `bdj-srv-a` (`vdp4`, `dp1`) and `bdj-srv-b` (`dp2`), and a `bdj-laptop` with no managed node. The
DePools' 100k to 150k bids made most sets DePool-only; with node1 and node2 (20k) in a set they still held
over 75%, so a round was won only when all three voted. Eight proposals ran, up to four at once: the burn
(applied after a key change by vote), a burn that lost, two conflicting p20 changes, a critical p14, a p151
BOC value, a leave and join again, and a change after the burn. They were paid by an EverWallet file, a
2-of-2 multisig file and a seed phrase, and voted by `join -n`, `join --all`, `cast --keyring --watch`,
`cast --control` (node1) and `burn --keyring`. Two elections failed for want of three bids (DePool stakes
cycle too slowly for 10-minute rounds); the overdue rounds were reported as such. The fixes are listed above.
