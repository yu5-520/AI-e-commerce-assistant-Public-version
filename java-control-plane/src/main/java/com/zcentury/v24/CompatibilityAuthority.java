package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V24.19 deterministic compatibility authority. */
final class CompatibilityAuthority {
    private CompatibilityAuthority() {}

    static Map<String, Object> assess(
        Map<String, Object> current,
        Map<String, Object> candidate,
        Map<String, Object> contract
    ) {
        List<String> violations = new ArrayList<>();
        List<String> migrations = new ArrayList<>();

        for (Object raw : Json.array(contract.get("requiredIdentityFields"))) {
            String field = text(raw);
            if (field.isBlank()) continue;
            if (text(candidate.get(field)).isBlank()) violations.add("MISSING_IDENTITY:" + field);
        }

        for (Object raw : Json.array(contract.get("stableExactFields"))) {
            String field = text(raw);
            if (field.isBlank()) continue;
            String before = text(current.get(field));
            String after = text(candidate.get(field));
            if (!before.equals(after)) violations.add("EXACT_FIELD_CHANGED:" + field + ":" + before + "->" + after);
        }

        String currentSchema = text(current.get("dataSchemaVersion"));
        String candidateSchema = text(candidate.get("dataSchemaVersion"));
        boolean migrationRequired = !currentSchema.equals(candidateSchema);
        if (migrationRequired) {
            Map<String, Object> migration = findMigration(currentSchema, candidateSchema, contract);
            if (migration.isEmpty()) {
                violations.add("SCHEMA_MIGRATION_UNDECLARED:" + currentSchema + "->" + candidateSchema);
            } else {
                if (!Boolean.TRUE.equals(migration.get("explicit"))) {
                    violations.add("SCHEMA_MIGRATION_NOT_EXPLICIT:" + currentSchema + "->" + candidateSchema);
                }
                if (!Boolean.TRUE.equals(migration.get("preflightRequired"))) {
                    violations.add("SCHEMA_MIGRATION_PREFLIGHT_NOT_REQUIRED:" + currentSchema + "->" + candidateSchema);
                }
                migrations.add(text(migration.get("migrationId")));
            }
        }

        Set<String> currentCapabilities = stringSet(current.get("capabilities"));
        Set<String> candidateCapabilities = stringSet(candidate.get("capabilities"));
        if (Boolean.TRUE.equals(contract.get("capabilitySupersetRequired"))) {
            for (String capability : currentCapabilities) {
                if (!candidateCapabilities.contains(capability)) {
                    violations.add("CAPABILITY_REMOVED:" + capability);
                }
            }
        }
        for (Object raw : Json.array(contract.get("requiredCapabilities"))) {
            String capability = text(raw);
            if (!capability.isBlank() && !candidateCapabilities.contains(capability)) {
                violations.add("REQUIRED_CAPABILITY_MISSING:" + capability);
            }
        }

        String releaseHash = text(candidate.get("releaseHash"));
        if (!releaseHash.startsWith("sha256:") || releaseHash.length() != 71) {
            violations.add("INVALID_RELEASE_HASH");
        }

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.compatibility_decision.v1");
        material.put("decision", violations.isEmpty() ? "COMPATIBLE" : "INCOMPATIBLE");
        material.put("migrationRequired", migrationRequired);
        material.put("migrationIds", migrations);
        material.put("violations", violations);
        material.put("currentReleaseHash", current.get("releaseHash"));
        material.put("candidateReleaseHash", candidate.get("releaseHash"));
        material.put("currentSchemaVersion", currentSchema);
        material.put("candidateSchemaVersion", candidateSchema);
        material.put("implicitFallbackAllowed", false);
        material.put("defaultDeny", true);
        material.put("contractHash", Hashing.canonicalHash(contract));
        material.put("compatibilityHash", Hashing.canonicalHash(material));
        return material;
    }

    private static Map<String, Object> findMigration(String from, String to, Map<String, Object> contract) {
        for (Object raw : Json.array(contract.get("schemaMigrations"))) {
            Map<String, Object> item = object(raw);
            if (from.equals(text(item.get("from"))) && to.equals(text(item.get("to")))) return item;
        }
        return Map.of();
    }

    private static Set<String> stringSet(Object value) {
        LinkedHashSet<String> result = new LinkedHashSet<>();
        for (Object raw : Json.array(value)) {
            String item = text(raw);
            if (!item.isBlank()) result.add(item);
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? (Map<String, Object>) value : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
