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
import unicodedata
from urllib.parse import urlparse
from collections import defaultdict, deque
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from html import escape, unescape

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
SERVICE_ROLE = os.getenv("SERVICE_ROLE", "app").strip().lower()
if SERVICE_ROLE not in {"app", "signer"}:
    raise RuntimeError("SERVICE_ROLE yalnızca app veya signer olabilir")
SIGNER_STAGE = "TRON-POOL-SWEEP-AND-WITHDRAW-STAGE3"
SIGNER_SUPPORTED_ASSETS = {"TRX", "USDT"}
SIGNER_BROADCAST_ENABLED = os.getenv("SIGNER_BROADCAST_ENABLED", "0").strip() == "1"
TRON_PRIVATE_KEY = os.getenv("TRON_PRIVATE_KEY", "").strip()
TRON_HOT_WALLET_ADDRESS = os.getenv("TRON_HOT_WALLET_ADDRESS", "").strip()
SIGNER_CALLBACK_URL = os.getenv("SIGNER_CALLBACK_URL", "").strip()
SIGNER_CONFIRM_POLL_SECONDS = max(10, int(os.getenv("SIGNER_CONFIRM_POLL_SECONDS", "20")))
SIGNER_EXPIRY_GRACE_SECONDS = max(60, int(os.getenv("SIGNER_EXPIRY_GRACE_SECONDS", "300")))
TRON_TRX_MIN_RESERVE = Decimal(os.getenv("TRON_TRX_MIN_RESERVE", "5"))
TRON_USDT_MIN_TRX_RESERVE = Decimal(os.getenv("TRON_USDT_MIN_TRX_RESERVE", "50"))
TRON_USDT_FEE_LIMIT_SUN = max(1_000_000, int(os.getenv("TRON_USDT_FEE_LIMIT_SUN", "100000000")))

# TRON deposit-pool collection. These secrets are valid only in SERVICE_ROLE=signer.
TRON_SWEEP_ENABLED = os.getenv("TRON_SWEEP_ENABLED", "0").strip() == "1"
TRON_SWEEP_MNEMONIC = os.getenv("TRON_SWEEP_MNEMONIC", "").strip()
TRON_SWEEP_MNEMONIC_PASSPHRASE = os.getenv("TRON_SWEEP_MNEMONIC_PASSPHRASE", "")
TRON_SWEEP_ACCOUNT_XPRV = os.getenv("TRON_SWEEP_ACCOUNT_XPRV", "").strip()
TRON_SWEEP_ACCOUNT_PATH = os.getenv("TRON_SWEEP_ACCOUNT_PATH", "m/44'/195'/0'").strip()
TRON_SWEEP_POLL_SECONDS = max(10, int(os.getenv("TRON_SWEEP_POLL_SECONDS", "20")))
TRON_SWEEP_BATCH_LIMIT = max(1, min(100, int(os.getenv("TRON_SWEEP_BATCH_LIMIT", "25"))))
TRON_SWEEP_TRX_RESERVE = Decimal(os.getenv("TRON_SWEEP_TRX_RESERVE", "1.5"))
TRON_SWEEP_MIN_TRX = Decimal(os.getenv("TRON_SWEEP_MIN_TRX", "2"))
TRON_SWEEP_MIN_USDT = Decimal(os.getenv("TRON_SWEEP_MIN_USDT", "1"))
TRON_SWEEP_USDT_GAS_TARGET = Decimal(os.getenv("TRON_SWEEP_USDT_GAS_TARGET", "50"))
TRON_SWEEP_USDT_FEE_LIMIT_SUN = max(1_000_000, int(os.getenv("TRON_SWEEP_USDT_FEE_LIMIT_SUN", "100000000")))
TRON_SWEEP_MAX_RETRIES = max(3, int(os.getenv("TRON_SWEEP_MAX_RETRIES", "12")))
TRON_POOL_ADDRESS = os.getenv("TRON_POOL_ADDRESS", "").strip() or TRON_HOT_WALLET_ADDRESS

BUILD_VERSION = "NERLO-2026-06-26-TRX-ONLY-AUTO-WITHDRAW-V13"
PANEL_RELEASE = "TREASURY-CONTROL-CENTER-V11"
SECURITY_RELEASE = "INTERNAL-DEPOSIT-ADDRESS-GUARD-V2"
SIGNER_RELEASE = "TRON-POOL-SWEEP-AND-WITHDRAW-SIGNER-V3"
SWEEP_RELEASE = "TRON-HD-DEPOSIT-SWEEP-V1"
SOURCE_BASE_SHA256 = "e02572a5075f8b29236086eedc23ddefd97ec049855ac2229138e4cd4b92aea2"

