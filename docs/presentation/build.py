#!/usr/bin/env python3
"""Build the two-minute pitch video deck: one self-contained index.html.

Six scenes on a 16:9 stage with no chrome, so a screen recording is the video.
Each scene builds in steps; every step has the line to say and a target time.

    python3 build.py                 # writes index.html, prints the timing table
    python3 build.py --frames DIR    # also one 1920x1080 PNG per step (needs Chrome)

Keys in the browser: right/left step, up/down scene, F fullscreen, A autoplay on
the target times, P presenter window (script + clock), S script on the stage
(rehearsal only), R reset, H this list.

Numbers on the slides and where they come from:
  149.4B, 1-3% markup   ~/arb-hackathon/docs/structured-notes-market.md (facts 1 and 4)
  0.25%/week, 1.0675    backend/scenarios/happy_path.py TERMS (coupon 25 bps, 26 + 1 periods)
  0.8475                happy_path-clock.log, series B (ends at 78%, knocked in)
  $757 cover premium    model/k3 at strike, vol 42% +- 3, bid 25 bps (see scene_sides)
  10,343 vs 10,338.2    happy_path-clock.log, trade 1 on series A (student vs teacher v3)
  8.4 max over 6 trades happy_path-clock.log, section f
  37.9 / 14.1 / 2.65    docs/k3-vol-input.md, set T (130,752 points, gate 50)
  7,465 / 23,901 bytes  model/k3/student_export.json, README status table
"""

import argparse
import json
import math
import random
import re
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent

# Flip when Deploy.s.sol has been broadcast to Robinhood Chain testnet (46630).
DEPLOYED_ON_ROBINHOOD_TESTNET = False
REPO_URL = "github.com/johba37/surrogate-pricer"

W, H = 1600, 900

BG = "#10131a"
PANEL = "#1a202b"
PANEL2 = "#232a38"
LINE = "#3c4558"
INK = "#f3f0e8"
INK2 = "#c9cfdb"
MUTED = "#a3adbe"
NEUTRAL = "#5b657c"
GOLD = "#e0c07a"  # brand accent and the NOTE token
# The three price paths. Validated as a set on PANEL (lightness band, chroma,
# colour-blind and normal-vision separation, 3:1 contrast).
EARLY = "#3987e5"
HOLD = "#199e70"
CRASH = "#d95926"  # also the COVER token

FONT = '"Liberation Sans", Arial, Helvetica, "DejaVu Sans", sans-serif'
WPM = 150


# ---------------------------------------------------------------- svg helpers

def esc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def T(x, y, s, size=28, fill=INK, anchor="start", weight=400, spacing=0, mono=False):
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if weight != 400:
        attrs += f' font-weight="{weight}"'
    if spacing:
        attrs += f' letter-spacing="{spacing}"'
    if mono:
        attrs += ' class="mono"'
    return f"<text {attrs}>{esc(s)}</text>"


def lines(x, y, rows, size=26, fill=INK2, leading=None, anchor="start", weight=400):
    leading = leading or round(size * 1.4)
    return "\n".join(T(x, y + n * leading, row, size, fill, anchor, weight) for n, row in enumerate(rows))


def rect(x, y, w, h, rx=0, fill="none", stroke=None, sw=1.5, opacity=None):
    extra = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    if opacity is not None:
        extra += f' opacity="{opacity}"'
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{extra}/>'


def panel(x, y, w, h):
    return rect(x, y, w, h, 16, PANEL, LINE)


def seg(x1, y1, x2, y2, stroke=LINE, sw=1.5, dash=None, opacity=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    if opacity is not None:
        extra += f' opacity="{opacity}"'
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round"{extra}/>'
    )


def poly(points, stroke, sw=2, opacity=None):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return (
        f'<polyline points="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" '
        f'stroke-linejoin="round" stroke-linecap="round"{extra}/>'
    )


def dot(x, y, fill, r=7, ring=BG):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{ring}" stroke-width="2"/>'


def arrow(x1, y, x2, stroke=MUTED, sw=3):
    return (
        seg(x1, y, x2 - 6, y, stroke, sw)
        + f'<polygon points="{x2},{y} {x2 - 16},{y - 9} {x2 - 16},{y + 9}" fill="{stroke}"/>'
    )


def chip(x, y, w, h, label, fill, ink="#1a140c", size=28):
    return rect(x, y, w, h, 12, fill) + T(x + w / 2, y + h / 2 + size * 0.36, label, size, ink, "middle", 700)


def step(k, *parts, dim=None, off=None):
    """Show from step k; fade back from step `dim`; leave the stage from step `off`."""
    attrs = f'class="st" data-s="{k}"'
    if dim is not None:
        attrs += f' data-dim="{dim}"'
    if off is not None:
        attrs += f' data-off="{off}"'
    return f"<g {attrs}>\n" + "\n".join(parts) + "\n</g>"


