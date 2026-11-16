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

# /// script
# requires-python = "==3.14.*"
# dependencies = ["sdgx==0.2.4", "psutil"]
#
# [tool.uv]
# # sdgx 0.2.4 pins numpy<2. The overrides replace its requirements with the
# # workspace lock's versions, and drop torch where it ships no wheel (an
# # Intel Mac): there the training stages report SKIP.
# override-dependencies = [
#   "numpy==2.3.5",
#   "pandas==3.0.3",
#   "pyarrow==23.0.1",
#   "scipy==1.17.1",
#   "scikit-learn==1.8.0",
#   "torch==2.11.0 ; sys_platform == 'linux' or platform_machine == 'arm64'",
# ]
# ///
"""Probe: does sdgx's CTGAN path run beside the workspace's Python 3.14 pins?

    uv run --script scripts/doc/probe_sdgx_compat.py

Self-contained: `uv` builds a throwaway environment from the inline block
above (sdgx 0.2.4 with its `numpy<2` pin overridden by the workspace lock's
numpy, pandas, pyarrow, scipy and scikit-learn), so it never touches the
workspace's own environment. It prints one markdown table: the evidence
table of docs/designs/2026-11-16-b2-library-backend-assessment.md.

Each stage is one step of what `SdgxBackend._fit_sdgx`
(`sdfb_core/engines/b2_library/backends.py`) asks of sdgx, on a seeded toy
frame (no source data). The last two stages are that method's call sequence
verbatim; they need torch, so they report SKIP on a machine with no torch
wheel for Python 3.14 and run on the M4 or a Linux runner.

`expected` is what the 2026-11-16 run on this repository's laptop observed
(`?` = not run there). A row whose outcome differs from it is news: sdgx,
pandas or numpy moved, and the assessment needs a second look.
"""

# The probed library is imported by name, stage by stage, so that a failed
# import is one row of the table and not the end of the run.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import os
import platform
import sys
import tempfile
import warnings

PROBE_SEED = 7
ROWS = 3_000
# Share of rows whose nullable columns are null in the toy frame.
NULL_SHARE_NUMERIC = 0.2
NULL_SHARE_CATEGORICAL = 0.3
# Enough epochs to exercise the training loop, not to fit anything.
FIT_EPOCHS = 2
SAMPLE_ROWS = 500

_CONNECTOR = "sdgx.data_connectors.dataframe_connector"
_LOADER = "sdgx.data_loader"
_METADATA = "sdgx.data_models.metadata"
_PROCESSORS = "sdgx.data_processors.manager"
_TRANSFORMER = "sdgx.models.components.optimize.sdv_ctgan.data_transformer"
_CTGAN = "sdgx.models.ml.single_table.ctgan"
_SYNTHESIZER = "sdgx.synthesizer"
# What sdgx writes where a non-numeric column was null.
_NULL_MARKER = "NAN_VALUE"


def _attr(module: str, name: str):
  return getattr(importlib.import_module(module), name)


def _toy_frame():
  """Numeric, integer, categorical, constant and nullable columns."""
  import numpy as np
  import pandas as pd

  rng = np.random.default_rng(PROBE_SEED)
  null_num = rng.random(ROWS) < NULL_SHARE_NUMERIC
  null_cat = rng.random(ROWS) < NULL_SHARE_CATEGORICAL
  picks = rng.choice(["p", "q"], ROWS)
  return pd.DataFrame({
      "amount":
          rng.lognormal(3, 1, ROWS),
      "count":
          rng.integers(0, 50, ROWS),
      "segment":
          rng.choice(["A", "B", "C", "D"], ROWS, p=[0.6, 0.25, 0.1, 0.05]),
      "flag":
          rng.choice(["Y", "N"], ROWS),
      "const": ["X"] * ROWS,
      "nullable_num":
          np.where(null_num, np.nan, rng.normal(0, 1, ROWS)),
      "nullable_cat": [
          None if n else v for n, v in zip(null_cat, picks, strict=True)
      ],
  })


def _strings_as_object(frame):
  """pandas 3 infers `str` for string columns; sdgx's CTGAN needs `object`."""
  return frame.astype(
      {c: object for c in frame.columns if str(frame[c].dtype) == "str"})


def _loader(frame):
  return _attr(_LOADER, "DataLoader")(
      _attr(_CONNECTOR, "DataFrameConnector")(df=frame), cacher="NoCache")


# Every stage takes the shared state, whether it reads it or not.
def stage_imports(state) -> str:  # pylint: disable=unused-argument
  for module in (_CONNECTOR, _LOADER, _METADATA, _PROCESSORS, _TRANSFORMER):
    importlib.import_module(module)
  return "sdgx " + importlib.metadata.version("sdgx")


def stage_metadata(state) -> str:
  metadata_cls = _attr(_METADATA, "Metadata")
  metadata = metadata_cls.from_dataloader(_loader(state["frame"]))
  state["metadata"] = metadata
  return (f"discrete={len(metadata.discrete_columns)} "
          f"int={len(metadata.int_columns)} "
          f"float={len(metadata.float_columns)}")


def stage_processors(state) -> str:
  """The default data processors, applied as `Synthesizer.fit` applies them."""
  manager = _attr(_PROCESSORS, "DataProcessorManager")()
  metadata = state["metadata"].model_copy()
  loader = _loader(state["frame"])
  chunk = state["frame"].copy()
  names = manager.registed_default_processor_list
  for name in names:
    processor = manager.init_data_processor(name)
    processor.fit(metadata=metadata, tabular_data=loader)
    chunk = processor.convert(chunk)
  state["processed"] = chunk
  return f"{len(names)} processors"


