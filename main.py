import os
import time
import random
import requests
import threading
import json
from datetime import datetime
from html import escape

from flask import Flask, request, redirect, session

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
PORT = int(os.getenv("PORT", "8080"))
OFFSET = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-in-railway")

user_state = {}
data_lock = threading.Lock()


def load_json(filename, default=None):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


coins = load_json("coins.json", {})
settings = load_json("settings.json", {})
messages = load_json("messages.json", {})
orders = load_json("orders.json", {})
referrals = load_json("referrals.json", {})


MESSAGE_DEFAULTS = {
    "welcome": "👋 Zaqel Swap'a hoş geldiniz.\n\nGüvenli, hızlı ve manuel onaylı takas platformu.",
    "swap_menu": "🔄 İşlem türünü seçiniz:",
    "coin_select_crypto_to_crypto": "🔄 Göndereceğiniz kripto para birimini seçiniz.",
    "coin_select_crypto_to_crypto_receive": "🔄 Almak istediğiniz kripto para birimini seçiniz.",
    "coin_select_iban_to_crypto": "🏦 Satın almak istediğiniz kripto para birimini seçiniz.",
    "coin_select_crypto_to_iban": "💳 TL'ye çevirmek istediğiniz kripto para birimini seçiniz.",
    "amount_question_crypto_to_crypto": "💰 Göndereceğiniz kripto miktarını giriniz:",
    "amount_question_iban_to_crypto": "💰 Göndereceğiniz TL miktarını giriniz:",
    "amount_question_crypto_to_iban": "💰 Bozduracağınız kripto miktarını giriniz:",
    "wallet_question": "📥 Alıcı cüzdan adresini giriniz:",
    "iban_question": "🏦 IBAN adresinizi giriniz:",
    "name_question": "👤 IBAN sahibinin ad soyad bilgisini giriniz:",
    "support": "📞 Destek için admin ile iletişime geçiniz.",
    "help": "ℹ️ İşlem türünü seçin ve bilgileri doldurun.",
    "iban_warning": "⚠️ Verilen IBAN numarasına para gönderen kişinin TC Kimlik numarasını açıklama kısmında belirtmesi zorunludur. Aksi takdirde dönüşüm işlemi gerçekleştirilmeyecektir.",
    "order_created": "✅ Siparişiniz oluşturuldu.",
    "order_completed": "✅ Siparişiniz tamamlandı.",
    "order_rejected": "❌ Siparişiniz reddedildi.",
    "orders_title": "📦 Siparişleriniz:",
    "orders_empty": "📦 Henüz siparişiniz yok.",
    "fees_title": "💰 Komisyonlar:",
    "iban_closed": "❌ Şu anda IBAN ile ödeme kapalıdır.",
    "session_expired": "❌ İşlem süresi doldu. Lütfen tekrar başlayın.",
    "working_hours": "7/24 Açık",
    "min_amount_error": "❌ Minimum işlem tutarı: {min_amount}",
    "referral_title": "👥 Referans Sistemi",
    "referral_text": "Referans linkiniz:\n{ref_link}\n\nToplam davet: {count}\nReferans kodunuz: {code}",
    "referral_registered": "✅ Referans kaydınız alındı.",
    "button_start_swap": "🔄 Swap Başlat",
    "button_my_orders": "📦 Siparişlerim",
    "button_fees": "💰 Komisyonlar",
    "button_help": "ℹ️ Nasıl Çalışır?",
    "button_support": "📞 Destek",
    "button_referral": "👥 Referansım",
    "button_iban_to_crypto": "🏦 IBAN → Kripto",
    "button_crypto_to_iban": "💳 Kripto → IBAN",
    "button_crypto_to_crypto": "🔄 Kripto → Kripto",
    "button_main_menu": "⬅️ Ana Menü",
    "button_back": "⬅️ Geri",
}

for key, default_value in MESSAGE_DEFAULTS.items():
    messages.setdefault(key, default_value)

save_json("messages.json", messages)


ICON_DEFAULTS = {
    "icon_start_swap": "",
    "icon_my_orders": "",
    "icon_fees": "",
    "icon_help": "",
    "icon_support": "",
    "icon_referral": "",
    "icon_iban_to_crypto": "",
    "icon_crypto_to_iban": "",
    "icon_crypto_to_crypto": "",
    "icon_main_menu": "",
    "icon_back": "",
}

for key, default_value in ICON_DEFAULTS.items():
    messages.setdefault(key, default_value)

save_json("messages.json", messages)


def telegram_button(text, callback_data, icon_key=None):
    button = {
        "text": text,
        "callback_data": callback_data,
    }

    if icon_key:
        custom_emoji_id = str(messages.get(icon_key, "")).strip()
        if custom_emoji_id:
            button["icon_custom_emoji_id"] = custom_emoji_id

    return button


def api(method, data):
    try:
        return requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=data,
            timeout=30,
        ).json()
    except Exception as exc:
        print("TELEGRAM API ERROR:", exc)
        return {}


