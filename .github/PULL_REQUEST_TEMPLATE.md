## What & why

<!-- 2-3 sentences; link the ADR/design doc if one exists -->

## Release checklist (drives the post-merge automation)

- [ ] Title is a conventional commit (`feat:`/`fix:`/`perf:`/`docs:`/`chore:`…) —
      it decides the SemVer bump (feat → minor, fix/perf → patch, `!` → major).
      Expected bump: `v_._._`
- [ ] Evidence bundles for runs this PR relies on are promoted from local
      `runs/<JOB_ID>/` and committed under
      `docs/releases/<version>/evidence/<JOB_ID>/`; the JOB_IDs are listed
      here: `…`
- [ ] `oss/` bundle leak scan is clean for any newly committed artifacts
- [ ] Docs/ADRs updated (or explicitly not needed)

<!-- After merge: the release Action tags vX.Y.Z and commits
     docs/releases/vX.Y.Z/report.md. Run the release-report skill for the
     insights pass. -->
