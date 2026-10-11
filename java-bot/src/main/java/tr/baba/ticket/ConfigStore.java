package tr.baba.ticket;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Reads the original Python JSON layout without converting Discord snowflakes.
 * All writes replace the file atomically; a corrupt file is never reset silently.
 */
public final class ConfigStore {
    public static final ObjectMapper MAPPER = JsonMapper.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();

    private static final String TICKETS_KEY = "_java_tickets";
    private static final Map<Path, Object> FILE_LOCKS = new ConcurrentHashMap<>();

    private final Path file;
    private final Object lock;

    public ConfigStore(Path file) {
        this.file = Objects.requireNonNull(file, "file").toAbsolutePath().normalize();
        this.lock = FILE_LOCKS.computeIfAbsent(this.file, ignored -> new Object());
        synchronized (lock) {
            read();
        }
    }

    public ObjectNode getGuild(String guildId) {
        checkId(guildId);
        synchronized (lock) {
            ObjectNode guild = objectAt(read(), guildId);
            return guild == null ? null : guild.deepCopy();
        }
    }

    public void saveGuild(String guildId, ObjectNode settings) {
        checkId(guildId);
        Objects.requireNonNull(settings, "settings");
        synchronized (lock) {
            ObjectNode root = read();
            ObjectNode previous = objectAt(root, guildId);
            ObjectNode merged = previous == null ? MAPPER.createObjectNode() : previous.deepCopy();
            merge(merged, settings);
            // An explicitly provided counter object is authoritative. A setup wizard
            // that does not supply it must retain the existing ticket numbering.
            if (settings.has("ticket_counters")) {
                merged.set("ticket_counters", settings.get("ticket_counters").deepCopy());
            }
            root.set(guildId, merged);
            write(root);
        }
    }

    public void removeGuild(String guildId) {
        checkId(guildId);
        synchronized (lock) {
            ObjectNode root = read();
            root.remove(guildId);
            // Open/closed ticket metadata outlives a panel configuration reset.
            write(root);
        }
    }

    public int nextTicketNumber(String guildId, String ticketType) {
        checkId(guildId);
        if (SupportType.fromKey(ticketType) == null) {
            throw new IllegalArgumentException("Bilinmeyen bilet türü: " + ticketType);
        }
        synchronized (lock) {
            ObjectNode root = read();
            ObjectNode guild = objectAt(root, guildId);
            if (guild == null) {
                guild = SupportType.defaultGuild();
                root.set(guildId, guild);
            }
            ObjectNode counters = objectAt(guild, "ticket_counters");
            if (counters == null) {
                counters = guild.putObject("ticket_counters");
            }
            JsonNode current = counters.get(ticketType);
            int number = 0;
            if (current != null) {
                if (!current.isIntegralNumber() || !current.canConvertToInt()
                        || current.intValue() < 0 || current.intValue() == Integer.MAX_VALUE) {
                    throw new IllegalStateException("Bilet sayacı geçersiz: " + ticketType);
                }
                number = current.intValue();
            }
            int next = number + 1;
            counters.put(ticketType, next);
            write(root);
            return next;
        }
    }

    public ObjectNode getTicket(String guildId, String channelId) {
        checkId(guildId);
        checkId(channelId);
        synchronized (lock) {
            ObjectNode tickets = objectAt(read(), TICKETS_KEY);
            ObjectNode guildTickets = tickets == null ? null : objectAt(tickets, guildId);
            ObjectNode ticket = guildTickets == null ? null : objectAt(guildTickets, channelId);
            return ticket == null ? null : ticket.deepCopy();
        }
    }

    public void saveTicket(String guildId, String channelId, ObjectNode ticket) {
        checkId(guildId);
        checkId(channelId);
        Objects.requireNonNull(ticket, "ticket");
        synchronized (lock) {
            ObjectNode root = read();
            ObjectNode tickets = objectAt(root, TICKETS_KEY);
            if (tickets == null) {
                tickets = root.putObject(TICKETS_KEY);
            }
            ObjectNode guildTickets = objectAt(tickets, guildId);
            if (guildTickets == null) {
                guildTickets = tickets.putObject(guildId);
            }
            guildTickets.set(channelId, ticket.deepCopy());
            write(root);
        }
    }

    public void removeTicket(String guildId, String channelId) {
        checkId(guildId);
        checkId(channelId);
        synchronized (lock) {
            ObjectNode root = read();
            ObjectNode tickets = objectAt(root, TICKETS_KEY);
            ObjectNode guildTickets = tickets == null ? null : objectAt(tickets, guildId);
            if (guildTickets != null) {
                guildTickets.remove(channelId);
                if (guildTickets.isEmpty()) {
                    tickets.remove(guildId);
                }
                write(root);
            }
        }
    }

    private ObjectNode read() {
        if (!Files.exists(file)) {
            return MAPPER.createObjectNode();
        }
        try {
            JsonNode root = MAPPER.readTree(file.toFile());
            if (!(root instanceof ObjectNode object)) {
                throw new IllegalStateException("Kayıt dosyası bir JSON nesnesi olmalı: " + file);
            }
            return object;
        } catch (IOException exception) {
            throw new UncheckedIOException("Kayıt dosyası okunamadı; mevcut dosya korunuyor: " + file, exception);
        }
    }

    private void write(ObjectNode root) {
        Path temporary = null;
        try {
            Path parent = file.getParent();
            Files.createDirectories(parent);
            temporary = Files.createTempFile(parent, ".ticket-data-", ".tmp");
            byte[] bytes = MAPPER.writerWithDefaultPrettyPrinter().writeValueAsBytes(root);
            try (FileChannel output = FileChannel.open(temporary, StandardOpenOption.WRITE,
                    StandardOpenOption.TRUNCATE_EXISTING)) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) {
                    output.write(buffer);
                }
                output.force(true);
            }
            // No non-atomic fallback: an unsupported filesystem fails safely.
            Files.move(temporary, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException exception) {
            throw new UncheckedIOException("Kayıt dosyası kaydedilemedi; önceki dosya korunuyor: " + file, exception);
        } finally {
            if (temporary != null) {
                try {
                    Files.deleteIfExists(temporary);
                } catch (IOException ignored) {
                    // Do not mask the actual read/write failure with cleanup failure.
                }
            }
        }
    }

    private static ObjectNode objectAt(ObjectNode parent, String key) {
        JsonNode value = parent.get(key);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!(value instanceof ObjectNode object)) {
            throw new IllegalStateException("Kayıt dosyasındaki alan JSON nesnesi olmalı: " + key);
        }
        return object;
    }

    private static void merge(ObjectNode destination, ObjectNode incoming) {
        for (Map.Entry<String, JsonNode> field : incoming.properties()) {
            JsonNode existing = destination.get(field.getKey());
            if (existing instanceof ObjectNode existingObject
                    && field.getValue() instanceof ObjectNode incomingObject) {
                merge(existingObject, incomingObject);
            } else {
                destination.set(field.getKey(), field.getValue().deepCopy());
            }
        }
    }

    private static void checkId(String id) {
        if (id == null || id.isBlank() || TICKETS_KEY.equals(id)) {
            throw new IllegalArgumentException("Sunucu veya kanal kimliği geçersiz.");
        }
    }
}
