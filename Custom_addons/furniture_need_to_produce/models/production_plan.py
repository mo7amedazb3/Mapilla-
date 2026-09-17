"""Auditable per-piece allocation with compatible bulk manufacturing orders."""
import json
import math
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare
from odoo.addons.furniture_mrp.models.mrp_production_order import FURNITURE_STAGE_SELECTION
from ..services.piece_planner import plan_piece

LANES = [('frame', 'النجارة'), ('bases', 'القواعد'), ('preparation', 'التجهيز'), ('finish', 'القواعد والتجهيز'),
         ('tailoring', 'التفصيل'), ('painting', 'الدهانات'), ('upholstery', 'الكسوة'), ('packaging', 'التغليف')]
ROLE_LANE = {'frame': 'frame', 'bases': 'bases', 'finish': 'preparation',
             'tailoring': 'tailoring', 'painting': 'painting', 'upholstery': 'upholstery'}
STAGE_LANE = {'carpentry': 'frame', 'bases': 'bases', 'finishing': 'preparation',
              'tailoring': 'tailoring', 'painting': 'painting'}


class NeedToProduce(models.Model):
    _name = 'furniture.need.to.produce'
    _description = 'Need to Produce — one piece'
    _order = 'furniture_model_id, product_id, id'

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, index=True)
    furniture_model_id = fields.Many2one('furniture.product.model', required=True, readonly=True, index=True)
    bom_id = fields.Many2one('mrp.bom', required=True, readonly=True)
    final_rule_id = fields.Many2one('furniture.mrp.final.replenishment.rule', readonly=True, ondelete='restrict')
    buffer_rule_id = fields.Many2one('furniture.mrp.stage.replenishment.rule', readonly=True, ondelete='restrict')
    target_lane = fields.Selection(LANES, default='packaging', required=True, readonly=True)
    state = fields.Selection([('draft', 'بانتظار الاعتماد'), ('approved', 'معتمد — مخطط للإنتاج'),
                              ('done', 'مكتمل'), ('cancelled', 'ملغي')], default='draft', readonly=True, index=True)
    stage_ids = fields.One2many('furniture.need.to.produce.stage', 'piece_id', readonly=True)
    route_text = fields.Text(readonly=True, string='المسار المطلوب')
    total_work_hours = fields.Float(readonly=True, string='إجمالي ساعات العمل')
    critical_path_hours = fields.Float(readonly=True, string='ساعات المسار المتوقعة')
    critical_path_days = fields.Float(readonly=True, string='أيام عمل (١٠ ساعات)')
    unknown_incoming_wait = fields.Boolean(readonly=True, string='توجد أوامر قائمة بموعد انتهاء غير محدد')
    preview_json = fields.Json(readonly=True)
    approved_by = fields.Many2one('res.users', readonly=True)
    approved_at = fields.Datetime(readonly=True)
    production_count = fields.Integer(compute='_compute_production_count')
    is_custom = fields.Boolean(compute='_compute_custom_approval', string='Custom')
    custom_bom_modified = fields.Boolean(compute='_compute_custom_approval')
    custom_partners = fields.Json(compute='_compute_custom_partners', string='المشتري والمستهلك')

    @api.depends('preview_json', 'is_custom', 'company_id')
    def _compute_custom_partners(self):
        for piece in self:
            payload = {'company_id': piece.company_id.id}
            metadata = (piece.preview_json or {}).get('custom_approval', {}) if piece.is_custom else {}
            for field in ('buyer_partner_id', 'beneficiary_partner_id'):
                partner_id = metadata.get(field)
                partner = self.env['res.partner'].browse(
                    partner_id if type(partner_id) is int and partner_id > 0 else []
                ).exists()
                payload[field] = [partner.id, partner.display_name] if partner else False
            piece.custom_partners = payload

    def _validate_custom_partner(self, partner_id, buyer=False):
        self.ensure_one()
        if partner_id is False or partner_id is None:
            return False
        if type(partner_id) is not int or partner_id <= 0:
            raise ValidationError(_('اختار العميل من القائمة.'))
        partner = self.env['res.partner'].browse(partner_id).exists()
        if not partner:
            raise ValidationError(_('العميل المختار لم يعد موجودًا.'))
        partner.check_access('read')
        if not partner.active:
            raise ValidationError(_('العميل المختار مؤرشف؛ اختار عميلًا نشطًا.'))
        if partner.company_id and partner.company_id != self.company_id:
            raise AccessError(_('العميل يجب أن يكون متاحًا لشركة هذه القطعة.'))
        if buyer and not partner.is_company:
            raise ValidationError(_('المشتري يجب أن يكون شركة، مثل أوامر الإنتاج.'))
        return partner.id

    def _custom_production_partner_values(self):
        self.ensure_one()
        metadata = (self.preview_json or {}).get('custom_approval', {}) if self.is_custom else {}
        return {field: self._validate_custom_partner(metadata.get(field), buyer=field == 'buyer_partner_id')
                for field in ('buyer_partner_id', 'beneficiary_partner_id')}

    def action_save_custom_partners(self, buyer_partner_id=False, beneficiary_partner_id=False):
        """Only customer metadata is writable here; never route/BoM/state data."""
        self.ensure_one()
        self._check_manager(self.company_id)
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        self.stage_ids.invalidate_recordset(['materials_customized', 'material_ids'])
        self.stage_ids.material_ids.invalidate_recordset(['customized'])
        if self.state != 'draft' or not self.is_custom:
            raise UserError(_('اختيار المشتري والمستهلك متاح للقطعة Custom قبل الاعتماد فقط.'))
        values = {
            'buyer_partner_id': self._validate_custom_partner(buyer_partner_id, buyer=True),
            'beneficiary_partner_id': self._validate_custom_partner(beneficiary_partner_id),
        }
        preview = dict(self.preview_json or {})
        preview['custom_approval'] = {**preview.get('custom_approval', {}), **values}
        self.sudo().write({'preview_json': preview})
        return True

    @api.depends('preview_json', 'stage_ids.materials_customized', 'stage_ids.material_ids.customized')
    def _compute_custom_approval(self):
        for piece in self:
            metadata = (piece.preview_json or {}).get('custom_approval', {})
            piece.custom_bom_modified = bool(metadata.get('bom_modified') or
                any(piece.stage_ids.mapped('materials_customized')) or
                any(piece.stage_ids.material_ids.mapped('customized')))
            # Custom is derived from BoM edits only, never a manual choice.
            piece.is_custom = piece.custom_bom_modified

    def action_toggle_custom(self):
        """Reject obsolete controls from an already-open browser without writes."""
        self.ensure_one()
        self._check_manager(self.company_id)
        self.check_access('write')
        raise UserError(_('Custom تُحدد تلقائيًا عند تعديل BoM فقط. حدّث الصفحة لإظهار الواجهة الجديدة.'))

    def _compute_production_count(self):
        for piece in self:
            piece.production_count = len(piece.stage_ids.production_id)

    @api.model
    def _check_manager(self, company=None):
        if not (self.env.user._is_admin() or self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')):
            raise AccessError(_('اعتماد خطة الإنتاج متاح لمدير المصنع فقط.'))
        if company and company not in self.env.companies:
            raise AccessError(_('الشركة خارج الشركات المسموح بها.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('تُنشأ القطع من تحديث الاحتياجات فقط.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_('استخدم إجراءات معاينة واعتماد الخطة.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su or self.filtered(lambda r: r.state != 'draft'):
            raise AccessError(_('لا يمكن حذف خطة معتمدة.'))
        self.stage_ids.input_ids.unlink()
        return super().unlink()

    @api.model
    def _lock_company(self, company):
        self.env['furniture.mrp.stage.replenishment.rule']._stage_replenishment_lock_company(company)

    def _dimensions(self):
        self.ensure_one()
        return tuple(round(value or 0, 3) for value in
                     (self.bom_id.furniture_width_cm, self.bom_id.furniture_depth_cm, self.bom_id.furniture_height_cm))

    def _same_product(self, product):
        return (product.furniture_dimension_source_product_id or product) == self.product_id

    def _target_stage_lane(self):
        self.ensure_one()
        if ((self.preview_json or {}).get('cycle_version') == 'parallel_finish_v1'
                and self.target_lane in ('bases', 'preparation')):
            return 'finish'
        return self.target_lane

    @api.model
    def _parallel_finish_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'furniture_need_to_produce.parallel_finish_v1', 'False') == 'True'

    def _supply_pools(self, ignore_pieces=None):
        """Availability is physical FIFO stock plus unclaimed open output.

        Unapproved previews only claim planning capacity. Approved stock claims
        are backed by assigned moves; future claims wait on the exact source line.
        """
        self.ensure_one()
        pools = defaultdict(list)
        Output = self.env['furniture.mrp.lane.output'].sudo()
        outputs = Output.search([('company_id', '=', self.company_id.id),
                                 ('furniture_model_id', '=', self.furniture_model_id.id),
                                 ('bom_id', '=', self.bom_id.id)])
        outputs = outputs.filtered(lambda o: o.lane in ROLE_LANE and self._same_product(o.final_product_id)
                                   and o._furniture_handoff_dimension_key() == self._dimensions())
        claims = self.env['furniture.need.to.produce.input'].sudo().search([
            ('piece_id.company_id', '=', self.company_id.id),
            ('piece_id.state', 'in', ['draft', 'approved']),
            ('piece_id', 'not in', [p.id for p in (ignore_pieces if ignore_pieces is not None else self)
                                  if isinstance(p.id, int)]),
        ])
        stock_claims, incoming_claims = defaultdict(float), defaultdict(float)
        def source_key(line):
            return (line.production_id.id, (line.cost_origin_line_id or line).id)
        for claim in claims:
            committed = sum(claim.handoff_ids.filtered(
                lambda handoff: handoff.state in ('reserved', 'consumed')
            ).mapped('quantity'))
            outstanding = max(claim.quantity - committed, 0.0)
            if claim.output_id:
                stock_claims[claim.output_id.id] += outstanding
            else:
                source_line = claim.source_line_id or claim.source_stage_id.production_id.production_line_ids[:1]
                if source_line:
                    incoming_claims[source_key(source_line)] += outstanding
        for output in outputs.sorted(lambda o: (o.fifo_date or o.ready_at or fields.Datetime.now(), o.id)):
            quantity = max(output.physical_available_qty - stock_claims[output.id], 0)
            # An allocated incoming line can become ready in several receipts.
            # Its existing consumers own the first available output even before
            # their handoffs are created; only the remainder is free FIFO stock.
            key = source_key(output.production_line_id)
            claimed_ready = min(quantity, incoming_claims[key])
            quantity -= claimed_ready
            incoming_claims[key] -= claimed_ready
            if quantity > 0.000001:
                pools[output.lane].append({'kind': 'stock', 'source_id': output.id, 'quantity': quantity})
        lines = self.env['furniture.mrp.production.line'].sudo().search([
            ('production_id.company_id', '=', self.company_id.id), ('active', '=', True),
            ('furniture_order_model_id', '=', self.furniture_model_id.id), ('bom_id', '=', self.bom_id.id),
            ('production_id.state', 'not in', ['done', 'cancelled']),
        ])
        produced = defaultdict(float)
        for output in outputs:
            produced[output.production_line_id.id] += output.qty_ready
        Production = self.env['furniture.mrp.production']
        for line in lines.sorted('id'):
            plan_stage = line.production_id.need_to_produce_stage_id
            if plan_stage and not (
                plan_stage.piece_id.state == 'approved'
                and plan_stage.piece_id.buffer_rule_id
                and plan_stage.lane == plan_stage.piece_id._target_stage_lane()
            ):
                # Intermediate stages already belong to another approved
                # piece. Only a department buffer's terminal production is
                # free supply that a later final-product plan may claim.
                continue
            if not self._same_product(line.product_id):
                continue
            if tuple(round(x or 0, 3) for x in (line.width_cm, line.depth_cm, line.height_cm)) != self._dimensions():
                continue
            lane = line.production_id.production_lane
            role = Production._furniture_lane_output_lanes().get(lane, lane)
            if role not in ROLE_LANE:
                continue
            quantity = line.product_uom_id._compute_quantity(line.product_qty, line.product_id.uom_id)
            quantity = max(quantity - produced[line.id], 0)
            reserved = min(quantity, incoming_claims[source_key(line)])
            quantity -= reserved
            incoming_claims[source_key(line)] -= reserved
            if quantity > 0.000001:
                pools[role].append({'kind': 'incoming', 'source_id': line.id, 'quantity': quantity})
        return dict(pools)

    def _build_preview(self, ignore_pieces=None):
        self.ensure_one()
        pools = self._supply_pools(ignore_pieces=ignore_pieces)
        if self.buffer_rule_id:
            # This piece represents the shortage AFTER its target stock/open
            # production were netted from Max. Reusing them here would subtract
            # the same coverage twice and generate an empty production route.
            output_role = self.env['furniture.mrp.production']._furniture_lane_output_lanes().get(
                self.target_lane, self.target_lane,
            )
            if self._parallel_finish_enabled() and self.target_lane in ('bases', 'preparation'):
                output_role = 'finish'
            pools.pop(output_role, None)
        return plan_piece(target_lane=self.target_lane, pools=pools,
                          parallel_finish=self._parallel_finish_enabled())

    def _replace_preview(self, preview):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['state', 'stage_ids'])
        if self.state != 'draft':
            raise UserError(_('لا يمكن تغيير مسار قطعة معتمدة.'))
        self.stage_ids.invalidate_recordset(['material_ids', 'materials_customized'])
        self.stage_ids.material_ids.invalidate_recordset()
        # A complete explicit snapshot preserves additions, deletions, and an
        # intentionally empty material list. Quantity-only overlays would
        # silently restore deleted master-BoM rows after a stock refresh.
        custom = {
            code: [{
                'stage_code': row.stage_code, 'product_id': row.product_id.id,
                'uom_id': row.uom_id.id, 'quantity': row.quantity,
            } for row in stage.material_ids if row.stage_code == code]
            for stage in self.stage_ids
            if stage.materials_customized or any(stage.material_ids.mapped('customized'))
            for code in (stage.stage_codes or [stage.stage_code])
        }
        metadata = dict((self.preview_json or {}).get('custom_approval', {}))
        if self.custom_bom_modified:
            metadata['bom_modified'] = True
        preview = dict(preview)
        if metadata:
            preview['custom_approval'] = metadata
        self.stage_ids.sudo().unlink()
        stages = {}
        for number, row in enumerate(preview['stages']):
            stage = self.env['furniture.need.to.produce.stage'].sudo().create({
                'piece_id': self.id, 'sequence': (number + 1) * 10, 'plan_key': row['key'],
                'lane': row['lane'], 'stage_code': row['stage_code'], 'stage_codes': row['stage_codes'],
                'quantity': row['quantity'], 'estimated_hours': row['estimated_hours'],
            })
            stages[row['key']] = stage
            stage._load_materials(custom)
        for row in preview['stages']:
            for source in row['inputs']:
                self.env['furniture.need.to.produce.input'].sudo().create({
                    'stage_id': stages[row['key']].id, 'role': source['role'], 'kind': source['kind'],
                    'quantity': source['quantity'],
                    'output_id': source.get('source_id') if source['kind'] == 'stock' else False,
                    'source_line_id': source.get('source_id') if source['kind'] == 'incoming' else False,
                    'source_stage_id': stages[source['source_key']].id if source['kind'] == 'stage' else False,
                })
        self.sudo().write({'preview_json': preview, 'route_text': preview['route_text'],
                          'total_work_hours': preview['total_work_hours'],
                          'critical_path_hours': preview['critical_path_hours'],
                          'critical_path_days': preview['critical_path_days'],
                          'unknown_incoming_wait': preview['has_unknown_incoming_wait']})

    @api.model
    def _final_committed_quantity(self, rule):
        """Unreceived packaging output, not a second count of finished stock.

        Both existing bulk orders and approved one-piece orders represent final
        capacity. Their actual receipts already enter finished stock, so count
        only the remainder. Traceable merged receipts are allocated once across
        their source lines, rather than deducted in full from every line.
        """
        lines = self.env['furniture.mrp.production.line'].sudo().search([
            ('production_id.company_id', '=', rule.company_id.id), ('active', '=', True),
            ('production_id.production_lane', '=', 'packaging'),
            ('production_id.state', 'not in', ['done', 'cancelled']),
            ('furniture_order_model_id', '=', rule.furniture_model_id.id),
            ('bom_id', '=', rule.bom_id.id),
        ]).filtered(lambda line: (
            line.product_id.furniture_dimension_source_product_id or line.product_id
        ) == rule.product_id)
        if not lines:
            return 0.0
        unit = rule.product_id.uom_id
        planned = {
            line.id: line.product_uom_id._compute_quantity(line.product_qty, unit)
            for line in lines
        }
        finished_location = self.env.ref('furniture_mrp.location_finished_goods', raise_if_not_found=False)
        if not finished_location:
            return sum(planned.values())
        receipts = self.env['stock.move'].sudo().search([
            ('company_id', '=', rule.company_id.id), ('state', '=', 'done'),
            ('location_dest_id', '=', finished_location.id),
            ('location_id', '!=', finished_location.id),
            '|', ('furniture_source_production_line_id', 'in', lines.ids),
            ('furniture_source_production_line_ids', 'in', lines.ids),
        ], order='date,id').filtered(lambda move: (
            move.product_id.furniture_dimension_source_product_id or move.product_id
        ) == rule.product_id)
        received = defaultdict(float)
        for move in receipts:
            remaining = move.product_uom._compute_quantity(move.quantity, unit)
            source_lines = move.furniture_source_production_line_ids or move.furniture_source_production_line_id
            for source in source_lines.sorted('id'):
                if (source.product_id.furniture_dimension_source_product_id or source.product_id) != rule.product_id:
                    continue
                capacity = source.product_uom_id._compute_quantity(source.product_qty, unit)
                quantity = min(max(capacity - received[source.id], 0.0), remaining)
                received[source.id] += quantity
                remaining -= quantity
                if remaining <= 0.000001:
                    break
        return sum(max(quantity - received[line_id], 0.0) for line_id, quantity in planned.items())

    @api.model
    def _refresh_company(self, company):
        self._lock_company(company)
        Piece = self.sudo().with_company(company)
        existing = Piece.search([('company_id', '=', company.id), ('state', 'in', ['draft', 'approved'])])
        for piece in existing.filtered(lambda p: p.state == 'approved'):
            target = piece.stage_ids.filtered(lambda s: s.lane == piece._target_stage_lane())
            if target and all(s.production_id.state == 'done' for s in target):
                piece.write({'state': 'done'})
        Stage = self.env['furniture.mrp.stage.replenishment.rule'].sudo().with_company(company)
        Final = self.env['furniture.mrp.final.replenishment.rule'].sudo().with_company(company)
        stage_rules = Stage._stage_replenishment_sync_company(company, lock=False)
        final_rules = Final._final_replenishment_sync_company(company, lock=False)
        buffer_rules = stage_rules.filtered(
            lambda rule: rule.max_qty > 0 and rule.stage_code in STAGE_LANE
        )
        # Release obsolete draft claims before allocating any other piece.
        # Approved work remains auditable even if its policy is later disabled.
        Piece.search([
            ('company_id', '=', company.id), ('state', '=', 'draft'),
            ('buffer_rule_id', '!=', False),
            ('buffer_rule_id', 'not in', buffer_rules.ids),
        ]).unlink()
        finished = Final._final_replenishment_finished_stock_map(final_rules)
        requests = []
        for rule in final_rules.filtered(lambda r: r.max_qty > 0):
            stock = max(finished.get((company.id, rule.product_id.id, rule.furniture_model_id.id), 0), 0)
            rounding = rule.uom_id.rounding or 0.001
            if float_compare(stock, rule.max_qty, precision_rounding=rounding) >= 0:
                if rule.replenishment_cycle_active:
                    rule._final_replenishment_internal_write({'replenishment_cycle_active': False})
            elif float_compare(stock, rule.min_qty, precision_rounding=rounding) <= 0:
                if not rule.replenishment_cycle_active:
                    rule._final_replenishment_internal_write({'replenishment_cycle_active': True})
            if stock > rule.min_qty and not rule.replenishment_cycle_active:
                continue
            # Existing upholstery is supply, not a second finished piece. Only
            # already-planned packaging counts as a committed final destination.
            wanted = max(rule.max_qty - stock - Piece._final_committed_quantity(rule), 0)
            requests.append((rule, 'packaging', wanted, 'final_rule_id'))
        # A department's own buffer is independent of finished-product demand.
        # Its shortage is evaluated after the final-piece previews claim supply.
        for rule, lane, wanted, link in requests:
            Piece._sync_request(rule, lane, wanted, link)
        paired_rules = (buffer_rules.filtered(lambda rule: rule.stage_code in ('bases', 'finishing'))
                        if Piece._parallel_finish_enabled() else Stage.browse())
        paired_groups = defaultdict(lambda: Stage.browse())
        for rule in paired_rules:
            paired_groups[(rule.product_id.id, rule.furniture_model_id.id, rule.bom_id.id)] |= rule
        for rules in paired_groups.values():
            sample = rules[0]
            temporary = Piece.new({'company_id': company.id, 'product_id': sample.product_id.id,
                                   'furniture_model_id': sample.furniture_model_id.id, 'bom_id': sample.bom_id.id})
            pool = temporary._supply_pools(ignore_pieces=Piece.browse()).get('finish', [])
            ready = sum(row['quantity'] for row in pool if row['kind'] == 'stock')
            coverage = sum(row['quantity'] for row in pool)
            # The two departments work on the SAME units. Keep both saved
            # policies, but fulfill the greatest shortage once, never add them.
            owner = max(rules, key=lambda rule: (
                max(rule.max_qty - coverage, 0) if ready <= rule.min_qty else 0, -rule.id))
            for rule in rules.sorted(lambda rule: rule == owner):
                wanted = max(rule.max_qty - coverage, 0) if rule == owner and ready <= rule.min_qty else 0
                Piece._sync_request(rule, STAGE_LANE[rule.stage_code], wanted, 'buffer_rule_id')
        for rule in buffer_rules - paired_rules:
            lane = STAGE_LANE[rule.stage_code]
            temporary = Piece.new({'company_id': company.id, 'product_id': rule.product_id.id,
                                   'furniture_model_id': rule.furniture_model_id.id, 'bom_id': rule.bom_id.id})
            pools = temporary._supply_pools(ignore_pieces=Piece.browse())
            role = 'finish' if lane == 'preparation' else lane
            ready = sum(s['quantity'] for s in pools.get(role, []) if s['kind'] == 'stock')
            coverage = sum(s['quantity'] for s in pools.get(role, []))
            # Approved buffer target output is included in coverage exactly
            # once, whether it is still incoming or has become physical stock.
            wanted = max(rule.max_qty - coverage, 0) if ready <= rule.min_qty else 0
            Piece._sync_request(rule, lane, wanted, 'buffer_rule_id')
        active_final_ids = [r.id for r, _l, _w, _f in requests]
        stale = Piece.search([('company_id', '=', company.id), ('state', '=', 'draft'),
                              ('final_rule_id', '!=', False), ('final_rule_id', 'not in', active_final_ids)])
        stale.unlink()
        self.env['furniture.need.to.produce.input']._release_available_inputs(company=company)
        return True

    @api.model
    def _sync_request(self, rule, lane, wanted, link):
        if not math.isfinite(wanted) or wanted > 500 or abs(wanted - round(wanted)) > 0.00001:
            raise UserError(_('الخطة بالقطعة: اضبط الاحتياج كعدد صحيح لا يتجاوز ٥٠٠ قطعة في الدفعة.'))
        wanted = int(round(wanted))
        drafts = self.sudo().search([(link, '=', rule.id), ('state', '=', 'draft')], order='id')
        drafts[wanted:].unlink()
        drafts = drafts[:wanted]
        for index in range(len(drafts), wanted):
            piece = self.sudo().create({'name': _('قطعة جديدة'),
                                        'company_id': rule.company_id.id, 'product_id': rule.product_id.id,
                                        'furniture_model_id': rule.furniture_model_id.id, 'bom_id': rule.bom_id.id,
                                        'target_lane': lane, link: rule.id})
            piece.write({'name': _('قطعة #%s') % piece.id})
            drafts |= piece
        # Release stale preview claims together, then distribute FIFO once.
        for piece in drafts:
            preview = piece._build_preview(ignore_pieces=drafts)
            # Prior previews are excluded only until they are rebuilt.
            piece._replace_preview(preview)
            drafts -= piece

    def action_refresh(self):
        self._check_manager(self.env.company)
        self._refresh_company(self.env.company)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _check_still_needed(self):
        """A preview is not authority to exceed a subsequently changed target."""
        self.ensure_one()
        rule = self.final_rule_id or self.buffer_rule_id
        if not rule or not rule.active or rule.max_qty <= 0:
            raise UserError(_('حدود الاحتياج تغيرت. حدّث الاحتياجات قبل الاعتماد.'))
        if self.final_rule_id:
            stock_map = rule._final_replenishment_finished_stock_map(rule)
            stock = max(stock_map.get((self.company_id.id, self.product_id.id, self.furniture_model_id.id), 0), 0)
            needed = rule.max_qty - stock - self._final_committed_quantity(rule)
        else:
            role = ('finish' if self.target_lane == 'preparation' or (
                self.target_lane == 'bases' and self._parallel_finish_enabled()) else self.target_lane)
            pool = self._supply_pools().get(role, [])
            ready = sum(row['quantity'] for row in pool if row['kind'] == 'stock')
            needed = rule.max_qty - sum(row['quantity'] for row in pool) if ready <= rule.min_qty else 0
        if needed < 1 - 0.000001:
            raise UserError(_('الاحتياج أصبح مغطى أو تغيرت حدوده. حدّث القائمة؛ لن ننشئ إنتاجًا زائدًا.'))
        return needed

    def _approval_policy_key(self):
        self.ensure_one()
        return (self.company_id.id, self.final_rule_id.id, self.buffer_rule_id.id)

    def _approval_group_key(self):
        """Only identical products, recipes and complete remaining routes batch.

        Exact stock/source IDs remain on each claim, not on the batch key: two
        otherwise identical pieces may draw from different FIFO receipts.
        A custom material recipe or different stock-covered operation splits
        the entire route, so stages never accidentally recombine later.
        """
        self.ensure_one()
        return (self.company_id.id, self.product_id.id, self.furniture_model_id.id,
                self.bom_id.id, self._dimensions(), self.target_lane,
                self.final_rule_id.id, self.buffer_rule_id.id,
                self.id if self.is_custom else False,
                tuple(stage._approval_signature() for stage in self.stage_ids.sorted('sequence')))

    def action_approve(self):
        if not self:
            return False
        for company in self.company_id.sorted('id'):
            self._check_manager(company)
            self._lock_company(company)
        self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(self.ids)])
        self.invalidate_recordset()
        groups, requested = {}, defaultdict(int)
        newly_approved = self.browse()
        batch_enabled = self.env['ir.config_parameter'].sudo().get_param(
            'furniture_need_to_produce.group_compatible_approvals', 'False',
        ) == 'True'
        for piece in self.sorted('id'):
            if piece.state == 'approved':
                continue  # Idempotent double-click.
            if piece.state != 'draft':
                raise UserError(_('لا يمكن اعتماد خطة مكتملة أو ملغاة.'))
            piece._custom_production_partner_values()
            needed = piece._check_still_needed()
            policy = piece._approval_policy_key()
            requested[policy] += 1
            if requested[policy] > needed + 0.000001:
                raise UserError(_('عدد القطع المحددة أكبر من الاحتياج الحالي. حدّث القائمة قبل الاعتماد.'))
            current = piece._build_preview()
            saved_route = {key: value for key, value in (piece.preview_json or {}).items()
                           if key != 'custom_approval'}
            if json.dumps(current, sort_keys=True) != json.dumps(saved_route, sort_keys=True):
                raise UserError(_('المخزون أو الأوامر المتاحة تغيّرت. اضغط تحديث الاحتياجات وراجع المسار قبل الاعتماد.'))
            for source in piece.stage_ids.input_ids.filtered(lambda i: i.kind == 'incoming'):
                if source.source_line_id.production_id.state == 'draft':
                    raise UserError(_('يوجد أمر قائم مسودة يغطي الاحتياج. أكّده أولًا ثم راجع الخطة؛ لن ننشئ أمرًا مكررًا.'))
            key = piece._approval_group_key() if batch_enabled else piece.id
            groups[key] = groups.get(key, self.browse()) | piece
        # Validate every selected preview before creating anything. Existing
        # approved orders are never enlarged or retroactively merged.
        for pieces in groups.values():
            stages_by_key = {}
            for stage in pieces.stage_ids.sorted(lambda s: (s.sequence, s.id)):
                stages_by_key[stage.plan_key] = stages_by_key.get(stage.plan_key, stage.browse()) | stage
            for stages in stages_by_key.values():
                stages._create_production()
            pieces.sudo().write({'state': 'approved', 'approved_by': self.env.uid, 'approved_at': fields.Datetime.now()})
            newly_approved |= pieces
        for piece in newly_approved.sorted('id'):
            piece.stage_ids.input_ids._reserve_ready_inputs()
            for claim in piece.stage_ids.input_ids.filtered(lambda row: row.kind == 'stock'):
                reserved = sum(claim.handoff_ids.filtered(lambda h: h.state in ('reserved', 'consumed')).mapped('quantity'))
                if reserved + 0.000001 < claim.quantity:
                    raise UserError(_('الرصيد الجاهز تغيّر أثناء الاعتماد؛ حدّث الخطة وأعد المحاولة.'))
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_open_productions(self):
        self._check_manager()
        return {'type': 'ir.actions.act_window', 'name': _('أوامر القطعة'), 'res_model': 'furniture.mrp.production',
                'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
                'domain': [('id', 'in', self.stage_ids.production_id.ids)], 'target': 'current'}


