#!/usr/bin/env python3
"""
Sunboard chassis v1 -> 1:1 cut templates (PDF) + cut list (CSV).

Source of truth: parts.json (the piece outlines used by the interactive 3D model).
The two template corrections from the assembly guide are applied here:
  1. FRONT ENDPLATE trimmed from 48 mm to 40 mm long (8 mm off the rear edge)
     so the tyres clear it at full steering lock.
  2. ELECTRONICS DECK screw holes moved onto the deck nut blocks:
     x 305/400 -> 320/385, right-side y -50 -> -40.
Ten small parts that were never in the 3D model are added as simple blanks
(DECK NUT BLOCK x8, ENCODER WASHER PLATE, CAMERA PLATE).

Outputs (in ../templates):
  sunboard_templates_PRINTSHOP_A1_A4.pdf   cover (A4) + 5 mm sheet (A1) + 3 mm sheet (A4)
  sunboard_templates_HOME_A4_tiled.pdf     cover + the A1 sheet tiled on A4 + 3 mm sheet
  cut_list.csv

Usage:  pip install reportlab shapely rectpack
        python3 make_templates.py
Coordinates: x rearward from the nose tip, y + = car LEFT, z up (mm).
"""
import csv
import json
import math
import os
from collections import Counter, defaultdict

from rectpack import newPacker, PackingMode, MaxRectsBssf, MaxRectsBl, SORT_AREA, SORT_DIFF
from reportlab.lib.colors import Color, black, white
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from shapely.geometry import Polygon, box
from shapely import affinity

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "templates"))
os.makedirs(OUT, exist_ok=True)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
for name, f in [("Cond", "DejaVuSansCondensed.ttf"), ("CondB", "DejaVuSansCondensed-Bold.ttf")]:
    try:
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, f)))
    except Exception:  # fall back to Helvetica on machines without DejaVu
        pass
F = "Cond" if "Cond" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
FB = "CondB" if "CondB" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"

RED = Color(0.78, 0.13, 0.10)
BLUE = Color(0.10, 0.33, 0.75)
TINT = Color(0.955, 0.955, 0.955)
GREY = Color(0.45, 0.45, 0.45)

A4 = (210.0, 297.0)
A1 = (594.0, 841.0)
GAP = 5.0          # mm between packed pieces
LABEL_H = 8.5      # mm label band under each piece
MARGIN = 12.0
MARGIN_A4 = 8.0


# ----------------------------------------------------------------------------- data
def circle(cx, cy, d, n=36):
    r = d / 2
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def to_uv(plane, d0, pts):
    """Project to the template plane so the PRINTED face is: top (x-y), outside (x-z), rear (y-z)."""
    if plane == "x-y":
        return [(x, -y) for x, y in pts], (-1, 0), "top"
    if plane == "x-z":
        if d0 >= 0:
            return [(x, z) for x, z in pts], (-1, 0), "outside (car left)"
        return [(-x, z) for x, z in pts], (1, 0), "outside (car right)"
    if plane == "y-z":
        return [(-y, z) for y, z in pts], None, "rear"
    raise ValueError(plane)


