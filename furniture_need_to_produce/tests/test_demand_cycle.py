from odoo import fields
from odoo.exceptions import UserError, ValidationError
from .test_production_plan import TestNeedToProduce


class TestDemandCycle(TestNeedToProduce):
    def _demand(self, qty=2):
        self.env['ir.config_parameter'].sudo().set_param('furniture_need_to_produce.parallel_finish_v1', 'True')
        self.env['ir.config_parameter'].sudo().set_param('furniture_need_to_produce.group_compatible_approvals', 'True')
        order, line = self._new_production('demand', qty, state='draft')
        return order.with_user(self.manager), line

    def test_direct_demand_cycle(self):
        order, line = self._demand()
        buyer = self.env['res.partner'].create({'name': 'Demand buyer', 'is_company': True})
        consumer = self.env['res.partner'].create({'name': 'Demand beneficiary'})
        order.write({'buyer_partner_id': buyer.id, 'beneficiary_partner_id': consumer.id})
        line.write({'width_cm': 120})
        order.action_confirm()
        self.assertEqual(order.production_lane, 'demand')
        self.assertEqual(order.state, 'confirmed')
        self.assertEqual(order._required_stage_codes(), [])
        self.assertEqual(line._selected_stage_codes(), [])
        self.assertFalse(order.material_line_ids)
        children = order.demand_child_ids
        self.assertEqual(len(children), 6)
        self.assertEqual(set(children.mapped('production_lane')), {'frame', 'finish', 'tailoring', 'painting', 'upholstery', 'packaging'})
        self.assertTrue(all(o.state == 'confirmed' and o.product_qty == 2 for o in children))
        self.assertTrue(all(o.width_cm == 120 for o in children))
        self.assertEqual(children.buyer_partner_id, buyer)
        self.assertEqual(children.beneficiary_partner_id, consumer)
        self.assertEqual(len(order.demand_piece_ids), 2)
        for piece in order.demand_piece_ids:
            stages = {s.lane: s for s in piece.stage_ids}
            self.assertEqual(set(stages['upholstery'].input_ids.mapped('role')), {'finish', 'tailoring'})
            self.assertEqual(set(stages['packaging'].input_ids.mapped('role')), {'upholstery', 'painting'})
            self.assertEqual(stages['finish'].stage_codes, ['bases', 'finishing'])
        with self.assertRaises(UserError):
            children.filtered(lambda o: o.production_lane == 'upholstery')._furniture_reserve_required_handoffs()
        with self.assertRaises(UserError):
            children.filtered(lambda o: o.production_lane == 'packaging')._furniture_reserve_required_handoffs()
        order.action_confirm()
        self.assertEqual(order.demand_child_ids, children)
        with self.assertRaises(UserError):
            line.write({'product_qty': 4})
        with self.assertRaises(UserError):
            order.action_reset_to_draft()

    def test_cancel_direct_demand(self):
        order, line = self._demand(1)
        order.action_confirm()
        children = order.demand_child_ids
        order.action_cancel()
        self.assertEqual(order.state, 'cancelled')
        self.assertFalse(children.exists())
        self.assertFalse(order.demand_piece_ids)
        order.action_reset_to_draft()
        order.action_confirm()
        self.assertEqual(len(order.demand_child_ids), 6)

    def test_fraction_not_rounded(self):
        order, line = self._demand(1.5)
        with self.assertRaises(ValidationError):
            order.action_confirm()
        self.assertFalse(order.demand_child_ids)

    def test_existing_legacy_unchanged(self):
        old, _line = self._new_production('legacy', 1, state='confirmed')
        with self.assertRaises(UserError):
            old.action_confirm()
        self.assertEqual(old.production_lane, 'legacy')
        self.assertFalse(old.demand_child_ids)

    def test_delivery_shortage_uses_cycle(self):
        self.env['ir.config_parameter'].sudo().set_param('furniture_need_to_produce.parallel_finish_v1', 'True')
        buyer = self.env['res.partner'].create({'name': 'Delivery buyer', 'is_company': True})
        order = self.env['furniture.mrp.future.order'].create({
            'company_id': self.company.id, 'buyer_partner_id': buyer.id,
            'beneficiary_partner_id': buyer.id, 'delivery_date': fields.Date.today(),
            'line_ids': [(0, 0, {'product_id': self.product.id, 'furniture_model_id': self.model.id, 'quantity': 1})],
        })
        order.action_produce_shortage()
        self.assertEqual(order.state, 'production')
        sources = order.production_ids.filtered(lambda p: p.production_lane == 'demand')
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources.demand_child_ids), 6)
        self.assertEqual(sources.demand_child_ids.future_order_id, order)
        self.assertEqual(sources.demand_child_ids.future_order_line_id, order.line_ids)
        with self.assertRaises(UserError):
            order.action_produce_shortage()

    def test_existing_frame_supply_allocated_once(self):
        self._stock('frame', 1)
        order, _line = self._demand(1)
        order.action_confirm()
        self.assertEqual(len(order.demand_child_ids), 5)
        self.assertNotIn('frame', order.demand_child_ids.mapped('production_lane'))
        claims = order.demand_piece_ids.stage_ids.input_ids.filtered(lambda i: i.kind == 'stock')
        self.assertEqual(len(claims), 1)
        self.assertEqual(sum(claims.handoff_ids.mapped('quantity')), 1)

    def test_new_order_defaults_to_cycle(self):
        order = self.Production.create({'company_id': self.company.id})
        self.assertEqual(order.production_lane, 'demand')
        self.assertEqual(order.cycle_label, 'دورة المنتج النهائي')
        order.unlink()

    def test_parent_tracks_child_completion(self):
        order, _line = self._demand(1)
        order.action_confirm()
        children = order.demand_child_ids
        children[:1].sudo().write({'state': 'in_production'})
        self.assertEqual(order.state, 'in_production')
        children.sudo().write({'state': 'done'})
        self.assertEqual(order.state, 'done')
