from __future__ import annotations

import base64
import hmac
import html
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('MAX_SALES_CLOUD_DB', BASE_DIR / 'cloud_dashboard.db'))
API_KEY = os.environ.get('MAX_SALES_CLOUD_API_KEY', '')
ADMIN_USER = os.environ.get('MAX_SALES_CLOUD_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('MAX_SALES_CLOUD_ADMIN_PASSWORD', '')
CLOUD_TIMEZONE = os.environ.get('MAX_SALES_CLOUD_TIMEZONE', 'Asia/Baghdad')
CLOUD_UTC_OFFSET = os.environ.get('MAX_SALES_CLOUD_UTC_OFFSET', '+03:00')


def _cloud_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(CLOUD_TIMEZONE)
    except Exception:
        try:
            sign = 1
            value = str(CLOUD_UTC_OFFSET).strip()
            if value.startswith('-'):
                sign = -1
                value = value[1:]
            elif value.startswith('+'):
                value = value[1:]
            hours, minutes = (value.split(':', 1) + ['0'])[:2]
            delta = timedelta(hours=int(hours) * sign, minutes=int(minutes) * sign)
            return timezone(delta)
        except Exception:
            return timezone(timedelta(hours=3))


def now() -> str:
    return datetime.now(_cloud_tz()).strftime('%Y-%m-%d %H:%M:%S')


def money(value) -> str:
    try:
        return f'{float(value or 0):,.0f} د.ع'
    except Exception:
        return '0 د.ع'


def num(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def esc(value) -> str:
    return html.escape(str(value if value is not None else ''))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            store_id TEXT PRIMARY KEY,
            store_name TEXT,
            received_at TEXT NOT NULL,
            sent_at TEXT,
            payload_json TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS snapshot_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            store_name TEXT,
            received_at TEXT NOT NULL,
            sent_at TEXT,
            payload_json TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            store_name TEXT,
            received_at TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT
        )''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_snapshot_history_store_time ON snapshot_history(store_id, received_at)')
        con.commit()


def save_snapshot(payload: dict) -> dict:
    init_db()
    store_id = str(payload.get('store_id') or 'default')
    store_name = str(payload.get('store_name') or store_id)
    received_at = now()
    sent_at = str(payload.get('sent_at') or '')
    text = json.dumps(payload, ensure_ascii=False)
    with sqlite3.connect(DB_PATH) as con:
        con.execute('''INSERT INTO snapshots(store_id, store_name, received_at, sent_at, payload_json)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(store_id) DO UPDATE SET
                         store_name=excluded.store_name,
                         received_at=excluded.received_at,
                         sent_at=excluded.sent_at,
                         payload_json=excluded.payload_json''',
                    (store_id, store_name, received_at, sent_at, text))
        con.execute('INSERT INTO snapshot_history(store_id, store_name, received_at, sent_at, payload_json) VALUES(?,?,?,?,?)',
                    (store_id, store_name, received_at, sent_at, text))
        con.execute('INSERT INTO sync_log(store_id, store_name, received_at, status, message) VALUES(?,?,?,?,?)',
                    (store_id, store_name, received_at, 'ok', 'snapshot accepted'))
        con.commit()
    return {'ok': True, 'received_at': received_at, 'store_id': store_id}


def latest_snapshot() -> dict | None:
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM snapshots ORDER BY received_at DESC LIMIT 1').fetchone()
        if not row:
            return None
        data = json.loads(row['payload_json'])
        data['_received_at'] = row['received_at']
        return data


def sync_history(limit=20) -> list[dict]:
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute('SELECT * FROM sync_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]


def snapshot_history(limit=50) -> list[dict]:
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT * FROM snapshot_history ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r['payload_json'])
                payload['_received_at'] = r['received_at']
                out.append(payload)
            except Exception:
                pass
        return out


class CloudDashboardHandler(BaseHTTPRequestHandler):
    server_version = 'MaxProCloudDashboard/2.0'

    def log_message(self, fmt, *args):
        return

    def _send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, text, status=200):
        data = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admin_ok(self) -> bool:
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Basic '):
            return False
        try:
            raw = base64.b64decode(auth.split(' ', 1)[1]).decode('utf-8')
            user, pw = raw.split(':', 1)
            if not ADMIN_PASSWORD:
                return False
            return hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(pw, ADMIN_PASSWORD)
        except Exception:
            return False

    def _require_admin(self) -> bool:
        if self._admin_ok():
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Max Pro Cloud Dashboard"')
        self.end_headers()
        return False

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/api/sync':
            self._send_json({'ok': False, 'error': 'not found'}, 404)
            return
        if not API_KEY or not hmac.compare_digest(self.headers.get('X-API-Key', ''), API_KEY):
            self._send_json({'ok': False, 'error': 'invalid api key'}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length <= 0 or length > 2_000_000:
                self._send_json({'ok': False, 'error': 'invalid payload size'}, 400)
                return
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            result = save_snapshot(payload)
            self._send_json(result)
        except Exception as exc:
            self._send_json({'ok': False, 'error': str(exc)}, 500)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            # نقطة الفحص لا تُفصح عن وقت الخادم لتقليل المعلومات المكشوفة
            self._send_json({'ok': True})
            return
        if not self._require_admin():
            return
        if path == '/api/latest':
            self._send_json(latest_snapshot() or {})
            return
        if path == '/api/history':
            self._send_json(snapshot_history())
            return
        self._send_html(render_dashboard())


def _bar_rows(items: list[dict], key: str, label_key: str = 'date', money_values: bool = True) -> str:
    if not items:
        return '<div class="empty-mini">لا توجد بيانات كافية للرسم</div>'
    maximum = max([num(x.get(key)) for x in items] + [1])
    rows = []
    for item in items:
        value = num(item.get(key))
        pct = max(2, min(100, (value / maximum) * 100 if maximum else 0))
        label = esc(str(item.get(label_key, '-'))[-5:] if label_key == 'date' else item.get(label_key, '-'))
        shown = money(value) if money_values else str(int(value))
        rows.append(f'<div class="bar-row"><span>{label}</span><div class="bar"><i style="width:{pct:.1f}%"></i></div><b>{shown}</b></div>')
    return ''.join(rows)


def _table(items: list[dict], columns: list[tuple[str, str]], empty='لا توجد بيانات') -> str:
    if not items:
        return f'<tr><td colspan="{len(columns)}">{esc(empty)}</td></tr>'
    out = []
    for item in items:
        cells = []
        for key, title in columns:
            value = item.get(key, '')
            if key in {'sales', 'profit', 'total', 'balance', 'expected_cash', 'counted_cash', 'cash_difference', 'opening_cash'}:
                value = money(value)
            cells.append(f'<td>{esc(value)}</td>')
        out.append('<tr>' + ''.join(cells) + '</tr>')
    return ''.join(out)


def render_dashboard() -> str:
    payload = latest_snapshot()
    history = sync_history()
    if not payload:
        store_name = 'لوحة ماكس برو السحابية'
        generated = '-'
        body = '<div class="empty">لم تصل أي بيانات بعد. شغّل المزامنة السحابية من نظام المحل.</div>'
    else:
        snap = payload.get('snapshot') or {}
        store_name = payload.get('store_name') or 'ماكس برو'
        generated = payload.get('_received_at') or payload.get('sent_at') or '-'
        sales = snap.get('sales') or {}
        returns = snap.get('returns') or {}
        series = snap.get('sales_series') or []
        top_products = snap.get('top_products') or []
        cashier_rows = snap.get('cashier_rows') or []
        shift = snap.get('shift_summary') or {}
        alerts = snap.get('cloud_alerts') or []
        security_alerts = snap.get('security_alerts') or []
        last_closed = shift.get('last_closed') or {}

        alert_html = ''.join(
            f'<div class="alert {esc(a.get("level","info"))}"><b>{esc(a.get("title","تنبيه"))}</b><span>{esc(a.get("message",""))}</span></div>'
            for a in alerts
        ) or '<div class="alert success"><b>الوضع مستقر</b><span>لا توجد تنبيهات حالياً.</span></div>'

        recent_sales = snap.get('recent_sales') or []
        recent_rows = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            esc(r.get('created_at','')), esc(r.get('invoice_no','')), money(r.get('total')), esc(r.get('payment_method',''))
        ) for r in recent_sales) or '<tr><td colspan="4">لا توجد فواتير</td></tr>'

        low = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            esc(r.get('name','')), esc(r.get('quantity','')), esc(r.get('min_quantity',''))
        ) for r in snap.get('low_rows') or []) or '<tr><td colspan="3">لا توجد منتجات ناقصة</td></tr>'

        debts = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            esc(r.get('name','')), esc(r.get('phone','')), money(r.get('balance'))
        ) for r in snap.get('debt_rows') or []) or '<tr><td colspan="3">لا توجد ديون تحتاج متابعة</td></tr>'

        open_shifts = shift.get('open_shifts') or []
        open_shift_rows = _table(open_shifts, [('id','رقم'), ('cashier','الكاشير'), ('opened_at','فتح'), ('opening_cash','افتتاحي')], 'لا توجد شفتات مفتوحة')
        closed_shift_rows = _table(shift.get('recent_closed') or [], [('id','رقم'), ('cashier','الكاشير'), ('closed_at','الإغلاق'), ('expected_cash','المتوقع'), ('counted_cash','الفعلي'), ('cash_difference','الفرق')], 'لا توجد شفتات مغلقة')
        cashier_table = _table(cashier_rows, [('cashier','الكاشير'), ('invoices','الفواتير'), ('sales','المبيعات'), ('profit','الربح')], 'لا توجد بيانات كاشير اليوم')
        security_rows = _table(security_alerts, [('created_at','الوقت'), ('title','التنبيه'), ('severity','الخطورة'), ('status','الحالة')], 'لا توجد تنبيهات أمان')

        diff_class = 'bad' if num(last_closed.get('cash_difference')) < 0 else 'good'
        body = f'''
        <div class="cards">
          <div class="card"><span>مبيعات اليوم</span><b>{money(sales.get('total'))}</b></div>
          <div class="card"><span>ربح اليوم</span><b>{money(sales.get('profit'))}</b></div>
          <div class="card"><span>عدد الفواتير</span><b>{int(sales.get('invoices') or 0)}</b></div>
          <div class="card"><span>مرتجعات اليوم</span><b>{money(returns.get('total'))}</b></div>
          <div class="card"><span>إجمالي الديون</span><b>{money(snap.get('total_debt'))}</b></div>
          <div class="card"><span>فرق آخر شفت</span><b class="{diff_class}">{money(last_closed.get('cash_difference'))}</b></div>
        </div>

        <div class="charts">
          <section><h2>مبيعات آخر 7 أيام</h2>{_bar_rows(series, 'sales', 'date', True)}</section>
          <section><h2>أرباح آخر 7 أيام</h2>{_bar_rows(series, 'profit', 'date', True)}</section>
          <section><h2>عدد الفواتير آخر 7 أيام</h2>{_bar_rows(series, 'invoices', 'date', False)}</section>
        </div>

        <section class="alerts"><h2>التنبيهات الأونلاين</h2>{alert_html}</section>

        <div class="grid">
          <section><h2>شفتات مفتوحة حالياً</h2><table><thead><tr><th>رقم</th><th>الكاشير</th><th>فتح</th><th>افتتاحي</th></tr></thead><tbody>{open_shift_rows}</tbody></table></section>
          <section><h2>آخر الشفتات المغلقة</h2><table><thead><tr><th>رقم</th><th>الكاشير</th><th>الإغلاق</th><th>المتوقع</th><th>الفعلي</th><th>الفرق</th></tr></thead><tbody>{closed_shift_rows}</tbody></table></section>
          <section><h2>أفضل الكاشيرين اليوم</h2><table><thead><tr><th>الكاشير</th><th>الفواتير</th><th>المبيعات</th><th>الربح</th></tr></thead><tbody>{cashier_table}</tbody></table></section>
          <section><h2>أفضل المنتجات</h2><table><thead><tr><th>المنتج</th><th>الكمية</th><th>الإجمالي</th></tr></thead><tbody>{_table(top_products, [('product_name','المنتج'), ('qty','الكمية'), ('total','الإجمالي')], 'لا توجد مبيعات اليوم')}</tbody></table></section>
          <section><h2>آخر الفواتير</h2><table><thead><tr><th>الوقت</th><th>الفاتورة</th><th>الإجمالي</th><th>الدفع</th></tr></thead><tbody>{recent_rows}</tbody></table></section>
          <section><h2>المخزون الناقص</h2><table><thead><tr><th>المنتج</th><th>الكمية</th><th>الحد</th></tr></thead><tbody>{low}</tbody></table></section>
          <section><h2>ديون تحتاج متابعة</h2><table><thead><tr><th>الزبون</th><th>الهاتف</th><th>الرصيد</th></tr></thead><tbody>{debts}</tbody></table></section>
          <section><h2>تنبيهات الأمان</h2><table><thead><tr><th>الوقت</th><th>التنبيه</th><th>الخطورة</th><th>الحالة</th></tr></thead><tbody>{security_rows}</tbody></table></section>
        </div>
        '''

    hist = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td></tr>'.format(esc(h.get('received_at','')), esc(h.get('store_name','')), esc(h.get('status',''))) for h in history) or '<tr><td colspan="3">لا يوجد سجل مزامنة</td></tr>'
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>لوحة ماكس برو السحابية</title><style>
    *{{box-sizing:border-box}} body{{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f3f7f8;color:#0f172a}}.top{{background:linear-gradient(90deg,#0f3439,#0b8786);color:white;padding:22px 28px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}.top h1{{margin:0;font-size:24px}}.wrap{{max-width:1440px;margin:auto;padding:22px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px;margin-bottom:18px}}.card,section,.empty{{background:white;border:1px solid #dce8ea;border-radius:16px;box-shadow:0 8px 24px rgba(15,52,57,.07);padding:18px}}.card span{{display:block;color:#64748b;margin-bottom:8px}}.card b{{color:#0f766e;font-size:24px}}.bad{{color:#dc2626!important}}.good{{color:#16a34a!important}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:16px}}h2{{margin:0 0 12px;font-size:18px;color:#11383d}}table{{width:100%;border-collapse:collapse}}th{{background:#e7f5f5;color:#0f3439}}td,th{{padding:10px;border-bottom:1px solid #edf2f3;text-align:right;font-size:14px}}.muted{{opacity:.85}}.history{{margin-top:16px}}.foot{{text-align:center;color:#64748b;padding:18px}}.bar-row{{display:grid;grid-template-columns:56px 1fr 95px;gap:8px;align-items:center;margin:10px 0}}.bar{{height:12px;background:#e5eef0;border-radius:999px;overflow:hidden}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,#0f766e,#10b6ae);border-radius:999px}}.bar-row b{{font-size:12px;color:#334155;text-align:left}}.alerts{{margin-bottom:16px}}.alert{{border-radius:12px;padding:12px 14px;margin:8px 0;border:1px solid #dbeafe;background:#eff6ff}}.alert b{{display:block;margin-bottom:4px}}.alert.warning{{border-color:#fde68a;background:#fffbeb}}.alert.danger,.alert.high{{border-color:#fecaca;background:#fef2f2}}.alert.success{{border-color:#bbf7d0;background:#f0fdf4}}.alert.info{{border-color:#bfdbfe;background:#eff6ff}}.empty-mini{{color:#64748b;background:#f8fafc;border-radius:10px;padding:12px}}
    </style></head><body><div class="top"><h1>{esc(store_name)} - لوحة أونلاين Pro</h1><div class="muted">آخر وصول: {esc(str(generated))}</div></div><div class="wrap">{body}<section class="history"><h2>سجل المزامنة</h2><table><thead><tr><th>الوقت</th><th>المحل</th><th>الحالة</th></tr></thead><tbody>{hist}</tbody></table></section></div><div class="foot">© PY : MOHIMEN MAX</div></body></html>'''


def run(host='0.0.0.0', port=9080):
    init_db()
    print('Max Pro Cloud Dashboard Pro')
    print(f'Dashboard: http://{host}:{port}')
    print('Admin user:', ADMIN_USER)
    _using_defaults = []
    if not API_KEY:
        _using_defaults.append('MAX_SALES_CLOUD_API_KEY')
    if not ADMIN_PASSWORD:
        _using_defaults.append('MAX_SALES_CLOUD_ADMIN_PASSWORD')
    if _using_defaults:
        raise RuntimeError(
            'خطأ أمني: يجب تغيير بيانات الاعتماد الافتراضية قبل تشغيل الخادم.\n'
            f'المتغيرات المطلوب تعيينها: {", ".join(_using_defaults)}\n'
            'مثال: set MAX_SALES_CLOUD_API_KEY=your-secret-key'
        )
    ThreadingHTTPServer((host, int(port)), CloudDashboardHandler).serve_forever()


if __name__ == '__main__':
    run(os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '9080')))
