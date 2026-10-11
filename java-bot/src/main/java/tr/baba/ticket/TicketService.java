package tr.baba.ticket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.EmbedBuilder;
import net.dv8tion.jda.api.Permission;
import net.dv8tion.jda.api.components.actionrow.ActionRow;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.components.label.Label;
import net.dv8tion.jda.api.components.textinput.TextInput;
import net.dv8tion.jda.api.components.textinput.TextInputStyle;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.entities.Member;
import net.dv8tion.jda.api.entities.Message;
import net.dv8tion.jda.api.entities.MessageEmbed;
import net.dv8tion.jda.api.entities.PermissionOverride;
import net.dv8tion.jda.api.entities.Role;
import net.dv8tion.jda.api.entities.channel.concrete.Category;
import net.dv8tion.jda.api.entities.channel.concrete.TextChannel;
import net.dv8tion.jda.api.events.interaction.ModalInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.ButtonInteractionEvent;
import net.dv8tion.jda.api.exceptions.ErrorResponseException;
import net.dv8tion.jda.api.interactions.InteractionHook;
import net.dv8tion.jda.api.managers.channel.concrete.TextChannelManager;
import net.dv8tion.jda.api.modals.Modal;
import net.dv8tion.jda.api.requests.ErrorResponse;
import net.dv8tion.jda.api.requests.restaction.ChannelAction;
import net.dv8tion.jda.api.utils.FileUpload;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.locks.ReentrantLock;
import java.util.logging.Level;
import java.util.logging.Logger;

/** Persistent component handlers. All REST work runs after acknowledgement, outside JDA event threads. */
public final class TicketService implements AutoCloseable {
    private static final Logger LOG = Logger.getLogger(TicketService.class.getName());
    private static final Set<String> ACTIONS = Set.of("ticket_wait", "ticket_review", "ticket_solved",
            "ticket_close", "ticket_members", "ticket_claim");
    private static final long READ = Permission.getRaw(Permission.VIEW_CHANNEL, Permission.MESSAGE_HISTORY);
    private static final long WRITE = READ | Permission.getRaw(Permission.MESSAGE_SEND,
            Permission.MESSAGE_ATTACH_FILES, Permission.MESSAGE_EMBED_LINKS);
    private static final long BOT_ACCESS = WRITE | Permission.getRaw(Permission.MANAGE_CHANNEL,
            Permission.MANAGE_PERMISSIONS);
    private final ConfigStore store;
    private final ExecutorService workers = Executors.newVirtualThreadPerTaskExecutor();
    private final Map<String, ReentrantLock> guildLocks;

    public TicketService(ConfigStore store) {
        this(store, new ConcurrentHashMap<>());
    }

    TicketService(ConfigStore store, Map<String, ReentrantLock> guildLocks) {
        this.store = store;
        this.guildLocks = guildLocks;
    }

    public boolean handlesButton(String id) {
        return id != null && (id.startsWith("ticket_open:") || ACTIONS.contains(id));
    }

    public boolean handlesModal(String id) {
        return id != null && (id.startsWith("ticket_reason:") || id.startsWith("ticket_member:"));
    }

