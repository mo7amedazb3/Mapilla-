from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user


class TestDualProductionLaneFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.completion_operator = new_test_user(
            cls.env,
            login='dual_lane_completion_operator',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_completion_operator'
            ),
        )
        cls.factory_manager_no_stock = new_test_user(
            cls.env,
            login='dual_lane_factory_manager_no_stock',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
        )
        cls.final_product = cls.env['product.product'].create({
            'name': 'Dual Lane Finished Sofa',
            'type': 'consu',
            'is_storable': True,
        })
        cls.furniture_model = cls.env['furniture.product.model'].create({
            'name': 'Dual Lane Test Model',
        })

    def _create_lane_production(self, lane, quantity=2.0, bom=False):
        production = self.env['furniture.mrp.production'].create({
            'production_lane': lane,
            'product_qty': quantity,
        })
        line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_preserve_explicit_bom=True,
        ).create({
            'production_id': production.id,
            'product_id': self.final_product.id,
            'furniture_order_model_id': self.furniture_model.id,
            'bom_id': bom.id if bom else False,
            'product_qty': quantity,
        })
        return production, line

    def test_carpentry_lane_is_named_consistently(self):
        labels = dict(
            self.env['furniture.mrp.production']
            ._fields['production_lane']
            ._description_selection(self.env)
        )
        self.assertEqual(labels['frame'], 'نجارة')
        self.assertEqual(labels['body'], 'نجارة (قديم)')
        self.assertNotIn('التقديم والتجميع', labels.values())
        self.assertFalse(any('الجسم' in label for label in labels.values()))

    def test_production_kanban_displays_every_current_lane_name(self):
        kanban_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_kanban',
        )
        kanban_arch = self.env['furniture.mrp.production'].with_user(
            self.factory_manager_no_stock,
        ).get_view(
            view_id=kanban_view.id,
            view_type='kanban',
        )['arch']
        kanban_document = etree.fromstring(kanban_arch.encode())

        lane_labels = {
            'frame': 'نجارة',
            'finish': 'القواعد والتجهيز',
            'tailoring': 'التفصيل والخياطة',
            'painting': 'تصنيع الدهانات',
            'upholstery': 'الكسوة',
            'packaging': 'التغليف',
        }
        for lane, label in lane_labels.items():
            lane_nodes = kanban_document.xpath(
                ".//t[contains(@t-if, \"record.production_lane.raw_value "
                f"== '{lane}'\") or contains(@t-elif, "
                f"\"record.production_lane.raw_value == '{lane}'\")]"
            )
            self.assertEqual(len(lane_nodes), 1, lane)
            self.assertIn(label, ''.join(lane_nodes[0].itertext()))

        self.assertNotIn('المسار القديم', ''.join(kanban_document.itertext()))

    def _create_ready_output(self, lane, unit_cost, quantity=2.0, bom=False):
        production, line = self._create_lane_production(
            lane,
            quantity=quantity,
            bom=bom,
        )
        return self._create_ready_output_for_line(
            production,
            line,
            lane,
            unit_cost,
            quantity=quantity,
            bom=bom,
        )

    def _create_ready_output_for_line(
        self,
        production,
        line,
        lane,
        unit_cost,
        quantity=2.0,
        bom=False,
    ):
        final_product = line.product_id
        furniture_model = line.furniture_order_model_id
        wip_product = self.env[
            'product.product'
        ]._furniture_get_or_create_lane_wip_product(
            self.env.company,
            lane,
            final_product,
            furniture_model,
        )
        ready_stage = {
            'frame': 'carpentry',
            'finish': 'finishing',
            'tailoring': 'tailoring',
            'upholstery': 'upholstery',
            'body': 'finishing',
            'cover': 'upholstery',
        }[lane]
        source_location = production._stage_storage_location(ready_stage)
        receipt = production._create_internal_move(
            production._get_production_location(),
            source_location,
            'Dual lane WIP receipt',
            product=wip_product,
            quantity=quantity,
            uom=wip_product.uom_id,
            source_production_line=line,
            price_unit=unit_cost,
        )
        output = self.env['furniture.mrp.lane.output'].create({
            'company_id': self.env.company.id,
            'lane': lane,
            'production_id': production.id,
            'production_line_id': line.id,
            'final_product_id': final_product.id,
            'wip_product_id': wip_product.id,
            'furniture_model_id': furniture_model.id,
            'uom_id': wip_product.uom_id.id,
            'source_location_id': source_location.id,
            'origin_receipt_move_id': receipt.id,
            'ready_move_id': receipt.id,
            'qty_ready': quantity,
            'unit_cost': unit_cost,
        })
        return output

    def test_lane_routes_are_exact_and_legacy_default_is_unchanged(self):
        legacy = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        self.assertEqual(legacy.production_lane, 'legacy')

        forged_body = self.env['furniture.mrp.production'].with_context(
            furniture_lane_route_normalizing=True,
        ).create({
            'production_lane': 'body',
            'product_qty': 1.0,
            'use_priming': False,
            'use_painting': True,
            'use_packaging': True,
        })
        self.assertTrue(forged_body.use_priming)
        self.assertFalse(forged_body.use_painting)
        self.assertFalse(forged_body.use_packaging)

        body, body_line = self._create_lane_production('body')
        cover, cover_line = self._create_lane_production('cover')
        self.assertEqual(
            body._required_stage_codes(),
            ['priming', 'carpentry', 'bases', 'finishing'],
        )
        self.assertEqual(
            cover._required_stage_codes(),
            ['upholstery', 'tailoring'],
        )
        self.assertEqual(
            body_line._selected_stage_codes(),
            ['priming', 'carpentry', 'bases', 'finishing'],
        )
        self.assertEqual(
            cover_line._selected_stage_codes(),
            ['upholstery', 'tailoring'],
        )
        self.assertFalse(body.use_painting)
        self.assertFalse(body.use_packaging)
        self.assertFalse(cover.use_finishing)
        self.assertFalse(cover.use_packaging)

        expected_component_routes = {
            'frame': ['priming', 'carpentry'],
            'finish': ['bases', 'finishing'],
            'tailoring': ['tailoring'],
            'upholstery': ['upholstery'],
        }
        for lane, expected_route in expected_component_routes.items():
            production, line = self._create_lane_production(lane)
            self.assertEqual(production._required_stage_codes(), expected_route)
            self.assertEqual(line._selected_stage_codes(), expected_route)

        # A client-supplied context flag can never unlock lane routing.
        body_line.write({'first_stage_started': True})
        with self.assertRaises(UserError):
            body.with_context(
                furniture_lane_route_normalizing=True,
            ).write({'production_lane': 'cover'})
        body_line.with_context(
            furniture_lane_route_normalizing=True,
        ).write({
            'use_priming': False,
            'use_painting': True,
            'use_packaging': True,
        })
        body.with_context(
            furniture_lane_route_normalizing=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'use_priming': False,
            'use_painting': True,
            'use_packaging': True,
        })
        self.assertTrue(body.use_priming)
        self.assertFalse(body.use_painting)
        self.assertFalse(body.use_packaging)
        self.assertTrue(body_line.use_priming)
        self.assertFalse(body_line.use_painting)
        self.assertFalse(body_line.use_packaging)

    def test_lane_output_specs_use_stable_technical_wip_identity(self):
        body, line = self._create_lane_production('body')
        first_specs = body._get_finished_output_specs_from_lines(line)
        second_specs = body._get_finished_output_specs_from_lines(line)
        self.assertEqual(len(first_specs), 1)
        self.assertEqual(first_specs[0]['product'], second_specs[0]['product'])
        wip_product = first_specs[0]['product']
        self.assertEqual(wip_product.furniture_wip_lane, 'body')
        self.assertEqual(
            wip_product.furniture_wip_final_product_id,
            first_specs[0]['final_product'],
        )
        self.assertEqual(
            wip_product.furniture_wip_model_id,
            self.furniture_model,
        )
        self.assertFalse(wip_product.sale_ok)
        self.assertFalse(wip_product.purchase_ok)
        forged_identity = {
            'furniture_wip_lane': 'body',
            'furniture_wip_final_product_id': self.final_product.id,
            'furniture_wip_model_id': self.furniture_model.id,
            'furniture_wip_company_id': self.env.company.id,
        }
        # Neither sudo nor a caller-invented context key can forge technical
        # WIP identity through the public product ORM surface.
        with self.assertRaises(AccessError):
            self.env['product.product'].sudo().with_context(
                furniture_internal_wip_identity=True,
            ).create({
                'name': 'Forged WIP product',
                **forged_identity,
            })
        with self.assertRaises(AccessError):
            self.final_product.sudo().with_context(
                furniture_internal_wip_identity=True,
            ).write(forged_identity)
        with self.assertRaises(AccessError):
            wip_product.sudo().with_context(
                furniture_internal_wip_identity=True,
            ).write({'furniture_wip_lane': 'cover'})
        with self.assertRaises(UserError):
            body.action_transfer_finished_product()

    def test_completed_body_lane_wip_is_handed_to_carpentry(self):
        carpentry_supervisor = new_test_user(
            self.env,
            login='dual_lane_carpentry_handoff_supervisor',
            groups='base.group_user',
        )
        carpentry_stage = self.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'carpentry'),
        ], limit=1)
        self.env['hr.employee'].create({
            'name': 'Dual lane carpentry handoff supervisor',
            'user_id': carpentry_supervisor.id,
            'company_id': self.env.company.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [
                (6, 0, carpentry_stage.ids),
            ],
        })
        body, line = self._create_lane_production('body', quantity=3.0)
        body._ensure_stage_locations()
        priming = self.env['furniture.mrp.priming'].create({
            'name': 'PRIMING/%s' % body.name,
            'production_order_id': body.id,
            'state': 'done',
        })
        carpentry = self.env['furniture.mrp.carpentry'].create({
            'name': 'CARPENTRY/%s' % body.name,
            'production_order_id': body.id,
            'state': 'pending',
        })
        body.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'priming_order_id': priming.id,
            'carpentry_order_id': carpentry.id,
        })
        priming.with_context(
            furniture_skip_line_consolidation=True,
        )._set_stage_line_ids_data(
            'completed_production_line_ids_data', line,
        )
        wip_product = body._get_finished_output_specs_from_lines(
            line,
        )[0]['product']
        body._create_internal_move(
            body._get_production_location(),
            body.location_priming_id,
            'Accepted lane WIP in priming store',
            product=wip_product,
            quantity=line.product_qty,
            uom=wip_product.uom_id,
            source_production_line=line,
        )

        moves = body._auto_transfer_completed_stage_lines('priming', line)

        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.product_id, wip_product)
        self.assertEqual(moves.location_id, body.location_priming_id)
        self.assertEqual(
            moves.location_dest_id,
            body.location_carpentry_wip_id,
        )
        self.assertEqual(moves.furniture_source_production_line_id, line)
        self.assertAlmostEqual(moves.quantity, 3.0)
        self.assertEqual(moves.state, 'assigned')
        handoff = moves.furniture_stage_transfer_handoff_id
        self.assertEqual(handoff.state, 'pending')
        self.assertEqual(handoff.target_stage, 'carpentry')
        self.assertNotIn(
            line,
            body._get_stage_pending_start_line_candidates(
                carpentry, 'carpentry',
            ),
        )

        body.with_user(carpentry_supervisor).action_accept_handoff_transfer(
            stage_handoff_id=handoff.id,
        )
        moves.invalidate_recordset(['state'])
        self.assertEqual(moves.state, 'done')
        self.assertIn(
            line,
            body._get_stage_pending_start_line_candidates(
                carpentry, 'carpentry',
            ),
        )
        self.assertFalse(
            body._auto_transfer_completed_stage_lines('priming', line)
        )

    def test_reserve_and_complete_consumes_both_lanes_once(self):
        body = self._create_ready_output('body', 100.0)
        cover = self._create_ready_output('cover', 40.0)
        assembly = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body.id,
            'cover_output_id': cover.id,
            'quantity': 1.0,
        })

        assembly.action_reserve()
        self.assertEqual(assembly.state, 'reserved')
        self.assertEqual(len(assembly.allocation_ids), 2)
        self.assertTrue(all(
            move.state == 'assigned' for move in assembly.component_move_ids
        ))
        body.invalidate_recordset()
        cover.invalidate_recordset()
        self.assertAlmostEqual(body.available_qty, 1.0)
        self.assertAlmostEqual(cover.available_qty, 1.0)

        self.assertIs(assembly.action_complete(), True)
        assembly.invalidate_recordset()
        first_move = assembly.final_move_id
        self.assertEqual(assembly.state, 'done')
        self.assertEqual(assembly.final_move_id.state, 'done')
        self.assertEqual(
            assembly.final_move_id.furniture_final_assembly_role,
            'final',
        )
        self.assertAlmostEqual(assembly.unit_cost, 140.0)
        self.assertAlmostEqual(
            assembly.final_move_id.furniture_actual_unit_cost,
            140.0,
        )

        # Retrying completion is idempotent and must never create a second
        # finished receipt.
        self.assertIs(assembly.action_complete(), True)
        self.assertEqual(assembly.final_move_id, first_move)
        self.assertEqual(self.env['stock.move'].search_count([
            ('furniture_final_assembly_id', '=', assembly.id),
            ('furniture_final_assembly_role', '=', 'final'),
        ]), 1)

    def test_four_component_outputs_auto_assemble_fifo_finished_product(self):
        outputs = {
            'frame': self._create_ready_output('frame', 60.0, quantity=2.0),
            'finish': self._create_ready_output('finish', 25.0, quantity=2.0),
            'tailoring': self._create_ready_output(
                'tailoring', 10.0, quantity=2.0,
            ),
            'upholstery': self._create_ready_output(
                'upholstery', 35.0, quantity=2.0,
            ),
        }
        assemblies = self.env[
            'furniture.mrp.lane.output'
        ]._furniture_auto_assemble_ready_components(
            company=self.env.company,
            final_product=self.final_product,
            furniture_model=self.furniture_model,
            maximum_quantity=2.0,
        )
        self.assertEqual(len(assemblies), 1)

        assembly = self.env['furniture.mrp.final.assembly'].search([
            ('frame_output_id', '=', outputs['frame'].id),
            ('finish_output_id', '=', outputs['finish'].id),
            ('tailoring_output_id', '=', outputs['tailoring'].id),
            ('upholstery_output_id', '=', outputs['upholstery'].id),
        ]).ensure_one()
        self.assertEqual(assembly.state, 'done')
        self.assertEqual(assembly.quantity, 2.0)
        self.assertEqual(assembly.unit_cost, 130.0)
        self.assertEqual(len(assembly.allocation_ids), 4)
        self.assertEqual(set(assembly.allocation_ids.mapped('lane')), {
            'frame', 'finish', 'tailoring', 'upholstery',
        })
        self.assertEqual(set(assembly.component_move_ids.mapped(
            'furniture_final_assembly_role'
        )), {'frame', 'finish', 'tailoring', 'upholstery'})
        for output in outputs.values():
            output.invalidate_recordset()
            self.assertEqual(output.state, 'exhausted')
            self.assertEqual(output.available_qty, 0.0)
        self.assertEqual(assembly.final_move_id.state, 'done')
        self.assertEqual(assembly.final_move_id.product_id, self.final_product)
        self.assertEqual(assembly.final_move_id.quantity, 2.0)

    def test_dashboard_pool_replaces_completion_menus_and_order_button(self):
        for xmlid in (
            'furniture_mrp.menu_furniture_mrp_completion_pool',
            'furniture_mrp.menu_furniture_mrp_final_assembly',
        ):
            self.assertFalse(self.env.ref(xmlid).active)

        production_form = self.env.ref(
            'furniture_mrp.view_furniture_mrp_production_form',
        )
        form_arch = self.env['furniture.mrp.production'].with_user(
            self.factory_manager_no_stock,
        ).get_view(
            view_id=production_form.id,
            view_type='form',
        )['arch']
        form_document = etree.fromstring(form_arch.encode())
        self.assertFalse(form_document.xpath(
            ".//button[@name='action_open_final_assembly_wizard']"
        ))
        completion_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_completion_pool',
        )
        self.assertIn("('lane', '=', 'body')", completion_action.domain)
        self.assertIn("('completable_qty', '>', 0)", completion_action.domain)
        self.assertIn('search_default_completable', completion_action.context)
        self.assertFalse(form_document.xpath(
            ".//div[contains(@class, 'o_furniture_lane_selector')]"
        ))
        lane_fields = form_document.xpath(
            ".//div[contains(@class, 'o_furniture_order_model_hero')]"
            "/field[@name='production_lane']"
        )
        self.assertEqual(len(lane_fields), 1)
        self.assertEqual(lane_fields[0].get('widget'), 'badge')
        self.assertEqual(lane_fields[0].get('readonly'), '1')
        self.assertIn('o_furniture_lane_header_badge', lane_fields[0].get('class'))
        self.assertFalse(form_document.xpath(
            ".//field[@name='production_lane'][@widget='radio']"
        ))

        body = self._create_ready_output('body', 100.0, quantity=2.0)
        body.production_id.invalidate_recordset(['can_open_final_assembly'])
        self.assertFalse(body.production_id.can_open_final_assembly)
        with self.assertRaises(UserError):
            body.production_id.action_open_final_assembly_wizard()

        cover = self._create_ready_output('cover', 40.0, quantity=2.0)
        body.production_id.invalidate_recordset(['can_open_final_assembly'])
        cover.production_id.invalidate_recordset(['can_open_final_assembly'])
        self.assertTrue(body.production_id.can_open_final_assembly)
        self.assertTrue(cover.production_id.can_open_final_assembly)

        action = body.production_id.action_open_final_assembly_wizard()
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.final.assembly.wizard',
        )
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['context']['default_body_output_id'], body.id)
        self.assertEqual(action['context']['default_quantity'], 2.0)

    def test_last_finished_product_auto_closes_both_multi_product_lane_orders(self):
        second_product = self.env['product.product'].create({
            'name': 'Dual Lane Finished Chair',
            'type': 'consu',
            'is_storable': True,
        })
        body_production, body_first_line = self._create_lane_production(
            'body', quantity=2.0,
        )
        cover_production, cover_first_line = self._create_lane_production(
            'cover', quantity=2.0,
        )
        line_context = {
            'furniture_skip_material_refresh': True,
            'furniture_skip_stage_plan_sync': True,
            'furniture_skip_line_consolidation': True,
        }
        body_second_line = self.env['furniture.mrp.production.line'].with_context(
            **line_context,
        ).create({
            'production_id': body_production.id,
            'product_id': second_product.id,
            'furniture_order_model_id': self.furniture_model.id,
            'product_qty': 1.0,
        })
        cover_second_line = self.env['furniture.mrp.production.line'].with_context(
            **line_context,
        ).create({
            'production_id': cover_production.id,
            'product_id': second_product.id,
            'furniture_order_model_id': self.furniture_model.id,
            'product_qty': 1.0,
        })
        body_first = self._create_ready_output_for_line(
            body_production, body_first_line, 'body', 100.0, quantity=2.0,
        )
        cover_first = self._create_ready_output_for_line(
            cover_production, cover_first_line, 'cover', 40.0, quantity=2.0,
        )
        body_second = self._create_ready_output_for_line(
            body_production, body_second_line, 'body', 70.0, quantity=1.0,
        )
        cover_second = self._create_ready_output_for_line(
            cover_production, cover_second_line, 'cover', 30.0, quantity=1.0,
        )
        (body_production | cover_production).write({'state': 'in_production'})

        first_assembly = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body_first.id,
            'cover_output_id': cover_first.id,
            'quantity': 2.0,
        })
        first_assembly.action_complete()
        self.assertEqual(body_production.state, 'in_production')
        self.assertEqual(cover_production.state, 'in_production')

        second_assembly = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body_second.id,
            'cover_output_id': cover_second.id,
            'quantity': 1.0,
        })
        second_assembly.action_complete()
        self.assertEqual(body_production.state, 'done')
        self.assertEqual(cover_production.state, 'done')
        self.assertTrue(body_production.date_finish)
        self.assertTrue(cover_production.date_finish)
        self.assertTrue(all(
            output.state == 'exhausted'
            for output in (
                body_first | cover_first | body_second | cover_second
            )
        ))

    def test_reservation_prevents_oversubscription_and_cancel_releases_pool(self):
        body = self._create_ready_output('body', 80.0, quantity=1.0)
        cover = self._create_ready_output('cover', 20.0, quantity=1.0)
        first = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body.id,
            'cover_output_id': cover.id,
            'quantity': 1.0,
        })
        first.action_reserve()
        body.invalidate_recordset()
        cover.invalidate_recordset()
        self.assertEqual(body.state, 'exhausted')
        self.assertEqual(cover.state, 'exhausted')

        second = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body.id,
            'cover_output_id': cover.id,
            'quantity': 1.0,
        })
        with self.assertRaises(UserError):
            second.action_reserve()

        first.action_cancel()
        body.invalidate_recordset()
        cover.invalidate_recordset()
        self.assertAlmostEqual(body.available_qty, 1.0)
        self.assertAlmostEqual(cover.available_qty, 1.0)
        second.action_reserve()
        self.assertEqual(second.state, 'reserved')

    def test_physical_stock_is_allocated_once_across_fifo_pool_rows(self):
        first = self._create_ready_output('body', 80.0, quantity=1.0)
        second = self._create_ready_output('body', 90.0, quantity=1.0)
        self.assertEqual(first.wip_product_id, second.wip_product_id)
        self.assertEqual(first.source_location_id, second.source_location_id)

        # Two pool rows claim one unit each, but only one unit remains in the
        # strict untracked stock bucket.  The physical cap must be shared once,
        # in FIFO order, rather than duplicated on both rows.
        first.production_id._create_internal_move(
            first.source_location_id,
            first.production_id._get_production_location(),
            'Consume one unit outside final assembly',
            product=first.wip_product_id,
            quantity=1.0,
            uom=first.uom_id,
            source_production_line=first.production_line_id,
            price_unit=first.unit_cost,
        )
        fifo_rows = first | second
        fifo_rows._compute_allocation_quantities()
        fifo_rows._compute_physical_available_qty()
        self.assertAlmostEqual(sum(fifo_rows.mapped('physical_available_qty')), 1.0)
        self.assertAlmostEqual(first.physical_available_qty, 1.0)
        self.assertAlmostEqual(second.physical_available_qty, 0.0)

    def test_outputs_from_different_recipes_never_match(self):
        Bom = self.env['mrp.bom']
        common_vals = {
            'product_tmpl_id': self.final_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': self.final_product.uom_id.id,
            'type': 'normal',
            'active': False,
            'furniture_product_id': self.final_product.id,
            'furniture_recipe_model_id': self.furniture_model.id,
        }
        body_master = Bom.create(dict(common_vals))
        cover_master = Bom.create(dict(common_vals))
        body_bom = body_master._find_hidden_furniture_model_recipe(
            self.furniture_model,
        )
        cover_bom = cover_master._find_hidden_furniture_model_recipe(
            self.furniture_model,
        )
        self.assertTrue(body_bom)
        self.assertTrue(cover_bom)
        body = self._create_ready_output('body', 100.0, quantity=1.0, bom=body_bom)
        cover = self._create_ready_output('cover', 40.0, quantity=1.0, bom=cover_bom)

        self.assertNotEqual(body.bom_id, cover.bom_id)
        self.assertAlmostEqual(body.matching_available_qty, 0.0)
        self.assertAlmostEqual(cover.matching_available_qty, 0.0)
        with self.assertRaises(ValidationError):
            self.env['furniture.mrp.final.assembly'].create({
                'body_output_id': body.id,
                'cover_output_id': cover.id,
                'quantity': 1.0,
            })

    def test_read_only_operator_completes_through_wizard_and_cannot_forge_audit(self):
        body = self._create_ready_output('body', 70.0, quantity=2.0)
        cover = self._create_ready_output('cover', 30.0, quantity=2.0)
        wizard = self.env['furniture.mrp.final.assembly.wizard'].with_user(
            self.completion_operator,
        ).create({
            'company_id': self.env.company.id,
            'final_product_id': body.final_product_id.id,
            'furniture_model_id': body.furniture_model_id.id,
            'bom_id': body.bom_id.id,
            'uom_id': body.uom_id.id,
            'quantity': 1.0,
            'body_output_id': body.id,
            'cover_output_id': cover.id,
        })
        action = wizard.action_complete()
        assembly = self.env['furniture.mrp.final.assembly'].sudo().browse(
            action['res_id'],
        )
        self.assertEqual(assembly.state, 'done')
        self.assertEqual(assembly.completed_by_id, self.completion_operator)
        self.assertEqual(assembly.final_move_id.state, 'done')
        with self.assertRaises(AccessError):
            assembly.with_user(self.completion_operator).read(['unit_cost'])
        with self.assertRaises(AccessError):
            body.with_user(self.completion_operator).read(['unit_cost'])

        self.assertFalse(self.factory_manager_no_stock.has_group(
            'stock.group_stock_user',
        ))
        assembly_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_final_assembly_form',
        )
        manager_arch = self.env['furniture.mrp.final.assembly'].with_user(
            self.factory_manager_no_stock,
        ).get_view(
            view_id=assembly_view.id,
            view_type='form',
        )['arch']
        manager_document = etree.fromstring(manager_arch.encode())
        self.assertFalse(manager_document.xpath(
            ".//field[@name='final_move_id' or @name='component_move_ids']",
        ))
        self.assertTrue(manager_document.xpath(".//field[@name='unit_cost']"))

        operator_arch = self.env['furniture.mrp.final.assembly'].with_user(
            self.completion_operator,
        ).get_view(
            view_id=assembly_view.id,
            view_type='form',
        )['arch']
        operator_document = etree.fromstring(operator_arch.encode())
        self.assertFalse(operator_document.xpath(
            ".//field[@name='unit_cost' or @name='total_cost' "
            "or @name='final_move_id' or @name='component_move_ids']",
        ))

        # Existing assembly buttons also run with read-only ACLs; their stock
        # and pool writes must stay inside the verified private workflow.
        manual_assembly = self.env['furniture.mrp.final.assembly'].create({
            'body_output_id': body.id,
            'cover_output_id': cover.id,
            'quantity': 1.0,
        })
        manual_as_operator = manual_assembly.with_user(self.completion_operator)
        manual_as_operator.action_reserve()
        self.assertEqual(manual_assembly.state, 'reserved')

        unrelated_move_vals = {
            'name': 'Unrelated stock move',
            'product_id': body.wip_product_id.id,
            'product_uom': body.uom_id.id,
            'product_uom_qty': 1.0,
            'location_id': body.source_location_id.id,
            'location_dest_id': body.production_id._get_production_location().id,
            'company_id': self.env.company.id,
        }
        unrelated_move = self.env['stock.move'].sudo().create(
            unrelated_move_vals,
        )
        forged_move_identity = {
            'furniture_final_assembly_id': manual_assembly.id,
            'furniture_final_assembly_role': 'body',
        }
        with self.assertRaises(AccessError):
            self.env['stock.move'].sudo().with_context(
                furniture_internal_final_assembly_move=True,
            ).create({**unrelated_move_vals, **forged_move_identity})
        with self.assertRaises(AccessError):
            unrelated_move.sudo().with_context(
                furniture_internal_final_assembly_move=True,
            ).write(forged_move_identity)

        manual_as_operator.action_cancel()
        self.assertEqual(manual_assembly.state, 'cancelled')
        self.assertEqual(unrelated_move.state, 'draft')
        reserve_message = manual_assembly.message_ids.filtered(
            lambda message: '🔒' in (message.body or '')
        )[:1]
        cancel_message = manual_assembly.message_ids.filtered(
            lambda message: '❌' in (message.body or '')
        )[:1]
        self.assertEqual(reserve_message.author_id, self.completion_operator.partner_id)
        self.assertEqual(cancel_message.author_id, self.completion_operator.partner_id)

        with self.assertRaises(AccessError):
            assembly.with_user(self.completion_operator).with_context(
                furniture_internal_assembly_write=True,
            ).write({'state': 'cancelled'})
        with self.assertRaises(AccessError):
            self.env['furniture.mrp.final.assembly.allocation'].with_user(
                self.completion_operator,
            ).with_context(
                furniture_internal_assembly_allocation=True,
            ).create({
                'assembly_id': assembly.id,
                'lane': 'body',
                'output_id': body.id,
                'qty': 1.0,
                'consumption_move_id': assembly.component_move_ids[:1].id,
            })