CONFIRMATION_THRESHOLDS = {
    "BTC": max(1, int(os.getenv("BTC_CONFIRMATIONS", "3"))),
    "LTC": max(1, int(os.getenv("LTC_CONFIRMATIONS", "6"))),
    "ETH": max(1, int(os.getenv("ETH_CONFIRMATIONS", "12"))),
    "TRX": max(1, int(os.getenv("TRX_CONFIRMATIONS", "19"))),
    "USDT": max(1, int(os.getenv("USDT_CONFIRMATIONS", "19"))),
    "XMR": max(1, int(os.getenv("XMR_CONFIRMATIONS", "10"))),
}
AUTO_DEPOSIT_ASSETS = {"BTC", "LTC", "ETH", "TRX", "USDT", "XMR"}
AUTO_WITHDRAW_ASSETS = {"TRX"}
MANUAL_WITHDRAW_ASSETS = {"BTC", "LTC", "ETH"}
WITHDRAW_ENABLED_ASSETS = {"TL"} | AUTO_WITHDRAW_ASSETS | MANUAL_WITHDRAW_ASSETS
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

    CREATE TABLE IF NOT EXISTS signer_requests (
        request_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        asset TEXT NOT NULL,
        destination TEXT NOT NULL,
        amount NUMERIC(50, 18) NOT NULL,
        status TEXT NOT NULL DEFAULT 'prepared',
        txid TEXT NOT NULL DEFAULT '',
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        response JSONB NOT NULL DEFAULT '{}'::jsonb,
        last_error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        broadcast_at TIMESTAMPTZ,
        confirmed_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_signer_requests_status_updated
        ON signer_requests (status, updated_at DESC);

    CREATE TABLE IF NOT EXISTS signer_sweeps (
        sweep_id BIGSERIAL PRIMARY KEY,
        event_id BIGINT NOT NULL UNIQUE,
        user_id TEXT NOT NULL,
        asset TEXT NOT NULL CHECK (asset IN ('TRX','USDT')),
        source_address TEXT NOT NULL,
        destination_address TEXT NOT NULL,
        derivation_index BIGINT,
        derivation_path TEXT NOT NULL DEFAULT '',
        amount NUMERIC(50,18) NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        funding_txid TEXT NOT NULL DEFAULT '',
        sweep_txid TEXT NOT NULL DEFAULT '',
        cleanup_txid TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        response JSONB NOT NULL DEFAULT '{}'::jsonb,
        last_error TEXT NOT NULL DEFAULT '',
        available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        locked_at TIMESTAMPTZ,
        locked_by TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        funded_at TIMESTAMPTZ,
        broadcast_at TIMESTAMPTZ,
        cleanup_at TIMESTAMPTZ,
        confirmed_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_signer_sweeps_claim
        ON signer_sweeps (status, available_at, sweep_id);
    CREATE INDEX IF NOT EXISTS idx_signer_sweeps_source
        ON signer_sweeps (source_address, asset, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signer_sweeps_funding_txid
        ON signer_sweeps (funding_txid) WHERE funding_txid<>'';
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


def _tron_address_from_private_int(private_int):
    private_int = int(private_int)
    if not 1 <= private_int < _SECP_N:
        raise ValueError("TRON private key secp256k1 aralığında değil")
    point = _secp_mul(private_int)
    if point is None:
        raise ValueError("TRON private key public key üretemedi")
    x, y = point
    public_key = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return _b58check_encode(b"\x41" + _keccak256(public_key)[-20:])


def _parse_bip32_path(path, allow_relative=False):
    text = str(path or "").strip()
    if not text:
        return []
    parts = text.split("/")
    if parts[0] in ("m", "M"):
        parts = parts[1:]
    elif not allow_relative:
        raise ValueError("BIP32 yolu m/ ile başlamalıdır")
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            raise ValueError("BIP32 yolunda boş bölüm var")
        hardened = part[-1:] in ("'", "h", "H")
        number_text = part[:-1] if hardened else part
        if not number_text.isdigit():
            raise ValueError("BIP32 yolu geçersiz")
        index = int(number_text)
        if not 0 <= index < 0x80000000:
            raise ValueError("BIP32 child index aralık dışında")
        result.append(index + (0x80000000 if hardened else 0))
    return result


def _bip39_seed(mnemonic, passphrase=""):
    words = " ".join(str(mnemonic or "").strip().split())
    if len(words.split()) not in (12, 15, 18, 21, 24):
        raise ValueError("TRON_SWEEP_MNEMONIC 12/15/18/21/24 kelime olmalıdır")
    normalized_mnemonic = unicodedata.normalize("NFKD", words)
    normalized_salt = unicodedata.normalize("NFKD", "mnemonic" + str(passphrase or ""))
    return hashlib.pbkdf2_hmac(
        "sha512",
        normalized_mnemonic.encode("utf-8"),
        normalized_salt.encode("utf-8"),
        2048,
        dklen=64,
    )


def _bip32_master_private(seed):
    digest = hmac.new(b"Bitcoin seed", bytes(seed), hashlib.sha512).digest()
    private_int = int.from_bytes(digest[:32], "big")
    if not 1 <= private_int < _SECP_N:
        raise ValueError("BIP32 master private key üretilemedi")
    return {"private_key": private_int, "chain_code": digest[32:]}


def _ckd_priv(node, index):
    private_int = int(node["private_key"])
    chain_code = bytes(node["chain_code"])
    index = int(index)
    if not 0 <= index <= 0xFFFFFFFF:
        raise ValueError("BIP32 child index geçersiz")
    if index >= 0x80000000:
        data = b"\x00" + private_int.to_bytes(32, "big") + index.to_bytes(4, "big")
    else:
        data = _secp_compress(_secp_mul(private_int)) + index.to_bytes(4, "big")
    digest = hmac.new(chain_code, data, hashlib.sha512).digest()
    tweak = int.from_bytes(digest[:32], "big")
    if tweak >= _SECP_N:
        raise ValueError("BIP32 child tweak aralık dışında")
    child_private = (private_int + tweak) % _SECP_N
    if child_private == 0:
        raise ValueError("BIP32 child private key sıfır oldu")
    return {"private_key": child_private, "chain_code": digest[32:]}


def _derive_private_node(node, path_indexes):
    current = {"private_key": int(node["private_key"]), "chain_code": bytes(node["chain_code"])}
    for index in path_indexes:
        current = _ckd_priv(current, index)
    return current


def _parse_xprv(xprv):
    payload = _b58check_decode(str(xprv or "").strip())
    if len(payload) != 78:
        raise ValueError("Extended private key 78 byte olmalıdır")
    key_data = payload[45:78]
    if len(key_data) != 33 or key_data[0] != 0:
        raise ValueError("Geçerli bir account XPRV bekleniyor")
    private_int = int.from_bytes(key_data[1:], "big")
    if not 1 <= private_int < _SECP_N:
        raise ValueError("XPRV private key aralık dışında")
    return {
        "version": payload[:4],
        "depth": payload[4],
        "parent_fingerprint": payload[5:9],
        "child_number": int.from_bytes(payload[9:13], "big"),
        "chain_code": payload[13:45],
        "private_key": private_int,
    }


_sweep_account_cache = None
_sweep_account_cache_lock = threading.Lock()


def _sweep_account_private_node(force=False):
    global _sweep_account_cache
    with _sweep_account_cache_lock:
        if _sweep_account_cache is not None and not force:
            return dict(_sweep_account_cache)
        if bool(TRON_SWEEP_MNEMONIC) == bool(TRON_SWEEP_ACCOUNT_XPRV):
            raise ValueError("Sweep için yalnızca TRON_SWEEP_MNEMONIC veya TRON_SWEEP_ACCOUNT_XPRV tanımlanmalıdır")
        if TRON_SWEEP_ACCOUNT_XPRV:
            parsed = _parse_xprv(TRON_SWEEP_ACCOUNT_XPRV)
            node = {"private_key": parsed["private_key"], "chain_code": parsed["chain_code"]}
        else:
            seed = _bip39_seed(TRON_SWEEP_MNEMONIC, TRON_SWEEP_MNEMONIC_PASSPHRASE)
            master = _bip32_master_private(seed)
            node = _derive_private_node(master, _parse_bip32_path(TRON_SWEEP_ACCOUNT_PATH))
        _sweep_account_cache = dict(node)
        return dict(node)


def _sweep_account_xpub_matches():
    if not TRON_XPUB:
        return False
    account = _sweep_account_private_node()
    parsed = _parse_xpub(TRON_XPUB)
    public_key = _secp_compress(_secp_mul(account["private_key"]))
    return (
        secrets.compare_digest(public_key, parsed["public_key"])
        and secrets.compare_digest(bytes(account["chain_code"]), bytes(parsed["chain_code"]))
    )


def _sweep_private_for_address(derivation_index, derivation_path, expected_address):
    account = _sweep_account_private_node()
    relative_path = str(derivation_path or "").strip()
    if relative_path:
        indexes = _parse_bip32_path(relative_path)
    else:
        if derivation_index is None:
            raise ValueError("Sweep adresinin derivation index bilgisi eksik")
        indexes = [int(_xpub_branch_for_chain("TRON")), int(derivation_index)]
    if any(index >= 0x80000000 for index in indexes):
        raise ValueError("XPUB altındaki sweep yolu hardened child içeremez")
    child = _derive_private_node(account, indexes)
    derived_address = _tron_address_from_private_int(child["private_key"])
    if not secrets.compare_digest(derived_address, str(expected_address or "")):
        raise ValueError("Sweep private key kaynak yatırma adresiyle eşleşmiyor")
    return int(child["private_key"])


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


def _address_compare_key(asset, value):
    """Return a canonical comparison key without changing the displayed address."""
    asset = str(asset or "").upper()
    text = re.sub(r"\s+", "", str(value or "").strip())
    if asset == "ETH":
        return text.lower()
    if asset == "BTC" and text.lower().startswith("bc1"):
        return text.lower()
    if asset == "LTC" and text.lower().startswith("ltc1"):
        return text.lower()
    if asset == "TON":
        raw_match = re.fullmatch(r"(-1|0):([0-9a-fA-F]{64})", text)
        if raw_match:
            return f"{raw_match.group(1)}:{raw_match.group(2).lower()}"
        try:
            padded = text + "=" * ((4 - len(text) % 4) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(raw) == 36:
                workchain = -1 if raw[1] == 0xFF else int(raw[1])
                return f"{workchain}:{raw[2:34].hex()}"
        except (ValueError, binascii.Error, UnicodeEncodeError):
            pass
    return text


def _configured_internal_wallet_addresses(asset):
    chain = _asset_chain(asset)
    keys_by_chain = {
        "TRON": ("wallet_TRX", "wallet_USDT"),
        "BTC": ("wallet_BTC",),
        "LTC": ("wallet_LTC",),
        "ETH": ("wallet_ETH",),
        "XMR": ("wallet_XMR",),
        "TON": ("wallet_TON",),
    }
    result = []
    for key in keys_by_chain.get(chain, (f"wallet_{asset}",)):
        value = str(settings.get(key, "")).strip()
        if value:
            result.append((key, value))
    return result


def find_internal_deposit_address(asset, address):
    """Find whether a destination belongs to any Nerlo deposit wallet.

    The check covers every user's generated deposit address and legacy/shared
    deposit addresses. TRX and TRC20 USDT are checked together on TRON.
    """
    asset = str(asset or "").upper()
    if asset == "TL":
        return None
    valid, normalized, _ = validate_wallet_address(asset, address)
    if not valid:
        return None
    target_key = _address_compare_key(asset, normalized)
    chain = _asset_chain(asset)

    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id,address,source
                    FROM exchange_addresses
                    WHERE chain=%s AND status='active'
                    """,
                    (chain,),
                )
                rows = cur.fetchall()
    except Exception as exc:
        print("INTERNAL ADDRESS SECURITY CHECK ERROR:", exc)
        raise RuntimeError("Yatırma adresi güvenlik kontrolü yapılamadı") from exc

    for owner_uid, stored_address, source in rows:
        if secrets.compare_digest(
            _address_compare_key(asset, stored_address),
            target_key,
        ):
            return {
                "source": source or "exchange_addresses",
                "user_id": str(owner_uid),
                "address": str(stored_address),
                "chain": chain,
            }

    for setting_key, stored_address in _configured_internal_wallet_addresses(asset):
        try:
            stored_key = _address_compare_key(asset, stored_address)
        except Exception:
            continue
        if stored_key and secrets.compare_digest(stored_key, target_key):
            return {
                "source": setting_key,
                "user_id": "",
                "address": stored_address,
                "chain": chain,
            }
    return None


def ensure_external_withdraw_address(uid, asset, address):
    """Validate, normalize and reject every internal deposit destination."""
    asset = str(asset or "").upper()
    valid, normalized, address_error = validate_wallet_address(asset, address)
    if not valid:
        raise ValueError(address_error)
    if asset in ("TRX", "USDT") and TRON_POOL_ADDRESS:
        if secrets.compare_digest(_address_compare_key(asset, normalized), _address_compare_key(asset, TRON_POOL_ADDRESS)):
            if lang_of(uid) == "en":
                raise ValueError("Withdrawals cannot be sent to the Nerlo treasury pool address.")
            raise ValueError("Çekim Nerlo ana havuz adresine gönderilemez.")
    internal = find_internal_deposit_address(asset, normalized)
    if internal:
        if lang_of(uid) == "en":
            raise ValueError(
                "This address belongs to a Nerlo Wallet deposit account. "
                "For security, withdrawals can only be sent to an external wallet address."
            )
        raise ValueError(
            "Bu adres Nerlo Wallet'a ait bir yatırma adresidir. "
            "Güvenlik nedeniyle çekim yalnızca harici bir cüzdan adresine yapılabilir."
        )
    return normalized

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


def exchange_get_request(rid):
    rid = str(rid or "").strip()
    if not rid:
        return None
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload,status,automatic FROM exchange_requests WHERE request_id=%s",
                (rid,),
            )
            row = cur.fetchone()
    if not row:
        return None
    record = dict(row[0] or {})
    record["id"] = rid
    record["status"] = row[1]
    record["automatic"] = bool(row[2])
    return record


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


def exchange_is_internal_tron_funding(txid, recipient_address):
    txid = str(txid or "").strip()
    recipient_address = str(recipient_address or "").strip()
    if not txid or not recipient_address:
        return False
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM signer_sweeps
                WHERE funding_txid=%s AND source_address=%s
                LIMIT 1
                """,
                (txid, recipient_address),
            )
            return cur.fetchone() is not None


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
                if exchange_is_internal_tron_funding(tx.get("txID"), address):
                    print("INTERNAL TRON GAS FUNDING SKIPPED:", tx.get("txID"), address, amount)
                    continue
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


def normalize_manual_withdraw_txid(asset, txid):
    asset = str(asset or "").upper()
    text = re.sub(r"\s+", "", str(txid or ""))
    if asset in ("BTC", "LTC"):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
            raise ValueError(f"Geçerli bir {asset} TXID giriniz")
        return text.lower()
    if asset == "ETH":
        body = text[2:] if text.lower().startswith("0x") else text
        if not re.fullmatch(r"[0-9a-fA-F]{64}", body):
            raise ValueError("Geçerli bir Ethereum TXID giriniz")
        return "0x" + body.lower()
    raise ValueError(f"{asset} manuel TXID tamamlama desteklenmiyor")


def exchange_complete_manual_withdrawal(rid, txid, admin_username=""):
    rid = str(rid or "").strip()
    lock_conn = _db_connect()
    lock_conn.autocommit = True
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (f"manual-withdraw:{rid}",))
        record = exchange_get_request(rid)
        if not record or record.get("type") != "withdraw":
            raise ValueError("Çekim talebi bulunamadı")
        asset = str(record.get("asset") or "").upper()
        if asset not in MANUAL_WITHDRAW_ASSETS:
            raise ValueError("Bu varlık manuel TXID iş akışını kullanmıyor")
        normalized = normalize_manual_withdraw_txid(asset, txid)
        if record.get("status") == "completed":
            existing = str(record.get("broadcast_txid") or record.get("manual_txid") or "")
            if existing and secrets.compare_digest(existing.lower(), normalized.lower()):
                return record
            raise ValueError("Tamamlanmış çekimin TXID değeri değiştirilemez")
        if record.get("status") == "rejected":
            raise ValueError("Reddedilmiş çekim tamamlanamaz")
        if record.get("status") not in ("pending", "processing"):
            raise ValueError("Çekim durumu manuel tamamlamaya uygun değil")
        normalized_address = ensure_external_withdraw_address(
            record.get("user_id", ""), asset, record.get("address", "")
        )
        exchange_update_request(rid, {
            "address": normalized_address,
            "internal_address_checked": True,
            "manual_txid": normalized,
            "broadcast_txid": normalized,
            "manual_completed_by": str(admin_username or ""),
            "manual_completed_at": now(),
            "manual_status": "txid_recorded",
        })
        return exchange_finalize_withdrawal(rid, normalized)
    finally:
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (f"manual-withdraw:{rid}",))
        except Exception:
            pass
        lock_conn.close()


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
                    record["address"] = ensure_external_withdraw_address(
                        record.get("user_id", ""), asset, record.get("address", "")
                    )
                    record["internal_address_checked"] = True
                    if asset in AUTO_WITHDRAW_ASSETS:
                        if not record.get("signer_enabled") or not WITHDRAW_SIGNER_URL:
                            raise ValueError("Otomatik gönderim servisi bağlı değil")
                        record.update({
                            "status": "processing",
                            "broadcast_locked": True,
                            "signer_status": "queued",
                            "broadcast_queued_at": now(),
                        })
                        should_enqueue_broadcast = True
                    elif asset in MANUAL_WITHDRAW_ASSETS:
                        record.update({
                            "status": "processing",
                            "manual_status": "awaiting_txid",
                            "manual_processing_at": now(),
                        })
                    else:
                        raise ValueError(f"{asset} çekimi bu sürümde desteklenmiyor")
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

    asset = str(record.get("asset") or "").upper()
    if asset != "TL":
        normalized_address = ensure_external_withdraw_address(
            record.get("user_id", ""), asset, record.get("address", "")
        )
        if normalized_address != record.get("address"):
            exchange_update_request(rid, {
                "address": normalized_address,
                "internal_address_checked": True,
            })
            record["address"] = normalized_address

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
            cur.execute("SELECT status,COUNT(*) FROM signer_sweeps GROUP BY status")
            sweeps = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT meta_value FROM exchange_meta WHERE meta_key='tron-pool-snapshot'")
            pool_row = cur.fetchone()
            pool_snapshot = dict(pool_row[0] or {}) if pool_row else {}
    return {
        "build_version": BUILD_VERSION,
        "panel_release": PANEL_RELEASE,
        "security_release": SECURITY_RELEASE,
        "signer_release": SIGNER_RELEASE,
        "signer_stage": SIGNER_STAGE,
        "service_role": SERVICE_ROLE,
        "source_base_sha256": SOURCE_BASE_SHA256,
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
        "sweeps": sweeps,
        "tron_pool": pool_snapshot,
        "reconciliation": reconciliation,
        "withdraw_guard": {
            "enabled": True,
            "automatic_assets": sorted(AUTO_WITHDRAW_ASSETS),
            "manual_assets": sorted(MANUAL_WITHDRAW_ASSETS),
            "manual_txid_required": True,
        },
    }


# Fail fast if the local Keccak implementation is ever modified incorrectly.
if _keccak256(b"").hex() != "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470":
    raise RuntimeError("Keccak-256 self-test failed")


def validate_runtime_config():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL tanımlı değil")
    if len(app.secret_key) < 32:
        raise RuntimeError("FLASK_SECRET_KEY en az 32 karakter olmalıdır")

    if SERVICE_ROLE == "signer":
        if len(WITHDRAW_SIGNER_TOKEN) < 32:
            raise RuntimeError("Signer servisi için WITHDRAW_SIGNER_TOKEN en az 32 karakter olmalıdır")
        if len(EXCHANGE_INTERNAL_TOKEN) < 32:
            raise RuntimeError("Signer callback işlemleri için EXCHANGE_INTERNAL_TOKEN en az 32 karakter olmalıdır")
        if SIGNER_BROADCAST_ENABLED:
            if not TRONGRID_KEY:
                raise RuntimeError("Gerçek TRON gönderimi için TRONGRID_KEY zorunludur")
            if not SIGNER_CALLBACK_URL:
                raise RuntimeError("Gerçek gönderim için SIGNER_CALLBACK_URL zorunludur")
            callback = urlparse(SIGNER_CALLBACK_URL)
            if callback.scheme != "https" and callback.hostname not in ("localhost", "127.0.0.1"):
                raise RuntimeError("SIGNER_CALLBACK_URL HTTPS olmalıdır")
            if not USDT_TRC20_CONTRACT:
                raise RuntimeError("USDT gönderimi için USDT_TRC20_CONTRACT zorunludur")
            valid_contract, normalized_contract, _ = validate_wallet_address("USDT", USDT_TRC20_CONTRACT)
            if not valid_contract:
                raise RuntimeError("USDT_TRC20_CONTRACT geçersiz TRON adresidir")
            if normalized_contract != USDT_TRC20_CONTRACT:
                raise RuntimeError("USDT_TRC20_CONTRACT normalize edilemedi")
            derived_address = _tron_address_from_private_key()
            if not TRON_HOT_WALLET_ADDRESS:
                raise RuntimeError("TRON_HOT_WALLET_ADDRESS zorunludur")
            valid_hot, normalized_hot, _ = validate_wallet_address("TRX", TRON_HOT_WALLET_ADDRESS)
            if not valid_hot or not secrets.compare_digest(derived_address, normalized_hot):
                raise RuntimeError("TRON_PRIVATE_KEY ile TRON_HOT_WALLET_ADDRESS eşleşmiyor")
            secret_values = {TRON_PRIVATE_KEY.lower().removeprefix("0x"), WITHDRAW_SIGNER_TOKEN, EXCHANGE_INTERNAL_TOKEN, app.secret_key}
            if len(secret_values) != 4:
                raise RuntimeError("Signer gizli değerleri birbirinden farklı olmalıdır")

            if TRON_SWEEP_ENABLED:
                if not TRON_POOL_ADDRESS:
                    raise RuntimeError("TRON sweep için TRON_POOL_ADDRESS zorunludur")
                valid_pool, normalized_pool, _ = validate_wallet_address("TRX", TRON_POOL_ADDRESS)
                if not valid_pool:
                    raise RuntimeError("TRON_POOL_ADDRESS geçersizdir")
                if not secrets.compare_digest(normalized_pool, normalized_hot):
                    raise RuntimeError("TRON_POOL_ADDRESS ile TRON_HOT_WALLET_ADDRESS aynı havuz cüzdanı olmalıdır")
                if not TRON_XPUB:
                    raise RuntimeError("Sweep private key eşleşmesi için signer servisinde TRON_XPUB zorunludur")
                if bool(TRON_SWEEP_MNEMONIC) == bool(TRON_SWEEP_ACCOUNT_XPRV):
                    raise RuntimeError("Yalnızca TRON_SWEEP_MNEMONIC veya TRON_SWEEP_ACCOUNT_XPRV tanımlanmalıdır")
                _parse_bip32_path(TRON_SWEEP_ACCOUNT_PATH)
                if not _sweep_account_xpub_matches():
                    raise RuntimeError("Sweep mnemonic/XPRV, TRON_XPUB ile eşleşmiyor")
                internal_pool = find_internal_deposit_address("TRX", normalized_pool)
                if internal_pool:
                    raise RuntimeError("TRON havuz adresi kullanıcı yatırma adreslerinden biri olamaz")
                sweep_secret = TRON_SWEEP_ACCOUNT_XPRV or TRON_SWEEP_MNEMONIC
                if sweep_secret in {WITHDRAW_SIGNER_TOKEN, EXCHANGE_INTERNAL_TOKEN, app.secret_key}:
                    raise RuntimeError("Sweep anahtarı diğer signer gizli değerleriyle aynı olamaz")
                if TRON_SWEEP_TRX_RESERVE < 0 or TRON_SWEEP_MIN_TRX <= 0 or TRON_SWEEP_MIN_USDT <= 0:
                    raise RuntimeError("Sweep minimum ve rezerv değerleri geçersiz")
                if TRON_SWEEP_USDT_GAS_TARGET <= TRON_SWEEP_TRX_RESERVE:
                    raise RuntimeError("TRON_SWEEP_USDT_GAS_TARGET, TRON_SWEEP_TRX_RESERVE değerinden büyük olmalıdır")
                configured_gas_sun = int((TRON_SWEEP_USDT_GAS_TARGET * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
                if configured_gas_sun > TRON_SWEEP_USDT_FEE_LIMIT_SUN:
                    raise RuntimeError("TRON_SWEEP_USDT_GAS_TARGET, TRON_SWEEP_USDT_FEE_LIMIT_SUN sınırını aşamaz")
        return

    if TRON_SWEEP_MNEMONIC or TRON_SWEEP_ACCOUNT_XPRV or TRON_PRIVATE_KEY:
        raise RuntimeError("Private key, mnemonic veya XPRV ana uygulama servisinde bulunamaz; yalnızca signer servisine ekleyiniz")

    required = {
        "BOT_TOKEN": TOKEN,
        "ADMIN_CHAT_ID": ADMIN_CHAT_ID,
        "PANEL_USERNAME": PANEL_USERNAME,
        "PANEL_PASSWORD": PANEL_PASSWORD,
    }
    missing = [key for key, value in required.items() if not str(value).strip()]
    if missing:
        raise RuntimeError("Eksik zorunlu ortam değişkenleri: " + ", ".join(missing))
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
        if len(WITHDRAW_SIGNER_TOKEN) < 32:
            raise RuntimeError("WITHDRAW_SIGNER_URL kullanılırken WITHDRAW_SIGNER_TOKEN en az 32 karakter olmalıdır")
        if len(EXCHANGE_INTERNAL_TOKEN) < 32:
            raise RuntimeError("Otomatik signer için EXCHANGE_INTERNAL_TOKEN en az 32 karakter olmalıdır")
        if PUBLIC_BASE_URL:
            public_url = urlparse(PUBLIC_BASE_URL)
            if public_url.scheme != "https":
                raise RuntimeError("PUBLIC_BASE_URL production ortamında HTTPS olmalıdır")
    if EXCHANGE_INTERNAL_TOKEN and len(EXCHANGE_INTERNAL_TOKEN) < 32:
        raise RuntimeError("EXCHANGE_INTERNAL_TOKEN en az 32 karakter olmalıdır")
    if TRON_POOL_ADDRESS:
        valid_pool, _, _ = validate_wallet_address("TRX", TRON_POOL_ADDRESS)
        if not valid_pool:
            raise RuntimeError("TRON_POOL_ADDRESS geçersiz TRON adresidir")


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32); session["csrf_token"] = token
    return token


@app.before_request
def enforce_service_role():
    signer_paths = {"/", "/version", "/health/signer", "/internal/signer/withdraw"}
    if SERVICE_ROLE == "signer" and request.path not in signer_paths:
        return {"ok": False, "error": "not_found"}, 404
    if SERVICE_ROLE == "app" and request.path.startswith("/internal/signer/"):
        return {"ok": False, "error": "not_found"}, 404
    return None


@app.before_request
def enforce_csrf():
    if request.method == "POST":
        if request.path.startswith("/internal/signer/"):
            return None
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
        "r10_verify": "R10 Doğrulama", "r10_link_question": "r10.net profil linkinizi gönderiniz.",
        "r10_invalid_link": "Geçerli bir r10.net profil linki gönderiniz.",
        "r10_fetch_failed": "r10 profili okunamadı. Linki kontrol edip tekrar deneyiniz.",
        "r10_key_sent": "Hoş geldiniz {name}.\n\nTL işlemleri için son adım:\nBu doğrulama keyini r10.net üzerinden @nerlowallet hesabına PM gönderiniz.\n\nKey: {key}",
        "r10_corporate_key_sent": "Kurumsal r10 hesabınız algılandı.\n\nTL işlemleri için @nerlowallet hesabına PM gönderiniz:\nKey: {key}\nIBAN ad soyad: ...\n\nBu ad soyad onaydan sonra değiştirilemez.",
        "r10_hidden_name": "Lütfen r10 profilinizde ad soyad gizlemesini kapatıp tekrar deneyiniz.",
        "r10_duplicate": "Bu r10 profili başka Telegram hesabına bağlı.",
        "r10_rules_failed": "Bireysel hesap için en az 6 aylık üyelik ve en az 5 trade gerekir.",
        "r10_pending": "R10 doğrulamanız admin onayı bekliyor.",
        "r10_required": "TL işlemleri için önce R10 doğrulaması gerekir.",
        "r10_name_mismatch": "Ad soyad R10 profilinizle uyuşmuyor.",
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
        "r10_verify": "R10 Verification", "r10_link_question": "Send your r10.net profile link.",
        "r10_invalid_link": "Send a valid r10.net profile link.",
        "r10_fetch_failed": "The r10 profile could not be read. Check the link and try again.",
        "r10_key_sent": "Welcome {name}.\n\nLast step for TRY transactions:\nSend this verification key to @nerlowallet on r10.net by PM.\n\nKey: {key}",
        "r10_corporate_key_sent": "Corporate r10 account detected.\n\nSend this to @nerlowallet by PM:\nKey: {key}\nIBAN full name: ...\n\nThis name cannot be changed after approval.",
        "r10_hidden_name": "Please disable name hiding on your r10 profile and try again.",
        "r10_duplicate": "This r10 profile is already linked to another Telegram account.",
        "r10_rules_failed": "Personal accounts require at least 6 months membership and at least 5 trades.",
        "r10_pending": "Your R10 verification is waiting for admin approval.",
        "r10_required": "R10 verification is required for TRY transactions.",
        "r10_name_mismatch": "The name does not match your R10 profile.",
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
    clean_rows = [
        (str(label).strip(), str(value).strip())
        for label, value in rows
        if value not in (None, "")
    ]
    long_labels = {
        "cüzdan adresi", "wallet address", "yatırma adresi", "deposit address",
        "iban", "txid", "referans", "reference",
    }
    lines = [str(title).strip()]
    for label, value in clean_rows:
        if label.lower() in long_labels or len(value) > 34:
            lines.extend(["", label, value])
        else:
            lines.append(f"{label}: {value}")
    if note:
        lines.extend(["", f"Bilgi: {str(note).strip()}"])
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


def _tr_lower(value):
    table = str.maketrans("Iİ", "ıi")
    return str(value or "").translate(table).lower()


def _name_parts(value):
    clean = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşü\s]", " ", str(value or ""))
    parts = [p for p in clean.split() if p]
    return parts


def r10_tl_ready(uid):
    info = users.get(str(uid), {}).get("r10_verification", {})
    return info.get("status") == "approved"


def r10_required_text(uid):
    info = users.get(str(uid), {}).get("r10_verification", {})
    if info.get("status") == "pending":
        return t(uid, "r10_pending")
    return t(uid, "r10_required")


def normalize_person_name(value):
    return " ".join(_name_parts(value)).strip()


def validate_r10_name(uid, full_name):
    info = users.get(str(uid), {}).get("r10_verification", {})
    if info.get("status") != "approved":
        return False
    if info.get("account_type") == "corporate":
        saved = normalize_person_name(info.get("iban_owner", ""))
        return bool(saved) and _tr_lower(normalize_person_name(full_name)) == _tr_lower(saved)
    first = _tr_lower(info.get("first2", ""))
    last = _tr_lower(info.get("last2", ""))
    parts = _name_parts(full_name)
    if len(parts) < 2 or not first or not last:
        return False
    return _tr_lower(parts[0]).startswith(first) and _tr_lower(parts[-1]).startswith(last)


def normalize_r10_profile_url(profile_url):
    parsed = urlparse(str(profile_url or "").strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.scheme not in ("http", "https") or host != "r10.net":
        raise ValueError("invalid_link")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) == 1:
        slug = parts[0].lower()
        if not re.fullmatch(r"[a-z0-9_.-]{2,64}", slug):
            raise ValueError("invalid_link")
        return f"https://www.r10.net/{slug}", slug

    if len(parts) == 2 and parts[0].lower() == "profil":
        match = re.fullmatch(r"([0-9]+)-([a-z0-9_.-]{2,64})\.html", parts[1], re.I)
        if not match:
            raise ValueError("invalid_link")
        profile_id, slug = match.group(1), match.group(2).lower()
        return f"https://www.r10.net/profil/{profile_id}-{slug}.html", slug

    raise ValueError("invalid_link")


def r10_profile_used_by(profile_slug, except_uid=""):
    slug = str(profile_slug or "").lower().strip()
    for other_uid, user in users.items():
        if str(other_uid) == str(except_uid):
            continue
        info = (user or {}).get("r10_verification", {}) or {}
        if slug and str(info.get("profile_slug", "")).lower() == slug and info.get("status") in ("pending", "approved"):
            return str(other_uid)
    return ""


def r10_iban_owner_used_by(owner_key, except_uid=""):
    key = _tr_lower(owner_key).strip()
    if not key:
        return ""
    for other_uid, user in users.items():
        if str(other_uid) == str(except_uid):
            continue
        info = (user or {}).get("r10_verification", {}) or {}
        if info.get("status") == "approved" and _tr_lower(info.get("iban_owner", "")).strip() == key:
            return str(other_uid)
    return ""


def _r10_profile_text(html):
    source = str(html or "")
    source = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", source)
    source = unescape(source).replace("\xa0", " ")
    source = re.sub(r"<[^>]+>", " ", source)
    return re.sub(r"\s+", " ", source).strip()


def _r10_field(text, label):
    next_labels = (
        "Ad Soyad|Unvan|Doğum Günü|Yaş|Üyelik Tarihi|Meslek|Şube|Konu Sayısı|"
        r"Mesaj Sayısı|Şikayet|Beğeniler|R10\+|Profil Ziyareti|Hakkında|Uzmanlıklar|Arkadaşlar"
    )
    match = re.search(
        rf"(?:^|\s){label}\s*:?\s*(.+?)(?=\s+(?:{next_labels})\s*:?\s*|$)",
        text,
        re.I,
    )
    return match.group(1).strip(" -:") if match else ""


def _r10_int(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else 0


def _r10_membership_months(day, month, year):
    try:
        joined = date(int(year), int(month), int(day))
    except ValueError:
        return None
    current = date.today()
    if joined > current:
        return None
    months = (current.year - joined.year) * 12 + current.month - joined.month
    if current.day < joined.day:
        months -= 1
    return max(0, months)


def _r10_mask_name(first, last):
    def mask_part(value):
        letters = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşü]", "", str(value or ""))
        if len(letters) < 2:
            return ""
        return letters[:2] + "*" * max(4, len(letters) - 2)

    return f"{mask_part(first)} {mask_part(last)}".strip()


def parse_r10_profile_name(html):
    text = _r10_profile_text(html)
    if not text:
        return None

    header = text.split("Künye", 1)[0]
    title = _r10_field(text, "Unvan")
    corporate = bool(title) or bool(re.search(r"\bKurumsal(?:\s+PLUS|\s+Üye)?\b", header, re.I))

    date_match = re.search(r"Üyelik\s*Tarihi\s*:?\s*([0-9]{1,2})[./-]([0-9]{1,2})[./-]([0-9]{4})", text, re.I)
    months = None
    membership_date = ""
    if date_match:
        day, month, year = date_match.groups()
        months = _r10_membership_months(day, month, year)
        membership_date = f"{int(day):02d}/{int(month):02d}/{int(year):04d}"

    r10_plus = re.search(r"R10\+\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", text, re.I)
    negative = neutral = positive = None
    if r10_plus:
        negative, neutral, positive = (_r10_int(value) for value in r10_plus.groups())

    common = {
        "account_type": "corporate" if corporate else "personal",
        "trades": positive,
        "months": months,
        "membership_date": membership_date,
        "r10_plus_negative": negative,
        "r10_plus_neutral": neutral,
        "r10_plus_positive": positive,
    }

    if corporate:
        common.update({
            "masked": title or "Kurumsal hesap",
            "first2": "",
            "last2": "",
            "company_title": title,
        })
        return common

    full_name = _r10_field(text, "Ad Soyad")
    if not full_name or full_name.strip() in ("-", "—"):
        return None

    name_tokens = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2,}(?:\*+)?", full_name)
    if len(name_tokens) < 2:
        return None

    first_token, last_token = name_tokens[0], name_tokens[-1]
    first_letters = re.sub(r"\*+", "", first_token)
    last_letters = re.sub(r"\*+", "", last_token)
    if len(first_letters) < 2 or len(last_letters) < 2:
        return None

    common.update({
        "masked": _r10_mask_name(first_letters, last_letters),
        "first2": first_letters[:2],
        "last2": last_letters[:2],
        "company_title": "",
    })
    return common


def fetch_r10_profile_mask(profile_url):
    canonical_url, slug = normalize_r10_profile_url(profile_url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.6",
        "Cache-Control": "no-cache",
    }
    response = requests.get(canonical_url, headers=headers, timeout=20, allow_redirects=True)
    response.raise_for_status()

    final_host = urlparse(response.url).netloc.lower().removeprefix("www.")
    if final_host != "r10.net":
        raise ValueError("invalid_link")

    lowered = response.text.lower()
    if "cf-chl-" in lowered or "just a moment" in lowered or "attention required" in lowered:
        raise RuntimeError("r10_access_challenge")

    parsed_name = parse_r10_profile_name(response.text)
    if not parsed_name:
        raise ValueError("name_not_found")
    if parsed_name.get("months") is None or parsed_name.get("trades") is None:
        raise ValueError("profile_data_incomplete")

    parsed_name["profile_url"] = response.url.split("?", 1)[0]
    parsed_name["profile_slug"] = slug
    return parsed_name


