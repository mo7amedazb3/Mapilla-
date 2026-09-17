# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare, float_round


class FurnitureMrpMaterialReservation(models.Model):
    """Planning-only reservation created by the material availability check.

    These rows deliberately do not reserve ``stock.quant`` and never create a
    stock move.  They only serialize the promise made by the planning screen
    so a later order cannot advertise the same physical quantity again.
    """

    _name = 'furniture.mrp.material.reservation'
    _description = 'حجز تخطيطي لخامات أمر الإنتاج'
    _order = 'reserved_at, id'
    _check_company_auto = True

    production_id = fields.Many2one(
        'furniture.mrp.production', required=True, ondelete='cascade',
        index=True, string='أمر الإنتاج', check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True, string='الشركة',
    )
    source_location_id = fields.Many2one(
        'stock.location', required=True, index=True, string='مخزن الخامات',
        check_company=True,
    )
    product_id = fields.Many2one(
        'product.product', required=True, index=True, string='الخامة',
        check_company=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', required=True, string='وحدة المخزون',
    )
    required_qty = fields.Float(
        string='المطلوب المتبقي', digits='Product Unit of Measure', required=True,
    )
    reserved_qty = fields.Float(
        string='المحجوز لهذا الأمر', digits='Product Unit of Measure', readonly=True,
    )
    shortage_qty = fields.Float(
        string='العجز', digits='Product Unit of Measure', readonly=True,
    )
    state = fields.Selection(
        [('active', 'نشط'), ('released', 'مفكوك')],
        required=True, default='active', index=True, string='الحالة',
    )
    reserved_at = fields.Datetime(
        string='وقت أولوية الحجز', required=True, default=fields.Datetime.now,
        index=True, readonly=True,
    )
    last_checked_at = fields.Datetime(string='آخر فحص', readonly=True)
    checked_by_id = fields.Many2one('res.users', string='فحص بواسطة', readonly=True)
    released_at = fields.Datetime(string='وقت فك الحجز', readonly=True)
    release_reason = fields.Char(string='سبب فك الحجز', readonly=True)

    _sql_constraints = [
        (
            'furniture_mrp_material_reservation_unique',
            'unique(production_id, company_id, source_location_id, product_id)',
            'يوجد حجز تخطيطي آخر لنفس الخامة داخل أمر الإنتاج.',
        ),
        (
            'furniture_mrp_material_reservation_qty_nonnegative',
            'check(required_qty >= 0 and reserved_qty >= 0 and shortage_qty >= 0)',
            'كميات حجز الخامات لا يمكن أن تكون سالبة.',
        ),
    ]

    @api.model
    def _bucket_lock_key(self, company, location, product):
        return 'furniture_mrp_material:%s:%s:%s' % (
            company.id, location.id, product.id,
        )

    @api.model
    def _lock_bucket(self, company, location, product):
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(hashtext(%s))',
            [self._bucket_lock_key(company, location, product)],
        )

    @api.model
    def _physical_available_qty(self, company, location, product):
        """Free physical quantity below one source location and company.

        Odoo stock moves sourced from ``WH/Stock`` may reserve quants from its
        internal child bins as well.  The planning promise must use the same
        location scope or it can report a false shortage while the warehouse
        can actually fulfil the move.
        """
        if not company or not location or not product:
            return 0.0
        # This method intentionally uses SQL so the planning lock and quantity
        # read stay in one serialized bucket.  Flush every column used by that
        # SQL first: inventory updates can create a quant earlier in the same
        # transaction and flushing only its quantity leaves the SQL lookup
        # blind to the new company/location/product keys.
        self.env['stock.quant'].flush_model([
            'company_id', 'location_id', 'product_id',
            'quantity', 'reserved_quantity',
        ])
        location_ids = self.env['stock.location'].sudo().search([
            ('id', 'child_of', location.id),
        ]).ids
        if not location_ids:
            return 0.0
        self.env.cr.execute(
            """
                SELECT COALESCE(SUM(quantity - reserved_quantity), 0.0)
                  FROM stock_quant
                 WHERE company_id = %s
                   AND location_id IN %s
                   AND product_id = %s
            """,
            [company.id, tuple(location_ids), product.id],
        )
        return max(float(self.env.cr.fetchone()[0] or 0.0), 0.0)

    @api.model
    def _reallocate_bucket(self, company, location, product):
        """Allocate first-check-wins after serializing the stock bucket."""
        self._lock_bucket(company, location, product)
        reservations = self.sudo().search([
            ('company_id', '=', company.id),
            ('source_location_id', '=', location.id),
            ('product_id', '=', product.id),
            ('state', '=', 'active'),
        ], order='reserved_at, id')
        remaining = self._physical_available_qty(company, location, product)
        changed_productions = self.env['furniture.mrp.production']
        rounding = product.uom_id.rounding or 0.001
        for reservation in reservations:
            reserved = min(max(reservation.required_qty, 0.0), remaining)
            shortage = max(reservation.required_qty - reserved, 0.0)
            remaining = max(remaining - reserved, 0.0)
            vals = {}
            if float_compare(
                reservation.reserved_qty, reserved,
                precision_rounding=rounding,
            ) != 0:
                vals['reserved_qty'] = reserved
            if float_compare(
                reservation.shortage_qty, shortage,
                precision_rounding=rounding,
            ) != 0:
                vals['shortage_qty'] = shortage
            if vals:
                reservation.sudo().write(vals)
                changed_productions |= reservation.production_id
        return changed_productions

    @api.model
    def protected_qty_for_other_productions(
        self, company, location, product, allowed_productions=False,
    ):
        allowed_ids = (allowed_productions or self.env['furniture.mrp.production']).ids
        domain = [
            ('company_id', '=', company.id),
            ('source_location_id', '=', location.id),
            ('product_id', '=', product.id),
            ('state', '=', 'active'),
        ]
        if allowed_ids:
            domain.append(('production_id', 'not in', allowed_ids))
        return sum(self.sudo().search(domain).mapped('reserved_qty'))


