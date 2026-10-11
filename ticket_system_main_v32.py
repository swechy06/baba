
# ticket_system_master_v1.py
# Discord Ticket Sistemi - Ana Yapı
#
# Komutlar:
# /biletsisteminikur
# /ticketsistemdogrulama
# /biletsistemisifirla
#
# Sistem:
# 1) Panel kanal seçimi
# 2) Genel Destek ayarları
#    - Açık ticket kategorisi
#    - Kapalı ticket kategorisi
#    - Roller
# 3) Rol İçi Destek ayarları
# 4) Yetkili Şikayeti ayarları
# 5) Sistem kurulumu
# 6) Sistem doğrulama
# 7) Sistem sıfırlama


import os
import json
import io
import re
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = "ticket_data.json"


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def create_default():
    return {
        "panel_channel": None,
        "panel_message_id": None,
        "support_types": {
            "gamepass_destek": {"open_category": None, "closed_category": None, "roles": []},
            "oyun_destek": {"open_category": None, "closed_category": None, "roles": []},
            "rutbe_destek": {"open_category": None, "closed_category": None, "roles": []},
            "reklam_destek": {"open_category": None, "closed_category": None, "roles": []},
            "transfer": {"open_category": None, "closed_category": None, "roles": []},
            "moderator": {"open_category": None, "closed_category": None, "roles": []}
        }
    }


SUPPORT_LABELS = {
    "gamepass_destek": "Gamepass Destek",
    "oyun_destek": "Oyun Destek",
    "rutbe_destek": "Rütbe Destek",
    "reklam_destek": "Reklam Destek",
    "transfer": "Transfer",
    "moderator": "Moderatör",
}


def resolve_channel(guild, value):
    try:
        return guild.get_channel(int(value)) if value else None
    except (TypeError, ValueError):
        return None


def is_ticket_panel(message, bot_id):
    expected_labels = {
        "Gamepass Destek Bileti", "Oyun Destek Bileti",
        "Rütbe Destek Bileti", "Reklam Destek Bileti",
        "Transfer Bileti", "Moderatör Bileti",
    }
    labels = {
        getattr(component, "label", None)
        for row in message.components for component in row.children
    }
    return (
        message.author.id == bot_id
        and any(embed.title == "# Destek Talebi Oluştur #" for embed in message.embeds)
        and expected_labels.issubset(labels)
    )


async def delete_ticket_panels(guild, settings):
    channel_id = settings.get("panel_channel")
    if not channel_id:
        return 0
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        raise ValueError("Kayıtlı panel kanalı geçersiz. Panel kanalını yeniden ayarlayın.")
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            return 0  # Kanal silinmişse panel de artık yoktur.
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Panel kanalı bir metin kanalı değil.")
    deleted = 0
    message_id = settings.get("panel_message_id")
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            pass
        else:
            if not is_ticket_panel(message, bot.user.id):
                raise ValueError("Kayıtlı mesaj bilet paneli değil; güvenlik için silinmedi.")
            try:
                await message.delete()
            except discord.NotFound:
                pass
            else:
                deleted += 1
    # Önceki sürüm mesaj ID'sini kaydetmiyordu. Aynı kanaldaki eski
    # panelleri de bul; kullanıcı mesajlarına ve bilet içi panellere dokunma.
    async for message in channel.history(limit=None):
        if is_ticket_panel(message, bot.user.id):
            try:
                await message.delete()
            except discord.NotFound:
                continue
            deleted += 1
    return deleted


