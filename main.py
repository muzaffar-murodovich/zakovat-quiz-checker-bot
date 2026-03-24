import os
import time
import json
import requests
import threading
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BOT_TOKEN = os.environ.get("BOT_TOKEN")
QUIZ_ID = 930
URL = f"https://zakovatklubi.uz/tournaments/{QUIZ_ID}"

USERS_FILE = "users.json"
CHECK_INTERVAL = 5

KEY_WORD = "Ishtirok etish"

offset = 0
notified = False
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

    print("Bot ishga tushdi")

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
                        "Roʻyxatdan o‘tish ochilishi bilan xabar yuboraman"
                    )

        except Exception as e:
            print("Bot xatosi:", e)

        time.sleep(2)

def selenium_checker_loop():
    global notified
    counter = 0
    print("Selenium monitoring boshlandi...")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=options)

    try:
        while not notified:
            driver.get(URL)
            time.sleep(5)

            # Redirect bo'lgan bo'lsa, sahifa hali mavjud emas
            if driver.current_url.rstrip("/") != URL.rstrip("/"):
                counter += 1
                print(f"{counter}. Sahifa hali mavjud emas (redirect)...")
                time.sleep(CHECK_INTERVAL)
                continue

            page = driver.page_source

            if KEY_WORD in page:
                notified = True
                broadcast(
                    "OCHILDI!\n\n"
                    f"👉 {URL}"
                )
                print("Xabar yuborildi.")
                break

            counter += 1
            print(f"{counter}. Hozircha yopiq...")
            time.sleep(CHECK_INTERVAL)

    finally:
        driver.quit()

if __name__ == "__main__":
    t1 = threading.Thread(target=telegram_bot_loop, daemon=True)
    t2 = threading.Thread(target=selenium_checker_loop, daemon=True)

    t1.start()
    t2.start()

    while True:
        time.sleep(1)