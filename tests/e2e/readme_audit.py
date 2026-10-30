"""Check independent README workflows without emitting private material."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from readme_workflows import BASE, CASES, config, root

new = config('new')
recovered = config('import')
normalize = lambda value: value.lower().removeprefix('0x')
source = CASES['new']['name']
# The replacement must prove it holds the same custodian keys as the node it
# replaces, read from that node's own key files rather than from its own claim.
original_public = sorted(
    normalize(json.loads((root('new') / f'keys/{source}_{i}.keys.json').read_text())['public'])
    for i in (1, 2, 3))
import_evidence = json.loads((BASE / 'evidence/import-verified.json').read_text())
assert original_public == sorted(normalize(value) for value in import_evidence['wallet']['public_keys'])
for field in ('wallet_address', 'depool_address'):
    assert recovered['wallet'][field] == new['wallet'][field]

def identity(path):
    data = json.loads((path / 'node_cfg/config.json').read_text())
    return hashlib.sha256(json.dumps(data['adnl_node']['keys'], sort_keys=True).encode()).hexdigest()

assert identity(root('new')) != identity(root('import'))
for case in CASES:
    for path in (root(case) / 'keys').rglob('*'):
        if not (path.is_dir() or path.name.endswith('.json') or 'seed' in path.name):
            continue
        assert path.stat().st_uid == 0
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path.name
states = {}
for case in CASES:
    container = 'evernode-' + CASES[case]['name']
    result = subprocess.run(['docker', 'inspect', container, '--format', '{{.State.Status}}'],
                            text=True, capture_output=True, check=True)
    states[container] = result.stdout.strip()
# A replacement must not validate beside the node it replaces.
assert states['evernode-' + CASES['new']['name']] == 'exited'
assert states['evernode-' + CASES['import']['name']] == 'running'
result = {'audit': 'passed', 'import_preserves_addresses_and_custodians': True,
          'node_identities_differ': True,
          'private_key_file_permissions': '0600', 'private_key_directory_permissions': '0700',
          'private_key_owner_uid': 0, 'container_states': states}
(BASE / 'evidence/recovery-audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
