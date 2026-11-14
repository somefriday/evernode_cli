# evernode host check

Report whether this host has what evernode needs.

## Example

```bash
evernode host check
```

## What it does

Looks for the required commands, asks Docker and Compose for their versions, asks systemd whether `docker`, `cron` and `chrony` are active, and asks the vote engine `evernode-vote` for its version. It installs and changes nothing.

## Flags

None.

## Behaviour

- Needs no root.
- Commands checked: `docker`, `git`, `tail`, `jq`, `bc`, `flock`, `cron`, `chronyc`, `gawk`, `rsync`, `yq`.
- `vote_engine` is the engine's path, or why `evernode vote` cannot use it: missing, or not the version this evernode needs. `host setup` builds it.
- Exit code `0` when everything is present, the three services are active and the vote engine is usable, otherwise `1`.
- Services are read with `systemctl is-active`, so a host without systemd reports them as not active.

## See also

- [host setup](setup.md)
