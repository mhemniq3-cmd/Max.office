# 🌐 سيرفر التراخيص السحابي المركزي (Max Pro Cloud License Server)

نظام إدارة الاشتراكات والتجديد الفوري عن بُعد لبرنامج **ماكس برو للمبيعات (Max Pro POS)**.

---

## 🚀 فكرة عمل النظام
1. **التسجيل التلقائي أو اليدوي**: عندما يقوم العميل بتثبيت البرنامج أو تجربة النسخة المجانية، يسجل معرف جهازه (`Machine ID`) في السيرفر كعميل تجريبي (`TRIAL`).
2. **سداد الاشتراك**: يحول لك العميل قيمة الاشتراك (زين كاش، ماستر كارد، أو نقداً).
3. **تجديد فوري بنقرة واحدة**: من هاتفك أو متصفحك عبر لوحة التحكم (`/admin`)، تضغط زر **⚡ تجديد سنة**.
4. **تفعيل تلقائي لدى العميل**: في نفس اللحظة التي يفتح فيها العميل برنامجه أو يضغط **☁️ تحديث الاشتراك سحابياً**، يتجدد الترخيص فورياً دون الحاجة لإرسال أي كود يدوي.
5. **البديل اليدوي بدون إنترنت**: إذا كان محل العميل بدون إنترنت إطلاقاً، يمكنك نسخ كود التفعيل المشفّر بضغطة زر وإرساله له عبر الواتساب ليفعله يدوياً.

---

## 🔐 حماية لوحة التحكم (Admin Authentication)
لوحة التحكم `/admin` وجميع واجهات الإدارة محمية بتسجيل دخول (HTTP Basic Auth):
* **اسم المستخدم الافتراضي:** `admin` (قابل للتغيير عبر المتغير `ADMIN_USERNAME`)
* **كلمة المرور الافتراضية:** `maxpro@2026` (قابل للتغيير عبر المتغير `ADMIN_PASSWORD`)
* عند فتح الرابط في المتصفح، تظهر نافذة طلب اسم المستخدم وكلمة المرور تلقائياً.

---

## 🛠️ التشغيل المحلي (Local Run)
```bash
python cloud_server/run_server.py
```
أو بالنقر المزدوج على: `run_license_server.bat`.
* لوحة تحكم المالك: `http://localhost:8000/admin`
* فحص سلامة السيرفر: `http://localhost:8000/health`
* رابط المزامنة لبرامج الكاشير: `http://localhost:8000/api/v1/license/sync`

---

## ☁️ خيارات الرفع السحابي (Deployment Options)

### 🥇 الخيار الأول: Railway.app (الأسرع والأسهل - 3 دقائق)
1. افتح حساباً في **[Railway.app](https://railway.app)**.
2. اضغط **New Project** واختر **Deploy from GitHub repo**.
3. في قسم **Variables**، أضف:
   * `ADMIN_USERNAME`: اسم الدخول الخاص بك (مثلاً: `admin`).
   * `ADMIN_PASSWORD`: كلمة مرور قوية من اختيارك.
   * `PORT`: `8000`.
4. اضغط **Generate Domain** في إعدادات الخدمة لتحصل على رابط مشفر مثل:
   `https://license-maxpro.up.railway.app`

---

### 🥈 الخيار الثاني: Render.com (سهل ومجاني)
1. ارفع الكود إلى GitHub.
2. في **[Render.com](https://render.com)** اختر **New Web Service** وحدد مستودعك.
3. الإعدادات:
   * **Runtime:** Python 3
   * **Build Command:** `pip install -r cloud_server/requirements.txt`
   * **Start Command:** `uvicorn cloud_server.app:app --host 0.0.0.0 --port $PORT`
4. في **Environment Variables** أضف `ADMIN_USERNAME` و `ADMIN_PASSWORD`.

---

### 🥉 الخيار الثالث: خادم افتراضي خاص (VPS - Hetzner / DigitalOcean)
وهو الخيار الأكثر استقراراً تجارياً على الإطلاق:
1. استأجر خادم Ubuntu في Hetzner (3.8€/شهر) أو DigitalOcean (4$/شهر).
2. انسخ مجلد `cloud_server` للخادم.
3. شغّل سكربت التثبيت التلقائي بضغطة واحدة:
   ```bash
   bash cloud_server/deploy_vps.sh
   ```
4. لربط الدومين مع شهادة SSL مجانية:
   ```bash
   sudo certbot --nginx -d license.yourdomain.com
   ```

---

## 🔗 ربط برنامج الكاشير بالسيرفر بعد رفعه
بعد حصولك على الرابط السحابي (مثلاً `https://license-maxpro.up.railway.app`):
في ملف `services/licensing/cloud_client.py`:
قم بتغيير `DEFAULT_CLOUD_SERVER` إلى رابطك الجديد:
```python
DEFAULT_CLOUD_SERVER = "https://license-maxpro.up.railway.app"
```
أو تعيين المتغير البيئي في نظام ويندوز:
```powershell
[System.Environment]::SetEnvironmentVariable('MAXPRO_LICENSE_SERVER_URL', 'https://license-maxpro.up.railway.app', 'User')
```
وسيقوم البرنامج بالمزامنة والتجديد الفوري مع سيرفرك السحابي تلقائياً!
