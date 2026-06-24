import os
import io
import base64
import binascii
import time
import json
import random
import hashlib
import hmac
import threading
import secrets
import shutil
import re
import contextlib
import uuid
import socket
import traceback
from urllib.parse import urlparse
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
BLOCKCYPHER_KEY = os.getenv("BLOCKCYPHER_KEY", "").strip()
ALCHEMY_KEY = os.getenv("ALCHEMY_KEY", "").strip()
TRONGRID_KEY = os.getenv("TRONGRID_KEY", "").strip()
WALLET_SCAN_SECONDS = max(30, int(os.getenv("WALLET_SCAN_SECONDS", "60")))

# Exchange core / blockchain infrastructure
EXCHANGE_MODE = os.getenv("EXCHANGE_MODE", "on").strip().lower() == "on"
BACKGROUND_SERVICES_ENABLED = os.getenv("START_BACKGROUND_SERVICES", "1") == "1"
EXCHANGE_WORKER_ID = os.getenv("EXCHANGE_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
EXCHANGE_JOB_POLL_SECONDS = max(1, int(os.getenv("EXCHANGE_JOB_POLL_SECONDS", "3")))
EXCHANGE_JOB_LOCK_SECONDS = max(60, int(os.getenv("EXCHANGE_JOB_LOCK_SECONDS", "300")))
EXCHANGE_MAX_JOB_ATTEMPTS = max(3, int(os.getenv("EXCHANGE_MAX_JOB_ATTEMPTS", "12")))
EXCHANGE_RECONCILE_SECONDS = max(300, int(os.getenv("EXCHANGE_RECONCILE_SECONDS", "3600")))
INDEXER_BACKFILL_BLOCKS = max(0, int(os.getenv("INDEXER_BACKFILL_BLOCKS", "30")))
INDEXER_MAX_BLOCKS_PER_PASS = max(1, int(os.getenv("INDEXER_MAX_BLOCKS_PER_PASS", "25")))
ETH_REORG_BACKTRACK_BLOCKS = max(6, int(os.getenv("ETH_REORG_BACKTRACK_BLOCKS", "24")))
INDEXER_ADDRESS_LIMIT = max(1, int(os.getenv("INDEXER_ADDRESS_LIMIT", "500")))
ALLOW_SHARED_DEPOSIT_ADDRESSES = os.getenv("ALLOW_SHARED_DEPOSIT_ADDRESSES", "0") == "1"

BTC_XPUB = os.getenv("BTC_XPUB", "").strip()
LTC_XPUB = os.getenv("LTC_XPUB", "").strip()
ETH_XPUB = os.getenv("ETH_XPUB", "").strip()
TRON_XPUB = os.getenv("TRON_XPUB", "").strip()
BTC_ADDRESS_TYPE = os.getenv("BTC_ADDRESS_TYPE", "p2pkh").strip().lower()
LTC_ADDRESS_TYPE = os.getenv("LTC_ADDRESS_TYPE", "p2pkh").strip().lower()
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "").strip() or (f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}" if ALCHEMY_KEY else "")
TRONGRID_BASE_URL = os.getenv("TRONGRID_BASE_URL", "https://api.trongrid.io").strip().rstrip("/")
USDT_TRC20_CONTRACT = os.getenv("USDT_TRC20_CONTRACT", "").strip()
XMR_WALLET_RPC_URL = os.getenv("XMR_WALLET_RPC_URL", "").strip()
XMR_WALLET_RPC_USERNAME = os.getenv("XMR_WALLET_RPC_USERNAME", "").strip()
XMR_WALLET_RPC_PASSWORD = os.getenv("XMR_WALLET_RPC_PASSWORD", "").strip()
XMR_ACCOUNT_INDEX = max(0, int(os.getenv("XMR_ACCOUNT_INDEX", "0")))
EVM_TOKEN_CONTRACTS_JSON = os.getenv("EVM_TOKEN_CONTRACTS_JSON", "{}").strip()
WITHDRAW_SIGNER_URL = os.getenv("WITHDRAW_SIGNER_URL", "").strip()
WITHDRAW_SIGNER_TOKEN = os.getenv("WITHDRAW_SIGNER_TOKEN", "").strip()
WITHDRAW_STATUS_URL = os.getenv("WITHDRAW_STATUS_URL", "").strip()
EXCHANGE_INTERNAL_TOKEN = os.getenv("EXCHANGE_INTERNAL_TOKEN", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
BUILD_VERSION = "NERLO-2026-06-25-PRO-PANEL-V5-FRESH"

CONFIRMATION_THRESHOLDS = {
    "BTC": max(1, int(os.getenv("BTC_CONFIRMATIONS", "3"))),
    "LTC": max(1, int(os.getenv("LTC_CONFIRMATIONS", "6"))),
    "ETH": max(1, int(os.getenv("ETH_CONFIRMATIONS", "12"))),
    "TRX": max(1, int(os.getenv("TRX_CONFIRMATIONS", "19"))),
    "USDT": max(1, int(os.getenv("USDT_CONFIRMATIONS", "19"))),
    "XMR": max(1, int(os.getenv("XMR_CONFIRMATIONS", "10"))),
}
AUTO_DEPOSIT_ASSETS = {"BTC", "LTC", "ETH", "TRX", "USDT", "XMR"}
AUTO_WITHDRAW_ASSETS = {"BTC", "LTC", "ETH", "TRX", "USDT"}
CHAIN_BY_ASSET = {"BTC": "BTC", "LTC": "LTC", "ETH": "ETH", "TRX": "TRON", "USDT": "TRON", "XMR": "XMR"}

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1", PERMANENT_SESSION_LIFETIME=1800)

ASSETS = ["TL", "USDT", "LTC", "TRX", "XMR", "BTC", "ETH", "TON"]
CRYPTO_ASSETS = ["USDT", "LTC", "TRX", "XMR", "BTC", "ETH", "TON"]
ASSET_PATTERN = "|".join(map(re.escape, ASSETS))
ASSET_PRECISIONS = {
    "TL": Decimal("0.01"), "USDT": Decimal("0.01"), "TRX": Decimal("0.01"),
    "LTC": Decimal("0.000001"), "XMR": Decimal("0.000001"), "TON": Decimal("0.000001"),
    "BTC": Decimal("0.00000001"), "ETH": Decimal("0.00000001"),
}
data_lock = threading.RLock()
_runtime_state_refresh_lock = threading.Lock()
_runtime_state_refresh_at = {}
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
    """Create the durable exchange schema.

    app_state remains as a compatibility layer for the existing bot/panel data.
    Balances, ledger entries, chain events, addresses and jobs live in dedicated
    transactional tables and are the source of truth for money movement.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS app_state (
        state_key TEXT PRIMARY KEY,
        state_data JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS exchange_profiles (
        user_id TEXT PRIMARY KEY,
        profile JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS exchange_accounts (
        user_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        available NUMERIC(50, 18) NOT NULL DEFAULT 0,
        pending NUMERIC(50, 18) NOT NULL DEFAULT 0,
        locked NUMERIC(50, 18) NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, asset),
        CHECK (available >= 0),
        CHECK (pending >= 0),
        CHECK (locked >= 0)
    );

    CREATE TABLE IF NOT EXISTS exchange_ledger (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        bucket TEXT NOT NULL CHECK (bucket IN ('available', 'pending', 'locked')),
        amount NUMERIC(50, 18) NOT NULL,
        entry_type TEXT NOT NULL,
        reference_type TEXT NOT NULL DEFAULT '',
        reference_id TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL UNIQUE,
        balance_after NUMERIC(50, 18) NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_exchange_ledger_user_asset_created
        ON exchange_ledger (user_id, asset, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_exchange_ledger_reference
        ON exchange_ledger (reference_type, reference_id);

    CREATE TABLE IF NOT EXISTS exchange_addresses (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL,
        chain TEXT NOT NULL,
        address TEXT NOT NULL,
        derivation_index BIGINT,
        derivation_path TEXT NOT NULL DEFAULT '',
        memo TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'xpub',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, chain),
        UNIQUE (chain, address)
    );
    CREATE INDEX IF NOT EXISTS idx_exchange_addresses_chain_status
        ON exchange_addresses (chain, status);

    CREATE TABLE IF NOT EXISTS exchange_meta (
        meta_key TEXT PRIMARY KEY,
        meta_value JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS exchange_chain_events (
        id BIGSERIAL PRIMARY KEY,
        chain TEXT NOT NULL,
        asset TEXT NOT NULL,
        txid TEXT NOT NULL,
        event_index TEXT NOT NULL DEFAULT '0',
        address TEXT NOT NULL,
        user_id TEXT NOT NULL,
        amount NUMERIC(50, 18) NOT NULL,
        block_height BIGINT NOT NULL DEFAULT 0,
        confirmations INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'detected',
        request_id TEXT NOT NULL DEFAULT '',
        raw JSONB NOT NULL DEFAULT '{}'::jsonb,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        confirmed_at TIMESTAMPTZ,
        credited_at TIMESTAMPTZ,
        generation INTEGER NOT NULL DEFAULT 0,
        UNIQUE (chain, asset, txid, event_index, address)
    );
    ALTER TABLE exchange_chain_events ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_exchange_chain_events_status
        ON exchange_chain_events (status, chain, block_height);
    CREATE INDEX IF NOT EXISTS idx_exchange_chain_events_user
        ON exchange_chain_events (user_id, first_seen_at DESC);

    CREATE TABLE IF NOT EXISTS exchange_jobs (
        id BIGSERIAL PRIMARY KEY,
        queue_name TEXT NOT NULL DEFAULT 'default',
        job_type TEXT NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 12,
        available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        locked_at TIMESTAMPTZ,
        locked_by TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        dedupe_key TEXT UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_exchange_jobs_claim
        ON exchange_jobs (status, available_at, id);

    CREATE TABLE IF NOT EXISTS exchange_cursors (
        chain TEXT PRIMARY KEY,
        cursor_value BIGINT NOT NULL DEFAULT 0,
        cursor_hash TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS exchange_requests (
        request_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        request_type TEXT NOT NULL,
        status TEXT NOT NULL,
        idempotency_key TEXT,
        automatic BOOLEAN NOT NULL DEFAULT FALSE,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        UNIQUE (idempotency_key)
    );
    CREATE INDEX IF NOT EXISTS idx_exchange_requests_user_created
        ON exchange_requests (user_id, created_at DESC);
    """
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
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



# =============================================================================
def refresh_runtime_state(state_key, target, min_interval=5, force=False):
    """Refresh a legacy configuration object across multiple web workers."""
    timestamp = time.monotonic()
    with _runtime_state_refresh_lock:
        last = _runtime_state_refresh_at.get(state_key, 0)
        if not force and timestamp - last < min_interval:
            return
        _runtime_state_refresh_at[state_key] = timestamp
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state_data FROM app_state WHERE state_key=%s", (state_key,))
                row = cur.fetchone()
        if row is not None and isinstance(row[0], dict):
            with data_lock:
                target.clear()
                target.update(row[0])
    except Exception as exc:
        print(f"STATE CACHE REFRESH ERROR [{state_key}]:", exc)


# EXCHANGE CORE: watch-only HD addresses, transactional ledger, queue and indexers
# =============================================================================

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}
_SECP_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SECP_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
_KECCAK_ROTATION = (
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
)
_KECCAK_RC = (
    0x0000000000000001, 0x0000000000008082,
    0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088,
    0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B,
    0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080,
    0x0000000080000001, 0x8000000080008008,
)


def _sha256(value):
    return hashlib.sha256(value).digest()


def _hash160(value):
    return hashlib.new("ripemd160", _sha256(value)).digest()


def _b58encode(raw):
    raw = bytes(raw)
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _B58_ALPHABET[remainder] + encoded
    leading = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading + (encoded or ("" if leading else "1"))


def _b58decode(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Boş Base58 değeri")
    number = 0
    for char in text:
        if char not in _B58_INDEX:
            raise ValueError("Geçersiz Base58 karakteri")
        number = number * 58 + _B58_INDEX[char]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + raw


def _b58check_encode(payload):
    payload = bytes(payload)
    return _b58encode(payload + _sha256(_sha256(payload))[:4])


def _b58check_decode(value):
    raw = _b58decode(value)
    if len(raw) < 5:
        raise ValueError("Base58Check değeri çok kısa")
    payload, checksum = raw[:-4], raw[-4:]
    if not secrets.compare_digest(checksum, _sha256(_sha256(payload))[:4]):
        raise ValueError("Base58Check checksum doğrulanamadı")
    return payload


_BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values):
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index, generator in enumerate(generators):
            if (top >> index) & 1:
                checksum ^= generator
    return checksum


def _bech32_hrp_expand(hrp):
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _convert_bits(data, from_bits, to_bits, pad=True):
    accumulator = 0
    bits = 0
    result = []
    max_value = (1 << to_bits) - 1
    max_accumulator = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError("convert_bits input geçersiz")
        accumulator = ((accumulator << from_bits) | value) & max_accumulator
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((accumulator >> bits) & max_value)
    if pad:
        if bits:
            result.append((accumulator << (to_bits - bits)) & max_value)
    elif bits >= from_bits or ((accumulator << (to_bits - bits)) & max_value):
        raise ValueError("convert_bits padding geçersiz")
    return result


def _bech32_encode(hrp, data):
    values = _bech32_hrp_expand(hrp) + list(data) + [0] * 6
    polymod = _bech32_polymod(values) ^ 1
    checksum = [(polymod >> (5 * (5 - index))) & 31 for index in range(6)]
    return hrp + "1" + "".join(_BECH32_ALPHABET[value] for value in list(data) + checksum)


def _segwit_address(hrp, witness_version, witness_program):
    witness_program = bytes(witness_program)
    if witness_version != 0 or not 2 <= len(witness_program) <= 40:
        raise ValueError("Yalnızca SegWit v0 destekleniyor")
    return _bech32_encode(hrp, [witness_version] + _convert_bits(witness_program, 8, 5, True))


def _rotl64(value, shift):
    shift %= 64
    mask = (1 << 64) - 1
    return ((value << shift) | (value >> (64 - shift))) & mask if shift else value & mask


def _keccak_f1600(state):
    mask = (1 << 64) - 1
    for rc in _KECCAK_RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                target_x = y
                target_y = (2 * x + 3 * y) % 5
                b[target_x + 5 * target_y] = _rotl64(state[x + 5 * y], _KECCAK_ROTATION[x + 5 * y])

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
                state[x + 5 * y] &= mask
        state[0] ^= rc
    return state