class FurnitureMrpProductionMaterialReservation(models.Model):
    _inherit = 'furniture.mrp.production'

    material_reservation_ids = fields.One2many(
        'furniture.mrp.material.reservation', 'production_id',
        string='حجوزات الخامات التخطيطية', readonly=True,
    )
    material_reservation_state = fields.Selection(
        [('none', 'لم يتم الفحص'), ('full', 'محجوز بالكامل'), ('partial', 'يوجد عجز')],
        string='حالة حجز الخامات', compute='_compute_material_reservation_summary',
    )
    material_reserved_qty = fields.Float(
        string='إجمالي المحجوز', compute='_compute_material_reservation_summary',
        digits=(16, 3),
    )
    material_shortage_qty = fields.Float(
        string='إجمالي العجز', compute='_compute_material_reservation_summary',
        digits=(16, 3),
    )
    has_active_material_reservation = fields.Boolean(
        compute='_compute_material_reservation_summary',
    )
    materials_checked_at = fields.Datetime(string='آخر فحص للخامات', readonly=True, copy=False)
    materials_checked_by_id = fields.Many2one(
        'res.users', string='فحص الخامات بواسطة', readonly=True, copy=False,
    )

    def _check_material_reservation_operator(self):
        forbidden = self.filtered(
            lambda production: production.company_id not in self.env.companies
        )
        if forbidden:
            raise AccessError(_(
                'لا يمكن فحص خامات أمر إنتاج تابع لشركة غير مفعلة حاليًا.'
            ))
        if self.env.is_superuser() or any((
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_manager'
            ),
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_supervisor'
            ),
        )):
            return True
        raise AccessError(_(
            'فحص الخامات وفك حجزها متاح لمدير الإنتاج أو مشرف الإنتاج فقط.'
        ))

    @api.depends(
        'material_reservation_ids.state',
        'material_reservation_ids.reserved_qty',
        'material_reservation_ids.shortage_qty',
    )
    def _compute_material_reservation_summary(self):
        for production in self:
            active = production.material_reservation_ids.filtered(
                lambda reservation: reservation.state == 'active'
            )
            production.material_reserved_qty = sum(active.mapped('reserved_qty'))
            production.material_shortage_qty = sum(active.mapped('shortage_qty'))
            production.has_active_material_reservation = bool(active)
            if not active:
                production.material_reservation_state = 'none'
            elif any(
                float_compare(line.shortage_qty, 0.0, precision_digits=3) > 0
                for line in active
            ):
                production.material_reservation_state = 'partial'
            else:
                production.material_reservation_state = 'full'

    def _material_reservation_source_location(self):
        self.ensure_one()
        if self.location_src_id:
            return self.location_src_id
        warehouse = self.env['stock.warehouse'].sudo().search([
            ('company_id', '=', self.company_id.id),
        ], order='id', limit=1)
        return warehouse.lot_stock_id

    def _material_reservation_issued_by_product(self, source_location):
        """Already issued raw material, aggregated once per stock move."""
        self.ensure_one()
        issued = defaultdict(float)
        if not source_location:
            return issued
        moves = self.env['stock.move'].sudo().search([
            ('origin', '=', self.name),
            ('state', '=', 'done'),
            ('location_id', '=', source_location.id),
        ])
        for move in moves:
            move_name = move.name or ''
            if not (
                'خامات' in move_name
                or 'تسليم مخزني' in move_name
                or move.id in self.material_line_ids.mapped('move_id').ids
            ):
                continue
            issued[move.product_id.id] += self._quantity_in_product_uom(
                move.product_id,
                move.quantity or move.product_uom_qty,
                move.product_uom,
            )
        return issued

    def _material_reservation_requirement_lines(self):
        """Allow independently supplied departments to own their replenishment."""
        return self.material_line_ids

    def _material_reservation_requirements(self):
        self.ensure_one()
        source_location = self._material_reservation_source_location()
        required = defaultdict(float)
        products = {}
        for line in self._material_reservation_requirement_lines().filtered(
            lambda item: item.product_id and item.qty_needed
        ):
            product = line.product_id
            products[product.id] = product
            required[product.id] += self._quantity_in_product_uom(
                product, line.qty_needed, line.product_uom_id,
            )
        issued = self._material_reservation_issued_by_product(source_location)
        return source_location, {
            product_id: {
                'product': products[product_id],
                'required_qty': max(quantity - issued.get(product_id, 0.0), 0.0),
            }
            for product_id, quantity in required.items()
            if float_compare(
                quantity - issued.get(product_id, 0.0), 0.0,
                precision_digits=3,
            ) > 0
        }

    def _refresh_material_reservations(
        self, preserve_priority=True, sync_purchase=True, record_check=False,
    ):
        Reservation = self.env['furniture.mrp.material.reservation']
        now = fields.Datetime.now()
        affected = set()
        productions_to_sync = self.env['furniture.mrp.production']
        productions = self.exists()
        payload_by_production = {}
        buckets_to_lock = {}

        # Establish one global lock order across both the old reservation
        # buckets and the freshly calculated requirements.  Every path that
        # writes reservation rows follows advisory-lock -> row-lock order.
        for production in productions.sorted('id'):
            source_location, requirements = production._material_reservation_requirements()
            if not source_location:
                raise UserError(_('حدد مخزن الخامات قبل فحص توفر المواد.'))
            payload_by_production[production.id] = (
                source_location, requirements,
            )
            existing_snapshot = Reservation.sudo().search([
                ('production_id', '=', production.id),
            ])
            for reservation in existing_snapshot:
                key = (
                    reservation.company_id.id,
                    reservation.source_location_id.id,
                    reservation.product_id.id,
                )
                buckets_to_lock[key] = (
                    reservation.company_id,
                    reservation.source_location_id,
                    reservation.product_id,
                )
            for values in requirements.values():
                product = values['product']
                key = (
                    production.company_id.id,
                    source_location.id,
                    product.id,
                )
                buckets_to_lock[key] = (
                    production.company_id, source_location, product,
                )
        for key in sorted(buckets_to_lock):
            Reservation._lock_bucket(*buckets_to_lock[key])

        for production in productions.sorted('id'):
            source_location, requirements = payload_by_production[production.id]
            existing = Reservation.sudo().search([
                ('production_id', '=', production.id),
            ])
            for reservation in existing:
                affected.add((
                    reservation.company_id,
                    reservation.source_location_id,
                    reservation.product_id,
                ))
            existing_by_product = {
                reservation.product_id.id: reservation for reservation in existing
            }
            for product_id, values in requirements.items():
                product = values['product']
                reservation = existing_by_product.get(product_id)
                vals = {
                    'company_id': production.company_id.id,
                    'source_location_id': source_location.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'required_qty': values['required_qty'],
                    'state': 'active',
                    'released_at': False,
                    'release_reason': False,
                }
                if record_check:
                    vals.update({
                        'last_checked_at': now,
                        'checked_by_id': self.env.user.id,
                    })
                elif not reservation:
                    vals.update({
                        'last_checked_at': production.materials_checked_at or now,
                        'checked_by_id': (
                            production.materials_checked_by_id.id
                            or self.env.user.id
                        ),
                    })
                if reservation:
                    if reservation.state != 'active' or not preserve_priority:
                        vals['reserved_at'] = now
                    reservation.sudo().write(vals)
                else:
                    vals.update({
                        'production_id': production.id,
                        'reserved_at': now,
                    })
                    reservation = Reservation.sudo().create(vals)
                affected.add((production.company_id, source_location, product))
            for reservation in existing.filtered(
                lambda item: item.product_id.id not in requirements
                and item.state == 'active'
            ):
                reservation.sudo().write({
                    'state': 'released',
                    'reserved_qty': 0.0,
                    'shortage_qty': 0.0,
                    'released_at': now,
                    'release_reason': _('لم تعد الخامة مطلوبة أو تم صرفها'),
                })
            if record_check:
                production.with_context(
                    furniture_skip_reservation_refresh=True,
                ).sudo().write({
                    'materials_checked_at': now,
                    'materials_checked_by_id': self.env.user.id,
                })
        for company, location, product in sorted(
            affected, key=lambda item: (item[0].id, item[1].id, item[2].id),
        ):
            productions_to_sync |= Reservation._reallocate_bucket(
                company, location, product,
            )
        if sync_purchase:
            productions_to_sync |= self.exists()
            for production in productions_to_sync.exists():
                production._sync_material_shortage_purchase_orders()
        self.invalidate_recordset([
            'material_reservation_ids', 'material_reservation_state',
            'material_reserved_qty', 'material_shortage_qty',
            'has_active_material_reservation',
        ])
        return True

    def _release_material_reservations(self, reason=False, sync_purchase=True):
        Reservation = self.env['furniture.mrp.material.reservation']
        affected = set()
        productions_to_sync = self.env['furniture.mrp.production']
        now = fields.Datetime.now()
        productions = self.exists()
        active_snapshot = Reservation.sudo().search([
            ('production_id', 'in', productions.ids),
            ('state', '=', 'active'),
        ])
        buckets_to_lock = {
            (
                reservation.company_id.id,
                reservation.source_location_id.id,
                reservation.product_id.id,
            ): (
                reservation.company_id,
                reservation.source_location_id,
                reservation.product_id,
            )
            for reservation in active_snapshot
        }
        for key in sorted(buckets_to_lock):
            Reservation._lock_bucket(*buckets_to_lock[key])

        # Re-read only after every advisory lock is held.  This avoids the
        # inverse row-lock -> advisory-lock order that could deadlock against
        # a concurrent availability refresh.
        active_reservations = Reservation.sudo().search([
            ('production_id', 'in', productions.ids),
            ('state', '=', 'active'),
        ])
        for production in productions.sorted('id'):
            active = active_reservations.filtered(
                lambda item: item.production_id == production
            )
            for reservation in active:
                affected.add((
                    reservation.company_id,
                    reservation.source_location_id,
                    reservation.product_id,
                ))
            if active:
                active.sudo().write({
                    'state': 'released',
                    'reserved_qty': 0.0,
                    'shortage_qty': 0.0,
                    'released_at': now,
                    'release_reason': reason or _('تم فك الحجز يدويًا'),
                })
            production.with_context(
                furniture_skip_reservation_refresh=True,
            ).sudo().write({
                'materials_checked_at': False,
                'materials_checked_by_id': False,
            })
        for company, location, product in sorted(
            affected, key=lambda item: (item[0].id, item[1].id, item[2].id),
        ):
            productions_to_sync |= Reservation._reallocate_bucket(
                company, location, product,
            )
        if sync_purchase:
            productions_to_sync |= self.exists()
            for production in productions_to_sync.exists():
                production._sync_material_shortage_purchase_orders()
        return True

    def action_release_material_reservations(self):
        self._check_material_reservation_operator()
        self._release_material_reservations(
            reason=_('فك مدير الإنتاج الحجز التخطيطي'),
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم فك حجز الخامات'),
                'message': _('أصبحت الكميات متاحة لفحص أوامر إنتاج أخرى.'),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _managed_purchase_order(self, vendor):
        self.ensure_one()
        domain = [
            ('furniture_material_check_production_id', '=', self.id),
            ('furniture_material_check_managed', '=', True),
            ('company_id', '=', self.company_id.id),
            ('state', 'in', ('draft', 'sent')),
        ]
        domain.append(
            ('partner_id', '=', vendor.id) if vendor else ('partner_id', '=', False)
        )
        return self.env['purchase.order'].sudo().search(
            domain, order='id desc', limit=1,
        )

    def _remove_empty_managed_purchase_orders(self, orders):
        """Cancel then delete empty generated RFQs using Odoo's lifecycle.

        Odoo 18 deliberately blocks deleting even a draft purchase order until
        it has been cancelled.  Availability reallocation can remove the last
        generated shortage line, so perform that required transition instead
        of leaving an empty RFQ behind or bypassing the core guard.
        """
        empty_orders = orders.sudo().exists().filtered(
            lambda order: (
                order.furniture_material_check_managed
                and not order.order_line
                and order.state in ('draft', 'sent')
            )
        )
        if empty_orders:
            empty_orders.button_cancel()
            empty_orders.unlink()
        return True

    def _confirmed_ordered_qty_for_reservation(self, reservation):
        lines = self.env['purchase.order.line'].sudo().search([
            ('furniture_material_reservation_id', '=', reservation.id),
            ('furniture_shortage_managed', '=', True),
            ('order_id.state', 'in', ('purchase', 'done')),
        ])
        outstanding = 0.0
        for line in lines:
            purchase_outstanding = max(
                (line.product_qty or 0.0) - (line.qty_received or 0.0), 0.0,
            )
            outstanding += self._quantity_in_product_uom(
                reservation.product_id,
                purchase_outstanding,
                line.product_uom,
            )
        return outstanding

    def _sync_material_shortage_purchase_orders(self):
        self.ensure_one()
        active = self.material_reservation_ids.filtered(
            lambda item: item.state == 'active'
        )
        reservations_by_vendor = defaultdict(lambda: self.env[
            'furniture.mrp.material.reservation'
        ])
        for reservation in active:
            vendor = self._get_vendor_for_material(reservation.product_id)
            reservations_by_vendor[vendor] |= reservation

        editable_orders = self.env['purchase.order'].sudo().search([
            ('furniture_material_check_production_id', '=', self.id),
            ('furniture_material_check_managed', '=', True),
            ('state', 'in', ('draft', 'sent')),
        ])
        touched_orders = self.env['purchase.order']
        processed_reservations = self.env['furniture.mrp.material.reservation']
        for vendor, reservations in reservations_by_vendor.items():
            order = self._managed_purchase_order(vendor)
            line_payloads = []
            for reservation in reservations:
                processed_reservations |= reservation
                confirmed_outstanding = self._confirmed_ordered_qty_for_reservation(
                    reservation,
                )
                to_order_stock = max(
                    reservation.shortage_qty - confirmed_outstanding, 0.0,
                )
                editable_lines = self.env['purchase.order.line'].sudo().search([
                    ('order_id.state', 'in', ('draft', 'sent')),
                    ('order_id.furniture_material_check_production_id', '=', self.id),
                    ('furniture_material_reservation_id', '=', reservation.id),
                    ('furniture_shortage_managed', '=', True),
                ], order='id desc')
                vendor_id = vendor.id if vendor else False
                matching_lines = editable_lines.filtered(lambda line: (
                    (line.order_id.partner_id.id or False) == vendor_id
                    and line.order_id.furniture_material_check_managed
                ))
                existing_line = matching_lines[:1]
                duplicate_lines = editable_lines - existing_line
                if duplicate_lines:
                    duplicate_lines.sudo().unlink()
                if existing_line:
                    order = existing_line.order_id
                rounding = reservation.product_uom_id.rounding or 0.001
                if float_compare(
                    to_order_stock, 0.0, precision_rounding=rounding,
                ) <= 0:
                    if existing_line:
                        existing_line.sudo().unlink()
                    continue
                purchase_uom = self._get_purchase_uom_for_product(
                    reservation.product_id, reservation.product_uom_id,
                )
                purchase_qty = reservation.product_uom_id._compute_quantity(
                    to_order_stock, purchase_uom, round=False,
                )
                purchase_qty = float_round(
                    purchase_qty,
                    precision_rounding=purchase_uom.rounding,
                    rounding_method='UP',
                )
                values = {
                    'product_id': reservation.product_id.id,
                    'product_uom': purchase_uom.id,
                    'product_qty': purchase_qty,
                    'price_unit': self._get_purchase_material_unit_cost(
                        reservation.product_id, purchase_uom,
                    ),
                    'name': reservation.product_id.display_name,
                    'date_planned': self.date_planned_start or fields.Datetime.now(),
                    'furniture_material_reservation_id': reservation.id,
                    'furniture_shortage_managed': True,
                }
                if existing_line:
                    existing_line.sudo().write(values)
                else:
                    line_payloads.append((0, 0, values))
            if line_payloads:
                if not order:
                    order = self.env['purchase.order'].sudo().create({
                        'partner_id': vendor.id if vendor else False,
                        'company_id': self.company_id.id,
                        'origin': self.name,
                        'furniture_material_check_production_id': self.id,
                        'furniture_material_check_managed': True,
                        'order_line': line_payloads,
                    })
                else:
                    order.sudo().write({'origin': self.name, 'order_line': line_payloads})
            if order and order.order_line.filtered('furniture_shortage_managed'):
                touched_orders |= order

        stale_lines = editable_orders.mapped('order_line').filtered(
            lambda line: (
                line.furniture_shortage_managed
                and line.furniture_material_reservation_id not in processed_reservations
            )
        )
        if stale_lines:
            stale_lines.sudo().unlink()
        self._remove_empty_managed_purchase_orders(editable_orders)

        touched_orders = touched_orders.exists()
        if touched_orders:
            self.purchase_order_ids = [(4, order.id) for order in touched_orders]
        blank = touched_orders.filtered(lambda order: not order.partner_id)[:1]
        self.with_context(furniture_skip_reservation_refresh=True).sudo().write({
            'missing_vendor_purchase_order_id': blank.id if blank else False,
        })
        return touched_orders

    def _material_check_feedback(self):
        """Return the same user-facing result for single and batch checks."""
        self.ensure_one()
        active = self.material_reservation_ids.filtered(
            lambda item: item.state == 'active'
        )
        shortage = sum(active.mapped('shortage_qty'))
        reserved = sum(active.mapped('reserved_qty'))
        if float_compare(shortage, 0.0, precision_digits=3) <= 0:
            message = _(
                'تم حجز خامات الأمر تخطيطيًا بالكامل: %s. '
                'لم تُنشأ أي حركة مخزون أو تكلفة.'
            ) % self._format_dimension_value(reserved)
            notification_type = 'success'
            title = _('الخامات محجوزة بالكامل')
        else:
            shortage_lines = [
                '%s: %s' % (
                    reservation.product_id.display_name,
                    self._format_dimension_value(reservation.shortage_qty),
                )
                for reservation in active
                if float_compare(
                    reservation.shortage_qty, 0.0, precision_digits=3,
                ) > 0
            ]
            message = _(
                'تم حجز المتاح حسب أولوية الفحص. العجز المطلوب شراؤه: %s'
            ) % '، '.join(shortage_lines)
            notification_type = 'warning'
            title = _('تم الحجز ويوجد عجز')
        return {
            'title': title,
            'message': message,
            'type': notification_type,
            'shortage': shortage,
            'reserved': reserved,
        }

    def action_check_materials(self):
        self.ensure_one()
        self._check_material_reservation_operator()
        if self.state != 'confirmed':
            raise UserError(_('يجب تأكيد الأمر أولًا.'))
        self._refresh_material_reservations(
            preserve_priority=True,
            record_check=True,
        )
        feedback = self._material_check_feedback()
        message = feedback['message']
        self.message_post(body='🔒 %s' % message)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': feedback['title'],
                'message': message,
                'type': feedback['type'],
                'sticky': bool(feedback['shortage']),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _check_material_issue_against_other_reservations(
        self, required_by_product, allowed_productions=False,
        source_location=False,
    ):
        self.ensure_one()
        allowed = allowed_productions or self
        source = source_location or self._material_reservation_source_location()
        if not source:
            raise UserError(_('تعذر تحديد مخزن مصدر الخامات لهذا الصرف.'))
        Reservation = self.env['furniture.mrp.material.reservation']
        shortages = []
        changed_productions = self.env['furniture.mrp.production']
        for product, requested_qty in sorted(
            required_by_product.items(), key=lambda item: item[0].id,
        ):
            changed_productions |= Reservation._reallocate_bucket(
                self.company_id, source, product,
            )
            physical = Reservation._physical_available_qty(
                self.company_id, source, product,
            )
            protected = Reservation.protected_qty_for_other_productions(
                self.company_id, source, product, allowed,
            )
            usable = max(physical - protected, 0.0)
            if float_compare(usable, requested_qty, precision_digits=3) < 0:
                shortages.append('%s: %s / %s' % (
                    product.display_name,
                    self._format_dimension_value(usable),
                    self._format_dimension_value(requested_qty),
                ))
        if shortages:
            raise UserError(_(
                'جزء من الرصيد محجوز تخطيطيًا لأوامر إنتاج سبق فحصها. '
                'المتاح لهذا الصرف لا يكفي:\n%s'
            ) % '\n'.join(shortages))
        for production in changed_productions.exists():
            production._sync_material_shortage_purchase_orders()
        return True

    def write(self, vals):
        tracked_fields = {
            'location_src_id', 'company_id', 'date_planned_start',
            'material_line_ids',
        }
        refresh_after = (
            not self.env.context.get('furniture_skip_reservation_refresh')
            and bool(tracked_fields & set(vals))
        )
        active_before = self.filtered('has_active_material_reservation')
        result = super().write(vals)
        if refresh_after:
            active_before.exists().filtered(
                lambda production: production.state in (
                    'confirmed', 'in_production',
                )
            )._refresh_material_reservations(preserve_priority=True)
        return result

    def action_cancel(self):
        result = super().action_cancel()
        self._release_material_reservations(reason=_('تم إلغاء أمر الإنتاج'))
        return result

    def action_reset_to_draft(self):
        result = super().action_reset_to_draft()
        self._release_material_reservations(reason=_('أعيد أمر الإنتاج إلى المسودة'))
        return result

    def unlink(self):
        editable_orders = self.env['purchase.order'].sudo().search([
            ('furniture_material_check_production_id', 'in', self.ids),
            ('furniture_material_check_managed', '=', True),
            ('state', 'in', ('draft', 'sent')),
        ])
        for order in editable_orders:
            managed_lines = order.order_line.filtered(
                'furniture_shortage_managed'
            )
            if managed_lines:
                managed_lines.sudo().unlink()
        self[:1]._remove_empty_managed_purchase_orders(editable_orders)
        self._release_material_reservations(
            reason=_('تم حذف أمر الإنتاج'), sync_purchase=False,
        )
        return super().unlink()


class FurnitureMrpMaterialLineReservationAvailability(models.Model):
    _inherit = 'furniture.mrp.material.line'

    def _refresh_linked_active_reservations(self, extra_productions=False):
        productions = self.mapped('production_id')
        if extra_productions:
            productions |= extra_productions
        productions = productions.exists().filtered(lambda production: (
            production.has_active_material_reservation
            and production.state in ('confirmed', 'in_production')
        ))
        if productions:
            productions._refresh_material_reservations(
                preserve_priority=True,
            )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('furniture_skip_reservation_refresh'):
            lines._refresh_linked_active_reservations()
        return lines

    def write(self, vals):
        tracked_fields = {
            'production_id', 'product_id', 'product_uom_id',
            'qty_needed', 'stage',
        }
        refresh_after = (
            not self.env.context.get('furniture_skip_reservation_refresh')
            and bool(tracked_fields & set(vals))
        )
        previous_productions = self.mapped('production_id') if refresh_after else False
        result = super().write(vals)
        if refresh_after:
            self._refresh_linked_active_reservations(previous_productions)
        return result

    def unlink(self):
        productions = self.mapped('production_id')
        refresh_after = not self.env.context.get(
            'furniture_skip_reservation_refresh'
        )
        result = super().unlink()
        if refresh_after:
            productions.exists().filtered(lambda production: (
                production.has_active_material_reservation
                and production.state in ('confirmed', 'in_production')
            ))._refresh_material_reservations(preserve_priority=True)
        return result

    def _compute_availability(self):
        # The parent implementation reads ``qty_available`` while iterating
        # individual material lines.  Prime all stockable products as one
        # recordset so Odoo computes their quantities in a single batch rather
        # than issuing the same stock/BOM query family once per product.  This
        # is especially visible when a compact tailoring save invalidates the
        # production's stored availability summary.
        stockable_products = self.mapped('product_id').filtered(
            lambda product: product.is_storable
        )
        if stockable_products:
            stockable_products.mapped('qty_available')
        by_production_product = defaultdict(lambda: self.env[
            'furniture.mrp.material.line'
        ])
        for line in self:
            if line.production_id and line.product_id:
                by_production_product[(
                    line.production_id.id, line.product_id.id,
                )] |= line
            else:
                line.qty_available = 0.0
                line.qty_reserved = 0.0
                line.availability = 'missing'
        for (production_id, product_id), requested_lines in by_production_product.items():
            production = self.env['furniture.mrp.production'].browse(production_id)
            reservation = production.material_reservation_ids.filtered(
                lambda item: item.state == 'active' and item.product_id.id == product_id
            )[:1]
            if not reservation:
                super(
                    FurnitureMrpMaterialLineReservationAvailability,
                    requested_lines,
                )._compute_availability()
                continue
            all_lines = production.material_line_ids.filtered(
                lambda line: line.product_id.id == product_id
            ).sorted('id')
            remaining = reservation.reserved_qty
            allocated_by_line = {}
            for line in all_lines:
                needed = production._quantity_in_product_uom(
                    line.product_id, line.qty_needed, line.product_uom_id,
                )
                allocated_stock = min(max(remaining, 0.0), needed)
                remaining -= allocated_stock
                allocated_by_line[line.id] = line.product_id.uom_id._compute_quantity(
                    allocated_stock, line.product_uom_id, round=False,
                )
            for line in requested_lines:
                allocated = allocated_by_line.get(line.id, 0.0)
                line.qty_available = allocated
                line.qty_reserved = allocated
                if float_compare(allocated, line.qty_needed, precision_digits=3) >= 0:
                    line.availability = 'ok'
                elif float_compare(allocated, 0.0, precision_digits=3) > 0:
                    line.availability = 'partial'
                else:
                    line.availability = 'missing'


class PurchaseOrderMaterialReservation(models.Model):
    _inherit = 'purchase.order'

    furniture_material_check_production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر إنتاج فحص الخامات',
        ondelete='set null', index=True, copy=False, readonly=True,
    )
    furniture_material_check_managed = fields.Boolean(
        string='طلب شراء مُدار من فحص الخامات', copy=False, readonly=True,
    )


