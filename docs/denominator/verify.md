# evernode denominator verify

Check each deployed pool's code, validator wallet and terms.

## Example

```bash
sudo evernode denominator verify -n validator01
```

## What it does

Reads each pool's account through the node's console and compares its code hash with the pinned one, and its stored terms with the prepared ones: the Safe as its validator wallet, the reward share, the staker limit and both minimums. Changes nothing on chain.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- After `denominator deploy`, on a synchronized node.
- Refuses a pool whose code or terms differ, naming each difference.
- Refuses a staker limit above 40: a pool pays every staker in one transaction, which has to fit the masterchain gas limit.
- Prints the next command: `denominator deposit`.

## See also

- [denominator deploy](deploy.md)
- [denominator deposit](deposit.md)
