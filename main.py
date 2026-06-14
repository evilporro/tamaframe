# main.py — Tamaframe Bot (Red Ribbon Imperium)
# Standalone community tamagotchi bot.
# Separated from General White — changes here do not affect White.
#
# Requirements (requirements.txt):
#   discord.py>=2.3
#   python-dotenv
#
# Environment variables:
#   TAMAGOTCHI_TOKEN  — bot token
#   SUPABASE_URL      — Supabase project URL
#   SUPABASE_KEY      — Supabase anon/service key

import os
import asyncio
import json
import re
import threading
import traceback
import random
from datetime import datetime, timezone, timedelta
from io import BytesIO

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------
# CONFIG
# ---------------------------
GUILD_ID = 500037407652052992
TOKEN    = os.getenv("TAMAGOTCHI_TOKEN")

# Role IDs
WARLORD_ROLE_ID  = 968733879206826024
EMISSARY_ROLE_ID = 972984081853845514
GENERAL_ROLE_ID  = 1126295579132178502
OFFICER_ROLE_ID  = 500059033538134046
RECRUITER_ROLE_ID = 988489162917281852
GOLD_STAR_ROLE_ID = 1288581355491299434

MOD_ROLE_IDS = {
    OFFICER_ROLE_ID, GENERAL_ROLE_ID, EMISSARY_ROLE_ID, WARLORD_ROLE_ID,
}

MOD_COMMAND_CHANNEL_IDS = {
    993971190093840434,   # Leader Chat
    1240135071474647071,  # Bot Experiment Room
}

TAMA_CATEGORY_ID            = 968939474224566295
CONTROL_CENTER_CATEGORY_ID  = 993970977610399874

TAMA_GOLD_STAR_ROLE_ID    = GOLD_STAR_ROLE_ID
TAMA_SOLDIER_ROLE_ID      = 502614463816400906
TAMA_CRIMSON_SOUL_ROLE_ID = 1270588563985141864
TAMA_ASSOCIATE_ROLE_ID    = 982009990619467816
TAMA_ALLIED_ROLE_ID       = 975532866001829928

TAMA_MEMBER_ROLES = {
    GOLD_STAR_ROLE_ID,
    TAMA_SOLDIER_ROLE_ID,
    TAMA_CRIMSON_SOUL_ROLE_ID,
    RECRUITER_ROLE_ID,
    OFFICER_ROLE_ID, GENERAL_ROLE_ID, EMISSARY_ROLE_ID, WARLORD_ROLE_ID,
}

ALLOWED_ROLES    = MOD_ROLE_IDS
ALLOWED_CHANNELS = MOD_COMMAND_CHANNEL_IDS

# ---------------------------
# SUPABASE (tama log only)
# ---------------------------
_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

def _supa_headers(prefer: str = "resolution=merge-duplicates,return=minimal"):
    return {
        "apikey":        _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        prefer,
    }

def _supa_request(method: str, path: str, data=None):
    import urllib.request, urllib.error
    url  = f"{_SUPABASE_URL}/rest/v1/{path}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req  = urllib.request.Request(url, data=body, headers=_supa_headers(), method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        print(f"[Tamaframe] Supabase {method} {path} failed: {e.code} {e.read()}")
        return None
    except Exception as e:
        print(f"[Tamaframe] Supabase error: {e}")
        return None

# ---------------------------
# TAMA ACTIVITY LOGGER
# Logs to Supabase tama_log table in a daemon thread.
# Never raises — logging must not crash gameplay.
# ---------------------------
def log_tama_event(
    event_type: str,
    *,
    user_id: int = None,
    action:  str = None,
    detail:  str = None,
):
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return
    row = {
        "session_id": tama.get("session_id", "unknown"),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "user_id":    str(user_id) if user_id else None,
        "action":     action,
        "stage":      tama.get("stage", "unknown"),
        "level":      int(tama.get("level", 0)),
        "feed":       int(tama.get("feed",       0)),
        "clean":      int(tama.get("clean",      0)),
        "focus":      int(tama.get("level_stat", 0)),
        "rest":       int(tama.get("rest",       0)),
        "detail":     detail,
    }
    def _push():
        try:
            _supa_request("POST", "tama_log", row)
        except Exception:
            pass
    threading.Thread(target=_push, daemon=True).start()

# ---------------------------
# PERMISSION HELPERS
# ---------------------------
def has_roles(member: discord.Member, role_set: set) -> bool:
    return any(r.id in role_set for r in member.roles) or member.guild_permissions.administrator

def is_warlord(member: discord.Member) -> bool:
    return any(r.id == WARLORD_ROLE_ID for r in member.roles) or member.guild_permissions.administrator

def is_mod(member: discord.Member) -> bool:
    return has_roles(member, MOD_ROLE_IDS)

def is_tama_member(member: discord.Member) -> bool:
    return has_roles(member, TAMA_MEMBER_ROLES)

def is_gold_star_eligible(member: discord.Member) -> bool:
    return has_roles(member, {GOLD_STAR_ROLE_ID, OFFICER_ROLE_ID, GENERAL_ROLE_ID, EMISSARY_ROLE_ID, WARLORD_ROLE_ID})

# ---------------------------
# BOT
# ---------------------------
intents          = discord.Intents.default()
intents.members  = True
intents.messages = True
bot      = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)


# ---------------------------
# BALANCE CONFIG
# Edit tama_config.py to change balance values.
# ---------------------------
from tama_config import (
    GLOBAL_COOLDOWN_MINUTES, DECAY_INTERVAL, DECAY_BASE,
    DECAY_SLEEP_FEED_MULT, DECAY_SLEEP_CLEAN_MULT, DECAY_SLEEP_REST_RECOVERY,
    DECAY_CORRUPTED_MULT, DECAY_TRIPLE_MULT, DECAY_TRIPLE_DURATION_MIN,
    DECAY_REST_LOW_THRESHOLD, DECAY_REST_MID_THRESHOLD,
    DECAY_REST_LOW_MULT, DECAY_REST_MID_MULT,
    RELIC_CLICK_THRESHOLD, RELIC_REACTANTS_NEEDED, ACTION_THRESHOLD,
    FEED_GAIN, FEED_CLEAN_COST, FEED_OVERFED_THRESHOLD, FEED_LOCK_DURATION_MIN,
    CLEAN_GAIN, CLEAN_REST_COST, CLEAN_OVER_THRESHOLD,
    TRAIN_STAT_GAIN, TRAIN_FEED_COST, TRAIN_CLEAN_COST, TRAIN_REST_COST,
    TRAIN_OVERTRAIN_STREAK, TRAIN_EVO_XP, FEED_EVO_XP,
    TRAIN_BLOCK_REST_THRESHOLD,
    TRAIN_REST_TIER_LOW, TRAIN_REST_TIER_MID, TRAIN_REST_TIER_HIGH,
    TRAIN_REST_COST_HIGH, TRAIN_REST_COST_MID, TRAIN_REST_COST_LOW,
    TRAIN_REST_XP_MULT_FULL, TRAIN_REST_XP_MULT_HIGH,
    TRAIN_REST_XP_MULT_MID, TRAIN_REST_XP_MULT_LOW,
    HAPPINESS_THRIVING, HAPPINESS_CONTENT, HAPPINESS_STRUGGLING, HAPPINESS_SUFFERING,
    SLEEP_DURATION_SECONDS, SLEEP_MIN_BEFORE_WAKE, SLEEP_VOTE_TIMEOUT,
    SLEEP_YES_NEEDED, SLEEP_NO_NEEDED, WAKE_YES_NEEDED, WAKE_NO_NEEDED,
    WAKE_EARLY_STAT_PENALTY,
    SICK_THRESHOLD, INFECTION_WARN_DURATION_SEC, INFECTION_IMMUNITY_MINUTES,
    ROLLING_GUARD_CLICKS_NEEDED, ROLLING_GUARD_WINDOW_MIN,
    ROLLING_GUARD_FAIL_STAT_HIT, ROLLING_GUARD_SUCCESS_BONUS,
    INFECTION_DECAY_PER_MIN, INFECTION_DECAY_CAP_MULT,
    INFECTION_PENALTY_DURATION_MIN, ACTION_THRESHOLD_POST_INFECTION,
    GAIN_VARIANCE, NOISE_CHANCE, NOISE_REST_THRESHOLD, NOISE_CLEAN_THRESHOLD,
    FEED_BACKFIRE_CLEAN_COST, OVERFEED_XP_MULT, IMMUNITY_GAIN_MULT,
    CONSECUTIVE_SAME_PENALTY,
    DEATH_STAT_ZEROS_NEEDED, DEATH_STAT_ZERO_DURATION_SEC,
    EVENT_CATALYST_CLICKS, EVENT_FORMA_CLICKS, EVENT_AFFINITY_CLICKS,
    EVENT_CORRUPTED_CLICKS, EVENT_ROLLING_GUARD_CLICKS,
    EVENT_CORRUPTED_FAIL_HIT, EVENT_CATALYST_DURATION_MIN,
    EVENT_FISSURE_DURATION_MIN, EVENT_CATALYST_WINDOW_MIN, EVENT_FORMA_WINDOW_MIN,
    EVENT_AFFINITY_WINDOW_MIN, EVENT_CORRUPTED_GRACE_MIN, EVENT_CORRUPTED_WINDOW_MIN,
    PITY_BASE_CHANCE, PITY_INCREMENT, PITY_MIN_INTERVAL, EVENT_COOLDOWN,
    STAT_MAX, LEVEL_MAX,
    WARFRAME_XP_PER_LEVEL, PRIME_XP_PER_LEVEL,
    FORMA_XP_MULT_BONUS, FORMA_XP_MULT_MAX, AFFINITY_BOOST_XP,
)

# ---------------------------
# SPRITE SOURCE
# Fetched fresh on startup and every 12h so CDN URLs never expire.
# S dict below is the fallback if fetch fails.
# ---------------------------
with open(os.path.join(os.path.dirname(__file__), "sprites.json"), encoding="utf-8") as _f:
    S = json.load(_f)

# Warframe roster — paired (base, prime) tuples
# Base can be None if only prime is available
# Add new warframes here — no other code changes needed
WARFRAME_ROSTER = [
    ("mag",    "mag_prime"),
    ("harrow", "harrow_prime"),
    ("saryn",  "saryn_prime"),
    ("yareli", "yareli_prime"),
]

# Action sprite override durations (seconds) per warframe
# Format: { warframe_prefix: { action: seconds } }
# Falls back to DEFAULT if not specified
SPRITE_OVERRIDE_DURATIONS = {
    "DEFAULT": {
        "feed":          5,
        "clean":         6,
        "train":         7,
        "rolling_guard": 6,
    },
    "mag": {
        "feed":          5,
        "clean":         6,
        "train":         5,
        "rolling_guard": 5,
    },
    "mag_prime": {
        "feed":          5,
        "clean":         6,
        "train":         8,
        "rolling_guard": 6,
    },
    "harrow": {
        "feed":          5,
        "clean":         6,
        "train":         7,
        "rolling_guard": 6,
    },
    "harrow_prime": {
        "feed":          5,
        "clean":         6,
        "train":         8,
        "rolling_guard": 7,
    },
    "saryn": {
        "feed":          5,
        "clean":         6,
        "train":         6,
        "rolling_guard": 6,
    },
    "saryn_prime": {
        "feed":          5,
        "clean":         6,
        "train":         6,
        "rolling_guard": 6,
    },
    "yareli": {
        "feed":          5,
        "clean":         6,
        "train":         6,
        "rolling_guard": 6,
    },
    "yareli_prime": {
        "feed":          5,
        "clean":         6,
        "train":         7,
        "rolling_guard": 6,
    },
}

# Evolution conditions per warframe
# None = no special condition, level 30 is enough
# Each condition has:
#   type:        internal identifier
#   tracker:     key in tama state that tracks progress
#   threshold:   value needed to unlock
#   met:         whether condition is currently satisfied
#   description: shown to players when evolution is blocked
WARFRAME_EVOLUTION_CONDITIONS = {
    "mag":    None,
    "harrow": {
        "type":        "low_stats_at_max",
        "tracker":     "harrow_condition_met",
        "threshold":   1,
        "description": "Harrow must reach Level 30 with Feed, Clean and Rest all at or below 30%, while Focus is at 100% — he gives everything but his conviction.",
        "hint":        "Reflect on what Harrow stands for. Penance demands total sacrifice — but faith must remain unbroken.",
    },
    "saryn": {
        "type":        "fully_infected",
        "tracker":     "saryn_survived_infection",
        "threshold":   1,
        "description": "Saryn must become fully infected with Technocyte at Level 30 — she transcends through the corruption itself.",
        "hint":        "Saryn has always had a complicated relationship with decay. Perhaps she needs to embrace it.",
    },
    "yareli": {
        "type":        "all_stats_thriving",
        "tracker":     "yareli_prime_condition_met",
        "threshold":   1,
        "description": "Yareli must reach Level 30 with all four stats at or above 80% simultaneously.",
        "hint":        "Yareli thrives in joy. She won't ascend until every part of her is flourishing. No weak links, no shortcuts.",
    },
}

PRIME_EVOLUTION_CONDITIONS = {
    "mag_prime":    None,
    "harrow_prime": None,
    "saryn_prime":  None,
    "yareli_prime": None,
}

# ---------------------------
# GAME STATE
# ---------------------------
def rand_stat():
    return random.randint(45, 75)

def fresh_state(preserve_leaderboard=False):
    lb = tama["leaderboard"] if preserve_leaderboard else {}
    return {
        "active": False,
        "session_id": None,
        "session_start_time": None,
        "stage": "relic",
        "dead": False,
        "paused": False,
        "completed": False,
        "message_id": None,
        "channel_id": None,
        "reactant_progress": 0,
        "reactants": 0,
        "feed": 70, "clean": 70, "level_stat": 70, "rest": 70,
        "level": 0,
        "evo_xp": 0,
        "infected": False,
        "infection_warned": False,
        "infection_warn_message_id": None,
        "infection_immunity_until": None,
        "rolling_guard_clicks": 0,
        "rolling_guard_message_id": None,
        "rolling_guard_expires": None,
        "decay_triple_until": None,
        "sleeping": False,
        "sleep_started": None,
        "sleep_streak": 0,
        "groggy_until": None,
        "sleep_message_id": None,
        "relic_cracking": False,
        "wake_yes": set(),
        "wake_no": set(),
        "sleep_vote_yes": set(),
        "sleep_vote_no": set(),
        "sleep_vote_message_id": None,
        "wake_vote_message_id": None,
        "sleep_vote_started": None,
        "consecutive_trains": 0,
        "last_action": None,
        "overfed_until": None,
        "overclean_debuff": False,
        "active_event": None,
        "active_event_id": -1,
        "event_message_id": None,
        "event_clicks": 0,
        "event_expires": None,
        "event_modifier_until": None,
        "event_modifier": None,
        "last_event_time": None,
        "ticks_since_event": 0,
        "current_pity_chance": PITY_BASE_CHANCE,
        "corrupted_warned": False,
        "corrupted_warn_message_id": None,
        "corruption_lingering_until": None,
        "leaderboard": lb,
        "cooldowns": {},
        "action_progress": {"feed": 0, "clean": 0, "train": 0, "reactant": 0},
        "sprite_override": None,
        "sprite_override_until": None,
        "prime_xp_multiplier": 1.0,
        "decay_multiplier": 1.0,
        "feed_zero_since":  None,
        "clean_zero_since": None,
        "rest_zero_since":  None,
        # Evolution condition trackers — reset each session
        "harrow_condition_met":    False,
        "saryn_survived_infection": False,
        "yareli_prime_condition_met": False,
        "infection_count":         0,
        "infection_penalty_until": None,
        "consecutive_feeds":       0,
        "consecutive_cleans":      0,
        "evolution_blocked":       False,
        "evolution_blocked_msg_id": None,
    }

tama = {
    "active": False,
    "session_id": None, "session_start_time": None, "stage": "relic", "dead": False, "paused": False,
    "completed": False, "message_id": None, "channel_id": None,
    "reactant_progress": 0, "reactants": 0, "relic_cracking": False,
    "feed": 70, "clean": 70, "level_stat": 70, "rest": 70,
    "level": 0, "evo_xp": 0,
    "infected": False, "infection_warned": False,
    "infection_warn_message_id": None,
    "infection_immunity_until": None,
    "rolling_guard_clicks": 0, "rolling_guard_message_id": None,
    "rolling_guard_expires": None, "decay_triple_until": None,
    "sleeping": False, "sleep_started": None, "sleep_streak": 0, "groggy_until": None,
    "wake_yes": set(), "wake_no": set(),
    "sleep_vote_yes": set(), "sleep_vote_no": set(),
    "sleep_vote_message_id": None, "wake_vote_message_id": None, "sleep_message_id": None,
    "sleep_vote_started": None,
    "consecutive_trains": 0, "last_action": None,
    "overfed_until": None, "overclean_debuff": False,
    "active_event": None, "active_event_id": -1,
    "event_message_id": None, "event_clicks": 0,
    "event_expires": None, "event_modifier_until": None,
    "event_modifier": None, "last_event_time": None,
    "ticks_since_event": 0, "current_pity_chance": PITY_BASE_CHANCE,
    "corrupted_warned": False, "corrupted_warn_message_id": None, "corruption_lingering_until": None,
    "leaderboard": {}, "cooldowns": {},
    "action_progress": {"feed": 0, "clean": 0, "train": 0, "reactant": 0},
    "sprite_override": None, "sprite_override_until": None,
    "prime_xp_multiplier": 1.0,
    "decay_multiplier": 1.0,
    "feed_zero_since":  None,
    "clean_zero_since": None,
    "rest_zero_since":  None,
    "yareli_prime_condition_met": False,
    "infection_count": 0, "infection_penalty_until": None,
    "consecutive_feeds": 0, "consecutive_cleans": 0,
}

STAT_KEYS = ["feed", "clean", "level_stat", "rest"]

# Stage event restrictions
STAGE_EVENTS = {
    "relic":    {"fissure"},
    "warframe": {"catalyst", "forma", "affinity", "corrupted"},
    "prime":    {"catalyst", "forma", "affinity", "corrupted"},
}

# ---------------------------
# HELPERS
# ---------------------------
# Warframe display names and pronouns
_wf_display_raw = json.load(open(os.path.join(os.path.dirname(__file__), "warframe_display.json"), encoding="utf-8"))
WARFRAME_DISPLAY = {k: tuple(v) for k, v in _wf_display_raw.items()}

def wf_name() -> str:
    """Display name of the current warframe based on stage."""
    stage = tama.get("stage", "relic")
    if stage == "prime":
        key = tama.get("current_prime", "mag_prime")
    else:
        key = tama.get("current_warframe", "mag")
    return WARFRAME_DISPLAY.get(key, ("Mag", "she", "her", "her"))[0]

def wf_pronoun() -> str:
    """Subject pronoun (he/she)."""
    stage = tama.get("stage", "relic")
    key   = tama.get("current_prime", "mag_prime") if stage == "prime" else tama.get("current_warframe", "mag")
    return WARFRAME_DISPLAY.get(key, ("Mag", "she", "her", "her"))[1]

def wf_obj() -> str:
    """Object pronoun (him/her)."""
    stage = tama.get("stage", "relic")
    key   = tama.get("current_prime", "mag_prime") if stage == "prime" else tama.get("current_warframe", "mag")
    return WARFRAME_DISPLAY.get(key, ("Mag", "she", "her", "her"))[2]

def wf_pos() -> str:
    """Possessive pronoun (his/her)."""
    stage = tama.get("stage", "relic")
    key   = tama.get("current_prime", "mag_prime") if stage == "prime" else tama.get("current_warframe", "mag")
    return WARFRAME_DISPLAY.get(key, ("Mag", "she", "her", "her"))[3]

def get_happiness_label():
    avg = sum(tama[k] for k in STAT_KEYS) / 4
    if avg >= 80: return "😄 Thriving"
    if avg >= 60: return "🙂 Content"
    if avg >= 40: return "😐 Struggling"
    if avg >= 20: return "😟 Suffering"
    return "😵 Critical"

def get_happiness_multiplier():
    avg = sum(tama[k] for k in STAT_KEYS) / 4
    if avg >= 80: return 1.5
    if avg >= 60: return 1.0
    if avg >= 40: return 0.75
    if avg >= 20: return 0.5
    return 0.0

def get_xp_needed():
    """XP scales with level — easy early (can multi-level per train), ~5 trains at level 29."""
    level = tama["level"]
    if tama["stage"] == "warframe":
        base = WARFRAME_XP_PER_LEVEL
    else:
        base = PRIME_XP_PER_LEVEL
    return base

def get_tama_channel(guild):
    cid = tama.get("channel_id")
    return guild.get_channel(cid) if cid else None

def get_sprite():
    stage  = tama["stage"]
    wf     = tama.get("current_warframe", "mag")
    prime  = tama.get("current_prime", "mag_prime")
    prefix = wf if stage == "warframe" else (prime if stage == "prime" else "relic")

    # Death and completion always win
    if tama["dead"]:
        return S.get(f"{prefix}_death", S.get(f"{wf}_death", S.get("mag_death", "")))
    if tama["completed"]:
        return S.get(f"{prefix}_idle", S.get(f"{wf}_idle", ""))

    # Relic stage
    if stage == "relic":
        if tama.get("relic_cracking"):
            return S.get("relic_crack", S["relic_idle"])
        if tama["active_event"] == "fissure":
            return S.get("relic_fissure", S["relic_idle"])
        return S["relic_idle"]

    # Persistent states — always win over action animations
    if tama["infected"]:
        return S.get(f"{prefix}_sick", S.get(f"{wf}_sick", ""))
    if tama["active_event"] == "corrupted" and not tama.get("corrupted_warned"):
        sprite = S.get(f"{prefix}_corruption") or S.get(f"{wf}_corruption")
        if sprite:
            return sprite
    if tama["sleeping"]:
        return S.get(f"{prefix}_sleep", S.get(f"{wf}_sleep", ""))

    # Timed action override — only shows during idle
    if tama["sprite_override"] and tama["sprite_override_until"]:
        if datetime.now(timezone.utc) < tama["sprite_override_until"]:
            key = f"{prefix}_{tama['sprite_override']}"
            return S.get(key, S.get(f"{prefix}_idle", ""))

    # Weighted idle rotation
    idle_main = S.get(f"{prefix}_idle")
    idle_walk = S.get(f"{prefix}_idle_walk")
    idle_jump = S.get(f"{prefix}_idle_jump")

    weighted_pool = []
    if idle_main:  weighted_pool += [idle_main] * 6
    if idle_walk:  weighted_pool += [idle_walk] * 2
    if idle_jump:  weighted_pool += [idle_jump] * 2

    if weighted_pool:
        chosen = random.choice(weighted_pool)
        sep = "&" if "?" in chosen else "?"
        return f"{chosen}{sep}t={int(datetime.now(timezone.utc).timestamp())}"

    return S.get(f"{prefix}_idle", "")

