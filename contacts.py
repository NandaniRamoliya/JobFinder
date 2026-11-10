"""Find a public contact e-mail on a company's own website.

Only addresses the company itself publishes are used. Nothing is guessed
(no "hr@<domain>" invention) - guessed addresses bounce and hurt your sender reputation.
"""
import html
import re
from urllib.parse import urljoin, urlparse

import net
from store import FREEMAIL, site_domain

EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_BAD_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico")
_BAD_DOMAINS = {"example.com", "domain.com", "email.com", "yourdomain.com", "company.com", "sentry.io",
                "wixpress.com", "sentry-next.wixpress.com", "schema.org", "w3.org", "godaddy.com"}
_BAD_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "abuse", "privacy", "postmaster",
              "webmaster", "unsubscribe", "mailer-daemon", "legal", "dpo", "security", "press", "billing"}
_HIRING = ("career", "job", "recruit", "talent", "hiring", "resume")
_HIRING_EXACT = {"hr", "cv", "people", "join", "work", "apply"}
_GENERIC = {"info", "hello", "contact", "hi", "team", "office", "admin", "mail", "connect",
            "enquiry", "enquiries", "inquiry", "inquiries", "business", "support", "sales"}
_LINK_HINT = re.compile(r"contact|career|job|join|about|team|hiring|work-with", re.I)


def _cf_decode(hexstr):
    """Decode Cloudflare's e-mail obfuscation (data-cfemail)."""
    try:
        b = bytes.fromhex(hexstr)
        return "".join(chr(c ^ b[0]) for c in b[1:])
    except ValueError:
        return ""


def extract_emails(text):
    text = html.unescape(text or "")
    found = [_cf_decode(h) for h in re.findall(r'(?:data-cfemail="|email-protection#)([0-9a-fA-F]{6,})', text)]
    text = re.sub(r"\s*[\[\(]\s*at\s*[\]\)]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(]\s*dot\s*[\]\)]\s*", ".", text, flags=re.I)
    found += EMAIL_RE.findall(text)
    out = []
    for e in found:
        e = e.strip(".").lower()
        if "@" not in e:
            continue
        local, dom = e.split("@", 1)
        if e.endswith(_BAD_EXT) or dom in _BAD_DOMAINS or local in _BAD_LOCAL or e in out:
            continue
        out.append(e)
    return out


def _score(email, domain):
    local, dom = email.split("@", 1)
    tokens = re.split(r"[._+\-]", local)
    if any(t.startswith(_HIRING) or t in _HIRING_EXACT for t in tokens):
        s, label = 3, "hiring"
    elif any(t in _GENERIC for t in tokens):
        s, label = 2, "general"
    else:
        s, label = 1, "person"
    same = bool(domain) and (dom == domain or dom.endswith("." + domain) or domain.endswith("." + dom))
    return s + (0.5 if same else 0), label, same


def pick_best(emails, domain=None):
    """Best address + quality label. `domain` set => drop third-party addresses (theme authors, vendors)."""
    best = None
    for e in emails:
        s, label, same = _score(e, domain or "")
        if domain and not same and e.split("@")[1] not in FREEMAIL:
            continue
        if best is None or s > best[0]:
            best = (s, e, label)
    return (best[1], best[2]) if best else ("", "")


def _robots_disallowed(base):
    """Paths disallowed for all agents ('*') in robots.txt. Missing/unreadable robots => nothing disallowed."""
    try:
        body = net.fetch(urljoin(base, "/robots.txt"), timeout=8, limit=100_000)[0].decode("utf-8", "ignore")
    except Exception:
        return []
    rules, applies = [], False
    for line in body.splitlines():
        key, _, val = line.split("#")[0].partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            applies = val == "*" or "jobreach" in val.lower()
        elif key == "disallow" and applies and val:
            rules.append(val)
    return rules


def _page(url):
    try:
        body, ctype, final = net.fetch(url, timeout=10, limit=400_000)
    except Exception:
        return "", url
    if ctype and "html" not in ctype and "text" not in ctype:
        return "", final
    return body.decode("utf-8", "ignore"), final


def find_email(website, max_pages=5):
    """Look at the homepage plus a few contact/careers/about pages. Returns (email, quality)."""
    if "//" not in website:
        website = "https://" + website
    home, final = _page(website)
    if not home:
        return "", ""
    host = urlparse(final).hostname or ""
    domain = site_domain(final)
    disallowed = _robots_disallowed(final)
    emails = extract_emails(home)

    links = []
    for href in re.findall(r'href=["\']([^"\'#]+)', home):
        url = urljoin(final, href)
        p = urlparse(url)
        if p.scheme in ("http", "https") and p.hostname == host and _LINK_HINT.search(p.path) and url not in links:
            links.append(url)
    if len(links) < 2:
        links += [urljoin(final, p) for p in ("/contact", "/careers")]

    for url in links[:max_pages]:
        if pick_best(emails, domain)[1] == "hiring":
            break
        if any(urlparse(url).path.startswith(d) for d in disallowed):
            continue
        emails += [e for e in extract_emails(_page(url)[0]) if e not in emails]
    return pick_best(emails, domain)
