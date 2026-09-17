"""Persistent cross-order kits; source MOs stay intact for traceability."""
import copy
import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare

STAGES = [('tailoring', 'التفصيل'), ('upholstery', 'الكسوة')]
QTY_FIELDS = ('planned_qty', 'started_qty', 'working_qty', 'quality_qty',
              'completed_qty', 'not_started_qty', 'remaining_qty')
_EXECUTION_CAPABILITY = object()


def _piece_order(product):
    name = product.get('product_name', '').lower()
    if re.search(r'كنب|sofa|couch', name) and re.search(r'كبير|large|big|3.seater', name):
        return (0, name)
    return (2 if re.search(r'فوتي|armchair|fauteuil', name) else 1, name)


class TextileKit(models.Model):
    _name = 'furniture.textile.kit'
    _description = 'طقم تشغيل التفصيل والكسوة'
    _order = 'id'

    token = fields.Char(required=True, readonly=True, index=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict')
    stage_code = fields.Selection(STAGES, required=True, readonly=True, index=True)
    model_id = fields.Many2one('furniture.product.model', required=True, readonly=True, ondelete='restrict')
    recipe_id = fields.Many2one('mrp.bom', required=True, readonly=True, ondelete='restrict')
    buyer_id = fields.Many2one('res.partner', readonly=True, ondelete='restrict')
    beneficiary_id = fields.Many2one('res.partner', readonly=True, ondelete='restrict')
    member_ids = fields.One2many('furniture.textile.kit.member', 'kit_id', readonly=True)
    state = fields.Selection([('pending', 'لم يبدأ'), ('in_progress', 'قيد التشغيل'),
                              ('done', 'مكتمل')], default='pending', required=True, readonly=True)
    stage_timer_planned_qty = fields.Float(readonly=True)
    stage_timer_piece_hours = fields.Float(readonly=True)
    stage_timer_planned_hours = fields.Float(readonly=True)
    stage_timer_started_at = fields.Datetime(readonly=True)
    stage_timer_finished_at = fields.Datetime(readonly=True)
    stage_timer_paused_at = fields.Datetime(readonly=True)
    stage_timer_paused_work_hours = fields.Float(readonly=True, default=0)
    started_by_id = fields.Many2one('res.users', readonly=True)
    finished_by_id = fields.Many2one('res.users', readonly=True)

    _sql_constraints = [('token_unique', 'unique(token)', 'الطقم موجود بالفعل.')]

    def _stage_timer_temporary_fixed_hours(self):
        self.ensure_one()
        productions = self.member_ids.production_id
        return self.stage_timer_planned_hours if productions and all(
            production._furniture_temporary_stage_hours() for production in productions) else 0.0

    def _rows(self):
        self.ensure_one()
        lines = self.member_ids.production_line_id
        return [(production, lines.filtered(lambda line: line.production_id == production))
                for production in lines.production_id.sorted('id')]

    def _lock_validate(self, caller):
        self.ensure_one()
        Production = self.env['furniture.mrp.production'].with_user(caller)
        Production._stage_dashboard_check_stage_access(self.stage_code)
        if self.company_id not in Production.env.companies:
            raise AccessError(_('الطقم خارج الشركات المسموحة.'))
        productions = self.member_ids.production_id.with_user(caller)
        productions.check_access('read')
        # Same global lock order used by scoped product execution: releases,
        # then source MOs, then physical rows. Sibling kit operations serialize.
        Release = self.env['furniture.mrp.advance.material.release']
        releases = self.env['furniture.mrp.advance.material.release']
        for production in productions:
            releases |= Release.find_active_stage_release(production.sudo(), self.stage_code).release_id
        for release in releases.sorted('id'):
            release._lock()
        if productions:
            self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id IN %s ORDER BY id FOR UPDATE',
                                [tuple(sorted(productions.ids))])
        self.env.cr.execute('SELECT id FROM furniture_textile_kit WHERE id=%s FOR UPDATE', [self.id])
        lines = self.member_ids.production_line_id
        if lines:
            self.env.cr.execute('SELECT id FROM furniture_mrp_production_line WHERE id IN %s ORDER BY id FOR UPDATE',
                                [tuple(sorted(lines.ids))])
        self.invalidate_recordset()
        lines.invalidate_recordset()
        if not self.member_ids or len(lines) != len(self.member_ids):
            raise UserError(_('أعضاء الطقم غير مكتملين. راجع مدير الإنتاج.'))
        for member in self.member_ids:
            line = member.production_line_id
            if (not line.active or line.production_id.company_id != self.company_id
                    or line.production_id.state not in ('confirmed', 'in_production', 'done')
                    or self.stage_code not in line._selected_stage_codes()
                    or member.snapshot != self._line_snapshot(line)):
                raise UserError(_('بيانات قطعة داخل الطقم تغيرت؛ راجع مدير الإنتاج قبل التشغيل.'))
        return True

    @api.model
    def _line_snapshot(self, line):
        return [line.product_id.id, line.product_qty, line.bom_id.id,
                line.product_uom_id.id, line.furniture_order_model_id.id,
                line.buyer_partner_id.id, line.beneficiary_partner_id.id,
                line.width_cm, line.depth_cm, line.height_cm]

    def _operate(self, operation, caller, line_ids=None, decision=None):
        self._lock_validate(caller)
        if operation == 'materials':
            # Existing receipts belong to source orders, not fictitious kit
            # stock. Process one actionable request/modal per click.
            for production, lines in self._rows():
                public = production.with_user(caller)
                stage = production._stage_dashboard_stage_order(self.stage_code)
                if not stage or stage.store_request_state != 'approved':
                    return public.action_stage_dashboard_request_order_materials(self.stage_code)
            return False
        if operation == 'start':
            if self.state != 'pending':
                return False
            for production, lines in self._rows():
                # This existing endpoint checks receipt, arrival, exact source
                # lines and the real caller's stage permissions before sudo.
                production.with_user(caller).with_context(textile_kit_execution_capability=_EXECUTION_CAPABILITY).action_stage_dashboard_start_order_product(
                    lines.ids, self.stage_code)
                stage = production._stage_dashboard_stage_order(self.stage_code)
                if not stage or lines - stage._get_stage_line_ids_data('active_production_line_ids_data'):
                    raise UserError(_('لم يبدأ كل مكونات الطقم؛ راجع استلام التحويلات والخامات.'))
            self.write({'state': 'in_progress', 'stage_timer_started_at': fields.Datetime.now(),
                        'started_by_id': caller.id})
        elif operation in ('pause', 'resume'):
            if self.state != 'in_progress':
                raise UserError(_('ابدأ الطقم أولًا.'))
            now = fields.Datetime.now()
            if operation == 'pause' and not self.stage_timer_paused_at:
                self.write({'stage_timer_paused_at': now})
            elif operation == 'resume' and self.stage_timer_paused_at:
                self._close_pause(now)
        elif operation == 'quality':
            if self.state != 'in_progress' or decision not in ('pass', 'reject'):
                raise UserError(_('اختار قرار جودة صحيح لطقم قيد التشغيل.'))
            selected = self.member_ids.production_line_id.filtered(lambda line: line.id in (line_ids or []))
            if not selected or set(selected.ids) != set(line_ids or []):
                raise AccessError(_('القطع المختارة لا تخص هذا الطقم.'))
            for production, lines in self._rows():
                part = lines & selected
                if part:
                    production.with_user(caller).action_stage_dashboard_review_order_product_quality(
                        part.ids, self.stage_code, decision)
        elif operation == 'finish':
            if self.state == 'done':
                return False
            if self.state != 'in_progress':
                raise UserError(_('ابدأ الطقم قبل إنهائه.'))
            for production, lines in self._rows():
                stage = production._stage_dashboard_stage_order(self.stage_code)
                if not stage or stage.state != 'in_progress':
                    raise UserError(_('مرحلة أحد مكونات الطقم ليست قيد التشغيل.'))
                if lines - stage._manual_quality_active_lines():
                    raise UserError(_('تتبع مكونات الطقم غير مطابق للتشغيل الحالي.'))
                stage._require_manual_quality(lines)
            for production, lines in self._rows():
                stage = production._stage_dashboard_stage_order(self.stage_code).with_context(
                    furniture_skip_line_consolidation=True)
                completed = stage._get_stage_line_ids_data('completed_production_line_ids_data')
                remaining = production.production_line_ids.filtered(
                    lambda line: line.active and self.stage_code in line._selected_stage_codes()) - completed - lines
                close_steps = getattr(stage, '_complete_internal_substages_for_dashboard_finish', None)
                if close_steps and not remaining:
                    close_steps()
                stage._send_selected_lines_to_quality(lines)
                stage._approve_product_batch_lines(lines)
            now = fields.Datetime.now()
            self._close_pause(now)
            self.write({'state': 'done', 'stage_timer_finished_at': now, 'finished_by_id': caller.id})
        else:
            raise AccessError(_('عملية الطقم غير مسموحة.'))
        return False

    def _close_pause(self, now):
        if self.stage_timer_paused_at:
            elapsed = self.env['furniture.mrp.stage.time.standard']._stage_timer_elapsed_hours(
                self, self.company_id, self.stage_timer_paused_at, now)
            self.write({'stage_timer_paused_work_hours': self.stage_timer_paused_work_hours + elapsed,
                        'stage_timer_paused_at': False})


