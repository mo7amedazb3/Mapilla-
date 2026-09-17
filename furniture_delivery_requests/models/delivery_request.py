from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class DeliveryRequest(models.Model):
    """Own app/access boundary; keep MRP's existing records and stock workflow."""
    _inherit = 'furniture.mrp.future.order'

    delivery_model_ids = fields.Many2many('furniture.product.model', compute='_compute_delivery_models', string='الموديلات')

    @api.depends('line_ids.furniture_model_id')
    def _compute_delivery_models(self):
        for order in self:
            order.delivery_model_ids = order.line_ids.furniture_model_id

    delivery_item_preview = fields.Json(compute='_compute_delivery_item_preview', string='الأصناف')

    @api.depends('line_ids.product_id.display_name', 'line_ids.quantity')
    def _compute_delivery_item_preview(self):
        for order in self:
            order.delivery_item_preview = [
                {'name': line.product_id.display_name, 'quantity': self._qty_label(line.quantity)}
                for line in order.line_ids if line.product_id
            ]

    beneficiary_partner_id = fields.Many2one(required=False)

    note_draft = fields.Text(
        string='ملاحظة جديدة',
        copy=False,
        help='اكتب الملاحظة هنا ثم اضغط إضافة لحفظها في ملاحظات طلب التسليم.',
    )

    def action_add_note(self):
        self.ensure_one()
        self._check_manager()
        if self.state != 'pending':
            raise UserError(_('لا يمكن إضافة ملاحظة بعد اتخاذ قرار الطلب.'))
        note = (self.note_draft or '').strip()
        if not note:
            raise UserError(_('اكتب الملاحظة أولًا ثم اضغط إضافة.'))
        current_notes = (self.notes or '').strip()
        self.write({
            'notes': '\n'.join(filter(None, (current_notes, note))),
            'note_draft': False,
        })
        return False

    def action_delete_note(self, note_index, note_text=None):
        self.ensure_one()
        self._check_manager()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_future_order WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset(['state', 'notes'])
        if self.state != 'pending':
            raise UserError(_('لا يمكن حذف ملاحظة بعد اتخاذ قرار الطلب.'))
        try:
            note_index = int(note_index)
        except (TypeError, ValueError):
            raise UserError(_('الملاحظة المطلوبة غير موجودة.')) from None
        notes = [note.strip() for note in (self.notes or '').split('\n') if note.strip()]
        if note_index < 0 or note_index >= len(notes):
            raise UserError(_('الملاحظة المطلوبة غير موجودة. حدّث الصفحة وحاول مرة أخرى.'))
        if note_text is not None and notes[note_index] != str(note_text).strip():
            raise UserError(_('الملاحظات تغيّرت. حدّث الصفحة وحاول مرة أخرى.'))
        del notes[note_index]
        self.write({'notes': '\n'.join(notes) or False})
        return False

    def action_edit_products_popup(self, model_id):
        self.ensure_one()
        self._check_manager()
        from odoo import Command
        from odoo.exceptions import UserError
        if self.state != 'pending':
            raise UserError(_('لا يمكن تعديل طلب تم اتخاذ قرار فيه.'))
        lines = self.line_ids.filtered(lambda line: line.furniture_model_id.id == model_id)
        if not lines:
            raise UserError(_('الأصناف تغيّرت. حدّث الطلب وحاول مرة أخرى.'))
        wizard = self.env['furniture.delivery.creation.wizard'].create({
            'order_id': self.id, 'original_model_id': model_id,
            'order_write_date': self.write_date, 'model_id': model_id,
            'company_id': self.company_id.id, 'delivery_date': self.delivery_date,
            'specification_mode': self.specification_mode,
            'buyer_partner_id': self.buyer_partner_id.id,
            'beneficiary_partner_id': self.beneficiary_partner_id.id,
            'selected_product_ids': [Command.set(lines.product_id.ids)],
            'line_ids': [Command.create({'product_id': line.product_id.id,
                                        'quantity': line.quantity, 'source_line_id': line.id, **line._delivery_values()}) for line in lines],
        })
        return {'type': 'ir.actions.act_window', 'name': _('تعديل طلب التسليم'),
                'res_model': wizard._name, 'res_id': wizard.id,
                'views': [(self.env.ref('furniture_delivery_requests.view_delivery_creation_wizard').id, 'form')],
                'target': 'new'}

    @api.model
    def get_product_picker_catalog(self, company_id=False):
        """Use the delivery routing rules for both manufactured items and kits."""
        self._check_manager()
        company = self.env['res.company'].browse(company_id) if company_id else self.env.company
        if company not in self.env.companies:
            raise AccessError(_('الشركة غير متاحة للمستخدم الحالي.'))
        products = self.env['product.product'].search([
            ('active', '=', True), ('furniture_dimension_source_product_id', '=', False),
            '|', ('furniture_has_active_normal_recipe', '=', True), ('is_kits', '=', True),
        ], order='name, id')
        draft = self.new({'company_id': company.id})
        lines = self.env['furniture.mrp.future.order.line']
        for product in products:
            lines += lines.new({'order_id': draft, 'product_id': product.id})
        # Computed relations on unsaved lines may contain NewId wrappers.
        # Re-browse their persisted IDs for a stable JSON catalog.
        models = self.env['furniture.product.model'].browse(lines.mapped('available_model_ids').ids)
        models = models.sorted(lambda model: (model.sequence, model.name, model.id))
        return {
            'models': [{'id': model.id, 'name': model.display_name} for model in models],
            'products': [{'id': line.product_id.id, 'name': line.product_id.display_name,
                          'model_ids': line.available_model_ids.ids, 'is_kit': line.is_kit}
                         for line in lines if line.available_model_ids],
        }

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('طلبات التسليم متاحة لمسؤول النظام فقط.'))

    @api.model
    def get_alert_data(self, limit=20):
        # This endpoint is called by every user's systray, before any model
        # reads. Non-admins must get neither the bell nor counts/customer data.
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            return {'can_manage': False, 'pending_total': 0, 'due_soon_count': 0,
                    'orders': [], 'next_pending': False}
        today = fields.Date.context_today(self)
        pending = [('company_id', 'in', self.env.companies.ids), ('state', '=', 'pending')]
        due = pending + [('delivery_date', '<=', fields.Date.add(today, days=15))]
        due_orders = self.search(due, order='delivery_date asc, id asc', limit=max(1, min(int(limit or 20), 100)))
        next_pending = self.search(pending, order='delivery_date asc, id asc', limit=1)
        return {
            'can_manage': True,
            'pending_total': self.search_count(pending),
            'due_soon_count': self.search_count(due),
            'orders': [{
                'id': order.id,
                'name': order.name,
                'delivery_date': fields.Date.to_string(order.delivery_date),
                'delivery_label': order._delivery_alert_label(today),
                'buyer': order.buyer_partner_id.display_name,
                'consumer': order.beneficiary_partner_id.display_name,
                'products': order.product_summary or '',
                'availability_state': order.availability_state,
                'availability_label': order.availability_label or '',
            } for order in due_orders],
            'next_pending': ({'id': next_pending.id,
                              'delivery_label': next_pending._delivery_alert_label(today)}
                             if next_pending else False),
        }

    def _broadcast_alert_refresh(self, company_ids=None):
        company_ids = set(company_ids or self.exists().mapped('company_id').ids)
        company_ids = company_ids or {self.env.company.id}
        admins = self.env.ref('base.group_system').sudo().users.filtered(
            lambda user: user.active and not user.share and user.partner_id
            and company_ids.intersection(user.company_ids.ids)
        )
        for user in admins:
            self.env['bus.bus'].sudo()._sendone(
                user.partner_id, 'furniture_future_order_changed', {'refresh': True},
            )
