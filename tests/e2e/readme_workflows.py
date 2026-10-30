"""Execute and record the two README workflows using the installed CLI."""
import contextlib
from datetime import datetime, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import hashlib
import subprocess
import sys
import time

from evernode import configuration, storage
from terminal import run as terminal_run

BASE = Path(os.environ['E2E_BASE'])
IMAGE = os.environ['E2E_IMAGE']
# A network still close to its zerostate synchronizes directly; an older one
# needs blockchain data from a stopped local observer (see seed_local_checkpoint).
CHECKPOINT_SOURCE = os.environ.get('E2E_CHECKPOINT_SOURCE')
# Every network-specific value comes from the environment, with no default that
# names one machine: a run against a regenerated network has to be told what to
# join, and containers a previous run left behind hold their names until
# someone removes them.
CASES = {
    'new': {
        'registry': 'fresh',
        'name': os.environ.get('E2E_NEW_NAME', 'readme-new'),
        'ip': os.environ['E2E_NEW_IP'],
        'port': int(os.environ.get('E2E_NEW_PORT', '30305')),
    },
    'import': {
        'registry': 'import',
        'name': os.environ.get('E2E_IMPORT_NAME', 'readme-import'),
        'ip': os.environ['E2E_IMPORT_IP'],
        'port': int(os.environ.get('E2E_IMPORT_PORT', '30306')),
    },
}


