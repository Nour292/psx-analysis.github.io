"""Pull daily candles (high, low, close) for the stocks on tickers/list.json from TradingView.

Writes tickers/history.json: per stock a list of [YYYYMMDD, high, low, close], oldest first.
Standard library only (own minimal websocket client). Run: python scripts/pull_history.py
"""
import base64
import json
import os
import random
import re
import socket
import ssl
import string
import struct
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "tickers", "list.json")
OUT = os.path.join(ROOT, "tickers", "history.json")
HOST = "data.tradingview.com"
BARS = 2000


def rid(p):
    return p + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def pack(o):
    s = o if isinstance(o, str) else json.dumps(o, separators=(",", ":"))
    return "~m~%d~m~%s" % (len(s), s)


class WS:
    def __init__(self):
        raw = socket.create_connection((HOST, 443), timeout=20)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /socket.io/websocket HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nOrigin: https://www.tradingview.com\r\n"
                        "User-Agent: Mozilla/5.0\r\n\r\n" % (HOST, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.s.recv(4096)
            if not c:
                raise IOError("handshake failed")
            buf += c
        if b" 101 " not in buf.split(b"\r\n")[0]:
            raise IOError(buf.split(b"\r\n")[0].decode())
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.rest) < n:
            c = self.s.recv(65536)
            if not c:
                raise IOError("closed")
            self.rest += c
        d, self.rest = self.rest[:n], self.rest[n:]
        return d

    def send(self, text):
        d = text.encode()
        h = b"\x81"
        n = len(d)
        h += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        m = os.urandom(4)
        self.s.sendall(h + m + bytes(b ^ m[i % 4] for i, b in enumerate(d)))

    def recv(self):
        out = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            d = self._read(n)
            if b0 & 0x0F == 8:
                raise IOError("closed")
            if b0 & 0x0F == 9:
                continue
            out += d
            if b0 & 0x80:
                return out.decode("utf-8", "replace")

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def msgs(text):
    out = []
    for m in re.split(r"~m~\d+~m~", text):
        if m:
            out.append(m)
    return out


def candles(ws, sym):
    cs, ss = rid("cs_"), "s1"
    ws.send(pack({"m": "set_auth_token", "p": ["unauthorized_user_token"]}))
    ws.send(pack({"m": "chart_create_session", "p": [cs, ""]}))
    ws.send(pack({"m": "resolve_symbol", "p": [cs, "sds_sym_1", '={"symbol":"PSX:%s","adjustment":"splits"}' % sym]}))
    ws.send(pack({"m": "create_series", "p": [cs, ss, "s1", "sds_sym_1", "1D", BARS, ""]}))
    bars, t0 = None, time.time()
    while time.time() - t0 < 25:
        for m in msgs(ws.recv()):
            if m.startswith("~h~"):
                ws.send(pack(m))
            elif m.startswith("{"):
                j = json.loads(m)
                if j.get("m") == "timescale_update":
                    bars = j["p"][1].get(ss, {}).get("s")
                elif j.get("m") in ("series_completed", "symbol_error", "critical_error"):
                    if j["m"] != "series_completed":
                        return None
                    if bars:
                        out = []
                        for b in bars:
                            v = b["v"]
                            d = datetime.fromtimestamp(v[0], timezone.utc).strftime("%Y%m%d")
                            out.append([d, round(v[2], 2), round(v[3], 2), round(v[4], 2)])
                        return out
    return None


def sheet_symbols():
    """Symbols in the Data tab of the PSX Analysis sheet (the same stocks the web app shows)."""
    import urllib.request
    url = "https://sheets.googleapis.com/v4/spreadsheets/1XTDO4sR5lFCUae8lHfsx96P8hLoXA7j2yeZMOiopuT8/values/Data!A:A?key=AIzaSyBfjtlvDvGxmS1OcqFVGNMMadqza1ofCrI"
    req = urllib.request.Request(url, headers={"Referer": "https://nour292.github.io/psx-analysis.github.io/"})
    rows = json.load(urllib.request.urlopen(req, timeout=30)).get("values", [])
    return [r[0].strip().upper() for r in rows[1:] if r and r[0].strip()]


def main():
    syms = sheet_symbols()
    only = sys.argv[1:]
    if only:
        syms = only
    res, bad = {}, []
    for sym in syms:
        got = None
        for _ in range(3):
            ws = None
            try:
                ws = WS()
                got = candles(ws, sym)
            except Exception as e:
                print(sym, "error", e)
            finally:
                if ws:
                    ws.close()
            if got:
                break
            time.sleep(2)
        if got:
            res[sym] = got
            print(sym, len(got), got[0][0], "->", got[-1][0])
        else:
            bad.append(sym)
        time.sleep(0.5)
    if bad:
        print("failed:", bad)
    if len(res) < max(1, len(syms) // 2):
        print("too few stocks pulled, keeping the old file")
        sys.exit(1)
    json.dump({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "data": res}, open(OUT, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
