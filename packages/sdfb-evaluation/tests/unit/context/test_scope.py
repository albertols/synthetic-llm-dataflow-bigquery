#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Which landing rows one generation job wrote (`resolve_scope`), the
source as it was when the job started (`pin_source`), and the salted
hash sample (`sampled_read`) — all pure SQL planning, no BigQuery.

One invented timeline (thelook `orders`, `demo-project`) throughout:

    13:10:16.5 job created ─ 13:49:21 ┬ COPY #1 ┬ 13:49:30.5
                                      13:49:31 ┴ COPY #2 ┴ 13:49:40.25
    scope window = [13:49:20, 13:49:41.25]   (first start - 1 s, last end + 1 s)
    now = 2026-09-14 12:00 → the 168 h time-travel floor is 2026-09-07 12:00
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sdfb_evaluation.context.jobs import JobWrite
from sdfb_evaluation.context.scope import (
    ScopePlan,
    pin_source,
    resolve_scope,
    sampled_read,
)

DS = "demo-project.thelook_synthetic"
ORDERS = f"{DS}.orders"
SOURCE = "demo-project.thelook_ecommerce.orders"
TEMP = "demo-project.sdfb_eval_tmp"
EVAL_ID = "ev_20260914_0001"
NOW = "2026-09-14T12:00:00.000000Z"
EXPIRY = ("OPTIONS(expiration_timestamp="
          "TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR))")
WINDOW = ("2026-09-13T13:49:20.000000Z", "2026-09-13T13:49:41.250000Z")
WINDOW_DT = (datetime(2026, 9, 13, 13, 49, 20, tzinfo=UTC),
             datetime(2026, 9, 13, 13, 49, 41, 250000, tzinfo=UTC))


def _write(start: str,
           end: str,
           *,
           table: str = ORDERS,
           rows: int | None = 9000,
           job_id: str = "beam_bq_job_COPY_sdfbthelook0913_0",
           job_type: str = "COPY") -> JobWrite:
  return JobWrite(
      table=table,
      job_type=job_type,
      start=start,
      end=end,
      output_rows=rows,
      job_id=job_id)


WRITES = (
    _write(
        "2026-09-13T13:49:21.000000Z",
        "2026-09-13T13:49:30.500000Z",
        rows=9000,
        job_id="beam_bq_job_COPY_sdfbthelook0913_0"),
    _write(
        "2026-09-13T13:49:31.000000Z",
        "2026-09-13T13:49:40.250000Z",
        rows=9250,
        job_id="beam_bq_job_COPY_sdfbthelook0913_1"),
)
EARLIER_LAUNCH = _write(
    "2026-09-12T09:00:00.000000Z",
    "2026-09-12T09:00:12.000000Z",
    job_id="beam_bq_job_LOAD_sdfbthelook0912_0",
    job_type="LOAD",
    rows=None)
LATER_DML = _write(
    "2026-09-14T08:00:00.000000Z",
    "2026-09-14T08:00:05.000000Z",
    job_id="bquxjob_5e1f0a2b",
    job_type="QUERY",
    rows=None)
OVERLAPPING_LOAD = _write(
    "2026-09-13T13:49:35.000000Z",
    "2026-09-13T13:49:37.000000Z",
    job_id="manual_load_77",
    job_type="LOAD",
    rows=None)


def _temp_re(side: str, short: str) -> str:
  return rf"{re.escape(TEMP)}\.sdfb_eval_{EVAL_ID}_{side}_{short}_[0-9a-f]{{8}}"


def _resolve(**overrides) -> ScopePlan:
  kwargs = {
      "landing_table": ORDERS,
      "write_disposition": "append",
      "writes": WRITES,
      "foreign": (),
      "expected_rows": 18250,
      "now": NOW,
      "time_travel_hours": 168,
      "temp_dataset": TEMP,
      "evaluation_id": EVAL_ID,
  }
  kwargs.update(overrides)
  return resolve_scope(**kwargs)


# ---------------------------------------------------------------------------
# overwrite
# ---------------------------------------------------------------------------


def test_overwrite_uses_whole_table():
  # An earlier launch's append is irrelevant: WRITE_TRUNCATE replaced it.
  plan = _resolve(write_disposition="overwrite", foreign=(EARLIER_LAUNCH,))
  assert plan.mode == "table"
  assert plan.status == "ok" and plan.ok and plan.readable
  assert plan.reason is None
  assert plan.read_table == ORDERS
  assert plan.read_expr == f"`{ORDERS}`"
  assert plan.prepare_sql == ()
  assert plan.params == {}
  assert plan.window == WINDOW
  assert plan.expected_rows == 18250


