# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_SELECTION,
)


class FurnitureDeliveryRequestStageWarning(models.Model):
    _name = 'furniture.delivery.request.stage.warning'
    _description = 'تحذير مرحلة من طلب التسليم'
    _order = 'stage, id'

    order_id = fields.Many2one(
        'furniture.mrp.future.order',
        string='طلب التسليم',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='order_id.company_id',
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

    def _check_editable(self):
        self.mapped('order_id')._check_delivery_stage_warning_editable()

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['furniture.mrp.future.order'].browse([
            vals['order_id'] for vals in vals_list if vals.get('order_id')
        ]).exists()
        orders._check_delivery_stage_warning_editable()
        return super().create(vals_list)

    def write(self, vals):
        self._check_editable()
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        return super().unlink()


class FurnitureDeliveryRequestStageWarningMixin(models.Model):
    _inherit = 'furniture.mrp.future.order'

    delivery_stage_warning_ids = fields.One2many(
        'furniture.delivery.request.stage.warning',
        'order_id',
        string='تحذيرات أوامر إنتاج العجز',
        copy=False,
    )

    def _check_delivery_stage_warning_editable(self):
        for order in self:
            order._check_manager()
            if order.state != 'pending':
                raise UserError(_(
                    'لا يمكن تعديل التحذيرات بعد اعتماد طلب التسليم.'
                ))

    def action_open_delivery_stage_warning_wizard(self):
        self.ensure_one()
        self._check_delivery_stage_warning_editable()
        view = self.env.ref(
            'furniture_delivery_requests.view_delivery_stage_warning_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('إضافة تحذير لأمر إنتاج العجز'),
            'res_model': 'furniture.delivery.request.stage.warning.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_order_id': self.id,
                'default_stage': FURNITURE_STAGE_SELECTION[0][0],
            },
            **({'view_id': view.id, 'views': [(view.id, 'form')]} if view else {}),
        }

    def _copy_delivery_stage_warnings_to_production(self, production):
        self.ensure_one()
        production.ensure_one()
        route_stages = set(production._required_stage_codes())
        warnings = self.sudo().delivery_stage_warning_ids.filtered(
            lambda warning: warning.active and warning.stage in route_stages
        ).sorted('id')
        values = [{
            'production_id': production.id,
            'stage': warning.stage,
            'message': (warning.message or '').strip(),
        } for warning in warnings if (warning.message or '').strip()]
        if not values:
            return self.env['furniture.mrp.production.stage.warning']
        copied = self.env['furniture.mrp.production.stage.warning'].sudo().create(values)
        production.message_post(body=_(
            '⚠️ تم نقل %(count)s تحذير مرحلة من طلب التسليم %(order)s.'
        ) % {'count': len(copied), 'order': self.display_name})
        return copied

    def action_produce_shortage(self):
        self.ensure_one()
        productions_before = self.production_ids
        result = super().action_produce_shortage()
        new_productions = self.production_ids - productions_before
        for production in new_productions:
            self._copy_delivery_stage_warnings_to_production(production)
        return result


class FurnitureDeliveryRequestStageWarningWizard(models.TransientModel):
    _name = 'furniture.delivery.request.stage.warning.wizard'
    _description = 'إضافة تحذير مرحلة من طلب التسليم'

    order_id = fields.Many2one(
        'furniture.mrp.future.order',
        string='طلب التسليم',
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
        help=(
            'سينتقل هذا النص إلى أوامر إنتاج العجز التي تحتوي هذه المرحلة، '
            'ثم يظهر للمشرف عند قبول التحويل.'
        ),
    )

    def action_add_warning(self):
        self.ensure_one()
        order = self.order_id
        order._check_delivery_stage_warning_editable()
        message = (self.message or '').strip()
        if not message:
            raise UserError(_('اكتب نص التحذير أولًا.'))
        warning = self.env['furniture.delivery.request.stage.warning'].create({
            'order_id': order.id,
            'stage': self.stage,
            'message': message,
        })
        order.message_post(body=_(
            '⚠️ أضاف %(user)s تحذيرًا لمشرف مرحلة %(stage)s '
            'في أوامر إنتاج العجز.'
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
                    'سينتقل تلقائيًا إلى أي أمر إنتاج عجز يحتوي المرحلة.'
                ),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
