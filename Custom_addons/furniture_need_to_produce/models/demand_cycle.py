"""Manual and delivery demand use the same approval/allocation engine as shortages."""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .flow_bridge import _keep_existing_lane_records


class DemandPiece(models.Model):
    _inherit = 'furniture.need.to.produce'

    demand_line_id = fields.Many2one('furniture.mrp.production.line', readonly=True,
                                    copy=False, index=True, ondelete='restrict')

    def _dimensions(self):
        self.ensure_one()
        if self.demand_line_id:
            return tuple(round(self.demand_line_id[k] or 0, 3)
                         for k in ('width_cm', 'depth_cm', 'height_cm'))
        return super()._dimensions()

    def _approval_policy_key(self):
        return ('demand', self.demand_line_id.id) if self.demand_line_id else super()._approval_policy_key()

    def _approval_group_key(self):
        return (*super()._approval_group_key(), self.demand_line_id.id)

    def _check_still_needed(self):
        if not self.demand_line_id:
            return super()._check_still_needed()
        line = self.demand_line_id
        order = line.production_id
        if (order.production_lane != 'demand' or order.state != 'draft'
                or order.company_id != self.company_id or line.bom_id != self.bom_id
                or line.furniture_order_model_id != self.furniture_model_id
                or (line.product_id.furniture_dimension_source_product_id or line.product_id) != self.product_id):
            raise UserError(_('تغير أمر الإنتاج الأصلي؛ راجع الأصناف قبل التأكيد.'))
        approved = self.sudo().search_count([('demand_line_id', '=', line.id),
                                           ('state', 'in', ['approved', 'done'])])
        return line.product_uom_id._compute_quantity(line.product_qty, self.product_id.uom_id) - approved

    def _custom_production_partner_values(self):
        if not self.demand_line_id:
            return super()._custom_production_partner_values()
        line = self.demand_line_id
        return {key: self._validate_custom_partner((line[key] or line.production_id[key]).id or False)
                for key in ('buyer_partner_id', 'beneficiary_partner_id')}

    def _approval_cancel_blocker(self):
        parents = self.demand_line_id.production_id
        if parents and parents.demand_piece_ids != self:
            return _('ألغِ دورة التصنيع كاملة من أمر الإنتاج الأصلي.')
        return super()._approval_cancel_blocker()

    def action_cancel_approval(self, approval_token=None):
        parents = self.demand_line_id.production_id
        # Include every product on the source order, or it would be partly approved.
        if parents and parents.demand_piece_ids != self:
            raise UserError(_('ألغِ دورة التصنيع كاملة من أمر الإنتاج الأصلي.'))
        result = super().action_cancel_approval(approval_token=approval_token)
        if parents:
            self.sudo().write({'state': 'cancelled'})  # Retain immutable withdrawal history.
            parents.sudo().write({'state': 'cancelled'})
            parents.message_post(body=_('أُلغي اعتماد دورة التصنيع قبل بدء التنفيذ.'))
        return result


class DemandStage(models.Model):
    _inherit = 'furniture.need.to.produce.stage'

    def _production_values(self):
        values = super()._production_values()
        source = self.piece_id.demand_line_id
        if source:
            values.update({key: source[key] for key in ('width_cm', 'depth_cm', 'height_cm')})
            values.update({'demand_parent_id': source.production_id.id,
                           'future_order_id': source.production_id.future_order_id.id,
                           'future_order_line_id': source.production_id.future_order_line_id.id})
        return values

    def _production_schedule_values(self):
        values = super()._production_schedule_values()
        source = self[:1].piece_id.demand_line_id.production_id
        if source:
            values.update({'date_planned_start': source.date_planned_start,
                           'date_planned_finish': source.date_planned_finish})
        return values


class DemandProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    production_lane = fields.Selection(selection_add=[('demand', 'دورة المنتج النهائي')],
                                       ondelete={'demand': _keep_existing_lane_records})
    demand_parent_id = fields.Many2one('furniture.mrp.production', readonly=True,
                                      copy=False, ondelete='restrict', index=True)
    demand_child_ids = fields.One2many('furniture.mrp.production', 'demand_parent_id', readonly=True)
    demand_piece_ids = fields.One2many('furniture.need.to.produce', compute='_compute_demand_pieces')
    demand_order_count = fields.Integer(compute='_compute_demand_pieces')
    cycle_label = fields.Char(compute='_compute_cycle_label')

    @api.depends('production_lane', 'state')
    def _compute_cycle_label(self):
        labels = dict(self._fields['production_lane']._description_selection(self.env))
        for order in self:
            order.cycle_label = (_('دورة المنتج النهائي') if order.production_lane == 'demand'
                                 or (order.production_lane == 'legacy' and order.state == 'draft')
                                 else labels.get(order.production_lane, ''))

    @api.depends('production_line_ids', 'demand_child_ids')
    def _compute_demand_pieces(self):
        can_read_plans = self.env.user._is_admin() or self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')
        for order in self:
            order.demand_piece_ids = self.env['furniture.need.to.produce'].search([
                ('demand_line_id.production_id', '=', order.id), ('state', '!=', 'cancelled')]) if order.id and order.production_lane == 'demand' and can_read_plans else False
            order.demand_order_count = len(order.demand_child_ids)

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if 'production_lane' in names and values.get('production_lane') == 'legacy':
            values['production_lane'] = 'demand'
        return values

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get('demand_parent_id') for v in vals_list):
            raise AccessError(_('ربط أوامر المراحل يتم من اعتماد دورة التصنيع فقط.'))
        return super().create(vals_list)

    @api.constrains('use_priming', 'use_painting', 'use_carpentry', 'use_bases',
                    'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging')
    def _check_weekly_stage_selection(self):
        return super(DemandProduction, self.filtered(lambda o: o.production_lane != 'demand'))._check_weekly_stage_selection()

    def _required_stage_codes(self):
        if self.production_lane == 'demand' and self.state != 'draft':
            return []
        return super()._required_stage_codes()

    def write(self, vals):
        if 'demand_parent_id' in vals and not self.env.su:
            raise AccessError(_('لا يمكن تغيير مرجع دورة التصنيع.'))
        protected = {'production_line_ids', 'product_id', 'product_qty', 'bom_id',
                     'furniture_order_model_id', 'width_cm', 'depth_cm', 'height_cm',
                     'buyer_partner_id', 'beneficiary_partner_id', 'production_lane'}
        if protected & vals.keys():
            self._lock_demand_sources()
        if protected & vals.keys() and self.filtered(lambda o: o.production_lane == 'demand' and o.demand_child_ids):
            raise UserError(_('أُلغي الاعتماد أولًا قبل تغيير أصناف دورة التصنيع.'))
        result = super().write(vals)
        if 'state' in vals:
            for parent in self.demand_parent_id:
                children = parent.demand_child_ids
                state = ('done' if children and all(o.state == 'done' for o in children)
                         else 'in_production' if any(o.state in ('done', 'in_production') for o in children)
                         else 'confirmed')
                if parent.state not in ('draft', 'cancelled') and parent.state != state:
                    parent.sudo().write({'state': state})
        return result

    def _lock_demand_sources(self):
        sources = self.filtered(lambda o: o.production_lane == 'demand')
        if sources:
            self.env.cr.execute('UPDATE furniture_mrp_production SET write_date=write_date WHERE id IN %s', [tuple(sorted(sources.ids))])
            sources.invalidate_recordset(['state', 'demand_child_ids', 'demand_piece_ids'])

    def _prepare_material_line_commands_for_stage_plan(self):
        if self.production_lane == 'demand' and self.state != 'draft':
            return []
        return super()._prepare_material_line_commands_for_stage_plan()

    def action_confirm(self):
        demand = self.filtered(lambda o: o.production_lane == 'demand' or
                               (o.production_lane == 'legacy' and o.state == 'draft'))
        other = self - demand
        result = super(DemandProduction, other).action_confirm() if other else {'type': 'ir.actions.client', 'tag': 'reload'}
        Piece = self.env['furniture.need.to.produce']
        for order in demand.sorted('id'):
            Piece._check_manager(order.company_id)
            order.check_access('write')
            Piece._lock_company(order.company_id)
            # An UPDATE is also a serialization fence under Odoo's repeatable-read isolation.
            self.env.cr.execute('UPDATE furniture_mrp_production SET write_date=write_date WHERE id=%s', [order.id])
            order.invalidate_recordset()
            if order.state != 'draft':
                if order.state in ('confirmed', 'in_production', 'done') and order.demand_child_ids:
                    continue
                raise UserError(_('يمكن تأكيد أمر مسودة فقط.'))
            if not order.production_line_ids:
                raise UserError(_('أضف أصناف أمر الإنتاج أولًا.'))
            if order.production_lane != 'demand':
                order.write({'production_lane': 'demand'})
            order._prepare_production_lines_for_confirm()
            order._consolidate_equivalent_production_lines()
            lines = order.production_line_ids.filtered('active')
            quantities = {}
            for line in lines:
                quantity = line.product_uom_id._compute_quantity(line.product_qty, line.product_id.uom_id)
                if quantity <= 0 or abs(quantity - round(quantity)) > 0.000001:
                    raise ValidationError(_('دورة المنتج النهائي تحتاج عدد قطع صحيحًا موجبًا: %s') % line.product_id.display_name)
                if not line.bom_id or not line.furniture_order_model_id:
                    raise ValidationError(_('حدد الموديل وريسيبي التصنيع لكل صنف.'))
                quantities[line.id] = int(round(quantity))
            if not quantities or sum(quantities.values()) > 500:
                raise ValidationError(_('اعتمد من قطعة إلى 500 قطعة في المرة الواحدة.'))
            start = order.date_planned_start or fields.Datetime.now()
            finish = order.date_planned_finish or start + timedelta(days=2)
            if finish < start:
                if order.future_order_id:
                    finish = start + timedelta(days=1)
                else:
                    raise ValidationError(_('تاريخ الانتهاء المخطط يجب أن يكون بعد تاريخ البدء.'))
            order.write({'date_planned_start': start, 'date_planned_finish': finish})
            if order.demand_piece_ids:
                raise UserError(_('توجد خطة مرتبطة بالفعل؛ حدّث أمر الإنتاج.'))
            pieces = Piece.browse()
            for line in lines:
                for number in range(quantities[line.id]):
                    piece = Piece.sudo().create({
                        'name': '%s / %s / %s' % (order.name, line.product_id.display_name, number + 1),
                        'company_id': order.company_id.id,
                        'product_id': (line.product_id.furniture_dimension_source_product_id or line.product_id).id,
                        'furniture_model_id': line.furniture_order_model_id.id, 'bom_id': line.bom_id.id,
                        'target_lane': 'packaging', 'demand_line_id': line.id,
                    })
                    piece._replace_preview(piece._build_preview())
                    pieces |= piece
            pieces.with_user(self.env.user).action_approve()
            # Source is a summary, never a second executable legacy route.
            order.with_context(furniture_skip_stage_plan_sync=True, furniture_skip_material_refresh=True).write({
                'state': 'confirmed', **{key: False for key in order._stage_use_field_names()},
                'stage_plan_line_id': False, 'dimension_line_id': False,
            })
            order._refresh_material_lines_for_stage_plan()
            order.invalidate_recordset(['demand_piece_ids', 'demand_order_count'])
            order.message_post(body=_('تم اعتماد دورة المنتج النهائي وإنشاء %s أمر مرحلة مرتبطًا.') % len(order.demand_child_ids))
        return result

    def action_check_materials(self):
        if self.filtered(lambda o: o.production_lane == 'demand'):
            return self.action_open_demand_orders()
        return super().action_check_materials()

    def action_consume_materials(self):
        if self.filtered(lambda o: o.production_lane == 'demand'):
            raise UserError(_('صرف الخامات يتم من أوامر المراحل المرتبطة.'))
        return super().action_consume_materials()

    def action_open_demand_orders(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_window', 'name': _('أوامر مراحل التصنيع'),
                'res_model': self._name, 'views': [(False, 'list'), (False, 'form')],
                'domain': [('demand_parent_id', '=', self.id)], 'target': 'current'}

    def action_cancel(self):
        demand = self.filtered(lambda o: o.production_lane == 'demand')
        for order in demand:
            self.env['furniture.need.to.produce']._check_manager(order.company_id)
            if order.demand_piece_ids:
                order.demand_piece_ids.action_cancel_approval()
        return super().action_cancel()

    def action_reset_to_draft(self):
        if self.filtered(lambda o: o.production_lane == 'demand' and o.demand_piece_ids):
            raise UserError(_('ألغِ دورة التصنيع قبل إعادتها للمسودة.'))
        return super().action_reset_to_draft()


