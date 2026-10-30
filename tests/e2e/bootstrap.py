"""Prepare an isolated registry and local-network orchestration source.

Set E2E_REFRESH_SOURCE=1 to re-copy the checkout's vendored scripts over an
existing source directory, which is how a server operator's `git pull` reaches
this harness. The local-network adaptations are re-applied afterwards.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

from evernode import images, storage

base = Path(os.environ['E2E_BASE'])
base.mkdir(parents=True, exist_ok=True)
os.chown(base, 0, 0)
base.chmod(0o700)
source = base / 'source'


def apply_local_network_adaptations():
    """Point the reference source at this local network instead of devnet."""
    shutil.copyfile('/network/global-config.json', source / 'configs/devnet/ton-global.config.json')
    defaults = source / 'configs/rnode/default_config.json'
    value = json.loads(defaults.read_text())
    reference = json.loads(Path('/network/node1/config.json').read_text())
    identity_fields = {'adnl_node', 'control_server', 'validator_keys', 'validator_key_ring',
                       'ton_global_config_name', 'log_config_name', 'internal_db_path'}
    value.update({key: item for key, item in reference.items() if key not in identity_fields})
    value['cells_db_config']['cache_size_bytes'] = 67108864
    value['remp'] = None  # Use the same native REMP defaults as the local network.
    value['sync_by_archives'] = False
    defaults.write_text(json.dumps(value, indent=2))
    # The local network uses the Solidity elector in this ABI; main/devnet differ.
    shutil.copyfile('/upstream/src/validator/Elector.abi.json', source / 'contracts/EverX/elector/Elector.abi.json')


if not source.exists():
    shutil.copytree('/workspace/vendor/ever-validator', source)
    apply_local_network_adaptations()
elif os.environ.get('E2E_REFRESH_SOURCE') == '1':
    for component in ('scripts', 'configs', 'contracts'):
        shutil.rmtree(source / component)
        shutil.copytree(Path('/workspace/vendor/ever-validator') / component, source / component)
    apply_local_network_adaptations()
    print(json.dumps({'source_refreshed': str(source)}))

store = storage.NodeStateStore(base / os.environ.get('E2E_REGISTRY', 'fresh'))
image = os.environ['E2E_IMAGE']
image_id = images.validate_node_image(image)
# An image this CLI built records the sources it was built from. Reuse that
# record so the registry states the real provenance rather than a test stub.
built = next((record for record in images.list_managed_image_records(
    storage.NodeStateStore(Path(os.environ.get('E2E_BUILD_STATE_DIR', '/var/lib/ever-validator'))))
    if record['image'] == image), None)
if built:
    record = dict(built)
else:
    inputs = {'node_commit': 'unrecorded', 'recipe': 'unrecorded'}
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    record = {'schema_version': 1, 'key': key, 'inputs': inputs}
record.update({'image': image, 'image_id': image_id})
with store.acquire_operation_lock():
    store.images_directory(create=True)
    storage.write_json_atomically(images.image_record_path(store, record['key']), record)
print(json.dumps({'registry': str(store.root), 'image': image, 'image_id': image_id}))
