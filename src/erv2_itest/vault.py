"""Generates an AWS credentials file from Vault-stored secrets.

Shells out to the local `vault` CLI - erv2-itest does not do its own Vault
authentication, it relies on the caller already having an active `vault login`
session, same as any other local vault CLI usage.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_PROFILE = "default"
EXTERNAL_RESOURCES_STATE_PROFILE = "external-resources-state"


def fetch_kv_secret(path: str) -> dict[str, str]:
    """Read a Vault secret via `vault kv get -format=json <path>`.

    Returns the top-level `data` field of the CLI's JSON output.
    """
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            ["vault", "kv", "get", "-format=json", path],  # ruff: ignore[start-process-with-partial-path]
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        # FileNotFoundError (not on PATH) and PermissionError (not executable) both
        # land here - either way the vault CLI couldn't be run at all.
        msg = f"Could not run the `vault` CLI: {exc}"
        raise RuntimeError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = (
            f"Failed to read Vault secret at {path!r}: {exc.stderr.strip()}\n"
            "Is your local Vault session still valid? Try `vault login`."
        )
        raise RuntimeError(msg) from exc

    payload = json.loads(result.stdout)
    data: dict[str, str] = payload["data"]
    return data


def build_aws_credentials_ini(
    *, default: dict[str, str], external_resources_state: dict[str, str]
) -> str:
    """Render a two-profile AWS credentials INI from two Vault secrets."""

    def _profile(name: str, secret: dict[str, str]) -> str:
        return (
            f"[{name}]\n"
            f"aws_access_key_id = {secret['aws_access_key_id']}\n"
            f"aws_secret_access_key = {secret['aws_secret_access_key']}\n"
        )

    return (
        _profile(DEFAULT_PROFILE, default)
        + "\n"
        + _profile(EXTERNAL_RESOURCES_STATE_PROFILE, external_resources_state)
    )


def _cache_filename(target_account: str, tf_state_account: str) -> str:
    digest = hashlib.sha256(
        f"{target_account}\n{tf_state_account}".encode()
    ).hexdigest()
    return f"vault-{digest[:16]}.credentials"


def get_or_create_credentials_file(
    *, target_account: str, tf_state_account: str, cache_dir: Path, refresh: bool
) -> Path:
    """A cached AWS credentials file for this account pair.

    Generated via Vault if missing or `refresh` is set, cached indefinitely
    otherwise: these are long-lived IAM user keys, not short-lived STS tokens, and
    re-fetching on every invocation would mean hitting the local Vault CLI's own
    (re-)auth flow far more often than necessary.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / _cache_filename(target_account, tf_state_account)

    if cache_path.exists() and not refresh:
        return cache_path

    target_secret = fetch_kv_secret(target_account)
    tf_state_secret = (
        target_secret
        if tf_state_account == target_account
        else fetch_kv_secret(tf_state_account)
    )

    content = build_aws_credentials_ini(
        default=target_secret, external_resources_state=tf_state_secret
    )
    cache_path.write_text(content, encoding="utf-8")
    cache_path.chmod(0o600)
    return cache_path