class DemandLine(models.Model):
    _inherit = 'furniture.mrp.production.line'

    def _selected_stage_codes(self):
        if self.production_id.production_lane == 'demand' and self.production_id.state != 'draft':
            return []
        return super()._selected_stage_codes()

    @api.model_create_multi
    def create(self, vals_list):
        parents = self.env['furniture.mrp.production'].browse([v['production_id'] for v in vals_list if v.get('production_id')])
        parents._lock_demand_sources()
        if parents.filtered(lambda p: p.production_lane == 'demand' and p.demand_child_ids):
            raise UserError(_('ألغِ دورة التصنيع أولًا قبل إضافة أصناف إلى الأمر الأصلي.'))
        return super().create(vals_list)

    def write(self, vals):
        protected = {'product_id', 'product_qty', 'product_uom_id', 'bom_id', 'production_id',
                     'furniture_order_model_id', 'width_cm', 'depth_cm', 'height_cm',
                     'buyer_partner_id', 'beneficiary_partner_id', 'active'}
        if protected & vals.keys():
            self.production_id._lock_demand_sources()
        if protected & vals.keys() and self.filtered(lambda l: l.production_id.production_lane == 'demand'
                and l.production_id.demand_child_ids):
            raise UserError(_('ألغِ دورة التصنيع أولًا قبل تعديل أصناف الأمر الأصلي.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda l: l.production_id.demand_child_ids):
            raise UserError(_('لا يمكن حذف صنف مرتبط بأوامر مراحل معتمدة.'))
        return super().unlink()


class DemandWizard(models.TransientModel):
    _inherit = 'furniture.mrp.production.first.line.wizard'

    def action_create_lines(self):
        creating = not self.production_id
        result = super().action_create_lines()
        if creating and self.production_id.production_lane == 'legacy':
            self.production_id.write({'production_lane': 'demand'})
        return result


class DeliveryDemand(models.Model):
    _inherit = 'furniture.mrp.future.order'

    def _create_shortage_production(self, future_line, specs):
        source = super()._create_shortage_production(future_line, specs)
        source.action_confirm()
        return source.demand_child_ids
