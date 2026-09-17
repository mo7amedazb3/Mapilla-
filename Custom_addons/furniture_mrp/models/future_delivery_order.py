# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero


class FurnitureMrpFutureOrder(models.Model):
    _name = 'furniture.mrp.future.order'
    _description = 'طلب تسليم مستقبلي للمصنع'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'delivery_date asc, id asc'
    _rec_name = 'name'

    name = fields.Char(
        string='رقم الطلب',
        default='جديد',
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
    )
    delivery_date = fields.Date(
        string='تاريخ التسليم',
        required=True,
        default=fields.Date.context_today,
        index=True,
        tracking=True,
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري',
        required=True,
        domain=[('is_company', '=', True)],
        ondelete='restrict',
        tracking=True,
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستهلك',
        required=True,
        ondelete='restrict',
        tracking=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.future.order.line',
        'order_id',
        string='الأصناف المطلوبة',
        copy=True,
    )
    state = fields.Selection([
        ('pending', 'بانتظار القرار'),
        ('reserved', 'محجوز من المخزن التام'),
        ('production', 'تم إنشاء إنتاج للعجز'),
        ('cancelled', 'ملغي'),
    ], string='الحالة', default='pending', required=True, readonly=True,
        copy=False, index=True, tracking=True)
    decision_user_id = fields.Many2one(
        'res.users', string='صاحب القرار', readonly=True, copy=False,
    )
    decision_date = fields.Datetime(
        string='وقت القرار', readonly=True, copy=False,
    )
    reservation_picking_id = fields.Many2one(
        'stock.picking',
        string='حجز المخزن التام',
        readonly=True,
        copy=False,
        ondelete='restrict',
    )
    production_ids = fields.One2many(
        'furniture.mrp.production',
        'future_order_id',
        string='أوامر الإنتاج',
        readonly=True,
        copy=False,
    )
    production_count = fields.Integer(
        string='عدد أوامر الإنتاج', compute='_compute_decision_summary',
    )
    line_count = fields.Integer(
        string='عدد الأصناف', compute='_compute_decision_summary',
    )
    reservation_fully_ready = fields.Boolean(
        string='الحجز مكتمل', compute='_compute_decision_summary',
    )
    availability_state = fields.Selection([
        ('available', 'متاح بالكامل'),
        ('partial', 'متاح جزئيًا'),
        ('unavailable', 'غير متاح'),
        ('invalid', 'يحتاج استكمال البيانات'),
    ], string='توافر الطلب', compute='_compute_availability_summary')
    availability_label = fields.Char(
        string='ملخص التوافر', compute='_compute_availability_summary',
    )
    product_summary = fields.Char(
        string='الأصناف', compute='_compute_product_summary',
    )
    notes = fields.Text(string='ملاحظات', tracking=True)
    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True,
        default=lambda self: self.env.company, index=True,
    )

    _sql_constraints = [
        ('future_order_name_company_unique', 'unique(name, company_id)',
         'رقم طلب التسليم مستخدم بالفعل داخل نفس الشركة.'),
    ]

    @api.depends('line_ids', 'production_ids', 'reservation_picking_id.move_ids.state',
                 'reservation_picking_id.move_ids.quantity',
                 'reservation_picking_id.move_ids.product_uom_qty')
    def _compute_decision_summary(self):
        for order in self:
            order.line_count = len(order.line_ids)
            order.production_count = len(order.production_ids)
            moves = order.reservation_picking_id.move_ids.filtered(
                lambda move: move.state != 'cancel'
            )
            order.reservation_fully_ready = bool(moves) and all(
                float_compare(
                    move.quantity,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                ) >= 0
                for move in moves
            )

    @api.depends('line_ids.available_qty', 'line_ids.shortage_qty',
                 'line_ids.configuration_error')
    def _compute_availability_summary(self):
        for order in self:
            lines = order.line_ids
            if not lines or any(lines.mapped('configuration_error')):
                order.availability_state = 'invalid'
                order.availability_label = _('استكمل الصنف والموديل والكمية')
                continue
            requested = sum(lines.mapped('quantity'))
            available = sum(lines.mapped('available_qty'))
            if all(float_is_zero(
                line.shortage_qty,
                precision_rounding=line.product_uom_id.rounding or 0.001,
            ) for line in lines):
                state = 'available'
                label = _('كل الأصناف متاحة للحجز')
            elif float_is_zero(available, precision_digits=3):
                state = 'unavailable'
                label = _('لا يوجد رصيد كافٍ؛ يلزم إنتاج المطلوب')
            else:
                state = 'partial'
                label = _('متاح جزئيًا: %(available)s من %(requested)s وحدة طلب') % {
                    'available': self._qty_label(available),
                    'requested': self._qty_label(requested),
                }
            order.availability_state = state
            order.availability_label = label

    @api.depends('line_ids.product_id', 'line_ids.quantity')
    def _compute_product_summary(self):
        for order in self:
            chunks = [
                '%s × %s' % (line.product_id.display_name, self._qty_label(line.quantity))
                for line in order.line_ids[:4]
                if line.product_id
            ]
            if len(order.line_ids) > 4:
                chunks.append(_('و%(count)s أصناف أخرى') % {'count': len(order.line_ids) - 4})
            order.product_summary = '، '.join(chunks) or False

    @api.model
    def _qty_label(self, quantity):
        return ('%.3f' % (quantity or 0.0)).rstrip('0').rstrip('.') or '0'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('furniture.mrp.future.order')
                    or 'جديد'
                )
        records = super().create(vals_list)
        records._broadcast_alert_refresh()
        return records

    def write(self, vals):
        protected = {
            'delivery_date', 'buyer_partner_id', 'beneficiary_partner_id',
            'line_ids', 'company_id',
        }
        if protected & set(vals) and not self.env.context.get('future_order_decision_write'):
            locked = self.filtered(lambda order: order.state != 'pending')
            if locked:
                raise UserError(_(
                    'لا يمكن تعديل بيانات طلب تم اتخاذ قرار فيه. ألغِ الطلب وأنشئ طلبًا جديدًا.'
                ))
        result = super().write(vals)
        if {'delivery_date', 'state', 'company_id'} & set(vals):
            self._broadcast_alert_refresh()
        return result

    def unlink(self):
        if any(order.state not in ('pending', 'cancelled') for order in self):
            raise UserError(_('ألغِ الطلب أولًا قبل حذفه.'))
        for order in self.filtered(lambda row: row.state == 'cancelled'):
            if (order.reservation_picking_id and order.reservation_picking_id.state != 'cancel') or any(production.state != 'cancelled' for production in order.production_ids):
                raise UserError(_('لا يمكن حذف طلب مرتبط بحجز أو إنتاج لم يتم إلغاؤه.'))
        company_ids = self.mapped('company_id').ids
        result = super().unlink()
        self.env['furniture.mrp.future.order']._broadcast_alert_refresh(
            company_ids=company_ids,
        )
        return result

    def _check_manager(self):
        if not self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager'):
            raise AccessError(_('اتخاذ قرار طلبات التسليم متاح لمدير المصنع فقط.'))

    def _lock_pending_order(self):
        self.ensure_one()
        self._check_manager()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_future_order WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset(['state', 'reservation_picking_id'])
        if self.state != 'pending':
            raise UserError(_('تم اتخاذ قرار لهذا الطلب بالفعل. حدّث الصفحة لمشاهدة حالته الحالية.'))
        if not self.line_ids:
            raise UserError(_('أضف صنفًا واحدًا على الأقل قبل اتخاذ القرار.'))
        for line in self.line_ids:
            line._validate_configuration()

    def _demand_specs(self):
        self.ensure_one()
        specs = []
        for line in self.line_ids.sorted(lambda item: (item.sequence, item.id)):
            specs.extend(line._resolved_demand_specs(materialize_stock_product=True))
        return specs

    @api.model
    def _aggregate_specs(self, specs):
        aggregated = {}
        for spec in specs:
            product = spec['stock_product']
            bucket = aggregated.setdefault(product.id, {
                'product': product,
                'qty': 0.0,
            })
            bucket['qty'] += spec['qty']
        return aggregated

    def _warehouse_out_type(self):
        self.ensure_one()
        warehouse = self.env['stock.warehouse'].sudo().search([
            ('company_id', '=', self.company_id.id),
        ], order='id', limit=1)
        if not warehouse or not warehouse.out_type_id:
            raise UserError(_('لا يوجد نوع عملية تسليم مضبوط لمخزن الشركة.'))
        return warehouse.out_type_id

    def _finished_location(self):
        location = self.env.ref(
            'furniture_mrp.location_finished_goods', raise_if_not_found=False,
        )
        if not location:
            raise UserError(_('مخزن الإنتاج التام غير مضبوط.'))
        return location

    def _lock_finished_stock(self, product_ids):
        self.ensure_one()
        # Use the same company lock as the Min/Max controller so a replenishment
        # run cannot inspect the finished stock between our availability check
        # and the creation of the real outgoing reservation.
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)',
            [4608850, self.company_id.id],
        )
        if product_ids:
            self.env.cr.execute(
                '''
                SELECT id
                  FROM stock_quant
                 WHERE company_id = %s
                   AND location_id = %s
                   AND product_id = ANY(%s)
                 ORDER BY product_id, id
                 FOR UPDATE
                ''',
                [self.company_id.id, self._finished_location().id, list(product_ids)],
            )

    def _available_by_product(self, demand):
        self.ensure_one()
        Quant = self.env['stock.quant'].sudo().with_company(self.company_id)
        location = self._finished_location()
        return {
            product_id: max(Quant._get_available_quantity(
                values['product'],
                location,
                lot_id=self.env['stock.lot'],
                package_id=self.env['stock.quant.package'],
                owner_id=self.env['res.partner'],
                strict=True,
            ), 0.0)
            for product_id, values in demand.items()
        }

    def _prepare_picking_moves(self, demand, picking_type, destination):
        self.ensure_one()
        location = self._finished_location()
        return [Command.create({
            'name': _('%(order)s - حجز تسليم %(product)s') % {
                'order': self.name,
                'product': values['product'].display_name,
            },
            'product_id': values['product'].id,
            'product_uom_qty': values['qty'],
            'product_uom': values['product'].uom_id.id,
            'location_id': location.id,
            'location_dest_id': destination.id,
            'company_id': self.company_id.id,
        }) for values in demand.values()]

    def _create_and_assign_picking(self, require_full=False):
        self.ensure_one()
        # Lock before resolving a potentially missing model-specific SKU.  The
        # resolver is allowed to create that SKU only while a decision is being
        # executed, never while the form merely computes its preview.
        self._lock_finished_stock([])
        specs = self._demand_specs()
        demand = self._aggregate_specs(specs)
        self._lock_finished_stock(demand.keys())
        available = self._available_by_product(demand)
        if require_full:
            missing = []
            for product_id, values in demand.items():
                product = values['product']
                if float_compare(
                    available.get(product_id, 0.0), values['qty'],
                    precision_rounding=product.uom_id.rounding,
                ) < 0:
                    missing.append(_('%(product)s: المطلوب %(needed)s والمتاح %(available)s') % {
                        'product': product.display_name,
                        'needed': self._qty_label(values['qty']),
                        'available': self._qty_label(available.get(product_id, 0.0)),
                    })
            if missing:
                raise UserError(_(
                    'لا يمكن حجز الطلب كاملًا من المخزن التام:\n%s\n'
                    'استخدم «إنتاج العجز وحجز المتاح» بدلًا من ذلك.'
                ) % '\n'.join(missing))

        picking_type = self._warehouse_out_type()
        beneficiary = self.beneficiary_partner_id.with_company(self.company_id)
        buyer = self.buyer_partner_id.with_company(self.company_id)
        destination = (
            beneficiary.property_stock_customer
            or buyer.property_stock_customer
            or self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
        )
        if not destination:
            raise UserError(_('موقع مخزون العملاء غير مضبوط.'))
        picking = self.env['stock.picking'].sudo().with_company(self.company_id).create({
            'picking_type_id': picking_type.id,
            'partner_id': self.beneficiary_partner_id.id or self.buyer_partner_id.id,
            'location_id': self._finished_location().id,
            'location_dest_id': destination.id,
            'scheduled_date': fields.Datetime.to_datetime(self.delivery_date),
            'origin': self.name,
            'move_ids': self._prepare_picking_moves(demand, picking_type, destination),
        })
        picking.action_confirm()
        picking.action_assign()

        reserved = defaultdict(float)
        for move in picking.move_ids.filtered(lambda item: item.state != 'cancel'):
            reserved[move.product_id.id] += move.product_uom._compute_quantity(
                move.quantity, move.product_id.uom_id, round=False,
            )
        if require_full:
            not_reserved = []
            for product_id, values in demand.items():
                product = values['product']
                if float_compare(
                    reserved.get(product_id, 0.0), values['qty'],
                    precision_rounding=product.uom_id.rounding,
                ) < 0:
                    not_reserved.append(product.display_name)
            if not_reserved:
                raise UserError(_(
                    'تغير الرصيد أثناء الحجز ولم يكتمل حجز: %s. لم يتم حفظ أي حجز جزئي.'
                ) % '، '.join(not_reserved))
        return picking, specs, demand, reserved

    def action_reserve_from_finished(self):
        self.ensure_one()
        self._lock_pending_order()
        picking, _specs, _demand, _reserved = self._create_and_assign_picking(
            require_full=True,
        )
        self.with_context(future_order_decision_write=True).write({
            'state': 'reserved',
            'reservation_picking_id': picking.id,
            'decision_user_id': self.env.user.id,
            'decision_date': fields.Datetime.now(),
        })
        self.message_post(body=_('✅ تم حجز الطلب كاملًا من مخزن الإنتاج التام.'))
        return self._reload_notification(
            _('تم الحجز'),
            _('تم حجز كل الأصناف، ولم تعد الكميات المحجوزة تدخل ضمن المتاح للـ Min/Max.'),
            'success',
        )

    def _production_specs_by_line(self, specs, reserved):
        remaining_reserved = defaultdict(float, reserved)
        by_line = defaultdict(list)
        for spec in specs:
            stock_product = spec['stock_product']
            take = min(remaining_reserved[stock_product.id], spec['qty'])
            remaining_reserved[stock_product.id] = max(
                remaining_reserved[stock_product.id] - take, 0.0,
            )
            shortage = max(spec['qty'] - take, 0.0)
            if float_is_zero(
                shortage, precision_rounding=stock_product.uom_id.rounding,
            ):
                continue
            by_line[spec['future_line']].append({**spec, 'qty': shortage})
        return by_line

    def _create_shortage_production(self, future_line, specs):
        self.ensure_one()
        first = specs[0]
        Production = self.env['furniture.mrp.production']
        ProductionLine = self.env['furniture.mrp.production.line']
        line_values = []
        for sequence, spec in enumerate(specs, start=1):
            bom = spec['production_bom']
            source_product = spec['production_product']
            model = spec['model']
            if not bom or not model:
                raise UserError(_(
                    'لا توجد ريسيبي تصنيع مكتملة للصنف %(product)s بالموديل %(model)s.'
                ) % {
                    'product': source_product.display_name,
                    'model': model.display_name if model else '-',
                })
            line_values.append({
                'sequence': sequence * 10,
                'product_id': source_product.id,
                'furniture_order_model_id': model.id,
                'buyer_partner_id': self.buyer_partner_id.id,
                'beneficiary_partner_id': self.beneficiary_partner_id.id,
                'product_qty': spec['qty'],
                'bom_id': bom.id,
                'width_cm': spec.get('width_cm', bom.furniture_width_cm or 0.0),
                'depth_cm': spec.get('depth_cm', bom.furniture_depth_cm or 0.0),
                'height_cm': spec.get('height_cm', bom.furniture_height_cm or 0.0),
                # Shortage quantities are deliberately kept loose here.  The
                # Kit planner is the authority that splits produced pieces into
                # physical Kit instances; assigning every aggregated shortage
                # to instance #1 would merge several sold Kits incorrectly.
                'kit_bom_id': False,
                'kit_instance_number': 0,
            })
        first_vals = line_values[0]
        production = Production.create({
            'company_id': self.company_id.id,
            'responsible_id': self.env.user.id,
            'product_id': first_vals['product_id'],
            'furniture_order_model_id': first_vals['furniture_order_model_id'],
            'buyer_partner_id': self.buyer_partner_id.id,
            'beneficiary_partner_id': self.beneficiary_partner_id.id,
            'product_qty': sum(item['product_qty'] for item in line_values),
            'bom_id': first_vals['bom_id'],
            'width_cm': first_vals['width_cm'],
            'depth_cm': first_vals['depth_cm'],
            'height_cm': first_vals['height_cm'],
            'date_planned_start': fields.Datetime.now(),
            'date_planned_finish': fields.Datetime.to_datetime(self.delivery_date),
            'future_order_id': self.id,
            'future_order_line_id': future_line.id,
            'notes': _(
                'إنتاج عجز طلب التسليم %(order)s — التسليم %(date)s — '
                'المشتري %(buyer)s — المستهلك %(consumer)s'
            ) % {
                'order': self.name,
                'date': self.delivery_date,
                'buyer': self.buyer_partner_id.display_name,
                'consumer': self.beneficiary_partner_id.display_name,
            },
        })
        created_lines = ProductionLine.create([
            {**values, 'production_id': production.id}
            for values in line_values
        ])
        if created_lines:
            production.write({
                'stage_plan_line_id': created_lines[0].id,
                'dimension_line_id': created_lines[0].id,
            })
        future_line.production_order_id = production.id
        production.message_post(body=_('🔔 أُنشئ من طلب التسليم المستقبلي %s.') % self.name)
        return production

    def action_produce_shortage(self):
        self.ensure_one()
        self._lock_pending_order()
        picking, specs, _demand, reserved = self._create_and_assign_picking(
            require_full=False,
        )
        shortages = self._production_specs_by_line(specs, reserved)
        productions = self.env['furniture.mrp.production']
        for future_line, line_specs in shortages.items():
            productions |= self._create_shortage_production(future_line, line_specs)

        resulting_state = 'production' if productions else 'reserved'
        self.with_context(future_order_decision_write=True).write({
            'state': resulting_state,
            'reservation_picking_id': picking.id,
            'decision_user_id': self.env.user.id,
            'decision_date': fields.Datetime.now(),
        })
        if productions:
            self.message_post(body=_(
                '🏭 تم حجز الرصيد المتاح وإنشاء %(count)s أمر إنتاج لمسح العجز.'
            ) % {'count': len(productions)})
            title = _('تم إنشاء أوامر الإنتاج')
            message = _(
                'حُجز المتاح من المخزن التام، وتم إنشاء %(count)s أمر إنتاج للعجز فقط.'
            ) % {'count': len(productions)}
        else:
            self.message_post(body=_('✅ كان الرصيد متاحًا بالكامل، فتم حجزه دون إنتاج زائد.'))
            title = _('تم الحجز دون إنتاج زائد')
            message = _('كل الأصناف كانت متاحة؛ تم حجزها من المخزن التام.')
        return self._reload_notification(title, message, 'success')

    def action_cancel(self):
        for order in self:
            order._check_manager()
            picking = order.reservation_picking_id.sudo()
            if picking and picking.state == 'done':
                raise UserError(_(
                    'لا يمكن إلغاء الطلب لأن حركة التسليم نُفذت. أنشئ مرتجعًا مخزنيًا أولًا.'
                ))
            started = order.production_ids.filtered(
                lambda production: production.state not in ('draft', 'cancelled')
            )
            if started:
                raise UserError(_(
                    'لا يمكن إلغاء الطلب لأن أمر إنتاج مرتبط به بدأ بالفعل: %s'
                ) % '، '.join(started.mapped('name')))
            if picking and picking.state != 'cancel':
                picking.action_cancel()
            draft_productions = order.production_ids.filtered(
                lambda production: production.state == 'draft'
            )
            if draft_productions:
                draft_productions.action_cancel()
            order.with_context(future_order_decision_write=True).write({
                'state': 'cancelled',
            })
            order.message_post(body=_('⛔ تم إلغاء طلب التسليم وتحرير أي حجز مخزني.'))
        return self._reload_notification(
            _('تم إلغاء الطلب'), _('تم تحرير أي كميات كانت محجوزة.'), 'warning',
        )

    def action_open_reservation(self):
        self.ensure_one()
        if not self.reservation_picking_id:
            raise UserError(_('لا توجد حركة حجز مرتبطة بهذا الطلب.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('حجز المخزن التام'),
            'res_model': 'stock.picking',
            'res_id': self.reservation_picking_id.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_open_productions(self):
        self.ensure_one()
        productions = self.production_ids.exists()
        if not productions:
            raise UserError(_('لا توجد أوامر إنتاج مرتبطة بهذا الطلب.'))
        if len(productions) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('أمر إنتاج العجز'),
                'res_model': 'furniture.mrp.production',
                'res_id': productions.id,
                'views': [(False, 'form')],
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('أوامر إنتاج العجز'),
            'res_model': 'furniture.mrp.production',
            'domain': [('id', 'in', productions.ids)],
            'view_mode': 'list,form',
            'target': 'current',
        }

    @api.model
    def get_alert_data(self, limit=20):
        if not self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager'):
            return {
                'can_manage': False,
                'pending_total': 0,
                'due_soon_count': 0,
                'orders': [],
            }
        today = fields.Date.context_today(self)
        deadline = fields.Date.add(today, days=15)
        company_domain = [('company_id', 'in', self.env.companies.ids)]
        pending_domain = company_domain + [('state', '=', 'pending')]
        due_domain = pending_domain + [('delivery_date', '<=', deadline)]
        due_orders = self.search(due_domain, order='delivery_date asc, id asc', limit=limit)
        next_pending = self.search(pending_domain, order='delivery_date asc, id asc', limit=1)
        return {
            'can_manage': True,
            'pending_total': self.search_count(pending_domain),
            'due_soon_count': self.search_count(due_domain),
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
            'next_pending': ({
                'id': next_pending.id,
                'delivery_label': next_pending._delivery_alert_label(today),
            } if next_pending else False),
        }

    def _delivery_alert_label(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        days = (self.delivery_date - today).days
        if days < 0:
            return _('متأخر %(days)s يوم') % {'days': abs(days)}
        if days == 0:
            return _('التسليم اليوم')
        if days == 1:
            return _('التسليم غدًا')
        return _('متبقي %(days)s يوم') % {'days': days}

    def _broadcast_alert_refresh(self, company_ids=None):
        company_ids = set(company_ids or self.exists().mapped('company_id').ids)
        if not company_ids:
            company_ids = {self.env.company.id}
        group = self.env.ref(
            'furniture_mrp.group_furniture_mrp_manager', raise_if_not_found=False,
        )
        users = group.sudo().users.filtered(
            lambda user: (
                user.active
                and not user.share
                and user.partner_id
                and company_ids.intersection(user.company_ids.ids)
            )
        ) if group else self.env['res.users']
        for user in users:
            self.env['bus.bus'].sudo()._sendone(
                user.partner_id,
                'furniture_future_order_changed',
                {'refresh': True},
            )

    @api.model
    def _reload_notification(self, title, message, notification_type):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notification_type,
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }


class FurnitureMrpFutureOrderLine(models.Model):
    _name = 'furniture.mrp.future.order.line'
    _description = 'سطر طلب تسليم مستقبلي'
    _order = 'sequence, id'

    order_id = fields.Many2one(
        'furniture.mrp.future.order', string='طلب التسليم', required=True,
        ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', string='الصنف', required=True, ondelete='restrict',
        domain=[
            ('active', '=', True),
            ('furniture_dimension_source_product_id', '=', False),
            '|',
            ('furniture_has_active_normal_recipe', '=', True),
            ('is_kits', '=', True),
        ],
    )
    available_model_ids = fields.Many2many(
        'furniture.product.model', compute='_compute_product_routing',
        string='الموديلات المتاحة',
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model', string='الموديل', required=True,
        ondelete='restrict', domain="[('id', 'in', available_model_ids)]",
    )
    is_kit = fields.Boolean(string='Kit', compute='_compute_product_routing')
    route_label = fields.Char(string='نوع الصنف', compute='_compute_product_routing')
    quantity = fields.Float(
        string='الكمية', required=True, default=1.0, digits='Product Unit of Measure',
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة', related='product_id.uom_id', readonly=True,
    )
    available_qty = fields.Float(
        string='المتاح للحجز', compute='_compute_stock_availability',
        digits='Product Unit of Measure',
    )
    shortage_qty = fields.Float(
        string='العجز', compute='_compute_stock_availability',
        digits='Product Unit of Measure',
    )
    configuration_error = fields.Char(
        string='ملاحظة الإعداد', compute='_compute_stock_availability',
    )
    production_order_id = fields.Many2one(
        'furniture.mrp.production', string='أمر إنتاج العجز', readonly=True,
        copy=False, ondelete='set null',
    )
    company_id = fields.Many2one(
        related='order_id.company_id', store=True, readonly=True,
    )

    _sql_constraints = [
        ('future_order_line_quantity_positive', 'CHECK(quantity > 0)',
         'كمية الصنف يجب أن تكون أكبر من صفر.'),
    ]

    @api.depends('product_id', 'order_id.company_id')
    def _compute_product_routing(self):
        Bom = self.env['mrp.bom'].sudo()
        for line in self:
            line.available_model_ids = False
            line.is_kit = False
            line.route_label = False
            if not line.product_id:
                continue
            company = line.order_id.company_id or self.env.company
            kit_bom = Bom._bom_find(
                line.product_id,
                company_id=company.id,
                bom_type='phantom',
            ).get(line.product_id)
            if kit_bom:
                line.is_kit = True
                line.route_label = _('Kit — يُفك تلقائيًا إلى قطعه')
                line.available_model_ids = kit_bom.furniture_model_id
                continue
            source = line.product_id.furniture_dimension_source_product_id or line.product_id
            recipes = Bom.with_context(active_test=False).search([
                ('active', '=', True),
                ('type', '=', 'normal'),
                ('furniture_product_id', '=', source.id),
                ('company_id', 'in', [company.id, False]),
                ('furniture_model_id', '!=', False),
            ])
            candidate_models = recipes.mapped('furniture_model_id')
            line.available_model_ids = candidate_models.filtered(
                lambda model: bool(Bom._find_furniture_production_recipe(
                    source, model, company=company,
                ))
            )
            line.route_label = _('Manufacture this Product')

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            line.furniture_model_id = False
            if len(line.available_model_ids) == 1:
                line.furniture_model_id = line.available_model_ids.id

    @api.constrains('product_id', 'furniture_model_id', 'quantity')
    def _check_configuration(self):
        for line in self:
            line._validate_configuration()

    def _validate_configuration(self):
        self.ensure_one()
        if not self.product_id:
            raise ValidationError(_('اختيار الصنف مطلوب.'))
        if not self.furniture_model_id:
            raise ValidationError(_('اختيار الموديل مطلوب للصنف %s.') % self.product_id.display_name)
        if float_compare(
            self.quantity, 0.0,
            precision_rounding=self.product_uom_id.rounding or 0.001,
        ) <= 0:
            raise ValidationError(_('كمية الصنف يجب أن تكون أكبر من صفر.'))
        self._resolved_demand_specs()

    def _kit_bom(self):
        self.ensure_one()
        if not self.product_id:
            return self.env['mrp.bom']
        company = self.order_id.company_id or self.env.company
        bom = self.env['mrp.bom'].sudo()._bom_find(
            self.product_id,
            company_id=company.id,
            bom_type='phantom',
        ).get(self.product_id)
        return bom if bom and bom.furniture_model_id else self.env['mrp.bom']

    def _stock_product_for_model(self, product, model, materialize=False):
        """Resolve a model SKU without creating records during form previews."""
        self.ensure_one()
        if not product or not model or product.furniture_model_id == model:
            return product

        normalized_name = ' '.join(
            (product.product_tmpl_id.name or product.name or '').split()
        ).strip().casefold()
        candidates = self.env['product.product'].with_context(active_test=False).search([
            ('id', '!=', product.id),
            ('furniture_model_id', '=', model.id),
        ])
        matching = candidates.filtered(
            lambda candidate: ' '.join(
                (candidate.product_tmpl_id.name or candidate.name or '').split()
            ).strip().casefold() == normalized_name
        )[:1]
        if matching:
            return matching

        conflicting_recipe = self.env['mrp.bom'].with_context(active_test=False).search([
            ('furniture_product_id', '=', product.id),
            ('furniture_model_id', '!=', False),
            ('furniture_model_id', '!=', model.id),
        ], limit=1)
        if not product.furniture_model_id and not conflicting_recipe:
            return product
        if materialize:
            return self.env['mrp.bom']._furniture_product_for_selected_model(
                product, model,
            )
        return self.env['product.product']

    def _resolved_demand_specs(self, materialize_stock_product=False):
        self.ensure_one()
        if not self.product_id or not self.furniture_model_id or self.quantity <= 0:
            raise ValidationError(_('استكمل الصنف والموديل والكمية أولًا.'))
        company = self.order_id.company_id or self.env.company
        Bom = self.env['mrp.bom'].sudo()
        kit_bom = self._kit_bom()
        if kit_bom:
            if kit_bom.furniture_model_id != self.furniture_model_id:
                raise ValidationError(_(
                    'موديل الصنف لا يطابق موديل الـKit %(kit)s.'
                ) % {'kit': self.product_id.display_name})
            bom_qty = self.product_id.uom_id._compute_quantity(
                1.0, kit_bom.product_uom_id, rounding_method='HALF-UP',
            )
            _boms, exploded_lines = kit_bom.explode(self.product_id, bom_qty)
            components = defaultdict(float)
            for bom_line, line_data in exploded_lines:
                component = bom_line.product_id
                qty = bom_line.product_uom_id._compute_quantity(
                    line_data['qty'], component.uom_id, round=False,
                )
                components[component] += qty * self.quantity
            if not components:
                raise ValidationError(_('الـKit %s لا يحتوي على مكونات.') % self.product_id.display_name)
            specs = []
            for component, qty in components.items():
                source = component.furniture_dimension_source_product_id or component
                model = component.furniture_model_id or kit_bom.furniture_model_id
                recipe = Bom._find_furniture_production_recipe(
                    source, model, company=company,
                )
                if not recipe:
                    raise ValidationError(_(
                        'مكون الـKit %(component)s ليس له ريسيبي تصنيع للموديل %(model)s.'
                    ) % {
                        'component': component.display_name,
                        'model': model.display_name if model else '-',
                    })
                specs.append({
                    'future_line': self,
                    'stock_product': component,
                    'production_product': source,
                    'production_bom': recipe,
                    'model': model,
                    'kit_bom': kit_bom,
                    'qty': qty,
                })
            return specs

        source = self.product_id.furniture_dimension_source_product_id or self.product_id
        recipe = Bom._find_furniture_production_recipe(
            source, self.furniture_model_id, company=company,
        )
        if not recipe:
            raise ValidationError(_(
                'لا توجد ريسيبي تصنيع للصنف %(product)s بالموديل %(model)s.'
            ) % {
                'product': source.display_name,
                'model': self.furniture_model_id.display_name,
            })
        stock_product = self._stock_product_for_model(
            source,
            self.furniture_model_id,
            materialize=materialize_stock_product,
        )
        return [{
            'future_line': self,
            'stock_product': stock_product,
            'production_product': source,
            'production_bom': recipe,
            'model': self.furniture_model_id,
            'kit_bom': False,
            'qty': self.quantity,
        }]

    @api.depends('product_id', 'furniture_model_id', 'quantity', 'order_id.line_ids.product_id',
                 'order_id.line_ids.furniture_model_id', 'order_id.line_ids.quantity')
    def _compute_stock_availability(self):
        Quant = self.env['stock.quant'].sudo()
        finished = self.env.ref(
            'furniture_mrp.location_finished_goods', raise_if_not_found=False,
        )
        for line in self:
            line.available_qty = 0.0
            line.shortage_qty = max(line.quantity, 0.0)
            line.configuration_error = False
            if not finished or not line.product_id or not line.furniture_model_id or line.quantity <= 0:
                line.configuration_error = _('استكمل الصنف والموديل والكمية')
                continue
            try:
                pools = {}
                earlier_lines = line.order_id.line_ids.sorted(
                    lambda item: (item.sequence, item.id or 0)
                )
                for candidate in earlier_lines:
                    specs = candidate._resolved_demand_specs()
                    if candidate == line:
                        ratios = []
                        for spec in specs:
                            product = spec['stock_product']
                            if not product:
                                ratios.append(0.0)
                                continue
                            if product.id not in pools:
                                pools[product.id] = max(Quant.with_company(line.company_id)._get_available_quantity(
                                    product,
                                    finished,
                                    lot_id=self.env['stock.lot'],
                                    package_id=self.env['stock.quant.package'],
                                    owner_id=self.env['res.partner'],
                                    strict=True,
                                ), 0.0)
                            ratios.append(pools[product.id] / spec['qty'] if spec['qty'] else 0.0)
                        fraction = min([1.0] + ratios)
                        line.available_qty = max(min(line.quantity * fraction, line.quantity), 0.0)
                        line.shortage_qty = max(line.quantity - line.available_qty, 0.0)
                        break
                    for spec in specs:
                        product = spec['stock_product']
                        if not product:
                            continue
                        if product.id not in pools:
                            pools[product.id] = max(Quant.with_company(line.company_id)._get_available_quantity(
                                product,
                                finished,
                                lot_id=self.env['stock.lot'],
                                package_id=self.env['stock.quant.package'],
                                owner_id=self.env['res.partner'],
                                strict=True,
                            ), 0.0)
                        pools[product.id] = max(pools[product.id] - spec['qty'], 0.0)
            except (UserError, ValidationError) as error:
                line.configuration_error = str(error)
                line.available_qty = 0.0
                line.shortage_qty = max(line.quantity, 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {
            values.get('order_id')
            for values in vals_list
            if values.get('order_id')
        }
        locked = self.env['furniture.mrp.future.order'].browse(
            list(order_ids)
        ).exists().filtered(lambda order: order.state != 'pending')
        if locked:
            raise UserError(_('لا يمكن إضافة أصناف إلى طلب تم اتخاذ قرار فيه.'))
        lines = super().create(vals_list)
        lines.mapped('order_id')._broadcast_alert_refresh()
        return lines

    def write(self, vals):
        if {'order_id', 'sequence', 'product_id', 'furniture_model_id', 'quantity'} & set(vals):
            affected_orders = self.mapped('order_id')
            if vals.get('order_id'):
                affected_orders |= self.env['furniture.mrp.future.order'].browse(
                    vals['order_id']
                ).exists()
            locked = affected_orders.filtered(lambda order: order.state != 'pending')
            if locked:
                raise UserError(_('لا يمكن تعديل أصناف طلب تم اتخاذ قرار فيه.'))
        orders = self.mapped('order_id')
        result = super().write(vals)
        (orders | self.mapped('order_id'))._broadcast_alert_refresh()
        return result

    def unlink(self):
        orders = self.mapped('order_id')
        if any(order.state != 'pending' for order in orders):
            raise UserError(_('لا يمكن حذف صنف من طلب تم اتخاذ قرار فيه.'))
        result = super().unlink()
        orders._broadcast_alert_refresh()
        return result


class FurnitureMrpProductionFutureOrder(models.Model):
    _inherit = 'furniture.mrp.production'

    future_order_id = fields.Many2one(
        'furniture.mrp.future.order', string='طلب التسليم المستقبلي',
        readonly=True, copy=False, ondelete='set null', index=True,
    )
    delivery_set_notes = fields.Text(
        string='ملاحظات', related='future_order_id.notes', readonly=True,
        compute_sudo=True,
    )

    def _delivery_set_notes_payload(self):
        # Only expose the shared note of orders linked to visible production.
        result = []
        seen = set()
        for production in self:
            order = production.sudo().future_order_id
            if order.id not in seen and (order.notes or '').strip():
                seen.add(order.id)
                result.append({'id': order.id, 'reference': order.name,
                               'text': order.notes.strip()})
        return result

    future_order_line_id = fields.Many2one(
        'furniture.mrp.future.order.line', string='سطر طلب التسليم',
        readonly=True, copy=False, ondelete='set null', index=True,
    )


class StockMoveFutureOrderReservation(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        result = super()._action_done(cancel_backorder=cancel_backorder)
        finished = self.env.ref(
            'furniture_mrp.location_finished_goods', raise_if_not_found=False,
        )
        incoming = (self | result).filtered(lambda move: (
            move.state == 'done'
            and finished
            and move.location_dest_id == finished
        ))
        for company in incoming.mapped('company_id'):
            company_moves = incoming.filtered(lambda move: move.company_id == company)
            product_ids = company_moves.mapped('product_id').ids
            orders = self.env['furniture.mrp.future.order'].sudo().with_company(
                company
            ).search([
                ('company_id', '=', company.id),
                ('state', '=', 'production'),
                ('reservation_picking_id.state', 'not in', ('done', 'cancel')),
                ('reservation_picking_id.move_ids.product_id', 'in', product_ids),
            ])
            for picking in orders.mapped('reservation_picking_id').filtered(
                lambda item: item.state not in ('done', 'cancel')
            ):
                picking.sudo().with_company(company)._action_assign()
        return result