    public void onButton(ButtonInteractionEvent event) {
        String id = event.getComponentId();
        if (!handlesButton(id)) return;
        if (event.getGuild() == null || event.getMember() == null) {
            event.reply("❌ Bu işlem yalnızca sunucuda kullanılabilir.").setEphemeral(true).queue();
            return;
        }
        if (id.startsWith("ticket_open:")) {
            SupportType type = SupportType.fromKey(id.substring("ticket_open:".length()));
            if (type == null) {
                event.reply("❌ Bu destek türü geçersiz. Güncel destek panelini kullanın.").setEphemeral(true).queue();
                return;
            }
            event.replyModal(Modal.create("ticket_reason:" + type.key(), "Destek Talebi Oluştur")
                    .addComponents(Label.of("Bilet açma sebebiniz", TextInput.create("reason", TextInputStyle.PARAGRAPH)
                            .setPlaceholder("Sorununuzu kısaca açıklayın…").setRequiredRange(1, 1000).build())).build()).queue();
            return;
        }
        if ("ticket_members".equals(id)) {
            TextChannel channel = event.getGuild().getTextChannelById(event.getChannelIdLong());
            if (channel == null || !hasStaffPermission(event.getMember(), channel)) {
                event.reply("❌ Bu bileti yönetme yetkiniz yok.").setEphemeral(true).queue();
                return;
            }
            event.replyModal(Modal.create("ticket_member:" + channel.getId(), "Bilete Üye Ekle")
                    .addComponents(Label.of("Üyenin Discord ID’si", TextInput.create("member_id", TextInputStyle.SHORT)
                            .setPlaceholder("123456789012345678").setRequiredRange(17, 20).build())).build()).queue();
            return;
        }
        event.deferReply(true).queue(hook -> run(event.getGuild().getId(), event.getChannel().getId(), hook, () -> {
            Guild guild = event.getGuild();
            TextChannel channel = requireChannel(guild, event.getChannelIdLong());
            Member actor = guild.retrieveMemberById(event.getUser().getId()).complete();
            requireStaff(actor, channel);
            ObjectNode ticket = loadTicket(channel);
            if ("closed".equals(ticket.path("status").asText())) throw new TicketFailure("Bu bilet zaten kapalı.");
            switch (id) {
                case "ticket_wait" -> updateStatus(channel, ticket, "wait", hook);
                case "ticket_review" -> updateStatus(channel, ticket, "review", hook);
                case "ticket_solved" -> updateStatus(channel, ticket, "solved", hook);
                case "ticket_claim" -> {
                    ticket.put("claimer_id", actor.getId());
                    updatePanel(channel, ticket, false);
                    save(channel, ticket);
                    respond(hook, "📌 Bileti üstlendiniz. Destek kartı güncellendi.");
                }
                case "ticket_close" -> closeTicket(channel, actor, ticket, hook);
                default -> throw new TicketFailure("Bu işlem desteklenmiyor.");
            }
        }));
    }

    public void onModal(ModalInteractionEvent event) {
        String id = event.getModalId();
        if (!handlesModal(id)) return;
        if (event.getGuild() == null) {
            event.reply("❌ Bu işlem yalnızca sunucuda kullanılabilir.").setEphemeral(true).queue();
            return;
        }
        if (id.startsWith("ticket_reason:")) {
            String key = id.substring("ticket_reason:".length());
            String reason = event.getValue("reason") == null ? "" : event.getValue("reason").getAsString().strip();
            event.deferReply(true).queue(hook -> run(event.getGuild().getId(), "create:" + event.getGuild().getId() + ":" + key, hook,
                    () -> createTicket(event.getGuild(), event.getUser().getId(), key, reason, hook)));
        } else {
            String channelId = id.substring("ticket_member:".length());
            String memberId = event.getValue("member_id") == null ? "" : event.getValue("member_id").getAsString().strip();
            event.deferReply(true).queue(hook -> run(event.getGuild().getId(), channelId, hook, () -> {
                if (!channelId.equals(event.getChannel().getId())) throw new TicketFailure("Üye ekleme penceresi bu bilete ait değil.");
                if (!channelId.matches("\\d{17,20}") || !memberId.matches("\\d{17,20}"))
                    throw new TicketFailure("Geçerli bir Discord kullanıcı ID’si girin.");
                Guild guild = event.getGuild();
                TextChannel channel = requireChannel(guild, Long.parseUnsignedLong(channelId));
                // A modal may remain open while staff roles or ticket state change. Always recheck at submission.
                Member actor = guild.retrieveMemberById(event.getUser().getId()).complete();
                requireStaff(actor, channel);
                ObjectNode ticket = loadTicket(channel);
                if ("closed".equals(ticket.path("status").asText())) throw new TicketFailure("Kapalı bilete üye eklenemez.");
                requireBot(channel, Permission.MANAGE_PERMISSIONS);
                Member target;
                try { target = guild.retrieveMemberById(memberId).complete(); }
                catch (ErrorResponseException error) {
                    if (error.getErrorResponse() == ErrorResponse.UNKNOWN_MEMBER) throw new TicketFailure("Bu kullanıcı sunucuda bulunamadı.");
                    throw error;
                }
                channel.upsertPermissionOverride(target).grant(WRITE).deny(0L).complete();
                respond(hook, "✅ " + target.getAsMention() + " bilete eklendi.");
            }));
        }
    }

