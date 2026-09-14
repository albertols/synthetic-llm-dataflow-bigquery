"""Unit tests for `scripts/dsg/precheck.py` — the sensitive-content gate.

The gate runs on every PR and on any exported tree before it is
published, so a false negative ships data to a public repo. Every rule gets a
positive and a negative case; sensitive tokens are exercised through a
hash computed here from a FAKE token, never a real one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "dsg" / "precheck.py"
_spec = importlib.util.spec_from_file_location("dsg_precheck", _SCRIPT)
precheck = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = precheck
_spec.loader.exec_module(precheck)

_SALT = "test-salt"


def _config(**overrides):
  base = {
      "forbidden_globs": ["**/evidence/**", "runs/**", "**/*.jsonl"],
      "email_allow_domains": ["example.com", "*.example.org"],
      "salt": _SALT,
      "token_hashes": frozenset(),
      "allow_findings": [],
  }
  base.update(overrides)
  return precheck.PrecheckConfig(**base)


def _write(root: Path, rel: str, text: str) -> None:
  path = root / rel
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")


def _rules(findings):
  return sorted({f.rule for f in findings})


def test_clean_tree_has_no_findings(tmp_path):
  _write(tmp_path, "src/app.py", "print('hello')\n")
  _write(tmp_path, "README.md", "Contact: alice@example.com\n")
  assert not precheck.scan_tree(tmp_path, _config())


def test_forbidden_paths_are_reported_without_reading_them(tmp_path):
  _write(tmp_path, "docs/releases/v1/evidence/real/m.json", "{}")
  _write(tmp_path, "runs/job/x.txt", "x")
  _write(tmp_path, "journal/runs.jsonl", "{}")
  findings = precheck.scan_tree(tmp_path, _config())
  assert _rules(findings) == ["forbidden-path"]
  assert len(findings) == 3


def test_secret_patterns(tmp_path):
  _write(tmp_path, "a.env", "KEY=AKIA" + "ABCDEFGHIJKLMNOP\n")
  _write(tmp_path, "b.pem", "-----BEGIN " + "PRIVATE KEY-----\n")
  _write(tmp_path, "c.py", 'token = "ghp_' + "a" * 36 + '"\n')
  findings = precheck.scan_tree(tmp_path, _config())
  assert _rules(findings) == ["secret-pattern"]
  assert len(findings) == 3


def test_luhn_valid_card_numbers_are_flagged_and_invalid_ones_are_not(tmp_path):
  # 4111111111111111 is the canonical Luhn-valid test PAN.
  _write(tmp_path, "valid.md", "pan 4111111111111111 here\n")
  _write(tmp_path, "invalid.md", "id 4111111111111112 here\n")
  _write(tmp_path, "masked.md", "pan 411111******1111 here\n")
  _write(tmp_path, "jobid.md", "job 2026-08-26_05_01_16-3186876581127148459\n")
  findings = precheck.scan_tree(tmp_path, _config())
  assert sorted(f.path for f in findings) == ["masked.md", "valid.md"]
  assert _rules(findings) == ["card-number"]


def test_iban_needs_a_valid_checksum(tmp_path):
  _write(tmp_path, "ok.md", "iban GB82 WEST 1234 5698 7654 32\n")
  _write(tmp_path, "bad.md", "iban GB00 WEST 1234 5698 7654 32\n")
  findings = precheck.scan_tree(tmp_path, _config())
  assert [f.path for f in findings] == ["ok.md"]
  assert _rules(findings) == ["iban"]


def test_emails_outside_the_allowlist(tmp_path):
  _write(tmp_path, "a.md", "x@example.com y@sub.example.org\n")
  _write(tmp_path, "b.md", "someone@gmail.com\n")
  findings = precheck.scan_tree(tmp_path, _config())
  assert [f.path for f in findings] == ["b.md"]
  assert _rules(findings) == ["email"]


def test_hashed_tokens_match_compound_and_phrase_forms(tmp_path):
  hashes = frozenset(
      precheck.hash_token(t, _SALT)
      for t in ["zz9999", "acme.example", "com/acme/gpu", "refund q", "acme_limit_key"])
  _write(tmp_path, "a.py", "value = fn(ZZ9999)  # head\n")
  _write(tmp_path, "b.md", "proxy for acme.example networks\n")
  _write(tmp_path, "c.md", "image repo/com/acme/gpu:12.2\n")
  _write(tmp_path, "d.md", "top value 'REFUND Q' at 31%\n")
  _write(tmp_path, "e.md", "unrelated zz99999 and acme alone\n")
  _write(tmp_path, "f.py", "def test_acme_limit_key_shape():\n")
  findings = precheck.scan_tree(tmp_path, _config(token_hashes=hashes))
  assert sorted(f.path for f in findings) == ["a.py", "b.md", "c.md", "d.md", "f.py"]
  assert _rules(findings) == ["sensitive-token"]


def test_excerpts_never_echo_the_matched_value(tmp_path):
  hashes = frozenset({precheck.hash_token("zz9999", _SALT)})
  _write(tmp_path, "a.py", "value = ZZ9999\n")
  (finding,) = precheck.scan_tree(tmp_path, _config(token_hashes=hashes))
  assert "ZZ9999" not in finding.excerpt
  assert finding.line == 1


def test_binary_files_are_skipped(tmp_path):
  (tmp_path / "img.png").write_bytes(b"\x89PNG\x00\x00 4111111111111111")
  assert not precheck.scan_tree(tmp_path, _config())


def test_allow_findings_suppress_a_reviewed_hit(tmp_path):
  _write(tmp_path, "docs/test_cards.md", "pan 4111111111111111\n")
  config = _config(allow_findings=[{
      "path": "docs/test_cards.md",
      "rule": "card-number"
  }])
  assert not precheck.scan_tree(tmp_path, config)


def test_load_config_reads_yaml_and_hash_file(tmp_path):
  (tmp_path / "hashes.txt").write_text(
      "# salt: s1\n" + precheck.hash_token("zz9999", "s1") + "\n",
      encoding="utf-8")
  (tmp_path / "precheck.yaml").write_text(
      "forbidden_globs: ['runs/**']\n"
      "email_allow_domains: [example.com]\n"
      "token_hash_file: hashes.txt\n",
      encoding="utf-8")
  config = precheck.load_config(tmp_path / "precheck.yaml")
  assert config.salt == "s1"
  assert precheck.hash_token("zz9999", "s1") in config.token_hashes
  assert config.forbidden_globs == ["runs/**"]


def test_main_exit_code(tmp_path, capsys):
  (tmp_path / "precheck.yaml").write_text(
      "forbidden_globs: ['runs/**']\n", encoding="utf-8")
  _write(tmp_path / "tree", "ok.md", "fine\n")
  assert precheck.main([
      "--root",
      str(tmp_path / "tree"), "--config",
      str(tmp_path / "precheck.yaml")
  ]) == 0
  _write(tmp_path / "tree", "runs/x.txt", "x")
  assert precheck.main([
      "--root",
      str(tmp_path / "tree"), "--config",
      str(tmp_path / "precheck.yaml")
  ]) == 1
  assert "forbidden-path" in capsys.readouterr().out
