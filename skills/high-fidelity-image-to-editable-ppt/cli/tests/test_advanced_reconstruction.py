import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

RUNTIME = Path(__file__).resolve().parents[1] / "editppt" / "runtime"
sys.path.insert(0, str(RUNTIME))

from bezier_curve import fit_points
from build_pptx_from_manifest import write_pptx
from layout_optimizer import optimize_manifest
from vectorize_icon import vectorize
from trace_plot_curve import trace_curve, trace_fragment
from universal_fidelity import (
    color_semantic_violations,
    curve_source_coverage_violations,
    micro_annotation_violations,
    reverse_source_coverage_violations,
)
from main import cmd_allow_offline_hints, cmd_next
from validate_pptx import (
    office_schema_violations,
    quality_contract_violations,
    text_overlap_violations,
    visual_inventory_coverage_violations,
)
from visual_fidelity import edge_fidelity_metrics, geometry_inventory_violations


class AdvancedReconstructionTests(unittest.TestCase):
    def test_reverse_coverage_detects_omitted_source_axis(self):
        source = __import__("numpy").full((120, 240, 3), 255, dtype="uint8")
        preview = source.copy()
        cv2 = __import__("cv2")
        cv2.line(source, (20, 90), (210, 90), (0, 0, 0), 2)
        cv2.line(source, (20, 90), (20, 25), (0, 0, 0), 2)
        cv2.putText(source, "t", (214, 94), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0), 1)
        manifest = {
            "slide": {"width": 2.4, "height": 1.2},
            "content_box": {"left": 0, "top": 0, "width": 2.4, "height": 1.2},
            "source": {"width_px": 240, "height_px": 120},
            "source_coverage_policy": {"max_unexplained_hotspots": 0, "min_source_edge_coverage": 0.95},
        }
        violations, metrics = reverse_source_coverage_violations(manifest, source, preview)
        self.assertLess(metrics["source_edge_coverage"], 0.5)
        self.assertTrue(any("source edges" in item["reason"] or "hotspots" in item["reason"] for item in violations))

    def test_plots_require_micro_annotation_inventory(self):
        image = __import__("numpy").full((100, 200, 3), 255, dtype="uint8")
        manifest = {"shapes": [{"id": "curve", "plot_area_px": [10, 10, 150, 60]}]}
        violations, _ = micro_annotation_violations(manifest, image, image, {"unexplained_hotspots": []})
        self.assertTrue(any(item["field"] == "micro_annotation_inventory" for item in violations))

    def test_trace_must_cover_source_roi_not_only_fit_its_own_short_points(self):
        cv2 = __import__("cv2")
        source = __import__("numpy").full((100, 220, 3), 255, dtype="uint8")
        points = __import__("numpy").array([(x, 70 - int(20 * x / 200)) for x in range(10, 211)], dtype="int32")
        cv2.polylines(source, [points], False, (166, 57, 18), 2)
        manifest = {"shapes": [{
            "id": "short", "type": "curve", "curve_role": "data-stroke", "stroke": "#1239A6",
            "plot_area_px": [10, 20, 200, 60],
            "curve_trace": {"target_color": "#1239A6", "color_distance": 30, "trace_points_px": [[10, 70], [30, 68], [50, 66]]},
        }]}
        violations, results = curve_source_coverage_violations(manifest, source)
        self.assertLess(results[0]["primary_span_ratio"], 0.55)
        self.assertTrue(any("trace spans only" in item["reason"] for item in violations))

    def test_color_gate_allows_mild_shift_but_rejects_grayscale_collapse_and_wrong_cell(self):
        np = __import__("numpy")
        source = np.full((100, 240, 3), 255, dtype="uint8")
        preview_mild = source.copy()
        colors_source = [(30, 150, 40), (20, 190, 210), (20, 100, 240)]
        colors_mild = [(35, 160, 45), (25, 180, 220), (25, 110, 235)]
        for idx, color in enumerate(colors_source):
            source[15:55, 15 + idx * 35:45 + idx * 35] = color
        for idx, color in enumerate(colors_mild):
            preview_mild[15:55, 15 + idx * 35:45 + idx * 35] = color
        source[70:90, 180:200] = (30, 30, 225)
        preview_mild[70:90, 180:200] = (35, 35, 220)
        manifest = {
            "slide": {"width": 2.4, "height": 1.0}, "content_box": {"left": 0, "top": 0, "width": 2.4, "height": 1.0},
            "source": {"width_px": 240, "height_px": 100},
            "images": [{"id": "palette", "box_px": [10, 10, 115, 50]}],
            "shapes": [{"id": "risk_cell", "type": "rect", "box_px": [180, 70, 20, 20], "fill": "#DD2222"}],
        }
        mild_violations, _ = color_semantic_violations(manifest, source, preview_mild)
        self.assertEqual(mild_violations, [])
        preview_gray = preview_mild.copy()
        preview_gray[10:60, 10:130] = (80, 80, 80)
        manifest["shapes"][0]["fill"] = "#F1F1F1"
        violations, _ = color_semantic_violations(manifest, source, preview_gray)
        self.assertTrue(any("chroma collapsed" in item["reason"] or "palette diversity" in item["reason"] for item in violations))
        self.assertTrue(any("DeltaE" in item["reason"] for item in violations))

    def test_ocr_gate_blocks_builtin_fallback_and_explicit_override_allows_next(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            page_dir = run_dir / "pages" / "page_001"
            page_dir.mkdir(parents=True)
            (run_dir / "deck_manifest.json").write_text(json.dumps({"image_backend": {"kind": "test"}}), encoding="utf-8")
            (run_dir / "page_jobs.json").write_text(json.dumps({
                "max_concurrent_pages": 1,
                "pages": [{"page_id": "page_001", "page_dir": "pages/page_001", "status": "pending"}],
            }), encoding="utf-8")
            (page_dir / "text_hints.json").write_text(json.dumps({"backend": "builtin-ink", "lines": []}), encoding="utf-8")

            out = StringIO()
            with patch.dict(os.environ, {"PADDLE_OCR_TOKEN": "configured-test-token"}), redirect_stdout(out):
                self.assertEqual(cmd_next(SimpleNamespace(run=str(run_dir), json=True)), 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["stage"], "ocr_quality_gate")
            self.assertIn("fell back", payload["reason"])

            with redirect_stdout(StringIO()):
                cmd_allow_offline_hints(SimpleNamespace(run=str(run_dir), reason="User explicitly approved local-only OCR hints"))
            out = StringIO()
            with patch.dict(os.environ, {"PADDLE_OCR_TOKEN": "configured-test-token"}), redirect_stdout(out):
                self.assertEqual(cmd_next(SimpleNamespace(run=str(run_dir), json=True)), 0)
            self.assertEqual(json.loads(out.getvalue())["stage"], "rebuild_page_locally")

    def test_empty_geometry_inventory_rejected_when_lines_or_curves_exist(self):
        manifest = {
            "shapes": [{"id": "line", "type": "line", "semantic_role": "divider", "points_px": [0, 0, 10, 10]}],
            "geometry_inventory": [],
        }
        violations, _ = geometry_inventory_violations(manifest)
        self.assertTrue(any("cannot be empty" in item["reason"] for item in violations))

    def test_native_structural_curve_requires_source_trace(self):
        manifest = {
            "visual_inventory": [],
            "background_strategy": {"mode": "native"},
            "quality_checks": {},
            "shapes": [{
                "id": "curve", "type": "curve", "semantic_role": "structural-connector",
                "curve_role": "native-structural", "bezier_px": {"start": [0, 0], "segments": [{"c1": [1, 1], "c2": [2, 2], "end": [3, 3]}]},
            }],
        }
        reasons = [item["reason"] for item in quality_contract_violations(manifest)]
        self.assertTrue(any("every non-fill curve" in reason for reason in reasons))

    def test_visual_inventory_must_cover_every_image_and_explain_reuse(self):
        manifest = {
            "images": [
                {"id": "one", "path": "assets/reused.png"},
                {"id": "two", "path": "assets/reused.png"},
            ],
            "visual_inventory": [{"id": "v", "object_ids": ["one"], "path": "assets/reused.png"}],
        }
        violations = visual_inventory_coverage_violations(manifest)
        self.assertTrue(any(item["field"] == "images[1]" for item in violations))
        self.assertTrue(any(item["field"] == "images[1].reuse_reason" for item in violations))
        self.assertEqual(sum("reuse_reason" in item["field"] for item in violations), 2)

    def test_material_text_overlap_requires_explicit_allowlist(self):
        manifest = {"text_boxes": [
            {"id": "a", "box_px": [10, 10, 100, 30]},
            {"id": "b", "box_px": [20, 15, 100, 30]},
        ]}
        self.assertEqual(len(text_overlap_violations(manifest)), 1)
        manifest["text_boxes"][0]["allow_overlap_with"] = ["b"]
        self.assertEqual(text_overlap_violations(manifest), [])

    def test_generated_package_passes_core_ooxml_schema_guard(self):
        manifest = {
            "slide": {"width": 13.333, "height": 7.5},
            "source": {"width_px": 1600, "height_px": 900},
            "shapes": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest_path = root / "manifest.json"; out = root / "schema-safe.pptx"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            write_pptx(manifest, out, manifest_path)
            with zipfile.ZipFile(out) as package:
                names = package.namelist()
                self.assertEqual(office_schema_violations(package, names), [])
                presentation = package.read("ppt/presentation.xml").decode("utf-8")
                self.assertIn('type="screen16x9"', presentation)

    def test_bezier_is_drawingml_cubic(self):
        points = [[x, 100 + (x - 100) ** 2 / 100] for x in range(20, 181, 10)]
        curve, error, _ = fit_points(points, tolerance=0.75)
        self.assertLess(error, 3.0)
        manifest = {"slide": {"width": 13.333, "height": 7.5}, "source": {"width_px": 200, "height_px": 200}, "shapes": [{"id": "c", "type": "curve", "bezier_px": curve, "stroke": "#1246A0", "fill": "none"}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest_path = root / "manifest.json"; manifest_path.write_text(json.dumps(manifest), encoding="utf-8"); out = root / "curve.pptx"
            write_pptx(manifest, out, manifest_path)
            with zipfile.ZipFile(out) as package:
                xml = package.read("ppt/slides/slide1.xml").decode("utf-8")
            self.assertIn("<a:cubicBezTo>", xml)

    def test_line_end_markers_are_real_drawingml_geometry(self):
        manifest = {
            "slide": {"width": 10, "height": 5.625},
            "source": {"width_px": 1000, "height_px": 563},
            "shapes": [{"id": "axis", "type": "line", "points_px": [100, 200, 700, 200], "stroke": "#111111", "start_arrow": "none", "end_arrow": "triangle"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest_path = root / "manifest.json"; out = root / "arrow.pptx"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            write_pptx(manifest, out, manifest_path)
            with zipfile.ZipFile(out) as package:
                xml = package.read("ppt/slides/slide1.xml").decode("utf-8")
                self.assertEqual(office_schema_violations(package, package.namelist()), [])
            self.assertIn('<a:tailEnd type="triangle"/>', xml)

    def test_vectorizer_emits_paths_not_embedded_raster(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); png = root / "icon.png"; svg = root / "icon.svg"
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0)); draw = ImageDraw.Draw(image); draw.ellipse((5, 5, 58, 58), fill=(10, 120, 220, 255)); draw.rectangle((28, 16, 35, 48), fill=(255, 255, 255, 255)); image.save(png)
            report = vectorize(png, svg, colors=3, tolerance=1.0); content = svg.read_text(encoding="utf-8")
            self.assertGreater(report["paths"], 0); self.assertIn("<path", content); self.assertNotIn("base64", content); self.assertFalse(report["embedded_raster"])

    def test_layout_resolves_overlap_and_preserves_lock(self):
        manifest = {"source": {"width_px": 400, "height_px": 200}, "quality_checks": {}, "text_boxes": [{"id": "a", "box_px": [10, 10, 120, 30], "locked": True}, {"id": "b", "box_px": [20, 20, 120, 30]}], "global_layout": {"safe_margin_px": 4, "constraints": [{"type": "non_overlap", "items": ["a", "b"], "axis": "vertical", "gap_px": 8}]}}
        optimized, report = optimize_manifest(manifest)
        self.assertTrue(report["ok"]); self.assertEqual(optimized["text_boxes"][0]["box_px"], [10.0, 10.0, 120.0, 30.0]); self.assertGreaterEqual(optimized["text_boxes"][1]["box_px"][1], 48)

    def test_pixel_trace_ignores_vertical_marker_and_keeps_axis_clearance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.png"
            image = Image.new("RGB", (220, 140), "white"); draw = ImageDraw.Draw(image)
            draw.line((15, 120, 205, 120), fill="black", width=2); draw.line((15, 20, 15, 120), fill="black", width=2)
            points = [(x, 112 - 65 * (1 - ((x - 110) / 90) ** 2)) for x in range(25, 196)]
            draw.line(points, fill="#0B3B99", width=2)
            for y in range(45, 114, 10): draw.line((165, y, 165, y + 5), fill="#0B3B99", width=1)
            image.save(source)
            shape, _mask, _crop = trace_fragment(source, [16, 20, 190, 100], "curve", "#0B3B99", 55, 18, [3, 1, 3, 5], 0.8, "#0B3B99", 1.2)
            self.assertEqual(shape["curve_role"], "data-stroke"); self.assertLess(shape["curve_trace"]["symmetric_chamfer_px"], 1.5)
            self.assertTrue(all(y <= 115 for _x, y in shape["curve_trace"]["trace_points_px"]))

    def test_dashed_trace_does_not_switch_to_crossing_solid_curve(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.png"
            image = Image.new("RGB", (240, 150), "white"); draw = ImageDraw.Draw(image)
            dashed = [(x, 118 - 62 * pow(2.718281828, -((x - 72) / 34) ** 2)) for x in range(20, 221)]
            solid = [(x, 118 - 66 * pow(2.718281828, -((x - 166) / 36) ** 2)) for x in range(20, 221)]
            draw.line(solid, fill="#0B3B99", width=2)
            for start in range(0, len(dashed), 14):
                draw.line(dashed[start:start + 8], fill="#0B3B99", width=2)
            image.save(source)

            points, _mask, _crop = trace_curve(
                source, [15, 35, 215, 90], "#0B3B99", 55, 18, 8.0,
                3, 2, 2, 1, "dashed", 12,
            )
            dashed_y = lambda x: 118 - 62 * pow(2.718281828, -((x - 72) / 34) ** 2)
            solid_y = lambda x: 118 - 66 * pow(2.718281828, -((x - 166) / 36) ** 2)
            dashed_error = sum(abs(y - dashed_y(x)) for x, y in points) / len(points)
            solid_error = sum(abs(y - solid_y(x)) for x, y in points) / len(points)
            self.assertLess(dashed_error, 2.5)
            self.assertLess(dashed_error, solid_error)

    def test_layout_avoid_moves_icon_below_plot(self):
        manifest = {"source": {"width_px": 300, "height_px": 200}, "quality_checks": {}, "shapes": [{"id": "plot", "box_px": [20, 20, 150, 100], "locked": True}], "images": [{"id": "phone", "box_px": [80, 100, 30, 30]}], "global_layout": {"constraints": [{"type": "avoid", "items": ["phone"], "obstacles": ["plot"], "direction": "down", "gap_px": 6}]}}
        optimized, report = optimize_manifest(manifest)
        self.assertTrue(report["ok"]); self.assertEqual(optimized["images"][0]["box_px"][1], 126.0)

    def test_complex_arrow_requires_source_decision(self):
        manifest = {"visual_inventory": [], "background_strategy": {"mode": "native"}, "quality_checks": {key: True for key in ("font_size_calibrated", "visual_inventory_matched", "background_strategy_checked", "shape_corner_geometry_checked", "bezier_curves_checked", "vector_assets_checked", "global_layout_optimized", "curve_source_traced", "axis_clearance_checked", "decorative_assets_checked")}, "shapes": [{"id": "red_arrow", "type": "rect", "preset": "curvedRightArrow", "box_px": [1, 1, 10, 10]}]}
        reasons = [item["reason"] for item in quality_contract_violations(manifest)]
        self.assertTrue(any("asset-sheet separation" in reason for reason in reasons))

    def test_axis_inventory_rejects_missing_source_arrow(self):
        manifest = {
            "slide": {"width": 10, "height": 5.625},
            "content_box": {"left": 0, "top": 0, "width": 10, "height": 5.625},
            "source": {"width_px": 200, "height_px": 100},
            "shapes": [{"id": "axis", "type": "line", "semantic_role": "axis", "points_px": [20, 50, 180, 50]}],
            "geometry_inventory": [{"id": "axis_check", "kind": "axis", "object_ids": ["axis"], "source_box_px": [15, 42, 175, 18], "expected_start_marker": "none", "expected_end_marker": "triangle"}],
        }
        violations, _ = geometry_inventory_violations(manifest)
        self.assertTrue(any("manifest line uses none" in item["reason"] for item in violations))

    def test_matching_axis_inventory_and_edge_render_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source_path = root / "source.png"; preview_path = root / "preview.png"
            image = Image.new("RGB", (200, 100), "white"); draw = ImageDraw.Draw(image)
            draw.line((20, 50, 170, 50), fill="black", width=2); draw.polygon([(180, 50), (168, 43), (168, 57)], fill="black")
            image.save(source_path); image.save(preview_path)
            manifest = {
                "slide": {"width": 2, "height": 1},
                "content_box": {"left": 0, "top": 0, "width": 2, "height": 1},
                "source": {"width_px": 200, "height_px": 100},
                "shapes": [{"id": "axis", "type": "line", "semantic_role": "axis", "points_px": [20, 50, 180, 50], "start_arrow": "none", "end_arrow": "triangle"}],
                "geometry_inventory": [{"id": "axis_check", "kind": "axis", "object_ids": ["axis"], "source_box_px": [10, 38, 180, 25], "expected_start_marker": "none", "expected_end_marker": "triangle"}],
            }
            violations, metrics = geometry_inventory_violations(manifest, source_path, preview_path)
            self.assertEqual(violations, []); self.assertEqual(len(metrics), 1); self.assertEqual(metrics[0]["source_coverage"], 1.0)

    def test_bracket_inventory_rejects_wrong_preset(self):
        manifest = {
            "source": {"width_px": 200, "height_px": 100},
            "shapes": [{"id": "brace", "type": "rect", "semantic_role": "bracket", "preset": "rightBracket", "box_px": [80, 20, 20, 60]}],
            "geometry_inventory": [{"id": "brace_check", "kind": "bracket", "object_ids": ["brace"], "source_box_px": [80, 20, 20, 60], "bracket_style": "curly", "orientation": "vertical"}],
        }
        violations, _ = geometry_inventory_violations(manifest)
        self.assertTrue(any("curly bracket" in item["reason"] for item in violations))

    def test_object_edge_metric_penalizes_missing_arrowhead(self):
        source = Image.new("RGB", (180, 40), "white"); source_draw = ImageDraw.Draw(source)
        source_draw.line((5, 20, 165, 20), fill="black", width=2)
        source_draw.polygon([(175, 20), (163, 13), (163, 27)], fill="black")
        preview = Image.new("RGB", (180, 40), "white"); ImageDraw.Draw(preview).line((5, 20, 165, 20), fill="black", width=2)
        metrics = edge_fidelity_metrics(
            __import__("numpy").array(source)[:, :, ::-1],
            __import__("numpy").array(preview)[:, :, ::-1],
            tolerance_px=2.0,
        )
        self.assertLess(metrics["source_coverage"], 0.95)


if __name__ == "__main__":
    unittest.main()
