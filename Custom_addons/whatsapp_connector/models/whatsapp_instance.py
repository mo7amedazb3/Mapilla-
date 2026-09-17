import base64
from io import BytesIO
import re
import uuid

import qrcode

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import requests
import logging

_logger = logging.getLogger(__name__)
INSTANCE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,63}$')

class WhatsappInstance(models.Model):
    _name = 'whatsapp.instance'
    _description = 'WhatsApp Instance'

    name = fields.Char(string='Instance Name', required=True, default='Primary WhatsApp')
    instance_id = fields.Char(string='Instance ID', required=True, default=lambda self: str(uuid.uuid4()), readonly=True, help='Unique ID for the session (e.g. session1)')
    api_url = fields.Char(string='API Base URL', default='http://127.0.0.1:3000', required=True)
    host_phone = fields.Char(string='Connected Number', readonly=True, help="The phone number logged into this WhatsApp instance.")
    
    _sql_constraints = [
        ('instance_id_unique', 'unique(instance_id)', 'Instance ID must be unique!')
    ]

    status = fields.Selection([
        ('initializing', 'Initializing...'),
        ('qr', 'Scan QR Code'),
        ('authenticated', 'Authenticated (Loading...)'),
        ('ready', 'Connected'),
        ('offline', 'Offline/Error'),
        ('auth_failure', 'Auth Failure'),
        ('offsess', 'Logged Out'),
    ], string='Status', default='offline', readonly=True)
    
    qr_code = fields.Binary(string='QR Code', readonly=True)
    last_check = fields.Datetime(string='Last Sync', readonly=True)
    last_history_sync = fields.Datetime(string='Last Chat Sync', readonly=True)

    def _wipe_local_chats(self):
        self.ensure_one()
        if 'whatsapp.session' not in self.env:
            return 0
        session_model = self.env['whatsapp.session'].sudo()
        domain = [('instance_id', '=', self.id)]
        if self.host_phone:
            domain = ['|', ('instance_id', '=', self.id), ('host_phone', '=', self.host_phone)]
        sessions = session_model.search(domain)
        count = len(sessions)
        if sessions:
            _logger.warning("WA Connector: wiping %s sessions for instance %s", count, self.instance_id)
            sessions.unlink()
        return count

    @api.model
    def _normalize_instance_id(self, value):
        value = (value or '').strip().lower()
        value = re.sub(r'\s+', '-', value)
        value = re.sub(r'[^a-z0-9_-]', '-', value)
        value = re.sub(r'-{2,}', '-', value)
        return value.strip('-_')

    @api.constrains('instance_id')
    def _check_instance_id_format(self):
        for rec in self:
            if not INSTANCE_ID_RE.match(rec.instance_id or ''):
                raise ValidationError(_(
                    "Instance ID must be 3-64 characters and use only lowercase letters, numbers, '-' or '_'."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if 'instance_id' in vals:
                vals['instance_id'] = self._normalize_instance_id(vals['instance_id'])
            normalized_vals_list.append(vals)
        return super().create(normalized_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'instance_id' in vals:
            normalized_instance_id = self._normalize_instance_id(vals['instance_id'])
            for rec in self:
                if rec.instance_id and normalized_instance_id != rec.instance_id:
                    raise UserError(_(
                        "Instance ID cannot be changed after creation because it is tied to the WhatsApp session on the server."
                    ))
            vals['instance_id'] = normalized_instance_id
        return super().write(vals)

    def _sync_history_from_api(self, chat_limit=80, message_limit=25):
        self.ensure_one()
        url = f"{self.api_url.rstrip('/')}/api/v2/sync"
        params = {
            'clientId': self.instance_id,
            'chatLimit': chat_limit,
            'messageLimit': message_limit,
        }
        try:
            response = requests.get(url, params=params, timeout=180)
            response.raise_for_status()
            data = response.json()
            self.env['whatsapp.session.message'].sudo().process_sync_payload(data)
            self.last_history_sync = fields.Datetime.now()
            return True
        except requests.exceptions.HTTPError as e:
            if response.status_code == 503:
                raise UserError(_("The WhatsApp API server is currently unavailable or the session is not fully ready. Please try clicking 'Refresh Status' first to ensure the session is active, then try syncing again."))
            else:
                raise UserError(_("Failed to sync history from API: %s") % str(e))
        except requests.exceptions.RequestException as e:
            raise UserError(_("Could not reach the WhatsApp API server: %s") % str(e))

    @api.model
    def action_silent_refresh(self, instance_id):
        """Silently fetch current status and QR from Node.js API without returning a view reload.
        Returns a dictionary with the NEW status so JS can compare.
        """
        rec = self.browse(instance_id)
        if not rec.exists():
            return {'has_changed': False, 'status': 'offline'}
            
        old_status = rec.status
        status_map = {
            'INITIALIZING': 'initializing',
            'QR': 'qr',
            'AUTHENTICATED': 'authenticated',
            'READY': 'ready',
            'AUTH_FAILURE': 'auth_failure',
            'OFFLINE': 'offline',
            'OFFSESS': 'offsess'
        }

        try:
            # Pass clientId as query param for status
            url = f"{rec.api_url.rstrip('/')}/api/v2/status"
            params = {'clientId': rec.instance_id}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                api_status = status_map.get(data.get('status'), 'offline')
                api_phone = data.get('host_phone')

                rec.status = api_status
                
                if data.get('qr'):
                    qr_str = data['qr']
                    if qr_str.startswith('data:image'):
                        qr_str = qr_str.split(',')[1]
                    else:
                        buffer = BytesIO()
                        image = qrcode.make(qr_str)
                        image.save(buffer, format='PNG')
                        qr_str = base64.b64encode(buffer.getvalue()).decode()
                    rec.qr_code = qr_str
                else:
                    rec.qr_code = False
                
                if api_phone:
                    rec.host_phone = api_phone
                elif api_status in ('qr', 'offsess', 'offline', 'auth_failure', 'initializing'):
                    rec.host_phone = False

                if api_status in ('qr', 'offsess') and old_status not in ('qr', 'offsess'):
                    rec._wipe_local_chats()
                    rec.last_history_sync = False
                
                rec.last_check = fields.Datetime.now()
                if old_status != 'ready' and rec.status == 'ready':
                    try:
                        rec._sync_history_from_api()
                    except Exception as sync_error:
                        _logger.warning("WhatsApp history sync failed for %s: %s", rec.instance_id, sync_error)
            else:
                rec.status = 'offline'
                rec.qr_code = False
        except Exception as e:
            _logger.warning("WhatsApp API Status Check Failed for %s: %s", rec.instance_id, str(e))
            rec.status = 'offline'
            rec.qr_code = False
            
        return {
            'has_changed': old_status != rec.status,
            'status': rec.status
        }

    def action_refresh_status(self):
        """Fetch current status and trigger UI reload (used by explicit UI button)."""
        for rec in self:
            self.action_silent_refresh(rec.id)

        # Reload the view to show the new status immediately
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_init_instance(self):
        """Initialize the instance on the Node.js API"""
        for rec in self:
            if not rec.instance_id:
                raise UserError(_("Instance ID is required before connecting WhatsApp."))
            try:
                url = f"{rec.api_url.rstrip('/')}/api/v2/init"
                requests.post(url, json={'clientId': rec.instance_id}, timeout=10)
                rec.status = 'initializing'
            except Exception as e:
                _logger.error("Failed to init instance %s: %s", rec.instance_id, e)
        return True

    def action_sync_history(self):
        for rec in self:
            rec._sync_history_from_api()
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_logout(self):
        """Trigger logout on Node.js API and delete associated chat history."""
        for rec in self:
            rec._wipe_local_chats()

            rec.status = 'offsess'
            rec.qr_code = False
            rec.host_phone = False
            rec.last_history_sync = False

            try:
                url = f"{rec.api_url.rstrip('/')}/api/v2/logout"
                response = requests.post(url, json={'clientId': rec.instance_id}, timeout=30)
                response.raise_for_status()
            except requests.exceptions.RequestException:
                _logger.warning("Node.js API unreachable during logout for %s", rec.instance_id)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    @api.model
    def get_instance_status(self):
        """Helper for UI polling if needed - modified to support current instance if called with context"""
        instance_id = self.env.context.get('active_id')
        if instance_id:
            inst = self.browse(instance_id)
        else:
            inst = self.search([], limit=1)
            
        if inst:
            inst.action_refresh_status()
            return {
                'status': inst.status,
                'qr_code': inst.qr_code,
            }
        return {'status': 'offline'}
