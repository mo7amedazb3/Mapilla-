"""Authenticated callbacks from the local WhatsApp transport, plus QR pairing UI."""
import base64
import hmac
import io
import json
import logging

import qrcode
from odoo import fields, http
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

_logger = logging.getLogger(__name__)
STATUS_MAP = {
    'INITIALIZING': 'initializing', 'QR': 'qr', 'AUTHENTICATED': 'authenticated',
    'READY': 'ready', 'OFFLINE': 'offline', 'OFFSESS': 'offsess', 'AUTH_FAILURE': 'auth_failure',
}


class WhatsAppLocalBridge(http.Controller):
    @http.route('/whatsapp/bridge/event', type='http', auth='public', methods=['POST'], csrf=False)
    def bridge_event(self, **kwargs):
        def response(payload, status=200):
            return request.make_json_response(payload, status=status)

        expected = request.env['ir.config_parameter'].sudo().get_param('whatsapp_integration.bridge_token')
        supplied = request.httprequest.headers.get('X-WhatsApp-Bridge-Token', '')
        if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
            return response({'error': 'Unauthorized'}, 403)
        try:
            payload = json.loads(request.httprequest.get_data())
            if not isinstance(payload, dict) or payload.get('database') != request.env.cr.dbname:
                return response({'error': 'Invalid database'}, 400)
            instance = request.env['whatsapp.instance'].sudo().search([
                ('instance_id', '=', payload.get('clientId')),
            ], limit=1)
            if not instance:
                return response({'error': 'Unknown instance'}, 404)
            data = payload.get('data')
            if not isinstance(data, dict):
                return response({'error': 'Invalid data'}, 400)
            event = payload.get('event')
            if event == 'status':
                status = STATUS_MAP.get(data.get('status'))
                if not status:
                    return response({'error': 'Invalid status'}, 400)
                vals = {'status': status, 'last_check': fields.Datetime.now()}
                if data.get('host_phone'):
                    vals['host_phone'] = ''.join(c for c in str(data['host_phone']) if c.isdigit())
                elif status not in ('ready', 'authenticated'):
                    vals['host_phone'] = False
                if data.get('qr') and status == 'qr':
                    buffer = io.BytesIO()
                    qrcode.make(data['qr']).save(buffer, format='PNG')
                    vals['qr_code'] = base64.b64encode(buffer.getvalue())
                else:
                    vals['qr_code'] = False
                instance.write(vals)
            elif event in ('message', 'sync'):
                # Resolve identity from the configured instance, never from an untrusted event field.
                data['clientId'] = instance.instance_id
                data['hostPhone'] = instance.host_phone or data.get('hostPhone')
                model = request.env['whatsapp.session.message'].sudo()
                with request.env.cr.savepoint():
                    if event == 'message':
                        if not model.process_incoming_webhook(data):
                            return response({'error': 'Message could not be imported'}, 422)
                    else:
                        if not model.process_sync_payload(data):
                            return response({'error': 'History could not be imported'}, 422)
                        instance.last_history_sync = fields.Datetime.now()
            else:
                return response({'error': 'Unknown event'}, 400)
            return response({'status': 'success'})
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # A send request and its message_create webhook can touch the same
            # conversation. Let Odoo retry only this database import, not the send.
            raise
        except (ValueError, TypeError):
            return response({'error': 'Invalid payload'}, 400)
        except Exception:
            # Explicit rollback: a retry must not acknowledge a partially applied event.
            request.env.cr.rollback()
            _logger.exception('Local WhatsApp bridge event failed')
            return response({'error': 'Event processing failed'}, 500)

    @http.route('/whatsapp/bridge/connection', type='json', auth='user')
    def connection_state(self, instance_id=3):
        instance = request.env['whatsapp.instance'].browse(int(instance_id)).exists()
        if not instance:
            return {'status': 'missing'}
        instance.check_access('read')
        qr = instance.qr_code
        return {
            'status': instance.status, 'phone': instance.host_phone or '',
            'qr': qr.decode() if isinstance(qr, bytes) else (qr or ''),
        }

    @http.route('/whatsapp/connect', type='http', auth='user', methods=['GET'])
    def pairing_page(self, **kwargs):
        return request.make_response(PAIRING_PAGE, headers=[
            ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
            ('X-Frame-Options', 'SAMEORIGIN'),
        ])


PAIRING_PAGE = '''<!doctype html>
<html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ربط واتساب — Mapilla</title>
<style>body{margin:0;background:#f3f6f5;font:18px system-ui;color:#153d33;display:grid;min-height:100vh;place-items:center}
main{background:white;padding:32px;border-radius:24px;max-width:520px;text-align:center;box-shadow:0 12px 50px #153d3314}
img{width:300px;max-width:85vw;image-rendering:pixelated}a{color:#087e60}p{line-height:1.8}#status{font-weight:700}</style>
<main><h1>ربط واتساب</h1><p id="status">جارٍ تحميل حالة الاتصال…</p><img id="qr" hidden alt="كود ربط واتساب">
<p id="help">من واتساب على الموبايل: الأجهزة المرتبطة ← ربط جهاز، ثم امسح الكود.<br>الكود بيتحدّث تلقائيًا.</p>
<p id="phone"></p><a href="/odoo/action-706">العودة لإعدادات الاتصال</a> · <a href="/whatsapp/chat">فتح المحادثات</a></main>
<script>
const names={qr:'امسح الكود لربط رقمك',initializing:'جارٍ تجهيز الاتصال…',authenticated:'تم الربط، جارٍ تحميل المحادثات…',ready:'واتساب متصل وجاهز',offline:'خدمة الربط غير متصلة حاليًا',offsess:'الحساب غير مربوط',auth_failure:'تعذّر ربط الحساب',missing:'إعداد الاتصال غير موجود'};
const instanceId=Number(new URLSearchParams(location.search).get('instance_id')||3);
async function refresh(){try{const r=await fetch('/whatsapp/bridge/connection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',method:'call',params:{instance_id:instanceId},id:1})});
const data=await r.json();if(data.error)throw new Error();const s=data.result;
document.getElementById('status').textContent=names[s.status]||s.status;
const img=document.getElementById('qr');img.hidden=!s.qr;if(s.qr)img.src='data:image/png;base64,'+s.qr;
document.getElementById('help').hidden=s.status==='ready';document.getElementById('phone').textContent=s.phone?'الرقم المتصل: '+s.phone:'';
}catch(e){document.getElementById('status').textContent='تعذّر تحديث الحالة. تأكد من تسجيل الدخول إلى أودو.'}finally{setTimeout(refresh,3000)}}refresh();
</script></html>'''
