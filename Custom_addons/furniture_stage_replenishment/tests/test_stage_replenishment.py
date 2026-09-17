# -*- coding: utf-8 -*-

from datetime import datetime
from types import SimpleNamespace

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user


class TestFurnitureStageReplenishment(TransactionCase):

    STAGE_CODES = (
        'priming',
        'painting',
        'carpentry',
        'bases',
        'finishing',
        'tailoring',
        'upholstery',
        'packaging',
    )
    STAGE_MODELS = {
        'priming': 'furniture.mrp.priming',
        'painting': 'furniture.mrp.painting',
        'carpentry': 'furniture.mrp.carpentry',
        'bases': 'furniture.mrp.bases',
        'finishing': 'furniture.mrp.finishing',
        'tailoring': 'furniture.mrp.tailoring',
        'upholstery': 'furniture.mrp.upholstery',
        'packaging': 'furniture.mrp.packaging',
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Rule = cls.env['furniture.mrp.stage.replenishment.rule']
        cls.Production = cls.env['furniture.mrp.production']
        cls.ProductionLine = cls.env['furniture.mrp.production.line']
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')

        cls.product_a = cls._create_product('Stage Replenishment Product A')
        cls.product_b = cls._create_product('Stage Replenishment Product B')
        cls.model_a = cls.env['furniture.product.model'].create({
            'name': 'Stage Replenishment Model A',
        })
        cls.model_b = cls.env['furniture.product.model'].create({
            'name': 'Stage Replenishment Model B',
        })
        cls.grouped_model = cls.env['furniture.product.model'].create({
            'name': 'Stage Replenishment Grouped Model',
        })
        cls.grouped_sofa = cls._create_product('كنبة كبيرة')
        cls.grouped_chaise = cls._create_product('شازلونج')
        cls.grouped_armchair = cls._create_product(
            'فوتيه',
            separate_replenishment_order=True,
        )
        cls.lane_product = cls._create_product(
            'Dual Lane Replenishment Product'
        )
        cls.lane_model = cls.env['furniture.product.model'].create({
            'name': 'Dual Lane Replenishment Model',
        })

        _root_aa, cls.bom_aa = cls._create_exact_recipe(
            cls.product_a,
            cls.model_a,
            ('priming', 'carpentry'),
        )
        _root_ab, cls.bom_ab = cls._create_exact_recipe(
            cls.product_a,
            cls.model_b,
            ('priming',),
        )
        _root_ba, cls.bom_ba = cls._create_exact_recipe(
            cls.product_b,
            cls.model_a,
            ('priming',),
        )
        _root_grouped_sofa, cls.bom_grouped_sofa = cls._create_exact_recipe(
            cls.grouped_sofa,
            cls.grouped_model,
            ('priming',),
        )
        _root_grouped_chaise, cls.bom_grouped_chaise = cls._create_exact_recipe(
            cls.grouped_chaise,
            cls.grouped_model,
            ('priming',),
        )
        (
            _root_grouped_armchair,
            cls.bom_grouped_armchair,
        ) = cls._create_exact_recipe(
            cls.grouped_armchair,
            cls.grouped_model,
            ('priming',),
        )
        _lane_root, cls.lane_bom = cls._create_exact_recipe(
            cls.lane_product,
            cls.lane_model,
            (
                'priming', 'painting', 'carpentry', 'bases', 'finishing',
                'upholstery', 'tailoring', 'packaging',
            ),
        )
        cls.Rule._stage_replenishment_sync_company(cls.company)

    @classmethod
    def _create_product(
        cls,
        name,
        separate_replenishment_order=False,
    ):
        return cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
            'furniture_stage_replenishment_separate_order': (
                separate_replenishment_order
            ),
        })

    @classmethod
    def _create_exact_recipe(cls, product, model, stage_codes, company=None):
        company = company or cls.company
        selected_stages = set(stage_codes)
        values = {
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': product.uom_id.id,
            'type': 'normal',
            'company_id': company.id,
            'furniture_product_id': product.id,
            'furniture_recipe_model_id': model.id,
            'furniture_width_cm': 100.0,
            'furniture_depth_cm': 80.0,
            'furniture_height_cm': 70.0,
            'use_sewing': False,
        }
        values.update({
            'use_%s' % stage_code: stage_code in selected_stages
            for stage_code in cls.STAGE_CODES
        })
        root_bom = cls.env['mrp.bom'].with_company(company).create(values)
        recipe = cls.env['mrp.bom'].with_company(
            company
        )._find_furniture_production_recipe(
            product,
            model=model,
            company=company,
        )
        if not recipe or not recipe.furniture_is_model_recipe:
            raise AssertionError('The fixture did not create an exact model recipe.')
        return root_bom, recipe

    def setUp(self):
        super().setUp()
        self._production_sequence = 0

    def test_minmax_menu_uses_unified_dashboard_and_redirects_old_final_link(self):
        root_menu = self.env.ref(
            'furniture_stage_replenishment.'
            'menu_furniture_stage_replenishment_root'
        )
        all_stages_menu = self.env.ref(
            'furniture_stage_replenishment.'
            'menu_furniture_stage_replenishment_all'
        )
        final_menu = self.env.ref(
            'furniture_stage_replenishment.menu_furniture_final_replenishment'
        )
        time_menu = self.env.ref(
            'furniture_stage_replenishment.menu_furniture_stage_time_standard'
        )

        self.assertTrue(root_menu.active)
        self.assertFalse(root_menu.parent_id)
        self.assertEqual(root_menu.name, 'Min / Max')
        self.assertEqual(
            root_menu.web_icon,
            'furniture_stage_replenishment,static/description/icon.svg',
        )
        self.assertEqual(root_menu.action, self.env.ref(
            'furniture_stage_replenishment.action_furniture_stage_replenishment_dashboard'
        ))
        self.assertFalse(all_stages_menu.active)
        self.assertFalse(root_menu.child_id)
        self.assertFalse(final_menu.active)
        self.assertEqual(
            final_menu.action.tag,
            'furniture_stage_replenishment.stage_replenishment_dashboard',
        )
        self.assertEqual(final_menu.action.context, "{'stage_code': 'final'}")
        self.assertFalse(time_menu.active)
        self.assertEqual(time_menu.action.res_model, 'furniture.mrp.stage.time.standard')
        self.assertTrue(self.env.ref(
            'furniture_stage_replenishment.view_furniture_stage_time_standard_list'
        ).active)
        self.assertEqual(
            {all_stages_menu.parent_id, final_menu.parent_id, time_menu.parent_id},
            {root_menu},
        )

    def _rule(self, stage_code, product=None, model=None, company=None):
        product = product or self.product_a
        model = model or self.model_a
        company = company or self.company
        return self.Rule.with_context(active_test=False).search([
            ('company_id', '=', company.id),
            ('stage_code', '=', stage_code),
            ('product_id', '=', product.id),
            ('furniture_model_id', '=', model.id),
        ], limit=1)

    def _configure(self, rule, minimum, maximum):
        rule.write({'min_qty': minimum, 'max_qty': maximum})
        return rule

    def _create_production(
        self,
        product,
        model,
        bom,
        stage_codes,
        state='confirmed',
        create_stage_orders=True,
        lane='legacy',
    ):
        self._production_sequence += 1
        selected_stages = set(stage_codes)
        values = {
            'name': 'STAGE-REPLENISHMENT-%s-%s' % (
                self._testMethodName,
                self._production_sequence,
            ),
            'company_id': self.company.id,
            'product_id': product.id,
            'furniture_order_model_id': model.id,
            'product_qty': 1.0,
            'bom_id': bom.id,
            'state': state,
            'stage_plan_mode': 'custom',
            'production_lane': lane,
            'use_sewing': False,
        }
        values.update({
            'use_%s' % stage_code: stage_code in selected_stages
            for stage_code in self.STAGE_CODES
        })
        production = self.Production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).create(values)
        stage_orders = {}
        if create_stage_orders:
            for stage_code in stage_codes:
                stage = self.env[self.STAGE_MODELS[stage_code]].create({
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

    def _create_lines(self, production, product, model, bom, quantities, stages):
        selected_stages = set(stages)
        lines = self.ProductionLine
        for sequence, quantity in enumerate(quantities, start=1):
            values = {
                'production_id': production.id,
                'sequence': sequence * 10,
                'product_id': product.id,
                'furniture_order_model_id': model.id,
                'product_qty': quantity,
                'bom_id': bom.id,
                'stage_selection_initialized': True,
                'use_sewing': False,
            }
            values.update({
                'use_%s' % stage_code: stage_code in selected_stages
                for stage_code in self.STAGE_CODES
            })
            lines |= self.ProductionLine.with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_kit_plan_invalidation=True,
                furniture_skip_line_consolidation=True,
                furniture_skip_running_line_initialization=True,
                furniture_preserve_explicit_bom=True,
            ).create(values)
        return lines

    def _set_tracking(self, stage, active=None, quality=None, completed=None):
        empty = self.ProductionLine
        for field_name, lines in (
            ('active_production_line_ids_data', active or empty),
            ('quality_production_line_ids_data', quality or empty),
            ('completed_production_line_ids_data', completed or empty),
        ):
            stage.with_context(
                furniture_skip_line_consolidation=True,
            )._set_stage_line_ids_data(field_name, lines)

    def _metric(self, rule):
        return self.Rule._stage_replenishment_metric_map(rule)[rule.id]

    def _generated_grouped_model_drafts(self):
        return self.Production.search([
            ('company_id', '=', self.company.id),
            ('state', '=', 'draft'),
            ('stage_replenishment_generated', '=', True),
            ('furniture_order_model_id', '=', self.grouped_model.id),
        ])

    def _production_line_quantities(self, production):
        return {
            line.product_id.id: line.product_qty
            for line in production.production_line_ids
        }

    def _create_lane_output(
        self,
        lane,
        quantity,
        product=None,
        dimensions=None,
    ):
        product = product or self.lane_product
        dimensions = dimensions or (
            self.lane_bom.furniture_width_cm,
            self.lane_bom.furniture_depth_cm,
            self.lane_bom.furniture_height_cm,
        )
        routes = {
            'frame': ('priming', 'carpentry'),
            'painting': ('painting',),
            'finish': ('bases', 'finishing'),
            'tailoring': ('tailoring',),
            'upholstery': ('upholstery',),
            'body': ('priming', 'carpentry', 'bases', 'finishing'),
            'cover': ('upholstery', 'tailoring'),
        }
        route = routes[lane]
        production, _stages = self._create_production(
            product,
            self.lane_model,
            self.lane_bom,
            route,
            create_stage_orders=False,
            lane=lane,
        )
        line = self._create_lines(
            production,
            product,
            self.lane_model,
            self.lane_bom,
            (quantity,),
            route,
        ).ensure_one()
        dimension_values = dict(zip(
            ('width_cm', 'depth_cm', 'height_cm'),
            dimensions,
        ))
        production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).write(dimension_values)
        line.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).write(dimension_values)
        final_product = production._furniture_line_final_product(line)
        wip_product = self.env[
            'product.product'
        ]._furniture_get_or_create_lane_wip_product(
            self.company,
            lane,
            final_product,
            self.lane_model,
        )
        source_location = production._get_production_location()
        ready_stage = {
            'frame': 'carpentry',
            'painting': 'painting',
            'finish': 'finishing',
            'tailoring': 'tailoring',
            'upholstery': 'upholstery',
            'body': 'finishing',
            'cover': 'upholstery',
        }[lane]
        ready_location = production._stage_storage_location(ready_stage)
        move = production._create_internal_move(
            source_location,
            ready_location,
            'Test lane output',
            move_type='finished_product',
            product=wip_product,
            quantity=quantity,
            uom=wip_product.uom_id,
            source_production_line=line,
            price_unit=10.0,
        ).filtered(lambda candidate: candidate.state == 'done')[-1:]
        output = self.env['furniture.mrp.lane.output'].create({
            'company_id': self.company.id,
            'lane': lane,
            'production_id': production.id,
            'production_line_id': line.id,
            'final_product_id': final_product.id,
            'wip_product_id': wip_product.id,
            'furniture_model_id': self.lane_model.id,
            'uom_id': wip_product.uom_id.id,
            'source_location_id': ready_location.id,
            'origin_receipt_move_id': move.id,
            'ready_move_id': move.id,
            'qty_ready': quantity,
            'unit_cost': 10.0,
        })
        return output, production, line

    def test_sync_is_idempotent_and_preserves_limits(self):
        expected = {
            ('carpentry', self.product_a.id, self.model_a.id, self.bom_aa.id),
            ('priming', self.product_a.id, self.model_b.id, self.bom_ab.id),
            ('priming', self.product_b.id, self.model_a.id, self.bom_ba.id),
        }
        fixture_rules = self.Rule.search([
            ('company_id', '=', self.company.id),
            ('product_id', 'in', (self.product_a | self.product_b).ids),
        ])
        self.assertEqual({
            (
                rule.stage_code,
                rule.product_id.id,
                rule.furniture_model_id.id,
                rule.bom_id.id,
            )
            for rule in fixture_rules
        }, expected)

        frame_rule = self._configure(
            self._rule('carpentry'),
            minimum=3.0,
            maximum=9.0,
        )
        rule_id = frame_rule.id
        self.Rule._stage_replenishment_sync_company(self.company)
        self.Rule._stage_replenishment_sync_company(self.company)

        fixture_rules = self.Rule.search([
            ('company_id', '=', self.company.id),
            ('product_id', 'in', (self.product_a | self.product_b).ids),
        ])
        self.assertEqual(len(fixture_rules), len(expected))
        self.assertEqual(self._rule('carpentry').id, rule_id)
        self.assertEqual(self._rule('carpentry').min_qty, 3.0)
        self.assertEqual(self._rule('carpentry').max_qty, 9.0)

    def test_metrics_use_stage_tracking_and_isolate_product_and_model(self):
        rules = (
            self._rule('carpentry', self.product_a, self.model_a)
            | self._rule('priming', self.product_a, self.model_b)
            | self._rule('priming', self.product_b, self.model_a)
        )
        for rule in rules:
            self._configure(rule, 1.0, 100.0)

        production, stages = self._create_production(
            self.product_a,
            self.model_a,
            self.bom_aa,
            ('carpentry',),
        )
        lines = self._create_lines(
            production,
            self.product_a,
            self.model_a,
            self.bom_aa,
            (2.0, 3.0, 4.0),
            ('carpentry',),
        )
        self._set_tracking(
            stages['carpentry'],
            active=lines[:2],
            quality=lines[1:2],
        )

        draft, _stages = self._create_production(
            self.product_a,
            self.model_a,
            self.bom_aa,
            ('carpentry',),
            state='draft',
            create_stage_orders=False,
        )
        self._create_lines(
            draft,
            self.product_a,
            self.model_a,
            self.bom_aa,
            (6.0,),
            ('carpentry',),
        )

        other_model_order, other_model_stages = self._create_production(
            self.product_a,
            self.model_b,
            self.bom_ab,
            ('priming',),
        )
        other_model_lines = self._create_lines(
            other_model_order,
            self.product_a,
            self.model_b,
            self.bom_ab,
            (7.0,),
            ('priming',),
        )
        self._set_tracking(
            other_model_stages['priming'],
            active=other_model_lines,
        )

        other_product_order, other_product_stages = self._create_production(
            self.product_b,
            self.model_a,
            self.bom_ba,
            ('priming',),
        )
        other_product_lines = self._create_lines(
            other_product_order,
            self.product_b,
            self.model_a,
            self.bom_ba,
            (9.0,),
            ('priming',),
        )
        self._set_tracking(
            other_product_stages['priming'],
            active=other_product_lines,
        )

        metrics = self.Rule._stage_replenishment_metric_map(rules)
        exact = metrics[self._rule('carpentry').id]
        self.assertEqual(exact['current_qty'], 5.0)
        self.assertEqual(exact['incoming_qty'], 4.0)
        self.assertEqual(exact['draft_qty'], 6.0)
        self.assertEqual(exact['forecast_qty'], 15.0)
        self.assertEqual(
            metrics[self._rule(
                'priming', self.product_a, self.model_b
            ).id]['current_qty'],
            7.0,
        )
        self.assertEqual(
            metrics[self._rule(
                'priming', self.product_b, self.model_a
            ).id]['current_qty'],
            9.0,
        )

    def test_lane_threshold_triggers_at_minimum_and_nets_open_supply(self):
        rule = self._configure(
            self._rule('carpentry', self.lane_product, self.lane_model),
            5.0,
            10.0,
        )
        output, _production, _line = self._create_lane_output('frame', 6.0)

        above_minimum = self._metric(rule)
        self.assertEqual(above_minimum['current_qty'], 6.0)
        self.assertEqual(above_minimum['forecast_qty'], 6.0)
        self.assertEqual(above_minimum['qty_to_produce'], 0.0)
        self.assertEqual(above_minimum['status'], 'working')

        self.env['stock.quant']._update_available_quantity(
            output.wip_product_id,
            output.source_location_id,
            -1.0,
        )
        output._compute_physical_available_qty()
        at_minimum = self._metric(rule)
        self.assertEqual(at_minimum['current_qty'], 5.0)
        self.assertEqual(at_minimum['forecast_qty'], 5.0)
        self.assertEqual(at_minimum['qty_to_produce'], 5.0)
        self.assertEqual(at_minimum['status'], 'to_produce')

    def test_header_only_mps_orders_are_counted_without_double_counting(self):
        rule = self._configure(self._rule('carpentry'), 1.0, 100.0)
        draft, _stages = self._create_production(
            self.product_a,
            self.model_a,
            self.bom_aa,
            ('carpentry',),
            state='draft',
            create_stage_orders=False,
        )
        draft.write({'product_qty': 4.0})

        incoming, _stages = self._create_production(
            self.product_a,
            self.model_a,
            self.bom_aa,
            ('carpentry',),
            state='confirmed',
            create_stage_orders=False,
        )
        incoming.write({'product_qty': 5.0})

        working, stages = self._create_production(
            self.product_a,
            self.model_a,
            self.bom_aa,
            ('carpentry',),
            state='in_production',
            create_stage_orders=True,
        )
        working.write({'product_qty': 6.0})
        stages['carpentry'].write({'state': 'in_progress'})

        metric = self._metric(rule)
        self.assertEqual(metric['draft_qty'], 4.0)
        self.assertEqual(metric['incoming_qty'], 5.0)
        self.assertEqual(metric['current_qty'], 6.0)
        self.assertEqual(metric['forecast_qty'], 15.0)

    def test_global_netting_creates_one_complete_idempotent_draft(self):
        carpentry_rule = self._configure(self._rule('carpentry'), 5.0, 14.0)

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 1)
        production = created
        self.assertEqual(production.state, 'draft')
        self.assertTrue(production.stage_replenishment_generated)
        self.assertEqual(production.product_id, self.product_a)
        self.assertEqual(production.furniture_order_model_id, self.model_a)
        self.assertEqual(production.bom_id, self.bom_aa)
        self.assertEqual(production.product_qty, 14.0)
        self.assertEqual(
            set(production.stage_replenishment_rule_ids.ids),
            {carpentry_rule.id},
        )
        self.assertTrue(production.date_planned_start)
        self.assertTrue(production.date_planned_finish)
        self.assertGreater(
            production.date_planned_finish,
            production.date_planned_start,
        )
        self.assertEqual(len(production.production_line_ids), 1)
        line = production.production_line_ids
        self.assertEqual(line.product_id, self.product_a)
        self.assertEqual(line.furniture_order_model_id, self.model_a)
        self.assertEqual(line.bom_id, self.bom_aa)
        self.assertEqual(line.product_qty, 14.0)
        self.assertEqual(
            set(line._selected_stage_codes()),
            {'priming', 'carpentry'},
        )
        self.assertEqual(carpentry_rule.last_production_id, production)

        second_run = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )
        self.assertFalse(second_run)
        self.assertEqual(self.Production.search_count([
            ('stage_replenishment_generated', '=', True),
            ('state', '=', 'draft'),
            ('product_id', '=', self.product_a.id),
            ('furniture_order_model_id', '=', self.model_a.id),
        ]), 1)

    def test_temporary_auto_confirm_confirms_only_generated_minmax_order(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_enabled',
            'True',
        )
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_until',
            fields.Datetime.to_string(
                fields.Datetime.add(fields.Datetime.now(), days=2)
            ),
        )
        carpentry_rule = self._configure(
            self._rule('carpentry'),
            5.0,
            14.0,
        )
        manual, _stages = self._create_production(
            self.product_b,
            self.model_a,
            self.bom_ba,
            ('priming',),
            state='draft',
            create_stage_orders=False,
        )

        generated = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=carpentry_rule.ids,
            raise_on_error=True,
        ).ensure_one()

        self.assertEqual(generated.state, 'confirmed')
        self.assertTrue(generated.stage_replenishment_generated)
        self.assertEqual(manual.state, 'draft')

    def test_expired_auto_confirm_window_leaves_generated_order_draft(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_enabled',
            'True',
        )
        parameters.set_param(
            'furniture_stage_replenishment.auto_confirm_until',
            fields.Datetime.to_string(
                fields.Datetime.subtract(fields.Datetime.now(), seconds=1)
            ),
        )
        carpentry_rule = self._configure(
            self._rule('carpentry'),
            5.0,
            14.0,
        )

        generated = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=carpentry_rule.ids,
            raise_on_error=True,
        ).ensure_one()

        self.assertEqual(generated.state, 'draft')

    def test_same_model_sofa_and_chaise_share_one_draft_with_exact_quantities(self):
        sofa_rule = self._configure(
            self._rule(
                'priming',
                self.grouped_sofa,
                self.grouped_model,
            ),
            3.0,
            10.0,
        )
        chaise_rule = self._configure(
            self._rule(
                'priming',
                self.grouped_chaise,
                self.grouped_model,
            ),
            2.0,
            7.0,
        )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 1)
        production = created.ensure_one()
        self.assertEqual(production, self._generated_grouped_model_drafts())
        self.assertEqual(production.furniture_order_model_id, self.grouped_model)
        self.assertEqual(
            self._production_line_quantities(production),
            {
                self.grouped_sofa.id: 10.0,
                self.grouped_chaise.id: 7.0,
            },
        )
        lines_by_product = {
            line.product_id.id: line
            for line in production.production_line_ids
        }
        self.assertEqual(
            lines_by_product[self.grouped_sofa.id].bom_id,
            self.bom_grouped_sofa,
        )
        self.assertEqual(
            lines_by_product[self.grouped_chaise.id].bom_id,
            self.bom_grouped_chaise,
        )
        self.assertEqual(
            set(production.stage_replenishment_rule_ids.ids),
            {sofa_rule.id, chaise_rule.id},
        )
        self.assertEqual(sofa_rule.last_production_id, production)
        self.assertEqual(chaise_rule.last_production_id, production)

    def test_later_shortage_is_appended_to_existing_unconfirmed_model_order(self):
        sofa_rule = self._configure(
            self._rule(
                'priming',
                self.grouped_sofa,
                self.grouped_model,
            ),
            3.0,
            10.0,
        )
        chaise_rule = self._configure(
            self._rule(
                'priming',
                self.grouped_chaise,
                self.grouped_model,
            ),
            2.0,
            7.0,
        )
        first_run = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )
        existing = first_run.ensure_one()
        self.assertEqual(existing.state, 'draft')
        self.assertEqual(
            self._production_line_quantities(existing),
            {
                self.grouped_sofa.id: 10.0,
                self.grouped_chaise.id: 7.0,
            },
        )

        self._configure(chaise_rule, 8.0, 10.0)
        self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        grouped_drafts = self._generated_grouped_model_drafts()
        self.assertEqual(len(grouped_drafts), 1)
        self.assertEqual(grouped_drafts, existing)
        existing.invalidate_recordset([
            'production_line_ids',
            'stage_replenishment_rule_ids',
        ])
        self.assertEqual(
            self._production_line_quantities(existing),
            {
                self.grouped_sofa.id: 10.0,
                self.grouped_chaise.id: 10.0,
            },
        )
        self.assertIn(sofa_rule, existing.stage_replenishment_rule_ids)
        self.assertIn(chaise_rule, existing.stage_replenishment_rule_ids)
        self.assertEqual(chaise_rule.last_production_id, existing)

    def test_armchair_groups_with_same_model_sofa_and_chaise(self):
        configured_rules = self.Rule
        for product, minimum, maximum in (
            (self.grouped_sofa, 3.0, 10.0),
            (self.grouped_chaise, 2.0, 7.0),
            (self.grouped_armchair, 1.0, 4.0),
        ):
            configured_rules |= self._configure(
                self._rule('priming', product, self.grouped_model),
                minimum,
                maximum,
            )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 1)
        self.assertEqual(
            set(created.ids),
            set(self._generated_grouped_model_drafts().ids),
        )
        grouped_order = created.ensure_one()
        self.assertEqual(
            self._production_line_quantities(grouped_order),
            {
                self.grouped_sofa.id: 10.0,
                self.grouped_chaise.id: 7.0,
                self.grouped_armchair.id: 4.0,
            },
        )
        self.assertIn(
            self.grouped_armchair,
            grouped_order.production_line_ids.product_id,
        )
        self.assertEqual(
            set(grouped_order.stage_replenishment_rule_ids.ids),
            set(configured_rules.ids),
        )

    def test_legacy_separate_marker_is_cleared(self):
        self.assertTrue(
            self.grouped_armchair.furniture_stage_replenishment_separate_order
        )

        cleared = self.Rule._stage_replenishment_clear_legacy_separate_products()

        self.assertIn(self.grouped_armchair.product_tmpl_id, cleared)
        self.assertFalse(
            self.grouped_armchair.furniture_stage_replenishment_separate_order
        )
        self.assertFalse(
            self.grouped_sofa.furniture_stage_replenishment_separate_order
        )
        self.assertFalse(
            self.grouped_chaise.furniture_stage_replenishment_separate_order
        )

    def test_cancelled_draft_can_be_generated_again(self):
        self._configure(self._rule('carpentry'), 5.0, 14.0)
        first = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )
        self.assertEqual(len(first), 1)
        first.write({'state': 'cancelled'})

        replacement = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(replacement), 1)
        self.assertNotEqual(replacement, first)
        self.assertEqual(replacement.state, 'draft')
        self.assertEqual(replacement.product_qty, 14.0)
        self.assertEqual(self.Production.search_count([
            ('stage_replenishment_generated', '=', True),
            ('product_id', '=', self.product_a.id),
            ('furniture_order_model_id', '=', self.model_a.id),
        ]), 2)

    def test_recipe_syncs_only_canonical_minmax_controllers(self):
        rules = self.Rule.with_context(active_test=False).search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ])
        active_rules = rules.filtered('active')
        self.assertEqual(
            set(active_rules.mapped('stage_code')),
            {
                'painting', 'carpentry', 'finishing', 'tailoring',
            },
        )
        self.assertFalse(rules.filtered(lambda rule: (
            rule.active
            and rule.stage_code in {
                'priming', 'bases', 'upholstery',
            }
        )))

    def test_dashboard_has_one_combined_finish_card_and_no_upholstery(self):
        payload = self.Rule._stage_replenishment_dashboard_payload()
        stage_codes = [stage['code'] for stage in payload['stages']]

        self.assertEqual(stage_codes, [
            'carpentry', 'painting', 'finishing', 'tailoring',
        ])
        finishing_card = next(
            stage for stage in payload['stages']
            if stage['code'] == 'finishing'
        )
        self.assertEqual(finishing_card['label'], 'القواعد والتجهيز')

    def test_incomplete_physical_routes_have_no_final_rule_or_partial_order(self):
        all_stages = {
            'priming', 'painting', 'carpentry', 'bases', 'finishing',
            'tailoring', 'upholstery', 'packaging',
        }
        incomplete = {}
        for missing_stage in ('priming', 'bases', 'upholstery', 'packaging'):
            product = self._create_product(
                'Incomplete route without %s' % missing_stage
            )
            model = self.env['furniture.product.model'].create({
                'name': 'Incomplete route %s' % missing_stage,
            })
            self._create_exact_recipe(
                product,
                model,
                tuple(sorted(all_stages - {missing_stage})),
            )
            incomplete[missing_stage] = (product, model)

        self.Rule._stage_replenishment_sync_company(self.company)
        FinalRule = self.env['furniture.mrp.final.replenishment.rule']
        FinalRule._final_replenishment_sync_company(self.company)
        for product, model in incomplete.values():
            self.assertFalse(FinalRule.search([
                ('company_id', '=', self.company.id),
                ('product_id', '=', product.id),
                ('furniture_model_id', '=', model.id),
            ]))

        for missing_stage, controller in (
            ('priming', 'carpentry'),
            ('bases', 'finishing'),
        ):
            product, model = incomplete[missing_stage]
            rule = self._configure(
                self._rule(controller, product, model),
                1.0,
                3.0,
            )
            with self.assertRaises(UserError):
                self.Rule._stage_replenishment_run_company(
                    self.company,
                    restricted_rule_ids=rule.ids,
                    raise_on_error=True,
                )

    def test_bases_rule_is_archived_and_its_limits_seed_finishing(self):
        bases_rule = self.Rule._stage_replenishment_internal_create([{
            'company_id': self.company.id,
            'stage_code': 'bases',
            'product_id': self.lane_product.id,
            'furniture_model_id': self.lane_model.id,
            'bom_id': self.lane_bom.id,
            'active': True,
            'min_qty': 2.0,
            'max_qty': 9.0,
        }])

        self.Rule._stage_replenishment_sync_company(self.company)

        bases_rule.invalidate_recordset(['active', 'min_qty', 'max_qty'])
        finishing_rule = self._rule(
            'finishing', self.lane_product, self.lane_model
        )
        self.assertFalse(bases_rule.active)
        self.assertEqual(bases_rule.min_qty, 2.0)
        self.assertEqual(bases_rule.max_qty, 9.0)
        self.assertEqual(finishing_rule.min_qty, 2.0)
        self.assertEqual(finishing_rule.max_qty, 9.0)

    def test_finish_trigger_at_minimum_nets_all_coverage_to_max(self):
        rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            3.0,
            6.0,
        )
        self._create_lane_output('finish', 3.0)
        upstream, _stages = self._create_production(
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            ('bases', 'finishing'),
            create_stage_orders=False,
            lane='finish',
        )
        self._create_lines(
            upstream,
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            (2.0,),
            ('bases', 'finishing'),
        )

        metric = self._metric(rule)
        self.assertEqual(metric['current_qty'], 3.0)
        self.assertEqual(metric['incoming_qty'], 2.0)
        self.assertEqual(metric['forecast_qty'], 5.0)
        self.assertEqual(metric['qty_to_produce'], 1.0)
        self.assertEqual(metric['status'], 'to_produce')

    def test_finish_forecast_never_creates_before_frame_is_ready(self):
        rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            5.0,
            10.0,
        )
        self._create_lane_output('finish', 5.0)
        frame, _stages = self._create_production(
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            ('priming', 'carpentry'),
            create_stage_orders=False,
            lane='frame',
        )
        self._create_lines(
            frame,
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            (3.0,),
            ('priming', 'carpentry'),
        )

        metric = self._metric(rule)

        self.assertEqual(metric['current_qty'], 5.0)
        self.assertEqual(metric['incoming_qty'], 0.0)
        self.assertEqual(metric['upstream_qty'], 3.0)
        self.assertEqual(metric['forecast_qty'], 8.0)
        self.assertEqual(metric['qty_to_produce'], 2.0)

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=rule.ids,
            raise_on_error=True,
        )
        self.assertFalse(created)

        FinalRule = self.env['furniture.mrp.final.replenishment.rule']
        FinalRule._final_replenishment_sync_company(self.company)
        final_rule = FinalRule.search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]).ensure_one()
        final_rule.write({'min_qty': 5.0, 'max_qty': 10.0})
        final_metric = FinalRule._final_replenishment_metric_map(final_rule)[
            final_rule.id
        ]
        self.assertEqual(final_metric['frame']['qty_to_produce'], 2.0)

        frame.write({'state': 'cancelled'})
        frame_output, _frame_source, _frame_line = self._create_lane_output(
            'frame', 3.0,
        )
        created = frame_output._furniture_auto_create_finish_orders().ensure_one()
        self.assertEqual(
            sum(created.upstream_handoff_ids.mapped('quantity')),
            3.0,
        )
        transitioned_metric = self._metric(rule)
        self.assertEqual(transitioned_metric['upstream_qty'], 3.0)
        self.assertEqual(transitioned_metric['forecast_qty'], 11.0)
        self.assertEqual(transitioned_metric['qty_to_produce'], 0.0)
        created.invalidate_recordset(['production_line_ids'])
        self.assertEqual(created.production_line_ids.product_qty, 3.0)

        reserved_final_metric = FinalRule._final_replenishment_metric_map(
            final_rule
        )[final_rule.id]
        self.assertEqual(
            reserved_final_metric['frame']['qty_to_produce'],
            2.0,
        )
        created._furniture_consume_required_handoffs()
        consumed_final_metric = FinalRule._final_replenishment_metric_map(
            final_rule
        )[final_rule.id]
        self.assertEqual(
            consumed_final_metric['frame']['qty_to_produce'],
            2.0,
        )

    def test_finish_lane_uses_only_finishing_as_its_minmax_controller(self):
        finishing_rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            10.0,
        )
        production, _stages = self._create_production(
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            ('bases', 'finishing'),
            create_stage_orders=False,
            lane='finish',
        )
        self._create_lines(
            production,
            self.lane_product,
            self.lane_model,
            self.lane_bom,
            (2.0,),
            ('bases', 'finishing'),
        )

        metric = self._metric(finishing_rule)
        bases_rule = self._rule('bases', self.lane_product, self.lane_model)
        self.assertFalse(bases_rule and bases_rule.active)
        self.assertEqual(metric['current_qty'], 0.0)
        self.assertEqual(metric['incoming_qty'], 2.0)
        self.assertEqual(metric['forecast_qty'], 2.0)
        self.assertEqual(metric['qty_to_produce'], 8.0)

    def test_finish_shortage_without_ready_frame_creates_no_order(self):
        finishing_rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            6.0,
        )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=finishing_rule.ids,
            raise_on_error=True,
        )

        self.assertFalse(created)

    def test_upholstery_rule_is_archived_without_deleting_its_policy(self):
        rule = self.Rule._stage_replenishment_internal_create([{
            'company_id': self.company.id,
            'stage_code': 'upholstery',
            'product_id': self.lane_product.id,
            'furniture_model_id': self.lane_model.id,
            'bom_id': self.lane_bom.id,
            'active': True,
            'min_qty': 1.0,
            'max_qty': 8.0,
        }])

        self.Rule._stage_replenishment_sync_company(self.company)

        rule.invalidate_recordset(['active', 'min_qty', 'max_qty'])
        self.assertFalse(rule.active)
        self.assertEqual(rule.min_qty, 1.0)
        self.assertEqual(rule.max_qty, 8.0)

    def test_same_identity_creates_upstream_and_parallel_component_drafts(self):
        frame_rule = self._configure(
            self._rule('carpentry', self.lane_product, self.lane_model),
            1.0,
            6.0,
        )
        finish_rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            5.0,
        )
        tailoring_rule = self._configure(
            self._rule('tailoring', self.lane_product, self.lane_model),
            1.0,
            4.0,
        )
        painting_rule = self._configure(
            self._rule('painting', self.lane_product, self.lane_model),
            1.0,
            3.0,
        )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 3)
        by_lane = {production.production_lane: production for production in created}
        self.assertEqual(
            set(by_lane),
            {'frame', 'painting', 'tailoring'},
        )
        self.assertEqual(
            by_lane['frame'].production_line_ids._selected_stage_codes(),
            ['priming', 'carpentry'],
        )
        self.assertEqual(
            by_lane['tailoring'].production_line_ids._selected_stage_codes(),
            ['tailoring'],
        )
        self.assertEqual(
            by_lane['painting'].production_line_ids._selected_stage_codes(),
            ['painting'],
        )
        self.assertEqual(by_lane['frame'].production_line_ids.product_qty, 6.0)
        self.assertEqual(by_lane['tailoring'].production_line_ids.product_qty, 4.0)
        self.assertEqual(by_lane['painting'].production_line_ids.product_qty, 3.0)
        expected_rules = {
            'frame': frame_rule,
            'painting': painting_rule,
            'tailoring': tailoring_rule,
        }
        for lane, rules in expected_rules.items():
            self.assertEqual(
                set(by_lane[lane].stage_replenishment_rule_ids.ids),
                set(rules.ids),
            )

    def test_painting_shortage_is_independent_from_other_components(self):
        painting_rule = self._configure(
            self._rule('painting', self.lane_product, self.lane_model),
            2.0,
            7.0,
        )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=painting_rule.ids,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 1)
        self.assertEqual(created.production_lane, 'painting')
        self.assertEqual(
            created.production_line_ids._selected_stage_codes(),
            ['painting'],
        )
        self.assertEqual(created.production_line_ids.product_qty, 7.0)

    def test_reusable_draft_never_crosses_lane_boundary(self):
        self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            4.0,
        )
        frame_output, _frame_production, _frame_line = self._create_lane_output(
            'frame',
            4.0,
        )
        finish_draft = (
            frame_output._furniture_auto_create_finish_orders().ensure_one()
        )
        painting_rule = self._configure(
            self._rule('painting', self.lane_product, self.lane_model),
            1.0,
            3.0,
        )

        painting_draft = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=painting_rule.ids,
            raise_on_error=True,
        ).ensure_one()

        self.assertNotEqual(finish_draft, painting_draft)
        self.assertEqual(finish_draft.production_lane, 'finish')
        self.assertEqual(painting_draft.production_lane, 'painting')

    def test_later_painting_shortage_reuses_only_its_draft(self):
        painting_rule = self._configure(
            self._rule('painting', self.lane_product, self.lane_model),
            1.0,
            3.0,
        )
        original = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=painting_rule.ids,
            raise_on_error=True,
        ).ensure_one()
        self._configure(painting_rule, 4.0, 5.0)

        updated = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=painting_rule.ids,
            raise_on_error=True,
        ).ensure_one()

        self.assertEqual(updated, original)
        self.assertEqual(updated.production_lane, 'painting')
        self.assertEqual(updated.production_line_ids.product_qty, 5.0)

    def test_all_products_share_model_key_but_lanes_stay_separate(self):
        body_model_key = self.Rule._stage_replenishment_order_group_key(
            self.grouped_sofa,
            self.grouped_model,
            lane='frame',
        )
        body_armchair_key = self.Rule._stage_replenishment_order_group_key(
            self.grouped_armchair,
            self.grouped_model,
            lane='frame',
        )
        painting_model_key = self.Rule._stage_replenishment_order_group_key(
            self.grouped_sofa,
            self.grouped_model,
            lane='painting',
        )
        painting_armchair_key = self.Rule._stage_replenishment_order_group_key(
            self.grouped_armchair,
            self.grouped_model,
            lane='painting',
        )
        self.assertEqual(body_model_key, body_armchair_key)
        self.assertEqual(painting_model_key, painting_armchair_key)
        self.assertNotEqual(body_model_key, painting_model_key)
        self.assertEqual(body_armchair_key[2], 'model')
        self.assertEqual(painting_armchair_key[2], 'model')

    def test_restricted_controller_does_not_expand_to_sibling_lane(self):
        self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            4.0,
        )
        painting_rule = self._configure(
            self._rule('painting', self.lane_product, self.lane_model),
            1.0,
            3.0,
        )

        created = self.Rule._stage_replenishment_run_company(
            self.company,
            restricted_rule_ids=painting_rule.ids,
            raise_on_error=True,
        )

        self.assertEqual(len(created), 1)
        self.assertEqual(created.production_lane, 'painting')

    def test_ready_buffers_create_upholstery_then_packaging_handoffs(self):
        finish_output, _finish_production, _finish_line = (
            self._create_lane_output('finish', 2.0)
        )
        tailoring_output, _tailoring_production, _tailoring_line = (
            self._create_lane_output('tailoring', 2.0)
        )
        painting_output, _painting_production, _painting_line = (
            self._create_lane_output('painting', 2.0)
        )

        FinalRule = self.env['furniture.mrp.final.replenishment.rule']
        FinalRule._final_replenishment_sync_company(self.company)
        final_rule = FinalRule.search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]).ensure_one()
        final_rule.write({'min_qty': 3.0, 'max_qty': 8.0})
        generated = FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=final_rule.ids,
            raise_on_error=True,
        )

        upholstery = generated.filtered(
            lambda production: production.production_lane == 'upholstery'
        ).ensure_one()
        frame = generated.filtered(
            lambda production: production.production_lane == 'frame'
        ).ensure_one()
        self.assertEqual(upholstery.product_qty, 2.0)
        self.assertEqual(frame.production_line_ids.product_qty, 6.0)
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('role')),
            {'finish', 'tailoring'},
        )
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertTrue(all(
            move.state == 'assigned'
            for move in upholstery.upstream_handoff_ids.mapped(
                'transfer_move_id'
            )
        ))
        (
            finish_output | tailoring_output | painting_output
        )._compute_allocation_quantities()
        self.assertEqual(finish_output.reserved_qty, 2.0)
        self.assertEqual(tailoring_output.reserved_qty, 2.0)
        self.assertEqual(painting_output.reserved_qty, 0.0)
        self.assertFalse(self.env['furniture.mrp.final.assembly'].search([
            ('final_product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]))

        upholstery_output, _upholstery_production, _upholstery_line = (
            self._create_lane_output('upholstery', 2.0)
        )
        packaging = upholstery_output._furniture_auto_create_packaging_orders(
        ).ensure_one()

        self.assertEqual(packaging.production_lane, 'packaging')
        self.assertEqual(packaging.product_qty, 2.0)
        self.assertEqual(
            set(packaging.upstream_handoff_ids.mapped('role')),
            {'upholstery', 'painting'},
        )
        self.assertEqual(
            set(packaging.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertFalse(
            upholstery_output._furniture_auto_create_packaging_orders()
        )
        final_rule.invalidate_recordset(['replenishment_cycle_active'])
        self.assertTrue(final_rule.replenishment_cycle_active)

    def test_finished_minmax_caps_upholstery_handoff_and_does_not_repeat(self):
        outputs = self.env['furniture.mrp.lane.output']
        for lane in ('finish', 'tailoring', 'painting'):
            output, _production, _line = self._create_lane_output(lane, 10.0)
            outputs |= output

        FinalRule = self.env['furniture.mrp.final.replenishment.rule']
        FinalRule._final_replenishment_sync_company(self.company)
        final_rule = FinalRule.search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]).ensure_one()
        final_rule.write({'min_qty': 3.0, 'max_qty': 8.0})

        generated = FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=final_rule.ids,
            raise_on_error=True,
        )

        upholstery = generated.filtered(
            lambda production: production.production_lane == 'upholstery'
        ).ensure_one()
        self.assertEqual(upholstery.product_qty, 8.0)
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('role')),
            {'finish', 'tailoring'},
        )
        self.assertEqual(
            set(upholstery.upstream_handoff_ids.mapped('state')),
            {'reserved'},
        )
        self.assertEqual(
            {
                role: sum(upholstery.upstream_handoff_ids.filtered(
                    lambda handoff, role=role: handoff.role == role
                ).mapped('quantity'))
                for role in ('finish', 'tailoring')
            },
            {'finish': 8.0, 'tailoring': 8.0},
        )
        outputs._compute_allocation_quantities()
        self.assertEqual(outputs.filtered(
            lambda output: output.lane == 'finish'
        ).available_qty, 2.0)
        self.assertEqual(outputs.filtered(
            lambda output: output.lane == 'tailoring'
        ).available_qty, 2.0)
        self.assertEqual(outputs.filtered(
            lambda output: output.lane == 'painting'
        ).available_qty, 10.0)
        self.assertFalse(self.env['furniture.mrp.final.assembly'].search([
            ('final_product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]))

        second_run = FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=final_rule.ids,
            raise_on_error=True,
        )
        self.assertFalse(second_run)
        self.assertEqual(self.Production.search_count([
            ('handoff_generated', '=', True),
            ('production_lane', '=', 'upholstery'),
            ('furniture_order_model_id', '=', self.lane_model.id),
            ('final_replenishment_rule_ids', 'in', final_rule.ids),
        ]), 1)
        final_rule.invalidate_recordset(['replenishment_cycle_active'])
        self.assertTrue(final_rule.replenishment_cycle_active)
        finished = FinalRule._final_replenishment_metric_map(final_rule)[
            final_rule.id
        ]
        self.assertEqual(finished['finished_qty'], 0.0)
        self.assertEqual(finished['frame']['qty_to_produce'], 0.0)

    def test_finished_max_is_global_across_dimensioned_candidates(self):
        variants = self.env['product.product']
        for width in (110.0, 120.0):
            dimensions = (width, 80.0, 70.0)
            finish_output, _production, _line = self._create_lane_output(
                'finish',
                8.0,
                dimensions=dimensions,
            )
            variant = finish_output.final_product_id
            variants |= variant
            self._create_lane_output(
                'tailoring',
                8.0,
                product=variant,
                dimensions=dimensions,
            )
            self._create_lane_output(
                'painting',
                8.0,
                product=variant,
                dimensions=dimensions,
            )

        FinalRule = self.env['furniture.mrp.final.replenishment.rule']
        FinalRule._final_replenishment_sync_company(self.company)
        final_rule = FinalRule.search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.lane_product.id),
            ('furniture_model_id', '=', self.lane_model.id),
        ]).ensure_one()
        final_rule.write({'min_qty': 3.0, 'max_qty': 8.0})

        generated = FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=final_rule.ids,
            raise_on_error=True,
        )
        upholstery = generated.filtered(
            lambda production: production.production_lane == 'upholstery'
        )
        self.assertEqual(
            sum(upholstery.mapped('production_line_ids.product_qty')),
            8.0,
        )
        self.assertEqual(
            set(upholstery.mapped('production_line_ids.product_id').ids)
            - set(variants.ids),
            set(),
        )
        self.assertFalse(FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=final_rule.ids,
            raise_on_error=True,
        ))

    def test_lane_buffer_uses_same_strict_free_stock_as_completion(self):
        rule = self._configure(
            self._rule('finishing', self.lane_product, self.lane_model),
            1.0,
            5.0,
        )
        output, _production, _line = self._create_lane_output('finish', 3.0)
        package = self.env['stock.quant.package'].create({
            'name': 'STRICT-WIP-PACKAGE',
        })
        Quant = self.env['stock.quant'].sudo()
        empty_lot = self.env['stock.lot']
        empty_owner = self.env['res.partner']
        Quant._update_available_quantity(
            output.wip_product_id,
            output.source_location_id,
            4.0,
            lot_id=empty_lot,
            package_id=package,
            owner_id=empty_owner,
        )
        Quant._update_available_quantity(
            output.wip_product_id,
            output.source_location_id,
            -4.0,
            lot_id=empty_lot,
            package_id=self.env['stock.quant.package'],
            owner_id=empty_owner,
        )

        output._compute_physical_available_qty()
        metric = self._metric(rule)

        self.assertEqual(output.physical_available_qty, 0.0)
        self.assertEqual(metric['current_qty'], 0.0)
        self.assertEqual(metric['qty_to_produce'], 5.0)

    def test_generated_order_is_traceable_from_every_covered_stage(self):
        carpentry_rule = self._configure(
            self._rule('carpentry'),
            3.0,
            10.0,
        )

        production = self.Rule._stage_replenishment_run_company(
            self.company,
            raise_on_error=True,
        )

        self.assertEqual(len(production), 1)
        self.assertEqual(
            set(production.stage_replenishment_rule_ids.ids),
            {carpentry_rule.id},
        )
        self.assertEqual(
            production.production_line_ids._selected_stage_codes(),
            ['priming', 'carpentry'],
        )
        self.assertEqual(carpentry_rule.last_production_id, production)
        action = self.Rule.stage_replenishment_open_orders(carpentry_rule.id)
        self.assertIn(
            ('stage_replenishment_rule_ids', 'in', carpentry_rule.ids),
            action['domain'],
        )

    def test_stale_selection_never_broadens_to_run_everything(self):
        self._configure(self._rule('carpentry'), 3.0, 10.0)
        before = self.Production.search_count([
            ('stage_replenishment_generated', '=', True),
        ])

        with self.assertRaises(AccessError):
            self.Rule.stage_replenishment_run_now([999999999])

        self.assertEqual(self.Production.search_count([
            ('stage_replenishment_generated', '=', True),
        ]), before)

    def test_limit_validation_rejects_invalid_values(self):
        rule = self._rule('carpentry')
        invalid_values = (
            ('not-a-number', 10.0),
            (float('nan'), 10.0),
            (float('inf'), 10.0),
            (-1.0, 10.0),
            (8.0, 4.0),
        )
        for minimum, maximum in invalid_values:
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.Rule.stage_replenishment_update_limits(
                    rule.id,
                    minimum,
                    maximum,
                )

        self.Rule.stage_replenishment_update_limits(rule.id, 2.0, 6.0)
        self.assertEqual(rule.min_qty, 2.0)
        self.assertEqual(rule.max_qty, 6.0)

    def test_internal_generation_fields_reject_context_forgery(self):
        rule = self._rule('carpentry')
        forged_rule_model = self.Rule.sudo().with_context(
            stage_replenishment_internal=True,
        )
        with self.assertRaises(AccessError):
            forged_rule_model.create({
                'company_id': self.company.id,
                'stage_code': 'priming',
                'product_id': self.product_b.id,
                'furniture_model_id': self.model_b.id,
                'bom_id': self.bom_ba.id,
            })
        with self.assertRaises(AccessError):
            rule.sudo().with_context(
                stage_replenishment_internal=True,
            ).write({'last_production_id': False})

        production = self.Production.create({'product_qty': 1.0})
        with self.assertRaises(AccessError):
            self.Production.sudo().with_context(
                stage_replenishment_internal=True,
            ).create({
                'product_qty': 1.0,
                'stage_replenishment_generated': True,
            })
        with self.assertRaises(AccessError):
            production.sudo().with_context(
                stage_replenishment_internal=True,
            ).write({'stage_replenishment_generated': True})

    def test_manager_access_and_company_scope(self):
        regular_user = new_test_user(
            self.env,
            login='stage_replenishment_regular_%s' % self._testMethodName,
            groups='base.group_user',
        )
        manager_user = new_test_user(
            self.env,
            login='stage_replenishment_manager_%s' % self._testMethodName,
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_manager'
            ),
        )
        regular_rules = self.Rule.with_user(regular_user).with_context(
            allowed_company_ids=[self.company.id],
        )
        with self.assertRaises(AccessError):
            regular_rules.stage_replenishment_sync_rules()

        manager_rules = self.Rule.with_user(manager_user).with_context(
            allowed_company_ids=[self.company.id],
        )
        own_rule = self._rule('carpentry')
        manager_rules.stage_replenishment_update_limits(
            own_rule.id,
            2.0,
            7.0,
        )
        self.assertEqual(own_rule.min_qty, 2.0)
        self.assertEqual(own_rule.max_qty, 7.0)
        payload = manager_rules.stage_replenishment_dashboard_data()
        self.assertIn(own_rule.id, {row['id'] for row in payload['rows']})
        generated = manager_rules.stage_replenishment_run_now([own_rule.id])
        self.assertEqual(generated['created_count'], 1)
        self.assertEqual(
            self.Production.browse(generated['created_ids']).state,
            'draft',
        )

        other_company = self.env['res.company'].create({
            'name': 'Stage Replenishment Other Company',
        })
        self.env.user.write({
            'company_ids': [(4, other_company.id)],
        })
        _root, other_bom = self._create_exact_recipe(
            self.product_b,
            self.model_b,
            ('priming',),
            company=other_company,
        )
        self.Rule.with_company(
            other_company
        )._stage_replenishment_sync_company(other_company)
        other_rule = self.Rule.sudo().with_context(
            allowed_company_ids=(self.company | other_company).ids,
        ).search([
            ('company_id', '=', other_company.id),
            ('stage_code', '=', 'priming'),
            ('product_id', '=', self.product_b.id),
            ('furniture_model_id', '=', self.model_b.id),
            ('bom_id', '=', other_bom.id),
        ], limit=1)
        self.assertTrue(other_rule)

        with self.assertRaises(UserError):
            manager_rules.stage_replenishment_update_limits(
                other_rule.id,
                1.0,
                2.0,
            )
        payload = manager_rules.stage_replenishment_dashboard_data()
        self.assertNotIn(other_rule.id, {row['id'] for row in payload['rows']})
