from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user


MATERIAL_ONLY_STAGES = (
    ('painting', 'furniture.mrp.painting'),
    ('tailoring', 'furniture.mrp.tailoring'),
)


class TestMaterialOnlyStages(TransactionCase):
    """Material-only departments run in parallel with the furniture body."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.storekeeper = new_test_user(
            cls.env,
            login='material_only_stage_storekeeper',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_storekeeper'
            ),
        )
        cls.worker_user = new_test_user(
            cls.env,
            login='material_only_stage_worker',
            groups='base.group_user',
        )
        worker_stages = cls.env['furniture.mrp.employee.stage']
        for stage_code in (
            'priming',
            *[code for code, _model in MATERIAL_ONLY_STAGES],
        ):
            worker_stage = cls.env['furniture.mrp.employee.stage'].search([
                ('code', '=', stage_code),
            ], limit=1)
            if not worker_stage:
                worker_stage = cls.env['furniture.mrp.employee.stage'].create({
                    'name': 'Parallel Test %s' % stage_code,
                    'code': stage_code,
                })
            worker_stages |= worker_stage
        cls.worker = cls.env['hr.employee'].create({
            'name': 'Material-only Stage Worker',
            'user_id': cls.worker_user.id,
            'furniture_mrp_role': 'worker',
            'furniture_mrp_worker_stage_ids': [(6, 0, worker_stages.ids)],
        })

    def _new_storable_product(self, name, standard_price=0.0):
        return self.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'standard_price': standard_price,
        })

    def _create_production(
        self,
        physical_stage='upholstery',
        mark_physical_started=True,
    ):
        model = self.env['furniture.product.model'].create({
            'name': 'Material-only Parallel Model',
        })
        finished = self._new_storable_product('Parallel Finished Sofa')
        materials = {
            stage_code: self._new_storable_product(
                'Raw Material %s' % stage_code,
                standard_price=10.0,
            )
            for stage_code in (
                physical_stage,
                *[code for code, _model_name in MATERIAL_ONLY_STAGES],
            )
        }
        stage_values = {
            'use_priming': physical_stage == 'priming',
            'use_painting': True,
            'use_carpentry': False,
            'use_bases': False,
            'use_finishing': False,
            'use_tailoring': True,
            'use_upholstery': True,
            'use_packaging': True,
        }
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': finished.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': finished.id,
            'furniture_recipe_model_id': model.id,
            **stage_values,
            'furniture_stage_material_line_ids': [
                (0, 0, {
                    'stage': stage_code,
                    'product_id': material.id,
                    'product_qty': 1.0,
                    'product_uom_id': material.uom_id.id,
                    'quantity_mode': 'scaled',
                })
                for stage_code, material in materials.items()
            ],
        })
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 2.0,
            'date_planned_start': '2026-08-22 08:00:00',
            'date_planned_finish': '2026-08-22 17:00:00',
            # These tests isolate the material/physical lane mechanics.  The
            # production model exposes this explicit test-only duration so
            # unrelated MPS timing setup cannot block their stage starts.
            'temporary_stage_fixed_hours': 2.0,
        })
        production_line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': finished.id,
            'furniture_order_model_id': model.id,
            'bom_id': bom.id,
            'product_qty': 2.0,
            **stage_values,
        })
        production.action_confirm()
        if mark_physical_started:
            production_line.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            ).write({
                'first_stage_started': True,
                'first_stage_started_stage': physical_stage,
                'planned_start_stage': physical_stage,
            })
        stock = production.location_src_id or self.env.ref(
            'stock.stock_location_stock'
        )
        for material in materials.values():
            self.env['stock.quant']._update_available_quantity(
                material,
                stock,
                2.0,
            )
        return production, production_line, finished, materials

    def _approve_receive_and_start(self, request):
        root = self.env.ref('base.user_root')
        request = request.with_user(root)
        request.action_approve()
        request._confirm_production_receipt({
            line.id: line.issued_qty
            for line in request.material_line_ids
        })
        request.action_start_approved()
        return request

    def _start_material_stage(
        self,
        production,
        production_line,
        stage_code,
        model_name,
    ):
        getattr(production, 'action_start_%s' % stage_code)()
        stage_order = production['%s_order_id' % stage_code]
        self.assertEqual(stage_order._name, model_name)
        self.assertTrue(stage_order._has_startable_stage_work())
        stage_order.worker_ids = [(6, 0, self.worker.ids)]

        stage_order.action_request_store_approval()
        request = self.env['furniture.mrp.store.request'].search([
            ('stage_order_model', '=', model_name),
            ('stage_order_res_id', '=', stage_order.id),
        ], order='id desc', limit=1)
        self.assertTrue(request)
        self.assertEqual(request.request_kind, 'direct')
        self.assertEqual(
            request.payload_json['production_line_ids'],
            production_line.ids,
        )
        self.assertTrue(request.material_line_ids)
        self._approve_receive_and_start(request)

        stage_order.invalidate_recordset([
            'state',
            'active_production_line_ids_data',
        ])
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertEqual(
            stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            production_line,
        )
        return stage_order

    def _complete_stage(self, stage_order, production_line):
        root = self.env.ref('base.user_root')

        stage_order = stage_order.with_user(root)
        if stage_order._name == 'furniture.mrp.tailoring':
            for substage_code in ('cutting', 'sewing', 'ironing'):
                stage_order.with_context(
                    tailoring_internal_substage=substage_code,
                ).action_complete_internal_substage()
        elif stage_order._name == 'furniture.mrp.painting':
            for substage_code, _label, _state_field, _required_field in (
                stage_order._get_active_substage_fields()
            ):
                stage_with_substage = stage_order.with_context(
                    painting_internal_substage=substage_code,
                    painting_external_substage=substage_code,
                )
                if substage_code in ('cells', 'veneer', 'paint'):
                    stage_with_substage.action_external_substage_exit()
                    stage_with_substage.action_external_substage_deliver()
                    stage_with_substage.action_external_substage_receive()
                else:
                    stage_with_substage.action_complete_internal_substage()
        stage_order.action_send_to_quality()
        self.assertEqual(stage_order.state, 'quality_check')
        stage_order.action_approve_quality()
        stage_order.invalidate_recordset([
            'state',
            'completed_production_line_ids_data',
        ])
        self.assertEqual(stage_order.state, 'done')
        self.assertIn(
            production_line,
            stage_order._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ),
        )

    def _run_stage(self, production, production_line, stage_code, model_name):
        stage_order = self._start_material_stage(
            production,
            production_line,
            stage_code,
            model_name,
        )
        self._complete_stage(stage_order, production_line)

    def _start_physical_first_stage(
        self,
        production,
        production_line,
        stage_code,
    ):
        getattr(production, 'action_start_%s' % stage_code)()
        stage_order = production['%s_order_id' % stage_code]
        stage_order.worker_ids = [(6, 0, self.worker.ids)]
        action = stage_order.sudo().action_request_store_approval()
        self.assertEqual(action['res_model'], 'furniture.mrp.first.stage.start.wizard')
        wizard = self.env[action['res_model']].with_context(
            action.get('context', {}),
        ).browse(action['res_id'])
        self.assertEqual(
            wizard.line_ids.mapped('production_line_id'),
            production_line,
        )
        wizard.action_submit_store_request()
        request = self.env['furniture.mrp.store.request'].search([
            ('stage_order_model', '=', stage_order._name),
            ('stage_order_res_id', '=', stage_order.id),
            ('state', '=', 'pending'),
        ], order='id desc', limit=1)
        self.assertTrue(request)
        self.assertEqual(request.request_kind, 'first_stage')
        self.assertTrue(request.material_line_ids)
        self._approve_receive_and_start(request)
        stage_order.invalidate_recordset([
            'state',
            'active_production_line_ids_data',
        ])
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertEqual(
            stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            production_line,
        )
        return stage_order

    def test_parallel_material_stages_complete_without_moving_finished_product(self):
        production, production_line, finished, materials = (
            self._create_production()
        )

        self.assertEqual(
            production._physical_stage_codes(),
            ['upholstery', 'packaging'],
        )
        self.assertTrue({
            stage_code for stage_code, _model_name in MATERIAL_ONLY_STAGES
        }.issubset(set(production._get_startable_stage_codes())))

        for stage_code, model_name in MATERIAL_ONLY_STAGES:
            with self.subTest(stage_code=stage_code):
                self._run_stage(
                    production,
                    production_line,
                    stage_code,
                    model_name,
                )

                work_location = production._stage_work_location(stage_code)
                storage_location = production._stage_storage_location(stage_code)
                self.assertEqual(
                    production._stage_location_product_qty(
                        work_location,
                        finished,
                    ),
                    0.0,
                )
                self.assertEqual(
                    production._stage_location_product_qty(
                        storage_location,
                        finished,
                    ),
                    0.0,
                )
                self.assertEqual(
                    production._stage_location_product_qty(
                        work_location,
                        materials[stage_code],
                    ),
                    0.0,
                )
                self.assertIn(
                    stage_code,
                    production._production_line_completed_stage_codes(
                        production_line,
                        product=finished,
                    ),
                )

        production_line.invalidate_recordset([
            'first_stage_started',
            'first_stage_started_stage',
            'planned_start_stage',
        ])
        self.assertTrue(production_line.first_stage_started)
        self.assertEqual(production_line.first_stage_started_stage, 'upholstery')
        self.assertEqual(production_line.planned_start_stage, 'upholstery')
        self.assertFalse(self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', finished.id),
            ('location_dest_id', 'in', [
                location.id
                for stage_code, _model_name in MATERIAL_ONLY_STAGES
                for location in (
                    production._stage_work_location(stage_code),
                    production._stage_storage_location(stage_code),
                )
            ]),
            ('state', '=', 'done'),
        ]))

    def test_priming_painting_and_tailoring_run_simultaneously(self):
        production, production_line, finished, materials = (
            self._create_production(
                physical_stage='priming',
                mark_physical_started=False,
            )
        )

        priming = self._start_physical_first_stage(
            production,
            production_line,
            'priming',
        )
        material_orders = {}
        for stage_code, model_name in MATERIAL_ONLY_STAGES:
            material_orders[stage_code] = self._start_material_stage(
                production,
                production_line,
                stage_code,
                model_name,
            )

        # This is the exact shop-floor overlap: the same product line is active
        # in priming, painting and tailoring before any stage closes. Sewing
        # is one of tailoring's concurrent internal substages, not an order.
        all_orders = {'priming': priming, **material_orders}
        for stage_code, stage_order in all_orders.items():
            with self.subTest(stage_code=stage_code):
                stage_order.invalidate_recordset([
                    'state',
                    'active_production_line_ids_data',
                ])
                self.assertEqual(stage_order.state, 'in_progress')
                self.assertEqual(
                    stage_order._get_stage_line_ids_data(
                        'active_production_line_ids_data'
                    ),
                    production_line,
                )

        tailoring = material_orders['tailoring']
        self.assertEqual(
            {
                tailoring.substage_cutting_state,
                tailoring.substage_sewing_state,
                tailoring.substage_ironing_state,
            },
            {'in_progress'},
        )
        self.assertFalse(production.sewing_order_id)

        for stage_code, stage_order in material_orders.items():
            work_location = production._stage_work_location(stage_code)
            self.assertEqual(
                production._stage_location_product_qty(
                    work_location,
                    materials[stage_code],
                ),
                2.0,
            )
            self.assertEqual(
                production._stage_location_product_qty(
                    work_location,
                    finished,
                ),
                0.0,
            )

        # Finishing tailoring (including sewing internally) must not stop the
        # other two concurrent jobs.
        self._complete_stage(material_orders['tailoring'], production_line)
        for stage_order in (
            priming,
            material_orders['painting'],
        ):
            stage_order.invalidate_recordset(['state'])
            self.assertEqual(stage_order.state, 'in_progress')

        production_line.invalidate_recordset([
            'first_stage_started',
            'first_stage_started_stage',
        ])
        self.assertTrue(production_line.first_stage_started)
        self.assertEqual(production_line.first_stage_started_stage, 'priming')
        self.assertFalse(self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', finished.id),
            ('location_dest_id', 'in', [
                location.id
                for stage_code, _model_name in MATERIAL_ONLY_STAGES
                for location in (
                    production._stage_work_location(stage_code),
                    production._stage_storage_location(stage_code),
                )
            ]),
            ('state', '=', 'done'),
        ]))

    def test_upholstery_requires_finished_product_after_priming_starts(self):
        production, production_line, finished, _materials = (
            self._create_production(
                physical_stage='priming',
                mark_physical_started=False,
            )
        )

        self.assertTrue(production._is_material_only_stage('painting'))
        self.assertTrue(production._is_material_only_stage('tailoring'))
        self.assertFalse(production._is_material_only_stage('upholstery'))
        transfer_payload = {
            'product': finished,
            'source_production': production,
            'source_production_line': production_line,
        }
        self.assertFalse(production._stage_payload_can_transfer_to_stage(
            'priming',
            'painting',
            transfer_payload,
        ))
        self.assertTrue(production._stage_payload_can_transfer_to_stage(
            'priming',
            'upholstery',
            transfer_payload,
        ))
        self.assertIn('upholstery', production._get_startable_stage_codes())

        self._start_physical_first_stage(
            production,
            production_line,
            'priming',
        )
        self.assertNotIn('upholstery', production._get_startable_stage_codes())
        with self.assertRaisesRegex(UserError, 'لا يوجد شغل جاهز'):
            production.action_start_upholstery()
