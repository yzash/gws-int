#!/usr/bin/env python3
"""Turn a real output/raw_YYYY-MM-DD.json into a shareable, anonymised test fixture.

Emails become user1@example.com, user2@example.com ...; names become "User N";
IP addresses, profile IDs and customer IDs are replaced. Event names, apps,
features and parameter names are kept, since that shape is what the test needs.

    python tools/anonymise_cache.py output/raw_2026-09-25.json tests/fixtures/tenant_cache.json
"""
import json
import sys
from pathlib import Path


def main(src: str, dst: str) -> None:
    data = json.loads(Path(src).read_text())
    mapping: dict[str, str] = {}

    def anon(email: str) -> str:
        email = (email or "").lower()
        if not email:
            return email
        if email not in mapping:
            mapping[email] = f"user{len(mapping) + 1}@example.com"
        return mapping[email]

    for u in data.get("users", []):
        u["primaryEmail"] = anon(u.get("primaryEmail"))
        u["name"] = {"fullName": "User " + u["primaryEmail"].split("@")[0][4:]}
        for k in list(u):
            if k not in ("primaryEmail", "name", "orgUnitPath", "suspended", "archived", "isAdmin"):
                u.pop(k)
    for i, a in enumerate(data.get("activities", [])):
        actor = a.get("actor", {})
        actor["email"] = anon(actor.get("email"))
        actor.pop("profileId", None)
        a["ipAddress"] = f"203.0.113.{i % 250}"
        a.get("id", {})["customerId"] = "C0example"
        for ev in a.get("events", []):
            for p in ev.get("parameters", []):
                # Free-text values could contain names or document titles.
                if isinstance(p.get("value"), str) and "@" in p["value"]:
                    p["value"] = anon(p["value"])
    Path(dst).write_text(json.dumps(data, indent=1))
    print(f"wrote {dst}: {len(data.get('activities', []))} activities, {len(mapping)} users anonymised")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