def _keccak256(data):
    """Legacy Keccak-256 used by Ethereum/TRON (not NIST SHA3-256)."""
    data = bytes(data)
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(block[lane * 8:(lane + 1) * 8], "little")
        _keccak_f1600(state)
    output = bytearray()
    while len(output) < 32:
        for lane in range(rate // 8):
            output.extend(state[lane].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            _keccak_f1600(state)
    return bytes(output[:32])


def _secp_inv(value):
    return pow(value % _SECP_P, _SECP_P - 2, _SECP_P)


def _secp_add(left, right):
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 + y2) % _SECP_P == 0:
        return None
    if left == right:
        slope = (3 * x1 * x1) * _secp_inv(2 * y1)
    else:
        slope = (y2 - y1) * _secp_inv(x2 - x1)
    slope %= _SECP_P
    x3 = (slope * slope - x1 - x2) % _SECP_P
    y3 = (slope * (x1 - x3) - y1) % _SECP_P
    return x3, y3


def _secp_mul(scalar, point=_SECP_G):
    scalar %= _SECP_N
    if scalar == 0 or point is None:
        return None
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = _secp_add(result, addend)
        addend = _secp_add(addend, addend)
        scalar >>= 1
    return result


def _secp_decompress(pubkey):
    pubkey = bytes(pubkey)
    if len(pubkey) != 33 or pubkey[0] not in (2, 3):
        raise ValueError("Sıkıştırılmış secp256k1 public key bekleniyor")
    x = int.from_bytes(pubkey[1:], "big")
    if x >= _SECP_P:
        raise ValueError("Geçersiz public key X koordinatı")
    alpha = (pow(x, 3, _SECP_P) + 7) % _SECP_P
    beta = pow(alpha, (_SECP_P + 1) // 4, _SECP_P)
    y = beta if beta % 2 == pubkey[0] % 2 else _SECP_P - beta
    if (y * y - (x * x * x + 7)) % _SECP_P:
        raise ValueError("Public key eğri üzerinde değil")
    return x, y


def _secp_compress(point):
    if point is None:
        raise ValueError("Sonsuz nokta public key olamaz")
    x, y = point
    return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")


def _parse_xpub(xpub):
    payload = _b58check_decode(xpub)
    if len(payload) != 78:
        raise ValueError("Extended public key 78 byte olmalıdır")
    key_data = payload[45:78]
    if key_data[0] not in (2, 3):
        raise ValueError("Private extended key kullanılamaz; XPUB giriniz")
    _secp_decompress(key_data)
    return {
        "version": payload[:4],
        "depth": payload[4],
        "parent_fingerprint": payload[5:9],
        "child_number": int.from_bytes(payload[9:13], "big"),
        "chain_code": payload[13:45],
        "public_key": key_data,
    }


def _ckd_pub(node, index):
    index = int(index)
    if index < 0 or index >= 0x80000000:
        raise ValueError("XPUB ile yalnızca non-hardened child üretilebilir")
    parent_pub = node["public_key"]
    parent_point = _secp_decompress(parent_pub)
    while index < 0x80000000:
        digest = hmac.new(node["chain_code"], parent_pub + index.to_bytes(4, "big"), hashlib.sha512).digest()
        left, right = digest[:32], digest[32:]
        tweak = int.from_bytes(left, "big")
        if 0 < tweak < _SECP_N:
            child_point = _secp_add(_secp_mul(tweak), parent_point)
            if child_point is not None:
                return {
                    "version": node["version"],
                    "depth": min(255, int(node["depth"]) + 1),
                    "parent_fingerprint": _hash160(parent_pub)[:4],
                    "child_number": index,
                    "chain_code": right,
                    "public_key": _secp_compress(child_point),
                }
        index += 1
    raise ValueError("Geçerli child public key üretilemedi")


def _derive_xpub_key(xpub, index, branch=0):
    node = _parse_xpub(xpub)
    if branch is not None:
        node = _ckd_pub(node, int(branch))
    node = _ckd_pub(node, int(index))
    return node["public_key"]


def _derive_p2pkh_address(xpub, index, prefix, branch=0):
    pubkey = _derive_xpub_key(xpub, index, branch)
    return _b58check_encode(bytes([prefix]) + _hash160(pubkey))


def _derive_utxo_address(xpub, index, asset, branch=0):
    asset = str(asset).upper()
    pubkey = _derive_xpub_key(xpub, index, branch)
    pubkey_hash = _hash160(pubkey)
    address_type = BTC_ADDRESS_TYPE if asset == "BTC" else LTC_ADDRESS_TYPE
    if address_type == "p2pkh":
        return _b58check_encode(bytes([0x00 if asset == "BTC" else 0x30]) + pubkey_hash)
    if address_type in ("p2wpkh", "bech32"):
        return _segwit_address("bc" if asset == "BTC" else "ltc", 0, pubkey_hash)
    if address_type in ("p2sh-p2wpkh", "nested-segwit"):
        redeem_script = b"\x00\x14" + pubkey_hash
        return _b58check_encode(bytes([0x05 if asset == "BTC" else 0x32]) + _hash160(redeem_script))
    raise ValueError(f"Geçersiz {asset}_ADDRESS_TYPE: {address_type}")


def _derive_evm_address(xpub, index, branch=0):
    pubkey = _derive_xpub_key(xpub, index, branch)
    x, y = _secp_decompress(pubkey)
    uncompressed = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return "0x" + _keccak256(uncompressed)[-20:].hex()


def _derive_tron_address(xpub, index, branch=0):
    pubkey = _derive_xpub_key(xpub, index, branch)
    x, y = _secp_decompress(pubkey)
    uncompressed = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return _b58check_encode(b"\x41" + _keccak256(uncompressed)[-20:])


def _tron_hex_to_base58(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("T"):
        return text
    text = text[2:] if text.startswith("0x") else text
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return ""
    if len(raw) == 20:
        raw = b"\x41" + raw
    if len(raw) != 21 or raw[0] != 0x41:
        return ""
    return _b58check_encode(raw)


def _normalize_evm_address(value):
    text = str(value or "").strip().lower()
    if text and not text.startswith("0x"):
        text = "0x" + text
    return text

def _decode_segwit_address(value, expected_hrp):
    text = str(value or "").strip()
    if not (8 <= len(text) <= 90) or (text.lower() != text and text.upper() != text):
        raise ValueError("Geçersiz Bech32 adresi")
    text = text.lower()
    separator = text.rfind("1")
    if separator < 1 or separator + 7 > len(text):
        raise ValueError("Geçersiz Bech32 yapısı")
    hrp = text[:separator]
    if hrp != expected_hrp:
        raise ValueError("Adres ağı uyuşmuyor")
    try:
        data = [_BECH32_ALPHABET.index(char) for char in text[separator + 1:]]
    except ValueError as exc:
        raise ValueError("Geçersiz Bech32 karakteri") from exc
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    encoding = "bech32" if polymod == 1 else ("bech32m" if polymod == 0x2BC830A3 else "")
    if not encoding:
        raise ValueError("Bech32 checksum doğrulanamadı")
    payload = data[:-6]
    if not payload:
        raise ValueError("SegWit verisi eksik")
    witness_version = payload[0]
    if witness_version > 16:
        raise ValueError("Geçersiz witness sürümü")
    program = bytes(_convert_bits(payload[1:], 5, 8, False))
    if not 2 <= len(program) <= 40:
        raise ValueError("Geçersiz witness programı")
    if witness_version == 0:
        if encoding != "bech32" or len(program) not in (20, 32):
            raise ValueError("Geçersiz SegWit v0 adresi")
    elif encoding != "bech32m":
        raise ValueError("SegWit v1+ adresi Bech32m olmalıdır")
    return text


def _validate_evm_checksum(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", text):
        return False
    body = text[2:]
    if body.islower() or body.isupper():
        return True
    digest = _keccak256(body.lower().encode("ascii")).hex()
    for index, char in enumerate(body):
        if char.isalpha():
            should_upper = int(digest[index], 16) >= 8
            if char.isupper() != should_upper:
                return False
    return True


_CRYPTONOTE_DECODED_BLOCK_SIZES = {2: 1, 3: 2, 5: 3, 6: 4, 7: 5, 9: 6, 10: 7, 11: 8}


def _cryptonote_base58_decode(value):
    text = str(value or "").strip()
    if not text or any(char not in _B58_INDEX for char in text):
        raise ValueError("Geçersiz Monero Base58 adresi")
    output = bytearray()
    for offset in range(0, len(text), 11):
        block = text[offset:offset + 11]
        decoded_size = _CRYPTONOTE_DECODED_BLOCK_SIZES.get(len(block))
        if decoded_size is None:
            raise ValueError("Geçersiz Monero adres uzunluğu")
        number = 0
        for char in block:
            number = number * 58 + _B58_INDEX[char]
        if number >= 1 << (8 * decoded_size):
            raise ValueError("Geçersiz Monero adres bloğu")
        output.extend(number.to_bytes(decoded_size, "big"))
    return bytes(output)


def _crc16_xmodem(data):
    crc = 0
    for byte in bytes(data):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def validate_wallet_address(asset, value):
    """Validate and normalize a withdrawal/saved wallet address.

    This proves that the text has a valid address format and checksum where the
    network provides one. It does not prove ownership of the destination.
    """
    asset = str(asset or "").upper()
    text = re.sub(r"\s+", "", str(value or "").strip())
    if asset == "TL":
        return True, text, ""
    if not text or len(text) > 160:
        return False, "", "Geçerli bir cüzdan adresi giriniz."

    try:
        if asset == "BTC":
            if text.lower().startswith("bc1"):
                return True, _decode_segwit_address(text, "bc"), ""
            payload = _b58check_decode(text)
            if len(payload) != 21 or payload[0] not in (0x00, 0x05):
                raise ValueError("Bitcoin ağıyla uyumsuz adres")
            return True, text, ""

        if asset == "LTC":
            if text.lower().startswith("ltc1"):
                return True, _decode_segwit_address(text, "ltc"), ""
            payload = _b58check_decode(text)
            if len(payload) != 21 or payload[0] not in (0x30, 0x05, 0x32):
                raise ValueError("Litecoin ağıyla uyumsuz adres")
            return True, text, ""

        if asset == "ETH":
            normalized = text if text.startswith("0x") else "0x" + text
            if not _validate_evm_checksum(normalized):
                raise ValueError("Ethereum adresi veya checksum geçersiz")
            return True, normalized, ""

        if asset in ("TRX", "USDT"):
            payload = _b58check_decode(text)
            if len(payload) != 21 or payload[0] != 0x41 or not text.startswith("T"):
                raise ValueError("TRON / TRC20 adresi geçersiz")
            return True, text, ""

        if asset == "XMR":
            raw = _cryptonote_base58_decode(text)
            if len(raw) not in (69, 77):
                raise ValueError("Monero adres uzunluğu geçersiz")
            payload, checksum = raw[:-4], raw[-4:]
            if not secrets.compare_digest(checksum, _keccak256(payload)[:4]):
                raise ValueError("Monero adres checksum geçersiz")
            if payload[0] not in (18, 19, 42):
                raise ValueError("Monero mainnet adresi bekleniyor")
            return True, text, ""

        if asset == "TON":
            if re.fullmatch(r"(?:-1|0):[0-9a-fA-F]{64}", text):
                workchain, account = text.split(":", 1)
                return True, f"{workchain}:{account.lower()}", ""
            padded = text + "=" * ((4 - len(text) % 4) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(raw) != 36:
                raise ValueError("TON friendly adres uzunluğu geçersiz")
            expected_crc = _crc16_xmodem(raw[:-2]).to_bytes(2, "big")
            if not secrets.compare_digest(raw[-2:], expected_crc):
                raise ValueError("TON adres checksum geçersiz")
            if (raw[0] & 0x7F) not in (0x11, 0x51) or raw[1] not in (0x00, 0xFF):
                raise ValueError("TON adres başlığı geçersiz")
            return True, text, ""

        raise ValueError("Bu varlık için adres doğrulama desteklenmiyor")
    except (ValueError, binascii.Error, UnicodeEncodeError):
        network = {
            "BTC": "Bitcoin", "LTC": "Litecoin", "ETH": "Ethereum",
            "TRX": "TRON", "USDT": "TRC20", "XMR": "Monero", "TON": "TON",
        }.get(asset, asset)
        return False, "", f"Geçersiz {network} cüzdan adresi. Adresi ve ağı kontrol ediniz."


def wallet_address_prompt(uid, asset):
    network = {
        "BTC": "Bitcoin (BTC)",
        "LTC": "Litecoin (LTC)",
        "ETH": "Ethereum (ERC20)",
        "TRX": "TRON",
        "USDT": "TRON / TRC20",
        "XMR": "Monero",
        "TON": "TON",
    }.get(str(asset or "").upper(), str(asset or "").upper())
    return (
        f"{network} ağındaki alıcı cüzdan adresini giriniz.\n\n"
        "Adres formatı ve ağ uyumu otomatik kontrol edilecektir."
        if lang_of(uid) == "tr"
        else f"Enter the recipient wallet address on {network}.\n\n"
             "The address format and network will be checked automatically."
    )


def _asset_chain(asset):
    return CHAIN_BY_ASSET.get(str(asset or "").upper(), str(asset or "").upper())


def exchange_auto_deposit_ready(asset):
    asset = str(asset or "").upper()
    if asset in ("BTC", "LTC"):
        return bool(BLOCKCYPHER_KEY and _xpub_for_chain(asset))
    if asset == "ETH":
        return bool(ETH_RPC_URL and ETH_XPUB)
    if asset == "TRX":
        return bool(TRONGRID_KEY and TRON_XPUB)
    if asset == "USDT":
        return bool(TRONGRID_KEY and TRON_XPUB and USDT_TRC20_CONTRACT)
    if asset == "XMR":
        return bool(XMR_WALLET_RPC_URL)
    return False


def _xpub_for_chain(chain):
    return {"BTC": BTC_XPUB, "LTC": LTC_XPUB, "ETH": ETH_XPUB, "TRON": TRON_XPUB}.get(chain, "")


def _xpub_branch_for_chain(chain):
    return int(os.getenv(f"{chain}_XPUB_BRANCH", os.getenv("XPUB_DERIVATION_BRANCH", "0")))


def _derive_chain_address(chain, index):
    xpub = _xpub_for_chain(chain)
    if not xpub:
        raise RuntimeError(f"{chain}_XPUB tanımlı değil")
    branch = _xpub_branch_for_chain(chain)
    if chain == "BTC":
        return _derive_utxo_address(xpub, index, "BTC", branch), f"m/{branch}/{index}"
    if chain == "LTC":
        return _derive_utxo_address(xpub, index, "LTC", branch), f"m/{branch}/{index}"
    if chain == "ETH":
        return _derive_evm_address(xpub, index, branch), f"m/{branch}/{index}"
    if chain == "TRON":
        return _derive_tron_address(xpub, index, branch), f"m/{branch}/{index}"
    raise RuntimeError(f"Desteklenmeyen HD chain: {chain}")


def _xmr_rpc(method, params=None):
    if not XMR_WALLET_RPC_URL:
        raise RuntimeError("XMR_WALLET_RPC_URL tanımlı değil")
    auth = None
    if XMR_WALLET_RPC_USERNAME or XMR_WALLET_RPC_PASSWORD:
        auth = (XMR_WALLET_RPC_USERNAME, XMR_WALLET_RPC_PASSWORD)
    response = requests.post(
        XMR_WALLET_RPC_URL,
        json={"jsonrpc": "2.0", "id": "nerlo", "method": method, "params": params or {}},
        auth=auth,
        headers={"Content-Type": "application/json", "User-Agent": "Nerlo-Exchange/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Monero wallet RPC error: {payload['error']}")
    return payload.get("result") or {}


def exchange_get_address(user_id, chain):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT address, derivation_index, derivation_path, memo, source FROM exchange_addresses WHERE user_id=%s AND chain=%s AND status='active'",
                (str(user_id), str(chain)),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {"address": row[0], "index": row[1], "path": row[2], "memo": row[3], "source": row[4], "chain": chain}


def exchange_get_or_create_address(user_id, asset):
    """Return a user-specific watch-only deposit address.

    BTC/LTC/ETH/TRON addresses are derived from account-level XPUB values. The
    web process never receives the corresponding private keys. TRX and TRC20
    USDT intentionally share one TRON address for the same user.
    """
    user_id = str(user_id)
    asset = str(asset or "").upper()
    chain = _asset_chain(asset)
    existing = exchange_get_address(user_id, chain)
    if existing:
        existing["asset"] = asset
        return existing

    if chain == "XMR" and XMR_WALLET_RPC_URL:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"address:XMR:{user_id}",))
                cur.execute(
                    "SELECT address,derivation_index,derivation_path,memo,source FROM exchange_addresses WHERE user_id=%s AND chain='XMR' AND status='active' FOR UPDATE",
                    (user_id,),
                )
                row = cur.fetchone()
                if row:
                    conn.commit()
                    return {"address": row[0], "index": row[1], "path": row[2], "memo": row[3], "source": row[4], "chain": chain, "asset": asset}
                result = _xmr_rpc("create_address", {"account_index": XMR_ACCOUNT_INDEX, "label": f"nerlo:{user_id}"})
                address = str(result.get("address") or "").strip()
                index = int(result.get("address_index", -1))
                if not address or index < 0:
                    raise RuntimeError("Monero wallet RPC geçerli subaddress döndürmedi")
                path = f"account/{XMR_ACCOUNT_INDEX}/subaddress/{index}"
                cur.execute(
                    "INSERT INTO exchange_addresses(user_id,chain,address,derivation_index,derivation_path,source) VALUES (%s,'XMR',%s,%s,%s,'wallet_rpc')",
                    (user_id, address, index, path),
                )
            conn.commit()
        return {"address": address, "index": index, "path": path, "memo": "", "source": "wallet_rpc", "chain": chain, "asset": asset}

    xpub = _xpub_for_chain(chain)
    if not xpub:
        legacy = ""
        if asset == "USDT":
            legacy = str(settings.get("wallet_USDT") or settings.get("wallet_TRX") or "").strip()
        else:
            legacy = str(settings.get(f"wallet_{asset}", "")).strip()
        if legacy and (asset not in AUTO_DEPOSIT_ASSETS or asset in ("XMR", "TON") or ALLOW_SHARED_DEPOSIT_ADDRESSES):
            return {"address": legacy, "index": None, "path": "", "memo": "", "source": "shared", "chain": chain, "asset": asset}
        raise RuntimeError(f"{chain} için kullanıcıya özel adres üretimi hazır değil; {chain}_XPUB eklenmelidir")

    with _db_connect() as conn:
        with conn.cursor() as cur:
            # A missing row cannot be protected with FOR UPDATE. The advisory lock
            # serializes first-address creation for the same user and chain.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"address:{chain}:{user_id}",))
            cur.execute(
                "SELECT address, derivation_index, derivation_path, memo, source FROM exchange_addresses WHERE user_id=%s AND chain=%s AND status='active' FOR UPDATE",
                (user_id, chain),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return {"address": row[0], "index": row[1], "path": row[2], "memo": row[3], "source": row[4], "chain": chain, "asset": asset}

            meta_key = f"address-index:{chain}"
            cur.execute(
                "INSERT INTO exchange_meta(meta_key, meta_value) VALUES (%s, %s) ON CONFLICT(meta_key) DO NOTHING",
                (meta_key, Jsonb({"next_index": 0})),
            )
            cur.execute("SELECT meta_value FROM exchange_meta WHERE meta_key=%s FOR UPDATE", (meta_key,))
            meta = cur.fetchone()[0] or {}
            index = int(meta.get("next_index", 0))
            address, path = _derive_chain_address(chain, index)
            cur.execute(
                """
                INSERT INTO exchange_addresses(user_id, chain, address, derivation_index, derivation_path, source)
                VALUES (%s, %s, %s, %s, %s, 'xpub')
                RETURNING address
                """,
                (user_id, chain, address, index, path),
            )
            cur.execute(
                "UPDATE exchange_meta SET meta_value=%s, updated_at=NOW() WHERE meta_key=%s",
                (Jsonb({"next_index": index + 1}), meta_key),
            )
        conn.commit()
    return {"address": address, "index": index, "path": path, "memo": "", "source": "xpub", "chain": chain, "asset": asset}


def exchange_list_addresses(chain=None, limit=None):
    params = []
    sql = "SELECT user_id, chain, address, derivation_index, derivation_path, memo, source FROM exchange_addresses WHERE status='active'"
    if chain:
        sql += " AND chain=%s"
        params.append(chain)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return [
        {"user_id": r[0], "chain": r[1], "address": r[2], "index": r[3], "path": r[4], "memo": r[5], "source": r[6]}
        for r in rows
    ]


def exchange_load_profile(user_id, conn=None, lock=False):
    owns_conn = conn is None
    conn = conn or _db_connect()
    try:
        with conn.cursor() as cur:
            suffix = " FOR UPDATE" if lock else ""
            cur.execute(f"SELECT profile FROM exchange_profiles WHERE user_id=%s{suffix}", (str(user_id),))
            row = cur.fetchone()
        return dict(row[0] or {}) if row else None
    finally:
        if owns_conn:
            conn.close()


def exchange_save_profile(user_id, profile, conn=None):
    owns_conn = conn is None
    conn = conn or _db_connect()
    try:
        clean_profile = dict(profile or {})
        clean_profile["chat_id"] = str(user_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_profiles(user_id,profile) VALUES (%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET profile=EXCLUDED.profile,updated_at=NOW()
                """,
                (str(user_id), Jsonb(clean_profile)),
            )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def exchange_load_all_profiles():
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id,profile FROM exchange_profiles ORDER BY user_id")
            rows = cur.fetchall()
    return {str(uid): dict(profile or {}) for uid, profile in rows}


def save_user_profile(user_id):
    user_id = str(user_id)
    profile = users.get(user_id) if "users" in globals() else None
    if profile is not None:
        exchange_save_profile(user_id, profile)


def refresh_all_user_profiles():
    if "users" not in globals():
        return
    profiles = exchange_load_all_profiles()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id,asset,available,pending FROM exchange_accounts")
            account_rows = cur.fetchall()
    users.clear()
    users.update(profiles)
    for uid, asset, available, pending in account_rows:
        uid = str(uid)
        if uid not in users:
            continue
        users[uid].setdefault("balances", {})[asset] = str(D(available))
        users[uid].setdefault("pending_balances", {})[asset] = str(D(pending))


def refresh_request_cache():
    if "requests_db" not in globals():
        return
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT request_id,payload,status,automatic FROM exchange_requests ORDER BY created_at")
            rows = cur.fetchall()
    fresh = {}
    for rid, payload, status, automatic in rows:
        item = dict(payload or {})
        item["id"] = str(rid)
        item["status"] = status
        item["automatic"] = bool(automatic)
        fresh[str(rid)] = item
    requests_db.clear()
    requests_db.update(fresh)


def exchange_account(user_id, asset, conn=None, lock=False):
    owns_conn = conn is None
    conn = conn or _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO exchange_accounts(user_id, asset) VALUES (%s,%s) ON CONFLICT(user_id,asset) DO NOTHING",
                (str(user_id), str(asset)),
            )
            suffix = " FOR UPDATE" if lock else ""
            cur.execute(
                f"SELECT available, pending, locked FROM exchange_accounts WHERE user_id=%s AND asset=%s{suffix}",
                (str(user_id), str(asset)),
            )
            row = cur.fetchone()
        if owns_conn:
            conn.commit()
        return {"available": D(row[0]), "pending": D(row[1]), "locked": D(row[2])}
    finally:
        if owns_conn:
            conn.close()


def exchange_balance(user_id, asset, bucket="available"):
    try:
        account = exchange_account(user_id, asset)
        return D(account.get(bucket, "0"))
    except Exception as exc:
        print("EXCHANGE BALANCE READ ERROR:", exc)
        snapshot = users.get(str(user_id), {}) if "users" in globals() else {}
        key = "pending_balances" if bucket == "pending" else "balances"
        return D(snapshot.get(key, {}).get(asset, "0"))


def exchange_refresh_user_cache(user_id):
    user_id = str(user_id)
    if "users" not in globals() or user_id not in users:
        return
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset,available,pending,locked FROM exchange_accounts WHERE user_id=%s", (user_id,))
            rows = cur.fetchall()
    users[user_id].setdefault("balances", {})
    users[user_id].setdefault("pending_balances", {})
    for asset, available, pending, locked in rows:
        users[user_id]["balances"][asset] = str(D(available))
        users[user_id]["pending_balances"][asset] = str(D(pending))


def _exchange_sync_user_cache(changed_accounts):
    if "users" not in globals() or not changed_accounts:
        return
    touched = False
    for (uid, asset), values in changed_accounts.items():
        uid = str(uid)
        if uid not in users:
            continue
        users[uid].setdefault("balances", {})[asset] = str(values["available"])
        users[uid].setdefault("pending_balances", {})[asset] = str(values["pending"])
        touched = True
    # The dedicated exchange_accounts table is authoritative. Profile rows are
    # intentionally not rewritten here, preventing a stale worker from
    # overwriting a concurrent PIN, status or favorites change.
    return


def exchange_apply_ledger(entries, mirror_legacy=True):
    """Apply one or more idempotent ledger entries in a single SQL transaction."""
    normalized = []
    for item in entries:
        amount = D(item.get("amount"))
        if amount == 0:
            continue
        bucket = str(item.get("bucket", "available"))
        if bucket not in ("available", "pending", "locked"):
            raise ValueError("Geçersiz ledger bucket")
        idem = str(item.get("idempotency_key") or uuid.uuid4().hex)
        normalized.append({
            "user_id": str(item["user_id"]),
            "asset": str(item["asset"]),
            "bucket": bucket,
            "amount": amount,
            "entry_type": str(item.get("entry_type") or "system"),
            "reference_type": str(item.get("reference_type") or ""),
            "reference_id": str(item.get("reference_id") or ""),
            "idempotency_key": idem,
            "metadata": item.get("metadata") or {},
        })
    if not normalized:
        return []

    account_keys = sorted({(item["user_id"], item["asset"]) for item in normalized})
    applied = []
    states = {}
    with _db_connect() as conn:
        with conn.cursor() as cur:
            for uid, asset in account_keys:
                cur.execute(
                    "INSERT INTO exchange_accounts(user_id, asset) VALUES (%s,%s) ON CONFLICT(user_id,asset) DO NOTHING",
                    (uid, asset),
                )
            for uid, asset in account_keys:
                cur.execute(
                    "SELECT available,pending,locked FROM exchange_accounts WHERE user_id=%s AND asset=%s FOR UPDATE",
                    (uid, asset),
                )
                row = cur.fetchone()
                states[(uid, asset)] = {"available": D(row[0]), "pending": D(row[1]), "locked": D(row[2])}

            for item in normalized:
                cur.execute("SELECT id,balance_after FROM exchange_ledger WHERE idempotency_key=%s", (item["idempotency_key"],))
                existing = cur.fetchone()
                if existing:
                    continue
                key = (item["user_id"], item["asset"])
                state = states[key]
                new_value = state[item["bucket"]] + item["amount"]
                if new_value < 0:
                    raise ValueError(f"Yetersiz {item['bucket']} bakiye")
                state[item["bucket"]] = new_value
                cur.execute(
                    """
                    INSERT INTO exchange_ledger(
                        user_id,asset,bucket,amount,entry_type,reference_type,reference_id,
                        idempotency_key,balance_after,metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        item["user_id"], item["asset"], item["bucket"], item["amount"],
                        item["entry_type"], item["reference_type"], item["reference_id"],
                        item["idempotency_key"], new_value, Jsonb(item["metadata"]),
                    ),
                )
                item["ledger_id"] = cur.fetchone()[0]
                item["balance_after"] = new_value
                applied.append(item)

            for (uid, asset), state in states.items():
                cur.execute(
                    """
                    UPDATE exchange_accounts
                    SET available=%s,pending=%s,locked=%s,updated_at=NOW()
                    WHERE user_id=%s AND asset=%s
                    """,
                    (state["available"], state["pending"], state["locked"], uid, asset),
                )
        conn.commit()

    _exchange_sync_user_cache(states)
    if mirror_legacy and applied and "add_transaction" in globals():
        for item in applied:
            try:
                add_transaction(
                    item["user_id"], item["asset"], item["amount"], item["entry_type"],
                    item["reference_id"], str(item["metadata"].get("note", "")), item["bucket"],
                )
            except Exception as exc:
                print("LEGACY TRANSACTION MIRROR ERROR:", exc)
    return applied


def exchange_post_ledger(user_id, asset, amount, bucket, entry_type, reference_id="", note="", idempotency_key=""):
    return exchange_apply_ledger([{
        "user_id": str(user_id),
        "asset": asset,
        "amount": amount,
        "bucket": bucket,
        "entry_type": entry_type,
        "reference_type": "request" if reference_id else "",
        "reference_id": str(reference_id),
        "idempotency_key": idempotency_key or f"manual:{uuid.uuid4().hex}",
        "metadata": {"note": note} if note else {},
    }])


def exchange_upsert_request(record, conn=None):
    record = dict(record)
    rid = str(record.get("id") or record.get("request_id") or "")
    if not rid:
        raise ValueError("request_id eksik")
    uid = str(record.get("user_id") or "")
    request_type = str(record.get("type") or record.get("request_type") or "")
    status = str(record.get("status") or "pending")
    idem = str(record.get("idempotency_key") or "") or None
    automatic = bool(record.get("automatic", False))
    owns_conn = conn is None
    conn = conn or _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_requests(request_id,user_id,request_type,status,idempotency_key,automatic,payload,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(NULLIF(%s,'')::timestamptz,NOW()),NOW())
                ON CONFLICT(request_id) DO UPDATE SET
                    user_id=EXCLUDED.user_id,
                    request_type=EXCLUDED.request_type,
                    status=EXCLUDED.status,
                    automatic=EXCLUDED.automatic,
                    payload=EXCLUDED.payload,
                    updated_at=NOW()
                """,
                (rid, uid, request_type, status, idem, automatic, Jsonb(record), record.get("created_at")),
            )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def exchange_update_request(rid, changes, persist_legacy=True):
    rid = str(rid)
    changes = dict(changes or {})
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM exchange_requests WHERE request_id=%s FOR UPDATE", (rid,))
            row = cur.fetchone()
            if not row:
                return False
            payload = dict(row[0] or {})
            payload.update(changes)
            status = str(payload.get("status") or "pending")
            completed = status == "completed"
            cur.execute(
                """
                UPDATE exchange_requests SET status=%s,payload=%s,updated_at=NOW(),
                    completed_at=CASE WHEN %s THEN COALESCE(completed_at,NOW()) ELSE completed_at END
                WHERE request_id=%s
                """,
                (status, Jsonb(payload), completed, rid),
            )
        conn.commit()
    if persist_legacy and "requests_db" in globals():
        requests_db[rid] = payload
        try:
            save_json(FILES["requests"], requests_db)
        except Exception as exc:
            print("REQUEST CACHE SYNC ERROR:", exc)
    return True


def exchange_bootstrap_state():
    """One-time import from the legacy JSONB snapshot, then DB -> cache sync."""
    if not EXCHANGE_MODE:
        return
    with _db_connect() as conn:
        with conn.cursor() as cur:
            for uid, user in users.items():
                cur.execute(
                    "INSERT INTO exchange_profiles(user_id,profile) VALUES (%s,%s) ON CONFLICT(user_id) DO NOTHING",
                    (str(uid), Jsonb(user)),
                )
                for asset in ASSETS:
                    available = D(user.get("balances", {}).get(asset, "0"))
                    pending = D(user.get("pending_balances", {}).get(asset, "0"))
                    cur.execute("SELECT 1 FROM exchange_accounts WHERE user_id=%s AND asset=%s", (str(uid), asset))
                    if cur.fetchone():
                        continue
                    cur.execute(
                        "INSERT INTO exchange_accounts(user_id,asset,available,pending,locked) VALUES (%s,%s,%s,%s,0)",
                        (str(uid), asset, available, pending),
                    )
                    if available != 0:
                        cur.execute(
                            """
                            INSERT INTO exchange_ledger(user_id,asset,bucket,amount,entry_type,reference_type,reference_id,idempotency_key,balance_after,metadata)
                            VALUES (%s,%s,'available',%s,'legacy_opening_balance','migration','legacy',%s,%s,%s)
                            ON CONFLICT(idempotency_key) DO NOTHING
                            """,
                            (str(uid), asset, available, f"legacy:{uid}:{asset}:available", available, Jsonb({"source": "app_state"})),
                        )
                    if pending != 0:
                        cur.execute(
                            """
                            INSERT INTO exchange_ledger(user_id,asset,bucket,amount,entry_type,reference_type,reference_id,idempotency_key,balance_after,metadata)
                            VALUES (%s,%s,'pending',%s,'legacy_opening_pending','migration','legacy',%s,%s,%s)
                            ON CONFLICT(idempotency_key) DO NOTHING
                            """,
                            (str(uid), asset, pending, f"legacy:{uid}:{asset}:pending", pending, Jsonb({"source": "app_state"})),
                        )

            for rid, record in requests_db.items():
                record = dict(record)
                record.setdefault("id", str(rid))
                idem = str(record.get("idempotency_key") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO exchange_requests(
                        request_id,user_id,request_type,status,idempotency_key,automatic,payload,created_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(NULLIF(%s,'')::timestamptz,NOW()),NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        str(rid), str(record.get("user_id") or ""),
                        str(record.get("type") or record.get("request_type") or ""),
                        str(record.get("status") or "pending"), idem, bool(record.get("automatic", False)),
                        Jsonb(record), record.get("created_at"),
                    ),
                )
        conn.commit()

    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id,asset,available,pending,locked FROM exchange_accounts")
            account_rows = cur.fetchall()
            cur.execute("SELECT request_id,payload,status,automatic FROM exchange_requests")
            request_rows = cur.fetchall()
            cur.execute("SELECT user_id,profile FROM exchange_profiles")
            profile_rows = cur.fetchall()
    users.clear()
    users.update({str(uid): dict(profile or {}) for uid, profile in profile_rows})
    cache = {}
    for uid, asset, available, pending, locked in account_rows:
        cache[(str(uid), asset)] = {"available": D(available), "pending": D(pending), "locked": D(locked)}
    _exchange_sync_user_cache(cache)
    for rid, payload, status, automatic in request_rows:
        item = dict(payload or {})
        item["id"] = str(rid)
        item["status"] = status
        item["automatic"] = bool(automatic)
        requests_db[str(rid)] = item
    save_json(FILES["requests"], requests_db)


def exchange_enqueue(job_type, payload, dedupe_key=None, queue_name="default", delay_seconds=0, max_attempts=None):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_jobs(queue_name,job_type,payload,status,max_attempts,available_at,dedupe_key)
                VALUES (%s,%s,%s,'queued',%s,NOW()+(%s * INTERVAL '1 second'),%s)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    status='queued',attempts=0,available_at=EXCLUDED.available_at,last_error='',updated_at=NOW()
                WHERE exchange_jobs.status='dead'
                RETURNING id
                """,
                (
                    queue_name, job_type, Jsonb(payload or {}), int(max_attempts or EXCHANGE_MAX_JOB_ATTEMPTS),
                    int(delay_seconds), dedupe_key,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def exchange_claim_job():
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE exchange_jobs SET status='queued',locked_at=NULL,locked_by='',updated_at=NOW()
                WHERE status='running' AND locked_at < NOW()-(%s * INTERVAL '1 second')
                """,
                (EXCHANGE_JOB_LOCK_SECONDS,),
            )
            cur.execute(
                """
                WITH candidate AS (
                    SELECT id FROM exchange_jobs
                    WHERE status='queued' AND available_at<=NOW()
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE exchange_jobs j
                SET status='running',attempts=j.attempts+1,locked_at=NOW(),locked_by=%s,updated_at=NOW()
                FROM candidate
                WHERE j.id=candidate.id
                RETURNING j.id,j.job_type,j.payload,j.attempts,j.max_attempts
                """,
                (EXCHANGE_WORKER_ID,),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {"id": row[0], "job_type": row[1], "payload": row[2] or {}, "attempts": row[3], "max_attempts": row[4]}


def exchange_complete_job(job_id):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE exchange_jobs SET status='completed',locked_at=NULL,updated_at=NOW() WHERE id=%s", (job_id,))
        conn.commit()


def exchange_fail_job(job, exc):
    error = ("".join(traceback.format_exception_only(type(exc), exc))).strip()[:2000]
    attempts = int(job.get("attempts", 1))
    max_attempts = int(job.get("max_attempts", EXCHANGE_MAX_JOB_ATTEMPTS))
    dead = attempts >= max_attempts
    delay = min(3600, 2 ** min(attempts, 10))
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE exchange_jobs SET status=%s,last_error=%s,locked_at=NULL,locked_by='',
                    available_at=CASE WHEN %s THEN available_at ELSE NOW()+(%s * INTERVAL '1 second') END,
                    updated_at=NOW()
                WHERE id=%s
                """,
                ("dead" if dead else "queued", error, dead, delay, job["id"]),
            )
        conn.commit()
    if dead and job.get("job_type") == "broadcast_withdrawal":
        rid = str((job.get("payload") or {}).get("request_id") or "")
        if rid:
            try:
                exchange_update_request(rid, {"broadcast_locked": False, "signer_status": "failed", "signer_error": error})
            except Exception as sync_exc:
                print("SIGNER FAILURE STATE ERROR:", sync_exc)
            if ADMIN_CHAT_ID:
                try:
                    send(ADMIN_CHAT_ID, f"KRİTİK ÇEKİM SIGNER HATASI\nTalep: #{rid}\nBakiye beklemede tutuldu; otomatik iade yapılmadı.\nHata: {error}")
                except Exception:
                    pass
    print("EXCHANGE JOB ERROR:", job.get("job_type"), error)


def exchange_record_chain_event(chain, asset, txid, event_index, address, user_id, amount, block_height=0, confirmations=0, raw=None, removed=False):
    amount = D(amount)
    if amount <= 0:
        return None
    status = "reorged" if removed else "detected"
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_chain_events(chain,asset,txid,event_index,address,user_id,amount,block_height,confirmations,status,raw)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(chain,asset,txid,event_index,address) DO UPDATE SET
                    confirmations=GREATEST(exchange_chain_events.confirmations,EXCLUDED.confirmations),
                    block_height=CASE WHEN EXCLUDED.block_height>0 THEN EXCLUDED.block_height ELSE exchange_chain_events.block_height END,
                    generation=CASE
                        WHEN EXCLUDED.status<>'reorged' AND exchange_chain_events.status IN ('reorged','reversed','reorg_debt','reorg_fee_debt')
                        THEN exchange_chain_events.generation+1 ELSE exchange_chain_events.generation END,
                    request_id=CASE
                        WHEN EXCLUDED.status<>'reorged' AND exchange_chain_events.status IN ('reorged','reversed','reorg_debt','reorg_fee_debt')
                        THEN '' ELSE exchange_chain_events.request_id END,
                    status=CASE
                        WHEN EXCLUDED.status='reorged' THEN 'reorged'
                        WHEN exchange_chain_events.status IN ('reorged','reversed','reorg_debt','reorg_fee_debt') THEN 'detected'
                        ELSE exchange_chain_events.status END,
                    raw=EXCLUDED.raw,updated_at=NOW()
                RETURNING id,status,generation
                """,
                (
                    chain, asset, str(txid), str(event_index), str(address), str(user_id), amount,
                    int(block_height or 0), int(confirmations or 0), status, Jsonb(raw or {}),
                ),
            )
            event_id, event_status, event_generation = cur.fetchone()
        conn.commit()
    if event_status not in ("credited", "reversed"):
        exchange_enqueue(
            "process_chain_event", {"event_id": event_id},
            f"chain-event:{event_id}:g{event_generation}", "blockchain",
        )
    return event_id


def _chain_confirmation_threshold(asset):
    return int(CONFIRMATION_THRESHOLDS.get(asset, 1))


def _eth_rpc(method, params=None):
    if not ETH_RPC_URL:
        raise RuntimeError("ETH_RPC_URL veya ALCHEMY_KEY tanımlı değil")
    response = requests.post(
        ETH_RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
        headers={"Content-Type": "application/json", "User-Agent": "Nerlo-Exchange/1.0"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Ethereum RPC error: {payload['error']}")
    return payload.get("result")


def _eth_latest_block():
    return int(_eth_rpc("eth_blockNumber"), 16)


def _event_current_confirmations(event):
    chain = event["chain"]
    if chain == "ETH":
        if int(event.get("block_height") or 0) <= 0:
            return int(event.get("confirmations") or 0)
        return max(0, _eth_latest_block() - int(event["block_height"]) + 1)
    return int(event.get("confirmations") or 0)


def _automatic_request_record(event, net, fee, status):
    generation = int(event.get("generation") or 0)
    default_rid = f"AUTO-{event['id']}" if generation == 0 else f"AUTO-{event['id']}-G{generation}"
    rid = event.get("request_id") or default_rid
    created = now()
    return rid, {
        "id": rid,
        "user_id": str(event["user_id"]),
        "type": "deposit",
        "status": status,
        "asset": event["asset"],
        "amount": str(event["amount"]),
        "fee": str(fee),
        "net_amount": str(net),
        "network": event["chain"],
        "txid": event["txid"],
        "event_index": event["event_index"],
        "automatic": True,
        "idempotency_key": f"chain-event:{event['id']}:g{generation}",
        "created_at": created,
        "updated_at": created,
    }


def _process_chain_event(job):
    event_id = int(job["payload"]["event_id"])
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,chain,asset,txid,event_index,address,user_id,amount,block_height,confirmations,status,request_id,raw,generation
                FROM exchange_chain_events WHERE id=%s
                """,
                (event_id,),
            )
            row = cur.fetchone()
    if not row:
        return
    event = {
        "id": row[0], "chain": row[1], "asset": row[2], "txid": row[3], "event_index": row[4],
        "address": row[5], "user_id": row[6], "amount": D(row[7]), "block_height": row[8],
        "confirmations": row[9], "status": row[10], "request_id": row[11], "raw": row[12] or {},
        "generation": int(row[13] or 0),
    }
    if event["status"] in ("credited", "reversed"):
        return

    base_key = f"deposit-event:{event_id}:g{event['generation']}"
    if event["status"] == "reorged":
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT amount FROM exchange_ledger WHERE idempotency_key=%s", (base_key + ":available-in",))
                credited_row = cur.fetchone()
                cur.execute("SELECT amount FROM exchange_ledger WHERE idempotency_key=%s", (base_key + ":pending-in",))
                pending_row = cur.fetchone()
                cur.execute("SELECT amount FROM exchange_ledger WHERE idempotency_key=%s", (base_key + ":fee",))
                fee_row = cur.fetchone()
        credited = credited_row is not None
        pending_seen = pending_row is not None
        fallback_fee_rate = fee_percent("deposit", event["asset"], event["user_id"]) if "fee_percent" in globals() else Decimal("0")
        fallback_fee = fee_amount(event["amount"], fallback_fee_rate) if "fee_amount" in globals() else Decimal("0")
        fee = abs(D(fee_row[0])) if fee_row else fallback_fee
        net = abs(D(credited_row[0])) if credited_row else (abs(D(pending_row[0])) if pending_row else event["amount"] - fee)
        user_reversal = None
        platform_reversal = None
        if credited:
            user_reversal = {
                "user_id": event["user_id"], "asset": event["asset"], "bucket": "available", "amount": -net,
                "entry_type": "deposit_reorg_reversal", "reference_type": "chain_event", "reference_id": str(event_id),
                "idempotency_key": base_key + ":reorg-available", "metadata": {"txid": event["txid"]},
            }
            if fee > 0:
                platform_reversal = {
                    "user_id": "__platform__", "asset": event["asset"], "bucket": "available", "amount": -fee,
                    "entry_type": "deposit_fee_reorg_reversal", "reference_type": "chain_event", "reference_id": str(event_id),
                    "idempotency_key": base_key + ":reorg-fee", "metadata": {"txid": event["txid"], "user_id": event["user_id"]},
                }
        elif pending_seen:
            user_reversal = {
                "user_id": event["user_id"], "asset": event["asset"], "bucket": "pending", "amount": -net,
                "entry_type": "deposit_reorg_pending_reversal", "reference_type": "chain_event", "reference_id": str(event_id),
                "idempotency_key": base_key + ":reorg-pending", "metadata": {"txid": event["txid"]},
            }
        reversal_status = "reversed"
        if user_reversal:
            try:
                exchange_apply_ledger([user_reversal])
            except ValueError as exc:
                # The credited funds were already spent. Freeze the account rather
                # than hiding the exposure or allowing further withdrawals.
                reversal_status = "reorg_debt"
                if event["user_id"] in users:
                    users[event["user_id"]]["status"] = "frozen"
                    users[event["user_id"]]["withdraw_locked"] = True
                    save_user_profile(event["user_id"])
                    add_security_event(event["user_id"], "blockchain_reorg_debt", str(exc))
                if ADMIN_CHAT_ID:
                    send(ADMIN_CHAT_ID, f"KRİTİK REORG BORCU\nKullanıcı: {event['user_id']}\nVarlık: {event['asset']}\nTXID: {event['txid']}\nTutar: {net}")
        if reversal_status == "reversed" and platform_reversal:
            try:
                exchange_apply_ledger([platform_reversal])
            except ValueError as exc:
                reversal_status = "reorg_fee_debt"
                if ADMIN_CHAT_ID:
                    send(ADMIN_CHAT_ID, f"KRİTİK PLATFORM REORG KOMİSYON BORCU\nVarlık: {event['asset']}\nTXID: {event['txid']}\nTutar: {fee}\nHata: {exc}")
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE exchange_chain_events SET status=%s,updated_at=NOW() WHERE id=%s", (reversal_status, event_id))
            conn.commit()
        if event.get("request_id"):
            exchange_update_request(event["request_id"], {"status": "rejected", "reorged_at": now(), "reorg_status": reversal_status})
        return

    confirmations = _event_current_confirmations(event)
    threshold = _chain_confirmation_threshold(event["asset"])
    fee_rate = fee_percent("deposit", event["asset"], event["user_id"]) if "fee_percent" in globals() else Decimal("0")
    fee = fee_amount(event["amount"], fee_rate) if "fee_amount" in globals() else Decimal("0")
    net = event["amount"] - fee
    if net <= 0:
        raise ValueError("Blockchain yatırımı komisyon sonrası sıfır veya negatif")

    minimum = min_amount("deposit", event["asset"]) if "min_amount" in globals() else Decimal("0")
    if minimum > 0 and event["amount"] < minimum:
        rid, request_record = _automatic_request_record(event, net, fee, "rejected")
        request_record.update({
            "failure_reason": "below_minimum",
            "minimum_amount": str(minimum),
            "updated_at": now(),
        })
        exchange_upsert_request(request_record)
        if "requests_db" in globals():
            requests_db[rid] = request_record
            save_json(FILES["requests"], requests_db)
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE exchange_chain_events SET status='below_minimum',request_id=%s,updated_at=NOW() WHERE id=%s",
                    (rid, event_id),
                )
            conn.commit()
        if "send" in globals() and str(event["user_id"]) in users:
            message = (
                f"{event['asset']} yatırımınız ağda görüldü ancak minimum yatırım tutarının altında olduğu için bakiyeye eklenmedi. "
                f"Minimum: {ucoin(event['user_id'], minimum, event['asset'])}"
                if lang_of(event["user_id"]) == "tr"
                else f"Your {event['asset']} deposit was detected but was below the minimum deposit amount and was not credited. "
                f"Minimum: {ucoin(event['user_id'], minimum, event['asset'])}"
            )
            send(event["user_id"], message, reply_keyboard(event["user_id"]))
        return

    rid, request_record = _automatic_request_record(event, net, fee, "pending")
    exchange_upsert_request(request_record)
    if "requests_db" in globals():
        requests_db[rid] = request_record
        save_json(FILES["requests"], requests_db)

    exchange_apply_ledger([{
        "user_id": event["user_id"], "asset": event["asset"], "bucket": "pending", "amount": net,
        "entry_type": "blockchain_deposit_pending", "reference_type": "chain_event", "reference_id": str(event_id),
        "idempotency_key": base_key + ":pending-in", "metadata": {"txid": event["txid"], "gross": str(event["amount"]), "fee": str(fee)},
    }])

    if confirmations < threshold:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE exchange_chain_events SET status='pending',confirmations=%s,request_id=%s,updated_at=NOW() WHERE id=%s",
                    (confirmations, rid, event_id),
                )
            conn.commit()
        # Recheck without creating duplicate jobs: current job will complete, new delayed key is unique.
        exchange_enqueue(
            "process_chain_event", {"event_id": event_id},
            None, "blockchain", delay_seconds=max(15, WALLET_SCAN_SECONDS),
        )
        return

    final_entries = [
        {
            "user_id": event["user_id"], "asset": event["asset"], "bucket": "pending", "amount": -net,
            "entry_type": "blockchain_deposit_pending_release", "reference_type": "chain_event", "reference_id": str(event_id),
            "idempotency_key": base_key + ":pending-out", "metadata": {"txid": event["txid"]},
        },
        {
            "user_id": event["user_id"], "asset": event["asset"], "bucket": "available", "amount": net,
            "entry_type": "blockchain_deposit_confirmed", "reference_type": "chain_event", "reference_id": str(event_id),
            "idempotency_key": base_key + ":available-in", "metadata": {"txid": event["txid"], "confirmations": confirmations},
        },
    ]
    if fee > 0:
        final_entries.append({
            "user_id": "__platform__", "asset": event["asset"], "bucket": "available", "amount": fee,
            "entry_type": "deposit_fee_revenue", "reference_type": "chain_event", "reference_id": str(event_id),
            "idempotency_key": base_key + ":fee", "metadata": {"user_id": event["user_id"], "txid": event["txid"]},
        })
    applied = exchange_apply_ledger(final_entries)
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE exchange_chain_events SET status='credited',confirmations=%s,request_id=%s,
                    confirmed_at=COALESCE(confirmed_at,NOW()),credited_at=COALESCE(credited_at,NOW()),updated_at=NOW()
                WHERE id=%s
                """,
                (confirmations, rid, event_id),
            )
        conn.commit()
    exchange_update_request(rid, {"status": "completed", "completed_at": now(), "confirmations": confirmations})
    if applied and "send" in globals() and str(event["user_id"]) in users:
        try:
            send(event["user_id"], receipt_text(rid, event["user_id"]), reply_keyboard(event["user_id"]))
        except Exception as exc:
            print("DEPOSIT NOTIFICATION ERROR:", exc)


def _configured_evm_tokens():
    try:
        raw = json.loads(EVM_TOKEN_CONTRACTS_JSON or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"EVM_TOKEN_CONTRACTS_JSON geçersiz: {exc}") from exc
    result = {}
    for asset, config in (raw or {}).items():
        if isinstance(config, str):
            config = {"address": config, "decimals": 18}
        address = _normalize_evm_address(config.get("address"))
        if address:
            result[str(asset).upper()] = {"address": address, "decimals": int(config.get("decimals", 18))}
    return result


def _cursor_get(chain):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cursor_value FROM exchange_cursors WHERE chain=%s", (chain,))
            row = cur.fetchone()
    return int(row[0]) if row else 0


def _cursor_set(chain, value, cursor_hash=""):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_cursors(chain,cursor_value,cursor_hash) VALUES (%s,%s,%s)
                ON CONFLICT(chain) DO UPDATE SET cursor_value=EXCLUDED.cursor_value,cursor_hash=EXCLUDED.cursor_hash,updated_at=NOW()
                """,
                (chain, int(value), str(cursor_hash or "")),
            )
        conn.commit()


def eth_indexer_once():
    if not ETH_RPC_URL:
        return
    addresses = exchange_list_addresses("ETH", INDEXER_ADDRESS_LIMIT)
    if not addresses:
        return
    watched = {_normalize_evm_address(item["address"]): item["user_id"] for item in addresses}
    latest = _eth_latest_block()
    cursor = _cursor_get("ETH")
    if cursor <= 0:
        cursor = max(0, latest - INDEXER_BACKFILL_BLOCKS)
        _cursor_set("ETH", cursor)
    scan_from = max(0, cursor - ETH_REORG_BACKTRACK_BLOCKS)
    stop = min(latest, cursor + INDEXER_MAX_BLOCKS_PER_PASS)
    transfer_topic = "0x" + _keccak256(b"Transfer(address,address,uint256)").hex()
    tokens = _configured_evm_tokens()
    contract_to_asset = {cfg["address"]: (asset, cfg["decimals"]) for asset, cfg in tokens.items()}
    stop_hash = ""

    for height in range(scan_from + 1, stop + 1):
        block_hex = hex(height)
        block = _eth_rpc("eth_getBlockByNumber", [block_hex, True]) or {}
        canonical_hash = str(block.get("hash") or "")
        stop_hash = canonical_hash if height == stop else stop_hash
        stale_ids = []
        if canonical_hash:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE exchange_chain_events SET status='reorged',updated_at=NOW()
                        WHERE chain='ETH' AND block_height=%s
                          AND status NOT IN ('reversed','reorged','reorg_debt')
                          AND COALESCE(raw->>'blockHash','')<>''
                          AND raw->>'blockHash'<>%s
                        RETURNING id
                        """,
                        (height, canonical_hash),
                    )
                    stale_ids = [row[0] for row in cur.fetchall()]
                conn.commit()
            for stale_id in stale_ids:
                # Reverse the orphaned generation before a transaction that moved
                # to the canonical block can be seen and credited as a new generation.
                _process_chain_event({"payload": {"event_id": stale_id}})
        confirmations = max(0, latest - height + 1)
        for tx_index, tx in enumerate(block.get("transactions") or []):
            to_address = _normalize_evm_address(tx.get("to"))
            if to_address not in watched:
                continue
            value = int(tx.get("value") or "0x0", 16)
            if value <= 0:
                continue
            exchange_record_chain_event(
                "ETH", "ETH", tx.get("hash"), f"native:{tx_index}", to_address, watched[to_address],
                Decimal(value) / Decimal(10 ** 18), height, confirmations, tx,
            )

        if contract_to_asset:
            logs = _eth_rpc("eth_getLogs", [{
                "fromBlock": block_hex,
                "toBlock": block_hex,
                "address": list(contract_to_asset.keys()),
                "topics": [transfer_topic],
            }]) or []
            for log in logs:
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                recipient = _normalize_evm_address("0x" + topics[2][-40:])
                if recipient not in watched:
                    continue
                contract = _normalize_evm_address(log.get("address"))
                token = contract_to_asset.get(contract)
                if not token:
                    continue
                asset, decimals = token
                amount = Decimal(int(log.get("data") or "0x0", 16)) / Decimal(10 ** decimals)
                exchange_record_chain_event(
                    "ETH", asset, log.get("transactionHash"), str(int(log.get("logIndex") or "0x0", 16)),
                    recipient, watched[recipient], amount, height, confirmations, log, bool(log.get("removed")),
                )
    _cursor_set("ETH", stop, stop_hash)


def _trongrid_headers():
    headers = {"Accept": "application/json", "User-Agent": "Nerlo-Exchange/1.0"}
    if TRONGRID_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_KEY
    return headers


def tron_indexer_once():
    if not TRONGRID_KEY:
        return
    addresses = exchange_list_addresses("TRON", INDEXER_ADDRESS_LIMIT)
    for item in addresses:
        address = item["address"]
        uid = item["user_id"]
        response = requests.get(
            f"{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions",
            params={"only_confirmed": "true", "only_to": "true", "limit": 50, "order_by": "block_timestamp,desc"},
            headers=_trongrid_headers(), timeout=25,
        )
        response.raise_for_status()
        for tx in response.json().get("data", []):
            if any(str(ret.get("contractRet", "")).upper() not in ("SUCCESS", "") for ret in tx.get("ret", [])):
                continue
            for contract_index, contract in enumerate(tx.get("raw_data", {}).get("contract", [])):
                if contract.get("type") != "TransferContract":
                    continue
                value = contract.get("parameter", {}).get("value", {})
                recipient = _tron_hex_to_base58(value.get("to_address"))
                if recipient != address:
                    continue
                amount = Decimal(int(value.get("amount", 0))) / Decimal(10 ** 6)
                exchange_record_chain_event(
                    "TRON", "TRX", tx.get("txID"), f"trx:{contract_index}", address, uid, amount,
                    int(tx.get("blockNumber") or 0), _chain_confirmation_threshold("TRX"), tx,
                )

        if not USDT_TRC20_CONTRACT:
            continue
        trc20 = requests.get(
            f"{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions/trc20",
            params={
                "only_confirmed": "true", "only_to": "true", "limit": 50,
                "order_by": "block_timestamp,desc", "contract_address": USDT_TRC20_CONTRACT,
            },
            headers=_trongrid_headers(), timeout=25,
        )
        trc20.raise_for_status()
        for transfer in trc20.json().get("data", []):
            token = transfer.get("token_info", {})
            contract_address = str(token.get("address") or "")
            if contract_address and contract_address != USDT_TRC20_CONTRACT:
                continue
            if str(transfer.get("to") or "") != address:
                continue
            decimals = int(token.get("decimals", 6))
            amount = Decimal(str(transfer.get("value") or "0")) / Decimal(10 ** decimals)
            exchange_record_chain_event(
                "TRON", "USDT", transfer.get("transaction_id"), "trc20:0", address, uid, amount,
                0, _chain_confirmation_threshold("USDT"), transfer,
            )


def xmr_indexer_once():
    if not XMR_WALLET_RPC_URL:
        return
    watched_rows = exchange_list_addresses("XMR", INDEXER_ADDRESS_LIMIT)
    if not watched_rows:
        return
    watched = {item["address"]: item["user_id"] for item in watched_rows}
    result = _xmr_rpc("get_transfers", {
        "in": True,
        "pending": True,
        "pool": True,
        "failed": False,
        "account_index": XMR_ACCOUNT_INDEX,
    })
    for category in ("in", "pending", "pool"):
        for transfer in result.get(category, []) or []:
            address = str(transfer.get("address") or "")
            if address not in watched:
                continue
            subaddr = transfer.get("subaddr_index") or {}
            event_index = f"{subaddr.get('major', XMR_ACCOUNT_INDEX)}:{subaddr.get('minor', 0)}"
            amount = Decimal(int(transfer.get("amount") or 0)) / Decimal(10 ** 12)
            confirmations = int(transfer.get("confirmations") or 0)
            removed = bool(transfer.get("double_spend_seen"))
            exchange_record_chain_event(
                "XMR", "XMR", transfer.get("txid"), event_index, address, watched[address], amount,
                int(transfer.get("height") or 0), confirmations, transfer, removed,
            )


def blockcypher_indexer_once(asset):
    asset = str(asset).upper()
    if asset not in ("BTC", "LTC") or not BLOCKCYPHER_KEY:
        return
    chain = asset
    coin = "btc" if asset == "BTC" else "ltc"
    addresses = exchange_list_addresses(chain, INDEXER_ADDRESS_LIMIT)
    for item in addresses:
        response = requests.get(
            f"https://api.blockcypher.com/v1/{coin}/main/addrs/{item['address']}",
            params={"token": BLOCKCYPHER_KEY, "limit": 200, "includeScript": "false"},
            headers={"User-Agent": "Nerlo-Exchange/1.0"}, timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        refs = list(payload.get("txrefs") or []) + list(payload.get("unconfirmed_txrefs") or [])
        for ref in refs:
            if int(ref.get("tx_input_n", -2)) != -1 or int(ref.get("tx_output_n", -1)) < 0:
                continue
            amount = Decimal(int(ref.get("value", 0))) / Decimal(10 ** 8)
            exchange_record_chain_event(
                chain, asset, ref.get("tx_hash"), str(ref.get("tx_output_n")), item["address"], item["user_id"],
                amount, int(ref.get("block_height") or 0), int(ref.get("confirmations") or 0), ref,
                bool(ref.get("double_spend")),
            )


def _sync_request_cache_from_db(rid):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload,status,automatic FROM exchange_requests WHERE request_id=%s", (str(rid),))
            row = cur.fetchone()
    if not row:
        return None
    payload = dict(row[0] or {})
    payload["status"] = row[1]
    payload["automatic"] = bool(row[2])
    requests_db[str(rid)] = payload
    save_json(FILES["requests"], requests_db)
    return payload


def exchange_finalize_withdrawal(rid, txid=""):
    rid = str(rid)
    record = _sync_request_cache_from_db(rid)
    if not record or record.get("type") != "withdraw":
        raise ValueError("Çekim talebi bulunamadı")
    if record.get("status") == "completed":
        return record
    if record.get("status") == "rejected":
        raise ValueError("Reddedilmiş çekim tamamlanamaz")
    amount = D(record.get("amount"))
    fee = D(record.get("fee"))
    entries = [{
        "user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -amount,
        "entry_type": "withdrawal_broadcast_settled", "reference_type": "request", "reference_id": rid,
        "idempotency_key": f"withdraw:{rid}:settled", "metadata": {"txid": txid or record.get("broadcast_txid", "")},
    }]
    if fee > 0:
        entries.append({
            "user_id": "__platform__", "asset": record["asset"], "bucket": "available", "amount": fee,
            "entry_type": "withdraw_fee_revenue", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"withdraw:{rid}:fee",
            "metadata": {"user_id": record["user_id"], "txid": txid or record.get("broadcast_txid", "")},
        })
    exchange_apply_ledger(entries)
    changes = {"status": "completed", "completed_at": now(), "broadcast_locked": False, "signer_status": "confirmed"}
    if txid:
        changes["broadcast_txid"] = txid
    exchange_update_request(rid, changes)
    return requests_db.get(rid, record)


def exchange_refund_withdrawal(rid, reason=""):
    rid = str(rid)
    record = _sync_request_cache_from_db(rid)
    if not record or record.get("type") != "withdraw":
        raise ValueError("Çekim talebi bulunamadı")
    if record.get("status") == "rejected":
        return record
    if record.get("status") == "completed":
        raise ValueError("Tamamlanmış çekim iade edilemez")
    amount = D(record.get("amount"))
    exchange_apply_ledger([
        {
            "user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -amount,
            "entry_type": "withdrawal_failed_pending_release", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"withdraw:{rid}:refund-pending", "metadata": {"reason": reason},
        },
        {
            "user_id": record["user_id"], "asset": record["asset"], "bucket": "available", "amount": amount,
            "entry_type": "withdrawal_failed_refund", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"withdraw:{rid}:refund-available", "metadata": {"reason": reason},
        },
    ])
    exchange_update_request(rid, {"status": "rejected", "rejected_at": now(), "failure_reason": reason, "broadcast_locked": False, "signer_status": "failed"})
    return requests_db.get(rid, record)


def _is_crypto_withdraw_record(record):
    """Kripto çekimlerini eski/yeni kayıt biçimlerinde güvenli şekilde tanır."""
    record = dict(record or {})
    request_type = str(record.get("type") or record.get("request_type") or "").strip().lower()
    asset = str(record.get("asset") or "").strip().upper()
    if asset not in CRYPTO_ASSETS:
        return False
    if request_type in ("withdraw", "withdrawal", "crypto_withdraw"):
        return True
    # Eski kayıtlarda type alanı eksik kalmış olabilir. Hedef adres veya
    # çekim onayı işareti varsa bunu da kripto çekimi kabul ederek güvenli tarafta kal.
    return bool(record.get("address") or record.get("approval_required") or record.get("funds_reserved"))


def exchange_admin_request_transition(rid, action):
    """Serialize admin transitions and make all money entries idempotent."""
    rid = str(rid)
    should_enqueue_broadcast = False
    lock_conn = _db_connect()
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"request:{rid}",))
            cur.execute("SELECT payload,status,automatic FROM exchange_requests WHERE request_id=%s FOR UPDATE", (rid,))
            row = cur.fetchone()
            if not row:
                raise ValueError("İşlem talebi bulunamadı")
            record = dict(row[0] or {})
            record["status"] = row[1]
            record["automatic"] = bool(row[2])
            if record.get("broadcast_locked") and action in ("approve_request", "reject_request", "process_request"):
                raise ValueError("Çekim gönderim sistemi tarafından işleniyor; zincir sonucu bekleniyor")
            status = record.get("status")
            if action == "process_request":
                if status != "pending":
                    raise ValueError("İşlem durumu değiştirilemedi")
                is_crypto_withdraw = _is_crypto_withdraw_record(record)
                if is_crypto_withdraw:
                    asset = str(record.get("asset") or "").upper()
                    if asset not in AUTO_WITHDRAW_ASSETS:
                        raise ValueError(f"{asset} otomatik çekim henüz desteklenmiyor")
                    if not record.get("signer_enabled") or not WITHDRAW_SIGNER_URL:
                        raise ValueError("Otomatik gönderim servisi bağlı değil")
                    record.update({
                        "status": "processing",
                        "broadcast_locked": True,
                        "signer_status": "queued",
                        "broadcast_queued_at": now(),
                    })
                    should_enqueue_broadcast = True
                else:
                    record["status"] = "processing"
            elif action == "approve_request":
                if status not in ("pending", "processing"):
                    raise ValueError("İşlem durumu değiştirilemedi")
                if _is_crypto_withdraw_record(record):
                    raise ValueError("Kripto çekimleri otomatik gönderim veya doğrulanmış zincir TXID'si olmadan tamamlanamaz")
                entries = []
                if record.get("type") == "deposit":
                    net = D(record.get("net_amount"))
                    entries.extend([
                        {"user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -net,
                         "entry_type": "deposit_pending_release", "reference_type": "request", "reference_id": rid,
                         "idempotency_key": f"admin:{rid}:deposit-pending-out", "metadata": {}},
                        {"user_id": record["user_id"], "asset": record["asset"], "bucket": "available", "amount": net,
                         "entry_type": "deposit_approved", "reference_type": "request", "reference_id": rid,
                         "idempotency_key": f"admin:{rid}:deposit-available-in", "metadata": {}},
                    ])
                    fee = D(record.get("fee"))
                    if fee > 0:
                        entries.append({"user_id": "__platform__", "asset": record["asset"], "bucket": "available", "amount": fee,
                                        "entry_type": "deposit_fee_revenue", "reference_type": "request", "reference_id": rid,
                                        "idempotency_key": f"admin:{rid}:deposit-fee", "metadata": {"user_id": record["user_id"]}})
                elif record.get("type") == "withdraw":
                    amount = D(record.get("amount"))
                    entries.append({"user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -amount,
                                    "entry_type": "withdraw_pending_release", "reference_type": "request", "reference_id": rid,
                                    "idempotency_key": f"admin:{rid}:withdraw-pending-out", "metadata": {}})
                    fee = D(record.get("fee"))
                    if fee > 0:
                        entries.append({"user_id": "__platform__", "asset": record["asset"], "bucket": "available", "amount": fee,
                                        "entry_type": "withdraw_fee_revenue", "reference_type": "request", "reference_id": rid,
                                        "idempotency_key": f"admin:{rid}:withdraw-fee", "metadata": {"user_id": record["user_id"]}})
                if entries:
                    exchange_apply_ledger(entries)
                record.update({"status": "completed", "completed_at": now()})
            elif action == "reject_request":
                if status not in ("pending", "processing"):
                    raise ValueError("İşlem durumu değiştirilemedi")
                entries = []
                if record.get("type") == "withdraw":
                    amount = D(record.get("amount"))
                    entries.extend([
                        {"user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -amount,
                         "entry_type": "withdraw_pending_cancel", "reference_type": "request", "reference_id": rid,
                         "idempotency_key": f"admin:{rid}:withdraw-pending-cancel", "metadata": {}},
                        {"user_id": record["user_id"], "asset": record["asset"], "bucket": "available", "amount": amount,
                         "entry_type": "withdraw_refund", "reference_type": "request", "reference_id": rid,
                         "idempotency_key": f"admin:{rid}:withdraw-refund", "metadata": {}},
                    ])
                elif record.get("type") == "deposit":
                    net = D(record.get("net_amount"))
                    entries.append({"user_id": record["user_id"], "asset": record["asset"], "bucket": "pending", "amount": -net,
                                    "entry_type": "deposit_pending_cancel", "reference_type": "request", "reference_id": rid,
                                    "idempotency_key": f"admin:{rid}:deposit-pending-cancel", "metadata": {}})
                if entries:
                    exchange_apply_ledger(entries)
                record.update({"status": "rejected", "rejected_at": now()})
            else:
                raise ValueError("Geçersiz yönetici işlemi")
            record["updated_at"] = now()
            cur.execute(
                "UPDATE exchange_requests SET status=%s,payload=%s,updated_at=NOW(),completed_at=CASE WHEN %s THEN COALESCE(completed_at,NOW()) ELSE completed_at END WHERE request_id=%s",
                (record["status"], Jsonb(record), record["status"] == "completed", rid),
            )
        lock_conn.commit()
    finally:
        lock_conn.close()
    requests_db[rid] = record
    save_json(FILES["requests"], requests_db)
    if should_enqueue_broadcast:
        try:
            exchange_enqueue("broadcast_withdrawal", {"request_id": rid}, f"withdraw-broadcast:{rid}", "withdrawals")
        except Exception:
            exchange_update_request(rid, {
                "status": "pending",
                "broadcast_locked": False,
                "signer_status": "queue_failed",
                "signer_error": "Gönderim kuyruğu oluşturulamadı",
            })
            raise
    return requests_db.get(rid, record)

def _notify_withdrawal_broadcast(rid, record, txid):
    uid = str(record.get("user_id") or "")
    if not uid or not txid:
        return
    asset = str(record.get("asset") or "")
    amount = record.get("net_amount") or record.get("amount") or "0"
    if lang_of(uid) == "en":
        text = (
            "Your withdrawal was sent to the blockchain.\n\n"
            f"Transaction: #{rid}\n"
            f"Asset: {asset}\n"
            f"Amount: {ucoin(uid, amount, asset)}\n"
            f"TXID: {txid}\n"
            "Status: Waiting for network confirmation"
        )
    else:
        text = (
            "Çekim işleminiz blockchain ağına gönderildi.\n\n"
            f"İşlem No: #{rid}\n"
            f"Coin: {asset}\n"
            f"Tutar: {ucoin(uid, amount, asset)}\n"
            f"TXID: {txid}\n"
            "Durum: Ağ onayı bekleniyor"
        )
    send(uid, text, reply_keyboard(uid))


def _process_withdraw_broadcast(job):
    if not WITHDRAW_SIGNER_URL:
        raise RuntimeError("WITHDRAW_SIGNER_URL tanımlı değil")
    rid = str(job["payload"].get("request_id") or "")
    record = _sync_request_cache_from_db(rid)
    if not record:
        raise ValueError("Çekim talebi bulunamadı")
    if record.get("status") in ("completed", "rejected"):
        return
    exchange_update_request(rid, {"status": "processing", "broadcast_locked": True, "signer_status": "attempting", "broadcast_started_at": now()})
    record = requests_db.get(rid, record)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Nerlo-Exchange/1.0",
        "Idempotency-Key": f"withdraw:{rid}",
    }
    if WITHDRAW_SIGNER_TOKEN:
        headers["Authorization"] = f"Bearer {WITHDRAW_SIGNER_TOKEN}"
    response = requests.post(
        WITHDRAW_SIGNER_URL,
        json={
            "request_id": rid,
            "user_id": record.get("user_id"),
            "asset": record.get("asset"),
            "amount": record.get("net_amount"),
            "gross_amount": record.get("amount"),
            "fee": record.get("fee"),
            "address": record.get("address"),
            "network": settings.get(f"network_{record.get('asset')}", record.get("asset")),
            "callback_url": f"{PUBLIC_BASE_URL}/internal/exchange/withdrawal" if PUBLIC_BASE_URL else "",
        },
        headers=headers, timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    txid = str(payload.get("txid") or payload.get("transaction_id") or "")
    if not txid:
        raise RuntimeError("Signer txid döndürmedi")
    changes = {"status": "processing", "broadcast_txid": txid, "broadcast_at": now(), "signer_response": payload, "signer_status": "broadcast"}
    exchange_update_request(rid, changes)
    current = requests_db.get(rid, record)
    if not current.get("txid_notified_at") or str(current.get("txid_notified")) != txid:
        _notify_withdrawal_broadcast(rid, current, txid)
        exchange_update_request(rid, {"txid_notified_at": now(), "txid_notified": txid})
    if payload.get("confirmed") is True or str(payload.get("status", "")).lower() == "confirmed":
        exchange_finalize_withdrawal(rid, txid)


def exchange_worker_loop():
    while True:
        job = None
        try:
            job = exchange_claim_job()
            if not job:
                time.sleep(EXCHANGE_JOB_POLL_SECONDS)
                continue
            handler = {
                "process_chain_event": _process_chain_event,
                "broadcast_withdrawal": _process_withdraw_broadcast,
            }.get(job["job_type"])
            if not handler:
                raise RuntimeError(f"Bilinmeyen job tipi: {job['job_type']}")
            handler(job)
            exchange_complete_job(job["id"])
        except Exception as exc:
            if job:
                exchange_fail_job(job, exc)
            else:
                print("EXCHANGE WORKER LOOP ERROR:", exc)
                time.sleep(EXCHANGE_JOB_POLL_SECONDS)


def _run_singleton_polling_service(lock_name, callback, interval):
    """Hold a PostgreSQL advisory lock so only one Railway replica indexes a chain."""
    while True:
        lock_conn = None
        try:
            lock_conn = _db_connect()
            lock_conn.autocommit = True
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint)", (f"nerlo:{lock_name}",))
                acquired = bool(cur.fetchone()[0])
            if not acquired:
                lock_conn.close()
                time.sleep(max(10, interval))
                continue
            while True:
                try:
                    callback()
                    with lock_conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        cur.fetchone()
                except Exception as exc:
                    print(f"{lock_name.upper()} SERVICE ERROR:", exc)
                    raise
                time.sleep(interval)
        except Exception as exc:
            print(f"{lock_name.upper()} LOCK ERROR:", exc)
            time.sleep(max(10, interval))
        finally:
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception:
                    pass


def start_exchange_threads():
    if not EXCHANGE_MODE:
        print("EXCHANGE MODE: disabled")
        return
    threading.Thread(target=exchange_worker_loop, daemon=True, name="exchange-worker").start()
    if ETH_RPC_URL:
        threading.Thread(target=_run_singleton_polling_service, args=("eth-indexer", eth_indexer_once, WALLET_SCAN_SECONDS), daemon=True, name="eth-indexer").start()
    if TRONGRID_KEY:
        threading.Thread(target=_run_singleton_polling_service, args=("tron-indexer", tron_indexer_once, WALLET_SCAN_SECONDS), daemon=True, name="tron-indexer").start()
    threading.Thread(target=_run_singleton_polling_service, args=("ledger-reconcile", exchange_reconcile_once, EXCHANGE_RECONCILE_SECONDS), daemon=True, name="ledger-reconcile").start()
    if XMR_WALLET_RPC_URL:
        threading.Thread(target=_run_singleton_polling_service, args=("xmr-indexer", xmr_indexer_once, WALLET_SCAN_SECONDS), daemon=True, name="xmr-indexer").start()
    if BLOCKCYPHER_KEY:
        threading.Thread(target=_run_singleton_polling_service, args=("btc-indexer", lambda: blockcypher_indexer_once("BTC"), WALLET_SCAN_SECONDS), daemon=True, name="btc-indexer").start()
        threading.Thread(target=_run_singleton_polling_service, args=("ltc-indexer", lambda: blockcypher_indexer_once("LTC"), WALLET_SCAN_SECONDS), daemon=True, name="ltc-indexer").start()


def exchange_reconcile_once():
    """Verify that the account projection exactly equals the immutable ledger."""
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.user_id,a.asset,a.available,a.pending,a.locked,
                       COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='available'),0) AS ledger_available,
                       COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='pending'),0) AS ledger_pending,
                       COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='locked'),0) AS ledger_locked
                FROM exchange_accounts a
                LEFT JOIN exchange_ledger l ON l.user_id=a.user_id AND l.asset=a.asset
                GROUP BY a.user_id,a.asset,a.available,a.pending,a.locked
                HAVING a.available<>COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='available'),0)
                    OR a.pending<>COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='pending'),0)
                    OR a.locked<>COALESCE(SUM(l.amount) FILTER (WHERE l.bucket='locked'),0)
                ORDER BY a.user_id,a.asset
                """
            )
            rows = cur.fetchall()
            summary = {
                "checked_at": now(),
                "mismatch_count": len(rows),
                "mismatches": [
                    {"user_id": r[0], "asset": r[1], "account": [str(r[2]), str(r[3]), str(r[4])], "ledger": [str(r[5]), str(r[6]), str(r[7])]}
                    for r in rows[:100]
                ],
            }
            cur.execute(
                """
                INSERT INTO exchange_meta(meta_key,meta_value) VALUES ('last-reconciliation',%s)
                ON CONFLICT(meta_key) DO UPDATE SET meta_value=EXCLUDED.meta_value,updated_at=NOW()
                """,
                (Jsonb(summary),),
            )
        conn.commit()
    if rows:
        print("CRITICAL LEDGER RECONCILIATION MISMATCH:", summary)
    return summary


def exchange_health_snapshot():
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,COUNT(*) FROM exchange_jobs GROUP BY status")
            jobs = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT status,COUNT(*) FROM exchange_chain_events GROUP BY status")
            events = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT chain,COUNT(*) FROM exchange_addresses WHERE status='active' GROUP BY chain")
            addresses = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT meta_value FROM exchange_meta WHERE meta_key='last-reconciliation'")
            reconciliation_row = cur.fetchone()
            reconciliation = reconciliation_row[0] if reconciliation_row else {}
    return {
        "build_version": BUILD_VERSION,
        "mode": EXCHANGE_MODE,
        "worker_id": EXCHANGE_WORKER_ID,
        "providers": {
            "ethereum": bool(ETH_RPC_URL),
            "tron": bool(TRONGRID_KEY),
            "blockcypher": bool(BLOCKCYPHER_KEY),
            "monero_wallet_rpc": bool(XMR_WALLET_RPC_URL),
            "signer": bool(WITHDRAW_SIGNER_URL),
        },
        "xpubs": {"BTC": bool(BTC_XPUB), "LTC": bool(LTC_XPUB), "ETH": bool(ETH_XPUB), "TRON": bool(TRON_XPUB)},
        "addresses": addresses,
        "events": events,
        "jobs": jobs,
        "reconciliation": reconciliation,
        "withdraw_guard": {
            "enabled": True,
            "crypto_manual_complete_blocked": True,
            "txid_required": True,
        },
    }


# Fail fast if the local Keccak implementation is ever modified incorrectly.
if _keccak256(b"").hex() != "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470":
    raise RuntimeError("Keccak-256 self-test failed")


def validate_runtime_config():
    required = {"BOT_TOKEN": TOKEN, "ADMIN_CHAT_ID": ADMIN_CHAT_ID, "PANEL_USERNAME": PANEL_USERNAME, "PANEL_PASSWORD": PANEL_PASSWORD, "FLASK_SECRET_KEY": app.secret_key}
    missing = [k for k, v in required.items() if not str(v).strip()]
    if missing:
        raise RuntimeError("Eksik zorunlu ortam değişkenleri: " + ", ".join(missing))
    if len(app.secret_key) < 32:
        raise RuntimeError("FLASK_SECRET_KEY en az 32 karakter olmalıdır")
    if len(PANEL_PASSWORD) < 12:
        raise RuntimeError("PANEL_PASSWORD en az 12 karakter olmalıdır")
    allowed_address_types = {"p2pkh", "p2wpkh", "bech32", "p2sh-p2wpkh", "nested-segwit"}
    if BTC_ADDRESS_TYPE not in allowed_address_types or LTC_ADDRESS_TYPE not in allowed_address_types:
        raise RuntimeError("BTC_ADDRESS_TYPE/LTC_ADDRESS_TYPE geçersiz")
    for chain, xpub in (("BTC", BTC_XPUB), ("LTC", LTC_XPUB), ("ETH", ETH_XPUB), ("TRON", TRON_XPUB)):
        if xpub:
            try:
                _derive_chain_address(chain, 0)
            except Exception as exc:
                raise RuntimeError(f"{chain}_XPUB doğrulanamadı: {exc}") from exc
    if XMR_WALLET_RPC_URL:
        parsed_xmr = urlparse(XMR_WALLET_RPC_URL)
        if parsed_xmr.scheme != "https" and parsed_xmr.hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("Uzak XMR_WALLET_RPC_URL HTTPS olmalıdır")
        if parsed_xmr.hostname not in ("localhost", "127.0.0.1") and not XMR_WALLET_RPC_PASSWORD:
            raise RuntimeError("Uzak Monero wallet RPC için kimlik doğrulama zorunludur")
    if WITHDRAW_SIGNER_URL:
        parsed = urlparse(WITHDRAW_SIGNER_URL)
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("WITHDRAW_SIGNER_URL production ortamında HTTPS olmalıdır")
        if not WITHDRAW_SIGNER_TOKEN:
            raise RuntimeError("WITHDRAW_SIGNER_URL kullanılırken WITHDRAW_SIGNER_TOKEN zorunludur")
        if len(EXCHANGE_INTERNAL_TOKEN) < 32:
            raise RuntimeError("Otomatik signer için EXCHANGE_INTERNAL_TOKEN en az 32 karakter olmalıdır")
        if PUBLIC_BASE_URL:
            public_url = urlparse(PUBLIC_BASE_URL)
            if public_url.scheme != "https":
                raise RuntimeError("PUBLIC_BASE_URL production ortamında HTTPS olmalıdır")
    if EXCHANGE_INTERNAL_TOKEN and len(EXCHANGE_INTERNAL_TOKEN) < 32:
        raise RuntimeError("EXCHANGE_INTERNAL_TOKEN en az 32 karakter olmalıdır")


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32); session["csrf_token"] = token
    return token


@app.before_request
def enforce_csrf():
    if request.method == "POST":
        if request.path.startswith("/internal/exchange/"):
            supplied_internal = request.headers.get("X-Exchange-Token", "") or request.headers.get("Authorization", "").removeprefix("Bearer ")
            if EXCHANGE_INTERNAL_TOKEN and secrets.compare_digest(supplied_internal, EXCHANGE_INTERNAL_TOKEN):
                return None
            abort(403)
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
    precision = ASSET_PRECISIONS.get(asset, Decimal("0.01"))
    out = value.quantize(precision, rounding=ROUND_DOWN)
    return f"{out} {asset}".strip()


def coin_fmt(value, asset):
    asset = str(asset or "")
    value = D(value)
    precision = ASSET_PRECISIONS.get(asset, Decimal("0.01"))
    out = value.quantize(precision, rounding=ROUND_DOWN)

    decimals = max(0, -precision.as_tuple().exponent)
    formatted = f"{out:,.{decimals}f}"
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
    "wallet_XMR": os.getenv("DEFAULT_WALLET_XMR", "4BBTqNzpdsg3cyB4WZS9jVNvo2gX5MRUdAFTx5NboVudR9BMcjWmsU9bHZiaH11P3E2cjmnopDDZj7hCuADLHeWTPopPxUh"),
    "wallet_LTC": os.getenv("DEFAULT_WALLET_LTC", ""),
    "rate_USDT_TL": "46.40",
    "rate_LTC_TL": "2065.00",
    "rate_TRX_TL": "15.50",
    "rate_XMR_TL": "0",
    "auto_rate_enabled": "on",
    "rates_source": "Binance Spot",
    "rates_last_updated": "",
    "rates_last_error": "",
    "fee_deposit_TL_percent": "6",
    "fee_deposit_USDT_percent": "1",
    "fee_deposit_LTC_percent": "1",
    "fee_deposit_TRX_percent": "1",
    "fee_deposit_XMR_percent": "1",
    "fee_withdraw_TL_percent": "7",
    "fee_withdraw_USDT_percent": "2",
    "fee_withdraw_LTC_percent": "2",
    "fee_withdraw_TRX_percent": "2",
    "fee_withdraw_XMR_percent": "2.5",
    # Legacy fallback oranları
    "fee_convert_tl_percent": "2",
    "fee_convert_crypto_percent": "2",
    # Yön ve parite bazında dönüşüm komisyonları
    "fee_convert_TL_USDT_percent": "10",
    "fee_convert_TL_LTC_percent": "11",
    "fee_convert_TL_TRX_percent": "11",
    "fee_convert_TL_XMR_percent": "12",
    "fee_convert_USDT_TL_percent": "2",
    "fee_convert_USDT_LTC_percent": "4",
    "fee_convert_USDT_TRX_percent": "4",
    "fee_convert_USDT_XMR_percent": "5",
    "fee_convert_LTC_TL_percent": "2.5",
    "fee_convert_LTC_USDT_percent": "4",
    "fee_convert_LTC_TRX_percent": "4.5",
    "fee_convert_LTC_XMR_percent": "5",
    "fee_convert_TRX_TL_percent": "2.5",
    "fee_convert_TRX_USDT_percent": "4",
    "fee_convert_TRX_LTC_percent": "4.5",
    "fee_convert_TRX_XMR_percent": "5",
    "fee_convert_XMR_TL_percent": "3",
    "fee_convert_XMR_USDT_percent": "5",
    "fee_convert_XMR_LTC_percent": "5",
    "fee_convert_XMR_TRX_percent": "5",
    "min_deposit_TL": "100",
    "min_deposit_USDT": "5",
    "min_deposit_LTC": "0.01",
    "min_deposit_TRX": "50",
    "min_deposit_XMR": "0.01",
    "min_withdraw_TL": "100",
    "min_withdraw_USDT": "5",
    "min_withdraw_LTC": "0.01",
    "min_withdraw_TRX": "50",
    "min_withdraw_XMR": "0.01",
    "min_convert_TL": "100",
    "min_convert_USDT": "5",
    "min_convert_LTC": "0.01",
    "min_convert_TRX": "50",
    "min_convert_XMR": "0.01",
    "daily_withdraw_limit_TL": "50000",
    "daily_withdraw_limit_USDT": "1000",
    "daily_withdraw_limit_LTC": "10",
    "daily_withdraw_limit_TRX": "50000",
    "daily_withdraw_limit_XMR": "5",
    "maintenance_mode": "off",
    "maintenance_message": "",
    "announcement_active": "off",
    "announcement_text": "",
    "network_USDT": "TRC20",
    "network_TRX": "TRON",
    "network_XMR": "Monero",
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
    "icon_XMR": "5900147027219587568",
    "icon_ETH": "5900047564366945678",
    "icon_BTC": "5899763383560838498",
    "icon_TON": "5900174643859299618",
    "icon_TL": "5897961936837943618",
}

