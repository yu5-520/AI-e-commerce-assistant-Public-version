# Competition evaluator sample reports

This directory contains two distinct kinds of competition assets:

- `AI经营参谋_脱敏样例_第1期.xlsx` ~ `第3期.xlsx`: judge-facing sanitized XLSX reports. The Data page exposes these files for download, and evaluators upload them through the normal `/api/data/upload/*` path. They are not a Demo shortcut.
- Existing JSON samples: deterministic lightweight fixtures retained for repeatable CI and contract checks. They are no longer the primary evaluator download format.

Runtime rule:

1. XLSX files must remain parseable by `src.services.import_adapter_service.parse_upload_file`.
2. The evaluator UI may reference only files that exist under this directory.
3. Agent, RAG, task mapping, and release semantics are not changed by these sample assets.
4. Do not replace these files with production customer data.