def record(value):
    value['time_utc'] = datetime.now(timezone.utc).isoformat()
    with (BASE / 'readme-trace.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value) + '\n')


def registry(case):
    return BASE / CASES[case]['registry']


def root(case):
    return registry(case) / 'nodes' / CASES[case]['name']


def config(case):
    return json.loads((root(case) / 'node.json').read_text())


def seed_local_checkpoint(case):
    """Only blockchain DB data from a stopped, unelected local observer."""
    if not CHECKPOINT_SOURCE:
        record({'kind': 'chain-checkpoint-skipped', 'case': case,
                'reason': 'This network is close to its zerostate; synchronize normally.'})
        return
    source_root = Path(CHECKPOINT_SOURCE)
    container = 'evernode-' + source_root.name
    source_state = subprocess.run(['docker', 'inspect', container,
                                    '--format', '{{.State.Status}}'], capture_output=True, text=True, check=True)
    assert source_state.stdout.strip() == 'exited'
    command(case, 'node', 'stop', '-n', CASES[case]['name'], '--timeout', '10')
    node_config = root(case) / 'node_cfg/config.json'
    identity = lambda: hashlib.sha256(json.dumps(json.loads(node_config.read_text())['adnl_node']['keys'], sort_keys=True).encode()).hexdigest()
    before = identity()
    target = root(case) / 'node_db'
    target.rename(root(case) / 'node_db-before-local-checkpoint')
    shutil.copytree(source_root / 'node_db', target, ignore=shutil.ignore_patterns('catchains'))
    assert identity() == before
    record({'kind': 'chain-checkpoint-adaptation', 'case': case,
            'source': str(source_root / 'node_db'), 'source_container_state': 'exited',
            'excluded': ['catchains', 'all keys/configuration/scripts outside node_db'],
            'identity_unchanged': True,
            'reason': 'The old zerostate full-shard overlay has no peers after shard splitting; seed current blockchain data before README synchronization.'})
    command(case, 'node', 'start', '-n', CASES[case]['name'])


def synchronize(case):
    command(case, 'node', 'sync', '-n', CASES[case]['name'], '--wait')
    if os.environ.get('E2E_RELAY_SENDMESSAGE') != '1':
        # The production transport: this node's own control console, reached
        # exactly as the vendored env.sh reaches it in Docker mode.
        record({'kind': 'transport', 'case': case, 'relay': False,
                'description': "Reads and writes both use this node's own console."})
        return
    configuration.write_node_environment(root(case) / 'scripts/env.sh', {
        'CALL_CONS': 'python3 /workspace/tests/e2e/console_proxy.py --container evernode-' + CASES[case]['name'],
    })
    record({'kind': 'transport-adaptation', 'case': case, 'relay': True,
            'description': 'Only sendmessage is relayed through node1; health, getters and consensus-key operations stay on this node.'})


def command(case, *arguments, terminal=False, phrases=(), state_dir=None):
    state = Path(state_dir) if state_dir else registry(case)
    argv = ['evernode', '--state-dir', str(state), *arguments]
    name = arguments[arguments.index('-n') + 1] if '-n' in arguments else CASES[case]['name']
    node_file = state / 'nodes' / name / 'node.json'
    before = json.loads(node_file.read_text()).get('setup_stage') if node_file.exists() else None
    record({'kind': 'command-start', 'case': case, 'argv': argv, 'stage_before': before})
    logs = BASE / 'logs'
    logs.mkdir(exist_ok=True, mode=0o700)
    label = '-'.join(arguments[:2])
    log = logs / f'{case}-{time.time_ns()}-{label}.log'
    code = 0
    if terminal:
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                terminal_run(argv, phrases=phrases)
        except RuntimeError:
            code = 1
        text = output.getvalue()
    else:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        code = result.returncode
        text = result.stdout + result.stderr
    log.write_text(text)
    log.chmod(0o600)
    after = json.loads(node_file.read_text()).get('setup_stage') if node_file.exists() else None
    record({'kind': 'command-finish', 'case': case, 'argv': argv, 'exit_code': code,
            'stage_before': before, 'stage_after': after, 'log': str(log)})
    print(f'[{case}] {shlex.join(argv)} -> {code}; stage={after}', flush=True)
    if code:
        raise RuntimeError(f'README command failed; inspect {log}')
    return text


def console(case, request):
    result = subprocess.run(['docker', 'exec', 'evernode-' + CASES[case]['name'], 'console',
                             '-C', '/ever-node/node_cfg/console.json', '-jc', request],
                            capture_output=True, text=True, check=True, timeout=20)
    return json.loads(result.stdout)


def wait_funded(case, field):
    address = config(case)['wallet'][field]
    for _ in range(60):
        if console(case, 'getaccount ' + address).get('acc_type') == 'Uninit':
            record({'kind': 'funding-visible', 'case': case, 'field': field, 'address': address})
            return
        time.sleep(2)
    raise RuntimeError('Expected funding was not visible')


def create(case, *, profile=None, phrases=()):
    item = CASES[case]
    extra = (['--import-wallet', '--wallet-address', profile['wallet_address'],
              '--depool-address', profile['depool_address']] if profile else
             ['--validator-assurance', '50000', '--min-stake', '10',
              '--reward-fraction', '65', '--balance-threshold', '20'])
    command(case, 'node', 'create', '-n', item['name'], '--image', IMAGE,
            '--project-source', str(BASE / 'source'), '--network', 'devnet',
            '--ip', item['ip'], '--memory', '2G', '--adnl-port', str(item['port']),
            '--workchain', '0', '--cron-interval', '10', '--depool-type', 'EverX',
            '--custodians', '3', '--required-signatures', '2', '--yes', *extra,
            terminal=bool(profile), phrases=phrases)
    seed_local_checkpoint(case)
    synchronize(case)


def snapshot(case, label):
    result = subprocess.run([sys.executable, '/workspace/tests/e2e/snapshot.py',
                             CASES[case]['registry'], CASES[case]['name']],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Evidence is collected while the chain is moving, so a failure here
        # needs its reason, not just an exit code.
        record({'kind': 'snapshot-failed', 'case': case, 'label': label,
                'exit_code': result.returncode, 'stderr': result.stderr[-2000:]})
        raise RuntimeError(f'Evidence snapshot {label} failed:\n{result.stderr[-2000:]}')
    value = json.loads(result.stdout)
    directory = BASE / 'evidence'
    directory.mkdir(exist_ok=True, mode=0o700)
    path = directory / f'{case}-{label}.json'
    storage.write_json_atomically(path, value)
    return value


def wait_out_of_validator_sets(case, name, timeout=900):
    """Wait until the chain says this node owes no blocks.

    Stopping a member of the current set loses the blocks it owes, and stopping
    a member of the *next* set also denies its successor consensus session a
    participant, which can stall a small network until it returns. The
    documented choices are to wait for the set to expire or to force the stop;
    a replacement flow can afford to wait.
    """
    deadline = time.monotonic() + timeout
    while True:
        chain = json.loads(command(case, 'election', 'status', '-n', name))['chain']
        if not chain['in_current_vset_p34'] and not chain['in_next_vset_p36']:
            record({'kind': 'source-node-left-validator-sets', 'case': case, 'node': name})
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f'{name} is still in a validator set after {timeout}s')
        time.sleep(10)


def start(case):
    command(case, 'election', 'start', '-n', CASES[case]['name'], '--yes')
    status = json.loads(command(case, 'election', 'status', '-n', CASES[case]['name']))
    assert status['enabled'] is True


def main():
    action = sys.argv[1]
    if action == 'new-wallet':
        command('new', '--version')
        command('new', 'wallet', '--help')
        command('new', 'image', 'list')
        create('new')
        command('new', 'wallet', 'create', '-n', CASES['new']['name'], '--yes', terminal=True)
    elif action == 'new-sync-wallet':
        seed_local_checkpoint('new')
        synchronize('new')
        command('new', 'wallet', 'create', '-n', CASES['new']['name'], '--yes', terminal=True)
    elif action == 'new-depool':
        wait_funded('new', 'wallet_address')
        synchronize('new')
        command('new', 'wallet', 'deploy', '-n', CASES['new']['name'], '--yes')
        command('new', 'depool', 'prepare', '-n', CASES['new']['name'], '--yes', terminal=True)
    elif action == 'new-finish':
        wait_funded('new', 'depool_address')
        synchronize('new')
        command('new', 'depool', 'deploy', '-n', CASES['new']['name'], '--yes')
        command('new', 'depool', 'stake-initial', '-n', CASES['new']['name'], '--yes')
        value = snapshot('new', 'initial-stake')
        assert int(value['participant']['total']) == 50000 * 10**9
        assert value['pending']['transactions'] == []
        assert value['wallet']['custodians'] == 3 and value['wallet']['required_signatures'] == 2
        assert int(value['depool']['participantRewardFraction']) == 65
        start('new')
    elif action == 'import':
        # Fence the node that owns these contracts before recovering them.
        source = CASES['new']['name']
        command('new', 'election', 'stop', '-n', source)
        wait_out_of_validator_sets('new', source)
        command('new', 'node', 'stop', '-n', source, '--timeout', '30')
        original = root('new')
        old = json.loads((original / 'node.json').read_text())
        phrases = [(original / f'keys/MSKeys_{source}/{source}_seed_{i}.txt').read_text().strip()
                   for i in range(1, 4)]
        record({'kind': 'source-node-fenced', 'case': 'import', 'source': source,
                'reason': 'A replacement must not validate beside the node it replaces.'})
        create('import', profile=old['wallet'], phrases=phrases)
        command('import', 'wallet', 'recover', '-n', CASES['import']['name'], '--yes')
        command('import', 'wallet', 'verify', '-n', CASES['import']['name'])
        command('import', 'depool', 'verify', '-n', CASES['import']['name'])
        value = snapshot('import', 'verified')
        assert config('import')['wallet']['wallet_address'] == old['wallet']['wallet_address']
        assert config('import')['wallet']['depool_address'] == old['wallet']['depool_address']
        assert value['wallet']['custodians'] == 3 and value['wallet']['required_signatures'] == 2
        # The recovered node must have its own network identity.
        identity = lambda path: hashlib.sha256(json.dumps(
            json.loads((path / 'node_cfg/config.json').read_text())['adnl_node']['keys'],
            sort_keys=True).encode()).hexdigest()
        assert identity(original) != identity(root('import'))
        record({'kind': 'recovery-audit', 'case': 'import',
                'same_wallet': True, 'same_depool': True, 'different_node_identity': True})
        start('import')
    else:
        raise SystemExit('Unknown phase')


if __name__ == '__main__':
    main()