    private void createTicket(Guild guild, String openerId, String key, String reason, InteractionHook hook) throws Exception {
        SupportType type = SupportType.fromKey(key);
        if (type == null) throw new TicketFailure("Bu destek türü geçersiz.");
        if (reason.isBlank() || reason.length() > 1000) throw new TicketFailure("Bilet sebebi 1–1000 karakter olmalı.");
        JsonNode settings = settings(guild, key);
        Category category = category(guild, settings.path("open_category").asText(), "Açık bilet kategorisi");
        Member opener = guild.retrieveMemberById(openerId).complete();
        Member bot = guild.getSelfMember();
        if (!bot.hasPermission(category, Permission.MANAGE_CHANNEL, Permission.MANAGE_PERMISSIONS,
                Permission.VIEW_CHANNEL, Permission.MESSAGE_SEND, Permission.MESSAGE_HISTORY,
                Permission.MESSAGE_EMBED_LINKS, Permission.MESSAGE_ATTACH_FILES))
            throw new TicketFailure("Botun açık bilet kategorisinde kanal/izin yönetme, görüntüleme, mesaj ve dosya gönderme izinlerini kontrol edin.");
        List<Role> roles = staffRoles(guild, settings);
        if (roles.isEmpty()) throw new TicketFailure("Bu destek türünde geçerli yetkili rolü yok. Kurulumu güncelleyin.");
        int number = store.nextTicketNumber(guild.getId(), key);
        String channelName = key.replace('_', '-') + "-" + String.format("%04d", number);
        ChannelAction<TextChannel> action = guild.createTextChannel(channelName, category)
                .setTopic(TicketText.topic(key, reason))
                .addRolePermissionOverride(guild.getPublicRole().getIdLong(), 0, Permission.VIEW_CHANNEL.getRawValue())
                .addMemberPermissionOverride(opener.getIdLong(), WRITE, 0)
                .addMemberPermissionOverride(bot.getIdLong(), BOT_ACCESS, 0);
        for (Role role : roles) action.addRolePermissionOverride(role.getIdLong(), WRITE, 0);
        TextChannel channel = action.complete();
        ObjectNode ticket = ConfigStore.MAPPER.createObjectNode();
        ticket.put("type", key).put("reason", reason).put("number", number).put("opener_id", openerId)
                .put("status", "review").put("claimer_id", "").put("created_at", OffsetDateTime.now().toString());
        // Persist the channel before sending: a send/storage failure must never silently delete an existing ticket.
        save(channel, ticket);
        String roleMentions = String.join(" ", roles.stream().map(Role::getAsMention).toList());
        Message panel;
        try {
            panel = channel.sendMessage(roleMentions).setEmbeds(ticketEmbed(ticket, type))
                    .setComponents(controls(false)).setAllowedMentions(EnumSet.of(Message.MentionType.ROLE))
                    .mentionRoles(roles.stream().map(Role::getId).toArray(String[]::new)).complete();
        } catch (Exception failure) {
            throw new TicketFailure("Bilet kanalı oluşturuldu: " + channel.getAsMention()
                    + ". Destek kartı gönderilemedi; botun mesaj ve bağlantı gömme izinlerini kontrol edin.", failure);
        }
        ticket.put("panel_message_id", panel.getId());
        save(channel, ticket);
        respond(hook, "✅ Biletiniz hazır: " + channel.getAsMention() + "\nYetkililer bu kanaldan sizinle ilgilenecek.");
    }

