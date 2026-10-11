import hashlib
import sqlite3
import secrets
import time
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, List, Dict

# Fix C: Absolute DB path relative to app/ directory
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("JANUS_DB_PATH", str(BASE_DIR / "app.db")))

# Fix A: Context manager that guarantees conn.close()
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Initializes SQLite database tables and enables WAL mode."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Fix E: Enable WAL mode & busy timeout to prevent write locks
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairing_codes (
                code TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                last_used REAL NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                widget_type TEXT NOT NULL,
                widget_label TEXT NOT NULL,
                default_val TEXT
            )
        """)
        # Additive migrations: retain existing phone pairing and script records.
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS agent_devices (
                device_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                name TEXT NOT NULL, agent_hash BLOB UNIQUE NOT NULL,
                controller_hash BLOB UNIQUE NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS panel_layouts (
                device_id TEXT PRIMARY KEY REFERENCES agent_devices(device_id),
                layout_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS panel_actions (
                device_id TEXT NOT NULL REFERENCES agent_devices(device_id),
                action_id TEXT NOT NULL, request_json TEXT NOT NULL,
                PRIMARY KEY(device_id, action_id)
            );
            CREATE TABLE IF NOT EXISTS panel_pairing (
                code TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES agent_devices(device_id),
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS panel_clients (
                phone_id TEXT PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
                device_id TEXT NOT NULL REFERENCES agent_devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS agent_jobs (
                job_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                owner_id TEXT NOT NULL, request_json TEXT NOT NULL,
                view_json TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS agent_jobs_scope ON agent_jobs(device_id, owner_id, created_at);
            CREATE TABLE IF NOT EXISTS generated_programs (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                scope_key TEXT NOT NULL, request_json TEXT NOT NULL,
                program_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS programs_scope ON generated_programs(device_id, owner_id, scope_key, created_at);
        """)
        conn.commit()

# --- PAIRING CODE LOGIC ---

def generate_pairing_code(ttl_seconds: int = 60) -> str:
    code = secrets.token_hex(3).upper()
    now = time.time()
    expires_at = now + ttl_seconds
    
    with get_db() as conn:
        cursor = conn.cursor()
        # Clean up all expired, abandoned codes before inserting new code
        cursor.execute("DELETE FROM pairing_codes WHERE expires_at < ?", (now,))
        cursor.execute(
            "INSERT INTO pairing_codes (code, expires_at) VALUES (?, ?)", 
            (code, expires_at)
        )
        conn.commit()
    return code

def verify_and_consume_code(code: str) -> bool:
    if not code or not isinstance(code, str):
        return False

    code = code.strip().upper()
    now = time.time()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pairing_codes WHERE code = ? RETURNING expires_at", (code,))
        row = cursor.fetchone()
        conn.commit()
        return row is not None and now < row["expires_at"]

# --- DEVICE MANAGEMENT ---

def hash_token(token: str) -> str:
    """Computes SHA-256 hash of a token string."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def register_device(device_name: str) -> dict:
    device_id = f"dev_{secrets.token_hex(4)}"
    raw_token = f"pwa_{secrets.token_hex(24)}"
    token_hash = hash_token(raw_token)
    now = time.time()
    
    with get_db() as conn:
        conn.execute(
            "INSERT INTO devices (device_id, name, token, created_at, last_used) VALUES (?, ?, ?, ?, ?)",
            (device_id, device_name, token_hash, now, now)
        )
        conn.commit()
        
    return {"device_id": device_id, "name": device_name, "token": raw_token}

def validate_device_token(token: str) -> Optional[dict]:
    token_hash = hash_token(token)
    now = time.time()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM devices WHERE token = ?", (token_hash,))
        row = cursor.fetchone()
        
        if row:
            cursor.execute("UPDATE devices SET last_used = ? WHERE token = ?", (now, token_hash))
            conn.commit()
            return dict(row)
    return None

def get_all_devices() -> List[dict]:
    """Retrieves all registered devices with human-readable connection status."""
    now = time.time()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT device_id, name, created_at, last_used FROM devices")
        rows = cursor.fetchall()
        
        devices = []
        for r in rows:
            device_dict = dict(r)
            # Active if communicated in the last 5 minutes
            device_dict["is_active"] = (now - device_dict["last_used"]) < 300
            devices.append(device_dict)
        return devices

def revoke_device_by_id(device_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        conn.commit()
        return cursor.rowcount > 0

# --- SCRIPT STORAGE ---

def save_script(script_id: str, name: str, code: str, widget_type: str, label: str, default_val: Optional[str] = None):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO scripts (id, name, code, widget_type, widget_label, default_val)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (script_id, name, code, widget_type, label, default_val))
        conn.commit()

def get_all_scripts() -> List[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scripts")
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "name": row["name"],
                "code": row["code"],
                "widget": {
                    "widget_type": row["widget_type"],
                    "label": row["widget_label"],
                    "default_val": row["default_val"]
                }
            })
        return results

def get_script_by_id(script_id: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scripts WHERE id = ?", (script_id,))
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "name": row["name"],
                "code": row["code"],
                "widget": {
                    "widget_type": row["widget_type"],
                    "label": row["widget_label"],
                    "default_val": row["default_val"]
                }
            }
    return None

def delete_script_by_id(script_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        conn.commit()
        return cursor.rowcount > 0
