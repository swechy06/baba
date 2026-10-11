package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;
import net.dv8tion.jda.api.EmbedBuilder;
import net.dv8tion.jda.api.JDA;
import net.dv8tion.jda.api.JDABuilder;
import net.dv8tion.jda.api.Permission;
import net.dv8tion.jda.api.components.actionrow.ActionRow;
import net.dv8tion.jda.api.components.buttons.Button;
import net.dv8tion.jda.api.entities.Guild;
import net.dv8tion.jda.api.events.interaction.GenericInteractionCreateEvent;
import net.dv8tion.jda.api.events.interaction.ModalInteractionEvent;
import net.dv8tion.jda.api.events.interaction.command.SlashCommandInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.ButtonInteractionEvent;
import net.dv8tion.jda.api.events.interaction.component.EntitySelectInteractionEvent;
import net.dv8tion.jda.api.events.session.ReadyEvent;
import net.dv8tion.jda.api.exceptions.ErrorResponseException;
import net.dv8tion.jda.api.exceptions.InsufficientPermissionException;
import net.dv8tion.jda.api.hooks.ListenerAdapter;
import net.dv8tion.jda.api.interactions.InteractionContextType;
import net.dv8tion.jda.api.interactions.InteractionHook;
import net.dv8tion.jda.api.interactions.callbacks.IReplyCallback;
import net.dv8tion.jda.api.interactions.commands.DefaultMemberPermissions;
import net.dv8tion.jda.api.interactions.commands.build.Commands;
import net.dv8tion.jda.api.requests.GatewayIntent;
import net.dv8tion.jda.api.utils.cache.CacheFlag;

import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.ReentrantLock;

/** Application entrypoint; credentials/data remain outside the executable JAR. */
public final class TicketBot extends ListenerAdapter implements AutoCloseable {
    private final ConfigStore store;
    private final PanelService panels;
    private final TicketService tickets;
    private final ExecutorService workers = Executors.newVirtualThreadPerTaskExecutor();
    private final Map<String, ReentrantLock> guildLocks = new ConcurrentHashMap<>();
    private final SetupService setup;
    private final Map<String, ResetSession> resets = new ConcurrentHashMap<>();

    private static final class ResetSession {
        final String guildId, userId;
        final Instant expires = Instant.now().plusSeconds(60);
        final AtomicBoolean processing = new AtomicBoolean();
        ResetSession(String guildId,String userId) { this.guildId = guildId; this.userId = userId; }
    }

    TicketBot(ConfigStore store) {
        this.store = store;
        panels = new PanelService(store);
        tickets = new TicketService(store,guildLocks);
        setup = new SetupService(store,panels,workers,guildLocks);
    }

