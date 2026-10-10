from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from cloud_server.signer import build_and_sign_token
except ImportError:
    from signer import build_and_sign_token

try:
    from cloud_server.notifier import (
        notify_new_store_onboarded,
        notify_subscription_renewed,
        notify_status_changed,
    )
except ImportError:
    try:
        from notifier import (
            notify_new_store_onboarded,
            notify_subscription_renewed,
            notify_status_changed,
        )
    except ImportError:
        notify_new_store_onboarded = lambda *args, **kwargs: None  # type: ignore
        notify_subscription_renewed = lambda *args, **kwargs: None  # type: ignore
        notify_status_changed = lambda *args, **kwargs: None  # type: ignore

DB_PATH = Path(os.environ.get("MAXPRO_LICENSE_DB_PATH", Path(__file__).resolve().parent / "licenses.db"))
BACKUP_JSON_PATH = Path(os.environ.get("MAXPRO_LICENSE_BACKUP_PATH", DB_PATH.parent / "licenses_backup.json"))


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target = db_path or DB_PATH
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn


def _save_db_backup(db_path: Optional[Path] = None) -> None:
    """Save persistent JSON snapshot of all devices and settings."""
    try:
        conn = get_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM devices")
        devices = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM revoked_devices")
        revoked = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM server_settings")
        settings = [dict(r) for r in cur.fetchall()]

        data = {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "devices": devices,
            "revoked_devices": revoked,
            "server_settings": settings,
        }
        target_path = BACKUP_JSON_PATH if db_path is None else (Path(db_path).parent / "licenses_backup.json")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _restore_db_from_backup_if_empty(db_path: Optional[Path] = None) -> None:
    """Restore devices from persistent JSON snapshot if the database file was reset."""
    try:
        target_path = BACKUP_JSON_PATH if db_path is None else (Path(db_path).parent / "licenses_backup.json")
        if not target_path.exists():
            return
        import json
        data = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return

        conn = get_connection(db_path)
        devices = data.get("devices", [])
        revoked = data.get("revoked_devices", [])
        settings = data.get("server_settings", [])

        with conn:
            for d in devices:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO devices (
                        machine_id, store_name, phone, status, plan_code, plan_name_ar,
                        tier, expires_at, grace_days, current_token, last_sync_at, created_at,
                        notes, branch_name, company_name, today_sales, today_profit,
                        today_invoices, cash_in_drawer, metrics_updated_at, owner_pin
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.get("machine_id", ""),
                        d.get("store_name", ""),
                        d.get("phone", ""),
                        d.get("status", "ACTIVE"),
                        d.get("plan_code", "12_months"),
                        d.get("plan_name_ar", "اشتراك سنوي"),
                        d.get("tier", "pro"),
                        d.get("expires_at", ""),
                        d.get("grace_days", 5),
                        d.get("current_token", ""),
                        d.get("last_sync_at", ""),
                        d.get("created_at", ""),
                        d.get("notes", ""),
                        d.get("branch_name", ""),
                        d.get("company_name", ""),
                        d.get("today_sales", 0.0),
                        d.get("today_profit", 0.0),
                        d.get("today_invoices", 0),
                        d.get("cash_in_drawer", 0.0),
                        d.get("metrics_updated_at", ""),
                        d.get("owner_pin", "1234"),
                    ),
                )
            for r in revoked:
                conn.execute(
                    "INSERT OR IGNORE INTO revoked_devices (machine_id, revoked_at, reason) VALUES (?, ?, ?)",
                    (r.get("machine_id", ""), r.get("revoked_at", ""), r.get("reason", "deleted_by_admin")),
                )
            for s in settings:
                conn.execute(
                    "INSERT OR IGNORE INTO server_settings (key, value) VALUES (?, ?)",
                    (s.get("key", ""), s.get("value", "")),
                )
    except Exception:
        pass


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in cursor.fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


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
                notes TEXT DEFAULT '',
                branch_name TEXT DEFAULT '',
                company_name TEXT DEFAULT '',
                today_sales REAL DEFAULT 0.0,
                today_profit REAL DEFAULT 0.0,
                today_invoices INTEGER DEFAULT 0,
                cash_in_drawer REAL DEFAULT 0.0,
                metrics_updated_at TEXT DEFAULT '',
                owner_pin TEXT DEFAULT '1234'
            )
            """
        )

        for col, ctype in [
            ("branch_name", "TEXT DEFAULT ''"),
            ("company_name", "TEXT DEFAULT ''"),
            ("today_sales", "REAL DEFAULT 0.0"),
            ("today_profit", "REAL DEFAULT 0.0"),
            ("today_invoices", "INTEGER DEFAULT 0"),
            ("cash_in_drawer", "REAL DEFAULT 0.0"),
            ("metrics_updated_at", "TEXT DEFAULT ''"),
            ("owner_pin", "TEXT DEFAULT '1234'"),
        ]:
            _ensure_column(conn, "devices", col, ctype)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_machine_id ON devices (machine_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS server_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                store_name TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                actor TEXT DEFAULT 'admin'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs (timestamp DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS revoked_devices (
                machine_id TEXT PRIMARY KEY,
                revoked_at TEXT NOT NULL,
                reason TEXT DEFAULT 'deleted_by_admin'
            )
            """
        )

        try:
            cur = conn.execute("SELECT count(*) FROM devices")
            if cur.fetchone()[0] == 0:
                _restore_db_from_backup_if_empty(db_path)
        except Exception:
            pass


