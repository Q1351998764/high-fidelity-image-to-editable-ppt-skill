"""Deterministic object-level geometry checks against source and rendered pixels."""

from pathlib import Path
import math

import cv2
import numpy as np

from bezier_curve import sampled_curve
from trace_plot_curve import symmetric_chamfer


ALLOWED_KINDS = {"axis", "bracket", "legend-symbol", "structural-curve"}
ALLOWED_MARKERS = {"none", "triangle", "stealth", "diamond", "oval", "arrow"}
ALLOWED_BRACKETS = {"square", "round", "curly", "measurement"}
ALLOWED_ORIENTATIONS = {"horizontal", "vertical"}
BRACKET_PRESETS = {
    "square": {"leftBracket", "rightBracket", "bracketPair"},
    "round": {"leftParen", "rightParen"},
    "curly": {"leftBrace", "rightBrace", "bracePair"},
}


def _shape_map(manifest):
    return {
        str(item["id"]): item
        for section in ("shapes", "images")
        for item in manifest.get(section, [])
        if isinstance(item, dict) and item.get("id")
    }


def _source_to_preview_box(manifest, source_box, preview_width, preview_height):
    slide = manifest.get("slide", {})
    source = manifest.get("source", {})
    content = manifest.get("content_box") or {
        "left": 0,
        "top": 0,
        "width": float(slide.get("width", 13.333)),
        "height": float(slide.get("height", 7.5)),
    }
    slide_width = max(float(slide.get("width", 13.333)), 0.001)
    slide_height = max(float(slide.get("height", 7.5)), 0.001)
    source_width = max(float(source.get("width_px", 1)), 1.0)
    source_height = max(float(source.get("height_px", 1)), 1.0)
    x, y, width, height = [float(value) for value in source_box]
    left = (float(content.get("left", 0)) + x / source_width * float(content.get("width", slide_width))) / slide_width * preview_width
    top = (float(content.get("top", 0)) + y / source_height * float(content.get("height", slide_height))) / slide_height * preview_height
    right = (float(content.get("left", 0)) + (x + width) / source_width * float(content.get("width", slide_width))) / slide_width * preview_width
    bottom = (float(content.get("top", 0)) + (y + height) / source_height * float(content.get("height", slide_height))) / slide_height * preview_height
    return [left, top, right - left, bottom - top]


def _crop(image, box):
    x, y, width, height = [float(value) for value in box]
    left = max(0, int(math.floor(x)))
    top = max(0, int(math.floor(y)))
    right = min(image.shape[1], int(math.ceil(x + width)))
    bottom = min(image.shape[0], int(math.ceil(y + height)))
    if right <= left or bottom <= top:
        return None
    return image[top:bottom, left:right]


def _edges(image):
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, 50, 150)


def _directed_distances(reference_edges, candidate_edges):
    points = reference_edges > 0
    if not np.any(points):
        return np.array([], dtype=np.float32)
    distance = cv2.distanceTransform((candidate_edges == 0).astype(np.uint8), cv2.DIST_L2, 3)
    return distance[points]


def edge_fidelity_metrics(source_crop, preview_crop, tolerance_px=2.5):
    height, width = source_crop.shape[:2]
    preview_crop = cv2.resize(preview_crop, (width, height), interpolation=cv2.INTER_AREA)
    source_edges = _edges(source_crop)
    preview_edges = _edges(preview_crop)
    source_to_preview = _directed_distances(source_edges, preview_edges)
    preview_to_source = _directed_distances(preview_edges, source_edges)
    metrics = {
        "source_edge_pixels": int(np.count_nonzero(source_edges)),
        "preview_edge_pixels": int(np.count_nonzero(preview_edges)),
        "source_coverage": 0.0,
        "preview_precision": 0.0,
        "p95_edge_distance_px": None,
        "mean_edge_distance_px": None,
    }
    if source_to_preview.size:
        metrics["source_coverage"] = float(np.mean(source_to_preview <= tolerance_px))
    if preview_to_source.size:
        metrics["preview_precision"] = float(np.mean(preview_to_source <= tolerance_px))
    if source_to_preview.size and preview_to_source.size:
        combined = np.concatenate([source_to_preview, preview_to_source])
        metrics["p95_edge_distance_px"] = float(np.percentile(combined, 95))
        metrics["mean_edge_distance_px"] = float(np.mean(combined))
    return metrics