class TextileKitMember(models.Model):
    _name = 'furniture.textile.kit.member'
    _description = 'مكون طقم تشغيلي مرتبط بأمره الأصلي'
    kit_id = fields.Many2one('furniture.textile.kit', required=True, ondelete='restrict', index=True)
    production_line_id = fields.Many2one('furniture.mrp.production.line', required=True, ondelete='restrict', index=True)
    production_id = fields.Many2one(related='production_line_id.production_id', store=True, index=True)
    company_id = fields.Many2one(related='kit_id.company_id', store=True)
    stage_code = fields.Selection(related='kit_id.stage_code', store=True, index=True)
    snapshot = fields.Json(required=True)
    _sql_constraints = [('line_stage_unique', 'unique(production_line_id,stage_code)',
                         'القطعة مخصصة لطقم آخر في نفس المرحلة.')]


class TextileKitProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    def action_stage_dashboard_start_order_product(self, production_line_ids, stage_code):
        reserved = self.env['furniture.textile.kit.member'].sudo().search_count([
            ('production_line_id', 'in', production_line_ids), ('stage_code', '=', stage_code)])
        if reserved and self.env.context.get('textile_kit_execution_capability') is not _EXECUTION_CAPABILITY:
            raise UserError(_('ابدأ القطع المجمعة من كارت الطقم، وليس من الأمر الأصلي.'))
        return super().action_stage_dashboard_start_order_product(production_line_ids, stage_code)

    def action_stage_dashboard_finish_order_stage(self, stage_code):
        if self.env['furniture.textile.kit.member'].sudo().search_count([
            ('production_id', 'in', self.ids), ('stage_code', '=', stage_code),
            ('kit_id.state', '=', 'in_progress')]):
            raise UserError(_('أنه كل طقم من كارته لحفظ استقلال باقي الأطقم.'))
        return super().action_stage_dashboard_finish_order_stage(stage_code)

    @api.model
    def _textile_kit_card_payload(self, definition, raw_orders, kit=None):
        """Adapt a real kit/preview to the established compact card template."""
        by_order = {order['id']: order for order in raw_orders}
        allocations = ([{'production_id': member.production_id.id,
                         'production_line_id': member.production_line_id.id,
                         'quantity': member.production_line_id.product_qty}
                        for member in kit.member_ids] if kit else
                       [member for product in definition['products'] for member in product['source_allocations']])
        originals = [by_order[order_id] for order_id in dict.fromkeys(
            member['production_id'] for member in allocations) if order_id in by_order]
        if len(originals) != len({member['production_id'] for member in allocations}):
            return False
        card = copy.deepcopy(originals[0])
        products = {}
        for member in allocations:
            line = self.env['furniture.mrp.production.line'].browse(member['production_line_id'])
            original = by_order[member['production_id']]
            source = next((product for product in original['product_lines']
                           if line.id in product['production_line_ids']), None)
            if not source:
                return False
            key = (source['product_id'], source.get('bom_id'), source.get('dimension_label'))
            if key not in products:
                products[key] = copy.deepcopy(source)
                products[key].update({field: 0.0 for field in QTY_FIELDS})
                products[key].update({'production_line_ids': [], 'quality_production_line_ids': [],
                                      'startable_production_line_ids': [], 'can_start_stage': False,
                                      '_textile_source_order_id': member['production_id']})
                products[key].update({'fabric_items': [], 'takawe_items': []})
            product = products[key]
            amount = member['quantity']
            for item_field, summary_field in (('fabric_items', 'fabric_summary'), ('takawe_items', 'takawe_summary')):
                items = copy.deepcopy(source.get(item_field, []))
                ratio = amount / source['planned_qty'] if source['planned_qty'] else 0
                for item in items:
                    item['qty'] = round(item.get('qty', 0) * ratio, 3)
                product[item_field], product[summary_field] = self._stage_dashboard_merge_tailoring_material_items(
                    line.production_id, product[item_field], items)
            stage = line.production_id._stage_dashboard_stage_order(definition['stage_code'])
            completed = bool(stage and line in stage._get_stage_line_ids_data('completed_production_line_ids_data'))
            working = bool(stage and line in stage._manual_quality_active_lines())
            product['production_line_ids'].append(line.id)
            if working:
                product['quality_production_line_ids'].append(line.id)
            product['planned_qty'] += amount
            product['started_qty'] += amount if working or completed else 0
            product['working_qty'] += amount if working else 0
            product['completed_qty'] += amount if completed else 0
            product['not_started_qty'] += amount if not working and not completed else 0
            product['remaining_qty'] += amount if not completed else 0
            product['can_review_quality'] = bool(working and definition['stage_code'] == 'tailoring')
        for product in products.values():
            reviews = []
            for line_id in product['quality_production_line_ids']:
                line = self.env['furniture.mrp.production.line'].browse(line_id)
                reviews.append(line.production_id._stage_dashboard_stage_order(definition['stage_code'])._manual_quality_state(line))
            product['manual_quality_state'] = 'reject' if 'reject' in reviews else 'pass' if reviews and all(value == 'pass' for value in reviews) else 'pending'
        model = self.env['furniture.product.model'].browse(definition['model_id'])
        buyer = self.env['res.partner'].browse(definition.get('buyer_id') or [])
        beneficiary = self.env['res.partner'].browse(definition.get('beneficiary_id') or [])
        state = kit.state if kit else 'pending'
        all_startable = all(any(member['production_line_id'] in product.get('startable_production_line_ids', [])
                                for product in by_order[member['production_id']]['product_lines']) for member in allocations)
        card.update({
            'id': 2000000000000 + kit.id if kit else 3000000000000 + int(definition['token'][4:14], 16),
            'textile_kit': True, 'textile_kit_token': definition['token'],
            '_textile_source_order_ids': [row['id'] for row in originals],
            'name': _('طقم %s') % model.display_name,
            'model_id': model.id, 'model_name': model.display_name,
            'buyer_summary': buyer.display_name if buyer else False,
            'beneficiary_summary': beneficiary.display_name if beneficiary else False,
            'state': state, 'stage_order_name': False, 'stage_order_id': False,
            'product_lines': sorted(products.values(), key=_piece_order), 'product_count': len(products),
            'can_start_stage': state == 'pending' and all_startable,
            'can_finish_stage': state == 'in_progress',
            'can_request_materials': state == 'pending' and any(row.get('can_request_materials') for row in originals),
            'can_receive_materials': state == 'pending' and any(row.get('can_receive_materials') for row in originals),
            'can_open_material_request': False,
            'can_start_batch': False, 'can_finish_batch': False,
            'kit_planned_hours': kit.stage_timer_planned_hours if kit else definition['planned_hours'],
            'timer': self.env['furniture.mrp.stage.time.standard']._stage_timer_payload(kit, state) if kit else False,
            'quality_ready': definition['stage_code'] == 'upholstery' or all(
                row['manual_quality_state'] == 'pass' for row in products.values()),
        })
        for field in QTY_FIELDS:
            card[field] = sum(row[field] for row in products.values())
        card['progress'] = 100 * card['completed_qty'] / card['planned_qty'] if card['planned_qty'] else 0
        return card

    @api.model
    def get_stage_dashboard_data(self, stage_code=False, date_from=False, date_to=False):
        result = super().get_stage_dashboard_data(stage_code, date_from, date_to)
        stage_code = result['selected_stage']
        if (stage_code not in dict(STAGES) or self.env.context.get('textile_kit_raw_dashboard')
                or self.env['ir.config_parameter'].sudo().get_param('furniture_need_to_produce.textile_kits_enabled') != 'True'):
            return result
        raw = result['orders']
        visible_ids = {row['id'] for row in raw}
        kits = self.env['furniture.textile.kit'].sudo().search([
            ('stage_code', '=', stage_code), ('company_id', 'in', self.env.companies.ids),
            ('member_ids.production_id', 'in', list(visible_ids))])
        planned = self._textile_kit_preview(stage_code, date_from, date_to, raw_payload=result)
        cards = []
        used = defaultdict(float)
        for kit in kits:
            definition = {'stage_code': stage_code, 'token': kit.token, 'model_id': kit.model_id.id,
                          'buyer_id': kit.buyer_id.id, 'beneficiary_id': kit.beneficiary_id.id}
            card = self._textile_kit_card_payload(definition, raw, kit)
            if card:
                cards.append(card)
                for member in kit.member_ids:
                    used[member.production_line_id.id] += member.production_line_id.product_qty
        for definition in planned['cards']:
            card = self._textile_kit_card_payload(definition, raw)
            if card:
                cards.append(card)
                for product in definition['products']:
                    for member in product['source_allocations']:
                        used[member['production_line_id']] += member['quantity']
        # Keep every incomplete/custom/legacy-running remainder, not a
        # duplicate copy of the original full order underneath the kit cards.
        leftovers = []
        for original in raw:
            order = copy.deepcopy(original)
            remaining_products = []
            for product in order['product_lines']:
                taken = sum(used[line_id] for line_id in product['production_line_ids'])
                if not taken:
                    remaining_products.append(product)
                    continue
                remaining = max(product['planned_qty'] - taken, 0)
                if remaining > 0.000001:
                    product['planned_qty'] = remaining
                    product['not_started_qty'] = max(product['not_started_qty'] - taken, 0)
                    product['remaining_qty'] = max(product['remaining_qty'] - taken, 0)
                    # A partial unmaterialized preview shares a technical row;
                    # never let a remainder button start its full source row.
                    original_ids = product['production_line_ids']
                    rows = self.env['furniture.mrp.production.line'].browse(original_ids)
                    partial = any(0 < used[line.id] < line.product_qty - 0.000001 for line in rows)
                    remaining_ids = rows.filtered(lambda line: used[line.id] < line.product_qty - 0.000001).ids
                    for id_field in ('production_line_ids', 'startable_production_line_ids', 'quality_production_line_ids'):
                        product[id_field] = [line_id for line_id in product.get(id_field, []) if line_id in remaining_ids]
                    product['can_start_stage'] = bool(product['startable_production_line_ids']) and not partial
                    product['_textile_partial_preview'] = partial
                    if not partial:
                        production = rows[:1].production_id
                        stage = production._stage_dashboard_stage_order(stage_code)
                        buckets = self._stage_dashboard_line_buckets(production, stage_code, stage)
                        remainder_rows = rows.filtered(lambda line: line.id in remaining_ids)
                        for field, bucket in (('started_qty', 'started_lines'), ('working_qty', 'working_lines'),
                                              ('quality_qty', 'quality_lines'), ('completed_qty', 'completed_lines'),
                                              ('not_started_qty', 'not_started_lines')):
                            product[field] = sum((remainder_rows & buckets[bucket]).mapped('product_qty'))
                        product['remaining_qty'] = product['not_started_qty']
                    remaining_products.append(product)
            if remaining_products:
                order['product_lines'] = remaining_products
                if any(used[line_id] for product in original['product_lines'] for line_id in product['production_line_ids']):
                    order['can_start_stage'] = False
                    order['can_finish_stage'] = False
                    for field in QTY_FIELDS:
                        order[field] = sum(product[field] for product in remaining_products)
                    order['can_start_stage'] = any(product.get('can_start_stage') for product in remaining_products)
                    order['can_finish_stage'] = any(product.get('quality_production_line_ids') for product in remaining_products)
                order['textile_loose'] = True
                leftovers.append(order)
        result['orders'] = cards + leftovers
        result['textile_kit_count'] = len(cards)
        return result

    def action_textile_loose_operation(self, stage_code, operation, line_ids):
        """Operate only the unallocated remainder of an original order."""
        self.ensure_one()
        if stage_code not in dict(STAGES) or operation not in ('start', 'finish'):
            raise AccessError(_('عملية غير مسموحة.'))
        self._stage_dashboard_validate_order_action(stage_code)
        lines = self.production_line_ids.filtered(lambda line: line.active and line.id in line_ids)
        if not lines or set(lines.ids) != set(line_ids):
            raise AccessError(_('القطع لا تخص الأمر.'))
        if self.env['furniture.textile.kit.member'].sudo().search_count([
                ('production_line_id', 'in', lines.ids), ('stage_code', '=', stage_code)]):
            raise UserError(_('شغّل القطع المخصصة للأطقم من كارت الطقم.'))
        if operation == 'start':
            return self.action_stage_dashboard_start_order_product(lines.ids, stage_code)
        stage = self.sudo()._stage_dashboard_stage_order(stage_code)
        if not stage or lines.sudo() - stage._manual_quality_active_lines():
            raise UserError(_('القطع ليست قيد التشغيل.'))
        stage._require_manual_quality(lines.sudo())
        remaining = self.sudo().production_line_ids.filtered(
            lambda line: line.active and stage_code in line._selected_stage_codes()) - lines.sudo() - stage._get_stage_line_ids_data('completed_production_line_ids_data')
        close_steps = getattr(stage, '_complete_internal_substages_for_dashboard_finish', None)
        if close_steps and not remaining:
            close_steps()
        stage._send_selected_lines_to_quality(lines.sudo())
        stage.with_context(furniture_skip_line_consolidation=True)._approve_product_batch_lines(lines.sudo())
        return False

    @api.model
    def _materialize_textile_kit(self, token, stage_code, date_from=False, date_to=False):
        self._stage_dashboard_check_stage_access(stage_code)
        Kit = self.env['furniture.textile.kit'].sudo()
        existing = Kit.search([('token', '=', token), ('stage_code', '=', stage_code)], limit=1)
        if existing:
            existing._lock_validate(self.env.user)
            return existing
        plan = self._textile_kit_preview(stage_code, date_from, date_to)
        candidate = next((card for card in plan['cards'] if card['token'] == token), None)
        if not candidate:
            raise UserError(_('تقسيمة الأطقم تغيرت. حدّث الصفحة وحاول مجددًا.'))
        allocations = [member for product in candidate['products'] for member in product['source_allocations']]
        ids = sorted({member['production_id'] for member in allocations})
        productions = self.search([('id', 'in', ids)])
        if set(productions.ids) != set(ids):
            raise AccessError(_('أحد أوامر الطقم غير مسموح لحسابك.'))
        releases = self.env['furniture.mrp.advance.material.release'].sudo()
        for production in productions:
            releases |= releases.find_active_stage_release(production.sudo(), stage_code).release_id
        for release in releases.sorted('id'):
            release._lock()
        self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(ids)])
        self.env.invalidate_all()
        existing = Kit.search([('token', '=', token), ('stage_code', '=', stage_code)], limit=1)
        if existing:
            return existing
        plan = self._textile_kit_preview(stage_code, date_from, date_to)
        fresh = next((card for card in plan['cards'] if card['token'] == token), None)
        if fresh != candidate:
            raise UserError(_('تغيرت قطع الطقم أثناء التجهيز. حدّث الصفحة.'))
        Line = self.env['furniture.mrp.production.line'].sudo()
        members = []
        for allocation in allocations:
            line = Line.browse(allocation['production_line_id'])
            qty = allocation['quantity']
            if line.product_qty > qty + 0.000001 and line.downstream_handoff_ids.filtered(lambda handoff: handoff.state != 'cancelled'):
                raise UserError(_('هذه الدفعة لها تحويلات مخصصة بالفعل؛ لا يمكن تقسيمها إلى أطقم قبل مراجعة تخصيص التحويل.'))
            completed = line.production_id._production_line_completed_stage_codes(line)
            if float_compare(line.product_qty, qty, precision_digits=6) < 0:
                raise UserError(_('كمية أحد المكونات لم تعد كافية.'))
            remainder = line.with_context(furniture_skip_line_consolidation=True)._split_for_partial_quantity(
                qty, preserve_progress=True)
            if remainder:
                line.production_id._copy_completed_stage_progress_to_split_line(line, remainder, completed)
            members.append((0, 0, {'production_line_id': line.id, 'snapshot': Kit._line_snapshot(line)}))
        for production in productions.sudo():
            production.with_context(furniture_skip_line_consolidation=True)._refresh_material_lines_for_stage_plan()
        for release in releases:
            for stage in release.stage_line_ids.filtered(lambda stage: stage.production_id in productions):
                stage._validate_current_snapshot(relink_sources=True)
        qty = sum(member['quantity'] for member in allocations)
        return Kit.create({
            'token': token, 'stage_code': stage_code, 'company_id': candidate['company_id'],
            'model_id': candidate['model_id'], 'recipe_id': candidate['recipe_id'],
            'buyer_id': candidate['buyer_id'], 'beneficiary_id': candidate['beneficiary_id'],
            'member_ids': members, 'stage_timer_planned_qty': qty,
            'stage_timer_planned_hours': candidate['planned_hours'],
            'stage_timer_piece_hours': candidate['planned_hours'] / qty if qty else 0,
        })

    @api.model
    def action_textile_kit_operation(self, token, stage_code, operation, date_from=False,
                                     date_to=False, line_ids=None, decision=None):
        if stage_code not in dict(STAGES) or operation not in ('materials', 'start', 'pause', 'resume', 'finish', 'quality'):
            raise AccessError(_('عملية الطقم غير مسموحة.'))
        self._stage_dashboard_check_stage_access(stage_code)
        kit = self.env['furniture.textile.kit'].sudo().search([
            ('token', '=', token), ('stage_code', '=', stage_code)], limit=1)
        if not kit:
            if operation not in ('materials', 'start'):
                raise UserError(_('الطقم لم يبدأ بعد.'))
            kit = self._materialize_textile_kit(token, stage_code, date_from, date_to)
        return kit._operate(operation, self.env.user, line_ids, decision)

    def _get_equivalent_production_line_groups(self):
        groups = super()._get_equivalent_production_line_groups()
        protected = self.env['furniture.textile.kit.member'].sudo().search([
            ('production_id', 'in', self.ids)]).production_line_id
        return [group for group in groups if not group & protected]


class TextileKitLineGuard(models.Model):
    _inherit = 'furniture.mrp.production.line'

    def write(self, vals):
        protected = {'product_id', 'product_qty', 'bom_id', 'production_id', 'active',
                     'furniture_order_model_id', 'buyer_partner_id', 'beneficiary_partner_id',
                     'width_cm', 'depth_cm', 'height_cm', 'consolidated_into_line_id'}
        changed = self.filtered(lambda line: any(
            (line[field].id if self._fields[field].type == 'many2one' else line[field]) != value
            for field, value in vals.items() if field in protected))
        if changed and self.env['furniture.textile.kit.member'].sudo().search_count([
            ('production_line_id', 'in', changed.ids)]):
            raise UserError(_('القطع موزعة على أطقم تشغيل؛ لا يمكن تغيير كمياتها أو دمجها مباشرة.'))
        return super().write(vals)