def test_overwrite_with_later_write_snapshots_as_of():
  plan = _resolve(write_disposition="overwrite", foreign=(LATER_DML,))
  assert plan.mode == "as_of"
  assert plan.status == "ok"
  assert re.fullmatch(_temp_re("syn", "orders"), plan.read_table)
  as_of = "TIMESTAMP '2026-09-13T13:49:41.250000Z'"
  assert plan.prepare_sql == (
      f"CREATE SNAPSHOT TABLE `{plan.read_table}` CLONE `{ORDERS}` "
      f"FOR SYSTEM_TIME AS OF {as_of} {EXPIRY}",)
  assert plan.read_expr == (f"(SELECT * FROM `{ORDERS}` "
                            f"FOR SYSTEM_TIME AS OF {as_of})")
  assert plan.params == {}
  assert plan.window == WINDOW


def test_overwrite_with_later_write_beyond_time_travel_is_expired():
  # The as-of point (13:49:41.25) fell out of the 168 h window a
  # microsecond ago: never silently read the (since modified) table.
  now = WINDOW_DT[1] + timedelta(hours=168, microseconds=1)
  plan = _resolve(write_disposition="overwrite", foreign=(LATER_DML,), now=now)
  assert plan.mode == "as_of"
  assert plan.status == "expired" and not plan.ok
  assert not plan.readable and plan.read_table == "" and plan.read_expr == ""
  assert plan.prepare_sql == ()
  assert "168 h" in plan.reason
  assert plan.window == WINDOW


def test_overwrite_table_contaminated_by_an_interleaved_writer():
  plan = _resolve(write_disposition="overwrite", foreign=(OVERLAPPING_LOAD,))
  assert plan.mode == "table"
  assert plan.status == "contaminated" and not plan.readable
  assert "manual_load_77" in plan.reason


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------


def test_append_within_window_uses_appends_ctas():
  plan = _resolve(foreign=(EARLIER_LAUNCH, LATER_DML))
  assert plan.mode == "appends"
  assert plan.status == "ok" and plan.reason is None
  assert re.fullmatch(_temp_re("syn", "orders"), plan.read_table)
  assert plan.prepare_sql == (
      f"CREATE TABLE `{plan.read_table}` {EXPIRY} AS SELECT * "
      f"EXCEPT(_CHANGE_TYPE, _CHANGE_TIMESTAMP) FROM APPENDS(TABLE "
      f"`{ORDERS}`, @start, @end)",)
  assert plan.read_expr == ("(SELECT * EXCEPT(_CHANGE_TYPE, _CHANGE_TIMESTAMP) "
                            f"FROM APPENDS(TABLE `{ORDERS}`, @start, @end))")
  # Bound, never formatted: no timestamp reaches the SQL text.
  assert plan.params == {"start": WINDOW_DT[0], "end": WINDOW_DT[1]}
  assert "2026-09-13" not in plan.prepare_sql[0] + plan.read_expr
  assert plan.window == WINDOW


def test_append_beyond_window_is_expired():
  # The window start (13:49:20) is one second older than the floor.
  plan = _resolve(now="2026-09-20T13:49:21.000000Z")
  assert plan.mode == "appends"
  assert plan.status == "expired" and not plan.readable
  assert plan.prepare_sql == () and plan.params == {}
  assert "168 h" in plan.reason and "2026-09-13T13:49:21" in plan.reason


def test_append_expiry_uses_the_tables_own_time_travel_window():
  plan = _resolve(now="2026-09-15T14:00:00Z", time_travel_hours=48)
  assert plan.status == "expired"
  assert "48 h" in plan.reason
  assert _resolve(now="2026-09-15T13:00:00Z", time_travel_hours=48).ok


def test_contaminated_appends_window_rejected():
  plan = _resolve(foreign=(EARLIER_LAUNCH, OVERLAPPING_LOAD, LATER_DML))
  assert plan.mode == "appends"
  assert plan.status == "contaminated" and not plan.ok
  assert not plan.readable and plan.prepare_sql == ()
  assert "manual_load_77" in plan.reason
  assert "allow_contaminated" in plan.reason
  assert "beam_bq_job_LOAD_sdfbthelook0912_0" not in plan.reason
  assert "bquxjob_5e1f0a2b" not in plan.reason


