"""Compose-only adaptations for an existing local ever-node Docker network."""
import os

from evernode import docker


def install():
    original = docker.build_node_compose_config
    if getattr(original, '_local_network_adapter', False):
        return

    def local_compose(config, directory):
        result = original(config, directory)
        result['networks']['testnet'] = {
            'external': True,
            'name': os.environ['E2E_DOCKER_NETWORK'],
        }
        node = result['services']['node']
        node.pop('ports', None)
        node['networks'] = {'node': {}, 'testnet': {'ipv4_address': config['ip']}}
        node['volumes'].append(os.environ['E2E_ZEROSTATE'] + ':/zerostate:ro')
        node['command'][0] += ' -z /zerostate'
        return result

    local_compose._local_network_adapter = True
    docker.build_node_compose_config = local_compose
