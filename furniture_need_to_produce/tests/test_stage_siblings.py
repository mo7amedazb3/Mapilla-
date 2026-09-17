from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from .test_production_plan import TestNeedToProduce


@tagged('post_install', '-at_install')
class TestStageSiblings(TransactionCase):
    _new_production = TestNeedToProduce._new_production
    _emit_output = TestNeedToProduce._emit_output
    _stock = TestNeedToProduce._stock

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.Piece = cls.env['furniture.need.to.produce']
        cls.Production = cls.env['furniture.mrp.production']
        cls.Rule = cls.env['furniture.mrp.stage.replenishment.rule']
        cls.model = cls.env['furniture.product.model'].create({'name': 'Sibling test model'})
        for key in ('parallel_finish_v1', 'group_compatible_approvals'):
            cls.env['ir.config_parameter'].sudo().set_param('furniture_need_to_produce.' + key, 'False')
        cls.products = cls.env['product.product'].create([
            {'name': name, 'type': 'consu', 'is_storable': True}
            for name in ('كنبة كبيرة للاختبار', 'شازلونج للاختبار', 'فوتيه للاختبار', 'صنف خارج الطقم')
        ])
        cls.raw = cls.env['product.product'].create({'name': 'Sibling test raw', 'type': 'consu', 'is_storable': True})
        cls.recipes = cls.env['mrp.bom']
        stages = TestNeedToProduce.STAGES
        for product in cls.products:
            cls.recipes |= cls.env['mrp.bom'].create({
                'product_tmpl_id': product.product_tmpl_id.id,
                'product_qty': 1, 'type': 'normal', 'company_id': cls.company.id,
                'furniture_product_id': product.id, 'furniture_recipe_model_id': cls.model.id,
                'furniture_width_cm': 100, 'furniture_depth_cm': 80, 'furniture_height_cm': 70,
                'use_sewing': False, **{'use_' + stage: True for stage in stages},
                'furniture_stage_material_line_ids': [(0, 0, {
                    'stage': stage, 'product_id': cls.raw.id, 'product_qty': 1,
                    'product_uom_id': cls.unit.id, 'quantity_mode': 'scaled',
                }) for stage in stages],
            })
        kit = cls.env['product.product'].create({
            'name': 'Sibling test kit', 'type': 'consu', 'furniture_model_id': cls.model.id,
        })
        cls.kit = cls.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id, 'product_qty': 1, 'type': 'phantom',
            'furniture_product_id': kit.id, 'furniture_model_id': cls.model.id,
            'bom_line_ids': [(0, 0, {'product_id': product.id, 'product_qty': 1,
                                   'product_uom_id': cls.unit.id}) for product in cls.products[:3]],
        })
        cls.rules = cls.Rule._stage_replenishment_sync_company(cls.company).filtered(
            lambda rule: rule.furniture_model_id == cls.model)
        cls.rules._stage_replenishment_internal_write({'min_qty': 3, 'max_qty': 6})
        cls.manager = new_test_user(cls.env, login='sibling.manager@test.invalid',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_manager',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])

    def _rule(self, index, stage='carpentry'):
        return self.rules.filtered(lambda rule: rule.product_id == self.products[index] and rule.stage_code == stage).ensure_one()

    def _supply(self, index, quantity, lane='frame', stock=False):
        self.product = self.products[index]
        self.bom = self.env['mrp.bom']._find_furniture_production_recipe(
            self.product, model=self.model, company=self.company)
        return self._stock(lane, quantity) if stock else self._new_production(lane, quantity)[0]

    def _anchor(self, stage='carpentry', lane='frame'):
        rule = self._rule(0, stage)
        self.Piece._sync_request(rule, lane, 1, 'buffer_rule_id')
        return self.Piece.search([('buffer_rule_id', '=', rule.id), ('state', '=', 'draft')]).with_user(self.manager)

    def test_stock_above_min_topup_and_repeat(self):
        self._supply(0, 3, stock=True)
        self._supply(1, 4, stock=True)
        anchor = self._anchor()
        rows = anchor.stage_sibling_balances()[self._rule(0).id]
        self.assertEqual(len(rows), 1)  # Neither armchair nor an unrelated model product.
        self.assertEqual((rows[0]['stock'], rows[0]['incoming'], rows[0]['missing']), (4, 0, 2))
        result = anchor.action_complete_stage_sibling(self._rule(1).id)
        self.assertEqual(result['quantity'], 2)
        plans = self.Piece.search([('buffer_rule_id', '=', self._rule(1).id), ('state', '=', 'approved')])
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans.sibling_source_rule_id, self._rule(0))
        self.assertTrue(plans.stage_ids.production_id)
        self.assertEqual(self.Piece._sibling_balance(self._rule(1))['total'], 6)
        before = self.Production.search_count([])
        self.assertEqual(anchor.action_complete_stage_sibling(self._rule(1).id)['quantity'], 0)
        self.assertEqual(self.Production.search_count([]), before)

    def test_stock_and_incoming_use_same_stage_and_latest_balance(self):
        self._supply(0, 2, stock=True)
        self._supply(0, 1)
        self._supply(1, 2, stock=True)
        self._supply(1, 2)
        self._supply(1, 12, lane='painting')
        anchor = self._anchor()
        row = anchor.stage_sibling_balances()[self._rule(0).id][0]
        self.assertEqual((row['stock'], row['incoming'], row['total']), (2, 2, 4))
        self._supply(1, 1)  # Arrives after the card was loaded.
        self.assertEqual(anchor.action_complete_stage_sibling(self._rule(1).id)['quantity'], 1)

    def test_exclusions_and_invalid_trigger(self):
        anchor = self._anchor()
        for target in (self._rule(2), self._rule(3), self._rule(1, 'painting')):
            with self.assertRaises(UserError):
                anchor.action_complete_stage_sibling(target.id)
        self._supply(0, 4)
        self.assertFalse(anchor.stage_sibling_balances())
        with self.assertRaises(UserError):
            anchor.action_complete_stage_sibling(self._rule(1).id)
        self.assertFalse(self.Piece._sibling_rules(self._rule(2)))
        with self.assertRaises(AccessError):
            anchor.with_user(self.env.ref('base.public_user')).action_complete_stage_sibling(self._rule(1).id)

    def test_parallel_finish_companion_is_not_duplicated_from_bases(self):
        self.env['ir.config_parameter'].sudo().set_param('furniture_need_to_produce.parallel_finish_v1', 'True')
        self._supply(0, 3, lane='finish', stock=True)
        self._supply(1, 4, lane='finish', stock=True)
        finishing = self._anchor('finishing', 'preparation')
        self.assertEqual(finishing.action_complete_stage_sibling(self._rule(1, 'finishing').id)['quantity'], 2)
        bases = self._anchor('bases', 'bases')
        self.assertEqual(bases.action_complete_stage_sibling(self._rule(1, 'bases').id)['quantity'], 0)

    def test_all_minmax_stages_and_final_excluded(self):
        for stage, lane in [('carpentry', 'frame'), ('bases', 'bases'), ('finishing', 'preparation'),
                            ('tailoring', 'tailoring'), ('painting', 'painting')]:
            anchor = self._anchor(stage, lane)
            self.assertEqual(anchor.stage_sibling_balances()[self._rule(0, stage).id][0]['rule_id'], self._rule(1, stage).id)
            self.assertEqual(anchor.action_complete_stage_sibling(self._rule(1, stage).id)['quantity'], 6)
        final = self.env['furniture.mrp.final.replenishment.rule']._final_replenishment_sync_company(self.company).filtered(
            lambda rule: rule.product_id == self.products[0] and rule.furniture_model_id == self.model)
        final.write({'min_qty': 3, 'max_qty': 6})
        self.Piece._sync_request(final, 'packaging', 1, 'final_rule_id')
        piece = self.Piece.search([('final_rule_id', '=', final.id), ('state', '=', 'draft')])
        self.assertFalse(piece.stage_sibling_balances())
        with self.assertRaises(UserError):
            piece.action_complete_stage_sibling(self._rule(1).id)
