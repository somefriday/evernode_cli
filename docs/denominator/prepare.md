# evernode denominator prepare

Compile the pool contract and derive the addresses of a denominator validator's pools.

## Example

```bash
sudo evernode denominator prepare -n validator01
```

## What it does

Builds the pool tools image, compiles the nominator-pool contract that ships with evernode and checks it against the pinned code hash, then derives each pool's address from the code, the node's Safe and the terms `node setup` recorded. It saves the addresses in the node's configuration and `env.sh`. Nothing is sent or spent.

## Flags

| Flag                | Value | Default                    | Meaning                                                              |
| ------------------- | ----- | -------------------------- | -------------------------------------------------------------------- |
| `-n`, `--name`      | NAME  | required                   | The managed node.                                                    |
| `--contract-source` | DIR   | the copy evernode installs | Another nominator-pool source directory.                             |
| `--new-contract`    |       | off                        | Move a node whose pools are deployed to new pools.                   |
| `--force`           |       | ask                        | Drop recorded pools that still hold funds without asking.            |
| `--yes`             |       | ask                        | Skip the confirmation prompt. It does not answer the question above. |

## Behaviour

- Denominator validators only, after `wallet deploy`, or `wallet verify` for an existing Safe: an address depends on the Safe.
- Needs Docker. The first run builds the tools image, which downloads its packages.
- The node need not be running, except to read a pool that prepare would drop.
- Refuses terms outside what `node setup` accepts, such as more than 40 stakers.
- Refuses code that gives other addresses than the deployed pools', unless `--new-contract`.
- **A pool it would drop is read first.** This happens with other code and `--new-contract`, or with other terms before deploy. Prepare reads:
  - the pool's balance;
  - its stake at the elector;
  - its stakers and queued withdrawals;
  - the Safe's unsigned transactions to it.
- **A pool that holds anything is dropped only when you answer yes, or with `--force`.** `--yes` does not answer this question, and without a terminal prepare refuses. evernode no longer maintains a dropped pool: nothing counts its rounds, recovers its stake or pays its withdrawals. The result lists each dropped pool with what it held.
- **A pool it cannot read blocks the change once the pools are deployed,** unless `--force`. Before deploy, prepare goes on and lists the pool as not checked.
- **Moving to pools that do not exist yet sets the stage back to `denominator-prepared`.** Deploy, verify and deposit follow.
- A pool keeps its terms for life: the Safe as its validator wallet, the reward share, the staker limit and both minimums.
- Prints the next command: `denominator deploy`.

## See also

- [node setup](../node/setup.md)
- [denominator deploy](deploy.md)
