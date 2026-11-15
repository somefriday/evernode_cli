# evernode node create

Create a node: its workspace, identity, containers and ports. Its validator role comes later, from `node setup`.

## Example

```bash
sudo evernode node create -n validator01
```

## What it does

Asks for the network, image and node settings (or takes them from flags), prepares `/var/lib/ever-validator/nodes/NAME`, generates the node's identity and starts the node and its StatsD exporter. Nothing here concerns wallets, DePools or seed phrases.

## Flags

| Flag                                                     | Value           | Default                         | Meaning                                                                 |
| -------------------------------------------------------- | --------------- | ------------------------------- | ----------------------------------------------------------------------- |
| `-n`, `--name`                                           | NAME            | asked                           | Lowercase letters, digits, hyphens; starts with a letter; max 40.       |
| `--image`                                                | IMAGE           | menu                            | A managed image from `image list`.                                      |
| `--build-new`                                            |                 | off                             | Build a new image instead; see `--node-ref`.                            |
| `--node-ref`                                             | REF             | `master`                        | ever-node ref to build; needs `--build-new`.                            |
| `--node-repo`, `--cli-repo`, `--cli-ref`, `--image-repo` |                 | as `image build`                | Sources for a new build; not with `--image`.                            |
| `--network`                                              | `main` `devnet` | menu; `main` without a terminal | Network the node joins.                                                 |
| `--ip`                                                   | IPv4            | detected, then asked            | Public address peers reach. Required without a terminal.                |
| `--memory`                                               | `40G`, `2048M`  | asked                           | Docker memory ceiling. Required without a terminal.                     |
| `--adnl-port`                                            | PORT            | first free from 58888           | ADNL UDP port.                                                          |
| `--metrics-port`                                         | PORT            | first free from 9102            | Loopback port of the metrics exporter.                                  |
| `--project-source`                                       | PATH            | the kit installed with evernode | Ever-Validator kit to install as the node's scripts.                    |
| `--project-repo`, `--project-ref`                        | URL, REF        |                                 | Clone a kit instead; the repository defaults to Custler/Ever-Validator. |
| `--resume`                                               |                 | off                             | Finish an interrupted preparation of `-n NAME` from its saved inputs.   |
| `--yes`                                                  |                 | ask                             | Skip the confirmation prompt.                                           |
| `--dry-run`                                              |                 | off                             | Print the plan and change nothing.                                      |

## Behaviour

- Needs root. Without a terminal, pass `-n`, `--memory`, `--ip` and `--image` or `--build-new`.
- An image chosen in the menu starts the creation at once; with `--image` or `--build-new` the plan is confirmed first.
- Without a source flag the node gets the kit installed with evernode; a kit that is missing is refused before any question.
- Refuses the names `depool`, `proxy0` and `proxy1`: the election scripts keep files of those names beside the node's own.
- Asks nothing about the validator role, so several nodes can be created and synchronized before any of them is set up.
- If preparation fails, everything this run created is removed. A directory that existed before is left alone.
- A create that was killed leaves the node in place: `--resume`, then `node start`.
- Next: `node sync -n NAME --wait`, then `node setup -n NAME`.

## See also

- [node sync](sync.md)
- [node setup](setup.md)
- [node lsync](lsync.md)
