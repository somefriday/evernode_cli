# Denominator pools

A denominator validator stakes through one or two TON nominator-pool contracts that stakers deposit
into. Code: `evernode/denominator.py`, the kit's `scripts/denominator_cycle.sh` and the pool tools
in `evernode/vendor/nominator-pool/tools/cells.cjs`, run in the image `assets/pool-tools.Dockerfile`
builds.

## The contract facts the logic relies on

- The code is upstream's pool v1, unchanged; `PINNED.json` names its commit and code hash
  `9a3ec14b…`, which the pinned compiler reproduces. The image build fails on any other hash.
- The pool's data fixes its validator wallet, reward share, staker limit and both minimums for
  life. No operation changes them, and none changes the code.
- A pool holds one stake at a time: state 0 idle, 1 bid sent, 2 staked. A pair bids in alternate
  elections; each pool's address differs only by `stake_at` in its initial data, which the first
  bid overwrites.
- Stakers send the comments `d` (deposit, 1-token fee) and `w` (withdraw everything) from
  basechain wallets. A `w` while staked is queued; the pool refuses to bid while any is queued.
- Taking the stake back needs two validator-set changes counted by op 6 and then `stake_held_for`
  + 60 s, or three changes. Anyone may send op 6, the recovery request and the payout of queued
  withdrawals (op 2).
- The pool pays every staker in the transaction that takes the stake back. At 40 stakers that
  uses about 190,000 of the masterchain's 1,000,000 gas; from about 177 it fails and the whole
  stake is lost. evernode never prepares or accepts a limit above 40.

## What every denominator command does first

Code: `denominator.handle_action`.

1. Take the node lock ([LOCK-1](properties.md#lock-1)).
2. Require a role and the denominator type.
3. Require a synchronized node ([NODE-1](properties.md#node-1)), except for `prepare` and `link`,
   which read nothing from the chain.
4. Ask for confirmation, except for `info`, `stakers`, `link` and `verify`.
5. Take the election lock for `prepare`, `deploy`, `deposit`, `withdraw` and `retire`
   ([LOCK-2](properties.md#lock-2)).
6. Run the command. Save the node record only if it succeeds.

## Prepare, deploy and verify

[denominator prepare](../denominator/prepare.md), [denominator deploy](../denominator/deploy.md),
[denominator verify](../denominator/verify.md): `denominator.prepare`, `deploy`, `verify`.

**Requires**: a denominator node whose Safe is in the masterchain (`wallet-deployed` or
`imported`). Deploy and verify need a synchronized node.

**Logic**:

1. Prepare builds the tools image, tagged by a fingerprint of its sources, and runs
   `PREPARE_PROGRAM` in it. That program compiles, compares with the pinned hash, then builds for
   each pool its StateInit, its address and a one-shot deployer with its trigger.
   - **Other addresses:** it refuses addresses that differ from deployed pools' unless
     `--new-contract`.
   - **A pool about to be dropped (`_check_dropped_pools`):** prepare reads it with the recorded
     tools image. It reads the account, its stake at the elector (the larger of the elector's credit
     and the stake sent), its stakers and queue, and the Safe's unsigned transactions to it.
   - **Asking:** a pool that holds anything is dropped only after a yes or `--force`. `--yes` does
     not answer, and with no terminal prepare refuses.
   - **An unreadable pool:** it blocks the drop at `denominator-deployed` and later, unless
     `--force`. Before that it is listed as not checked.
   - **The question comes before the election lock.** Only moving the files and rewriting `env.sh`
     hold the lock.
   - **The stage:** new pools that are not deployed yet set the stage back to
     `denominator-prepared`.
2. Deploy funds each missing pool's deployer from the Safe (`scripts.fund_nominator_deployer`) and
   sends the trigger through the console, three times at most, until the pool is active. The
   deployer creates the pool with a top-up (op 1) and deletes itself.
3. Verify reads each pool through `getaccountstate` and the tool's `account`, and compares the
   code hash and every term with the manifest.

**Result**: `config["denominator"]["pools"]`, `DENOMINATOR_ADDRS` and `DENOMINATOR_TOOLS_IMAGE` in
the settings and `env.sh`; stages `denominator-prepared`, `-deployed`, `-verified`.

**On failure**: a refused prepare leaves the previous files and records, apart from a tools image it
built. A deploy that stops after paying is run again; a paid deployer is not paid again.

## The validator's funds

[denominator deposit](../denominator/deposit.md), [denominator withdraw](../denominator/withdraw.md):
`denominator.deposit`, `withdraw`, through `denominator_cycle.sh send`.

**Requires**: stage `denominator-verified` or later. A withdrawal needs an idle pool and at most
the pool's balance less its stakers' funds and its 10-token reserve.

**Logic**: the cycle script builds the body (op 4, or op 5 with the amount), submits it from the
Safe with `Submit_And_Identify` and signs it through with `Complete_And_Await`. A deposit is
confirmed when the Safe's balance falls. A withdrawal is confirmed when the pool's validator funds
fall by the amount (`Effect_Pool_Validator_Funds_At_Most`): the Safe gets the amount back, so its
balance rises.

**Result**: the pool's validator funds change. A deposit moves the stage to `ready-for-elections`
once every pool holds the minimum, at least 101 tokens without config 40.

## The pools' cycle

[election start](../election/start.md), [election stop](../election/stop.md),
[stake recover](../stake/recover.md): `elections._denominator_sequence_program`,
`denominator.start_bidding`, `stop_bidding`, `maintain`; `denominator_cycle.sh cycle`.

**Requires**: stage `ready-for-elections` and the tools image.

**Logic**: every scheduled run, bidding or not:

1. For each pool with a stake out: send op 6 when config 34's `utime_since` is later than the
   pool's last counted change and fewer than three are counted. Otherwise, once the pool's guard
   allows it, ask for the stake back when the elector owes at least what was sent, or three
   changes are counted.
2. For each idle pool with queued withdrawals: send op 2 for up to 40, with 1 token plus 0.15
   per withdrawal.
3. With bidding on: skip the election if a pool already bid in it. Otherwise take the first idle
   pool with no queue, nothing owed by the elector, enough validator funds and a stake of at least
   the elector's minimum, and run the kit's `take_part_in_elections.sh` unchanged in its nominator
   mode from a directory whose `env.sh` names that pool and its stake: its balance less 11 tokens.

**Result**: `election stop` sets `bidding` false and rewrites the program; the cycle keeps running.
`stake recover` runs steps 1 and 2 once. With bidding off, `wallet send`, `node restart` and image
maintenance no longer wait for the schedule (`elections.schedule_can_bid`); they hold the election
lock, so no run overlaps them, and keep the cycle. `node stop` turns the bids off and keeps the
cycle (`elections.stop_denominator_bidding`). `node remove` refuses while the cycle is installed,
unless `--force` (`nodes._refuse_while_pools_need_their_cycle`).

**On failure**: a step that fails is decided again by the next run from the pool's state.

## Reports, links and retirement

[denominator info](../denominator/info.md), [denominator stakers](../denominator/stakers.md),
[denominator link](../denominator/link.md), [denominator retire](../denominator/retire.md).

**Logic**: info and stakers read each pool, the elector's credit and the Safe. Link builds every
Oberton link from the recorded addresses and fixed bodies, offline. Retire refuses while bidding,
while a stake is out, while the elector owes a pool or while a withdrawal is queued, then removes
the cron file.
