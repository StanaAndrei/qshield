"""Application configuration and structured logging for Q-SHIELD.

Provides the central `Settings` instance loaded from environment variables and
`configs/app.yaml` fallback, along with structured logging that redacts sensitive
key material and credentials.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

REDACT_KEYS = [
    "password",
    "secret",
    "token",
    "key_material",
    "private_key",
    "api_key",
    "secret_audit_salt",
]

REDACTION_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(REDACT_KEYS) + r")\b\s*[:=]\s*['\"]?([^'\",\s]+)['\"]?"
)


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive keywords, tokens, and secret keys."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = REDACTION_PATTERN.sub(r"\1=***REDACTED***", record.msg)
        if record.args:
            if isinstance(record.args, dict):
                cleaned: dict[str, Any] = {}
                for k, v in record.args.items():
                    if any(sensitive in k.lower() for sensitive in REDACT_KEYS):
                        cleaned[k] = "***REDACTED***"
                    else:
                        cleaned[k] = v
                record.args = cleaned
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    REDACTION_PATTERN.sub(r"\1=***REDACTED***", str(arg))
                    if isinstance(arg, str)
                    else arg
                    for arg in record.args
                )
        return True


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Settings source that reads values from configs/app.yaml."""

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        yaml_data = self._load_yaml()
        val = yaml_data.get(field_name.lower())
        if val is None:
            val = yaml_data.get(field_name.upper())
        return val, field_name, False

    def _load_yaml(self) -> dict[str, Any]:
        config_path = Path(os.getenv("QSHIELD_CONFIG_PATH", "configs/app.yaml"))
        if not config_path.is_file():
            return {}
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                # Flatten top-level sections if needed
                flat: dict[str, Any] = {}
                for k, v in data.items():
                    if isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            flat[f"{k}_{sub_k}"] = sub_v
                            flat[sub_k] = sub_v
                    flat[k] = v
                return flat
        except Exception:
            return {}

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        yaml_data = self._load_yaml()
        for field_name in self.settings_cls.model_fields:
            if field_name.lower() in yaml_data:
                d[field_name] = yaml_data[field_name.lower()]
            elif field_name.upper() in yaml_data:
                d[field_name] = yaml_data[field_name.upper()]
        return d


class Settings(BaseSettings):
    """Central configuration for Q-SHIELD."""

    model_config = SettingsConfigDict(
        env_prefix="QSHIELD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_ENV: str = Field(default="simulation", description="Execution environment: simulation, test, or production")
    APP_NAME: str = Field(default="Q-SHIELD Orchestrator", description="Application service name")
    VERSION: str = Field(default="0.1.0", description="SemVer version")

    DB_URL: str = Field(default="sqlite:///./qshield.db", description="Database connection string")
    DB_ECHO: bool = Field(default=False, description="Enable SQLAlchemy engine query logging")

    SECRET_AUDIT_SALT: str = Field(
        default="qshield-audit-salt-simulation-secret-key-32b",
        description="Salt used for tamper-evident audit log hashing",
    )

    LOG_LEVEL: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    LOG_FORMAT: str = Field(default="text", description="Log output format: text or json")

    API_HOST: str = Field(default="0.0.0.0", description="API listen host")
    API_PORT: int = Field(default=8000, description="API listen port")

    CONFIG_DIR: str = Field(default="configs", description="Configuration files directory")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def configure_logging(level: str | None = None) -> None:
    """Configures structured, redacted logging for the application."""
    log_level_str = level or settings.LOG_LEVEL
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers on re-entry
    if not root_logger.handlers:
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(RedactingFilter())
        root_logger.addHandler(stream_handler)
    else:
        for existing_handler in root_logger.handlers:
            existing_handler.addFilter(RedactingFilter())


# Singleton instance
settings = Settings()
configure_logging()