    boolean hasStaffPermission(Member member, TextChannel channel) {
        if (member == null || channel == null) return false;
        TicketText.Topic topic = TicketText.parseTopic(channel.getTopic());
        if (SupportType.fromKey(topic.type()) == null) return false;
        ObjectNode data = store.getGuild(channel.getGuild().getId());
        if (data == null) return false;
        JsonNode roles = data.path("support_types").path(topic.type()).path("roles");
        return member.getRoles().stream().anyMatch(role -> containsId(roles, role.getId()));
    }

    private static boolean containsId(JsonNode array, String id) {
        for (JsonNode value : array) if (id.equals(value.asText())) return true;
        return false;
    }

    private void requireStaff(Member member, TextChannel channel) throws TicketFailure {
        if (!hasStaffPermission(member, channel)) throw new TicketFailure("Bu bileti yönetme yetkiniz yok. Destek türünün yetkili rollerini kontrol edin.");
    }

    private JsonNode settings(Guild guild, String type) throws TicketFailure {
        ObjectNode data = store.getGuild(guild.getId());
        if (data == null) throw new TicketFailure("Bilet sistemi kurulmamış. /biletsisteminikur komutunu kullanın.");
        JsonNode settings = data.path("support_types").path(type);
        if (!settings.isObject()) throw new TicketFailure("Bu destek türü ayarlanmamış.");
        return settings;
    }

    private static List<Role> staffRoles(Guild guild, JsonNode settings) {
        List<Role> result = new ArrayList<>();
        for (JsonNode id : settings.path("roles")) {
            if (!id.asText().matches("\\d+")) continue;
            Role role = guild.getRoleById(id.asText());
            if (role != null && !role.isPublicRole() && !result.contains(role)) result.add(role);
        }
        return result;
    }

    private static Category category(Guild guild, String id, String label) throws TicketFailure {
        if (!id.matches("\\d+")) throw new TicketFailure(label + " ayarlanmamış.");
        Category result = guild.getCategoryById(id);
        if (result == null) throw new TicketFailure(label + " bulunamadı. Bilet sistemi kurulumunu güncelleyin.");
        return result;
    }

    private static TextChannel requireChannel(Guild guild, long id) throws TicketFailure {
        TextChannel channel = guild.getTextChannelById(id);
        if (channel == null) throw new TicketFailure("Bilet kanalı bulunamadı.");
        return channel;
    }

    private static void requireBot(TextChannel channel, Permission... permissions) throws TicketFailure {
        if (!channel.getGuild().getSelfMember().hasPermission(channel, permissions))
            throw new TicketFailure("Botun " + channel.getAsMention() + " kanalındaki gerekli izinleri eksik. Kanal izinlerini kontrol edin.");
    }

    ObjectNode loadTicket(TextChannel channel) throws Exception {
        TicketText.Topic topic = TicketText.parseTopic(channel.getTopic());
        if (SupportType.fromKey(topic.type()) == null) throw new TicketFailure("Bu kanal bir destek bileti değil.");
        ObjectNode ticket = store.getTicket(channel.getGuild().getId(), channel.getId());
        if (ticket != null) {
            if (!topic.type().equals(ticket.path("type").asText())) throw new TicketFailure("Biletin kategori bilgileri uyuşmuyor. Kanal konusunu kontrol edin.");
            return ticket;
        }
        // Existing Python tickets carry their category/reason in the topic and the rest in the original embed.
        Message panel = findPanel(channel, null);
        MessageEmbed embed = panel.getEmbeds().getFirst();
        String information = String.join("\n", embed.getFields().stream().map(MessageEmbed.Field::getValue).toList());
        ticket = ConfigStore.MAPPER.createObjectNode();
        ticket.put("type", topic.type()).put("reason", topic.reason()).put("number", TicketText.ticketNumber(embed.getTitle()))
                .put("opener_id", TicketText.firstUserId(embed.getDescription()))
                .put("panel_message_id", panel.getId()).put("status", TicketText.legacyStatus(information))
                .put("claimer_id", TicketText.legacyClaimer(information)).put("created_at", panel.getTimeCreated().toString());
        JsonNode closed = settings(channel.getGuild(), topic.type()).path("closed_category");
        if (closed.asText().equals(channel.getParentCategoryId())) ticket.put("status", "closed");
        save(channel, ticket);
        return ticket;
    }

