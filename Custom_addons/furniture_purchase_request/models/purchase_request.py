# -*- coding: utf-8 -*-

from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class FurniturePurchaseRequest(models.Model):
    _name = 'furniture.purchase.request'
    _description = 'Warehouse Purchase Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(
        string='رقم الطلب', default=lambda self: _('New'), readonly=True,
        copy=False, index=True, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True,
        default=lambda self: self.env.company, index=True,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='المخزن', required=True, check_company=True,
        default=lambda self: self._default_stock_warehouse(), tracking=True,
    )
    stock_location_id = fields.Many2one(
        'stock.location', string='موقع الاستلام',
        related='warehouse_id.lot_stock_id', readonly=True,
    )
    requested_by_id = fields.Many2one(
        'res.users', string='مقدم الطلب', required=True, readonly=True,
        default=lambda self: self.env.user, tracking=True,
    )
    state = fields.Selection(
        [
            ('draft', 'مسودة'),
            ('requested', 'تم الإرسال للمشتريات'),
            ('rfq_created', 'تم إنشاء عروض الأسعار'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة', default='draft', required=True, readonly=True,
        copy=False, index=True, tracking=True,
    )
    line_ids = fields.One2many(
        'furniture.purchase.request.line', 'request_id',
        string='الخامات', copy=True,
    )
    note = fields.Text(string='ملاحظات')
    purchase_order_ids = fields.Many2many(
        'purchase.order', 'furniture_purchase_request_order_rel',
        'request_id', 'purchase_order_id', string='عروض الأسعار',
        readonly=True, copy=False, check_company=True,
    )
    rfq_count = fields.Integer(
        string='عدد عروض الأسعار', compute='_compute_rfq_count',
    )
    can_create_rfqs = fields.Boolean(
        compute='_compute_can_create_rfqs',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج', readonly=True,
        copy=False, index=True, check_company=True, ondelete='set null',
    )
    production_shortage_managed = fields.Boolean(
        string='طلب عجز مُدار من فحص المواد', readonly=True, copy=False,
        index=True,
    )

    @api.model
    def _stock_warehouse_for_company(self, company):
        """Return this installation's main Stock warehouse without fixed DB ids."""
        company = company or self.env.company
        main_warehouse = self.env.ref('stock.warehouse0', raise_if_not_found=False)
        if main_warehouse and main_warehouse.company_id == company:
            return main_warehouse

        return self.env['stock.warehouse'].search([
            ('company_id', '=', company.id),
        ], order='id', limit=1)

    @api.model
    def _default_stock_warehouse(self):
        return self._stock_warehouse_for_company(self.env.company)

    @api.model
    def _user_can_submit(self):
        return (
            self.env.user.has_group('furniture_mrp.group_furniture_mrp_storekeeper')
            or self.env.user.has_group('stock.group_stock_manager')
        )

    @api.model
    def _user_can_process_purchase(self):
        is_purchase_manager = self.env.user.has_group('purchase.group_purchase_manager')
        is_purchase_user = self.env.user.has_group('purchase.group_purchase_user')
        is_storekeeper = self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_storekeeper'
        )
        return is_purchase_manager or (is_purchase_user and not is_storekeeper)

    @api.depends('purchase_order_ids')
    def _compute_rfq_count(self):
        for request in self:
            request.rfq_count = len(request.purchase_order_ids)

    @api.depends_context('uid')
    def _compute_can_create_rfqs(self):
        can_process = self._user_can_process_purchase()
        for request in self:
            request.can_create_rfqs = can_process

    @api.constrains('warehouse_id', 'company_id')
    def _check_stock_warehouse(self):
        for request in self:
            expected = request._stock_warehouse_for_company(request.company_id)
            if not expected:
                raise ValidationError(_('لا يوجد مخزن Stock للشركة المختارة.'))
            if request.warehouse_id != expected:
                raise ValidationError(_(
                    'طلبات أمين المخزن مرتبطة بمخزن Stock فقط: %s.',
                    expected.display_name,
                ))

    @api.constrains('state', 'purchase_order_ids', 'line_ids')
    def _check_rfq_state_integrity(self):
        for request in self.filtered(lambda rec: rec.state == 'rfq_created'):
            linked_orders = request.line_ids.mapped('purchase_order_id')
            if (
                not request.line_ids
                or len(linked_orders) == 0
                or request.line_ids.filtered(lambda line: not line.purchase_order_id)
                or set(linked_orders.ids) != set(request.purchase_order_ids.ids)
            ):
                raise ValidationError(_(
                    'لا يمكن اعتبار الطلب مكتملًا قبل ربط كل خامة بعرض السعر الخاص بها.'
                ))
            invalid_orders = linked_orders.filtered(lambda order: (
                order.company_id != request.company_id
                or order.picking_type_id != request.warehouse_id.in_type_id
                or order.origin != request.name
            ))
            if invalid_orders:
                raise ValidationError(_(
                    'عروض الأسعار المرتبطة يجب أن تخص نفس الشركة ومخزن Stock والطلب.'
                ))

    @api.model_create_multi
    def create(self, vals_list):
        if not self._user_can_submit():
            raise AccessError(_('إنشاء طلب الخامات متاح لأمين المخزن فقط.'))
        prepared_vals = []
        for incoming_vals in vals_list:
            vals = dict(incoming_vals)
            company = self.env['res.company'].browse(
                vals.get('company_id') or self.env.company.id
            )
            if company not in self.env.companies:
                raise AccessError(_('ليس لديك صلاحية إنشاء طلب لهذه الشركة.'))
            if vals.get('purchase_order_ids'):
                raise AccessError(_('يتم ربط عروض الأسعار من قسم المشتريات فقط.'))
            warehouse = self._stock_warehouse_for_company(company)
            if not warehouse:
                raise UserError(_('لا يوجد مخزن Stock للشركة المختارة.'))
            if vals.get('warehouse_id') and vals['warehouse_id'] != warehouse.id:
                raise UserError(_('طلب الشراء يجب أن يكون على مخزن Stock فقط.'))
            vals['warehouse_id'] = warehouse.id
            vals['requested_by_id'] = self.env.user.id
            vals.setdefault('state', 'draft')
            if vals['state'] != 'draft':
                raise UserError(_('يجب إنشاء طلب الشراء في حالة مسودة.'))
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code(
                    'furniture.purchase.request'
                ) or _('New')
            prepared_vals.append(vals)
        return super().create(prepared_vals)

    @api.model
    def _system_create_material_shortage_request(self, production):
        """Create the production shortage request without exposing an RPC bypass."""
        production.ensure_one()
        company = production.company_id
        warehouse = self._stock_warehouse_for_company(company)
        if not warehouse:
            raise UserError(_('لا يوجد مخزن Stock لشركة أمر الإنتاج.'))
        requester = (
            production.materials_checked_by_id
            or production.responsible_id
            or self.env.user
        )
        system_model = self.sudo().with_company(company)
        name = self.env['ir.sequence'].with_company(company).next_by_code(
            'furniture.purchase.request'
        ) or _('New')
        request = super(FurniturePurchaseRequest, system_model).create({
            'name': name,
            'company_id': company.id,
            'warehouse_id': warehouse.id,
            'requested_by_id': requester.id,
            'state': 'requested',
            'production_id': production.id,
            'production_shortage_managed': True,
            'note': _(
                'تم إنشاء الطلب تلقائيًا من فحص خامات أمر الإنتاج %s.',
                production.name,
            ),
        })
        request.message_post(body=_(
            'تم إرسال عجز خامات أمر الإنتاج %s إلى قسم المشتريات.',
            production.name,
        ))
        return request

    def _system_cancel_material_shortage_request(self):
        """Cancel only an open request generated by the material checker."""
        for request in self.sudo():
            if (
                request.production_shortage_managed
                and request.state in ('draft', 'requested')
            ):
                super(FurniturePurchaseRequest, request).write({
                    'state': 'cancelled',
                })
                request.message_post(body=_(
                    'تم إلغاء الطلب تلقائيًا لعدم وجود عجز شراء حالي.'
                ))
        return True

    def write(self, vals):
        vals = dict(vals)
        protected_fields = {
            'name', 'company_id', 'warehouse_id', 'requested_by_id',
            'state', 'purchase_order_ids', 'production_id',
            'production_shortage_managed',
        }
        if protected_fields & vals.keys():
            raise AccessError(_(
                'رقم الطلب والشركة والمخزن والحالة وروابط RFQ تُدار من النظام فقط.'
            ))

        if {'line_ids', 'note'} & vals.keys():
            for request in self:
                if request.state == 'draft':
                    if not request._user_can_submit():
                        raise AccessError(_('تعديل المسودة متاح لأمين المخزن فقط.'))
                    continue
                if request.state == 'requested' and request._user_can_process_purchase():
                    continue
                raise UserError(_('لا يمكن تعديل هذا الطلب في حالته الحالية.'))
        return super().write(vals)

    def _lock_workflow_rows(self):
        """Serialize workflow actions; private methods are not exposed through RPC."""
        if not self:
            return
        self.flush_recordset(['state'])
        self.env.cr.execute(
            'SELECT id FROM furniture_purchase_request '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(self.ids)],
        )
        self.invalidate_recordset(['state', 'purchase_order_ids', 'line_ids'])
        self.mapped('line_ids').invalidate_recordset([
            'product_id', 'quantity', 'product_uom_id', 'supplier_id',
            'suggested_supplier_id', 'purchase_order_id',
        ])

    def _set_workflow_state(self, state):
        """Write a validated state from a public workflow action only."""
        return super(FurniturePurchaseRequest, self).write({'state': state})

    def _mark_rfq_created(self, purchase_orders):
        self.ensure_one()
        return super(FurniturePurchaseRequest, self).write({
            'purchase_order_ids': [Command.set(purchase_orders.ids)],
            'state': 'rfq_created',
        })

    def unlink(self):
        if not self._user_can_submit():
            raise AccessError(_('حذف طلب الخامات متاح لإدارة المخزن فقط.'))
        if any(request.state != 'draft' for request in self):
            raise UserError(_('يمكن حذف طلبات الشراء المسودة فقط.'))
        return super().unlink()

    def action_submit(self):
        if not self._user_can_submit():
            raise AccessError(_('إرسال الطلب متاح لأمين المخزن فقط.'))
        self._lock_workflow_rows()
        for request in self:
            if request.state != 'draft':
                raise UserError(_('يمكن إرسال الطلب المسودة فقط.'))
            if not request.line_ids:
                raise UserError(_('أضف خامة واحدة على الأقل قبل إرسال الطلب.'))
            invalid_lines = request.line_ids.filtered(lambda line: line.quantity <= 0)
            if invalid_lines:
                raise UserError(_('كل كميات طلب الشراء يجب أن تكون أكبر من صفر.'))
            request._set_workflow_state('requested')
            request.message_post(body=_('تم إرسال طلب الخامات إلى قسم المشتريات.'))
        return True

    def _ensure_purchase_processor(self):
        if not self._user_can_process_purchase():
            raise AccessError(_('إنشاء عروض الأسعار متاح لقسم المشتريات فقط.'))

    def action_create_rfqs(self):
        self.ensure_one()
        self._ensure_purchase_processor()
        self._lock_workflow_rows()
        if self.state != 'requested':
            raise UserError(_('يجب أن يرسل أمين المخزن الطلب قبل إنشاء RFQ.'))
        if not self.line_ids:
            raise UserError(_('لا توجد خامات في الطلب.'))
        if self.line_ids.filtered('purchase_order_id'):
            raise UserError(_('تم إنشاء عرض سعر لبعض سطور هذا الطلب بالفعل.'))

        missing_supplier = self.line_ids.filtered(
            lambda line: not (line.supplier_id or line.suggested_supplier_id)
        )
        if missing_supplier:
            names = ', '.join(missing_supplier.mapped('product_id.display_name'))
            raise UserError(_(
                'حدد المورد للمواد التالية قبل إنشاء RFQ: %s', names,
            ))

        grouped_lines = defaultdict(lambda: self.env['furniture.purchase.request.line'])
        for line in self.line_ids:
            supplier = line.supplier_id or line.suggested_supplier_id
            grouped_lines[supplier.id] |= line

        purchase_orders = self.env['purchase.order']
        PurchaseOrder = self.env['purchase.order'].with_company(self.company_id)
        PurchaseOrderLine = self.env['purchase.order.line'].with_company(self.company_id)

        for supplier_id, lines in grouped_lines.items():
            supplier = self.env['res.partner'].browse(supplier_id)
            if supplier.company_id and supplier.company_id != self.company_id:
                raise UserError(_(
                    'المورد %s لا يتبع شركة طلب الخامات.', supplier.display_name,
                ))
            purchase_order_vals = {
                'partner_id': supplier.id,
                'company_id': self.company_id.id,
                'picking_type_id': self.warehouse_id.in_type_id.id,
                'origin': self.name,
            }
            if self.production_shortage_managed and self.production_id:
                purchase_order_vals.update({
                    'furniture_material_check_production_id': self.production_id.id,
                    'furniture_material_check_managed': True,
                })
            purchase_order = PurchaseOrder.create(purchase_order_vals)
            for line in lines:
                preferred_uom = line.product_id.uom_po_id or line.product_uom_id
                purchase_uom = PurchaseOrderLine._furniture_purchase_uom_for_product(
                    line.product_id, preferred_uom,
                ) or preferred_uom
                purchase_qty = line.product_uom_id._compute_quantity(
                    line.quantity, purchase_uom, rounding_method='HALF-UP'
                )
                purchase_line_vals = {
                    'order_id': purchase_order.id,
                    'product_id': line.product_id.id,
                    'product_qty': purchase_qty,
                    'product_uom': purchase_uom.id,
                }
                if line.material_shortage_managed and line.material_reservation_id:
                    purchase_line_vals.update({
                        'furniture_material_reservation_id': line.material_reservation_id.id,
                        'furniture_shortage_managed': True,
                    })
                PurchaseOrderLine.create(purchase_line_vals)
            lines._link_purchase_order(purchase_order)
            purchase_orders |= purchase_order

        if self.production_shortage_managed and self.production_id and purchase_orders:
            self.production_id.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_reservation_refresh=True,
            ).sudo().write({
                'purchase_order_ids': [
                    Command.link(order.id) for order in purchase_orders
                ],
            })

        self._mark_rfq_created(purchase_orders)
        self.message_post(body=_(
            'أنشأ قسم المشتريات عروض الأسعار: %s',
            ', '.join(purchase_orders.mapped('name')),
        ))
        return self.action_view_rfqs()

    def action_view_rfqs(self):
        self.ensure_one()
        self._ensure_purchase_processor()
        action = self.env['ir.actions.actions']._for_xml_id('purchase.purchase_rfq')
        if len(self.purchase_order_ids) == 1:
            form_view = self.env.ref('purchase.purchase_order_form')
            action.update({
                'views': [(form_view.id, 'form')],
                'res_id': self.purchase_order_ids.id,
            })
        else:
            action['domain'] = [('id', 'in', self.purchase_order_ids.ids)]
        return action

    def action_cancel(self):
        can_cancel = (
            self._user_can_submit()
            or self._user_can_process_purchase()
            or self.env.user.has_group('purchase.group_purchase_manager')
        )
        if not can_cancel:
            raise AccessError(_('ليس لديك صلاحية إلغاء طلب الشراء.'))
        self._lock_workflow_rows()
        for request in self:
            if request.state not in ('draft', 'requested'):
                raise UserError(_('لا يمكن إلغاء الطلب بعد إنشاء عروض الأسعار.'))
            request._set_workflow_state('cancelled')
            request.message_post(body=_('تم إلغاء طلب شراء الخامات.'))
        return True