def head(kicker, title):
    return "\n".join([
        T(80, 86, kicker.upper(), 22, GOLD, spacing=3, weight=700),
        T(80, 152, title, 52, INK, weight=700, spacing=-1),
    ])


# ---------------------------------------------------------------------- scenes

def scene_hook():
    market = step(
        0,
        T(72, 390, "$149B", 230, INK, weight=700, spacing=-6),
        T(80, 468, "of structured notes sold in the US in 2024", 46, INK2),
        off=2,
    )
    problem = step(
        1,
        rect(80, 548, 120, 5, 2, GOLD),
        T(80, 640, "The bank that sells the note also sets its price.", 54, INK, weight=700, spacing=-1),
        T(80, 700, "Studies find a markup of 1 to 3% built in.", 36, INK2),
        off=2,
    )
    sources = step(
        0,
        T(80, 852, "Sources: SRP via GenTwo (US issuance, 2024) · J. Risk Financial Manag. 16(9):401 (markups)", 20, MUTED),
        off=2,
    )
    title = step(
        2,
        T(800, 400, "Surrogate Pricer", 124, INK, "middle", 700, -3),
        rect(740, 440, 120, 5, 2, GOLD),
        T(800, 530, "The fair price, computed on-chain, inside the trade.", 46, INK, "middle"),
        T(800, 612, "A neural network on Stylus  ·  stock tokens on Robinhood Chain  ·  settled in USDG", 28, MUTED, "middle"),
    )
    return "\n".join([market, problem, sources, title])


# Weekly fixings in percent of the starting price, index 0 = start, 27 = the end.
# From week 11 on these are the fixings of series A and B in the logged happy
# path; the first ten weeks are drawn (the scenario strikes its series mid-life).
PATH_EARLY = [100, 93, 87, 84, 88, 94, 101]
PATH_HOLD = [100, 98, 96, 94, 95, 92, 90, 91, 88, 89, 88,
             86, 82, 79, 84, 88, 93, 89, 85, 80, 76, 81, 87, 92, 90, 88, 91, 92]
PATH_CRASH = [100, 95, 89, 82, 74, 66, 58, 62, 68, 74, 80,
              79, 75, 72, 76, 81, 78, 73, 69, 72, 75, 77, 74, 79, 81, 76, 77, 78]


def scene_payoff():
    left, right, top, bot = 80, 860, 230, 750

    def x_of(i):
        return left + i / 27 * (right - left)

    def y_of(pct):
        return top + (112 - pct) / 62 * (bot - top)

    def pts(path):
        return [(x_of(i), y_of(p)) for i, p in enumerate(path)]

    frame = [head("The note", "A note that pays a coupon, unless the stock crashes.")]
    frame.append(seg(left, y_of(100), right, y_of(100), INK2, 1.5, opacity=0.7))
    frame.append(T(right, y_of(100) - 14, "starting price", 24, INK2, "end"))
    frame.append(seg(left, y_of(60), right, y_of(60), INK2, 1.5, "8 7", 0.7))
    frame.append(T(right, y_of(60) - 14, "60% of the start", 24, INK2, "end"))
    for i in range(28):
        tall = i in (0, 27)
        frame.append(seg(x_of(i), 768, x_of(i), 784 if tall else 778, LINE if not tall else MUTED, 2))
    frame.append(T(left, 824, "start", 24, MUTED))
    frame.append(T((left + right) / 2, 824, "26 weekly checks", 24, MUTED, "middle"))
    frame.append(T(right, 824, "end", 24, MUTED, "end"))

    def card(n, color, title, rows):
        y = 215 + n * 205
        return "\n".join([
            panel(910, y, 610, 185),
            rect(910, y, 8, 185, 4, color),
            T(942, y + 56, title, 28, INK, weight=700),
            lines(942, y + 104, rows, 26, INK2, 40),
        ])

    early = pts(PATH_EARLY)
    hold = pts(PATH_HOLD)
    crash = pts(PATH_CRASH)

    parts = ["\n".join(frame)]
    parts.append(step(
        1,
        poly(early, EARLY, 3.5),
        dot(*early[-1], EARLY),
        T(early[-1][0] + 16, early[-1][1] - 14, "ends early", 24, INK),
        dim=2,
    ))
    parts.append(step(1, card(0, EARLY, "Back at the starting price", [
        "The note ends early.",
        "$1 back, plus 0.25% per week so far.",
    ])))
    parts.append(step(
        2,
        poly(hold, HOLD, 3.5),
        dot(*hold[-1], HOLD),
        T(right - 14, hold[-1][1] - 22, "stays above 60%", 24, INK, "end"),
        dim=3,
    ))
    parts.append(step(2, card(1, HOLD, "Never below 60% on a check", [
        "The note runs to the end.",
        "$1 back, plus every coupon: $1.0675.",
    ])))
    parts.append(step(
        3,
        poly(crash, CRASH, 3.5),
        dot(*crash[6], CRASH),
        T(crash[6][0] + 16, crash[6][1] + 36, "below 60% on a check", 24, INK),
        dot(*crash[-1], CRASH),
        T(right - 14, crash[-1][1] + 66, "ends at 78%", 24, INK, "end"),
    ))
    parts.append(step(3, card(2, CRASH, "Below 60%, and finishes down", [
        "Paid the ending price, plus coupons.",
        "Ending at 78% pays $0.8475.",
    ])))
    return "\n".join(parts)


