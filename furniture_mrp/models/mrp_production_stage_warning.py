# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .mrp_production_order import FURNITURE_STAGE_SELECTION


# The physical supplier stages for each shop-floor department.  This is kept
# server-side so a supervisor cannot forge an arbitrary warning recipient over
# RPC.  Upholstery deliberately has two suppliers: the finished body and the
# tailored cover.
PRODUCTION_WARNING_UPSTREAM_STAGES = {
    'painting': ('priming',),
    'carpentry': ('priming', 'painting'),
    'bases': ('carpentry',),
    'finishing': ('bases',),
    'upholstery': ('finishing', 'tailoring'),
    'packaging': ('upholstery',),
}


class FurnitureMrpProductionStageWarning(models.Model):
    _name = 'furniture.mrp.production.stage.warning'
    _description = 'تنبيه مشرف مرحلة داخل أمر الإنتاج'
    _order = 'stage, id'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='production_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='المرحلة المستهدفة',
        required=True,
        index=True,
    )
    message = fields.Text(string='نص التحذير', required=True)
    active = fields.Boolean(default=True)

    @api.constrains('message')
    def _check_message(self):
        for warning in self:
            if not (warning.message or '').strip():
                raise ValidationError(_('اكتب نص التحذير أولًا.'))

    @api.constrains('production_id', 'stage')
    def _check_stage_in_production_route(self):
        for warning in self:
            if (
                warning.production_id
                and warning.stage not in warning.production_id._required_stage_codes()
            ):
                stage_label = dict(FURNITURE_STAGE_SELECTION).get(
                    warning.stage, warning.stage,
                )
                raise ValidationError(_(
                    'مرحلة %(stage)s غير موجودة في مسار أمر الإنتاج %(order)s.'
                ) % {
                    'stage': stage_label,
                    'order': warning.production_id.display_name,
                })


class FurnitureMrpProductionStageWarningMixin(models.Model):
    _inherit = 'furniture.mrp.production'

    stage_warning_ids = fields.One2many(
        'furniture.mrp.production.stage.warning',
        'production_id',
        string='تنبيهات مشرفي المراحل',
        copy=False,
    )

    def _check_stage_warning_manager(self):
        if not self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        ):
            raise AccessError(_(
                'إضافة تنبيهات مشرفي المراحل متاحة لمدير المصنع فقط.'
            ))

    def action_open_stage_warning_wizard(self):
        self.ensure_one()
        self._check_stage_warning_manager()
        required_stages = self._required_stage_codes()
        if not required_stages:
            raise UserError(_(
                'اختار مراحل أمر الإنتاج أولًا قبل إضافة تحذير للمشرف.'
            ))
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_stage_warning_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('إضافة تحذير لمشرف مرحلة'),
            'res_model': 'furniture.mrp.production.stage.warning.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_production_id': self.id,
                'default_stage': required_stages[0],
            },
            **({'view_id': view.id, 'views': [(view.id, 'form')]} if view else {}),
        }

    def _furniture_stage_acceptance_warning_payload(self, stage_code):
        self.ensure_one()
        warnings = self.sudo().stage_warning_ids.filtered(
            lambda warning: warning.active and warning.stage == stage_code
        ).sorted('id')
        if not warnings:
            return False
        stage_label = dict(FURNITURE_STAGE_SELECTION).get(
            stage_code, stage_code,
        )
        messages = [(warning.message or '').strip() for warning in warnings]
        messages = [message for message in messages if message]
        if not messages:
            return False
        message = (
            messages[0]
            if len(messages) == 1
            else '\n'.join(
                '%s) %s' % (index, text)
                for index, text in enumerate(messages, start=1)
            )
        )
        return {
            'title': _('⚠️ تنبيه إداري — %s') % stage_label,
            'message': message,
            'type': 'warning',
            'sticky': True,
            'play_sound': True,
            'stage_acceptance_warning': True,
            'stage_acceptance_warning_stage': stage_code,
            'stage_acceptance_warning_production_id': self.id,
        }

    def _furniture_notify_stage_acceptance_warning(self, stage_code, user=None):
        self.ensure_one()
        user = user or self.env.user
        payload = self._furniture_stage_acceptance_warning_payload(stage_code)
        users = self.env['res.users'].sudo().browse(user.id).exists()
        if not payload or not users:
            return False
        self.env['furniture.mrp.store.request']._send_bus_notification(
            users,
            payload['title'],
            payload['message'],
            notification_type=payload['type'],
            sticky=True,
            refresh_model=self._name,
            refresh_res_id=self.id,
            play_sound=True,
            extra_payload={
                key: value
                for key, value in payload.items()
                if key.startswith('stage_acceptance_warning')
            },
        )
        return payload


