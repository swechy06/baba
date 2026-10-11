package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import net.dv8tion.jda.api.Permission;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.Member;
import net.dv8tion.jda.api.entities.Message;
import net.dv8tion.jda.api.entities.PermissionOverride;
import net.dv8tion.jda.api.entities.Role;
import net.dv8tion.jda.api.entities.SelfMember;
import net.dv8tion.jda.api.entities.User;
import net.dv8tion.jda.api.entities.channel.concrete.Category;
import net.dv8tion.jda.api.entities.channel.concrete.TextChannel;
import net.dv8tion.jda.api.entities.channel.unions.MessageChannelUnion;
import net.dv8tion.jda.api.events.interaction.ModalInteractionEvent;
import net.dv8tion.jda.api.interactions.InteractionHook;
import net.dv8tion.jda.api.interactions.modals.ModalMapping;
import net.dv8tion.jda.api.managers.channel.concrete.TextChannelManager;
import net.dv8tion.jda.api.requests.restaction.MessageCreateAction;
import net.dv8tion.jda.api.requests.restaction.ChannelAction;
import net.dv8tion.jda.api.requests.restaction.CacheRestAction;
import net.dv8tion.jda.api.requests.restaction.MessageEditAction;
import net.dv8tion.jda.api.requests.restaction.WebhookMessageEditAction;
import net.dv8tion.jda.api.requests.restaction.interactions.ReplyCallbackAction;
import net.dv8tion.jda.api.requests.restaction.pagination.MessagePaginationAction;
import net.dv8tion.jda.api.utils.FileUpload;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Collection;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class TicketServiceTest {
    private final ConfigStore store = mock(ConfigStore.class);
    private final Guild guild = mock(Guild.class);
    private final TextChannel channel = mock(TextChannel.class);
    private final SelfMember bot = mock(SelfMember.class);
    private final Member staff = mock(Member.class);
    private final Role publicRole = mock(Role.class);
    private final Role supportRole = mock(Role.class);
    private TicketService service;
    private ObjectNode configuration;

    @BeforeEach void prepare() {
        service = new TicketService(store);
        configuration = ConfigStore.MAPPER.createObjectNode();
        configuration.putObject("support_types").putObject("oyun_destek").putArray("roles").add("222222222222222222");
        when(guild.getId()).thenReturn("111111111111111111");
        when(guild.getSelfMember()).thenReturn(bot);
        when(guild.getPublicRole()).thenReturn(publicRole);
        when(guild.getRoleById("222222222222222222")).thenReturn(supportRole);
        when(bot.getId()).thenReturn("333333333333333333");
        when(channel.getId()).thenReturn("444444444444444444");
        when(channel.getGuild()).thenReturn(guild);
        when(channel.getTopic()).thenReturn("oyun_destek|Oyun açılmıyor");
        when(channel.getAsMention()).thenReturn("<#444444444444444444>");
        when(publicRole.getId()).thenReturn("111111111111111111");
        when(supportRole.getId()).thenReturn("222222222222222222");
        when(staff.getRoles()).thenReturn(List.of(supportRole));
        when(staff.getId()).thenReturn("555555555555555555");
        when(staff.getAsMention()).thenReturn("<@555555555555555555>");
        when(store.getGuild(guild.getId())).thenAnswer(ignored -> configuration.deepCopy());
    }

    @AfterEach void stop() { service.close(); }

    @Test void staffPermissionUsesCurrentConfigurationAndRejectsUnrelatedChannels() {
        assertTrue(service.hasStaffPermission(staff, channel));
        ((ArrayNode) configuration.path("support_types").path("oyun_destek").path("roles")).removeAll();
        assertFalse(service.hasStaffPermission(staff, channel));
        when(channel.getTopic()).thenReturn("normal chat topic");
        assertFalse(service.hasStaffPermission(staff, channel));
        verifyNoInteractions(bot);
    }

    @Test void configurationResetImmediatelyRevokesOldStaffAccess() {
        assertTrue(service.hasStaffPermission(staff, channel));
        when(store.getGuild(guild.getId())).thenReturn(null);
        assertFalse(service.hasStaffPermission(staff, channel));
    }

    @Test void archiveRemovesMemberAccessButKeepsExplicitBotAndStaffReadAccess() {
        TextChannelManager manager = mock(TextChannelManager.class, RETURNS_SELF);
        Category archive = mock(Category.class);
        PermissionOverride botOverride = mock(PermissionOverride.class);
        PermissionOverride openerOverride = mock(PermissionOverride.class);
        when(botOverride.getId()).thenReturn("333333333333333333");
        when(openerOverride.getId()).thenReturn("666666666666666666");
        when(openerOverride.getIdLong()).thenReturn(666666666666666666L);
        when(channel.getPermissionOverrides()).thenReturn(List.of(botOverride, openerOverride));
        when(channel.getManager()).thenReturn(manager);
        service.archiveChannel(channel, archive, configuration.path("support_types").path("oyun_destek"));
        verify(manager).setParent(archive);
        verify(manager).removePermissionOverride(666666666666666666L);
        verify(manager, never()).removePermissionOverride(333333333333333333L);
        verify(manager).putPermissionOverride(eq(bot), longThat(bits -> (bits & Permission.VIEW_CHANNEL.getRawValue()) != 0
                && (bits & Permission.MESSAGE_SEND.getRawValue()) != 0), eq(0L));
        verify(manager).putPermissionOverride(supportRole,
                Permission.getRaw(Permission.VIEW_CHANNEL, Permission.MESSAGE_HISTORY), Permission.MESSAGE_SEND.getRawValue());
        verify(manager).putPermissionOverride(publicRole, 0L, Permission.VIEW_CHANNEL.getRawValue());
        verify(manager).complete();
    }

    @Test void configuredMissingLogPreventsCountdownOrArchive() {
        configuration.path("support_types").path("oyun_destek").withObject("").put("log_channel", "777777777777777777");
        when(bot.hasPermission(eq(channel), any(Permission[].class))).thenReturn(true);
        Exception failure = assertThrows(Exception.class,
                () -> service.closeTicket(channel, staff, existingTicket(), mock(InteractionHook.class)));
        assertTrue(failure.getMessage().contains("Log kanalı bulunamadı"));
        verify(channel, never()).sendMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class));
        verify(channel, never()).getManager();
        verify(store, never()).saveTicket(anyString(), anyString(), any());
    }

    @Test void transcriptFailureLeavesChannelPermissionsAndTicketStatusUntouched() {
        TextChannel logChannel = mock(TextChannel.class);
        when(logChannel.getId()).thenReturn("777777777777777777");
        when(logChannel.getGuild()).thenReturn(guild);
        when(guild.getTextChannelById("777777777777777777")).thenReturn(logChannel);
        configuration.path("support_types").path("oyun_destek").withObject("").put("log_channel", logChannel.getId());
        when(bot.hasPermission(any(TextChannel.class), any(Permission[].class))).thenReturn(true);
        Message countdown = mock(Message.class);
        MessageCreateAction create = mock(MessageCreateAction.class, RETURNS_SELF);
        MessageEditAction edit = mock(MessageEditAction.class, RETURNS_SELF);
        when(channel.sendMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class))).thenReturn(create);
        when(create.complete()).thenReturn(countdown);
        when(countdown.editMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class))).thenReturn(edit);
        when(channel.getIterableHistory()).thenThrow(new IllegalStateException("History fetch failed"));
        ObjectNode ticket = existingTicket();
        Exception failure = assertThrows(Exception.class,
                () -> service.closeTicket(channel, staff, ticket, mock(InteractionHook.class)));
        assertTrue(failure.getMessage().contains("Transcript"));
        assertEquals("review", ticket.path("status").asText());
        verify(channel, never()).getManager();
        verify(store, never()).saveTicket(anyString(), anyString(), any());
    }

    @Test void failedArchiveInvalidatesSuccessfulLogSoRetryCapturesNewConversation() {
        TextChannel logChannel = mock(TextChannel.class);
        when(logChannel.getId()).thenReturn("777777777777777777");
        when(logChannel.getGuild()).thenReturn(guild);
        when(guild.getTextChannelById("777777777777777777")).thenReturn(logChannel);
        ((ObjectNode) configuration.path("support_types").path("oyun_destek")).put("log_channel", "777777777777777777");
        when(bot.hasPermission(any(TextChannel.class), any(Permission[].class))).thenReturn(true);
        Message countdown = mock(Message.class);
        MessageCreateAction create = mock(MessageCreateAction.class, RETURNS_SELF);
        MessageEditAction edit = mock(MessageEditAction.class, RETURNS_SELF);
        when(channel.sendMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class))).thenReturn(create);
        when(create.complete()).thenReturn(countdown);
        when(countdown.editMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class))).thenReturn(edit);
        when(channel.getManager()).thenThrow(new IllegalStateException("Permission edit failed"));
        ObjectNode ticket = existingTicket().put("log_completed", true).put("log_message_id", "999999999999999999");
        Exception failure = assertThrows(Exception.class,
                () -> service.closeTicket(channel, staff, ticket, mock(InteractionHook.class)));
        assertTrue(failure.getMessage().contains("güncel transcript"));
        assertFalse(ticket.path("log_completed").asBoolean());
        assertEquals("review", ticket.path("status").asText());
        verify(store).saveTicket(guild.getId(), channel.getId(), ticket);
        verify(channel, never()).getIterableHistory();
    }

    @Test void uploadedTranscriptContainsAllHistoryInChronologicalOrderAndAttachmentLinks() throws Exception {
        TextChannel logChannel = mock(TextChannel.class);
        MessagePaginationAction history = mock(MessagePaginationAction.class);
        Message early = mock(Message.class);
        Message late = mock(Message.class);
        User author = mock(User.class);
        Message.Attachment attachment = mock(Message.Attachment.class);
        MessageCreateAction upload = mock(MessageCreateAction.class, RETURNS_SELF);
        Message uploadedMessage = mock(Message.class);
        AtomicReference<String> uploaded = new AtomicReference<>();
        when(channel.getName()).thenReturn("oyun-destek-0001");
        when(channel.getIterableHistory()).thenReturn(history);
        when(guild.getMaxFileSize()).thenReturn(10L * 1024 * 1024);
        when(author.getName()).thenReturn("Üye");
        when(author.getId()).thenReturn("666666666666666666");
        when(early.getAuthor()).thenReturn(author);
        when(late.getAuthor()).thenReturn(author);
        when(early.getIdLong()).thenReturn(100L);
        when(late.getIdLong()).thenReturn(200L);
        when(early.getTimeCreated()).thenReturn(OffsetDateTime.parse("2026-10-11T07:00:00Z"));
        when(late.getTimeCreated()).thenReturn(OffsetDateTime.parse("2026-10-11T07:01:00Z"));
        when(early.getContentRaw()).thenReturn("İLK MESAJ");
        when(late.getContentRaw()).thenReturn("SON MESAJ");
        when(attachment.getUrl()).thenReturn("https://cdn.discordapp.com/ticket.png");
        when(late.getAttachments()).thenReturn(List.of(attachment));
        doAnswer(invocation -> {
            Consumer<Message> visitor = invocation.getArgument(0);
            visitor.accept(late);
            visitor.accept(early);
            return null;
        }).when(history).forEach(any());
        when(logChannel.sendMessageEmbeds(any(net.dv8tion.jda.api.entities.MessageEmbed.class))).thenReturn(upload);
        when(upload.addFiles(anyCollection())).thenAnswer(invocation -> {
            Collection<FileUpload> files = invocation.getArgument(0);
            StringBuilder text = new StringBuilder();
            for (FileUpload file : files) text.append(new String(file.getData().readAllBytes(), StandardCharsets.UTF_8));
            uploaded.set(text.toString());
            return upload;
        });
        when(upload.complete()).thenReturn(uploadedMessage);
        when(uploadedMessage.getId()).thenReturn("999999999999999999");
        ObjectNode ticket = existingTicket().put("closed_at", "2026-10-11T07:02:00Z");
        service.sendLog(channel, logChannel, staff, ticket);
        assertNotNull(uploaded.get());
        assertTrue(uploaded.get().indexOf("İLK MESAJ") < uploaded.get().indexOf("SON MESAJ"));
        assertTrue(uploaded.get().contains("[11.10.2026 10:00]"));
        assertTrue(uploaded.get().contains("https://cdn.discordapp.com/ticket.png"));
        assertTrue(ticket.path("log_completed").asBoolean());
        assertEquals("999999999999999999", ticket.path("log_message_id").asText());
    }

    @Test void forgedMemberModalCannotAddAnyoneInAnotherChannel() throws Exception {
        ModalInteractionEvent event = mock(ModalInteractionEvent.class);
        MessageChannelUnion clickedChannel = mock(MessageChannelUnion.class);
        ReplyCallbackAction reply = mock(ReplyCallbackAction.class);
        InteractionHook hook = mock(InteractionHook.class);
        @SuppressWarnings("unchecked") WebhookMessageEditAction<Message> edit = mock(WebhookMessageEditAction.class, RETURNS_SELF);
        AtomicReference<String> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        when(event.getModalId()).thenReturn("ticket_member:444444444444444444");
        when(event.getGuild()).thenReturn(guild);
        when(event.getChannel()).thenReturn(clickedChannel);
        when(clickedChannel.getId()).thenReturn("888888888888888888");
        when(event.deferReply(true)).thenReturn(reply);
        doAnswer(invocation -> {
            Consumer<InteractionHook> callback = invocation.getArgument(0);
            callback.accept(hook);
            return null;
        }).when(reply).queue(any());
        when(hook.editOriginal(anyString())).thenAnswer(invocation -> {
            result.set(invocation.getArgument(0));
            done.countDown();
            return edit;
        });
        service.onModal(event);
        assertTrue(done.await(5, TimeUnit.SECONDS));
        assertTrue(result.get().contains("bu bilete ait değil"));
        verify(guild, never()).retrieveMemberById(anyString());
        verify(channel, never()).upsertPermissionOverride(any());
    }

    @Test void createsPrivateTicketFromReasonModalAndPersistsStableControlMetadata() throws Exception {
        String openerId = "666666666666666666";
        long openerSnowflake = 666666666666666666L;
        String categoryId = "888888888888888888";
        ((ObjectNode) configuration.path("support_types").path("oyun_destek")).put("open_category", categoryId);
        Category openCategory = mock(Category.class);
        User user = mock(User.class);
        Member opener = mock(Member.class);
        ModalInteractionEvent event = mock(ModalInteractionEvent.class);
        ModalMapping mapping = mock(ModalMapping.class);
        ReplyCallbackAction reply = mock(ReplyCallbackAction.class);
        InteractionHook hook = mock(InteractionHook.class);
        @SuppressWarnings("unchecked") CacheRestAction<Member> fetchMember = mock(CacheRestAction.class);
        @SuppressWarnings("unchecked") ChannelAction<TextChannel> createChannel = mock(ChannelAction.class, RETURNS_SELF);
        @SuppressWarnings("unchecked") WebhookMessageEditAction<Message> editReply = mock(WebhookMessageEditAction.class, RETURNS_SELF);
        MessageCreateAction sendPanel = mock(MessageCreateAction.class, RETURNS_SELF);
        Message panel = mock(Message.class);
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        AtomicReference<ObjectNode> persisted = new AtomicReference<>();
        when(event.getModalId()).thenReturn("ticket_reason:oyun_destek");
        when(event.getGuild()).thenReturn(guild);
        when(event.getUser()).thenReturn(user);
        when(user.getId()).thenReturn(openerId);
        when(event.getValue("reason")).thenReturn(mapping);
        when(mapping.getAsString()).thenReturn("Oyun | hatası");
        when(event.deferReply(true)).thenReturn(reply);
        doAnswer(invocation -> {
            Consumer<InteractionHook> callback = invocation.getArgument(0);
            callback.accept(hook);
            return null;
        }).when(reply).queue(any());
        when(hook.editOriginal(anyString())).thenAnswer(invocation -> {
            result.set(invocation.getArgument(0));
            done.countDown();
            return editReply;
        });
        when(guild.getCategoryById(categoryId)).thenReturn(openCategory);
        when(bot.hasPermission(eq(openCategory), any(Permission[].class))).thenReturn(true);
        when(guild.retrieveMemberById(openerId)).thenReturn(fetchMember);
        when(fetchMember.complete()).thenReturn(opener);
        when(opener.getIdLong()).thenReturn(openerSnowflake);
        when(bot.getIdLong()).thenReturn(333333333333333333L);
        when(publicRole.getIdLong()).thenReturn(111111111111111111L);
        when(supportRole.getIdLong()).thenReturn(222222222222222222L);
        when(supportRole.getAsMention()).thenReturn("<@&222222222222222222>");
        when(store.nextTicketNumber(guild.getId(), "oyun_destek")).thenReturn(42);
        when(guild.createTextChannel("oyun-destek-0042", openCategory)).thenReturn(createChannel);
        when(createChannel.complete()).thenReturn(channel);
        when(channel.sendMessage("<@&222222222222222222>")).thenReturn(sendPanel);
        when(sendPanel.complete()).thenReturn(panel);
        when(panel.getId()).thenReturn("777777777777777777");
        doAnswer(invocation -> {
            persisted.set(((ObjectNode) invocation.getArgument(2)).deepCopy());
            return null;
        }).when(store).saveTicket(eq("111111111111111111"), eq("444444444444444444"), any(ObjectNode.class));
        service.onModal(event);
        assertTrue(done.await(5, TimeUnit.SECONDS));
        assertTrue(result.get().contains("Biletiniz hazır"));
        assertTrue(result.get().contains(channel.getAsMention()));
        verify(createChannel).setTopic("oyun_destek|Oyun | hatası");
        verify(createChannel).addRolePermissionOverride(111111111111111111L, 0L, Permission.VIEW_CHANNEL.getRawValue());
        verify(createChannel).addMemberPermissionOverride(eq(openerSnowflake),
                longThat(bits -> (bits & Permission.VIEW_CHANNEL.getRawValue()) != 0
                        && (bits & Permission.MESSAGE_SEND.getRawValue()) != 0), eq(0L));
        verify(createChannel).addMemberPermissionOverride(eq(333333333333333333L),
                longThat(bits -> (bits & Permission.MANAGE_PERMISSIONS.getRawValue()) != 0), eq(0L));
        verify(createChannel).addRolePermissionOverride(eq(222222222222222222L),
                longThat(bits -> (bits & Permission.VIEW_CHANNEL.getRawValue()) != 0), eq(0L));
        verify(sendPanel).setComponents(anyCollection());
        assertEquals("oyun_destek", persisted.get().path("type").asText());
        assertEquals("Oyun | hatası", persisted.get().path("reason").asText());
        assertEquals(42, persisted.get().path("number").asInt());
        assertEquals(openerId, persisted.get().path("opener_id").asText());
        assertEquals("777777777777777777", persisted.get().path("panel_message_id").asText());
        assertEquals("review", persisted.get().path("status").asText());
    }

    private ObjectNode existingTicket() {
        ObjectNode ticket = ConfigStore.MAPPER.createObjectNode();
        return ticket.put("type", "oyun_destek").put("reason", "Oyun açılmıyor").put("number", 1)
                .put("status", "review").put("opener_id", "666666666666666666").put("created_at", "2026-10-11T07:00:00Z");
    }
}
