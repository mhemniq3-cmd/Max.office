import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from cloud_server.signer import build_and_sign_token
except ImportError:
    from signer import build_and_sign_token

DB_PATH = Path(os.environ.get("MAXPRO_LICENSE_DB_PATH", Path(__file__).resolve().parent / "licenses.db"))



def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target = db_path or DB_PATH
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT UNIQUE NOT NULL,
                store_name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                status TEXT DEFAULT 'ACTIVE',
                plan_code TEXT DEFAULT '12_months',
                plan_name_ar TEXT DEFAULT 'اشتراك سنوي (12 شهراً)',
                tier TEXT DEFAULT 'pro',
                expires_at TEXT NOT NULL,
                grace_days INTEGER DEFAULT 5,
                current_token TEXT NOT NULL,
                last_sync_at TEXT,
                created_at TEXT NOT NULL,
                notes TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_machine_id ON devices (machine_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS server_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def get_setting(key: str, default: str = "", db_path: Optional[Path] = None) -> str:
    conn = get_connection(db_path)
    row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row["value"])
    return default


def set_setting(key: str, value: str, db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO server_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    now = datetime.now(timezone.utc)
    try:
        exp_dt = datetime.fromisoformat(d["expires_at"])
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        diff = exp_dt - now
        days_rem = int(diff.total_seconds() // 86400)
        d["days_remaining"] = max(0, days_rem)
        d["is_expired"] = exp_dt <= now
    except Exception:
        d["days_remaining"] = 0
        d["is_expired"] = True

    d["is_trial"] = (d.get("status") == "TRIAL" or d.get("plan_code") == "trial")
    
    # Calculate visual progress percentage (0 - 100%)
    if d["is_trial"]:
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 14.0) * 100)))
    elif d.get("plan_code") == "monthly":
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 30.0) * 100)))
    else:
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 365.0) * 100)))

    return d


def get_device(machine_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM devices WHERE machine_id = ?", (mid,))
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_devices(search: str = "", status_filter: str = "", db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    query = "SELECT * FROM devices WHERE 1=1"
    params: list[Any] = []

    if search:
        pattern = f"%{search.strip()}%"
        query += " AND (machine_id LIKE ? OR store_name LIKE ? OR phone LIKE ?)"
        params.extend([pattern, pattern, pattern])

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter.strip().upper())

    query += " ORDER BY id DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def register_or_create_device(
    machine_id: str,
    store_name: str,
    phone: str = "",
    status: str = "ACTIVE",
    months: int = 12,
    tier: str = "pro",
    grace_days: int = 5,
    notes: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)

    # Compute expiry and plan labels
    if status == "TRIAL" or months == 0:
        exp_dt = now + timedelta(days=14)
        plan_code = "trial"
        plan_ar = "نسخة تجريبية مجانية (14 يوماً)"
        actual_status = "TRIAL"
        actual_tier = "trial"
        actual_grace = 0
    elif months >= 120:
        exp_dt = now + timedelta(days=36500)
        plan_code = "lifetime"
        plan_ar = "ترخيص مدى الحياة (Lifetime)"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days
    elif months == 1:
        exp_dt = now + timedelta(days=31)
        plan_code = "monthly"
        plan_ar = "اشتراك شهري (1 شهر)"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days
    elif months == 3:
        exp_dt = now + timedelta(days=93)
        plan_code = "quarterly"
        plan_ar = "اشتراك 3 أشهر"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days
    elif months == 6:
        exp_dt = now + timedelta(days=186)
        plan_code = "semi_annual"
        plan_ar = "اشتراك 6 أشهر"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days
    else:
        exp_dt = now + timedelta(days=int(months * 30.5))
        plan_code = f"{months}_months"
        plan_ar = f"اشتراك سنوي ({months} شهراً - Pro)"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days

    token, _ = build_and_sign_token(
        machine_id=mid,
        store_name=store_name,
        expires_at=exp_dt,
        plan_code=plan_code,
        plan_name_ar=plan_ar,
        tier=actual_tier,
        grace_days=actual_grace,
    )

    with conn:
        conn.execute(
            """
            INSERT INTO devices (
                machine_id, store_name, phone, status, plan_code, plan_name_ar,
                tier, expires_at, grace_days, current_token, created_at, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(machine_id) DO UPDATE SET
                store_name = excluded.store_name,
                phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE devices.phone END,
                status = excluded.status,
                plan_code = excluded.plan_code,
                plan_name_ar = excluded.plan_name_ar,
                tier = excluded.tier,
                expires_at = excluded.expires_at,
                grace_days = excluded.grace_days,
                current_token = excluded.current_token,
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE devices.notes END
            """,
            (
                mid,
                store_name.strip(),
                phone.strip(),
                actual_status,
                plan_code,
                plan_ar,
                actual_tier,
                exp_dt.isoformat(),
                actual_grace,
                token,
                now.isoformat(),
                notes.strip(),
            ),
        )

    return get_device(mid, db_path=db_path) or {}


