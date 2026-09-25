"""Load and validate config.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

AUDIT = "https://www.googleapis.com/auth/admin.reports.audit.readonly"
DIRECTORY = "https://www.googleapis.com/auth/admin.directory.user.readonly"
USAGE = "https://www.googleapis.com/auth/admin.reports.usage.readonly"
LICENSING = "https://www.googleapis.com/auth/apps.licensing"
CHROME_REPORTS = "https://www.googleapis.com/auth/chrome.management.reports.readonly"
CHROME_TELEMETRY = "https://www.googleapis.com/auth/chrome.management.telemetry.readonly"

# The two scopes every run needs.
BASE_SCOPES = (AUDIT, DIRECTORY)
ALLOWED_SCOPES = BASE_SCOPES  # backwards-compatible name

# Optional modules and the extra scope(s) each one needs. All read-only except
# Licensing, for which Google offers no read-only scope (the tool only calls list).
MODULE_SCOPES = {
    "activity_logs": (),                       # same audit scope as Gemini
    "usage_reports": (USAGE,),
    "licensing": (LICENSING,),
    "chrome": (CHROME_REPORTS, CHROME_TELEMETRY),
}
PERMITTED_SCOPES = set(BASE_SCOPES) | {s for v in MODULE_SCOPES.values() for s in v}
NOT_READ_ONLY = {LICENSING}

DEFAULT_ACTIVITY_APPS = ["drive", "meet", "chat", "calendar", "login", "token",
                         "classroom", "keep", "workspace_studio", "chrome"]

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
    "modules": {"activity_logs": True, "usage_reports": False, "licensing": False, "chrome": False},
    "activity_applications": list(DEFAULT_ACTIVITY_APPS),
    "activity_window_days": None,          # defaults to window_days
    "gmail_sent_log": False,               # Gmail delivery log is very large; usage reports cover counts
    "licence_products": ["Google-Apps", "101047", "101031", "101034", "101037", "101038", "101039"],
    "ai_licence_keywords": ["gemini", "ai pro", "ai ultra", "ai expanded", "ai meetings", "ai security"],
    "ai_app_keywords": ["chatgpt", "openai", "claude", "anthropic", "copilot", "perplexity",
                        "gemini", "bard", "notebooklm", "jasper", "grammarly", "otter", "fireflies",
                        "notion", "writesonic", "character.ai", "deepseek", "mistral", "poe",
                        "quillbot", "magicschool", "brisk", "diffit", "curipod", "canva", "cursor"],
    "work_hours": {"start": 8, "end": 18, "days": [0, 1, 2, 3, 4]},
    "office_ip_ranges": [],
    "periods": [],
    "group_by": ["ou_path", "ou_level_1"],
    "custom_fields": [],
    "min_group_size": 10,
    "target_adoption_pct": 50,
    "use_case_map": {},
    "pseudonymise": False,
    "scopes": list(BASE_SCOPES),
}

MAX_RECOMMENDED_WINDOW_DAYS = 180


class ConfigError(Exception):
    pass


def scopes_for(modules: dict) -> list[str]:
    scopes = list(BASE_SCOPES)
    for name, enabled in (modules or {}).items():
        if enabled:
            for s in MODULE_SCOPES.get(name, ()):
                if s not in scopes:
                    scopes.append(s)
    return scopes


def validate_scopes(scopes, modules: dict | None = None) -> list[str]:
    """Refuse any scope outside the tool's fixed list."""
    scopes = list(scopes or [])
    extra = [s for s in scopes if s not in PERMITTED_SCOPES]
    if extra:
        raise ConfigError(
            "Refusing to run: config.yaml lists scopes this tool does not use: "
            f"{extra}. Remove them and try again."
        )
    missing = [s for s in BASE_SCOPES if s not in scopes]
    if missing:
        raise ConfigError(f"config.yaml is missing required scope(s): {missing}")
    if modules is not None:
        needed = [s for s in scopes_for(modules) if s not in scopes]
        if needed:
            raise ConfigError(
                f"Enabled modules need scope(s) {needed} that are not in config.yaml. "
                "Run `python setup.py` again to update permissions."
            )
    return scopes


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found. Run `python setup.py` first to create it.")
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    cfg = {**DEFAULTS, **raw}
    cfg["modules"] = {**DEFAULTS["modules"], **(raw.get("modules") or {})}
    cfg["work_hours"] = {**DEFAULTS["work_hours"], **(raw.get("work_hours") or {})}
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
    cfg["activity_window_days"] = int(cfg["activity_window_days"] or cfg["window_days"])
    cfg["scopes"] = validate_scopes(cfg["scopes"], cfg["modules"])
    for key in ("ou_filter", "usage_event_names", "activity_applications", "licence_products",
                "ai_licence_keywords", "ai_app_keywords", "office_ip_ranges", "periods",
                "group_by", "custom_fields"):
        cfg[key] = list(cfg.get(key) or [])
    cfg["param_map"] = dict(cfg.get("param_map") or {})
    cfg["use_case_map"] = dict(cfg.get("use_case_map") or {})
    cfg["base_dir"] = str(path.resolve().parent)
    return cfg


def save_config(cfg: dict, path: str | Path) -> None:
    data = {k: cfg[k] for k in DEFAULTS if k in cfg}
    validate_scopes(data.get("scopes"), data.get("modules"))
    with Path(path).open("w") as f:
        f.write("# Written by setup.py. See config.example.yaml for what each field means.\n")
        yaml.safe_dump(data, f, sort_keys=False)
