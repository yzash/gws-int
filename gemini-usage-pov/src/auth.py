"""Build Google credentials for either the OAuth desktop flow or a service account."""
from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

from .config import ConfigError, validate_scopes


def _resolve(cfg: dict, key: str) -> Path:
    p = Path(cfg[key])
    return p if p.is_absolute() else Path(cfg.get("base_dir", ".")) / p


def get_credentials(cfg: dict, open_browser: bool = True):
    scopes = validate_scopes(cfg["scopes"])
    if cfg["auth_mode"] == "service_account":
        return _service_account_credentials(cfg, scopes)
    return _oauth_credentials(cfg, scopes, open_browser)


def _service_account_credentials(cfg: dict, scopes: list[str]):
    from google.oauth2 import service_account

    key_path = _resolve(cfg, "service_account_file")
    if not key_path.exists():
        raise ConfigError(f"Service account key not found: {key_path}")
    creds = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=scopes
    )
    return creds.with_subject(cfg["impersonate_user"])


def _oauth_credentials(cfg: dict, scopes: list[str], open_browser: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_path = _resolve(cfg, "credentials_file")
    token_path = _resolve(cfg, "token_file")
    creds = None

    if token_path.exists():
        stored = json.loads(token_path.read_text())
        # A token issued for different scopes is discarded rather than reused.
        if sorted(stored.get("scopes") or []) == sorted(scopes):
            creds = Credentials.from_authorized_user_info(stored, scopes)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(token_path, creds)
            return creds
        except Exception:
            creds = None  # fall through to a fresh sign-in

    if not client_path.exists():
        raise ConfigError(
            f"OAuth client file not found: {client_path}. Run `python setup.py`."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes)
    print("\nA browser window will open. Sign in as your Workspace super admin and click Allow.")
    kwargs = dict(
        port=0,
        authorization_prompt_message="If the browser did not open, visit this URL:\n{url}\n",
        success_message="Signed in. You can close this tab and return to the terminal.",
    )
    try:
        creds = flow.run_local_server(open_browser=open_browser, **kwargs)
    except webbrowser.Error:
        # No browser found on this machine: print the URL for the admin to open.
        creds = flow.run_local_server(open_browser=False, **kwargs)
    _save_token(token_path, creds)
    return creds


def _save_token(path: Path, creds) -> None:
    path.write_text(creds.to_json())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
