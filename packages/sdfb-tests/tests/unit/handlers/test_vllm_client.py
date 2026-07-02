"""Unit tests for `sdfb_beam.handlers.vllm_client.VLLMModelClient`.

These run on the laptop / CI WITHOUT vllm or openai installed. The real
client is CUDA-only and validated end-to-end at M1 §11 on an L4 (those
tests would carry `@pytest.mark.gpu`). Here we mock every heavy boundary:

  - the `openai` chat client (injected as `client._client`),
  - the server subprocess (`subprocess.Popen`),
  - the `google.cloud.storage` client (injected into `sys.modules`),

and assert the contract: the request SHAPE (chat `messages`, `extra_body`
carrying `guided_json` + `chat_template_kwargs.enable_thinking=False` +
`guided_decoding_backend`), JSON parsing, `n` handling, the
not-set-up guard, and teardown subprocess termination.

The class is importable here precisely because all heavy imports are
deferred into method bodies — that property is itself part of the contract
(`test_import_does_not_require_heavy_deps`).
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest
from sdfb_beam.handlers.vllm_client import VLLMModelClient, _split_gs_uri
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


def test_generate_json_extra_body_carries_guided_json_and_thinking_off():
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    c, fake_openai = _client_with_fake_openai(['{"x": 7}'])
    c.generate_json("p", schema)
    extra_body = fake_openai.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra_body["guided_json"] == schema
    assert extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert extra_body["guided_decoding_backend"] == "outlines"


def test_generate_json_passes_sampling_params():
    c, fake_openai = _client_with_fake_openai(['{"a": 1}'])
    c.generate_json("p", {}, max_tokens=512, temperature=0.2, n=1, seed=99)
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.2
    assert kwargs["seed"] == 99


def test_guided_decoding_backend_is_configurable():
    c, fake_openai = _client_with_fake_openai(
        ['{"a": 1}'], guided_decoding_backend="lm-format-enforcer"
    )
    c.generate_json("p", {})
    extra_body = fake_openai.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra_body["guided_decoding_backend"] == "lm-format-enforcer"


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


def test_generate_json_before_setup_raises():
    c = VLLMModelClient(model_uri="gs://bucket/m/v1/")
    with pytest.raises(RuntimeError, match="before setup"):
        c.generate_json("p", {})


def test_generate_json_after_teardown_raises():
    c, _ = _client_with_fake_openai(['{"a": 1}'])
    c.teardown()  # drops the client
    with pytest.raises(RuntimeError, match="before setup"):
        c.generate_json("p", {})


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
    downloaded = sorted(b.downloaded_to for b in fake_gcs["blobs"] if b.downloaded_to)
    assert downloaded == sorted(
        [
            str(tmp_path / "model" / "config.json"),
            str(tmp_path / "model" / "model-00001.safetensors"),
            str(tmp_path / "model" / "tokenizer" / "tokenizer.json"),
        ]
    )


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
