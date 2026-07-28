#!/usr/bin/env python3
"""Trace a plotted stroke from source pixels, then fit editable cubic Beziers."""
import argparse
import json
import math
from pathlib import Path

from bezier_curve import fit_points, sampled_curve


def parse_box(value):
    values = [int(round(float(part))) for part in str(value).split(",")]
    if len(values) != 4:
        raise ValueError("ROI must be X,Y,W,H")
    return values


def rgb(value):
    value = str(value).strip().lstrip("#")
    if len(value) != 6:
        raise ValueError("target color must be #RRGGBB")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def consecutive_clusters(values):
    clusters = []
    for value in values:
        if not clusters or value > clusters[-1][-1] + 1:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def trace_curve(source_path, roi, target_color="#0B3B99", color_distance=48.0, max_gap=18, max_step_y=8.0, bottom_clearance=3, left_clearance=2, right_clearance=2, top_clearance=1, line_style="auto", dash_max_component_width=32):
    import cv2
    import numpy as np

    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read source image: {source_path}")
    x0, y0, width, height = roi
    crop = image[y0:y0 + height, x0:x0 + width]
    if crop.size == 0:
        raise ValueError("curve ROI is outside the source image")
    target_bgr = np.uint8([[list(reversed(rgb(target_color)))]] )
    target_lab = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)[0, 0]
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    distance = np.linalg.norm(lab - target_lab, axis=2)
    mask = distance <= float(color_distance)
    mask[:max(0, top_clearance), :] = False
    mask[:, :max(0, left_clearance)] = False
    if right_clearance: mask[:, max(0, width - right_clearance):] = False
    if bottom_clearance: mask[max(0, height - bottom_clearance):, :] = False
    if line_style == "dashed":
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        keep = np.zeros_like(mask)
        for label in range(1, count):
            component_width = int(stats[label, cv2.CC_STAT_WIDTH])
            component_area = int(stats[label, cv2.CC_STAT_AREA])
            if component_area >= 1 and component_width <= int(dash_max_component_width):
                keep |= labels == label
        mask = keep

    observations = []
    previous = None
    previous_previous = None
    last_x = None
    for x in range(width):
        ys = np.flatnonzero(mask[:, x]).tolist()
        if not ys:
            continue
        centers = [sum(cluster) / len(cluster) for cluster in consecutive_clusters(ys)]
        if previous is None or (last_x is not None and x - last_x > max_gap):
            chosen = max(centers)  # plotted curves normally enter from the lower-left
        else:
            slope = 0.0 if previous_previous is None else previous - previous_previous
            predicted = previous + slope * max(1, x - last_x)
            chosen = min(centers, key=lambda center: abs(center - predicted))
        observations.append([x, chosen])
        previous_previous, previous = previous, chosen
        last_x = x

    if len(observations) < 4:
        raise ValueError("too few target-color pixels were traced; tighten ROI or adjust target/tolerance")

    # Keep the longest run whose missing-column gaps can be safely interpolated.
    runs = [[]]
    for point in observations:
        dx = point[0] - runs[-1][-1][0] if runs[-1] else 1
        slope_jump = abs(point[1] - runs[-1][-1][1]) / max(1, dx) if runs[-1] else 0
        if runs[-1] and (dx > max_gap or slope_jump > max_step_y):
            runs.append([])
        runs[-1].append(point)
    traced = max(runs, key=len)
    dense = []
    for index, point in enumerate(traced):
        if index:
            prior = traced[index - 1]
            gap = int(point[0] - prior[0])
            for step in range(1, gap):
                ratio = step / gap
                dense.append([prior[0] + step, prior[1] * (1 - ratio) + point[1] * ratio])
        dense.append(point)

    # Suppress one-pixel antialiasing jitter without flattening real peaks.
    smoothed = []
    for index, point in enumerate(dense):
        nearby = [dense[pos][1] for pos in range(max(0, index - 2), min(len(dense), index + 3))]
        smoothed.append([point[0], float(np.median(nearby))])
    absolute = [[x0 + x, y0 + min(y, height - bottom_clearance - 0.5)] for x, y in smoothed]
    return absolute, mask, crop


def symmetric_chamfer(points_a, points_b):
    def one_way(source, target):
        return sum(min(math.dist(point, other) for other in target) for point in source) / max(1, len(source))
    return (one_way(points_a, points_b) + one_way(points_b, points_a)) / 2.0