def scene_sides():
    # A stock holder with $10,000 of stock tokens buys 10,000 COVER at the start.
    # Premium: model/k3 at strike, spot at par, listing vol 42% with a 3-point band
    # and a 25 bps bid (the happy-path listing): NOTE 9943 at vol 45%, so
    # cover = 10675 - (9943 - 25) = 757 bps. The model's mid at 42% is 688.
    # Payouts are (1.0675 - note payout) per COVER, see NoteSeries._redeemValue.
    cx = [80, 900, 1180, 1520]  # label, stock, cover pays, together (right edges after the first)

    def row(y, color, label, stock, cover, total, bold=False):
        w = 700 if bold else 400
        return "\n".join([
            rect(80, y - 34, 8, 46, 4, color),
            T(112, y, label, 28, INK, weight=w),
            T(cx[1], y, stock, 28, INK2, "end"),
            T(cx[2], y, cover, 28, INK, "end", 700 if bold else 400),
            T(cx[3], y, total, 28, INK, "end", w),
            seg(80, y + 26, 1520, y + 26, LINE, 1),
        ])

    base = [
        head("Two tokens", "The cash is locked up front. Two tokens split it."),
        T(80, 250, "$1.0675 of USDG is locked per note, and split between", 30, INK2),
        chip(830, 212, 130, 54, "NOTE", GOLD, size=26),
        T(984, 250, "and", 30, INK2),
        chip(1056, 212, 150, 54, "COVER", CRASH, size=26),
    ]
    table_head = [
        T(80, 350, "You hold $10,000 of stock tokens and buy cover on all of it for $757.", 32, INK, weight=700),
        T(112, 428, "What the stock does", 24, MUTED),
        T(cx[1], 428, "Stock is worth", 24, MUTED, "end"),
        T(cx[2], 428, "COVER pays", 24, MUTED, "end"),
        T(cx[3], 428, "Together, before the premium", 24, MUTED, "end"),
        seg(80, 448, 1520, 448, LINE, 1.5),
        row(500, EARLY, "Back at the start by week 6", "$10,100", "$525", "$10,625"),
        row(572, HOLD, "Dips to 78%, never below 60% on a check", "$7,800", "$0", "$7,800"),
    ]
    crash = [
        row(644, CRASH, "Below 60% on a check, ends at 78%", "$7,800", "$2,200", "$10,000", True),
        row(716, CRASH, "Below 60% on a check, ends at 50%", "$5,000", "$5,000", "$10,000", True),
    ]
    other = [
        T(80, 800, "The other side: a cash holder keeps NOTE, earns 0.25% a week, and takes that loss.", 28, INK2),
        T(80, 844, "Fully funded on both sides. No margin calls, no liquidations.", 28, INK, weight=700),
    ]
    return "\n".join([
        "\n".join(base),
        step(1, *table_head),
        step(2, *crash),
        step(3, *other),
    ])


