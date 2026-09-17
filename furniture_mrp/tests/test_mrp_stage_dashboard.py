from datetime import timedelta
from pathlib import Path

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user


TEST_IMAGE = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
    b'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


class TestFurnitureMrpStageDashboard(TransactionCase):

    STAGE_LABELS = {
        'priming': 'التقديم',
        'painting': 'تصنيع دهانات',
        'carpentry': 'تجميع',
        'bases': 'القواعد',
        'finishing': 'تجهيز',
        'upholstery': 'كسوه',
        'tailoring': 'تفصيل',
        'packaging': 'التغليف',
    }
    STAGE_MODELS = {
        'priming': 'furniture.mrp.priming',
        'painting': 'furniture.mrp.painting',
        'carpentry': 'furniture.mrp.carpentry',
        'bases': 'furniture.mrp.bases',
        'finishing': 'furniture.mrp.finishing',
        'upholstery': 'furniture.mrp.upholstery',
        'tailoring': 'furniture.mrp.tailoring',
        'packaging': 'furniture.mrp.packaging',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Production = cls.env['furniture.mrp.production']
        cls.ProductionLine = cls.env['furniture.mrp.production.line']
        cls.product = cls.env['product.product'].create({
            'name': 'Stage Dashboard Test Product',
            'type': 'consu',
            'is_storable': True,
        })

    def _create_production(
        self,
        stage_codes,
        company=None,
        state='confirmed',
        create_stage_orders=True,
        name=None,
        production_lane='legacy',
    ):
        company = company or self.env.company
        selected_stages = set(stage_codes)
        values = {
            'name': name or 'STAGE-DASHBOARD-%s' % self._testMethodName,
            'company_id': company.id,
            'state': state,
            'stage_plan_mode': 'custom',
            'production_lane': production_lane,
        }
        values.update({
            'use_%s' % stage_code: stage_code in selected_stages
            for stage_code in self.STAGE_LABELS
        })
        production = self.Production.with_company(company).with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create(values)

        stage_orders = {}
        for stage_code in stage_codes if create_stage_orders else ():
            stage = self.env[self.STAGE_MODELS[stage_code]].with_company(
                company
            ).create({
                'name': '%s/%s' % (stage_code.upper(), production.name),
                'production_order_id': production.id,
                'state': 'pending',
            })
            production.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_line_consolidation=True,
            ).write({'%s_order_id' % stage_code: stage.id})
            stage_orders[stage_code] = stage
        return production, stage_orders

    def _create_lines(
        self, production, quantities, stage_codes, extra_values=None,
    ):
        selected_stages = set(stage_codes)
        extra_values = extra_values or {}
        lines = self.ProductionLine
        for sequence, quantity in enumerate(quantities, start=1):
            values = {
                'production_id': production.id,
                'sequence': sequence * 10,
                'product_id': self.product.id,
                'product_qty': quantity,
                'stage_selection_initialized': True,
            }
            values.update(extra_values)
            values.update({
                'use_%s' % stage_code: stage_code in selected_stages
                for stage_code in self.STAGE_LABELS
            })
            lines |= self.ProductionLine.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_kit_plan_invalidation=True,
                furniture_skip_line_consolidation=True,
                furniture_skip_running_line_initialization=True,
            ).create(values)
        return lines

    def _set_tracking(
        self, stage, active=None, quality=None, completed=None,
    ):
        empty_lines = self.ProductionLine
        tracked_sets = {
            'active_production_line_ids_data': active or empty_lines,
            'quality_production_line_ids_data': quality or empty_lines,
            'completed_production_line_ids_data': completed or empty_lines,
        }
        stage = stage.with_context(furniture_skip_line_consolidation=True)
        for field_name, lines in tracked_sets.items():
            stage._set_stage_line_ids_data(field_name, lines)

    def _create_supervisor_user(self, stage_codes):
        stage_token = '-'.join(stage_codes)
        login = 'dashboard.supervisor.%s.%s@example.test' % (
            self._testMethodName,
            stage_token,
        )
        user = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Dashboard Supervisor %s %s' % (
                self._testMethodName,
                stage_token,
            ),
            'login': login,
            'email': login,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
            ])],
        })
        stages = self.env['furniture.mrp.employee.stage'].search([
            ('code', 'in', tuple(stage_codes)),
        ])
        self.env['hr.employee'].create({
            'name': user.name,
            'user_id': user.id,
            'company_id': self.env.company.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })
        return user

    def _dashboard(
        self,
        stage_code,
        company=None,
        date_from=False,
        date_to=False,
    ):
        company = company or self.env.company
        return self.Production.with_context(
            allowed_company_ids=[company.id],
        ).get_stage_dashboard_data(
            stage_code,
            date_from=date_from,
            date_to=date_to,
        )

    def _order_payload(self, production, stage_code, company=None):
        data = self._dashboard(stage_code, company=company)
        matching = [
            order for order in data['orders']
            if order['id'] == production.id
        ]
        self.assertEqual(len(matching), 1)
        return data, matching[0]

    def _assert_quantities(self, payload, **expected):
        for key, value in expected.items():
            self.assertIn(key, payload)
            self.assertAlmostEqual(payload[key], value)

    def _supervisor_product_batches(self, user, stage_code='priming'):
        data = self.Production.with_user(user).get_stage_dashboard_data(
            stage_code
        )
        self.assertEqual(data['orders'], [])
        self.assertIn('product_batches', data)
        return data, data['product_batches']

    def _prepare_two_requestable_product_batches(self):
        supervisor = self._create_supervisor_user(('priming',))
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Bulk Batch Storekeeper %s' % self._testMethodName,
            'login': 'bulk.batch.storekeeper.%s@example.test'
            % self._testMethodName,
            'email': 'bulk.batch.storekeeper.%s@example.test'
            % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        products = self.product
        products |= self.env['product.product'].create({
            'name': 'Second Bulk Batch Product %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        stock_location = self.env.ref('stock.stock_location_stock')
        for index, product in enumerate(products, start=1):
            production, stages = self._create_production(
                ('priming',), name='BULK-BATCH-%s-%s' % (
                    self._testMethodName, index,
                ),
            )
            line = self._create_lines(
                production,
                (float(index),),
                ('priming',),
                extra_values={'product_id': product.id},
            )
            raw = self.env['product.product'].create({
                'name': 'Bulk Batch Raw %s %s' % (
                    self._testMethodName, index,
                ),
                'type': 'consu',
                'is_storable': True,
            })
            self.env['furniture.mrp.material.line'].create({
                'production_id': production.id,
                'production_line_id': line.id,
                'product_id': raw.id,
                'product_uom_id': raw.uom_id.id,
                'qty_needed': float(index + 1),
                'stage': 'priming',
            })
            production._ensure_stage_locations()
            self.env['stock.quant']._update_available_quantity(
                raw, stock_location, 20.0,
            )
            self._set_tracking(stages['priming'])

        _data, batches = self._supervisor_product_batches(supervisor)
        payloads = [
            batch for batch in batches
            if batch['product']['id'] in products.ids
        ]
        self.assertEqual(len(payloads), 2)
        self.assertTrue(all(
            payload['can_request_materials'] for payload in payloads
        ))
        return supervisor, payloads

    def _assert_no_order_or_customer_leak(self, value, secret_values=()):
        forbidden_key_parts = (
            'production', 'order', 'buyer', 'beneficiary', 'customer',
        )
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).lower()
                self.assertFalse(
                    any(part in normalized_key for part in forbidden_key_parts),
                    'restricted batch payload leaked key %r' % key,
                )
                self._assert_no_order_or_customer_leak(
                    child, secret_values=secret_values,
                )
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                self._assert_no_order_or_customer_leak(
                    child, secret_values=secret_values,
                )
            return
        if isinstance(value, str):
            for secret in secret_values:
                self.assertNotIn(secret, value)

    def test_manual_quality_is_scoped_and_invalidated_by_product_changes(self):
        production, stages = self._create_production(('tailoring',))
        line = self._create_lines(production, (1.0,), ('tailoring',))
        foreign, _ = self._create_production(('tailoring',))
        foreign_line = self._create_lines(foreign, (1.0,), ('tailoring',))
        supervisor = self._create_supervisor_user(('tailoring',))
        wrong_supervisor = self._create_supervisor_user(('packaging',))
        stage = stages['tailoring']
        stage.write({'state': 'in_progress', 'date_start': fields.Datetime.now()})
        self._set_tracking(stage, active=line)
        current = production.with_user(supervisor)
        with self.assertRaises(AccessError):
            current.action_stage_dashboard_review_order_product_quality(foreign_line.ids, 'tailoring', 'pass')
        with self.assertRaises(AccessError):
            production.with_user(wrong_supervisor).action_stage_dashboard_review_order_product_quality(line.ids, 'tailoring', 'pass')
        with self.assertRaises(ValidationError):
            current.action_stage_dashboard_review_order_product_quality(line.ids, 'tailoring', 'automatic')
        current.action_stage_dashboard_review_order_product_quality(line.ids, 'tailoring', 'pass')
        self.assertEqual(stage._manual_quality_state(line), 'pass')
        self.assertEqual(stage.state, 'in_progress')
        with self.assertRaises(AccessError):
            stage.with_user(supervisor).write({'manual_quality_reviews': {}})
        with self.assertRaises(AccessError):
            stage.with_user(supervisor).with_context(
                default_manual_quality_reviews={'forged': {'state': 'pass'}},
            ).create({})
        with self.assertRaises(AccessError):
            self.Production.with_user(supervisor).with_context(
                default_handoff_quality_reviews={'forged': {'state': 'pass'}},
            ).create({})
        stage.write({'date_start': fields.Datetime.now() + timedelta(hours=1)})
        self.assertEqual(stage._manual_quality_state(line), 'pending')
        with self.assertRaises(UserError):
            current.action_stage_dashboard_finish_order_stage('tailoring')

    def test_manual_upholstery_quality_before_classic_packaging_acceptance(self):
        supervisor = self._create_supervisor_user(('packaging',))
        production, stages = self._create_production(('upholstery', 'packaging'))
        line = self._create_lines(production, (2.0,), ('upholstery', 'packaging'))
        production._ensure_stage_locations()
        line.write({'first_stage_started': True})
        wip_product = production._get_finished_output_specs_from_lines(line)[0]['product']
        production._create_internal_move(
            production._get_production_location(), production.location_upholstery_id,
            'Manual QC upholstery output', product=wip_product, quantity=line.product_qty,
            uom=wip_product.uom_id, source_production_line=line,
        )
        self._set_tracking(stages['upholstery'], completed=line)
        moves = production._auto_transfer_completed_stage_lines('upholstery', line)
        handoff = moves.furniture_stage_transfer_handoff_id.ensure_one()
        current = production.with_user(supervisor)
        payload = self.Production.with_user(supervisor)._furniture_stage_dashboard_pending_handoffs('packaging')
        item = next(item for item in payload if item['handoff_id'] == handoff.id)
        self.assertFalse(item['quality_ready'])
        row = item['quality_rows'][0]
        self.assertFalse(production._packaging_upholstery_quality_ready(line))
        for decision in (False, 'reject'):
            if decision:
                current.action_review_handoff_quality(row['key'], decision, handoff.id)
            with self.assertRaises(UserError):
                current.action_accept_handoff_transfer(handoff.id)
            self.assertEqual(handoff.state, 'pending')
            self.assertTrue(all(move.state == 'assigned' for move in moves))
        current.action_review_handoff_quality(row['key'], 'pass', handoff.id)
        self.assertTrue(production._packaging_upholstery_quality_ready(line))
        self.assertEqual(handoff.state, 'pending')
        self.assertTrue(all(move.state == 'assigned' for move in moves))
        current.action_accept_handoff_transfer(handoff.id)
        self.assertEqual(handoff.state, 'accepted')
        self.assertTrue(all(move.state == 'done' for move in moves))
        self.assertTrue(production._packaging_upholstery_quality_ready(line))
        production.write({'handoff_quality_reviews': {}})
        with self.assertRaisesRegex(UserError, 'جودة الكسوة'):
            stages['packaging'].with_user(supervisor).action_start()
        self.assertEqual(stages['packaging'].state, 'pending')

    def test_stage_labels_contract(self):
        data = self._dashboard('priming')

        self.assertEqual(data['stage_labels'], self.STAGE_LABELS)
        with self.assertRaises(ValidationError):
            self._dashboard('sewing')

    def test_dashboard_displays_upholstery_before_tailoring(self):
        data = self._dashboard(False)
        stage_codes = [stage['code'] for stage in data['stages']]

        self.assertLess(
            stage_codes.index('upholstery'),
            stage_codes.index('tailoring'),
        )

        dashboard_js = (
            Path(__file__).resolve().parents[1]
            / 'static/src/js/mrp_stage_dashboard.js'
        ).read_text(encoding='utf-8')
        stage_definitions = dashboard_js.split(
            'const STAGE_DEFINITIONS = [', 1
        )[1].split('];', 1)[0]
        self.assertLess(
            stage_definitions.index('{ code: "upholstery"'),
            stage_definitions.index('{ code: "tailoring"'),
        )

    def test_batch_supervisor_aggregates_same_identity_across_three_orders(self):
        supervisor = self._create_supervisor_user(('priming',))
        for index, quantity in enumerate((1.0, 2.0, 3.0), start=1):
            production, stages = self._create_production(
                ('priming',),
                name='HIDDEN-ORDER-%s-%s' % (self._testMethodName, index),
            )
            self._create_lines(production, (quantity,), ('priming',))
            self._set_tracking(stages['priming'])

        data, batches = self._supervisor_product_batches(supervisor)
        matching = [
            batch for batch in batches
            if batch['product']['id'] == self.product.id
        ]

        self.assertTrue(data['supervisor_mode'])
        self.assertTrue(data['batch_supervisor_mode'])
        self.assertEqual(data['allowed_stage_codes'], ['priming'])
        self.assertEqual(
            [stage['code'] for stage in data['stages']], ['priming']
        )
        self.assertEqual(data['selected_stage'], 'priming')
        self.assertEqual(len(matching), 1)
        batch = matching[0]
        self.assertAlmostEqual(batch['planned_qty'], 6.0)
        self.assertEqual(batch['stage_code'], 'priming')
        self.assertEqual(batch['state'], 'unrequested')
        self.assertTrue(batch['batch_token'])

    def test_bulk_product_batch_request_is_one_atomic_deduplicated_operation(self):
        supervisor, payloads = self._prepare_two_requestable_product_batches()
        tokens = [payload['batch_token'] for payload in payloads]

        result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_request_product_batches_materials(
            [tokens[0], tokens[0], tokens[1]], 'priming',
        )

        self.assertTrue(result['requested'])
        self.assertEqual(result['requested_count'], 2)
        self.assertEqual(set(result['batch_tokens']), set(tokens))
        persisted = self.env[
            'furniture.mrp.stage.product.batch'
        ].sudo().search([('token', 'in', tokens)])
        self.assertEqual(len(persisted), 2)
        self.assertTrue(all(
            batch._effective_state() == 'waiting_store'
            for batch in persisted
        ))
        self.assertEqual(
            len(persisted.mapped('advance_release_id').exists()), 2,
        )

    def test_bulk_product_batch_request_rejects_all_before_any_write(self):
        supervisor, payloads = self._prepare_two_requestable_product_batches()
        valid_token = payloads[0]['batch_token']
        batch_model = self.env['furniture.mrp.stage.product.batch'].sudo()
        release_model = self.env[
            'furniture.mrp.advance.material.release'
        ].sudo()
        release_count = release_model.search_count([])

        with self.assertRaises(AccessError):
            self.Production.with_user(
                supervisor
            ).action_stage_dashboard_request_product_batches_materials(
                [valid_token, 'invalid-batch-token'], 'priming',
            )

        self.assertFalse(batch_model.search([('token', '=', valid_token)]))
        self.assertEqual(release_model.search_count([]), release_count)

    def test_finishing_supervisor_uses_the_same_product_batch_surface(self):
        production, stages = self._create_production(('finishing',))
        self._create_lines(production, (4.0,), ('finishing',))
        self._set_tracking(stages['finishing'])
        supervisor = self._create_supervisor_user(('finishing',))

        data, batches = self._supervisor_product_batches(
            supervisor, stage_code='finishing',
        )
        matching = [
            batch for batch in batches
            if batch['product']['id'] == self.product.id
        ]

        self.assertTrue(data['supervisor_mode'])
        self.assertTrue(data['batch_supervisor_mode'])
        self.assertFalse(data['order_supervisor_mode'])
        self.assertEqual(data['allowed_stage_codes'], ['finishing'])
        self.assertEqual(data['selected_stage'], 'finishing')
        self.assertEqual(data['orders'], [])
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['stage_code'], 'finishing')
        self.assertAlmostEqual(matching[0]['planned_qty'], 4.0)
        self.assertEqual(matching[0]['state'], 'unrequested')
        self.assertTrue(matching[0]['can_request_materials'])
        self.assertTrue(matching[0]['can_open_bom'] is False)

    def test_painting_supervisor_uses_the_same_product_batch_surface(self):
        production, stages = self._create_production(
            ('painting',), production_lane='painting',
        )
        self._create_lines(production, (3.0,), ('painting',))
        self._set_tracking(stages['painting'])
        supervisor = self._create_supervisor_user(('painting',))

        data, batches = self._supervisor_product_batches(
            supervisor, stage_code='painting',
        )
        matching = [
            batch for batch in batches
            if batch['product']['id'] == self.product.id
        ]

        self.assertTrue(data['supervisor_mode'])
        self.assertTrue(data['batch_supervisor_mode'])
        self.assertFalse(data['order_supervisor_mode'])
        self.assertEqual(data['allowed_stage_codes'], ['painting'])
        self.assertEqual(data['selected_stage'], 'painting')
        self.assertEqual(data['orders'], [])
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['stage_code'], 'painting')
        self.assertAlmostEqual(matching[0]['planned_qty'], 3.0)
        self.assertEqual(matching[0]['state'], 'unrequested')
        self.assertTrue(matching[0]['can_request_materials'])
        self.assertFalse(matching[0]['can_start'])

    def test_painting_product_batch_requests_receives_and_starts(self):
        supervisor = self._create_supervisor_user(('painting',))
        storekeeper = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Painting Batch Storekeeper %s' % self._testMethodName,
            'login': 'painting.batch.storekeeper.%s@example.test'
            % self._testMethodName,
            'email': 'painting.batch.storekeeper.%s@example.test'
            % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        production, stages = self._create_production(
            ('painting',), production_lane='painting',
        )
        raw = self.env['product.product'].create({
            'name': 'Painting Batch Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': self.product.uom_id.id,
            'type': 'normal',
            'furniture_product_id': self.product.id,
            'use_painting': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'painting',
                'product_id': raw.id,
                'product_qty': 2.0,
                'product_uom_id': raw.uom_id.id,
                'quantity_mode': 'scaled',
            })],
        })
        line = self._create_lines(
            production,
            (3.0,),
            ('painting',),
            extra_values={'bom_id': bom.id},
        )
        production._ensure_stage_locations()
        self.env['stock.quant']._update_available_quantity(
            raw, self.env.ref('stock.stock_location_stock'), 10.0,
        )
        self._set_tracking(stages['painting'])
        self.assertFalse(production.material_line_ids)

        _data, payloads = self._supervisor_product_batches(
            supervisor, stage_code='painting',
        )
        payload = next(
            batch for batch in payloads
            if batch['product']['id'] == self.product.id
        )
        token = payload['batch_token']
        requested = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_request_product_batch_materials(
            token, 'painting',
        )
        self.assertEqual(requested['state'], 'waiting_store')
        production.invalidate_recordset(['material_line_ids'])
        generated_material = production.material_line_ids.ensure_one()
        self.assertEqual(generated_material.product_id, raw)
        self.assertEqual(generated_material.production_line_id, line)
        self.assertEqual(generated_material.stage, 'painting')
        self.assertAlmostEqual(generated_material.qty_needed, 6.0)

        batch = self.env['furniture.mrp.stage.product.batch'].sudo().search([
            ('token', '=', token),
        ]).ensure_one()
        release = batch.advance_release_id
        release.sudo().write({'assigned_to_id': storekeeper.id})
        release.with_user(storekeeper).action_issue()
        received = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_receive_product_batch_materials(
            token, 'painting',
        )
        self.assertEqual(received['state'], 'ready')

        started = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_start_product_batch(token, 'painting')
        self.assertEqual(started['state'], 'in_progress')
        stage = stages['painting']
        stage.invalidate_recordset()
        self.assertEqual(stage.state, 'in_progress')
        self.assertIn(
            line,
            stage._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
        )
        self.assertTrue(stage._get_active_substage_fields())
        self.assertTrue(all(
            stage[field_name] == 'in_progress'
            for _code, _label, field_name, _required_field
            in stage._get_active_substage_fields()
        ))

    def test_body_lane_bases_handoff_exposes_exact_finishing_product_card(self):
        supervisor = self._create_supervisor_user(('finishing',))
        model = self.env['furniture.product.model'].create({
            'name': 'Finishing handoff %s' % self._testMethodName,
        })
        route = ('priming', 'carpentry', 'bases', 'finishing')
        production, stages = self._create_production(
            route, production_lane='body',
        )
        line = self._create_lines(
            production,
            (6.0,),
            route,
            extra_values={'furniture_order_model_id': model.id},
        ).ensure_one()
        production._ensure_stage_locations()
        line.write({'first_stage_started': True})

        _early_data, early_batches = self._supervisor_product_batches(
            supervisor, stage_code='finishing',
        )
        self.assertFalse([
            batch for batch in early_batches
            if batch['product']['id'] == line.product_id.id
        ])

        wip_product = production._get_finished_output_specs_from_lines(
            line,
        )[0]['product']
        production._create_internal_move(
            production._get_production_location(),
            production.location_bases_id,
            'Accepted bases WIP for finishing-card regression',
            product=wip_product,
            quantity=line.product_qty,
            uom=wip_product.uom_id,
            source_production_line=line,
        )
        self._set_tracking(stages['bases'], completed=line)
        moves = production._auto_transfer_completed_stage_lines('bases', line)

        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.product_id, wip_product)
        self.assertEqual(moves.location_id, production.location_bases_id)
        self.assertEqual(
            moves.location_dest_id, production.location_finishing_wip_id,
        )
        self.assertEqual(moves.furniture_source_production_line_id, line)

        handoff = moves.furniture_stage_transfer_handoff_id
        self.assertEqual(handoff.state, 'pending')
        self.assertEqual(handoff.target_stage, 'finishing')

        _pending_data, pending_batches = self._supervisor_product_batches(
            supervisor, stage_code='finishing',
        )
        self.assertFalse([
            batch for batch in pending_batches
            if batch['product']['id'] == line.product_id.id
        ])
        handoff.with_user(supervisor)._dismiss_for_user()
        dismissed_data, _dismissed_batches = self._supervisor_product_batches(
            supervisor, stage_code='finishing',
        )
        self.assertEqual(len(dismissed_data['pending_handoffs']), 1)
        self.assertEqual(
            dismissed_data['pending_handoffs'][0]['key'],
            'stage:%s' % handoff.id,
        )
        self.assertEqual(
            dismissed_data['pending_handoffs'][0]['production_id'],
            production.id,
        )

        production.with_user(supervisor).action_accept_handoff_transfer(
            stage_handoff_id=handoff.id,
        )
        moves.invalidate_recordset(['state'])
        self.assertEqual(moves.state, 'done')

        data, batches = self._supervisor_product_batches(
            supervisor, stage_code='finishing',
        )
        matching = [
            batch for batch in batches
            if batch['product']['id'] == line.product_id.id
        ]
        self.assertTrue(data['batch_supervisor_mode'])
        self.assertFalse(data['order_supervisor_mode'])
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['stage_code'], 'finishing')
        self.assertEqual(matching[0]['state'], 'unrequested')
        self.assertAlmostEqual(matching[0]['planned_qty'], 6.0)

    def test_batch_supervisor_splits_bom_and_dimension_identities(self):
        supervisor = self._create_supervisor_user(('priming',))
        bom_common_values = {
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'furniture_product_id': self.product.id,
            'product_qty': 1.0,
            'product_uom_id': self.product.uom_id.id,
            'type': 'normal',
            'furniture_width_cm': 100.0,
            'furniture_depth_cm': 50.0,
            'furniture_height_cm': 80.0,
        }
        first_bom = self.env['mrp.bom'].create(bom_common_values)
        second_bom = self.env['mrp.bom'].create(bom_common_values)
        line_specs = (
            (2.0, first_bom, (100.0, 50.0, 80.0)),
            (3.0, second_bom, (100.0, 50.0, 80.0)),
            (4.0, first_bom, (120.0, 50.0, 80.0)),
        )
        for index, (quantity, bom, dimensions) in enumerate(
            line_specs, start=1,
        ):
            production, stages = self._create_production(
                ('priming',),
                name='HIDDEN-SPLIT-ORDER-%s-%s' % (
                    self._testMethodName, index,
                ),
            )
            self._create_lines(
                production,
                (quantity,),
                ('priming',),
                extra_values={
                    'bom_id': bom.id,
                    'width_cm': dimensions[0],
                    'depth_cm': dimensions[1],
                    'height_cm': dimensions[2],
                },
            )
            self._set_tracking(stages['priming'])

        _data, batches = self._supervisor_product_batches(supervisor)
        matching = [
            batch for batch in batches
            if batch['product']['id'] == self.product.id
        ]
        quantities_by_identity = {
            (
                batch['bom']['id'],
                batch.get('dimension_label') or False,
            ): batch['planned_qty']
            for batch in matching
        }

        self.assertEqual(len(matching), 3)
        self.assertEqual(
            quantities_by_identity,
            {
                (first_bom.id, False): 2.0,
                (second_bom.id, False): 3.0,
                (first_bom.id, '120×50×80'): 4.0,
            },
        )

    def test_downstream_cards_wait_for_exact_product_line_arrival(self):
        production, _stages = self._create_production(
            ('priming', 'carpentry'),
            create_stage_orders=False,
        )
        chaise_product = self.env['product.product'].create({
            'name': 'Downstream Chaise %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        chair_product = self.env['product.product'].create({
            'name': 'Downstream Chair %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        sofa_line = self._create_lines(
            production, (6.0,), ('priming', 'carpentry'),
        )
        chaise_line = self._create_lines(
            production,
            (6.0,),
            ('priming', 'carpentry'),
            extra_values={'product_id': chaise_product.id},
        )
        chair_line = self._create_lines(
            production,
            (12.0,),
            ('priming', 'carpentry'),
            extra_values={'product_id': chair_product.id},
        )
        production_lines = sofa_line | chaise_line | chair_line
        production._ensure_stage_locations()

        # A batch at the previous stage must not suppress the same exact line
        # from the downstream stage after it physically arrives there.
        Batch = self.env['furniture.mrp.stage.product.batch']
        chaise_identity = Batch._identity_values_from_line(chaise_line)
        Batch.create({
            'token': 'previous-stage-%s' % self._testMethodName,
            'company_id': self.env.company.id,
            'stage_code': 'priming',
            'identity_key': chaise_identity['identity_key'],
            'product_id': chaise_product.id,
            'model_id': chaise_identity['model'].id or False,
            'bom_id': chaise_identity['bom'].id or False,
            'dimension_label': chaise_identity['dimension_label'],
            'uom_id': chaise_identity['uom'].id,
            'production_line_ids': [(6, 0, chaise_line.ids)],
            'planned_qty': 6.0,
            'state': 'ready',
            'member_ids': [(0, 0, {
                'production_line_id': chaise_line.id,
                'qty_snapshot': 6.0,
                'identity_key': chaise_identity['identity_key'],
            })],
        })

        early_groups = [
            group for group in Batch._candidate_groups('carpentry')
            if group['lines'] & production_lines
        ]
        self.assertEqual(early_groups, [])

        chaise_line.write({'first_stage_started': True})
        production._create_internal_move(
            production._get_production_location(),
            production.location_carpentry_wip_id,
            'Exact downstream product-line arrival regression',
            product=chaise_product,
            quantity=6.0,
            uom=chaise_product.uom_id,
            source_production_line=chaise_line,
        )

        arrived_groups = [
            group for group in Batch._candidate_groups('carpentry')
            if group['lines'] & production_lines
        ]
        self.assertEqual(len(arrived_groups), 1)
        self.assertEqual(arrived_groups[0]['lines'], chaise_line)
        self.assertEqual(arrived_groups[0]['product'], chaise_product)
        self.assertAlmostEqual(arrived_groups[0]['planned_qty'], 6.0)

    def test_batch_supervisor_payload_recursively_hides_orders_and_customers(self):
        secret_order_name = 'TOP-SECRET-PRODUCTION-REFERENCE'
        secret_buyer_name = 'TOP-SECRET-BUYER'
        secret_beneficiary_name = 'TOP-SECRET-BENEFICIARY'
        buyer = self.env['res.partner'].create({
            'name': secret_buyer_name,
            'is_company': True,
        })
        beneficiary = self.env['res.partner'].create({
            'name': secret_beneficiary_name,
        })
        production, stages = self._create_production(
            ('priming',), name=secret_order_name,
        )
        self._create_lines(
            production,
            (3.0,),
            ('priming',),
            extra_values={
                'buyer_partner_id': buyer.id,
                'beneficiary_partner_id': beneficiary.id,
            },
        )
        self._set_tracking(stages['priming'])
        supervisor = self._create_supervisor_user(('priming',))

        _data, batches = self._supervisor_product_batches(supervisor)
        matching = [
            batch for batch in batches
            if batch['product']['id'] == self.product.id
        ]

        self.assertEqual(len(matching), 1)
        self._assert_no_order_or_customer_leak(
            matching,
            secret_values=(
                secret_order_name,
                secret_buyer_name,
                secret_beneficiary_name,
            ),
        )

    def test_tailoring_upholstery_packaging_supervisors_keep_order_payload(self):
        """The three finishing stages stay order-based, never product-batched."""
        buyer = self.env['res.partner'].create({
            'name': 'Order Workflow Buyer %s' % self._testMethodName,
            'is_company': True,
        })
        beneficiary = self.env['res.partner'].create({
            'name': 'Order Workflow Beneficiary %s' % self._testMethodName,
        })
        production, stages = self._create_production(
            ('tailoring', 'upholstery', 'packaging'),
            name='ORDER-WORKFLOW-%s' % self._testMethodName,
        )
        production.write({
            'tailoring_set_image_1920': TEST_IMAGE,
            'tailoring_set_image_token': 'dashboard-order-image-token',
        })
        line = self._create_lines(
            production,
            (3.0,),
            ('tailoring', 'upholstery', 'packaging'),
            extra_values={
                'buyer_partner_id': buyer.id,
                'beneficiary_partner_id': beneficiary.id,
                'kit_piece_note': 'ملاحظة تشغيل آمنة للصنف',
            },
        )
        line.sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).write({
            'tailoring_fabric_configured': True,
            'tailoring_takawe_configured': True,
            'tailoring_fabric_piece_size': '45',
            'tailoring_takawe_piece_size': '40x60',
        })
        meter_uom = self.env.ref('furniture_mrp.furniture_uom_meter')
        fabric, takawe = self.env['product.product'].create([
            {
                'name': 'Dashboard Fabric %s' % self._testMethodName,
                'type': 'consu',
                'is_storable': True,
                'uom_id': meter_uom.id,
                'uom_po_id': meter_uom.id,
                'furniture_tailoring_material_kind': 'fabric',
            },
            {
                'name': 'Dashboard Takawe %s' % self._testMethodName,
                'type': 'consu',
                'is_storable': True,
                'uom_id': meter_uom.id,
                'uom_po_id': meter_uom.id,
                'furniture_tailoring_material_kind': 'takawe',
            },
        ])
        self.env[
            'furniture.mrp.tailoring.material.allocation'
        ].sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).create([
            {
                'production_id': production.id,
                'production_line_id': line.id,
                'material_kind': 'fabric',
                'product_id': fabric.id,
                'qty': 4.25,
                'product_uom_id': meter_uom.id,
            },
            {
                'production_id': production.id,
                'production_line_id': line.id,
                'material_kind': 'takawe',
                'product_id': takawe.id,
                'qty': 2.0,
                'product_uom_id': meter_uom.id,
                'piece_size': '40x60',
            },
        ])
        for stage in stages.values():
            self._set_tracking(stage)
        supervisor = self._create_supervisor_user(
            ('tailoring', 'upholstery', 'packaging')
        )

        expected_item_keys = {
            'product_id', 'product_name', 'qty', 'uom_id', 'uom',
            'piece_size',
        }
        for stage_code in ('tailoring', 'upholstery', 'packaging'):
            data = self.Production.with_user(
                supervisor
            ).get_stage_dashboard_data(stage_code)

            self.assertTrue(data['supervisor_mode'])
            self.assertTrue(data['order_supervisor_mode'])
            self.assertFalse(data['batch_supervisor_mode'])
            self.assertEqual(data.get('product_batches', []), [])
            self.assertEqual(data['selected_stage'], stage_code)
            matching_orders = [
                order for order in data['orders']
                if order['id'] == production.id
            ]
            self.assertEqual(len(matching_orders), 1)
            order = matching_orders[0]
            self.assertEqual(order['id'], production.id)
            self.assertEqual(order['stage_code'], stage_code)
            # These stages retain the ordinary per-order customer context;
            # only the product-batch supervisor surface strips it.
            self.assertEqual(order['buyer_summary'], buyer.display_name)
            self.assertEqual(
                order['beneficiary_summary'], beneficiary.display_name,
            )
            for field_name in (
                'has_order_image',
                'order_image_url',
                'can_request_materials',
                'can_start_stage',
                'can_receive_materials',
                'can_open_material_request',
                'completed_qty',
                'remaining_qty',
            ):
                self.assertIn(field_name, order)
            self.assertTrue(order['has_order_image'])
            self.assertTrue(order['order_image_url'].startswith('/web/image/'))
            self.assertNotIn('base64', order['order_image_url'].lower())

            product_payload = order['product_lines'][0]
            for field_name in (
                'fabric_items',
                'fabric_summary',
                'takawe_items',
                'takawe_summary',
                'takawe_piece_size',
                'notes',
            ):
                self.assertIn(field_name, product_payload)
            self.assertNotIn('fabric_piece_size', product_payload)
            self.assertEqual(product_payload['takawe_piece_size'], '40×60')
            self.assertEqual(
                product_payload['notes'], 'ملاحظة تشغيل آمنة للصنف',
            )
            self.assertEqual(len(product_payload['fabric_items']), 1)
            self.assertEqual(len(product_payload['takawe_items']), 1)
            self.assertEqual(
                set(product_payload['fabric_items'][0]), expected_item_keys,
            )
            self.assertEqual(
                set(product_payload['takawe_items'][0]), expected_item_keys,
            )
            self.assertEqual(
                product_payload['fabric_items'][0]['product_id'], fabric.id,
            )
            self.assertAlmostEqual(
                product_payload['fabric_items'][0]['qty'], 4.25,
            )
            self.assertEqual(
                product_payload['takawe_items'][0]['product_id'], takawe.id,
            )
            self.assertAlmostEqual(
                product_payload['takawe_items'][0]['qty'], 2.0,
            )
            self.assertEqual(
                product_payload['takawe_items'][0]['piece_size'],
                '40×60',
            )
            self.assertFalse(
                product_payload['fabric_items'][0]['piece_size'],
            )
            self.assertIn(fabric.display_name, product_payload['fabric_summary'])
            self.assertIn(takawe.display_name, product_payload['takawe_summary'])

    def test_admin_order_payload_never_enables_supervisor_modes(self):
        production, stages = self._create_production(('tailoring',))
        self._create_lines(production, (2.0,), ('tailoring',))
        self._set_tracking(stages['tailoring'])

        data, order = self._order_payload(production, 'tailoring')

        self.assertFalse(data['supervisor_mode'])
        self.assertFalse(data['batch_supervisor_mode'])
        self.assertFalse(data['order_supervisor_mode'])
        self.assertEqual(order['id'], production.id)

    def test_admin_rich_cards_keep_readonly_modes_for_every_stage(self):
        stage_codes = tuple(self.STAGE_LABELS)
        production, stages = self._create_production(stage_codes)
        self._create_lines(
            production, (2.0,), stage_codes,
            extra_values={'kit_piece_note': 'تفاصيل مشتركة للأدمن'},
        )
        production.write({
            'tailoring_set_image_1920': TEST_IMAGE,
            'tailoring_set_image_token': 'admin-card-image',
        })
        for stage in stages.values():
            self._set_tracking(stage)
        for code in stage_codes:
            data, order = self._order_payload(production, code)
            self.assertFalse(data['supervisor_mode'])
            self.assertFalse(data['batch_supervisor_mode'])
            self.assertFalse(data['order_supervisor_mode'])
            self.assertTrue(order['has_order_image'])
            self.assertIn(str(production.id), order['order_image_url'])
            for field in ('can_request_materials', 'can_receive_materials', 'can_start_stage', 'can_finish_stage'):
                self.assertFalse(order[field], (code, field))
            self.assertEqual(order['product_lines'][0]['notes'], 'تفاصيل مشتركة للأدمن')
            self.assertIn('fabric_items', order['product_lines'][0])
            self.assertIn('takawe_items', order['product_lines'][0])
            self.assertEqual(order['planned_qty'], 2.0)

    def test_identical_tailoring_rows_sum_saved_fabric_and_takawe(self):
        production, stages = self._create_production(('tailoring',))
        lines = self._create_lines(
            production,
            (1.0, 2.0),
            ('tailoring',),
            extra_values={'kit_piece_note': 'نفس ملاحظات التجهيز'},
        )
        lines.sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).write({
            'tailoring_fabric_configured': True,
            'tailoring_takawe_configured': True,
            'tailoring_fabric_piece_size': '55',
            'tailoring_takawe_piece_size': '40x60',
        })
        meter_uom = self.env.ref('furniture_mrp.furniture_uom_meter')
        fabric, takawe = self.env['product.product'].create([
            {
                'name': 'Repeated Dashboard Fabric %s' % self._testMethodName,
                'type': 'consu',
                'is_storable': True,
                'uom_id': meter_uom.id,
                'uom_po_id': meter_uom.id,
                'furniture_tailoring_material_kind': 'fabric',
            },
            {
                'name': 'Repeated Dashboard Takawe %s' % self._testMethodName,
                'type': 'consu',
                'is_storable': True,
                'uom_id': meter_uom.id,
                'uom_po_id': meter_uom.id,
                'furniture_tailoring_material_kind': 'takawe',
            },
        ])
        allocation_values = []
        for line in lines:
            allocation_values.extend([
                {
                    'production_id': production.id,
                    'production_line_id': line.id,
                    'material_kind': 'fabric',
                    'product_id': fabric.id,
                    'qty': 4.25,
                    'product_uom_id': meter_uom.id,
                },
                {
                    'production_id': production.id,
                    'production_line_id': line.id,
                    'material_kind': 'takawe',
                    'product_id': takawe.id,
                    'qty': 2.0,
                    'product_uom_id': meter_uom.id,
                    'piece_size': '40x60',
                },
            ])
        self.env[
            'furniture.mrp.tailoring.material.allocation'
        ].sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).create(allocation_values)
        self._set_tracking(stages['tailoring'])
        supervisor = self._create_supervisor_user(('tailoring',))

        data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('tailoring')
        order = next(
            payload for payload in data['orders']
            if payload['id'] == production.id
        )

        self.assertEqual(len(order['product_lines']), 1)
        product_payload = order['product_lines'][0]
        self.assertAlmostEqual(product_payload['planned_qty'], 3.0)
        self.assertEqual(len(product_payload['fabric_items']), 1)
        self.assertEqual(len(product_payload['takawe_items']), 1)
        self.assertAlmostEqual(
            product_payload['fabric_items'][0]['qty'], 8.5,
        )
        self.assertAlmostEqual(
            product_payload['takawe_items'][0]['qty'], 4.0,
        )
        self.assertIn('8.5', product_payload['fabric_summary'])
        self.assertIn('4', product_payload['takawe_summary'])

    def test_order_stage_actions_are_record_and_stage_scoped(self):
        production, _stages = self._create_production(('tailoring',))
        self._create_lines(production, (1.0,), ('tailoring',))
        other_production, _other_stages = self._create_production(
            ('upholstery',),
        )
        self._create_lines(other_production, (1.0,), ('upholstery',))
        tailoring_supervisor = self._create_supervisor_user(('tailoring',))

        for method_name in (
            'action_stage_dashboard_request_order_materials',
            'action_stage_dashboard_start_order_stage',
        ):
            method = getattr(
                (production | other_production).with_user(
                    tailoring_supervisor
                ),
                method_name,
            )
            with self.assertRaises(ValueError):
                method('tailoring')

            with self.assertRaises(UserError):
                getattr(
                    other_production.with_user(tailoring_supervisor),
                    method_name,
                )('tailoring')

            with self.assertRaises(AccessError):
                getattr(
                    production.with_user(tailoring_supervisor),
                    method_name,
                )('upholstery')

    def test_stage_mixin_rejects_supervisor_assigned_to_another_stage(self):
        production, stages = self._create_production(('upholstery',))
        self._create_lines(production, (1.0,), ('upholstery',))
        tailoring_supervisor = self._create_supervisor_user(('tailoring',))
        upholstery_order = stages['upholstery'].with_user(
            tailoring_supervisor
        )

        self.assertTrue(tailoring_supervisor.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor_tailoring'
        ))
        self.assertFalse(tailoring_supervisor.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor_upholstery'
        ))
        with self.assertRaises(AccessError):
            upholstery_order._check_stage_operation_access()
        with self.assertRaises(AccessError):
            upholstery_order.action_request_store_approval()
        with self.assertRaises(AccessError):
            upholstery_order.action_start()

        stages['upholstery'].invalidate_recordset(['state', 'foreman_id'])
        self.assertEqual(stages['upholstery'].state, 'pending')
        self.assertFalse(stages['upholstery'].foreman_id)
        self.assertFalse(self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', '=', production.id),
            ('stage_order_model', '=', stages['upholstery']._name),
            ('stage_order_res_id', '=', stages['upholstery'].id),
        ]))

    def test_direct_stage_writes_require_the_exact_supervisor_group(self):
        production, stages = self._create_production(
            ('tailoring', 'upholstery'),
        )
        self._create_lines(
            production, (1.0,), ('tailoring', 'upholstery'),
        )
        tailoring_supervisor = self._create_supervisor_user(('tailoring',))

        own_stage = stages['tailoring'].with_user(tailoring_supervisor)
        self.assertEqual(
            own_stage.read(['name'])[0]['name'],
            stages['tailoring'].name,
        )
        with self.assertRaises(AccessError):
            stages['upholstery'].with_user(tailoring_supervisor).read(['name'])

        own_stage.write({
            'priority': '1',
        })
        self.assertEqual(stages['tailoring'].priority, '1')
        with self.assertRaises(AccessError):
            stages['upholstery'].with_user(tailoring_supervisor).write({
                'priority': '1',
            })

        worker = new_test_user(
            self.env,
            login='stage_direct_write_worker_%s' % self._testMethodName,
            groups='base.group_user',
        )
        with self.assertRaises(AccessError):
            stages['tailoring'].with_user(worker).write({'priority': '2'})
        with self.assertRaises(AccessError):
            stages['tailoring'].with_user(worker).read(['name'])

    def test_cover_tailoring_prerequisite_does_not_leak_upholstery_acl(self):
        production, stages = self._create_production(
            ('tailoring', 'upholstery'), production_lane='cover',
        )
        line = self._create_lines(
            production, (1.0,), ('tailoring', 'upholstery'),
        ).ensure_one()
        stages['upholstery'].sudo().with_context(
            furniture_skip_line_consolidation=True,
        )._set_stage_line_ids_data(
            'completed_production_line_ids_data', line.sudo(),
        )
        stages['upholstery'].sudo().write({'state': 'done'})
        tailoring_supervisor = self._create_supervisor_user(('tailoring',))

        with self.assertRaises(AccessError):
            stages['upholstery'].with_user(tailoring_supervisor).read(['name'])

        candidates = production.with_user(
            tailoring_supervisor
        )._get_material_only_stage_start_line_candidates(
            'tailoring',
            stage_order=stages['tailoring'].with_user(tailoring_supervisor),
        )
        self.assertEqual(candidates.ids, line.ids)

    def test_cover_tailoring_dashboard_starts_received_products_without_hall_carryover(self):
        production, stages = self._create_production(
            ('tailoring', 'upholstery'), production_lane='cover',
        )
        furniture_model = self.env['furniture.product.model'].create({
            'name': 'Cover Tailoring Dashboard %s' % self._testMethodName,
        })
        lines = self._create_lines(
            production, (1.0, 1.0), ('tailoring', 'upholstery'),
            extra_values={
                'furniture_order_model_id': furniture_model.id,
            },
        )
        lines.sudo().write({
            'first_stage_started': True,
            'first_stage_started_stage': 'upholstery',
            'planned_start_stage': 'upholstery',
        })
        production._ensure_stage_locations()
        production._move_stage_work_to_stock(
            stages['upholstery']._name,
            production_lines=lines,
        )
        stages['upholstery'].sudo().with_context(
            furniture_skip_line_consolidation=True,
        )._set_stage_line_ids_data(
            'completed_production_line_ids_data', lines.sudo(),
        )
        stages['upholstery'].sudo().write({'state': 'done'})

        raw_material = self.env['product.product'].create({
            'name': 'Cover Tailoring Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        for line in lines:
            self.env['furniture.mrp.material.line'].create({
                'production_id': production.id,
                'production_line_id': line.id,
                'product_id': raw_material.id,
                'product_uom_id': raw_material.uom_id.id,
                'qty_needed': 2.0,
                'stage': 'tailoring',
            })
        source_location = (
            production.location_src_id
            or self.env.ref('stock.stock_location_stock')
        )
        self.env['stock.quant']._update_available_quantity(
            raw_material, source_location, 4.0,
        )
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_stage_codes(
            production, ('tailoring',), notify_storekeeper=False,
        )
        release.sudo().action_issue()
        supervisor = self._create_supervisor_user(('tailoring',))
        production.with_user(
            supervisor
        ).action_stage_dashboard_request_order_materials('tailoring')

        first_result = production.with_user(
            supervisor
        ).action_stage_dashboard_start_order_product(
            lines[:1].ids, 'tailoring',
        )
        self.assertTrue(first_result['started'])
        stages['tailoring'].invalidate_recordset(['state'])
        self.assertEqual(stages['tailoring'].state, 'in_progress')
        self.assertEqual(
            stages['tailoring']._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            lines[:1],
        )

        second_result = production.with_user(
            supervisor
        ).action_stage_dashboard_start_order_product(
            lines[1:].ids, 'tailoring',
        )
        self.assertTrue(second_result['started'])
        self.assertEqual(
            stages['tailoring']._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            lines,
        )

        # Quality is a manual, per-product decision BEFORE stage completion.
        current = production.with_user(supervisor)
        with self.assertRaises(UserError):
            current.action_stage_dashboard_finish_order_stage('tailoring')
        with self.assertRaises(AccessError):
            stages['tailoring'].with_user(supervisor).write({'manual_quality_reviews': {}})
        current.action_stage_dashboard_review_order_product_quality(lines[:1].ids, 'tailoring', 'reject')
        with self.assertRaises(UserError):
            current.action_stage_dashboard_finish_order_stage('tailoring')
        current.action_stage_dashboard_review_order_product_quality(lines[:1].ids, 'tailoring', 'pass')
        with self.assertRaises(UserError):
            current.action_stage_dashboard_finish_order_stage('tailoring')
        current.action_stage_dashboard_review_order_product_quality(lines[1:].ids, 'tailoring', 'pass')
        self.assertEqual(stages['tailoring'].state, 'in_progress')
        self.assertFalse(stages['tailoring']._get_stage_line_ids_data('completed_production_line_ids_data'))
        self.assertEqual(stages['tailoring']._manual_quality_state(lines), 'pass')
        self.assertEqual(stages['tailoring'].manual_quality_reviews[str(lines[0].id)]['user_id'], supervisor.id)

        finish_result = production.with_user(
            supervisor
        ).action_stage_dashboard_finish_order_stage('tailoring')
        self.assertTrue(finish_result['finished'])
        stages['tailoring'].invalidate_recordset([
            'state',
            'substage_cutting_state',
            'substage_sewing_state',
            'substage_ironing_state',
        ])
        self.assertEqual(stages['tailoring'].state, 'done')
        self.assertTrue(stages['tailoring'].all_substages_done)
        self.assertEqual(
            stages['tailoring']._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ),
            lines,
        )
        self.assertFalse(
            stages['tailoring']._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
        )

    def test_operational_record_rules_scope_stage_supervisors_and_manager(self):
        """Exact-stage supervisors must not cross stage or company boundaries."""
        stage_codes = ('tailoring', 'upholstery', 'packaging')
        productions = self.Production
        production_lines = self.ProductionLine
        material_lines = self.env['furniture.mrp.material.line']
        store_requests = self.env['furniture.mrp.store.request']
        store_request_lines = self.env['furniture.mrp.store.request.line']
        fixtures = {}

        for stage_code in stage_codes:
            production, stages = self._create_production(
                (stage_code,),
                name='SCOPE-%s-%s' % (stage_code, self._testMethodName),
            )
            production_line = self._create_lines(
                production, (1.0,), (stage_code,),
            ).ensure_one()
            material_line = self.env['furniture.mrp.material.line'].create({
                'production_id': production.id,
                'production_line_id': production_line.id,
                'product_id': self.product.id,
                'product_uom_id': self.product.uom_id.id,
                'qty_needed': 1.0,
                'stage': stage_code,
            })
            stage_order = stages[stage_code]
            store_request = self.env['furniture.mrp.store.request'].sudo().create({
                'production_id': production.id,
                'stage_code': stage_code,
                'stage_order_model': stage_order._name,
                'stage_order_res_id': stage_order.id,
                'stage_order_name': stage_order.display_name,
                'request_kind': 'direct',
                'start_mode': 'direct',
                'assigned_to_id': self.env.user.id,
            })
            store_request_line = self.env[
                'furniture.mrp.store.request.line'
            ].sudo().create({
                'request_id': store_request.id,
                'product_id': self.product.id,
                'product_uom_id': self.product.uom_id.id,
                'requested_qty': 1.0,
            })
            fixtures[stage_code] = {
                'production': production,
                'production_line': production_line,
                'material_line': material_line,
                'store_request': store_request,
                'store_request_line': store_request_line,
            }
            productions |= production
            production_lines |= production_line
            material_lines |= material_line
            store_requests |= store_request
            store_request_lines |= store_request_line

        other_company = self.env['res.company'].create({
            'name': 'Scope Other Company %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, other_company.id)]})
        other_production, other_stages = self._create_production(
            ('tailoring',),
            company=other_company,
            name='SCOPE-OTHER-COMPANY-%s' % self._testMethodName,
        )
        other_line = self._create_lines(
            other_production, (1.0,), ('tailoring',),
        ).ensure_one()
        other_material = self.env[
            'furniture.mrp.material.line'
        ].with_company(other_company).create({
            'production_id': other_production.id,
            'production_line_id': other_line.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'qty_needed': 1.0,
            'stage': 'tailoring',
        })
        other_stage_order = other_stages['tailoring']
        other_store_request = self.env[
            'furniture.mrp.store.request'
        ].sudo().with_company(other_company).create({
            'production_id': other_production.id,
            'stage_code': 'tailoring',
            'stage_order_model': other_stage_order._name,
            'stage_order_res_id': other_stage_order.id,
            'stage_order_name': other_stage_order.display_name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'assigned_to_id': self.env.user.id,
        })
        other_store_request_line = self.env[
            'furniture.mrp.store.request.line'
        ].sudo().create({
            'request_id': other_store_request.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 1.0,
        })
        productions |= other_production
        production_lines |= other_line
        material_lines |= other_material
        store_requests |= other_store_request
        store_request_lines |= other_store_request_line

        model_cases = (
            ('furniture.mrp.production', productions, 'production'),
            (
                'furniture.mrp.production.line',
                production_lines,
                'production_line',
            ),
            (
                'furniture.mrp.material.line',
                material_lines,
                'material_line',
            ),
            (
                'furniture.mrp.store.request',
                store_requests,
                'store_request',
            ),
            (
                'furniture.mrp.store.request.line',
                store_request_lines,
                'store_request_line',
            ),
        )
        for stage_code in stage_codes:
            supervisor = self._create_supervisor_user((stage_code,))
            expected = fixtures[stage_code]
            for model_name, candidates, fixture_key in model_cases:
                scoped_model = self.env[model_name].with_user(
                    supervisor
                ).with_context(allowed_company_ids=[self.env.company.id])
                visible = scoped_model.search([('id', 'in', candidates.ids)])
                self.assertEqual(
                    visible.ids,
                    [expected[fixture_key].id],
                    '%s supervisor leaked %s records' % (
                        stage_code, model_name,
                    ),
                )
                foreign = candidates - expected[fixture_key]
                with self.assertRaises(AccessError):
                    foreign[:1].with_user(supervisor).read(['display_name'])

        multi_stage_supervisor = self._create_supervisor_user(stage_codes)
        for model_name, candidates, fixture_key in model_cases:
            visible = self.env[model_name].with_user(
                multi_stage_supervisor
            ).with_context(
                allowed_company_ids=[self.env.company.id],
            ).search([('id', 'in', candidates.ids)])
            self.assertEqual(
                set(visible.ids),
                {
                    fixtures[stage_code][fixture_key].id
                    for stage_code in stage_codes
                },
            )

        unscoped_supervisor = new_test_user(
            self.env,
            login='scope_unassigned_supervisor_%s' % self._testMethodName,
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_supervisor'
            ),
        )
        for model_name, candidates, _fixture_key in model_cases:
            visible = self.env[model_name].with_user(
                unscoped_supervisor
            ).with_context(
                allowed_company_ids=[self.env.company.id],
            ).search([('id', 'in', candidates.ids)])
            self.assertFalse(visible)

        manager = new_test_user(
            self.env,
            login='scope_manager_%s' % self._testMethodName,
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
        )
        manager.write({
            'company_ids': [(6, 0, (self.env.company | other_company).ids)],
        })
        for model_name, candidates, _fixture_key in model_cases:
            visible = self.env[model_name].with_user(manager).with_context(
                allowed_company_ids=(self.env.company | other_company).ids,
            ).search([('id', 'in', candidates.ids)])
            self.assertEqual(set(visible.ids), set(candidates.ids))

    def test_order_supervisor_dashboard_loads_restricted_real_bom(self):
        model = self.env['furniture.product.model'].create({
            'name': 'Restricted Dashboard BoM Model %s' % self._testMethodName,
        })
        raw_material = self.env['product.product'].create({
            'name': 'Restricted Dashboard BoM Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': self.product.uom_id.id,
            'type': 'normal',
            'furniture_product_id': self.product.id,
            'furniture_recipe_model_id': model.id,
            'use_tailoring': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'tailoring',
                'product_id': raw_material.id,
                'product_qty': 1.5,
                'product_uom_id': raw_material.uom_id.id,
                'quantity_mode': 'scaled',
            })],
        })
        production, stages = self._create_production(('tailoring',))
        line = self._create_lines(
            production,
            (2.0,),
            ('tailoring',),
            extra_values={
                'furniture_order_model_id': model.id,
                'bom_id': bom.id,
            },
        ).ensure_one()
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': line.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 3.0,
            'stage': 'tailoring',
        })
        self._set_tracking(stages['tailoring'])
        supervisor = self._create_supervisor_user(('tailoring',))

        assigned_bom = line.bom_id
        self.assertTrue(assigned_bom)
        self.assertFalse(supervisor.has_group('mrp.group_mrp_user'))
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            assigned_bom.with_user(supervisor).read(['display_name'])
        self.env.invalidate_all()

        data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('tailoring')
        order = next(
            payload for payload in data['orders']
            if payload['id'] == production.id
        )
        product_line = order['product_lines'][0]

        self.assertTrue(data['order_supervisor_mode'])
        self.assertEqual(product_line['production_line_id'], line.id)
        self.assertEqual(product_line['bom_id'], assigned_bom.id)
        self.assertEqual(product_line['bom_name'], assigned_bom.display_name)

        action = production.with_user(
            supervisor
        ).action_open_stage_dashboard_bom(line.id, 'tailoring')
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.stage.product.batch.bom.wizard',
        )
        self.assertEqual(action['target'], 'new')
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(wizard.product_name, line.product_id.display_name)
        self.assertEqual(wizard.model_name, model.display_name)
        self.assertEqual(wizard.stage_name, self.STAGE_LABELS['tailoring'])
        self.assertEqual(wizard.material_count, 1)
        self.assertEqual(wizard.line_ids.material_name, raw_material.display_name)
        self.assertAlmostEqual(wizard.line_ids.required_qty, 3.0)

    def test_order_supervisor_dashboard_uses_hidden_prior_stage_completion(self):
        production, _stages = self._create_production(
            ('priming', 'tailoring'),
            create_stage_orders=False,
        )
        line = self._create_lines(
            production,
            (1.0,),
            ('priming', 'tailoring'),
        ).ensure_one()
        priming = self.env['furniture.mrp.priming'].create({
            'name': 'PRIMING/%s' % production.name,
            'production_order_id': production.id,
            'state': 'done',
        })
        production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).write({'priming_order_id': priming.id})
        self._set_tracking(priming, completed=line)
        supervisor = self._create_supervisor_user(('tailoring',))

        with self.assertRaises(AccessError):
            priming.with_user(supervisor).read(['name'])

        data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('tailoring')
        order = next(
            payload for payload in data['orders']
            if payload['id'] == production.id
        )

        self.assertTrue(data['order_supervisor_mode'])
        self.assertTrue(order['can_request_materials'])
        with self.assertRaises(AccessError):
            priming.with_user(supervisor).read(['name'])

    def test_order_dashboard_receives_issued_advance_release_before_stage_exists(self):
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Advance Dashboard Storekeeper %s' % self._testMethodName,
            'login': 'advance.dashboard.storekeeper.%s@example.test'
                     % self._testMethodName,
            'email': 'advance.dashboard.storekeeper.%s@example.test'
                     % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        production, _stages = self._create_production(
            ('upholstery',), create_stage_orders=False,
        )
        line = self._create_lines(
            production, (2.0,), ('upholstery',),
        ).ensure_one()
        sibling_product = self.env['product.product'].create({
            'name': 'Advance Dashboard Upholstery Sibling %s'
                    % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        sibling_line = self._create_lines(
            production,
            (1.0,),
            ('upholstery',),
            extra_values={
                'product_id': sibling_product.id,
                'sequence': 20,
            },
        ).ensure_one()
        raw_material = self.env['product.product'].create({
            'name': 'Advance Dashboard Upholstery Raw %s'
                    % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': line.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 4.0,
            'stage': 'upholstery',
        })
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': sibling_line.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 3.0,
            'stage': 'upholstery',
        })
        source_location = (
            production.location_src_id
            or self.env.ref('stock.stock_location_stock')
        )
        self.env['stock.quant']._update_available_quantity(
            raw_material, source_location, 10.0,
        )
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_stage_codes(
            production, ('upholstery',), notify_storekeeper=False,
        )
        release_stage = release.stage_line_ids.ensure_one()
        release.sudo().action_issue()
        supervisor = self._create_supervisor_user(('upholstery',))

        data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('upholstery')
        order = next(
            payload for payload in data['orders']
            if payload['id'] == production.id
        )

        self.assertFalse(order['stage_order_id'])
        self.assertEqual(order['store_request_state'], 'awaiting_receipt')
        self.assertFalse(order['can_request_materials'])
        self.assertTrue(order['can_receive_materials'])
        self.assertTrue(order['can_open_material_request'])

        action = production.with_user(
            supervisor
        ).action_stage_dashboard_request_order_materials('upholstery')
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertNotIn('next', action['params'])
        release_stage.invalidate_recordset([
            'receipt_confirmed', 'receipt_state', 'received_by_id',
        ])
        self.assertTrue(release_stage.receipt_confirmed)
        self.assertEqual(release_stage.receipt_state, 'full')
        self.assertEqual(release_stage.received_by_id, supervisor)
        production.invalidate_recordset(['upholstery_order_id'])
        self.assertTrue(production.upholstery_order_id)
        refreshed = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('upholstery')
        refreshed_order = next(
            payload for payload in refreshed['orders']
            if payload['id'] == production.id
        )
        self.assertFalse(refreshed_order['can_receive_materials'])
        self.assertTrue(refreshed_order['can_start_stage'])

        start_action = production.with_user(
            supervisor
        ).action_stage_dashboard_start_order_stage('upholstery')
        expected_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_first_stage_start_wizard_form'
        )
        self.assertEqual(start_action['type'], 'ir.actions.act_window')
        self.assertEqual(
            start_action['res_model'],
            'furniture.mrp.first.stage.start.wizard',
        )
        self.assertEqual(start_action['view_id'], expected_view.id)
        self.assertEqual(start_action['views'], [(expected_view.id, 'form')])
        self.assertEqual(start_action['target'], 'new')

        target_payload = next(
            product_payload
            for product_payload in refreshed_order['product_lines']
            if product_payload['production_line_id'] == line.id
        )
        sibling_payload = next(
            product_payload
            for product_payload in refreshed_order['product_lines']
            if product_payload['production_line_id'] == sibling_line.id
        )
        self.assertTrue(target_payload['can_start_stage'])
        self.assertEqual(
            target_payload['startable_production_line_ids'], [line.id],
        )
        self.assertTrue(sibling_payload['can_start_stage'])

        start_result = production.with_user(
            supervisor
        ).action_stage_dashboard_start_order_product(
            [line.id], 'upholstery',
        )
        self.assertTrue(start_result['started'])
        self.assertEqual(start_result['production_line_ids'], [line.id])
        line.invalidate_recordset([
            'first_stage_started', 'first_stage_started_stage',
        ])
        sibling_line.invalidate_recordset([
            'first_stage_started', 'first_stage_started_stage',
        ])
        production.upholstery_order_id.invalidate_recordset(['state'])
        self.assertTrue(line.first_stage_started)
        self.assertEqual(line.first_stage_started_stage, 'upholstery')
        self.assertFalse(sibling_line.first_stage_started)
        self.assertEqual(production.upholstery_order_id.state, 'in_progress')
        self.assertEqual(
            production.upholstery_order_id._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            line,
        )

        after_start = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('upholstery')
        after_start_order = next(
            payload for payload in after_start['orders']
            if payload['id'] == production.id
        )
        started_product = next(
            payload for payload in after_start_order['product_lines']
            if payload['production_line_id'] == line.id
        )
        waiting_product = next(
            payload for payload in after_start_order['product_lines']
            if payload['production_line_id'] == sibling_line.id
        )
        self.assertAlmostEqual(started_product['working_qty'], 2.0)
        self.assertFalse(started_product['can_start_stage'])
        self.assertAlmostEqual(waiting_product['not_started_qty'], 1.0)
        self.assertTrue(waiting_product['can_start_stage'])
        self.assertTrue(after_start_order['can_start_stage'])

        sibling_start_result = production.with_user(
            supervisor
        ).action_stage_dashboard_start_order_product(
            [sibling_line.id], 'upholstery',
        )
        self.assertTrue(sibling_start_result['started'])
        sibling_line.invalidate_recordset([
            'first_stage_started', 'first_stage_started_stage',
        ])
        self.assertTrue(sibling_line.first_stage_started)
        self.assertEqual(
            production.upholstery_order_id._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            line | sibling_line,
        )
        all_started = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('upholstery')
        all_started_order = next(
            payload for payload in all_started['orders']
            if payload['id'] == production.id
        )
        self.assertFalse(all_started_order['can_start_stage'])
        self.assertTrue(all_started_order['can_finish_stage'])

        finish_result = production.with_user(
            supervisor
        ).action_stage_dashboard_finish_order_stage('upholstery')
        self.assertTrue(finish_result['finished'])
        self.assertEqual(
            set(finish_result['production_line_ids']),
            {line.id, sibling_line.id},
        )
        production.upholstery_order_id.invalidate_recordset(['state'])
        self.assertEqual(production.upholstery_order_id.state, 'done')
        self.assertFalse(
            production.upholstery_order_id._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
        )
        self.assertEqual(
            production.upholstery_order_id._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ),
            line | sibling_line,
        )
        finished_data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('upholstery')
        finished_order = next(
            payload for payload in finished_data['orders']
            if payload['id'] == production.id
        )
        self.assertFalse(finished_order['can_finish_stage'])

    def test_order_stage_actions_reject_disallowed_company(self):
        other_company = self.env['res.company'].create({
            'name': 'Dashboard RPC Other Company %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, other_company.id)]})
        production, _stages = self._create_production(
            ('tailoring',), company=other_company,
        )
        self._create_lines(production, (1.0,), ('tailoring',))
        supervisor = self._create_supervisor_user(('tailoring',))
        restricted_production = production.with_user(supervisor).with_context(
            allowed_company_ids=supervisor.company_ids.ids,
        )

        for method_name in (
            'action_stage_dashboard_request_order_materials',
            'action_stage_dashboard_start_order_stage',
            'action_stage_dashboard_finish_order_stage',
        ):
            with self.assertRaises(AccessError):
                getattr(restricted_production, method_name)('tailoring')

    def test_order_stage_actions_reject_draft_and_cancelled_productions(self):
        supervisor = self._create_supervisor_user(('tailoring',))
        productions = self.Production
        for state in ('draft', 'cancelled'):
            production, stages = self._create_production(
                ('tailoring',),
                state=state,
                name='ORDER-RPC-%s-%s' % (state, self._testMethodName),
            )
            self._create_lines(production, (1.0,), ('tailoring',))
            productions |= production

            for method_name in (
                'action_stage_dashboard_request_order_materials',
                'action_stage_dashboard_start_order_stage',
            ):
                with self.assertRaises(UserError):
                    getattr(
                        production.with_user(supervisor), method_name,
                    )('tailoring')
            stages['tailoring'].invalidate_recordset(['state', 'foreman_id'])
            self.assertEqual(stages['tailoring'].state, 'pending')
            self.assertFalse(stages['tailoring'].foreman_id)

        requests = self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', 'in', productions.ids),
        ])
        self.assertFalse(requests)

    def test_order_material_request_and_receipt_stay_on_dashboard(self):
        self._check_order_material_request_and_receipt_stay_on_dashboard(10.0)

    def test_order_dashboard_receives_only_issued_qty_with_shortage(self):
        self._check_order_material_request_and_receipt_stay_on_dashboard(1.5)

    def _check_order_material_request_and_receipt_stay_on_dashboard(self, stock_qty):
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Order Dashboard Storekeeper %s' % self._testMethodName,
            'login': 'order.dashboard.storekeeper.%s@example.test'
                     % self._testMethodName,
            'email': 'order.dashboard.storekeeper.%s@example.test'
                     % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        model = self.env['furniture.product.model'].create({
            'name': 'Order Request Model %s' % self._testMethodName,
        })
        raw_material = self.env['product.product'].create({
            'name': 'Order Request Tailoring Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': self.product.uom_id.id,
            'type': 'normal',
            'furniture_product_id': self.product.id,
            'furniture_recipe_model_id': model.id,
            'use_tailoring': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'tailoring',
                'product_id': raw_material.id,
                'product_qty': 1.5,
                'product_uom_id': raw_material.uom_id.id,
                'quantity_mode': 'scaled',
            })],
        })
        line_values = {
            'furniture_order_model_id': model.id,
            'bom_id': bom.id,
        }
        production, stages = self._create_production(('tailoring',))
        self._create_lines(
            production, (2.0,), ('tailoring',), extra_values=line_values,
        )
        sibling, _sibling_stages = self._create_production(
            ('tailoring',), name='ORDER-SIBLING-%s' % self._testMethodName,
        )
        self._create_lines(
            sibling, (3.0,), ('tailoring',), extra_values=line_values,
        )
        supervisor = self._create_supervisor_user(('tailoring',))
        scoped_production = production.with_user(supervisor)

        created_action = (
            scoped_production.action_stage_dashboard_request_order_materials(
                'tailoring'
            )
        )
        self.assertEqual(created_action['type'], 'ir.actions.client')
        self.assertEqual(created_action['tag'], 'display_notification')
        self.assertNotIn('next', created_action['params'])

        request = self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', '=', production.id),
            ('stage_order_model', '=', stages['tailoring']._name),
            ('stage_order_res_id', '=', stages['tailoring'].id),
            ('stage_code', '=', 'tailoring'),
        ]).ensure_one()
        self.assertEqual(request.state, 'pending')
        self.assertEqual(request.requested_by_id, supervisor)
        self.assertEqual(request.material_line_ids.product_id, raw_material)
        self.assertAlmostEqual(
            request.material_line_ids.requested_qty, 3.0,
        )
        self.assertFalse(self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', '=', sibling.id),
        ]))

        pending_action = (
            scoped_production.action_stage_dashboard_request_order_materials(
                'tailoring'
            )
        )
        self.assertEqual(pending_action['type'], 'ir.actions.client')
        self.assertEqual(pending_action['tag'], 'display_notification')
        self.assertNotIn('next', pending_action['params'])
        self.assertFalse(request.receipt_confirmed)
        self.assertFalse(request.material_line_ids.issue_move_ids)
        self.assertTrue(request.activity_ids.filtered(
            lambda activity: activity.user_id == request.assigned_to_id
        ))
        self.assertEqual(self.env['furniture.mrp.store.request'].sudo().search_count([
            ('production_id', '=', production.id),
            ('stage_order_model', '=', stages['tailoring']._name),
            ('stage_order_res_id', '=', stages['tailoring'].id),
        ]), 1)

        self.env['stock.quant']._update_available_quantity(
            raw_material, request.source_location_id, stock_qty,
        )
        request.with_user(request.assigned_to_id).action_approve()
        request.invalidate_recordset()
        self.assertEqual(request.state, 'approved')
        self.assertFalse(request.receipt_confirmed)
        issued_moves = request.material_line_ids.issue_move_ids
        self.assertTrue(issued_moves)
        stock_before = self.env['stock.quant']._get_available_quantity(
            raw_material, request.source_location_id,
        )

        received_action = (
            scoped_production.action_stage_dashboard_request_order_materials(
                'tailoring',
            )
        )
        self.assertEqual(received_action['type'], 'ir.actions.client')
        self.assertEqual(received_action['tag'], 'display_notification')
        self.assertEqual(received_action['params']['type'], 'success')
        self.assertNotIn('next', received_action['params'])
        request.invalidate_recordset()
        self.assertTrue(request.receipt_confirmed)
        self.assertEqual(request.receipt_state, 'full' if stock_qty >= 3.0 else 'partial')
        self.assertEqual(request.received_by_id, supervisor)
        self.assertAlmostEqual(request.material_line_ids.received_qty, min(stock_qty, 3.0))
        self.assertEqual(request.state, 'approved')
        self.assertEqual(stages['tailoring'].state, 'pending')
        self.assertFalse(request.material_line_ids.receipt_move_ids)
        self.assertEqual(request.material_line_ids.issue_move_ids, issued_moves)
        self.assertAlmostEqual(
            self.env['stock.quant']._get_available_quantity(
                raw_material, request.source_location_id,
            ), stock_before,
        )

        # Repeated clicks are informational, not another receipt/stock move.
        received_at = request.received_at
        repeated_action = (
            scoped_production.action_stage_dashboard_request_order_materials(
                'tailoring',
            )
        )
        self.assertEqual(repeated_action['tag'], 'display_notification')
        self.assertNotIn('next', repeated_action['params'])
        request.invalidate_recordset()
        self.assertEqual(request.received_at, received_at)
        self.assertEqual(request.material_line_ids.issue_move_ids, issued_moves)
        self.assertEqual(stages['tailoring'].state, 'pending')

    def test_batch_token_is_server_validated_and_bom_summary_is_sanitized(self):
        production, stages = self._create_production(('priming',))
        furniture_model = self.env['furniture.product.model'].create({
            'name': 'Popup Model %s' % self._testMethodName,
        })
        raw_material = self.env['product.product'].create({
            'name': 'Sanitized BoM Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'furniture_product_id': self.product.id,
            'product_qty': 1.0,
            'product_uom_id': self.product.uom_id.id,
            'type': 'normal',
            'furniture_model_id': furniture_model.id,
        })
        production_line = self._create_lines(
            production,
            quantities=(4.0,),
            stage_codes=('priming',),
            extra_values={
                'bom_id': bom.id,
                'furniture_order_model_id': furniture_model.id,
            },
        ).ensure_one()
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'production_line_id': production_line.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 7.5,
            'stage': 'priming',
        })
        self._set_tracking(stages['priming'])
        supervisor = self._create_supervisor_user(('priming',))

        _data, batches = self._supervisor_product_batches(supervisor)
        payload = next(
            batch for batch in batches
            if (
                batch['product']['id'] == self.product.id
                and batch['model']['id'] == furniture_model.id
            )
        )
        # Setting the order model can legitimately materialize a model-specific
        # recipe from the selected generic BoM.  The card must expose the exact
        # recipe that ended up on the production line, not the source template.
        self.assertEqual(payload['bom']['id'], production_line.bom_id.id)
        self.assertTrue(payload['can_open_bom'])

        result = self.Production.with_user(
            supervisor
        ).action_open_stage_dashboard_product_batch_bom(
            payload['batch_token'], 'priming'
        )
        action = result.get('action', result)
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.stage.product.batch.bom.wizard',
        )
        wizard = self.env[action['res_model']].with_user(supervisor).browse(
            action['res_id']
        ).exists().ensure_one()
        self.assertEqual(wizard.product_name, self.product.display_name)
        self.assertEqual(wizard.model_name, furniture_model.display_name)
        self.assertEqual(wizard.stage_name, 'التقديم')
        self.assertTrue(wizard.has_materials)
        self.assertEqual(wizard.material_count, 1)
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(
            wizard.line_ids.material_name, raw_material.display_name,
        )
        self.assertAlmostEqual(wizard.line_ids.required_qty, 7.5)
        self.assertTrue(wizard.line_ids.uom_name)
        self.assertFalse(hasattr(wizard, 'production_id'))
        self._assert_no_order_or_customer_leak(action)

        started_payload = self.env[
            'furniture.mrp.stage.product.batch'
        ]._sanitized_payload({
            'batch_token': payload['batch_token'],
            'identity_key': payload['identity_key'],
            'stage_code': 'priming',
            'product': self.product,
            'model': furniture_model,
            'bom': production_line.bom_id,
            'uom': self.product.uom_id,
            'planned_qty': 4.0,
            'started_at': fields.Datetime.now(),
            'finished_at': False,
        }, 'in_progress')
        self.assertFalse(started_payload['can_open_bom'])

        view_arch = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_product_batch_bom_wizard_form'
        ).arch_db
        self.assertIn('o_furniture_stage_bom_wizard__hero', view_arch)
        self.assertIn('o_furniture_stage_bom_wizard__model', view_arch)
        self.assertIn('o_furniture_stage_bom_wizard__stage', view_arch)
        popup_css = (
            Path(__file__).resolve().parents[1]
            / 'static/src/css/furniture_mrp.css'
        ).read_text(encoding='utf-8')
        self.assertIn(
            '.modal-dialog:has(.o_furniture_stage_bom_wizard)',
            popup_css,
        )

        with self.assertRaises(AccessError):
            self.Production.with_user(
                supervisor
            ).action_open_stage_dashboard_product_batch_bom(
                '%s-forged' % payload['batch_token'], 'priming'
            )
        with self.assertRaises(AccessError):
            self.Production.with_user(
                supervisor
            ).action_open_stage_dashboard_product_batch_bom(
                payload['batch_token'], 'carpentry'
            )
        with self.assertRaises(AccessError):
            self.Production.with_user(supervisor).get_stage_dashboard_data(
                'carpentry'
            )

    def test_product_batch_flow_freezes_membership_and_scopes_materials(self):
        supervisor = self._create_supervisor_user(('priming', 'carpentry'))
        storekeeper = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Batch Storekeeper %s' % self._testMethodName,
            'login': 'batch.storekeeper.%s@example.test' % self._testMethodName,
            'email': 'batch.storekeeper.%s@example.test' % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        production, stages = self._create_production(
            ('priming', 'carpentry')
        )
        other_product = self.env['product.product'].create({
            'name': 'Other Batch Product %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        selected_line = self._create_lines(
            production, (2.0,), ('priming', 'carpentry'),
        )
        other_line = self._create_lines(
            production,
            (7.0,),
            ('priming', 'carpentry'),
            extra_values={'product_id': other_product.id},
        )
        selected_raw = self.env['product.product'].create({
            'name': 'Selected Batch Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        other_raw = self.env['product.product'].create({
            'name': 'Other Batch Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        selected_material, other_material = self.env[
            'furniture.mrp.material.line'
        ].create([
            {
                'production_id': production.id,
                'production_line_id': selected_line.id,
                'product_id': selected_raw.id,
                'product_uom_id': selected_raw.uom_id.id,
                'qty_needed': 4.0,
                'stage': 'priming',
            },
            {
                'production_id': production.id,
                'production_line_id': other_line.id,
                'product_id': other_raw.id,
                'product_uom_id': other_raw.uom_id.id,
                'qty_needed': 9.0,
                'stage': 'priming',
            },
        ])
        production._ensure_stage_locations()
        stock_location = self.env.ref('stock.stock_location_stock')
        self.env['stock.quant']._update_available_quantity(
            selected_raw, stock_location, 10.0,
        )
        self.env['stock.quant']._update_available_quantity(
            other_raw, stock_location, 10.0,
        )
        self._set_tracking(stages['priming'])

        _data, payloads = self._supervisor_product_batches(supervisor)
        payload = next(
            batch for batch in payloads
            if batch['product']['id'] == self.product.id
        )
        token = payload['batch_token']

        request_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_request_product_batch_materials(
            token, 'priming'
        )
        self.assertEqual(request_result['state'], 'waiting_store')

        _data, after_request_payloads = self._supervisor_product_batches(
            supervisor,
        )
        untouched_sibling = next(
            item for item in after_request_payloads
            if item['product']['id'] == other_product.id
        )
        self.assertEqual(untouched_sibling['state'], 'unrequested')
        self.assertTrue(untouched_sibling['can_request_materials'])
        self.assertFalse(untouched_sibling['can_start'])

        # A same-identity line created after the request must never be folded
        # into the immutable snapshot.  It remains visible as a fresh virtual
        # candidate with its own token and quantity.
        late_same_product_line = self._create_lines(
            production, (5.0,), ('priming', 'carpentry'),
        )
        _data, refreshed_payloads = self._supervisor_product_batches(
            supervisor,
        )
        persisted_payload = next(
            item for item in refreshed_payloads
            if item['batch_token'] == token
        )
        late_candidates = [
            item for item in refreshed_payloads
            if (
                item['product']['id'] == self.product.id
                and not item['persisted']
            )
        ]
        self.assertTrue(persisted_payload['persisted'])
        self.assertAlmostEqual(persisted_payload['planned_qty'], 2.0)
        self.assertEqual(len(late_candidates), 1)
        self.assertNotEqual(late_candidates[0]['batch_token'], token)
        self.assertAlmostEqual(late_candidates[0]['planned_qty'], 5.0)

        batch = self.env['furniture.mrp.stage.product.batch'].sudo().search([
            ('token', '=', token),
        ]).ensure_one()
        release = batch.advance_release_id
        release.sudo().write({'assigned_to_id': storekeeper.id})
        release_stage = release.stage_line_ids.ensure_one()

        # Once requested, the server snapshot is the complete authority for
        # membership.  The sibling product and its materials stay outside it.
        self.assertEqual(batch.production_line_ids, selected_line)
        self.assertEqual(
            batch.member_ids.mapped('production_line_id'), selected_line,
        )
        self.assertAlmostEqual(batch.member_ids.qty_snapshot, 2.0)
        self.assertEqual(
            release_stage.source_production_line_ids, selected_line,
        )
        self.assertEqual(
            release_stage.source_material_line_ids, selected_material,
        )
        self.assertEqual(
            release_stage.material_line_ids.mapped('source_material_line_ids'),
            selected_material,
        )
        self.assertEqual(
            release_stage.material_line_ids.mapped('product_id'), selected_raw,
        )
        self.assertAlmostEqual(
            release_stage.material_line_ids.requested_qty, 4.0,
        )
        self.assertNotIn(other_line, batch.production_line_ids)
        self.assertNotIn(late_same_product_line, batch.production_line_ids)
        self.assertNotIn(
            late_same_product_line,
            batch.member_ids.mapped('production_line_id'),
        )
        self.assertNotIn(
            other_material,
            release_stage.material_line_ids.mapped('source_material_line_ids'),
        )
        with self.assertRaises(UserError):
            selected_line.write({'product_qty': 3.0})

        release.with_user(storekeeper).action_issue()
        batch.invalidate_recordset(['state'])
        release_stage.invalidate_recordset(['state', 'receipt_confirmed'])
        self.assertEqual(batch._effective_state(), 'waiting_receipt')
        self.assertEqual(release_stage.state, 'issued')

        receive_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_receive_product_batch_materials(
            token, 'priming'
        )
        self.assertEqual(receive_result['state'], 'ready')
        _data, after_receive_payloads = self._supervisor_product_batches(
            supervisor,
        )
        untouched_sibling = next(
            item for item in after_receive_payloads
            if item['product']['id'] == other_product.id
        )
        self.assertEqual(untouched_sibling['state'], 'unrequested')
        self.assertTrue(untouched_sibling['can_request_materials'])
        self.assertFalse(untouched_sibling['can_start'])
        release_stage.invalidate_recordset(['receipt_confirmed'])
        self.assertTrue(release_stage.receipt_confirmed)

        start_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_start_product_batch(token, 'priming')
        self.assertEqual(start_result['state'], 'in_progress')
        stage = stages['priming']
        stage.invalidate_recordset(['state'])
        self.assertEqual(stage.state, 'in_progress')
        self.assertIn(
            selected_line,
            stage._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
        )
        self.assertNotIn(
            other_line,
            stage._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
        )

        self.assertFalse(batch._dashboard_payload()['quality_ready'])
        with self.assertRaises(UserError):
            self.Production.with_user(supervisor).action_stage_dashboard_finish_product_batch(token, 'priming')
        wrong_stage_user = self._create_supervisor_user(('tailoring',))
        with self.assertRaises(AccessError):
            self.Production.with_user(wrong_stage_user).action_stage_dashboard_review_product_batch_quality(token, 'priming', 'pass')
        self.Production.with_user(supervisor).action_stage_dashboard_review_product_batch_quality(token, 'priming', 'reject')
        with self.assertRaises(UserError):
            self.Production.with_user(supervisor).action_stage_dashboard_finish_product_batch(token, 'priming')
        self.Production.with_user(supervisor).action_stage_dashboard_review_product_batch_quality(token, 'priming', 'pass')
        self.assertEqual(batch.state, 'in_progress')
        self.assertTrue(batch._dashboard_payload()['quality_ready'])
        self.assertFalse(stage._get_stage_line_ids_data('completed_production_line_ids_data'))

        finish_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_finish_product_batch(token, 'priming')
        self.assertEqual(finish_result['state'], 'done')
        batch.invalidate_recordset(['state', 'finished_at'])
        release.invalidate_recordset(['state'])
        self.assertEqual(batch.state, 'done')
        self.assertTrue(batch.finished_at)
        self.assertEqual(release.state, 'completed')
        self.assertIn(
            selected_line,
            stage._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ),
        )
        self.assertNotIn(
            other_line,
            stage._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ),
        )
        output_product = (
            production._get_or_create_dimensioned_finished_product_for_line(
                selected_line
            )
            or selected_line.product_id
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_priming_id,
                output_product,
                selected_line,
                'priming',
            ),
            2.0,
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_carpentry_wip_id,
                output_product,
                selected_line,
                'carpentry',
            ),
            0.0,
        )
        carpentry_candidates = (
            production._get_stage_pending_start_line_candidates(
                stages['carpentry'], 'carpentry',
            )
        )
        self.assertNotIn(selected_line, carpentry_candidates)
        self.assertNotIn(other_line, carpentry_candidates)
        self.assertNotIn(late_same_product_line, carpentry_candidates)

        automatic_moves = self.env['stock.move'].sudo().search([
            ('origin', '=', production.name),
            ('product_id', '=', output_product.id),
            ('location_id', '=', production.location_priming_id.id),
            (
                'location_dest_id', '=',
                production.location_carpentry_wip_id.id,
            ),
            ('furniture_source_production_line_id', '=', selected_line.id),
            ('state', '=', 'assigned'),
        ])
        self.assertEqual(len(automatic_moves), 1)
        self.assertEqual(
            automatic_moves.furniture_source_production_line_ids,
            selected_line,
        )
        handoff = automatic_moves.furniture_stage_transfer_handoff_id
        self.assertEqual(handoff.state, 'pending')
        self.assertFalse(
            production._auto_transfer_completed_stage_lines(
                'priming', selected_line,
            )
        )
        self.assertEqual(
            self.env['stock.move'].sudo().search_count([
                ('origin', '=', production.name),
                ('product_id', '=', output_product.id),
                ('location_id', '=', production.location_priming_id.id),
                (
                    'location_dest_id', '=',
                    production.location_carpentry_wip_id.id,
                ),
                ('furniture_source_production_line_id', '=', selected_line.id),
                ('state', '=', 'assigned'),
            ]),
            1,
        )

        production.with_user(supervisor).action_accept_handoff_transfer(
            stage_handoff_id=handoff.id,
        )
        handoff.invalidate_recordset(['state'])
        automatic_moves.invalidate_recordset(['state'])
        self.assertEqual(handoff.state, 'accepted')
        self.assertEqual(automatic_moves.state, 'done')
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_priming_id,
                output_product,
                selected_line,
                'priming',
            ),
            0.0,
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty_for_line(
                production.location_carpentry_wip_id,
                output_product,
                selected_line,
                'carpentry',
            ),
            2.0,
        )
        self.assertIn(
            selected_line,
            production._get_stage_pending_start_line_candidates(
                stages['carpentry'], 'carpentry',
            ),
        )

        # A completed sibling has already consumed its own priming materials.
        # Requesting the untouched product must validate only that product's
        # frozen line, not reject the whole production order because of the
        # completed sibling.
        _remaining_data, remaining_batches = (
            self._supervisor_product_batches(supervisor)
        )
        sibling_payload = next(
            payload for payload in remaining_batches
            if payload['product']['id'] == other_product.id
        )
        sibling_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_request_product_batch_materials(
            sibling_payload['batch_token'], 'priming'
        )
        self.assertEqual(sibling_result['state'], 'waiting_store')
        sibling_batch = self.env[
            'furniture.mrp.stage.product.batch'
        ].sudo().search([
            ('token', '=', sibling_payload['batch_token']),
        ]).ensure_one()
        self.assertEqual(
            sibling_batch.member_ids.mapped('production_line_id'),
            other_line,
        )
        self.assertEqual(
            sibling_batch.advance_release_stage_ids.source_production_line_ids,
            other_line,
        )

    def test_auto_handoff_uses_each_lines_immediate_physical_stage(self):
        carpentry_supervisor = self._create_supervisor_user(('carpentry',))
        bases_supervisor = self._create_supervisor_user(('bases',))
        production, stages = self._create_production(
            ('priming', 'carpentry', 'bases')
        )
        bases_product = self.env['product.product'].create({
            'name': 'Direct Bases Product %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        carpentry_line = self._create_lines(
            production,
            (2.0,),
            ('priming', 'carpentry', 'bases'),
        ).ensure_one()
        bases_line = self._create_lines(
            production,
            (3.0,),
            ('priming', 'bases'),
            extra_values={'product_id': bases_product.id},
        ).ensure_one()
        production._ensure_stage_locations()
        production_location = production._get_production_location()
        for line in carpentry_line | bases_line:
            production._create_internal_move(
                production_location,
                production.location_priming_id,
                'Accepted priming output for automatic handoff',
                product=line.product_id,
                quantity=line.product_qty,
                uom=line.product_uom_id,
                source_production_line=line,
            )
        self._set_tracking(
            stages['priming'], completed=carpentry_line | bases_line,
        )

        moves = production._auto_transfer_completed_stage_lines(
            'priming', carpentry_line | bases_line,
        )

        self.assertEqual(len(moves), 2)
        carpentry_move = moves.filtered(
            lambda move: move.furniture_source_production_line_id
            == carpentry_line
        )
        bases_move = moves.filtered(
            lambda move: move.furniture_source_production_line_id
            == bases_line
        )
        self.assertEqual(len(carpentry_move), 1)
        self.assertEqual(
            carpentry_move.location_dest_id,
            production.location_carpentry_wip_id,
        )
        self.assertEqual(len(bases_move), 1)
        self.assertEqual(
            bases_move.location_dest_id,
            production.location_bases_wip_id,
        )

        self.assertEqual(carpentry_move.state, 'assigned')
        self.assertEqual(bases_move.state, 'assigned')
        self.assertEqual(
            carpentry_move.furniture_stage_transfer_handoff_id.target_stage,
            'carpentry',
        )
        self.assertEqual(
            bases_move.furniture_stage_transfer_handoff_id.target_stage,
            'bases',
        )

        self.assertNotIn(
            carpentry_line,
            production._get_stage_pending_start_line_candidates(
                stages['carpentry'], 'carpentry',
            ),
        )
        self.assertNotIn(
            bases_line,
            production._get_stage_pending_start_line_candidates(
                stages['bases'], 'bases',
            ),
        )

        production.with_user(
            carpentry_supervisor
        ).action_accept_handoff_transfer(
            stage_handoff_id=(
                carpentry_move.furniture_stage_transfer_handoff_id.id
            ),
        )
        production.with_user(bases_supervisor).action_accept_handoff_transfer(
            stage_handoff_id=bases_move.furniture_stage_transfer_handoff_id.id,
        )
        moves.invalidate_recordset(['state'])
        self.assertTrue(all(move.state == 'done' for move in moves))

        carpentry_candidates = (
            production._get_stage_pending_start_line_candidates(
                stages['carpentry'], 'carpentry',
            )
        )
        bases_candidates = production._get_stage_pending_start_line_candidates(
            stages['bases'], 'bases',
        )
        self.assertIn(carpentry_line, carpentry_candidates)
        self.assertNotIn(bases_line, carpentry_candidates)
        self.assertIn(bases_line, bases_candidates)
        self.assertNotIn(carpentry_line, bases_candidates)

    def test_product_cards_reuse_issued_aggregate_release_for_receipt(self):
        supervisor = self._create_supervisor_user(('priming',))
        storekeeper = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Aggregate Storekeeper %s' % self._testMethodName,
            'login': 'aggregate.storekeeper.%s@example.test'
            % self._testMethodName,
            'email': 'aggregate.storekeeper.%s@example.test'
            % self._testMethodName,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_storekeeper'
                ).id,
            ])],
        })
        production, stages = self._create_production(('priming',))
        second_product = self.env['product.product'].create({
            'name': 'Second Aggregate Product %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        first_line = self._create_lines(
            production, (2.0,), ('priming',),
        )
        second_line = self._create_lines(
            production,
            (3.0,),
            ('priming',),
            extra_values={'product_id': second_product.id},
        )
        shared_raw = self.env['product.product'].create({
            'name': 'Shared Aggregate Raw %s' % self._testMethodName,
            'type': 'consu',
            'is_storable': True,
        })
        first_material, second_material = self.env[
            'furniture.mrp.material.line'
        ].create([
            {
                'production_id': production.id,
                'production_line_id': first_line.id,
                'product_id': shared_raw.id,
                'product_uom_id': shared_raw.uom_id.id,
                'qty_needed': 4.0,
                'stage': 'priming',
            },
            {
                'production_id': production.id,
                'production_line_id': second_line.id,
                'product_id': shared_raw.id,
                'product_uom_id': shared_raw.uom_id.id,
                'qty_needed': 5.0,
                'stage': 'priming',
            },
        ])
        production._ensure_stage_locations()
        stock_location = self.env.ref('stock.stock_location_stock')
        self.env['stock.quant']._update_available_quantity(
            shared_raw, stock_location, 10.0,
        )
        self._set_tracking(stages['priming'])

        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_production_stage_map(
            production,
            {production.id: {'priming'}},
            notify_storekeeper=False,
            is_batch_request=True,
        )
        release.sudo().write({'assigned_to_id': storekeeper.id})
        release.with_user(storekeeper).action_issue()
        release_count = self.env[
            'furniture.mrp.advance.material.release'
        ].sudo().search_count([])

        _data, payloads = self._supervisor_product_batches(supervisor)
        matching = [
            payload for payload in payloads
            if payload['product']['id'] in (
                self.product.id, second_product.id,
            )
        ]
        self.assertEqual(len(matching), 2)
        self.assertTrue(all(
            payload['state'] == 'waiting_receipt'
            and payload['can_receive_materials']
            and not payload['can_request_materials']
            for payload in matching
        ))

        # A stale browser click must adopt the existing aggregate release
        # instead of raising the duplicate-release error shown to supervisors.
        first_payload = next(
            payload for payload in matching
            if payload['product']['id'] == self.product.id
        )
        stale_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_request_product_batch_materials(
            first_payload['batch_token'], 'priming'
        )
        self.assertEqual(stale_result['state'], 'waiting_receipt')
        self.assertEqual(
            self.env['furniture.mrp.advance.material.release'].sudo(
            ).search_count([]),
            release_count,
        )
        batch = self.env['furniture.mrp.stage.product.batch'].sudo().search([
            ('token', '=', first_payload['batch_token']),
        ]).ensure_one()
        self.assertEqual(batch.advance_release_id, release)
        self.assertEqual(
            batch.advance_release_stage_ids,
            release.stage_line_ids,
        )

        receive_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_receive_product_batch_materials(
            first_payload['batch_token'], 'priming'
        )
        self.assertEqual(receive_result['state'], 'ready')
        release.stage_line_ids.invalidate_recordset([
            'receipt_confirmed', 'receipt_state',
        ])
        first_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'warehouse_receipt_stage_id', 'move_id',
        ])
        second_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
            'warehouse_receipt_stage_id', 'move_id',
        ])
        self.assertFalse(release.stage_line_ids.receipt_confirmed)
        self.assertEqual(release.stage_line_ids.receipt_state, 'partial')
        self.assertTrue(first_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(first_material.warehouse_received_qty, 4.0)
        self.assertFalse(second_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(second_material.warehouse_received_qty, 0.0)
        handover = release.stage_line_ids._material_handover_location()
        destination = release.stage_line_ids.destination_location_id
        self.assertAlmostEqual(
            production._stage_location_product_qty(handover, shared_raw),
            5.0,
        )
        self.assertAlmostEqual(
            production._stage_location_product_qty(destination, shared_raw),
            4.0,
        )
        receipt_moves = release.stage_line_ids.material_line_ids.receipt_move_ids
        self.assertEqual(len(receipt_moves), 1)
        self.assertEqual(
            receipt_moves.furniture_source_production_line_ids, first_line,
        )

        start_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_start_product_batch(
            first_payload['batch_token'], 'priming'
        )
        self.assertEqual(start_result['state'], 'in_progress')
        release.stage_line_ids.invalidate_recordset(['state'])
        self.assertEqual(release.stage_line_ids.state, 'started')

        _data, refreshed = self._supervisor_product_batches(supervisor)
        second_payload = next(
            payload for payload in refreshed
            if payload['product']['id'] == second_product.id
        )
        self.assertEqual(second_payload['state'], 'waiting_receipt')
        self.assertTrue(second_payload['can_receive_materials'])
        self.assertFalse(second_payload['can_start'])
        self.assertFalse(second_payload['can_request_materials'])

        # Hall receipt belongs to the displayed product, not to the stage as a
        # whole.  A running first product must not stop the supervisor from
        # receiving the already-issued share of the second product in advance.
        second_receive_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_receive_product_batch_materials(
            second_payload['batch_token'], 'priming'
        )
        self.assertEqual(second_receive_result['state'], 'ready')
        first_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])
        second_material.invalidate_recordset([
            'warehouse_receipt_confirmed', 'warehouse_received_qty',
        ])
        self.assertTrue(first_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(first_material.warehouse_received_qty, 4.0)
        self.assertTrue(second_material.warehouse_receipt_confirmed)
        self.assertAlmostEqual(second_material.warehouse_received_qty, 5.0)

        second_batch = self.env[
            'furniture.mrp.stage.product.batch'
        ].sudo().search([
            ('token', '=', second_payload['batch_token']),
        ]).ensure_one()
        self.assertEqual(second_batch.state, 'ready')
        batch.invalidate_recordset(['state'])
        self.assertEqual(batch.state, 'in_progress')
        with self.assertRaises(UserError):
            self.Production.with_user(
                supervisor
            ).action_stage_dashboard_start_product_batch(
                second_payload['batch_token'], 'priming'
            )

        # Parallel execution is an explicit, order-scoped opt-in.  It lets a
        # second product batch join an already running stage order while both
        # batch ledgers remain independently startable and finishable.
        production.allow_parallel_product_batches = True
        second_start_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_start_product_batch(
            second_payload['batch_token'], 'priming'
        )
        self.assertEqual(second_start_result['state'], 'in_progress')
        stage_order = production._stage_order_record('priming')
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertEqual(
            stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            first_line | second_line,
        )

        self.Production.with_user(supervisor).action_stage_dashboard_review_product_batch_quality(first_payload['batch_token'], 'priming', 'pass')
        self.assertFalse(second_batch._dashboard_payload()['quality_ready'])
        first_finish_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_finish_product_batch(
            first_payload['batch_token'], 'priming'
        )
        self.assertEqual(first_finish_result['state'], 'done')
        second_batch.invalidate_recordset(['state'])
        stage_order.invalidate_recordset(['state'])
        self.assertEqual(second_batch.state, 'in_progress')
        self.assertEqual(stage_order.state, 'in_progress')
        self.assertEqual(
            stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            ),
            second_line,
        )

        self.Production.with_user(supervisor).action_stage_dashboard_review_product_batch_quality(second_payload['batch_token'], 'priming', 'pass')
        second_finish_result = self.Production.with_user(
            supervisor
        ).action_stage_dashboard_finish_product_batch(
            second_payload['batch_token'], 'priming'
        )
        self.assertEqual(second_finish_result['state'], 'done')
        stage_order.invalidate_recordset(['state'])
        self.assertEqual(stage_order.state, 'done')

    def test_overdue_orders_are_company_scoped_open_and_due(self):
        dashboard_company = self.env['res.company'].create({
            'name': 'Overdue Dashboard %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, dashboard_company.id)]})
        now = fields.Datetime.now()

        def create_order(state, planned_finish):
            production, _stages = self._create_production(
                ('priming',), company=dashboard_company, state=state,
                create_stage_orders=False,
            )
            production.date_planned_finish = planned_finish
            return production

        overdue = create_order('confirmed', now - timedelta(days=2))
        overdue_draft = create_order('draft', now - timedelta(hours=3))
        future = create_order('confirmed', now + timedelta(days=1))
        finished = create_order('done', now - timedelta(days=3))
        cancelled = create_order('cancelled', now - timedelta(days=4))
        no_deadline = create_order('confirmed', False)

        payload = self.Production.with_company(dashboard_company).with_context(
            allowed_company_ids=[dashboard_company.id],
        ).get_overdue_dashboard_orders(limit=1)

        self.assertEqual(payload['count'], 2)
        self.assertTrue(payload['limited'])
        self.assertEqual(len(payload['orders']), 1)
        self.assertEqual(payload['orders'][0]['id'], overdue.id)
        self.assertIn('متأخر', payload['orders'][0]['overdue_label'])
        self.assertEqual(payload['orders'][0]['state_label'], 'مؤكد - جاهز للإنتاج')

        all_payload = self.Production.with_company(
            dashboard_company
        ).with_context(
            allowed_company_ids=[dashboard_company.id],
        ).get_overdue_dashboard_orders()
        order_ids = {order['id'] for order in all_payload['orders']}
        self.assertEqual(order_ids, {overdue.id, overdue_draft.id})
        self.assertFalse(all_payload['limited'])
        self.assertFalse({
            future.id, finished.id, cancelled.id, no_deadline.id,
        } & order_ids)

    def test_planned_start_date_filter_limits_orders_and_stage_totals(self):
        dashboard_company = self.env['res.company'].create({
            'name': 'Stage Date Filter %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, dashboard_company.id)]})
        productions = self.Production
        for planned_start, quantity in (
            ('2026-07-10 08:00:00', 3.0),
            ('2026-08-10 08:00:00', 5.0),
            ('2026-09-10 08:00:00', 7.0),
        ):
            production, stages = self._create_production(
                ('priming',), company=dashboard_company
            )
            production.date_planned_start = planned_start
            lines = self._create_lines(
                production,
                quantities=(quantity,),
                stage_codes=('priming',),
            )
            self._set_tracking(stages['priming'], active=lines)
            productions |= production

        all_data = self._dashboard('priming', company=dashboard_company)
        filtered_data = self._dashboard(
            'priming',
            company=dashboard_company,
            date_from='2026-08-01',
            date_to='2026-08-31',
        )
        all_order_ids = {order['id'] for order in all_data['orders']}
        filtered_order_ids = {
            order['id'] for order in filtered_data['orders']
        }
        filtered_stage = next(
            stage for stage in filtered_data['stages']
            if stage['code'] == 'priming'
        )

        self.assertTrue(set(productions.ids).issubset(all_order_ids))
        self.assertEqual(filtered_order_ids, {productions[1].id})
        self.assertEqual(filtered_stage['order_count'], 1)
        self.assertAlmostEqual(filtered_stage['planned_qty'], 5.0)
        self.assertEqual(filtered_data['date_filter'], {
            'date_from': '2026-08-01',
            'date_to': '2026-08-31',
            'field': 'date_planned_start',
        })

    def test_stage_date_filter_accepts_open_bounds_and_rejects_reverse_range(self):
        production, stages = self._create_production(('priming',))
        production.date_planned_start = '2026-08-10 08:00:00'
        lines = self._create_lines(production, (2.0,), ('priming',))
        self._set_tracking(stages['priming'], active=lines)

        from_only_ids = {
            order['id'] for order in self._dashboard(
                'priming', date_from='2026-08-01'
            )['orders']
        }
        to_only_ids = {
            order['id'] for order in self._dashboard(
                'priming', date_to='2026-08-31'
            )['orders']
        }

        self.assertIn(production.id, from_only_ids)
        self.assertIn(production.id, to_only_ids)
        with self.assertRaises(ValidationError):
            self._dashboard(
                'priming',
                date_from='2026-09-01',
                date_to='2026-08-01',
            )

    def test_stage_navigation_is_canonical_and_defaults_to_first_active_stage(self):
        dashboard_company = self.env['res.company'].create({
            'name': 'Stage Dashboard Navigation %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, dashboard_company.id)]})
        production, stages = self._create_production(
            ('carpentry',), company=dashboard_company
        )
        lines = self._create_lines(
            production,
            quantities=(4.0,),
            stage_codes=('carpentry',),
        )
        self._set_tracking(stages['carpentry'], active=lines)

        data = self._dashboard(False, company=dashboard_company)

        self.assertEqual(
            [stage['code'] for stage in data['stages']],
            list(self.STAGE_LABELS),
        )
        self.assertEqual(
            [stage['label'] for stage in data['stages']],
            list(self.STAGE_LABELS.values()),
        )
        self.assertEqual(data['selected_stage'], 'carpentry')
        self.assertEqual(
            [order['id'] for order in data['orders']],
            [production.id],
        )

    def test_selected_stage_payload_does_not_leak_other_stage_orders(self):
        priming_production, priming_stages = self._create_production(
            ('priming',)
        )
        priming_lines = self._create_lines(
            priming_production, (5.0,), ('priming',)
        )
        self._set_tracking(
            priming_stages['priming'], active=priming_lines
        )

        carpentry_production, carpentry_stages = self._create_production(
            ('carpentry',)
        )
        carpentry_lines = self._create_lines(
            carpentry_production, (7.0,), ('carpentry',)
        )
        self._set_tracking(
            carpentry_stages['carpentry'], active=carpentry_lines
        )

        priming_data = self._dashboard('priming')
        carpentry_data = self._dashboard('carpentry')

        self.assertEqual(priming_data['selected_stage'], 'priming')
        self.assertEqual(carpentry_data['selected_stage'], 'carpentry')
        self.assertIn(
            priming_production.id,
            {order['id'] for order in priming_data['orders']},
        )
        self.assertNotIn(
            carpentry_production.id,
            {order['id'] for order in priming_data['orders']},
        )
        self.assertIn(
            carpentry_production.id,
            {order['id'] for order in carpentry_data['orders']},
        )
        self.assertNotIn(
            priming_production.id,
            {order['id'] for order in carpentry_data['orders']},
        )

    def test_stage_summary_kpis_equal_drilldown_order_contributions(self):
        first, first_stages = self._create_production(('priming',))
        first_lines = self._create_lines(
            first, quantities=(2.0, 3.0), stage_codes=('priming',)
        )
        self._set_tracking(
            first_stages['priming'],
            active=first_lines,
            completed=first_lines[1:],
        )

        second, second_stages = self._create_production(('priming',))
        second_lines = self._create_lines(
            second, quantities=(4.0, 6.0), stage_codes=('priming',)
        )
        self._set_tracking(
            second_stages['priming'],
            active=second_lines,
            quality=second_lines[1:],
        )

        data = self._dashboard('priming')
        summary = next(
            stage for stage in data['stages']
            if stage['code'] == 'priming'
        )
        order_ids = {first.id, second.id}
        orders = data['orders']
        matching_orders = [
            order for order in data['orders']
            if order['id'] in order_ids
        ]
        self.assertEqual(
            {order['id'] for order in matching_orders}, order_ids
        )
        self.assertEqual(summary['order_count'], len(orders))

        for field_name in (
            'planned_qty',
            'started_qty',
            'working_qty',
            'quality_qty',
            'completed_qty',
            'not_started_qty',
            'remaining_qty',
        ):
            self.assertAlmostEqual(
                summary[field_name],
                sum(order[field_name] for order in orders),
                msg=field_name,
            )

        for order in matching_orders:
            self.assertEqual(order['stage_code'], 'priming')
            self.assertTrue(order['name'])
            self.assertTrue(order['stage_order_id'])
            self.assertTrue(order['state_label'])
            self.assertAlmostEqual(
                order['planned_qty'],
                order['working_qty']
                + order['completed_qty']
                + order['remaining_qty'],
            )

    def test_confirmed_lines_are_planned_before_any_stage_record_exists(self):
        production, stages = self._create_production(
            ('priming', 'carpentry'),
            create_stage_orders=False,
        )
        self.assertFalse(stages)
        self._create_lines(
            production,
            quantities=(5.0, 7.0),
            stage_codes=('priming', 'carpentry'),
        )

        priming_data, priming_payload = self._order_payload(
            production, 'priming'
        )
        _carpentry_data, carpentry_payload = self._order_payload(
            production, 'carpentry'
        )

        for payload in (priming_payload, carpentry_payload):
            self.assertFalse(payload['stage_order_id'])
            self.assertEqual(payload['state'], 'not_started')
            self.assertEqual(payload['state_label'], 'لم يبدأ')
            self._assert_quantities(
                payload,
                planned_qty=12.0,
                started_qty=0.0,
                working_qty=0.0,
                quality_qty=0.0,
                completed_qty=0.0,
                not_started_qty=12.0,
                remaining_qty=12.0,
                progress=0.0,
            )
        priming_summary = next(
            row for row in priming_data['stages']
            if row['code'] == 'priming'
        )
        self.assertIn(production.id, {
            order['id'] for order in priming_data['orders']
        })
        self.assertGreaterEqual(priming_summary['planned_qty'], 12.0)

    def test_order_payload_obeys_planned_working_completed_remaining_equation(self):
        production, stages = self._create_production(('priming',))
        lines = self._create_lines(
            production,
            quantities=(4.0, 6.0, 3.0),
            stage_codes=('priming',),
        )
        self._set_tracking(
            stages['priming'],
            active=lines[:2],
            completed=lines[1:2],
        )

        _data, payload = self._order_payload(production, 'priming')

        self._assert_quantities(
            payload,
            planned_qty=13.0,
            working_qty=4.0,
            completed_qty=6.0,
            not_started_qty=3.0,
            remaining_qty=3.0,
        )
        self.assertAlmostEqual(
            payload['planned_qty'],
            payload['working_qty']
            + payload['completed_qty']
            + payload['remaining_qty'],
        )

    def test_order_payload_is_product_first_and_exposes_report_details(self):
        production, stages = self._create_production(('priming',))
        furniture_model = self.env['furniture.product.model'].create({
            'name': 'Dashboard Model %s' % self._testMethodName,
        })
        buyer = self.env['res.partner'].create({
            'name': 'Dashboard Buyer %s' % self._testMethodName,
            'is_company': True,
        })
        beneficiary = self.env['res.partner'].create({
            'name': 'Dashboard Beneficiary %s' % self._testMethodName,
        })
        lines = self._create_lines(
            production,
            quantities=(4.0, 6.0),
            stage_codes=('priming',),
            extra_values={
                'furniture_order_model_id': furniture_model.id,
                'buyer_partner_id': buyer.id,
                'beneficiary_partner_id': beneficiary.id,
            },
        )
        foreman = self.env['hr.employee'].create({
            'name': 'Dashboard Foreman %s' % self._testMethodName,
            'company_id': self.env.company.id,
        })
        worker = self.env['hr.employee'].create({
            'name': 'Dashboard Worker %s' % self._testMethodName,
            'company_id': self.env.company.id,
        })
        stage = stages['priming']
        stage.write({
            'state': 'in_progress',
            'foreman_id': foreman.id,
            'worker_ids': [(6, 0, worker.ids)],
        })
        self._set_tracking(
            stage,
            active=lines,
            completed=lines[1:],
        )

        _data, payload = self._order_payload(production, 'priming')

        self.assertEqual(payload['foreman_name'], foreman.display_name)
        self.assertEqual(payload['worker_names'], [worker.display_name])
        self.assertEqual(payload['model_name'], furniture_model.display_name)
        self.assertEqual(payload['buyer_summary'], buyer.display_name)
        self.assertEqual(
            payload['beneficiary_summary'], beneficiary.display_name
        )
        self.assertEqual(len(payload['product_lines']), 1)
        product_payload = payload['product_lines'][0]
        self.assertEqual(
            product_payload['product_name'], self.product.display_name
        )
        self.assertEqual(
            product_payload['model_name'], furniture_model.display_name
        )
        self.assertEqual(product_payload['buyer_name'], buyer.display_name)
        self.assertEqual(
            product_payload['beneficiary_name'], beneficiary.display_name
        )
        self.assertEqual(product_payload['uom_id'], self.product.uom_id.id)
        self.assertEqual(product_payload['uom'], self.product.uom_id.display_name)
        self._assert_quantities(
            product_payload,
            planned_qty=10.0,
            started_qty=10.0,
            working_qty=4.0,
            completed_qty=6.0,
            not_started_qty=0.0,
            remaining_qty=0.0,
        )

    def test_supervisor_dashboard_reads_employee_public_names(self):
        production, stages = self._create_production(('tailoring',))
        lines = self._create_lines(
            production,
            quantities=(2.0,),
            stage_codes=('tailoring',),
        )
        foreman = self.env['hr.employee'].create({
            'name': 'Public Dashboard Foreman %s' % self._testMethodName,
            'company_id': self.env.company.id,
            'furniture_mrp_factory_department': 'tailoring',
            'furniture_mrp_role': 'supervisor',
        })
        worker = self.env['hr.employee'].create({
            'name': 'Public Dashboard Worker %s' % self._testMethodName,
            'company_id': self.env.company.id,
            'furniture_mrp_factory_department': 'tailoring',
            'furniture_mrp_role': 'worker',
        })
        stages['tailoring'].write({
            'state': 'in_progress',
            'foreman_id': foreman.id,
            'worker_ids': [(6, 0, worker.ids)],
        })
        self._set_tracking(stages['tailoring'], active=lines)
        supervisor = self._create_supervisor_user(('tailoring',))
        self.assertFalse(supervisor.has_group('hr.group_hr_user'))

        self.env.invalidate_all()
        data = self.Production.with_user(
            supervisor
        ).get_stage_dashboard_data('tailoring')
        payload = next(
            item for item in data['orders']
            if item['id'] == production.id
        )

        self.assertEqual(payload['foreman_id'], foreman.id)
        self.assertEqual(payload['foreman_name'], foreman.display_name)
        self.assertEqual(payload['worker_ids'], worker.ids)
        self.assertEqual(payload['worker_names'], [worker.display_name])

    def test_tracking_union_does_not_double_count_overlapping_lines(self):
        production, stages = self._create_production(('priming',))
        lines = self._create_lines(
            production,
            quantities=(5.0, 10.0, 15.0),
            stage_codes=('priming',),
        )
        working_line = lines[:1]
        quality_line = lines[1:2]
        completed_line = lines[2:]
        stage = stages['priming']
        stage.write({'state': 'quality_check'})

        # A line can still be present in the active JSON while it is in quality,
        # and stale overlap with the completed JSON must not inflate started work.
        self._set_tracking(
            stage,
            active=working_line | quality_line | completed_line,
            quality=quality_line | completed_line,
            completed=completed_line,
        )

        _data, payload = self._order_payload(production, 'priming')
        self._assert_quantities(
            payload,
            planned_qty=30.0,
            started_qty=30.0,
            working_qty=15.0,
            quality_qty=10.0,
            completed_qty=15.0,
            not_started_qty=0.0,
            remaining_qty=0.0,
            progress=50.0,
        )
        self.assertAlmostEqual(
            payload['planned_qty'],
            payload['working_qty']
            + payload['completed_qty']
            + payload['remaining_qty'],
        )
        self.assertLessEqual(payload['quality_qty'], payload['working_qty'])

    def test_pending_partial_stage_stays_visible_at_half_progress(self):
        production, stages = self._create_production(('priming',))
        lines = self._create_lines(
            production,
            quantities=(15.0, 15.0),
            stage_codes=('priming',),
        )
        stage = stages['priming']
        self.assertEqual(stage.state, 'pending')
        self._set_tracking(stage, completed=lines[:1])

        data, payload = self._order_payload(production, 'priming')

        self.assertIn('priming', {row['code'] for row in data['stages']})
        self.assertEqual(payload['state'], 'pending')
        self._assert_quantities(
            payload,
            planned_qty=30.0,
            started_qty=15.0,
            working_qty=0.0,
            quality_qty=0.0,
            completed_qty=15.0,
            not_started_qty=15.0,
            remaining_qty=15.0,
            progress=50.0,
        )

    def test_fully_completed_stage_remains_visible_in_active_order_plan(self):
        production, stages = self._create_production(('priming',))
        lines = self._create_lines(
            production,
            quantities=(15.0, 15.0),
            stage_codes=('priming',),
        )
        stage = stages['priming']
        stage.write({'state': 'done'})
        self._set_tracking(stage, completed=lines)

        _data, payload = self._order_payload(production, 'priming')

        self._assert_quantities(
            payload,
            planned_qty=30.0,
            working_qty=0.0,
            completed_qty=30.0,
            remaining_qty=0.0,
            progress=100.0,
        )

    def test_draft_and_closed_orders_do_not_inflate_live_stage_plan(self):
        draft, _draft_stages = self._create_production(
            ('priming',), state='draft', create_stage_orders=False
        )
        self._create_lines(draft, (5.0,), ('priming',))
        closed, _closed_stages = self._create_production(
            ('priming',), state='done', create_stage_orders=False
        )
        self._create_lines(closed, (7.0,), ('priming',))

        order_ids = {
            order['id'] for order in self._dashboard('priming')['orders']
        }

        self.assertNotIn(draft.id, order_ids)
        self.assertNotIn(closed.id, order_ids)

    def test_same_production_can_appear_in_two_stage_dashboards(self):
        production, stages = self._create_production(
            ('priming', 'carpentry')
        )
        lines = self._create_lines(
            production,
            quantities=(12.0, 18.0),
            stage_codes=('priming', 'carpentry'),
        )
        for stage in stages.values():
            stage.write({'state': 'in_progress'})
            self._set_tracking(stage, active=lines)

        priming_data, priming_payload = self._order_payload(
            production, 'priming'
        )
        carpentry_data, carpentry_payload = self._order_payload(
            production, 'carpentry'
        )

        self.assertEqual(priming_payload['id'], production.id)
        self.assertEqual(carpentry_payload['id'], production.id)
        self.assertEqual(priming_payload['stage_label'], 'التقديم')
        self.assertEqual(carpentry_payload['stage_label'], 'تجميع')
        self.assertIn('priming', {row['code'] for row in priming_data['stages']})
        self.assertIn(
            'carpentry', {row['code'] for row in carpentry_data['stages']}
        )

    def test_allowed_company_context_isolates_stage_orders(self):
        current_company = self.env.company
        other_company = self.env['res.company'].create({
            'name': 'Stage Dashboard Other Company %s' % self._testMethodName,
        })
        self.env.user.write({'company_ids': [(4, other_company.id)]})

        current_production, current_stages = self._create_production(
            ('priming',), company=current_company
        )
        current_lines = self._create_lines(
            current_production, (3.0,), ('priming',)
        )
        self._set_tracking(
            current_stages['priming'], active=current_lines
        )

        other_production, other_stages = self._create_production(
            ('priming',), company=other_company
        )
        other_lines = self._create_lines(
            other_production, (7.0,), ('priming',)
        )
        self._set_tracking(other_stages['priming'], active=other_lines)

        current_ids = {
            order['id']
            for order in self._dashboard(
                'priming', company=current_company
            )['orders']
        }
        other_ids = {
            order['id']
            for order in self._dashboard(
                'priming', company=other_company
            )['orders']
        }

        self.assertIn(current_production.id, current_ids)
        self.assertNotIn(other_production.id, current_ids)
        self.assertIn(other_production.id, other_ids)
        self.assertNotIn(current_production.id, other_ids)

    def test_product_warning_is_sent_upstream_and_reminded_once(self):
        furniture_model = self.env['furniture.product.model'].create({
            'name': 'Big Moon warning %s' % self._testMethodName,
        })
        source_supervisor = self._create_supervisor_user(('carpentry',))
        target_supervisor = self._create_supervisor_user(('priming',))
        route = ('priming', 'carpentry')
        origin_production, _origin_stages = self._create_production(route)
        origin_line = self._create_lines(
            origin_production,
            (1.0,),
            route,
            extra_values={'furniture_order_model_id': furniture_model.id},
        ).ensure_one()

        action = origin_production.with_user(
            source_supervisor
        ).action_open_stage_dashboard_order_product_warning(
            origin_line.id,
            'carpentry',
        )
        wizard = self.env[
            'furniture.mrp.product.production.warning.wizard'
        ].with_user(source_supervisor).browse(action['res_id'])
        priming_stage = wizard.available_target_stage_ids.filtered(
            lambda stage: stage.code == 'priming'
        )
        self.assertTrue(priming_stage)
        wizard.write({
            'target_stage_id': priming_stage.id,
            'message': 'الخشم مش متقطع كويس',
        })
        wizard.action_send_warning()

        warning = self.env[
            'furniture.mrp.product.production.warning'
        ].search([('origin_production_line_ids', 'in', origin_line.id)])
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning.source_stage, 'carpentry')
        self.assertEqual(warning.target_stage, 'priming')
        self.assertEqual(warning.reported_by_id, source_supervisor)
        self.assertFalse(warning.reminder_consumed)
        self.assertFalse(
            warning.with_user(target_supervisor)._consume_next_for_lines(
                'priming', origin_line, batch_token='origin'
            )
        )

        next_production, _next_stages = self._create_production(route)
        next_line = self._create_lines(
            next_production,
            (1.0,),
            route,
            extra_values={'furniture_order_model_id': furniture_model.id},
        ).ensure_one()
        payload = warning.with_user(
            target_supervisor
        )._consume_next_for_lines(
            'priming', next_line, batch_token='next-order'
        )
        self.assertTrue(payload['production_quality_warning'])
        self.assertIn('الخشم مش متقطع كويس', payload['message'])
        self.assertIn(self.product.display_name, payload['message'])
        self.assertIn(furniture_model.display_name, payload['message'])
        self.assertTrue(warning.reminder_consumed)
        self.assertEqual(warning.reminder_batch_token, 'next-order')
        self.assertFalse(
            warning.with_user(target_supervisor)._consume_next_for_lines(
                'priming', next_line, batch_token='duplicate-attempt'
            )
        )

    def test_upholstery_warning_can_target_finishing_or_tailoring(self):
        upholstery_supervisor = self._create_supervisor_user(('upholstery',))
        route = ('finishing', 'tailoring', 'upholstery')
        production, _stages = self._create_production(route)
        line = self._create_lines(production, (1.0,), route).ensure_one()

        action = production.with_user(
            upholstery_supervisor
        ).action_open_stage_dashboard_order_product_warning(
            line.id,
            'upholstery',
        )
        wizard = self.env[
            'furniture.mrp.product.production.warning.wizard'
        ].with_user(upholstery_supervisor).browse(action['res_id'])

        self.assertEqual(
            set(wizard.available_target_stage_ids.mapped('code')),
            {'finishing', 'tailoring'},
        )

    def test_product_warning_rejects_supervisor_of_another_stage(self):
        production, _stages = self._create_production(('priming', 'carpentry'))
        line = self._create_lines(
            production, (1.0,), ('priming', 'carpentry')
        ).ensure_one()
        wrong_supervisor = self._create_supervisor_user(('packaging',))

        with self.assertRaises(AccessError):
            production.with_user(
                wrong_supervisor
            ).action_open_stage_dashboard_order_product_warning(
                line.id,
                'carpentry',
            )

    def test_product_warning_rejects_admin(self):
        production, _stages = self._create_production(('priming', 'carpentry'))
        line = self._create_lines(
            production, (1.0,), ('priming', 'carpentry')
        ).ensure_one()

        with self.assertRaises(AccessError):
            production.action_open_stage_dashboard_order_product_warning(
                line.id,
                'carpentry',
            )
