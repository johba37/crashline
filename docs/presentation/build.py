#!/usr/bin/env python3
"""Build the two-minute pitch video deck: one self-contained index.html.

Six scenes on a 16:9 stage with no chrome, so a screen recording is the video.
Each scene builds in steps; every step has the line to say and a target time.

    python3 build.py                 # writes index.html, prints the timing table
    python3 build.py --frames DIR    # also one 1920x1080 PNG per step (needs Chrome)

Keys in the browser: right/left step, up/down scene, F fullscreen, A autoplay on
the target times, P presenter window (script + clock), S script on the stage
(rehearsal only), R reset, H this list.

Words and look are the frontend's. The words: the landing page and the dashboard
glossary (Crashline, Protect, Earn, cover, crash line, weekly check, weekly income,
the pot; amounts in USDG). The look is read from the frontend at build time, so
the deck follows it:
  frontend/src/theme.css               the colours (dark theme)
  frontend/src/components/galaxy.ts    the night sky (node 22.13+ strips its types)
  frontend/src/components/Logo.tsx     the mark
  frontend/src/assets                  the hero ship, the Stylus logomark
  fonts/                               Syne, Jost and Russo One (SIL OFL), copied
                                       from the frontend's @fontsource packages

Numbers on the slides and where they come from:
  182B for AIG          docs/pitch.md (Congressional Research Service R42953)
  149.4B                ~/arb-hackathon/docs/structured-notes-market.md (fact 1)
  0.25%/week, 1.0675    backend/scenarios/happy_path.py TERMS (coupon 25 bps, 26 + 1 periods)
  0.8475                happy_path-clock.log, series B (ends at 78%, knocked in)
  85.50 for the cover   model/k3 on day one at vol 55%, the landing page's example (see scene_sides)
  10,343 vs 10,338.2    happy_path-clock.log, trade 1 on series A (student vs teacher v3)
  8.4 max over 6 trades happy_path-clock.log, section f
  38.0 / 15.9 / 2.68    docs/k3-vol-input.md, sets T2 and T3 (187,182 points, gate 50; see scene_proof)
  7,465 / 23,901 bytes  model/k3/student_export.json, README status table
"""

import argparse
import base64
import json
import math
import random
import re
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
FRONTEND = OUT.parents[1] / "frontend"

# Deploy.s.sol was broadcast to Robinhood Chain testnet (46630) on 2026-10-02: deployments/46630.json.
DEPLOYED_ON_ROBINHOOD_TESTNET = True
REPO_URL = "github.com/johba37/crashline"

W, H = 1600, 900


def theme():
    """The dark theme's colours: the defaults of theme.css, before any :root override."""
    css = (FRONTEND / "src/theme.css").read_text()
    block = css[css.index("@theme static"):css.index(":root {")]
    return dict(re.findall(r"--color-([\w-]+):\s*([^;]+);", block))


C = theme()
BG = C["surface"]
LINE = C["line"]
LINE2 = C["line-strong"]
INK = C["ink"]
MUTED = C["ink-muted"]
ACCENT = C["accent"]  # the call to action and the crash line
ACCENT_SOFT = C["accent-soft"]
PROTECT = C["protect"]  # the two sides of a note
EARN = C["earn"]
# Steel panels: text, lines and insets inside one take the panel's own values.
PANEL = C["panel"]
PANEL_MUTED = C["panel-ink-muted"]
PANEL_LINE = C["panel-line-strong"]
# Polished plates are bright in both themes, so text on them is dark.
PLATE_INK = C["plate-ink"]
PLATE_MUTED = C["plate-ink-muted"]
PLATE_EDGE = C["plate-edge"]
# The three ways a note can end. Hues from the theme (nebula-violet, go, accent), the green one
# step deeper than --color-go so all three sit in one lightness band. Validated as a set on the
# surface, the steel panel and the plot ground (lightness band, chroma, colour-blind and
# normal-vision separation, 3:1 contrast).
EARLY = C["nebula-violet"]
NOCRASH = "#22ac84"
CRASH = ACCENT

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


def T(x, y, s, size=28, fill=INK, anchor="start", weight=400, spacing=0, face=None):
    """Jost, or with face "d" the display face (Syne), "wm" the wordmark's, "mono" code."""
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if weight != 400:
        attrs += f' font-weight="{weight}"'
    if spacing:
        attrs += f' letter-spacing="{spacing}"'
    if face:
        attrs += f' class="{face}"'
    return f"<text {attrs}>{esc(s)}</text>"


def lines(x, y, rows, size=26, fill=MUTED, leading=None, anchor="start", weight=400):
    leading = leading or round(size * 1.4)
    return "\n".join(T(x, y + n * leading, row, size, fill, anchor, weight) for n, row in enumerate(rows))


def money(x, y, value, size=28, fill=INK, anchor="end", weight=400, unit_fill=MUTED, unit_size=None):
    """An amount with its unit, the unit smaller and quieter."""
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" class="num"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if weight != 400:
        attrs += f' font-weight="{weight}"'
    unit = f'<tspan font-size="{unit_size or round(size * 0.62)}" font-weight="400" fill="{unit_fill}"> USDG</tspan>'
    return f"<text {attrs}>{esc(value)}{unit}</text>"


def rect(x, y, w, h, rx=0, fill="none", stroke=None, sw=1.5, opacity=None):
    extra = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    if opacity is not None:
        extra += f' opacity="{opacity}"'
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{extra}/>'


def panel(x, y, w, h):
    """Steel, the material of cards."""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="24" '
        f'fill="url(#steel)" stroke="url(#steel-edge)" stroke-width="1.5" filter="url(#lift)"/>'
    )


def plate(x, y, w, h, metal=""):
    """Polished plate, the material of diagram nodes: silver, or with metal "-accent" copper."""
    box = f'x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="18"'
    return (
        f'<rect {box} fill="url(#plate{metal})" stroke="{PLATE_EDGE}" stroke-width="1.5" filter="url(#lift)"/>'
        f'<rect {box} fill="url(#sheen)"/>'
    )


