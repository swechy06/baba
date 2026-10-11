package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.*;

class ConfigStoreTest {
    @TempDir
    Path directory;

    private Path dataFile() {
        return directory.resolve("ticket_data.json");
    }

    @Test
    void loadsPythonIdsWithoutChangingTheirTypesAndRoundTripsTurkishText() throws Exception {
        Files.writeString(dataFile(), """
                {"123":{"panel_channel":1234567890123456789,"panel_message_id":"987654321098765432",
                  "support_types":{"rutbe_destek":{"open_category":"444","closed_category":555,
                    "roles":["666",777],"log_channel":888}},"custom_note":"Rütbe şikâyeti"}}
                """, StandardCharsets.UTF_8);
        ConfigStore store = new ConfigStore(dataFile());

        ObjectNode guild = store.getGuild("123");
        assertEquals(1234567890123456789L, guild.path("panel_channel").longValue());
        assertTrue(guild.path("panel_message_id").isTextual());
        assertEquals("666", guild.path("support_types").path("rutbe_destek").path("roles").get(0).textValue());
        assertEquals(777, guild.path("support_types").path("rutbe_destek").path("roles").get(1).intValue());

        store.saveGuild("123", guild);
        assertEquals("Rütbe şikâyeti", new ConfigStore(dataFile()).getGuild("123").path("custom_note").textValue());
        assertTrue(Files.readString(dataFile()).contains("Rütbe şikâyeti"));
    }

    @Test
    void setupSavesPreserveCountersOtherGuildsAndUnknownNestedFields() {
        ConfigStore store = new ConfigStore(dataFile());
        ObjectNode original = SupportType.defaultGuild();
        original.put("future_option", "keep");
        original.withObject("support_types").withObject("gamepass_destek").put("future_option", "nested");
        store.saveGuild("123", original);
        store.saveGuild("456", SupportType.defaultGuild());
        assertEquals(1, store.nextTicketNumber("123", "gamepass_destek"));
        assertEquals(2, store.nextTicketNumber("123", "gamepass_destek"));

        ObjectNode setup = SupportType.defaultGuild();
        setup.put("panel_channel", "999");
        store.saveGuild("123", setup);

        ObjectNode persisted = store.getGuild("123");
        assertEquals(2, persisted.path("ticket_counters").path("gamepass_destek").intValue());
        assertEquals("keep", persisted.path("future_option").textValue());
        assertEquals("nested", persisted.path("support_types").path("gamepass_destek").path("future_option").textValue());
        assertEquals("999", persisted.path("panel_channel").textValue());
        assertNotNull(store.getGuild("456"));
    }

    @Test
    void explicitlySuppliedCountersReplacePreviousCounters() {
        ConfigStore store = new ConfigStore(dataFile());
        store.nextTicketNumber("123", "gamepass_destek");
        store.nextTicketNumber("123", "oyun_destek");
        ObjectNode settings = SupportType.defaultGuild();
        settings.putObject("ticket_counters").put("gamepass_destek", 20);
        store.saveGuild("123", settings);

        assertEquals(21, store.nextTicketNumber("123", "gamepass_destek"));
        assertEquals(1, store.nextTicketNumber("123", "oyun_destek"));
    }

    @Test
    void resetPreservesTicketMetadataAndOtherGuilds() {
        ConfigStore store = new ConfigStore(dataFile());
        store.saveGuild("123", SupportType.defaultGuild());
        store.saveGuild("456", SupportType.defaultGuild());
        ObjectNode ticket = ConfigStore.MAPPER.createObjectNode();
        ticket.put("opener_id", "987");
        ticket.put("type", "rutbe_destek");
        ticket.put("status", "review");
        store.saveTicket("123", "789", ticket);

        store.removeGuild("123");

        assertNull(store.getGuild("123"));
        assertNotNull(store.getGuild("456"));
        assertEquals(ticket, store.getTicket("123", "789"));
        store.removeTicket("123", "789");
        assertNull(store.getTicket("123", "789"));
        assertNotNull(store.getGuild("456"));
    }

    @Test
    void gettersAndSavesDoNotExposeMutableStorage() {
        ConfigStore store = new ConfigStore(dataFile());
        ObjectNode guild = SupportType.defaultGuild();
        store.saveGuild("123", guild);
        guild.put("panel_channel", "should-not-persist");
        ObjectNode fetched = store.getGuild("123");
        fetched.put("panel_channel", "also-should-not-persist");
        assertTrue(store.getGuild("123").path("panel_channel").isNull());

        ObjectNode ticket = ConfigStore.MAPPER.createObjectNode().put("status", "review");
        store.saveTicket("123", "789", ticket);
        ticket.put("status", "should-not-persist");
        store.getTicket("123", "789").put("status", "also-should-not-persist");
        assertEquals("review", store.getTicket("123", "789").path("status").textValue());
    }

