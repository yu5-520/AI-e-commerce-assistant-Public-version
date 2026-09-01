package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * V24.23 task-scoped Unified Authority Kernel (Shadow).
 *
 * This kernel does not take production ownership. It standardizes how Information,
 * Invocation, Temporal and Mutation authority requests are described and decided.
 * Existing domain authorities remain the implementation owners behind registered adapters.
 */
public final class UnifiedAuthorityKernel {
    public static final String VERSION = "24.23.0";
    public static final String POLICY_SCHEMA = "v24.unified_authority_kernel.policy.v1";
    public static final String REQUEST_SCHEMA = "v24.unified_authority.request.v1";
    public static final String DECISION_SCHEMA = "v24.unified_authority.decision.v1";

    private static final Set<String> DOMAINS = Set.of(
        "INFORMATION", "INVOCATION", "TEMPORAL", "MUTATION"
    );

    private UnifiedAuthorityKernel() {}

    public static String policyHash(Map<String, Object> policy) {
        validatePolicy(policy);
        return Hashing.canonicalHash(policy);
    }

    public static Map<String, Object> evaluate(
        Map<String, Object> request,
        Map<String, Object> policy,
        Map<String, Object> currentGeneration
    ) {
        validatePolicy(policy);
        String policyHash = Hashing.canonicalHash(policy);
        String requestHash = Hashing.canonicalHash(request);
        List<String> reasons = new ArrayList<>();

        String requestId = text(request.get("requestId"));
        String transactionId = text(request.get("transactionId"));
        String executionId = text(request.get("executionId"));
        String actor = text(request.get("actor"));
        String action = text(request.get("action")).toUpperCase();
        String resource = text(request.get("resource"));
        String domain = text(request.get("domain")).toUpperCase();
        String goal = text(request.get("goal"));
        String state = text(request.get("state")).toUpperCase();
        String temporalMode = text(request.get("temporalMode")).toUpperCase();

        requireNonBlank(requestId, "REQUEST_ID_REQUIRED", reasons);
        requireNonBlank(transactionId, "TRANSACTION_ID_REQUIRED", reasons);
        requireNonBlank(executionId, "EXECUTION_ID_REQUIRED", reasons);
        requireNonBlank(actor, "ACTOR_REQUIRED", reasons);
        requireNonBlank(action, "ACTION_REQUIRED", reasons);
        requireNonBlank(resource, "RESOURCE_REQUIRED", reasons);
        requireNonBlank(domain, "DOMAIN_REQUIRED", reasons);
        requireNonBlank(goal, "GOAL_REQUIRED", reasons);
        requireNonBlank(state, "STATE_REQUIRED", reasons);

        if (!REQUEST_SCHEMA.equals(text(request.get("schema")))) reasons.add("REQUEST_SCHEMA_INVALID");
        if (!DOMAINS.contains(domain)) reasons.add("UNKNOWN_AUTHORITY_DOMAIN");
        if (!policyHash.equals(text(request.get("policyHash")))) reasons.add("POLICY_HASH_MISMATCH");

        List<Object> evidenceRefs = listOrEmpty(request.get("evidenceRefs"));
        if (principle(policy, "evidenceRefsRequired") && evidenceRefs.isEmpty()) {
            reasons.add("EVIDENCE_REFS_REQUIRED");
        }

        GenerationCheck generationCheck = checkGeneration(request, currentGeneration);
        if (!generationCheck.matched()) {
            reasons.add(generationCheck.reason());
            return decision(
                request, policy, currentGeneration, policyHash, requestHash,
                "CONFLICT", reasons, Map.of()
            );
        }

        if (!reasons.isEmpty()) {
            return decision(
                request, policy, currentGeneration, policyHash, requestHash,
                "BLOCK", reasons, Map.of()
            );
        }

        Map<String, Object> grantedScope = switch (domain) {
            case "INFORMATION" -> evaluateInformation(request, policy, reasons);
            case "INVOCATION" -> evaluateInvocation(actor, action, resource, goal, state, request, policy, reasons);
            case "TEMPORAL" -> evaluateTemporal(action, state, temporalMode, request, policy, reasons);
            case "MUTATION" -> evaluateMutation(action, resource, state, temporalMode, request, policy, reasons);
            default -> Map.of();
        };

        String result = reasons.isEmpty() ? "PASS" : "BLOCK";
        return decision(
            request, policy, currentGeneration, policyHash, requestHash,
            result, reasons, reasons.isEmpty() ? grantedScope : Map.of()
        );
    }