class FurniturePurchaseRequestLine(models.Model):
    _name = 'furniture.purchase.request.line'
    _description = 'Warehouse Purchase Request Line'
    _order = 'id'
    _check_company_auto = True

    request_id = fields.Many2one(
        'furniture.purchase.request', string='طلب الشراء', required=True,
        ondelete='cascade', index=True, check_company=True,
    )
    company_id = fields.Many2one(
        related='request_id.company_id', store=True, readonly=True, index=True,
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة', required=True, check_company=True,
    )
    quantity = fields.Float(
        string='الكمية المطلوبة', required=True, default=1.0,
        digits='Product Unit of Measure',
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='وحدة القياس', required=True,
    )
    product_uom_category_id = fields.Many2one(
        'uom.category', related='product_id.uom_id.category_id', readonly=True,
    )
    suggested_supplier_id = fields.Many2one(
        'res.partner', string='المورد المقترح',
        compute='_compute_suggested_supplier', readonly=True, check_company=True,
    )
    supplier_id = fields.Many2one(
        'res.partner', string='المورد المختار',
        domain="[('supplier_rank', '>', 0)]", check_company=True,
    )
    purchase_order_id = fields.Many2one(
        'purchase.order', string='RFQ', readonly=True, copy=False,
        check_company=True, ondelete='restrict',
    )
    material_reservation_id = fields.Many2one(
        'furniture.mrp.material.reservation', string='حجز خامات الإنتاج',
        readonly=True, copy=False, index=True, check_company=True,
        ondelete='set null',
    )
    material_shortage_managed = fields.Boolean(
        string='سطر عجز مُدار من فحص المواد', readonly=True, copy=False,
        index=True,
    )

    _sql_constraints = [
        (
            'furniture_purchase_request_reservation_unique',
            'unique(request_id, material_reservation_id)',
            'لا يمكن تكرار نفس حجز الخامة داخل طلب الشراء.',
        ),
    ]

    @api.depends('product_id', 'quantity', 'product_uom_id', 'company_id')
    def _compute_suggested_supplier(self):
        today = fields.Date.context_today(self)
        for line in self:
            seller = self.env['product.supplierinfo']
            if line.product_id:
                company = line.company_id or self.env.company
                seller = line.product_id.with_company(company)._select_seller(
                    quantity=line.quantity,
                    date=today,
                    uom_id=line.product_uom_id or line.product_id.uom_po_id,
                )
            line.suggested_supplier_id = seller.partner_id if seller else False

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            preferred_uom = self.product_id.uom_po_id or self.product_id.uom_id
            self.product_uom_id = self.env[
                'purchase.order.line'
            ]._furniture_purchase_uom_for_product(
                self.product_id, preferred_uom,
            ) or preferred_uom
            self.supplier_id = False

    @api.constrains('product_id', 'quantity', 'product_uom_id')
    def _check_line_values(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(_('الكمية المطلوبة يجب أن تكون أكبر من صفر.'))
            if line.product_id and not line.product_id.purchase_ok:
                raise ValidationError(_('الخامة المختارة غير متاحة للشراء.'))
            if (
                line.product_id
                and line.product_uom_id
                and line.product_id.uom_id.category_id != line.product_uom_id.category_id
            ):
                raise ValidationError(_('وحدة القياس يجب أن تكون من نفس فئة وحدة الخامة.'))

    @api.model
    def _prepare_create_vals(self, incoming_vals):
        vals = dict(incoming_vals)
        if vals.get('material_reservation_id') or vals.get('material_shortage_managed'):
            raise AccessError(_('ربط عجز الإنتاج يُدار تلقائيًا من فحص المواد.'))
        request = self.env['furniture.purchase.request'].browse(vals.get('request_id')).exists()
        if not request:
            raise UserError(_('حدد طلب الشراء أولًا.'))
        if request.state != 'draft' or not request._user_can_submit():
            raise AccessError(_('إضافة الخامات متاحة لأمين المخزن في المسودة فقط.'))
        if vals.get('supplier_id') and not request._user_can_process_purchase():
            raise AccessError(_('اختيار المورد متاح لقسم المشتريات فقط.'))
        if vals.get('purchase_order_id'):
            raise AccessError(_('يتم ربط RFQ تلقائيًا من قسم المشتريات فقط.'))
        product = self.env['product.product'].browse(vals.get('product_id')).exists()
        if product and not vals.get('product_uom_id'):
            vals['product_uom_id'] = (product.uom_po_id or product.uom_id).id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            self._prepare_create_vals(vals) for vals in vals_list
        ])

    @api.model_create_multi
    def _system_create_material_shortage_lines(self, vals_list):
        """Create shortage lines from the private production sync only."""
        return super(
            FurniturePurchaseRequestLine, self.sudo()
        ).create(vals_list)

    def _system_write_material_shortage_line(self, vals):
        return super(
            FurniturePurchaseRequestLine, self.sudo()
        ).write(vals)

    def _system_unlink_material_shortage_lines(self):
        return super(
            FurniturePurchaseRequestLine, self.sudo()
        ).unlink()

    def write(self, vals):
        vals = dict(vals)
        protected_fields = {
            'purchase_order_id', 'material_reservation_id',
            'material_shortage_managed',
        }
        if protected_fields & vals.keys():
            raise AccessError(_('روابط RFQ وعجز الإنتاج تُدار تلقائيًا من النظام.'))
        if 'request_id' in vals:
            raise UserError(_('لا يمكن نقل السطر إلى طلب شراء آخر.'))
        if 'supplier_id' in vals and not self.env[
            'furniture.purchase.request'
        ]._user_can_process_purchase():
            raise AccessError(_('اختيار المورد متاح لقسم المشتريات فقط.'))
        for line in self:
            if line.purchase_order_id:
                raise UserError(_('لا يمكن تعديل سطر مرتبط بعرض سعر.'))
            if line.request_id.state == 'draft':
                if not line.request_id._user_can_submit():
                    raise AccessError(_('تعديل الخامات متاح لأمين المخزن فقط.'))
                continue
            if (
                line.request_id.state == 'requested'
                and line.request_id._user_can_process_purchase()
            ):
                if set(vals) - {'supplier_id'}:
                    raise AccessError(_(
                        'بعد الإرسال يمكن لقسم المشتريات اختيار المورد فقط.'
                    ))
                continue
            raise UserError(_('لا يمكن تعديل خامات الطلب في حالته الحالية.'))
        if vals.get('product_id') and not vals.get('product_uom_id'):
            product = self.env['product.product'].browse(vals['product_id'])
            vals['product_uom_id'] = (product.uom_po_id or product.uom_id).id
        return super().write(vals)

    def _link_purchase_order(self, purchase_order):
        """Link lines to the RFQ created by the workflow after strict validation."""
        for line in self:
            supplier = line.supplier_id or line.suggested_supplier_id
            valid_order_line = purchase_order.order_line.filtered(
                lambda order_line: order_line.product_id == line.product_id
            )
            if (
                line.request_id.state != 'requested'
                or not line.request_id._user_can_process_purchase()
                or purchase_order.company_id != line.company_id
                or purchase_order.picking_type_id != line.request_id.warehouse_id.in_type_id
                or purchase_order.origin != line.request_id.name
                or purchase_order.partner_id != supplier
                or not valid_order_line
            ):
                raise ValidationError(_('لا يمكن ربط السطر بعرض سعر غير مطابق للطلب.'))
        return super(FurniturePurchaseRequestLine, self).write({
            'purchase_order_id': purchase_order.id,
        })

    def unlink(self):
        for line in self:
            if line.purchase_order_id:
                raise UserError(_('لا يمكن حذف سطر مرتبط بعرض سعر.'))
            if line.request_id.state != 'draft' or not line.request_id._user_can_submit():
                raise AccessError(_('حذف الخامات متاح لأمين المخزن في المسودة فقط.'))
        return super().unlink()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _furniture_whatsapp_message(self):
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        if not lines:
            raise UserError(_('أضف صنفًا واحدًا على الأقل قبل الإرسال عبر واتساب.'))

        def format_quantity(quantity):
            return ('%.3f' % quantity).rstrip('0').rstrip('.') or '0'

        item_lines = []
        for index, line in enumerate(lines, start=1):
            item_lines.append('%s. %s — %s %s' % (
                index,
                line.product_id.display_name,
                format_quantity(line.product_qty),
                line.product_uom.name or '',
            ))
        return '\n'.join([
            _('السلام عليكم %s،') % self.partner_id.name,
            _('طلب شراء رقم: %s') % self.name,
            _('الشركة: %s') % self.company_id.name,
            '',
            _('الأصناف المطلوبة:'),
            *item_lines,
            '',
            _('برجاء تأكيد التوفر والسعر وموعد التسليم.'),
        ])

    def action_send_via_whatsapp(self):
        self.ensure_one()
        if self.state == 'cancel':
            raise UserError(_('لا يمكن إرسال أمر شراء ملغي.'))
        mobile = self.partner_id.mobile or self.partner_id.phone
        if not mobile:
            raise UserError(_(
                'المورد %s ليس لديه رقم موبايل أو هاتف مسجل.',
                self.partner_id.display_name,
            ))
        whatsapp_message = self.env['whatsapp.msg'].create({
            'partner_id': self.partner_id.id,
            'mobile': mobile,
            'message': self._furniture_whatsapp_message(),
            'res_model': self._name,
            'res_id': self.id,
        })
        ready_instance = self.env['whatsapp.instance'].sudo().search([
            ('status', '=', 'ready'),
        ], limit=1)
        if ready_instance:
            whatsapp_message.action_send_via_api()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('تم الإرسال عبر واتساب'),
                    'message': _('تم إرسال %s إلى المورد %s.') % (
                        self.name, self.partner_id.display_name,
                    ),
                    'type': 'success',
                },
            }
        form_view = self.env.ref('whatsapp_integration.view_whatsapp_msg_form')
        return {
            'type': 'ir.actions.act_window',
            'name': _('رسالة واتساب - %s') % self.name,
            'res_model': 'whatsapp.msg',
            'res_id': whatsapp_message.id,
            'views': [(form_view.id, 'form')],
            'view_mode': 'form',
            'target': 'new',
        }

    def unlink(self):
        linked_line = self.env['furniture.purchase.request.line'].sudo().search([
            ('purchase_order_id', 'in', self.ids),
        ], limit=1)
        if linked_line:
            raise UserError(_(
                'لا يمكن حذف RFQ مرتبط بطلب خامات المخزن؛ يمكنك إلغاء عرض السعر بدلًا منه.'
            ))
        return super().unlink()
