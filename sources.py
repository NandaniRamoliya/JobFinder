"""Where leads come from.

LOCAL  = companies physically near you (speculative applications)
         - OpenStreetMap (free, no key)
         - Google Places (optional, better coverage; set GOOGLE_MAPS_API_KEY)
GLOBAL = companies with a live remote opening that matches your keywords
         - Remotive, RemoteOK, Arbeitnow, Jobicy, Hacker News "Who is hiring?"
"""
import html
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import net
from contacts import extract_emails, pick_best

# ---------------------------------------------------------------- helpers

def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def strip_html(s):
    s = re.sub(r"<\s*(p|br|li|div)[^>]*>", "\n", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _epoch(v):
    """Job boards use ints, ISO strings, 'YYYY-MM-DD HH:MM:SS'... normalise to a unix timestamp (0 = unknown)."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        d = datetime.fromisoformat(str(v).strip())
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


def _kw_match(text, keywords):
    text = text.lower()
    return any(re.search(r"(?<![a-z0-9+#.])" + re.escape(k.lower()) + r"(?![a-z0-9+#])", text) for k in keywords)


# ---------------------------------------------------------------- local

def geocode(place):
    r = net.get_json("https://nominatim.openstreetmap.org/search?" + urlencode({"q": place, "format": "jsonv2", "limit": 1}))
    if not r:
        raise SystemExit(f"Could not find '{place}' on the map. Set lat/lon in config.toml instead.")
    return float(r[0]["lat"]), float(r[0]["lon"])


_OSM_NAME = "soft|tech|info|digital|system|solution|labs|web|data|cloud|cyber|comput|code|app"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def local_osm(lat, lon, radius_km):
    around = f"(around:{int(radius_km * 1000)},{lat},{lon})"
    query = (
        "[out:json][timeout:60];("
        f'nwr["office"~"^(it|software)$"]{around};'
        f'nwr["office"="company"]["name"~"{_OSM_NAME}",i]{around};'
        f'nwr["company"~"^(it|software)$"]{around};'
        ");out center tags;"
    )
    data, err = None, None
    for url in OVERPASS:
        try:
            data = net.get_json(url, data={"data": query}, timeout=90)
            break
        except Exception as e:  # try the mirror
            err = e
    if data is None:
        raise RuntimeError(f"OpenStreetMap query failed: {err}")
    leads = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        pos = el if "lat" in el else el.get("center", {})
        if not t.get("name") or "lat" not in pos:
            continue
        leads.append({
            "scope": "local", "source": "openstreetmap", "company": t["name"],
            "website": t.get("website") or t.get("contact:website") or t.get("url") or "",
            "email": (t.get("email") or t.get("contact:email") or "").split(";")[0].strip(),
            "email_quality": "listed" if (t.get("email") or t.get("contact:email")) else "",
            "location": ", ".join(x for x in (t.get("addr:street"), t.get("addr:city")) if x),
            "distance_km": round(km_between(lat, lon, pos["lat"], pos["lon"]), 1),
            "notes": t.get("phone") or t.get("contact:phone") or "",
        })
    return leads


def local_google(lat, lon, radius_km, api_key, queries):
    """Google Places API (New) text search. Optional: needs a Google Cloud API key."""
    leads, seen = [], set()
    headers = {
        "Content-Type": "application/json", "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.websiteUri,places.formattedAddress,"
                            "places.location,places.nationalPhoneNumber,nextPageToken",
    }
    for q in queries:
        body = {"textQuery": q, "pageSize": 20, "locationBias": {"circle": {
            "center": {"latitude": lat, "longitude": lon}, "radius": min(radius_km * 1000, 50000)}}}
        for _ in range(3):  # Google returns at most 3 pages (60 places) per query
            r = net.get_json("https://places.googleapis.com/v1/places:searchText",
                             data=json.dumps(body).encode(), headers=headers)
            for p in r.get("places", []):
                loc = p.get("location", {})
                d = km_between(lat, lon, loc.get("latitude", lat), loc.get("longitude", lon))
                if p.get("id") in seen or d > radius_km:
                    continue
                seen.add(p.get("id"))
                leads.append({
                    "scope": "local", "source": "google_places",
                    "company": p.get("displayName", {}).get("text", ""),
                    "website": p.get("websiteUri", ""), "location": p.get("formattedAddress", ""),
                    "distance_km": round(d, 1), "notes": p.get("nationalPhoneNumber", ""),
                })
            if not r.get("nextPageToken"):
                break
            body["pageToken"] = r["nextPageToken"]
    return leads


# ---------------------------------------------------------------- global

def _job(source, company, title, url, desc="", tags=(), location="", posted=0, website=""):
    return {"scope": "global", "source": source, "company": (company or "").strip(), "job_title": (title or "").strip(),
            "job_url": url or "", "website": website, "location": location or "Remote",
            "_text": f"{title} {' '.join(tags or ())}", "_desc": strip_html(desc), "_posted": _epoch(posted)}


def _remotive(kw):
    r = net.get_json("https://remotive.com/api/remote-jobs?category=software-dev")
    return [_job("remotive", j.get("company_name"), j.get("title"), j.get("url"), j.get("description"),
                 j.get("tags"), j.get("candidate_required_location"), j.get("publication_date")) for j in r.get("jobs", [])]


def _remoteok(kw):
    r = net.get_json("https://remoteok.com/api")
    return [_job("remoteok", j.get("company"), j.get("position"), j.get("url"), j.get("description"),
                 j.get("tags"), j.get("location"), j.get("epoch") or j.get("date")) for j in r if j.get("position")]


def _arbeitnow(kw):
    r = net.get_json("https://www.arbeitnow.com/api/job-board-api")
    return [_job("arbeitnow", j.get("company_name"), j.get("title"), j.get("url"), j.get("description"),
                 j.get("tags"), j.get("location"), j.get("created_at")) for j in r.get("data", []) if j.get("remote")]


def _jobicy(kw):
    out = []
    for k in kw:
        r = net.get_json("https://jobicy.com/api/v2/remote-jobs?" + urlencode({"count": 50, "tag": k}))
        out += [_job("jobicy", j.get("companyName"), j.get("jobTitle"), j.get("url"),
                     j.get("jobDescription") or j.get("jobExcerpt"), [k], j.get("jobGeo"), j.get("pubDate"))
                for j in r.get("jobs", [])]
    return out


def _hn(kw):
    """Latest 'Ask HN: Who is hiring?' thread - posts are written by the hiring team and often include an e-mail."""
    api = "https://hn.algolia.com/api/v1/"
    stories = net.get_json(api + "search_by_date?tags=story,author_whoishiring&hitsPerPage=10").get("hits", [])
    story = next((s for s in stories if "who is hiring" in (s.get("title") or "").lower()), None)
    if not story:
        return []
    sid, out = int(story["objectID"]), []
    for k in kw:
        r = net.get_json(api + "search?" + urlencode({"tags": f"comment,story_{sid}", "query": k, "hitsPerPage": 100}))
        for c in r.get("hits", []):
            if c.get("parent_id") != sid:  # top-level comments are the job posts; replies are chatter
                continue
            raw = c.get("comment_text") or ""
            text = strip_html(raw)
            head = [p.strip() for p in text.split("\n")[0].split("|")]
            if not head[0] or len(head[0]) > 60:
                continue
            links = re.findall(r'href="(https?://[^"]+)"', html.unescape(raw))
            out.append(_job("hn_whoishiring", head[0], head[1] if len(head) > 1 else "",
                            f"https://news.ycombinator.com/item?id={c.get('objectID')}", raw, [k],
                            "", c.get("created_at_i"), links[0] if links else ""))
    return out


GLOBAL_SOURCES = {"remotive": _remotive, "remoteok": _remoteok, "arbeitnow": _arbeitnow, "jobicy": _jobicy, "hn": _hn}


def find_global(keywords, sources, max_age_days=30, exclude_title=(), match_description=False, now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    leads = []
    for name in sources:
        try:
            jobs = GLOBAL_SOURCES[name](keywords)
        except Exception as e:  # one board being down must not stop the others
            print(f"  ! {name}: skipped ({e})")
            continue
        kept, seen = 0, set()
        for j in jobs:
            if j["job_url"] in seen:  # the same post can come back once per keyword
                continue
            seen.add(j["job_url"])
            text = j.pop("_text") + (" " + j["_desc"] if match_description else "")
            desc, posted = j.pop("_desc"), j.pop("_posted")
            if not _kw_match(text, keywords) or _kw_match(j["job_title"], exclude_title):
                continue
            if posted and now - posted > max_age_days * 86400:
                continue
            j["email"], j["email_quality"] = pick_best(extract_emails(desc))
            leads.append(j)
            kept += 1
        print(f"  {name}: {kept} matching openings")
    return leads
