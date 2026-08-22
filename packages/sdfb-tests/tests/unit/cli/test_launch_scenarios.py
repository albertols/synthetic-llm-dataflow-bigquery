"""The three launch scenarios (ADR 0029 rev B) — minimal inputs.

Users give landing_table (one FQN or a CSV list) + the flag; everything
else — fk_parent_landing, ordering, sibling source tables — derives.
"""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import (
    derive_fk_parent_landing,
    derive_source_fqn,
    parse_landing_tables,
    plan_launch,
)
from sdfb_core.contracts.relational import ForeignKey, RelationalContract

_SRC = "proj.src_ds.A_TABLE"
_LAND = "proj.synthetic_data"


def _fk(cols, ref, ref_cols, info=False):
    return ForeignKey(cols=tuple(cols), ref=ref, ref_cols=tuple(ref_cols),
                      informational=info)


def _contracts():
    return {
        f"{_LAND}.A_TABLE": RelationalContract(sdfb=1, pk=("A_COL_001",)),
        f"{_LAND}.B_TABLE": RelationalContract(
            sdfb=1,
            fk=(_fk(["B_COL_006"], "synthetic_data.A_TABLE", ["A_COL_001"]),),
        ),
        f"{_LAND}.C_TABLE": RelationalContract(
            sdfb=1,
            fk=(_fk(["PK_X"], "synthetic_data.A_TABLE", ["PK_X"], info=True),),
        ),
        f"{_LAND}.LONER": None,
    }


class TestDerivations:
    def test_fk_parent_landing_is_the_landing_dataset(self):
        assert derive_fk_parent_landing(
            "proj.synthetic_data.B_TABLE"
        ) == "proj.synthetic_data"

    def test_sibling_source_shares_the_reference_dataset(self):
        assert derive_source_fqn(
            f"{_LAND}.B_TABLE", _SRC
        ) == "proj.src_ds.B_TABLE"

    def test_landing_tables_csv(self):
        assert parse_landing_tables(" a.b.t1, a.b.t2 ") == [
            "a.b.t1", "a.b.t2"
        ]


class TestScenario1SingleIsolated:
    def test_single_table_flag_false_plans_itself_only(self):
        plan = plan_launch(
            [f"{_LAND}.B_TABLE"], _SRC, False, _contracts(), "r1"
        )
        assert plan.scenario == "isolated"
        (run,) = plan.runs
        assert run.landing_table == f"{_LAND}.B_TABLE"
        assert run.source_table == "proj.src_ds.B_TABLE"
        assert run.fk_parent_landing == ""
        assert run.run_id == "r1"  # single run: untouched (goldens)
        # the ignored enforced edge is called out
        assert any("B_TABLE" in w for w in plan.warnings)


class TestScenario2RelationalClosure:
    def test_related_table_expands_to_the_component(self):
        plan = plan_launch(
            [f"{_LAND}.B_TABLE"], _SRC, True, _contracts(), "r1"
        )
        assert plan.scenario == "relational_closure"
        order = [r.landing_table for r in plan.runs]
        # parents-first: A before B; C grouped in via its dashed edge
        assert order.index(f"{_LAND}.A_TABLE") < order.index(
            f"{_LAND}.B_TABLE"
        )
        assert f"{_LAND}.C_TABLE" in order
        assert f"{_LAND}.LONER" not in order

    def test_only_enforced_children_get_parent_landing(self):
        plan = plan_launch(
            [f"{_LAND}.B_TABLE"], _SRC, True, _contracts(), "r1"
        )
        by_table = {r.landing_table: r for r in plan.runs}
        assert by_table[f"{_LAND}.B_TABLE"].fk_parent_landing == _LAND
        assert by_table[f"{_LAND}.A_TABLE"].fk_parent_landing == ""
        assert by_table[f"{_LAND}.C_TABLE"].fk_parent_landing == ""

    def test_multi_run_ids_are_suffixed(self):
        plan = plan_launch(
            [f"{_LAND}.B_TABLE"], _SRC, True, _contracts(), "r1"
        )
        assert [r.run_id for r in plan.runs] == [
            f"r1-{i:02d}-{r.landing_table.rsplit('.', 1)[-1]}"
            for i, r in enumerate(plan.runs)
        ]

    def test_unrelated_single_table_stays_single(self):
        plan = plan_launch(
            [f"{_LAND}.LONER"], _SRC, True, _contracts(), "r1"
        )
        assert plan.scenario == "isolated"
        (run,) = plan.runs
        assert run.run_id == "r1"
        assert plan.warnings == ()