def area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def load_pieces():
    data = json.load(open(os.path.join(HERE, "parts.json")))
    pieces = []
    for p in data["parts"]:
        poly = p["polys"][0]
        outer = [tuple(v) for v in poly["outer"]]
        holes = [[tuple(v) for v in h] for h in poly["holes"] if area(h) > 0.5]  # drop zero-area artefacts
        note = ""
        dashed = []
        if p["name"] == "FRONT ENDPLATE":
            g = Polygon(outer, holes).intersection(box(-1e3, -1e3, 40.0, 1e3))
            outer = list(g.exterior.coords)[:-1]
            holes = [list(i.coords)[:-1] for i in g.interiors]
            note = "trimmed to 40 mm long (correction)"
        if p["name"] == "ELECTRONICS DECK":
            keep = []
            for h in holes:
                cx = sum(v[0] for v in h) / len(h)
                cy = sum(v[1] for v in h) / len(h)
                if area(h) < 12 and round(cx) in (305, 400):
                    continue  # old screw holes over the bulkhead centres
                keep.append(h)
            holes = keep + [circle(x, y, 3.2) for x, y in [(320, 50), (385, 50), (320, -40), (385, -40)]]
            note = "screw holes moved onto nut blocks (correction); left side = vent slots"
        if p["name"] == "SIDEPOD FRONT TOP" and max(v[1] for v in outer) <= 0:  # y < 0 = car right
            cx = sum(v[0] for v in outer) / len(outer)
            cy = sum(v[1] for v in outer) / len(outer)
            dashed.append(circle(cx, cy, 7.2))
            note = "RIGHT piece: KY-040 hole Ø7.2 on dashed circle (check fit)"
        uv_o, nose, face = to_uv(p["plane"], p["d0"], outer)
        uv_h = [to_uv(p["plane"], p["d0"], h)[0] for h in holes]
        uv_d = [to_uv(p["plane"], p["d0"], h)[0] for h in dashed]
        pieces.append(dict(name=p["name"], mat=p["mat"], t=p["t"], outer=uv_o, holes=uv_h, dashed=uv_d,
                           bends=[], nose=nose, face=face, note=note, redrawn=False))

    # ribbons (bent strips): develop the centre-line length flat; bend lines dashed
    for r in data["ribbons"]:
        line = r["line"]
        L = [0.0]
        for a, b in zip(line, line[1:]):
            L.append(L[-1] + math.dist(a, b))
        w0, w1 = r["w"]
        pieces.append(dict(name=r["name"], mat="SUN3", t=float(r["t"]), outer=rect(0, w0, L[-1], w1), holes=[],
                           dashed=[], bends=[((u, w0), (u, w1)) for u in L[1:-1]], nose=(-1, 0), face="outside",
                           note="score the inside face on the dashed bend lines", redrawn=False))

    # parts never in the 3D model: simple blanks, sizes chosen to fit (not from the original templates)
    for i in range(8):
        pieces.append(dict(name="DECK NUT BLOCK", mat="SUN5", t=5.0, outer=rect(0, 0, 20, 20),
                           holes=[circle(10, 10, 3.2)], dashed=[], bends=[], nose=None, face="any",
                           note="2 layers per block; hole 10 mm from the bulkhead face", redrawn=True))
    pieces.append(dict(name="ENCODER WASHER PLATE", mat="SUN5", t=5.0, outer=rect(0, 0, 30, 30),
                       holes=[circle(15, 15, 7.2)], dashed=[], bends=[], nose=None, face="any",
                       note="under the right SIDEPOD FRONT TOP", redrawn=True))
    pieces.append(dict(name="CAMERA PLATE", mat="SUN5", t=5.0, outer=rect(0, 0, 38, 44),
                       holes=[], dashed=[circle(19, 22, 9.0)], bends=[], nose=None, face="any",
                       note="lens hole Ø9: mark from your ESP32-CAM before cutting", redrawn=True))

    # number duplicates: "BH3 1/2"
    count = Counter(p["name"] for p in pieces)
    seen = defaultdict(int)
    for p in pieces:
        seen[p["name"]] += 1
        p["idx"], p["of"] = seen[p["name"]], count[p["name"]]
        xs = [v[0] for v in p["outer"]]
        ys = [v[1] for v in p["outer"]]
        p["bb"] = (min(xs), min(ys), max(xs), max(ys))
        p["w"], p["h"] = max(xs) - min(xs), max(ys) - min(ys)
    return pieces


# ----------------------------------------------------------------------------- packing
def label_lines(p):
    n = p["name"] + (f"  {p['idx']}/{p['of']}" if p["of"] > 1 else "")
    if p["redrawn"]:
        n += " *"
    return n, f"{p['t']:.0f} mm · printed face: {p['face']}"


def label_w(p):
    a, b = label_lines(p)
    return max(pdfmetrics.stringWidth(a, FB, 2.6), pdfmetrics.stringWidth(b, F, 2.0)) + 1


def slot(p, rot):
    if rot:
        return max(p["h"], label_w(p)) + GAP, p["w"] + LABEL_H + GAP
    return max(p["w"], label_w(p)) + GAP, p["h"] + LABEL_H + GAP


