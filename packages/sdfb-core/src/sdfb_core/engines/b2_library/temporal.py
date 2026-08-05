"""Temporal profiling + novel-range sampling for B.2 (WS1, spec §3a).

High-cardinality DATE/TIME/TIMESTAMP columns (and date-shaped STRING
columns) must never be resampled verbatim from the observed value table —
the 2026-07-20 E2E run landed 8 such columns at copy_ratio=1.0. Instead
the profile records the observed [min, max] as epoch floats and the
backend samples uniformly within it, rendering back to the column's
native value type / observed string format. Mirrors B.1's temporal
novel-range behavior (b1_rag/profile.py `_profile_temporal`).

Pure stdlib + the shared text_shapes detector. No Beam, no GCP, no torch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, cast

from sdfb_core.engines.text_shapes import detect_temporal_format

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    import numpy as np

# ColumnProfile.temporal_value_type values. Epoch units per type:
# seconds since 1970 (datetimes and rendered strings), proleptic ordinal
# days (dates), seconds-of-day (times). from_epoch mirrors to_epoch, so
# the unit never leaks outside this module.
VT_DATETIME = "datetime"          # tz-naive datetime objects
VT_DATETIME_UTC = "datetime_utc"  # tz-aware datetime objects (rendered UTC)
VT_DATE = "date"                  # datetime.date objects
VT_TIME = "time"                  # datetime.time objects
VT_STR = "str"                    # strings in one strftime format

_EPOCH_NAIVE = datetime(1970, 1, 1)
_MAX_SECONDS_OF_DAY = 86_399.999_999


def classify_temporal_values(values: Sequence[object]) -> tuple[str, str | None] | None:  # noqa: PLR0911 — type classifier; sequential returns read clearer than nesting
    """``(value_type, strftime_format)`` when EVERY value is uniformly
    temporal, else ``None`` (mixed types/formats stay on their existing
    route — same all-or-nothing contract as ``detect_temporal_format``)."""
    if not values:
        return None
    first = values[0]
    if isinstance(first, datetime):  # before date: datetime IS a date
        dts = [v for v in values if isinstance(v, datetime)]
        if len(dts) != len(values):
            return None
        aware = first.tzinfo is not None
        if any((v.tzinfo is not None) != aware for v in dts):
            return None
        return (VT_DATETIME_UTC if aware else VT_DATETIME, None)
    if isinstance(first, date):
        if not all(isinstance(v, date) and not isinstance(v, datetime) for v in values):
            return None
        return (VT_DATE, None)
    if isinstance(first, time):
        if not all(isinstance(v, time) for v in values):
            return None
        return (VT_TIME, None)
    if isinstance(first, str):
        strs = [v for v in values if isinstance(v, str)]
        if len(strs) != len(values):
            return None
        fmt = detect_temporal_format(strs)
        return (VT_STR, fmt) if fmt else None
    return None


def to_epoch(value: object, value_type: str, fmt: str | None) -> float:
    # casts, not isinstance: value_type is the classifier's verdict on the
    # whole column — by contract it names the runtime type, and VT_STR
    # always carries a format.
    if value_type == VT_DATETIME_UTC:
        return cast("datetime", value).timestamp()
    if value_type == VT_DATETIME:
        return (cast("datetime", value) - _EPOCH_NAIVE).total_seconds()
    if value_type == VT_DATE:
        return float(cast("date", value).toordinal())
    if value_type == VT_TIME:
        t = cast("time", value)
        return (
            t.hour * 3600 + t.minute * 60 + t.second
            + t.microsecond / 1e6
        )
    return (
        datetime.strptime(cast("str", value), cast("str", fmt)) - _EPOCH_NAIVE
    ).total_seconds()


def from_epoch(x: float, value_type: str, fmt: str | None) -> object:
    if value_type == VT_DATETIME_UTC:
        return datetime.fromtimestamp(x, tz=UTC)
    if value_type == VT_DATETIME:
        return _EPOCH_NAIVE + timedelta(seconds=x)
    if value_type == VT_DATE:
        return date.fromordinal(round(x))
    if value_type == VT_TIME:
        s = max(0.0, min(x, _MAX_SECONDS_OF_DAY))
        whole = int(s)
        return time(whole // 3600, (whole % 3600) // 60, whole % 60,
                    round((s - whole) * 1e6))
    return (_EPOCH_NAIVE + timedelta(seconds=x)).strftime(cast("str", fmt))


def value_year(value: object, value_type: str, fmt: str | None) -> int | None:
    """The calendar year of a temporal value, or None where years don't
    apply (VT_TIME). Used by the profiler's sentinel-year split."""
    if value_type == VT_TIME:
        return None
    if value_type == VT_STR:
        return datetime.strptime(cast("str", value), cast("str", fmt)).year
    return cast("date", value).year


def age_floor_epoch(
    value_type: str, fmt: str | None, max_age_years: int
) -> float | None:
    """The epoch (on this value type's axis) of `max_age_years` before now,
    or None where age doesn't apply (VT_TIME).

    Used by the profiler to clamp a TEMPORAL column's jitter floor —
    profile-time `now` is deliberate: the O(1) fit runs once per worker,
    and per-run drift of the floor is within the interim policy's
    tolerance.
    """
    if value_type == VT_TIME:
        return None
    cutoff = datetime.now(UTC) - timedelta(days=round(max_age_years * 365.25))
    if value_type == VT_DATETIME_UTC:
        return cutoff.timestamp()
    if value_type == VT_DATETIME:
        return to_epoch(cutoff.replace(tzinfo=None), VT_DATETIME, None)
    if value_type == VT_DATE:
        return float(cutoff.date().toordinal())
    return to_epoch(cutoff.strftime(cast("str", fmt)), VT_STR, fmt)


def sample_temporal(
    minimum: float | None,
    maximum: float | None,
    value_type: str,
    fmt: str | None,
    n: int,
    rng: np.random.Generator,
) -> list:
    """``n`` novel values uniformly within the observed epoch bounds."""
    if minimum is None or maximum is None:
        return [None] * n
    if maximum <= minimum:
        return [from_epoch(minimum, value_type, fmt)] * n
    draws = rng.uniform(minimum, maximum, size=n)
    return [from_epoch(float(x), value_type, fmt) for x in draws]
