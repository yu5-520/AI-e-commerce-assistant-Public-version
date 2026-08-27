# V24.16-V24.17 Frontend Authority + SSE

## Scope

Phase4 does not redesign the UI and does not move Agent or business reasoning into Java. It isolates the deterministic frontend publication contract so the browser can consume one authoritative Head and immutable content-hash modules.

## Frozen production baseline

The current production path remains Python + SQLite + browser JavaScript during Phase4 SHADOW verification.

Current Head behavior:

```text
GET /api/view/head/{view_key}
  -> calculate RuntimeStateHash
  -> compare current stored Head
  -> if missing/stale/failed, materialize frontend views
  -> write frontend_view_head_v2259
  -> return Head
```

Therefore the current Head GET is not a pure read. The browser already has useful immutable hash caching for manifests/modules, but it still requests the mutable Head before module reads and there is no EventSource/SSE authority.

## V24.16 target authority

```text
Domain State Change
  -> View Projection Trigger
  -> Build immutable module artifacts
  -> Build immutable Manifest
  -> generation fence
  -> Head compare-and-set
  -> publish one new Head
```

The browser read path becomes conceptually:

```text
GET Head
  -> pure read only
```

Phase4 Java verifies:

- immutable module content hashes
- immutable manifest identity
- `headVersion` compare-and-set
- a single winner when two writers publish the same expected Head version
- stale generation rejection
- Head reads do not build, publish, increment a version, or mutate state

## V24.17 SSE target authority

A successful changed Head publication produces exactly one event:

```text
event: view-head-changed
id: <manifestHash>
data: {
  headVersion,
  manifestHash,
  runtimeStateHash,
  generationSeq,
  generationHash,
  changedModules
}
```

Rules:

- the SSE event ID is the ManifestHash
- duplicate publication of the same ManifestHash emits no event
- CAS conflicts emit no event
- stale-generation writes emit no event
- event payload carries hashes/references, not duplicated business payloads
- the browser compares the new ManifestHash and keeps cached modules whose content hashes did not change

## Phase4 verification scenario

The deterministic verifier performs:

1. initial six-module publication
2. repeated pure Head reads and state-hash comparison
3. duplicate Manifest publication and SSE suppression
4. one-module (`products`) change and exact changed-module isolation
5. two writers with the same expected Head version; exactly one wins CAS
6. Runtime Generation rotation followed by an old-generation publish; result must be `STALE_GENERATION`
7. SSE frame and latest Head/Manifest alignment checks

## Production boundary

Phase4 is intentionally SHADOW:

```text
Python production View writer        unchanged
Browser JavaScript runtime           unchanged
Java production View cutover         disabled
Network SSE cutover                  disabled
```

This avoids creating a temporary Python SSE authority that would need to be migrated again. The next production cutover should connect the already-verified Java Frontend Authority to the runtime state/event source, then expose the Java-owned pure Head read and SSE stream as one migration.