def tint(x, y, w, h, color, rx=14):
    """A flat tint in a lane's colour, as the nodes of the landing page's flow map."""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{color}" '
        f'fill-opacity="0.1" stroke="{color}" stroke-opacity="0.45" stroke-width="1.5"/>'
    )


def lane(x, y, w, name, color, icon):
    """A side of a note by name: Protect or Earn, with its icon in its colour."""
    return "\n".join([
        tint(x, y, w, 56, color),
        f'<use href="#{icon}" x="{x + 16:.1f}" y="{y + 12:.1f}" width="32" height="32" color="{color}"/>',
        T(x + 58, y + 38, name, 28, INK, weight=600),
    ])


def node(x, y, w, h, label, size=24):
    return tint(x, y, w, h, PANEL_LINE, 12) + T(x + w / 2, y + h / 2 + size * 0.34, label, size, INK, "middle", 500)


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


def key(x, y, color):
    """A path's colour next to its name: a short piece of the line, ending in its dot."""
    return seg(x, y, x + 30, y, color, 3.5) + dot(x + 34, y, color, 6, PANEL)


def arrow(x1, y, x2, stroke=MUTED, sw=3):
    return (
        seg(x1, y, x2 - 6, y, stroke, sw)
        + f'<polygon points="{x2},{y} {x2 - 16},{y - 9} {x2 - 16},{y + 9}" fill="{stroke}"/>'
    )


def lockup(x, y, size):
    """The mark and the wordmark, slanted like the ship. y is the wordmark's baseline."""
    mark_h = size * 1.2
    mark_w = mark_h * 1040 / 978
    return "\n".join([
        f'<use href="#mark" x="{x:.1f}" y="{y - size * 0.36 - mark_h / 2:.1f}" width="{mark_w:.1f}" '
        f'height="{mark_h:.1f}" color="{INK}"/>',
        f'<text class="wm" font-size="{size}" fill="{INK}" '
        f'transform="translate({x + mark_w + size * 0.42:.1f} {y:.1f}) skewX(-11)">CrashLine</text>',
    ])


def ship(x, y, w):
    """The hero's ship. The art is 1440 x 617."""
    return f'<use href="#ship" transform="translate({x:.1f} {y:.1f}) scale({w / 1440:.4f})"/>'


def step(k, *parts, dim=None, off=None):
    """Show from step k; fade back from step `dim`; leave the stage from step `off`."""
    attrs = f'class="st" data-s="{k}"'
    if dim is not None:
        attrs += f' data-dim="{dim}"'
    if off is not None:
        attrs += f' data-off="{off}"'
    return f"<g {attrs}>\n" + "\n".join(parts) + "\n</g>"


def head(title):
    return T(80, 124, title, 42, INK, face="d")


# ---------------------------------------------------------------------- scenes

def scene_hook():
    # The opener of docs/pitch.md, "The Big Short, fixed": 2008's crash insurance had three flaws,
    # and Crashline is the fix for them (never "a 2008 product"). Only the numbers verified there.
    aig = step(
        0,
        T(74, 390, "$182B", 230, INK, weight=600, spacing=-6, face="num"),
        T(80, 462, "of US government support for AIG in the 2008 crisis:", 40, MUTED),
        T(80, 514, "it had sold crash insurance it couldn’t cover.", 40, MUTED),
        off=2,
    )
    flaws = step(
        1,
        rect(80, 572, 120, 5, 2, ACCENT),
        T(80, 648, "Priced in private.", 44, INK, weight=600, spacing=-0.5),
        T(80, 710, "Sold without the money behind it.", 44, INK, weight=600, spacing=-0.5),
        T(80, 772, "Valued by the banks on the other side.", 44, INK, weight=600, spacing=-0.5),
        off=2,
    )
    sources = step(
        0,
        T(80, 852, "Source: Congressional Research Service, R42953 (about $70B from the Treasury, $112B from the New York Fed)", 20, MUTED),
        off=2,
    )
    # The landing page's hero: the name, its headline and the ship at the right edge.
    title = step(
        2,
        ship(600, 150, 1000),
        lockup(80, 226, 50),
        T(80, 352, "Crash", 84, INK, face="d"),
        T(80, 438, "insurance", 84, INK, face="d"),
        T(80, 524, "that pays.", 84, INK, face="d"),
        T(80, 596, "Priced in public. Fully backed. Valued in public.", 32, MUTED),
        T(80, 822, "A small AI model on Arbitrum Stylus  ·  coins and stocks on Robinhood Chain  ·  settled in USDG", 26, MUTED),
    )
    return "\n".join([aig, flaws, sources, title])


# Weekly fixings in percent of the starting price, index 0 = start, 27 = the end.
# From week 11 on, the last two are the fixings of series A and B in the logged happy
# path; the first ten weeks are drawn (the scenario strikes its series mid-life). The first
# is drawn: it ends early at the 10th check, as in the worked example (scene_sides).
PATH_EARLY = [100, 94, 88, 84, 82, 85, 88, 90, 94, 98, 101]
PATH_NOCRASH = [100, 98, 96, 94, 95, 92, 90, 91, 88, 89, 88,
                86, 82, 79, 84, 88, 93, 89, 85, 80, 76, 81, 87, 92, 90, 88, 91, 92]
PATH_CRASH = [100, 95, 89, 82, 74, 66, 58, 62, 68, 74, 80,
              79, 75, 72, 76, 81, 78, 73, 69, 72, 75, 77, 74, 79, 81, 76, 77, 78]


