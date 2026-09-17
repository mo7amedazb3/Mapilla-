import urllib.parse
import requests
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..send_safety import send_request_id

_logger = logging.getLogger(__name__)

class WhatsappMsg(models.Model):
    _name = 'whatsapp.msg'
    _description = 'WhatsApp Message'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Recipient', required=True)
    mobile = fields.Char(string='Mobile Number')
    message = fields.Text(string='Message', required=True)
    state = fields.Selection([
        ('draft', 'Draft (Pending)'),
        ('sent', 'Sent')
    ], string='Status', default='draft', readonly=True)
    res_model = fields.Char(string='Related Document Model')
    res_id = fields.Integer(string='Related Document ID')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('whatsapp.msg') or _('New')
        return super(WhatsappMsg, self).create(vals_list)

    def action_send_whatsapp(self):
        """
        Creates a WhatsApp Web link and marks the message as sent.
        For fully automated (background API) sending, you would replace this
        logic with requests module to hit your WhatsApp provider API.
        """
        self.ensure_one()
        mobile_to_use = self.mobile or self.partner_id.mobile
        if not mobile_to_use:
            raise UserError(_("The recipient does not have a mobile number saved!"))

        # Clean the mobile number
        mobile = ''.join(filter(str.isdigit, mobile_to_use))
        
        # Ensure country code (e.g. Egypt 20)
        # Note: Depending on how numbers are saved, you might need stronger logic here.
        if not mobile.startswith('+'):
             if mobile.startswith('01'):
                 mobile = '2' + mobile # Assuming Egyptian numbers as default if local format
        
        # Format URL properly
        encoded_message = urllib.parse.quote(self.message)
        whatsapp_url = f"https://web.whatsapp.com/send?phone={mobile}&text={encoded_message}"
        
        self.state = 'sent'
        
        # Log a note in the related document if applicable
        if self.res_model and self.res_id:
            try:
                record = self.env[self.res_model].browse(self.res_id)
                if hasattr(record, 'message_post'):
                    record.message_post(body=_(f"WhatsApp message sent manually: {self.message}"))
            except Exception:
                pass
        
        return {
            'type': 'ir.actions.act_url',
            'url': whatsapp_url,
            'target': 'new',
        }

    def action_send_via_api(self):
        """
        Connects to the local Node.js API (SkylineHub WhatsApp API) to send the message invisibly.
        Uses the first available ready WhatsApp instance.
        """
        instance = None
        if 'whatsapp.instance' in self.env:
            instance = self.env['whatsapp.instance'].sudo().search(
                [('status', '=', 'ready')], limit=1
            )
        
        if not instance:
            raise UserError(_("No connected WhatsApp session found. Please connect a WhatsApp account first from the WA Connector menu."))
        
        api_base = instance.api_url.rstrip('/')
        client_id = instance.instance_id

        for record in self:
            if record.state == 'sent':
                continue
                
            mobile_to_use = record.mobile or record.partner_id.mobile
            if not mobile_to_use:
                continue

            # Clean and ensure country code
            mobile = ''.join(filter(str.isdigit, mobile_to_use))
            if not mobile.startswith('+'):
                if mobile.startswith('01'):
                    mobile = '2' + mobile
                    
            api_url = f"{api_base}/send"
            payload = {
                "clientId": client_id,
                "requestId": send_request_id(self.env, 'message', f'message-record:{record.id}'),
                "phone": mobile,
                "message": record.message
            }
            
            try:
                response = requests.post(api_url, json=payload, timeout=15)
                if response.status_code == 200:
                    record.state = 'sent'
                    # Log a note
                    if record.res_model and record.res_id:
                        try:
                            parent = self.env[record.res_model].browse(record.res_id)
                            if hasattr(parent, 'message_post'):
                                parent.message_post(body=_(f"WhatsApp message auto-sent via API: {record.message}"))
                        except Exception:
                            pass
                    
                    # Also append this message to the chat inbox tied to the active instance.
                    try:
                        session_model = self.env['whatsapp.session']
                        session = session_model._get_or_create_session(
                            mobile,
                            instance=instance,
                            host_phone=instance.host_phone,
                        )
                        
                        self.env['whatsapp.session.message'].create({
                            'session_id': session.id,
                            'msg_type': 'out',
                            'body': record.message,
                            'date': fields.Datetime.now(),
                        })
                        session.last_message_date = fields.Datetime.now()
                    except Exception as e:
                        _logger.error(f"Could not append Pending Message to Chat Session: {e}")
                        
                else:
                    # Could log an error or raise UserError if triggered manually
                    error_data = response.json()
                    raise UserError(_(f"Node API Error: {error_data.get('error', 'Unknown Error')}"))
            except requests.exceptions.RequestException as e:
                raise UserError(_(f"Could not connect to SkylineHub WhatsApp API server.\nEnsure the Node.js app is running.\nError details: {e}"))
