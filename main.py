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
RATE_UPDATE_SECONDS = max(60, int(os.getenv("RATE_UPDATE_SECONDS", "900")))
RATE_API_BASES = [base.strip().rstrip("/") for base in os.getenv("RATE_API_BASES", "https://api.binance.com,https://data-api.binance.vision").split(",") if base.strip()]
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()

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
    "panel_users": "panel_users.json",
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


EN_MESSAGES = {
    "welcome": "Welcome to Nerlo Wallet.\n\nUse the menu below to manage your wallet simply and securely.",
    "wallet_title": "Your Wallet Balances",
    "deposit_menu": "Select the balance type you want to deposit.",
    "withdraw_menu": "Select the balance type you want to withdraw.",
    "convert_menu": "Select the balance you want to convert.",
    "amount_question": "Enter the transaction amount.",
    "pin_question": "Enter your transaction PIN.",
    "pin_set_question": "For your security, create a 4–6 digit transaction PIN.",
    "pin_wrong": "The PIN could not be verified. Please try again.",
    "pin_saved": "Your transaction PIN has been saved securely.",
    "pin_changed": "Your transaction PIN has been changed. All other sessions were closed for your security.",
    "insufficient_balance": "You do not have enough balance for this transaction.",
    "no_balance": "You do not have an available balance.",
    "request_created": "Your request has been created and submitted for review.",
    "request_cancelled": "The transaction was cancelled.",
    "support": "Please contact the administrator for support.",
    "history_empty": "You do not have any transaction history yet.",
    "iban_warning": "The sender's Turkish ID number must be included in the payment description. Payments without this information may not be processed.",
    "maintenance": "The system is currently under short maintenance. Please try again later.",
    "frozen": "Your account is temporarily restricted. Please contact support.",
    "withdraw_locked": "Your withdrawals are temporarily locked.",
    "deposit_crypto_intro": "Send only through the specified network. Transfers made through a different network may be lost.",
    "deposit_received": "Your deposit notification has been received. It will be credited after review and approval.",
}

BOT_TEXTS = {
    "tr": {
        "choose_language": "Lütfen kullanmak istediğiniz dili seçin.\n\nPlease select your preferred language.",
        "language_saved": "Dil tercihiniz Türkçe olarak kaydedildi.",
        "wallet": "Cüzdanım", "deposit": "Bakiye Yükle", "withdraw": "Para Çek", "convert": "Dönüştür",
        "history": "İşlem Geçmişi", "security": "Güvenlik", "favorites": "Kayıtlı Adresler", "support": "Destek",
        "language": "Dil / Language", "cancel": "İptal", "confirm": "Onayla", "main_menu": "Ana Menü",
        "menu_prompt": "Menüden bir işlem seçiniz.", "wallet_title": "Cüzdan", "pending": "Bekleyen",
        "last_10": "Son 10 İşlem", "not_found": "İşlem bulunamadı.", "completed": "İşlem tamamlandı",
        "deposit_kind": "Yükleme", "withdraw_kind": "Çekim", "convert_kind": "Dönüşüm", "transaction_kind": "İşlem",
        "status_pending": "Bekliyor", "status_processing": "İşleniyor", "status_completed": "Tamamlandı", "status_rejected": "Reddedildi",
        "amount": "Tutar", "net": "Net", "sender": "Gönderen", "reference": "Referans", "network": "Ağ",
        "fee": "Komisyon", "bank": "Banka", "recipient": "Alıcı", "address": "Adres", "given": "Verilen", "received": "Alınan",
        "security_center": "Güvenlik Merkezi", "transaction_pin": "İşlem PIN'i", "withdraw_status": "Çekim durumu",
        "active_session": "Aktif oturum", "last_activity": "Son etkinlik", "active": "Aktif", "not_set": "Ayarlanmamış",
        "locked": "Kilitli", "open": "Açık", "change_pin": "PIN Değiştir", "notification_preferences": "Bildirim Tercihleri",
        "logout_sessions": "Tüm Oturumları Kapat", "confirm_question": "İşlemi onaylıyor musunuz?",
        "request_created_title": "Talep oluşturuldu", "new_withdraw_admin": "Yeni çekim talebi",
        "no_saved_address": "Kayıtlı adresiniz bulunmuyor.", "new_address": "Yeni Adres Ekle", "saved_wallets": "Kayıtlı Cüzdan Adresleri",
        "pin_digits": "PIN 4-6 haneli rakamlardan oluşmalıdır.", "repeat_pin": "Yeni PIN'inizi tekrar giriniz.",
        "pin_mismatch": "PIN'ler eşleşmedi. İşlemi yeniden başlatınız.", "new_pin": "Yeni işlem PIN'inizi giriniz.",
        "pin_locked": "Üç hatalı PIN denemesi nedeniyle çekimleriniz geçici olarak kilitlendi.",
        "valid_amount": "Geçerli bir tutar giriniz.", "insufficient": "Yetersiz bakiye", "minimum": "Minimum",
        "daily_limit": "Günlük çekim limitiniz aşılıyor. Kalan limit: {amount}",
        "deposit_summary_tl": "TL Yükleme Özeti", "deposit_summary": "{asset} Yükleme Özeti", "to_deposit": "Yüklenecek",
        "credited": "Bakiyeye Geçecek", "iban_copy": "IBAN Kopyala", "payment_sent": "Ödemeyi Yaptım",
        "show_qr": "QR Göster", "notify_transfer": "Gönderimi Bildir", "deposit_address": "Yatırma Adresi",
        "bank_unavailable": "TL yükleme bilgileri şu anda eksik. Lütfen daha sonra tekrar deneyin veya destek ile iletişime geçin.",
        "bank_name_question": "Banka adını giriniz.", "iban_question": "IBAN bilginizi giriniz.", "account_name_question": "Hesap sahibinin ad ve soyadını giriniz.",
        "wallet_address_question": "Alıcı cüzdan adresini giriniz.", "withdraw_address_select": "Çekim adresini seçiniz.", "enter_new_address": "Yeni adres gir",
        "swap_summary": "Takas Özeti", "sent": "Gönderilen", "to_receive": "Alınacak", "all_balance_note": "Tüm kullanılabilir bakiye dönüştürülecektir.",
        "rate_note": "Kur son onayda yeniden hesaplanır. Canlı kurlar 15 dakikada bir yenilenir.{updated}",
        "sender_name_question": "Ödemeyi gönderen kişinin ad ve soyadını giriniz.",
        "sender_name_invalid": "Lütfen ödemeyi gönderen kişinin ad ve soyadını doğru giriniz.",
        "reference_question": "Ödeme açıklamasını veya dekont referansını giriniz. Yoksa YOK yazabilirsiniz.",
        "deposit_session_missing": "Yükleme oturumu bulunamadı. İşlemi yeniden başlatınız.", "session_missing": "İşlem oturumu bulunamadı.",
        "qr_missing": "QR bilgisi bulunamadı.", "qr_caption": "Yatırma QR Kodu", "new_deposit_admin": "Yeni bakiye yükleme bildirimi",
        "available_balance": "Çekilebilir bakiye", "min_withdraw": "Minimum çekim", "convert_available": "Dönüştürülecek: {amount} kullanılabilir.",
        "balance": "Bakiye", "enter_or_all": "Tutarı girin veya tüm bakiyeyi dönüştürün.", "convert_all": "Tüm Bakiyeyi Dönüştür",
        "convert_session_missing": "Dönüşüm oturumu bulunamadı.", "invalid_rate": "Geçersiz kur nedeniyle dönüşüm yapılamıyor.",
        "invalid_fee": "Geçersiz komisyon oranı.", "invalid_net": "Komisyon sonrası geçerli tutar oluşmadı.",
        "already_confirmed": "Bu onay daha önce kullanıldı.", "missing_convert": "Dönüşüm bilgileri eksik. İşlemi yeniden başlatınız.",
        "nothing_to_confirm": "Onaylanacak işlem bulunamadı. İşlemi yeniden başlatınız.",
        "operation_failed": "İşlem tamamlanamadı. Lütfen işlemi yeniden başlatınız.",
        "favorite_asset": "Kayıtlı adresin para birimini seçiniz.", "favorite_label": "Bu adres için bir isim giriniz.",
        "favorite_address": "Cüzdan adresini giriniz.", "favorite_saved": "Adres kaydedildi.",
        "current_pin": "Mevcut işlem PIN'inizi giriniz.", "notifications_edit": "Bildirim tercihlerinizi düzenleyiniz.",
        "transactions": "İşlemler", "announcements": "Duyurular", "notification_updated": "Bildirim tercihi güncellendi.",
        "sessions_closed": "Diğer oturum kayıtları kapatıldı.", "on": "Açık", "off": "Kapalı",
        "withdraw_summary_tl": "TL Çekim Özeti", "withdraw_summary": "{asset} Çekim Özeti", "recipient_gets": "Alıcıya Geçecek", "to_send": "Gönderilecek",
        "wallet_address": "Cüzdan Adresi", "deposit_pending_title": "Yükleme bildiriminiz alındı", "request_rejected": "İşleminiz reddedildi.",
    },
    "en": {
        "choose_language": "Please select your preferred language.\n\nLütfen kullanmak istediğiniz dili seçin.",
        "language_saved": "Your language preference has been saved as English.",
        "wallet": "My Wallet", "deposit": "Deposit", "withdraw": "Withdraw", "convert": "Convert",
        "history": "Transaction History", "security": "Security", "favorites": "Saved Addresses", "support": "Support",
        "language": "Language / Dil", "cancel": "Cancel", "confirm": "Confirm", "main_menu": "Main Menu",
        "menu_prompt": "Please select an action from the menu.", "wallet_title": "Wallet", "pending": "Pending",
        "last_10": "Last 10 Transactions", "not_found": "Transaction not found.", "completed": "Transaction completed",
        "deposit_kind": "Deposit", "withdraw_kind": "Withdrawal", "convert_kind": "Conversion", "transaction_kind": "Transaction",
        "status_pending": "Pending", "status_processing": "Processing", "status_completed": "Completed", "status_rejected": "Rejected",
        "amount": "Amount", "net": "Net", "sender": "Sender", "reference": "Reference", "network": "Network",
        "fee": "Fee", "bank": "Bank", "recipient": "Recipient", "address": "Address", "given": "Sent", "received": "Received",
        "security_center": "Security Center", "transaction_pin": "Transaction PIN", "withdraw_status": "Withdrawal status",
        "active_session": "Active session", "last_activity": "Last activity", "active": "Active", "not_set": "Not set",
        "locked": "Locked", "open": "Open", "change_pin": "Change PIN", "notification_preferences": "Notification Preferences",
        "logout_sessions": "Close All Other Sessions", "confirm_question": "Do you confirm this transaction?",
        "request_created_title": "Request created", "new_withdraw_admin": "Yeni çekim talebi",
        "no_saved_address": "You do not have any saved addresses.", "new_address": "Add New Address", "saved_wallets": "Saved Wallet Addresses",
        "pin_digits": "The PIN must contain 4–6 digits.", "repeat_pin": "Enter your new PIN again.",
        "pin_mismatch": "The PINs do not match. Please restart the transaction.", "new_pin": "Enter your new transaction PIN.",
        "pin_locked": "Your withdrawals were temporarily locked after three incorrect PIN attempts.",
        "valid_amount": "Enter a valid amount.", "insufficient": "Insufficient balance", "minimum": "Minimum",
        "daily_limit": "This exceeds your daily withdrawal limit. Remaining limit: {amount}",
        "deposit_summary_tl": "TRY Deposit Summary", "deposit_summary": "{asset} Deposit Summary", "to_deposit": "Deposit amount",
        "credited": "Amount to be credited", "iban_copy": "Copy IBAN", "payment_sent": "I Made the Payment",
        "show_qr": "Show QR", "notify_transfer": "Notify Transfer", "deposit_address": "Deposit Address",
        "bank_unavailable": "TRY deposit details are currently incomplete. Please try again later or contact support.",
        "bank_name_question": "Enter the bank name.", "iban_question": "Enter your IBAN.", "account_name_question": "Enter the account holder's full name.",
        "wallet_address_question": "Enter the recipient wallet address.", "withdraw_address_select": "Select the withdrawal address.", "enter_new_address": "Enter a new address",
        "swap_summary": "Conversion Summary", "sent": "Sent", "to_receive": "You will receive", "all_balance_note": "All available balance will be converted.",
        "rate_note": "The rate is recalculated at final confirmation. Live rates refresh every 15 minutes.{updated}",
        "sender_name_question": "Enter the full name of the person who made the payment.",
        "sender_name_invalid": "Enter the payer's full name correctly.",
        "reference_question": "Enter the payment description or receipt reference. Enter NONE if unavailable.",
        "deposit_session_missing": "The deposit session was not found. Please restart the transaction.", "session_missing": "The transaction session was not found.",
        "qr_missing": "QR information was not found.", "qr_caption": "Deposit QR Code", "new_deposit_admin": "Yeni bakiye yükleme bildirimi",
        "available_balance": "Available balance", "min_withdraw": "Minimum withdrawal", "convert_available": "Available to convert: {amount}.",
        "balance": "Balance", "enter_or_all": "Enter an amount or convert the full balance.", "convert_all": "Convert Full Balance",
        "convert_session_missing": "The conversion session was not found.", "invalid_rate": "The conversion cannot be completed because the exchange rate is invalid.",
        "invalid_fee": "The fee rate is invalid.", "invalid_net": "No valid amount remains after the fee.",
        "already_confirmed": "This confirmation has already been used.", "missing_convert": "Conversion information is incomplete. Please restart the transaction.",
        "nothing_to_confirm": "There is no transaction to confirm. Please restart it.",
        "operation_failed": "The transaction could not be completed. Please restart it.",
        "favorite_asset": "Select the asset for the saved address.", "favorite_label": "Enter a name for this address.",
        "favorite_address": "Enter the wallet address.", "favorite_saved": "Address saved.",
        "current_pin": "Enter your current transaction PIN.", "notifications_edit": "Edit your notification preferences.",
        "transactions": "Transactions", "announcements": "Announcements", "notification_updated": "Notification preference updated.",
        "sessions_closed": "Other session records were closed.", "on": "On", "off": "Off",
        "withdraw_summary_tl": "TRY Withdrawal Summary", "withdraw_summary": "{asset} Withdrawal Summary", "recipient_gets": "Recipient receives", "to_send": "Amount to send",
        "wallet_address": "Wallet Address", "deposit_pending_title": "Your deposit notification was received", "request_rejected": "Your transaction was rejected.",
    },
}