    private Message findPanel(TextChannel channel, ObjectNode ticket) throws Exception {
        requireBot(channel, Permission.VIEW_CHANNEL, Permission.MESSAGE_HISTORY);
        if (ticket != null && ticket.path("panel_message_id").asText().matches("\\d+")) {
            try {
                Message message = channel.retrieveMessageById(ticket.path("panel_message_id").asText()).complete();
                if (isTicketPanel(message, channel)) return message;
            } catch (ErrorResponseException failure) {
                if (failure.getErrorResponse() != ErrorResponse.UNKNOWN_MESSAGE) throw failure;
            }
        }
        for (Message message : channel.getIterableHistory()) {
            if (isTicketPanel(message, channel)) return message;
        }
        throw new TicketFailure("Biletin destek kartı bulunamadı. Bilet kanalı ve konuşmalar korunuyor.");
    }

    private static boolean isTicketPanel(Message message, TextChannel channel) {
        if (!message.getAuthor().getId().equals(channel.getJDA().getSelfUser().getId()) || message.getEmbeds().isEmpty()) return false;
        String title = message.getEmbeds().getFirst().getTitle();
        return title != null && title.startsWith("🎫 Destek Talebi #");
    }

    private void updateStatus(TextChannel channel, ObjectNode ticket, String status, InteractionHook hook) throws Exception {
        ticket.put("status", status);
        updatePanel(channel, ticket, false);
        save(channel, ticket);
        respond(hook, "✅ Bilet durumu güncellendi: " + statusLabel(status));
    }

    private void updatePanel(TextChannel channel, ObjectNode ticket, boolean closed) throws Exception {
        requireBot(channel, Permission.MESSAGE_SEND, Permission.MESSAGE_EMBED_LINKS);
        Message panel = findPanel(channel, ticket);
        panel.editMessageEmbeds(ticketEmbed(ticket, SupportType.fromKey(ticket.path("type").asText())))
                .setComponents(controls(closed)).setAllowedMentions(List.of()).complete();
        ticket.put("panel_message_id", panel.getId());
    }

    private static MessageEmbed ticketEmbed(ObjectNode ticket, SupportType type) {
        String opener = mention(ticket.path("opener_id").asText(), "Bilinmiyor");
        String claimer = mention(ticket.path("claimer_id").asText(), "Henüz üstlenilmedi");
        String number = String.format("%04d", ticket.path("number").asInt());
        String status = ticket.path("status").asText("review");
        EmbedBuilder embed = new EmbedBuilder().setTitle("🎫 Destek Talebi #" + number)
                .setDescription("Talebinizi bu kanalda paylaşabilirsiniz. Yetkili ekibimiz sizinle ilgilenecek.")
                .setColor("closed".equals(status) ? 0xED4245 : "solved".equals(status) ? 0x57F287 : 0x5865F2)
                .addField("Destek türü", type == null ? "Destek" : type.label(), true)
                .addField("Bileti açan", opener, true)
                .addField("Durum", statusLabel(status), true)
                .addField("İlgilenen yetkili", claimer, true)
                .addField("Talep sebebi", TicketText.clip(ticket.path("reason").asText("Belirtilmedi"), 1024), false)
                .setFooter("Destek Merkezi • Bilet #" + number);
        try { embed.setTimestamp(OffsetDateTime.parse(ticket.path("created_at").asText())); }
        catch (RuntimeException ignored) { /* Python migration uses message creation time. */ }
        return embed.build();
    }

    static String statusLabel(String status) {
        return switch (status) {
            case "wait" -> "⏳ Beklemede";
            case "solved" -> "✅ Çözüldü";
            case "closed" -> "🔒 Kapalı";
            default -> "🔎 İnceleniyor";
        };
    }

