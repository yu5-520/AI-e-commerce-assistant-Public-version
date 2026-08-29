package com.zcentury.v24;

import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Durable single-writer Authority Generation controller.
 *
 * V24.22 deliberately permits READY_NO_AUTHORITY -> CUTOVER_PREPARED and rollback only.
 * FIVE_AUTHORITY_ACTIVE remains impossible until the production persistence bridge is sealed.
 */
final class AuthorityGenerationStore {
    static final String SCHEMA = "v24.authority-generation.state.v1";
    static final String VERSION = "24.22.0";
    static final String READY = "READY_NO_AUTHORITY";
    static final String PREPARED = "CUTOVER_PREPARED";
    private static final Set<String> MODES = Set.of(READY, PREPARED);
    private static final List<String> REQUIRED_PROOFS = List.of(
        "SEALED_JAVA_RUNTIME_VERIFIED",
        "JAVA_SERVICE_READY_NO_AUTHORITY",
        "PYTHON_JAVA_MIRROR_PARITY_PROVEN",
        "DURABLE_STATE_ADAPTER_VERIFIED",
        "SINGLE_WRITER_GENERATION_ROTATION_PREPARED",
        "FULL_ROLLBACK_PROVEN"
    );

    private final Path statePath;
    private final Path lockPath;

    AuthorityGenerationStore(Path statePath) {
        this.statePath = statePath.toAbsolutePath().normalize();
        Path parent = this.statePath.getParent();
        if (parent == null) throw new IllegalArgumentException("authority_state_parent_required");
        this.lockPath = parent.resolve(this.statePath.getFileName().toString() + ".lock");
    }

    Map<String, Object> status() throws IOException {
        return withLock(() -> {
            Map<String, Object> current = readOrInitialize();
            return copy(current);
        });
    }

    Map<String, Object> prepare(
        String expectedStateHash,
        String sourceCommit,
        String releaseHash,
        Map<String, Object> proof
    ) throws IOException {
        requireCommit(sourceCommit);
        requireHash(releaseHash, "release_hash_required");
        verifyProof(proof, sourceCommit, releaseHash);
        return withLock(() -> {
            Map<String, Object> current = readOrInitialize();
            compareStateHash(current, expectedStateHash);
            String mode = text(current.get("mode"));
            if (PREPARED.equals(mode)) {
                if (sourceCommit.equals(text(current.get("sourceCommit")))
                    && releaseHash.equals(text(current.get("releaseHash")))) {
                    return copy(current);
                }
                throw new IllegalStateException("authority_generation_already_prepared_for_other_release");
            }
            if (!READY.equals(mode)) throw new IllegalStateException("authority_prepare_from_invalid_mode:" + mode);
            Map<String, Object> next = nextState(
                current,
                PREPARED,
                sourceCommit,
                releaseHash,
                "five_authority_cutover_prepared",
                proof
            );
            writeAtomic(next);
            return copy(next);
        });
    }

    Map<String, Object> rollback(String expectedStateHash, String reason) throws IOException {
        return withLock(() -> {
            Map<String, Object> current = readOrInitialize();
            compareStateHash(current, expectedStateHash);
            String mode = text(current.get("mode"));
            if (READY.equals(mode)) return copy(current);
            if (!PREPARED.equals(mode)) throw new IllegalStateException("authority_rollback_from_invalid_mode:" + mode);
            Map<String, Object> next = nextState(
                current,
                READY,
                text(current.get("sourceCommit")),
                text(current.get("releaseHash")),
                reason == null || reason.isBlank() ? "operator_rollback" : reason,
                Map.of()
            );
            writeAtomic(next);
            return copy(next);
        });
    }

    Map<String, Object> activateForbidden() {
        throw new IllegalStateException(
            "five_authority_activation_forbidden_until_durable_queue_frontend_bridge_is_sealed"
        );
    }

    boolean matches(long generationSeq, String generationHash, long fencingToken) throws IOException {
        Map<String, Object> current = status();
        return number(current.get("generationSeq")) == generationSeq
            && number(current.get("fencingToken")) == fencingToken
            && text(current.get("generationHash")).equals(generationHash);
    }