class PurchaseOrderLineMaterialReservation(models.Model):
    _inherit = 'purchase.order.line'

    furniture_material_reservation_id = fields.Many2one(
        'furniture.mrp.material.reservation', string='حجز خامات الإنتاج',
        ondelete='set null', index=True, copy=False, readonly=True,
    )
    furniture_shortage_managed = fields.Boolean(
        string='سطر عجز مُدار من فحص الخامات', copy=False, readonly=True,
    )


class FurnitureMrpStoreRequestMaterialReservation(models.Model):
    _inherit = 'furniture.mrp.store.request'

    def _material_issue_available_qty(self, source_location, product):
        self.ensure_one()
        if not self._allows_material_shortage_start():
            return super()._material_issue_available_qty(
                source_location, product,
            )
        Reservation = self.env['furniture.mrp.material.reservation']
        Reservation._reallocate_bucket(
            self.production_id.company_id,
            source_location,
            product,
        )
        physical = Reservation._physical_available_qty(
            self.production_id.company_id,
            source_location,
            product,
        )
        protected = Reservation.protected_qty_for_other_productions(
            self.production_id.company_id,
            source_location,
            product,
            self.production_id,
        )
        return max(physical - protected, 0.0)

    def _issue_materials_to_handover(self):
        self.ensure_one()
        if not self._allows_material_shortage_start():
            required = defaultdict(float)
            for line in self.material_line_ids:
                already = sum(
                    line._move_qty_in_line_uom(move)
                    for move in line._valid_issue_moves(
                        self.handover_location_id or self._material_handover_location()
                    )
                )
                missing_line_qty = max(line.requested_qty - already, 0.0)
                missing_stock_qty = line.product_uom_id._compute_quantity(
                    missing_line_qty, line.product_id.uom_id, round=False,
                )
                required[line.product_id] += missing_stock_qty
            self.production_id._check_material_issue_against_other_reservations(
                required,
                self.production_id,
                source_location=(
                    self.source_location_id
                    or self.production_id._material_reservation_source_location()
                ),
            )
        result = super()._issue_materials_to_handover()
        self.production_id._refresh_material_reservations(
            preserve_priority=True, sync_purchase=True,
        )
        return result


