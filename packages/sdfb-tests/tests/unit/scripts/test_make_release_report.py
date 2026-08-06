"""Unit tests for `scripts/release/make_release_report.py` (logic half: SemVer
bump parsing, artifact discovery at git refs, metric-delta computation).

Loaded via importlib the same way `test_e2e_bundle_export.py` loads
`scripts/e2e/e2e_bundle_export.py` (see that file's docstring/idiom).

Artifact-shape grounding for `compute_deltas` (real key names, not guesses):
  * `scripts/e2e/e2e_gcp_probe.py` (`e2e_gcp_metrics.json` / "gcp"): top-level
    `dataflow: [ {job_id, timing: {execution_seconds}, job_phases:
    {dominant_stages: [{seconds, ...}]}, metrics: {custom_counters: {...}},
    engine_milestones: {durations_seconds: {"a->b": seconds}}} ]`.
  * `scripts/e2e/e2e_validation_analysis.py` (`e2e_validation_metrics.json` /
    "offline"): `engines: {label: {full_row_duplicate_ratio, columns: {col:
    {top_value_share, ...}}}}`.
  * `scripts/e2e/freetext_crosscheck.py` (`freetext_crosscheck_metrics.json` /
    "crosscheck"): `columns: {col: {diff: {shape_recall, shape_precision,
    copy_fraction}}}`.
  * `scripts/e2e/source_synthetic_stats_diff.py` (`stats_diff.json` /
    "stats_diff"): `columns: {col: {entropy_gap, decile_ks}}`.

`discover_artifact_sets` git-plumbing tests build a throwaway tree object
(`git hash-object -w --stdin` + `git mktree`) instead of relying on
pre-existing committed `integration_test/<job_id>/` folders — none are
actually committed to this repo (verified via `git log --all --diff-filter=A
--name-only` across all branches/history), only the real-run artifacts under
the gitignored `integration_tests/` (plural). Building a synthetic tree is
still a read-only operation from the branch's point of view: it writes loose
objects but touches no ref, branch, or working tree, exactly like the
suggested `git hash-object -t tree /dev/null` empty-tree trick.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "release" / "make_release_report.py"
_spec = importlib.util.spec_from_file_location("make_release_report", _SCRIPT)
rel = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rel
_spec.loader.exec_module(rel)

# Canonical empty-tree object id (`git hash-object -t tree /dev/null`).
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


# --------------------------------------------------------------------------
# git plumbing helpers (test-only; build a throwaway tree, no branch/ref touched)
# --------------------------------------------------------------------------
def _git(*args: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    ).stdout


def _make_blob(content: str) -> str:
    return _git("hash-object", "-w", "--stdin", input_text=content).strip()


def _make_tree(entries: dict[str, str]) -> str:
    """Build nested tree objects from {relative/path.json: content} and
    return the root tree sha. Pure git plumbing — no working tree, index,
    ref, or commit is touched."""
    files: dict[str, str] = {}
    subgroups: dict[str, dict[str, str]] = {}
    for path, content in entries.items():
        head, sep, rest = path.partition("/")
        if sep:
            subgroups.setdefault(head, {})[rest] = content
        else:
            files[path] = content
    lines = []
    for name, content in files.items():
        blob = _make_blob(content)
        lines.append(f"100644 blob {blob}\t{name}")
    for name, sub in subgroups.items():
        sub_sha = _make_tree(sub)
        lines.append(f"040000 tree {sub_sha}\t{name}")
    return _git("mktree", input_text="\n".join(lines) + "\n").strip()


# --------------------------------------------------------------------------
# Brief's verbatim tests
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "bump"),
    [
        ("feat(stats): add tiers", "minor"),
        ("fix(b1): pool race", "patch"),
        ("perf(vllm): batch size", "patch"),
        ("docs(adr): new decision", "patch"),
        ("chore!: drop py310", "major"),
        ("feat(api)!: new contract", "major"),
        ("not conventional at all", "patch"),
    ],
)
def test_parse_bump(title, bump):
    assert rel.parse_bump(title) == bump


def test_next_version_first_tag():
    assert rel.next_version(None, "minor") == "v0.1.0"


def test_next_version_pre_1_0_demotes_major():
    assert rel.next_version("v0.3.1", "major") == "v0.4.0"


def test_next_version_bumps():
    assert rel.next_version("v0.3.1", "minor") == "v0.4.0"
    assert rel.next_version("v0.3.1", "patch") == "v0.3.2"
    assert rel.next_version("v1.2.3", "major") == "v2.0.0"


def test_compute_deltas_marks_missing_side():
    d = rel.compute_deltas(None, {"gcp": {"jobs": []}})
    assert "base" in d["missing"]


def test_compute_deltas_diffs_wall_clock():
    base = {"gcp": {"jobs": [{"execution_seconds": 100.0}]}}
    head = {"gcp": {"jobs": [{"execution_seconds": 80.0}]}}
    d = rel.compute_deltas(base, head)
    assert d["performance"]["execution_seconds"]["delta"] == -20.0


# --------------------------------------------------------------------------
# discover_artifact_sets / latest_job — git-backed, hermetic
# --------------------------------------------------------------------------
def test_discover_artifact_sets_returns_dict_for_head():
    sets = rel.discover_artifact_sets("HEAD")
    assert isinstance(sets, dict)


def test_discover_artifact_sets_empty_tree_is_empty_dict():
    assert rel.discover_artifact_sets(_EMPTY_TREE) == {}


def test_discover_artifact_sets_groups_by_job_and_parses_json():
    tree = _make_tree(
        {
            "integration_test/2026-01-01_00_00_00-1/e2e_gcp_metrics.json": json.dumps(
                {"dataflow": [{"job_id": "abc", "timing": {"execution_seconds": 12.5}}]}
            ),
            "integration_test/2026-01-01_00_00_00-1/stats_diff.json": json.dumps(
                {"columns": {}, "table": {}, "evaluation": None}
            ),
            # A second job with a different (partial) set of files, to prove
            # grouping keys off the job_id directory, not the whole path.
            "integration_test/2026-02-02_00_00_00-2/freetext_crosscheck_metrics.json": (
                json.dumps({"meta": {}, "columns": {}})
            ),
        }
    )
    sets = rel.discover_artifact_sets(tree)
    assert set(sets.keys()) == {"2026-01-01_00_00_00-1", "2026-02-02_00_00_00-2"}

    first = sets["2026-01-01_00_00_00-1"]
    assert first["gcp"] == {"dataflow": [{"job_id": "abc", "timing": {"execution_seconds": 12.5}}]}
    assert first["stats_diff"] == {"columns": {}, "table": {}, "evaluation": None}
    assert "offline" not in first
    assert "crosscheck" not in first

    second = sets["2026-02-02_00_00_00-2"]
    assert second["crosscheck"] == {"meta": {}, "columns": {}}
    assert "gcp" not in second


def test_latest_job_picks_lexicographically_greatest_id():
    sets = {"2026-01-01_00_00_00-1": {}, "2026-02-02_00_00_00-2": {}}
    assert rel.latest_job(sets) == "2026-02-02_00_00_00-2"


def test_latest_job_empty_sets_is_none():
    assert rel.latest_job({}) is None


# --------------------------------------------------------------------------
# compute_deltas — grounded in the real artifact shapes (see module docstring)
# --------------------------------------------------------------------------
def test_compute_deltas_missing_both_sides():
    d = rel.compute_deltas(None, None)
    assert d["missing"] == ["base", "head"]


def test_compute_deltas_execution_seconds_from_real_dataflow_shape():
    # Real e2e_gcp_probe.py output nests execution_seconds under
    # dataflow[i].timing, not a flat "jobs" list — compute_deltas must read
    # both shapes defensively.
    base = {"gcp": {"dataflow": [{"timing": {"execution_seconds": 120.0}}]}}
    head = {"gcp": {"dataflow": [{"timing": {"execution_seconds": 90.0}}]}}
    d = rel.compute_deltas(base, head)
    assert d["performance"]["execution_seconds"] == {
        "base": 120.0,
        "head": 90.0,
        "delta": -30.0,
    }


def test_compute_deltas_quality_from_offline_and_crosscheck_and_stats_diff():
    base = {
        "offline": {
            "engines": {
                "b1_rag": {
                    "full_row_duplicate_ratio": 0.02,
                    "columns": {"c1": {"top_value_share": 0.3}},
                }
            }
        },
        "crosscheck": {
            "columns": {
                "notes": {
                    "diff": {
                        "shape_recall": 0.9,
                        "shape_precision": 0.8,
                        "copy_fraction": 0.01,
                    }
                }
            }
        },
        "stats_diff": {"columns": {"amount": {"entropy_gap": 0.05, "decile_ks": 0.1}}},
    }
    head = {
        "offline": {
            "engines": {
                "b1_rag": {
                    "full_row_duplicate_ratio": 0.01,
                    "columns": {"c1": {"top_value_share": 0.25}},
                }
            }
        },
        "crosscheck": {
            "columns": {
                "notes": {
                    "diff": {
                        "shape_recall": 0.95,
                        "shape_precision": 0.85,
                        "copy_fraction": 0.0,
                    }
                }
            }
        },
        "stats_diff": {"columns": {"amount": {"entropy_gap": 0.02, "decile_ks": 0.05}}},
    }
    d = rel.compute_deltas(base, head)
    assert d["quality"]["dup_ratio_max"]["delta"] == pytest.approx(-0.01)
    assert d["quality"]["top_value_share_max"] == {"base": 0.3, "head": 0.25, "delta": pytest.approx(-0.05)}
    assert d["quality"]["shape_recall_min"] == {"base": 0.9, "head": 0.95, "delta": pytest.approx(0.05)}
    assert d["quality"]["shape_precision_min"]["base"] == 0.8
    assert d["quality"]["copy_fraction_max"] == {"base": 0.01, "head": 0.0, "delta": pytest.approx(-0.01)}
    assert d["quality"]["entropy_gap_max"]["delta"] == pytest.approx(-0.03)
    assert d["quality"]["decile_ks_max"]["delta"] == pytest.approx(-0.05)


def test_compute_deltas_tokens_per_s_none_when_unmeasured():
    # Real e2e_gcp_probe.py artifacts carry no "tokens" custom counter today,
    # so tokens_per_s must stay None on both sides rather than crash.
    d = rel.compute_deltas({"gcp": {"jobs": []}}, {"gcp": {"jobs": []}})
    assert d["vllm"]["tokens_per_s"] == {"base": None, "head": None, "delta": None}


def test_compute_deltas_counters_resolve_real_namespaced_keys():
    # Real e2e_gcp_probe.py output namespaces every Beam custom counter as
    # f"{namespace}.{metric}" (_job_metrics, scripts/e2e/e2e_gcp_probe.py)
    # because the DoFns that emit them use Metrics.counter("generation",
    # "yielded") / Metrics.counter("generation", "failed")
    # (packages/sdfb-beam/src/sdfb_beam/dofns/generate.py:83-84) — never a
    # bare "yielded"/"failed" key. compute_deltas must resolve those
    # namespaced keys, not silently read them as unmeasured.
    base = {
        "gcp": {
            "dataflow": [
                {
                    "metrics": {
                        "custom_counters": {
                            "generation.yielded": 900,
                            "generation.failed": 50,
                        }
                    }
                }
            ]
        }
    }
    head = {
        "gcp": {
            "dataflow": [
                {
                    "metrics": {
                        "custom_counters": {
                            "generation.yielded": 950,
                            "generation.failed": 10,
                        }
                    }
                }
            ]
        }
    }
    d = rel.compute_deltas(base, head)
    assert d["performance"]["counter_yielded"] == {"base": 900, "head": 950, "delta": 50}
    assert d["performance"]["counter_failed"] == {"base": 50, "head": 10, "delta": -40}


def test_compute_deltas_tokens_per_s_zero_duration_is_none_not_zerodiv():
    # Minor fix: a measured-but-zero milestone duration must not be treated
    # as "unmeasured" via a falsy check, and must not raise ZeroDivisionError
    # — it's an explicit divide-by-zero guard, distinct from "no duration
    # observed at all".
    job = {
        "metrics": {"custom_counters": {"tokens": 100}},
        "engine_milestones": {
            "durations_seconds": {"vllm_engine_init->vllm_ready": 0.0}
        },
    }
    d = rel.compute_deltas({"gcp": {"jobs": [job]}}, {"gcp": {"jobs": [job]}})
    assert d["vllm"]["tokens_per_s"] == {"base": None, "head": None, "delta": None}


def test_compute_deltas_stamps_base_job_and_head_job_when_provided():
    d = rel.compute_deltas(None, None, base_job="j1", head_job="j2")
    assert d["base_job"] == "j1"
    assert d["head_job"] == "j2"


def test_compute_deltas_base_job_head_job_default_none():
    d = rel.compute_deltas({"gcp": {"jobs": []}}, {"gcp": {"jobs": []}})
    assert d["base_job"] is None
    assert d["head_job"] is None