def test_contaminated_appends_window_allowed_keeps_status_and_warns():
  plan = _resolve(foreign=(OVERLAPPING_LOAD,), allow_contaminated=True)
  assert plan.status == "contaminated" and not plan.ok
  assert plan.readable
  assert plan.prepare_sql[0].startswith(f"CREATE TABLE `{plan.read_table}`")
  assert "manual_load_77" in plan.reason
  assert "allow_contaminated" in plan.reason


def test_a_writer_that_started_inside_the_window_contaminates_it():
  # Its commit (end) is after the window, but its start is inside it:
  # rows it committed may carry a change timestamp inside the window.
  straddling = _write(
      "2026-09-13T13:49:40.000000Z",
      "2026-09-13T13:49:55.000000Z",
      job_id="bquxjob_straddle",
      job_type="QUERY",
      rows=None)
  assert _resolve(foreign=(straddling,)).status == "contaminated"


def test_append_scope_count_mismatch_flags_not_ok():
  plan = _resolve()
  assert plan.ok and plan.observed_rows is None
  exact = plan.verify(18250)
  assert exact.status == "ok" and exact.observed_rows == 18250
  # 0.5 % of 18 250 is 91.25 rows.
  assert plan.verify(18250 + 91).status == "ok"
  mismatch = plan.verify(18250 + 92)
  assert mismatch.status == "count_mismatch" and not mismatch.ok
  assert mismatch.observed_rows == 18342
  assert "18342" in mismatch.reason and "18250" in mismatch.reason
  assert plan.status == "ok"  # verify returns a new plan


def test_count_check_is_exact_below_one_thousand_rows():
  plan = _resolve(expected_rows=999)
  assert plan.verify(999).ok
  assert plan.verify(998).status == "count_mismatch"
  assert plan.verify(1000).status == "count_mismatch"


def test_count_check_without_an_expected_count_says_so():
  checked = _resolve(expected_rows=None).verify(18250)
  assert checked.status == "ok" and checked.observed_rows == 18250
  assert "not verified" in checked.reason


def test_count_mismatch_keeps_a_worse_status():
  plan = _resolve(foreign=(OVERLAPPING_LOAD,), allow_contaminated=True)
  checked = plan.verify(20000)
  assert checked.status == "contaminated"
  assert "20000" in checked.reason and "manual_load_77" in checked.reason


def test_verify_refuses_a_scope_nothing_was_read_from():
  with pytest.raises(ValueError, match="nothing"):
    _resolve(now="2026-09-30T00:00:00Z").verify(0)
  with pytest.raises(ValueError):
    _resolve().verify(-1)
  with pytest.raises(ValueError):
    _resolve().verify(True)


# ---------------------------------------------------------------------------
# manual, requested modes, missing inputs
# ---------------------------------------------------------------------------


def test_manual_reads_current_table():
  plan = _resolve(requested="manual")
  assert plan.mode == "manual" and plan.ok
  assert plan.read_table == ORDERS and plan.prepare_sql == ()
  assert plan.reason is None


def test_manual_warns_when_more_than_one_writer():
  plan = _resolve(requested="manual", foreign=(OVERLAPPING_LOAD, LATER_DML))
  assert plan.mode == "manual" and plan.status == "ok"
  assert plan.read_table == ORDERS
  assert "3 writers" in plan.reason
  assert "manual_load_77" in plan.reason and "bquxjob_5e1f0a2b" in plan.reason


def test_manual_ignores_the_time_travel_window():
  plan = _resolve(requested="manual", now="2026-12-31T00:00:00Z")
  assert plan.ok and plan.read_table == ORDERS


def test_requested_mode_overrides_the_write_disposition():
  plan = _resolve(requested="appends", write_disposition="overwrite")
  assert plan.mode == "appends" and plan.ok
  assert plan.prepare_sql[0].startswith("CREATE TABLE ")
  table = _resolve(requested="table", foreign=(LATER_DML,))
  assert table.mode == "table" and table.status == "contaminated"
  assert "bquxjob_5e1f0a2b" in table.reason


def test_unknown_requested_mode_is_rejected():
  with pytest.raises(ValueError, match="snapshot"):
    _resolve(requested="snapshot")