class PlanStage(models.Model):
    _name = 'furniture.need.to.produce.stage'
    _description = 'Piece production route stage'
    _order = 'piece_id, sequence, id'
    _rec_name = 'lane'

    piece_id = fields.Many2one('furniture.need.to.produce', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='piece_id.company_id', store=True, index=True)
    state = fields.Selection(related='piece_id.state')
    sequence = fields.Integer(default=10)
    plan_key = fields.Char(required=True)
    lane = fields.Selection(LANES, required=True)
    stage_code = fields.Selection(FURNITURE_STAGE_SELECTION, required=True)
    stage_codes = fields.Json()
    quantity = fields.Float(default=1, required=True)
    estimated_hours = fields.Float(readonly=True)
    estimated_days = fields.Float(compute='_compute_operation_timing', string='أيام عمل (١٠ ساعات)')
    operation_timing = fields.Char(compute='_compute_operation_timing', string='مدة كل عملية')
    production_id = fields.Many2one('furniture.mrp.production', readonly=True, ondelete='restrict', index=True)
    material_ids = fields.One2many('furniture.need.to.produce.material', 'stage_id')
    input_ids = fields.One2many('furniture.need.to.produce.input', 'stage_id', readonly=True)
    materials_initialized = fields.Boolean(default=False)
    materials_customized = fields.Boolean(default=False, readonly=True)

    @api.depends('lane', 'piece_id.name')
    def _compute_display_name(self):
        labels = dict(LANES)
        for stage in self:
            stage.display_name = '%s — %s' % (labels.get(stage.lane, stage.lane or ''), stage.piece_id.name or '')

    @api.depends('estimated_hours', 'stage_codes')
    def _compute_operation_timing(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for stage in self:
            stage.estimated_days = stage.estimated_hours / 10.0
            codes = stage.stage_codes or []
            hours = stage.estimated_hours / len(codes) if codes else 0
            stage.operation_timing = ' | '.join('%s: %g ساعة / %g يوم عمل' %
                                               (labels.get(code, code), hours, hours / 10.0)
                                               for code in codes)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('مراحل الخطة ينشئها النظام فقط.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            self.env['furniture.need.to.produce']._check_manager()
            if set(vals) != {'material_ids'}:
                raise AccessError(_('عدّل الخامات من تفاصيل المرحلة فقط.'))
            pieces = self.piece_id
            self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(pieces.ids)])
            pieces.invalidate_recordset(['state'])
            if any(p.state != 'draft' or p.company_id not in self.env.companies for p in pieces):
                raise AccessError(_('لا يمكن تعديل خامات خطة معتمدة.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su or self.filtered('production_id'):
            raise AccessError(_('لا يمكن حذف مرحلة مرتبطة بإنتاج.'))
        self.input_ids.unlink()
        return super().unlink()

    def _production_values(self):
        self.ensure_one()
        piece, bom = self.piece_id, self.piece_id.bom_id
        return {'company_id': piece.company_id.id, 'product_id': piece.product_id.id,
                'furniture_order_model_id': piece.furniture_model_id.id, 'bom_id': bom.id,
                'product_qty': self.quantity, 'production_lane': self.lane, 'stage_plan_mode': 'custom',
                'width_cm': bom.furniture_width_cm, 'depth_cm': bom.furniture_depth_cm, 'height_cm': bom.furniture_height_cm}

    def _approval_signature(self):
        self.ensure_one()
        materials, inputs = defaultdict(float), defaultdict(float)
        for row in self.material_ids:
            materials[(row.stage_code, row.product_id.id, row.uom_id.id)] += row.quantity
        for claim in self.input_ids:
            inputs[(claim.role, claim.kind, claim.source_stage_id.plan_key or '')] += claim.quantity
        return (self.plan_key, self.lane, tuple(self.stage_codes or []), self.quantity,
                self.materials_initialized, tuple(sorted(materials.items())), tuple(sorted(inputs.items())))

    def _load_materials(self, custom):
        self.ensure_one()
        values = self._production_values()
        production = self.env['furniture.mrp.production'].sudo().new(values)
        line = self.env['furniture.mrp.production.line'].sudo().new({
            **{k: v for k, v in values.items() if k in self.env['furniture.mrp.production.line']._fields},
            'production_id': production.id, 'stage_selection_initialized': True,
        })
        for stage_code in self.stage_codes:
            if stage_code in custom:
                self.env['furniture.need.to.produce.material'].sudo().create([{
                    **row, 'stage_id': self.id, 'customized': True,
                } for row in custom[stage_code]])
                self.sudo().write({'materials_customized': True})
                continue
            commands = production._prepare_material_lines_for_product_stage(self.piece_id.product_id, self.quantity, stage_code, line)
            quantities = defaultdict(float)
            for command in commands:
                vals = command[2]
                quantities[(vals['product_id'], vals['product_uom_id'])] += vals.get('qty_needed', 0)
            for (product, uom), quantity in quantities.items():
                self.env['furniture.need.to.produce.material'].sudo().create({
                    'stage_id': self.id, 'stage_code': stage_code, 'product_id': product, 'uom_id': uom,
                    'quantity': quantity,
                })
        self.sudo().write({'materials_initialized': True})

    def _production_schedule_values(self):
        now = fields.Datetime.now()
        return {
            "date_planned_start": now,
            "date_planned_finish": now + timedelta(days=max(1, math.ceil(sum(self.piece_id.mapped("critical_path_days"))))),
        }

    def _create_production(self):
        if not self:
            return self.env['furniture.mrp.production']
        first = self.sorted('id')[0]
        if self.production_id:
            if len(self.production_id) != 1 or any(not stage.production_id for stage in self):
                raise UserError(_('لا يمكن دمج مراحل سبق إنشاء أوامر مختلفة لها.'))
            return self.production_id
        if any(stage.piece_id._approval_group_key() != first.piece_id._approval_group_key()
               or stage.plan_key != first.plan_key for stage in self):
            raise UserError(_('التجميع يتطلب نفس الصنف والموديل والمسار والخامات.'))
        now = fields.Datetime.now()
        values = first._production_values()
        values.update(first.piece_id._custom_production_partner_values())
        values['product_qty'] = sum(self.mapped('quantity'))
        production = self.env['furniture.mrp.production'].sudo().with_company(first.company_id).with_context(
            furniture_skip_material_refresh=True, furniture_skip_stage_plan_sync=True, furniture_skip_line_consolidation=True,
        ).create({**values, 'need_to_produce_stage_id': first.id,
                  **self._production_schedule_values(),
                  'temporary_stage_fixed_hours': 2.0 * values['product_qty'] if len(self) > 1 else 2.0,
                  'notes': '%s / %s' % ('Custom' if first.piece_id.is_custom else 'Need to Produce',
                                       ', '.join(self.piece_id.mapped('name')))})
        line_values = {k: v for k, v in values.items() if k in self.env['furniture.mrp.production.line']._fields}
        line_values.update(production._stage_selection_vals(first.stage_codes))
        self.env['furniture.mrp.production.line'].sudo().with_context(
            furniture_preserve_explicit_bom=True, furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True, furniture_skip_stage_plan_sync=True,
            furniture_skip_running_line_initialization=True,
        ).create({**line_values, 'production_id': production.id, 'stage_selection_initialized': True})
        self.sudo().write({'production_id': production.id})
        production._refresh_material_lines_for_stage_plan()
        production.action_confirm()
        return production

    def action_open(self):
        self.ensure_one()
        self.env['furniture.need.to.produce']._check_manager(self.company_id)
        return {'type': 'ir.actions.act_window', 'name': _('مراجعة خامات مرحلة القطعة'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}


class PlanMaterial(models.Model):
    _name = 'furniture.need.to.produce.material'
    _description = 'Piece-specific stage materials — never edits the master BoM'

    stage_id = fields.Many2one('furniture.need.to.produce.stage', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='stage_id.company_id', store=True)
    stage_code = fields.Selection(FURNITURE_STAGE_SELECTION, required=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='restrict')
    uom_id = fields.Many2one('uom.uom', required=True, ondelete='restrict')
    quantity = fields.Float(required=True, default=1)
    customized = fields.Boolean(default=False, readonly=True)

    def _check_editable(self):
        self.env['furniture.need.to.produce']._check_manager()
        pieces = self.stage_id.piece_id
        if pieces:
            self.env.cr.execute('SELECT id FROM furniture_need_to_produce WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(pieces.ids)])
            pieces.invalidate_recordset(['state'])
        if any(p.state != 'draft' or p.company_id not in self.env.companies for p in pieces):
            raise AccessError(_('تعديل الخامات مسموح قبل الاعتماد فقط وفي شركتك.'))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        if not self.env.su:
            for vals in vals_list:
                stage = self.env['furniture.need.to.produce.stage'].browse(vals.get('stage_id')).exists()
                if not stage:
                    raise AccessError(_('المرحلة غير موجودة.'))
                self.new({'stage_id': stage.id})._check_editable()
                vals['customized'] = True
        rows = super().create(vals_list)
        if not self.env.su:
            rows.stage_id.sudo().write({'materials_customized': True})
        return rows

    def write(self, vals):
        if not self.env.su:
            self._check_editable()
            if 'stage_id' in vals:
                raise AccessError(_('لا يمكن نقل خامة إلى قطعة أخرى.'))
            vals = {**vals, 'customized': True}
        result = super().write(vals)
        if not self.env.su:
            self.stage_id.sudo().write({'materials_customized': True})
        return result

    def unlink(self):
        if not self.env.su:
            self._check_editable()
        stages = self.stage_id
        result = super().unlink()
        if not self.env.su:
            stages.sudo().write({'materials_customized': True})
        return result

    @api.constrains('quantity', 'product_id', 'uom_id', 'stage_code', 'stage_id')
    def _validate_material(self):
        for row in self:
            if not math.isfinite(row.quantity) or row.quantity < 0:
                raise ValidationError(_('كمية الخامة يجب أن تكون صفرًا أو أكبر.'))
            if row.uom_id.category_id != row.product_id.uom_id.category_id:
                raise ValidationError(_('وحدة القياس لا تناسب الخامة.'))
            if row.stage_code not in (row.stage_id.stage_codes or []):
                raise ValidationError(_('الخامة يجب أن تخص مرحلة من مسار هذه القطعة.'))


class PlanInput(models.Model):
    _name = 'furniture.need.to.produce.input'
    _description = 'Exact allocated upstream supply for an approved piece'

    stage_id = fields.Many2one('furniture.need.to.produce.stage', required=True, ondelete='cascade', index=True)
    piece_id = fields.Many2one(related='stage_id.piece_id', store=True, index=True)
    company_id = fields.Many2one(related='piece_id.company_id', store=True, index=True)
    role = fields.Selection([(role, dict(LANES)[lane]) for role, lane in ROLE_LANE.items()], required=True)
    kind = fields.Selection([('stock', 'مخزون جاهز'), ('incoming', 'أمر قائم'), ('stage', 'مرحلة في خط القطعة')], required=True)
    quantity = fields.Float(required=True)
    output_id = fields.Many2one('furniture.mrp.lane.output', ondelete='restrict', index=True)
    source_line_id = fields.Many2one('furniture.mrp.production.line', ondelete='restrict', index=True)
    source_stage_id = fields.Many2one('furniture.need.to.produce.stage', ondelete='restrict')
    handoff_ids = fields.Many2many('furniture.mrp.lane.handoff', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('توزيع المخزون ينفذه مخطط الإنتاج فقط.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_('توزيع المخزون غير قابل للتغيير اليدوي.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su or self.filtered(lambda c: c.piece_id.state != 'draft'):
            raise AccessError(_('لا يمكن حذف توزيع مخزون معتمد.'))
        return super().unlink()

    def _reserve_ready_inputs(self):
        for claim in self.sudo().sorted('id'):
            # A receipt callback and an approval retry can both reach the same
            # allocation. Re-read its live coverage after taking the row lock.
            self.env.cr.execute(
                'SELECT id FROM furniture_need_to_produce_input WHERE id = %s FOR UPDATE',
                [claim.id],
            )
            claim.invalidate_recordset()
            claim.piece_id.invalidate_recordset(['state'])
            claim.handoff_ids.invalidate_recordset(['state', 'quantity'])
            live_handoffs = claim.handoff_ids.filtered(
                lambda handoff: handoff.state in ('reserved', 'consumed')
            )
            missing = max(claim.quantity - sum(live_handoffs.mapped('quantity')), 0.0)
            if missing <= 0.000001 or claim.piece_id.state != 'approved':
                continue
            production = claim.stage_id.production_id
            production.invalidate_recordset(['state'])
            if not production or production.state not in ('confirmed', 'in_production'):
                continue
            outputs = claim.output_id
            if not outputs:
                source_lines = claim.source_stage_id.production_id.production_line_ids.filtered('active')
                if claim.source_line_id:
                    source = claim.source_line_id
                    source_lines = source.production_id.production_line_ids.filtered(lambda line:
                        line.active and (line == source or line.cost_origin_line_id == (source.cost_origin_line_id or source)))
                if source_lines:
                    outputs = self.env['furniture.mrp.lane.output'].sudo().search([
                        ('production_line_id', 'in', source_lines.ids), ('lane', '=', claim.role)], order='fifo_date,ready_at,id')
            if not outputs:
                continue
            fifo_group = outputs._furniture_fifo_group_outputs()
            fifo_group._furniture_lock_rows()
            fifo_group._furniture_refresh_matching_groups()
            handoffs = self.env['furniture.mrp.lane.handoff']
            target_lines = production._furniture_handoff_lines()
            for output, line in ((output, line) for output in outputs for line in target_lines):
                # Reserve each arriving portion against this exact source. A
                # missing remainder remains a planning claim, not free supply
                # for another piece, and never accepts or starts the stage.
                final_product = production._furniture_line_final_product(line)
                needed = production._quantity_in_product_uom(final_product, line.product_qty, line.product_uom_id)
                production.invalidate_recordset(['upstream_handoff_ids'])
                covered = sum(production.upstream_handoff_ids.filtered(lambda handoff:
                    handoff.downstream_line_id == line and handoff.role == claim.role
                    and handoff.state in ('reserved', 'consumed')).mapped('quantity'))
                quantity = min(output.physical_available_qty, missing, max(needed - covered, 0))
                if quantity <= 0.000001:
                    continue
                output._furniture_assert_fifo_available()
                move = self.env['stock.move']._furniture_internal_create_for_handoff([{
                    'name': '%s / %s' % (production.name, claim.role), 'origin': production.name,
                    'company_id': production.company_id.id, 'product_id': output.wip_product_id.id,
                    'product_uom': output.uom_id.id, 'product_uom_qty': quantity,
                    'location_id': output.source_location_id.id,
                    'location_dest_id': production._stage_storage_location_for_handoff().id,
                    'furniture_source_production_line_id': output.production_line_id.id,
                    'furniture_lane_handoff_role': claim.role,
                }])
                move._action_confirm(merge=False)
                move._action_assign()
                if move.state != 'assigned':
                    raise UserError(_('تعذر حجز رصيد الخطة؛ حدث المخزون وأعد المحاولة.'))
                handoffs |= self.env['furniture.mrp.lane.handoff']._furniture_internal_create([{
                    'downstream_production_id': production.id, 'downstream_line_id': line.id,
                    'output_id': output.id, 'role': claim.role, 'quantity': quantity,
                    'transfer_move_id': move.id, 'state': 'reserved',
                }])
                missing -= quantity
                output.invalidate_recordset([
                    'handoff_ids', 'reserved_qty', 'assembled_qty',
                    'available_qty', 'physical_available_qty', 'state',
                ])
                output._furniture_refresh_matching_groups()
                if missing <= 0.000001:
                    break
            if not handoffs:
                continue
            # Retain cancelled rows for audit while appending replacement or
            # partial reservations. Only live rows count on the next retry.
            claim.write({'handoff_ids': [(4, handoff.id) for handoff in handoffs]})
            production.invalidate_recordset(['upstream_handoff_ids'])
            target_lines.invalidate_recordset(['downstream_handoff_ids'])
            production._furniture_sync_handoff_inherited_costs()
            if production._furniture_handoffs_cover_lines():
                production._furniture_notify_pending_handoff()

    @api.model
    def _release_available_inputs(self, outputs=None, company=None):
        domain = [('piece_id.state', '=', 'approved')]
        if company:
            domain.append(('company_id', '=', company.id))
        if outputs is not None:
            if not outputs:
                return
            domain.extend([
                ('company_id', 'in', outputs.company_id.ids),
                '|', '|', ('output_id', 'in', outputs.ids),
                ('source_line_id', 'in', (outputs.production_line_id | outputs.production_line_id.cost_origin_line_id).ids),
                ('source_stage_id.production_id', 'in', outputs.production_id.ids),
            ])
        claims = self.sudo().search(domain)
        for company in claims.company_id.sorted('id'):
            self.env['furniture.need.to.produce']._lock_company(company)
        # Includes partially reserved and cancelled allocations; the locked
        # reservation method subtracts existing live coverage idempotently.
        claims._reserve_ready_inputs()
