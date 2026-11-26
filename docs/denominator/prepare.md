# evernode denominator prepare

Compile the pool contract and derive the addresses of a denominator validator's pools.

## Example

```bash
sudo evernode denominator prepare -n validator01
```

## What it does

Builds the pool tools image, compiles the nominator-pool contract that ships with evernode and checks it against the pinned code hash, then derives each pool's address from the code, the node's Safe and the terms `node setup` recorded. It saves the addresses in the node's configuration and `env.sh`. Nothing is sent or spent.

## Flags

| Flag                | Value | Default                    | Meaning                                            |
| ------------------- | ----- | -------------------------- | -------------------------------------------------- |
| `-n`, `--name`      | NAME  | required                   | The managed node.                                  |
| `--contract-source` | DIR   | the copy evernode installs | Another nominator-pool source directory.           |
| `--new-contract`    |       | off                        | Move a node whose pools are deployed to new pools. |
| `--yes`             |       | ask                        | Skip the confirmation prompt.                      |

## Behaviour

- Denominator validators only, after `wallet deploy`, or `wallet verify` for an existing Safe: an address depends on the Safe.
- Needs Docker; the node need not be running. The first run builds the tools image, which downloads its packages.
- Refuses terms outside what `node setup` accepts, such as more than 40 stakers.
- Refuses code that gives other addresses than the deployed pools', unless `--new-contract`.
- A pool keeps its terms for life: the Safe as its validator wallet, the reward share, the staker limit and both minimums.
- Prints the next command: `denominator deploy`.

## See also

- [node setup](../node/setup.md)
- [denominator deploy](deploy.md)