_NEW_ASSET_DEFAULTS = {
    "BTC": {"rate": "0", "deposit_min": "0.00001", "withdraw_min": "0.0001", "convert_min": "0.00001", "daily": "1", "network": "Bitcoin"},
    "ETH": {"rate": "0", "deposit_min": "0.0001", "withdraw_min": "0.001", "convert_min": "0.0001", "daily": "10", "network": "Ethereum"},
    "TON": {"rate": "0", "deposit_min": "0.1", "withdraw_min": "1", "convert_min": "0.1", "daily": "10000", "network": "TON"},
}
for _asset, _defaults in _NEW_ASSET_DEFAULTS.items():
    DEFAULT_SETTINGS.setdefault(f"wallet_{_asset}", os.getenv(f"DEFAULT_WALLET_{_asset}", ""))
    DEFAULT_SETTINGS.setdefault(f"rate_{_asset}_TL", _defaults["rate"])
    DEFAULT_SETTINGS.setdefault(f"fee_deposit_{_asset}_percent", "1")
    DEFAULT_SETTINGS.setdefault(f"fee_withdraw_{_asset}_percent", "2")
    DEFAULT_SETTINGS.setdefault(f"min_deposit_{_asset}", _defaults["deposit_min"])
    DEFAULT_SETTINGS.setdefault(f"min_withdraw_{_asset}", _defaults["withdraw_min"])
    DEFAULT_SETTINGS.setdefault(f"min_convert_{_asset}", _defaults["convert_min"])
    DEFAULT_SETTINGS.setdefault(f"daily_withdraw_limit_{_asset}", _defaults["daily"])
    DEFAULT_SETTINGS.setdefault(f"network_{_asset}", _defaults["network"])
    DEFAULT_SETTINGS.setdefault(f"icon_{_asset}", "")

