"""Offline end-to-end test: fake job-board/map APIs, a fake company website, a fake SMTP server."""
import email, http.server, os, shutil, socketserver, sys, tempfile, threading, unittest
from datetime import datetime, timedelta
from email import policy
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contacts, jobreach, mailer, net, sources
from store import Store

NOW = datetime.now().timestamp()
SITE = {
    "/": '<a href="/contact-us">Contact</a> <a href="/careers/">Jobs</a> <a href="https://x.com/a">x</a> theme by dev@themeshop.io <img src="logo@2x.png">',
    "/contact-us": 'Write to <a href="mailto:info@acme-infotech.in">us</a> or sales [at] acme-infotech [dot] in',
    "/careers/": '<a class="__cf_email__" data-cfemail="{cf}">[email protected]</a>',
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
}
def cf_encode(addr, key=0x42):
    return bytes([key] + [ord(c) ^ key for c in addr]).hex()

class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SITE.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write((body or "").encode())
    def log_message(self, *a): pass

INBOX = []
class FakeSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write(s.encode() + b"\r\n")
        w("220 fake")
        while line := self.rfile.readline():
            cmd = line[:4].upper()
            if cmd in (b"EHLO", b"HELO"): w("250 fake")
            elif cmd == b"RCPT" and b"refuse" in line: w("550 no such user")
            elif cmd == b"DATA":
                w("354 go"); data = b""
                while (l := self.rfile.readline()) != b".\r\n": data += l
                INBOX.append(email.message_from_bytes(data, policy=policy.default)); w("250 ok")
            elif cmd == b"QUIT": w("221 bye"); return
            else: w("250 ok")

WEB = "http://acme-infotech.in"
REAL_FETCH = net.fetch
def site_fetch(url, **kw):
    """Route the pretend company domain to the local test web server."""
    body, ctype, final = REAL_FETCH(url.replace(WEB, LOCAL), **kw)
    return body, ctype, final.replace(LOCAL, WEB)

