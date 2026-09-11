# V26.4 System Review

V26.4 extends the V26.3 product lifecycle beyond pre-Agent admission into post-execution observation and review.

## Boundary

The system does **not** ask an LLM to write another review report when deterministic evidence is enough.

Agent2 remains the sole model-side owner of `plan.*`, including:

- `plan.review_window`
- `plan.expected_trend`
- `plan.lower_guard`
- `plan.upper_guard`
- `plan.minimum_evidence`
- `plan.acceptance_criteria`
- `plan.risk_boundaries`

Java does not rewrite those fields. `ReviewContractAuthority` freezes the already-authorized values together with the task/action-graph identity and system-observed baseline. This prevents retrospective movement of success criteria after execution begins.

## Lifecycle

```text
MONITORING
  -> OBSERVING
  -> REVIEW_READY
  -> REVIEWING
      -> MONITORING            (SETTLED result)
      -> ADJUSTMENT_REQUIRED   (Agent1 re-entry allowed)
```

`TaskStateAuthority` is deliberately not reused. Product observation/review lifecycle and operator task workflow remain separate authorities.

## Deterministic review

`SystemReviewAuthority` evaluates only frozen criteria and observed facts:

1. review window due;
2. minimum evidence satisfied;
3. required review metrics present;
4. lower/upper guards respected;
5. explicitly structured expected trend satisfied.

A clear pass becomes `SETTLED` and returns the product to `MONITORING` without creating an Agent job.

A guard/trend breach becomes `ADJUSTMENT_REQUIRED` and may re-enter Agent1.

Legacy or ambiguous expectation forms are never guessed by Java. For example, historical free-form values such as `stable_or_up` are marked non-deterministic and fail closed into `ADJUSTMENT_REQUIRED`, where Agent1 may interpret the business meaning.

## Existing authority roots

V26.4 creates no new authority domain:

- `INFORMATION`: freeze/read observed review facts and deterministic comparison inputs;
- `TEMPORAL`: review-due eligibility;
- `MUTATION`: product lifecycle transitions;
- `INVOCATION`: Agent1 re-entry only when review requires renewed reasoning.

All operations remain bound to the existing Authority Generation root and stale generation fencing.

## Verification

`V264SystemReviewMain` proves in hosted and sealed Java gates that:

- review before due time waits;
- insufficient evidence waits;
- clear success settles without Agent1;
- settlement returns product lifecycle to monitoring;
- guard breach re-enters Agent1;
- non-deterministic legacy criteria fail closed to Agent1;
- no fifth authority domain is created;
- Java receives no `plan.*` write authority;
- `TaskStateAuthority` is not repurposed as product lifecycle.

The proof is executed by the existing V26 hosted authority gate and again inside the existing sealed V24 production authority bundle before Release Seal acceptance.
