"""Unit tests for `sdfb_beam.handlers.vllm_client.VLLMModelClient`.

These run on the laptop / CI WITHOUT vllm or openai installed. The real
client is CUDA-only and validated end-to-end at M1 §11 on an L4 (those
tests would carry `@pytest.mark.gpu`). Here we mock every heavy boundary:

  - the `openai` chat client (injected as `client._client`),
  - the server subprocess (`subprocess.Popen`),
  - the `google.cloud.storage` client (injected into `sys.modules`),

and assert the contract: the request SHAPE (chat `messages`,
`response_format` json_schema for vLLM >= 0.10 structured outputs,
`extra_body` carrying `chat_template_kwargs.enable_thinking=False`, no
request seed unless explicit), JSON parsing, `n` handling, lazy
self-ignition on first use, and teardown subprocess termination.

The class is importable here precisely because all heavy imports are
deferred into method bodies — that property is itself part of the contract
(`test_import_does_not_require_heavy_deps`).
"""

from __future__ import annotations

import json
import sys
import types
from unittest import mock

import pytest
from sdfb_beam.handlers.vllm_client import (
    ModelGpuIncompatibleError,
    VLLMModelClient,
    _assert_dtype_supported,
    _split_gs_uri,
)
from sdfb_core.engines import ModelClient

# ---------------------------------------------------------------------------
# Fakes for the mocked openai chat response shape.
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, contents):
        self.choices = [_FakeChoice(c) for c in contents]


def _client_with_fake_openai(contents, **kwargs):
    """A VLLMModelClient whose `_client` is a mock returning `contents`.

    Bypasses setup() entirely: we inject the mock chat client and the served
    model name so generate_json() runs against the mock.
    """
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/", **kwargs)
    fake_openai = mock.MagicMock()
    fake_openai.chat.completions.create.return_value = _FakeResponse(contents)
    c._client = fake_openai
    c._served_model_name = "/local-ssd/model"
    return c, fake_openai


# ---------------------------------------------------------------------------
# Protocol conformance + laptop-importability.
# ---------------------------------------------------------------------------


def test_satisfies_model_client_protocol():
    c = VLLMModelClient(model_uri="gs://bucket/m/v1/")
    assert isinstance(c, ModelClient)


def test_import_does_not_require_heavy_deps():
    """vllm / openai / google.cloud.storage must NOT be imported at module load."""
    import sdfb_beam.handlers.vllm_client as mod

    # Constructing the class must not import any heavy dep.
    VLLMModelClient(model_uri="gs://bucket/m/v1/")
    assert mod.__file__.endswith("vllm_client.py")
    # vllm is the linux-only dep that is never present on the laptop; importing
    # this module (done above) must not have pulled it in.
    assert "vllm" not in sys.modules


def test_init_defaults_match_factory_call():
    """`VLLMModelClient(model_uri=...)` (the cli factory call) must work."""
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/gemma4/e4b-it/v1/")
    assert c.model_uri == "gs://bucket/synthetic/models/gemma4/e4b-it/v1/"
    assert c.vllm_server_kwargs == {}
    assert c.local_model_dir == "/local-ssd/model"
    assert c.port == 8000
    assert c.base_url == "http://127.0.0.1:8000/v1"


# ---------------------------------------------------------------------------
# generate_json — request shape.
# ---------------------------------------------------------------------------


def test_generate_json_uses_chat_endpoint_with_user_message():
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("hello prompt", {"type": "object"})
    fake_openai.chat.completions.create.assert_called_once()
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hello prompt"}]
    assert kwargs["model"] == "/local-ssd/model"


def test_generate_json_request_uses_response_format_json_schema():
    # vLLM ≥ 0.10 structured outputs: the schema constraint travels in the
    # OpenAI-standard `response_format`, NOT the legacy `guided_json`
    # extra_body field — vLLM 0.24 silently ignores the legacy field, which
    # produced free-form text and 100 % parse-drop in the 2026-07-15 E2E run.
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    c, fake_openai = _client_with_fake_openai(['{"x": 7}'])
    c.generate_json("p", schema)
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "sdfb_record", "schema": schema},
    }
    extra_body = kwargs["extra_body"]
    assert extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "guided_json" not in extra_body
    assert "guided_decoding_backend" not in extra_body