for _source in ASSETS:
    for _target in ASSETS:
        if _source != _target:
            DEFAULT_SETTINGS.setdefault(
                f"fee_convert_{_source}_{_target}_percent",
                "2" if "TL" in (_source, _target) else "4",
            )

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
legacy_tl_convert_fee = str(settings.get("fee_convert_tl_percent", legacy_convert_fee))
legacy_crypto_convert_fee = str(settings.get("fee_convert_crypto_percent", legacy_convert_fee))
for k, v in DEFAULT_SETTINGS.items():
    if k.startswith("fee_convert_") and k.endswith("_percent"):
        pair_match = re.fullmatch(rf"fee_convert_({ASSET_PATTERN})_({ASSET_PATTERN})_percent", k)
        if pair_match:
            source_asset, target_asset = pair_match.groups()
            fallback = legacy_tl_convert_fee if "TL" in (source_asset, target_asset) else legacy_crypto_convert_fee
            settings.setdefault(k, fallback)
            continue
    settings.setdefault(k, legacy_convert_fee if k in ("fee_convert_tl_percent", "fee_convert_crypto_percent") else v)
settings.pop("fee_convert_percent", None)

COMMISSION_PRESET_VERSION = "2026-06-24-balanced-v1"
COMMISSION_PRESET = {
    "fee_deposit_TL_percent": "6",
    "fee_deposit_USDT_percent": "1",
    "fee_deposit_LTC_percent": "1",
    "fee_deposit_TRX_percent": "1",
    "fee_deposit_XMR_percent": "1",
    "fee_withdraw_TL_percent": "7",
    "fee_withdraw_USDT_percent": "2",
    "fee_withdraw_LTC_percent": "2",
    "fee_withdraw_TRX_percent": "2",
    "fee_withdraw_XMR_percent": "2.5",
    "fee_convert_TL_USDT_percent": "10",
    "fee_convert_TL_LTC_percent": "11",
    "fee_convert_TL_TRX_percent": "11",
    "fee_convert_TL_XMR_percent": "12",
    "fee_convert_USDT_TL_percent": "2",
    "fee_convert_LTC_TL_percent": "2.5",
    "fee_convert_TRX_TL_percent": "2.5",
    "fee_convert_XMR_TL_percent": "3",
    "fee_convert_USDT_LTC_percent": "4",
    "fee_convert_LTC_USDT_percent": "4",
    "fee_convert_USDT_TRX_percent": "4",
    "fee_convert_TRX_USDT_percent": "4",
    "fee_convert_LTC_TRX_percent": "4.5",
    "fee_convert_TRX_LTC_percent": "4.5",
    "fee_convert_USDT_XMR_percent": "5",
    "fee_convert_XMR_USDT_percent": "5",
    "fee_convert_LTC_XMR_percent": "5",
    "fee_convert_XMR_LTC_percent": "5",
    "fee_convert_TRX_XMR_percent": "5",
    "fee_convert_XMR_TRX_percent": "5",
}
if settings.get("_commission_preset_version") != COMMISSION_PRESET_VERSION:
    settings.update(COMMISSION_PRESET)
    settings["_commission_preset_version"] = COMMISSION_PRESET_VERSION
