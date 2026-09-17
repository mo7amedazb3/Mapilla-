from unittest.mock import patch
from types import SimpleNamespace

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestNeedIndependentBuffers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env['furniture.mrp.stage.replenishment.rule']
        cls.Piece = cls.env['furniture.need.to.produce']
        cls.product = cls.env['product.product'].create({
            'name': 'Independent buffer chaise', 'type': 'consu', 'is_storable': True,
        })
        cls.model = cls.env['furniture.product.model'].create({'name': 'Independent buffer model'})
        values = {
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1, 'product_uom_id': cls.product.uom_id.id,
            'company_id': cls.env.company.id, 'type': 'normal',
            'furniture_product_id': cls.product.id,
            'furniture_recipe_model_id': cls.model.id,
            'furniture_width_cm': 100, 'furniture_depth_cm': 80, 'furniture_height_cm': 70,
            'use_priming': True, 'use_carpentry': True, 'use_bases': True,
            'use_finishing': True, 'use_tailoring': False, 'use_painting': False,
            'use_upholstery': False, 'use_packaging': False, 'use_sewing': False,
        }
        cls.env['mrp.bom'].create(values)
        cls.recipe = cls.env['mrp.bom']._find_furniture_production_recipe(
            cls.product, model=cls.model, company=cls.env.company,
        )
        synced = cls.Rule._stage_replenishment_sync_company(cls.env.company)
        cls.rules = synced.filtered(lambda rule: (
            rule.product_id == cls.product and rule.furniture_model_id == cls.model
        ))
        cls.bases = cls.rules.filtered(lambda rule: rule.stage_code == 'bases')
        cls.finishing = cls.rules.filtered(lambda rule: rule.stage_code == 'finishing')
        (cls.bases | cls.finishing)._stage_replenishment_internal_write({'min_qty': 0, 'max_qty': 3})

    def _metrics(self, pools):
        with patch.object(type(self.Piece), '_supply_pools', return_value=pools):
            return self.Rule._stage_replenishment_metric_map(self.bases | self.finishing)

    def test_independent_rule_controllers_and_routes(self):
        self.assertEqual(len(self.bases), 1)
        self.assertEqual(len(self.finishing), 1)
        self.assertEqual(self.Rule._stage_replenishment_rule_lane(self.bases), 'bases')
        self.assertEqual(self.Rule._stage_replenishment_rule_lane(self.finishing), 'preparation')
        self.assertEqual(self.Rule._stage_replenishment_lane_rule_stages('bases'), ('bases',))
        self.assertEqual(self.Rule._stage_replenishment_lane_rule_stages('preparation'), ('finishing',))
        self.assertEqual(self.Rule._stage_replenishment_lane_route('bases', self.recipe), ('bases',))
        self.assertEqual(self.Rule._stage_replenishment_lane_route('preparation', self.recipe), ('finishing',))
        self.assertEqual(self.Rule._stage_replenishment_lane_route('finish', self.recipe), ('bases', 'finishing'))

    def test_bases_configuration_uses_unified_menu_only(self):
        shortcut = self.env.ref('furniture_need_to_produce.menu_need_to_produce_bases_minmax')
        unified = self.env.ref('furniture_stage_replenishment.menu_furniture_stage_replenishment_all')
        self.assertFalse(shortcut.active)
        self.assertTrue(unified.active)
        with patch.object(type(self.Piece), '_supply_pools', return_value={}):
            payload = self.Rule._stage_replenishment_dashboard_payload()
        tabs = [row for row in payload['stages'] if row['code'] == 'bases']
        self.assertEqual(len(tabs), 1)
        self.assertTrue(any(row['id'] == self.bases.id for row in payload['rows']))

    def test_frame_stock_never_counts_as_finished_bases_or_preparation(self):
        metrics = self._metrics({'frame': [{'kind': 'stock', 'source_id': 100, 'quantity': 99}]})
        for rule in self.bases | self.finishing:
            self.assertEqual(metrics[rule.id]['current_qty'], 0)
            self.assertEqual(metrics[rule.id]['forecast_qty'], 0)
            self.assertEqual(metrics[rule.id]['upstream_qty'], 0)
            self.assertEqual(metrics[rule.id]['qty_to_produce'], 3)

    def test_each_buffer_uses_only_its_exact_output_role(self):
        metrics = self._metrics({
            'bases': [{'kind': 'stock', 'source_id': 100, 'quantity': 3}],
            'finish': [],
        })
        self.assertEqual(metrics[self.bases.id]['current_qty'], 3)
        self.assertEqual(metrics[self.bases.id]['qty_to_produce'], 0)
        self.assertEqual(metrics[self.finishing.id]['current_qty'], 0)
        self.assertEqual(metrics[self.finishing.id]['qty_to_produce'], 3)

    def test_min_zero_max_positive_is_configured_and_visible(self):
        with patch.object(type(self.Piece), '_supply_pools', return_value={}):
            payload = self.Rule._stage_replenishment_dashboard_payload()
        bases = [row for row in payload['rows'] if row['id'] == self.bases.id]
        finishing = [row for row in payload['rows'] if row['id'] == self.finishing.id]
        self.assertEqual(len(bases), 1)
        self.assertEqual(bases[0]['stage'], 'القواعد')
        self.assertEqual(finishing[0]['stage'], 'التجهيز')
        self.assertEqual(finishing[0]['lane'], 'preparation')
        self.assertEqual(bases[0]['status'], 'to_produce')
        self.assertGreaterEqual(payload['summary']['configured'], 2)
        self.assertEqual(len([tab for tab in payload['stages'] if tab['code'] == 'bases']), 1)

    def test_approved_own_buffer_is_incoming_not_another_final_piece(self):
        def create_piece(buffer_rule=False):
            return self.Piece.sudo().create({
                'name': 'Approved buffer regression',
                'company_id': self.env.company.id,
                'product_id': self.product.id,
                'furniture_model_id': self.model.id,
                'bom_id': self.recipe.id,
                'buffer_rule_id': buffer_rule.id if buffer_rule else False,
                'target_lane': 'bases' if buffer_rule else 'packaging',
                'state': 'approved',
            })
        buffer_piece = create_piece(self.bases)
        final_piece = create_piece()
        Stage = self.env['furniture.need.to.produce.stage'].sudo()
        for piece, qty in ((buffer_piece, 1), (final_piece, 20)):
            stage = Stage.create({
                'piece_id': piece.id, 'sequence': 10, 'plan_key': 'bases-test',
                'lane': 'bases', 'stage_code': 'bases', 'stage_codes': ['bases'],
                'quantity': qty, 'estimated_hours': 2 * qty,
            })
            stage._create_production()
        # Incoming means a real, unclaimed source line. An approved stage
        # without an MO is not supply; a final piece's intermediate MO is not
        # a shareable buffer either.
        pools = buffer_piece._supply_pools(ignore_pieces=self.Piece.browse())
        self.assertEqual(sum(row['quantity'] for row in pools['bases']), 1)
        self.assertEqual(
            [row['source_id'] for row in pools['bases']],
            buffer_piece.stage_ids.production_id.production_line_ids.ids,
        )
        metrics = self.Rule._stage_replenishment_metric_map(self.bases | self.finishing)
        self.assertEqual(metrics[self.bases.id]['incoming_qty'], 1)
        self.assertEqual(metrics[self.bases.id]['qty_to_produce'], 2)
        self.assertEqual(metrics[self.finishing.id]['incoming_qty'], 0)
        self.assertEqual(metrics[self.finishing.id]['qty_to_produce'], 3)

    def test_stock_above_min_defers_own_buffer_without_hiding_final_demand(self):
        metrics = self._metrics({'bases': [{'kind': 'stock', 'source_id': 100, 'quantity': 1}]})
        self.assertEqual(metrics[self.bases.id]['current_qty'], 1)
        self.assertEqual(metrics[self.bases.id]['qty_to_produce'], 0)
        # Independent preparation still needs its own finished output.
        self.assertEqual(metrics[self.finishing.id]['qty_to_produce'], 3)

    def test_planning_and_physical_reservations_are_not_free_buffer_stock(self):
        output_totals = {'physical_available_qty': [3.0], 'reserved_qty': [1.0]}
        outputs = SimpleNamespace(mapped=lambda field: output_totals[field])
        with patch.object(type(self.Rule), '_need_buffer_outputs', return_value=outputs):
            metrics = self._metrics({'bases': [{'kind': 'stock', 'source_id': 100, 'quantity': 1}]})
        # One physical handoff + two unexecuted plan claims; only one is free.
        self.assertEqual(metrics[self.bases.id]['current_qty'], 1)
        self.assertEqual(metrics[self.bases.id]['reserved_qty'], 3)
        self.assertEqual(metrics[self.bases.id]['forecast_qty'], 1)