class FurnitureMrpProductionStageWarningWizard(models.TransientModel):
    _name = 'furniture.mrp.production.stage.warning.wizard'
    _description = 'إضافة تحذير لمشرف مرحلة'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='يظهر لمشرف مرحلة',
        required=True,
    )
    message = fields.Text(
        string='نص التحذير',
        required=True,
        help='سيظهر هذا النص لمشرف المرحلة فور نجاح قبول التحويل.',
    )

    def action_add_warning(self):
        self.ensure_one()
        production = self.production_id
        production._check_stage_warning_manager()
        message = (self.message or '').strip()
        if not message:
            raise UserError(_('اكتب نص التحذير أولًا.'))
        required_stages = production._required_stage_codes()
        if self.stage not in required_stages:
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(
                self.stage, self.stage,
            )
            raise UserError(_(
                'مرحلة %(stage)s غير موجودة في مسار أمر الإنتاج %(order)s.'
            ) % {
                'stage': stage_label,
                'order': production.display_name,
            })
        warning = self.env[
            'furniture.mrp.production.stage.warning'
        ].create({
            'production_id': production.id,
            'stage': self.stage,
            'message': message,
        })
        production.message_post(body=_(
            '⚠️ أضاف %(user)s تحذيرًا لمشرف مرحلة %(stage)s.'
        ) % {
            'user': self.env.user.display_name,
            'stage': dict(FURNITURE_STAGE_SELECTION)[warning.stage],
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم حفظ التحذير'),
                'message': _(
                    'سيظهر لمشرف المرحلة فور قبوله تحويل هذا الأمر.'
                ),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class FurnitureMrpProductProductionWarning(models.Model):
    _name = 'furniture.mrp.product.production.warning'
    _description = 'تحذير إنتاج بين مشرفي المراحل'
    _order = 'id desc'

    company_id = fields.Many2one(
        'res.company', required=True, readonly=True, index=True,
    )
    product_id = fields.Many2one(
        'product.product', required=True, readonly=True,
        ondelete='restrict', index=True,
    )
    model_id = fields.Many2one(
        'furniture.product.model', readonly=True,
        ondelete='restrict', index=True,
    )
    source_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True, index=True,
        string='المرحلة المُرسلة',
    )
    target_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True, index=True,
        string='المرحلة المستهدفة',
    )
    message = fields.Text(required=True, readonly=True, string='التحذير')
    reported_by_id = fields.Many2one(
        'res.users', required=True, readonly=True,
        default=lambda self: self.env.user,
    )
    origin_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furn_product_warning_origin_line_rel',
        'warning_id', 'production_line_id',
        readonly=True,
        string='سطور التشغيل الأصلية',
    )
    reminder_consumed = fields.Boolean(default=False, readonly=True, index=True)
    reminder_consumed_at = fields.Datetime(readonly=True)
    reminder_batch_token = fields.Char(readonly=True)
    active = fields.Boolean(default=True)

    @api.constrains('message')
    def _check_warning_message(self):
        for warning in self:
            if not (warning.message or '').strip():
                raise ValidationError(_('اكتب نص تحذير الإنتاج أولًا.'))

    @api.constrains('source_stage', 'target_stage')
    def _check_warning_route(self):
        for warning in self:
            if warning.target_stage not in PRODUCTION_WARNING_UPSTREAM_STAGES.get(
                warning.source_stage, (),
            ):
                raise ValidationError(_(
                    'المرحلة المستهدفة لا تسلّم شغلًا إلى المرحلة الحالية.'
                ))

    @api.model
    def _stage_supervisor_users(self, company, stage_code):
        employees = self.env['hr.employee'].sudo().search([
            ('active', '=', True),
            ('furniture_mrp_role', '=', 'supervisor'),
            ('furniture_mrp_supervisor_stage_ids.code', '=', stage_code),
            ('user_id', '!=', False),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ])
        return employees.mapped('user_id').filtered(lambda user: (
            user.active and not user.share and company in user.company_ids
        ))

    def _popup_payload(self, future_reminder=False):
        self.ensure_one()
        labels = dict(FURNITURE_STAGE_SELECTION)
        heading = (
            _('تذكير من أول تشغيل تالٍ')
            if future_reminder else _('تحذير جديد من مشرف مرحلة')
        )
        return {
            'title': _('⚠️ تحذير إنتاج — %s') % (
                labels.get(self.source_stage, self.source_stage)
            ),
            'message': '%s\n%s — %s\n%s' % (
                heading,
                self.model_id.display_name or _('بدون موديل'),
                self.product_id.display_name,
                (self.message or '').strip(),
            ),
            'production_quality_warning': True,
            'production_warning_id': self.id,
        }

    @api.model
    def _consume_next_for_lines(
        self, stage_code, production_lines, batch_token=False,
    ):
        """Consume matching warnings once, excluding their originating work."""
        production_lines = production_lines.exists()
        if not production_lines:
            return False
        self.env['furniture.mrp.production']._stage_dashboard_check_stage_access(
            stage_code,
        )
        company_ids = production_lines.mapped('production_id.company_id').ids
        product_ids = production_lines.mapped('product_id').ids
        line_models = production_lines.mapped(
            lambda line: (
                line.furniture_order_model_id
                or line.production_id.furniture_order_model_id
            )
        )
        model_ids = line_models.ids
        domain = [
            ('active', '=', True),
            ('reminder_consumed', '=', False),
            ('target_stage', '=', stage_code),
            ('company_id', 'in', company_ids),
            ('product_id', 'in', product_ids),
            '|', ('model_id', '=', False), ('model_id', 'in', model_ids),
        ]
        candidates = self.sudo().search(domain, order='id')
        if not candidates:
            return False
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_product_production_warning '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(candidates.ids)],
        )
        candidates.invalidate_recordset([
            'active', 'reminder_consumed', 'origin_production_line_ids',
        ])
        candidates = candidates.filtered(
            lambda warning: warning.active and not warning.reminder_consumed
        )
        matched = self.browse()
        for warning in candidates:
            matching_lines = production_lines.filtered(lambda line: (
                line.production_id.company_id == warning.company_id
                and line.product_id == warning.product_id
                and (
                    not warning.model_id
                    or (
                        line.furniture_order_model_id
                        or line.production_id.furniture_order_model_id
                    ) == warning.model_id
                )
            ))
            if matching_lines - warning.origin_production_line_ids:
                matched |= warning
        if not matched:
            return False
        matched.sudo().write({
            'reminder_consumed': True,
            'reminder_consumed_at': fields.Datetime.now(),
            'reminder_batch_token': batch_token or False,
        })
        payloads = [warning._popup_payload(future_reminder=True) for warning in matched]
        return {
            'title': payloads[0]['title'],
            'message': '\n\n'.join(payload['message'] for payload in payloads),
            'production_quality_warning': True,
            'production_warning_ids': matched.ids,
        }


