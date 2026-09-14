#!/usr/bin/env python
"""Sensitive-content gate for this repository and any tree exported from it.

Fails when a tree carries anything that must never reach a public repo:
forbidden paths (evidence bundles, run journals), secret-shaped strings,
card numbers, IBANs, e-mail addresses outside an allowlist, and tokens
whose salted SHA-256 is listed in the hash file. The hash file
lets the gate recognise known-sensitive identifiers without the repo ever
containing them in plain text.

    uv run python scripts/dsg/precheck.py                 # the repo, tracked files
    uv run python scripts/dsg/precheck.py --root /tmp/export
    uv run python scripts/dsg/precheck.py --hash TOKEN    # a line for the hash file

Exit code 1 when anything is found. Excerpts mask the matched value.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "dsg" / "precheck.yaml"
_MAX_TOKEN_PARTS = 6
_MAX_PHRASE_WORDS = 3
_EXCERPT_CHARS = 120
_IBAN_MIN_LEN, _IBAN_MAX_LEN, _IBAN_MODULUS = 15, 34, 97

_SECRET_PATTERNS = [
    re.compile(p) for p in (
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        r"\"private_key\"\s*:",
        r"gh[pousr]_[A-Za-z0-9]{36}",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
        r"ya29\.[0-9A-Za-z_-]{20,}",
        r"hf_[A-Za-z0-9]{30,}",
        r"sk-(?:ant-)?[A-Za-z0-9_-]{32,}",
        r"glpat-[A-Za-z0-9_-]{20}",
    )
]
_CARD = re.compile(r"(?<![\w-])\d{15,16}(?![\w-])")
_MASKED_CARD = re.compile(r"(?<![\w*])\d{6}\*{6}\d{4}(?![\w*])")
_IBAN = re.compile(
    r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@"
                    r"([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[._/@:_-][a-z0-9]+)*")
_SEPARATOR = re.compile(r"([._/@:_-])")


@dataclasses.dataclass(frozen=True)
class Finding:
  """One hit: where it is, which rule fired, and a masked excerpt."""
  path: str
  line: int
  rule: str
  excerpt: str


@dataclasses.dataclass(frozen=True)
class PrecheckConfig:
  """What the gate forbids and what it has been told to allow."""
  forbidden_globs: Sequence[str] = ()
  email_allow_domains: Sequence[str] = ()
  salt: str = ""
  token_hashes: frozenset[str] = frozenset()
  allow_findings: Sequence[dict] = ()


def hash_token(token: str, salt: str) -> str:
  """Salted SHA-256 of the case-folded token — the hash-file format."""
  return hashlib.sha256((salt + token.casefold()).encode("utf-8")).hexdigest()


def glob_to_regex(pattern: str) -> re.Pattern[str]:
  """`**/` spans directories, `*` stays inside one path segment."""
  out, i = [], 0
  while i < len(pattern):
    if pattern.startswith("**/", i):
      out.append("(?:.*/)?")
      i += 3
    elif pattern.startswith("**", i):
      out.append(".*")
      i += 2
    elif pattern[i] == "*":
      out.append("[^/]*")
      i += 1
    elif pattern[i] == "?":
      out.append("[^/]")
      i += 1
    else:
      out.append(re.escape(pattern[i]))
      i += 1
  return re.compile("".join(out) + r"\Z")


def load_config(path: Path) -> PrecheckConfig:
  """Read the YAML config and the salted hash file it points at."""
  raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
  salt, hashes = "", set()
  hash_file = raw.get("token_hash_file")
  if hash_file:
    for raw_line in (Path(path).parent /
                     hash_file).read_text(encoding="utf-8").splitlines():
      entry = raw_line.strip()
      if entry.startswith("# salt:"):
        salt = entry.split(":", 1)[1].strip()
      elif entry and not entry.startswith("#"):
        hashes.add(entry)
  return PrecheckConfig(
      forbidden_globs=list(raw.get("forbidden_globs", [])),
      email_allow_domains=list(raw.get("email_allow_domains", [])),
      salt=salt,
      token_hashes=frozenset(hashes),
      allow_findings=list(raw.get("allow_findings", [])),
  )


def _luhn_ok(digits: str) -> bool:
  total = 0
  for i, ch in enumerate(reversed(digits)):
    d = int(ch) * (2 if i % 2 else 1)
    total += sum(divmod(d, 10))
  return total % 10 == 0


def _iban_ok(candidate: str) -> bool:
  compact = candidate.replace(" ", "")
  if not _IBAN_MIN_LEN <= len(compact) <= _IBAN_MAX_LEN:
    return False
  rearranged = compact[4:] + compact[:4]
  numeric = "".join(str(int(c, 36)) for c in rearranged)
  return int(numeric) % _IBAN_MODULUS == 1


def _candidates(line: str) -> set[str]:
  """Every normalized token window a hash-file entry could name."""
  folded = line.casefold()
  compounds = _COMPOUND.findall(folded)
  out: set[str] = set()
  for compound in compounds:
    pieces = _SEPARATOR.split(compound)
    parts, seps = pieces[0::2], pieces[1::2]
    for start, _ in enumerate(parts):
      for end in range(start + 1,
                       min(len(parts), start + _MAX_TOKEN_PARTS) + 1):
        text = parts[start]
        for k in range(start + 1, end):
          text += seps[k - 1] + parts[k]
        out.add(text)
  for size in range(2, _MAX_PHRASE_WORDS + 1):
    for start in range(len(compounds) - size + 1):
      out.add(" ".join(compounds[start:start + size]))
  return out


def _mask(line: str, span: tuple[int, int] | None) -> str:
  if span is not None:
    line = line[:span[0]] + "***" + line[span[1]:]
  return line.strip()[:_EXCERPT_CHARS]


def _domain_allowed(domain: str, allow: Sequence[str]) -> bool:
  domain = domain.lower()
  return any(fnmatch.fnmatch(domain, p.lower()) for p in allow)


def scan_text(rel: str, text: str, config: PrecheckConfig) -> list[Finding]:
  """All content findings for one decoded text file."""
  findings: list[Finding] = []
  for number, line in enumerate(text.splitlines(), start=1):

    def hit(rule, span, line=line, number=number):
      findings.append(Finding(rel, number, rule, _mask(line, span)))

    for pattern in _SECRET_PATTERNS:
      for m in pattern.finditer(line):
        hit("secret-pattern", m.span())
    for m in _CARD.finditer(line):
      if _luhn_ok(m.group()):
        hit("card-number", m.span())
    for m in _MASKED_CARD.finditer(line):
      hit("card-number", m.span())
    for m in _IBAN.finditer(line):
      if _iban_ok(m.group()):
        hit("iban", m.span())
    for m in _EMAIL.finditer(line):
      if not _domain_allowed(m.group(1), config.email_allow_domains):
        hit("email", m.span())
    if config.token_hashes:
      matched = [
          c for c in _candidates(line)
          if hash_token(c, config.salt) in config.token_hashes
      ]
      if matched:
        hit("sensitive-token", None)
        findings[-1] = dataclasses.replace(
            findings[-1],
            excerpt=_mask_tokens(line, matched),
        )
  return findings


def _mask_tokens(line: str, tokens: Iterable[str]) -> str:
  masked = line
  for token in sorted(tokens, key=len, reverse=True):
    masked = re.sub(re.escape(token), "***", masked, flags=re.IGNORECASE)
  return masked.strip()[:_EXCERPT_CHARS]


def _list_files(root: Path) -> list[str]:
  """Tracked and untracked-unignored files at a git work-tree top, else a walk."""
  try:
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True).stdout.strip()
  except (OSError, subprocess.CalledProcessError):
    top = ""
  if top and Path(top).resolve() == root.resolve():
    listed = subprocess.run([
        "git", "-C",
        str(root), "ls-files", "-z", "--cached", "--others",
        "--exclude-standard"
    ],
                            capture_output=True,
                            text=True,
                            check=True).stdout
    # Tracked paths deleted in the working tree are not there to scan.
    return sorted(p for p in listed.split("\0") if p and (root / p).is_file())
  files = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d != ".git"]
    for name in filenames:
      files.append(Path(dirpath, name).relative_to(root).as_posix())
  return sorted(files)


def _allowed(finding: Finding, config: PrecheckConfig) -> bool:
  for entry in config.allow_findings:
    if (glob_to_regex(entry["path"]).match(finding.path) and
        entry.get("rule", finding.rule) == finding.rule and
        entry.get("line", finding.line) == finding.line):
      return True
  return False


def scan_tree(root: Path, config: PrecheckConfig) -> list[Finding]:
  """Scan every file under `root`; binary files are skipped."""
  root = Path(root)
  forbidden = [glob_to_regex(g) for g in config.forbidden_globs]
  findings: list[Finding] = []
  for rel in _list_files(root):
    if any(p.match(rel) for p in forbidden):
      findings.append(Finding(rel, 0, "forbidden-path", ""))
      continue
    data = (root / rel).read_bytes()
    if b"\0" in data[:8192]:
      continue
    findings.extend(
        scan_text(rel, data.decode("utf-8", errors="replace"), config))
  return [f for f in findings if not _allowed(f, config)]


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--root", default=".", type=Path)
  parser.add_argument("--config", default=_DEFAULT_CONFIG, type=Path)
  parser.add_argument(
      "--hash",
      nargs="+",
      metavar="TOKEN",
      help="print hash-file lines for TOKEN(s) and exit")
  args = parser.parse_args(argv)
  config = load_config(args.config)
  if args.hash:
    for token in args.hash:
      print(hash_token(token, config.salt))
    return 0
  findings = scan_tree(args.root, config)
  for f in findings:
    print(f"{f.path}:{f.line}: {f.rule}: {f.excerpt}")
  print(f"precheck: {len(findings)} finding(s) under {args.root}")
  return 1 if findings else 0


if __name__ == "__main__":
  sys.exit(main())