MENU_ACTIONS = {
    "Cüzdanım": "wallet", "My Wallet": "wallet",
    "Bakiye Yükle": "deposit", "Deposit": "deposit",
    "Para Çek": "withdraw", "Withdraw": "withdraw",
    "Dönüştür": "convert", "Convert": "convert",
    "İşlem Geçmişi": "history", "Transaction History": "history",
    "Güvenlik": "security", "Security": "security",
    "Kayıtlı Adresler": "favorites", "Saved Addresses": "favorites",
    "Destek": "support", "Support": "support",
    "Dil / Language": "language", "Language / Dil": "language",
    "Ana Menü": "menu", "Main Menu": "menu",
}

DEFAULT_SETTINGS = {
    "bank_name": os.getenv("DEFAULT_BANK_NAME", ""),
    "iban": os.getenv("DEFAULT_IBAN", ""),
    "iban_owner": os.getenv("DEFAULT_IBAN_OWNER", ""),
    "wallet_USDT": os.getenv("DEFAULT_WALLET_USDT", ""),
    "wallet_TRX": os.getenv("DEFAULT_WALLET_TRX", ""),
    "wallet_XMR": os.getenv("DEFAULT_WALLET_XMR", ""),
    "wallet_LTC": os.getenv("DEFAULT_WALLET_LTC", ""),
    "rate_USDT_TL": "46.40",
    "rate_LTC_TL": "2065.00",
    "rate_TRX_TL": "15.50",
    "auto_rate_enabled": "on",
    "rates_source": "Binance Spot",
    "rates_last_updated": "",
    "rates_last_error": "",
    "fee_deposit_TL_percent": "0",
    "fee_deposit_USDT_percent": "0",
    "fee_deposit_LTC_percent": "0",
    "fee_deposit_TRX_percent": "0",
    "fee_withdraw_TL_percent": "1",
    "fee_withdraw_USDT_percent": "1",
    "fee_withdraw_LTC_percent": "1",
    "fee_withdraw_TRX_percent": "1",
    "fee_convert_tl_percent": "2",
    "fee_convert_crypto_percent": "2",
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
    "icon_XMR": "5900147027219587568",
}

init_database()

users = load_json(FILES["users"], {})
requests_db = load_json(FILES["requests"], {})
transactions = load_json(FILES["transactions"], {})
settings = load_json(FILES["settings"], {})
messages = load_json(FILES["messages"], {})
admin_logs = load_json(FILES["admin_logs"], [])
security_events = load_json(FILES["security_events"], [])
panel_users = load_json(FILES["panel_users"], {})
legacy_convert_fee = str(settings.get("fee_convert_percent", "2"))
for k, v in DEFAULT_SETTINGS.items():
    settings.setdefault(k, legacy_convert_fee if k in ("fee_convert_tl_percent", "fee_convert_crypto_percent") else v)
settings.pop("fee_convert_percent", None)
if not str(settings.get("icon_TL", "")).strip():
    settings["icon_TL"] = DEFAULT_SETTINGS["icon_TL"]
for k, v in DEFAULT_MESSAGES.items():
    messages.setdefault(k, v)
rank_data_removed = False
for existing_user in users.values():
    if "tier" in existing_user:
        existing_user.pop("tier", None)
        rank_data_removed = True
save_json(FILES["settings"], settings)
save_json(FILES["messages"], messages)
if rank_data_removed:
    save_json(FILES["users"], users)


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
    pattern = re.compile(r"\{\{(TL|USDT|LTC|TRX|XMR)\}\}")

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
        r"\{\{(TL|USDT|LTC|TRX|XMR)\}\}",
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


def lang_of(uid):
    lang = str(users.get(str(uid), {}).get("language", "")).lower()
    return lang if lang in ("tr", "en") else "tr"


def t(uid, key, **kwargs):
    lang = lang_of(uid)
    value = BOT_TEXTS.get(lang, BOT_TEXTS["tr"]).get(key, BOT_TEXTS["tr"].get(key, key))
    try:
        return value.format(**kwargs)
    except (KeyError, ValueError):
        return value


def msg(uid, key):
    return EN_MESSAGES.get(key, DEFAULT_MESSAGES.get(key, key)) if lang_of(uid) == "en" else messages.get(key, DEFAULT_MESSAGES.get(key, key))


def language_keyboard():
    return {"inline_keyboard": [[
        inline_button("🇹🇷 Türkçe", "lang:tr"),
        inline_button("🇬🇧 English", "lang:en"),
    ]]}


