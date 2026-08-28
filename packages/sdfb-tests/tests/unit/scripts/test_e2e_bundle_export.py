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
  4. `--csv engine_label=path` registers the sample CSV's header + values in
     the redaction mapping but the CSV is NOT copied into `real/` or `oss/`
     (the parent-level copy is the only one — bundles carry no triplicates).
  5. `--doc label=path.md` ingests a markdown artifact — verbatim into
     `real/<label>.md`, redacted into `oss/<label>.md`.
  6. `--prune-inputs` deletes bundle-local input files (metrics JSONs + doc
     MDs living directly in the bundle folder) after a clean leak scan, so
     `real/` holds the single canonical copy.
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


def test_csv_not_bundled_but_still_feeds_the_mapping(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    report_path.write_text(
        f"# E2E Validation Report\n\nProject: {_PROJECT_ID}\n"
        "Top customer_name literal was Alice Smith.\n"
    )
    out_root = tmp_path / "integration_test"
    csv_path = tmp_path / "b1_rag_sample.csv"
    csv_path.write_text("customer_name,amount\nAlice Smith,42\n")

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
    # The parent-level CSV is the only copy — no triplicates in the bundles.
    assert not (bundle_dir / "real" / "b1_rag_sample.csv").exists()
    assert not (bundle_dir / "oss" / "b1_rag_sample.csv").exists()

    # …but its header + cell values still feed the mapping, so the report
    # redaction quality is unchanged.
    oss_report = (bundle_dir / "oss" / "report.md").read_text()
    assert "customer_name" not in oss_report
    assert "Alice Smith" not in oss_report

    mapping = json.loads((bundle_dir / "real" / "mapping.json").read_text())
    assert "customer_name" in mapping["columns"]
    assert "Alice Smith" in mapping["values"]


def test_csv_values_kept_in_report_with_no_redact_values(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    report_path.write_text(
        f"# E2E Validation Report\n\nProject: {_PROJECT_ID}\n"
        "Top customer_name literal was Alice Smith.\n"
    )
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

    oss_dir = out_root / _JOB_ID / "oss"
    assert not (oss_dir / "b2_library_sample.csv").exists()
    oss_report = (oss_dir / "report.md").read_text()
    assert "customer_name" not in oss_report  # metadata hidden even with the flag
    assert "Alice Smith" in oss_report  # data values kept


def test_doc_verbatim_in_real_and_redacted_in_oss(tmp_path):
    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    out_root = tmp_path / "integration_test"
    doc_path = tmp_path / "stats_diff.md"
    doc_text = (
        f"# Stats diff\n\nSource: {_PROJECT_ID}.raw_data.source_table\n"
        "Everything within tolerance.\n"
    )
    doc_path.write_text(doc_text)

    rc = bundle_module.main(
        [
            "--metrics",
            f"gcp={gcp_path}",
            "--metrics",
            f"offline={offline_path}",
            "--doc",
            f"stats_diff={doc_path}",
            "--report",
            str(report_path),
            "--out-root",
            str(out_root),
        ]
    )
    assert rc == 0

    bundle_dir = out_root / _JOB_ID
    assert (bundle_dir / "real" / "stats_diff.md").read_text() == doc_text
    oss_doc = (bundle_dir / "oss" / "stats_diff.md").read_text()
    assert _PROJECT_ID not in oss_doc
    assert "within tolerance" in oss_doc  # prose survives, identifiers don't


def test_prune_inputs_removes_bundle_local_duplicates_after_clean_scan(tmp_path):
    out_root = tmp_path / "integration_test"
    job_dir = out_root / _JOB_ID
    job_dir.mkdir(parents=True)

    gcp_path, offline_path, report_path = _write_metrics(tmp_path)
    # Inputs living directly in the bundle folder — the pre-export layout.
    bundle_gcp = job_dir / "e2e_gcp_metrics.json"
    bundle_offline = job_dir / "e2e_validation_metrics.json"
    bundle_doc = job_dir / "freetext_crosscheck_report.md"
    bundle_gcp.write_text(gcp_path.read_text())
    bundle_offline.write_text(offline_path.read_text())
    bundle_doc.write_text("# Crosscheck\n\nAll shapes recalled.\n")
    csv_path = job_dir / "b1_rag_sample.csv"
    csv_path.write_text("customer_name,amount\nAlice Smith,42\n")

    rc = bundle_module.main(
        [
            "--metrics",
            f"gcp={bundle_gcp}",
            "--metrics",
            f"offline={bundle_offline}",
            "--doc",
            f"freetext_crosscheck_report={bundle_doc}",
            "--csv",
            f"b1_rag={csv_path}",
            "--report",
            str(report_path),
            "--out-root",
            str(out_root),
            "--prune-inputs",
        ]
    )
    assert rc == 0

    # Bundle-local metrics + docs are replaced by their real/ copies…
    assert not bundle_gcp.exists()
    assert not bundle_offline.exists()
    assert not bundle_doc.exists()
    assert (job_dir / "real" / "gcp_metrics.json").exists()
    assert (job_dir / "real" / "offline_metrics.json").exists()
    assert (job_dir / "real" / "freetext_crosscheck_report.md").exists()
    assert (job_dir / "oss" / "freetext_crosscheck_report.md").exists()

    # …while the sample CSV (only copy) and the report (outside the bundle)
    # are never pruned.
    assert csv_path.exists()
    assert report_path.exists()


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
