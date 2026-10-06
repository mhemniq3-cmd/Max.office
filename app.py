import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

try:
    from cloud_server.db import (
        delete_device,
        extend_trial,
        get_device,
        init_db,
        list_devices,
        record_sync,
        register_or_create_device,
        renew_device,
        toggle_suspend,
    )
except ImportError:
    from db import (
        delete_device,
        extend_trial,
        get_device,
        init_db,
        list_devices,
        record_sync,
        register_or_create_device,
        renew_device,
        toggle_suspend,
    )

# Initialize database tables on module load
init_db()

app = FastAPI(
    title="Max Pro POS - Central Cloud Licensing Server",
    description="Enterprise Ed25519 cloud licensing and 1-click instant subscription renewals.",
    version="1.0.0",
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
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "maxpro@2026")
    api_key = os.environ.get("ADMIN_API_KEY", "")
    return username, password, api_key


def verify_admin(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Verify administrator identity for dashboard and management APIs."""
    # Allow testing or development bypass if explicitly configured
    if os.environ.get("ADMIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return "admin"

    expected_user, expected_pass, expected_key = get_admin_credentials()

    # Support X-Admin-Key header (for automated scripts or webhooks)
    req_key = request.headers.get("X-Admin-Key", "")
    if expected_key and req_key and secrets.compare_digest(req_key, expected_key):
        return "admin"

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


# ─── Pydantic Schemas ────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    machine_id: str = Field(..., description="Unique machine ID (MP-XXXX-...)")
    current_key: Optional[str] = Field("", description="Current installed MPLIC-... license token")
    store_name: Optional[str] = Field("", description="Store or shop name")
    is_trial: Optional[bool] = Field(False, description="Whether client is currently in trial mode")
    days_remaining: Optional[int] = Field(0, description="Remaining trial/active days")
    client_version: Optional[str] = Field("1.0.0", description="POS app version")


class RegisterRequest(BaseModel):
    machine_id: str
    store_name: str
    phone: Optional[str] = ""
    notes: Optional[str] = ""


class AdminCreateRequest(BaseModel):
    machine_id: str
    store_name: str
    phone: Optional[str] = ""
    status: Optional[str] = "ACTIVE"
    months: int = 12
    tier: Optional[str] = "pro"
    notes: Optional[str] = ""


class AdminRenewRequest(BaseModel):
    machine_id: str
    months: int = 12
    tier: Optional[str] = None
    grace_days: Optional[int] = None


class AdminExtendTrialRequest(BaseModel):
    machine_id: str
    days: int = 7


class ActionRequest(BaseModel):
    machine_id: str


# ─── Public / Desktop Client Routes ──────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin")


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Max Pro Cloud Licensing Server"}


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(user: str = Depends(verify_admin)):
    if not INDEX_HTML_PATH.exists():
        return HTMLResponse("<h3>Dashboard template not found.</h3>", status_code=404)
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content)


@app.post("/api/v1/license/sync")
async def client_sync(req: SyncRequest):
    """Called by desktop POS client (LicenseManager.sync_cloud).
    
    Verifies machine status and returns renewed Ed25519 tokens if available.
    """
    mid = req.machine_id.strip().upper()
    if not mid:
        raise HTTPException(status_code=400, detail="معرّف الجهاز (machine_id) مطلوب.")

    device = get_device(mid)

    # If device not yet registered on cloud, auto-onboard it as TRIAL lead!
    if not device:
        store_title = req.store_name.strip() or f"متجر تجريبي ({mid[-4:]})"
        init_status = "TRIAL" if (req.is_trial or not req.current_key) else "ACTIVE"
        init_months = 0 if init_status == "TRIAL" else 12
        device = register_or_create_device(
            machine_id=mid,
            store_name=store_title,
            phone="",
            status=init_status,
            months=init_months,
            tier="trial" if init_status == "TRIAL" else "pro",
            notes="عميل جديد بدأ النسخة التجريبية (مسجل تلقائياً)",
        )

    # Check administrative suspension
    if device.get("status") == "SUSPENDED":
        return {
            "has_update": False,
            "status": "SUSPENDED",
            "message": "تم إيقاف هذا الترخيص إدارياً. يرجى مراجعة إدارة النظام.",
        }

    # Record sync timestamp
    record_sync(mid, store_name=req.store_name or "")

    server_token = device.get("current_token", "")
    client_key = (req.current_key or "").strip()

    # If client has a different or empty key, send the server token as an update
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

    device = register_or_create_device(
        machine_id=mid,
        store_name=req.store_name,
        phone=req.phone or "",
        months=1,
        tier="pro",
        notes=req.notes or "",
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
        months=req.months,
        tier=req.tier or "pro",
        notes=req.notes or "",
    )
    return device


@app.post("/api/v1/admin/renew")
async def admin_renew_device(req: AdminRenewRequest, user: str = Depends(verify_admin)):
    """Instant 1-click renewal from admin smartphone/browser."""
    mid = req.machine_id.strip().upper()
    try:
        updated = renew_device(
            machine_id=mid,
            months=req.months,
            tier=req.tier,
            grace_days=req.grace_days,
        )
        return updated
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء التجديد: {exc}")


@app.post("/api/v1/admin/extend_trial")
async def admin_extend_trial(req: AdminExtendTrialRequest, user: str = Depends(verify_admin)):
    """Extend trial period by N days."""
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
