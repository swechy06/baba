package tr.baba.ticket;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Reads Python-style single-line .env values; never logs credential values. */
final class Environment {
    private Environment() {}

    static Map<String, String> readDotEnv(Path path) throws IOException {
        Map<String, String> values = new LinkedHashMap<>();
        if (!Files.exists(path)) return values;
        String content = Files.readString(path,StandardCharsets.UTF_8);
        if (content.startsWith("\uFEFF")) content = content.substring(1);
        for (String raw : content.split("\\R")) {
            String line = raw.strip();
            if (line.isEmpty() || line.startsWith("#")) continue;
            if (line.startsWith("export ")) line = line.substring(7).strip();
            int separator = line.indexOf('=');
            if (separator < 1) continue;
            String key = line.substring(0, separator).strip();
            if (!key.matches("[A-Za-z_][A-Za-z0-9_]*")) continue;
            String value = parseValue(line.substring(separator + 1).strip());
            values.put(key, value);
        }
        return values;
    }

    private static String parseValue(String raw) {
        if (raw.isEmpty()) return "";
        char quote = raw.charAt(0);
        if (quote == '\'' || quote == '"') {
            StringBuilder value = new StringBuilder();
            for (int i = 1; i < raw.length(); i++) {
                char c = raw.charAt(i);
                if (c == quote) return value.toString();
                if (c == '\\' && i + 1 < raw.length()) {
                    char next = raw.charAt(i + 1);
                    if (next == quote || next == '\\') { value.append(next); i++; continue; }
                    if (quote == '"') {
                        switch (next) {
                            case 'n' -> { value.append('\n'); i++; continue; }
                            case 'r' -> { value.append('\r'); i++; continue; }
                            case 't' -> { value.append('\t'); i++; continue; }
                        }
                    }
                }
                value.append(c);
            }
            throw new IllegalArgumentException(".env dosyasında kapanmamış tırnak var.");
        }
        for (int i = 0; i < raw.length(); i++) {
            if (raw.charAt(i) == '#' && (i == 0 || Character.isWhitespace(raw.charAt(i - 1)))) return raw.substring(0,i).strip();
        }
        return raw;
    }

    static String requireToken(Path envFile) throws IOException {
        String token = System.getenv("DISCORD_TOKEN");
        if (token == null || token.isBlank()) token = readDotEnv(envFile).get("DISCORD_TOKEN");
        if (token == null || token.isBlank()) {
            throw new IllegalStateException("DISCORD_TOKEN bulunamadı. Mevcut .env dosyanızı bot klasörüne kopyalayın.");
        }
        return token.strip();
    }
}
