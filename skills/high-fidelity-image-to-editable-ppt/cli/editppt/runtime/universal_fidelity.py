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


def _scale_profile(image):
    height, width = image.shape[:2]
    short_side = float(min(width, height))
    area_scale = math.sqrt((width * height) / (1600.0 * 900.0))
    return {
        "width": width,
        "height": height,
        "short_side": short_side,
        "area_scale": max(0.25, area_scale),
        "distance_px": max(1.25, short_side * 0.0032),
        "micro_width_px": max(8.0, width * 0.030),
        "micro_height_px": max(6.0, height * 0.035),
        "plot_padding_px": max(4.0, short_side * 0.020),
    }


def _edges(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    non_extreme = gray[(gray > 5) & (gray < 250)]
    median = float(np.median(non_extreme)) if non_extreme.size else float(np.median(gray))
    spread = float(np.percentile(non_extreme, 75) - np.percentile(non_extreme, 25)) if non_extreme.size else 32.0
    lower = int(np.clip(median - max(18.0, 0.9 * spread), 20, 110))
    upper = int(np.clip(median + max(32.0, 1.6 * spread), lower + 25, 230))
    return cv2.Canny(gray, lower, upper)


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


def _hotspots(binary, min_edge_pixels=10, max_results=40, join_radius=1):
    original = (binary > 0).astype(np.uint8)
    kernel_size = max(1, int(join_radius) * 2 + 1)
    joined = cv2.dilate(original, np.ones((kernel_size, kernel_size), np.uint8), iterations=1)
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

    profile = _scale_profile(source_image)
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

    edge_tolerance = profile["distance_px"]
    if policy.get("edge_tolerance_px") is not None:
        edge_tolerance = min(edge_tolerance, max(0.75, float(policy["edge_tolerance_px"])))
    distance = cv2.distanceTransform((preview_edges == 0).astype(np.uint8), cv2.DIST_L2, 3)
    source_points = (source_edges > 0) & (comparison_mask > 0)
    unmatched_full = source_points & (distance > edge_tolerance)

    # Require the omission to survive a second scale.  This suppresses
    # antialiasing/font-rasterization noise while retaining genuinely missing
    # axes, labels, icons, and blocks.
    half_width = max(1, source_image.shape[1] // 2)
    half_height = max(1, source_image.shape[0] // 2)
    source_half = cv2.resize(source_image, (half_width, half_height), interpolation=cv2.INTER_AREA)
    preview_half = cv2.resize(aligned, (half_width, half_height), interpolation=cv2.INTER_AREA)
    source_edges_half = _edges(source_half)
    preview_edges_half = _edges(preview_half)
    distance_half = cv2.distanceTransform((preview_edges_half == 0).astype(np.uint8), cv2.DIST_L2, 3)
    unmatched_half = (source_edges_half > 0) & (distance_half > max(0.75, edge_tolerance / 2.0))
    unmatched_half_full = cv2.resize(unmatched_half.astype(np.uint8), (source_image.shape[1], source_image.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    unmatched_half_full = cv2.dilate(unmatched_half_full.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    unmatched = unmatched_full & unmatched_half_full
    source_count = int(np.count_nonzero(source_points))
    unmatched_count = int(np.count_nonzero(unmatched))
    coverage = 1.0 if source_count == 0 else 1.0 - unmatched_count / source_count
    adaptive_min_hotspot = max(4, int(round(10.0 * profile["area_scale"])))
    if policy.get("min_hotspot_edge_pixels") is not None:
        adaptive_min_hotspot = min(adaptive_min_hotspot, max(3, int(policy["min_hotspot_edge_pixels"])))
    join_radius = max(1, int(round(profile["area_scale"])))
    hotspots = _hotspots(unmatched.astype(np.uint8) * 255, adaptive_min_hotspot, join_radius=join_radius)
    micro_hotspots = [
        item for item in _hotspots(unmatched.astype(np.uint8) * 255, max(2, int(round(3 * profile["area_scale"]))), max_results=200, join_radius=join_radius)
        if item["box_px"][2] <= profile["micro_width_px"]
        and item["box_px"][3] <= profile["micro_height_px"]
        and item["edge_pixels"] <= max(16, int(110 * profile["area_scale"]))
    ]
    edge_density = source_count / max(1.0, float(np.count_nonzero(comparison_mask)))
    adaptive_minimum_coverage = float(np.clip(0.87 - 0.60 * edge_density, 0.76, 0.86))
    adaptive_maximum_hotspots = max(4, int(round(8.0 * profile["area_scale"] + 20.0 * edge_density)))
    minimum_coverage = adaptive_minimum_coverage
    maximum_hotspots = adaptive_maximum_hotspots
    if policy.get("min_source_edge_coverage") is not None:
        minimum_coverage = max(minimum_coverage, float(policy["min_source_edge_coverage"]))
    if policy.get("max_unexplained_hotspots") is not None:
        maximum_hotspots = min(maximum_hotspots, int(policy["max_unexplained_hotspots"]))
    coverage_failed = coverage < minimum_coverage
    hotspots_failed = len(hotspots) > maximum_hotspots
    metrics = {
        "source_edge_pixels": source_count,
        "unmatched_source_edge_pixels": unmatched_count,
        "source_edge_coverage": float(coverage),
        "unexplained_hotspots": hotspots,
        "unexplained_micro_hotspots": micro_hotspots,
        "edge_density": float(edge_density),
        "adaptive_edge_tolerance_px": float(edge_tolerance),
        "adaptive_min_source_edge_coverage": minimum_coverage,
        "adaptive_max_unexplained_hotspots": maximum_hotspots,
        "hard_failure": bool(coverage_failed and hotspots_failed),
        "review_only": bool(coverage_failed != hotspots_failed),
    }
    if coverage_failed and hotspots_failed:
        violations.append({
            "field": "source_coverage",
            "reason": f"multi-scale evidence agrees on omissions: edge coverage {coverage:.3f} < adaptive {minimum_coverage:.3f}, and {len(hotspots)} hotspots > adaptive {maximum_hotspots}",
            "hotspots": hotspots[:12],
        })
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
    profile = _scale_profile(source_image) if source_image is not None else None
    plot_padding = profile["plot_padding_px"] if profile else 12.0
    for shape in manifest.get("shapes", []):
        box = shape.get("plot_area_px")
        if _valid_box(box):
            x, y, width, height = [float(value) for value in box]
            plot_regions.append([max(0, x - plot_padding), max(0, y - plot_padding), width + 2 * plot_padding, height + 2 * plot_padding])
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
                local_short_side = min(source_crop.shape[:2])
                adaptive_tolerance = max(0.8, min(profile["distance_px"] if profile else 2.5, local_short_side * 0.12))
                requested_tolerance = entry.get("edge_tolerance_px")
                if requested_tolerance is not None:
                    adaptive_tolerance = min(adaptive_tolerance, max(0.5, float(requested_tolerance)))
                metrics = edge_fidelity_metrics(source_crop, preview_crop, adaptive_tolerance)
                results.append({"id": entry.get("id", f"micro_{index}"), "kind": entry.get("kind"), **metrics})
                if metrics["source_coverage"] < max(float(entry.get("min_source_coverage", 0.72)), 0.60):
                    violations.append({"field": field, "reason": f"micro annotation render covers only {metrics['source_coverage']:.3f} of source edges"})

    hotspots = (coverage_metrics or {}).get("unexplained_micro_hotspots", [])
    missing_micro = []
    for hotspot in hotspots:
        box = hotspot["box_px"]
        _x, _y, width, height = box
        if profile and (width > profile["micro_width_px"] or height > profile["micro_height_px"]):
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


def _palette_stats(crop):
    """Describe chroma and palette complexity without fixed hue buckets."""
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    lightness = lab[:, :, 0] * 100.0 / 255.0
    ab = lab[:, :, 1:3] - 128.0
    chroma = np.linalg.norm(ab, axis=2)
    # Background is inferred from the border, so white, tinted, and dark cards
    # all work without a global RGB cutoff.
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]), axis=0)
    background = np.median(border, axis=0)
    foreground = np.linalg.norm(lab - background, axis=2) > max(5.0, float(np.median(np.abs(border - background))) * 3.0)
    foreground_count = int(np.count_nonzero(foreground))
    foreground_chroma = chroma[foreground]
    if foreground_count == 0:
        return {"foreground_pixels": 0, "chromatic_pixels": 0, "chroma_ratio": 0.0, "median_chroma": 0.0, "palette_groups": 0}
    chroma_floor = max(10.0, float(np.percentile(foreground_chroma, 35)) * 0.65)
    chromatic = foreground & (chroma >= chroma_floor) & (lightness >= 8.0)
    samples = ab[chromatic]
    chroma_count = int(samples.shape[0])
    groups = 0
    if chroma_count:
        stride = max(1, chroma_count // 1500)
        data = samples[::stride].astype(np.float32)
        maximum_groups = min(5, max(1, int(round(math.sqrt(len(data) / 80.0)))))
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
        _compactness, labels, centers = cv2.kmeans(data, maximum_groups, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        counts = np.bincount(labels.ravel(), minlength=maximum_groups)
        minimum_share = max(0.04, 8.0 / len(data))
        significant = [i for i, count in enumerate(counts) if count / len(data) >= minimum_share]
        # Merge clusters that differ only through compression/antialiasing.
        merged = []
        for i in sorted(significant, key=lambda item: counts[item], reverse=True):
            if all(np.linalg.norm(centers[i] - centers[j]) >= 10.0 for j in merged):
                merged.append(i)
        groups = len(merged)
    return {
        "foreground_pixels": foreground_count,
        "chromatic_pixels": chroma_count,
        "chroma_ratio": float(chroma_count / foreground_count),
        "median_chroma": float(np.median(foreground_chroma)),
        "palette_groups": groups,
    }


def _source_curve_mask(source_image, roi, target_color, color_distance=None):
    crop = _crop(source_image, roi)
    if crop is None:
        return None, None
    target = _hex_to_bgr(target_color)
    if target is None:
        return crop, None
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    target_lab = cv2.cvtColor(target.reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    distance = np.linalg.norm(lab - target_lab, axis=2)
    nearest = np.percentile(distance, 2)
    near_values = distance[distance <= np.percentile(distance, 12)]
    dispersion = float(np.median(np.abs(near_values - np.median(near_values)))) if near_values.size else 0.0
    adaptive_distance = float(np.clip(nearest + 4.0 * max(2.0, dispersion), 10.0, 48.0))
    if color_distance is not None:
        adaptive_distance = min(adaptive_distance, max(8.0, float(color_distance)))
    mask = (distance <= adaptive_distance).astype(np.uint8) * 255
    kernel_size = max(1, int(round(min(crop.shape[:2]) * 0.004)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((kernel_size, kernel_size), np.uint8))
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
        _crop_image, source_mask = _source_curve_mask(source_image, roi, trace.get("target_color") or shape.get("stroke"), trace.get("color_distance", 48.0))
        source_support = None
        source_stroke_coverage = None
        trace_span_px = float(np.ptp(point_array[:, 0])) if width >= height else float(np.ptp(point_array[:, 1]))
        source_span_px = None
        span_ratio = None
        if source_mask is not None and np.count_nonzero(source_mask):
            mask_y, mask_x = np.nonzero(source_mask)
            source_axis = mask_x if width >= height else mask_y
            source_span_px = float(np.percentile(source_axis, 98) - np.percentile(source_axis, 2))
            span_ratio = trace_span_px / max(source_span_px, 1.0)
            local_points = np.rint(point_array - np.array([[x, y]], dtype=np.float32)).astype(np.int32)
            local_points[:, 0] = np.clip(local_points[:, 0], 0, source_mask.shape[1] - 1)
            local_points[:, 1] = np.clip(local_points[:, 1], 0, source_mask.shape[0] - 1)
            distance_to_source = cv2.distanceTransform((source_mask == 0).astype(np.uint8), cv2.DIST_L2, 3)
            distance_tolerance = max(1.0, min(source_mask.shape[:2]) * 0.025)
            source_support = float(np.mean(distance_to_source[local_points[:, 1], local_points[:, 0]] <= distance_tolerance))
            trace_mask = np.zeros(source_mask.shape, dtype=np.uint8)
            stroke_width = max(2, int(round(distance_tolerance * 2.0)))
            cv2.polylines(trace_mask, [local_points.reshape(-1, 1, 2)], False, 255, stroke_width, cv2.LINE_AA)
            source_stroke_coverage = float(np.count_nonzero((source_mask > 0) & (trace_mask > 0)) / max(1, np.count_nonzero(source_mask)))
        result = {
            "id": shape.get("id", f"curve_{index}"),
            "trace_primary_span_px": trace_span_px,
            "source_primary_span_px": source_span_px,
            "trace_to_source_span_ratio": span_ratio,
            "source_point_support": source_support,
            "source_stroke_coverage": source_stroke_coverage,
        }
        results.append(result)
        span_failed = span_ratio is None or span_ratio < 0.72
        support_failed = source_support is None or source_support < 0.60
        coverage_failed = source_stroke_coverage is None or source_stroke_coverage < 0.28
        if span_failed and (support_failed or coverage_failed):
            violations.append({"field": field, "reason": f"trace-to-source evidence indicates an incomplete curve (span={span_ratio}, point_support={source_support}, stroke_coverage={source_stroke_coverage})"})
        elif support_failed and coverage_failed:
            violations.append({"field": field, "reason": f"trace geometry is unsupported by the source stroke (point_support={source_support}, stroke_coverage={source_stroke_coverage})"})
    return violations, results


def _state_cell_indices(shapes, source_image):
    """Find repeated small rectangular indicators from geometry, not names."""
    if source_image is None:
        return set()
    page_height, page_width = source_image.shape[:2]
    candidates = []
    for index, shape in enumerate(shapes):
        if shape.get("type") not in {"rect", "roundRect"} or not _valid_box(shape.get("box_px")):
            continue
        x, y, width, height = [float(value) for value in shape["box_px"]]
        if width <= page_width * 0.12 and height <= page_height * 0.20:
            candidates.append((index, x, y, width, height))
    selected = {
        index for index, shape in enumerate(shapes)
        if str(shape.get("semantic_role") or "").lower() in {"state-cell", "status-cell", "indicator-cell"}
    }
    for anchor_pos, anchor in enumerate(candidates):
        ai, ax, ay, aw, ah = anchor
        group = [anchor]
        for candidate in candidates[anchor_pos + 1:]:
            ci, cx, cy, cw, ch = candidate
            size_similar = abs(cw - aw) <= max(2.0, 0.22 * aw) and abs(ch - ah) <= max(2.0, 0.22 * ah)
            row_aligned = abs((cy + ch / 2) - (ay + ah / 2)) <= max(2.0, 0.35 * ah)
            column_aligned = abs((cx + cw / 2) - (ax + aw / 2)) <= max(2.0, 0.35 * aw)
            if size_similar and (row_aligned or column_aligned):
                group.append(candidate)
        if len(group) >= 3:
            centers = sorted((item[1] + item[3] / 2 for item in group))
            gaps = np.diff(centers)
            regular = len(gaps) < 2 or float(np.median(np.abs(gaps - np.median(gaps)))) <= max(2.0, 0.35 * float(np.median(gaps)))
            if regular:
                selected.update(item[0] for item in group)
    return selected


def _source_fill_stats(crop):
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] *= 100.0 / 255.0
    lab[:, :, 1:] -= 128.0
    pixels = lab.reshape(-1, 3)
    center = np.median(pixels, axis=0)
    distances = np.linalg.norm(pixels - center, axis=1)
    dispersion = float(np.median(np.abs(distances - np.median(distances))))
    return center, dispersion


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
        source_stats = _palette_stats(source_crop)
        preview_stats = _palette_stats(preview_crop)
        result = {"id": image.get("id", f"image_{index}"), "kind": "image-palette", "source": source_stats, "preview": preview_stats}
        results.append(result)
        minimum_evidence = max(8, int(round(source_crop.shape[0] * source_crop.shape[1] * 0.002)))
        if source_stats["chromatic_pixels"] >= minimum_evidence and source_stats["chroma_ratio"] >= 0.08:
            retained = preview_stats["chroma_ratio"] / max(source_stats["chroma_ratio"], 1e-6)
            chroma_strength_retained = preview_stats["median_chroma"] / max(source_stats["median_chroma"], 1e-6)
            if retained < 0.38 and chroma_strength_retained < 0.55:
                violations.append({"field": f"images[{index}]", "reason": f"source chroma collapsed to {retained:.3f} of its original proportion; mild color shifts are allowed but color-to-grayscale drift is not"})
            if source_stats["palette_groups"] >= 3 and preview_stats["palette_groups"] <= max(1, source_stats["palette_groups"] - 2):
                violations.append({"field": f"images[{index}]", "reason": f"source palette diversity collapsed from {source_stats['palette_groups']} adaptive color groups to {preview_stats['palette_groups']}"})

    shapes = manifest.get("shapes", [])
    state_indices = _state_cell_indices(shapes, source_image)
    for index, shape in enumerate(shapes):
        if index not in state_indices:
            continue
        expected_bgr = _hex_to_bgr(shape.get("fill"))
        source_crop = _crop(source_image, shape["box_px"], inset=0.22)
        if expected_bgr is None or source_crop is None or source_crop.size < 9:
            continue
        source_lab, dispersion = _source_fill_stats(source_crop)
        fill_lab = _lab_color(expected_bgr)
        delta_e = float(np.linalg.norm(source_lab - fill_lab))
        tolerance = float(np.clip(14.0 + 2.8 * dispersion, 18.0, 44.0))
        result = {"id": shape.get("id", f"shape_{index}"), "kind": "state-fill", "delta_e": delta_e, "source_dispersion": dispersion, "adaptive_tolerance": tolerance}
        results.append(result)
        if delta_e > tolerance:
            violations.append({"field": f"shapes[{index}].fill", "reason": f"repeated state-cell fill differs from its source by DeltaE {delta_e:.1f}; adaptive tolerance is {tolerance:.1f}"})
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
