"""Relay only external messages through another validator's console.

Node health, account reads and consensus-key generation stay on the managed
node. The relay exists for a local network whose managed node cannot deliver
external messages itself; name the relay container and its console path in
E2E_RELAY_CONTAINER and E2E_RELAY_CONSOLE.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--container', required=True)
args, console_args = parser.parse_known_args()
command = shlex.split(console_args[-1])
if command[0] not in ('sendmessage', 'send-message'):
    raise SystemExit(subprocess.run(['docker', 'exec', args.container, 'console',
                                     '-C', '/ever-node/node_cfg/console.json', *console_args]).returncode)

relay = os.environ['E2E_RELAY_CONTAINER']
console = os.environ.get('E2E_RELAY_CONSOLE', '/ever-node/node_cfg/console.json')
target = '/tmp/evernode-e2e-relay-' + uuid.uuid4().hex + '.boc'
with tempfile.TemporaryDirectory() as temporary:
    local = Path(temporary) / 'message.boc'
    subprocess.run(['docker', 'cp', args.container + ':' + command[1], str(local)], check=True)
    subprocess.run(['docker', 'cp', str(local), relay + ':' + target], check=True)
    try:
        result = subprocess.run(['docker', 'exec', relay, 'console',
                                 '-C', console, '-c', 'sendmessage ' + target])
    finally:
        subprocess.run(['docker', 'exec', relay, 'unlink', target], check=True)
raise SystemExit(result.returncode)
