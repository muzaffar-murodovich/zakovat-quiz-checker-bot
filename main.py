import os
import time
import json
import requests
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "zakovat quiz")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))

API_URL = "https://api.zakovatklubi.uz/v1/tournament/last"
TOURNAMENT_URL = "https://zakovatklubi.uz/tournaments/{id}"

USERS_FILE = "users.json"
STATE_FILE = "state.json"

TZ = ZoneInfo("Asia/Tashkent")

# Ro'yxatdan o'tish faqat dushanba (kamdan-kam seshanba) kuni 10:30-14:30
# oralig'ida ochiladi; oyna 15 daqiqa zaxira bilan olingan.
WINDOW_DAYS = (0, 1)
WINDOW_START = (10, 15)
WINDOW_END = (14, 45)

offset = 0
lock = threading.Lock()

def load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(users), f)

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data), None
        return set(data.get("notified_ids", [])), data.get("last_notified_at")
    except Exception:
        return set(), None

def save_state(notified_ids, last_notified_at):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"notified_ids": sorted(notified_ids), "last_notified_at": last_notified_at},
            f
        )

def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=10
    )

def broadcast(text):
    users = load_users()
    for chat_id in users:
        try:
            send_message(chat_id, text)
        except Exception:
            pass

def telegram_bot_loop():
    global offset

    print("Bot ishga tushdi", flush=True)

    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )

            updates = r.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text", "")

                if text == "/start":
                    with lock:
                        users = load_users()
                        if chat_id not in users:
                            users.add(chat_id)
                            save_users(users)

                    send_message(
                        chat_id,
                        "Botga muvaffaqiyatli ulandingiz\n"
                        "Roʻyxatdan o‘tish ochilishi bilan xabar yuboriladi"
                    )

        except Exception as e:
            print("Bot xatosi:", e, flush=True)

        time.sleep(2)

def fetch_tournaments():
    r = requests.get(API_URL, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])

def matches_filter(tournament):
    return FILTER_KEYWORD.lower() in tournament.get("title", "").lower()

def week_start(dt):
    monday = (dt - timedelta(days=dt.weekday())).date()
    return datetime.combine(monday, datetime.min.time(), tzinfo=TZ)

def next_poll_wait(now, last_notified_at):
    """0 — hozir tekshirish oynasidamiz; aks holda keyingi oynagacha soniya.

    Haftada bitta quiz ochiladi: shu hafta xabar yuborilgan bo'lsa,
    kelasi dushanbagacha tekshiruv kerak emas.
    """
    base = week_start(now)
    satisfied = (
        last_notified_at is not None
        and datetime.fromtimestamp(last_notified_at, TZ) >= base
    )

    candidates = []
    for week in (0, 1):
        if week == 0 and satisfied:
            continue
        for day_idx in WINDOW_DAYS:
            day = base + timedelta(weeks=week, days=day_idx)
            start = day.replace(hour=WINDOW_START[0], minute=WINDOW_START[1])
            end = day.replace(hour=WINDOW_END[0], minute=WINDOW_END[1])
            if now > end:
                continue
            if now >= start:
                return 0
            candidates.append(start)

    return (min(candidates) - now).total_seconds()

def check_once(tournaments, notified, now_ts):
    """Filtrga mos, hali xabar qilinmagan, ro'yxati ochiq turnirlar."""
    opened = []
    for t in tournaments:
        if not matches_filter(t):
            continue

        start = t.get("start_submission_request_date")
        end = t.get("end_submission_request_date")
        if not start or not end:
            continue

        if t["id"] not in notified and start <= now_ts <= end:
            opened.append(t)

    return opened

def api_checker_loop():
    notified, last_notified_at = load_state()
    counter = 0
    announced = None
    print("API monitoring boshlandi...", flush=True)

    while True:
        now = datetime.now(TZ)
        wait = next_poll_wait(now, last_notified_at)

        if wait > 0:
            resume = now + timedelta(seconds=wait)
            label = f"{resume:%d.%m %H:%M}"
            if label != announced:
                announced = label
                print(f"Oyna yopiq. Keyingi tekshiruv: {label} (Toshkent)", flush=True)
            time.sleep(min(wait, 3600))
            continue

        announced = None

        try:
            tournaments = fetch_tournaments()
            now_ts = int(time.time())

            for t in check_once(tournaments, notified, now_ts):
                broadcast(
                    "OCHILDI!\n\n"
                    f"{t['title']}\n"
                    f"👉 {TOURNAMENT_URL.format(id=t['id'])}"
                )
                notified.add(t["id"])
                last_notified_at = now_ts
                save_state(notified, last_notified_at)
                print(f"Xabar yuborildi: {t['id']} — {t['title']}", flush=True)

            counter += 1
            if counter % 60 == 0:
                print(f"{counter}. Oyna ochiq, hozircha yangi quiz yo'q...", flush=True)

        except Exception as e:
            print("Monitoring xatosi:", e, flush=True)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    t1 = threading.Thread(target=telegram_bot_loop, daemon=True)
    t2 = threading.Thread(target=api_checker_loop, daemon=True)

    t1.start()
    t2.start()

    while True:
        time.sleep(1)