    public static void validatePolicy(Map<String, Object> policy) {
        if (!POLICY_SCHEMA.equals(text(policy.get("schema")))) {
            throw new IllegalArgumentException("unified_authority_policy_schema_invalid");
        }
        if (!VERSION.equals(text(policy.get("version")))) {
            throw new IllegalArgumentException("unified_authority_policy_version_invalid");
        }
        if (!"SHADOW".equals(text(policy.get("enforcementMode")))) {
            throw new IllegalArgumentException("unified_authority_kernel_must_start_shadow");
        }
        if (!"BLOCK".equals(text(policy.get("defaultDecision")))) {
            throw new IllegalArgumentException("unified_authority_default_must_block");
        }
        for (String required : DOMAINS) {
            if (!strings(policy.get("domains")).contains(required)) {
                throw new IllegalArgumentException("authority_domain_missing:" + required);
            }
        }
        Map<String, Object> principles = objectOrEmpty(policy.get("principles"));
        requireTrue(principles, "taskScopedAuthorityRequired");
        requireTrue(principles, "effectiveAuthorityMustRemainSubsetOfPreGrantedAuthority");
        requireFalse(principles, "modelMayGrantAuthority");
        requireFalse(principles, "modelMayExpandInvocationGraph");
        requireFalse(principles, "derivedInformationMayCreateSystemFact");
        requireFalse(principles, "terminalTransactionMutationAllowed");
        requireFalse(principles, "staleGenerationAllowed");
        requireTrue(principles, "singleAuthorityGenerationRootRequired");
        requireFalse(principles, "productionAuthorityOwnershipChangedByThisPhase");
    }

    private static Map<String, Object> evaluateInformation(
        Map<String, Object> request,
        Map<String, Object> policy,
        List<String> reasons
    ) {
        String action = text(request.get("action")).toUpperCase();
        String semanticType = text(request.get("semanticType")).toUpperCase();
        Map<String, Object> semantic = objectOrEmpty(objectOrEmpty(policy.get("semanticAuthority")).get(semanticType));
        if (semantic.isEmpty()) {
            reasons.add("UNKNOWN_SEMANTIC_AUTHORITY");
            return Map.of();
        }
        String property = switch (action) {
            case "READ" -> "read";
            case "DERIVE" -> "derive";
            case "PROMOTE_TO_FACT" -> "promoteToFact";
            case "USE_FOR_MUTATION" -> "useForMutation";
            default -> null;
        };
        if (property == null) {
            reasons.add("INFORMATION_ACTION_NOT_REGISTERED");
            return Map.of();
        }
        if (!Boolean.TRUE.equals(semantic.get(property))) {
            reasons.add("INFORMATION_AUTHORITY_NOT_GRANTED");
            if ("PROMOTE_TO_FACT".equals(action)) reasons.add("DERIVED_INFORMATION_CANNOT_CREATE_SYSTEM_FACT");
            return Map.of();
        }
        LinkedHashMap<String, Object> scope = requestedScope(request);
        scope.put("semanticType", semanticType);
        scope.put("informationAction", action);
        return scope;
    }

    private static Map<String, Object> evaluateInvocation(
        String actor,
        String action,
        String resource,
        String goal,
        String state,
        Map<String, Object> request,
        Map<String, Object> policy,
        List<String> reasons
    ) {
        if (!"INVOKE".equals(action)) {
            reasons.add("INVOCATION_ACTION_NOT_REGISTERED");
            return Map.of();
        }
        for (Object raw : listOrEmpty(policy.get("invocationEdges"))) {
            Map<String, Object> edge = objectOrEmpty(raw);
            if (!actor.equals(text(edge.get("from")))) continue;
            if (!resource.equals(text(edge.get("to")))) continue;
            if (!strings(edge.get("goals")).contains(goal)) continue;
            if (!strings(edge.get("states")).contains(state)) continue;
            LinkedHashMap<String, Object> scope = requestedScope(request);
            scope.put("edge", actor + "->" + resource);
            scope.put("goal", goal);
            scope.put("state", state);
            return scope;
        }
        reasons.add("AUTHORITY_EDGE_NOT_GRANTED");
        return Map.of();
    }

    private static Map<String, Object> evaluateTemporal(
        String action,
        String state,
        String temporalMode,
        Map<String, Object> request,
        Map<String, Object> policy,
        List<String> reasons
    ) {
        if (temporalMode.isBlank()) {
            reasons.add("TEMPORAL_MODE_REQUIRED");
            return Map.of();
        }
        List<String> allowed = strings(objectOrEmpty(policy.get("temporalModes")).get(temporalMode));
        if (allowed.isEmpty() && !objectOrEmpty(policy.get("temporalModes")).containsKey(temporalMode)) {
            reasons.add("UNKNOWN_TEMPORAL_MODE");
            return Map.of();
        }
        if (strings(policy.get("terminalStates")).contains(state) && !"RETROSPECTIVE_READ".equals(action)) {
            reasons.add("TERMINAL_REOPEN_BLOCKED");
            return Map.of();
        }
        if (!allowed.contains(action)) {
            reasons.add("TEMPORAL_AUTHORITY_NOT_GRANTED");
            return Map.of();
        }
        LinkedHashMap<String, Object> scope = requestedScope(request);
        scope.put("temporalMode", temporalMode);
        scope.put("temporalAction", action);
        return scope;
    }

