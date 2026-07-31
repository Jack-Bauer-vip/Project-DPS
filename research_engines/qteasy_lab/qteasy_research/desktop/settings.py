"""桌面端非敏感配置。API Key 仍建议通过环境变量提供。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_settings_path() -> Path:
    base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "qteasy_research" / "settings.json"


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else default_settings_path()
    if not target.exists():
        return {"store_dir": "", "default_horizon": "medium", "default_update_policy": "reuse", "data_mode": "direct", "evidence_research": True, "indicator_config": {}}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"store_dir": "", "default_horizon": "medium", "default_update_policy": "reuse", "data_mode": "direct", "evidence_research": True, "indicator_config": {}}


def save_settings(values: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path else default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
