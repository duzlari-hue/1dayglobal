"""config.py — YouTube bot sozlamalari"""
import os
import pytz
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# YouTube
YOUTUBE_ENABLED  = os.getenv("YOUTUBE_ENABLED", "true").lower() == "true"
YOUTUBE_LOCAL    = os.getenv("YOUTUBE_LOCAL",   "false").lower() == "true"
YOUTUBE_PLAYLIST = {
    "uz": os.getenv("YOUTUBE_PLAYLIST_UZ", ""),
    "ru": os.getenv("YOUTUBE_PLAYLIST_RU", ""),
    "en": os.getenv("YOUTUBE_PLAYLIST_EN", ""),
}

# Vaqt
TASHKENT = pytz.timezone("Asia/Tashkent")

# Papkalar
QUEUE_DIR  = os.getenv("QUEUE_DIR",  "../TELEGRAM/output/youtube_queue")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output/videos")
TEMP_DIR   = os.getenv("TEMP_DIR",   "output/temp")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR,   exist_ok=True)
os.makedirs(QUEUE_DIR,  exist_ok=True)

# Video
VIDEO_W = 1280
VIDEO_H = 720
FPS     = 24

# ── Ovoz sozlamalari ──────────────────────────────────────────
# UZ: daraja bo'yicha ovoz tanlash
#   muhim (urush/siyosat) → Sardor (erkak) — jiddiy tembr
#   tezkor/xabar          → Madina (ayol)  — ravon, aniq
VOICES = {
    "uz": {
        # Standart (yangilik, xabar)
        "default": {
            "voice":  "uz-UZ-MadinaNeural",
            "rate":   "-8%",    # biroz sekin → aniqroq
            "pitch":  "+0Hz",
            "volume": "+20%",
        },
        # Muhim (siyosat, urush, falokat) — erkak ovozi
        "muhim": {
            "voice":  "uz-UZ-SardorNeural",
            "rate":   "-6%",    # unchalik sekin emas
            "pitch":  "+8Hz",   # tembr ko'tarilgan (g'alizlikni kamaytiradi)
            "volume": "+20%",
        },
    },
    "ru": {
        "default": {
            "voice":  "ru-RU-SvetlanaNeural",
            "rate":   "-3%",
            "pitch":  "+0Hz",
            "volume": "+15%",
        },
    },
    "en": {
        "default": {
            "voice":  "en-US-GuyNeural",
            "rate":   "-3%",
            "pitch":  "-10Hz",
            "volume": "+15%",
        },
    },
}

# ── Audio post-processing (ffmpeg) ───────────────────────────
# Broadcast ovoz effekti: EQ + kompressor + tozalash
AUDIO_FX = {
    "uz": (
        "highpass=f=80,"                          # Pastki shovqin
        "lowpass=f=9000,"                         # Yuqori shovqin
        "equalizer=f=250:width_type=o:width=2:g=-2,"   # Haddan tashqari bass -2dB
        "equalizer=f=2500:width_type=o:width=2:g=+3,"  # Nutq aniqlik +3dB
        "equalizer=f=6000:width_type=o:width=2:g=+1,"  # Havo, yuqori +1dB
        "acompressor=threshold=0.4:ratio=3:attack=5:release=80:makeup=1.5,"  # Kompressor
        "volume=1.1"                              # Qo'shimcha +1dB
    ),
    "ru": (
        "highpass=f=80,"
        "lowpass=f=10000,"
        "equalizer=f=300:width_type=o:width=2:g=-1,"
        "equalizer=f=3000:width_type=o:width=2:g=+2,"
        "acompressor=threshold=0.5:ratio=3:attack=5:release=80:makeup=1.3,"
        "volume=1.0"
    ),
    "en": (
        "highpass=f=80,"
        "lowpass=f=10000,"
        "equalizer=f=300:width_type=o:width=2:g=-1,"
        "equalizer=f=3000:width_type=o:width=2:g=+2,"
        "acompressor=threshold=0.5:ratio=3:attack=5:release=80:makeup=1.3,"
        "volume=1.0"
    ),
}

# YouTube auth
CLIENT_SECRETS = "client_secrets.json"
TOKEN_FILE     = "youtube_token.json"
SCOPES         = ["https://www.googleapis.com/auth/youtube.upload"]
