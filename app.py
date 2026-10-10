import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

try:
    from cloud_server.db import (
        add_audit_log,
        delete_device,
        extend_trial,
        get_device,
        get_setting,
        init_db,
        is_device_revoked,
        list_audit_logs,
        list_devices,
        record_sync,
        register_or_create_device,
        renew_device,
        set_owner_pin,
        set_setting,
        toggle_suspend,
        update_device,
        update_store_metrics,
        verify_owner_pin,
    )
    from cloud_server.notifier import (
        get_telegram_config,
        send_telegram_message_sync,
        send_telegram_notification,
    )
except ImportError:
    from db import (
        add_audit_log,
        delete_device,
        extend_trial,
        get_device,
        get_setting,
        init_db,
        is_device_revoked,
        list_audit_logs,
        list_devices,
        record_sync,
        register_or_create_device,
        renew_device,
        set_owner_pin,
        set_setting,
        toggle_suspend,
        update_device,
        update_store_metrics,
        verify_owner_pin,
    )
    from notifier import (
        get_telegram_config,
        send_telegram_message_sync,
        send_telegram_notification,
    )

# Initialize database tables on module load
init_db()

app = FastAPI(
    title="Max Pro POS - Central Cloud Licensing Server",
    description="Enterprise Ed25519 cloud licensing, Telegram alerts, and Store Owner Mobile Portal.",
    version="2.5.0",
)

# Enable CORS for web dashboards and clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Admin Authentication Security ──────────────────────────────────────────
security = HTTPBasic(auto_error=False)


def get_admin_credentials():
    username = get_setting("admin_username", os.environ.get("ADMIN_USERNAME", "admin"))
    password = get_setting("admin_password", os.environ.get("ADMIN_PASSWORD", "maxpro@2026"))
    api_key = os.environ.get("ADMIN_API_KEY", "")
    return username, password, api_key