def stage_null_fill(state) -> str:
  """What the processors hand CTGAN where the source had nulls."""
  frame, processed = state["frame"], state["processed"]
  was_null = frame["nullable_num"].isna()
  zeros = int((processed["nullable_num"][was_null] == 0).sum())
  cat_nulls = int(frame["nullable_cat"].isna().sum())
  marked = int((processed["nullable_cat"] == _NULL_MARKER).sum())
  return (f"numeric: {int(was_null.sum())} nulls -> {zeros} zeros; "
          f"categorical: {cat_nulls} nulls -> {marked} {_NULL_MARKER!r}")


def _transform_round_trip(frame) -> str:
  loader = _loader(frame)
  metadata = _attr(_METADATA, "Metadata").from_dataloader(loader)
  transformer = _attr(_TRANSFORMER, "DataTransformer")(metadata=metadata)
  discrete = [c for c in frame.columns if c in metadata.discrete_columns]
  transformer.fit(loader, discrete)
  encoded = transformer.transform(loader)
  decoded = transformer.inverse_transform(encoded)
  # The encoded width comes from an unseeded mixture fit: not printed.
  return f"fit, transform, inverse: {decoded.shape[1]} columns back"


def stage_transformer_str(state) -> str:
  return _transform_round_trip(state["processed"])


def stage_transformer_object(state) -> str:
  return _transform_round_trip(_strings_as_object(state["processed"]))


def stage_default_device(state) -> str:  # pylint: disable=unused-argument
  model = _attr(_CTGAN, "CTGANSynthesizerModel")
  return repr(inspect.signature(model).parameters["device"].default)


def _fit_and_sample(frame) -> str:
  """`SdgxBackend._fit_sdgx`'s calls, then `sample`, on the CPU."""
  connector = _attr(_CONNECTOR, "DataFrameConnector")(df=frame)
  loader = _attr(_LOADER, "DataLoader")(connector)
  metadata = _attr(_METADATA, "Metadata").from_dataloader(loader)
  model_cls = _attr(_CTGAN, "CTGANSynthesizerModel")
  synthesizer = _attr(_SYNTHESIZER, "Synthesizer")(
      model=model_cls(epochs=FIT_EPOCHS, device="cpu"),
      data_connector=connector,
      metadata=metadata,
  )
  synthesizer.fit()
  sampled = synthesizer.sample(SAMPLE_ROWS)
  nulls = float(sampled["nullable_num"].isna().mean())
  cached = sum(len(files) for _, _, files in os.walk(".sdgx_cache"))
  return (f"sampled {len(sampled)} rows; nullable_num null share "
          f"{nulls:.2f} (source {NULL_SHARE_NUMERIC:.2f}); "
          f"{cached} files under ./.sdgx_cache")


def stage_fit_str(state) -> str:
  return _fit_and_sample(state["frame"])


def stage_fit_object(state) -> str:
  return _fit_and_sample(_strings_as_object(state["frame"]))


# (stage, what it asks of sdgx, needs torch, expected on 2026-11-16).
STAGES = (
    (stage_imports, "import connector, loader, metadata", False, "ok"),
    (stage_metadata, "infer metadata from the frame", False, "ok"),
    (stage_processors, "default processors: fit + convert", False, "ok"),
    (stage_null_fill, "what nulls become before CTGAN", False, "ok"),
    (stage_transformer_str, "CTGAN transformer, pandas 3 `str` columns", False,
     "FAIL"),
    (stage_transformer_object, "CTGAN transformer, strings cast to `object`",
     False, "ok"),
    (stage_default_device, "CTGAN's default device here", True, "?"),
    (stage_fit_str, "`_fit_sdgx` calls as written, then sample", True, "?"),
    (stage_fit_object, "the same with strings cast to `object`", True, "?"),
)


def _run_stage(stage, state, *, needs_torch: bool) -> tuple[str, str]:
  if needs_torch and importlib.util.find_spec("torch") is None:
    return "SKIP", "no torch wheel for this platform"
  # A probe reports every failure of the probed library as a row.
  try:
    return "ok", stage(state)
  except Exception as e:  # pylint: disable=broad-exception-caught
    return "FAIL", f"{type(e).__name__}: {str(e)[:110]}"


def main() -> None:
  warnings.simplefilter("ignore")
  # Whatever sdgx caches goes to a throwaway directory, where the last
  # stage can count it.
  os.chdir(tempfile.mkdtemp(prefix="sdgx-probe-"))
  importlib.import_module("loguru").logger.remove()

  versions = {
      name: importlib.metadata.version(name)
      for name in ("sdgx", "numpy", "pandas", "scikit-learn")
  }
  torch = (
      importlib.metadata.version("torch")
      if importlib.util.find_spec("torch") else "not installed")
  print(f"python {platform.python_version()} on {sys.platform} "
        f"{platform.machine()}; " +
        ", ".join(f"{k} {v}" for k, v in versions.items()) + f", torch {torch}")
  print()
  print("| # | stage | outcome | expected | detail |")
  print("|---|---|---|---|---|")
  state: dict = {"frame": _toy_frame()}
  news = 0
  for i, (stage, label, needs_torch, expected) in enumerate(STAGES, start=1):
    outcome, detail = _run_stage(stage, state, needs_torch=needs_torch)
    if outcome != "SKIP" and expected not in ("?", outcome):
      news += 1
    print(f"| {i} | {label} | {outcome} | {expected} | {detail} |")
  print()
  print(f"{news} stage(s) differ from the 2026-11-16 expectation.")


if __name__ == "__main__":
  main()
