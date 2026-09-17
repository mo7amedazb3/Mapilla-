"""Approved workspaces and atomic withdrawal of unstarted approvals."""
from collections import defaultdict
import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare
from odoo.addons.furniture_mrp.models.mrp_production_order import FURNITURE_STAGE_SELECTION
from odoo.addons.furniture_mrp.models.mrp_lane_handoff import FurnitureMrpLaneHandoff, StockMoveLaneHandoff
from .production_plan import LANES


class ApprovedTracking(models.Model):
    _inherit = 'furniture.need.to.produce'

    approval_cancellation_history = fields.Json(readonly=True, copy=False)

    def _approval_family(self):
        """Connected components, including every owner of a shared bulk MO."""
        family = self
        while family:
            linked = self.sudo().search([('stage_ids.production_id', 'in', family.stage_ids.production_id.ids)])
            expanded = family | linked
            if expanded == family:
                break
            family = expanded
        return family

    def _approval_token(self):
        payload = [(p.id, fields.Datetime.to_string(p.approved_at), sorted(p.stage_ids.production_id.ids))
                   for p in self.sorted('id')]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()

    def _approval_stock_moves(self):
        orders = self.stage_ids.production_id.sudo()
        lines = orders.production_line_ids
        linked = self.env['stock.move'].sudo().search(['|',
            ('furniture_source_production_line_id', 'in', lines.ids),
            ('furniture_source_production_line_ids', 'in', lines.ids)])
        return linked | orders.stock_move_ids

    def _approval_cancel_blocker(self):
        orders = self.stage_ids.production_id.sudo()
        if not orders or any(p.state != 'approved' for p in self):
            return _('الإلغاء متاح للاعتماد القائم الذي له أوامر إنتاج فقط.')
        if self._approval_family() != self:
            return _('الأوامر مشتركة مع قطع أخرى. حدّث القائمة وألغِ الكارت الكامل.')
        if any(o.need_to_produce_stage_id not in self.stage_ids for o in orders):
            return _('يوجد أمر غير مطابق لربط الاعتماد؛ راجع الخطة.')
        if any(o.state not in ('draft', 'confirmed', 'cancelled') for o in orders):
            return _('بدأ تنفيذ أحد الأوامر أو اكتمل؛ لا يمكن حذف إنتاج منفّذ من إلغاء الاعتماد.')
        if any(state not in (False, 'pending') for o in orders for _code, _stage, state in o._required_stage_infos()):
            return _('بدأت إحدى المراحل أو دخلت الجودة؛ يلزم مراجعة التنفيذ أولًا.')
        moves = self._approval_stock_moves()
        if moves.filtered(lambda m: m.state == 'done'):
            return _('توجد حركة مخزون منفّذة على الأوامر؛ يلزم مراجعة حركاتها أولًا.')
        if moves.filtered(lambda m: m.state != 'cancel' and bool(m.furniture_source_production_line_ids.production_id - orders)):
            return _('توجد حركة مخزون مشتركة مع أوامر خارج هذا الاعتماد؛ راجع الحركة أولًا.')
        if self.env['furniture.mrp.lane.output'].sudo().search_count([('production_id', 'in', orders.ids)]):
            return _('توجد مخرجات إنتاج مسجلة؛ لا يمكن حذف أوامرها.')
        handoffs = self.env['furniture.mrp.lane.handoff'].sudo().search([
            ('downstream_production_id', 'in', orders.ids)])
        if handoffs.filtered(lambda h: h.state == 'consumed' or h.transfer_move_id.state == 'done'):
            return _('تم استلام تحويل من مرحلة سابقة؛ يلزم مراجعة الاستلام أولًا.')
        dependencies = self.env['furniture.need.to.produce.input'].sudo().search([
            ('piece_id', 'not in', self.ids), ('piece_id.state', 'in', ['approved', 'done']),
            '|', ('source_stage_id.production_id', 'in', orders.ids),
            ('source_line_id.production_id', 'in', orders.ids)])
        if dependencies:
            return _('إنتاج هذا الكارت يغطي قطعًا معتمدة أخرى: %s. ألغِ اعتماد القطع التابعة أولًا.') % ', '.join(dependencies.piece_id.mapped('name'))
        releases = self.env['furniture.mrp.advance.material.release.stage'].sudo().search([
            ('production_id', 'in', orders.ids)])
        if releases.filtered(lambda r: r.state in ('issued', 'started') or any(m.issued_qty or m.received_qty for m in r.material_line_ids)):
            return _('تم صرف خامات لأحد الأوامر؛ يلزم مراجعة الصرف أولًا.')
        batch_members = self.env['furniture.mrp.stage.product.batch.member'].sudo().search([('production_id', 'in', orders.ids)])
        for batch in batch_members.batch_id:
            if batch.state not in ('waiting_store', 'cancelled'):
                return _('دفعة تشغيل مرتبطة تجاوزت انتظار المخزن؛ راجع تنفيذها قبل الإلغاء.')
            if batch.state != 'cancelled' and batch.member_ids.production_id - orders:
                return _('دفعة الصنف مشتركة مع اعتماد آخر؛ ألغِ طلب خامات الدفعة أولًا.')
        members = self.env['furniture.textile.kit.member'].sudo().search([('production_id', 'in', orders.ids)])
        if members.kit_id.filtered(lambda k: k.state != 'pending' or k.stage_timer_started_at):
            return _('بدأ تشغيل طقم مرتبط بالقطع؛ يلزم مراجعة الطقم قبل الإلغاء.')
        return False

    @api.model
    def _approved_stage_material_shortages(self, orders):
        """Return current, read-only raw-material shortages per open stage.

        Existing planning reservations keep their established priority. Orders
        that have not been checked yet share only the remaining physical stock,
        in planned-date/order order, so the same quantity is never advertised
        to several approved cards.
        """
        orders = orders.exists().sudo()
        if not orders:
            return {}
        Reservation = self.env['furniture.mrp.material.reservation'].sudo()
        active_reservations = Reservation.search([
            ('state', '=', 'active'),
            ('company_id', 'in', orders.company_id.ids),
            ('product_id', 'in', orders.material_line_ids.product_id.ids),
        ])
        reserved_by_order_product = {
            (reservation.production_id.id, reservation.product_id.id): reservation.reserved_qty
            for reservation in active_reservations
            if reservation.production_id in orders
        }
        protected_by_bucket = defaultdict(float)
        for reservation in active_reservations:
            protected_by_bucket[(
                reservation.company_id.id,
                reservation.source_location_id.id,
                reservation.product_id.id,
            )] += reservation.reserved_qty
        free_pool = {}
        shortage_quantities = defaultdict(lambda: defaultdict(float))
        stage_states_by_order = {}
        for order in orders:
            stage_states_by_order[order.id] = {
                code: ('cancelled' if order.state == 'cancelled' else
                       (state or ('done' if order.state == 'done' else 'pending')))
                for code, _stage_order, state in order._required_stage_infos()
            }
        for order in orders.sorted(lambda item: (str(item.date_planned_start or ''), item.id)):
            if order.state in ('done', 'cancelled'):
                continue
            stage_states = stage_states_by_order[order.id]
            open_codes = {
                code for code, state in stage_states.items()
                if state in ('pending', 'in_progress')
            }
            if not open_codes:
                continue
            source = order._material_reservation_source_location()
            if not source:
                continue
            stage_index = {
                code: index for index, code in enumerate(order._required_stage_codes())
            }
            first_open_code = next((
                code for code in order._required_stage_codes() if code in open_codes
            ), False)
            lines_by_product = defaultdict(list)
            for line in order.material_line_ids.filtered(
                lambda material: bool(material.product_id and material.qty_needed)
            ):
                code = line.stage or first_open_code
                if code:
                    lines_by_product[line.product_id.id].append((line, code))
            issued_by_product = order._material_reservation_issued_by_product(source)
            for product_id, line_rows in lines_by_product.items():
                product = self.env['product.product'].browse(product_id)
                issued_remaining = issued_by_product.get(product_id, 0.0)
                remaining_rows = []
                for line, code in sorted(
                    line_rows,
                    key=lambda row: (stage_index.get(row[1], 999), row[0].id),
                ):
                    needed = order._quantity_in_product_uom(
                        product, line.qty_needed, line.product_uom_id,
                    )
                    issued = min(max(issued_remaining, 0.0), needed)
                    issued_remaining -= issued
                    remaining = max(needed - issued, 0.0)
                    if code in open_codes and float_compare(
                        remaining, 0.0, precision_rounding=product.uom_id.rounding or 0.001,
                    ) > 0:
                        remaining_rows.append((code, remaining))
                if not remaining_rows:
                    continue
                reservation_key = (order.id, product_id)
                if reservation_key in reserved_by_order_product:
                    available = max(reserved_by_order_product[reservation_key], 0.0)
                else:
                    bucket = (order.company_id.id, source.id, product_id)
                    if bucket not in free_pool:
                        free_pool[bucket] = max(
                            Reservation._physical_available_qty(order.company_id, source, product)
                            - protected_by_bucket[bucket],
                            0.0,
                        )
                    available = free_pool[bucket]
                for code, required in remaining_rows:
                    covered = min(available, required)
                    available -= covered
                    shortage = max(required - covered, 0.0)
                    if float_compare(
                        shortage, 0.0, precision_rounding=product.uom_id.rounding or 0.001,
                    ) > 0:
                        shortage_quantities[(order.id, code)][product] += shortage
                if reservation_key not in reserved_by_order_product:
                    free_pool[bucket] = available
        return {
            key: [{
                'product': product.display_name,
                'quantity': round(quantity, 3),
                'uom': product.uom_id.name,
                'label': '%s: %s %s' % (
                    product.display_name,
                    ('%.3f' % quantity).rstrip('0').rstrip('.'),
                    product.uom_id.name,
                ),
            } for product, quantity in products.items()]
            for key, products in shortage_quantities.items()
        }

    @api.model
    def get_approved_tracking(self, scope='final', search='', status='all', page=0, model_id=None):
        self._check_manager()
        if scope not in ('final', 'stage') or status not in ('all', 'waiting', 'running', 'done', 'attention'):
            raise ValidationError(_('مرشح المتابعة غير صحيح.'))
        if type(page) is not int or page < 0 or not isinstance(search, str):
            raise ValidationError(_('بيانات التصفح غير صحيحة.'))
        if model_id is not None and (type(model_id) is not int or model_id < 0):
            raise ValidationError(_('الموديل المحدد غير صحيح.'))
        domain = [('company_id', 'in', self.env.companies.ids), ('state', 'in', ['approved', 'done']),
                  ('final_rule_id' if scope == 'final' else 'buffer_rule_id', '!=', False)]
        # Build complete families before filtering/paging so cancellation never
        # receives a truncated subset of an approved manufacturing batch.
        pieces = self.search(domain, order='approved_at desc, id desc')
        material_shortages = self._approved_stage_material_shortages(
            pieces.stage_ids.production_id,
        )
        remaining, cards = set(pieces.ids), []
        labels = dict(FURNITURE_STAGE_SELECTION)
        stage_states = {'pending': _('في الانتظار'), 'in_progress': _('جاري التنفيذ'),
                        'quality_check': _('فحص الجودة'), 'done': _('مكتمل')}
        order_owners = {}
        for piece in pieces:
            for order in piece.stage_ids.production_id:
                order_owners.setdefault(order.id, set()).add(piece.id)
        compatible = {}
        for first in pieces:
            if first.id not in remaining:
                continue
            ids, pending = {first.id}, [first.id]
            while pending:
                current = pieces.browse(pending.pop())
                for order in current.stage_ids.production_id:
                    extra = order_owners[order.id] - ids
                    ids.update(extra)
                    pending.extend(extra)
            remaining.difference_update(ids)
            group = self.browse(sorted(ids))
            signatures = {p._approval_group_key() for p in group}
            # Keep every existing shared-order family intact. Only ordinary,
            # identical recipes/routes may share a presentation/cancellation card.
            key = ('compatible', next(iter(signatures))) if len(signatures) == 1 and not any(group.mapped('is_custom')) else ('family', min(ids))
            compatible[key] = compatible.get(key, self.browse()) | group
        for group in compatible.values():
            ids = group.ids
            first = max(group, key=lambda p: (str(p.approved_at or ''), p.id))
            orders = group.stage_ids.production_id.sudo()
            steps, completed, running = [], 0, False
            shortage_stage_names = []
            for order in orders.sorted(lambda o: (o.need_to_produce_stage_id.sequence, o.id)):
                for code, stage_order, state in order._required_stage_infos():
                    effective = 'cancelled' if order.state == 'cancelled' else (state or ('done' if order.state == 'done' else 'pending'))
                    shortage_rows = material_shortages.get((order.id, code), [])
                    if shortage_rows and labels.get(code, code) not in shortage_stage_names:
                        shortage_stage_names.append(labels.get(code, code))
                    steps.append({'key': '%s:%s' % (order.id, code), 'code': code, 'label': labels.get(code, code),
                                  'state': effective, 'state_label': _('ملغي') if effective == 'cancelled' else stage_states.get(effective, effective),
                                  'production_id': order.id, 'production_name': order.name,
                                  'material_shortage': bool(shortage_rows),
                                  'material_shortages': shortage_rows,
                                  'material_shortage_summary': '، '.join(row['label'] for row in shortage_rows)})
                    completed += effective == 'done'
                    running |= effective in ('in_progress', 'quality_check')
            finished = bool(orders) and all(o.state == 'done' for o in orders)
            attention = bool(shortage_stage_names)
            card_status = 'attention' if attention else 'done' if finished else 'running' if running or completed or any(o.state == 'in_production' for o in orders) else 'waiting'
            if status != 'all' and status != card_status:
                continue
            search_text = ' '.join(group.mapped('name') + group.product_id.mapped('display_name') + group.furniture_model_id.mapped('name') + orders.mapped('name'))
            if search.strip() and search.strip().casefold() not in search_text.casefold():
                continue
            blocker = group._approval_cancel_blocker()
            cards.append({'id': min(ids), 'piece_ids': sorted(ids), 'approval_token': group._approval_token(), 'quantity': len(group),
                          'piece_names': ', '.join(group.mapped('name')),
                          'model': first.furniture_model_id.name, 'model_id': first.furniture_model_id.id or 0,
                          'product': first.product_id.display_name,
                          'target': dict(LANES).get(first.target_lane), 'company': first.company_id.name,
                          'approved_by': first.approved_by.name or '',
                          'approved_at': fields.Datetime.to_string(first.approved_at) if first.approved_at else '',
                          'approvals': [{'piece': p.name, 'approved_by': p.approved_by.name or '',
                                         'approved_at': fields.Datetime.to_string(p.approved_at) if p.approved_at else ''}
                                        for p in group.sorted('id')],
                          'custom': any(group.mapped('is_custom')), 'status': card_status,
                          'shortage_stage_names': '، '.join(shortage_stage_names),
                          'material_shortage_summary': ' | '.join(
                              '%s — %s' % (step['label'], step['material_shortage_summary'])
                              for step in steps if step['material_shortage']
                          ),
                          'progress': 100 if finished else min(99, round(100 * completed / len(steps))) if steps else 0,
                          'completed_steps': completed, 'total_steps': len(steps), 'steps': steps,
                          'order_count': len(orders), 'can_cancel': not blocker, 'cancel_reason': blocker or ''})
        return self._approved_tracking_page(cards, page, model_id)

    @api.model
    def _approved_tracking_page(self, cards, page=0, model_id=None):
        # Summarize all matching approvals before pagination. A model must not
        # disappear or show partial totals because its cards span several pages.
        models = {}
        for card in cards:
            model = models.setdefault(card['model_id'], {
                'id': card['model_id'], 'name': card['model'] or _('بدون موديل'),
                'count': 0, 'quantity': 0, 'completed_steps': 0, 'total_steps': 0,
                'waiting': 0, 'running': 0, 'done': 0, 'attention': 0,
            })
            model['count'] += 1
            model['quantity'] += card['quantity']
            model['completed_steps'] += card['completed_steps']
            model['total_steps'] += card['total_steps']
            model[card['status']] += 1
        for model in models.values():
            model['progress'] = (100 if model['done'] == model['count'] else
                                 min(99, round(100 * model['completed_steps'] / model['total_steps']))
                                 if model['total_steps'] else 0)
        if model_id is not None:
            cards = [card for card in cards if card['model_id'] == model_id]
        counts = {key: sum(card['status'] == key for card in cards)
                  for key in ('waiting', 'running', 'done', 'attention')}
        total = len(cards)
        page = min(page, max(0, (total - 1) // 24))
        return {'cards': cards[page * 24:(page + 1) * 24], 'total': total, 'page': page,
                'pages': max(1, (total + 23) // 24), 'models': list(models.values()), 'counts': counts}

    def action_cancel_approval(self, approval_token=None):
        """One transaction: validate entire family, cancel, unlink, return drafts.

        Never use a broad product/model search to delete manufacturing orders.
        Completed stock and source orders are outside this operation.
        """
        if not self:
            raise UserError(_('اختر كارتًا معتمدًا.'))
        self.check_access('read')
        self.check_access('write')
        for company in self.company_id.sorted('id'):
            self._check_manager(company)
            self._lock_company(company)
        with self.env.cr.savepoint():
            self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(self.ids))])
            self.invalidate_recordset()
            if len(self.exists()) != len(self):
                raise UserError(_('تغيرت القطع؛ حدّث القائمة.'))
            if all(p.state == 'draft' and p.approval_cancellation_history for p in self):
                return True  # Retry after a successful withdrawal.
            orders = self.stage_ids.production_id.sudo()
            KitMember = self.env['furniture.textile.kit.member'].sudo()
            kits = KitMember.search([('production_id', 'in', orders.ids)]).kit_id
            locked_orders = orders | kits.member_ids.production_id
            locked_releases = self.env['furniture.mrp.advance.material.release.stage'].sudo().search([
                ('production_id', 'in', locked_orders.ids)]).release_id
            releases = locked_releases.filtered(lambda r: bool(r.stage_line_ids.production_id & orders))
            for release in locked_releases.sorted('id'):
                release._lock()
            if orders:
                self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(locked_orders.ids))])
                locked_orders.invalidate_recordset()
            if kits:
                self.env.cr.execute('SELECT id FROM furniture_textile_kit WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(kits.ids))])
                kits.invalidate_recordset()
            if KitMember.search([('production_id', 'in', orders.ids)]).kit_id != kits:
                raise UserError(_('تغير تجميع الأطقم؛ حدّث القائمة قبل الإلغاء.'))
            if approval_token is not None and approval_token != self._approval_token():
                raise UserError(_('تغير الاعتماد منذ فتح الكارت؛ حدّث القائمة قبل الإلغاء.'))
            batch_members = self.env['furniture.mrp.stage.product.batch.member'].sudo().search([('production_id', 'in', orders.ids)])
            batches = batch_members.batch_id
            if batches:
                self.env.cr.execute('SELECT id FROM furniture_mrp_stage_product_batch WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(batches.ids))])
                batches.invalidate_recordset()
            blocker = self._approval_cancel_blocker()
            if blocker:
                raise UserError(blocker)
            draft_dependents = self.env['furniture.need.to.produce.input'].sudo().search([
                ('piece_id', 'not in', self.ids), ('piece_id.state', '=', 'draft'),
                '|', ('source_stage_id.production_id', 'in', orders.ids),
                ('source_line_id.production_id', 'in', orders.ids)]).piece_id
            snapshots = {p.id: {'at': fields.Datetime.to_string(fields.Datetime.now()), 'user_id': self.env.uid,
                               'approved_at': fields.Datetime.to_string(p.approved_at), 'approved_by': p.approved_by.id,
                               'warehouse_requests': releases.mapped('name'),
                               'product_batches': batches.ids,
                               'pending_kits': [{'id': k.id, 'token': k.token, 'members': k.member_ids.production_line_id.ids} for k in kits],
                               'orders': [{'id': o.id, 'name': o.name} for o in p.stage_ids.production_id]}
                         for p in self}
            for release in releases.filtered(lambda r: r.state == 'pending' and not (r.stage_line_ids.production_id - orders)):
                release.action_cancel()
            open_moves = self._approval_stock_moves().filtered(lambda m: m.state not in ('done', 'cancel'))
            for order in orders.sorted(lambda o: (o.need_to_produce_stage_id.sequence, o.id), reverse=True):
                if order.state != 'cancelled':
                    order.action_cancel()
            open_moves.filtered(lambda m: m.state != 'cancel')._action_cancel()
            # Only cancelled, never-consumed reservations may lose their audit
            # rows when their destination MO is explicitly being deleted.
            handoffs = self.env['furniture.mrp.lane.handoff'].sudo().search([
                ('downstream_production_id', 'in', orders.ids)])
            if any(h.state != 'cancelled' or h.transfer_move_id.state != 'cancel' for h in handoffs):
                raise UserError(_('تعذر تحرير تحويلات المراحل؛ لم يُلغَ الاعتماد.'))
            if handoffs:
                # Clear the two-way link only after both reservation records
                # are cancelled; retain the cancelled stock move as history.
                super(StockMoveLaneHandoff, handoffs.transfer_move_id).write({'furniture_lane_handoff_id': False, 'furniture_lane_handoff_role': False})
                super(FurnitureMrpLaneHandoff, handoffs).unlink()
            for piece in draft_dependents:
                piece._replace_preview(piece._build_preview())
            # An unstarted kit is a grouping of physical source lines, not a
            # stock movement. Dissolve it; remaining orders/lines stay intact
            # and the supervisor preview can regroup them on its next read.
            batches.filtered(lambda b: b.state == 'waiting_store').write({'state': 'cancelled'})
            batch_members.unlink()
            kits.member_ids.unlink()
            kits.unlink()
            release_stages = releases.stage_line_ids.filtered(lambda r: r.production_id in orders)
            release_stages.material_line_ids.unlink()
            release_stages.unlink()
            self.stage_ids.sudo().write({'production_id': False})
            orders.unlink()
            for piece in self:
                piece.sudo().write({'state': 'draft', 'approved_at': False, 'approved_by': False,
                                    'approval_cancellation_history': [*(piece.approval_cancellation_history or []), snapshots[piece.id]]})
            # Rebuild against current supply and retain custom material recipes.
            for piece in self.sorted('id'):
                piece._replace_preview(piece._build_preview())
        return True