def api(url, **kw):
    """Canned responses shaped like the real APIs."""
    if "overpass" in url:
        return {"elements": [
            {"type": "node", "id": 1, "lat": 23.03, "lon": 72.58, "tags": {"name": "Acme Infotech Pvt Ltd", "office": "it", "website": WEB, "phone": "+91 1"}},
            {"type": "way", "id": 2, "center": {"lat": 23.05, "lon": 72.60}, "tags": {"name": "Bolt Software", "office": "it", "contact:email": "careers@bolt.example.org;x@y.org"}},
            {"type": "node", "id": 3, "lat": 23.0, "lon": 72.5, "tags": {"name": "NoSite Systems", "office": "company"}},
            {"type": "node", "id": 4, "lat": 23.0, "lon": 72.5, "tags": {"office": "it"}}]}
    if "nominatim" in url: return [{"lat": "23.0225", "lon": "72.5714"}]
    if "remotive" in url:
        return {"jobs": [
            {"company_name": "Globex", "title": "React Developer", "url": "https://remotive.com/j/1", "tags": ["react"], "publication_date": datetime.now().isoformat(), "description": "<p>Send CV to jobs@globex.io</p>", "candidate_required_location": "Worldwide"},
            {"company_name": "Globex", "title": "Senior React Engineer", "url": "https://remotive.com/j/2", "tags": ["react"], "publication_date": datetime.now().isoformat(), "description": ""},
            {"company_name": "OldCo", "title": "React Dev", "url": "u", "tags": [], "publication_date": "2020-01-01T00:00:00", "description": ""},
            {"company_name": "JavaCo", "title": "Java Developer", "url": "u", "tags": ["java"], "publication_date": datetime.now().isoformat(), "description": "we also use react"}]}
    if "remoteok" in url:
        return [{"legal": "notice"}, {"company": "Initech", "position": "Frontend (React)", "url": "https://remoteok.com/j/9", "tags": ["react", "js"], "epoch": int(NOW), "description": "Apply online"}]
    if "arbeitnow" in url:
        return {"data": [{"company_name": "Hooli GmbH", "title": "Python Engineer", "url": "https://arbeitnow.com/j/5", "tags": ["python"], "remote": True, "created_at": int(NOW), "description": "", "location": "Berlin"},
                         {"company_name": "Onsite AG", "title": "Python Engineer", "url": "u", "tags": ["python"], "remote": False, "created_at": int(NOW), "description": ""}]}
    if "jobicy" in url: raise OSError("board is down")
    if "search_by_date" in url:
        return {"hits": [{"title": "Ask HN: Who wants to be hired? (November 2026)", "objectID": "100"}, {"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "200"}]}
    if "hn.algolia" in url:
        assert "story_200" in url
        return {"hits": [
            {"objectID": "201", "parent_id": 200, "story_id": 200, "created_at_i": int(NOW), "comment_text": 'Pied Piper | Backend Engineer (Python) | Remote<p>We do compression. <a href="https:&#x2F;&#x2F;piedpiper.com">https:&#x2F;&#x2F;piedpiper.com</a><p>Email richard (at) piedpiper (dot) com'},
            {"objectID": "202", "parent_id": 201, "story_id": 200, "created_at_i": int(NOW), "comment_text": "Is this python role remote?"}]}
    raise AssertionError(url)

class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global LOCAL
        cls.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
        for s in (cls.web, cls.smtp): threading.Thread(target=s.serve_forever, daemon=True).start()
        LOCAL = f"http://127.0.0.1:{cls.web.server_port}"
        SITE["/careers/"] = SITE["/careers/"].format(cf=cf_encode("careers@acme-infotech.in"))
        cls.dir = tempfile.mkdtemp()
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(src, "config.example.toml")) as f: cfg = f.read()
        cfg = (cfg.replace('smtp_host = "smtp.gmail.com"', 'smtp_host = "127.0.0.1"').replace("smtp_port = 587", f"smtp_port = {cls.smtp.server_address[1]}")
                  .replace('smtp_security = "starttls"', 'smtp_security = "none"').replace("min_gap_seconds = 60", "min_gap_seconds = 0")
                  .replace("max_gap_seconds = 180", "max_gap_seconds = 0").replace("daily_limit = 20", "daily_limit = 3"))
        cls.cfg = os.path.join(cls.dir, "config.toml")
        with open(cls.cfg, "w") as f: f.write(cfg)
        with open(os.path.join(cls.dir, "resume.pdf"), "wb") as f: f.write(b"%PDF-1.4 fake resume")
    @classmethod
    def tearDownClass(cls):
        cls.web.shutdown(); cls.smtp.shutdown(); shutil.rmtree(cls.dir)

    def app(self): return jobreach.App(self.cfg)
    def by_company(self, app): return {r["company"]: r for r in app.store.rows}

    def test_1_email_extraction(self):
        got = contacts.extract_emails('a hr@acme.in b <img src="x@2x.png"> noreply@acme.in bob [at] acme [dot] in ' + f'data-cfemail="{cf_encode("jobs@acme.in")}"')
        self.assertEqual(sorted(got), ["bob@acme.in", "hr@acme.in", "jobs@acme.in"])
        self.assertEqual(contacts.pick_best(["bob@acme.in", "info@acme.in", "hr@acme.in"], "acme.in"), ("hr@acme.in", "hiring"))
        self.assertEqual(contacts.pick_best(["dev@themeshop.io", "info@acme.in"], "acme.in"), ("info@acme.in", "general"))
        self.assertEqual(contacts.pick_best(["dev@themeshop.io"], "acme.in"), ("", ""))          # third-party address dropped
        self.assertEqual(contacts.pick_best(["acmeinfotech@gmail.com"], "acme.in")[0], "acmeinfotech@gmail.com")  # small firms use gmail
        self.assertEqual(contacts.pick_best(["hrishi@acme.in"], "acme.in")[1], "person")          # 'hr' prefix is not HR

    def test_2_find_enrich_draft(self):
        with mock.patch.object(net, "get_json", api):
            app = self.app(); app.find()
            n = len(app.store.rows); app.find()            # second run adds nothing
            self.assertEqual(len(app.store.rows), n)
        rows = self.by_company(app)
        self.assertEqual(set(rows), {"Acme Infotech Pvt Ltd", "Bolt Software", "NoSite Systems", "Globex", "Initech", "Hooli GmbH", "Pied Piper"})
        self.assertEqual(rows["Globex"]["job_title"], "React Developer")       # one row per company, senior role excluded
        self.assertEqual(rows["Globex"]["email"], "jobs@globex.io")
        self.assertEqual(rows["Pied Piper"]["email"], "richard@piedpiper.com")
        self.assertEqual(rows["Pied Piper"]["website"], "https://piedpiper.com")
        self.assertEqual(rows["Bolt Software"]["email"], "careers@bolt.example.org")
        self.assertLess(float(rows["Acme Infotech Pvt Ltd"]["distance_km"]), 2)

        with mock.patch.object(net, "fetch", site_fetch):      # real HTTP crawl of the fake company site
            app.enrich(delay=0)
        rows = self.by_company(app)
        self.assertEqual(rows["Acme Infotech Pvt Ltd"]["status"], "ready")
        self.assertEqual((rows["Acme Infotech Pvt Ltd"]["email"], rows["Acme Infotech Pvt Ltd"]["email_quality"]), ("careers@acme-infotech.in", "hiring"))
        self.assertEqual(rows["NoSite Systems"]["status"], "manual")
        self.assertEqual(rows["Initech"]["status"], "manual")
        self.assertIn("apply via job_url", rows["Initech"]["notes"])

        app.draft()
        rows = self.by_company(app)
        subj, body = mailer.read_draft(app.drafts, rows["Globex"])
        self.assertEqual(subj, "Application: React Developer - Your Name")
        self.assertIn("https://remotive.com/j/1", body)
        self.assertNotIn("{", body)
        subj, body = mailer.read_draft(app.drafts, rows["Bolt Software"])
        self.assertIn("based in Ahmedabad", subj); self.assertIn("Hello Bolt Software team", body)
        self.assertTrue(os.path.exists(mailer.draft_path(app.drafts, rows["Initech"])))   # manual leads get a draft to paste

    def test_3_send_respects_approval_and_limit(self):
        app = self.app()
        app.send(live=True)                                   # nothing approved -> nothing sent
        self.assertEqual(INBOX, [])
        for r in app.store.rows:
            if r["status"] == "drafted": r["approved"] = "yes"
        app.store.save()
        app.send(live=False)                                  # preview sends nothing
        self.assertEqual(INBOX, [])
        app.send(live=True)
        self.assertEqual(len(INBOX), 3)                       # 4 approved, daily_limit = 3
        m = INBOX[0]
        self.assertEqual(m["From"], "Your Name <you@gmail.com>")
        att = list(m.iter_attachments())
        self.assertEqual((att[0].get_filename(), att[0].get_content()), ("resume.pdf", b"%PDF-1.4 fake resume"))
        app = self.app(); app.send(live=True)                 # same day: budget exhausted, no double send
        self.assertEqual(len(INBOX), 3)
        self.assertEqual(sum(r["status"] == "sent" for r in app.store.rows), 3)

    def test_4_replies_and_followup(self):
        app = self.app()
        sent = [r for r in app.store.rows if r["status"] == "sent"]
        old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        for r in sent: r["sent_at"] = old
        app.store.save()
        imap = mock.MagicMock(); imap.__enter__.return_value = imap
        imap.search.side_effect = lambda _, q: ("OK", [b"7" if (sent[0]["email"] in q and "mailer-daemon" not in q) else b""])
        with mock.patch.object(mailer.imaplib, "IMAP4_SSL", return_value=imap), mock.patch.dict(os.environ, {"JOBREACH_SMTP_PASSWORD": "x"}):
            a = self.app(); a.send_cfg["smtp_security"] = "none"; a.replies()
        app = self.app()
        self.assertEqual(sum(r["status"] == "replied" for r in app.store.rows), 1)
        INBOX.clear(); app.followup(live=True)
        self.assertEqual(len(INBOX), 2)                       # the one who replied is left alone
        f = INBOX[0]
        orig = next(r for r in app.store.rows if r["email"] == f["To"])
        self.assertTrue(f["Subject"].startswith("Re: ")); self.assertEqual(f["In-Reply-To"], orig["message_id"])
        INBOX.clear(); self.app().followup(live=True)
        self.assertEqual(INBOX, [])                           # only ever one follow-up

if __name__ == "__main__":
    unittest.main(verbosity=2)
