## What & why

<!-- 2-3 sentences; link the ADR/design doc if one exists -->

## Release checklist (drives the post-merge automation)

- [ ] Title is a conventional commit (`feat:`/`fix:`/`perf:`/`docs:`/`chore:`…) —
      it decides the SemVer bump (feat → minor, fix/perf → patch, `!` → major).
      Expected bump: `v_._._`
- [ ] Integration-test artifacts for runs this PR relies on are committed under
      `integration_test/<JOB_ID>/` and the JOB_IDs are listed here: `…`
- [ ] `oss/` bundle leak scan is clean for any newly committed artifacts
- [ ] Docs/ADRs updated (or explicitly not needed)

<!-- After merge: the release Action tags vX.Y.Z and commits
     docs/releases/vX.Y.Z/report.md. Run the release-report skill for the
     insights pass. -->
