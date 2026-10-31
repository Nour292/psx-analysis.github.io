"""Pull PSX company announcements for the stocks on tickers/list.json and flag the important ones.

Writes tickers/news.json: per stock the latest announcements (last 30 days, noise removed),
keyword flags, and the latest quarterly profit against the same quarter a year earlier.
Standard library only. Run: python scripts/pull_news.py
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "tickers", "list.json")
OUT = os.path.join(ROOT, "tickers", "news.json")
COMPANY_URL = "https://dps.psx.com.pk/company/"
UA = {"User-Agent": "Mozilla/5.0"}
DAYS = 30
MAX_ITEMS = 8

# (flag, pattern) in priority order. A title can carry several flags.
FLAGS = [
    ("Dividend", r"dividend|\bnil\b|entitlement|book closure"),
    ("Corporate action", r"takeover|take-over|offer|acquisition|merger|amalgamation|buy-?back|split|bonus|right (shares|issue)|equity injection|scheme of arrangement|delist"),
    ("Results", r"financial results|annual report|quarterly report"),
    ("Board meeting", r"board (of directors )?meeting|closed period"),
    ("Meeting", r"general meeting|\bAGM\b|\bEOGM\b|\bEGM\b"),
    ("Material", r"material information|price sensitive|clarification|unusual"),
    ("Regulator", r"SECP|show cause|penalt|investigation|suspension|default"),
    ("Management", r"change of (director|ceo|chief|company secretary)|resign|appointment"),
    ("Insider", r"disclosure of interest"),
]
NOISE = r"transmission of|newspaper|certified (true )?copy|extract(s)? of resolution|placement of .* on website|dissemination of video|presentation of corporate briefing"


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3)


def parse_announcements(page, today=None):
    """Rows look like <tr><td>Oct 27, 2026</td><td>Title</td><td>...<a href="/download/document/ID.pdf">."""
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=DAYS)
    seen, items = set(), []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 2:
            continue
        ds = html.unescape(re.sub(r"<[^>]+>", "", tds[0])).strip()
        try:
            d = datetime.strptime(ds, "%b %d, %Y").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", tds[1]))).strip()
        m = re.search(r"/download/document/(\d+)\.pdf", tr)
        doc = m.group(1) if m else ""
        key = (doc or title, ds)
        if key in seen or re.search(NOISE, title, re.I):
            continue
        seen.add(key)
        flags = [f for f, pat in FLAGS if re.search(pat, title, re.I)]
        items.append({"date": d.isoformat(), "title": title, "flags": flags, "doc": doc})
    items.sort(key=lambda i: (i["date"], i["doc"]), reverse=True)
    return items


def norm(page):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "|", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"(\| ?)+", "|", t)


def parse_num(q):
    q = q.strip()
    if not re.fullmatch(r"\(?-?[\d,]+\.?\d*\)?", q):
        return None
    neg = "(" in q or q.startswith("-")
    v = float(re.sub(r"[(),-]", "", q))
    return -v if neg else v


def parse_quarter(page):
    """Latest quarterly profit against the same quarter one year earlier (None if not shown)."""
    t = norm(page)
    i = t.find("All numbers in thousands")
    if i < 0:
        return None
    seg = t[i:i + 4000]
    m = re.search(r"\|((?:(?:Q\d|H\d|9M|Half)[^|]*\d{4}\|){2,5})", seg)
    if not m:
        return None
    labels = [x for x in m.group(1).split("|") if x]
    rest = seg[m.end():]
    k = rest.lower().find("profit after taxation")
    if k < 0:
        return None
    vals = []
    for q in rest[k:].split("|")[1:1 + len(labels)]:
        v = parse_num(q)
        if v is None:
            break
        vals.append(v)
    if len(vals) < len(labels):
        return None
    latest = labels[0]
    mm = re.match(r"(.*?)(\d{4})$", latest.strip())
    if not mm:
        return None
    prior_label = "%s%d" % (mm.group(1), int(mm.group(2)) - 1)
    if prior_label not in labels:
        return {"label": latest, "pat": vals[0], "prev": None, "change": None}
    prev = vals[labels.index(prior_label)]
    change = round((vals[0] - prev) / abs(prev) * 100, 1) if prev else None
    return {"label": latest, "pat": vals[0], "prev": prev, "change": change}


def main():
    with open(LIST, encoding="utf-8") as fh:
        syms = [r["symbol"] for r in json.load(fh)["rows"] if r["status"] in ("New", "Active", "Override", "Review")]

    def get(s):
        try:
            time.sleep(0.4)
            return s, fetch(COMPANY_URL + s)
        except Exception:
            return s, None

    with ThreadPoolExecutor(4) as ex:
        pages = dict(ex.map(get, syms))
    failed = [s for s in syms if pages.get(s) is None]
    if len(failed) > max(1, len(syms) // 10):
        raise SystemExit("Too many company pages failed (%d of %d). News not changed." % (len(failed), len(syms)))

    rows = []
    for s in syms:
        page = pages.get(s)
        if page is None:
            continue
        items = parse_announcements(page)
        flagged = [i for i in items if i["flags"]]
        rows.append({"symbol": s, "quarter": parse_quarter(page), "flagged": len(flagged), "items": items[:MAX_ITEMS]})
    rows.sort(key=lambda r: (-r["flagged"], r["symbol"]))
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "days": DAYS, "rows": rows}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("News for %d stocks | flagged items: %d" % (len(rows), sum(r["flagged"] for r in rows)))
    for r in rows:
        q = r["quarter"]
        print(r["symbol"], r["flagged"], (q["label"] + " " + str(q["change"]) + "%") if q else "-")


if __name__ == "__main__":
    main()
