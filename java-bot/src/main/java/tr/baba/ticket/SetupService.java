package tr.baba.ticket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.EmbedBuilder;
import net.dv8tion.jda.api.components.actionrow.ActionRow;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.components.label.Label;
import net.dv8tion.jda.api.components.selections.EntitySelectMenu;
import net.dv8tion.jda.api.components.textinput.TextInput;
import net.dv8tion.jda.api.components.textinput.TextInputStyle;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.channel.ChannelType;
import net.dv8tion.jda.api.events.interaction.GenericInteractionCreateEvent;
import net.dv8tion.jda.api.events.interaction.ModalInteractionEvent;
import net.dv8tion.jda.api.events.interaction.command.SlashCommandInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.ButtonInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.EntitySelectInteractionEvent;
import net.dv8tion.jda.api.interactions.callbacks.IReplyCallback;
import net.dv8tion.jda.api.modals.Modal;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.locks.ReentrantLock;

final class SetupService {
    private final ConfigStore store;
    private final PanelService panels;
    private final ExecutorService workers;
    private final Map<String, ReentrantLock> guildLocks;
    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    private static final class Session {
        final String guildId, userId;
        final Instant expires = Instant.now().plusSeconds(900);
        final ObjectNode settings;
        final Map<String, ObjectNode> drafts = new HashMap<>();
        boolean publishing;
        Session(String guildId, String userId, ObjectNode settings) {
            this.guildId = guildId; this.userId = userId; this.settings = settings;
        }
    }

    SetupService(ConfigStore store, PanelService panels, ExecutorService workers, Map<String, ReentrantLock> guildLocks) {
        this.store = store; this.panels = panels; this.workers = workers; this.guildLocks = guildLocks;
    }

    void open(SlashCommandInteractionEvent event) {
        sessions.entrySet().removeIf(entry -> entry.getValue().expires.isBefore(Instant.now()));
        ObjectNode existing = store.getGuild(event.getGuild().getId());
        ObjectNode settings = SupportType.defaultGuild();
        if (existing != null) {
            settings.setAll(existing);
            ObjectNode types = settings.withObject("/support_types");
            for (SupportType type : SupportType.values()) {
                if (!types.path(type.key()).isObject()) types.set(type.key(), SupportType.defaultGuild().path("support_types").path(type.key()));
            }
        }
        String id = UUID.randomUUID().toString();
        sessions.put(id, new Session(event.getGuild().getId(), event.getUser().getId(), settings));
        event.replyEmbeds(new EmbedBuilder().setTitle("⚙️ Bilet Sistemi Kurulumu").setColor(0x5865F2)
                .setDescription("**1.** Panel kanalını seçin.\n**2.** Bilet türlerini düzenleyip kaydedin.\n"
                        + "**3.** Sistemi kurun ve paneli gönderin.\n\nMevcut ayarlar düzenleme için yüklenir.")
                .setFooter("Yalnızca siz kullanabilirsiniz • Bu ekran 15 dakika geçerli").build())
                .setComponents(setupRows(id)).setEphemeral(true).queue();
    }

    private static List<ActionRow> setupRows(String id) {
        List<Button> types = Arrays.stream(SupportType.values())
                .map(type -> Button.primary("setup:type:" + id + ":" + type.key(), type.label())).toList();
        return List.of(ActionRow.of(channelMenu("setup:panel:" + id, ChannelType.TEXT, "Panel kanalını seç")),
                ActionRow.of(types.subList(0,3)), ActionRow.of(types.subList(3,6)),
                ActionRow.of(Button.success("setup:publish:" + id, "Sistemi kur ve paneli gönder")));
    }

    private static EntitySelectMenu channelMenu(String id, ChannelType type, String placeholder) {
        return EntitySelectMenu.create(id, EntitySelectMenu.SelectTarget.CHANNEL)
                .setChannelTypes(type).setPlaceholder(placeholder).build();
    }

    private Session session(GenericInteractionCreateEvent event, String id) {
        Session session = sessions.get(id);
        if (session == null || session.expires.isBefore(Instant.now())) {
            sessions.remove(id);
            ((IReplyCallback) event).reply("⌛ Kurulum ekranının süresi doldu. /biletsisteminikur komutunu tekrar kullanın.").setEphemeral(true).queue();
            return null;
        }
        if (event.getGuild() == null || !session.guildId.equals(event.getGuild().getId())
                || !session.userId.equals(event.getUser().getId()) || event.getMember() == null
                || !event.getMember().hasPermission(net.dv8tion.jda.api.Permission.ADMINISTRATOR)) {
            ((IReplyCallback) event).reply("❌ Bu ekranı yalnızca kurulumu başlatan yönetici kullanabilir.").setEphemeral(true).queue();
            return null;
        }
        return session;
    }