def is_device_revoked(machine_id: str, db_path: Optional[Path] = None) -> bool:
    """Check if the machine ID has been explicitly deleted or revoked by the admin."""
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    row = conn.execute("SELECT 1 FROM revoked_devices WHERE machine_id = ?", (mid,)).fetchone()
    return bool(row)


def unrevoke_device(machine_id: str, db_path: Optional[Path] = None) -> None:
    """Remove device from revocation blacklist (when admin re-adds or re-activates it)."""
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    with conn:
        conn.execute("DELETE FROM revoked_devices WHERE machine_id = ?", (mid,))


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


def add_audit_log(
    machine_id: str,
    store_name: str,
    action: str,
    details: str,
    actor: str = "admin",
    db_path: Optional[Path] = None,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO audit_logs (timestamp, machine_id, store_name, action, details, actor)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now_iso, machine_id, store_name, action, details, actor),
        )


def list_audit_logs(
    limit: int = 150,
    search: str = "",
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    if search:
        s = f"%{search.strip().lower()}%"
        cur.execute(
            """
            SELECT * FROM audit_logs
            WHERE LOWER(store_name) LIKE ? OR LOWER(machine_id) LIKE ? OR LOWER(action) LIKE ? OR LOWER(details) LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (s, s, s, s, limit),
        )
    else:
        cur.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    now = datetime.now(timezone.utc)
    try:
        exp_dt = datetime.fromisoformat(d["expires_at"])
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        diff = exp_dt - now
        total_sec = diff.total_seconds()
        if total_sec <= 0:
            d["days_remaining"] = 0
            d["is_expired"] = True
        else:
            import math
            d["days_remaining"] = max(1, math.ceil(total_sec / 86400))
            d["is_expired"] = False
    except Exception:
        d["days_remaining"] = 0
        d["is_expired"] = True

    d["is_trial"] = (d.get("status") == "TRIAL" or d.get("plan_code") == "trial")

    if d["is_trial"]:
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 14.0) * 100)))
    elif d.get("plan_code") == "monthly":
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 30.0) * 100)))
    else:
        d["percent_remaining"] = min(100, max(0, int((d["days_remaining"] / 365.0) * 100)))

    # Fallback default PIN
    if not d.get("owner_pin"):
        d["owner_pin"] = "1234"

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

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter.upper())

    if search:
        s = f"%{search.strip().lower()}%"
        query += " AND (LOWER(store_name) LIKE ? OR LOWER(machine_id) LIKE ? OR LOWER(phone) LIKE ? OR LOWER(branch_name) LIKE ? OR LOWER(company_name) LIKE ?)"
        params.extend([s, s, s, s, s])

    query += " ORDER BY id DESC"
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def register_or_create_device(
    machine_id: str,
    store_name: str,
    phone: str = "",
    status: str = "ACTIVE",
    months: int = 12,
    days: Optional[int] = None,
    exact_expiry: Optional[str] = None,
    tier: str = "pro",
    grace_days: int = 5,
    notes: str = "",
    branch_name: str = "",
    company_name: str = "",
    current_token: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    unrevoke_device(mid, db_path=db_path)
    now = datetime.now(timezone.utc)
    conn = get_connection(db_path)
    existing = get_device(mid, db_path=db_path)

    if exact_expiry:
        try:
            exp_dt = datetime.fromisoformat(exact_expiry.strip())
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt.hour == 0 and exp_dt.minute == 0 and exp_dt.second == 0:
                exp_dt = exp_dt.replace(hour=23, minute=59, second=59)
            if status in ("TRIAL", "TRIAL_EXPIRED") or tier == "trial":
                plan_code = "trial"
                plan_ar = "نسخة تجريبية مجانية (14 يوماً)"
                actual_status = status if status in ("TRIAL", "TRIAL_EXPIRED") else "TRIAL"
                actual_tier = "trial"
                actual_grace = 0
            else:
                plan_code = "custom_date"
                plan_ar = f"اشتراك محدد التاريخ ({exp_dt.strftime('%Y-%m-%d')})"
                actual_status = "ACTIVE"
                actual_tier = tier
                actual_grace = grace_days
        except Exception:
            exp_dt = now + timedelta(days=365)
            plan_code = "12_months"
            plan_ar = "اشتراك سنوي (12 شهراً - Pro)"
            actual_status = "ACTIVE"
            actual_tier = tier
    elif status == "TRIAL" or months == 0:
        trial_days = days if (days is not None and days > 0) else 14
        exp_dt = now + timedelta(days=trial_days)
        plan_code = "trial"
        plan_ar = f"نسخة تجريبية مجانية ({trial_days} يوماً)"
        actual_status = "TRIAL"
        actual_tier = "trial"
        actual_grace = 0
    elif days is not None and days > 0:
        exp_dt = now + timedelta(days=days)
        plan_code = f"{days}_days"
        plan_ar = f"اشتراك مخصص ({days} يوماً)"
        actual_status = "ACTIVE"
        actual_tier = tier
        actual_grace = grace_days
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

    if current_token and str(current_token).strip():
        token = str(current_token).strip()
    else:
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
                tier, expires_at, grace_days, current_token, created_at, notes,
                branch_name, company_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE devices.notes END,
                branch_name = CASE WHEN excluded.branch_name != '' THEN excluded.branch_name ELSE devices.branch_name END,
                company_name = CASE WHEN excluded.company_name != '' THEN excluded.company_name ELSE devices.company_name END
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
                branch_name.strip(),
                company_name.strip(),
            ),
        )

    saved = get_device(mid, db_path=db_path) or {}

    # Audit & Notification
    action_type = "UPDATE_REGISTRATION" if existing else "NEW_REGISTRATION"
    add_audit_log(
        machine_id=mid,
        store_name=store_name,
        action=action_type,
        details=f"تسجيل الجهاز بالخطة: {plan_ar} حتى {exp_dt.strftime('%Y-%m-%d')}",
        actor="admin",
        db_path=db_path,
    )
    if not existing:
        notify_new_store_onboarded(saved, db_path=db_path)

    _save_db_backup(db_path=db_path)
    return saved


def update_device(
    machine_id: str,
    store_name: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    branch_name: Optional[str] = None,
    company_name: Optional[str] = None,
    owner_pin: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    conn = get_connection(db_path)
    fields = []
    values = []
    if store_name is not None and store_name.strip():
        fields.append("store_name = ?")
        values.append(store_name.strip())
    if phone is not None:
        fields.append("phone = ?")
        values.append(phone.strip())
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes.strip())
    if status is not None and status.strip():
        fields.append("status = ?")
        values.append(status.strip().upper())
    if branch_name is not None:
        fields.append("branch_name = ?")
        values.append(branch_name.strip())
    if company_name is not None:
        fields.append("company_name = ?")
        values.append(company_name.strip())
    if owner_pin is not None and owner_pin.strip():
        fields.append("owner_pin = ?")
        values.append(owner_pin.strip())

    if fields:
        values.append(mid)
        sql = f"UPDATE devices SET {', '.join(fields)} WHERE machine_id = ?"
        with conn:
            conn.execute(sql, tuple(values))

    updated = get_device(mid, db_path=db_path) or {}
    add_audit_log(
        machine_id=mid,
        store_name=updated.get("store_name", ""),
        action="UPDATE_INFO",
        details="تحديث معلومات المتجر/الفروع",
        actor="admin",
        db_path=db_path,
    )
    _save_db_backup(db_path=db_path)
    return updated


def update_store_metrics(
    machine_id: str,
    sales: Optional[float] = None,
    profit: Optional[float] = None,
    invoices: Optional[int] = None,
    cash_drawer: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    mid = machine_id.strip().upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    updates = ["metrics_updated_at = ?"]
    params: list[Any] = [now_iso]

    if sales is not None:
        updates.append("today_sales = ?")
        params.append(round(float(sales), 2))
    if profit is not None:
        updates.append("today_profit = ?")
        params.append(round(float(profit), 2))
    if invoices is not None:
        updates.append("today_invoices = ?")
        params.append(int(invoices))
    if cash_drawer is not None:
        updates.append("cash_in_drawer = ?")
        params.append(round(float(cash_drawer), 2))

    params.append(mid)
    sql = f"UPDATE devices SET {', '.join(updates)} WHERE machine_id = ?"
    with conn:
        cur = conn.execute(sql, tuple(params))
        return cur.rowcount > 0


def set_owner_pin(machine_id: str, pin: str, db_path: Optional[Path] = None) -> bool:
    mid = machine_id.strip().upper()
    clean_pin = pin.strip()
    conn = get_connection(db_path)
    with conn:
        cur = conn.execute("UPDATE devices SET owner_pin = ? WHERE machine_id = ?", (clean_pin, mid))
        return cur.rowcount > 0


def verify_owner_pin(machine_id: str, pin: str, db_path: Optional[Path] = None) -> bool:
    dev = get_device(machine_id, db_path=db_path)
    if not dev:
        return False
    expected = (dev.get("owner_pin") or "1234").strip()
    return pin.strip() == expected


def renew_device(
    machine_id: str,
    months: Optional[int] = None,
    days: Optional[int] = None,
    exact_expiry: Optional[str] = None,
    tier: Optional[str] = None,
    grace_days: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل: {mid}")

    now = datetime.now(timezone.utc)
    base_date = now

    if not device["is_expired"] and not device["is_trial"]:
        try:
            curr_exp = datetime.fromisoformat(device["expires_at"])
            if curr_exp.tzinfo is None:
                curr_exp = curr_exp.replace(tzinfo=timezone.utc)
            if curr_exp > now:
                base_date = curr_exp
        except Exception:
            base_date = now

    details_text = ""
    if exact_expiry:
        try:
            new_exp = datetime.fromisoformat(exact_expiry.strip())
            if new_exp.tzinfo is None:
                new_exp = new_exp.replace(tzinfo=timezone.utc)
            if new_exp.hour == 0 and new_exp.minute == 0 and new_exp.second == 0:
                new_exp = new_exp.replace(hour=23, minute=59, second=59)
            plan_code = "custom_date"
            plan_ar = f"اشتراك محدد التاريخ ({new_exp.strftime('%Y-%m-%d')})"
            details_text = f"تحديد تاريخ انتهاء بالتقويم حتى {new_exp.strftime('%Y-%m-%d')}"
        except Exception as ex:
            raise ValueError(f"تنسيق التاريخ غير صحيح (YYYY-MM-DD): {ex}")
    elif days is not None and days > 0:
        new_exp = base_date + timedelta(days=days)
        plan_code = f"{days}_days"
        plan_ar = f"اشتراك مخصص ({days} يوماً)"
        details_text = f"تجديد يدوي بإضافة {days} يوماً"
    else:
        m = months if months is not None else 12
        if m >= 120:
            new_exp = now + timedelta(days=36500)
            plan_code = "lifetime"
            plan_ar = "ترخيص مدى الحياة (Lifetime)"
            details_text = "ترقية لترخيص مدى الحياة"
        elif m == 1:
            new_exp = base_date + timedelta(days=31)
            plan_code = "monthly"
            plan_ar = "اشتراك شهري (1 شهر)"
            details_text = "تجديد لمدة شهر واحد"
        elif m == 3:
            new_exp = base_date + timedelta(days=93)
            plan_code = "quarterly"
            plan_ar = "اشتراك 3 أشهر"
            details_text = "تجديد لمدة 3 أشهر"
        elif m == 6:
            new_exp = base_date + timedelta(days=186)
            plan_code = "semi_annual"
            plan_ar = "اشتراك 6 أشهر"
            details_text = "تجديد لمدة 6 أشهر"
        else:
            new_exp = base_date + timedelta(days=int(m * 30.5))
            plan_code = f"{m}_months"
            plan_ar = f"اشتراك سنوي ({m} شهراً - Pro)"
            details_text = f"تجديد اشتراك سنوي ({m} شهراً)"

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

    updated = get_device(mid, db_path=db_path) or {}
    add_audit_log(
        machine_id=mid,
        store_name=updated.get("store_name", ""),
        action="RENEWAL",
        details=f"{details_text} حتى {new_exp.strftime('%Y-%m-%d')}",
        actor="admin",
        db_path=db_path,
    )
    notify_subscription_renewed(updated, details_text, db_path=db_path)
    _save_db_backup(db_path=db_path)
    return updated


def extend_trial(machine_id: str, days: int = 7, db_path: Optional[Path] = None) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل: {mid}")

    now = datetime.now(timezone.utc)
    base_date = now
    try:
        curr_exp = datetime.fromisoformat(device["expires_at"])
        if curr_exp.tzinfo is None:
            curr_exp = curr_exp.replace(tzinfo=timezone.utc)
        if curr_exp > now:
            base_date = curr_exp
    except Exception:
        base_date = now

    new_exp = base_date + timedelta(days=days)
    plan_ar = f"فترة تجريبية ممددة (+{days} أيام)"

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

    updated = get_device(mid, db_path=db_path) or {}
    add_audit_log(
        machine_id=mid,
        store_name=updated.get("store_name", ""),
        action="EXTEND_TRIAL",
        details=f"تمديد تجريبي لمدة {days} أيام إضافية حتى {new_exp.strftime('%Y-%m-%d')}",
        actor="admin",
        db_path=db_path,
    )
    notify_subscription_renewed(updated, f"تمديد تجريبي {days} أيام", db_path=db_path)
    _save_db_backup(db_path=db_path)
    return updated


def toggle_suspend(machine_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    mid = machine_id.strip().upper()
    device = get_device(mid, db_path=db_path)
    if not device:
        raise ValueError(f"الجهاز غير مسجل: {mid}")

    is_suspending = (device["status"] == "ACTIVE")
    new_status = "SUSPENDED" if is_suspending else "ACTIVE"
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            "UPDATE devices SET status = ? WHERE machine_id = ?",
            (new_status, mid),
        )

    updated = get_device(mid, db_path=db_path) or {}
    add_audit_log(
        machine_id=mid,
        store_name=updated.get("store_name", ""),
        action="SUSPEND" if is_suspending else "REACTIVATE",
        details="إيقاف المتجر إدارياً" if is_suspending else "إعادة تنشيط المتجر",
        actor="admin",
        db_path=db_path,
    )
    notify_status_changed(updated, is_suspending, db_path=db_path)
    _save_db_backup(db_path=db_path)
    return updated


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
    dev = get_device(mid, db_path=db_path)
    sname = dev.get("store_name", "") if dev else ""
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    with conn:
        cur = conn.execute("DELETE FROM devices WHERE machine_id = ?", (mid,))
        conn.execute(
            """
            INSERT INTO revoked_devices (machine_id, revoked_at, reason)
            VALUES (?, ?, 'deleted_by_admin')
            ON CONFLICT(machine_id) DO UPDATE SET revoked_at=excluded.revoked_at
            """,
            (mid, now_iso),
        )
        ok = cur.rowcount > 0
    if ok:
        add_audit_log(
            machine_id=mid,
            store_name=sname,
            action="DELETE",
            details="حذف الجهاز نهائياً وإضافته لقائمة التراخيص الملغاة",
            actor="admin",
            db_path=db_path,
        )
    _save_db_backup(db_path=db_path)
    return ok
