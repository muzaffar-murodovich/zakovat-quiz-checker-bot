"""Zakovat turnirga API orqali avtomatik ro'yxatdan o'tish.

Brauzer/Playwright ishlatmaydi — to'g'ridan-to'g'ri api.zakovatklubi.uz/v1
bilan ishlaydi: sign-in bilan token oladi, /user/request bilan jamoani yozadi.
"""
import os
import threading
import time

import requests

API_ROOT = "https://api.zakovatklubi.uz/v1"

ZK_PHONE = os.environ.get("ZK_PHONE", "")
ZK_PASSWORD = os.environ.get("ZK_PASSWORD", "")
TEAM_ID = int(os.environ.get("TEAM_ID", "28631"))
# Zaxira: agar leader-team a'zolari olinmasa shu ro'yxat ishlatiladi
_fallback = os.environ.get("MAIN_PERSON_IDS", "")
FALLBACK_PERSON_IDS = [int(x) for x in _fallback.replace(",", " ").split()] if _fallback else []

_token = None
_token_lock = threading.Lock()
# Parol noto'g'ri deb aniqlangач boshqa urinmaymiz (akkaunt bloklanmasin)
_auth_blocked = False


def _log(msg):
    print(f"[registrar] {msg}", flush=True)


def login(force=False):
    """Sign-in qilib Bearer token qaytaradi (keshlaydi). Xato bo'lsa None."""
    global _token, _auth_blocked

    if _auth_blocked:
        return None

    with _token_lock:
        if _token and not force:
            return _token

        if not ZK_PHONE or not ZK_PASSWORD:
            _log("ZK_PHONE/ZK_PASSWORD .env da yo'q — login qilinmaydi.")
            return None

        try:
            r = requests.post(
                f"{API_ROOT}/user/sign-in",
                json={"phone": ZK_PHONE, "password": ZK_PASSWORD},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            _log(f"Login so'rovi xatosi: {e!r}")
            return None

        if data.get("code") == 1 and data.get("data", {}).get("token"):
            _token = data["data"]["token"]
            _log("Login muvaffaqiyatli, token olindi.")
            return _token

        # Noto'g'ri parol / limit holati — qayta urinmaymiz
        errors = data.get("errors") or {}
        msg = errors.get("password") or data.get("message") or "noma'lum xato"
        if "count" in errors or "парол" in str(msg).lower() or "password" in str(errors):
            _auth_blocked = True
            _log(f"Login rad etildi, boshqa urinilmaydi: {msg} | {errors.get('count', '')}")
        else:
            _log(f"Login xatosi: {msg}")
        return None


def get_me_id(token):
    """Login qilgan foydalanuvchi (kapitan)ning person ID'si. Xato bo'lsa None."""
    try:
        r = requests.get(
            f"{API_ROOT}/user/get-me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        _log(f"get-me so'rovi xatosi: {e!r}")
        return None

    if data.get("code") != 1:
        _log(f"get-me xatosi: {data.get('message')}")
        return None
    return (data.get("data") or {}).get("id")


def get_accessible_match_id(token, tournament_id):
    """type_request=2 turnirlar uchun haqiqiy match ID. Topilmasa None."""
    try:
        r = requests.get(
            f"{API_ROOT}/tournament/{tournament_id}/accessible-match",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        _log(f"accessible-match so'rovi xatosi: {e!r}")
        return None

    matches = data.get("data") or []
    if data.get("code") == 1 and matches:
        return matches[0].get("id")
    return None


def is_team_registered(token, tournament_id, team_id=TEAM_ID):
    """Jamoa turnir ro'yxatida bormi: True/False, aniqlab bo'lmasa None."""
    page = 1
    while True:
        try:
            r = requests.get(
                f"{API_ROOT}/tournament/{tournament_id}/teams",
                headers={"Authorization": f"Bearer {token}"},
                params={"page": page, "limit": 50},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            _log(f"teams so'rovi xatosi: {e!r}")
            return None

        if data.get("code") != 1:
            _log(f"teams xatosi: {data.get('message')}")
            return None

        for t in data.get("data") or []:
            if isinstance(t, dict) and t.get("id") == team_id:
                return True

        meta = data.get("meta") or {}
        if page >= int(meta.get("pageCount") or 1):
            return False
        page += 1


def get_team_members(token, team_id=TEAM_ID):
    """Kapitan bo'lgan jamoaning a'zo ID'larini qaytaradi. Xato bo'lsa []."""
    try:
        r = requests.get(
            f"{API_ROOT}/user/leader-team",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        _log(f"leader-team so'rovi xatosi: {e!r}")
        return []

    if data.get("code") != 1:
        _log(f"leader-team xatosi: {data.get('message')}")
        return []

    teams = data.get("data")
    teams = teams if isinstance(teams, list) else [teams]
    for t in teams:
        if not isinstance(t, dict):
            continue
        if t.get("id") == team_id or len(teams) == 1:
            persons = t.get("persons") or t.get("players") or t.get("mainPersons") or []
            ids = [p["id"] for p in persons if isinstance(p, dict) and p.get("id")]
            if ids:
                return ids
    return []


def build_payload(tournament_id, member_ids, type_request=1, match_id=None):
    """`/user/request` uchun to'liq payloadni quradi.

    Sayt frontend'i bilan bir xil: match_id FAQAT type_request=2 da
    yuboriladi (va u alohida match obyektidan olinadi — turnir ID emas!).
    type_request=1 (Zakovat Quiz) da match_id umuman yuborilmaydi —
    noto'g'ri match_id bilan API "success" qaytarsa ham jamoa ro'yxatga
    tushmaydi (2026-07-13 dagi soxta registratsiya sababi).
    """
    payload = {
        "team_id": TEAM_ID,
        "type": type_request,
        "tournament_id": tournament_id,
        "mainPersonIds": member_ids,
        "reservePersonIds": [],
    }
    if type_request == 2 and match_id:
        payload["match_id"] = match_id
    return payload


def register(tournament_id, type_request=1, dry_run=False):
    """Baurlar jamoasini turnirга yozadi. (ok, message) qaytaradi.

    dry_run=True bo'lsa POST yubormaydi — faqat payloadni qaytaradi (test uchun).
    """
    # Registratsiya haftada bir marta bo'ladi; token opaque va serverda muddati
    # bo'lishi mumkin, shuning uchun keshga tayanmay har safar yangi login
    # qilamiz — get_team_members ham, POST ham yangi token bilan ishlaydi.
    token = login(force=True)
    if not token:
        return False, "Login qilib bo'lmadi (parol yoki tarmoq xatosi)"

    members = get_team_members(token) or FALLBACK_PERSON_IDS
    if not members:
        return False, "Jamoa a'zolari aniqlanmadi"

    # Sayt kapitanning o'z ID'sini mainPersonIds'dan chiqarib yuboradi —
    # backend uni o'zi qo'shadi. Biz ham xuddi shunday qilamiz.
    me = get_me_id(token)
    if me:
        members = [m for m in members if m != me]
    if not members:
        return False, "Kapitandan boshqa a'zo topilmadi"

    match_id = None
    if type_request == 2:
        match_id = get_accessible_match_id(token, tournament_id)
        if not match_id:
            return False, "type_request=2, lekin accessible-match topilmadi"

    payload = build_payload(tournament_id, members, type_request, match_id)

    if dry_run:
        return True, f"[DRY-RUN] payload: {payload}"

    _log(f"POST /user/request payload: {payload}")

    def _post():
        r = requests.post(
            f"{API_ROOT}/user/request",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
        _log(f"/user/request javobi [{r.status_code}]: {r.text[:500]}")
        return r, r.json()

    try:
        r, data = _post()
    except Exception as e:
        return False, f"So'rov xatosi: {e!r}"

    # Token eskirgan bo'lishi mumkin — bir marta qayta login qilib urinib ko'ramiz
    if data.get("code") != 1 and r.status_code in (401, 403):
        token = login(force=True)
        if token:
            try:
                r, data = _post()
            except Exception as e:
                return False, f"Qayta so'rov xatosi: {e!r}"

    if data.get("code") != 1:
        errors = data.get("errors") or {}
        msg = data.get("message") or "noma'lum xato"
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else msg
        return False, detail

    # API "success" desa ham ishonmaymiz (2026-07-13 da success kelgan, lekin
    # jamoa ro'yxatga tushmagan) — turnir ro'yxatida haqiqatan bormi tekshiramiz.
    time.sleep(3)
    seen = is_team_registered(token, tournament_id)
    if seen is True:
        return True, f"Baurlar ro'yxatdan o'tdi, ro'yxatda tasdiqlandi ({len(members)} a'zo + kapitan)"
    if seen is False:
        return False, (
            "API 'success' dedi, lekin jamoa turnir ro'yxatida KO'RINMAYAPTI — "
            "tezda qo'lda ro'yxatdan o'ting!"
        )
    return True, (
        f"Baurlar ro'yxatdan o'tdi ({len(members)} a'zo + kapitan), "
        "lekin ro'yxatni tekshirib bo'lmadi — saytdan qo'lda tasdiqlang"
    )
