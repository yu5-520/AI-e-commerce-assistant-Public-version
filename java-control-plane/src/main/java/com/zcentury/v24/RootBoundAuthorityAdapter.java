package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Supplier;

/**
 * V24.25 root-bound runtime wrapper for one deterministic authority domain.
 *
 * The adapter never issues an Authority Generation. It only admits an operation when the
 * caller presents the current generation identity from UnifiedAuthorityGenerationRoot,
 * then re-checks the same identity after the deterministic domain operation completes.
 * Production ownership remains with the legacy production lane in this phase.
 */
final class RootBoundAuthorityAdapter {
    static final String VERSION = "24.25.0";
    static final String RECEIPT_SCHEMA = "v24.root_bound_authority_adapter.binding_receipt.v1";

    record Token(long generationSeq, String generationHash, long fencingToken) {}

    private final String domain;
    private final String implementationClassName;
    private final UnifiedAuthorityGenerationRoot root;
    private final GenerationFencer generationConsumer;

    RootBoundAuthorityAdapter(
        String domain,
        String implementationClassName,
        UnifiedAuthorityGenerationRoot root
    ) {
        this.domain = requireText(domain, "authority_domain_required").toUpperCase();
        this.implementationClassName = requireText(
            implementationClassName,
            "authority_implementation_class_required"
        );
        this.root = Objects.requireNonNull(root, "unified_authority_generation_root_required");
        this.generationConsumer = root.consumerFence(this.domain);
        try {
            Class.forName(this.implementationClassName);
        } catch (ClassNotFoundException exc) {
            throw new IllegalStateException(
                "authority_adapter_implementation_class_missing:" + this.implementationClassName,
                exc
            );
        }
    }

    String domain() {
        return domain;
    }

    String implementationClassName() {
        return implementationClassName;
    }

    Token token() {
        GenerationFencer.Snapshot value = generationConsumer.current();
        return new Token(value.generationSeq(), value.generationHash(), value.fencingToken());
    }

    boolean matches(Token token) {
        if (token == null) return false;
        return generationConsumer.matches(new GenerationFencer.Fence(
            token.generationSeq(), token.generationHash(), token.fencingToken()
        ));
    }

    <T> T execute(Token token, String operationName, Supplier<T> operation) {
        String normalizedOperation = requireText(operationName, "authority_operation_name_required");
        Objects.requireNonNull(operation, "authority_operation_required");
        requireCurrent(token, "BEFORE", normalizedOperation);
        T result = operation.get();
        requireCurrent(token, "AFTER", normalizedOperation);
        return result;
    }

    QueueAuthority queue(String contractVersion) {
        if (!"INVOCATION".equals(domain)) {
            throw new IllegalStateException("queue_runtime_requires_invocation_domain:" + domain);
        }
        return new QueueAuthority(generationConsumer, contractVersion);
    }

    Map<String, Object> bindingReceipt() {
        Token current = token();
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("schema", RECEIPT_SCHEMA);
        out.put("version", VERSION);
        out.put("enforcementMode", "SHADOW");
        out.put("domain", domain);
        out.put("implementationClass", implementationClassName);
        out.put("rootSource", "AuthorityGenerationStore");
        out.put("rootBound", generationConsumer.rootBound());
        out.put("generationSeq", current.generationSeq());
        out.put("generationHash", current.generationHash());
        out.put("fencingToken", current.fencingToken());
        out.put("domainMayRotateGeneration", false);
        out.put("admissionBeforeOperationRequired", true);
        out.put("admissionRecheckAfterOperationRequired", true);
        out.put("productionAuthorityOwnershipChanged", false);
        out.put("authorityGrantCreated", false);
        out.put("receiptHash", Hashing.canonicalHash(out));
        return out;
    }

    private void requireCurrent(Token token, String position, String operationName) {
        if (token == null) {
            throw new IllegalStateException(
                "authority_generation_token_required:" + domain + ":" + operationName
            );
        }
        if (!matches(token)) {
            throw new IllegalStateException(
                "stale_root_bound_authority_generation:" + domain + ":" + position + ":" + operationName
            );
        }
    }

    private static String requireText(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value.trim();
    }
}
