package com.zcentury.v24;

import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Stateless live comparison. Receives source input only, never the Python result. */
final class LiveInformationMirror {
    static final String PATH = "/v1/mirror/canonical-product";
    static final int MAX_BYTES = 524288;

    private LiveInformationMirror() {}

    static void handle(HttpExchange exchange) throws IOException {
        try {
            if (!PATH.equals(exchange.getRequestURI().getPath())) {
                respond(exchange, 404, Map.of("error", "unknown_mirror_operation"));
                return;
            }
            if (!exchange.getRemoteAddress().getAddress().isLoopbackAddress()) {
                respond(exchange, 403, Map.of("error", "loopback_required"));
                return;
            }
            if (!"POST".equals(exchange.getRequestMethod())) {
                exchange.getResponseHeaders().set("Allow", "POST");
                respond(exchange, 405, Map.of("error", "post_required"));
                return;
            }
            byte[] bytes = exchange.getRequestBody().readNBytes(MAX_BYTES + 1);
            if (bytes.length > MAX_BYTES) {
                respond(exchange, 413, Map.of("error", "mirror_input_too_large"));
                return;
            }
            Map<String, Object> request = Json.object(Json.parse(new String(bytes, StandardCharsets.UTF_8)));
            if (!request.keySet().equals(Set.of("sampleId", "input", "dataVersion"))) {
                throw new IllegalArgumentException("mirror_input_fields_invalid");
            }
            if (!(request.get("sampleId") instanceof String sampleId)
                || !sampleId.matches("[0-9a-f]{32}")) {
                throw new IllegalArgumentException("sample_id_invalid");
            }
            Object version = request.get("dataVersion");
            if (version != null && !(version instanceof String)) {
                throw new IllegalArgumentException("data_version_invalid");
            }
            Map<String, Object> product = CanonicalProductMapper.build(
                Json.object(request.get("input")), (String) version);
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            result.put("schema", "v24.live_information_mirror.response.v1");
            result.put("sampleId", request.get("sampleId"));
            result.put("domain", "INFORMATION");
            result.put("inputWireHash", "sha256:" + HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(bytes)));
            result.put("shadowResultHash", product.get("productSnapshotHash"));
            result.put("sourceCommit", System.getenv().getOrDefault("V24_MIRROR_SOURCE_COMMIT", ""));
            result.put("releaseHash", System.getenv().getOrDefault("V24_MIRROR_RELEASE_HASH", ""));
            result.put("javaContractHash", System.getenv().getOrDefault("V24_MIRROR_CONTRACT_HASH", ""));
            result.put("productionMutationAllowed", false);
            result.put("authorityGrantCreated", false);
            respond(exchange, 200, result);
        } catch (Exception error) {
            // Do not return or log raw product data or exception messages.
            respond(exchange, 400, Map.of("error", "mirror_input_rejected"));
        } finally {
            exchange.close();
        }
    }

    private static void respond(HttpExchange exchange, int code, Map<String, Object> value) throws IOException {
        byte[] body = (Json.canonical(value) + "\n").getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(code, body.length);
        exchange.getResponseBody().write(body);
    }
}
