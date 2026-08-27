package com.zcentury.v24;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

final class Hashing {
    private Hashing() {}

    static String canonicalHash(Object value) {
        return "sha256:" + hex(digest(Json.canonical(value).getBytes(StandardCharsets.UTF_8)));
    }

    static String fileHash(Path path) throws IOException {
        return "sha256:" + hex(digest(Files.readAllBytes(path)));
    }

    private static byte[] digest(byte[] bytes) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException("sha256_unavailable", exc);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) out.append(String.format("%02x", value & 0xff));
        return out.toString();
    }
}