def scene_how():
    cols = [80, 590, 1100]
    box_y, box_h = 330, 230

    def column(x, label, title, rows):
        return "\n".join([
            T(x, 244, label, 24, MUTED),
            T(x, 298, title, 40, INK, weight=700),
            lines(x, 664, rows, 26, INK2, 38),
        ])

    # one date: a put's payoff, a kinked line
    bx = cols[0]
    kink = "\n".join([
        seg(bx, box_y + box_h, bx + 400, box_y + box_h, LINE, 1.5),
        poly([(bx + 20, box_y + 30), (bx + 190, box_y + 190), (bx + 390, box_y + 190)], INK2, 3.5),
        seg(bx + 190, box_y + 190, bx + 190, box_y + box_h, MUTED, 1.5, "5 6"),
        T(bx + 400, box_y + box_h + 34, "price on the final day", 24, MUTED, "end"),
    ])

    # many dates: a fan of simulated paths, with the weekly checks behind it
    rng = random.Random(7)
    bx = cols[1]
    fan = []
    for i in range(1, 9):
        gx = bx + i * 400 / 8
        fan.append(seg(gx, box_y + 10, gx, box_y + box_h, LINE, 1, "3 7"))
    for _ in range(22):
        y = box_y + box_h / 2
        pts = [(bx, y)]
        for n in range(1, 41):
            y += rng.gauss(0, 8)
            y = min(max(y, box_y + 6), box_y + box_h - 6)
            pts.append((bx + n * 10, y))
        fan.append(poly(pts, INK2, 1.4, 0.5))
    fan = "\n".join(fan)

    # the network: 10 inputs, four hidden layers, one price
    bx = cols[2]
    layers = [5, 7, 6, 5, 5, 1]
    nodes = []
    for li, count in enumerate(layers):
        x = bx + 30 + li * 68
        nodes.append([(x, box_y + box_h / 2 + (k - (count - 1) / 2) * 30) for k in range(count)])
    net = []
    for a, b in zip(nodes, nodes[1:]):
        for p in a:
            for q in b:
                net.append(seg(p[0], p[1], q[0], q[1], MUTED, 1, opacity=0.35))
    for layer in nodes[:-1]:
        for x, y in layer:
            net.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{PANEL2}" stroke="{INK2}" stroke-width="2"/>')
    ox, oy = nodes[-1][0]
    net.append(f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="10" fill="{GOLD}"/>')
    net.append(T(ox + 20, oy + 9, "price", 24, INK))
    net = "\n".join(net)

    return "\n".join([
        head("Why a neural network", "No formula, so a small neural network prices it."),
        step(0, column(cols[0], "One date", "A formula.", [
            "A plain option: only the",
            "final price matters.",
        ]), kink, dim=2),
        step(1, arrow(500, 445, 552), column(cols[1], "26 dates, and an early exit", "A simulation.", [
            "262,144 random price paths.",
            "Too heavy to run on a chain.",
        ]), fan, dim=3),
        step(2, arrow(1004, 445, 1056), column(cols[2], "Trained on the simulation", "A neural network.", [
            "7,465 parameters, integer math.",
            "The whole model is 23.9 KB.",
        ]), net),
        step(
            3,
            rect(1074, 196, 478, 540, 18, "none", GOLD, 2.5),
            rect(1300, 180, 230, 34, 8, BG),
            T(1415, 206, "Stylus contract", 24, GOLD, "middle", 700),
            T(80, 796, "It runs inside the trade transaction.", 34, INK, weight=700),
            T(80, 842, "The weights are public: anyone can re-run the simulation and check a quote.", 28, INK2),
        ),
    ])


def scene_proof():
    lx, ly, lw, lh = 80, 200, 760, 630
    rx, rw = 880, 640

    # gap to the simulation on a line from 0 to the limit, dollars per $10,000
    ax0, ax1, ay = lx + 40, lx + lw - 60, ly + 500

    def gx(v):
        return ax0 + v / 50 * (ax1 - ax0)

    scale = [
        seg(ax0, ay, ax1, ay, LINE, 2),
        seg(ax0, ay - 8, ax0, ay + 8, MUTED, 2),
        seg(ax1, ay - 22, ax1, ay + 22, INK, 3),
        T(ax0, ay + 52, "0", 24, MUTED, "middle"),
        T(ax1, ay + 52, "limit $50", 24, INK, "end", 700),
        dot(gx(2.65), ay, GOLD, 9, PANEL),
        T(gx(2.65) + 14, ay + 52, "average $2.65", 24, INK),
        dot(gx(14.1), ay, GOLD, 9, PANEL),
        T(gx(14.1), ay - 28, "99% within $14.10", 24, INK, "middle"),
        dot(gx(37.9), ay, GOLD, 9, PANEL),
        T(gx(37.9), ay - 28, "worst $37.90", 24, INK, "middle"),
    ]

    return "\n".join([
        head("Proof", "It matches the simulation, or it refuses."),
        panel(lx, ly, lw, lh),
        step(0, T(lx + 36, ly + 56, "A real trade on our dev node: $10,000 of notes", 26, INK2)),
        step(
            1,
            T(lx + 36, ly + 122, "On-chain model", 24, MUTED),
            T(lx + 36, ly + 196, "$10,343", 68, INK, weight=700, spacing=-1),
            T(lx + 380, ly + 122, "Simulation, 262,144 paths", 24, MUTED),
            T(lx + 380, ly + 196, "$10,338.20", 68, INK2, weight=700, spacing=-1),
            T(lx + 36, ly + 254, "Gap: $4.80, or 0.05%. All six trades in the run: within $8.40.", 26, INK),
        ),
        step(
            2,
            seg(lx + 36, ly + 300, lx + lw - 36, ly + 300, LINE, 1.5),
            T(lx + 36, ly + 356, "130,752 held-out test points", 30, INK, weight=700),
            T(lx + 36, ly + 396, "Gap to the simulation, in dollars per $10,000 of notes", 24, MUTED),
            *scale,
            T(lx + 36, ly + 600, "The limit was fixed before the test.", 24, MUTED),
        ),
        step(
            3,
            panel(rx, 200, rw, 305),
            T(rx + 36, 258, "It refuses instead of guessing.", 32, INK, weight=700),
            rect(rx + 36, 286, 372, 52, 8, BG, LINE),
            T(rx + 54, 321, "revert Uncertified(1)", 26, GOLD, mono=True),
            lines(rx + 36, 388, [
                "On a check day, right at the 60% line, the",
                "fair price jumps. There, no trade happens.",
            ], 26, INK2, 38),
        ),
        step(
            4,
            panel(rx, 525, rw, 305),
            T(rx + 36, 583, "Payouts never call the model.", 32, INK, weight=700),
            chip(rx + 36, 612, 196, 52, "weekly prices", PANEL2, INK, 24),
            arrow(rx + 246, 638, rx + 290),
            chip(rx + 304, 612, 150, 52, "arithmetic", PANEL2, INK, 24),
            arrow(rx + 468, 638, rx + 512),
            T(rx + 528, 647, "USDG", 26, GOLD, weight=700),
            lines(rx + 36, 722, [
                "The model only quotes trades you are",
                "free to decline.",
            ], 26, INK2, 38),
        ),
    ])


def scene_close():
    chain = "Live on Robinhood Chain testnet" if DEPLOYED_ON_ROBINHOOD_TESTNET else "Built for Robinhood Chain"

    def promise(y, color, text):
        return rect(80, y - 36, 8, 46, 4, color) + T(112, y, text, 46, INK, weight=700, spacing=-0.5)

    def pill(x, text, width):
        return rect(x, 668, width, 56, 28, "none", LINE, 2) + T(x + width / 2, 705, text, 26, INK2, "middle")

    return "\n".join([
        step(
            0,
            promise(170, CRASH, "Crash cover for stock-token holders."),
            promise(250, GOLD, "A coupon for cash."),
            promise(330, EARLY, "A price anyone can check."),
        ),
        step(
            1,
            T(80, 560, "Surrogate Pricer", 124, INK, weight=700, spacing=-3),
            rect(84, 596, 120, 5, 2, GOLD),
            pill(80, "Stylus", 150),
            pill(250, "Settled in USDG", 270),
            pill(540, chain, 70 + len(chain) * 13),
            T(80, 806, REPO_URL, 34, GOLD, weight=700),
            T(1520, 806, "7,465-parameter model  ·  140 contract tests", 26, MUTED, "end"),
        ),
    ])


# Each step: the line to say while it is on screen. Numbers are spelled out so the
# word count is what you actually speak.
SCENES = [
    {
        "name": "Hook",
        "seconds": 15,
        "svg": scene_hook,
        "say": [
            "In twenty twenty-four, Americans bought almost a hundred and fifty billion dollars of structured notes.",
            "The bank that sells the note also sets its price.",
            "Surrogate Pricer computes that price on-chain, where anyone can check it.",
        ],
    },
    {
        "name": "The note",
        "seconds": 19,
        "svg": scene_payoff,
        "say": [
            "The note: one stock token, checked once a week for twenty-six weeks.",
            "Back at its starting price? It ends early: your dollar back, plus coupons.",
            "Never below sixty percent on a check? Dollar back, every coupon.",
            "Below sixty, and it finishes down? You take the stock's loss.",
        ],
    },
    {
        "name": "Two tokens",
        "seconds": 21,
        "svg": scene_sides,
        "say": [
            "The cash is locked up front and split into two tokens: the note, and the cover.",
            "Hold ten thousand dollars of stock: cover costs about seven hundred fifty.",
            "Cross the sixty percent line, and it pays your whole loss back.",
            "A cash holder takes the other side, for the coupon.",
        ],
    },
    {
        "name": "Why a neural network",
        "seconds": 23,
        "svg": scene_how,
        "say": [
            "A normal option depends on one date, so it has a formula.",
            "This depends on twenty-six, and can end early. No formula: you simulate hundreds of thousands of price paths.",
            "That cannot run on a chain. So we trained a small neural network to give the simulation's answer.",
            "It runs as a Stylus contract, inside the trade.",
        ],
    },
    {
        "name": "Proof",
        "seconds": 31,
        "svg": scene_proof,
        "say": [
            "A real trade from our test chain: ten thousand dollars of notes.",
            "The on-chain model priced it at ten thousand three hundred forty-three. The full simulation: ten thousand three hundred thirty-eight.",
            "Over a hundred and thirty thousand test points, the average gap is under three dollars, the worst thirty-eight, inside a limit fixed in advance.",
            "Where the price jumps, the contract refuses instead of guessing.",
            "And payouts never call the model. They are arithmetic on recorded prices.",
        ],
    },
    {
        "name": "Close",
        "seconds": 11,
        "svg": scene_close,
        "say": [
            "Crash cover for stock-token holders. A coupon for cash. A price anyone can check.",
            "Settled in USDG, running on Stylus, built for Robinhood Chain. Surrogate Pricer.",
        ],
    },
]


def timings():
    """Split each scene's seconds over its steps by word count."""
    deck, clock = [], 0.0
    for scene in SCENES:
        words = [len(s.split()) for s in scene["say"]]
        steps = []
        for say, n in zip(scene["say"], words):
            dur = scene["seconds"] * n / sum(words)
            steps.append({"say": say, "dur": round(dur, 2), "at": round(clock, 2)})
            clock += dur
        deck.append({"name": scene["name"], "seconds": scene["seconds"], "words": sum(words), "steps": steps})
    return deck


# ------------------------------------------------------------------------ html

CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: @BG@; color: @INK@; overflow: hidden;
    font-family: @FONT@; }
  text { font-family: @FONT@; }
  text.mono { font-family: "Liberation Mono", "DejaVu Sans Mono", Menlo, Consolas, monospace; }
  #stage { position: fixed; inset: 0; }
  .scene { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0;
    transition: opacity .5s ease; pointer-events: none; }
  .scene.cur { opacity: 1; }
  .st { opacity: 0; transition: opacity .5s ease; }
  .st.on { opacity: 1; }
  .st.on.dim { opacity: .5; }
  html.still * { transition: none !important; }
  body.idle { cursor: none; }

  #cap { position: fixed; left: 50%; bottom: 3vh; transform: translateX(-50%); width: min(1200px, 92vw);
    background: rgba(10, 12, 17, .92); border: 1px solid @LINE@; border-radius: 12px;
    padding: 14px 20px; font-size: 22px; line-height: 1.4; color: @INK@; display: none; }
  body.script #cap { display: block; }
  #cap small { color: @GOLD@; letter-spacing: .12em; font-size: 13px; margin-right: 12px; }

  #help { position: fixed; right: 20px; top: 16px; background: rgba(10, 12, 17, .94);
    border: 1px solid @LINE@; border-radius: 12px; padding: 14px 18px; font-size: 15px;
    line-height: 1.7; color: @INK2@; opacity: 0; transition: opacity .4s; pointer-events: none; }
  #help.on { opacity: 1; }
  #help b { color: @INK@; display: inline-block; min-width: 86px; }
  html.still #help, html.still #cap { display: none; }

  /* presenter window: index.html?notes */
  html.notes body { overflow: auto; }
  html.notes #stage, html.notes #cap, html.notes #help { display: none; }
  #notes { display: none; padding: 22px 26px 40px; max-width: 860px; margin: 0 auto; }
  html.notes #notes { display: block; }
  #notes .bar { display: flex; align-items: baseline; gap: 18px; position: sticky; top: 0;
    background: @BG@; padding: 6px 0 14px; border-bottom: 1px solid @LINE@; }
  #clock { font-size: 44px; font-weight: 700; font-variant-numeric: tabular-nums; }
  #target { color: @MUTED@; font-size: 18px; font-variant-numeric: tabular-nums; }
  #clock.late { color: @CRASH@; }
  #state { margin-left: auto; color: @GOLD@; font-size: 14px; letter-spacing: .12em; text-transform: uppercase; }
  #notes h2 { margin: 26px 0 8px; font-size: 14px; letter-spacing: .12em; text-transform: uppercase;
    color: @MUTED@; font-weight: 700; display: flex; justify-content: space-between; }
  #notes p { margin: 0; padding: 9px 12px; border-radius: 8px; font-size: 21px; line-height: 1.4;
    color: @MUTED@; border-left: 4px solid transparent; }
  #notes p.now { color: @INK@; background: @PANEL@; border-left-color: @GOLD@; }
  #notes p.done { opacity: .45; }
  #notes .keys { margin-top: 30px; color: @MUTED@; font-size: 14px; line-height: 1.7; }
