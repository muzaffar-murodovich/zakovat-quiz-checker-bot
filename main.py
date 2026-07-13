import os
import time
import json
import requests
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

import registrar

BOT_TOKEN = os.environ.get("BOT_TOKEN")
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "zakovat quiz")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
REGISTER_DELAY = int(os.environ.get("REGISTER_DELAY", "10"))

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
            return set(data), None, set()
        return (
            set(data.get("notified_ids", [])),
            data.get("last_notified_at"),
            set(data.get("registered_ids", [])),
        )
    except Exception:
        return set(), None, set()

def save_state(notified_ids, last_notified_at, registered_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "notified_ids": sorted(notified_ids),
                "last_notified_at": last_notified_at,
                "registered_ids": sorted(registered_ids),
            },
            f
        )

# Keep-alive session: har xabarda yangi TLS ulanish ochilmasin.
# Parallel broadcast uchun pool obunachilar soniga yetarli bo'lishi kerak.
_tg_session = requests.Session()
_tg_session.mount(
    "https://",
    requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=32),
)

def send_message(chat_id, text):
    _tg_session.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=10
    )

def _send_safe(chat_id, text):
    try:
        send_message(chat_id, text)
    except Exception:
        pass

def broadcast(text):
    """Barcha obunachilarga PARALLEL yuboradi.

    Ketma-ket yuborish Telegram sekinlashganda obunachi boshiga ~1s+
    yo'qotadi (2026-07-13 da 10 obunachi = ~13s kechikish bo'lgan).
    """
    users = load_users()
    started = time.time()
    threads = []
    for chat_id in users:
        th = threading.Thread(target=_send_safe, args=(chat_id, text), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(timeout=15)
    print(f"Broadcast: {len(users)} obunachi, {time.time() - started:.1f}s", flush=True)

def telegram_bot_loop():
    global offset

    print("Bot ishga tushdi", flush=True)

    while True:
        try:
            r = _tg_session.get(
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

# Alohida keep-alive session — TLS handshake har poll'da takrorlanmasin.
# Faqat checker thread ishlatadi.
_api_session = requests.Session()

def fetch_tournaments():
    # Qisqa timeout: ochilish daqiqasida API sekinlashsa ham poll uzoq
    # qotib qolmasin (xato -> POLL_INTERVAL kutib qayta urinish).
    r = _api_session.get(API_URL, timeout=(3, 5))
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

def register_after_delay(tournament, type_request, on_done):
    """OCHILDI xabaridan REGISTER_DELAY soniya keyin Baurlar'ni yozadi.

    Alohida thread'da ishlaydi — checker loop'ni bloklamaydi. Natija
    faqat ADMIN_CHAT_ID ga yuboriladi. Tugagach on_done(tournament_id)
    chaqiriladi (registered_ids ni saqlash uchun).
    """
    time.sleep(REGISTER_DELAY)
    url = TOURNAMENT_URL.format(id=tournament["id"])
    try:
        ok, msg = registrar.register(tournament["id"], type_request=type_request)
    except Exception as e:
        ok, msg = False, f"kutilmagan xato: {e!r}"

    if ok:
        print(f"Registratsiya OK: {tournament['id']} — {msg}", flush=True)
        text = f"✅ {tournament['title']}\n{msg}"
    else:
        print(f"Registratsiya XATO: {tournament['id']} — {msg}", flush=True)
        text = (
            f"❌ Baurlar ro'yxatdan o'tolmadi\n{tournament['title']}\n"
            f"Sabab: {msg}\n👉 qo'lda: {url}"
        )

    on_done(tournament["id"])

    if ADMIN_CHAT_ID:
        try:
            send_message(ADMIN_CHAT_ID, text)
        except Exception as e:
            print(f"Admin xabari yuborilmadi: {e!r}", flush=True)


def api_checker_loop():
    notified, last_notified_at, registered = load_state()
    counter = 0
    announced = None
    print("API monitoring boshlandi...", flush=True)

    def mark_registered(tid):
        # Register thread'idan (10s keyin) chaqiriladi. Fayldan qayta o'qib
        # merge qilamiz — shu orada loop yangilagan notified/last_notified_at
        # qiymatlarini bosib yozib yubormaslik uchun.
        with lock:
            n, lna, reg = load_state()
            reg.add(tid)
            registered.add(tid)
            save_state(n, lna, reg)

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
                print(f"TOPILDI: {t['id']} — {t['title']}", flush=True)
                broadcast(
                    "OCHILDI!\n\n"
                    f"{t['title']}\n"
                    f"👉 {TOURNAMENT_URL.format(id=t['id'])}"
                )
                notified.add(t["id"])
                last_notified_at = now_ts
                save_state(notified, last_notified_at, registered)
                print(f"Xabar yuborildi: {t['id']} — {t['title']}", flush=True)

                # OCHILDI xabaridan REGISTER_DELAY (10s) keyin Baurlar'ni yozamiz.
                # Alohida thread — loop bloklanmaydi, boshqa quiz'lar tekshirilaveradi.
                if t["id"] not in registered:
                    threading.Thread(
                        target=register_after_delay,
                        args=(t, t.get("type_request", 1), mark_registered),
                        daemon=True,
                    ).start()

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
