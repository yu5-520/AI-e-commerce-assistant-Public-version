# Live production INFORMATION observations

The deployed Python canonical snapshot materializer now sends the **same source
projection it actually processed** to the packaged Java `CanonicalProductMapper`.
Capture happens after the snapshot transaction commits. Pure mapper calls, CI
replays and cached reads do not invoke the production capture hook. The Java
endpoint receives source data and the data-version context; it never receives
the Python result to copy. Its only result is the independently calculated hash.

## Operation and deployment

Use the existing exact-commit `deploy-ai-release <sealed-main-commit>` workflow.
The existing Java/Python supervisor enables collection and passes the admitted
source commit, release hash, Java contract hash and loopback port to both children.
No system JDK, manual environment edit or second systemd unit is needed.

After deployment, upload/process a report through the normal application to
materialize a new canonical snapshot. Read the observation status on ECS:

```bash
cd /opt/ai-ecommerce-assistant/current && .venv/bin/python src/services/v24_live_information_mirror_service.py
```

This command reads evidence; it does not manufacture traffic or call an LLM.
If no actual materialization has happened it returns `NO_LIVE_SAMPLES`.
The existing `deploy-ai-release status` still reports the installed release.

## Recorded evidence and failures

Records are stored under the existing shared outputs link at
`outputs/v24-live-mirror/<sourceCommit>/<processSession>.jsonl`. Each observation
has a PENDING record followed by MATCH, MISMATCH or an explicit failure outcome.
Both records bind the source/release/Java identities, sample UUID and exact wire
input hash. Records are ordered and hash chained; files have mode 0600. Raw source
data, product titles, report content and credentials are never written to this
journal. The journal provides local corruption detection, not a signature or
remote attestation against an administrator who can rewrite the entire journal.

The worker queue is bounded to 32 requests; request bodies are limited to 512 KiB;
each HTTP operation has a two-second socket timeout. Java is called in a background
worker, not on the business decision path. Queue overflow, timeout, invalid Java
identity, mismatch and unfinished samples remain visible in the status report.
Local journal appends happen at capture time so a killed worker cannot silently
turn a pending sample into a pass. Each session journal is capped at 16 MiB; a
full/unwritable journal stops that collector and emits an error plus a `.blocked`
marker when the filesystem permits. Restarted processes use a new session; old
records are retained and checked by status. Shared-output retention is operational
storage management; this update never deletes observation evidence automatically.

## Deliberate evidence boundary

This update covers one real operation: `INFORMATION:canonical-product`. It is
not full INFORMATION authority coverage, and it does not implement production
INVOCATION, TEMPORAL or MUTATION mirroring. The deployed readiness service still
has no active Authority Generation and no production generation/rollback proof.

Consequently these records use `LIVE_PRODUCTION_INFORMATION_OBSERVATION`, not
`EXTERNAL_PRODUCTION_MIRROR`. They cannot be supplied directly as V24.26 four-domain
receipts: they intentionally have no fabricated generationSeq, fencingToken or
rollback-success fields. Even if every product hash matches, the report keeps
`externalProductionMirrorParityProven=false` and `cutoverAllowed=false`.
Next work must connect real invocation/temporal/mutation operations and a live
generation/rollback protocol before the existing four-domain gate can be used.

## Registry and verification

The capture implementation is registered with the canonical source in
`signal_admission` and `frontend_view`. The existing lifecycle supervisor remains
under `release_governance`. The scoped policy is
`governance/v24/live-information-mirror-update-policy.json`; the existing lineage
workflow selects it and checks exact BASE/TARGET graphs. No compiler or verifier
rule is relaxed. Java sources remain attested and compiled into the sealed JAR;
the Python observer is shipped under the existing runtime source glob.

`tests/test_v22_4_v24_live_information_mirror.py` is collected by the existing
Release Hash Seal test gate. It exercises real Java independent computation,
identity/mismatch failures, rejected result injection, oversize input, corrupt or
incomplete journals, unavailable Java and the actual post-commit Python hook.
Local development can set `V24_TEST_JAVA_HOME` to JDK 17+; release CI uses the
packaged JRE/JAR. Test observations are confined to pytest temporary directories.