def get_color():
    if tama["dead"]:      return discord.Color.dark_gray()
    if tama["completed"]: return discord.Color.gold()
    if tama["infected"]:  return discord.Color.from_rgb(0, 180, 80)
    avg = sum(tama[k] for k in STAT_KEYS) / 4
    if avg >= 80: return discord.Color.green()
    if avg >= 60: return discord.Color.yellow()
    if avg >= 40: return discord.Color.orange()
    return discord.Color.red()

def bar(val, max_val=100, length=12):
    filled = round((val / max_val) * length)
    return "█" * filled + "░" * (length - filled)

def progress_bar(current, total, length=12):
    filled = round((current / total) * length) if total > 0 else 0
    return "█" * filled + "░" * (length - filled)

def build_leaderboard_text():
    def score(e): return e["clicks"]
    entries = sorted(tama["leaderboard"].values(), key=score, reverse=True)[:5]
    if not entries:
        return "No contributions yet."
    lines = []
    medals = ["🥇", "🥈", "🥉", "4.", "5."]
    for i, e in enumerate(entries):
        lines.append(f"{medals[i]} {e['name']:<16} {score(e)} pts")
    return "```\n" + "\n".join(lines) + "\n```"

def action_threshold():
    base = ACTION_THRESHOLD
    if tama["infected"]:
        base *= 2
    elif tama.get("infection_penalty_until") and datetime.now(timezone.utc) < tama["infection_penalty_until"]:
        base = ACTION_THRESHOLD_POST_INFECTION
    return base

def tama_is_mod(member: discord.Member) -> bool:
    return any(r.id in MOD_ROLE_IDS for r in member.roles) or member.guild_permissions.administrator

def tama_is_warlord(member: discord.Member) -> bool:
    return any(r.id == WARLORD_ROLE_ID for r in member.roles) or member.guild_permissions.administrator

def tama_mod_channel_ok(interaction: discord.Interaction) -> bool:
    return interaction.channel_id in MOD_COMMAND_CHANNEL_IDS

def tama_member_channel_ok(interaction: discord.Interaction) -> bool:
    """True if used in the tamaframe channel only."""
    allowed = set()
    if tama.get("channel_id"):
        allowed = {tama["channel_id"]}
    return interaction.channel_id in allowed

def has_roles(member: discord.Member, role_set: set) -> bool:
    return any(r.id in role_set for r in member.roles) or member.guild_permissions.administrator

def is_warlord(member: discord.Member) -> bool:
    return any(r.id == WARLORD_ROLE_ID for r in member.roles) or member.guild_permissions.administrator

def is_mod(member: discord.Member) -> bool:
    return has_roles(member, MOD_ROLES)

def is_senior_mod(member: discord.Member) -> bool:
    return has_roles(member, SENIOR_MOD_ROLES)

def is_tama_member(member: discord.Member) -> bool:
    return has_roles(member, TAMA_MEMBER_ROLES)

def is_gold_star_eligible(member: discord.Member) -> bool:
    return has_roles(member, GOLD_STAR_ELIGIBLE)

def reset_leaderboard():
    tama["leaderboard"] = {}

def build_embed():
    happiness = get_happiness_label()
    if tama["dead"]:
        title = "💀 THE COMPANION HAS FALLEN"
    elif tama["completed"]:
        title = f"✨ {wf_name()} — MAXIMUM POWER ACHIEVED! 🎖️"
    elif tama["stage"] == "relic":
        title = "🥚 RELIC — Gathering Reactants"
    elif tama["stage"] == "warframe":
        title = f"⚔️ {wf_name()} — Level {tama['level']}/{LEVEL_MAX} — {happiness}"
    else:
        title = f"✨ {wf_name()} — Level {tama['level']}/{LEVEL_MAX} — {happiness}"

    embed = discord.Embed(title=title, color=get_color())
    embed.set_image(url=get_sprite())

    if tama["stage"] == "relic":
        embed.add_field(
            name="⚗️ Relic Progress",
            value=(
                f"```\n"
                f"Reactants  {progress_bar(tama['reactants'], RELIC_REACTANTS_NEEDED)} {tama['reactants']}/{RELIC_REACTANTS_NEEDED}\n"
                f"Gathering  {progress_bar(tama['action_progress']['reactant'], RELIC_CLICK_THRESHOLD)} {tama['action_progress']['reactant']}/{RELIC_CLICK_THRESHOLD} clicks\n"
                f"```"
            ), inline=False
        )
    elif not tama["dead"] and not tama["completed"]:
        thresh  = action_threshold()
        stats_text = (
            f"🍖 Feed      {bar(tama['feed'])}  {tama['feed']:>3}%  [{tama['action_progress']['feed']}/{thresh}]\n"
            f"🧼 Clean     {bar(tama['clean'])}  {tama['clean']:>3}%  [{tama['action_progress']['clean']}/{thresh}]\n"
            f"⚡ Focus     {bar(tama['level_stat'])}  {tama['level_stat']:>3}%  [{tama['action_progress']['train']}/{thresh}]\n"
            f"😴 Rest      {bar(tama['rest'])}  {tama['rest']:>3}%  [sleep to restore]\n"
        )
        embed.add_field(name="📊 Stats", value=f"```\n{stats_text}```", inline=False)

        xp_needed  = get_xp_needed()
        level_text = f"Level  {progress_bar(tama['evo_xp'], xp_needed)}  {tama['evo_xp']}/{xp_needed} XP\n"
        if tama["event_modifier"] == "catalyst" and tama["event_modifier_until"] and datetime.now(timezone.utc) < tama["event_modifier_until"]:
            level_text += "Orokin Reactor Active — Focus XP x2!\n"
        if tama["stage"] in ("warframe", "prime"):
            forma_count = round((tama["prime_xp_multiplier"] - 1.0) / FORMA_XP_MULT_BONUS)
            if forma_count > 0:
                level_text += f"🔧 {forma_count} Forma applied — XP x{tama['prime_xp_multiplier']:.1f}\n"
        embed.add_field(name="📈 Level Progress", value=f"```\n{level_text}```", inline=False)

        status_lines = []
        if tama["sleeping"]:
            elapsed   = int((datetime.now(timezone.utc) - tama["sleep_started"]).total_seconds())
            remaining = max(0, SLEEP_DURATION_SECONDS - elapsed)
            status_lines.append(f"😴 Sleeping — {remaining//60}m {remaining%60}s remaining")
        if tama["infected"]:
            status_lines.append("☣️ TECHNOCYTE INFECTED — Actions cost more clicks!")
        if tama["active_event"] == "corrupted" and not tama["corrupted_warned"] and not tama["sleeping"]:
            status_lines.append("⚠️ CORRUPTED — Decay accelerates!")
        if tama.get("corruption_lingering_until") and datetime.now(timezone.utc) < tama["corruption_lingering_until"] and not tama["sleeping"]:
            remaining_min = int((tama["corruption_lingering_until"] - datetime.now(timezone.utc)).total_seconds() / 60) + 1
            status_lines.append(f"☠️ CORRUPTION LINGERING — Decay accelerates for {remaining_min}m more")
        if tama["overfed_until"] and datetime.now(timezone.utc) < tama["overfed_until"]:
            status_lines.append("🍖 Overfed — Feeding locked temporarily")
        triple = tama.get("decay_triple_until")
        if triple and datetime.now(timezone.utc) < triple:
            status_lines.append("💥 Rolling Guard failed — Decay spikes!")
        if tama["rest"] < 25:
            status_lines.append("😵 Exhausted — Feed & Clean decaying 1.8x faster!")
        elif tama["rest"] < 50:
            status_lines.append("😓 Fatigued — Feed & Clean decaying 1.4x faster!")
        if tama["level_stat"] < 25:
            status_lines.append("🧠 Unfocused — XP gain reduced to 40%!")
        elif tama["level_stat"] < 50:
            status_lines.append("🧠 Distracted — XP gain reduced to 70%!")
        if tama.get("groggy_until") and datetime.now(timezone.utc) < tama["groggy_until"]:
            streak = tama.get("sleep_streak", 1)
            groggy_mult = max(0.3, 1.0 - (streak - 1) * 0.2)
            status_lines.append(f"😴 Groggy — consecutive sleep penalty, Focus XP at {int(groggy_mult*100)}%!")
        if status_lines:
            embed.add_field(name="⚠️ Status", value="\n".join(status_lines), inline=False)
    elif tama["completed"]:
        embed.add_field(name="🎖️ Fully Evolved", value=f"{wf_name()} has reached maximum power. Await a mod to close the session.", inline=False)

    if not tama["dead"]:
        embed.add_field(name="🏆 Top Tenno", value=build_leaderboard_text(), inline=False)

    if tama["dead"]:
        embed.set_footer(text="You had one job. She's gone now.")
    elif tama["paused"]:
        embed.set_footer(text="⏸️ Paused | Care for it together. Every click counts.")
    else:
        embed.set_footer(text="Care for it together. Every click counts.")
    embed.timestamp = datetime.now(timezone.utc)
    return embed

def build_view():
    return TamagotchiView()

# ---------------------------
# COOLDOWN HELPERS
# ---------------------------
PING_PREFS_FILE    = "ping_prefs.json"
TAMA_STATE_FILE    = "tama_state.json"

def load_tama_state():
    """Restore channel_id and message_id from disk after restart."""
    try:
        with open(TAMA_STATE_FILE, "r") as f:
            data = json.load(f)
            if data.get("channel_id"):
                tama["channel_id"] = data["channel_id"]
            if data.get("message_id"):
                tama["message_id"] = data["message_id"]
    except Exception:
        pass

def save_tama_state():
    """Persist channel_id and message_id to disk."""
    try:
        with open(TAMA_STATE_FILE, "w") as f:
            json.dump({
                "channel_id": tama.get("channel_id"),
                "message_id": tama.get("message_id"),
            }, f)
    except Exception:
        pass

def load_ping_prefs():
    global tama_ping_opted_in, tama_dm_opted_in
    try:
        with open(PING_PREFS_FILE, "r") as f:
            data = json.load(f)
            tama_ping_opted_in = set(data.get("channel", []))
            tama_dm_opted_in   = set(data.get("dm", []))
    except Exception:
        tama_ping_opted_in = set()
        tama_dm_opted_in   = set()

def save_ping_prefs():
    try:
        with open(PING_PREFS_FILE, "w") as f:
            json.dump({
                "channel": list(tama_ping_opted_in),
                "dm":      list(tama_dm_opted_in),
            }, f)
    except Exception:
        pass

tama_ping_opted_in: set = set()
tama_dm_opted_in: set = set()
load_ping_prefs()
load_tama_state()

# Separate cooldown tracker for event/rolling guard clicks — does NOT affect action cooldown
event_cooldowns: dict = {}

def is_on_event_cooldown(user_id: int) -> tuple:
    cd = event_cooldowns.get(user_id)
    if cd and datetime.now(timezone.utc) < cd:
        return True, int((cd - datetime.now(timezone.utc)).total_seconds())
    return False, 0

def set_event_cooldown(user_id: int):
    event_cooldowns[user_id] = datetime.now(timezone.utc) + timedelta(minutes=get_config_value("GLOBAL_COOLDOWN_MINUTES"))

def is_on_cooldown(user_id: int) -> tuple:
    cd = tama["cooldowns"].get(user_id)
    if cd and datetime.now(timezone.utc) < cd:
        remaining = int((cd - datetime.now(timezone.utc)).total_seconds())
        return True, remaining
    return False, 0

def set_cooldown(user_id: int):
    expires = datetime.now(timezone.utc) + timedelta(minutes=get_config_value("GLOBAL_COOLDOWN_MINUTES"))
    tama["cooldowns"][user_id] = expires
    if user_id in tama_ping_opted_in or user_id in tama_dm_opted_in:
        safe_task(_ping_when_ready(user_id, expires))

async def _ping_when_ready(user_id: int, expires: datetime):
    wait = (expires - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        await asyncio.sleep(wait)
    if (user_id not in tama_ping_opted_in and user_id not in tama_dm_opted_in) or not tama["active"] or tama["dead"] or tama["completed"]:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    member = guild.get_member(user_id)
    if not member:
        return
    if user_id in tama_dm_opted_in:
        try:
            await member.send("⚡ Your cooldown is up in **tamaframe** — ready to contribute!")
        except discord.Forbidden:
            pass
    if user_id in tama_ping_opted_in:
        ch = get_tama_channel(guild)
        if ch:
            try:
                await ch.send(f"{member.mention} Your cooldown is up — ready to contribute!", delete_after=10)
            except Exception:
                pass

def add_to_leaderboard(user_id: int, name: str, is_train: bool = False):
    if user_id not in tama["leaderboard"]:
        tama["leaderboard"][user_id] = {"name": name, "clicks": 0, "train_clicks": 0}
    tama["leaderboard"][user_id]["clicks"] += 1
    if is_train:
        tama["leaderboard"][user_id]["train_clicks"] += 1
        # Don't double-count — train already incremented clicks above

# ---------------------------
# VIEWS
# ---------------------------
class TamagotchiView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        if tama["completed"] or tama["dead"]:
            return
        if tama["stage"] == "relic":
            self.add_item(GatherButton())
        elif not tama["sleeping"]:
            self.add_item(FeedButton())
            self.add_item(CleanButton())
            self.add_item(TrainButton())
            if tama["infected"]:
                self.add_item(RollingGuardButton())
            elif not tama["sleep_vote_message_id"]:
                self.add_item(SleepVoteButton())

class GatherButton(ui.Button):
    def __init__(self):
        super().__init__(label="⚗️ Gather Reactant", style=discord.ButtonStyle.primary, custom_id="tama_gather")
    async def callback(self, interaction: discord.Interaction):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and \
           not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance. "
                "Join the Red Ribbon Imperium or an allied clan to participate!",
                ephemeral=True, delete_after=15
            )
            return
        on_cd, remaining = is_on_cooldown(interaction.user.id)
        if on_cd:
            m, s = remaining // 60, remaining % 60
            await interaction.response.send_message(f"⏳ Cooldown: {m}m {s}s remaining.", ephemeral=True, delete_after=6)
            return
        if tama["stage"] != "relic":
            await interaction.response.send_message("This button is no longer active.", ephemeral=True, delete_after=6)
            return
        set_cooldown(interaction.user.id)
        add_to_leaderboard(interaction.user.id, interaction.user.display_name)
        fissure_bonus = tama["active_event"] == "fissure"
        tama["action_progress"]["reactant"] += 2 if fissure_bonus else 1
        if tama["action_progress"]["reactant"] >= RELIC_CLICK_THRESHOLD:
            tama["action_progress"]["reactant"] = 0
            tama["reactants"] = min(RELIC_REACTANTS_NEEDED, tama["reactants"] + 1)
            if tama["reactants"] >= RELIC_REACTANTS_NEEDED:
                await interaction.response.send_message("⚗️ The relic cracks open!", ephemeral=True, delete_after=6)
                tama["relic_cracking"] = True
                await refresh_embed(interaction.guild)
                await asyncio.sleep(4)
                tama["relic_cracking"] = False
                await evolve_to_warframe(interaction.guild)
                return
            bonus_text = " (⚗️ Fissure — double speed!)" if fissure_bonus else ""
            await interaction.response.send_message(f"⚗️ Reactant gathered{bonus_text}! {tama['reactants']}/{RELIC_REACTANTS_NEEDED}", ephemeral=True, delete_after=6)
        else:
            progress_left = RELIC_CLICK_THRESHOLD - tama["action_progress"]["reactant"]
            bonus_text = f" (⚗️ +2/click)" if fissure_bonus else ""
            await interaction.response.send_message(f"⚗️ Contribution recorded{bonus_text}! {progress_left} progress remaining.", ephemeral=True, delete_after=6)
        await refresh_embed(interaction.guild)

class FeedButton(ui.Button):
    def __init__(self):
        super().__init__(label="🍖 Feed", style=discord.ButtonStyle.success, custom_id="tama_feed")
    async def callback(self, interaction: discord.Interaction):
        await handle_action(interaction, "feed")

class CleanButton(ui.Button):
    def __init__(self):
        super().__init__(label="🧼 Clean", style=discord.ButtonStyle.primary, custom_id="tama_clean")
    async def callback(self, interaction: discord.Interaction):
        await handle_action(interaction, "clean")

class TrainButton(ui.Button):
    def __init__(self):
        super().__init__(label="⚡ Focus", style=discord.ButtonStyle.danger, custom_id="tama_train")
    async def callback(self, interaction: discord.Interaction):
        await handle_action(interaction, "train")

class RollingGuardButton(ui.Button):
    def __init__(self):
        super().__init__(label="💊 Rolling Guard", style=discord.ButtonStyle.danger, custom_id="tama_rolling_guard")
    async def callback(self, interaction: discord.Interaction):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and \
           not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance. "
                "Join the Red Ribbon Imperium or an allied clan to participate!",
                ephemeral=True, delete_after=15
            )
            return
        if not tama["infected"]:
            await interaction.response.send_message("Not infected right now.", ephemeral=True, delete_after=6)
            return
        on_cd, remaining = is_on_event_cooldown(interaction.user.id)
        if on_cd:
            m, s = remaining // 60, remaining % 60
            await interaction.response.send_message(f"⏳ Event cooldown: {m}m {s}s remaining.", ephemeral=True, delete_after=6)
            return
        set_event_cooldown(interaction.user.id)
        tama["rolling_guard_clicks"] += 1
        current = tama["rolling_guard_clicks"]
        needed  = EVENT_ROLLING_GUARD_CLICKS
        add_to_leaderboard(interaction.user.id, interaction.user.display_name)
        # Show rolling guard animation — duration from SPRITE_OVERRIDE_DURATIONS
        wf_key = tama.get("current_prime") if tama["stage"] == "prime" else tama.get("current_warframe", "mag")
        rg_secs = SPRITE_OVERRIDE_DURATIONS.get(wf_key, SPRITE_OVERRIDE_DURATIONS["DEFAULT"]).get("rolling_guard", 6)
        tama["sprite_override"]       = "rolling_guard"
        tama["sprite_override_until"] = datetime.now(timezone.utc) + timedelta(seconds=rg_secs)
        await interaction.response.send_message(f"💊 Rolling Guard: {current}/{needed} clicks.", ephemeral=True, delete_after=6)
        if current >= needed:
            await apply_rolling_guard(interaction.guild)
        else:
            await refresh_embed(interaction.guild)

class SleepVoteButton(ui.Button):
    def __init__(self):
        super().__init__(label="💤 Vote Sleep", style=discord.ButtonStyle.secondary, custom_id="tama_sleep_vote")
    async def callback(self, interaction: discord.Interaction):
        if tama["sleeping"]:
            await interaction.response.send_message("Already sleeping.", ephemeral=True, delete_after=6)
            return
        if tama["infected"]:
            await interaction.response.send_message(f"☣️ Can't sleep while {wf_name()} is infected — cure {wf_obj()} first!", ephemeral=True, delete_after=6)
            return
        if tama["active_event"] == "corrupted" or (tama.get("corruption_lingering_until") and datetime.now(timezone.utc) < tama["corruption_lingering_until"]):
            await interaction.response.send_message("⚠️ Can't sleep while Corruption is active — wait for it to resolve!", ephemeral=True, delete_after=6)
            return
        if interaction.user.id in tama["sleep_vote_yes"] or interaction.user.id in tama["sleep_vote_no"]:
            await interaction.response.send_message("You already voted.", ephemeral=True, delete_after=6)
            return
        tama["sleep_vote_yes"].add(interaction.user.id)
        if len(tama["sleep_vote_yes"]) == 1:
            tama["sleep_vote_started"] = datetime.now(timezone.utc)
            safe_task(sleep_vote_expiry_timer(interaction.guild))
        await interaction.response.send_message(f"💤 Sleep vote: {len(tama['sleep_vote_yes'])}/{SLEEP_YES_NEEDED} yes.", ephemeral=True, delete_after=6)
        if len(tama["sleep_vote_yes"]) >= SLEEP_YES_NEEDED:
            if tama["infected"]:
                tama["sleep_vote_yes"] = set()
                tama["sleep_vote_no"]  = set()
                tama["sleep_vote_started"] = None
                ch = get_tama_channel(interaction.guild)
                if ch and tama["sleep_vote_message_id"]:
                    try:
                        msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                        await msg.delete()
                    except Exception:
                        pass
                tama["sleep_vote_message_id"] = None
                if ch:
                    m = await ch.send(f"☣️ **Sleep cancelled!** {wf_name()} got infected mid-vote — cure {wf_obj()} before {wf_pronoun()} can rest!", delete_after=20)
            else:
                await start_sleep(interaction.guild)
        else:
            await post_sleep_vote_status(interaction.guild)
        await refresh_embed(interaction.guild)

