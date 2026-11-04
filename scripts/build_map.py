"""Build tracker/static/us-states.json from us-atlas's pre-projected states TopoJSON.

    curl -sSo states-albers-10m.json https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-albers-10m.json
    python3 scripts/build_map.py states-albers-10m.json

Output: {"viewBox": [w, h], "states": {"TX": {"d": "M...Z", "x": .., "y": ..}, ...}} in the
975x610 Albers USA frame us-atlas uses (Alaska and Hawaii inset). us-atlas is ISC-licensed and
derived from the US Census Bureau's cartographic boundary files (public domain).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

# Label nudges (in map units) where the largest polygon's centroid reads badly.
NUDGE = {"FL": (12, 6), "LA": (-10, -2), "MI": (10, 22), "KY": (6, 2), "VA": (8, 2), "ID": (0, 18),
         "CA": (-8, 0), "NY": (6, 4), "HI": (8, 6), "AK": (0, 0), "TX": (6, 0), "MN": (-6, 6),
         "WV": (-2, 4), "NC": (8, 0), "OK": (10, -2), "MD": (-4, -2)}


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def ring_points(ring: list[int], arcs) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in ring:
        seg = arcs[i] if i >= 0 else arcs[~i][::-1]
        pts.extend(seg if not pts else seg[1:])
    return pts


def area_centroid(pts):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    if not a:
        return 0.0, (pts[0] if pts else (0, 0))
    return abs(a) / 2, (cx / (3 * a), cy / (3 * a))


def main(src: str, dst: str) -> None:
    topo = json.loads(Path(src).read_text())
    arcs = decode_arcs(topo)
    states = {}
    for g in topo["objects"]["states"]["geometries"]:
        abbr = ABBR[g["properties"]["name"]]
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        d, best = [], (0.0, (0, 0))
        for poly in polys:
            for k, ring in enumerate(poly):
                pts = ring_points(ring, arcs)
                d.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
                if k == 0:
                    best = max(best, area_centroid(pts), key=lambda t: t[0])
        nx, ny = NUDGE.get(abbr, (0, 0))
        states[abbr] = {"d": "".join(d), "x": round(best[1][0] + nx, 1), "y": round(best[1][1] + ny, 1)}
    Path(dst).write_text(json.dumps({"viewBox": [975, 610], "states": states}, separators=(",", ":")))
    print(f"wrote {dst}: {len(states)} states, {Path(dst).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "tracker/static/us-states.json")
