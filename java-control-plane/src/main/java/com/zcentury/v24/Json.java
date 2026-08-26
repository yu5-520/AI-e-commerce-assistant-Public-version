package com.zcentury.v24;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal dependency-free JSON parser plus Python-compatible canonical serializer. */
final class Json {
    private Json() {}

    static Object parse(String text) {
        Parser parser = new Parser(text);
        Object value = parser.parseValue();
        parser.skipWhitespace();
        if (!parser.end()) {
            throw new IllegalArgumentException("trailing_json_content_at:" + parser.index);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object value) {
        if (!(value instanceof Map<?, ?>)) {
            throw new IllegalArgumentException("json_object_required");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    static List<Object> array(Object value) {
        if (!(value instanceof List<?>)) {
            throw new IllegalArgumentException("json_array_required");
        }
        return (List<Object>) value;
    }

    static String canonical(Object value) {
        StringBuilder out = new StringBuilder();
        writeCanonical(value, out);
        return out.toString();
    }

    private static void writeCanonical(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
            return;
        }
        if (value instanceof String text) {
            writeString(text, out);
            return;
        }
        if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
            out.append(value);
            return;
        }
        if (value instanceof BigDecimal number) {
            out.append(number.toPlainString());
            return;
        }
        if (value instanceof Number number) {
            out.append(number);
            return;
        }
        if (value instanceof Map<?, ?> raw) {
            List<String> keys = new ArrayList<>();
            for (Object key : raw.keySet()) {
                keys.add(String.valueOf(key));
            }
            Collections.sort(keys);
            out.append('{');
            boolean first = true;
            for (String key : keys) {
                if (!first) out.append(',');
                first = false;
                writeString(key, out);
                out.append(':');
                writeCanonical(raw.get(key), out);
            }
            out.append('}');
            return;
        }
        if (value instanceof List<?> list) {
            out.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) out.append(',');
                writeCanonical(list.get(i), out);
            }
            out.append(']');
            return;
        }
        // Matches the existing Python canonicalizer's default=str behavior for unexpected values.
        writeString(String.valueOf(value), out);
    }

    private static void writeString(String value, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }

    private static final class Parser {
        private final String text;
        private int index;

        private Parser(String text) {
            this.text = text;
        }

        private boolean end() { return index >= text.length(); }

        private void skipWhitespace() {
            while (!end() && Character.isWhitespace(text.charAt(index))) index++;
        }

        private Object parseValue() {
            skipWhitespace();
            if (end()) throw error("unexpected_eof");
            char c = text.charAt(index);
            return switch (c) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't' -> { expect("true"); yield Boolean.TRUE; }
                case 'f' -> { expect("false"); yield Boolean.FALSE; }
                case 'n' -> { expect("null"); yield null; }
                default -> {
                    if (c == '-' || Character.isDigit(c)) yield parseNumber();
                    throw error("unexpected_character:" + c);
                }
            };
        }

        private Map<String, Object> parseObject() {
            expectChar('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (peek('}')) { index++; return result; }
            while (true) {
                skipWhitespace();
                String key = parseString();
                skipWhitespace();
                expectChar(':');
                result.put(key, parseValue());
                skipWhitespace();
                if (peek('}')) { index++; return result; }
                expectChar(',');
            }
        }

        private List<Object> parseArray() {
            expectChar('[');
            ArrayList<Object> result = new ArrayList<>();
            skipWhitespace();
            if (peek(']')) { index++; return result; }
            while (true) {
                result.add(parseValue());
                skipWhitespace();
                if (peek(']')) { index++; return result; }
                expectChar(',');
            }
        }

        private String parseString() {
            expectChar('"');
            StringBuilder result = new StringBuilder();
            while (!end()) {
                char c = text.charAt(index++);
                if (c == '"') return result.toString();
                if (c != '\\') {
                    result.append(c);
                    continue;
                }
                if (end()) throw error("unfinished_escape");
                char escaped = text.charAt(index++);
                switch (escaped) {
                    case '"' -> result.append('"');
                    case '\\' -> result.append('\\');
                    case '/' -> result.append('/');
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (index + 4 > text.length()) throw error("short_unicode_escape");
                        String hex = text.substring(index, index + 4);
                        index += 4;
                        result.append((char) Integer.parseInt(hex, 16));
                    }
                    default -> throw error("invalid_escape:" + escaped);
                }
            }
            throw error("unterminated_string");
        }

        private Number parseNumber() {
            int start = index;
            if (peek('-')) index++;
            while (!end() && Character.isDigit(text.charAt(index))) index++;
            boolean decimal = false;
            if (!end() && text.charAt(index) == '.') {
                decimal = true;
                index++;
                while (!end() && Character.isDigit(text.charAt(index))) index++;
            }
            if (!end() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (!end() && (text.charAt(index) == '+' || text.charAt(index) == '-')) index++;
                while (!end() && Character.isDigit(text.charAt(index))) index++;
            }
            String raw = text.substring(start, index);
            if (decimal) return new BigDecimal(raw);
            try { return Long.parseLong(raw); }
            catch (NumberFormatException ignored) { return new BigDecimal(raw); }
        }

        private boolean peek(char c) { return !end() && text.charAt(index) == c; }

        private void expect(String expected) {
            if (!text.startsWith(expected, index)) throw error("expected:" + expected);
            index += expected.length();
        }

        private void expectChar(char expected) {
            skipWhitespace();
            if (end() || text.charAt(index) != expected) throw error("expected_char:" + expected);
            index++;
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + "@" + index);
        }
    }
}
