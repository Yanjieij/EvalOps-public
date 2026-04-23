"""Persist and load Run objects as JSON.

One file per run, suitable for `evalops report <file>`, checking into
`runs/` for regression comparison, and uploading as a CI artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from evalops.models import Run


def write_run(run: Run, path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return out


def read_run(path: Path | str) -> Run:
    data = json.loads(Path(path).read_text())
    return Run.model_validate(data)