"""
for _k, _v in {
    "BG": BG, "INK": INK, "INK2": INK2, "MUTED": MUTED, "LINE": LINE, "GOLD": GOLD,
    "PANEL": PANEL, "CRASH": CRASH, "FONT": FONT,
}.items():
    CSS = CSS.replace(f"@{_k}@", _v)

JS = """
  const DECK = @DECK@;
  const still = new URLSearchParams(location.search).has("still");
  const notesMode = new URLSearchParams(location.search).has("notes");
  if (still) document.documentElement.classList.add("still");
  if (notesMode) document.documentElement.classList.add("notes");

  const scenes = [...document.querySelectorAll(".scene")];
  const last = (i) => DECK[i].steps.length - 1;
  let i = 0, j = 0, t0 = null, playing = false, timers = [], notesWin = null;

  function render() {
    scenes.forEach((svg, k) => {
      svg.classList.toggle("cur", k === i);
      if (k !== i) return;
      svg.querySelectorAll(".st").forEach((g) => {
        const s = +g.dataset.s, off = g.dataset.off, dim = g.dataset.dim;
        g.classList.toggle("on", s <= j && !(off !== undefined && j >= +off));
        g.classList.toggle("dim", dim !== undefined && j >= +dim);
      });
    });
    document.getElementById("cap").innerHTML = "<small>SAY</small>" + DECK[i].steps[j].say;
    history.replaceState(null, "", "#" + (i + 1) + "." + j);
    tell();
  }
  function go(a, b) { i = Math.max(0, Math.min(scenes.length - 1, a)); j = Math.max(0, Math.min(last(i), b)); render(); }
  function next() {
    if (t0 === null) t0 = Date.now() - DECK[i].steps[j].at * 1000;
    if (j < last(i)) go(i, j + 1); else if (i < scenes.length - 1) go(i + 1, 0);
  }
  function prev() { if (j > 0) go(i, j - 1); else if (i > 0) go(i - 1, last(i - 1)); }
  function stop() { timers.forEach(clearTimeout); timers = []; playing = false; }
  function reset() { stop(); t0 = null; go(0, 0); }
  function play() {
    stop(); playing = true; t0 = Date.now(); go(0, 0);
    DECK.forEach((scene, a) => scene.steps.forEach((st, b) => {
      if (a || b) timers.push(setTimeout(() => go(a, b), st.at * 1000));
    }));
    const end = DECK.reduce((sum, s) => sum + s.seconds, 0);
    timers.push(setTimeout(() => { playing = false; tell(); }, end * 1000));
  }
  function tell() { if (notesWin && !notesWin.closed) notesWin.postMessage({ sp: 1, i, j, t0, playing }, "*"); }

  function key(k) {
    if (["ArrowRight", "PageDown", " ", "Enter"].includes(k)) { stop(); next(); }
    else if (["ArrowLeft", "PageUp", "Backspace"].includes(k)) { stop(); prev(); }
    else if (k === "ArrowDown") { stop(); go(i + 1, 0); }
    else if (k === "ArrowUp") { stop(); go(i - 1, 0); }
    else if (k === "Home" || k === "r" || k === "R") reset();
    else if (k === "End") { stop(); go(scenes.length - 1, last(scenes.length - 1)); }
    else if (k === "a" || k === "A") { playing ? stop() : play(); tell(); }
    else if (k === "s" || k === "S") document.body.classList.toggle("script");
    else if (k === "h" || k === "H" || k === "?") document.getElementById("help").classList.toggle("on");
    else if (k === "f" || k === "F") {
      document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
    } else if (k === "p" || k === "P") {
      notesWin = window.open(location.pathname + "?notes", "sp-notes", "width=760,height=900");
      setTimeout(tell, 400);
    } else return false;
    return true;
  }

  if (!notesMode) {
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (key(e.key)) e.preventDefault();
    });
    window.addEventListener("message", (e) => {
      if (e.data && e.data.sp === "key") key(e.data.key);
      if (e.data && e.data.sp === "hello") tell();
    });
    let idle;
    document.addEventListener("mousemove", () => {
      document.body.classList.remove("idle");
      clearTimeout(idle);
      idle = setTimeout(() => document.body.classList.add("idle"), 1500);
    });
    const m = /^#(\\d+)(?:\\.(\\d+))?$/.exec(location.hash);
    if (m) go(+m[1] - 1, +(m[2] || 0)); else render();
    if (!still && !m) {
      const help = document.getElementById("help");
      help.classList.add("on");
      setTimeout(() => help.classList.remove("on"), 5000);
    }
  } else {
    // presenter window: the script, the clock, and the keys forwarded to the stage
    const root = document.getElementById("list");
    DECK.forEach((scene, a) => {
      const h = document.createElement("h2");
      const end = scene.steps[0].at + scene.seconds;
      h.innerHTML = "<span>" + (a + 1) + ". " + scene.name + "</span><span>until " + fmt(end) + "</span>";
      root.appendChild(h);
      scene.steps.forEach((st, b) => {
        const p = document.createElement("p");
        p.id = "s" + a + "_" + b;
        p.textContent = st.say;
        root.appendChild(p);
      });
    });
    let cur = { i: 0, j: 0, t0: null, playing: false };
    function fmt(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
    function paint() {
      DECK.forEach((scene, a) => scene.steps.forEach((st, b) => {
        const p = document.getElementById("s" + a + "_" + b);
        const now = a === cur.i && b === cur.j;
        p.classList.toggle("now", now);
        p.classList.toggle("done", a < cur.i || (a === cur.i && b < cur.j));
        if (now) p.scrollIntoView({ block: "center", behavior: "smooth" });
      }));
      document.getElementById("state").textContent = cur.playing ? "autoplay" : "manual";
    }
    function tick() {
      const st = DECK[cur.i].steps[cur.j];
      const t = cur.t0 === null ? 0 : (Date.now() - cur.t0) / 1000;
      const clock = document.getElementById("clock");
      clock.textContent = fmt(t);
      clock.classList.toggle("late", t > st.at + st.dur + 2);
      document.getElementById("target").textContent = "this line " + fmt(st.at) + " to " + fmt(st.at + st.dur);
    }
    window.addEventListener("message", (e) => { if (e.data && e.data.sp === 1) { cur = e.data; paint(); tick(); } });
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || !window.opener) return;
      if (e.key === "p" || e.key === "P" || e.key === "f" || e.key === "F") return;
      window.opener.postMessage({ sp: "key", key: e.key }, "*");
      e.preventDefault();
    });
    setInterval(tick, 250);
    paint();
    tick();
    if (window.opener) window.opener.postMessage({ sp: "hello" }, "*");
  }
