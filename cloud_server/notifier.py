from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("cloud_server.notifier")


def get_telegram_config(db_path: Optional[Path] = None) -> tuple[str, str]:
    """Retrieve Telegram Bot Token and Chat ID from database or environment variables."""
    token = ""
    chat_id = ""
    try:
        from cloud_server.db import get_setting
        token = get_setting("telegram_bot_token", "", db_path=db_path)
        chat_id = get_setting("telegram_chat_id", "", db_path=db_path)
    except Exception:
        pass

    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    return token.strip(), chat_id.strip()


def send_telegram_message_sync(
    text: str,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    db_path: Optional[Path] = None,
) -> bool:
    """Send message to Telegram synchronously. Returns True on success."""
    bot_token = token or ""
    target_chat = chat_id or ""

    if not bot_token or not target_chat:
        cfg_token, cfg_chat = get_telegram_config(db_path=db_path)
        bot_token = bot_token or cfg_token
        target_chat = target_chat or cfg_chat

    if not bot_token or not target_chat:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "MaxProCloud/2.5"},
    )

    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning(f"Failed to send Telegram message: {exc}")
        return False


def send_telegram_notification(
    text: str,
    parse_mode: str = "HTML",
    db_path: Optional[Path] = None,
) -> None:
    """Send notification to Telegram in a background daemon thread so it never blocks HTTP requests."""
    t = threading.Thread(
        target=send_telegram_message_sync,
        kwargs={"text": text, "parse_mode": parse_mode, "db_path": db_path},
        daemon=True,
    )
    t.start()


# ─── Specialized Notifications ───────────────────────────────────────────────

def notify_new_store_onboarded(device: Dict[str, Any], db_path: Optional[Path] = None) -> None:
    """Triggered when a new store or trial device connects or is created."""
    mid = device.get("machine_id", "MP-????")
    name = device.get("store_name", "متجر غير معروف")
    branch = device.get("branch_name", "")
    phone = device.get("phone", "") or "غير مسجل"
    status = device.get("status", "ACTIVE")
    plan = device.get("plan_name_ar", "اشتراك")
    exp = (device.get("expires_at") or "")[:10]
    days = device.get("days_remaining", 0)

    status_icon = "🟣" if status == "TRIAL" else "🟢"
    branch_txt = f" ({branch})" if branch else ""

    msg = (
        f"🔔 <b>متجر جديد بدأ العمل!</b>\n\n"
        f"🏪 <b>اسم المتجر:</b> {name}{branch_txt}\n"
        f"💻 <b>معرّف الجهاز:</b> <code>{mid}</code>\n"
        f"📱 <b>الهاتف:</b> {phone}\n"
        f"{status_icon} <b>الحالة:</b> {plan}\n"
        f"📅 <b>الصلاحية:</b> حتى {exp} ({days} يوم متبقٍ)\n\n"
        f"✨ <i>نظام ماكس برو POS السحابي</i>"
    )
    send_telegram_notification(msg, db_path=db_path)


def notify_subscription_renewed(
    device: Dict[str, Any], details: str, db_path: Optional[Path] = None
) -> None:
    """Triggered upon renewing or extending a device license."""
    mid = device.get("machine_id", "MP-????")
    name = device.get("store_name", "متجر")
    branch = device.get("branch_name", "")
    exp = (device.get("expires_at") or "")[:10]
    days = device.get("days_remaining", 0)
    branch_txt = f" ({branch})" if branch else ""

    msg = (
        f"⚡ <b>تم تجديد ترخيص المتجر بنجاح!</b>\n\n"
        f"🏪 <b>المتجر:</b> {name}{branch_txt}\n"
        f"💻 <b>معرّف الجهاز:</b> <code>{mid}</code>\n"
        f"📝 <b>التفاصيل:</b> {details}\n"
        f"📅 <b>الصلاحية الجديدة:</b> حتى {exp} ({days} يوم متبقٍ)\n\n"
        f"🚀 <i>تم التحديث فورياً بسيرفر التراخيص</i>"
    )
    send_telegram_notification(msg, db_path=db_path)


def notify_status_changed(
    device: Dict[str, Any], is_suspended: bool, db_path: Optional[Path] = None
) -> None:
    """Triggered upon suspending or reactivating a device."""
    mid = device.get("machine_id", "MP-????")
    name = device.get("store_name", "متجر")
    status_text = "⛔ تم إيقاف المتجر مؤقتاً" if is_suspended else "🟢 تم إعادة تنشيط المتجر"

    msg = (
        f"{status_text}\n\n"
        f"🏪 <b>المتجر:</b> {name}\n"
        f"💻 <b>معرّف الجهاز:</b> <code>{mid}</code>\n"
        f"🕒 <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    send_telegram_notification(msg, db_path=db_path)
