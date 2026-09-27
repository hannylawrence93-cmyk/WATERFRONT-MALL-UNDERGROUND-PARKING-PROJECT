"""
database.py
-----------
SQLite layer. Schema is "dynamic" in the same spirit as v1: LEVELS in
config.py can be edited (new level, more slots per type) and
ensure_slots() will INSERT only the missing rows - existing slots,
sessions and users are never touched.

Tables
------
users     : login accounts (attendant / manager)
slots     : every physical bay: level + type (regular/vip/accessible)
sessions  : append-only log of every vehicle that passed through
settings  : key/value store for parking rates, VIP surcharge, VAT rate
            - lets a manager change pricing from the web UI at any
              time, with NO code change or redeploy (Objective 1.2)
"""

import json
import sqlite3
from contextlib import contextmanager
from werkzeug.security import generate_password_hash

from config import LEVELS, SEED_USERS, DB_PATH, VEHICLE_TYPES, VIP_SURCHARGE, VAT_RATE


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'attendant'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_number TEXT UNIQUE NOT NULL,
                level TEXT NOT NULL,
                slot_type TEXT NOT NULL DEFAULT 'regular',  -- regular|vip|accessible
                status TEXT NOT NULL DEFAULT 'available',   -- available|occupied|out_of_service
                plate_number TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_number TEXT NOT NULL,
                vehicle_type TEXT NOT NULL DEFAULT 'car',
                slot_id INTEGER NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                fee REAL,
                vat_amount REAL,
                payment_method TEXT,
                payment_ref TEXT,
                status TEXT NOT NULL DEFAULT 'active',   -- active|completed
                FOREIGN KEY (slot_id) REFERENCES slots (id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        ensure_slots(conn)
        ensure_seed_users(conn)
        ensure_settings(conn)
        ensure_columns(conn)


def ensure_slots(conn):
    """Grow the slots table to match config.LEVELS without touching
    existing rows. Slot numbers look like L1-R01, L1-V01, L1-A01, etc."""
    existing = {row["slot_number"] for row in conn.execute("SELECT slot_number FROM slots")}
    prefix = {"regular": "R", "vip": "V", "accessible": "A"}

    for level, counts in LEVELS.items():
        for slot_type, count in counts.items():
            for i in range(1, count + 1):
                slot_number = f"{level}-{prefix[slot_type]}{i:02d}"
                if slot_number not in existing:
                    conn.execute(
                        "INSERT INTO slots (slot_number, level, slot_type, status) "
                        "VALUES (?, ?, ?, 'available')",
                        (slot_number, level, slot_type),
                    )


def ensure_seed_users(conn):
    existing = {row["username"] for row in conn.execute("SELECT username FROM users")}
    for u in SEED_USERS:
        if u["username"] not in existing:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (u["username"], generate_password_hash(u["password"]), u["role"]),
            )


def ensure_settings(conn):
    """Seed the settings table from config.py defaults on first run
    only. After that, the settings table is the single source of
    truth for pricing - config.py is never read again for rates, so a
    manager can change prices from /admin/rates with zero code changes
    and zero restarts (Objective 1.2)."""
    existing = {row["key"] for row in conn.execute("SELECT key FROM settings")}
    defaults = {
        "vehicle_types": json.dumps(VEHICLE_TYPES),
        "vip_surcharge": json.dumps(VIP_SURCHARGE),
        "vat_rate": json.dumps(VAT_RATE),
    }
    for key, value in defaults.items():
        if key not in existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def ensure_columns(conn):
    """Lightweight migration: add columns that didn't exist in earlier
    versions of this schema, without touching existing data. Safe to
    run on every startup."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "vat_amount" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN vat_amount REAL")
    if "payment_status" not in cols:
        # NULL until a payment attempt starts; 'pending' while waiting on
        # a real M-Pesa STK push, 'confirmed' or 'failed' once Safaricom's
        # callback arrives. Cash/card/simulated M-Pesa skip straight to
        # a completed session and never need this column.
        conn.execute("ALTER TABLE sessions ADD COLUMN payment_status TEXT")
    if "mpesa_checkout_request_id" not in cols:
        # Correlates an outstanding STK push with the session it belongs
        # to when Safaricom's asynchronous callback arrives later.
        conn.execute("ALTER TABLE sessions ADD COLUMN mpesa_checkout_request_id TEXT")
