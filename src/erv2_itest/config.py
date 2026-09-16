"""Optional project-level configuration for erv2-itest.

Resolved from (in precedence order) explicit constructor kwargs, environment
variables (ERV2_ITEST_*), an optional .erv2_itest.yml/.yaml file in the invoking CWD,
and finally these built-in defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .models import ModuleConfig, TerraformModuleConfig


class ErvItestConfig(BaseSettings):
    """Project-level defaults, overridable by CLI flags.

    `default_module`/`default_terraform`/`default_base_input` let a repo whose
    scenarios all exercise the same module (the common case) declare it once here
    instead of repeating an identical `module:`/`base_input:` block in every scenario
    YAML - a scenario's own block, if given, still takes precedence.
    """

    model_config = SettingsConfigDict(
        env_prefix="ERV2_ITEST_",
        yaml_file=(".erv2_itest.yml", ".erv2_itest.yaml"),
        extra="forbid",
    )

    output_dir: Path = Path(".erv2-itests")
    scenarios_dir: Path = Path("integration-tests")
    default_mode: Literal["erv2", "terraform"] = "erv2"
    default_module: ModuleConfig | None = None
    default_terraform: TerraformModuleConfig | None = None
    default_base_input: Path | None = None
    log_level: str = "INFO"

    # mode: erv2 only - Vault KVv2 paths used to generate a module's credentials_file
    # when it isn't set manually. See vault.py.
    target_account: str = "app-sre/creds/terraform/ter-int-dev/config"
    tf_state_account: str = "app-sre/creds/terraform/ter-int-dev/config"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # ruff: ignore[unused-class-method-argument]
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Explicit kwargs > env vars > the YAML file > field defaults."""
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
