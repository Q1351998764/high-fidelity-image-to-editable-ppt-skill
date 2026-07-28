# Manifest Schema

This document describes the responsibilities, owners, and current field contracts for `editppt` run/page JSON files. All key state is advanced by `editppt` commands; page reconstructors write only page-local files.

## Contents

- `deck_manifest.json`
- `page_jobs.json`
- `page_request.json`
- `page_result.json`
- `pages/page_NNN/validation.json`
- `pages/page_NNN/manifest.json`
- `pages/page_NNN/imagegen-jobs.json`
- `notes_manifest.json`

## `deck_manifest.json`

Owner: created by `editppt prepare`; `editppt run backend` may update the image backend; `editppt run finalize` reads it and writes completion time.

Purpose:

- Input type.
- Page order.
- Page manifest paths.
- Notes manifest path.
- Final output path.
- Run-level image backend contract.
- Original user request.

Key fields:

```json
{
  "schema_version": 1,
  "run_id": "job-id",
  "input_type": "image|images|pdf|pptx",
  "max_concurrent_pages": 6,
  "image_backend": {
    "backend_id": "builtin-imagegen",
    "tool_name": "image_gen.imagegen",
    "required_parameters": {
      "generate": ["prompt"],
      "edit": ["prompt", "referenced_image_paths"]
    },
    "input_context_policy": "generate with prompt; before editing, view_image each input, then use prompt plus absolute local referenced_image_paths",
    "save_path_policy": "use only an explicit valid local result/output_hint path, then editppt image import; never scan for a newest file",
    "fallback_command": "editppt image generate/edit",
    "fallback_order": ["codex-oauth", "openai-compatible-api"],
    "fallback_policy": {
      "on": [
        "tool-unavailable",
        "tool-error",
        "input-unreadable",
        "no-valid-local-output"
      ],
      "missing_optional_parameters": false
    }
  },
  "pages": [],
  "notes_manifest": "notes_manifest.json",
  "output": "final/origin_edited.pptx"
}
```

`image_backend` is written by `editppt prepare` and may be overwritten by `editppt run backend` when needed. Parent-level backend selection policy lives in `SKILL.md` subsection "Image Backend Selection".

For `backend_id: "builtin-imagegen"`, these fields are required and have fixed meanings:

- `tool_name`: `image_gen.imagegen`, an agent tool rather than a Python or shell API.
- `required_parameters`: the complete required argument sets. Generation needs `prompt`; editing needs `prompt` plus absolute local paths in `referenced_image_paths`.
- `input_context_policy`: requires `view_image` on every edit input before the built-in call; generation has no image input.
- `save_path_policy`: permits only an explicit valid local result path, including `output_hint`, followed by `editppt image import`; newest-file directory scanning is forbidden.
- `fallback_command`: the CLI surface used only after the fallback policy matches.
- `fallback_order`: the CLI's internal order, Codex OAuth before a configured OpenAI-compatible API.
- `fallback_policy.on`: the only events that permit leaving the built-in tool: it is unavailable/not callable, its call errors, an edit input is unreadable, or it returns no valid local image.
- `fallback_policy.missing_optional_parameters`: always `false`; absent optional controls never authorize fallback.

Other backend metadata may describe model labels, runtime homes, or handoff text, but it does not change this order. Parent-level tool selection and user-interaction policy live in `SKILL.md` subsection "Image Backend Selection"; page reconstructors execute the copied contract above.

## `page_jobs.json`

Owner: created by `editppt prepare`, updated by `editppt run` commands.

Purpose:

- Source of truth for page state.
- Dispatch records.
- Result records.

Structure:

```json
{
  "schema_version": 1,
  "run_id": "job-id",
  "max_concurrent_pages": 6,
  "pages": [
    {
      "page_id": "page_001",
      "status": "pending",
      "page_dir": "pages/page_001",
      "page_request": "pages/page_001/page_request.json",
      "source": "pages/page_001/source.png",
      "dispatch": null,
      "result": null
    }
  ]
}
```

`dispatch` is written by `editppt run dispatch`. It includes `execution_mode`: `"worker"` for normal page-worker dispatch and `"local"` for the parent agent's single-page local claim; older dispatch records without this field are treated as `"worker"`. A page with status `dispatched` is an active execution lease until explicit completion, failure, cancellation, or lost-worker verification; elapsed time alone does not make it lost. `result` is written by `editppt run record`. `accepted` is written by `editppt run finalize`.

## `page_request.json`

Owner: `editppt prepare`.

