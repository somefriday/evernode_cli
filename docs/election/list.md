# evernode election list

Show the schedule state of every node.

## Example

```bash
sudo evernode election list
```

## What it does

Prints the `election status` report of each managed node as one JSON array.

## Flags

None.

## Behaviour

- Needs root.
- A node whose console does not answer shows `chain.error`.

## See also

- [election status](status.md)
