# -*- coding: utf-8 -*-

import base64
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.whatsapp_integration.send_safety import send_request_id


class SimplePayrollSlipWhatsApp(models.Model):
    _inherit = 'simple.payroll.slip'

    def _furniture_payroll_whatsapp_phone(self):
        self.ensure_one()
        employee = self.employee_id.sudo()
        return (
            employee.mobile_phone
            or employee.private_phone
            or employee.work_phone
            or False
        )

    def _check_furniture_payroll_whatsapp_access(self):
        allowed_groups = (
            'furniture_mrp.group_furniture_mrp_manager',
            'hr.group_hr_manager',
            'hr_contract.group_hr_contract_manager',
        )
        if not any(self.env.user.has_group(group) for group in allowed_groups):
            raise AccessError(_(
                'إرسال كشوف الرواتب عبر واتساب متاح لمديري الرواتب فقط.'
            ))

    def _validate_furniture_payroll_whatsapp_selection(self):
        if not self:
            raise UserError(_('حدد كشف راتب واحدًا على الأقل.'))
        self._check_furniture_payroll_whatsapp_access()
        cancelled = self.filtered(lambda slip: slip.state == 'cancel')
        if cancelled:
            raise UserError(_(
                'لا يمكن إرسال كشف ملغي. أزل الكشوف الملغية من التحديد.'
            ))
    def action_open_payroll_whatsapp_wizard(self):
        self._validate_furniture_payroll_whatsapp_selection()
        wizard = self.env['furniture.mrp.payroll.whatsapp.wizard'].create({
            'slip_ids': [Command.set(self.ids)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('إرسال كشوف الرواتب عبر واتساب'),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref(
                    'furniture_mrp.view_furniture_payroll_whatsapp_wizard_form'
                ).id,
                'form',
            )],
            'target': 'new',
        }