def test_generate_json_passes_sampling_params():
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("p", {}, max_tokens=512, temperature=0.2, n=1, seed=99)
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.2
    assert kwargs["seed"] == 99


def test_generate_json_passes_top_p_and_top_k_when_set():
    # A served model can pin sampling truncation via its generation_config
    # (Qwen3-4B: top_k=20, top_p=0.8 — vLLM's override warning in the
    # 2026-07-16 run). Escalated pool retries must be able to send explicit
    # overrides; top_p is OpenAI-standard, top_k is vLLM-specific extra_body
    # (0 = consider all tokens).
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("p", {}, temperature=1.3, top_p=1.0, top_k=0)
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["top_p"] == 1.0
    assert kwargs["extra_body"]["top_k"] == 0


def test_generate_json_omits_top_p_and_top_k_by_default():
    # Default requests keep the served model's vendor-tuned sampling defaults.
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("p", {})
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs["extra_body"]


def test_generate_json_omits_seed_when_none():
    # A per-request seed with n>1 collapses all n choices to one completion
    # on vLLM — no seed in the request unless a caller explicitly sets one.
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("p", {}, n=4)
    assert "seed" not in fake_openai.chat.completions.create.call_args.kwargs


def test_guided_decoding_backend_param_accepted_but_not_sent():
    # Constructor param kept for flex-template compatibility; per-request
    # backend selection no longer exists in vLLM 0.24 (server-side config).
    c, fake_openai = _client_with_fake_openai(
        ['{"a": 1}'], guided_decoding_backend="lm-format-enforcer"
    )
    c.generate_json("p", {})
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert "guided_decoding_backend" not in kwargs.get("extra_body", {})


# ---------------------------------------------------------------------------
# generate_json — n handling + JSON parsing.
# ---------------------------------------------------------------------------


def test_generate_json_n_passed_to_api_and_all_choices_parsed():
    contents = ['{"i": 0}', '{"i": 1}', '{"i": 2}']
    c, fake_openai = _client_with_fake_openai(contents)
    out = c.generate_json("p", {}, n=3)
    assert fake_openai.chat.completions.create.call_args.kwargs["n"] == 3
    assert out == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_generate_json_parses_single_object():
    c, _ = _client_with_fake_openai(['{"name": "x", "age": 3}'])
    assert c.generate_json("p", {}) == [{"name": "x", "age": 3}]


def test_generate_json_drops_unparseable_choices():
    # Second choice is not valid JSON — it should be dropped, not crash.
    c, _ = _client_with_fake_openai(['{"ok": 1}', "not json", '{"ok": 2}'])
    out = c.generate_json("p", {}, n=3)
    assert out == [{"ok": 1}, {"ok": 2}]


def test_generate_json_drops_non_object_json():
    # A JSON array / scalar is valid JSON but not a record dict — drop it.
    c, _ = _client_with_fake_openai(["[1, 2, 3]", "42", '{"ok": 1}'])
    out = c.generate_json("p", {}, n=3)
    assert out == [{"ok": 1}]


def test_generate_json_handles_none_and_empty_content():
    c, _ = _client_with_fake_openai([None, "", '{"ok": 1}'])
    out = c.generate_json("p", {}, n=3)
    assert out == [{"ok": 1}]


def test_generate_json_self_ignites_when_not_set_up(monkeypatch):
    """WS1 §3b: generate_json() must call setup() itself instead of raising.
    A ready client is borrowed to stand in for what setup() would build."""
    ready, _ = _client_with_fake_openai([json.dumps({"values": ["x"]})])
    c = VLLMModelClient(model_uri="gs://bucket/m/v1/")
    calls: list[str] = []

    def fake_setup():
        calls.append("setup")
        c._client = ready._client
        c._served_model_name = ready._served_model_name

    monkeypatch.setattr(c, "setup", fake_setup)
    out = c.generate_json(prompt="p", json_schema={"type": "object"})
    assert calls == ["setup"]
    assert out == [{"values": ["x"]}]