class ResetView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=60)
        self.guild_id = str(guild_id)
        self.user_id = user_id
        self.processing = False

    async def interaction_check(self, interaction):
        if (interaction.guild is None
                or str(interaction.guild.id) != self.guild_id
                or interaction.user.id != self.user_id
                or not interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ Bu işlemi yalnızca komutu kullanan yönetici yapabilir.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="✅ Sıfırla", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.processing:
            await interaction.response.send_message("⏳ İşlem zaten devam ediyor.", ephemeral=True)
            return
        self.processing = True
        await interaction.response.defer()
        try:
            data = load_data()
            settings = data.get(self.guild_id)
            deleted = await delete_ticket_panels(interaction.guild, settings) if settings else 0
            # Ağ çağrıları sırasında diğer sunuculardaki güncellemeleri koru.
            data = load_data()
            data.pop(self.guild_id, None)
            save_data(data)
        except discord.Forbidden:
            text = ("❌ Panel silinemedi. Botun panel kanalında Kanalı Görüntüle ve "
                    "Mesaj Geçmişini Oku izinlerini kontrol edin. Ayarlar korunuyor; tekrar deneyebilirsiniz.")
        except discord.HTTPException:
            text = "❌ Discord'a erişirken hata oluştu. Ayarlar korunuyor; tekrar deneyebilirsiniz."
        except (OSError, ValueError) as exc:
            text = "❌ Panel veya kayıt dosyası işlenemedi. Ayarlar sıfırlanmadı; kanal ve dosya izinlerini kontrol edin."
        else:
            self.stop()
            await interaction.edit_original_response(
                content=f"✅ Bilet sistemi sıfırlandı.\n🗑️ Silinen panel: **{deleted}**\nMevcut bilet kanalları silinmedi.",
                view=None,
            )
            return
        finally:
            self.processing = False
        await interaction.edit_original_response(content=text, view=self)

    @discord.ui.button(label="❌ İptal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if self.processing:
            await interaction.response.send_message("⏳ Sıfırlama işlemi devam ediyor.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="❌ İşlem iptal edildi.", view=None)



def next_ticket_number(guild_id, ticket_type):
    data = load_data()
    guild_data = data.setdefault(str(guild_id), create_default())
    counters = guild_data.setdefault("ticket_counters", {})
    number = counters.get(ticket_type, 0) + 1
    counters[ticket_type] = number
    save_data(data)
    return number


class TicketReasonModal(discord.ui.Modal, title="Ticket Oluştur"):
    reason = discord.ui.TextInput(
        label="Ticket açma sebebiniz",
        style=discord.TextStyle.paragraph,
        placeholder="Kısaca açıklayın...",
        required=True,
        max_length=1000
    )

    def __init__(self, ticket_type):
        super().__init__()
        self.ticket_type = ticket_type

    async def on_submit(self, interaction):
        await create_ticket(interaction, self.ticket_type, self.reason.value)


class TicketReasonButtonView(discord.ui.View):
    def __init__(self, ticket_type):
        super().__init__(timeout=60)
        self.ticket_type = ticket_type

    @discord.ui.button(label="Ticket Aç", style=discord.ButtonStyle.success)
    async def open(self, interaction, button):
        await interaction.response.send_modal(
            TicketReasonModal(self.ticket_type)
        )


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Gamepass Destek Bileti", style=discord.ButtonStyle.secondary, row=0)
    async def ticket_1(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("gamepass_destek"))

    @discord.ui.button(label="Oyun Destek Bileti", style=discord.ButtonStyle.secondary, row=0)
    async def ticket_2(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("oyun_destek"))

    @discord.ui.button(label="Rütbe Destek Bileti", style=discord.ButtonStyle.secondary, row=0)
    async def ticket_3(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("rutbe_destek"))

    @discord.ui.button(label="Reklam Destek Bileti", style=discord.ButtonStyle.secondary, row=1)
    async def ticket_4(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("reklam_destek"))

    @discord.ui.button(label="Transfer Bileti", style=discord.ButtonStyle.secondary, row=1)
    async def ticket_5(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("transfer"))

    @discord.ui.button(label="Moderatör Bileti", style=discord.ButtonStyle.secondary, row=1)
    async def ticket_6(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("moderator"))


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()


bot = Bot()




class SupportSetupView(discord.ui.View):
    def __init__(self, support_type, parent_view=None):
        super().__init__(timeout=600)
        self.support_type = support_type
        self.parent_view = parent_view
        self.data = {
            "open_category": None,
            "closed_category": None,
            "roles": [],
            "log_channel": None
        }

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.category],
        placeholder="Açık ticket kategorisini seç"
    )
    async def open_cat(self, interaction, select):
        self.data["open_category"] = select.values[0].id
        await interaction.response.send_message(
            "✅ Açık ticket kategorisi kaydedildi.",
            ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.category],
        placeholder="Kapalı ticket kategorisini seç"
    )
    async def closed_cat(self, interaction, select):
        self.data["closed_category"] = select.values[0].id
        await interaction.response.send_message(
            "✅ Kapalı ticket kategorisi kaydedildi.",
            ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Log kanalını seç"
    )
    async def log_channel(self, interaction, select):
        self.data["log_channel"] = select.values[0].id
        await interaction.response.send_message(
            "✅ Log kanalı kaydedildi.",
            ephemeral=True
        )

    @discord.ui.button(label="Rol ID Gir", style=discord.ButtonStyle.secondary)
    async def role(self, interaction, button):
        await interaction.response.send_modal(RoleModal(self.data))

    @discord.ui.button(label="Kaydet", style=discord.ButtonStyle.success)
    async def save(self, interaction, button):

        if self.parent_view:
            key = {
                "Gamepass Destek Bileti": "gamepass_destek",
                "Oyun Destek Bileti": "oyun_destek",
                "Rütbe Destek Bileti": "rutbe_destek",
                "Reklam Destek Bileti": "reklam_destek",
                "Transfer Bileti": "transfer",
                "Moderatör Bileti": "moderator"
            }.get(self.support_type)

            if key:
                self.parent_view.settings["support_types"][key] = self.data

        await interaction.response.send_message(
            f"✅ {self.support_type} ayarları ana sisteme kaydedildi.",
            ephemeral=True
        )