def extend_trial(
    machine_id: str,
    days: int = 7,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extend trial period for a prospect/lead by N additional days."""
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل: {mid}")

    now = datetime.now(timezone.utc)
    try:
        curr_exp = datetime.fromisoformat(device["expires_at"])
        if curr_exp.tzinfo is None:
            curr_exp = curr_exp.replace(tzinfo=timezone.utc)
        base = curr_exp if curr_exp > now else now
    except Exception:
        base = now

    new_exp = base + timedelta(days=days)
    plan_ar = f"فترة تجريبية ممددة ({days} أيام إضافية)"

    new_token, _ = build_and_sign_token(
        machine_id=mid,
        store_name=device["store_name"],
        expires_at=new_exp,
        plan_code="trial",
        plan_name_ar=plan_ar,
        tier="trial",
        grace_days=0,
    )

    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            UPDATE devices
            SET expires_at = ?,
                plan_code = 'trial',
                plan_name_ar = ?,
                current_token = ?,
                status = 'TRIAL'
            WHERE machine_id = ?
            """,
            (new_exp.isoformat(), plan_ar, new_token, mid),
        )

    return get_device(mid, db_path=db_path) or {}


def renew_device(
    machine_id: str,
    months: int = 12,
    tier: Optional[str] = None,
    grace_days: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل في النظام: {mid}")

    now = datetime.now(timezone.utc)
    # If currently active and expiring in future, add time to existing expiry!
    # If device was in TRIAL, calculate the paid subscription from now:
    is_trial_device = (device.get("status") == "TRIAL" or device.get("plan_code") == "trial")
    try:
        current_exp = datetime.fromisoformat(device["expires_at"])
        if current_exp.tzinfo is None:
            current_exp = current_exp.replace(tzinfo=timezone.utc)
        base_date = current_exp if (current_exp > now and not is_trial_device) else now
    except Exception:
        base_date = now

    if months >= 120:
        new_exp = now + timedelta(days=36500)
        plan_code = "lifetime"
        plan_ar = "ترخيص مدى الحياة (Lifetime)"
    elif months == 1:
        new_exp = base_date + timedelta(days=31)
        plan_code = "monthly"
        plan_ar = "اشتراك شهري (1 شهر)"
    elif months == 3:
        new_exp = base_date + timedelta(days=93)
        plan_code = "quarterly"
        plan_ar = "اشتراك 3 أشهر"
    elif months == 6:
        new_exp = base_date + timedelta(days=186)
        plan_code = "semi_annual"
        plan_ar = "اشتراك 6 أشهر"
    else:
        new_exp = base_date + timedelta(days=int(months * 30.5))
        plan_code = f"{months}_months"
        plan_ar = f"اشتراك سنوي ({months} شهراً - Pro)"

    target_tier = tier or device["tier"] or "pro"
    target_grace = grace_days if grace_days is not None else int(device.get("grace_days", 5))

    new_token, _ = build_and_sign_token(
        machine_id=mid,
        store_name=device["store_name"],
        expires_at=new_exp,
        plan_code=plan_code,
        plan_name_ar=plan_ar,
        tier=target_tier,
        grace_days=target_grace,
    )

    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            UPDATE devices
            SET expires_at = ?,
                plan_code = ?,
                plan_name_ar = ?,
                tier = ?,
                grace_days = ?,
                current_token = ?,
                status = 'ACTIVE'
            WHERE machine_id = ?
            """,
            (
                new_exp.isoformat(),
                plan_code,
                plan_ar,
                target_tier,
                target_grace,
                new_token,
                mid,
            ),
        )

    return get_device(mid, db_path=db_path) or {}


def toggle_suspend(machine_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل: {mid}")

    new_status = "SUSPENDED" if device["status"] == "ACTIVE" else "ACTIVE"
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            "UPDATE devices SET status = ? WHERE machine_id = ?",
            (new_status, mid),
        )
    return get_device(mid, db_path=db_path) or {}


def record_sync(machine_id: str, store_name: str = "", db_path: Optional[Path] = None) -> None:
    mid = machine_id.strip().upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    with conn:
        if store_name:
            conn.execute(
                "UPDATE devices SET last_sync_at = ?, store_name = CASE WHEN store_name='' THEN ? ELSE store_name END WHERE machine_id = ?",
                (now_iso, store_name.strip(), mid),
            )
        else:
            conn.execute(
                "UPDATE devices SET last_sync_at = ? WHERE machine_id = ?",
                (now_iso, mid),
            )


def delete_device(machine_id: str, db_path: Optional[Path] = None) -> bool:
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    with conn:
        cur = conn.execute("DELETE FROM devices WHERE machine_id = ?", (mid,))
        return cur.rowcount > 0
