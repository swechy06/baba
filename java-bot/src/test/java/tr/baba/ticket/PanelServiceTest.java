package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.EmbedBuilder;
import net.dv8tion.jda.api.JDA;
import net.dv8tion.jda.api.Permission;
import net.dv8tion.jda.api.components.MessageTopLevelComponent;
import net.dv8tion.jda.api.components.actionrow.ActionRow;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.components.tree.MessageComponentTree;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.Message;
import net.dv8tion.jda.api.entities.MessageEmbed;
import net.dv8tion.jda.api.entities.Member;
import net.dv8tion.jda.api.entities.SelfUser;
import net.dv8tion.jda.api.entities.User;
import net.dv8tion.jda.api.entities.channel.concrete.Category;
import net.dv8tion.jda.api.entities.channel.concrete.TextChannel;
import net.dv8tion.jda.api.exceptions.ErrorResponseException;
import net.dv8tion.jda.api.events.interaction.command.SlashCommandInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.ButtonInteractionEvent;
import net.dv8tion.jda.api.interactions.InteractionHook;
import net.dv8tion.jda.api.requests.ErrorResponse;
import net.dv8tion.jda.api.requests.RestAction;
import net.dv8tion.jda.api.requests.restaction.AuditableRestAction;
import net.dv8tion.jda.api.requests.restaction.MessageCreateAction;
import net.dv8tion.jda.api.requests.restaction.WebhookMessageEditAction;
import net.dv8tion.jda.api.requests.restaction.interactions.ReplyCallbackAction;
import net.dv8tion.jda.api.requests.restaction.interactions.MessageEditCallbackAction;
import net.dv8tion.jda.api.requests.restaction.pagination.MessagePaginationAction;
import net.dv8tion.jda.api.requests.restaction.pagination.PaginationAction;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class PanelServiceTest {
    private static final long BOT_ID = 999;
    @TempDir
    Path directory;

    private Guild guild() {
        Guild guild = mock(Guild.class);
        JDA jda = mock(JDA.class);
        SelfUser bot = mock(SelfUser.class);
        when(guild.getId()).thenReturn("100");
        when(guild.getJDA()).thenReturn(jda);
        when(jda.getSelfUser()).thenReturn(bot);
        when(bot.getIdLong()).thenReturn(BOT_ID);
        return guild;
    }

    private Message message(long authorId, String title, List<ActionRow> rows) {
        Message message = mock(Message.class);
        User author = mock(User.class);
        when(author.getIdLong()).thenReturn(authorId);
        when(message.getAuthor()).thenReturn(author);
        when(message.getEmbeds()).thenReturn(List.of(new EmbedBuilder().setTitle(title).build()));
        when(message.getComponentTree()).thenReturn(MessageComponentTree.of(rows));
        return message;
    }

    private Message panel(String id, String title) {
        Message message = message(BOT_ID, title, PanelService.panelButtons());
        when(message.getId()).thenReturn(id);
        return message;
    }

    @SuppressWarnings("unchecked")
    private AuditableRestAction<Void> deletion(Message message) {
        AuditableRestAction<Void> action = mock(AuditableRestAction.class);
        when(message.delete()).thenReturn(action);
        return action;
    }

    @SuppressWarnings("unchecked")
    private RestAction<Message> retrieve(TextChannel channel, String id, Message message) {
        RestAction<Message> action = mock(RestAction.class);
        when(channel.retrieveMessageById(id)).thenReturn(action);
        when(action.complete()).thenReturn(message);
        return action;
    }

    @SuppressWarnings("unchecked")
    private void history(TextChannel channel, Message... messages) {
        MessagePaginationAction history = mock(MessagePaginationAction.class);
        when(channel.getIterableHistory()).thenReturn(history);
        var source = Arrays.asList(messages).iterator();
        PaginationAction.PaginationIterator<Message> iterator = mock(PaginationAction.PaginationIterator.class);
        when(iterator.hasNext()).thenAnswer(ignored -> source.hasNext());
        when(iterator.next()).thenAnswer(ignored -> source.next());
        when(history.iterator()).thenReturn(iterator);
    }

    private MessageCreateAction publishAction(TextChannel channel, Message message) {
        MessageCreateAction action = mock(MessageCreateAction.class, RETURNS_SELF);
        when(channel.sendMessageEmbeds(any(MessageEmbed.class))).thenReturn(action);
        when(action.complete()).thenReturn(message);
        return action;
    }

    private ErrorResponseException error(ErrorResponse response) {
        ErrorResponseException error = mock(ErrorResponseException.class);
        when(error.getErrorResponse()).thenReturn(response);
        return error;
    }

    @Test
    void recognizesOnlyBotOwnedJavaOrPythonPanelsWithAllSixSupportLabels() {
        assertTrue(PanelService.isPanel(panel("300", PanelService.TITLE), BOT_ID));
        assertTrue(PanelService.isPanel(panel("300", "# Destek Talebi Oluştur #"), BOT_ID));
        assertFalse(PanelService.isPanel(message(888, PanelService.TITLE, PanelService.panelButtons()), BOT_ID));
        assertFalse(PanelService.isPanel(message(BOT_ID, "Bilet Bilgisi", PanelService.panelButtons()), BOT_ID));
        assertFalse(PanelService.isPanel(message(BOT_ID, PanelService.TITLE,
                List.of(ActionRow.of(Button.secondary("a", "Gamepass Destek Bileti")))), BOT_ID));

        List<Button> pythonButtons = Arrays.stream(SupportType.values())
                .map(type -> Button.secondary("legacy-random-" + type.key(), type.panelLabel())).toList();
        assertTrue(PanelService.isPanel(message(BOT_ID, "# Destek Talebi Oluştur #", List.of(
                ActionRow.of(pythonButtons.subList(0, 3)), ActionRow.of(pythonButtons.subList(3, 6)))), BOT_ID));
    }

    @Test
    void publishesNewMessageIdWhileKeepingCountersAndPreviousReferences() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild();
        settings.put("panel_channel", 200);
        settings.put("panel_message_id", 301);
        settings.putArray("panel_messages").addObject().put("channel_id", "201").put("message_id", "302");
        settings.put("future_option", "keep");
        store.saveGuild("100", settings);
        store.nextTicketNumber("100", "gamepass_destek");
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message sent = panel("300", PanelService.TITLE);
        publishAction(channel, sent);

        assertSame(sent, new PanelService(store).publish(guild, settings));

        ObjectNode saved = store.getGuild("100");
        assertEquals("300", saved.path("panel_message_id").textValue());
        assertEquals(1, saved.path("ticket_counters").path("gamepass_destek").intValue());
        assertEquals("keep", saved.path("future_option").textValue());
        assertEquals(3, saved.path("panel_messages").size());
        assertEquals("201", saved.path("panel_messages").get(0).path("channel_id").textValue());
        assertEquals("301", saved.path("panel_messages").get(1).path("message_id").textValue());
        assertEquals("300", saved.path("panel_messages").get(2).path("message_id").textValue());
        assertEquals(301, settings.path("panel_message_id").intValue());
        verify(sent, never()).delete();
    }

    @Test
    void removesNewlyPublishedPanelWhenPersistenceFailsAndKeepsPriorData() throws Exception {
        Path file = directory.resolve("ticket_data.json");
        ConfigStore store = new ConfigStore(file);
        ObjectNode settings = SupportType.defaultGuild();
        settings.put("panel_channel", "200");
        store.saveGuild("100", settings);
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message sent = panel("300", PanelService.TITLE);
        MessageCreateAction action = publishAction(channel, sent);
        AuditableRestAction<Void> remove = deletion(sent);
        when(action.complete()).thenAnswer(ignored -> {
            Files.writeString(file, "{broken");
            return sent;
        });

        assertThrows(UncheckedIOException.class, () -> new PanelService(store).publish(guild, settings));

        verify(remove).complete();
        assertEquals("{broken", Files.readString(file));
        assertTrue(settings.path("panel_message_id").isNull());
    }

    @Test
    void exposesRollbackFailureWithoutHidingTheOriginalPersistenceError() {
        ConfigStore store = mock(ConfigStore.class);
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200");
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message sent = panel("300", PanelService.TITLE);
        publishAction(channel, sent);
        RuntimeException original = new UncheckedIOException(new IOException("example write failure"));
        RuntimeException rollback = error(ErrorResponse.MISSING_ACCESS);
        doThrow(original).when(store).saveGuild(eq("100"), any(ObjectNode.class));
        when(deletion(sent).complete()).thenThrow(rollback);

        RuntimeException failure = assertThrows(RuntimeException.class,
                () -> new PanelService(store).publish(guild, settings));

        assertSame(original, failure);
        assertArrayEquals(new Throwable[]{rollback}, failure.getSuppressed());
    }

    @Test
    void deletesRecordedAndLegacyPanelsInAllReferencedChannelsWithoutTouchingUserMessages() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "300");
        settings.putArray("panel_messages").addObject().put("channel_id", "201").put("message_id", "301");
        store.saveGuild("100", settings);
        Guild guild = guild();
        TextChannel first = mock(TextChannel.class), second = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(first);
        when(guild.getTextChannelById("201")).thenReturn(second);
        Message recorded = panel("300", PanelService.TITLE);
        Message moved = panel("301", PanelService.TITLE);
        Message legacy = panel("302", "# Destek Talebi Oluştur #");
        Message userMessage = message(888, PanelService.TITLE, PanelService.panelButtons());
        Message unrelated = message(BOT_ID, "Bilet Bilgisi", PanelService.panelButtons());
        AuditableRestAction<Void> removeRecorded = deletion(recorded), removeMoved = deletion(moved), removeLegacy = deletion(legacy);
        retrieve(first, "300", recorded);
        retrieve(second, "301", moved);
        history(first, userMessage, legacy, unrelated);
        history(second);

        assertEquals(3, new PanelService(store).deleteAll(guild, settings));

        verify(removeRecorded).complete();
        verify(removeMoved).complete();
        verify(removeLegacy).complete();
        verify(userMessage, never()).delete();
        verify(unrelated, never()).delete();
        assertNotNull(store.getGuild("100"), "Panel deletion must not itself erase configuration");
    }

    @Test
    void toleratesAlreadyRemovedMessagesAndDeletedChannels() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "300");
        settings.putArray("panel_messages").addObject().put("channel_id", "201").put("message_id", "301");
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        ErrorResponseException missing = error(ErrorResponse.UNKNOWN_MESSAGE);
        when(retrieve(channel, "300", null).complete()).thenThrow(missing);
        Message disappeared = panel("302", PanelService.TITLE);
        when(deletion(disappeared).complete()).thenThrow(missing);
        history(channel, disappeared);

        assertEquals(0, new PanelService(store).deleteAll(guild, settings));
        verify(guild).getTextChannelById("201");
    }

    @Test
    void stopsWhenRecordedMessageIsNotOurPanel() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "300");
        store.saveGuild("100", settings);
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message userMessage = message(888, PanelService.TITLE, PanelService.panelButtons());
        retrieve(channel, "300", userMessage);

        assertThrows(IllegalArgumentException.class, () -> new PanelService(store).deleteAll(guild, settings));

        verify(userMessage, never()).delete();
        verify(channel, never()).getIterableHistory();
        assertEquals(settings, store.getGuild("100"));
    }

    @Test
    void propagatesPermissionErrorsAndLeavesConfigurationAvailableForRetry() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "300");
        store.saveGuild("100", settings);
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        ErrorResponseException denied = error(ErrorResponse.MISSING_ACCESS);
        when(retrieve(channel, "300", null).complete()).thenThrow(denied);

        assertSame(denied, assertThrows(ErrorResponseException.class,
                () -> new PanelService(store).deleteAll(guild, settings)));
        assertEquals(settings, store.getGuild("100"));
    }

    @Test
    void refusesInvalidIdsAndNonTextChannels() {
        Guild guild = guild();
        Category category = mock(Category.class);
        when(guild.getGuildChannelById("200")).thenReturn(category);
        assertThrows(IllegalArgumentException.class, () -> PanelService.resolveTextChannel(guild, "200"));
        assertThrows(IllegalArgumentException.class, () -> PanelService.id(ConfigStore.MAPPER.getNodeFactory().textNode("oops")));
        assertEquals("123", PanelService.id(ConfigStore.MAPPER.getNodeFactory().numberNode(123)));
        assertNull(PanelService.id(ConfigStore.MAPPER.nullNode()));
    }

    @Test
    void confirmedResetDeletesPanelsBeforeRemovingSettingsAndKeepsTicketMetadata() throws Exception {
        resetScenario(false);
    }

    @Test
    void confirmedResetKeepsSettingsWhenPanelDeletionFails() throws Exception {
        resetScenario(true);
    }

    @SuppressWarnings("unchecked")
    private void resetScenario(boolean failDeletion) throws Exception {
        ConfigStore store = spy(new ConfigStore(directory.resolve("ticket_data.json")));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "300");
        store.saveGuild("100", settings);
        ObjectNode ticket = ConfigStore.MAPPER.createObjectNode().put("opener_id", "500").put("type", "oyun_destek");
        store.saveTicket("100", "400", ticket);
        Guild guild = guild();
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message panel = panel("300", PanelService.TITLE);
        retrieve(channel, "300", panel);
        history(channel);
        AuditableRestAction<Void> delete = deletion(panel);
        ErrorResponseException denied = error(ErrorResponse.MISSING_ACCESS);
        when(delete.complete()).thenAnswer(ignored -> {
            assertNotNull(store.getGuild("100"), "Configuration must still exist while deleting the panel");
            if (failDeletion) throw denied;
            return null;
        });
        User user = mock(User.class);
        Member administrator = mock(Member.class);
        when(user.getId()).thenReturn("500");
        when(administrator.hasPermission(Permission.ADMINISTRATOR)).thenReturn(true);
        SlashCommandInteractionEvent request = mock(SlashCommandInteractionEvent.class);
        when(request.getName()).thenReturn("biletsistemisifirla");
        when(request.getGuild()).thenReturn(guild);
        when(request.getUser()).thenReturn(user);
        when(request.getMember()).thenReturn(administrator);
        ReplyCallbackAction prompt = mock(ReplyCallbackAction.class, RETURNS_SELF);
        when(request.replyEmbeds(any(MessageEmbed.class))).thenReturn(prompt);
        try (TicketBot bot = new TicketBot(store)) {
            bot.onSlashCommandInteraction(request);
            ArgumentCaptor<MessageTopLevelComponent[]> rows = ArgumentCaptor.forClass(MessageTopLevelComponent[].class);
            verify(prompt).setComponents(rows.capture());
            String confirmation = MessageComponentTree.of(rows.getValue()).findAll(Button.class).stream()
                    .map(Button::getCustomId).filter(id -> id != null && id.startsWith("reset:confirm:"))
                    .findFirst().orElseThrow();
            ButtonInteractionEvent event = mock(ButtonInteractionEvent.class);
            when(event.getComponentId()).thenReturn(confirmation);
            when(event.getGuild()).thenReturn(guild);
            when(event.getUser()).thenReturn(user);
            when(event.getMember()).thenReturn(administrator);
            MessageEditCallbackAction acknowledge = mock(MessageEditCallbackAction.class, RETURNS_SELF);
            when(event.deferEdit()).thenReturn(acknowledge);
            InteractionHook hook = mock(InteractionHook.class);
            WebhookMessageEditAction<Message> edit = mock(WebhookMessageEditAction.class, RETURNS_SELF);
            CountDownLatch finished = new CountDownLatch(1);
            AtomicReference<String> response = new AtomicReference<>();
            when(hook.editOriginal(anyString())).thenAnswer(invocation -> {
                response.set(invocation.getArgument(0, String.class));
                finished.countDown();
                return edit;
            });
            doAnswer(invocation -> {
                invocation.getArgument(0, Consumer.class).accept(hook);
                return null;
            }).when(acknowledge).queue(any(Consumer.class), any(Consumer.class));

            bot.onButtonInteraction(event);

            assertTrue(finished.await(3, TimeUnit.SECONDS), "Reset must finish and respond");
            assertEquals(ticket, store.getTicket("100", "400"));
            if (failDeletion) {
                assertEquals(settings, store.getGuild("100"));
                verify(store, never()).removeGuild("100");
                assertTrue(response.get().contains("Ayarlar sıfırlanmadı"));
            } else {
                assertNull(store.getGuild("100"));
                var order = inOrder(delete, store);
                order.verify(delete).complete();
                order.verify(store).removeGuild("100");
                assertTrue(response.get().contains("Silinen panel: **1**"));
            }
        }
    }
}
