"""Host dependency inspection and installation for supported Linux hosts."""

import os
import shutil
import sys
from pathlib import Path

from . import process

YQ_VERSION = "4.44.5"
REQUIRED_COMMANDS = (
    "docker",
    "git",
    "tail",
    "jq",
    "bc",
    "flock",
    "cron",
    "chronyc",
    "gawk",
    "yq",
)
REQUIRED_SERVICES = ("docker", "cron", "chrony")


def _find_command(name):
    """Find a command even when root's PATH omits administrative directories."""
    path = shutil.which(name)
    if path:
        return path
    search_path = os.pathsep.join(
        filter(
            None, [os.environ.get("PATH", ""), "/usr/local/sbin", "/usr/sbin", "/sbin"]
        )
    )
    return shutil.which(name, path=search_path)


def collect_host_dependency_report():
    checks = {
        "platform": sys.platform,
        "root": getattr(os, "geteuid", lambda: -1)() == 0,
        "commands": {x: _find_command(x) for x in REQUIRED_COMMANDS},
    }
    for key, cmd in (
        ("docker", ["docker", "info", "--format", "{{.ServerVersion}}"]),
        ("compose", ["docker", "compose", "version"]),
    ):
        try:
            result = process.execute_command(cmd)
            value = result.stdout.strip()
            if not value:
                raise process.OperationError(
                    result.stderr.strip() or f"{key} returned no usable result"
                )
            checks[key] = value
        except process.OperationError as exc:
            checks[key] = {"error": str(exc)}
    checks["services"] = {}
    for service in REQUIRED_SERVICES:
        try:
            result = process.execute_command(
                ["systemctl", "is-active", service], check=False
            )
            checks["services"][service] = (
                result.stdout.strip() or result.stderr.strip() or "inactive"
            )
        except process.OperationError as exc:
            checks["services"][service] = "error: " + str(exc)
    checks["note"] = (
        "Read-only check. No package installation, firewall change or pruning."
    )
    return checks


def host_dependencies_available(checks):
    return (
        checks.get("platform") == "linux"
        and all(
            bool(checks.get("commands", {}).get(command))
            for command in REQUIRED_COMMANDS
        )
        and all(isinstance(checks.get(key), str) for key in ("docker", "compose"))
        and all(
            checks.get("services", {}).get(service) == "active"
            for service in REQUIRED_SERVICES
        )
    )


def _os_release():
    values = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip("'\"")
    except OSError as exc:
        raise process.OperationError("Cannot read /etc/os-release") from exc
    return values


def _detect_distribution(release):
    distro = release.get("ID", "").strip().lower()
    if distro in ("ubuntu", "debian"):
        return distro
    family = release.get("ID_LIKE", "").lower().split()
    if "ubuntu" in family:
        return "ubuntu"
    if "debian" in family:
        return "debian"
    return ""


def _extract_codename(release, distro):
    codename = release.get("VERSION_CODENAME", "").strip().lower()
    if codename:
        return codename
    version = release.get("VERSION", "")
    if "(" in version and ")" in version:
        return version[version.find("(") + 1 : version.find(")")].strip().lower()
    if distro == "debian":
        return {"13": "trixie", "12": "bookworm", "11": "bullseye"}.get(
            release.get("VERSION_ID", "").strip(), ""
        )
    return ""


def _supported_release():
    release = _os_release()
    distro = _detect_distribution(release)
    codename = _extract_codename(release, distro)
    if distro not in ("ubuntu", "debian") or not codename:
        raise process.OperationError(
            "host setup supports Ubuntu and Debian releases with a known VERSION_CODENAME only"
        )
    return distro, codename


def get_setup_plan():
    distro, _ = _supported_release()
    return (
        "install Docker Engine, Compose, Git and certificates from Docker's official "
        + distro.title()
        + " repository"
    )


def _docker_plugin_available(plugin):
    if not _find_command("docker"):
        return False
    try:
        result = process.execute_command(["docker", plugin, "version"], check=False)
    except process.OperationError:
        return False
    return result.returncode == 0


def _configure_docker_repository(distro, codename):
    keyring = Path("/etc/apt/keyrings")
    keyring.mkdir(mode=0o755, parents=True, exist_ok=True)
    process.execute_command(
        [
            "curl",
            "-fsSL",
            f"https://download.docker.com/linux/{distro}/gpg",
            "-o",
            keyring / "docker.asc",
        ],
        timeout=120,
    )
    os.chmod(keyring / "docker.asc", 0o644)
    architecture = process.execute_command(
        ["dpkg", "--print-architecture"]
    ).stdout.strip()
    source = (
        f"deb [arch={architecture} signed-by=/etc/apt/keyrings/docker.asc] "
        f"https://download.docker.com/linux/{distro} {codename} stable\n"
    )
    Path("/etc/apt/sources.list.d/docker.list").write_text(source)
    os.chmod("/etc/apt/sources.list.d/docker.list", 0o644)
    process.execute_command(["apt-get", "update"], timeout=600)


def install_host_dependencies():
    """Install the supported Docker runtime and script dependencies on Ubuntu or Debian."""
    distro, codename = _supported_release()
    process.execute_command(["apt-get", "update"], timeout=600)
    process.execute_command(
        [
            "apt-get",
            "install",
            "-y",
            "ca-certificates",
            "curl",
            "git",
            "jq",
            "bc",
            "gawk",
            "util-linux",
            "procps",
            "cron",
            "chrony",
        ],
        timeout=600,
    )
    engine_missing = not _find_command("docker")
    compose_missing = not _docker_plugin_available("compose")
    buildx_missing = not _docker_plugin_available("buildx")
    if engine_missing or compose_missing or buildx_missing:
        _configure_docker_repository(distro, codename)
    if engine_missing:
        process.execute_command(
            [
                "apt-get",
                "install",
                "-y",
                "docker-ce",
                "docker-ce-cli",
                "containerd.io",
                "docker-buildx-plugin",
                "docker-compose-plugin",
            ],
            timeout=1200,
        )
    elif compose_missing or buildx_missing:
        packages = ["apt-get", "install", "-y"]
        if buildx_missing:
            packages.append("docker-buildx-plugin")
        if compose_missing:
            packages.append("docker-compose-plugin")
        process.execute_command(packages, timeout=600)
    process.execute_command(
        [
            "curl",
            "--proto",
            "=https",
            "--tlsv1.2",
            "-fL",
            "https://github.com/mikefarah/yq/releases/download/v"
            + YQ_VERSION
            + "/yq_linux_amd64",
            "-o",
            "/usr/local/bin/yq",
        ],
        timeout=300,
    )
    os.chmod("/usr/local/bin/yq", 0o755)
    process.execute_command(
        ["systemctl", "enable", "--now", "docker", "cron", "chrony"], timeout=120
    )
    checks = collect_host_dependency_report()
    if not host_dependencies_available(checks):
        raise process.OperationError(
            "Docker installation finished but Docker Engine or Compose is unavailable"
        )
    return checks
