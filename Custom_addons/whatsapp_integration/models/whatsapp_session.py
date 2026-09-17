import base64
import json
import logging
from datetime import datetime, timezone

import requests
from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.exceptions import UserError
from ..send_safety import send_request_id

_logger = logging.getLogger(__name__)

class WhatsappSession(models.Model):
    _name = 'whatsapp.session'
    _description = 'WhatsApp Chat Session'
    _order = 'last_message_date desc, id desc'
    _sql_constraints = [
        (
            'whatsapp_session_instance_mobile_unique',
            'unique(instance_id, mobile)',
            'A WhatsApp chat already exists for this number on the selected account.',
        ),
    ]

    name = fields.Char(string='Contact Name', compute='_compute_name', store=True)
    whatsapp_name = fields.Char(string="WhatsApp Profile Name")
    whatsapp_image = fields.Binary(string="WhatsApp Profile Image")
    whatsapp_profile_pic_url = fields.Char(string="Profile Picture URL")
    mobile = fields.Char(string="Mobile Number", required=True, index=True)
    host_phone = fields.Char(string='Host WhatsApp Number', index=True, help='The connected WhatsApp number that owns this chat')
    whatsapp_id = fields.Char(string="Raw WhatsApp ID")
    partner_id = fields.Many2one('res.partner', string="Contact")
    instance_id = fields.Many2one('whatsapp.instance', string="WhatsApp Instance", ondelete='set null', index=True)
    state = fields.Selection([
        ('active', 'Active'),
        ('closed', 'Closed')
    ], string="Status", default='active')

    last_message_date = fields.Datetime("Last Message Date")
    message_ids = fields.One2many('whatsapp.session.message', 'session_id', string="Messages")
    label_ids = fields.Many2many('whatsapp.label', string="Labels")
    reply_text = fields.Text(string="Type a reply...")

    @api.depends('partner_id', 'mobile', 'whatsapp_name')
    def _compute_name(self):
        for rec in self:
            if rec.partner_id:
                rec.name = rec.partner_id.name
            elif rec.whatsapp_name:
                rec.name = rec.whatsapp_name
            else:
                rec.name = rec.mobile

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for incoming_vals in vals_list:
            vals = dict(incoming_vals)
            if vals.get('mobile'):
                vals['mobile'] = self._normalize_mobile(vals['mobile'])
            if 'mobile' in vals and not vals.get('partner_id'):
                partner = self.env['res.partner'].sudo().search([('mobile', '=', vals['mobile'])], limit=1)
                if not partner:
                    partner = self.env['res.partner'].sudo().search([('phone', '=', vals['mobile'])], limit=1)
                if partner:
                    vals['partner_id'] = partner.id

            existing = self._existing_session_from_vals(vals)
            if existing:
                self._update_session_from_vals(existing, vals)
                records |= existing
                continue

            try:
                with self.env.cr.savepoint():
                    records |= super(WhatsappSession, self).create([vals])
            except IntegrityError:
                existing = self._existing_session_from_vals(vals)
                if not existing:
                    raise
                self._update_session_from_vals(existing, vals)
                records |= existing
        return records

    @api.model
    def _normalize_mobile(self, mobile):
        """Normalize a phone number. Returns False for LIDs/groups (non-real numbers)."""
        if not mobile:
            return False
        mobile_str = str(mobile).replace('@c.us', '').strip()
        # Groups
        if '@g.us' in mobile_str:
            return False
        # LID - WhatsApp internal IDs (very long digit strings not starting with country code pattern)
        # LIDs look like: 5441807462599@lid or just 5441807462599
        if '@lid' in str(mobile):
            return False
        normalized = ''.join(ch for ch in mobile_str.split('@')[0] if ch.isdigit())
        # If the number is 13+ digits and doesn't start with a recognizable country code prefix (like 20, 1, 44...)
        # it's likely a LID
        if normalized and len(normalized) > 13:
            return False
        return normalized or False

    @api.model
    def _is_lid(self, mobile):
        """Returns True if the given ID is a WhatsApp LID (not a real phone number)."""
        if not mobile:
            return False
        m = str(mobile)
        if '@lid' in m:
            return True
        digits = ''.join(ch for ch in m.split('@')[0] if ch.isdigit())
        if digits and len(digits) > 13:
            return True
        return False

    @api.model
    def _mobile_from_display_value(self, value):
        """Extract a phone only when WhatsApp displays it explicitly."""
        text = str(value or '').strip()
        if not text.startswith('+'):
            return False
        digits = ''.join(ch for ch in text if ch.isdigit())
        return digits if 8 <= len(digits) <= 15 else False

    @api.model
    def _existing_session_from_vals(self, vals):
        mobile = self._normalize_mobile(vals.get('mobile'))
        if not mobile:
            return self.browse()

        whatsapp_id = vals.get('whatsapp_id')
        instance_id = vals.get('instance_id')
        host_phone = self._normalize_mobile(vals.get('host_phone'))

        if whatsapp_id and instance_id:
            session = self.sudo().search([
                ('whatsapp_id', '=', whatsapp_id),
                ('instance_id', '=', instance_id),
            ], limit=1)
            if session:
                return session

        if whatsapp_id and host_phone:
            session = self.sudo().search([
                ('whatsapp_id', '=', whatsapp_id),
                ('host_phone', '=', host_phone),
            ], limit=1)
            if session:
                return session

        domain = [('mobile', '=', mobile)]
        if instance_id:
            domain.append(('instance_id', '=', instance_id))
        elif host_phone:
            domain.append(('host_phone', '=', host_phone))
        else:
            domain.append(('instance_id', '=', False))
        return self.sudo().search(domain, limit=1)

    @api.model
    def _update_session_from_vals(self, session, vals):
        update_vals = {}
        if vals.get('mobile') and session.mobile != vals['mobile']:
            update_vals['mobile'] = vals['mobile']
        if vals.get('partner_id') and not session.partner_id:
            update_vals['partner_id'] = vals['partner_id']
        if vals.get('instance_id') and session.instance_id.id != vals.get('instance_id'):
            update_vals['instance_id'] = vals['instance_id']
        if vals.get('whatsapp_name') and vals.get('whatsapp_name') != session.whatsapp_name:
            update_vals['whatsapp_name'] = vals['whatsapp_name']
        if vals.get('host_phone'):
            normalized_host_phone = self._normalize_mobile(vals.get('host_phone'))
            if normalized_host_phone and normalized_host_phone != session.host_phone:
                update_vals['host_phone'] = normalized_host_phone
        if vals.get('whatsapp_id') and vals.get('whatsapp_id') != session.whatsapp_id:
            update_vals['whatsapp_id'] = vals['whatsapp_id']
        if vals.get('state') == 'active' and session.state != 'active':
            update_vals['state'] = 'active'
        if vals.get('last_message_date'):
            incoming_date = vals['last_message_date']
            if not session.last_message_date or incoming_date > session.last_message_date:
                update_vals['last_message_date'] = incoming_date
        if update_vals:
            session.sudo().write(update_vals)
        return session

    @api.model
    def _session_group_key(self, session):
        return (
            session.instance_id.id or 0,
            session.mobile or '',
            session.host_phone or '',
        )

    @api.model
    def _canonical_session_ids(self, domain=None):
        ids = []
        seen = set()
        sessions = self.sudo().search(domain or [], order='last_message_date desc, id desc')
        for session in sessions:
            key = self._session_group_key(session)
            if key in seen:
                continue
            seen.add(key)
            ids.append(session.id)
        return ids

    @api.model
    def action_merge_duplicate_sessions(self, instance_id=False):
        domain = []
        if instance_id:
            domain.append(('instance_id', '=', instance_id))

        sessions = self.sudo().search(domain, order='last_message_date desc, id desc')
        groups = {}
        for session in sessions:
            groups.setdefault(self._session_group_key(session), self.browse())
            groups[self._session_group_key(session)] |= session

        merged_groups = 0
        removed_sessions = 0
        removed_messages = 0
        message_model = self.env['whatsapp.session.message'].sudo()

        for group in groups.values():
            if len(group) <= 1:
                continue

            ordered = group.sorted(key=lambda rec: (rec.last_message_date or fields.Datetime.from_string('1970-01-01 00:00:00'), rec.id), reverse=True)
            primary = ordered[:1]
            duplicates = ordered[1:]
            if not duplicates:
                continue

            latest_date = max((rec.last_message_date for rec in group if rec.last_message_date), default=primary.last_message_date)
            merged_vals = {}
            partner = primary.partner_id or next((rec.partner_id for rec in duplicates if rec.partner_id), False)
            if partner and primary.partner_id != partner:
                merged_vals['partner_id'] = partner.id
            whatsapp_name = primary.whatsapp_name or next((rec.whatsapp_name for rec in duplicates if rec.whatsapp_name), False)
            if whatsapp_name and primary.whatsapp_name != whatsapp_name:
                merged_vals['whatsapp_name'] = whatsapp_name
            whatsapp_id = primary.whatsapp_id or next((rec.whatsapp_id for rec in duplicates if rec.whatsapp_id), False)
            if whatsapp_id and primary.whatsapp_id != whatsapp_id:
                merged_vals['whatsapp_id'] = whatsapp_id
            host_phone = primary.host_phone or next((rec.host_phone for rec in duplicates if rec.host_phone), False)
            if host_phone and primary.host_phone != host_phone:
                merged_vals['host_phone'] = host_phone
            if latest_date and primary.last_message_date != latest_date:
                merged_vals['last_message_date'] = latest_date
            if merged_vals:
                primary.sudo().write(merged_vals)

            duplicate_messages = message_model.search([('session_id', 'in', duplicates.ids)])
            if duplicate_messages:
                duplicate_messages.write({'session_id': primary.id})

            removed_messages += primary._deduplicate_messages()
            removed_sessions += len(duplicates)
            duplicates.unlink()
            merged_groups += 1

        _logger.info(
            "Merged %s duplicate WhatsApp session groups, removed %s sessions and %s duplicate messages",
            merged_groups,
            removed_sessions,
            removed_messages,
        )
        return {
            'merged_groups': merged_groups,
            'removed_sessions': removed_sessions,
            'removed_messages': removed_messages,
        }

    def _deduplicate_messages(self):
        self.ensure_one()
        messages = self.env['whatsapp.session.message'].sudo().search([
            ('session_id', '=', self.id),
        ], order='date asc, id asc')
        seen = set()
        to_remove = self.env['whatsapp.session.message'].browse()

        for message in messages:
            key = message.wa_message_id or (
                message.msg_type,
                fields.Datetime.to_string(message.date) if message.date else '',
                message.body or '',
                message.media_type or '',
                message.media_filename or '',
            )
            if key in seen:
                to_remove |= message
                continue
            seen.add(key)

        count = len(to_remove)
        if to_remove:
            to_remove.unlink()
        return count

    @api.model
    def _profile_image_from_url(self, profile_pic_url):
        if not profile_pic_url:
            return False
        try:
            response = requests.get(profile_pic_url, timeout=10)
            response.raise_for_status()
            if not response.content:
                return False
            return base64.b64encode(response.content)
        except requests.RequestException as exc:
            _logger.warning("Could not download WhatsApp profile image: %s", exc)
            return False

    @api.model
    def _get_or_create_session(self, mobile, instance=False, whatsapp_name=False, host_phone=False, whatsapp_id=False, profile_pic_url=False):
        mobile = self._normalize_mobile(mobile)
        if not mobile and not whatsapp_id:
            return self.browse()

        session = self.sudo().browse()
        base_domain = [('mobile', '=', mobile)]

        if whatsapp_id and instance:
            session = self.sudo().search([
                ('whatsapp_id', '=', whatsapp_id),
                ('instance_id', '=', instance.id),
            ], limit=1)

        if not session and whatsapp_id and host_phone:
            session = self.sudo().search([
                ('whatsapp_id', '=', whatsapp_id),
                ('host_phone', '=', host_phone),
            ], limit=1)

        if not session and whatsapp_id:
            session = self.sudo().search([('whatsapp_id', '=', whatsapp_id)], limit=1)

        if not session and instance:
            session = self.sudo().search(base_domain + [('instance_id', '=', instance.id)], limit=1)

        if not session and host_phone:
            session = self.sudo().search(base_domain + [('host_phone', '=', host_phone)], limit=1)

        if not session:
            session = self.sudo().search(base_domain + [('instance_id', '=', False)], limit=1)

        vals = {
            'mobile': mobile,
            'state': 'active',
        }
        if instance:
            vals['instance_id'] = instance.id
        if whatsapp_name:
            vals['whatsapp_name'] = whatsapp_name
        if host_phone:
            vals['host_phone'] = host_phone
        if whatsapp_id:
            vals['whatsapp_id'] = whatsapp_id
        if profile_pic_url and not session:
            vals['whatsapp_profile_pic_url'] = profile_pic_url
            profile_image = self._profile_image_from_url(profile_pic_url)
            if profile_image:
                vals['whatsapp_image'] = profile_image

        if session:
            update_vals = {}
            if session.state == 'closed':
                update_vals['state'] = 'active'
            if mobile and session.mobile != mobile:
                update_vals['mobile'] = mobile
            if instance and session.instance_id != instance:
                update_vals['instance_id'] = instance.id
            if whatsapp_name and session.whatsapp_name != whatsapp_name:
                update_vals['whatsapp_name'] = whatsapp_name
            if host_phone and session.host_phone != host_phone:
                update_vals['host_phone'] = host_phone
            if whatsapp_id and session.whatsapp_id != whatsapp_id:
                update_vals['whatsapp_id'] = whatsapp_id
            if profile_pic_url and (
                session.whatsapp_profile_pic_url != profile_pic_url or not session.whatsapp_image
            ):
                update_vals['whatsapp_profile_pic_url'] = profile_pic_url
                profile_image = self._profile_image_from_url(profile_pic_url)
                if profile_image:
                    update_vals['whatsapp_image'] = profile_image
            if update_vals:
                session.sudo().write(update_vals)
            return session

        return self.sudo().create(vals)

    def action_send_reply(self):
        self.ensure_one()
        reply_text = self.env.context.get('ctx_reply_text', self.reply_text)
        request_key = self.env.context.get('ctx_send_request_id') or send_request_id(self.env, f'reply:{self.id}')
        media_b64 = self.env.context.get('ctx_media_b64')
        media_name = self.env.context.get('ctx_media_name')
        media_type = self.env.context.get('ctx_media_type')
        media_is_sticker = self.env.context.get('ctx_media_is_sticker')
        quoted_message_id = self.env.context.get('ctx_quoted_message_id')
        quoted_body = self.env.context.get('ctx_quoted_body')
        quoted_sender = self.env.context.get('ctx_quoted_sender')

        if not reply_text and not media_b64:
            return True

        instance = self.instance_id
        if instance and instance.status != 'ready':
            instance.sudo().action_silent_refresh(instance.id)
            instance = instance.sudo().browse(instance.id)
        if not instance and self.host_phone:
            instance = self.env['whatsapp.instance'].sudo().search([
                ('host_phone', '=', self.host_phone),
            ], limit=1)
            if instance and instance.status != 'ready':
                instance.sudo().action_silent_refresh(instance.id)
                instance = instance.sudo().browse(instance.id)
        if not instance:
            instance = self.env['whatsapp.instance'].sudo().search([('status', '=', 'ready')], limit=1)
        if not instance:
            fallback_instance = self.env['whatsapp.instance'].sudo().search([], limit=1)
            if fallback_instance:
                fallback_instance.sudo().action_silent_refresh(fallback_instance.id)
                instance = fallback_instance.sudo().browse(fallback_instance.id)
                if instance.status != 'ready':
                    instance = self.env['whatsapp.instance'].sudo().search([('status', '=', 'ready')], limit=1)
        if instance and instance.status != 'ready':
            raise UserError("WhatsApp instance is connected but not ready yet. Please wait a few seconds and try again.")
        if not instance:
            raise UserError("No connected WhatsApp instance is ready to send replies.")

        api_url = instance.api_url.rstrip('/') + "/send"
        payload = {
            "clientId": instance.instance_id or "default",
            "requestId": request_key,
            "phone": self.whatsapp_id or self.mobile
        }
        if reply_text:
            payload["message"] = reply_text
        if media_b64:
            payload["mediaBase64"] = media_b64
            payload["mediaName"] = media_name
            payload["mediaMimeType"] = media_type
            payload["mediaIsSticker"] = bool(media_is_sticker)
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
            
        try:
            _logger.info(
                "Sending WhatsApp reply via %s to %s using instance %s",
                api_url,
                self.mobile,
                instance.instance_id,
            )
            response = requests.post(api_url, json=payload, timeout=120)
        except requests.exceptions.RequestException as e:
            _logger.error("Error sending WhatsApp reply: %s", e)
            raise UserError(f"WhatsApp reply failed: {e}")

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        if not response.ok:
            error_message = response_data.get('error') or response.text or response.reason or f"HTTP {response.status_code}"
            _logger.error("Error sending WhatsApp reply: %s", error_message)
            raise UserError(f"WhatsApp reply failed: {error_message}")

        msg_vals = {
            'session_id': self.id,
            'msg_type': 'out',
            'body': reply_text or (f'[{media_type}]' if media_type else '[media]'),
            'date': fields.Datetime.now(),
            'wa_message_id': response_data.get('messageId'),
            'quoted_wa_message_id': quoted_message_id or False,
            'quoted_body': quoted_body or False,
            'quoted_sender': quoted_sender or False,
        }
        if media_b64:
            msg_vals.update({
                'media_b64': media_b64,
                'media_filename': media_name,
                'media_mime': media_type,
                'media_type': 'sticker' if media_is_sticker else media_type
            })
        try:
            with self.env.cr.savepoint():
                self.env['whatsapp.session.message'].sudo().create(msg_vals)
        except IntegrityError:
            _logger.info(
                "Skipped duplicate outgoing WhatsApp message %s for session %s",
                response_data.get('messageId'),
                self.id,
            )

        self.write({
            'reply_text': False,
            'last_message_date': fields.Datetime.now(),
            'instance_id': instance.id,
            'host_phone': instance.host_phone or self.host_phone,
        })
        return True

    @api.model
    def action_wipe_instance(self, clientId):
        """Called upon WhatsApp logout for a specific instance"""
        _logger.warning("Wiping WhatsApp data for instance: %s", clientId)
        instance = self.env['whatsapp.instance'].sudo().search([('instance_id', '=', clientId)], limit=1)
        if instance:
            domain = [('instance_id', '=', instance.id)]
            if instance.host_phone:
                domain = ['|', ('instance_id', '=', instance.id), ('host_phone', '=', instance.host_phone)]
            sessions = self.sudo().search(domain)
            sessions.unlink()
        return True

    @api.model
    def action_wipe_all(self):
        """Called upon WhatsApp logout to clear all chat data"""
        _logger.warning("Wiping all WhatsApp sessions and messages")
        self.env['whatsapp.session.message'].sudo().search([]).unlink()
        self.sudo().search([]).unlink()
        return True


