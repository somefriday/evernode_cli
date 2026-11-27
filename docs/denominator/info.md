# evernode denominator info

Show where each pool's money is and what its cycle does next.

## Example

```bash
sudo evernode denominator info -n validator01
```

## What it does

Reads each pool, what the elector owes it and the Safe through the node's console. Changes nothing.

## Flags

| Flag           | Value   | Default    | Meaning           |
| -------------- | ------- | ---------- | ----------------- |
| `-n`, `--name` | NAME    | required   | The managed node. |
| `--pool`       | `A` `B` | every pool | One pool.         |

## Behaviour

- `phase`: `idle`, `bid sent` or `staked`. A pool holds one stake at a time; a pair bids in alternate elections.
- `phase` `returned`: the elector holds the whole stake for the pool again. With fewer than two `validator_sets_counted` it was not elected; the pool takes it back only after two new validator sets and the hold time, as if it had validated.
- `stakers` of `max_stakers`. `stakers_funds_nanotokens` includes `pending_deposits_nanotokens`, made while staked, which join the stake after the round.
- `queued_withdrawals`: withdrawals asked for while staked. The cycle pays them before the pool bids again.
- `validator_funds_nanotokens` against `validator_funds_required_nanotokens`.
- `validator_withdrawable_nanotokens`, while idle: what `denominator withdraw` allows. It is what the pool holds beyond its stakers' funds, its 10-token reserve and a year of storage.
- `pool_storage_per_year_nanotokens`: a year of masterchain storage for a full pool at the network's current prices, about 53 tokens on mainnet. The pool pays it from its balance.
- `validator_sets_counted` and `next_action`: what the cycle sends next, and when the stake can be recovered.
- `bidding` is false after `election stop`; the cycle still runs.
- A source that cannot be read shows as `unknown`; the rest of the report still shows.

## See also

- [denominator stakers](stakers.md)
- [stake recover](../stake/recover.md)
