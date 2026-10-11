package tr.baba.ticket;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Discord-independent formatting shared by new tickets and imported Python tickets. */
final class TicketText {
    static final ZoneId TURKEY = ZoneId.of("Europe/Istanbul");
    static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm");
    private static final Pattern USER_MENTION = Pattern.compile("<@!?(\\d+)>");
    private static final Pattern NUMBER = Pattern.compile("#(\\d+)");

    private TicketText() {}

    record Topic(String type, String reason) {}

    static Topic parseTopic(String topic) {
        if (topic == null || topic.isBlank()) return new Topic("", "Belirtilmedi");
        String[] parts = topic.split("\\|", 2);
        return new Topic(parts[0], parts.length > 1 && !parts[1].isBlank() ? parts[1] : "Belirtilmedi");
    }

    static String topic(String type, String reason) {
        // Discord topics allow 1024 characters. Full reasons remain in runtime metadata.
        return clip(type + "|" + reason, 1024);
    }

    static String clip(String value, int length) {
        if (value == null || value.isEmpty()) return "Belirtilmedi";
        if (value.length() <= length) return value;
        int end = length;
        if (Character.isHighSurrogate(value.charAt(end - 1))) end--;
        return value.substring(0, end);
    }

    static String firstUserId(String text) {
        Matcher matcher = USER_MENTION.matcher(text == null ? "" : text);
        return matcher.find() ? matcher.group(1) : "";
    }

    static int ticketNumber(String title) {
        Matcher matcher = NUMBER.matcher(title == null ? "" : title);
        if (!matcher.find()) return 0;
        try { return Integer.parseInt(matcher.group(1)); }
        catch (NumberFormatException ignored) { return 0; }
    }

    static String legacyStatus(String information) {
        String value = information == null ? "" : information;
        String statusLine = value.lines().filter(line -> line.contains("Destek Durumu:")).findFirst().orElse("");
        if (statusLine.contains("Beklemede")) return "wait";
        if (statusLine.contains("Çözüldü")) return "solved";
        return "review";
    }

    static String legacyClaimer(String information) {
        return information == null ? "" : information.lines()
                .filter(line -> line.contains("Biletle İlgilenen Yetkili:"))
                .map(TicketText::firstUserId).findFirst().orElse("");
    }

    static String time(OffsetDateTime value) {
        return value.atZoneSameInstant(TURKEY).format(TIME);
    }

    static String transcriptEntry(OffsetDateTime created, String author, String authorId,
                                  String content, List<String> attachments, List<String> embeds) {
        StringBuilder out = new StringBuilder("[").append(time(created)).append("] ")
                .append(author).append(" (").append(authorId).append("):\n");
        if (content != null && !content.isEmpty()) out.append(content).append('\n');
        for (String url : attachments) out.append("📎 ").append(url).append('\n');
        for (String embed : embeds) out.append(embed).append('\n');
        return out.append("\n--------------------------------\n\n").toString();
    }

    /** Split only between Unicode code points so uploaded transcript parts remain valid UTF-8. */
    static List<byte[]> splitUtf8(String text, int maximumBytes) {
        if (maximumBytes < 4) throw new IllegalArgumentException("Transcript part size must be at least 4 bytes");
        List<byte[]> parts = new ArrayList<>();
        StringBuilder part = new StringBuilder();
        int bytes = 0;
        for (int i = 0; i < text.length();) {
            int point = text.codePointAt(i);
            String character = new String(Character.toChars(point));
            int size = character.getBytes(StandardCharsets.UTF_8).length;
            if (bytes + size > maximumBytes) {
                parts.add(part.toString().getBytes(StandardCharsets.UTF_8));
                part.setLength(0);
                bytes = 0;
            }
            part.append(character);
            bytes += size;
            i += Character.charCount(point);
        }
        if (!part.isEmpty() || parts.isEmpty()) parts.add(part.toString().getBytes(StandardCharsets.UTF_8));
        return parts;
    }
}
