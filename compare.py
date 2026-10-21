#!/usr/bin/env python3
"""Compare Valhalla route responses in old_density/ and new_density/ and write README.md.

Routes are ranked by shape difference first (share of route length that is not
covered by the other route), then by relative change in duration, then by
relative change in cost.
"""
import json
import math
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).parent
OLD, NEW = "old_density", "new_density"
REPO = "chrstnbwnkl/density_routes"
BRANCH = "main"
TOOL = "https://chrstnbwnkl.github.io/valhalla-browser-tools/route-comparison"
TOP_N = 100
STEP_M = 20  # densification step along the shape
TOL_M = 30  # max distance for a point to count as shared with the other route
R = 6371008.8


def decode(shape, precision=6):
    coords, idx, lat, lon = [], 0, 0, 0
    factor = 10**precision
    while idx < len(shape):
        for i in range(2):
            result, shift = 0, 0
            while True:
                b = ord(shape[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if i == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def project(coords, lat0):
    k = math.cos(math.radians(lat0))
    return [(math.radians(lon) * R * k, math.radians(lat) * R) for lat, lon in coords]


def densify(pts):
    """Return (points, weights) sampled every STEP_M, weight = represented length."""
    out, weights = [], []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, math.ceil(d / STEP_M))
        for i in range(n):
            t = (i + 0.5) / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
            weights.append(d / n)
    return out, weights


def grid(points):
    g = {}
    for x, y in points:
        g.setdefault((int(x // TOL_M), int(y // TOL_M)), []).append((x, y))
    return g


def unshared_fraction(points, weights, other_grid):
    total = sum(weights)
    if total == 0:
        return 0.0
    miss = 0.0
    for (x, y), w in zip(points, weights):
        cx, cy = int(x // TOL_M), int(y // TOL_M)
        hit = any(
            (x - ox) ** 2 + (y - oy) ** 2 <= TOL_M**2
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for ox, oy in other_grid.get((cx + dx, cy + dy), ())
        )
        if not hit:
            miss += w
    return miss / total


def shape_of(trip):
    coords = []
    for leg in trip["legs"]:
        coords.extend(decode(leg["shape"]))
    return coords


def shape_diff(a, b):
    lat0 = (a[0][0] + b[0][0]) / 2
    pa, wa = densify(project(a, lat0))
    pb, wb = densify(project(b, lat0))
    # include the last vertex so very short routes still have samples
    pa.append(project(a[-1:], lat0)[0])
    wa.append(0)
    pb.append(project(b[-1:], lat0)[0])
    wb.append(0)
    return (unshared_fraction(pa, wa, grid(pb)) + unshared_fraction(pb, wb, grid(pa))) / 2


def rel(old, new):
    return (new - old) / old if old else 0.0


def raw_url(path):
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}"


def blob_url(path):
    return f"https://github.com/{REPO}/blob/{BRANCH}/{path}"


def tool_url(pairs):
    params = {}
    n = 1
    for rid in pairs:
        for d in (OLD, NEW):
            params[f"res_{n}"] = raw_url(f"{d}/{rid}.json")
            params[f"label_{n}"] = f"{d} #{rid}"
            n += 1
    return f"{TOOL}?{urlencode(params)}"


def main():
    rows = []
    for f in sorted((ROOT / OLD).glob("*.json"), key=lambda p: int(p.stem)):
        rid = int(f.stem)
        old = json.loads(f.read_text())["trip"]
        new = json.loads((ROOT / NEW / f.name).read_text())["trip"]
        so, sn = old["summary"], new["summary"]
        rows.append(
            {
                "id": rid,
                "shape": shape_diff(shape_of(old), shape_of(new)),
                "time_old": so["time"],
                "time_new": sn["time"],
                "dtime": rel(so["time"], sn["time"]),
                "cost_old": so["cost"],
                "cost_new": sn["cost"],
                "dcost": rel(so["cost"], sn["cost"]),
                "len_old": so["length"],
                "len_new": sn["length"],
            }
        )

    # shape is bucketed to whole percent so duration/cost break ties among similar shape changes
    rows.sort(key=lambda r: (round(r["shape"] * 100), abs(r["dtime"]), abs(r["dcost"])), reverse=True)
    top = rows[:TOP_N]

    n_shape = sum(r["shape"] >= 0.01 for r in rows)
    n_time = sum(abs(r["dtime"]) >= 0.001 for r in rows)
    n_cost = sum(abs(r["dcost"]) >= 0.001 for r in rows)

    lines = [
        "# Density routes: old vs. new",
        "",
        f"Comparison of {len(rows)} Valhalla route responses in [`{OLD}/`]({OLD}) and [`{NEW}/`]({NEW}).",
        "",
        f"- Routes whose shape differs by ≥ 1 %: **{n_shape}**",
        f"- Routes whose duration changed by ≥ 0.1 %: **{n_time}**",
        f"- Routes whose cost changed by ≥ 0.1 %: **{n_cost}**",
        "",
        f"**[Open the top 10 in the route comparison tool]({tool_url([r['id'] for r in top[:10]])})**",
        "",
        "## Method",
        "",
        f"- **Shape Δ**: share of route length (averaged over both routes) that lies more than {TOL_M} m "
        "from the other route's shape. 0 % = identical, 100 % = completely disjoint.",
        "- **Duration Δ / Cost Δ**: relative change of `trip.summary.time` / `trip.summary.cost` from old to new.",
        "- Ranking: shape Δ (rounded to whole percent) first, then |duration Δ|, then |cost Δ|.",
        "",
        "Regenerate with `python3 compare.py`.",
        "",
        f"## Top {TOP_N} changed routes",
        "",
        "| # | Route | Shape Δ | Duration old → new | Duration Δ | Cost old → new | Cost Δ | Length old → new (km) | Compare |",
        "|--:|------:|--------:|-------------------:|-----------:|---------------:|-------:|----------------------:|:-------:|",
    ]

    def fmt_t(s):
        return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"

    for i, r in enumerate(top, 1):
        rid = r["id"]
        lines.append(
            f"| {i} | {rid} ([old]({blob_url(f'{OLD}/{rid}.json')}), [new]({blob_url(f'{NEW}/{rid}.json')})) "
            f"| {r['shape'] * 100:.1f} % "
            f"| {fmt_t(r['time_old'])} → {fmt_t(r['time_new'])} | {r['dtime'] * 100:+.2f} % "
            f"| {r['cost_old']:.0f} → {r['cost_new']:.0f} | {r['dcost'] * 100:+.2f} % "
            f"| {r['len_old']:.1f} → {r['len_new']:.1f} "
            f"| [view]({tool_url([rid])}) |"
        )

    (ROOT / "README.md").write_text("\n".join(lines) + "\n")
    print(f"wrote README.md ({n_shape} shape changes, {n_time} duration changes, {n_cost} cost changes)")


if __name__ == "__main__":
    main()
