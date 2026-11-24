#!/usr/bin/env python3
"""Render a still of the sunboard chassis from parts.json (for the README).

Usage: python3 render_chassis.py [out.png]
Needs matplotlib. Coordinates in parts.json: x rearward, y + = left, z up (mm).
"""
import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "chassis_render.png")
data = json.load(open(os.path.join(HERE, "parts.json")))
M = data["meta"]

LIGHT = np.array([-0.35, 0.45, 0.82])
LIGHT /= np.linalg.norm(LIGHT)


def W(x, y, z):
    """parts.json frame -> right-handed plotting frame (forward, left, up)."""
    return (-x, y, z)


def shade(base, normal):
    n = np.array(normal, float)
    n /= (np.linalg.norm(n) or 1)
    k = 0.55 + 0.45 * abs(float(n @ LIGHT))
    return tuple(min(1, c * k) for c in base)


faces, colors = [], []


def add(poly, base, normal, edge=True):
    faces.append(poly)
    colors.append(shade(base, normal))


def prism(pts2d, plane, d0, t, base):
    """Extrude a 2-D outline by t from d0 along the plane normal."""
    def lift(u, v, w):
        if plane == "x-y":
            return W(u, v, w)
        if plane == "x-z":
            return W(u, w, v)
        return W(w, u, v)  # y-z
    nrm = {"x-y": (0, 0, 1), "x-z": (0, 1, 0), "y-z": (-1, 0, 0)}[plane]
    a = [lift(u, v, d0) for u, v in pts2d]
    b = [lift(u, v, d0 + t) for u, v in pts2d]
    add(a, base, nrm)
    add(b, base, nrm)
    for i in range(len(pts2d)):
        j = (i + 1) % len(pts2d)
        p0, p1 = np.array(a[i]), np.array(a[j])
        side = np.cross(p1 - p0, np.array(b[i]) - p0)
        add([a[i], a[j], b[j], b[i]], base, side)


SUN = (0.93, 0.93, 0.92)
FLAP = (0.84, 0.20, 0.15)
for p in data["parts"]:
    base = FLAP if "FLAP" in p["name"] else SUN
    for poly in p["polys"]:
        prism(poly["outer"], p["plane"], p["d0"], p["t"], base)

for r in data["ribbons"]:
    w0, w1 = r["w"]
    line = r["line"]
    for (x0, z0), (x1, z1) in zip(line, line[1:]):
        quad = [W(x0, w0, z0), W(x1, w0, z1), W(x1, w1, z1), W(x0, w1, z0)]
        add(quad, SUN, np.cross(np.array(quad[1]) - quad[0], np.array(quad[3]) - quad[0]))

# wheels (cylinders), axle height = R above ground
R, Wd = M["R"], M["W"]
for x, yc in [(M["FA"], M["YWF"]), (M["FA"], -M["YWF"]), (M["RA"], M["YWR"]), (M["RA"], -M["YWR"])]:
    n = 40
    ring = [(x + R * math.cos(2 * math.pi * i / n), R + R * math.sin(2 * math.pi * i / n)) for i in range(n)]
    ya, yb = yc - Wd / 2, yc + Wd / 2
    outer_face = yb if yc > 0 else ya
    for i in range(n):
        j = (i + 1) % n
        (xa, za), (xb, zb) = ring[i], ring[j]
        add([W(xa, ya, za), W(xb, ya, zb), W(xb, yb, zb), W(xa, yb, za)], (0.16, 0.16, 0.17),
            (-(xa + xb) / 2 + x, 0, (za + zb) / 2 - R))
    add([W(u, outer_face, v) for u, v in ring], (0.22, 0.22, 0.23), (0, 1, 0))
    hub = [(x + 0.55 * R * math.cos(2 * math.pi * i / 24), R + 0.55 * R * math.sin(2 * math.pi * i / 24)) for i in range(24)]
    off = 0.3 if yc > 0 else -0.3
    add([W(u, outer_face + off, v) for u, v in hub], (0.62, 0.64, 0.67), (0, 1, 0))

# VCU board on the electronics deck (100 x 80, centred at x = 340)
bx0, bx1, by = 290, 390, 40
prism([(bx0, -by), (bx1, -by), (bx1, by), (bx0, by)], "x-y", 70, 1.6, (0.10, 0.42, 0.26))

fig = plt.figure(figsize=(14, 14), dpi=200)
ax = fig.add_subplot(111, projection="3d")
pc = Poly3DCollection(faces, facecolors=colors, edgecolors=(0.25, 0.25, 0.25, 0.35), linewidths=0.25)
pc.set_zsort("average")
ax.add_collection3d(pc)
ax.set_xlim(-580, 20)
ax.set_ylim(-300, 300)
ax.set_zlim(-120, 240)
ax.set_box_aspect((600, 600, 360))
ax.view_init(elev=20, azim=38)
ax.set_axis_off()
fig.subplots_adjust(0, 0, 1, 1)
plt.savefig(OUT, dpi=200, facecolor="white")
# crop the empty space matplotlib leaves around 3-D axes
from PIL import Image, ImageChops
im = Image.open(OUT).convert("RGB")
bb = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
pad = 40
im.crop((bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad)).save(OUT)
print(OUT, im.crop(bb).size)
