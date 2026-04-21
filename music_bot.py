import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
from collections import deque
from pathlib import Path

# ==============================
# CẤU HÌNH
# ==============================
import os
TOKEN = os.environ.get("DISCORD_TOKEN")       # Thay bằng token bot của bạn
LOCAL_MUSIC_DIR = "./music"         # Thư mục chứa file MP3 local

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==============================
# TRẠNG THÁI MỖI SERVER
# ==============================
queues: dict[int, deque] = {}
current: dict[int, dict] = {}       # {"title": ..., "source": ..., "url": ...}

# ==============================
# YT-DLP OPTIONS (YouTube + SoundCloud)
# ==============================
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

FFMPEG_LOCAL_OPTIONS = {
    "options": "-vn",
}

SOURCE_ICON = {
    "youtube":     "🎬",
    "soundcloud":  "🔶",
    "local":       "💾",
}

# ==============================
# NHẬN DIỆN NGUỒN NHẠC
# ==============================
def detect_source(query: str) -> str:
    """Trả về: 'local' | 'soundcloud' | 'youtube'"""
    q = query.strip()
    if q.endswith((".mp3", ".wav", ".flac", ".ogg")) or os.path.exists(q):
        return "local"
    if "soundcloud.com" in q:
        return "soundcloud"
    return "youtube"


async def fetch_audio(query: str, source: str) -> tuple[str, str, str]:
    """Trả về (title, audio_url_or_path, source)."""
    if source == "local":
        path = Path(query)
        if not path.exists():
            path = Path(LOCAL_MUSIC_DIR) / query
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: `{query}`")
        return path.stem, str(path.resolve()), "local"

    # YouTube hoặc SoundCloud — yt-dlp xử lý cả hai
    loop = asyncio.get_event_loop()
    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info["title"], info["url"]

    title, url = await loop.run_in_executor(None, _extract)
    return title, url, source


# ==============================
# PHÁT NHẠC
# ==============================
def get_queue(guild_id: int) -> deque:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def play_next(guild_id: int, voice_client: discord.VoiceClient):
    queue = get_queue(guild_id)
    if not queue:
        current.pop(guild_id, None)
        return

    item = queue.popleft()
    current[guild_id] = item

    opts = FFMPEG_LOCAL_OPTIONS if item["source"] == "local" else FFMPEG_OPTIONS
    audio = discord.FFmpegPCMAudio(item["url"], **opts)
    voice_client.play(
        audio,
        after=lambda e: play_next(guild_id, voice_client) if not e else None,
    )


# ==============================
# EVENTS
# ==============================
@bot.event
async def on_ready():
    await bot.tree.sync()
    os.makedirs(LOCAL_MUSIC_DIR, exist_ok=True)
    print(f"✅ Bot online: {bot.user}")
    print(f"📁 Nhạc local: {os.path.abspath(LOCAL_MUSIC_DIR)}")


# ==============================
# /play — nhận diện tự động nguồn
# ==============================
@bot.tree.command(name="play", description="Phát nhạc: YouTube, SoundCloud, hoặc tên file MP3 local")
@app_commands.describe(query="Tên bài / URL YouTube / URL SoundCloud / tên file MP3")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Vào voice channel trước!", ephemeral=True)

    await interaction.response.defer()

    vc = interaction.guild.voice_client
    voice_channel = interaction.user.voice.channel

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    source_type = detect_source(query)

    try:
        title, url, source = await fetch_audio(query, source_type)
    except FileNotFoundError as e:
        return await interaction.followup.send(f"❌ {e}")
    except Exception as e:
        return await interaction.followup.send(f"❌ Lỗi tải nhạc: `{e}`")

    item = {"title": title, "url": url, "source": source}
    queue = get_queue(interaction.guild_id)
    icon = SOURCE_ICON[source]

    if vc.is_playing() or vc.is_paused():
        queue.append(item)
        await interaction.followup.send(f"➕ {icon} Thêm vào hàng đợi: **{title}**")
    else:
        queue.append(item)
        play_next(interaction.guild_id, vc)
        await interaction.followup.send(f"▶️ {icon} Đang phát: **{title}**")


# ==============================
# /local — xem danh sách file nhạc
# ==============================
@bot.tree.command(name="local", description="Xem danh sách file nhạc local")
async def local_list(interaction: discord.Interaction):
    music_dir = Path(LOCAL_MUSIC_DIR)
    files = sorted([
        f.name for f in music_dir.iterdir()
        if f.suffix.lower() in (".mp3", ".wav", ".flac", ".ogg")
    ])

    if not files:
        return await interaction.response.send_message(
            f"📭 Thư mục `{LOCAL_MUSIC_DIR}` trống. Hãy thêm file nhạc vào đó.",
            ephemeral=True,
        )

    lines = [f"💾 **Nhạc local** (`{LOCAL_MUSIC_DIR}`):"]
    for i, f in enumerate(files, 1):
        lines.append(f"  {i}. `{f}`")
    lines.append("\n💡 Dùng `/play <tên file>` để phát.")
    await interaction.response.send_message("\n".join(lines))


# ==============================
# Các lệnh điều khiển
# ==============================
@bot.tree.command(name="skip", description="Bỏ qua bài đang phát")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Đã bỏ qua.")
    else:
        await interaction.response.send_message("❌ Không có bài nào đang phát.", ephemeral=True)


@bot.tree.command(name="pause", description="Tạm dừng nhạc")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Đã tạm dừng.")
    else:
        await interaction.response.send_message("❌ Không có gì đang phát.", ephemeral=True)


@bot.tree.command(name="resume", description="Tiếp tục phát nhạc")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Tiếp tục phát.")
    else:
        await interaction.response.send_message("❌ Nhạc không bị tạm dừng.", ephemeral=True)


@bot.tree.command(name="stop", description="Dừng nhạc và thoát kênh")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        get_queue(interaction.guild_id).clear()
        current.pop(interaction.guild_id, None)
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Đã dừng và thoát.")
    else:
        await interaction.response.send_message("❌ Bot chưa ở trong kênh nào.", ephemeral=True)


@bot.tree.command(name="queue", description="Xem danh sách hàng đợi")
async def queue_cmd(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    queue = get_queue(guild_id)
    now = current.get(guild_id)

    if not now and not queue:
        return await interaction.response.send_message("📭 Hàng đợi trống.", ephemeral=True)

    lines = []
    if now:
        icon = SOURCE_ICON[now["source"]]
        lines.append(f"{icon} **Đang phát:** {now['title']}")
    if queue:
        lines.append("**Hàng đợi:**")
        for i, item in enumerate(queue, 1):
            icon = SOURCE_ICON[item["source"]]
            lines.append(f"  {i}. {icon} {item['title']}")

    await interaction.response.send_message("\n".join(lines))


# ==============================
# CHẠY BOT
# ==============================
bot.run(TOKEN)