if not str(settings.get("icon_TL", "")).strip():
    settings["icon_TL"] = DEFAULT_SETTINGS["icon_TL"]
if not str(settings.get("icon_XMR", "")).strip():
    settings["icon_XMR"] = DEFAULT_SETTINGS["icon_XMR"]
for _asset in ("ETH", "BTC", "TON"):
    if not str(settings.get(f"icon_{_asset}", "")).strip():
        settings[f"icon_{_asset}"] = DEFAULT_SETTINGS[f"icon_{_asset}"]
if not str(settings.get("wallet_XMR", "")).strip():
    settings["wallet_XMR"] = DEFAULT_SETTINGS["wallet_XMR"]
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

# Import existing balances/requests exactly once, then use the transactional
# exchange tables as the source of truth.
exchange_bootstrap_state()


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
    pattern = re.compile(rf"\{{\{{({ASSET_PATTERN})\}}\}}")

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
        rf"\{{\{{({ASSET_PATTERN})\}}\}}",
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
    refresh_runtime_state(_db_key(FILES["messages"]), messages)
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
    clean_rows = [(str(label).strip(), str(value).strip()) for label, value in rows if value not in (None, "")]
    lines = ["NERLO WALLET", str(title).upper(), "────────────────────"]
    for label, value in clean_rows:
        lines.append(f"{label.upper()}")
        lines.append(value)
    if note:
        lines.extend(["────────────────────", f"Bilgi · {str(note).strip()}"])
    return "\n".join(lines)


def coin_fmt_lang(value, asset, lang="tr"):
    asset = str(asset or "")
    value = D(value)
    precision = ASSET_PRECISIONS.get(asset, Decimal("0.01"))
    out = value.quantize(precision, rounding=ROUND_DOWN)
    decimals = max(0, -precision.as_tuple().exponent)
    raw = f"{out:,.{decimals}f}"
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
    stored_profile = exchange_load_profile(uid)
    if stored_profile is not None:
        users[uid] = stored_profile
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
    try:
        exchange_refresh_user_cache(uid)
    except Exception as exc:
        print("USER BALANCE REFRESH ERROR:", exc)
    if username:
        u["username"] = username
    u["last_seen"] = now()
    u["sessions"]["telegram"]["last_seen"] = now()
    save_user_profile(uid)
    return u


def balance(uid, asset):
    """Kullanılabilir bakiye; PostgreSQL account projection is the source of truth."""
    return exchange_balance(str(uid), asset, "available")


def pending_balance(uid, asset):
    """İşlemde/blokede bekleyen bakiye."""
    return exchange_balance(str(uid), asset, "pending")


def add_transaction(uid, asset, amount, kind, ref_id="", note="", bucket="available"):
    """Compatibility audit mirror for the existing panel transaction view."""
    tid = uuid.uuid4().hex[:20]
    while tid in transactions:
        tid = uuid.uuid4().hex[:20]
    previous = list(transactions.values())[-1].get("entry_hash", "") if transactions else ""
    entry = {"id": tid, "user_id": str(uid), "asset": asset, "amount": str(D(amount)), "kind": kind, "bucket": bucket, "ref_id": str(ref_id), "note": note, "available_after": str(balance(uid, asset)), "pending_after": str(pending_balance(uid, asset)), "created_at": now(), "previous_hash": previous}
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    entry["entry_hash"] = hashlib.sha256((previous + canonical).encode()).hexdigest()
    transactions[tid] = entry
    save_json(FILES["transactions"], transactions)
    return tid


def change_balance(uid, asset, amount, kind, ref_id="", note="", idempotency_key=""):
    uid = str(uid)
    exchange_post_ledger(
        uid, asset, D(amount), "available", kind, ref_id, note,
        idempotency_key=idempotency_key or f"{kind}:{ref_id}:{uuid.uuid4().hex}",
    )
    return True


def change_pending(uid, asset, amount, kind, ref_id="", note="", idempotency_key=""):
    uid = str(uid)
    exchange_post_ledger(
        uid, asset, D(amount), "pending", kind, ref_id, note,
        idempotency_key=idempotency_key or f"{kind}:{ref_id}:{uuid.uuid4().hex}",
    )
    return True


def add_security_event(uid, event, detail=""):
    security_events.append({"created_at": now(), "user_id": str(uid), "event": event, "detail": detail})
    users[str(uid)]["last_security_event"] = now()
    save_json(FILES["security_events"], security_events)
    save_user_profile(uid)


def add_admin_log(action, details, uid=""):
    admin_logs.append({"created_at": now(), "action": action, "details": details, "user_id": str(uid)})
    save_json(FILES["admin_logs"], admin_logs)


def new_request(uid, kind, data):
    data = dict(data or {})
    uid = str(uid)
    idem = str(data.get("idempotency_key") or "").strip()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            if idem:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"request-idempotency:{idem}",))
                cur.execute(
                    "SELECT request_id,payload,status,automatic FROM exchange_requests WHERE idempotency_key=%s",
                    (idem,),
                )
                row = cur.fetchone()
                if row:
                    conn.commit()
                    rid = str(row[0])
                    cached = dict(row[1] or {})
                    cached["status"] = row[2]
                    cached["automatic"] = bool(row[3])
                    requests_db[rid] = cached
                    return rid

            while True:
                rid = str(secrets.randbelow(900_000_000_000) + 100_000_000_000)
                cur.execute("SELECT 1 FROM exchange_requests WHERE request_id=%s", (rid,))
                if not cur.fetchone():
                    break
            record = {
                "id": rid, "user_id": uid, "type": kind, "status": "pending",
                "created_at": now(), "updated_at": now(), **data,
            }
            exchange_upsert_request(record, conn=conn)
        conn.commit()
    requests_db[rid] = record
    save_json(FILES["requests"], requests_db)
    return rid


def atomic_withdraw(uid, state):
    uid = str(uid)
    amount = D(state["amount"])
    asset = state["asset"]
    if asset != "TL":
        valid, normalized_address, address_error = validate_wallet_address(asset, state.get("address", ""))
        if not valid:
            raise ValueError(address_error)
        state["address"] = normalized_address
    if amount <= 0:
        raise ValueError("Geçersiz çekim tutarı")
    if balance(uid, asset) < amount:
        raise ValueError("Yetersiz bakiye")
    signer_enabled = bool(WITHDRAW_SIGNER_URL and asset in AUTO_WITHDRAW_ASSETS)
    rid = new_request(uid, "withdraw", {
        "asset": asset, "amount": state["amount"], "fee": state["fee"], "net_amount": state["net_amount"],
        "bank_name": state.get("bank_name", ""), "iban": state.get("iban", ""), "name": state.get("name", ""),
        "address": state.get("address", ""), "second_confirmation": True,
        "idempotency_key": state.get("confirm_token", ""), "automatic": False,
        "signer_enabled": signer_enabled, "approval_required": asset != "TL",
    })
    try:
        exchange_apply_ledger([
            {
                "user_id": uid, "asset": asset, "bucket": "available", "amount": -amount,
                "entry_type": "withdraw_hold_available", "reference_type": "request", "reference_id": rid,
                "idempotency_key": f"withdraw:{rid}:available-hold", "metadata": {},
            },
            {
                "user_id": uid, "asset": asset, "bucket": "pending", "amount": amount,
                "entry_type": "withdraw_hold_pending", "reference_type": "request", "reference_id": rid,
                "idempotency_key": f"withdraw:{rid}:pending-hold", "metadata": {},
            },
        ])
    except Exception as exc:
        exchange_update_request(rid, {"status": "rejected", "failure_reason": "reservation_failed", "failure_detail": str(exc)[:500]})
        raise
    requests_db[rid]["funds_reserved"] = True
    requests_db[rid]["updated_at"] = now()
    exchange_upsert_request(requests_db[rid])
    save_json(FILES["requests"], requests_db)
    return rid


def atomic_convert(uid, state):
    uid = str(uid)
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
    state.update({"tl_value": str(tl_value), "gross_to": str(gross), "fee": str(fee), "net_amount": str(net)})

    rid = new_request(uid, "convert", {
        "from_asset": source, "to_asset": target, "from_amount": str(amount), "tl_value": str(tl_value),
        "fee": str(fee), "net_to_amount": str(net), "second_confirmation": True,
        "idempotency_key": state.get("confirm_token", ""), "automatic": True,
    })
    entries = [
        {
            "user_id": uid, "asset": source, "bucket": "available", "amount": -amount,
            "entry_type": "convert_out", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"convert:{rid}:out", "metadata": {"target": target},
        },
        {
            "user_id": uid, "asset": target, "bucket": "available", "amount": net,
            "entry_type": "convert_in", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"convert:{rid}:in", "metadata": {"source": source},
        },
    ]
    if fee > 0:
        entries.append({
            "user_id": "__platform__", "asset": target, "bucket": "available", "amount": fee,
            "entry_type": "convert_fee_revenue", "reference_type": "request", "reference_id": rid,
            "idempotency_key": f"convert:{rid}:fee", "metadata": {"user_id": uid, "source": source},
        })
    try:
        exchange_apply_ledger(entries)
    except Exception as exc:
        exchange_update_request(rid, {"status": "rejected", "failure_reason": "ledger_failed", "failure_detail": str(exc)[:500]})
        raise
    requests_db[rid].update({"status": "completed", "completed_at": now(), "updated_at": now()})
    exchange_upsert_request(requests_db[rid])
    save_json(FILES["requests"], requests_db)
    return rid


def consume_confirmation(state):
    if not state.get("confirm_token") or state.get("confirmation_consumed"): return False
    state["confirmation_consumed"] = True
    return True