def pack(pieces, bins, turn=True, algo=None, sort=SORT_AREA):
    """Pack into bins [(W, H), ...] tried in order (big sheet first, overflow onto the next).
    Each piece is turned 90 degrees if that makes its slot (piece + label band) smaller.
    Returns [(bin_index, [(piece, x, y, w, h), ...]), ...]."""
    from rectpack import GuillotineBafSlas
    S = 10  # 0.1 mm grid
    packer = newPacker(mode=PackingMode.Offline, pack_algo=algo or GuillotineBafSlas, sort_algo=sort,
                       rotation=False)
    for i, p in enumerate(pieces):
        a0, a1 = slot(p, False), slot(p, True)
        p["rot"] = turn and a1[0] * a1[1] < a0[0] * a0[1] - 1
        if slot(p, p["rot"])[0] > bins[0][0]:
            p["rot"] = not p["rot"]
        w, h = slot(p, p["rot"])
        packer.add_rect(int(math.ceil(w * S)), int(math.ceil(h * S)), rid=i)
    for W, H in bins:
        packer.add_bin(int(W * S), int(H * S), count=1)
    packer.pack()
    pages = defaultdict(list)
    for b, x, y, w, h, rid in packer.rect_list():
        pages[b].append((pieces[rid], x / S, y / S, w / S, h / S))
    assert sum(len(v) for v in pages.values()) == len(pieces), "packing lost a piece"
    return [(k, pages[k]) for k in sorted(pages)]


# ----------------------------------------------------------------------------- drawing (canvas in mm)
def xform(p, pts, ox, oy):
    x0, y0, x1, y1 = p["bb"]
    if p.get("rot"):
        # rotate +90 deg: (u,v) -> (-v, u), then shift into the slot
        return [(ox + (y1 - v), oy + (u - x0)) for u, v in pts]
    return [(ox + (u - x0), oy + (v - y0)) for u, v in pts]


def draw_poly(c, pts, fill=False, dash=None, color=black, lw=0.2):
    path = c.beginPath()
    path.moveTo(*pts[0])
    for q in pts[1:]:
        path.lineTo(*q)
    path.close()
    c.setStrokeColor(color)
    c.setLineWidth(lw)
    c.setDash(*dash) if dash else c.setDash()
    c.drawPath(path, stroke=1, fill=1 if fill else 0)


def draw_piece(c, p, sx, sy):
    pw, ph = (p["h"], p["w"]) if p.get("rot") else (p["w"], p["h"])
    ox, oy = sx + GAP / 2, sy + GAP / 2 + LABEL_H
    # body: tinted, holes white
    path = c.beginPath()
    for ring in [p["outer"]] + p["holes"]:
        r = xform(p, ring, ox, oy)
        path.moveTo(*r[0])
        for q in r[1:]:
            path.lineTo(*q)
        path.close()
    c.setFillColor(TINT)
    c.setStrokeColor(black)
    c.setLineWidth(0.22)
    c.setDash()
    c.drawPath(path, stroke=1, fill=1, fillMode=0)  # even-odd: holes stay white
    for d in p["dashed"]:
        draw_poly(c, xform(p, d, ox, oy), dash=(1.2, 0.8), color=BLUE, lw=0.2)
    for a, b in p["bends"]:
        (x0, y0), (x1, y1) = xform(p, [a, b], ox, oy)
        c.setStrokeColor(BLUE)
        c.setDash(2, 1)
        c.setLineWidth(0.25)
        c.line(x0, y0, x1, y1)
    c.setDash()
    # nose arrow
    if p["nose"]:
        nx, ny = p["nose"]
        if p.get("rot"):
            nx, ny = -ny, nx
        arrow = {(-1, 0): "← NOSE", (1, 0): "NOSE →", (0, 1): "↑ NOSE", (0, -1): "↓ NOSE"}[(nx, ny)]
        c.setFillColor(RED)
        c.setFont(F, 2.2)
        cx, cy = ox + pw / 2, oy + ph / 2
        if pw > 22 and ph > 6:
            c.drawCentredString(cx, cy - 0.8, arrow)
    # labels
    a, b = label_lines(p)
    c.setFillColor(RED)
    c.setFont(FB, 2.6)
    c.drawString(ox, sy + GAP / 2 + 4.6, a)
    c.setFillColor(BLUE)
    c.setFont(F, 2.0)
    c.drawString(ox, sy + GAP / 2 + 1.6, b)