def begin_r10_verification(chat_id):
    uid = str(chat_id)
    user_state[uid] = {"flow": "r10_verify", "step": "profile_link"}
    send(chat_id, t(uid, "r10_link_question"))

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
        state["address"] = ensure_external_withdraw_address(
            uid, asset, state.get("address", "")
        )
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
        "internal_address_checked": asset != "TL",
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
    request_type = str(r.get("type") or "")
    status = localized_status(uid, r.get("status"))
    created = str(r.get("created_at") or "-")
    lines = []

    if request_type == "withdraw":
        asset = str(r.get("asset") or "")
        net = ucoin(uid, r.get("net_amount"), asset)
        lines.extend([
            net,
            "Gönderilecek net tutar" if is_tr else "Net amount to be sent",
            "",
            f"{'Durum' if is_tr else 'Status'}: {status}",
            f"{'Ağ' if is_tr else 'Network'}: {settings.get(f'network_{asset}', asset)}",
        ])
        if asset == "TL":
            lines.extend([
                "",
                "IBAN",
                str(r.get("iban") or "-"),
                f"{'Alıcı' if is_tr else 'Recipient'}: {r.get('name') or '-'}",
                f"{'Banka' if is_tr else 'Bank'}: {r.get('bank_name') or '-'}",
            ])
        else:
            lines.extend([
                "",
                "Hedef cüzdan" if is_tr else "Destination wallet",
                str(r.get("address") or "-"),
            ])
        lines.extend([
            "",
            f"{'Talep tutarı' if is_tr else 'Requested amount'}: {ucoin(uid, r.get('amount'), asset)}",
            f"{'Hizmet ücreti' if is_tr else 'Service fee'}: {ucoin(uid, r.get('fee'), asset)}",
        ])
        txid = str(r.get("broadcast_txid") or "")
        if txid:
            lines.extend(["", "TXID", txid])

    elif request_type == "deposit":
        asset = str(r.get("asset") or "")
        net = ucoin(uid, r.get("net_amount"), asset)
        lines.extend([
            net,
            "Bakiyeye eklenecek tutar" if is_tr else "Amount to be credited",
            "",
            f"{'Durum' if is_tr else 'Status'}: {status}",
        ])
        if asset != "TL":
            lines.append(f"{'Ağ' if is_tr else 'Network'}: {r.get('network') or settings.get(f'network_{asset}', asset)}")
        lines.extend([
            f"{'Brüt tutar' if is_tr else 'Gross amount'}: {ucoin(uid, r.get('amount'), asset)}",
            f"{'Hizmet ücreti' if is_tr else 'Service fee'}: {ucoin(uid, r.get('fee'), asset)}",
        ])
        if asset == "TL":
            lines.extend([
                f"{'Gönderen' if is_tr else 'Sender'}: {r.get('sender_name') or '-'}",
                "",
                "Referans" if is_tr else "Reference",
                str(r.get("tx_note") or "-"),
            ])
        txid = str(r.get("txid") or "")
        if txid:
            lines.extend(["", "TXID", txid])

    elif request_type == "convert":
        source = str(r.get("from_asset") or "")
        target = str(r.get("to_asset") or "")
        lines.extend([
            ucoin(uid, r.get("net_to_amount"), target),
            "Alınacak tutar" if is_tr else "Amount to receive",
            "",
            f"{'Durum' if is_tr else 'Status'}: {status}",
            f"{'Gönderilen' if is_tr else 'Sent'}: {ucoin(uid, r.get('from_amount'), source)}",
            f"{'Hizmet ücreti' if is_tr else 'Service fee'}: {ucoin(uid, r.get('fee'), target)}",
            f"{'Parite' if is_tr else 'Pair'}: {source} → {target}",
        ])
    else:
        lines.extend([
            f"{'Durum' if is_tr else 'Status'}: {status}",
        ])

    lines.extend([
        "",
        f"{'İşlem no' if is_tr else 'Transaction ID'}: #{rid}",
        f"{'Tarih' if is_tr else 'Date'}: {created}",
    ])
    return "\n".join(lines)


def receipt_text(rid, lang_uid=None):
    r = requests_db.get(str(rid))
    uid = str(lang_uid if lang_uid is not None else (r or {}).get("user_id", ""))
    if not r:
        return t(uid, "not_found")

    is_tr = lang_of(uid) == "tr"
    request_type = str(r.get("type") or "")
    status = str(r.get("status") or "pending")
    type_names = {
        "deposit": ("Yükleme", "Deposit"),
        "withdraw": ("Çekim", "Withdrawal"),
        "convert": ("Dönüşüm", "Conversion"),
    }
    tr_kind, en_kind = type_names.get(request_type, ("İşlem", "Transaction"))
    titles = {
        "pending": (f"{tr_kind} talebiniz alındı", f"{en_kind} request received"),
        "processing": (f"{tr_kind} işleminiz yürütülüyor", f"{en_kind} is being processed"),
        "completed": (f"{tr_kind} işleminiz tamamlandı", f"{en_kind} completed"),
        "rejected": (f"{tr_kind} talebiniz reddedildi", f"{en_kind} request rejected"),
    }
    descriptions = {
        "pending": (
            "Talebiniz güvenlik kontrolü için sıraya alındı.",
            "Your request has been queued for security review.",
        ),
        "processing": (
            "İşleminiz ağ veya yönetici onayı bekliyor.",
            "Your transaction is waiting for network or administrator confirmation.",
        ),
        "completed": (
            "İşlem başarıyla sonuçlandı.",
            "The transaction was completed successfully.",
        ),
        "rejected": (
            "İşlem tamamlanmadı. Ayrılan bakiye iade sürecine alınır.",
            "The transaction was not completed. Reserved funds enter the refund process.",
        ),
    }
    marks = {"pending": "◷", "processing": "↻", "completed": "✓", "rejected": "×"}
    title_pair = titles.get(status, titles["pending"])
    description_pair = descriptions.get(status, descriptions["pending"])
    title = title_pair[0] if is_tr else title_pair[1]
    description = description_pair[0] if is_tr else description_pair[1]
    return f"{marks.get(status, '•')} {title}\n{description}\n\n{request_summary(rid, uid)}"


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
        [inline_button(t(uid, "r10_verify"), "r10:start")],
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
    assets = [asset for asset in active_balances(chat_id) if asset in WITHDRAW_ENABLED_ASSETS]
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
    send(uid, receipt_text(rid, uid), reply_keyboard(uid))
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

    if flow == "r10_verify" and step == "profile_link":
        try:
            parsed = fetch_r10_profile_mask(text)
        except ValueError as exc:
            send(chat_id, t(uid, "r10_invalid_link") if str(exc) == "invalid_link" else (t(uid, "r10_hidden_name") if str(exc) == "name_not_found" else t(uid, "r10_fetch_failed")))
            return
        except Exception as exc:
            print("R10 PROFILE FETCH ERROR:", exc)
            send(chat_id, t(uid, "r10_fetch_failed"))
            return
        if r10_profile_used_by(parsed.get("profile_slug"), uid):
            send(chat_id, t(uid, "r10_duplicate"), reply_keyboard(uid)); user_state.pop(uid, None); return
        if parsed.get("account_type") == "personal" and (int(parsed.get("months") or 0) < 6 or int(parsed.get("trades") or 0) < 5):
            send(chat_id, t(uid, "r10_rules_failed"), reply_keyboard(uid)); user_state.pop(uid, None); return
        key = "NERLO-" + secrets.token_hex(3).upper()
        users[uid]["r10_verification"] = {
            "status": "pending", "profile_url": parsed.get("profile_url", text), "profile_slug": parsed.get("profile_slug", ""),
            "masked_name": parsed["masked"], "account_type": parsed.get("account_type", "personal"),
            "first2": parsed.get("first2", ""), "last2": parsed.get("last2", ""), "key": key,
            "r10_months": int(parsed.get("months") or 0), "r10_trades": int(parsed.get("trades") or 0),
            "iban_owner": "", "created_at": now(), "approved_at": "", "approved_by": "",
        }
        save_user_profile(uid)
        add_admin_log("r10_verify_started", "R10 doğrulama başlatıldı", uid)
        user_state.pop(uid, None)
        if parsed.get("account_type") == "corporate":
            send(chat_id, t(uid, "r10_corporate_key_sent", key=key), reply_keyboard(uid))
        else:
            send(chat_id, t(uid, "r10_key_sent", name=parsed["masked"], key=key), reply_keyboard(uid))
        return

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
        if not validate_r10_name(uid, text):
            send(chat_id, t(uid, "r10_name_mismatch")); return
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
        if not validate_r10_name(uid, text):
            send(chat_id, t(uid, "r10_name_mismatch")); return
        state["name"] = text
        state["preview"] = order_summary(t(uid, "withdraw_summary_tl"), [
            (t(uid, "amount"), ucoin(uid, state["amount"], state["asset"])),
            (t(uid, "recipient_gets"), ucoin(uid, state["net_amount"], state["asset"])),
            ("IBAN", state["iban"]), (t(uid, "recipient"), state["name"]),
        ])
        require_pin(uid, state); return
    if flow == "withdraw" and step == "address":
        try:
            state["address"] = ensure_external_withdraw_address(uid, state["asset"], text)
        except (ValueError, RuntimeError) as exc:
            send(chat_id, str(exc))
            return
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
        try:
            normalized_address = ensure_external_withdraw_address(uid, state["asset"], text)
        except (ValueError, RuntimeError) as exc:
            send(chat_id, str(exc))
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
    if data == "r10:start":
        begin_r10_verification(chat_id)
        return
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
        if asset == "TL" and not r10_tl_ready(uid):
            send(chat_id, r10_required_text(uid), {"inline_keyboard": [[inline_button(t(uid, "r10_verify"), "r10:start")], [inline_button(t(uid, "cancel"), "cancel")]]})
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
        if asset == "TL" and not r10_tl_ready(uid):
            send(chat_id, r10_required_text(uid), {"inline_keyboard": [[inline_button(t(uid, "r10_verify"), "r10:start")], [inline_button(t(uid, "cancel"), "cancel")]]})
            return
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
            try:
                state["address"] = ensure_external_withdraw_address(
                    uid, state["asset"], normalized_address
                )
            except (ValueError, RuntimeError) as exc:
                send(chat_id, str(exc))
                state["step"] = "address"
                return
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
    "pool": "Havuz ve sweep işlemleri",
    "requests": "İşlem talepleri",
    "users": "Kullanıcı yönetimi",
    "approvals": "Kullanıcı onay menüsü",
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
    "complete_manual_crypto": "requests",
    "reject_request": "requests",
    "adjust_balance": "users",
    "update_user_profile": "users",
    "approve_r10_tl": "approvals",
    "revoke_r10_tl": "approvals",
    "request_r10_reverify": "approvals",
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
def home():
    if SERVICE_ROLE == "signer":
        return f"Nerlo Private Signer hazır · {BUILD_VERSION}"
    return f"Nerlo Wallet aktif ✅ · {BUILD_VERSION}"


@app.route("/version")
def version_info():
    return {
        "build_version": BUILD_VERSION,
        "service_role": SERVICE_ROLE,
        "withdraw_guard": True,
        "automatic_withdraw_assets": sorted(AUTO_WITHDRAW_ASSETS),
        "manual_withdraw_assets": sorted(MANUAL_WITHDRAW_ASSETS),
        "manual_txid_required": True,
        "panel_release": PANEL_RELEASE,
        "security_release": SECURITY_RELEASE,
        "signer_release": SIGNER_RELEASE,
        "sweep_release": SWEEP_RELEASE,
        "signer_stage": SIGNER_STAGE,
        "source_base_sha256": SOURCE_BASE_SHA256,
    }


@app.route("/health/exchange")
def exchange_health():
    snapshot = exchange_health_snapshot()
    mismatch_count = int((snapshot.get("reconciliation") or {}).get("mismatch_count") or 0)
    dead_jobs = int((snapshot.get("jobs") or {}).get("dead") or 0)
    review_sweeps = int((snapshot.get("sweeps") or {}).get("review") or 0)
    snapshot["status"] = "degraded" if mismatch_count or dead_jobs or review_sweeps else "ok"
    return snapshot


def _signer_supplied_token():
    auth_header = str(request.headers.get("Authorization") or "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("X-Signer-Token") or "").strip()


def _signer_request_row(request_id):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id,idempotency_key,asset,destination,amount,status,txid,response,last_error,created_at,updated_at
                FROM signer_requests WHERE request_id=%s
                """,
                (str(request_id),),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "request_id": row[0], "idempotency_key": row[1], "asset": row[2],
        "destination": row[3], "amount": str(row[4]), "status": row[5],
        "txid": row[6], "response": row[7] or {}, "last_error": row[8],
        "created_at": row[9].isoformat() if row[9] else "",
        "updated_at": row[10].isoformat() if row[10] else "",
    }


def _tron_private_key_int():
    value = re.sub(r"\s+", "", str(TRON_PRIVATE_KEY or ""))
    if value.lower().startswith("0x"):
        value = value[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("TRON_PRIVATE_KEY 64 karakter hexadecimal olmalıdır")
    private_int = int(value, 16)
    if not 1 <= private_int < _SECP_N:
        raise ValueError("TRON_PRIVATE_KEY secp256k1 aralığında değil")
    return private_int


def _tron_address_from_private_key():
    return _tron_address_from_private_int(_tron_private_key_int())


def _rfc6979_nonce(private_int, digest, retry=0):
    digest = bytes(digest)
    if len(digest) != 32:
        raise ValueError("İmzalanacak özet 32 byte olmalıdır")
    private_bytes = int(private_int).to_bytes(32, "big")
    seed = digest if retry == 0 else hashlib.sha256(digest + retry.to_bytes(4, "big")).digest()
    key = b"\x00" * 32
    value = b"\x01" * 32
    key = hmac.new(key, value + b"\x00" + private_bytes + seed, hashlib.sha256).digest()
    value = hmac.new(key, value, hashlib.sha256).digest()
    key = hmac.new(key, value + b"\x01" + private_bytes + seed, hashlib.sha256).digest()
    value = hmac.new(key, value, hashlib.sha256).digest()
    while True:
        value = hmac.new(key, value, hashlib.sha256).digest()
        candidate = int.from_bytes(value, "big")
        if 1 <= candidate < _SECP_N:
            return candidate
        key = hmac.new(key, value + b"\x00", hashlib.sha256).digest()
        value = hmac.new(key, value, hashlib.sha256).digest()


def _secp_sign_recoverable(private_int, digest):
    private_int = int(private_int)
    digest = bytes(digest)
    z = int.from_bytes(digest, "big")
    for retry in range(100):
        nonce = _rfc6979_nonce(private_int, digest, retry)
        point = _secp_mul(nonce)
        if point is None:
            continue
        x, y = point
        r = x % _SECP_N
        if r == 0:
            continue
        s = (pow(nonce, -1, _SECP_N) * (z + r * private_int)) % _SECP_N
        if s == 0:
            continue
        recovery_id = (y & 1) | (2 if x >= _SECP_N else 0)
        if s > _SECP_N // 2:
            s = _SECP_N - s
            recovery_id ^= 1
        return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([recovery_id])
    raise RuntimeError("ECDSA imzası üretilemedi")


def _secp_recover_public_key(digest, signature):
    digest = bytes(digest)
    signature = bytes(signature)
    if len(signature) != 65:
        raise ValueError("Recoverable signature 65 byte olmalıdır")
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    recovery_id = signature[64]
    if not (1 <= r < _SECP_N and 1 <= s < _SECP_N and 0 <= recovery_id <= 3):
        raise ValueError("ECDSA signature bileşenleri geçersiz")
    x = r + (recovery_id // 2) * _SECP_N
    if x >= _SECP_P:
        raise ValueError("ECDSA recovery X koordinatı geçersiz")
    alpha = (pow(x, 3, _SECP_P) + 7) % _SECP_P
    beta = pow(alpha, (_SECP_P + 1) // 4, _SECP_P)
    y = beta if (beta & 1) == (recovery_id & 1) else _SECP_P - beta
    point_r = (x, y)
    z = int.from_bytes(digest, "big") % _SECP_N
    r_inv = pow(r, -1, _SECP_N)
    return _secp_add(
        _secp_mul((s * r_inv) % _SECP_N, point_r),
        _secp_mul((-z * r_inv) % _SECP_N, _SECP_G),
    )


def _tron_signing_self_test():
    digest = hashlib.sha256(b"nerlo-tron-signer-self-test").digest()
    signature = _secp_sign_recoverable(1, digest)
    recovered = _secp_recover_public_key(digest, signature)
    if recovered != _SECP_G:
        raise RuntimeError("TRON ECDSA recoverable signature self-test failed")


def _tron_api_error(payload):
    payload = payload or {}
    message = str(payload.get("message") or payload.get("Error") or payload.get("error") or "").strip()
    if message and re.fullmatch(r"[0-9a-fA-F]+", message) and len(message) % 2 == 0:
        try:
            decoded = bytes.fromhex(message).decode("utf-8", "replace").strip()
            if decoded:
                message = decoded
        except ValueError:
            pass
    code = str(payload.get("code") or payload.get("result") or "").strip()
    return " · ".join(part for part in (code, message) if part) or "TRON node bilinmeyen hata döndürdü"


def _tron_api_post(path, payload, timeout=35):
    response = requests.post(
        f"{TRONGRID_BASE_URL}/{str(path).lstrip('/')}",
        json=payload,
        headers=_trongrid_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("TRON node geçersiz JSON döndürdü")
    return result


def _tron_address_payload(address):
    payload = _b58check_decode(address)
    if len(payload) != 21 or payload[0] != 0x41:
        raise ValueError("Geçersiz TRON adresi")
    return payload


def _tron_abi_address(address):
    return _tron_address_payload(address)[1:].hex().rjust(64, "0")


def _tron_units(amount, decimals=6):
    amount = D(amount)
    factor = Decimal(10) ** int(decimals)
    scaled = amount * factor
    integral = scaled.to_integral_value(rounding=ROUND_DOWN)
    if scaled != integral or integral <= 0:
        raise ValueError(f"Tutar en fazla {decimals} ondalık basamak içermelidir")
    return int(integral)


def _tron_account(address):
    return _tron_api_post("wallet/getaccount", {"address": address, "visible": True})


def _tron_trx_balance_sun(address):
    return int((_tron_account(address) or {}).get("balance") or 0)


def _tron_trc20_balance_units(address):
    result = _tron_api_post("wallet/triggerconstantcontract", {
        "owner_address": address,
        "contract_address": USDT_TRC20_CONTRACT,
        "function_selector": "balanceOf(address)",
        "parameter": _tron_abi_address(address),
        "visible": True,
    })
    values = result.get("constant_result") or []
    if not values:
        detail = _tron_api_error(result.get("result") if isinstance(result.get("result"), dict) else result)
        raise RuntimeError(f"USDT bakiyesi okunamadı: {detail}")
    return int(str(values[0]), 16)


_tron_chain_parameter_cache = {"loaded_at": 0.0, "values": {}}


def _tron_chain_parameters():
    timestamp = time.monotonic()
    cached = _tron_chain_parameter_cache.get("values") or {}
    if cached and timestamp - float(_tron_chain_parameter_cache.get("loaded_at") or 0) < 300:
        return dict(cached)
    payload = _tron_api_post("wallet/getchainparameters", {})
    values = {}
    for item in payload.get("chainParameter", []) or []:
        key = str(item.get("key") or "")
        if key:
            values[key] = int(item.get("value") or 0)
    _tron_chain_parameter_cache["loaded_at"] = timestamp
    _tron_chain_parameter_cache["values"] = dict(values)
    return values


def _tron_estimate_usdt_transfer_sun(owner, destination, token_units):
    parameter = _tron_abi_address(destination) + int(token_units).to_bytes(32, "big").hex()
    request_payload = {
        "owner_address": owner,
        "contract_address": USDT_TRC20_CONTRACT,
        "function_selector": "transfer(address,uint256)",
        "parameter": parameter,
        "visible": True,
    }
    energy_required = 0
    try:
        estimate = _tron_api_post("wallet/estimateenergy", request_payload)
        if (estimate.get("result") or {}).get("result"):
            energy_required = int(estimate.get("energy_required") or 0)
    except Exception:
        energy_required = 0
    if energy_required <= 0:
        simulation = _tron_api_post("wallet/triggerconstantcontract", request_payload)
        if not (simulation.get("result") or {}).get("result"):
            raise RuntimeError(f"USDT transfer enerji tahmini başarısız: {_tron_api_error(simulation.get('result') or simulation)}")
        energy_required = int(simulation.get("energy_used") or 0)
    if energy_required <= 0:
        return int((TRON_SWEEP_USDT_GAS_TARGET * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
    parameters = _tron_chain_parameters()
    energy_price_sun = int(parameters.get("getEnergyFee") or 100)
    estimated_sun = energy_required * energy_price_sun
    # Add 30% execution margin plus 2 TRX for bandwidth/activation variance.
    estimated_sun = int(Decimal(estimated_sun) * Decimal("1.30")) + 2_000_000
    if estimated_sun > TRON_SWEEP_USDT_FEE_LIMIT_SUN:
        raise RuntimeError("Tahmini USDT sweep maliyeti fee_limit değerini aşıyor")
    configured_floor = int((TRON_SWEEP_USDT_GAS_TARGET * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
    return max(configured_floor, estimated_sun)


def _tron_create_unsigned(asset, destination, amount):
    asset = str(asset).upper()
    owner = _tron_address_from_private_key()
    units = _tron_units(amount, 6)
    trx_balance = _tron_trx_balance_sun(owner)

    if asset == "TRX":
        reserve_sun = _tron_units(TRON_TRX_MIN_RESERVE, 6)
        if trx_balance < units + reserve_sun:
            raise RuntimeError(
                f"Sıcak cüzdanda yetersiz TRX. Gerekli: {(Decimal(units + reserve_sun) / Decimal(10**6))} TRX"
            )
        transaction = _tron_api_post("wallet/createtransaction", {
            "owner_address": owner,
            "to_address": destination,
            "amount": units,
            "visible": True,
        })
        if transaction.get("Error") or not transaction.get("txID"):
            raise RuntimeError(f"TRX işlemi oluşturulamadı: {_tron_api_error(transaction)}")
        return transaction

    if asset == "USDT":
        reserve_sun = _tron_units(TRON_USDT_MIN_TRX_RESERVE, 6)
        if trx_balance < reserve_sun:
            raise RuntimeError(
                f"USDT ağ ücretleri için en az {TRON_USDT_MIN_TRX_RESERVE} TRX sıcak cüzdan bakiyesi gerekli"
            )
        token_balance = _tron_trc20_balance_units(owner)
        if token_balance < units:
            raise RuntimeError(
                f"Sıcak cüzdanda yetersiz USDT. Mevcut: {Decimal(token_balance) / Decimal(10**6)} USDT"
            )
        trigger = _tron_api_post("wallet/triggersmartcontract", {
            "owner_address": owner,
            "contract_address": USDT_TRC20_CONTRACT,
            "function_selector": "transfer(address,uint256)",
            "parameter": _tron_abi_address(destination) + int(units).to_bytes(32, "big").hex(),
            "fee_limit": TRON_USDT_FEE_LIMIT_SUN,
            "call_value": 0,
            "visible": True,
        })
        trigger_result = trigger.get("result") or {}
        if not trigger_result.get("result") or not trigger.get("transaction"):
            raise RuntimeError(f"USDT işlemi oluşturulamadı: {_tron_api_error(trigger_result)}")
        transaction = trigger["transaction"]
        if not transaction.get("txID"):
            raise RuntimeError("USDT işlemi txID içermiyor")
        return transaction

    raise ValueError("Signer yalnızca TRX ve USDT destekliyor")


def _tron_sign_transaction(transaction, private_int=None):
    transaction = dict(transaction or {})
    txid = str(transaction.get("txID") or "").lower()
    raw_hex = str(transaction.get("raw_data_hex") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", txid) or not re.fullmatch(r"[0-9a-fA-F]+", raw_hex):
        raise RuntimeError("TRON node imzalanabilir işlem verisi döndürmedi")
    raw_bytes = bytes.fromhex(raw_hex)
    calculated = hashlib.sha256(raw_bytes).hexdigest()
    if not secrets.compare_digest(calculated, txid):
        raise RuntimeError("TRON işlem hash doğrulaması başarısız")
    signing_key = int(private_int if private_int is not None else _tron_private_key_int())
    signature = _secp_sign_recoverable(signing_key, bytes.fromhex(txid))
    if _secp_recover_public_key(bytes.fromhex(txid), signature) != _secp_mul(signing_key):
        raise RuntimeError("TRON işlem imzası doğrulanamadı")
    transaction["signature"] = [signature.hex()]
    return transaction


def _tron_broadcast_signed(transaction):
    result = _tron_api_post("wallet/broadcasttransaction", transaction, timeout=45)
    if result.get("result") is True:
        return result
    detail = _tron_api_error(result)
    if "DUP_TRANSACTION_ERROR" in detail.upper() or "DUPLICATE" in detail.upper():
        return {**result, "result": True, "duplicate": True}
    raise RuntimeError(f"TRON broadcast reddedildi: {detail}")


def _signer_callback(payload):
    if not SIGNER_CALLBACK_URL:
        raise RuntimeError("SIGNER_CALLBACK_URL tanımlı değil")
    response = requests.post(
        SIGNER_CALLBACK_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {EXCHANGE_INTERNAL_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "Nerlo-Private-Signer/2.0",
        },
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Ana uygulama callback işlemini reddetti: {body}")
    return body


def _signer_merge_response(request_id, status=None, txid=None, response_patch=None, last_error=None, confirmed=False):
    patch = dict(response_patch or {})
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signer_requests
                SET status=COALESCE(%s,status),
                    txid=COALESCE(NULLIF(%s,''),txid),
                    response=COALESCE(response,'{}'::jsonb) || %s,
                    last_error=COALESCE(%s,last_error),
                    updated_at=NOW(),
                    broadcast_at=CASE WHEN COALESCE(%s,status) IN ('broadcast','broadcast_unknown') THEN COALESCE(broadcast_at,NOW()) ELSE broadcast_at END,
                    confirmed_at=CASE WHEN %s THEN COALESCE(confirmed_at,NOW()) ELSE confirmed_at END
                WHERE request_id=%s
                """,
                (status, txid or "", Jsonb(patch), last_error, status, bool(confirmed), str(request_id)),
            )
        conn.commit()


