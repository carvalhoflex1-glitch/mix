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
    "welcome": "Nerlo Wallet'a hoş geldiniz.\n\nCüzdanınızı sade ve güvenli biçimde yönetmek için aşağıdaki menüden devam edebilirsiniz.",
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
    return api("sendPhoto", data, {"photo": ("nerlo-wallet-qr.png", buf, "image/png")})

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
    legacy = hashlib.sha256((os.getenv("PIN_SALT", bytes.fromhex("7a6171656c76322d70696e2d73616c74").decode()) + str(pin)).encode()).hexdigest()
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
        source = state["from_asset"]
        target = state["to_asset"]
        amount = D(state["amount"])

        if amount <= 0:
            raise ValueError("Geçersiz dönüşüm tutarı")
        if balance(uid, source) < amount:
            raise ValueError("Yetersiz bakiye")

        source_rate = rate(source)
        target_rate = rate(target)
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Geçersiz kur nedeniyle işlem yapılamıyor")

        fee_rate = fee_percent("convert", uid=uid)
        if fee_rate < 0 or fee_rate >= 100:
            raise ValueError("Geçersiz komisyon oranı")

        tl_value = amount * source_rate
        gross = tl_value / target_rate
        fee = fee_amount(gross, fee_rate)
        net = gross - fee
        if net <= 0:
            raise ValueError("Komisyon sonrası geçerli tutar oluşmadı")

        state.update({
            "tl_value": str(tl_value),
            "gross_to": str(gross),
            "fee": str(fee),
            "net_amount": str(net),
        })

        rid = new_request(uid, "convert", {
            "from_asset": source,
            "to_asset": target,
            "from_amount": str(amount),
            "tl_value": str(tl_value),
            "fee": str(fee),
            "net_to_amount": str(net),
            "second_confirmation": True,
            "idempotency_key": state.get("confirm_token", ""),
        })
        old_source, old_target = balance(uid, source), balance(uid, target)
        try:
            change_balance(uid, source, -amount, "convert_out", rid)
            change_balance(uid, target, net, "convert_in", rid)
            requests_db[rid].update({"status": "completed", "completed_at": now(), "updated_at": now()})
            save_json(FILES["requests"], requests_db)
            return rid
        except Exception:
            users[uid]["balances"][source] = str(old_source)
            users[uid]["balances"][target] = str(old_target)
            requests_db.pop(rid, None)
            save_json(FILES["users"], users)
            save_json(FILES["requests"], requests_db)
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
    lines = [f"Cüzdan · {tier_label(u.get('tier', 'Basic'))}", ""]
    for asset in ASSETS:
        lines.append(coin_fmt(u["balances"].get(asset, "0"), asset))
        pending = D(u.get("pending_balances", {}).get(asset, "0"))
        if pending > 0:
            lines.append(f"Bekleyen · {coin_fmt(pending, asset)}")
        lines.append("")
    return "\n".join(lines).rstrip()


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


