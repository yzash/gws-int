# Gemini Usage Tracker for Google Workspace

See who in your organization uses Gemini in Gmail, Docs, Sheets, Slides, Drive,
Chat, Meet, Calendar, Workflows and the Gemini app, how often, and when they last used it.

The tool reads your tenant's Gemini audit log (Admin SDK Reports API,
`gemini_in_workspace_apps`), joins it with your user directory so people with
zero usage appear too, and produces three CSV files plus a one-page dashboard.

Optionally it also reads the other Workspace audit logs (Drive, Meet, Chat, Calendar,
Classroom, sign-ins, third-party app grants, Chrome), Google's usage reports, licence
assignments and Chrome management data. The dashboard then opens with a **coverage map**:
every adoption metric in the catalog, marked as extracted, supported but empty, blocked
by Google (with the reason), or not exposed by Google's APIs at all.

- **Read-only.** By default two read-only permissions; each optional source adds its own
  (see [Data sources](#data-sources)). The tool refuses to run with any scope not on its fixed list.
- **Runs on your own computer.** Credentials, tokens and results never leave it.
  The only network calls go to Google (`googleapis.com` / `accounts.google.com`).
- **No prompt or response content.** The audit log does not contain it, and the tool does not try to infer it.

Time needed: about 30 minutes the first time, then one command per report.

---

## What you need

- A **super admin** account on the Workspace tenant.
- A **Gemini-capable edition**: Business Standard/Plus, Enterprise Standard/Plus, or a Gemini add-on.
- A Mac, Windows or Linux computer with **Python 3.11 or newer**.

### Install Python (skip if `python3 --version` shows 3.11+)

- **Mac:** download the installer from <https://www.python.org/downloads/> and run it.
- **Windows:** install "Python 3.12" from the Microsoft Store, or from python.org (tick **Add python.exe to PATH**).

In the commands below, Windows users type `python` where it says `python3`.

## 1. Get the tool and install its libraries

Open **Terminal** (Mac: Spotlight > "Terminal"; Windows: Start > "Terminal" or "PowerShell"),
go to this folder, and run:

```bash
cd path/to/gemini-usage-pov
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

You should now see `(.venv)` at the start of the prompt. Whenever you open a new
terminal later, run the `cd` and `activate` lines again.

## 2. Run the setup wizard

```bash
python setup.py
```

The wizard walks through eight steps, one at a time, tells you exactly where to
click, and checks each answer before moving on. Your answers are saved, so you
can stop with **Ctrl+C** and run it again later to continue.

| Step | What you do | Where |
| --- | --- | --- |
| 1 | Confirm you are a super admin with a Gemini-capable edition | — |
| 2 | Create a Google Cloud project named `gemini-usage-pov`; paste its **Project ID** | [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate) |
| 3 | Enable the **Admin SDK API** in that project | APIs & Services > Library > "Admin SDK API" > Enable |
| 4 | Choose how the tool signs in (OAuth is recommended) | — |
| 5a | *OAuth:* set up the consent screen (**Internal**, app name `Gemini Usage POV`), then create an OAuth client ID of type **Desktop app** and download the JSON | Google Auth Platform (older consoles: APIs & Services > OAuth consent screen), then APIs & Services > Credentials |
| 5b | *Service account:* create `gemini-usage-reader`, download a JSON key, then authorise its numeric Client ID for domain-wide delegation with the two scopes below | IAM & Admin > Service Accounts; Admin console > Security > Access and data control > API controls > Domain-wide delegation |
| 6 | Review the two read-only scopes | — |
| 7 | Choose the reporting window (default 28 days), whether to include suspended users (default no), and your time zone | — |
| 8 | Smoke test: one API call for the last 24 hours. You sign in in the browser and see how many records came back | — |

When the wizard asks for a downloaded file, you can drag it from your Downloads
folder into the terminal window and press Enter; it copies it into place.

**Which sign-in method?**

- **OAuth desktop client (recommended):** you sign in once as yourself in a browser. No domain-wide delegation.
  A refresh token is stored in `token.json` on your computer.
- **Service account:** for unattended runs later. Needs a key file (`service-account.json`) and an extra
  Admin console authorisation. Some organisations block key creation by policy.

**Scopes** (the tool asks for these and nothing else):

```
https://www.googleapis.com/auth/admin.reports.audit.readonly
https://www.googleapis.com/auth/admin.directory.user.readonly
```

## 3. Produce the report

```bash
python run.py
```

This fetches every Gemini event in the window, fetches the user list, writes the
files below into `output/`, and opens the dashboard in your browser.

| File | One row per | Columns |
| --- | --- | --- |
| `raw_events_YYYY-MM-DD.csv` | Gemini event | timestamp, user_email, app, feature, action, event_name, ip, event_type, other_params |
| `user_summary_YYYY-MM-DD.csv` | user | user_email, name, ou_path, suspended, total_actions, gmail, docs, sheets, slides, drive, chat, meet, calendar, workflows, gemini_app, other, distinct_features, active_days, first_used, last_used, tier |
| `org_summary_YYYY-MM-DD.csv` | metric | metric, value |
| `dashboard.html` (and `dashboard_YYYY-MM-DD.html`) | — | headline tiles, actions by app, users by tier, top 20 users, top 10 features, adoption by OU, zero-usage users by OU |
| `details_YYYY-MM-DD/*.csv` | varies | every table behind the dashboard sections (per-user Drive, Meet, Chat, Classroom, licences, coverage, …) |
| `raw_YYYY-MM-DD.json` (+ `raw_extra_YYYY-MM-DD.json.gz`) | — | the untouched API responses, so the report can be rebuilt without calling Google again |

The CSVs open directly in Google Sheets (File > Import > Upload) or Excel. The
dashboard is a single self-contained file: it can be opened anywhere or emailed as-is.

**Usage tiers** match the Admin console's Gemini reports: Zero (0 actions),
Low (1–4), Medium (5–19), High (20+).

### Other options

```bash
python run.py --dry-run                 # sign in and count records; writes nothing
python run.py --smoke-test              # last 24 hours only, shows the shape of the data
python run.py --days 90                 # a different window just for this run
python run.py --from-cache              # rebuild the report from the newest output/raw_*.json, offline
python run.py --compare output/org_summary_2026-08-28.csv   # show change vs an earlier run
python run.py --no-browser              # don't open a browser (sign-in URL is printed instead)
python run.py --pseudonymise            # replace emails, names, titles and IPs in all outputs, for sharing
python setup.py --modules               # change which optional data sources are collected
```

## Data sources

Change these with `python setup.py --modules` (it tells you which APIs to enable). If the
permissions change, the next `python run.py` asks you to sign in again and approve them.

| Module | What it adds | Extra permission | API to enable |
| --- | --- | --- | --- |
| *(always)* Gemini + directory | Gemini adoption, tiers, use cases, time patterns, groups, 2SV status | `admin.reports.audit.readonly`, `admin.directory.user.readonly` | Admin SDK API |
| `activity_logs` (on by default) | Drive, Meet, Chat, Calendar, Classroom, Keep, Workspace Studio, sign-in, OAuth-grant and Chrome audit logs | none | — |
| `usage_reports` | daily / 7-day / 30-day active users per app, emails sent/received, Drive volumes, last sign-in and interaction per user, dormant accounts | `admin.reports.usage.readonly` | — |
| `licensing` | licences per SKU, idle paid AI seats, heavy AI users without an AI licence | `apps.licensing` (Google offers **no read-only** licensing scope; the tool only lists) | Enterprise License Manager API |
| `chrome` | installed browser extensions (AI tools flagged), ChromeOS app running time, device active/idle/locked | `chrome.management.reports.readonly`, `chrome.management.telemetry.readonly` | Chrome Management API |

A source the tenant can't provide (wrong edition, API not enabled, feature not used) doesn't
stop the run: its dashboard section and coverage rows show exactly why.

**What Google's APIs do not expose at all** (shown as ❌ on the coverage map): visits to
external AI sites such as ChatGPT or Claude (needs Chrome Enterprise reporting to BigQuery or a
SIEM), NotebookLM outputs, Meet reactions, Classroom guardian summaries, users hitting Gemini
usage limits, and Docs comment resolution / suggestions.

**Data volume:** the Drive log records every file view, so on large tenants it can be big. Lower
`activity_window_days` or remove `drive` from `activity_applications` if a run is too slow.
Usage reports run 2–5 days behind, so the newest days in the window are skipped automatically.

### Settings (`config.yaml`)

The wizard writes this file; `config.example.yaml` explains every field. The ones you may want to change:

- `window_days` — how far back to look. Over 180 days prints a warning, because audit-log retention is limited.
- `include_suspended` — include suspended accounts in the summaries.
- `timezone` — used to count "active days" (e.g. `Asia/Singapore`).
- `ou_filter` — only report on some organizational units, e.g. `["/Sales", "/Engineering/APAC"]`. Sub-OUs are included.
- `usage_event_names` — which audit event names count as usage (default `feature_utilization`).
  The run log lists any other event names it sees so you can decide whether to add them.
- `param_map` — only needed if your data uses different parameter names (see below).

## How the numbers are built

- **Users in scope** = every user in your directory (minus suspended users unless included, limited by
  `ou_filter`), plus anyone who appears in the Gemini log but no longer exists in the directory
  (shown with OU `(not in directory)`).
  The directory API does not say who holds a Gemini license, so on tenants where only some users are
  licensed, use `ou_filter` or read "adoption %" as a share of all users.
- **Active user** = at least one Gemini action in the window.
- **Actions** = number of `feature_utilization` events. The "By app" columns add up to the total; anything
  whose app isn't recognised is counted under `other`.
- **Feature** = the event's `feature_source` parameter; **action** = its `action` parameter.
- **Active days** = distinct calendar days with at least one action, in your chosen time zone.

Google does not guarantee that parameter names are the same for every app. On every run the tool logs
the parameter names it found and warns about any it doesn't recognise; those values are kept in the
`other_params` column of `raw_events.csv`. If your data uses, say, `feature_name` instead of
`feature_source`, add to `config.yaml`:

```yaml
param_map:
  feature: [feature_name]
```

and rebuild without calling Google again: `python run.py --from-cache`.

**Checking against the Admin console:** the Admin console's Gemini usage reports (see Google's article
[Review Gemini usage in your organization](https://knowledge.workspace.google.com/admin/generative-ai/review-gemini-usage-in-your-organization))
show active users and usage tiers for the last 28 days. Run with the default 28-day window and compare; the numbers should be
within a few percent (differences come from the exact start/end time and audit-log delay of a few hours).

## Troubleshooting

**Zero records.** The three usual causes:

1. **No Gemini license** — only users with a Gemini-capable edition, add-on, or Workspace Labs produce events.
2. **No usage in the window** — or it is very recent; audit data can lag by a few hours. Try `--days 28`.
3. **Wrong account or scope** — you signed in with a non-super-admin account, or an old token. Delete
   `token.json` and run again.

**"Access blocked: … can only be used within its organization"** — you signed in with an account outside
the Workspace. Use your admin account for that tenant.

**"Google hasn't verified this app"** — expected for an Internal app you created yourself. Click *Continue*.

**`accessNotConfigured` / "Admin SDK API has not been used in project"** — step 3 was skipped or done in a
different project. Enable the Admin SDK API in the project that owns your OAuth client.

**`unauthorized_client` (service account)** — the domain-wide delegation entry is missing, has a different
Client ID, or lists different scopes. It can take several minutes to apply after saving.

**403 / "Not Authorized to access this resource"** — the signed-in or impersonated user is not a super admin.

**Something else** — run `python run.py --verbose` and share the output (it contains no credentials).

## Security and privacy

- Keep `credentials.json`, `service-account.json`, `token.json`, `config.yaml` and `output/` private. They are
  listed in `.gitignore` and must never be committed or emailed. `token.json` and key files are saved readable
  only by you.
- To revoke access at any time: delete `token.json`, and remove the OAuth client (or the service-account key and
  its domain-wide delegation entry) in the Cloud and Admin consoles.
- IP addresses appear only in `raw_events.csv` and the raw caches, never in the summaries or dashboard.
  (With `office_ip_ranges` set, only the share of activity from those ranges is reported.)
- The dashboard and CSVs show **per-user usage with email addresses**. With the optional modules this includes
  sign-in history, meeting attendance, file activity, third-party app grants and device usage. Per-user data
  like this may be subject to employee-monitoring and data-protection rules (for example Singapore's PDPA, GDPR
  in Europe). **Collect it only where authorised, and confirm with HR or legal before sharing results beyond IT.**
  `python run.py --pseudonymise` replaces emails, names, file and course titles and IPs in every output.
- For customer deployments: the customer's own admin creates the Cloud project and credentials and runs the tool.
  DevX never needs your credentials or raw data; share the CSVs or dashboard only if you choose to.

## For developers

```
setup.py            interactive wizard -> config.yaml
run.py              auth -> fetch -> transform -> write -> dashboard
src/config.py       config loading, scope guard
src/auth.py         OAuth desktop flow or service-account impersonation
src/fetch.py        Reports (audit + usage), Directory, Licensing, Chrome calls; retry/backoff; raw cache
src/transform.py    Gemini flatten, directory join, per-user and org summaries (pure, no network)
src/pipeline.py     builds every dashboard section from a cache
src/sections_*.py   Gemini deep-dive, Workspace audit-log sections, usage/licences/Chrome sections
src/coverage.py     the catalog of metrics and the coverage map
src/common.py       shared helpers, pseudonymiser
src/report.py       CSV writers, dashboard rendering
templates/          dashboard.html.j2, section.html.j2
tests/              pytest suite; runs offline on tests/fixtures/sample_cache.json and
                    tests/synthetic_extra.py (synthetic API-shaped records, used only by tests)
tools/              make_sample_fixture.py, anonymise_cache.py
```

```bash
python -m pytest -q
```

To test against the real shape of your tenant's data, anonymise a cache (emails become `user1@example.com`
and so on, IPs and IDs are replaced) and point the tests at it:

```bash
python tools/anonymise_cache.py output/raw_2026-09-25.json tests/fixtures/tenant_cache.json
```