def scale_square(c, x, y, s):
    c.setStrokeColor(black)
    c.setLineWidth(0.3)
    c.setDash()
    c.rect(x, y, s, s, stroke=1, fill=0)
    c.setFont(F, 2.4)
    c.setFillColor(black)
    c.drawCentredString(x + s / 2, y + s / 2 - 1, f"{s:.0f} × {s:.0f} mm")
    c.drawCentredString(x + s / 2, y + s / 2 - 4.5, "measure before cutting")


def sheet(c, size, title, placed):
    W, H = size
    M = MARGIN if W > 300 else MARGIN_A4
    c.setPageSize((W * mm, H * mm))
    c.scale(mm, mm)
    c.setFillColor(black)
    c.setFont(FB, 7)
    c.drawString(M, H - M - 6, title)
    c.setFont(F, 3.2 if W > 300 else 2.5)
    c.setFillColor(GREY)
    c.drawString(M, H - M - 11,
                 "Print at 100 % / actual size. Black = cut. Blue dashed = mark or bend, not a cut. "
                 "* = simple blank, not from the original templates.")
    scale_square(c, W - M - 50, H - M - 52, 50) if W > 300 else scale_square(c, W - M - 30, H - M - 44, 30)
    for p, x, y, w, h in placed:
        draw_piece(c, p, M + x, M + y)
    c.showPage()


