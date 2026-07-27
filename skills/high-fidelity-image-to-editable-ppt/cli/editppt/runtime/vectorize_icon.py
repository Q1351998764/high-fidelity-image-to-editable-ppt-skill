#!/usr/bin/env python3
"""Deterministically vectorize an already separated transparent icon PNG."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def vectorize(input_path, svg_path, colors=8, tolerance=1.2):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("vectorize icon requires opencv-python-headless; reinstall the editppt CLI") from exc
    image = cv2.imread(str(input_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"cannot read image: {input_path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    height, width = image.shape[:2]
    alpha = image[:, :, 3]
    visible = alpha > 8
    if not visible.any():
        raise ValueError("input icon has no visible alpha pixels")
    rgb = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2RGB)
    pixels = rgb[visible].reshape((-1, 3)).astype(np.float32)
    k = max(1, min(int(colors), len(pixels)))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _compactness, labels, centers = cv2.kmeans(pixels, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    label_map = np.full((height, width), -1, dtype=np.int16); label_map[visible] = labels[:, 0]
    paths = []
    for label, center in enumerate(centers):
        mask = ((label_map == label).astype(np.uint8) * 255)
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hierarchy is None: continue
        color = "#" + "".join(f"{max(0, min(255, int(round(v)))):02X}" for v in center)
        compound_commands = []
        for contour in contours:
            if abs(cv2.contourArea(contour)) < 1.0: continue
            simplified = cv2.approxPolyDP(contour, float(tolerance), True).reshape((-1, 2))
            if len(simplified) < 3: continue
            commands = [f"M {simplified[0][0]:.2f} {simplified[0][1]:.2f}"]
            commands.extend(f"L {point[0]:.2f} {point[1]:.2f}" for point in simplified[1:])
            compound_commands.append(" ".join(commands) + " Z")
        if compound_commands:
            # Contours of one quantized color form one compound path. evenodd
            # preserves holes such as clock faces, phone screens, shields, and
            # outlined pictograms instead of painting every inner contour solid.
            paths.append(f'<path d="{" ".join(compound_commands)}" fill="{color}" fill-rule="evenodd"/>')
    if not paths:
        raise ValueError("no vector contours were produced")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + "".join(paths) + "</svg>"
    svg_path.parent.mkdir(parents=True, exist_ok=True); svg_path.write_text(svg, encoding="utf-8")
    return {"width_px": width, "height_px": height, "paths": len(paths), "colors": k, "embedded_raster": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vectorize a separated icon PNG to SVG and optional EMF.")
    parser.add_argument("page_dir"); parser.add_argument("--input", required=True); parser.add_argument("--out", required=True); parser.add_argument("--emf"); parser.add_argument("--colors", type=int, default=8); parser.add_argument("--tolerance", type=float, default=1.2); parser.add_argument("--fragment"); parser.add_argument("--id", default="vector_icon"); parser.add_argument("--box", help="X,Y,W,H source-pixel placement")
    args = parser.parse_args(argv); page = Path(args.page_dir).resolve(); source = Path(args.input); source = source if source.is_absolute() else page / source; out = Path(args.out); out = out if out.is_absolute() else page / out
    report = vectorize(source, out, args.colors, args.tolerance); report.update({"input": str(source), "svg": str(out), "method": "alpha-color-contour-vectorization"})
    if args.emf:
        emf = Path(args.emf); emf = emf if emf.is_absolute() else page / emf; inkscape = shutil.which("inkscape")
        if inkscape:
            emf.parent.mkdir(parents=True, exist_ok=True); subprocess.run([inkscape, str(out), "--export-filename", str(emf)], check=True); report["emf"] = str(emf)
        else: report["emf_warning"] = "Inkscape unavailable; SVG retained as the authoritative vector asset"
    if args.fragment:
        if not args.box: raise SystemExit("--fragment requires --box X,Y,W,H")
        rel = out.relative_to(page).as_posix() if out.is_relative_to(page) else str(out)
        source_rel = source.relative_to(page).as_posix() if source.is_relative_to(page) else str(source)
        image_item = {"id": args.id, "path": rel, "box_px": [float(v) for v in args.box.split(",")], "alt": "asset-sheet-separated vector icon", "vectorization": report}
        fragment = {
            "images": [image_item],
            "asset_provenance": [{"path": rel, "source": source_rel, "source_type": "asset-sheet-separated", "provenance_note": "Source-faithful image-edit asset-sheet separation followed by deterministic alpha/color contour vectorization."}],
            "vector_inventory": [{"id": args.id, "image": rel, "source": source_rel, "editable": "vector-image", "embedded_raster": False}],
        }
        fragment_path = Path(args.fragment); fragment_path = fragment_path if fragment_path.is_absolute() else page / fragment_path; fragment_path.parent.mkdir(parents=True, exist_ok=True); fragment_path.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8"); report["fragment"] = str(fragment_path)
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