class FurnitureMrpAdvanceMaterialReleaseReservation(models.Model):
    _inherit = 'furniture.mrp.advance.material.release'

    def _preflight_issue_availability(self):
        result = super()._preflight_issue_availability()
        required_by_bucket = defaultdict(float)
        production_by_bucket = {}
        for stage in self.stage_line_ids.filtered(lambda item: item.state == 'pending'):
            if stage.stage_code == 'tailoring':
                continue
            production = stage.production_id
            source = production._material_reservation_source_location()
            for detail in stage.material_line_ids:
                staged = sum(
                    detail._move_qty_in_product_uom(move)
                    for move in detail._valid_issue_moves(
                        stage._material_handover_location()
                    )
                )
                missing = max(detail.requested_qty - staged, 0.0)
                key = (
                    production.company_id.id, source.id,
                    detail.product_id.id,
                )
                required_by_bucket[key] += missing
                production_by_bucket[key] = production
        allowed = self.production_ids
        grouped_by_production = defaultdict(dict)
        for key, required_qty in required_by_bucket.items():
            production = production_by_bucket[key]
            product = self.env['product.product'].browse(key[2])
            grouped_by_production[production][product] = required_qty
        for production, required in grouped_by_production.items():
            production._check_material_issue_against_other_reservations(
                required, allowed,
                source_location=production._material_reservation_source_location(),
            )
        return result

    def action_issue(self):
        productions = self.mapped('production_ids').exists()
        result = super().action_issue()
        productions._refresh_material_reservations(
            preserve_priority=True, sync_purchase=True,
        )
        return result


