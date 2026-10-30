"""Run the real CLI with only the local transport/zerostate Compose additions."""
from evernode import cli
from network_adapter import install

install()
raise SystemExit(cli.main())