    private Map<String, Object> readOrInitialize() throws IOException {
        if (!Files.exists(statePath)) {
            Map<String, Object> initial = initialState();
            writeAtomic(initial);
            return initial;
        }
        String raw = Files.readString(statePath, StandardCharsets.UTF_8);
        Map<String, Object> value = Json.object(Json.parse(raw));
        validate(value);
        return value;
    }

    private Map<String, Object> initialState() {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", SCHEMA);
        value.put("version", VERSION);
        value.put("stateVersion", 1L);
        value.put("mode", READY);
        value.put("generationSeq", 0L);
        value.put("fencingToken", 0L);
        value.put("generationHash", generationHash(0L, 0L, READY, "initial"));
        value.put("previousGenerationHash", null);
        value.put("sourceCommit", null);
        value.put("releaseHash", null);
        value.put("owners", owners(false));
        value.put("productionMutationAllowed", false);
        value.put("deploymentAuthorityTransferAllowed", false);
        value.put("legacyRemovalAllowed", false);
        value.put("reason", "initial");
        value.put("proofHash", null);
        value.put("stateHash", stateHash(value));
        return value;
    }

    private Map<String, Object> nextState(
        Map<String, Object> current,
        String mode,
        String sourceCommit,
        String releaseHash,
        String reason,
        Map<String, Object> proof
    ) {
        long nextSeq = number(current.get("generationSeq")) + 1L;
        long nextToken = number(current.get("fencingToken")) + 1L;
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", SCHEMA);
        value.put("version", VERSION);
        value.put("stateVersion", number(current.get("stateVersion")) + 1L);
        value.put("mode", mode);
        value.put("generationSeq", nextSeq);
        value.put("fencingToken", nextToken);
        value.put("generationHash", generationHash(nextSeq, nextToken, mode, reason));
        value.put("previousGenerationHash", current.get("generationHash"));
        value.put("sourceCommit", blankToNull(sourceCommit));
        value.put("releaseHash", blankToNull(releaseHash));
        value.put("owners", owners(false));
        value.put("productionMutationAllowed", false);
        value.put("deploymentAuthorityTransferAllowed", false);
        value.put("legacyRemovalAllowed", false);
        value.put("reason", reason);
        value.put("proofHash", proof.isEmpty() ? null : Hashing.canonicalHash(proof));
        value.put("stateHash", stateHash(value));
        return value;
    }