class FurnitureMrpProductProductionWarningWizard(models.TransientModel):
    _name = 'furniture.mrp.product.production.warning.wizard'
    _description = 'إرسال تحذير إنتاج لمشرف مرحلة موردة'

    company_id = fields.Many2one('res.company', required=True, readonly=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True)
    model_id = fields.Many2one('furniture.product.model', readonly=True)
    source_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True,
        string='من مرحلة',
    )
    available_target_stage_ids = fields.Many2many(
        'furniture.mrp.employee.stage',
        'furn_product_warning_target_stage_rel',
        'wizard_id',
        'stage_id',
        readonly=True,
    )
    target_stage_id = fields.Many2one(
        'furniture.mrp.employee.stage', required=True,
        string='إرسال إلى',
        domain="[('id', 'in', available_target_stage_ids)]",
    )
    production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furn_product_warning_wizard_line_rel',
        'wizard_id',
        'production_line_id',
        readonly=True,
    )
    message = fields.Text(string='نص التحذير')

    @api.model
    def _action_for_lines(self, production_lines, stage_code):
        production_lines = production_lines.exists()
        self.env[
            'furniture.mrp.production'
        ]._stage_dashboard_check_product_warning_access(stage_code)
        target_codes = PRODUCTION_WARNING_UPSTREAM_STAGES.get(stage_code, ())
        if not target_codes:
            raise UserError(_(
                'هذه المرحلة لا تستلم شغلًا من مرحلة إنتاج سابقة.'
            ))
        if not production_lines:
            raise UserError(_('الصنف لم يعد موجودًا في أمر التشغيل.'))
        products = production_lines.mapped('product_id')
        models = production_lines.mapped(
            lambda line: (
                line.furniture_order_model_id
                or line.production_id.furniture_order_model_id
            )
        )
        companies = production_lines.mapped('production_id.company_id')
        if len(products) != 1 or len(models) > 1 or len(companies) != 1:
            raise ValidationError(_('دفعة الصنف غير متجانسة ولا يمكن إرسال تحذير لها.'))
        target_stages = self.env['furniture.mrp.employee.stage'].search([
            ('code', 'in', target_codes), ('active', '=', True),
        ], order='sequence, id')
        if not target_stages:
            raise UserError(_('مراحل الاستلام السابقة غير مهيأة في النظام.'))
        wizard = self.create({
            'company_id': companies.id,
            'product_id': products.id,
            'model_id': models.id if models else False,
            'source_stage': stage_code,
            'available_target_stage_ids': [(6, 0, target_stages.ids)],
            'target_stage_id': target_stages[0].id,
            'production_line_ids': [(6, 0, production_lines.ids)],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_product_production_warning_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('تحذيرات الإنتاج'),
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
        }

    def action_send_warning(self):
        self.ensure_one()
        self.env[
            'furniture.mrp.production'
        ]._stage_dashboard_check_product_warning_access(self.source_stage)
        target_code = self.target_stage_id.code
        if (
            self.target_stage_id not in self.available_target_stage_ids
            or target_code not in PRODUCTION_WARNING_UPSTREAM_STAGES.get(
                self.source_stage, (),
            )
        ):
            raise AccessError(_('مرحلة استلام التحذير غير مسموح بها.'))
        message = (self.message or '').strip()
        if not message:
            raise UserError(_('اكتب نص تحذير الإنتاج أولًا.'))
        Warning = self.env['furniture.mrp.product.production.warning']
        recipients = Warning._stage_supervisor_users(
            self.company_id, target_code,
        )
        if not recipients:
            raise UserError(_(
                'لا يوجد مستخدم مشرف نشط مربوط بالمرحلة المستهدفة.'
            ))
        warning = Warning.sudo().create({
            'company_id': self.company_id.id,
            'product_id': self.product_id.id,
            'model_id': self.model_id.id,
            'source_stage': self.source_stage,
            'target_stage': target_code,
            'message': message,
            'reported_by_id': self.env.user.id,
            'origin_production_line_ids': [(6, 0, self.production_line_ids.ids)],
        })
        payload = warning._popup_payload()
        self.env['furniture.mrp.store.request']._send_bus_notification(
            recipients,
            payload['title'],
            payload['message'],
            notification_type='warning',
            sticky=True,
            play_sound=True,
            extra_payload={
                'production_quality_warning': True,
                'production_warning_id': warning.id,
            },
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم إرسال تحذير الإنتاج'),
                'message': _(
                    'وصل التحذير للمشرف المستهدف وسيظهر مرة أخرى مع أول تشغيل تالٍ مطابق.'
                ),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class FurnitureMrpProductProductionWarningDashboard(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _stage_dashboard_check_product_warning_access(self, stage_code):
        """Production warnings belong only to the assigned stage supervisor."""
        profile = self._stage_dashboard_check_stage_access(stage_code)
        if not profile['is_supervisor']:
            raise AccessError(_(
                'تحذيرات الإنتاج متاحة لمشرف المرحلة فقط.'
            ))
        return profile

    @api.model
    def action_open_stage_dashboard_product_warning(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self.env['furniture.mrp.stage.product.batch']
        batch, group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=True,
        )
        lines = batch.production_line_ids if batch else group.get('lines')
        return self.env[
            'furniture.mrp.product.production.warning.wizard'
        ]._action_for_lines(lines, stage_code)

    def action_open_stage_dashboard_order_product_warning(
        self, production_line_id=False, stage_code=False,
    ):
        self.ensure_one()
        self._stage_dashboard_check_product_warning_access(stage_code)
        try:
            production_line_id = int(production_line_id or 0)
        except (TypeError, ValueError):
            raise AccessError(_('معرّف الصنف غير صالح.'))
        line = self.production_line_ids.filtered(
            lambda item: item.id == production_line_id
        )
        if not line:
            raise AccessError(_('الصنف لا يخص أمر التشغيل الحالي.'))
        return self.env[
            'furniture.mrp.product.production.warning.wizard'
        ]._action_for_lines(line, stage_code)
