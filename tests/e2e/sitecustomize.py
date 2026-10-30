"""Opt-in local Docker transport when invoking the installed evernode command."""
import os

if os.environ.get('EVERNODE_E2E_NETWORK'):
    from network_adapter import install
    install()
