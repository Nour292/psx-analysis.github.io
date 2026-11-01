"""Pull news (last 3 months), EPS and dividends per share (last fiscal years) for every stock any user holds.

Holdings come from each "<user>-stock" tab of the PSX Analysis sheet (net units > 0).
Only the stock symbols are saved, never who holds what. The web app picks the stocks of the logged-in user.
EPS: PSX company page (psx) and TradingView scanner (tv). Dividends per share: TradingView scanner only.
Writes tickers/held.json. Standard library only. Run: python scripts/pull_held.py
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pull_news as pn  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tickers", "held.json")
SHEET = "1XTDO4sR5lFCUae8lHfsx96P8hLoXA7j2yeZMOiopuT8"
KEY = "AIzaSyBfjtlvDvGxmS1OcqFVGNMMadqza1ofCrI"
REFERER = {"Referer": "https://nour292.github.io/psx-analysis.github.io/"}
NEWS_DAYS = 92
MAX_NEWS = 60
KEEP_YEARS = 6


def sheets(path):
    req = urllib.request.Request("https://sheets.googleapis.com/v4/spreadsheets/%s%s" % (SHEET, path), headers=REFERER)
    return json.load(urllib.request.urlopen(req, timeout=40))


def held_symbols():
    """Symbols with net units above zero in any user's stock tab."""
    tabs = [s["properties"]["title"] for s in sheets("?fields=sheets.properties.title&key=" + KEY)["sheets"]]
    held = set()
    for tab in tabs:
        if not tab.lower().endswith("-stock"):
            continue
        rows = sheets("/values/%s?key=%s" % (urllib.parse.quote(tab), KEY)).get("values", [])
        if not rows:
            continue
        head = [str(h or "").strip().lower() for h in rows[0]]
        try:
            cs, ct, cu = head.index("stock"), head.index("type"), head.index("units")
        except ValueError:
            continue
        net = {}
        for r in rows[1:]:
            if len(r) <= max(cs, ct, cu):
                continue
            sym, typ = str(r[cs]).strip().upper(), str(r[ct]).strip().lower()
            try:
                u = float(str(r[cu]).replace(",", ""))
            except ValueError:
                continue
            if sym and typ == "buy":
                net[sym] = net.get(sym, 0) + u
            elif sym and typ in ("sell", "sold"):
                net[sym] = net.get(sym, 0) - u
        held |= {s for s, n in net.items() if n > 0.5}
        print(tab, "->", len([s for s, n in net.items() if n > 0.5]), "stocks")
    return sorted(held)


def psx_eps(page):
    """Yearly EPS from the PSX company page: {year: eps}."""
    t = pn.norm(page)
    i = t.find("All numbers in thousands")
    if i < 0:
        return {}
    seg = t[i:i + 6000]
    k = seg.find("|Quarterly|")
    if k < 0:
        return {}
    toks = seg[k + len("|Quarterly|"):].split("|")
    years = []
    for x in toks:
        if x.isdigit() and len(x) == 4:
            years.append(x)
        else:
            break
    if not years:
        return {}
    try:
        j = toks.index("EPS")
    except ValueError:
        return {}
    vals = []
    for x in toks[j + 1:j + 1 + len(years)]:
        v = pn.parse_num(x)
        if v is None:
            break
        vals.append(v)
    return {y: v for y, v in zip(years, vals)}


def tv_history(syms):
    """TradingView scanner: {symbol: {"eps": {year: v}, "dps": {year: v}}} for the latest fiscal years."""
    body = json.dumps({"symbols": {"tickers": ["PSX:" + s for s in syms]},
                       "columns": ["name", "fiscal_period_fy_h", "earnings_per_share_diluted_fy_h", "dps_common_stock_prim_issue_fy_h"]}).encode()
    req = urllib.request.Request("https://scanner.tradingview.com/pakistan/scan", data=body,
                                 headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://www.tradingview.com"})
    data = json.load(urllib.request.urlopen(req, timeout=60)).get("data", [])
    out = {}
    for row in data:
        name, yrs, eps, dps = row["d"]
        if not yrs:
            continue
        pick = lambda arr: {str(y): round(v, 2) for y, v in list(zip(yrs, arr or []))[:KEEP_YEARS] if v is not None}
        out[name] = {"eps": pick(eps), "dps": pick(dps)}
    return out


def main():
    syms = held_symbols()
    if not syms:
        raise SystemExit("No held stocks found. File not changed.")
    print("Held stocks:", len(syms), syms)

    def get(s):
        try:
            time.sleep(0.4)
            return s, pn.fetch(pn.COMPANY_URL + s)
        except Exception:
            return s, None

    with ThreadPoolExecutor(4) as ex:
        pages = dict(ex.map(get, syms))
    failed = [s for s in syms if pages.get(s) is None]
    if len(failed) > max(1, len(syms) // 10):
        raise SystemExit("Too many company pages failed (%d of %d). File not changed." % (len(failed), len(syms)))
    try:
        tv = tv_history(syms)
    except Exception as e:
        print("TradingView failed:", e)
        tv = {}

    pn.DAYS = NEWS_DAYS
    rows = {}
    for s in syms:
        page = pages.get(s)
        pe = psx_eps(page) if page else {}
        t = tv.get(s, {"eps": {}, "dps": {}})
        eps = {}
        for y in set(pe) | set(t["eps"]):
            e = {}
            if y in pe:
                e["psx"] = pe[y]
            if y in t["eps"]:
                e["tv"] = t["eps"][y]
            eps[y] = e
        dps = {y: {"tv": v} for y, v in t["dps"].items()}
        news = pn.parse_announcements(page)[:MAX_NEWS] if page else []
        rows[s] = {"eps": eps, "dps": dps, "news": news}
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "news_days": NEWS_DAYS, "rows": rows}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print("Saved", len(rows), "stocks | failed pages:", failed)
    for s, r in rows.items():
        print(s, "eps", sorted(r["eps"])[-5:], "dps", sorted(r["dps"])[-5:], "news", len(r["news"]))


if __name__ == "__main__":
    main()
