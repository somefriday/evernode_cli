# evernode node lsync

Seed an unsynchronized node's database from another node on this host.

## Example

```bash
sudo evernode node lsync --from validator01 --to validator02 --dry-run
sudo evernode node lsync --from validator01 --to validator02
```

## What it does

Stops both nodes, copies the source's block database into the target with `rsync`, starts the source, swaps the target's database and starts the target.

## Flags

| Flag        | Value | Default  | Meaning                            |
| ----------- | ----- | -------- | ---------------------------------- |
| `--from`    | NAME  | required | Synchronized source node.          |
| `--to`      | NAME  | required | Target node that is behind.        |
| `--yes`     |       | ask      | Skip the confirmation prompt.      |
| `--dry-run` |       | off      | Print the plan and change nothing. |

## Behaviour

- Never copies keys, node configuration, wallet files or `catchains/`.
- Refuses if either node has an election schedule, if the source is in a validator set, if the target is already synchronized, or if the images or global configurations differ.
- Refuses while an election run that began before `election stop` is still using either node; retry when it finishes.
- Both nodes must be running: the checks read their consoles.
- If the copy fails or is interrupted (Ctrl+C, a closed SSH session), or the target does not start on the copied database, the target gets its previous database back and both nodes are started again.
- The database it no longer needs is deleted last. A deletion that fails is only warned about and never changes the result; an interrupt leaves the rest of it. What stays is a `.node_db-lsync-*` directory in the target's workspace, named in the warning, to delete by hand.
- Needs free space for one more copy of the source database.

## See also

- [node sync](sync.md)
