"""Integration checks on isolated products; safe on a disposable database clone."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestNeedToProduce(TransactionCase):
    STAGES = ('priming', 'carpentry', 'bases', 'finishing', 'tailoring',
              'painting', 'upholstery', 'packaging')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.Piece = cls.env['furniture.need.to.produce']
        cls.Production = cls.env['furniture.mrp.production']
        cls.StageRule = cls.env['furniture.mrp.stage.replenishment.rule']
        cls.FinalRule = cls.env['furniture.mrp.final.replenishment.rule']
        # TransactionCase rolls these fixture-only settings back. A clone may
        # contain live factory policies; they must not create unrelated plans.
        for rule_model in (cls.StageRule, cls.FinalRule):
            rule_model.sudo().search([('company_id', '=', cls.company.id)]).write({
                'min_qty': 0, 'max_qty': 0,
            })
        cls.env['ir.config_parameter'].sudo().set_param('furniture_mrp.mps_enabled', 'False')
        cls.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.parallel_finish_v1', 'False')
        # Legacy scenarios deliberately cover individual approval behavior;
        # grouped scenarios opt in explicitly, independent of the clone's flag.
        cls.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.group_compatible_approvals', 'False',
        )
        cls.product = cls.env['product.product'].create({
            'name': 'NTP integration chaise', 'type': 'consu', 'is_storable': True,
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id,
        })
        cls.model = cls.env['furniture.product.model'].create({'name': 'NTP integration model'})
        cls.raw = cls.env['product.product'].create({
            'name': 'NTP integration raw', 'type': 'consu', 'is_storable': True,
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id, 'standard_price': 1,
        })
        cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1, 'product_uom_id': cls.unit.id, 'type': 'normal',
            'company_id': cls.company.id, 'furniture_product_id': cls.product.id,
            'furniture_recipe_model_id': cls.model.id,
            'furniture_width_cm': 100, 'furniture_depth_cm': 80, 'furniture_height_cm': 70,
            'use_sewing': False,
            **{'use_%s' % stage: True for stage in cls.STAGES},
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': stage, 'product_id': cls.raw.id, 'product_qty': 1,
                'product_uom_id': cls.unit.id, 'quantity_mode': 'scaled',
            }) for stage in cls.STAGES],
        })
        cls.bom = cls.env['mrp.bom']._find_furniture_production_recipe(
            cls.product, model=cls.model, company=cls.company,
        )
        cls.StageRule._stage_replenishment_sync_company(cls.company)
        cls.FinalRule._final_replenishment_sync_company(cls.company)
        cls.rule = cls.FinalRule.search([
            ('company_id', '=', cls.company.id), ('product_id', '=', cls.product.id),
            ('furniture_model_id', '=', cls.model.id),
        ]).ensure_one()
        cls.manager = new_test_user(
            cls.env, login='ntp.integration.manager@example.test',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_manager',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)],
        )

    def _new_production(self, lane, quantity=1, state='confirmed'):
        values = {
            'company_id': self.company.id, 'product_id': self.product.id,
            'furniture_order_model_id': self.model.id, 'product_qty': quantity,
            'bom_id': self.bom.id, 'production_lane': lane, 'stage_plan_mode': 'custom',
            'width_cm': 100, 'depth_cm': 80, 'height_cm': 70,
        }
        production = self.Production.with_context(
            furniture_skip_material_refresh=True, furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).create({**values, 'state': state})
        line = self.env['furniture.mrp.production.line'].with_context(
            furniture_preserve_explicit_bom=True, furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True, furniture_skip_stage_plan_sync=True,
            furniture_skip_running_line_initialization=True,
        ).create({
            **{key: value for key, value in values.items()
               if key in self.env['furniture.mrp.production.line']._fields},
            'production_id': production.id, 'stage_selection_initialized': True,
        })
        return production, line

    def _emit_output(self, production):
        """Materialize a real stock/output fixture, invoking release callbacks.

        This helper is not a simulated supervisor quality approval. It isolates
        the actual stock-ledger dependency handoff from labor/quality fixtures.
        """
        line = production.production_line_ids.ensure_one()
        lane = production._furniture_lane_output_lanes()[production.production_lane]
        final = production._furniture_line_final_product(line)
        wip = self.env['product.product']._furniture_get_or_create_lane_wip_product(
            self.company, lane, final, self.model,
        )
        ready_stage = production._furniture_lane_ready_stages()[production.production_lane]
        production._create_internal_move(
            production._get_production_location(), production._stage_storage_location(ready_stage),
            'NTP integration ready output', move_type='finished_product', product=wip,
            quantity=line.product_qty, uom=wip.uom_id, source_production_line=line, price_unit=2,
        )
        return production._ensure_lane_outputs(line).ensure_one()

    def _stock(self, lane, quantity):
        production, _line = self._new_production(lane, quantity)
        return self._emit_output(production)

    def _plans(self, quantity=3):
        self.rule.write({'min_qty': 0, 'max_qty': quantity})
        self.Piece._sync_request(self.rule, 'packaging', quantity, 'final_rule_id')
        return self.Piece.search([('final_rule_id', '=', self.rule.id)], order='id')

    def _stage(self, piece, lane):
        return piece.stage_ids.filtered(lambda stage: stage.lane == lane).ensure_one()

    def _accept_inputs(self, production):
        # Private stock consumption is used only as a fixture to isolate the
        # next dependency; UI supervisor acceptance remains a separate gate.
        production._furniture_consume_required_handoffs()

    def _enable_grouped_approval(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_need_to_produce.group_compatible_approvals', 'True',
        )

    def test_grouped_identical_pieces_share_one_order_per_lane(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids), 21)
        orders = pieces.stage_ids.production_id
        self.assertEqual(len(orders), 7)
        self.assertEqual(set(orders.mapped('state')), {'confirmed'})
        self.assertEqual(set(orders.mapped('product_qty')), {3})
        self.assertEqual(set(orders.mapped('temporary_stage_fixed_hours')), {6})
        self.assertEqual(set(orders.production_line_ids.mapped('product_qty')), {3})
        self.assertEqual(len(orders.production_line_ids), 7)
        for lane in ('frame', 'bases', 'preparation', 'tailoring', 'painting', 'upholstery', 'packaging'):
            stages = pieces.stage_ids.filtered(lambda s: s.lane == lane)
            self.assertEqual(len(stages.production_id), 1)
            self.assertEqual(set(stages.mapped('quantity')), {1})
            self.assertIn(stages.production_id.need_to_produce_stage_id, stages)
            for code in stages[0].stage_codes:
                self.assertEqual(sum(stages.production_id.material_line_ids.filtered(
                    lambda material: material.stage == code and material.product_id == self.raw
                ).mapped('qty_needed')), 3)
        self.assertEqual(self.Piece._final_committed_quantity(self.rule), 3)
        # Complete the actual grouped dependency chain; no source may leak to
        # another plan, and every piece retains its own exact allocation.
        for lane in ('frame', 'bases', 'preparation', 'tailoring', 'painting', 'upholstery'):
            order = orders.filtered(lambda p: p.production_lane == lane)
            if order._furniture_handoff_required_lanes():
                self._accept_inputs(order)
                self.assertTrue(order._furniture_accepted_handoffs_cover_lines())
            self._emit_output(order)
        packaging = orders.filtered(lambda p: p.production_lane == 'packaging')
        self.assertTrue(packaging._furniture_handoffs_cover_lines())
        self.assertFalse(packaging._furniture_accepted_handoffs_cover_lines())
        self._accept_inputs(packaging)
        for claim in pieces.stage_ids.input_ids:
            self.assertAlmostEqual(sum(claim.handoff_ids.filtered(
                lambda h: h.state == 'consumed').mapped('quantity')), claim.quantity)
        for piece in pieces:
            nodes = piece.route_graph['nodes']
            source_nodes = [n for n in nodes if n['kind'] == 'stage' and n['lane'] != 'packaging']
            self.assertTrue(source_nodes)
            self.assertTrue(all(n.get('withdrawn') for n in source_nodes))

    def test_obsolete_manual_custom_metadata_does_not_split_unedited_pieces(self):
        self._enable_grouped_approval()
        pieces = self._plans(4)
        for piece in pieces[:2]:
            piece.sudo().write({'preview_json': {**piece.preview_json,
                                               'custom_approval': {'manual': True}}})
            self.assertFalse(piece.is_custom)
            self.assertFalse(piece.custom_bom_modified)
            self.assertEqual(piece.state, 'draft')
        self.assertFalse(pieces.stage_ids.production_id)
        self.Piece._refresh_company(self.company)
        self.assertFalse(any(p.is_custom for p in pieces))
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 7)
        for lane in set(pieces.stage_ids.mapped('lane')):
            orders = pieces.stage_ids.filtered(lambda s: s.lane == lane).production_id
            self.assertEqual(orders.mapped('product_qty'), [4])
        self.assertFalse(any('Custom' in p.notes for p in pieces.stage_ids.production_id))
        for piece in pieces[:2]:
            with self.assertRaises(UserError):
                piece.with_user(self.manager).action_toggle_custom()

    def test_custom_identical_bom_edits_stay_individual(self):
        self._enable_grouped_approval()
        pieces = self._plans(4)
        for piece in pieces[:2]:
            material = self._stage(piece, 'painting').material_ids
            material.with_user(self.manager).write({'quantity': 5})
            self.assertTrue(piece.is_custom)
            self.assertTrue(piece.custom_bom_modified)
            with self.assertRaises(UserError):
                piece.with_user(self.manager).action_toggle_custom()
        self.Piece._refresh_company(self.company)
        self.assertTrue(all(p.is_custom for p in pieces[:2]))
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 21)
        self.assertFalse(pieces[0].stage_ids.production_id & pieces[1].stage_ids.production_id)
        self.assertEqual(pieces[2].stage_ids.production_id, pieces[3].stage_ids.production_id)
        for piece in pieces[:2]:
            self.assertEqual(sum(self._stage(piece, 'painting').production_id.material_line_ids.mapped('qty_needed')), 5)

    def test_custom_partners_persist_and_follow_every_separate_order(self):
        self._enable_grouped_approval()
        pieces = self._plans(4)
        buyer = self.env['res.partner'].create({'name': 'Custom buyer test', 'is_company': True})
        consumers = self.env['res.partner'].create([
            {'name': 'Custom consumer A'}, {'name': 'Custom consumer B'},
        ])
        for piece, consumer in zip(pieces[:2], consumers):
            self._stage(piece, 'painting').material_ids.with_user(self.manager).write({'quantity': 5})
            piece.with_user(self.manager).action_save_custom_partners(buyer.id, consumer.id)
            self.assertEqual(piece.custom_partners['buyer_partner_id'][0], buyer.id)
            self.assertEqual(piece.state, 'draft')
        self.Piece._refresh_company(self.company)
        for piece, consumer in zip(pieces[:2], consumers):
            self.assertEqual(piece.custom_partners['beneficiary_partner_id'][0], consumer.id)
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 21)
        for piece, consumer in zip(pieces[:2], consumers):
            orders = piece.stage_ids.production_id
            self.assertEqual(len(orders), 7)
            for order in orders:
                self.assertEqual(order.buyer_partner_id, buyer)
                self.assertEqual(order.beneficiary_partner_id, consumer)
                self.assertEqual(order.production_line_ids.buyer_partner_id, buyer)
                self.assertEqual(order.production_line_ids.beneficiary_partner_id, consumer)
                self.assertEqual(order.product_qty, 1)
            with self.assertRaises(UserError):
                piece.with_user(self.manager).action_save_custom_partners(False, False)
        regular = pieces[2:].stage_ids.production_id
        self.assertEqual(len(regular), 7)
        self.assertFalse(regular.buyer_partner_id)
        self.assertFalse(regular.beneficiary_partner_id)
        self.assertEqual(set(regular.mapped('product_qty')), {2})

    def test_custom_partners_edit_guards_and_clear(self):
        piece = self._plans(1)
        buyer = self.env['res.partner'].create({'name': 'Custom buyer test', 'is_company': True})
        consumer = self.env['res.partner'].create({'name': 'Custom consumer test'})
        with self.assertRaises(UserError):
            piece.with_user(self.manager).action_save_custom_partners(buyer.id, consumer.id)
        self._stage(piece, 'painting').material_ids.with_user(self.manager).write({'quantity': 5})
        ordinary_user = new_test_user(self.env, login='ntp.partners.worker', groups='base.group_user')
        with self.assertRaises(AccessError):
            piece.with_user(ordinary_user).action_save_custom_partners(buyer.id, consumer.id)
        for invalid in (True, -1, '1', 999999999):
            with self.assertRaises(ValidationError):
                piece.with_user(self.manager).action_save_custom_partners(invalid, consumer.id)
        with self.assertRaises(ValidationError):
            piece.with_user(self.manager).action_save_custom_partners(consumer.id, consumer.id)
        other_company = self.env['res.company'].create({'name': 'Custom other company'})
        foreign = self.env['res.partner'].create({'name': 'Foreign consumer', 'company_id': other_company.id})
        with self.assertRaises(AccessError):
            piece.with_user(self.manager).action_save_custom_partners(buyer.id, foreign.id)
        before = {k: v for k, v in piece.preview_json.items() if k != 'custom_approval'}
        piece.with_user(self.manager).action_save_custom_partners(buyer.id, consumer.id)
        piece.with_user(self.manager).action_save_custom_partners(False, False)
        self.assertFalse(piece.custom_partners['buyer_partner_id'])
        self.assertFalse(piece.custom_partners['beneficiary_partner_id'])
        self.assertEqual(before, {k: v for k, v in piece.preview_json.items() if k != 'custom_approval'})
        self.assertFalse(piece.stage_ids.production_id)

    def test_custom_partners_revalidated_at_approval(self):
        piece = self._plans(1)
        self._stage(piece, 'painting').material_ids.with_user(self.manager).write({'quantity': 5})
        buyer = self.env['res.partner'].create({'name': 'Custom buyer test', 'is_company': True})
        piece.with_user(self.manager).action_save_custom_partners(buyer.id, False)
        buyer.write({'active': False})
        with self.assertRaises(ValidationError):
            piece.with_user(self.manager).action_approve()
        self.assertFalse(piece.stage_ids.production_id)

    def test_obsolete_custom_toggle_rejected_without_changing_piece(self):
        self._enable_grouped_approval()
        pieces = self._plans(2)
        before = dict(pieces[0].preview_json)
        with self.assertRaises(UserError):
            pieces[0].with_user(self.manager).action_toggle_custom()
        self.assertEqual(pieces[0].preview_json, before)
        self.assertFalse(pieces[0].is_custom)
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 7)
        self.assertEqual(set(pieces.stage_ids.production_id.mapped('product_qty')), {2})

    def test_custom_metadata_does_not_bypass_stale_route_guard(self):
        pieces = self._plans(1)
        self._stage(pieces, 'painting').material_ids.with_user(self.manager).write({'quantity': 5})
        saved = dict(pieces.preview_json)
        saved['critical_path_hours'] += 1
        pieces.sudo().write({'preview_json': saved})
        with self.assertRaises(UserError):
            pieces.with_user(self.manager).action_approve()
        self.assertFalse(pieces.stage_ids.production_id)

    def test_custom_edit_then_restore_recipe_still_separates_piece(self):
        self._enable_grouped_approval()
        pieces = self._plans(2)
        material = self._stage(pieces[0], 'painting').material_ids
        original = material.quantity
        material.with_user(self.manager).write({'quantity': original + 1})
        material.with_user(self.manager).write({'quantity': original})
        pieces[0]._replace_preview(pieces[0]._build_preview())
        self.assertTrue(pieces[0].is_custom)
        pieces.with_user(self.manager).action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 14)

    def test_custom_empty_recipe_survives_refresh(self):
        pieces = self._plans(1)
        self._stage(pieces, 'painting').material_ids.with_user(self.manager).unlink()
        self.assertTrue(pieces.is_custom)
        pieces._replace_preview(pieces._build_preview())
        self.assertTrue(pieces.is_custom)
        self.assertTrue(pieces.preview_json['custom_approval']['bom_modified'])
        self.assertFalse(self._stage(pieces, 'painting').material_ids)

    def test_grouped_ready_inputs_reserve_each_piece_without_duplicates(self):
        self._enable_grouped_approval()
        outputs = self._stock('finish', 3) | self._stock('tailoring', 3) | self._stock('painting', 3)
        pieces = self._plans(3)
        pieces.with_user(self.manager).action_approve()
        orders = pieces.stage_ids.production_id
        self.assertEqual(len(orders), 2)
        self.assertEqual(set(orders.mapped('product_qty')), {3})
        self.assertEqual(set(outputs.mapped('reserved_qty')), {3})
        claims = pieces.stage_ids.input_ids.filtered(lambda claim: claim.kind == 'stock')
        self.assertEqual(len(claims), 9)
        self.assertEqual(len(claims.handoff_ids), 9)
        self.assertTrue(all(sum(claim.handoff_ids.mapped('quantity')) == 1 for claim in claims))
        before = claims.handoff_ids
        self.env['furniture.need.to.produce.input']._release_available_inputs(company=self.company)
        self.assertEqual(claims.handoff_ids, before)
        upholstery = orders.filtered(lambda p: p.production_lane == 'upholstery')
        self._accept_inputs(upholstery)
        self._emit_output(upholstery)
        packaging = orders.filtered(lambda p: p.production_lane == 'packaging')
        self._accept_inputs(packaging)
        self.assertTrue(packaging._furniture_accepted_handoffs_cover_lines())

    def test_textile_kit_splits_preserve_exact_planned_handoffs(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        pieces.with_user(self.manager).action_approve()
        orders = pieces.stage_ids.production_id
        for order in orders.filtered(lambda p: p.production_lane in ('tailoring', 'upholstery')):
            remainder = order.production_line_ids.with_context(
                furniture_skip_line_consolidation=True)._split_for_partial_quantity(1, preserve_progress=True)
            remainder.with_context(furniture_skip_line_consolidation=True)._split_for_partial_quantity(1, preserve_progress=True)
            self.assertEqual(len(order.production_line_ids), 3)
        for lane in ('frame', 'bases', 'preparation', 'tailoring', 'painting', 'upholstery'):
            order = orders.filtered(lambda p: p.production_lane == lane)
            if order._furniture_handoff_required_lanes():
                order._furniture_reserve_required_handoffs()
                self.assertTrue(order._furniture_handoffs_cover_lines())
                self._accept_inputs(order)
                self.assertTrue(order._furniture_accepted_handoffs_cover_lines())
            for line in order.production_line_ids:
                output_lane = order._furniture_lane_output_lanes()[lane]
                final = order._furniture_line_final_product(line)
                wip = self.env['product.product']._furniture_get_or_create_lane_wip_product(
                    self.company, output_lane, final, self.model)
                ready_stage = order._furniture_lane_ready_stages()[lane]
                order._create_internal_move(order._get_production_location(), order._stage_storage_location(ready_stage),
                    'Split kit test output', move_type='finished_product', product=wip, quantity=line.product_qty,
                    uom=wip.uom_id, source_production_line=line, price_unit=2)
                order._ensure_lane_outputs(line)
        packaging = orders.filtered(lambda p: p.production_lane == 'packaging')
        self.assertTrue(packaging._furniture_handoffs_cover_lines())
        self._accept_inputs(packaging)
        for claim in pieces.stage_ids.input_ids:
            self.assertAlmostEqual(sum(claim.handoff_ids.filtered(lambda h: h.state == 'consumed').mapped('quantity')), claim.quantity)
        upholstery = orders.filtered(lambda p: p.production_lane == 'upholstery')
        for line in upholstery.production_line_ids:
            self.assertTrue(upholstery._furniture_accepted_handoffs_cover_lines(line))
        before = pieces.stage_ids.input_ids.handoff_ids
        pieces.stage_ids.input_ids._reserve_ready_inputs()
        self.assertEqual(before, pieces.stage_ids.input_ids.handoff_ids)

    def test_grouped_different_remaining_routes_do_not_merge(self):
        self._enable_grouped_approval()
        self._stock('frame', 1)
        pieces = self._plans(3)
        pieces.action_approve()
        self.assertFalse(pieces[0].stage_ids.filtered(lambda s: s.lane == 'frame'))
        for lane in ('bases', 'preparation', 'tailoring', 'painting', 'upholstery', 'packaging'):
            first = self._stage(pieces[0], lane).production_id
            second = self._stage(pieces[1], lane).production_id
            self.assertNotEqual(first, second)
            self.assertEqual(second, self._stage(pieces[2], lane).production_id)
            self.assertEqual(first.product_qty, 1)
            self.assertEqual(second.product_qty, 2)

    def test_grouped_material_override_splits_the_whole_route(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        self._stage(pieces[0], 'painting').material_ids.with_user(self.manager).write({'quantity': 5})
        pieces.action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 14)
        modified = self._stage(pieces[0], 'painting').production_id
        standard = self._stage(pieces[1], 'painting').production_id
        self.assertEqual(sum(modified.material_line_ids.mapped('qty_needed')), 5)
        self.assertEqual(sum(standard.material_line_ids.mapped('qty_needed')), 2)
        self.assertEqual(standard, self._stage(pieces[2], 'painting').production_id)

    def test_grouped_selection_retry_and_later_approval_leave_existing_orders_alone(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        selected = pieces[0] | pieces[2]
        selected.with_user(self.manager).action_approve()
        orders = selected.stage_ids.production_id
        self.assertEqual(len(orders), 7)
        self.assertEqual(set(orders.mapped('product_qty')), {2})
        self.assertFalse(pieces[1].stage_ids.production_id)
        self.assertEqual(pieces[1].state, 'draft')
        selected.with_user(self.manager).action_approve()
        self.assertEqual(selected.stage_ids.production_id, orders)
        pieces[1].with_user(self.manager).action_approve()
        self.assertFalse(pieces[1].stage_ids.production_id & orders)
        self.assertEqual(set(orders.mapped('product_qty')), {2})

    def test_grouped_changed_limit_rejects_entire_batch_before_creation(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        self.rule.write({'max_qty': 2})
        orders_before = self.Production.search_count([])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            pieces.with_user(self.manager).action_approve()
        self.assertEqual(set(pieces.mapped('state')), {'draft'})
        self.assertFalse(pieces.stage_ids.production_id)
        self.assertEqual(self.Production.search_count([]), orders_before)

    def test_grouped_same_route_different_product_never_merges(self):
        self._enable_grouped_approval()
        first = self._plans(1)
        product = self.env['product.product'].create({
            'name': 'NTP second different product', 'type': 'consu', 'is_storable': True,
            'uom_id': self.unit.id, 'uom_po_id': self.unit.id})
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1, 'product_uom_id': self.unit.id, 'type': 'normal',
            'company_id': self.company.id, 'furniture_product_id': product.id,
            'furniture_recipe_model_id': self.model.id,
            'furniture_width_cm': 100, 'furniture_depth_cm': 80, 'furniture_height_cm': 70,
            'use_sewing': False, **{'use_%s' % stage: True for stage in self.STAGES},
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': stage, 'product_id': self.raw.id, 'product_qty': 1,
                'product_uom_id': self.unit.id, 'quantity_mode': 'scaled',
            }) for stage in self.STAGES]})
        bom = self.env['mrp.bom']._find_furniture_production_recipe(
            product, model=self.model, company=self.company)
        self.StageRule._stage_replenishment_sync_company(self.company)
        self.FinalRule._final_replenishment_sync_company(self.company)
        rule = self.FinalRule.search([('product_id', '=', product.id), ('bom_id', '=', bom.id),
                                      ('company_id', '=', self.company.id)]).ensure_one()
        rule.write({'min_qty': 0, 'max_qty': 1})
        self.Piece._sync_request(rule, 'packaging', 1, 'final_rule_id')
        second = self.Piece.search([('final_rule_id', '=', rule.id)])
        (first | second).action_approve()
        self.assertFalse(first.stage_ids.production_id & second.stage_ids.production_id)
        self.assertEqual(len((first | second).stage_ids.production_id), 14)

    def test_grouped_partial_receipts_wait_for_all_claims(self):
        self._enable_grouped_approval()
        pieces = self._plans(3)
        pieces.action_approve()
        source = self._stage(pieces[0], 'frame').production_id
        target = self._stage(pieces[0], 'bases').production_id
        line = source.production_line_ids.ensure_one()
        wip = self.env['product.product']._furniture_get_or_create_lane_wip_product(
            self.company, 'frame', source._furniture_line_final_product(line), self.model)
        for quantity in (1, 2):
            source._create_internal_move(
                source._get_production_location(), source._stage_storage_location('carpentry'),
                'NTP grouped partial receipt', move_type='finished_product', product=wip,
                quantity=quantity, uom=wip.uom_id, source_production_line=line, price_unit=2)
            if quantity == 1:
                self.assertFalse(target._furniture_handoffs_cover_lines())
                with self.assertRaises(UserError), self.env.cr.savepoint():
                    target._furniture_reserve_required_handoffs()
            else:
                source._ensure_lane_outputs()
                self.assertTrue(target._furniture_handoffs_cover_lines())
        claims = pieces.stage_ids.filtered(lambda s: s.lane == 'bases').input_ids
        self.assertEqual(len(claims.handoff_ids), 3)
        self.assertEqual(sum(claims.handoff_ids.mapped('quantity')), 3)
        self._accept_inputs(target)
        self.assertTrue(target._furniture_accepted_handoffs_cover_lines())

    def test_grouped_buffer_output_is_counted_and_claimed_once(self):
        self._enable_grouped_approval()
        buffer = self.StageRule.search([
            ('product_id', '=', self.product.id), ('bom_id', '=', self.bom.id),
            ('stage_code', '=', 'bases'), ('company_id', '=', self.company.id)]).ensure_one()
        buffer.write({'min_qty': 0, 'max_qty': 3})
        self.Piece._sync_request(buffer, 'bases', 3, 'buffer_rule_id')
        pieces = self.Piece.search([('buffer_rule_id', '=', buffer.id)])
        pieces.action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 2)
        source = self._stage(pieces[0], 'bases').production_id
        self.assertEqual(source.product_qty, 3)
        final = self._plans(3)
        claims = final.stage_ids.input_ids.filtered(lambda claim: claim.kind == 'incoming')
        self.assertEqual(sum(claims.mapped('quantity')), 3)
        self.assertEqual(claims.source_line_id, source.production_line_ids)
        final.action_approve()
        self.assertEqual(len(final.stage_ids.production_id), 5)
        self.assertEqual(self.Piece._final_committed_quantity(self.rule), 3)

    def test_final_stock_arriving_after_preview_prevents_excess_approval(self):
        piece = self._plans(1)
        self.env['stock.quant']._update_available_quantity(
            self.product, self.env.ref('furniture_mrp.location_finished_goods'), 1,
        )
        with self.assertRaises(UserError):
            piece.action_approve()
        self.assertFalse(piece.stage_ids.production_id)

    def test_bulk_approval_only_selected_pieces_and_keeps_material_overrides(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        pieces = self._plans(3)
        material = self._stage(pieces[0], 'painting').material_ids.ensure_one()
        material.with_user(self.manager).write({'quantity': 5})
        selected = pieces[0] | pieces[2]
        selected.with_user(self.manager).action_approve()
        self.assertEqual(set(selected.mapped('state')), {'approved'})
        self.assertEqual(pieces[1].state, 'draft')
        self.assertFalse(pieces[1].stage_ids.production_id)
        orders = selected.stage_ids.production_id
        self.assertEqual(len(orders), 6)
        self.assertEqual(set(orders.mapped('state')), {'confirmed'})
        paint_order = self._stage(pieces[0], 'painting').production_id
        self.assertEqual(sum(paint_order.material_line_ids.filtered(
            lambda line: line.stage == 'painting' and line.product_id == self.raw
        ).mapped('qty_needed')), 5)
        selected.with_user(self.manager).action_approve()
        self.assertEqual(selected.stage_ids.production_id, orders)

    def test_bulk_approval_is_one_transaction_if_a_selected_piece_fails(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        pieces = self._plans(3)
        pieces[1].sudo().write({'state': 'cancelled'})
        orders_before = self.Production.search_count([])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            pieces[:2].with_user(self.manager).action_approve()
        self.assertEqual(pieces[0].state, 'draft')
        self.assertFalse(pieces[0].stage_ids.production_id)
        self.assertEqual(self.Production.search_count([]), orders_before)

    def test_final_refill_cycle_keeps_remaining_need_above_min_until_max(self):
        self.rule.write({'min_qty': 0, 'max_qty': 3})
        self.Piece._refresh_company(self.company)
        self.assertTrue(self.rule.replenishment_cycle_active)
        self.assertEqual(self.Piece.search_count([
            ('final_rule_id', '=', self.rule.id), ('state', '=', 'draft'),
        ]), 3)
        finished = self.env.ref('furniture_mrp.location_finished_goods')
        self.env['stock.quant']._update_available_quantity(self.product, finished, 1)
        self.Piece._refresh_company(self.company)
        self.assertTrue(self.rule.replenishment_cycle_active)
        self.assertEqual(self.Piece.search_count([
            ('final_rule_id', '=', self.rule.id), ('state', '=', 'draft'),
        ]), 2)
        self.env['stock.quant']._update_available_quantity(self.product, finished, 2)
        self.Piece._refresh_company(self.company)
        self.assertFalse(self.rule.replenishment_cycle_active)
        self.assertFalse(self.Piece.search([
            ('final_rule_id', '=', self.rule.id), ('state', '=', 'draft'),
        ]))

    def test_bulk_packaging_partial_receipt_counts_only_remaining_commitment(self):
        production, line = self._new_production('packaging', 3)
        self.rule.write({'min_qty': 0, 'max_qty': 4})
        self.Piece._refresh_company(self.company)
        self.assertEqual(self.Piece._final_committed_quantity(self.rule), 3)
        pieces = self.Piece.search([
            ('final_rule_id', '=', self.rule.id), ('state', '=', 'draft'),
        ])
        self.assertEqual(len(pieces), 1)
        production._create_internal_move(
            production._get_production_location(),
            self.env.ref('furniture_mrp.location_finished_goods'),
            'NTP historical packaging partial receipt', move_type='finished_product',
            product=self.product, quantity=1, uom=self.unit,
            source_production_line=line, price_unit=2,
        )
        self.assertEqual(self.Piece._final_committed_quantity(self.rule), 2)
        self.Piece._refresh_company(self.company)
        self.assertEqual(self.Piece.search([
            ('final_rule_id', '=', self.rule.id), ('state', '=', 'draft'),
        ]), pieces)
        pieces.action_approve()
        self.assertEqual(pieces.state, 'approved')
        self.assertEqual(self.Piece._final_committed_quantity(self.rule), 3)

    def test_exact_pending_predecessor_cannot_be_replaced_by_unrelated_stock(self):
        piece = self._plans(1)
        piece.action_approve()
        bases = self._stage(piece, 'bases').production_id
        unrelated = self._stock('frame', 1)
        self.assertEqual(unrelated.physical_available_qty, 1)
        with self.assertRaises(UserError):
            bases._furniture_reserve_required_handoffs()
        self.assertFalse(bases.upstream_handoff_ids)
        self.assertEqual(unrelated.physical_available_qty, 1)

    def test_approved_packaging_still_requires_supervisor_quality_and_receipt(self):
        self._stock('finish', 1)
        self._stock('tailoring', 1)
        self._stock('painting', 1)
        piece = self._plans(1)
        piece.action_approve()
        upholstery = self._stage(piece, 'upholstery').production_id
        self._accept_inputs(upholstery)
        self._emit_output(upholstery)
        packaging = self._stage(piece, 'packaging').production_id
        user = new_test_user(self.env, login='ntp.packaging.quality@example.test', groups='base.group_user')
        stages = self.env['furniture.mrp.employee.stage'].search([('code', '=', 'packaging')])
        employee = self.env['hr.employee'].create({
            'name': 'NTP quality supervisor', 'user_id': user.id,
            'company_id': self.company.id, 'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })
        stage = packaging._create_stage_order('furniture.mrp.packaging', 'PKG', {'foreman_id': employee.id})
        packaging.write({'packaging_order_id': stage.id})
        current = packaging.with_user(user)
        quality = packaging._incoming_upholstery_quality_rows()
        self.assertEqual(len(quality), 1)
        with self.assertRaises(UserError):
            current.action_accept_handoff_transfer()
        with self.assertRaises(UserError):
            stage.with_user(user).action_start()
        current.action_review_handoff_quality(quality[0]['key'], 'pass')
        self.assertFalse(packaging._furniture_accepted_handoffs_cover_lines())
        current.action_accept_handoff_transfer()
        self.assertTrue(packaging._packaging_upholstery_quality_ready())
        self.assertTrue(packaging._furniture_accepted_handoffs_cover_lines())
        moves = packaging.upstream_handoff_ids.transfer_move_id
        self.assertEqual(set(moves.mapped('state')), {'done'})
        current.action_accept_handoff_transfer()
        self.assertEqual(packaging.upstream_handoff_ids.transfer_move_id, moves)

    def test_case_one_ready_buffers_only_upholstery_and_packaging(self):
        finish = self._stock('finish', 3)
        tailoring = self._stock('tailoring', 3)
        painting = self._stock('painting', 3)
        before = self.Production.search_count([])
        pieces = self._plans()
        self.assertEqual(len(pieces), 3)
        self.assertEqual(self.Production.search_count([]), before)
        for piece in pieces:
            self.assertEqual(set(piece.stage_ids.mapped('lane')), {'upholstery', 'packaging'})
            self.assertEqual(piece.total_work_hours, 4)
            self.assertEqual(piece.critical_path_hours, 4)
            self.assertEqual(piece.critical_path_days, 0.4)
        pieces.action_approve()
        self.assertEqual(self.Production.search_count([]) - before, 6)
        self.assertEqual(set(pieces.stage_ids.production_id.mapped('state')), {'confirmed'})
        self.assertEqual(set(pieces.stage_ids.production_id.mapped('product_qty')), {1})
        for output in finish | tailoring | painting:
            output.invalidate_recordset()
            self.assertEqual(output.reserved_qty, 3)
        for piece in pieces:
            upholstery = self._stage(piece, 'upholstery').production_id
            packaging = self._stage(piece, 'packaging').production_id
            self.assertTrue(upholstery._furniture_handoffs_cover_lines())
            self.assertFalse(upholstery._furniture_accepted_handoffs_cover_lines())
            self.assertFalse(packaging._furniture_handoffs_cover_lines())
            with self.assertRaises(UserError):
                upholstery._furniture_require_accepted_handoffs()

    def test_case_two_separate_frame_bases_preparation_orders_wait_for_inputs(self):
        self._stock('tailoring', 3)
        self._stock('painting', 3)
        pieces = self._plans()
        for piece in pieces:
            self.assertEqual(set(piece.stage_ids.mapped('lane')),
                             {'frame', 'bases', 'preparation', 'upholstery', 'packaging'})
        pieces.action_approve()
        piece = pieces[0]
        frame = self._stage(piece, 'frame').production_id
        bases = self._stage(piece, 'bases').production_id
        preparation = self._stage(piece, 'preparation').production_id
        self.assertNotEqual(bases, preparation)
        self.assertEqual(bases._required_stage_codes(), ['bases'])
        self.assertEqual(preparation._required_stage_codes(), ['finishing'])
        self.assertFalse(bases.upstream_handoff_ids)
        self.assertFalse(preparation.upstream_handoff_ids)
        with self.assertRaises(UserError):
            bases.action_start_bases()
        with self.assertRaises(UserError):
            preparation.action_start_finishing()
        before = self.Production.search_count([])
        frame_output = self._emit_output(frame)
        self.assertEqual(self.Production.search_count([]), before)
        self.assertEqual(bases.upstream_handoff_ids.output_id, frame_output)
        self.assertFalse(preparation.upstream_handoff_ids)
        self._accept_inputs(bases)
        bases_output = self._emit_output(bases)
        self.assertEqual(bases_output.lane, 'bases')
        self.assertEqual(preparation.upstream_handoff_ids.output_id, bases_output)
        self.assertFalse(preparation._furniture_accepted_handoffs_cover_lines())
        self._accept_inputs(preparation)
        finish_output = self._emit_output(preparation)
        self.assertEqual(finish_output.lane, 'finish')
        self.assertEqual(finish_output.wip_product_id.furniture_wip_lane, 'finish')
        upholstery = self._stage(piece, 'upholstery').production_id
        self.assertEqual(upholstery.upstream_handoff_ids.filtered(lambda h: h.role == 'finish').output_id,
                         finish_output)
        self.assertEqual(self.Production.search_count([]), before)

    def test_case_three_painting_parallel_and_packaging_waits_for_both(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        pieces = self._plans()
        for piece in pieces:
            self.assertEqual(set(piece.stage_ids.mapped('lane')), {'upholstery', 'painting', 'packaging'})
            self.assertEqual(piece.total_work_hours, 6)
            self.assertEqual(piece.critical_path_hours, 4)
        pieces.action_approve()
        piece = pieces[0]
        upholstery = self._stage(piece, 'upholstery').production_id
        painting = self._stage(piece, 'painting').production_id
        packaging = self._stage(piece, 'packaging').production_id
        self._accept_inputs(upholstery)
        before = self.Production.search_count([])
        self._emit_output(upholstery)
        self.assertFalse(packaging._furniture_handoffs_cover_lines())
        with self.assertRaises(UserError):
            packaging._furniture_require_accepted_handoffs()
        self._emit_output(painting)
        self.assertTrue(packaging._furniture_handoffs_cover_lines())
        self.assertFalse(packaging._furniture_accepted_handoffs_cover_lines())
        self.assertEqual(self.Production.search_count([]), before)

    def test_mixed_stock_allocates_different_exact_routes_per_piece(self):
        finish = self._stock('finish', 1)
        painting = self._stock('painting', 2)
        pieces = self._plans()
        self.assertEqual(set(pieces[0].stage_ids.mapped('lane')), {'tailoring', 'upholstery', 'packaging'})
        self.assertEqual(set(pieces[1].stage_ids.mapped('lane')),
                         {'frame', 'bases', 'preparation', 'tailoring', 'upholstery', 'packaging'})
        self.assertEqual(set(pieces[2].stage_ids.mapped('lane')),
                         {'frame', 'bases', 'preparation', 'tailoring', 'painting', 'upholstery', 'packaging'})
        claims = pieces.stage_ids.input_ids
        self.assertEqual(sum(claims.filtered(lambda c: c.output_id == finish).mapped('quantity')), 1)
        self.assertEqual(sum(claims.filtered(lambda c: c.output_id == painting).mapped('quantity')), 2)
        pieces.action_approve()
        self.assertEqual(sum(pieces.stage_ids.filtered(lambda s: s.lane == 'painting').mapped('quantity')), 1)
        self.assertEqual(sum(pieces.stage_ids.filtered(lambda s: s.lane == 'preparation').mapped('quantity')), 2)

    def test_approval_is_idempotent_and_stale_stock_requires_review(self):
        finish = self._stock('finish', 3)
        self._stock('tailoring', 3)
        self._stock('painting', 3)
        pieces = self._plans()
        pieces[0].action_approve()
        orders = pieces[0].stage_ids.production_id
        handoffs = pieces[0].stage_ids.input_ids.handoff_ids
        pieces[0].action_approve()
        self.assertEqual(pieces[0].stage_ids.production_id, orders)
        self.assertEqual(pieces[0].stage_ids.input_ids.handoff_ids, handoffs)
        self.env['stock.quant']._update_available_quantity(
            finish.wip_product_id, finish.source_location_id, -1,
        )
        with self.assertRaises(UserError):
            pieces[1].action_approve()
        self.assertFalse(pieces[1].stage_ids.production_id)

    def test_existing_upholstery_supply_is_reused_without_duplicate_order(self):
        existing, source_line = self._new_production('upholstery', 3)
        self._stock('painting', 3)
        pieces = self._plans()
        for piece in pieces:
            self.assertEqual(piece.stage_ids.mapped('lane'), ['packaging'])
            claim = piece.stage_ids.input_ids.filtered(lambda c: c.role == 'upholstery').ensure_one()
            self.assertEqual(claim.source_line_id, source_line)
            self.assertEqual(claim.kind, 'incoming')
            self.assertTrue(piece.unknown_incoming_wait)
        pieces.action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 3)
        output = self._emit_output(existing)
        self.assertEqual(sum(pieces.stage_ids.input_ids.handoff_ids.filtered(
            lambda h: h.output_id == output).mapped('quantity')), 3)

    def test_material_override_is_per_piece_and_stage_not_master_recipe(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        pieces = self._plans()
        stage = self._stage(pieces[0], 'painting')
        material = stage.material_ids.filtered(lambda m: m.product_id == self.raw).ensure_one()
        original = material.quantity
        bom_quantities = self.bom.furniture_stage_material_line_ids.mapped('product_qty')
        material.with_user(self.manager).write({'quantity': original + 4})
        self.assertEqual(self._stage(pieces[1], 'painting').material_ids.quantity, original)
        self.assertEqual(self._stage(pieces[0], 'upholstery').material_ids.quantity, original)
        self.assertEqual(self.bom.furniture_stage_material_line_ids.mapped('product_qty'), bom_quantities)
        pieces[0].action_approve()
        actual = stage.production_id.material_line_ids.filtered(
            lambda row: row.stage == 'painting' and row.product_id == self.raw)
        self.assertTrue(actual)
        self.assertEqual(sum(actual.mapped('qty_needed')), original + 4)
        self.assertEqual(stage.production_id.bom_id, self.bom)
        self.assertEqual(stage.production_id.production_line_ids.bom_id, self.bom)
        stage.production_id._refresh_material_lines_for_stage_plan()
        self.assertEqual(sum(stage.production_id.material_line_ids.filtered(
            lambda row: row.stage == 'painting' and row.product_id == self.raw
        ).mapped('qty_needed')), original + 4)
        request_commands = stage.production_id._prepare_material_lines_for_product_stage(
            self.product, 1, 'painting', stage.production_id.production_line_ids,
        )
        self.assertEqual(sum(command[2]['qty_needed'] for command in request_commands), original + 4)
        with self.assertRaises(AccessError):
            material.with_user(self.manager).write({'quantity': 99})

    def test_bases_buffer_has_independent_limits_and_final_demand_override(self):
        bases_rule = self.StageRule.search([
            ('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'bases'),
        ]).ensure_one()
        finishing_rule = self.StageRule.search([
            ('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'finishing'),
        ]).ensure_one()
        self.assertTrue(bases_rule.active)
        bases_rule.write({'min_qty': 0, 'max_qty': 2})
        self.Piece._refresh_company(self.company)
        buffer_pieces = self.Piece.search([('buffer_rule_id', '=', bases_rule.id)])
        self.assertEqual(len(buffer_pieces), 2)
        self.assertEqual(set(buffer_pieces.stage_ids.mapped('lane')), {'frame', 'bases'})
        self.assertEqual(finishing_rule.max_qty, 0)
        # Final-product need does not stop at the department's smaller Max.
        buffer_pieces.unlink()
        self._stock('bases', 1)
        self._stock('tailoring', 3)
        self._stock('painting', 3)
        pieces = self._plans()
        self.assertEqual(sum(pieces.stage_ids.filtered(lambda s: s.lane == 'bases').mapped('quantity')), 2)
        self.assertEqual(sum(pieces.stage_ids.filtered(lambda s: s.lane == 'preparation').mapped('quantity')), 3)
        self.assertEqual(bases_rule.min_qty, 0)
        self.assertEqual(bases_rule.max_qty, 2)

    def test_added_and_deleted_materials_survive_preview_refresh(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        pieces = self._plans()
        piece = pieces[0]
        painting = self._stage(piece, 'painting')
        original = painting.material_ids
        extra = self.env['product.product'].create({
            'name': 'NTP alternate painting material', 'type': 'consu',
            'is_storable': True, 'uom_id': self.unit.id, 'uom_po_id': self.unit.id,
        })
        self.env['furniture.need.to.produce.material'].with_user(self.manager).create({
            'stage_id': painting.id, 'stage_code': 'painting', 'product_id': extra.id,
            'uom_id': self.unit.id, 'quantity': 2,
        })
        original.with_user(self.manager).unlink()
        self._plans()
        refreshed = self._stage(piece, 'painting')
        self.assertEqual(refreshed.material_ids.product_id, extra)
        self.assertEqual(refreshed.material_ids.quantity, 2)
        self.assertEqual(self._stage(pieces[1], 'painting').material_ids.product_id, self.raw)

    def test_disabling_buffer_policy_removes_unapproved_needs(self):
        bases_rule = self.StageRule.search([
            ('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'bases'),
        ]).ensure_one()
        bases_rule.write({'min_qty': 0, 'max_qty': 2})
        self.Piece._refresh_company(self.company)
        self.assertEqual(self.Piece.search_count([
            ('buffer_rule_id', '=', bases_rule.id), ('state', '=', 'draft'),
        ]), 2)
        bases_rule.write({'min_qty': 0, 'max_qty': 0})
        self.Piece._refresh_company(self.company)
        self.assertFalse(self.Piece.search([
            ('buffer_rule_id', '=', bases_rule.id), ('state', '=', 'draft'),
        ]))

    def test_explicit_empty_material_override_survives_refresh_and_approval(self):
        self._stock('finish', 3)
        self._stock('tailoring', 3)
        piece = self._plans()[0]
        painting = self._stage(piece, 'painting')
        painting.material_ids.with_user(self.manager).unlink()
        self.assertTrue(painting.materials_customized)
        self._plans()
        refreshed = self._stage(piece, 'painting')
        self.assertTrue(refreshed.materials_initialized)
        self.assertTrue(refreshed.materials_customized)
        self.assertFalse(refreshed.material_ids)
        piece.action_approve()
        self.assertFalse(refreshed.production_id.material_line_ids.filtered(
            lambda row: row.stage == 'painting'
        ))

    def test_buffer_shortage_does_not_reuse_stock_already_netted_from_target(self):
        bases_rule = self.StageRule.search([
            ('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'bases'),
        ]).ensure_one()
        self._stock('bases', 1)
        self._stock('frame', 3)
        bases_rule.write({'min_qty': 1, 'max_qty': 3})
        self.Piece._refresh_company(self.company)
        pieces = self.Piece.search([('buffer_rule_id', '=', bases_rule.id)], order='id')
        self.assertEqual(len(pieces), 2)
        for piece in pieces:
            self.assertEqual(piece.stage_ids.mapped('lane'), ['bases'])
            self.assertEqual(piece.stage_ids.quantity, 1)
        pieces.action_approve()
        self.assertEqual(len(pieces.stage_ids.production_id), 2)
        self.assertEqual(set(pieces.stage_ids.production_id.mapped('state')), {'confirmed'})

    def test_approved_buffer_target_is_shared_once_and_not_double_counted(self):
        bases_rule = self.StageRule.search([
            ('company_id', '=', self.company.id), ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id), ('stage_code', '=', 'bases'),
        ]).ensure_one()
        self._stock('frame', 3)
        bases_rule.write({'min_qty': 0, 'max_qty': 2})
        self.Piece._refresh_company(self.company)
        buffer_pieces = self.Piece.search([('buffer_rule_id', '=', bases_rule.id)], order='id')
        buffer_pieces.action_approve()
        target_lines = buffer_pieces.stage_ids.production_id.production_line_ids
        self.assertEqual(len(target_lines), 2)
        metrics = self.StageRule._need_buffer_metric(bases_rule)
        self.assertEqual(metrics['incoming_qty'], 2)
        self.assertEqual(metrics['forecast_qty'], 2)
        self.assertEqual(metrics['qty_to_produce'], 0)
        self.Piece._refresh_company(self.company)
        self.assertFalse(self.Piece.search([
            ('buffer_rule_id', '=', bases_rule.id), ('state', '=', 'draft'),
        ]))

        self._stock('tailoring', 3)
        self._stock('painting', 3)
        pieces = self._plans()
        # Two final pieces reuse the approved buffer targets. Only the third
        # needs another bases order; its frame input is already ready too.
        self.assertEqual(sum(pieces.stage_ids.filtered(
            lambda stage: stage.lane == 'bases'
        ).mapped('quantity')), 1)
        self.assertEqual(sum(pieces.stage_ids.filtered(
            lambda stage: stage.lane == 'preparation'
        ).mapped('quantity')), 3)
        reused = pieces.stage_ids.input_ids.filtered(
            lambda claim: claim.kind == 'incoming' and claim.role == 'bases'
        )
        self.assertEqual(reused.source_line_id, target_lines)
        self.assertEqual(sum(reused.mapped('quantity')), 2)
        claimed_metrics = self.StageRule._need_buffer_metric(bases_rule)
        self.assertEqual(claimed_metrics['incoming_qty'], 0)
        pieces.action_approve()
        self.assertEqual(sum(buffer_pieces.stage_ids.production_id.mapped('product_qty')), 2)