def send(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": str(text)}
    if keyboard:
        data["reply_markup"] = keyboard
    return api("sendMessage", data)


def answer(cb_id):
    api("answerCallbackQuery", {"callback_query_id": cb_id})


def active_coins():
    return {k: v for k, v in coins.items() if v.get("active") == "on"}


def coin_label(symbol):
    c = coins.get(symbol, {})
    name = c.get("name", symbol)
    return f"{name} ({symbol})"


def coin_button(symbol, prefix):
    coin = coins.get(symbol, {})
    button = {
        "text": coin_label(symbol),
        "callback_data": f"{prefix}_{symbol}",
    }

    custom_emoji_id = str(coin.get("custom_emoji_id", "")).strip()
    if custom_emoji_id:
        button["icon_custom_emoji_id"] = custom_emoji_id

    return button


def menu(chat_id):
    send(
        chat_id,
        messages.get("welcome", MESSAGE_DEFAULTS["welcome"]),
        {
            "inline_keyboard": [
                [telegram_button(messages.get("button_start_swap", MESSAGE_DEFAULTS["button_start_swap"]), "swap", "icon_start_swap")],
                [telegram_button(messages.get("button_my_orders", MESSAGE_DEFAULTS["button_my_orders"]), "orders", "icon_my_orders")],
                [telegram_button(messages.get("button_fees", MESSAGE_DEFAULTS["button_fees"]), "fees", "icon_fees")],
                [telegram_button(messages.get("button_help", MESSAGE_DEFAULTS["button_help"]), "help", "icon_help")],
                [telegram_button(messages.get("button_referral", MESSAGE_DEFAULTS["button_referral"]), "referral", "icon_referral")],
                [telegram_button(messages.get("button_support", MESSAGE_DEFAULTS["button_support"]), "support", "icon_support")],
            ]
        },
    )


def swap_menu(chat_id):
    send(
        chat_id,
        messages.get("swap_menu", MESSAGE_DEFAULTS["swap_menu"]),
        {
            "inline_keyboard": [
                [telegram_button(messages.get("button_iban_to_crypto", MESSAGE_DEFAULTS["button_iban_to_crypto"]), "type_iban_to_crypto", "icon_iban_to_crypto")],
                [telegram_button(messages.get("button_crypto_to_iban", MESSAGE_DEFAULTS["button_crypto_to_iban"]), "type_crypto_to_iban", "icon_crypto_to_iban")],
                [telegram_button(messages.get("button_crypto_to_crypto", MESSAGE_DEFAULTS["button_crypto_to_crypto"]), "type_crypto_to_crypto", "icon_crypto_to_crypto")],
                [telegram_button(messages.get("button_main_menu", MESSAGE_DEFAULTS["button_main_menu"]), "main", "icon_main_menu")],
            ]
        },
    )


def coin_menu(chat_id, prefix, exclude=None, message_text="Coin seçiniz:"):
    rows = []

    for symbol in active_coins():
        if symbol != exclude:
            rows.append([coin_button(symbol, prefix)])

    rows.append([
        telegram_button(
            messages.get("button_back", MESSAGE_DEFAULTS["button_back"]),
            "swap",
            "icon_back",
        )
    ])

    send(chat_id, message_text, {"inline_keyboard": rows})


def order_type_name(order_type):
    return {
        "crypto_to_crypto": "🔄 Kripto → Kripto",
        "iban_to_crypto": "🏦 IBAN → Kripto",
        "crypto_to_iban": "💳 Kripto → IBAN",
    }.get(order_type, order_type or "Bilinmiyor")


def parse_amount(value):
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            return None
        return amount
    except Exception:
        return None


def min_key_for_type(order_type):
    return "min_" + str(order_type or "")


def check_min_amount(order_type, amount_text):
    amount = parse_amount(amount_text)
    if amount is None:
        return False, "❌ Lütfen geçerli bir miktar giriniz."

    min_value = parse_amount(settings.get(min_key_for_type(order_type), "0"))
    if min_value and amount < min_value:
        msg = messages.get("min_amount_error", MESSAGE_DEFAULTS["min_amount_error"])
        return False, msg.replace("{min_amount}", str(settings.get(min_key_for_type(order_type), min_value)))

    return True, ""


def bot_username():
    cached = settings.get("bot_username", "").strip()
    if cached:
        return cached.lstrip("@")

    result = api("getMe", {})
    username = result.get("result", {}).get("username", "") if isinstance(result, dict) else ""
    if username:
        settings["bot_username"] = username
        save_json("settings.json", settings)
    return username


def referral_code(chat_id):
    return str(chat_id)


def register_referral(new_chat_id, ref_code):
    new_chat_id = str(new_chat_id)
    ref_code = str(ref_code or "").replace("ref_", "").strip()

    if not ref_code or ref_code == new_chat_id:
        return False

    with data_lock:
        profile = referrals.setdefault(new_chat_id, {})
        if profile.get("referrer_id"):
            return False

        profile["referrer_id"] = ref_code
        profile["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        owner = referrals.setdefault(ref_code, {})
        invited = owner.setdefault("invited", [])
        if new_chat_id not in invited:
            invited.append(new_chat_id)

        save_json("referrals.json", referrals)

    return True


def referral_info(chat_id):
    chat_id = str(chat_id)
    code = referral_code(chat_id)
    username = bot_username()
    ref_link = f"https://t.me/{username}?start=ref_{code}" if username else f"/start ref_{code}"
    count = len(referrals.get(chat_id, {}).get("invited", []))

    text = messages.get("referral_text", MESSAGE_DEFAULTS["referral_text"])
    text = text.replace("{ref_link}", ref_link).replace("{count}", str(count)).replace("{code}", code)
    return messages.get("referral_title", MESSAGE_DEFAULTS["referral_title"]) + "\n\n" + text


def create_order(chat_id, username):
    s = user_state.get(chat_id)
    if not s:
        return

    with data_lock:
        oid = str(random.randint(10000, 99999))
        while oid in orders:
            oid = str(random.randint(10000, 99999))

        orders[oid] = {
            "chat_id": chat_id,
            "username": username,
            "referrer_id": referrals.get(str(chat_id), {}).get("referrer_id", ""),
            **s,
            "status": "⏳ Bekliyor",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_json("orders.json", orders)

    type_name = order_type_name(s.get("type"))
    fee = settings.get("fee_" + s.get("type", ""), "0")
    min_amount = settings.get("min_" + s.get("type", ""), "0")

    text = (
        f"📄 Sipariş Özeti\n\n"
        f"No: #{oid}\n"
        f"Tür: {type_name}\n"
        f"Komisyon: %{fee}\n"
        f"Minimum İşlem: {min_amount} TL\n"
    )

    if s.get("type") == "crypto_to_crypto":
        text += (
            f"\nGönderilen: {s.get('amount', '')} {s.get('from_coin', '')}\n"
            f"Alınacak: {s.get('to_coin', '')}\n"
            f"Alıcı adres:\n{s.get('wallet', '')}\n"
        )

    elif s.get("type") == "iban_to_crypto":
        text += (
            f"\nÖdenecek TL: {s.get('amount', '')} TL\n"
            f"Alınacak: {s.get('to_coin', '')}\n"
            f"Alıcı adres:\n{s.get('wallet', '')}\n\n"
            f"🏦 Ödeme IBAN:\n"
            f"Banka: {settings.get('bank_name', '')}\n"
            f"IBAN: {settings.get('iban', '')}\n"
            f"Alıcı: {settings.get('iban_owner', '')}\n\n"
            f"{messages.get('iban_warning', '')}\n"
        )

    elif s.get("type") == "crypto_to_iban":
        text += (
            f"\nGönderilen: {s.get('amount', '')} {s.get('from_coin', '')}\n"
            f"IBAN:\n{s.get('iban', '')}\n"
            f"Ad Soyad: {s.get('name', '')}\n"
        )

    text += "\nDurum: ⏳ Admin onayı bekleniyor"

    created_prefix = messages.get("order_created", "✅ Siparişiniz oluşturuldu.")
    send(chat_id, f"{created_prefix}\n\n{text}")

    if ADMIN_CHAT_ID:
        send(
            ADMIN_CHAT_ID,
            (
                f"🚨 Yeni Sipariş\n\n"
                f"Kullanıcı: @{username}\n"
                f"{text}\n\n"
                f"Web panelden tamamlayabilir veya reddedebilirsiniz.\n"
                f"Komutla tamamlamak için: /tamamla {oid}"
            ),
        )

    user_state.pop(chat_id, None)


def my_orders(chat_id):
    found = [
        f"#{oid} — {o.get('status', 'Bilinmiyor')}"
        for oid, o in orders.items()
        if str(o.get("chat_id")) == str(chat_id)
    ]
    if found:
        send(chat_id, messages.get("orders_title", MESSAGE_DEFAULTS["orders_title"]) + "\n\n" + "\n".join(found))
    else:
        send(chat_id, messages.get("orders_empty", MESSAGE_DEFAULTS["orders_empty"]))


def update_order_status(oid, new_status, reject_reason="", approval_reason=""):
    oid = str(oid)

    with data_lock:
        order = orders.get(oid)
        if not order:
            return False, "Sipariş bulunamadı."

        if new_status == "completed":
            order["status"] = "✅ Tamamlandı"
            order["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            order["approval_reason"] = approval_reason.strip()
            order["archived"] = False
            user_message = messages.get("order_completed", MESSAGE_DEFAULTS["order_completed"])

        elif new_status == "rejected":
            order["status"] = "❌ Reddedildi"
            order["rejected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            order["reject_reason"] = reject_reason.strip()
            order["archived"] = False
            user_message = messages.get("order_rejected", MESSAGE_DEFAULTS["order_rejected"])

        else:
            return False, "Geçersiz durum."

        save_json("orders.json", orders)

    notification = f"{user_message}\n\nSipariş No: #{oid}"

    if new_status == "completed" and approval_reason.strip():
        notification += f"\nOnay notu: {approval_reason.strip()}"

    if new_status == "rejected" and reject_reason.strip():
        notification += f"\nRed sebebi: {reject_reason.strip()}"

    send(order.get("chat_id"), notification)
    return True, order["status"]


def bot_loop():
    global OFFSET
    print("Bot started")

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

                    custom_emoji_ids = [
                        str(entity.get("custom_emoji_id"))
                        for entity in msg.get("entities", [])
                        if entity.get("type") == "custom_emoji"
                        and entity.get("custom_emoji_id")
                    ]

                    if custom_emoji_ids and str(chat_id) == str(ADMIN_CHAT_ID):
                        send(
                            chat_id,
                            "🧩 Custom Emoji ID:\n\n" + "\n".join(custom_emoji_ids)
                        )
                        continue

                    if text.startswith("/start"):
                        parts = text.split(maxsplit=1)
                        if len(parts) == 2 and parts[1].startswith("ref_"):
                            if register_referral(chat_id, parts[1]):
                                send(chat_id, messages.get("referral_registered", MESSAGE_DEFAULTS["referral_registered"]))
                        menu(chat_id)

                    elif text == "/siparislerim":
                        my_orders(chat_id)

                    elif text in ["/referans", "/ref"]:
                        send(chat_id, referral_info(chat_id))

                    elif text.startswith("/tamamla") and str(chat_id) == str(ADMIN_CHAT_ID):
                        parts = text.split()
                        if len(parts) >= 2:
                            ok, result = update_order_status(parts[1], "completed")
                            send(chat_id, "✅ Sipariş tamamlandı." if ok else f"❌ {result}")
                        else:
                            send(chat_id, "❌ Kullanım: /tamamla SIPARIS_NO")

                    elif chat_id in user_state:
                        s = user_state[chat_id]
                        step = s.get("step")

                        if step == "amount":
                            ok_min, min_error = check_min_amount(s.get("type"), text)
                            if not ok_min:
                                send(chat_id, min_error)
                                continue

                            s["amount"] = text

                            if s["type"] in ["crypto_to_crypto", "iban_to_crypto"]:
                                s["step"] = "wallet"
                                send(
                                    chat_id,
                                    messages.get(
                                        "wallet_question",
                                        "📥 Alacağın coin için cüzdan adresini gir:"
                                    ),
                                )
                            else:
                                s["step"] = "iban"
                                send(
                                    chat_id,
                                    messages.get(
                                        "iban_question",
                                        "🏦 IBAN adresini gir:"
                                    ),
                                )

                        elif step == "wallet":
                            s["wallet"] = text
                            create_order(chat_id, username)

                        elif step == "iban":
                            s["iban"] = text
                            s["step"] = "name"
                            send(
                                chat_id,
                                messages.get(
                                    "name_question",
                                    "👤 IBAN sahibinin ad soyad bilgisini gir:"
                                ),
                            )

                        elif step == "name":
                            s["name"] = text
                            create_order(chat_id, username)

                if "callback_query" in u:
                    cb = u["callback_query"]
                    answer(cb["id"])
                    chat_id = cb["message"]["chat"]["id"]
                    data = cb["data"]

                    if data == "main":
                        menu(chat_id)

                    elif data == "swap":
                        swap_menu(chat_id)

                    elif data == "orders":
                        my_orders(chat_id)

                    elif data == "fees":
                        send(
                            chat_id,
                            (
                                messages.get("fees_title", MESSAGE_DEFAULTS["fees_title"]) + "\n\n"
                                f"🔄 Kripto → Kripto: %{settings.get('fee_crypto_to_crypto', '0')}\n"
                                f"🏦 IBAN → Kripto: %{settings.get('fee_iban_to_crypto', '0')}\n"
                                f"💳 Kripto → IBAN: %{settings.get('fee_crypto_to_iban', '0')}"
                            ),
                        )

                    elif data == "help":
                        send(
                            chat_id,
                            messages.get("help", "ℹ️ İşlem türünü seçin.")
                            + f"\n\nÇalışma saatleri: {messages.get('working_hours', '')}",
                        )

                    elif data == "support":
                        send(chat_id, messages.get("support", "📞 Destek"))

                    elif data == "referral":
                        send(chat_id, referral_info(chat_id))

                    elif data == "type_crypto_to_crypto":
                        user_state[chat_id] = {"type": "crypto_to_crypto"}
                        coin_menu(
                            chat_id,
                            "from",
                            message_text=messages.get(
                                "coin_select_crypto_to_crypto",
                                "🔄 Takas etmek istediğiniz kripto para birimini seçiniz."
                            ),
                        )

                    elif data == "type_iban_to_crypto":
                        if settings.get("iban_active") != "on":
                            send(chat_id, messages.get("iban_closed", MESSAGE_DEFAULTS["iban_closed"]))
                        else:
                            user_state[chat_id] = {"type": "iban_to_crypto"}
                            coin_menu(
                                chat_id,
                                "to",
                                message_text=messages.get(
                                    "coin_select_iban_to_crypto",
                                    "🏦 Satın almak istediğiniz kripto para birimini seçiniz."
                                ),
                            )

                    elif data == "type_crypto_to_iban":
                        user_state[chat_id] = {"type": "crypto_to_iban"}
                        coin_menu(
                            chat_id,
                            "from",
                            message_text=messages.get(
                                "coin_select_crypto_to_iban",
                                "💳 TL'ye çevirmek istediğiniz kripto para birimini seçiniz."
                            ),
                        )

                    elif data.startswith("from_"):
                        coin = data.replace("from_", "")
                        s = user_state.get(chat_id)
                        if not s:
                            send(chat_id, messages.get("session_expired", MESSAGE_DEFAULTS["session_expired"]))
                            continue

                        s["from_coin"] = coin

                        if s["type"] == "crypto_to_crypto":
                            coin_menu(
                                chat_id,
                                "to",
                                exclude=coin,
                                message_text=messages.get(
                                    "coin_select_crypto_to_crypto_receive",
                                    MESSAGE_DEFAULTS["coin_select_crypto_to_crypto_receive"]
                                ),
                            )
                        else:
                            s["step"] = "amount"
                            send(
                                chat_id,
                                messages.get(
                                    "amount_question_crypto_to_iban",
                                    "💰 Kripto miktarını giriniz:"
                                ),
                            )

                    elif data.startswith("to_"):
                        coin = data.replace("to_", "")
                        s = user_state.get(chat_id)
                        if not s:
                            send(chat_id, messages.get("session_expired", MESSAGE_DEFAULTS["session_expired"]))
                            continue

                        s["to_coin"] = coin
                        s["step"] = "amount"

                        if s["type"] == "iban_to_crypto":
                            send(
                                chat_id,
                                messages.get(
                                    "amount_question_iban_to_crypto",
                                    "💰 TL tutarını giriniz:"
                                ),
                            )
                        elif s["type"] == "crypto_to_crypto":
                            send(
                                chat_id,
                                messages.get(
                                    "amount_question_crypto_to_crypto",
                                    "💰 Miktarı giriniz:"
                                ),
                            )

            time.sleep(1)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(5)


def logged_in():
    return session.get("login") is True


def h(value):
    return escape(str(value if value is not None else ""), quote=True)


@app.route("/")
def home():
    return "Zaqel Bot aktif ✅"


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""

    if request.method == "POST":
        if (
            request.form.get("username") == PANEL_USERNAME
            and request.form.get("password") == PANEL_PASSWORD
        ):
            session["login"] = True
            return redirect("/admin")
        error = "Hatalı giriş"

    return f"""
    <!doctype html>
    <html lang="tr">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Zaqel Admin Giriş</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #0b0d12;
                color: white;
                display: grid;
                place-items: center;
                min-height: 100vh;
                margin: 0;
            }}
            .login-box {{
                width: min(420px, calc(100% - 40px));
                background: #151922;
                border: 1px solid #2a3040;
                padding: 28px;
                border-radius: 18px;
            }}
            input {{
                width: 100%;
                box-sizing: border-box;
                padding: 12px;
                margin-top: 7px;
                background: #0f131b;
                color: white;
                border: 1px solid #343b4e;
                border-radius: 10px;
            }}
            button {{
                width: 100%;
                padding: 12px;
                margin-top: 18px;
                border: 0;
                border-radius: 10px;
                background: #ef3340;
                color: white;
                font-weight: bold;
                cursor: pointer;
            }}
            .error {{ color: #ff6b73; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>🔐 Zaqel Admin</h1>
            <form method="post">
                <label>Kullanıcı adı</label>
                <input name="username" autocomplete="username" required>
                <br><br>
                <label>Şifre</label>
                <input name="password" type="password" autocomplete="current-password" required>
                <button type="submit">Giriş Yap</button>
            </form>
            <p class="error">{h(error)}</p>
        </div>
    </body>
    </html>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


def order_counts():
    counts = {
        "active": 0,
        "completed": 0,
        "rejected": 0,
        "all": len(orders),
    }

    for order in orders.values():
        status = order.get("status", "")
        archived = order.get("archived") is True

        if status == "⏳ Bekliyor":
            counts["active"] += 1
        elif status == "✅ Tamamlandı" and not archived:
            counts["completed"] += 1
        elif status == "❌ Reddedildi" and not archived:
            counts["rejected"] += 1

    return counts


def render_order_cards(view="active"):
    cards = ""

    sorted_orders = sorted(
        orders.items(),
        key=lambda item: item[1].get("created_at", ""),
        reverse=True,
    )

    for oid, order in sorted_orders:
        status = order.get("status", "Bilinmiyor")
        archived = order.get("archived") is True

        if view == "active" and status != "⏳ Bekliyor":
            continue
        if view == "completed" and (status != "✅ Tamamlandı" or archived):
            continue
        if view == "rejected" and (status != "❌ Reddedildi" or archived):
            continue

        is_pending = status == "⏳ Bekliyor"

        if is_pending:
            status_class = "pending"
        elif status == "✅ Tamamlandı":
            status_class = "completed"
        else:
            status_class = "rejected"

        details = [
            ("Sipariş No", f"#{oid}"),
            ("Kullanıcı", f"@{order.get('username', 'unknown')}"),
            ("Telegram ID", order.get("chat_id", "")),
            ("Referans Veren", order.get("referrer_id", "-") or "-"),
            ("Tür", order_type_name(order.get("type"))),
            ("Miktar", order.get("amount", "")),
            ("Gönderilen Coin", order.get("from_coin", "-")),
            ("Alınacak Coin", order.get("to_coin", "-")),
            ("Cüzdan", order.get("wallet", "-")),
            ("IBAN", order.get("iban", "-")),
            ("Ad Soyad", order.get("name", "-")),
            ("Oluşturulma", order.get("created_at", "-")),
            ("Durum", status),
        ]

        if order.get("completed_at"):
            details.append(("Tamamlanma", order.get("completed_at")))

        if order.get("approval_reason"):
            details.append(("Onay Notu", order.get("approval_reason")))

        if order.get("rejected_at"):
            details.append(("Reddedilme", order.get("rejected_at")))

        if order.get("reject_reason"):
            details.append(("Red Sebebi", order.get("reject_reason")))

        if archived:
            details.append(("Liste Durumu", "Ana listelerden kaldırıldı"))

        details_html = "".join(
            f"<div class='detail'><span>{h(label)}</span><strong>{h(value)}</strong></div>"
            for label, value in details
        )

        actions_html = ""

        if is_pending:
            actions_html = f"""
            <div class="actions">
                <form method="post" onsubmit="return confirm('Bu sipariş tamamlandı olarak işaretlensin mi?')">
                    <input type="hidden" name="action" value="complete_order">
                    <input type="hidden" name="order_id" value="{h(oid)}">
                    <input type="hidden" name="return_view" value="{h(view)}">
                    <textarea class="approval-reason" name="approval_reason" placeholder="Onay notu yazın (isteğe bağlı)..."></textarea>
                    <button class="complete" type="submit">✅ Tamamla</button>
                </form>

                <form method="post" onsubmit="return confirm('Bu sipariş reddedilsin mi?')">
                    <input type="hidden" name="action" value="reject_order">
                    <input type="hidden" name="order_id" value="{h(oid)}">
                    <input type="hidden" name="return_view" value="{h(view)}">
                    <textarea class="reject-reason" name="reject_reason" placeholder="Red sebebi yazın..." required></textarea>
                    <button class="reject" type="submit">❌ Reddet</button>
                </form>
            </div>
            """

        elif not archived:
            actions_html = f"""
            <div class="single-action">
                <form method="post" onsubmit="return confirm('Bu sipariş ana listeden kaldırılsın mı? Tüm Siparişler bölümünde görünmeye devam eder.')">
                    <input type="hidden" name="action" value="archive_order">
                    <input type="hidden" name="order_id" value="{h(oid)}">
                    <input type="hidden" name="return_view" value="{h(view)}">
                    <button class="archive" type="submit">🗂️ Listeden Kaldır</button>
                </form>
            </div>
            """

        else:
            actions_html = f"""
            <div class="single-action">
                <form method="post">
                    <input type="hidden" name="action" value="restore_order">
                    <input type="hidden" name="order_id" value="{h(oid)}">
                    <input type="hidden" name="return_view" value="{h(view)}">
                    <button class="restore" type="submit">↩️ Listeye Geri Al</button>
                </form>
            </div>
            """

        cards += f"""
        <article class="order-card {status_class}">
            <div class="order-head">
                <h3>#{h(oid)}</h3>
                <span class="status">{h(status)}</span>
            </div>

            <div class="details">
                {details_html}
            </div>

            {actions_html}
        </article>
        """

    empty_texts = {
        "active": "Bekleyen sipariş bulunmuyor.",
        "completed": "Listede tamamlanmış sipariş bulunmuyor.",
        "rejected": "Listede reddedilmiş sipariş bulunmuyor.",
        "all": "Henüz sipariş bulunmuyor.",
    }

    if not cards:
        cards = f"<div class='empty-state'>📭 {h(empty_texts.get(view, 'Sipariş bulunmuyor.'))}</div>"

    return cards


@app.route("/admin/orders-fragment")
def admin_orders_fragment():
    if not logged_in():
        return "", 401

    view = request.args.get("view", "active")

    if view not in {"active", "completed", "rejected", "all"}:
        view = "active"

    fragment = render_order_cards(view)
    pending_count = order_counts()["active"]
    return f"<div data-fragment-pending-count='{pending_count}'>{fragment}</div>"


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in():
        return redirect("/login")

    if request.method == "POST":
        action = request.form.get("action")

        if action == "settings":
            for key in list(settings.keys()):
                settings[key] = request.form.get(key, "")

            for key in MESSAGE_DEFAULTS.keys():
                messages[key] = request.form.get(
                    key,
                    messages.get(key, MESSAGE_DEFAULTS[key]),
                )

            for key in ICON_DEFAULTS.keys():
                messages[key] = request.form.get(
                    key,
                    messages.get(key, ICON_DEFAULTS[key]),
                ).strip()

        elif action == "add_coin":
            symbol = request.form.get("symbol", "").upper().strip()

            if symbol:
                coins[symbol] = {
                    "name": request.form.get("name", ""),
                    "emoji": request.form.get("emoji", "🪙"),
                    "network": request.form.get("network", ""),
                    "address": request.form.get("address", ""),
                    "logo": request.form.get("logo", ""),
                    "custom_emoji_id": request.form.get("custom_emoji_id", "").strip(),
                    "active": request.form.get("active", "on"),
                }

        elif action == "update_coins":
            for symbol in list(coins.keys()):
                coins[symbol]["name"] = request.form.get(
                    f"name_{symbol}",
                    coins[symbol].get("name", ""),
                )
                coins[symbol]["emoji"] = request.form.get(
                    f"emoji_{symbol}",
                    coins[symbol].get("emoji", "🪙"),
                )
                coins[symbol]["network"] = request.form.get(
                    f"network_{symbol}",
                    coins[symbol].get("network", ""),
                )
                coins[symbol]["address"] = request.form.get(
                    f"address_{symbol}",
                    coins[symbol].get("address", ""),
                )
                coins[symbol]["logo"] = request.form.get(
                    f"logo_{symbol}",
                    coins[symbol].get("logo", ""),
                )
                coins[symbol]["custom_emoji_id"] = request.form.get(
                    f"custom_emoji_id_{symbol}",
                    coins[symbol].get("custom_emoji_id", ""),
                ).strip()
                coins[symbol]["active"] = request.form.get(
                    f"active_{symbol}",
                    "off",
                )

        elif action == "complete_order":
            update_order_status(
                request.form.get("order_id", ""),
                "completed",
                approval_reason=request.form.get("approval_reason", ""),
            )

        elif action == "reject_order":
            update_order_status(
                request.form.get("order_id", ""),
                "rejected",
                request.form.get("reject_reason", ""),
            )

        elif action == "archive_order":
            oid = str(request.form.get("order_id", ""))

            if oid in orders and orders[oid].get("status") != "⏳ Bekliyor":
                orders[oid]["archived"] = True

        elif action == "restore_order":
            oid = str(request.form.get("order_id", ""))

            if oid in orders:
                orders[oid]["archived"] = False

        save_json("settings.json", settings)
        save_json("messages.json", messages)
        save_json("coins.json", coins)
        save_json("orders.json", orders)
        save_json("referrals.json", referrals)

        return_view = request.form.get("return_view", "active")

        if return_view not in {"active", "completed", "rejected", "all"}:
            return_view = "active"

        return redirect(f"/admin?view={return_view}")

    current_view = request.args.get("view", "active")

    if current_view not in {"active", "completed", "rejected", "all"}:
        current_view = "active"

    counts = order_counts()
    order_cards = render_order_cards(current_view)

    coin_rows = ""

    for symbol, coin in coins.items():
        checked = "checked" if coin.get("active") == "on" else ""

        coin_rows += f"""
        <tr>
            <td><strong>{h(symbol)}</strong></td>
            <td><input name="emoji_{h(symbol)}" value="{h(coin.get('emoji', ''))}" class="small"></td>
            <td><input name="name_{h(symbol)}" value="{h(coin.get('name', ''))}"></td>
            <td><input name="network_{h(symbol)}" value="{h(coin.get('network', ''))}"></td>
            <td><input name="address_{h(symbol)}" value="{h(coin.get('address', ''))}"></td>
            <td><input name="logo_{h(symbol)}" value="{h(coin.get('logo', ''))}"></td>
            <td><input name="custom_emoji_id_{h(symbol)}" value="{h(coin.get('custom_emoji_id', ''))}" placeholder="Custom Emoji ID"></td>
            <td><input type="checkbox" name="active_{h(symbol)}" value="on" {checked}></td>
        </tr>
        """

    message_fields = [
        ("welcome", "Hoş geldin mesajı"),
        ("swap_menu", "İşlem türü seçim mesajı"),
        ("coin_select_crypto_to_crypto", "Kripto → Kripto: gönderilecek coin seçim mesajı"),
        ("coin_select_crypto_to_crypto_receive", "Kripto → Kripto: alınacak coin seçim mesajı"),
        ("coin_select_iban_to_crypto", "IBAN → Kripto coin seçim mesajı"),
        ("coin_select_crypto_to_iban", "Kripto → IBAN coin seçim mesajı"),
        ("amount_question_crypto_to_crypto", "Kripto → Kripto miktar sorusu"),
        ("amount_question_iban_to_crypto", "IBAN → Kripto miktar sorusu"),
        ("amount_question_crypto_to_iban", "Kripto → IBAN miktar sorusu"),
        ("wallet_question", "Cüzdan adresi sorusu"),
        ("iban_question", "IBAN sorusu"),
        ("name_question", "Ad soyad sorusu"),
        ("order_created", "Sipariş oluşturuldu mesajı"),
        ("order_completed", "Sipariş tamamlandı mesajı"),
        ("order_rejected", "Sipariş reddedildi mesajı"),
        ("orders_title", "Siparişlerim başlığı"),
        ("orders_empty", "Sipariş yok mesajı"),
        ("fees_title", "Komisyonlar başlığı"),
        ("iban_closed", "IBAN kapalı mesajı"),
        ("session_expired", "İşlem süresi doldu mesajı"),
        ("help", "Nasıl çalışır mesajı"),
        ("support", "Destek mesajı"),
        ("iban_warning", "IBAN uyarı mesajı"),
        ("working_hours", "Çalışma saatleri / durum metni"),
        ("min_amount_error", "Minimum miktar hata mesajı"),
        ("referral_title", "Referans başlığı"),
        ("referral_text", "Referans mesajı"),
        ("referral_registered", "Referans kayıt mesajı"),
    ]

    button_fields = [
        ("button_start_swap", "Ana menü: Swap Başlat"),
        ("button_my_orders", "Ana menü: Siparişlerim"),
        ("button_fees", "Ana menü: Komisyonlar"),
        ("button_help", "Ana menü: Nasıl Çalışır?"),
        ("button_referral", "Ana menü: Referansım"),
        ("button_support", "Ana menü: Destek"),
        ("button_iban_to_crypto", "İşlem türü: IBAN → Kripto"),
        ("button_crypto_to_iban", "İşlem türü: Kripto → IBAN"),
        ("button_crypto_to_crypto", "İşlem türü: Kripto → Kripto"),
        ("button_main_menu", "Ana Menü butonu"),
        ("button_back", "Geri butonu"),
    ]

    icon_fields = [
        ("icon_start_swap", "Swap Başlat custom emoji ID"),
        ("icon_my_orders", "Siparişlerim custom emoji ID"),
        ("icon_fees", "Komisyonlar custom emoji ID"),
        ("icon_help", "Nasıl Çalışır custom emoji ID"),
        ("icon_referral", "Referansım custom emoji ID"),
        ("icon_support", "Destek custom emoji ID"),
        ("icon_iban_to_crypto", "IBAN → Kripto custom emoji ID"),
        ("icon_crypto_to_iban", "Kripto → IBAN custom emoji ID"),
        ("icon_crypto_to_crypto", "Kripto → Kripto custom emoji ID"),
        ("icon_main_menu", "Ana Menü custom emoji ID"),
        ("icon_back", "Geri custom emoji ID"),
    ]

    message_inputs = ""

    for key, label in message_fields:
        value = messages.get(key, MESSAGE_DEFAULTS.get(key, ""))

        if key == "working_hours":
            message_inputs += (
                f"<label>{h(label)}</label>"
                f"<input name='{h(key)}' value='{h(value)}'>"
            )
        else:
            message_inputs += (
                f"<label>{h(label)}</label>"
                f"<textarea name='{h(key)}'>{h(value)}</textarea>"
            )

    button_inputs = ""

    for key, label in button_fields:
        button_inputs += (
            f"<label>{h(label)}</label>"
            f"<input name='{h(key)}' value='{h(messages.get(key, MESSAGE_DEFAULTS.get(key, '')))}'>"
        )

    icon_inputs = ""

    for key, label in icon_fields:
        icon_inputs += (
            f"<label>{h(label)}</label>"
            f"<input name='{h(key)}' value='{h(messages.get(key, ''))}' "
            f"placeholder='Custom Emoji ID'>"
        )

    return f"""
    <!doctype html>
    <html lang="tr">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Zaqel Admin</title>

        <style>
            :root {{
                color-scheme: dark;
                --bg: #080b12;
                --panel: rgba(20, 25, 36, 0.86);
                --panel-solid: #111722;
                --input: #0c111a;
                --border: #293246;
                --text: #f6f8fc;
                --muted: #96a0b4;
                --red: #ff4758;
                --green: #28c77a;
                --blue: #607dff;
                --purple: #9265ff;
                --orange: #f4a62a;
                --shadow: 0 20px 60px rgba(0, 0, 0, .32);
            }}

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                min-height: 100vh;
                font-family: Inter, Arial, sans-serif;
                color: var(--text);
                background:
                    radial-gradient(circle at 10% 0%, rgba(96, 125, 255, .18), transparent 34%),
                    radial-gradient(circle at 90% 4%, rgba(146, 101, 255, .16), transparent 30%),
                    var(--bg);
            }}

            .container {{
                width: min(1380px, calc(100% - 28px));
                margin: 0 auto;
                padding: 26px 0 70px;
            }}
            .logout-row {{
                display: flex;
                justify-content: flex-end;
                align-items: center;
                gap: 10px;
                margin-bottom: 16px;
            }}


.logout {{
                color: white;
                text-decoration: none;
                border: 1px solid var(--border);
                border-radius: 11px;
                padding: 10px 14px;
                background: rgba(255, 255, 255, .04);
            }}

            .box {{
                padding: 22px;
                margin-bottom: 20px;
                border: 1px solid var(--border);
                border-radius: 20px;
                background: var(--panel);
                box-shadow: 0 10px 28px rgba(0, 0, 0, .20);
            }}

            details.box {{
                padding: 0;
                overflow: hidden;
                content-visibility: auto;
                contain-intrinsic-size: 72px 520px;
            }}

            .order-card {{
                content-visibility: auto;
                contain-intrinsic-size: 360px;
            }}

            .table-wrap {{
                contain: layout paint;
            }}

            @media (prefers-reduced-motion: reduce) {{
                * {{
                    scroll-behavior: auto !important;
                    transition: none !important;
                    animation: none !important;
                }}
            }}

            details.box > summary {{
                list-style: none;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
                padding: 20px 22px;
                cursor: pointer;
                user-select: none;
                font-size: 20px;
                font-weight: 800;
                transition: background .2s ease;
            }}

            details.box > summary::-webkit-details-marker {{
                display: none;
            }}

            details.box > summary:hover {{
                background: rgba(255,255,255,.035);
            }}

            details.box > summary::after {{
                content: "＋";
                display: inline-grid;
                place-items: center;
                width: 34px;
                height: 34px;
                flex: 0 0 34px;
                border-radius: 10px;
                background: rgba(96,125,255,.16);
                border: 1px solid rgba(96,125,255,.35);
                font-size: 22px;
                transition: transform .22s ease, background .22s ease;
            }}

            details.box[open] > summary::after {{
                content: "−";
                transform: rotate(180deg);
                background: rgba(146,101,255,.18);
                border-color: rgba(146,101,255,.38);
            }}

            .collapsible-content {{
                padding: 0 22px 22px;
                border-top: 1px solid rgba(255,255,255,.05);
            }}

            .collapsible-content .section-note {{
                margin-top: 16px;
            }}

            h2 {{
                margin: 0 0 14px;
            }}

            .section-note {{
                color: var(--muted);
                line-height: 1.55;
                margin-bottom: 16px;
            }}

            label {{
                display: block;
                color: var(--muted);
                margin: 14px 0 7px;
                font-size: 14px;
            }}

            input,
            textarea {{
                width: 100%;
                color: white;
                background: var(--input);
                border: 1px solid #344058;
                border-radius: 11px;
                padding: 11px 12px;
                outline: none;
                transition: border-color .2s, box-shadow .2s;
            }}

            input:focus,
            textarea:focus {{
                border-color: var(--blue);
                box-shadow: 0 0 0 3px rgba(96, 125, 255, .14);
            }}

            textarea {{
                min-height: 88px;
                resize: vertical;
            }}

            button {{
                border: 0;
                border-radius: 11px;
                padding: 11px 17px;
                color: white;
                font-weight: 700;
                cursor: pointer;
                background: linear-gradient(135deg, var(--red), #d92d42);
                transition: transform .18s, opacity .18s;
            }}

            button:hover {{
                transform: translateY(-1px);
                opacity: .94;
            }}

            .tabs {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 16px 0 20px;
            }}

            .tab {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 10px 14px;
                color: var(--muted);
                text-decoration: none;
                border: 1px solid var(--border);
                border-radius: 999px;
                background: rgba(10, 14, 22, .72);
            }}

            .tab.active {{
                color: white;
                border-color: var(--blue);
                background: rgba(96, 125, 255, .18);
                box-shadow: 0 0 0 3px rgba(96, 125, 255, .08);
            }}

            .count {{
                min-width: 24px;
                height: 24px;
                display: inline-grid;
                place-items: center;
                padding: 0 7px;
                border-radius: 999px;
                font-size: 12px;
                background: rgba(255, 255, 255, .08);
            }}

            .order-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
                gap: 16px;
            }}

            .order-card {{
                padding: 17px;
                border: 1px solid var(--border);
                border-radius: 16px;
                background: linear-gradient(180deg, rgba(17, 23, 34, .98), rgba(10, 14, 22, .98));
                box-shadow: 0 14px 36px rgba(0, 0, 0, .22);
                transition: transform .2s, border-color .2s;
            }}

            .order-card:hover {{
                transform: translateY(-2px);
                border-color: #48546e;
            }}

            .order-card.pending {{
                border-top: 3px solid var(--orange);
            }}

            .order-card.completed {{
                border-top: 3px solid var(--green);
            }}

            .order-card.rejected {{
                border-top: 3px solid var(--red);
            }}

            .order-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                padding-bottom: 12px;
                margin-bottom: 12px;
                border-bottom: 1px solid var(--border);
            }}

            .order-head h3 {{
                margin: 0;
            }}

            .status {{
                font-size: 14px;
                color: #d7dce8;
            }}

            .details {{
                display: grid;
                gap: 7px;
            }}

            .detail {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                padding: 7px 0;
                border-bottom: 1px dashed #263044;
            }}

            .detail span {{
                color: var(--muted);
            }}

            .detail strong {{
                max-width: 62%;
                text-align: right;
                overflow-wrap: anywhere;
            }}

            .actions {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 10px;
                margin-top: 16px;
                align-items: start;
            }}

            .actions form {{
                margin: 0;
            }}

            .actions button {{
                width: 100%;
            }}

            .reject-reason {{
                min-height: 76px;
                margin-bottom: 8px;
            }}

            .complete {{
                background: linear-gradient(135deg, var(--green), #16995a);
            }}

            .reject {{
                background: linear-gradient(135deg, var(--red), #d62f44);
            }}

            .archive {{
                width: 100%;
                background: linear-gradient(135deg, #465066, #323a4b);
            }}

            .restore {{
                width: 100%;
                background: linear-gradient(135deg, var(--blue), #455ad6);
            }}

            .single-action {{
                margin-top: 16px;
            }}

            .two-col {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 20px;
            }}

            .settings-grid {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 20px;
            }}

            .save {{
                width: 100%;
                margin: 2px 0 20px;
                padding: 14px;
                font-size: 16px;
                background: linear-gradient(135deg, var(--blue), var(--purple));
            }}

            .table-wrap {{
                overflow-x: auto;
            }}

            table {{
                width: 100%;
                min-width: 980px;
                border-collapse: collapse;
            }}

            th,
            td {{
                padding: 11px;
                text-align: left;
                vertical-align: middle;
                border-bottom: 1px solid var(--border);
            }}

            .small {{
                min-width: 70px;
            }}

            .empty-state {{
                grid-column: 1 / -1;
                padding: 44px 20px;
                text-align: center;
                color: var(--muted);
                border: 1px dashed var(--border);
                border-radius: 16px;
            }}

            @media (max-width: 980px) {{
                .two-col,
                .settings-grid {{
                    grid-template-columns: 1fr;
                }}
            }}

            @media (hover: none) {{
                .order-card:hover {{
                    transform: none;
                }}

                button:hover {{
                    transform: none;
                }}
            }}

            @media (max-width: 680px) {{
                .container {{
                    width: min(100% - 16px, 1380px);
                }}

                .box {{
                    padding: 15px;
                    border-radius: 15px;
                }}

                details.box {{
                    padding: 0;
                }}

                details.box > summary {{
                    padding: 16px;
                    font-size: 17px;
                }}

                .collapsible-content {{
                    padding: 0 16px 16px;
                }}

                .order-grid {{
                    grid-template-columns: 1fr;
                }}

                .detail {{
                    display: block;
                }}

                .detail strong {{
                    display: block;
                    max-width: 100%;
                    margin-top: 4px;
                    text-align: left;
                }}

                .actions {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>

    <body>
        <main class="container">
            <div class="logout-row">
                <a class="logout" href="/logout">Çıkış</a>
            </div>

            <section class="box">
                <h2>📦 Sipariş Kontrol Alanı</h2>

                <div class="section-note">
                    Bekleyen siparişler ana ekranda kalır. Tamamlanan veya reddedilen siparişleri
                    listeden kaldırabilirsiniz; Tüm Siparişler bölümünde her zaman görünmeye devam ederler.
                </div>

                <nav class="tabs">
                    <a class="tab {'active' if current_view == 'active' else ''}" href="/admin?view=active">
                        ⏳ Bekleyen <span class="count">{counts['active']}</span>
                    </a>

                    <a class="tab {'active' if current_view == 'completed' else ''}" href="/admin?view=completed">
                        ✅ Tamamlanan <span class="count">{counts['completed']}</span>
                    </a>

                    <a class="tab {'active' if current_view == 'rejected' else ''}" href="/admin?view=rejected">
                        ❌ Reddedilen <span class="count">{counts['rejected']}</span>
                    </a>

                    <a class="tab {'active' if current_view == 'all' else ''}" href="/admin?view=all">
                        📚 Tüm Siparişler <span class="count">{counts['all']}</span>
                    </a>
                </nav>

                <div
                    id="order-grid"
                    class="order-grid"
                    data-view="{h(current_view)}"
                    data-pending-count="{counts['active']}"
                >
                    {order_cards}
                </div>
            </section>

            <form method="post">
                <input type="hidden" name="action" value="settings">
                <input type="hidden" name="return_view" value="{h(current_view)}">

                <div class="two-col">
                    <details class="box">
                        <summary>📝 Bot Mesajları</summary>
                        <div class="collapsible-content">
                        <div class="section-note">
                            Kullanıcıya gönderilen temel bot metinlerinin tamamını buradan düzenleyebilirsiniz.
                        </div>
                        {message_inputs}

                        </div>
                    </details>

                    <details class="box">
                        <summary>🔘 Buton Yazıları</summary>
                        <div class="collapsible-content">
                        <div class="section-note">
                            Telegram menülerindeki buton metinlerini buradan değiştirebilirsiniz.
                            Custom emoji kullanırken buton metnindeki normal emojiyi silebilirsiniz.
                        </div>
                        {button_inputs}

                        </div>
                    </details>
                </div>

                <details class="box">
                    <summary>🧩 Tüm Menü Custom Emojileri</summary>
                    <div class="collapsible-content">
                    <div class="section-note">
                        ZIP içindeki görseller doğrudan bu alanlara yüklenmez. Önce görselleri Telegram'da
                        custom emoji paketi olarak ekleyin. Sonra her emojiyi Zaqel botuna tek başına gönderin;
                        bot size ID değerini cevap olarak verir. İlgili ID'yi aşağıdaki alana yapıştırın.
                    </div>
                    <div class="settings-grid">
                        {icon_inputs}
                    </div>

                    </div>
                </details>

                <div class="settings-grid">
                    <details class="box">
                        <summary>💰 Komisyon Yönetimi</summary>
                        <div class="collapsible-content">

                        <label>Kripto → Kripto %</label>
                        <input name="fee_crypto_to_crypto" value="{h(settings.get('fee_crypto_to_crypto', ''))}">

                        <label>IBAN → Kripto %</label>
                        <input name="fee_iban_to_crypto" value="{h(settings.get('fee_iban_to_crypto', ''))}">

                        <label>Kripto → IBAN %</label>
                        <input name="fee_crypto_to_iban" value="{h(settings.get('fee_crypto_to_iban', ''))}">

                        </div>
                    </details>

                    <details class="box">
                        <summary>📉 Minimum Ödeme</summary>
                        <div class="collapsible-content">

                        <label>Min Kripto → Kripto TL</label>
                        <input name="min_crypto_to_crypto" value="{h(settings.get('min_crypto_to_crypto', ''))}">

                        <label>Min IBAN → Kripto TL</label>
                        <input name="min_iban_to_crypto" value="{h(settings.get('min_iban_to_crypto', ''))}">

                        <label>Min Kripto → IBAN TL</label>
                        <input name="min_crypto_to_iban" value="{h(settings.get('min_crypto_to_iban', ''))}">

                        </div>
                    </details>

                    <details class="box">
                        <summary>🏦 IBAN Yönetimi</summary>
                        <div class="collapsible-content">

                        <label>Banka adı</label>
                        <input name="bank_name" value="{h(settings.get('bank_name', ''))}">

                        <label>IBAN</label>
                        <input name="iban" value="{h(settings.get('iban', ''))}">

                        <label>Alıcı adı soyadı</label>
                        <input name="iban_owner" value="{h(settings.get('iban_owner', ''))}">

                        <label>Aktiflik: on / off</label>
                        <input name="iban_active" value="{h(settings.get('iban_active', 'on'))}">

                        </div>
                    </details>
                </div>

                <button class="save" type="submit">
                    💾 Tüm Mesaj, Buton ve Ayarları Kaydet
                </button>
            </form>


            <details class="box">
                <summary>👥 Referans Sistemi</summary>
                <div class="collapsible-content">
                <div class="section-note">
                    Kullanıcılar kendi referans linkini /referans komutu veya Referansım butonu ile alır.
                    Linkle gelen kullanıcı ilk /start sırasında referans verene bağlanır. Sistem 7/24 sipariş almaya devam eder.
                </div>
                <div class="details">
                    <div class="detail"><span>Toplam referans profili</span><strong>{len(referrals)}</strong></div>
                    <div class="detail"><span>Toplam davet kaydı</span><strong>{sum(len(v.get('invited', [])) for v in referrals.values() if isinstance(v, dict))}</strong></div>
                </div>
                </div>
            </details>

            <details class="box">
                <summary>🪙 Coin Yönetimi</summary>
                <div class="collapsible-content">
                <div class="section-note">
                    Coin custom emojisini Zaqel botuna tek başına gönderin.
                    Bot size Custom Emoji ID değerini cevap olarak verir.
                    Bu değeri ilgili coin alanına yapıştırıp Coinleri Kaydet'e basın.
                </div>

                <form method="post">
                    <input type="hidden" name="action" value="update_coins">
                    <input type="hidden" name="return_view" value="{h(current_view)}">

                    <div class="table-wrap">
                        <table>
                            <tr>
                                <th>Sembol</th>
                                <th>Emoji</th>
                                <th>Ad</th>
                                <th>Ağ</th>
                                <th>Ödeme Adresi</th>
                                <th>Logo URL</th>
                                <th>Custom Emoji ID</th>
                                <th>Aktif</th>
                            </tr>
                            {coin_rows}
                        </table>
                    </div>

                    <br>
                    <button type="submit">Coinleri Kaydet</button>
                </form>

                </div>
            </details>

            <details class="box">
                <summary>➕ Yeni Coin Ekle</summary>
                <div class="collapsible-content">

                <form method="post">
                    <input type="hidden" name="action" value="add_coin">
                    <input type="hidden" name="return_view" value="{h(current_view)}">

                    <label>Sembol</label>
                    <input name="symbol" placeholder="BTC" required>

                    <label>Ad</label>
                    <input name="name" placeholder="Bitcoin">

                    <label>Emoji</label>
                    <input name="emoji" placeholder="🟠">

                    <label>Ağ</label>
                    <input name="network" placeholder="BTC / BEP20 / ERC20">

                    <label>Ödeme Adresi</label>
                    <input name="address">

                    <label>Logo URL</label>
                    <input name="logo">

                    <label>Custom Emoji ID</label>
                    <input name="custom_emoji_id" placeholder="Örn: 5368324170671202286">

                    <label>Aktiflik: on / off</label>
                    <input name="active" value="on">

                    <br><br>
                    <button type="submit">Coin Ekle</button>
                </form>

                </div>
            </details>
        </main>

        <script>
            let soundEnabled = true;
            let audioContext = null;
            let audioUnlocked = false;
            let previousPendingCount = Number(
                document.getElementById("order-grid")?.dataset.pendingCount || 0
            );

            function ensureAudioContext() {{
                if (!audioContext) {{
                    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                    if (AudioContextClass) {{
                        audioContext = new AudioContextClass();
                    }}
                }}

                if (audioContext && audioContext.state === "suspended") {{
                    audioContext.resume();
                }}

                audioUnlocked = Boolean(
                    audioContext && audioContext.state === "running"
                );
            }}

            function unlockAudioOnce() {{
                ensureAudioContext();

                if (!audioContext || !audioUnlocked) {{
                    return;
                }}

                // Sessiz ve çok kısa bir sesle tarayıcı ses iznini aktif hale getirir.
                const oscillator = audioContext.createOscillator();
                const gain = audioContext.createGain();

                gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
                oscillator.connect(gain);
                gain.connect(audioContext.destination);
                oscillator.start();
                oscillator.stop(audioContext.currentTime + 0.01);

                document.removeEventListener("click", unlockAudioOnce);
                document.removeEventListener("touchstart", unlockAudioOnce);
                document.removeEventListener("keydown", unlockAudioOnce);
            }}

            document.addEventListener("click", unlockAudioOnce, {{ passive: true }});
            document.addEventListener("touchstart", unlockAudioOnce, {{ passive: true }});
            document.addEventListener("keydown", unlockAudioOnce);

            // Tarayıcı izin verirse sayfa açılır açılmaz sesi hazır etmeyi dener.
            ensureAudioContext();

            function playOrderTink() {{
                if (!soundEnabled) return;

                ensureAudioContext();
                if (!audioContext || audioContext.state !== "running") return;

                const now = audioContext.currentTime;
                const master = audioContext.createGain();
                master.connect(audioContext.destination);
                master.gain.setValueAtTime(0.0001, now);
                master.gain.exponentialRampToValueAtTime(0.22, now + 0.01);
                master.gain.exponentialRampToValueAtTime(0.0001, now + 0.65);

                const osc1 = audioContext.createOscillator();
                const osc2 = audioContext.createOscillator();

                osc1.type = "sine";
                osc2.type = "sine";
                osc1.frequency.setValueAtTime(1318.51, now);
                osc2.frequency.setValueAtTime(1760.00, now + 0.08);

                osc1.connect(master);
                osc2.connect(master);

                osc1.start(now);
                osc1.stop(now + 0.35);
                osc2.start(now + 0.08);
                osc2.stop(now + 0.5);
            }}

            let lastOrderHtml = "";
            let refreshInProgress = false;

            function userIsEditing() {{
                const active = document.activeElement;

                if (!active) {{
                    return false;
                }}

                return (
                    active.tagName === "INPUT" ||
                    active.tagName === "TEXTAREA" ||
                    active.tagName === "SELECT"
                );
            }}

            async function refreshOrders() {{
                if (
                    document.hidden ||
                    userIsEditing() ||
                    refreshInProgress
                ) {{
                    return;
                }}

                const grid = document.getElementById("order-grid");

                if (!grid) {{
                    return;
                }}

                const view = grid.dataset.view || "active";
                refreshInProgress = true;

                try {{
                    const response = await fetch(
                        "/admin/orders-fragment?view=" + encodeURIComponent(view),
                        {{
                            cache: "no-store",
                            headers: {{
                                "X-Requested-With": "fetch"
                            }}
                        }}
                    );

                    if (!response.ok) {{
                        return;
                    }}

                    const html = await response.text();
                    const parser = new DOMParser();
                    const documentFragment = parser.parseFromString(html, "text/html");
                    const wrapper = documentFragment.querySelector("[data-fragment-pending-count]");
                    const newPendingCount = Number(
                        wrapper?.dataset.fragmentPendingCount || previousPendingCount
                    );
                    const nextHtml = wrapper ? wrapper.innerHTML : html;

                    if (newPendingCount > previousPendingCount) {{
                        playOrderTink();
                    }}

                    previousPendingCount = newPendingCount;
                    grid.dataset.pendingCount = String(newPendingCount);

                    if (nextHtml !== lastOrderHtml && nextHtml !== grid.innerHTML) {{
                        grid.innerHTML = nextHtml;
                        lastOrderHtml = nextHtml;
                    }}
                }} catch (error) {{
                    console.log("Sipariş yenileme hatası:", error);
                }} finally {{
                    refreshInProgress = false;
                }}
            }}

            document.addEventListener("visibilitychange", () => {{
                if (!document.hidden) {{
                    refreshOrders();
                }}
            }});

            document.querySelectorAll("details.box").forEach((details) => {{
                details.addEventListener("toggle", () => {{
                    if (!details.open) {{
                        return;
                    }}

                    const parent = details.parentElement;

                    if (!parent) {{
                        return;
                    }}

                    parent.querySelectorAll(":scope > details.box[open]").forEach((other) => {{
                        if (other !== details) {{
                            other.open = false;
                        }}
                    }});
                }});
            }});

            setInterval(refreshOrders, 8000);
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