class FurnitureMrpAdvanceMaterialReleaseStageReservation(models.Model):
    _inherit = 'furniture.mrp.advance.material.release.stage'

    def _material_issue_available_qty(self, source_location, product):
        self.ensure_one()
        if self.stage_code != 'tailoring':
            return super()._material_issue_available_qty(
                source_location, product,
            )
        Reservation = self.env['furniture.mrp.material.reservation']
        Reservation._reallocate_bucket(
            self.production_id.company_id,
            source_location,
            product,
        )
        physical = Reservation._physical_available_qty(
            self.production_id.company_id,
            source_location,
            product,
        )
        protected = Reservation.protected_qty_for_other_productions(
            self.production_id.company_id,
            source_location,
            product,
            self.release_id.production_ids,
        )
        return max(physical - protected, 0.0)


class FurnitureMrpMaterialCheckBatchWizard(models.TransientModel):
    _name = 'furniture.mrp.material.check.batch.wizard'
    _description = 'فحص وحجز خامات عدة أوامر إنتاج'

    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    select_all = fields.Boolean(string='تحديد كل الأوامر')
    line_ids = fields.One2many(
        'furniture.mrp.material.check.batch.wizard.line',
        'wizard_id', string='أوامر الإنتاج',
    )
    eligible_count = fields.Integer(
        string='الأوامر المتاحة', compute='_compute_selection_counts',
    )
    selected_count = fields.Integer(
        string='الأوامر المختارة', compute='_compute_selection_counts',
    )

    @api.depends('line_ids', 'line_ids.selected')
    def _compute_selection_counts(self):
        for wizard in self:
            wizard.eligible_count = len(wizard.line_ids)
            wizard.selected_count = len(
                wizard.line_ids.filtered('selected')
            )

    @api.onchange('select_all')
    def _onchange_select_all(self):
        for wizard in self:
            wizard.line_ids.update({'selected': wizard.select_all})

    @api.model
    def _eligible_productions(self, company=False):
        company = company or self.env.company
        return self.env['furniture.mrp.production'].search([
            ('company_id', '=', company.id),
            ('state', '=', 'confirmed'),
        ], order='dashboard_sequence asc, date_planned_start asc, id asc')

    @api.model
    def _line_commands(self, productions, selected_ids=False):
        selected_ids = set(selected_ids or ())
        return [
            (0, 0, {
                'production_id': production.id,
                'selected': production.id in selected_ids,
                'product_summary': (
                    production.header_product_summary
                    or production.product_id.display_name
                    or _('بدون أصناف')
                ),
                'buyer_summary': (
                    '، '.join(production.buyer_partner_ids.mapped('display_name'))
                    or _('غير محدد')
                ),
                'beneficiary_summary': (
                    '، '.join(
                        production.beneficiary_partner_ids.mapped('display_name')
                    )
                    or _('غير محدد')
                ),
            })
            for production in productions
        ]

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if not self.env.context.get('auto_load_eligible_productions'):
            return values
        company = self.env['res.company'].browse(
            values.get('company_id') or self.env.company.id
        ).exists() or self.env.company
        productions = self._eligible_productions(company)
        active_ids = set()
        if self.env.context.get('active_model') == 'furniture.mrp.production':
            active_ids = {
                int(record_id)
                for record_id in self.env.context.get('active_ids', [])
                if str(record_id).isdigit()
            }
        selected_ids = active_ids.intersection(productions.ids)
        values.update({
            'company_id': company.id,
            'line_ids': self._line_commands(productions, selected_ids),
        })
        return values

    def action_check_selected_materials(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_('حدد أمر إنتاج واحدًا على الأقل لفحص خاماته.'))
        productions = selected_lines.mapped('production_id').exists().sorted('id')
        if len(productions) != len(selected_lines):
            raise UserError(_('أحد أوامر الإنتاج المختارة لم يعد موجودًا.'))
        productions._check_material_reservation_operator()
        invalid = productions.filtered(
            lambda production: (
                production.company_id != self.company_id
                or production.state != 'confirmed'
            )
        )
        if invalid:
            raise UserError(_(
                'لا يمكن فحص أمر غير مؤكد أو تابع لشركة أخرى: %s'
            ) % '، '.join(invalid.mapped('display_name')))

        # Refresh the whole selection together.  The existing reservation
        # engine locks material buckets in one deterministic order and then
        # synchronizes every affected shortage RFQ, including older orders.
        productions._refresh_material_reservations(
            preserve_priority=True,
            record_check=True,
        )

        full_count = 0
        shortage_count = 0
        for production in productions:
            feedback = production._material_check_feedback()
            if feedback['type'] == 'warning':
                shortage_count += 1
            else:
                full_count += 1
            production.message_post(body='🔒 %s' % feedback['message'])

        managed_rfq_count = self.env['purchase.order'].sudo().search_count([
            ('furniture_material_check_production_id', 'in', productions.ids),
            ('furniture_material_check_managed', '=', True),
            ('state', 'in', ('draft', 'sent')),
        ])
        message = _(
            'تم فحص %(total)s أوامر إنتاج: %(full)s محجوزة بالكامل، '
            '%(shortage)s بها عجز. تم تحديث %(rfq)s طلبات شراء.'
        ) % {
            'total': len(productions),
            'full': full_count,
            'shortage': shortage_count,
            'rfq': managed_rfq_count,
        }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('اكتمل فحص المواد المجمع'),
                'message': message,
                'type': 'warning' if shortage_count else 'success',
                'sticky': bool(shortage_count),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class FurnitureMrpMaterialCheckBatchWizardLine(models.TransientModel):
    _name = 'furniture.mrp.material.check.batch.wizard.line'
    _description = 'اختيار أمر ضمن فحص المواد المجمع'
    _order = 'production_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.material.check.batch.wizard',
        string='فحص المواد المجمع', required=True, ondelete='cascade',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        required=True, readonly=True, ondelete='cascade',
    )
    selected = fields.Boolean(string='اختيار للفحص')
    production_state = fields.Selection(
        related='production_id.state', string='حالة الأمر', readonly=True,
    )
    model_id = fields.Many2one(
        related='production_id.furniture_order_model_id',
        string='الموديل', readonly=True,
    )
    date_planned_start = fields.Datetime(
        related='production_id.date_planned_start',
        string='البدء المخطط', readonly=True,
    )
    date_planned_finish = fields.Datetime(
        related='production_id.date_planned_finish',
        string='الانتهاء المخطط', readonly=True,
    )
    product_summary = fields.Text(string='الأصناف', readonly=True)
    buyer_summary = fields.Char(string='المشتري', readonly=True)
    beneficiary_summary = fields.Char(string='المستفيد', readonly=True)
    material_reservation_state = fields.Selection(
        related='production_id.material_reservation_state',
        string='حالة الحجز', readonly=True,
    )
    materials_checked_at = fields.Datetime(
        related='production_id.materials_checked_at',
        string='آخر فحص', readonly=True,
    )

    _sql_constraints = [
        (
            'material_check_batch_wizard_production_unique',
            'unique(wizard_id, production_id)',
            'لا يمكن تكرار أمر الإنتاج داخل فحص المواد المجمع.',
        ),
    ]
