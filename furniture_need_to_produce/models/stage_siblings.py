"""Explicit kit companion replenishment, bounded by the department maximum."""
import math
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

from .production_plan import LANES, STAGE_LANE


class StageSiblingReplenishment(models.Model):
    _inherit = 'furniture.need.to.produce'

    sibling_source_rule_id = fields.Many2one(
        'furniture.mrp.stage.replenishment.rule', readonly=True, ondelete='restrict',
        string='استكمال طقم بسبب نقص المرحلة', copy=False,
    )

    def _sibling_is_armchair(self, product):
        product = product.furniture_dimension_source_product_id or product
        name = re.sub(r'[\u064b-\u065f\u0640\s]+', '',
                      (product.with_context(lang=False).name or '').casefold())
        return any(word in name for word in ('فوتي', 'فوتى', 'armchair', 'fauteuil'))

    def _sibling_balance(self, rule):
        probe = self.new({
            'company_id': rule.company_id.id, 'product_id': rule.product_id.id,
            'furniture_model_id': rule.furniture_model_id.id, 'bom_id': rule.bom_id.id,
            'target_lane': STAGE_LANE[rule.stage_code],
        })
        lane = probe.target_lane
        role = 'finish' if lane == 'preparation' or (
            lane == 'bases' and self._parallel_finish_enabled()) else lane
        pool = probe._supply_pools(ignore_pieces=self.browse()).get(role, [])
        stock = sum(row['quantity'] for row in pool if row['kind'] == 'stock')
        incoming = sum(row['quantity'] for row in pool if row['kind'] == 'incoming')
        return {'stock': stock, 'incoming': incoming, 'total': stock + incoming}

    def _sibling_rules(self, source):
        """An actual kit recipe and the same company/model/department are required."""
        Rule = self.env['furniture.mrp.stage.replenishment.rule']
        if (not source.active or source.max_qty <= 0 or source.stage_code not in STAGE_LANE
                or self._sibling_is_armchair(source.product_id)):
            return Rule.browse()
        kits = self.env['mrp.bom'].search([
            ('type', '=', 'phantom'), ('furniture_model_id', '=', source.furniture_model_id.id),
            ('company_id', 'in', [False, source.company_id.id]),
        ])
        products = self.env['product.product'].browse()
        for kit in kits:
            components = kit.bom_line_ids.filtered(lambda line: line.product_qty > 0).product_id
            components = components.mapped(lambda product: product.furniture_dimension_source_product_id or product)
            if source.product_id in components:
                products |= components
        products = (products - source.product_id).filtered(lambda product: not self._sibling_is_armchair(product))
        return Rule.search([
            ('company_id', '=', source.company_id.id),
            ('furniture_model_id', '=', source.furniture_model_id.id),
            ('stage_code', '=', source.stage_code), ('product_id', 'in', products.ids),
            ('active', '=', True), ('max_qty', '>', 0),
        ]) if products else Rule.browse()

    def stage_sibling_balances(self):
        self.check_access('read')
        result = {}
        for source in self.filtered(lambda piece: piece.state == 'draft' and not piece.final_rule_id).buffer_rule_id:
            self._check_manager(source.company_id)
            siblings = self._sibling_rules(source)
            if not siblings or self._sibling_balance(source)['total'] > source.min_qty + 0.000001:
                continue
            anchor = self.filtered(lambda piece: piece.buffer_rule_id == source and piece.state == 'draft')[:1]
            result[source.id] = [{
                'source_piece_id': anchor.id, 'rule_id': rule.id,
                'name': rule.product_id.display_name,
                'stage': dict(LANES)[STAGE_LANE[rule.stage_code]],
                'max': rule.max_qty, **(balance := self._sibling_balance(rule)),
                'missing': max(rule.max_qty - balance['total'], 0),
            } for rule in siblings]
        return result

    def _check_still_needed(self):
        self.ensure_one()
        if not self.sibling_source_rule_id:
            return super()._check_still_needed()
        source = self.sibling_source_rule_id
        if (self.final_rule_id or self.buffer_rule_id not in self._sibling_rules(source)
                or self._sibling_balance(source)['total'] > source.min_qty + 0.000001):
            raise UserError(_('حالة نقص الطقم تغيرت؛ حدّث نواقص المراحل.'))
        needed = self.buffer_rule_id.max_qty - self._sibling_balance(self.buffer_rule_id)['total']
        if needed < 1 - 0.000001:
            raise UserError(_('رصيد الصنف أصبح يغطي الماكس؛ لا يحتاج أوامر جديدة.'))
        return needed

    def _sibling_lock_rules(self, rules):
        # Odoo uses repeatable-read transactions. An advisory lock alone does
        # not refresh a waiting request's snapshot. Touch the shared policy row
        # so concurrent approvals retry on serialization failure, with fresh
        # stock/orders, while preserving the configuration's write date.
        if rules:
            self.env.cr.execute(
                'UPDATE furniture_mrp_stage_replenishment_rule SET write_date = write_date '
                'WHERE id IN %s', [tuple(rules.sorted('id').ids)])

    def action_approve(self):
        for company in self.company_id.sorted('id'):
            self._check_manager(company)
            self._lock_company(company)
        self._sibling_lock_rules(self.buffer_rule_id | self.sibling_source_rule_id)
        return super().action_approve()

    def action_complete_stage_sibling(self, rule_id):
        self.ensure_one()
        self.check_access('write')
        self._check_manager(self.company_id)
        self._lock_company(self.company_id)
        self.invalidate_recordset()
        source = self.buffer_rule_id
        if self.state != 'draft' or self.final_rule_id or not source:
            raise UserError(_('الاستكمال متاح من كارت نقص مرحلة بانتظار الاعتماد فقط.'))
        source.invalidate_recordset()
        siblings = self._sibling_rules(source)
        rule = siblings.filtered(lambda row: type(rule_id) is int and row.id == rule_id)
        if not rule or self._sibling_balance(source)['total'] > source.min_qty + 0.000001:
            raise UserError(_('الصنف ليس رفيقًا لهذا النقص في نفس الطقم والمرحلة، أو تغير رصيد المرحلة.'))
        self._sibling_lock_rules(source | rule)
        rule.invalidate_recordset()
        wanted = max(rule.max_qty - self._sibling_balance(rule)['total'], 0)
        if wanted < 0.000001:
            return {'quantity': 0}
        if not math.isfinite(wanted) or wanted > 500 or abs(wanted - round(wanted)) > 0.00001:
            raise UserError(_('الاستكمال بالقطعة: الكمية المطلوبة يجب أن تكون عددًا صحيحًا لا يتجاوز ٥٠٠.'))
        wanted = int(round(wanted))
        # Keep existing custom previews intact. Normal approval will reject a
        # stale route instead of silently discarding edits or stock claims.
        plans = self.sudo().search([
            ('buffer_rule_id', '=', rule.id), ('state', '=', 'draft'),
        ], order='id', limit=wanted)
        for index in range(wanted - len(plans)):
            piece = self.sudo().create({
                'name': _('استكمال طقم'), 'company_id': rule.company_id.id,
                'product_id': rule.product_id.id, 'furniture_model_id': rule.furniture_model_id.id,
                'bom_id': rule.bom_id.id, 'target_lane': STAGE_LANE[rule.stage_code],
                'buffer_rule_id': rule.id,
            })
            piece._replace_preview(piece._build_preview())
            plans |= piece
        plans.write({'sibling_source_rule_id': source.id})
        plans.with_user(self.env.user).action_approve()
        return {'quantity': wanted}
