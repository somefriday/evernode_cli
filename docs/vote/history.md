# evernode vote history

List every proposal joined on this host and the votes sent.

## Example

```bash
sudo evernode vote history
sudo evernode vote history --proposal HASH
```

## What it does

Reads the host's vote store. With `--proposal` it lists each vote: round, node, validator key, when sent and counted, and the transaction.

## Flags

| Flag         | Value | Default | Meaning                           |
| ------------ | ----- | ------- | --------------------------------- |
| `--proposal` | HASH  | all     | Show one proposal with its votes. |

## Behaviour

- Needs root; needs no endpoint.
- A vote a pass sent but never saw counted is not listed; the chain has it.
- The end time is when a pass on this host saw the proposal end, up to a round after the chain decided.

## See also

- [vote status](status.md)