def scene_payoff():
    # The plot, on a ground of its own like the app's position graph: the starting price dashed,
    # the crash line in the accent, and below it the band where the money is at risk.
    gx, gy, gw, gh = 80, 190, 800, 630
    left, right, top, bot = gx + 32, gx + gw - 32, gy + 76, gy + 540

    def x_of(i):
        return left + i / 27 * (right - left)

    def y_of(pct):
        return top + (103 - pct) / 53 * (bot - top)

    def pts(path):
        return [(x_of(i), y_of(p)) for i, p in enumerate(path)]

    frame = [head("A weekly income, unless the stock crashes.")]
    frame.append(rect(gx, gy, gw, gh, 18, C["plot"], LINE))
    frame.append(rect(left, y_of(60), right - left, bot - y_of(60), 0, ACCENT_SOFT))
    frame.append(seg(left, y_of(100), right, y_of(100), LINE2, 1.5, "8 7"))
    frame.append(T(right, y_of(100) - 14, "starting price", 24, MUTED, "end", 500))
    frame.append(seg(left, y_of(60), right, y_of(60), ACCENT, 2))
    frame.append(T(right, y_of(60) - 14, "crash line: 60% of the starting price", 24, INK, "end", 500))
    frame.append(seg(left, bot, right, bot, LINE2, 1.5))
    for i in range(28):
        frame.append(seg(x_of(i), bot, x_of(i), bot + (16 if i in (0, 27) else 9), LINE2, 2))
    frame.append(T(left, bot + 52, "start", 24, MUTED, weight=500))
    frame.append(T((left + right) / 2, bot + 52, "26 weekly checks", 24, MUTED, "middle", 500))
    frame.append(T(right, bot + 52, "end", 24, MUTED, "end", 500))

    def card(n, color, title, rows):
        y = gy + n * 216
        return "\n".join([
            panel(912, y, 608, 198),
            key(944, y + 50, color),
            T(1000, y + 59, title, 28, INK, weight=600),
            lines(944, y + 110, rows, 25, PANEL_MUTED, 40),
        ])

    early = pts(PATH_EARLY)
    nocrash = pts(PATH_NOCRASH)
    crash = pts(PATH_CRASH)

    parts = ["\n".join(frame)]
    parts.append(step(
        1,
        poly(early, EARLY, 3.5),
        dot(*early[-1], EARLY),
        T(early[-1][0] + 16, early[-1][1] - 14, "ends early", 24, INK, weight=500),
        dim=2,
    ))
    parts.append(step(1, card(0, EARLY, "Ends early", [
        "Back at the starting price at a weekly check.",
        "1 USDG back, plus 0.25% a week so far.",
    ])))
    parts.append(step(
        2,
        poly(nocrash, NOCRASH, 3.5),
        dot(*nocrash[-1], NOCRASH),
        T(right - 14, nocrash[-1][1] - 22, "stays above the crash line", 24, INK, "end", 500),
        dim=3,
    ))
    parts.append(step(2, card(1, NOCRASH, "No crash", [
        "Never below the crash line at a check.",
        "1 USDG back, plus all the income: 1.0675.",
    ])))
    parts.append(step(
        3,
        poly(crash, CRASH, 3.5),
        dot(*crash[6], CRASH),
        T(crash[6][0] + 16, crash[6][1] + 36, "below the crash line at a check", 24, INK, weight=500),
        dot(*crash[-1], CRASH),
        T(right - 14, crash[-1][1] + 62, "ends at 78%", 24, INK, "end", 500),
    ))
    parts.append(step(3, card(2, CRASH, "Crash", [
        "Below the crash line at a check, and ends down.",
        "You take the fall: at 78%, you get 0.8475.",
    ])))
    return "\n".join(parts)


def scene_sides():
    # The landing page's worked example (frontend/src/components/landing/HowItWorks.tsx): 1,000 USDG
    # of TSLA, covered on the note's first day. What cover costs: model/k3 at strike, spot at par,
    # vol 55% (the TSLA listing): NOTE 9820 bps, so cover = 10675 - 9820 = 855 bps, 85.50 on 1,000.
    # That is the model's own price: the Desk sells a little above it (at the listing's 2-point
    # band and 20 bps bid, 10675 - (9788 - 20) = 907). Recompute with tools/pricer_quant.py.
    # Payouts are (1.0675 - note payout) per unit of cover, see NoteSeries._redeemValue: ending
    # early at check 10 gives back the 17 weeks of income left, 17 x 2.50.
    px, py, pw, ph = 80, 258, 1440, 478
    cx = [px + 68, 950, 1190, px + pw - 36]  # label, stock, cover pays, together (right edges after the first)

    def row(y, color, label, stock, cover, total, bold=False, rule=True):
        w = 600 if bold else 400
        return "\n".join([
            rect(px + 36, y - 30, 6, 40, 3, color),
            T(cx[0], y, label, 26, INK, weight=w),
            money(cx[1], y, stock, 28, PANEL_MUTED, unit_fill=PANEL_MUTED),
            money(cx[2], y, cover, 28, INK, weight=w, unit_fill=PANEL_MUTED),
            money(cx[3], y, total, 28, INK, weight=w, unit_fill=PANEL_MUTED),
            seg(px + 36, y + 28, px + pw - 36, y + 28, PANEL_LINE, 1, opacity=0.45) if rule else "",
        ])

    base = [
        head("One pot, locked up front. Two sides split it."),
        T(80, 210, "The pot: 1.0675 USDG locked per note, split between", 30, MUTED),
        lane(784, 172, 170, "Protect", PROTECT, "i-protect"),
        T(972, 210, "and", 30, MUTED),
        lane(1037, 172, 138, "Earn", EARN, "i-earn"),
    ]
    table_head = [
        panel(px, py, pw, ph),
        T(px + 36, py + 62, "You hold 1,000 USDG of TSLA and cover all of it for 85.50 USDG.", 30, INK, weight=600),
        T(cx[0], py + 122, "How it can end", 22, PANEL_MUTED, weight=500),
        T(cx[1], py + 122, "TSLA is worth", 22, PANEL_MUTED, "end", 500),
        T(cx[2], py + 122, "Cover pays you", 22, PANEL_MUTED, "end", 500),
        T(cx[3], py + 122, "Together, before the 85.50", 22, PANEL_MUTED, "end", 500),
        seg(px + 36, py + 142, px + pw - 36, py + 142, PANEL_LINE, 1.5, opacity=0.7),
        row(py + 192, EARLY, "Ends early: back at the start at the 10th check", "1,010.00", "42.50", "1,052.50"),
        row(py + 262, NOCRASH, "No crash: dips to 78%, never below the crash line", "780.00", "0.00", "780.00"),
        T(px + 36, py + 452, "The model’s price on the note’s first day. The Desk sells a little above it.", 20, PANEL_MUTED),
    ]
    crash = [
        row(py + 332, CRASH, "Crash: below the crash line at a check, ends at 78%", "780.00", "220.00", "1,000.00", True),
        row(py + 402, CRASH, "Crash: below the crash line at a check, ends at 50%", "500.00", "500.00", "1,000.00", True, False),
    ]
    other = [
        T(80, 790, "Earn is the other side: it takes over that risk, for a weekly income of 0.25%.", 28, MUTED),
        T(80, 836, "Fully backed on both sides. No margin calls, no liquidations.", 28, INK, weight=600),
    ]
    return "\n".join([
        "\n".join(base),
        step(1, *table_head),
        step(2, *crash),
        step(3, *other),
    ])


