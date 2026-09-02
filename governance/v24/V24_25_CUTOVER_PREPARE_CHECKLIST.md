# V24.25 Cutover Prepare Checklist

Required in this phase:

- [x] Four authority domains registered.
- [x] Four authority domains Root-bound.
- [x] Domain adapters cannot rotate Authority Generation.
- [x] Generation admission occurs before domain execution.
- [x] Generation is rechecked after domain execution.
- [x] Legacy deterministic semantic parity is tested.
- [x] Existing Python/Java mirror parity gate is rerun.
- [x] Old Root generation is rejected across all four domains.
- [x] Fresh Root generation is accepted across all four domains.
- [x] Prepare/rollback leaves production owner snapshot unchanged.
- [x] Production mutation remains disabled.
- [x] Authority grant creation remains disabled.

Deferred to the next phase:

- [ ] External production mirror traffic replay under the Root-bound adapters.
- [ ] Time-windowed parity/SLO evidence.
- [ ] Rollback window and stale-request drain proof against the live production lane.
- [ ] Production Authority Owner transfer.
- [ ] Legacy production path removal.
