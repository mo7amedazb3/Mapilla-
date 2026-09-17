import math
from odoo import api, Command, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.addons.furniture_mrp.models.mrp_tailoring_material_setup import TAILORING_PIECE_SIZE_SELECTION


def validate_materials(env, values):
    if not values:
        return
    if not isinstance(values, list):
        raise ValidationError(_('تقسيمة الخامات غير صحيحة.'))
    sizes = dict(TAILORING_PIECE_SIZE_SELECTION)
    for item in values:
        if not isinstance(item, dict) or item.get('kind') not in ('fabric', 'takawe'):
            raise ValidationError(_('نوع الخامة غير صحيح.'))
        pid, qty = item.get('product_id'), item.get('quantity')
        if type(pid) is not int or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty <= 0:
            raise ValidationError(_('اختر الخامة وأدخل كمية أكبر من صفر.'))
        count = item.get('piece_count', 1)
        if item['kind'] == 'takawe' and (type(count) is not int or count <= 0):
            raise ValidationError(_('عدد التكاوي يجب أن يكون عددًا صحيحًا أكبر من صفر.'))
        product = env['product.product'].browse(pid).exists()
        if not product or product.furniture_is_finished_product or not product.is_storable or product.furniture_tailoring_material_kind not in ('fabric', 'takawe'):
            raise ValidationError(_('اختر خامة من الأقمشة أو التكاوي.'))
        product.check_access('read')
        if item['kind'] == 'takawe' and item.get('piece_size') not in sizes:
            raise ValidationError(_('حدد مقاس كل تكوي.'))


def _fill_material_summaries(records):
    for line in records:
        sections = {'fabric': [], 'takawe': []}
        for item in line.upholstery_data or []:
            product = records.env['product.product'].browse(item.get('product_id')).exists()
            if not product or item.get('kind') not in sections:
                continue
            description = '%s — %g م' % (product.display_name, item.get('quantity', 0))
            if item['kind'] == 'takawe' and item.get('piece_size'):
                description += ' — %s' % dict(TAILORING_PIECE_SIZE_SELECTION).get(item['piece_size'], item['piece_size'])
            if item['kind'] == 'takawe':
                count = item.get('piece_count', 1)
                description += ' — %s تكوة — %g م' % (count, count * item.get('quantity', 0))
            sections[item['kind']].append(description)
        line.fabric_summary = '\n'.join(sections['fabric']) or _('غير محدد')
        line.takawe_summary = '\n'.join(sections['takawe']) or _('غير محدد')


class DeliveryLineSpecifications(models.Model):
    _inherit = 'furniture.mrp.future.order.line'

    upholstery_data = fields.Json(string='الأقمشة والتكاوي', copy=True)

    fabric_summary = fields.Text(compute='_compute_material_summaries')
    takawe_summary = fields.Text(compute='_compute_material_summaries')

    @api.depends('upholstery_data')
    def _compute_material_summaries(self):
        _fill_material_summaries(self)

    def _delivery_values(self):
        self.ensure_one()
        return {**self._dimension_values(), 'upholstery_data': self.upholstery_data or []}

    @api.constrains('upholstery_data')
    def _check_upholstery_data(self):
        for line in self:
            validate_materials(self.env, line.upholstery_data)

    def write(self, vals):
        if 'upholstery_data' in vals and any(line.order_id.state != 'pending' for line in self):
            raise UserError(_('لا يمكن تعديل خامات طلب تم اتخاذ قرار فيه.'))
        return super().write(vals)


