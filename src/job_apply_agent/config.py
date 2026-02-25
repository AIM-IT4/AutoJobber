from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _expand_env_values(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_name = value[2:-1]
            return os.getenv(env_name, "")
        return value

    if isinstance(value, list):
        return [_expand_env_values(item) for item in value]

    if isinstance(value, dict):
        return {key: _expand_env_values(val) for key, val in value.items()}

    return value


def load_yaml(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return dict(default or {})

    with file_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    return _expand_env_values(data)


def parse_csv_arg(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def ensure_output_dir(path: str | Path) -> Path:
    out_dir = Path(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir
