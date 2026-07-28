"""General source-to-reconstruction fidelity gates.

These checks deliberately compare the source with the rendered preview.  They
close the manifest's open-world loophole: an author cannot make an omitted
object disappear merely by leaving it out of every inventory.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


MICRO_KINDS = {"axis-label", "time-label", "percent-label", "axis-arrow", "tick-label", "micro-symbol"}


def _crop(image, box, inset=0.0):
    x, y, width, height = [float(value) for value in box]
    x += width * inset
    y += height * inset
    width *= 1.0 - 2.0 * inset
    height *= 1.0 - 2.0 * inset
    left = max(0, int(math.floor(x)))
    top = max(0, int(math.floor(y)))
    right = min(image.shape[1], int(math.ceil(x + width)))
    bottom = min(image.shape[0], int(math.ceil(y + height)))
    if right <= left or bottom <= top:
        return None
    return image[top:bottom, left:right]


def _source_to_preview_box(manifest, source_box, preview_width, preview_height):
    slide = manifest.get("slide", {})
    source = manifest.get("source", {})
    slide_width = max(float(slide.get("width", 13.333)), 0.001)
    slide_height = max(float(slide.get("height", 7.5)), 0.001)
    content = manifest.get("content_box") or {"left": 0, "top": 0, "width": slide_width, "height": slide_height}
    source_width = max(float(source.get("width_px", 1)), 1.0)
    source_height = max(float(source.get("height_px", 1)), 1.0)
    x, y, width, height = [float(value) for value in source_box]
    left = (float(content.get("left", 0)) + x / source_width * float(content.get("width", slide_width))) / slide_width * preview_width
    top = (float(content.get("top", 0)) + y / source_height * float(content.get("height", slide_height))) / slide_height * preview_height
    right = (float(content.get("left", 0)) + (x + width) / source_width * float(content.get("width", slide_width))) / slide_width * preview_width
    bottom = (float(content.get("top", 0)) + (y + height) / source_height * float(content.get("height", slide_height))) / slide_height * preview_height
    return [left, top, right - left, bottom - top]


def _preview_aligned_to_source(manifest, preview, source_width, source_height):
    full_source_box = [0, 0, source_width, source_height]
    content_box = _source_to_preview_box(manifest, full_source_box, preview.shape[1], preview.shape[0])
    content = _crop(preview, content_box)
    if content is None:
        return None
    return cv2.resize(content, (source_width, source_height), interpolation=cv2.INTER_AREA)


def _edges(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, 45, 135)


def _valid_box(value):
    return isinstance(value, list) and len(value) == 4 and float(value[2]) > 0 and float(value[3]) > 0


def _paint_box(mask, box, value=0, padding=0):
    if not _valid_box(box):
        return
    x, y, width, height = [float(item) for item in box]
    left = max(0, int(math.floor(x - padding)))
    top = max(0, int(math.floor(y - padding)))
    right = min(mask.shape[1], int(math.ceil(x + width + padding)))
    bottom = min(mask.shape[0], int(math.ceil(y + height + padding)))
    if right > left and bottom > top:
        mask[top:bottom, left:right] = value


def _hotspots(binary, min_edge_pixels=10, max_results=40):
    original = (binary > 0).astype(np.uint8)
    joined = cv2.dilate(original, np.ones((3, 3), np.uint8), iterations=1)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(joined, 8)
    results = []
    for label in range(1, count):
        x, y, width, height, _area = [int(value) for value in stats[label]]
        edge_pixels = int(np.count_nonzero(original[labels == label]))
        if edge_pixels < min_edge_pixels:
            continue
        results.append({"box_px": [x, y, width, height], "edge_pixels": edge_pixels})
    results.sort(key=lambda item: item["edge_pixels"], reverse=True)
    return results[:max_results]


def reverse_source_coverage_violations(manifest, source_image, preview_image):
    violations = []
    if source_image is None or preview_image is None:
        return ([{"field": "source_coverage", "reason": "source.png and preview.png are required for reverse source coverage"}], {})
    aligned = _preview_aligned_to_source(manifest, preview_image, source_image.shape[1], source_image.shape[0])
    if aligned is None:
        return ([{"field": "source_coverage", "reason": "preview content_box cannot be aligned to source pixels"}], {})

    source_edges = _edges(source_image)
    preview_edges = _edges(aligned)
    comparison_mask = np.full(source_edges.shape, 255, dtype=np.uint8)
    for text_box in manifest.get("text_boxes", []):
        _paint_box(comparison_mask, text_box.get("box_px"), 0, padding=2)

    policy = manifest.get("source_coverage_policy", {})
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        violations.append({"field": "source_coverage_policy", "reason": "source_coverage_policy must be an object"})
        policy = {}
    ignored_area = 0.0
    page_area = float(source_image.shape[0] * source_image.shape[1])
    for index, region in enumerate(policy.get("ignore_regions", [])):
        if not isinstance(region, dict) or not _valid_box(region.get("box_px")) or not str(region.get("reason") or "").strip():
            violations.append({"field": f"source_coverage_policy.ignore_regions[{index}]", "reason": "ignored source regions require box_px and a non-empty reason"})
            continue
        region_area = float(region["box_px"][2]) * float(region["box_px"][3])
        if region_area > page_area * 0.05:
            violations.append({"field": f"source_coverage_policy.ignore_regions[{index}]", "reason": "one ignored region cannot exceed 5% of the source page"})
            continue
        ignored_area += region_area
        _paint_box(comparison_mask, region["box_px"], 0)
    if ignored_area > page_area * 0.12:
        violations.append({"field": "source_coverage_policy.ignore_regions", "reason": "ignored regions cannot cover more than 12% of the source page"})

    edge_tolerance = min(float(policy.get("edge_tolerance_px", 3.0)), 5.0)
    distance = cv2.distanceTransform((preview_edges == 0).astype(np.uint8), cv2.DIST_L2, 3)
    source_points = (source_edges > 0) & (comparison_mask > 0)
    unmatched = source_points & (distance > edge_tolerance)
    source_count = int(np.count_nonzero(source_points))
    unmatched_count = int(np.count_nonzero(unmatched))
    coverage = 1.0 if source_count == 0 else 1.0 - unmatched_count / source_count
    hotspots = _hotspots(unmatched.astype(np.uint8) * 255, min(int(policy.get("min_hotspot_edge_pixels", 12)), 30))
    micro_hotspots = [
        item for item in _hotspots(unmatched.astype(np.uint8) * 255, 3, max_results=200)
        if item["box_px"][2] <= 45 and item["box_px"][3] <= 30 and item["edge_pixels"] <= 110
    ]
    metrics = {
        "source_edge_pixels": source_count,
        "unmatched_source_edge_pixels": unmatched_count,
        "source_edge_coverage": float(coverage),
        "unexplained_hotspots": hotspots,
        "unexplained_micro_hotspots": micro_hotspots,
    }
    minimum_coverage = max(float(policy.get("min_source_edge_coverage", 0.82)), 0.75)
    maximum_hotspots = min(int(policy.get("max_unexplained_hotspots", 8)), 20)
    if coverage < minimum_coverage:
        violations.append({"field": "source_coverage", "reason": f"only {coverage:.3f} of non-text source edges are explained by the preview; minimum is {minimum_coverage:.3f}"})
    if len(hotspots) > maximum_hotspots:
        violations.append({"field": "source_coverage", "reason": f"{len(hotspots)} unexplained source-edge hotspots exceed the allowed {maximum_hotspots}", "hotspots": hotspots[:12]})
    return violations, metrics


def _box_center_inside(box, region):
    x, y, width, height = [float(value) for value in box]
    rx, ry, rw, rh = [float(value) for value in region]
    center_x, center_y = x + width / 2.0, y + height / 2.0
    return rx <= center_x <= rx + rw and ry <= center_y <= ry + rh


def micro_annotation_violations(manifest, source_image, preview_image, coverage_metrics=None):
    violations = []
    results = []
    plot_regions = []
    for shape in manifest.get("shapes", []):
        box = shape.get("plot_area_px")
        if _valid_box(box):
            x, y, width, height = [float(value) for value in box]
            plot_regions.append([max(0, x - 30), max(0, y - 30), width + 60, height + 60])
    if not plot_regions:
        return violations, results

    inventory = manifest.get("micro_annotation_inventory")
    if not isinstance(inventory, list):
        violations.append({"field": "micro_annotation_inventory", "reason": "pages with plots require an inventory of time labels, percent labels, tick labels, and small axis arrows"})
        inventory = []
    if not inventory:
        audit = manifest.get("micro_annotation_audit", {})
        if not isinstance(audit, dict) or audit.get("none_found") is not True or not str(audit.get("reason") or "").strip():
            violations.append({"field": "micro_annotation_inventory", "reason": "an empty plot micro-annotation inventory requires micro_annotation_audit.none_found=true and a concrete reason"})
    objects = {
        str(item.get("id")): item
        for section in ("text_boxes", "shapes", "images")
        for item in manifest.get(section, [])
        if isinstance(item, dict) and item.get("id")
    }
    inventoried_boxes = []
    for index, entry in enumerate(inventory):
        field = f"micro_annotation_inventory[{index}]"
        if not isinstance(entry, dict):
            violations.append({"field": field, "reason": "micro annotation entries must be objects"})
            continue
        if entry.get("kind") not in MICRO_KINDS:
            violations.append({"field": f"{field}.kind", "reason": f"kind must be one of {sorted(MICRO_KINDS)}"})
        box = entry.get("source_box_px")
        if not _valid_box(box):
            violations.append({"field": f"{field}.source_box_px", "reason": "micro annotations require a tight positive source_box_px"})
            continue
        inventoried_boxes.append(box)
        object_ids = entry.get("object_ids")
        if not isinstance(object_ids, list) or not object_ids:
            violations.append({"field": f"{field}.object_ids", "reason": "micro annotations require one or more positioned object ids"})
            continue
        missing = [value for value in object_ids if str(value) not in objects]
        if missing:
            violations.append({"field": f"{field}.object_ids", "reason": f"unknown object ids: {missing}"})
        if source_image is not None and preview_image is not None:
            source_crop = _crop(source_image, box)
            preview_box = _source_to_preview_box(manifest, box, preview_image.shape[1], preview_image.shape[0])
            preview_crop = _crop(preview_image, preview_box)
            if source_crop is not None and preview_crop is not None:
                from visual_fidelity import edge_fidelity_metrics
                metrics = edge_fidelity_metrics(source_crop, preview_crop, float(entry.get("edge_tolerance_px", 2.5)))
                results.append({"id": entry.get("id", f"micro_{index}"), "kind": entry.get("kind"), **metrics})
                if metrics["source_coverage"] < max(float(entry.get("min_source_coverage", 0.72)), 0.60):
                    violations.append({"field": field, "reason": f"micro annotation render covers only {metrics['source_coverage']:.3f} of source edges"})

    hotspots = (coverage_metrics or {}).get("unexplained_micro_hotspots", [])
    missing_micro = []
    for hotspot in hotspots:
        box = hotspot["box_px"]
        _x, _y, width, height = box
        if width > 45 or height > 30 or hotspot["edge_pixels"] > 110:
            continue
        if not any(_box_center_inside(box, region) for region in plot_regions):
            continue
        if any(_box_center_inside(box, inventory_box) for inventory_box in inventoried_boxes):
            continue
        missing_micro.append(hotspot)
    if missing_micro:
        violations.append({"field": "micro_annotation_inventory", "reason": f"{len(missing_micro)} small source annotations near plots are absent or unexplained", "hotspots": missing_micro[:20]})
    return violations, results


def _hex_to_bgr(value):
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None
    return np.array([blue, green, red], dtype=np.uint8)


def _lab_color(bgr):
    pixel = np.asarray(bgr, dtype=np.uint8).reshape(1, 1, 3)
    lab = cv2.cvtColor(pixel, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    return np.array([lab[0] * 100.0 / 255.0, lab[1] - 128.0, lab[2] - 128.0], dtype=np.float32)


def _median_lab(crop):
    pixels = crop.reshape(-1, 3)
    median = np.median(pixels, axis=0).astype(np.uint8)
    return _lab_color(median)


def _chroma_stats(crop):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    foreground = (value < 245) | (saturation > 25)
    foreground_count = int(np.count_nonzero(foreground))
    chromatic = foreground & (saturation >= 50) & (value >= 35)
    chroma_count = int(np.count_nonzero(chromatic))
    ratio = 0.0 if foreground_count == 0 else chroma_count / foreground_count
    diversity = 0
    if chroma_count:
        histogram, _ = np.histogram(hsv[:, :, 0][chromatic], bins=8, range=(0, 180))
        histogram = histogram / max(1, histogram.sum())
        diversity = int(np.count_nonzero(histogram >= 0.07))
    return {"foreground_pixels": foreground_count, "chromatic_pixels": chroma_count, "chroma_ratio": float(ratio), "hue_diversity": diversity}


def _source_curve_mask(source_image, roi, target_color, color_distance):
    crop = _crop(source_image, roi)
    if crop is None:
        return None, None
    target = _hex_to_bgr(target_color)
    if target is None:
        return crop, None
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    target_lab = cv2.cvtColor(target.reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    distance = np.linalg.norm(lab - target_lab, axis=2)
    mask = (distance <= min(float(color_distance), 55.0)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return crop, mask


def curve_source_coverage_violations(manifest, source_image):
    violations = []
    results = []
    if source_image is None:
        return ([{"field": "curve_source_coverage", "reason": "source.png is required for trace-to-source validation"}], results)
    for index, shape in enumerate(manifest.get("shapes", [])):
        if not (shape.get("type") == "curve" or shape.get("bezier_px")) or shape.get("curve_role") == "area-fill":
            continue
        field = f"shapes[{index}].curve_trace"
        trace = shape.get("curve_trace", {})
        points = trace.get("trace_points_px")
        roi = trace.get("source_roi_px") or shape.get("plot_area_px")
        if not isinstance(points, list) or len(points) < 2 or not _valid_box(roi):
            violations.append({"field": field, "reason": "trace-to-source validation requires trace_points_px and a tight source_roi_px/plot_area_px"})
            continue
        point_array = np.asarray(points, dtype=np.float32)
        x, y, width, height = [float(value) for value in roi]
        primary_span = (float(np.ptp(point_array[:, 0])) / width) if width >= height else (float(np.ptp(point_array[:, 1])) / height)
        minimum_span = float(trace.get("min_primary_span_ratio", 0.55))
        if minimum_span < 0.35:
            minimum_span = 0.35
        _crop_image, source_mask = _source_curve_mask(source_image, roi, trace.get("target_color") or shape.get("stroke"), trace.get("color_distance", 48.0))
        source_support = None
        source_stroke_coverage = None
        if source_mask is not None and np.count_nonzero(source_mask):
            local_points = np.rint(point_array - np.array([[x, y]], dtype=np.float32)).astype(np.int32)
            local_points[:, 0] = np.clip(local_points[:, 0], 0, source_mask.shape[1] - 1)
            local_points[:, 1] = np.clip(local_points[:, 1], 0, source_mask.shape[0] - 1)
            distance_to_source = cv2.distanceTransform((source_mask == 0).astype(np.uint8), cv2.DIST_L2, 3)
            source_support = float(np.mean(distance_to_source[local_points[:, 1], local_points[:, 0]] <= 2.5))
            trace_mask = np.zeros(source_mask.shape, dtype=np.uint8)
            cv2.polylines(trace_mask, [local_points.reshape(-1, 1, 2)], False, 255, 5, cv2.LINE_AA)
            source_stroke_coverage = float(np.count_nonzero((source_mask > 0) & (trace_mask > 0)) / max(1, np.count_nonzero(source_mask)))
        result = {
            "id": shape.get("id", f"curve_{index}"),
            "primary_span_ratio": primary_span,
            "source_point_support": source_support,
            "source_stroke_coverage": source_stroke_coverage,
        }
        results.append(result)
        if primary_span < minimum_span:
            violations.append({"field": field, "reason": f"trace spans only {primary_span:.3f} of its primary source ROI; minimum is {minimum_span:.3f}"})
        minimum_support = max(float(trace.get("min_source_point_support", 0.82)), 0.65)
        minimum_stroke_coverage = max(float(trace.get("min_source_stroke_coverage", 0.22)), 0.12)
        if source_support is None or source_support < minimum_support:
            violations.append({"field": field, "reason": f"trace points are insufficiently supported by source-color pixels ({source_support})"})
        if source_stroke_coverage is None or source_stroke_coverage < minimum_stroke_coverage:
            violations.append({"field": field, "reason": f"trace explains too little of the source-colored stroke ({source_stroke_coverage})"})
    return violations, results


def color_semantic_violations(manifest, source_image, preview_image):
    violations = []
    results = []
    if source_image is None or preview_image is None:
        return ([{"field": "color_fidelity", "reason": "source.png and preview.png are required for color semantic validation"}], results)

    for index, image in enumerate(manifest.get("images", [])):
        box = image.get("box_px")
        if not _valid_box(box):
            continue
        source_crop = _crop(source_image, box)
        preview_crop = _crop(preview_image, _source_to_preview_box(manifest, box, preview_image.shape[1], preview_image.shape[0]))
        if source_crop is None or preview_crop is None:
            continue
        preview_crop = cv2.resize(preview_crop, (source_crop.shape[1], source_crop.shape[0]), interpolation=cv2.INTER_AREA)
        source_stats = _chroma_stats(source_crop)
        preview_stats = _chroma_stats(preview_crop)
        result = {"id": image.get("id", f"image_{index}"), "kind": "image-palette", "source": source_stats, "preview": preview_stats}
        results.append(result)
        if source_stats["chromatic_pixels"] >= 20 and source_stats["chroma_ratio"] >= 0.10:
            retained = preview_stats["chroma_ratio"] / max(source_stats["chroma_ratio"], 1e-6)
            if retained < 0.42:
                violations.append({"field": f"images[{index}]", "reason": f"source chroma collapsed to {retained:.3f} of its original proportion; mild color shifts are allowed but color-to-grayscale drift is not"})
            if source_stats["hue_diversity"] >= 3 and preview_stats["hue_diversity"] <= source_stats["hue_diversity"] - 2:
                violations.append({"field": f"images[{index}]", "reason": f"source palette diversity collapsed from {source_stats['hue_diversity']} hue groups to {preview_stats['hue_diversity']}"})

    for index, shape in enumerate(manifest.get("shapes", [])):
        if shape.get("type") not in {"rect", "roundRect"} or not _valid_box(shape.get("box_px")):
            continue
        width, height = float(shape["box_px"][2]), float(shape["box_px"][3])
        role_text = f"{shape.get('id', '')} {shape.get('semantic_role', '')}".lower()
        state_like = width <= 42 and height <= 42 or any(term in role_text for term in ("risk", "state", "status", "cell", "slot", "bar"))
        if not state_like:
            continue
        expected_bgr = _hex_to_bgr(shape.get("fill"))
        source_crop = _crop(source_image, shape["box_px"], inset=0.22)
        if expected_bgr is None or source_crop is None or source_crop.size < 9:
            continue
        source_lab = _median_lab(source_crop)
        fill_lab = _lab_color(expected_bgr)
        delta_e = float(np.linalg.norm(source_lab - fill_lab))
        result = {"id": shape.get("id", f"shape_{index}"), "kind": "state-fill", "delta_e": delta_e}
        results.append(result)
        tolerance = min(float(shape.get("source_color_tolerance_delta_e", 34.0)), 45.0)
        if delta_e > tolerance:
            violations.append({"field": f"shapes[{index}].fill", "reason": f"small state-cell fill differs from its source by DeltaE {delta_e:.1f}; tolerance is {tolerance:.1f}"})
    return violations, results


def universal_fidelity_violations(manifest, source_path, preview_path):
    source_image = cv2.imread(str(source_path), cv2.IMREAD_COLOR) if source_path and Path(source_path).exists() else None
    preview_image = cv2.imread(str(preview_path), cv2.IMREAD_COLOR) if preview_path and Path(preview_path).exists() else None
    coverage_violations, coverage = reverse_source_coverage_violations(manifest, source_image, preview_image)
    micro_violations, micro = micro_annotation_violations(manifest, source_image, preview_image, coverage)
    curve_violations, curves = curve_source_coverage_violations(manifest, source_image)
    color_violations, colors = color_semantic_violations(manifest, source_image, preview_image)
    return coverage_violations + micro_violations + curve_violations + color_violations, {
        "source_coverage": coverage,
        "micro_annotations": micro,
        "curve_source_coverage": curves,
        "color_fidelity": colors,
    }
