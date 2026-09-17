# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import timedelta
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare, float_is_zero

from .mrp_production_order import FURNITURE_STAGE_SELECTION


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    furniture_delivery_date = fields.Date(
        string='تاريخ التسليم',
        copy=True,
    )
    furniture_kit_allocation_ids = fields.One2many(
        'furniture.sale.kit.allocation',
        'order_id',
        string='توافر مكونات الأطقم',
        copy=False,
    )
    furniture_kit_display_line_ids = fields.Many2many(
        'sale.order.line',
        string='الأطقم في أمر البيع',
        compute='_compute_furniture_kit_summary',
    )
    furniture_shortage_mps_id = fields.Many2one(
        'furniture.mrp.mps',
        string='طلب إنتاج العجز',
        copy=False,
        readonly=True,
        ondelete='set null',
    )
    furniture_shortage_production_ids = fields.One2many(
        'furniture.mrp.production',
        'furniture_sale_order_id',
        string='أوامر إنتاج عجز الأطقم',
        readonly=True,
    )
    furniture_kit_status = fields.Selection([
        ('none', 'لا توجد أطقم'),
        ('ready', 'جاهز من المخزن التام'),
        ('wip', 'محجوز من مراحل التصنيع'),
        ('shortage', 'يوجد عجز'),
        ('delivered', 'تم التسليم'),
    ], string='حالة توافر الأطقم', compute='_compute_furniture_kit_summary')
    furniture_kit_availability_summary = fields.Text(
        string='ملخص توافر الأطقم',
        compute='_compute_furniture_kit_summary',
    )
    furniture_kit_has_shortage = fields.Boolean(
        string='يوجد عجز في الأطقم',
        compute='_compute_furniture_kit_summary',
    )
    furniture_has_kit_lines = fields.Boolean(
        string='يحتوي على أطقم',
        compute='_compute_furniture_kit_summary',
    )
    furniture_kit_type_count = fields.Integer(
        string='أنواع الأطقم',
        compute='_compute_furniture_kit_summary',
    )
    furniture_kit_total_qty = fields.Float(
        string='الأطقم المطلوبة',
        compute='_compute_furniture_kit_summary',
        digits=(16, 3),
    )
    furniture_kit_ready_qty = fields.Float(
        string='جاهز من التام',
        compute='_compute_furniture_kit_summary',
        digits=(16, 3),
    )

    furniture_kit_wip_qty = fields.Float(
        string='تحت التصنيع',
        compute='_compute_furniture_kit_summary',
        digits=(16, 3),
    )
    furniture_kit_shortage_qty = fields.Float(
        string='أطقم بها عجز',
        compute='_compute_furniture_kit_summary',
        digits=(16, 3),
    )

    def _get_product_catalog_domain(self):
        return expression.AND([
            super()._get_product_catalog_domain(),
            [('furniture_has_active_sale_kit_recipe', '=', True)],
        ])

    @api.depends(
        'furniture_kit_allocation_ids.state',
        'furniture_kit_allocation_ids.required_qty',
        'furniture_kit_allocation_ids.finished_allocated_qty',
        'furniture_kit_allocation_ids.wip_allocated_qty',
        'furniture_kit_allocation_ids.shortage_qty',
        'order_line.product_id',
        'order_line.product_uom_qty',
        'order_line.furniture_finished_kit_qty',
        'order_line.furniture_wip_kit_qty',
        'order_line.furniture_shortage_kit_qty',
    )
    def _compute_furniture_kit_summary(self):
        labels = {
            'ready': _('جاهز من المخزن التام'),
            'wip': _('محجوز من مراحل التصنيع'),
            'shortage': _('يوجد عجز يحتاج إنتاج'),
            'delivered': _('تم التسليم'),
            'cancel': _('ملغي'),
        }
        for order in self:
            kit_lines = order.order_line.filtered(
                lambda line: (
                    not line.display_type
                    and line.product_id
                    and line.product_uom_qty > 0
                    and line.furniture_is_kit
                )
            )
            order.furniture_kit_display_line_ids = kit_lines
            order.furniture_has_kit_lines = bool(kit_lines)
            order.furniture_kit_type_count = len(set(kit_lines.mapped('product_id').ids))
            order.furniture_kit_total_qty = sum(kit_lines.mapped('product_uom_qty'))
            order.furniture_kit_ready_qty = sum(kit_lines.mapped('furniture_finished_kit_qty'))
            order.furniture_kit_wip_qty = sum(kit_lines.mapped('furniture_wip_kit_qty'))
            order.furniture_kit_shortage_qty = sum(kit_lines.mapped('furniture_shortage_kit_qty'))
            allocations = order.furniture_kit_allocation_ids.filtered(
                lambda allocation: allocation.state != 'cancel'
            )
            if not allocations:
                order.furniture_kit_status = 'none'
                order.furniture_kit_availability_summary = False
                order.furniture_kit_has_shortage = False
                continue
            states = set(allocations.mapped('state'))
            if 'shortage' in states:
                status = 'shortage'
            elif 'wip' in states:
                status = 'wip'
            elif states == {'delivered'}:
                status = 'delivered'
            else:
                status = 'ready'
            order.furniture_kit_status = status
            order.furniture_kit_has_shortage = status == 'shortage'
            counts = defaultdict(int)
            for allocation in allocations:
                counts[allocation.state] += 1
            order.furniture_kit_availability_summary = ' | '.join(
                '%s: %s' % (labels[state], counts[state])
                for state in ('ready', 'wip', 'shortage', 'delivered')
                if counts[state]
            )

    def action_refresh_furniture_kit_availability(self):
        self._furniture_sync_kit_allocations()
        self.ensure_one()
        has_shortage = self.furniture_kit_has_shortage
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم تحديث توافر الأطقم'),
                'message': (
                    _('التوافر محدّث، ويوجد عجز يحتاج إلى إنتاج.')
                    if has_shortage
                    else _('تمت مراجعة المخزن التام ومراحل التصنيع.')
                ),
                'type': 'warning' if has_shortage else 'success',
                'sticky': False,
                'next': {
                    'type': 'ir.actions.client',
                    'tag': 'soft_reload',
                },
            },
        }

    def action_open_furniture_shortage_mps(self):
        self.ensure_one()
        if not (
            self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')
            or self.env.user.has_group('furniture_mrp.group_furniture_mrp_supervisor')
        ):
            raise AccessError(_(
                'عرض طلب إنتاج عجز MPS متاح لمدير المصنع ومشرف الإنتاج فقط.'
            ))
        if not self.furniture_shortage_mps_id:
            raise UserError(_('لا يوجد طلب إنتاج عجز مرتبط بأمر البيع.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('طلب إنتاج عجز الأطقم'),
            'res_model': 'furniture.mrp.mps',
            'res_id': self.furniture_shortage_mps_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _furniture_shortage_production_action(self, productions):
        self.ensure_one()
        productions = productions.exists().sorted(lambda production: (
            production.furniture_sale_kit_line_id.sequence
            if production.furniture_sale_kit_line_id
            else 999999,
            production.furniture_sale_kit_line_id.id
            if production.furniture_sale_kit_line_id
            else production.id,
        ))
        if not productions:
            raise UserError(_('لم يتم العثور على أوامر إنتاج عجز مرتبطة بأمر البيع.'))
        if len(productions) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('أمر إنتاج عجز الطقم'),
                'res_model': 'furniture.mrp.production',
                'res_id': productions.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('أوامر إنتاج عجز الأطقم'),
            'res_model': 'furniture.mrp.production',
            'view_mode': 'list,form',
            'domain': [('id', 'in', productions.ids)],
            'target': 'current',
        }

    def _furniture_shortage_allocation_groups(self, allocations):
        """Keep every sold Kit line as an independent production batch."""
        self.ensure_one()
        groups = {}
        sale_lines = self.env['sale.order.line']
        for allocation in allocations.sorted(lambda item: (
            item.sale_line_id.sequence,
            item.sale_line_id.id,
            item.id,
        )):
            sale_line = allocation.sale_line_id
            if not sale_line:
                continue
            sale_lines |= sale_line
            groups[sale_line.id] = (
                groups.get(sale_line.id, self.env['furniture.sale.kit.allocation'])
                | allocation
            )
        return [
            (sale_line, groups[sale_line.id])
            for sale_line in sale_lines.sorted(lambda line: (line.sequence, line.id))
        ]

    def _furniture_shortage_allocations_for_production(self, production, allocations):
        self.ensure_one()
        if production.furniture_sale_kit_line_id:
            return allocations.filtered(
                lambda allocation: allocation.sale_line_id == production.furniture_sale_kit_line_id
            )
        linked_allocation_ids = set(
            production.production_line_ids.mapped('sale_kit_allocation_id').ids
        )
        return allocations.filtered(
            lambda allocation: allocation.id in linked_allocation_ids
        )

    def _furniture_shortage_production_specs(self, allocations):
        self.ensure_one()
        specs = []
        missing_bom_products = self.env['product.product']
        for sequence, allocation in enumerate(allocations.sorted(
            lambda item: (
                item.sale_line_id.sequence,
                item.sale_line_id.id,
                item.component_product_id.display_name,
                item.id,
            )
        ), start=1):
            component = allocation.component_product_id
            source_product = component.furniture_dimension_source_product_id or component
            furniture_model = (
                component.furniture_model_id
                or allocation.kit_bom_id.furniture_model_id
                or source_product.furniture_model_id
            )
            bom = self.env['mrp.bom']._find_furniture_normal_recipe(
                source_product,
                furniture_model,
                company=self.company_id,
            )
            if not bom:
                missing_bom_products |= source_product
                continue
            specs.append((allocation, {
                'sequence': sequence * 10,
                'product_id': source_product.id,
                'furniture_order_model_id': furniture_model.id,
                'buyer_partner_id': self.partner_id.id,
                'product_qty': allocation.shortage_qty,
                'bom_id': bom.id,
                'width_cm': bom.furniture_width_cm,
                'depth_cm': bom.furniture_depth_cm,
                'height_cm': bom.furniture_height_cm,
                'sale_kit_allocation_id': allocation.id,
            }))
        if missing_bom_products:
            raise UserError(_(
                'لا يمكن تجهيز أمر الإنتاج لأن المنتجات التالية ليس لها '
                'ريسيبي تصنيع عادي: %s'
            ) % '، '.join(missing_bom_products.mapped('display_name')))
        return specs

    def _furniture_link_shortage_production_to_mps(self, production, allocations):
        self.ensure_one()
        mps = self.furniture_shortage_mps_id
        if not mps:
            return
        if production.mps_id != mps:
            production.mps_id = mps.id
        production_lines = production.production_line_ids.filtered(
            lambda line: line.sale_kit_allocation_id in allocations
        )
        lines_by_allocation = {
            line.sale_kit_allocation_id.id: line
            for line in production_lines
        }
        for allocation in allocations:
            mps_line = allocation.mps_line_id
            production_line = lines_by_allocation.get(allocation.id)
            if mps_line and production_line and mps_line.production_order_id != production:
                mps_line.production_order_id = production.id

    def _furniture_sync_shortage_production_draft(self, production, sale_line, allocations):
        self.ensure_one()
        specs = self._furniture_shortage_production_specs(allocations)
        if not specs:
            raise UserError(_('لم يتم العثور على مكونات ناقصة قابلة للإنتاج.'))

        Line = self.env['furniture.mrp.production.line']
        kit_name = sale_line.product_id.display_name
        if not production:
            _first_allocation, first_vals = specs[0]
            production = self.env['furniture.mrp.production'].create({
                'product_id': first_vals['product_id'],
                'furniture_order_model_id': first_vals['furniture_order_model_id'],
                'product_qty': sum(vals['product_qty'] for _allocation, vals in specs),
                'bom_id': first_vals['bom_id'],
                'width_cm': first_vals['width_cm'],
                'depth_cm': first_vals['depth_cm'],
                'height_cm': first_vals['height_cm'],
                'date_planned_start': fields.Datetime.now(),
                'mps_id': self.furniture_shortage_mps_id.id,
                'furniture_sale_order_id': self.id,
                'furniture_sale_kit_line_id': sale_line.id,
                'notes': _(
                    'مسودة إنتاج مكونات الطقم %(kit)s من أمر البيع %(sale)s '
                    '- عدد الأطقم: %(quantity)s - العميل: %(customer)s'
                ) % {
                    'kit': kit_name,
                    'sale': self.name,
                    'quantity': sale_line.product_uom_qty,
                    'customer': self.partner_id.display_name,
                },
            })
            created_lines = Line.create([
                {**vals, 'production_id': production.id}
                for _allocation, vals in specs
            ])
            if created_lines:
                production.write({
                    'stage_plan_line_id': created_lines[0].id,
                    'dimension_line_id': created_lines[0].id,
                })
            production.message_post(body=_(
                '🛒 تم تجهيز مسودة إنتاج مكونات الطقم %(kit)s من أمر البيع %(sale)s.'
            ) % {
                'kit': kit_name,
                'sale': self.name,
            })
            return production

        existing_by_allocation = {
            line.sale_kit_allocation_id.id: line
            for line in production.production_line_ids
            if line.sale_kit_allocation_id
        }
        valid_allocation_ids = set(allocations.ids)
        stale_lines = production.production_line_ids.filtered(
            lambda line: (
                line.sale_kit_allocation_id
                and line.sale_kit_allocation_id.id not in valid_allocation_ids
            )
        )
        if stale_lines:
            stale_lines.unlink()
        new_line_vals = []
        for allocation, vals in specs:
            existing_line = existing_by_allocation.get(allocation.id)
            if existing_line:
                update_vals = {}
                if float_compare(
                    existing_line.product_qty,
                    allocation.shortage_qty,
                    precision_digits=3,
                ) != 0:
                    update_vals['product_qty'] = allocation.shortage_qty
                if existing_line.buyer_partner_id != self.partner_id:
                    update_vals['buyer_partner_id'] = self.partner_id.id
                if update_vals:
                    existing_line.write(update_vals)
            else:
                new_line_vals.append({**vals, 'production_id': production.id})
        if new_line_vals:
            Line.create(new_line_vals)
        production.write({
            'furniture_sale_kit_line_id': sale_line.id,
            'product_qty': sum(production.production_line_ids.mapped('product_qty')),
        })
        if not production.stage_plan_line_id and production.production_line_ids:
            production.write({
                'stage_plan_line_id': production.production_line_ids[0].id,
                'dimension_line_id': production.production_line_ids[0].id,
            })
        return production

    def _furniture_prepare_shortage_productions(self):
        self.ensure_one()
        if self.state == 'cancel':
            raise UserError(_('لا يمكن تجهيز أوامر إنتاج من أمر بيع ملغي.'))

        self._furniture_sync_kit_allocations()
        allocations = self.furniture_kit_allocation_ids.filtered(
            lambda allocation: allocation.state == 'shortage' and allocation.shortage_qty > 0
        )
        active_productions = self.furniture_shortage_production_ids.filtered(
            lambda production: production.state != 'cancelled'
        ).sorted(lambda production: production.id, reverse=True)
        if not allocations:
            if active_productions:
                return active_productions
            raise UserError(_('لا يوجد عجز حالي في مكونات الأطقم.'))

        result = self.env['furniture.mrp.production']
        unused_legacy_drafts = active_productions.filtered(
            lambda production: (
                production.state == 'draft'
                and not production.furniture_sale_kit_line_id
            )
        )
        for sale_line, group_allocations in self._furniture_shortage_allocation_groups(allocations):
            candidates = active_productions.filtered(
                lambda production: production.furniture_sale_kit_line_id == sale_line
            )
            if not candidates:
                candidates = active_productions.filtered(lambda production: (
                    not production.furniture_sale_kit_line_id
                    and len(production.production_line_ids.mapped(
                        'sale_kit_allocation_id.sale_line_id'
                    )) == 1
                    and production.production_line_ids.mapped(
                        'sale_kit_allocation_id.sale_line_id'
                    ) == sale_line
                ))
            production = candidates.filtered(
                lambda candidate: candidate.state == 'draft'
            )[:1] or candidates[:1]
            if not production and unused_legacy_drafts:
                production = unused_legacy_drafts[:1]
                unused_legacy_drafts -= production
            if not production or production.state == 'draft':
                production = self._furniture_sync_shortage_production_draft(
                    production,
                    sale_line,
                    group_allocations,
                )
            result |= production
            self._furniture_link_shortage_production_to_mps(
                production,
                group_allocations,
            )
        return result

    def action_open_or_create_furniture_shortage_production(self):
        self.ensure_one()
        return self._furniture_shortage_production_action(
            self._furniture_prepare_shortage_productions()
        )

    def action_confirm(self):
        result = super().action_confirm()
        self._furniture_sync_kit_allocations()
        return result

    def _action_cancel(self):
        draft_shortage_productions = self.mapped('furniture_shortage_production_ids').filtered(
            lambda production: production.state == 'draft'
        )
        result = super()._action_cancel()
        self._furniture_release_kit_allocations()
        if draft_shortage_productions:
            draft_shortage_productions.action_cancel()
        return result

    def action_draft(self):
        result = super().action_draft()
        self._furniture_sync_kit_allocations()
        return result

    def _furniture_release_kit_allocations(self):
        for order in self:
            allocations = order.furniture_kit_allocation_ids.sudo()
            allocations.mapped('wip_source_ids').unlink()
            allocations.write({
                'finished_allocated_qty': 0.0,
                'wip_allocated_qty': 0.0,
                'shortage_qty': 0.0,
                'state': 'cancel',
                'stage_summary': False,
                'expected_date': False,
            })
            mps = order.furniture_shortage_mps_id.sudo()
            if mps and mps.state != 'done':
                if not mps.production_order_ids and mps.state == 'draft':
                    order.furniture_shortage_mps_id = False
                    mps.unlink()
                elif mps.state != 'cancelled':
                    mps.action_cancel()

    @api.model
    def _furniture_finished_location(self):
        return (
            self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False)
            or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        )

    @api.model
    def _furniture_stage_for_line(self, line):
        production = line.production_id
        selected_codes = line._selected_stage_codes()
        if not production or not selected_codes:
            return (False, False)
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        last_completed = False
        for stage_code in selected_codes:
            stage_order = production._stage_order_record(stage_code)
            if not stage_order:
                continue
            active_lines = stage_order._get_stage_line_ids_data('active_production_line_ids_data')
            quality_lines = stage_order._get_stage_line_ids_data('quality_production_line_ids_data')
            completed_lines = stage_order._get_stage_line_ids_data('completed_production_line_ids_data')
            if line in active_lines:
                return (stage_code, _('%s - داخل الصالة') % stage_labels.get(stage_code, stage_code))
            if line in quality_lines:
                return (stage_code, _('%s - فحص الجودة') % stage_labels.get(stage_code, stage_code))
            if line in completed_lines:
                last_completed = stage_code
        current_code = last_completed or line.first_stage_started_stage or line.planned_start_stage or selected_codes[0]
        suffix = _('جاهز للتحويل') if last_completed else _('بدأ التصنيع')
        return (current_code, '%s - %s' % (stage_labels.get(current_code, current_code), suffix))

    @api.model
    def _furniture_build_wip_candidates(self, products, company):
        """Return real, traceable unfinished outputs without touching stock.

        A regular source is the remaining output of an exact production line.
        Legacy carryover is included only when it has no source line, otherwise
        it would count the same pieces twice.
        """
        product_ids = set(products.ids)
        candidates = defaultdict(list)
        if not product_ids:
            return candidates

        Line = self.env['furniture.mrp.production.line'].sudo().with_context(active_test=False)
        lines = Line.search([
            ('active', '=', True),
            ('consolidated_into_line_id', '=', False),
            ('first_stage_started', '=', True),
            ('production_id.company_id', '=', company.id),
            ('production_id.state', 'not in', ('draft', 'cancelled')),
        ], order='production_id asc, sequence asc, id asc')

        transferred_by_key = defaultdict(float)
        transferred_lines = self.env['furniture.mrp.finished.transfer.line'].sudo().search([
            ('source_production_line_id', 'in', lines.ids),
            ('product_id', 'in', list(product_ids)),
            ('state', '=', 'transferred'),
        ])
        for transfer in transferred_lines:
            qty = transfer.product_uom_id._compute_quantity(
                transfer.qty,
                transfer.product_id.uom_id,
                round=False,
            )
            transferred_by_key[(transfer.source_production_line_id.id, transfer.product_id.id)] += qty

        for line in lines:
            production = line.production_id
            for spec in production._get_finished_output_specs_from_lines(line, ensure_storable=False):
                product = spec.get('product')
                if not product or product.id not in product_ids:
                    continue
                qty = (spec.get('uom') or product.uom_id)._compute_quantity(
                    spec.get('qty') or 0.0,
                    product.uom_id,
                    round=False,
                )
                qty -= transferred_by_key.get((line.id, product.id), 0.0)
                if float_compare(qty, 0.0, precision_rounding=product.uom_id.rounding) <= 0:
                    continue
                stage_code, stage_label = self._furniture_stage_for_line(line)
                candidates[product.id].append({
                    'source_key': ('line', line.id),
                    'production_line': line,
                    'carryover_line': self.env['furniture.mrp.carryover.line'],
                    'qty': qty,
                    'stage_code': stage_code,
                    'stage_label': stage_label,
                    'source_reference': production.name,
                    'expected_date': production.date_planned_finish or production.date_planned_start,
                })

        carryovers = self.env['furniture.mrp.carryover.line'].sudo().search([
            ('product_id', 'in', list(product_ids)),
            ('production_id.company_id', '=', company.id),
            ('source_production_line_id', '=', False),
            ('state', 'in', ('selected', 'started', 'stage_done')),
            ('qty', '>', 0.0),
        ], order='production_id asc, id asc')
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        for carryover in carryovers:
            product = carryover.product_id
            qty = carryover.product_uom_id._compute_quantity(
                carryover.qty,
                product.uom_id,
                round=False,
            )
            candidates[product.id].append({
                'source_key': ('carryover', carryover.id),
                'production_line': self.env['furniture.mrp.production.line'],
                'carryover_line': carryover,
                'qty': qty,
                'stage_code': carryover.current_stage,
                'stage_label': stage_labels.get(carryover.current_stage, carryover.current_stage),
                'source_reference': carryover.source_origin or carryover.production_id.name,
                'expected_date': carryover.production_id.date_planned_finish or carryover.production_id.date_planned_start,
            })
        return candidates

    @api.model
    def _furniture_refresh_orders_for_products(self, products):
        if not products or self.env.context.get('furniture_skip_kit_stock_refresh'):
            return self.env['sale.order']
        allocations = self.env['furniture.sale.kit.allocation'].sudo().search([
            ('component_product_id', 'in', products.ids),
            ('order_id.state', '=', 'sale'),
        ])
        orders = allocations.mapped('order_id')
        if not orders:
            return orders
        active_moves = orders.order_line.move_ids.filtered(
            lambda move: move.state in ('confirmed', 'waiting', 'partially_available', 'assigned')
        )
        if active_moves:
            active_moves.with_context(furniture_skip_kit_stock_refresh=True)._action_assign()
        orders.with_context(furniture_skip_kit_stock_refresh=True)._furniture_sync_kit_allocations()
        return orders

    def _furniture_sync_kit_allocations(self):
        Allocation = self.env['furniture.sale.kit.allocation'].sudo()
        Source = self.env['furniture.sale.kit.wip.allocation'].sudo()
        finished_location = self._furniture_finished_location()
        for order in self:
            if not order.id or order.state == 'cancel':
                continue
            # A transaction-level lock makes two simultaneously confirmed sales
            # orders unable to promise the same unfinished pieces.
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ['furniture_kit_wip_company_%s' % order.company_id.id],
            )
            kit_lines = order.order_line.filtered(
                lambda line: not line.display_type and line.product_id and line.product_uom_qty > 0
            )
            expected_by_key = {}
            for sale_line in kit_lines:
                bom = sale_line._furniture_kit_bom()
                if not bom:
                    continue
                component_data = sale_line._get_bom_component_qty(bom)
                ordered_kit_qty = sale_line.product_uom._compute_quantity(
                    sale_line.product_uom_qty,
                    sale_line.product_id.uom_id,
                    round=False,
                )
                for product_id, data in component_data.items():
                    product = self.env['product.product'].browse(product_id)
                    qty_per_kit = data['qty']
                    expected_by_key[(sale_line.id, product_id)] = {
                        'order_id': order.id,
                        'sale_line_id': sale_line.id,
                        'kit_bom_id': bom.id,
                        'component_product_id': product_id,
                        'product_uom_id': product.uom_id.id,
                        'qty_per_kit': qty_per_kit,
                        'ordered_qty': ordered_kit_qty * qty_per_kit,
                        'company_id': order.company_id.id,
                    }

            existing = order.furniture_kit_allocation_ids.sudo()
            existing_by_key = {
                (allocation.sale_line_id.id, allocation.component_product_id.id): allocation
                for allocation in existing
            }
            active_allocations = Allocation
            for key, vals in expected_by_key.items():
                allocation = existing_by_key.get(key)
                if allocation:
                    changed = any(
                        allocation[field_name].id != value
                        if allocation._fields[field_name].type == 'many2one'
                        else float_compare(allocation[field_name], value, precision_digits=6) != 0
                        if allocation._fields[field_name].type == 'float'
                        else allocation[field_name] != value
                        for field_name, value in vals.items()
                        if field_name not in ('order_id', 'sale_line_id')
                    )
                    if changed:
                        allocation.write(vals)
                else:
                    allocation = Allocation.create(vals)
                active_allocations |= allocation

            stale = existing - active_allocations
            if stale:
                stale.mapped('wip_source_ids').unlink()
                stale.unlink()

            if not active_allocations:
                order._furniture_sync_shortage_mps()
                continue

            Source.search([('allocation_id', 'in', active_allocations.ids)]).unlink()
            sale_moves = order.order_line.move_ids.filtered(
                lambda move: move.state in ('confirmed', 'waiting', 'partially_available', 'assigned')
            )
            if order.state == 'sale' and sale_moves:
                sale_moves.with_context(furniture_skip_kit_stock_refresh=True)._action_assign()

            products = active_allocations.mapped('component_product_id')
            wip_candidates = self._furniture_build_wip_candidates(products, order.company_id)
            other_sources = Source.search([
                ('allocation_id.order_id.state', '=', 'sale'),
                ('allocation_id', 'not in', active_allocations.ids),
                ('component_product_id', 'in', products.ids),
            ])
            used_by_source = defaultdict(float)
            for source in other_sources:
                key = (
                    ('line', source.production_line_id.id)
                    if source.production_line_id
                    else ('carryover', source.carryover_line_id.id)
                )
                used_by_source[key] += source.qty

            preview_finished_pool = {}
            if order.state != 'sale' and finished_location:
                Quant = self.env['stock.quant'].sudo().with_company(order.company_id)
                for product in products:
                    preview_finished_pool[product.id] = max(
                        Quant._get_available_quantity(product, finished_location, strict=False),
                        0.0,
                    )

            source_vals = []
            for allocation in active_allocations.sorted(
                lambda item: (item.sale_line_id.sequence, item.sale_line_id.id, item.component_product_id.id)
            ):
                delivered_qty, reserved_qty = allocation.sale_line_id._furniture_component_move_quantities(
                    allocation.component_product_id,
                    finished_location,
                )
                remaining_qty = max(allocation.ordered_qty - delivered_qty, 0.0)
                if order.state == 'sale':
                    finished_qty = min(reserved_qty, remaining_qty)
                else:
                    available_finished = preview_finished_pool.get(allocation.component_product_id.id, 0.0)
                    finished_qty = min(available_finished, remaining_qty)
                    preview_finished_pool[allocation.component_product_id.id] = max(
                        available_finished - finished_qty,
                        0.0,
                    )
                needed_wip = max(remaining_qty - finished_qty, 0.0)
                allocated_wip = 0.0
                stage_labels = []
                expected_dates = []
                for candidate in wip_candidates.get(allocation.component_product_id.id, []):
                    free_qty = max(
                        candidate['qty'] - used_by_source.get(candidate['source_key'], 0.0),
                        0.0,
                    )
                    take_qty = min(free_qty, needed_wip - allocated_wip)
                    if float_compare(
                        take_qty,
                        0.0,
                        precision_rounding=allocation.product_uom_id.rounding,
                    ) <= 0:
                        continue
                    source_vals.append({
                        'allocation_id': allocation.id,
                        'production_line_id': candidate['production_line'].id,
                        'carryover_line_id': candidate['carryover_line'].id,
                        'component_product_id': allocation.component_product_id.id,
                        'qty': take_qty,
                        'stage_code': candidate['stage_code'],
                        'stage_label': candidate['stage_label'],
                        'source_reference': candidate['source_reference'],
                        'expected_date': candidate['expected_date'],
                        'company_id': order.company_id.id,
                    })
                    used_by_source[candidate['source_key']] += take_qty
                    allocated_wip += take_qty
                    if candidate['stage_label'] and candidate['stage_label'] not in stage_labels:
                        stage_labels.append(candidate['stage_label'])
                    if candidate['expected_date']:
                        expected_dates.append(candidate['expected_date'])
                    if float_compare(
                        allocated_wip,
                        needed_wip,
                        precision_rounding=allocation.product_uom_id.rounding,
                    ) >= 0:
                        break

                shortage_qty = max(remaining_qty - finished_qty - allocated_wip, 0.0)
                if float_is_zero(remaining_qty, precision_rounding=allocation.product_uom_id.rounding):
                    state = 'delivered'
                elif float_compare(shortage_qty, 0.0, precision_rounding=allocation.product_uom_id.rounding) > 0:
                    state = 'shortage'
                elif float_compare(allocated_wip, 0.0, precision_rounding=allocation.product_uom_id.rounding) > 0:
                    state = 'wip'
                else:
                    state = 'ready'
                allocation.write({
                    'delivered_qty': delivered_qty,
                    'required_qty': remaining_qty,
                    'finished_allocated_qty': finished_qty,
                    'wip_allocated_qty': allocated_wip,
                    'shortage_qty': shortage_qty,
                    'state': state,
                    'stage_summary': '، '.join(stage_labels) or False,
                    'expected_date': max(expected_dates) if expected_dates else False,
                })
            if source_vals:
                Source.create(source_vals)
            order._furniture_sync_shortage_mps()
        return True

    def _furniture_sync_shortage_mps(self):
        self.ensure_one()
        allocations = self.furniture_kit_allocation_ids.sudo().filtered(
            lambda allocation: allocation.shortage_qty > 0 and allocation.state == 'shortage'
        )
        mps = self.furniture_shortage_mps_id.sudo()
        if self.state != 'sale' or not allocations:
            if mps and mps.state == 'draft' and not mps.production_order_ids:
                self.furniture_shortage_mps_id = False
                mps.unlink()
            return

        if not mps:
            today = fields.Date.context_today(self)
            week_start = today - timedelta(days=today.weekday())
            mps = self.env['furniture.mrp.mps'].sudo().create({
                'date_from': week_start,
                'company_id': self.company_id.id,
                'responsible_id': self.user_id.id or self.env.user.id,
                'priority': '1',
                'furniture_sale_order_id': self.id,
                'notes': _('طلب إنتاج تلقائي لعجز مكونات الأطقم في أمر البيع %s') % self.name,
            })
            self.furniture_shortage_mps_id = mps.id

        if mps.state != 'draft':
            return
        valid_mps_lines = self.env['furniture.mrp.mps.line']
        for allocation in allocations:
            component = allocation.component_product_id
            source_product = component.furniture_dimension_source_product_id or component
            furniture_model = (
                component.furniture_model_id
                or allocation.kit_bom_id.furniture_model_id
                or source_product.furniture_model_id
            )
            bom = self.env['mrp.bom'].sudo()._find_furniture_normal_recipe(
                source_product,
                furniture_model,
                company=self.company_id,
            )
            if not bom:
                continue
            mps_line = allocation.mps_line_id
            vals = {
                'mps_id': mps.id,
                'product_id': source_product.id,
                'furniture_order_model_id': furniture_model.id,
                'product_qty': allocation.shortage_qty,
                'bom_id': bom.id,
                'width_cm': bom.furniture_width_cm,
                'depth_cm': bom.furniture_depth_cm,
                'height_cm': bom.furniture_height_cm,
                'sale_kit_allocation_id': allocation.id,
            }
            if mps_line and not mps_line.production_order_id:
                mps_line.write(vals)
            elif not mps_line:
                mps_line = self.env['furniture.mrp.mps.line'].sudo().create(vals)
                allocation.mps_line_id = mps_line.id
            valid_mps_lines |= mps_line
        stale_lines = mps.line_ids.filtered(
            lambda line: line.sale_kit_allocation_id and line not in valid_mps_lines and not line.production_order_id
        )
        if stale_lines:
            stale_lines.unlink()
        mps.demand_qty = sum(allocations.mapped('shortage_qty'))
        linked_productions = self.furniture_shortage_production_ids.filtered(
            lambda production: production.state != 'cancelled'
        )
        for production in linked_productions:
            production_allocations = self._furniture_shortage_allocations_for_production(
                production,
                allocations,
            )
            if production_allocations:
                self._furniture_link_shortage_production_to_mps(
                    production,
                    production_allocations,
                )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    product_id = fields.Many2one(
        domain="[('sale_ok', '=', True), ('furniture_has_active_sale_kit_recipe', '=', True)]",
    )
    product_template_id = fields.Many2one(
        domain="[('sale_ok', '=', True), ('furniture_has_active_sale_kit_recipe', '=', True)]",
    )

    furniture_kit_allocation_ids = fields.One2many(
        'furniture.sale.kit.allocation',
        'sale_line_id',
        string='توافر مكونات الطقم',
        copy=False,
    )
    furniture_is_kit = fields.Boolean(string='طقم أثاث', compute='_compute_furniture_kit_availability')
    furniture_kit_availability = fields.Char(
        string='توافر الطقم',
        compute='_compute_furniture_kit_availability',
    )
    furniture_finished_kit_qty = fields.Float(
        string='أطقم جاهزة',
        compute='_compute_furniture_kit_availability',
        digits=(16, 3),
    )
    furniture_wip_kit_qty = fields.Float(
        string='أطقم تحت التصنيع',
        compute='_compute_furniture_kit_availability',
        digits=(16, 3),
    )
    furniture_shortage_kit_qty = fields.Float(
        string='عجز الأطقم',
        compute='_compute_furniture_kit_availability',
        digits=(16, 3),
    )
    furniture_kit_state = fields.Selection([
        ('none', 'جارٍ الحساب'),
        ('ready', 'جاهز من المخزن التام'),
        ('wip', 'محجوز تحت التصنيع'),
        ('shortage', 'يوجد عجز'),
        ('delivered', 'تم التسليم'),
    ], string='حالة الطقم', compute='_compute_furniture_kit_availability')
    furniture_kit_components_html = fields.Html(
        string='مكونات الطقم',
        compute='_compute_furniture_kit_availability',
        sanitize=True,
    )

    def _furniture_kit_bom(self):
        self.ensure_one()
        if not self.product_id:
            return self.env['mrp.bom']
        bom = self.env['mrp.bom'].sudo()._bom_find(
            self.product_id,
            company_id=self.company_id.id,
            bom_type='phantom',
        ).get(self.product_id)
        return bom if bom and bom.furniture_model_id else self.env['mrp.bom']

    @api.depends(
        'product_id',
        'product_uom_qty',
        'furniture_kit_allocation_ids.qty_per_kit',
        'furniture_kit_allocation_ids.required_qty',
        'furniture_kit_allocation_ids.finished_allocated_qty',
        'furniture_kit_allocation_ids.wip_allocated_qty',
        'furniture_kit_allocation_ids.shortage_qty',
    )
    def _compute_furniture_kit_availability(self):
        for line in self:
            allocations = line.furniture_kit_allocation_ids.filtered(
                lambda allocation: allocation.qty_per_kit > 0 and allocation.state != 'cancel'
            )
            line.furniture_is_kit = bool(allocations or line._furniture_kit_bom())
            line.furniture_kit_state = 'none'
            line.furniture_kit_components_html = False
            if not allocations:
                line.furniture_finished_kit_qty = 0.0
                line.furniture_wip_kit_qty = 0.0
                line.furniture_shortage_kit_qty = 0.0
                line.furniture_kit_availability = _('يتم الحساب تلقائيًا') if line.furniture_is_kit else False
                continue
            finished_kits = min(
                allocation.finished_allocated_qty / allocation.qty_per_kit
                for allocation in allocations
            )
            covered_kits = min(
                (allocation.finished_allocated_qty + allocation.wip_allocated_qty) / allocation.qty_per_kit
                for allocation in allocations
            )
            shortage_kits = max(
                allocation.shortage_qty / allocation.qty_per_kit
                for allocation in allocations
            )
            line.furniture_finished_kit_qty = finished_kits
            line.furniture_wip_kit_qty = max(covered_kits - finished_kits, 0.0)
            line.furniture_shortage_kit_qty = shortage_kits
            states = set(allocations.mapped('state'))
            if 'shortage' in states:
                line.furniture_kit_state = 'shortage'
            elif 'wip' in states:
                line.furniture_kit_state = 'wip'
            elif states == {'delivered'}:
                line.furniture_kit_state = 'delivered'
            else:
                line.furniture_kit_state = 'ready'
            line.furniture_kit_availability = _('تام %(ready)s | تصنيع %(wip)s | عجز %(shortage)s') % {
                'ready': line.furniture_finished_kit_qty,
                'wip': line.furniture_wip_kit_qty,
                'shortage': line.furniture_shortage_kit_qty,
            }
            line.furniture_kit_components_html = line._furniture_render_kit_components(allocations)

    @api.model
    def _furniture_qty_label(self, qty):
        return ('%.3f' % (qty or 0.0)).rstrip('0').rstrip('.') or '0'

    def _furniture_render_kit_components(self, allocations):
        self.ensure_one()
        state_labels = dict(self.env['furniture.sale.kit.allocation']._fields['state'].selection)
        state_classes = {
            'ready': 'is-ready',
            'wip': 'is-wip',
            'shortage': 'is-shortage',
            'delivered': 'is-delivered',
        }
        rows = []
        for allocation in allocations.sorted(lambda item: (item.component_product_id.display_name, item.id)):
            if allocation.state == 'ready':
                location_label = _('المخزن التام')
            elif allocation.state == 'wip':
                location_label = allocation.stage_summary or _('داخل التصنيع')
            elif allocation.state == 'shortage':
                location_label = _('يحتاج إنتاج')
            elif allocation.state == 'delivered':
                location_label = _('تم التسليم')
            else:
                location_label = '-'
            rows.append(Markup(
                '<tr class="o_furniture_kit_component_row {state_class}">'
                '<td class="o_furniture_kit_component_name">{component}</td>'
                '<td>{required}</td>'
                '<td>{finished}</td>'
                '<td>{wip}</td>'
                '<td>{shortage}</td>'
                '<td class="o_furniture_kit_component_location">{location}</td>'
                '<td><span class="o_furniture_kit_component_state">{state}</span></td>'
                '</tr>'
            ).format(
                state_class=state_classes.get(allocation.state, ''),
                component=allocation.component_product_id.display_name,
                required=self._furniture_qty_label(allocation.required_qty),
                finished=self._furniture_qty_label(allocation.finished_allocated_qty),
                wip=self._furniture_qty_label(allocation.wip_allocated_qty),
                shortage=self._furniture_qty_label(allocation.shortage_qty),
                location=location_label,
                state=state_labels.get(allocation.state, allocation.state),
            ))
        return Markup(
            '<div class="o_furniture_kit_components_table_wrap">'
            '<table class="o_furniture_kit_components_table">'
            '<thead><tr>'
            '<th>القطعة</th><th>المطلوب</th><th>من التام</th>'
            '<th>في التصنيع</th><th>العجز</th><th>مكان القطعة</th><th>الحالة</th>'
            '</tr></thead><tbody>{rows}</tbody>'
            '</table></div>'
        ).format(rows=Markup('').join(rows))

    def _furniture_component_move_quantities(self, component, finished_location):
        self.ensure_one()
        delivered = 0.0
        reserved = 0.0
        moves = self.move_ids.filtered(lambda move: move.product_id == component and not move.scrapped)
        for move in moves:
            qty = move.product_uom._compute_quantity(move.quantity, component.uom_id, round=False)
            if move.state == 'done':
                if move.location_dest_id.usage == 'customer':
                    delivered += qty
                elif move.location_id.usage == 'customer':
                    delivered -= qty
            elif move.state not in ('cancel', 'draft') and finished_location and (
                move.location_id == finished_location
                or move.location_id.location_id == finished_location
            ):
                reserved += qty
        return (max(delivered, 0.0), max(reserved, 0.0))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('furniture_skip_kit_sync'):
            lines.mapped('order_id').filtered(lambda order: order.state != 'cancel').with_context(
                furniture_skip_kit_sync=True
            )._furniture_sync_kit_allocations()
        return lines

    def write(self, vals):
        orders = self.mapped('order_id')
        result = super().write(vals)
        if (
            not self.env.context.get('furniture_skip_kit_sync')
            and {'product_id', 'product_uom_qty', 'product_uom'} & set(vals)
        ):
            orders.filtered(lambda order: order.state != 'cancel').with_context(
                furniture_skip_kit_sync=True
            )._furniture_sync_kit_allocations()
        return result

    def unlink(self):
        orders = self.mapped('order_id')
        result = super().unlink()
        if not self.env.context.get('furniture_skip_kit_sync'):
            orders.filtered(lambda order: order.exists() and order.state != 'cancel').with_context(
                furniture_skip_kit_sync=True
            )._furniture_sync_kit_allocations()
        return result