# ---------------------------------------------------------------------------
# setup() orchestration (boundaries mocked).
# ---------------------------------------------------------------------------


def test_setup_pulls_spawns_polls_and_builds_client_in_order():
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    calls = []
    sentinel_client = object()
    with (
        mock.patch.object(c, "_pull_weights", side_effect=lambda: calls.append("pull")),
        mock.patch.object(c, "_spawn_server", side_effect=lambda: calls.append("spawn")),
        mock.patch.object(c, "_wait_until_ready", side_effect=lambda: calls.append("wait")),
        mock.patch.object(
            c,
            "_build_openai_client",
            side_effect=lambda: (calls.append("build") or sentinel_client),
        ),
    ):
        c.setup()
    assert calls == ["pull", "spawn", "wait", "build"]
    assert c._client is sentinel_client
    # gs:// URI → served model name is the local dir.
    assert c._served_model_name == "/local-ssd/model"


def test_setup_is_idempotent():
    c, _ = _client_with_fake_openai(['{"a": 1}'])  # _client already set
    with (
        mock.patch.object(c, "_pull_weights") as pull,
        mock.patch.object(c, "_spawn_server") as spawn,
    ):
        c.setup()  # _client is not None → no-op
    pull.assert_not_called()
    spawn.assert_not_called()


def test_setup_emits_milestones(caplog):
    import logging

    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server"),
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
        caplog.at_level(logging.INFO, logger="sdfb.milestone"),
    ):
        c.setup()
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=model_pull_start" in text
    assert "SDFB_MILESTONE name=model_pull_done" in text
    assert "SDFB_MILESTONE name=vllm_spawn" in text
    assert "SDFB_MILESTONE name=vllm_ready" in text


def test_setup_local_path_skips_pull_and_serves_in_place():
    c = VLLMModelClient(model_uri="/already/local/model")
    with (
        mock.patch.object(c, "_pull_weights") as pull,
        mock.patch.object(c, "_spawn_server"),
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    pull.assert_not_called()
    assert c._served_model_name == "/already/local/model"


# ---------------------------------------------------------------------------
# setup() — reuse of an already-healthy server on this worker.
#
# 2026-07-16 corp run: each Dataflow bundle retry ran in a fresh sibling SDK
# process that re-pulled 7.5 GB of weights and spawned a NEW vLLM server into
# the GPU still owned by the first attempt's server — EngineCore crashed with
# "Free memory on device cuda:0 (0.66/14.56 GiB)" on every retry while the
# health check accidentally passed against the surviving first server. setup()
# must detect that surviving server and reuse it instead.
# ---------------------------------------------------------------------------


class _FakeModelsResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_setup_reuses_existing_healthy_server(monkeypatch, caplog):
    import logging
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _FakeModelsResponse({"data": [{"id": "/local-ssd/model"}]}),
    )
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    sentinel = object()
    with (
        mock.patch.object(c, "_pull_weights") as pull,
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_build_openai_client", return_value=sentinel),
        caplog.at_level(logging.INFO, logger="sdfb.milestone"),
    ):
        c.setup()
    pull.assert_not_called()
    spawn.assert_not_called()
    assert c._client is sentinel
    assert c._served_model_name == "/local-ssd/model"
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=vllm_reuse" in text


def test_setup_spawns_when_existing_server_serves_a_different_model(monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _FakeModelsResponse({"data": [{"id": "/other/model"}]}),
    )
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    with (
        mock.patch.object(c, "_pull_weights") as pull,
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    pull.assert_called_once()
    spawn.assert_called_once()


def test_probe_reusable_server_false_on_connection_error(monkeypatch):
    import urllib.request

    def _refuse(*a, **k):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    assert c._probe_reusable_server("/local-ssd/model") is False


def test_probe_reusable_server_false_on_non_json_body(monkeypatch):
    import urllib.request

    class _HtmlResponse(_FakeModelsResponse):
        def read(self):
            return b"<html>not a vllm server</html>"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _HtmlResponse({})
    )
    c = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
    assert c._probe_reusable_server("/local-ssd/model") is False


