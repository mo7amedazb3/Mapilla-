import base64
from urllib.parse import quote

import requests
from odoo import http, fields
import logging
from datetime import datetime
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from ..send_safety import send_request_id

_logger = logging.getLogger(__name__)

class WhatsAppSPAController(http.Controller):

    @http.route('/whatsapp/avatar/<int:session_id>', type='http', auth='user')
    def get_session_avatar(self, session_id, **kwargs):
        session = http.request.env['whatsapp.session'].sudo().browse(session_id)
        if not session.exists() or not session.whatsapp_image:
            return http.request.not_found()
        return http.request.make_response(
            base64.b64decode(session.whatsapp_image),
            headers=[
                ('Content-Type', 'image/jpeg'),
                ('Cache-Control', 'private, max-age=3600'),
            ],
        )

    @http.route('/whatsapp/media/<int:message_id>', type='http', auth='user')
    def get_message_media(self, message_id, **kwargs):
        message = http.request.env['whatsapp.session.message'].sudo().browse(message_id)
        if not message.exists() or not message.media_b64:
            return http.request.not_found()
        mime = str(message.media_mime or message.media_type or 'application/octet-stream')
        mime = mime.split(';', 1)[0].strip()
        mime = {'ptt':'audio/ogg', 'audio':'audio/ogg', 'image':'image/jpeg', 'video':'video/mp4'}.get(mime, mime)
        filename = str(message.media_filename or 'media').replace('\r', '').replace('\n', '')
        disposition = 'attachment' if str(kwargs.get('download', '')).lower() in ('1', 'true', 'yes') else 'inline'
        data = base64.b64decode(message.media_b64)
        response = http.request.make_response(
            data,
            headers=[
                ('Content-Type', mime),
                ('Content-Disposition', f"{disposition}; filename*=UTF-8''{quote(filename)}"),
                ('Cache-Control', 'private, max-age=3600'),
                ('X-Content-Type-Options', 'nosniff'),
            ],
        )
        return response.make_conditional(http.request.httprequest, accept_ranges=True, complete_length=len(data))

    @http.route('/whatsapp/api/fetch_media', type='json', auth='user')
    def fetch_message_media(self, message_id):
        message = http.request.env['whatsapp.session.message'].browse(int(message_id)).exists()
        if not message:
            return {'status':'unavailable'}
        message.check_access('read')
        if message.media_b64:
            return {'status':'success'}
        instance = message.session_id.instance_id
        if not message.wa_message_id or not instance:
            return {'status':'unavailable', 'message':'الملف القديم غير متاح حاليًا؛ انتظر اكتمال المزامنة.'}
        try:
            response = requests.post(instance.api_url.rstrip('/') + '/media', json={
                'clientId':instance.instance_id, 'messageId':message.wa_message_id,
            }, timeout=45)
            if not response.ok:
                return {'status':'unavailable', 'message':'تعذّر استرجاع الملف من واتساب؛ قد يكون لم يعد متاحًا.'}
            data = response.json()
            if data.get('waMessageId') != message.wa_message_id or not data.get('mediaB64'):
                return {'status':'unavailable'}
            message.write({'media_b64':data['mediaB64'], 'media_mime':data.get('mediaMime'),
                'media_filename':data.get('mediaFilename'), 'media_type':data.get('mediaType')})
            return {'status':'success'}
        except requests.RequestException:
            return {'status':'unavailable', 'message':'تعذّر الاتصال بواتساب؛ حاول لاحقًا.'}

    @http.route('/whatsapp/chat', type='http', auth='user')
    def render_chat_ui(self, **kwargs):
        # Render the custom HTML View
        return http.request.render('whatsapp_integration.whatsapp_web_ui_template', {})

    @http.route('/whatsapp/api/get_sessions', type='json', auth='user')
    def get_sessions(self, instance_id=None, **kwargs):
        try:
            domain = []
            instance_model = http.request.env['whatsapp.instance'].sudo()
            if instance_id:
                instance = instance_model.browse(int(instance_id))
                if not instance.exists() or instance.status not in ('ready', 'authenticated'):
                    return []
                domain.append(('instance_id', '=', int(instance_id)))
            else:
                visible_instances = instance_model.search([('status', 'in', ('ready', 'authenticated'))])
                if not visible_instances:
                    return []
                domain.append(('instance_id', 'in', visible_instances.ids))

            session_model = http.request.env['whatsapp.session'].sudo()
            canonical_ids = session_model._canonical_session_ids(domain)
            sessions = session_model.browse(canonical_ids).sorted(
                key=lambda rec: (rec.last_message_date or fields.Datetime.from_string('1970-01-01 00:00:00'), rec.id),
                reverse=True,
            )
            res = []
            message_model = http.request.env['whatsapp.session.message'].sudo()
            for s in sessions:
                if s.mobile and s.host_phone and s.mobile == s.host_phone:
                    has_incoming = bool(message_model.search_count([
                        ('session_id', '=', s.id),
                        ('msg_type', '=', 'in'),
                    ]))
                    if not has_incoming:
                        continue
                local_time = fields.Datetime.context_timestamp(http.request.env.user, s.last_message_date) if s.last_message_date else None
                # Get last message preview
                last_msg_rec = message_model.search(
                    [('session_id', '=', s.id)], order='date desc', limit=1
                )
                last_msg = ''
                if last_msg_rec:
                    if last_msg_rec.media_type and last_msg_rec.media_type not in ('False', ''):
                        mtype = last_msg_rec.media_type.split('/')[0] if '/' in (last_msg_rec.media_type or '') else last_msg_rec.media_type
                        icons = {'image': '🖼', 'video': '🎥', 'audio': '🎤', 'document': '📎'}
                        last_msg = icons.get(mtype, '📎') + ' ' + (last_msg_rec.body or mtype)
                    else:
                        last_msg = last_msg_rec.body or ''
                    if last_msg_rec.msg_type == 'out':
                        last_msg = '↩ ' + last_msg
                
                display_name = str(s.whatsapp_name or s.name or s.mobile or '')
                initials = display_name[0].upper() if display_name else '?'
                
                partner = http.request.env['res.partner'].sudo().search([('mobile', '=', s.mobile)], limit=1)
                is_registered = bool(partner)
                partner_id = partner.id if partner else False

                labels_data = [{'id': l.id, 'name': l.name, 'color': l.color} for l in s.label_ids]
                
                res.append({
                    'id': s.id,
                    'name': display_name,
                    'mobile': str(s.mobile or ''),
                    'time': local_time.strftime('%I:%M %p') if local_time else '',
                    'last_msg': str(last_msg)[:55],
                    'initials': initials,
                    'is_registered': is_registered,
                    'partner_id': partner_id,
                    'labels': labels_data,
                    'image_url': (
                        f'/whatsapp/avatar/{s.id}'
                        f'?db={http.request.env.cr.dbname}'
                        f'&unique={int(s.write_date.timestamp()) if s.write_date else s.id}'
                    ) if s.whatsapp_image else '',
                })
            return res
        except Exception as e:
            _logger.error("API get_sessions Error: %s", str(e))
            raise

    @http.route('/whatsapp/api/get_instances', type='json', auth='user')
    def get_instances(self, **kwargs):
        """Returns list of configured WhatsApp instances from WA Connector"""
        try:
            instances = http.request.env['whatsapp.instance'].sudo().search([])
            res = []
            for inst in instances:
                res.append({
                    'id': inst.id,
                    'name': inst.name,
                    'instance_id': inst.instance_id,
                    'status': inst.status,
                    'mobile': inst.host_phone or '',
                })
            return res
        except Exception as e:
            _logger.error("API get_instances Error: %s", str(e))
            return []

    @http.route('/whatsapp/api/sync_chats', type='json', auth='public', csrf=False)
    def sync_chats(self, chats=None, clientId='default', hostPhone=None, **kwargs):
        """Receives recent chats and delegates import to the session message model."""
        if not chats:
            return {'status': 'success', 'message': 'No chats provided'}

        payload = {
            'clientId': clientId,
            'hostPhone': hostPhone,
            'chats': chats,
        }
        try:
            http.request.env['whatsapp.session.message'].sudo().process_sync_payload(payload)
            return {'status': 'success'}
        except Exception as e:
            _logger.error("Error syncing chats: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/get_messages', type='json', auth='user')
    def get_messages(self, session_id, **kwargs):
        try:
            messages = http.request.env['whatsapp.session.message'].search([('session_id', '=', int(session_id))], order='date asc')
            _logger.info(f"get_messages called for session {session_id}, returned {len(messages)} messages")
            res = []
            for m in messages:
                local_date = fields.Datetime.context_timestamp(http.request.env.user, m.date) if m.date else None
                res.append({
                    'id': m.id,
                    'body': str(m.body or ''),
                    'type': str(m.msg_type or ''), # 'in' or 'out'
                    'time': local_date.strftime('%I:%M %p') if local_date else '',
                    # Poll only metadata; audio/images load once via the cached
                    # authenticated media URL instead of repeating Base64 every 3s.
                    'media_b64': '',
                    'media_type': str(m.media_type or ''),
                    'media_mime': str(m.media_mime or ''),
                    'media_filename': str(m.media_filename or ''),
                    'media_url': f'/whatsapp/media/{m.id}?db={http.request.env.cr.dbname}' if m.media_b64 else '',
                    'wa_message_id': str(m.wa_message_id or ''),
                    'quoted_wa_message_id': str(m.quoted_wa_message_id or ''),
                    'quoted_body': str(m.quoted_body or ''),
                    'quoted_sender': str(m.quoted_sender or ''),
                })
            return res
        except Exception as e:
            _logger.error("API get_messages Error: %s", str(e))
            raise

    @http.route('/whatsapp/api/send_message', type='json', auth='user')
    def send_message(
        self, session_id, body, media_b64=None, media_name=None, media_type=None,
        media_is_sticker=False, quoted_message_id=None, quoted_body=None,
        quoted_sender=None, request_id=None, **kwargs
    ):
        """Sends a message via the active session or creates a new one"""
        try:
            session = http.request.env['whatsapp.session'].browse(int(session_id))
            if not session.exists():
                return {'status': 'error', 'message': 'Session not found'}

            _logger.info(
                "WhatsApp send requested for session %s (%s)",
                session.id,
                session.mobile,
            )
            request_key = send_request_id(http.request.env, f'reply:{session.id}', request_id)
            
            session = session.with_context(
                ctx_reply_text=body,
                ctx_send_request_id=request_key,
                ctx_media_b64=media_b64,
                ctx_media_name=media_name,
                ctx_media_type=media_type,
                ctx_media_is_sticker=bool(media_is_sticker),
                ctx_quoted_message_id=quoted_message_id,
                ctx_quoted_body=quoted_body,
                ctx_quoted_sender=quoted_sender,
            )

            success = session.action_send_reply()
            return {'status': 'success' if success else 'failed'}
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception as e:
            _logger.error("send_message Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/forward_message', type='json', auth='user')
    def forward_message(self, wa_message_id, target_session_id, request_id=None, **kwargs):
        try:
            message = http.request.env['whatsapp.session.message'].sudo().search([
                ('wa_message_id', '=', str(wa_message_id)),
            ], limit=1)
            target = http.request.env['whatsapp.session'].sudo().browse(int(target_session_id))
            if not message.exists() or not target.exists():
                return {'status': 'error', 'message': 'Message or target chat not found'}

            source_instance = message.session_id.instance_id
            target_instance = target.instance_id
            if not source_instance or source_instance != target_instance:
                return {'status': 'error', 'message': 'Forwarding is only available within the same WhatsApp account'}
            if source_instance.status != 'ready':
                source_instance.sudo().action_silent_refresh(source_instance.id)
                source_instance = source_instance.sudo().browse(source_instance.id)
            if source_instance.status != 'ready':
                return {'status': 'error', 'message': 'WhatsApp account is not ready'}

            response = requests.post(
                source_instance.api_url.rstrip('/') + '/forward',
                json={
                    'clientId': source_instance.instance_id,
                    'messageId': message.wa_message_id,
                    'chatId': target.whatsapp_id or target.mobile,
                    'requestId': send_request_id(http.request.env, f'forward:{target.id}', request_id),
                },
                timeout=120,
            )
            try:
                response_data = response.json()
            except ValueError:
                response_data = {}
            if not response.ok:
                return {
                    'status': 'error',
                    'message': response_data.get('error') or response.text or 'Forward failed',
                }
            return {'status': 'success'}
        except Exception as e:
            _logger.error("forward_message Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/get_or_create_session', type='json', auth='user')
    def get_or_create_session(self, mobile, name='', instance_id=None, **kwargs):
        """Find an existing WhatsApp session by mobile number, or create one. Returns session info."""
        try:
            # Clean mobile
            mobile = str(mobile).strip().replace('+', '').replace(' ', '')
            session_obj = http.request.env['whatsapp.session'].sudo()
            instance = False
            if instance_id:
                instance = http.request.env['whatsapp.instance'].sudo().browse(int(instance_id))
            if not instance:
                instance = http.request.env['whatsapp.instance'].sudo().search([('status', '=', 'ready')], limit=1)

            session = session_obj._get_or_create_session(
                mobile,
                instance=instance,
                whatsapp_name=name or mobile,
                host_phone=instance.host_phone if instance else False,
            )
            return {
                'status': 'success',
                'session_id': session.id,
                'name': str(session.name or mobile),
                'mobile': str(session.mobile or mobile),
            }
        except Exception as e:
            _logger.error("get_or_create_session Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/add_odoo_contact', type='json', auth='user')
    def add_odoo_contact(self, name, mobile, session_id=None, **kwargs):
        """Create a new contact in Odoo res.partner with the given name and mobile."""
        try:
            mobile = str(mobile).strip()
            # Check if contact already exists
            partner_obj = http.request.env['res.partner']
            existing = partner_obj.search([('mobile', '=', mobile)], limit=1)
            
            partner = existing
            if not existing:
                partner = partner_obj.create({
                    'name': name or mobile,
                    'mobile': mobile,
                    'phone': mobile,
                })
            
            if session_id:
                session = http.request.env['whatsapp.session'].sudo().browse(int(session_id))
                if session.exists():
                    session.write({
                        'partner_id': partner.id,
                        'mobile': mobile
                    })

            status = 'exists' if existing else 'created'
            return {'status': status, 'partner_id': partner.id, 'name': partner.name}
        except Exception as e:
            _logger.error("add_odoo_contact Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/get_unread_notifications', type='json', auth='user')
    def get_unread_notifications(self, **kwargs):
        """Returns recent incoming unread messages for global notifications."""
        _logger.info("WhatsApp Notification Poll Received")
        try:
            # We look for messages in the last 60 seconds to be safe with polling intervals
            date_threshold = fields.Datetime.to_string(fields.Datetime.now() - http.request.env['whatsapp.session.message']._get_notification_delta())
            
            messages = http.request.env['whatsapp.session.message'].search([
                ('msg_type', '=', 'in'),
                ('date', '>', date_threshold),
            ], order='date desc', limit=5)
            
            res = []
            for m in messages:
                res.append({
                    'id': m.id,
                    'session_id': m.session_id.id,
                    'sender_name': m.session_id.name,
                    'body': m.body[:100] if m.body else '', # Snip long messages
                    'time': fields.Datetime.context_timestamp(http.request.env.user, m.date).strftime('%I:%M %p') if m.date else ''
                })
            return res
        except Exception as e:
            _logger.error("get_unread_notifications Error: %s", str(e))
            return []

    @http.route('/whatsapp/api/get_reservation_data', type='json', auth='user')
    def get_reservation_data(self, **kwargs):
        """Returns data needed for the Quick Reserve modal."""
        try:
            env = http.request.env
            products = env['product.product'].sudo().search([('type', '=', 'service')])
            
            specialists = env['hr.employee'].sudo().search([
                ('is_specialist', '=', True),
                ('active', '=', True),
            ])
            rooms = env['spa.room'].sudo().search([])
            time_slots = env['booking.time.slot'].sudo().search([])

            res_products = [{'id': p.id, 'name': p.display_name, 'price': p.list_price} for p in products]
            res_specialists = [{'id': s.id, 'name': s.name} for s in specialists]
            res_rooms = [{'id': r.id, 'name': r.name} for r in rooms]
            res_time_slots = [{'id': t.id, 'name': t.name} for t in time_slots]

            return {
                'status': 'success',
                'products': res_products,
                'specialists': res_specialists,
                'rooms': res_rooms,
                'time_slots': res_time_slots,
            }
        except Exception as e:
            _logger.error("get_reservation_data Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/create_quick_reservation', type='json', auth='user')
    def create_quick_reservation(self, partner_id, product_id, duration, price_unit, booking_date, booking_time_id, room_id=False, specialist_id=False, **kwargs):
        """Creates a Quick Reservation (Sale Order)."""
        try:
            env = http.request.env
            
            order_vals = {
                'partner_id': int(partner_id),
                'is_booking_order': True,
            }
            if specialist_id:
                order_vals['specialist_id'] = int(specialist_id)
                
            order = env['sale.order'].sudo().create(order_vals)
            
            product = env['product.product'].sudo().browse(int(product_id))
            
            line_vals = {
                'order_id': order.id,
                'product_id': product.id,
                'name': product.name,
                'price_unit': float(price_unit),
                'product_uom_qty': 1.0,
                'booking_date': booking_date,
                'booking_time_id': int(booking_time_id),
                'duration': int(duration),
            }
            if room_id:
                line_vals['room_id'] = int(room_id)
                
            env['sale.order.line'].sudo().create(line_vals)
            
            return {
                'status': 'success',
                'order_id': order.id
            }
        except Exception as e:
            _logger.error("create_quick_reservation Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    # === LABELS API ===
    @http.route('/whatsapp/api/get_all_labels', type='json', auth='user')
    def get_all_labels(self, **kwargs):
        try:
            labels = http.request.env['whatsapp.label'].sudo().search([])
            return [{'id': l.id, 'name': l.name, 'color': l.color} for l in labels]
        except Exception as e:
            _logger.error("API get_all_labels Error: %s", str(e))
            return []

    @http.route('/whatsapp/api/create_label', type='json', auth='user')
    def create_label(self, name, color, **kwargs):
        try:
            if not name:
                return {'status': 'error', 'message': 'Name is required'}
            env = http.request.env
            label = env['whatsapp.label'].sudo().create({
                'name': name,
                'color': color or '#8e8e93'
            })
            return {'status': 'success', 'id': label.id, 'name': label.name, 'color': label.color}
        except Exception as e:
            _logger.error("API create_label Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    @http.route('/whatsapp/api/assign_labels', type='json', auth='user')
    def assign_labels(self, session_id, label_ids, **kwargs):
        try:
            session = http.request.env['whatsapp.session'].sudo().browse(int(session_id))
            if session.exists():
                session.write({'label_ids': [(6, 0, label_ids)]})
                return {'status': 'success'}
            return {'status': 'error', 'message': 'Session not found'}
        except Exception as e:
            _logger.error("API assign_labels Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}

    # === FINANCIALS API ===
    @http.route('/whatsapp/api/get_customer_dues', type='json', auth='user')
    def get_customer_dues(self, partner_id, **kwargs):
        try:
            env = http.request.env
            partner = env['res.partner'].sudo().browse(int(partner_id))
            if not partner.exists():
                return {'status': 'error', 'message': 'Customer not found'}
                
            invoices = env['account.move'].sudo().search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted')
            ])
            
            total_invoiced = sum(invoices.mapped('amount_total'))
            remaining_due = sum(invoices.mapped('amount_residual'))
            total_paid = total_invoiced - remaining_due
            
            # Format using company currency
            currency = env.company.currency_id
            
            def format_money(amount):
                return f"{amount:,.2f} {currency.symbol or currency.name}"

            return {
                'status': 'success',
                'total_invoiced': format_money(total_invoiced),
                'total_paid': format_money(total_paid),
                'remaining_due': format_money(remaining_due),
            }
        except Exception as e:
            _logger.error("API get_customer_dues Error: %s", str(e))
            return {'status': 'error', 'message': str(e)}