    @Test
    void concurrentCountersAreUniqueAcrossMultipleStoreInstances() throws Exception {
        ConfigStore first = new ConfigStore(dataFile());
        ConfigStore second = new ConfigStore(dataFile());
        List<Callable<Integer>> jobs = IntStream.range(0, 100)
                .mapToObj(index -> (Callable<Integer>) () ->
                        (index % 2 == 0 ? first : second).nextTicketNumber("123", "gamepass_destek"))
                .toList();
        Set<Integer> results = new HashSet<>();
        try (var executor = Executors.newFixedThreadPool(8)) {
            for (Future<Integer> future : executor.invokeAll(jobs)) {
                results.add(future.get());
            }
        }

        assertEquals(100, results.size());
        assertEquals(Set.copyOf(IntStream.rangeClosed(1, 100).boxed().toList()), results);
        assertEquals(100, first.getGuild("123").path("ticket_counters").path("gamepass_destek").intValue());
        try (var children = Files.list(directory)) {
            assertEquals(List.of(dataFile()), children.toList());
        }
    }

    @Test
    void reReadsExternalChangesInsteadOfOverwritingOtherGuilds() throws Exception {
        ConfigStore store = new ConfigStore(dataFile());
        store.saveGuild("123", SupportType.defaultGuild());
        ObjectNode external = (ObjectNode) ConfigStore.MAPPER.readTree(dataFile().toFile());
        external.set("456", ConfigStore.MAPPER.createObjectNode().put("panel_channel", "999"));
        ConfigStore.MAPPER.writeValue(dataFile().toFile(), external);

        store.nextTicketNumber("123", "transfer");

        assertEquals("999", store.getGuild("456").path("panel_channel").textValue());
    }

    @Test
    void corruptJsonIsRejectedAndPreserved() throws Exception {
        String corrupt = "{\"123\": {broken";
        Files.writeString(dataFile(), corrupt);
        assertThrows(UncheckedIOException.class, () -> new ConfigStore(dataFile()));
        assertEquals(corrupt, Files.readString(dataFile()));
    }

    @Test
    void invalidRootDuplicateKeysAndTrailingJsonAreRejected() throws Exception {
        for (String invalid : List.of("[]", "null", "", "{\"123\":{},\"123\":{}}", "{} {}")) {
            Files.writeString(dataFile(), invalid);
            assertThrows(RuntimeException.class, () -> new ConfigStore(dataFile()), invalid);
            assertEquals(invalid, Files.readString(dataFile()));
        }
    }

    @Test
    void corruptionAfterStartupCannotBeOverwrittenByASave() throws Exception {
        ConfigStore store = new ConfigStore(dataFile());
        store.saveGuild("123", SupportType.defaultGuild());
        Files.writeString(dataFile(), "{broken");

        assertThrows(UncheckedIOException.class, () -> store.saveGuild("123", SupportType.defaultGuild()));
        assertEquals("{broken", Files.readString(dataFile()));
    }

    @Test
    void malformedOrOverflowingCounterFailsWithoutChangingTheFile() throws Exception {
        ConfigStore store = new ConfigStore(dataFile());
        for (String value : List.of("-1", "2147483647", "\"broken\"", "null")) {
            String json = "{\"123\":{\"ticket_counters\":{\"oyun_destek\":" + value + "}}}";
            Files.writeString(dataFile(), json);
            assertThrows(IllegalStateException.class, () -> store.nextTicketNumber("123", "oyun_destek"));
            assertEquals(json, Files.readString(dataFile()));
        }
    }

    @Test
    void defaultSchemaAndLabelsKeepOriginalSupportTypes() {
        assertEquals(6, SupportType.values().length);
        ObjectNode defaults = SupportType.defaultGuild();
        assertTrue(defaults.path("panel_channel").isNull());
        assertTrue(defaults.path("panel_message_id").isNull());
        for (SupportType type : SupportType.values()) {
            assertSame(type, SupportType.fromKey(type.key()));
            assertEquals(type.label() + " Bileti", type.panelLabel());
            var settings = defaults.path("support_types").path(type.key());
            assertTrue(settings.path("open_category").isNull());
            assertTrue(settings.path("closed_category").isNull());
            assertTrue(settings.path("roles").isArray());
            assertTrue(settings.path("roles").isEmpty());
            assertTrue(settings.path("log_channel").isNull());
        }
        defaults.put("panel_channel", "123");
        assertTrue(SupportType.defaultGuild().path("panel_channel").isNull());
        assertNull(SupportType.fromKey("unsupported"));
        assertNull(SupportType.fromKey(null));
    }
}