# ---------------------------------------------------------------------------
# setup() — concurrent DoFn threads must share ONE server per worker.
#
# 2026-07-16 b2_library run: a freshly-autoscaled worker handed bundles to 8
# DoFn threads at once. Every thread failed the (unlocked) reuse probe within
# the same instant, so 3+ vLLM servers spawned onto one 14.56 GiB T4 — each
# loaded ~4.8 GiB of weights and all of them OOMed allocating KV cache, exit
# code 1, four bundle-retry strikes, job FAILED. setup() must serialize the
# probe → pull → spawn → ready window process-wide so exactly one server
# exists and late threads bind to it.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_server_registry(monkeypatch):
    """Isolate the module-level shared-server registry per test."""
    import sdfb_beam.handlers.vllm_client as mod

    monkeypatch.setattr(mod, "_SERVER_REFS", {})
    monkeypatch.setattr(mod, "_PARKED_SERVERS", {})


def test_concurrent_setup_spawns_exactly_one_server(monkeypatch):
    import threading
    import time as _time
    import urllib.request

    spawned: list = []
    pulled: list = []

    def fake_urlopen(*a, **k):
        # No server listening until the first spawn lands.
        if not spawned:
            raise ConnectionRefusedError("nothing listening yet")
        return _FakeModelsResponse({"data": [{"id": "/local-ssd/model"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def fake_pull(self):
        _time.sleep(0.02)  # widen the race window
        pulled.append(self)

    def fake_spawn(self):
        _time.sleep(0.05)  # widen the race window
        spawned.append(self)

    with (
        mock.patch.object(VLLMModelClient, "_pull_weights", fake_pull),
        mock.patch.object(VLLMModelClient, "_spawn_server", fake_spawn),
        mock.patch.object(VLLMModelClient, "_wait_until_ready", lambda self: None),
        mock.patch.object(
            VLLMModelClient, "_build_openai_client", lambda self: object()
        ),
    ):
        clients = [
            VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
            for _ in range(8)
        ]
        threads = [threading.Thread(target=c.setup) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(spawned) == 1, "concurrent setup() must spawn exactly one server"
    assert len(pulled) == 1, "concurrent setup() must pull weights exactly once"
    assert all(c._client is not None for c in clients)


def test_teardown_owner_parks_server_while_reusers_active(monkeypatch):
    """The spawning client's teardown must NOT kill a server that sibling
    clients (reusers) are still bound to — the 2026-07-16 b2 run terminated
    pid=109 out from under 7 reusing threads. The LAST client out kills it."""
    import urllib.request

    fake_proc = mock.MagicMock()
    fake_proc.pid = 4321
    server_up: list = []

    def fake_urlopen(*a, **k):
        if not server_up:
            raise ConnectionRefusedError("nothing listening yet")
        return _FakeModelsResponse({"data": [{"id": "/local-ssd/model"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def fake_spawn(self):
        self._server = fake_proc
        server_up.append(True)

    with (
        mock.patch.object(VLLMModelClient, "_pull_weights", lambda self: None),
        mock.patch.object(VLLMModelClient, "_spawn_server", fake_spawn),
        mock.patch.object(VLLMModelClient, "_wait_until_ready", lambda self: None),
        mock.patch.object(
            VLLMModelClient, "_build_openai_client", lambda self: object()
        ),
    ):
        owner = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
        owner.setup()  # spawns
        reuser = VLLMModelClient(model_uri="gs://bucket/synthetic/models/m/v1/")
        reuser.setup()  # binds to the same server

    owner.teardown()
    fake_proc.terminate.assert_not_called()  # reuser still active

    reuser.teardown()
    fake_proc.terminate.assert_called_once()  # last one out kills it


# ---------------------------------------------------------------------------
# _server_command — argv construction from vllm_server_kwargs.
# ---------------------------------------------------------------------------


def test_server_command_includes_model_host_port():
    c = VLLMModelClient(model_uri="/local/model", port=9001, host="0.0.0.0")
    c._served_model_name = "/local/model"
    cmd = c._server_command()
    assert "vllm.entrypoints.openai.api_server" in cmd
    assert cmd[cmd.index("--model") + 1] == "/local/model"
    assert cmd[cmd.index("--port") + 1] == "9001"
    assert cmd[cmd.index("--host") + 1] == "0.0.0.0"


def test_server_command_maps_kwargs_to_flags():
    c = VLLMModelClient(
        model_uri="/local/model",
        vllm_server_kwargs={
            "quantization": "awq",
            "max-model-len": "8192",
            "gpu-memory-utilization": "0.85",
        },
    )
    c._served_model_name = "/local/model"
    cmd = c._server_command()
    assert cmd[cmd.index("--quantization") + 1] == "awq"
    assert cmd[cmd.index("--max-model-len") + 1] == "8192"
    assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.85"


def test_server_command_bare_flag_for_true_and_skips_falsey():
    c = VLLMModelClient(
        model_uri="/local/model",
        vllm_server_kwargs={"enforce-eager": True, "trust-remote-code": False, "x": None},
    )
    c._served_model_name = "/local/model"
    cmd = c._server_command()
    # A True value → bare flag with no following value.
    assert "--enforce-eager" in cmd
    idx = cmd.index("--enforce-eager")
    # Either it's the last token, or the next token is another flag (not "True").
    assert idx == len(cmd) - 1 or cmd[idx + 1].startswith("--")
    # False / None values → flag omitted entirely.
    assert "--trust-remote-code" not in cmd
    assert "--x" not in cmd


# ---------------------------------------------------------------------------
# teardown — subprocess termination.
# ---------------------------------------------------------------------------


def test_teardown_terminates_subprocess():
    c = VLLMModelClient(model_uri="/local/model")
    fake_proc = mock.MagicMock()
    fake_proc.pid = 4321
    c._server = fake_proc
    c._client = object()
    c.teardown()
    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called()
    assert c._server is None
    assert c._client is None


def test_teardown_kills_when_terminate_times_out():
    import subprocess

    c = VLLMModelClient(model_uri="/local/model")
    fake_proc = mock.MagicMock()
    fake_proc.pid = 1
    # First wait (after terminate) times out; second wait (after kill) returns.
    fake_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="vllm", timeout=30), 0]
    c._server = fake_proc
    c.teardown()
    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()


def test_teardown_no_server_is_noop():
    c = VLLMModelClient(model_uri="/local/model")
    # No setup() ever ran.
    c.teardown()  # must not raise
    assert c._server is None


# ---------------------------------------------------------------------------
# _pull_weights — GCS client mocked via sys.modules injection.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_gcs(monkeypatch):
    """Inject a fake `google.cloud.storage` module + return its recorder."""

    class _FakeBlob:
        def __init__(self, name):
            self.name = name
            self.downloaded_to = None

        def download_to_filename(self, dest):
            self.downloaded_to = dest
            with open(dest, "w", encoding="utf-8") as f:
                f.write(self.name)

    recorder = {"list_calls": [], "blobs": []}

    class _FakeStorageClient:
        def list_blobs(self, bucket, prefix=""):
            recorder["list_calls"].append((bucket, prefix))
            return list(recorder["blobs"])

    def _make_client(*_args, **_kwargs):
        return _FakeStorageClient()

    storage_mod = types.ModuleType("google.cloud.storage")
    storage_mod.Client = _make_client

    cloud_mod = types.ModuleType("google.cloud")
    cloud_mod.storage = storage_mod
    google_mod = sys.modules.get("google") or types.ModuleType("google")

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    recorder["blob_cls"] = _FakeBlob
    return recorder


def test_pull_weights_downloads_blobs_relative_to_prefix(fake_gcs, tmp_path):
    blob_cls = fake_gcs["blob_cls"]
    prefix = "synthetic/models/gemma4/e4b-it/v1/"
    fake_gcs["blobs"] = [
        blob_cls(prefix),  # the directory placeholder — must be skipped
        blob_cls(prefix + "config.json"),
        blob_cls(prefix + "model-00001.safetensors"),
        blob_cls(prefix + "tokenizer/tokenizer.json"),  # nested
    ]
    c = VLLMModelClient(
        model_uri=f"gs://my-bucket/{prefix}",
        local_model_dir=str(tmp_path / "model"),
    )
    c._pull_weights()

    assert fake_gcs["list_calls"] == [("my-bucket", prefix)]
    # Downloads stage in a temp dir and are renamed into place (the warm-pull
    # is atomic since the 2026-07-24 SIGBUS postmortem) — assert the final
    # layout, not where the blob API wrote.
    for rel in (
        "config.json",
        "model-00001.safetensors",
        "tokenizer/tokenizer.json",
    ):
        assert (tmp_path / "model" / rel).is_file(), rel


def test_pull_weights_raises_when_no_blobs(fake_gcs, tmp_path):
    fake_gcs["blobs"] = []
    c = VLLMModelClient(
        model_uri="gs://empty-bucket/no/such/prefix/",
        local_model_dir=str(tmp_path / "model"),
    )
    with pytest.raises(RuntimeError, match="No blobs found"):
        c._pull_weights()


# ---------------------------------------------------------------------------
# _split_gs_uri helper.
# ---------------------------------------------------------------------------


def test_split_gs_uri_basic():
    assert _split_gs_uri("gs://bucket/a/b/c/") == ("bucket", "a/b/c/")


def test_split_gs_uri_no_trailing_slash():
    assert _split_gs_uri("gs://bucket/a/b") == ("bucket", "a/b")


def test_split_gs_uri_rejects_non_gs():
    with pytest.raises(ValueError, match="Not a gs"):
        _split_gs_uri("/local/path")


def test_split_gs_uri_rejects_missing_bucket():
    with pytest.raises(ValueError, match="no bucket"):
        _split_gs_uri("gs:///path/only")


# ---------------------------------------------------------------------------
# _assert_dtype_supported — pure function, no torch import required to test.
# ---------------------------------------------------------------------------


def test_assert_dtype_supported_raises_bf16_on_turing():
    with pytest.raises(ModelGpuIncompatibleError, match="qwen3_4b_instruct_2507"):
        _assert_dtype_supported("bfloat16", (7, 5))


def test_assert_dtype_supported_passes_bf16_on_ampere_plus():
    _assert_dtype_supported("bfloat16", (8, 9))  # must not raise


def test_assert_dtype_supported_passes_fp16_on_turing():
    _assert_dtype_supported("float16", (7, 5))  # must not raise


# ---------------------------------------------------------------------------
# setup() — fatal GPU/dtype guard, fires BEFORE the vLLM server spawn.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_torch(monkeypatch):
    """Inject a fake `torch` module reporting a Turing (T4-class) GPU."""

    def _install(capability):
        mod = types.ModuleType("torch")
        mod.cuda = types.SimpleNamespace(
            is_available=lambda: True,
            get_device_capability=lambda: capability,
        )
        monkeypatch.setitem(sys.modules, "torch", mod)
        return mod

    return _install


def test_setup_raises_on_bf16_turing_before_spawn(tmp_path, fake_torch):
    fake_torch((7, 5))  # T4
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}))

    c = VLLMModelClient(
        model_uri="gs://bucket/synthetic/models/gemma4/e4b-it/v1/",
        local_model_dir=str(model_dir),
    )
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server") as spawn,
        pytest.raises(ModelGpuIncompatibleError, match="qwen3_4b_instruct_2507"),
    ):
        c.setup()
    spawn.assert_not_called()


def test_setup_allows_bf16_on_ampere_or_newer(tmp_path, fake_torch):
    fake_torch((8, 9))  # L4 (Ada) — well above the bf16 floor.
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}))

    c = VLLMModelClient(
        model_uri="gs://bucket/synthetic/models/gemma4/e4b-it/v1/",
        local_model_dir=str(model_dir),
    )
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    spawn.assert_called_once()


