import os
import time
import json
import random
import hashlib
import threading
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from html import escape

import requests
from flask import Flask, request, redirect, session, url_for

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
PORT = int(os.getenv("PORT", "8080"))
OFFSET = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-in-railway")

data_lock = threading.Lock()
user_state = {}

ASSETS = ["TL", "USDT", "LTC", "TRX"]
CRYPTO_ASSETS = ["USDT", "LTC", "TRX"]

FILES = {
    "users": "users.json",
    "requests": "requests.json",
    "transactions": "transactions.json",
    "settings": "settings.json",
    "messages": "messages.json",
    "admin_logs": "admin_logs.json",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filename)


def D(value, fallback="0"):
    try:
        if value is None or value == "":
            value = fallback
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return Decimal(fallback)


def fmt_amount(value, asset=""):
    d = D(value)
    if asset == "TL":
        q = d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return f"{q} TL"
    if asset == "USDT":
        q = d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return f"{q} USDT"
    if asset == "TRX":
        q = d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return f"{q} TRX"
    if asset == "LTC":
        q = d.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        return f"{q} LTC"
    return str(d.normalize())


def fmt_plain(value, asset=""):
    d = D(value)
    if asset == "LTC":
        return str(d.quantize(Decimal("0.000001"), rounding=ROUND_DOWN))
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def h(value):
    return escape(str(value if value is not None else ""), quote=True)


DEFAULT_MESSAGES = {
    "welcome": "ZaqelV2'ye hoş geldiniz.\n\nCüzdanınızı yönetmek için aşağıdaki menüden devam edebilirsiniz.",
    "wallet_title": "Cüzdan Bakiyeleriniz",
    "deposit_menu": "Yüklemek istediğiniz bakiye türünü seçiniz.",
    "withdraw_menu": "Çekmek istediğiniz bakiye türünü seçiniz.",
    "convert_menu": "Dönüştürmek istediğiniz bakiyeyi seçiniz.",
    "amount_question": "İşlem tutarını giriniz.",
    "pin_question": "İşlem PIN'inizi giriniz.",
    "pin_set_question": "Güvenliğiniz için 4-6 haneli bir işlem PIN'i belirleyiniz.",
    "pin_wrong": "PIN hatalı. Lütfen tekrar deneyiniz.",
    "pin_saved": "İşlem PIN'iniz kaydedildi.",
    "insufficient_balance": "Bakiyeniz bu işlem için yetersiz.",
    "request_created": "Talebiniz oluşturuldu ve incelemeye alındı.",
    "request_cancelled": "İşlem iptal edildi.",
    "support": "Destek talebi oluşturabilirsiniz.",
    "history_empty": "Henüz işlem geçmişiniz bulunmuyor.",
    "iban_warning": "Ödeme açıklamasına göndericiye ait TC Kimlik Numarasının yazılması zorunludur. TC Kimlik Numarası bulunmayan ödemeler işleme alınmayabilir.",
}

DEFAULT_SETTINGS = {
    "bank_name": "Zaqel Test Bank",
    "iban": "TR3300062000000000066295784",
    "iban_owner": "ZAQEL TEST HESABI",
    "wallet_USDT": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE",
    "wallet_TRX": "TGzz8gjYiYRqpfmDwnLxfgPuLVNmpCswVp",
    "wallet_LTC": "ltc1q2fx6cdwx8vx3n5psd7f8k0z0w7rx0y9k4q0s8r",
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
    "daily_withdraw_limit_TL": "50000",
    "daily_withdraw_limit_USDT": "1000",
    "daily_withdraw_limit_LTC": "10",
    "daily_withdraw_limit_TRX": "50000",
    # Menü / işlem custom emoji ID'leri
    "icon_wallet": "5895439304976506343",
    "icon_deposit": "5895549153060069171",
    "icon_withdraw": "5895506164732403256",
    "icon_convert": "5895671971944866108",
    "icon_history": "5895533287450877887",
    "icon_support": "5895698390288703053",
    "icon_fees": "5895334439055009075",
    "icon_info": "5895656948149263789",
    "icon_swap": "5893252234614939371",

    # Durum custom emoji ID'leri
    "icon_pending": "5895304795190730655",
    "icon_processing": "5895589615946964496",
    "icon_security": "5895439304976506343",
    "icon_completed": "5893391786692323248",
    "icon_rejected": "5895319286410387652",

    # Coin custom emoji ID'leri
    "icon_USDT": "5895571353746021767",
    "icon_LTC": "5895441495409828662",
    "icon_TRX": "5895440778150288520",
    "icon_TL": "",
}

users = load_json(FILES["users"], {})
requests_db = load_json(FILES["requests"], {})
transactions = load_json(FILES["transactions"], {})
settings = load_json(FILES["settings"], {})
messages = load_json(FILES["messages"], {})
admin_logs = load_json(FILES["admin_logs"], [])