"""


def build_html() -> str:
    deck = timings()
    svgs = []
    for n, scene in enumerate(SCENES):
        inner = scene["svg"]()
        steps = {int(s) for s in re.findall(r'data-s="(\d+)"', inner)}
        assert max(steps) == len(scene["say"]) - 1, (scene["name"], steps, len(scene["say"]))
        svgs.append(
            f'<svg class="scene" data-n="{n + 1}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{esc(scene["name"])}">\n'
            f'<rect width="{W}" height="{H}" fill="{BG}"/>\n{inner}\n</svg>'
        )
    stage = "\n".join(svgs)
    total = sum(s["seconds"] for s in SCENES)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Surrogate Pricer: the pitch, {total // 60}:{total % 60:02d}</title>
<style>{CSS}</style>
</head>
<body>
<div id="stage">
{stage}
</div>
<div id="cap"></div>
<div id="help">
  <b>→ / ←</b>next / previous step<br>
  <b>↓ / ↑</b>next / previous scene<br>
  <b>F</b>fullscreen<br>
  <b>A</b>autoplay on the target times<br>
  <b>P</b>presenter window: script and clock<br>
  <b>S</b>script on the stage (rehearsal)<br>
  <b>R</b>back to the start<br>
  <b>H</b>this list
</div>
<div id="notes">
  <div class="bar"><span id="clock">0:00</span><span id="target"></span><span id="state">manual</span></div>
  <div id="list"></div>
  <p class="keys">Keys typed here drive the stage window: → next, ← back, A autoplay, R reset.
  Record the stage window only.</p>
</div>
<script>{JS.replace("@DECK@", json.dumps(deck))}</script>
</body>
</html>
"""