# ---------------------------
# ACTION HANDLER
# ---------------------------
async def handle_action(interaction: discord.Interaction, action: str):
    # Associate and Allied Clan Member can see but cannot interact
    member_roles = {r.id for r in interaction.user.roles}
    if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and \
       not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                            TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
        await interaction.response.send_message(
            "⚔️ Button interactions are exclusive to members of clans in our alliance. "
            "Join the Red Ribbon Imperium or an allied clan to participate!",
            ephemeral=True, delete_after=15
        )
        return
    if tama["stage"] == "relic":
        await interaction.response.send_message("This button is no longer active.", ephemeral=True, delete_after=6)
        return
    if tama["dead"]:
        await interaction.response.send_message("💀 A mod must use `/tama-manage End` to close the session.", ephemeral=True, delete_after=6)
        return
    if tama["completed"]:
        await interaction.response.send_message(f"✨ {wf_name()} is complete! Awaiting mod.", ephemeral=True, delete_after=6)
        return
    if tama["paused"]:
        await interaction.response.send_message("⏸️ Paused.", ephemeral=True, delete_after=6)
        return
    if tama["sleeping"]:
        await interaction.response.send_message("😴 Sleeping — actions locked.", ephemeral=True, delete_after=6)
        return
    on_cd, remaining = is_on_cooldown(interaction.user.id)
    if on_cd:
        m, s = remaining // 60, remaining % 60
        await interaction.response.send_message(f"⏳ Cooldown: {m}m {s}s remaining.", ephemeral=True, delete_after=6)
        return
    if action == "feed" and tama["overfed_until"] and datetime.now(timezone.utc) < tama["overfed_until"]:
        await interaction.response.send_message("🍖 Overfed — feeding locked for now.", ephemeral=True, delete_after=6)
        return
    if action == "train" and tama["rest"] < TRAIN_BLOCK_REST_THRESHOLD:
        await interaction.response.send_message(
            f"😴 {wf_name()} is too exhausted to train. Rest {wf_obj()} first!",
            ephemeral=True, delete_after=8
        )
        return
    set_cooldown(interaction.user.id)
    add_to_leaderboard(interaction.user.id, interaction.user.display_name, is_train=(action == "train"))
    tama["action_progress"][action] += 1
    thresh  = action_threshold()
    current = tama["action_progress"][action]
    log_tama_event("click", user_id=interaction.user.id, action=action)
    if current >= thresh:
        tama["action_progress"][action] = 0
        try:
            await interaction.response.send_message(f"✅ **{action.capitalize()}** triggered!", ephemeral=True, delete_after=6)
        except Exception:
            pass
        log_tama_event("action_trigger", user_id=interaction.user.id, action=action)
        safe_task(trigger_action(action, interaction.guild))
    else:
        clicks_left = thresh - current
        try:
            await interaction.response.send_message(f"✅ Contribution recorded! {clicks_left} more click(s) needed.", ephemeral=True, delete_after=6)
        except Exception:
            pass
    safe_task(refresh_embed(interaction.guild))

async def trigger_action(action: str, guild):
    mult            = get_happiness_multiplier()
    catalyst_active = (tama["event_modifier"] == "catalyst" and tama["event_modifier_until"] and datetime.now(timezone.utc) < tama["event_modifier_until"])
    xp_mult         = (2.0 if catalyst_active else 1.0) * tama.get("prime_xp_multiplier", 1.0)

    # Option D — immunity window reduces all gains to 75%
    immunity_active = tama.get("infection_immunity_until") and datetime.now(timezone.utc) < tama["infection_immunity_until"]
    gain_mult = IMMUNITY_GAIN_MULT if immunity_active else 1.0

    # Helper: apply ±30% variance to any gain value
    def vary(val):
        return max(1, int(val * random.uniform(1 - GAIN_VARIANCE, 1 + GAIN_VARIANCE)))

    # Low Focus reduces XP gain — unfocused training is ineffective
    focus_pct = tama["level_stat"]
    if focus_pct < 25:
        xp_mult *= 0.4
    elif focus_pct < 50:
        xp_mult *= 0.7

    # Groggy penalty — consecutive sleep reduces Focus XP gain temporarily
    if tama.get("groggy_until") and datetime.now(timezone.utc) < tama["groggy_until"]:
        streak = tama.get("sleep_streak", 1)
        groggy_mult = max(0.3, 1.0 - (streak - 1) * 0.2)
        xp_mult *= groggy_mult

    # Sprite override
    wf_key = tama.get("current_prime") if tama["stage"] == "prime" else tama.get("current_warframe", "mag")
    durations = SPRITE_OVERRIDE_DURATIONS.get(wf_key, SPRITE_OVERRIDE_DURATIONS["DEFAULT"])
    action_key = "rolling_guard" if action == "rolling_guard" else action
    override_secs = durations.get(action_key, SPRITE_OVERRIDE_DURATIONS["DEFAULT"].get(action_key, 5))
    tama["sprite_override"]       = action if action in ["feed", "clean", "train"] else None
    tama["sprite_override_until"] = datetime.now(timezone.utc) + timedelta(seconds=override_secs)
    async def revert_sprite():
        await asyncio.sleep(override_secs)
        tama["sprite_override"] = None
        tama["sprite_override_until"] = None
        await refresh_embed(guild)
    safe_task(revert_sprite())

    ch = get_tama_channel(guild)

    if action == "feed":
        if tama["feed"] >= FEED_OVERFED_THRESHOLD:
            tama["overfed_until"] = datetime.now(timezone.utc) + timedelta(minutes=FEED_LOCK_DURATION_MIN)
            if ch:
                asyncio.create_task(delete_after(await ch.send(
                    f"🍖 **Overfed!** {wf_name()} is stuffed — feeding locked for a while."
                ), 20))
        else:
            # Option C — 40% chance of backfire when Clean is low
            if tama["clean"] < NOISE_CLEAN_THRESHOLD and random.random() < NOISE_CHANCE:
                tama["clean"] = max(0, tama["clean"] - FEED_BACKFIRE_CLEAN_COST)
                if ch:
                    asyncio.create_task(delete_after(await ch.send(
                        f"🍖 **Messy feeding!** {wf_name()} made a mess — Clean took a hit."
                    ), 20))
            else:
                # Option B — vary gain, Option D — immunity mult
                tama["feed"]  = min(STAT_MAX, tama["feed"]  + int(vary(FEED_GAIN) * gain_mult))
                tama["clean"] = max(0,        tama["clean"] - vary(FEED_CLEAN_COST))
            # Feed XP
            if mult > 0:
                feed_xp = int(FEED_EVO_XP * mult * xp_mult * gain_mult)
                tama["evo_xp"] += feed_xp
                await check_level_up(guild)
        # Option E — consecutive same action tracking
        tama["consecutive_feeds"]  = tama.get("consecutive_feeds", 0) + 1
        tama["consecutive_cleans"] = 0
        tama["consecutive_trains"] = 0
        tama["last_action"] = "feed"

    elif action == "clean":
        if tama["clean"] >= CLEAN_OVER_THRESHOLD:
            tama["overclean_debuff"] = True
            if ch:
                asyncio.create_task(delete_after(await ch.send(
                    "🧼 **Over-sanitized!** Corrupted event chance increased."
                ), 20))
        else:
            # Option E — diminishing returns after consecutive cleans
            consec = tama.get("consecutive_cleans", 0)
            eff    = 0.5 if consec >= CONSECUTIVE_SAME_PENALTY else 1.0
            tama["clean"] = min(STAT_MAX, tama["clean"] + int(vary(CLEAN_GAIN) * gain_mult * eff))
            tama["rest"]  = max(0,        tama["rest"]  - vary(CLEAN_REST_COST))
        tama["consecutive_cleans"] = tama.get("consecutive_cleans", 0) + 1
        tama["consecutive_feeds"]  = 0
        tama["consecutive_trains"] = 0
        tama["last_action"] = "clean"

    elif action == "train":
        rest_pct = tama["rest"]
        # Rest-tiered XP multiplier
        if rest_pct < TRAIN_REST_TIER_LOW:
            rest_xp_mult = TRAIN_REST_XP_MULT_LOW
        elif rest_pct < TRAIN_REST_TIER_MID:
            rest_xp_mult = TRAIN_REST_XP_MULT_MID
        elif rest_pct < TRAIN_REST_TIER_HIGH:
            rest_xp_mult = TRAIN_REST_XP_MULT_HIGH
        else:
            rest_xp_mult = TRAIN_REST_XP_MULT_FULL

        # Rest-tiered cost scaling
        if rest_pct < TRAIN_REST_TIER_LOW:
            rest_cost = TRAIN_REST_COST_LOW
        elif rest_pct < TRAIN_REST_TIER_MID:
            rest_cost = TRAIN_REST_COST_MID
        else:
            rest_cost = TRAIN_REST_COST_HIGH

        tama["feed"]  = max(0, tama["feed"]  - vary(TRAIN_FEED_COST))
        tama["clean"] = max(0, tama["clean"] - vary(TRAIN_CLEAN_COST))
        tama["rest"]  = max(0, tama["rest"]  - vary(rest_cost))

        # Option E — diminishing returns after consecutive trains
        consec_train = tama.get("consecutive_trains", 0)
        train_eff    = 0.5 if consec_train >= CONSECUTIVE_SAME_PENALTY else 1.0

        tama["consecutive_trains"] = consec_train + 1
        tama["consecutive_feeds"]  = 0
        tama["consecutive_cleans"] = 0
        tama["last_action"] = "train"
        tama["sleep_streak"] = 0

        if tama["consecutive_trains"] > TRAIN_OVERTRAIN_STREAK:
            if ch:
                asyncio.create_task(delete_after(await ch.send(
                    f"⚡ **Overtrained!** {wf_name()} is exhausted. Rest {wf_obj()} soon."
                ), 20))

        # Option C — 40% chance noise cancels XP when Rest is low
        noise_cancelled = False
        if rest_pct < NOISE_REST_THRESHOLD and random.random() < NOISE_CHANCE:
            noise_cancelled = True
            if ch:
                asyncio.create_task(delete_after(await ch.send(
                    f"⚡ **{wf_name()} pushed too hard!** Training yielded no results — rest more."
                ), 20))

        if rest_pct < TRAIN_REST_TIER_LOW:
            tama["level_stat"] = max(0, tama["level_stat"] - vary(TRAIN_STAT_GAIN))
            if ch:
                asyncio.create_task(delete_after(await ch.send(
                    f"💢 **{wf_name()} is too exhausted to focus!** Training had the opposite effect — Focus dropped."
                ), 30))
        else:
            tama["level_stat"] = min(STAT_MAX, tama["level_stat"] + int(vary(TRAIN_STAT_GAIN) * gain_mult * train_eff))
            if mult > 0 and not noise_cancelled:
                # Option A — overfed reduces XP gain
                overfeed_mult = OVERFEED_XP_MULT if tama["feed"] >= FEED_OVERFED_THRESHOLD else 1.0
                xp_gain = int(TRAIN_EVO_XP * mult * xp_mult * rest_xp_mult * gain_mult * train_eff * overfeed_mult)
                tama["evo_xp"] += xp_gain
                await check_level_up(guild)

    await refresh_embed(guild)

async def check_level_up(guild):
    xp_needed = get_xp_needed()
    ch        = get_tama_channel(guild)
    while tama["evo_xp"] >= xp_needed and tama["level"] < LEVEL_MAX:
        tama["evo_xp"] -= xp_needed
        tama["level"]  += 1
        log_tama_event("level_up", detail=f"level={tama['level']}")
        if ch:
            asyncio.create_task(delete_after(await ch.send(f"🎉 **Level Up!** Reached **Level {tama['level']}/{LEVEL_MAX}**!"), 30))
        if tama["level"] >= LEVEL_MAX:
            if tama["stage"] == "warframe":
                # Check warframe evolution condition
                wf  = tama.get("current_warframe", "mag")
                cond = WARFRAME_EVOLUTION_CONDITIONS.get(wf)
                if cond and not tama.get(cond["tracker"], False):
                    # Condition not met — block evolution, inform players
                    tama["evolution_blocked"] = True
                    if ch:
                        blocked_msg = await ch.send(
                            f"⚔️ **{wf_name()} has reached Level 30 — but the evolution is not yet unlocked.**\n"
                            f"🔍 *{cond.get('hint', 'Consider this Warframe\'s lore.')}*\n"
                            f"Keep {wf_obj()} alive until the condition is met."
                        )
                        tama["evolution_blocked_msg_id"] = blocked_msg.id
                else:
                    tama["evolution_blocked"] = False
                    await evolve_to_prime(guild)
            elif tama["stage"] == "prime":
                # Check prime evolution condition (prime complete condition)
                prime = tama.get("current_prime", "mag_prime")
                cond  = PRIME_EVOLUTION_CONDITIONS.get(prime)
                if cond and not tama.get(cond["tracker"], False):
                    tama["evolution_blocked"] = True
                    if ch:
                        blocked_msg = await ch.send(
                            f"✨ **{wf_name()} has reached Level 30 — but the ascension is not yet unlocked.**\n"
                            f"🔍 *{cond.get('hint', 'Consider this Warframe\'s lore.')}*\n"
                            f"Keep {wf_obj()} alive until the condition is met."
                        )
                        tama["evolution_blocked_msg_id"] = blocked_msg.id
                else:
                    tama["evolution_blocked"] = False
                    await trigger_prime_complete(guild)
            break

async def trigger_prime_complete(guild):
    log_tama_event("completion")
    tama["completed"] = True
    ch = get_tama_channel(guild)
    if ch:
        entries = sorted(tama["leaderboard"].values(), key=lambda x: x["clicks"], reverse=True)[:3]
        medals  = ["🥇", "🥈", "🥉"]
        podium  = "\n".join(f"{medals[i]} **{e['name']}** — {e['clicks']} pts" for i, e in enumerate(entries)) if entries else "No contributions recorded."
        await ch.send(
            f"✨ **{wf_name().upper()} HAS REACHED MAXIMUM POWER!** 🎖️\n"
            f"The Companion is fully evolved. Well done, Tenno!\n\n"
            f"🏆 **Top Contributors this session:**\n{podium}\n\n"
            f"A mod may now use `/tama-manage End` to close this session."
        )
    await refresh_embed(guild)

# ---------------------------
# SLEEP
# ---------------------------
async def sleep_vote_expiry_timer(guild):
    await asyncio.sleep(SLEEP_VOTE_TIMEOUT)
    if tama["sleep_vote_message_id"] and not tama["sleeping"]:
        tama["sleep_vote_yes"]      = set()
        tama["sleep_vote_no"]       = set()
        tama["sleep_vote_started"]  = None
        ch = get_tama_channel(guild)
        if ch:
            try:
                msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                await msg.delete()
            except Exception:
                pass
        tama["sleep_vote_message_id"] = None
        if ch:
            m = await ch.send(f"💤 Sleep vote expired with no consensus — {wf_name()} stays awake!", delete_after=30)
        await refresh_embed(guild)

async def post_sleep_vote_status(guild):
    ch = get_tama_channel(guild)
    if not ch:
        return
    yes = len(tama["sleep_vote_yes"])
    no  = len(tama["sleep_vote_no"])
    text = (
        f"💤 **Sleep Vote in Progress**\n"
        f"```\n"
        f"Yes: {'█' * yes}{'░' * max(0, SLEEP_YES_NEEDED - yes)} {yes}/{SLEEP_YES_NEEDED}\n"
        f"No:  {'█' * no }{'░' * max(0, SLEEP_NO_NEEDED  - no )} {no}/{SLEEP_NO_NEEDED}\n"
        f"```\n"
        f"{SLEEP_YES_NEEDED} yes = sleep | {SLEEP_NO_NEEDED} no = cancel | expires in {SLEEP_VOTE_TIMEOUT//60}m"
    )
    view = SleepCounterView()
    if tama["sleep_vote_message_id"]:
        try:
            msg = await ch.fetch_message(tama["sleep_vote_message_id"])
            await msg.edit(content=text, view=view)
            return
        except Exception:
            pass
    msg = await ch.send(text, view=view)
    tama["sleep_vote_message_id"] = msg.id

class SleepCounterView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @ui.button(label="✅ Yes, sleep", style=discord.ButtonStyle.success, custom_id="sleep_yes")
    async def yes(self, interaction: discord.Interaction, button: ui.Button):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and            not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance.",
                ephemeral=True, delete_after=15
            )
            return
        if interaction.user.id in tama["sleep_vote_yes"] or interaction.user.id in tama["sleep_vote_no"]:
            await interaction.response.send_message("Already voted.", ephemeral=True, delete_after=6)
            return
        tama["sleep_vote_yes"].add(interaction.user.id)
        await interaction.response.send_message(f"Voted yes. {len(tama['sleep_vote_yes'])}/{SLEEP_YES_NEEDED}", ephemeral=True, delete_after=6)
        if len(tama["sleep_vote_yes"]) >= SLEEP_YES_NEEDED:
            if tama["infected"]:
                # Infection started mid-vote — cancel sleep, notify
                tama["sleep_vote_yes"] = set()
                tama["sleep_vote_no"]  = set()
                tama["sleep_vote_started"] = None
                ch = get_tama_channel(interaction.guild)
                if ch and tama["sleep_vote_message_id"]:
                    try:
                        msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                        await msg.delete()
                    except Exception:
                        pass
                tama["sleep_vote_message_id"] = None
                if ch:
                    m = await ch.send(f"☣️ **Sleep cancelled!** {wf_name()} got infected mid-vote — cure {wf_obj()} before {wf_pronoun()} can rest!", delete_after=20)
            else:
                await start_sleep(interaction.guild)
        else:
            await post_sleep_vote_status(interaction.guild)
    @ui.button(label="❌ No, keep going", style=discord.ButtonStyle.danger, custom_id="sleep_no")
    async def no(self, interaction: discord.Interaction, button: ui.Button):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and            not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance.",
                ephemeral=True, delete_after=15
            )
            return
        if interaction.user.id in tama["sleep_vote_yes"] or interaction.user.id in tama["sleep_vote_no"]:
            await interaction.response.send_message("Already voted.", ephemeral=True, delete_after=6)
            return
        tama["sleep_vote_no"].add(interaction.user.id)
        await interaction.response.send_message(f"Voted no. {len(tama['sleep_vote_no'])}/{SLEEP_NO_NEEDED}", ephemeral=True, delete_after=6)
        if len(tama["sleep_vote_no"]) >= SLEEP_NO_NEEDED:
            tama["sleep_vote_yes"]       = set()
            tama["sleep_vote_no"]        = set()
            tama["sleep_vote_started"]   = None
            ch = get_tama_channel(interaction.guild)
            if ch and tama["sleep_vote_message_id"]:
                try:
                    msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                    await msg.delete()
                except Exception:
                    pass
            tama["sleep_vote_message_id"] = None
            if ch:
                m = await ch.send(f"❌ Sleep vote cancelled — {wf_name()} stays awake!", delete_after=30)
        else:
            await post_sleep_vote_status(interaction.guild)

async def start_sleep(guild):
    if tama["active_event"] == "corrupted":
        ch = get_tama_channel(guild)
        if ch:
            m = await ch.send("⚠️ **Sleep cancelled!** Can't sleep while Corruption is active — purify it first!", delete_after=20)
        tama["sleep_vote_yes"] = set()
        tama["sleep_vote_no"]  = set()
        tama["sleep_vote_started"] = None
        if tama["sleep_vote_message_id"]:
            ch = ch or get_tama_channel(guild)
            if ch:
                try:
                    msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                    await msg.delete()
                except Exception:
                    pass
        tama["sleep_vote_message_id"] = None
        return
    if any(tama[k] < SICK_THRESHOLD for k in ("feed", "clean")):
        ch = get_tama_channel(guild)
        if ch:
            await ch.send(
                f"⚠️ **Sleep cancelled!** {wf_name()}'s Feed or Clean is critically low — "
                f"restore them above {SICK_THRESHOLD}% before sleeping.",
                delete_after=20
            )
        tama["sleep_vote_yes"] = set()
        tama["sleep_vote_no"]  = set()
        tama["sleep_vote_started"] = None
        if tama["sleep_vote_message_id"]:
            ch = ch or get_tama_channel(guild)
            if ch:
                try:
                    msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                    await msg.delete()
                except Exception:
                    pass
        tama["sleep_vote_message_id"] = None
        return
    tama["sleeping"]           = True
    tama["sleep_started"]      = datetime.now(timezone.utc)
    log_tama_event("sleep_start")
    tama["sleep_vote_yes"]     = set()
    tama["sleep_vote_no"]      = set()
    tama["sleep_vote_started"] = None
    tama["consecutive_trains"] = 0
    tama["sleep_streak"]       = tama.get("sleep_streak", 0) + 1
    # Do NOT reset infection_warned — sleep cannot be used to dodge infection
    ch = get_tama_channel(guild)
    if ch:
        if tama["sleep_vote_message_id"]:
            try:
                msg = await ch.fetch_message(tama["sleep_vote_message_id"])
                await msg.delete()
            except Exception:
                pass
        tama["sleep_vote_message_id"] = None
        dur = SLEEP_DURATION_SECONDS // 60
        sleep_msg = await ch.send(f"😴 **{wf_name()} is sleeping.** All actions locked for {dur} minutes. Early wake tanks all stats -{WAKE_EARLY_STAT_PENALTY}. Two wake votes will be held — one now and one at the midpoint.", delete_after=SLEEP_DURATION_SECONDS)
        tama["sleep_message_id"] = sleep_msg.id
    await refresh_embed(guild)
    safe_task(sleep_timer(guild))
    # Post wake vote immediately — the button enforces the min wait internally
    safe_task(_post_wake_vote_after_delay(guild))

async def _post_wake_vote_after_delay(guild):
    """Post wake vote immediately, then again at the midpoint of sleep."""
    if tama["sleeping"]:
        await post_wake_vote(guild)
    # Schedule second opportunity at midpoint
    await asyncio.sleep(SLEEP_MIN_BEFORE_WAKE)
    if tama["sleeping"]:
        # Delete the first vote message before reposting to avoid orphan
        ch = get_tama_channel(guild)
        if ch and tama.get("wake_vote_message_id"):
            try:
                msg = await ch.fetch_message(tama["wake_vote_message_id"])
                await msg.delete()
            except Exception:
                pass
        tama["wake_yes"] = set()
        tama["wake_no"]  = set()
        tama["wake_vote_message_id"] = None
        await post_wake_vote(guild)