def futures(count=18, seed=23):
    """Possible futures for a price over a note's life, as on the landing page (priceArt.ts):
    a random step every week and now and then a sudden drop. An illustration, not model output."""
    rng = random.Random(seed)
    swing = 0.55 / math.sqrt(52)
    paths = []
    for _ in range(count):
        value, crashed, path = 1.0, False, [1.0]
        for week in range(1, 28):
            value *= math.exp(swing * rng.gauss(0, 1) - swing * swing / 2)
            if rng.random() < 0.03:
                value *= 0.72 + 0.16 * rng.random()
            crashed = crashed or (week < 27 and value < 0.6)
            path.append(value)
        paths.append((path, crashed))
    return paths


def scene_how():
    # How a price is made, as three plates: the landing page's picture, with the formula a plain
    # option has in front of it. The model's plate is the copper one, and Stylus gets the steel strip.
    cols = [(80, 400), (540, 400), (1000, 520)]
    py, ph = 176, 462
    box_y, box_h = py + 124, 190

    def column(n, label, title, rows, art, tinted=""):
        x, w = cols[n]
        return "\n".join([
            plate(x, py, w, ph, tinted),
            T(x + 28, py + 50, label, 22, PLATE_MUTED, weight=500),
            T(x + 28, py + 94, title, 34, PLATE_INK, weight=600),
            art,
            lines(x + 28, py + 392, rows, 25, PLATE_MUTED, 36),
        ])

    # one date: a put's payoff, a kinked line
    bx = cols[0][0] + 28
    kink = "\n".join([
        seg(bx, box_y + box_h, bx + 344, box_y + box_h, PLATE_INK, 1.5, opacity=0.5),
        poly([(bx + 16, box_y + 20), (bx + 160, box_y + 150), (bx + 334, box_y + 150)], PLATE_INK, 3.5),
        seg(bx + 160, box_y + 150, bx + 160, box_y + box_h, PLATE_INK, 1.5, "5 6", 0.6),
        T(bx + 344, box_y + box_h + 30, "price on the final day", 20, PLATE_MUTED, "end"),
    ])

    # many dates: possible futures, the ones that fall below the crash line in bold
    bx = cols[1][0] + 28

    def fan_y(value):
        return box_y + (1.9 - min(1.9, max(0.3, value))) / 1.6 * box_h

    fan = [
        seg(bx, fan_y(0.6), bx + 344, fan_y(0.6), PLATE_INK, 1.5, "6 5"),
        T(bx, fan_y(0.6) + 24, "crash line", 20, PLATE_INK),
    ]
    for path, crashed in futures():
        points = [(bx + week * 344 / 27, fan_y(value)) for week, value in enumerate(path)]
        fan.append(poly(points, PLATE_INK, 2.4 if crashed else 1.4, 0.9 if crashed else 0.3))
    fan = "\n".join(fan)

    # the model: what it reads from the chain, its hidden rows, one price
    bx = cols[2][0] + 28
    inputs = ["Price today", "How much it swings", "Time to next check", "Checks left", "Crash line crossed?"]
    layers = [len(inputs), 7, 6, 5, 5, 1]
    mid = box_y + box_h / 2 + 6
    nodes = []
    for li, count in enumerate(layers):
        x = bx + 200 + li * 50
        gap = 40 if li == 0 else 28
        nodes.append([(x, mid + (k - (count - 1) / 2) * gap) for k in range(count)])
    net = []
    for a, b in zip(nodes, nodes[1:]):
        for p in a:
            for q in b:
                net.append(seg(p[0], p[1], q[0], q[1], PLATE_INK, 1, opacity=0.22))
    for layer in nodes:
        for x, y in layer:
            net.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{PLATE_INK}"/>')
    for label, (x, y) in zip(inputs, nodes[0]):
        net.append(T(x - 16, y + 7, label, 20, PLATE_INK, "end"))
    ox, oy = nodes[-1][0]
    net.append(T(ox + 6, oy - 18, "Price", 20, PLATE_INK, "end", 500))
    net = "\n".join(net)

    sx, sy = 80, 662
    return "\n".join([
        head("No formula, so a small AI model prices it."),
        step(0, column(0, "One date", "A formula.", [
            "If only the last day counted,",
            "a simple formula would do.",
        ], kink), dim=2),
        step(1, arrow(486, py + ph / 2, 534), column(1, "26 checks, and it can end early", "A simulation.", [
            "262,144 possible futures.",
            "Too much work for a blockchain.",
        ], fan), dim=3),
        step(2, arrow(946, py + ph / 2, 994), column(2, "Learned from the simulation", "A small AI model.", [
            "7,465 numbers, integer math.",
            "The whole model is 23.9 KB.",
        ], net, "-accent")),
        step(
            3,
            panel(sx, sy, 1440, 178),
            f'<use href="#stylus" transform="translate({sx + 40} {sy + 43}) scale(0.092)"/>',
            T(sx + 168, sy + 60, "Made possible by Arbitrum Stylus", 30, INK, weight=600),
            T(sx + 168, sy + 104, "Every price is computed fresh, on-chain, inside the trade itself.", 26, PANEL_MUTED),
            T(sx + 168, sy + 142, "The model is public: anyone can re-run the simulation and check a price.", 26, PANEL_MUTED),
        ),
    ])