    void onButton(ButtonInteractionEvent event) {
        String[] parts = event.getComponentId().split(":");
        if (parts.length < 3) { event.reply("❌ Geçersiz kurulum düğmesi.").setEphemeral(true).queue(); return; }
        Session session = session(event, parts[2]);
        if (session == null) return;
        synchronized (session) {
            String typeKey = parts.length > 3 ? parts[3] : "";
            SupportType type = SupportType.fromKey(typeKey);
            switch (parts[1]) {
                case "type" -> {
                    if (type == null) throw new IllegalArgumentException("Bilet türü bulunamadı.");
                    ObjectNode draft = session.drafts.computeIfAbsent(type.key(), key -> session.settings.withObject("/support_types").withObject("/" + key).deepCopy());
                    event.replyEmbeds(typeEmbed(event.getGuild(),type,draft)).setComponents(typeRows(parts[2],type))
                            .setEphemeral(true).queue();
                }
                case "roles" -> {
                    if (type == null) throw new IllegalArgumentException("Bilet türü bulunamadı.");
                    event.replyModal(Modal.create("setup:roles:" + parts[2] + ":" + typeKey,"Yetkili Roller")
                            .addComponents(Label.of("Rol ID'leri (virgülle ayırın)",TextInput.create("roles",TextInputStyle.PARAGRAPH)
                                    .setPlaceholder("123456789,987654321 • Boş bırakılırsa roller temizlenir")
                                    .setRequired(false).setMaxLength(2000).build())).build()).queue();
                }
                case "save" -> {
                    if (type == null) throw new IllegalArgumentException("Bilet türü bulunamadı.");
                    ObjectNode draft = session.drafts.get(typeKey);
                    if (draft == null) throw new IllegalArgumentException("Önce bilet türünün ayar ekranını açın.");
                    session.settings.withObject("/support_types").set(typeKey, draft.deepCopy());
                    event.editMessageEmbeds(typeEmbed(event.getGuild(), type,draft)).setComponents(typeRows(parts[2],type))
                            .queue(hook -> hook.sendMessage("✅ " + type.label() + " ayarları kurulum taslağına kaydedildi. Ana ekrandan sistemi kurabilirsiniz.")
                                    .setEphemeral(true).queue());
                }
                case "publish" -> {
                    if (session.publishing) {
                        event.reply("⏳ Kurulum zaten devam ediyor.").setEphemeral(true).queue();
                        return;
                    }
                    session.publishing = true;
                    ObjectNode snapshot = session.settings.deepCopy();
                    // Counters belong to live tickets, not to this fifteen-minute setup draft.
                    snapshot.remove("ticket_counters");
                    snapshot.remove("panel_message_id");
                    event.deferReply(true).queue(hook -> workers.submit(() -> {
                        ReentrantLock lock = guildLocks.computeIfAbsent(session.guildId, ignored -> new ReentrantLock());
                        lock.lock();
                        try {
                            ObjectNode current = store.getGuild(session.guildId);
                            if (current != null) {
                                snapshot.set("panel_messages",current.withArray("panel_messages").deepCopy());
                                if (current.hasNonNull("panel_message_id")) {
                                    snapshot.withArray("panel_messages").addObject()
                                            .put("channel_id",current.path("panel_channel").asText())
                                            .put("message_id",current.path("panel_message_id").asText());
                                }
                            }
                            var panel = panels.publish(event.getGuild(),snapshot);
                            sessions.remove(parts[2]);
                            hook.editOriginal("✅ Sistem kaydedildi ve panel gönderildi: " + panel.getJumpUrl()).queue();
                        } catch (RuntimeException error) {
                            synchronized (session) { session.publishing = false; }
                            TicketBot.report(hook,error);
                        } finally { lock.unlock(); }
                    }),failure -> { synchronized (session) { session.publishing = false; } });
                }
                default -> event.reply("❌ Bilinmeyen kurulum işlemi.").setEphemeral(true).queue();
            }
        }
    }

    void onSelect(EntitySelectInteractionEvent event) {
        String[] parts = event.getComponentId().split(":");
        if (parts.length < 3) { event.reply("❌ Geçersiz seçim.").setEphemeral(true).queue(); return; }
        Session session = session(event,parts[2]);
        if (session == null) return;
        synchronized (session) {
            if (parts[1].equals("panel")) {
                session.settings.put("panel_channel",event.getValues().getFirst().getId());
                event.reply("✅ Panel kanalı seçildi: " + event.getValues().getFirst().getAsMention()).setEphemeral(true).queue();
                return;
            }
            if (parts.length < 4 || SupportType.fromKey(parts[3]) == null) throw new IllegalArgumentException("Bilet türü bulunamadı.");
            ObjectNode draft = session.drafts.get(parts[3]);
            if (draft == null) throw new IllegalArgumentException("Önce bilet ayar ekranını açın.");
            switch (parts[1]) {
                case "open" -> draft.put("open_category",event.getValues().getFirst().getId());
                case "closed" -> draft.put("closed_category",event.getValues().getFirst().getId());
                case "log" -> draft.put("log_channel",event.getValues().getFirst().getId());
                case "selectroles" -> {
                    var roles = draft.putArray("roles");
                    event.getValues().forEach(value -> roles.add(value.getId()));
                }
                default -> throw new IllegalArgumentException("Bilinmeyen ayar.");
            }
            event.editMessageEmbeds(typeEmbed(event.getGuild(),SupportType.fromKey(parts[3]),draft))
                    .setComponents(typeRows(parts[2],SupportType.fromKey(parts[3]))).queue();
        }
    }

