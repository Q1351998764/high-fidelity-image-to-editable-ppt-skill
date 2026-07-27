#!/usr/bin/env python3
"""Fit source-pixel samples to editable DrawingML cubic Bezier segments."""
import argparse
import json
import math
from pathlib import Path


def point_line_distance(point, start, end):
    x, y = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / math.hypot(dx, dy)


def rdp(points, tolerance):
    if len(points) <= 2:
        return points[:]
    distances = [point_line_distance(p, points[0], points[-1]) for p in points[1:-1]]
    if not distances or max(distances) <= tolerance:
        return [points[0], points[-1]]
    index = distances.index(max(distances)) + 1
    return rdp(points[: index + 1], tolerance)[:-1] + rdp(points[index:], tolerance)


def catmull_rom_to_bezier(points, tension=1.0):
    """Interpolating cubic segments. End tangents use one-sided neighbours."""
    if len(points) < 2:
        raise ValueError("curve fitting requires at least two points")
    segments = []
    factor = float(tension) / 6.0
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        c1 = [p1[0] + (p2[0] - p0[0]) * factor, p1[1] + (p2[1] - p0[1]) * factor]
        c2 = [p2[0] - (p3[0] - p1[0]) * factor, p2[1] - (p3[1] - p1[1]) * factor]
        segments.append({"c1": c1, "c2": c2, "end": list(p2)})
    return {"start": list(points[0]), "segments": segments, "closed": False}


def cubic_point(start, segment, t):
    p0, p1, p2, p3 = start, segment["c1"], segment["c2"], segment["end"]
    u = 1.0 - t
    return [
        u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
        u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
    ]


def sampled_curve(bezier, samples_per_segment=64):
    result = [bezier["start"]]
    start = bezier["start"]
    for segment in bezier["segments"]:
        result.extend(cubic_point(start, segment, n / samples_per_segment) for n in range(1, samples_per_segment + 1))
        start = segment["end"]
    return result


def maximum_nearest_error(points, bezier):
    samples = sampled_curve(bezier)
    return max(min(math.dist(point, sample) for sample in samples) for point in points)


def fit_points(points, tolerance=1.5, tension=1.0):
    original = [[float(x), float(y)] for x, y in points]
    target = max(0.05, float(tolerance))
    simplify_tolerance = target
    while True:
        simplified = rdp(original, simplify_tolerance) if simplify_tolerance > 0 else original
        bezier = catmull_rom_to_bezier(simplified, tension=tension)
        error = maximum_nearest_error(original, bezier)
        if error <= target or len(simplified) == len(original) or simplify_tolerance <= 0.01:
            return bezier, error, len(simplified)
        simplify_tolerance /= 2.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fit sampled points to cubic Bezier manifest geometry.")
    parser.add_argument("page_dir")
    parser.add_argument("--points-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--id", default="curve")
    parser.add_argument("--stroke", default="#000000")
    parser.add_argument("--stroke-width", type=float, default=1.0)
    parser.add_argument("--tolerance", type=float, default=1.5)
    parser.add_argument("--tension", type=float, default=1.0)
    parser.add_argument("--closed", action="store_true")
    args = parser.parse_args(argv)
    page_dir = Path(args.page_dir).resolve()
    points_path = Path(args.points_file)
    if not points_path.is_absolute():
        points_path = page_dir / points_path
    payload = json.loads(points_path.read_text(encoding="utf-8"))
    points = payload.get("points", payload) if isinstance(payload, dict) else payload
    bezier, error, retained = fit_points(points, args.tolerance, args.tension)
    bezier["closed"] = bool(args.closed)
    fragment = {
        "id": args.id,
        "type": "curve",
        "bezier_px": bezier,
        "fill": "none",
        "stroke": args.stroke,
        "stroke_width": args.stroke_width,
        "curve_fit": {"method": "rdp-catmull-rom-cubic", "tolerance_px": args.tolerance, "max_error_px": round(error, 4), "input_points": len(points), "retained_points": retained},
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = page_dir / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), **fragment["curve_fit"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