def test_unknown_write_disposition_is_unknown_not_guessed():
  plan = _resolve(write_disposition=None)
  assert plan.status == "unknown" and not plan.readable
  assert "--scope" in plan.reason
  with pytest.raises(ValueError, match="truncate"):
    _resolve(write_disposition="truncate")


def test_no_labelled_write_on_an_overwrite_launch_reads_the_table_as_unknown():
  plan = _resolve(write_disposition="overwrite", writes=())
  assert plan.mode == "table" and plan.status == "unknown"
  assert plan.readable and plan.read_table == ORDERS
  assert plan.window == (None, None)
  assert "no labelled" in plan.reason


def test_no_labelled_write_on_an_append_launch_cannot_be_isolated():
  plan = _resolve(writes=())
  assert plan.mode == "appends" and plan.status == "unknown"
  assert not plan.readable and plan.prepare_sql == ()
  assert "--scope manual" in plan.reason


def test_empty_scope_when_the_job_wrote_no_rows():
  empty = (_write(
      "2026-09-13T13:49:21.000000Z", "2026-09-13T13:49:22.000000Z", rows=0),)
  plan = _resolve(writes=empty, expected_rows=0)
  assert plan.status == "empty" and not plan.readable
  assert _resolve(writes=empty, expected_rows=None).status == "empty"
  # Zero expected but rows committed: not empty, the count check decides.
  assert _resolve(expected_rows=0).status == "ok"


def test_writes_to_other_tables_do_not_widen_the_window():
  users = _write(
      "2026-09-13T13:40:55.100000Z",
      "2026-09-13T13:41:10.200000Z",
      table=f"{DS}.users",
      job_id="beam_bq_job_LOAD_sdfbthelook0913_9",
      job_type="LOAD")
  plan = _resolve(writes=(users, *WRITES))
  assert plan.window == WINDOW
  # A foreign write into ANOTHER table inside the window is not ours.
  other = _write(
      "2026-09-13T13:49:35.000000Z",
      "2026-09-13T13:49:37.000000Z",
      table=f"{DS}.users",
      job_id="manual_load_78",
      job_type="LOAD")
  assert _resolve(foreign=(other,)).ok


def test_landing_table_accepts_the_colon_form():
  plan = _resolve(landing_table="demo-project:thelook_synthetic.orders")
  assert plan.landing_table == ORDERS and plan.window == WINDOW


@pytest.mark.parametrize("bad", [{
    "evaluation_id": "ev 1; DROP"
}, {
    "evaluation_id": ""
}, {
    "temp_dataset": "not a dataset"
}, {
    "temp_dataset": "demo-project.tmp.extra"
}, {
    "landing_table": "orders"
}, {
    "time_travel_hours": 0
}, {
    "expected_rows": -1
}])
def test_inputs_that_would_reach_sql_are_validated(bad):
  with pytest.raises(ValueError):
    _resolve(write_disposition="overwrite", foreign=(LATER_DML,), **bad)


def test_temp_tables_differ_per_table_even_with_the_same_short_name():
  other = "demo-project.thelook_synthetic_b.orders"
  writes = tuple(
      JobWrite(
          table=other,
          job_type=w.job_type,
          start=w.start,
          end=w.end,
          output_rows=w.output_rows,
          job_id=w.job_id) for w in WRITES)
  first = _resolve()
  second = _resolve(landing_table=other, writes=writes)
  assert first.read_table != second.read_table
  assert _resolve().read_table == first.read_table  # deterministic


# ---------------------------------------------------------------------------
# pin_source
# ---------------------------------------------------------------------------


def _pin(**overrides):
  kwargs = {
      "source_table": SOURCE,
      "job_create_time": "2026-09-13T13:10:16.512345Z",
      "now": NOW,
      "time_travel_hours": 168,
      "temp_dataset": TEMP,
      "evaluation_id": EVAL_ID,
  }
  kwargs.update(overrides)
  return pin_source(**kwargs)


