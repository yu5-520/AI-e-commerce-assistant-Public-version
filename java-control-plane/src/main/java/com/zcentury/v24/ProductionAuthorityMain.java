package com.zcentury.v24;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;

/**
 * V24.21 production-packaged control-plane readiness service.
 *
 * This process is intentionally incapable of taking production authority. It proves that the
 * sealed Java runtime and control-plane artifact can start on ECS before one atomic Authority
 * Generation transfers Compatibility, Runtime Admission, Gate/Task State, Queue/Generation and
 * Frontend Head/SSE ownership. Deployment and legacy removal remain outside this service.
 */
public final class ProductionAuthorityMain {
    static final String VERSION = "24.21.0";
    static final String MODE = "READY_NO_AUTHORITY";

    private ProductionAuthorityMain() {}

    public static void main(String[] args) throws Exception {
        String configuredMode = System.getenv().getOrDefault("V24_AUTHORITY_MODE", MODE);
        if (!MODE.equals(configuredMode)) {
            throw new IllegalStateException("production_authority_activation_forbidden_before_generation_cutover");
        }
        String host = System.getenv().getOrDefault("V24_AUTHORITY_HOST", "127.0.0.1");
        int port = parsePort(System.getenv().getOrDefault("V24_AUTHORITY_PORT", "39024"));
        HttpServer server = HttpServer.create(new InetSocketAddress(host, port), 32);
        server.createContext("/healthz", exchange -> write(exchange, 200, health()));
        server.createContext("/readyz", exchange -> write(exchange, 200, status()));
        server.createContext("/v1/authority/status", exchange -> write(exchange, 200, status()));
        server.setExecutor(Executors.newFixedThreadPool(2));
        Runtime.getRuntime().addShutdownHook(new Thread(() -> server.stop(1), "v24-authority-shutdown"));
        server.start();
        System.out.println(Json.canonical(Map.of(
            "event", "V24_PRODUCTION_AUTHORITY_READY",
            "host", host,
            "port", port,
            "mode", MODE,
            "version", VERSION
        )));
        new CountDownLatch(1).await();
    }

    private static Map<String, Object> health() {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", "v24.production-authority.health.v1");
        value.put("version", VERSION);
        value.put("mode", MODE);
        value.put("healthy", true);
        value.put("javaVersion", System.getProperty("java.runtime.version"));
        value.put("javaVendor", System.getProperty("java.vendor"));
        value.put("observedAt", Instant.now().toString());
        value.put("healthHash", Hashing.canonicalHash(value));
        return value;
    }

    private static Map<String, Object> status() {
        LinkedHashMap<String, Object> owners = new LinkedHashMap<>();
        owners.put("compatibility", "PYTHON_BASH_PRODUCTION");
        owners.put("runtimeAdmission", "PYTHON_BASH_PRODUCTION");
        owners.put("gateAndTaskState", "PYTHON_PRODUCTION");
        owners.put("queueAndGeneration", "PYTHON_PRODUCTION");
        owners.put("frontendHeadAndSse", "PYTHON_PRODUCTION");
        owners.put("deployment", "BASH_SYSTEMD_PRODUCTION");
        owners.put("legacyRemoval", "DISABLED");

        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", "v24.production-authority.status.v1");
        value.put("version", VERSION);
        value.put("mode", MODE);
        value.put("ready", true);
        value.put("authorityGeneration", null);
        value.put("candidateAuthorities", List.of(
            "COMPATIBILITY",
            "RUNTIME_ADMISSION",
            "GATE_TASK_STATE",
            "QUEUE_GENERATION",
            "FRONTEND_HEAD_SSE"
        ));
        value.put("owners", owners);
        value.put("productionMutationAllowed", false);
        value.put("deploymentAuthorityTransferAllowed", false);
        value.put("legacyRemovalAllowed", false);
        value.put("statusHash", Hashing.canonicalHash(value));
        return value;
    }

    private static void write(HttpExchange exchange, int code, Map<String, Object> payload) throws IOException {
        if (!"GET".equals(exchange.getRequestMethod())) {
            exchange.getResponseHeaders().set("Allow", "GET");
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }
        byte[] body = (Json.canonical(payload) + "\n").getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(code, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static int parsePort(String raw) {
        int port = Integer.parseInt(raw);
        if (port < 1024 || port > 65535) throw new IllegalArgumentException("invalid_authority_port");
        return port;
    }
}
