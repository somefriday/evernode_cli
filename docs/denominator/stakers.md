# evernode denominator stakers

List each pool's stakers and what they hold.

## Example

```bash
sudo evernode denominator stakers -n validator01
```

## What it does

Reads each pool's storage through the node's console and lists every staker: its basechain address, the `amount` in the stake, `pending` deposits that join after the round, and whether a withdrawal is queued. Changes nothing.

## Flags

| Flag           | Value   | Default    | Meaning           |
| -------------- | ------- | ---------- | ----------------- |
| `-n`, `--name` | NAME    | required   | The managed node. |
| `--pool`       | `A` `B` | every pool | One pool.         |

## Behaviour

- Amounts are in nanotokens.
- A pool takes at most the number of stakers `node setup` set, 40 at most.

## See also

- [denominator info](info.md)
- [denominator link](link.md)
