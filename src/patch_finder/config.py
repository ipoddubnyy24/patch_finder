"""Credentials, clone location, and branch -> Maloo job mapping.

Nothing here is secret: Maloo credentials are read at runtime from the process
environment or from the same file the installed ``maloo`` CLI already uses
(``~/.config/maloo-tool/.env``).  No secret is ever committed to the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MALOO_URL = "https://testing.whamcloud.com"
DEFAULT_MALOO_ENV = Path.home() / ".config" / "maloo-tool" / ".env"
DEFAULT_CLONE = Path.home() / "work" / "src" / "lustre" / "lustre-release"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class MalooCredentials:
    base_url: str
    username: str
    password: str


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` .env file, ignoring blank lines and ``#`` comments."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_maloo_credentials(
    environ: dict[str, str] | None = None,
    env_path: Path | None = None,
) -> MalooCredentials:
    """Load Maloo credentials from the environment, then the maloo-tool .env.

    The process environment wins over the file, so a single run can be
    overridden without editing any config.
    """
    environ = dict(os.environ) if environ is None else environ
    file_values = parse_env_file(env_path or DEFAULT_MALOO_ENV)

    def pick(name: str) -> str | None:
        return environ.get(name) or file_values.get(name)

    username = pick("MALOO_USER")
    password = pick("MALOO_PASS")
    base_url = pick("MALOO_URL") or DEFAULT_MALOO_URL
    if not username or not password:
        raise ConfigError(
            "Maloo credentials not found: set MALOO_USER and MALOO_PASS, "
            f"or populate {DEFAULT_MALOO_ENV}"
        )
    return MalooCredentials(base_url.rstrip("/"), username, password)


def branch_to_job(branch: str) -> str:
    """Map a git branch name to its Maloo ``trigger_job``.

    Integration jobs are named ``lustre-<branch>`` (``b_es6_0`` ->
    ``lustre-b_es6_0``).  A value already starting with ``lustre-`` (a real
    job name such as ``lustre-reviews``) is returned unchanged.
    """
    return branch if branch.startswith("lustre-") else f"lustre-{branch}"
