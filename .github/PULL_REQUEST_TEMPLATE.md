## What & why

<!-- 2-3 sentences; link the ADR/design doc if one exists -->

## Release checklist (drives the post-merge automation)

- [ ] Title is a conventional commit (`feat:`/`fix:`/`perf:`/`docs:`/`chore:`…) —
      it decides the SemVer bump (feat → minor, fix/perf → patch, `!` → major).
      Expected bump: `v_._._`
- [ ] Runs this PR relies on are listed by JOB_ID here: `…`. Their evidence
      bundles stay local in `runs/<JOB_ID>/`; only aggregate numbers reach
      the repo (`scripts/dsg/precheck.py` rejects `**/evidence/**`)
- [ ] `uv run python scripts/dsg/precheck.py` is clean
- [ ] Docs/ADRs updated (or explicitly not needed)

<!-- After merge: the release Action tags vX.Y.Z and commits
     docs/releases/vX.Y.Z/report.md. Run the release-report skill for the
     insights pass. -->
