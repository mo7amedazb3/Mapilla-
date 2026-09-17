from .delivery_dimensions import custom_dimension_label
from odoo import api, Command, fields, models, _
from odoo.exceptions import ValidationError


class DeliveryCreationWizard(models.TransientModel):
    _name = 'furniture.delivery.creation.wizard'
    _description = 'إنشاء طلب تسليم'

    order_id = fields.Many2one('furniture.mrp.future.order', readonly=True)
    original_model_id = fields.Many2one('furniture.product.model', readonly=True)
    order_write_date = fields.Datetime(readonly=True)

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    delivery_date = fields.Date(required=True, string='موعد التسليم')
    buyer_partner_id = fields.Many2one('res.partner', required=True, domain=[('is_company', '=', True)], string='المشتري (الشركة)')
    beneficiary_partner_id = fields.Many2one('res.partner', string='المستهلك')
    model_id = fields.Many2one('furniture.product.model', required=True, string='الموديل')
    available_model_ids = fields.Many2many('furniture.product.model', compute='_compute_choices')
    available_product_ids = fields.Many2many('product.product', compute='_compute_choices')
    selected_product_ids = fields.Many2many('product.product', string='المنتجات النهائية')
    line_ids = fields.One2many('furniture.delivery.creation.line', 'wizard_id', string='الكميات')

    @api.depends('model_id', 'company_id')
    def _compute_choices(self):
        for wizard in self:
            catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(wizard.company_id.id)
            wizard.available_model_ids = [Command.set([model['id'] for model in catalog['models']])]
            wizard.available_product_ids = [Command.set([
                product['id'] for product in catalog['products'] if (wizard.model_id._origin.id or wizard.model_id.id) in product['model_ids']
            ])]

    @api.onchange('model_id', 'company_id')
    def _onchange_model(self):
        self.selected_product_ids = False
        self.line_ids = [Command.clear()]

    @api.onchange('selected_product_ids')
    def _onchange_products(self):
        commands = [Command.clear()]
        for product in self.selected_product_ids:
            existing = self.line_ids.filtered(lambda line: (line.product_id._origin.id or line.product_id.id) == (product._origin.id or product.id))
            commands += [Command.create({'product_id': product.id, 'quantity': line.quantity,
                                         'source_line_id': line.source_line_id.id, **line._delivery_values()}) for line in existing] if existing else [Command.create({'product_id': product.id, 'quantity': 1})]
        self.line_ids = commands

    @api.model
    def action_edit_inline_draft(self, values):
        self.env['furniture.mrp.future.order']._check_manager()
        company = values.get('company_id') or self.env.company.id
        catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(company)
        if not any(p['id'] == values.get('product_id') and values.get('model_id') in p['model_ids'] for p in catalog['products']):
            raise ValidationError(_('الصنف غير متاح لهذا الموديل.'))
        draft = self.create({
            'company_id': company, 'buyer_partner_id': values.get('buyer_partner_id') or self.env.user.partner_id.id,
            'model_id': values['model_id'], 'delivery_date': fields.Date.today(),
            'line_ids': [Command.create({'product_id': values['product_id'], 'quantity': 1,
                **{key: values.get(key) or 0 for key in ('width_cm', 'depth_cm', 'height_cm')},
                'upholstery_data': values.get('upholstery_data') or []})],
        })
        action = draft.line_ids.action_open_specifications()
        action['draft_line_id'] = draft.line_ids.id
        action['context'] = dict(self.env.context, company_standard_materials_only=True)
        return action

    def action_create_delivery(self):
        self.ensure_one()
        Order = self.env['furniture.mrp.future.order']
        Order._check_manager()
        if not self.line_ids or set(self.line_ids.product_id.ids) != set(self.selected_product_ids.ids):
            raise ValidationError(_('اختار المنتجات وحدّد الكميات المطلوبة.'))
        if self.selected_product_ids - self.available_product_ids:
            raise ValidationError(_('بعض الأصناف غير متاحة للموديل المختار. أعد اختيار المنتجات.'))
        if any(line.quantity <= 0 for line in self.line_ids):
            raise ValidationError(_('كمية كل صنف يجب أن تكون أكبر من صفر.'))
        if self.order_id:
            order = self.order_id
            order._lock_pending_order()
            order.invalidate_recordset(['write_date'])
            if order.write_date != self.order_write_date:
                raise ValidationError(_('الطلب اتعدل بعد فتح النافذة. اقفلها وافتحها مرة أخرى لعرض أحدث البيانات.'))
            scope = order.line_ids.filtered(lambda line: line.furniture_model_id == self.original_model_id)
            if self.line_ids.source_line_id - scope:
                raise ValidationError(_('أصناف التعديل غير مطابقة للطلب.'))
            commands = [Command.delete(line.id) for line in scope - self.line_ids.source_line_id]
            for line in self.line_ids:
                values = {'product_id': line.product_id.id, 'furniture_model_id': self.model_id.id, 'quantity': line.quantity, **line._delivery_values()}
                commands.append(Command.update(line.source_line_id.id, values) if line.source_line_id else Command.create(values))
            order.write({'delivery_date': self.delivery_date,
                         'buyer_partner_id': self.buyer_partner_id.id,
                         'beneficiary_partner_id': self.beneficiary_partner_id.id,
                         'line_ids': commands})
            return {'type': 'ir.actions.act_window_close'}
        order = Order.create({
            'company_id': self.company_id.id, 'delivery_date': self.delivery_date,
            'buyer_partner_id': self.buyer_partner_id.id,
            'beneficiary_partner_id': self.beneficiary_partner_id.id,
            'line_ids': [Command.create({'product_id': line.product_id.id,
                                       'furniture_model_id': self.model_id.id,
                                       'quantity': line.quantity, **line._delivery_values()}) for line in self.line_ids],
        })
        return {'type': 'ir.actions.act_window', 'name': order.name,
                'res_model': Order._name, 'res_id': order.id,
                'views': [(False, 'form')], 'target': 'current'}


class DeliveryCreationLine(models.TransientModel):
    _name = 'furniture.delivery.creation.line'
    _description = 'صنف طلب التسليم الجديد'
    wizard_id = fields.Many2one('furniture.delivery.creation.wizard', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='المنتج النهائي')
    source_line_id = fields.Many2one('furniture.mrp.future.order.line', readonly=True, ondelete='set null')
    quantity = fields.Float(required=True, default=1, string='الكمية', digits='Product Unit of Measure')

    dimensions_expanded = fields.Boolean(string='المقاس')
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    dimension_label = fields.Char(compute='_compute_dimension_label')

    def _dimension_values(self):
        self.ensure_one()
        return {name: self[name] for name in ('width_cm', 'depth_cm', 'height_cm')}

    @api.depends('width_cm', 'depth_cm', 'height_cm', 'product_id', 'wizard_id.model_id', 'wizard_id.company_id')
    def _compute_dimension_label(self):
        for line in self:
            values = list(line._dimension_values().values())
            line.dimension_label = custom_dimension_label(line.env, line.product_id, line.wizard_id.model_id, line.wizard_id.company_id, values)
