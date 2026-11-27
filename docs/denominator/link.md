# evernode denominator link

Make the Oberton wallet links stakers use: stake, withdraw and the rescue steps.

## Example

```bash
sudo evernode denominator link -n validator01
```

## What it does

Prints, for each pool, an Oberton link for every operation a staker sends: `stake`, `withdraw`, `update`, `recover` and `pay`, and `pay-one` with `--staker`. A link carries the pool's address, the tokens to attach and the message body.

## Flags

| Flag           | Value   | Default    | Meaning                             |
| -------------- | ------- | ---------- | ----------------------------------- |
| `-n`, `--name` | NAME    | required   | The managed node.                   |
| `--pool`       | `A` `B` | every pool | One pool.                           |
| `--staker`     | `0:…`   |            | Add a pay-one link for this staker. |

## Behaviour

- Reads nothing from the chain and needs no running node. Nothing is signed or sent, and the links hold no secret.
- `stake` attaches the minimum deposit plus the pool's 1-token fee; every other link attaches 2 tokens. Oberton lets the sender change the tokens; a withdrawal returns what it does not use.
- **When storage has eaten into a pool's 10-token reserve,** the last staker's `withdraw` pays nothing with 2 tokens. The pool sends the 2 tokens back, minus gas, and the staker stays in. Attaching more than the shortfall pays the staker out, and the pool keeps what was attached. `denominator withdraw` leaves a year of storage in each pool so that this does not happen.
- `withdraw` takes the staker's whole balance: at once while the pool is idle, otherwise when its stake returns. There is no partial withdrawal: withdraw, then stake again what to keep.
- Stakers use a basechain (`0:`) wallet. Oberton sends without bounce: a deposit the pool refuses, because it is full or below the minimum, stays in the pool, where only the validator can take it out (`denominator withdraw`), for example to send it back.
- The rescue links are for when this validator stops; anyone may send them. `update` once as each of the two rounds after a bid begins, `recover` 9 h 7 min after the second on mainnet, then `pay` until no withdrawal waits.
- A `recover` sent too early is refused and its tokens stay in the pool. An `update` or `pay` with nothing to do returns what it does not use.
- Publish the rescue links when the pools open: a validator that has stopped cannot make them.

## See also

- [denominator info](info.md)
- [nominator link](../nominator/link.md)
