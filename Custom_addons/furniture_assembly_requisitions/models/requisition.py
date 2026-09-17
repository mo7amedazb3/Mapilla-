import math
from odoo import _, Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_SELECTION,
)


STOREKEEPER = 'furniture_mrp.group_furniture_mrp_storekeeper'
ITEMS = ('nail', 'glue', 'sandpaper')
SHARED_HALL_STAGES = tuple(
    stage_code for stage_code, _label in FURNITURE_STAGE_SELECTION
)
STAGE_LABELS = dict(FURNITURE_STAGE_SELECTION)
STAGE_GROUPS = {
    stage_code: 'furniture_mrp.group_furniture_mrp_supervisor_%s' % stage_code
    for stage_code in SHARED_HALL_STAGES
}
STAGE_HALL_XMLIDS = {
    'priming': 'furniture_mrp.location_stage_priming_wip',
    'painting': 'furniture_mrp.location_stage_painting_wip',
    'carpentry': 'furniture_mrp.location_stage_carpentry_wip',
    'bases': 'furniture_mrp.location_stage_bases_wip',
    'finishing': 'furniture_mrp.location_stage_finishing_wip',
    'tailoring': 'furniture_mrp.location_stage_tailoring_wip',
    'upholstery': 'furniture_mrp.location_stage_upholstery_wip',
    'packaging': 'furniture_mrp.location_stage_packaging',
}


class AssemblySupervisorMaterial(models.Model):
    _name = 'furniture.assembly.supervisor.material'
    _description = 'خامات الطلب الخارجي المسموحة للمشرف'
    _order = 'stage_code, supervisor_id'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True,
        default=lambda self: self.env.company,
    )
    supervisor_id = fields.Many2one(
        'res.users', string='المشرف', required=True, ondelete='cascade',
        domain="[('share', '=', False), ('active', '=', True), "
               "('company_ids', 'in', company_id)]",
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION, string='المرحلة', required=True,
    )
    product_ids = fields.Many2many(
        'product.product',
        'furniture_assembly_supervisor_material_product_rel',
        'policy_id', 'product_id', string='الخامات المسموحة',
        domain="[('is_storable', '=', True), ('active', '=', True)]",
    )

    _sql_constraints = [(
        'supervisor_stage_company_unique',
        'unique(company_id, supervisor_id, stage_code)',
        'يوجد إعداد خامات لهذا المشرف والمرحلة بالفعل.',
    )]

    @api.constrains('company_id', 'supervisor_id', 'stage_code', 'product_ids')
    def _check_policy(self):
        for policy in self:
            if policy.stage_code not in SHARED_HALL_STAGES:
                raise ValidationError(_('المرحلة المختارة لا تستخدم طلب الاذونات.'))
            if (
                policy.supervisor_id.share
                or not policy.supervisor_id.active
                or policy.company_id not in policy.supervisor_id.company_ids
            ):
                raise ValidationError(_('اختر مشرفًا داخليًا فعالًا تابعًا للشركة.'))
            if not policy.supervisor_id.has_group(STAGE_GROUPS[policy.stage_code]):
                raise ValidationError(_('المستخدم المختار ليس مشرفًا على هذه المرحلة.'))
            invalid = policy.product_ids.filtered(
                lambda product: not product.active or not product.is_storable
            )
            if invalid:
                raise ValidationError(_('لا يمكن السماح إلا بخامات مخزنية فعالة.'))

    @api.model
    def _allowed_products(self, company, supervisor, stage_code):
        if stage_code not in SHARED_HALL_STAGES:
            return self.env['product.product']
        policy = self.sudo().search([
            ('company_id', '=', company.id),
            ('supervisor_id', '=', supervisor.id),
            ('stage_code', '=', stage_code),
        ], limit=1)
        return policy.product_ids.filtered(lambda product: (
            product.active and product.is_storable
        ))