def _point_segment_distance(point, start, end):
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denominator))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _minimum_curve_line_distance(curve_shape, line_shape):
    points = sampled_curve(curve_shape["bezier_px"], 64)
    x1, y1, x2, y2 = [float(value) for value in line_shape["points_px"]]
    return min(_point_segment_distance(point, (x1, y1), (x2, y2)) for point in points)


def geometry_inventory_violations(manifest, source_path=None, preview_path=None):
    violations = []
    results = []
    inventory = manifest.get("geometry_inventory")
    if not isinstance(inventory, list):
        return ([{"field": "geometry_inventory", "reason": "geometry_inventory must list every axis, bracket, legend symbol, and structural curve"}], results)

    visible_geometry = [
        shape for shape in manifest.get("shapes", [])
        if isinstance(shape, dict) and (shape.get("type") in {"line", "curve"} or shape.get("bezier_px"))
    ]
    if visible_geometry and not inventory:
        violations.append({
            "field": "geometry_inventory",
            "reason": "geometry_inventory cannot be empty when the page contains visible lines or curves",
        })

    objects = _shape_map(manifest)
    source_image = cv2.imread(str(source_path), cv2.IMREAD_COLOR) if source_path and Path(source_path).exists() else None
    preview_image = cv2.imread(str(preview_path), cv2.IMREAD_COLOR) if preview_path and Path(preview_path).exists() else None
    covered = set()

    for index, entry in enumerate(inventory):
        field = f"geometry_inventory[{index}]"
        if not isinstance(entry, dict):
            violations.append({"field": field, "reason": "geometry inventory entries must be objects"})
            continue
        kind = entry.get("kind")
        if kind not in ALLOWED_KINDS:
            violations.append({"field": f"{field}.kind", "reason": f"kind must be one of {sorted(ALLOWED_KINDS)}"})
            continue
        object_ids = entry.get("object_ids")
        if not isinstance(object_ids, list) or not object_ids:
            violations.append({"field": f"{field}.object_ids", "reason": "geometry checks require one or more referenced object ids"})
            continue
        missing = [object_id for object_id in object_ids if str(object_id) not in objects]
        if missing:
            violations.append({"field": f"{field}.object_ids", "reason": f"unknown object ids: {missing}"})
            continue
        covered.update(str(object_id) for object_id in object_ids)
        shapes = [objects[str(object_id)] for object_id in object_ids]

        source_box = entry.get("source_box_px")
        if not isinstance(source_box, list) or len(source_box) != 4 or float(source_box[2]) <= 0 or float(source_box[3]) <= 0:
            violations.append({"field": f"{field}.source_box_px", "reason": "source_box_px must be a positive source-pixel ROI"})
            continue

        if kind == "axis":
            expected_start = entry.get("expected_start_marker")
            expected_end = entry.get("expected_end_marker")
            if expected_start not in ALLOWED_MARKERS or expected_end not in ALLOWED_MARKERS:
                violations.append({"field": field, "reason": "axis checks require explicit expected_start_marker and expected_end_marker"})
            elif len(shapes) != 1 or shapes[0].get("type") != "line":
                violations.append({"field": f"{field}.object_ids", "reason": "an axis marker check must reference exactly one line shape"})
            else:
                actual_start = shapes[0].get("start_arrow", "none") or "none"
                actual_end = shapes[0].get("end_arrow", "none") or "none"
                if actual_start != expected_start:
                    violations.append({"field": f"{field}.expected_start_marker", "reason": f"source expects {expected_start}, manifest line uses {actual_start}"})
                if actual_end != expected_end:
                    violations.append({"field": f"{field}.expected_end_marker", "reason": f"source expects {expected_end}, manifest line uses {actual_end}"})

        elif kind == "bracket":
            style = entry.get("bracket_style")
            orientation = entry.get("orientation")
            if style not in ALLOWED_BRACKETS or orientation not in ALLOWED_ORIENTATIONS:
                violations.append({"field": field, "reason": "bracket checks require bracket_style and orientation"})
            elif style == "measurement":
                if not any(shape.get("type") == "line" and (shape.get("start_arrow") or shape.get("end_arrow")) for shape in shapes):
                    violations.append({"field": f"{field}.object_ids", "reason": "measurement brackets require a line with source-matched line-end markers"})
            else:
                presets = BRACKET_PRESETS[style]
                source_matched = any(shape.get("preset") in presets for shape in shapes)
                source_traced = any(shape.get("bezier_px") and shape.get("curve_trace", {}).get("trace_points_px") for shape in shapes)
                if not source_matched and not source_traced:
                    violations.append({"field": f"{field}.object_ids", "reason": f"{style} bracket must use its matching PowerPoint preset or a source-traced editable curve"})

        elif kind == "legend-symbol":
            if len(shapes) != 1 or shapes[0].get("curve_role") != "legend-symbol":
                violations.append({"field": f"{field}.object_ids", "reason": "legend-symbol checks must reference exactly one legend-symbol curve"})
            else:
                shape = shapes[0]
                trace = shape.get("curve_trace")
                if not isinstance(trace, dict) or not trace.get("trace_points_px"):
                    violations.append({"field": f"{field}.object_ids", "reason": "legend curves must be traced from source pixels"})
                else:
                    chamfer = symmetric_chamfer(trace["trace_points_px"], sampled_curve(shape["bezier_px"], 64))
                    limit = float(trace.get("max_chamfer_px", 1.5))
                    if chamfer > limit:
                        violations.append({"field": f"{field}.object_ids", "reason": f"legend source-to-Bezier Chamfer error {chamfer:.3f}px exceeds {limit:.3f}px"})
                relation = entry.get("baseline_relation", "none")
                baseline_ids = entry.get("baseline_ids", [])
                if relation not in {"none", "separated", "touching"}:
                    violations.append({"field": f"{field}.baseline_relation", "reason": "baseline_relation must be none, separated, or touching"})
                if relation != "none":
                    if not baseline_ids:
                        violations.append({"field": f"{field}.baseline_ids", "reason": "a legend baseline relation requires baseline line ids"})
                    for baseline_id in baseline_ids:
                        baseline = objects.get(str(baseline_id))
                        if not baseline or baseline.get("type") != "line" or not baseline.get("points_px"):
                            violations.append({"field": f"{field}.baseline_ids", "reason": f"baseline {baseline_id} must be a line with points_px"})
                            continue
                        distance = _minimum_curve_line_distance(shape, baseline)
                        if relation == "separated" and distance < float(entry.get("min_clearance_px", 2.0)):
                            violations.append({"field": f"{field}.baseline_relation", "reason": f"legend curve is only {distance:.3f}px from its baseline"})

        if source_image is None or preview_image is None:
            violations.append({"field": field, "reason": "source.png and preview.png are required for object-level visual fidelity checks"})
            continue
        source_crop = _crop(source_image, source_box)
        preview_box = _source_to_preview_box(manifest, source_box, preview_image.shape[1], preview_image.shape[0])
        preview_crop = _crop(preview_image, preview_box)
        if source_crop is None or preview_crop is None:
            violations.append({"field": f"{field}.source_box_px", "reason": "geometry comparison ROI falls outside the source or preview"})
            continue
        tolerance = float(entry.get("edge_tolerance_px", 2.5))
        metrics = edge_fidelity_metrics(source_crop, preview_crop, tolerance)
        result = {"id": entry.get("id", f"geometry_{index}"), "kind": kind, **metrics}
        results.append(result)
        if metrics["source_edge_pixels"] < int(entry.get("min_edge_pixels", 6)):
            violations.append({"field": field, "reason": "source geometry ROI contains too few detectable edge pixels; tighten or correct the ROI"})
            continue
        if metrics["preview_edge_pixels"] < int(entry.get("min_edge_pixels", 6)):
            violations.append({"field": field, "reason": "rendered geometry ROI contains too few detectable edge pixels"})
            continue
        if metrics["source_coverage"] < float(entry.get("min_source_coverage", 0.88)):
            violations.append({"field": field, "reason": f"render covers only {metrics['source_coverage']:.3f} of source geometry edges"})
        if metrics["preview_precision"] < float(entry.get("min_preview_precision", 0.84)):
            violations.append({"field": field, "reason": f"only {metrics['preview_precision']:.3f} of rendered geometry edges match the source"})
        max_p95 = float(entry.get("max_p95_edge_distance_px", 3.5))
        if metrics["p95_edge_distance_px"] is None or metrics["p95_edge_distance_px"] > max_p95:
            violations.append({"field": field, "reason": f"object-level edge distance exceeds {max_p95:.3f}px"})

    required_roles = {
        str(shape.get("id"))
        for shape in manifest.get("shapes", [])
        if shape.get("id") and (
            shape.get("semantic_role") in {"axis", "bracket"}
            or shape.get("curve_role") in {"legend-symbol", "native-structural"}
        )
    }
    missing_coverage = sorted(required_roles - covered)
    if missing_coverage:
        violations.append({"field": "geometry_inventory", "reason": f"geometry inventory does not cover required structural objects: {missing_coverage}"})
    return violations, results
