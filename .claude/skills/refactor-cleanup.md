---
name: refactor-cleanup
description: Recipe to follow whenever deleting or renaming any file, symbol, or image tag. Catches doc / code drift before it ships. Load when you're about to remove or rename anything that other files might reference.
---

# Skill — refactor cleanup

Whenever you delete a file, rename a symbol, retire an image tag, or move content between docs: assume the rest of the repo still references the old name. Confirm; don't guess.

## Workflow

1. **Grep project-wide BEFORE deleting** — find every reference to the about-to-be-removed identifier:

   ```bash
   grep -rn "<removed-name>" \
       --include='*.md' --include='*.yaml' --include='*.yml' \
       --include='*.py' --include='*.toml' --include='*.sh' --include='*.json' \
       --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__
   ```

2. **Update every match** in the same commit as the deletion / rename. Don't leave "I'll fix it later" — drift compounds.

3. **Register the removed name in `.github/drift-check.txt`** so CI catches future regressions. Format: one literal string per line; comments start with `#`. Group new entries under a dated comment block (e.g. `# 2026-05-19 — <reason>`).

4. **Maintenance**: entries that have been clean for ≥ 1 month can be removed from `.github/drift-check.txt`. The goal is to catch fresh drift, not maintain a memorial.

5. **Historical references in ADRs are OK** — `docs/adr/` is excluded from the CI grep by design, because past decisions legitimately mention what was removed and why.

## Why this skill exists

After the M1 §10 rewrite (single image, CI-driven builds) on 2026-05-19, several docs still referenced the retired GPU Dockerfile + local build scripts. The user flagged it. This recipe + the CI drift check is the durable fix.

## Mechanisms — context-token cost recap

| Mechanism | Token cost | When it fires | Defense level |
|---|---|---|---|
| `.github/workflows/ci.yml` drift step | 0 (CI-side) | every push / PR | hard gate |
| `.github/drift-check.txt` patterns | 0 unless read | static config | the list of forbidden strings |
| This skill | ~0 until loaded | when I deliberately load it | reminder for me |
| CLAUDE.md anti-pattern line | ~1 line | always loaded | sets expectations |

## See also

- [ADR 0007 (DRY)](../../docs/adr/0007-dry-documentation-policy.md) — link-don't-duplicate.
- The drift-check workflow step in `.github/workflows/ci.yml`.
