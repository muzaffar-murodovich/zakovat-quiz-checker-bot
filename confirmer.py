"""Jamoa a'zolari nomidan turnir taklifini avtomatik tasdiqlash.

Kapitan `/user/request` bilan so'rov yuborgach, har bir a'zoga taklif
("Siz {jamoa} bilan {turnir} turnirida qatnashmoqchimisiz?") boradi va jamoa
moderatsiyaga faqat HAMMA a'zo taklifni qabul qilgandan keyin o'tadi.

Sayt shu ish uchun quyidagi endpoint'lardan foydalanadi (main.js bundle'dan
aniqlangan, `/profile/transfers` sahifasi):

    GET  /user/transfer-request               -> kelgan takliflar ro'yxati
    POST /user/accept-transfer-request/{id}   -> "Qabul qilish"
    POST /user/cancel-transfer-request/{id}   -> "Rad etish"

Taklif obyektida `is_owner == 0` — taklif SIZGA kelgan degani (1 bo'lsa siz
yuborgansiz). `type`: 3/4 — turnirda ishtirok etish takliflari.

A'zolar login ma'lumotlari `members.json` da (git'da yo'q, deploy'dan
exclude qilinadi):

    [
      {"name": "Ali", "phone": "+998901234567", "password": "..."},
      {"name": "Vali", "phone": "+998907654321", "password": "..."}
    ]

Kapitanning o'zi bu ro'yxatда bo'lishi shart emas — u so'rov yuborgani uchun
tasdiqlashi kerak emas.

Qo'lda ishlatish:
    python confirmer.py --list            # har bir a'zoning kutayotgan takliflari
    python confirmer.py <tournament_id>   # shu turnir taklifini tasdiqlash
"""
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import requests

API_ROOT = "https://api.zakovatklubi.uz/v1"

MEMBERS_FILE = os.environ.get("MEMBERS_FILE", "members.json")
TEAM_ID = int(os.environ.get("TEAM_ID", "28631"))
# Taklif API'da darhol paydo bo'lmasligi mumkin — bir necha marta urinamiz
CONFIRM_ATTEMPTS = int(os.environ.get("CONFIRM_ATTEMPTS", "5"))
CONFIRM_RETRY = int(os.environ.get("CONFIRM_RETRY", "30"))

# Turnirda ishtirok etish bilan bog'liq taklif turlari (sayt matnlariga ko'ra):
#   3 — "... jamoasiga qo'shilib {turnir} turnirida qatnashmoqchimisiz?"
#   4 — "Siz {jamoa} bilan {turnir} turnirida qatnashmoqchimisiz?"
TOURNAMENT_REQUEST_TYPES = (3, 4)

# Noto'g'ri parol aniqlangan telefonlar — 5 urinishdan keyin akkaunt
# bloklanadi, shuning uchun boshqa urinmaymiz (registrar.py dagi kabi).
_blocked_phones = set()

_session = requests.Session()


def _log(msg):
    print(f"[confirmer] {msg}", flush=True)


def load_members():
    """members.json dan a'zolar ro'yxati. Fayl yo'q/buzuq bo'lsa []."""
    try:
        with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        _log(f"{MEMBERS_FILE} topilmadi — a'zolar tasdiqlash o'chirilgan.")
        return []
    except Exception as e:
        _log(f"{MEMBERS_FILE} o'qilmadi: {e!r}")
        return []

    members = []
    for m in data if isinstance(data, list) else []:
        if not isinstance(m, dict):
            continue
        phone, password = m.get("phone"), m.get("password")
        if phone and password:
            members.append(
                {"name": m.get("name") or phone, "phone": phone, "password": password}
            )
        else:
            _log(f"E'tibor bermadim (phone/password yo'q): {m}")
    return members


