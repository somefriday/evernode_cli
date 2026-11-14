# evernode stake set

Record what a solo or nominator validator stakes in each election.

## Example

```bash
sudo evernode stake set -n validator01 --mode balanced
```

## What it does

Checks the amount against the chain's stake limits and writes it into the node's settings. The election scripts then bid exactly this number every time.

## Flags

| Flag            | Value               | Default  | Meaning                                                   |
| --------------- | ------------------- | -------- | --------------------------------------------------------- |
| `-n`, `--name`  | NAME                | required | The managed node.                                         |
| `--amount`      | TOKENS              |          | Whole tokens per election.                                |
| `--mode`        | `balanced` `single` | menu     | Compute it now: half the spendable balance, or all of it. |
| `--all-balance` |                     |          | Old name of `--mode single`.                              |
| `--yes`         |                     | ask      | Skip the confirmation prompt.                             |

## Behaviour

- Spendable is the staking account's balance plus what the elector owes it, minus 50 tokens kept for fees. The staking account is the Safe (solo) or the nominator contract.
- A stake already at the elector is not counted: on a validating node a mode sizes only what is liquid, and says so. Use `--amount` there.
- Below the elector's minimum is refused. A mode above the maximum is capped; a named amount above it is refused.
- The first `stake set` moves setup to `ready-for-elections`.
- To be elected the stake must also compete: the elector seats the set with the largest total stake.

## See also

- [stake status](status.md)
- [election start](../election/start.md)
