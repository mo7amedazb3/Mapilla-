from odoo import http
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)

class WhatsAppWebhookController(http.Controller):

    @http.route('/whatsapp/webhook', type='http', auth='public', methods=['POST', 'GET'], csrf=False)
    def receive_webhook(self, **kwargs):
        try:
            # Handle plain JSON payload from webhook
            data = request.httprequest.data
            payload = {}
            if data:
                payload = json.loads(data.decode('utf-8'))
            payload.update(kwargs)
            
            sender = payload.get('sender') or payload.get('mobile') or ''
            body = payload.get('body') or ''

            if not sender or not body:
                return request.make_response(json.dumps({'error': 'Missing sender or body'}), headers=[('Content-Type', 'application/json')])

            if '@' in sender:
                mobile = sender.split('@')[0]
            else:
                mobile = sender

            payload['mobile'] = mobile
            _logger.info("WhatsApp Webhook received: From=%s, Body=%s", mobile, body)
            
            # Need to get env with su=True for public routes
            env = request.env(su=True)
            env['whatsapp.session.message'].sudo().process_incoming_webhook(payload)
            
            return request.make_response(json.dumps({'status': 'success'}), headers=[('Content-Type', 'application/json')])

        except Exception as e:
            _logger.error("Error processing WhatsApp Webhook: %s", str(e))
            return request.make_response(json.dumps({'error': str(e)}), headers=[('Content-Type', 'application/json')])
