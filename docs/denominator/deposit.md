# evernode denominator deposit

Send the validator's own funds from the Safe into the pools.

## Example

```bash
sudo evernode denominator deposit -n validator01 --amount 1000
```

## What it does

Sends the amount plus 1 token from the Safe to each pool, or to the one `--pool` names, with the validator deposit operation. The pool keeps the token as its fee and adds the amount to the validator's funds.

## Flags

| Flag           | Value   | Default    | Meaning                          |
| -------------- | ------- | ---------- | -------------------------------- |
| `-n`, `--name` | NAME    | required   | The managed node.                |
| `--amount`     | TOKENS  | required   | Whole tokens each pool receives. |
| `--pool`       | `A` `B` | every pool | One pool.                        |
| `--yes`        |         | ask        | Skip the confirmation prompt.    |

## Behaviour

- After `denominator verify`, on a synchronized node.
- A pool bids only while the validator's funds are at least the minimum `node setup` set, and at least 101 tokens on a network without config 40, such as Everscale mainnet.
- Once every pool holds that much, the node is ready for `election start`.
- The validator's funds take the validator's share of each reward and absorb a loss before any staker's.
- Holds the node's election lock: a scheduled run waits.

## See also

- [denominator withdraw](withdraw.md)
- [election start](../election/start.md)
