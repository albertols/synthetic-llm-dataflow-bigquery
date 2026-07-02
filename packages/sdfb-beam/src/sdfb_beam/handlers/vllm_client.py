"""vLLM-backed `ModelClient` — owns a vLLM OpenAI-compatible server.

Per [ADR 0014](../../../../docs/adr/0014-vllm-model-client-owns-server.md)
(which amends [ADR 0011](../../../../docs/adr/0011-adopt-beam-vllm-model-handler.md)),
this client manages the vLLM server subprocess directly instead of going
through Beam's `RunInference` handler. The engines call
`generate_json(prompt, json_schema, ...)` synchronously and O(1) times
(free-text pools / distribution inference, NOT per row — see ADR 0013),
so a `RunInference` PTransform is the wrong shape for the seam.

Lifecycle (per Dataflow worker, driven by the engine's `DoFn`):

    c = VLLMModelClient(model_uri="gs://.../gemma4/e4b-it/v1/",
                        vllm_server_kwargs={"max-model-len": "8192", ...})
    c.setup()        # GCS warm-pull → /local-ssd/model; spawn server; poll ready
    rows = c.generate_json(prompt, schema, n=5)
    c.teardown()     # terminate the server subprocess

CUDA-only. This CANNOT run on the M4 (vLLM ships no macOS wheels and needs
an NVIDIA GPU). The laptop only ever imports the class and exercises the
mock-based unit tests — every heavy dependency (`vllm`, `openai`,
`google.cloud.storage`) is imported INSIDE `setup()` / `generate_json()`,
never at module load. Real-vLLM behavior is validated at M1 §11 on an L4.

Constraints honoured here:
  - Weights pulled via the `google-cloud-storage` Python client, NOT gsutil
    (ADR 0012 — the CLI drags in a `packages.cloud.google.com` apt dep the
    enterprise build can't reach; ADC authenticates the client on-worker).
  - The **chat** endpoint (not completions) is used so vLLM applies Gemma 4's
    chat template — required to suppress the chain-of-thought channel via
    `chat_template_kwargs={"enable_thinking": False}` (ADR 0014; the
    completions endpoint does not apply the chat template).
  - Guided JSON via `extra_body={"guided_json": schema, ...}` — vLLM's
    OpenAI-server extension for schema-constrained decoding.

REFs:
  - docs/adr/0014-vllm-model-client-owns-server.md (THE design)
  - docs/adr/0011-adopt-beam-vllm-model-handler.md (amended)
  - docs/adr/0012-enterprise-image-build.md (GCS-client pull, version pins)
  - docs/adr/0013-distribution-estimator-spine.md (LLM is O(1))
  - config/models.yml (`vllm_server_kwargs` per model)
  - .claude/skills/model-handler.md (recipe)
  - https://docs.vllm.ai/en/latest/usage/structured_outputs.html
  - https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    import subprocess

logger = logging.getLogger(__name__)

# Where the GCS warm-pull lands and where the vLLM server reads weights from.
# Dataflow GPU workers mount fast local SSD here (see gpu-dockerfile recipe).
DEFAULT_LOCAL_MODEL_DIR = "/local-ssd/model"
DEFAULT_PORT = 8000
# vLLM cold start on an L4 (CUDA graph capture + weight load) is well under
# this; the GCS pull happens before the poll loop starts. Generous but bounded.
DEFAULT_STARTUP_TIMEOUT_S = 600.0
DEFAULT_POLL_INTERVAL_S = 2.0
_HTTP_OK = 200


class VLLMModelClient:
    """`ModelClient` impl that owns a vLLM OpenAI-compatible server.

    Structural `ModelClient` (the Protocol in `sdfb_core.engines.base`) —
    does not subclass it; engines test interchangeability via
    `isinstance(client, ModelClient)`.
    """

    def __init__(
        self,
        model_uri: str,
        *,
        vllm_server_kwargs: dict[str, Any] | None = None,
        local_model_dir: str = DEFAULT_LOCAL_MODEL_DIR,
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
        startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        guided_decoding_backend: str = "outlines",
    ) -> None:
        """Configure the client. No heavy work happens here.

        Args:
            model_uri: `gs://{bucket}/.../{family}/{model}/{version}/` prefix
                whose contents are pulled to `local_model_dir`. A bare local
                path (no `gs://` scheme) is used as-is (skips the pull) — handy
                for an L4 box that already has the weights staged.
            vllm_server_kwargs: extra CLI flags for
                `vllm.entrypoints.openai.api_server`, from the matching entry
                in `config/models.yml` (e.g. `{"quantization": "awq",
                "max-model-len": "8192", "gpu-memory-utilization": "0.85"}`).
                Keys map to `--key value` (or a bare `--key` flag when the
                value is `True`).
            local_model_dir: where weights land / where vLLM reads them.
            port / host: where the spawned server listens.
            startup_timeout_s / poll_interval_s: readiness-poll budget.
            guided_decoding_backend: vLLM guided-decoding backend (ADR 0011
                fallback chain: vLLM guided JSON → outlines → repair loop).
        """
        self.model_uri = model_uri
        self.vllm_server_kwargs: dict[str, Any] = dict(vllm_server_kwargs or {})
        self.local_model_dir = local_model_dir
        self.port = port
        self.host = host
        self.startup_timeout_s = startup_timeout_s
        self.poll_interval_s = poll_interval_s
        self.guided_decoding_backend = guided_decoding_backend

        # Populated by setup(); reset by teardown().
        self._server: subprocess.Popen[bytes] | None = None
        self._client: Any = None  # openai.OpenAI
        # The model identifier the OpenAI client must send. vLLM registers the
        # served model under the path/name it was launched with, so it equals
        # the local model dir after a GCS pull.
        self._served_model_name: str = local_model_dir

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Per-worker init. Idempotent (a second call is a no-op).

        1. Pull weights GCS → `local_model_dir` (skipped for a local path).
        2. Spawn the vLLM OpenAI server subprocess.
        3. Poll `/v1/models` until ready (or time out).
        4. Build the `openai.OpenAI` client pointed at the local server.
        """
        if self._client is not None:
            return

        if self.model_uri.startswith("gs://"):
            self._pull_weights()
            self._served_model_name = self.local_model_dir
        else:
            # Already-local weights; serve them in place.
            logger.info(
                "model_uri %r is not a gs:// URI — serving it as a local "
                "path (skipping GCS pull).",
                self.model_uri,
            )
            self._served_model_name = self.model_uri

        self._spawn_server()
        self._wait_until_ready()
        self._client = self._build_openai_client()
        logger.info("vLLM server ready at %s", self.base_url)

    def teardown(self) -> None:
        """Terminate the server subprocess and drop the client.

        Safe to call when `setup()` never ran or already torn down.
        """
        self._client = None
        server, self._server = self._server, None
        if server is None:
            return
        logger.info("Terminating vLLM server subprocess (pid=%s)", server.pid)
        server.terminate()
        try:
            server.wait(timeout=30)
        except Exception:  # best-effort cleanup — terminate may hang
            logger.warning("vLLM server did not exit on SIGTERM; killing.")
            server.kill()
            try:
                server.wait(timeout=10)
            except Exception:
                logger.error("vLLM server did not exit on SIGKILL.")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        json_schema: dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        n: int = 1,
        seed: int | None = None,
    ) -> list[dict]:
        """Return up to `n` JSON dicts conforming to `json_schema`.

        Calls the vLLM OpenAI-compatible **chat** endpoint once with `n=`,
        so the server applies Gemma 4's chat template (which lets us suppress
        the thinking channel) and batches the `n` candidates server-side.
        Each `choices[*].message.content` is parsed as JSON; entries that fail
        to parse to a dict are dropped (the engine's repair loop handles
        shortfalls — yielding fewer than `n` is allowed by the contract).
        """
        if self._client is None:
            raise RuntimeError(
                "VLLMModelClient.generate_json() called before setup() (or "
                "after teardown()). The Beam DoFn must call setup() once per "
                "worker before generate_batch()."
            )

        response = self._client.chat.completions.create(
            model=self._served_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            n=n,
            seed=seed,
            extra_body={
                # vLLM guided-decoding: constrain output to the JSON schema.
                "guided_json": json_schema,
                # vLLM applies the chat template on this endpoint; pass
                # template kwargs through to suppress Gemma 4's chain-of-thought
                # channel (ADR 0014). Unknown kwargs are ignored by Jinja.
                "chat_template_kwargs": {"enable_thinking": False},
                "guided_decoding_backend": self.guided_decoding_backend,
            },
        )

        out: list[dict] = []
        for choice in response.choices:
            content = choice.message.content
            parsed = self._parse_json(content)
            if parsed is None:
                logger.warning(
                    "vLLM choice content did not parse to a JSON object "
                    "(len=%d); dropping. Guided decoding should make this "
                    "rare — investigate the schema if it recurs.",
                    len(content or ""),
                )
                continue
            out.append(parsed)
        return out

    # ------------------------------------------------------------------
    # Internals — heavy imports stay inside these (laptop-importable class).
    # ------------------------------------------------------------------

    def _pull_weights(self) -> None:
        """Warm-pull `model_uri` (gs://) → `local_model_dir` via the GCS client.

        Uses the `google-cloud-storage` Python client (ADR 0012), authenticated
        via ADC on the worker. Never shells out to gsutil.
        """
        from pathlib import Path

        from google.cloud import storage

        bucket_name, prefix = _split_gs_uri(self.model_uri)
        logger.info(
            "Warm-pulling weights from gs://%s/%s → %s",
            bucket_name,
            prefix,
            self.local_model_dir,
        )
        client = storage.Client()
        dest_root = Path(self.local_model_dir)
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
                f"model_uri. Nothing was pulled to {self.local_model_dir}."
            )
        logger.info("Pulled %d files to %s", n_files, self.local_model_dir)

    def _server_command(self) -> list[str]:
        """Build the `python -m vllm.entrypoints.openai.api_server ...` argv."""
        import sys

        cmd = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            self._served_model_name,
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        for key, value in self.vllm_server_kwargs.items():
            flag = f"--{key}"
            if value is True:
                cmd.append(flag)
            elif value is False or value is None:
                continue
            else:
                cmd.extend([flag, str(value)])
        return cmd

    def _spawn_server(self) -> None:
        import subprocess

        cmd = self._server_command()
        logger.info("Spawning vLLM server: %s", " ".join(cmd))
        self._server = subprocess.Popen(cmd)

    def _wait_until_ready(self) -> None:
        """Poll `/v1/models` until the server answers 200, or time out.

        Fails fast if the subprocess dies during startup (so a bad weight
        path / OOM surfaces as an error instead of a silent timeout).
        """
        from urllib.error import URLError
        from urllib.request import urlopen

        models_url = f"{self.base_url}/models"
        deadline = time.monotonic() + self.startup_timeout_s
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._server is not None and self._server.poll() is not None:
                raise RuntimeError(
                    "vLLM server subprocess exited during startup with code "
                    f"{self._server.returncode}. Check the model path "
                    f"({self._served_model_name!r}) and vllm_server_kwargs."
                )
            try:
                with urlopen(models_url, timeout=self.poll_interval_s) as resp:
                    if resp.status == _HTTP_OK:
                        return
            except URLError as e:  # not up yet — keep polling
                last_err = e
            except Exception as e:  # connection refused / reset during boot
                last_err = e
            time.sleep(self.poll_interval_s)
        raise TimeoutError(
            f"vLLM server did not become ready at {models_url} within "
            f"{self.startup_timeout_s}s. Last error: {last_err!r}"
        )

    def _build_openai_client(self) -> Any:
        from openai import OpenAI

        # The local vLLM server ignores the key, but the client requires a
        # non-empty value. This is NOT an external API call (ADR 0001 / hard
        # constraint #4) — base_url points at localhost.
        return OpenAI(base_url=self.base_url, api_key="EMPTY")

    @staticmethod
    def _parse_json(content: str | None) -> dict | None:
        """Parse guided-decoding output into a dict; None if not a JSON object.

        Guided JSON makes the content a bare JSON object, so a strict
        `json.loads` is enough — no lenient brace-scanning like the MLX path
        (which has no token-level grammar constraint).
        """
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def _split_gs_uri(uri: str) -> tuple[str, str]:
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
