"""Unit tests for `scripts/e2e_bundle_export.py` (the vendored e2e bundle exporter).

Loaded via importlib the same way `test_deployment_prerequisites.py` loads
`scripts/deployment_prerequisites.py` (see that file's docstring/idiom).

Covers Task 10's two additive changes:
  1. `_leak_scan` scans `oss_dir` recursively (files in subdirectories must be
     checked, not just the top-level dir).
  2. `_collect_identifiers` also maps each Dataflow job's
     `environment.worker_image` to `WORKER_IMAGE`, so a full container image
     URI (registry/project/repo/image:tag) never survives into `oss/`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e_bundle_export.py"
_spec = importlib.util.spec_from_file_location("e2e_bundle_export", _SCRIPT)
bundle_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bundle_module
_spec.loader.exec_module(bundle_module)

_PROJECT_ID = "my-real-gcp-project"
_WORKER_IMAGE = "europe-docker.pkg.dev/proj/repo/img:tag"


def _write_metrics(tmp_path: Path) -> tuple[Path, Path, Path]:
    gcp = {
        "project": _PROJECT_ID,
        "bigquery": {
            "source_fqn": f"{_PROJECT_ID}.raw_data.source_table",
            "landing_fqn": f"{_PROJECT_ID}.landing_data.landing_table",
        },
        "dataflow": [
            {
                "job_id": "2026-07-06_01_23_45-1234567890",
                "name": "synthetic-e2e-run",
                "environment": {"worker_image": _WORKER_IMAGE},
            }
        ],
    }
    offline = {
        "primary_key": [],
        "identity_columns": [],
        "engines": {},
    }
    gcp_path = tmp_path / "gcp_metrics.json"
    offline_path = tmp_path / "offline_metrics.json"
    gcp_path.write_text(json.dumps(gcp))
    offline_path.write_text(json.dumps(offline))

    report_path = tmp_path / "end_to_end_validation_report_2026_07_06_16_26.md"
    report_path.write_text(
        f"# E2E Validation Report\n\nProject: {_PROJECT_ID}\n"
        "Everything looked fine.\n"
    )
    return gcp_path, offline_path, report_path


def test_main_redacts_project_and_maps_worker_image(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    out_root = tmp_path / "integration_tests"

    rc = bundle_module.main(
        [
            "--metrics",
            f"gcp={gcp_path}",
            "--metrics",
            f"offline={offline_path}",
            "--report",
            str(report_path),
            "--out-root",
            str(out_root),
        ]
    )
    assert rc == 0

    (bundle_dir,) = list(out_root.iterdir())
    oss_report = (bundle_dir / "oss" / "report.md").read_text()
    assert _PROJECT_ID not in oss_report

    mapping = json.loads((bundle_dir / "real" / "mapping.json").read_text())
    assert mapping["identifiers"][_WORKER_IMAGE] == "WORKER_IMAGE"


def test_leak_scan_catches_leak_in_subdirectory(tmp_path):
    oss_dir = tmp_path / "oss"
    subdir = oss_dir / "nested"
    subdir.mkdir(parents=True)
    (oss_dir / "report.md").write_text("clean, no secrets here")
    (subdir / "leftover.json").write_text(f'{{"project": "{_PROJECT_ID}"}}')

    mapping = bundle_module.Mapping()
    mapping.add_identifier(_PROJECT_ID, "PROJECT_ID")

    hits = bundle_module._leak_scan(oss_dir, mapping)
    assert ("leftover.json", _PROJECT_ID) in hits
