# CLAUDE.md

Zakovat "Quiz" turnirlarini kuzatib, obunachilarга xabar beruvchi va foydalanuvchining jamoasini avtomatik roʻyxatga yozuvchi Telegram bot.

## Nima qiladi

1. **Kuzatadi** — `api.zakovatklubi.uz/v1/tournament/last` ni poll qilib, nomida "Zakovat Quiz" bor yangi turnir roʻyxati ochilishini kutadi.
2. **Xabar beradi** — roʻyxat ochilganda barcha Telegram obunachilariga (`/start` bosganlar) "OCHILDI!" yuboradi.
3. **Roʻyxatga yozadi** — xabardan `REGISTER_DELAY` (10s) keyin foydalanuvchining jamoasini API orqali turnirga yozadi va natijani admin'ga xabar qiladi.

## Arxitektura

Ikkita fayl, uchta thread (`main.py` da ishga tushadi):

- **[main.py](main.py)** — ikki loop bitta jarayonda:
  - `telegram_bot_loop()` — Telegram `getUpdates` long-polling; `/start` bosgan chat'larni `users.json` ga yozadi.
  - `api_checker_loop()` — turnir API'sini poll qiladi, trigger boʻlganda `broadcast()` va (alohida thread'da) `register_after_delay()` chaqiradi.
- **[registrar.py](registrar.py)** — brauzersiz API registratsiya: `login()` (`POST /v1/user/sign-in` → Bearer token) → `get_team_members()` (`GET /v1/user/leader-team`) → `register()` (`POST /v1/user/request`).

**Selenium/Playwright ishlatilmaydi** — hammasi to'g'ridan-to'g'ri JSON API orqali. (Eski brauzer-asosli variant `~/tech/zk-auto` da arxivlangan.)

## Vaqt oynasi mantigʻi

Roʻyxatdan oʻtish **faqat dushanba (kamdan-kam seshanba) Toshkent vaqti 10:30–14:30** da ochiladi, haftada bitta quiz. Shuning uchun bot faqat **Du/Se 10:15–14:45** oynasida (15 daq zaxira) poll qiladi; oynadan tashqarida va shu hafta allaqachon xabar yuborilgan boʻlsa — kelasi dushanbagacha uxlaydi. Mantiq `next_poll_wait()` da, `WINDOW_DAYS/WINDOW_START/WINDOW_END` konstantalari bilan. Vaqt `Asia/Tashkent` da hisoblanadi (server UTC boʻlsa ham).

## State

- `users.json` — obunachi chat ID'lari (git'da yoʻq, runtime'da yaratiladi).
- `state.json` — `{notified_ids, last_notified_at, registered_ids}`. Idempotentlik: bir turnirga bir marta xabar + bir marta registratsiya. `load_state()` eski formatlarni (oddiy list, `registered_ids`siz dict) ham oʻqiydi.

## Konfiguratsiya (`.env`, git'da yoʻq)

| Kalit | Vazifa |
|---|---|
| `BOT_TOKEN` | Telegram bot tokeni (@zquizcheckerbot) |
| `POLL_INTERVAL` | oyna ichida poll oralig'i, soniya (hozir 3) |
| `FILTER_KEYWORD` | turnir filtri (default "zakovat quiz") |
| `ZK_PHONE`, `ZK_PASSWORD` | zakovatklubi.uz login (registratsiya uchun) |
| `TEAM_ID` | roʻyxatga yoziladigan jamoa (default 28631, Baurlar) |
| `ADMIN_CHAT_ID` | registratsiya natijasi yuboriladigan chat |
| `REGISTER_DELAY` | OCHILDI'dan keyin registratsiyagacha kutish (default 10s) |
| `REGISTER_VERIFY_DELAY` | POST'dan keyin ro'yxatda tasdiqni tekshirishgacha kutish, admin tasdiqlashi uchun (default 1800s / 30 daq) |
| `MAIN_PERSON_IDS` | zaxira aʼzo ID'lari (leader-team olinmasa) |

## Ishga tushirish

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

`Pipfile` mavjud, lekin serverda oddiy venv + `requirements.txt` ishlatiladi.

## Deploy

Serverda (`ssh myserver`, Ubuntu, user `deploy`) **user-level systemd** servis sifatida 24/7 ishlaydi — parolsiz sudo yoʻqligi uchun `systemctl --user`.

```bash
./deploy/deploy.sh        # rsync + pip + systemctl --user restart
```

`.env`, `users.json`, `state.json` deploy'dan exclude qilinadi — serverda saqlanadi.

- Holat: `ssh myserver systemctl --user status zakovat-bot`
- Loglar: `ssh myserver journalctl --user -u zakovat-bot -f`

## Muhim tafsilotlar

- **Login limiti:** notoʻgʻri parolда 5 urinishdan keyin akkaunt bloklanadi. `registrar.login()` xato parolni aniqlasa `_auth_blocked` bilan qayta urinmaydi.
- **Token yangiligi:** token opaque (muddat oʻqib boʻlmaydi). Registratsiya haftada bir marta boʻlgani uchun `register()` har safar `login(force=True)` bilan yangi token oladi — eskirish muammosi boʻlmaydi.
- **`match_id` faqat `type_request == 2` da yuboriladi** (manba: `GET /tournament/{id}/accessible-match`). Zakovat Quiz `type_request == 1` — `match_id` UMUMAN yuborilmaydi. `mainPersonIds` da kapitanning o'z ID'si (`GET /user/get-me`) bo'lmasligi kerak — sayt frontend'i uni filtrlaydi. 2026-07-13 da noto'g'ri `match_id = tournament_id` yuborilgan: API "success" qaytargan, lekin jamoa ro'yxatga tushmagan.
- **Registratsiya tekshiriladi, lekin kechiktirib:** `GET /tournament/{id}/teams` ro'yxati POST'dan darhol keyin emas — admin jamoani qo'lda tasdiqlagandan keyin yangilanadi. Shu sabab `registrar.register()` POST'dan keyin darhol tekshirmaydi (avval tekshirar edi va deyarli har doim yolg'on "XATO" berardi); `main.py:verify_after_delay()` `REGISTER_VERIFY_DELAY` (default 30 daq) dan keyin `registrar.check_registration()` bilan alohida tekshiradi va faqat shunda hali ko'rinmasa admin'ga ma'lumot beradi (bu ⚠️ emas, ℹ️ — kunlab cho'zilishi mumkinligi sabab xato degani emas).
- Registratsiya xatolari hech qachon loop'larni yiqitmaydi (hammasi try/except, alohida thread).
- Loglar `print(..., flush=True)` — journalctl'da darhol koʻrinishi uchun.