def test_source_pinned_as_of_job_create_time():
  pin = _pin()
  read_table, prepare_sql, pinned = pin[:3]
  assert pinned is True and pin.pinned
  assert re.fullmatch(_temp_re("src", "orders"), read_table)
  as_of = "TIMESTAMP '2026-09-13T13:10:16.512345Z'"
  assert prepare_sql == (f"CREATE SNAPSHOT TABLE `{read_table}` CLONE "
                         f"`{SOURCE}` FOR SYSTEM_TIME AS OF {as_of} {EXPIRY}",)
  assert pin.read_expr == (f"(SELECT * FROM `{SOURCE}` "
                           f"FOR SYSTEM_TIME AS OF {as_of})")
  assert pin.as_of == "2026-09-13T13:10:16.512345Z"
  assert pin.reason is None


def test_source_pin_renders_its_own_utc_literal():
  # A datetime with an offset is re-rendered in UTC; text that is not a
  # timestamp never reaches the SQL.
  pin = _pin(
      job_create_time=datetime(
          2026, 9, 13, 15, 10, 16, 512345, tzinfo=timezone(timedelta(hours=2))))
  assert "TIMESTAMP '2026-09-13T13:10:16.512345Z'" in pin.prepare_sql[0]
  with pytest.raises(ValueError):
    _pin(job_create_time="2026-09-13T13:10:16Z' OR TRUE --")


def test_source_beyond_time_travel_is_read_current_and_unpinned():
  pin = _pin(now="2026-09-25T00:00:00Z")
  assert pin[:3] == (SOURCE, (), False)
  assert pin.read_expr == f"`{SOURCE}`"
  assert pin.as_of is None
  assert "168 h" in pin.reason


def test_source_with_unknown_create_time_is_unpinned():
  pin = _pin(job_create_time=None)
  assert pin[:3] == (SOURCE, (), False)
  assert "create time" in pin.reason


def test_source_create_time_after_now_is_rejected():
  with pytest.raises(ValueError, match="after"):
    _pin(job_create_time="2026-09-15T00:00:00Z")


# ---------------------------------------------------------------------------
# sampled_read
# ---------------------------------------------------------------------------


def _bq_abs_mod(value: int, modulo: int) -> int:
  """BigQuery's `ABS(MOD(x, m))`: MOD takes the sign of `x`."""
  rest = abs(value) % modulo
  return abs(rest if value >= 0 else -rest)


def test_sampled_read_deterministic():
  kwargs = {
      "keep": 37,
      "modulo": 1000,
      "salt": "c2FsdA",
      "temp_dataset": TEMP,
      "evaluation_id": EVAL_ID,
      "side": "syn",
  }
  sql, temp = sampled_read(ORDERS, **kwargs)
  assert (sql, temp) == sampled_read(ORDERS, **kwargs)
  assert re.fullmatch(_temp_re("syn", "orders"), temp)
  assert temp != _resolve().read_table  # never the scope's own temp table
  assert sql == (f"CREATE TABLE `{temp}` {EXPIRY} AS SELECT * FROM "
                 f"`{ORDERS}` AS t WHERE ABS(MOD(FARM_FINGERPRINT("
                 "CONCAT(@salt, TO_JSON_STRING(t))), 1000)) < 37")
  # The salt is bound (@salt), so the text does not depend on it.
  assert "c2FsdA" not in sql
  assert sampled_read(ORDERS, **{**kwargs, "salt": "other"}) == (sql, temp)
  # ABS(MOD(x, m)) keeps exactly the rows MOD(ABS(x), m) would, and cannot
  # overflow on the one INT64 whose absolute value does not exist.
  for value in (-(2**63) + 1, -1001, -37, -1, 0, 36, 37, 999, 2**63 - 1):
    assert _bq_abs_mod(value, 1000) == abs(value) % 1000
  assert _bq_abs_mod(-(2**63), 1000) < 1000


@pytest.mark.parametrize("bad", [{
    "keep": 1001
}, {
    "keep": -1
}, {
    "modulo": 0
}, {
    "keep": True
}, {
    "modulo": 10.0
}, {
    "salt": ""
}, {
    "side": "syn; DROP"
}, {
    "read_table": "orders"
}])
def test_sampled_read_validates_everything_it_formats(bad):
  kwargs = {
      "read_table": ORDERS,
      "keep": 37,
      "modulo": 1000,
      "salt": "c2FsdA",
      "temp_dataset": TEMP,
      "evaluation_id": EVAL_ID,
      "side": "syn",
  }
  kwargs.update(bad)
  read_table = kwargs.pop("read_table")
  with pytest.raises(ValueError):
    sampled_read(read_table, **kwargs)