def rate(asset):
    refresh_runtime_state(_db_key(FILES["settings"]), settings)
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

    rates = {
        "USDT": usdt_try, "LTC": try_rate("LTC"), "TRX": try_rate("TRX"),
        "BTC": try_rate("BTC"), "ETH": try_rate("ETH"), "TON": try_rate("TON"),
    }
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
            "ids": "tether,litecoin,tron,monero,bitcoin,ethereum,the-open-network",
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
        "XMR": D(payload.get("monero", {}).get("try", "0")),
        "BTC": D(payload.get("bitcoin", {}).get("try", "0")),
        "ETH": D(payload.get("ethereum", {}).get("try", "0")),
        "TON": D(payload.get("the-open-network", {}).get("try", "0")),
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
        print("LIVE RATES UPDATED:", {asset: settings[f"rate_{asset}_TL"] for asset in CRYPTO_ASSETS if f"rate_{asset}_TL" in settings})
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
    refresh_runtime_state(_db_key(FILES["settings"]), settings)
    if uid is not None:
        override = str(users.get(str(uid), {}).get("custom_fee_percent", "")).strip()
        if override:
            return D(override)

    if kind == "convert":
        source = str(from_asset or "").upper()
        target = str(to_asset or "").upper()
        if source in ASSETS and target in ASSETS and source != target:
            pair_key = f"fee_convert_{source}_{target}_percent"
            if pair_key in settings:
                return D(settings.get(pair_key, "0"))

        involves_tl = source == "TL" or target == "TL"
        legacy_key = "fee_convert_tl_percent" if involves_tl else "fee_convert_crypto_percent"
        return D(settings.get(legacy_key, "0"))

    return D(settings.get(f"fee_{kind}_{asset}_percent", "0"))


def fee_amount(amount, p):
    return D(amount) * D(p) / Decimal("100")


def min_amount(kind, asset):
    refresh_runtime_state(_db_key(FILES["settings"]), settings)
    return D(settings.get(f"min_{kind}_{asset}", "0"))


def daily_limit(asset):
    refresh_runtime_state(_db_key(FILES["settings"]), settings)
    return D(settings.get(f"daily_withdraw_limit_{asset}", "0"))


def withdrawn_today(uid, asset):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(NULLIF(payload->>'amount','')::numeric),0)
                FROM exchange_requests
                WHERE user_id=%s AND request_type='withdraw'
                  AND payload->>'asset'=%s
                  AND status IN ('pending','processing','completed')
                  AND (created_at AT TIME ZONE 'Europe/Istanbul')::date=(NOW() AT TIME ZONE 'Europe/Istanbul')::date
                """,
                (str(uid), str(asset)),
            )
            row = cur.fetchone()
    return D(row[0] if row else 0)


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

    is_tr = lang_of(uid) == "tr"
    kind = {
        "deposit": t(uid, "deposit_kind"),
        "withdraw": t(uid, "withdraw_kind"),
        "convert": t(uid, "convert_kind"),
    }.get(r.get("type"), t(uid, "transaction_kind"))
    status = localized_status(uid, r.get("status"))
    rows = [
        ("İşlem No" if is_tr else "Transaction ID", f"#{rid}"),
        ("Durum" if is_tr else "Status", status),
    ]

    if r.get("type") == "deposit":
        asset = r.get("asset")
        rows.extend([
            (t(uid, "amount"), ucoin(uid, r.get("amount"), asset)),
            (t(uid, "fee"), ucoin(uid, r.get("fee"), asset)),
            (t(uid, "credited"), ucoin(uid, r.get("net_amount"), asset)),
        ])
        if asset == "TL":
            rows.extend([
                (t(uid, "sender"), r.get("sender_name", "-")),
                (t(uid, "reference"), r.get("tx_note", "-")),
            ])
        else:
            rows.append((t(uid, "network"), r.get("network") or settings.get(f"network_{asset}", asset)))
            if r.get("txid"):
                rows.append(("TXID", r.get("txid")))

    elif r.get("type") == "withdraw":
        asset = r.get("asset")
        rows.extend([
            (t(uid, "amount"), ucoin(uid, r.get("amount"), asset)),
            (t(uid, "fee"), ucoin(uid, r.get("fee"), asset)),
            (t(uid, "recipient_gets"), ucoin(uid, r.get("net_amount"), asset)),
        ])
        if asset == "TL":
            rows.extend([
                (t(uid, "bank"), r.get("bank_name", "-")),
                ("IBAN", r.get("iban", "-")),
                (t(uid, "recipient"), r.get("name", "-")),
            ])
        else:
            rows.extend([
                (t(uid, "network"), settings.get(f"network_{asset}", asset)),
                (t(uid, "wallet_address"), r.get("address", "-")),
            ])
            if r.get("broadcast_txid"):
                rows.append(("TXID", r.get("broadcast_txid")))

    elif r.get("type") == "convert":
        rows.extend([
            (t(uid, "sent"), ucoin(uid, r.get("from_amount"), r.get("from_asset"))),
            (t(uid, "fee"), ucoin(uid, r.get("fee"), r.get("to_asset"))),
            (t(uid, "to_receive"), ucoin(uid, r.get("net_to_amount"), r.get("to_asset"))),
        ])

    rows.append(("Tarih" if is_tr else "Date", r.get("created_at", "-")))
    return order_summary(f"{kind} · {status}", rows)


def receipt_text(rid, lang_uid=None):
    r = requests_db.get(str(rid))
    uid = str(lang_uid if lang_uid is not None else (r or {}).get("user_id", ""))
    if not r:
        return t(uid, "not_found")
    is_tr = lang_of(uid) == "tr"
    titles = {
        "pending": "İşlem Talebi Alındı" if is_tr else "Request Received",
        "processing": "İşlem İşleniyor" if is_tr else "Transaction Processing",
        "completed": "İşlem Tamamlandı" if is_tr else "Transaction Completed",
        "rejected": "İşlem Reddedildi" if is_tr else "Transaction Rejected",
    }
    marks = {"pending": "◷", "processing": "↻", "completed": "✓", "rejected": "×"}
    status = str(r.get("status") or "pending")
    return f"{marks.get(status, '•')} {titles.get(status, titles['pending'])}\n\n{request_summary(rid, uid)}"


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
    refresh_request_cache()
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


def show_crypto_deposit_address(chat_id, asset, state):
    """Show a persistent user-specific address without trusting a declared amount."""
    uid = str(chat_id)
    if asset in {"BTC", "LTC", "ETH", "TRX", "USDT"} and not exchange_auto_deposit_ready(asset):
        user_state.pop(uid, None)
        message = (
            "Bu ağın otomatik yatırma sistemi henüz tam yapılandırılmadı. XPUB, indexer API ve ağ ayarları tamamlanmadan adres gösterilmez."
            if lang_of(uid) == "tr"
            else "Automatic deposits for this network are not fully configured. The address is hidden until the XPUB, indexer API, and network settings are complete."
        )
        send(chat_id, message, reply_keyboard(uid))
        return False
    try:
        address_info = exchange_get_or_create_address(uid, asset)
    except Exception as exc:
        print("DEPOSIT ADDRESS ERROR:", exc)
        user_state.pop(uid, None)
        send(
            chat_id,
            "Bu coin için güvenli yatırma adresi üretilemedi. Sistem ayarlarını kontrol ediniz."
            if lang_of(uid) == "tr"
            else "A secure deposit address could not be generated for this asset. Check the system configuration.",
            reply_keyboard(uid),
        )
        return False

    address = address_info["address"]
    automatic = (
        asset in AUTO_DEPOSIT_ASSETS
        and address_info.get("source") in ("xpub", "wallet_rpc")
        and exchange_auto_deposit_ready(asset)
    )
    network = settings.get(f"network_{asset}", asset)
    state.update({
        "network": network,
        "qr_content": address,
        "qr_caption": f"{asset} {t(uid, 'qr_caption')} · {network}",
        "deposit_address": address,
        "automatic_deposit": automatic,
        "derivation_path": address_info.get("path", ""),
        "step": "waiting_sent",
    })
    rows = [(t(uid, "network"), network)]
    if state.get("amount"):
        rows.extend([
            (t(uid, "to_deposit"), ucoin(uid, state["amount"], asset)),
            (t(uid, "credited"), ucoin(uid, state["net_amount"], asset)),
        ])
    rows.append((t(uid, "deposit_address"), address))

    if automatic:
        percent = fee_percent("deposit", asset, uid)
        note = (
            f"Bu adres yalnızca size aittir. Gerçek zincir tutarı %{format(percent, 'f')} komisyon sonrası "
            f"{_chain_confirmation_threshold(asset)} ağ onayında otomatik bakiyeye geçer."
            if lang_of(uid) == "tr"
            else f"This address is assigned only to you. The actual on-chain amount is credited automatically "
            f"after a {format(percent, 'f')}% fee and {_chain_confirmation_threshold(asset)} network confirmations."
        )
    else:
        note = (
            "Bu ağ manuel yönetici onayıyla işlenir."
            if lang_of(uid) == "tr"
            else "This network is processed with manual administrator approval."
        )
    card = order_summary(t(uid, "deposit_summary", asset=asset), rows, note)
    buttons = []
    copy = copy_button(t(uid, "deposit_address"), address)
    if copy:
        buttons.append([copy])
    buttons.append([inline_button(t(uid, "show_qr"), "show_deposit_qr")])
    if not automatic:
        buttons.append([inline_button(t(uid, "notify_transfer"), "deposit_sent")])
    buttons.append([inline_button(t(uid, "cancel"), "cancel")])
    send(chat_id, card, {"inline_keyboard": buttons})
    return True


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
        "asset": state["asset"], "amount": state["amount"], "fee": state["fee"],
        "net_amount": state["net_amount"], "idempotency_key": idem, "automatic": False,
    }
    if extra:
        payload.update(extra)
    rid = new_request(uid, "deposit", payload)
    try:
        exchange_post_ledger(
            uid, state["asset"], D(state["net_amount"]), "pending", "deposit_pending", rid,
            idempotency_key=f"manual-deposit:{rid}:pending",
        )
    except Exception as exc:
        exchange_update_request(rid, {"status": "rejected", "failure_reason": "ledger_failed", "failure_detail": str(exc)[:500]})
        raise
    state["request_id"] = rid
    return rid

def prepare_withdraw_amount(uid, state, amount):
    uid = str(uid)
    asset = str(state.get("asset") or "").upper()
    amount = D(amount)
    available = balance(uid, asset)
    if amount <= 0:
        send(uid, t(uid, "valid_amount"))
        return False
    if amount > available:
        send(uid, f"{t(uid, 'insufficient')} · {ucoin(uid, available, asset)}")
        return False
    minimum = min_amount("withdraw", asset)
    if amount < minimum:
        send(uid, f"{t(uid, 'minimum')} · {ucoin(uid, minimum, asset)}")
        return False
    used_today = withdrawn_today(uid, asset)
    limit = daily_limit(asset)
    if limit > 0 and used_today + amount > limit:
        remaining = max(limit - used_today, Decimal("0"))
        send(uid, t(uid, "daily_limit", amount=ucoin(uid, remaining, asset)))
        return False

    percent = fee_percent("withdraw", asset, uid)
    fee = fee_amount(amount, percent)
    net = amount - fee
    if net <= 0:
        send(uid, t(uid, "invalid_net"))
        return False
    state.update({
        "amount": str(amount),
        "fee": str(fee),
        "net_amount": str(net),
        "withdraw_all": amount == available,
    })

    if asset == "TL":
        state["step"] = "bank_name"
        send(uid, t(uid, "bank_name_question"))
        return True

    favs = [item for item in users[uid].get("favorites", []) if item.get("asset") == asset]
    if favs:
        rows = [
            [inline_button(item["label"], f"favorite_use:{index}")]
            for index, item in enumerate(users[uid]["favorites"])
            if item.get("asset") == asset
        ]
        rows.append([inline_button(t(uid, "enter_new_address"), "favorite_use:new")])
        rows.append([inline_button(t(uid, "cancel"), "cancel")])
        send(uid, t(uid, "withdraw_address_select"), {"inline_keyboard": rows})
        state["step"] = "address_choice"
    else:
        state["step"] = "address"
        send(uid, wallet_address_prompt(uid, asset))
    return True


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
        save_user_profile(uid)
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
            save_user_profile(uid)
            if users[uid]["pin_failed_attempts"] >= 3:
                users[uid]["withdraw_locked"] = True
                save_user_profile(uid)
                add_security_event(uid, "pin_lock", "3 hatalı PIN denemesi")
                send(chat_id, t(uid, "pin_locked"))
            else:
                send(chat_id, msg(uid, "pin_wrong"))
            return
        users[uid]["pin_failed_attempts"] = 0
        save_user_profile(uid)
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
                show_crypto_deposit_address(chat_id, asset, state)
            if asset == "TL":
                state["step"] = "waiting_sent"
            return

        if flow == "withdraw":
            prepare_withdraw_amount(uid, state, amount)
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
                [(t(uid, "sent"), ucoin(uid, amount, asset)), (t(uid, "to_receive"), ucoin(uid, net, to_asset))],
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
            (t(uid, "recipient_gets"), ucoin(uid, state["net_amount"], state["asset"])),
            ("IBAN", state["iban"]), (t(uid, "recipient"), state["name"]),
        ])
        require_pin(uid, state); return
    if flow == "withdraw" and step == "address":
        valid, normalized_address, address_error = validate_wallet_address(state["asset"], text)
        if not valid:
            send(chat_id, address_error)
            return
        state["address"] = normalized_address
        state["preview"] = order_summary(t(uid, "withdraw_summary", asset=state["asset"]), [
            (t(uid, "network"), settings.get(f"network_{state['asset']}", state["asset"])),
            (t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])),
            (t(uid, "fee"), ucoin(uid, state["fee"], state["asset"])),
            (t(uid, "to_send"), ucoin(uid, state["net_amount"], state["asset"])),
            (t(uid, "wallet_address"), state["address"]),
        ], "Adres ve ağ formatı doğrulandı." if lang_of(uid) == "tr" else "Address and network format verified.")
        require_pin(uid, state); return
    if flow == "favorite_add" and step == "label":
        state["label"] = text
        state["step"] = "address"
        send(chat_id, wallet_address_prompt(uid, state["asset"]))
        return
    if flow == "favorite_add" and step == "address":
        valid, normalized_address, address_error = validate_wallet_address(state["asset"], text)
        if not valid:
            send(chat_id, address_error)
            return
        users[uid]["favorites"].append({
            "label": state["label"], "asset": state["asset"],
            "address": normalized_address, "created_at": now(),
        })
        save_user_profile(uid)
        user_state.pop(uid, None)
        send(chat_id, t(uid, "favorite_saved"), reply_keyboard(uid))
        return


def handle_callback(chat_id, username, data, cb_id):
    uid = str(chat_id)
    get_user(chat_id, username)

    if data.startswith("lang:"):
        lang = data.split(":", 1)[1]
        if lang not in ("tr", "en"):
            answer(cb_id); return
        users[uid]["language"] = lang
        save_user_profile(uid)
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
            send(chat_id, receipt_text(rid, uid), {"inline_keyboard": [[inline_button(localized_status(uid, requests_db.get(rid, {}).get("status")), f"detail:{rid}")]]})
        return
    if data.startswith("deposit_asset:"):
        asset = data.split(":", 1)[1]
        if asset not in ASSETS:
            send(chat_id, t(uid, "operation_failed"), reply_keyboard(uid))
            return
        current = user_state.get(uid, {})
        idem = current.get("idempotency_key", secrets.token_urlsafe(24))
        state = {
            "flow": "deposit",
            "step": "amount",
            "asset": asset,
            "idempotency_key": idem,
        }
        user_state[uid] = state
        if asset != "TL" and (asset in {"BTC", "LTC", "ETH", "TRX", "USDT"} or exchange_auto_deposit_ready(asset)):
            show_crypto_deposit_address(chat_id, asset, state)
            return
        send(
            chat_id,
            f"{asset}: {msg(uid, 'amount_question')}\n"
            f"{t(uid, 'minimum')}: {ucoin(uid, min_amount('deposit', asset), asset)}",
        )
        return
    if data == "deposit_sent":
        state = user_state.get(uid, {})
        if state.get("flow") != "deposit" or state.get("step") != "waiting_sent" or not state.get("amount"):
            send(chat_id, t(uid, "deposit_session_missing"), reply_keyboard(uid)); return
        if state.get("asset") == "TL":
            state["step"] = "sender_name"
            send(chat_id, t(uid, "sender_name_question"))
        elif state.get("automatic_deposit"):
            send(
                chat_id,
                "Transferiniz blockchain indexer tarafından otomatik algılanacaktır. Manuel bildirim gerekmez."
                if lang_of(uid) == "tr"
                else "Your transfer will be detected automatically by the blockchain indexer. No manual notice is required.",
                reply_keyboard(uid),
            )
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
        available = balance(uid, asset)
        if available <= 0:
            send(chat_id, msg(uid, "no_balance"))
            return
        user_state[uid] = {"flow": "withdraw", "step": "amount", "asset": asset}
        full_label = "Tüm Bakiyeyi Çek" if lang_of(uid) == "tr" else "Withdraw Full Balance"
        keyboard = {"inline_keyboard": [
            [inline_button(full_label, "withdraw_all")],
            [inline_button(t(uid, "cancel"), "cancel")],
        ]}
        send(
            chat_id,
            f"{t(uid, 'available_balance')}: {ucoin(uid, available, asset)}\n"
            f"{t(uid, 'min_withdraw')}: {ucoin(uid, min_amount('withdraw', asset), asset)}\n\n"
            f"{msg(uid, 'amount_question')}",
            keyboard,
        )
        return
    if data == "withdraw_all":
        state = user_state.get(uid, {})
        if state.get("flow") != "withdraw" or state.get("step") != "amount" or not state.get("asset"):
            send(chat_id, t(uid, "session_missing"), reply_keyboard(uid))
            return
        amount = balance(uid, state["asset"])
        if prepare_withdraw_amount(uid, state, amount):
            state["withdraw_all"] = True
        return
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
        state["preview"] = order_summary(t(uid, "swap_summary"), [(t(uid, "sent"), ucoin(uid, amount, source)), (t(uid, "to_receive"), ucoin(uid, net, target))], t(uid, "all_balance_note") + " " + live_rate_note(uid))
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
        choice = data.split(":", 1)[1]
        state = user_state.get(uid, {})
        if choice == "new":
            state["step"] = "address"
            send(chat_id, wallet_address_prompt(uid, state.get("asset")))
        else:
            try:
                fav = users[uid]["favorites"][int(choice)]
            except (ValueError, IndexError):
                send(chat_id, t(uid, "session_missing"))
                return
            valid, normalized_address, address_error = validate_wallet_address(state["asset"], fav.get("address", ""))
            if not valid:
                send(
                    chat_id,
                    ("Bu kayıtlı adres artık geçerli değil. Yeni bir adres giriniz.\n\n" if lang_of(uid) == "tr"
                     else "This saved address is no longer valid. Enter a new address.\n\n") + address_error,
                )
                state["step"] = "address"
                return
            state["address"] = normalized_address
            state["preview"] = order_summary(t(uid, "withdraw_summary", asset=state["asset"]), [
                (t(uid, "network"), settings.get(f"network_{state['asset']}", state["asset"])),
                (t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])),
                (t(uid, "fee"), ucoin(uid, state["fee"], state["asset"])),
                (t(uid, "to_send"), ucoin(uid, state["net_amount"], state["asset"])),
                (t(uid, "wallet_address"), state["address"]),
            ], "Kayıtlı adres doğrulandı." if lang_of(uid) == "tr" else "Saved address verified.")
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
            save_user_profile(uid)
        send(chat_id, t(uid, "notification_updated")); return
    if data == "security:logout_sessions":
        users[uid]["sessions"] = {"telegram": {"created_at": now(), "last_seen": now(), "active": True}}
        save_user_profile(uid); send(chat_id, t(uid, "sessions_closed")); return

def load_offset():
    return load_json(OFFSET_FILE, {"offset": None}).get("offset")


def save_offset(value):
    save_json(OFFSET_FILE, {"offset": value})


def bot_poll_once():
    global OFFSET
    if OFFSET is None:
        OFFSET = load_offset()
    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/getUpdates",
        params={"offset": OFFSET, "timeout": 25},
        timeout=35,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok", False):
        raise RuntimeError(f"Telegram getUpdates failed: {result}")
    for update in result.get("result", []):
        OFFSET = update["update_id"] + 1
        save_offset(OFFSET)
        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]
            username = message.get("from", {}).get("username", "unknown")
            text = message.get("text", "")
            ids = [
                str(entity.get("custom_emoji_id"))
                for entity in message.get("entities", [])
                if entity.get("type") == "custom_emoji" and entity.get("custom_emoji_id")
            ]
            if ids and str(chat_id) == str(ADMIN_CHAT_ID):
                send(chat_id, "\n".join(ids))
                continue
            handle_text(chat_id, username, text)
        elif "callback_query" in update:
            callback = update["callback_query"]
            handle_callback(
                callback["message"]["chat"]["id"],
                callback.get("from", {}).get("username", "unknown"),
                callback.get("data", ""),
                callback["id"],
            )


def bot_loop():
    while True:
        try:
            bot_poll_once()
        except Exception as exc:
            print("BOT LOOP ERROR:", exc)
            time.sleep(5)


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
    refresh_runtime_state(_db_key(FILES["panel_users"]), panel_users)
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
    refresh_runtime_state(_db_key(FILES["panel_users"]), panel_users, min_interval=0, force=True)
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
def home(): return f"Nerlo Wallet aktif ✅ · {BUILD_VERSION}"


@app.route("/version")
def version_info():
    return {
        "build_version": BUILD_VERSION,
        "withdraw_guard": True,
        "crypto_manual_complete_blocked": True,
        "panel_release": "PRO-PANEL-V5-FRESH",
    }


@app.route("/health/exchange")
def exchange_health():
    snapshot = exchange_health_snapshot()
    mismatch_count = int((snapshot.get("reconciliation") or {}).get("mismatch_count") or 0)
    dead_jobs = int((snapshot.get("jobs") or {}).get("dead") or 0)
    snapshot["status"] = "degraded" if mismatch_count or dead_jobs else "ok"
    return snapshot


@app.route("/internal/exchange/withdrawal", methods=["POST"])
def exchange_withdrawal_callback():
    auth_header = str(request.headers.get("Authorization") or "")
    supplied = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else str(request.headers.get("X-Exchange-Token") or "").strip()
    if not EXCHANGE_INTERNAL_TOKEN or not supplied or not secrets.compare_digest(supplied, EXCHANGE_INTERNAL_TOKEN):
        return {"ok": False, "error": "unauthorized"}, 401
    payload = request.get_json(silent=True) or {}
    rid = str(payload.get("request_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    txid = str(payload.get("txid") or payload.get("transaction_id") or "").strip()
    if not rid or status not in ("broadcast", "processing", "confirmed", "completed", "failed", "rejected"):
        return {"ok": False, "error": "invalid_payload"}, 400
    try:
        if status in ("confirmed", "completed"):
            record = exchange_finalize_withdrawal(rid, txid)
            uid = str(record.get("user_id", ""))
            if uid:
                send(uid, receipt_text(rid, uid), reply_keyboard(uid))
        elif status in ("failed", "rejected") and payload.get("refund") is True:
            record = exchange_refund_withdrawal(rid, str(payload.get("reason") or "Signer failed"))
            uid = str(record.get("user_id", ""))
            if uid:
                send(uid, f"{t(uid, 'request_rejected')}\n\n#{rid}", reply_keyboard(uid))
        else:
            changes = {"status": "processing", "signer_status": status, "updated_at": now()}
            if txid:
                changes["broadcast_txid"] = txid
            exchange_update_request(rid, changes)
        return {"ok": True, "request_id": rid}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 404
    except Exception as exc:
        print("WITHDRAW CALLBACK ERROR:", exc)
        return {"ok": False, "error": "processing_failed"}, 500

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    attempts = LOGIN_ATTEMPTS[ip]
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    if request.method == "POST":
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            return "Çok fazla başarısız giriş denemesi", 429
        panel_username, is_root = authenticate_panel_account(
            request.form.get("username", ""), request.form.get("password", "")
        )
        if panel_username:
            attempts.clear()
            session.clear()
            session["login"] = True
            session["panel_username"] = panel_username
            session["panel_root"] = is_root
            session.permanent = True
            csrf_token()
            return redirect("/admin")
        attempts.append(time.time())
        time.sleep(min(2 ** len(attempts), 8) / 10)
        error = "Kullanıcı adı veya şifre hatalı"

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta name='color-scheme' content='dark'><title>Nerlo Operations</title><style>
    :root{{--bg:#05080d;--card:#0d131c;--line:#1c2836;--text:#f6f8fb;--muted:#8190a3;--accent:#64e4ce;--accent2:#72bfff;--danger:#ff8095}}
    *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;color:var(--text);
    font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:radial-gradient(circle at 12% 12%,rgba(100,228,206,.13),transparent 28%),
    radial-gradient(circle at 88% 86%,rgba(114,191,255,.10),transparent 30%),var(--bg)}}
    .shell{{width:min(960px,100%);display:grid;grid-template-columns:1.05fr .95fr;border:1px solid var(--line);
    border-radius:28px;overflow:hidden;background:rgba(8,12,18,.92);box-shadow:0 40px 120px rgba(0,0,0,.48)}}
    .intro{{padding:54px;background:linear-gradient(145deg,rgba(100,228,206,.10),rgba(114,191,255,.03));display:flex;flex-direction:column;justify-content:space-between}}
    .brand{{display:flex;align-items:center;gap:13px}}.mark{{width:46px;height:46px;border-radius:15px;display:grid;place-items:center;
    background:linear-gradient(135deg,var(--accent),var(--accent2));color:#031014;font-size:20px;font-weight:950}}
    .brand b{{font-size:18px}}.brand span{{display:block;color:var(--muted);font-size:12px;margin-top:2px}}
    .intro h1{{font-size:38px;line-height:1.08;letter-spacing:-.045em;margin:52px 0 14px;max-width:520px}}
    .intro p{{color:#9aa8b9;line-height:1.65;max-width:510px;margin:0}}.trust{{display:flex;gap:9px;flex-wrap:wrap;margin-top:44px}}
    .trust span{{padding:8px 10px;border:1px solid rgba(255,255,255,.08);border-radius:999px;color:#a9b5c4;font-size:11px;background:rgba(255,255,255,.025)}}
    .login{{padding:54px;display:flex;flex-direction:column;justify-content:center;background:var(--card)}}.login h2{{font-size:24px;margin:0}}
    .login>p{{color:var(--muted);margin:8px 0 26px;line-height:1.5}}label{{display:block;color:#aeb9c7;font-size:11px;font-weight:750;margin:16px 0 7px}}
    input,button{{width:100%;height:48px;border-radius:13px;font:inherit}}input{{border:1px solid var(--line);background:#080d14;color:var(--text);padding:0 14px;outline:none}}
    input:focus{{border-color:var(--accent);box-shadow:0 0 0 4px rgba(100,228,206,.08)}}button{{border:0;margin-top:20px;
    background:linear-gradient(135deg,var(--accent),var(--accent2));color:#041116;font-weight:900;cursor:pointer}}
    .error{{min-height:22px;color:var(--danger);font-size:12px;margin-top:12px}}.secure{{color:#627083;font-size:10px;margin-top:18px;text-align:center}}
    @media(max-width:760px){{.shell{{grid-template-columns:1fr}}.intro{{display:none}}.login{{padding:34px 24px}}}}
    </style></head><body><main class='shell'><section class='intro'><div><div class='brand'><div class='mark'>N</div>
    <div><b>Nerlo Wallet</b><span>Exchange Operations Suite</span></div></div><h1>Finans operasyonlarını tek merkezden yönetin.</h1>
    <p>Bakiyeler, blockchain hareketleri, kullanıcılar, çekim onayları ve sistem ayarları için güvenli yönetim alanı.</p></div>
    <div class='trust'><span>PostgreSQL Ledger</span><span>Blockchain Indexer</span><span>Rol Bazlı Erişim</span></div></section>
    <form class='login' method='post'><h2>Yönetim paneli</h2><p>Yetkili hesabınızla güvenli oturum açın.</p>
    <label>Kullanıcı adı</label><input name='username' autocomplete='username' required>
    <label>Şifre</label><input type='password' autocomplete='current-password' name='password' required>
    <button>Güvenli Giriş</button><div class='error'>{h(error)}</div><div class='secure'>Oturumlar güvenli çerez ve CSRF koruması ile doğrulanır.</div>
    </form></main></body></html>"""

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
    "legacy_opening_balance": "Eski kullanılabilir bakiye aktarımı",
    "legacy_opening_pending": "Eski bekleyen bakiye aktarımı",
    "blockchain_deposit_pending": "Blockchain yatırımı bekleyen bakiye",
    "blockchain_deposit_pending_release": "Blockchain bekleyen yatırım çözümü",
    "blockchain_deposit_confirmed": "Onaylanmış blockchain yatırımı",
    "deposit_fee_revenue": "Yatırım komisyon geliri",
    "deposit_reorg_reversal": "Blockchain reorg yatırım ters kaydı",
    "deposit_fee_reorg_reversal": "Blockchain reorg komisyon ters kaydı",
    "withdrawal_broadcast_settled": "Zincirde tamamlanan çekim",
    "withdrawal_failed_pending_release": "Başarısız çekim blokesi çözümü",
    "withdrawal_failed_refund": "Başarısız çekim iadesi",
    "withdraw_fee_revenue": "Çekim komisyon geliri",
    "convert_fee_revenue": "Dönüşüm komisyon geliri",
}

