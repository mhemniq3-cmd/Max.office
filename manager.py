from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import SETTINGS_DIR
from services.licensing.crypto import verify_license_key
from services.licensing.hardware import get_machine_id
from services.licensing.time_guard import ClockGuard

LICENSE_FILE = SETTINGS_DIR / "license_state.json"
DEFAULT_TRIAL_DAYS = 14
DEFAULT_GRACE_DAYS = 3


class LicenseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRING_SOON = "EXPIRING_SOON"
    GRACE_PERIOD = "GRACE_PERIOD"
    TRIAL = "TRIAL"
    TRIAL_EXPIRED = "TRIAL_EXPIRED"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    MACHINE_MISMATCH = "MACHINE_MISMATCH"
    CLOCK_TAMPERED = "CLOCK_TAMPERED"
    INVALID = "INVALID"


@dataclass
class LicenseInfo:
    status: LicenseStatus
    status_text_ar: str
    store_name: str
    plan_name_ar: str
    machine_id: str
    expires_at: Optional[datetime]
    days_remaining: int
    is_valid: bool
    can_sell: bool
    is_read_only: bool
    tier: str
    license_key: str
    features: list[str]


class LicenseManager:
    """Enterprise Hybrid Subscription & License Manager for Max Pro."""

    def __init__(self, state_file: Path = LICENSE_FILE):
        self.path = Path(state_file)
        self.clock_guard = ClockGuard()
        self.machine_id = get_machine_id()
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

        # First run on this machine: initialize trial
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(days=DEFAULT_TRIAL_DAYS)
        initial = {
            "version": 1,
            "installed_at": now.isoformat(),
            "trial_expires_at": trial_end.isoformat(),
            "license_key": "",
            "verified_payload": None,
        }
        self._save_state(initial)
        return initial

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def update_store_info(self, store_name: str, phone: str = "") -> None:
        """Update store name and contact phone in local license metadata."""
        try:
            state = self._load_state()
            state["store_name"] = str(store_name or "").strip()
            state["store_phone"] = str(phone or "").strip()
            if state.get("verified_payload") and isinstance(state["verified_payload"], dict):
                state["verified_payload"]["store"] = state["store_name"]
            self._save_state(state)
        except Exception:
            pass

    def activate_license(self, license_key_str: str) -> Tuple[bool, str]:
        """Validate, verify signature, match hardware, and install a license key."""
        clean_key = str(license_key_str or "").strip()
        if not clean_key:
            return False, "يرجى إدخال كود الترخيص"

        try:
            payload = verify_license_key(clean_key)
        except ValueError as exc:
            return False, str(exc)

        # 1. Verify Machine ID binding
        licensed_mid = str(payload.get("mid") or payload.get("machine_id") or "").strip().upper()
        current_mid = self.machine_id.upper()
        if licensed_mid and licensed_mid != current_mid:
            return False, f"هذا الترخيص مخصص لجهاز آخر ({licensed_mid})، بينما معرّف هذا الجهاز هو ({current_mid})."

        # 2. Verify expiry date format
        exp_str = str(payload.get("exp") or payload.get("expires_at") or "").strip()
        if not exp_str:
            return False, "الترخيص لا يحتوي على تاريخ انتهاء صالح"

        try:
            exp_dt = datetime.fromisoformat(exp_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return False, "تنسيق تاريخ انتهاء الترخيص غير صالح"

        # 3. Check if already expired at activation time
        now = datetime.now(timezone.utc)
        if exp_dt <= now:
            return False, f"كود الترخيص منتهي الصلاحية بتاريخ ({exp_str})."

        # Save active license
        self.state["license_key"] = clean_key
        self.state["verified_payload"] = payload
        self.state["activated_at"] = now.isoformat()
        self.state["is_revoked"] = False
        self.state["is_suspended"] = False
        self.state.pop("status", None)
        self._save_state(self.state)

        store_name = payload.get("store") or payload.get("store_name") or "المتجر"
        plan_name = payload.get("plan_ar") or payload.get("plan_name_ar") or "اشتراك معتمد"
        return True, f"تم تفعيل ترخيص [{store_name}] بنجاح! الباقة: {plan_name}"

    def get_info(self) -> LicenseInfo:
        """Inspect and return current subscription state."""
        # 1. Clock tamper check
        clock_ok, clock_msg = self.clock_guard.check_and_update()
        if not clock_ok or self.clock_guard.is_flagged_tampered():
            return LicenseInfo(
                status=LicenseStatus.CLOCK_TAMPERED,
                status_text_ar="تم رصد تلاعب بساعة الجهاز - النظام متوقف لحين تصحيح الوقت",
                store_name="غير محدد",
                plan_name_ar="معطل أمنياً",
                machine_id=self.machine_id,
                expires_at=None,
                days_remaining=0,
                is_valid=False,
                can_sell=False,
                is_read_only=True,
                tier="locked",
                license_key="",
                features=[],
            )

        # Check remote revocation / deletion
        if self.state.get("is_revoked") or self.state.get("status") == "REVOKED":
            return LicenseInfo(
                status=LicenseStatus.REVOKED,
                status_text_ar="تم إلغاء وحذف ترخيص هذا الجهاز نهائياً من قبل الإدارة (تم إيقاف النظام)",
                store_name=str(self.state.get("store_name") or "ملغى"),
                plan_name_ar="ترخيص ملغى",
                machine_id=self.machine_id,
                expires_at=None,
                days_remaining=0,
                is_valid=False,
                can_sell=False,
                is_read_only=True,
                tier="revoked",
                license_key="",
                features=[],
            )

        # Check remote suspension
        if self.state.get("is_suspended") or self.state.get("status") == "SUSPENDED":
            return LicenseInfo(
                status=LicenseStatus.SUSPENDED,
                status_text_ar="تم إيقاف الترخيص إدارياً ومؤقتاً من قبل الإدارة (البيع معلق)",
                store_name=str(self.state.get("store_name") or "موقوف"),
                plan_name_ar="ترخيص موقوف",
                machine_id=self.machine_id,
                expires_at=None,
                days_remaining=0,
                is_valid=False,
                can_sell=False,
                is_read_only=True,
                tier="suspended",
                license_key=str(self.state.get("license_key") or ""),
                features=[],
            )

        now = datetime.now(timezone.utc)
        payload = self.state.get("verified_payload")
        active_key = str(self.state.get("license_key") or "")

        # 2. Check full license if present
        if payload and isinstance(payload, dict) and active_key:
            # Re-verify signature on the fly to guard against file tampering
            try:
                verified = verify_license_key(active_key)
            except Exception:
                return LicenseInfo(
                    status=LicenseStatus.INVALID,
                    status_text_ar="ملف الترخيص تالف أو تم تعديله دون تصريح",
                    store_name="تالف",
                    plan_name_ar="غير صالح",
                    machine_id=self.machine_id,
                    expires_at=None,
                    days_remaining=0,
                    is_valid=False,
                    can_sell=False,
                    is_read_only=True,
                    tier="none",
                    license_key=active_key,
                    features=[],
                )

            # Check machine binding
            licensed_mid = str(verified.get("mid") or verified.get("machine_id") or "").strip().upper()
            if licensed_mid and licensed_mid != self.machine_id.upper():
                return LicenseInfo(
                    status=LicenseStatus.MACHINE_MISMATCH,
                    status_text_ar="كود الترخيص غير مخصص لهذا الجهاز",
                    store_name=str(verified.get("store") or verified.get("store_name") or ""),
                    plan_name_ar="جهاز غير مطابق",
                    machine_id=self.machine_id,
                    expires_at=None,
                    days_remaining=0,
                    is_valid=False,
                    can_sell=False,
                    is_read_only=True,
                    tier="none",
                    license_key=active_key,
                    features=[],
                )

            exp_str = str(verified.get("exp") or verified.get("expires_at") or "")
            try:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            except Exception:
                exp_dt = now

            diff = exp_dt - now
            days_left = max(0, int(diff.total_seconds() // 86400))
            grace_days = int(verified.get("grace", DEFAULT_GRACE_DAYS))
            store_name = str(verified.get("store") or verified.get("store_name") or "ماكس برو")
            plan_ar = str(verified.get("plan_ar") or verified.get("plan_name_ar") or "اشتراك معتمد")
            tier = str(verified.get("tier", "pro"))
            features = list(verified.get("features", ["all"]))

            if exp_dt > now:
                if days_left <= 7:
                    return LicenseInfo(
                        status=LicenseStatus.EXPIRING_SOON,
                        status_text_ar=f"أوشك الاشتراك على الانتهاء ({days_left} يوم متبقي)",
                        store_name=store_name,
                        plan_name_ar=plan_ar,
                        machine_id=self.machine_id,
                        expires_at=exp_dt,
                        days_remaining=days_left,
                        is_valid=True,
                        can_sell=True,
                        is_read_only=False,
                        tier=tier,
                        license_key=active_key,
                        features=features,
                    )
                return LicenseInfo(
                    status=LicenseStatus.ACTIVE,
                    status_text_ar=f"اشتراك سارٍ ({days_left} يوم متبقي)",
                    store_name=store_name,
                    plan_name_ar=plan_ar,
                    machine_id=self.machine_id,
                    expires_at=exp_dt,
                    days_remaining=days_left,
                    is_valid=True,
                    can_sell=True,
                    is_read_only=False,
                    tier=tier,
                    license_key=active_key,
                    features=features,
                )

            # Expired: check grace period
            overdue_days = int((now - exp_dt).total_seconds() // 86400)
            if overdue_days <= grace_days:
                return LicenseInfo(
                    status=LicenseStatus.GRACE_PERIOD,
                    status_text_ar=f"فترة سماح مؤقتة (انتهى الاشتراك منذ {overdue_days} يوم - يرجى التجديد)",
                    store_name=store_name,
                    plan_name_ar=plan_ar,
                    machine_id=self.machine_id,
                    expires_at=exp_dt,
                    days_remaining=0,
                    is_valid=True,
                    can_sell=True,
                    is_read_only=False,
                    tier=tier,
                    license_key=active_key,
                    features=features,
                )

            return LicenseInfo(
                status=LicenseStatus.EXPIRED,
                status_text_ar=f"انتهى الاشتراك رسمياً بتاريخ {exp_dt.strftime('%Y-%m-%d')} (البيع معلق)",
                store_name=store_name,
                plan_name_ar=plan_ar,
                machine_id=self.machine_id,
                expires_at=exp_dt,
                days_remaining=0,
                is_valid=False,
                can_sell=False,
                is_read_only=True,
                tier=tier,
                license_key=active_key,
                features=features,
            )

        # 3. No full license -> Fallback to Trial period
        trial_str = str(self.state.get("trial_expires_at") or "")
        try:
            trial_dt = datetime.fromisoformat(trial_str)
            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(tzinfo=timezone.utc)
        except Exception:
            trial_dt = now

        if trial_dt > now:
            days_left = max(1, int((trial_dt - now).total_seconds() // 86400))
            return LicenseInfo(
                status=LicenseStatus.TRIAL,
                status_text_ar=f"نسخة تجريبية مجانية ({days_left} يوم متبقٍ)",
                store_name="نسخة تجريبية",
                plan_name_ar=f"فترة تجريبية مجانية ({DEFAULT_TRIAL_DAYS} يوماً)",
                machine_id=self.machine_id,
                expires_at=trial_dt,
                days_remaining=days_left,
                is_valid=True,
                can_sell=True,
                is_read_only=False,
                tier="trial",
                license_key="",
                features=["all"],
            )

        return LicenseInfo(
            status=LicenseStatus.TRIAL_EXPIRED,
            status_text_ar="انتهت الفترة التجريبية المجانية - يرجى تفعيل اشتراك لمتابعة البيع",
            store_name="نسخة تجريبية",
            plan_name_ar="فترة تجريبية منتهية",
            machine_id=self.machine_id,
            expires_at=trial_dt,
            days_remaining=0,
            is_valid=False,
            can_sell=False,
            is_read_only=True,
            tier="trial_expired",
            license_key="",
            features=[],
        )

    def can_sell(self) -> Tuple[bool, str]:
        """Check whether checkout/sales operations are permitted."""
        info = self.get_info()
        if info.can_sell:
            return True, ""
        return False, info.status_text_ar

    def sync_cloud(self, server_url: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """Query central cloud license server and auto-activate remote renewals if available.
        
        Returns:
            (success: bool, message_ar: str, response_data: dict)
        """
        from services.licensing.cloud_client import CloudLicenseClient

        info = self.get_info()
        client = CloudLicenseClient(server_url=server_url)
        current_key = str(self.state.get("license_key") or "")
        is_trial = (info.status in (LicenseStatus.TRIAL, LicenseStatus.TRIAL_EXPIRED))

        store_name = info.store_name if (info.store_name and info.store_name != "نسخة تجريبية") else ""
        if not store_name:
            try:
                from config import SETTINGS_DIR
                comp_file = SETTINGS_DIR / "company_settings.json"
                if comp_file.exists():
                    comp_data = json.loads(comp_file.read_text(encoding="utf-8"))
                    store_name = comp_data.get("name") or comp_data.get("company_name") or ""
            except Exception:
                pass

        branch_name = ""
        today_sales = None
        today_profit = None
        today_invoices = None
        cash_in_drawer = None

        try:
            from config import DB_PATH, SETTINGS_DIR
            comp_file = SETTINGS_DIR / "company_settings.json"
            if comp_file.exists():
                comp_data = json.loads(comp_file.read_text(encoding="utf-8"))
                branch_name = comp_data.get("branch") or comp_data.get("branch_name") or ""

            if DB_PATH.exists():
                import sqlite3
                with sqlite3.connect(str(DB_PATH), timeout=2) as local_conn:
                    local_conn.row_factory = sqlite3.Row
                    cur = local_conn.cursor()
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    sales_row = cur.execute(
                        """
                        SELECT COUNT(*) AS inv_count,
                               COALESCE(SUM(total), 0) AS s_sum,
                               COALESCE(SUM(total_profit), 0) AS p_sum
                        FROM sales
                        WHERE date(created_at) = date(?) AND COALESCE(status, 'completed') = 'completed'
                        """,
                        (today_str,),
                    ).fetchone()
                    if sales_row:
                        today_invoices = int(sales_row["inv_count"] or 0)
                        today_sales = float(sales_row["s_sum"] or 0.0)
                        today_profit = float(sales_row["p_sum"] or 0.0)

                    try:
                        shift_row = cur.execute(
                            "SELECT counted_cash, opening_cash FROM cashier_shifts WHERE status='open' ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        if shift_row:
                            cash_in_drawer = float(shift_row["counted_cash"] if shift_row["counted_cash"] is not None else (shift_row["opening_cash"] or 0.0))
                    except Exception:
                        pass
        except Exception:
            pass

        ok, msg, data = client.sync(
            machine_id=self.machine_id,
            current_license_key=current_key,
            store_name=store_name,
            is_trial=is_trial,
            days_remaining=info.days_remaining,
            branch_name=branch_name,
            today_sales=today_sales,
            today_profit=today_profit,
            today_invoices=today_invoices,
            cash_in_drawer=cash_in_drawer,
        )
        if not ok:
            return False, msg, data

        server_status = str(data.get("status") or "").upper()
        is_revoked = bool(data.get("is_revoked")) or server_status == "REVOKED"
        is_suspended = bool(data.get("is_suspended")) or server_status == "SUSPENDED"

        if is_revoked:
            self.state["is_revoked"] = True
            self.state["is_suspended"] = False
            self.state["status"] = "REVOKED"
            self._save_state(self.state)
            return False, "⚠️ تم إلغاء اشتراكك وحذفه من قبل إدارة التراخيص. تم إيقاف النظام.", data

        if is_suspended:
            self.state["is_suspended"] = True
            self.state["is_revoked"] = False
            self.state["status"] = "SUSPENDED"
            self._save_state(self.state)
            return False, "⏸️ تم إيقاف هذا الترخيص إدارياً ومؤقتاً من قبل الإدارة المركزية. عمليات البيع معلقة.", data

        # If server confirmed active/trial and not suspended, clear any suspension/revocation flags
        if server_status in ("ACTIVE", "TRIAL"):
            if self.state.get("is_suspended") or self.state.get("is_revoked"):
                self.state["is_suspended"] = False
                self.state["is_revoked"] = False
                self.state.pop("status", None)
                self._save_state(self.state)

        # If server returned NOT_FOUND (unindexed or temporary), keep valid local license active
        if server_status == "NOT_FOUND":
            return True, "اشتراكك المحلي سارٍ وموثق برمجياً.", data

        has_update = bool(data.get("has_update"))
        new_key = str(data.get("license_key") or "").strip()

        if has_update and new_key:
            act_ok, act_msg = self.activate_license(new_key)
            if act_ok:
                return True, f"🎉 تم تحديث وتجديد الترخيص سحابياً بنجاح!\n{act_msg}", data
            return False, f"تم استلام ترخيص سحابي جديد لكن تعذر تثبيته: {act_msg}", data

        return True, msg or "اشتراكك محدث وسارٍ، ولا توجد تجديدات جديدة معلقة على السيرفر.", data