def reply_keyboard(uid=None):
    lang = lang_of(uid) if uid is not None else "tr"
    x = BOT_TEXTS[lang]
    return {
        "keyboard": [
            [{"text": x["wallet"]}, {"text": x["deposit"]}],
            [{"text": x["withdraw"]}, {"text": x["convert"]}],
            [{"text": x["history"]}, {"text": x["security"]}],
            [{"text": x["favorites"]}, {"text": x["support"]}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def asset_keyboard(prefix, assets, exclude=None, uid=None):
    rows = []
    for asset in assets:
        if asset != exclude:
            rows.append([inline_button(asset, f"{prefix}:{asset}", f"icon_{asset}")])
    rows.append([inline_button(t(uid, "cancel"), "cancel")])
    return {"inline_keyboard": rows}


def confirm_keyboard(ok_data, cancel_data="cancel", uid=None):
    return {"inline_keyboard": [[inline_button(t(uid, "confirm"), ok_data), inline_button(t(uid, "cancel"), cancel_data)]]}


def copy_button(text, value):
    value = str(value or "").strip()
    if not value:
        return None
    return {"text": text, "copy_text": {"text": value}}


def order_summary(title, rows, note=""):
    lines = [title, "━━━━━━━━━━━━"]
    for label, value in rows:
        lines.append(f"{label}\n{value}")
    if note:
        lines.extend(["━━━━━━━━━━━━", note])
    return "\n\n".join(lines)


def coin_fmt_lang(value, asset, lang="tr"):
    asset = str(asset or "")
    value = D(value)
    precision = Decimal("0.000001") if asset == "LTC" else Decimal("0.01")
    out = value.quantize(precision, rounding=ROUND_DOWN)
    raw = f"{out:,.6f}" if asset == "LTC" else f"{out:,.2f}"
    if lang == "tr":
        raw = raw.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{raw} {{{{{asset}}}}}"


def ucoin(uid, value, asset):
    return coin_fmt_lang(value, asset, lang_of(uid))

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
            "language": "",
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
    u.setdefault("language", "")
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

        fee_rate = fee_percent("convert", uid=uid, from_asset=source, to_asset=target)
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


def _ticker_price(symbol):
    last_error = None
    for base in RATE_API_BASES:
        try:
            response = requests.get(
                f"{base}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=12,
                headers={"User-Agent": "Nerlo-Wallet/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
            price = D(payload.get("price", "0"))
            if price > 0:
                return price
            last_error = RuntimeError(f"{symbol} price is empty")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Live price could not be retrieved for {symbol}: {last_error}")


def _fetch_binance_rates():
    try:
        usdt_try = _ticker_price("USDTTRY")
    except Exception:
        try_usdt = _ticker_price("TRYUSDT")
        if try_usdt <= 0:
            raise RuntimeError("TRY/USDT rate is invalid")
        usdt_try = Decimal("1") / try_usdt

    def try_rate(asset):
        try:
            return _ticker_price(f"{asset}TRY")
        except Exception:
            return _ticker_price(f"{asset}USDT") * usdt_try

    rates = {"USDT": usdt_try, "LTC": try_rate("LTC"), "TRX": try_rate("TRX")}
    if any(value <= 0 for value in rates.values()):
        raise RuntimeError("One or more Binance rates are invalid")
    return rates


def _fetch_coingecko_rates():
    headers = {"User-Agent": "Nerlo-Wallet/1.0"}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": "tether,litecoin,tron",
            "vs_currencies": "try",
            "include_last_updated_at": "true",
        },
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rates = {
        "USDT": D(payload.get("tether", {}).get("try", "0")),
        "LTC": D(payload.get("litecoin", {}).get("try", "0")),
        "TRX": D(payload.get("tron", {}).get("try", "0")),
    }
    if any(value <= 0 for value in rates.values()):
        raise RuntimeError("One or more CoinGecko rates are invalid")
    return rates


def fetch_live_rates():
    errors = []
    try:
        return _fetch_binance_rates(), "Binance Spot"
    except Exception as exc:
        errors.append(f"Binance: {exc}")
    try:
        return _fetch_coingecko_rates(), "CoinGecko"
    except Exception as exc:
        errors.append(f"CoinGecko: {exc}")
    raise RuntimeError(" | ".join(errors))


def update_live_rates():
    if str(settings.get("auto_rate_enabled", "on")).lower() != "on":
        return False
    try:
        rates, source = fetch_live_rates()
        with data_lock:
            for asset, value in rates.items():
                precision = Decimal("0.00000001")
                settings[f"rate_{asset}_TL"] = format(value.quantize(precision, rounding=ROUND_DOWN), "f")
            settings["rates_source"] = source
            settings["rates_last_updated"] = now()
            settings["rates_last_error"] = ""
            save_json(FILES["settings"], settings)
        print("LIVE RATES UPDATED:", {asset: settings[f"rate_{asset}_TL"] for asset in ("USDT", "LTC", "TRX")})
        return True
    except Exception as exc:
        settings["rates_last_error"] = str(exc)[:500]
        try:
            save_json(FILES["settings"], settings)
        except Exception:
            pass
        print("LIVE RATE UPDATE ERROR:", exc)
        return False


def rate_update_loop():
    while True:
        update_live_rates()
        time.sleep(RATE_UPDATE_SECONDS)


def live_rate_note(uid):
    updated = str(settings.get("rates_last_updated", "")).strip()
    suffix = f" Son güncelleme: {updated}." if updated and lang_of(uid) == "tr" else (f" Last update: {updated}." if updated else "")
    return t(uid, "rate_note", updated=suffix)

def fee_percent(kind, asset=None, uid=None, from_asset=None, to_asset=None):
    if uid is not None:
        override = str(users.get(str(uid), {}).get("custom_fee_percent", "")).strip()
        if override:
            return D(override)
    if kind == "convert":
        involves_tl = from_asset == "TL" or to_asset == "TL"
        key = "fee_convert_tl_percent" if involves_tl else "fee_convert_crypto_percent"
        return D(settings.get(key, "0"))
    return D(settings.get(f"fee_{kind}_{asset}_percent", "0"))


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
    uid = str(uid)
    u = users[uid]
    lines = [t(uid, "wallet_title"), ""]
    for asset in ASSETS:
        lines.append(ucoin(uid, u["balances"].get(asset, "0"), asset))
        pending = D(u.get("pending_balances", {}).get(asset, "0"))
        if pending > 0:
            lines.append(f"{t(uid, 'pending')} · {ucoin(uid, pending, asset)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def active_balances(uid):
    return [a for a in ASSETS if balance(uid, a) > 0]


def localized_status(uid, value):
    return t(uid, f"status_{value}") if value in ("pending", "processing", "completed", "rejected") else value


def request_summary(rid, lang_uid=None):
    r = requests_db.get(str(rid), {})
    uid = str(lang_uid if lang_uid is not None else r.get("user_id", ""))
    if not r:
        return t(uid, "not_found")

    kind = {
        "deposit": t(uid, "deposit_kind"),
        "withdraw": t(uid, "withdraw_kind"),
        "convert": t(uid, "convert_kind"),
    }.get(r.get("type"), t(uid, "transaction_kind"))
    lines = [f"{kind} · #{rid}", localized_status(uid, r.get("status"))]

    if r.get("type") == "deposit":
        asset = r.get("asset")
        lines += [
            f"{t(uid, 'amount')} · {ucoin(uid, r.get('amount'), asset)}",
            f"{t(uid, 'net')} · {ucoin(uid, r.get('net_amount'), asset)}",
        ]
        if asset == "TL":
            lines += [
                f"{t(uid, 'sender')} · {r.get('sender_name', '-')}",
                f"{t(uid, 'reference')} · {r.get('tx_note', '-')}",
            ]
        else:
            lines.append(f"{t(uid, 'network')} · {r.get('network', '-')}")
    elif r.get("type") == "withdraw":
        asset = r.get("asset")
        lines += [
            f"{t(uid, 'amount')} · {ucoin(uid, r.get('amount'), asset)}",
            f"{t(uid, 'net')} · {ucoin(uid, r.get('net_amount'), asset)}",
            f"{t(uid, 'fee')} · {ucoin(uid, r.get('fee'), asset)}",
        ]
        if asset == "TL":
            lines += [
                f"{t(uid, 'bank')} · {r.get('bank_name', '-')}",
                f"IBAN · {r.get('iban', '-')}",
                f"{t(uid, 'recipient')} · {r.get('name', '-')}",
            ]
        else:
            lines.append(f"{t(uid, 'address')} · {r.get('address', '-')}")
    elif r.get("type") == "convert":
        lines += [
            f"{t(uid, 'given')} · {ucoin(uid, r.get('from_amount'), r.get('from_asset'))}",
            f"{t(uid, 'received')} · {ucoin(uid, r.get('net_to_amount'), r.get('to_asset'))}",
            f"{t(uid, 'fee')} · {ucoin(uid, r.get('fee'), r.get('to_asset'))}",
        ]
    lines.append(r.get("created_at", ""))
    return "\n".join(lines)


def receipt_text(rid, lang_uid=None):
    r = requests_db.get(str(rid))
    uid = str(lang_uid if lang_uid is not None else (r or {}).get("user_id", ""))
    if not r:
        return t(uid, "not_found")
    return t(uid, "completed") + "\n\n" + request_summary(rid, uid)


def user_allowed(chat_id):
    uid = str(chat_id)
    u = get_user(chat_id)
    if u.get("status") == "frozen":
        send(chat_id, msg(uid, "frozen"), reply_keyboard(uid))
        return False
    if settings.get("maintenance_mode") == "on" and uid != str(ADMIN_CHAT_ID):
        maintenance_text = settings.get("maintenance_message") if lang_of(uid) == "tr" else ""
        send(chat_id, maintenance_text or msg(uid, "maintenance"), reply_keyboard(uid))
        return False
    return True


def ask_language(chat_id):
    send(chat_id, BOT_TEXTS["tr"]["choose_language"], language_keyboard())


def start_user(chat_id, username=""):
    uid = str(chat_id)
    u = get_user(chat_id, username)
    if u.get("language") not in ("tr", "en"):
        ask_language(chat_id)
        return
    text = msg(uid, "welcome")
    if settings.get("announcement_active") == "on" and u.get("notifications", {}).get("announcements", True) and settings.get("announcement_text"):
        text += "\n\n📢 " + settings["announcement_text"]
    send(chat_id, text, reply_keyboard(uid))


def show_history(chat_id):
    uid = str(chat_id)
    items = [r for r in requests_db.values() if r.get("user_id") == uid]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    items = items[:10]
    if not items:
        send(chat_id, msg(uid, "history_empty"), reply_keyboard(uid))
        return
    rows = [[inline_button(f"#{r['id']} · {localized_status(uid, r.get('status'))}", f"detail:{r['id']}")] for r in items]
    send(chat_id, t(uid, "last_10"), {"inline_keyboard": rows})


def show_security(chat_id):
    uid = str(chat_id)
    u = get_user(chat_id)
    pin = t(uid, "active") if u.get("pin_hash") else t(uid, "not_set")
    lock = t(uid, "locked") if u.get("withdraw_locked") else t(uid, "open")
    text = (
        f"{t(uid, 'security_center')}\n\n"
        f"{t(uid, 'transaction_pin')}: {pin}\n"
        f"{t(uid, 'withdraw_status')}: {lock}\n"
        f"{t(uid, 'active_session')}: Telegram\n"
        f"{t(uid, 'last_activity')}: {u.get('last_seen', '')}"
    )
    kb = {"inline_keyboard": [
        [inline_button(t(uid, "change_pin"), "security:set_pin")],
        [inline_button(t(uid, "notification_preferences"), "security:notifications")],
        [inline_button(t(uid, "logout_sessions"), "security:logout_sessions")],
    ]}
    send(chat_id, text, kb)


def begin_deposit(chat_id):
    uid = str(chat_id)
    user_state[uid] = {"flow": "deposit", "step": "asset", "idempotency_key": secrets.token_urlsafe(24)}
    send(chat_id, msg(uid, "deposit_menu"), asset_keyboard("deposit_asset", ASSETS, uid=uid))


def begin_withdraw(chat_id):
    uid = str(chat_id)
    u = get_user(chat_id)
    if u.get("withdraw_locked"):
        send(chat_id, msg(uid, "withdraw_locked"), reply_keyboard(uid))
        return
    assets = active_balances(chat_id)
    if not assets:
        send(chat_id, msg(uid, "no_balance"), reply_keyboard(uid))
        return
    user_state[uid] = {"flow": "withdraw", "step": "asset"}
    send(chat_id, msg(uid, "withdraw_menu"), asset_keyboard("withdraw_asset", assets, uid=uid))


def begin_convert(chat_id):
    uid = str(chat_id)
    assets = active_balances(chat_id)
    if not assets:
        send(chat_id, msg(uid, "no_balance"), reply_keyboard(uid))
        return
    user_state[uid] = {"flow": "convert", "step": "from_asset"}
    send(chat_id, msg(uid, "convert_menu"), asset_keyboard("convert_from", assets, uid=uid))


def require_pin(uid, state, next_step="pin"):
    if not users[uid].get("pin_hash"):
        state["after_pin_setup"] = next_step
        state["step"] = "set_pin"
        send(uid, msg(uid, "pin_set_question"), reply_keyboard(uid))
    else:
        state["step"] = next_step
        send(uid, msg(uid, "pin_question"), reply_keyboard(uid))


def send_second_confirmation(uid, state, prefix=""):
    state["step"] = "second_confirm"
    state["confirm_token"] = secrets.token_urlsafe(24)
    state["confirmation_consumed"] = False
    preview = state.get("preview", t(uid, "confirm_question"))
    text = f"{prefix}\n\n{preview}" if prefix else preview
    send(uid, text, confirm_keyboard("second_confirm", uid=uid))


def finalize_withdraw(uid, state):
    rid = atomic_withdraw(uid, state)
    user_state.pop(uid, None)
    send(uid, t(uid, "request_created_title") + "\n\n" + request_summary(rid, uid), reply_keyboard(uid))
    if ADMIN_CHAT_ID:
        send(ADMIN_CHAT_ID, "Yeni çekim talebi\n\n" + request_summary(rid, ""))


def create_deposit_notice(uid, state, extra=None):
    uid = str(uid)
    if state.get("request_id") and state["request_id"] in requests_db:
        return state["request_id"]
    idem = state.setdefault("idempotency_key", secrets.token_urlsafe(24))
    for rid, existing in requests_db.items():
        if existing.get("user_id") == uid and existing.get("type") == "deposit" and existing.get("idempotency_key") == idem:
            state["request_id"] = rid
            return rid
    payload = {
        "asset": state["asset"],
        "amount": state["amount"],
        "fee": state["fee"],
        "net_amount": state["net_amount"],
        "idempotency_key": idem,
    }
    if extra:
        payload.update(extra)
    with data_lock:
        old_pending = pending_balance(uid, state["asset"])
        rid = new_request(uid, "deposit", payload)
        try:
            change_pending(uid, state["asset"], D(state["net_amount"]), "deposit_pending", rid)
            state["request_id"] = rid
            return rid
        except Exception:
            users[uid]["pending_balances"][state["asset"]] = str(old_pending)
            requests_db.pop(rid, None)
            save_json(FILES["users"], users)
            save_json(FILES["requests"], requests_db)
            raise


def handle_text(chat_id, username, text):
    uid = str(chat_id)
    u = get_user(chat_id, username)
    text = str(text or "").strip()
    if text == "/start" or MENU_ACTIONS.get(text) == "menu":
        start_user(chat_id, username)
        return
    if text in ("/language", "/dil") or MENU_ACTIONS.get(text) == "language":
        ask_language(chat_id)
        return
    if u.get("language") not in ("tr", "en"):
        ask_language(chat_id)
        return
    if not user_allowed(chat_id):
        return

    action = MENU_ACTIONS.get(text)
    if action == "wallet": send(chat_id, wallet_text(uid), reply_keyboard(uid)); return
    if action == "deposit": begin_deposit(chat_id); return
    if action == "withdraw": begin_withdraw(chat_id); return
    if action == "convert": begin_convert(chat_id); return
    if action == "history": show_history(chat_id); return
    if action == "security": show_security(chat_id); return
    if action == "favorites":
        favs = users[uid].get("favorites", [])
        if not favs:
            send(chat_id, t(uid, "no_saved_address"), {"inline_keyboard": [[inline_button(t(uid, "new_address"), "favorite:add")]]})
        else:
            lines = [t(uid, "saved_wallets")] + [f"{i+1}. {f['label']} · {f['asset']}\n{f['address']}" for i, f in enumerate(favs)]
            send(chat_id, "\n\n".join(lines), {"inline_keyboard": [[inline_button(t(uid, "new_address"), "favorite:add")]]})
        return
    if action == "support": send(chat_id, msg(uid, "support"), reply_keyboard(uid)); return

    state = user_state.get(uid)
    if not state:
        send(chat_id, t(uid, "menu_prompt"), reply_keyboard(uid)); return
    flow, step = state.get("flow"), state.get("step")

    if step == "set_pin":
        pin = text
        if not pin.isdigit() or not 4 <= len(pin) <= 6:
            send(chat_id, t(uid, "pin_digits")); return
        state["new_pin"] = pin
        state["step"] = "confirm_new_pin"
        send(chat_id, t(uid, "repeat_pin")); return

    if step == "confirm_new_pin":
        if text != state.get("new_pin"):
            user_state.pop(uid, None)
            send(chat_id, t(uid, "pin_mismatch"), reply_keyboard(uid)); return
        changing = state.get("changing_pin", False)
        next_step = state.get("after_pin_setup")
        users[uid]["pin_hash"] = hash_pin(text)
        users[uid]["session_version"] = int(users[uid].get("session_version", 1)) + 1
        users[uid]["last_pin_change"] = now()
        users[uid]["sessions"] = {"telegram": {"created_at": now(), "last_seen": now(), "active": True, "version": users[uid]["session_version"]}}
        users[uid]["pin_failed_attempts"] = 0
        save_json(FILES["users"], users)
        add_security_event(uid, "pin_changed" if changing else "pin_created", "Tüm eski oturumlar geçersiz kılındı")
        state.pop("new_pin", None); state.pop("after_pin_setup", None)
        if next_step == "pin":
            send_second_confirmation(uid, state, msg(uid, "pin_saved"))
        elif next_step:
            state["step"] = next_step
            send(chat_id, msg(uid, "pin_saved"))
        else:
            user_state.pop(uid, None)
            send(chat_id, msg(uid, "pin_changed" if changing else "pin_saved"), reply_keyboard(uid))
        return

    if step in ("old_pin", "pin"):
        if not verify_pin(users[uid].get("pin_hash"), text):
            users[uid]["pin_failed_attempts"] = int(users[uid].get("pin_failed_attempts", 0)) + 1
            save_json(FILES["users"], users)
            if users[uid]["pin_failed_attempts"] >= 3:
                users[uid]["withdraw_locked"] = True
                save_json(FILES["users"], users)
                add_security_event(uid, "pin_lock", "3 hatalı PIN denemesi")
                send(chat_id, t(uid, "pin_locked"))
            else:
                send(chat_id, msg(uid, "pin_wrong"))
            return
        users[uid]["pin_failed_attempts"] = 0
        save_json(FILES["users"], users)
        if step == "old_pin":
            state["step"] = "set_pin"; state["changing_pin"] = True
            send(chat_id, t(uid, "new_pin"))
        else:
            send_second_confirmation(uid, state)
        return

    if step == "amount":
        amount = D(text)
        if amount <= 0:
            send(chat_id, t(uid, "valid_amount")); return
        asset = state.get("asset") or state.get("from_asset")
        if flow in ("withdraw", "convert") and amount > balance(uid, asset):
            send(chat_id, f"{t(uid, 'insufficient')} · {ucoin(uid, balance(uid, asset), asset)}"); return
        if amount < min_amount(flow, asset):
            send(chat_id, f"{t(uid, 'minimum')} · {ucoin(uid, min_amount(flow, asset), asset)}"); return
        state["amount"] = str(amount)

        if flow == "deposit":
            p = fee_percent("deposit", asset, uid); fee = fee_amount(amount, p); net = amount - fee
            state.update({"fee": str(fee), "net_amount": str(net)})
            if asset == "TL":
                iban = str(settings.get("iban", "")).strip()
                owner = str(settings.get("iban_owner", "")).strip()
                if not iban or not owner:
                    user_state.pop(uid, None)
                    send(chat_id, t(uid, "bank_unavailable"), reply_keyboard(uid)); return
                summary = order_summary(
                    t(uid, "deposit_summary_tl"),
                    [
                        (t(uid, "bank"), settings.get("bank_name") or "-"),
                        (t(uid, "recipient"), owner),
                        ("IBAN", iban),
                        (t(uid, "to_deposit"), ucoin(uid, amount, "TL")),
                        (t(uid, "fee"), ucoin(uid, fee, "TL")),
                        (t(uid, "credited"), ucoin(uid, net, "TL")),
                    ],
                    msg(uid, "iban_warning"),
                )
                buttons = []
                copy = copy_button(t(uid, "iban_copy"), iban)
                if copy: buttons.append([copy])
                buttons += [[inline_button(t(uid, "payment_sent"), "deposit_sent")], [inline_button(t(uid, "cancel"), "cancel")]]
                send(chat_id, summary, {"inline_keyboard": buttons})
            else:
                address = str(settings.get(f"wallet_{asset}", "")).strip()
                if not address:
                    user_state.pop(uid, None)
                    send(chat_id, t(uid, "bank_unavailable"), reply_keyboard(uid)); return
                network = settings.get(f"network_{asset}", asset)
                state.update({"network": network, "qr_content": address, "qr_caption": f"{asset} {t(uid, 'qr_caption')} · {network}"})
                card = order_summary(
                    t(uid, "deposit_summary", asset=asset),
                    [
                        (t(uid, "network"), network),
                        (t(uid, "to_deposit"), ucoin(uid, amount, asset)),
                        (t(uid, "fee"), ucoin(uid, fee, asset)),
                        (t(uid, "credited"), ucoin(uid, net, asset)),
                        (t(uid, "deposit_address"), address),
                    ],
                    msg(uid, "deposit_crypto_intro"),
                )
                send(chat_id, card, {"inline_keyboard": [
                    [inline_button(t(uid, "show_qr"), "show_deposit_qr")],
                    [inline_button(t(uid, "notify_transfer"), "deposit_sent")],
                    [inline_button(t(uid, "cancel"), "cancel")],
                ]})
            state["step"] = "waiting_sent"
            return

        if flow == "withdraw":
            if withdrawn_today(uid, asset) + amount > daily_limit(asset) > 0:
                remaining = daily_limit(asset) - withdrawn_today(uid, asset)
                send(chat_id, t(uid, "daily_limit", amount=ucoin(uid, max(remaining, Decimal("0")), asset))); return
            p = fee_percent("withdraw", asset, uid); fee = fee_amount(amount, p); net = amount - fee
            state.update({"fee": str(fee), "net_amount": str(net)})
            if asset == "TL":
                state["step"] = "bank_name"; send(chat_id, t(uid, "bank_name_question"))
            else:
                favs = [f for f in users[uid].get("favorites", []) if f.get("asset") == asset]
                if favs:
                    rows = [[inline_button(f["label"], f"favorite_use:{i}")] for i, f in enumerate(users[uid]["favorites"]) if f.get("asset") == asset]
                    rows.append([inline_button(t(uid, "enter_new_address"), "favorite_use:new")])
                    send(chat_id, t(uid, "withdraw_address_select"), {"inline_keyboard": rows}); state["step"] = "address_choice"
                else:
                    state["step"] = "address"; send(chat_id, t(uid, "wallet_address_question"))
            return

        if flow == "convert":
            to_asset = state["to_asset"]
            source_rate, target_rate = rate(asset), rate(to_asset)
            if source_rate <= 0 or target_rate <= 0: send(chat_id, t(uid, "invalid_rate")); return
            p = fee_percent("convert", uid=uid, from_asset=asset, to_asset=to_asset)
            if p < 0 or p >= 100: send(chat_id, t(uid, "invalid_fee")); return
            tl_value = amount * source_rate; gross = tl_value / target_rate; fee = fee_amount(gross, p); net = gross - fee
            if net <= 0: send(chat_id, t(uid, "invalid_net")); return
            state.update({"tl_value": str(tl_value), "gross_to": str(gross), "fee": str(fee), "net_amount": str(net)})
            state["preview"] = order_summary(
                t(uid, "swap_summary"),
                [(t(uid, "sent"), ucoin(uid, amount, asset)), (t(uid, "to_receive"), ucoin(uid, net, to_asset)), (t(uid, "fee"), ucoin(uid, fee, to_asset))],
                live_rate_note(uid),
            )
            require_pin(uid, state); return

    if flow == "deposit" and step == "sender_name":
        if len(text) < 3 or not any(ch.isalpha() for ch in text):
            send(chat_id, t(uid, "sender_name_invalid")); return
        state["sender_name"] = text
        state["step"] = "tx_note"
        send(chat_id, t(uid, "reference_question")); return

    if flow == "deposit" and step == "tx_note":
        try:
            rid = create_deposit_notice(uid, state, {"sender_name": state.get("sender_name", ""), "tx_note": text})
            user_state.pop(uid, None)
            send(chat_id, msg(uid, "deposit_received") + f"\n\n{request_summary(rid, uid)}", reply_keyboard(uid))
            if ADMIN_CHAT_ID: send(ADMIN_CHAT_ID, "Yeni bakiye yükleme bildirimi\n\n" + request_summary(rid, ""))
        except Exception as exc:
            print("TL DEPOSIT NOTICE ERROR:", exc)
            user_state.pop(uid, None)
            send(chat_id, t(uid, "operation_failed"), reply_keyboard(uid))
        return

    if flow == "withdraw" and step == "bank_name": state["bank_name"] = text; state["step"] = "iban"; send(chat_id, t(uid, "iban_question")); return
    if flow == "withdraw" and step == "iban": state["iban"] = text.replace(" ", "").upper(); state["step"] = "name"; send(chat_id, t(uid, "account_name_question")); return
    if flow == "withdraw" and step == "name":
        state["name"] = text
        state["preview"] = order_summary(t(uid, "withdraw_summary_tl"), [
            (t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])),
            (t(uid, "fee"), ucoin(uid, state["fee"], state["asset"])),
            (t(uid, "recipient_gets"), ucoin(uid, state["net_amount"], state["asset"])),
            ("IBAN", state["iban"]), (t(uid, "recipient"), state["name"]),
        ])
        require_pin(uid, state); return
    if flow == "withdraw" and step == "address":
        state["address"] = text
        state["preview"] = order_summary(t(uid, "withdraw_summary", asset=state["asset"]), [
            (t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])),
            (t(uid, "fee"), ucoin(uid, state["fee"], state["asset"])),
            (t(uid, "to_send"), ucoin(uid, state["net_amount"], state["asset"])),
            (t(uid, "wallet_address"), state["address"]),
        ])
        require_pin(uid, state); return
    if flow == "favorite_add" and step == "label": state["label"] = text; state["step"] = "address"; send(chat_id, t(uid, "favorite_address")); return
    if flow == "favorite_add" and step == "address":
        users[uid]["favorites"].append({"label": state["label"], "asset": state["asset"], "address": text, "created_at": now()})
        save_json(FILES["users"], users); user_state.pop(uid, None); send(chat_id, t(uid, "favorite_saved"), reply_keyboard(uid)); return


def handle_callback(chat_id, username, data, cb_id):
    uid = str(chat_id)
    get_user(chat_id, username)

    if data.startswith("lang:"):
        lang = data.split(":", 1)[1]
        if lang not in ("tr", "en"):
            answer(cb_id); return
        users[uid]["language"] = lang
        save_json(FILES["users"], users)
        answer(cb_id, BOT_TEXTS[lang]["language_saved"])
        user_state.pop(uid, None)
        start_user(chat_id, username)
        return

    if data != "second_confirm":
        answer(cb_id)
    if users[uid].get("language") not in ("tr", "en"):
        ask_language(chat_id); return
    if data == "cancel": user_state.pop(uid, None); send(chat_id, msg(uid, "request_cancelled"), reply_keyboard(uid)); return
    if data == "show_deposit_qr":
        state = user_state.get(uid, {})
        content = str(state.get("qr_content", "")).strip()
        if not content: send(chat_id, t(uid, "qr_missing")); return
        send_qr(chat_id, content, state.get("qr_caption", t(uid, "qr_caption"))); return
    if data.startswith("detail:"):
        rid = data.split(":", 1)[1]
        if requests_db.get(rid, {}).get("user_id") == uid:
            send(chat_id, receipt_text(rid, uid), {"inline_keyboard": [[inline_button(t(uid, "completed"), f"detail:{rid}")]]})
        return
    if data.startswith("deposit_asset:"):
        asset = data.split(":", 1)[1]
        current = user_state.get(uid, {})
        idem = current.get("idempotency_key", secrets.token_urlsafe(24))
        user_state[uid] = {"flow": "deposit", "step": "amount", "asset": asset, "idempotency_key": idem}
        send(chat_id, f"{asset}: {msg(uid, 'amount_question')}\n{t(uid, 'minimum')}: {ucoin(uid, min_amount('deposit', asset), asset)}"); return
    if data == "deposit_sent":
        state = user_state.get(uid, {})
        if state.get("flow") != "deposit" or state.get("step") != "waiting_sent" or not state.get("amount"):
            send(chat_id, t(uid, "deposit_session_missing"), reply_keyboard(uid)); return
        if state.get("asset") == "TL":
            state["step"] = "sender_name"
            send(chat_id, t(uid, "sender_name_question"))
        else:
            try:
                rid = create_deposit_notice(uid, state, {"network": state.get("network", "")})
                user_state.pop(uid, None)
                send(chat_id, msg(uid, "deposit_received") + f"\n\n{request_summary(rid, uid)}", reply_keyboard(uid))
                if ADMIN_CHAT_ID: send(ADMIN_CHAT_ID, "Yeni bakiye yükleme bildirimi\n\n" + request_summary(rid, ""))
            except Exception as exc:
                print("CRYPTO DEPOSIT NOTICE ERROR:", exc)
                user_state.pop(uid, None)
                send(chat_id, t(uid, "operation_failed"), reply_keyboard(uid))
        return
    if data.startswith("withdraw_asset:"):
        asset = data.split(":", 1)[1]
        if balance(uid, asset) <= 0: send(chat_id, msg(uid, "no_balance")); return
        user_state[uid] = {"flow": "withdraw", "step": "amount", "asset": asset}
        send(chat_id, f"{t(uid, 'available_balance')}: {ucoin(uid, balance(uid, asset), asset)}\n{t(uid, 'min_withdraw')}: {ucoin(uid, min_amount('withdraw', asset), asset)}\n\n{msg(uid, 'amount_question')}"); return
    if data.startswith("convert_from:"):
        asset = data.split(":", 1)[1]
        if balance(uid, asset) <= 0: send(chat_id, msg(uid, "no_balance")); return
        user_state[uid] = {"flow": "convert", "step": "to_asset", "from_asset": asset}
        send(chat_id, t(uid, "convert_available", amount=ucoin(uid, balance(uid, asset), asset)), asset_keyboard("convert_to", ASSETS, asset, uid)); return
    if data.startswith("convert_to:"):
        to_asset = data.split(":", 1)[1]
        state = user_state.get(uid, {})
        if state.get("flow") != "convert" or not state.get("from_asset"): send(chat_id, t(uid, "convert_session_missing")); return
        state.update({"to_asset": to_asset, "step": "amount"})
        source = state["from_asset"]
        send(chat_id, f"{t(uid, 'balance')} · {ucoin(uid, balance(uid, source), source)}\n{t(uid, 'minimum')} · {ucoin(uid, min_amount('convert', source), source)}\n\n{t(uid, 'enter_or_all')}", {"inline_keyboard": [[inline_button(t(uid, "convert_all"), "convert_all")], [inline_button(t(uid, "cancel"), "cancel")]]}); return
    if data == "convert_all":
        state = user_state.get(uid, {})
        if state.get("flow") != "convert" or not state.get("from_asset") or not state.get("to_asset"): send(chat_id, t(uid, "convert_session_missing")); return
        source, target = state["from_asset"], state["to_asset"]
        amount = balance(uid, source)
        if amount <= 0: send(chat_id, msg(uid, "no_balance")); return
        if amount < min_amount("convert", source): send(chat_id, f"{t(uid, 'minimum')} · {ucoin(uid, min_amount('convert', source), source)}"); return
        source_rate, target_rate = rate(source), rate(target)
        if source_rate <= 0 or target_rate <= 0: send(chat_id, t(uid, "invalid_rate")); return
        fee_rate = fee_percent("convert", uid=uid, from_asset=source, to_asset=target)
        if fee_rate < 0 or fee_rate >= 100: send(chat_id, t(uid, "invalid_fee")); return
        tl_value = amount * source_rate; gross = tl_value / target_rate; fee = fee_amount(gross, fee_rate); net = gross - fee
        if net <= 0: send(chat_id, t(uid, "invalid_net")); return
        state.update({"amount": str(amount), "tl_value": str(tl_value), "gross_to": str(gross), "fee": str(fee), "net_amount": str(net)})
        state["preview"] = order_summary(t(uid, "swap_summary"), [(t(uid, "sent"), ucoin(uid, amount, source)), (t(uid, "to_receive"), ucoin(uid, net, target)), (t(uid, "fee"), ucoin(uid, fee, target))], t(uid, "all_balance_note") + " " + live_rate_note(uid))
        require_pin(uid, state); return
    if data == "second_confirm":
        state = user_state.get(uid, {})
        if not consume_confirmation(state): answer(cb_id, t(uid, "already_confirmed")); return
        answer(cb_id)
        try:
            if state.get("flow") == "withdraw": finalize_withdraw(uid, state)
            elif state.get("flow") == "convert":
                if any(not state.get(key) for key in ("from_asset", "to_asset", "amount")): raise ValueError(t(uid, "missing_convert"))
                rid = atomic_convert(uid, state)
                user_state.pop(uid, None)
                send(chat_id, receipt_text(rid, uid), reply_keyboard(uid))
            else: raise ValueError(t(uid, "nothing_to_confirm"))
        except ValueError as exc:
            user_state.pop(uid, None); send(chat_id, str(exc), reply_keyboard(uid))
        except Exception as exc:
            print("TRANSACTION CONFIRM ERROR:", exc); user_state.pop(uid, None); send(chat_id, t(uid, "operation_failed"), reply_keyboard(uid))
        return
    if data == "favorite:add": user_state[uid] = {"flow": "favorite_add", "step": "asset"}; send(chat_id, t(uid, "favorite_asset"), asset_keyboard("favorite_asset", CRYPTO_ASSETS, uid=uid)); return
    if data.startswith("favorite_asset:"): user_state[uid] = {"flow": "favorite_add", "step": "label", "asset": data.split(":",1)[1]}; send(chat_id, t(uid, "favorite_label")); return
    if data.startswith("favorite_use:"):
        choice = data.split(":", 1)[1]; state = user_state.get(uid, {})
        if choice == "new": state["step"] = "address"; send(chat_id, t(uid, "wallet_address_question"))
        else:
            try: fav = users[uid]["favorites"][int(choice)]
            except (ValueError, IndexError): send(chat_id, t(uid, "session_missing")); return
            state["address"] = fav["address"]
            state["preview"] = order_summary(t(uid, "withdraw_summary", asset=state["asset"]), [(t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])), (t(uid, "fee"), ucoin(uid, state["fee"], state["asset"])), (t(uid, "to_send"), ucoin(uid, state["net_amount"], state["asset"])), (t(uid, "wallet_address"), state["address"])])
            require_pin(uid, state)
        return
    if data == "security:set_pin":
        if users[uid].get("pin_hash"): user_state[uid] = {"flow": "set_pin", "step": "old_pin"}; send(chat_id, t(uid, "current_pin"))
        else: user_state[uid] = {"flow": "set_pin", "step": "set_pin"}; send(chat_id, msg(uid, "pin_set_question"))
        return
    if data == "security:notifications":
        n = users[uid]["notifications"]
        kb = {"inline_keyboard": [
            [inline_button(f"{t(uid, 'transactions')}: {t(uid, 'on') if n['transactions'] else t(uid, 'off')}", "notify:transactions")],
            [inline_button(f"{t(uid, 'security')}: {t(uid, 'on') if n['security'] else t(uid, 'off')}", "notify:security")],
            [inline_button(f"{t(uid, 'announcements')}: {t(uid, 'on') if n['announcements'] else t(uid, 'off')}", "notify:announcements")],
        ]}
        send(chat_id, t(uid, "notifications_edit"), kb); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]
        if key in users[uid]["notifications"]:
            users[uid]["notifications"][key] = not users[uid]["notifications"].get(key, True)
            save_json(FILES["users"], users)
        send(chat_id, t(uid, "notification_updated")); return
    if data == "security:logout_sessions":
        users[uid]["sessions"] = {"telegram": {"created_at": now(), "last_seen": now(), "active": True}}
        save_json(FILES["users"], users); send(chat_id, t(uid, "sessions_closed")); return

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