def send_second_confirmation(uid, state, prefix=""):
    state["step"] = "second_confirm"
    state["confirm_token"] = secrets.token_urlsafe(24)
    state["confirmation_consumed"] = False
    preview = state.get("preview", "İşlemi onaylıyor musunuz?")
    text = f"{prefix}\n\n{preview}" if prefix else preview
    send(uid, text, confirm_keyboard("second_confirm"))


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
    if text == "Kayıtlı Adresler":
        favs = users[uid].get("favorites", [])
        if not favs:
            send(chat_id, "Kayıtlı adresiniz bulunmuyor.", {"inline_keyboard": [[inline_button("Yeni Adres Ekle", "favorite:add")]]})
        else:
            lines = ["Kayıtlı Cüzdan Adresleri"] + [f"{i+1}. {f['label']} · {f['asset']}\n{f['address']}" for i, f in enumerate(favs)]
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
        if next_step == "pin":
            state.pop("new_pin", None)
            state.pop("after_pin_setup", None)
            send_second_confirmation(uid, state, messages["pin_saved"])
        elif next_step:
            state.pop("new_pin", None)
            state.pop("after_pin_setup", None)
            state["step"] = next_step
            send(chat_id, messages["pin_saved"])
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
        send_second_confirmation(uid, state)
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
            to_asset = state["to_asset"]
            source_rate = rate(asset)
            target_rate = rate(to_asset)
            if source_rate <= 0 or target_rate <= 0:
                send(chat_id, "Geçersiz kur nedeniyle dönüşüm yapılamıyor.")
                return
            p = fee_percent("convert", uid=uid)
            if p < 0 or p >= 100:
                send(chat_id, "Geçersiz komisyon oranı.")
                return
            tl_value = amount * source_rate
            gross = tl_value / target_rate
            fee = fee_amount(gross, p)
            net = gross - fee
            if net <= 0:
                send(chat_id, "Komisyon sonrası geçerli tutar oluşmadı.")
                return
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
        users[uid]["favorites"].append({"label": state["label"], "asset": state["asset"], "address": text.strip(), "created_at": now()}); save_json(FILES["users"], users); user_state.pop(uid, None); send(chat_id, "Adres kaydedildi.", reply_keyboard()); return


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
        to_asset = data.split(":", 1)[1]
        state = user_state.get(uid, {})
        if state.get("flow") != "convert" or not state.get("from_asset"):
            send(chat_id, "Dönüşüm oturumu bulunamadı.")
            return
        state.update({"to_asset": to_asset, "step": "amount"})
        source = state["from_asset"]
        send(
            chat_id,
            f"Bakiye · {coin_fmt(balance(uid, source), source)}\n"
            f"Minimum · {coin_fmt(min_amount('convert', source), source)}\n\n"
            "Tutarı girin veya tüm bakiyeyi dönüştürün.",
            {"inline_keyboard": [
                [inline_button("Tüm Bakiyeyi Dönüştür", "convert_all")],
                [inline_button("İptal", "cancel")],
            ]},
        )
        return
    if data == "convert_all":
        state = user_state.get(uid, {})
        if state.get("flow") != "convert" or not state.get("from_asset") or not state.get("to_asset"):
            send(chat_id, "Dönüşüm oturumu bulunamadı.")
            return

        source = state["from_asset"]
        target = state["to_asset"]
        amount = balance(uid, source)
        if amount <= 0:
            send(chat_id, messages["no_balance"])
            return
        if amount < min_amount("convert", source):
            send(chat_id, f"Minimum · {coin_fmt(min_amount('convert', source), source)}")
            return

        source_rate = rate(source)
        target_rate = rate(target)
        if source_rate <= 0 or target_rate <= 0:
            send(chat_id, "Geçersiz kur nedeniyle dönüşüm yapılamıyor.")
            return

        fee_rate = fee_percent("convert", uid=uid)
        if fee_rate < 0 or fee_rate >= 100:
            send(chat_id, "Geçersiz komisyon oranı.")
            return

        tl_value = amount * source_rate
        gross = tl_value / target_rate
        fee = fee_amount(gross, fee_rate)
        net = gross - fee
        if net <= 0:
            send(chat_id, "Komisyon sonrası geçerli tutar oluşmadı.")
            return

        state.update({
            "amount": str(amount),
            "tl_value": str(tl_value),
            "gross_to": str(gross),
            "fee": str(fee),
            "net_amount": str(net),
        })
        state["preview"] = order_summary(
            "Takas Özeti",
            [
                ("Gönderilen", coin_fmt(amount, source)),
                ("Alınacak", coin_fmt(net, target)),
                ("Komisyon", coin_fmt(fee, target)),
            ],
            "Tüm kullanılabilir bakiye dönüştürülecektir. Kur son onayda yeniden hesaplanır.",
        )
        require_pin(uid, state)
        return
    if data == "second_confirm":
        state = user_state.get(uid, {})
        if not consume_confirmation(state):
            answer(cb_id, "Bu onay daha önce kullanıldı.")
            return
        try:
            if state.get("flow") == "withdraw":
                finalize_withdraw(uid, state)
            elif state.get("flow") == "convert":
                required = ("from_asset", "to_asset", "amount")
                if any(not state.get(key) for key in required):
                    raise ValueError("Dönüşüm bilgileri eksik. İşlemi yeniden başlatınız.")
                rid = atomic_convert(uid, state)
                user_state.pop(uid, None)
                send(chat_id, receipt_text(rid), reply_keyboard())
            else:
                raise ValueError("Onaylanacak işlem bulunamadı. İşlemi yeniden başlatınız.")
        except ValueError as exc:
            user_state.pop(uid, None)
            send(chat_id, str(exc), reply_keyboard())
        except Exception as exc:
            print("İŞLEM ONAY HATASI:", exc)
            user_state.pop(uid, None)
            send(chat_id, "İşlem tamamlanamadı. Lütfen işlemi yeniden başlatınız.", reply_keyboard())
        return
    if data == "favorite:add": user_state[uid] = {"flow": "favorite_add", "step": "asset"}; send(chat_id, "Kayıtlı adresin para birimini seçiniz.", asset_keyboard("favorite_asset", CRYPTO_ASSETS)); return
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
def home(): return "Nerlo Wallet aktif ✅"

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
        attempts.append(time.time()); time.sleep(min(2 ** len(attempts), 8) / 10); error = "Kullanıcı adı veya şifre hatalı"
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'><title>Nerlo Wallet Yönetim</title><style>
    :root{{--bg:#070a10;--panel:#0f141d;--panel2:#141b26;--line:#242d3a;--text:#f5f7fb;--muted:#8f9bab;--accent:#6ee7d8;--accent2:#7dd3fc;--danger:#fb7185}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(circle at 15% 10%,rgba(110,231,216,.12),transparent 30%),radial-gradient(circle at 90% 90%,rgba(125,211,252,.1),transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.login{{width:min(420px,100%);background:rgba(15,20,29,.94);border:1px solid var(--line);border-radius:24px;padding:28px;box-shadow:0 30px 90px rgba(0,0,0,.42);backdrop-filter:blur(18px)}}.brand{{display:flex;align-items:center;gap:12px;margin-bottom:26px}}.mark{{width:42px;height:42px;display:grid;place-items:center;border-radius:14px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#051116;font-weight:900;font-size:20px}}h1{{font-size:22px;margin:0}}p{{margin:5px 0 0;color:var(--muted);font-size:14px}}label{{display:block;font-size:12px;font-weight:700;color:#b8c2cf;margin:16px 0 7px}}input,button{{width:100%;height:46px;border-radius:13px;border:1px solid var(--line);font:inherit}}input{{background:#0a0f16;color:var(--text);padding:0 14px;outline:none}}input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(110,231,216,.1)}}button{{margin-top:18px;border:0;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#061116;font-weight:850;cursor:pointer}}.error{{min-height:20px;color:var(--danger);font-size:13px;margin-top:12px}}</style></head><body><form class='login' method='post'><div class='brand'><div class='mark'>N</div><div><h1>Nerlo Wallet</h1><p>Yönetim paneli</p></div></div><label>Kullanıcı adı</label><input name='username' autocomplete='username' required><label>Şifre</label><input type='password' autocomplete='current-password' name='password' required><button>Giriş Yap</button><div class='error'>{h(error)}</div></form></body></html>"""

@app.route("/logout")
def logout(): session.clear(); return redirect("/login")


REQUEST_TYPE_LABELS = {
    "deposit": "Bakiye yükleme",
    "withdraw": "Para çekme",
    "convert": "Kripto para dönüştürme",
}

STATUS_LABELS = {
    "pending": "Bekliyor",
    "processing": "İşleniyor",
    "completed": "Tamamlandı",
    "rejected": "Reddedildi",
}

ACCOUNT_STATUS_LABELS = {
    "active": "Açık",
    "frozen": "Dondurulmuş",
}

TIER_LABELS = {
    "Basic": "Temel",
    "Plus": "Artı",
    "Prime": "Öncelikli",
}

TRANSACTION_KIND_LABELS = {
    "withdraw_hold_available": "Çekim için kullanılabilir bakiyeden ayırma",
    "withdraw_hold_pending": "Çekimi bekleyen bakiyeye aktarma",
    "withdraw_pending_release": "Tamamlanan çekimi bekleyen bakiyeden düşme",
    "withdraw_pending_cancel": "İptal edilen çekimi bekleyen bakiyeden düşme",
    "withdraw_refund": "Reddedilen çekimi iade etme",
    "convert_out": "Dönüşümde verilen bakiye",
    "convert_in": "Dönüşümde alınan bakiye",
    "deposit_pending": "Yüklemeyi bekleyen bakiyeye ekleme",
    "deposit_pending_release": "Onaylanan yüklemeyi bekleyen bakiyeden düşme",
    "deposit_pending_cancel": "Reddedilen yüklemeyi bekleyen bakiyeden düşme",
    "deposit_approved": "Onaylanan yüklemeyi bakiyeye ekleme",
    "admin_adjustment": "Yönetici bakiye düzeltmesi",
}

BUCKET_LABELS = {
    "available": "Kullanılabilir bakiye",
    "pending": "Bekleyen bakiye",
}

ADMIN_ACTION_LABELS = {
    "settings": "Ayarları güncelleme",
    "process_request": "İşlemi işleme alma",
    "approve_request": "İşlemi tamamlama",
    "reject_request": "İşlemi reddetme",
    "adjust_balance": "Bakiye düzeltme",
    "update_user_profile": "Kullanıcı profilini güncelleme",
    "freeze_user": "Hesabı dondurma",
    "unfreeze_user": "Hesabı açma",
    "lock_withdraw": "Çekimi kilitleme",
    "unlock_withdraw": "Çekim kilidini açma",
    "broadcast": "Duyuru gönderme",
}

MESSAGE_LABELS = {
    "welcome": "Karşılama mesajı",
    "wallet_title": "Cüzdan başlığı",
    "deposit_menu": "Bakiye yükleme menüsü mesajı",
    "withdraw_menu": "Para çekme menüsü mesajı",
    "convert_menu": "Dönüştürme menüsü mesajı",
    "amount_question": "Tutar isteme mesajı",
    "pin_question": "İşlem PIN'i isteme mesajı",
    "pin_set_question": "İşlem PIN'i oluşturma mesajı",
    "pin_wrong": "Hatalı PIN mesajı",
    "pin_saved": "PIN kaydedildi mesajı",
    "pin_changed": "PIN değiştirildi mesajı",
    "insufficient_balance": "Yetersiz bakiye mesajı",
    "no_balance": "Bakiye bulunamadı mesajı",
    "request_created": "Talep oluşturuldu mesajı",
    "request_cancelled": "İşlem iptal edildi mesajı",
    "support": "Destek mesajı",
    "history_empty": "Boş işlem geçmişi mesajı",
    "iban_warning": "IBAN uyarısı",
    "maintenance": "Bakım mesajı",
    "frozen": "Dondurulmuş hesap mesajı",
    "withdraw_locked": "Kilitli çekim mesajı",
    "deposit_crypto_intro": "Kripto para yükleme uyarısı",
    "deposit_received": "Yükleme bildirimi alındı mesajı",
}

ICON_LABELS = {
    "wallet": "Cüzdan",
    "deposit": "Bakiye yükleme",
    "withdraw": "Para çekme",
    "convert": "Dönüştürme",
    "history": "İşlem geçmişi",
    "support": "Destek",
    "fees": "Komisyon",
    "info": "Bilgi",
    "swap": "Takas",
    "pending": "Bekleyen işlem",
    "processing": "İşlenen işlem",
    "security": "Güvenlik",
    "completed": "Tamamlanan işlem",
    "rejected": "Reddedilen işlem",
    "USDT": "USDT",
    "LTC": "LTC",
    "TRX": "TRX",
    "TL": "TL",
}


def status_label(value):
    return STATUS_LABELS.get(value, "Bilinmiyor")


def request_type_label(value):
    return REQUEST_TYPE_LABELS.get(value, "Diğer işlem")


def account_status_label(value):
    return ACCOUNT_STATUS_LABELS.get(value, "Bilinmiyor")


def username_label(value):
    text = str(value or "").strip()
    return "bilinmiyor" if not text or text.lower() == "unknown" else text


def tier_label(value):
    return TIER_LABELS.get(value, "Temel")


def transaction_kind_label(value):
    return TRANSACTION_KIND_LABELS.get(value, "Sistem işlemi")


def bucket_label(value):
    return BUCKET_LABELS.get(value, "Bilinmiyor")


def admin_action_label(value):
    return ADMIN_ACTION_LABELS.get(value, "Yönetim işlemi")


def localized_admin_detail(value):
    text = str(value or "")
    for source, target in TIER_LABELS.items():
        text = text.replace(source, target)
    return text


def setting_label(key):
    direct = {
        "bank_name": "Banka adı",
        "iban": "IBAN",
        "iban_owner": "IBAN hesap sahibi",
        "wallet_USDT": "USDT yatırma adresi",
        "wallet_TRX": "TRX yatırma adresi",
        "wallet_LTC": "LTC yatırma adresi",
        "maintenance_mode": "Bakım durumu",
        "maintenance_message": "Bakım mesajı",
        "announcement_active": "Duyuru durumu",
        "announcement_text": "Duyuru metni",
        "network_USDT": "USDT ağı",
        "network_TRX": "TRX ağı",
        "network_LTC": "LTC ağı",
    }
    if key in direct:
        return direct[key]
    match = re.fullmatch(r"rate_(USDT|LTC|TRX)_TL", key)
    if match:
        return f"{match.group(1)} / TL kuru"
    match = re.fullmatch(r"fee_(deposit|withdraw)_(TL|USDT|LTC|TRX)_percent", key)
    if match:
        operation = "yükleme" if match.group(1) == "deposit" else "çekim"
        return f"{match.group(2)} {operation} komisyonu (%)"
    if key == "fee_convert_percent":
        return "Dönüştürme komisyonu (%)"
    match = re.fullmatch(r"min_(deposit|withdraw|convert)_(TL|USDT|LTC|TRX)", key)
    if match:
        operation = {"deposit": "yükleme", "withdraw": "çekim", "convert": "dönüştürme"}[match.group(1)]
        return f"{match.group(2)} en düşük {operation} tutarı"
    match = re.fullmatch(r"daily_withdraw_limit_(TL|USDT|LTC|TRX)", key)
    if match:
        return f"{match.group(1)} günlük çekim sınırı"
    match = re.fullmatch(r"icon_(.+)", key)
    if match:
        return f"{ICON_LABELS.get(match.group(1), 'Sistem')} simge kimliği"
    return "Sistem ayarı"


def setting_field(key):
    label = h(setting_label(key))
    value = str(settings.get(key, ""))
    if key in ("maintenance_mode", "announcement_active"):
        return (
            f"<div class='field'><label>{label}</label><select name='{h(key)}'>"
            f"<option value='off' {'selected' if value != 'on' else ''}>Kapalı</option>"
            f"<option value='on' {'selected' if value == 'on' else ''}>Açık</option>"
            "</select></div>"
        )
    return f"<div class='field'><label>{label}</label><input name='{h(key)}' value='{h(value)}'></div>"


def message_field(key):
    return f"<div class='field'><label>{h(MESSAGE_LABELS.get(key, 'Bot mesajı'))}</label><textarea name='{h(key)}'>{h(messages.get(key, ''))}</textarea></div>"


def reserve_totals():
    totals = {a: Decimal("0") for a in ASSETS}; pending = {a: Decimal("0") for a in ASSETS}
    for u in users.values():
        for a in ASSETS: totals[a] += D(u.get("balances", {}).get(a, "0"))
    for r in requests_db.values():
        if r.get("type") == "withdraw" and r.get("status") in ("pending", "processing"): pending[r.get("asset")] += D(r.get("amount"))
    return totals, pending


def request_status_class(value):
    return {
        "pending": "waiting",
        "processing": "working",
        "completed": "done",
        "rejected": "declined",
    }.get(value, "neutral")


def request_search_matches(rid, r, query):
    if not query:
        return True
    uid = str(r.get("user_id", ""))
    username = username_label(users.get(uid, {}).get("username"))
    haystack = " ".join((str(rid), uid, username, str(r.get("asset", "")), request_type_label(r.get("type")))).lower()
    return query.lower() in haystack


def panel_request_card(rid, r):
    uid = str(r.get("user_id", ""))
    username = username_label(users.get(uid, {}).get("username"))
    status = r.get("status")
    detail_items = [
        ("Kullanıcı", f"@{username} · {uid}"),
        ("Oluşturuldu", r.get("created_at", "-")),
    ]
    if r.get("type") == "deposit":
        asset = r.get("asset")
        detail_items += [
            ("Tutar", fmt(r.get("amount"), asset)),
            ("Net", fmt(r.get("net_amount"), asset)),
        ]
        if asset == "TL":
            detail_items += [("Gönderen", r.get("sender_name", "-")), ("Açıklama", r.get("tx_note", "-"))]
        else:
            detail_items.append(("Ağ", r.get("network", "-")))
    elif r.get("type") == "withdraw":
        asset = r.get("asset")
        detail_items += [
            ("Tutar", fmt(r.get("amount"), asset)),
            ("Komisyon", fmt(r.get("fee"), asset)),
            ("Net", fmt(r.get("net_amount"), asset)),
            ("Hedef", r.get("iban") or r.get("address", "-")),
        ]
    elif r.get("type") == "convert":
        detail_items += [
            ("Gönderilen", fmt(r.get("from_amount"), r.get("from_asset"))),
            ("Alınan", fmt(r.get("net_to_amount"), r.get("to_asset"))),
        ]

    details = "".join(
        f"<div class='request-detail'><span>{h(label)}</span><b>{h(value)}</b></div>"
        for label, value in detail_items
    )
    actions = ""
    if status in ("pending", "processing"):
        process_button = "" if status == "processing" else "<button class='btn ghost' name='action' value='process_request'>İşleme Al</button>"
        actions = (
            f"<form method='post' class='request-actions'>"
            f"<input type='hidden' name='rid' value='{h(rid)}'>"
            f"<input type='hidden' name='return_to' value='/admin?view=requests'>"
            f"{process_button}"
            f"<button class='btn success' name='action' value='approve_request'>Tamamla</button>"
            f"<button class='btn danger' name='action' value='reject_request'>Reddet</button>"
            f"</form>"
        )
    return (
        f"<article class='request-item'>"
        f"<div class='request-title'><div><span class='eyebrow'>#{h(rid)}</span><h3>{h(request_type_label(r.get('type')))}</h3></div>"
        f"<span class='status {request_status_class(status)}'>{h(status_label(status))}</span></div>"
        f"<div class='request-details'>{details}</div>{actions}</article>"
    )


def render_request_list(query="", status_filter="all", type_filter="all"):
    items = []
    for rid, r in requests_db.items():
        if status_filter != "all" and r.get("status") != status_filter:
            continue
        if type_filter != "all" and r.get("type") != type_filter:
            continue
        if not request_search_matches(rid, r, query):
            continue
        items.append((rid, r))
    items.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
    return "".join(panel_request_card(rid, r) for rid, r in items[:100]) or "<div class='empty-state'>Filtreye uygun işlem talebi bulunamadı.</div>"


def user_balance_cards(uid, u):
    cards = []
    for asset in ASSETS:
        available = fmt(u.get("balances", {}).get(asset, "0"), asset)
        pending_value = fmt(u.get("pending_balances", {}).get(asset, "0"), asset)
        cards.append(
            f"<div class='mini-balance'><div><span>{h(asset)}</span><strong>{h(available)}</strong></div>"
            f"<small>Bekleyen: {h(pending_value)}</small></div>"
        )
    return "".join(cards)


def render_user_management(uid):
    uid = str(uid or "").strip()
    if not uid:
        return "<div class='empty-state user-empty'><b>Kullanıcı kimliği girin</b><span>Kullanıcının bakiyesi, profili, güvenliği ve son işlemleri burada açılır.</span></div>"
    if uid not in users:
        return f"<div class='empty-state error-state'><b>Kullanıcı bulunamadı</b><span>{h(uid)} kimliği kayıtlı değil.</span></div>"

    u = users[uid]
    return_to = f"/admin?view=users&manage_user_id={h(uid)}"
    reqs = sorted(
        [r for r in requests_db.values() if r.get("user_id") == uid],
        key=lambda item: item.get("created_at", ""), reverse=True,
    )[:12]
    txs = sorted(
        [t for t in transactions.values() if t.get("user_id") == uid],
        key=lambda item: item.get("created_at", ""), reverse=True,
    )[:12]
    request_rows = "".join(
        f"<tr><td>#{h(r.get('id'))}</td><td>{h(request_type_label(r.get('type')))}</td><td><span class='status {request_status_class(r.get('status'))}'>{h(status_label(r.get('status')))}</span></td><td>{h(r.get('created_at'))}</td></tr>"
        for r in reqs
    ) or "<tr><td colspan='4' class='muted-cell'>İşlem kaydı yok.</td></tr>"
    transaction_rows = "".join(
        f"<tr><td>{h(t.get('created_at'))}</td><td>{h(transaction_kind_label(t.get('kind')))}</td><td>{h(fmt(t.get('amount'), t.get('asset')))}</td><td>{h(fmt(t.get('available_after', '0'), t.get('asset')))}</td><td>{h(t.get('note') or '-')}</td></tr>"
        for t in txs
    ) or "<tr><td colspan='5' class='muted-cell'>Bakiye hareketi yok.</td></tr>"

    security_forms = []
    security_actions = [
        ("freeze_user", "Hesabı Dondur", "danger"),
        ("unfreeze_user", "Hesabı Aç", "ghost"),
        ("lock_withdraw", "Çekimi Kilitle", "danger"),
        ("unlock_withdraw", "Çekimi Aç", "ghost"),
    ]
    for action, label, style in security_actions:
        security_forms.append(
            f"<form method='post'><input type='hidden' name='user_id' value='{h(uid)}'>"
            f"<input type='hidden' name='return_to' value='{return_to}'>"
            f"<button class='btn {style}' name='action' value='{action}'>{label}</button></form>"
        )

    return f"""
    <div class='user-profile-head'>
      <div><span class='eyebrow'>KULLANICI</span><h2>@{h(username_label(u.get('username')))}</h2><p>{h(uid)} · Son görülme {h(u.get('last_seen') or '-')}</p></div>
      <div class='profile-badges'><span class='pill'>{h(tier_label(u.get('tier', 'Basic')))}</span><span class='pill {'danger-pill' if u.get('status') == 'frozen' else ''}'>{h(account_status_label(u.get('status')))}</span></div>
    </div>
    <div class='mini-balance-grid'>{user_balance_cards(uid, u)}</div>
    <div class='user-workspace'>
      <section class='panel-card compact-card'>
        <div class='section-head'><div><span class='eyebrow'>HIZLI İŞLEM</span><h3>Bakiye Ekle / Düş</h3></div></div>
        <form method='post' class='form-grid balance-form'>
          <input type='hidden' name='action' value='adjust_balance'><input type='hidden' name='user_id' value='{h(uid)}'><input type='hidden' name='return_to' value='{return_to}'>
          <div><label>İşlem</label><select name='direction'><option value='add'>Bakiye ekle</option><option value='subtract'>Bakiyeden düş</option></select></div>
          <div><label>Varlık</label><select name='asset'>{''.join(f'<option value="{a}">{a}</option>' for a in ASSETS)}</select></div>
          <div><label>Tutar</label><input name='amount' inputmode='decimal' placeholder='0.00' required></div>
          <div class='wide'><label>Açıklama</label><input name='note' placeholder='İşlem nedeni' required></div>
          <div class='submit-cell'><button class='btn primary'>Bakiyeyi Güncelle</button></div>
        </form>
      </section>
      <section class='panel-card compact-card'>
        <div class='section-head'><div><span class='eyebrow'>PROFİL</span><h3>Kullanıcı Ayarları</h3></div></div>
        <form method='post' class='form-grid'>
          <input type='hidden' name='action' value='update_user_profile'><input type='hidden' name='user_id' value='{h(uid)}'><input type='hidden' name='return_to' value='{return_to}'>
          <div><label>Hesap seviyesi</label><select name='tier'><option value='Basic' {'selected' if u.get('tier') == 'Basic' else ''}>Temel</option><option value='Plus' {'selected' if u.get('tier') == 'Plus' else ''}>Artı</option><option value='Prime' {'selected' if u.get('tier') == 'Prime' else ''}>Öncelikli</option></select></div>
          <div><label>Özel komisyon %</label><input name='custom_fee_percent' value='{h(u.get('custom_fee_percent', ''))}' placeholder='Genel oran'></div>
          <div><label>Özel TL günlük limit</label><input name='custom_daily_limit_TL' value='{h(u.get('custom_daily_limit_TL', ''))}' placeholder='Genel limit'></div>
          <div class='wide'><label>Yönetici notu</label><input name='note' value='{h(u.get('note', ''))}' placeholder='İç not'></div>
          <div class='submit-cell'><button class='btn primary'>Profili Kaydet</button></div>
        </form>
      </section>
    </div>
    <section class='panel-card compact-card'>
      <div class='section-head'><div><span class='eyebrow'>GÜVENLİK</span><h3>Hesap Kontrolleri</h3></div><p>Çekim: {'Kilitli' if u.get('withdraw_locked') else 'Açık'} · PIN: {'Aktif' if u.get('pin_hash') else 'Ayarlanmamış'}</p></div>
      <div class='security-actions'>{''.join(security_forms)}</div>
    </section>
    <div class='user-workspace history-grid'>
      <section class='panel-card compact-card'><div class='section-head'><div><span class='eyebrow'>SON KAYITLAR</span><h3>İşlem Talepleri</h3></div></div><div class='table-wrap'><table><thead><tr><th>No</th><th>Tür</th><th>Durum</th><th>Tarih</th></tr></thead><tbody>{request_rows}</tbody></table></div></section>
      <section class='panel-card compact-card'><div class='section-head'><div><span class='eyebrow'>HAREKETLER</span><h3>Bakiye Defteri</h3></div></div><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Tutar</th><th>Son bakiye</th><th>Not</th></tr></thead><tbody>{transaction_rows}</tbody></table></div></section>
    </div>
    """


@app.route("/admin/requests-fragment")
def admin_requests_fragment():
    if not logged_in():
        return "", 401
    return render_request_list(
        request.args.get("rq", "").strip(),
        request.args.get("status", "all"),
        request.args.get("type", "all"),
    )


@app.route("/admin/user-fragment")
def admin_user_fragment():
    if not logged_in():
        return "", 401
    return render_user_management(request.args.get("uid", ""))


def safe_admin_return(default="/admin"):
    target = str(request.form.get("return_to", default) or default)
    if not target.startswith("/admin") or target.startswith("//"):
        return default
    return target


def set_admin_notice(message, kind="success"):
    session["admin_notice"] = {"message": str(message), "kind": kind}


EDITABLE_SETTING_KEYS = [key for key in DEFAULT_SETTINGS if not key.startswith("icon_")]


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in(): return redirect("/login")
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "settings":
            for key in EDITABLE_SETTING_KEYS:
                settings[key] = request.form.get(key, settings.get(key, ""))
            for key in DEFAULT_MESSAGES:
                messages[key] = request.form.get(key, messages.get(key, ""))
            save_json(FILES["settings"], settings); save_json(FILES["messages"], messages)
            add_admin_log("settings", "Ayarlar güncellendi")
            set_admin_notice("Ayarlar kaydedildi.")
        elif action in ("process_request", "approve_request", "reject_request"):
            rid = request.form.get("rid", ""); r = requests_db.get(rid)
            if r:
                uid = r["user_id"]
                if action == "process_request" and r.get("status") == "pending":
                    r["status"] = "processing"
                    set_admin_notice(f"#{rid} işleme alındı.")
                elif action == "approve_request" and r.get("status") in ("pending", "processing"):
                    if r["type"] == "deposit":
                        change_pending(uid, r["asset"], -D(r["net_amount"]), "deposit_pending_release", rid)
                        change_balance(uid, r["asset"], r["net_amount"], "deposit_approved", rid)
                    elif r["type"] == "withdraw":
                        change_pending(uid, r["asset"], -D(r["amount"]), "withdraw_pending_release", rid)
                    r["status"] = "completed"; r["completed_at"] = now()
                    send(uid, "İşleminiz tamamlandı.\n\n" + receipt_text(rid), reply_keyboard())
                    set_admin_notice(f"#{rid} tamamlandı.")
                elif action == "reject_request" and r.get("status") in ("pending", "processing"):
                    if r["type"] == "withdraw":
                        change_pending(uid, r["asset"], -D(r["amount"]), "withdraw_pending_cancel", rid)
                        change_balance(uid, r["asset"], r["amount"], "withdraw_refund", rid)
                    elif r["type"] == "deposit":
                        change_pending(uid, r["asset"], -D(r["net_amount"]), "deposit_pending_cancel", rid)
                    r["status"] = "rejected"; r["rejected_at"] = now()
                    send(uid, f"İşleminiz reddedildi.\n\nİşlem No: #{rid}", reply_keyboard())
                    set_admin_notice(f"#{rid} reddedildi.")
                else:
                    set_admin_notice("İşlem durumu değiştirilemedi.", "error")
                r["updated_at"] = now(); save_json(FILES["requests"], requests_db); add_admin_log(action, f"#{rid}", uid)
            else:
                set_admin_notice("İşlem talebi bulunamadı.", "error")
        elif action == "adjust_balance":
            uid = request.form.get("user_id", "").strip()
            asset = request.form.get("asset", "").strip()
            note = request.form.get("note", "").strip()
            raw_amount = D(request.form.get("amount", "0"))
            direction = request.form.get("direction", "add")
            amount = abs(raw_amount)
            if direction == "subtract":
                amount = -amount
            if uid not in users:
                set_admin_notice("Kullanıcı bulunamadı.", "error")
            elif asset not in ASSETS or amount == 0:
                set_admin_notice("Geçerli bir varlık ve tutar girin.", "error")
            elif not note:
                set_admin_notice("Bakiye işlemi için açıklama zorunludur.", "error")
            else:
                try:
                    change_balance(uid, asset, amount, "admin_adjustment", "", note)
                    add_admin_log("adjust_balance", f"{asset} {amount}: {note}", uid)
                    set_admin_notice(f"{uid} kullanıcısının {asset} bakiyesi güncellendi.")
                except ValueError as exc:
                    set_admin_notice(str(exc), "error")
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
                add_admin_log("update_user_profile", f"Seviye: {tier_label(tier)}", uid)
                set_admin_notice("Kullanıcı profili kaydedildi.")
            else:
                set_admin_notice("Kullanıcı bulunamadı.", "error")
        elif action in ("freeze_user", "unfreeze_user", "lock_withdraw", "unlock_withdraw"):
            uid = request.form.get("user_id", "")
            if uid in users:
                if action == "freeze_user": users[uid]["status"] = "frozen"
                elif action == "unfreeze_user": users[uid]["status"] = "active"
                elif action == "lock_withdraw": users[uid]["withdraw_locked"] = True
                else: users[uid]["withdraw_locked"] = False
                save_json(FILES["users"], users); add_admin_log(action, "Kullanıcı güvenlik durumu değiştirildi", uid)
                set_admin_notice("Kullanıcı güvenlik durumu güncellendi.")
            else:
                set_admin_notice("Kullanıcı bulunamadı.", "error")
        elif action == "broadcast":
            announcement = request.form.get("announcement_text", "").strip()
            if announcement:
                count = 0
                for uid, u in users.items():
                    if u.get("notifications", {}).get("announcements", True):
                        send(uid, "📢 " + announcement, reply_keyboard()); count += 1
                add_admin_log("broadcast", f"{count} kullanıcıya gönderildi")
                set_admin_notice(f"Duyuru {count} kullanıcıya gönderildi.")
            else:
                set_admin_notice("Duyuru metni boş olamaz.", "error")
        return redirect(safe_admin_return())

    allowed_views = {"dashboard", "requests", "users", "broadcast", "settings", "logs"}
    active_view = request.args.get("view", "dashboard")
    if active_view not in allowed_views:
        active_view = "dashboard"
    manage_user_id = request.args.get("manage_user_id", "").strip()
    request_query = request.args.get("rq", "").strip()
    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("type", "all")
    totals, pending = reserve_totals()

    asset_metrics = "".join(
        f"<div class='wallet-metric'><div class='asset-dot'>{h(a[0])}</div><div><span>{h(a)} toplam bakiye</span><strong>{h(fmt(totals[a], a))}</strong><small>Bekleyen çekim: {h(fmt(pending[a], a))}</small></div></div>"
        for a in ASSETS
    )
    pending_count = sum(1 for r in requests_db.values() if r.get("status") in ("pending", "processing"))
    completed_today = sum(1 for r in requests_db.values() if r.get("status") == "completed" and str(r.get("updated_at", r.get("created_at", ""))).startswith(today()))
    recent_users = sorted(users.items(), key=lambda item: item[1].get("last_seen", ""), reverse=True)[:25]
    user_rows = "".join(
        f"<tr><td><code>{h(uid)}</code></td><td>@{h(username_label(u.get('username')))}</td><td>{h(fmt(u.get('balances', {}).get('TL', '0'), 'TL'))}</td><td>{h(fmt(u.get('balances', {}).get('USDT', '0'), 'USDT'))}</td><td>{h(fmt(u.get('balances', {}).get('LTC', '0'), 'LTC'))}</td><td>{h(fmt(u.get('balances', {}).get('TRX', '0'), 'TRX'))}</td><td>{h(account_status_label(u.get('status')))}</td><td>{h(u.get('last_seen') or '-')}</td></tr>"
        for uid, u in recent_users
    ) or "<tr><td colspan='8' class='muted-cell'>Henüz kullanıcı yok.</td></tr>"

    settings_groups = [
        ("rates", "Kur Yönetimi", [k for k in EDITABLE_SETTING_KEYS if k.startswith("rate_")]),
        ("fees", "Komisyonlar", [k for k in EDITABLE_SETTING_KEYS if k.startswith("fee_")]),
        ("limits", "Limitler", [k for k in EDITABLE_SETTING_KEYS if k.startswith("min_") or k.startswith("daily_")]),
        ("wallets", "Cüzdan ve Ağ", [k for k in EDITABLE_SETTING_KEYS if k.startswith("wallet_") or k.startswith("network_") or k in ("bank_name", "iban", "iban_owner")]),
        ("system", "Sistem ve Duyuru", [k for k in EDITABLE_SETTING_KEYS if k.startswith("maintenance_") or k.startswith("announcement_")]),
        ("messages", "Bot Mesajları", list(DEFAULT_MESSAGES.keys())),
    ]
    settings_tabs = "".join(f"<button type='button' class='setting-tab {'active' if i == 0 else ''}' data-setting-target='{h(slug)}'>{h(title)}</button>" for i, (slug, title, keys) in enumerate(settings_groups))
    settings_panes = []
    for index, (slug, title, keys) in enumerate(settings_groups):
        if slug == "messages":
            fields = "".join(message_field(key) for key in keys)
        else:
            fields = "".join(setting_field(key) for key in keys)
        settings_panes.append(f"<div class='setting-pane {'active' if index == 0 else ''}' data-setting-pane='{h(slug)}'><div class='section-head'><div><span class='eyebrow'>AYARLAR</span><h3>{h(title)}</h3></div></div><div class='settings-grid'>{fields}</div></div>")

    logs = "".join(
        f"<tr><td>{h(item.get('created_at'))}</td><td>{h(admin_action_label(item.get('action')))}</td><td>{h(item.get('user_id') or '-')}</td><td>{h(localized_admin_detail(item.get('details')))}</td></tr>"
        for item in reversed(admin_logs[-120:])
    ) or "<tr><td colspan='4' class='muted-cell'>Yönetim kaydı yok.</td></tr>"
    notice = session.pop("admin_notice", None)
    notice_html = ""
    if notice:
        notice_html = f"<div class='toast {'toast-error' if notice.get('kind') == 'error' else ''}' id='admin-toast'>{h(notice.get('message'))}</div>"

    nav_items = [
        ("dashboard", "Genel Bakış", "01"),
        ("requests", "İşlem Talepleri", "02"),
        ("users", "Kullanıcı Yönetimi", "03"),
        ("broadcast", "Duyurular", "04"),
        ("settings", "Ayarlar", "05"),
        ("logs", "Yönetim Kayıtları", "06"),
    ]
    nav_html = "".join(
        f"<button class='nav-item {'active' if slug == active_view else ''}' data-view-target='{slug}'><span>{number}</span>{label}</button>"
        for slug, label, number in nav_items
    )

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'><title>Nerlo Wallet Yönetim</title><style>
    :root{{--bg:#090c12;--sidebar:#0b0f16;--surface:#10151e;--surface-2:#141b25;--surface-3:#0c1118;--line:#222b38;--line-soft:#1a2230;--text:#f4f7fb;--muted:#8c98a8;--muted-2:#667386;--accent:#68e0d2;--accent-2:#7cc7ff;--success:#59d99b;--warning:#f6c96b;--danger:#ff7489;--radius:18px}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 85% -10%,rgba(104,224,210,.08),transparent 32%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}}button,input,select,textarea{{font:inherit}}button{{cursor:pointer}}.app-shell{{min-height:100vh;display:grid;grid-template-columns:238px minmax(0,1fr)}}.sidebar{{position:sticky;top:0;height:100vh;background:rgba(11,15,22,.96);border-right:1px solid var(--line-soft);padding:20px 14px;display:flex;flex-direction:column;backdrop-filter:blur(18px)}}.brand{{display:flex;align-items:center;gap:11px;padding:6px 8px 22px}}.brand-mark{{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116;font-weight:950;font-size:18px;box-shadow:0 10px 30px rgba(104,224,210,.14)}}.brand strong{{display:block;font-size:15px}}.brand small{{display:block;color:var(--muted);margin-top:2px;font-size:11px}}.nav{{display:grid;gap:4px}}.nav-item{{width:100%;border:0;background:transparent;color:#aeb8c6;display:flex;align-items:center;gap:10px;padding:10px 11px;border-radius:11px;text-align:left;font-weight:680}}.nav-item span{{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;background:#111923;color:#66778c;font-size:10px}}.nav-item:hover{{background:#111822;color:#fff}}.nav-item.active{{background:#151e29;color:#fff}}.nav-item.active span{{background:rgba(104,224,210,.13);color:var(--accent)}}.sidebar-foot{{margin-top:auto;padding:14px 8px 2px;border-top:1px solid var(--line-soft)}}.version{{display:block;color:var(--muted-2);font-size:10px;margin-bottom:10px;letter-spacing:.06em}}.logout{{color:#aeb8c6;text-decoration:none;font-size:12px}}.main{{min-width:0;padding:22px clamp(16px,3vw,36px) 40px}}.topbar{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}}.topbar h1{{font-size:22px;margin:0;letter-spacing:-.03em}}.topbar p{{margin:5px 0 0;color:var(--muted);font-size:12px}}.top-pill{{padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--surface-3);font-size:11px}}.page-view{{display:none}}.page-view.active{{display:block}}.section-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px}}.section-head h2,.section-head h3{{margin:2px 0 0;letter-spacing:-.025em}}.section-head h2{{font-size:18px}}.section-head h3{{font-size:15px}}.section-head p{{margin:3px 0 0;color:var(--muted);font-size:12px}}.eyebrow{{display:block;color:var(--muted-2);font-size:9px;font-weight:850;letter-spacing:.13em}}.panel-card{{background:rgba(16,21,30,.92);border:1px solid var(--line);border-radius:var(--radius);padding:17px;box-shadow:0 16px 50px rgba(0,0,0,.14)}}.compact-card{{padding:15px}}.dashboard-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.wallet-metric{{min-height:108px;background:linear-gradient(145deg,#111823,#0d131c);border:1px solid var(--line);border-radius:16px;padding:14px;display:flex;gap:11px;align-items:flex-start}}.asset-dot{{width:30px;height:30px;flex:0 0 auto;border-radius:10px;background:rgba(104,224,210,.1);color:var(--accent);display:grid;place-items:center;font-size:11px;font-weight:900}}.wallet-metric span{{display:block;color:var(--muted);font-size:10px}}.wallet-metric strong{{display:block;font-size:18px;margin:5px 0 3px;letter-spacing:-.03em;white-space:nowrap}}.wallet-metric small{{color:var(--muted-2);font-size:10px}}.summary-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px}}.summary-card{{padding:13px 14px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3)}}.summary-card span{{color:var(--muted);font-size:10px}}.summary-card strong{{display:block;font-size:20px;margin-top:4px}}.toolbar{{display:grid;grid-template-columns:minmax(220px,2fr) repeat(2,minmax(150px,1fr)) auto;gap:9px;margin-bottom:13px}}input,select,textarea{{width:100%;border:1px solid var(--line);background:#0b1017;color:var(--text);border-radius:11px;min-height:41px;padding:9px 11px;outline:none}}input:focus,select:focus,textarea:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(104,224,210,.08)}}textarea{{min-height:110px;resize:vertical}}label{{display:block;color:#aab5c4;font-size:10px;font-weight:760;margin:0 0 6px}}.btn{{border:1px solid transparent;min-height:38px;border-radius:10px;padding:8px 12px;font-weight:800;background:#192431;color:#dfe8f3}}.btn.primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116}}.btn.ghost{{background:#111923;border-color:var(--line);color:#c9d3df}}.btn.success{{background:rgba(89,217,155,.13);border-color:rgba(89,217,155,.23);color:#88ebba}}.btn.danger{{background:rgba(255,116,137,.12);border-color:rgba(255,116,137,.22);color:#ff96a6}}.request-list{{display:grid;gap:9px}}.request-item{{background:var(--surface-3);border:1px solid var(--line);border-radius:15px;padding:13px}}.request-title{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.request-title h3{{font-size:14px;margin:3px 0 0}}.request-details{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:11px}}.request-detail{{min-width:0;background:#101721;border:1px solid var(--line-soft);border-radius:10px;padding:8px 9px}}.request-detail span{{display:block;color:var(--muted-2);font-size:9px;margin-bottom:4px}}.request-detail b{{display:block;font-size:11px;overflow-wrap:anywhere}}.request-actions{{display:flex;justify-content:flex-end;gap:7px;margin-top:10px}}.request-actions .btn{{width:auto;min-height:34px;font-size:11px}}.status{{display:inline-flex;align-items:center;justify-content:center;min-height:25px;padding:4px 8px;border-radius:999px;font-size:9px;font-weight:850;white-space:nowrap}}.status.waiting{{background:rgba(246,201,107,.12);color:var(--warning)}}.status.working{{background:rgba(124,199,255,.12);color:var(--accent-2)}}.status.done{{background:rgba(89,217,155,.12);color:var(--success)}}.status.declined{{background:rgba(255,116,137,.12);color:var(--danger)}}.empty-state{{min-height:140px;border:1px dashed #2a3442;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:var(--muted);gap:5px;padding:20px}}.empty-state b{{color:#dbe4ee}}.error-state{{border-color:rgba(255,116,137,.3)}}.lookup-bar{{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:9px;margin-bottom:12px}}.lookup-bar .btn{{min-width:130px}}.user-profile-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin:2px 0 13px}}.user-profile-head h2{{font-size:19px;margin:3px 0}}.user-profile-head p{{margin:0;color:var(--muted);font-size:11px}}.profile-badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.pill{{padding:6px 9px;border-radius:999px;background:#151e29;border:1px solid var(--line);color:#cbd5e1;font-size:9px;font-weight:800}}.danger-pill{{color:var(--danger);background:rgba(255,116,137,.08)}}.mini-balance-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:10px}}.mini-balance{{background:#0d141d;border:1px solid var(--line);border-radius:13px;padding:11px}}.mini-balance div{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.mini-balance span{{font-size:10px;color:var(--muted)}}.mini-balance strong{{font-size:13px;white-space:nowrap}}.mini-balance small{{display:block;color:var(--muted-2);font-size:9px;margin-top:7px}}.user-workspace{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}.history-grid{{align-items:start}}.form-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-items:end}}.form-grid .wide{{grid-column:span 2}}.submit-cell{{display:flex;align-items:flex-end}}.submit-cell .btn{{width:100%}}.balance-form{{grid-template-columns:repeat(3,minmax(0,1fr))}}.security-actions{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.security-actions .btn{{width:100%}}.table-wrap{{overflow:auto;border:1px solid var(--line-soft);border-radius:12px}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line-soft);font-size:10px;white-space:nowrap}}th{{color:var(--muted-2);font-size:9px;letter-spacing:.04em;background:#0c121a}}td{{color:#cbd5df}}tbody tr:last-child td{{border-bottom:0}}code{{color:#c8d5e4;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px}}.muted-cell{{text-align:center;color:var(--muted)}}.broadcast-grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:10px}}.broadcast-note{{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3);color:var(--muted);font-size:12px;line-height:1.55}}.settings-nav{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}.setting-tab{{border:1px solid var(--line);background:#0c1219;color:#95a2b2;padding:8px 10px;border-radius:9px;font-size:10px;font-weight:800}}.setting-tab.active{{background:#17222d;color:var(--accent);border-color:#29404a}}.setting-pane{{display:none}}.setting-pane.active{{display:block}}.settings-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}}.field{{min-width:0}}.settings-grid textarea{{min-height:90px}}.save-bar{{display:flex;justify-content:flex-end;margin-top:13px}}.save-bar .btn{{min-width:180px}}.toast{{position:fixed;right:22px;top:18px;z-index:20;max-width:min(360px,calc(100vw - 32px));padding:11px 14px;border:1px solid rgba(89,217,155,.25);background:#10231c;color:#9aebc0;border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.35);font-size:12px}}.toast-error{{background:#28131a;color:#ff9bad;border-color:rgba(255,116,137,.28)}}@media(max-width:1180px){{.dashboard-grid,.mini-balance-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.request-details{{grid-template-columns:repeat(2,minmax(0,1fr))}}.settings-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:880px){{.app-shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;padding:12px}}.brand{{padding-bottom:12px}}.nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.nav-item{{justify-content:center;font-size:11px}}.sidebar-foot{{display:none}}.main{{padding-top:14px}}.user-workspace,.broadcast-grid{{grid-template-columns:1fr}}.toolbar{{grid-template-columns:1fr 1fr}}.toolbar input{{grid-column:1/-1}}.security-actions{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{.topbar{{align-items:flex-start}}.top-pill{{display:none}}.dashboard-grid,.summary-grid,.mini-balance-grid{{grid-template-columns:1fr}}.nav{{grid-template-columns:repeat(2,minmax(0,1fr))}}.nav-item span{{display:none}}.toolbar,.lookup-bar,.settings-grid,.form-grid,.balance-form{{grid-template-columns:1fr}}.form-grid .wide{{grid-column:auto}}.request-details{{grid-template-columns:1fr}}.request-actions{{display:grid;grid-template-columns:1fr}}.request-actions .btn{{width:100%}}.user-profile-head{{display:block}}.profile-badges{{justify-content:flex-start;margin-top:10px}}.security-actions{{grid-template-columns:1fr}}}}
    </style></head><body>{notice_html}<div class='app-shell'><aside class='sidebar'><div class='brand'><div class='brand-mark'>N</div><div><strong>Nerlo Wallet</strong><small>Yönetim Merkezi</small></div></div><nav class='nav'>{nav_html}</nav><div class='sidebar-foot'><span class='version'>NERLO-PANEL-2026.06.24-R1</span><a class='logout' href='/logout'>Güvenli çıkış</a></div></aside><main class='main'><header class='topbar'><div><h1>Kontrol Merkezi</h1><p>Kullanıcı, bakiye ve işlem operasyonları</p></div><span class='top-pill'>{h(now())}</span></header>

    <section class='page-view {'active' if active_view == 'dashboard' else ''}' data-view='dashboard'><div class='section-head'><div><span class='eyebrow'>GENEL BAKIŞ</span><h2>Cüzdan Özeti</h2><p>Tüm kullanıcı bakiyelerinin kompakt görünümü</p></div></div><div class='dashboard-grid'>{asset_metrics}</div><div class='summary-grid'><div class='summary-card'><span>Toplam kullanıcı</span><strong>{len(users)}</strong></div><div class='summary-card'><span>Bekleyen / işlenen talep</span><strong>{pending_count}</strong></div><div class='summary-card'><span>Bugün tamamlanan</span><strong>{completed_today}</strong></div></div></section>

    <section class='page-view {'active' if active_view == 'requests' else ''}' data-view='requests'><div class='section-head'><div><span class='eyebrow'>OPERASYON</span><h2>İşlem Talepleri</h2><p>Yükleme, çekim ve dönüşüm taleplerini tek ekrandan yönetin</p></div></div><div class='panel-card'><form id='request-filter' class='toolbar'><input name='rq' value='{h(request_query)}' placeholder='İşlem no, kullanıcı ID veya kullanıcı adı'><select name='status'><option value='all'>Tüm durumlar</option>{''.join(f"<option value='{s}' {'selected' if status_filter == s else ''}>{status_label(s)}</option>" for s in ['pending','processing','completed','rejected'])}</select><select name='type'><option value='all'>Tüm işlem türleri</option>{''.join(f"<option value='{t}' {'selected' if type_filter == t else ''}>{request_type_label(t)}</option>" for t in ['deposit','withdraw','convert'])}</select><button class='btn primary'>Filtrele</button></form><div id='request-list' class='request-list'>{render_request_list(request_query, status_filter, type_filter)}</div></div></section>

    <section class='page-view {'active' if active_view == 'users' else ''}' data-view='users'><div class='section-head'><div><span class='eyebrow'>KULLANICI YÖNETİMİ</span><h2>ID ile Kullanıcı Aç</h2><p>Kullanıcı satırlarına tıklamadan doğrudan kimlik ile yönetin</p></div></div><div class='panel-card'><form id='user-lookup' class='lookup-bar'><input id='manage-user-id' name='uid' value='{h(manage_user_id)}' inputmode='numeric' placeholder='Telegram kullanıcı ID'><button class='btn primary'>Kullanıcıyı Getir</button></form><div id='user-management-result'>{render_user_management(manage_user_id)}</div></div><div class='panel-card' style='margin-top:10px'><div class='section-head'><div><span class='eyebrow'>SON KULLANICILAR</span><h3>Hızlı Referans</h3></div><p>ID değerleri bağlantı değildir</p></div><div class='table-wrap'><table><thead><tr><th>Kullanıcı ID</th><th>Kullanıcı adı</th><th>TL</th><th>USDT</th><th>LTC</th><th>TRX</th><th>Hesap</th><th>Son görülme</th></tr></thead><tbody>{user_rows}</tbody></table></div></div></section>

    <section class='page-view {'active' if active_view == 'broadcast' else ''}' data-view='broadcast'><div class='section-head'><div><span class='eyebrow'>İLETİŞİM</span><h2>Duyuru Gönder</h2><p>Bildirimleri açık kullanıcılara toplu mesaj gönderin</p></div></div><div class='broadcast-grid'><form method='post' class='panel-card'><input type='hidden' name='action' value='broadcast'><input type='hidden' name='return_to' value='/admin?view=broadcast'><label>Duyuru metni</label><textarea name='announcement_text' placeholder='Kullanıcılara gönderilecek mesajı yazın' required></textarea><button class='btn primary' style='width:100%;margin-top:10px'>Duyuruyu Gönder</button></form><div class='broadcast-note'><b style='color:#dce5ef'>Gönderim bilgisi</b><br><br>Duyuru yalnızca duyuru bildirimleri açık olan kullanıcılara iletilir. Gönderim sonucu yönetim kayıtlarına eklenir.</div></div></section>

    <section class='page-view {'active' if active_view == 'settings' else ''}' data-view='settings'><div class='section-head'><div><span class='eyebrow'>SİSTEM</span><h2>Ayar Yönetimi</h2><p>Kur, limit, cüzdan, sistem ve bot mesajlarını yönetin</p></div></div><form method='post' class='panel-card'><input type='hidden' name='action' value='settings'><input type='hidden' name='return_to' value='/admin?view=settings'><div class='settings-nav'>{settings_tabs}</div>{''.join(settings_panes)}<div class='save-bar'><button class='btn primary'>Tüm Ayarları Kaydet</button></div></form></section>

    <section class='page-view {'active' if active_view == 'logs' else ''}' data-view='logs'><div class='section-head'><div><span class='eyebrow'>DENETİM</span><h2>Yönetim Kayıtları</h2><p>Son 120 yönetici işlemi</p></div></div><div class='panel-card'><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Kullanıcı</th><th>Detay</th></tr></thead><tbody>{logs}</tbody></table></div></div></section>
    </main></div><script>
    const views=[...document.querySelectorAll('[data-view]')];
    const navItems=[...document.querySelectorAll('[data-view-target]')];
    function openView(name,updateUrl=true){{
      views.forEach(view=>view.classList.toggle('active',view.dataset.view===name));
      navItems.forEach(item=>item.classList.toggle('active',item.dataset.viewTarget===name));
      if(updateUrl){{const url=new URL(location.href);url.searchParams.set('view',name);history.replaceState(null,'',url);}}
      window.scrollTo({{top:0,behavior:'smooth'}});
    }}
    navItems.forEach(item=>item.addEventListener('click',()=>openView(item.dataset.viewTarget)));

    const filterForm=document.getElementById('request-filter');
    async function refreshRequests(){{
      if(!filterForm)return;
      const params=new URLSearchParams(new FormData(filterForm));
      try{{const response=await fetch('/admin/requests-fragment?'+params.toString(),{{cache:'no-store'}});if(response.ok)document.getElementById('request-list').innerHTML=await response.text();}}catch(error){{console.log('Talep yenileme hatası',error);}}
    }}
    if(filterForm)filterForm.addEventListener('submit',event=>{{event.preventDefault();refreshRequests();}});
    setInterval(()=>{{if(document.querySelector('[data-view="requests"]').classList.contains('active'))refreshRequests();}},20000);
    document.addEventListener('visibilitychange',()=>{{if(!document.hidden&&document.querySelector('[data-view="requests"]').classList.contains('active'))refreshRequests();}});

    const userLookup=document.getElementById('user-lookup');
    if(userLookup)userLookup.addEventListener('submit',async event=>{{
      event.preventDefault();
      const uid=document.getElementById('manage-user-id').value.trim();
      const result=document.getElementById('user-management-result');
      result.innerHTML="<div class='empty-state'>Kullanıcı yükleniyor…</div>";
      try{{
        const response=await fetch('/admin/user-fragment?uid='+encodeURIComponent(uid),{{cache:'no-store'}});
        result.innerHTML=response.ok?await response.text():"<div class='empty-state error-state'>Kullanıcı bilgisi alınamadı.</div>";
        const url=new URL(location.href);url.searchParams.set('view','users');if(uid)url.searchParams.set('manage_user_id',uid);else url.searchParams.delete('manage_user_id');history.replaceState(null,'',url);
      }}catch(error){{result.innerHTML="<div class='empty-state error-state'>Bağlantı hatası oluştu.</div>";}}
    }});

    const settingTabs=[...document.querySelectorAll('[data-setting-target]')];
    const settingPanes=[...document.querySelectorAll('[data-setting-pane]')];
    settingTabs.forEach(tab=>tab.addEventListener('click',()=>{{settingTabs.forEach(item=>item.classList.toggle('active',item===tab));settingPanes.forEach(pane=>pane.classList.toggle('active',pane.dataset.settingPane===tab.dataset.settingTarget));}}));
    const toast=document.getElementById('admin-toast');if(toast)setTimeout(()=>toast.remove(),4200);
    </script></body></html>"""


@app.route("/admin/user/<uid>")
def admin_user(uid):
    if not logged_in(): return redirect("/login")
    return redirect(f"/admin?view=users&manage_user_id={uid}")


if __name__ == "__main__":
    validate_runtime_config()
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)