Purpose: task boundary for the page worker.

Includes:

- page id
- page directory
- source image
- slide size
- content box
- max concurrent pages
- allowed write scope
- required outputs
- user constraints
- image backend contract

Must not include:

- page type prediction
- `imagegen_required` prediction
- object-level decisions

If the run uses an image backend, `page_request.json` must contain the same `image_backend` object without weakening or reordering its `fallback_policy` or `fallback_order`.

`slide` and `content_box` are computed automatically by `editppt prepare`. Inputs close to 16:9 use the standard widescreen canvas; other inputs use a custom canvas converted from the source image pixel dimensions. The agent must copy these two fields into the page `manifest.json` and must not compress, stretch, or recalculate the canvas.

## `page_result.json`

Owner: created by the page reconstructor, validated by `editppt run record`.

Includes:

- manifest path
- imagegen jobs path
- page pptx path
- preview path
- contact sheet path
- validation path
- page-local output hashes, which may be supplemented by `editppt run record`

Minimal required shape (paths are relative to the page directory):

```json
{
  "page_manifest": "manifest.json",
  "imagegen_jobs": "imagegen-jobs.json",
  "page_pptx": "page.pptx",
  "preview": "preview.png",
  "contact_sheet": "split_assets_contact.png",
  "validation": "validation.json",
  "page_result": "page_result.json"
}
```

The `manifest` artifact is the authoritative page source for final assembly. `editppt run finalize` rebuilds the final deck from recorded page manifests in page order. The `page_pptx` artifact remains a page-level deliverability artifact and is validated by `editppt run record`, but it is not the final assembly input.

## `pages/page_NNN/validation.json`

Owner: created by the page reconstructor, read by `editppt run record`.

Purpose: page-level deliverability conclusion.

Must contain at top level:

```json
{
  "passed": true
}
```

`passed` must be a boolean. `editppt run record` only reads top-level `passed` to decide whether the page can enter final assembly. `status: "pass"`, `runtime_validation.passed`, or other nested fields may remain as supplemental information, but they cannot replace top-level `passed`.

## `pages/page_NNN/manifest.json`

Owner: page reconstructor.

Purpose: source of truth for page-level PPTX construction.

The manifest is not a summary of a separately authored `page.pptx`. It is the build contract for both page-level validation and final deck assembly. A page may not pass validation if the page PPTX can only be reproduced by custom page-local code while the manifest lacks object positions.

Must contain:

- `slide`
- `content_box`
- `source`
- `text_inventory`
- `visual_inventory`
- `geometry_inventory`
- `micro_annotation_inventory` when the page contains plots
- `background_strategy`
- `quality_checks`
- `text_boxes`
- `shapes`
- `images`
- `asset_provenance`
- page strategy

`slide`, `content_box`, and `source.width_px/source.height_px` must come from `page_request.json`. All `box_px`, `points_px`, `polygon_px`, and `bezier_px` values use `source.png` pixel coordinates; the runtime maps these coordinates into `content_box` instead of stretching them to the whole slide. Coordinate layouts:

- `box_px: [x, y, width, height]`
- `points_px: [x1, y1, x2, y2]`
- `bezier_px: {"start": [x,y], "segments": [{"c1": [x,y], "c2": [x,y], "end": [x,y]}], "closed": false}`

Positioned build object requirements:

- Every `text_boxes[]` item must have `box_px`. Text in `text_inventory` does not create a positioned text box.
- Every `images[]` item must have `box_px`.
- Every non-line/non-curve `shapes[]` item must have `box_px`; curve bounds are derived from all Bézier points when omitted.
- Every line shape must have `points_px`.
- Every curve shape must have valid `bezier_px` cubic segments. `polygon_px` is not an acceptable replacement for a smooth source curve.

`text_inventory` and `visual_inventory` are only inventories; they do not substitute for positioned `text_boxes`, `images`, and `shapes`. The manifest must be sufficient to rebuild the page without reading any custom page script. Every positioned `images[]` object must be mapped by a `visual_inventory[].object_ids` entry, or by an exact asset path only when that path is used once. Reusing one asset path for multiple positioned images requires both stable object-id mappings and a non-empty `reuse_reason` on every reuse; one generic inventory sentence cannot cover many unrelated icons.

Missing coordinates are page-contract violations. The runtime must reject them during `editppt run record` and deck validation because otherwise missing values fall back to default positions such as the top-left corner.

Text-size fitting:

- `text_boxes[].font_size` is treated as the requested font size. The deterministic builder may clamp it downward during normalization when the requested size is too large for the resolved source-pixel box.
- Keep default fitting enabled for first drafts. Set `fit_text: false` only when the page author has manually calibrated the box and font size.
- `text_boxes[].box_px` should describe the source text bounds plus modest padding. Do not use an entire card, chart, table cell group, or unrelated container as the text box, because the fitter can only infer size from the box it receives.
- Optional tuning fields are `min_font_size`, `max_font_size`, `text_fit_safety`, and `line_height`.

Text alignment:

- `text_boxes[].align` accepts `left`, `center`, or `right` (default `left`). The equivalent DrawingML tokens `l`, `ctr`, and `r` are also accepted.
- `text_boxes[].valign` accepts `top`, `middle`, or `bottom` (default `top`); `center` is an alias for `middle`. The equivalent DrawingML tokens `t`, `ctr`, and `b` are also accepted.
- The deterministic builder translates these manifest values to valid DrawingML enum tokens. Unsupported values are page-contract violations instead of silently falling back to an application default.

`text_inventory` may be a list of strings or a list of structured objects. In structured objects, the fields used for exact text validation are `text`, `required_text`, `items`, or `texts`; fields such as `id`, `decision`, `description`, and `note` are only records and are not used for exact text matching. Example:

```json
[
  {"id": "title", "text": "Market Overview", "decision": "native-text"},
  {"id": "metrics", "required_text": ["Annual recurring revenue", "42.8M"]}
]
```

`quality_checks` must include at least:

```json
{
  "font_size_calibrated": true,
  "visual_inventory_matched": true,
  "background_strategy_checked": true,
  "shape_corner_geometry_checked": true,
  "bezier_curves_checked": true,
  "vector_assets_checked": true,
  "global_layout_optimized": true,
  "curve_source_traced": true,
  "axis_clearance_checked": true,
  "decorative_assets_checked": true,
  "geometry_inventory_checked": true,
  "structural_fidelity_checked": true,
  "legend_source_traced": true,
  "axis_marker_checked": true,
  "bracket_geometry_checked": true,
  "source_coverage_checked": true,
  "micro_annotations_checked": true,
  "curve_source_coverage_checked": true,
  "color_semantics_checked": true
}
```

The four final flags are evidence-backed gates rather than self-attestations. `editppt page validate` recomputes them from `source.png`, `preview.png`, and positioned objects. Setting a flag to `true` never waives a failed measurement.

`geometry_inventory` is the hard visual-geometry contract for small components that whole-page review commonly misses. It cannot be empty when the page contains visible lines or curves. It must cover every shape whose `semantic_role` is `axis` or `bracket`, and every curve whose `curve_role` is `legend-symbol` or `native-structural`. Each entry uses a tight source-pixel ROI; `editppt page validate` maps that ROI into `preview.png`, compares source/render edges, and fails missing or distorted geometry. Setting `quality_checks.geometry_inventory_checked=true` does not waive missing inventory entries or failed pixel evidence.

`source_coverage_policy` controls the whole-page reverse-coverage gate. The runtime aligns `preview.png` back to source pixels, masks declared editable text boxes, and finds source edges that no rendered object explains. Default tolerances allow antialiasing and minor rendering drift. Optional `ignore_regions` entries require `box_px` plus a concrete `reason`; one ignored region is capped at 5% and all ignored regions at 12% of the page, so they cannot hide an omitted section.

Pages with plots require `micro_annotation_inventory`. Inventory every small axis label (`t`, `%`, units, tick values), time-direction marker, and small axis arrow with `id`, `kind`, `object_ids`, and a tight `source_box_px`. Allowed kinds are `axis-label`, `time-label`, `percent-label`, `axis-arrow`, `tick-label`, and `micro-symbol`. A genuine label-free sparkline may use an empty list only with `micro_annotation_audit: {"none_found": true, "reason": "..."}`. Multi-scale reverse coverage independently reports unexplained small edge components around plots, so the audit cannot hide a visible omitted item.

Axis example:

```json
{
  "id": "distribution_axis_check",
  "kind": "axis",
  "object_ids": ["distribution_axis"],
  "source_box_px": [620, 270, 390, 25],
  "expected_start_marker": "none",
  "expected_end_marker": "triangle",
  "edge_tolerance_px": 2.5,
  "min_source_coverage": 0.88,
  "min_preview_precision": 0.84,
  "max_p95_edge_distance_px": 3.5
}
```