    private static String mention(String id, String fallback) {
        return id.matches("\\d+") ? "<@" + id + ">" : fallback;
    }

    private static List<ActionRow> controls(boolean disabled) {
        return List.of(ActionRow.of(
                        Button.secondary("ticket_wait", "Beklemede").withDisabled(disabled),
                        Button.primary("ticket_review", "İnceleniyor").withDisabled(disabled),
                        Button.success("ticket_solved", "Çözüldü").withDisabled(disabled)),
                ActionRow.of(Button.danger("ticket_close", "Bileti Kapat").withDisabled(disabled),
                        Button.secondary("ticket_members", "Üye Ekle").withDisabled(disabled),
                        Button.success("ticket_claim", "Bileti Üstlen").withDisabled(disabled)));
    }

    void closeTicket(TextChannel channel, Member actor, ObjectNode ticket, InteractionHook hook) throws Exception {
        requireBot(channel, Permission.VIEW_CHANNEL, Permission.MESSAGE_HISTORY, Permission.MESSAGE_SEND,
                Permission.MESSAGE_EMBED_LINKS, Permission.MANAGE_CHANNEL, Permission.MANAGE_PERMISSIONS);
        JsonNode settings = settings(channel.getGuild(), ticket.path("type").asText());
        Category archive = null;
        String archiveId = settings.path("closed_category").asText();
        if (!archiveId.isBlank() && !"null".equals(archiveId)) archive = category(channel.getGuild(), archiveId, "Kapalı bilet kategorisi");
        TextChannel log = null;
        String logId = settings.path("log_channel").asText();
        if (!logId.isBlank() && !"null".equals(logId)) {
            if (!logId.matches("\\d+")) throw new TicketFailure("Log kanalı ayarı geçersiz. Bilet kapatılmadı.");
            log = channel.getGuild().getTextChannelById(logId);
            if (log == null || log.getId().equals(channel.getId())) throw new TicketFailure("Log kanalı bulunamadı veya bilet kanalıyla aynı. Bilet kapatılmadı.");
            requireBot(log, Permission.VIEW_CHANNEL, Permission.MESSAGE_SEND, Permission.MESSAGE_EMBED_LINKS, Permission.MESSAGE_ATTACH_FILES);
        }
        Message countdown = channel.sendMessageEmbeds(countdownEmbed(3)).setAllowedMentions(List.of()).complete();
        for (int remaining = 2; remaining >= 1; remaining--) {
            Thread.sleep(1000);
            countdown.editMessageEmbeds(countdownEmbed(remaining)).complete();
        }
        Thread.sleep(1000);
        ticket.put("closed_at", OffsetDateTime.now().toString()).put("closed_by", actor.getId());
        // Upload the entire conversation BEFORE restricting access. A failed log leaves the ticket usable.
        if (log != null && !ticket.path("log_completed").asBoolean(false)) {
            try { sendLog(channel, log, actor, ticket); }
            catch (Exception failure) {
                countdown.editMessageEmbeds(new EmbedBuilder().setTitle("❌ Bilet kapatılamadı")
                        .setDescription("Konuşma kaydı log kanalına gönderilemedi. Bilet açık bırakıldı; yetkili yeniden deneyebilir.")
                        .setColor(0xED4245).build()).complete();
                throw new TicketFailure("Transcript log kanalına gönderilemedi. Bilet ve konuşmalar korunuyor; kanal izinlerini kontrol edip tekrar deneyin.", failure);
            }
        }
        try {
            archiveChannel(channel, archive, settings);
        } catch (Exception failure) {
            // A later retry must capture messages posted after this failed attempt, even if this log uploaded.
            ticket.put("log_completed", false);
            save(channel, ticket);
            countdown.editMessageEmbeds(new EmbedBuilder().setTitle("❌ Arşivleme başarısız")
                    .setDescription("Bilet arşive taşınamadı. Konuşmalar korunuyor; botun kanal yönetme izinlerini kontrol edin.")
                    .setColor(0xED4245).build()).complete();
            throw new TicketFailure("Bilet arşive alınamadı. Konuşmalar korunuyor; yeniden denemede güncel transcript tekrar gönderilecek.", failure);
        }
        ticket.put("status", "closed");
        save(channel, ticket);
        // Even if a panel was manually deleted, successful archival remains recorded.
        String warning = "";
        try { updatePanel(channel, ticket, true); save(channel, ticket); }
        catch (Exception failure) {
            LOG.log(Level.WARNING, "Ticket archived but control panel could not be updated: " + channel.getId(), failure);
            warning = "\nDestek kartı güncellenemedi; kanal kapatma işlemi tamamlandı.";
        }
        countdown.editMessageEmbeds(new EmbedBuilder().setTitle("🔒 Bilet Kapatıldı")
                .setDescription("Konuşmalar saklandı. Bilet arşive alındı.").setColor(0xED4245).build()).complete();
        respond(hook, "🔒 Bilet kapatıldı; kanal ve konuşmalar saklandı."
                + (log == null ? "\nLog kanalı ayarlanmadığından transcript gönderilmedi." : "\nTranscript log kanalına gönderildi.") + warning);
    }

