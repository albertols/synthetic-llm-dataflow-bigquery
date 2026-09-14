#!/usr/bin/env python
"""Replicate a golden-source ref into the Dataflow Solution Guides repo.

The source repo is the only place this solution is edited (ADR 0040). A
sync exports one ref with `git archive` (untracked and ignored files can
never ship), selects files by `dsg/manifest.yaml` from THAT ref, lays the
`dsg/` overlays on top, generates the DSG build files from `uv.lock`, pins
links to documents that are not shipped, replaces the owned DSG paths
wholesale, runs the same gates DSG CI runs, and optionally commits, pushes
the fork branch and opens or updates the PR.

    uv run python scripts/dsg/sync.py --ref v0.4.0 \\
        --dsg ~/IdeaProjects/dataflow-solution-guides --open-pr

Nothing is committed when a gate fails; the gate report is printed and the
DSG checkout is left on the sync branch for inspection.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_TOOL = "scripts/dsg/sync.py"
_LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")
_SCRIPT_REF = re.compile(r'"scripts"((?:\s*/\s*"[^"]+")+)')
_PLACEHOLDER = re.compile(r"\{(ref|sha|short_sha|prev_sha|compare_url|"
                          r"changelog|gates_table|cloud_status|source_repo|"
                          r"pipeline_dir)\}")
_SEMVER_TAG = re.compile(r"v(\d+\.\d+\.\d+)")


def _load_precheck():
  spec = importlib.util.spec_from_file_location("dsg_precheck_for_sync",
                                                _HERE / "precheck.py")
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module  # dataclasses resolve their module by name
  spec.loader.exec_module(module)
  return module


precheck = _load_precheck()


class SyncError(RuntimeError):
  """A condition that must stop the sync before the DSG checkout changes."""


@dataclasses.dataclass(frozen=True)
class IndexRow:
  """One idempotent edit to a DSG index file outside the owned paths.

  With an `anchor`, `text` is a single line that replaces the line holding
  `marker`, or is inserted after the line holding `anchor`. Without one,
  `text` is a block kept between `<!-- dsg-sync:<marker>:start/end -->`.
  """
  file: str
  anchor: str | None
  marker: str
  text: str


@dataclasses.dataclass(frozen=True)
class GateResult:
  name: str
  ok: bool
  seconds: float
  tail: str


@dataclasses.dataclass(frozen=True)
class Manifest:
  """`dsg/manifest.yaml`, read from the exported ref."""
  source_repo: str
  target_repo: str
  target_base: str
  branch: str
  pipeline_dir: str
  owned_paths: Sequence[str]
  preserve: Sequence[str]
  include: Sequence[str]
  exclude: Sequence[str]
  script_tests: dict
  overlays: dict
  readme_header: str | None
  pylintrc: dict
  precheck_config: str
  pr_template: str
  index_rows: Sequence[IndexRow]
  pr_title: str = "feat({pipeline_name}): sync from source {ref}"
  requirements: dict = dataclasses.field(default_factory=lambda: {
      "runtime_package": "sdfb-beam",
      "dev_group": "dev"
  })
  python_version_file: str = ".python-version"

  @classmethod
  def from_dict(cls, raw: dict) -> Manifest:
    raw = dict(raw)
    raw["index_rows"] = [IndexRow(**row) for row in raw.get("index_rows", [])]
    raw.setdefault("script_tests", {})
    raw.setdefault("preserve", [])
    raw.setdefault("readme_header", None)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
      raise SyncError(f"unknown manifest keys: {unknown}")
    return cls(**raw)

  @classmethod
  def load(cls, path: Path) -> Manifest:
    return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

  @property
  def pipeline_name(self) -> str:
    return posixpath.basename(self.pipeline_dir)


def _run(cmd: Sequence[str], cwd: Path | None = None, **kwargs) -> str:
  proc = subprocess.run(
      list(cmd), cwd=cwd, capture_output=True, text=True, check=False, **kwargs)
  if proc.returncode != 0:
    raise SyncError(f"{' '.join(cmd)} failed ({proc.returncode}):\n"
                    f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
  return proc.stdout


def _walk(root: Path) -> list[str]:
  files = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d != ".git"]
    for name in filenames:
      files.append(Path(dirpath, name).relative_to(root).as_posix())
  return sorted(files)


def _matches(rel: str, globs: Sequence[str]) -> bool:
  return any(precheck.glob_to_regex(g).match(rel) for g in globs)


def export_ref(repo: Path, ref: str, dest: Path) -> tuple[str, str]:
  """`git archive` one ref into `dest`; returns (sha, UTC commit time)."""
  sha = _run(["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"]).strip()
  epoch = int(
      _run(["git", "-C",
            str(repo), "log", "-1", "--format=%ct", sha]).strip())
  committed_at = dt.datetime.fromtimestamp(
      epoch, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
  archive = subprocess.run(["git", "-C", str(repo), "archive", sha],
                           capture_output=True,
                           check=True).stdout
  dest.mkdir(parents=True, exist_ok=True)
  with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(dest, filter="data")
  return sha, committed_at


def select_files(export_root: Path, manifest: Manifest) -> list[str]:
  """Source files that ship under the pipeline dir, checked for consistency.

  A shipped test that loads a script which does not ship would fail DSG CI,
  so it stops the sync here instead.
  """
  selected = [
      rel for rel in _walk(export_root) if _matches(rel, manifest.include) and
      not _matches(rel, manifest.exclude)
  ]
  shipped = set(selected)
  pairs = dict(manifest.script_tests)
  for rel in selected:
    if rel.endswith(".py") and "/tests/" in f"/{rel}":
      text = (export_root / rel).read_text(encoding="utf-8", errors="replace")
      for match in _SCRIPT_REF.finditer(text):
        parts = re.findall(r'"([^"]+)"', match.group(1))
        pairs.setdefault(rel, "scripts/" + "/".join(parts))
  missing = sorted(f"{test} loads {script}, which does not ship"
                   for test, script in pairs.items()
                   if test in shipped and script not in shipped)
  if missing:
    raise SyncError("inconsistent manifest:\n  " + "\n  ".join(missing))
  return selected


def _copy(src: Path, dst: Path) -> None:
  dst.parent.mkdir(parents=True, exist_ok=True)
  shutil.copy2(src, dst)


def stage_tree(export_root: Path, manifest: Manifest, staging: Path, *,
               sha: str, ref: str, committed_at: str) -> None:
  """Build the exact DSG-relative tree this ref ships."""
  pipe = staging / manifest.pipeline_dir
  for rel in select_files(export_root, manifest):
    _copy(export_root / rel, pipe / rel)
  for src_prefix, dst_prefix in manifest.overlays.items():
    base = export_root / src_prefix
    if not base.is_dir():
      raise SyncError(f"overlay {src_prefix} is missing from the ref")
    for rel in _walk(base):
      if f"{src_prefix}/{rel}" == manifest.readme_header:
        continue
      _copy(base / rel, staging / dst_prefix / rel)
  if manifest.readme_header:
    readme = pipe / "README.md"
    header = (export_root / manifest.readme_header).read_text(encoding="utf-8")
    readme.write_text(
        header + readme.read_text(encoding="utf-8"), encoding="utf-8")
  stamp = {
      "source_repo": manifest.source_repo,
      "ref": ref,
      "sha": sha,
      "committed_at": committed_at,
      "tool": _TOOL,
  }
  (pipe / ".sync-source.json").write_text(
      json.dumps(stamp, indent=2) + "\n", encoding="utf-8")


def generate_requirements(export_root: Path, manifest: Manifest, staging: Path,
                          sha: str) -> None:
  """DSG CI installs `requirements*.txt` with pipenv; derive them from uv.lock."""
  pipe = staging / manifest.pipeline_dir
  header = (f"# Generated by {_TOOL} from uv.lock at {sha[:12]}. Do not edit:\n"
            "# change pyproject.toml in the golden source and re-sync.\n")
  common = [
      "uv", "export", "--frozen", "--no-hashes", "--no-header", "--no-annotate"
  ]
  runtime = _run([
      *common, "--no-dev", "--package", manifest.requirements["runtime_package"]
  ],
                 cwd=export_root)
  dev = _run([*common, "--only-group", manifest.requirements["dev_group"]],
             cwd=export_root)
  (pipe / "requirements.txt").write_text(header + runtime, encoding="utf-8")
  (pipe / "requirements-dev.txt").write_text(header + dev, encoding="utf-8")


def rewrite_links(root: Path,
                  dsg_root: Path,
                  manifest: Manifest,
                  *,
                  sha: str,
                  export_root: Path | None = None,
                  scope: Sequence[str] | None = None) -> list[str]:
  """Pin relative links whose target does not ship; return broken ones.

  A link resolves when its target exists in `root` or already in the DSG
  checkout. Otherwise a target inside the pipeline dir becomes a URL to
  the golden source at `sha`; anything else is reported as broken.
  """
  broken: list[str] = []
  pipe_prefix = manifest.pipeline_dir + "/"
  for md in sorted(root.rglob("*.md")):
    rel_md = md.relative_to(root).as_posix()
    if scope is not None and not any(rel_md == s or rel_md.startswith(s + "/")
                                     for s in scope):
      continue
    lines = md.read_text(encoding="utf-8").splitlines(keepends=True)
    in_fence = False
    for i, line in enumerate(lines):
      if line.lstrip().startswith("```"):
        in_fence = not in_fence
        continue
      if in_fence:
        continue

      def repl(m, rel_md=rel_md):
        target = m.group(2)
        if "://" in target or target.startswith(("#", "mailto:")):
          return m.group(0)
        path, _, anchor = target.partition("#")
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(rel_md), path))
        if not resolved.startswith("../") and ((root / resolved).exists() or
                                               (dsg_root / resolved).exists()):
          return m.group(0)
        source_rel = resolved[len(pipe_prefix):] if resolved.startswith(
            pipe_prefix) else None
        if source_rel is not None and (export_root is None or
                                       (export_root / source_rel).exists()):
          url = f"{manifest.source_repo}/blob/{sha}/{source_rel}"
          return m.group(1) + url + (f"#{anchor}"
                                     if anchor else "") + m.group(3)
        broken.append(f"{rel_md} -> {target}")
        return m.group(0)

      lines[i] = _LINK.sub(repl, line)
    new_text = "".join(lines)
    if new_text != md.read_text(encoding="utf-8"):
      md.write_text(new_text, encoding="utf-8")
  return broken


def apply_index_rows(dsg_root: Path, rows: Sequence[IndexRow]) -> list[str]:
  """Apply each row idempotently; return the files that changed."""
  changed: list[str] = []
  for row in rows:
    path = dsg_root / row.file
    old = path.read_text(encoding="utf-8")
    if row.anchor is None:
      start = f"<!-- dsg-sync:{row.marker}:start -->"
      end = f"<!-- dsg-sync:{row.marker}:end -->"
      block = f"{start}\n{row.text.rstrip()}\n{end}"
      if start in old:
        head, _, rest = old.partition(start)
        _, _, tail = rest.partition(end)
        new = head + block + tail
      else:
        new = old.rstrip("\n") + "\n\n" + block + "\n"
    else:
      lines = old.splitlines(keepends=True)
      hits = [i for i, line in enumerate(lines) if row.marker in line]
      if len(hits) > 1:
        raise SyncError(f"{row.file}: marker {row.marker!r} is not unique")
      if hits:
        lines[hits[0]] = row.text + "\n"
      else:
        anchors = [i for i, line in enumerate(lines) if row.anchor in line]
        if not anchors:
          raise SyncError(f"{row.file}: anchor {row.anchor!r} not found — "
                          "the DSG index changed; update dsg/manifest.yaml")
        lines.insert(anchors[0] + 1, row.text + "\n")
      new = "".join(lines)
    if new != old:
      path.write_text(new, encoding="utf-8")
      if row.file not in changed:
        changed.append(row.file)
  return changed


def install_into(dsg_root: Path, staging: Path, manifest: Manifest) -> None:
  """Replace every owned path wholesale, keeping the preserved files."""
  kept = {
      rel: (dsg_root / rel).read_bytes()
      for rel in manifest.preserve
      if (dsg_root / rel).is_file()
  }
  for owned in manifest.owned_paths:
    target, source = dsg_root / owned, staging / owned
    if target.is_dir():
      shutil.rmtree(target)
    elif target.exists():
      target.unlink()
    if source.is_dir():
      shutil.copytree(source, target)
    elif source.exists():
      _copy(source, target)
  for rel, data in kept.items():
    (dsg_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (dsg_root / rel).write_bytes(data)


def _sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def check_pylintrc(export_root: Path, dsg_root: Path,
                   manifest: Manifest) -> GateResult:
  vendored = export_root / manifest.pylintrc["vendored"]
  target = dsg_root / manifest.pylintrc["target"]
  ok = vendored.exists() and target.exists() and _sha256(vendored) == _sha256(
      target)
  tail = "" if ok else (f"{manifest.pylintrc['vendored']} differs from DSG "
                        f"{manifest.pylintrc['target']}: re-vendor it and "
                        "re-lint the source")
  return GateResult("pylintrc-parity", ok, 0.0, tail)


def changelog_section(text: str, ref: str) -> str:
  """The CHANGELOG body for a `vX.Y.Z` tag, else the [Unreleased] block."""
  semver = _SEMVER_TAG.fullmatch(ref)
  heading = f"## [{semver.group(1)}]" if semver else "## [Unreleased]"
  out, capturing = [], False
  for line in text.splitlines():
    if line.startswith("## ["):
      if capturing:
        break
      capturing = line.startswith(heading)
      continue
    if capturing:
      out.append(line)
  return "\n".join(out).strip()


def render_pr_body(template: str, *, manifest: Manifest, ref: str, sha: str,
                   prev_sha: str | None, gates: Sequence[GateResult],
                   changelog: str, cloud_run: str | None) -> str:
  compare_url = (f"{manifest.source_repo}/compare/{prev_sha}...{sha}"
                 if prev_sha else f"{manifest.source_repo}/tree/{sha}")
  table = ["| Gate | Result | Seconds |", "| :-- | :-: | --: |"]
  table += [
      f"| {g.name} | {'✅' if g.ok else '❌'} | {g.seconds:.0f} |" for g in gates
  ]
  if cloud_run:
    cloud_status = f"Verified on Dataflow for this ref: {cloud_run}"
  else:
    cloud_status = (
        "> [!IMPORTANT]\n> **Not yet run in Google Cloud** for this sync. "
        "The gates above are local and CI-equivalent; a live Dataflow run "
        "of `scripts/04_run_dataflow.sh` is pending.")
  values = {
      "ref": ref,
      "sha": sha,
      "short_sha": sha[:12],
      "prev_sha": prev_sha or "(first sync)",
      "compare_url": compare_url,
      "changelog": changelog or "_No changelog entry for this ref._",
      "gates_table": "\n".join(table),
      "cloud_status": cloud_status,
      "source_repo": manifest.source_repo,
      "pipeline_dir": manifest.pipeline_dir,
  }
  return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


# --------------------------------------------------------------------------
# Gates — the same checks DSG CI runs, executed against the DSG checkout.
# --------------------------------------------------------------------------


def _timed(name: str, fn) -> GateResult:
  start = time.monotonic()
  try:
    tail = fn() or ""
    ok = True
  except SyncError as exc:
    tail, ok = str(exc)[-3000:], False
  return GateResult(name, ok, time.monotonic() - start, tail)


def _gate_precheck(export_root: Path, dsg_root: Path, manifest: Manifest):
  config = precheck.load_config(export_root / manifest.precheck_config)
  findings = []
  for owned in manifest.owned_paths:
    path = dsg_root / owned
    if path.is_dir():
      findings += [
          dataclasses.replace(f, path=f"{owned}/{f.path}")
          for f in precheck.scan_tree(path, config)
      ]
    elif path.is_file():
      findings += precheck.scan_text(owned, path.read_text(encoding="utf-8"),
                                     config)
  if findings:
    raise SyncError("\n".join(
        f"{f.path}:{f.line}: {f.rule}: {f.excerpt}" for f in findings))


def _gate_links(dsg_root: Path, manifest: Manifest, sha: str):
  broken = rewrite_links(
      dsg_root, dsg_root, manifest, sha=sha, scope=manifest.owned_paths)
  if broken:
    raise SyncError("broken links:\n  " + "\n  ".join(broken))


def _gate_shell(dsg_root: Path, manifest: Manifest):
  for owned in manifest.owned_paths:
    for script in sorted(
        (dsg_root / owned).rglob("*.sh")) if (dsg_root /
                                              owned).is_dir() else []:
      _run(["bash", "-n", str(script)])


def _gate_style(dsg_root: Path, manifest: Manifest):
  pipe = dsg_root / manifest.pipeline_dir
  diff = _run([
      sys.executable, "-m", "yapf", "--diff", "--recursive", "--style", "yapf",
      "--exclude", "**/.venv/**", "."
  ],
              cwd=pipe)
  if diff.strip():
    raise SyncError("yapf would reformat:\n" + diff[-3000:])
  return _run([
      sys.executable, "-m", "pylint", "--rcfile", "../pylintrc",
      "--ignore=.venv,venv,.gradle,build,bin,third_party", "."
  ],
              cwd=pipe)[-300:]


def _gate_terraform(dsg_root: Path, manifest: Manifest):
  terraform = os.environ.get("TERRAFORM_BIN", "terraform")
  tf_dirs = [p for p in manifest.owned_paths if p.startswith("terraform/")]
  with tempfile.TemporaryDirectory() as tmp:
    for rel in [*tf_dirs, manifest.pipeline_dir]:
      shutil.copytree(
          dsg_root / rel,
          Path(tmp) / rel,
          ignore=shutil.ignore_patterns(".venv", ".terraform*"))
    for rel in tf_dirs:
      cwd = Path(tmp) / rel
      _run([terraform, "fmt", "-check", "-recursive"], cwd=cwd)
      _run([terraform, "init", "-backend=false", "-input=false"], cwd=cwd)
      _run([terraform, "validate"], cwd=cwd)
      if (cwd / "tests").is_dir():
        _run([terraform, "test"], cwd=cwd)


def _gate_python_build(dsg_root: Path, manifest: Manifest):
  """DSG CI's python-build job, step for step, in a throwaway copy."""
  with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / manifest.pipeline_name
    shutil.copytree(
        dsg_root / manifest.pipeline_dir,
        work,
        ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    version = (work / manifest.python_version_file).read_text(
        encoding="utf-8").strip()
    env = dict(
        os.environ, PIPENV_VENV_IN_PROJECT="1", PIPENV_IGNORE_VIRTUALENVS="1")
    env.pop("VIRTUAL_ENV", None)
    _run(["pipenv", "--python", version, "install"], cwd=work, env=env)
    for req in sorted(work.glob("requirements*.txt")):
      _run(["pipenv", "install", "-r", req.name], cwd=work, env=env)
    _run(["pipenv", "run", "pip", "install", "setuptools"], cwd=work, env=env)
    _run(["pipenv", "run", "python", "setup.py", "sdist"], cwd=work, env=env)
    if (work / "tests").is_dir():
      _run(["pipenv", "run", "pytest", "tests/", "-v"],
           cwd=work,
           env=dict(env, PYTHONPATH="."))
    _run(["pipenv", "run", "python", "-m", "compileall", "-q", "."],
         cwd=work,
         env=env)


def run_gates(export_root: Path, dsg_root: Path, manifest: Manifest, *,
              sha: str, level: str) -> list[GateResult]:
  """`none` < `fast` (no python build) < `full` (everything DSG CI runs)."""
  if level == "none":
    return []
  gates = [
      _timed("precheck",
             lambda: _gate_precheck(export_root, dsg_root, manifest)),
      check_pylintrc(export_root, dsg_root, manifest),
      _timed("links", lambda: _gate_links(dsg_root, manifest, sha)),
      _timed("bash -n", lambda: _gate_shell(dsg_root, manifest)),
      _timed("yapf + pylint", lambda: _gate_style(dsg_root, manifest)),
      _timed("terraform", lambda: _gate_terraform(dsg_root, manifest)),
  ]
  if level == "full":
    gates.append(
        _timed("python build (pipenv + sdist + pytest)",
               lambda: _gate_python_build(dsg_root, manifest)))
  return gates


# --------------------------------------------------------------------------
# Git + GitHub.
# --------------------------------------------------------------------------


def prepare_branch(dsg_root: Path, manifest: Manifest) -> str:
  """Reset the one sync branch onto the DSG base branch.

  A single branch carries every sync: while its PR is open a re-sync updates
  that PR; once it has merged, the next sync opens a new one.
  """
  if _run(["git", "-C", str(dsg_root), "status", "--porcelain"]).strip():
    raise SyncError(f"{dsg_root} has uncommitted changes")
  branch = manifest.branch
  _run(["git", "-C", str(dsg_root), "fetch", "upstream", manifest.target_base])
  _run([
      "git", "-C",
      str(dsg_root), "switch", "-C", branch, f"upstream/{manifest.target_base}"
  ])
  return branch


def read_prev_sha(dsg_root: Path, manifest: Manifest) -> str | None:
  stamp = dsg_root / manifest.pipeline_dir / ".sync-source.json"
  if not stamp.exists():
    return None
  return json.loads(stamp.read_text(encoding="utf-8")).get("sha")


def commit(dsg_root: Path, manifest: Manifest, *, ref: str, sha: str,
           extra_files: Sequence[str]) -> bool:
  """Commit the synced paths under the checkout's own git identity.

  The DSG is a Google-owned repository: the message carries the source ref
  and nothing else, no co-author or tool trailers (ADR 0040).
  """
  tracked = set(_run(["git", "-C", str(dsg_root), "ls-files"]).splitlines())
  paths = [
      p for p in [*manifest.owned_paths, *extra_files]
      if (dsg_root / p).exists() or p in tracked or any(
          t.startswith(p + "/") for t in tracked)
  ]
  _run(["git", "-C", str(dsg_root), "add", "-A", "--", *paths])
  if not _run(["git", "-C",
               str(dsg_root), "diff", "--cached", "--name-only"]).strip():
    return False
  message = (f"feat({manifest.pipeline_name}): sync from source {ref} "
             f"({sha[:12]})\n\nSource: {manifest.source_repo}/tree/{sha}\n")
  _run(["git", "-C", str(dsg_root), "commit", "-q", "-m", message])
  return True


def push_and_open_pr(dsg_root: Path, manifest: Manifest, *, branch: str,
                     title: str, body: str) -> str:
  # Refresh the lease: the fork may still hold this branch from an earlier sync.
  subprocess.run(
      ["git", "-C", str(dsg_root), "fetch", "origin", branch],
      capture_output=True,
      check=False)
  _run([
      "git", "-C",
      str(dsg_root), "push", "--force-with-lease", "-u", "origin", branch
  ])
  owner = _run(["gh", "api", "user", "--jq", ".login"]).strip()
  existing = _run([
      "gh", "pr", "list", "-R", manifest.target_repo, "--head", branch,
      "--state", "open", "--json", "url", "--jq", ".[0].url // empty"
  ]).strip()
  with tempfile.NamedTemporaryFile(
      "w", suffix=".md", delete=False, encoding="utf-8") as fh:
    fh.write(body)
  if existing:
    _run([
        "gh", "pr", "edit", existing, "-R", manifest.target_repo, "--title",
        title, "--body-file", fh.name
    ])
    return existing
  return _run([
      "gh", "pr", "create", "-R", manifest.target_repo, "--base",
      manifest.target_base, "--head", f"{owner}:{branch}", "--title", title,
      "--body-file", fh.name
  ]).strip()


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--ref", required=True, help="tag, branch or sha")
  parser.add_argument("--dsg", required=True, type=Path, help="DSG checkout")
  parser.add_argument("--source", type=Path, default=_REPO_ROOT)
  parser.add_argument(
      "--gates", choices=["none", "fast", "full"], default="full")
  parser.add_argument(
      "--no-branch",
      action="store_true",
      help="stage onto the current DSG branch")
  parser.add_argument("--commit", action="store_true")
  parser.add_argument(
      "--open-pr",
      action="store_true",
      help="commit, push the fork branch, open/update the PR")
  parser.add_argument("--cloud-run", help="URL/id of a verifying Dataflow job")
  args = parser.parse_args(argv)
  dsg_root = args.dsg.expanduser().resolve()

  with tempfile.TemporaryDirectory(prefix="dsg-sync-") as tmp:
    export_root, staging = Path(tmp, "export"), Path(tmp, "staging")
    sha, committed_at = export_ref(args.source, args.ref, export_root)
    manifest = Manifest.load(export_root / "dsg" / "manifest.yaml")
    stage_tree(
        export_root,
        manifest,
        staging,
        sha=sha,
        ref=args.ref,
        committed_at=committed_at)
    generate_requirements(export_root, manifest, staging, sha)
    config = precheck.load_config(export_root / manifest.precheck_config)
    findings = precheck.scan_tree(staging, config)
    if findings:
      for f in findings:
        print(f"{f.path}:{f.line}: {f.rule}: {f.excerpt}")
      raise SyncError(f"precheck: {len(findings)} finding(s); nothing synced")
    branch = None if args.no_branch else prepare_branch(dsg_root, manifest)
    prev_sha = read_prev_sha(dsg_root, manifest)
    broken = rewrite_links(
        staging, dsg_root, manifest, sha=sha, export_root=export_root)
    if broken:
      raise SyncError("broken links:\n  " + "\n  ".join(broken))
    install_into(dsg_root, staging, manifest)
    index_files = apply_index_rows(dsg_root, manifest.index_rows)
    gates = run_gates(
        export_root, dsg_root, manifest, sha=sha, level=args.gates)
    for gate in gates:
      print(f"[{'PASS' if gate.ok else 'FAIL'}] {gate.name} "
            f"({gate.seconds:.0f}s)")
      if not gate.ok:
        print(gate.tail)
    if not all(g.ok for g in gates):
      return 1
    changelog_path = export_root / "CHANGELOG.md"
    changelog = changelog_section(
        changelog_path.read_text(
            encoding="utf-8"), args.ref) if changelog_path.exists() else ""
    body = render_pr_body(
        (export_root / manifest.pr_template).read_text(encoding="utf-8"),
        manifest=manifest,
        ref=args.ref,
        sha=sha,
        prev_sha=prev_sha,
        gates=gates,
        changelog=changelog,
        cloud_run=args.cloud_run)
    print(body)
    if args.commit or args.open_pr:
      commit(dsg_root, manifest, ref=args.ref, sha=sha, extra_files=index_files)
    if args.open_pr:
      title = manifest.pr_title.format(
          pipeline_name=manifest.pipeline_name, ref=args.ref)
      print(
          push_and_open_pr(
              dsg_root,
              manifest,
              branch=branch or
              _run(["git", "-C",
                    str(dsg_root), "branch", "--show-current"]).strip(),
              title=title,
              body=body))
  return 0


if __name__ == "__main__":
  try:
    sys.exit(main())
  except SyncError as error:
    print(f"sync stopped: {error}", file=sys.stderr)
    sys.exit(2)