Allowed line-end values are `none`, `triangle`, `stealth`, `diamond`, `oval`, and `arrow`. Put the corresponding values in the referenced line shape's `start_arrow` and `end_arrow`; they are emitted as editable DrawingML line ends.

Bracket example:

```json
{
  "id": "overflow_brace_check",
  "kind": "bracket",
  "object_ids": ["overflow_brace"],
  "source_box_px": [1570, 535, 35, 90],
  "bracket_style": "curly",
  "orientation": "vertical"
}
```

Allowed bracket styles are `square`, `round`, `curly`, and `measurement`. Match them with the appropriate editable bracket/brace/parenthesis preset or a source-traced editable curve. Measurement brackets require a line with source-matched line-end markers.

Legend example:

```json
{
  "id": "legend_curve_check",
  "kind": "legend-symbol",
  "object_ids": ["legend_curve"],
  "source_box_px": [610, 840, 70, 45],
  "baseline_relation": "separated",
  "baseline_ids": ["legend_curve_axis"],
  "min_clearance_px": 2
}
```

A `legend-symbol` curve must carry the same `curve_trace.trace_points_px` and `max_chamfer_px` evidence as a data curve. `baseline_relation` is `none`, `separated`, or `touching`; declare the actual source relationship rather than imposing a universal gap.

Every visible line and curve records a non-empty `semantic_role`. Every curve records `curve_role`: `data-stroke`, `area-fill`, `legend-symbol`, or `native-structural`. Every non-`area-fill` curve, including `native-structural`, must include `curve_trace.trace_points_px`, `curve_trace.source_roi_px`, and `curve_trace.max_chamfer_px`; misclassifying a chart curve as structural therefore cannot bypass validation. The runtime checks three independent relationships: Bézier-to-trace Chamfer error, trace-point support by source-color pixels, and how much of the source-colored stroke/primary ROI the trace covers. A short but internally well-fitted wrong segment therefore fails. A `data-stroke` must additionally be open, use `fill: none`, and include `plot_area_px` and `axis_clearance_px` (`left`, `top`, `right`, `bottom`). Under-curve shading is a separate closed `area-fill` shape.

Color validation is intentionally tolerant. Raster images are checked for chroma retention and broad hue-group diversity, not exact RGB equality; normal compression, antialiasing, brightness changes, and mild hue shifts pass. The gate rejects semantic collapse such as a multicolor gauge becoming grayscale. Small state cells and bars compare their declared fill with the median source interior in CIE Lab space using default DeltaE tolerance 34 (hard maximum 45), which catches wrong red/empty counts without demanding pixel-perfect color.

Text collision validation is global and deterministic. Every `text_boxes[].box_px` participates even when no `global_layout` constraint names it. A material overlap fails validation unless one of the two text boxes explicitly lists the other's stable id in `allow_overlap_with`; use that exception only when the source intentionally superimposes the text.

Complex arrow presets (`curved*Arrow`, `bentArrow`, `circularArrow`) require `source_role: native-structural` plus `geometry_comparison_note`. Decorative or style-specific arrows must instead be source-faithfully separated as foreground assets.

Optional `global_layout` applies deterministic source-pixel constraints before build. Every participant needs a stable unique `id` and `box_px`; `locked: true` prevents movement. Supported constraints are `contain` (`parent`, `children`, `padding_px`), `align` (`items`, `edge`), `equal_size`, `equal_gap`, `flow`, `non_overlap` (`axis`, `gap_px`), and `avoid` (`items`, `obstacles`, `direction`, `gap_px`). Use `avoid` for icon rows below plots. Groups may declare `members`, `flow`, `gap_px`, and `align`. `editppt layout optimize` writes updated boxes plus `layout_optimization.movement_px` and `layout_optimization.unresolved_overlaps`; unresolved overlaps fail validation.

Vectorized foreground icons remain subject to the asset provenance contract. They must first be produced by image-edit asset-sheet separation and splitting, then traced with `editppt vectorize icon`. The authoritative SVG must contain vector paths and `vectorization.embedded_raster` must be `false`; optional EMF export requires Inkscape and may not be fabricated when unavailable.

`background_strategy` must explain at least:

- `mode`: `native-or-script`, `source-preserving-local-cleanup`, `imagegen-full-clean-base`, or similar.
- `source_consistency_contract`: which composition, perspective, object positions, colors, lighting, and key details are preserved.
- `removed_foreground`: which foreground objects were removed from the background and rebuilt later.
- `comparison_note`: the background consistency conclusion after comparing the preview against the source.

