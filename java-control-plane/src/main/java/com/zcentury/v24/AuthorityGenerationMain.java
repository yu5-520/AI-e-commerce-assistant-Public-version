package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Root-invoked CLI for durable V24 Authority Generation state. */
public final class AuthorityGenerationMain {
    private static final Path DEFAULT_STATE = Path.of(
        "/opt/ai-ecommerce-assistant/shared/v24/authority-generation.json"
    );

    private AuthorityGenerationMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 1) usage();
        Path statePath = Path.of(
            System.getenv().getOrDefault("V24_AUTHORITY_STATE_PATH", DEFAULT_STATE.toString())
        ).toAbsolutePath().normalize();
        AuthorityGenerationStore store = new AuthorityGenerationStore(statePath);
        Map<String, Object> result;
        switch (args[0]) {
            case "status" -> result = store.status();
            case "prepare" -> {
                if (args.length != 5) usage();
                Map<String, Object> proof = Json.object(Json.parse(
                    Files.readString(Path.of(args[4]), StandardCharsets.UTF_8)
                ));
                result = store.prepare(args[1], args[2], args[3], proof);
            }
            case "rollback" -> {
                if (args.length < 2 || args.length > 3) usage();
                result = store.rollback(args[1], args.length == 3 ? args[2] : "operator_rollback");
            }
            case "matches" -> {
                if (args.length != 4) usage();
                boolean matched = store.matches(Long.parseLong(args[1]), args[2], Long.parseLong(args[3]));
                LinkedHashMap<String, Object> value = new LinkedHashMap<>();
                value.put("schema", "v24.authority-generation.match.v1");
                value.put("matched", matched);
                result = value;
            }
            case "activate" -> result = store.activateForbidden();
            default -> {
                usage();
                return;
            }
        }
        System.out.println(Json.canonical(result));
    }

    private static void usage() {
        throw new IllegalArgumentException(
            "usage: status | prepare <expectedStateHash> <sourceCommit> <releaseHash> <proof.json>"
                + " | rollback <expectedStateHash> [reason] | matches <seq> <hash> <token> | activate"
        );
    }
}
