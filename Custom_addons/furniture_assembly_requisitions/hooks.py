from collections import Counter

from odoo.exceptions import ValidationError


PRODUCTS = {
    'nail': 'مسمار 4 سم هواء',
    'glue': 'غراء',
    'sandpaper': 'فرخ سنفره 36 مقاس صغير',
}


def post_init_hook(env):
    """Bind existing recipes; never create products, stock or production orders."""
    source = env.ref('stock.stock_location_stock')
    hall = env.ref('furniture_mrp.location_stage_carpentry_wip')
    company = hall.company_id or source.company_id or env.company
    Config = env['furniture.assembly.supply.config'].sudo()
    if Config.search_count([('company_id', '=', company.id)]):
        return
    values = {'company_id': company.id, 'source_id': source.id, 'hall_id': hall.id}
    for key, name in PRODUCTS.items():
        product = env['product.product'].with_context(lang='en_US').search([
            ('name', '=', name), ('company_id', 'in', [False, company.id]),
        ])
        if len(product) != 1 or not product.is_storable:
            raise ValidationError('راجع الصنف المخزني الثابت قبل التفعيل: %s' % name)
        recipes = env['furniture.mrp.bom.stage.line'].search([
            ('stage', '=', 'carpentry'), ('product_id', '=', product.id),
        ])
        # Count recipe rows, not deduplicated mapped(recordset) units.
        units = Counter(row.product_uom_id.id for row in recipes if row.product_uom_id)
        ranked = units.most_common()
        if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
            raise ValidationError('وحدة الوصفة غير محددة للصنف: %s' % name)
        values[key + '_product_id'] = product.id
        values[key + '_uom_id'] = ranked[0][0]
    Config.create(values)
