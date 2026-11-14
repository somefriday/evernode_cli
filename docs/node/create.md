# evernode node create

Create a validator node: its workspace, keys, containers and ports.

## Example

```bash
sudo evernode node create -n validator01
```

## What it does

Asks for the validator type, network, image and settings (or takes them from flags), prepares `/var/lib/ever-validator/nodes/NAME`, generates the node's identity and starts the node and its StatsD exporter. Wallets are not created here: `node status` names the next command.

## Flags

| Flag                                                     | Value                       | Default                                        | Meaning                                                                 |
| -------------------------------------------------------- | --------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------- |
| `-n`, `--name`                                           | NAME                        | asked                                          | Lowercase letters, digits, hyphens; starts with a letter; max 40.       |
| `--validator-type`                                       | `depool` `solo` `nominator` | menu; `depool` without a terminal              | How the validator stakes. Fixed for the node's life.                    |
| `--image`                                                | IMAGE                       | menu                                           | A managed image from `image list`.                                      |
| `--build-new`                                            |                             | off                                            | Build a new image instead; see `--node-ref`.                            |
| `--node-ref`                                             | REF                         | `master`                                       | ever-node ref to build; needs `--build-new`.                            |
| `--node-repo`, `--cli-repo`, `--cli-ref`, `--image-repo` |                             | as `image build`                               | Sources for a new build; not with `--image`.                            |
| `--network`                                              | `main` `devnet`             | menu; `main` without a terminal                | Network the node joins.                                                 |
| `--workchain`                                            | `0` `-1`                    | menu; `0` without a terminal                   | Workchain the node works in.                                            |
| `--ip`                                                   | IPv4                        | detected, then asked                           | Public address peers reach. Required without a terminal.                |
| `--memory`                                               | `40G`, `2048M`              | asked                                          | Docker memory ceiling. Required without a terminal.                     |
| `--adnl-port`                                            | PORT                        | first free from 58888                          | ADNL UDP port.                                                          |
| `--metrics-port`                                         | PORT                        | first free from 9102                           | Loopback port of the metrics exporter.                                  |
| `--cron-interval`                                        | MINUTES                     | asked; `10` without a terminal                 | Minutes between election runs; use a divisor of 60.                     |
| `--project-source`                                       | PATH                        | the kit installed with evernode                | Ever-Validator kit to install as the node's scripts.                    |
| `--project-repo`, `--project-ref`                        | URL, REF                    |                                                | Clone a kit instead; the repository defaults to Custler/Ever-Validator. |
| `--depool-type`                                          | `EverX` `StEver`            | menu; `EverX` without a terminal               | DePool contract. DePool validators only.                                |
| `--validator-assurance`                                  | TOKENS                      | asked; `50000` without a terminal              | DePool: stake the validator must hold.                                  |
| `--min-stake`                                            | TOKENS                      | asked; `10` without a terminal                 | DePool: smallest participant stake.                                     |
| `--reward-fraction`                                      | PERCENT                     | asked; `65` without a terminal                 | DePool: participants' share of rewards.                                 |
| `--balance-threshold`                                    | TOKENS                      | asked; `20` without a terminal                 | DePool: its own operating balance.                                      |
| `--custodians`                                           | N                           | asked; `3` without a terminal; nominator: menu | Safe custodians, 1-31.                                                  |
| `--required-signatures`                                  | N                           | asked; `2` without a terminal; nominator: menu | Signatures a Safe transaction needs.                                    |
| `--owner-address`                                        | `-1:…`                      | asked                                          | Nominator: the cold owner wallet. Cannot be changed later.              |
| `--import-wallet`                                        |                             | asked on a terminal                            | Recover an existing Safe from seed phrases.                             |
| `--wallet-address`                                       | ADDRESS                     |                                                | Existing Safe; needs `--import-wallet`.                                 |
| `--depool-address`                                       | ADDRESS                     |                                                | Existing DePool; needs `--import-wallet`, DePool type only.             |
| `--resume`                                               |                             | off                                            | Finish an interrupted preparation of `-n NAME` from its saved inputs.   |
| `--yes`                                                  |                             | ask                                            | Skip the confirmation prompt.                                           |
| `--dry-run`                                              |                             | off                                            | Print the plan and change nothing.                                      |

## Behaviour

- Needs root. Without a terminal, pass `-n`, `--memory`, `--ip` and `--image` or `--build-new`.
- An image chosen in the menu starts the creation at once; with `--image` or `--build-new` the plan is confirmed first.
- Without a source flag the node gets the kit installed with evernode; a kit that is missing is refused before any question.
- Solo and nominator Safes are always in the masterchain; a DePool validator's Safe is in the node's workchain.
- A new nominator node is asked for its Safe's keys: one key (the default, also without a terminal) or several, which then asks for custodians and signatures (`3` and `2`). That Safe only instructs the contract, so one key is enough. The two flags skip the menu; an imported nominator Safe is asked for its numbers (`1` and `1`).
- With `--import-wallet`, one seed phrase per custodian is typed at a hidden prompt and stored root-only in the node's `keys/`.
- If preparation fails, everything this run created is removed. A directory that existed before is left alone.
- A create that was killed leaves the node in place: `--resume`, then `node start`, then the wallet command `node status` names.

## See also

- [node status](status.md)
- [wallet create](../wallet/create.md)
- [wallet recover](../wallet/recover.md)