BUCKET_LABELS = {
    "available": "Kullanılabilir bakiye",
    "pending": "Bekleyen bakiye",
    "locked": "Kilitli bakiye",
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
    "XMR": "XMR",
    "BTC": "BTC",
    "ETH": "ETH",
    "TON": "TON",
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
        "wallet_XMR": "XMR yatırma adresi",
        "wallet_LTC": "LTC yatırma adresi",
        "wallet_BTC": "BTC çekim cüzdanı",
        "wallet_ETH": "ETH çekim cüzdanı",
        "wallet_TON": "TON çekim cüzdanı",
        "maintenance_mode": "Bakım durumu",
        "maintenance_message": "Bakım mesajı",
        "announcement_active": "Duyuru durumu",
        "announcement_text": "Duyuru metni",
        "network_USDT": "USDT ağı",
        "network_TRX": "TRX ağı",
        "network_XMR": "XMR ağı",
        "network_LTC": "LTC ağı",
        "network_BTC": "BTC ağı",
        "network_ETH": "ETH ağı",
        "network_TON": "TON ağı",
    }
    if key in direct:
        return direct[key]
    match = re.fullmatch(rf"rate_({'|'.join(CRYPTO_ASSETS)})_TL", key)
    if match:
        return f"{match.group(1)} / TL kuru"
    match = re.fullmatch(rf"fee_(deposit|withdraw)_({ASSET_PATTERN})_percent", key)
    if match:
        operation = "yükleme" if match.group(1) == "deposit" else "çekim"
        return f"{match.group(2)} {operation} komisyonu (%)"
    pair_match = re.fullmatch(rf"fee_convert_({ASSET_PATTERN})_({ASSET_PATTERN})_percent", key)
    if pair_match:
        return f"{pair_match.group(1)} → {pair_match.group(2)} dönüşüm komisyonu (%)"
    if key == "fee_convert_tl_percent":
        return "Eski TL içeren dönüşüm oranı (yedek)"
    if key == "fee_convert_crypto_percent":
        return "Eski kripto dönüşüm oranı (yedek)"
    match = re.fullmatch(rf"min_(deposit|withdraw|convert)_({ASSET_PATTERN})", key)
    if match:
        operation = {"deposit": "yükleme", "withdraw": "çekim", "convert": "dönüştürme"}[match.group(1)]
        return f"{match.group(2)} en düşük {operation} tutarı"
    match = re.fullmatch(rf"daily_withdraw_limit_({ASSET_PATTERN})", key)
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
    if key.startswith("fee_"):
        return f"<div class='field'><label>{label}</label><input type='number' min='0' max='99.9999' step='0.0001' name='{h(key)}' value='{h(value)}'></div>"

    return f"<div class='field'><label>{label}</label><input name='{h(key)}' value='{h(value)}'></div>"


def message_field(key):
    return f"<div class='field'><label>{h(MESSAGE_LABELS.get(key, 'Bot mesajı'))}</label><textarea name='{h(key)}'>{h(messages.get(key, ''))}</textarea></div>"


