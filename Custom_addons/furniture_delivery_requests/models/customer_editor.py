import copy
from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError
from .delivery_specifications import _fill_material_summaries


class DeliveryCustomerEditor(models.TransientModel):
    _name = 'furniture.delivery.customer.editor'
    _description = 'اختيارات تسليم المشتري'

    partner_id = fields.Many2one('res.partner', required=True, readonly=True)
    buyer_display_name = fields.Char(related='partner_id.name', readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    model_id = fields.Many2one('furniture.product.model', string='الموديل')
    product_id = fields.Many2one('product.product', string='الصنف')
    available_model_ids = fields.Many2many('furniture.product.model', compute='_compute_choices')
    available_product_ids = fields.Many2many('product.product', compute='_compute_choices')
    current_key = fields.Char()
    initial_data = fields.Json(default=dict, readonly=True)
    draft_data = fields.Json(default=dict)
    width_cm = fields.Float(string='العرض (سم)')
    depth_cm = fields.Float(string='العمق (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')
    upholstery_data = fields.Json(default=list)
    dimensions_expanded = fields.Boolean()
    fabric_summary = fields.Text(compute='_compute_summaries')
    takawe_summary = fields.Text(compute='_compute_summaries')
    dimension_label = fields.Char(compute='_compute_summaries', string='المقاس')

    @api.depends('company_id', 'model_id')
    def _compute_choices(self):
        for editor in self:
            catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(editor.company_id.id)
            editor.available_model_ids = [Command.set([m['id'] for m in catalog['models']])]
            model_id = editor.model_id._origin.id or editor.model_id.id
            editor.available_product_ids = [Command.set([p['id'] for p in catalog['products']
                if model_id in p['model_ids'] and not p['is_kit']])]

    @api.depends('upholstery_data', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_summaries(self):
        _fill_material_summaries(self)
        for editor in self:
            dims = [editor.width_cm, editor.depth_cm, editor.height_cm]
            editor.dimension_label = ' × '.join('%g' % v for v in dims) + ' سم' if any(dims) else _('الافتراضي')

    def _selection_values(self):
        return {**{k: 0 for k in ('width_cm', 'depth_cm', 'height_cm')},
                'upholstery_data': copy.deepcopy(self.upholstery_data or [])}

    @api.model
    def _empty_values(self):
        return {'width_cm': 0, 'depth_cm': 0, 'height_cm': 0, 'upholstery_data': []}

    def _stash_selection(self):
        if self.current_key:
            drafts = copy.deepcopy(self.draft_data or {})
            drafts[self.current_key] = self._selection_values()
            self.draft_data = drafts

    @api.onchange('model_id', 'product_id')
    def _onchange_selection(self):
        self._stash_selection()
        product_id = self.product_id._origin.id or self.product_id.id
        if product_id not in {p._origin.id or p.id for p in self.available_product_ids}:
            self.product_id = False
        key = '%s:%s' % (self.model_id._origin.id or self.model_id.id, product_id) if self.product_id else False
        self.current_key = key
        self.update(copy.deepcopy((self.draft_data or {}).get(key, self._empty_values())))

    @api.model
    def open_for_partner(self, partner_id, model_id=False, product_id=False):
        self.check_access('create')
        partner = self.env['res.partner'].browse(partner_id).exists()
        partner.check_access('write')
        specs = self.env['furniture.delivery.customer.spec'].search([
            ('partner_id', '=', partner.id), ('company_id', '=', self.env.company.id)])
        initial = {'%s:%s' % (s.model_id.id, s.product_id.id): s._delivery_values() for s in specs}
        key = '%s:%s' % (model_id, product_id) if model_id and product_id else False
        editor = self.create({'partner_id': partner.id, 'initial_data': initial, 'draft_data': initial,
                              'model_id': model_id, 'product_id': product_id, 'current_key': key,
                              **copy.deepcopy(initial.get(key, self._empty_values()))})
        return {'type': 'ir.actions.act_window', 'name': (partner.name or '').upper(),
                'res_model': self._name, 'res_id': editor.id,
                'target': 'current' if self.env.context.get('company_standards_page') else 'new',
                'context': dict(self.env.context),
                'views': [(self.env.ref('furniture_delivery_requests.view_delivery_customer_editor').id, 'form')]}

    def detail_models(self):
        self.ensure_one()
        self.check_access('read')
        catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(self.company_id.id)
        return [m for m in catalog['models'] if any(
            m['id'] in p['model_ids'] and not p['is_kit'] for p in catalog['products'])]

    def model_overview(self, model_id, drafts):
        self.ensure_one()
        self.check_access('read')
        catalog = self.env['furniture.mrp.future.order'].get_product_picker_catalog(self.company_id.id)
        rows = []
        for product in catalog['products']:
            if model_id not in product['model_ids'] or product['is_kit']:
                continue
            values = (drafts or {}).get('%s:%s' % (model_id, product['id']), self._empty_values())
            preview = self.new({k: values.get(k, default) for k, default in self._empty_values().items()})
            rows.append({'id': product['id'], 'name': product['name'],
                         'dimension': preview.dimension_label, 'fabric': preview.fabric_summary,
                         'takawe': preview.takawe_summary,
                         'takawe_counts': [{'index': i, 'value': v.get('piece_count', 1),
                            'name': self.env['product.product'].browse(v['product_id']).display_name}
                            for i, v in enumerate(preview.upholstery_data or []) if v['kind'] == 'takawe']})
        def item_order(row):
            name = row['name'].replace('ة', 'ه').replace('ى', 'ي').replace('أ', 'ا').replace('إ', 'ا')
            if 'كنبه' in name and 'كبير' in name:
                return 0
            if 'فوتيه' in name:
                return 2
            return 1

        return sorted(rows, key=item_order)

    def action_save(self):
        self.ensure_one()
        self.partner_id.check_access('write')
        self._stash_selection()
        specs = self.env['furniture.delivery.customer.spec'].search([
            ('partner_id', '=', self.partner_id.id), ('company_id', '=', self.company_id.id)])
        existing = {'%s:%s' % (s.model_id.id, s.product_id.id): s for s in specs}
        for key, values in (self.draft_data or {}).items():
            original = (self.initial_data or {}).get(key, self._empty_values())
            if values == original:
                continue
            current = existing.get(key)
            if (current and current._delivery_values() != original) or (not current and key in (self.initial_data or {})):
                raise UserError(_('تخصيصات المشتري تغيرت في نافذة أخرى. أعد فتح الاختيارات قبل الحفظ.'))
            if current:
                current.write(values)
            else:
                model_id, product_id = map(int, key.split(':'))
                self.env['furniture.delivery.customer.spec'].create({
                    'partner_id': self.partner_id.id, 'company_id': self.company_id.id,
                    'model_id': model_id, 'product_id': product_id, **values})
        return self.action_back_to_buyer() if self.env.context.get('company_standards_page') else {'type': 'ir.actions.act_window_close'}

    def action_back_to_buyer(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('استاندرد شركات'),
                'res_model': 'res.partner', 'res_id': self.partner_id.id, 'target': 'current',
                'views': [(self.env.ref('furniture_delivery_requests.view_delivery_buyer_form').id, 'form')]}


class PartnerDeliveryEditor(models.Model):
    _inherit = 'res.partner'

    def delivery_standard_models(self):
        self.ensure_one()
        self.check_access('read')
        specs = self.env['furniture.delivery.customer.spec'].search([
            ('partner_id', '=', self.id), ('company_id', '=', self.env.company.id)])
        return [{'id': model.id, 'name': model.display_name} for model in specs.model_id]

    def action_delivery_model_preferences(self, model_id):
        self.ensure_one()
        self.check_access('write')
        spec = self.env['furniture.delivery.customer.spec'].search([
            ('partner_id', '=', self.id), ('company_id', '=', self.env.company.id),
            ('model_id', '=', model_id)], limit=1)
        if not spec:
            raise UserError(_('لا توجد اختيارات محفوظة لهذا الموديل.'))
        return self.env['furniture.delivery.customer.editor'].with_context(
            company_standards_page=False).open_for_partner(self.id, model_id, spec.product_id.id)

    def action_delivery_custom_summary(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'name': _('تخصيصات %s', self.display_name),
            'res_model': 'furniture.delivery.customer.spec',
            'views': [(self.env.ref('furniture_delivery_requests.view_delivery_customer_summary').id, 'list')],
            'target': 'new',
            'domain': [('partner_id', '=', self.id), ('company_id', '=', self.env.company.id)],
            'context': {'group_by': 'model_id', 'create': False, 'edit': False, 'delete': False},
            'help': '<p class="o_view_nocontent_smiling_face">لا توجد تخصيصات محفوظة لهذا المشتري بعد.</p>',
        }

    def action_delivery_preferences(self):
        self.ensure_one()
        return self.env['furniture.delivery.customer.editor'].open_for_partner(self.id)


class ProfileDeliveryEditor(models.Model):
    _inherit = 'furniture.delivery.customer.spec'

    def action_delivery_preferences(self):
        self.ensure_one()
        return self.env['furniture.delivery.customer.editor'].with_company(self.company_id).open_for_partner(
            self.partner_id.id, self.model_id.id, self.product_id.id)