class AssemblySupplyConfig(models.Model):
    _name = 'furniture.assembly.supply.config'
    _description = 'إعداد مخزن طلبات أذونات المراحل'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True)
    source_id = fields.Many2one('stock.location', required=True, check_company=True)
    hall_id = fields.Many2one('stock.location', required=True, check_company=True)
    nail_product_id = fields.Many2one('product.product', required=True, check_company=True)
    glue_product_id = fields.Many2one('product.product', required=True, check_company=True)
    sandpaper_product_id = fields.Many2one('product.product', required=True, check_company=True)
    nail_uom_id = fields.Many2one('uom.uom', required=True)
    glue_uom_id = fields.Many2one('uom.uom', required=True)
    sandpaper_uom_id = fields.Many2one('uom.uom', required=True)
    _sql_constraints = [('company_unique', 'unique(company_id)', 'إعداد واحد لكل شركة.')]

    @api.constrains('source_id', 'hall_id', 'company_id')
    def _check_locations(self):
        for config in self:
            if (config.source_id == config.hall_id
                    or config.source_id.usage != 'internal'
                    or config.hall_id.usage != 'internal'):
                raise ValidationError(_('يجب اختيار مخزن وصالة داخليين مختلفين.'))

    @api.model
    def _for_company(self, company):
        if company not in self.env.companies:
            raise AccessError(_('الشركة غير مسموح بها.'))
        config = self.sudo().search([('company_id', '=', company.id)], limit=1)
        if not config:
            raise UserError(_('إعداد طلب الاذونات غير موجود لهذه الشركة.'))
        return config

    @api.model
    def _stock_quantity(self, product, quantity, uom):
        # Preserve the recipe-unit convention used by furniture_mrp, including
        # legacy recipe units that belong to a different category from Units.
        if uom.category_id == product.uom_id.category_id:
            return uom._compute_quantity(quantity, product.uom_id, round=False)
        return quantity

    @api.model
    def _lock_and_check_stock(self, company, location, quantities, protect_orders=False):
        """Serialize shared stock users and respect Odoo and MRP reservations."""
        Quant = self.env['stock.quant'].sudo().with_company(company)
        Reservation = self.env['furniture.mrp.material.reservation'].sudo()
        for product in sorted(quantities, key=lambda item: item.id):
            Reservation._lock_bucket(company, location, product)
            self.env.cr.execute(
                'SELECT id FROM stock_quant WHERE company_id=%s '
                'AND location_id=%s AND product_id=%s ORDER BY id FOR UPDATE',
                [company.id, location.id, product.id],
            )
            Quant.invalidate_model(['quantity', 'reserved_quantity'])
            available = Quant._get_available_quantity(product, location, strict=True)
            if protect_orders:
                available -= Reservation.protected_qty_for_other_productions(
                    company, location, product,
                )
            if float_compare(available, quantities[product], precision_digits=6) < 0:
                raise UserError(_(
                    'رصيد %s في %s غير كافٍ. المتاح غير المحجوز: %.3f، المطلوب: %.3f. '
                    'لم يتم صرف أو استهلاك أي خامات.'
                ) % (product.display_name, location.display_name, available, quantities[product]))


