"""Unit tests for erv2_itest.vault - Vault-generated AWS credentials, no real Vault needed."""

from __future__ import annotations

import json
import stat
import subprocess
from typing import TYPE_CHECKING

import pytest

from erv2_itest.vault import (
    build_aws_credentials_ini,
    fetch_kv_secret,
    get_or_create_credentials_file,
)

if TYPE_CHECKING:
    from pathlib import Path

TARGET_SECRET = {
    "aws_access_key_id": "AKIATARGET",
    "aws_secret_access_key": "target-secret",
    "aws_provider_version": "3.22.0",
    "key": "qontract-reconcile.tfstate",
}
TF_STATE_SECRET = {
    "aws_access_key_id": "AKIATFSTATE",
    "aws_secret_access_key": "tf-state-secret",
}

OWNER_READ_WRITE_ONLY = 0o600


def _fake_completed_process(secret: dict[str, str]) -> subprocess.CompletedProcess[str]:
    stdout = json.dumps({"data": secret})
    return subprocess.CompletedProcess(
        args=["vault"], returncode=0, stdout=stdout, stderr=""
    )


def test_fetch_kv_secret_parses_data_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "erv2_itest.vault.subprocess.run",
        lambda *_a, **_kw: _fake_completed_process(TARGET_SECRET),
    )

    secret = fetch_kv_secret("app-sre/creds/terraform/ter-int-dev/config")

    assert secret == TARGET_SECRET


def test_fetch_kv_secret_raises_clear_error_when_vault_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("erv2_itest.vault.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="Could not run the `vault` CLI"):
        fetch_kv_secret("app-sre/creds/terraform/ter-int-dev/config")


def test_fetch_kv_secret_raises_clear_error_when_vault_binary_not_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise PermissionError

    monkeypatch.setattr("erv2_itest.vault.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="Could not run the `vault` CLI"):
        fetch_kv_secret("app-sre/creds/terraform/ter-int-dev/config")


def test_fetch_kv_secret_raises_clear_error_on_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise subprocess.CalledProcessError(
            2, ["vault"], output="", stderr="Error: permission denied"
        )

    monkeypatch.setattr("erv2_itest.vault.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="vault login"):
        fetch_kv_secret("app-sre/creds/terraform/ter-int-dev/config")


def test_build_aws_credentials_ini_renders_both_profiles() -> None:
    ini = build_aws_credentials_ini(
        default=TARGET_SECRET, external_resources_state=TF_STATE_SECRET
    )

    assert "[default]" in ini
    assert "aws_access_key_id = AKIATARGET" in ini
    assert "aws_secret_access_key = target-secret" in ini
    assert "[external-resources-state]" in ini
    assert "aws_access_key_id = AKIATFSTATE" in ini
    assert "aws_secret_access_key = tf-state-secret" in ini


def test_get_or_create_credentials_file_fetches_and_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def _fake_fetch(path: str) -> dict[str, str]:
        calls.append(path)
        return TARGET_SECRET if path == "target-path" else TF_STATE_SECRET

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fake_fetch)

    cache_path = get_or_create_credentials_file(
        target_account="target-path",
        tf_state_account="tf-state-path",
        cache_dir=tmp_path,
        refresh=False,
    )

    assert calls == ["target-path", "tf-state-path"]
    content = cache_path.read_text(encoding="utf-8")
    assert "AKIATARGET" in content
    assert "AKIATFSTATE" in content
    assert stat.S_IMODE(cache_path.stat().st_mode) == OWNER_READ_WRITE_ONLY


def test_get_or_create_credentials_file_dedupes_identical_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def _fake_fetch(path: str) -> dict[str, str]:
        calls.append(path)
        return TARGET_SECRET

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fake_fetch)

    get_or_create_credentials_file(
        target_account="same-path",
        tf_state_account="same-path",
        cache_dir=tmp_path,
        refresh=False,
    )

    assert calls == ["same-path"]


def test_get_or_create_credentials_file_reuses_cache_without_calling_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "erv2_itest.vault.fetch_kv_secret",
        lambda path: TARGET_SECRET if path == "target-path" else TF_STATE_SECRET,
    )
    seeded_path = get_or_create_credentials_file(
        target_account="target-path",
        tf_state_account="tf-state-path",
        cache_dir=tmp_path,
        refresh=False,
    )

    def _fail_if_called(_path: str) -> dict[str, str]:
        raise AssertionError("must not call Vault when a cache hit is available")

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fail_if_called)

    cached_path = get_or_create_credentials_file(
        target_account="target-path",
        tf_state_account="tf-state-path",
        cache_dir=tmp_path,
        refresh=False,
    )

    assert cached_path == seeded_path


def test_get_or_create_credentials_file_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def _fake_fetch(path: str) -> dict[str, str]:
        calls.append(path)
        return TARGET_SECRET if path == "target-path" else TF_STATE_SECRET

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fake_fetch)

    get_or_create_credentials_file(
        target_account="target-path",
        tf_state_account="tf-state-path",
        cache_dir=tmp_path,
        refresh=False,
    )
    get_or_create_credentials_file(
        target_account="target-path",
        tf_state_account="tf-state-path",
        cache_dir=tmp_path,
        refresh=True,
    )

    assert calls == ["target-path", "tf-state-path", "target-path", "tf-state-path"]
