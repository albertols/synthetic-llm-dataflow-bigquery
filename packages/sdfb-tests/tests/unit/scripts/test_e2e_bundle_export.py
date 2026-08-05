"""Unit tests for `scripts/e2e/e2e_bundle_export.py` (the vendored e2e bundle exporter).

Loaded via importlib the same way `test_deployment_prerequisites.py` loads
`scripts/deployment_prerequisites.py` (see that file's docstring/idiom).

Covers Task 10's two additive changes:
  1. `_leak_scan` scans `oss_dir` recursively (files in subdirectories must be
     checked, not just the top-level dir).
  2. `_collect_identifiers` also maps each Dataflow job's
     `environment.worker_image` to `WORKER_IMAGE`, so a full container image
     URI (registry/project/repo/image:tag) never survives into `oss/`.

Plus the per-job bundle layout:
  3. The bundle folder is `<out-root>/<job_id>/` and Dataflow job ids + job
     names are KEPT verbatim in `oss/` (not redacted).
  4. `--csv engine_label=path` exports sample CSVs — verbatim into `real/`,
     column-name/value-redacted into `oss/`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "e2e_bundle_export.py"
_spec = importlib.util.spec_from_file_location("e2e_bundle_export", _SCRIPT)
bundle_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bundle_module
_spec.loader.exec_module(bundle_module)

_PROJECT_ID = "my-real-gcp-project"
_WORKER_IMAGE = "europe-docker.pkg.dev/proj/repo/img:tag"
_JOB_ID = "2026-07-06_01_23_45-1234567890"
_JOB_NAME = "synthetic-e2e-run"


def _write_metrics(tmp_path: Path) -> tuple[Path, Path, Path]:
    gcp = {
        "project": _PROJECT_ID,
        "bigquery": {
            "source_fqn": f"{_PROJECT_ID}.raw_data.source_table",
            "landing_fqn": f"{_PROJECT_ID}.landing_data.landing_table",
        },
        "dataflow": [
            {
                "job_id": _JOB_ID,
                "name": _JOB_NAME,
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
    assert bundle_dir.name == _JOB_ID  # bundle folder is the Dataflow job id
    oss_report = (bundle_dir / "oss" / "report.md").read_text()
    assert _PROJECT_ID not in oss_report

    mapping = json.loads((bundle_dir / "real" / "mapping.json").read_text())
    assert mapping["identifiers"][_WORKER_IMAGE] == "WORKER_IMAGE"


def test_job_id_and_name_kept_verbatim_in_oss(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    out_root = tmp_path / "integration_test"

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

    oss_dir = out_root / _JOB_ID / "oss"
    oss_gcp = json.loads((oss_dir / "gcp_metrics.json").read_text())
    assert oss_gcp["dataflow"][0]["job_id"] == _JOB_ID
    assert oss_gcp["dataflow"][0]["name"] == _JOB_NAME

    mapping = json.loads(
        (out_root / _JOB_ID / "real" / "mapping.json").read_text()
    )
    assert _JOB_ID not in mapping["identifiers"]
    assert _JOB_NAME not in mapping["identifiers"]


def test_csv_verbatim_in_real_and_redacted_in_oss(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    out_root = tmp_path / "integration_test"
    csv_path = tmp_path / "b1_rag_sample.csv"
    csv_text = "customer_name,amount\nAlice Smith,42\n"
    csv_path.write_text(csv_text)

    rc = bundle_module.main(
        [
            "--metrics",
            f"gcp={gcp_path}",
            "--metrics",
            f"offline={offline_path}",
            "--csv",
            f"b1_rag={csv_path}",
            "--report",
            str(report_path),
            "--out-root",
            str(out_root),
        ]
    )
    assert rc == 0

    bundle_dir = out_root / _JOB_ID
    assert (bundle_dir / "real" / "b1_rag_sample.csv").read_text() == csv_text

    oss_csv = (bundle_dir / "oss" / "b1_rag_sample.csv").read_text()
    header, row = oss_csv.strip().splitlines()
    assert "customer_name" not in header  # column names always hidden
    assert "Alice Smith" not in row  # values redacted by default
    assert row.endswith(",42")  # bare numbers preserved


def test_csv_values_kept_with_no_redact_values(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    out_root = tmp_path / "integration_test"
    csv_path = tmp_path / "b2_library_sample.csv"
    csv_path.write_text("customer_name,amount\nAlice Smith,42\n")

    rc = bundle_module.main(
        [
            "--metrics",
            f"gcp={gcp_path}",
            "--metrics",
            f"offline={offline_path}",
            "--csv",
            f"b2_library={csv_path}",
            "--report",
            str(report_path),
            "--out-root",
            str(out_root),
            "--no-redact-values",
        ]
    )
    assert rc == 0

    oss_csv = (out_root / _JOB_ID / "oss" / "b2_library_sample.csv").read_text()
    header, row = oss_csv.strip().splitlines()
    assert "customer_name" not in header  # metadata hidden even with the flag
    assert row == "Alice Smith,42"  # data values kept


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
