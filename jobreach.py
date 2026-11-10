#!/usr/bin/env python3
"""jobreach - find IT companies (near you + worldwide), draft a personal message, send your resume.

    python jobreach.py find            # 1. collect companies (local + global)
    python jobreach.py enrich          # 2. find a public contact e-mail for each
    python jobreach.py draft           # 3. write a personalised message per company -> drafts/
                                       #    (draft --redo rewrites unsent drafts after you change the templates)
       ... open leads.csv, read drafts/, type "yes" in the `approved` column ...
    python jobreach.py send            # 4. preview what would go out
    python jobreach.py send --live     #    actually send (approved rows only, daily cap, spaced out)
    python jobreach.py replies         # 5. mark who answered / bounced (reads your inbox)
    python jobreach.py followup --live # 6. one polite follow-up after N days of silence
    python jobreach.py status          # where everything stands
"""
import argparse
import os
import random
import smtplib
import sys
import time
import tomllib
from collections import Counter
from datetime import datetime, timedelta

import contacts
import mailer
import sources
from store import Store

YES = {"yes", "y", "true", "1", "ok"}


class App:
    def __init__(self, config_path):
        if not os.path.exists(config_path):
            sys.exit(f"{config_path} not found. Copy config.example.toml to config.toml and fill it in.")
        with open(config_path, "rb") as f:
            self.cfg = tomllib.load(f)
        self.base = os.path.dirname(os.path.abspath(config_path))
        self.store = Store(os.path.join(self.base, "leads.csv"))
        self.drafts = os.path.join(self.base, "drafts")
        self.me = self.cfg["me"]
        self.send_cfg = self.cfg["send"]
        self.resume = os.path.join(self.base, self.me["resume"])
        self.password = os.environ.get("JOBREACH_SMTP_PASSWORD", "")

    # ------------------------------------------------------------ 1. find
    def find(self, scope="all"):
        leads = []
        if scope in ("all", "local"):
            loc = self.cfg["local"]
            lat, lon = (loc["lat"], loc["lon"]) if "lat" in loc else sources.geocode(loc["location"])
            print(f"Local: IT companies within {loc['radius_km']} km of {loc['location']}")
            try:
                found = sources.local_osm(lat, lon, loc["radius_km"])
                print(f"  openstreetmap: {len(found)} companies")
                leads += found
            except Exception as e:
                print(f"  ! openstreetmap: skipped ({e})")
            if key := os.environ.get("GOOGLE_MAPS_API_KEY"):
                try:
                    found = sources.local_google(lat, lon, loc["radius_km"], key, loc.get(
                        "google_queries", ["software company", "IT company", "web development company"]))
                    print(f"  google_places: {len(found)} companies")
                    leads += found
                except Exception as e:
                    print(f"  ! google_places: skipped ({e})")
            leads.sort(key=lambda l: l.get("distance_km") or 0)  # nearest first
        if scope in ("all", "global"):
            g = self.cfg["global"]
            print(f"Global: remote openings matching {g['keywords']}")
            leads += sources.find_global(g["keywords"], g["sources"], g.get("max_age_days", 30),
                                         g.get("exclude_title", []), g.get("match_description", False))
        print(f"Added {self.store.add(leads)} new companies (duplicates skipped). Total: {len(self.store.rows)}")

    # ------------------------------------------------------------ 2. enrich
    def enrich(self, delay=1.0):
        todo = [r for r in self.store.rows if r["status"] == "new"]
        for i, r in enumerate(todo, 1):
            if not r["email"] and r["website"]:
                r["email"], r["email_quality"] = contacts.find_email(r["website"])
                time.sleep(delay)  # be polite to small company websites
            if r["email"]:
                r["status"] = "ready"
            else:
                r["status"] = "manual"
                r["notes"] = (r["notes"] + " | " if r["notes"] else "") + (
                    "no public e-mail: apply via job_url" if r["job_url"] else "no public e-mail: use website contact form / phone / walk in")
            print(f"  [{i}/{len(todo)}] {r['company'][:40]:40} {r['email'] or '-> manual'}")
            self.store.save()
        c = Counter(r["status"] for r in todo)
        print(f"E-mail found: {c['ready']}   needs manual approach: {c['manual']}")

    # ------------------------------------------------------------ 3. draft
    def _context(self, lead):
        me = self.me
        return {**lead, "name": me["name"], "role": me["role"], "pitch": me["pitch"], "my_email": me["email"],
                "skills": ", ".join(me["skills"]), "highlights": me.get("highlights", "").strip(),
                "phone": me.get("phone", ""), "links": me.get("links", ""),
                "city": self.cfg["local"]["location"].split(",")[0].strip(),
                "job_title": lead["job_title"] or me["role"]}

    def draft(self, redo=False):
        """Write one draft per company. redo=True rewrites every unsent draft after you change the templates."""
        t, n = self.cfg["templates"], 0
        for r in self.store.rows:
            exists = os.path.exists(mailer.draft_path(self.drafts, r))
            if redo and r["status"] in ("drafted", "manual") and exists:
                r["approved"] = ""  # the text changed, so it needs your approval again
            elif r["status"] not in ("ready", "manual") or exists:
                continue
            kind = "local" if r["scope"] == "local" else "global"
            ctx = self._context(r)
            mailer.write_draft(self.drafts, r, mailer.render(t[kind + "_subject"], ctx), mailer.render(t[kind + "_body"], ctx))
            if r["status"] == "ready":
                r["status"] = "drafted"
            n += 1
        self.store.save()
        print(f"Wrote {n} drafts to {self.drafts}")
        print('Next: read them, edit freely, then type "yes" in the `approved` column of leads.csv for the ones to send.')

    # ------------------------------------------------------------ 4. send
    def _budget(self):
        today = datetime.now().date().isoformat()
        used = sum(r["sent_at"].startswith(today) + r["followup_at"].startswith(today) for r in self.store.rows)
        return max(0, int(self.send_cfg["daily_limit"]) - used)

    def _deliver(self, queue, live, make):
        """Shared loop for send + followup. `make(lead)` -> (subject, body, in_reply_to, done_callback)."""
        budget = self._budget()
        if live and not os.path.exists(self.resume):
            sys.exit(f"Resume not found: {self.resume}")
        if live and not self.password and self.send_cfg.get("smtp_security", "starttls") != "none":
            sys.exit("Set the JOBREACH_SMTP_PASSWORD environment variable (for Gmail: an App Password).")
        batch = queue[:budget]
        print(f"{len(queue)} queued, daily budget left {budget} -> {'sending' if live else 'PREVIEW of'} {len(batch)}")
        for i, r in enumerate(batch):
            subject, body, reply_to, done = make(r)
            print(f"  {r['id']}  {r['email']:35} {subject}")
            if not live:
                continue
            msg = mailer.build_message(self.me, r["email"], subject, body, self.resume, reply_to)
            try:
                mailer.smtp_send(self.send_cfg, self.me, msg, self.password)
                done(r, msg["Message-ID"])
            except smtplib.SMTPRecipientsRefused:
                r["status"] = "bounced"
            except (smtplib.SMTPException, OSError) as e:
                self.store.save()
                sys.exit(f"Stopped: mail server error ({e}). Nothing else was sent.")
            self.store.save()  # saved after every message: a crash can never cause a double send
            if i < len(batch) - 1:
                time.sleep(random.uniform(self.send_cfg["min_gap_seconds"], self.send_cfg["max_gap_seconds"]))
        if not live and batch:
            print("Preview only. Add --live to send.")

    def send(self, live=False, test=False):
        queue = [r for r in self.store.rows if r["status"] == "drafted" and r["approved"].strip().lower() in YES and r["email"]]
        if test:  # send the first approved draft to yourself to see exactly what a company would receive
            if not queue:
                sys.exit("Nothing approved yet.")
            subject, body = mailer.read_draft(self.drafts, queue[0])
            msg = mailer.build_message(self.me, self.me["email"], "[TEST] " + subject, body, self.resume)
            mailer.smtp_send(self.send_cfg, self.me, msg, self.password)
            return print(f"Test message sent to {self.me['email']}")

        def make(r):
            def done(r, mid):
                r.update(status="sent", sent_at=datetime.now().isoformat(timespec="seconds"), message_id=mid)
            return (*mailer.read_draft(self.drafts, r), "", done)
        self._deliver(queue, live, make)

    # ------------------------------------------------------------ 5/6. replies + follow-up
    def replies(self):
        sent = [r for r in self.store.rows if r["status"] in ("sent", "followed_up")]
        if not self.password:
            sys.exit("Set JOBREACH_SMTP_PASSWORD to let me read your inbox for replies.")
        found = mailer.check_inbox(self.send_cfg, self.me, self.password, sent)
        for r in sent:
            if r["id"] in found:
                r["status"] = found[r["id"]]
                print(f"  {r['company']}: {r['status']}")
        self.store.save()
        print(f"{len(found)} updates out of {len(sent)} contacted.")

    def followup(self, live=False):
        cutoff = (datetime.now() - timedelta(days=self.send_cfg["followup_after_days"])).isoformat()
        queue = [r for r in self.store.rows if r["status"] == "sent" and r["sent_at"] and r["sent_at"] <= cutoff]

        def make(r):
            subject, _ = mailer.read_draft(self.drafts, r)
            body = mailer.render(self.cfg["templates"]["followup_body"], self._context(r)) + "\n"

            def done(r, mid):
                r.update(status="followed_up", followup_at=datetime.now().isoformat(timespec="seconds"))
            return "Re: " + subject, body, r["message_id"], done
        self._deliver(queue, live, make)

    def status(self):
        for scope in ("local", "global"):
            c = Counter(r["status"] for r in self.store.rows if r["scope"] == scope)
            print(f"{scope:7} " + ("  ".join(f"{k}={v}" for k, v in sorted(c.items())) or "-"))
        waiting = sum(r["status"] == "drafted" and r["approved"].strip().lower() not in YES for r in self.store.rows)
        print(f"Drafts waiting for your approval: {waiting}   |   sends left today: {self._budget()}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find").add_argument("--scope", choices=["all", "local", "global"], default="all")
    for name in ("enrich", "replies", "status"):
        sub.add_parser(name)
    sub.add_parser("draft").add_argument("--redo", action="store_true", help="rewrite all unsent drafts from the current templates")
    s = sub.add_parser("send")
    s.add_argument("--live", action="store_true", help="really send (default is a preview)")
    s.add_argument("--test", action="store_true", help="send the first approved draft to yourself")
    sub.add_parser("followup").add_argument("--live", action="store_true")
    a = p.parse_args(argv)
    app = App(a.config)
    if a.cmd == "find":
        app.find(a.scope)
    elif a.cmd == "send":
        app.send(a.live, a.test)
    elif a.cmd == "followup":
        app.followup(a.live)
    elif a.cmd == "draft":
        app.draft(a.redo)
    else:
        getattr(app, a.cmd)()


if __name__ == "__main__":
    main()