PANEL_PERMISSION_LABELS = {
    "dashboard": "Genel bakış",
    "requests": "İşlem talepleri",
    "users": "Kullanıcı yönetimi",
    "broadcast": "Duyuru gönderme",
    "settings": "Ayar yönetimi",
    "logs": "Yönetim kayıtları",
    "admins": "Panel yetkilileri",
}

VIEW_PERMISSIONS = dict(PANEL_PERMISSION_LABELS)
ACTION_PERMISSIONS = {
    "settings": "settings",
    "process_request": "requests",
    "approve_request": "requests",
    "reject_request": "requests",
    "adjust_balance": "users",
    "update_user_profile": "users",
    "freeze_user": "users",
    "unfreeze_user": "users",
    "lock_withdraw": "users",
    "unlock_withdraw": "users",
    "broadcast": "broadcast",
    "create_panel_user": "admins",
    "update_panel_user": "admins",
    "delete_panel_user": "admins",
}


def current_panel_username():
    return str(session.get("panel_username", ""))


def panel_is_root():
    return session.get("panel_root") is True and secrets.compare_digest(current_panel_username(), PANEL_USERNAME)


def logged_in():
    if session.get("login") is not True:
        return False
    if panel_is_root():
        return True
    account = panel_users.get(current_panel_username().lower())
    return bool(account and account.get("active", True))


