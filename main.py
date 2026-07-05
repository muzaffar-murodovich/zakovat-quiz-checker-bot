import os
import time
import json
import requests
import threading
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "zakovat quiz")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "20"))

API_URL = "https://api.zakovatklubi.uz/v1/tournament/last"
TOURNAMENT_URL = "https://zakovatklubi.uz/tournaments/{id}"

USERS_FILE = "users.json"
STATE_FILE = "state.json"

# Ochilishga shuncha soniya qolganda tez polling rejimiga o'tiladi
FAST_WINDOW = 180
FAST_INTERVAL = 2

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

def load_notified():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_notified(ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f)

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

def check_once(tournaments, notified, now):
    """Yangi ochilgan turnirlar ro'yxatini va keyingi ochilishgacha qolgan vaqtni qaytaradi."""
    opened = []
    next_opening = None

    for t in tournaments:
        if not matches_filter(t):
            continue

        start = t.get("start_submission_request_date")
        end = t.get("end_submission_request_date")
        if not start or not end:
            continue

        if t["id"] not in notified and start <= now <= end:
            opened.append(t)
        elif now < start:
            wait = start - now
            if next_opening is None or wait < next_opening:
                next_opening = wait

    return opened, next_opening

def api_checker_loop():
    notified = load_notified()
    counter = 0
    print("API monitoring boshlandi...", flush=True)

    while True:
        try:
            tournaments = fetch_tournaments()
            now = int(time.time())
            opened, next_opening = check_once(tournaments, notified, now)

            for t in opened:
                broadcast(
                    "OCHILDI!\n\n"
                    f"{t['title']}\n"
                    f"👉 {TOURNAMENT_URL.format(id=t['id'])}"
                )
                notified.add(t["id"])
                save_notified(notified)
                print(f"Xabar yuborildi: {t['id']} — {t['title']}", flush=True)

            counter += 1
            if next_opening is not None and next_opening <= FAST_WINDOW:
                interval = FAST_INTERVAL
                print(f"{counter}. Ochilishga {next_opening}s qoldi, tez rejim...", flush=True)
            else:
                interval = CHECK_INTERVAL
                if counter % 30 == 0:
                    print(f"{counter}. Hozircha yangi turnir yo'q...", flush=True)

        except Exception as e:
            interval = CHECK_INTERVAL
            print("Monitoring xatosi:", e, flush=True)

        time.sleep(interval)

if __name__ == "__main__":
    t1 = threading.Thread(target=telegram_bot_loop, daemon=True)
    t2 = threading.Thread(target=api_checker_loop, daemon=True)

    t1.start()
    t2.start()

    while True:
        time.sleep(1)