    void archiveChannel(TextChannel channel, Category archive, JsonNode settings) {
        Member bot = channel.getGuild().getSelfMember();
        TextChannelManager manager = channel.getManager();
        if (archive != null) manager.setParent(archive);
        for (PermissionOverride override : channel.getPermissionOverrides()) {
            if (!override.getId().equals(bot.getId())) manager.removePermissionOverride(override.getIdLong());
        }
        manager.putPermissionOverride(channel.getGuild().getPublicRole(), 0L, Permission.VIEW_CHANNEL.getRawValue());
        // Explicit bot access avoids Python's failure where all overrides were removed before the log was sent.
        manager.putPermissionOverride(bot, BOT_ACCESS, 0L);
        for (Role role : staffRoles(channel.getGuild(), settings))
            manager.putPermissionOverride(role, READ, Permission.MESSAGE_SEND.getRawValue());
        manager.complete();
    }

    private static MessageEmbed countdownEmbed(int remaining) {
        return new EmbedBuilder().setTitle("🔒 Bilet Kapatılıyor")
                .setDescription("Konuşmalar kaydedilecek ve bilet arşive alınacak.\n\n⏳ **" + remaining + "**")
                .setColor(0xFEE75C).build();
    }

    void sendLog(TextChannel channel, TextChannel log, Member actor, ObjectNode ticket) throws Exception {
        List<Message> messages = new ArrayList<>();
        channel.getIterableHistory().forEach(messages::add);
        messages.sort(Comparator.comparingLong(Message::getIdLong));
        StringBuilder transcript = new StringBuilder("================================\n🎫 Bilet Konuşma Kaydı\nBilet: ")
                .append(channel.getName()).append("\nSaat dilimi: Europe/Istanbul (UTC+3)\n================================\n\n");
        for (Message message : messages) {
            List<String> embeds = new ArrayList<>();
            for (MessageEmbed embed : message.getEmbeds()) {
                if (embed.getTitle() != null) embeds.add("[Kart] " + embed.getTitle());
                if (embed.getDescription() != null) embeds.add(embed.getDescription());
                for (MessageEmbed.Field field : embed.getFields()) embeds.add(field.getName() + ": " + field.getValue());
                if (embed.getUrl() != null) embeds.add(embed.getUrl());
            }
            transcript.append(TicketText.transcriptEntry(message.getTimeCreated(), message.getAuthor().getName(),
                    message.getAuthor().getId(), message.getContentRaw(), message.getAttachments().stream()
                            .map(Message.Attachment::getUrl).toList(), embeds));
        }
        transcript.append("================================\nBilet Sonu\n================================\n");
        int maximumBytes = (int) Math.min(channel.getGuild().getMaxFileSize() - 1024, 8L * 1024 * 1024);
        List<byte[]> parts = TicketText.splitUtf8(transcript.toString(), maximumBytes);
        MessageEmbed embed = new EmbedBuilder().setTitle("🔒 Bilet Kapatıldı").setColor(0xED4245)
                .addField("Bilet", channel.getAsMention() + " • " + channel.getName(), false)
                .addField("Destek türü", SupportType.fromKey(ticket.path("type").asText()).label(), true)
                .addField("Kapatan yetkili", actor.getAsMention(), true)
                .addField("Açılış / Kapanış", "Açılış: " + safeTime(ticket.path("created_at").asText())
                        + "\nKapanış: " + safeTime(ticket.path("closed_at").asText()), false)
                .addField("Talep sebebi", TicketText.clip(ticket.path("reason").asText(), 1024), false)
                .addField("Konuşma kaydı", "Tüm mesajlar ve ek bağlantıları aşağıdaki UTF-8 dosya"
                        + (parts.size() > 1 ? "larında" : "sında") + " kayıtlıdır.", false).build();
        // At most ten files per Discord message. Mark completion only after every part reached Discord.
        for (int offset = 0; offset < parts.size(); offset += 10) {
            List<FileUpload> uploads = new ArrayList<>();
            for (int i = offset; i < Math.min(offset + 10, parts.size()); i++)
                uploads.add(FileUpload.fromData(parts.get(i), channel.getName() + "-transcript"
                        + (parts.size() == 1 ? "" : "-" + (i + 1)) + ".txt"));
            try {
                Message sent = log.sendMessageEmbeds(embed).addFiles(uploads).setAllowedMentions(List.of()).complete();
                ticket.put("log_message_id", sent.getId());
                save(channel, ticket);
            } finally {
                for (FileUpload upload : uploads) upload.close();
            }
        }
        ticket.put("log_completed", true);
        save(channel, ticket);
    }