def _signer_confirm_one(record):
    request_id = str(record["request_id"])
    asset = str(record["asset"])
    txid = str(record["txid"] or "")
    status = str(record["status"] or "")
    response_data = dict(record.get("response") or {})

    if status in ("confirmed", "failed", "expired_review"):
        if response_data.get("callback_sent"):
            return
        callback_status = "confirmed" if status == "confirmed" else "failed"
        refund = bool(response_data.get("refund", False))
        payload = {
            "request_id": request_id,
            "status": callback_status,
            "txid": txid,
            "refund": refund,
            "reason": response_data.get("failure_reason", ""),
        }
        try:
            _signer_callback(payload)
            _signer_merge_response(request_id, response_patch={"callback_sent": True, "callback_sent_at": now(), "callback_error": ""})
        except Exception as exc:
            _signer_merge_response(request_id, response_patch={"callback_sent": False, "callback_error": str(exc)[:500]}, last_error=str(exc)[:500])
        return

    if not txid:
        return
    info = _tron_api_post("walletsolidity/gettransactioninfobyid", {"value": txid})
    if info.get("id"):
        receipt_result = str((info.get("receipt") or {}).get("result") or "").upper()
        if receipt_result and receipt_result != "SUCCESS":
            reason = f"Zincir işlemi başarısız: {receipt_result}"
            _signer_merge_response(
                request_id,
                status="failed",
                response_patch={"chain_info": info, "refund": True, "failure_reason": reason, "callback_sent": False},
                last_error=reason,
            )
        else:
            _signer_merge_response(
                request_id,
                status="confirmed",
                response_patch={"chain_info": info, "callback_sent": False},
                last_error="",
                confirmed=True,
            )
        return

    signed_transaction = response_data.get("signed_transaction") or {}
    expiration = int((signed_transaction.get("raw_data") or {}).get("expiration") or 0)
    if expiration and int(time.time() * 1000) > expiration + SIGNER_EXPIRY_GRACE_SECONDS * 1000:
        fullnode_tx = _tron_api_post("wallet/gettransactionbyid", {"value": txid})
        if not fullnode_tx.get("txID"):
            reason = "İşlem süresi doldu ancak zincir sonucu kesinleşmedi; otomatik iade yapılmadı"
            _signer_merge_response(
                request_id,
                status="expired_review",
                response_patch={"refund": False, "failure_reason": reason, "callback_sent": False},
                last_error=reason,
            )


def signer_confirmation_once():
    if SERVICE_ROLE != "signer" or not SIGNER_BROADCAST_ENABLED:
        return
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id,asset,status,txid,response
                FROM signer_requests
                WHERE status IN ('broadcast','broadcast_unknown','confirmed','failed','expired_review')
                ORDER BY updated_at ASC
                LIMIT 100
                """
            )
            rows = cur.fetchall()
    for row in rows:
        try:
            _signer_confirm_one({
                "request_id": row[0], "asset": row[1], "status": row[2],
                "txid": row[3], "response": row[4] or {},
            })
        except Exception as exc:
            print("SIGNER CONFIRMATION ERROR:", row[0], exc)
            _signer_merge_response(str(row[0]), last_error=str(exc)[:500])



def _tron_create_trx_unsigned_for_owner(owner, destination, amount_sun):
    amount_sun = int(amount_sun)
    if amount_sun <= 0:
        raise ValueError("TRX sweep tutarı sıfır olamaz")
    transaction = _tron_api_post("wallet/createtransaction", {
        "owner_address": owner,
        "to_address": destination,
        "amount": amount_sun,
        "visible": True,
    })
    if transaction.get("Error") or not transaction.get("txID"):
        raise RuntimeError(f"TRX sweep işlemi oluşturulamadı: {_tron_api_error(transaction)}")
    return transaction


def _tron_create_usdt_unsigned_for_owner(owner, destination, token_units):
    token_units = int(token_units)
    if token_units <= 0:
        raise ValueError("USDT sweep tutarı sıfır olamaz")
    trigger = _tron_api_post("wallet/triggersmartcontract", {
        "owner_address": owner,
        "contract_address": USDT_TRC20_CONTRACT,
        "function_selector": "transfer(address,uint256)",
        "parameter": _tron_abi_address(destination) + token_units.to_bytes(32, "big").hex(),
        "fee_limit": TRON_SWEEP_USDT_FEE_LIMIT_SUN,
        "call_value": 0,
        "visible": True,
    })
    trigger_result = trigger.get("result") or {}
    if not trigger_result.get("result") or not trigger.get("transaction"):
        raise RuntimeError(f"USDT sweep işlemi oluşturulamadı: {_tron_api_error(trigger_result)}")
    transaction = trigger["transaction"]
    if not transaction.get("txID"):
        raise RuntimeError("USDT sweep işlemi txID içermiyor")
    return transaction


def _tron_transaction_state(txid):
    txid = str(txid or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", txid):
        return {"state": "missing", "info": {}, "transaction": {}}
    info = _tron_api_post("walletsolidity/gettransactioninfobyid", {"value": txid})
    if info.get("id"):
        receipt = str((info.get("receipt") or {}).get("result") or "SUCCESS").upper()
        if receipt and receipt != "SUCCESS":
            return {"state": "failed", "reason": receipt, "info": info, "transaction": {}}
        return {"state": "confirmed", "info": info, "transaction": {}}
    transaction = _tron_api_post("wallet/gettransactionbyid", {"value": txid})
    if transaction.get("txID"):
        return {"state": "pending", "info": info, "transaction": transaction}
    return {"state": "missing", "info": info, "transaction": transaction}


def _signed_transaction_expired(transaction):
    expiration = int(((transaction or {}).get("raw_data") or {}).get("expiration") or 0)
    return bool(expiration and int(time.time() * 1000) > expiration + SIGNER_EXPIRY_GRACE_SECONDS * 1000)


def _sweep_discover_candidates():
    if not TRON_POOL_ADDRESS:
        return 0
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signer_sweeps(
                    event_id,user_id,asset,source_address,destination_address,
                    derivation_index,derivation_path,status,payload
                )
                SELECT e.id,e.user_id,e.asset,e.address,%s,a.derivation_index,a.derivation_path,'queued',
                       jsonb_build_object('deposit_txid',e.txid,'event_index',e.event_index,'event_amount',e.amount,'generation',e.generation)
                FROM exchange_chain_events e
                JOIN exchange_addresses a ON a.chain='TRON' AND a.address=e.address AND a.user_id=e.user_id
                WHERE e.chain='TRON' AND e.asset IN ('TRX','USDT') AND e.status='credited'
                  AND a.status='active' AND a.source='xpub'
                  AND e.address<>%s
                  AND NOT EXISTS (SELECT 1 FROM signer_sweeps s WHERE s.event_id=e.id)
                ORDER BY e.id
                LIMIT %s
                ON CONFLICT(event_id) DO NOTHING
                RETURNING sweep_id
                """,
                (TRON_POOL_ADDRESS, TRON_POOL_ADDRESS, TRON_SWEEP_BATCH_LIMIT),
            )
            inserted = len(cur.fetchall())
        conn.commit()
    return inserted


def _sweep_row(sweep_id):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sweep_id,event_id,user_id,asset,source_address,destination_address,
                       derivation_index,derivation_path,amount,status,funding_txid,sweep_txid,
                       cleanup_txid,attempts,payload,response,last_error,available_at
                FROM signer_sweeps WHERE sweep_id=%s
                """,
                (int(sweep_id),),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "sweep_id": row[0], "event_id": row[1], "user_id": row[2], "asset": row[3],
        "source_address": row[4], "destination_address": row[5], "derivation_index": row[6],
        "derivation_path": row[7], "amount": D(row[8]), "status": row[9],
        "funding_txid": row[10], "sweep_txid": row[11], "cleanup_txid": row[12],
        "attempts": int(row[13] or 0), "payload": row[14] or {}, "response": row[15] or {},
        "last_error": row[16], "available_at": row[17],
    }


def _sweep_update(sweep_id, *, status=None, amount=None, funding_txid=None, sweep_txid=None,
                  cleanup_txid=None, response_patch=None, last_error=None, delay_seconds=0,
                  funded=False, broadcast=False, cleanup=False, confirmed=False, reset_attempts=False):
    patch = dict(response_patch or {})
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signer_sweeps
                SET status=COALESCE(%s,status),
                    amount=COALESCE(%s,amount),
                    funding_txid=COALESCE(NULLIF(%s,''),funding_txid),
                    sweep_txid=COALESCE(NULLIF(%s,''),sweep_txid),
                    cleanup_txid=COALESCE(NULLIF(%s,''),cleanup_txid),
                    response=COALESCE(response,'{}'::jsonb) || %s,
                    last_error=COALESCE(%s,last_error),
                    available_at=NOW()+(%s * INTERVAL '1 second'),
                    locked_at=NULL,locked_by='',updated_at=NOW(),
                    attempts=CASE WHEN %s THEN 0 ELSE attempts END,
                    funded_at=CASE WHEN %s THEN COALESCE(funded_at,NOW()) ELSE funded_at END,
                    broadcast_at=CASE WHEN %s THEN COALESCE(broadcast_at,NOW()) ELSE broadcast_at END,
                    cleanup_at=CASE WHEN %s THEN COALESCE(cleanup_at,NOW()) ELSE cleanup_at END,
                    confirmed_at=CASE WHEN %s THEN COALESCE(confirmed_at,NOW()) ELSE confirmed_at END
                WHERE sweep_id=%s
                """,
                (
                    status, amount, funding_txid or "", sweep_txid or "", cleanup_txid or "",
                    Jsonb(patch), last_error, int(delay_seconds), bool(reset_attempts), bool(funded),
                    bool(broadcast), bool(cleanup), bool(confirmed), int(sweep_id),
                ),
            )
        conn.commit()


def _sweep_fail(record, exc):
    attempts = int(record.get("attempts") or 0) + 1
    error = ("".join(traceback.format_exception_only(type(exc), exc))).strip()[:1500]
    terminal = attempts >= TRON_SWEEP_MAX_RETRIES
    delay = min(3600, max(15, 2 ** min(attempts, 10)))
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signer_sweeps
                SET attempts=%s,status=CASE WHEN %s THEN 'review' ELSE status END,
                    last_error=%s,available_at=NOW()+(%s * INTERVAL '1 second'),
                    locked_at=NULL,locked_by='',updated_at=NOW()
                WHERE sweep_id=%s
                """,
                (attempts, terminal, error, delay, int(record["sweep_id"])),
            )
        conn.commit()
    print("TRON SWEEP ERROR:", record.get("sweep_id"), record.get("source_address"), error)


def _sweep_mark_event(record, status, txid=""):
    details = {
        "sweep_status": status,
        "sweep_txid": str(txid or record.get("sweep_txid") or ""),
        "sweep_id": int(record["sweep_id"]),
        "pool_address": record.get("destination_address") or TRON_POOL_ADDRESS,
        "swept_at": now() if status == "confirmed" else "",
    }
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exchange_chain_events SET raw=COALESCE(raw,'{}'::jsonb)||%s,updated_at=NOW() WHERE id=%s",
                (Jsonb(details), int(record["event_id"])),
            )
            cur.execute(
                """
                UPDATE exchange_requests
                SET payload=COALESCE(payload,'{}'::jsonb)||%s,updated_at=NOW()
                WHERE request_id=(SELECT request_id FROM exchange_chain_events WHERE id=%s)
                """,
                (Jsonb(details), int(record["event_id"])),
            )
        conn.commit()


def _sweep_is_covered(record):
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM signer_sweeps
                WHERE source_address=%s AND asset=%s AND sweep_id<>%s
                  AND status IN ('sweep_signed','broadcast','cleanup_pending','cleanup_signed','cleanup_broadcast','confirmed')
                  AND created_at>=NOW()-INTERVAL '7 days'
                LIMIT 1
                """,
                (record["source_address"], record["asset"], int(record["sweep_id"])),
            )
            return cur.fetchone() is not None


