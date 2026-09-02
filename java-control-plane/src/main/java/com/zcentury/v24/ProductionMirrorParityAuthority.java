package com.zcentury.v24;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * V24.26 fail-closed verifier for production-vs-Java shadow parity evidence.
 *
 * Repository replay is allowed to prove the mirror mechanism only. Only sealed evidence whose
 * source is EXTERNAL_PRODUCTION_MIRROR may prove production parity, and even then this phase never
 * grants Java production ownership or mutation authority.
 */
final class ProductionMirrorParityAuthority {
    static final String VERSION = "24.26.0";
    static final String POLICY_SCHEMA = "v24.production_mirror_parity_policy.v1";
    static final String EVIDENCE_SCHEMA = "v24.production_mirror_evidence.v1";
    static final Set<String> DOMAINS = Set.of(
        "INFORMATION", "INVOCATION", "TEMPORAL", "MUTATION"
    );

    private ProductionMirrorParityAuthority() {}

    static void verifyPolicy(Map<String, Object> policy) {
        require(POLICY_SCHEMA.equals(text(policy.get("schema"))), "production_mirror_policy_schema_invalid");
        require(VERSION.equals(text(policy.get("version"))), "production_mirror_policy_version_invalid");
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "production_mirror_must_start_shadow");
        require(new LinkedHashSet<>(strings(policy.get("requiredDomains"))).equals(DOMAINS), "production_mirror_domain_set_invalid");
        require(number(policy.get("minimumSamplesPerDomainPerWindow")) >= 1L, "production_mirror_minimum_samples_invalid");
        require(number(policy.get("requiredConsecutiveParityWindows")) >= 1L, "production_mirror_window_count_invalid");
        require(number(policy.get("maximumDecisionMismatchCount")) == 0L, "production_mirror_mismatch_budget_must_be_zero");
        require(Boolean.TRUE.equals(policy.get("requiresExternalProductionReceipt")), "external_production_receipt_required");
        require(Boolean.TRUE.equals(policy.get("repositoryReplayMayProveMechanismOnly")), "repository_replay_must_be_mechanism_only");
        require("EXTERNAL_PRODUCTION_MIRROR".equals(text(policy.get("productionReceiptSource"))), "production_receipt_source_invalid");
        require(Boolean.TRUE.equals(policy.get("legacyProductionRemainsOwner")), "legacy_production_owner_must_remain");
        require(Boolean.FALSE.equals(policy.get("javaProductionMutationAllowed")), "java_production_mutation_must_stay_off");
        require(Boolean.FALSE.equals(policy.get("authorityOwnerTransferAllowed")), "owner_transfer_must_stay_off");
        require(Boolean.TRUE.equals(policy.get("rollbackWindowRequired")), "rollback_window_required");
        require(Boolean.TRUE.equals(policy.get("inFlightDrainRequired")), "inflight_drain_required");
        require(Boolean.TRUE.equals(policy.get("staleGenerationMustFailClosed")), "stale_generation_fail_closed_required");
        require(Boolean.TRUE.equals(policy.get("freshGenerationMustRemainAdmissible")), "fresh_generation_admission_required");
        require(Boolean.TRUE.equals(policy.get("preparedGenerationMustFailAfterRollback")), "rollback_generation_invalidation_required");
        require("BLOCK".equals(text(policy.get("defaultDecision"))), "production_mirror_default_must_block");
    }

    static Map<String, Object> evaluate(Map<String, Object> policy, Map<String, Object> evidence) {
        verifyPolicy(policy);
        require(EVIDENCE_SCHEMA.equals(text(evidence.get("schema"))), "production_mirror_evidence_schema_invalid");
        require(VERSION.equals(text(evidence.get("version"))), "production_mirror_evidence_version_invalid");

        String evidenceSource = text(evidence.get("evidenceSource"));
        require(Set.of("REPOSITORY_REPLAY", "EXTERNAL_PRODUCTION_MIRROR").contains(evidenceSource), "production_mirror_evidence_source_invalid");
        boolean externalSource = "EXTERNAL_PRODUCTION_MIRROR".equals(evidenceSource);
        boolean replaySource = "REPOSITORY_REPLAY".equals(evidenceSource);

        int minimumSamples = (int) number(policy.get("minimumSamplesPerDomainPerWindow"));
        int requiredWindows = (int) number(policy.get("requiredConsecutiveParityWindows"));
        List<Object> rawWindows = Json.array(evidence.get("windows"));
        require(!rawWindows.isEmpty(), "production_mirror_windows_required");

        int mismatchCount = 0;
        int passingWindowCount = 0;
        List<Map<String, Object>> windowResults = new ArrayList<>();
        for (Object rawWindow : rawWindows) {
            Map<String, Object> window = Json.object(rawWindow);
            require(Boolean.TRUE.equals(window.get("sealed")), "production_mirror_window_must_be_sealed");
            require(!text(window.get("windowId")).isBlank(), "production_mirror_window_id_required");
            require(number(window.get("generationSeq")) >= 0L, "production_mirror_generation_seq_invalid");
            requireHash(text(window.get("generationHash")), "production_mirror_generation_hash_invalid");
            require(number(window.get("fencingToken")) >= 0L, "production_mirror_fencing_token_invalid");

            Map<String, Integer> domainCounts = new HashMap<>();
            int windowMismatch = 0;
            List<Object> samples = Json.array(window.get("samples"));
            require(!samples.isEmpty(), "production_mirror_samples_required");
            for (Object rawSample : samples) {
                Map<String, Object> sample = Json.object(rawSample);
                String domain = text(sample.get("domain"));
                require(DOMAINS.contains(domain), "production_mirror_unknown_domain:" + domain);
                require(!text(sample.get("sampleId")).isBlank(), "production_mirror_sample_id_required");
                requireHash(text(sample.get("inputHash")), "production_mirror_input_hash_invalid");
                requireHash(text(sample.get("productionResultHash")), "production_mirror_production_result_hash_invalid");
                requireHash(text(sample.get("shadowResultHash")), "production_mirror_shadow_result_hash_invalid");
                require(Boolean.FALSE.equals(sample.get("shadowWriteAttempted")), "shadow_write_attempt_forbidden");
                require(Boolean.TRUE.equals(sample.get("productionOwnerUnchanged")), "production_owner_changed_during_mirror");
                domainCounts.merge(domain, 1, Integer::sum);
                if (!text(sample.get("productionResultHash")).equals(text(sample.get("shadowResultHash")))) {
                    windowMismatch++;
                }
            }
            for (String domain : DOMAINS) {
                require(domainCounts.getOrDefault(domain, 0) >= minimumSamples, "production_mirror_insufficient_domain_samples:" + domain);
            }
            boolean windowParity = windowMismatch == 0;
            if (windowParity) passingWindowCount++;
            mismatchCount += windowMismatch;
            LinkedHashMap<String, Object> windowResult = new LinkedHashMap<>();
            windowResult.put("windowId", window.get("windowId"));
            windowResult.put("sampleCount", samples.size());
            windowResult.put("domainCounts", new LinkedHashMap<>(domainCounts));
            windowResult.put("mismatchCount", windowMismatch);
            windowResult.put("parity", windowParity);
            windowResult.put("windowHash", Hashing.canonicalHash(window));
            windowResults.add(windowResult);
        }

        boolean parityWindowsSatisfied = passingWindowCount >= requiredWindows && mismatchCount == 0;
        boolean drainVerified = Boolean.TRUE.equals(evidence.get("inFlightDrainVerified"));
        boolean staleBlocked = Boolean.TRUE.equals(evidence.get("staleGenerationBlocked"));
        boolean freshAdmissible = Boolean.TRUE.equals(evidence.get("freshGenerationAdmissible"));
        boolean rollbackVerified = Boolean.TRUE.equals(evidence.get("rollbackWindowVerified"));
        boolean preparedInvalidAfterRollback = Boolean.TRUE.equals(evidence.get("preparedGenerationInvalidAfterRollback"));
        boolean ownerStable = Boolean.TRUE.equals(evidence.get("productionOwnerBoundaryStable"));
        boolean productionMutationAllowed = Boolean.TRUE.equals(evidence.get("productionMutationAllowed"));

        boolean mechanismVerified = parityWindowsSatisfied
            && drainVerified
            && staleBlocked
            && freshAdmissible
            && rollbackVerified
            && preparedInvalidAfterRollback
            && ownerStable
            && !productionMutationAllowed;
        boolean externalProductionMirrorParityProven = mechanismVerified && externalSource;
        boolean replayMechanismOnly = mechanismVerified && replaySource;
        boolean cutoverQualified = externalProductionMirrorParityProven;

        String status;
        if (!mechanismVerified) {
            status = "BLOCKED_PARITY_OR_ROLLBACK_EVIDENCE_INVALID";
        } else if (!externalSource) {
            status = "MIRROR_MECHANISM_VERIFIED_EXTERNAL_EVIDENCE_REQUIRED";
        } else {
            status = "PRODUCTION_MIRROR_PARITY_PROVEN_OWNER_TRANSFER_GATE_REQUIRED";
        }

        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "v24.production_mirror_parity.decision.v1");
        result.put("version", VERSION);
        result.put("verified", mechanismVerified);
        result.put("enforcementMode", "SHADOW");
        result.put("evidenceSource", evidenceSource);
        result.put("windowCount", rawWindows.size());
        result.put("passingWindowCount", passingWindowCount);
        result.put("requiredConsecutiveParityWindows", requiredWindows);
        result.put("mismatchCount", mismatchCount);
        result.put("maximumDecisionMismatchCount", policy.get("maximumDecisionMismatchCount"));
        result.put("parityWindowsSatisfied", parityWindowsSatisfied);
        result.put("inFlightDrainVerified", drainVerified);
        result.put("staleGenerationBlocked", staleBlocked);
        result.put("freshGenerationAdmissible", freshAdmissible);
        result.put("rollbackWindowVerified", rollbackVerified);
        result.put("preparedGenerationInvalidAfterRollback", preparedInvalidAfterRollback);
        result.put("productionOwnerBoundaryStable", ownerStable);
        result.put("productionMutationAllowed", productionMutationAllowed);
        result.put("mirrorMechanismVerified", mechanismVerified);
        result.put("repositoryReplayMechanismOnly", replayMechanismOnly);
        result.put("externalProductionMirrorParityProven", externalProductionMirrorParityProven);
        result.put("cutoverQualificationReady", cutoverQualified);
        result.put("productionAuthorityOwnershipChanged", false);
        result.put("authorityGrantCreated", false);
        result.put("cutoverAllowed", false);
        result.put("status", status);
        result.put("windowResults", windowResults);
        result.put("evidenceHash", Hashing.canonicalHash(evidence));
        result.put("policyHash", Hashing.canonicalHash(policy));
        result.put("decisionHash", Hashing.canonicalHash(result));
        return result;
    }

    static Map<String, Object> tamperOneSample(Map<String, Object> evidence) {
        LinkedHashMap<String, Object> copy = new LinkedHashMap<>(evidence);
        List<Object> windows = new ArrayList<>(Json.array(evidence.get("windows")));
        Map<String, Object> firstWindow = new LinkedHashMap<>(Json.object(windows.get(0)));
        List<Object> samples = new ArrayList<>(Json.array(firstWindow.get("samples")));
        Map<String, Object> firstSample = new LinkedHashMap<>(Json.object(samples.get(0)));
        firstSample.put("shadowResultHash", Hashing.canonicalHash(Map.of("tampered", true)));
        samples.set(0, firstSample);
        firstWindow.put("samples", samples);
        windows.set(0, firstWindow);
        copy.put("windows", windows);
        return copy;
    }

    private static List<String> strings(Object value) {
        List<String> out = new ArrayList<>();
        for (Object item : Json.array(value)) out.add(text(item));
        return out;
    }

    private static void requireHash(String value, String error) {
        require(value != null && value.matches("sha256:[0-9a-f]{64}"), error);
    }

    private static long number(Object value) {
        if (value instanceof Number number) return number.longValue();
        try {
            return Long.parseLong(text(value));
        } catch (Exception ignored) {
            return -1L;
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static void require(boolean condition, String error) {
        if (!condition) throw new IllegalArgumentException(error);
    }
}
