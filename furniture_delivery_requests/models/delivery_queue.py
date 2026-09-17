from datetime import timedelta
from odoo import api, fields, models, _
from odoo.exceptions import UserError


def next_delivery_week(today):
    start = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    return start, start + timedelta(days=6)


class DeliveryQueue(models.Model):
    _inherit = 'furniture.mrp.future.order'

    next_week_label = fields.Char(compute='_compute_next_week', string='موعد قريب')

    @api.depends('delivery_date', 'state')
    @api.depends_context('tz', 'uid')
    def _compute_next_week(self):
        start, end = next_delivery_week(fields.Date.context_today(self))
        for order in self:
            order.next_week_label = _('الأسبوع القادم') if order.state == 'pending' and order.delivery_date and start <= order.delivery_date <= end else False

    def _batch_decide(self, mode):
        self._check_manager()
        if not self:
            raise UserError(_('حدد طلبًا واحدًا على الأقل.'))
        # Lock in a stable order; any invalid request rolls back the whole batch.
        orders = self.sorted('id')
        for order in orders:
            order._lock_pending_order()
        for order in orders:
            if mode == 'reserve':
                order.action_reserve_from_finished()
            else:
                order.action_produce_shortage()
        return self._reload_notification(_('تم اعتماد الطلبات'), _('تم اعتماد %s طلب.') % len(orders), 'success')

    def action_batch_reserve(self):
        return self._batch_decide('reserve')

    def action_batch_produce(self):
        return self._batch_decide('produce')

    def action_delete_cancelled(self):
        self._check_manager()
        if any(order.state != 'cancelled' for order in self):
            raise UserError(_('يمكن حذف الطلبات الملغاة فقط. ألغِ الطلب أولًا.'))
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
