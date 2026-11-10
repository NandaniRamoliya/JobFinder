"""leads.csv: the single source of truth. Open it in Excel / Google Sheets to review."""
import csv
import os
import re
from urllib.parse import urlparse

FIELDS = [
    "id", "scope", "source", "company", "website", "email", "email_quality",
    "location", "distance_km", "job_title", "job_url",
    "status",      # new -> ready | manual -> drafted -> sent -> followed_up | replied | bounced | skip
    "approved",    # YOU type "yes" here; nothing is sent without it
    "sent_at", "message_id", "followup_at", "notes",
]
FREEMAIL = {"gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com", "hotmail.com",
            "live.com", "rediffmail.com", "proton.me", "protonmail.com", "icloud.com", "aol.com"}
_SUFFIXES = {"pvt", "private", "ltd", "limited", "llp", "inc", "llc", "gmbh", "corp", "co", "plc"}


def site_domain(url):
    if not url:
        return ""
    host = urlparse(url if "//" in url else "//" + url).hostname or ""
    return host.lower().removeprefix("www.")


def company_slug(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return "".join(words)


def lead_keys(lead):
    """Identity keys used so the same company is never contacted twice."""
    keys = set()
    if d := site_domain(lead.get("website")):
        keys.add("d:" + d)
    if "@" in (lead.get("email") or ""):
        d = lead["email"].split("@")[1].lower()
        keys.add(("e:" + lead["email"].lower()) if d in FREEMAIL else ("d:" + d))
    if s := company_slug(lead.get("company")):
        keys.add("n:" + s)
    return keys


class Store:
    def __init__(self, path):
        self.path = path
        self.rows = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8-sig") as f:
                self.rows = [{k: (r.get(k) or "") for k in FIELDS} for r in csv.DictReader(f)]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(self.rows)
        os.replace(tmp, self.path)

    def add(self, leads):
        """Append leads that are not already known. Returns how many were added."""
        seen = set().union(*(lead_keys(r) for r in self.rows)) if self.rows else set()
        n = max((int(r["id"][1:]) for r in self.rows), default=0)
        added = 0
        for lead in leads:
            keys = lead_keys(lead)
            if not lead.get("company") or not keys or keys & seen:
                continue
            seen |= keys
            n += 1
            row = {k: "" if lead.get(k) is None else str(lead[k]) for k in FIELDS}
            row.update(id=f"L{n:04d}", status="new", approved="")
            self.rows.append(row)
            added += 1
        self.save()
        return added