def has_panel_permission(permission):
    if not logged_in():
        return False
    if panel_is_root():
        return True
    account = panel_users.get(current_panel_username().lower(), {})
    return permission in set(account.get("permissions", []))


def panel_permissions_from_form():
    return sorted({value for value in request.form.getlist("permissions") if value in PANEL_PERMISSION_LABELS})


def authenticate_panel_account(username, password):
    username = str(username or "").strip()
    password = str(password or "")
    if secrets.compare_digest(username, PANEL_USERNAME) and secrets.compare_digest(password, PANEL_PASSWORD):
        return username, True
    account = panel_users.get(username.lower())
    if not account or not account.get("active", True):
        return "", False
    try:
        if check_password_hash(account.get("password_hash", ""), password):
            return username.lower(), False
    except (ValueError, TypeError):
        pass
    return "", False

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
        panel_username, is_root = authenticate_panel_account(request.form.get("username", ""), request.form.get("password", ""))
        if panel_username:
            attempts.clear()
            session.clear()
            session["login"] = True
            session["panel_username"] = panel_username
            session["panel_root"] = is_root
            session.permanent = True
            csrf_token()
            return redirect("/admin")
        attempts.append(time.time()); time.sleep(min(2 ** len(attempts), 8) / 10); error = "Kullanıcı adı veya şifre hatalı"
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'><title>Nerlo Wallet Yönetim</title><style>
    :root{{--bg:#070a10;--panel:#0f141d;--panel2:#141b26;--line:#242d3a;--text:#f5f7fb;--muted:#8f9bab;--accent:#6ee7d8;--accent2:#7dd3fc;--danger:#fb7185}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(circle at 15% 10%,rgba(110,231,216,.12),transparent 30%),radial-gradient(circle at 90% 90%,rgba(125,211,252,.1),transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.login{{width:min(420px,100%);background:rgba(15,20,29,.94);border:1px solid var(--line);border-radius:24px;padding:28px;box-shadow:0 30px 90px rgba(0,0,0,.42);backdrop-filter:blur(18px)}}.brand{{display:flex;align-items:center;gap:12px;margin-bottom:26px}}.mark{{width:42px;height:42px;display:grid;place-items:center;border-radius:14px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#051116;font-weight:900;font-size:20px}}h1{{font-size:22px;margin:0}}p{{margin:5px 0 0;color:var(--muted);font-size:14px}}label{{display:block;font-size:12px;font-weight:700;color:#b8c2cf;margin:16px 0 7px}}input,button{{width:100%;height:46px;border-radius:13px;border:1px solid var(--line);font:inherit}}input{{background:#0a0f16;color:var(--text);padding:0 14px;outline:none}}input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(110,231,216,.1)}}button{{margin-top:18px;border:0;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#061116;font-weight:850;cursor:pointer}}.error{{min-height:20px;color:var(--danger);font-size:13px;margin-top:12px}}</style></head><body><form class='login' method='post'><div class='brand'><div class='mark'>N</div><div><h1>Nerlo Wallet</h1><p>Yönetim paneli</p></div></div><label>Kullanıcı adı</label><input name='username' autocomplete='username' required><label>Şifre</label><input type='password' autocomplete='current-password' name='password' required><button>Giriş Yap</button><div class='error'>{h(error)}</div></form></body></html>"""

@app.route("/logout")
def logout(): session.clear(); return redirect("/login")


REQUEST_TYPE_LABELS = {
    "deposit": "Bakiye yükleme",
    "withdraw": "Para çekme",
    "convert": "Bakiye dönüştürme",
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
    "create_panel_user": "Panel yetkilisi oluşturma",
    "update_panel_user": "Panel yetkilisi güncelleme",
    "delete_panel_user": "Panel yetkilisi silme",
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


def transaction_kind_label(value):
    return TRANSACTION_KIND_LABELS.get(value, "Sistem işlemi")


def bucket_label(value):
    return BUCKET_LABELS.get(value, "Bilinmiyor")


def admin_action_label(value):
    return ADMIN_ACTION_LABELS.get(value, "Yönetim işlemi")


def localized_admin_detail(value):
    return str(value or "")


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
    match = re.fullmatch(r"fee_(deposit|withdraw)_(TL|USDT|LTC|TRX|XMR)_percent", key)
    if match:
        operation = "yükleme" if match.group(1) == "deposit" else "çekim"
        return f"{match.group(2)} {operation} komisyonu (%)"
    if key == "fee_convert_tl_percent":
        return "TL içeren dönüşüm komisyonu (%)"
    if key == "fee_convert_crypto_percent":
        return "Kripto → kripto dönüşüm komisyonu (%)"
    match = re.fullmatch(r"min_(deposit|withdraw|convert)_(TL|USDT|LTC|TRX|XMR)", key)
    if match:
        operation = {"deposit": "yükleme", "withdraw": "çekim", "convert": "dönüştürme"}[match.group(1)]
        return f"{match.group(2)} en düşük {operation} tutarı"
    match = re.fullmatch(r"daily_withdraw_limit_(TL|USDT|LTC|TRX|XMR)", key)
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
      <div class='profile-badges'><span class='pill {'danger-pill' if u.get('status') == 'frozen' else ''}'>{h(account_status_label(u.get('status')))}</span></div>
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


def permission_options(selected_permissions):
    selected = set(selected_permissions or [])
    return "".join(
        f"<label class='permission-option'><input type='checkbox' name='permissions' value='{h(key)}' {'checked' if key in selected else ''}><span>{h(label)}</span></label>"
        for key, label in PANEL_PERMISSION_LABELS.items()
    )


def render_panel_user_management():
    root_card = f"""
    <div class='admin-account root-account'>
      <div><span class='eyebrow'>ANA YÖNETİCİ</span><h3>{h(PANEL_USERNAME)}</h3><p>Railway ortam değişkenleriyle yönetilir ve tüm yetkilere sahiptir.</p></div>
      <span class='pill'>Tam yetki</span>
    </div>
    """
    account_cards = []
    for username, account in sorted(panel_users.items()):
        permissions = account.get("permissions", [])
        active = account.get("active", True)
        account_cards.append(f"""
        <form method='post' class='admin-account admin-account-form'>
          <input type='hidden' name='target_username' value='{h(username)}'>
          <input type='hidden' name='return_to' value='/admin?view=admins'>
          <div class='admin-account-head'>
            <div><span class='eyebrow'>PANEL YETKİLİSİ</span><h3>{h(username)}</h3><p>Oluşturulma: {h(account.get('created_at') or '-')} · Oluşturan: {h(account.get('created_by') or '-')}</p></div>
            <span class='pill {'danger-pill' if not active else ''}'>{'Pasif' if not active else 'Aktif'}</span>
          </div>
          <div class='admin-account-grid'>
            <div><label>Hesap durumu</label><select name='active'><option value='on' {'selected' if active else ''}>Aktif</option><option value='off' {'selected' if not active else ''}>Pasif</option></select></div>
            <div><label>Yeni şifre (değişmeyecekse boş bırakın)</label><input type='password' name='new_password' autocomplete='new-password' minlength='12' placeholder='En az 12 karakter'></div>
          </div>
          <label class='permission-title'>Yetkiler</label><div class='permission-grid'>{permission_options(permissions)}</div>
          <div class='admin-account-actions'><button class='btn primary' name='action' value='update_panel_user'>Yetkileri Kaydet</button><button class='btn danger' name='action' value='delete_panel_user'>Yetkiliyi Sil</button></div>
        </form>
        """)
    accounts_html = "".join(account_cards) or "<div class='empty-state'><b>Ek panel yetkilisi yok</b><span>Yeni yetkiliyi üstteki formdan oluşturabilirsiniz.</span></div>"
    return f"""
    <section class='panel-card compact-card'>
      <div class='section-head'><div><span class='eyebrow'>YENİ HESAP</span><h3>Panel Yetkilisi Ekle</h3></div><p>Şifre en az 12 karakter olmalıdır.</p></div>
      <form method='post'>
        <input type='hidden' name='action' value='create_panel_user'><input type='hidden' name='return_to' value='/admin?view=admins'>
        <div class='admin-account-grid'><div><label>Kullanıcı adı</label><input name='panel_username' minlength='3' maxlength='32' pattern='[a-z0-9_.-]{{3,32}}' placeholder='ornek.yonetici' required></div><div><label>Şifre</label><input type='password' name='panel_password' autocomplete='new-password' minlength='12' placeholder='En az 12 karakter' required></div></div>
        <label class='permission-title'>Yetkiler</label><div class='permission-grid'>{permission_options(PANEL_PERMISSION_LABELS.keys())}</div>
        <div class='save-bar'><button class='btn primary'>Yetkiliyi Oluştur</button></div>
      </form>
    </section>
    <div class='admin-account-list'>{root_card}{accounts_html}</div>
    """


@app.route("/admin/requests-fragment")
def admin_requests_fragment():
    if not logged_in():
        return "", 401
    if not has_panel_permission("requests"):
        return "", 403
    return render_request_list(
        request.args.get("rq", "").strip(),
        request.args.get("status", "all"),
        request.args.get("type", "all"),
    )


@app.route("/admin/user-fragment")
def admin_user_fragment():
    if not logged_in():
        return "", 401
    if not has_panel_permission("users"):
        return "", 403
    return render_user_management(request.args.get("uid", ""))


def safe_admin_return(default="/admin"):
    target = str(request.form.get("return_to", default) or default)
    if not target.startswith("/admin") or target.startswith("//"):
        return default
    return target


def set_admin_notice(message, kind="success"):
    session["admin_notice"] = {"message": str(message), "kind": kind}


EDITABLE_SETTING_KEYS = [key for key in DEFAULT_SETTINGS if not key.startswith("icon_") and key not in {"rates_source", "rates_last_updated", "rates_last_error"}]


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in(): return redirect("/login")
    if request.method == "POST":
        action = request.form.get("action", "")
        required_permission = ACTION_PERMISSIONS.get(action)
        if not required_permission:
            abort(400)
        if not has_panel_permission(required_permission):
            abort(403)
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
                    send(uid, receipt_text(rid, uid), reply_keyboard(uid))
                    set_admin_notice(f"#{rid} tamamlandı.")
                elif action == "reject_request" and r.get("status") in ("pending", "processing"):
                    if r["type"] == "withdraw":
                        change_pending(uid, r["asset"], -D(r["amount"]), "withdraw_pending_cancel", rid)
                        change_balance(uid, r["asset"], r["amount"], "withdraw_refund", rid)
                    elif r["type"] == "deposit":
                        change_pending(uid, r["asset"], -D(r["net_amount"]), "deposit_pending_cancel", rid)
                    r["status"] = "rejected"; r["rejected_at"] = now()
                    send(uid, f"{t(uid, 'request_rejected')}\n\n#{rid}", reply_keyboard(uid))
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
                users[uid]["custom_fee_percent"] = request.form.get("custom_fee_percent", "").strip()
                users[uid]["custom_daily_limit_TL"] = request.form.get("custom_daily_limit_TL", "").strip()
                users[uid]["note"] = request.form.get("note", "").strip()
                save_json(FILES["users"], users)
                add_admin_log("update_user_profile", "Kullanıcı ayarları güncellendi", uid)
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
                        send(uid, "📢 " + announcement, reply_keyboard(uid)); count += 1
                add_admin_log("broadcast", f"{count} kullanıcıya gönderildi")
                set_admin_notice(f"Duyuru {count} kullanıcıya gönderildi.")
            else:
                set_admin_notice("Duyuru metni boş olamaz.", "error")
        elif action == "create_panel_user":
            username = request.form.get("panel_username", "").strip().lower()
            password = request.form.get("panel_password", "")
            permissions = panel_permissions_from_form()
            if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
                set_admin_notice("Kullanıcı adı 3-32 karakter olmalı; yalnızca küçük harf, rakam, nokta, alt çizgi ve tire kullanılabilir.", "error")
            elif username == PANEL_USERNAME.lower() or username in panel_users:
                set_admin_notice("Bu panel kullanıcı adı zaten kullanılıyor.", "error")
            elif len(password) < 12:
                set_admin_notice("Panel şifresi en az 12 karakter olmalıdır.", "error")
            elif not permissions:
                set_admin_notice("En az bir yetki seçmelisiniz.", "error")
            else:
                panel_users[username] = {
                    "username": username,
                    "password_hash": generate_password_hash(password, method="scrypt"),
                    "permissions": permissions,
                    "active": True,
                    "created_at": now(),
                    "created_by": current_panel_username(),
                    "updated_at": now(),
                }
                save_json(FILES["panel_users"], panel_users)
                add_admin_log("create_panel_user", f"Panel yetkilisi: {username}; yetkiler: {', '.join(permissions)}")
                set_admin_notice(f"{username} panel yetkilisi oluşturuldu.")
        elif action == "update_panel_user":
            target = request.form.get("target_username", "").strip().lower()
            account = panel_users.get(target)
            permissions = panel_permissions_from_form()
            active = request.form.get("active", "on") == "on"
            new_password = request.form.get("new_password", "")
            if not account:
                set_admin_notice("Panel yetkilisi bulunamadı.", "error")
            elif not permissions:
                set_admin_notice("En az bir yetki seçmelisiniz.", "error")
            elif target == current_panel_username().lower() and not active:
                set_admin_notice("Kendi panel hesabınızı pasif duruma getiremezsiniz.", "error")
            elif new_password and len(new_password) < 12:
                set_admin_notice("Yeni şifre en az 12 karakter olmalıdır.", "error")
            else:
                account["permissions"] = permissions
                account["active"] = active
                account["updated_at"] = now()
                account["updated_by"] = current_panel_username()
                if new_password:
                    account["password_hash"] = generate_password_hash(new_password, method="scrypt")
                    account["password_changed_at"] = now()
                save_json(FILES["panel_users"], panel_users)
                add_admin_log("update_panel_user", f"Panel yetkilisi: {target}; yetkiler: {', '.join(permissions)}; durum: {'aktif' if active else 'pasif'}")
                set_admin_notice(f"{target} yetkileri güncellendi.")
        elif action == "delete_panel_user":
            target = request.form.get("target_username", "").strip().lower()
            if target == current_panel_username().lower():
                set_admin_notice("Kendi panel hesabınızı silemezsiniz.", "error")
            elif target not in panel_users:
                set_admin_notice("Panel yetkilisi bulunamadı.", "error")
            else:
                panel_users.pop(target, None)
                save_json(FILES["panel_users"], panel_users)
                add_admin_log("delete_panel_user", f"Panel yetkilisi silindi: {target}")
                set_admin_notice(f"{target} panel yetkilisi silindi.")
        return redirect(safe_admin_return())

    allowed_views = [key for key in PANEL_PERMISSION_LABELS if has_panel_permission(key)]
    if not allowed_views:
        return "Bu panel hesabına herhangi bir yetki tanımlanmamış.", 403
    active_view = request.args.get("view", allowed_views[0])
    if active_view not in allowed_views:
        active_view = allowed_views[0]
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
        ("admins", "Panel Yetkilileri", "07"),
    ]
    nav_html = "".join(
        f"<button class='nav-item {'active' if slug == active_view else ''}' data-view-target='{slug}'><span>{number}</span>{label}</button>"
        for slug, label, number in nav_items if slug in allowed_views
    )

    dashboard_section = f"""<section class='page-view {'active' if active_view == 'dashboard' else ''}' data-view='dashboard'><div class='section-head'><div><span class='eyebrow'>GENEL BAKIŞ</span><h2>Cüzdan Özeti</h2><p>Tüm kullanıcı bakiyelerinin kompakt görünümü</p></div></div><div class='dashboard-grid'>{asset_metrics}</div><div class='summary-grid'><div class='summary-card'><span>Toplam kullanıcı</span><strong>{len(users)}</strong></div><div class='summary-card'><span>Bekleyen / işlenen talep</span><strong>{pending_count}</strong></div><div class='summary-card'><span>Bugün tamamlanan</span><strong>{completed_today}</strong></div></div></section>""" if "dashboard" in allowed_views else ""
    requests_section = f"""<section class='page-view {'active' if active_view == 'requests' else ''}' data-view='requests'><div class='section-head'><div><span class='eyebrow'>OPERASYON</span><h2>İşlem Talepleri</h2><p>Yükleme, çekim ve dönüşüm taleplerini tek ekrandan yönetin</p></div></div><div class='panel-card'><form id='request-filter' class='toolbar'><input name='rq' value='{h(request_query)}' placeholder='İşlem no, kullanıcı ID veya kullanıcı adı'><select name='status'><option value='all'>Tüm durumlar</option>{''.join(f"<option value='{s}' {'selected' if status_filter == s else ''}>{status_label(s)}</option>" for s in ['pending','processing','completed','rejected'])}</select><select name='type'><option value='all'>Tüm işlem türleri</option>{''.join(f"<option value='{t}' {'selected' if type_filter == t else ''}>{request_type_label(t)}</option>" for t in ['deposit','withdraw','convert'])}</select><button class='btn primary'>Filtrele</button></form><div id='request-list' class='request-list'>{render_request_list(request_query, status_filter, type_filter)}</div></div></section>""" if "requests" in allowed_views else ""
    users_section = f"""<section class='page-view {'active' if active_view == 'users' else ''}' data-view='users'><div class='section-head'><div><span class='eyebrow'>KULLANICI YÖNETİMİ</span><h2>ID ile Kullanıcı Aç</h2><p>Kullanıcı satırlarına tıklamadan doğrudan kimlik ile yönetin</p></div></div><div class='panel-card'><form id='user-lookup' class='lookup-bar'><input id='manage-user-id' name='uid' value='{h(manage_user_id)}' inputmode='numeric' placeholder='Telegram kullanıcı ID'><button class='btn primary'>Kullanıcıyı Getir</button></form><div id='user-management-result'>{render_user_management(manage_user_id)}</div></div><div class='panel-card' style='margin-top:10px'><div class='section-head'><div><span class='eyebrow'>SON KULLANICILAR</span><h3>Hızlı Referans</h3></div><p>ID değerleri bağlantı değildir</p></div><div class='table-wrap'><table><thead><tr><th>Kullanıcı ID</th><th>Kullanıcı adı</th><th>TL</th><th>USDT</th><th>LTC</th><th>TRX</th><th>Hesap</th><th>Son görülme</th></tr></thead><tbody>{user_rows}</tbody></table></div></div></section>""" if "users" in allowed_views else ""
    broadcast_section = f"""<section class='page-view {'active' if active_view == 'broadcast' else ''}' data-view='broadcast'><div class='section-head'><div><span class='eyebrow'>İLETİŞİM</span><h2>Duyuru Gönder</h2><p>Bildirimleri açık kullanıcılara toplu mesaj gönderin</p></div></div><div class='broadcast-grid'><form method='post' class='panel-card'><input type='hidden' name='action' value='broadcast'><input type='hidden' name='return_to' value='/admin?view=broadcast'><label>Duyuru metni</label><textarea name='announcement_text' placeholder='Kullanıcılara gönderilecek mesajı yazın' required></textarea><button class='btn primary' style='width:100%;margin-top:10px'>Duyuruyu Gönder</button></form><div class='broadcast-note'><b style='color:#dce5ef'>Gönderim bilgisi</b><br><br>Duyuru yalnızca duyuru bildirimleri açık olan kullanıcılara iletilir. Gönderim sonucu yönetim kayıtlarına eklenir.</div></div></section>""" if "broadcast" in allowed_views else ""
    settings_section = f"""<section class='page-view {'active' if active_view == 'settings' else ''}' data-view='settings'><div class='section-head'><div><span class='eyebrow'>SİSTEM</span><h2>Ayar Yönetimi</h2><p>Kur, limit, cüzdan, sistem ve bot mesajlarını yönetin</p></div></div><form method='post' class='panel-card'><input type='hidden' name='action' value='settings'><input type='hidden' name='return_to' value='/admin?view=settings'><div class='settings-nav'>{settings_tabs}</div>{''.join(settings_panes)}<div class='save-bar'><button class='btn primary'>Tüm Ayarları Kaydet</button></div></form></section>""" if "settings" in allowed_views else ""
    logs_section = f"""<section class='page-view {'active' if active_view == 'logs' else ''}' data-view='logs'><div class='section-head'><div><span class='eyebrow'>DENETİM</span><h2>Yönetim Kayıtları</h2><p>Son 120 yönetici işlemi</p></div></div><div class='panel-card'><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Kullanıcı</th><th>Detay</th></tr></thead><tbody>{logs}</tbody></table></div></div></section>""" if "logs" in allowed_views else ""
    admins_section = f"""<section class='page-view {'active' if active_view == 'admins' else ''}' data-view='admins'><div class='section-head'><div><span class='eyebrow'>ERİŞİM YÖNETİMİ</span><h2>Panel Yetkilileri</h2><p>Yeni panel kullanıcısı oluşturun ve bölüm bazlı yetkilerini düzenleyin</p></div></div>{render_panel_user_management()}</section>""" if "admins" in allowed_views else ""

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'><title>Nerlo Wallet Yönetim</title><style>
    :root{{--bg:#090c12;--sidebar:#0b0f16;--surface:#10151e;--surface-2:#141b25;--surface-3:#0c1118;--line:#222b38;--line-soft:#1a2230;--text:#f4f7fb;--muted:#8c98a8;--muted-2:#667386;--accent:#68e0d2;--accent-2:#7cc7ff;--success:#59d99b;--warning:#f6c96b;--danger:#ff7489;--radius:18px}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 85% -10%,rgba(104,224,210,.08),transparent 32%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}}button,input,select,textarea{{font:inherit}}button{{cursor:pointer}}.app-shell{{min-height:100vh;display:grid;grid-template-columns:238px minmax(0,1fr)}}.sidebar{{position:sticky;top:0;height:100vh;background:rgba(11,15,22,.96);border-right:1px solid var(--line-soft);padding:20px 14px;display:flex;flex-direction:column;backdrop-filter:blur(18px)}}.brand{{display:flex;align-items:center;gap:11px;padding:6px 8px 22px}}.brand-mark{{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116;font-weight:950;font-size:18px;box-shadow:0 10px 30px rgba(104,224,210,.14)}}.brand strong{{display:block;font-size:15px}}.brand small{{display:block;color:var(--muted);margin-top:2px;font-size:11px}}.nav{{display:grid;gap:4px}}.nav-item{{width:100%;border:0;background:transparent;color:#aeb8c6;display:flex;align-items:center;gap:10px;padding:10px 11px;border-radius:11px;text-align:left;font-weight:680}}.nav-item span{{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;background:#111923;color:#66778c;font-size:10px}}.nav-item:hover{{background:#111822;color:#fff}}.nav-item.active{{background:#151e29;color:#fff}}.nav-item.active span{{background:rgba(104,224,210,.13);color:var(--accent)}}.sidebar-foot{{margin-top:auto;padding:14px 8px 2px;border-top:1px solid var(--line-soft)}}.version{{display:block;color:var(--muted-2);font-size:10px;margin-bottom:10px;letter-spacing:.06em}}.logout{{color:#aeb8c6;text-decoration:none;font-size:12px}}.main{{min-width:0;padding:22px clamp(16px,3vw,36px) 40px}}.topbar{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}}.topbar h1{{font-size:22px;margin:0;letter-spacing:-.03em}}.topbar p{{margin:5px 0 0;color:var(--muted);font-size:12px}}.top-pill{{padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--surface-3);font-size:11px}}.page-view{{display:none}}.page-view.active{{display:block}}.section-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px}}.section-head h2,.section-head h3{{margin:2px 0 0;letter-spacing:-.025em}}.section-head h2{{font-size:18px}}.section-head h3{{font-size:15px}}.section-head p{{margin:3px 0 0;color:var(--muted);font-size:12px}}.eyebrow{{display:block;color:var(--muted-2);font-size:9px;font-weight:850;letter-spacing:.13em}}.panel-card{{background:rgba(16,21,30,.92);border:1px solid var(--line);border-radius:var(--radius);padding:17px;box-shadow:0 16px 50px rgba(0,0,0,.14)}}.compact-card{{padding:15px}}.dashboard-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.wallet-metric{{min-height:108px;background:linear-gradient(145deg,#111823,#0d131c);border:1px solid var(--line);border-radius:16px;padding:14px;display:flex;gap:11px;align-items:flex-start}}.asset-dot{{width:30px;height:30px;flex:0 0 auto;border-radius:10px;background:rgba(104,224,210,.1);color:var(--accent);display:grid;place-items:center;font-size:11px;font-weight:900}}.wallet-metric span{{display:block;color:var(--muted);font-size:10px}}.wallet-metric strong{{display:block;font-size:18px;margin:5px 0 3px;letter-spacing:-.03em;white-space:nowrap}}.wallet-metric small{{color:var(--muted-2);font-size:10px}}.summary-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px}}.summary-card{{padding:13px 14px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3)}}.summary-card span{{color:var(--muted);font-size:10px}}.summary-card strong{{display:block;font-size:20px;margin-top:4px}}.toolbar{{display:grid;grid-template-columns:minmax(220px,2fr) repeat(2,minmax(150px,1fr)) auto;gap:9px;margin-bottom:13px}}input,select,textarea{{width:100%;border:1px solid var(--line);background:#0b1017;color:var(--text);border-radius:11px;min-height:41px;padding:9px 11px;outline:none}}input:focus,select:focus,textarea:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(104,224,210,.08)}}textarea{{min-height:110px;resize:vertical}}label{{display:block;color:#aab5c4;font-size:10px;font-weight:760;margin:0 0 6px}}.btn{{border:1px solid transparent;min-height:38px;border-radius:10px;padding:8px 12px;font-weight:800;background:#192431;color:#dfe8f3}}.btn.primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116}}.btn.ghost{{background:#111923;border-color:var(--line);color:#c9d3df}}.btn.success{{background:rgba(89,217,155,.13);border-color:rgba(89,217,155,.23);color:#88ebba}}.btn.danger{{background:rgba(255,116,137,.12);border-color:rgba(255,116,137,.22);color:#ff96a6}}.request-list{{display:grid;gap:9px}}.request-item{{background:var(--surface-3);border:1px solid var(--line);border-radius:15px;padding:13px}}.request-title{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.request-title h3{{font-size:14px;margin:3px 0 0}}.request-details{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:11px}}.request-detail{{min-width:0;background:#101721;border:1px solid var(--line-soft);border-radius:10px;padding:8px 9px}}.request-detail span{{display:block;color:var(--muted-2);font-size:9px;margin-bottom:4px}}.request-detail b{{display:block;font-size:11px;overflow-wrap:anywhere}}.request-actions{{display:flex;justify-content:flex-end;gap:7px;margin-top:10px}}.request-actions .btn{{width:auto;min-height:34px;font-size:11px}}.status{{display:inline-flex;align-items:center;justify-content:center;min-height:25px;padding:4px 8px;border-radius:999px;font-size:9px;font-weight:850;white-space:nowrap}}.status.waiting{{background:rgba(246,201,107,.12);color:var(--warning)}}.status.working{{background:rgba(124,199,255,.12);color:var(--accent-2)}}.status.done{{background:rgba(89,217,155,.12);color:var(--success)}}.status.declined{{background:rgba(255,116,137,.12);color:var(--danger)}}.empty-state{{min-height:140px;border:1px dashed #2a3442;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:var(--muted);gap:5px;padding:20px}}.empty-state b{{color:#dbe4ee}}.error-state{{border-color:rgba(255,116,137,.3)}}.lookup-bar{{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:9px;margin-bottom:12px}}.lookup-bar .btn{{min-width:130px}}.user-profile-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin:2px 0 13px}}.user-profile-head h2{{font-size:19px;margin:3px 0}}.user-profile-head p{{margin:0;color:var(--muted);font-size:11px}}.profile-badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.pill{{padding:6px 9px;border-radius:999px;background:#151e29;border:1px solid var(--line);color:#cbd5e1;font-size:9px;font-weight:800}}.danger-pill{{color:var(--danger);background:rgba(255,116,137,.08)}}.mini-balance-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:10px}}.mini-balance{{background:#0d141d;border:1px solid var(--line);border-radius:13px;padding:11px}}.mini-balance div{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.mini-balance span{{font-size:10px;color:var(--muted)}}.mini-balance strong{{font-size:13px;white-space:nowrap}}.mini-balance small{{display:block;color:var(--muted-2);font-size:9px;margin-top:7px}}.user-workspace{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}.history-grid{{align-items:start}}.form-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-items:end}}.form-grid .wide{{grid-column:span 2}}.submit-cell{{display:flex;align-items:flex-end}}.submit-cell .btn{{width:100%}}.balance-form{{grid-template-columns:repeat(3,minmax(0,1fr))}}.security-actions{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.security-actions .btn{{width:100%}}.table-wrap{{overflow:auto;border:1px solid var(--line-soft);border-radius:12px}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line-soft);font-size:10px;white-space:nowrap}}th{{color:var(--muted-2);font-size:9px;letter-spacing:.04em;background:#0c121a}}td{{color:#cbd5df}}tbody tr:last-child td{{border-bottom:0}}code{{color:#c8d5e4;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px}}.muted-cell{{text-align:center;color:var(--muted)}}.broadcast-grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:10px}}.broadcast-note{{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3);color:var(--muted);font-size:12px;line-height:1.55}}.settings-nav{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}.setting-tab{{border:1px solid var(--line);background:#0c1219;color:#95a2b2;padding:8px 10px;border-radius:9px;font-size:10px;font-weight:800}}.setting-tab.active{{background:#17222d;color:var(--accent);border-color:#29404a}}.setting-pane{{display:none}}.setting-pane.active{{display:block}}.settings-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}}.field{{min-width:0}}.settings-grid textarea{{min-height:90px}}.save-bar{{display:flex;justify-content:flex-end;margin-top:13px}}.save-bar .btn{{min-width:180px}}.admin-account-list{{display:grid;gap:10px;margin-top:10px}}.admin-account{{background:var(--surface-3);border:1px solid var(--line);border-radius:15px;padding:15px}}.root-account{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}.admin-account h3{{margin:3px 0;font-size:15px}}.admin-account p{{margin:0;color:var(--muted);font-size:10px}}.admin-account-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}}.admin-account-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.permission-title{{margin-top:13px}}.permission-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}}.permission-option{{display:flex;align-items:center;gap:8px;margin:0;padding:9px 10px;border:1px solid var(--line-soft);border-radius:10px;background:#0d141d;color:#c4cfdb;font-size:10px;cursor:pointer}}.permission-option input{{width:auto;min-height:0;margin:0;accent-color:var(--accent)}}.admin-account-actions{{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}}.toast{{position:fixed;right:22px;top:18px;z-index:20;max-width:min(360px,calc(100vw - 32px));padding:11px 14px;border:1px solid rgba(89,217,155,.25);background:#10231c;color:#9aebc0;border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.35);font-size:12px}}.toast-error{{background:#28131a;color:#ff9bad;border-color:rgba(255,116,137,.28)}}@media(max-width:1180px){{.permission-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.dashboard-grid,.mini-balance-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.request-details{{grid-template-columns:repeat(2,minmax(0,1fr))}}.settings-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:880px){{.app-shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;padding:12px}}.brand{{padding-bottom:12px}}.nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.nav-item{{justify-content:center;font-size:11px}}.sidebar-foot{{display:none}}.main{{padding-top:14px}}.user-workspace,.broadcast-grid{{grid-template-columns:1fr}}.toolbar{{grid-template-columns:1fr 1fr}}.toolbar input{{grid-column:1/-1}}.security-actions{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{.admin-account-grid,.permission-grid{{grid-template-columns:1fr}}.admin-account-actions{{display:grid}}.root-account,.admin-account-head{{display:block}}.root-account .pill,.admin-account-head .pill{{display:inline-flex;margin-top:9px}}.topbar{{align-items:flex-start}}.top-pill{{display:none}}.dashboard-grid,.summary-grid,.mini-balance-grid{{grid-template-columns:1fr}}.nav{{grid-template-columns:repeat(2,minmax(0,1fr))}}.nav-item span{{display:none}}.toolbar,.lookup-bar,.settings-grid,.form-grid,.balance-form{{grid-template-columns:1fr}}.form-grid .wide{{grid-column:auto}}.request-details{{grid-template-columns:1fr}}.request-actions{{display:grid;grid-template-columns:1fr}}.request-actions .btn{{width:100%}}.user-profile-head{{display:block}}.profile-badges{{justify-content:flex-start;margin-top:10px}}.security-actions{{grid-template-columns:1fr}}}}
    </style></head><body>{notice_html}<div class='app-shell'><aside class='sidebar'><div class='brand'><div class='brand-mark'>N</div><div><strong>Nerlo Wallet</strong><small>Yönetim Merkezi</small></div></div><nav class='nav'>{nav_html}</nav><div class='sidebar-foot'><span class='version'>NERLO-PANEL-2026.06.24-R2</span><a class='logout' href='/logout'>Güvenli çıkış</a></div></aside><main class='main'><header class='topbar'><div><h1>Kontrol Merkezi</h1><p>Kullanıcı, bakiye ve işlem operasyonları</p></div><span class='top-pill'>{h(current_panel_username())} · {h(now())}</span></header>

    {dashboard_section}

    {requests_section}

    {users_section}

    {broadcast_section}

    {settings_section}

    {logs_section}

    {admins_section}
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
    const requestView=document.querySelector('[data-view="requests"]');
    setInterval(()=>{{if(requestView&&requestView.classList.contains('active'))refreshRequests();}},20000);
    document.addEventListener('visibilitychange',()=>{{if(!document.hidden&&requestView&&requestView.classList.contains('active'))refreshRequests();}});

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
    if not has_panel_permission("users"): abort(403)
    return redirect(f"/admin?view=users&manage_user_id={uid}")


if __name__ == "__main__":
    validate_runtime_config()
    threading.Thread(target=bot_loop, daemon=True, name="telegram-bot").start()
    threading.Thread(target=rate_update_loop, daemon=True, name="live-rates").start()
    app.run(host="0.0.0.0", port=PORT)


@app.route("/panel/custom-id", methods=["GET","POST"])
def custom_id_panel():
    if request.method == "POST":
        for k in ["icon_TL","icon_USDT","icon_TRX","icon_LTC","icon_XMR"]:
            if k in request.form:
                settings[k] = request.form.get(k,"")
        save_json(FILES["settings"], settings)
        return redirect("/panel/custom-id")

    return '''
    <h3>Custom Emoji ID Panel</h3>
    <form method="post">
        TL: <input name="icon_TL" value="{TL}"><br>
        USDT: <input name="icon_USDT" value="{USDT}"><br>
        TRX: <input name="icon_TRX" value="{TRX}"><br>
        LTC: <input name="icon_LTC" value="{LTC}"><br>
        XMR: <input name="icon_XMR" value="{XMR}"><br>
        <button type="submit">Save</button>
    </form>
    '''.format(
        TL=settings.get("icon_TL",""),
        USDT=settings.get("icon_USDT",""),
        TRX=settings.get("icon_TRX",""),
        LTC=settings.get("icon_LTC",""),
        XMR=settings.get("icon_XMR","")
    )