`asset_provenance` requirements — every path referenced in `images[]` must have a matching entry:

- `path`: the image path as referenced in `images[]`.
- `source`: the file the asset was produced from (for separated assets and clean bases this is typically `source.png` or the recorded asset sheet; for formulas the `.tex` file). The referenced file must exist.
- `source_type`: exactly one of `asset-sheet-separated`, `imagegen`, `latex-rendered-formula`, `user-provided`, `user-approved-rasterization`. No other value passes validation.
- `provenance_note`: a non-empty explanation of how the asset was produced.

Validation keyword-scans the free text of `visual_inventory` and `asset_provenance` entries:

- An item whose description names a foreground object (icon, photo, logo, screenshot, badge, 图标, 照片, ...) must state its separation method in its text — include a term like "asset-sheet separated" / "image edit" / "分离" — unless the text marks it as background, formula, or native structure. Matching is substring-level, so words like "benchmark" or "trademark" also trigger the foreground check ("mark"); give native structural items an explicit "native structural" / "结构" marker in their description to exempt them.
- Terms naming forbidden fallbacks — "crop", "approximation", "fallback", "emoji", "裁剪", "近似", "降级", and similar — fail validation wherever they appear in these texts, even inside negations such as "no crop". Describe what was done ("asset-sheet separated from source"), not what was avoided.

`roundRect` shapes must record `source_corner_radius_px`; they may also record `corner_reason`. If the source is a straight-corner rectangle, use `rect`.

Recommended record:

```json
{
  "type": "roundRect",
  "box_px": [64, 169, 472, 187],
  "source_corner_radius_px": 12,
  "corner_category": "small-radius",
  "corner_reason": "source card corners are lightly rounded"
}
```

Allowed `corner_category` values: `straight`, `small-radius`, `large-radius`, `pill`. `straight` should not use `roundRect`.

`latex-rendered-formula` formula assets must record:

```json
{
  "images": [
    {
      "id": "formula_c2_1",
      "path": "assets/formula_c2_1.svg",
      "box_px": [105, 392, 390, 90],
      "alt": "LaTeX rendered formula formula_c2_1",
      "z_index": 220
    }
  ],
  "asset_provenance": [
    {
      "path": "assets/formula_c2_1.svg",
      "source": "assets/formula_c2_1.tex",
      "source_type": "latex-rendered-formula",
      "provenance_note": "Rendered from LaTeX by editppt formula render-latex; visual fidelity is prioritized over formula editability."
    }
  ],
  "formula_inventory": [
    {
      "id": "formula_c2_1",
      "decision": "latex-rendered-image",
      "editable": false,
      "image": "assets/formula_c2_1.svg",
      "tex_source": "assets/formula_c2_1.tex"
    }
  ]
}
```

Formula images must be generated by `editppt formula render-latex`. Do not use source-image formula snippets, and do not assemble complex formulas from hand-written native text boxes.

## `pages/page_NNN/imagegen-jobs.json`

Owner: created by `editppt prepare`, updated by `editppt image import` and `editppt image process-sheet` (`generate`/`edit` do not write it — importing the selected output is what records the job).

Purpose: record the generation and processing process for clean bases, asset sheets, and selected bitmap assets.

Each imported job records at least the selected output and the backend that actually produced it:

```json
{
  "schema_version": 1,
  "jobs": [
    {
      "job_id": "icon-sheet",
      "role": "asset_sheet",
      "status": "recorded",
      "source_image": "/absolute/path/from/tool-output.png",
      "output": "assets/icon-sheet.png",
      "output_sha256": "...",
      "backend": "builtin-imagegen",
      "fallback_reason": null
    }
  ]
}
```

`backend` is the actual producer: `builtin-imagegen`, `codex-oauth`, or `openai-compatible-api`; `unknown` is reserved for legacy page directories that have no `image_backend` contract. `editppt image import` requires an explicit producer, rejects files that are not readable images, and checks `backend`/`fallback_reason` against the page contract. `fallback_reason` is `null` when the preferred backend succeeded or the run selected a CLI contract directly; when a built-in contract enters its CLI fallback, it records the matching event from `image_backend.fallback_policy.on`.

State and provenance record rules are described in the State Principles section of `SKILL.md` and in the asset processing examples in `cli-helper.md`.

## `notes_manifest.json`

Owner: created by `editppt prepare`, read by `editppt run finalize`.

Purpose:

- Original PPT/PPTX speaker notes.
- Notes hashes.
- Page mapping.

Notes are not handed to page workers, translated, summarized, or rewritten.
