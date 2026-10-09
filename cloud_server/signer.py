from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519

# Default Master Private Key (Vendor secret)
# In production, set the MAXPRO_MASTER_PRIVATE_KEY_B64 environment variable.
DEFAULT_FALLBACK_PRIV_KEY_B64 = "HXZv2q/GRrl2l08CCpAB7GiyoCsmgpjTP5nlNikB57o="
MASTER_PUBLIC_KEY_B64 = "MLInzK8PM9hTWVf3pO/l6EM/PXtZajUu7PXQwsf34N0="
LICENSE_PREFIX = "MPLIC-"


def get_private_key_bytes() -> bytes:
    """Load vendor private key from environment or fallback key."""
    env_key = os.environ.get("MAXPRO_MASTER_PRIVATE_KEY_B64", "").strip()
    if env_key:
        return base64.b64decode(env_key)

    # Check local developer secrets if running inside the repository
    local_key_file = Path(__file__).resolve().parent.parent / "developer_secrets" / "master_private.key"
    if local_key_file.exists():
        try:
            content = local_key_file.read_text(encoding="ascii").strip()
            if content:
                return base64.b64decode(content)
        except Exception:
            pass

    return base64.b64decode(DEFAULT_FALLBACK_PRIV_KEY_B64)


def canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    """Deterministic JSON serialization for cryptographic hashing and signing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_license_payload(payload: Dict[str, Any], private_key_bytes: bytes) -> str:
    """Sign a license dictionary with the vendor's private key and return an MPLIC-... token."""
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    data_bytes = canonical_json_bytes(payload)
    signature = priv.sign(data_bytes)

    envelope = {
        "v": 1,
        "d": base64.urlsafe_b64encode(data_bytes).decode("ascii"),
        "s": base64.urlsafe_b64encode(signature).decode("ascii"),
    }
    envelope_json = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    token_body = base64.urlsafe_b64encode(envelope_json).decode("ascii")
    return f"{LICENSE_PREFIX}{token_body}"


def build_and_sign_token(
    machine_id: str,
    store_name: str,
    expires_at: datetime,
    plan_code: str = "12_months",
    plan_name_ar: str = "اشتراك سنوي (12 شهراً - Pro)",
    tier: str = "pro",
    grace_days: int = 5,
) -> Tuple[str, Dict[str, Any]]:
    """Build canonical payload and sign it cryptographically."""
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    payload = {
        "store": store_name.strip(),
        "mid": machine_id.strip().upper(),
        "plan": plan_code,
        "plan_ar": plan_name_ar,
        "exp": expires_at.strftime("%Y-%m-%dT23:59:59"),
        "iat": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "grace": int(grace_days),
        "tier": tier,
        "features": ["all"],
    }
    priv_bytes = get_private_key_bytes()
    token = sign_license_payload(payload, priv_bytes)
    return token, payload


def verify_license_token(license_str: str, public_key_b64: Optional[str] = None) -> Dict[str, Any]:
    """Verify an MPLIC-... license token with the master public key.
    
    Returns the verified payload dictionary.
    Raises ValueError on invalid token format, tamper, or signature failure.
    """
    from cryptography.exceptions import InvalidSignature

    pub_key_str = public_key_b64 or MASTER_PUBLIC_KEY_B64
    clean_str = str(license_str or "").strip()
    if not clean_str.startswith(LICENSE_PREFIX):
        raise ValueError("صيغة كود الترخيص غير صالحة (يجب أن تبدأ بـ MPLIC-)")

    token_body = clean_str[len(LICENSE_PREFIX):].strip()
    try:
        envelope_json = base64.urlsafe_b64decode(token_body.encode("ascii"))
        envelope = json.loads(envelope_json.decode("utf-8"))
    except Exception as exc:
        raise ValueError("كود الترخيص تالف أو غير صالح للفك") from exc

    if not isinstance(envelope, dict) or "d" not in envelope or "s" not in envelope:
        raise ValueError("هيكل حزمة الترخيص غير مطابق للمواصفات")

    try:
        data_bytes = base64.urlsafe_b64decode(envelope["d"].encode("ascii"))
        sig_bytes = base64.urlsafe_b64decode(envelope["s"].encode("ascii"))
    except Exception as exc:
        raise ValueError("بيانات الترخيص المشفرة غير صالحة") from exc

    pub_bytes = base64.b64decode(pub_key_str.encode("ascii"))
    pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)

    try:
        pub.verify(sig_bytes, data_bytes)
    except InvalidSignature as exc:
        raise ValueError("فشل التحقق من التوقيع الرقمي للترخيص (كود مزور أو تم التعديل عليه)") from exc

    try:
        payload = json.loads(data_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("محتوى الترخيص غير صالح")
        return payload
    except Exception as exc:
        raise ValueError("فشل قراءة بيانات الترخيص") from exc