class WhatsappSessionMessage(models.Model):
    _name = 'whatsapp.session.message'
    _description = 'WhatsApp Message Bubble'
    _order = 'date asc, id asc'
    _sql_constraints = [
        (
            'whatsapp_session_message_wa_message_id_unique',
            'unique(wa_message_id)',
            'This WhatsApp message already exists.',
        ),
    ]

    session_id = fields.Many2one('whatsapp.session', string="Session", required=True, ondelete='cascade')
    msg_type = fields.Selection([
        ('in', 'Incoming'),
        ('out', 'Outgoing')
    ], string="Type", required=True)
    body = fields.Text(string="Message Body")
    date = fields.Datetime(string="Date", default=fields.Datetime.now)
    wa_message_id = fields.Char(string="WA Message ID", index=True)

    # Media fields
    media_type = fields.Char(string="Media Type")
    media_mime = fields.Char(string="MIME Type")
    media_filename = fields.Char(string="Filename")
    media_b64 = fields.Text(string="Media Base64")
    media_url = fields.Char(string="Media URL")
    vcard_data = fields.Text(string="vCard Data")
    quoted_wa_message_id = fields.Char(string="Quoted WhatsApp Message ID", index=True)
    quoted_body = fields.Text(string="Quoted Message")
    quoted_sender = fields.Char(string="Quoted Sender")

    @api.model
    def _get_notification_delta(self):
        """Returns the timedelta for notification polling (60 seconds for safety)."""
        from datetime import timedelta
        return timedelta(seconds=60)

    @api.model
    def _message_datetime(self, timestamp):
        if not timestamp:
            return fields.Datetime.now()
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return fields.Datetime.now()

    @api.model
    def _message_body(self, data):
        body = data.get('body')
        if body:
            return body
        media_type = data.get('mediaType') or data.get('mediaMime') or data.get('type')
        if media_type:
            media_label = media_type.split('/')[0] if '/' in media_type else media_type
            return f'[{media_label}]'
        return '[media]'

    @api.model
    def _get_instance_from_payload(self, client_id, host_phone=False):
        instance = self.env['whatsapp.instance'].sudo().search([('instance_id', '=', client_id)], limit=1)
        if not instance and client_id == 'default':
            vals = {
                'instance_id': 'default',
                'name': 'Default Account',
            }
            if host_phone:
                vals['host_phone'] = host_phone
            instance = self.env['whatsapp.instance'].sudo().create(vals)
        if instance and host_phone and instance.host_phone != host_phone:
            instance.sudo().write({'host_phone': host_phone})
        return instance

    @api.model
    def _find_existing_message(self, session, data, msg_date, msg_type):
        wa_message_id = data.get('waMessageId')
        if wa_message_id:
            identified = self.sudo().search([('wa_message_id', '=', wa_message_id)], limit=1)
            if identified:
                return identified
        domain = [
            ('session_id', '=', session.id),
            ('msg_type', '=', msg_type),
            ('body', '=', self._message_body(data)),
            ('date', '=', msg_date),
        ]
        if wa_message_id:
            # Upgrade a uniquely matching legacy bubble in place. Never merge
            # distinct identified messages or guess between ambiguous matches.
            legacy = self.sudo().search(domain + [('wa_message_id', '=', False)], limit=2)
            return legacy if len(legacy) == 1 else self.browse()
        return self.sudo().search(domain, limit=1)

    @api.model
    def _ingest_payload_data(self, data, instance=False):
        if not isinstance(data, dict):
            return False

        _logger.info(f"DEBUG INGEST: Start ingesting message: body={data.get('body')} fromMe={data.get('fromMe')}")
        client_id = data.get('clientId') or 'default'
        host_phone = ''.join(ch for ch in str(data.get('hostPhone') or '') if ch.isdigit()) or False
        if not instance:
            instance = self._get_instance_from_payload(client_id, host_phone=host_phone)
        if not instance:
            instance = self._get_instance_from_payload(client_id, host_phone=host_phone)
        if not instance:
            _logger.error("_ingest_payload_data: Received payload for unknown instance: %s", client_id)
            return False

        msg_type = 'out' if (data.get('isFromMe') or data.get('fromMe') or data.get('is_from_me')) else 'in'
        sender_source = data.get('chatId') if msg_type == 'out' else (
            data.get('mobile') or data.get('sender') or data.get('chatId')
        )
        sender_phone = self.env['whatsapp.session']._normalize_mobile(sender_source)
        display_mobile = self.env['whatsapp.session']._mobile_from_display_value(
            data.get('displayPhone') or data.get('senderName') or data.get('name')
        )
        if self.env['whatsapp.session']._is_lid(data.get('chatId')) and display_mobile:
            sender_phone = display_mobile
        elif not sender_phone:
            sender_phone = display_mobile
        normalized_host_phone = self.env['whatsapp.session']._normalize_mobile(host_phone)

        if not sender_phone and not data.get('chatId'):
            return False

        if msg_type == 'out' and normalized_host_phone and sender_phone == normalized_host_phone:
            fallback_phone = self.env['whatsapp.session']._normalize_mobile(data.get('mobile'))
            if fallback_phone and fallback_phone != normalized_host_phone:
                sender_phone = fallback_phone
            else:
                _logger.warning("_ingest_payload_data: Ignoring outgoing webhook resolved to host phone without clear recipient. Data: %s", data)
                return False

        profile_pic_url = data.get('senderImageUrl') or data.get('profilePicUrl') or False
        if normalized_host_phone and sender_phone == normalized_host_phone:
            profile_pic_url = False

        session = self.env['whatsapp.session']._get_or_create_session(
            sender_phone or data.get('mobile') or '',
            instance=instance,
            whatsapp_name=data.get('senderName') or data.get('whatsappName') or data.get('name'),
            host_phone=host_phone or instance.host_phone,
            whatsapp_id=data.get('chatId') or data.get('whatsappId'),
            profile_pic_url=profile_pic_url,
        )

        if not session:
            return False
        
        if session and data.get('lid'):
            session.sudo().write({'lid': data.get('lid')})
        if not session:
            _logger.error("_ingest_payload_data: Could not create session for %s", sender_phone)
            return False

        msg_date = self._message_datetime(data.get('timestamp'))
        existing = self._find_existing_message(session, data, msg_date, msg_type)
        if existing:
            _logger.info("_ingest_payload_data: Message %s already exists.", data.get('waMessageId'))
            media_update = {}
            if data.get('waMessageId') and not existing.wa_message_id:
                media_update['wa_message_id'] = data['waMessageId']
            incoming_body = self._message_body(data)
            if data.get('mediaType') == 'call_log' and existing.body in (False, '', '[media]', '[call_log]'):
                # Repair identified legacy call entries without changing their
                # IDs, dates or direction, or treating them as downloadable media.
                media_update['body'] = incoming_body
            if (
                data.get('mediaType') == 'location'
                and 'google.com/maps' in (incoming_body or '')
                and existing.body != incoming_body
            ):
                # Older bridge versions stored WhatsApp's location thumbnail
                # Base64 in the body. Replace it once the coordinates are known.
                media_update['body'] = incoming_body
            if data.get('mediaB64') and not existing.media_b64:
                media_update['media_b64'] = data.get('mediaB64')
            if data.get('mediaType') and not existing.media_type:
                media_update['media_type'] = data.get('mediaType')
            if data.get('mediaMime') and existing.media_mime in (False, '', 'media'):
                media_update['media_mime'] = data.get('mediaMime')
            if data.get('mediaFilename') and not existing.media_filename:
                media_update['media_filename'] = data.get('mediaFilename')
            if data.get('quotedWaMessageId') and not existing.quoted_wa_message_id:
                media_update['quoted_wa_message_id'] = data.get('quotedWaMessageId')
            if data.get('quotedBody') and not existing.quoted_body:
                media_update['quoted_body'] = data.get('quotedBody')
            if data.get('quotedSender') and not existing.quoted_sender:
                media_update['quoted_sender'] = data.get('quotedSender')
            if media_update:
                existing.sudo().write(media_update)
            if session.last_message_date != msg_date:
                session.sudo().write({'last_message_date': msg_date})
            return True

        vals = {
            'session_id': session.id,
            'msg_type': msg_type,
            'body': self._message_body(data),
            'wa_message_id': data.get('waMessageId'),
            'date': msg_date,
            'media_type': data.get('mediaType') or False,
            'media_mime': data.get('mediaMime') or False,
            'media_filename': data.get('mediaFilename') or False,
            'media_b64': data.get('mediaB64') or data.get('mediaData') or False,
            'media_url': data.get('mediaUrl') or False,
            'vcard_data': data.get('vcardData') or False,
            'quoted_wa_message_id': data.get('quotedWaMessageId') or False,
            'quoted_body': data.get('quotedBody') or False,
            'quoted_sender': data.get('quotedSender') or False,
        }
        try:
            with self.env.cr.savepoint():
                self.sudo().create(vals)
        except IntegrityError:
            _logger.info(
                "Skipped duplicate webhook/sync message %s for session %s",
                data.get('waMessageId'),
                session.id,
            )
        session.sudo().write({
            'last_message_date': msg_date,
            'host_phone': host_phone or session.host_phone or instance.host_phone,
            'instance_id': instance.id,
        })
        return True

    @api.model
    def process_incoming_webhook(self, payload_str):
        """Processes incoming messages for multi-session support."""
        try:
            data = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except Exception as e:
            _logger.error(f"Failed to decode incoming Webhook JSON: {e}")
            return False
        _logger.info("process_incoming_webhook payload: %s", data)
        res = self._ingest_payload_data(data)
        _logger.info("_ingest_payload_data result: %s", res)
        return res

    @api.model
    def process_sync_payload(self, payload_str):
        """Imports recent chats and messages for a connected instance."""
        try:
            payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except Exception as e:
            _logger.error("Failed to decode sync payload: %s", e)
            return False

        chats = payload.get('chats') or []
        client_id = payload.get('clientId') or 'default'
        host_phone = ''.join(ch for ch in str(payload.get('hostPhone') or '') if ch.isdigit()) or False
        instance = self._get_instance_from_payload(client_id, host_phone=host_phone)
        if not instance:
            _logger.error("Sync payload rejected: unknown instance %s", client_id)
            return False

        imported = 0
        session_model = self.env['whatsapp.session']
        for chat in chats:
            raw_mobile = chat.get('mobile') or chat.get('chatId') or ''
            chat_id = chat.get('chatId') or ''

            # Skip groups
            if '@g.us' in chat_id:
                _logger.info("Skipping group chat: %s", chat_id)
                continue

            # Normalize the mobile - returns False for LIDs/groups
            mobile = session_model._normalize_mobile(raw_mobile)
            is_lid = session_model._is_lid(raw_mobile) or session_model._is_lid(chat_id)

            if is_lid:
                display_mobile = session_model._mobile_from_display_value(
                    chat.get('displayPhone') or chat.get('name')
                )
                if display_mobile:
                    mobile = display_mobile
                # LID chat: check if server.js resolved it to a real number
                # raw_mobile here will still be the LID if not resolved, or real number if resolved
                if mobile:
                    # server.js successfully resolved the LID to a real phone number
                    _logger.info("LID resolved by server.js: %s -> %s", chat_id, mobile)
                else:
                    # LID not resolved - look for existing session by whatsapp_id
                    existing = session_model.search([
                        ('whatsapp_id', '=', chat_id),
                        ('instance_id', '=', instance.id),
                    ], limit=1)
                    if existing and existing.mobile:
                        session = existing
                        mobile = existing.mobile
                    else:
                        # Cannot store LID without a real phone number - skip
                        _logger.warning("Skipping unresolved LID chat: %s (name: %s)", chat_id, chat.get('name'))
                        continue
            else:
                if not mobile:
                    _logger.warning("Skipping chat with no valid mobile: chatId=%s mobile=%s", chat_id, raw_mobile)
                    continue

            if not mobile:
                continue

            session = session_model._get_or_create_session(
                mobile,
                instance=instance,
                whatsapp_name=chat.get('whatsappName') or chat.get('senderName') or chat.get('name'),
                host_phone=host_phone or instance.host_phone,
                whatsapp_id=chat_id,
                profile_pic_url=chat.get('profilePicUrl') or False,
            )

            for msg in chat.get('messages', []):
                _logger.info("DEBUG SYNC MSG: chat=%s fromMe=%s body=%s", chat_id, msg.get('fromMe'), msg.get('body'))
                message_payload = {
                    'clientId': client_id,
                    'hostPhone': host_phone or instance.host_phone,
                    'mobile': mobile,
                    'chatId': chat_id,
                    'senderName': chat.get('whatsappName') or chat.get('senderName') or chat.get('name'),
                    'body': msg.get('body'),
                    'timestamp': msg.get('timestamp'),
                    'fromMe': msg.get('fromMe'),
                    'waMessageId': msg.get('waMessageId'),
                    'mediaType': msg.get('mediaType'),
                    'mediaMime': msg.get('mediaMime'),
                    'mediaFilename': msg.get('mediaFilename'),
                    'mediaB64': msg.get('mediaB64'),
                }
                if self._ingest_payload_data(message_payload, instance=instance):
                    imported += 1

            if chat.get('timestamp'):
                session.sudo().write({'last_message_date': self._message_datetime(chat.get('timestamp'))})

        _logger.info("Imported %s WhatsApp messages for instance %s", imported, client_id)
        return True