def _sweep_prepare_source_transaction(record, private_int):
    source = record["source_address"]
    destination = record["destination_address"]
    asset = record["asset"]
    if secrets.compare_digest(source, destination):
        raise ValueError("Sweep kaynak ve havuz adresi aynı olamaz")

    if asset == "TRX":
        balance_sun = _tron_trx_balance_sun(source)
        reserve_sun = int((TRON_SWEEP_TRX_RESERVE * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
        min_sun = int((TRON_SWEEP_MIN_TRX * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
        send_sun = max(0, balance_sun - reserve_sun)
        if send_sun < min_sun:
            terminal = "covered" if _sweep_is_covered(record) else "below_minimum"
            _sweep_update(record["sweep_id"], status=terminal, response_patch={"source_trx_sun": balance_sun}, last_error="")
            _sweep_mark_event(record, terminal)
            return
        unsigned = _tron_create_trx_unsigned_for_owner(source, destination, send_sun)
        signed = _tron_sign_transaction(unsigned, private_int)
        amount = Decimal(send_sun) / Decimal(10 ** 6)
        _sweep_update(
            record["sweep_id"], status="sweep_signed", amount=amount, sweep_txid=signed["txID"],
            response_patch={"sweep_signed_transaction": signed, "source_trx_sun": balance_sun},
            last_error="", reset_attempts=True,
        )
        return

    token_units = _tron_trc20_balance_units(source)
    min_units = int((TRON_SWEEP_MIN_USDT * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
    if token_units < min_units:
        terminal = "covered" if _sweep_is_covered(record) else "below_minimum"
        _sweep_update(record["sweep_id"], status=terminal, response_patch={"source_usdt_units": token_units}, last_error="")
        _sweep_mark_event(record, terminal)
        return

    source_trx_sun = _tron_trx_balance_sun(source)
    gas_target_sun = _tron_estimate_usdt_transfer_sun(source, destination, token_units)
    if source_trx_sun < gas_target_sun:
        topup_sun = gas_target_sun - source_trx_sun
        topup_amount = Decimal(topup_sun) / Decimal(10 ** 6)
        unsigned = _tron_create_unsigned("TRX", source, topup_amount)
        signed = _tron_sign_transaction(unsigned)
        _sweep_update(
            record["sweep_id"], status="funding_signed", funding_txid=signed["txID"],
            response_patch={
                "funding_signed_transaction": signed,
                "funding_amount_trx": str(topup_amount),
                "source_trx_before_sun": source_trx_sun,
                "source_usdt_units": token_units,
            },
            last_error="", reset_attempts=True,
        )
        return

    unsigned = _tron_create_usdt_unsigned_for_owner(source, destination, token_units)
    signed = _tron_sign_transaction(unsigned, private_int)
    amount = Decimal(token_units) / Decimal(10 ** 6)
    _sweep_update(
        record["sweep_id"], status="sweep_signed", amount=amount, sweep_txid=signed["txID"],
        response_patch={"sweep_signed_transaction": signed, "source_usdt_units": token_units},
        last_error="", reset_attempts=True,
    )


def _sweep_broadcast_stored(record, response_key, txid_field, next_status, timestamp_flag=None):
    transaction = (record.get("response") or {}).get(response_key) or {}
    if not transaction.get("txID"):
        raise RuntimeError(f"{response_key} bulunamadı")
    _tron_broadcast_signed(transaction)
    kwargs = {
        "status": next_status,
        txid_field: transaction["txID"],
        "response_patch": {f"{response_key}_broadcast_at": now()},
        "last_error": "",
        "reset_attempts": True,
    }
    if timestamp_flag:
        kwargs[timestamp_flag] = True
    _sweep_update(record["sweep_id"], **kwargs)


def _sweep_wait_transaction(record, txid, signed_key, success_status, failed_label):
    state = _tron_transaction_state(txid)
    if state["state"] == "confirmed":
        _sweep_update(
            record["sweep_id"], status=success_status,
            response_patch={f"{failed_label}_chain_info": state.get("info") or {}},
            last_error="", reset_attempts=True,
            funded=success_status == "funded",
        )
        return True
    if state["state"] == "failed":
        reason = f"{failed_label} zincir işlemi başarısız: {state.get('reason') or 'UNKNOWN'}"
        _sweep_update(record["sweep_id"], status="review", response_patch={f"{failed_label}_chain_info": state.get("info") or {}}, last_error=reason)
        _sweep_mark_event(record, "review", txid)
        return False
    signed = (record.get("response") or {}).get(signed_key) or {}
    if state["state"] == "missing" and _signed_transaction_expired(signed):
        reason = f"{failed_label} işleminin süresi doldu; otomatik yeniden üretim güvenlik nedeniyle durduruldu"
        _sweep_update(record["sweep_id"], status="review", last_error=reason)
        _sweep_mark_event(record, "review", txid)
        return False
    _sweep_update(record["sweep_id"], delay_seconds=TRON_SWEEP_POLL_SECONDS)
    return False


def _sweep_prepare_cleanup(record, private_int):
    source = record["source_address"]
    balance_sun = _tron_trx_balance_sun(source)
    reserve_sun = int((TRON_SWEEP_TRX_RESERVE * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
    min_sun = int((TRON_SWEEP_MIN_TRX * Decimal(10 ** 6)).to_integral_value(rounding=ROUND_DOWN))
    send_sun = max(0, balance_sun - reserve_sun)
    if send_sun < min_sun:
        _sweep_update(record["sweep_id"], status="confirmed", response_patch={"cleanup_source_trx_sun": balance_sun}, last_error="", confirmed=True)
        _sweep_mark_event(record, "confirmed", record.get("sweep_txid"))
        return
    unsigned = _tron_create_trx_unsigned_for_owner(source, record["destination_address"], send_sun)
    signed = _tron_sign_transaction(unsigned, private_int)
    _sweep_update(
        record["sweep_id"], status="cleanup_signed", cleanup_txid=signed["txID"],
        response_patch={"cleanup_signed_transaction": signed, "cleanup_amount_trx": str(Decimal(send_sun) / Decimal(10 ** 6))},
        last_error="", reset_attempts=True,
    )


def _process_sweep_record(record):
    private_int = _sweep_private_for_address(
        record.get("derivation_index"), record.get("derivation_path"), record.get("source_address")
    )
    status = str(record.get("status") or "queued")
    if status == "queued":
        _sweep_prepare_source_transaction(record, private_int)
    elif status == "funding_signed":
        _sweep_broadcast_stored(record, "funding_signed_transaction", "funding_txid", "funding_broadcast")
    elif status == "funding_broadcast":
        _sweep_wait_transaction(record, record.get("funding_txid"), "funding_signed_transaction", "funded", "funding")
    elif status == "funded":
        _sweep_prepare_source_transaction(record, private_int)
    elif status == "sweep_signed":
        _sweep_broadcast_stored(record, "sweep_signed_transaction", "sweep_txid", "broadcast", "broadcast")
    elif status == "broadcast":
        state = _tron_transaction_state(record.get("sweep_txid"))
        if state["state"] == "confirmed":
            if record.get("asset") == "USDT":
                _sweep_update(record["sweep_id"], status="cleanup_pending", response_patch={"sweep_chain_info": state.get("info") or {}}, last_error="", reset_attempts=True)
            else:
                _sweep_update(record["sweep_id"], status="confirmed", response_patch={"sweep_chain_info": state.get("info") or {}}, last_error="", confirmed=True, reset_attempts=True)
                _sweep_mark_event(record, "confirmed", record.get("sweep_txid"))
        elif state["state"] == "failed":
            reason = f"Sweep zincir işlemi başarısız: {state.get('reason') or 'UNKNOWN'}"
            _sweep_update(record["sweep_id"], status="review", response_patch={"sweep_chain_info": state.get("info") or {}}, last_error=reason)
            _sweep_mark_event(record, "review", record.get("sweep_txid"))
        else:
            signed = (record.get("response") or {}).get("sweep_signed_transaction") or {}
            if state["state"] == "missing" and _signed_transaction_expired(signed):
                reason = "Sweep işleminin süresi doldu; zincir sonucu belirsiz olduğu için manuel inceleme gerekli"
                _sweep_update(record["sweep_id"], status="review", last_error=reason)
                _sweep_mark_event(record, "review", record.get("sweep_txid"))
            else:
                _sweep_update(record["sweep_id"], delay_seconds=TRON_SWEEP_POLL_SECONDS)
    elif status == "cleanup_pending":
        _sweep_prepare_cleanup(record, private_int)
    elif status == "cleanup_signed":
        _sweep_broadcast_stored(record, "cleanup_signed_transaction", "cleanup_txid", "cleanup_broadcast", "cleanup")
    elif status == "cleanup_broadcast":
        state = _tron_transaction_state(record.get("cleanup_txid"))
        if state["state"] == "confirmed":
            _sweep_update(record["sweep_id"], status="confirmed", response_patch={"cleanup_chain_info": state.get("info") or {}}, last_error="", confirmed=True, reset_attempts=True)
            _sweep_mark_event(record, "confirmed", record.get("sweep_txid"))
        elif state["state"] == "failed":
            reason = f"USDT sonrası TRX geri toplama başarısız: {state.get('reason') or 'UNKNOWN'}"
            _sweep_update(record["sweep_id"], status="review", response_patch={"cleanup_chain_info": state.get("info") or {}}, last_error=reason)
            _sweep_mark_event(record, "review", record.get("sweep_txid"))
        else:
            signed = (record.get("response") or {}).get("cleanup_signed_transaction") or {}
            if state["state"] == "missing" and _signed_transaction_expired(signed):
                reason = "TRX geri toplama işleminin süresi doldu; manuel inceleme gerekli"
                _sweep_update(record["sweep_id"], status="review", last_error=reason)
                _sweep_mark_event(record, "review", record.get("sweep_txid"))
            else:
                _sweep_update(record["sweep_id"], delay_seconds=TRON_SWEEP_POLL_SECONDS)


def _refresh_tron_pool_meta():
    snapshot = {
        "address": TRON_POOL_ADDRESS,
        "checked_at": now(),
        "trx": "0",
        "usdt": "0",
        "error": "",
        "sweep_enabled": TRON_SWEEP_ENABLED,
    }
    if TRON_POOL_ADDRESS and TRONGRID_KEY:
        try:
            snapshot["trx"] = str(Decimal(_tron_trx_balance_sun(TRON_POOL_ADDRESS)) / Decimal(10 ** 6))
            snapshot["usdt"] = str(Decimal(_tron_trc20_balance_units(TRON_POOL_ADDRESS)) / Decimal(10 ** 6))
        except Exception as exc:
            snapshot["error"] = str(exc)[:500]
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,COUNT(*) FROM signer_sweeps GROUP BY status")
            snapshot["sweeps"] = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute(
                """
                INSERT INTO exchange_meta(meta_key,meta_value) VALUES ('tron-pool-snapshot',%s)
                ON CONFLICT(meta_key) DO UPDATE SET meta_value=EXCLUDED.meta_value,updated_at=NOW()
                """,
                (Jsonb(snapshot),),
            )
        conn.commit()
    return snapshot


def signer_sweep_once():
    if SERVICE_ROLE != "signer" or not SIGNER_BROADCAST_ENABLED:
        return
    if not TRON_POOL_ADDRESS:
        return
    if not TRON_SWEEP_ENABLED:
        _refresh_tron_pool_meta()
        return
    _sweep_discover_candidates()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.sweep_id FROM signer_sweeps s
                WHERE s.status IN ('queued','funding_signed','funding_broadcast','funded','sweep_signed','broadcast','cleanup_pending','cleanup_signed','cleanup_broadcast')
                  AND s.available_at<=NOW()
                  AND NOT EXISTS (
                      SELECT 1 FROM signer_sweeps older
                      WHERE older.source_address=s.source_address
                        AND older.sweep_id<s.sweep_id
                        AND older.status NOT IN ('confirmed','covered','below_minimum')
                  )
                ORDER BY s.sweep_id
                LIMIT %s
                """,
                (TRON_SWEEP_BATCH_LIMIT,),
            )
            sweep_ids = [row[0] for row in cur.fetchall()]
    for sweep_id in sweep_ids:
        record = _sweep_row(sweep_id)
        if not record:
            continue
        lock_key = f"tron-sweep-source:{record.get('source_address') or sweep_id}"
        lock_conn = _db_connect()
        lock_conn.autocommit = True
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (lock_key,))
            record = _sweep_row(sweep_id)
            if not record or record.get("status") in ("confirmed", "review", "covered", "below_minimum"):
                continue
            try:
                _process_sweep_record(record)
            except Exception as exc:
                _sweep_fail(record, exc)
        finally:
            try:
                with lock_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (lock_key,))
            except Exception:
                pass
            lock_conn.close()
    _refresh_tron_pool_meta()


def tron_pool_snapshot():
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT meta_value FROM exchange_meta WHERE meta_key='tron-pool-snapshot'")
            row = cur.fetchone()
            snapshot = dict(row[0] or {}) if row else {}
            cur.execute("SELECT status,COUNT(*) FROM signer_sweeps GROUP BY status")
            counts = {status: count for status, count in cur.fetchall()}
            cur.execute(
                """
                SELECT sweep_id,event_id,user_id,asset,source_address,amount,status,
                       funding_txid,sweep_txid,cleanup_txid,last_error,created_at,updated_at
                FROM signer_sweeps ORDER BY sweep_id DESC LIMIT 80
                """
            )
            rows = cur.fetchall()
    snapshot.setdefault("address", TRON_POOL_ADDRESS)
    snapshot["sweeps"] = counts
    snapshot["recent"] = [
        {
            "sweep_id": r[0], "event_id": r[1], "user_id": r[2], "asset": r[3],
            "source_address": r[4], "amount": str(r[5]), "status": r[6],
            "funding_txid": r[7], "sweep_txid": r[8], "cleanup_txid": r[9],
            "last_error": r[10],
            "created_at": r[11].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r[11], "strftime") else str(r[11]),
            "updated_at": r[12].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r[12], "strftime") else str(r[12]),
        }
        for r in rows
    ]
    return snapshot


def _signer_runtime_state():
    private_key_loaded = False
    derived_address = ""
    key_error = ""
    try:
        derived_address = _tron_address_from_private_key()
        private_key_loaded = True
    except Exception as exc:
        key_error = str(exc)
    address_matches = bool(
        private_key_loaded and TRON_HOT_WALLET_ADDRESS
        and secrets.compare_digest(derived_address, TRON_HOT_WALLET_ADDRESS)
    )
    ready = bool(
        SIGNER_BROADCAST_ENABLED and private_key_loaded and address_matches
        and TRONGRID_KEY and USDT_TRC20_CONTRACT and SIGNER_CALLBACK_URL
    )

    sweep_key_loaded = False
    sweep_xpub_matches = False
    sweep_key_error = ""
    if TRON_SWEEP_ENABLED:
        try:
            _sweep_account_private_node()
            sweep_key_loaded = True
            sweep_xpub_matches = _sweep_account_xpub_matches()
        except Exception as exc:
            sweep_key_error = str(exc)
    sweep_ready = bool(
        TRON_SWEEP_ENABLED and ready and sweep_key_loaded and sweep_xpub_matches
        and TRON_POOL_ADDRESS and secrets.compare_digest(TRON_POOL_ADDRESS, derived_address)
    )
    return {
        "private_key_loaded": private_key_loaded,
        "derived_address": derived_address,
        "address_matches": address_matches,
        "key_error": key_error,
        "ready": ready,
        "sweep_enabled": TRON_SWEEP_ENABLED,
        "sweep_key_loaded": sweep_key_loaded,
        "sweep_xpub_matches": sweep_xpub_matches,
        "sweep_key_error": sweep_key_error,
        "sweep_ready": sweep_ready,
    }


@app.route("/health/signer")
def signer_health():
    if SERVICE_ROLE != "signer":
        return {"ok": False, "error": "not_found"}, 404
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,COUNT(*) FROM signer_requests GROUP BY status")
            request_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT status,COUNT(*) FROM signer_sweeps GROUP BY status")
            sweep_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT meta_value FROM exchange_meta WHERE meta_key='tron-pool-snapshot'")
            pool_row = cur.fetchone()
            pool_snapshot = dict(pool_row[0] or {}) if pool_row else {}
    runtime = _signer_runtime_state()
    return {
        "ok": True,
        "service_role": SERVICE_ROLE,
        "build_version": BUILD_VERSION,
        "signer_release": SIGNER_RELEASE,
        "sweep_release": SWEEP_RELEASE,
        "stage": SIGNER_STAGE,
        "supported_assets": sorted(SIGNER_SUPPORTED_ASSETS),
        "network": "TRON mainnet",
        "broadcast_enabled": SIGNER_BROADCAST_ENABLED,
        "private_keys_loaded": runtime["private_key_loaded"],
        "hot_wallet_address": runtime["derived_address"] if runtime["private_key_loaded"] else "",
        "address_matches": runtime["address_matches"],
        "ready": runtime["ready"],
        "sweep_enabled": runtime["sweep_enabled"],
        "sweep_key_loaded": runtime["sweep_key_loaded"],
        "sweep_xpub_matches": runtime["sweep_xpub_matches"],
        "sweep_ready": runtime["sweep_ready"],
        "pool": pool_snapshot,
        "requests": request_counts,
        "sweeps": sweep_counts,
    }


@app.route("/internal/signer/withdraw", methods=["POST"])
def signer_prepare_withdrawal():
    if SERVICE_ROLE != "signer":
        return {"ok": False, "error": "not_found"}, 404
    supplied = _signer_supplied_token()
    if not supplied or not secrets.compare_digest(supplied, WITHDRAW_SIGNER_TOKEN):
        return {"ok": False, "error": "unauthorized"}, 401
    if not SIGNER_BROADCAST_ENABLED:
        return {"ok": False, "error": "broadcast_disabled"}, 503

    runtime = _signer_runtime_state()
    if not runtime["ready"]:
        return {"ok": False, "error": "signer_not_ready"}, 503

    payload = request.get_json(silent=True) or {}
    request_id = str(payload.get("request_id") or "").strip()
    idempotency_key = str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or "").strip()
    asset = str(payload.get("asset") or "").strip().upper()
    destination = str(payload.get("address") or "").strip()
    amount = D(payload.get("amount"))

    if not re.fullmatch(r"[A-Za-z0-9_-]{3,100}", request_id):
        return {"ok": False, "error": "invalid_request_id"}, 400
    expected_idempotency = f"withdraw:{request_id}"
    if not idempotency_key or not secrets.compare_digest(idempotency_key, expected_idempotency):
        return {"ok": False, "error": "invalid_idempotency_key"}, 400
    if asset not in SIGNER_SUPPORTED_ASSETS:
        return {"ok": False, "error": "unsupported_asset", "supported_assets": sorted(SIGNER_SUPPORTED_ASSETS)}, 400
    if amount <= 0:
        return {"ok": False, "error": "invalid_amount"}, 400

    try:
        normalized_destination = ensure_external_withdraw_address(
            str(payload.get("user_id") or ""), asset, destination
        )
    except ValueError as exc:
        return {"ok": False, "error": "invalid_destination", "detail": str(exc)}, 400
    except RuntimeError as exc:
        return {"ok": False, "error": "destination_check_unavailable", "detail": str(exc)}, 503

    lock_conn = _db_connect()
    lock_conn.autocommit = True
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (f"signer:{request_id}",))
            cur.execute(
                """
                SELECT idempotency_key,asset,destination,amount,status,txid,response
                FROM signer_requests WHERE request_id=%s
                """,
                (request_id,),
            )
            existing = cur.fetchone()
            if existing:
                same_request = (
                    secrets.compare_digest(str(existing[0]), idempotency_key)
                    and str(existing[1]) == asset
                    and secrets.compare_digest(_address_compare_key(asset, existing[2]), _address_compare_key(asset, normalized_destination))
                    and D(existing[3]) == amount
                )
                if not same_request:
                    return {"ok": False, "error": "idempotency_conflict"}, 409
                existing_status = str(existing[4])
                existing_txid = str(existing[5] or "")
                existing_response = dict(existing[6] or {})
                if existing_status in ("broadcast", "confirmed") and existing_txid:
                    return {
                        "ok": True, "request_id": request_id, "status": existing_status,
                        "txid": existing_txid, "idempotent_replay": True,
                        "confirmed": existing_status == "confirmed",
                    }
                if existing_status == "expired_review":
                    return {"ok": False, "error": "manual_review_required", "txid": existing_txid}, 409
            else:
                existing_status = "prepared"
                existing_txid = ""
                existing_response = {}
                cur.execute(
                    """
                    INSERT INTO signer_requests(
                        request_id,idempotency_key,asset,destination,amount,status,payload,response,last_error
                    ) VALUES (%s,%s,%s,%s,%s,'prepared',%s,%s,'')
                    """,
                    (request_id, idempotency_key, asset, normalized_destination, amount, Jsonb(payload), Jsonb({"stage": SIGNER_STAGE})),
                )

        signed_transaction = existing_response.get("signed_transaction")
        txid = existing_txid
        if not signed_transaction:
            unsigned_transaction = _tron_create_unsigned(asset, normalized_destination, amount)
            signed_transaction = _tron_sign_transaction(unsigned_transaction)
            txid = str(signed_transaction.get("txID") or "")
            _signer_merge_response(
                request_id,
                status="signed",
                txid=txid,
                response_patch={
                    "stage": SIGNER_STAGE,
                    "signed_transaction": signed_transaction,
                    "signed_at": now(),
                    "hot_wallet_address": runtime["derived_address"],
                },
                last_error="",
            )

        try:
            broadcast_result = _tron_broadcast_signed(signed_transaction)
        except requests.RequestException as exc:
            _signer_merge_response(
                request_id,
                status="broadcast_unknown",
                txid=txid,
                response_patch={"broadcast_error": str(exc)[:500]},
                last_error=str(exc)[:500],
            )
            return {
                "ok": False, "request_id": request_id, "status": "broadcast_unknown",
                "txid": txid, "error": "broadcast_result_unknown",
            }, 503
        except Exception as exc:
            _signer_merge_response(
                request_id,
                status="failed",
                txid=txid,
                response_patch={"broadcast_error": str(exc)[:500], "refund": False, "callback_sent": False},
                last_error=str(exc)[:500],
            )
            return {"ok": False, "request_id": request_id, "status": "failed", "txid": txid, "error": str(exc)}, 502

        _signer_merge_response(
            request_id,
            status="broadcast",
            txid=txid,
            response_patch={"broadcast_result": broadcast_result, "broadcast_at": now(), "callback_sent": False},
            last_error="",
        )
        return {
            "ok": True,
            "request_id": request_id,
            "status": "broadcast",
            "txid": txid,
            "confirmed": False,
            "idempotent_replay": bool(existing),
        }
    finally:
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (f"signer:{request_id}",))
        except Exception:
            pass
        lock_conn.close()


_tron_signing_self_test()


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
    <meta name='color-scheme' content='dark'><title>Nerlo Treasury Control</title><style>
    :root{{--ink:#f4f7fb;--muted:#8b98a9;--line:#202b3a;--panel:#0d141e;--panel2:#111b28;--bg:#06090e;
    --blue:#6aa9ff;--mint:#69dfc3;--amber:#efc36b;--danger:#ff7890;--success:#5ed29b}}
    *{{box-sizing:border-box}}html,body{{min-height:100%}}body{{margin:0;color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:
    radial-gradient(circle at 8% 0%,rgba(106,169,255,.12),transparent 28%),radial-gradient(circle at 90% 100%,rgba(105,223,195,.09),transparent 30%),var(--bg)}}
    .login-layout{{min-height:100vh;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(420px,.75fr)}}
    .briefing{{position:relative;overflow:hidden;padding:54px clamp(36px,7vw,92px);display:flex;flex-direction:column;justify-content:space-between;border-right:1px solid rgba(255,255,255,.06)}}
    .briefing:before{{content:"";position:absolute;inset:0;background:linear-gradient(125deg,rgba(255,255,255,.018),transparent 45%);pointer-events:none}}
    .brandline{{position:relative;display:flex;align-items:center;gap:13px}}.brandmark{{width:44px;height:44px;border-radius:13px;display:grid;place-items:center;background:#f4f7fb;color:#06101b;font-weight:950;font-size:18px}}
    .brandline b{{display:block;font-size:16px;letter-spacing:-.02em}}.brandline span{{display:block;color:#66758a;font-size:10px;letter-spacing:.13em;text-transform:uppercase;margin-top:2px}}
    .brief-copy{{position:relative;max-width:730px;margin:80px 0}}.brief-copy small{{display:inline-flex;align-items:center;gap:8px;color:var(--mint);font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:10px}}
    .brief-copy small:before{{content:"";width:24px;height:1px;background:var(--mint)}}.brief-copy h1{{font-size:clamp(42px,6vw,76px);line-height:.98;letter-spacing:-.06em;margin:18px 0 22px;max-width:780px}}
    .brief-copy p{{max-width:650px;color:#94a2b3;font-size:16px;line-height:1.7;margin:0}}
    .capabilities{{position:relative;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}.cap{{padding:15px 16px;border:1px solid rgba(255,255,255,.065);border-radius:15px;background:rgba(12,18,27,.58)}}
    .cap span{{display:block;color:#627184;font-size:9px;letter-spacing:.1em;text-transform:uppercase}}.cap strong{{display:block;margin-top:5px;font-size:12px}}
    .access{{display:grid;place-items:center;padding:30px;background:rgba(7,11,17,.86)}}.access-card{{width:min(420px,100%);padding:34px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(180deg,#101823,#0b1119);box-shadow:0 32px 100px rgba(0,0,0,.42)}}
    .access-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:28px}}.access-head h2{{margin:0;font-size:24px;letter-spacing:-.035em}}.access-head p{{margin:7px 0 0;color:var(--muted);font-size:12px}}
    .secure-dot{{width:38px;height:38px;border:1px solid rgba(94,210,155,.2);border-radius:12px;display:grid;place-items:center;background:rgba(94,210,155,.08);color:var(--success);font-weight:900}}
    label{{display:block;margin:15px 0 7px;color:#a8b4c3;font-size:10px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}}input,button{{width:100%;height:48px;border-radius:12px;font:inherit}}
    input{{border:1px solid var(--line);background:#080d14;color:var(--ink);padding:0 14px;outline:none}}input:focus{{border-color:var(--blue);box-shadow:0 0 0 4px rgba(106,169,255,.09)}}
    button{{border:0;margin-top:21px;background:#f4f7fb;color:#07111b;font-weight:900;cursor:pointer;transition:.2s transform,.2s opacity}}button:hover{{transform:translateY(-1px);opacity:.94}}
    .login-error{{min-height:20px;margin-top:12px;color:var(--danger);font-size:11px}}.access-foot{{display:flex;justify-content:space-between;gap:12px;margin-top:18px;padding-top:16px;border-top:1px solid rgba(255,255,255,.055);color:#637185;font-size:9px}}
    @media(max-width:900px){{.login-layout{{grid-template-columns:1fr}}.briefing{{display:none}}.access{{min-height:100vh;padding:20px}}.access-card{{padding:28px 22px}}}}
    </style></head><body><main class='login-layout'>
      <section class='briefing'><div class='brandline'><div class='brandmark'>N</div><div><b>Nerlo Wallet</b><span>Treasury Control Center</span></div></div>
        <div class='brief-copy'><small>Kurumsal operasyon alanı</small><h1>Finans akışını net, kontrollü ve izlenebilir yönetin.</h1><p>Blockchain yatırımları, havuz hareketleri, çekim onayları, kullanıcı bakiyeleri ve denetim kayıtları için tek merkez.</p></div>
        <div class='capabilities'><div class='cap'><span>Finans</span><strong>PostgreSQL Ledger</strong></div><div class='cap'><span>Blockchain</span><strong>Indexer & Sweep</strong></div><div class='cap'><span>Güvenlik</span><strong>Rol Bazlı Yetki</strong></div></div>
      </section>
      <section class='access'><form class='access-card' method='post'><div class='access-head'><div><h2>Yetkili girişi</h2><p>Operasyon hesabınızla devam edin.</p></div><div class='secure-dot'>✓</div></div>
        <label>Kullanıcı adı</label><input name='username' autocomplete='username' required autofocus>
        <label>Şifre</label><input type='password' autocomplete='current-password' name='password' required>
        <button>Kontrol merkezine gir</button><div class='login-error'>{h(error)}</div>
        <div class='access-foot'><span>CSRF korumalı oturum</span><span>30 dk güvenli süre</span></div>
      </form></section>
    </main></body></html>"""
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
    "complete_manual_crypto": "Manuel kripto çekimini TXID ile tamamlama",
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


def sweep_status_label(value):
    return {
        "queued": "Sırada",
        "funding_signed": "Gas imzalandı",
        "funding_broadcast": "Gas gönderildi",
        "funded": "Gas hazır",
        "sweep_signed": "Sweep imzalandı",
        "broadcast": "Ağ onayı",
        "cleanup_pending": "TRX geri toplama",
        "cleanup_signed": "Geri toplama imzalandı",
        "cleanup_broadcast": "Geri toplama ağda",
        "confirmed": "Tamamlandı",
        "covered": "Toplu işlemle kapsandı",
        "below_minimum": "Minimum altında",
        "review": "İnceleme gerekli",
    }.get(str(value or ""), str(value or "-"))


def sweep_status_class(value):
    return {
        "confirmed": "done",
        "covered": "done",
        "review": "declined",
        "below_minimum": "neutral",
        "broadcast": "working",
        "cleanup_broadcast": "working",
        "funding_broadcast": "working",
    }.get(str(value or ""), "waiting")


def render_sweep_rows(snapshot):
    rows = []
    for item in (snapshot or {}).get("recent", []):
        txid = item.get("sweep_txid") or item.get("funding_txid") or item.get("cleanup_txid") or "-"
        error = str(item.get("last_error") or "")
        details = error if error else txid
        rows.append(
            "<tr>"
            f"<td><code>#{h(item.get('sweep_id'))}</code></td>"
            f"<td>{h(item.get('asset'))}</td>"
            f"<td><code>{h(item.get('user_id'))}</code></td>"
            f"<td><code class='address-cell'>{h(item.get('source_address'))}</code></td>"
            f"<td>{h(fmt(item.get('amount'), item.get('asset')))}</td>"
            f"<td><span class='status {sweep_status_class(item.get('status'))}'><i></i>{h(sweep_status_label(item.get('status')))}</span></td>"
            f"<td title='{h(details)}'><code class='tx-cell'>{h(details)}</code></td>"
            f"<td>{h(item.get('updated_at'))}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='8' class='muted-cell'>Henüz sweep kaydı bulunmuyor.</td></tr>"


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
    txid = str(r.get("broadcast_txid") or r.get("txid") or "")

    destination_label = ""
    destination_value = ""
    if request_type == "deposit":
        primary_label = "Net yatırım"
        primary_value = fmt(r.get("net_amount"), asset)
        gross_label = "Brüt yatırım"
        gross_value = fmt(r.get("amount"), asset)
        fee_value = fmt(r.get("fee"), asset)
        details = [("Ağ", network), ("Kullanıcı", f"@{username}"), ("Kullanıcı ID", uid)]
        if asset == "TL":
            details.insert(0, ("Gönderen", r.get("sender_name") or "-"))
            destination_label = "Ödeme referansı"
            destination_value = str(r.get("tx_note") or "-")
        elif txid:
            destination_label = "Blockchain TXID"
            destination_value = txid
    elif request_type == "withdraw":
        primary_label = "Net gönderim"
        primary_value = fmt(r.get("net_amount"), asset)
        gross_label = "Talep tutarı"
        gross_value = fmt(r.get("amount"), asset)
        fee_value = fmt(r.get("fee"), asset)
        details = [("Ağ", network), ("Kullanıcı", f"@{username}"), ("Kullanıcı ID", uid)]
        destination_label = "Hedef IBAN" if asset == "TL" else "Hedef cüzdan"
        destination_value = str(r.get("iban") or r.get("address") or "-")
        if asset == "TL":
            details.insert(1, ("Alıcı", r.get("name") or "-"))
        if txid:
            details.append(("TXID", txid))
    else:
        target_asset = str(r.get("to_asset") or "")
        primary_label = "Dönüşüm sonucu"
        primary_value = fmt(r.get("net_to_amount"), target_asset)
        gross_label = "Kaynak tutar"
        gross_value = fmt(r.get("from_amount"), r.get("from_asset"))
        fee_value = fmt(r.get("fee"), target_asset)
        details = [("Parite", f"{r.get('from_asset', '-')} → {target_asset or '-'}"), ("Kullanıcı", f"@{username}"), ("Kullanıcı ID", uid)]

    detail_html = "".join(
        f"<div class='case-detail'><span>{h(label)}</span><strong title='{h(value)}'>{h(value)}</strong></div>"
        for label, value in details
    )
    destination_html = ""
    if destination_value:
        destination_html = (
            "<div class='case-destination'><div><span>" + h(destination_label) + "</span>"
            f"<code>{h(destination_value)}</code></div>"
            + (f"<button type='button' class='copy-control' data-copy='{h(destination_value)}'>Kopyala</button>" if destination_value != "-" else "")
            + "</div>"
        )

    actions = ""
    if status in ("pending", "processing") and not r.get("automatic"):
        is_crypto_withdraw = _is_crypto_withdraw_record(r)
        asset_upper = str(r.get("asset") or "").upper()
        if is_crypto_withdraw and r.get("broadcast_locked"):
            actions = (
                "<div class='case-alert info'><b>Blockchain gönderimi devam ediyor</b>"
                f"<span>Signer durumu: {h(r.get('signer_status') or 'işleniyor')}. Ağ sonucu bekleniyor.</span></div>"
            )
        elif is_crypto_withdraw and asset_upper in MANUAL_WITHDRAW_ASSETS:
            process_button = "" if status == "processing" else "<button class='btn subtle' name='action' value='process_request'>İşleme al</button>"
            actions = (
                "<div class='case-alert warning'><b>Manuel blockchain transferi</b><span>Harici cüzdandan gönderim yapın; gerçek TXID oluşunca kaydedin.</span></div>"
                f"<form method='post' class='txid-command'><input type='hidden' name='rid' value='{h(rid)}'><input type='hidden' name='return_to' value='/admin?view=requests'>"
                f"<input name='manual_txid' autocomplete='off' placeholder='{h(asset_upper)} TXID' required><button class='btn positive' name='action' value='complete_manual_crypto'>TXID ile tamamla</button></form>"
                f"<form method='post' class='case-actions'><input type='hidden' name='rid' value='{h(rid)}'><input type='hidden' name='return_to' value='/admin?view=requests'>{process_button}<button class='btn negative' name='action' value='reject_request'>Reddet</button></form>"
            )
        else:
            process_button = "" if status == "processing" else "<button class='btn subtle' name='action' value='process_request'>İşleme al</button>"
            approve_button = "" if is_crypto_withdraw else "<button class='btn positive' name='action' value='approve_request'>Tamamla</button>"
            if is_crypto_withdraw and asset_upper in AUTO_WITHDRAW_ASSETS and r.get("signer_enabled"):
                note = "<div class='case-alert safe'><b>Otomatik gönderime hazır</b><span>İşleme al komutu transferi TRON ağına iletir.</span></div>"
            elif is_crypto_withdraw:
                note = "<div class='case-alert warning'><b>Blockchain akışı hazır değil</b><span>Talep gerçek TXID olmadan tamamlanamaz.</span></div>"
            else:
                note = ""
            actions = (
                note + f"<form method='post' class='case-actions'><input type='hidden' name='rid' value='{h(rid)}'><input type='hidden' name='return_to' value='/admin?view=requests'>"
                f"{process_button}{approve_button}<button class='btn negative' name='action' value='reject_request'>Reddet</button></form>"
            )

    return f"""
    <article class='case-card case-{h(request_type)}'>
      <div class='case-topline'>
        <div class='case-identity'><span class='case-type'>{h(request_type_label(request_type))}</span><strong>#{h(rid)}</strong><small>{h(created)}</small></div>
        <span class='status {request_status_class(status)}'><i></i>{h(status_label(status))}</span>
      </div>
      <div class='case-body'>
        <section class='case-amount'><span>{h(primary_label)}</span><strong>{h(primary_value)}</strong><div class='case-breakdown'><span>{h(gross_label)} <b>{h(gross_value)}</b></span><span>Hizmet ücreti <b>{h(fee_value)}</b></span></div></section>
        <section class='case-information'>{detail_html}</section>
      </div>
      {destination_html}
      <footer class='case-footer'>{actions or "<span class='case-complete-note'>Bu işlem için açık yönetici aksiyonu bulunmuyor.</span>"}</footer>
    </article>
    """
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

    r10 = u.get("r10_verification", {}) or {}
    r10_status = r10.get("status") or "yok"
    r10_action = ""
    if r10.get("status") == "pending":
        r10_action = (
            f"<form method='post'><input type='hidden' name='user_id' value='{h(uid)}'>"
            f"<input type='hidden' name='return_to' value='{return_to}'>"
            f"<button class='btn primary' name='action' value='approve_r10_tl'>TL işlemlerini aç</button></form>"
        )
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
      <div class='section-head'><div><span class='eyebrow'>R10</span><h3>TL Doğrulama</h3></div><p>Durum: {h(r10_status)}</p></div>
      <div class='table-wrap'><table><tbody>
        <tr><th>Profil</th><td>{('<a href="' + h(r10.get('profile_url')) + '" target="_blank">' + h(r10.get('profile_url')) + '</a>') if r10.get('profile_url') else '-'}</td></tr>
        <tr><th>Gizli ad</th><td>{h(r10.get('masked_name') or '-')}</td></tr>
        <tr><th>Key</th><td><b>{h(r10.get('key') or '-')}</b></td></tr>
        <tr><th>Onay</th><td>{h(r10.get('approved_at') or '-')}</td></tr>
      </tbody></table></div>
      <div class='security-actions'>{r10_action}</div>
    </section>
    <section class='panel-card compact-card'>
      <div class='section-head'><div><span class='eyebrow'>GÜVENLİK</span><h3>Hesap Kontrolleri</h3></div><p>Çekim: {'Kilitli' if u.get('withdraw_locked') else 'Açık'} · PIN: {'Aktif' if u.get('pin_hash') else 'Ayarlanmamış'}</p></div>
      <div class='security-actions'>{''.join(security_forms)}</div>
    </section>
    <div class='user-workspace history-grid'>
      <section class='panel-card compact-card'><div class='section-head'><div><span class='eyebrow'>SON KAYITLAR</span><h3>İşlem Talepleri</h3></div></div><div class='table-wrap'><table><thead><tr><th>No</th><th>Tür</th><th>Durum</th><th>Tarih</th></tr></thead><tbody>{request_rows}</tbody></table></div></section>
      <section class='panel-card compact-card'><div class='section-head'><div><span class='eyebrow'>HAREKETLER</span><h3>Bakiye Defteri</h3></div></div><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Tutar</th><th>Son bakiye</th><th>Not</th></tr></thead><tbody>{transaction_rows}</tbody></table></div></section>
    </div>
    """


def render_r10_approval_menu():
    rows = []
    for uid, u in sorted(users.items(), key=lambda item: (item[1].get("r10_verification", {}) or {}).get("created_at", ""), reverse=True):
        info = (u or {}).get("r10_verification", {}) or {}
        if not info:
            continue
        owner_input = ""
        if info.get("account_type") == "corporate" and info.get("status") == "pending":
            owner_input = f"<input name='iban_owner' placeholder='Kurumsal IBAN ad soyad' required>"
        actions = []
        if info.get("status") == "pending":
            actions.append(f"<form method='post' class='inline-form'><input type='hidden' name='action' value='approve_r10_tl'><input type='hidden' name='user_id' value='{h(uid)}'><input type='hidden' name='return_to' value='/admin?view=approvals'>{owner_input}<button class='btn primary'>Onayla</button></form>")
        if info.get("status") == "approved":
            actions.append(f"<form method='post' class='inline-form'><input type='hidden' name='action' value='revoke_r10_tl'><input type='hidden' name='user_id' value='{h(uid)}'><input type='hidden' name='return_to' value='/admin?view=approvals'><button class='btn danger'>Onayı kaldır</button></form>")
            actions.append(f"<form method='post' class='inline-form'><input type='hidden' name='action' value='request_r10_reverify'><input type='hidden' name='user_id' value='{h(uid)}'><input type='hidden' name='return_to' value='/admin?view=approvals'><button class='btn ghost'>Yeniden doğrulama iste</button></form>")
        rows.append(f"<tr><td>{h(uid)}<br><small>@{h(username_label(u.get('username')))}</small></td><td>{h(info.get('status') or '-')}</td><td>{h(info.get('account_type') or '-')}</td><td><a href='{h(info.get('profile_url') or '#')}' target='_blank'>{h(info.get('profile_slug') or info.get('profile_url') or '-')}</a></td><td>{h(info.get('masked_name') or '-')}<br><small>{h(str(info.get('r10_months', 0)))} ay · {h(str(info.get('r10_trades', 0)))} trade</small></td><td><b>{h(info.get('key') or '-')}</b><br><small>{h(info.get('iban_owner') or '-')}</small></td><td>{h(info.get('approved_by') or '-')}<br><small>{h(info.get('approved_at') or '-')}</small></td><td><div class='security-actions'>{''.join(actions)}</div></td></tr>")
    body = ''.join(rows) or "<tr><td colspan='8' class='muted-cell'>R10 doğrulama kaydı yok.</td></tr>"
    return f"<section class='surface'><div class='surface-head'><div><span>R10</span><h3>Kullanıcı onay menüsü</h3></div><small>PM key kontrolünden sonra manuel onay verin.</small></div><div class='table-wrap'><table><thead><tr><th>Kullanıcı</th><th>Durum</th><th>Tip</th><th>Profil</th><th>Bilgi</th><th>Key / IBAN adı</th><th>Onay</th><th>İşlem</th></tr></thead><tbody>{body}</tbody></table></div></section>"


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
        elif action == "complete_manual_crypto":
            rid = str(request.form.get("rid", "")).strip()
            txid = str(request.form.get("manual_txid", "")).strip()
            try:
                updated = exchange_complete_manual_withdrawal(rid, txid, current_panel_username())
                uid = str(updated.get("user_id") or "")
                if uid:
                    send(uid, receipt_text(rid, uid), reply_keyboard(uid))
                add_admin_log("complete_manual_crypto", f"#{rid} · {updated.get('asset')} · {updated.get('broadcast_txid')}", uid)
                set_admin_notice(f"#{rid} TXID kaydedilerek tamamlandı.")
            except ValueError as exc:
                set_admin_notice(str(exc), "error")
            except Exception as exc:
                print("MANUAL CRYPTO COMPLETE ERROR:", exc)
                set_admin_notice("Manuel çekim güvenli biçimde tamamlanamadı.", "error")
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
        elif action == "approve_r10_tl":
            uid = request.form.get("user_id", "")
            if uid in users and users[uid].get("r10_verification", {}).get("status") == "pending":
                info = users[uid]["r10_verification"]
                if r10_profile_used_by(info.get("profile_slug"), uid):
                    set_admin_notice("Bu r10 profili başka hesaba bağlı.", "error")
                else:
                    iban_owner = normalize_person_name(request.form.get("iban_owner", "")) if info.get("account_type") == "corporate" else ""
                    if info.get("account_type") == "corporate" and len(_name_parts(iban_owner)) < 2:
                        set_admin_notice("Kurumsal hesap için IBAN ad soyad zorunlu.", "error")
                    elif iban_owner and r10_iban_owner_used_by(iban_owner, uid):
                        set_admin_notice("Bu IBAN ad soyad başka hesapta kayıtlı.", "error")
                    else:
                        info["status"] = "approved"
                        info["approved_at"] = now()
                        info["approved_by"] = current_panel_username()
                        if iban_owner:
                            info["iban_owner"] = iban_owner
                        save_user_profile(uid)
                        add_admin_log("approve_r10_tl", f"R10 TL doğrulaması onaylandı · {info.get('profile_slug','')}", uid)
                        set_admin_notice("TL işlemleri açıldı.")
            else:
                set_admin_notice("Onay bekleyen R10 doğrulaması yok.", "error")
        elif action == "revoke_r10_tl":
            uid = request.form.get("user_id", "")
            if uid in users and users[uid].get("r10_verification"):
                users[uid]["r10_verification"]["status"] = "revoked"
                users[uid]["r10_verification"]["revoked_at"] = now()
                users[uid]["r10_verification"]["revoked_by"] = current_panel_username()
                save_user_profile(uid)
                add_admin_log("revoke_r10_tl", "R10 TL onayı kaldırıldı", uid)
                set_admin_notice("TL onayı kaldırıldı.")
            else:
                set_admin_notice("R10 doğrulama kaydı yok.", "error")
        elif action == "request_r10_reverify":
            uid = request.form.get("user_id", "")
            if uid in users:
                users[uid]["r10_verification"] = {"status": "reverify_required", "requested_at": now(), "requested_by": current_panel_username()}
                save_user_profile(uid)
                add_admin_log("request_r10_reverify", "Yeniden R10 doğrulama istendi", uid)
                try:
                    send(uid, "TL işlemleri için yeniden R10 doğrulaması yapmanız gerekiyor.", reply_keyboard(uid))
                except Exception:
                    pass
                set_admin_notice("Yeniden doğrulama istendi.")
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
    pool_snapshot = tron_pool_snapshot()
    pool_counts = pool_snapshot.get("sweeps") or {}
    pool_queue_count = sum(int(pool_counts.get(key, 0) or 0) for key in (
        "queued", "funding_signed", "funding_broadcast", "funded", "sweep_signed",
        "broadcast", "cleanup_pending", "cleanup_signed", "cleanup_broadcast",
    ))
    pool_review_count = int(pool_counts.get("review", 0) or 0)
    pool_address = str(pool_snapshot.get("address") or TRON_POOL_ADDRESS or "")
    pool_error = str(pool_snapshot.get("error") or "")

    status_counts = {key: 0 for key in ("pending", "processing", "completed", "rejected")}
    type_counts = {key: 0 for key in ("deposit", "withdraw", "convert")}
    for item in requests_db.values():
        status_counts[str(item.get("status") or "pending")] = status_counts.get(str(item.get("status") or "pending"), 0) + 1
        type_counts[str(item.get("type") or "other")] = type_counts.get(str(item.get("type") or "other"), 0) + 1

    pending_count = status_counts.get("pending", 0) + status_counts.get("processing", 0)
    completed_today = sum(
        1 for r in requests_db.values()
        if r.get("status") == "completed" and str(r.get("updated_at", r.get("created_at", ""))).startswith(today())
    )
    frozen_users = sum(1 for u in users.values() if u.get("status") == "frozen")
    locked_users = sum(1 for u in users.values() if u.get("withdraw_locked"))

    recent_requests = sorted(requests_db.items(), key=lambda item: item[1].get("created_at", ""), reverse=True)[:8]
    recent_activity = "".join(
        f"<div class='activity-row'><span class='activity-mark {request_status_class(r.get('status'))}'></span>"
        f"<div><strong>{h(request_type_label(r.get('type')))} · #{h(rid)}</strong><small>@{h(username_label(users.get(str(r.get('user_id')), {}).get('username')))} · {h(r.get('created_at') or '-')}</small></div>"
        f"<b>{h(fmt(r.get('net_amount') or r.get('net_to_amount') or r.get('amount') or r.get('from_amount') or '0', r.get('asset') or r.get('to_asset') or r.get('from_asset') or ''))}</b></div>"
        for rid, r in recent_requests
    ) or "<div class='empty-state compact-empty'>Henüz işlem hareketi bulunmuyor.</div>"

    asset_cards = "".join(
        f"<div class='asset-card'><div class='asset-head'><span class='asset-symbol'>{h(asset)}</span><small>Kullanıcı yükümlülüğü</small></div>"
        f"<strong>{h(fmt(totals[asset], asset))}</strong><div class='asset-meta'><span>Bekleyen çekim</span><b>{h(fmt(pending[asset], asset))}</b></div></div>"
        for asset in ASSETS
    )

    recent_users = sorted(users.items(), key=lambda item: item[1].get("last_seen", ""), reverse=True)[:25]
    user_rows = "".join(
        f"<tr><td><code>{h(uid)}</code></td><td>@{h(username_label(u.get('username')))}</td><td>{h(fmt(u.get('balances', {}).get('TL', '0'), 'TL'))}</td>"
        f"<td>{h(fmt(u.get('balances', {}).get('USDT', '0'), 'USDT'))}</td><td>{h(fmt(u.get('balances', {}).get('TRX', '0'), 'TRX'))}</td>"
        f"<td><span class='status {'declined' if u.get('status') == 'frozen' else 'done'}'><i></i>{h(account_status_label(u.get('status')))}</span></td><td>{h(u.get('last_seen') or '-')}</td></tr>"
        for uid, u in recent_users
    ) or "<tr><td colspan='7' class='muted-cell'>Henüz kullanıcı yok.</td></tr>"

    settings_groups = [
        ("rates", "Kur Yönetimi", [k for k in EDITABLE_SETTING_KEYS if k.startswith("rate_")]),
        ("fees", "Komisyonlar", [k for k in EDITABLE_SETTING_KEYS if k.startswith("fee_") and k not in ("fee_convert_tl_percent", "fee_convert_crypto_percent")]),
        ("limits", "Limitler", [k for k in EDITABLE_SETTING_KEYS if k.startswith("min_") or k.startswith("daily_")]),
        ("wallets", "Cüzdan ve Ağ", [k for k in EDITABLE_SETTING_KEYS if k.startswith("wallet_") or k.startswith("network_") or k in ("bank_name", "iban", "iban_owner")]),
        ("system", "Sistem ve Duyuru", [k for k in EDITABLE_SETTING_KEYS if k.startswith("maintenance_") or k.startswith("announcement_")]),
        ("messages", "Bot Mesajları", list(DEFAULT_MESSAGES.keys())),
    ]
    settings_tabs = "".join(
        f"<button type='button' class='setting-tab {'active' if i == 0 else ''}' data-setting-target='{h(slug)}'>{h(title)}</button>"
        for i, (slug, title, keys) in enumerate(settings_groups)
    )
    settings_panes = []
    for index, (slug, title, keys) in enumerate(settings_groups):
        fields = "".join(message_field(key) for key in keys) if slug == "messages" else "".join(setting_field(key) for key in keys)
        settings_panes.append(
            f"<div class='setting-pane {'active' if index == 0 else ''}' data-setting-pane='{h(slug)}'><div class='section-title compact'><div><span>AYAR GRUBU</span><h3>{h(title)}</h3></div></div><div class='settings-grid'>{fields}</div></div>"
        )

    logs = "".join(
        f"<tr><td>{h(item.get('created_at'))}</td><td>{h(admin_action_label(item.get('action')))}</td><td>{h(item.get('user_id') or '-')}</td><td>{h(localized_admin_detail(item.get('details')))}</td></tr>"
        for item in reversed(admin_logs[-120:])
    ) or "<tr><td colspan='4' class='muted-cell'>Yönetim kaydı yok.</td></tr>"

    notice = session.pop("admin_notice", None)
    notice_html = ""
    if notice:
        notice_html = f"<div class='toast {'toast-error' if notice.get('kind') == 'error' else ''}' id='admin-toast'>{h(notice.get('message'))}</div>"

    nav_items = [
        ("dashboard", "Kontrol Merkezi", "01", "Operasyon"),
        ("pool", "Havuz & Sweep", "02", "Operasyon"),
        ("requests", "İşlem Talepleri", "03", "Operasyon"),
        ("users", "Kullanıcılar", "04", "Yönetim"),
        ("approvals", "Kullanıcı Onay", "05", "Yönetim"),
        ("broadcast", "Duyurular", "06", "Yönetim"),
        ("settings", "Sistem Ayarları", "07", "Sistem"),
        ("logs", "Denetim Kayıtları", "08", "Sistem"),
        ("admins", "Yetkililer", "09", "Sistem"),
    ]
    nav_parts = []
    last_group = None
    for slug, label, number, group in nav_items:
        if slug not in allowed_views:
            continue
        if group != last_group:
            nav_parts.append(f"<div class='nav-group-label'>{h(group)}</div>")
            last_group = group
        badge = ""
        if slug == "requests" and pending_count:
            badge = f"<em>{pending_count}</em>"
        elif slug == "pool" and pool_review_count:
            badge = f"<em class='alert-badge'>{pool_review_count}</em>"
        nav_parts.append(
            f"<button class='nav-entry {'active' if slug == active_view else ''}' data-view-target='{slug}' data-view-title='{h(label)}'><span>{number}</span><b>{h(label)}</b>{badge}</button>"
        )
    nav_html = "".join(nav_parts)

    dashboard_section = f"""<section class='page-view {'active' if active_view == 'dashboard' else ''}' data-view='dashboard'>
      <div class='command-hero'>
        <div class='hero-copy'><span>NERLO TREASURY CONTROL</span><h2>Finans operasyonlarının canlı görünümü</h2><p>Yükümlülükler, işlem kuyrukları, blockchain havuzu ve kullanıcı risklerini tek ekrandan izleyin.</p></div>
        <div class='hero-date'><small>Rapor tarihi</small><strong>{h(today())}</strong><span>Türkiye saati</span></div>
      </div>
      <div class='kpi-grid'>
        <div class='kpi'><span>Toplam kullanıcı</span><strong>{len(users)}</strong><small>{frozen_users} dondurulmuş hesap</small></div>
        <div class='kpi attention'><span>Aksiyon bekleyen</span><strong>{pending_count}</strong><small>{status_counts.get('pending',0)} bekliyor · {status_counts.get('processing',0)} işleniyor</small></div>
        <div class='kpi'><span>Bugün tamamlanan</span><strong>{completed_today}</strong><small>Başarılı işlem kaydı</small></div>
        <div class='kpi {'attention' if locked_users else ''}'><span>Çekimi kilitli</span><strong>{locked_users}</strong><small>Kullanıcı güvenlik kontrolü</small></div>
      </div>
      <div class='section-title'><div><span>FİNANSAL YÜKÜMLÜLÜKLER</span><h3>Varlık görünümü</h3></div><p>Kullanıcı kullanılabilir bakiyeleri ve açık çekim yükümlülükleri</p></div>
      <div class='asset-grid'>{asset_cards}</div>
      <div class='dashboard-split'>
        <section class='surface'><div class='surface-head'><div><span>İŞLEM DAĞILIMI</span><h3>Operasyon kuyruğu</h3></div><small>Canlı kayıtlar</small></div>
          <div class='pipeline'><div><span>Yatırım</span><strong>{type_counts.get('deposit',0)}</strong></div><div><span>Çekim</span><strong>{type_counts.get('withdraw',0)}</strong></div><div><span>Dönüşüm</span><strong>{type_counts.get('convert',0)}</strong></div><div><span>İnceleme</span><strong>{pool_review_count}</strong></div></div>
          <div class='status-line'><span><i class='waiting'></i>Bekleyen {status_counts.get('pending',0)}</span><span><i class='working'></i>İşlenen {status_counts.get('processing',0)}</span><span><i class='done'></i>Tamamlanan {status_counts.get('completed',0)}</span><span><i class='declined'></i>Reddedilen {status_counts.get('rejected',0)}</span></div>
        </section>
        <section class='surface'><div class='surface-head'><div><span>SON AKIŞ</span><h3>Güncel hareketler</h3></div><button type='button' class='text-action' data-jump-view='requests'>Tüm talepler</button></div><div class='activity-list'>{recent_activity}</div></section>
      </div>
    </section>""" if "dashboard" in allowed_views else ""

    pool_section = f"""<section class='page-view {'active' if active_view == 'pool' else ''}' data-view='pool'>
      <div class='page-heading'><div><span>TRON HAZİNE OPERASYONLARI</span><h2>Havuz ve otomatik sweep</h2><p>Kullanıcı yatırma adreslerinden ana havuza taşınan TRX ve TRC20 USDT hareketleri.</p></div><span class='health-badge {'danger' if pool_review_count else ''}'>{'İnceleme gereken ' + str(pool_review_count) if pool_review_count else 'Akış normal'}</span></div>
      <div class='treasury-layout'>
        <div class='treasury-balance'><span>ANA HAVUZ</span><div class='treasury-values'><div><small>TRX</small><strong>{h(fmt(pool_snapshot.get('trx','0'),'TRX'))}</strong></div><div><small>USDT</small><strong>{h(fmt(pool_snapshot.get('usdt','0'),'USDT'))}</strong></div></div><p>{h(pool_snapshot.get('checked_at') or 'Henüz ölçülmedi')}</p></div>
        <div class='treasury-state'><div><span>Aktif sweep</span><strong>{pool_queue_count}</strong></div><div><span>İnceleme</span><strong>{pool_review_count}</strong></div><div><span>Tamamlanan</span><strong>{int(pool_counts.get('confirmed',0) or 0)+int(pool_counts.get('covered',0) or 0)}</strong></div></div>
      </div>
      <div class='pool-address'><div><span>ANA TRON HAVUZ ADRESİ</span><code>{h(pool_address or 'TRON_POOL_ADDRESS tanımlı değil')}</code><small>{h(pool_error or 'Bakiye ve sweep bilgileri signer servisi tarafından güncellenir.')}</small></div>{f"<button type='button' class='copy-control' data-copy='{h(pool_address)}'>Adresi kopyala</button>" if pool_address else ''}</div>
      <div class='flow-steps'><div><b>01</b><span>Yatırım algılanır</span></div><i></i><div><b>02</b><span>Ağ onayı tamamlanır</span></div><i></i><div><b>03</b><span>Sweep imzalanır</span></div><i></i><div><b>04</b><span>Ana havuza taşınır</span></div></div>
      <section class='surface'><div class='surface-head'><div><span>SWEEP GÜNLÜĞÜ</span><h3>Son blockchain hareketleri</h3></div><small>Gas, token taşıma ve geri toplama adımları</small></div><div class='table-wrap'><table><thead><tr><th>Sweep</th><th>Varlık</th><th>Kullanıcı</th><th>Kaynak</th><th>Tutar</th><th>Durum</th><th>TXID / Hata</th><th>Güncelleme</th></tr></thead><tbody>{render_sweep_rows(pool_snapshot)}</tbody></table></div></section>
    </section>""" if "pool" in allowed_views else ""

    requests_section = f"""<section class='page-view {'active' if active_view == 'requests' else ''}' data-view='requests'>
      <div class='page-heading'><div><span>ONAY VE İŞLEM AKIŞI</span><h2>İşlem talepleri</h2><p>Her talebi açık tutar, hedef, ağ ve durum bilgileriyle yönetin.</p></div><div class='heading-metrics'><span>Bekleyen <b>{status_counts.get('pending',0)}</b></span><span>İşlenen <b>{status_counts.get('processing',0)}</b></span></div></div>
      <form id='request-filter' class='command-filter'><div class='search-field'><span>⌕</span><input name='rq' value='{h(request_query)}' placeholder='İşlem no, kullanıcı ID veya kullanıcı adı'></div><select name='status'><option value='all'>Tüm durumlar</option>{''.join(f"<option value='{s}' {'selected' if status_filter == s else ''}>{status_label(s)}</option>" for s in ['pending','processing','completed','rejected'])}</select><select name='type'><option value='all'>Tüm işlem türleri</option>{''.join(f"<option value='{t}' {'selected' if type_filter == t else ''}>{request_type_label(t)}</option>" for t in ['deposit','withdraw','convert'])}</select><button class='btn primary'>Filtrele</button></form>
      <div id='request-list' class='case-list'>{render_request_list(request_query, status_filter, type_filter)}</div>
    </section>""" if "requests" in allowed_views else ""

    users_section = f"""<section class='page-view {'active' if active_view == 'users' else ''}' data-view='users'>
      <div class='page-heading'><div><span>KULLANICI OPERASYONLARI</span><h2>Kullanıcı kontrol masası</h2><p>Profil, bakiye, güvenlik ve işlem geçmişini tek görünümde yönetin.</p></div></div>
      <form id='user-lookup' class='user-command'><div><label>Telegram kullanıcı ID</label><input id='manage-user-id' name='uid' value='{h(manage_user_id)}' inputmode='numeric' placeholder='Örn. 123456789'></div><button class='btn primary'>Kullanıcıyı aç</button></form>
      <div id='user-management-result' class='user-result'>{render_user_management(manage_user_id)}</div>
      <section class='surface'><div class='surface-head'><div><span>SON AKTİVİTE</span><h3>Yakın zamanda görülen kullanıcılar</h3></div><small>Son 25 kayıt</small></div><div class='table-wrap'><table><thead><tr><th>Kullanıcı ID</th><th>Kullanıcı adı</th><th>TL</th><th>USDT</th><th>TRX</th><th>Hesap</th><th>Son görülme</th></tr></thead><tbody>{user_rows}</tbody></table></div></section>
    </section>""" if "users" in allowed_views else ""

    approvals_section = f"""<section class='page-view {'active' if active_view == 'approvals' else ''}' data-view='approvals'>
      <div class='page-heading'><div><span>R10 VE TL ERİŞİMİ</span><h2>Kullanıcı onay menüsü</h2><p>R10 profili, key ve IBAN adı kontrolünü buradan yönetin.</p></div></div>
      {render_r10_approval_menu()}
    </section>""" if "approvals" in allowed_views else ""

    broadcast_section = f"""<section class='page-view {'active' if active_view == 'broadcast' else ''}' data-view='broadcast'>
      <div class='page-heading'><div><span>KULLANICI İLETİŞİMİ</span><h2>Duyuru merkezi</h2><p>Bildirim izni açık kullanıcılara kontrollü toplu mesaj gönderin.</p></div></div>
      <div class='broadcast-layout'><form method='post' class='surface compose-card'><input type='hidden' name='action' value='broadcast'><input type='hidden' name='return_to' value='/admin?view=broadcast'><label>Duyuru metni</label><textarea name='announcement_text' placeholder='Kullanıcılara iletilecek mesajı yazın' required></textarea><div class='compose-foot'><small>Mesaj gönderim sonucu denetim kayıtlarına eklenir.</small><button class='btn primary'>Duyuruyu gönder</button></div></form><aside class='delivery-card'><span>GÖNDERİM KAPSAMI</span><strong>{len(users)}</strong><p>Kayıtlı kullanıcı</p><div><i></i>Yalnızca duyuru bildirimleri açık hesaplara gönderilir.</div></aside></div>
    </section>""" if "broadcast" in allowed_views else ""

    settings_section = f"""<section class='page-view {'active' if active_view == 'settings' else ''}' data-view='settings'>
      <div class='page-heading'><div><span>SİSTEM YAPILANDIRMASI</span><h2>Ayar yönetimi</h2><p>Kur, komisyon, limit, cüzdan ve bot mesajlarını kontrollü biçimde yönetin.</p></div></div>
      <form method='post' class='surface settings-surface'><input type='hidden' name='action' value='settings'><input type='hidden' name='return_to' value='/admin?view=settings'><div class='settings-nav'>{settings_tabs}</div>{''.join(settings_panes)}<div class='save-bar'><button class='btn primary'>Tüm ayarları kaydet</button></div></form>
    </section>""" if "settings" in allowed_views else ""

    logs_section = f"""<section class='page-view {'active' if active_view == 'logs' else ''}' data-view='logs'>
      <div class='page-heading'><div><span>DENETİM VE İZLENEBİLİRLİK</span><h2>Yönetim kayıtları</h2><p>Son yönetici işlemlerini tarih, kullanıcı ve ayrıntı bazında inceleyin.</p></div></div>
      <section class='surface'><div class='surface-head'><div><span>SON KAYITLAR</span><h3>Yönetici hareketleri</h3></div><small>Son 120 işlem</small></div><div class='table-wrap'><table><thead><tr><th>Tarih</th><th>İşlem</th><th>Kullanıcı</th><th>Detay</th></tr></thead><tbody>{logs}</tbody></table></div></section>
    </section>""" if "logs" in allowed_views else ""

    admins_section = f"""<section class='page-view {'active' if active_view == 'admins' else ''}' data-view='admins'>
      <div class='page-heading'><div><span>ERİŞİM YÖNETİMİ</span><h2>Panel yetkilileri</h2><p>Rol, erişim ve hesap durumlarını bölüm bazında yönetin.</p></div></div>{render_panel_user_management()}
    </section>""" if "admins" in allowed_views else ""

    panel_css = r"""
    :root{--bg:#070a0f;--sidebar:#0a0e14;--panel:#0e151f;--panel2:#111b27;--panel3:#091019;--line:#1e2a39;--line2:#172230;
      --text:#f2f5f9;--muted:#8b98a9;--subtle:#5f6d7e;--blue:#6aa9ff;--mint:#68dfc1;--amber:#efc36b;--red:#ff7890;--green:#5ed29b;--radius:18px}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(180deg,#070a0f,#05080c);color:var(--text);font:13px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    button,input,select,textarea{font:inherit}button{cursor:pointer}code{font-family:"SFMono-Regular",Consolas,monospace}
    .control-shell{min-height:100vh;display:grid;grid-template-columns:286px minmax(0,1fr)}
    .sidebar{position:sticky;top:0;height:100vh;padding:24px 17px 18px;background:linear-gradient(180deg,#0a0f16,#080c12);border-right:1px solid rgba(255,255,255,.055);display:flex;flex-direction:column;z-index:20}
    .brand{display:flex;align-items:center;gap:12px;padding:0 8px 24px}.brand-mark{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:#f3f6fa;color:#07111a;font-weight:950;font-size:17px}.brand strong{display:block;font-size:15px}.brand small{display:block;margin-top:2px;color:#5c697a;font-size:9px;letter-spacing:.13em;text-transform:uppercase}
    .nav-scroll{overflow:auto;padding-right:2px}.nav-group-label{padding:18px 10px 7px;color:#4d5a6a;font-size:8px;font-weight:900;letter-spacing:.16em;text-transform:uppercase}.nav-group-label:first-child{padding-top:0}
    .nav-entry{width:100%;min-height:44px;margin:3px 0;display:grid;grid-template-columns:31px 1fr auto;align-items:center;gap:10px;padding:6px 9px;border:1px solid transparent;border-radius:12px;background:transparent;color:#8f9cac;text-align:left}
    .nav-entry>span{width:29px;height:29px;border-radius:9px;display:grid;place-items:center;background:#0e1620;color:#657487;font-size:9px;font-weight:900}.nav-entry b{font-size:11px}.nav-entry em{min-width:22px;height:20px;padding:0 6px;border-radius:999px;display:grid;place-items:center;background:rgba(239,195,107,.11);color:var(--amber);font-size:9px;font-style:normal}.nav-entry .alert-badge{background:rgba(255,120,144,.12);color:var(--red)}
    .nav-entry:hover{background:#0e151e;color:#fff}.nav-entry.active{background:linear-gradient(90deg,rgba(106,169,255,.13),rgba(106,169,255,.035));border-color:rgba(106,169,255,.17);color:#fff}.nav-entry.active>span{background:rgba(106,169,255,.14);color:var(--blue)}
    .sidebar-footer{margin-top:auto;padding:15px 8px 0;border-top:1px solid rgba(255,255,255,.055)}.operator{display:flex;align-items:center;gap:10px}.operator-avatar{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:#111b27;color:var(--mint);font-weight:900}.operator strong{display:block;font-size:10px}.operator small{display:block;color:#5f6c7d;font-size:8px}.signout{display:inline-flex;margin-top:12px;color:#788697;text-decoration:none;font-size:9px}
    .workspace{min-width:0}.topbar{height:76px;position:sticky;top:0;z-index:15;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:0 clamp(20px,3vw,42px);background:rgba(7,10,15,.86);border-bottom:1px solid rgba(255,255,255,.055);backdrop-filter:blur(18px)}
    .topbar-left{display:flex;align-items:center;gap:13px}.menu-toggle{display:none;width:36px;height:36px;border:1px solid var(--line);border-radius:10px;background:#0d141d;color:#fff}.breadcrumbs span{display:block;color:#5d6b7c;font-size:8px;letter-spacing:.15em;text-transform:uppercase}.breadcrumbs h1{margin:2px 0 0;font-size:20px;letter-spacing:-.035em}.system-health{display:flex;align-items:center;gap:8px}.health-chip{display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:#0a1017;color:#8593a4;font-size:9px}.health-chip i{width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px rgba(94,210,155,.08)}.health-chip.warn i{background:var(--amber)}
    .content{padding:30px clamp(20px,3vw,42px) 60px}.page-view{display:none}.page-view.active{display:block}
    .command-hero{min-height:240px;padding:34px;display:flex;align-items:flex-end;justify-content:space-between;gap:24px;border:1px solid rgba(106,169,255,.15);border-radius:24px;background:radial-gradient(circle at 85% 10%,rgba(104,223,193,.13),transparent 30%),linear-gradient(135deg,#101a27,#0b121c);overflow:hidden}.hero-copy{max-width:760px}.hero-copy>span,.page-heading>div>span,.section-title>div>span,.surface-head>div>span,.treasury-balance>span,.delivery-card>span{color:var(--blue);font-size:9px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}.hero-copy h2{margin:11px 0 13px;font-size:clamp(30px,4vw,48px);line-height:1.04;letter-spacing:-.055em}.hero-copy p{margin:0;max-width:690px;color:#94a2b2;font-size:14px;line-height:1.7}.hero-date{min-width:170px;padding:17px;border:1px solid rgba(255,255,255,.07);border-radius:15px;background:rgba(5,10,16,.5)}.hero-date small,.hero-date span{display:block;color:#667588;font-size:9px}.hero-date strong{display:block;margin:4px 0;font-size:18px}
    .kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin-top:12px}.kpi{padding:20px;border:1px solid var(--line2);border-radius:17px;background:var(--panel)}.kpi>span{display:block;color:#8190a1;font-size:10px}.kpi strong{display:block;margin:8px 0 2px;font-size:28px;letter-spacing:-.04em}.kpi small{color:#5e6b7b;font-size:9px}.kpi.attention{border-color:rgba(239,195,107,.2);background:linear-gradient(180deg,rgba(239,195,107,.045),var(--panel))}
    .section-title,.page-heading,.surface-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.section-title{margin:30px 0 13px}.section-title h3,.surface-head h3{margin:3px 0 0;font-size:17px}.section-title p,.page-heading p,.surface-head>small{margin:0;color:#718093;font-size:10px}.section-title.compact{margin:0 0 14px}.page-heading{margin-bottom:18px}.page-heading h2{margin:5px 0 7px;font-size:29px;letter-spacing:-.045em}.heading-metrics{display:flex;gap:8px}.heading-metrics span{padding:8px 11px;border:1px solid var(--line);border-radius:10px;background:#0a1017;color:#7d8b9c;font-size:9px}.heading-metrics b{color:#fff;margin-left:5px}
    .asset-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.asset-card{padding:17px;border:1px solid var(--line2);border-radius:16px;background:#0c131c}.asset-head{display:flex;align-items:center;justify-content:space-between}.asset-symbol{min-width:38px;height:25px;padding:0 8px;border-radius:8px;display:grid;place-items:center;background:#121e2b;color:#b9c9da;font-size:9px;font-weight:900}.asset-head small{color:#596779;font-size:8px}.asset-card>strong{display:block;margin:14px 0 12px;font-size:17px}.asset-meta{display:flex;justify-content:space-between;gap:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.045);color:#657386;font-size:8px}.asset-meta b{color:#9ba9b9;font-size:9px}
    .dashboard-split{display:grid;grid-template-columns:.92fr 1.08fr;gap:12px;margin-top:12px}.surface{padding:20px;border:1px solid var(--line2);border-radius:18px;background:var(--panel)}.surface-head{align-items:center;margin-bottom:16px}.text-action{border:0;background:transparent;color:var(--blue);font-size:9px;font-weight:800}.pipeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.pipeline>div{padding:13px;border:1px solid rgba(255,255,255,.05);border-radius:12px;background:#0a1119}.pipeline span{display:block;color:#687789;font-size:8px}.pipeline strong{display:block;margin-top:5px;font-size:18px}.status-line{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px;color:#6f7e90;font-size:8px}.status-line span{display:flex;align-items:center;gap:6px}.status-line i,.activity-mark{width:7px;height:7px;border-radius:50%;background:#748296}.status-line i.waiting,.activity-mark.waiting{background:var(--amber)}.status-line i.working,.activity-mark.working{background:var(--blue)}.status-line i.done,.activity-mark.done{background:var(--green)}.status-line i.declined,.activity-mark.declined{background:var(--red)}
    .activity-list{display:grid;gap:2px}.activity-row{display:grid;grid-template-columns:9px 1fr auto;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid rgba(255,255,255,.045)}.activity-row:last-child{border-bottom:0}.activity-row strong{display:block;font-size:10px}.activity-row small{display:block;color:#627184;font-size:8px;margin-top:2px}.activity-row>b{font-size:9px;color:#b8c4d1}
    .health-badge{padding:8px 11px;border:1px solid rgba(94,210,155,.2);border-radius:999px;background:rgba(94,210,155,.07);color:var(--green);font-size:9px}.health-badge.danger{border-color:rgba(255,120,144,.22);background:rgba(255,120,144,.07);color:var(--red)}
    .treasury-layout{display:grid;grid-template-columns:1.25fr .75fr;gap:12px}.treasury-balance{padding:27px;border:1px solid rgba(104,223,193,.15);border-radius:21px;background:radial-gradient(circle at 90% 10%,rgba(104,223,193,.1),transparent 35%),#0e171f}.treasury-values{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:19px}.treasury-values div+div{border-left:1px solid rgba(255,255,255,.08);padding-left:18px}.treasury-values small{display:block;color:#6b798a;font-size:9px}.treasury-values strong{display:block;margin-top:5px;font-size:25px}.treasury-balance p{margin:20px 0 0;color:#627184;font-size:9px}.treasury-state{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:12px;border:1px solid var(--line2);border-radius:21px;background:var(--panel)}.treasury-state>div{display:flex;flex-direction:column;justify-content:center;align-items:center;border-right:1px solid rgba(255,255,255,.05)}.treasury-state>div:last-child{border:0}.treasury-state span{color:#6e7c8e;font-size:8px}.treasury-state strong{margin-top:7px;font-size:23px}.pool-address{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:12px 0;padding:16px 18px;border:1px solid var(--line2);border-radius:15px;background:#0b121a}.pool-address span,.case-destination span{display:block;color:#647386;font-size:8px;letter-spacing:.09em;text-transform:uppercase}.pool-address code{display:block;margin:5px 0;color:#dce5ef;font-size:11px;word-break:break-all}.pool-address small{color:#657386;font-size:8px}.flow-steps{display:flex;align-items:center;gap:10px;margin:16px 0 12px;padding:16px;border:1px solid var(--line2);border-radius:15px;background:#0a1017}.flow-steps div{display:flex;align-items:center;gap:8px;white-space:nowrap}.flow-steps b{width:27px;height:27px;border-radius:8px;display:grid;place-items:center;background:#111c29;color:var(--blue);font-size:8px}.flow-steps span{color:#9ca9b8;font-size:9px}.flow-steps>i{height:1px;flex:1;background:#223043}
    .command-filter{display:grid;grid-template-columns:minmax(280px,1fr) 180px 180px auto;gap:9px;margin-bottom:13px;padding:11px;border:1px solid var(--line2);border-radius:15px;background:#0b1119}.search-field{position:relative}.search-field span{position:absolute;left:12px;top:9px;color:#5f6e80;font-size:16px}.search-field input{padding-left:34px}.case-list{display:grid;gap:11px}.case-card{border:1px solid var(--line2);border-radius:19px;background:linear-gradient(180deg,#0e151f,#0b1119);overflow:hidden}.case-topline{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 18px;border-bottom:1px solid rgba(255,255,255,.05)}.case-identity{display:flex;align-items:center;gap:10px}.case-type{padding:5px 8px;border-radius:8px;background:#111c28;color:#8fa1b4;font-size:8px;font-weight:900;text-transform:uppercase}.case-identity>strong{font-size:12px}.case-identity>small{color:#617083;font-size:8px}.case-body{display:grid;grid-template-columns:minmax(270px,.72fr) 1.28fr;gap:0}.case-amount{padding:23px 18px;border-right:1px solid rgba(255,255,255,.05)}.case-amount>span{color:#718094;font-size:9px;text-transform:uppercase}.case-amount>strong{display:block;margin:6px 0 15px;font-size:28px;letter-spacing:-.04em}.case-breakdown{display:flex;gap:12px;flex-wrap:wrap;color:#657487;font-size:8px}.case-breakdown b{color:#aab7c6}.case-information{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;padding:13px 8px}.case-detail{min-width:0;padding:10px 12px}.case-detail span{display:block;color:#5e6d80;font-size:8px;text-transform:uppercase}.case-detail strong{display:block;margin-top:4px;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.case-destination{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:0 18px 14px;padding:13px 14px;border:1px solid rgba(106,169,255,.12);border-radius:12px;background:#09111a}.case-destination code{display:block;margin-top:4px;color:#cbd6e2;font-size:10px;word-break:break-all}.case-footer{padding:0 18px 17px}.case-complete-note{color:#59687a;font-size:8px}.case-actions{display:flex;justify-content:flex-end;gap:8px}.txid-command{display:grid;grid-template-columns:1fr auto;gap:8px;margin:9px 0}.case-alert{display:flex;flex-direction:column;gap:3px;margin:10px 0;padding:11px 13px;border:1px solid rgba(239,195,107,.17);border-radius:11px;background:rgba(239,195,107,.045)}.case-alert b{font-size:9px;color:#dec27d}.case-alert span{color:#7e8b9b;font-size:8px}.case-alert.safe{border-color:rgba(94,210,155,.17);background:rgba(94,210,155,.04)}.case-alert.safe b{color:var(--green)}.case-alert.info{border-color:rgba(106,169,255,.17);background:rgba(106,169,255,.04)}.case-alert.info b{color:var(--blue)}
    .user-command{display:grid;grid-template-columns:1fr auto;align-items:end;gap:10px;padding:17px;border:1px solid var(--line2);border-radius:17px;background:var(--panel);margin-bottom:12px}.user-command label{margin-top:0}.user-result{margin-bottom:12px}.user-profile-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:22px;border:1px solid var(--line2);border-radius:18px;background:var(--panel)}.user-profile-head h2{margin:4px 0;font-size:23px}.user-profile-head p{margin:0;color:#697789;font-size:9px}.eyebrow{color:var(--blue);font-size:8px;letter-spacing:.14em;font-weight:900}.profile-badges{display:flex;gap:7px}.mini-balance-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin:10px 0}.mini-balance{padding:14px;border:1px solid var(--line2);border-radius:14px;background:#0b121a}.mini-asset{display:flex;justify-content:space-between}.mini-asset span{color:#b7c4d2;font-size:9px;font-weight:900}.mini-asset small{color:#536173;font-size:8px}.mini-balance>strong{display:block;margin:10px 0 8px;font-size:13px}.mini-pending{padding-top:8px;border-top:1px solid rgba(255,255,255,.045);color:#617083;font-size:8px}.user-workspace{display:grid;grid-template-columns:1fr 1fr;gap:10px}.history-grid{margin-top:10px}.panel-card,.compact-card{padding:18px;border:1px solid var(--line2);border-radius:17px;background:var(--panel)}.section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:13px}.section-head h2,.section-head h3{margin:3px 0 0}.section-head p{margin:0;color:#697789;font-size:9px}.security-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.security-actions form,.security-actions .btn{width:100%}
    .broadcast-layout{display:grid;grid-template-columns:1.2fr .8fr;gap:12px}.compose-card textarea{min-height:220px}.compose-foot{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:12px}.compose-foot small{color:#627184;font-size:8px}.delivery-card{padding:25px;border:1px solid rgba(106,169,255,.15);border-radius:18px;background:linear-gradient(145deg,rgba(106,169,255,.07),#0d151f)}.delivery-card strong{display:block;margin:18px 0 2px;font-size:46px}.delivery-card p{margin:0;color:#718094}.delivery-card div{margin-top:28px;padding-top:16px;border-top:1px solid rgba(255,255,255,.06);color:#7e8c9d;font-size:9px}.delivery-card i{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:7px}
    .settings-surface{padding:18px}.settings-nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px;padding:5px;border:1px solid rgba(255,255,255,.05);border-radius:12px;background:#080e15}.setting-tab{border:0;border-radius:8px;background:transparent;color:#788698;padding:8px 11px;font-size:8px;font-weight:900}.setting-tab.active{background:#121e2b;color:var(--blue)}.setting-pane{display:none}.setting-pane.active{display:block}.settings-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.field{min-width:0}.save-bar{display:flex;justify-content:flex-end;margin-top:17px}
    .admin-account-list{display:grid;gap:10px;margin-top:10px}.admin-account{padding:18px;border:1px solid var(--line2);border-radius:17px;background:var(--panel)}.root-account,.admin-account-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.admin-account h3{margin:4px 0}.admin-account p{margin:0;color:#687789;font-size:8px}.admin-account-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:12px}.permission-title{margin-top:14px}.permission-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}.permission-option{display:flex;align-items:center;gap:8px;margin:0;padding:9px;border:1px solid rgba(255,255,255,.05);border-radius:9px;background:#0b121a;color:#9eacbb;font-size:8px}.permission-option input{width:auto;min-height:0;margin:0;accent-color:var(--blue)}.admin-account-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}
    label{display:block;margin:10px 0 6px;color:#8e9bac;font-size:8px;font-weight:850;text-transform:uppercase;letter-spacing:.04em}input,select,textarea{width:100%;min-height:40px;border:1px solid var(--line);border-radius:10px;background:#080e15;color:var(--text);padding:9px 11px;outline:none}textarea{resize:vertical}input:focus,select:focus,textarea:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(106,169,255,.07)}
    .form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.form-grid .wide{grid-column:1/-1}.balance-form{grid-template-columns:repeat(3,minmax(0,1fr))}.balance-form .wide{grid-column:1/-1}.submit-cell{display:flex;align-items:flex-end}.btn{min-height:38px;padding:0 13px;border:1px solid var(--line);border-radius:10px;background:#101924;color:#c8d3df;font-size:9px;font-weight:900}.btn.primary{border-color:#f3f6fa;background:#f3f6fa;color:#07111a}.btn.positive,.btn.success{border-color:rgba(94,210,155,.25);background:rgba(94,210,155,.1);color:#78dfaa}.btn.negative,.btn.danger{border-color:rgba(255,120,144,.23);background:rgba(255,120,144,.08);color:#ff8fa2}.btn.subtle,.btn.ghost{background:#0d1620;color:#99a8b8}.copy-control{min-height:34px;padding:0 11px;border:1px solid var(--line);border-radius:9px;background:#101924;color:#aab8c7;font-size:8px;font-weight:850}
    .pill{display:inline-flex;padding:6px 9px;border:1px solid rgba(94,210,155,.18);border-radius:999px;background:rgba(94,210,155,.06);color:var(--green);font-size:8px}.danger-pill{border-color:rgba(255,120,144,.2);background:rgba(255,120,144,.06);color:var(--red)}.status{display:inline-flex;align-items:center;gap:6px;padding:6px 8px;border:1px solid var(--line);border-radius:999px;background:#0b121a;color:#8d9aaa;font-size:8px}.status i{width:6px;height:6px;border-radius:50%;background:#748296}.status.waiting i{background:var(--amber)}.status.working i{background:var(--blue)}.status.done i{background:var(--green)}.status.declined i{background:var(--red)}
    .table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:760px}th,td{padding:11px 10px;border-bottom:1px solid rgba(255,255,255,.045);text-align:left;font-size:9px}th{color:#5d6b7c;font-size:8px;text-transform:uppercase;letter-spacing:.05em}td{color:#9eacbb}.muted-cell{text-align:center;color:#5f6d7e;padding:26px}.address-cell,.tx-cell{display:block;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .empty-state{min-height:140px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;padding:20px;border:1px dashed #263446;border-radius:14px;background:rgba(255,255,255,.012);color:#647386;text-align:center}.empty-state b{color:#cbd5df}.compact-empty{min-height:100px}.error-state{border-color:rgba(255,120,144,.24)}.toast{position:fixed;right:22px;top:18px;z-index:60;max-width:min(390px,calc(100vw - 32px));padding:12px 15px;border:1px solid rgba(94,210,155,.24);border-radius:12px;background:#10231c;color:#8ce8b7;box-shadow:0 20px 70px rgba(0,0,0,.42);font-size:10px}.toast-error{border-color:rgba(255,120,144,.25);background:#281219;color:#ff96a8}
    @media(max-width:1280px){.asset-grid,.mini-balance-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.settings-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.case-information{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:980px){.control-shell{grid-template-columns:1fr}.sidebar{position:fixed;left:-300px;width:286px;transition:.22s left;box-shadow:20px 0 80px rgba(0,0,0,.42)}body.sidebar-open .sidebar{left:0}.menu-toggle{display:grid;place-items:center}.system-health .health-chip:not(:first-child){display:none}.dashboard-split,.treasury-layout,.broadcast-layout,.user-workspace{grid-template-columns:1fr}.command-filter{grid-template-columns:1fr 1fr}.search-field{grid-column:1/-1}.case-body{grid-template-columns:1fr}.case-amount{border-right:0;border-bottom:1px solid rgba(255,255,255,.05)}}
    @media(max-width:640px){.content{padding:20px 13px 42px}.topbar{height:66px;padding:0 13px}.breadcrumbs h1{font-size:17px}.command-hero{min-height:auto;padding:24px;display:block}.hero-copy h2{font-size:33px}.hero-date{margin-top:24px}.kpi-grid,.asset-grid,.mini-balance-grid,.pipeline,.treasury-state,.settings-grid,.permission-grid,.security-actions,.balance-form,.form-grid{grid-template-columns:1fr}.treasury-values{grid-template-columns:1fr}.treasury-values div+div{border-left:0;border-top:1px solid rgba(255,255,255,.08);padding:14px 0 0}.flow-steps{display:grid;grid-template-columns:1fr}.flow-steps>i{display:none}.command-filter,.user-command{grid-template-columns:1fr}.case-identity{align-items:flex-start;flex-direction:column;gap:4px}.case-information{grid-template-columns:1fr}.case-destination,.pool-address,.compose-foot,.page-heading,.section-title,.surface-head,.user-profile-head,.root-account,.admin-account-head{align-items:flex-start;flex-direction:column}.case-actions,.admin-account-actions,.txid-command{display:grid;grid-template-columns:1fr}.case-actions .btn,.admin-account-actions .btn,.copy-control{width:100%}.heading-metrics{display:none}.admin-account-grid{grid-template-columns:1fr}}
    """

    panel_script = r"""
    const views=[...document.querySelectorAll('[data-view]')];
    const navEntries=[...document.querySelectorAll('[data-view-target]')];
    const title=document.getElementById('page-title');
    function openView(name,update=true){
      views.forEach(v=>v.classList.toggle('active',v.dataset.view===name));
      navEntries.forEach(n=>n.classList.toggle('active',n.dataset.viewTarget===name));
      const current=navEntries.find(n=>n.dataset.viewTarget===name);
      if(current&&title)title.textContent=current.dataset.viewTitle||'Kontrol Merkezi';
      if(update){const url=new URL(location.href);url.searchParams.set('view',name);history.replaceState(null,'',url);}
      document.body.classList.remove('sidebar-open');window.scrollTo({top:0,behavior:'smooth'});
    }
    navEntries.forEach(n=>n.addEventListener('click',()=>openView(n.dataset.viewTarget)));
    document.querySelectorAll('[data-jump-view]').forEach(b=>b.addEventListener('click',()=>openView(b.dataset.jumpView)));
    const menu=document.getElementById('menu-toggle');if(menu)menu.addEventListener('click',()=>document.body.classList.toggle('sidebar-open'));

    const filter=document.getElementById('request-filter');
    async function refreshRequests(){if(!filter)return;const params=new URLSearchParams(new FormData(filter));try{const res=await fetch('/admin/requests-fragment?'+params.toString(),{cache:'no-store'});if(res.ok)document.getElementById('request-list').innerHTML=await res.text();}catch(e){console.log(e);}}
    if(filter)filter.addEventListener('submit',e=>{e.preventDefault();refreshRequests();});
    const requestView=document.querySelector('[data-view="requests"]');setInterval(()=>{if(requestView&&requestView.classList.contains('active'))refreshRequests();},20000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&requestView&&requestView.classList.contains('active'))refreshRequests();});

    const lookup=document.getElementById('user-lookup');if(lookup)lookup.addEventListener('submit',async e=>{e.preventDefault();const uid=document.getElementById('manage-user-id').value.trim();const result=document.getElementById('user-management-result');result.innerHTML="<div class='empty-state'>Kullanıcı yükleniyor…</div>";try{const res=await fetch('/admin/user-fragment?uid='+encodeURIComponent(uid),{cache:'no-store'});result.innerHTML=res.ok?await res.text():"<div class='empty-state error-state'>Kullanıcı bilgisi alınamadı.</div>";const url=new URL(location.href);url.searchParams.set('view','users');if(uid)url.searchParams.set('manage_user_id',uid);else url.searchParams.delete('manage_user_id');history.replaceState(null,'',url);}catch(err){result.innerHTML="<div class='empty-state error-state'>Bağlantı hatası oluştu.</div>";}});

    const tabs=[...document.querySelectorAll('[data-setting-target]')],panes=[...document.querySelectorAll('[data-setting-pane]')];tabs.forEach(tab=>tab.addEventListener('click',()=>{tabs.forEach(x=>x.classList.toggle('active',x===tab));panes.forEach(p=>p.classList.toggle('active',p.dataset.settingPane===tab.dataset.settingTarget));}));
    document.addEventListener('click',async e=>{const button=e.target.closest('[data-copy]');if(!button)return;try{await navigator.clipboard.writeText(button.dataset.copy||'');const old=button.textContent;button.textContent='Kopyalandı';setTimeout(()=>button.textContent=old,1200);}catch(err){button.textContent='Kopyalanamadı';}});
    const toast=document.getElementById('admin-toast');if(toast)setTimeout(()=>toast.remove(),4300);
    """

    signer_online = bool(WITHDRAW_SIGNER_URL)
    active_title = next((label for slug, label, number, group in nav_items if slug == active_view), "Kontrol Merkezi")
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'>
    <title>Nerlo Treasury Control · V11</title><style>{panel_css}</style></head><body>{notice_html}
    <div class='control-shell'>
      <aside class='sidebar'><div class='brand'><div class='brand-mark'>N</div><div><strong>Nerlo Wallet</strong><small>Treasury Control · V11</small></div></div><nav class='nav-scroll'>{nav_html}</nav>
        <div class='sidebar-footer'><div class='operator'><div class='operator-avatar'>{h(current_panel_username()[:1].upper() or 'N')}</div><div><strong>{h(current_panel_username())}</strong><small>Yetkili operasyon hesabı</small></div></div><a class='signout' href='/logout'>Güvenli çıkış yap</a></div>
      </aside>
      <section class='workspace'><header class='topbar'><div class='topbar-left'><button id='menu-toggle' class='menu-toggle' type='button'>☰</button><div class='breadcrumbs'><span>NERLO / OPERASYON</span><h1 id='page-title'>{h(active_title)}</h1></div></div><div class='system-health'><span class='health-chip'><i></i>Ledger aktif</span><span class='health-chip {'warn' if not signer_online else ''}'><i></i>{'Signer bağlı' if signer_online else 'Signer bekliyor'}</span><span class='health-chip'><i></i>Çevrim içi</span></div></header>
        <main class='content'>{dashboard_section}{pool_section}{requests_section}{users_section}{approvals_section}{broadcast_section}{settings_section}{logs_section}{admins_section}</main>
      </section>
    </div><script>{panel_script}</script></body></html>"""


@app.route("/admin/user/<uid>")
def admin_user(uid):
    if not logged_in(): return redirect("/login")
    if not has_panel_permission("users"): abort(403)
    return redirect(f"/admin?view=users&manage_user_id={uid}")


_background_services_started = False
_background_services_lock = threading.Lock()


def start_background_services_once():
    global _background_services_started
    with _background_services_lock:
        if _background_services_started:
            return
        validate_runtime_config()
        if SERVICE_ROLE == "signer":
            if SIGNER_BROADCAST_ENABLED:
                threading.Thread(
                    target=_run_singleton_polling_service,
                    args=("signer-confirmations", signer_confirmation_once, SIGNER_CONFIRM_POLL_SECONDS),
                    daemon=True,
                    name="signer-confirmations",
                ).start()
                if TRON_POOL_ADDRESS:
                    threading.Thread(
                        target=_run_singleton_polling_service,
                        args=("signer-sweeps", signer_sweep_once, TRON_SWEEP_POLL_SECONDS),
                        daemon=True,
                        name="signer-sweeps",
                    ).start()
            _background_services_started = True
            runtime = _signer_runtime_state()
            print("BUILD VERSION:", BUILD_VERSION)
            print("SERVICE ROLE: signer")
            print("SIGNER STAGE:", SIGNER_STAGE)
            print("SIGNER BROADCAST ENABLED:", SIGNER_BROADCAST_ENABLED)
            print("SIGNER READY:", runtime.get("ready"))
            print("SIGNER HOT WALLET:", runtime.get("derived_address") or "not-loaded")
            print("TRON SWEEP ENABLED:", runtime.get("sweep_enabled"))
            print("TRON SWEEP READY:", runtime.get("sweep_ready"))
            return
        if not BACKGROUND_SERVICES_ENABLED:
            _background_services_started = True
            print("BUILD VERSION:", BUILD_VERSION)
            print("SERVICE ROLE: app (background services disabled)")
            return
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
        print("SERVICE ROLE: app")
        print("EXCHANGE HEALTH:", exchange_health_snapshot())
        print("WALLET MODE: PostgreSQL ledger + durable queue + watch-only HD addresses + blockchain indexers")


start_background_services_once()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