    void onModal(ModalInteractionEvent event) {
        String[] parts = event.getModalId().split(":");
        if (parts.length < 4) { event.reply("❌ Geçersiz rol formu.").setEphemeral(true).queue(); return; }
        Session session = session(event,parts[2]);
        if (session == null) return;
        synchronized (session) {
            ObjectNode draft = session.drafts.get(parts[3]);
            if (draft == null) throw new IllegalArgumentException("Önce bilet ayar ekranını açın.");
            Set<String> roles = new LinkedHashSet<>();
            String input = event.getValue("roles").getAsString();
            for (String raw : input.split(",")) {
                String id = raw.strip();
                if (id.isBlank()) continue;
                if (!id.matches("[0-9]{1,20}") || event.getGuild().getRoleById(id) == null) {
                    event.reply("❌ Bir rol ID'si geçersiz veya bu sunucuda bulunamadı. Mevcut roller değişmedi.").setEphemeral(true).queue();
                    return;
                }
                roles.add(id);
            }
            var array = draft.putArray("roles"); roles.forEach(array::add);
            event.reply("✅ Yetkili roller güncellendi. Bilet ayar ekranında Kaydet'e basın.").setEphemeral(true).queue();
        }
    }

    private static List<ActionRow> typeRows(String id,SupportType type) {
        String suffix = id + ":" + type.key();
        return List.of(
                ActionRow.of(channelMenu("setup:open:"+suffix,ChannelType.CATEGORY,"Açık bilet kategorisi")),
                ActionRow.of(channelMenu("setup:closed:"+suffix,ChannelType.CATEGORY,"Kapalı bilet kategorisi")),
                ActionRow.of(channelMenu("setup:log:"+suffix,ChannelType.TEXT,"Log kanalı")),
                ActionRow.of(EntitySelectMenu.create("setup:selectroles:"+suffix,EntitySelectMenu.SelectTarget.ROLE)
                        .setPlaceholder("Yetkili rolleri seçin").setMinValues(0).setMaxValues(25).build()),
                ActionRow.of(Button.secondary("setup:roles:"+suffix,"Rol ID Gir"),Button.success("setup:save:"+suffix,"Kaydet")));
    }

    private static net.dv8tion.jda.api.entities.MessageEmbed typeEmbed(Guild guild,SupportType type,ObjectNode settings) {
        return new EmbedBuilder().setTitle("⚙️ " + type.label()).setColor(0x5865F2)
                .setDescription("Aşağıdan kategorileri, log kanalını ve yetkili rolleri seçin.")
                .addField("Açık kategori",displayChannel(guild,settings.get("open_category")),true)
                .addField("Kapalı kategori",displayChannel(guild,settings.get("closed_category")),true)
                .addField("Log kanalı",displayChannel(guild,settings.get("log_channel")),true)
                .addField("Yetkili roller",displayRoles(guild,settings.path("roles")),false)
                .setFooter("Seçimlerden sonra Kaydet'e basın; ardından ana ekrandan sistemi kurun.").build();
    }

    static String displayChannel(Guild guild,JsonNode value) {
        try {
            String id = PanelService.id(value);
            var channel = id == null ? null : guild.getGuildChannelById(id);
            return channel == null ? "⚠️ Seçilmemiş veya silinmiş" : channel.getAsMention();
        } catch (IllegalArgumentException error) { return "⚠️ Geçersiz kanal"; }
    }

    static String displayRoles(Guild guild,JsonNode values) {
        List<String> roles = new ArrayList<>(); int missing = 0;
        for (JsonNode value : values) {
            try {
                String id = PanelService.id(value);
                var role = id == null ? null : guild.getRoleById(id);
                if (role == null) missing++; else roles.add(role.getAsMention());
            } catch (IllegalArgumentException error) { missing++; }
        }
        String text = roles.isEmpty() ? "⚠️ Yetkili rolü seçilmemiş" : String.join(", ",roles);
        if (missing > 0) text += " • ⚠️ " + missing + " geçersiz/silinmiş rol";
        return text.length() > 800 ? text.substring(0,790)+"…" : text;
    }
}