    private static Map<String, Object> owners(boolean active) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        String deterministic = active ? "JAVA_PRODUCTION" : "PYTHON_PRODUCTION";
        value.put("compatibility", active ? "JAVA_PRODUCTION" : "PYTHON_BASH_PRODUCTION");
        value.put("runtimeAdmission", active ? "JAVA_PRODUCTION" : "PYTHON_BASH_PRODUCTION");
        value.put("gateAndTaskState", deterministic);
        value.put("queueAndGeneration", deterministic);
        value.put("frontendHeadAndSse", deterministic);
        value.put("deployment", "BASH_SYSTEMD_PRODUCTION");
        value.put("agentProvider", "PYTHON_PRODUCTION");
        value.put("legacyRemoval", "DISABLED");
        return value;
    }

    private static void verifyProof(Map<String, Object> proof, String sourceCommit, String releaseHash) {
        if (!"v24.authority-generation.proof.v1".equals(text(proof.get("schema")))) {
            throw new IllegalArgumentException("authority_proof_schema_invalid");
        }
        if (!Boolean.TRUE.equals(proof.get("verified"))) {
            throw new IllegalArgumentException("authority_proof_not_verified");
        }
        if (!sourceCommit.equals(text(proof.get("sourceCommit")))) {
            throw new IllegalArgumentException("authority_proof_source_commit_mismatch");
        }
        if (!releaseHash.equals(text(proof.get("releaseHash")))) {
            throw new IllegalArgumentException("authority_proof_release_hash_mismatch");
        }
        Map<String, Object> gates = Json.object(proof.get("gates"));
        for (String gate : REQUIRED_PROOFS) {
            if (!Boolean.TRUE.equals(gates.get(gate))) {
                throw new IllegalArgumentException("authority_proof_gate_missing:" + gate);
            }
        }
    }

    private static void validate(Map<String, Object> value) {
        if (!SCHEMA.equals(text(value.get("schema")))) throw new IllegalStateException("authority_state_schema_invalid");
        if (!VERSION.equals(text(value.get("version")))) throw new IllegalStateException("authority_state_version_invalid");
        if (!MODES.contains(text(value.get("mode")))) throw new IllegalStateException("authority_state_mode_invalid");
        if (number(value.get("stateVersion")) < 1L) throw new IllegalStateException("authority_state_version_missing");
        if (number(value.get("generationSeq")) < 0L || number(value.get("fencingToken")) < 0L) {
            throw new IllegalStateException("authority_generation_identity_invalid");
        }
        requireHash(text(value.get("generationHash")), "authority_generation_hash_invalid");
        String actual = text(value.get("stateHash"));
        String expected = stateHash(value);
        if (!expected.equals(actual)) throw new IllegalStateException("authority_state_hash_mismatch");
        if (Boolean.TRUE.equals(value.get("productionMutationAllowed"))) {
            throw new IllegalStateException("v24_22_production_mutation_forbidden");
        }
        Map<String, Object> ownerMap = Json.object(value.get("owners"));
        if (ownerMap.values().stream().anyMatch(owner -> "JAVA_PRODUCTION".equals(text(owner)))) {
            throw new IllegalStateException("v24_22_java_production_owner_forbidden");
        }
    }

    private static String stateHash(Map<String, Object> value) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(value);
        material.remove("stateHash");
        return Hashing.canonicalHash(material);
    }

    private static String generationHash(long seq, long token, String mode, String reason) {
        return Hashing.canonicalHash(Map.of(
            "schema", "v24.authority-generation.identity.v1",
            "generationSeq", seq,
            "fencingToken", token,
            "mode", mode,
            "reason", reason
        ));
    }

    private void writeAtomic(Map<String, Object> value) throws IOException {
        validate(value);
        Path parent = statePath.getParent();
        Files.createDirectories(parent);
        Path temporary = parent.resolve("." + statePath.getFileName() + ".tmp-" + ProcessHandle.current().pid() + "-" + System.nanoTime());
        Files.writeString(
            temporary,
            Json.canonical(value) + "\n",
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE_NEW,
            StandardOpenOption.WRITE
        );
        try (FileChannel file = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
            file.force(true);
        }
        try {
            Files.move(temporary, statePath, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException ignored) {
            Files.move(temporary, statePath, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temporary);
        }
        try (FileChannel directory = FileChannel.open(parent, StandardOpenOption.READ)) {
            directory.force(true);
        } catch (IOException ignored) {
            // The file fsync and atomic rename remain authoritative on filesystems without directory channels.
        }
    }

    private <T> T withLock(IoOperation<T> operation) throws IOException {
        Files.createDirectories(statePath.getParent());
        try (
            FileChannel channel = FileChannel.open(lockPath, StandardOpenOption.CREATE, StandardOpenOption.WRITE);
            FileLock ignored = channel.lock()
        ) {
            return operation.run();
        }
    }

    private static void compareStateHash(Map<String, Object> current, String expected) {
        if (expected == null || expected.isBlank()) throw new IllegalArgumentException("expected_state_hash_required");
        if (!expected.equals(text(current.get("stateHash")))) {
            throw new IllegalStateException("authority_state_compare_and_set_conflict");
        }
    }

    private static void requireCommit(String value) {
        if (value == null || !value.matches("[0-9a-f]{40}")) {
            throw new IllegalArgumentException("source_commit_must_be_exact_sha");
        }
    }

    private static void requireHash(String value, String error) {
        if (value == null || !value.matches("sha256:[0-9a-f]{64}")) {
            throw new IllegalArgumentException(error);
        }
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

    private static Object blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private static Map<String, Object> copy(Map<String, Object> value) {
        return new LinkedHashMap<>(value);
    }

    @FunctionalInterface
    private interface IoOperation<T> {
        T run() throws IOException;
    }
}