def trace_fragment(source_path, roi, curve_id, target_color, color_distance, max_gap, axis_clearance, fit_tolerance, stroke, stroke_width, dash=None, max_step_y=8.0, line_style="auto", dash_max_component_width=32):
    points, mask, crop = trace_curve(source_path, roi, target_color, color_distance, max_gap, max_step_y, axis_clearance[3], axis_clearance[0], axis_clearance[2], axis_clearance[1], line_style, dash_max_component_width)
    bezier, fit_error, retained = fit_points(points, fit_tolerance)
    left = roi[0] + axis_clearance[0]; top = roi[1] + axis_clearance[1]
    right = roi[0] + roi[2] - axis_clearance[2]; bottom = roi[1] + roi[3] - axis_clearance[3]
    def clamp(point):
        point[0] = min(max(float(point[0]), left), right); point[1] = min(max(float(point[1]), top), bottom)
    clamp(bezier["start"])
    for segment in bezier["segments"]:
        for key in ("c1", "c2", "end"): clamp(segment[key])
    rendered = sampled_curve(bezier, 64)
    chamfer = symmetric_chamfer(points[::max(1, len(points) // 160)], rendered[::max(1, len(rendered) // 320)])
    shape = {
        "id": curve_id,
        "type": "curve",
        "curve_role": "data-stroke",
        "bezier_px": bezier,
        "fill": "none",
        "stroke": stroke,
        "stroke_width": stroke_width,
        "plot_area_px": roi,
        "axis_clearance_px": {"left": axis_clearance[0], "top": axis_clearance[1], "right": axis_clearance[2], "bottom": axis_clearance[3]},
        "curve_trace": {
            "method": "lab-mask-continuity-trace-adaptive-cubic",
            "source": str(source_path),
            "target_color": target_color,
            "color_distance": color_distance,
            "max_step_y": max_step_y,
            "line_style": line_style,
            "source_roi_px": [round(float(value), 2) for value in roi],
            "validation_policy": "adaptive-source-span-and-stroke-evidence",
            "trace_points": len(points),
            "retained_points": retained,
            "max_fit_error_px": round(fit_error, 4),
            "symmetric_chamfer_px": round(chamfer, 4),
            "max_chamfer_px": 1.5,
            "trace_points_px": [[round(x, 2), round(y, 2)] for x, y in points[::max(1, len(points) // 160)]],
        },
    }
    if dash: shape["dash"] = dash
    return shape, mask, crop


def main(argv=None):
    parser = argparse.ArgumentParser(description="Trace a source plot stroke and fit editable cubic Bezier geometry.")
    parser.add_argument("page_dir"); parser.add_argument("--source", default="source.png"); parser.add_argument("--roi", required=True); parser.add_argument("--out", required=True); parser.add_argument("--preview"); parser.add_argument("--id", required=True)
    parser.add_argument("--target-color", default="#0B3B99"); parser.add_argument("--color-distance", type=float, default=48.0); parser.add_argument("--max-gap", type=int, default=18); parser.add_argument("--max-step-y", type=float, default=8.0, help="Split unrelated same-color objects when adjacent traced columns jump farther than this."); parser.add_argument("--line-style", choices=["auto", "solid", "dashed"], default="auto"); parser.add_argument("--dash-max-component-width", type=int, default=32); parser.add_argument("--axis-clearance", default="2,1,2,3", help="left,top,right,bottom pixels"); parser.add_argument("--fit-tolerance", type=float, default=0.8); parser.add_argument("--stroke", default="#0B3B99"); parser.add_argument("--stroke-width", type=float, default=1.2); parser.add_argument("--dash")
    args = parser.parse_args(argv); page = Path(args.page_dir).resolve(); source = Path(args.source); source = source if source.is_absolute() else page / source; roi = parse_box(args.roi); clearance = parse_box(args.axis_clearance)
    shape, mask, crop = trace_fragment(source, roi, args.id, args.target_color, args.color_distance, args.max_gap, clearance, args.fit_tolerance, args.stroke, args.stroke_width, args.dash, args.max_step_y, args.line_style, args.dash_max_component_width)
    out = Path(args.out); out = out if out.is_absolute() else page / out; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(shape, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.preview:
        import cv2
        preview = crop.copy(); local = [[int(round(x - roi[0])), int(round(y - roi[1]))] for x, y in shape["curve_trace"]["trace_points_px"]];
        for first, second in zip(local, local[1:]): cv2.line(preview, tuple(first), tuple(second), (0, 0, 255), 1, cv2.LINE_AA)
        preview_path = Path(args.preview); preview_path = preview_path if preview_path.is_absolute() else page / preview_path; preview_path.parent.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(preview_path), preview)
    print(json.dumps({"out": str(out), "id": args.id, **shape["curve_trace"]}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
