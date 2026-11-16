# Setup: from a running node to a validator

How a synchronized node gets its role, its Safe and its staking contract, by validator type. The
operator's command sequences are in the [command reference](../README.md). Flags are on each
command's page.

## The paths

`node setup` fixes the *role*: the validator type, and whether the Safe is new or imported. After
that each command needs one setup stage and moves to the next
([SETUP-1](properties.md#setup-1), [state.md](state.md#setup-stages)).

A new Safe:

```
node setup ─ wallet create ─ wallet deploy ─┬─ depool:    depool prepare ─ depool deploy ─ depool stake-initial ─┐
                                            ├─ solo:      stake set ─────────────────────────────────────────────┤
                                            └─ nominator: nominator prepare ─ deploy ─ verify ─ stake set ───────┴─ election start
```

An imported Safe:

```
node setup ─ wallet recover ─ wallet verify ─┬─ depool:    depool verify ───────────────────────────────────┐
                                             ├─ solo:      stake set ───────────────────────────────────────┤
                                             └─ nominator: nominator prepare ─ deploy ─ verify ─ stake set ─┴─ election start
```

`stake set` is in [staking.md](staking.md#set-the-stake). `election start` is in
[elections.md](elections.md#the-schedule).

## What every setup command does first

Code: `setup_actions.handle_setup_action`.

1. Take the node lock ([LOCK-1](properties.md#lock-1)).
2. Require a role (`configuration.require_role`).
3. Require a synchronized node ([NODE-1](properties.md#node-1)). `nominator prepare` is the one
   exception: it reads nothing from the chain.
4. Ask for confirmation, unless the command only reads or only verifies.
5. Take the election lock, if the command is in `setup_actions.ELECTION_LOCKED_ACTIONS`
   ([LOCK-2](properties.md#lock-2), [LOCK-3](properties.md#lock-3)).
6. Run the command. Save the node record only if it succeeds.

A command that fails leaves the setup stage where it was. Running it again is the way to
continue.

## Set the role

Command: [`node setup`](../node/setup.md). Code: `role.handle_node_setup`, `role.ask_role`.

**Requires**

- A node that was fully created and is not removed.
- No wallet setup begun ([SETUP-3](properties.md#setup-3)).

**Logic**

1. Ask the validator type: `depool`, `solo` or `nominator`. Without a terminal and without the
   flag it is `depool`.
2. Ask whether the Safe exists already.
3. Ask the node's workchain, 0 or -1, and the minutes between election runs, 1 to 59.
4. For a DePool validator ask the DePool type. For a new DePool ask its four terms: validator
   assurance, minimum stake, participant reward fraction, balance threshold. Refuse what the
   DePool's constructor refuses: a reward fraction outside 1 to 99, a minimum stake under 1
   token, or an assurance under the minimum stake.
5. For a nominator validator ask the cold owner's address. It must be in the masterchain.
6. Ask the Safe's custodians and required signatures. A nominator validator's Safe defaults to
   one key. The other types default to 3 custodians and 2 signatures.
7. For an imported Safe ask its address, and for a DePool validator the DePool's address. A solo
   or nominator Safe must be in the masterchain ([SETUP-4](properties.md#setup-4)).
8. Refuse an answer that does not fit the type: DePool terms for a solo or nominator validator,
   deployment terms for an imported DePool, an owner for another type, an address without the
   import.
9. Check the wallet profile ([state.md](state.md#wallet-profile)). Check that no other node has
   these addresses ([STATE-5](properties.md#state-5)) and that a nominator's owner is not a wallet
   of this server ([SEC-3](properties.md#sec-3)).
10. Show the plan. `--dry-run` ends here. Ask.
11. Under the node lock and the registry lock, check the requirements and the addresses again,
    save the role in the record and write `env.sh`.

**Result**

The record holds `validator_type`, `settings` and `wallet`. The setup stage does not change.
`env.sh` carries the stake mode and the settings.

**On failure**

Nothing is saved before step 11. Run the command again. It may also be run again to change the
role until `wallet create` or `wallet recover` has run.

## A new Safe

### Create the keys

Command: [`wallet create`](../wallet/create.md). Code: `wallets.wallet_create`,
`wallets.prepare_wallet`, `Msig-prep.sh`.

**Requires**

- Wallet mode `new`, stage `node-started`.
- A terminal on both input and output ([SEC-2](properties.md#sec-2)).
- The vote engine installed ([VOTE-5](properties.md#vote-5)): the kit derives keys with it.

**Logic**

1. `Msig-prep.sh` runs for the number of custodians and the Safe's workchain
   ([SETUP-4](properties.md#setup-4)). For each custodian it:
   1. generates a seed phrase and stores it root-only, unless one is stored already;
   2. pipes the phrase to `evernode-vote key-from-phrase` and saves the key pair.
2. The script computes the Safe's address from the wallet code, the first key and the workchain.
   It copies the address and the key pairs into `keys/`.
3. evernode checks that the key files and the stored phrases agree, and that no other
   node has the address.
4. evernode prints the phrases and the address and waits for the word `SAVED`.
5. The stage becomes `wallet-created` and the profile gets the address.

**Result**

Keys and phrases on disk, an address to fund, nothing on chain.

**On failure**

Without `SAVED` the stage is not saved. Running the command again shows the same phrases: an
existing phrase file is never generated again.

### Deploy

Command: [`wallet deploy`](../wallet/deploy.md). Code: `wallets.wallet_deploy`, `Msig_deploy.sh`.

**Requires**

- Wallet mode `new`, stage `wallet-created`.
- At least 0.9 tokens on the Safe's address.

**Logic**

1. Read the account. If it is already active, skip step 2 (`wallets._deploy_once`).
2. `Msig_deploy.sh` builds the deploy message, with every custodian's public key and the number
   of required signatures, and sends it. If the script fails but the account now reads active,
   continue.
3. Wait up to 240 s for the account to become active.
4. Read the Safe's custodians and threshold from the chain and compare them with the profile and
   the key files ([SETUP-2](properties.md#setup-2), `wallets.require_own_safe`).
5. The stage becomes `wallet-deployed`.

**Result**

An active Safe that this node's keys control.

**On failure**

The stage stays `wallet-created`. Running the command again recognises a deployed account and
does not deploy twice. A Safe with other custodians or another threshold is refused for good: this
node cannot use it.

## An imported Safe

### Recover the keys

Command: [`wallet recover`](../wallet/recover.md). Code: `wallets.wallet_recover`,
`wallets.enter_import_seeds`.

**Requires**

- Wallet mode `import`, stage `node-started`.
- A terminal: every custodian's seed phrase is typed at a hidden prompt
  ([SEC-2](properties.md#sec-2)).

**Logic**

1. If no phrase is stored, or with `--replace-seeds`, read the Safe's custodians and threshold
   on chain; where setup recorded others, use the Safe's and say so. Ask for one phrase per
   custodian. Each must be one line of at least 12 words. Store them root-only (mode 0600). All are checked
   before any is written.
2. `Msig-prep.sh` derives the key pairs from the stored phrases.
3. Read the Safe at the recorded address: its custodians and its threshold
   (`scripts.verify_imported_safe`).
4. Compare: the count, the threshold, and the set of keys must be the profile's and the derived
   ones ([SETUP-2](properties.md#setup-2)).
5. Copy the key pairs into `keys/` and write the recorded address into `keys/NAME.addr`.
6. The stage becomes `wallet-recovered`.

**On failure**

The stage stays `node-started` and `keys/NAME.addr` is not written. A mistyped phrase is typed
again with `--replace-seeds`.

### Verify

Command: [`wallet verify`](../wallet/verify.md). Code: `wallets.wallet_verify`.

**Requires**

- Wallet mode `import`, stage `wallet-recovered`.

**Logic**

1. Read the Safe again and compare it with the key files now in `keys/`.
2. A solo or nominator validator's stage becomes `imported`. A DePool validator's becomes
   `wallet-verified`.

## A DePool validator

### Prepare the DePool

Command: [`depool prepare`](../depool/prepare.md). Code: `wallets.depool_prepare`,
`DePool-prep.sh`.

**Requires**

- Validator type `depool`, wallet mode `new`, stage `wallet-deployed`.
- A terminal on both input and output.

**Logic**

1. `DePool-prep.sh` generates the DePool's seed phrase and stores it root-only, unless one is
   stored already, and derives its key pair through the vote engine.
2. The script computes the DePool's address in workchain 0 and writes `keys/depool.addr`.
3. evernode checks that no other node has the address, prints the phrase and the address, and
   waits for the word `SAVED`.
4. The stage becomes `depool-prepared` and the profile gets the address.

### Deploy the DePool

Command: [`depool deploy`](../depool/deploy.md). Code: `wallets.depool_deploy`,
`DePool_deploy.sh`.

**Requires**

- Stage `depool-prepared`.
- On the DePool's address, at least twice the balance threshold plus 5 tokens.

**Logic**

1. Read the account. If it is already active, skip step 2.
2. `DePool_deploy.sh` builds the deploy message with the terms from the settings and sends it.
3. Wait up to 240 s for the account to become active.
4. Read the DePool. It must name this node's Safe as its validator wallet
   ([SETUP-2](properties.md#setup-2), `wallets.require_constructed_depool`).
5. The stage becomes `depool-deployed`.

**On failure**

- The stage stays `depool-prepared`. Running the command again checks the account again and
  deploys nothing twice.
- An active DePool that names another wallet was not constructed. An active account cannot be
  deployed again, so the node needs a new DePool. On a new network the constructor needs config
  param 32, which exists only after the first validator-set switch.

### The first stake

Command: [`depool stake-initial`](../depool/stake-initial.md). Code:
`wallets.depool_stake_initial`.

**Requires**

- Stage `depool-deployed`.
- Both proxies read as Active ([ELECT-8](properties.md#elect-8)), and a low one is topped up
  first ([ELECT-7](properties.md#elect-7)).
- The Safe holds at least the validator assurance plus 1 token.

**Logic**

1. Take the election lock, check the proxies and top up a low one.
2. Run the ordinary-stake operation for the validator assurance
   ([staking.md](staking.md#depool-operations)), marked as the initial stake.
3. Marked so, the operation transfers nothing if the Safe's stake in the DePool is already at
   least the assurance.
4. The stage becomes `ready-for-elections`.

**On failure**

The stage stays `depool-deployed`. Running the command again does not pay the assurance twice:
step 3, and for a pending transaction [OP-5](properties.md#op-5). `depool info` and
`depool confirm` work at this stage.

### Verify an imported DePool

Command: [`depool verify`](../depool/verify.md). Code: `wallets.depool_verify`.

**Requires**

- Validator type `depool`, wallet mode `import`, stage `wallet-verified`.

**Logic**

1. Read the DePool at the recorded address. It must name this node's Safe as its validator
   wallet.
2. Write `keys/depool.addr`.
3. The stage becomes `imported`.

## A nominator validator

The Safe is the contract's *hot wallet*: it may tell the contract to bid and to recover, nothing
else. The cold owner's key never comes here ([SEC-3](properties.md#sec-3)). Code: `nominator.py`.

### Prepare the contract

Command: [`nominator prepare`](../nominator/prepare.md). Code: `nominator.prepare`.

**Requires**

- Validator type `nominator`, with an owner on record.
- A Safe address in the masterchain that differs from the owner.
- Docker. No synchronized node is needed.

**Logic**

1. Find the contract source: the directory named by flag, one recorded by an earlier prepare,
   one beside a local kit, or the copy installed with evernode
   (`nominator.resolve_contract_source`).
2. Build the tools image from the source's `contracts/`, `tools/`, `package.json`,
   `package-lock.json` and `PINNED.json`, unless an image with the same fingerprint exists. The
   build fails if the contract does not compile to the code hash in `PINNED.json`.
3. In that image, with no network, `tools/cells.cjs` works in a staging directory beside the
   node's contract files:
   1. compiles the contract into `code.boc` and compares its hash with the pinned one;
   2. builds the contract's initial state from the owner and the Safe, which fixes its address;
   3. builds the deployer's message, which fixes the deployer's address;
   4. writes `manifest.json`.
4. If the node's contract is deployed (stage `nominator-deployed`, `nominator-verified` or
   `ready-for-elections`) and this address differs, stop unless `--new-contract`: the node would
   bid from an empty contract while its stake sits in the old one. The staging directory is
   removed and the node's contract files are left as they were.
5. Move the new files into `elections/nominator/`.
6. Save the address and the tools image in the settings, the record and `env.sh`.
7. The stage becomes `nominator-prepared`, from `wallet-deployed`, `imported` or
   `nominator-prepared`. The next step named is `nominator deploy`, or `nominator verify` for an
   imported node or a contract already deployed.

Every command that reads the contract files (`deploy`, `verify`, `info` and the owner's
requests) refuses files whose address is not the node's recorded contract
(`nominator._manifest`).

**Result**

`elections/nominator/` holds the contract and its manifest. Nothing is deployed and nothing is
spent.

### Deploy the contract

Command: [`nominator deploy`](../nominator/deploy.md). Code: `nominator.deploy`,
`scripts.fund_nominator_deployer`.

**Requires**

- A prepared contract whose hot wallet is still this node's Safe.
- In the Safe: `NOMINATOR_DEPLOY_VALUE` plus `NOMINATOR_DEPLOY_MARGIN`, 11 tokens, and 1 token
  more for its own fees.

**Logic**

1. If the contract is already active, report that and stop. The stage does not change;
   `nominator verify` is next.
2. Fund the deployer from the Safe ([wallet-operations.md](wallet-operations.md)):
   1. If the deployer already holds the deploy value, pay nothing.
   2. Send 11 tokens, without bounce and without a body.
   3. The effect is the deployer's balance reaching the deploy value, 10 tokens.
3. Send the deployer's message through the console. It is not signed: the deployer's code can
   only create the contract and delete itself.
4. Wait up to a minute for the contract to become active; until it is, send the trigger again,
   three sends in all. A later copy finds no deployer once the first has run.
5. The stage becomes `nominator-deployed`.

**On failure**

Running the command again continues: a funded deployer is not funded twice, and a payment left
pending in the Safe is finished ([OP-5](properties.md#op-5)).

### Verify the contract

Command: [`nominator verify`](../nominator/verify.md). Code: `nominator.verify`.

**Requires**

- A prepared contract that is active.

**Logic**

1. Save the contract's account state through the console.
2. In the tools image, read from that state: the code hash, the owner and the hot wallet.
3. Each must equal the manifest's value ([SETUP-2](properties.md#setup-2)).
4. The stage becomes `nominator-verified`, from `nominator-deployed` or `nominator-prepared`.

**Result**

The stake can now be funded: the owner sends the principal to the contract's address. `stake set`
is next.

**On failure**

A contract that differs from the manifest is refused, and each difference is named. Do not fund
it.
