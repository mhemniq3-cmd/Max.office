# Max Pro Cloud Dashboard Server

لوحة أونلاين منفصلة تستقبل ملخصات من نظام المحل عبر طلبات خارجة فقط. لا تحتاج فتح Port على جهاز المحل.

## التشغيل المحلي للتجربة

```bash
cd cloud_dashboard_server
set MAX_SALES_CLOUD_API_KEY=ضع_مفتاح_سري
set MAX_SALES_CLOUD_ADMIN_USER=admin
set MAX_SALES_CLOUD_ADMIN_PASSWORD=كلمة_قوية
python app.py
```

افتح:

```text
http://SERVER-IP:9080
```

## نقطة استقبال البيانات

```text
POST /api/sync
Header: X-API-Key: <MAX_SALES_CLOUD_API_KEY>
Content-Type: application/json
```

## ملاحظات أمان

- غيّر مفتاح API وكلمة مرور المدير قبل رفعها على الاستضافة.
- استخدم HTTPS في الاستضافة الحقيقية.
- النسخة الأولى قراءة فقط: لا ترسل كلمات مرور المستخدمين ولا قاعدة البيانات الكاملة.

## Timezone / توقيت لوحة Render

The dashboard displays received times using `MAX_SALES_CLOUD_TIMEZONE`.
Default is `Asia/Baghdad` (UTC+3). On Render you may add:

```text
MAX_SALES_CLOUD_TIMEZONE=Asia/Baghdad
```

Fallback offset variable if zoneinfo is unavailable:

```text
MAX_SALES_CLOUD_UTC_OFFSET=+03:00
```
