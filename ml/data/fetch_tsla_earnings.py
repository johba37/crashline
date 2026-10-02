"""Fetch TSLA's quarterly earnings release times from SEC EDGAR and write them as JSON.

Source: the EDGAR submissions API (data.sec.gov), Tesla's 8-K filings that
carry item 2.02 ("Results of Operations and Financial Condition"). Tesla files
two kinds of them each quarter: the production/deliveries report in the first
days of the quarter and the earnings release (update letter) three to four
weeks later. The earnings release is taken as the LAST item-2.02 8-K accepted
in each quarter's window: the 15th of January / April / July / October to the
end of the following month.

    python ml/data/fetch_tsla_earnings.py --start 2016-09-28 --end 2026-09-28

Reaction day: EDGAR's acceptance time is UTC. A release accepted at or after
the New York close (16:00) moves the next session; one accepted before the
close moves that day's session. The reaction day is matched against the
trading dates in ml/data/tsla_daily.csv.

Writes tsla_earnings.json: source URLs, fetch time, and per release the
accession number, acceptance time, New York date and reaction day. The
deliveries reports are listed too (kind "deliveries"), but only "earnings"
rows are used by ml/calibrate_earnings.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
CIK = "0001318605"
BASE = "https://data.sec.gov/submissions/"
UA = {"User-Agent": "surrogate-pricer research", "Accept": "application/json"}
NY = ZoneInfo("America/New_York")
WINDOW_MONTHS = {1: (1, 2), 4: (4, 5), 7: (7, 8), 10: (10, 11)}   # quarter window: from the 15th of the first month


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def filings() -> tuple[list[str], list[dict]]:
    """Every 8-K with item 2.02, oldest first."""
    urls = [BASE + f"CIK{CIK}.json"]
    sub = get_json(urls[0])
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"]["files"]:
        urls.append(BASE + f["name"])
        blocks.append(get_json(urls[-1]))
    rows = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if form == "8-K" and "2.02" in b["items"][i].split(","):
                rows.append({"accession": b["accessionNumber"][i], "acceptedUtc": b["acceptanceDateTime"][i],
                             "items": b["items"][i]})
    rows.sort(key=lambda r: r["acceptedUtc"])
    return urls, rows


def quarter_window(d: dt.date):
    """(year, first month) of the earnings window that contains d, or None."""
    for first, months in WINDOW_MONTHS.items():
        if d.month in months and not (d.month == first and d.day < 15):
            return d.year, first
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-09-28")
    ap.add_argument("--end", default="2026-09-28")
    ap.add_argument("--csv", default=os.path.join(HERE, "tsla_daily.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "tsla_earnings.json"))
    args = ap.parse_args()

    with open(args.csv) as f:
        next(f)
        trading = [line.split(",")[0] for line in f]

    urls, rows = filings()
    last_in_window: dict[tuple[int, int], int] = {}
    for i, r in enumerate(rows):
        t = dt.datetime.fromisoformat(r["acceptedUtc"].replace("Z", "+00:00")).astimezone(NY)
        r["acceptedNewYork"] = t.strftime("%Y-%m-%d %H:%M")
        after_close = (t.hour, t.minute) >= (16, 0)
        day = t.date().isoformat()
        later = [d for d in trading if (d > day if after_close else d >= day)]
        r["reactionDay"] = later[0] if later else None
        w = quarter_window(t.date())
        r["kind"] = "deliveries"
        if w is not None:
            last_in_window[w] = i          # rows are sorted: the last one wins
    for i in last_in_window.values():
        rows[i]["kind"] = "earnings"

    rows = [r for r in rows if r["reactionDay"] and args.start < r["reactionDay"] <= args.end]
    earnings = [r for r in rows if r["kind"] == "earnings"]
    gaps = [(dt.date.fromisoformat(b["reactionDay"]) - dt.date.fromisoformat(a["reactionDay"])).days
            for a, b in zip(earnings, earnings[1:])]
    out = {
        "symbol": "TSLA",
        "fetchedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "SEC EDGAR submissions API, form 8-K with item 2.02",
        "urls": urls,
        "window": [args.start, args.end],
        "rule": "earnings = the last item-2.02 8-K accepted from the 15th of Jan/Apr/Jul/Oct to the end of the "
                "following month; reaction day = that session if accepted before 16:00 New York, else the next",
        "earningsCount": len(earnings),
        "daysBetweenEarnings": {"min": min(gaps), "max": max(gaps), "mean": round(sum(gaps) / len(gaps), 2)},
        "releases": rows,
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"wrote {args.out}: {len(rows)} item-2.02 filings in the window, {len(earnings)} earnings releases, "
          f"days between them {min(gaps)}..{max(gaps)} (mean {sum(gaps) / len(gaps):.1f})")
    for r in earnings:
        print(f"  {r['acceptedNewYork']} NY  -> reaction {r['reactionDay']}  {r['accession']}  items {r['items']}")


if __name__ == "__main__":
    main()
