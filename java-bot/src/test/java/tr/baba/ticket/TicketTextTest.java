package tr.baba.ticket;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class TicketTextTest {
    @Test void legacyTopicPreservesPipesInsideReason() {
        TicketText.Topic topic = TicketText.parseTopic("oyun_destek|Hata | oyun açılmıyor");
        assertEquals("oyun_destek", topic.type());
        assertEquals("Hata | oyun açılmıyor", topic.reason());
        assertEquals("Belirtilmedi", TicketText.parseTopic(null).reason());
    }

    @Test void longReasonStillFitsDiscordTopicWithoutBreakingEmoji() {
        String topic = TicketText.topic("gamepass_destek", "x".repeat(1008) + "🎫".repeat(30));
        assertTrue(topic.length() <= 1024);
        assertFalse(Character.isHighSurrogate(topic.charAt(topic.length() - 1)));
        assertEquals("gamepass_destek", TicketText.parseTopic(topic).type());
    }

    @Test void importsPythonMentionCounterAndStaffStatus() {
        assertEquals("123456789012345678", TicketText.firstUserId("👤 **<@!123456789012345678>** tarafından ticket talebi"));
        assertEquals(42, TicketText.ticketNumber("🎫 Destek Talebi #0042"));
        String information = "**📊 Destek Durumu:** ✅ Çözüldü\n\n**👤 Biletle İlgilenen Yetkili:** <@555555555555555555>";
        assertEquals("solved", TicketText.legacyStatus(information));
        assertEquals("555555555555555555", TicketText.legacyClaimer(information));
        assertEquals("review", TicketText.legacyStatus("Talep sebebi: Beklemede demesine rağmen\n**📊 Destek Durumu:** 🔎 İnceleniyor"));
    }

    @Test void malformedHugeCounterCannotCrashTicketImport() {
        assertEquals(0, TicketText.ticketNumber("🎫 Destek Talebi #999999999999999999999999999"));
        assertEquals("", TicketText.firstUserId("Yetkili henüz yok"));
    }

    @Test void transcriptUsesTurkishTimeAndKeepsContentAttachmentsAndEmbeds() {
        String entry = TicketText.transcriptEntry(OffsetDateTime.parse("2026-10-11T07:15:00Z"), "Üye", "123",
                "Satır 1\nSatır 2", List.of("https://cdn.discordapp.com/a.png"), List.of("[Kart] Destek bilgisi"));
        assertTrue(entry.startsWith("[11.10.2026 10:15] Üye (123):"));
        assertTrue(entry.contains("Satır 1\nSatır 2"));
        assertTrue(entry.contains("📎 https://cdn.discordapp.com/a.png"));
        assertTrue(entry.contains("[Kart] Destek bilgisi"));
    }

    @Test void splitTranscriptRespectsUploadBytesAndPreservesEveryUnicodeCharacter() {
        String source = "İğşıçö🎫😀\n".repeat(15);
        List<byte[]> parts = TicketText.splitUtf8(source, 23);
        StringBuilder rebuilt = new StringBuilder();
        for (byte[] part : parts) {
            assertTrue(part.length <= 23);
            String decoded = new String(part, StandardCharsets.UTF_8);
            assertFalse(decoded.contains("\uFFFD"));
            rebuilt.append(decoded);
        }
        assertEquals(source, rebuilt.toString());
    }
}