class DeliveryCreationSpecifications(models.TransientModel):
    _inherit = 'furniture.delivery.creation.line'

    upholstery_data = fields.Json(string='الأقمشة والتكاوي')

    fabric_summary = fields.Text(string='الأقمشة والكسوة المختارة', compute='_compute_material_summaries')
    takawe_summary = fields.Text(string='التكاوي المختارة', compute='_compute_material_summaries')

    @api.depends('upholstery_data')
    def _compute_material_summaries(self):
        _fill_material_summaries(self)

    def _delivery_values(self):
        self.ensure_one()
        return {**self._dimension_values(), 'upholstery_data': self.upholstery_data or []}

    @api.constrains('upholstery_data')
    def _check_upholstery_data(self):
        for line in self:
            validate_materials(self.env, line.upholstery_data)

    def action_open_specifications(self):
        self.ensure_one()
        self.env['furniture.mrp.future.order']._check_manager()
        wizard = self.env['furniture.delivery.spec.wizard'].create({
            'delivery_line_id': self.id,
            'source_snapshot': self._delivery_values(),
            **self._dimension_values(),
            'material_line_ids': [Command.create(item) for item in self.upholstery_data or []],
        })
        return {'type': 'ir.actions.act_window', 'name': _('المقاس والأقمشة والتكاوي'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form',
                'views': [(self.env.ref('furniture_delivery_requests.view_delivery_spec_wizard').id, 'form')],
                'target': 'new'}


class DeliverySpecificationWizard(models.TransientModel):
    _name = 'furniture.delivery.spec.wizard'
    _description = 'المقاس وتقسيمة أقمشة وتكاوي صنف التسليم'

    delivery_line_id = fields.Many2one('furniture.delivery.creation.line', ondelete='cascade', readonly=True)
    customer_spec_id = fields.Many2one('furniture.delivery.customer.spec', ondelete='cascade', readonly=True)
    product_id = fields.Many2one('product.product', compute='_compute_target')
    model_id = fields.Many2one('furniture.product.model', compute='_compute_target')

    @api.depends('delivery_line_id.product_id', 'delivery_line_id.wizard_id.model_id', 'customer_spec_id.product_id', 'customer_spec_id.model_id')
    def _compute_target(self):
        for wizard in self:
            wizard.product_id = wizard.customer_spec_id.product_id or wizard.delivery_line_id.product_id
            wizard.model_id = wizard.customer_spec_id.model_id or wizard.delivery_line_id.wizard_id.model_id

    @api.constrains('delivery_line_id', 'customer_spec_id')
    def _check_target(self):
        for wizard in self:
            if bool(wizard.delivery_line_id) == bool(wizard.customer_spec_id):
                raise ValidationError(_('حدد صنف الطلب أو تخصيص المشتري.'))
    source_snapshot = fields.Json(readonly=True)
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    material_line_ids = fields.One2many('furniture.delivery.spec.material', 'wizard_id')
    fabric_line_ids = fields.One2many('furniture.delivery.spec.material', 'fabric_wizard_id')
    takawe_line_ids = fields.One2many('furniture.delivery.spec.material', 'takawe_wizard_id')

    fabric_product_ids = fields.Many2many('product.product', 'delivery_spec_fabric_rel', 'wizard_id', 'product_id', compute='_compute_selected', inverse='_inverse_fabric', readonly=False)
    takawe_product_ids = fields.Many2many('product.product', 'delivery_spec_takawe_rel', 'wizard_id', 'product_id', compute='_compute_selected', inverse='_inverse_takawe', readonly=False)

    @api.depends('fabric_line_ids.product_id', 'takawe_line_ids.product_id')
    def _compute_selected(self):
        for wizard in self:
            wizard.fabric_product_ids = wizard.fabric_line_ids.product_id
            wizard.takawe_product_ids = wizard.takawe_line_ids.product_id

    def _recipe_production_line(self):
        self.ensure_one()
        company = self.customer_spec_id.company_id or self.delivery_line_id.wizard_id.company_id or self.env.company
        production = self.env['furniture.mrp.production'].new({'company_id': company.id})
        bom = production._find_bom_for_product(self.product_id, self.model_id)
        if not bom:
            return False
        line = self.env['furniture.mrp.production.line'].new({
            'production_id': production,
            'product_id': self.product_id.id,
            'furniture_order_model_id': self.model_id.id,
            'bom_id': bom.id, 'product_qty': 1,
            'use_tailoring': bom.use_tailoring,
            'use_upholstery': bom.use_upholstery,
            'width_cm': self.width_cm, 'depth_cm': self.depth_cm, 'height_cm': self.height_cm,
        })
        return line

    def _recipe_fabric_meters(self):
        line = self._recipe_production_line()
        return line._tailoring_recipe_material_qty('fabric') if line else 0.0

    @api.onchange('width_cm', 'depth_cm', 'height_cm')
    def _onchange_recipe_meters(self):
        for wizard in self:
            if wizard.customer_spec_id or wizard.delivery_line_id.wizard_id.specification_mode == 'custom':
                continue
            meters = wizard._recipe_fabric_meters()
            for material in wizard.fabric_line_ids:
                material.quantity = meters

    def _sync_kind(self, kind, products):
        self.ensure_one()
        field_name = kind + '_line_ids'
        lines = self[field_name]
        # Onchange wraps persisted products in NewId(origin=...). Compare their
        # database identities, not recordsets with different origin wrappers.
        product_ids = {product._origin.id or product.id for product in products}
        existing_ids = {line.product_id._origin.id or line.product_id.id for line in lines}
        commands = [Command.delete(line.id) for line in lines
                    if (line.product_id._origin.id or line.product_id.id) not in product_ids]
        commands += [Command.create({'kind': kind, 'product_id': product_id,
                                     'quantity': self._recipe_fabric_meters() if kind == 'fabric' else 1})
                     for product_id in sorted(product_ids - existing_ids)]
        if commands:
            self[field_name] = commands

    def _inverse_fabric(self):
        for wizard in self:
            wizard._sync_kind('fabric', wizard.fabric_product_ids)

    def _inverse_takawe(self):
        for wizard in self:
            wizard._sync_kind('takawe', wizard.takawe_product_ids)

    @api.onchange('fabric_product_ids')
    def _onchange_fabric(self):
        self._sync_kind('fabric', self.fabric_product_ids)

    @api.onchange('takawe_product_ids')
    def _onchange_takawe(self):
        self._sync_kind('takawe', self.takawe_product_ids)

    def action_apply(self):
        self.ensure_one()
        self.env['furniture.mrp.future.order']._check_manager()
        line = (self.customer_spec_id or self.delivery_line_id).exists()
        line.check_access('write')
        if not line or line._delivery_values() != self.source_snapshot:
            raise UserError(_('تفاصيل الصنف تغيّرت. افتح النافذة مرة أخرى.'))
        dims = {key: self[key] for key in ('width_cm', 'depth_cm', 'height_cm')}
        if any(dims.values()) and any(not math.isfinite(v) or v <= 0 for v in dims.values()):
            raise ValidationError(_('أدخل المقاسات الثلاثة أكبر من صفر، أو اتركها كلها صفرًا للمقاس الافتراضي.'))
        items = [{'kind': x.kind, 'product_id': x.product_id.id, 'quantity': x.quantity,
                  'piece_size': x.piece_size or False,
                  **({'piece_count': x.piece_count} if x.kind == 'takawe' else {})} for x in self.material_line_ids]
        validate_materials(self.env, items)
        line.write({**dims, 'upholstery_data': items})
        return {'type': 'ir.actions.act_window_close'}


class DeliverySpecificationMaterial(models.TransientModel):
    _name = 'furniture.delivery.spec.material'
    _description = 'خامة أقمشة أو تكاوي لصنف التسليم'

    wizard_id = fields.Many2one('furniture.delivery.spec.wizard', required=True, ondelete='cascade')
    fabric_wizard_id = fields.Many2one('furniture.delivery.spec.wizard', ondelete='cascade')
    takawe_wizard_id = fields.Many2one('furniture.delivery.spec.wizard', ondelete='cascade')

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for values in vals_list:
            values = dict(values)
            wizard_id = values.get('wizard_id') or values.get('fabric_wizard_id') or values.get('takawe_wizard_id')
            kind = values.get('kind') or ('fabric' if values.get('fabric_wizard_id') else 'takawe')
            values.update(wizard_id=wizard_id, kind=kind)
            values[kind + '_wizard_id'] = wizard_id
            normalized.append(values)
        return super().create(normalized)

    kind = fields.Selection([('fabric', 'الأقمشة والكسوة'), ('takawe', 'التكاوي')], required=True, string='الاستخدام')
    product_id = fields.Many2one('product.product', required=True, string='الخامة')
    quantity = fields.Float(string='الأمتار لكل قطعة', default=1, required=True, digits='Product Unit of Measure')
    piece_count = fields.Integer(string='عدد التكاوي لكل صنف', default=1, required=True)
    total_meters = fields.Float(string='إجمالي الأمتار لكل صنف', compute='_compute_total_meters')

    @api.depends('piece_count', 'quantity')
    def _compute_total_meters(self):
        for line in self:
            line.total_meters = line.quantity * line.piece_count

    piece_size = fields.Selection(TAILORING_PIECE_SIZE_SELECTION, string='مقاس التكاوي')
