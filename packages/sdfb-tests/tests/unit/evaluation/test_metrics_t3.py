"""Tier 3 — deferred imports: clear error without the extra, skip-clean with it."""

import pandas as pd
import pytest
from sdfb_core.evaluation import metrics_t3


def _df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1.0, 2.0, 3.0]})


def test_missing_extra_raises_actionable_importerror():
    if metrics_t3.tier3_available():
        pytest.skip("eval-extra installed — the ImportError path is untestable")
    with pytest.raises(ImportError, match="eval-extra"):
        metrics_t3.syntheval_privacy(_df(), _df())
    with pytest.raises(ImportError, match="eval-extra"):
        metrics_t3.evidently_drift_report(_df(), _df(), "/tmp/x.html")


def test_syntheval_runs_when_installed():
    pytest.importorskip("syntheval")
    out = metrics_t3.syntheval_privacy(_df(), _df())
    assert isinstance(out, dict)
