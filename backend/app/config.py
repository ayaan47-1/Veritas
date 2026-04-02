from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import os
import yaml


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    data_dir: str
    max_pages: int
    cors_origins: list[str]
    app_env: str
    mcp_server_path: str
    raw: Dict[str, Any]


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_from_path(cfg: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _apply_env_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(cfg)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        cfg.setdefault("database", {})["url"] = database_url

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        cfg.setdefault("redis", {})["url"] = redis_url

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        cfg.setdefault("storage", {})["data_dir"] = data_dir

    app_env = os.getenv("APP_ENV")
    if app_env:
        cfg.setdefault("app", {})["env"] = app_env

    mcp_server_path = os.getenv("MCP_SERVER_PATH")
    if mcp_server_path:
        cfg.setdefault("mcp", {})["server_path"] = mcp_server_path

    return cfg


def _load_db_overrides() -> Dict[str, Any]:
    # TODO: merge config_overrides table (key/value) once DB is wired.
    return {}


def _merge_dicts(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings() -> Settings:
    default_path = Path(__file__).resolve().parents[1] / "config.yaml"
    config_path = Path(os.getenv("VERITAS_CONFIG_PATH", str(default_path)))
    base_cfg = _load_yaml(config_path)
    db_overrides = _load_db_overrides()
    cfg = _merge_dicts(base_cfg, db_overrides)
    cfg = _apply_env_overrides(cfg)

    return Settings(
        database_url=_get_from_path(cfg, "database.url", ""),
        redis_url=_get_from_path(cfg, "redis.url", ""),
        data_dir=_get_from_path(cfg, "storage.data_dir", "/data"),
        max_pages=int(_get_from_path(cfg, "ingest.max_pages", 500)),
        cors_origins=_get_from_path(cfg, "app.cors_origins", []),
        app_env=_get_from_path(cfg, "app.env", "dev"),
        mcp_server_path=_get_from_path(cfg, "mcp.server_path", ""),
        raw=cfg,
    )


settings = load_settings()