class TestScenario3MultiIsolated:
    def test_many_tables_flag_false_run_in_given_order(self):
        plan = plan_launch(
            [f"{_LAND}.LONER", f"{_LAND}.A_TABLE"], _SRC, False,
            _contracts(), "r1",
        )
        assert plan.scenario == "multi_isolated"
        assert [r.landing_table for r in plan.runs] == [
            f"{_LAND}.LONER", f"{_LAND}.A_TABLE"
        ]
        assert all(r.fk_parent_landing == "" for r in plan.runs)
        assert [r.run_id for r in plan.runs] == [
            "r1-00-LONER", "r1-01-A_TABLE"
        ]

    def test_many_tables_flag_true_takes_component_union(self):
        plan = plan_launch(
            [f"{_LAND}.LONER", f"{_LAND}.B_TABLE"], _SRC, True,
            _contracts(), "r1",
        )
        order = [r.landing_table for r in plan.runs]
        assert f"{_LAND}.LONER" in order
        assert order.index(f"{_LAND}.A_TABLE") < order.index(
            f"{_LAND}.B_TABLE"
        )
        assert len(order) == len(set(order))  # deduped


class TestMainPlansAndLoops:
    def test_scenario2_runs_component_in_order(self, tmp_path, monkeypatch):
        import json as _json

        from sdfb_beam.cli import run_pipeline as rp

        contracts = {
            f"{_LAND}.A_TABLE": {"sdfb": 1, "pk": ["A_COL_001"]},
            f"{_LAND}.B_TABLE": {
                "sdfb": 1,
                "fk": [{"cols": ["B_COL_006"],
                        "ref": "synthetic_data.A_TABLE",
                        "ref_cols": ["A_COL_001"]}],
            },
        }
        cj = tmp_path / "contracts.json"
        cj.write_text(_json.dumps(contracts))
        seen = []
        monkeypatch.setattr(
            rp, "_run_one_table",
            lambda a, beam_argv: (seen.append(
                (a.landing_table, a.reference_table, a.run_id,
                 a.fk_parent_landing)
            ) or 0),
        )
        rc = rp.main([
            "--reference_table=proj.src_ds.B_TABLE",
            f"--landing_table={_LAND}.B_TABLE",
            "--dlq_table=proj.q.dlq",
            "--num_rows=10",
            "--model_uri=gs://m/x",
            "--run_id=r9",
            "--multi_table_mode=sequential_jobs",
            f"--fk_contracts_json={cj}",
        ])
        assert rc == 0
        assert [t for t, *_ in seen] == [
            f"{_LAND}.A_TABLE", f"{_LAND}.B_TABLE"
        ]
        a_run, b_run = seen
        assert a_run[1] == "proj.src_ds.A_TABLE"  # sibling source derived
        assert a_run[3] == ""                      # root: no parent landing
        assert b_run[3] == _LAND                   # child: derived
        assert a_run[2] == "r9-00-A_TABLE" and b_run[2] == "r9-01-B_TABLE"

    def test_failure_aborts_remaining_tables(self, tmp_path, monkeypatch):
        import json as _json

        from sdfb_beam.cli import run_pipeline as rp

        contracts = {
            f"{_LAND}.A_TABLE": {"sdfb": 1, "pk": ["A_COL_001"]},
            f"{_LAND}.B_TABLE": {
                "sdfb": 1,
                "fk": [{"cols": ["B_COL_006"],
                        "ref": "synthetic_data.A_TABLE",
                        "ref_cols": ["A_COL_001"]}],
            },
        }
        cj = tmp_path / "contracts.json"
        cj.write_text(_json.dumps(contracts))
        seen = []
        monkeypatch.setattr(
            rp, "_run_one_table",
            lambda a, beam_argv: (seen.append(a.landing_table) or 7),
        )
        rc = rp.main([
            "--reference_table=proj.src_ds.B_TABLE",
            f"--landing_table={_LAND}.B_TABLE",
            "--dlq_table=proj.q.dlq",
            "--num_rows=10",
            "--model_uri=gs://m/x",
            "--run_id=r9",
            "--multi_table_mode=sequential_jobs",
            f"--fk_contracts_json={cj}",
        ])
        assert rc == 7
        assert seen == [f"{_LAND}.A_TABLE"]  # B never ran


    def test_single_job_default_routes_to_relational_runner(
        self, tmp_path, monkeypatch
    ):
        import json as _json

        from sdfb_beam.cli import run_pipeline as rp

        contracts = {
            f"{_LAND}.A_TABLE": {"sdfb": 1, "pk": ["A_COL_001"]},
            f"{_LAND}.B_TABLE": {
                "sdfb": 1,
                "fk": [{"cols": ["B_COL_006"],
                        "ref": "synthetic_data.A_TABLE",
                        "ref_cols": ["A_COL_001"]}],
            },
        }
        cj = tmp_path / "contracts.json"
        cj.write_text(_json.dumps(contracts))
        captured = {}
        monkeypatch.setattr(
            rp, "_run_relational_job",
            lambda plan, a, beam_argv: (
                captured.update(plan=plan) or 0
            ),
        )
        rc = rp.main([
            "--reference_table=proj.src_ds.B_TABLE",
            f"--landing_table={_LAND}.B_TABLE",
            "--dlq_table=proj.q.dlq",
            "--num_rows=10",
            "--model_uri=gs://m/x",
            "--run_id=r9",
            f"--fk_contracts_json={cj}",
        ])
        assert rc == 0
        assert [r.landing_table for r in captured["plan"].runs] == [
            f"{_LAND}.A_TABLE", f"{_LAND}.B_TABLE"
        ]


    def test_single_job_prep_collects_all_failures(
        self, tmp_path, monkeypatch, caplog
    ):
        """2026-08-22 launch: table 1's P4 stop hid tables 2-4 entirely.
        Driver-side prep must keep going, surface EVERY table's report,
        then abort once with all blockers named."""
        import json as _json
        import logging as _logging

        from sdfb_beam.cli import run_pipeline as rp

        contracts = {
            f"{_LAND}.A_TABLE": {"sdfb": 1, "pk": ["A_COL_001"]},
            f"{_LAND}.B_TABLE": {
                "sdfb": 1,
                "fk": [{"cols": ["B_COL_006"],
                        "ref": "synthetic_data.A_TABLE",
                        "ref_cols": ["A_COL_001"]}],
            },
        }
        cj = tmp_path / "contracts.json"
        cj.write_text(_json.dumps(contracts))
        prepped = []

        def _fake_prep(a, client, in_set_landing=frozenset()):
            prepped.append(a.landing_table)
            if a.landing_table.endswith("A_TABLE"):
                raise SystemExit("[preflight P4] boom on A")
            raise SystemExit("[preflight P4] boom on B")

        monkeypatch.setattr(rp, "_prepare_table_spec", _fake_prep)
        monkeypatch.setattr(rp, "build_model_client", lambda *a, **k: object())
        import pytest as _pytest

        with (
            caplog.at_level(_logging.ERROR),
            _pytest.raises(SystemExit) as exc,
        ):
            rp.main([
                "--reference_table=proj.src_ds.B_TABLE",
                f"--landing_table={_LAND}.B_TABLE",
                "--dlq_table=proj.q.dlq",
                "--num_rows=10",
                "--model_uri=gs://m/x",
                "--run_id=r9",
                f"--fk_contracts_json={cj}",
            ])
        # BOTH tables were prepped despite the first failure...
        assert prepped == [f"{_LAND}.A_TABLE", f"{_LAND}.B_TABLE"]
        # ...and the single abort names both blockers.
        assert "A_TABLE" in str(exc.value) and "B_TABLE" in str(exc.value)