class FurnitureSaleKitAllocation(models.Model):
    _name = 'furniture.sale.kit.allocation'
    _description = 'حجز مكونات طقم البيع'
    _order = 'order_id desc, sale_line_id, component_product_id'
    _rec_name = 'component_product_id'

    order_id = fields.Many2one('sale.order', string='أمر البيع', required=True, ondelete='cascade', index=True)
    sale_line_id = fields.Many2one('sale.order.line', string='سطر الطقم', required=True, ondelete='cascade', index=True)
    kit_product_id = fields.Many2one(related='sale_line_id.product_id', string='الطقم', store=True, readonly=True)
    kit_bom_id = fields.Many2one('mrp.bom', string='وصفة الطقم', required=True, ondelete='restrict')
    component_product_id = fields.Many2one('product.product', string='مكون الطقم', required=True, ondelete='restrict', index=True)
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True, ondelete='restrict')
    qty_per_kit = fields.Float(string='الكمية في الطقم', required=True, digits=(16, 3))
    ordered_qty = fields.Float(string='إجمالي المطلوب', digits=(16, 3), readonly=True)
    delivered_qty = fields.Float(string='تم تسليمه', digits=(16, 3), readonly=True)
    required_qty = fields.Float(string='المتبقي المطلوب', digits=(16, 3), readonly=True)
    finished_allocated_qty = fields.Float(string='محجوز من التام', digits=(16, 3), readonly=True)
    wip_allocated_qty = fields.Float(string='محجوز تحت التصنيع', digits=(16, 3), readonly=True)
    shortage_qty = fields.Float(string='العجز', digits=(16, 3), readonly=True)
    stage_summary = fields.Char(string='مكان الشغل المحجوز', readonly=True)
    expected_date = fields.Datetime(string='التاريخ المتوقع', readonly=True)
    state = fields.Selection([
        ('ready', 'جاهز من المخزن التام'),
        ('wip', 'محجوز تحت التصنيع'),
        ('shortage', 'عجز يحتاج إنتاج'),
        ('delivered', 'تم التسليم'),
        ('cancel', 'ملغي'),
    ], string='الحالة', default='shortage', required=True, readonly=True, index=True)
    wip_source_ids = fields.One2many(
        'furniture.sale.kit.wip.allocation',
        'allocation_id',
        string='مصادر الحجز تحت التصنيع',
        copy=False,
    )
    mps_line_id = fields.Many2one(
        'furniture.mrp.mps.line',
        string='سطر طلب إنتاج العجز',
        copy=False,
        readonly=True,
        ondelete='set null',
    )
    company_id = fields.Many2one('res.company', string='الشركة', required=True, index=True)

    _sql_constraints = [
        (
            'sale_line_component_unique',
            'unique(sale_line_id, component_product_id)',
            'لا يمكن تكرار نفس مكون الطقم في نفس سطر البيع.',
        ),
    ]


