"""Compatibility build configuration for Ubuntu's older setuptools releases."""
from setuptools import find_packages, setup


setup(
    name="evernode-manager",
    version="0.3.3",
    description="Build and manage Everscale validator node containers",
    python_requires=">=3.9",
    packages=find_packages(include=["evernode", "evernode.*"]),
    package_data={"evernode": ["assets/*"]},
    # The vendored kit and contract, as MANIFEST.in lists them.
    include_package_data=True,
    entry_points={"console_scripts": ["evernode=evernode.cli:main"]},
)