def scene_proof():
    lx, ly, lw, lh = 80, 176, 760, 664
    rx, rw = 880, 640

    # Gap to the simulation on a line from 0 to the limit, USDG per 10,000 USDG. The figures are
    # T2 and T3 of docs/k3-vol-input.md together, the two sets never used before the model was
    # final (143,484 + 43,698 points): worst 38.0 (T3), mean 2.68 (2.63 and 2.85, weighted), and
    # 99% within 15.9 (T3's p99, T2's is 14.0, so it holds for both together). Not the gate set T
    # (37.9 / 14.1 / 2.65): its first result shaped the fix, so the doc calls it optimistic.
    ax0, ax1, ay = lx + 40, lx + lw - 60, ly + 536

    def gx(v):
        return ax0 + v / 50 * (ax1 - ax0)

    scale = [
        seg(ax0, ay, ax1, ay, PANEL_LINE, 2),
        seg(ax0, ay - 8, ax0, ay + 8, PANEL_LINE, 2),
        seg(ax1, ay - 22, ax1, ay + 22, INK, 3),
        T(ax0, ay + 52, "0", 24, PANEL_MUTED, "middle"),
        T(ax1, ay + 52, "limit 50", 24, INK, "end", 600),
        dot(gx(2.68), ay, INK, 9, PANEL),
        T(gx(2.68) + 14, ay + 52, "average 2.68", 24, INK),
        dot(gx(15.9), ay, INK, 9, PANEL),
        T(gx(15.9), ay - 28, "99% within 15.90", 24, INK, "middle"),
        dot(gx(38.0), ay, INK, 9, PANEL),
        T(gx(38.0), ay - 28, "worst 38.00", 24, INK, "middle"),
    ]

    return "\n".join([
        head("It matches the simulation, or it refuses."),
        panel(lx, ly, lw, lh),
        step(0, T(lx + 36, ly + 58, "A real trade on our dev node: 10,000 USDG of notes", 26, PANEL_MUTED)),
        step(
            1,
            T(lx + 36, ly + 124, "On-chain model", 24, PANEL_MUTED, weight=500),
            money(lx + 36, ly + 190, "10,343", 52, INK, "start", 600, PANEL_MUTED, 26),
            T(lx + 344, ly + 124, "Simulation, 262,144 futures", 24, PANEL_MUTED, weight=500),
            money(lx + 344, ly + 190, "10,338.20", 52, PANEL_MUTED, "start", 600, PANEL_MUTED, 26),
            T(lx + 36, ly + 250, "Gap: 4.80 USDG, or 0.05%. All six trades in the run: within 8.40.", 24, INK),
        ),
        step(
            2,
            seg(lx + 36, ly + 300, lx + lw - 36, ly + 300, PANEL_LINE, 1.5, opacity=0.6),
            T(lx + 36, ly + 356, "Tested in 187,182 situations", 30, INK, weight=600),
            lines(lx + 36, ly + 396, [
                "Kept aside until the model was final.",
                "Gap to the simulation, in USDG per 10,000 USDG of notes:",
            ], 24, PANEL_MUTED, 34),
            *scale,
            T(lx + 36, ly + 636, "The limit was fixed before the test.", 24, PANEL_MUTED),
        ),
        step(
            3,
            panel(rx, ly, rw, 322),
            T(rx + 36, ly + 62, "It refuses instead of guessing.", 32, INK, weight=600),
            rect(rx + 36, ly + 92, 372, 52, 10, C["hold-soft"]),
            T(rx + 54, ly + 127, "revert Uncertified(1)", 26, C["hold"], face="mono"),
            lines(rx + 36, ly + 200, [
                "On a check day, right at the crash line, the",
                "fair price jumps. There, the model gives no price.",
            ], 26, PANEL_MUTED, 38),
        ),
        step(
            4,
            panel(rx, ly + 342, rw, 322),
            T(rx + 36, ly + 404, "Payouts never call the model.", 32, INK, weight=600),
            node(rx + 36, ly + 434, 196, 52, "weekly checks"),
            arrow(rx + 246, ly + 460, rx + 290, PANEL_MUTED),
            node(rx + 304, ly + 434, 150, 52, "fixed rules"),
            arrow(rx + 468, ly + 460, rx + 512, PANEL_MUTED),
            T(rx + 528, ly + 469, "USDG", 26, INK, weight=600),
            lines(rx + 36, ly + 542, [
                "The model only prices trades you are",
                "free to decline.",
            ], 26, PANEL_MUTED, 38),
        ),
    ])


def scene_close():
    chain = "Live on Robinhood Chain testnet" if DEPLOYED_ON_ROBINHOOD_TESTNET else "Built for Robinhood Chain"

    def promise(y, color, text):
        return rect(80, y - 36, 8, 46, 4, color) + T(112, y, text, 46, INK, weight=600, spacing=-0.5)

    def pill(x, text, width):
        return rect(x, 628, width, 56, 28, "none", LINE2, 2) + T(x + width / 2, 665, text, 26, INK, "middle", 500)

    return "\n".join([
        step(
            0,
            promise(150, PROTECT, "Crash insurance for your coins and stocks."),
            promise(230, EARN, "A weekly income for cash."),
            promise(310, ACCENT, "A price anyone can check."),
        ),
        step(
            1,
            ship(800, 330, 800),
            lockup(80, 550, 104),
            pill(80, "Arbitrum Stylus", 230),
            pill(330, "Settled in USDG", 240),
            pill(590, chain, 70 + len(chain) * 12),
            T(80, 800, REPO_URL, 34, INK, weight=600),
            rect(80, 814, 582, 3, 1.5, ACCENT),
            T(1520, 762, "The market: $149B of structured notes sold in the US in 2024 (SRP)", 26, MUTED, "end"),
            T(1520, 800, "A model of 7,465 numbers  ·  140 contract tests", 26, MUTED, "end"),
        ),
    ])


