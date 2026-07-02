"""Unit tests for `sdfb_beam.gcs` — the shared GCS → local warm-pull.

The `google.cloud.storage` client is mocked by injecting a fake module into
`sys.modules`, so these run on a bare laptop with no GCP deps installed.
"""

from __future__ import annotations

import sys
import types

import pytest
from sdfb_beam.gcs import localize_gcs_prefix, split_gs_uri


@pytest.fixture
def fake_gcs(monkeypatch):
    """Inject a fake `google.cloud.storage` module + return its recorder."""

    class _FakeBlob:
        def __init__(self, name):
            self.name = name
            self.downloaded_to = None

        def download_to_filename(self, dest):
            self.downloaded_to = dest

    recorder: dict = {"list_calls": [], "blobs": []}

    class _FakeStorageClient:
        def list_blobs(self, bucket, prefix=""):
            recorder["list_calls"].append((bucket, prefix))
            return list(recorder["blobs"])

    storage_mod = types.ModuleType("google.cloud.storage")
    storage_mod.Client = lambda *_a, **_k: _FakeStorageClient()

    cloud_mod = types.ModuleType("google.cloud")
    cloud_mod.storage = storage_mod
    google_mod = sys.modules.get("google") or types.ModuleType("google")

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    recorder["blob_cls"] = _FakeBlob
    return recorder


def test_split_gs_uri_returns_bucket_and_prefix():
    assert split_gs_uri("gs://bkt/a/b/c/") == ("bkt", "a/b/c/")


@pytest.mark.parametrize("bad", ["/local/path", "s3://x/y", "gs://"])
def test_split_gs_uri_rejects_non_gcs(bad):
    with pytest.raises(ValueError):
        split_gs_uri(bad)


def test_import_does_not_require_google_cloud():
    """Importing the module must not drag in google.cloud.storage."""
    # The lazy import lives inside localize_gcs_prefix, not at module scope.
    import importlib

    import sdfb_beam.gcs as gcs_mod

    importlib.reload(gcs_mod)
    # Nothing to assert beyond a clean import; the reload would raise if the
    # heavy dep were imported at module scope on a laptop without it.


def test_localize_downloads_blobs_relative_to_prefix(fake_gcs, tmp_path):
    blob_cls = fake_gcs["blob_cls"]
    prefix = "synthetic/models/embedders/bge-small-en-v1.5/v1/"
    fake_gcs["blobs"] = [
        blob_cls(prefix),  # directory placeholder — must be skipped
        blob_cls(prefix + "config.json"),
        blob_cls(prefix + "model.safetensors"),
        blob_cls(prefix + "1_Pooling/config.json"),  # nested
    ]
    dest = str(tmp_path / "emb")

    out = localize_gcs_prefix(f"gs://my-bucket/{prefix}", dest)

    assert out == dest
    assert fake_gcs["list_calls"] == [("my-bucket", prefix)]
    downloaded = sorted(
        b.downloaded_to for b in fake_gcs["blobs"] if b.downloaded_to
    )
    assert downloaded == sorted(
        [
            str(tmp_path / "emb" / "config.json"),
            str(tmp_path / "emb" / "model.safetensors"),
            str(tmp_path / "emb" / "1_Pooling" / "config.json"),
        ]
    )


def test_localize_raises_when_no_blobs(fake_gcs, tmp_path):
    fake_gcs["blobs"] = []
    with pytest.raises(RuntimeError, match="No blobs found"):
        localize_gcs_prefix("gs://empty/no/such/prefix/", str(tmp_path / "emb"))