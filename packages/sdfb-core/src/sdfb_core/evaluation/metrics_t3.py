"""Tier-3 evaluation — SynthEval + Evidently, behind sdfb-beam[eval-extra].

Deferred imports inside each function body: absence of the extra never
breaks a Tier 1/2 run. The EvaluationDoFn records tier3 as not_installed
rather than calling these; they exist for opt-in offline use and for the
future --eval_tier knob (design §7)."""

from __future__ import annotations

import pandas as pd

_HINT = (
    "Tier-3 evaluation needs the eval-extra extras: "
    "uv sync --group dev --package sdfb-beam --extra eval-extra"
)


def tier3_available() -> bool:
    try:
        import evidently  # noqa: F401
        import syntheval  # noqa: F401
    except ImportError:
        return False
    return True


def syntheval_privacy(real_df: pd.DataFrame, synth_df: pd.DataFrame) -> dict:
    """SynthEval's native exact-Gower DCR/NNDR — the slow second opinion."""
    try:
        from syntheval import SynthEval
    except ImportError as e:
        raise ImportError(_HINT) from e
    evaluator = SynthEval(real_df)
    results = evaluator.evaluate(synth_df, analysis_target_var=None, **{"dcr": {}, "nndr": {}})
    return {"syntheval": str(results)} if results is not None else {}


def evidently_drift_report(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, out_path: str
) -> str:
    """Durable HTML artifact (a FILE, never a dashboard). Returns out_path."""
    try:
        from evidently import Report
        from evidently.presets import DataDriftPreset
    except ImportError as e:
        raise ImportError(_HINT) from e
    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(reference_data=real_df, current_data=synth_df)
    snapshot.save_html(out_path)
    return out_path
