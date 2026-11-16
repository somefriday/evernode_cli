# Network config votes

What evernode adds to the vote engine: which keys vote, which wallet pays, and a schedule that
votes in every round. The rules are [VOTE-1](properties.md#vote-1) to
[VOTE-5](properties.md#vote-5) and [SEC-4](properties.md#sec-4).

Two parts do the work.

| Part                                        | Does                                                              | Described in                         |
| ------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------ |
| The vote engine, `evernode-vote`            | Reads the config contract, checks and dry-runs every message, pays for a proposal, casts votes and follows each until it is counted. | [vote/README.md](../../vote/README.md) |
| evernode's `vote` group                     | Tells the engine where keys and wallets are, keeps joined proposals, and starts a pass from cron. | this file                            |

The operator's procedure is in [voting_instruction.md](../../voting_instruction.md). The engine
reaches the chain through a GraphQL endpoint: mainnet's public one,
`https://mainnet.evercloud.dev/graphql` (`arguments.DEFAULT_ENDPOINT`), unless the operator names
another with `--endpoint`. Nothing here uses a node's console.

## How evernode runs the engine

Code: `voting.handle_vote`, `voting.run_engine`.

**Requires**

- The engine in the version evernode names ([VOTE-5](properties.md#vote-5)).
- Root, only when a managed node's keys are used (`-n`, `--all`) or for the schedule commands.

**Logic**

1. Refuse the engine options evernode sets itself: `--net`, `--secret`, `--lock`
   (`voting.REFUSED_OPTIONS`).
2. `burn-key` and `mint-wallet` read no chain and no key. Pass them to the engine as they are.
3. `join`, `leave`, `history` and `tick` are evernode's own: see [The schedule](#the-schedule).
4. For every other command take evernode's own options and leave the rest for the engine.
5. Refuse `cast`, `run` and `burn` with `-n` or `--all`
   ([VOTE-1](properties.md#vote-1)).
6. Refuse a paying option with any command but `propose`, and more than one paying option
   ([VOTE-2](properties.md#vote-2)).
7. Write the *description*: the endpoint and the nodes that vote.
   - The endpoint is `--endpoint`, else mainnet's public one. An empty `--endpoint` is refused. A
     command that sends (`propose`, `cast`, `run`, `burn`) names the default before the engine
     starts.
   - Mainnet's public endpoint is refused for a managed node on another network
     (`voting.require_endpoint_network`).
   - A managed node is its key ring, `nodes/NAME/node_cfg/config.json`, read in place. The file
     must pass the private-path check ([SEC-1](properties.md#sec-1)).
   - `--all` passes over a removed node and a node with no key ring yet. If none is left, it is
     an error.
   - A `--keyring` or `--control` file must not be readable by other users.
8. Start the engine with `--net /dev/fd/N` and write the description into that pipe
   ([SEC-4](properties.md#sec-4)).
9. The engine prints to the operator's terminal. Its exit code is evernode's.

`vote status` run as root adds the proposals joined on this host.

## Propose and pay

Command: [`vote propose`](../vote/propose.md). Code: `voting.pay_and_propose`,
`voting.paid_propose`; in the engine, `propose` in `vote/src/campaign.rs`.

**Requires**

- One paying wallet, named in one of three ways.
- For `--pay-from-safe`: a node named with `-n`, whose Safe is in the masterchain
  (`voting.require_masterchain_safe`). The config contract takes payment from nowhere else, so a
  Safe in workchain 0 is refused.

**Logic**

1. With `--dry-run`, run the engine's dry run and stop. Nothing is paid and no lock is taken.
2. Describe the payer:
   - `--from FILE`: the engine reads the wallet file itself. The file and the key files it names
     must not be readable by other users.
   - `--pay-from ADDRESS`: ask for the wallet's seed phrase at a hidden prompt and put it in the
     description. A terminal is required.
   - `--pay-from-safe`: take the node lock. Put the Safe's address and every custodian's key
     file in the description. Pass the node's election lock to the engine as `--lock`.
3. The engine shows the change and its price and asks. `--yes` skips the question.
4. After the answer the engine takes the election lock
   ([LOCK-3](properties.md#lock-3)). If a run or a command holds it, nothing is paid.
5. The engine pays the price it showed, once, and follows the payment to the config's answer.

**Result**

The proposal is registered and the engine prints its hash. Voting for it is a separate step:
`vote join`.

**On failure**

The engine's own rules decide whether a payment may be sent again
([vote/README.md](../../vote/README.md#commands)). evernode keeps no record of a payment.

## The schedule

Commands: [`vote join`](../vote/join.md), [`vote leave`](../vote/leave.md),
[`vote history`](../vote/history.md), [`vote status`](../vote/status.md). Code:
`vote_schedule.py`.

A *joined* proposal is one this host votes for in every round until it is decided. The vote store
keeps each one ([state.md](state.md#vote-store)). Cron is only a clock: every minute it starts
`evernode vote tick`, which runs a *pass* for each open proposal that is due.

### Join

**Requires**

- Root and the proposal's hash.
- The keys that vote: `-n NAME`, `--all`, or `--keyring` for a node evernode does not manage. A
  key ring may be a file on the host or `CONTAINER:/PATH` in a running container. It must not be
  readable by other users.
- `evernode` on root's `PATH`: cron starts it.

**Logic**

1. If the proposal is already joined for managed nodes on another network, refuse. One host
   keeps one schedule per proposal hash.
2. Take the endpoint: `--endpoint`, else the one the open proposal was joined with, else
   mainnet's public one, which is named. Refuse mainnet's for a managed node on another network.
3. Run a pass with the engine's `--dry-run`. It shows the proposal and sends nothing. A proposal
   that is not open is refused.
4. Ask.
5. Take the vote lock ([VOTE-3](properties.md#vote-3)).
6. Install the cron file. A join that cannot schedule its passes records nothing.
7. Add the proposal to the store as `open` and due now, with its nodes, key rings and endpoint.
   A proposal joined before keeps its votes and gets the new nodes, key rings and endpoint.
8. Run one pass at once.
9. Remove the cron file if no proposal is open any more.

**Result**

The node's keys have voted in the current round, and cron votes in each later round.

### One pass

Code: `vote_schedule.tick`, `vote_schedule.run_due_pass`, `vote_schedule.run_pass`.

**Logic**

1. `vote tick` takes the vote lock. If it is held, the tick ends without output.
2. For each open proposal whose next pass is due:
   1. Find every joined key ring again. A managed node's is read in place. One in a container is
      copied into a private temporary file in `voting/` for the pass. One that cannot be read is
      noted. If none can be read, the pass fails.
   2. Write the description: the stored endpoint, the key rings, and what earlier passes read
      about the proposal (`known`), which lets the engine recognise a stale endpoint and the
      proposal's end.
   3. Run `evernode-vote follow`. Its progress and its result line go to `voting/cron.log`.
      evernode adds a line of its own only for a key ring it could not read and for each
      `--keyring` file that holds no key of the round (`keyless`), with the engine's stamp,
      `[HH:MM:SSZ]` in UTC.
   4. Save each vote the moment the engine reports it counted
      ([VOTE-4](properties.md#vote-4)).
   5. Kill a pass that runs 600 s.
3. A pass that ended with a report:
   - the proposal is still open: store the progress and the time of the next pass, which the
     engine names. If a key ring could not be read, the next pass is at most 60 s away;
   - the proposal ended: store its state, `applied` or `not applied`, and remove its endpoint.
4. A pass that failed: keep the votes it reported, store the error, and leave the proposal due.
   The next minute tries again.
5. Remove the cron file when no proposal is open.

What a pass checks, when it votes and when it calls the next pass are the engine's rules:
[vote/README.md](../../vote/README.md#commands), `follow`.

### Leave, history, status

- `vote leave` takes the vote lock, marks the open proposal `left`, removes its endpoint, and
  removes the cron file if no proposal is open. Votes already sent stay on chain.
- `vote history` prints every proposal joined on this host, open and ended, with each vote sent.
- `vote status`, as root, adds the open joined proposals to the engine's report. It warns when
  the cron file is missing or no cron daemon runs. First, under the vote lock, it reads each open
  proposal with a dry-run pass through the endpoint stored with it (never the one `status` was
  given), with no key ring (`vote_schedule.check_open`). One the pass finds decided ends as at a
  pass: its state, its endpoint removed, the cron file removed when none is open. Any other gets
  the report's progress; `last_pass` and `next_pass_at` stay. A failed read changes nothing and
  is shown. While a pass holds the lock, nothing is read.

### The cron file

`/etc/cron.d/evernode_vote` for the default state directory. Another state directory gets
`evernode_vote_` and 8 hex digits of its path's hash, so two directories never replace each
other's file. The file exists only while a proposal is open. evernode refuses to replace or remove
a file that does not start with `# Managed by evernode`.

## Votes and elections on one server

- A pass signs with the node's validator keys, not with the Safe. It takes no node lock and no
  election lock, and a scheduled election run does not wait for it.
- Only `vote propose --pay-from-safe` uses the Safe. It holds the node lock while the engine
  works and the election lock while it pays ([LOCK-2](properties.md#lock-2)).