def report():
    deck = timings()
    clock = 0
    print(f"{'scene':<24}{'from':>6}{'secs':>6}{'words':>7}{'wpm':>6}")
    for scene in deck:
        wpm = scene["words"] / scene["seconds"] * 60
        flag = "  <- fast" if wpm > WPM + 10 else ""
        print(f"{scene['name']:<24}{clock // 60:>3}:{clock % 60:02d}{scene['seconds']:>6}{scene['words']:>7}{wpm:>6.0f}{flag}")
        clock += scene["seconds"]
    words = sum(s["words"] for s in deck)
    print(f"{'total':<24}{clock // 60:>3}:{clock % 60:02d}{'':>6}{words:>7}{words / clock * 60:>6.0f}")


def frames(out: Path):
    chrome = next((p for p in ("google-chrome", "chromium", "chromium-browser") if shutil.which(p)), None)
    if not chrome:
        raise SystemExit("no Chrome found for --frames")
    out.mkdir(parents=True, exist_ok=True)
    url = (OUT / "index.html").as_uri()
    for a, scene in enumerate(timings(), start=1):
        for b, _ in enumerate(scene["steps"]):
            png = out / f"{a}-{b}.png"
            # new headless reserves 87 px of the window for chrome it does not draw
            subprocess.run(
                [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=1", "--window-size=1920,1167",
                 f"--screenshot={png}", f"{url}?still#{a}.{b}"],
                check=True, capture_output=True, timeout=90,
            )
            try:
                from PIL import Image
                with Image.open(png) as im:
                    im.crop((0, 0, 1920, 1080)).save(png)
            except ImportError:
                pass  # the stage is the top 1920x1080 of the image
            print(png)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames", type=Path, metavar="DIR", help="also write one PNG per step")
    args = ap.parse_args()
    (OUT / "index.html").write_text(build_html())
    print("index.html")
    report()
    if args.frames:
        frames(args.frames)


if __name__ == "__main__":
    main()
