# -*- coding: utf-8 -*-
"""Explicit quality decisions, separate from completion and stock acceptance."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


def _decision_value(decision):
    if decision not in ('pass', 'reject'):
        raise ValidationError(_('اختار قبول أو رفض الجودة.'))
    return decision


def _review_state(review, signature):
    if not review or review.get('signature') != signature:
        return 'pending'
    return review.get('state', 'pending')


def _combined_state(states):
    states = list(states)
    if 'reject' in states:
        return 'reject'
    return 'pass' if states and all(state == 'pass' for state in states) else 'pending'


class FurnitureStageManualQuality(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    manual_quality_reviews = fields.Json(
        string='قرارات جودة المنتجات', default=dict, readonly=True, copy=False,
    )

    def write(self, vals):
        if 'manual_quality_reviews' in vals and not self.env.su:
            raise AccessError(_('سجّل قرار الجودة من أزرار الجودة فقط.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            self.env.context.get('default_manual_quality_reviews')
            or any(vals.get('manual_quality_reviews') for vals in vals_list)
        ):
            raise AccessError(_('لا يمكن إنشاء موافقات جودة مسبقة.'))
        return super().create(vals_list)

    def _manual_quality_active_lines(self):
        self.ensure_one()
        return (
            self._get_stage_line_ids_data('active_production_line_ids_data')
            - self._get_stage_line_ids_data('completed_production_line_ids_data')
        ).filtered(lambda line: line.active and line.production_id == self.production_order_id)

    def _manual_quality_signature(self, line):
        self.ensure_one()
        return [
            line.product_id.id, line.product_qty, line.product_uom_id.id,
            line.bom_id.id, line.width_cm, line.depth_cm, line.height_cm,
            fields.Datetime.to_string(self.date_start) if self.date_start else False,
        ]

    def _manual_quality_state(self, lines):
        self.ensure_one()
        reviews = self.sudo().manual_quality_reviews or {}
        return _combined_state(
            _review_state(reviews.get(str(line.id)), self._manual_quality_signature(line))
            for line in lines
        )

    def _require_manual_quality(self, lines):
        self.ensure_one()
        self.invalidate_recordset(['manual_quality_reviews'])
        stage_code = self.production_order_id._stage_model_to_code(self._name)
        # Upholstery is inspected by packaging before the incoming stock move.
        if stage_code == 'upholstery':
            return True
        if not lines or self._manual_quality_state(lines) != 'pass':
            raise UserError(_(
                'لا يمكن إنهاء المرحلة قبل قبول جودة كل منتج يدويًا. '
                'راجع المنتجات المرفوضة أو التي لم يتم فحصها.'
            ))
        return True

    def _record_manual_quality(self, lines, decision):
        self.ensure_one()
        _decision_value(decision)
        self.invalidate_recordset([
            'state', 'manual_quality_reviews', 'active_production_line_ids_data',
            'completed_production_line_ids_data', 'date_start',
        ])
        stage_code = self.production_order_id._stage_model_to_code(self._name)
        if stage_code == 'upholstery':
            raise UserError(_('جودة الكسوة يقبلها مشرف التغليف قبل استلام التحويل.'))
        if self.state not in ('in_progress', 'quality_check'):
            raise UserError(_('ابدأ تشغيل المنتج قبل تسجيل قرار الجودة.'))
        if not lines or lines - self._manual_quality_active_lines():
            raise AccessError(_('المنتجات المختارة ليست قيد التشغيل في هذه المرحلة.'))
        reviews = dict(self.sudo().manual_quality_reviews or {})
        for line in lines:
            reviews[str(line.id)] = {
                'state': decision, 'user_id': self.env.uid,
                'at': fields.Datetime.to_string(fields.Datetime.now()),
                'signature': self._manual_quality_signature(line),
            }
        self.sudo().write({'manual_quality_reviews': reviews})
        self.sudo().message_post(body=_(
            'فحص جودة يدوي — %(decision)s: %(products)s. '
            'لم يتم إنهاء المرحلة أو تحويل المخزون بهذا القرار.'
        ) % {
            'decision': _('مقبول') if decision == 'pass' else _('مرفوض'),
            'products': '، '.join(lines.mapped('product_id.display_name')),
        })
        return True

    def _add_stage_active_lines(self, production_lines):
        self.ensure_one()
        new_lines = production_lines - self._get_stage_line_ids_data('active_production_line_ids_data')
        if new_lines and self.sudo().manual_quality_reviews:
            reviews = dict(self.sudo().manual_quality_reviews)
            for line in new_lines:
                reviews.pop(str(line.id), None)
            self.sudo().write({'manual_quality_reviews': reviews})
        return super()._add_stage_active_lines(production_lines)


class FurnitureBatchManualQuality(models.Model):
    _inherit = 'furniture.mrp.stage.product.batch'

    def _preflight_finish(self):
        result = super()._preflight_finish()
        for production, lines in self._stage_lines_by_production():
            production._stage_order_record(self.stage_code)._require_manual_quality(lines)
        return result

    def _dashboard_payload(self):
        result = super()._dashboard_payload()
        states = [
            production._stage_order_record(self.stage_code)._manual_quality_state(lines)
            for production, lines in self._stage_lines_by_production()
            if production._stage_order_record(self.stage_code)
        ]
        result.update({
            'can_review_quality': result['state'] == 'in_progress',
            'manual_quality_state': _combined_state(states),
            'quality_ready': bool(states) and all(state == 'pass' for state in states),
        })
        return result


class FurnitureProductionManualQuality(models.Model):
    _inherit = 'furniture.mrp.production'

    handoff_quality_reviews = fields.Json(
        string='فحص جودة الكسوة عند التغليف', default=dict, readonly=True, copy=False,
    )

    def write(self, vals):
        if 'handoff_quality_reviews' in vals and not self.env.su:
            raise AccessError(_('سجّل فحص الكسوة من أزرار الجودة فقط.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            self.env.context.get('default_handoff_quality_reviews')
            or any(vals.get('handoff_quality_reviews') for vals in vals_list)
        ):
            raise AccessError(_('لا يمكن إنشاء موافقات جودة مسبقة.'))
        return super().create(vals_list)

    @api.model
    def action_stage_dashboard_review_product_batch_quality(
        self, batch_token=False, stage_code=False, decision=False,
    ):
        _decision_value(decision)
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(batch_token, stage_code)
        batch._advisory_lock_token(batch.token)
        batch._lock_operation()
        batch._validate_member_snapshot()
        batch._check_release_scope()
        if batch._effective_state() != 'in_progress':
            raise UserError(_('دفعة المنتج ليست قيد التشغيل.'))
        for production, lines in batch._stage_lines_by_production():
            stage = production._stage_order_record(stage_code)
            stage._record_manual_quality(lines, decision)
        return {'reviewed': True, 'decision': decision}

    def action_stage_dashboard_review_order_product_quality(
        self, production_line_ids, stage_code, decision,
    ):
        self.ensure_one()
        _decision_value(decision)
        stage = self._stage_dashboard_validate_order_action(stage_code)
        if not stage:
            raise UserError(_('المرحلة لم تبدأ.'))
        requested_ids = set(production_line_ids or [])
        lines = stage.sudo()._manual_quality_active_lines().filtered(
            lambda line: line.id in requested_ids
        )
        if not lines or set(lines.ids) != requested_ids:
            raise AccessError(_('المنتجات المختارة لا تخص التشغيل الحالي.'))
        stage.sudo()._record_manual_quality(lines, decision)
        return {'reviewed': True, 'decision': decision}

    def action_stage_dashboard_finish_order_stage(self, stage_code):
        stage = self._stage_dashboard_validate_order_action(stage_code)
        if stage:
            stage.invalidate_recordset(['manual_quality_reviews'])
            stage._require_manual_quality(stage.sudo()._manual_quality_active_lines())
        return super().action_stage_dashboard_finish_order_stage(stage_code)

    def action_stage_dashboard_finish_batch(self, stage_code):
        self.ensure_one()
        self._stage_dashboard_check_stage_access(stage_code, batch_only=True)
        self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE', [self.id])
        stage = self._stage_dashboard_stage_order(stage_code)
        if stage:
            stage.invalidate_recordset(['manual_quality_reviews'])
            stage._require_manual_quality(stage.sudo()._manual_quality_active_lines())
        return super().action_stage_dashboard_finish_batch(stage_code)

    def _stage_dashboard_order_payload(
        self, production, stage_code, stage_label, stage_order, metrics,
        line_buckets, include_order_supervisor_details=False,
        include_order_display_details=False,
    ):
        result = super()._stage_dashboard_order_payload(
            production, stage_code, stage_label, stage_order, metrics,
            line_buckets, include_order_supervisor_details=include_order_supervisor_details,
            include_order_display_details=include_order_display_details,
        )
        stage = stage_order.sudo() if stage_order else False
        active = stage._manual_quality_active_lines() if stage else self.env['furniture.mrp.production.line']
        result['quality_ready'] = bool(
            stage_code == 'upholstery'
            or (stage and stage._manual_quality_state(active) == 'pass')
        )
        for product in result['product_lines']:
            lines = active.filtered(lambda line: line.id in product['production_line_ids'])
            product.update({
                'quality_production_line_ids': lines.ids,
                'can_review_quality': bool(lines and stage_code != 'upholstery' and stage.state in ('in_progress', 'quality_check')),
                'manual_quality_state': stage._manual_quality_state(lines) if lines else 'pending',
            })
            if stage_code == 'packaging':
                startable_ids = product['startable_production_line_ids']
                ready_lines = production.sudo().production_line_ids.filtered(
                    lambda line: line.id in startable_ids
                    and production._packaging_upholstery_quality_ready(line)
                )
                product['startable_production_line_ids'] = ready_lines.ids
                product['can_start_stage'] = bool(ready_lines)
        if stage_code == 'packaging':
            result['can_start_stage'] = any(
                product['can_start_stage'] for product in result['product_lines']
            )
        return result

    def _incoming_upholstery_quality_rows(self, stage_handoff=False, include_accepted=False):
        self.ensure_one()
        rows = []
        if stage_handoff:
            states = ('pending', 'accepted') if include_accepted else ('pending',)
            if (
                (stage_handoff.source_stage, stage_handoff.target_stage) != ('upholstery', 'packaging')
                or stage_handoff.state not in states
            ):
                return rows
            for line in stage_handoff.production_line_ids.sorted('id'):
                moves = stage_handoff.move_ids.filtered(lambda move: move.furniture_source_production_line_id == line)
                rows.append({
                    'key': 'stage:%s:%s' % (stage_handoff.id, line.id),
                    'production_line_id': line.id,
                    'product_name': line.product_id.display_name, 'quantity': line.product_qty,
                    'signature': [line.product_id.id, line.product_qty, sorted(moves.ids)],
                })
        elif self._furniture_handoff_target_stage()[0] == 'packaging':
            states = ('reserved', 'consumed') if include_accepted else ('reserved',)
            for handoff in self.upstream_handoff_ids.filtered(lambda row: row.role == 'upholstery' and row.state in states).sorted('id'):
                rows.append({
                    'key': 'lane:%s' % handoff.id,
                    'production_line_id': handoff.downstream_line_id.id,
                    'product_name': handoff.downstream_line_id.product_id.display_name,
                    'quantity': handoff.quantity,
                    'signature': [handoff.output_id.id, handoff.quantity, handoff.transfer_move_id.id],
                })
        reviews = self.handoff_quality_reviews or {}
        for row in rows:
            row['state'] = _review_state(reviews.get(row['key']), row['signature'])
        return rows

    def _packaging_upholstery_quality_ready(self, production_lines=False):
        """Retain the exact incoming QC requirement after stock acceptance."""
        self.ensure_one()
        production = self.sudo()
        lines = (
            production.production_line_ids if production_lines is False
            else production_lines.sudo()
        ).filtered(lambda line: line.active and line.use_packaging)
        if not lines or any(line.production_id != production for line in lines):
            return False
        rows = production._incoming_upholstery_quality_rows(include_accepted=True)
        for handoff in production.stage_transfer_handoff_ids.filtered(
            lambda row: row.source_stage == 'upholstery'
            and row.target_stage == 'packaging'
            and row.state in ('pending', 'accepted')
        ):
            rows.extend(production._incoming_upholstery_quality_rows(
                handoff, include_accepted=True,
            ))
        for line in lines:
            line_rows = [row for row in rows if row['production_line_id'] == line.id]
            if not line_rows or any(row['state'] != 'pass' for row in line_rows):
                return False
        return True

    def _require_packaging_upholstery_quality(self, production_lines=False):
        self.ensure_one()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset(['handoff_quality_reviews'])
        self.sudo().upstream_handoff_ids.invalidate_recordset()
        self.sudo().stage_transfer_handoff_ids.invalidate_recordset()
        if not self._packaging_upholstery_quality_ready(production_lines):
            raise UserError(_(
                'لا يمكن بدء التغليف قبل قبول جودة الكسوة لكل منتج '
                'يدويًا من مشرف التغليف.'
            ))
        return True

    def _move_stage_materials_for_lines(self, stage_code, production_lines):
        if stage_code == 'packaging' and production_lines:
            self._require_packaging_upholstery_quality(production_lines)
        return super()._move_stage_materials_for_lines(stage_code, production_lines)

    def _start_first_stage_lines_from_stock(self, stage_order, stage_code, wizard_lines):
        if stage_code == 'packaging':
            lines = self.env['furniture.mrp.production.line']
            for row in wizard_lines.filtered(lambda item: item.selected and item.production_line_id):
                for line, quantity in row._technical_qty_allocations():
                    if quantity > 0:
                        lines |= line
            if lines:
                self._require_packaging_upholstery_quality(lines)
        return super()._start_first_stage_lines_from_stock(stage_order, stage_code, wizard_lines)

    def _lock_incoming_quality(self, stage_handoff_id=False):
        self.ensure_one()
        if self.company_id not in self.env.companies:
            raise AccessError(_('التحويلة لا تتبع الشركة الحالية.'))
        self.env.cr.execute('SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['handoff_quality_reviews', 'upstream_handoff_ids'])
        if stage_handoff_id:
            handoff = self.env['furniture.mrp.stage.transfer.handoff'].sudo().browse(stage_handoff_id).exists()
            if not handoff or handoff.production_id != self:
                raise AccessError(_('التحويلة لا تخص أمر الإنتاج.'))
            handoff._check_acceptance_access(self.env.user)
            self.env.cr.execute('SELECT id FROM furniture_mrp_stage_transfer_handoff WHERE id = %s FOR UPDATE', [handoff.id])
            handoff.invalidate_recordset()
            return handoff
        self._furniture_check_handoff_acceptance_access(self.env.user)
        self.sudo().upstream_handoff_ids.invalidate_recordset()
        return False

    def action_review_handoff_quality(self, review_key, decision, stage_handoff_id=False):
        self.ensure_one()
        _decision_value(decision)
        self._stage_dashboard_check_stage_access('packaging')
        handoff = self._lock_incoming_quality(stage_handoff_id)
        rows = self.sudo()._incoming_upholstery_quality_rows(handoff)
        row = next((row for row in rows if row['key'] == review_key), False)
        if not row:
            raise AccessError(_('الصنف لا يخص تحويل كسوة منتظرًا إلى التغليف.'))
        reviews = dict(self.sudo().handoff_quality_reviews or {})
        reviews[review_key] = {
            'state': decision, 'signature': row['signature'], 'user_id': self.env.uid,
            'at': fields.Datetime.to_string(fields.Datetime.now()),
        }
        self.sudo().write({'handoff_quality_reviews': reviews})
        self.sudo().message_post(body=_('فحص الكسوة عند التغليف — %(product)s: %(decision)s. لم يتم إدخال المنتج إلى الصالة.') % {
            'product': row['product_name'], 'decision': _('مقبول') if decision == 'pass' else _('مرفوض'),
        })
        return {'reviewed': True, 'decision': decision}

    def action_accept_handoff_transfer(self, stage_handoff_id=False):
        for production in self:
            handoff = production._lock_incoming_quality(stage_handoff_id)
            rows = production.sudo()._incoming_upholstery_quality_rows(handoff)
            if any(row['state'] != 'pass' for row in rows):
                raise UserError(_('اقبل جودة الكسوة يدويًا من لوحة التغليف أولًا، ثم اضغط قبول التحويل.'))
        return super().action_accept_handoff_transfer(stage_handoff_id=stage_handoff_id)

    @api.model
    def _furniture_stage_dashboard_pending_handoffs(self, stage_code):
        result = super()._furniture_stage_dashboard_pending_handoffs(stage_code)
        for item in result:
            production = self.sudo().browse(item['production_id'])
            handoff = self.env['furniture.mrp.stage.transfer.handoff'].sudo().browse(item['handoff_id']) if item['kind'] == 'stage' else False
            rows = production._incoming_upholstery_quality_rows(handoff)
            item['quality_rows'] = [{key: value for key, value in row.items() if key != 'signature'} for row in rows]
            item['quality_ready'] = all(row['state'] == 'pass' for row in rows)
        return result


class FurniturePackagingIncomingQuality(models.Model):
    _inherit = 'furniture.mrp.packaging'

    def action_start(self):
        self._check_stage_operation_access()
        for stage in self:
            lines = stage._manual_quality_active_lines()
            stage.production_order_id._require_packaging_upholstery_quality(
                lines or False,
            )
        return super().action_start()
