# -*- coding: utf-8 -*-

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .mrp_production_order import FURNITURE_STAGE_SELECTION


class FurnitureMrpStageTransferHandoff(models.Model):
    _name = 'furniture.mrp.stage.transfer.handoff'
    _description = 'تحويل منتج بين مرحلتين بانتظار قبول المشرف'
    _order = 'id desc'

    production_id = fields.Many2one(
        'furniture.mrp.production', required=True, readonly=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='production_id.company_id', store=True, readonly=True,
        index=True,
    )
    source_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True, index=True,
    )
    target_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True, index=True,
    )
    production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furniture_mrp_stage_transfer_handoff_line_rel',
        'handoff_id', 'production_line_id',
        string='أصناف التحويل', readonly=True, copy=False,
    )
    move_ids = fields.One2many(
        'stock.move', 'furniture_stage_transfer_handoff_id',
        string='حركات التحويل', readonly=True, copy=False,
    )
    state = fields.Selection(
        [
            ('pending', 'بانتظار القبول'),
            ('accepted', 'مقبول'),
            ('cancelled', 'ملغي'),
        ],
        required=True, default='pending', readonly=True, copy=False,
        index=True,
    )
    notification_sent_at = fields.Datetime(readonly=True, copy=False)
    notification_dismissed_user_ids = fields.Many2many(
        'res.users',
        'furniture_mrp_stage_handoff_dismissed_user_rel',
        'handoff_id', 'user_id',
        string='مستخدمون أغلقوا التنبيه', readonly=True, copy=False,
    )
    accepted_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False,
    )
    accepted_at = fields.Datetime(readonly=True, copy=False)

    def _supervisor_users(self):
        self.ensure_one()
        return self.production_id._furniture_stage_supervisor_users(
            self.target_stage,
        )

    def _check_acceptance_access(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if user not in self._supervisor_users():
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(
                self.target_stage, self.target_stage,
            )
            raise AccessError(_(
                'قبول التحويل متاح فقط لمشرف مرحلة %s.'
            ) % stage_label)
        return True

    def _notification_payload(self):
        self.ensure_one()
        labels = dict(FURNITURE_STAGE_SELECTION)
        product_rows = [
            '%s × %s' % (
                line.product_id.display_name,
                self.production_id._format_dimension_value(
                    line.product_qty,
                ),
            )
            for line in self.production_line_ids.sorted(
                lambda line: (line.sequence, line.id)
            )
        ]
        source_label = labels.get(self.source_stage, self.source_stage)
        target_label = labels.get(self.target_stage, self.target_stage)
        return {
            'title': _('تحويل منتظر إلى %s') % target_label,
            'message': _(
                'هيتحول لك من %(source)s إلى %(target)s: %(products)s. '
                'لن تدخل الكميات صالة %(target)s قبل الضغط على «قبول التحويل». '
                'إغلاق التنبيه يخفيه فقط ولا ينفذ التحويل.'
            ) % {
                'source': source_label,
                'target': target_label,
                'products': '، '.join(product_rows),
            },
            'type': 'warning',
            'sticky': True,
            'refresh_model': self.production_id._name,
            'refresh_res_id': self.production_id.id,
            'play_sound': True,
            'handoff_event': 'pending',
            'handoff_kind': 'stage',
            'handoff_notification_key': 'stage:%s' % self.id,
            'handoff_id': self.id,
            'handoff_production_id': self.production_id.id,
            'handoff_stage_code': self.target_stage,
            'handoff_stage_label': target_label,
        }

    def _notify_pending(self):
        self.ensure_one()
        if self.state != 'pending' or self.notification_sent_at:
            return False
        users = self._supervisor_users()
        if not users:
            return False
        payload = self._notification_payload()
        self.env['furniture.mrp.store.request']._send_bus_notification(
            users,
            payload['title'],
            payload['message'],
            notification_type='warning',
            sticky=True,
            refresh_model=self.production_id._name,
            refresh_res_id=self.production_id.id,
            play_sound=True,
            extra_payload={
                key: value
                for key, value in payload.items()
                if key.startswith('handoff_')
            },
        )
        self.sudo().write({
            'notification_sent_at': fields.Datetime.now(),
            'notification_dismissed_user_ids': [Command.clear()],
        })
        return True

    @api.model
    def _create_pending_transfer(
        self, production, source_stage, target_stage, move_specs,
    ):
        production.ensure_one()
        move_specs = list(move_specs or [])
        lines = self.env['furniture.mrp.production.line']
        for spec in move_specs:
            lines |= spec.get('source_production_line')
        if not lines:
            return self
        handoff = self.sudo().create({
            'production_id': production.id,
            'source_stage': source_stage,
            'target_stage': target_stage,
            'production_line_ids': [Command.set(lines.ids)],
        })
        created = production._create_internal_moves_batch(
            move_specs, defer_done=True,
        )
        moves = self.env['stock.move']
        for _spec, move in created:
            moves |= move
        moves.sudo().write({
            'furniture_stage_transfer_handoff_id': handoff.id,
        })
        handoff.invalidate_recordset(['move_ids'])
        handoff._notify_pending()
        production.sudo().message_post(body=_(
            '🔔 تم تجهيز تحويلة من %(source)s إلى %(target)s للأصناف: '
            '%(products)s. الحركة محجوزة ولن تُنفذ قبل قبول مشرف المرحلة.'
        ) % {
            'source': dict(FURNITURE_STAGE_SELECTION).get(
                source_stage, source_stage,
            ),
            'target': dict(FURNITURE_STAGE_SELECTION).get(
                target_stage, target_stage,
            ),
            'products': '، '.join(lines.mapped('product_id.display_name')),
        })
        return handoff

    def _dismiss_for_user(self, user=None):
        user = user or self.env.user
        for handoff in self.sudo().exists():
            handoff._check_acceptance_access(user)
            if handoff.state == 'pending':
                handoff.write({
                    'notification_dismissed_user_ids': [
                        Command.link(user.id),
                    ],
                })
        return True

    def _accept(self, user=None):
        user = user or self.env.user
        accepted_handoffs = self.browse()
        for source_handoff in self:
            handoff = source_handoff.sudo()
            handoff._check_acceptance_access(user)
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_stage_transfer_handoff '
                'WHERE id = %s FOR UPDATE',
                [handoff.id],
            )
            handoff.invalidate_recordset(['state', 'move_ids'])
            if handoff.state == 'accepted':
                continue
            if handoff.state != 'pending':
                raise UserError(_('التحويلة لم تعد متاحة للقبول.'))
            moves = handoff.move_ids.sorted('id').sudo()
            pending_moves = moves.filtered(lambda move: move.state != 'done')
            cancelled = pending_moves.filtered(
                lambda move: move.state == 'cancel'
            )
            if cancelled:
                raise UserError(_('إحدى حركات التحويلة ملغاة.'))
            if pending_moves:
                pending_moves._action_assign()
                unavailable = pending_moves.filtered(
                    lambda move: move.state != 'assigned'
                )
                if unavailable:
                    raise UserError(_(
                        'رصيد التحويلة المحجوز لم يعد متاحًا بالكامل.'
                    ))
                for move in pending_moves:
                    move.quantity = move.product_uom_qty
                pending_moves.write({'picked': True})
                pending_moves._action_done()
            accepted_at = fields.Datetime.now()
            handoff.write({
                'state': 'accepted',
                'accepted_by_id': user.id,
                'accepted_at': accepted_at,
                'notification_dismissed_user_ids': [Command.clear()],
            })
            accepted_handoffs |= handoff
            production = handoff.production_id
            production._reopen_stage_order_for_pending_start(
                production._stage_order_record(handoff.target_stage),
                handoff.target_stage,
            )
            message = _(
                'قبل %(user)s التحويل من %(source)s إلى %(target)s. '
                'أصبحت الأصناف جاهزة داخل صالة المرحلة المستقبلة.'
            ) % {
                'user': user.display_name,
                'source': dict(FURNITURE_STAGE_SELECTION).get(
                    handoff.source_stage, handoff.source_stage,
                ),
                'target': dict(FURNITURE_STAGE_SELECTION).get(
                    handoff.target_stage, handoff.target_stage,
                ),
            }
            production.message_post(body='✅ %s' % message)
            self.env['furniture.mrp.store.request']._send_bus_notification(
                handoff._supervisor_users() | user.sudo(),
                _('تم قبول تحويل المرحلة'),
                message,
                notification_type='success',
                refresh_model=production._name,
                refresh_res_id=production.id,
                extra_payload={
                    'handoff_event': 'resolved',
                    'handoff_kind': 'stage',
                    'handoff_notification_key': 'stage:%s' % handoff.id,
                    'handoff_id': handoff.id,
                    'handoff_production_id': production.id,
                },
            )
        return accepted_handoffs


class FurnitureMrpProductionStageTransferHandoff(models.Model):
    _inherit = 'furniture.mrp.production'

    stage_transfer_handoff_ids = fields.One2many(
        'furniture.mrp.stage.transfer.handoff', 'production_id',
        string='تحويلات المراحل الداخلية', readonly=True, copy=False,
    )


class StockMoveStageTransferHandoff(models.Model):
    _inherit = 'stock.move'

    furniture_stage_transfer_handoff_id = fields.Many2one(
        'furniture.mrp.stage.transfer.handoff',
        string='تحويلة مرحلة معلقة', readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