async def notify_wake_pings(guild):
    """Ping all opted-in members that the warframe has woken up."""
    ch = get_tama_channel(guild)
    if not ch:
        return
    mentions = []
    for uid in tama_ping_opted_in:
        member = guild.get_member(uid)
        if member:
            mentions.append(member.mention)
    if mentions:
        m = await ch.send(f"☀️ {' '.join(mentions)} — {wf_name()} is awake! Your cooldown is ready.")
        asyncio.create_task(delete_after(m, 30))
    # DM opted-in members
    for uid in tama_dm_opted_in:
        member = guild.get_member(uid)
        if member:
            try:
                await member.send(f"☀️ {wf_name()} has woken up — your cooldown is ready!")
            except Exception:
                pass

async def sleep_timer(guild):
    await asyncio.sleep(SLEEP_DURATION_SECONDS)
    if tama["sleeping"]:
        tama["sleeping"] = False
        tama["wake_yes"] = set()
        tama["wake_no"]  = set()
        log_tama_event("sleep_end")
        # Option D — rest recovery capped by streak (diminishing returns)
        streak   = tama.get("sleep_streak", 0)
        max_rest = max(50, 100 - ((streak - 1) * 15))  # 100, 85, 70, 55, 50 floor
        tama["rest"] = min(max_rest, STAT_MAX)

        # Option A — groggy Focus penalty for consecutive sleeps
        if streak > 1:
            groggy_mins = min(streak * 5, 20)  # 10min, 15min, 20min cap
            tama["groggy_until"] = datetime.now(timezone.utc) + timedelta(minutes=groggy_mins)

        # Stats floored at 40% on wake — gives members time to adjust
        for k in ("feed", "clean", "level_stat"):
            tama[k] = max(40, tama[k])

        ch = get_tama_channel(guild)
        if ch:
            if tama["sleep_message_id"]:
                try:
                    msg = await ch.fetch_message(tama["sleep_message_id"])
                    await msg.delete()
                except Exception:
                    pass
            tama["sleep_message_id"] = None
            if tama["wake_vote_message_id"]:
                try:
                    msg = await ch.fetch_message(tama["wake_vote_message_id"])
                    await msg.delete()
                except Exception:
                    pass
            tama["wake_vote_message_id"] = None

            # Wake message — warn about grogginess if applicable
            if streak > 1:
                groggy_mins = min(streak * 5, 20)
                await ch.send(
                    f"☀️ **{wf_name()} has woken up!** Rest restored to {max_rest}%.\n"
                    f"😴 **Groggy** — consecutive sleep #{streak}. Focus XP reduced for {groggy_mins} minutes.",
                    delete_after=40
                )
            else:
                await ch.send(f"☀️ **{wf_name()} has woken up!** Rest fully restored. Ready for action.", delete_after=30)
        await notify_wake_pings(guild)
        await refresh_embed(guild)

async def post_wake_vote(guild):
    ch = get_tama_channel(guild)
    if not ch:
        return
    yes = len(tama["wake_yes"])
    no  = len(tama["wake_no"])
    text = (
        f"⚡ **Early Wake Vote**\n"
        f"⚠️ Warning: early wake tanks ALL stats -{WAKE_EARLY_STAT_PENALTY}!\n"
        f"```\n"
        f"Wake:  {'█' * yes}{'░' * max(0, WAKE_YES_NEEDED - yes)} {yes}/{WAKE_YES_NEEDED}\n"
        f"Sleep: {'█' * no }{'░' * max(0, WAKE_NO_NEEDED  - no )} {no}/{WAKE_NO_NEEDED}\n"
        f"```"
    )
    view = WakeVoteView()
    if tama["wake_vote_message_id"]:
        try:
            msg = await ch.fetch_message(tama["wake_vote_message_id"])
            await msg.edit(content=text, view=view)
            return
        except Exception:
            pass
    msg = await ch.send(text, view=view)
    tama["wake_vote_message_id"] = msg.id

class WakeVoteView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @ui.button(label="⚡ Wake up!", style=discord.ButtonStyle.danger, custom_id="wake_yes")
    async def wake(self, interaction: discord.Interaction, button: ui.Button):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and            not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance.",
                ephemeral=True, delete_after=15
            )
            return
        if not tama["sleeping"]:
            await interaction.response.send_message("Already awake.", ephemeral=True, delete_after=6)
            return
        if interaction.user.id in tama["wake_yes"] or interaction.user.id in tama["wake_no"]:
            await interaction.response.send_message("Already voted.", ephemeral=True, delete_after=6)
            return
        tama["wake_yes"].add(interaction.user.id)
        await interaction.response.send_message(f"Voted wake. {len(tama['wake_yes'])}/{WAKE_YES_NEEDED}", ephemeral=True, delete_after=6)
        if len(tama["wake_yes"]) >= WAKE_YES_NEEDED:
            for k in STAT_KEYS:
                tama[k] = max(0, tama[k] - WAKE_EARLY_STAT_PENALTY)
            tama["sleeping"] = False
            tama["wake_yes"] = set()
            tama["wake_no"]  = set()
            log_tama_event("wake_early")
            ch = get_tama_channel(interaction.guild)
            if ch:
                if tama["sleep_message_id"]:
                    try:
                        msg = await ch.fetch_message(tama["sleep_message_id"])
                        await msg.delete()
                    except Exception:
                        pass
                tama["sleep_message_id"] = None
                if tama["wake_vote_message_id"]:
                    try:
                        msg = await ch.fetch_message(tama["wake_vote_message_id"])
                        await msg.delete()
                    except Exception:
                        pass
                tama["wake_vote_message_id"] = None
                await ch.send(f"⚡ **{wf_name()} was woken up early!** All stats -{WAKE_EARLY_STAT_PENALTY}. Worth it?", delete_after=30)
            await notify_wake_pings(interaction.guild)
            await refresh_embed(interaction.guild)
        else:
            await post_wake_vote(interaction.guild)
    @ui.button(label="😴 Let it sleep", style=discord.ButtonStyle.secondary, custom_id="wake_no")
    async def keep_sleep(self, interaction: discord.Interaction, button: ui.Button):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and            not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance.",
                ephemeral=True, delete_after=15
            )
            return
        if interaction.user.id in tama["wake_yes"] or interaction.user.id in tama["wake_no"]:
            await interaction.response.send_message("Already voted.", ephemeral=True, delete_after=6)
            return
        tama["wake_no"].add(interaction.user.id)
        await interaction.response.send_message(f"Voted sleep. {len(tama['wake_no'])}/{WAKE_NO_NEEDED}", ephemeral=True, delete_after=6)
        if len(tama["wake_no"]) >= WAKE_NO_NEEDED:
            tama["wake_yes"] = set()
            tama["wake_no"]  = set()
            ch = get_tama_channel(interaction.guild)
            if ch:
                if tama["wake_vote_message_id"]:
                    try:
                        msg = await ch.fetch_message(tama["wake_vote_message_id"])
                        await msg.delete()
                    except Exception:
                        pass
                tama["wake_vote_message_id"] = None
                m = await ch.send(f"😴 Wake vote cancelled — sweet dreams, {wf_name()}.")
                asyncio.create_task(delete_after(m, 15))
        else:
            await post_wake_vote(interaction.guild)

# ---------------------------
# INFECTION / ROLLING GUARD
# ---------------------------
async def clear_infection_messages(guild):
    """Delete any lingering infection warning or rolling guard messages."""
    tama["infection_warned"] = False
    ch = get_tama_channel(guild)
    if not ch:
        return
    for key in ("infection_warn_message_id", "rolling_guard_message_id"):
        mid = tama.get(key)
        if mid:
            try:
                msg = await ch.fetch_message(mid)
                await msg.delete()
            except Exception:
                pass
            tama[key] = None

async def apply_rolling_guard(guild):
    log_tama_event("rolling_guard_success")
    tama["infected"]                  = False
    tama["infection_warned"]          = False
    tama["rolling_guard_clicks"]      = 0
    tama["rolling_guard_expires"]     = None
    tama["infection_immunity_until"]  = datetime.now(timezone.utc) + timedelta(minutes=INFECTION_IMMUNITY_MINUTES)
    tama["infection_penalty_until"]   = datetime.now(timezone.utc) + timedelta(minutes=INFECTION_PENALTY_DURATION_MIN)
    await clear_infection_messages(guild)
    ch = get_tama_channel(guild)
    if ch:
        await ch.send(f"💊 **Rolling Guard administered!** {wf_name()} is cured. Technocyte immunity granted.", delete_after=30)
    await refresh_embed(guild)

async def rolling_guard_fail(guild):
    log_tama_event("rolling_guard_fail")
    tama["rolling_guard_clicks"]  = 0
    tama["decay_triple_until"]    = datetime.now(timezone.utc) + timedelta(minutes=DECAY_TRIPLE_DURATION_MIN)
    for k in STAT_KEYS:
        tama[k] = max(0, tama[k] - ROLLING_GUARD_FAIL_STAT_HIT)
    await clear_infection_messages(guild)
    ch = get_tama_channel(guild)
    if ch:
        await ch.send(f"💥 **Rolling Guard expired!** All stats -{ROLLING_GUARD_FAIL_STAT_HIT}. Decay spikes for a while!", delete_after=30)
    await refresh_embed(guild)

async def infection_warning(guild, ch):
    msg = await ch.send(
        f"⚠️ **WARNING:** {wf_name()} is showing signs of Technocyte infection! "
        f"Boost {wf_pos()} stats in the next 5 minutes or {wf_pronoun()}'ll be fully infected!"
    )
    tama["infection_warn_message_id"] = msg.id
    log_tama_event("infection_warn")
    await asyncio.sleep(INFECTION_WARN_DURATION_SEC)
    # If message was already cleared by a mod action, bail out entirely
    if not tama.get("infection_warn_message_id") and not tama["infection_warned"]:
        return
    tama["infection_warn_message_id"] = None
    try:
        await msg.delete()
    except Exception:
        pass
    if tama["infection_warned"] and not tama["infected"]:
        tama["infected"]         = True
        tama["infection_warned"] = False
        tama["infection_count"]  = tama.get("infection_count", 0) + 1
        log_tama_event("infection_trigger")
        needed = EVENT_ROLLING_GUARD_CLICKS
        rg_msg = await ch.send(
            f"☣️ **TECHNOCYTE INFECTION!** " + wf_name() + " is infected! Administer Rolling Guard NOW! "
            f"{needed} clicks needed within **{ROLLING_GUARD_WINDOW_MIN} minutes** or her condition worsens.",
            delete_after=ROLLING_GUARD_WINDOW_MIN * 60
        )
        safe_task(rolling_guard_expiry_timer(guild, rg_msg.id))
        await refresh_embed(guild)
    elif not tama["infection_warned"]:
        m = await ch.send(f"✅ **Technocyte threat neutralised.** {wf_name()}'s vitals are stable.", delete_after=30)

async def rolling_guard_expiry_timer(guild, msg_id: int):
    tama["rolling_guard_message_id"] = msg_id
    tama["rolling_guard_expires"]    = datetime.now(timezone.utc) + timedelta(minutes=ROLLING_GUARD_WINDOW_MIN)
    await asyncio.sleep(ROLLING_GUARD_WINDOW_MIN * 60)
    if tama["infected"] and tama["rolling_guard_message_id"]:
        tama["rolling_guard_message_id"] = None
        await rolling_guard_fail(guild)

# ---------------------------
# EVOLUTION
# ---------------------------
async def clear_stage(guild):
    global event_cooldowns
    event_cooldowns = {}
    ch = get_tama_channel(guild)
    if ch:
        await purge_channel(ch)
    tama["active_event"]              = None
    tama["active_event_id"]           = -1
    tama["event_message_id"]          = None
    tama["event_clicks"]              = 0
    tama["corrupted_warned"]          = False
    tama["corrupted_warn_message_id"] = None
    tama["sleep_vote_yes"]            = set()
    tama["sleep_vote_no"]             = set()
    tama["sleep_vote_message_id"]     = None
    tama["sleep_vote_started"]        = None
    tama["sleeping"]                  = False
    tama["wake_yes"]                  = set()
    tama["wake_no"]                   = set()
    tama["wake_vote_message_id"]      = None
    tama["sleep_message_id"]          = None
    tama["infected"]                  = False
    tama["infection_warned"]          = False
    tama["infection_warn_message_id"] = None
    tama["rolling_guard_clicks"]      = 0
    tama["rolling_guard_message_id"]  = None
    tama["message_id"]                = None

async def evolve_to_warframe(guild):
    log_tama_event("evolution", detail="relic_to_warframe")
    await clear_stage(guild)
    await asyncio.sleep(1)  # ensure purge completes before posting new embed
    tama["stage"]              = "warframe"
    tama["reactants"]          = 0
    tama["reactant_progress"]  = 0
    tama["action_progress"]    = {"feed": 0, "clean": 0, "train": 0, "reactant": 0}
    tama["feed"]               = rand_stat()
    tama["clean"]              = rand_stat()
    tama["level_stat"]         = rand_stat()
    tama["rest"]               = rand_stat()
    tama["level"]              = 0
    tama["evo_xp"]             = 0
    tama["consecutive_trains"] = 0
    ch = get_tama_channel(guild)
    if ch:
        frame_name = tama.get("current_warframe", "mag").upper()
        await ch.send(f"⚔️ **The relic cracks open! {frame_name} has emerged!** Time to level {wf_obj()} up. Care for {wf_obj()} well, Tenno.", delete_after=60)
        try:
            await ch.edit(name="⚔️︱tamaframe")
        except Exception:
            pass
    await refresh_embed(guild)

async def evolve_to_prime(guild):
    log_tama_event("evolution", detail="warframe_to_prime")
    tama["infection_count"]        = 0
    tama["infection_penalty_until"] = None
    await clear_stage(guild)
    await asyncio.sleep(1)  # ensure purge completes before posting new embed
    tama["stage"]                    = "prime"
    tama["feed"]                     = rand_stat()
    tama["clean"]                    = rand_stat()
    tama["level_stat"]               = rand_stat()
    tama["rest"]                     = rand_stat()
    tama["level"]                    = 0
    tama["evo_xp"]                   = 0
    tama["action_progress"]          = {"feed": 0, "clean": 0, "train": 0, "reactant": 0}
    tama["prime_xp_multiplier"]      = 1.0
    tama["consecutive_trains"]       = 0
    tama["evolution_blocked"]        = False
    tama["evolution_blocked_msg_id"] = None
    ch = get_tama_channel(guild)
    if ch:
        prime_name = tama.get("current_prime", "mag_prime").upper().replace("_", " ")
        await ch.send(f"✨ **The Warframe has transcended into {prime_name}!** The journey continues — level {wf_obj()} to 30. It won't be easy.", delete_after=60)
        try:
            await ch.edit(name="✨︱tamaframe")
        except Exception:
            pass
    await refresh_embed(guild)

# ---------------------------
# EVENTS
# ---------------------------
async def trigger_event(event_type: str, guild):
    allowed = STAGE_EVENTS.get(tama["stage"], set())
    if event_type not in allowed:
        ch = get_tama_channel(guild)
        if ch:
            asyncio.create_task(delete_after(await ch.send(f"❌ Event `{event_type}` is not valid in stage `{tama['stage']}`."), 10))
        return
    # Never trigger corruption during sleep, and never trigger anything during active corruption
    if event_type == "corrupted" and tama["sleeping"]:
        return
    event_id = random.randint(0, 2**31)
    tama["active_event"]        = event_type
    tama["active_event_id"]     = event_id
    tama["event_clicks"]        = 0
    tama["ticks_since_event"]   = 0
    tama["current_pity_chance"] = PITY_BASE_CHANCE
    tama["last_event_time"]     = datetime.now(timezone.utc)
    log_tama_event("event_start", action=event_type)
    ch = get_tama_channel(guild)
    if not ch:
        return

    if event_type == "fissure":
        tama["event_expires"] = datetime.now(timezone.utc) + timedelta(minutes=5)
        msg = await ch.send("```\n⚗️ ═══════════════════════════════ ⚗️\n         VOID FISSURE DETECTED!\n   Reactant gathering is DOUBLED!\n        Active for 5 minutes.\n⚗️ ═══════════════════════════════ ⚗️\n```")
        tama["event_message_id"] = msg.id
        safe_task(expire_event(guild, 300, event_id))
    elif event_type == "catalyst":
        needed = EVENT_CATALYST_CLICKS
        tama["event_expires"] = datetime.now(timezone.utc) + timedelta(minutes=15)
        msg = await ch.send(f"```\n⚗️ ═══════════════════════════════════════ ⚗️\n          OROKIN REACTOR DETECTED!\n   {needed} Tenno must click to activate it.\n   Focus XP will be DOUBLED for 30 minutes.\n        Time limit: 15 minutes.\n⚗️ ═══════════════════════════════════════ ⚗️\n```")
        tama["event_message_id"] = msg.id
        await msg.edit(view=EventClickView(event_type, needed))
        safe_task(expire_event(guild, 900, event_id))
    elif event_type == "forma":
        needed = EVENT_FORMA_CLICKS
        tama["event_expires"] = datetime.now(timezone.utc) + timedelta(minutes=15)
        msg = await ch.send(f"```\n🔧 ═══════════════════════════════════════ 🔧\n           OROKIN FORMA DETECTED!\n   Resets {wf_name()}'s level but permanently\n   boosts the XP multiplier by +0.5x!\n   {needed} Tenno must click to apply it.\n        Time limit: 15 minutes.\n🔧 ═══════════════════════════════════════ 🔧\n```")
        tama["event_message_id"] = msg.id
        await msg.edit(view=EventClickView(event_type, needed))
        safe_task(expire_event(guild, 900, event_id))
    elif event_type == "affinity":
        needed = EVENT_AFFINITY_CLICKS
        tama["event_expires"] = datetime.now(timezone.utc) + timedelta(minutes=10)
        msg = await ch.send(f"```\n✨ ═══════════════════════════════════════ ✨\n          AFFINITY BOOSTER ACTIVE!\n   {needed} Tenno must click to activate it.\n   Immediate +30 XP boost incoming!\n        Time limit: 10 minutes.\n✨ ═══════════════════════════════════════ ✨\n```")
        tama["event_message_id"] = msg.id
        await msg.edit(view=EventClickView(event_type, needed))
        safe_task(expire_event(guild, 600, event_id))
    elif event_type == "corrupted":
        # Corruption and infection are mutually exclusive
        if tama["infected"]:
            return
        needed = EVENT_CORRUPTED_CLICKS
        tama["corrupted_warned"] = True
        msg = await ch.send(
            f"```\n"
            f"☠️ ═══════════════════════════════════════ ☠️\n"
            f"           CORRUPTED ENERGY DETECTED!\n"
            f"   All stat decay accelerates!\n"
            f"   {needed} Tenno must use Rolling Guard\n"
            f"   within 10 minutes to purge it!\n"
            f"        ACT NOW, TENNO!\n"
            f"☠️ ═══════════════════════════════════════ ☠️\n"
            f"```"
        )
        tama["event_message_id"]         = msg.id
        tama["corrupted_warn_message_id"] = msg.id
        await msg.edit(view=EventClickView(event_type, needed))
        safe_task(expire_event(guild, EVENT_CORRUPTED_WINDOW_MIN * 60, event_id))
    await refresh_embed(guild)

class EventClickView(ui.View):
    def __init__(self, event_type: str, needed: int):
        super().__init__(timeout=None)
        labels = {"catalyst": "Activate Reactor", "forma": "🔧 Apply Forma", "affinity": "✨ Boost Affinity", "corrupted": "💊 Rolling Guard"}
        self.add_item(EventClickButton(labels.get(event_type, "✅ Contribute"), event_type, needed))

