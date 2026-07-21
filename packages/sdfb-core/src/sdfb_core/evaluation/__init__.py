"""Post-write fidelity/privacy evaluation (WS3).

Pure-Python: sampling plans, reservoir sampling, Tier 1-3 metrics, and the
memorization gate. Beam wiring lives in ``sdfb_beam.dofns.evaluation``.

REF: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from sdfb_core.evaluation.gate import (
    MemorizationThresholdExceeded,
    evaluate_memorization_gate,
    raise_if_blocker,
)
from sdfb_core.evaluation.profile import (
    StratificationPlan,
    choose_stratification_column,
    stratum_key,
)
from sdfb_core.evaluation.sampling import (
    ReservoirAccumulator,
    add_row,
    extract_sample,
    merge_accumulators,
    per_stratum_cap,
    sort_key,
)

__all__ = [
    "MemorizationThresholdExceeded",
    "ReservoirAccumulator",
    "StratificationPlan",
    "add_row",
    "choose_stratification_column",
    "evaluate_memorization_gate",
    "extract_sample",
    "merge_accumulators",
    "per_stratum_cap",
    "raise_if_blocker",
    "sort_key",
    "stratum_key",
]