class FurnitureSaleKitWipAllocation(models.Model):
    _name = 'furniture.sale.kit.wip.allocation'
    _description = 'مصدر حجز مكون طقم تحت التصنيع'
    _order = 'expected_date, id'

    allocation_id = fields.Many2one(
        'furniture.sale.kit.allocation',
        string='حجز مكون الطقم',
        required=True,
        ondelete='cascade',
        index=True,
    )
    component_product_id = fields.Many2one('product.product', string='المكون', required=True, ondelete='restrict', index=True)
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر التشغيل',
        ondelete='cascade',
        index=True,
    )
    carryover_line_id = fields.Many2one(
        'furniture.mrp.carryover.line',
        string='سطر الشغل القديم',
        ondelete='cascade',
        index=True,
    )
    qty = fields.Float(string='الكمية المحجوزة', required=True, digits=(16, 3))
    stage_code = fields.Selection(FURNITURE_STAGE_SELECTION, string='المرحلة')
    stage_label = fields.Char(string='حالة المرحلة')
    source_reference = fields.Char(string='مرجع أمر التشغيل')
    expected_date = fields.Datetime(string='التاريخ المتوقع')
    company_id = fields.Many2one('res.company', string='الشركة', required=True, index=True)


class FurnitureMrpMPS(models.Model):
    _inherit = 'furniture.mrp.mps'

    furniture_sale_order_id = fields.Many2one(
        'sale.order',
        string='أمر البيع مصدر العجز',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
    )

    def action_generate_production(self):
        self.ensure_one()
        if self.furniture_sale_order_id:
            if self.state != 'confirmed':
                raise UserError(_('يجب تأكيد الجدول أولاً قبل فتح أوامر الإنتاج.'))
            sale_order = self.furniture_sale_order_id
            productions = sale_order._furniture_prepare_shortage_productions()
            allocations = sale_order.furniture_kit_allocation_ids.filtered(
                lambda allocation: (
                    allocation.state == 'shortage'
                    and allocation.shortage_qty > 0
                )
            )
            for production in productions:
                production_allocations = sale_order._furniture_shortage_allocations_for_production(
                    production,
                    allocations,
                )
                if production.mps_id != self:
                    production.mps_id = self.id
                if production_allocations:
                    sale_order._furniture_link_shortage_production_to_mps(
                        production,
                        production_allocations,
                    )
            return sale_order._furniture_shortage_production_action(productions)
        return super().action_generate_production()


