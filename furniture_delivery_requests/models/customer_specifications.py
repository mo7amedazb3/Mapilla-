import copy
import math
from odoo import api, Command, fields, models, _
from odoo.exceptions import ValidationError
from .delivery_specifications import validate_materials, _fill_material_summaries


class DeliveryCustomer(models.Model):
    _inherit = 'res.partner'

    is_delivery_standard_company = fields.Boolean(
        string='إضافة إلى استاندرد الشركات',
        copy=False,
        help='عند التفعيل تظهر جهة الاتصال كشركة داخل تطبيق استاندرد شركات.',
    )
    delivery_custom_ids = fields.One2many('furniture.delivery.customer.spec', 'partner_id', string='تخصيصات التسليم', groups='base.group_system')

    @api.onchange('is_delivery_standard_company')
    def _onchange_is_delivery_standard_company(self):
        if self.is_delivery_standard_company:
            self.is_company = True

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for incoming in vals_list:
            vals = dict(incoming)
            if vals.get('is_delivery_standard_company'):
                vals['is_company'] = True
            normalized.append(vals)
        return super().create(normalized)

    def write(self, vals):
        values = dict(vals)
        if values.get('is_delivery_standard_company'):
            values['is_company'] = True
        return super().write(values)


class DeliveryCustomerSpecification(models.Model):
    _name = 'furniture.delivery.customer.spec'
    _description = 'تخصيص تسليم المشتري'
    _rec_name = 'product_id'
    _order = 'model_id, product_id, id'
    _check_company_auto = True

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    model_id = fields.Many2one('furniture.product.model', required=True, string='الموديل')
    product_id = fields.Many2one('product.product', required=True, string='الصنف', check_company=True)
    available_product_ids = fields.Many2many('product.product', compute='_compute_available_products')
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    upholstery_data = fields.Json(default=list)
    dimensions_expanded = fields.Boolean()
    fabric_summary = fields.Text(compute='_compute_summaries')
    takawe_summary = fields.Text(compute='_compute_summaries')
    dimension_label = fields.Char(compute='_compute_dimension_label', string='المقاس')

    _sql_constraints = [('buyer_model_product_unique', 'unique(partner_id, company_id, model_id, product_id)',
                         'يوجد تخصيص لهذا المشتري والموديل والصنف بالفعل. عدّل التخصيص الموجود.')]

    @api.depends('model_id', 'company_id')
    def _compute_available_products(self):
        for spec in self:
            catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(spec.company_id.id)
            spec.available_product_ids = [Command.set([p['id'] for p in catalog['products']
                if (spec.model_id._origin.id or spec.model_id.id) in p['model_ids'] and not p['is_kit']])]

    @api.onchange('model_id')
    def _onchange_model(self):
        if self.product_id not in self.available_product_ids:
            self.product_id = False

    @api.onchange('product_id')
    def _onchange_product(self):
        self.update({'width_cm': 0, 'depth_cm': 0, 'height_cm': 0, 'upholstery_data': []})

    def _dimension_values(self):
        self.ensure_one()
        return {key: self[key] for key in ('width_cm', 'depth_cm', 'height_cm')}

    def _delivery_values(self):
        self.ensure_one()
        return {'width_cm': 0, 'depth_cm': 0, 'height_cm': 0,
                'upholstery_data': copy.deepcopy(self.upholstery_data or [])}

    @api.depends('upholstery_data')
    def _compute_summaries(self):
        _fill_material_summaries(self)

    @api.depends('width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_label(self):
        for spec in self:
            dims = list(spec._dimension_values().values())
            spec.dimension_label = ' × '.join('%g' % v for v in dims) + ' سم' if any(dims) else _('الافتراضي')

    @api.constrains('product_id', 'model_id', 'company_id', 'width_cm', 'depth_cm', 'height_cm', 'upholstery_data')
    def _check_spec(self):
        for spec in self:
            if (spec.product_id._origin.id or spec.product_id.id) not in {
                    p._origin.id or p.id for p in spec.available_product_ids}:
                raise ValidationError(_('اختر صنفًا له ريسيبي في الموديل المحدد. تخصيص الطقم يكون على قطعه.'))
            dims = list(spec._dimension_values().values())
            if any(not math.isfinite(v) for v in dims) or (any(dims) and any(v <= 0 for v in dims)):
                raise ValidationError(_('أدخل المقاسات الثلاثة أكبر من صفر أو اتركها كلها صفرًا للمقاس الافتراضي.'))
            validate_materials(self.env, spec.upholstery_data)

    def action_open_specifications(self):
        self.ensure_one()
        self.check_access('write')
        wizard = self.env['furniture.delivery.spec.wizard'].create({
            'customer_spec_id': self.id, 'source_snapshot': self._delivery_values(),
            'width_cm': 0, 'depth_cm': 0, 'height_cm': 0, 'material_line_ids': [Command.create(v) for v in self.upholstery_data or []],
        })
        return {'type': 'ir.actions.act_window', 'name': _('تخصيص الأقمشة والتكاوي'),
                'context': {**self.env.context, 'company_standard_materials_only': True},
                'res_model': wizard._name, 'res_id': wizard.id, 'target': 'new',
                'views': [(self.env.ref('furniture_delivery_requests.view_delivery_spec_wizard').id, 'form')]}

    @api.model
    def action_edit_draft_specification(self, values):
        """Edit dialog values in transient records; cancel never saves a preset."""
        self.check_access('create')
        allowed = ('partner_id', 'company_id', 'model_id', 'product_id',
                   'width_cm', 'depth_cm', 'height_cm', 'upholstery_data')
        draft_values = {k: v for k, v in values.items() if k in allowed}
        draft_values['company_id'] = draft_values.get('company_id') or self.env.company.id
        spec = self.new(draft_values)
        spec._check_spec()
        wizard = self.env['furniture.delivery.creation.wizard'].create({
            'buyer_partner_id': spec.partner_id.id or self.env.user.partner_id.id,
            'company_id': spec.company_id.id, 'model_id': spec.model_id.id,
            'delivery_date': fields.Date.today(), 'specification_mode': 'custom',
            'line_ids': [Command.create({'product_id': spec.product_id.id,
                                        'quantity': 1, **spec._delivery_values()})],
        })
        action = wizard.line_ids.action_open_specifications()
        action['draft_line_id'] = wizard.line_ids.id
        action['context'] = {**self.env.context, 'company_standard_materials_only': True}
        return action


class DeliveryCustomerMode(models.Model):
    _inherit = 'furniture.mrp.future.order'

    specification_mode = fields.Selection([('standard', 'Custom'), ('custom', 'Standard')], default='standard', required=True, copy=True)


class DeliveryCreationCustomerMode(models.TransientModel):
    _inherit = 'furniture.delivery.creation.wizard'

    specification_mode = fields.Selection([('standard', 'Custom'), ('custom', 'Standard')], default='custom', required=True, string='تخصيص المشتري')
    has_custom_specs = fields.Boolean(compute='_compute_has_custom_specs')
    model_drafts = fields.Json(default=lambda self: {})
    model_draft_key = fields.Char()

    def _draft_key(self):
        return '%s:%s:%s:%s' % (self.company_id.id, self.buyer_partner_id._origin.id or self.buyer_partner_id.id, self.specification_mode, self.model_id._origin.id or self.model_id.id)

    def _stash_model_draft(self):
        if self.model_draft_key:
            drafts = dict(self.model_drafts or {})
            drafts[self.model_draft_key] = [{'product_id': line.product_id._origin.id or line.product_id.id, 'quantity': line.quantity, 'source_line_id': line.source_line_id.id, **line._delivery_values()} for line in self.line_ids]
            self.model_drafts = drafts


    def _customer_specs(self):
        self.ensure_one()
        return self.env['furniture.delivery.customer.spec'].search([
            ('partner_id', '=', self.buyer_partner_id._origin.id), ('company_id', '=', self.company_id.id)]) if self.buyer_partner_id else self.env['furniture.delivery.customer.spec']

    @api.depends(
        'model_id',
        'company_id',
        'buyer_partner_id',
        'specification_mode',
    )
    def _compute_choices(self):
        super()._compute_choices()
        for wizard in self:
            # The selection value ``custom`` is the user-facing Standard mode.
            # In that mode the buyer's saved profiles are the complete catalog,
            # not defaults layered over every company model.
            if wizard.specification_mode != 'custom' or not wizard.buyer_partner_id:
                continue
            specs = wizard._customer_specs()
            available_model_ids = set(wizard.available_model_ids.ids)
            standard_model_ids = set(specs.model_id.ids)
            wizard.available_model_ids = [Command.set(sorted(
                available_model_ids & standard_model_ids
            ))]
            model_id = wizard.model_id._origin.id or wizard.model_id.id
            if not model_id or model_id not in standard_model_ids:
                wizard.available_product_ids = [Command.clear()]
                continue
            standard_product_ids = set(
                specs.filtered(lambda spec: spec.model_id.id == model_id).product_id.ids
            )
            wizard.available_product_ids = [Command.set(sorted(
                set(wizard.available_product_ids.ids) & standard_product_ids
            ))]

    @api.depends('buyer_partner_id', 'company_id')
    def _compute_has_custom_specs(self):
        for wizard in self:
            wizard.has_custom_specs = bool(wizard._customer_specs())

    def _apply_customer_defaults(self, lines):
        specs = self._customer_specs().filtered(lambda s: s.model_id.id == (self.model_id._origin.id or self.model_id.id)) if self.specification_mode == 'custom' else self.env['furniture.delivery.customer.spec']
        by_product = {s.product_id.id: s for s in specs}
        for line in lines:
            spec = by_product.get(line.product_id._origin.id or line.product_id.id)
            vals = {'width_cm': 0, 'depth_cm': 0, 'height_cm': 0, 'upholstery_data': []}
            if spec:
                vals.update(spec._delivery_values())
            # Empty material kinds retain the recipe; configured kinds are copied.
            recipe = self.env['furniture.delivery.spec.wizard'].new({
                'delivery_line_id': line, **{k: vals[k] for k in ('width_cm', 'depth_cm', 'height_cm')},
            })._recipe_production_line()
            if recipe and not any(v['kind'] == 'fabric' for v in vals['upholstery_data']):
                vals['upholstery_data'] += [
                    {'kind': 'fabric', 'product_id': v['product_id'], 'quantity': v['qty'], 'piece_size': False}
                    for v in recipe._tailoring_recipe_material_values('fabric') if v['qty'] > 0
                ]
            line.update(vals)

    def _expand_custom_kits(self):
        if self.specification_mode != 'custom':
            return
        for line in self.line_ids:
            draft = self.env['furniture.mrp.future.order'].new({'company_id': self.company_id.id})
            demand = self.env['furniture.mrp.future.order.line'].new({
                'order_id': draft, 'product_id': line.product_id.id,
                'furniture_model_id': self.model_id.id, 'quantity': line.quantity,
            })
            if not demand._kit_bom():
                continue
            specs = demand._resolved_demand_specs()
            if any(s['model'] != self.model_id for s in specs):
                raise ValidationError(_('أضف قطع الطقم المختلط بالموديل الخاص بكل قطعة.'))
            commands = [Command.delete(line.id)]
            for spec in specs:
                existing = (self.line_ids - line).filtered(lambda l: l.product_id == spec['production_product'])
                if existing:
                    existing[0].quantity += spec['qty']
                else:
                    commands.append(Command.create({'product_id': spec['production_product'].id, 'quantity': spec['qty']}))
            self.line_ids = commands
        self.selected_product_ids = self.line_ids.product_id

    @api.onchange('selected_product_ids')
    def _onchange_products(self):
        previous_ids = {l.product_id._origin.id or l.product_id.id for l in self.line_ids}
        drafts = dict(self.model_drafts or {})
        key = 'items:' + self._draft_key()
        saved = dict(drafts.get(key, {}))
        for line in self.line_ids:
            saved[str(line.product_id._origin.id or line.product_id.id)] = {
                'quantity': line.quantity, 'source_line_id': line.source_line_id.id, **line._delivery_values()}
        drafts[key] = saved
        self.model_drafts = drafts
        super()._onchange_products()
        self._expand_custom_kits()
        added = self.line_ids.filtered(lambda l: (l.product_id._origin.id or l.product_id.id) not in previous_ids)
        self._apply_customer_defaults(added)
        for line in added:
            values = saved.get(str(line.product_id._origin.id or line.product_id.id))
            if values:
                line.update(values)

    def _select_company_standard_products(self):
        if self.specification_mode != 'custom' or not self.model_id:
            return
        specs = self._customer_specs().filtered(
            lambda s: s.model_id.id == (self.model_id._origin.id or self.model_id.id))
        available_ids = {p._origin.id or p.id for p in self.available_product_ids}
        products = specs.product_id.filtered(lambda p: p.id in available_ids)
        self.selected_product_ids = products
        self._onchange_products()

    @api.onchange('model_id', 'company_id')
    def _onchange_model(self):
        self._stash_model_draft()
        super()._onchange_model()
        self._select_company_standard_products()
        self.model_draft_key = self._draft_key()
        saved = (self.model_drafts or {}).get(self.model_draft_key)
        if saved is not None:
            allowed = {p._origin.id or p.id for p in self.available_product_ids}
            saved = [v for v in saved if v['product_id'] in allowed]
            self.selected_product_ids = [Command.set([v['product_id'] for v in saved])]
            self.line_ids = [Command.clear()] + [Command.create(v) for v in saved]

    @api.onchange('buyer_partner_id', 'company_id')
    def _onchange_buyer_standard_mode(self):
        self.specification_mode = 'custom' if self._customer_specs() else 'standard'
        self._onchange_customer_configuration()

    @api.onchange('specification_mode')
    def _onchange_customer_configuration(self):
        self.model_drafts = {}
        model_id = self.model_id._origin.id or self.model_id.id
        if (
            self.specification_mode == 'custom'
            and model_id
            and model_id not in self._customer_specs().model_id.ids
        ):
            self.model_id = False
            self.selected_product_ids = [Command.clear()]
            self.line_ids = [Command.clear()]
        self.model_draft_key = self._draft_key()
        self._select_company_standard_products()
        self._expand_custom_kits()
        self._apply_customer_defaults(self.line_ids)

    def action_create_delivery(self):
        self.ensure_one()
        model_id = self.model_id._origin.id or self.model_id.id
        if (
            self.specification_mode == 'custom'
            and model_id not in self._customer_specs().model_id.ids
        ):
            raise ValidationError(_(
                'الموديل المختار غير موجود في استاندرد الشركة. اختر موديلًا محفوظًا '
                'للشركة أو حوّل التخصيص إلى Custom.'
            ))
        result = super().action_create_delivery()
        order = self.order_id or self.env['furniture.mrp.future.order'].browse(result['res_id'])
        order.write({'specification_mode': self.specification_mode})
        return result