# Each step: the line to say while it is on screen. Numbers are spelled out so the
# word count is what you actually speak. `hero` scenes show the night sky as it is;
# the others dim it, as the landing page does below its hero.
SCENES = [
    {
        "name": "Hook",
        "seconds": 17,
        "svg": scene_hook,
        "hero": True,
        "say": [
            "In The Big Short, crash insurance paid off. But its seller, AIG, needed a hundred eighty-two billion from the government.",
            "It was priced in private, sold without the money, and valued by the banks.",
            "Crashline is crash insurance with all three fixed.",
        ],
    },
    {
        "name": "The note",
        "seconds": 19,
        "svg": scene_payoff,
        "say": [
            "The insurance is a note on one stock, checked weekly for twenty-six weeks.",
            "Back at its starting price? It ends early: your dollar back, plus income.",
            "Never below the crash line at sixty percent? Dollar back, all the income.",
            "Below it, and it finishes down? You take the fall.",
        ],
    },
    {
        "name": "Two sides",
        "seconds": 19,
        "svg": scene_sides,
        "say": [
            "The pot is locked up front and split between two sides: Protect and Earn.",
            "Hold a thousand dollars of Tesla: cover costs about eighty-five.",
            "Cross the crash line, and it pays your whole loss back.",
            "Earn takes the other side, for a weekly income.",
        ],
    },
    {
        "name": "Computed on-chain by AI",
        "seconds": 23,
        "svg": scene_how,
        "say": [
            "A normal option depends on one date, so it has a formula.",
            "This depends on twenty-six, and can end early. No formula: you play out hundreds of thousands of possible futures.",
            "That is too much work for a blockchain. So a small AI model learned the simulation's answers.",
            "It runs on Arbitrum Stylus, inside the trade.",
        ],
    },
    {
        "name": "Every price is provable",
        "seconds": 31,
        "svg": scene_proof,
        "say": [
            "A real trade from our test chain: ten thousand dollars of notes.",
            "The on-chain model priced it at ten thousand three hundred forty-three. The full simulation: ten thousand three hundred thirty-eight.",
            "Over a hundred and eighty thousand test situations, the average gap is under three dollars, the worst thirty-eight, inside a limit fixed in advance.",
            "Where the price jumps, the model refuses instead of guessing.",
            "And payouts never call the model. Fixed rules decide them, on recorded prices.",
        ],
    },
    {
        "name": "Close",
        "seconds": 11,
        "svg": scene_close,
        "hero": True,
        "say": [
            "Crash insurance for coins and stocks. A weekly income for cash. A price anyone can check.",
            "Settled in USDG, running on Stylus, live on Robinhood Chain testnet. Crashline."
            if DEPLOYED_ON_ROBINHOOD_TESTNET else
            "Settled in USDG, running on Stylus, built for Robinhood Chain. Crashline.",
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
        deck.append({"name": scene["name"], "seconds": scene["seconds"], "words": sum(words),
                     "hero": scene.get("hero", False), "steps": steps})
    return deck


# ------------------------------------------------------------- frontend assets

def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def font_faces() -> str:
    faces = [("Jost", 400, "jost-latin-400"), ("Jost", 500, "jost-latin-500"), ("Jost", 600, "jost-latin-600"),
             ("Syne", 800, "syne-latin-800"), ("Russo One", 400, "russo-one-latin-400")]
    return "\n".join(
        f'  @font-face {{ font-family: "{family}"; font-weight: {weight}; font-style: normal; '
        f'src: url({data_uri(OUT / "fonts" / f"{name}-normal.woff2", "font/woff2")}) format("woff2"); }}'
        for family, weight, name in faces
    )


def galaxy_js() -> str:
    """The landing page's sky code as plain JavaScript."""
    node = shutil.which("node")
    if not node:
        raise SystemExit("node 22.13+ is needed: it strips the types from frontend/src/components/galaxy.ts")
    strip = ("let s = ''; process.stdin.on('data', (d) => (s += d)).on('end', () => "
             "process.stdout.write(require('node:module').stripTypeScriptTypes(s)))")
    js = subprocess.run(
        [node, "--disable-warning=ExperimentalWarning", "-e", strip],
        input=(FRONTEND / "src/components/galaxy.ts").read_text(), capture_output=True, text=True, check=True,
    ).stdout
    js = re.sub(r"^export ", "", js, flags=re.M)
    return re.sub(r"\n\s*\n+", "\n", js)


# Phosphor's duotone ShieldCheck and Coins (MIT), the icons of Protect and Earn on the landing page.
ICONS = {
    "i-protect": [
        ("M216,56v56c0,96-88,120-88,120S40,208,40,112V56a8,8,0,0,1,8-8H208A8,8,0,0,1,216,56Z", 0.2),
        ("M208,40H48A16,16,0,0,0,32,56v56c0,52.72,25.52,84.67,46.93,102.19,23.06,18.86,46,25.26,47,25.53a8,8,0,0,0,4.2,0c1-.27,23.91-6.67,47-25.53C198.48,196.67,224,164.72,224,112V56A16,16,0,0,0,208,40Zm0,72c0,37.07-13.66,67.16-40.6,89.42A129.3,129.3,0,0,1,128,223.62a128.25,128.25,0,0,1-38.92-21.81C61.82,179.51,48,149.3,48,112l0-56,160,0ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z", 1),
    ],
    "i-earn": [
        ("M240,132c0,19.88-35.82,36-80,36-19.6,0-37.56-3.17-51.47-8.44h0C146.76,156.85,176,142,176,124V96.72h0C212.52,100.06,240,114.58,240,132ZM176,84c0-19.88-35.82-36-80-36S16,64.12,16,84s35.82,36,80,36S176,103.88,176,84Z", 0.2),
        ("M184,89.57V84c0-25.08-37.83-44-88-44S8,58.92,8,84v40c0,20.89,26.25,37.49,64,42.46V172c0,25.08,37.83,44,88,44s88-18.92,88-44V132C248,111.3,222.58,94.68,184,89.57ZM232,132c0,13.22-30.79,28-72,28-3.73,0-7.43-.13-11.08-.37C170.49,151.77,184,139,184,124V105.74C213.87,110.19,232,122.27,232,132ZM72,150.25V126.46A183.74,183.74,0,0,0,96,128a183.74,183.74,0,0,0,24-1.54v23.79A163,163,0,0,1,96,152,163,163,0,0,1,72,150.25Zm96-40.32V124c0,8.39-12.41,17.4-32,22.87V123.5C148.91,120.37,159.84,115.71,168,109.93ZM96,56c41.21,0,72,14.78,72,28s-30.79,28-72,28S24,97.22,24,84,54.79,56,96,56ZM24,124V109.93c8.16,5.78,19.09,10.44,32,13.57v23.37C36.41,141.4,24,132.39,24,124Zm64,48v-4.17c2.63.1,5.29.17,8,.17,3.88,0,7.67-.13,11.39-.35A121.92,121.92,0,0,0,120,171.41v23.46C100.41,189.4,88,180.39,88,172Zm48,26.25V174.4a179.48,179.48,0,0,0,24,1.6,183.74,183.74,0,0,0,24-1.54v23.79a165.45,165.45,0,0,1-48,0Zm64-3.38V171.5c12.91-3.13,23.84-7.79,32-13.57V172C232,180.39,219.59,189.4,200,194.87Z", 1),
    ],
}


def defs() -> str:
    """What the scenes share: the materials' gradients, the mark, the icons and the two images."""
    logo = (FRONTEND / "src/components/Logo.tsx").read_text()
    view = re.search(r'viewBox="([^"]+)"', logo).group(1)
    mark = "".join(
        f'<path fill="currentColor"{transform} d="{d}"/>'
        for transform, d in re.findall(r'<path fill="currentColor"((?: transform="[^"]*")?) d="([^"]+)"', logo)
    )
    icons = "\n".join(
        f'<symbol id="{name}" viewBox="0 0 256 256">'
        + "".join(f'<path fill="currentColor" opacity="{opacity}" d="{d}"/>' for d, opacity in paths)
        + "</symbol>"
        for name, paths in ICONS.items()
    )

    def gradient(name, top_color, bottom_color):
        return (f'<linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top_color}"/>'
                f'<stop offset="1" stop-color="{bottom_color}"/></linearGradient>')

    hero = FRONTEND / "src/assets/hero/ship-desktop-1440.avif"
    stylus = FRONTEND / "src/assets/stylus-logomark.svg"
    return f"""<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
{gradient("steel", C["panel-top"], C["panel"])}
<linearGradient id="steel-edge" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.22"/>
<stop offset="0.12" stop-color="{C["panel-edge"]}"/><stop offset="1" stop-color="{C["panel-edge"]}"/></linearGradient>
{gradient("plate", C["plate-top"], C["plate"])}
{gradient("plate-accent", C["plate-accent-top"], C["plate-accent"])}
<linearGradient id="sheen" x1="0" y1="0" x2="1" y2="0.4"><stop offset="0.28" stop-color="#fff" stop-opacity="0"/>
<stop offset="0.44" stop-color="#fff" stop-opacity="0.55"/><stop offset="0.6" stop-color="#fff" stop-opacity="0"/></linearGradient>
<filter id="lift" x="-10%" y="-10%" width="120%" height="135%">
<feDropShadow dx="0" dy="14" stdDeviation="14" flood-color="#03080a" flood-opacity="0.6"/></filter>
<symbol id="mark" viewBox="{view}">{mark}</symbol>
{icons}
<image id="ship" width="1440" height="617" href="{data_uri(hero, "image/avif")}"/>
<image id="stylus" width="1000" height="1000" href="{data_uri(stylus, "image/svg+xml")}"/>
</defs></svg>"""


def sky_colors() -> str:
    def rgb(name):
        return [int(C[name][i:i + 2], 16) for i in (1, 3, 5)]

    return json.dumps({
        "ground": rgb("surface"), "arm": rgb("nebula-arm"), "core": rgb("nebula-core"), "dust": rgb("nebula-dust"),
        "rose": rgb("nebula-rose"), "violet": rgb("nebula-violet"), "star": rgb("ink"), "starCool": rgb("info"),
    })


# ------------------------------------------------------------------------ html

CSS = """
@FONTS@
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: @BG@; color: @INK@; overflow: hidden;
    font-family: @SANS@; -webkit-font-smoothing: antialiased; }
  text { font-family: @SANS@; }
  text.d { font-family: "Syne", "Arial Black", sans-serif; font-weight: 800; letter-spacing: -0.015em; }
  text.wm { font-family: "Russo One", "Arial Black", sans-serif; }
  text.num { font-variant-numeric: tabular-nums; }
  text.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", "DejaVu Sans Mono", monospace; }
  /* One night sky behind every scene; the veil dims it under everything but the hero scenes. */
  #sky, #veil, #grain { position: fixed; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  #sky { object-fit: cover; }
  #veil { background: @VEIL@; opacity: 0; transition: opacity .5s ease; }
  body.veil #veil { opacity: 1; }
  #grain { opacity: .1; mix-blend-mode: overlay; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"); }
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
    background: @GLASS@; border: 1px solid @GLASS_EDGE@; border-radius: 14px;
    padding: 14px 20px; font-size: 22px; line-height: 1.4; color: @INK@; display: none; }
  body.script #cap { display: block; }
  #cap small { color: @ACCENT_TEXT@; letter-spacing: .08em; font-size: 13px; font-weight: 500; margin-right: 12px; }

  #help { position: fixed; right: 20px; top: 16px; background: @GLASS@;
    border: 1px solid @GLASS_EDGE@; border-radius: 14px; padding: 14px 18px; font-size: 15px;
    line-height: 1.7; color: @MUTED@; opacity: 0; transition: opacity .4s; pointer-events: none; }
  #help.on { opacity: 1; }
  #help b { color: @INK@; font-weight: 600; display: inline-block; min-width: 86px; }
  html.still #help, html.still #cap { display: none; }

  /* presenter window: index.html?notes */
  html.notes body { overflow: auto; }
  html.notes #sky, html.notes #veil, html.notes #grain, html.notes #stage, html.notes #cap, html.notes #help { display: none; }
  #notes { display: none; padding: 22px 26px 40px; max-width: 860px; margin: 0 auto; }
  html.notes #notes { display: block; }
  #notes .bar { display: flex; align-items: baseline; gap: 18px; position: sticky; top: 0;
    background: @BG@; padding: 6px 0 14px; border-bottom: 1px solid @LINE@; }
  #clock { font-size: 44px; font-weight: 600; font-variant-numeric: tabular-nums; }
  #target { color: @MUTED@; font-size: 18px; font-variant-numeric: tabular-nums; }
  #clock.late { color: @ABORT@; }
  #state { margin-left: auto; color: @ACCENT_TEXT@; font-size: 14px; font-weight: 500; letter-spacing: .08em; text-transform: uppercase; }
  #notes h2 { margin: 26px 0 8px; font-size: 14px; letter-spacing: .08em; text-transform: uppercase;
    color: @MUTED@; font-weight: 600; display: flex; justify-content: space-between; }
  #notes p { margin: 0; padding: 9px 12px; border-radius: 10px; font-size: 21px; line-height: 1.4;
    color: @MUTED@; border-left: 4px solid transparent; }
  #notes p.now { color: @INK@; background: @RAISED@; border-left-color: @ACCENT@; }
  #notes p.done { opacity: .45; }
  #notes .keys { margin-top: 30px; color: @MUTED@; font-size: 14px; line-height: 1.7; }
"""
for _k, _v in {
    "BG": BG, "INK": INK, "MUTED": MUTED, "LINE": LINE, "ACCENT_TEXT": C["accent-text"], "ACCENT": ACCENT,
    "RAISED": C["surface-raised"], "ABORT": C["abort"], "VEIL": C["sky-veil"], "GLASS": C["glass-fill-strong"],
    "GLASS_EDGE": C["glass-edge"], "SANS": '"Jost", "Futura", "Century Gothic", system-ui, sans-serif',
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

  // The landing hero's sky on the 16:9 stage: the Milky Way, its star dust and the resting stars
  // of Starfield.tsx. Painted once per window size; it covers the window like the stage fits it.
  const SKY = @SKY@;
  function paintSky() {
    const sky = document.getElementById("sky");
    const k = Math.max(innerWidth / @W@, innerHeight / @H@) * Math.min(devicePixelRatio || 1, 2);
    sky.width = Math.round(@W@ * k);
    sky.height = Math.round(@H@ * k);
    const ctx = sky.getContext("2d"), band = bandFor(@W@);
    const nebula = renderNebula(@W@, @H@, SKY, band, @H@);
    const small = document.createElement("canvas");
    small.width = nebula.width;
    small.height = nebula.height;
    small.getContext("2d").putImageData(new ImageData(nebula.data, nebula.width, nebula.height), 0, 0);
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(small, 0, 0, @W@, @H@);
    const rgb = (c) => "rgb(" + c.map(Math.round).join(" ") + ")";
    for (const s of dustStars(@W@, @H@, SKY, band, @H@)) {
      ctx.globalAlpha = s.alpha;
      ctx.fillStyle = rgb(s.color);
      ctx.fillRect(s.x, s.y, s.r, s.r);
    }
    const rand = mulberry32(1961);
    for (let n = 0; n < @W@ * @H@ / 1600; n++) {
      const size = rand(), x = rand() * @W@, y = rand() * @H@;
      const r = size > 0.985 ? 1.6 : size > 0.9 ? 1.1 : 0.6 + rand() * 0.3;
      const alpha = 0.35 + rand() * 0.65;
      ctx.globalAlpha = alpha;
      ctx.fillStyle = rgb(rand() < 0.12 ? SKY.starCool : SKY.star);
      if (r < 1) { ctx.fillRect(x, y, r, r); continue; }
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      if (r > 1.5) {
        // soft halo and a 4-point flare
        ctx.globalAlpha = alpha * 0.15;
        ctx.beginPath();
        ctx.arc(x, y, r * 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = alpha * 0.45;
        ctx.fillRect(x - r * 5, y - 0.25, r * 10, 0.5);
        ctx.fillRect(x - 0.25, y - r * 5, 0.5, r * 10);
      }
    }
    ctx.globalAlpha = 1;
  }

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
    document.body.classList.toggle("veil", !DECK[i].hero);
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
    paintSky();
    let resized;
    window.addEventListener("resize", () => { clearTimeout(resized); resized = setTimeout(paintSky, 150); });
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
            f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{esc(scene["name"])}">\n{inner}\n</svg>'
        )
    stage = "\n".join(svgs)
    total = sum(s["seconds"] for s in SCENES)
    js = JS.replace("@DECK@", json.dumps(deck)).replace("@SKY@", sky_colors()).replace("@W@", str(W)).replace("@H@", str(H))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crashline: the pitch, {total // 60}:{total % 60:02d}</title>
<style>{CSS.replace("@FONTS@", font_faces())}</style>
</head>
<body>
<canvas id="sky" aria-hidden="true"></canvas>
<div id="veil" aria-hidden="true"></div>
{defs()}
<div id="stage">
{stage}
</div>
<div id="grain" aria-hidden="true"></div>
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
<script>(() => {{
{galaxy_js()}
{js}}})();</script>
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
