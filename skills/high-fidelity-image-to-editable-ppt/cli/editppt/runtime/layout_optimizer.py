#!/usr/bin/env python3
"""Source-pixel global layout constraint projection with soft source anchors."""
import argparse
import json
from copy import deepcopy
from pathlib import Path


def all_items(manifest):
    return [item for section in ("shapes", "images", "text_boxes") for item in manifest.get(section, [])]


def box(item):
    return [float(value) for value in item["box_px"]]


def set_box(item, value):
    item["box_px"] = [round(float(v), 3) for v in value]


def overlap(a, b, gap=0):
    ax, ay, aw, ah = box(a); bx, by, bw, bh = box(b)
    return ax < bx + bw + gap and bx < ax + aw + gap and ay < by + bh + gap and by < ay + ah + gap


def movable(item):
    return not item.get("locked", False)


def optimize_manifest(manifest, iterations=12):
    result = deepcopy(manifest)
    items = {str(item.get("id")): item for item in all_items(result) if item.get("id") and item.get("box_px")}
    original = {key: box(item) for key, item in items.items()}
    config = result.get("global_layout", {})
    constraints = list(config.get("constraints", []))
    for group in config.get("groups", []):
        if group.get("flow"):
            constraints.append({"type": "flow", "items": group.get("members", []), "axis": "vertical" if group["flow"] == "vertical" else "horizontal", "gap_px": group.get("gap_px", 0), "align": group.get("align")})
        if group.get("align"):
            edge = group["align"]
            if edge == "center": edge = "center_x" if group.get("flow") == "vertical" else "center_y"
            constraints.append({"type": "align", "items": group.get("members", []), "edge": edge})
    width = float(result.get("source", {}).get("width_px", 0)); height = float(result.get("source", {}).get("height_px", 0))
    margin = float(config.get("safe_margin_px", 0))

    for _ in range(max(1, iterations)):
        for constraint in constraints:
            kind = constraint.get("type")
            members = [items[key] for key in constraint.get("items", constraint.get("children", [])) if key in items]
            if kind == "contain" and constraint.get("parent") in items:
                parent = items[constraint["parent"]]; px, py, pw, ph = box(parent); pad = float(constraint.get("padding_px", 0))
                for item in members:
                    if not movable(item): continue
                    x, y, w, h = box(item); set_box(item, [min(max(x, px + pad), px + pw - pad - w), min(max(y, py + pad), py + ph - pad - h), w, h])
            elif kind == "align" and len(members) > 1:
                edge = constraint.get("edge", "left"); ref = box(members[0])
                for item in members[1:]:
                    if not movable(item): continue
                    x, y, w, h = box(item)
                    if edge == "left": x = ref[0]
                    elif edge == "right": x = ref[0] + ref[2] - w
                    elif edge == "top": y = ref[1]
                    elif edge == "bottom": y = ref[1] + ref[3] - h
                    elif edge == "center_x": x = ref[0] + (ref[2] - w) / 2
                    elif edge == "center_y": y = ref[1] + (ref[3] - h) / 2
                    set_box(item, [x, y, w, h])
            elif kind == "equal_size" and len(members) > 1:
                rw, rh = box(members[0])[2:]
                for item in members[1:]:
                    if movable(item):
                        x, y, _, _ = box(item); set_box(item, [x, y, rw, rh])
            elif kind in {"equal_gap", "flow"} and len(members) > 1:
                axis = constraint.get("axis", "horizontal"); gap = float(constraint.get("gap_px", 0))
                ordered = members if kind == "flow" else sorted(members, key=lambda i: box(i)[0 if axis == "horizontal" else 1])
                if kind == "equal_gap" and "gap_px" not in constraint:
                    first, last = box(ordered[0]), box(ordered[-1])
                    span = (last[0] + last[2] - first[0]) if axis == "horizontal" else (last[1] + last[3] - first[1])
                    gap = (span - sum(box(i)[2 if axis == "horizontal" else 3] for i in ordered)) / (len(ordered) - 1)
                cursor = box(ordered[0])[0 if axis == "horizontal" else 1] + box(ordered[0])[2 if axis == "horizontal" else 3] + gap
                for item in ordered[1:]:
                    x, y, w, h = box(item)
                    if movable(item):
                        if axis == "horizontal": x = cursor
                        else: y = cursor
                        set_box(item, [x, y, w, h])
                    cursor = (x + w if axis == "horizontal" else y + h) + gap
            elif kind == "non_overlap" and len(members) > 1:
                axis = constraint.get("axis", "vertical"); gap = float(constraint.get("gap_px", 0))
                ordered = sorted(members, key=lambda i: box(i)[1 if axis == "vertical" else 0])
                for previous, item in zip(ordered, ordered[1:]):
                    if not overlap(previous, item, gap) or not movable(item): continue
                    x, y, w, h = box(item); px, py, pw, ph = box(previous)
                    if axis == "vertical": y = py + ph + gap
                    else: x = px + pw + gap
                    set_box(item, [x, y, w, h])
            elif kind == "avoid":
                obstacles = [items[key] for key in constraint.get("obstacles", []) if key in items]
                direction = constraint.get("direction", "down"); gap = float(constraint.get("gap_px", 0))
                for item in members:
                    if not movable(item): continue
                    for obstacle in obstacles:
                        if not overlap(item, obstacle, gap): continue
                        x, y, w, h = box(item); ox, oy, ow, oh = box(obstacle)
                        if direction == "down": y = oy + oh + gap
                        elif direction == "up": y = oy - gap - h
                        elif direction == "right": x = ox + ow + gap
                        elif direction == "left": x = ox - gap - w
                        set_box(item, [x, y, w, h])
        for item in items.values():
            if not movable(item): continue
            x, y, w, h = box(item)
            if width: x = min(max(margin, x), max(margin, width - margin - w))
            if height: y = min(max(margin, y), max(margin, height - margin - h))
            set_box(item, [x, y, w, h])

    unresolved = []
    for constraint in constraints:
        kind = constraint.get("type")
        members = [items[key] for key in constraint.get("items", []) if key in items]
        if kind == "non_overlap":
            for index, a in enumerate(members):
                for b in members[index + 1:]:
                    if overlap(a, b, float(constraint.get("gap_px", 0))): unresolved.append([a.get("id"), b.get("id")])
        elif kind == "avoid":
            obstacles = [items[key] for key in constraint.get("obstacles", []) if key in items]
            for item in members:
                for obstacle in obstacles:
                    if overlap(item, obstacle, float(constraint.get("gap_px", 0))): unresolved.append([item.get("id"), obstacle.get("id")])
    movement = {key: round(sum((a - b) ** 2 for a, b in zip(original[key], box(item))) ** 0.5, 3) for key, item in items.items()}
    report = {"iterations": iterations, "movement_px": movement, "unresolved_overlaps": unresolved, "ok": not unresolved}
    result.setdefault("quality_checks", {})["global_layout_optimized"] = not unresolved
    result["layout_optimization"] = report
    return result, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optimize global source-pixel layout constraints.")
    parser.add_argument("page_dir"); parser.add_argument("--manifest", default="manifest.json"); parser.add_argument("--report", default="layout-optimization.json"); parser.add_argument("--iterations", type=int, default=12)
    args = parser.parse_args(argv); page = Path(args.page_dir).resolve(); manifest_path = page / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); optimized, report = optimize_manifest(manifest, args.iterations)
    manifest_path.write_text(json.dumps(optimized, ensure_ascii=False, indent=2), encoding="utf-8"); report_path = page / args.report; report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "report": str(report_path), **report}, ensure_ascii=False, indent=2)); return 0 if report["ok"] else 2


if __name__ == "__main__": raise SystemExit(main())
