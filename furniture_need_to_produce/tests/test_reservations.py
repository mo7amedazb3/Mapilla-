"""Exact plan allocation lifecycle on isolated stock-ledger fixtures."""
from odoo.tests import TransactionCase, tagged

from . import test_production_plan


@tagged('post_install', '-at_install')
class TestNeedReservations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.Piece = cls.env['furniture.need.to.produce']
        cls.Input = cls.env['furniture.need.to.produce.input']
        cls.Production = cls.env['furniture.mrp.production']
        for model in ('furniture.mrp.stage.replenishment.rule',
                      'furniture.mrp.final.replenishment.rule'):
            cls.env[model].sudo().search([('company_id', '=', cls.company.id)]).write({
                'min_qty': 0, 'max_qty': 0,
            })
        cls.env['ir.config_parameter'].sudo().set_param('furniture_mrp.mps_enabled', 'False')
        cls.product = cls.env['product.product'].create({
            'name': 'NTP reservation lifecycle product',
            'type': 'consu', 'is_storable': True,
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id,
        })
        cls.model = cls.env['furniture.product.model'].create({
            'name': 'NTP reservation lifecycle model',
        })
        cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1, 'product_uom_id': cls.unit.id,
            'company_id': cls.company.id, 'type': 'normal',
            'furniture_product_id': cls.product.id,
            'furniture_recipe_model_id': cls.model.id,
            'furniture_width_cm': 100, 'furniture_depth_cm': 80,
            'furniture_height_cm': 70,
            'use_priming': True, 'use_carpentry': True, 'use_bases': True,
        })
        cls.bom = cls.env['mrp.bom']._find_furniture_production_recipe(
            cls.product, model=cls.model, company=cls.company,
        )

    def _source_and_claim(self, kind='incoming', output=False, source=False):
        if source:
            source_line = source.production_line_ids.ensure_one()
        else:
            source, source_line = test_production_plan.TestNeedToProduce._new_production(self, 'frame', 3)
        target, _target_line = test_production_plan.TestNeedToProduce._new_production(self, 'bases', 3)
        piece = self.Piece.sudo().create({
            'name': 'NTP reservation %s' % self._testMethodName,
            'company_id': self.company.id, 'product_id': self.product.id,
            'furniture_model_id': self.model.id, 'bom_id': self.bom.id,
            'target_lane': 'bases', 'state': 'approved',
        })
        stage = self.env['furniture.need.to.produce.stage'].sudo().create({
            'piece_id': piece.id, 'plan_key': 'bases', 'lane': 'bases',
            'stage_code': 'bases', 'stage_codes': ['bases'], 'quantity': 3,
            'production_id': target.id,
        })
        target.sudo().write({'need_to_produce_stage_id': stage.id})
        claim = self.Input.sudo().create({
            'stage_id': stage.id, 'kind': kind, 'role': 'frame', 'quantity': 3,
            'source_line_id': source_line.id if kind == 'incoming' else False,
            'output_id': output.id if output else False,
        })
        return source, target, claim

    def _receipt(self, source, quantity):
        line = source.production_line_ids.ensure_one()
        final = source._furniture_line_final_product(line)
        wip = self.env['product.product']._furniture_get_or_create_lane_wip_product(
            self.company, 'frame', final, self.model,
        )
        return source._create_internal_move(
            source._get_production_location(), source._stage_storage_location('carpentry'),
            'NTP partial physical receipt', move_type='finished_product', product=wip,
            quantity=quantity, uom=wip.uom_id, source_production_line=line, price_unit=2,
        )

    def _live(self, claim):
        claim.invalidate_recordset(['handoff_ids'])
        return claim.handoff_ids.filtered(lambda h: h.state in ('reserved', 'consumed'))

    def _assert_no_unclaimed_frame(self, claim):
        preview = self.Piece.new({
            'company_id': self.company.id, 'product_id': self.product.id,
            'furniture_model_id': self.model.id, 'bom_id': self.bom.id,
        })
        pools = preview._supply_pools(ignore_pieces=self.Piece.browse())
        self.assertEqual(sum(row['quantity'] for row in pools.get('frame', [])), 0)

    def test_partial_physical_availability_is_reserved_and_retried_without_duplicates(self):
        source, _line = test_production_plan.TestNeedToProduce._new_production(self, 'frame', 3)
        # Complete a genuine three-unit receipt first so quantity, valuation,
        # and FIFO all agree. A separate ordinary warehouse transfer then
        # reserves two units temporarily, leaving one physically available.
        self._receipt(source, 3)
        output = source._ensure_lane_outputs().ensure_one()
        destination = self.env['stock.location'].create({
            'name': 'NTP temporary transfer destination',
            'usage': 'internal', 'company_id': self.company.id,
        })
        temporary = self.env['stock.move'].create({
            'name': 'NTP temporary warehouse reservation',
            'product_id': output.wip_product_id.id,
            'product_uom': output.uom_id.id, 'product_uom_qty': 2,
            'location_id': output.source_location_id.id,
            'location_dest_id': destination.id, 'company_id': self.company.id,
        })
        temporary._action_confirm(merge=False)
        temporary._action_assign()
        self.assertEqual(temporary.state, 'assigned')
        output._furniture_refresh_matching_groups()
        self.assertEqual(output.physical_available_qty, 1)

        source, target, claim = self._source_and_claim(source=source)
        self._assert_no_unclaimed_frame(claim)
        self.Input._release_available_inputs()
        self.assertEqual(sum(self._live(claim).mapped('quantity')), 1)
        self.assertEqual(output.reserved_qty, 1)
        self.assertFalse(target._furniture_handoffs_cover_lines())
        self.assertFalse(target._furniture_accepted_handoffs_cover_lines())
        self._assert_no_unclaimed_frame(claim)
        first = claim.handoff_ids
        self.Input._release_available_inputs()
        self.assertEqual(claim.handoff_ids, first)

        temporary._action_cancel()
        self.assertEqual(temporary.state, 'cancel')
        output._furniture_refresh_matching_groups()
        self.Input._release_available_inputs()
        self.assertEqual(sum(self._live(claim).mapped('quantity')), 3)
        self.assertEqual(len(self._live(claim)), 2)
        self.assertEqual(set(self._live(claim).transfer_move_id.mapped('state')), {'assigned'})
        self.assertTrue(target._furniture_handoffs_cover_lines())
        self.assertFalse(target._furniture_accepted_handoffs_cover_lines())
        self._assert_no_unclaimed_frame(claim)
        complete = claim.handoff_ids
        self.Input._release_available_inputs()
        self.assertEqual(claim.handoff_ids, complete)

    def test_cancelled_reservation_retries_exact_source_without_exposing_stock(self):
        source, _target, claim = self._source_and_claim()
        self._receipt(source, 3)
        output = source._ensure_lane_outputs().ensure_one()
        old = self._live(claim).ensure_one()
        self.assertEqual(old.quantity, 3)
        old.transfer_move_id.with_context(
            furniture_internal_cancel_lane_handoff=True,
        )._action_cancel()
        old._furniture_internal_write({'state': 'cancelled'})
        output._furniture_refresh_matching_groups()
        self.assertEqual(output.physical_available_qty, 3)
        self._assert_no_unclaimed_frame(claim)
        self.Input._release_available_inputs()
        new = self._live(claim).ensure_one()
        self.assertNotEqual(old, new)
        self.assertEqual(new.output_id, output)
        self.assertEqual(new.quantity, 3)
        self.assertEqual(set(claim.handoff_ids.mapped('state')), {'cancelled', 'reserved'})
        self.Input._release_available_inputs()
        self.assertEqual(len(claim.handoff_ids), 2)

    def test_consumed_coverage_is_not_reserved_again(self):
        source, target, claim = self._source_and_claim()
        self._receipt(source, 3)
        source._ensure_lane_outputs()
        target._furniture_consume_required_handoffs()
        old = claim.handoff_ids
        self.assertEqual(set(old.mapped('state')), {'consumed'})
        self.Input._release_available_inputs()
        self.assertEqual(claim.handoff_ids, old)
        self.assertEqual(sum(self._live(claim).mapped('quantity')), 3)

    def test_cancelled_downstream_does_not_reserve_on_later_source_arrival(self):
        source, target, claim = self._source_and_claim()
        target.action_cancel()
        self._receipt(source, 3)
        source._ensure_lane_outputs()
        self.Input._release_available_inputs()
        self.assertFalse(claim.handoff_ids)
