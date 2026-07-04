"""
Kintara Merchant Alert Bot - Enhanced Edition
=============================================
Monitor-only Telegram bot that watches Kintara merchant APIs and sends
alerts when important state changes occur.

This bot is READ-ONLY. It only calls GET endpoints and never performs
any in-game actions such as donating, claiming, buying, or selling.

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in your values
  3. Run bot.py  OR  double-click run.bat
"""

import json
import os
import sys
import time
import logging
import traceback
import random
import string
from datetime import datetime, timedelta
import concurrent.futures

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
def print(*args, **kwargs):
    # Override print globally to pipe to logging info
    msg = " ".join(map(str, args))
    if "[WARN]" in msg or "[ERROR]" in msg or "error" in msg.lower():
        logging.warning(msg)
    else:
        logging.info(msg)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PUBLIC_API_URL   = "https://fanout.kintara.gg/api/world/merchant-campaign"
PRIVATE_API_URL  = "https://kintara.com/api/auth/merchant-cycle-status"

STATE_FILE      = "state.json"
REQUEST_TIMEOUT = 15  # seconds
HISTORY_MAX     = 5   # how many past cycles to keep in history
BAR_WIDTH       = 8   # width of progress bars

# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------

# ── Main menu ─────────────────────────────────────────────────────────────
def _build_main_keyboard(chat_id: str) -> dict:
    kb = {
        "inline_keyboard": [
            [
                {"text": "📊 Live Status",  "callback_data": "status"},
                {"text": "📅 History",     "callback_data": "history"},
            ],
            [
                {"text": "⚙️ Settings",    "callback_data": "settings"},
                {"text": "❓ Help",          "callback_data": "help"},
            ],
        ]
    }
    if str(chat_id) == str(CONFIG.get("chat_id")):
        kb["inline_keyboard"].append([{"text": "👑 Admin Dashboard", "callback_data": "admin_dashboard"}])
    return kb

# ── Alert keyboard ─ shown on every automatic alert ───────────────────────
ALERT_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "📊 Live Status",  "callback_data": "status"},
            {"text": "⚙️ Settings",    "callback_data": "settings"},
        ],
    ]
}

# ── Status keyboard ─ shown below /status ─────────────────────────────────
STATUS_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🔄 Refresh",      "callback_data": "status"},
            {"text": "📅 History",     "callback_data": "history"},
        ],
        [
            {"text": "⚙️ Settings",    "callback_data": "settings"},
            {"text": "❓ Help",          "callback_data": "help"},
        ],
    ]
}


def _build_settings_keyboard(state: dict, chat_id: str) -> dict:
    """Professional settings keyboard with clear sections and toggle states."""
    # Use per-user prefs (fallback to global defaults)
    prefs     = state.get("prefs", {}).get(str(chat_id), {})
    claim_on  = prefs.get("claim_alerts",  True)
    gold_on   = prefs.get("gold_alerts",   True)
    repeat_on = prefs.get("repeat_donate", True)
    interval  = prefs.get("public_interval", CONFIG.get("public_interval", 5))

    def tog(flag): return "🟢" if flag else "🔴"

    return {
        "inline_keyboard": [
            # Section: Alerts
            [{"text": "─── 🔔 Alert Controls ───",              "callback_data": "noop"}],
            [{"text": f"{tog(True)}  Donation Open  (always ON)",    "callback_data": "noop"}],
            [{"text": f"{tog(repeat_on)}  Repeat Remind  (every {CONFIG.get('repeat_mins',10)}m)", "callback_data": "toggle_repeat"}],
            [{"text": f"{tog(claim_on)}  Claim Phase    (pool > 0 only)",  "callback_data": "toggle_claim"}],
            [{"text": f"{tog(gold_on)}  Gold Available",               "callback_data": "toggle_gold"}],
            # Section: Schedule
            [{"text": "─── ⏰ Schedule ───",                        "callback_data": "noop"}],
            [{"text": f"⏱  Check Speed   every {interval}s",          "callback_data": "intervals"}],
            # Section: Tools
            [{"text": "─── 🛠 Tools ───",                         "callback_data": "noop"}],
            [
                {"text": "🧪 Test Alert",  "callback_data": "test_alert"},
                {"text": "📅 History",   "callback_data": "history"},
            ],
            [{"text": "◀️  Back to Menu",                            "callback_data": "start"}],
        ]
    }


def _build_interval_keyboard(state: dict, chat_id: str) -> dict:
    """Speed picker — checkmark on the currently active interval."""
    cur = state.get("prefs", {}).get(str(chat_id), {}).get("public_interval", CONFIG.get("public_interval", 5))
    def btn(v):
        label = f"✅ {v}s" if cur == v else f"{v}s"
        return {"text": label, "callback_data": f"interval_{v}"}
    return {
        "inline_keyboard": [
            [btn(5), btn(10), btn(15)],
            [btn(30), btn(60)],
            [{"text": "◀️  Back to Settings", "callback_data": "settings"}],
        ]
    }


# ---------------------------------------------------------------------------
# Environment / Configuration
# ---------------------------------------------------------------------------

def load_env() -> dict:
    """
    Load configuration from the .env file (falls back to environment vars).
    Returns a config dict with all required settings.
    """
    load_dotenv(override=True)

    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN is missing from .env")
        sys.exit(1)
    if not chat_id:
        print("[ERROR] TELEGRAM_CHAT_ID is missing from .env")
        sys.exit(1)

    cookie = os.getenv("KINTARA_COOKIE", "").strip()
    access_code = os.getenv("ACCESS_CODE", "12345").strip()

    try:
        public_interval = int(os.getenv("PUBLIC_CHECK_SECONDS", "5"))
    except ValueError:
        public_interval = 5

    try:
        private_interval = int(os.getenv("PRIVATE_CHECK_SECONDS", "60"))
    except ValueError:
        private_interval = 60

    try:
        repeat_mins = int(os.getenv("REPEAT_DONATE_ALERT_MINUTES", "10"))
    except ValueError:
        repeat_mins = 10

    return {
        "token":            token,
        "chat_id":          chat_id,
        "cookie":           cookie if cookie else None,
        "public_interval":  max(3, public_interval),
        "private_interval": max(10, private_interval),
        "repeat_mins":      max(1, repeat_mins),
        "access_code":      access_code,
    }


