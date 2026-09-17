# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.osv import expression


class FurnitureProductFamily(models.Model):
    _name = 'furniture.product.family'
    _description = 'الفئة الرئيسية لمنتج الأثاث'
    _order = 'sequence, name, id'

    name = fields.Char(string='الفئة الرئيسية', required=True, index=True)
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(default=True)
    model_ids = fields.One2many(
        'furniture.product.model',
        'family_id',
        string='الموديلات',
    )
    product_template_ids = fields.One2many(
        'product.template',
        'furniture_family_id',
        string='المنتجات النهائية',
    )

    @api.model
    def _normalize_name(self, value):
        return ' '.join((value or '').split()).strip()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'name' in vals:
                vals['name'] = self._normalize_name(vals['name'])
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'name' in vals:
            vals['name'] = self._normalize_name(vals['name'])
        return super().write(vals)

    @api.constrains('name')
    def _check_unique_name(self):
        for rec in self:
            if not rec.name:
                raise ValidationError(_('اسم الفئة الرئيسية مطلوب.'))
            duplicate = self.with_context(active_test=False).search_count([
                ('id', '!=', rec.id),
                ('name', '=ilike', rec.name),
            ])
            if duplicate:
                raise ValidationError(_('الفئة الرئيسية "%s" موجودة بالفعل.') % rec.name)