class RoleModal(discord.ui.Modal, title="Ticket Rol Ayarı"):
    role_ids = discord.ui.TextInput(
        label="Rol ID'leri",
        placeholder="123456789,987654321"
    )

    def __init__(self, target):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction):
        self.target["roles"] = [
            x.strip() for x in self.role_ids.value.split(",")
            if x.strip()
        ]

        await interaction.response.send_message(
            "✅ Roller kaydedildi.",
            ephemeral=True
        )


class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)
        self.settings = create_default()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Ticket panel kanalını seç"
    )
    async def panel_select(self, interaction, select):
        self.settings["panel_channel"] = select.values[0].id
        await interaction.response.send_message(
            "✅ Panel kanalı seçildi.",
            ephemeral=True
        )

    @discord.ui.button(label="Gamepass Destek Bileti Ayarla", style=discord.ButtonStyle.primary, row=1)
    async def setup_1(self, interaction, button):
        await interaction.response.send_message(
            "Gamepass Destek Bileti Ayarları",
            view=SupportSetupView("Gamepass Destek Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="Oyun Destek Bileti Ayarla", style=discord.ButtonStyle.primary, row=1)
    async def setup_2(self, interaction, button):
        await interaction.response.send_message(
            "Oyun Destek Bileti Ayarları",
            view=SupportSetupView("Oyun Destek Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="Rütbe Destek Bileti Ayarla", style=discord.ButtonStyle.primary, row=1)
    async def setup_3(self, interaction, button):
        await interaction.response.send_message(
            "Rütbe Destek Bileti Ayarları",
            view=SupportSetupView("Rütbe Destek Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="Reklam Destek Bileti Ayarla", style=discord.ButtonStyle.primary, row=1)
    async def setup_4(self, interaction, button):
        await interaction.response.send_message(
            "Reklam Destek Bileti Ayarları",
            view=SupportSetupView("Reklam Destek Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="Transfer Bileti Ayarla", style=discord.ButtonStyle.primary, row=1)
    async def setup_5(self, interaction, button):
        await interaction.response.send_message(
            "Transfer Bileti Ayarları",
            view=SupportSetupView("Transfer Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="Moderatör Bileti Ayarla", style=discord.ButtonStyle.primary, row=2)
    async def setup_6(self, interaction, button):
        await interaction.response.send_message(
            "Moderatör Bileti Ayarları",
            view=SupportSetupView("Moderatör Bileti", self),
            ephemeral=True
        )

    @discord.ui.button(label="✅ Sistemi Kur", style=discord.ButtonStyle.success)
    async def kur(self, interaction, button):
        channel = resolve_channel(interaction.guild, self.settings.get("panel_channel"))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Önce geçerli bir panel kanalı seçin.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(
            title="# Destek Talebi Oluştur #",
            description=(
                "━━━━━━━━━━━━━━━━━━\n"
                "Aşağıdaki butonlardan size uygun kategoriyi seçerek destek talebi oluşturabilirsiniz.\n\n"
                "➜ **Gamepass Destek Bileti**\n"
                "Robux ile satın alım veya diğer türlü satın alımlar hakkında yardım almak için bu bileti seçin.\n\n"
                "➜ **Oyun Destek Bileti**\n"
                "Oyunumuz içinde yaşanan sorunlar, oyun içi oyuncuların usulüne uygun bir şekilde davranmaması veya haksızlığa uğradığınızı düşündüğünüzde bu bileti seçin.\n\n"
                "➜ **Rütbe Destek Bileti**\n"
                "Rütbeniz hakkında yaşanan sorunlar hakkında yardım almak için bu bileti seçin.\n\n"
                "➜ **Reklam Destek Bileti**\n"
                "Oyun içi veya Discord üzerinden farklı bir topluluk/kamp için reklam yapıldığında şikayet için bu bileti kullanın.\n\n"
                "➜ **Transfer Bileti**\n"
                "Transfer olmak için bu bileti kullanın.\n\n"
                "➜ **Moderatör Bileti**\n"
                "Discord üzerinden yaşadığınız sorunlar ve yardım için bu bileti kullanın."
            ),
            color=discord.Color.dark_grey()
        )

        try:
            message = await channel.send(embed=embed, view=TicketPanelView())
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ Panel gönderilemedi. Botun kanalı görme, mesaj gönderme ve bağlantı yerleştirme izinlerini kontrol edin.",
                ephemeral=True,
            )
            return
        data = load_data()
        previous = data.get(str(interaction.guild.id), {})
        settings = dict(self.settings)
        settings["panel_message_id"] = message.id
        if "ticket_counters" in previous:
            settings["ticket_counters"] = previous["ticket_counters"]
        data[str(interaction.guild.id)] = settings
        try:
            save_data(data)
        except (OSError, ValueError):
            await interaction.followup.send(
                "❌ Panel gönderildi ancak ayarlar kaydedilemedi. ticket_data.json dosyasının yazma iznini kontrol edin.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "✅ Ticket sistemi kuruldu ve panel gönderildi.",
            ephemeral=True
        )


@bot.tree.command(
    name="biletsisteminikur",
    description="Ticket sistem kurulum panelini açar"
)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(interaction):
    data = load_data()
    data.setdefault(str(interaction.guild.id), create_default())
    save_data(data)

    await interaction.response.send_message(
        "🎫 Ticket Sistem Kurulumu\n\n"
        "Panel kanalı ve destek türlerini ayarlayın.",
        view=SetupView(),
        ephemeral=True
    )


@bot.tree.command(
    name="ticketsistemdogrulama",
    description="Ticket sistem durumunu gösterir"
)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def verify_ticket(interaction):
    data = load_data().get(str(interaction.guild.id))
    if not data:
        await interaction.response.send_message("ℹ️ Bilet sistemi henüz kurulmamış.", ephemeral=True)
        return

    channel = resolve_channel(interaction.guild, data.get("panel_channel"))
    embed = discord.Embed(
        title="🎫 Bilet Sistemi Durumu",
        description=f"**Panel kanalı:** {channel.mention if channel else '⚠️ Seçilmemiş veya silinmiş'}",
        color=discord.Color.blurple(),
    )
    support_types = data.get("support_types", {})
    for key, label in SUPPORT_LABELS.items():
        settings = support_types.get(key, {})
        opened = resolve_channel(interaction.guild, settings.get("open_category"))
        closed = resolve_channel(interaction.guild, settings.get("closed_category"))
        def category_text(category):
            return category.name if isinstance(category, discord.CategoryChannel) else "⚠️ Seçilmemiş veya silinmiş"
        roles = []
        missing = 0
        for role_id in settings.get("roles", []):
            try:
                role = interaction.guild.get_role(int(role_id))
            except (TypeError, ValueError):
                role = None
            if role:
                roles.append(role.mention)
            else:
                missing += 1
        role_text = ", ".join(roles) or "⚠️ Yetkili rolü seçilmemiş"
        if missing:
            role_text += f" (⚠️ {missing} geçersiz/silinmiş rol)"
        embed.add_field(
            name=label,
            value=(f"Açık kategori: {category_text(opened)}\n"
                   f"Kapalı kategori: {category_text(closed)}\n"
                   f"Yetkililer: {role_text[:700]}"),
            inline=False,
        )
    embed.set_footer(text="⚠️ işaretli ayarları /biletsisteminikur ile düzenleyebilirsiniz.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="biletsistemisifirla",
    description="Ticket sistem ayarlarını temizler"
)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def reset_ticket(interaction):
    if not load_data().get(str(interaction.guild.id)):
        await interaction.response.send_message("ℹ️ Sıfırlanacak bir bilet sistemi bulunamadı.", ephemeral=True)
        return
    await interaction.response.send_message(
        "⚠️ Bilet ayarları ve gönderilen bilet panelleri silinecek.\nMevcut bilet kanalları korunacak. Devam edilsin mi?",
        view=ResetView(interaction.guild.id, interaction.user.id),
        ephemeral=True
    )


@bot.event
async def on_ready():
    print(f"{bot.user} aktif!")


class MemberAddModal(discord.ui.Modal, title="Üye Ekle"):
    discord_id = discord.ui.TextInput(
        label="Eklenecek kişinin Discord IDsi",
        placeholder="123456789012345678",
        required=True
    )

    async def on_submit(self, interaction):
        try:
            member = await interaction.guild.fetch_member(int(self.discord_id.value))
            await interaction.channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
            await interaction.response.send_message(
                f"✅ {member.mention} ticket'a eklendi.",
                ephemeral=True
            )
        except:
            await interaction.response.send_message(
                "❌ Kullanıcı bulunamadı.",
                ephemeral=True
            )



async def update_ticket_panel(channel, status=None, claimer=None):

    async for message in channel.history(limit=20):
        if not message.embeds:
            continue

        embed = message.embeds[0]

        if not embed.fields:
            continue

        field = embed.fields[0]
        value = field.value

        if status:
            value = re.sub(
                r"\*\*📊 Destek Durumu:\*\*.*",
                f"**📊 Destek Durumu:** {status}",
                value
            )

        if claimer:
            value = re.sub(
                r"\*\*👤 Biletle İlgilenen Yetkili:\*\*.*",
                f"**👤 Biletle İlgilenen Yetkili:** {claimer}",
                value
            )

        embed.set_field_at(
            0,
            name="📌 Ticket Bilgileri",
            value=value,
            inline=False
        )

        await message.edit(embed=embed)
        return



async def has_ticket_permission(interaction):
    data = load_data().get(str(interaction.guild.id), {})
    allowed = data.get("support_types", {}).get(
        get_ticket_info(interaction.channel)[0],
        {}
    ).get("roles", [])

    allowed = [int(x) for x in allowed]

    return any(
        role.id in allowed
        for role in interaction.user.roles
    )




def get_ticket_info(channel):
    topic = channel.topic or ""
    parts = topic.split("|", 1)

    ticket_type = parts[0]
    reason = parts[1] if len(parts) > 1 else "Belirtilmedi"

    return ticket_type, reason




def turkey_time():
    return datetime.now(timezone(timedelta(hours=3)))


async def create_transcript(channel):
    lines = []

    lines.append("================================")
    lines.append("🎫 Ticket Transcript")
    lines.append(f"Ticket: {channel.name}")
    lines.append("================================")
    lines.append("")

    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%d.%m.%Y %H:%M")

        lines.append(
            f"[{timestamp}] {message.author}:"
        )

        if message.content:
            lines.append(message.content)

        if message.attachments:
            for attachment in message.attachments:
                lines.append(f"📎 {attachment.url}")

        lines.append("")
        lines.append("--------------------------------")
        lines.append("")

    lines.append("================================")
    lines.append("Ticket Sonu")
    lines.append("================================")

    content = "\n".join(lines)

    return io.BytesIO(content.encode("utf-8"))



async def send_ticket_log(channel, ticket_type, reason, closed_by):
    data = load_data().get(str(channel.guild.id), {})
    settings = data.get("support_types", {}).get(ticket_type, {})

    log_id = settings.get("log_channel")

    if not log_id:
        return

    log_channel = channel.guild.get_channel(int(log_id))

    if not log_channel:
        return

    embed = discord.Embed(
        title="🔒 Ticket Kapatıldı",
        color=discord.Color.red()
    )

    embed.add_field(
        name="🆔 Ticket",
        value=channel.name,
        inline=False
    )

    embed.add_field(
        name="📂 Kategori",
        value=ticket_type.replace("_", " ").title(),
        inline=True
    )

    embed.add_field(
        name="🔧 Kapatan Yetkili",
        value=closed_by.mention,
        inline=True
    )

    embed.add_field(
        name="⏰ Açılış / 🔒 Kapanış",
        value=(
            f"⏰ **Açılış:** {channel.created_at.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M')}\n"
            f"🔒 **Kapanış:** {turkey_time().strftime('%d.%m.%Y %H:%M')}"
        ),
        inline=False
    )

    embed.add_field(
        name="📝 Ticket Sebebi",
        value=reason or "Belirtilmedi",
        inline=False
    )

    embed.add_field(
        name="📜 Transcript",
        value="Transcript sistemi bağlanmaya hazır.",
        inline=False
    )

    transcript_file = await create_transcript(channel)

    await log_channel.send(
        embed=embed,
        file=discord.File(
            transcript_file,
            filename=f"{channel.name}-transcript.txt"
        )
    )


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏳ Beklemede", style=discord.ButtonStyle.secondary, row=0, custom_id="ticket_wait")
    async def wait_status(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message(
                "❌ Bu işlemi yapmak için yetkiniz yok.",
                ephemeral=True
            )
            return

        await update_ticket_panel(interaction.channel, "⏳ Beklemede")
        await interaction.response.send_message("⏳ Durum güncellendi.", ephemeral=True)

    @discord.ui.button(label="🔎 İnceleniyor", style=discord.ButtonStyle.primary, row=0, custom_id="ticket_review")
    async def reviewing_status(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message(
                "❌ Bu işlemi yapmak için yetkiniz yok.",
                ephemeral=True
            )
            return

        await update_ticket_panel(interaction.channel, "🔎 İnceleniyor")
        await interaction.response.send_message("🔎 Durum güncellendi.", ephemeral=True)

    @discord.ui.button(label="✅ Çözüldü", style=discord.ButtonStyle.success, row=0, custom_id="ticket_solved")
    async def solved_status(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message(
                "❌ Bu işlemi yapmak için yetkiniz yok.",
                ephemeral=True
            )
            return

        await update_ticket_panel(interaction.channel, "✅ Çözüldü")
        await interaction.response.send_message("✅ Durum güncellendi.", ephemeral=True)

    @discord.ui.button(label="🔒 Bileti Kapat", style=discord.ButtonStyle.danger, row=1, custom_id="ticket_close")
    async def close_ticket(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message(
                "❌ Bu işlemi yapmak için yetkiniz yok.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🔒 Bilet Kapatılıyor",
            description="Ticket kapanıyor...\n\n⏳ **3**",
            color=discord.Color.orange()
        )

        await interaction.response.send_message(
            embed=embed
        )

        import asyncio

        msg = await interaction.original_response()

        for number in ["2", "1"]:
            await asyncio.sleep(1)

            embed.description = (
                "Ticket kapanıyor...\n\n"
                f"⏳ **{number}**"
            )

            await msg.edit(embed=embed)

        await asyncio.sleep(1)

        channel = interaction.channel

        # Tüm özel izinleri kaldır
        overwrites = {
            interaction.guild.default_role:
            discord.PermissionOverwrite(view_channel=False)
        }

        await channel.edit(
            overwrites=overwrites
        )

        data = load_data().get(str(interaction.guild.id), {})
        ticket_type, reason = get_ticket_info(channel)
        closed = data.get("support_types", {}).get(ticket_type, {}).get("closed_category")

        if closed:
            category = interaction.guild.get_channel(int(closed))
            if category:
                await channel.edit(category=category)

        await send_ticket_log(
            channel,
            ticket_type,
            reason,
            interaction.user
        )

        await msg.edit(
            embed=discord.Embed(
                title="🔒 Bilet Kapatıldı",
                description="Ticket başarıyla kapatıldı.",
                color=discord.Color.red()
            )
        )

    @discord.ui.button(label="👥 Üyeleri Yönet", style=discord.ButtonStyle.secondary, row=1, custom_id="ticket_members")
    async def manage_members(self, interaction, button):
        data=load_data().get(str(interaction.guild.id),{})
        allowed=data.get("support_types",{}).get(get_ticket_info(interaction.channel)[0],{}).get("roles",[])

        if not any(r.id in [int(x) for x in allowed] for r in interaction.user.roles):
            await interaction.response.send_message(
                "❌ Bu butonu kullanma yetkiniz yok.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(MemberAddModal())

    @discord.ui.button(label="📌 Bilet Üstlenme", style=discord.ButtonStyle.success, row=1, custom_id="ticket_claim")
    async def claim_ticket(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message(
                "❌ Bu işlemi yapmak için yetkiniz yok.",
                ephemeral=True
            )
            return

        await update_ticket_panel(
            interaction.channel,
            claimer=interaction.user.mention
        )
        await interaction.response.send_message(
            f"📌 {interaction.user.mention} bileti üstlendi.",
            ephemeral=True
        )


async def create_ticket(interaction, ticket_type, reason=None):

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    user = interaction.user

    data = load_data().get(str(guild.id))

    if not data:
        await interaction.followup.send(
            "❌ Ticket sistemi kurulmamış.",
            ephemeral=True
        )
        return

    settings = data["support_types"].get(ticket_type)

    if not settings or not settings.get("open_category"):
        await interaction.followup.send(
            "❌ Bu ticket türü ayarlanmamış.",
            ephemeral=True
        )
        return

    category = guild.get_channel(
        int(settings["open_category"])
    )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True
        )
    }

    for role_id in settings.get("roles", []):
        role = guild.get_role(int(role_id))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )

    ticket_no = next_ticket_number(guild.id, ticket_type)

    category_names = {
        "gamepass_destek": "Gamepass Destek Bileti",
        "oyun_destek": "Oyun Destek Bileti",
        "rutbe_destek": "Rütbe Destek Bileti",
        "reklam_destek": "Reklam Destek Bileti",
        "transfer": "Transfer Bileti",
        "moderator": "Moderatör Bileti"
    }

    channel = await guild.create_text_channel(
        name=f"{category_names.get(ticket_type, 'Destek')}-{ticket_no:04d}",
        category=category,
        overwrites=overwrites,
        topic=f'{ticket_type}|{reason or "Belirtilmedi"}'
    )

    role_objects = [
        guild.get_role(int(r))
        for r in settings.get("roles", [])
        if guild.get_role(int(r))
    ]

    role_mentions = " ".join(role.mention for role in role_objects) or "Yetkili rolü ayarlanmamış."

    embed = discord.Embed(
        title=f"🎫 Destek Talebi #{ticket_no:04d}",
        description=(
            f"👤 **{user.mention}** tarafından ticket talebi\n"
            f"🕒 **{turkey_time().strftime('%d.%m.%Y %H:%M')} tarihinde oluşturuldu.**\n\n"
            f"{role_mentions}\n"
            "rolüne sahip yetkililer sizinle ilgilenecektir.\n"
            "━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.dark_blue()
    )

    embed.set_footer(text="Destek Merkezi • Ticket Sistemi")

    embed.add_field(
        name="📌 Ticket Bilgileri",
        value=(
            f"**🆔 Ticket ID:** #{ticket_no:04d}\n\n"
            f"**📂 Destek Kategorisi:** {category_names.get(ticket_type, ticket_type)}\n\n"
            f"**📝 Talep Sebebi:**\n{reason or 'Belirtilmedi'}\n\n"
            "**📊 Destek Durumu:** 🟢 İnceleniyor\n\n**👤 Biletle İlgilenen Yetkili:** N/A"
        ),
        inline=False
    )

    await channel.send(
        content=role_mentions,
        embed=embed,
        view=CloseTicketView()
    )

    await interaction.followup.send(
        f"✅ Ticket oluşturuldu: {channel.mention}",
        ephemeral=True
    )


# Panel butonlarına gerçek ticket açma bağlantısı
async def general_ticket(interaction):
    await create_ticket(interaction, "genel_destek")


async def role_ticket(interaction):
    await create_ticket(interaction, "rol_ici_destek")


async def complaint_ticket(interaction):
    await create_ticket(interaction, "yetkili_sikayet")



bot.run(TOKEN)


# v14 - Ticket modal ve açılış panel entegrasyonu düzeltildi

# ==========================================================
# TICKET PANEL GELİŞTİRME MODÜLÜ
# ==========================================================
#
# Bu ana dosyaya eklenecek sistemler:
#
# 1) /biletsisteminikur
#    - Panel kanal seçimi
#    - Genel Destek ayarı
#    - Rol İçi Destek ayarı
#    - Yetkili Şikayeti ayarı
#
# 2) Destek türü ayarları:
#    Genel Destek:
#       - Açık ticket kategorisi
#       - Kapalı ticket kategorisi
#       - Görme yetkili roller
#
#    Rol İçi Destek:
#       - Açık ticket kategorisi
#       - Kapalı ticket kategorisi
#       - Görme yetkili roller
#
#    Yetkili Şikayeti:
#       - Açık ticket kategorisi
#       - Kapalı ticket kategorisi
#       - Görme yetkili roller
#
# 3) Sistemi Kur:
#    - Ayarları kaydetme
#    - Ticket panelini seçilen kanala gönderme
#
# 4) Ticket Panel:
#    - Genel Destek
#    - Rol İçi Destek
#    - Yetkili Şikayeti
#
# 5) Ticket işlemleri:
#    - Kanal oluşturma
#    - Doğru kategoriye taşıma
#    - Sadece ilgili rollerin görmesi
#    - Kapatma sistemi
#
# 6) Yönetim:
#    - /ticketsistemdogrulama
#    - /biletsistemisifirla
#
# ==========================================================


# ==========================================================
# ANA DOSYA GELİŞTİRME NOTU
# ==========================================================
# Bu sürüm ana dosya olarak kullanılacaktır.
#
# Korunan komutlar:
# /biletsisteminikur
# /ticketsistemdogrulama
# /biletsistemisifirla
#
# Eklenecek gerçek panel sistemi:
# - Panel kanal seçimi
# - Genel Destek:
#   açık kategori / kapalı kategori / roller
# - Rol İçi Destek:
#   açık kategori / kapalı kategori / roller
# - Yetkili Şikayeti:
#   açık kategori / kapalı kategori / roller
# - Sistemi Kur ile kayıt
# - Ticket panel gönderimi
# - Ticket oluşturma
#
# Bu dosya bundan sonraki ana dosya olarak kullanılacaktır.


# ==========================================================
# V3 INTEGRATION TARGET
# ==========================================================
# The next implementation layer adds the real SetupView panel:
# - Panel channel selector
# - Genel Destek button
# - Rol İçi Destek button
# - Yetkili Şikayeti button
# - Sistemi Kur button
#
# Existing commands remain:
# /biletsisteminikur
# /ticketsistemdogrulama
# /biletsistemisifirla


# ==========================================================
# TICKET OLUŞTURMA SİSTEMİ
# ==========================================================




# ==========================================================
# V22 LOG + TRANSCRIPT ENTEGRASYON NOTU
# ==========================================================
# Ana ticket sistemi üzerine eklenecek:
#
# /biletsisteminikur içinde:
# - Genel Destek Log Kanalı
# - Rol İçi Destek Log Kanalı
# - Yetkili Şikayeti Log Kanalı
#
# Ticket kapanınca:
# - İlgili ticket türünün log kanalına gönderim
# - Ticket bilgileri
# - Açan kullanıcı
# - Üstlenen yetkili
# - Açılış/kapanış zamanı
# - Transcript dosyası
#
# Mevcut komutlar korunur.
# ==========================================================
