package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.Permission;
import net.dv8tion.jda.api.components.MessageTopLevelComponent;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.components.tree.MessageComponentTree;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.Member;
import net.dv8tion.jda.api.entities.Message;
import net.dv8tion.jda.api.entities.MessageEmbed;
import net.dv8tion.jda.api.entities.Role;
import net.dv8tion.jda.api.entities.User;
import net.dv8tion.jda.api.entities.channel.concrete.TextChannel;
import net.dv8tion.jda.api.events.interaction.command.SlashCommandInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.ButtonInteractionEvent;
import net.dv8tion.jda.api.interactions.InteractionHook;
import net.dv8tion.jda.api.requests.restaction.MessageCreateAction;
import net.dv8tion.jda.api.requests.restaction.WebhookMessageEditAction;
import net.dv8tion.jda.api.requests.restaction.interactions.ReplyCallbackAction;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;

import java.nio.file.Path;
import java.util.Collection;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class SetupServiceTest {
    @TempDir
    Path directory;

    private String openSetup(SetupService service, Guild guild, User user) {
        SlashCommandInteractionEvent event = mock(SlashCommandInteractionEvent.class);
        ReplyCallbackAction reply = mock(ReplyCallbackAction.class, RETURNS_SELF);
        when(event.getGuild()).thenReturn(guild);
        when(event.getUser()).thenReturn(user);
        when(event.replyEmbeds(any(MessageEmbed.class))).thenReturn(reply);
        service.open(event);
        @SuppressWarnings("unchecked")
        ArgumentCaptor<Collection<? extends MessageTopLevelComponent>> components = ArgumentCaptor.forClass(Collection.class);
        verify(reply).setComponents(components.capture());
        return MessageComponentTree.of(components.getValue()).findAll(Button.class).stream()
                .map(Button::getCustomId).filter(id -> id != null && id.startsWith("setup:publish:"))
                .findFirst().orElseThrow();
    }

    @Test
    @SuppressWarnings("unchecked")
    void publishingAnOldWizardCannotRollBackTicketCountersAndKeepsEarlierPanelReferences() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        ObjectNode settings = SupportType.defaultGuild().put("panel_channel", "200").put("panel_message_id", "301");
        settings.putArray("panel_messages").addObject().put("channel_id", "201").put("message_id", "302");
        store.saveGuild("100", settings);
        assertEquals(1, store.nextTicketNumber("100", "gamepass_destek"));
        Guild guild = mock(Guild.class);
        User owner = mock(User.class);
        Member member = mock(Member.class);
        when(guild.getId()).thenReturn("100");
        when(owner.getId()).thenReturn("500");
        when(member.hasPermission(Permission.ADMINISTRATOR)).thenReturn(true);
        ExecutorService workers = mock(ExecutorService.class);
        when(workers.submit(any(Runnable.class))).thenAnswer(invocation -> {
            invocation.getArgument(0, Runnable.class).run();
            return CompletableFuture.completedFuture(null);
        });
        Map<String, ReentrantLock> locks = new ConcurrentHashMap<>();
        SetupService service = new SetupService(store, new PanelService(store), workers, locks);
        String publishId = openSetup(service, guild, owner);

        // A ticket opens while the user is still editing the setup dialog.
        assertEquals(2, store.nextTicketNumber("100", "gamepass_destek"));
        TextChannel channel = mock(TextChannel.class);
        when(guild.getTextChannelById("200")).thenReturn(channel);
        Message panel = mock(Message.class);
        when(panel.getId()).thenReturn("300");
        when(panel.getJumpUrl()).thenReturn("https://discord.com/channels/100/200/300");
        MessageCreateAction create = mock(MessageCreateAction.class, RETURNS_SELF);
        when(channel.sendMessageEmbeds(any(MessageEmbed.class))).thenReturn(create);
        when(create.complete()).thenReturn(panel);
        ButtonInteractionEvent event = mock(ButtonInteractionEvent.class);
        when(event.getComponentId()).thenReturn(publishId);
        when(event.getGuild()).thenReturn(guild);
        when(event.getUser()).thenReturn(owner);
        when(event.getMember()).thenReturn(member);
        ReplyCallbackAction acknowledgment = mock(ReplyCallbackAction.class, RETURNS_SELF);
        when(event.deferReply(true)).thenReturn(acknowledgment);
        InteractionHook hook = mock(InteractionHook.class);
        WebhookMessageEditAction<Message> edit = mock(WebhookMessageEditAction.class, RETURNS_SELF);
        when(hook.editOriginal(anyString())).thenReturn(edit);
        doAnswer(invocation -> {
            invocation.getArgument(0, Consumer.class).accept(hook);
            return null;
        }).when(acknowledgment).queue(any(Consumer.class), any(Consumer.class));

        service.onButton(event);

        ObjectNode saved = store.getGuild("100");
        assertEquals("300", saved.path("panel_message_id").textValue());
        assertEquals(2, saved.path("ticket_counters").path("gamepass_destek").intValue());
        assertEquals(3, store.nextTicketNumber("100", "gamepass_destek"));
        assertTrue(saved.path("panel_messages").toString().contains("302"));
        assertTrue(saved.path("panel_messages").toString().contains("301"));
        verify(hook).editOriginal(contains("Sistem kaydedildi"));
    }

    @Test
    void rejectsAnotherAdministratorUsingTheSetupOwnersButtons() {
        ConfigStore store = new ConfigStore(directory.resolve("ticket_data.json"));
        Guild guild = mock(Guild.class);
        User owner = mock(User.class), stranger = mock(User.class);
        Member member = mock(Member.class);
        when(guild.getId()).thenReturn("100");
        when(owner.getId()).thenReturn("500");
        when(stranger.getId()).thenReturn("501");
        when(member.hasPermission(Permission.ADMINISTRATOR)).thenReturn(true);
        ExecutorService workers = mock(ExecutorService.class);
        SetupService service = new SetupService(store, new PanelService(store), workers, new ConcurrentHashMap<>());
        String publishId = openSetup(service, guild, owner);
        ButtonInteractionEvent event = mock(ButtonInteractionEvent.class);
        when(event.getComponentId()).thenReturn(publishId);
        when(event.getGuild()).thenReturn(guild);
        when(event.getUser()).thenReturn(stranger);
        when(event.getMember()).thenReturn(member);
        ReplyCallbackAction rejection = mock(ReplyCallbackAction.class, RETURNS_SELF);
        when(event.reply(anyString())).thenReturn(rejection);

        service.onButton(event);

        verify(event).reply(contains("yalnızca kurulumu başlatan yönetici"));
        verify(event, never()).deferReply(anyBoolean());
        verifyNoInteractions(workers);
        assertNull(store.getGuild("100"));
    }

    @Test
    void displaysReadableChannelAndRoleWarningsForLegacyMixedIds() {
        Guild guild = mock(Guild.class);
        TextChannel channel = mock(TextChannel.class);
        Role role = mock(Role.class);
        when(guild.getGuildChannelById("200")).thenReturn(channel);
        when(channel.getAsMention()).thenReturn("<#200>");
        when(guild.getRoleById("500")).thenReturn(role);
        when(role.getAsMention()).thenReturn("<@&500>");

        assertEquals("<#200>", SetupService.displayChannel(guild, ConfigStore.MAPPER.getNodeFactory().numberNode(200)));
        assertTrue(SetupService.displayChannel(guild, ConfigStore.MAPPER.nullNode()).contains("Seçilmemiş"));
        assertTrue(SetupService.displayChannel(guild, ConfigStore.MAPPER.getNodeFactory().textNode("bad")).contains("Geçersiz"));
        var roles = ConfigStore.MAPPER.createArrayNode().add(500).add("501").add("bad");
        String display = SetupService.displayRoles(guild, roles);
        assertTrue(display.contains("<@&500>"));
        assertTrue(display.contains("2 geçersiz/silinmiş rol"));
        assertTrue(SetupService.displayRoles(guild, ConfigStore.MAPPER.createArrayNode()).contains("rolü seçilmemiş"));
    }
}