# Global config populated in main()
CONFIG: dict = {}


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _tg_url(endpoint: str) -> str:
    return f"https://api.telegram.org/bot{CONFIG['token']}/{endpoint}"


def send_telegram(text: str, keyboard: dict = None, chat_id: str = None) -> bool:
    """
    Send an HTML message to the configured Telegram chat.
    Pass a keyboard dict (inline_keyboard) to attach tappable buttons.
    Returns True on success, False on failure.
    """
    target = chat_id or CONFIG.get("chat_id")
    if not target:
        return False

    url     = _tg_url("sendMessage")
    payload = {
        "chat_id":    target,
        "text":       text,
        "parse_mode": "HTML",
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        print(f"[WARN] sendMessage failed: {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"[WARN] sendMessage error: {exc}")
        return False

def broadcast_telegram(state: dict, event_type: str, text: str, keyboard: dict = None) -> None:
    """
    Send an alert to all registered subscribers who have this event_type enabled.
    """
    subs = state.get("subscribers", {})
    target_ids = list(subs.keys())
    # Always include the original owner from .env for safety
    owner = str(CONFIG.get("chat_id"))
    if owner and owner not in target_ids:
        target_ids.append(owner)

    def _send_if_enabled(sub_id):
        # Check personal pref
        prefs = state.get("prefs", {}).get(str(sub_id), {})
        # If user turned it off, skip them
        if event_type and not prefs.get(event_type, True):
            return
        send_telegram(text, keyboard=keyboard, chat_id=sub_id)

    # Send concurrently to avoid blocking when there are many users
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for sub_id in target_ids:
            executor.submit(_send_if_enabled, sub_id)


def answer_callback(callback_id: str, text: str = "") -> None:
    """
    Acknowledge a button press so Telegram stops showing the loading spinner.
    Optionally shows a small toast notification to the user.
    """
    try:
        requests.post(
            _tg_url("answerCallbackQuery"),
            json={"callback_query_id": callback_id, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        pass


def get_telegram_updates(offset: int) -> list:
    """
    Poll Telegram for new updates (messages + button presses) via short-polling.
    Returns a list of update objects.
    """
    url    = _tg_url("getUpdates")
    params = {
        "offset":  offset,
        "timeout": 2,
        "limit":   20,
    }
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
    except requests.RequestException as exc:
        print(f"[WARN] getUpdates error: {exc}")
    return []


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def fetch_json(url: str, headers: dict = None) -> dict:
    """
    Perform a GET request and return parsed JSON, or None on any error.
    """
    default_headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    if headers:
        default_headers.update(headers)

    try:
        resp = requests.get(url, headers=default_headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        print(f"[WARN] HTTP {resp.status_code} from {url}")
        return None
    except requests.RequestException as exc:
        print(f"[WARN] Request error for {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """
    Load the last-known state from state.json.
    Returns an empty dict if the file does not exist or is corrupted.
    """
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            st = json.load(fh)
            # Auto-migrate subscribers from list to dict if necessary
            subs = st.get("subscribers")
            if isinstance(subs, list):
                new_subs = {}
                for s in subs:
                    new_subs[str(s)] = {"name": "Unknown", "joined": ""}
                st["subscribers"] = new_subs
            return st
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] Could not load {STATE_FILE}: {exc} -- starting fresh.")
        return {}


def save_state(state: dict) -> None:
    """Persist the current state to state.json."""
    tmp_file = f"{STATE_FILE}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except OSError as exc:
        print(f"[WARN] Could not save state: {exc}")


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def fmt(n) -> str:
    """Format a number with commas, or return N/A."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n) if n is not None else "N/A"


def fmt_f(n, decimals: int = 4) -> str:
    """Format a float."""
    try:
        return f"{float(n):.{decimals}f}"
    except (TypeError, ValueError):
        return str(n) if n is not None else "N/A"


def progress_bar(current, total, width: int = BAR_WIDTH) -> str:
    """
    Return a text progress bar like: ████░░░░ 50%
    Safe against zero/None totals.
    """
    try:
        cur = float(current or 0)
        tot = float(total or 0)
        if tot <= 0:
            return "░" * width + "  0%"
        pct = min(cur / tot, 1.0)
        filled = round(pct * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"{bar} {int(pct * 100)}%"
    except (TypeError, ValueError):
        return "░" * width + "  ?%"


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------

def format_merchant_status(data: dict) -> str:
    """Build a human-readable summary with progress bars."""
    goals = data.get("goals", {})

    def row(label, cur_key, goal_key):
        cur  = data.get(cur_key, 0)
        goal = goals.get(goal_key, 0)
        bar  = progress_bar(cur, goal)
        return f"  {label:<12}: {bar}  {fmt(cur)}/{fmt(goal)}"

    pool_rem  = data.get("poolRemaining", 0)
    pool_full = data.get("poolFull", 1)
    pool_bar  = progress_bar(pool_full - pool_rem, pool_full)  # filled = claimed
    pool_pct  = int((1 - pool_rem / max(pool_full, 1)) * 100)

    lines = [
        "<b>📦 Kintara Merchant Status</b>",
        f"Cycle    : <b>{data.get('cycleId', 'N/A')}</b>   Phase: <b>{data.get('phase', 'N/A')}</b>   Done: <b>{'✅' if data.get('complete') else '❌'}</b>",
        "",
        "<b>🪵 Donations</b>",
        row("Wood",        "wood",            "wood"),
        row("Stone",       "stone",           "stone"),
        row("Coal",        "coal",            "coal"),
        row("Cooked Fish", "cooked_fish_meat","cooked_fish_meat"),
        row("Metal",       "metal",           "metal"),
        "",
        "<b>🏦 Gold Pool</b>",
        f"  Pool     : {pool_bar}  {fmt(pool_rem)} left / {fmt(pool_full)}  ({pool_pct}% claimed)",
        f"  Gold/Pt  : {fmt(data.get('goldPerPoint'))}",
        f"  Stock    : {fmt(data.get('goldStock'))} / {fmt(data.get('goldStockFull'))}",
        f"  Trade    : {'Enabled' if data.get('goldTradeEnabled') else 'Disabled'}",
    ]
    return "\n".join(lines)


def format_personal_status(data: dict) -> str:
    """Build a human-readable summary of the private merchant-cycle-status response."""
    avail = data.get("myAvailable", 0)
    claimed = data.get("myClaimed", 0)
    limit   = data.get("myLimit", 0)
    bar = progress_bar(claimed, limit)
    lines = [
        "<b>👤 Your Status</b>",
        f"Phase    : <b>{data.get('phase', 'N/A')}</b>   Done: <b>{'✅' if data.get('complete') else '❌'}</b>",
        "",
        "<b>🪙 Your Gold</b>",
        f"  Available : <b>{fmt(avail)}</b>",
        f"  Claimed   : {bar}  {fmt(claimed)}/{fmt(limit)}",
        f"  My Points : {fmt_f(data.get('myPoints'))}",
        f"  Pool Left : {fmt(data.get('poolRemaining'))} / {fmt(data.get('poolFull'))}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alert checks -- public merchant
# ---------------------------------------------------------------------------

def check_public_merchant(state: dict) -> dict:
    """
    Fetch the public merchant-campaign endpoint, compare with saved state,
    and fire Telegram alerts for any important state changes.
    Returns the (possibly updated) state dict.
    """
    data = fetch_json(PUBLIC_API_URL)
    if data is None or not data.get("ok"):
        print(f"[{_ts()}] Public merchant API unavailable or returned ok=false")
        return state

    cycle_id       = data.get("cycleId")
    phase          = data.get("phase")
    complete       = data.get("complete", False)
    pool_remaining = data.get("poolRemaining")

    prev                = state.get("public", {})
    prev_phase          = prev.get("phase")
    prev_complete       = prev.get("complete")
    prev_pool_remaining = prev.get("poolRemaining")
    prev_cycle_id       = prev.get("cycleId")

    goals = data.get("goals", {})

    # ---- Rule 1: Phase changed to "donate" ----
    if phase == "donate" and (phase != prev_phase or cycle_id != prev_cycle_id):
        # Save time of first donate alert for this cycle
        state["last_donate_alert"] = time.time()
        msg = (
            "🔔 <b>Kintara Merchant is open for donation!</b>\n\n"
            f"Cycle ID  : <b>{cycle_id}</b>\n\n"
            "<b>🪵 Donations</b>\n"
            f"  Wood        : {progress_bar(data.get('wood'), goals.get('wood'))}  {fmt(data.get('wood'))}/{fmt(goals.get('wood'))}\n"
            f"  Stone       : {progress_bar(data.get('stone'), goals.get('stone'))}  {fmt(data.get('stone'))}/{fmt(goals.get('stone'))}\n"
            f"  Coal        : {progress_bar(data.get('coal'), goals.get('coal'))}  {fmt(data.get('coal'))}/{fmt(goals.get('coal'))}\n"
            f"  Cooked Fish : {progress_bar(data.get('cooked_fish_meat'), goals.get('cooked_fish_meat'))}  {fmt(data.get('cooked_fish_meat'))}/{fmt(goals.get('cooked_fish_meat'))}\n"
            f"  Metal       : {progress_bar(data.get('metal'), goals.get('metal'))}  {fmt(data.get('metal'))}/{fmt(goals.get('metal'))}\n\n"
            f"  Pool Left   : {fmt(pool_remaining)} / {fmt(data.get('poolFull'))}"
        )
        print(f"[{_ts()}] ALERT: Phase changed to 'donate'")
        broadcast_telegram(state, None, msg, keyboard=ALERT_KEYBOARD)

    # ---- Repeat donate alert ----
    elif phase == "donate":
        last_alert = state.get("last_donate_alert", 0)
        repeat_secs = CONFIG.get("repeat_mins", 10) * 60
        if time.time() - last_alert >= repeat_secs:
            state["last_donate_alert"] = time.time()
            msg = (
                "🔔 <b>Reminder: Merchant donation is still open!</b>\n\n"
                f"Cycle ID  : <b>{cycle_id}</b>\n\n"
                "<b>🪵 Current Progress</b>\n"
                f"  Wood        : {progress_bar(data.get('wood'), goals.get('wood'))}  {fmt(data.get('wood'))}/{fmt(goals.get('wood'))}\n"
                f"  Stone       : {progress_bar(data.get('stone'), goals.get('stone'))}  {fmt(data.get('stone'))}/{fmt(goals.get('stone'))}\n"
                f"  Coal        : {progress_bar(data.get('coal'), goals.get('coal'))}  {fmt(data.get('coal'))}/{fmt(goals.get('coal'))}\n"
                f"  Cooked Fish : {progress_bar(data.get('cooked_fish_meat'), goals.get('cooked_fish_meat'))}  {fmt(data.get('cooked_fish_meat'))}/{fmt(goals.get('cooked_fish_meat'))}\n"
                f"  Metal       : {progress_bar(data.get('metal'), goals.get('metal'))}  {fmt(data.get('metal'))}/{fmt(goals.get('metal'))}"
            )
            print(f"[{_ts()}] REPEAT ALERT: Donate still open")
            broadcast_telegram(state, "repeat_donate", msg, keyboard=ALERT_KEYBOARD)

    # ---- Rule 2: Phase changed to "claim" ----
    # Only alert if pool_remaining > 0 — FCFS means no pool = no point alerting
    elif phase == "claim" and (phase != prev_phase or cycle_id != prev_cycle_id):
        pool_has_gold = (pool_remaining is not None and pool_remaining > 0)
        if pool_has_gold:
            msg = (
                "🏆 <b>Claim phase open — pool still has gold!</b>\n\n"
                f"Cycle ID   : <b>{cycle_id}</b>\n"
                f"Pool Left  : <b>{fmt(pool_remaining)}</b> / {fmt(data.get('poolFull'))}\n"
                f"Gold/Point : {fmt(data.get('goldPerPoint'))}\n"
                f"Gold Stock : {fmt(data.get('goldStock'))} / {fmt(data.get('goldStockFull'))}"
            )
            print(f"[{_ts()}] ALERT: Claim phase open, pool={fmt(pool_remaining)}")
            broadcast_telegram(state, "claim_alerts", msg, keyboard=ALERT_KEYBOARD)
        else:
            print(f"[{_ts()}] Claim phase open but pool is empty — no alert sent")

    # ---- Rule 3: Donation complete (false -> true) ----
    if complete and not prev_complete and cycle_id is not None and cycle_id == prev_cycle_id:
        msg = (
            "✅ <b>Kintara merchant donation is complete!</b>\n"
            "Claim should be available soon.\n\n"
            f"Cycle ID  : <b>{cycle_id}</b>\n"
            f"Pool Left : {fmt(pool_remaining)} / {fmt(data.get('poolFull'))}"
        )
        print(f"[{_ts()}] ALERT: Donation complete")
        broadcast_telegram(state, None, msg, keyboard=ALERT_KEYBOARD)

    # ---- Rule 4: Pool exhausted ----
    if (pool_remaining == 0
            and prev_pool_remaining not in (0, None)
            and cycle_id == prev_cycle_id):
        msg = (
            "⚠️ <b>Kintara merchant pool is empty!</b>\n\n"
            f"Cycle ID : <b>{cycle_id}</b>   Phase: {phase}"
        )
        print(f"[{_ts()}] ALERT: Pool empty")
        broadcast_telegram(state, None, msg, keyboard=ALERT_KEYBOARD)

    # ---- Save cycle history ----
    if cycle_id and cycle_id != prev_cycle_id and prev_cycle_id is not None:
        history = state.get("history", [])
        entry = {
            "cycleId":  prev_cycle_id,
            "phase":    prev_phase,
            "complete": prev_complete,
            "ended":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        history.insert(0, entry)
        state["history"] = history[:HISTORY_MAX]

    state["public"] = {
        "cycleId":       cycle_id,
        "phase":         phase,
        "complete":      complete,
        "poolRemaining": pool_remaining,
    }
    save_state(state)

    # Get effective interval
    eff_interval = state.get("prefs", {}).get("public_interval", CONFIG["public_interval"])
    print(
        f"[{_ts()}] OK | Cycle {cycle_id} | Phase: {phase} | "
        f"Pool: {fmt(pool_remaining)} | Next in {eff_interval}s"
    )
    return state





# ---------------------------------------------------------------------------
# Alert checks -- private personal status
# ---------------------------------------------------------------------------

def check_private_status(state: dict) -> dict:
    """Fetch personal gold status and alert on changes."""
    if not CONFIG.get("cookie"):
        return state

    headers = {"Cookie": CONFIG["cookie"]}
    data    = fetch_json(PRIVATE_API_URL, headers=headers)

    if data is None or not data.get("ok"):
        print(f"[{_ts()}] Private API unavailable or returned ok=false")
        return state

    my_available = data.get("myAvailable", 0)
    my_claimed   = data.get("myClaimed", 0)
    my_limit     = data.get("myLimit", 0)

    prev         = state.get("private", {})
    prev_avail   = prev.get("myAvailable", 0)
    prev_claimed = prev.get("myClaimed", 0)

    if my_available > 0 and prev_avail == 0:
        msg = (
            "💰 <b>Gold claim available now!</b>\n\n"
            f"  Available  : <b>{fmt(my_available)}</b>\n"
            f"  Claimed    : {fmt(my_claimed)} / {fmt(my_limit)}\n"
            f"  My Points  : {fmt_f(data.get('myPoints'))}\n"
            f"  Pool Left  : {fmt(data.get('poolRemaining'))} / {fmt(data.get('poolFull'))}"
        )
        print(f"[{_ts()}] ALERT: Gold available")
        broadcast_telegram(state, "gold_alerts", msg, keyboard=ALERT_KEYBOARD)

    if (my_claimed > 0 and my_limit > 0
            and my_claimed >= my_limit and prev_claimed < my_limit):
        print(f"[{_ts()}] ALERT: All gold claimed")
        broadcast_telegram(
            state,
            "gold_alerts",
            "🎉 <b>You claimed all gold for this merchant!</b>\n"
            f"Claimed: <b>{fmt(my_claimed)} / {fmt(my_limit)}</b>",
            keyboard=_build_main_keyboard(chat_id)
        )

    state["private"] = {
        "myAvailable": my_available,
        "myClaimed":   my_claimed,
        "myLimit":     my_limit,
    }
    save_state(state)
    print(f"[{_ts()}] Private OK | Avail: {fmt(my_available)} | Claimed: {fmt(my_claimed)}/{fmt(my_limit)}")
    return state


# ---------------------------------------------------------------------------
# Telegram command handler
# ---------------------------------------------------------------------------

def handle_telegram_commands(state: dict, update_offset: list) -> dict:
    """
    Poll Telegram for new messages AND button presses (callback_query).
    update_offset is a mutable list[int] so the offset persists across calls.
    Returns the (possibly updated) state dict.
    """
    updates = get_telegram_updates(update_offset[0])

    for update in updates:
        update_id        = update.get("update_id", 0)
        update_offset[0] = update_id + 1  # advance offset

        # ---------------------------------------------------------------
        # Handle inline button presses (callback_query)
        # ---------------------------------------------------------------
        callback = update.get("callback_query")
        if callback:
            cb_id   = callback.get("id", "")
            cb_data = callback.get("data", "")
            cb_chat = str(callback.get("message", {}).get("chat", {}).get("id", ""))

            # Multi-user support: process callbacks from anyone
            if cb_chat:
                is_sub = cb_chat in state.get("subscribers", {}) or cb_chat == str(CONFIG.get("chat_id"))
                if not is_sub:
                    answer_callback(cb_id, "🔒 Unauthorized. Send /start <code_here> to access.")
                    continue

                if cb_data == "status":
                    answer_callback(cb_id, "Fetching...")
                    state = _cmd_status(state, cb_chat)
                elif cb_data == "help":
                    answer_callback(cb_id)
                    _cmd_help(cb_chat)
                elif cb_data == "settings":
                    answer_callback(cb_id)
                    state = _cmd_settings(state, cb_chat)
                elif cb_data == "start":
                    answer_callback(cb_id)
                    state = _cmd_start(state, cb_chat)
                elif cb_data == "history":
                    answer_callback(cb_id)
                    state = _cmd_history(state, cb_chat)
                elif cb_data == "intervals":
                    answer_callback(cb_id)
                    state = _cmd_show_intervals(state, cb_chat)
                elif cb_data.startswith("interval_"):
                    secs = int(cb_data.split("_")[1])
                    prefs = state.setdefault("prefs", {}).setdefault(cb_chat, {})
                    prefs["public_interval"] = secs
                    save_state(state)
                    answer_callback(cb_id, f"Check interval: {secs}s")
                    print(f"[{_ts()}] User {cb_chat} interval set to {secs}s")
                    state = _cmd_show_intervals(state, cb_chat)
                elif cb_data == "test_alert":
                    answer_callback(cb_id, "Sending test alert...")
                    _cmd_test_alert(cb_chat)
                elif cb_data == "toggle_claim":
                    prefs = state.setdefault("prefs", {}).setdefault(cb_chat, {})
                    prefs["claim_alerts"] = not prefs.get("claim_alerts", True)
                    save_state(state)
                    s = "ON 🟢" if prefs["claim_alerts"] else "OFF 🔴"
                    answer_callback(cb_id, f"Claim alerts: {s}")
                    print(f"[{_ts()}] User {cb_chat} claim alerts: {s}")
                    state = _cmd_settings(state, cb_chat)
                elif cb_data == "toggle_gold":
                    prefs = state.setdefault("prefs", {}).setdefault(cb_chat, {})
                    prefs["gold_alerts"] = not prefs.get("gold_alerts", True)
                    save_state(state)
                    s = "ON 🟢" if prefs["gold_alerts"] else "OFF 🔴"
                    answer_callback(cb_id, f"Gold alerts: {s}")
                    print(f"[{_ts()}] User {cb_chat} gold alerts: {s}")
                    state = _cmd_settings(state, cb_chat)
                elif cb_data == "toggle_repeat":
                    prefs = state.setdefault("prefs", {}).setdefault(cb_chat, {})
                    prefs["repeat_donate"] = not prefs.get("repeat_donate", True)
                    save_state(state)
                    s = "ON 🟢" if prefs["repeat_donate"] else "OFF 🔴"
                    answer_callback(cb_id, f"Repeat alerts: {s}")
                    print(f"[{_ts()}] User {cb_chat} repeat alerts: {s}")
                    state = _cmd_settings(state, cb_chat)
                elif cb_data == "noop":
                    answer_callback(cb_id, "Donation alerts are always ON")
                elif cb_data == "admin_dashboard" and cb_chat == str(CONFIG.get("chat_id")):
                    answer_callback(cb_id)
                    _cmd_admin_dashboard(state, cb_chat)
                elif cb_data.startswith("admin_generate_code_"):
                    answer_callback(cb_id)
                    parts = cb_data.split("_")
                    duration = int(parts[-2])
                    count = int(parts[-1])
                    invites = state.setdefault("invite_codes", {})
                    if isinstance(invites, list):
                        invites = {c: 30 for c in invites}
                        state["invite_codes"] = invites
                    
                    new_codes = []
                    prefix = "KIN-" if duration == 1 else "KINTARA-"
                    for _ in range(count):
                        new_code = prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
                        invites[new_code] = duration
                        new_codes.append(new_code)
                    save_state(state)
                    codes_str = "\n".join([f"<code>{c}</code>" for c in new_codes])
                    send_telegram(f"🔑 Generated {count} ({duration}-Day) Code(s):\n\n{codes_str}\n\nThese will expire once used.", chat_id=cb_chat)
                elif cb_data == "admin_broadcast":
                    answer_callback(cb_id)
                    state["admin_state"] = "awaiting_broadcast"
                    save_state(state)
                    send_telegram("📣 <b>Broadcast Mode</b>\n\nPlease type the message you want to send to all users. (Or send /cancel to abort)", chat_id=cb_chat)
                elif cb_data.startswith("admin_manage_users"):
                    answer_callback(cb_id)
                    parts = cb_data.split("_")
                    page = int(parts[-1]) if parts[-1].isdigit() else 0
                    state = _cmd_admin_users(state, cb_chat, page)
                elif cb_data.startswith("kick_"):
                    target = cb_data.split("_")[1]
                    subs = state.setdefault("subscribers", {})
                    if target in subs:
                        name = subs[target].get("name", "Unknown") if isinstance(subs[target], dict) else "Unknown"
                        kb = {
                            "inline_keyboard": [
                                [{"text": "✅ Yes, Kick", "callback_data": f"dokick_{target}"},
                                 {"text": "❌ Cancel", "callback_data": "admin_manage_users"}]
                            ]
                        }
                        send_telegram(f"⚠️ Are you sure you want to kick <b>{name}</b>?", keyboard=kb, chat_id=cb_chat)
                        answer_callback(cb_id)
                    else:
                        answer_callback(cb_id, "User not found.")
                elif cb_data.startswith("dokick_"):
                    target = cb_data.split("_")[1]
                    subs = state.setdefault("subscribers", {})
                    if target in subs:
                        del subs[target]
                        save_state(state)
                        answer_callback(cb_id, "User kicked!")
                        send_telegram("🛑 Your access has been revoked by the admin.", chat_id=target)
                    else:
                        answer_callback(cb_id, "User not found.")
                    state = _cmd_admin_users(state, cb_chat, 0)
                else:
                    answer_callback(cb_id)
            else:
                answer_callback(cb_id)
            continue

        # ---------------------------------------------------------------
        # Handle regular text messages / slash commands
        # ---------------------------------------------------------------
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        text    = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not chat_id:
            continue

        command = text.split()[0].lower() if text else ""
        
        if "@" in command:
            command = command.split("@")[0]

        is_sub = chat_id in state.get("subscribers", {}) or chat_id == str(CONFIG.get("chat_id"))
        is_owner = chat_id == str(CONFIG.get("chat_id"))
        first_name = message.get("chat", {}).get("first_name", "Unknown")

        if command == "/start":
            if is_sub:
                state = _cmd_start(state, chat_id, first_name)
            else:
                send_telegram("🔒 <b>Welcome!</b>\n\nPlease reply to this message with your Secret Access Code to unlock the bot.", chat_id=chat_id)
            continue
            
        if not is_sub:
            invites = state.get("invite_codes", {})
            if isinstance(invites, list):
                invites = {c: 30 for c in invites}
                state["invite_codes"] = invites
            master_code = CONFIG.get("access_code")
            if text.strip() == master_code or text.strip() in invites:
                duration_days = 36500 if text.strip() == master_code else invites.get(text.strip(), 30)
                if text.strip() in invites:
                    del invites[text.strip()]
                state = _cmd_start(state, chat_id, first_name, duration_days)
            else:
                send_telegram("❌ <b>Invalid code.</b> Please try again.", chat_id=chat_id)
            continue
            
        # Admin Broadcast Handling
        if is_owner and state.get("admin_state") == "awaiting_broadcast":
            if command == "/cancel":
                state["admin_state"] = None
                save_state(state)
                send_telegram("Broadcast cancelled.", chat_id=chat_id)
                continue
            
            subs = state.get("subscribers", {})
            sent_count = 0
            for sub_id in subs.keys():
                if sub_id != str(CONFIG.get("chat_id")):
                    send_telegram(f"📣 <b>Admin Broadcast</b>\n\n{text}", chat_id=sub_id)
                    sent_count += 1
                    
            state["admin_state"] = None
            save_state(state)
            send_telegram(f"✅ Broadcast sent to {sent_count} users.", chat_id=chat_id)
            continue

        if command == "/admin" and is_owner:
            _cmd_admin_dashboard(state, chat_id)
            continue
        elif command == "/stop":
            subs = state.setdefault("subscribers", {})
            if chat_id in subs:
                del subs[chat_id]
                save_state(state)
            send_telegram("🛑 <b>Alerts stopped.</b>\nSend /start to re-subscribe.", chat_id=chat_id)
        elif command == "/status":
            state = _cmd_status(state, chat_id)
        elif command == "/help":
            _cmd_help(chat_id)
        elif command == "/settings":
            state = _cmd_settings(state, chat_id)
        elif command == "/history":
            state = _cmd_history(state, chat_id)
        else:
            if text.startswith("/"):
                send_telegram(
                    "❓ Unknown command. Tap ⚙️ Settings or /help.",
                    keyboard=_build_main_keyboard(chat_id),
                    chat_id=chat_id
                )

    return state


def _cmd_start(state: dict, chat_id: str, first_name: str = "Unknown", duration_days: int = 30) -> dict:
    subs = state.setdefault("subscribers", {})
    if chat_id not in subs:
        expires_at = (datetime.now() + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M")
        subs[chat_id] = {"name": first_name, "joined": datetime.now().strftime("%Y-%m-%d %H:%M"), "expires_at": expires_at}
        save_state(state)
        print(f"[{_ts()}] New subscriber added: {first_name} ({chat_id})")

    prefs        = {}
    eff_interval = CONFIG["public_interval"]
    cookie_status = (
        f"every {CONFIG['private_interval']}s"
        if CONFIG.get("cookie") else "Not set"
    )
    user_info = state.get("subscribers", {}).get(chat_id, {})
    expires_at = user_info.get("expires_at", "Never") if isinstance(user_info, dict) else "Never"
    
    msg = (
        "🤖 <b>Kintara Merchant Alert Bot</b>\n\n"
        f"✅ <b>Access Granted!</b> Welcome, {first_name}.\n"
        f"⏳ <b>Subscription Expires:</b> {expires_at}\n\n"
        f"📡 Check interval : <b>{eff_interval}s</b>  (silent — alerts on change only)\n"
        f"🔐 Personal       : <b>{cookie_status}</b>\n\n"
        "Tap a button below:"
    )
    send_telegram(msg, keyboard=_build_main_keyboard(chat_id), chat_id=chat_id)
    print(f"[{_ts()}] /start for {chat_id}")
    return state


def _cmd_admin_dashboard(state: dict, chat_id: str) -> None:
    kb = {
        "inline_keyboard": [
            [{"text": "🔑 Gen 1 Trial (1 Day)", "callback_data": "admin_generate_code_1_1"},
             {"text": "🔑 Gen 5 Trial", "callback_data": "admin_generate_code_1_5"}],
            [{"text": "🔑 Gen 1 Premium (30 Day)", "callback_data": "admin_generate_code_30_1"},
             {"text": "🔑 Gen 5 Premium", "callback_data": "admin_generate_code_30_5"}],
            [{"text": "📣 Broadcast", "callback_data": "admin_broadcast"}],
            [{"text": "👥 Manage Users", "callback_data": "admin_manage_users_0"}],
        ]
    }
    send_telegram("👑 <b>Admin Dashboard</b>\n\nSelect an option below:", keyboard=kb, chat_id=chat_id)


def _cmd_admin_users(state: dict, chat_id: str, page: int = 0) -> dict:
    subs = state.get("subscribers", {})
    if not subs:
        send_telegram("No other users are currently subscribed.", chat_id=chat_id)
        return state
        
    kb = {"inline_keyboard": []}
    user_list = []
    
    active_subs = {k: v for k, v in subs.items() if k != str(CONFIG.get("chat_id"))}
    sub_keys = list(active_subs.keys())
    
    per_page = 10
    total_pages = max(1, (len(sub_keys) + per_page - 1) // per_page)
    page = min(max(0, page), total_pages - 1)
    
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_keys = sub_keys[start_idx:end_idx]
    
    for sub_id in page_keys:
        info = active_subs[sub_id]
        name = info.get("name", "Unknown") if isinstance(info, dict) else "Unknown"
        joined = info.get("joined", "Unknown Date") if isinstance(info, dict) else "Unknown Date"
        expires = info.get("expires_at", "Never") if isinstance(info, dict) else "Never"
        user_list.append(f"• <b>{name}</b> (Joined: {joined}, Exp: {expires})")
        kb["inline_keyboard"].append([
            {"text": f"Kick {name}", "callback_data": f"kick_{sub_id}"}
        ])
        
    nav_row = []
    if page > 0:
        nav_row.append({"text": "◀️ Prev", "callback_data": f"admin_manage_users_{page-1}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Next ▶️", "callback_data": f"admin_manage_users_{page+1}"})
    if nav_row:
        kb["inline_keyboard"].append(nav_row)
        
    kb["inline_keyboard"].append([{"text": "◀️ Back", "callback_data": "admin_dashboard"}])
    
    users_text = "\n".join(user_list) if user_list else "No other users."
    send_telegram(f"👥 <b>Manage Users (Page {page+1}/{total_pages})</b>\n\n{users_text}\n\nTap a user below to kick them:", keyboard=kb, chat_id=chat_id)
    return state


def _cmd_settings(state: dict, chat_id: str) -> dict:
    """Show the alert toggle settings panel."""
    prefs     = state.get("prefs", {}).get(str(chat_id), {})
    claim_on  = prefs.get("claim_alerts",  True)
    gold_on   = prefs.get("gold_alerts",   True)
    repeat_on = prefs.get("repeat_donate", True)
    interval  = prefs.get("public_interval", CONFIG["public_interval"])

    msg = (
        "⚙️ <b>Alert Settings</b>\n\n"
        f"🔔 Donation Alert  : <b>🟢 ON (always)</b>\n"
        f"🔁 Repeat Donate   : <b>{'🟢 ON' if repeat_on else '🔴 OFF'}</b> (every {CONFIG['repeat_mins']} min)\n"
        f"🏆 Claim Alert     : <b>{'🟢 ON' if claim_on else '🔴 OFF'}</b>\n"
        f"💰 Gold Alert      : <b>{'🟢 ON' if gold_on else '🔴 OFF'}</b>\n"
        f"⏱ Check Interval  : <b>{interval}s</b>\n\n"
        "Tap a row to toggle."
    )
    send_telegram(msg, keyboard=_build_settings_keyboard(state, chat_id), chat_id=chat_id)
    print(f"[{_ts()}] /settings for {chat_id}")
    return state


def _cmd_show_intervals(state: dict, chat_id: str) -> dict:
    """Show the interval picker keyboard."""
    interval = state.get("prefs", {}).get(str(chat_id), {}).get("public_interval", CONFIG["public_interval"])
    send_telegram(
        f"⏱ <b>Check Interval</b>\n\nCurrently: <b>{interval}s</b>\nTap to change:",
        keyboard=_build_interval_keyboard(state, chat_id),
        chat_id=chat_id
    )
    return state


def _cmd_test_alert(chat_id: str) -> None:
    """Send a fake donate alert so the user can test their notification sound."""
    msg = (
        "🧪 <b>[TEST] Merchant donation open!</b>\n\n"
        "This is a test alert — no real merchant event occurred.\n\n"
        "Wood        : ████████░░ 80%  1,000,000/1,250,000\n"
        "Stone       : █████░░░░░ 50%  375,000/750,000\n"
        "Coal        : ██████████ 100% 500,000/500,000\n"
        "Cooked Fish : ███░░░░░░░ 30%  60,000/200,000\n"
        "Metal       : ████████░░ 80%  160,000/200,000\n\n"
        "Pool Left   : 1,500 / 1,500"
    )
    send_telegram(msg, keyboard=ALERT_KEYBOARD, chat_id=chat_id)
    print(f"[{_ts()}] Test alert sent to {chat_id}")


def _cmd_history(state: dict, chat_id: str) -> dict:
    """Show the last few completed cycles."""
    history = state.get("history", [])
    pub     = state.get("public", {})

    lines = ["📅 <b>Cycle History</b>\n"]

    # Current cycle
    if pub.get("cycleId"):
        lines.append(
            f"<b>Cycle {pub['cycleId']}</b> — {pub.get('phase','?')} phase "
            f"({'✅ done' if pub.get('complete') else '⏳ active'})  ← current"
        )

    if history:
        lines.append("")
        for entry in history:
            lines.append(
                f"Cycle {entry.get('cycleId','?')} — "
                f"{entry.get('phase','?')} | "
                f"{'✅' if entry.get('complete') else '❌'} | "
                f"{entry.get('ended','?')}"
            )
    else:
        lines.append("\nNo past cycles recorded yet.")

    hist_keyboard = {
        "inline_keyboard": [
            [{"text": "📊 Status", "callback_data": "status"},
             {"text": "◀️ Back",  "callback_data": "start"}]
        ]
    }
    send_telegram("\n".join(lines), keyboard=hist_keyboard, chat_id=chat_id)
    print(f"[{_ts()}] /history for {chat_id}")
    return state


def _cmd_status(state: dict, chat_id: str) -> dict:
    """Fetch fresh data and send current status with countdown."""
    parts = ["📊 <b>Current Merchant Status</b>\n"]

    data = fetch_json(PUBLIC_API_URL)
    if data and data.get("ok"):
        parts.append(format_merchant_status(data))
    else:
        parts.append("⚠️ Could not fetch public merchant data.")

    # Expansion tribute (removed)

    # Personal
    if CONFIG.get("cookie"):
        parts.append("")
        pdata = fetch_json(PRIVATE_API_URL, headers={"Cookie": CONFIG["cookie"]})
        if pdata and pdata.get("ok"):
            parts.append(format_personal_status(pdata))
        else:
            parts.append("⚠️ Personal status unavailable (check cookie).")

    eff_interval = state.get("prefs", {}).get(str(chat_id), {}).get("public_interval", CONFIG["public_interval"])
    parts.append(f"\n<i>Checks every {eff_interval}s — alerts only on change</i>")

    status_kb = {
        "inline_keyboard": [
            [{"text": "🔄 Refresh",    "callback_data": "status"},
             {"text": "📅 History",    "callback_data": "history"}],
            [{"text": "⚙️ Settings",  "callback_data": "settings"},
             {"text": "❓ Help",       "callback_data": "help"}],
        ]
    }
    send_telegram("\n".join(parts), keyboard=status_kb, chat_id=chat_id)
    print(f"[{_ts()}] /status for {chat_id}")
    return state


def _cmd_help(chat_id: str) -> None:
    msg = (
        "ℹ️ <b>Kintara Merchant Alert Bot — Guide</b>\n\n"
        "<b>📌 Basic Commands</b>\n"
        "  /start    — Unlock the bot & see status\n"
        "  /status   — Check live merchant state\n"
        "  /settings — Personalize your alerts\n"
        "  /history  — View past merchant cycles\n"
        "  /help     — This guide\n\n"
        "<b>🔔 Automatic Alerts</b>\n"
        "  • <b>Donation Open:</b> Instant alert when merchant arrives\n"
        f"  • <b>Reminders:</b> Every {CONFIG.get('repeat_mins', 1)} min while donation is open\n"
        "  • <b>Claim Phase:</b> Alerted when claiming begins\n"
        "  • <b>Pool Filled:</b> Alerted when gold pool reaches 100%\n\n"
        "<b>🔒 Security Note</b>\n"
        "<i>This bot is private. New users must reply with the secret Access Code to receive alerts.</i>"
    )
    send_telegram(msg, keyboard=_build_main_keyboard(chat_id), chat_id=chat_id)
    print(f"[{_ts()}] /help for {chat_id}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _ts() -> str:
    """Return a short timestamp string for console logs."""
    return datetime.now().strftime("%H:%M:%S")


def setup_bot_commands() -> None:
    """
    Register commands with Telegram so the blue Menu button appears
    in the chat input bar. Users can tap it to see all available commands.
    """
    commands = [
        {"command": "status",   "description": "📊 Live merchant status"},
        {"command": "settings", "description": "⚙️ Alert toggles & check speed"},
        {"command": "history",  "description": "📅 Past cycle history"},
        {"command": "start",    "description": "🤖 Bot info & status"},
        {"command": "help",     "description": "❓ All commands & alerts"},
    ]
    try:
        # Register command list (shows in Menu button)
        r1 = requests.post(
            _tg_url("setMyCommands"),
            json={"commands": commands},
            timeout=REQUEST_TIMEOUT,
        )
        # Set menu button type to 'commands' (shows blue Menu button)
        r2 = requests.post(
            _tg_url("setChatMenuButton"),
            json={"menu_button": {"type": "commands"}},
            timeout=REQUEST_TIMEOUT,
        )
        if r1.json().get("ok") and r2.json().get("ok"):
            print("  Menu button    : registered (tap Menu in Telegram)")
        else:
            print(f"  Menu button    : [WARN] {r1.text[:80]}")
    except Exception as exc:
        print(f"  Menu button    : [WARN] could not register: {exc}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global CONFIG
    CONFIG = load_env()

    print("=" * 60)
    print("  Kintara Merchant Alert Bot - Enhanced Edition")
    print("=" * 60)
    print(f"  Public check   : every {CONFIG['public_interval']}s (default)")
    print(f"  Repeat alerts  : every {CONFIG['repeat_mins']} min while donate open")
    if CONFIG.get("cookie"):
        print(f"  Private check  : every {CONFIG['private_interval']}s")
    else:
        print("  Private check  : disabled (KINTARA_COOKIE not set)")
    print(f"  State file     : {STATE_FILE}")
    setup_bot_commands()
    print("=" * 60)
    print("Running. Send /start in Telegram or tap the Menu button.\n")

    state = load_state()

    last_public_check   = 0.0
    last_private_check  = 0.0
    update_offset       = [0]

    send_telegram(
        "🤖 <b>Kintara Merchant Alert Bot started!</b>\n"
        "Tap 📊 Status to see the current state:",
        keyboard=_build_main_keyboard(CONFIG["chat_id"]),
    )

    while True:
        now = time.time()
        # Use interval from state prefs if user changed it via buttons
        pub_interval = state.get("prefs", {}).get(
            "public_interval", CONFIG["public_interval"]
        )

        try:
            state = handle_telegram_commands(state, update_offset)

            if now - last_public_check >= pub_interval:
                state             = check_public_merchant(state)
                last_public_check = time.time()

            if (CONFIG.get("cookie")
                    and now - last_private_check >= CONFIG["private_interval"]):
                state              = check_private_status(state)
                last_private_check = time.time()

            # --- Auto-kick Expired Users ---
            if now - state.get("last_expire_check", 0) >= 3600:  # Check every hour
                state["last_expire_check"] = now
                subs = state.get("subscribers", {})
                expired = []
                for sub_id, info in subs.items():
                    if sub_id == str(CONFIG.get("chat_id")):
                        continue
                    if isinstance(info, dict):
                        expires_at = info.get("expires_at")
                        if expires_at and expires_at != "Never":
                            try:
                                exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M")
                                if datetime.now() > exp_dt:
                                    expired.append(sub_id)
                            except ValueError:
                                pass
                for exp_id in expired:
                    del subs[exp_id]
                    send_telegram("🛑 Your subscription has expired! Please provide a new Access Code via /start to regain access.", chat_id=exp_id)
                    logging.info(f"User {exp_id} expired and was kicked.")
                if expired:
                    save_state(state)

        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except Exception:
            print(f"[{_ts()}] Unexpected error:\n{traceback.format_exc()}")

        time.sleep(1)


if __name__ == "__main__":
    main()
