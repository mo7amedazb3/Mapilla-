from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


def custom_dimension_label(env, product, model, company, values):
    if not any(values):
        return False
    production = env['furniture.mrp.production'].with_company(company or env.company).new({})
    bom = production._find_bom_for_product(product, model)
    if bom and all(abs(value - (bom[field] or 0)) < 0.001 for value, field in zip(
            values, ('furniture_width_cm', 'furniture_depth_cm', 'furniture_height_cm'))):
        return False
    return ' × '.join('%g' % value for value in values)


class DeliveryLineDimensions(models.Model):
    _inherit = 'furniture.mrp.future.order.line'

    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    dimension_label = fields.Char(string='المقاس', compute='_compute_dimension_label')

    def _dimension_values(self):
        self.ensure_one()
        return {name: self[name] for name in ('width_cm', 'depth_cm', 'height_cm')}

    @api.depends('width_cm', 'depth_cm', 'height_cm', 'product_id', 'furniture_model_id', 'order_id.company_id')
    def _compute_dimension_label(self):
        for line in self:
            values = list(line._dimension_values().values())
            line.dimension_label = custom_dimension_label(line.env, line.product_id, line.furniture_model_id, line.order_id.company_id, values)

    @api.constrains('width_cm', 'depth_cm', 'height_cm', 'product_id')
    def _check_delivery_dimensions(self):
        for line in self:
            values = list(line._dimension_values().values())
            if any(values) and any(value <= 0 for value in values):
                raise ValidationError(_('أدخل العرض والعمق والارتفاع بالسنتيمتر، وكل مقاس أكبر من صفر.'))
            if any(values) and line._kit_bom():
                raise ValidationError(_('لتعديل المقاس أضف قطع الطقم كأصناف منفصلة وحدد مقاس كل قطعة.'))

    def write(self, vals):
        if {'width_cm', 'depth_cm', 'height_cm'} & vals.keys():
            if any(line.order_id.state != 'pending' for line in self):
                raise UserError(_('لا يمكن تعديل مقاس طلب تم اتخاذ قرار فيه.'))
        return super().write(vals)

    def _resolved_demand_specs(self, materialize_stock_product=False):
        specs = super()._resolved_demand_specs(materialize_stock_product=materialize_stock_product)
        if not any(self._dimension_values().values()):
            return specs
        self._check_delivery_dimensions()
        for spec in specs:
            spec.update(self._dimension_values())
            # Reuse production's exact dimension identity, without creating an
            # order or a stock product merely to display availability.
            production = self.env['furniture.mrp.production'].new({})
            line = self.env['furniture.mrp.production.line'].new({
                'production_id': production.id,
                'product_id': spec['production_product'].id,
                'furniture_order_model_id': spec['model'].id,
                'bom_id': spec['production_bom'].id,
                **self._dimension_values(),
            })
            if production._get_production_line_dimension_label(line):
                spec['stock_product'] = (
                    production._get_or_create_dimensioned_finished_product_for_line(line)
                    if materialize_stock_product else production._find_dimensioned_product_for_line(line)
                ) or self.env['product.product']
        return specs

    @api.depends('width_cm', 'depth_cm', 'height_cm',
                 'order_id.line_ids.width_cm', 'order_id.line_ids.depth_cm',
                 'order_id.line_ids.height_cm')
    def _compute_stock_availability(self):
        return super()._compute_stock_availability()