def test_setup_skips_guard_without_cuda(tmp_path, monkeypatch):
    """No CUDA (e.g. CPU-only worker, or torch absent) → guard is a no-op."""
    mod = types.ModuleType("torch")
    mod.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", mod)

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}))

    c = VLLMModelClient(
        model_uri="gs://bucket/synthetic/models/gemma4/e4b-it/v1/",
        local_model_dir=str(model_dir),
    )
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    spawn.assert_called_once()


def test_setup_raises_for_local_model_uri_distinct_from_local_model_dir(
    tmp_path, fake_torch
):
    """Already-local weights branch: the guard must inspect the directory
    actually served (the local `model_uri`), NOT `local_model_dir` — those
    differ in the documented L4-local-weights workflow, and reading the
    wrong one silently skips the fatal-init guarantee."""
    fake_torch((7, 5))  # T4
    served_dir = tmp_path / "staged-weights"  # dir A — actually served
    served_dir.mkdir()
    (served_dir / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}))
    other_dir = tmp_path / "unrelated"  # dir B — default pull target, unused
    other_dir.mkdir()

    c = VLLMModelClient(model_uri=str(served_dir), local_model_dir=str(other_dir))
    with (
        mock.patch.object(c, "_pull_weights") as pull,
        mock.patch.object(c, "_spawn_server") as spawn,
        # Mocked so a non-raising (buggy) guard fails the assertions below
        # instead of hanging in the real readiness poll loop.
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
        pytest.raises(ModelGpuIncompatibleError, match="qwen3_4b_instruct_2507"),
    ):
        c.setup()
    pull.assert_not_called()  # local path — no GCS pull
    spawn.assert_not_called()  # fatal BEFORE the server spawn


