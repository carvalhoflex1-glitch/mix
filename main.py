import os
import io
import base64
import time
import json
import random
import hashlib
import threading
import secrets
import shutil
import re
import contextlib
from collections import defaultdict, deque
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from html import escape

import requests
import qrcode
from PIL import Image
from flask import Flask, request, redirect, session, abort
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None
    Jsonb = None

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
PORT = int(os.getenv("PORT", "8080"))
OFFSET_FILE = os.getenv("OFFSET_FILE", "telegram_offset.json")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
OFFSET = None
LOGIN_ATTEMPTS = defaultdict(deque)
LOGIN_WINDOW_SECONDS = 900
LOGIN_MAX_ATTEMPTS = 5

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1", PERMANENT_SESSION_LIFETIME=1800)

ASSETS = ["TL", "USDT", "LTC", "TRX"]
CRYPTO_ASSETS = ["USDT", "LTC", "TRX"]
data_lock = threading.RLock()
user_state = {}

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')

FILES = {
    "users": "users.json",
    "requests": "requests.json",
    "transactions": "transactions.json",
    "settings": "settings.json",
    "messages": "messages.json",
    "admin_logs": "admin_logs.json",
    "security_events": "security_events.json",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().isoformat()


def _db_key(path):
    return os.path.basename(str(path))


def _require_database():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL tanımlı değil. Railway PostgreSQL bağlantısını ekleyiniz.")
    if psycopg is None:
        raise RuntimeError("PostgreSQL sürücüsü bulunamadı. Railway'e psycopg[binary] paketi eklenmelidir.")


def _db_connect():
    _require_database()
    return psycopg.connect(DATABASE_URL, autocommit=False)


def init_database():
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    state_key TEXT PRIMARY KEY,
                    state_data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def _read_legacy_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Eski veri dosyası okunamadı: {path}: {exc}") from exc


def load_json(path, default):
    key = _db_key(path)
    with data_lock, _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT state_data FROM app_state WHERE state_key = %s", (key,))
            row = cur.fetchone()
            if row is not None:
                return row[0]

            initial = _read_legacy_json(path, default)
            cur.execute(
                "INSERT INTO app_state (state_key, state_data) VALUES (%s, %s) ON CONFLICT (state_key) DO NOTHING",
                (key, Jsonb(initial)),
            )
        conn.commit()
        return initial


def save_json(path, data):
    key = _db_key(path)
    with data_lock, _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_state (state_key, state_data, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (state_key)
                DO UPDATE SET state_data = EXCLUDED.state_data, updated_at = NOW()
                """,
                (key, Jsonb(data)),
            )
        conn.commit()


def validate_runtime_config():
    required = {"BOT_TOKEN": TOKEN, "ADMIN_CHAT_ID": ADMIN_CHAT_ID, "PANEL_USERNAME": PANEL_USERNAME, "PANEL_PASSWORD": PANEL_PASSWORD, "FLASK_SECRET_KEY": app.secret_key}
    missing = [k for k, v in required.items() if not str(v).strip()]
    if missing: raise RuntimeError("Eksik zorunlu ortam değişkenleri: " + ", ".join(missing))
    if len(app.secret_key) < 32: raise RuntimeError("FLASK_SECRET_KEY en az 32 karakter olmalıdır")
    if len(PANEL_PASSWORD) < 12: raise RuntimeError("PANEL_PASSWORD en az 12 karakter olmalıdır")


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32); session["csrf_token"] = token
    return token


@app.before_request
def enforce_csrf():
    if request.method == "POST":
        supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not supplied or not secrets.compare_digest(supplied, session.get("csrf_token", "")):
            abort(403)


def D(value, fallback="0"):

    try:
        return Decimal(str(value if value not in (None, "") else fallback).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return Decimal(fallback)


def fmt(value, asset=""):
    value = D(value)
    precision = Decimal("0.000001") if asset == "LTC" else Decimal("0.01")
    out = value.quantize(precision, rounding=ROUND_DOWN)
    return f"{out} {asset}".strip()


def coin_fmt(value, asset):
    asset = str(asset or "")
    value = D(value)
    precision = Decimal("0.000001") if asset == "LTC" else Decimal("0.01")
    out = value.quantize(precision, rounding=ROUND_DOWN)
    if asset == "TL":
        return f"{{{{TL}}}}{out}"
    return f"{out} {{{{{asset}}}}}"


def h(value):
    return escape(str(value if value is not None else ""), quote=True)


DEFAULT_MESSAGES = {
    "welcome": "ZaqelV2'ye hoş geldiniz.\n\nCüzdanınızı güvenli biçimde yönetmek için aşağıdaki menüden devam edebilirsiniz.",
    "wallet_title": "Cüzdan Bakiyeleriniz",
    "deposit_menu": "Yüklemek istediğiniz bakiye türünü seçiniz.",
    "withdraw_menu": "Çekmek istediğiniz bakiye türünü seçiniz.",
    "convert_menu": "Dönüştürmek istediğiniz bakiyeyi seçiniz.",
    "amount_question": "İşlem tutarını giriniz.",
    "pin_question": "İşlem PIN'inizi giriniz.",
    "pin_set_question": "Güvenliğiniz için 4-6 haneli bir işlem PIN'i belirleyiniz.",
    "pin_wrong": "PIN doğrulanamadı. Lütfen tekrar deneyiniz.",
    "pin_saved": "İşlem PIN'iniz güvenle kaydedildi.",
    "pin_changed": "İşlem PIN'iniz değiştirildi. Güvenliğiniz için diğer tüm oturumlar kapatıldı.",
    "insufficient_balance": "Bu işlem için yeterli bakiyeniz bulunmuyor.",
    "no_balance": "Kullanılabilir bakiyeniz bulunmuyor.",
    "request_created": "Talebiniz oluşturuldu ve incelemeye alındı.",
    "request_cancelled": "İşlem iptal edildi.",
    "support": "Destek talebi için yöneticiyle iletişime geçebilirsiniz.",
    "history_empty": "Henüz işlem geçmişiniz bulunmuyor.",
    "iban_warning": "Ödeme açıklamasına göndericiye ait TC Kimlik Numarasının yazılması zorunludur. Bu bilgi bulunmayan ödemeler işleme alınmayabilir.",
    "maintenance": "Sistem şu anda kısa süreli bakımdadır. Lütfen daha sonra tekrar deneyiniz.",
    "frozen": "Hesabınız geçici olarak kısıtlanmıştır. Destek ile iletişime geçiniz.",
    "withdraw_locked": "Çekim işlemleriniz geçici olarak kilitlidir.",
    "deposit_crypto_intro": "Yalnızca belirtilen ağ üzerinden gönderim yapınız. Farklı ağdan yapılan transferler kaybolabilir.",
    "deposit_received": "Yatırım bildiriminiz alındı. Kontrol ve onay sonrasında bakiyenize yansıtılacaktır.",
}

DEFAULT_SETTINGS = {
    "bank_name": os.getenv("DEFAULT_BANK_NAME", ""),
    "iban": os.getenv("DEFAULT_IBAN", ""),
    "iban_owner": os.getenv("DEFAULT_IBAN_OWNER", ""),
    "wallet_USDT": os.getenv("DEFAULT_WALLET_USDT", ""),
    "wallet_TRX": os.getenv("DEFAULT_WALLET_TRX", ""),
    "wallet_LTC": os.getenv("DEFAULT_WALLET_LTC", ""),
    "rate_USDT_TL": "46.40",
    "rate_LTC_TL": "2065.00",
    "rate_TRX_TL": "15.50",
    "fee_deposit_TL_percent": "0",
    "fee_deposit_USDT_percent": "0",
    "fee_deposit_LTC_percent": "0",
    "fee_deposit_TRX_percent": "0",
    "fee_withdraw_TL_percent": "1",
    "fee_withdraw_USDT_percent": "1",
    "fee_withdraw_LTC_percent": "1",
    "fee_withdraw_TRX_percent": "1",
    "fee_convert_percent": "2",
    "min_deposit_TL": "100",
    "min_deposit_USDT": "5",
    "min_deposit_LTC": "0.01",
    "min_deposit_TRX": "50",
    "min_withdraw_TL": "100",
    "min_withdraw_USDT": "5",
    "min_withdraw_LTC": "0.01",
    "min_withdraw_TRX": "50",
    "min_convert_TL": "100",
    "min_convert_USDT": "5",
    "min_convert_LTC": "0.01",
    "min_convert_TRX": "50",
    "daily_withdraw_limit_TL": "50000",
    "daily_withdraw_limit_USDT": "1000",
    "daily_withdraw_limit_LTC": "10",
    "daily_withdraw_limit_TRX": "50000",
    "maintenance_mode": "off",
    "maintenance_message": "",
    "announcement_active": "off",
    "announcement_text": "",
    "network_USDT": "TRC20",
    "network_TRX": "TRON",
    "network_LTC": "Litecoin",
    "icon_wallet": "5895439304976506343",
    "icon_deposit": "5895549153060069171",
    "icon_withdraw": "5895506164732403256",
    "icon_convert": "5895671971944866108",
    "icon_history": "5895533287450877887",
    "icon_support": "5895698390288703053",
    "icon_fees": "5895334439055009075",
    "icon_info": "5895656948149263789",
    "icon_swap": "5893252234614939371",
    "icon_pending": "5895304795190730655",
    "icon_processing": "5895589615946964496",
    "icon_security": "5895439304976506343",
    "icon_completed": "5893391786692323248",
    "icon_rejected": "5895319286410387652",
    "icon_USDT": "5895571353746021767",
    "icon_LTC": "5895441495409828662",
    "icon_TRX": "5895440778150288520",
    "icon_TL": "",
}

init_database()

users = load_json(FILES["users"], {})
requests_db = load_json(FILES["requests"], {})
transactions = load_json(FILES["transactions"], {})
settings = load_json(FILES["settings"], {})
messages = load_json(FILES["messages"], {})
admin_logs = load_json(FILES["admin_logs"], [])
security_events = load_json(FILES["security_events"], [])
for k, v in DEFAULT_SETTINGS.items():
    settings.setdefault(k, v)
for k, v in DEFAULT_MESSAGES.items():
    messages.setdefault(k, v)
save_json(FILES["settings"], settings)
save_json(FILES["messages"], messages)


def api(method, data=None, files=None):
    if not TOKEN:
        return {}
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/{method}"
        if files:
            return requests.post(url, data=data or {}, files=files, timeout=30).json()
        return requests.post(url, json=data or {}, timeout=30).json()
    except Exception as exc:
        print("TELEGRAM API ERROR:", exc)
        return {}


def _utf16_len(value):
    return len(str(value).encode("utf-16-le")) // 2


def _render_asset_icons(value):
    source = str(value)
    output = []
    entities = []
    cursor = 0
    offset = 0
    pattern = re.compile(r"\{\{(TL|USDT|LTC|TRX)\}\}")

    for match in pattern.finditer(source):
        before = source[cursor:match.start()]
        output.append(before)
        offset += _utf16_len(before)

        asset = match.group(1)
        emoji_id = str(settings.get(f"icon_{asset}", "")).strip()
        if asset == "TL":
            replacement = "₺"
        elif emoji_id:
            replacement = "🪙"
            entities.append({
                "type": "custom_emoji",
                "offset": offset,
                "length": _utf16_len(replacement),
                "custom_emoji_id": emoji_id,
            })
        else:
            replacement = asset

        output.append(replacement)
        offset += _utf16_len(replacement)
        cursor = match.end()

    tail = source[cursor:]
    output.append(tail)
    return "".join(output), entities


def _plain_asset_icons(value):
    return re.sub(
        r"\{\{(TL|USDT|LTC|TRX)\}\}",
        lambda m: "₺" if m.group(1) == "TL" else m.group(1),
        str(value),
    )


def send(chat_id, text, keyboard=None):
    rendered, entities = _render_asset_icons(text)
    payload = {"chat_id": chat_id, "text": rendered}
    if entities:
        payload["entities"] = entities
    if keyboard:
        payload["reply_markup"] = keyboard

    result = api("sendMessage", payload)
    if entities and not result.get("ok", False):
        fallback = {"chat_id": chat_id, "text": _plain_asset_icons(text)}
        if keyboard:
            fallback["reply_markup"] = keyboard
        return api("sendMessage", fallback)
    return result


def send_qr(chat_id, content, caption=""):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#07111f", back_color="white").convert("RGBA")

    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo.thumbnail((img.width // 4, img.height // 4), Image.Resampling.LANCZOS)
        img.alpha_composite(
            logo,
            ((img.width - logo.width) // 2, (img.height - logo.height) // 2),
        )
    except Exception as exc:
        print("QR LOGO ERROR:", exc)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)

    rendered_caption, caption_entities = _render_asset_icons(caption)
    data = {"chat_id": str(chat_id), "caption": rendered_caption}
    if caption_entities:
        data["caption_entities"] = json.dumps(caption_entities, ensure_ascii=False)
    return api("sendPhoto", data, {"photo": ("zaqel-qr.png", buf, "image/png")})

def answer(cb_id, text=""):
    payload = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text
    return api("answerCallbackQuery", payload)


def inline_button(text, data, icon_key=None):
    b = {"text": text, "callback_data": data}
    emoji_id = str(settings.get(icon_key or "", "")).strip()
    if emoji_id:
        b["icon_custom_emoji_id"] = emoji_id
    return b


def reply_keyboard():
    return {
        "keyboard": [
            [{"text": "Cüzdanım"}, {"text": "Bakiye Yükle"}],
            [{"text": "Para Çek"}, {"text": "Dönüştür"}],
            [{"text": "İşlem Geçmişi"}, {"text": "Güvenlik"}],
            [{"text": "Favori Adresler"}, {"text": "Destek"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def asset_keyboard(prefix, assets, exclude=None):
    rows = []
    for asset in assets:
        if asset != exclude:
            rows.append([inline_button(asset, f"{prefix}:{asset}", f"icon_{asset}")])
    rows.append([inline_button("İptal", "cancel")])
    return {"inline_keyboard": rows}


def confirm_keyboard(ok_data, cancel_data="cancel"):
    return {"inline_keyboard": [[inline_button("Onayla", ok_data), inline_button("İptal", cancel_data)]]}


def copy_button(text, value):
    return {"text": text, "copy_text": {"text": str(value)}}


def order_summary(title, rows, note=""):
    lines = [title, "━━━━━━━━━━━━"]
    for label, value in rows:
        lines.append(f"{label}\n{value}")
    if note:
        lines.extend(["━━━━━━━━━━━━", note])
    return "\n\n".join(lines)


def hash_pin(pin):
    return generate_password_hash(str(pin), method="scrypt")


def verify_pin(stored, pin):
    if not stored: return False
    try:
        if stored.startswith(("scrypt:", "pbkdf2:")):
            return check_password_hash(stored, str(pin))
    except ValueError:
        return False
    legacy = hashlib.sha256((os.getenv("PIN_SALT", "zaqelv2-pin-salt") + str(pin)).encode()).hexdigest()
    return secrets.compare_digest(stored, legacy)


def get_user(chat_id, username=""):
    uid = str(chat_id)
    if uid not in users:
        users[uid] = {
            "chat_id": uid,
            "username": username or "unknown",
            "created_at": now(),
            "last_seen": now(),
            "status": "active",
            "withdraw_locked": False,
            "pin_hash": "",
            "pin_failed_attempts": 0,
            "tier": "Basic",
            "balances": {a: "0" for a in ASSETS},
            "pending_balances": {a: "0" for a in ASSETS},
            "session_version": 1,
            "last_pin_change": "",
            "last_security_event": "",
            "favorites": [],
            "notifications": {"transactions": True, "security": True, "announcements": True},
            "sessions": {"telegram": {"created_at": now(), "last_seen": now(), "active": True}},
            "note": "",
        }
    u = users[uid]
    u.setdefault("withdraw_locked", False)
    u.setdefault("pin_failed_attempts", 0)
    u.setdefault("tier", "Basic")
    u.setdefault("favorites", [])
    u.setdefault("notifications", {"transactions": True, "security": True, "announcements": True})
    u.setdefault("sessions", {"telegram": {"created_at": now(), "last_seen": now(), "active": True}})
    u.setdefault("balances", {a: "0" for a in ASSETS})
    u.setdefault("pending_balances", {a: "0" for a in ASSETS})
    u.setdefault("session_version", 1)
    u.setdefault("last_pin_change", "")
    u.setdefault("last_security_event", "")
    for a in ASSETS:
        u["balances"].setdefault(a, "0")
        u["pending_balances"].setdefault(a, "0")
    if username:
        u["username"] = username
    u["last_seen"] = now()
    u["sessions"]["telegram"]["last_seen"] = now()
    save_json(FILES["users"], users)
    return u


def balance(uid, asset):
    """Kullanılabilir bakiye."""
    return D(users.get(str(uid), {}).get("balances", {}).get(asset, "0"))


def pending_balance(uid, asset):
    """İşlemde/blokede bekleyen bakiye."""
    return D(users.get(str(uid), {}).get("pending_balances", {}).get(asset, "0"))


def add_transaction(uid, asset, amount, kind, ref_id="", note="", bucket="available"):
    """Değiştirilemeyen bakiye hareketi kaydı (ledger)."""
    tid = str(random.randint(100000, 999999))
    while tid in transactions:
        tid = str(random.randint(100000, 999999))
    previous = list(transactions.values())[-1].get("entry_hash", "") if transactions else ""
    entry = {"id": tid, "user_id": str(uid), "asset": asset, "amount": str(D(amount)), "kind": kind, "bucket": bucket, "ref_id": str(ref_id), "note": note, "available_after": str(balance(uid, asset)), "pending_after": str(pending_balance(uid, asset)), "created_at": now(), "previous_hash": previous}
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    entry["entry_hash"] = hashlib.sha256((previous + canonical).encode()).hexdigest()
    transactions[tid] = entry
    save_json(FILES["transactions"], transactions)
    return tid


def change_balance(uid, asset, amount, kind, ref_id="", note=""):
    uid = str(uid)
    with data_lock:
        new_value = balance(uid, asset) + D(amount)
        if new_value < 0:
            raise ValueError("Yetersiz bakiye")
        users[uid]["balances"][asset] = str(new_value)
        save_json(FILES["users"], users)
        return add_transaction(uid, asset, amount, kind, ref_id, note, "available")


def change_pending(uid, asset, amount, kind, ref_id="", note=""):
    uid = str(uid)
    with data_lock:
        new_value = pending_balance(uid, asset) + D(amount)
        if new_value < 0:
            raise ValueError("Bekleyen bakiye yetersiz")
        users[uid]["pending_balances"][asset] = str(new_value)
        save_json(FILES["users"], users)
        return add_transaction(uid, asset, amount, kind, ref_id, note, "pending")


def add_security_event(uid, event, detail=""):
    security_events.append({"created_at": now(), "user_id": str(uid), "event": event, "detail": detail})
    users[str(uid)]["last_security_event"] = now()
    save_json(FILES["security_events"], security_events)
    save_json(FILES["users"], users)


def add_admin_log(action, details, uid=""):
    admin_logs.append({"created_at": now(), "action": action, "details": details, "user_id": str(uid)})
    save_json(FILES["admin_logs"], admin_logs)


def new_request(uid, kind, data):
    rid = str(random.randint(10000, 99999))
    while rid in requests_db:
        rid = str(random.randint(10000, 99999))
    requests_db[rid] = {
        "id": rid, "user_id": str(uid), "type": kind, "status": "pending",
        "created_at": now(), "updated_at": now(), **data,
    }
    save_json(FILES["requests"], requests_db)
    return rid


def atomic_withdraw(uid, state):
    uid = str(uid)
    with data_lock:
        amount = D(state["amount"])
        if balance(uid, state["asset"]) < amount: raise ValueError("Yetersiz bakiye")
        rid = new_request(uid, "withdraw", {"asset": state["asset"], "amount": state["amount"], "fee": state["fee"], "net_amount": state["net_amount"], "bank_name": state.get("bank_name", ""), "iban": state.get("iban", ""), "name": state.get("name", ""), "address": state.get("address", ""), "second_confirmation": True, "idempotency_key": state.get("confirm_token", "")})
        old_available, old_pending = balance(uid, state["asset"]), pending_balance(uid, state["asset"])
        try:
            change_balance(uid, state["asset"], -amount, "withdraw_hold_available", rid)
            change_pending(uid, state["asset"], amount, "withdraw_hold_pending", rid)
            requests_db[rid]["funds_reserved"] = True; save_json(FILES["requests"], requests_db)
            return rid
        except Exception:
            users[uid]["balances"][state["asset"]] = str(old_available); users[uid]["pending_balances"][state["asset"]] = str(old_pending)
            requests_db.pop(rid, None); save_json(FILES["users"], users); save_json(FILES["requests"], requests_db)
            raise


def atomic_convert(uid, state):
    uid = str(uid)
    with data_lock:
        source, target = state["from_asset"], state["to_asset"]
        amount, net = D(state["amount"]), D(state["net_amount"])
        if balance(uid, source) < amount: raise ValueError("Yetersiz bakiye")
        rid = new_request(uid, "convert", {"from_asset": source, "to_asset": target, "from_amount": state["amount"], "tl_value": state["tl_value"], "fee": state["fee"], "net_to_amount": state["net_amount"], "second_confirmation": True, "idempotency_key": state.get("confirm_token", "")})
        old_source, old_target = balance(uid, source), balance(uid, target)
        try:
            change_balance(uid, source, -amount, "convert_out", rid); change_balance(uid, target, net, "convert_in", rid)
            requests_db[rid].update({"status": "completed", "completed_at": now(), "updated_at": now()}); save_json(FILES["requests"], requests_db)
            return rid
        except Exception:
            users[uid]["balances"][source] = str(old_source); users[uid]["balances"][target] = str(old_target)
            requests_db.pop(rid, None); save_json(FILES["users"], users); save_json(FILES["requests"], requests_db)
            raise


def consume_confirmation(state):
    if not state.get("confirm_token") or state.get("confirmation_consumed"): return False
    state["confirmation_consumed"] = True
    return True


def rate(asset):
    return Decimal("1") if asset == "TL" else D(settings.get(f"rate_{asset}_TL", "0"))


def fee_percent(kind, asset=None, uid=None):
    if uid is not None:
        override = str(users.get(str(uid), {}).get("custom_fee_percent", "")).strip()
        if override:
            return D(override)
    return D(settings.get("fee_convert_percent", "0")) if kind == "convert" else D(settings.get(f"fee_{kind}_{asset}_percent", "0"))


def fee_amount(amount, p):
    return D(amount) * D(p) / Decimal("100")


def min_amount(kind, asset):
    return D(settings.get(f"min_{kind}_{asset}", "0"))


def daily_limit(asset):
    return D(settings.get(f"daily_withdraw_limit_{asset}", "0"))


def withdrawn_today(uid, asset):
    total = Decimal("0")
    for r in requests_db.values():
        if r.get("user_id") == str(uid) and r.get("type") == "withdraw" and r.get("asset") == asset and r.get("status") in ("pending", "processing", "completed") and str(r.get("created_at", "")).startswith(today()):
            total += D(r.get("amount"))
    return total


def wallet_text(uid):
    u = users[str(uid)]
    lines = [f"Cüzdan · {u.get('tier', 'Basic')}", ""]
    for asset in ASSETS:
        lines.append(coin_fmt(u["balances"].get(asset, "0"), asset))
        pending = D(u.get("pending_balances", {}).get(asset, "0"))
        if pending > 0:
            lines.append(f"Bekleyen · {coin_fmt(pending, asset)}")
    return "\n".join(lines)


def active_balances(uid):
    return [a for a in ASSETS if balance(uid, a) > 0]


def request_summary(rid):
    r = requests_db.get(str(rid), {})
    if not r:
        return "İşlem bulunamadı."

    kind = {
        "deposit": "Yükleme",
        "withdraw": "Çekim",
        "convert": "Dönüşüm",
    }.get(r.get("type"), "İşlem")

    lines = [f"{kind} · #{rid}", status_label(r.get("status"))]

    if r.get("type") == "deposit":
        asset = r.get("asset")
        lines += [
            f"Tutar · {coin_fmt(r.get('amount'), asset)}",
            f"Net · {coin_fmt(r.get('net_amount'), asset)}",
        ]
        if asset == "TL":
            lines += [
                f"Gönderen · {r.get('sender_name', '-')}",
                f"Referans · {r.get('tx_note', '-')}",
            ]
        else:
            lines.append(f"Ağ · {r.get('network', '-')}")

    elif r.get("type") == "withdraw":
        asset = r.get("asset")
        lines += [
            f"Tutar · {coin_fmt(r.get('amount'), asset)}",
            f"Net · {coin_fmt(r.get('net_amount'), asset)}",
            f"Komisyon · {coin_fmt(r.get('fee'), asset)}",
        ]
        if asset == "TL":
            lines += [
                f"Banka · {r.get('bank_name', '-')}",
                f"IBAN · {r.get('iban', '-')}",
                f"Alıcı · {r.get('name', '-')}",
            ]
        else:
            lines.append(f"Adres · {r.get('address', '-')}")

    elif r.get("type") == "convert":
        lines += [
            f"Verilen · {coin_fmt(r.get('from_amount'), r.get('from_asset'))}",
            f"Alınan · {coin_fmt(r.get('net_to_amount'), r.get('to_asset'))}",
            f"Komisyon · {coin_fmt(r.get('fee'), r.get('to_asset'))}",
        ]

    lines.append(r.get("created_at", ""))
    return "\n".join(lines)


def receipt_text(rid):
    if not requests_db.get(str(rid)):
        return "İşlem bulunamadı."
    return "İşlem tamamlandı\n\n" + request_summary(rid)


def user_allowed(chat_id):
    u = get_user(chat_id)
    if u.get("status") == "frozen":
        send(chat_id, messages["frozen"], reply_keyboard())
        return False
    if settings.get("maintenance_mode") == "on" and str(chat_id) != str(ADMIN_CHAT_ID):
        send(chat_id, settings.get("maintenance_message") or messages["maintenance"], reply_keyboard())
        return False
    return True


def start_user(chat_id, username=""):
    u = get_user(chat_id, username)
    text = messages["welcome"]
    if settings.get("announcement_active") == "on" and u.get("notifications", {}).get("announcements", True) and settings.get("announcement_text"):
        text += "\n\n📢 " + settings["announcement_text"]
    send(chat_id, text, reply_keyboard())


def show_history(chat_id):
    items = [r for r in requests_db.values() if r.get("user_id") == str(chat_id)]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    items = items[:10]
    if not items:
        send(chat_id, messages["history_empty"], reply_keyboard())
        return
    rows = [[inline_button(f"#{r['id']} · {r.get('status')}", f"detail:{r['id']}")] for r in items]
    send(chat_id, "Son 10 İşlem", {"inline_keyboard": rows})


def show_security(chat_id):
    u = get_user(chat_id)
    pin = "Aktif" if u.get("pin_hash") else "Ayarlanmamış"
    lock = "Kilitli" if u.get("withdraw_locked") else "Açık"
    text = f"Güvenlik Merkezi\n\nİşlem PIN'i: {pin}\nÇekim durumu: {lock}\nAktif oturum: Telegram\nSon etkinlik: {u.get('last_seen','')}"
    kb = {"inline_keyboard": [
        [inline_button("PIN Değiştir", "security:set_pin")],
        [inline_button("Bildirim Tercihleri", "security:notifications")],
        [inline_button("Tüm Oturumları Kapat", "security:logout_sessions")],
    ]}
    send(chat_id, text, kb)


def begin_deposit(chat_id):
    user_state[str(chat_id)] = {"flow": "deposit", "step": "asset"}
    send(chat_id, messages["deposit_menu"], asset_keyboard("deposit_asset", ASSETS))


def begin_withdraw(chat_id):
    u = get_user(chat_id)
    if u.get("withdraw_locked"):
        send(chat_id, messages["withdraw_locked"], reply_keyboard())
        return
    assets = active_balances(chat_id)
    if not assets:
        send(chat_id, messages["no_balance"], reply_keyboard())
        return
    user_state[str(chat_id)] = {"flow": "withdraw", "step": "asset"}
    send(chat_id, messages["withdraw_menu"], asset_keyboard("withdraw_asset", assets))


def begin_convert(chat_id):
    assets = active_balances(chat_id)
    if not assets:
        send(chat_id, messages["no_balance"], reply_keyboard())
        return
    user_state[str(chat_id)] = {"flow": "convert", "step": "from_asset"}
    send(chat_id, messages["convert_menu"], asset_keyboard("convert_from", assets))


def require_pin(uid, state, next_step="pin"):
    if not users[uid].get("pin_hash"):
        state["after_pin_setup"] = next_step
        state["step"] = "set_pin"
        send(uid, messages["pin_set_question"], reply_keyboard())
    else:
        state["step"] = next_step
        send(uid, messages["pin_question"], reply_keyboard())


def finalize_withdraw(uid, state):
    rid = atomic_withdraw(uid, state)
    user_state.pop(uid, None)
    send(uid, "Talep oluşturuldu\n\n" + request_summary(rid), reply_keyboard())
    if ADMIN_CHAT_ID:
        send(ADMIN_CHAT_ID, "Yeni çekim talebi\n\n" + request_summary(rid))


def handle_text(chat_id, username, text):
    uid = str(chat_id)
    get_user(chat_id, username)
    if text == "/start" or text == "Ana Menü":
        start_user(chat_id, username); return
    if not user_allowed(chat_id):
        return
    if text == "Cüzdanım":
        send(chat_id, wallet_text(uid), reply_keyboard()); return
    if text == "Bakiye Yükle": begin_deposit(chat_id); return
    if text == "Para Çek": begin_withdraw(chat_id); return
    if text == "Dönüştür": begin_convert(chat_id); return
    if text == "İşlem Geçmişi": show_history(chat_id); return
    if text == "Güvenlik": show_security(chat_id); return
    if text == "Favori Adresler":
        favs = users[uid].get("favorites", [])
        if not favs:
            send(chat_id, "Kayıtlı favori adresiniz bulunmuyor.", {"inline_keyboard": [[inline_button("Yeni Adres Ekle", "favorite:add")]]})
        else:
            lines = ["Favori Cüzdan Adresleri"] + [f"{i+1}. {f['label']} · {f['asset']}\n{f['address']}" for i, f in enumerate(favs)]
            send(chat_id, "\n\n".join(lines), {"inline_keyboard": [[inline_button("Yeni Adres Ekle", "favorite:add")]]})
        return
    if text == "Destek": send(chat_id, messages["support"], reply_keyboard()); return

    state = user_state.get(uid)
    if not state:
        send(chat_id, "Menüden bir işlem seçiniz.", reply_keyboard()); return
    flow, step = state.get("flow"), state.get("step")

    if step == "set_pin":
        pin = text.strip()
        if not pin.isdigit() or not 4 <= len(pin) <= 6:
            send(chat_id, "PIN 4-6 haneli rakamlardan oluşmalıdır.")
            return
        state["new_pin"] = pin
        state["step"] = "confirm_new_pin"
        send(chat_id, "Yeni PIN'inizi tekrar giriniz.")
        return

    if step == "confirm_new_pin":
        if text.strip() != state.get("new_pin"):
            user_state.pop(uid, None)
            send(chat_id, "PIN'ler eşleşmedi. İşlemi yeniden başlatınız.")
            return
        changing = state.get("changing_pin", False)
        next_step = state.get("after_pin_setup")
        users[uid]["pin_hash"] = hash_pin(text.strip())
        users[uid]["session_version"] = int(users[uid].get("session_version", 1)) + 1
        users[uid]["last_pin_change"] = now()
        users[uid]["sessions"] = {"telegram": {"created_at": now(), "last_seen": now(), "active": True, "version": users[uid]["session_version"]}}
        users[uid]["pin_failed_attempts"] = 0
        save_json(FILES["users"], users)
        add_security_event(uid, "pin_changed" if changing else "pin_created", "Tüm eski oturumlar geçersiz kılındı")
        if next_step:
            state.clear()
            state.update({"flow": flow, "step": next_step})
            send(chat_id, messages["pin_saved"] + "\n\n" + messages["pin_question"])
        else:
            user_state.pop(uid, None)
            send(chat_id, messages["pin_changed"] if changing else messages["pin_saved"], reply_keyboard())
        return

    if step == "old_pin":
        if not verify_pin(users[uid].get("pin_hash"), text.strip()):
            users[uid]["pin_failed_attempts"] = int(users[uid].get("pin_failed_attempts", 0)) + 1
            save_json(FILES["users"], users)
            if users[uid]["pin_failed_attempts"] >= 3:
                users[uid]["withdraw_locked"] = True
                save_json(FILES["users"], users)
                add_security_event(uid, "pin_lock", "3 hatalı PIN denemesi")
                send(chat_id, "Üç hatalı PIN denemesi nedeniyle çekimleriniz geçici olarak kilitlendi.")
            else:
                send(chat_id, messages["pin_wrong"])
            return
        users[uid]["pin_failed_attempts"] = 0
        save_json(FILES["users"], users)
        state["step"] = "set_pin"
        state["changing_pin"] = True
        send(chat_id, "Yeni işlem PIN'inizi giriniz.")
        return

    if step == "pin":
        if not verify_pin(users[uid].get("pin_hash"), text.strip()):
            users[uid]["pin_failed_attempts"] = int(users[uid].get("pin_failed_attempts", 0)) + 1
            save_json(FILES["users"], users)
            if users[uid]["pin_failed_attempts"] >= 3:
                users[uid]["withdraw_locked"] = True
                save_json(FILES["users"], users)
                add_security_event(uid, "pin_lock", "3 hatalı PIN denemesi")
                send(chat_id, "Üç hatalı PIN denemesi nedeniyle çekimleriniz geçici olarak kilitlendi.")
            else:
                send(chat_id, messages["pin_wrong"])
            return
        users[uid]["pin_failed_attempts"] = 0
        save_json(FILES["users"], users)
        state["step"] = "second_confirm"; state["confirm_token"] = secrets.token_urlsafe(24); state["confirmation_consumed"] = False
        preview = state.get("preview", "İşlemi onaylıyor musunuz?")
        send(chat_id, preview, confirm_keyboard("second_confirm"))
        return

    if step == "amount":
        amount = D(text)
        if amount <= 0:
            send(chat_id, "Geçerli bir tutar giriniz."); return
        asset = state.get("asset") or state.get("from_asset")
        if flow in ("withdraw", "convert") and amount > balance(uid, asset):
            send(chat_id, f"Yetersiz bakiye · {coin_fmt(balance(uid, asset), asset)}"); return
        if amount < min_amount(flow, asset):
            send(chat_id, f"Minimum · {coin_fmt(min_amount(flow, asset), asset)}"); return
        state["amount"] = str(amount)
        if flow == "deposit":
            p = fee_percent("deposit", asset, uid)
            fee = fee_amount(amount, p)
            net = amount - fee
            state.update({"fee": str(fee), "net_amount": str(net)})
            if asset == "TL":
                summary = order_summary(
                    "TL Yükleme Özeti",
                    [
                        ("Banka", settings["bank_name"] or "-"),
                        ("Alıcı", settings["iban_owner"] or "-"),
                        ("IBAN", settings["iban"] or "-"),
                        ("Yüklenecek", coin_fmt(amount, "TL")),
                        ("Komisyon", coin_fmt(fee, "TL")),
                        ("Bakiyeye Geçecek", coin_fmt(net, "TL")),
                    ],
                    messages["iban_warning"],
                )
                send(chat_id, summary, {"inline_keyboard": [
                    [copy_button("IBAN Kopyala", settings["iban"])],
                    [inline_button("Ödemeyi Yaptım", "deposit_sent")],
                    [inline_button("İptal", "cancel")],
                ]})
            else:
                address = settings.get(f"wallet_{asset}", "")
                network = settings.get(f"network_{asset}", asset)
                state["network"] = network
                state["qr_content"] = address
                state["qr_caption"] = f"{asset} Yatırma QR Kodu · {network}"
                card = order_summary(
                    f"{asset} Yükleme Özeti",
                    [
                        ("Ağ", network),
                        ("Yüklenecek", coin_fmt(amount, asset)),
                        ("Komisyon", coin_fmt(fee, asset)),
                        ("Bakiyeye Geçecek", coin_fmt(net, asset)),
                        ("Yatırma Adresi", address or "-"),
                    ],
                    messages["deposit_crypto_intro"],
                )
                send(chat_id, card, {"inline_keyboard": [
                    [inline_button("QR Göster", "show_deposit_qr")],
                    [inline_button("Gönderimi Bildir", "deposit_sent")],
                    [inline_button("İptal", "cancel")],
                ]})
            state["step"] = "waiting_sent"
            return
        if flow == "withdraw":
            if withdrawn_today(uid, asset) + amount > daily_limit(asset) > 0:
                remaining = daily_limit(asset) - withdrawn_today(uid, asset)
                send(chat_id, f"Günlük çekim limitiniz aşılıyor. Kalan limit: {coin_fmt(max(remaining, Decimal('0')), asset)}"); return
            p = fee_percent("withdraw", asset, uid); fee = fee_amount(amount, p); net = amount - fee
            state.update({"fee": str(fee), "net_amount": str(net)})
            if asset == "TL": state["step"] = "bank_name"; send(chat_id, "Banka adını giriniz.")
            else:
                favs = [f for f in users[uid].get("favorites", []) if f.get("asset") == asset]
                if favs:
                    rows = [[inline_button(f["label"], f"favorite_use:{i}")] for i, f in enumerate(users[uid]["favorites"]) if f.get("asset") == asset]
                    rows.append([inline_button("Yeni adres gir", "favorite_use:new")])
                    send(chat_id, "Çekim adresini seçiniz.", {"inline_keyboard": rows}); state["step"] = "address_choice"
                else:
                    state["step"] = "address"; send(chat_id, "Alıcı cüzdan adresini giriniz.")
            return
        if flow == "convert":
            to_asset = state["to_asset"]; tl_value = amount * rate(asset); gross = tl_value / rate(to_asset); p = fee_percent("convert", uid=uid); fee = fee_amount(gross, p); net = gross - fee
            state.update({"tl_value": str(tl_value), "gross_to": str(gross), "fee": str(fee), "net_amount": str(net)})
            state["preview"] = order_summary(
                "Takas Özeti",
                [
                    ("Gönderilen", coin_fmt(amount, asset)),
                    ("Alınacak", coin_fmt(net, to_asset)),
                    ("Komisyon", coin_fmt(fee, to_asset)),
                ],
                "Kur ve tutarlar onay anındaki değerlerdir.",
            )
            require_pin(uid, state); return

    if flow == "deposit" and step == "sender_name":
        state["sender_name"] = text.strip()
        state["step"] = "tx_note"
        send(chat_id, "Ödeme açıklamasını veya dekont referansını giriniz. Yoksa YOK yazabilirsiniz.")
        return
    if flow == "deposit" and step == "tx_note":
        data = {
            "asset": state["asset"],
            "amount": state["amount"],
            "fee": state["fee"],
            "net_amount": state["net_amount"],
            "sender_name": state.get("sender_name", ""),
            "tx_note": text.strip(),
        }
        rid = new_request(uid, "deposit", data)
        change_pending(uid, state["asset"], D(state["net_amount"]), "deposit_pending", rid)
        user_state.pop(uid, None)
        send(chat_id, messages["deposit_received"] + f"\n\n{request_summary(rid)}", reply_keyboard())
        if ADMIN_CHAT_ID:
            send(ADMIN_CHAT_ID, "Yeni bakiye yükleme bildirimi\n\n" + request_summary(rid))
        return
    if flow == "withdraw" and step == "bank_name": state["bank_name"] = text.strip(); state["step"] = "iban"; send(chat_id, "IBAN bilginizi giriniz."); return
    if flow == "withdraw" and step == "iban": state["iban"] = text.replace(" ", "").upper(); state["step"] = "name"; send(chat_id, "Hesap sahibinin ad ve soyadını giriniz."); return
    if flow == "withdraw" and step == "name":
        state["name"] = text.strip()
        state["preview"] = order_summary(
            "TL Çekim Özeti",
            [
                ("Tutar", coin_fmt(state["amount"], state["asset"])),
                ("Komisyon", coin_fmt(state["fee"], state["asset"])),
                ("Alıcıya Geçecek", coin_fmt(state["net_amount"], state["asset"])),
                ("IBAN", state["iban"]),
                ("Alıcı", state["name"]),
            ],
        )
        require_pin(uid, state)
        return
    if flow == "withdraw" and step == "address":
        state["address"] = text.strip()
        state["preview"] = order_summary(
            f"{state['asset']} Çekim Özeti",
            [
                ("Tutar", coin_fmt(state["amount"], state["asset"])),
                ("Komisyon", coin_fmt(state["fee"], state["asset"])),
                ("Gönderilecek", coin_fmt(state["net_amount"], state["asset"])),
                ("Cüzdan Adresi", state["address"]),
            ],
        )
        require_pin(uid, state)
        return
    if flow == "favorite_add" and step == "label": state["label"] = text.strip(); state["step"] = "address"; send(chat_id, "Cüzdan adresini giriniz."); return
    if flow == "favorite_add" and step == "address":
        users[uid]["favorites"].append({"label": state["label"], "asset": state["asset"], "address": text.strip(), "created_at": now()}); save_json(FILES["users"], users); user_state.pop(uid, None); send(chat_id, "Favori adres kaydedildi.", reply_keyboard()); return


def handle_callback(chat_id, username, data, cb_id):
    answer(cb_id)
    uid = str(chat_id); get_user(chat_id, username)
    if data == "cancel": user_state.pop(uid, None); send(chat_id, messages["request_cancelled"], reply_keyboard()); return
    if data == "show_deposit_qr":
        state = user_state.get(uid, {})
        content = str(state.get("qr_content", "")).strip()
        if not content:
            send(chat_id, "QR bilgisi bulunamadı.")
            return
        send_qr(chat_id, content, state.get("qr_caption", "Yatırma QR Kodu"))
        return
    if data.startswith("detail:"):
        rid = data.split(":", 1)[1]
        if requests_db.get(rid, {}).get("user_id") == uid:
            send(chat_id, receipt_text(rid), {"inline_keyboard": [[inline_button("Makbuzu Yeniden Göster", f"detail:{rid}")]]})
        return
    if data.startswith("deposit_asset:"):
        asset = data.split(":", 1)[1]; user_state[uid] = {"flow": "deposit", "step": "amount", "asset": asset}; send(chat_id, f"{asset} için {messages['amount_question']}\nMinimum: {coin_fmt(min_amount('deposit', asset), asset)}"); return
    if data == "deposit_sent":
        state = user_state.get(uid, {})
        if not state:
            send(chat_id, "İşlem oturumu bulunamadı.")
            return
        if state.get("asset") == "TL":
            state["step"] = "sender_name"
            send(chat_id, "Ödemeyi gönderen kişinin ad ve soyadını giriniz.")
        else:
            data = {
                "asset": state["asset"],
                "amount": state["amount"],
                "fee": state["fee"],
                "net_amount": state["net_amount"],
                "network": state.get("network", ""),
            }
            rid = new_request(uid, "deposit", data)
            change_pending(uid, state["asset"], D(state["net_amount"]), "deposit_pending", rid)
            user_state.pop(uid, None)
            send(chat_id, messages["deposit_received"] + f"\n\n{request_summary(rid)}", reply_keyboard())
            if ADMIN_CHAT_ID:
                send(ADMIN_CHAT_ID, "Yeni bakiye yükleme bildirimi\n\n" + request_summary(rid))
        return
    if data.startswith("withdraw_asset:"):
        asset = data.split(":", 1)[1]
        if balance(uid, asset) <= 0: send(chat_id, messages["no_balance"]); return
        user_state[uid] = {"flow": "withdraw", "step": "amount", "asset": asset}; send(chat_id, f"Çekilebilir bakiye: {coin_fmt(balance(uid, asset), asset)}\nMinimum çekim: {coin_fmt(min_amount('withdraw', asset), asset)}\n\nÇekmek istediğiniz tutarı giriniz."); return
    if data.startswith("convert_from:"):
        asset = data.split(":", 1)[1]
        if balance(uid, asset) <= 0: send(chat_id, messages["no_balance"]); return
        user_state[uid] = {"flow": "convert", "step": "to_asset", "from_asset": asset}
        send(chat_id, f"Dönüştürülecek: {coin_fmt(balance(uid, asset), asset)} kullanılabilir.", asset_keyboard("convert_to", ASSETS, asset)); return
    if data.startswith("convert_to:"):
        to_asset = data.split(":", 1)[1]; state = user_state.get(uid, {}); state.update({"to_asset": to_asset, "step": "amount"}); send(chat_id, f"Bakiye · {coin_fmt(balance(uid, state['from_asset']), state['from_asset'])}\nMinimum · {coin_fmt(min_amount('convert', state['from_asset']), state['from_asset'])}\n\nTutarı girin."); return
    if data == "second_confirm":
        state = user_state.get(uid, {})
        if not consume_confirmation(state):
            answer(cb_id, "Bu onay daha önce kullanıldı."); return
        try:
            if state.get("flow") == "withdraw": finalize_withdraw(uid, state)
            elif state.get("flow") == "convert":
                rid = atomic_convert(uid, state); user_state.pop(uid, None)
                send(chat_id, receipt_text(rid), reply_keyboard())
        except ValueError as exc:
            user_state.pop(uid, None); send(chat_id, str(exc), reply_keyboard())
        return
    if data == "favorite:add": user_state[uid] = {"flow": "favorite_add", "step": "asset"}; send(chat_id, "Favori adresin para birimini seçiniz.", asset_keyboard("favorite_asset", CRYPTO_ASSETS)); return
    if data.startswith("favorite_asset:"): user_state[uid] = {"flow": "favorite_add", "step": "label", "asset": data.split(":",1)[1]}; send(chat_id, "Bu adres için bir isim giriniz."); return
    if data.startswith("favorite_use:"):
        choice = data.split(":", 1)[1]; state = user_state.get(uid, {})
        if choice == "new": state["step"] = "address"; send(chat_id, "Alıcı cüzdan adresini giriniz.")
        else:
            fav = users[uid]["favorites"][int(choice)]; state["address"] = fav["address"]
            state["preview"] = order_summary(
                f"{state['asset']} Çekim Özeti",
                [
                    ("Tutar", coin_fmt(state["amount"], state["asset"])),
                    ("Komisyon", coin_fmt(state["fee"], state["asset"])),
                    ("Gönderilecek", coin_fmt(state["net_amount"], state["asset"])),
                    ("Cüzdan Adresi", state["address"]),
                ],
            )
            require_pin(uid, state)
        return
    if data == "security:set_pin":
        if users[uid].get("pin_hash"):
            user_state[uid] = {"flow": "set_pin", "step": "old_pin"}
            send(chat_id, "Mevcut işlem PIN'inizi giriniz.")
        else:
            user_state[uid] = {"flow": "set_pin", "step": "set_pin"}
            send(chat_id, messages["pin_set_question"])
        return
    if data == "security:notifications":
        n = users[uid]["notifications"]
        kb = {"inline_keyboard": [[inline_button(f"İşlemler: {'Açık' if n['transactions'] else 'Kapalı'}", "notify:transactions")], [inline_button(f"Güvenlik: {'Açık' if n['security'] else 'Kapalı'}", "notify:security")], [inline_button(f"Duyurular: {'Açık' if n['announcements'] else 'Kapalı'}", "notify:announcements")]]}
        send(chat_id, "Bildirim tercihlerinizi düzenleyiniz.", kb); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]; users[uid]["notifications"][key] = not users[uid]["notifications"].get(key, True); save_json(FILES["users"], users); send(chat_id, "Bildirim tercihi güncellendi."); return
    if data == "security:logout_sessions":
        users[uid]["sessions"] = {"telegram": {"created_at": now(), "last_seen": now(), "active": True}}; save_json(FILES["users"], users); send(chat_id, "Diğer oturum kayıtları kapatıldı."); return


def load_offset():
    return load_json(OFFSET_FILE, {"offset": None}).get("offset")


def save_offset(value):
    save_json(OFFSET_FILE, {"offset": value})


def bot_loop():
    global OFFSET
    OFFSET = load_offset()
    while True:
        try:
            result = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", params={"offset": OFFSET, "timeout": 25}, timeout=35).json()
            for update in result.get("result", []):
                OFFSET = update["update_id"] + 1
                save_offset(OFFSET)
                if "message" in update:
                    m = update["message"]; chat_id = m["chat"]["id"]; username = m.get("from", {}).get("username", "unknown"); text = m.get("text", "")
                    ids = [str(e.get("custom_emoji_id")) for e in m.get("entities", []) if e.get("type") == "custom_emoji" and e.get("custom_emoji_id")]
                    if ids and str(chat_id) == str(ADMIN_CHAT_ID): send(chat_id, "\n".join(ids)); continue
                    handle_text(chat_id, username, text)
                elif "callback_query" in update:
                    c = update["callback_query"]; handle_callback(c["message"]["chat"]["id"], c.get("from", {}).get("username", "unknown"), c.get("data", ""), c["id"])
        except Exception as exc:
            print("BOT LOOP ERROR:", exc); time.sleep(5)


def logged_in(): return session.get("login") is True

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    if response.content_type and response.content_type.startswith("text/html"):
        body = response.get_data(as_text=True); token = h(csrf_token())
        body = re.sub(r"(<form\b[^>]*method=['\"]post['\"][^>]*>)", lambda m: m.group(1) + f"<input type='hidden' name='csrf_token' value='{token}'>", body, flags=re.I)
        response.set_data(body)
    return response

@app.route("/")
def home(): return "ZaqelV2 aktif ✅"

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    attempts = LOGIN_ATTEMPTS[ip]; cutoff = time.time() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff: attempts.popleft()
    if request.method == "POST":
        if len(attempts) >= LOGIN_MAX_ATTEMPTS: return "Çok fazla başarısız giriş denemesi", 429
        if secrets.compare_digest(request.form.get("username", ""), PANEL_USERNAME) and secrets.compare_digest(request.form.get("password", ""), PANEL_PASSWORD):
            attempts.clear(); session.clear(); session["login"] = True; session.permanent = True; csrf_token(); return redirect("/admin")
        attempts.append(time.time()); time.sleep(min(2 ** len(attempts), 8) / 10); error = "Hatalı giriş"
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ZaqelV2</title><style>body{{margin:0;background:#07111f;color:#fff;font-family:Arial;display:grid;place-items:center;min-height:100vh}}form{{width:min(400px,calc(100% - 32px));background:#0e1b2d;padding:28px;border:1px solid #1e3656;border-radius:22px}}input,button{{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:12px;border:1px solid #29466f;background:#091424;color:#fff}}button{{background:#00d4ff;color:#001018;font-weight:800}}</style></head><body><form method='post'><h1>ZaqelV2 Admin</h1><input name='username' autocomplete='username' placeholder='Kullanıcı adı'><input type='password' autocomplete='current-password' name='password' placeholder='Şifre'><button>Giriş Yap</button><p>{h(error)}</p></form></body></html>"""

@app.route("/logout")
def logout(): session.clear(); return redirect("/login")


def status_label(s): return {"pending":"Bekliyor","processing":"İşleniyor","completed":"Tamamlandı","rejected":"Reddedildi"}.get(s, s)


def reserve_totals():
    totals = {a: Decimal("0") for a in ASSETS}; pending = {a: Decimal("0") for a in ASSETS}
    for u in users.values():
        for a in ASSETS: totals[a] += D(u.get("balances", {}).get(a, "0"))
    for r in requests_db.values():
        if r.get("type") == "withdraw" and r.get("status") in ("pending", "processing"): pending[r.get("asset")] += D(r.get("amount"))
    return totals, pending


def panel_request_card(rid, r):
    rows = [("Kullanıcı", f"@{users.get(r.get('user_id'),{}).get('username','unknown')} · {r.get('user_id')}"), ("Tür", r.get("type")), ("Durum", status_label(r.get("status"))), ("Tarih", r.get("created_at"))]
    if r.get("type") == "deposit":
        rows += [("Tutar", fmt(r.get("amount"), r.get("asset"))), ("Bakiyeye Geçecek", fmt(r.get("net_amount"), r.get("asset")))]
        if r.get("asset") == "TL":
            rows += [("Gönderen", r.get("sender_name", "")), ("Açıklama", r.get("tx_note", ""))]
        else:
            rows += [("Ağ", r.get("network", ""))]
    elif r.get("type") == "withdraw": rows += [("Tutar", fmt(r.get("amount"), r.get("asset"))), ("Komisyon", fmt(r.get("fee"), r.get("asset"))), ("Net", fmt(r.get("net_amount"), r.get("asset"))), ("Hedef", r.get("iban") or r.get("address", ""))]
    elif r.get("type") == "convert": rows += [("Gönderilen", fmt(r.get("from_amount"), r.get("from_asset"))), ("Alınan", fmt(r.get("net_to_amount"), r.get("to_asset")))]
    body = "".join(f"<div class='detail'><span>{h(k)}</span><b>{h(v)}</b></div>" for k,v in rows)
    actions = ""
    if r.get("status") in ("pending", "processing"):
        actions = f"<form method='post' class='actions'><input type='hidden' name='rid' value='{h(rid)}'><button name='action' value='process_request'>İşleme Al</button><button class='ok' name='action' value='approve_request'>Tamamla</button><button class='bad' name='action' value='reject_request'>Reddet</button></form>"
    return f"<article class='card'><div class='cardhead'><h3>#{h(rid)}</h3><span class='badge'>{h(status_label(r.get('status')))}</span></div>{body}{actions}</article>"


@app.route("/admin/requests-fragment")
def admin_requests_fragment():
    if not logged_in():
        return "", 401
    items = sorted(requests_db.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)
    return "".join(panel_request_card(rid, r) for rid, r in items[:100]) or '<div class="metric">İşlem bulunmuyor.</div>'


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in(): return redirect("/login")
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "settings":
            for key in DEFAULT_SETTINGS: settings[key] = request.form.get(key, settings.get(key, ""))
            for key in DEFAULT_MESSAGES: messages[key] = request.form.get(key, messages.get(key, ""))
            save_json(FILES["settings"], settings); save_json(FILES["messages"], messages); add_admin_log("settings", "Ayarlar güncellendi")
        elif action in ("process_request", "approve_request", "reject_request"):
            rid = request.form.get("rid", ""); r = requests_db.get(rid)
            if r:
                uid = r["user_id"]
                if action == "process_request" and r.get("status") == "pending": r["status"] = "processing"
                elif action == "approve_request" and r.get("status") in ("pending", "processing"):
                    if r["type"] == "deposit":
                        change_pending(uid, r["asset"], -D(r["net_amount"]), "deposit_pending_release", rid)
                        change_balance(uid, r["asset"], r["net_amount"], "deposit_approved", rid)
                    elif r["type"] == "withdraw":
                        change_pending(uid, r["asset"], -D(r["amount"]), "withdraw_pending_release", rid)
                    r["status"] = "completed"; r["completed_at"] = now(); send(uid, "İşleminiz tamamlandı.\n\n" + receipt_text(rid), reply_keyboard())
                elif action == "reject_request" and r.get("status") in ("pending", "processing"):
                    if r["type"] == "withdraw":
                        change_pending(uid, r["asset"], -D(r["amount"]), "withdraw_pending_cancel", rid)
                        change_balance(uid, r["asset"], r["amount"], "withdraw_refund", rid)
                    elif r["type"] == "deposit":
                        change_pending(uid, r["asset"], -D(r["net_amount"]), "deposit_pending_cancel", rid)
                    r["status"] = "rejected"; r["rejected_at"] = now(); send(uid, f"İşleminiz reddedildi.\n\nİşlem No: #{rid}", reply_keyboard())
                r["updated_at"] = now(); save_json(FILES["requests"], requests_db); add_admin_log(action, f"#{rid}", uid)
        elif action == "adjust_balance":
            uid, asset, note = request.form.get("user_id", ""), request.form.get("asset", ""), request.form.get("note", "").strip(); amount = D(request.form.get("amount", "0"))
            if uid in users and asset in ASSETS and amount != 0 and note:
                try: change_balance(uid, asset, amount, "admin_adjustment", "", note); add_admin_log("adjust_balance", f"{asset} {amount}: {note}", uid)
                except ValueError: pass
        elif action == "update_user_profile":
            uid = request.form.get("user_id", "")
            if uid in users:
                tier = request.form.get("tier", "Basic")
                if tier not in ("Basic", "Plus", "Prime"):
                    tier = "Basic"
                users[uid]["tier"] = tier
                users[uid]["custom_fee_percent"] = request.form.get("custom_fee_percent", "").strip()
                users[uid]["custom_daily_limit_TL"] = request.form.get("custom_daily_limit_TL", "").strip()
                users[uid]["note"] = request.form.get("note", "").strip()
                save_json(FILES["users"], users)
                add_admin_log("update_user_profile", f"Seviye: {tier}", uid)
        elif action in ("freeze_user", "unfreeze_user", "lock_withdraw", "unlock_withdraw"):
            uid = request.form.get("user_id", "")
            if uid in users:
                if action == "freeze_user": users[uid]["status"] = "frozen"
                elif action == "unfreeze_user": users[uid]["status"] = "active"
                elif action == "lock_withdraw": users[uid]["withdraw_locked"] = True
                else: users[uid]["withdraw_locked"] = False
                save_json(FILES["users"], users); add_admin_log(action, "Kullanıcı güvenlik durumu değiştirildi", uid)
        elif action == "broadcast":
            text = request.form.get("announcement_text", "").strip()
            if text:
                count = 0
                for uid, u in users.items():
                    if u.get("notifications", {}).get("announcements", True): send(uid, "📢 " + text, reply_keyboard()); count += 1
                add_admin_log("broadcast", f"{count} kullanıcıya gönderildi")
        return redirect("/admin")

    q = request.args.get("q", "").lower().strip(); status_filter = request.args.get("status", "all"); type_filter = request.args.get("type", "all")
    filtered_users = [(uid,u) for uid,u in users.items() if not q or q in uid.lower() or q in str(u.get("username","")).lower()]
    filtered_requests = [(rid,r) for rid,r in requests_db.items() if (status_filter == "all" or r.get("status") == status_filter) and (type_filter == "all" or r.get("type") == type_filter)]
    filtered_requests.sort(key=lambda x:x[1].get("created_at",""), reverse=True)
    totals, pending = reserve_totals()
    user_rows = "".join(f"<tr><td><a href='/admin/user/{h(uid)}'>{h(uid)}</a></td><td>@{h(u.get('username','unknown'))}</td><td>{h(fmt(u.get('balances',{}).get('TL','0'),'TL'))}</td><td>{h(fmt(u.get('balances',{}).get('USDT','0'),'USDT'))}</td><td>{h(fmt(u.get('balances',{}).get('LTC','0'),'LTC'))}</td><td>{h(fmt(u.get('balances',{}).get('TRX','0'),'TRX'))}</td><td>{h(u.get('tier','Basic'))}</td><td>{h(u.get('status'))}</td><td>{'Kilitli' if u.get('withdraw_locked') else 'Açık'}</td></tr>" for uid,u in filtered_users)
    cards = "".join(panel_request_card(rid,r) for rid,r in filtered_requests[:100])
    metrics = "".join(f"<div class='metric'><span>{a} kullanıcı bakiyesi</span><b>{fmt(totals[a],a)}</b><small>Bekleyen çekim: {fmt(pending[a],a)}</small></div>" for a in ASSETS)
    setting_groups = {
        "Kur Yönetimi": [k for k in DEFAULT_SETTINGS if k.startswith("rate_")],
        "Komisyon Yönetimi": [k for k in DEFAULT_SETTINGS if k.startswith("fee_")],
        "Limit Yönetimi": [k for k in DEFAULT_SETTINGS if k.startswith("min_") or k.startswith("daily_")],
        "Cüzdan ve Ağ Ayarları": [k for k in DEFAULT_SETTINGS if k.startswith("wallet_") or k.startswith("network_") or k in ("bank_name", "iban", "iban_owner")],
        "Güvenlik, Bakım ve Duyuru": [k for k in DEFAULT_SETTINGS if k.startswith("maintenance_") or k.startswith("announcement_")],
        "Custom Emoji ID'leri": [k for k in DEFAULT_SETTINGS if k.startswith("icon_")],
    }
    setting_sections = ""
    for title, keys in setting_groups.items():
        fields = "".join(f"<label>{h(k)}</label><input name='{h(k)}' value='{h(settings.get(k,''))}'>" for k in keys)
        setting_sections += f"<details class='subbox'><summary>{h(title)}</summary><div class='grid inner'>{fields}</div></details>"
    message_inputs = "".join(f"<label>{h(k)}</label><textarea name='{h(k)}'>{h(messages.get(k,''))}</textarea>" for k in DEFAULT_MESSAGES)
    logs = "".join(f"<tr><td>{h(x.get('created_at'))}</td><td>{h(x.get('action'))}</td><td>{h(x.get('user_id'))}</td><td>{h(x.get('details'))}</td></tr>" for x in reversed(admin_logs[-100:]))
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ZaqelV2 Admin</title><style>
    :root{{--bg:#07111f;--panel:#0d1b2d;--panel2:#0a1626;--line:#1d3857;--text:#f5fbff;--muted:#91abc0;--cyan:#22d3ee;--green:#22c55e;--red:#ef4444;--orange:#f59e0b}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(135deg,#06101d,#0a1830);color:var(--text);font-family:Inter,Arial,sans-serif}}a{{color:var(--cyan);text-decoration:none}}.layout{{display:grid;grid-template-columns:250px 1fr;min-height:100vh}}aside{{padding:24px;background:rgba(5,15,28,.96);border-right:1px solid var(--line);position:sticky;top:0;height:100vh}}aside h1{{font-size:22px}}aside a{{display:block;color:#cde9f6;padding:11px 12px;border-radius:10px;margin:4px 0}}aside a:hover{{background:#10243b}}main{{padding:24px;min-width:0}}.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}}.box,.card{{background:rgba(13,27,45,.9);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 16px 42px rgba(0,0,0,.2)}}section{{margin-bottom:20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}}.metric{{padding:18px;background:var(--panel2);border:1px solid var(--line);border-radius:16px}}.metric span,.metric small{{color:var(--muted)}}.metric b{{display:block;font-size:24px;margin:7px 0}}.cardhead{{display:flex;justify-content:space-between;align-items:center}}.badge{{padding:6px 10px;border-radius:999px;background:#16314f;color:#c9eff8}}.detail{{display:flex;justify-content:space-between;gap:14px;padding:8px 0;border-bottom:1px dashed #244361}}.detail span{{color:var(--muted)}}.detail b{{max-width:65%;text-align:right;overflow-wrap:anywhere}}input,textarea,select,button{{width:100%;padding:11px 12px;border-radius:11px;border:1px solid #29496d;background:#081525;color:white}}textarea{{min-height:80px}}button{{background:var(--cyan);color:#001018;font-weight:800;border:0;cursor:pointer}}button.ok{{background:var(--green);color:white}}button.bad{{background:var(--red);color:white}}.actions{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}}label{{display:block;color:var(--muted);margin:11px 0 6px}}details summary{{cursor:pointer;font-size:18px;font-weight:800;padding:10px 0}}.tablewrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:900px}}th,td{{padding:11px;border-bottom:1px solid var(--line);text-align:left}}.filters{{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:10px}}.settings-stack{{display:grid;gap:12px}}.subbox{{background:#081525;border:1px solid var(--line);border-radius:14px;padding:0 14px}}.subbox summary{{padding:16px 0}}.inner{{padding:0 0 16px}}@media(max-width:900px){{.layout{{grid-template-columns:1fr}}aside{{height:auto;position:static}}.filters{{grid-template-columns:1fr}}}}@media(max-width:680px){{main{{padding:14px}}.detail{{display:block}}.detail b{{display:block;text-align:left;max-width:100%;margin-top:4px}}.actions{{grid-template-columns:1fr}}}}
    </style></head><body><div class='layout'><aside><h1>ZaqelV2</h1><a href='#dashboard'>Genel Bakış</a><a href='#requests'>İşlemler</a><a href='#users'>Kullanıcılar</a><a href='#adjust'>Bakiye Düzeltme</a><a href='#broadcast'>Duyurular</a><a href='#settings'>Ayarlar</a><a href='#logs'>Admin Logları</a><a href='/logout'>Çıkış</a></aside><main><div class='top'><div><h2>Kontrol Merkezi</h2><p style='color:var(--muted)'>Cüzdan, kullanıcı, işlem ve güvenlik yönetimi</p></div></div>
    <section id='dashboard'><div class='grid'>{metrics}<div class='metric'><span>Toplam kullanıcı</span><b>{len(users)}</b></div><div class='metric'><span>Bekleyen işlem</span><b>{sum(1 for r in requests_db.values() if r.get('status') in ('pending','processing'))}</b></div></div></section>
    <section id='requests' class='box'><h2>İşlem Talepleri</h2><form class='filters' method='get'><input name='q' placeholder='Kullanıcı ara' value='{h(q)}'><select name='status'><option value='all'>Tüm durumlar</option>{''.join(f"<option value='{s}' {'selected' if status_filter==s else ''}>{status_label(s)}</option>" for s in ['pending','processing','completed','rejected'])}</select><select name='type'><option value='all'>Tüm türler</option>{''.join(f"<option value='{t}' {'selected' if type_filter==t else ''}>{ {'deposit':'Yatırma','withdraw':'Çekme','convert':'Dönüştürme'}[t] }</option>" for t in ['deposit','withdraw','convert'])}</select><button>Filtrele</button></form><div id='request-grid' class='grid' style='margin-top:14px'>{cards or '<div class="metric">İşlem bulunmuyor.</div>'}</div></section>
    <section id='users' class='box'><h2>Kullanıcılar</h2><div class='tablewrap'><table><tr><th>ID</th><th>Kullanıcı</th><th>TL</th><th>USDT</th><th>LTC</th><th>TRX</th><th>Seviye</th><th>Hesap</th><th>Çekim</th></tr>{user_rows}</table></div></section>
    <section id='adjust' class='box'><h2>Admin Bakiye Ekle / Düş</h2><form method='post' class='grid'><input type='hidden' name='action' value='adjust_balance'><div><label>Kullanıcı ID</label><input name='user_id' required></div><div><label>Para Birimi</label><select name='asset'>{''.join(f'<option>{a}</option>' for a in ASSETS)}</select></div><div><label>Tutar (+/-)</label><input name='amount' required></div><div><label>Zorunlu açıklama</label><input name='note' required></div><div><label>&nbsp;</label><button>Uygula</button></div></form></section>
    <section id='broadcast' class='box'><h2>Duyuru Gönder</h2><form method='post'><input type='hidden' name='action' value='broadcast'><textarea name='announcement_text' placeholder='Duyuru metni'></textarea><button style='margin-top:10px'>Tüm Kullanıcılara Gönder</button></form></section>
    <section id='settings'><form method='post'><input type='hidden' name='action' value='settings'><details class='box' open><summary>Ayar Yönetimi</summary><div class='settings-stack'>{setting_sections}</div></details><details class='box'><summary>Bot Mesajları</summary><div class='grid'>{message_inputs}</div></details><button style='margin:10px 0 20px'>Tüm Ayarları Kaydet</button></form></section>
    <section id='logs' class='box'><h2>Admin İşlem Logları</h2><div class='tablewrap'><table><tr><th>Tarih</th><th>İşlem</th><th>Kullanıcı</th><th>Detay</th></tr>{logs}</table></div></section>
    </main></div><script>
    async function yenileTalepler(){{
      try{{
        const response=await fetch('/admin/requests-fragment',{{cache:'no-store'}});
        if(response.ok){{document.getElementById('request-grid').innerHTML=await response.text();}}
      }}catch(error){{console.log('Talep yenileme hatası',error);}}
    }}
    setInterval(yenileTalepler,20000);
    document.addEventListener('visibilitychange',()=>{{if(!document.hidden)yenileTalepler();}});
    </script></body></html>"""


@app.route("/admin/user/<uid>", methods=["GET", "POST"])
def admin_user(uid):
    if not logged_in(): return redirect("/login")
    if uid not in users: return "Kullanıcı bulunamadı", 404
    u = users[uid]
    reqs = sorted([r for r in requests_db.values() if r.get("user_id") == uid], key=lambda x:x.get("created_at",""), reverse=True)
    txs = sorted([t for t in transactions.values() if t.get("user_id") == uid], key=lambda x:x.get("created_at",""), reverse=True)
    req_html = "".join(f"<tr><td>#{h(r.get('id'))}</td><td>{h(r.get('type'))}</td><td>{h(status_label(r.get('status')))}</td><td>{h(r.get('created_at'))}</td></tr>" for r in reqs)
    tx_html = "".join(f"<tr><td>{h(t.get('created_at'))}</td><td>{h(t.get('kind'))}</td><td>{h(t.get('bucket','available'))}</td><td>{h(fmt(t.get('amount'),t.get('asset')))}</td><td>{h(fmt(t.get('available_after','0'),t.get('asset')))}</td><td>{h(fmt(t.get('pending_after','0'),t.get('asset')))}</td><td>{h(t.get('note'))}</td></tr>" for t in txs)
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Kullanıcı {h(uid)}</title><style>body{{background:#07111f;color:white;font-family:Arial;margin:0;padding:20px}}a{{color:#22d3ee}}.box{{background:#0d1b2d;border:1px solid #1d3857;border-radius:16px;padding:18px;margin-bottom:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.metric{{background:#081525;padding:14px;border-radius:12px}}button{{padding:11px;border:0;border-radius:10px;background:#22d3ee;font-weight:800}}button.bad{{background:#ef4444;color:white}}table{{width:100%;border-collapse:collapse;min-width:700px}}td,th{{padding:10px;border-bottom:1px solid #1d3857;text-align:left}}.table{{overflow:auto}}</style></head><body><a href='/admin'>← Panele dön</a><h1>@{h(u.get('username','unknown'))}</h1><div class='box'><div class='grid'>{''.join(f"<div class='metric'><small>{a}</small><h2>{h(fmt(u.get('balances',{}).get(a,'0'),a))}</h2><p>Bekleyen: {h(fmt(u.get('pending_balances',{}).get(a,'0'),a))}</p></div>" for a in ASSETS)}</div></div><div class='box'><h2>Kullanıcı Profili</h2><form method='post' action='/admin'><input type='hidden' name='action' value='update_user_profile'><input type='hidden' name='user_id' value='{h(uid)}'><div class='grid'><div><label>Hesap Seviyesi</label><select name='tier'><option {'selected' if u.get('tier')=='Basic' else ''}>Basic</option><option {'selected' if u.get('tier')=='Plus' else ''}>Plus</option><option {'selected' if u.get('tier')=='Prime' else ''}>Prime</option></select></div><div><label>Özel Komisyon % (boşsa genel)</label><input name='custom_fee_percent' value='{h(u.get('custom_fee_percent',''))}'></div><div><label>Özel TL Günlük Limit</label><input name='custom_daily_limit_TL' value='{h(u.get('custom_daily_limit_TL',''))}'></div><div><label>Admin Notu</label><input name='note' value='{h(u.get('note',''))}'></div></div><button style='margin-top:12px'>Profili Kaydet</button></form></div><div class='box'><h2>Güvenlik</h2><p>Hesap: {h(u.get('status'))} · Çekim: {'Kilitli' if u.get('withdraw_locked') else 'Açık'} · Son etkinlik: {h(u.get('last_seen'))} · Oturum sürümü: {h(u.get('session_version',1))} · Son PIN değişimi: {h(u.get('last_pin_change') or '-')}</p><div class='grid'><form method='post' action='/admin'><input type='hidden' name='user_id' value='{h(uid)}'><button name='action' value='freeze_user' class='bad'>Hesabı Dondur</button></form><form method='post' action='/admin'><input type='hidden' name='user_id' value='{h(uid)}'><button name='action' value='unfreeze_user'>Hesabı Aç</button></form><form method='post' action='/admin'><input type='hidden' name='user_id' value='{h(uid)}'><button name='action' value='lock_withdraw' class='bad'>Çekimi Kilitle</button></form><form method='post' action='/admin'><input type='hidden' name='user_id' value='{h(uid)}'><button name='action' value='unlock_withdraw'>Çekimi Aç</button></form></div></div><div class='box'><h2>İşlemler</h2><div class='table'><table><tr><th>No</th><th>Tür</th><th>Durum</th><th>Tarih</th></tr>{req_html}</table></div></div><div class='box'><h2>İşlem Defteri (Ledger)</h2><div class='table'><table><tr><th>Tarih</th><th>Tür</th><th>Hesap</th><th>Tutar</th><th>Kullanılabilir Sonrası</th><th>Bekleyen Sonrası</th><th>Not</th></tr>{tx_html}</table></div></div></body></html>"""


if __name__ == "__main__":
    validate_runtime_config()
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