class FurnitureProductModel(models.Model):
    _name = 'furniture.product.model'
    _description = 'موديل منتج الأثاث'
    _order = 'sequence, name, id'

    name = fields.Char(string='الموديل', required=True, index=True)
    family_id = fields.Many2one(
        'furniture.product.family',
        string='الفئة الرئيسية',
        ondelete='restrict',
        index=True,
        help='حقل داخلي اختياري للتوافق مع التصنيفات القديمة.',
    )
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(default=True)
    match_key = fields.Char(
        string='مفتاح مطابقة الموديل',
        compute='_compute_match_key',
        store=True,
        index=True,
        help='اسم موحد يربط نفس الموديل عبر فئات مختلفة داخل الأطقم.',
    )
    product_template_ids = fields.One2many(
        'product.template',
        'furniture_model_id',
        string='المنتجات النهائية',
    )

    @api.depends('name', 'family_id.name')
    def _compute_match_key(self):
        for rec in self:
            model_name = rec._normalize_name(rec.name)
            family_name = rec._normalize_name(rec.family_id.name)
            if (
                family_name
                and model_name.casefold().startswith(family_name.casefold() + ' ')
            ):
                model_name = model_name[len(family_name):].strip()
            rec.match_key = model_name.casefold() or False

    @api.model
    def _normalize_name(self, value):
        return ' '.join((value or '').split()).strip()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'name' in vals:
                vals['name'] = self._normalize_name(vals['name'])
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'name' in vals:
            vals['name'] = self._normalize_name(vals['name'])
        return super().write(vals)

    @api.constrains('name')
    def _check_unique_name(self):
        for rec in self:
            if not rec.name:
                raise ValidationError(_('اسم الموديل مطلوب.'))
            duplicate = self.with_context(active_test=False).search_count([
                ('id', '!=', rec.id),
                ('name', '=ilike', rec.name),
            ])
            if duplicate:
                raise ValidationError(_('الموديل "%s" موجود بالفعل.') % rec.name)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    furniture_has_active_sale_recipe = fields.Boolean(
        string='متاح في أوامر البيع',
        compute='_compute_furniture_has_active_sale_recipe',
        store=True,
        index=True,
        help=(
            'حقل فني يسمح بظهور المنتج في أمر البيع عندما تكون له '
            'ريسيبي فعالة من نوع تصنيع أو Kit.'
        ),
    )
    furniture_has_active_sale_kit_recipe = fields.Boolean(
        string='طقم متاح في أوامر البيع',
        compute='_compute_furniture_has_active_sale_kit_recipe',
        store=True,
        index=True,
        help='حقل فني يسمح بظهور المنتج في أمر البيع عندما تكون له ريسيبي Kit فعالة.',
    )

    furniture_family_id = fields.Many2one(
        'furniture.product.family',
        string='الفئة الرئيسية',
        ondelete='restrict',
        index=True,
        copy=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        ondelete='restrict',
        index=True,
        copy=True,
        domain="[('family_id', '=', furniture_family_id)]",
    )
    furniture_classification_label = fields.Char(
        string='التصنيف',
        compute='_compute_furniture_classification_label',
    )
    furniture_model_match_key = fields.Char(
        related='furniture_model_id.match_key',
        string='مفتاح مطابقة الموديل',
        store=True,
        index=True,
        readonly=True,
    )

    @api.depends('bom_ids.active', 'bom_ids.type')
    def _compute_furniture_has_active_sale_recipe(self):
        for template in self:
            template.furniture_has_active_sale_recipe = any(
                bom.active and bom.type in ('normal', 'phantom')
                for bom in template.with_context(active_test=False).bom_ids
            )

    @api.depends('bom_ids.active', 'bom_ids.type')
    def _compute_furniture_has_active_sale_kit_recipe(self):
        for template in self:
            template.furniture_has_active_sale_kit_recipe = any(
                bom.active and bom.type == 'phantom'
                for bom in template.with_context(active_test=False).bom_ids
            )

    @api.depends('furniture_model_id.name')
    def _compute_furniture_classification_label(self):
        for template in self:
            template.furniture_classification_label = (
                template.furniture_model_id.name or False
            )

    def _compute_bom_count(self):
        super()._compute_bom_count()
        Bom = self.env['mrp.bom']
        for template in self:
            hidden_count = Bom.search_count([
                ('furniture_is_model_recipe', '=', True),
                '|',
                ('product_tmpl_id', '=', template.id),
                ('byproduct_ids.product_id.product_tmpl_id', '=', template.id),
            ])
            template.bom_count = max(template.bom_count - hidden_count, 0)

    def action_used_in_bom(self):
        action = super().action_used_in_bom()
        action['domain'] = expression.AND([
            action.get('domain', []),
            [('furniture_is_model_recipe', '=', False)],
        ])
        return action

    @api.onchange('furniture_family_id')
    def _onchange_furniture_family_id(self):
        for template in self:
            if (
                template.furniture_model_id
                and template.furniture_model_id.family_id != template.furniture_family_id
            ):
                template.furniture_model_id = False

    @api.onchange('furniture_model_id')
    def _onchange_furniture_model_id(self):
        for template in self:
            if template.furniture_model_id:
                template.furniture_family_id = template.furniture_model_id.family_id

    @api.constrains('furniture_family_id', 'furniture_model_id')
    def _check_furniture_model_family(self):
        for template in self:
            if (
                template.furniture_model_id
                and template.furniture_model_id.family_id != template.furniture_family_id
            ):
                raise ValidationError(
                    _('الموديل المختار لا يتبع الفئة الرئيسية المحددة للمنتج %s.')
                    % template.display_name
                )

    def write(self, vals):
        result = super().write(vals)
        if (
            {'furniture_family_id', 'furniture_model_id'} & set(vals)
            and not self.env.context.get('furniture_skip_bom_classification_sync')
        ):
            for template in self:
                # A normal manufacturing recipe owns its model independently:
                # the same neutral product can have one recipe per furniture
                # model.  Product classification is still mirrored to Kits,
                # because each Kit itself is a model-specific stock SKU.
                boms = self.env['mrp.bom'].sudo().search([
                    ('furniture_product_id', 'in', template.product_variant_ids.ids),
                    ('type', '=', 'phantom'),
                ])
                if boms:
                    boms.with_context(furniture_skip_product_classification_sync=True).write({
                        'furniture_family_id': template.furniture_family_id.id,
                        'furniture_model_id': template.furniture_model_id.id,
                    })
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    furniture_has_active_sale_recipe = fields.Boolean(
        related='product_tmpl_id.furniture_has_active_sale_recipe',
        string='متاح في أوامر البيع',
        store=True,
        index=True,
        readonly=True,
    )
    furniture_has_active_sale_kit_recipe = fields.Boolean(
        related='product_tmpl_id.furniture_has_active_sale_kit_recipe',
        string='طقم متاح في أوامر البيع',
        store=True,
        index=True,
        readonly=True,
    )

    furniture_finished_bom_ids = fields.One2many(
        'mrp.bom',
        'furniture_product_id',
        string='ريسيبيات المنتج النهائي',
        context={'active_test': False},
        readonly=True,
    )
    furniture_is_finished_product = fields.Boolean(
        string='منتج أثاث نهائي',
        compute='_compute_furniture_is_finished_product',
        store=True,
        index=True,
    )
    furniture_has_active_normal_recipe = fields.Boolean(
        string='له ريسيبي تصنيع فعالة',
        compute='_compute_furniture_has_active_normal_recipe',
        store=True,
        index=True,
        help='حقل فني لحصر اختيارات التصنيع في المنتجات الأساسية التي لها ريسيبي تصنيع فعالة.',
    )

    furniture_family_id = fields.Many2one(
        related='product_tmpl_id.furniture_family_id',
        string='الفئة الرئيسية',
        store=True,
        readonly=False,
    )
    furniture_model_id = fields.Many2one(
        related='product_tmpl_id.furniture_model_id',
        string='الموديل',
        store=True,
        readonly=False,
    )
    furniture_classification_label = fields.Char(
        related='product_tmpl_id.furniture_classification_label',
        string='التصنيف',
        readonly=True,
    )
    furniture_model_match_key = fields.Char(
        related='product_tmpl_id.furniture_model_match_key',
        string='مفتاح مطابقة الموديل',
        store=True,
        index=True,
        readonly=True,
    )

    @api.depends('furniture_finished_bom_ids.type', 'furniture_dimension_source_product_id')
    def _compute_furniture_is_finished_product(self):
        for product in self:
            product.furniture_is_finished_product = bool(
                product.furniture_finished_bom_ids.filtered(lambda bom: bom.type == 'normal')
                or product.furniture_dimension_source_product_id
            )

    @api.depends(
        'furniture_finished_bom_ids.active',
        'furniture_finished_bom_ids.type',
    )
    def _compute_furniture_has_active_normal_recipe(self):
        for product in self:
            product.furniture_has_active_normal_recipe = any(
                bom.active and bom.type == 'normal'
                for bom in product.furniture_finished_bom_ids
            )

    def _compute_bom_count(self):
        super()._compute_bom_count()
        Bom = self.env['mrp.bom']
        for product in self:
            hidden_count = Bom.search_count([
                ('furniture_is_model_recipe', '=', True),
                '|', '|',
                ('byproduct_ids.product_id', '=', product.id),
                ('product_id', '=', product.id),
                '&',
                ('product_id', '=', False),
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
            ])
            product.bom_count = max(product.bom_count - hidden_count, 0)

    def action_used_in_bom(self):
        action = super().action_used_in_bom()
        action['domain'] = expression.AND([
            action.get('domain', []),
            [('furniture_is_model_recipe', '=', False)],
        ])
        return action

    def action_view_bom(self):
        action = super().action_view_bom()
        action['domain'] = expression.AND([
            action.get('domain', []),
            [('furniture_is_model_recipe', '=', False)],
        ])
        return action

    def _compute_quantities_dict(
        self, lot_id, owner_id, package_id, from_date=False, to_date=False,
    ):
        """Compute furniture Kit availability from finished goods only.

        Odoo normally evaluates a phantom BoM against every internal location
        covered by the current warehouse.  Semi-finished quantities in stage
        stores must never make a sellable furniture Kit look available.
        """
        bom_kits = self.env['mrp.bom']._bom_find(self, bom_type='phantom')
        furniture_kits = self.filtered(
            lambda product: (
                bom_kits.get(product)
                and bom_kits[product].furniture_model_match_key
            )
        )
        regular_products = self - furniture_kits
        result = (
            super(ProductProduct, regular_products)._compute_quantities_dict(
                lot_id,
                owner_id,
                package_id,
                from_date=from_date,
                to_date=to_date,
            )
            if regular_products
            else {}
        )
        finished_location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        if furniture_kits and finished_location:
            finished_result = super(
                ProductProduct,
                furniture_kits.with_context(location=finished_location.id),
            )._compute_quantities_dict(
                lot_id,
                owner_id,
                package_id,
                from_date=from_date,
                to_date=to_date,
            )
            result.update(finished_result)
        elif furniture_kits:
            result.update(
                super(ProductProduct, furniture_kits)._compute_quantities_dict(
                    lot_id,
                    owner_id,
                    package_id,
                    from_date=from_date,
                    to_date=to_date,
                )
            )
        return result


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    # The user edits one visible BoM per furniture product.  Every model is
    # persisted in a hidden child BoM so switching the selector can restore
    # that model's complete recipe while production still receives an exact,
    # ordinary mrp.bom record.
    furniture_parent_bom_id = fields.Many2one(
        'mrp.bom',
        string='ريسيبي المنتج الرئيسي',
        ondelete='cascade',
        index=True,
        copy=False,
    )
    furniture_model_recipe_ids = fields.One2many(
        'mrp.bom',
        'furniture_parent_bom_id',
        string='ريسيبيات الموديلات المحفوظة',
        copy=False,
    )
    furniture_is_model_recipe = fields.Boolean(
        string='ريسيبي موديل داخلي',
        default=False,
        index=True,
        copy=False,
    )
    furniture_recipe_ready = fields.Boolean(
        string='ريسيبي الموديل محفوظ',
        default=True,
        index=True,
        copy=False,
        help=(
            'يظل الريسيبي الجديد غير متاح للإنتاج حتى يحفظ المستخدم '
            'خامات الموديل أو إعداداته لأول مرة.'
        ),
    )

    furniture_family_id = fields.Many2one(
        'furniture.product.family',
        string='الفئة الرئيسية',
        ondelete='restrict',
        index=True,
        copy=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        ondelete='restrict',
        index=True,
        copy=True,
        domain="[('family_id', '=', furniture_family_id)]",
    )
    furniture_recipe_model_id = fields.Many2one(
        'furniture.product.model',
        string='عرض خامات موديل',
        ondelete='restrict',
        index=True,
        copy=False,
        domain="[('family_id', '=', furniture_family_id)]",
        help=(
            'موديل الريسيبي المعروض حاليًا داخل قائمة المواد الرئيسية. '
            'هذا الحقل للتنقل بين الريسيبيات فقط ولا يصنف المنتج نفسه.'
        ),
    )
    furniture_model_match_key = fields.Char(
        related='furniture_model_id.match_key',
        string='مفتاح مطابقة الموديل',
        store=True,
        index=True,
        readonly=True,
    )
    furniture_finished_location_id = fields.Many2one(
        'stock.location',
        string='مخزن المنتج التام',
        compute='_compute_furniture_finished_location_id',
    )

    @api.model
    def _furniture_recipe_content_field_names(self):
        return {
            'furniture_width_cm',
            'furniture_depth_cm',
            'furniture_height_cm',
            'use_priming',
            'use_painting',
            'use_carpentry',
            'use_bases',
            'use_finishing',
            'use_tailoring',
            'use_sewing',
            'use_upholstery',
            'use_packaging',
            'furniture_stage_material_line_ids',
            'priming_material_line_ids',
            'painting_material_line_ids',
            'carpentry_material_line_ids',
            'bases_material_line_ids',
            'finishing_material_line_ids',
            'tailoring_material_line_ids',
            'sewing_material_line_ids',
            'upholstery_material_line_ids',
            'packaging_material_line_ids',
            'furniture_stage_ids',
            'bom_line_ids',
        }

    @api.model
    def _bom_find_domain(
        self,
        products,
        picking_type=None,
        company_id=False,
        bom_type=False,
    ):
        domain = super()._bom_find_domain(
            products,
            picking_type=picking_type,
            company_id=company_id,
            bom_type=bom_type,
        )
        if not self.env.context.get('furniture_include_internal_model_recipes'):
            domain = expression.AND([
                domain,
                [
                    ('furniture_is_model_recipe', '=', False),
                    ('furniture_recipe_model_id', '=', False),
                ],
            ])
        return domain

    def _find_hidden_furniture_model_recipe(self, model):
        self.ensure_one()
        if not self.id or not model:
            return self.env['mrp.bom']
        return self.env['mrp.bom'].with_context(active_test=False).search([
            ('furniture_parent_bom_id', '=', self.id),
            ('furniture_is_model_recipe', '=', True),
            ('furniture_model_id', '=', model.id),
        ], order='id desc', limit=1)

    def _furniture_material_commands_from_recipe(self, source_bom):
        commands = [(5, 0, 0)]
        if not source_bom:
            return commands
        for vals in self._iter_source_material_values(source_bom):
            commands.append((0, 0, vals))
        return commands

    def _furniture_model_switch_content_vals(
        self,
        source_bom=False,
        split_stage_fields=False,
    ):
        """Values shown on the visible BoM for one selected model."""
        if not source_bom:
            vals = {
                'furniture_width_cm': 0.0,
                'furniture_depth_cm': 0.0,
                'furniture_height_cm': 0.0,
                'use_priming': True,
                'use_painting': True,
                'use_carpentry': True,
                'use_bases': True,
                'use_finishing': True,
                'use_tailoring': True,
                'use_sewing': False,
                'use_upholstery': True,
                'use_packaging': True,
                'furniture_stage_ids': [(5, 0, 0)],
                'bom_line_ids': [(5, 0, 0)],
                'furniture_source_bom_id': False,
            }
        else:
            vals = {
                'furniture_width_cm': source_bom.furniture_width_cm,
                'furniture_depth_cm': source_bom.furniture_depth_cm,
                'furniture_height_cm': source_bom.furniture_height_cm,
                'use_priming': source_bom.use_priming,
                'use_painting': source_bom.use_painting,
                'use_carpentry': source_bom.use_carpentry,
                'use_bases': source_bom.use_bases,
                'use_finishing': source_bom.use_finishing,
                'use_tailoring': source_bom.use_tailoring or source_bom.use_sewing,
                'use_sewing': False,
                'use_upholstery': source_bom.use_upholstery,
                'use_packaging': source_bom.use_packaging,
                'furniture_stage_ids': [(5, 0, 0)],
                'bom_line_ids': [(5, 0, 0)],
                'furniture_source_bom_id': False,
            }
        if split_stage_fields:
            if source_bom:
                vals.update(self._get_stage_material_commands_by_field(source_bom))
            else:
                vals.update({
                    'priming_material_line_ids': [(5, 0, 0)],
                    'painting_material_line_ids': [],
                    'carpentry_material_line_ids': [],
                    'bases_material_line_ids': [],
                    'finishing_material_line_ids': [],
                    'tailoring_material_line_ids': [],
                    'sewing_material_line_ids': [],
                    'upholstery_material_line_ids': [],
                    'packaging_material_line_ids': [],
                })
        else:
            vals['furniture_stage_material_line_ids'] = (
                self._furniture_material_commands_from_recipe(source_bom)
            )
        return vals

    def _furniture_hidden_recipe_vals(self):
        self.ensure_one()
        selected_model = self.furniture_recipe_model_id
        vals = self._furniture_model_switch_content_vals(self)
        vals.update({
            'product_tmpl_id': self.product_tmpl_id.id,
            'furniture_product_id': self.furniture_product_id.id,
            'product_qty': self.product_qty,
            'product_uom_id': self.product_uom_id.id,
            'type': 'normal',
            'company_id': self.company_id.id or False,
            'active': self.active,
            'furniture_family_id': selected_model.family_id.id or False,
            'furniture_model_id': selected_model.id,
            'furniture_recipe_model_id': False,
            'furniture_parent_bom_id': self.id,
            'furniture_is_model_recipe': True,
            'furniture_recipe_ready': True,
            'furniture_source_bom_id': False,
        })
        return vals

    def _sync_furniture_model_recipe(self):
        if self.env.context.get('furniture_skip_model_recipe_sync'):
            return
        Recipe = self.env['mrp.bom'].with_context(
            active_test=False,
            furniture_skip_model_recipe_sync=True,
            furniture_skip_model_switch_load=True,
            furniture_skip_model_material_reset=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        for bom in self.filtered(lambda rec: (
            rec.type == 'normal'
            and not rec.furniture_is_model_recipe
            and rec.furniture_product_id
            and rec.furniture_recipe_model_id
        )):
            recipes = Recipe.search([
                ('furniture_parent_bom_id', '=', bom.id),
                ('furniture_is_model_recipe', '=', True),
            ])
            if recipes:
                recipes.write({'active': bom.active})
            recipe = recipes.filtered(
                lambda rec: rec.furniture_model_id == bom.furniture_recipe_model_id
            )[:1]
            vals = bom._furniture_hidden_recipe_vals()
            if recipe:
                recipe.write(vals)
            else:
                Recipe.create(vals)

    def _sync_furniture_model_recipe_metadata(self):
        """Keep child identity aligned without copying stale recipe content."""
        Recipe = self.env['mrp.bom'].with_context(
            active_test=False,
            furniture_skip_model_recipe_sync=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        for bom in self.filtered(lambda rec: (
            rec.type == 'normal'
            and not rec.furniture_is_model_recipe
        )):
            recipes = Recipe.search([
                ('furniture_parent_bom_id', '=', bom.id),
                ('furniture_is_model_recipe', '=', True),
            ])
            if recipes:
                recipes.write({
                    'product_tmpl_id': bom.product_tmpl_id.id,
                    'furniture_product_id': bom.furniture_product_id.id,
                    'product_qty': bom.product_qty,
                    'product_uom_id': bom.product_uom_id.id,
                    'company_id': bom.company_id.id or False,
                    'active': bom.active,
                })

    def _compute_furniture_finished_location_id(self):
        location = self.env.ref(
            'furniture_mrp.location_finished_goods',
            raise_if_not_found=False,
        )
        for bom in self:
            bom.furniture_finished_location_id = location

    def action_select_furniture_recipe_model(self, model_id):
        """Return the exact model recipe to open without dirtying the form."""
        self.ensure_one()
        root_bom = (
            self.furniture_parent_bom_id
            if self.furniture_is_model_recipe
            else self
        )
        if (
            self.type != 'normal'
            or not root_bom
            or root_bom.furniture_is_model_recipe
        ):
            raise ValidationError(_(
                'تبديل عرض خامات الموديل متاح فقط في ريسيبي المنتج الرئيسية.'
            ))
        model = self.env['furniture.product.model'].browse(model_id).exists()
        if not model:
            raise ValidationError(_('الموديل المختار غير موجود.'))
        if root_bom.furniture_recipe_model_id != model:
            root_bom.with_context(
                furniture_skip_model_recipe_sync=True,
                tracking_disable=True,
            ).write({'furniture_recipe_model_id': model.id})
        recipe = root_bom._find_hidden_furniture_model_recipe(model)
        if not recipe:
            vals = root_bom._furniture_model_switch_content_vals(False)
            vals.update({
                'product_tmpl_id': root_bom.product_tmpl_id.id,
                'furniture_product_id': root_bom.furniture_product_id.id,
                'product_qty': root_bom.product_qty,
                'product_uom_id': root_bom.product_uom_id.id,
                'type': 'normal',
                'company_id': root_bom.company_id.id or False,
                'active': root_bom.active,
                'furniture_family_id': model.family_id.id or False,
                'furniture_model_id': model.id,
                'furniture_recipe_model_id': False,
                'furniture_parent_bom_id': root_bom.id,
                'furniture_is_model_recipe': True,
                'furniture_recipe_ready': False,
                'furniture_source_bom_id': False,
            })
            recipe = self.env['mrp.bom'].with_context(
                furniture_skip_model_recipe_sync=True,
                furniture_skip_model_switch_load=True,
                furniture_skip_model_material_reset=True,
                tracking_disable=True,
                mail_create_nolog=True,
            ).create(vals)
        elif not recipe.active and root_bom.active:
            recipe.with_context(
                furniture_skip_model_recipe_sync=True,
                tracking_disable=True,
            ).write({'active': True})
        return recipe.id

    @api.onchange('furniture_product_id')
    def _onchange_furniture_product_classification(self):
        for bom in self:
            if bom.type == 'phantom':
                bom.furniture_family_id = bom.furniture_product_id.furniture_family_id
                bom.furniture_model_id = bom.furniture_product_id.furniture_model_id

    @api.onchange('type')
    def _onchange_furniture_bom_type_model(self):
        for bom in self:
            if bom.type == 'phantom' and bom.furniture_product_id:
                bom.furniture_family_id = bom.furniture_product_id.furniture_family_id
                bom.furniture_model_id = bom.furniture_product_id.furniture_model_id

    @api.onchange('furniture_family_id')
    def _onchange_furniture_bom_family(self):
        for bom in self:
            if bom.furniture_model_id and bom.furniture_model_id.family_id != bom.furniture_family_id:
                bom.furniture_model_id = False
            if (
                bom.furniture_recipe_model_id
                and bom.furniture_recipe_model_id.family_id != bom.furniture_family_id
            ):
                bom.furniture_recipe_model_id = False

    @api.onchange('furniture_model_id')
    def _onchange_furniture_bom_model(self):
        for bom in self:
            if bom.furniture_model_id:
                bom.furniture_family_id = bom.furniture_model_id.family_id

    @api.onchange('furniture_recipe_model_id')
    def _onchange_furniture_recipe_model_id(self):
        for bom in self:
            if (
                bom.type != 'normal'
                or bom.furniture_is_model_recipe
                or not bom.furniture_recipe_model_id
            ):
                continue
            origin = bom._origin if bom._origin and bom._origin.id else bom
            recipe = origin._find_hidden_furniture_model_recipe(
                bom.furniture_recipe_model_id
            ) if origin.id else self.env['mrp.bom']
            bom.update(bom._furniture_model_switch_content_vals(
                recipe,
                split_stage_fields=True,
            ))
            bom.furniture_family_id = bom.furniture_recipe_model_id.family_id

    @api.model
    def _find_furniture_normal_recipe(self, product, model, company=False):
        """Return only the recipe matching the exact product and model."""
        if not product or not model:
            return self.env['mrp.bom']
        source_product = product.furniture_dimension_source_product_id or product
        company = company or self.env.company
        hidden_domain = [
            ('type', '=', 'normal'),
            ('furniture_is_model_recipe', '=', True),
            ('furniture_recipe_ready', '=', True),
            ('furniture_product_id', '=', source_product.id),
            ('furniture_model_id', '=', model.id),
        ]
        company_recipe = self.search(
            hidden_domain + [('company_id', '=', company.id)],
            order='id desc',
            limit=1,
        )
        company_recipe = company_recipe or self.search(
            hidden_domain + [('company_id', '=', False)],
            order='id desc',
            limit=1,
        )
        if company_recipe:
            return company_recipe

        # Compatibility fallback while an older database is being migrated.
        legacy_domain = [
            ('type', '=', 'normal'),
            ('furniture_is_model_recipe', '=', False),
            ('furniture_product_id', '=', source_product.id),
            ('furniture_model_id', '=', model.id),
        ]
        return self.search(
            legacy_domain + [('company_id', 'in', [company.id, False])],
            order='company_id desc, id desc',
            limit=1,
        )

    @api.model
    def _furniture_product_uses_model_recipes(self, product, company=False):
        """Whether this product must be routed by an explicit model."""
        if not product:
            return False
        source_product = product.furniture_dimension_source_product_id or product
        company = company or self.env.company
        return bool(self.with_context(active_test=False).search_count([
            ('type', '=', 'normal'),
            ('furniture_product_id', '=', source_product.id),
            ('company_id', 'in', [company.id, False]),
            '|',
            ('furniture_is_model_recipe', '=', True),
            '|',
            ('furniture_recipe_model_id', '!=', False),
            ('furniture_model_id', '!=', False),
        ]))

    @api.model
    def _find_furniture_generic_normal_recipe(self, product, company=False):
        """Compatibility route for genuinely model-less legacy products."""
        if not product:
            return self.env['mrp.bom']
        source_product = product.furniture_dimension_source_product_id or product
        company = company or self.env.company
        if self._furniture_product_uses_model_recipes(source_product, company):
            return self.env['mrp.bom']
        domain = [
            ('type', '=', 'normal'),
            ('furniture_is_model_recipe', '=', False),
            ('furniture_recipe_model_id', '=', False),
            ('furniture_model_id', '=', False),
            ('furniture_product_id', '=', source_product.id),
        ]
        recipe = self.search(
            domain + [('company_id', '=', company.id)],
            order='id desc',
            limit=1,
        )
        return recipe or self.search(
            domain + [('company_id', '=', False)],
            order='id desc',
            limit=1,
        )

    @api.model
    def _find_furniture_production_recipe(
        self,
        product,
        model=False,
        company=False,
    ):
        """Resolve the safe operational recipe for a product/model choice.

        Model-routed products must keep using their exact ready model recipe.
        A generic recipe is only a valid fallback for a product that has no
        model routing at all; ``_find_furniture_generic_normal_recipe``
        enforces that distinction.
        """
        company = company or self.env.company
        if model:
            exact_recipe = self._find_furniture_normal_recipe(
                product,
                model,
                company=company,
            )
            if exact_recipe:
                return exact_recipe
        return self._find_furniture_generic_normal_recipe(
            product,
            company=company,
        )

    @api.model
    def _furniture_product_for_selected_model(self, product, model):
        """Return the stock SKU dedicated to ``product name + model``.

        Users deliberately select the commercial product name and the furniture
        model separately on the BoM.  Stock, however, must use a different
        ``product.product`` for every model so that a Mironi large sofa is not
        mixed with the same sofa in another model.
        """
        if not product or not model or product.furniture_model_id == model:
            return product

        normalized_name = ' '.join(
            (product.product_tmpl_id.name or product.name or '').split()
        ).strip().casefold()
        candidates = self.env['product.product'].with_context(active_test=False).search([
            ('id', '!=', product.id),
            ('furniture_model_id', '=', model.id),
        ])
        matching_product = candidates.filtered(
            lambda candidate: ' '.join(
                (candidate.product_tmpl_id.name or candidate.name or '').split()
            ).strip().casefold() == normalized_name
        )[:1]
        if matching_product:
            return matching_product

        # An unclassified product can safely become the first model-specific SKU
        # as long as it is not already used by another classified recipe.
        conflicting_bom = self.with_context(active_test=False).search([
            ('furniture_product_id', '=', product.id),
            ('furniture_model_id', '!=', False),
            ('furniture_model_id', '!=', model.id),
            ('id', 'not in', self.ids or [0]),
        ], limit=1)
        if not product.furniture_model_id and not conflicting_bom:
            return product

        model_product = product.copy({
            'name': product.product_tmpl_id.name or product.name,
            'default_code': False,
        })
        model_product.product_tmpl_id.with_context(
            furniture_skip_bom_classification_sync=True,
        ).write({
            'furniture_family_id': model.family_id.id,
            'furniture_model_id': model.id,
        })
        return model_product

    @api.model
    def _prepare_furniture_classification_vals(self, vals):
        vals = dict(vals)
        bom_type = vals.get('type')
        if not bom_type and len(self) == 1:
            bom_type = self.type
        bom_type = bom_type or 'normal'

        is_model_recipe = vals.get('furniture_is_model_recipe')
        if 'furniture_is_model_recipe' not in vals and len(self) == 1:
            is_model_recipe = self.furniture_is_model_recipe
        is_visible_normal = bom_type == 'normal' and not is_model_recipe

        # Backward-compatible API: older callers used furniture_model_id when
        # creating/editing the one visible normal BoM.  On that neutral master
        # it now means "which recipe is being viewed"; only hidden recipes and
        # Kits carry a real furniture_model_id.
        if is_visible_normal and 'furniture_model_id' in vals:
            vals.setdefault(
                'furniture_recipe_model_id',
                vals.get('furniture_model_id'),
            )
            vals['furniture_model_id'] = False

        selected_model_field = (
            'furniture_recipe_model_id'
            if is_visible_normal
            else 'furniture_model_id'
        )
        selected_model = (
            self.env['furniture.product.model'].browse(
                vals.get(selected_model_field)
            )
            if vals.get(selected_model_field)
            else False
        )
        if (
            is_visible_normal
            and not selected_model
            and selected_model_field not in vals
            and len(self) == 1
        ):
            selected_model = self.furniture_recipe_model_id
        product_id = vals.get('furniture_product_id')
        if product_id:
            product = self.env['product.product'].browse(product_id)
            if (
                is_visible_normal
                and not selected_model
                and product.furniture_model_id
            ):
                selected_model = product.furniture_model_id
                vals['furniture_recipe_model_id'] = selected_model.id
            if bom_type == 'phantom' and selected_model:
                product = self._furniture_product_for_selected_model(
                    product,
                    selected_model,
                )
                vals['furniture_product_id'] = product.id
                vals['product_tmpl_id'] = product.product_tmpl_id.id
                vals['furniture_family_id'] = selected_model.family_id.id
            if bom_type == 'phantom':
                vals.setdefault('furniture_family_id', product.furniture_family_id.id)
                vals.setdefault('furniture_model_id', product.furniture_model_id.id)
        if selected_model:
            vals['furniture_family_id'] = selected_model.family_id.id
        elif selected_model_field in vals:
            vals['furniture_family_id'] = False
        return vals

    def _sync_furniture_product_classification(self):
        for bom in self:
            if bom.type != 'phantom':
                continue
            product = bom.furniture_product_id
            if not product:
                continue
            if bom.furniture_model_id and bom.furniture_model_id.family_id != bom.furniture_family_id:
                raise ValidationError(
                    _('الموديل المختار لا يتبع الفئة الرئيسية المحددة في الريسيبي.')
                )
            template = product.product_tmpl_id
            target_vals = {
                'furniture_family_id': bom.furniture_family_id.id,
                'furniture_model_id': bom.furniture_model_id.id,
            }
            if (
                template.furniture_family_id != bom.furniture_family_id
                or template.furniture_model_id != bom.furniture_model_id
            ):
                template.with_context(furniture_skip_bom_classification_sync=True).write(target_vals)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            source_bom = (
                self.browse(vals.get('furniture_source_bom_id')).exists()
                if vals.get('furniture_source_bom_id') else False
            )
            is_visible_normal = (
                vals.get('type', 'normal') == 'normal'
                and not vals.get('furniture_is_model_recipe')
            )
            selected_model_id = (
                vals.get('furniture_recipe_model_id')
                or (vals.get('furniture_model_id') if is_visible_normal else False)
            )
            source_model = (
                source_bom.furniture_recipe_model_id
                if source_bom
                and source_bom.type == 'normal'
                and not source_bom.furniture_is_model_recipe
                else source_bom.furniture_model_id
            ) if source_bom else self.env['furniture.product.model']
            if (
                source_bom
                and selected_model_id
                and source_model
                and source_model.id != selected_model_id
            ):
                vals['furniture_stage_material_line_ids'] = [(5, 0, 0)]
                vals['furniture_stage_ids'] = [(5, 0, 0)]
                vals['bom_line_ids'] = [(5, 0, 0)]
        records = super().create([
            self._prepare_furniture_classification_vals(vals)
            for vals in vals_list
        ])
        records._sync_furniture_product_classification()
        records._sync_furniture_model_recipe()
        return records

    def write(self, vals):
        vals = dict(vals)
        requested_fields = set(vals)
        if (
            len(self) == 1
            and self.type == 'normal'
            and not self.furniture_is_model_recipe
            and (
                self.furniture_recipe_model_id
                or self.with_context(active_test=False).search_count([
                    ('furniture_parent_bom_id', '=', self.id),
                    ('furniture_is_model_recipe', '=', True),
                ])
            )
            and requested_fields & self._furniture_recipe_content_field_names()
            and not self.env.context.get('furniture_allow_master_recipe_content_write')
        ):
            raise ValidationError(_(
                'خامات ومقاسات المنتج محفوظة لكل موديل على حدة. '
                'افتح الموديل المطلوب من حقل الموديل ثم عدّل ريسيبيه.'
            ))
        classification_fields = {
            'type',
            'furniture_is_model_recipe',
            'furniture_parent_bom_id',
            'furniture_product_id',
            'furniture_model_id',
            'furniture_recipe_model_id',
        }
        if len(self) > 1 and classification_fields & set(vals):
            result = True
            for bom in self:
                result = bom.write(dict(vals)) and result
            return result
        if (
            len(self) == 1
            and self.type == 'normal'
            and not self.furniture_is_model_recipe
            and 'furniture_model_id' in vals
            and 'furniture_recipe_model_id' not in vals
        ):
            vals['furniture_recipe_model_id'] = vals.pop('furniture_model_id')
        if (
            len(self) == 1
            and self.furniture_is_model_recipe
            and not self.furniture_recipe_ready
            and requested_fields & self._furniture_recipe_content_field_names()
        ):
            vals['furniture_recipe_ready'] = True
        vals = self._prepare_furniture_classification_vals(vals)
        sync_kit_components = bool({'type', 'furniture_model_id'} & set(vals))
        result = super().write(vals)
        if not self.env.context.get('furniture_skip_product_classification_sync'):
            self._sync_furniture_product_classification()
        if sync_kit_components:
            self.filtered(lambda bom: bom.type == 'phantom').bom_line_ids._furniture_sync_kit_component_products()
        metadata_fields = {
            'product_tmpl_id',
            'furniture_product_id',
            'product_qty',
            'product_uom_id',
            'company_id',
            'active',
        }
        if requested_fields & metadata_fields:
            self._sync_furniture_model_recipe_metadata()
        return result

    @api.constrains(
        'furniture_family_id',
        'furniture_model_id',
        'furniture_recipe_model_id',
    )
    def _check_furniture_bom_model_family(self):
        for bom in self:
            if bom.furniture_model_id and bom.furniture_model_id.family_id != bom.furniture_family_id:
                raise ValidationError(_('الموديل المختار لا يتبع الفئة الرئيسية المحددة في الريسيبي.'))
            if (
                bom.furniture_recipe_model_id
                and bom.furniture_recipe_model_id.family_id != bom.furniture_family_id
            ):
                raise ValidationError(_(
                    'موديل الريسيبي المعروض لا يتبع الفئة الرئيسية المحددة.'
                ))

    @api.constrains('type', 'furniture_is_model_recipe', 'furniture_model_id')
    def _check_visible_normal_bom_is_model_neutral(self):
        for bom in self:
            if (
                bom.type == 'normal'
                and not bom.furniture_is_model_recipe
                and bom.furniture_model_id
            ):
                raise ValidationError(_(
                    'قائمة المواد الرئيسية مرتبطة بالصنف فقط؛ '
                    'الموديل يُحفظ داخل ريسيبي الموديل الداخلي.'
                ))

    @api.constrains(
        'type',
        'furniture_is_model_recipe',
        'furniture_recipe_model_id',
    )
    def _check_model_recipe_master_has_navigator_model(self):
        for bom in self:
            if (
                bom.type == 'normal'
                and not bom.furniture_is_model_recipe
                and not bom.furniture_recipe_model_id
                and self.with_context(active_test=False).search_count([
                    ('furniture_parent_bom_id', '=', bom.id),
                    ('furniture_is_model_recipe', '=', True),
                ])
            ):
                raise ValidationError(_(
                    'لا يمكن مسح موديل العرض ما دام للمنتج ريسيبيات موديلات محفوظة.'
                ))

    @api.constrains('type', 'furniture_model_id')
    def _check_furniture_kit_model(self):
        for bom in self:
            if bom.type == 'phantom' and not bom.furniture_model_match_key:
                raise ValidationError(_(
                    'حدد الموديل للطقم قبل إضافة منتجاته.'
                ))

    @api.constrains(
        'furniture_is_model_recipe',
        'furniture_parent_bom_id',
        'furniture_product_id',
        'furniture_model_id',
        'furniture_recipe_model_id',
        'product_tmpl_id',
        'company_id',
        'type',
    )
    def _check_furniture_hidden_model_recipe(self):
        for bom in self:
            if not bom.furniture_is_model_recipe:
                if bom.furniture_parent_bom_id:
                    raise ValidationError(_('الريسيبي الداخلي فقط يرتبط بريسيبي منتج رئيسي.'))
                continue
            parent = bom.furniture_parent_bom_id
            if not parent or parent.furniture_is_model_recipe:
                raise ValidationError(_('ريسيبي الموديل الداخلي يحتاج ريسيبي منتج رئيسي صحيح.'))
            if bom.type != 'normal' or parent.type != 'normal':
                raise ValidationError(_('ريسيبي الموديل الداخلي يجب أن يكون وصفة تصنيع عادية.'))
            if not bom.furniture_model_id:
                raise ValidationError(_('حدد الموديل قبل حفظ ريسيبي خاماته.'))
            if bom.furniture_recipe_model_id:
                raise ValidationError(_('ريسيبي الموديل الداخلي لا يحمل اختيار عرض مستقل.'))
            if bom.furniture_product_id != parent.furniture_product_id:
                raise ValidationError(_('منتج ريسيبي الموديل لا يطابق المنتج الرئيسي.'))
            if bom.product_tmpl_id != parent.product_tmpl_id:
                raise ValidationError(_('قالب منتج ريسيبي الموديل لا يطابق المنتج الرئيسي.'))
            if bom.company_id != parent.company_id:
                raise ValidationError(_('شركة ريسيبي الموديل لا تطابق الريسيبي الرئيسي.'))

    @api.constrains(
        'active', 'type', 'furniture_product_id', 'furniture_model_id',
        'company_id', 'furniture_is_model_recipe', 'furniture_parent_bom_id',
    )
    def _check_unique_furniture_product_model_recipe(self):
        for bom in self:
            if (
                not bom.active
                or bom.type != 'normal'
                or not bom.furniture_is_model_recipe
                or not bom.furniture_product_id
                or not bom.furniture_model_id
            ):
                continue
            duplicate = self.search([
                ('id', '!=', bom.id),
                ('active', '=', True),
                ('type', '=', 'normal'),
                ('furniture_is_model_recipe', '=', True),
                ('furniture_product_id', '=', bom.furniture_product_id.id),
                ('furniture_model_id', '=', bom.furniture_model_id.id),
                ('company_id', '=', bom.company_id.id or False),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    'يوجد بالفعل ريسيبي تصنيع نشط للمنتج %(product)s مع الموديل %(model)s.'
                ) % {
                    'product': bom.furniture_product_id.display_name,
                    'model': bom.furniture_model_id.display_name,
                })


class FurnitureMrpProductionLine(models.Model):
    _inherit = 'furniture.mrp.production.line'

    furniture_family_id = fields.Many2one(
        related='product_id.furniture_family_id',
        string='الفئة الرئيسية',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        related='product_id.furniture_model_id',
        string='الموديل',
        readonly=True,
    )
    furniture_classification_label = fields.Char(
        related='product_id.furniture_classification_label',
        string='التصنيف',
        readonly=True,
    )


class FurnitureMrpMPSLine(models.Model):
    _inherit = 'furniture.mrp.mps.line'

    furniture_family_id = fields.Many2one(
        related='product_id.furniture_family_id',
        string='الفئة الرئيسية',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        related='product_id.furniture_model_id',
        string='الموديل',
        readonly=True,
    )
    furniture_classification_label = fields.Char(
        related='product_id.furniture_classification_label',
        string='التصنيف',
        readonly=True,
    )


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    furniture_tailoring_material_kind = fields.Selection(
        related='product_id.furniture_tailoring_material_kind',
        string='نوع الخامة',
        readonly=True,
    )
    furniture_model_id = fields.Many2one(
        related='product_id.furniture_model_id',
        string='الموديل',
        readonly=True,
    )
    furniture_dimension_label = fields.Char(
        related='product_id.furniture_dimension_label',
        string='المقاس',
        readonly=True,
    )