    private static String safeTime(String timestamp) {
        try { return TicketText.time(OffsetDateTime.parse(timestamp)); }
        catch (RuntimeException ignored) { return "Bilinmiyor"; }
    }

    private void save(TextChannel channel, ObjectNode ticket) throws Exception {
        store.saveTicket(channel.getGuild().getId(), channel.getId(), ticket);
    }

    private void run(String guildId, String lockKey, InteractionHook hook, Work work) {
        workers.submit(() -> {
            // This also serializes same-ticket work. ReentrantLock keeps Java 21 virtual threads unpinned.
            ReentrantLock guildLock = guildLocks.computeIfAbsent(guildId, ignored -> new ReentrantLock());
            boolean acquired = false;
            try {
                guildLock.lockInterruptibly();
                acquired = true;
                work.run();
            } catch (TicketFailure failure) {
                if (failure.getCause() != null) LOG.log(Level.WARNING, failure.getMessage(), failure.getCause());
                respond(hook, "❌ " + failure.getMessage());
            } catch (InterruptedException failure) {
                Thread.currentThread().interrupt();
                respond(hook, "❌ İşlem bot kapanırken durduruldu. Bilet kanalını kontrol edip tekrar deneyin.");
            } catch (Exception failure) {
                LOG.log(Level.SEVERE, "Ticket operation failed for " + lockKey, failure);
                respond(hook, "❌ İşlem tamamlanamadı. Botun kanal izinlerini ve konsol kaydını kontrol edin. Bilet konuşmaları silinmedi.");
            } finally {
                if (acquired) guildLock.unlock();
            }
        });
    }

    private static void respond(InteractionHook hook, String message) {
        hook.editOriginal(message).setAllowedMentions(List.of()).queue(null,
                failure -> LOG.log(Level.WARNING, "Interaction result could not be delivered", failure));
    }

    @FunctionalInterface private interface Work { void run() throws Exception; }
    private static final class TicketFailure extends Exception {
        TicketFailure(String message) { super(message); }
        TicketFailure(String message, Throwable cause) { super(message, cause); }
    }

    @Override public void close() { workers.shutdownNow(); }
}
