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

    formatted = f"{out:,.6f}" if asset == "LTC" else f"{out:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")

    return f"{formatted} {{{{{asset}}}}}"


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
    "icon_TL": "5897961936837943618",
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
if not str(settings.get("icon_TL", "")).strip():
    settings["icon_TL"] = DEFAULT_SETTINGS["icon_TL"]
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
        if emoji_id:
            replacement = "🪙"
            entities.append({
                "type": "custom_emoji",
                "offset": offset,
                "length": _utf16_len(replacement),
                "custom_emoji_id": emoji_id,
            })
        elif asset == "TL":
            replacement = "₺"
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
            [{"text": "Kayıtlı Adresler"}, {"text": "Destek"}],
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
        users[uid]["pending_balances"][asset] = s