    public static void main(String[] args) {
        try {
            String token = Environment.requireToken(Path.of(".env"));
            TicketBot bot = new TicketBot(new ConfigStore(Path.of("ticket_data.json")));
            JDA jda = JDABuilder.createDefault(token)
                    .enableIntents(GatewayIntent.GUILD_MEMBERS,GatewayIntent.MESSAGE_CONTENT)
                    .enableCache(CacheFlag.MEMBER_OVERRIDES)
                    .addEventListeners(bot).build();
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                jda.shutdown();
                bot.close();
            },"ticket-shutdown"));
            jda.awaitReady();
        } catch (Exception failure) {
            System.err.println("❌ Bot başlatılamadı (" + failure.getClass().getSimpleName() + ").");
            System.err.println("Java 21+, DISCORD_TOKEN/.env, ticket_data.json ve Discord intent ayarlarını kontrol edin.");
            System.exit(1);
        }
    }

    @Override public void onReady(ReadyEvent event) {
        // Upsert our commands individually so unrelated application commands are retained.
        Map<String,String> commands = Map.of(
                "biletsisteminikur","Bilet panelini ve destek türlerini yapılandırır",
                "ticketsistemdogrulama","Bilet sisteminin ayarlarını anlaşılır biçimde gösterir",
                "biletsistemisifirla","Bilet ayarlarını ve gönderilmiş panelleri sıfırlar",
                "biletpaneliniyenile","Mevcut ayarlarla Java bilet panelini yeniden gönderir");
        commands.forEach((name,description) -> event.getJDA().upsertCommand(
                Commands.slash(name,description).setContexts(InteractionContextType.GUILD)
                        .setDefaultPermissions(DefaultMemberPermissions.enabledFor(Permission.ADMINISTRATOR)))
                .queue(command -> {},failure -> System.err.println("Komut kaydedilemedi: /" + name)));
        System.out.println("✅ " + event.getJDA().getSelfUser().getName() + " aktif. Bilet sistemi Java sürümü.");
    }

    private static boolean administrator(GenericInteractionCreateEvent event) {
        if (event.getGuild() == null || event.getMember() == null || !event.getMember().hasPermission(Permission.ADMINISTRATOR)) {
            ((IReplyCallback)event).reply("❌ Bu komutu yalnızca sunucu yöneticileri kullanabilir.").setEphemeral(true).queue();
            return false;
        }
        return true;
    }

    @Override public void onSlashCommandInteraction(SlashCommandInteractionEvent event) {
        if (!java.util.Set.of("biletsisteminikur","ticketsistemdogrulama","biletsistemisifirla","biletpaneliniyenile").contains(event.getName())) return;
        if (!administrator(event)) return;
        try {
            switch (event.getName()) {
                case "biletsisteminikur" -> setup.open(event);
                case "ticketsistemdogrulama" -> verify(event);
                case "biletsistemisifirla" -> askReset(event);
                case "biletpaneliniyenile" -> refresh(event);
            }
        } catch (RuntimeException error) { replyFailure(event,error); }
    }

    @Override public void onButtonInteraction(ButtonInteractionEvent event) {
        try {
            if (tickets.handlesButton(event.getComponentId())) tickets.onButton(event);
            else if (event.getComponentId().startsWith("setup:")) setup.onButton(event);
            else if (event.getComponentId().startsWith("reset:")) handleReset(event);
        } catch (RuntimeException error) { replyFailure(event,error); }
    }

    @Override public void onEntitySelectInteraction(EntitySelectInteractionEvent event) {
        if (!event.getComponentId().startsWith("setup:")) return;
        try { setup.onSelect(event); } catch (RuntimeException error) { replyFailure(event,error); }
    }

    @Override public void onModalInteraction(ModalInteractionEvent event) {
        try {
            if (tickets.handlesModal(event.getModalId())) tickets.onModal(event);
            else if (event.getModalId().startsWith("setup:")) setup.onModal(event);
        } catch (RuntimeException error) { replyFailure(event,error); }
    }

    private void verify(SlashCommandInteractionEvent event) {
        ObjectNode data = store.getGuild(event.getGuild().getId());
        if (data == null) { event.reply("ℹ️ Bilet sistemi henüz kurulmamış.").setEphemeral(true).queue(); return; }
        Guild guild = event.getGuild();
        EmbedBuilder embed = new EmbedBuilder().setTitle("🎫 Bilet Sistemi Durumu").setColor(0x5865F2)
                .setDescription("**Panel kanalı:** " + SetupService.displayChannel(guild,data.get("panel_channel")));
        for (SupportType type : SupportType.values()) {
            var settings = data.path("support_types").path(type.key());
            embed.addField(type.label(),
                    "Açık kategori: " + SetupService.displayChannel(guild,settings.get("open_category"))
                    + "\nKapalı kategori: " + SetupService.displayChannel(guild,settings.get("closed_category"))
                    + "\nYetkililer: " + SetupService.displayRoles(guild,settings.path("roles"))
                    + "\nLog: " + SetupService.displayChannel(guild,settings.get("log_channel")),false);
        }
        embed.setFooter("⚠️ Eksik ayarları /biletsisteminikur ile düzenleyebilirsiniz.");
        event.replyEmbeds(embed.build()).setEphemeral(true).queue();
    }

    private void refresh(SlashCommandInteractionEvent event) {
        event.deferReply(true).queue(hook -> workers.submit(() -> {
            ReentrantLock lock = guildLocks.computeIfAbsent(event.getGuild().getId(), ignored -> new ReentrantLock());
            lock.lock();
            try {
                ObjectNode settings = store.getGuild(event.getGuild().getId());
                if (settings == null) { hook.editOriginal("ℹ️ Önce /biletsisteminikur ile sistemi kurun.").queue(); return; }
                var message = panels.publish(event.getGuild(),settings);
                hook.editOriginal("✅ Ayarlarınız korundu; Java paneli gönderildi: " + message.getJumpUrl()
                        + "\nEski Python ana panelindeki açma butonlarını kullanmayın; yeni paneli kullanın.").queue();
            } catch (RuntimeException error) { report(hook,error); }
            finally { lock.unlock(); }
        }));
    }

    private void askReset(SlashCommandInteractionEvent event) {
        if (store.getGuild(event.getGuild().getId()) == null) {
            event.reply("ℹ️ Sıfırlanacak bilet sistemi bulunamadı.").setEphemeral(true).queue(); return;
        }
        resets.entrySet().removeIf(entry -> !entry.getValue().processing.get() && entry.getValue().expires.isBefore(Instant.now()));
        String id = UUID.randomUUID().toString();
        resets.put(id,new ResetSession(event.getGuild().getId(),event.getUser().getId()));
        event.replyEmbeds(new EmbedBuilder().setTitle("⚠️ Bilet Sistemi Sıfırlama").setColor(0xED4245)
                        .setDescription("Bilet ayarları ve botun gönderdiği bilet panelleri silinecek.\n"
                                + "Mevcut bilet kanalları korunacak. Devam etmek istiyor musunuz?")
                        .setFooter("Bu onay 60 saniye geçerli").build())
                .setComponents(ActionRow.of(Button.danger("reset:confirm:"+id,"Sıfırla"),Button.secondary("reset:cancel:"+id,"İptal")))
                .setEphemeral(true).queue();
    }

    private void handleReset(ButtonInteractionEvent event) {
        if (!administrator(event)) return;
        String[] parts = event.getComponentId().split(":");
        ResetSession session = parts.length == 3 ? resets.get(parts[2]) : null;
        if (session == null || session.expires.isBefore(Instant.now())) {
            event.reply("⌛ Onayın süresi doldu. /biletsistemisifirla komutunu tekrar kullanın.").setEphemeral(true).queue(); return;
        }
        if (!session.userId.equals(event.getUser().getId()) || !session.guildId.equals(event.getGuild().getId())) {
            event.reply("❌ Bu onayı yalnızca komutu kullanan yönetici verebilir.").setEphemeral(true).queue(); return;
        }
        if (session.processing.get()) { event.reply("⏳ Sıfırlama zaten devam ediyor.").setEphemeral(true).queue(); return; }
        if (parts[1].equals("cancel")) {
            resets.remove(parts[2]);
            event.editMessage("❌ İşlem iptal edildi.").setEmbeds().setComponents().queue(); return;
        }
        if (!parts[1].equals("confirm")) return;
        if (!session.processing.compareAndSet(false,true)) {
            event.reply("⏳ Sıfırlama zaten devam ediyor.").setEphemeral(true).queue(); return;
        }
        event.deferEdit().queue(hook -> workers.submit(() -> {
            ReentrantLock lock = guildLocks.computeIfAbsent(session.guildId, ignored -> new ReentrantLock());
            lock.lock();
            try {
                ObjectNode current = store.getGuild(session.guildId);
                int deleted = current == null ? 0 : panels.deleteAll(event.getGuild(),current);
                store.removeGuild(session.guildId);
                resets.remove(parts[2]);
                hook.editOriginal("✅ Bilet sistemi sıfırlandı.\n🗑️ Silinen panel: **" + deleted + "**\nMevcut bilet kanalları korunuyor.")
                        .setEmbeds().setComponents().queue();
            } catch (RuntimeException failure) {
                hook.editOriginal(friendlyError(failure) + "\nAyarlar sıfırlanmadı; sorunu giderip tekrar deneyebilirsiniz.").setEmbeds().queue();
            } finally { session.processing.set(false); lock.unlock(); }
        }),failure -> session.processing.set(false));
    }

    static void report(InteractionHook hook,RuntimeException error) {
        System.err.println("İşlem başarısız: " + error.getClass().getSimpleName());
        hook.editOriginal(friendlyError(error)).queue();
    }

    private static void replyFailure(IReplyCallback event,RuntimeException error) {
        if (event.isAcknowledged()) report(event.getHook(),error);
        else event.reply(friendlyError(error)).setEphemeral(true).queue();
    }

    static String friendlyError(RuntimeException error) {
        if (error instanceof InsufficientPermissionException || error instanceof ErrorResponseException) {
            return "❌ Discord işlemi tamamlanamadı. Botun kanal, mesaj geçmişi, rol/kanal yönetimi ve mesaj gönderme izinlerini kontrol edin; sonra tekrar deneyin.";
        }
        if (error instanceof IllegalArgumentException) return "❌ " + error.getMessage();
        if (error instanceof UncheckedIOException || error instanceof IllegalStateException) {
            return "❌ Kayıt dosyası okunamadı veya yazılamadı. ticket_data.json dosyasını ve yazma izinlerini kontrol edin.";
        }
        return "❌ İşlem tamamlanamadı. Bot konsolunu ve Discord izinlerini kontrol edip tekrar deneyin.";
    }

    @Override public void close() { tickets.close(); workers.close(); }
}
