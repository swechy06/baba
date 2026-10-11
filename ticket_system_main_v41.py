
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
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = "ticket_data.json"


# Persistent staff statistics. Kept separate from ticket_data.json so resetting
# the ticket panel does not erase closed tickets or user evaluations.
import copy as _stats_copy
import json as _stats_json
import math as _stats_math
import os as _stats_os
import statistics as _stats_statistics
import tempfile as _stats_tempfile
import threading as _stats_threading
from datetime import datetime as _StatsDateTime, timezone as _StatsTimezone
from pathlib import Path as _StatsPath


STATS_FILE = "ticket_stats.json"
_TICKET_STATS_LOCK = _stats_threading.RLock()


class TicketStatsError(RuntimeError):
    """Statistics could not be read or safely persisted."""


def _stats_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Geçersiz kimlik.")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("Geçersiz kimlik.")
    result = int(value)
    if result <= 0:
        raise ValueError("Geçersiz kimlik.")
    return result


def _stats_datetime(value=None):
    if value is None:
        return _StatsDateTime.now(_StatsTimezone.utc)
    if isinstance(value, str):
        value = _StatsDateTime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, _StatsDateTime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Tarih saat dilimi içermeli.")
    return value.astimezone(_StatsTimezone.utc)


def _stats_validate(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("İstatistik dosyası sürümü geçersiz.")
    if not isinstance(data.get("guilds"), dict):
        raise ValueError("Sunucu istatistikleri geçersiz.")
    required = {
        "owner_id", "ticket_type", "ticket_number", "opened_at", "panel_message_id",
        "claimed_by", "interventions", "closed_at", "closed_by", "staff_id",
        "rating_score", "rating_message_id", "rating_dm_channel_id", "rating_state",
    }
    for guild_id, guild in data["guilds"].items():
        if not isinstance(guild_id, str) or str(_stats_id(guild_id)) != guild_id:
            raise ValueError("Sunucu kimliği geçersiz.")
        if not isinstance(guild, dict) or not isinstance(guild.get("tickets"), dict):
            raise ValueError("Bilet istatistikleri geçersiz.")
        for channel_id, record in guild["tickets"].items():
            if not isinstance(channel_id, str) or str(_stats_id(channel_id)) != channel_id:
                raise ValueError("Bilet kimliği geçersiz.")
            if not isinstance(record, dict) or not required.issubset(record):
                raise ValueError("Bilet kaydı eksik.")
            if record["owner_id"] is not None:
                _stats_id(record["owner_id"])
            if not isinstance(record["ticket_type"], str) or not record["ticket_type"]:
                raise ValueError("Destek türü geçersiz.")
            if isinstance(record["ticket_number"], bool) or not isinstance(record["ticket_number"], (str, int)):
                raise ValueError("Bilet numarası geçersiz.")
            if not isinstance(record["opened_at"], str):
                raise ValueError("Bilet açılış zamanı geçersiz.")
            _stats_datetime(record["opened_at"])
            for key in ("panel_message_id", "claimed_by", "closed_by", "staff_id", "rating_message_id", "rating_dm_channel_id"):
                if record[key] is not None:
                    _stats_id(record[key])
            if not isinstance(record["interventions"], dict):
                raise ValueError("Müdahale kayıtları geçersiz.")
            for staff_id, intervention in record["interventions"].items():
                if not isinstance(staff_id, str) or str(_stats_id(staff_id)) != staff_id:
                    raise ValueError("Yetkili kimliği geçersiz.")
                if not isinstance(intervention, dict) or not {"at", "elapsed_seconds"}.issubset(intervention):
                    raise ValueError("Müdahale kaydı eksik.")
                if not isinstance(intervention["at"], str):
                    raise ValueError("Müdahale zamanı geçersiz.")
                _stats_datetime(intervention["at"])
                elapsed = intervention["elapsed_seconds"]
                if isinstance(elapsed, bool) or not isinstance(elapsed, (float, int)) or not _stats_math.isfinite(elapsed) or elapsed < 0:
                    raise ValueError("Müdahale süresi geçersiz.")
            if record["closed_at"] is not None:
                if not isinstance(record["closed_at"], str):
                    raise ValueError("Kapanış zamanı geçersiz.")
                _stats_datetime(record["closed_at"])
                if record["closed_by"] is None or record["staff_id"] is None:
                    raise ValueError("Kapatan yetkili eksik.")
            elif record["closed_by"] is not None or record["staff_id"] is not None or record["rating_score"] is not None:
                raise ValueError("Açık bilet kapanış bilgisi içeriyor.")
            score = record["rating_score"]
            if score is not None and (type(score) is not int or not 1 <= score <= 5):
                raise ValueError("Değerlendirme puanı geçersiz.")
            if not isinstance(record["rating_state"], str) or not record["rating_state"]:
                raise ValueError("Değerlendirme durumu geçersiz.")
    return data


def _stats_read_file(path):
    try:
        with path.open("r", encoding="utf-8") as source:
            return _stats_validate(_stats_json.load(source))
    except FileNotFoundError:
        return {"version": 1, "guilds": {}}
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as error:
        raise TicketStatsError(
            "ticket_stats.json okunamadı veya bozuk. Mevcut dosya korunuyor; yedekten geri yükleyin."
        ) from error


def load_ticket_stats():
    """Return a fresh validated snapshot, never replacing a damaged file."""
    with _TICKET_STATS_LOCK:
        return _stats_read_file(_StatsPath(STATS_FILE))


def save_ticket_stats(data):
    """Replace the file atomically only after validating old and new data."""
    with _TICKET_STATS_LOCK:
        _stats_validate(data)
        path = _StatsPath(STATS_FILE)
        # Even direct callers cannot accidentally overwrite a malformed file.
        _stats_read_file(path)
        temporary_path = None
        try:
            with _stats_tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(path.parent),
                prefix=".ticket_stats.", suffix=".tmp", delete=False,
            ) as target:
                temporary_path = _StatsPath(target.name)
                _stats_json.dump(data, target, ensure_ascii=False, indent=2, allow_nan=False)
                target.write("\n")
                target.flush()
                _stats_os.fsync(target.fileno())
            _stats_os.replace(temporary_path, path)
            temporary_path = None
        except (OSError, ValueError, TypeError) as error:
            raise TicketStatsError("Bilet istatistikleri kaydedilemedi; mevcut dosya korunuyor.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _stats_record(data, guild_id, channel_id):
    guild = data["guilds"].get(str(_stats_id(guild_id)), {})
    return guild.get("tickets", {}).get(str(_stats_id(channel_id)))


def stats_get_ticket(guild_id, channel_id):
    with _TICKET_STATS_LOCK:
        return _stats_copy.deepcopy(_stats_record(load_ticket_stats(), guild_id, channel_id))


def stats_register_ticket(guild_id, channel_id, *, ticket_type, ticket_number,
                          owner_id, opened_at, panel_message_id=None, claimed_by=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        existing = _stats_record(data, guild_id, channel_id)
        if existing is not None:
            return _stats_copy.deepcopy(existing)
        if opened_at is None:
            raise ValueError("Bilet açılış zamanı gerekli.")
        record = {
            "owner_id": None if owner_id is None else _stats_id(owner_id),
            "ticket_type": ticket_type,
            "ticket_number": ticket_number,
            "opened_at": _stats_datetime(opened_at).isoformat(),
            "panel_message_id": None if panel_message_id is None else _stats_id(panel_message_id),
            "claimed_by": None if claimed_by is None else _stats_id(claimed_by),
            "interventions": {},
            "closed_at": None,
            "closed_by": None,
            "staff_id": None,
            "rating_score": None,
            "rating_message_id": None,
            "rating_dm_channel_id": None,
            "rating_state": "pending",
        }
        guild = data["guilds"].setdefault(str(_stats_id(guild_id)), {"tickets": {}})
        guild["tickets"][str(_stats_id(channel_id))] = record
        save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def _stats_touch_record(record, staff_id, at):
    staff_key = str(_stats_id(staff_id))
    occurred_at = _stats_datetime(at)
    existing = record["interventions"].get(staff_key)
    if existing is not None and _stats_datetime(existing["at"]) <= occurred_at:
        return False
    opened_at = _stats_datetime(record["opened_at"])
    record["interventions"][staff_key] = {
        "at": occurred_at.isoformat(),
        "elapsed_seconds": max(0.0, (occurred_at - opened_at).total_seconds()),
    }
    return True


def stats_touch_ticket(guild_id, channel_id, staff_id, at=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None or record["closed_at"] is not None:
            return _stats_copy.deepcopy(record)
        if _stats_touch_record(record, staff_id, at):
            save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def stats_claim_ticket(guild_id, channel_id, staff_id, at=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None or record["closed_at"] is not None:
            return _stats_copy.deepcopy(record)
        staff_id = _stats_id(staff_id)
        _stats_touch_record(record, staff_id, at)
        record["claimed_by"] = staff_id
        save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def stats_close_ticket(guild_id, channel_id, closed_by, at=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None or record["closed_at"] is not None:
            return _stats_copy.deepcopy(record)
        closed_by = _stats_id(closed_by)
        occurred_at = _stats_datetime(at)
        _stats_touch_record(record, closed_by, occurred_at)
        record["closed_at"] = occurred_at.isoformat()
        record["closed_by"] = closed_by
        record["staff_id"] = record["claimed_by"] or closed_by
        save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def stats_set_panel_message(guild_id, channel_id, message_id):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None or record["closed_at"] is not None:
            return _stats_copy.deepcopy(record)
        record["panel_message_id"] = _stats_id(message_id)
        save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def stats_set_delivery(guild_id, channel_id, *, state, message_id=None, dm_channel_id=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None:
            return None
        record["rating_state"] = "rated" if record["rating_score"] is not None else state
        if message_id is not None:
            record["rating_message_id"] = _stats_id(message_id)
        if dm_channel_id is not None:
            record["rating_dm_channel_id"] = _stats_id(dm_channel_id)
        save_ticket_stats(data)
        return _stats_copy.deepcopy(record)


def stats_submit_rating(guild_id, channel_id, owner_id, score, message_id=None):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        record = _stats_record(data, guild_id, channel_id)
        if record is None or record["closed_at"] is None:
            return "unavailable"
        if record["owner_id"] is None:
            return "unavailable"
        try:
            if _stats_id(owner_id) != _stats_id(record["owner_id"]):
                return "forbidden"
        except (ValueError, TypeError):
            return "forbidden"
        if type(score) is not int or not 1 <= score <= 5:
            return "invalid"
        if message_id is not None:
            if record["rating_message_id"] is None:
                return "unavailable"
            try:
                if _stats_id(message_id) != _stats_id(record["rating_message_id"]):
                    return "forbidden"
            except (ValueError, TypeError):
                return "forbidden"
        if record["rating_score"] is not None:
            return "already_rated"
        record["rating_score"] = score
        record["rating_state"] = "rated"
        save_ticket_stats(data)
        return "saved"


def stats_for_staff(guild_id, staff_id):
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        guild = data["guilds"].get(str(_stats_id(guild_id)), {"tickets": {}})
        staff_id = _stats_id(staff_id)
        resolved = [record for record in guild["tickets"].values()
                    if record["closed_at"] is not None and _stats_id(record["staff_id"]) == staff_id]
        responses = [record["interventions"][str(staff_id)]["elapsed_seconds"]
                     for record in resolved if str(staff_id) in record["interventions"]]
        ratings = [record["rating_score"] for record in resolved
                   if type(record["rating_score"]) is int and 1 <= record["rating_score"] <= 5]
        return {
            "resolved_count": len(resolved),
            "average_response_seconds": _stats_statistics.mean(responses) if responses else None,
            "response_sample_count": len(responses),
            "average_rating": _stats_statistics.mean(ratings) if ratings else None,
            "rating_count": len(ratings),
        }


def stats_pending_ratings():
    with _TICKET_STATS_LOCK:
        data = load_ticket_stats()
        return [(int(guild_id), int(channel_id), _stats_copy.deepcopy(record))
                for guild_id, guild in data["guilds"].items()
                for channel_id, record in guild["tickets"].items()
                if record["closed_at"] is not None and record["rating_score"] is None
                and record["rating_message_id"] is not None]


AUTHORIZED_COMMAND_ROLE = 1558781695950331904


async def command_role_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Bu komut sadece sunucuda kullanılabilir.",
                ephemeral=True
            )
        return False

    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except Exception:
            member = None

    if member and any(role.id == AUTHORIZED_COMMAND_ROLE for role in member.roles):
        return True

    if not interaction.response.is_done():
        await interaction.response.send_message(
            "❌ Bu bot komutlarını kullanma yetkiniz yok.",
            ephemeral=True
        )
    return False




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

    @discord.ui.button(label="Gamepass Destek Bileti", style=discord.ButtonStyle.secondary, row=0, custom_id="ticket_open:gamepass_destek")
    async def ticket_1(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("gamepass_destek"))

    @discord.ui.button(label="Oyun Destek Bileti", style=discord.ButtonStyle.secondary, row=0, custom_id="ticket_open:oyun_destek")
    async def ticket_2(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("oyun_destek"))

    @discord.ui.button(label="Rütbe Destek Bileti", style=discord.ButtonStyle.secondary, row=0, custom_id="ticket_open:rutbe_destek")
    async def ticket_3(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("rutbe_destek"))

    @discord.ui.button(label="Reklam Destek Bileti", style=discord.ButtonStyle.secondary, row=1, custom_id="ticket_open:reklam_destek")
    async def ticket_4(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("reklam_destek"))

    @discord.ui.button(label="Transfer Bileti", style=discord.ButtonStyle.secondary, row=1, custom_id="ticket_open:transfer")
    async def ticket_5(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("transfer"))

    @discord.ui.button(label="Moderatör Bileti", style=discord.ButtonStyle.secondary, row=1, custom_id="ticket_open:moderator")
    async def ticket_6(self, interaction, button):
        await interaction.response.send_modal(TicketReasonModal("moderator"))


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Kalıcı bilet kontrolleri ve DM puanlama butonları yeniden başlatmada da çalışır.
        self.add_view(CloseTicketView())
        self.add_view(TicketPanelView())
        for guild_id, channel_id, record in stats_pending_ratings():
            self.add_view(
                TicketRatingView(guild_id, channel_id),
                message_id=int(record["rating_message_id"]),
            )
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
@app_commands.guild_only()
async def setup_ticket(interaction):
    if not await command_role_check(interaction):
        return
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
@app_commands.guild_only()
async def verify_ticket(interaction):
    if not await command_role_check(interaction):
        return
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
@app_commands.guild_only()
async def reset_ticket(interaction):
    if not await command_role_check(interaction):
        return
    if not load_data().get(str(interaction.guild.id)):
        await interaction.response.send_message("ℹ️ Sıfırlanacak bir bilet sistemi bulunamadı.", ephemeral=True)
        return
    await interaction.response.send_message(
        "⚠️ Bilet ayarları ve gönderilen bilet panelleri silinecek.\nMevcut bilet kanalları korunacak. Devam edilsin mi?",
        view=ResetView(interaction.guild.id, interaction.user.id),
        ephemeral=True
    )


@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Bu bot komutlarını kullanma yetkiniz yok.",
                ephemeral=True
            )
        return
    raise error


async def restore_existing_ticket_panels():
    # Önceki sürümlerin rastgele buton kimliklerini, mesajı değiştirmeden geri bağla.
    data = load_data()
    for guild in bot.guilds:
        settings = data.get(str(guild.id), {})
        channel = resolve_channel(guild, settings.get("panel_channel"))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            messages = []
            if settings.get("panel_message_id"):
                try:
                    messages.append(await channel.fetch_message(int(settings["panel_message_id"])))
                except discord.NotFound:
                    pass
            else:
                async for message in channel.history(limit=None):
                    if is_ticket_panel(message, bot.user.id):
                        messages.append(message)
            for message in messages:
                if not is_ticket_panel(message, bot.user.id):
                    continue
                custom_ids = {
                    component.label: component.custom_id
                    for row in message.components for component in row.children
                    if getattr(component, "custom_id", None)
                }
                view = TicketPanelView()
                if not all(child.label in custom_ids for child in view.children):
                    continue
                for child in view.children:
                    child.custom_id = custom_ids[child.label]
                bot.add_view(view, message_id=message.id)
        except (discord.HTTPException, ValueError):
            logging.getLogger("ticket.statistics").exception("Eski bilet paneli yeniden bağlanamadı")


@bot.event
async def on_ready():
    print(f"{bot.user} aktif!")
    try:
        await restore_existing_ticket_panels()
    except (OSError, ValueError):
        logging.getLogger("ticket.statistics").exception("Bilet paneli kayıtları okunamadı")


class MemberAddModal(discord.ui.Modal, title="Üye Ekle"):
    discord_id = discord.ui.TextInput(label="Eklenecek kişinin Discord IDsi", placeholder="123456789012345678", required=True)

    async def on_submit(self, interaction):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message("❌ Bu bileti yönetme yetkiniz yok.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with ticket_operation_lock(interaction.channel):
                record = await ensure_ticket_record(interaction.channel)
                if record.get("closed_at"):
                    await interaction.followup.send("❌ Kapalı bilete üye eklenemez.", ephemeral=True)
                    return
                await recover_staff_interventions(interaction.channel, record, [interaction.user.id])
                member = await interaction.guild.fetch_member(int(self.discord_id.value))
                await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
                stats_touch_ticket(interaction.guild.id, interaction.channel.id, interaction.user.id, at=interaction.created_at)
                await interaction.followup.send(f"✅ {member.mention} ticket'a eklendi.", ephemeral=True)
        except (ValueError, discord.NotFound):
            await interaction.followup.send("❌ Kullanıcı bulunamadı veya biletin kaydı doğrulanamadı.", ephemeral=True)
        except (OSError, RuntimeError, discord.HTTPException):
            _STATS_LOG.exception("Üye ekleme veya müdahale kaydı başarısız")
            await interaction.followup.send("❌ Üye eklenemedi veya istatistik kaydedilemedi. Botun kanal ve dosya izinlerini kontrol edin.", ephemeral=True)



async def update_ticket_panel(channel, status=None, claimer=None):
    record = stats_get_ticket(channel.guild.id, channel.id)
    message = await find_ticket_message(channel, record)
    if message is None or not message.embeds[0].fields:
        raise ValueError("Biletin destek kartı bulunamadı.")
    embed = message.embeds[0].copy()
    value = embed.fields[0].value
    if status:
        value = re.sub(r"\*\*📊 Destek Durumu:\*\*.*", f"**📊 Destek Durumu:** {status}", value)
    if claimer:
        value = re.sub(r"\*\*👤 Biletle İlgilenen Yetkili:\*\*.*", f"**👤 Biletle İlgilenen Yetkili:** {claimer}", value)
    embed.set_field_at(0, name="📌 Ticket Bilgileri", value=value, inline=False)
    await message.edit(embed=embed)
    if record is not None:
        stats_set_panel_message(channel.guild.id, channel.id, message.id)



async def has_ticket_permission(interaction):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        return False
    ticket_type, _ = get_ticket_info(interaction.channel)
    allowed = valid_support_roles(interaction.guild, ticket_type)
    return any(role.id in allowed for role in getattr(interaction.user, "roles", []))



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


# Yetkili istatistikleri ayarlardan bağımsız tutulur; sistem sıfırlansa da geçmiş korunur.
_STATS_LOG = logging.getLogger("ticket.statistics")
_ticket_operation_locks = defaultdict(asyncio.Lock)
_stats_history_recovered = set()


def ticket_operation_lock(channel):
    return _ticket_operation_locks[(str(channel.guild.id), str(channel.id))]


def valid_support_roles(guild, ticket_type):
    settings = load_data().get(str(guild.id), {}).get("support_types", {}).get(ticket_type, {})
    roles = set()
    for value in settings.get("roles", []):
        try:
            roles.add(int(value))
        except (ValueError, TypeError):
            continue
    return roles


def is_ticket_message(message):
    return (
        bot.user is not None and message.author.id == bot.user.id
        and message.embeds and (message.embeds[0].title or "").startswith("🎫 Destek Talebi #")
    )


async def find_ticket_message(channel, record=None):
    message_id = (record or {}).get("panel_message_id")
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            pass
        else:
            if is_ticket_message(message):
                return message
    async for message in channel.history(limit=None, oldest_first=True):
        if is_ticket_message(message):
            return message
    return None


async def ensure_ticket_record(channel):
    record = stats_get_ticket(channel.guild.id, channel.id)
    if record is not None:
        return record
    ticket_type, _ = get_ticket_info(channel)
    if ticket_type not in SUPPORT_LABELS:
        raise ValueError("Bu kanal kayıtlı bir destek bileti değil.")
    panel = await find_ticket_message(channel)
    if panel is None:
        # Eklenen üyelerin izinlerinden bilet sahibini tahmin etmeyiz.
        raise ValueError("Biletin ilk destek kartı bulunamadı. Bilet sahibi doğrulanamadı.")
    embed = panel.embeds[0]
    owner_match = re.search(r"<@!?(\d+)>", embed.description or "")
    number_match = re.search(r"#(\d+)", embed.title or "")
    fields = embed.fields[0].value if embed.fields else ""
    # Talep metni aynı etiketi içerebilir; gerçek yetkili satırı kartın sonundadır.
    claimer_value = fields.rpartition("**👤 Biletle İlgilenen Yetkili:**")[2].strip()
    claimer_match = re.match(r"<@!?(\d+)>", claimer_value)
    owner_id = int(owner_match.group(1)) if owner_match else None
    allowed = valid_support_roles(channel.guild, ticket_type)
    interventions = {}
    # Önce taramayı tamamla; bir Discord hatası olursa yarım eski-bilet kaydı oluşturma.
    async for message in channel.history(limit=None, oldest_first=True):
        if message.author.bot or message.author.id == owner_id:
            continue
        member = message.author if isinstance(message.author, discord.Member) else channel.guild.get_member(message.author.id)
        if member and message.author.id not in interventions and any(role.id in allowed for role in member.roles):
            interventions[message.author.id] = message.created_at
    stats_register_ticket(
        channel.guild.id, channel.id, ticket_type=ticket_type,
        ticket_number=int(number_match.group(1)) if number_match else 0,
        owner_id=owner_id, opened_at=channel.created_at, panel_message_id=panel.id,
        claimed_by=int(claimer_match.group(1)) if claimer_match else None,
    )
    for staff_id, first_message_at in interventions.items():
        stats_touch_ticket(channel.guild.id, channel.id, staff_id, at=first_message_at)
    return stats_get_ticket(channel.guild.id, channel.id)


async def recover_staff_interventions(channel, record, staff_ids):
    """Recover first messages sent during downtime before recording a new action."""
    if record.get("closed_at"):
        return
    guild_id, channel_id = channel.guild.id, channel.id
    wanted = {
        int(staff_id) for staff_id in staff_ids
        if staff_id and int(staff_id) != record.get("owner_id")
        and (guild_id, channel_id, int(staff_id)) not in _stats_history_recovered
    }
    if not wanted:
        return
    first_messages = {}
    async for message in channel.history(limit=None, oldest_first=True):
        if not message.author.bot and message.author.id in wanted:
            first_messages.setdefault(message.author.id, message.created_at)
            if len(first_messages) == len(wanted):
                break
    # Tamamlanmayan taramaları önbelleğe alma; bir sonraki işlem yeniden denesin.
    for staff_id, first_message_at in first_messages.items():
        stats_touch_ticket(guild_id, channel_id, staff_id, at=first_message_at)
    _stats_history_recovered.update((guild_id, channel_id, staff_id) for staff_id in wanted)


def readable_duration(seconds):
    if seconds is None:
        return "Henüz veri yok"
    seconds = max(0, round(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} gün")
    if hours:
        parts.append(f"{hours} saat")
    if minutes:
        parts.append(f"{minutes} dk")
    if seconds or not parts:
        parts.append(f"{seconds} sn")
    return " ".join(parts)


class RatingStarButton(discord.ui.Button):
    def __init__(self, guild_id, channel_id, score):
        super().__init__(
            label=f"{score} yıldız", emoji="⭐", style=discord.ButtonStyle.secondary,
            custom_id=f"ticket_rating:{guild_id}:{channel_id}:{score}",
        )
        self.score = score

    async def callback(self, interaction):
        await self.view.submit(interaction, self.score)


class TicketRatingView(discord.ui.View):
    def __init__(self, guild_id, channel_id):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.channel_id = str(channel_id)
        for score in range(1, 6):
            self.add_item(RatingStarButton(self.guild_id, self.channel_id, score))

    async def interaction_check(self, interaction):
        try:
            record = stats_get_ticket(self.guild_id, self.channel_id)
        except (OSError, ValueError, RuntimeError):
            _STATS_LOG.exception("Değerlendirme kaydı okunamadı")
            await interaction.response.send_message("❌ Değerlendirme kaydı okunamadı. Lütfen biraz sonra tekrar deneyin.", ephemeral=True)
            return False
        if record is None or record.get("owner_id") != interaction.user.id:
            await interaction.response.send_message("❌ Bu bileti yalnızca bilet sahibi değerlendirebilir.", ephemeral=True)
            return False
        return True

    async def submit(self, interaction, score):
        # Burada await öncesinde atomik yazılır; iki eşzamanlı tıklama çift puan oluşturamaz.
        try:
            result = stats_submit_rating(
                self.guild_id, self.channel_id, interaction.user.id, score,
                message_id=interaction.message.id if interaction.message else 0,
            )
        except (OSError, ValueError, RuntimeError):
            _STATS_LOG.exception("Değerlendirme kaydedilemedi")
            await interaction.response.send_message("❌ Puan kaydedilemedi. Lütfen biraz sonra tekrar deneyin.", ephemeral=True)
            return
        if result != "saved":
            messages = {
                "already_rated": "ℹ️ Bu bilet için daha önce puan verdiniz. Her bilet bir kez değerlendirilir.",
                "forbidden": "❌ Bu değerlendirme size ait değil.",
                "invalid": "❌ Lütfen 1 ile 5 arasında bir puan seçin.",
                "unavailable": "❌ Bu bilet için değerlendirme kaydı bulunamadı.",
            }
            await interaction.response.send_message(messages.get(result, "❌ Puan kaydedilemedi."), ephemeral=True)
            return
        record = stats_get_ticket(self.guild_id, self.channel_id)
        embed = discord.Embed(
            title="✅ Değerlendirmeniz Kaydedildi",
            description=(f"**{SUPPORT_LABELS.get(record['ticket_type'], 'Destek')} Bileti #{record['ticket_number']:04d}**\n\n"
                         f"Verdiğiniz puan: **{score}/5** {'⭐' * score}\n\nGeri bildiriminiz için teşekkür ederiz."),
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


async def send_ticket_rating(channel):
    record = stats_get_ticket(channel.guild.id, channel.id)
    if record is None or not record.get("closed_at"):
        return ""
    if record.get("rating_message_id") or record.get("rating_score") is not None:
        return ""
    if record.get("owner_id") is None:
        stats_set_delivery(channel.guild.id, channel.id, state="unavailable")
        return "Bilet sahibi bulunamadığından değerlendirme DM'i gönderilemedi."
    try:
        owner = bot.get_user(record["owner_id"]) or await bot.fetch_user(record["owner_id"])
        embed = discord.Embed(
            title=channel.guild.name,
            description=(f"**{SUPPORT_LABELS.get(record['ticket_type'], 'Destek')} Bileti #{record['ticket_number']:04d}**\n\n"
                         "Biletinizle yeterince iyi ilgilenildi mi?\n"
                         "Lütfen geri dönüş için aşağıdaki tuşlardan puanlayın."),
            color=discord.Color.gold(),
        )
        if channel.guild.icon:
            embed.set_thumbnail(url=channel.guild.icon.url)
        embed.set_footer(text="Kullanıcı Değerlendirmesi • 1 en düşük, 5 en yüksek puan")
        view = TicketRatingView(channel.guild.id, channel.id)
        message = await owner.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        stats_set_delivery(
            channel.guild.id, channel.id, state="sent", message_id=message.id, dm_channel_id=message.channel.id,
        )
        return "Bilet sahibine değerlendirme DM'i gönderildi."
    except discord.Forbidden:
        stats_set_delivery(channel.guild.id, channel.id, state="blocked")
        return "Kullanıcının DM'i kapalı; değerlendirme gönderilemedi. İstatistik kaydı korunuyor."
    except discord.HTTPException:
        stats_set_delivery(channel.guild.id, channel.id, state="failed")
        _STATS_LOG.exception("Değerlendirme DM'i gönderilemedi")
        return "Değerlendirme DM'i gönderilemedi. İstatistik kaydı korunuyor."


@bot.tree.command(name="yetkiliistatistik", description="Yetkilinin bilet ve kullanıcı değerlendirmesi istatistiklerini gösterir")
@app_commands.guild_only()
@app_commands.describe(yetkili="İstatistikleri gösterilecek yetkili; boş bırakırsanız kendi istatistikleriniz")
async def staff_statistics(interaction: discord.Interaction, yetkili: discord.Member = None):
    if not await command_role_check(interaction):
        return
    target = yetkili or interaction.user
    try:
        stats = stats_for_staff(interaction.guild.id, target.id)
    except (OSError, ValueError, RuntimeError):
        _STATS_LOG.exception("İstatistik okunamadı")
        await interaction.response.send_message("❌ İstatistik kayıtları okunamadı. ticket_stats.json dosyasını kontrol edin.", ephemeral=True)
        return
    rating = "Henüz değerlendirme yok" if stats["average_rating"] is None else f"⭐ **{stats['average_rating']:.2f}/5** ({stats['rating_count']} değerlendirme)"
    embed = discord.Embed(title="📊 Yetkili İstatistikleri", description=target.mention, color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="✅ Çözülen Bilet", value=str(stats["resolved_count"]), inline=True)
    embed.add_field(name="⏱️ Ortalama Müdahale Süresi", value=readable_duration(stats["average_response_seconds"]), inline=True)
    embed.add_field(name="⭐ Kullanıcı Değerlendirmesi", value=rating, inline=False)
    embed.set_footer(text=f"Kapanan biletler • Müdahale süresi: {stats['response_sample_count']} bilet üzerinden hesaplandı")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message):
    # commands.Bot'un olası metin komutları çalışmaya devam etsin.
    try:
        if message.guild is not None and not message.author.bot and isinstance(message.channel, discord.TextChannel):
            ticket_type, _ = get_ticket_info(message.channel)
            allowed = valid_support_roles(message.guild, ticket_type) if ticket_type in SUPPORT_LABELS else set()
            member = message.author if isinstance(message.author, discord.Member) else message.guild.get_member(message.author.id)
            if member and any(role.id in allowed for role in member.roles):
                async with ticket_operation_lock(message.channel):
                    record = await ensure_ticket_record(message.channel)
                    if not record.get("closed_at") and record.get("owner_id") != message.author.id:
                        await recover_staff_interventions(message.channel, record, [message.author.id])
                        stats_touch_ticket(message.guild.id, message.channel.id, message.author.id, at=message.created_at)
    except (OSError, ValueError, RuntimeError, discord.HTTPException):
        _STATS_LOG.exception("İlk yetkili mesajı istatistik kaydına yazılamadı")
    finally:
        await bot.process_commands(message)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _staff_action(self, interaction, *, status=None, claim=False):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message("❌ Bu işlemi yapmak için yetkiniz yok.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with ticket_operation_lock(interaction.channel):
                record = await ensure_ticket_record(interaction.channel)
                if record.get("closed_at"):
                    await interaction.followup.send("ℹ️ Bu bilet zaten kapatılmış.", ephemeral=True)
                    return
                await recover_staff_interventions(interaction.channel, record, [interaction.user.id])
                await update_ticket_panel(
                    interaction.channel, status=status,
                    claimer=interaction.user.mention if claim else None,
                )
                if claim:
                    stats_claim_ticket(interaction.guild.id, interaction.channel.id, interaction.user.id, at=interaction.created_at)
                else:
                    stats_touch_ticket(interaction.guild.id, interaction.channel.id, interaction.user.id, at=interaction.created_at)
            text = f"📌 {interaction.user.mention} bileti üstlendi." if claim else f"✅ Bilet durumu güncellendi: {status}"
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except (OSError, ValueError, RuntimeError, discord.HTTPException):
            _STATS_LOG.exception("Bilet işlemi tamamlanamadı")
            await interaction.followup.send("❌ Bilet kartı veya istatistik kaydı güncellenemedi. Kanal ve dosya izinlerini kontrol edin.", ephemeral=True)

    @discord.ui.button(label="⏳ Beklemede", style=discord.ButtonStyle.secondary, row=0, custom_id="ticket_wait")
    async def wait_status(self, interaction, button):
        await self._staff_action(interaction, status="⏳ Beklemede")

    @discord.ui.button(label="🔎 İnceleniyor", style=discord.ButtonStyle.primary, row=0, custom_id="ticket_review")
    async def reviewing_status(self, interaction, button):
        await self._staff_action(interaction, status="🔎 İnceleniyor")

    @discord.ui.button(label="✅ Çözüldü", style=discord.ButtonStyle.success, row=0, custom_id="ticket_solved")
    async def solved_status(self, interaction, button):
        # Kullanıcı tercihi: sayacı artıran işlem yalnızca başarılı bilet kapanışıdır.
        await self._staff_action(interaction, status="✅ Çözüldü")

    @discord.ui.button(label="🔒 Bileti Kapat", style=discord.ButtonStyle.danger, row=1, custom_id="ticket_close")
    async def close_ticket(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message("❌ Bu işlemi yapmak için yetkiniz yok.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        try:
            async with ticket_operation_lock(channel):
                record = await ensure_ticket_record(channel)
                if record.get("closed_at"):
                    await interaction.followup.send("ℹ️ Bu bilet zaten kapatılmış; ikinci kez sayılmadı.", ephemeral=True)
                    return
                await recover_staff_interventions(channel, record, [interaction.user.id, record.get("claimed_by")])
                stats_touch_ticket(interaction.guild.id, channel.id, interaction.user.id, at=interaction.created_at)
                settings = load_data().get(str(interaction.guild.id), {}).get("support_types", {}).get(record["ticket_type"], {})
                category = None
                if settings.get("closed_category"):
                    category = resolve_channel(interaction.guild, settings["closed_category"])
                    if not isinstance(category, discord.CategoryChannel):
                        await interaction.followup.send("❌ Kapalı bilet kategorisi bulunamadı. Bilet kapatılmadı; kurulum ayarlarını kontrol edin.", ephemeral=True)
                        return
                bot_member = interaction.guild.me
                if bot_member is None:
                    raise ValueError("Botun sunucu üyeliği bulunamadı.")
                bot_permissions = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    embed_links=True, attach_files=True,
                )
                # Orijinal kod bütün izinleri silince bot da konuşma kaydına erişemiyordu.
                await channel.set_permissions(bot_member, overwrite=bot_permissions)
                countdown = discord.Embed(title="🔒 Bilet Kapatılıyor", description="Ticket kapanıyor...\n\n⏳ **3**", color=discord.Color.orange())
                message = await channel.send(embed=countdown)
                for number in (2, 1):
                    await asyncio.sleep(1)
                    countdown.description = f"Ticket kapanıyor...\n\n⏳ **{number}**"
                    await message.edit(embed=countdown)
                await asyncio.sleep(1)
                log_warning = ""
                ticket_type, reason = get_ticket_info(channel)
                try:
                    await send_ticket_log(channel, ticket_type, reason, interaction.user)
                except discord.HTTPException:
                    _STATS_LOG.exception("Bilet konuşma kaydı log kanalına gönderilemedi")
                    log_warning = "Konuşma kaydı log kanalına gönderilemedi; kanal ve konuşmalar korunuyor."
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    bot_member: bot_permissions,
                }
                edit_args = {"overwrites": overwrites}
                if category is not None:
                    edit_args["category"] = category
                await channel.edit(**edit_args)
                # Başarısız bir Discord kapatma isteği çözülen bilet sayısını artırmaz.
                stats_close_ticket(interaction.guild.id, channel.id, interaction.user.id)
                warnings = [log_warning] if log_warning else []
                try:
                    await update_ticket_panel(channel, status="🔒 Kapalı")
                    panel = await find_ticket_message(channel, stats_get_ticket(interaction.guild.id, channel.id))
                    if panel:
                        disabled_view = CloseTicketView()
                        for child in disabled_view.children:
                            child.disabled = True
                        await panel.edit(view=disabled_view)
                    await message.edit(embed=discord.Embed(title="🔒 Bilet Kapatıldı", description="Ticket başarıyla kapatıldı.", color=discord.Color.red()))
                except (OSError, ValueError, RuntimeError, discord.HTTPException):
                    _STATS_LOG.exception("Kapanan biletin destek kartı güncellenemedi")
                    warnings.append("Bilet kapandı ancak destek kartı güncellenemedi.")
                try:
                    rating_notice = await send_ticket_rating(channel)
                    if rating_notice:
                        warnings.append(rating_notice)
                except (OSError, ValueError, RuntimeError, discord.HTTPException):
                    _STATS_LOG.exception("Kapanan biletin değerlendirmesi gönderilemedi")
                    warnings.append("Değerlendirme gönderilemedi; kapanış istatistiği kaydedildi.")
                result = "✅ Bilet kapatıldı ve yetkili istatistiğine kaydedildi."
                if warnings:
                    result += "\n" + "\n".join(warnings)
                await interaction.followup.send(result, ephemeral=True)
        except (OSError, ValueError, RuntimeError, discord.HTTPException):
            _STATS_LOG.exception("Bilet kapanışı veya istatistik kaydı tamamlanamadı")
            await interaction.followup.send(
                "❌ Kapatma veya istatistik kaydı tamamlanamadı. Kanalı ve ticket_stats.json dosyasının yazma iznini kontrol edin.", ephemeral=True,
            )

    @discord.ui.button(label="👥 Üyeleri Yönet", style=discord.ButtonStyle.secondary, row=1, custom_id="ticket_members")
    async def manage_members(self, interaction, button):
        if not await has_ticket_permission(interaction):
            await interaction.response.send_message("❌ Bu butonu kullanma yetkiniz yok.", ephemeral=True)
            return
        await interaction.response.send_modal(MemberAddModal())

    @discord.ui.button(label="📌 Bilet Üstlenme", style=discord.ButtonStyle.success, row=1, custom_id="ticket_claim")
    async def claim_ticket(self, interaction, button):
        await self._staff_action(interaction, claim=True)



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
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, attach_files=True,
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

    stats_register_ticket(
        guild.id, channel.id, ticket_type=ticket_type, ticket_number=ticket_no,
        owner_id=user.id, opened_at=channel.created_at,
    )
    ticket_message = await channel.send(
        content=role_mentions,
        embed=embed,
        view=CloseTicketView()
    )
    stats_set_panel_message(guild.id, channel.id, ticket_message.id)

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



if __name__ == "__main__":
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