class EventClickButton(ui.Button):
    def __init__(self, label: str, event_type: str, needed: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"event_{event_type}")
        self.event_type = event_type
        self.needed     = needed
    async def callback(self, interaction: discord.Interaction):
        member_roles = {r.id for r in interaction.user.roles}
        if (TAMA_ASSOCIATE_ROLE_ID in member_roles or TAMA_ALLIED_ROLE_ID in member_roles) and \
           not any(r in member_roles for r in [TAMA_SOLDIER_ROLE_ID, TAMA_CRIMSON_SOUL_ROLE_ID,
                                                TAMA_GOLD_STAR_ROLE_ID, *MOD_ROLE_IDS]):
            await interaction.response.send_message(
                "⚔️ Button interactions are exclusive to members of clans in our alliance. "
                "Join the Red Ribbon Imperium or an allied clan to participate!",
                ephemeral=True, delete_after=15
            )
            return
        if tama["active_event"] != self.event_type:
            await interaction.response.send_message("This event is no longer active.", ephemeral=True, delete_after=6)
            return
        on_cd, remaining = is_on_event_cooldown(interaction.user.id)
        if on_cd:
            m, s = remaining // 60, remaining % 60
            await interaction.response.send_message(f"⏳ Event cooldown: {m}m {s}s remaining.", ephemeral=True, delete_after=6)
            return
        set_event_cooldown(interaction.user.id)
        tama["event_clicks"] += 1
        current = tama["event_clicks"]
        add_to_leaderboard(interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(f"✅ Contributed! {current}/{self.needed}", ephemeral=True, delete_after=6)
        if current >= self.needed:
            await resolve_event(self.event_type, interaction.guild)
        else:
            ch = get_tama_channel(interaction.guild)
            if ch and tama["event_message_id"]:
                try:
                    msg = await ch.fetch_message(tama["event_message_id"])
                    new_content = msg.content.replace(f"{current-1}/{self.needed}", f"{current}/{self.needed}")
                    await msg.edit(content=new_content)
                except Exception:
                    pass

async def resolve_event(event_type: str, guild):
    log_tama_event("event_resolve", action=event_type)
    ch = get_tama_channel(guild)
    tama["active_event"] = None
    if ch and tama["event_message_id"]:
        try:
            msg = await ch.fetch_message(tama["event_message_id"])
            await msg.delete()
        except Exception:
            pass
    tama["event_message_id"] = None
    if event_type == "catalyst":
        tama["event_modifier"]       = "catalyst"
        tama["event_modifier_until"] = datetime.now(timezone.utc) + timedelta(minutes=30)
        if ch:
            asyncio.create_task(delete_after(await ch.send("⚗️ **Orokin Reactor installed!** Focus XP is DOUBLED for 30 minutes."), 60))
    elif event_type == "forma":
        tama["prime_xp_multiplier"] = min(FORMA_XP_MULT_MAX, tama["prime_xp_multiplier"] + FORMA_XP_MULT_BONUS)
        tama["evo_xp"]              = 0
        tama["level"]               = 0
        if ch:
            asyncio.create_task(delete_after(await ch.send(f"🔧 **Forma applied!** Level reset. XP multiplier now x{tama["prime_xp_multiplier"]:.1f}."), 60))
    elif event_type == "affinity":
        tama["evo_xp"] += AFFINITY_BOOST_XP
        await check_level_up(guild)
        if ch:
            asyncio.create_task(delete_after(await ch.send(f"✨ **Affinity Booster activated!** +{AFFINITY_BOOST_XP} XP boost applied!"), 30))
    elif event_type == "corrupted":
        tama["corrupted_warned"] = False
        # Trigger rolling guard animation
        tama["sprite_override"]       = "rolling_guard"
        tama["sprite_override_until"] = datetime.now(timezone.utc) + timedelta(seconds=8)
        if ch:
            asyncio.create_task(delete_after(await ch.send(
                f"💊 **Rolling Guard deployed!** {wf_name()} shrugs off the Corruption. Decay returns to normal."
            ), 30))
    await refresh_embed(guild)

async def corruption_linger_timer(guild):
    """Corruption lingers for 10 minutes after failing rolling guard, then silently resolves."""
    await asyncio.sleep(600)
    if tama.get("corruption_lingering_until") and datetime.now(timezone.utc) >= tama["corruption_lingering_until"]:
        tama["corruption_lingering_until"] = None
        await refresh_embed(guild)

async def expire_event(guild, delay: int, event_id: int = -1):
    await asyncio.sleep(delay)
    if not tama["active_event"] or tama.get("active_event_id", -1) != event_id:
        return
    event_type           = tama["active_event"]
    tama["active_event"] = None
    log_tama_event("event_fail", action=event_type)
    ch = get_tama_channel(guild)
    if ch and tama["event_message_id"]:
        try:
            msg = await ch.fetch_message(tama["event_message_id"])
            await msg.delete()
        except Exception:
            pass
    tama["event_message_id"] = None
    if event_type == "corrupted":
        for k in STAT_KEYS:
            tama[k] = max(0, tama[k] - EVENT_CORRUPTED_FAIL_HIT)
        tama["corrupted_warned"]          = False
        tama["corruption_lingering_until"] = datetime.now(timezone.utc) + timedelta(minutes=10)
        if ch:
            asyncio.create_task(delete_after(await ch.send(
                f"💥 **Rolling Guard failed!** All stats -{EVENT_CORRUPTED_FAIL_HIT}. "
                f"Corruption lingers for 10 minutes — decay stays doubled. It will resolve on its own."
            ), 40))
        safe_task(corruption_linger_timer(guild))
    elif event_type == "fissure":
        if ch:
            asyncio.create_task(delete_after(await ch.send("⚗️ The Void Fissure has closed."), 15))
    else:
        if ch:
            asyncio.create_task(delete_after(await ch.send("❌ **The opportunity was missed.** The event expired without enough contributions."), 20))
    await refresh_embed(guild)

# ---------------------------
# DECAY LOOP
# ---------------------------
@tasks.loop(seconds=45)
async def idle_rotation_loop():
    """Refresh the embed every 20 seconds during idle to rotate sprites visibly."""
    if not tama["active"] or tama["dead"] or tama["paused"] or not tama["stage"] in ("warframe", "prime"):
        return
    # Only rotate during true idle — not during action overrides, sleep, infection, corruption
    if tama["sleeping"] or tama["infected"]:
        return
    if tama.get("sprite_override") and tama.get("sprite_override_until"):
        if datetime.now(timezone.utc) < tama["sprite_override_until"]:
            return
    if tama["active_event"] == "corrupted" and not tama.get("corrupted_warned"):
        return
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await refresh_embed(guild)

@tasks.loop(hours=1)
async def hourly_info_loop():
    """Post a brief command guide in the tamaframe channel every hour while a session is active."""
    if not tama["active"] or tama["dead"] or tama["completed"] or tama["paused"]:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = get_tama_channel(guild)
    if not ch:
        return
    msg = await ch.send(
        "📋 **Tamaframe Commands**\n"
        "```\n"
        "/tama-status    — your cooldown & leaderboard position\n"
        "/tama-top       — full top 10 leaderboard (only you can see it)\n"
        "/tama-ping-on   — get notified when your cooldown is ready\n"
        "/tama-ping-off  — turn off cooldown notifications\n"
        "```"
    )
    asyncio.create_task(delete_after(msg, 300))  # visible for 5 minutes

@tasks.loop(seconds=DECAY_INTERVAL)
async def decay_loop():
    if not tama["active"] or tama["paused"] or tama["dead"] or tama["completed"]:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    # Relic stage: only run fissure pity system, no stat decay
    if tama["stage"] == "relic":
        if not tama["active_event"]:
            tama["ticks_since_event"] += 1
            seconds_since = tama["ticks_since_event"] * DECAY_INTERVAL
            if seconds_since >= PITY_MIN_INTERVAL:
                tama["current_pity_chance"] = min(0.8, tama["current_pity_chance"] + PITY_INCREMENT)
            if random.random() < tama["current_pity_chance"]:
                last = tama.get("last_event_time")
                if not last or (datetime.now(timezone.utc) - last).total_seconds() > EVENT_COOLDOWN:
                    asyncio.create_task(trigger_event("fissure", guild))
        return

    decay_base     = get_config_value("DECAY_BASE")
    decay_interval = get_config_value("DECAY_INTERVAL")
    cooldown_mins  = get_config_value("GLOBAL_COOLDOWN_MINUTES")

    # Infection uses escalating decay: 5%/min * infection_count, capped at 3x
    if tama["infected"]:
        infection_mult = min(tama.get("infection_count", 1), INFECTION_DECAY_CAP_MULT)
        decay = INFECTION_DECAY_PER_MIN * (decay_interval / 60.0) * infection_mult
    else:
        decay = decay_base * tama.get("decay_multiplier", 1.0)
    corruption_lingering = tama.get("corruption_lingering_until") and datetime.now(timezone.utc) < tama["corruption_lingering_until"]
    if tama.get("decay_triple_until") and datetime.now(timezone.utc) < tama["decay_triple_until"]:
        decay *= 3
    elif (tama["active_event"] == "corrupted" and not tama["corrupted_warned"] and not tama["sleeping"]) or \
         (corruption_lingering and not tama["sleeping"]):
        decay *= 2

    # Low Rest amplifies decay on Feed and Clean — exhausted Mag can't maintain herself
    rest_pct = tama["rest"]
    if rest_pct < DECAY_REST_LOW_THRESHOLD:
        rest_decay_mult = DECAY_REST_LOW_MULT
    elif rest_pct < DECAY_REST_MID_THRESHOLD:
        rest_decay_mult = DECAY_REST_MID_MULT
    else:
        rest_decay_mult = 1.0

    if tama["sleeping"]:
        # Rest ticks up steadily to 100% by end of sleep
        ticks_in_sleep  = max(1, SLEEP_DURATION_SECONDS // decay_interval)
        elapsed_ticks   = max(1, int((datetime.now(timezone.utc) - tama["sleep_started"]).total_seconds() / decay_interval))
        ticks_remaining = max(1, ticks_in_sleep - elapsed_ticks + 1)

        # Rest always goes up, hits 100% when cycle ends
        rest_gain = max(1, (100 - tama["rest"]) // ticks_remaining)
        tama["rest"] = min(100, tama["rest"] + rest_gain)

        # Feed and Clean decay but never below 30%
        for k in ("feed", "clean", "level_stat"):
            tama[k] = max(30, tama[k] - decay)
    else:
        for k in STAT_KEYS:
            if k in ("feed", "clean"):
                # Apply rest fatigue multiplier to Feed and Clean
                tama[k] = max(0, tama[k] - int(decay * rest_decay_mult))
            else:
                tama[k] = max(0, tama[k] - decay)
    if tama["sprite_override_until"] and datetime.now(timezone.utc) > tama["sprite_override_until"]:
        tama["sprite_override"]       = None
        tama["sprite_override_until"] = None
    immunity = tama.get("infection_immunity_until")
    # Clear infection warning immediately if stats recovered
    if tama["infection_warned"] and not tama["infected"]:
        if all(tama[k] >= SICK_THRESHOLD for k in ("feed", "clean")):
            tama["infection_warned"] = False
            warn_mid = tama.get("infection_warn_message_id")
            if warn_mid:
                tama["infection_warn_message_id"] = None
                ch = get_tama_channel(guild)
                if ch:
                    try:
                        warn_msg = await ch.fetch_message(warn_mid)
                        await warn_msg.delete()
                    except Exception:
                        pass
                    m = await ch.send(f"✅ **Technocyte threat neutralised.** {wf_name()}'s vitals are stable.", delete_after=30)
    # Infection trigger — blocked during sleep, while corrupted, or while corruption is lingering
    if (not tama["sleeping"] and not tama["infected"]
            and tama["active_event"] != "corrupted"
            and not corruption_lingering
            and (not immunity or datetime.now(timezone.utc) > immunity)):
        if any(tama[k] < SICK_THRESHOLD for k in ("feed", "clean")):
            if not tama["infection_warned"]:
                tama["infection_warned"] = True
                ch = get_tama_channel(guild)
                if ch:
                    safe_task(infection_warning(guild, ch))
    # Saryn at level 30 evolution blocked — floor Feed and Clean at 1%
    # Prevents death during the infection grace period
    if (tama.get("evolution_blocked") and tama["level"] >= LEVEL_MAX
            and tama.get("current_warframe") == "saryn"
            and tama["stage"] == "warframe"):
        for k in ("feed", "clean"):
            if tama[k] < 1:
                tama[k] = 1

    # Stat death timer — if Feed, Clean or Rest stays at 0 for 30 min, instant death
    now = datetime.now(timezone.utc)
    for k in ("feed", "clean", "rest"):
        if tama[k] == 0:
            key = f"{k}_zero_since"
            if tama.get(key) is None:
                tama[key] = now
            elif (now - tama[key]).total_seconds() >= DEATH_STAT_ZERO_DURATION_SEC:
                await trigger_death(guild)
                return
        else:
            tama[f"{k}_zero_since"] = None

    # Death check
    if sum(1 for k in STAT_KEYS if tama[k] == 0) >= 2:
        await trigger_death(guild)
        return

    # Evolution condition checks — evaluate per tick when blocked at level 30
    if tama["level"] >= LEVEL_MAX and tama["stage"] in ("warframe", "prime") and not tama["dead"] and not tama["completed"]:
        wf    = tama.get("current_warframe", "mag")
        prime = tama.get("current_prime", "mag_prime")
        ch    = get_tama_channel(guild)

        # Auto-set evolution_blocked if condition exists and hasn't been met
        # This catches restores where evolution_blocked wasn't saved
        if tama["stage"] == "warframe":
            cond = WARFRAME_EVOLUTION_CONDITIONS.get(wf)
            if cond and not tama.get(cond["tracker"], False) and not tama.get("evolution_blocked"):
                tama["evolution_blocked"] = True
        wf    = tama.get("current_warframe", "mag")
        prime = tama.get("current_prime", "mag_prime")
        ch    = get_tama_channel(guild)

        if tama["stage"] == "warframe":
            cond = WARFRAME_EVOLUTION_CONDITIONS.get(wf)
            if cond and cond["type"] == "low_stats_at_max":
                # Harrow: feed, clean AND rest below 30%, but Focus at 100% — Penance
                if (tama["feed"] <= 30 and tama["clean"] <= 30 and tama["rest"] <= 30
                        and tama["level_stat"] >= 100
                        and not tama.get("harrow_condition_met")):
                    tama["harrow_condition_met"] = True
                    tama["evolution_blocked"]    = False
                    if ch:
                        if tama.get("evolution_blocked_msg_id"):
                            try:
                                msg = await ch.fetch_message(tama["evolution_blocked_msg_id"])
                                await msg.delete()
                            except Exception:
                                pass
                        tama["evolution_blocked_msg_id"] = None
                        asyncio.create_task(delete_after(await ch.send(
                            f"🙏 **{wf_name()} has given everything — but his focus never wavered.** "
                            f"Feed, Clean and Rest at 30% or below, Focus at 100%. "
                            f"Penance complete. Evolution unlocked! ⚔️✨"
                        ), 40))
                    await evolve_to_prime(guild)
                    if ch:
                        asyncio.create_task(delete_after(await ch.send(
                            "📖 **What just happened?**\n"
                            "Harrow's **Penance** demands total sacrifice. His Feed, Clean and Rest had to fall to 30% or below "
                            "while his Focus remained at 100% — he gave everything to the mission, but never lost his conviction. "
                            "That is the price of his Prime."
                        ), 600))

            elif cond and cond["type"] == "fully_infected":
                # Saryn: must become fully infected at level 30 — infection itself triggers evolution
                if tama["infected"] and not tama.get("saryn_survived_infection"):
                    tama["saryn_survived_infection"] = True
                    tama["evolution_blocked"]        = False
                    tama["infected"]                 = False
                    tama["infection_warned"]         = False
                    tama["infection_immunity_until"] = datetime.now(timezone.utc) + timedelta(minutes=INFECTION_IMMUNITY_MINUTES)
                    if ch:
                        if tama.get("evolution_blocked_msg_id"):
                            try:
                                msg = await ch.fetch_message(tama["evolution_blocked_msg_id"])
                                await msg.delete()
                            except Exception:
                                pass
                        tama["evolution_blocked_msg_id"] = None
                        asyncio.create_task(delete_after(await ch.send(
                            f"🧬 **The Technocyte claimed her — but Saryn is beyond it now.** "
                            f"The infection becomes her power. Ascending to Prime! ✨"
                        ), 40))
                    await evolve_to_prime(guild)
                    if ch:
                        asyncio.create_task(delete_after(await ch.send(
                            "📖 **What just happened?**\n"
                            "Saryn's relationship with the Technocyte is unlike any other Warframe. "
                            "Rather than fighting the infection, she had to **embrace it** — allowing herself to become fully infected at Level 30. "
                            "The corruption didn't destroy her. It became her. That is how Saryn Prime is born."
                        ), 600))
            elif cond and cond["type"] == "all_stats_thriving":
                # Yareli: all four stats at or above 80% simultaneously at level 30
                if all(tama[k] >= 80 for k in STAT_KEYS):
                    tama["yareli_prime_condition_met"] = True
                    tama["evolution_blocked"]          = False
                    if ch:
                        if tama.get("evolution_blocked_msg_id"):
                            try:
                                msg = await ch.fetch_message(tama["evolution_blocked_msg_id"])
                                await msg.delete()
                            except Exception:
                                pass
                        tama["evolution_blocked_msg_id"] = None
                        asyncio.create_task(delete_after(await ch.send(
                            f"🌊 **{wf_name()} is riding the perfect wave.** "
                            f"All stats above 80% — she's never been more alive. "
                            f"Ascending to Prime! ✨"
                        ), 40))
                    await evolve_to_prime(guild)
                    if ch:
                        asyncio.create_task(delete_after(await ch.send(
                            "📖 **What just happened?**\n"
                            "Yareli thrives in joy. Her Prime could only emerge from a state of total flourishing — "
                            "Feed, Clean, Focus and Rest all above 80% simultaneously at Level 30. "
                            "No weak links, no shortcuts. The perfect wave."
                        ), 600))
    # Pity event — blocked during sleep and while infected
    if not tama["active_event"] and not tama["sleeping"] and not tama["infected"]:
        tama["ticks_since_event"] += 1
        seconds_since = tama["ticks_since_event"] * DECAY_INTERVAL
        if seconds_since >= PITY_MIN_INTERVAL:
            tama["current_pity_chance"] = min(0.8, tama["current_pity_chance"] + PITY_INCREMENT)
        if random.random() < tama["current_pity_chance"]:
            last = tama.get("last_event_time")
            if not last or (datetime.now(timezone.utc) - last).total_seconds() > EVENT_COOLDOWN:
                if tama["stage"] == "warframe":
                    events = ["catalyst", "forma", "affinity", "corrupted"]
                    if tama["overclean_debuff"]:
                        events += ["corrupted"]
                        tama["overclean_debuff"] = False
                else:
                    events = ["catalyst", "forma", "affinity", "corrupted"]
                asyncio.create_task(trigger_event(random.choice(events), guild))
    await refresh_embed(guild)

async def trigger_death(guild):
    log_tama_event("death")
    tama["dead"]     = True
    tama["infected"] = False
    tama["sleeping"] = False
    ch = get_tama_channel(guild)
    if ch:
        # Purge all lingering bot messages before posting death screen
        await purge_channel(ch)
        tama["message_id"] = None
        # Post death embed with no buttons
        embed = build_embed()
        msg = await ch.send(embed=embed)
        tama["message_id"] = msg.id
        # Show final leaderboard before wiping it
        final_lb = build_leaderboard_text()
        await ch.send(
            f"💀 **{wf_name()} has fallen.** The Technocyte claimed {wf_obj()}. No one cared enough.\n\n"
            f"**Final standings before {wf_pronoun()} died:**\n{final_lb}\n\n"
            f"A mod must use `/tama-manage End` to close the session."
        )

# ---------------------------
# REFRESH / UTILS
# ---------------------------
_refresh_pending: bool = False
_refresh_needed:  bool = False
_last_refresh_time: datetime = datetime.now(timezone.utc) - timedelta(seconds=10)
REFRESH_DEBOUNCE_SECONDS = 3

# Control center debounce — kept tight so the CC stays in sync with the tama embed
CC_REFRESH_DEBOUNCE_SECONDS = 3
_cc_refresh_pending: bool = False
_cc_refresh_needed:  bool = False
_cc_last_refresh_time: datetime = datetime.now(timezone.utc) - timedelta(seconds=CC_REFRESH_DEBOUNCE_SECONDS)
_config_dirty: bool = False  # set True when a config override changes; cleared after group embeds update

async def refresh_embed(guild):
    global _refresh_pending, _refresh_needed, _last_refresh_time

    if _refresh_pending:
        _refresh_needed = True
        return

    now     = datetime.now(timezone.utc)
    elapsed = (now - _last_refresh_time).total_seconds()
    if elapsed < REFRESH_DEBOUNCE_SECONDS:
        _refresh_pending = True
        await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS - elapsed)
        _refresh_pending = False

    _last_refresh_time = datetime.now(timezone.utc)

    if _refresh_needed:
        _refresh_needed = False
        safe_task(refresh_embed(guild))

    # Safety: if sleep_vote_message_id is set but vote hasn't started or timed out, clear it
    if tama["sleep_vote_message_id"] and not tama["sleeping"]:
        started = tama.get("sleep_vote_started")
        if started and (datetime.now(timezone.utc) - started).total_seconds() > 300:
            tama["sleep_vote_message_id"] = None
            tama["sleep_vote_yes"]        = set()
            tama["sleep_vote_no"]         = set()
            tama["sleep_vote_started"]    = None
    channel = get_tama_channel(guild)
    if not channel:
        return
    embed = build_embed()
    view  = build_view()
    if tama["message_id"]:
        try:
            msg = channel.get_partial_message(tama["message_id"])
            await msg.edit(embed=embed, view=view)
            safe_task(refresh_control_center(guild))
            return
        except discord.NotFound:
            tama["message_id"] = None
        except Exception:
            return  # edit failed for transient reason — don't post a second embed
    msg = await channel.send(embed=embed, view=view)
    tama["message_id"] = msg.id
    save_tama_state()
    # Mirror to control center
    safe_task(refresh_control_center(guild))

async def delete_after(msg, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass

async def purge_channel(channel, limit=100):
    try:
        # Bulk purge — handles messages newer than 14 days (Discord limit)
        await channel.purge(limit=limit, check=lambda m: m.author.id == bot.user.id)
    except Exception:
        # Fallback: individual deletes (handles older messages or missing Manage Messages perm)
        async for msg in channel.history(limit=limit):
            if msg.author.id == bot.user.id:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

async def set_tama_channel_perms(channel, guild):
    try:
        # Everyone can see the channel — no opt-in needed
        await channel.set_permissions(guild.default_role,
            view_channel=True,
            send_messages=False,
            add_reactions=False,
            read_message_history=True,
            use_application_commands=False)

        # Roles that can interact with buttons
        button_roles = [
            TAMA_GOLD_STAR_ROLE_ID,
            TAMA_SOLDIER_ROLE_ID,
            TAMA_CRIMSON_SOUL_ROLE_ID,
            RECRUITER_ROLE_ID,
        ]
        for role_id in button_roles:
            role = guild.get_role(role_id)
            if role:
                await channel.set_permissions(role,
                    view_channel=True,
                    send_messages=False,
                    add_reactions=False,
                    read_message_history=True,
                    use_application_commands=False)

        # Associate and Allied — view only, same as default (already covered)
        for role_id in [TAMA_ASSOCIATE_ROLE_ID, TAMA_ALLIED_ROLE_ID]:
            role = guild.get_role(role_id)
            if role:
                await channel.set_permissions(role,
                    view_channel=True,
                    send_messages=False,
                    add_reactions=False,
                    read_message_history=True,
                    use_application_commands=False)

        # Mods — full access for commands
        for role_id in MOD_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                await channel.set_permissions(role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    use_application_commands=True,
                    manage_messages=True)

    except discord.Forbidden:
        pass

# ---------------------------
# TAMA SLASH COMMANDS
# ---------------------------
@bot.tree.command(name="tama-manage", description="Session management. Mod only.", guild=guild_obj)
@app_commands.describe(
    action="What to do",
    warframe="Warframe to spawn on Start: random, mag, harrow, saryn, yareli (default: random)",
)
@app_commands.choices(action=[
    app_commands.Choice(name="Start",   value="start"),
    app_commands.Choice(name="End",     value="end"),
    app_commands.Choice(name="Refresh", value="refresh"),
])
async def tama_manage(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    warframe: str = "random",
):
    val = action.value

    # --- START ---
    if val == "start":
        if not is_mod(interaction.user):
            await interaction.response.send_message("❌ Officer or above only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        category = guild.get_channel(TAMA_CATEGORY_ID)
        tama_ch  = None
        if tama.get("channel_id"):
            tama_ch = guild.get_channel(tama["channel_id"])
        if not tama_ch and category:
            for ch in category.text_channels:
                if "tamaframe" in ch.name:
                    tama_ch = ch
                    break
        if not tama_ch:
            try:
                tama_ch = await guild.create_text_channel(
                    name="🥚︱tamaframe",
                    category=category,
                    topic="Collective tamagotchi — click the buttons to keep the Warframe alive!",
                    reason="tama-manage start: creating permanent tamaframe channel"
                )
            except discord.Forbidden:
                await interaction.followup.send("❌ Missing permission to create channels.", ephemeral=True)
                return
        await set_tama_channel_perms(tama_ch, guild)
        await purge_channel(tama_ch)
        await asyncio.sleep(1)
        new_state               = fresh_state(preserve_leaderboard=False)
        new_state["active"]     = True
        new_state["channel_id"] = tama_ch.id
        choice = warframe.lower() if warframe else "random"
        if choice not in ("mag", "harrow", "saryn", "yareli"):
            choice = "random"
        if choice == "random":
            chosen_wf, chosen_prime = random.choice(WARFRAME_ROSTER)
        else:
            pair = next(((w, p) for w, p in WARFRAME_ROSTER if w == choice or p == choice), None)
            chosen_wf, chosen_prime = pair if pair else random.choice(WARFRAME_ROSTER)
        new_state["current_warframe"] = chosen_wf if chosen_wf else chosen_prime
        new_state["current_prime"]    = chosen_prime
        tama.update(new_state)
        tama["session_id"] = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        tama["session_start_time"] = datetime.now(timezone.utc)
        reset_config_to_defaults()  # clear overrides and restore all module constants
        log_tama_event("session_start", detail=f"warframe={chosen_wf or chosen_prime}")
        save_tama_state()
        await refresh_embed(guild)
        wf_label = chosen_prime.replace("_", " ").title() if not chosen_wf else chosen_wf.title()
        await interaction.followup.send(f"✅ Session started in {tama_ch.mention}! Warframe: **{wf_label}**.", ephemeral=True)

    # --- END ---
    elif val == "end":
        if not is_mod(interaction.user):
            await interaction.response.send_message("❌ Officer or above only.", ephemeral=True)
            return
        if not is_warlord(interaction.user) and not (tama["dead"] or tama["completed"]):
            await interaction.response.send_message("❌ The session can only be ended once the companion has fallen or been completed. Warlords can end at any time.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cid = tama.get("channel_id")
        ch  = interaction.guild.get_channel(cid) if cid else None
        if not ch:
            category = interaction.guild.get_channel(TAMA_CATEGORY_ID)
            if category:
                for c in category.text_channels:
                    if "tamaframe" in c.name:
                        ch = c
                        break
        if ch:
            await purge_channel(ch)
            try:
                await ch.edit(name="🥚︱tamaframe", topic="Collective tamagotchi — next session coming soon.")
            except Exception:
                pass
        new_state = fresh_state(preserve_leaderboard=False)
        new_state["active"]     = False
        new_state["channel_id"] = ch.id if ch else cid
        log_tama_event("session_end")
        tama.update(new_state)
        await interaction.followup.send("✅ Session ended. Channel cleared and ready for the next session.", ephemeral=True)

    # --- REFRESH ---
    elif val == "refresh":
        if not is_warlord(interaction.user):
            await interaction.response.send_message("❌ Warlord only.", ephemeral=True)
            return
        if not tama["active"]:
            await interaction.response.send_message("❌ No active tama session.", ephemeral=True)
            return
        tama["sleep_vote_message_id"] = None
        tama["sleep_vote_yes"]        = set()
        tama["sleep_vote_no"]         = set()
        tama["sleep_vote_started"]    = None
        tama["wake_vote_message_id"]  = None
        tama["sprite_override"]       = None
        tama["sprite_override_until"] = None
        bot.add_view(TamagotchiView())
        bot.add_view(SleepCounterView())
        bot.add_view(WakeVoteView())
        tama["message_id"] = None
        await interaction.response.send_message("✅ Refreshed.", ephemeral=True)
        await refresh_embed(interaction.guild)


@bot.tree.command(name="tama-status", description="Check your personal cooldown and leaderboard position.", guild=guild_obj)
async def tama_status(interaction: discord.Interaction):
    if not is_tama_member(interaction.user):
        await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        return
    on_cd, remaining = is_on_cooldown(interaction.user.id)
    m, s = remaining // 60, remaining % 60
    cd_text   = f"{m}m {s}s" if on_cd else "Ready!"
    lb        = tama["leaderboard"].get(interaction.user.id, {"clicks": 0, "train_clicks": 0})
    score     = lb["clicks"]
    rank_list = sorted(tama["leaderboard"].values(), key=lambda x: x["clicks"], reverse=True)
    position  = next((i + 1 for i, v in enumerate(rank_list) if v == lb), "—")
    text = f"Cooldown: {cd_text}\nTotal score: {score} pts\nLeaderboard position: #{position}"
    await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True, delete_after=15)

class LeaderboardView(ui.View):
    def __init__(self, entries, page=0, per_page=10):
        super().__init__(timeout=120)
        self.entries   = entries
        self.page      = page
        self.per_page  = per_page
        self.max_page  = max(0, (len(entries) - 1) // per_page)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.max_page
        self.page_btn.label    = f"{self.page + 1}/{self.max_page + 1}"

    def build_content(self):
        start   = self.page * self.per_page
        chunk   = self.entries[start:start + self.per_page]
        medals  = ["🥇","🥈","🥉"] + [f"{i}." for i in range(4, len(self.entries) + 1)]
        lines   = []
        for i, e in enumerate(chunk):
            rank = start + i
            lines.append(f"{medals[rank]} {e['name']:<16} {e['clicks']} pts")
        return f"🏆 **Top Tenno** — Page {self.page+1}/{self.max_page+1}\n```\n" + "\n".join(lines) + "\n```"

    @ui.button(emoji="◀", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)

    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, custom_id="lb_page", disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: ui.Button):
        pass

    @ui.button(emoji="▶", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)

@bot.tree.command(name="tama-top", description="Show the full contributor leaderboard.", guild=guild_obj)
async def tama_top(interaction: discord.Interaction):
    if not is_tama_member(interaction.user):
        await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        return
    if not tama["leaderboard"]:
        await interaction.response.send_message("No contributions recorded this session.", ephemeral=True, delete_after=10)
        return
    entries = sorted(tama["leaderboard"].values(), key=lambda x: x["clicks"], reverse=True)
    view    = LeaderboardView(entries)
    suffix  = ""
    if tama.get("dead"):       suffix = "\n💀 Session ended — Companion fell."
    elif tama.get("completed"): suffix = "\n✨ Session complete — Companion fully evolved."
    await interaction.response.send_message(view.build_content() + suffix, view=view, ephemeral=True)

@bot.tree.command(name="tama-restore", description="Restore tama session from a save file. Mod only.", guild=guild_obj)
@app_commands.describe(file="Upload the tama_save.json file generated by /tama-save")
async def tama_restore(interaction: discord.Interaction, file: discord.Attachment):
    if not is_warlord(interaction.user):
        await interaction.response.send_message("❌ Warlord only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    if not file.filename.endswith(".json"):
        await interaction.followup.send("❌ Please upload a `.json` file generated by `/tama-save`.", ephemeral=True)
        return

    try:
        raw = await file.read()
        save_data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        await interaction.followup.send(f"❌ Could not read the file: {e}", ephemeral=True)
        return

    tama.update({
        "active":                   True,
        "stage":                    save_data["stage"],
        "current_warframe":         save_data.get("current_warframe", "mag"),
        "current_prime":            save_data.get("current_prime", "mag_prime"),
        "dead":                     save_data["dead"],
        "completed":                save_data["completed"],
        "paused":                   save_data["paused"],
        "channel_id":               save_data["channel_id"],
        "reactants":                save_data["reactants"],
        "reactant_progress":        save_data["reactant_progress"],
        "feed":                     save_data["feed"],
        "clean":                    save_data["clean"],
        "level_stat":               save_data["level_stat"],
        "rest":                     save_data["rest"],
        "level":                    save_data["level"],
        "evo_xp":                   save_data["evo_xp"],
        "prime_xp_multiplier":      save_data["prime_xp_multiplier"],
        "decay_multiplier":         save_data.get("decay_multiplier", 1.0),
        "infected":                 save_data["infected"],
        "infection_immunity_until": (
            datetime.fromisoformat(save_data["infection_immunity_until"])
            if save_data.get("infection_immunity_until") else None
        ),
        "consecutive_trains":       save_data["consecutive_trains"],
        "sleep_streak":             save_data.get("sleep_streak", 0),
        "evolution_blocked":        save_data.get("evolution_blocked", False),
        "harrow_condition_met":     save_data.get("harrow_condition_met", False),
        "saryn_survived_infection": save_data.get("saryn_survived_infection", False),
        "yareli_prime_condition_met": save_data.get("yareli_prime_condition_met", False),
        "infection_count":          save_data.get("infection_count", 0),
        "infection_penalty_until":  None,
        "consecutive_feeds":        save_data.get("consecutive_feeds", 0),
        "consecutive_cleans":       save_data.get("consecutive_cleans", 0),
        "groggy_until":             None,
        "action_progress":          save_data["action_progress"],
        "leaderboard":              {int(k): v for k, v in save_data["leaderboard"].items()},
        "message_id":               None,
        # Reset all transient state
        "sleeping":                 False,
        "sleep_started":            None,
        "sleep_vote_yes":           set(),
        "sleep_vote_no":            set(),
        "sleep_vote_message_id":    None,
        "wake_vote_message_id":     None,
        "sleep_message_id":         None,
        "wake_yes":                 set(),
        "wake_no":                  set(),
        "active_event":             None,
        "active_event_id":          -1,
        "event_message_id":         None,
        "event_clicks":             0,
        "corrupted_warned":         False,
        "infection_warned":         False,
        "infection_warn_message_id": None,
        "rolling_guard_clicks":     0,
        "rolling_guard_message_id": None,
        "cooldowns":                {},
    })

    if save_data.get("channel_id"):
        tama["channel_id"] = save_data["channel_id"]

    # Pause decay loop for the duration of restore to prevent it posting a
    # competing embed into the freshly-purged channel while message_id is None.
    tama["paused"] = True
    ch = get_tama_channel(interaction.guild)
    if ch:
        await purge_channel(ch)
        await asyncio.sleep(1)

    # Restore config overrides from save — resets to defaults first, then applies saved overrides
    reset_config_to_defaults()
    for key, value in save_data.get("config_overrides", {}).items():
        set_config_override(key, value)

    await refresh_embed(interaction.guild)
    tama["paused"] = save_data.get("paused", False)  # restore original paused state
    await interaction.followup.send(
        f"✅ Restored! Stage: **{tama['stage']}** | Level: **{tama['level']}/{LEVEL_MAX}** | "
        f"Feed: **{tama['feed']}** | Clean: **{tama['clean']}** | Focus: **{tama['level_stat']}** | Rest: **{tama['rest']}**",
        ephemeral=True,
    )


# ---------------------------
# EVENTS
# ---------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # Cooldown — send ephemeral message instead of logging
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        minutes = int(error.retry_after // 60)
        seconds = int(error.retry_after % 60)
        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        try:
            await interaction.response.send_message(
                f"⏳ You're on cooldown. Try again in **{time_str}**.",
                ephemeral=True, delete_after=10
            )
        except Exception:
            pass
        return
    # Suppress unknown interaction errors (10062)
    if isinstance(error, discord.app_commands.CommandInvokeError):
        if isinstance(error.original, discord.NotFound) and error.original.code == 10062:
            return
    # Log everything else
    import traceback
    traceback.print_exception(type(error), error, error.__traceback__)

def handle_task_exception(loop, context):
    """Global handler for unhandled asyncio task exceptions."""
    exc = context.get("exception")
    msg = context.get("message", "Unknown error")
    if exc:
        import traceback
        print(f"[TASK ERROR] {msg}: {exc}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        print(f"[TASK ERROR] {msg}")

def safe_task(coro):
    """Wrap a coroutine in a task with error logging."""
    async def _wrapper():
        try:
            await coro
        except Exception as e:
            import traceback
            print(f"[SAFE TASK ERROR] {e}")
            traceback.print_exc()
    return asyncio.create_task(_wrapper())

RECRUITER_PING_RESPONSES = [
    "WHO PINGED THE RECRUITER?! Use **/joinclan**. Just type it. Hit enter. Done. It's not complicated.",
    "Oh wonderful, another ping. Did you try **/joinclan**? No? Well try it now.",
    "The recruiter has been summoned. For what purpose? Unclear. What IS clear is that you should use **/joinclan**.",
    "Congratulations. You pinged a role instead of reading either the DM our bot sent you OR the welcome channel. Type **/joinclan** and submit your IGN. Revolutionary concept, I know.",
    "Every time someone pings this role instead of using the command, a Warframe dies. Use **/joinclan**.",
    "The answer to whatever you're about to ask is: **/joinclan**. Issue it, fill it in, press enter. You're welcome.",
    "Remarkable. You found the ping button. Now find the **/joinclan** command and use that instead.",
    "Not a DM. Not a ping. **/joinclan**. The command exists for a reason. Please acknowledge its existence.",
    "General White here. I don't know why you pinged. I don't want to know. Use **/joinclan** and get it over with.",
    "Your message has been received. Your message has been ignored. Please use **/joinclan** like everyone else.",
    "If you are trying to join the clan: **/joinclan**. If you are not trying to join the clan: **/joinclan** anyway, it will be funny.",
    "Pinging the recruiter does not summon a human being to your aid. **/joinclan** does the same thing faster and doesn't bother anyone.",
    "I have been watching this channel for a long time. The ping never helps. **/joinclan** always helps. Choose wisely.",
    "The recruiter role is not a chat feature. **/joinclan** is. Use the correct tool.",
    "Attention soldier. The clan application process is: type **/joinclan**, press enter, fill in your IGN, press enter. Not: ping the recruiter and wait.",
    "This is not how you join. This is not how you get help. **/joinclan** is how you join. **/joinclan** is how you get help.",
    "Red Ribbon Imperium has a state of the art application system. It is called **/joinclan**. Please use it.",
    "I admire the boldness. Pinging the role publicly, for all to see. Next time just announce you can't read for shit. Now, pretend you're not an idiot, type **/joinclan**, press enter.",
    "You have successfully alerted the recruiter role. The recruiter role cannot respond to pings. **/joinclan** can.",
    "Type **/joinclan**. Your IGN goes in the boxes. You press submit. A recruiter handles it. This is the entire process. No pings required.",
]

_recruiter_pool: list = []

def get_recruiter_response() -> str:
    global _recruiter_pool
    if not _recruiter_pool:
        _recruiter_pool = RECRUITER_PING_RESPONSES.copy()
        random.shuffle(_recruiter_pool)
    return _recruiter_pool.pop()

# ================================================
# TAMA CONTROL CENTER
# ================================================

CONTROL_CENTER_CATEGORY_ID = 993970977610399874
CONTROL_OVERRIDES_PATH     = "tama_config_overrides.json"

# Roles that can use the control center
CONTROL_MOD_ROLES = {OFFICER_ROLE_ID, GENERAL_ROLE_ID, EMISSARY_ROLE_ID, WARLORD_ROLE_ID}

def load_overrides() -> dict:
    if os.path.exists(CONTROL_OVERRIDES_PATH):
        with open(CONTROL_OVERRIDES_PATH, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_overrides(overrides: dict):
    with open(CONTROL_OVERRIDES_PATH, "w") as f:
        json.dump(overrides, f, indent=2)

def get_config_value(key: str):
    """Get current config value — always reflects active overrides."""
    import tama_config as _cfg
    return globals().get(key, getattr(_cfg, key, None))

def set_config_override(key: str, value):
    """Save override to disk and immediately update the module-level constant."""
    global _config_dirty
    overrides = load_overrides()
    overrides[key] = value
    save_overrides(overrides)
    import tama_config as _cfg
    if hasattr(_cfg, key):
        globals()[key] = value
    # DECAY_INTERVAL requires an explicit loop change — globals() update alone isn't enough
    if key == "DECAY_INTERVAL" and decay_loop.is_running():
        decay_loop.change_interval(seconds=value)
    _config_dirty = True

def reset_config_override(key: str):
    """Remove override from disk and restore the module-level constant to config default."""
    global _config_dirty
    overrides = load_overrides()
    overrides.pop(key, None)
    save_overrides(overrides)
    import tama_config as _cfg
    if hasattr(_cfg, key):
        globals()[key] = getattr(_cfg, key)
    if key == "DECAY_INTERVAL" and decay_loop.is_running():
        decay_loop.change_interval(seconds=getattr(_cfg, "DECAY_INTERVAL"))
    _config_dirty = True

def apply_persisted_overrides():
    """Restore any overrides saved before the last redeploy. Call after decay_loop.start()."""
    import tama_config as _cfg
    overrides = load_overrides()
    for key, value in overrides.items():
        if hasattr(_cfg, key):
            globals()[key] = value
    if "DECAY_INTERVAL" in overrides and decay_loop.is_running():
        decay_loop.change_interval(seconds=overrides["DECAY_INTERVAL"])

def reset_config_to_defaults():
    """Clear overrides file and reset all module-level constants to tama_config.py defaults."""
    global _config_dirty
    import tama_config as _cfg
    save_overrides({})
    for key in vars(_cfg):
        if not key.startswith("_") and key in globals():
            globals()[key] = getattr(_cfg, key)
    if decay_loop.is_running():
        decay_loop.change_interval(seconds=_cfg.DECAY_INTERVAL)
    _config_dirty = True

# ---------------------------
# CONFIG PANEL GROUPS
# ---------------------------
CONFIG_GROUPS = [
    ("⚙️ Decay", [
        ("DECAY_BASE",              "Base decay per tick (%)",          "float"),
        ("DECAY_INTERVAL",          "Seconds per tick",                 "int"),
        ("DECAY_CORRUPTED_MULT",    "Corrupted decay multiplier",       "float"),
        ("DECAY_TRIPLE_MULT",       "Triple decay multiplier",          "float"),
        ("DECAY_TRIPLE_DURATION_MIN","Triple decay duration (min)",     "int"),
        ("DECAY_REST_MID_THRESHOLD","Rest mid neglect threshold (%)",   "int"),
        ("DECAY_REST_LOW_THRESHOLD","Rest low neglect threshold (%)",   "int"),
        ("DECAY_REST_MID_MULT",     "Rest mid decay multiplier",        "float"),
        ("DECAY_REST_LOW_MULT",     "Rest low decay multiplier",        "float"),
    ]),
    ("⏱️ Cooldowns & Thresholds", [
        ("GLOBAL_COOLDOWN_MINUTES", "Global cooldown (minutes)",        "float"),
        ("ACTION_THRESHOLD",        "Clicks to trigger action",         "int"),
        ("RELIC_CLICK_THRESHOLD",   "Clicks per reactant",              "int"),
        ("RELIC_REACTANTS_NEEDED",  "Reactants to crack relic",         "int"),
    ]),
    ("😴 Sleep", [
        ("SLEEP_DURATION_SECONDS",  "Sleep duration (seconds)",         "int"),
        ("SLEEP_MIN_BEFORE_WAKE",   "Min seconds before wake vote",     "int"),
        ("SLEEP_VOTE_TIMEOUT",      "Vote timeout (seconds)",           "int"),
        ("SLEEP_YES_NEEDED",        "Yes votes to sleep",               "int"),
        ("SLEEP_NO_NEEDED",         "No votes to cancel",               "int"),
        ("WAKE_YES_NEEDED",         "Yes votes to wake early",          "int"),
        ("WAKE_EARLY_STAT_PENALTY", "Early wake stat penalty",          "int"),
    ]),
    ("☣️ Infection", [
        ("SICK_THRESHOLD",              "Sick threshold (%)",               "int"),
        ("INFECTION_WARN_DURATION_SEC", "Warning grace period (seconds)",   "int"),
        ("INFECTION_IMMUNITY_MINUTES",  "Immunity after cure (minutes)",    "int"),
        ("ROLLING_GUARD_CLICKS_NEEDED", "Rolling Guard clicks needed",      "int"),
        ("ROLLING_GUARD_WINDOW_MIN",    "Rolling Guard window (minutes)",   "int"),
        ("ROLLING_GUARD_FAIL_STAT_HIT", "Fail stat penalty",                "int"),
        ("ROLLING_GUARD_SUCCESS_BONUS", "Cure stat bonus",                  "int"),
    ]),
    ("📈 XP & Levelling", [
        ("WARFRAME_XP_PER_LEVEL",   "Warframe XP per level",            "int"),
        ("PRIME_XP_PER_LEVEL",      "Prime XP per level",               "int"),
        ("TRAIN_EVO_XP",            "XP gained per Train action",       "int"),
        ("AFFINITY_BOOST_XP",       "Affinity Booster XP bonus",        "int"),
        ("FORMA_XP_MULT_BONUS",     "Forma XP multiplier bonus",        "float"),
        ("FORMA_XP_MULT_MAX",       "Max Forma XP multiplier",          "float"),
    ]),
    ("⚡ Events", [
        ("EVENT_CATALYST_CLICKS",   "Reactor clicks needed",            "int"),
        ("EVENT_FORMA_CLICKS",      "Forma clicks needed",              "int"),
        ("EVENT_AFFINITY_CLICKS",   "Affinity clicks needed",           "int"),
        ("EVENT_CORRUPTED_CLICKS",  "Corruption purify clicks",         "int"),
        ("EVENT_ROLLING_GUARD_CLICKS","Rolling Guard event clicks",     "int"),
        ("PITY_BASE_CHANCE",        "Base pity event chance",           "float"),
        ("PITY_INCREMENT",          "Pity increment per tick",          "float"),
        ("EVENT_COOLDOWN",          "Event cooldown (seconds)",         "int"),
    ]),
    ("🍖 Stats", [
        ("FEED_GAIN",               "Feed stat gain",                   "int"),
        ("FEED_CLEAN_COST",         "Feed: Clean cost",                 "int"),
        ("CLEAN_GAIN",              "Clean stat gain",                  "int"),
        ("CLEAN_REST_COST",         "Clean: Rest cost",                 "int"),
        ("TRAIN_STAT_GAIN",         "Train: Focus gain",                "int"),
        ("TRAIN_FEED_COST",         "Train: Feed cost",                 "int"),
        ("TRAIN_CLEAN_COST",        "Train: Clean cost",                "int"),
        ("TRAIN_REST_COST",         "Train: Rest cost (normal)",        "int"),
        ("TRAIN_BLOCK_REST_THRESHOLD","Train block Rest threshold (%)", "int"),
        ("DEATH_STAT_ZERO_DURATION_SEC","Stat-at-zero death timer (s)","int"),
    ]),
]

# ---------------------------
# CONTROL CENTER STATE
# ---------------------------
control_center = {
    "channel_id":      None,
    "message_id":      None,  # main control embed
    "config_msg_ids":  {},    # group_name: message_id
}

# ---------------------------
# CONTROL CENTER HELPERS
# ---------------------------
def is_control_mod(member: discord.Member) -> bool:
    return any(r.id in CONTROL_MOD_ROLES for r in member.roles) or member.guild_permissions.administrator

def get_control_channel(guild) -> discord.TextChannel:
    cid = control_center.get("channel_id")
    if cid:
        return guild.get_channel(cid)
    return None

def build_control_embed(guild) -> discord.Embed:
    """Build the main control center embed mirroring tama state plus analytics."""
    if not tama["active"]:
        embed = discord.Embed(
            title       = "🎮 Tamaframe Control Center",
            description = "No active session. Use **Start** to begin.",
            color       = discord.Color.dark_gray(),
        )
        return embed

    stage   = tama["stage"]
    dead    = tama["dead"]
    completed = tama.get("completed", False)

    color = discord.Color.dark_gray() if dead else (
        discord.Color.gold() if completed else (
            discord.Color.from_rgb(0, 180, 80) if tama["infected"] else discord.Color.blue()
        )
    )

    wf = wf_name()
    title = f"🎮 Control Center — {wf}"
    embed = discord.Embed(title=title, color=color)

    # --- TAMA STATE ---
    if stage == "relic":
        embed.add_field(
            name="🥚 Relic",
            value=f"Reactants: **{tama['reactants']}/{RELIC_REACTANTS_NEEDED}**",
            inline=False,
        )
    elif not dead:
        def bar(v, l=10):
            f = round((v/100)*l)
            return "█"*f + "░"*(l-f)

        stats = (
            f"🍖 Feed   {bar(tama['feed'])}  **{tama['feed']}%**\n"
            f"🧼 Clean  {bar(tama['clean'])}  **{tama['clean']}%**\n"
            f"⚡ Focus  {bar(tama['level_stat'])}  **{tama['level_stat']}%**\n"
            f"😴 Rest   {bar(tama['rest'])}  **{tama['rest']}%**"
        )
        embed.add_field(name="📊 Stats", value=stats, inline=False)

        xp_needed = get_xp_needed()
        xp_bar = round((tama['evo_xp'] / xp_needed) * 10) if xp_needed else 0
        embed.add_field(
            name="📈 Level",
            value=f"**{tama['level']}/{LEVEL_MAX}** — XP: {tama['evo_xp']}/{xp_needed} {'█'*xp_bar}{'░'*(10-xp_bar)}",
            inline=False,
        )

    # --- STATUS FLAGS ---
    flags = []
    if dead:             flags.append("💀 **DEAD**")
    if completed:        flags.append("✨ **COMPLETED**")
    if tama["paused"]:   flags.append("⏸️ Paused")
    if tama["sleeping"]: flags.append("😴 Sleeping")
    if tama["infected"]: flags.append("☣️ Infected")
    if tama.get("active_event"): flags.append(f"⚡ Event: `{tama['active_event']}`")
    dm = tama.get("decay_multiplier", 1.0)
    if dm != 1.0:        flags.append(f"📉 Decay x{dm:.2f}")
    if flags:
        embed.add_field(name="⚠️ Status", value="  ".join(flags), inline=False)

    # --- ANALYTICS ---
    now = datetime.now(timezone.utc)
    session_start = tama.get("session_start_time")
    if session_start:
        elapsed = int((now - session_start).total_seconds())
        h, m = elapsed // 3600, (elapsed % 3600) // 60
        duration_str = f"{h}h {m}m" if h else f"{m}m"
    else:
        duration_str = "Unknown"

    # Active members — unique clickers in last 30 min
    cutoff = now - timedelta(minutes=30)
    active_count = sum(
        1 for uid, cd in tama["cooldowns"].items()
        if cd and cd > cutoff
    )

    total_clicks = sum(
        v.get("clicks", 0) + v.get("train_clicks", 0)
        for v in tama["leaderboard"].values()
    )

    # Effective decay rate
    effective_decay = get_config_value("DECAY_BASE") * tama.get("decay_multiplier", 1.0)
    if tama.get("decay_triple_until") and now < tama["decay_triple_until"]:
        effective_decay *= DECAY_TRIPLE_MULT
    elif tama.get("active_event") == "corrupted":
        effective_decay *= DECAY_CORRUPTED_MULT

    # Time until stats hit zero
    def ticks_to_zero(stat_val, decay):
        if decay <= 0: return "∞"
        ticks = stat_val / decay
        secs  = int(ticks * get_config_value("DECAY_INTERVAL"))
        m, s  = secs // 60, secs % 60
        return f"{m}m {s}s"

    if stage not in ("relic",) and not dead:
        ttz = (
            f"Feed: {ticks_to_zero(tama['feed'], effective_decay)} | "
            f"Clean: {ticks_to_zero(tama['clean'], effective_decay)} | "
            f"Focus: {ticks_to_zero(tama['level_stat'], effective_decay)} | "
            f"Rest: {ticks_to_zero(tama['rest'], effective_decay)}"
        )
        embed.add_field(name="⏱️ Time to Zero (est.)", value=ttz, inline=False)

    analytics = (
        f"Session: **{duration_str}** | "
        f"Active (30m): **{active_count}** | "
        f"Total clicks: **{total_clicks}** | "
        f"Decay/tick: **{effective_decay:.1f}%**\n"
        f"Cooldown: **{int(get_config_value('GLOBAL_COOLDOWN_MINUTES'))}m {int((get_config_value('GLOBAL_COOLDOWN_MINUTES') % 1) * 60)}s** | "
        f"Tick interval: **{int(get_config_value('DECAY_INTERVAL') // 60)}m {int(get_config_value('DECAY_INTERVAL') % 60)}s**"
    )
    embed.add_field(name="📊 Analytics", value=analytics, inline=False)

    # Active overrides
    overrides = load_overrides()
    if overrides:
        import tama_config as _cfg
        override_lines = []
        for k, v in overrides.items():
            default = getattr(_cfg, k, "?")
            override_lines.append(f"`{k}`: **{v}** *(default: {default})*")
        embed.add_field(name="⚠️ Active Overrides", value="\n".join(override_lines), inline=False)

    # Top 3
    top = sorted(tama["leaderboard"].values(), key=lambda x: x["clicks"], reverse=True)[:3]
    if top:
        medals = ["🥇", "🥈", "🥉"]
        lb_text = "\n".join(f"{medals[i]} {e['name']} — {e['clicks']} pts" for i, e in enumerate(top))
        embed.add_field(name="🏆 Top 3", value=lb_text, inline=False)

    embed.set_footer(text=f"⚠️ Estimate only — doesn't account for player actions | Updated {now.strftime('%H:%M:%S')} UTC")
    embed.timestamp = now
    return embed

def build_config_group_embed(group_name: str, fields: list) -> discord.Embed:
    """Build embed for a config group showing current values."""
    overrides = load_overrides()
    import tama_config as _cfg
    embed = discord.Embed(title=group_name, color=discord.Color.dark_gray())
    lines = []
    for key, label, _ in fields:
        default = getattr(_cfg, key, "N/A")
        current = overrides.get(key, default)
        flag    = " ⚠️" if key in overrides else ""
        lines.append(f"`{label}`: **{current}**{flag}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="⚠️ = overridden from default | Warlord only to edit")
    return embed

# ---------------------------
# CONTROL CENTER VIEWS
# ---------------------------
class ControlSessionView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check(self, interaction: discord.Interaction, warlord_only: bool = False) -> bool:
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        if warlord_only and not is_warlord(member):
            await interaction.response.send_message("❌ Warlord only.", ephemeral=True)
            return False
        if not is_control_mod(member):
            await interaction.response.send_message("❌ Officer or above only.", ephemeral=True)
            return False
        return True

    @ui.button(label="▶ Start", style=discord.ButtonStyle.success, custom_id="cc_start", row=0)
    async def cc_start(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active"]:
            await interaction.response.send_message("❌ Session already active.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Starting session…", ephemeral=True)
        guild = interaction.guild
        category  = guild.get_channel(TAMA_CATEGORY_ID)
        tama_ch   = guild.get_channel(tama.get("channel_id")) if tama.get("channel_id") else None
        if not tama_ch and category:
            for ch in category.text_channels:
                if "tamaframe" in ch.name:
                    tama_ch = ch
                    break
        if not tama_ch:
            try:
                tama_ch = await guild.create_text_channel(
                    name="🥚︱tamaframe", category=category,
                    reason="tama-control-setup: creating tamaframe channel"
                )
            except Exception:
                return
        await set_tama_channel_perms(tama_ch, guild)
        await purge_channel(tama_ch)
        new_state               = fresh_state(preserve_leaderboard=False)
        new_state["active"]     = True
        new_state["channel_id"] = tama_ch.id
        wf, prime               = random.choice(WARFRAME_ROSTER)
        new_state["current_warframe"] = wf
        new_state["current_prime"]    = prime
        new_state["session_start_time"] = datetime.now(timezone.utc)
        tama.update(new_state)
        tama["session_id"] = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        new_state["session_start_time"] = datetime.now(timezone.utc)
        reset_config_to_defaults()  # clear overrides and restore all module constants
        log_tama_event("session_start", detail=f"warframe={wf}")
        save_tama_state()
        await refresh_embed(guild)
        await refresh_control_center(guild)

    @ui.button(label="⏹ End", style=discord.ButtonStyle.danger, custom_id="cc_end", row=0)
    async def cc_end(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not is_warlord(member) and not (tama["dead"] or tama.get("completed", False)):
            await interaction.response.send_message("❌ Session can only be ended after death or completion.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Ending session…", ephemeral=True)
        cid = tama.get("channel_id")
        ch  = interaction.guild.get_channel(cid) if cid else None
        if ch:
            await purge_channel(ch)
            try: await ch.edit(name="🥚︱tamaframe", topic="Next session coming soon.")
            except Exception: pass
        log_tama_event("session_end")
        new_state = fresh_state(preserve_leaderboard=False)
        new_state["active"]     = False
        new_state["channel_id"] = cid
        tama.update(new_state)
        await refresh_control_center(interaction.guild)

    @ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="cc_refresh", row=0)
    async def cc_refresh(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction, warlord_only=True): return
        await interaction.response.send_message("✅ Refreshing…", ephemeral=True)
        # Pause decay so it cannot post a competing embed while message_id is None
        tama["paused"] = True
        ch = get_tama_channel(interaction.guild)
        if ch:
            await purge_channel(ch)
            await asyncio.sleep(1)
        tama["message_id"] = None
        await refresh_embed(interaction.guild)
        tama["paused"] = False
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="💾 Save", style=discord.ButtonStyle.secondary, custom_id="cc_save", row=0)
    async def cc_save(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction, warlord_only=True): return
        if not tama["active"]:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        import io
        save = {
            "stage": tama["stage"], "current_warframe": tama.get("current_warframe","mag"),
            "current_prime": tama.get("current_prime","mag_prime"), "dead": tama["dead"],
            "completed": tama.get("completed",False), "paused": tama["paused"],
            "channel_id": tama["channel_id"], "reactants": tama["reactants"],
            "reactant_progress": tama["reactant_progress"], "feed": tama["feed"],
            "clean": tama["clean"], "level_stat": tama["level_stat"], "rest": tama["rest"],
            "level": tama["level"], "evo_xp": tama["evo_xp"],
            "prime_xp_multiplier": tama["prime_xp_multiplier"],
            "decay_multiplier": tama.get("decay_multiplier",1.0),
            "infected": tama["infected"],
            "infection_immunity_until": tama["infection_immunity_until"].isoformat() if tama.get("infection_immunity_until") else None,
            "consecutive_trains": tama["consecutive_trains"],
            "sleep_streak": tama.get("sleep_streak",0),
            "evolution_blocked": tama.get("evolution_blocked",False),
            "harrow_condition_met": tama.get("harrow_condition_met",False),
            "saryn_survived_infection": tama.get("saryn_survived_infection",False),
            "yareli_prime_condition_met": tama.get("yareli_prime_condition_met",False),
            "infection_count":          tama.get("infection_count", 0),
            "infection_penalty_until":  tama["infection_penalty_until"].isoformat() if tama.get("infection_penalty_until") else None,
            "consecutive_feeds":        tama.get("consecutive_feeds", 0),
            "consecutive_cleans":       tama.get("consecutive_cleans", 0),
            "action_progress": tama["action_progress"],
            "leaderboard": {str(k): v for k,v in tama["leaderboard"].items()},
            "config_overrides": load_overrides(),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        payload = json.dumps(save, indent=2).encode("utf-8")
        wf_key  = "current_prime" if tama["stage"] == "prime" else "current_warframe"
        wf_label = tama.get(wf_key, "mag").replace("_"," ").title()
        summary = (
            f"📦 **Tamaframe Save — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}**\n"
            f"```\nStage: {tama['stage'].capitalize()} ({wf_label})\n"
            f"Level: {tama['level']}/{LEVEL_MAX}\nFeed: {tama['feed']}%\n"
            f"Clean: {tama['clean']}%\nFocus: {tama['level_stat']}%\nRest: {tama['rest']}%\n"
            f"XP: {tama['evo_xp']}/{get_xp_needed()}\nInfected: {'Yes' if tama['infected'] else 'No'}\n```\n"
            f"Use `/tama-restore` and upload this file to resume."
        )
        await interaction.response.send_message("📬 Sending save file to your DMs…", ephemeral=True)
        try:
            file = discord.File(fp=BytesIO(payload), filename="tama_save.json")
            await interaction.user.send(content=summary, file=file)
        except discord.Forbidden:
            await interaction.followup.send("❌ Could not DM you.", ephemeral=True)

    @ui.button(label="☠️ Kill", style=discord.ButtonStyle.danger, custom_id="cc_kill", row=0)
    async def cc_kill(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction, warlord_only=True): return
        if not tama["active"] or tama["dead"]:
            await interaction.response.send_message("❌ Nothing to kill.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Kill triggered.", ephemeral=True)
        await trigger_death(interaction.guild)
        await refresh_control_center(interaction.guild)


class ControlActionView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check(self, interaction: discord.Interaction) -> bool:
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not is_control_mod(member):
            await interaction.response.send_message("❌ Officer or above only.", ephemeral=True)
            return False
        if not tama["active"]:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return False
        return True

    @ui.button(label="🍖 Feed",  style=discord.ButtonStyle.success,   custom_id="cc_act_feed",  row=0)
    async def act_feed(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_message("✅ Triggered: Feed", ephemeral=True)
        safe_task(trigger_action("feed", interaction.guild))

    @ui.button(label="🧼 Clean", style=discord.ButtonStyle.primary,   custom_id="cc_act_clean", row=0)
    async def act_clean(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_message("✅ Triggered: Clean", ephemeral=True)
        safe_task(trigger_action("clean", interaction.guild))

    @ui.button(label="⚡ Train", style=discord.ButtonStyle.danger,    custom_id="cc_act_train", row=0)
    async def act_train(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_message("✅ Triggered: Train", ephemeral=True)
        safe_task(trigger_action("train", interaction.guild))

    @ui.button(label="😴 Sleep", style=discord.ButtonStyle.secondary, custom_id="cc_act_sleep", row=0)
    async def act_sleep(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["sleeping"]:
            await interaction.response.send_message("❌ Already sleeping.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Sleep", ephemeral=True)
        await start_sleep(interaction.guild)

    @ui.button(label="☀️ Wake",  style=discord.ButtonStyle.secondary, custom_id="cc_act_wake",  row=0)
    async def act_wake(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if not tama["sleeping"]:
            await interaction.response.send_message("❌ Not sleeping.", ephemeral=True)
            return
        tama["sleeping"] = False
        tama["wake_yes"] = set(); tama["wake_no"] = set()
        ch = get_tama_channel(interaction.guild)
        if ch:
            for mk in ("sleep_message_id","wake_vote_message_id"):
                if tama.get(mk):
                    try: await (await ch.fetch_message(tama[mk])).delete()
                    except Exception: pass
                    tama[mk] = None
        await interaction.response.send_message("✅ Woken up.", ephemeral=True)
        await notify_wake_pings(interaction.guild)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="⚗️ Reactor",  style=discord.ButtonStyle.primary,   custom_id="cc_ev_cat",  row=1)
    async def ev_cat(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"]:
            await interaction.response.send_message(f"❌ Event already active: `{tama['active_event']}`", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Reactor", ephemeral=True)
        await trigger_event("catalyst", interaction.guild)

    @ui.button(label="🔧 Forma",    style=discord.ButtonStyle.primary,   custom_id="cc_ev_forma", row=1)
    async def ev_forma(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"]:
            await interaction.response.send_message(f"❌ Event already active: `{tama['active_event']}`", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Forma", ephemeral=True)
        await trigger_event("forma", interaction.guild)

    @ui.button(label="✨ Affinity", style=discord.ButtonStyle.primary,   custom_id="cc_ev_aff",  row=1)
    async def ev_aff(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"]:
            await interaction.response.send_message(f"❌ Event already active: `{tama['active_event']}`", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Affinity", ephemeral=True)
        await trigger_event("affinity", interaction.guild)

    @ui.button(label="⚠️ Corrupted",style=discord.ButtonStyle.danger,    custom_id="cc_ev_cor",  row=1)
    async def ev_cor(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"]:
            await interaction.response.send_message(f"❌ Event already active: `{tama['active_event']}`", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Corrupted", ephemeral=True)
        await trigger_event("corrupted", interaction.guild)

    @ui.button(label="🌀 Fissure",  style=discord.ButtonStyle.primary,   custom_id="cc_ev_fiss", row=1)
    async def ev_fiss(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"]:
            await interaction.response.send_message(f"❌ Event already active: `{tama['active_event']}`", ephemeral=True)
            return
        await interaction.response.send_message("✅ Triggered: Fissure", ephemeral=True)
        await trigger_event("fissure", interaction.guild)

    @ui.button(label="☣️ Trigger Warning", style=discord.ButtonStyle.danger, custom_id="cc_inf_inf", row=2)
    async def inf_infect(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["infected"]:
            await interaction.response.send_message("❌ Already infected.", ephemeral=True)
            return
        if tama["infection_warned"]:
            await interaction.response.send_message("❌ Warning already active.", ephemeral=True)
            return
        tama["infection_warned"] = True
        ch = get_tama_channel(interaction.guild)
        if ch:
            safe_task(infection_warning(interaction.guild, ch))
        await interaction.response.send_message("✅ Infection warning triggered.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="💊 Cure",        style=discord.ButtonStyle.success,   custom_id="cc_inf_cure", row=2)
    async def inf_cure(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if not tama["infected"]:
            await interaction.response.send_message("❌ Not infected.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Infection cleared.", ephemeral=True)
        await apply_rolling_guard(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="🛡️ Clear Corruption", style=discord.ButtonStyle.success, custom_id="cc_cl_cor", row=2)
    async def cl_cor(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if tama["active_event"] != "corrupted":
            await interaction.response.send_message("❌ No active corruption.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Corruption cleared.", ephemeral=True)
        await resolve_event("corrupted", interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="❌ Clear Event",  style=discord.ButtonStyle.secondary, custom_id="cc_cl_ev",  row=2)
    async def cl_ev(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if not tama["active_event"]:
            await interaction.response.send_message("❌ No active event.", ephemeral=True)
            return
        event = tama["active_event"]; tama["active_event"] = None
        ch = get_tama_channel(interaction.guild)
        if ch and tama["event_message_id"]:
            try: await (await ch.fetch_message(tama["event_message_id"])).delete()
            except Exception: pass
        tama["event_message_id"] = None
        await interaction.response.send_message(f"✅ Event `{event}` cleared.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="🌙 Clear Sleep", style=discord.ButtonStyle.secondary, custom_id="cc_cl_slp", row=2)
    async def cl_slp(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        if not tama["sleeping"]:
            await interaction.response.send_message("❌ Not sleeping.", ephemeral=True)
            return
        tama["sleeping"] = False; tama["wake_yes"] = set(); tama["wake_no"] = set()
        ch = get_tama_channel(interaction.guild)
        if ch:
            for mk in ("sleep_message_id","wake_vote_message_id"):
                if tama.get(mk):
                    try: await (await ch.fetch_message(tama[mk])).delete()
                    except Exception: pass
                    tama[mk] = None
            safe_task(delete_after(await ch.send(f"☀️ **{wf_name()} has woken up!** Rest restored. Ready for action."), 30))
        await interaction.response.send_message("✅ Sleep cleared.", ephemeral=True)
        await notify_wake_pings(interaction.guild)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)


class SetStatModal(ui.Modal):
    def __init__(self, stat_key: str, stat_label: str):
        super().__init__(title=f"Set {stat_label}")
        self.stat_key = stat_key
        self.value_input = ui.TextInput(label=f"{stat_label} (0-100)", placeholder="e.g. 75", max_length=3)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            v = int(max(0, min(100, int(self.value_input.value))))
        except ValueError:
            await interaction.response.send_message("❌ Enter a number 0-100.", ephemeral=True)
            return
        key_map = {"feed": "feed", "clean": "clean", "focus": "level_stat", "rest": "rest"}
        tama[key_map[self.stat_key]] = v
        if tama["infected"] and all(tama[k] >= SICK_THRESHOLD for k in ("feed","clean","rest")):
            tama["infected"] = False; tama["infection_warned"] = False
            await clear_infection_messages(interaction.guild)
        await interaction.response.send_message(f"✅ {self.stat_key.capitalize()} set to {v}.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)


class SetValueModal(ui.Modal):
    def __init__(self, title: str, label: str, placeholder: str, callback):
        super().__init__(title=title)
        self._callback = callback
        self.value_input = ui.TextInput(label=label, placeholder=placeholder, max_length=10)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self._callback(interaction, self.value_input.value)


class ControlSetView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check(self, interaction: discord.Interaction) -> bool:
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not is_control_mod(member):
            await interaction.response.send_message("❌ Officer or above only.", ephemeral=True)
            return False
        if not tama["active"]:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return False
        return True

    @ui.button(label="🍖 Set Feed",   style=discord.ButtonStyle.secondary, custom_id="cc_set_feed",  row=0)
    async def set_feed(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetStatModal("feed", "Feed"))

    @ui.button(label="🧼 Set Clean",  style=discord.ButtonStyle.secondary, custom_id="cc_set_clean", row=0)
    async def set_clean(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetStatModal("clean", "Clean"))

    @ui.button(label="⚡ Set Focus",  style=discord.ButtonStyle.secondary, custom_id="cc_set_focus", row=0)
    async def set_focus(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetStatModal("focus", "Focus"))

    @ui.button(label="😴 Set Rest",   style=discord.ButtonStyle.secondary, custom_id="cc_set_rest",  row=0)
    async def set_rest(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetStatModal("rest", "Rest"))

    @ui.button(label="💯 Max All",    style=discord.ButtonStyle.success,   custom_id="cc_set_max",   row=0)
    async def set_max(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        for k in STAT_KEYS: tama[k] = 100
        tama["dead"] = False; tama["infected"] = False; tama["infection_warned"] = False
        await clear_infection_messages(interaction.guild)
        await interaction.response.send_message("✅ All stats maxed.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="📈 Set Level",  style=discord.ButtonStyle.secondary, custom_id="cc_set_lvl",   row=1)
    async def set_level(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        async def cb(inter, val):
            try:
                v = int(max(0, min(LEVEL_MAX, int(val))))
            except ValueError:
                await inter.response.send_message("❌ Enter 0-30.", ephemeral=True)
                return
            tama["level"] = v; tama["evo_xp"] = 0
            await inter.response.send_message(f"✅ Level set to {v}.", ephemeral=True)
            await refresh_embed(inter.guild)
            await refresh_control_center(inter.guild)
        await interaction.response.send_modal(SetValueModal("Set Level", "Level (0-30)", "e.g. 15", cb))

    @ui.button(label="✨ Set XP Mult",style=discord.ButtonStyle.secondary, custom_id="cc_set_xpm",   row=1)
    async def set_xp_mult(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        async def cb(inter, val):
            try:
                v = max(0.1, min(5.0, float(val)))
            except ValueError:
                await inter.response.send_message("❌ Enter a number.", ephemeral=True)
                return
            tama["prime_xp_multiplier"] = v
            await inter.response.send_message(f"✅ XP multiplier set to x{v:.2f}.", ephemeral=True)
            await refresh_embed(inter.guild)
            await refresh_control_center(inter.guild)
        await interaction.response.send_modal(SetValueModal("Set XP Multiplier", "Multiplier (0.1-5.0)", "e.g. 1.5", cb))

    @ui.button(label="🏆 Set Points", style=discord.ButtonStyle.secondary, custom_id="cc_set_pts",   row=1)
    async def set_points(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        async def cb(inter, val):
            parts = val.strip().split()
            if len(parts) != 2:
                await inter.response.send_message("❌ Format: `UserID points`", ephemeral=True)
                return
            try:
                uid = int(parts[0]); pts = int(parts[1])
            except ValueError:
                await inter.response.send_message("❌ Invalid format.", ephemeral=True)
                return
            if uid not in tama["leaderboard"]:
                member = inter.guild.get_member(uid)
                name = member.display_name if member else str(uid)
                tama["leaderboard"][uid] = {"name": name, "clicks": 0, "train_clicks": 0}
            tama["leaderboard"][uid]["clicks"] = pts
            await inter.response.send_message(f"✅ Points set to {pts}.", ephemeral=True)
            await refresh_embed(inter.guild)
            await refresh_control_center(inter.guild)
        await interaction.response.send_modal(SetValueModal("Set Points", "UserID then points", "e.g. 123456789 50", cb))

    @ui.button(label="↩️ Reset to Default", style=discord.ButtonStyle.danger,    custom_id="cc_rst_dec",   row=1)
    async def reset_decay(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        reset_config_to_defaults()
        await interaction.response.send_message("✅ All overrides cleared. Config reset to tama_config.py defaults.", ephemeral=True)
        await refresh_control_center(interaction.guild)

    @ui.button(label="⏸ Pause",      style=discord.ButtonStyle.secondary, custom_id="cc_pause",     row=2)
    async def cc_pause(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        tama["paused"] = True
        await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="▶️ Resume",    style=discord.ButtonStyle.secondary, custom_id="cc_resume",    row=2)
    async def cc_resume(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        tama["paused"] = False
        await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        await refresh_embed(interaction.guild)
        await refresh_control_center(interaction.guild)

    @ui.button(label="🔺 Evolve",    style=discord.ButtonStyle.primary,   custom_id="cc_evolve",    row=2)
    async def cc_evolve(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        tama["evolution_blocked"] = False
        if tama["stage"] == "relic":
            await interaction.response.send_message("✅ Evolving to Warframe…", ephemeral=True)
            await evolve_to_warframe(interaction.guild)
        elif tama["stage"] == "warframe":
            await interaction.response.send_message("✅ Evolving to Prime…", ephemeral=True)
            await evolve_to_prime(interaction.guild)
        elif tama["stage"] == "prime" and not tama.get("completed"):
            await interaction.response.send_message("✅ Completing session…", ephemeral=True)
            tama["level"] = LEVEL_MAX; tama["evo_xp"] = 0
            await trigger_prime_complete(interaction.guild)
        else:
            await interaction.response.send_message("❌ Already at max stage.", ephemeral=True)
            return
        await refresh_control_center(interaction.guild)

    @ui.button(label="📉 Set Decay %",     style=discord.ButtonStyle.secondary, custom_id="cc_set_decay_pct",  row=3)
    async def set_decay_pct(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetDecayPctModal())

    @ui.button(label="⏱️ Set Cooldown",    style=discord.ButtonStyle.secondary, custom_id="cc_set_cooldown",   row=3)
    async def set_cooldown(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetCooldownModal())

    @ui.button(label="🕐 Set Decay Time",  style=discord.ButtonStyle.secondary, custom_id="cc_set_decay_time", row=3)
    async def set_decay_time(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._check(interaction): return
        await interaction.response.send_modal(SetDecayTimeModal())


class SetDecayPctModal(ui.Modal, title="Set Decay Percentage"):
    pct = ui.TextInput(
        label       = "Decay % per tick (e.g. 3 = 3% per tick)",
        placeholder = "e.g. 3",
        max_length  = 5,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            v = float(self.pct.value)
            if v < 0: raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Enter a positive number.", ephemeral=True)
            return
        set_config_override("DECAY_BASE", v)
        await interaction.response.send_message(
            f"✅ Decay set to **{v}%** per tick. Takes effect on the next decay tick.\n"
            f"⚠️ This resets to config default on next session start.",
            ephemeral=True
        )
        await refresh_control_center(interaction.guild)


class SetCooldownModal(ui.Modal, title="Set Click Cooldown"):
    minutes = ui.TextInput(
        label       = "Minutes",
        placeholder = "e.g. 5",
        max_length  = 3,
        default     = "5",
    )
    seconds = ui.TextInput(
        label       = "Seconds",
        placeholder = "e.g. 0",
        max_length  = 2,
        default     = "0",
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            m = int(self.minutes.value)
            s = int(self.seconds.value)
            if m < 0 or s < 0 or s > 59: raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Enter valid minutes (0+) and seconds (0-59).", ephemeral=True)
            return
        total_minutes = m + (s / 60)
        set_config_override("GLOBAL_COOLDOWN_MINUTES", total_minutes)
        await interaction.response.send_message(
            f"✅ Click cooldown set to **{m}m {s}s**. Takes effect immediately.\n"
            f"⚠️ This resets to config default on next session start.",
            ephemeral=True
        )
        await refresh_control_center(interaction.guild)


class SetDecayTimeModal(ui.Modal, title="Set Decay Tick Interval"):
    minutes = ui.TextInput(
        label       = "Minutes",
        placeholder = "e.g. 3",
        max_length  = 3,
        default     = "3",
    )
    seconds = ui.TextInput(
        label       = "Seconds",
        placeholder = "e.g. 0",
        max_length  = 2,
        default     = "0",
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            m = int(self.minutes.value)
            s = int(self.seconds.value)
            if m < 0 or s < 0 or s > 59: raise ValueError
            total_seconds = (m * 60) + s
            if total_seconds < 10: raise ValueError  # minimum 10 seconds
        except ValueError:
            await interaction.response.send_message(
                "❌ Enter valid minutes (0+) and seconds (0-59). Minimum interval is 10 seconds.",
                ephemeral=True
            )
            return
        set_config_override("DECAY_INTERVAL", total_seconds)
        await interaction.response.send_message(
            f"✅ Decay tick interval set to **{m}m {s}s** ({total_seconds}s). "
            f"Takes effect on the next tick.\n"
            f"⚠️ This resets to config default on next session start.",
            ephemeral=True
        )
        await refresh_control_center(interaction.guild)


class ConfigEditModal(ui.Modal):
    def __init__(self, key: str, label: str, current_val, dtype: str):
        super().__init__(title=f"Edit: {label[:44]}")
        self.config_key = key
        self.dtype      = dtype
        self.value_input = ui.TextInput(
            label       = f"New value (currently {current_val})",
            placeholder = str(current_val),
            default     = str(current_val),
            max_length  = 20,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_warlord(interaction.guild.get_member(interaction.user.id) or interaction.user):
            await interaction.response.send_message("❌ Warlord only.", ephemeral=True)
            return
        try:
            if self.dtype == "int":
                v = int(self.value_input.value)
            else:
                v = float(self.value_input.value)
        except ValueError:
            await interaction.response.send_message("❌ Invalid value.", ephemeral=True)
            return
        set_config_override(self.config_key, v)
        await interaction.response.send_message(
            f"⚠️ **`{self.config_key}`** set to **{v}**. Takes effect on next decay tick.\n"
            f"Deploy or delete the override file to reset to config defaults.",
            ephemeral=True
        )
        await refresh_control_center(interaction.guild)


class ConfigGroupView(ui.View):
    def __init__(self, group_name: str, fields: list):
        super().__init__(timeout=None)
        self.group_name = group_name
        self.fields     = fields
        overrides = load_overrides()
        import tama_config as _cfg
        # Discord max: 5 rows × 5 buttons = 25 buttons per view
        for i, (key, label, dtype) in enumerate(fields[:25]):
            current = overrides.get(key, getattr(_cfg, key, "?"))
            btn = ui.Button(
                label     = f"✏️ {label[:22]}",
                style     = discord.ButtonStyle.danger if key in overrides else discord.ButtonStyle.secondary,
                custom_id = f"cc_cfg_{key}",
                row       = i // 5,
            )
            # Use default args to capture loop variables correctly (no async needed)
            def make_callback(k=key, l=label, d=dtype):
                async def callback(inter: discord.Interaction):
                    if not is_warlord(inter.guild.get_member(inter.user.id) or inter.user):
                        await inter.response.send_message("❌ Warlord only.", ephemeral=True)
                        return
                    ov = load_overrides()
                    import tama_config as _c
                    cur = ov.get(k, getattr(_c, k, "?"))
                    await inter.response.send_modal(ConfigEditModal(k, l, cur, d))
                return callback
            btn.callback = make_callback()
            self.add_item(btn)


CONTROL_CENTER_STATE_PATH = "control_center_state.json"

def load_control_center_state():
    if os.path.exists(CONTROL_CENTER_STATE_PATH):
        with open(CONTROL_CENTER_STATE_PATH, "r") as f:
            try:
                data = json.load(f)
                control_center["channel_id"]     = data.get("channel_id")
                control_center["message_id"]     = data.get("message_id")
                control_center["config_msg_ids"] = data.get("config_msg_ids", {})
            except Exception:
                pass

def save_control_center_state():
    with open(CONTROL_CENTER_STATE_PATH, "w") as f:
        json.dump({
            "channel_id":     control_center["channel_id"],
            "message_id":     control_center["message_id"],
            "config_msg_ids": control_center["config_msg_ids"],
        }, f, indent=2)

# ---------------------------
# CONTROL CENTER REFRESH
# ---------------------------
async def refresh_control_center(guild):
    """Update the control center embed, debounced to avoid rate limiting."""
    global _cc_refresh_pending, _cc_refresh_needed, _cc_last_refresh_time, _config_dirty

    if _cc_refresh_pending:
        _cc_refresh_needed = True
        return

    now     = datetime.now(timezone.utc)
    elapsed = (now - _cc_last_refresh_time).total_seconds()
    if elapsed < CC_REFRESH_DEBOUNCE_SECONDS:
        _cc_refresh_pending = True
        await asyncio.sleep(CC_REFRESH_DEBOUNCE_SECONDS - elapsed)
        _cc_refresh_pending = False

    _cc_last_refresh_time = datetime.now(timezone.utc)

    if _cc_refresh_needed:
        _cc_refresh_needed = False
        safe_task(refresh_control_center(guild))

    cid = control_center.get("channel_id")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if not ch:
        return

    embed = build_control_embed(guild)
    mid   = control_center.get("message_id")
    if mid:
        try:
            msg = ch.get_partial_message(mid)
            await msg.edit(embed=embed)
        except discord.NotFound:
            control_center["message_id"] = None
        except Exception:
            return
    else:
        msg = await ch.send(embed=embed)
        control_center["message_id"] = msg.id
        save_control_center_state()

    # Config group embeds only update when a value actually changed — not on every stats tick
    if _config_dirty:
        _config_dirty = False
        for group_name, fields in CONFIG_GROUPS:
            gmid = control_center["config_msg_ids"].get(group_name)
            if not gmid:
                continue
            try:
                gmsg = ch.get_partial_message(gmid)
                await gmsg.edit(embed=build_config_group_embed(group_name, fields))
            except discord.NotFound:
                control_center["config_msg_ids"].pop(group_name, None)
            except Exception:
                pass


# ---------------------------
# /tama-control-setup
# ---------------------------
@bot.tree.command(name="tama-control-setup", description="Create the Tamaframe control center channel. Warlord only.", guild=guild_obj)
async def tama_control_setup(interaction: discord.Interaction):
    if not is_warlord(interaction.user):
        await interaction.response.send_message("❌ Warlord only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild    = interaction.guild
    category = guild.get_channel(CONTROL_CENTER_CATEGORY_ID)

    # Find or create channel
    ch = guild.get_channel(control_center.get("channel_id") or 0)
    if not ch and category:
        for c in category.text_channels:
            if "control" in c.name and "tama" in c.name:
                ch = c
                break
    if not ch:
        try:
            ch = await guild.create_text_channel(
                name     = "⚙️︱tama-control",
                category = category,
                topic    = "Tamaframe control center — mod eyes only",
                reason   = "tama-control-setup"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Missing permission to create channels.", ephemeral=True)
            return

    # Set permissions — Officers+ only, everyone else no access
    try:
        await ch.set_permissions(guild.default_role, view_channel=False)
        for role_id in CONTROL_MOD_ROLES:
            role = guild.get_role(role_id)
            if role:
                await ch.set_permissions(role,
                    view_channel=True, send_messages=False,
                    read_message_history=True, use_application_commands=True)
    except Exception:
        pass

    control_center["channel_id"] = ch.id
    await purge_channel(ch)

    # Post main control embed + action/session views
    embed = build_control_embed(guild)
    msg   = await ch.send(embed=embed, view=ControlSessionView())
    control_center["message_id"] = msg.id

    # Post action buttons
    await ch.send("**🎮 Actions & Events**", view=ControlActionView())

    # Post set/decay/evolve buttons
    await ch.send("**⚙️ Set & Control**", view=ControlSetView())

    # Post config group embeds
    import tama_config as _cfg
    overrides = load_overrides()
    control_center["config_msg_ids"] = {}
    for group_name, fields in CONFIG_GROUPS:
        group_embed = build_config_group_embed(group_name, fields)
        group_view  = ConfigGroupView(group_name, fields)
        gmsg        = await ch.send(embed=group_embed, view=group_view)
        control_center["config_msg_ids"][group_name] = gmsg.id

    await interaction.followup.send(f"✅ Control center live in {ch.mention}.", ephemeral=True)
    save_control_center_state()




# ---------------------------
# EVENTS
# ---------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # Cooldown — send ephemeral message instead of logging
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        minutes = int(error.retry_after // 60)
        seconds = int(error.retry_after % 60)
        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        try:
            await interaction.response.send_message(
                f"⏳ You're on cooldown. Try again in **{time_str}**.",
                ephemeral=True, delete_after=10
            )
        except Exception:
            pass
        return
    # Suppress unknown interaction errors (10062)
    if isinstance(error, discord.app_commands.CommandInvokeError):
        if isinstance(error.original, discord.NotFound) and error.original.code == 10062:
            return
    # Log everything else
    import traceback
    traceback.print_exception(type(error), error, error.__traceback__)

def handle_task_exception(loop, context):
    """Global handler for unhandled asyncio task exceptions."""
    exc = context.get("exception")
    msg = context.get("message", "Unknown error")
    if exc:
        import traceback
        print(f"[TASK ERROR] {msg}: {exc}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        print(f"[TASK ERROR] {msg}")

def safe_task(coro):
    """Wrap a coroutine in a task with error logging."""
    async def _wrapper():
        try:
            await coro
        except Exception as e:
            import traceback
            print(f"[SAFE TASK ERROR] {e}")
            traceback.print_exc()
    return asyncio.create_task(_wrapper())

# ---------------------------
# TAMA PING COMMANDS
# ---------------------------
@bot.tree.command(name="tama-ping-on", description="Get notified when your click cooldown is ready.", guild=guild_obj)
@app_commands.describe(mode="How you want to be notified")
@app_commands.choices(mode=[
    app_commands.Choice(name="Channel ping", value="channel"),
    app_commands.Choice(name="DM", value="dm"),
    app_commands.Choice(name="Both", value="both"),
])
async def tama_ping_on(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not is_tama_member(interaction.user):
        await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        return
    if mode.value in ("channel", "both"):
        tama_ping_opted_in.add(interaction.user.id)
    if mode.value in ("dm", "both"):
        tama_dm_opted_in.add(interaction.user.id)
    save_ping_prefs()
    descriptions = {
        "channel": "🔔 You'll be pinged in the companion channel when your cooldown is up.",
        "dm":      "🔔 You'll receive a DM when your cooldown is up. Make sure your DMs are open to server members.",
        "both":    "🔔 You'll be pinged in the companion channel and receive a DM when your cooldown is up. Make sure your DMs are open to server members.",
    }
    await interaction.response.send_message(descriptions[mode.value], ephemeral=True)

@bot.tree.command(name="tama-ping-off", description="Stop receiving cooldown-ready notifications.", guild=guild_obj)
async def tama_ping_off(interaction: discord.Interaction):
    if not is_tama_member(interaction.user):
        await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        return
    tama_ping_opted_in.discard(interaction.user.id)
    tama_dm_opted_in.discard(interaction.user.id)
    save_ping_prefs()
    await interaction.response.send_message("🔕 Cooldown notifications disabled.", ephemeral=True)



# ---------------------------
# ON READY
# ---------------------------
@bot.event
async def on_ready():
    print(f"[Tamaframe] Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        bot.add_view(TamagotchiView())
        bot.add_view(SleepCounterView())
        bot.add_view(WakeVoteView())
        bot.add_view(ControlSessionView())
        bot.add_view(ControlActionView())
        bot.add_view(ControlSetView())
        for group_name, fields in CONFIG_GROUPS:
            bot.add_view(ConfigGroupView(group_name, fields))
        load_control_center_state()
        load_tama_state()
        load_ping_prefs()
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        guild_obj_sync = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild_obj_sync)
        print(f"[Tamaframe] Synced {len(synced)} command(s) to guild {GUILD_ID}")
        asyncio.get_running_loop().set_exception_handler(handle_task_exception)
        if not decay_loop.is_running():
            decay_loop.start()
        apply_persisted_overrides()
        idle_rotation_loop.start()
        hourly_info_loop.start()
        guild = bot.get_guild(GUILD_ID)
        if guild and tama.get("channel_id"):
            ch = guild.get_channel(tama["channel_id"])
            if ch:
                embed_msgs = []
                async for msg in ch.history(limit=30):
                    if msg.author.id == bot.user.id and msg.embeds:
                        embed_msgs.append(msg)
                if embed_msgs:
                    tama["message_id"] = embed_msgs[0].id
                    tama["active"]     = True
                    print(f"[Tamaframe] Resumed session in #{ch.name} (msg {embed_msgs[0].id})")
                    for stale in embed_msgs[1:]:
                        try:
                            await stale.delete()
                        except Exception:
                            pass
                if tama["active"]:
                    tama["sleep_vote_message_id"] = None
                    tama["sleep_vote_yes"]        = set()
                    tama["sleep_vote_no"]         = set()
                    tama["sleep_vote_started"]    = None
                    tama["wake_vote_message_id"]  = None
                    await refresh_embed(guild)
        else:
            print("[Tamaframe] No active session. Use /tama-manage Start to begin.")
    except Exception:
        traceback.print_exc()

# ---------------------------
# RUN
# ---------------------------
if not TOKEN:
    raise RuntimeError("TAMAGOTCHI_TOKEN environment variable not set.")
bot.run(TOKEN)
