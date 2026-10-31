"""Monthly PSX ticker pull. Applies the 4 list filters and writes tickers/list.json.

Filters: 1 dividend yield > 0 and P/E > 0
         2 top half by 30D volume AND top half by price x 30D volume
         3 profit after tax rising (latest 3 fiscal years, PSX company page)
         4 dividend paid 3+ years in a row (TradingView continuous_dividend_payout)
Standard library only. Run: python scripts/pull_tickers.py [schedule|manual]
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SCREENER_URL = "https://dps.psx.com.pk/screener"
COMPANY_URL = "https://dps.psx.com.pk/company/"
TV_URL = "https://scanner.tradingview.com/pakistan/scan"
MIN_DIV_YEARS = 3
OVERRIDES = ["CPHL"]  # kept by owner decision, never dropped automatically
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tickers", "list.json")
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url, data=None, headers=None, tries=3):
    h = dict(UA)
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3)


def num(x):
    try:
        return float(x.replace(",", "").replace("%", ""))
    except Exception:
        return 0.0


def parse_screener(page):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 11:
            continue
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in tds]
        sym = re.sub(r"XD$", "", c[0])
        out.append({"sym": sym, "price": num(c[4]), "pe": num(c[7]), "yld": num(c[8]), "vol": num(c[10])})
    return out


def liquidity_top(rows):
    half = (len(rows) + 1) // 2
    by_vol = {r["sym"] for r in sorted(rows, key=lambda r: -r["vol"])[:half]}
    by_val = {r["sym"] for r in sorted(rows, key=lambda r: -r["price"] * r["vol"])[:half]}
    return [r for r in rows if r["sym"] in by_vol and r["sym"] in by_val]


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


def parse_financials(page):
    t = norm(page)
    i = t.find("All numbers in thousands")
    if i < 0:
        return None
    seg = t[i:i + 3000]
    m = re.search(r"\|(Q\d|H\d|9M|Half)[^|]*\d{4}\|", seg)
    ann = seg[:m.start()] if m else seg[:1500]
    parts = ann.split("|")
    years = [p for p in parts if re.fullmatch(r"20\d\d", p)]

    def row(names):
        for k, p in enumerate(parts):
            if p.strip().lower() in names:
                vals = []
                for q in parts[k + 1:k + 9]:
                    v = parse_num(q)
                    if v is None:
                        break
                    vals.append(v)
                return vals
        return []

    return {"years": years, "pat": row(["profit after taxation", "profit for the year"]), "eps": row(["eps"])}


def profit_rising(f):
    vals = f["pat"] if len(f["pat"]) >= 2 else f["eps"]
    k = min(3, len(vals))
    if k < 2:
        return False, vals[:3]
    return all(vals[i] > vals[i + 1] for i in range(k - 1)), vals[:3]


def tv_div_years(syms):
    body = json.dumps({"symbols": {"tickers": ["PSX:" + s for s in syms]},
                       "columns": ["name", "continuous_dividend_payout", "Perf.3Y", "Perf.All"]}).encode()
    page = fetch(TV_URL, data=body, headers={"Content-Type": "application/json"})
    out = {}
    for d in json.loads(page)["data"]:
        n, dy, p3, pall = d["d"]
        # Perf.All equal to Perf.3Y means the stock has traded for under 3 years
        out[n] = (dy, p3 is not None and pall is not None and abs(p3 - pall) < 1e-6)
    return out


def main(trigger):
    screener = parse_screener(fetch(SCREENER_URL))
    if len(screener) < 100:
        raise SystemExit("Screener returned %d rows; PSX may be blocking." % len(screener))
    by_sym = {r["sym"]: r for r in screener}
    cand = liquidity_top([r for r in screener if r["yld"] > 0 and r["pe"] > 0])

    syms = [r["sym"] for r in cand]

    def get_fin(s):
        try:
            time.sleep(0.4)
            return s, parse_financials(fetch(COMPANY_URL + s))
        except Exception:
            return s, None

    with ThreadPoolExecutor(4) as ex:
        fin = dict(ex.map(get_fin, syms))
    div = tv_div_years(syms)

    # Safety: never replace the saved list with a partial or blocked result.
    failed = [s for s in syms if fin.get(s) is None]
    if len(failed) > max(2, len(syms) // 20) or len(div) < len(syms) * 0.9:
        raise SystemExit("Incomplete data (%d company pages failed, %d/%d dividend rows). List not changed."
                         % (len(failed), len(div), len(syms)))

    prev = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            prev = {r["symbol"]: r["status"] for r in json.load(fh).get("rows", [])}

    rows = []

    def add(sym, status, r, vals, dy, reason):
        rows.append({"symbol": sym, "status": status, "price": r["price"] if r else None,
                     "yield": r["yld"] if r else None, "profit": vals, "divYears": dy, "reason": reason})

    for r in cand:
        s = r["sym"]
        f = fin.get(s)
        ok, vals = profit_rising(f) if f else (False, [])
        dy, recent = div.get(s, (None, False))
        shown = len(f["years"]) if f else 0
        status, reason = None, ""
        if not ok:
            reason = "Profit not rising 3 years"
        elif dy is None:
            reason = "No dividend data"
        elif dy >= MIN_DIV_YEARS:
            status = "pass"
        elif recent and dy >= max(2, min(shown, 3)):
            status, reason = "pass", "New listing, paid every year since listing"
        else:
            reason = "Dividend record under 3 years"
        if status == "pass":
            st = "Override" if s in OVERRIDES else ("Active" if prev.get(s) in ("Active", "New", "Override") else "New")
            add(s, st, r, vals, dy, reason)
        elif status == "review":
            add(s, "Review", r, vals, dy, reason)
        elif prev.get(s) in ("Active", "New", "Override", "Review") and s not in OVERRIDES:
            add(s, "Dropped", r, vals, dy, reason)

    listed = {r["symbol"] for r in rows}
    for s in OVERRIDES:
        if s not in listed:
            add(s, "Override", by_sym.get(s), [], div.get(s, (None, False))[0], "Kept by owner decision")
    listed = {r["symbol"] for r in rows}
    for s, st in prev.items():  # earlier members that fell out of the candidate pool
        if s not in listed and st in ("Active", "New", "Override", "Review"):
            add(s, "Dropped", by_sym.get(s), [], None, "Left the liquidity or dividend pool")

    order = {"New": 0, "Review": 1, "Override": 2, "Active": 3, "Dropped": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["symbol"]))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in order}
    result = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "trigger": trigger,
              "universe": len(screener), "candidates": len(cand), "counts": counts, "rows": rows}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print("Screener rows %d | after filters 1-2: %d | %s" % (len(screener), len(cand), counts))
    print("PASS:", ", ".join(r["symbol"] + ":" + r["status"] for r in rows if r["status"] in ("New", "Active", "Override")))
    print("REVIEW:", ", ".join(r["symbol"] for r in rows if r["status"] == "Review"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "manual")
