# Local Workflow Repair Report

## Scope

This repair targets reusable workflow behavior. It does not regenerate final
videos and does not depend on the unstable cloud sandbox.

## Changes

- Quality checks use `render_subtitles.srt` when it exists, matching the file
  rendered by final composition.
- `body_sync_diff` fails when its absolute value exceeds 0.2 seconds.
- Quality reports record the actual subtitle source instead of a hard-coded
  value.
- Critical runtime JSON files use atomic UTF-8 writes and are parsed before
  replacing the previous file.
- Material audit defaults to the full supplied manifest. Historical run-only
  auditing requires explicit `--scope used`.
- Material audit accepts the runtime manifest through `--manifest` or
  `ASSET_MANIFEST_PATH`.
- Product-related assets remain unverified unless they are explicitly marked
  `manually_confirmed=true` in `assets/product_4070_safe_whitelist.json`.
- Editing watermarks such as CapCut, Jianying, InShot, VivaVideo, Kuaiying and
  Bcut are rejected.
- Original script verification compares exact bytes and the SHA-256 recorded
  in `input_meta.json`, so punctuation, quotes and line breaks are protected.

## Validation

- Modified Python files compile successfully.
- Delivery validation result: 101 valid JSON files, 0 invalid.
- Existing 416 JSON files in the complete exported project parse successfully.

## Material Audit Usage

Full-library audit:

```bash
python scripts/material_quality_audit.py --manifest /path/to/materials.csv
```

Historical used-assets-only audit:

```bash
python scripts/material_quality_audit.py \
  --scope used \
  --manifest /path/to/materials.csv
```

The exported project does not contain the runtime material CSV. Supply the
original material list when running the full audit.
