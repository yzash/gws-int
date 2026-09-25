#!/usr/bin/env python3
"""Interactive setup wizard for the Gemini Usage Tracker.

Walks a Workspace super admin through the Google Cloud and Admin console steps
one at a time, checks each answer, writes config.yaml, then runs a smoke test.
Run it again at any time: previous answers are offered as defaults.

    python setup.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from src.config import (AUDIT, CHROME_REPORTS, CHROME_TELEMETRY, DEFAULTS, DIRECTORY, LICENSING,
                        MAX_RECOMMENDED_WINDOW_DAYS, USAGE, load_config, save_config, scopes_for)

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.yaml"
PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- helpers
def say(text: str = "") -> None:
    print(text)


def header(n, title: str) -> None:
    say()
    say("=" * 70)
    say(f" Step {n} - {title}")
    say("=" * 70)


def ask(prompt: str, default: str | None = None, validate=None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            value = input(f"\n> {prompt}{suffix}: ").strip()
        except EOFError:
            sys.exit("\nSetup cancelled.")
        if not value and default is not None:
            value = str(default)
        if not value:
            say("  Please enter a value.")
            continue
        if validate:
            error = validate(value)
            if error:
                say(f"  {error}")
                continue
        return value


def yes_no(prompt: str, default: bool | None = None) -> bool:
    d = {True: "y", False: "n", None: None}[default]
    while True:
        v = ask(f"{prompt} (y/n)", d).lower()
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False
        say("  Please answer y or n.")


def wait_for(prompt: str = "Type 'done' when you have finished this step") -> None:
    ask(prompt, validate=lambda v: None if v.lower() in ("done", "d", "y", "yes", "ok") else
        "Type 'done' to continue (or press Ctrl+C to stop and come back later).")


def private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _find_downloads(pattern: str) -> list[Path]:
    """Matching files in the usual download folders, newest first."""
    found: list[Path] = []
    for d in (Path.home() / "Downloads", Path.home() / "Desktop"):
        if d.is_dir():
            found.extend(d.glob(pattern))
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)


def _clean_path(v: str) -> Path:
    # Terminals quote dragged paths or escape their spaces with backslashes.
    v = v.strip().strip("'\"").replace("\\ ", " ")
    return Path(v).expanduser()


def place_file(target: Path, label: str, pattern: str | None = None) -> None:
    """Find the downloaded file (or let the admin give its path) and copy it into place."""
    if target.exists():
        if yes_no(f"{target.name} is already here. Use it?", True):
            private(target)
            return
    if pattern:
        for cand in _find_downloads(pattern)[:3]:
            say(f"\nFound {cand}")
            if yes_no("Is this the file you just downloaded?", True):
                shutil.copyfile(cand, target)
                private(target)
                say(f"  Copied to {target}")
                return
    say(f"\nI need the {label}. Either save it as:  {target}")
    say("or drag the file from Finder / File Explorer into this terminal window and press Enter.")
    while True:
        v = ask(f"Press Enter once {target.name} is in place, or drag/paste the downloaded file's path",
                default="")
        if v:
            src = _clean_path(v)
            if not src.exists() and any(c in str(src) for c in "*?"):
                matches = sorted(src.parent.glob(src.name), key=lambda p: p.stat().st_mtime, reverse=True)
                src = matches[0] if matches else src
            if not src.exists():
                say(f"  I can't find {src}.")
                if pattern:
                    say(f"  Its name should look like {pattern} (the * is a long ID). Dragging the file")
                    say("  into this window is the easiest way to get the exact path.")
                continue
            shutil.copyfile(src, target)
            say(f"  Copied to {target}")
        if target.exists():
            private(target)
            return
        say(f"  {target} does not exist yet.")


def load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as e:
        return None, f"{path.name} is not valid JSON ({e}). Download it again."
    except OSError as e:
        return None, str(e)


def check_oauth_client(path: Path) -> str | None:
    data, err = load_json(path)
    if err:
        return err
    if "web" in data:
        return ("This is a 'Web application' client. Create a new OAuth client ID with "
                "Application type = Desktop app and download that one instead.")
    if "installed" not in data:
        return "This does not look like an OAuth client file (no 'installed' section)."
    cid = data["installed"].get("client_id", "")
    if not cid.endswith(".apps.googleusercontent.com"):
        return f"The client ID '{cid}' does not look right (should end in .apps.googleusercontent.com)."
    if not data["installed"].get("client_secret"):
        return "The file has no client_secret. Download the JSON again from the Credentials page."
    return None


def check_service_account(path: Path) -> tuple[str | None, str | None]:
    data, err = load_json(path)
    if err:
        return err, None
    if data.get("type") != "service_account":
        return "This is not a service-account key file (type is not 'service_account').", None
    cid = str(data.get("client_id", ""))
    if not cid.isdigit():
        return "The key file has no numeric client_id.", None
    if not data.get("private_key"):
        return "The key file has no private key. Create a new JSON key.", None
    return None, cid


# ---------------------------------------------------------------- steps
def main() -> int:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update({k: v for k, v in load_config(CONFIG).items() if k in DEFAULTS})
            say(f"Found an existing {CONFIG.name}; your previous answers are shown as defaults.")
        except Exception as e:
            say(f"(Ignoring unreadable {CONFIG.name}: {e})")

    say("\nGemini Usage Tracker - setup")
    say("This takes about 20-30 minutes. You need a browser signed in as a")
    say("Workspace super admin. Press Ctrl+C at any time; re-run to resume.")

    # Step 1
    header(1, "Confirm admin access")
    say("This tool only reads audit logs, which requires a super admin, and Gemini")
    say("events only exist on Gemini-capable editions (Business Standard/Plus,")
    say("Enterprise Standard/Plus, or a Gemini add-on).")
    if not yes_no("Are you a super admin on this Workspace tenant?"):
        say("\nPlease ask a super admin to run this setup. Stopping.")
        return 1
    if not yes_no("Does the tenant have a Gemini-capable edition or Gemini add-on?"):
        say("\nWithout a Gemini-capable license there are no Gemini audit events to report. Stopping.")
        return 1

    # Step 2
    header(2, "Create a Google Cloud project")
    say("1. Open https://console.cloud.google.com/projectcreate")
    say("   (sign in with your Workspace admin account).")
    say("2. Project name: gemini-usage-pov")
    say("3. Organization / Location: your Workspace domain. Click Create.")
    say("4. The Project ID is shown under the name box (e.g. gemini-usage-pov or")
    say("   gemini-usage-pov-123456). You can also find it on the project dashboard.")
    cfg["project_id"] = ask(
        "Paste the Project ID", cfg.get("project_id") or None,
        validate=lambda v: None if PROJECT_ID_RE.match(v) else
        "Project IDs are 6-30 characters: lowercase letters, digits and hyphens, starting "
        "with a letter. (Paste the ID, not the project name or number.)")
    pid = cfg["project_id"]

    # Step 3
    header(3, "Enable the Admin SDK API")
    say(f"1. Open https://console.cloud.google.com/apis/library/admin.googleapis.com?project={pid}")
    say("2. Check the project picker at the top shows your project.")
    say("3. Click Enable. (If it says 'Manage' / 'API Enabled', it is already on.)")
    wait_for()

    # Step 4
    header(4, "Choose how the tool signs in")
    say("  1) OAuth desktop client  (recommended for this POV)")
    say("     You sign in once in a browser as yourself. No domain-wide delegation.")
    say("  2) Service account with domain-wide delegation")
    say("     Better for unattended runs later, but needs a key file and an extra")
    say("     Admin console authorisation.")
    choice = ask("Enter 1 or 2", "2" if cfg.get("auth_mode") == "service_account" else "1",
                 validate=lambda v: None if v in ("1", "2") else "Enter 1 or 2.")
    cfg["auth_mode"] = "oauth" if choice == "1" else "service_account"

    choose_modules(cfg, pid)
    scopes_csv = ",".join(scopes_for(cfg["modules"]))
    if cfg["auth_mode"] == "oauth":
        header("5a", "Create the OAuth desktop client")
        say("A) Consent screen (only needs doing once per project):")
        say(f"   Open https://console.cloud.google.com/auth/overview?project={pid}")
        say("   (older consoles: APIs & Services > OAuth consent screen).")
        say("   Click Get started. App name: Gemini Usage POV. Support email: your email.")
        say("   Audience: choose Internal. Contact email: your email. Finish / Create.")
        wait_for("Type 'done' once the consent screen is saved")
        say("\nB) Client ID:")
        say(f"   Open https://console.cloud.google.com/apis/credentials?project={pid}")
        say("   Click + Create credentials > OAuth client ID.")
        say("   Application type: Desktop app. Name: Gemini Usage POV. Click Create.")
        say("   In the dialog, click Download JSON.")
        target = BASE / cfg.get("credentials_file", "credentials.json")
        while True:
            place_file(target, "downloaded OAuth client JSON", "client_secret_*.json")
            err = check_oauth_client(target)
            if not err:
                say(f"  OK - {target.name} is a valid Desktop OAuth client.")
                break
            say(f"  Problem: {err}")
            target.unlink(missing_ok=True)
    else:
        header("5b", "Create the service account")
        say(f"1. Open https://console.cloud.google.com/iam-admin/serviceaccounts/create?project={pid}")
        say("   Name: gemini-usage-reader. Click Create and continue, skip the optional")
        say("   role steps, click Done. (It needs no Cloud IAM roles.)")
        say("2. Click the new service account > Keys tab > Add key > Create new key > JSON.")
        say("   A file downloads. (If key creation is blocked by an organisation policy,")
        say("   use the OAuth path instead, or ask your Cloud admin to allow it.)")
        target = BASE / cfg.get("service_account_file", "service-account.json")
        while True:
            place_file(target, "downloaded service-account key", f"{pid}-*.json")
            err, client_id = check_service_account(target)
            if not err:
                break
            say(f"  Problem: {err}")
            target.unlink(missing_ok=True)
        say(f"\n  OK - service account Client ID: {client_id}")
        header("5b (cont.)", "Authorise domain-wide delegation")
        say("1. Open https://admin.google.com/ac/owl/domainwidedelegation")
        say("   (Admin console > Security > Access and data control > API controls >")
        say("   Manage Domain Wide Delegation).")
        say("2. Click Add new and paste:")
        say(f"     Client ID:     {client_id}")
        say(f"     OAuth scopes:  {scopes_csv}")
        say("3. Click Authorise. (It can take a few minutes to take effect.)")
        wait_for()
        cfg["impersonate_user"] = ask(
            "Email of the super admin the service account should act as",
            cfg.get("impersonate_user") or None,
            validate=lambda v: None if EMAIL_RE.match(v) else "That does not look like an email address.")

    # Step 6
    header(6, "Scopes")
    cfg["scopes"] = scopes_for(cfg["modules"])
    say("The tool asks for exactly these permissions, nothing else:")
    for sc in cfg["scopes"]:
        say(f"  {sc}   ({SCOPE_PURPOSE.get(sc, '')})")
    say("It refuses to run if config.yaml ever lists any other scope.")

    # Step 7
    header(7, "Run parameters")

    def check_days(v):
        if not v.isdigit() or int(v) < 1:
            return "Enter a whole number of days, e.g. 28."
        return None

    days = int(ask("Reporting window in days", str(cfg.get("window_days", 28)), validate=check_days))
    if days > MAX_RECOMMENDED_WINDOW_DAYS:
        say(f"  Warning: audit-log retention is limited; more than {MAX_RECOMMENDED_WINDOW_DAYS} days "
            "will likely be incomplete.")
        if not yes_no(f"Keep {days} days anyway?", False):
            days = 28
    cfg["window_days"] = days
    cfg["include_suspended"] = yes_no("Include suspended users?", bool(cfg.get("include_suspended", False)))
    cfg["timezone"] = ask("Time zone for counting 'active days' (e.g. UTC, Asia/Singapore)",
                          cfg.get("timezone", "UTC"), validate=_check_tz)

    save_config(cfg, CONFIG)
    say(f"\nSaved {CONFIG}")

    # Step 8
    header(8, "Smoke test")
    say("Now one API call for the last 24 hours to prove everything works.")
    if cfg["auth_mode"] == "oauth":
        say("A browser will open: sign in as your super admin and click Allow.")
        say("(If you see 'Google hasn't verified this app', that is expected for an")
        say("Internal app you created yourself.)")
    from run import smoke_test
    from src.config import load_config as _load
    try:
        count = smoke_test(_load(CONFIG))
    except Exception as e:
        say(f"\nThe smoke test failed: {e}")
        say(_explain_error(e, cfg))
        say("Fix the step above and run `python setup.py` again (your answers are saved).")
        return 1
    if count == 0:
        say("Nothing is broken, but there is nothing to show yet. Try `python run.py --dry-run`")
        say("to count the full 28-day window.")
    if not yes_no("Does that look right?", True):
        say("Tell whoever supports you what looked wrong, together with the output above.")
        return 1
    say("\nSetup complete. From now on just run:   python run.py")
    return 0


SCOPE_PURPOSE = {
    AUDIT: "audit logs: Gemini, Drive, Meet, Chat, Calendar, Classroom, sign-ins, OAuth apps, Chrome - read-only",
    DIRECTORY: "user list, OUs, 2-Step Verification status - read-only",
    USAGE: "usage reports: active users per app, email and Drive volumes, last sign-in - read-only",
    LICENSING: "licence assignments - Google offers no read-only scope; the tool only lists",
    CHROME_REPORTS: "installed Chrome apps and extensions - read-only",
    CHROME_TELEMETRY: "ChromeOS device and app-usage telemetry - read-only",
}


def choose_modules(cfg: dict, pid: str) -> None:
    """Ask which optional data sources to collect; tell the admin which APIs to enable."""
    header("4b", "Choose what data to collect")
    mods = dict(cfg.get("modules") or {})
    say("Gemini usage and the user list are always collected. Optional extras:")
    say("")
    say("  A) Workspace audit logs: Drive, Meet, Chat, Calendar, Classroom, Keep, sign-ins,")
    say("     third-party app grants, Chrome. Uses the same permission as Gemini.")
    mods["activity_logs"] = yes_no("Collect Workspace audit logs?", mods.get("activity_logs", True))
    say("\n  B) Usage reports: daily/weekly/monthly active users per app, emails sent and received,")
    say("     Drive volumes, last sign-in per user. Adds one read-only permission.")
    mods["usage_reports"] = yes_no("Collect usage reports?", mods.get("usage_reports", False))
    say("\n  C) Licences: who holds which Workspace / Gemini licence, idle paid seats.")
    say("     Needs the licensing permission, which Google only offers as read+write")
    say("     (this tool only ever reads). Needs the Enterprise License Manager API.")
    mods["licensing"] = yes_no("Collect licence assignments?", mods.get("licensing", False))
    say("\n  D) Chrome management: installed extensions (e.g. AI tools) and ChromeOS app-usage")
    say("     telemetry. Only useful with managed Chrome browsers or Chromebooks.")
    say("     Adds two read-only permissions. Needs the Chrome Management API.")
    mods["chrome"] = yes_no("Collect Chrome management data?", mods.get("chrome", False))
    cfg["modules"] = mods
    apis = [("licensing", "licensing.googleapis.com", "Enterprise License Manager API"),
            ("chrome", "chromemanagement.googleapis.com", "Chrome Management API")]
    needed = [(api, name) for m, api, name in apis if mods.get(m)]
    if needed:
        say("\nEnable these APIs in your project too (click Enable on each page):")
        for api, name in needed:
            say(f"  {name}: https://console.cloud.google.com/apis/library/{api}?project={pid}")
        wait_for()
    if mods.get("activity_logs") or mods.get("usage_reports"):
        say("\nPer-person activity data can be subject to employee-monitoring rules. Make sure")
        say("collecting it is authorised for this tenant. Use `python run.py --pseudonymise`")
        say("to replace emails, names and titles in the output before sharing it.")


def update_modules() -> int:
    """`python setup.py --modules`: change data sources without redoing the whole setup."""
    if not CONFIG.exists():
        say("No config.yaml yet - run `python setup.py` first.")
        return 1
    cfg = load_config(CONFIG)
    old = set(cfg["scopes"])
    choose_modules(cfg, cfg.get("project_id", ""))
    cfg["scopes"] = scopes_for(cfg["modules"])
    save_config(cfg, CONFIG)
    say(f"\nSaved {CONFIG}")
    if set(cfg["scopes"]) != old:
        if cfg["auth_mode"] == "oauth":
            say("Permissions changed: the next `python run.py` opens the browser so you can")
            say("sign in again and approve the new permissions.")
        else:
            say("Permissions changed. Update the domain-wide delegation entry for your service account at")
            say("https://admin.google.com/ac/owl/domainwidedelegation with these scopes:")
            say("  " + ",".join(cfg["scopes"]))
    say("\nNow run:   python run.py")
    return 0


def _check_tz(v: str) -> str | None:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(v)
        return None
    except Exception:
        return "Unknown time zone. Use a name like UTC, Asia/Singapore or Europe/London."


def _explain_error(e: Exception, cfg: dict) -> str:
    text = str(e)
    if "unauthorized_client" in text:
        return ("  Domain-wide delegation is not authorised for this Client ID / these scopes yet. "
                "Re-check step 5b (and allow a few minutes for it to apply).")
    if "accessNotConfigured" in text or "has not been used in project" in text or "SERVICE_DISABLED" in text:
        return "  The Admin SDK API is not enabled in this project. Re-do step 3."
    if "403" in text or "Not Authorized" in text or "insufficient" in text.lower():
        return ("  The signed-in account is not allowed to read audit logs. Sign in as a super admin: "
                "delete token.json and run setup again.")
    if "access_denied" in text or "org_internal" in text:
        return "  Sign-in was refused. Make sure you use an account in this Workspace (consent screen is Internal)."
    return "  See the README's Troubleshooting section."


if __name__ == "__main__":
    try:
        sys.exit(update_modules() if "--modules" in sys.argv[1:] else main())
    except KeyboardInterrupt:
        print("\nSetup stopped. Run `python setup.py` again to resume.")
        sys.exit(130)