def login(phone, password):
    """A'zo nomidan sign-in. Bearer token yoki None."""
    if phone in _blocked_phones:
        return None

    try:
        r = _session.post(
            f"{API_ROOT}/user/sign-in",
            json={"phone": phone, "password": password},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        _log(f"{phone}: login so'rovi xatosi: {e!r}")
        return None

    if data.get("code") == 1 and (data.get("data") or {}).get("token"):
        return data["data"]["token"]

    errors = data.get("errors") or {}
    msg = errors.get("password") or data.get("message") or "noma'lum xato"
    # Parol xato bo'lsa qayta urinmaymiz — akkaunt bloklanmasin
    if "count" in errors or "password" in errors:
        _blocked_phones.add(phone)
        _log(f"{phone}: login rad etildi, boshqa urinilmaydi: {msg}")
    else:
        _log(f"{phone}: login xatosi: {msg}")
    return None


def pending_requests(token):
    """Foydalanuvchiga kelgan/yuborilgan takliflar ro'yxati (xato bo'lsa None)."""
    try:
        r = _session.get(
            f"{API_ROOT}/user/transfer-request",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "sort": "-created_at",
                "page": 1,
                "limit": 50,
                "include": "team,club,person,tournament",
            },
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        _log(f"transfer-request so'rovi xatosi: {e!r}")
        return None

    if data.get("code") != 1:
        _log(f"transfer-request xatosi: {data.get('message')}")
        return None
    return data.get("data") or []


def _nested_id(item, key):
    obj = item.get(key)
    if isinstance(obj, dict) and obj.get("id"):
        return obj["id"]
    raw = item.get(f"{key}_id")
    return raw.get("id") if isinstance(raw, dict) else raw


def find_invitation(items, tournament_id, team_id=TEAM_ID):
    """Ro'yxatdan shu turnir uchun kelgan (is_owner=0) taklifni topadi."""
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("is_owner") not in (0, "0"):
            continue  # o'zi yuborgan so'rov — tasdiqlash bizga tegishli emas
        if _nested_id(item, "tournament") != tournament_id:
            continue
        if team_id and _nested_id(item, "team") not in (None, team_id):
            continue
        if item.get("type") not in TOURNAMENT_REQUEST_TYPES:
            _log(f"Diqqat: type={item.get('type')} taklif (kutilgani 3/4), baribir qabul qilamiz")
        return item
    return None


def accept(token, request_id):
    """`Qabul qilish` tugmasi. (ok, xabar) qaytaradi."""
    try:
        r = _session.post(
            f"{API_ROOT}/user/accept-transfer-request/{request_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=None,
            timeout=20,
        )
        data = r.json()
    except Exception as e:
        return False, f"so'rov xatosi: {e!r}"

    if data.get("code") == 1:
        return True, data.get("message") or "qabul qilindi"
    return False, data.get("message") or f"HTTP {r.status_code}"


def confirm_member(member, tournament_id, team_id=TEAM_ID, dry_run=False):
    """Bitta a'zo uchun taklifni topib qabul qiladi.

    ("ok" | "yo'q" | "xato", xabar) qaytaradi. "yo'q" — taklif topilmadi
    (hali kelmagan yoki allaqachon tasdiqlangan).
    """
    token = login(member["phone"], member["password"])
    if not token:
        return "xato", "login qilib bo'lmadi"

    items = pending_requests(token)
    if items is None:
        return "xato", "takliflar ro'yxati olinmadi"

    inv = find_invitation(items, tournament_id, team_id)
    if not inv:
        return "yo'q", "taklif topilmadi (hali kelmagan yoki tasdiqlangan)"

    if dry_run:
        return "ok", f"[DRY-RUN] taklif #{inv.get('id')} topildi, qabul qilinmadi"

    ok, msg = accept(token, inv.get("id"))
    return ("ok" if ok else "xato"), msg


def confirm_all(
    tournament_id,
    team_id=TEAM_ID,
    attempts=CONFIRM_ATTEMPTS,
    retry_delay=CONFIRM_RETRY,
    dry_run=False,
):
    """Barcha a'zolar uchun taklifni tasdiqlaydi.

    Taklif API'da kechikib paydo bo'lishi mumkin — tasdiqlanmaganlar uchun
    `attempts` marta `retry_delay` oralig'ida qayta uriniladi.
    `{name: (status, xabar)}` qaytaradi.
    """
    members = load_members()
    if not members:
        return {}

    results = {}
    pending = list(members)

    for attempt in range(1, attempts + 1):
        still = []
        for m in pending:
            try:
                status, msg = confirm_member(m, tournament_id, team_id, dry_run)
            except Exception as e:
                status, msg = "xato", f"kutilmagan xato: {e!r}"

            results[m["name"]] = (status, msg)
            _log(f"{attempt}-urinish | {m['name']}: {status} — {msg}")

            # Login butunlay bloklangan bo'lsa qayta urinishdan foyda yo'q
            if status != "ok" and m["phone"] not in _blocked_phones:
                still.append(m)

        pending = still
        if not pending or attempt == attempts:
            break
        time.sleep(retry_delay)

    return results


def format_results(results):
    """Admin'ga yuborish uchun qisqa matn."""
    if not results:
        return "A'zolar ro'yxati bo'sh — tasdiqlash o'tkazilmadi"

    icon = {"ok": "✅", "yo'q": "⏳", "xato": "❌"}
    lines = [f"{icon.get(s, '•')} {name}: {msg}" for name, (s, msg) in results.items()]
    ok_count = sum(1 for s, _ in results.values() if s == "ok")
    lines.insert(0, f"A'zolar tasdig'i: {ok_count}/{len(results)}")
    return "\n".join(lines)


def _cli_list():
    for m in load_members():
        token = login(m["phone"], m["password"])
        if not token:
            print(f"{m['name']}: login qilib bo'lmadi")
            continue
        items = pending_requests(token)
        if items is None:
            print(f"{m['name']}: ro'yxat olinmadi")
            continue
        print(f"\n=== {m['name']} ({len(items)} ta yozuv) ===")
        for it in items:
            print(
                f"  #{it.get('id')} type={it.get('type')} is_owner={it.get('is_owner')} "
                f"team={_nested_id(it, 'team')} tournament={_nested_id(it, 'tournament')} "
                f"| {(it.get('tournament') or {}).get('title', '-')}"
            )


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] == "--list":
        _cli_list()
    else:
        dry = "--dry-run" in args
        res = confirm_all(int(args[0]), dry_run=dry)
        print(format_results(res))
