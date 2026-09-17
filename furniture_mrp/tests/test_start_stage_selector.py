from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user


STAGE_USE_FIELDS = {
    'priming': 'use_priming',
    'painting': 'use_painting',
    'carpentry': 'use_carpentry',
    'bases': 'use_bases',
    'finishing': 'use_finishing',
    'tailoring': 'use_tailoring',
    'upholstery': 'use_upholstery',
    'packaging': 'use_packaging',
}


class TestStartStageSelector(TransactionCase):
    """Regression coverage for explicitly choosing an order's first stage.

    These tests intentionally use a route where priming sorts before packaging,
    then choose packaging.  A selector that merely delegates to the historical
    ``selected_stages[0]`` behavior will therefore fail this suite.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.storekeeper = new_test_user(
            cls.env,
            login='furniture_start_stage_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.worker_user = new_test_user(
            cls.env,
            login='furniture_arbitrary_start_worker',
            groups='base.group_user',
        )
        cls.readonly_user = new_test_user(
            cls.env,
            login='furniture_start_stage_readonly',
            groups='base.group_user',
        )
        worker_stage = cls.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'packaging'),
        ], limit=1)
        if not worker_stage:
            worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                'name': 'Test Packaging',
                'code': 'packaging',
            })
        finishing_worker_stage = cls.env['furniture.mrp.employee.stage'].search([
            ('code', '=', 'finishing'),
        ], limit=1)
        if not finishing_worker_stage:
            finishing_worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                'name': 'Test Finishing',
                'code': 'finishing',
            })
        worker_stage |= finishing_worker_stage
        cls.worker = cls.env['hr.employee'].create({
            'name': 'Arbitrary Start Packaging Worker',
            'user_id': cls.worker_user.id,
            'furniture_mrp_role': 'worker',
            'furniture_mrp_worker_stage_ids': [(6, 0, worker_stage.ids)],
        })

    def _stage_values(self, active_codes):
        active_codes = set(active_codes)
        return {
            field_name: stage_code in active_codes
            for stage_code, field_name in STAGE_USE_FIELDS.items()
        }

    def _create_product_and_bom(self, name, route):
        product = self.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            **self._stage_values(route),
        })
        return product, bom

    def _create_production_with_lines(self, routes):
        production = self.env['furniture.mrp.production'].create({
            'product_qty': float(len(routes) or 1),
        })
        lines = self.env['furniture.mrp.production.line']
        for index, route in enumerate(routes, start=1):
            product, bom = self._create_product_and_bom(
                'Arbitrary Start Product %s' % index,
                route,
            )
            line = self.env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
            ).create({
                'production_id': production.id,
                'sequence': index * 10,
                'product_id': product.id,
                'bom_id': bom.id,
                'product_qty': 1.0,
                **self._stage_values(route),
            })
            lines |= line
        production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'state': 'confirmed',
            **self._stage_values({
                stage_code
                for route in routes
                for stage_code in route
            }),
        })
        return production, lines

    def _open_selector(self, production):
        action = production.action_open_start_stage_selector()
        expected_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_start_stage_wizard_form'
        )
        wizard = self.env[action['res_model']].with_context(
            action.get('context', {}),
        ).browse(action['res_id']).exists()
        self.assertTrue(wizard)
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action.get('view_id'), expected_view.id)
        self.assertEqual(action.get('views'), [(expected_view.id, 'form')])
        self.assertEqual(
            action['context'].get('default_production_id'),
            production.id,
        )
        self.assertEqual(
            action['context'].get('form_view_initial_mode'),
            'edit',
        )
        return action, wizard

    def _selection_codes(self, wizard):
        description = wizard.fields_get(allfields=['stage_code'])['stage_code']
        return [stage_code for stage_code, _label in description['selection']]

    def _choose_stage(self, production, stage_code):
        _action, wizard = self._open_selector(production)
        wizard.stage_code = stage_code
        result = wizard.action_start_selected_stage()
        return wizard, result

    def _request_first_stage(self, stage_order):
        action = stage_order.sudo().action_request_store_approval()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.first.stage.start.wizard',
        )
        self.assertEqual(action['target'], 'new')
        self.assertTrue(action['context']['furniture_store_request_only'])
        self.assertTrue(action['context']['default_request_only'])
        wizard = self.env[action['res_model']].with_context(
            action.get('context', {}),
        ).browse(action['res_id']).exists()
        self.assertTrue(wizard)
        self.assertTrue(wizard.request_only)
        return wizard

    def test_selector_lists_every_unstarted_route_stage(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'packaging'),
            ('tailoring',),
        ])

        _action, wizard = self._open_selector(production)

        self.assertEqual(
            self._selection_codes(wizard),
            ['priming', 'tailoring', 'packaging'],
        )
        self.assertTrue(all(not line.planned_start_stage for line in lines))

    def test_new_stages_create_operational_orders_and_store_requests(self):
        for stage_code, model_name in (
            ('bases', 'furniture.mrp.bases'),
            ('packaging', 'furniture.mrp.packaging'),
        ):
            production, lines = self._create_production_with_lines([
                (stage_code,),
            ])

            _wizard, action = self._choose_stage(production, stage_code)
            stage_order = production['%s_order_id' % stage_code]

            self.assertTrue(stage_order)
            self.assertEqual(stage_order._name, model_name)
            self.assertEqual(action['res_model'], model_name)
            self.assertEqual(lines.planned_start_stage, stage_code)

            request_wizard = self._request_first_stage(stage_order)
            self.assertEqual(request_wizard.stage_code, stage_code)
            self.assertEqual(
                request_wizard.line_ids.mapped('production_line_id'),
                lines,
            )

    def test_arbitrary_choice_plans_only_matching_lines_and_opens_stage(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'packaging'),
            ('priming',),
        ])
        packaging_line, priming_only_line = lines.sorted('sequence')
        move_count = self.env['stock.move'].search_count([])
        request_count = self.env['furniture.mrp.store.request'].search_count([])

        _wizard, result = self._choose_stage(production, 'packaging')
        production.invalidate_recordset([
            'state', 'packaging_order_id', 'priming_order_id',
        ])
        lines.invalidate_recordset(['planned_start_stage'])

        self.assertEqual(packaging_line.planned_start_stage, 'packaging')
        self.assertFalse(priming_only_line.planned_start_stage)
        self.assertEqual(production.state, 'in_production')
        self.assertTrue(production.packaging_order_id)
        self.assertFalse(production.priming_order_id)
        self.assertEqual(production.packaging_order_id.state, 'pending')
        self.assertEqual(result['type'], 'ir.actions.act_window')
        self.assertEqual(result['res_model'], 'furniture.mrp.packaging')
        self.assertEqual(result['res_id'], production.packaging_order_id.id)
        self.assertEqual(result['target'], 'current')
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)
        self.assertEqual(
            self.env['furniture.mrp.store.request'].search_count([]),
            request_count,
        )

        self.assertEqual(
            production._get_first_stage_start_line_candidates('packaging'),
            packaging_line,
        )
        self.assertEqual(
            production._get_first_stage_start_line_candidates('priming'),
            priming_only_line,
        )

    def test_planned_stage_flows_into_first_stage_store_request(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'packaging'),
            ('priming',),
        ])
        packaging_line = lines.sorted('sequence')[0]
        self._choose_stage(production, 'packaging')
        stage_order = production.packaging_order_id
        move_count = self.env['stock.move'].search_count([])

        first_stage_wizard = self._request_first_stage(stage_order)
        self.assertEqual(
            first_stage_wizard.line_ids.mapped('production_line_id'),
            packaging_line,
        )
        submit_action = first_stage_wizard.action_submit_store_request()
        request = self.env['furniture.mrp.store.request'].search([
            ('stage_order_model', '=', stage_order._name),
            ('stage_order_res_id', '=', stage_order.id),
        ], order='id desc', limit=1)

        self.assertTrue(request)
        self.assertEqual(request.state, 'pending')
        self.assertEqual(request.request_kind, 'first_stage')
        self.assertEqual(request.stage_code, 'packaging')
        self.assertEqual(
            request.payload_json['lines'][0]['production_line_id'],
            packaging_line.id,
        )
        self.assertEqual(submit_action['tag'], 'display_notification')
        self.assertEqual(
            submit_action['params']['next']['type'],
            'ir.actions.act_window_close',
        )
        self.assertEqual(stage_order.state, 'pending')
        self.assertFalse(packaging_line.first_stage_started)
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)

    def test_arbitrary_first_stage_quality_creates_its_own_output(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'finishing'),
            ('priming',),
        ])
        production_line = lines.sorted('sequence')[0]
        product = production_line.product_id
        foreign_production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        foreign_line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
        ).create({
            'production_id': foreign_production.id,
            'product_id': product.id,
            'bom_id': production_line.bom_id.id,
            'product_qty': 1.0,
            **self._stage_values(('finishing',)),
        })
        foreign_production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'state': 'confirmed',
            **self._stage_values(('finishing',)),
        })
        foreign_production._create_internal_move(
            foreign_production._get_production_location(),
            production.location_finishing_wip_id,
            'Foreign identical-product WIP isolation regression',
            product=product,
            quantity=1.0,
            uom=product.uom_id,
            source_production_line=foreign_line,
        )
        foreign_wip_qty = production._stage_location_product_qty(
            production.location_finishing_wip_id,
            product,
        )
        self._choose_stage(production, 'finishing')
        stage_order = production.finishing_order_id
        stage_order.worker_ids = [(6, 0, self.worker.ids)]

        first_stage_wizard = self._request_first_stage(stage_order)
        first_stage_wizard.action_submit_store_request()
        request = self.env['furniture.mrp.store.request'].search([
            ('stage_order_model', '=', stage_order._name),
            ('stage_order_res_id', '=', stage_order.id),
            ('state', '=', 'pending'),
        ], order='id desc', limit=1)
        root_user = self.env.ref('base.user_root')
        request = request.with_user(root_user)
        request.action_approve()
        start_action = request.action_start_approved()

        production_line.invalidate_recordset([
            'first_stage_started', 'first_stage_started_stage',
        ])
        self.assertEqual(start_action['res_model'], stage_order._name)
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertTrue(production_line.first_stage_started)
        self.assertEqual(
            production_line.first_stage_started_stage,
            'finishing',
        )

        stage_order = stage_order.with_user(root_user)
        stage_order.action_send_to_quality()
        self.assertEqual(stage_order.state, 'quality_check')
        stage_order.action_approve_quality()

        output_move = self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', product.id),
            ('location_id', '=', production._get_production_location().id),
            ('location_dest_id', '=', production.location_finishing_id.id),
            ('furniture_source_production_line_id', '=', production_line.id),
            ('state', '=', 'done'),
        ])
        self.assertTrue(output_move)
        self.assertEqual(
            production._stage_location_product_qty(
                production.location_finishing_wip_id,
                product,
            ),
            foreign_wip_qty,
        )
        self.assertFalse(self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', product.id),
            ('location_id', '=', production.location_finishing_wip_id.id),
            ('location_dest_id', '=', production.location_finishing_id.id),
            ('furniture_source_production_line_id', '=', production_line.id),
            ('state', '=', 'done'),
        ]))
        self.assertGreaterEqual(
            production._stage_location_product_qty(
                production.location_finishing_id,
                product,
            ),
            1.0,
        )
        self.assertIn(
            'finishing',
            production._production_line_completed_stage_codes(
                production_line,
                product=product,
            ),
        )
        self.assertNotIn(
            'priming',
            production._production_line_completed_stage_codes(
                production_line,
                product=product,
            ),
        )
        self.assertFalse(
            production._production_line_all_selected_stages_done(
                production_line,
                product=product,
            )
        )
        self.assertFalse(production._get_finished_transfer_current_payloads())
        production.invalidate_recordset(['can_transfer_finished_product'])
        self.assertFalse(production.can_transfer_finished_product)

    def test_quality_approval_routes_exact_line_to_next_stage_hall(self):
        carpentry_supervisor = new_test_user(
            self.env,
            login='quality.handoff.carpentry.supervisor@example.test',
            groups='base.group_user',
        )
        carpentry_employee_stage = self.env[
            'furniture.mrp.employee.stage'
        ].search([('code', '=', 'carpentry')], limit=1)
        self.env['hr.employee'].create({
            'name': 'Quality Handoff Carpentry Supervisor',
            'user_id': carpentry_supervisor.id,
            'company_id': self.env.company.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [
                (6, 0, carpentry_employee_stage.ids),
            ],
        })
        production, lines = self._create_production_with_lines([
            ('priming', 'carpentry'),
        ])
        production_line = lines.ensure_one()
        production.action_start_priming()
        stage_order = production.priming_order_id
        production_line.write({
            'planned_start_stage': 'priming',
            'first_stage_started': True,
            'first_stage_started_stage': 'priming',
        })
        stage_order.with_context(
            furniture_skip_line_consolidation=True,
        )._set_stage_line_ids_data(
            'active_production_line_ids_data', production_line,
        )
        stage_order.write({'state': 'in_progress'})

        root_user = self.env.ref('base.user_root')
        stage_order = stage_order.with_user(root_user)
        stage_order.action_send_to_quality()
        stage_order.action_approve_quality()

        output_product = (
            production._get_or_create_dimensioned_finished_product_for_line(
                production_line
            )
            or production_line.product_id
        )
        automatic_move = self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', output_product.id),
            ('location_id', '=', production.location_priming_id.id),
            (
                'location_dest_id', '=',
                production.location_carpentry_wip_id.id,
            ),
            (
                'furniture_source_production_line_id', '=',
                production_line.id,
            ),
            ('state', '=', 'assigned'),
        ])
        self.assertEqual(len(automatic_move), 1)
        handoff = automatic_move.furniture_stage_transfer_handoff_id
        self.assertEqual(handoff.state, 'pending')
        self.assertEqual(handoff.target_stage, 'carpentry')
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_priming_id,
                output_product,
                production_line,
                'priming',
            ),
            1.0,
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_carpentry_wip_id,
                output_product,
                production_line,
                'carpentry',
            ),
            0.0,
        )
        self.assertNotIn(
            production_line,
            production._get_stage_pending_start_line_candidates(
                False, 'carpentry',
            ),
        )
        pending_payloads = production.with_user(
            carpentry_supervisor
        ).furniture_pending_handoff_notifications()
        self.assertIn(
            handoff.id,
            [payload.get('handoff_id') for payload in pending_payloads],
        )

        production.with_user(
            carpentry_supervisor
        ).action_accept_handoff_transfer(
            stage_handoff_id=handoff.id,
        )
        handoff.invalidate_recordset(['state', 'accepted_by_id'])
        automatic_move.invalidate_recordset(['state'])
        self.assertEqual(handoff.state, 'accepted')
        self.assertEqual(handoff.accepted_by_id, carpentry_supervisor)
        resolved_payloads = production.with_user(
            carpentry_supervisor
        ).furniture_pending_handoff_notifications()
        self.assertNotIn(
            handoff.id,
            [payload.get('handoff_id') for payload in resolved_payloads],
        )
        self.assertEqual(automatic_move.state, 'done')
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_priming_id,
                output_product,
                production_line,
                'priming',
            ),
            0.0,
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_carpentry_wip_id,
                output_product,
                production_line,
                'carpentry',
            ),
            1.0,
        )
        self.assertIn(
            production_line,
            production._get_stage_pending_start_line_candidates(
                False, 'carpentry',
            ),
        )

    def test_partial_first_stage_split_keeps_planned_entry_stage(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'packaging'),
        ])
        production_line = lines.ensure_one()
        production_line.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({'product_qty': 4.0})
        self._choose_stage(production, 'packaging')

        remaining_line = production_line._split_for_partial_first_stage(1.5)

        self.assertTrue(remaining_line)
        self.assertEqual(production_line.product_qty, 1.5)
        self.assertEqual(remaining_line.product_qty, 2.5)
        self.assertEqual(production_line.planned_start_stage, 'packaging')
        self.assertEqual(remaining_line.planned_start_stage, 'packaging')

    def test_stale_or_route_invalid_selection_is_rejected(self):
        production, _lines = self._create_production_with_lines([
            ('priming', 'packaging'),
        ])
        _action, stale_wizard = self._open_selector(production)
        stale_wizard.stage_code = 'priming'
        production.action_start_priming()
        priming_order = production.priming_order_id
        priming_order_count = self.env['furniture.mrp.priming'].search_count([
            ('production_order_id', '=', production.id),
        ])

        with self.assertRaises(UserError):
            stale_wizard.action_start_selected_stage()
        self.assertEqual(production.priming_order_id, priming_order)
        self.assertEqual(
            self.env['furniture.mrp.priming'].search_count([
                ('production_order_id', '=', production.id),
            ]),
            priming_order_count,
        )

        invalid_wizard = self.env['furniture.mrp.start.stage.wizard'].create({
            'production_id': production.id,
            'stage_code': 'painting',
        })
        with self.assertRaises(UserError):
            invalid_wizard.action_start_selected_stage()
        self.assertFalse(production.painting_order_id)

    def test_reopened_default_stage_reserves_new_line_before_selector(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'packaging'),
        ])
        original_line = lines.ensure_one()
        production.action_start_priming()
        priming_order = production.priming_order_id
        original_line.write({
            'first_stage_started': True,
            'first_stage_started_stage': 'priming',
        })
        priming_order.write({
            'state': 'done',
            'quality_check': 'pass',
        })

        new_product, new_bom = self._create_product_and_bom(
            'New Running-Order Product',
            ('priming', 'packaging'),
        )
        new_line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
        ).create({
            'production_id': production.id,
            'sequence': 20,
            'product_id': new_product.id,
            'bom_id': new_bom.id,
            'product_qty': 1.0,
            **self._stage_values(('priming', 'packaging')),
        })

        pending_lines = production._reopen_stage_order_for_pending_start(
            priming_order,
            'priming',
        )
        new_line.invalidate_recordset(['planned_start_stage'])

        self.assertIn(new_line, pending_lines)
        self.assertEqual(priming_order.state, 'pending')
        self.assertEqual(new_line.planned_start_stage, 'priming')
        self.assertNotIn(
            new_line,
            production._get_start_stage_selector_line_candidates('packaging'),
        )
        self.assertNotIn('packaging', production._get_startable_stage_codes())

    def test_foreign_stage_hall_stock_does_not_unlock_selector(self):
        production, lines = self._create_production_with_lines([
            ('priming', 'finishing'),
        ])
        production_line = lines.ensure_one()
        production_line.write({
            'planned_start_stage': 'priming',
            'first_stage_started': True,
            'first_stage_started_stage': 'priming',
        })
        foreign_production, foreign_lines = self._create_production_with_lines([
            ('finishing',),
        ])
        foreign_line = foreign_lines.ensure_one()
        foreign_production._create_internal_move(
            foreign_production._get_production_location(),
            production.location_finishing_wip_id,
            'Foreign WIP for selector isolation',
            product=foreign_line.product_id,
            quantity=1.0,
            uom=foreign_line.product_uom_id,
            source_production_line=foreign_line,
        )

        self.assertNotIn(
            'finishing',
            production._get_startable_stage_codes(),
        )

    def test_selector_is_restricted_to_production_management(self):
        production, _lines = self._create_production_with_lines([
            ('packaging',),
        ])

        with self.assertRaises(AccessError):
            production.with_user(self.readonly_user).action_open_start_stage_selector()

    def test_production_form_exposes_start_stage_selector(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_production_form')
        admin = self.env.ref('base.user_admin')
        arch = self.env[view.model].with_user(admin).get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())
        buttons = document.xpath(
            "//header/button[@name='action_open_start_stage_selector']"
        )

        self.assertEqual(len(buttons), 1)
        self.assertIn(
            "state not in ('confirmed', 'in_production')",
            buttons[0].get('invisible'),
        )
        self.assertIn('not can_choose_start_stage', buttons[0].get('invisible'))
        raw_document = etree.fromstring(view.arch_db.encode())
        raw_button = raw_document.xpath(
            "//header/button[@name='action_open_start_stage_selector']"
        )[0]
        groups = raw_button.get('groups', '')
        self.assertIn('furniture_mrp.group_furniture_mrp_manager', groups)
        self.assertIn('furniture_mrp.group_furniture_mrp_supervisor', groups)
        transfer_buttons = document.xpath(
            "//header/button[@name='action_open_stage_transfer_wizard']"
        )
        self.assertEqual(len(transfer_buttons), 1)
        self.assertEqual(transfer_buttons[0].get('invisible'), '1')

    def test_start_stage_wizard_view_keeps_behavior_contract(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_start_stage_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())
        form = document.xpath('//form')[0]

        self.assertIn(
            'o_furniture_start_stage_wizard',
            form.get('class', '').split(),
        )
        self.assertEqual(len(document.xpath("//field[@name='production_id']")), 1)
        stage_fields = document.xpath("//field[@name='stage_code']")
        self.assertEqual(len(stage_fields), 1)
        self.assertEqual(stage_fields[0].get('widget'), 'radio')
        self.assertIn('horizontal', stage_fields[0].get('options', ''))
        self.assertEqual(
            len(document.xpath(
                "//footer/button[@name='action_start_selected_stage' "
                "and @type='object']"
            )),
            1,
        )
        self.assertEqual(
            len(document.xpath("//footer/button[@special='cancel']")),
            1,
        )
        for class_name in (
            'o_furniture_start_stage_meta',
            'o_furniture_start_stage_panel',
            'o_furniture_start_stage_note',
        ):
            self.assertEqual(
                len(document.xpath(
                    "//*[contains(concat(' ', normalize-space(@class), ' '), "
                    "' %s ')]" % class_name
                )),
                1,
            )
