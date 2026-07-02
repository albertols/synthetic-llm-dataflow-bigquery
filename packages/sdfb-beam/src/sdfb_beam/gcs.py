"""Shared GCS → worker-local warm-pull (ADR 0012).

Offline loaders (`transformers`, vLLM) read weights from a **local
directory** only — they cannot open a `gs://` URI. So any weight prefix
staged in GCS must be pulled onto worker-local disk once, before the
loader touches it. Both the vLLM `ModelClient` (LLM weights) and the
generation DoFn (B.1's embedder) need this, so the pull lives here in
exactly one place rather than being duplicated per caller.

Uses the `google-cloud-storage` Python client, authenticated via ADC on
the worker. Never shells out to `gsutil`. The heavy import stays inside
the function so importing this module on a bare laptop is dependency-free.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def split_gs_uri(uri: str) -> tuple[str, str]:
    """Split `gs://bucket/path/to/prefix/` → `("bucket", "path/to/prefix/")`.

    The returned prefix keeps any trailing slash so `blob.name[len(prefix):]`
    yields paths relative to the model directory.
    """
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a gs:// URI: {uri!r}")
    parts = urlsplit(uri)
    bucket = parts.netloc
    prefix = parts.path.lstrip("/")
    if not bucket:
        raise ValueError(f"gs:// URI has no bucket: {uri!r}")
    return bucket, prefix


def localize_gcs_prefix(uri: str, local_dir: str) -> str:
    """Warm-pull every blob under `uri` (gs://) into `local_dir`.

    Returns `local_dir` for call-site convenience. Raises `RuntimeError` if
    the prefix contains no blobs (a mistyped URI would otherwise silently
    yield an empty directory that the loader then fails on cryptically).
    """
    from pathlib import Path

    from google.cloud import storage

    bucket_name, prefix = split_gs_uri(uri)
    logger.info(
        "Warm-pulling gs://%s/%s → %s", bucket_name, prefix, local_dir
    )
    client = storage.Client()
    dest_root = Path(local_dir)
    n_files = 0
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        rel = blob.name[len(prefix):].lstrip("/")
        if not rel:
            # The prefix "directory" placeholder blob, if present.
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        n_files += 1
    if n_files == 0:
        raise RuntimeError(
            f"No blobs found under gs://{bucket_name}/{prefix} — check the "
            f"URI. Nothing was pulled to {local_dir}."
        )
    logger.info("Pulled %d files to %s", n_files, local_dir)
    return local_dir


__all__ = ["localize_gcs_prefix", "split_gs_uri"]