def verify_admin(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Verify administrator identity for dashboard and management APIs."""
    if os.environ.get("ADMIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return "admin"

    expected_user, expected_pass, expected_key = get_admin_credentials()

    header_pass = request.headers.get("X-Admin-Password", "")
    if header_pass and secrets.compare_digest(header_pass.encode("utf-8"), expected_pass.encode("utf-8")):
        return expected_user

    req_key = request.headers.get("X-Admin-Key", "")
    if expected_key and req_key and secrets.compare_digest(req_key, expected_key):
        return expected_user

    if credentials:
        user_ok = secrets.compare_digest(
            credentials.username.encode("utf-8"), expected_user.encode("utf-8")
        )
        pass_ok = secrets.compare_digest(
            credentials.password.encode("utf-8"), expected_pass.encode("utf-8")
        )
        if user_ok and pass_ok:
            return credentials.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="اسم المستخدم أو كلمة المرور غير صحيحة.",
        headers={"WWW-Authenticate": 'Basic realm="Max Pro Licensing Admin"'},
    )


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
INDEX_HTML_PATH = TEMPLATES_DIR / "index.html"
PORTAL_HTML_PATH = TEMPLATES_DIR / "portal.html"


# ─── Pydantic Schemas ────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    machine_id: str = Field(..., description="Unique machine ID (MP-XXXX-...)")
    current_key: Optional[str] = Field("", description="Current installed MPLIC-... license token")
    store_name: Optional[str] = Field("", description="Store or shop name")
    branch_name: Optional[str] = Field("", description="Branch name")
    is_trial: Optional[bool] = Field(False, description="Whether client is currently in trial mode")
    days_remaining: Optional[int] = Field(0, description="Remaining trial/active days")
    client_version: Optional[str] = Field("1.0.0", description="POS app version")
    today_sales: Optional[float] = Field(None, description="Today's total sales")
    today_profit: Optional[float] = Field(None, description="Today's total net profit")
    today_invoices: Optional[int] = Field(None, description="Today's invoices count")
    cash_in_drawer: Optional[float] = Field(None, description="Current cash in drawer")


class RegisterRequest(BaseModel):
    machine_id: str
    store_name: str
    phone: Optional[str] = ""
    notes: Optional[str] = ""
    branch_name: Optional[str] = ""
    company_name: Optional[str] = ""


class AdminCreateRequest(BaseModel):
    machine_id: str
    store_name: str
    phone: Optional[str] = ""
    status: Optional[str] = "ACTIVE"
    months: Optional[int] = 12
    days: Optional[int] = None
    exact_expiry: Optional[str] = None
    tier: Optional[str] = "pro"
    notes: Optional[str] = ""
    branch_name: Optional[str] = ""
    company_name: Optional[str] = ""


class AdminRenewRequest(BaseModel):
    machine_id: str
    months: Optional[int] = 12
    days: Optional[int] = None
    exact_expiry: Optional[str] = None
    tier: Optional[str] = None
    grace_days: Optional[int] = None


class AdminUpdateRequest(BaseModel):
    machine_id: str
    store_name: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    branch_name: Optional[str] = None
    company_name: Optional[str] = None
    owner_pin: Optional[str] = None


class AdminExtendTrialRequest(BaseModel):
    machine_id: str
    days: int = 7


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ActionRequest(BaseModel):
    machine_id: str


class TelegramSettingsRequest(BaseModel):
    bot_token: Optional[str] = ""
    chat_id: Optional[str] = ""


class PortalChangePinRequest(BaseModel):
    machine_id: str
    old_pin: str
    new_pin: str


# ─── Public / Client Routes ──────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin")


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Max Pro Cloud Licensing Server"}


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    if not INDEX_HTML_PATH.exists():
        return HTMLResponse("<h3>Dashboard template not found.</h3>", status_code=404)
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content)


@app.get("/portal", response_class=HTMLResponse)
async def store_portal():
    if not PORTAL_HTML_PATH.exists():
        return HTMLResponse("<h3>Portal template not found.</h3>", status_code=404)
    content = PORTAL_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content)


@app.get("/api/v1/portal/data")
async def portal_get_data(machine_id: str, pin: str):
    """Secure endpoint for store owners to retrieve their live daily metrics."""
    mid = machine_id.strip().upper()
    dev = get_device(mid)
    if not dev:
        raise HTTPException(status_code=404, detail="معرّف الجهاز غير مسجل بالسيرفر.")
    expected_pin = (dev.get("owner_pin") or "1234").strip()
    if pin.strip() != expected_pin:
        raise HTTPException(status_code=401, detail="رمز الدخول (PIN) غير صحيح.")
    return {
        "machine_id": dev.get("machine_id", ""),
        "store_name": dev.get("store_name", ""),
        "branch_name": dev.get("branch_name", ""),
        "company_name": dev.get("company_name", ""),
        "status": dev.get("status", ""),
        "plan_name_ar": dev.get("plan_name_ar", ""),
        "expires_at": dev.get("expires_at", ""),
        "days_remaining": dev.get("days_remaining", 0),
        "is_expired": dev.get("is_expired", False),
        "is_trial": dev.get("is_trial", False),
        "today_sales": dev.get("today_sales", 0.0),
        "today_profit": dev.get("today_profit", 0.0),
        "today_invoices": dev.get("today_invoices", 0),
        "cash_in_drawer": dev.get("cash_in_drawer", 0.0),
        "last_sync_at": dev.get("last_sync_at", ""),
        "metrics_updated_at": dev.get("metrics_updated_at", ""),
    }


@app.post("/api/v1/portal/change_pin")
async def portal_change_pin(req: PortalChangePinRequest):
    """Allows store owner to change their dashboard PIN."""
    mid = req.machine_id.strip().upper()
    dev = get_device(mid)
    if not dev:
        raise HTTPException(status_code=404, detail="الجهاز غير موجود.")
    expected_pin = (dev.get("owner_pin") or "1234").strip()
    if req.old_pin.strip() != expected_pin:
        raise HTTPException(status_code=401, detail="رمز PIN الحالي غير صحيح.")
    new_pin = req.new_pin.strip()
    if len(new_pin) < 4:
        raise HTTPException(status_code=400, detail="الرمز الجديد يجب ألا يقل عن 4 أرقام.")
    set_owner_pin(mid, new_pin)
    add_audit_log(
        machine_id=mid,
        store_name=dev.get("store_name", ""),
        action="CHANGE_PIN",
        details="تغيير رمز PIN لبوابة المالك",
        actor="store_owner",
    )
    return {"success": True, "message": "تم تغيير رمز PIN بنجاح."}


@app.post("/api/v1/admin/login")
async def admin_login(req: LoginRequest):
    expected_user, expected_pass, _ = get_admin_credentials()
    user_ok = secrets.compare_digest(req.username.strip().encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = secrets.compare_digest(req.password.strip().encode("utf-8"), expected_pass.encode("utf-8"))
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة.")
    return {"success": True, "username": expected_user}


@app.post("/api/v1/admin/change_password")
async def admin_change_password(req: ChangePasswordRequest, user: str = Depends(verify_admin)):
    expected_user, expected_pass, _ = get_admin_credentials()
    if not secrets.compare_digest(req.current_password.encode("utf-8"), expected_pass.encode("utf-8")):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة.")

    new_pass = req.new_password.strip()
    if len(new_pass) < 4:
        raise HTTPException(status_code=400, detail="كلمة المرور الجديدة يجب ألا تقل عن 4 خانات.")

    set_setting("admin_password", new_pass)
    add_audit_log(
        machine_id="SERVER",
        store_name="لوحة التحكم الإدارية",
        action="CHANGE_ADMIN_PASSWORD",
        details="تغيير كلمة مرور المشرف العام",
        actor=user,
    )
    return {"success": True, "message": "تم تغيير كلمة المرور بنجاح! استخدم الرمز الجديد في المرات القادمة."}


@app.post("/api/v1/license/sync")
async def client_sync(req: SyncRequest):
    """Called by desktop POS client (LicenseManager.sync_cloud).
    
    Verifies machine status, records metrics, and returns renewed tokens if available.
    """
    mid = req.machine_id.strip().upper()
    if not mid:
        raise HTTPException(status_code=400, detail="معرّف الجهاز (machine_id) مطلوب.")

    # 1. Check if device has been explicitly deleted or revoked by admin
    if is_device_revoked(mid):
        return {
            "has_update": False,
            "status": "REVOKED",
            "is_revoked": True,
            "explicit_revoke": True,
            "can_sell": False,
            "message": "تم إلغاء وحذف ترخيص هذا الجهاز نهائياً من قبل الإدارة. تم إيقاف النظام.",
        }

    device = get_device(mid)

    # 2. If device is not in database:
    if not device:
        # Check if client holds a cryptographically authentic, signed license key!
        token_payload = None
        if req.current_key:
            try:
                try:
                    from cloud_server.signer import verify_license_token
                except ImportError:
                    from signer import verify_license_token
                token_payload = verify_license_token(req.current_key)
            except Exception:
                try:
                    from services.licensing.crypto import verify_license_key
                    token_payload = verify_license_key(req.current_key)
                except Exception:
                    token_payload = None

        if token_payload and isinstance(token_payload, dict):
            licensed_mid = str(token_payload.get("mid") or "").strip().upper()
            if licensed_mid == mid:
                # ── SELF-HEALING / AUTO-RESTORATION ──
                # The client holds an authentic cryptographic token signed by our vendor key.
                # Auto-restore the device into the database so it reappears in the dashboard!
                exp_str = str(token_payload.get("exp") or "").strip()
                store_title = str(token_payload.get("store") or req.store_name or f"متجر ({mid[-4:]})").strip()
                plan_code = str(token_payload.get("plan") or "12_months")
                plan_ar = str(token_payload.get("plan_ar") or "اشتراك معتمد")
                tier_val = str(token_payload.get("tier") or "pro")
                grace_val = int(token_payload.get("grace") or 5)

                now_utc = datetime.now(timezone.utc)
                try:
                    exp_dt = datetime.fromisoformat(exp_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    is_expired = (exp_dt <= now_utc)
                except Exception:
                    is_expired = False

                if plan_code == "trial":
                    status_val = "TRIAL_EXPIRED" if is_expired else "TRIAL"
                else:
                    status_val = "EXPIRED" if is_expired else "ACTIVE"

                device = register_or_create_device(
                    machine_id=mid,
                    store_name=store_title,
                    phone="",
                    status=status_val,
                    exact_expiry=exp_str if exp_str else None,
                    tier=tier_val,
                    grace_days=grace_val,
                    notes="تمت استعادة الترخيص تلقائياً من التوقيع الرقمي المعتمد للعميل بعد إعادة تشغيل السيرفر",
                    branch_name=req.branch_name or "",
                    current_token=req.current_key,
                )

                add_audit_log(
                    machine_id=mid,
                    store_name=store_title,
                    action="AUTO_RESTORE",
                    details=f"استعادة تلقائية لترخيص [{store_title}] من التوقيع الرقمي للعميل",
                    actor="system_auto_heal",
                )

        if not device:
            # Completely fresh installation without any prior license: only create a trial
            store_title = req.store_name.strip() or f"متجر تجريبي ({mid[-4:]})"
            device = register_or_create_device(
                machine_id=mid,
                store_name=store_title,
                phone="",
                status="TRIAL",
                months=0,
                days=14,
                tier="trial",
                notes="عميل جديد بدأ النسخة التجريبية (مسجل تلقائياً)",
                branch_name=req.branch_name or "",
            )

    # 3. Check suspension
    if device.get("status") == "SUSPENDED":
        return {
            "has_update": False,
            "status": "SUSPENDED",
            "is_suspended": True,
            "can_sell": False,
            "message": "تم إيقاف هذا الترخيص إدارياً من قبل الإدارة. يرجى مراجعة إدارة النظام.",
        }

    # Record sync timestamp
    record_sync(mid, store_name=req.store_name or "")

    # Update daily sales metrics if sent
    if req.today_sales is not None or req.cash_in_drawer is not None or req.today_invoices is not None:
        update_store_metrics(
            machine_id=mid,
            sales=req.today_sales,
            profit=req.today_profit,
            invoices=req.today_invoices,
            cash_drawer=req.cash_in_drawer,
        )

    if req.branch_name and not device.get("branch_name"):
        update_device(mid, branch_name=req.branch_name)

    server_token = device.get("current_token", "")
    client_key = (req.current_key or "").strip()

    if not client_key or client_key != server_token:
        exp_date = device.get("expires_at", "")[:10]
        is_device_trial = (device.get("status") == "TRIAL")
        return {
            "has_update": True,
            "status": device.get("status"),
            "license_key": server_token,
            "expires_at": device.get("expires_at"),
            "plan_name_ar": device.get("plan_name_ar"),
            "message": (
                f"أنت تعمل بالنسخة التجريبية المجانية حتى {exp_date}."
                if is_device_trial
                else f"تم تحديث اشتراكك بنجاح حتى تاريخ {exp_date}!"
            ),
        }

    return {
        "has_update": False,
        "status": device.get("status", "ACTIVE"),
        "license_key": server_token,
        "expires_at": device.get("expires_at"),
        "plan_name_ar": device.get("plan_name_ar"),
        "message": "اشتراكك سارٍ ومحدث لأحدث إصدار.",
    }


@app.post("/api/v1/license/register")
async def client_register(req: RegisterRequest):
    """Device self-registration from client onboarding."""
    mid = req.machine_id.strip().upper()
    if not mid or not req.store_name:
        raise HTTPException(status_code=400, detail="بيانات الجهاز واسم المتجر مطلوبة.")

    if is_device_revoked(mid):
        raise HTTPException(status_code=403, detail="هذا الجهاز تم حذفه/إلغاؤه من قبل الإدارة المركزية.")

    device = register_or_create_device(
        machine_id=mid,
        store_name=req.store_name,
        phone=req.phone or "",
        months=1,
        tier="pro",
        notes=req.notes or "",
        branch_name=req.branch_name or "",
        company_name=req.company_name or "",
    )
    return {
        "success": True,
        "message": "تم تسجيل الجهاز بنجاح بالسيرفر المركزي.",
        "device": device,
    }


# ─── Admin API Routes (Protected) ────────────────────────────────────────────

@app.get("/api/v1/admin/devices")
async def admin_list_devices(search: str = "", user: str = Depends(verify_admin)):
    return list_devices(search=search)


@app.post("/api/v1/admin/devices")
async def admin_create_device(req: AdminCreateRequest, user: str = Depends(verify_admin)):
    mid = req.machine_id.strip().upper()
    device = register_or_create_device(
        machine_id=mid,
        store_name=req.store_name,
        phone=req.phone or "",
        status=req.status or "ACTIVE",
        months=req.months if req.months is not None else 12,
        days=req.days,
        exact_expiry=req.exact_expiry,
        tier=req.tier or "pro",
        notes=req.notes or "",
        branch_name=req.branch_name or "",
        company_name=req.company_name or "",
    )
    return device


@app.post("/api/v1/admin/devices/update")
async def admin_update_device(req: AdminUpdateRequest, user: str = Depends(verify_admin)):
    mid = req.machine_id.strip().upper()
    try:
        updated = update_device(
            machine_id=mid,
            store_name=req.store_name,
            phone=req.phone,
            notes=req.notes,
            status=req.status,
            branch_name=req.branch_name,
            company_name=req.company_name,
            owner_pin=req.owner_pin,
        )
        return updated
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء تعديل البيانات: {exc}")


@app.post("/api/v1/admin/renew")
async def admin_renew_device(req: AdminRenewRequest, user: str = Depends(verify_admin)):
    """Instant renewal by months, custom days, or exact expiry date."""
    mid = req.machine_id.strip().upper()
    try:
        updated = renew_device(
            machine_id=mid,
            months=req.months,
            days=req.days,
            exact_expiry=req.exact_expiry,
            tier=req.tier,
            grace_days=req.grace_days,
        )
        return updated
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء التجديد: {exc}")


@app.get("/api/v1/admin/audit_logs")
async def admin_get_audit_logs(limit: int = 150, search: str = "", user: str = Depends(verify_admin)):
    """Retrieve system audit trail records."""
    return list_audit_logs(limit=limit, search=search)


@app.get("/api/v1/admin/telegram/settings")
async def admin_get_telegram_settings(user: str = Depends(verify_admin)):
    """Retrieve Telegram bot integration state."""
    token = get_setting("telegram_bot_token", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = get_setting("telegram_chat_id", os.environ.get("TELEGRAM_CHAT_ID", ""))
    masked = (token[:6] + "..." + token[-4:]) if len(token) > 10 else ("مفعل" if token else "")
    return {
        "configured": bool(token and chat_id),
        "bot_token_masked": masked,
        "chat_id": chat_id,
    }


@app.post("/api/v1/admin/telegram/settings")
async def admin_save_telegram_settings(req: TelegramSettingsRequest, user: str = Depends(verify_admin)):
    """Save Telegram bot token and chat ID."""
    if req.bot_token and req.bot_token.strip():
        set_setting("telegram_bot_token", req.bot_token.strip())
    if req.chat_id and req.chat_id.strip():
        set_setting("telegram_chat_id", req.chat_id.strip())
    add_audit_log(
        machine_id="SERVER",
        store_name="إعدادات النظام",
        action="UPDATE_TELEGRAM_SETTINGS",
        details="تحديث إعدادات وتوكن بوت تيليغرام",
        actor=user,
    )
    return {"success": True, "message": "تم حفظ إعدادات بوت تيليغرام بنجاح."}


@app.post("/api/v1/admin/telegram/test")
async def admin_test_telegram(req: Optional[TelegramSettingsRequest] = None, user: str = Depends(verify_admin)):
    """Send test notification via Telegram bot."""
    token = req.bot_token.strip() if (req and req.bot_token) else None
    chat_id = req.chat_id.strip() if (req and req.chat_id) else None
    msg = (
        "🎉 <b>فحص اتصال بوت تيليغرام ناجح!</b>\n\n"
        "تم ربط البوت بلوحة تحكم ماكس برو السحابية بنجاح 🚀\n"
        "ستصلك الآن تنبيهات الاشتراكات والمشتركين الجدد فورياً هنا."
    )
    ok = send_telegram_message_sync(msg, token=token, chat_id=chat_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="فشل إرسال الرسالة إلى تيليغرام. تأكد من الـ Bot Token ومعرّف المحادثة Chat ID، وأنك أرسلت /start للبوت أولاً.",
        )
    return {"success": True, "message": "تم إرسال رسالة تجريبية إلى حسابك في تيليغرام بنجاح!"}


@app.post("/api/v1/admin/telegram/send_reminders")
async def admin_send_telegram_reminders(user: str = Depends(verify_admin)):
    """Manually trigger collection reminder notifications to Telegram for shops expiring within 3 days."""
    devices = list_devices()
    expiring = [
        d for d in devices
        if not d.get("is_expired") and d.get("days_remaining", 99) <= 3 and d.get("status") != "SUSPENDED"
    ]
    if not expiring:
        return {"count": 0, "message": "لا توجد اشتراكات تنتهي خلال الـ 3 أيام القادمة."}

    lines = ["⏰ <b>تنبيه الاشتراكات التي توشك على الانتهاء:</b>\n"]
    for d in expiring:
        lines.append(
            f"• 🏪 <b>{d['store_name']}</b>: متبقٍ {d['days_remaining']} يوم (بتاريخ {d['expires_at'][:10]}) - هاتف: {d.get('phone') or 'غير مسجل'}"
        )
    lines.append("\n💡 <i>يرجى التواصل معهم للتجديد والمتابعة.</i>")
    send_telegram_notification("\n".join(lines))
    return {"count": len(expiring), "message": f"تم إرسال تنبيه بـ {len(expiring)} متجر إلى تيليغرام."}


@app.get("/api/v1/admin/export/csv")
async def admin_export_csv(user: str = Depends(verify_admin)):
    """Export all stores and licenses as a downloadable CSV spreadsheet."""
    import csv
    import io
    from fastapi.responses import Response

    devices = list_devices()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "معرّف الجهاز (Machine ID)",
        "اسم المتجر",
        "الفرع",
        "المؤسسة / الشركة",
        "رقم الهاتف",
        "الحالة",
        "الخطة / الباقة",
        "تاريخ الانتهاء",
        "الأيام المتبقية",
        "مبيعات اليوم",
        "أرباح اليوم",
        "عدد الفواتير",
        "الكاش في الدرج",
        "آخر مزامنة",
        "تاريخ التسجيل",
        "ملاحظات",
        "كود الترخيص",
    ])
    for d in devices:
        writer.writerow([
            d.get("machine_id", ""),
            d.get("store_name", ""),
            d.get("branch_name", ""),
            d.get("company_name", ""),
            d.get("phone", ""),
            d.get("status", ""),
            d.get("plan_name_ar", ""),
            (d.get("expires_at", "") or "")[:10],
            d.get("days_remaining", 0),
            d.get("today_sales", 0),
            d.get("today_profit", 0),
            d.get("today_invoices", 0),
            d.get("cash_in_drawer", 0),
            (d.get("last_sync_at", "") or "")[:19].replace("T", " "),
            (d.get("created_at", "") or "")[:10],
            d.get("notes", ""),
            d.get("current_token", ""),
        ])
    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=maxpro_licenses_export.csv"},
    )


@app.post("/api/v1/admin/extend_trial")
async def admin_extend_trial(req: AdminExtendTrialRequest, user: str = Depends(verify_admin)):
    mid = req.machine_id.strip().upper()
    try:
        updated = extend_trial(machine_id=mid, days=req.days)
        return updated
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء تمديد التجربة: {exc}")


@app.post("/api/v1/admin/suspend")
async def admin_suspend_device(req: ActionRequest, user: str = Depends(verify_admin)):
    try:
        return toggle_suspend(req.machine_id)
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))


@app.post("/api/v1/admin/delete")
async def admin_delete_device(req: ActionRequest, user: str = Depends(verify_admin)):
    ok = delete_device(req.machine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="الجهاز غير موجود.")
    return {"success": True, "message": "تم حذف الجهاز بنجاح."}
