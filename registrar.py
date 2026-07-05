"""Zakovat turnirga API orqali avtomatik ro'yxatdan o'tish.

Brauzer/Playwright ishlatmaydi — to'g'ridan-to'g'ri api.zakovatklubi.uz/v1
bilan ishlaydi: sign-in bilan token oladi, /user/request bilan jamoani yozadi.
"""
import os
import threading

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


def build_payload(tournament_id, member_ids, type_request=1):
    """`/user/request` uchun to'liq payloadni quradi."""
    return {
        "team_id": TEAM_ID,
        "type": type_request,
        "tournament_id": tournament_id,
        "match_id": tournament_id,  # Zakovat Quiz'da match_id == tournament_id
        "mainPersonIds": member_ids,
        "reservePersonIds": [],
    }


def register(tournament_id, type_request=1, dry_run=False):
    """Baurlar jamoasini turnirга yozadi. (ok, message) qaytaradi.

    dry_run=True bo'lsa POST yubormaydi — faqat payloadni qaytaradi (test uchun).
    """
    token = login()
    if not token:
        return False, "Login qilib bo'lmadi (parol yoki tarmoq xatosi)"

    members = get_team_members(token) or FALLBACK_PERSON_IDS
    if not members:
        return False, "Jamoa a'zolari aniqlanmadi"

    payload = build_payload(tournament_id, members, type_request)

    if dry_run:
        return True, f"[DRY-RUN] payload: {payload}"

    try:
        r = requests.post(
            f"{API_ROOT}/user/request",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
        data = r.json()
    except Exception as e:
        return False, f"So'rov xatosi: {e!r}"

    if data.get("code") == 1:
        return True, f"Baurlar ro'yxatdan o'tdi ({len(members)} a'zo)"

    # Token eskirgan bo'lishi mumkin — bir marta qayta login qilib urinib ko'ramiz
    errors = data.get("errors") or {}
    msg = data.get("message") or "noma'lum xato"
    if r.status_code in (401, 403):
        token = login(force=True)
        if token:
            try:
                r2 = requests.post(
                    f"{API_ROOT}/user/request",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                    timeout=20,
                )
                d2 = r2.json()
                if d2.get("code") == 1:
                    return True, f"Baurlar ro'yxatdan o'tdi ({len(members)} a'zo)"
                msg = d2.get("message") or msg
                errors = d2.get("errors") or errors
            except Exception as e:
                return False, f"Qayta so'rov xatosi: {e!r}"

    detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else msg
    return False, detail
