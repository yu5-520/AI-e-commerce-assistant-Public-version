# V24 Java environment deployment repair

The release already packages the pinned Temurin JRE, JAR and runtime contract.
This repair connects that package to the existing deployment/startup lifecycle.

## Registry and update scope

`release_governance` in `config/v23_registry_runtime.json` owns
`scripts/start_server.sh`, `scripts/deploy_release.sh`, the existing
`scripts/start_v24_authority.sh`, and `config/deployment/v24_java_lifecycle.py`.
The existing Competition Registry Lineage workflow selects
`governance/v24/java-deployment-lifecycle-update-policy.json` for this repair.
BASE/TARGET compilation and the existing scope verifier remain mandatory.

## Deployment and rollback

1. The existing pinned Root Verifier verifies the candidate bundle.
2. Before the deployment core stops the current service, the lifecycle helper
   verifies the packaged Java file set, hashes, source identity and executable
   version, then starts Java on an isolated loopback port and checks readiness.
   The child is stopped after preflight, including on failure or interruption.
3. The existing deployment core switches the release and starts the same systemd
   unit. The verified startup script starts a supervisor in that unit's cgroup.
4. Java must be ready before the supervisor starts Python/uvicorn. Startup checks
   require READY_NO_AUTHORITY, no generation and no mutation/deployment/removal
   authority. Python continues to own production business execution.
5. Either child exiting makes the supervisor fail and terminate both child process
   groups. SIGTERM stops both groups. The existing unit restart and deployment
   rollback therefore apply to Java and Python together. Each release resolves
   its own embedded JRE; no global Java installation or tool-cache link is used.

The Java service binds only `127.0.0.1`; `V24_AUTHORITY_PORT` defaults to 39024.
An occupied port blocks startup without stopping its owner. Readiness receipts in
deployment output/journal include sourceCommit, releaseHash, contractHash and PID.
The preflight endpoint is a local process probe, not an external business API.

## Verification and boundaries

`tests/test_v22_4_v24_java_deployment_lifecycle.py` follows the existing release
test discovery pattern in `pytest.ini` and covers missing/tampered Java,
commit mismatch, port collision, failed Java startup, Python exit, Java exit and
service stop. Release Hash Seal builds the exact JRE before pytest, enabling an
additional real packaged-Java preflight test. Local runs without a built JRE
explicitly skip that integration test.

This repair establishes environment delivery and joint process lifecycle only.
Production mirror receipts, business authority transfer and legacy-path removal
remain governed by their existing separate gates. Repository/CI success does not
assert that an ECS production deployment or rollback has been performed.