def reserve_totals():
    totals = {asset: Decimal("0") for asset in ASSETS}
    pending = {asset: Decimal("0") for asset in ASSETS}
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asset,COALESCE(SUM(available),0) FROM exchange_accounts WHERE user_id<>'__platform__' GROUP BY asset"
            )
            for asset, value in cur.fetchall():
                if asset in totals:
                    totals[asset] = D(value)
            cur.execute(
                """
                SELECT payload->>'asset',COALESCE(SUM(NULLIF(payload->>'amount','')::numeric),0)
                FROM exchange_requests
                WHERE request_type='withdraw' AND status IN ('pending','processing')
                GROUP BY payload->>'asset'
                """
            )
            for asset, value in cur.fetchall():
                if asset in pending:
                    pending[asset] = D(value)
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
    status = str(r.get("status") or "pending")
    request_type = str(r.get("type") or "")
    asset = str(r.get("asset") or r.get("to_asset") or "")
    network = str(r.get("network") or settings.get(f"network_{asset}", asset) or asset)
    created = str(r.get("created_at") or "-")
    target = str(r.get("iban") or r.get("address") or "")
    txid = str(r.get("broadcast_txid") or r.get("txid") or "")

    if request_type == "deposit":
        primary_label = "Bakiyeye geçecek"
        primary_value = fmt(r.get("net_amount"), asset)
        secondary = [
            ("Brüt tutar", fmt(r.get("amount"), asset)),
            ("Komisyon", fmt(r.get("fee"), asset)),
            ("Ağ", network),
        ]
        if asset == "TL":
            secondary.extend([("Gönderen", r.get("sender_name", "-")), ("Referans", r.get("tx_note", "-"))])
    elif request_type == "withdraw":
        primary_label = "Alıcıya gönderilecek"
        primary_value = fmt(r.get("net_amount"), asset)
        secondary = [
            ("Talep tutarı", fmt(r.get("amount"), asset)),
            ("Komisyon", fmt(r.get("fee"), asset)),
            ("Ağ", network),
            ("Hedef", target or "-"),
        ]
    else:
        primary_label = "Alınacak"
        primary_value = fmt(r.get("net_to_amount"), r.get("to_asset"))
        secondary = [
            ("Gönderilen", fmt(r.get("from_amount"), r.get("from_asset"))),
            ("Komisyon", fmt(r.get("fee"), r.get("to_asset"))),
            ("Parite", f"{r.get('from_asset', '-')} → {r.get('to_asset', '-')}"),
        ]

    if txid:
        secondary.append(("TXID", txid))
    details = "".join(
        f"<div class='request-detail'><span>{h(label)}</span><b title='{h(value)}'>{h(value)}</b></div>"
        for label, value in secondary
    )

    actions = ""
    if status in ("pending", "processing") and not r.get("automatic"):
        is_crypto_withdraw = _is_crypto_withdraw_record(r)
        if is_crypto_withdraw and r.get("broadcast_locked"):
            signer_state = h(r.get("signer_status") or "işleniyor")
            actions = f"<div class='request-warning'><b>Blockchain gönderimi başlatıldı</b><span>Durum: {signer_state}. Ağ sonucu bekleniyor.</span></div>"
        else:
            process_button = "" if status == "processing" else "<button class='btn ghost' name='action' value='process_request'>İşleme Al</button>"
            approve_button = "" if is_crypto_withdraw else "<button class='btn success' name='action' value='approve_request'>Tamamla</button>"
            if is_crypto_withdraw and r.get("signer_enabled"):
                warning = "<div class='request-warning safe'><b>Otomatik çekim hazır</b><span>İşleme Al dediğinizde transfer blockchain'e gönderilir.</span></div>"
            elif is_crypto_withdraw:
                warning = "<div class='request-warning'><b>Gönderim servisi bağlı değil</b><span>Bu çekim para gönderilmeden tamamlanamaz.</span></div>"
            else:
                warning = ""
            actions = (
                f"{warning}<form method='post' class='request-actions'>"
                f"<input type='hidden' name='rid' value='{h(rid)}'>"
                f"<input type='hidden' name='return_to' value='/admin?view=requests'>"
                f"{process_button}{approve_button}"
                f"<button class='btn danger' name='action' value='reject_request'>Reddet</button>"
                f"</form>"
            )

    return (
        f"<article class='request-item request-{h(request_type)}'>"
        f"<div class='request-title'>"
        f"<div class='request-ident'><span class='request-type'>{h(request_type_label(request_type))}</span>"
        f"<h3>#{h(rid)}</h3><p>@{h(username)} · {h(uid)} · {h(created)}</p></div>"
        f"<span class='status {request_status_class(status)}'>{h(status_label(status))}</span></div>"
        f"<div class='request-primary'><span>{h(primary_label)}</span><strong>{h(primary_value)}</strong></div>"
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


def exchange_user_ledger_rows(user_id, limit=50):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at,entry_type,amount,asset,balance_after,metadata,bucket,reference_id
                FROM exchange_ledger
                WHERE user_id=%s
                ORDER BY id DESC
                LIMIT %s
                """,
                (str(user_id), int(limit)),
            )
            rows = cur.fetchall()
    return [
        {
            "created_at": row[0].strftime("%Y-%m-%d %H:%M:%S") if hasattr(row[0], "strftime") else str(row[0]),
            "kind": row[1], "amount": str(row[2]), "asset": row[3],
            "available_after": str(row[4]), "note": str((row[5] or {}).get("note") or ""),
            "bucket": row[6], "ref_id": row[7],
        }
        for row in rows
    ]


def user_balance_cards(uid, u):
    names = {"TL": "Türk Lirası", "USDT": "Tether", "LTC": "Litecoin", "TRX": "TRON",
             "XMR": "Monero", "BTC": "Bitcoin", "ETH": "Ethereum", "TON": "Toncoin"}
    cards = []
    for asset in ASSETS:
        available = fmt(u.get("balances", {}).get(asset, "0"), asset)
        pending_value = fmt(u.get("pending_balances", {}).get(asset, "0"), asset)
        cards.append(
            f"<div class='mini-balance'><div class='mini-asset'><span>{h(asset)}</span>"
            f"<small>{h(names.get(asset, asset))}</small></div><strong>{h(available)}</strong>"
            f"<div class='mini-pending'>Bekleyen · {h(pending_value)}</div></div>"
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
    txs = exchange_user_ledger_rows(uid, 12)
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
    refresh_request_cache()
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
    uid = request.args.get("uid", "")
    profile = exchange_load_profile(uid) if uid else None
    if profile is not None:
        users[str(uid)] = profile
        exchange_refresh_user_cache(uid)
    refresh_request_cache()
    return render_user_management(uid)


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
    refresh_all_user_profiles()
    refresh_request_cache()
    refresh_runtime_state(_db_key(FILES["settings"]), settings, min_interval=0, force=True)
    refresh_runtime_state(_db_key(FILES["messages"]), messages, min_interval=0, force=True)
    if request.method == "POST":
        action = request.form.get("action", "")
        # Savunma katmanı: eski panel HTML'i veya elle hazırlanmış POST isteği bile
        # kripto çekimini TXID olmadan tamamlayamaz.
        if action == "approve_request":
            requested_rid = str(request.form.get("rid", "")).strip()
            requested_record = exchange_get_request(requested_rid) or requests_db.get(requested_rid, {})
            if _is_crypto_withdraw_record(requested_record):
                set_admin_notice("GÜVENLİK ENGELİ: Kripto çekimi gerçek TXID olmadan tamamlanamaz.", "error")
                return redirect(safe_admin_return())
        required_permission = ACTION_PERMISSIONS.get(action)
        if not required_permission:
            abort(400)
        if not has_panel_permission(required_permission):
            abort(403)
        if action == "settings":
            validation_error = ""
            pending_settings = {}
            for key in EDITABLE_SETTING_KEYS:
                raw_value = request.form.get(key, settings.get(key, ""))
                if key.startswith("fee_"):
                    percentage = D(raw_value, "-1")
                    if percentage < 0 or percentage >= 100:
                        validation_error = f"{setting_label(key)} için 0 ile 100 arasında bir değer girin."
                        break
                    raw_value = format(percentage, "f")
                pending_settings[key] = raw_value

            if validation_error:
                set_admin_notice(validation_error, "error")
            else:
                settings.update(pending_settings)
                for key in DEFAULT_MESSAGES:
                    messages[key] = request.form.get(key, messages.get(key, ""))
                save_json(FILES["settings"], settings)
                save_json(FILES["messages"], messages)
                add_admin_log("settings", "Ayrıntılı komisyon ve sistem ayarları güncellendi")
                set_admin_notice("Ayarlar güvenli şekilde kaydedildi.")
        elif action in ("process_request", "approve_request", "reject_request"):
            rid = request.form.get("rid", "")
            try:
                current_record = exchange_get_request(rid) or requests_db.get(str(rid), {})
                if action == "approve_request" and _is_crypto_withdraw_record(current_record):
                    raise ValueError("GÜVENLİK ENGELİ: Kripto çekimi TXID olmadan tamamlanamaz")
                updated = exchange_admin_request_transition(rid, action)
                uid = updated["user_id"]
                if action == "process_request":
                    if updated.get("signer_status") == "queued":
                        set_admin_notice(f"#{rid} otomatik gönderim sırasına alındı.")
                    else:
                        set_admin_notice(f"#{rid} işleme alındı.")
                elif action == "approve_request":
                    send(uid, receipt_text(rid, uid), reply_keyboard(uid))
                    set_admin_notice(f"#{rid} tamamlandı.")
                else:
                    send(uid, f"{t(uid, 'request_rejected')}\n\n#{rid}", reply_keyboard(uid))
                    set_admin_notice(f"#{rid} reddedildi.")
                add_admin_log(action, f"#{rid}", uid)
            except ValueError as exc:
                set_admin_notice(str(exc), "error")
            except Exception as exc:
                print("ADMIN REQUEST TRANSITION ERROR:", exc)
                set_admin_notice("İşlem güvenli biçimde tamamlanamadı.", "error")
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
                save_user_profile(uid)
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
                save_user_profile(uid); add_admin_log(action, "Kullanıcı güvenlik durumu değiştirildi", uid)
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
        ("fees", "Komisyonlar", [k for k in EDITABLE_SETTING_KEYS if k.startswith("fee_") and k not in ("fee_convert_tl_percent", "fee_convert_crypto_percent")]),
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

    dashboard_section = f"""<section class='page-view {'active' if active_view == 'dashboard' else ''}' data-view='dashboard'><div class='section-head'><div><span class='eyebrow'>CANLI FİNANS ÖZETİ</span><h2>Varlık ve Operasyon Merkezi</h2><p>Kullanıcı varlıkları, bekleyen çekimler ve günlük işlem akışı.</p></div><span class='pill'>Ledger eşleşmesi aktif</span></div><div class='summary-grid'><div class='summary-card'><span>Toplam kullanıcı</span><strong>{len(users)}</strong></div><div class='summary-card'><span>Aksiyon bekleyen</span><strong>{pending_count}</strong></div><div class='summary-card'><span>Bugün tamamlanan</span><strong>{completed_today}</strong></div></div><div class='dashboard-grid' style='margin-top:12px'>{asset_metrics}</div></section>""" if "dashboard" in allowed_views else ""
    requests_section = f"""<section class='page-view {'active' if active_view == 'requests' else ''}' data-view='requests'><div class='section-head'><div><span class='eyebrow'>ONAY VE İŞLEM AKIŞI</span><h2>İşlem Merkezi</h2><p>Her talebi tutar, ağ, hedef ve durum bilgileriyle hızlıca yönetin.</p></div></div><div class='panel-card'><form id='request-filter' class='toolbar'><input name='rq' value='{h(request_query)}' placeholder='İşlem no, kullanıcı ID veya kullanıcı adı'><select name='status'><option value='all'>Tüm durumlar</option>{''.join(f"<option value='{s}' {'selected' if status_filter == s else ''}>{status_label(s)}</option>" for s in ['pending','processing','completed','rejected'])}</select><select name='type'><option value='all'>Tüm işlem türleri</option>{''.join(f"<option value='{t}' {'selected' if type_filter == t else ''}>{request_type_label(t)}</option>" for t in ['deposit','withdraw','convert'])}</select><button class='btn primary'>Filtrele</button></form><div id='request-list' class='request-list'>{render_request_list(request_query, status_filter, type_filter)}</div></div></section>""" if "requests" in allowed_views else ""
    users_section = f"""<section class='page-view {'active' if active_view == 'users' else ''}' data-view='users'><div class='section-head'><div><span class='eyebrow'>KULLANICI YÖNETİMİ</span><h2>ID ile Kullanıcı Aç</h2><p>Kullanıcı satırlarına tıklamadan doğrudan kimlik ile yönetin</p></div></div><div class='panel-card'><form id='user-lookup' class='lookup-bar'><input id='manage-user-id' name='uid' value='{h(manage_user_id)}' inputmode='numeric' placeholder='Telegram kullanıcı ID'><button class='btn primary'>Kullanıcıyı Getir</button></form><div id='user-management-result'>{render_user_management(manage_user_id)}</div></div><div class='panel-card' style='margin-top:10px'><div class='section-head'><div><span class='eyebrow'>SON KULLANICILAR</span><h3>Hızlı Referans</h3></div><p>ID değerleri bağlantı değildir</p></div><div class='table-wrap'><table><thead><tr><th>Kullanıcı ID</th><th>Kullanıcı adı</th><th>TL</th><th>USDT</th><th>LTC</th><th>TRX</th><th>Hesap</th><th>Son görülme</th></tr></thead><tbody>{user_rows}</tbody></table></div></div></section>""" if "users" in allowed_views else ""
    broadcast_section = f"""<section class='page-view {'active' if active_view == 'broadcast' else ''}' data-view='broadcast'><div class='section-head'><div><span class='eyebrow'>İLETİŞİM</span><h2>Duyuru Gönder</h2><p>Bildirimleri açık kullanıcılara toplu mesaj gönderin</p></div></div><div class='broadcast-grid'><form method='post' class='panel-card'><input type='hidden' name='action' value='broadcast'><input type='hidden' name='return_to' value='/admin?view=broadcast'><label>Duyuru metni</label><textarea name='announcement_text' placeholder='Kullanıcılara gönderilecek mesajı yazın' required></textarea><button class='btn primary' style='width:100%;margin-top:10px'>Duyuruyu Gönder</button></form><div class='broadcast-note'><b style='color:#dce5ef'>Gönderim bilgisi</b><br><br>Duyuru yalnızca duyuru bildirimleri açık olan kullanıcılara iletilir. Gönderim sonucu yönetim kayıtlarına eklenir.</div></div></section>""" if "broadcast" in allowed_views else ""
    settings_section = f"""<section class='page-view {'active' if active_view == 'settings' else ''}' data-view='settings'><div class='section-head'><div><span class='eyebrow'>SİSTEM</span><h2>Ayar Yönetimi</h2><p>Kur, limit, cüzdan, sistem ve bot mesajlarını yönetin</p></div></div><form method='post' class='panel-card'><input type='hidden' name='action' value='settings'><input type='hidden' name='return_to' value='/admin?view=settings'><div class='settings-nav'>{settings_tabs}</div>{''.join(settings_panes)}<div class='save-bar'><button class='btn primary'>Tüm Ayarları Kaydet</button></div></form></section>""" if "settings" in allowed_views else ""
    logs_section = f"""<section class='page-view {'active' if active_view == 'logs' else ''}' data-view='logs'><div class='section-head'><div><span class='eyebrow'>DENETİM</span><h2>Yönetim Kayıtları</h2><p>Son 120 yönetici işlemi</p></div></div><div class='panel-card'><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Kullanıcı</th><th>Detay</th></tr></thead><tbody>{logs}</tbody></table></div></div></section>""" if "logs" in allowed_views else ""
    admins_section = f"""<section class='page-view {'active' if active_view == 'admins' else ''}' data-view='admins'><div class='section-head'><div><span class='eyebrow'>ERİŞİM YÖNETİMİ</span><h2>Panel Yetkilileri</h2><p>Yeni panel kullanıcısı oluşturun ve bölüm bazlı yetkilerini düzenleyin</p></div></div>{render_panel_user_management()}</section>""" if "admins" in allowed_views else ""

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'><title>Nerlo Wallet Yönetim</title><style>
    :root{{--bg:#090c12;--sidebar:#0b0f16;--surface:#10151e;--surface-2:#141b25;--surface-3:#0c1118;--line:#222b38;--line-soft:#1a2230;--text:#f4f7fb;--muted:#8c98a8;--muted-2:#667386;--accent:#68e0d2;--accent-2:#7cc7ff;--success:#59d99b;--warning:#f6c96b;--danger:#ff7489;--radius:18px}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 85% -10%,rgba(104,224,210,.08),transparent 32%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}}button,input,select,textarea{{font:inherit}}button{{cursor:pointer}}.app-shell{{min-height:100vh;display:grid;grid-template-columns:238px minmax(0,1fr)}}.sidebar{{position:sticky;top:0;height:100vh;background:rgba(11,15,22,.96);border-right:1px solid var(--line-soft);padding:20px 14px;display:flex;flex-direction:column;backdrop-filter:blur(18px)}}.brand{{display:flex;align-items:center;gap:11px;padding:6px 8px 22px}}.brand-mark{{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116;font-weight:950;font-size:18px;box-shadow:0 10px 30px rgba(104,224,210,.14)}}.brand strong{{display:block;font-size:15px}}.brand small{{display:block;color:var(--muted);margin-top:2px;font-size:11px}}.nav{{display:grid;gap:4px}}.nav-item{{width:100%;border:0;background:transparent;color:#aeb8c6;display:flex;align-items:center;gap:10px;padding:10px 11px;border-radius:11px;text-align:left;font-weight:680}}.nav-item span{{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;background:#111923;color:#66778c;font-size:10px}}.nav-item:hover{{background:#111822;color:#fff}}.nav-item.active{{background:#151e29;color:#fff}}.nav-item.active span{{background:rgba(104,224,210,.13);color:var(--accent)}}.sidebar-foot{{margin-top:auto;padding:14px 8px 2px;border-top:1px solid var(--line-soft)}}.version{{display:block;color:var(--muted-2);font-size:10px;margin-bottom:10px;letter-spacing:.06em}}.logout{{color:#aeb8c6;text-decoration:none;font-size:12px}}.main{{min-width:0;padding:22px clamp(16px,3vw,36px) 40px}}.topbar{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}}.topbar h1{{font-size:22px;margin:0;letter-spacing:-.03em}}.topbar p{{margin:5px 0 0;color:var(--muted);font-size:12px}}.top-pill{{padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--surface-3);font-size:11px}}.page-view{{display:none}}.page-view.active{{display:block}}.section-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px}}.section-head h2,.section-head h3{{margin:2px 0 0;letter-spacing:-.025em}}.section-head h2{{font-size:18px}}.section-head h3{{font-size:15px}}.section-head p{{margin:3px 0 0;color:var(--muted);font-size:12px}}.eyebrow{{display:block;color:var(--muted-2);font-size:9px;font-weight:850;letter-spacing:.13em}}.panel-card{{background:rgba(16,21,30,.92);border:1px solid var(--line);border-radius:var(--radius);padding:17px;box-shadow:0 16px 50px rgba(0,0,0,.14)}}.compact-card{{padding:15px}}.dashboard-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.wallet-metric{{min-height:108px;background:linear-gradient(145deg,#111823,#0d131c);border:1px solid var(--line);border-radius:16px;padding:14px;display:flex;gap:11px;align-items:flex-start}}.asset-dot{{width:30px;height:30px;flex:0 0 auto;border-radius:10px;background:rgba(104,224,210,.1);color:var(--accent);display:grid;place-items:center;font-size:11px;font-weight:900}}.wallet-metric span{{display:block;color:var(--muted);font-size:10px}}.wallet-metric strong{{display:block;font-size:18px;margin:5px 0 3px;letter-spacing:-.03em;white-space:nowrap}}.wallet-metric small{{color:var(--muted-2);font-size:10px}}.summary-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px}}.summary-card{{padding:13px 14px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3)}}.summary-card span{{color:var(--muted);font-size:10px}}.summary-card strong{{display:block;font-size:20px;margin-top:4px}}.toolbar{{display:grid;grid-template-columns:minmax(220px,2fr) repeat(2,minmax(150px,1fr)) auto;gap:9px;margin-bottom:13px}}input,select,textarea{{width:100%;border:1px solid var(--line);background:#0b1017;color:var(--text);border-radius:11px;min-height:41px;padding:9px 11px;outline:none}}input:focus,select:focus,textarea:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(104,224,210,.08)}}textarea{{min-height:110px;resize:vertical}}label{{display:block;color:#aab5c4;font-size:10px;font-weight:760;margin:0 0 6px}}.btn{{border:1px solid transparent;min-height:38px;border-radius:10px;padding:8px 12px;font-weight:800;background:#192431;color:#dfe8f3}}.btn.primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#061116}}.btn.ghost{{background:#111923;border-color:var(--line);color:#c9d3df}}.btn.success{{background:rgba(89,217,155,.13);border-color:rgba(89,217,155,.23);color:#88ebba}}.btn.danger{{background:rgba(255,116,137,.12);border-color:rgba(255,116,137,.22);color:#ff96a6}}.request-list{{display:grid;gap:9px}}.request-item{{background:var(--surface-3);border:1px solid var(--line);border-radius:15px;padding:13px}}.request-title{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.request-title h3{{font-size:14px;margin:3px 0 0}}.request-details{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:11px}}.request-detail{{min-width:0;background:#101721;border:1px solid var(--line-soft);border-radius:10px;padding:8px 9px}}.request-detail span{{display:block;color:var(--muted-2);font-size:9px;margin-bottom:4px}}.request-detail b{{display:block;font-size:11px;overflow-wrap:anywhere}}.request-warning{{margin-top:10px;padding:9px 10px;border:1px solid rgba(246,201,107,.25);border-radius:10px;background:rgba(246,201,107,.08);color:var(--warning);font-size:10px;line-height:1.45}}.request-actions{{display:flex;justify-content:flex-end;gap:7px;margin-top:10px}}.request-actions .btn{{width:auto;min-height:34px;font-size:11px}}.status{{display:inline-flex;align-items:center;justify-content:center;min-height:25px;padding:4px 8px;border-radius:999px;font-size:9px;font-weight:850;white-space:nowrap}}.status.waiting{{background:rgba(246,201,107,.12);color:var(--warning)}}.status.working{{background:rgba(124,199,255,.12);color:var(--accent-2)}}.status.done{{background:rgba(89,217,155,.12);color:var(--success)}}.status.declined{{background:rgba(255,116,137,.12);color:var(--danger)}}.empty-state{{min-height:140px;border:1px dashed #2a3442;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:var(--muted);gap:5px;padding:20px}}.empty-state b{{color:#dbe4ee}}.error-state{{border-color:rgba(255,116,137,.3)}}.lookup-bar{{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:9px;margin-bottom:12px}}.lookup-bar .btn{{min-width:130px}}.user-profile-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin:2px 0 13px}}.user-profile-head h2{{font-size:19px;margin:3px 0}}.user-profile-head p{{margin:0;color:var(--muted);font-size:11px}}.profile-badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.pill{{padding:6px 9px;border-radius:999px;background:#151e29;border:1px solid var(--line);color:#cbd5e1;font-size:9px;font-weight:800}}.danger-pill{{color:var(--danger);background:rgba(255,116,137,.08)}}.mini-balance-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:10px}}.mini-balance{{background:#0d141d;border:1px solid var(--line);border-radius:13px;padding:11px}}.mini-balance div{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.mini-balance span{{font-size:10px;color:var(--muted)}}.mini-balance strong{{font-size:13px;white-space:nowrap}}.mini-balance small{{display:block;color:var(--muted-2);font-size:9px;margin-top:7px}}.user-workspace{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}.history-grid{{align-items:start}}.form-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-items:end}}.form-grid .wide{{grid-column:span 2}}.submit-cell{{display:flex;align-items:flex-end}}.submit-cell .btn{{width:100%}}.balance-form{{grid-template-columns:repeat(3,minmax(0,1fr))}}.security-actions{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.security-actions .btn{{width:100%}}.table-wrap{{overflow:auto;border:1px solid var(--line-soft);border-radius:12px}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line-soft);font-size:10px;white-space:nowrap}}th{{color:var(--muted-2);font-size:9px;letter-spacing:.04em;background:#0c121a}}td{{color:#cbd5df}}tbody tr:last-child td{{border-bottom:0}}code{{color:#c8d5e4;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px}}.muted-cell{{text-align:center;color:var(--muted)}}.broadcast-grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:10px}}.broadcast-note{{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-3);color:var(--muted);font-size:12px;line-height:1.55}}.settings-nav{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}.setting-tab{{border:1px solid var(--line);background:#0c1219;color:#95a2b2;padding:8px 10px;border-radius:9px;font-size:10px;font-weight:800}}.setting-tab.active{{background:#17222d;color:var(--accent);border-color:#29404a}}.setting-pane{{display:none}}.setting-pane.active{{display:block}}.settings-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}}.field{{min-width:0}}.settings-grid textarea{{min-height:90px}}.save-bar{{display:flex;justify-content:flex-end;margin-top:13px}}.save-bar .btn{{min-width:180px}}.admin-account-list{{display:grid;gap:10px;margin-top:10px}}.admin-account{{background:var(--surface-3);border:1px solid var(--line);border-radius:15px;padding:15px}}.root-account{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}.admin-account h3{{margin:3px 0;font-size:15px}}.admin-account p{{margin:0;color:var(--muted);font-size:10px}}.admin-account-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}}.admin-account-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.permission-title{{margin-top:13px}}.permission-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}}.permission-option{{display:flex;align-items:center;gap:8px;margin:0;padding:9px 10px;border:1px solid var(--line-soft);border-radius:10px;background:#0d141d;color:#c4cfdb;font-size:10px;cursor:pointer}}.permission-option input{{width:auto;min-height:0;margin:0;accent-color:var(--accent)}}.admin-account-actions{{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}}.toast{{position:fixed;right:22px;top:18px;z-index:20;max-width:min(360px,calc(100vw - 32px));padding:11px 14px;border:1px solid rgba(89,217,155,.25);background:#10231c;color:#9aebc0;border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.35);font-size:12px}}.toast-error{{background:#28131a;color:#ff9bad;border-color:rgba(255,116,137,.28)}}@media(max-width:1180px){{.permission-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.dashboard-grid,.mini-balance-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.request-details{{grid-template-columns:repeat(2,minmax(0,1fr))}}.settings-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:880px){{.app-shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;padding:12px}}.brand{{padding-bottom:12px}}.nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.nav-item{{justify-content:center;font-size:11px}}.sidebar-foot{{display:none}}.main{{padding-top:14px}}.user-workspace,.broadcast-grid{{grid-template-columns:1fr}}.toolbar{{grid-template-columns:1fr 1fr}}.toolbar input{{grid-column:1/-1}}.security-actions{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{.admin-account-grid,.permission-grid{{grid-template-columns:1fr}}.admin-account-actions{{display:grid}}.root-account,.admin-account-head{{display:block}}.root-account .pill,.admin-account-head .pill{{display:inline-flex;margin-top:9px}}.topbar{{align-items:flex-start}}.top-pill{{display:none}}.dashboard-grid,.summary-grid,.mini-balance-grid{{grid-template-columns:1fr}}.nav{{grid-template-columns:repeat(2,minmax(0,1fr))}}.nav-item span{{display:none}}.toolbar,.lookup-bar,.settings-grid,.form-grid,.balance-form{{grid-template-columns:1fr}}.form-grid .wide{{grid-column:auto}}.request-details{{grid-template-columns:1fr}}.request-actions{{display:grid;grid-template-columns:1fr}}.request-actions .btn{{width:100%}}.user-profile-head{{display:block}}.profile-badges{{justify-content:flex-start;margin-top:10px}}.security-actions{{grid-template-columns:1fr}}}}
    
    /* Professional Operations UI v5 fresh build */
    :root{{--bg:#06090e;--sidebar:#090d13;--surface:#0d131c;--surface-2:#111925;--surface-3:#080d14;
    --line:#1b2735;--line-soft:#141e2a;--text:#f5f7fa;--muted:#8998aa;--muted-2:#5e6c7d;
    --accent:#63e2cb;--accent-2:#72bfff;--success:#62d99e;--warning:#f1c96d;--danger:#ff7e94;--radius:20px}}
    body{{background:radial-gradient(circle at 82% -8%,rgba(99,226,203,.08),transparent 28%),
    linear-gradient(180deg,#070a10 0%,#05080c 100%);font-size:14px}}
    .app-shell{{grid-template-columns:260px minmax(0,1fr)}}
    .sidebar{{padding:22px 16px;background:rgba(8,12,18,.97);border-right:1px solid rgba(255,255,255,.055)}}
    .brand{{padding:4px 8px 25px}}.brand-mark{{width:42px;height:42px;border-radius:14px;box-shadow:none}}
    .brand strong{{font-size:16px;letter-spacing:-.02em}}.brand small{{font-size:10px;letter-spacing:.05em;text-transform:uppercase}}
    .nav{{gap:6px}}.nav-item{{min-height:44px;border:1px solid transparent;border-radius:13px;padding:9px 11px;color:#98a6b7;font-weight:700}}
    .nav-item span{{width:28px;height:28px;border-radius:9px;background:#0d141e;color:#627286}}
    .nav-item:hover{{background:#0e151f;border-color:#182433}}.nav-item.active{{background:linear-gradient(135deg,rgba(99,226,203,.12),rgba(114,191,255,.06));
    border-color:rgba(99,226,203,.17);color:#f6f9fb}}.nav-item.active span{{background:rgba(99,226,203,.15);color:var(--accent)}}
    .sidebar-foot{{border-color:rgba(255,255,255,.055)}}.logout{{display:inline-flex;padding:8px 0;color:#aab5c2}}
    .main{{padding:28px clamp(20px,3.2vw,46px) 48px;max-width:1680px;width:100%}}
    .topbar{{margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid rgba(255,255,255,.055)}}
    .topbar h1{{font-size:25px}}.topbar p{{font-size:12px}}.top-pill{{display:flex;align-items:center;gap:8px;padding:9px 12px;background:#0a1018}}
    .live-dot{{width:7px;height:7px;border-radius:50%;background:var(--success);box-shadow:0 0 0 5px rgba(98,217,158,.09)}}
    .section-head{{margin-bottom:17px}}.section-head h2{{font-size:21px}}.section-head h3{{font-size:16px}}
    .eyebrow{{color:#6f8092;font-size:9px;letter-spacing:.16em}}.panel-card{{background:linear-gradient(180deg,rgba(15,22,32,.96),rgba(10,16,24,.96));
    border-color:rgba(255,255,255,.07);box-shadow:0 22px 60px rgba(0,0,0,.16);padding:20px}}
    .dashboard-grid{{grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.wallet-metric{{min-height:124px;padding:17px;border-radius:18px;
    background:linear-gradient(145deg,#0f1722,#0a1018);border-color:rgba(255,255,255,.07)}}
    .asset-dot{{width:36px;height:36px;border-radius:11px;font-size:12px}}.wallet-metric span{{font-size:10px;text-transform:uppercase;letter-spacing:.08em}}
    .wallet-metric strong{{font-size:20px;margin-top:8px}}.wallet-metric small{{display:block;margin-top:7px}}
    .summary-grid{{gap:12px;margin-top:12px}}.summary-card{{padding:17px 18px;border-radius:16px;background:#0a1018;border-color:rgba(255,255,255,.065)}}
    .summary-card span{{font-size:10px;text-transform:uppercase;letter-spacing:.08em}}.summary-card strong{{font-size:25px}}
    input,select,textarea{{min-height:44px;border-radius:12px;border-color:#1d2a39;background:#080d14;padding:10px 12px}}
    label{{font-size:10px;letter-spacing:.04em}}.btn{{min-height:40px;border-radius:11px;padding:9px 14px;transition:.18s ease}}
    .btn:hover{{transform:translateY(-1px);filter:brightness(1.06)}}.btn.primary{{box-shadow:0 12px 26px rgba(99,226,203,.10)}}
    .toolbar{{padding:4px;gap:10px;margin-bottom:17px}}.request-list{{gap:12px}}
    .request-item{{position:relative;overflow:hidden;padding:18px;border-radius:18px;background:linear-gradient(160deg,#0d141e,#080d14);
    border-color:rgba(255,255,255,.075)}}.request-item:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#334255}}
    .request-deposit:before{{background:var(--success)}}.request-withdraw:before{{background:var(--accent-2)}}.request-convert:before{{background:var(--accent)}}
    .request-title{{align-items:center}}.request-ident{{min-width:0}}.request-type{{display:inline-block;color:var(--accent);font-size:9px;font-weight:850;
    letter-spacing:.12em;text-transform:uppercase;margin-bottom:5px}}.request-title h3{{font-size:15px;margin:0}}.request-title p{{margin:5px 0 0;color:var(--muted-2);font-size:10px}}
    .request-primary{{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:16px 0 12px;padding:15px 16px;
    border:1px solid rgba(255,255,255,.06);border-radius:14px;background:rgba(255,255,255,.022)}}
    .request-primary span{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}}.request-primary strong{{font-size:22px;letter-spacing:-.035em}}
    .request-details{{grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:0}}.request-detail{{padding:10px 11px;border-radius:11px;
    background:#0b1119;border-color:rgba(255,255,255,.05)}}.request-detail span{{font-size:9px;text-transform:uppercase;letter-spacing:.07em}}
    .request-detail b{{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.request-warning{{display:flex;justify-content:space-between;gap:12px;align-items:center;
    padding:11px 12px;border-radius:12px}}.request-warning b{{font-size:11px}}.request-warning span{{font-size:10px}}.request-warning.safe{{border-color:rgba(98,217,158,.22);
    background:rgba(98,217,158,.07);color:#8ce8b9}}.request-actions{{padding-top:12px;border-top:1px solid rgba(255,255,255,.05)}}
    .status{{min-height:28px;padding:5px 10px;font-size:9px;letter-spacing:.04em}}
    .mini-balance-grid{{gap:10px}}.mini-balance{{padding:13px;border-radius:14px;background:#0a1018;border-color:rgba(255,255,255,.06)}}
    .mini-balance .mini-asset{{display:flex;align-items:baseline;justify-content:flex-start;gap:7px}}.mini-balance .mini-asset span{{color:var(--accent);font-weight:900}}
    .mini-balance .mini-asset small{{margin:0;color:var(--muted-2)}}.mini-balance strong{{display:block;font-size:15px;margin-top:10px}}
    .mini-pending{{font-size:9px;color:var(--muted-2);margin-top:8px}}.table-wrap{{border-radius:14px;border-color:rgba(255,255,255,.06)}}
    th{{padding:11px 12px;background:#090f16;font-size:9px}}td{{padding:11px 12px;font-size:10px}}tbody tr:hover{{background:rgba(255,255,255,.018)}}
    .settings-nav{{padding:5px;border:1px solid rgba(255,255,255,.06);border-radius:13px;background:#080d14}}
    .setting-tab{{border:0;border-radius:9px}}.setting-tab.active{{background:rgba(99,226,203,.11);color:var(--accent)}}
    .toast{{top:22px;right:26px;border-radius:13px}}.empty-state{{border-color:#263444;background:rgba(255,255,255,.012)}}
    @media(max-width:1180px){{.dashboard-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.request-details{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(max-width:880px){{.app-shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto}}.nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}
    .main{{padding:18px}}.request-primary{{align-items:flex-start;flex-direction:column}}.request-primary strong{{font-size:20px}}}}
    @media(max-width:620px){{.dashboard-grid,.summary-grid,.mini-balance-grid{{grid-template-columns:1fr}}.nav{{grid-template-columns:repeat(2,minmax(0,1fr))}}
    .request-details{{grid-template-columns:1fr}}.request-warning{{display:block}}.request-warning span{{display:block;margin-top:5px}}}}

    </style></head><body>{notice_html}<div class='app-shell'><aside class='sidebar'><div class='brand'><div class='brand-mark'>N</div><div><strong>Nerlo Wallet</strong><small>Yönetim Merkezi</small></div></div><nav class='nav'>{nav_html}</nav><div class='sidebar-foot'><span class='version'>NERLO OPERATIONS · PRO V5</span><a class='logout' href='/logout'>Güvenli çıkış</a></div></aside><main class='main'><header class='topbar'><div><span class='eyebrow'>NERLO OPERATIONS</span><h1>Finans Kontrol Merkezi</h1><p>Blockchain, kullanıcı ve bakiye operasyonları tek güvenli çalışma alanında.</p></div><span class='top-pill'><i class='live-dot'></i>{h(current_panel_username())} · Sistem aktif</span></header>

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


_background_services_started = False
_background_services_lock = threading.Lock()


def start_background_services_once():
    global _background_services_started
    if not BACKGROUND_SERVICES_ENABLED:
        return
    with _background_services_lock:
        if _background_services_started:
            return
        validate_runtime_config()
        threading.Thread(
            target=_run_singleton_polling_service,
            args=("telegram-bot", bot_poll_once, 1),
            daemon=True,
            name="telegram-bot",
        ).start()
        threading.Thread(
            target=_run_singleton_polling_service,
            args=("live-rates", update_live_rates, RATE_UPDATE_SECONDS),
            daemon=True,
            name="live-rates",
        ).start()
        start_exchange_threads()
        _background_services_started = True
        print("BUILD VERSION:", BUILD_VERSION)
        print("EXCHANGE HEALTH:", exchange_health_snapshot())
        print("WALLET MODE: PostgreSQL ledger + durable queue + watch-only HD addresses + blockchain indexers")


start_background_services_once()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
