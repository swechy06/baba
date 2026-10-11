package tr.baba.ticket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.EmbedBuilder;
import net.dv8tion.jda.api.components.actionrow.ActionRow;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.Message;
import net.dv8tion.jda.api.entities.MessageEmbed;
import net.dv8tion.jda.api.entities.channel.concrete.TextChannel;
import net.dv8tion.jda.api.exceptions.ErrorResponseException;
import net.dv8tion.jda.api.requests.ErrorResponse;

import java.util.*;
import java.util.stream.Collectors;

/** REST operations run on worker threads, after acknowledging the interaction. */
final class PanelService {
    static final String TITLE = "🎫 Destek Merkezi";
    private final ConfigStore store;

    PanelService(ConfigStore store) { this.store = store; }

    static MessageEmbed panelEmbed() {
        return new EmbedBuilder().setTitle(TITLE).setColor(0x5865F2)
                .setDescription("Yardım almak istediğiniz konuyu seçin ve açılan forma talebinizi yazın.\n"
                        + "Biletiniz yalnızca siz ve ilgili yetkililer tarafından görülebilir.")
                .addField("Gamepass", "Robux ve satın alım desteği", true)
                .addField("Oyun", "Oyun içi sorun ve şikâyetler", true)
                .addField("Rütbe", "Rütbe sorunları ve sorular", true)
                .addField("Reklam", "İzinsiz reklam şikâyetleri", true)
                .addField("Transfer", "Transfer başvuruları", true)
                .addField("Moderatör", "Discord desteği", true)
                .setFooter("Destek Merkezi • Bir konu için tek bilet açmanız yeterli")
                .build();
    }

    static List<ActionRow> panelButtons() {
        List<Button> buttons = Arrays.stream(SupportType.values())
                .map(type -> Button.secondary("ticket_open:" + type.key(), type.panelLabel()))
                .toList();
        return List.of(ActionRow.of(buttons.subList(0, 3)), ActionRow.of(buttons.subList(3, 6)));
    }

    static boolean isPanel(Message message, long botId) {
        if (message.getAuthor().getIdLong() != botId) return false;
        boolean title = message.getEmbeds().stream().map(MessageEmbed::getTitle)
                .anyMatch(t -> TITLE.equals(t) || "# Destek Talebi Oluştur #".equals(t));
        Set<String> labels = message.getComponentTree().findAll(Button.class).stream()
                .map(Button::getLabel).collect(Collectors.toSet());
        Set<String> expected = Arrays.stream(SupportType.values()).map(SupportType::panelLabel)
                .collect(Collectors.toSet());
        return title && labels.containsAll(expected);
    }

    Message publish(Guild guild, ObjectNode settings) {
        String channelId = id(settings.get("panel_channel"));
        if (channelId == null) throw new IllegalArgumentException("Önce bir panel kanalı seçin.");
        TextChannel channel = resolveTextChannel(guild, channelId);
        if (channel == null) throw new IllegalArgumentException("Panel kanalı silinmiş; yeni bir kanal seçin.");
        Message message = channel.sendMessageEmbeds(panelEmbed()).setComponents(panelButtons()).complete();
        ObjectNode updated = settings.deepCopy();
        ArrayNode references = updated.withArray("panel_messages");
        // Keep the prior Python panel and panels in earlier selected channels tracked.
        addReference(references, id(settings.get("panel_channel")), id(settings.get("panel_message_id")));
        addReference(references, channelId, message.getId());
        updated.put("panel_message_id", message.getId());
        try {
            store.saveGuild(guild.getId(), updated);
        } catch (RuntimeException failure) {
            try { message.delete().complete(); } catch (RuntimeException rollback) { failure.addSuppressed(rollback); }
            throw failure;
        }
        return message;
    }

    private static void addReference(ArrayNode references, String channel, String message) {
        if (channel == null || message == null) return;
        for (JsonNode ref : references) {
            if (channel.equals(id(ref.get("channel_id"))) && message.equals(id(ref.get("message_id")))) return;
        }
        references.addObject().put("channel_id", channel).put("message_id", message);
    }

    int deleteAll(Guild guild, ObjectNode settings) {
        Map<String, Set<String>> channels = new LinkedHashMap<>();
        String original = id(settings.get("panel_channel"));
        if (original != null) {
            Set<String> messages = channels.computeIfAbsent(original, ignored -> new LinkedHashSet<>());
            String message = id(settings.get("panel_message_id"));
            if (message != null) messages.add(message);
        }
        for (JsonNode ref : settings.path("panel_messages")) {
            String channel = id(ref.get("channel_id"));
            String message = id(ref.get("message_id"));
            if (channel != null && message != null) channels.computeIfAbsent(channel, ignored -> new LinkedHashSet<>()).add(message);
        }
        long botId = guild.getJDA().getSelfUser().getIdLong();
        int count = 0;
        for (var entry : channels.entrySet()) {
            TextChannel channel = resolveTextChannel(guild, entry.getKey());
            if (channel == null) continue;
            for (String messageId : entry.getValue()) {
                try {
                    Message message = channel.retrieveMessageById(messageId).complete();
                    if (!isPanel(message, botId)) throw new IllegalArgumentException("Kayıtlı mesaj bilet paneli değil; silme işlemi durduruldu.");
                    if (deleteIfPresent(message)) count++;
                } catch (ErrorResponseException error) {
                    if (error.getErrorResponse() != ErrorResponse.UNKNOWN_MESSAGE) throw error;
                }
            }
            // Legacy Python releases did not save panel IDs; inspect only bot-owned panels.
            for (Message message : channel.getIterableHistory()) {
                if (isPanel(message, botId) && deleteIfPresent(message)) count++;
            }
        }
        return count;
    }

    private static boolean deleteIfPresent(Message message) {
        try { message.delete().complete(); return true; }
        catch (ErrorResponseException error) {
            if (error.getErrorResponse() == ErrorResponse.UNKNOWN_MESSAGE) return false;
            throw error;
        }
    }

    static TextChannel resolveTextChannel(Guild guild, String channelId) {
        if (channelId == null) return null;
        TextChannel cached = guild.getTextChannelById(channelId);
        if (cached != null) return cached;
        if (guild.getGuildChannelById(channelId) != null) throw new IllegalArgumentException("Seçilen kanal bir metin kanalı değil.");
        // JDA keeps all guild channels cached after GuildReady. A missing channel is deleted.
        return null;
    }

    static String id(JsonNode value) {
        if (value == null || value.isNull() || value.asText().isBlank()) return null;
        String id = value.asText();
        if (!id.matches("[0-9]{1,20}")) throw new IllegalArgumentException("Kayıt dosyasında geçersiz Discord kimliği var.");
        return id;
    }
}