def cover(c, pieces, pages_desc, home):
    W, H = A4
    c.setPageSize((W * mm, H * mm))
    c.scale(mm, mm)
    y = H - 22
    c.setFont(FB, 9)
    c.setFillColor(black)
    c.drawString(15, y, "F1 VCU car — sunboard chassis v1")
    y -= 7
    c.setFont(F, 5)
    c.setFillColor(GREY)
    c.drawString(15, y, "1:1 hand-cut templates · " + ("home print, A4 tiles" if home else "print-shop set, A1 + A4"))
    y -= 12
    c.setFillColor(black)
    lines = [
        ("CondB", "Before you cut"),
        (F, "1. Print at 100 % (\"actual size\", no fit-to-page). Measure the scale square on every sheet."),
        (F, "2. Measure your sheet with calipers: the slots are drawn for 5.0 mm and 3.0 mm sunboard."),
        (F, "3. Spray-mount or glue-stick the paper on, cut through it with a fresh blade, steel rule, 3–4 light passes."),
        (F, "4. Test one slot on an offcut before cutting the rest."),
        (F, ""),
        ("CondB", "Reading the sheets"),
        (F, "Black outline = cut line. Blue dashed = mark, hole to drill to suit, or bend line (score, don't cut)."),
        (F, "Red = part name and copy number (BH3 1/2). '← NOSE' shows which end faces the front."),
        (F, "Printed face: horizontal parts = top, side parts = outside, cross parts = facing the rear."),
        (F, "Laminated parts (motor plates, pylons, BH3/BH4, crash plate, nut blocks: 2 layers; knuckles: 3)."),
        (F, ""),
        ("CondB", "Corrections already applied (vs the first print)"),
        (F, "• FRONT ENDPLATE is 40 mm long, not 48 (tyre clearance at full lock)."),
        (F, "• ELECTRONICS DECK screw holes moved to x 320 / 385, y +50 (left) and −40 (right), over the nut blocks."),
        (F, "• Parts marked * were not in the 3D model and are drawn as simple blanks:"),
        (F, "   DECK NUT BLOCK ×8 (20 × 20), ENCODER WASHER PLATE (30 × 30), CAMERA PLATE (38 × 44)."),
        (F, ""),
        ("CondB", "What's in this file"),
    ] + [(F, "• " + d) for d in pages_desc]
    for font, t in lines:
        c.setFont(font, 3.6 if font == F else 4.2)
        c.drawString(15, y, t)
        y -= 5.6 if t else 3
    # piece count table
    y -= 4
    c.setFont(FB, 4.2)
    c.drawString(15, y, "Piece count")
    y -= 6
    cnt = Counter((p["name"] + (" *" if p["redrawn"] else ""), p["mat"]) for p in pieces)
    rows = sorted(cnt.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    c.setFont(F, 2.9)
    col_h = math.ceil(len(rows) / 3)
    for i, ((n, m), k) in enumerate(rows):
        cx = 15 + (i // col_h) * 61
        cy = y - (i % col_h) * 4.0
        c.drawString(cx, cy, f"{k:>2} ×  {n}")
        c.drawRightString(cx + 57, cy, "5 mm" if m == "SUN5" else "3 mm")
    y -= col_h * 4.0 + 3
    n5 = sum(1 for p in pieces if p["mat"] == "SUN5")
    n3 = len(pieces) - n5
    c.setFont(FB, 3.4)
    c.drawString(15, y, f"Total {len(pieces)} pieces: {n5} in 5 mm sunboard, {n3} in 2–3 mm sunboard.")
    c.setFont(F, 3.0)
    c.setFillColor(GREY)
    c.drawString(15, 20, "Generated by MECHANICAL/chassis_sunboard_v1/source/make_templates.py from parts.json.")
    c.drawString(15, 16, "Coordinates: x rearward from the nose tip, y + = car left, z up (mm).")
    c.setFillColor(black)
    scale_square(c, W - 15 - 50, 15, 50)
    c.showPage()


def tiles(c, size, title, placed, tile=A4, overlap=10.0, m=8.0):
    """Tile a big sheet onto A4 pages with overlap, crosshairs every 50 mm and row/col ids."""
    W, H = size
    tw, th = tile[0] - 2 * m, tile[1] - 2 * m
    step_x, step_y = tw - overlap, th - overlap
    nx = math.ceil((W - overlap) / step_x)
    ny = math.ceil((H - overlap) / step_y)
    for j in range(ny):          # rows from the top
        for i in range(nx):
            x0 = i * step_x
            y0 = H - (j + 1) * step_y - overlap
            c.setPageSize((tile[0] * mm, tile[1] * mm))
            c.scale(mm, mm)
            c.saveState()
            p = c.beginPath()
            p.rect(m, m, tw, th)
            c.clipPath(p, stroke=0, fill=0)
            c.translate(m - x0, m - y0)
            # content
            c.setFillColor(black)
            c.setFont(FB, 7)
            c.drawString(MARGIN, H - MARGIN - 6, title)
            for pc, x, y, w, h in placed:
                draw_piece(c, pc, MARGIN + x, MARGIN + y)
            # crosshairs every 50 mm
            c.setStrokeColor(Color(0.6, 0.6, 0.6))
            c.setLineWidth(0.15)
            c.setDash()
            for gx in range(0, int(W) + 1, 50):
                for gy in range(0, int(H) + 1, 50):
                    c.line(gx - 2, gy, gx + 2, gy)
                    c.line(gx, gy - 2, gx, gy + 2)
            c.restoreState()
            # tile frame + id
            c.setStrokeColor(GREY)
            c.setLineWidth(0.2)
            c.setDash(1, 1)
            c.rect(m, m, tw, th)
            c.setDash()
            c.setFont(FB, 3.2)
            c.setFillColor(RED)
            rid = f"{chr(65 + j)}{i + 1}"
            c.drawString(m, tile[1] - m + 2.5,
                         f"Tile {rid}  (row {chr(65 + j)} of {chr(64 + ny)}, column {i + 1} of {nx})  — "
                         f"overlap {overlap:.0f} mm onto the next tile; align the + crosshairs")
            c.setFillColor(GREY)
            c.setFont(F, 2.6)
            c.drawString(m, m - 5, "Print at 100 %. Trim the right and bottom margins, overlap, tape.")
            # 30 mm check square on each tile (bottom right, in the margin band area)
            c.setStrokeColor(black)
            c.setLineWidth(0.25)
            c.rect(tile[0] - m - 30, 1.5, 30, 5)
            c.setFont(F, 2.4)
            c.setFillColor(black)
            c.drawCentredString(tile[0] - m - 15, 3.2, "this bar = 30 mm")
            c.showPage()
    return nx * ny


def main():
    pieces = load_pieces()
    p5 = [p for p in pieces if p["mat"] == "SUN5"]
    p3 = [p for p in pieces if p["mat"] == "SUN3"]
    A1_AREA = (A1[0] - 2 * MARGIN, A1[1] - 2 * MARGIN - 58)       # header band: title + 50 mm square
    A4_AREA = (A4[0] - 2 * MARGIN_A4, A4[1] - 2 * MARGIN_A4 - 46)  # header band: title + 30 mm square
    s5 = pack(p5, [A1_AREA, A4_AREA, A4_AREA], algo=MaxRectsBl, sort=SORT_DIFF)  # best of a search: 49 on A1
    big = [pg for b, pg in s5 if b == 0]
    small5 = [pg for b, pg in s5 if b > 0]
    s3 = pack(p3, [A4_AREA, A4_AREA], turn=False, algo=MaxRectsBssf)
    assert len(s3) == 1, "3 mm parts don't fit one A4"
    small3 = s3[0][1]

    def a4_pages(c):
        for i, pg in enumerate(small5):
            sheet(c, A4, f"5 mm SUNBOARD — small parts {i + 1}/{len(small5)} · {len(pg)} pieces", pg)
        sheet(c, A4, f"2–3 mm SUNBOARD · {len(small3)} pieces", small3)

    n_big = sum(len(pg) for pg in big)
    n_s5 = sum(len(pg) for pg in small5)

    # print shop: one A1 + A4 pages printed at home
    f1 = os.path.join(OUT, "sunboard_templates_PRINTSHOP_A1_A4.pdf")
    c = canvas.Canvas(f1)
    c.setTitle("F1 VCU sunboard chassis v1 — templates (print shop)")
    desc = [f"Page 2: A1 (594 × 841 mm), 5 mm sunboard, {n_big} pieces — print shop, 100 %",
            f"Page{'s' if len(small5) > 1 else ''} 3{'–' + str(2 + len(small5)) if len(small5) > 1 else ''}: "
            f"A4, 5 mm sunboard small parts, {n_s5} pieces — home printer, 100 %",
            f"Page {3 + len(small5)}: A4, 2–3 mm sunboard, {len(small3)} pieces — home printer, 100 %"]
    cover(c, pieces, desc, home=False)
    for pg in big:
        sheet(c, A1, f"5 mm SUNBOARD — main sheet · {len(pg)} pieces", pg)
    a4_pages(c)
    c.save()

    # home: the A1 sheet tiled on A4
    f2 = os.path.join(OUT, "sunboard_templates_HOME_A4_tiled.pdf")
    c = canvas.Canvas(f2)
    c.setTitle("F1 VCU sunboard chassis v1 — templates (home A4 tiles)")
    nx = math.ceil((A1[0] - 10) / (A4[0] - 26))
    ny = math.ceil((A1[1] - 10) / (A4[1] - 26))
    ntile = nx * ny
    desc = [f"Pages 2–{1 + ntile}: the A1 main sheet ({n_big} pieces, 5 mm) as {ntile} A4 tiles, "
            f"rows A–{chr(64 + ny)}, columns 1–{nx}",
            f"Then: A4 5 mm small parts ({n_s5} pieces) and the A4 2–3 mm sheet ({len(small3)} pieces), not tiled"]
    cover(c, pieces, desc, home=True)
    for pg in big:
        tiles(c, A1, "5 mm SUNBOARD — main sheet", pg)
    a4_pages(c)
    c.save()

    # cut list
    with open(os.path.join(OUT, "cut_list.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["part", "copy", "of", "sheet_mm", "width_mm", "height_mm", "printed_face", "notes"])
        for p in pieces:
            wr.writerow([p["name"], p["idx"], p["of"], f"{p['t']:.0f}", f"{p['w']:.1f}", f"{p['h']:.1f}",
                         p["face"], (p["note"] + ("; simple blank, not in 3D model" if p["redrawn"] else "")).strip("; ")])
    print(f"{len(pieces)} pieces: {n_big} on A1, {n_s5} on {len(small5)} A4 (5 mm), {len(small3)} on A4 (3 mm)")
    print(f1)
    print(f2)


if __name__ == "__main__":
    main()