for k, v in DEFAULT_SETTINGS.items():
    settings.setdefault(k, v)
for k, v in DEFAULT_MESSAGES.items():
    messages.setdefault(k, v)

save_json(FILES["settings"], settings)
save_json(FILES["messages"], messages)


def api(method, data):
    if not TOKEN:
        print("BOT_TOKEN missing")
        return {}
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=data, timeout=30).json()
    except Exception as exc:
        print("TELEGRAM API ERROR:", exc)
        return {}


def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": str(text)}
    if keyboard:
        payload["reply_markup"] = keyboard
    return api("sendMessage", payload)


def answer(cb_id, text=""):
    data = {"callback_query_id": cb_id}
    if text:
        data["text"] = text
    return api("answerCallbackQuery", data)


def reply_keyboard():
    return {
        "keyboard": [
            [{"text": "Cüzdanım"}, {"text": "Bakiye Yükle"}],
            [{"text": "Para Çek"}, {"text": "Dönüştür"}],
            [{"text": "İşlem Geçmişi"}, {"text": "Destek"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def inline_button(text, data, icon_key=None):
    button = {"text": text, "callback_data": data}
    emoji_id = str(settings.get(icon_key or "", "")).strip()
    if emoji_id:
        button["icon_custom_emoji_id"] = emoji_id
    return button


def asset_keyboard(prefix, include_tl=True, exclude=None):
    assets = ASSETS if include_tl else CRYPTO_ASSETS
    rows = []
    for asset in assets:
        if asset == exclude:
            continue
        rows.append([inline_button(asset, f"{prefix}:{asset}", f"icon_{asset}")])
    rows.append([inline_button("İptal", "cancel")])
    return {"inline_keyboard": rows}


def confirm_keyboard(ok_data, cancel_data="cancel"):
    return {"inline_keyboard": [[inline_button("Onayla", ok_data), inline_button("İptal", cancel_data)]]}


def hash_pin(pin):
    salt = os.getenv("PIN_SALT", "zaqelv2-pin-salt")
    return hashlib.sha256((salt + str(pin)).encode("utf-8")).hexdigest()


def get_user(chat_id, username=""):
    uid = str(chat_id)
    if uid not in users:
        users[uid] = {
            "chat_id": uid,
            "username": username or "unknown",
            "created_at": now(),
            "status": "active",
            "pin_hash": "",
            "balances": {asset: "0" for asset in ASSETS},
            "limits": {},
            "note": "",
        }
        save_json(FILES["users"], users)
    else:
        if username and users[uid].get("username") != username:
            users[uid]["username"] = username
            save_json(FILES["users"], users)
    return users[uid]


def user_balance(uid, asset):
    return D(users.get(str(uid), {}).get("balances", {}).get(asset, "0"))


def change_balance(uid, asset, amount, reason, ref_id=""):
    uid = str(uid)
    amount_d = D(amount)
    users[uid]["balances"][asset] = str(user_balance(uid, asset) + amount_d)
    tid = str(random.randint(100000, 999999))
    while tid in transactions:
        tid = str(random.randint(100000, 999999))
    transactions[tid] = {
        "id": tid,
        "user_id": uid,
        "asset": asset,
        "amount": str(amount_d),
        "reason": reason,
        "ref_id": ref_id,
        "created_at": now(),
    }
    save_json(FILES["users"], users)
    save_json(FILES["transactions"], transactions)
    return tid


def add_admin_log(action, details):
    admin_logs.append({"created_at": now(), "action": action, "details": details})
    save_json(FILES["admin_logs"], admin_logs)


def new_request(uid, rtype, data):
    rid = str(random.randint(10000, 99999))
    while rid in requests_db:
        rid = str(random.randint(10000, 99999))
    requests_db[rid] = {
        "id": rid,
        "user_id": str(uid),
        "type": rtype,
        "status": "pending",
        "created_at": now(),
        "updated_at": now(),
        **data,
    }
    save_json(FILES["requests"], requests_db)
    return rid


def rate(asset):
    if asset == "TL":
        return Decimal("1")
    return D(settings.get(f"rate_{asset}_TL", "0"))


def fee_percent(kind, asset=None):
    if kind == "convert":
        return D(settings.get("fee_convert_percent", "0"))
    return D(settings.get(f"fee_{kind}_{asset}_percent", "0"))


def fee_amount(amount, percent):
    return (D(amount) * D(percent) / Decimal("100"))


def min_amount(kind, asset):
    return D(settings.get(f"min_{kind}_{asset}", "0"))


def daily_limit(asset):
    return D(settings.get(f"daily_withdraw_limit_{asset}", "0"))


def converted_amount(from_asset, to_asset, amount):
    tl_value = D(amount) * rate(from_asset)
    gross_to = tl_value / rate(to_asset) if rate(to_asset) > 0 else Decimal("0")
    fee_p = fee_percent("convert")
    fee_to = fee_amount(gross_to, fee_p)
    net_to = gross_to - fee_to
    return tl_value, gross_to, fee_to, net_to, fee_p


def wallet_text(uid):
    u = users[str(uid)]
    b = u["balances"]
    return (
        f"{messages.get('wallet_title')}\n\n"
        f"TL: {fmt_amount(b.get('TL', '0'), 'TL')}\n"
        f"USDT: {fmt_amount(b.get('USDT', '0'), 'USDT')}\n"
        f"LTC: {fmt_amount(b.get('LTC', '0'), 'LTC')}\n"
        f"TRX: {fmt_amount(b.get('TRX', '0'), 'TRX')}"
    )


def request_summary(rid):
    r = requests_db.get(str(rid), {})
    if not r:
        return "İşlem bulunamadı."
    uid = r.get("user_id", "")
    title = {
        "deposit": "Bakiye Yükleme",
        "withdraw": "Para Çekme",
        "convert": "Dönüştürme",
    }.get(r.get("type"), r.get("type"))
    text = f"İşlem #{rid}\n{title}\nDurum: {r.get('status')}\n"
    if r.get("type") == "deposit":
        text += f"\nYüklenen: {fmt_amount(r.get('amount'), r.get('asset'))}\nNet Bakiye: {fmt_amount(r.get('net_amount'), r.get('asset'))}"
        if r.get("sender_name"):
            text += f"\nGönderen: {r.get('sender_name')}"
        if r.get("tx_note"):
            text += f"\nNot/Tx: {r.get('tx_note')}"
    elif r.get("type") == "withdraw":
        text += f"\nÇekim: {fmt_amount(r.get('amount'), r.get('asset'))}\nKomisyon: {fmt_amount(r.get('fee'), r.get('asset'))}\nNet: {fmt_amount(r.get('net_amount'), r.get('asset'))}"
        if r.get("asset") == "TL":
            text += f"\nBanka: {r.get('bank_name')}\nIBAN: {r.get('iban')}\nAd Soyad: {r.get('name')}"
        else:
            text += f"\nAdres: {r.get('address')}"
    elif r.get("type") == "convert":
        text += f"\nGönderilen: {fmt_amount(r.get('from_amount'), r.get('from_asset'))}\nTL Değeri: {fmt_amount(r.get('tl_value'), 'TL')}\nAlınan: {fmt_amount(r.get('net_to_amount'), r.get('to_asset'))}"
    text += f"\n\nOluşturulma: {r.get('created_at')}"
    return text


def admin_notify(text):
    if ADMIN_CHAT_ID:
        send(ADMIN_CHAT_ID, text)


def start_user(chat_id, username=""):
    get_user(chat_id, username)
    send(chat_id, messages.get("welcome"), reply_keyboard())


def show_wallet(chat_id):
    get_user(chat_id)
    send(chat_id, wallet_text(chat_id), reply_keyboard())


def show_history(chat_id):
    uid = str(chat_id)
    items = [r for r in requests_db.values() if r.get("user_id") == uid]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    items = items[:5]
    if not items:
        send(chat_id, messages.get("history_empty"), reply_keyboard())
        return
    rows = []
    lines = ["Son 5 İşlem"]
    for r in items:
        rid = r.get("id")
        lines.append(f"#{rid} — {r.get('status')} — {r.get('created_at', '')}")
        rows.append([inline_button(f"#{rid} Detay", f"detail:{rid}")])
    send(chat_id, "\n".join(lines), {"inline_keyboard": rows})


def require_pin_or_start(chat_id, next_step):
    u = get_user(chat_id)
    if not u.get("pin_hash"):
        user_state[str(chat_id)] = {"flow": "set_pin", "next": next_step}
        send(chat_id, messages.get("pin_set_question"), reply_keyboard())
        return False
    user_state[str(chat_id)]["step"] = "pin"
    send(chat_id, messages.get("pin_question"), reply_keyboard())
    return True


def begin_deposit(chat_id):
    user_state[str(chat_id)] = {"flow": "deposit", "step": "asset"}
    send(chat_id, messages.get("deposit_menu"), asset_keyboard("deposit_asset", include_tl=True))


def begin_withdraw(chat_id):
    user_state[str(chat_id)] = {"flow": "withdraw", "step": "asset"}
    send(chat_id, messages.get("withdraw_menu"), asset_keyboard("withdraw_asset", include_tl=True))


def begin_convert(chat_id):
    user_state[str(chat_id)] = {"flow": "convert", "step": "from_asset"}
    send(chat_id, messages.get("convert_menu"), asset_keyboard("convert_from", include_tl=True))


def handle_text(chat_id, username, text):
    uid = str(chat_id)
    get_user(chat_id, username)

    if text in ["/start", "Ana Menü"]:
        start_user(chat_id, username)
        return
    if text == "Cüzdanım":
        show_wallet(chat_id)
        return
    if text == "Bakiye Yükle":
        begin_deposit(chat_id)
        return
    if text == "Para Çek":
        begin_withdraw(chat_id)
        return
    if text == "Dönüştür":
        begin_convert(chat_id)
        return
    if text == "İşlem Geçmişi":
        show_history(chat_id)
        return
    if text == "Destek":
        send(chat_id, messages.get("support"), reply_keyboard())
        return
    if text == "/pin":
        user_state[uid] = {"flow": "set_pin", "next": None}
        send(chat_id, messages.get("pin_set_question"), reply_keyboard())
        return

    state = user_state.get(uid)
    if not state:
        send(chat_id, "Menüden bir işlem seçiniz.", reply_keyboard())
        return

    flow = state.get("flow")
    step = state.get("step")

    if flow == "set_pin":
        pin = text.strip()
        if not pin.isdigit() or not (4 <= len(pin) <= 6):
            send(chat_id, "PIN 4-6 haneli rakamlardan oluşmalıdır.", reply_keyboard())
            return
        users[uid]["pin_hash"] = hash_pin(pin)
        save_json(FILES["users"], users)
        next_state = state.get("next")
        send(chat_id, messages.get("pin_saved"), reply_keyboard())
        if next_state:
            user_state[uid] = next_state
            user_state[uid]["step"] = "pin"
            send(chat_id, messages.get("pin_question"), reply_keyboard())
        else:
            user_state.pop(uid, None)
        return

    if step == "amount":
        amount = D(text)
        if amount <= 0:
            send(chat_id, "Geçerli bir tutar giriniz.", reply_keyboard())
            return
        state["amount"] = str(amount)
        if flow == "deposit":
            asset = state["asset"]
            if amount < min_amount("deposit", asset):
                send(chat_id, f"Minimum yükleme tutarı: {fmt_amount(min_amount('deposit', asset), asset)}", reply_keyboard())
                return
            fee = fee_amount(amount, fee_percent("deposit", asset))
            net = amount - fee
            state["fee"] = str(fee)
            state["net_amount"] = str(net)
            if asset == "TL":
                send(chat_id, settings.get("bank_name"), reply_keyboard())
                send(chat_id, settings.get("iban"), reply_keyboard())
                send(chat_id, settings.get("iban_owner"), reply_keyboard())
                send(chat_id, messages.get("iban_warning"), confirm_keyboard("deposit_sent"))
                state["step"] = "waiting_sent"
            else:
                send(chat_id, f"{asset} yatırma adresi:")
                send(chat_id, settings.get(f"wallet_{asset}", ""), confirm_keyboard("deposit_sent"))
                state["step"] = "waiting_sent"
            return
        if flow == "withdraw":
            asset = state["asset"]
            if amount < min_amount("withdraw", asset):
                send(chat_id, f"Minimum çekim tutarı: {fmt_amount(min_amount('withdraw', asset), asset)}", reply_keyboard())
                return
            fee = fee_amount(amount, fee_percent("withdraw", asset))
            net = amount - fee
            if user_balance(uid, asset) < amount:
                send(chat_id, messages.get("insufficient_balance"), reply_keyboard())
                user_state.pop(uid, None)
                return
            state["fee"] = str(fee)
            state["net_amount"] = str(net)
            if asset == "TL":
                state["step"] = "bank_name"
                send(chat_id, "Banka adını giriniz.", reply_keyboard())
            else:
                state["step"] = "address"
                send(chat_id, "Çekim cüzdan adresini giriniz.", reply_keyboard())
            return
        if flow == "convert":
            from_asset = state["from_asset"]
            to_asset = state["to_asset"]
            if user_balance(uid, from_asset) < amount:
                send(chat_id, messages.get("insufficient_balance"), reply_keyboard())
                user_state.pop(uid, None)
                return
            tl_value, gross_to, fee_to, net_to, fee_p = converted_amount(from_asset, to_asset, amount)
            if net_to <= 0:
                send(chat_id, "Kur ayarları geçersiz. Lütfen destek ile iletişime geçiniz.", reply_keyboard())
                user_state.pop(uid, None)
                return
            state.update({"tl_value": str(tl_value), "gross_to_amount": str(gross_to), "fee_to_amount": str(fee_to), "net_to_amount": str(net_to), "fee_percent": str(fee_p)})
            if not require_pin_or_start(chat_id, dict(state)):
                return
            return

    if step == "pin":
        if users[uid].get("pin_hash") != hash_pin(text.strip()):
            send(chat_id, messages.get("pin_wrong"), reply_keyboard())
            return
        if flow == "withdraw":
            asset = state["asset"]
            text2 = (
                f"Çekim Onayı\n\n"
                f"Tutar: {fmt_amount(state['amount'], asset)}\n"
                f"Komisyon: {fmt_amount(state['fee'], asset)}\n"
                f"Net: {fmt_amount(state['net_amount'], asset)}\n\n"
                f"Onaylıyor musunuz?"
            )
            state["step"] = "confirm"
            send(chat_id, text2, confirm_keyboard("withdraw_confirm"))
            return
        if flow == "convert":
            text2 = (
                f"Dönüştürme Onayı\n\n"
                f"Gönderilen: {fmt_amount(state['amount'], state['from_asset'])}\n"
                f"TL Değeri: {fmt_amount(state['tl_value'], 'TL')}\n"
                f"Alınacak Net: {fmt_amount(state['net_to_amount'], state['to_asset'])}\n\n"
                f"Onaylıyor musunuz?"
            )
            state["step"] = "confirm"
            send(chat_id, text2, confirm_keyboard("convert_confirm"))
            return

    if flow == "deposit" and step == "sender_name":
        state["sender_name"] = text.strip()
        state["step"] = "tx_note"
        send(chat_id, "Varsa işlem notu / TXID giriniz. Yoksa '-' yazınız.", reply_keyboard())
        return

    if flow == "deposit" and step == "tx_note":
        state["tx_note"] = text.strip()
        rid = new_request(uid, "deposit", {
            "asset": state["asset"],
            "amount": state["amount"],
            "fee": state.get("fee", "0"),
            "net_amount": state.get("net_amount", state["amount"]),
            "sender_name": state.get("sender_name", ""),
            "tx_note": state.get("tx_note", ""),
        })
        user_state.pop(uid, None)
        send(chat_id, f"{messages.get('request_created')}\n\nİşlem No: #{rid}", reply_keyboard())
        admin_notify(f"Yeni bakiye yükleme talebi\n\n{request_summary(rid)}")
        return

    if flow == "withdraw" and step == "bank_name":
        state["bank_name"] = text.strip()
        state["step"] = "iban"
        send(chat_id, "IBAN bilginizi giriniz.", reply_keyboard())
        return
    if flow == "withdraw" and step == "iban":
        state["iban"] = text.strip()
        state["step"] = "name"
        send(chat_id, "Ad ve soyad bilginizi giriniz.", reply_keyboard())
        return
    if flow == "withdraw" and step == "name":
        state["name"] = text.strip()
        if not require_pin_or_start(chat_id, dict(state)):
            return
        return
    if flow == "withdraw" and step == "address":
        state["address"] = text.strip()
        if not require_pin_or_start(chat_id, dict(state)):
            return
        return

    send(chat_id, "İşlemi tamamlamak için menüden devam ediniz.", reply_keyboard())


def handle_callback(chat_id, username, data, cb_id):
    answer(cb_id)
    uid = str(chat_id)
    get_user(chat_id, username)

    if data == "cancel":
        user_state.pop(uid, None)
        send(chat_id, messages.get("request_cancelled"), reply_keyboard())
        return
    if data.startswith("detail:"):
        rid = data.split(":", 1)[1]
        send(chat_id, request_summary(rid), reply_keyboard())
        return
    if data.startswith("deposit_asset:"):
        asset = data.split(":", 1)[1]
        user_state[uid] = {"flow": "deposit", "step": "amount", "asset": asset}
        send(chat_id, messages.get("amount_question"), reply_keyboard())
        return
    if data == "deposit_sent":
        state = user_state.get(uid, {})
        if state.get("flow") != "deposit":
            return
        state["step"] = "sender_name"
        send(chat_id, "Gönderen ad ve soyad bilgisini giriniz.", reply_keyboard())
        return
    if data.startswith("withdraw_asset:"):
        asset = data.split(":", 1)[1]
        user_state[uid] = {"flow": "withdraw", "step": "amount", "asset": asset}
        send(chat_id, messages.get("amount_question"), reply_keyboard())
        return
    if data == "withdraw_confirm":
        state = user_state.get(uid, {})
        if state.get("flow") != "withdraw":
            return
        asset = state["asset"]
        amount = D(state["amount"])
        if user_balance(uid, asset) < amount:
            send(chat_id, messages.get("insufficient_balance"), reply_keyboard())
            user_state.pop(uid, None)
            return
        change_balance(uid, asset, -amount, "withdraw_reserved")
        rid = new_request(uid, "withdraw", {
            "asset": asset,
            "amount": str(amount),
            "fee": state["fee"],
            "net_amount": state["net_amount"],
            "bank_name": state.get("bank_name", ""),
            "iban": state.get("iban", ""),
            "name": state.get("name", ""),
            "address": state.get("address", ""),
        })
        user_state.pop(uid, None)
        send(chat_id, f"{messages.get('request_created')}\n\nİşlem No: #{rid}", reply_keyboard())
        admin_notify(f"Yeni çekim talebi\n\n{request_summary(rid)}")
        return
    if data.startswith("convert_from:"):
        from_asset = data.split(":", 1)[1]
        user_state[uid] = {"flow": "convert", "step": "to_asset", "from_asset": from_asset}
        send(chat_id, "Almak istediğiniz bakiye türünü seçiniz.", asset_keyboard("convert_to", include_tl=True, exclude=from_asset))
        return
    if data.startswith("convert_to:"):
        to_asset = data.split(":", 1)[1]
        state = user_state.get(uid, {})
        state["to_asset"] = to_asset
        state["step"] = "amount"
        send(chat_id, messages.get("amount_question"), reply_keyboard())
        return
    if data == "convert_confirm":
        state = user_state.get(uid, {})
        if state.get("flow") != "convert":
            return
        from_asset = state["from_asset"]
        to_asset = state["to_asset"]
        amount = D(state["amount"])
        net_to = D(state["net_to_amount"])
        if user_balance(uid, from_asset) < amount:
            send(chat_id, messages.get("insufficient_balance"), reply_keyboard())
            user_state.pop(uid, None)
            return
        change_balance(uid, from_asset, -amount, "convert_out")
        change_balance(uid, to_asset, net_to, "convert_in")
        rid = new_request(uid, "convert", {
            "from_asset": from_asset,
            "to_asset": to_asset,
            "from_amount": str(amount),
            "tl_value": state["tl_value"],
            "gross_to_amount": state["gross_to_amount"],
            "fee_to_amount": state["fee_to_amount"],
            "net_to_amount": state["net_to_amount"],
            "fee_percent": state["fee_percent"],
            "status": "completed",
            "completed_at": now(),
        })
        requests_db[rid]["status"] = "completed"
        requests_db[rid]["completed_at"] = now()
        save_json(FILES["requests"], requests_db)
        user_state.pop(uid, None)
        send(chat_id, f"Dönüştürme tamamlandı.\n\n{request_summary(rid)}", reply_keyboard())
        return


def bot_loop():
    global OFFSET
    print("ZaqelV2 bot started")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": OFFSET, "timeout": 20},
                timeout=30,
            ).json()
            for u in r.get("result", []):
                OFFSET = u["update_id"] + 1
                if "message" in u:
                    msg = u["message"]
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "")
                    username = msg.get("from", {}).get("username", "unknown")
                    custom_ids = [str(e.get("custom_emoji_id")) for e in msg.get("entities", []) if e.get("type") == "custom_emoji" and e.get("custom_emoji_id")]
                    if custom_ids and str(chat_id) == str(ADMIN_CHAT_ID):
                        send(chat_id, "\n".join(custom_ids))
                        continue
                    handle_text(chat_id, username, text)
                if "callback_query" in u:
                    cb = u["callback_query"]
                    chat_id = cb["message"]["chat"]["id"]
                    username = cb.get("from", {}).get("username", "unknown")
                    handle_callback(chat_id, username, cb.get("data", ""), cb["id"])
            time.sleep(1)
        except Exception as exc:
            print("BOT ERROR:", exc)
            time.sleep(5)


def logged_in():
    return session.get("login") is True


@app.route("/")
def home():
    return "ZaqelV2 aktif ✅"


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("username") == PANEL_USERNAME and request.form.get("password") == PANEL_PASSWORD:
            session["login"] = True
            return redirect("/admin")
        error = "Hatalı giriş"
    return f"""
    <!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>ZaqelV2 Admin</title><style>
    body{{margin:0;background:#07111f;color:white;font-family:Arial;display:grid;place-items:center;min-height:100vh}}
    .box{{width:min(420px,calc(100% - 32px));background:#0e1b2d;border:1px solid #1e3656;border-radius:18px;padding:26px}}
    input,button{{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;margin-top:8px}}input{{background:#091424;color:white;border:1px solid #29466f}}button{{border:0;background:#00bcd4;color:#001018;font-weight:800;cursor:pointer}}.e{{color:#ff6b6b}}
    </style></head><body><div class='box'><h2>ZaqelV2 Admin</h2><form method='post'><label>Kullanıcı adı</label><input name='username' required><label>Şifre</label><input name='password' type='password' required><button>Giriş Yap</button></form><p class='e'>{h(error)}</p></div></body></html>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


def request_status_label(status):
    return {"pending": "Bekliyor", "completed": "Tamamlandı", "rejected": "Reddedildi"}.get(status, status)


def panel_request_card(rid, r):
    u = users.get(str(r.get("user_id")), {})
    rows = [
        ("İşlem No", f"#{rid}"),
        ("Kullanıcı", f"@{u.get('username','unknown')} / {r.get('user_id')}"),
        ("Tür", r.get("type")),
        ("Durum", request_status_label(r.get("status"))),
        ("Tarih", r.get("created_at")),
    ]
    if r.get("type") == "deposit":
        rows += [("Yükleme", fmt_amount(r.get("amount"), r.get("asset"))), ("Net", fmt_amount(r.get("net_amount"), r.get("asset"))), ("Gönderen", r.get("sender_name", "")), ("Not/Tx", r.get("tx_note", ""))]
    elif r.get("type") == "withdraw":
        rows += [("Çekim", fmt_amount(r.get("amount"), r.get("asset"))), ("Komisyon", fmt_amount(r.get("fee"), r.get("asset"))), ("Net", fmt_amount(r.get("net_amount"), r.get("asset"))), ("IBAN/Adres", r.get("iban") or r.get("address")), ("Ad Soyad", r.get("name", ""))]
    elif r.get("type") == "convert":
        rows += [("Gönderilen", fmt_amount(r.get("from_amount"), r.get("from_asset"))), ("TL Değeri", fmt_amount(r.get("tl_value"), "TL")), ("Alınan", fmt_amount(r.get("net_to_amount"), r.get("to_asset")))]
    details = "".join(f"<div class='detail'><span>{h(k)}</span><b>{h(v)}</b></div>" for k, v in rows)
    actions = ""
    if r.get("status") == "pending":
        actions = f"""
        <form method='post' class='actions'>
          <input type='hidden' name='rid' value='{h(rid)}'>
          <button name='action' value='approve_request' class='ok'>Onayla</button>
          <button name='action' value='reject_request' class='bad'>Reddet</button>
        </form>
        """
    return f"<article class='card'><h3>#{h(rid)} · {h(request_status_label(r.get('status')))}</h3>{details}{actions}</article>"


def reserve_totals():
    totals = {asset: Decimal("0") for asset in ASSETS}
    for u in users.values():
        for asset in ASSETS:
            totals[asset] += D(u.get("balances", {}).get(asset, "0"))
    pending_withdraw = {asset: Decimal("0") for asset in ASSETS}
    for r in requests_db.values():
        if r.get("type") == "withdraw" and r.get("status") == "pending":
            pending_withdraw[r.get("asset")] += D(r.get("amount"))
    return totals, pending_withdraw


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in():
        return redirect("/login")
    if request.method == "POST":
        action = request.form.get("action")
        if action == "settings":
            for key in list(settings.keys()):
                settings[key] = request.form.get(key, settings.get(key, ""))
            for key in list(messages.keys()):
                messages[key] = request.form.get(key, messages.get(key, ""))
            save_json(FILES["settings"], settings)
            save_json(FILES["messages"], messages)
            add_admin_log("settings", "Ayarlar güncellendi")
        elif action in ["approve_request", "reject_request"]:
            rid = request.form.get("rid", "")
            r = requests_db.get(rid)
            if r and r.get("status") == "pending":
                uid = r.get("user_id")
                if action == "approve_request":
                    if r.get("type") == "deposit":
                        change_balance(uid, r.get("asset"), r.get("net_amount"), "deposit_approved", rid)
                    r["status"] = "completed"
                    r["completed_at"] = now()
                    send(uid, f"İşleminiz tamamlandı.\n\nİşlem No: #{rid}", reply_keyboard())
                    add_admin_log("approve", f"#{rid} onaylandı")
                else:
                    if r.get("type") == "withdraw":
                        change_balance(uid, r.get("asset"), r.get("amount"), "withdraw_refund", rid)
                    r["status"] = "rejected"
                    r["rejected_at"] = now()
                    send(uid, f"İşleminiz işleme alınamadı.\n\nİşlem No: #{rid}", reply_keyboard())
                    add_admin_log("reject", f"#{rid} reddedildi")
                r["updated_at"] = now()
                save_json(FILES["requests"], requests_db)
        elif action == "adjust_balance":
            uid = request.form.get("user_id", "")
            asset = request.form.get("asset", "")
            amount = D(request.form.get("amount", "0"))
            note = request.form.get("note", "").strip()
            if uid in users and asset in ASSETS and amount != 0 and note:
                change_balance(uid, asset, amount, "admin_adjustment: " + note)
                add_admin_log("adjust", f"{uid} {asset} {amount} {note}")
        return redirect("/admin")

    totals, pending_w = reserve_totals()
    pending = {"deposit": 0, "withdraw": 0, "convert": 0}
    for r in requests_db.values():
        if r.get("status") == "pending" and r.get("type") in pending:
            pending[r.get("type")] += 1
    cards = "".join(panel_request_card(rid, r) for rid, r in sorted(requests_db.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:80])
    user_rows = "".join(
        f"<tr><td>{h(uid)}</td><td>@{h(u.get('username','unknown'))}</td><td>{h(fmt_amount(u.get('balances',{}).get('TL','0'),'TL'))}</td><td>{h(fmt_amount(u.get('balances',{}).get('USDT','0'),'USDT'))}</td><td>{h(fmt_amount(u.get('balances',{}).get('LTC','0'),'LTC'))}</td><td>{h(fmt_amount(u.get('balances',{}).get('TRX','0'),'TRX'))}</td><td>{h(u.get('status'))}</td></tr>"
        for uid, u in sorted(users.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)
    )
    setting_inputs = "".join(f"<label>{h(k)}</label><input name='{h(k)}' value='{h(v)}'>" for k, v in settings.items())
    message_inputs = "".join(f"<label>{h(k)}</label><textarea name='{h(k)}'>{h(v)}</textarea>" for k, v in messages.items())
    reserve_html = "".join(f"<div class='metric'><span>{asset}</span><b>{fmt_amount(totals[asset], asset)}</b><small>Bekleyen çekim: {fmt_amount(pending_w[asset], asset)}</small></div>" for asset in ASSETS)
    return f"""
    <!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ZaqelV2 Admin</title>
    <style>
    :root{{--bg:#07111f;--panel:#0e1b2d;--line:#1e3656;--text:#f3fbff;--muted:#8fb0c7;--cyan:#00d4ff;--green:#17c964;--red:#ff4d5e;--orange:#ffb020}}
    *{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top left,rgba(0,212,255,.16),transparent 32%),var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif}}.wrap{{width:min(1440px,calc(100% - 26px));margin:auto;padding:22px 0 70px}}a{{color:white}}.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}}.box,.card{{background:rgba(14,27,45,.88);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px;box-shadow:0 16px 45px rgba(0,0,0,.22)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.metric{{background:#091424;border:1px solid #163154;border-radius:15px;padding:15px}}.metric span{{color:var(--muted)}}.metric b{{display:block;font-size:22px;margin:5px 0}}.metric small{{color:var(--muted)}}.detail{{display:flex;justify-content:space-between;gap:12px;border-bottom:1px dashed #223f63;padding:8px 0}}.detail span{{color:var(--muted)}}.detail b{{max-width:62%;text-align:right;overflow-wrap:anywhere}}button{{border:0;border-radius:10px;padding:11px 14px;font-weight:800;cursor:pointer;background:var(--cyan);color:#001018}}.ok{{background:var(--green);color:white}}.bad{{background:var(--red);color:white}}.actions{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}}input,textarea,select{{width:100%;padding:11px;border-radius:10px;border:1px solid #29466f;background:#091424;color:white}}textarea{{min-height:76px}}label{{display:block;color:var(--muted);margin:12px 0 6px}}details summary{{cursor:pointer;font-weight:900;font-size:19px;padding:12px 0}}table{{width:100%;border-collapse:collapse;min-width:800px}}td,th{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}.tablewrap{{overflow:auto}}@media(max-width:680px){{.detail{{display:block}}.detail b{{display:block;text-align:left;max-width:100%;margin-top:4px}}}}
    </style></head><body><main class='wrap'>
    <div class='top'><h1>ZaqelV2 Admin</h1><a href='/logout'>Çıkış</a></div>
    <section class='box'><h2>Rezerv Durumu</h2><div class='grid'>{reserve_html}</div></section>
    <section class='box'><h2>Bekleyen İşlemler</h2><div class='grid'><div class='metric'><span>Yükleme</span><b>{pending['deposit']}</b></div><div class='metric'><span>Çekim</span><b>{pending['withdraw']}</b></div><div class='metric'><span>Dönüşüm</span><b>{pending['convert']}</b></div></div></section>
    <section class='box'><h2>İşlem Talepleri</h2><div class='grid'>{cards or '<div class="metric">İşlem bulunmuyor.</div>'}</div></section>
    <section class='box'><h2>Kullanıcılar</h2><div class='tablewrap'><table><tr><th>ID</th><th>Kullanıcı</th><th>TL</th><th>USDT</th><th>LTC</th><th>TRX</th><th>Durum</th></tr>{user_rows}</table></div></section>
    <section class='box'><h2>Admin Bakiye Düzeltme</h2><form method='post' class='grid'><input type='hidden' name='action' value='adjust_balance'><div><label>Kullanıcı ID</label><input name='user_id'></div><div><label>Para Birimi</label><select name='asset'>{''.join(f'<option>{a}</option>' for a in ASSETS)}</select></div><div><label>Tutar (+ / -)</label><input name='amount' placeholder='100 veya -50'></div><div><label>Zorunlu Not</label><input name='note'></div><div><label>&nbsp;</label><button>Uygula</button></div></form></section>
    <form method='post'><input type='hidden' name='action' value='settings'><details class='box'><summary>Ayarlar / Kur / Komisyon / Cüzdan / Custom ID</summary><div class='grid'>{setting_inputs}</div></details><details class='box'><summary>Bot Mesajları</summary><div class='grid'>{message_inputs}</div></details><button style='width:100%;margin:10px 0 30px'>Tüm Ayarları Kaydet</button></form>
    </main></body></html>
    """


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
