"""Post-write fidelity/privacy evaluation (WS3).

Pure-Python: sampling plans, reservoir sampling, Tier 1-3 metrics, and the
memorization gate. Beam wiring lives in ``sdfb_beam.dofns.evaluation``.

REF: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from sdfb_core.evaluation.profile import (
    StratificationPlan,
    choose_stratification_column,
    stratum_key,
)

__all__ = [
    "StratificationPlan",
    "choose_stratification_column",
    "stratum_key",
]