class FurnitureMrpMPSLine(models.Model):
    _inherit = 'furniture.mrp.mps.line'

    sale_kit_allocation_id = fields.Many2one(
        'furniture.sale.kit.allocation',
        string='حجز طقم البيع',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
    )


class FurnitureMrpProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    furniture_sale_order_id = fields.Many2one(
        'sale.order',
        string='أمر البيع مصدر العجز',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
    )
    furniture_sale_kit_line_id = fields.Many2one(
        'sale.order.line',
        string='سطر طقم البيع',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
        help='يفصل أمر إنتاج مكونات كل طقم مباع عن باقي أطقم أمر البيع.',
    )


class FurnitureMrpProductionLine(models.Model):
    _inherit = 'furniture.mrp.production.line'

    sale_kit_allocation_id = fields.Many2one(
        'furniture.sale.kit.allocation',
        string='حجز طقم البيع',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
    )
    sale_kit_product_id = fields.Many2one(
        'product.product',
        string='طقم البيع',
        related='sale_kit_allocation_id.kit_product_id',
        store=True,
        readonly=True,
    )


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        result = super()._action_done(cancel_backorder=cancel_backorder)
        if self.env.context.get('furniture_skip_kit_stock_refresh'):
            return result
        done_moves = (self | result).filtered(lambda move: move.state == 'done')
        sale_orders = done_moves.mapped('sale_line_id.order_id').filtered(lambda order: order.state == 'sale')
        finished_location = self.env['sale.order']._furniture_finished_location()
        incoming_products = done_moves.filtered(lambda move: (
            finished_location
            and (
                move.location_dest_id == finished_location
                or move.location_dest_id.location_id == finished_location
            )
        )).mapped('product_id')
        if incoming_products:
            self.env['sale.order']._furniture_refresh_orders_for_products(incoming_products)
        if sale_orders:
            sale_orders.with_context(
                furniture_skip_kit_stock_refresh=True
            )._furniture_sync_kit_allocations()
        return result


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _pre_action_done_hook(self):
        self._furniture_check_complete_kit_quantities()
        return super()._pre_action_done_hook()

    def _furniture_check_complete_kit_quantities(self):
        for picking in self.filtered(lambda item: item.picking_type_id.code == 'outgoing'):
            has_explicit_pick = any(
                move.picked and move.state not in ('done', 'cancel')
                for move in picking.move_ids
            )
            sale_lines = picking.move_ids.mapped('sale_line_id').filtered(lambda line: line._furniture_kit_bom())
            for sale_line in sale_lines:
                bom = sale_line._furniture_kit_bom()
                components = sale_line._get_bom_component_qty(bom)
                if not components:
                    continue
                kit_quantities = []
                any_quantity = False
                for product_id, data in components.items():
                    component = self.env['product.product'].browse(product_id)
                    component_qty = sum(
                        move.product_uom._compute_quantity(
                            move._get_picked_quantity() if move.picked else move.quantity,
                            component.uom_id,
                            round=False,
                        )
                        for move in picking.move_ids.filtered(
                            lambda move: move.sale_line_id == sale_line
                            and move.product_id == component
                            and move.state not in ('done', 'cancel')
                            and (not has_explicit_pick or move.picked)
                        )
                    )
                    any_quantity = any_quantity or not float_is_zero(
                        component_qty,
                        precision_rounding=component.uom_id.rounding,
                    )
                    kit_quantities.append((component, component_qty / data['qty']))
                if not any_quantity:
                    continue
                expected_kit_qty = kit_quantities[0][1]
                mismatched = [
                    component.display_name
                    for component, kit_qty in kit_quantities
                    if float_compare(kit_qty, expected_kit_qty, precision_digits=3) != 0
                ]
                if mismatched:
                    raise UserError(_(
                        'لا يمكن تسليم جزء ناقص من الطقم %(kit)s. '\
                        'الكميات المختارة لا تكوّن نفس عدد الأطقم لكل المكونات. راجع: %(components)s'
                    ) % {
                        'kit': sale_line.product_id.display_name,
                        'components': '، '.join(mismatched),
                    })
                if float_compare(
                    expected_kit_qty,
                    round(expected_kit_qty),
                    precision_digits=3,
                ) != 0:
                    raise UserError(_(
                        'لا يمكن تسليم كسر طقم من %(kit)s. اختار عدد أطقم كامل.'
                    ) % {'kit': sale_line.product_id.display_name})
        return True