def test_setup_guard_noop_when_config_missing(tmp_path, fake_torch):
    """No config.json (e.g. local-path dev model) → guard can't inspect dtype,
    skip rather than fail closed with no signal."""
    fake_torch((7, 5))  # even on a T4, absence of config.json must not raise.
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    c = VLLMModelClient(model_uri=str(model_dir), local_model_dir=str(model_dir))
    with (
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    spawn.assert_called_once()


# ---------------------------------------------------------------------------
# Real-vLLM behavior is deferred to M1 §11 (L4 only). A placeholder that is
# skipped on the laptop documents that intent.
# ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_real_vllm_roundtrip_deferred_to_m1_section_11():  # pragma: no cover
    pytest.skip(
        "Real vLLM server behavior (GCS pull → spawn → guided JSON) is "
        "validated end-to-end on an L4 at M1 §11; CUDA-only, not runnable "
        "on the laptop."
    )


def test_assert_dtype_supported_fp16_override_allows_bf16_checkpoint_on_turing():
    # Qwen ships bf16 checkpoints but is fp16-safe: an explicit --dtype
    # float16 downcast must pass the guard on SM 7.5.
    _assert_dtype_supported(
        "bfloat16", (7, 5), dtype_override="float16", model_type="qwen3"
    )


def test_assert_dtype_supported_fp16_override_still_rejects_gemma():
    # Gemma in fp16 silently emits empty/pad output — the override must NOT
    # bypass the guard for gemma-family checkpoints.
    with pytest.raises(ModelGpuIncompatibleError, match="empty output"):
        _assert_dtype_supported(
            "bfloat16", (7, 5), dtype_override="float16", model_type="gemma3_text"
        )


def test_setup_fp16_override_spawns_bf16_qwen_on_turing(tmp_path, fake_torch):
    fake_torch((7, 5))  # T4
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps({"torch_dtype": "bfloat16", "model_type": "qwen3"})
    )
    c = VLLMModelClient(
        model_uri=str(model_dir),
        local_model_dir=str(model_dir),
        vllm_server_kwargs={"dtype": "float16"},
    )
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
    ):
        c.setup()
    spawn.assert_called_once()
    cmd = c._server_command()
    assert "--dtype" in cmd and "float16" in cmd


def test_setup_fp16_override_still_fatal_for_gemma_on_turing(tmp_path, fake_torch):
    fake_torch((7, 5))  # T4
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps({"torch_dtype": "bfloat16", "model_type": "gemma3_text"})
    )
    c = VLLMModelClient(
        model_uri=str(model_dir),
        local_model_dir=str(model_dir),
        vllm_server_kwargs={"dtype": "float16"},
    )
    with (
        mock.patch.object(c, "_pull_weights"),
        mock.patch.object(c, "_spawn_server") as spawn,
        mock.patch.object(c, "_wait_until_ready"),
        mock.patch.object(c, "_build_openai_client", return_value=object()),
        pytest.raises(ModelGpuIncompatibleError, match="empty output"),
    ):
        c.setup()
    spawn.assert_not_called()
