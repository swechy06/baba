package tr.baba.ticket;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class EnvironmentTest {
    @TempDir
    Path directory;

    private Map<String, String> parse(String text) throws Exception {
        Path file = directory.resolve(".env");
        Files.writeString(file, text);
        return Environment.readDotEnv(file);
    }

    @Test
    void acceptsBomExportQuotedValuesAndCommentsWithoutPrintingCredentials() throws Exception {
        Map<String, String> values = parse("""
                \uFEFF# Example-only fixture; no actual credentials
                export DISCORD_TOKEN="example-only"
                TITLE='Rütbe Destek'
                PLAIN = value # a comment
                WITH_EQUALS=first=second
                EMPTY=
                """);
        assertEquals(Map.of("DISCORD_TOKEN", "example-only", "TITLE", "Rütbe Destek",
                "PLAIN", "value", "WITH_EQUALS", "first=second", "EMPTY", ""), values);
    }

    @Test
    void removesCommentsAfterQuotedValuesAndKeepsHashInsideQuotes() throws Exception {
        Map<String, String> values = parse("""
                FIRST="example-only" # explanation
                SECOND='another-example' # explanation
                HASH="value # still part of the value" # trailing comment
                RAW_HASH=value#literal
                """);
        assertEquals("example-only", values.get("FIRST"));
        assertEquals("another-example", values.get("SECOND"));
        assertEquals("value # still part of the value", values.get("HASH"));
        assertEquals("value#literal", values.get("RAW_HASH"));
    }

    @Test
    void skipsEmptyKeysMalformedLinesAndComments() throws Exception {
        Map<String, String> values = parse("""
                # comment
                missing separator
                =ignored
                   =also-ignored
                GOOD=kept
                """);
        assertEquals(Map.of("GOOD", "kept"), values);
    }

    @Test
    void onlyTreatsLeadingBomAsFormatting() throws Exception {
        Map<String, String> values = parse("\uFEFFFIRST=one\nINNER=left\uFEFFright\n");
        assertEquals("one", values.get("FIRST"));
        assertEquals("left\uFEFFright", values.get("INNER"));
    }

    @Test
    void handlesAbsentFileAndLastAssignmentWins() throws Exception {
        assertTrue(Environment.readDotEnv(directory.resolve("absent.env")).isEmpty());
        assertEquals("last", parse("SAMPLE=first\nSAMPLE=last\n").get("SAMPLE"));
    }
}