class FurniturePayrollWhatsAppWizard(models.TransientModel):
    _name = 'furniture.mrp.payroll.whatsapp.wizard'
    _description = 'Send Individual Payroll PDFs via WhatsApp'

    slip_ids = fields.Many2many(
        'simple.payroll.slip', string='كشوف الرواتب', required=True,
    )
    slip_count = fields.Integer(
        string='عدد الكشوف', compute='_compute_summary',
    )
    employee_count = fields.Integer(
        string='عدد الموظفين', compute='_compute_summary',
    )
    period_count = fields.Integer(
        string='عدد الفترات', compute='_compute_summary',
    )
    date_from = fields.Date(
        string='من تاريخ', compute='_compute_summary',
    )
    date_to = fields.Date(
        string='إلى تاريخ', compute='_compute_summary',
    )
    missing_phone_names = fields.Text(
        string='موظفون بدون رقم موبايل', compute='_compute_summary',
    )
    missing_phone_count = fields.Integer(
        string='أرقام ناقصة', compute='_compute_summary',
    )
    whatsapp_ready = fields.Boolean(
        string='واتساب متصل', compute='_compute_summary',
    )
    can_send = fields.Boolean(
        string='جاهز للإرسال', compute='_compute_summary',
    )

    @api.depends('slip_ids', 'slip_ids.employee_id')
    def _compute_summary(self):
        ready_instance = self.env['whatsapp.instance'].sudo().search([
            ('status', '=', 'ready'),
        ], limit=1)
        for wizard in self:
            slips = wizard.slip_ids.exists()
            missing = slips.filtered(
                lambda slip: not slip._furniture_payroll_whatsapp_phone()
            ).mapped('employee_id')
            wizard.slip_count = len(slips)
            wizard.employee_count = len(slips.mapped('employee_id'))
            wizard.period_count = len({
                (slip.date_from, slip.date_to) for slip in slips
            })
            wizard.date_from = min(slips.mapped('date_from')) if slips else False
            wizard.date_to = max(slips.mapped('date_to')) if slips else False
            wizard.missing_phone_count = len(missing)
            wizard.missing_phone_names = '\n'.join(missing.mapped('name'))
            wizard.whatsapp_ready = bool(ready_instance)
            wizard.can_send = bool(slips and not missing and ready_instance)

    def _payroll_pdf_filename(self, slip):
        safe_name = re.sub(
            r'[^\w\-\u0600-\u06ff]+', '-', slip.employee_id.name or 'employee'
        ).strip('-')
        return 'salary-slip-%s-%s-%s.pdf' % (
            safe_name or slip.employee_id.id,
            fields.Date.to_string(slip.date_from),
            fields.Date.to_string(slip.date_to),
        )

    def _normalized_whatsapp_phone(self, raw_phone):
        phone = self.env['whatsapp.session'].sudo()._normalize_mobile(raw_phone)
        if phone and phone.startswith('0020'):
            phone = phone[2:]
        elif phone and phone.startswith('01'):
            phone = '2' + phone
        return phone

    def action_send(self):
        self.ensure_one()
        slips = self.slip_ids.exists().sorted(
            key=lambda slip: (slip.employee_id.name or '', slip.id),
        )
        slips._validate_furniture_payroll_whatsapp_selection()

        missing = slips.filtered(
            lambda slip: not slip._furniture_payroll_whatsapp_phone()
        ).mapped('employee_id')
        if missing:
            raise UserError(_(
                'أضف رقم الموبايل في ملف الموظف أولًا:\n- %s'
            ) % '\n- '.join(missing.mapped('name')))

        instance = self.env['whatsapp.instance'].sudo().search([
            ('status', '=', 'ready'),
        ], limit=1)
        if not instance:
            raise UserError(_(
                'حساب واتساب غير متصل. اربطه من إعدادات واتساب ثم أعد المحاولة.'
            ))

        report = self.env.ref(
            'simple_payroll_salary-2.action_report_simple_payroll_slip'
        )
        session_model = self.env['whatsapp.session'].sudo()
        prepared = []
        # Render and validate every document before the first external send.
        # This avoids a half-sent batch when one PDF or phone is invalid.
        for slip in slips:
            phone = self._normalized_whatsapp_phone(
                slip._furniture_payroll_whatsapp_phone()
            )
            if not phone:
                raise UserError(_(
                    'رقم موبايل الموظف %s غير صالح لواتساب.'
                ) % slip.employee_id.display_name)
            pdf_content, _report_type = (
                self.env['ir.actions.report']
                .sudo()
                .with_company(slip.company_id)
                ._render_qweb_pdf(report.report_name, res_ids=[slip.id])
            )
            prepared.append((
                slip,
                phone,
                base64.b64encode(pdf_content).decode('ascii'),
                self._payroll_pdf_filename(slip),
            ))

        supplied_request_id = self.env.context.get(
            'furniture_payroll_whatsapp_request_id'
        )
        for slip, phone, pdf_b64, filename in prepared:
            session = session_model._get_or_create_session(
                phone,
                instance=instance,
                whatsapp_name=slip.employee_id.name,
                host_phone=instance.host_phone,
            )
            message = _(
                'السلام عليكم %(employee)s،\n'
                'مرفق كشف راتبك عن الفترة من %(date_from)s إلى %(date_to)s.',
                employee=slip.employee_id.name,
                date_from=fields.Date.to_string(slip.date_from),
                date_to=fields.Date.to_string(slip.date_to),
            )
            request_key = send_request_id(
                self.env,
                'payroll-slip:%s' % slip.id,
                supplied=supplied_request_id,
            )
            session.with_context(
                ctx_reply_text=message,
                ctx_send_request_id=request_key,
                ctx_media_b64=pdf_b64,
                ctx_media_name=filename,
                ctx_media_type='application/pdf',
            ).action_send_reply()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم إرسال كشوف الرواتب'),
                'message': _(
                    'تم إرسال %(count)s ملف PDF منفصل إلى %(employees)s موظف.',
                    count=len(slips),
                    employees=len(slips.mapped('employee_id')),
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
