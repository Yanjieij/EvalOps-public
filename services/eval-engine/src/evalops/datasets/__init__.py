"""Dataset loader — reads a benchmark directory off disk.

A benchmark on disk has this layout:

    datasets/<name>/
        benchmark.yaml         # Benchmark metadata
        cases/*.yaml           # one file per case (or a single cases.yaml list)

Small datasets can ship as a single YAML; larger ones naturally split into
per-case files for easy diffs and cherry-picking.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from evalops.models import Benchmark, CapabilityTag, Case, CaseKind


def load_benchmark(path: Path | str) -> tuple[Benchmark, list[Case]]:
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"benchmark path does not exist: {root}")

    meta_path = root / "benchmark.yaml"
    if not meta_path.exists():
        raise FileNotFoundError(f"missing benchmark.yaml in {root}")
    meta = yaml.safe_load(meta_path.read_text()) or {}
    benchmark = Benchmark(**meta)

    cases_path_single = root / "cases.yaml"
    cases_dir = root / "cases"

    raw_cases: list[dict] = []
    if cases_path_single.exists():
        data = yaml.safe_load(cases_path_single.read_text()) or []
        if not isinstance(data, list):
            raise ValueError(f"{cases_path_single} must be a YAML list of cases")
        raw_cases.extend(data)
    elif cases_dir.exists():
        for p in sorted(cases_dir.glob("*.yaml")):
            data = yaml.safe_load(p.read_text())
            if isinstance(data, list):
                raw_cases.extend(data)
            elif isinstance(data, dict):
                raw_cases.append(data)
    else:
        raise FileNotFoundError(f"no cases.yaml or cases/ directory in {root}")

    cases: list[Case] = []
    for raw in raw_cases:
        if "kind" in raw and isinstance(raw["kind"], str):
            raw["kind"] = CaseKind(raw["kind"])
        tags_raw = raw.get("capability_tags") or []
        raw["capability_tags"] = [
            CapabilityTag(**t) if isinstance(t, dict) else CapabilityTag(path=str(t))
            for t in tags_raw
        ]
        raw.setdefault("benchmark_id", benchmark.id)
        cases.append(Case(**raw))
    return benchmark, cases


__all__ = ["load_benchmark"]