    private static Map<String, Object> evaluateMutation(
        String action,
        String resource,
        String state,
        String temporalMode,
        Map<String, Object> request,
        Map<String, Object> policy,
        List<String> reasons
    ) {
        if (!"MUTATE".equals(action)) {
            reasons.add("MUTATION_ACTION_NOT_REGISTERED");
            return Map.of();
        }
        if (strings(policy.get("terminalStates")).contains(state)) {
            reasons.add("TERMINAL_TRANSACTION_MUTATION_BLOCKED");
            return Map.of();
        }
        if (!"ACTIVE".equals(temporalMode)) {
            reasons.add("MUTATION_REQUIRES_ACTIVE_TEMPORAL_SCOPE");
            return Map.of();
        }
        if (!strings(policy.get("mutationResources")).contains(resource)) {
            reasons.add("MUTATION_RESOURCE_NOT_GRANTED");
            return Map.of();
        }
        LinkedHashMap<String, Object> scope = requestedScope(request);
        scope.put("mutationResource", resource);
        scope.put("temporalMode", temporalMode);
        return scope;
    }

    private static Map<String, Object> decision(
        Map<String, Object> request,
        Map<String, Object> policy,
        Map<String, Object> generation,
        String policyHash,
        String requestHash,
        String decision,
        List<String> reasons,
        Map<String, Object> grantedScope
    ) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("schema", DECISION_SCHEMA);
        out.put("version", VERSION);
        out.put("enforcementMode", policy.get("enforcementMode"));
        out.put("requestId", request.get("requestId"));
        out.put("transactionId", request.get("transactionId"));
        out.put("executionId", request.get("executionId"));
        out.put("actor", request.get("actor"));
        out.put("domain", request.get("domain"));
        out.put("action", request.get("action"));
        out.put("resource", request.get("resource"));
        out.put("decision", decision);
        out.put("reasonCodes", List.copyOf(reasons));
        out.put("authorityGeneration", generationIdentity(generation));
        out.put("policyHash", policyHash);
        out.put("requestHash", requestHash);
        out.put("evidenceHash", Hashing.canonicalHash(listOrEmpty(request.get("evidenceRefs"))));
        out.put("requestedScopeHash", Hashing.canonicalHash(objectOrEmpty(request.get("requestedScope"))));
        out.put("grantedScope", grantedScope);
        out.put("authorityGrantCreated", false);
        out.put("productionAuthorityOwnershipChanged", false);
        out.put("modelMayExpandAuthority", false);
        out.put("decisionHash", Hashing.canonicalHash(out));
        return out;
    }

    private static GenerationCheck checkGeneration(
        Map<String, Object> request,
        Map<String, Object> current
    ) {
        long requestedSeq = number(request.get("generationSeq"));
        long requestedToken = number(request.get("fencingToken"));
        String requestedHash = text(request.get("generationHash"));
        long currentSeq = number(current.get("generationSeq"));
        long currentToken = number(current.get("fencingToken"));
        String currentHash = text(current.get("generationHash"));
        if (requestedSeq < 0 || requestedToken < 0 || !isSha256(requestedHash)) {
            return new GenerationCheck(false, "AUTHORITY_GENERATION_IDENTITY_INVALID");
        }
        if (requestedSeq != currentSeq || requestedToken != currentToken || !requestedHash.equals(currentHash)) {
            return new GenerationCheck(false, "STALE_AUTHORITY_GENERATION");
        }
        return new GenerationCheck(true, "MATCHED");
    }

    private static Map<String, Object> generationIdentity(Map<String, Object> current) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("generationSeq", current.get("generationSeq"));
        value.put("generationHash", current.get("generationHash"));
        value.put("fencingToken", current.get("fencingToken"));
        return value;
    }

    private static LinkedHashMap<String, Object> requestedScope(Map<String, Object> request) {
        return new LinkedHashMap<>(objectOrEmpty(request.get("requestedScope")));
    }

    private static boolean principle(Map<String, Object> policy, String name) {
        return Boolean.TRUE.equals(objectOrEmpty(policy.get("principles")).get(name));
    }

    private static void requireNonBlank(String value, String reason, List<String> reasons) {
        if (value.isBlank()) reasons.add(reason);
    }

    private static void requireTrue(Map<String, Object> values, String key) {
        if (!Boolean.TRUE.equals(values.get(key))) throw new IllegalArgumentException("authority_principle_required_true:" + key);
    }

    private static void requireFalse(Map<String, Object> values, String key) {
        if (!Boolean.FALSE.equals(values.get(key))) throw new IllegalArgumentException("authority_principle_required_false:" + key);
    }

    private static Map<String, Object> objectOrEmpty(Object value) {
        return value instanceof Map<?, ?> ? Json.object(value) : Map.of();
    }

    private static List<Object> listOrEmpty(Object value) {
        return value instanceof List<?> ? Json.array(value) : List.of();
    }

    private static List<String> strings(Object value) {
        List<String> result = new ArrayList<>();
        for (Object item : listOrEmpty(value)) result.add(text(item));
        return result;
    }

    private static long number(Object value) {
        if (value instanceof Number number) return number.longValue();
        try { return Long.parseLong(text(value)); }
        catch (Exception ignored) { return -1L; }
    }

    private static boolean isSha256(String value) {
        return value.matches("sha256:[0-9a-fA-F]{64}");
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private record GenerationCheck(boolean matched, String reason) {}
}