class AssemblyRequisition(models.Model):
    _name = 'furniture.assembly.requisition'
    _description = 'طلب إذن خامات مرحلة'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char('رقم الطلب', default='جديد', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    requested_by_id = fields.Many2one(
        'res.users', string='مقدم الطلب', readonly=True,
        default=lambda self: self.env.user,
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION, string='المرحلة',
        tracking=True, default=lambda self: self._default_stage_code(),
    )
    # Retained for compatibility with approved historical assembly requests.
    config_id = fields.Many2one(
        'furniture.assembly.supply.config', required=True, readonly=True,
        check_company=True,
        default=lambda self: self.env[
            'furniture.assembly.supply.config'
        ]._for_company(self.env.company),
    )
    nail_product_id = fields.Many2one(related='config_id.nail_product_id')
    glue_product_id = fields.Many2one(related='config_id.glue_product_id')
    sandpaper_product_id = fields.Many2one(related='config_id.sandpaper_product_id')
    nail_uom_id = fields.Many2one(related='config_id.nail_uom_id')
    glue_uom_id = fields.Many2one(related='config_id.glue_uom_id')
    sandpaper_uom_id = fields.Many2one(related='config_id.sandpaper_uom_id')
    source_id = fields.Many2one(
        'stock.location', string='مخزن الصرف', compute='_compute_locations',
    )
    hall_id = fields.Many2one(
        'stock.location', string='صالة الاستلام', compute='_compute_locations',
    )
    line_ids = fields.One2many(
        'furniture.assembly.requisition.line', 'requisition_id',
        string='الخامات المطلوبة', copy=False,
    )
    requested_items_summary = fields.Char(
        string='ملخص الخامات', compute='_compute_requested_items_summary',
    )
    nail_qty = fields.Float('مسمار 4 سم هواء', digits=(16, 3), tracking=True)
    glue_qty = fields.Float('غراء', digits=(16, 3), tracking=True)
    sandpaper_qty = fields.Float('فرخ سنفره 36 مقاس صغير', digits=(16, 3), tracking=True)
    state = fields.Selection([
        ('draft', 'مسودة'),
        ('pending', 'بانتظار أمين المخزن'),
        ('done', 'تم الصرف للصالة'),
        ('rejected', 'مرفوض'),
    ], string='الحالة', default='draft', required=True, readonly=True, tracking=True)
    submitted_at = fields.Datetime('تاريخ الإرسال', readonly=True)
    decided_at = fields.Datetime('تاريخ القرار', readonly=True)
    decided_by_id = fields.Many2one('res.users', string='أمين المخزن', readonly=True)
    move_ids = fields.One2many('stock.move', 'assembly_requisition_id', readonly=True)
    manual_product_ids = fields.Many2many(
        'product.product',
        'furniture_assembly_requisition_manual_product_rel',
        'requisition_id', 'product_id',
        string='أصناف التوريد اليدوي', readonly=True, copy=False,
    )
    can_submit = fields.Boolean(compute='_compute_permissions')
    can_decide = fields.Boolean(compute='_compute_permissions')

    def init(self):
        # Records from 18.0.1 were assembly-only and did not carry a stage.
        self.env.cr.execute(
            "UPDATE furniture_assembly_requisition "
            "SET stage_code='carpentry' WHERE stage_code IS NULL"
        )

    def _backfill_legacy_lines(self):
        """Expose pre-line-model request quantities in the current form."""
        requests = self.sudo()
        if not requests:
            requests = self.sudo().search([('line_ids', '=', False)])
        else:
            requests = requests.filtered(lambda request: not request.line_ids)

        Line = self.env['furniture.assembly.requisition.line'].sudo().with_context(
            stage_requisition_parent_write=True,
        )
        created = Line.browse()
        for request in requests:
            values_list = []
            for key in ITEMS:
                product = request[key + '_product_id']
                uom = request[key + '_uom_id']
                quantity = request[key + '_qty']
                if product and uom and quantity > 0:
                    values_list.append({
                        'requisition_id': request.id,
                        'product_id': product.id,
                        'product_uom_id': uom.id,
                        'requested_qty': quantity,
                    })
            if values_list:
                created |= Line.create(values_list)
        return created

    @api.model
    def _allowed_stage_codes(self, user=None):
        user = user or self.env.user
        return tuple(
            stage_code for stage_code in SHARED_HALL_STAGES
            if user.has_group(STAGE_GROUPS[stage_code])
        )

    @api.model
    def _default_stage_code(self):
        stages = self._allowed_stage_codes()
        return stages[0] if stages else False

    @api.model
    def _check_stage_access(self, stage_code):
        if stage_code not in SHARED_HALL_STAGES:
            raise AccessError(_('المرحلة المختارة لا تستخدم تطبيق طلب الاذونات.'))
        if not self.env.su and stage_code not in self._allowed_stage_codes():
            raise AccessError(_('لا تملك صلاحية إنشاء طلب لهذه المرحلة.'))
        return True

    @api.model
    def _recipe_item_values(self, stage_code, user=None, company=None):
        if stage_code not in SHARED_HALL_STAGES:
            return []
        user = user or self.env.user
        company = company or self.env.company
        products = self.env[
            'furniture.assembly.supervisor.material'
        ]._allowed_products(company, user, stage_code)
        result = []
        for product in products.sorted(lambda item: item.display_name):
            result.append({
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'requested_qty': 0.0,
            })
        return result

    @api.model
    def _manual_supply_product_ids(self, company, stage_code):
        """Products intentionally supplied through the hall top-up app.

        A positive line becomes manual as soon as its request is submitted.
        Rejected-only requests do not change the policy, while an approved
        request keeps the product manual for later production orders.
        """
        if stage_code not in SHARED_HALL_STAGES:
            return set()
        lines = self.env['furniture.assembly.requisition.line'].sudo().search([
            ('company_id', '=', company.id),
            ('requisition_id.stage_code', '=', stage_code),
            ('requisition_id.state', 'in', ('pending', 'done')),
            ('requested_qty', '>', 0),
        ])
        product_ids = set(lines.mapped('product_id').ids)
        approved = self.sudo().search([
            ('company_id', '=', company.id),
            ('stage_code', '=', stage_code),
            ('state', '=', 'done'),
        ])
        product_ids.update(approved.mapped('manual_product_ids').ids)
        product_ids.update(approved.mapped('move_ids.product_id').ids)

        # Requests created by the first assembly-only version stored the three
        # quantities on the header instead of line records.
        if stage_code == 'carpentry':
            historical = self.sudo().search([
                ('company_id', '=', company.id),
                ('stage_code', '=', stage_code),
                # Approved legacy requests are recovered from their immutable
                # stock moves above.  Their related config products may have
                # changed since approval and must not reclassify new products.
                ('state', '=', 'pending'),
            ])
            for request in historical.filtered(
                lambda item: not item.line_ids.filtered(
                    lambda line: line.requested_qty > 0
                )
            ):
                for key in ITEMS:
                    if request[key + '_qty'] > 0:
                        product_ids.add(request[key + '_product_id'].id)
        return product_ids

    @api.model
    def _validate_line_commands(self, stage_code, commands, user=None, company=None):
        def relational_id(value):
            value = getattr(value, 'id', value)
            if isinstance(value, dict):
                value = value.get('id')
            if isinstance(value, (list, tuple)):
                value = value[0] if value else False
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            return value

        expected_by_product = {
            item['product_id']: item
            for item in self._recipe_item_values(
                stage_code, user=user, company=company,
            )
        }
        for command in commands or []:
            operation = command[0]
            if operation == Command.CREATE:
                values = command[2]
                product_id = relational_id(values.get('product_id'))
                uom_id = relational_id(values.get('product_uom_id'))
                expected = expected_by_product.get(product_id)
                if not expected:
                    raise AccessError(_('الصنف ليس ضمن وصفات المرحلة المختارة.'))
                if uom_id != expected['product_uom_id']:
                    raise AccessError(_('وحدة الصنف ثابتة طبقًا لوصفات المرحلة.'))
            elif operation == Command.UPDATE:
                if set(command[2]) - {'requested_qty'}:
                    raise AccessError(_('يمكن تعديل الكمية المطلوبة فقط.'))
            elif operation not in (Command.DELETE, Command.UNLINK, Command.CLEAR):
                raise AccessError(_('لا يمكن ربط سطور خامات من طلب آخر.'))

    @api.model
    def default_get(self, field_list):
        values = super().default_get(field_list)
        stage_code = values.get('stage_code') or self._default_stage_code()
        if 'stage_code' in field_list:
            values['stage_code'] = stage_code
        if 'line_ids' in field_list and stage_code:
            values['line_ids'] = [
                Command.create(item) for item in self._recipe_item_values(stage_code)
            ]
        return values

    @api.onchange('stage_code')
    def _onchange_stage_code(self):
        for request in self:
            request.line_ids = [
                Command.clear(),
                *(
                    Command.create(item)
                    for item in request._recipe_item_values(request.stage_code)
                ),
            ]

    @api.depends('stage_code', 'config_id.source_id')
    def _compute_locations(self):
        for request in self:
            request.source_id = request.config_id.source_id
            xmlid = STAGE_HALL_XMLIDS.get(request.stage_code)
            request.hall_id = self.env.ref(
                xmlid, raise_if_not_found=False,
            ) if xmlid else False

    @api.depends('line_ids.requested_qty', 'line_ids.product_id')
    def _compute_requested_items_summary(self):
        for request in self:
            items = request.line_ids.filtered(lambda line: line.requested_qty > 0)
            request.requested_items_summary = '، '.join(
                '%s: %s' % (line.product_id.display_name, line.requested_qty)
                for line in items[:3]
            )
            if len(items) > 3:
                request.requested_items_summary += _(' + %s أصناف') % (len(items) - 3)

    @api.depends_context('uid')
    @api.depends('state', 'requested_by_id', 'stage_code')
    def _compute_permissions(self):
        allowed_stages = self._allowed_stage_codes()
        for request in self:
            request.can_submit = (
                request.stage_code in allowed_stages
                and request.requested_by_id == self.env.user
            )
            request.can_decide = self.env.user.has_group(STOREKEEPER)

    @api.constrains('nail_qty', 'glue_qty', 'sandpaper_qty')
    def _check_quantities(self):
        for request in self:
            if any(
                not math.isfinite(request[key + '_qty'])
                or request[key + '_qty'] < 0
                for key in ITEMS
            ):
                raise ValidationError(_('اكتب كميات موجبة أو صفر فقط.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self._allowed_stage_codes() and not self.env.su:
            raise AccessError(_('إنشاء الطلب متاح لمشرفي المراحل فقط.'))
        allowed = {key + '_qty' for key in ITEMS} | {
            'company_id', 'stage_code', 'line_ids',
        }
        prepared = []
        for vals in vals_list:
            self._validate_input_quantities(vals)
            if set(vals) - allowed:
                raise AccessError(_(
                    'الأصناف والوحدات وحالة الطلب ثابتة؛ أدخل الكميات فقط.'
                ))
            company = self.env['res.company'].browse(
                vals.get('company_id') or self.env.company.id
            )
            stage_code = vals.get('stage_code') or self._default_stage_code()
            self._check_stage_access(stage_code)
            if stage_code != 'carpentry' and any(
                key + '_qty' in vals for key in ITEMS
            ):
                raise AccessError(_(
                    'حقول طلب التجميع القديمة لا تُستخدم مع المراحل الأخرى.'
                ))
            self._validate_line_commands(
                stage_code, vals.get('line_ids'), user=self.env.user,
                company=company,
            )
            config = self.env['furniture.assembly.supply.config']._for_company(company)
            prepared.append(dict(
                vals,
                config_id=config.id,
                requested_by_id=self.env.uid,
                company_id=company.id,
                stage_code=stage_code,
                state='draft',
            ))
        records = super(
            AssemblyRequisition,
            self.with_context(stage_requisition_parent_write=True),
        ).create(prepared)
        for record in records:
            super(AssemblyRequisition, record).write({
                'name': 'AR/%06d' % record.id,
            })
        return records

    def write(self, vals):
        self._validate_input_quantities(vals)
        allowed = {key + '_qty' for key in ITEMS} | {'stage_code', 'line_ids'}
        if set(vals) - allowed:
            raise AccessError(_('لا يمكن تعديل بيانات الطلب الثابتة أو حالته مباشرة.'))
        for request in self:
            request._check_requester()
            if request.state != 'draft':
                raise UserError(_('لا يمكن تعديل الكميات بعد إرسال الطلب.'))
            if 'stage_code' in vals:
                request._check_stage_access(vals['stage_code'])
            request._validate_line_commands(
                vals.get('stage_code', request.stage_code),
                vals.get('line_ids'),
                user=request.requested_by_id,
                company=request.company_id,
            )
        return super(
            AssemblyRequisition,
            self.with_context(stage_requisition_parent_write=True),
        ).write(vals)

    @api.model
    def _validate_input_quantities(self, vals):
        for key in ITEMS:
            if key + '_qty' not in vals:
                continue
            try:
                value = float(vals[key + '_qty'])
            except (ValueError, TypeError):
                raise ValidationError(_('أدخل كمية رقمية صحيحة.'))
            if not math.isfinite(value) or value < 0:
                raise ValidationError(_('اكتب كميات موجبة أو صفر فقط.'))

    def unlink(self):
        for request in self:
            request._check_requester()
            if request.state != 'draft':
                raise UserError(_('لا يمكن حذف طلب أُرسل إلى المخزن.'))
        return super().unlink()

    def _check_requester(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.su and (
            self.stage_code not in self._allowed_stage_codes()
            or self.requested_by_id != self.env.user
        ):
            raise AccessError(_('هذا الطلب يخص مشرف المرحلة الذي أنشأه.'))

    def _lock(self):
        self.ensure_one()
        self.check_access('write')
        self.env.cr.execute(
            'SELECT id FROM furniture_assembly_requisition WHERE id=%s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset()

    def _notify(self, users, title, message):
        self.env['furniture.mrp.store.request']._send_bus_notification(
            users, title, message, sticky=True, play_sound=True,
            action_model=self._name, action_res_id=self.id,
            action_name=_('فتح الطلب'),
        )

    def _requested_quantities(self):
        self.ensure_one()
        Config = self.env['furniture.assembly.supply.config']
        quantities = {}
        positive_lines = self.line_ids.filtered(lambda line: line.requested_qty > 0)
        if positive_lines:
            for line in positive_lines:
                qty = Config._stock_quantity(
                    line.product_id, line.requested_qty, line.product_uom_id,
                )
                quantities[line.product_id] = quantities.get(line.product_id, 0.0) + qty
            return quantities
        # Historical assembly requests stored their quantities on the header.
        for key in ITEMS:
            product = self[key + '_product_id']
            qty = Config._stock_quantity(
                product, self[key + '_qty'], self[key + '_uom_id'],
            )
            if qty > 0:
                quantities[product] = quantities.get(product, 0.0) + qty
        return quantities

    def action_submit(self):
        self._check_requester()
        self._lock()
        if self.state == 'pending':
            return True
        if self.state != 'draft':
            raise UserError(_('لا يمكن إرسال الطلب في حالته الحالية.'))
        allowed_products = self.env[
            'furniture.assembly.supervisor.material'
        ]._allowed_products(
            self.company_id, self.requested_by_id, self.stage_code,
        )
        if not allowed_products:
            raise UserError(_(
                'لم يحدد مدير الإنتاج خامات مسموحة لك في هذه المرحلة.'
            ))
        quantities = self._requested_quantities()
        forbidden = set(quantities) - set(allowed_products)
        if forbidden:
            raise AccessError(_(
                'إحدى الخامات المطلوبة غير مسموح لك بطلبها من الخارج.'
            ))
        if not quantities:
            raise UserError(_('أدخل كمية لصنف واحد على الأقل.'))
        if not self.hall_id:
            raise UserError(_('صالة المرحلة غير معدّة. حدّث موديول MRP II.'))
        users = self.env.ref(STOREKEEPER).sudo().users.filtered(
            lambda user: (
                user.active and not user.share
                and self.company_id in user.company_ids
            )
        )
        if not users:
            raise UserError(_('لا يوجد أمين مخزن للشركة لاستلام الطلب.'))
        super(AssemblyRequisition, self).write({
            'state': 'pending',
            'submitted_at': fields.Datetime.now(),
            'manual_product_ids': [Command.set(
                [product.id for product in quantities]
            )],
        })
        stage_label = STAGE_LABELS.get(self.stage_code, self.stage_code)
        for user in users:
            self.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=_('طلب خامات %s - %s') % (stage_label, self.name),
            )
        self._notify(
            users, _('طلب إذن %s جديد') % stage_label, self.name,
        )
        return True

    def _check_storekeeper(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.su and not self.env.user.has_group(STOREKEEPER):
            raise AccessError(_('اعتماد أو رفض الإذن متاح لأمين المخزن فقط.'))

    def _decide(self, state):
        super(AssemblyRequisition, self).write({
            'state': state,
            'decided_at': fields.Datetime.now(),
            'decided_by_id': self.env.uid,
        })
        self.sudo().activity_feedback(['mail.mail_activity_data_todo'])
        self._notify(
            self.requested_by_id,
            _('نتيجة طلب إذن %s') % STAGE_LABELS.get(
                self.stage_code, self.stage_code,
            ),
            _('%s: تم نقل الخامات إلى صالة المرحلة.') % self.name
            if state == 'done' else _('%s: تم رفض الطلب.') % self.name,
        )

    def action_approve(self):
        self._check_storekeeper()
        self._lock()
        if self.state == 'done':
            return True
        if self.state != 'pending':
            raise UserError(_('الطلب ليس بانتظار أمين المخزن.'))
        with self.env.cr.savepoint():
            quantities = self._requested_quantities()
            Config = self.env['furniture.assembly.supply.config']
            Config._lock_and_check_stock(
                self.company_id, self.source_id, quantities,
                protect_orders=True,
            )
            moves = self.env['stock.move'].sudo().with_company(
                self.company_id
            ).create([{
                'name': '%s - خامات صالة %s' % (
                    self.name,
                    STAGE_LABELS.get(self.stage_code, self.stage_code),
                ),
                'origin': self.name,
                'company_id': self.company_id.id,
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': qty,
                'location_id': self.source_id.id,
                'location_dest_id': self.hall_id.id,
                'assembly_requisition_id': self.id,
            } for product, qty in quantities.items()])
            moves._action_confirm(merge=False)
            moves._action_assign()
            for move in moves:
                move.quantity = move.product_uom_qty
            moves.write({'picked': True})
            moves._action_done()
            self._decide('done')
        return True

    def action_reject(self):
        self._check_storekeeper()
        self._lock()
        if self.state == 'rejected':
            return True
        if self.state != 'pending':
            raise UserError(_('الطلب ليس بانتظار أمين المخزن.'))
        self._decide('rejected')
        return True


class AssemblyRequisitionLine(models.Model):
    _name = 'furniture.assembly.requisition.line'
    _description = 'سطر خامة طلب إذن مرحلة'
    _order = 'product_id, id'
    _check_company_auto = True

    requisition_id = fields.Many2one(
        'furniture.assembly.requisition', required=True, ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='requisition_id.company_id', store=True, index=True,
    )
    product_id = fields.Many2one(
        'product.product', string='الصنف', required=True, readonly=True,
        check_company=True, ondelete='restrict',
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة', required=True, readonly=True,
        ondelete='restrict',
    )
    requested_qty = fields.Float(
        'الكمية المطلوبة', digits=(16, 3), default=0.0,
    )

    @api.constrains('requested_qty')
    def _check_requested_qty(self):
        for line in self:
            if not math.isfinite(line.requested_qty) or line.requested_qty < 0:
                raise ValidationError(_('اكتب كميات موجبة أو صفر فقط.'))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('stage_requisition_parent_write'):
            return super().create(vals_list)
        for vals in vals_list:
            request = self.env['furniture.assembly.requisition'].browse(
                vals.get('requisition_id')
            )
            if not request or request.state != 'draft':
                raise AccessError(_('لا يمكن إضافة خامات إلا إلى طلب مسودة.'))
            request._check_requester()
            expected_by_product = {
                item['product_id']: item
                for item in request._recipe_item_values(
                    request.stage_code,
                    user=request.requested_by_id,
                    company=request.company_id,
                )
            }
            expected = expected_by_product.get(vals.get('product_id'))
            if not expected:
                raise AccessError(_('الصنف ليس ضمن وصفات المرحلة المختارة.'))
            if vals.get('product_uom_id') != expected['product_uom_id']:
                raise AccessError(_('وحدة الصنف ثابتة طبقًا لوصفات المرحلة.'))
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - {'requested_qty'}:
            raise AccessError(_('يمكن تعديل الكمية المطلوبة فقط.'))
        for line in self:
            line.requisition_id._check_requester()
            if line.requisition_id.state != 'draft':
                raise UserError(_('لا يمكن تعديل الكميات بعد إرسال الطلب.'))
        return super().write(vals)

    def unlink(self):
        for line in self:
            line.requisition_id._check_requester()
            if line.requisition_id.state != 'draft':
                raise UserError(_('لا يمكن حذف خامة بعد إرسال الطلب.'))
        return super().unlink()


class StockMove(models.Model):
    _inherit = 'stock.move'

    assembly_requisition_id = fields.Many2one(
        'furniture.assembly.requisition', readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
