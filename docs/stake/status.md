# evernode stake status

Show the stake, the staking account and what the elector owes.

## Example

```bash
sudo evernode stake status -n validator01
```

## What it does

Reads the staking account's balance, the elector's credit for it, the open election and the chain's stake limits.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- `recoverable_nanotokens`: stake and reward of finished rounds, returned by the next scheduled run or `stake recover`.
- `funding` counts liquid and recoverable funds only; with the node in a validator set it says that the stake at the elector is not counted.
- Solo: `pending_elector_transactions` lists Safe requests to the elector short of signatures. Nominator: `pending_wallet_transactions`.
- `chain` holds the limits and timings read from config params 15 and 17.
- Denominator: shows `denominator info` instead.

## See also

- [stake confirm](confirm.md)
- [nominator info](../nominator/info.md)
