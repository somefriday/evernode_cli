"""Build configuration; it also serves Ubuntu's older setuptools releases.

The package carries the vote engine's sources: building it copies vote/'s build
inputs to evernode/vendor/vote, where evernode host setup builds the engine this
evernode speaks to. MANIFEST.in lists the same files for the sdist.
"""
from pathlib import Path
import shutil

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
# What the engine's Dockerfile and cargo read; README.md, testnet/, runs/ and target/ stay out.
ENGINE_INPUTS = ("Cargo.toml", "Cargo.lock", "Dockerfile", ".dockerignore", "src", "res")


class BuildWithEngineSources(build_py):
    """build_py, plus the vote engine's build inputs at evernode/vendor/vote."""

    def run(self):
        # pip builds inside the checkout and reuses build/, so the package starts
        # from what the checkout holds now: a file a newer version deletes is not
        # installed again. Never when the build directory is the checkout itself.
        package = Path(self.build_lib) / "evernode"
        if package.resolve() != (ROOT / "evernode").resolve():
            shutil.rmtree(package, ignore_errors=True)
        super().run()
        target = Path(self.build_lib) / "evernode" / "vendor" / "vote"
        # A build directory reused from an earlier build keeps no deleted source.
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for name in ENGINE_INPUTS:
            source = ROOT / "vote" / name
            if source.is_dir():
                shutil.copytree(source, target / name, ignore=shutil.ignore_patterns(".*"))
            else:
                shutil.copy2(source, target / name)


setup(
    name="evernode-manager",
    version="0.5.4",
    description="Build and manage Everscale validator node containers",
    python_requires=">=3.9",
    packages=find_packages(include=["evernode", "evernode.*"]),
    package_data={"evernode": ["assets/*"]},
    # The vendored kit and contract, as MANIFEST.in lists them.
    include_package_data=True,
    entry_points={"console_scripts": ["evernode=evernode.cli:main"]},
    cmdclass={"build_py": BuildWithEngineSources},
)
