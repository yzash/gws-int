"""Load and validate config.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

ALLOWED_SCOPES = (
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
)

DEFAULTS = {
    "project_id": "",
    "auth_mode": "oauth",
    "credentials_file": "credentials.json",
    "token_file": "token.json",
    "service_account_file": "service-account.json",
    "impersonate_user": "",
    "customer_id": "my_customer",
    "window_days": 28,
    "include_suspended": False,
    "timezone": "UTC",
    "usage_event_names": ["feature_utilization"],
    "ou_filter": [],
    "param_map": {},
    "scopes": list(ALLOWED_SCOPES),
}

MAX_RECOMMENDED_WINDOW_DAYS = 180


class ConfigError(Exception):
    pass


def validate_scopes(scopes) -> list[str]:
    """Refuse anything other than the two read-only scopes."""
    scopes = list(scopes or [])
    extra = [s for s in scopes if s not in ALLOWED_SCOPES]
    if extra:
        raise ConfigError(
            "Refusing to run: config.yaml lists scopes other than the two read-only "
            f"scopes this tool needs: {extra}. Remove them and try again."
        )
    missing = [s for s in ALLOWED_SCOPES if s not in scopes]
    if missing:
        raise ConfigError(f"config.yaml is missing required scope(s): {missing}")
    return scopes


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"{path} not found. Run `python setup.py` first to create it."
        )
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    cfg = {**DEFAULTS, **raw}
    if cfg["auth_mode"] not in ("oauth", "service_account"):
        raise ConfigError("auth_mode must be 'oauth' or 'service_account'")
    if cfg["auth_mode"] == "service_account" and not cfg.get("impersonate_user"):
        raise ConfigError("service_account mode needs impersonate_user (an admin email)")
    try:
        cfg["window_days"] = int(cfg["window_days"])
    except (TypeError, ValueError):
        raise ConfigError("window_days must be a whole number")
    if cfg["window_days"] < 1:
        raise ConfigError("window_days must be at least 1")
    cfg["scopes"] = validate_scopes(cfg["scopes"])
    cfg["ou_filter"] = list(cfg.get("ou_filter") or [])
    cfg["usage_event_names"] = list(cfg.get("usage_event_names") or [])
    cfg["param_map"] = dict(cfg.get("param_map") or {})
    cfg["base_dir"] = str(path.resolve().parent)
    return cfg


def save_config(cfg: dict, path: str | Path) -> None:
    data = {k: cfg[k] for k in DEFAULTS if k in cfg}
    validate_scopes(data.get("scopes"))
    with Path(path).open("w") as f:
        f.write("# Written by setup.py. See config.example.yaml for what each field means.\n")
        yaml.safe_dump(data, f, sort_keys=False)
