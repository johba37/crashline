"""Fetch TSLA daily closes from two public endpoints and write them as CSV.

Primary (the file the calibration reads): Yahoo Finance chart API, full
history since the 2010 IPO, `close` = split-adjusted close (TSLA pays no
dividends, so split-adjusted = total-return-adjusted; `adjclose` is kept for
the check). Cross-check: Nasdaq's public historical-quotes API (last ~10 y).

    python ml/data/fetch_tsla.py --end 2026-09-29

`--end` is exclusive (UTC midnight), so a partial session on the fetch day is
never included and a refetch with the same `--end` is reproducible up to
vendor revisions. Writes tsla_daily.csv, tsla_daily_nasdaq.csv and
tsla_daily.source.json (URLs, fetch time, split events, row counts).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0",
      "Accept": "application/json"}


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_yahoo(end: dt.date):
    p2 = int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.timezone.utc).timestamp())
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/TSLA"
           f"?period1=1262304000&period2={p2}&interval=1d&events=split")
    js = get_json(url)
    res = js["chart"]["result"][0]
    tz_off = res["meta"]["gmtoffset"]  # not used for dates; dates come from exchange tz below
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    splits = sorted(res.get("events", {}).get("splits", {}).values(), key=lambda s: s["date"])
    rows = []
    for t, c, a in zip(ts, q["close"], adj):
        if c is None:
            continue
        # Yahoo stamps daily bars at the session open (09:30 New York); shifting
        # by -4 h is enough to land on the New York calendar date in EST and EDT.
        d = dt.datetime.fromtimestamp(t - 4 * 3600, dt.timezone.utc).date()
        rows.append((d.isoformat(), c, a))
    del tz_off
    return url, rows, splits


def fetch_nasdaq(end: dt.date):
    frm = dt.date(end.year - 11, end.month, 1)
    url = ("https://api.nasdaq.com/api/quote/TSLA/historical?assetclass=stocks"
           f"&fromdate={frm.isoformat()}&todate={(end - dt.timedelta(days=1)).isoformat()}&limit=9999")
    js = get_json(url)
    rows = []
    for r in js["data"]["tradesTable"]["rows"]:
        m, d, y = r["date"].split("/")
        rows.append((f"{y}-{m}-{d}", float(r["close"].replace("$", "").replace(",", ""))))
    rows.sort()
    return url, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", required=True, help="exclusive end date, YYYY-MM-DD (UTC)")
    args = ap.parse_args()
    end = dt.date.fromisoformat(args.end)
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    y_url, y_rows, splits = fetch_yahoo(end)
    with open(os.path.join(HERE, "tsla_daily.csv"), "w") as f:
        f.write("date,close,adjclose\n")
        for d, c, a in y_rows:
            f.write(f"{d},{c:.6f},{a:.6f}\n")

    n_url, n_rows = fetch_nasdaq(end)
    with open(os.path.join(HERE, "tsla_daily_nasdaq.csv"), "w") as f:
        f.write("date,close\n")
        for d, c in n_rows:
            f.write(f"{d},{c:.4f}\n")

    meta = {
        "symbol": "TSLA",
        "fetchedAtUtc": fetched_at,
        "endExclusiveUtc": end.isoformat(),
        "primary": {
            "file": "tsla_daily.csv",
            "source": "Yahoo Finance chart API (v8)",
            "url": y_url,
            "columns": "close = split-adjusted close; adjclose = split+dividend adjusted",
            "rows": len(y_rows), "first": y_rows[0][0], "last": y_rows[-1][0],
            "splits": [{"date": dt.datetime.fromtimestamp(s["date"], dt.timezone.utc).date().isoformat(),
                        "ratio": f'{s["numerator"]}:{s["denominator"]}'} for s in splits],
        },
        "crossCheck": {
            "file": "tsla_daily_nasdaq.csv",
            "source": "Nasdaq historical quotes API",
            "url": n_url,
            "rows": len(n_rows), "first": n_rows[0][0], "last": n_rows[-1][0],
        },
    }
    with open(os.path.join(HERE, "tsla_daily.source.json"), "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
