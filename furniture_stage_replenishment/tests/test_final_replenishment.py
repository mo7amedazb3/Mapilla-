# -*- coding: utf-8 -*-

from datetime import datetime
from types import SimpleNamespace

from lxml import etree

from odoo import fields
from odoo.tests.common import TransactionCase
from odoo.tools.misc import file_open


class TestFurnitureFinalReplenishment(TransactionCase):

    def test_stage_timer_markup_is_native_and_has_no_qweb_inheritance(self):
        with file_open(
            'furniture_mrp/static/src/xml/mrp_stage_dashboard.xml',
            mode='rb',
        ) as template_file:
            document = etree.parse(template_file)
        batch_actions = document.xpath(
            "//t[@t-name='furniture_mrp.StageDashboardPanel']"
            "//span[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_stage_dashboard__product_batch_actions ')]"
        )
        self.assertEqual(len(batch_actions), 1)
        self.assertTrue(batch_actions[0].xpath(
            ".//span[@t-if='batch.timer']"
        ))
        manifest = self.env['ir.module.module'].get_module_info(
            'furniture_stage_replenishment'
        )
        backend_assets = manifest.get('assets', {}).get('web.assets_backend', [])
        self.assertNotIn(
            'furniture_stage_replenishment/static/src/xml/'
            'stage_timer_dashboard_patch.xml',
            backend_assets,
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.resource_calendar_id.tz = 'UTC'
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'Final MinMax Test Armchair',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
            'furniture_stage_replenishment_separate_order': True,
        })
        cls.model = cls.env['furniture.product.model'].create({
            'name': 'Final MinMax Test Model',
        })
        values = {
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'product_uom_id': cls.product.uom_id.id,
            'type': 'normal',
            'company_id': cls.company.id,
            'furniture_product_id': cls.product.id,
            'furniture_recipe_model_id': cls.model.id,
            'furniture_width_cm': 100.0,
            'furniture_depth_cm': 80.0,
            'furniture_height_cm': 70.0,
            'use_sewing': False,
        }
        values.update({
            'use_%s' % stage: stage in {
                'priming', 'painting', 'carpentry', 'bases', 'finishing',
                'upholstery', 'tailoring', 'packaging',
            }
            for stage in (
                'priming', 'painting', 'carpentry', 'bases', 'finishing',
                'tailoring', 'upholstery', 'packaging',
            )
        })
        cls.env['mrp.bom'].create(values)
        cls.bom = cls.env['mrp.bom']._find_furniture_production_recipe(
            cls.product,
            model=cls.model,
            company=cls.company,
        )
        cls.StageRule = cls.env['furniture.mrp.stage.replenishment.rule']
        cls.FinalRule = cls.env['furniture.mrp.final.replenishment.rule']
        cls.StageRule._stage_replenishment_sync_company(cls.company)
        cls.FinalRule._final_replenishment_sync_company(cls.company)
        cls.rule = cls.FinalRule.search([
            ('company_id', '=', cls.company.id),
            ('product_id', '=', cls.product.id),
            ('furniture_model_id', '=', cls.model.id),
        ], limit=1)

    def _create_lane_output(self, lane, quantity):
        routes = {
            'frame': ('priming', 'carpentry'),
            'finish': ('bases', 'finishing'),
            'tailoring': ('tailoring',),
            'painting': ('painting',),
        }
        ready_stage = {
            'frame': 'carpentry',
            'finish': 'finishing',
            'tailoring': 'tailoring',
            'painting': 'painting',
        }[lane]
        selected_stages = set(routes[lane])
        stage_values = {
            'use_%s' % stage: stage in selected_stages
            for stage in (
                'priming', 'painting', 'carpentry', 'bases', 'finishing',
                'tailoring', 'upholstery', 'packaging',
            )
        }
        dimensions = {
            'width_cm': self.bom.furniture_width_cm,
            'depth_cm': self.bom.furniture_depth_cm,
            'height_cm': self.bom.furniture_height_cm,
        }
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_line_consolidation=True,
        ).create({
            'name': 'FINAL-SERIAL/%s/%s' % (self._testMethodName, lane),
            'company_id': self.company.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model.id,
            'product_qty': quantity,
            'bom_id': self.bom.id,
            'production_lane': lane,
            'stage_plan_mode': 'custom',
            'state': 'confirmed',
            **dimensions,
            **stage_values,
        })
        line = self.env['furniture.mrp.production.line'].with_context(
            furniture_preserve_explicit_bom=True,
            furniture_skip_line_consolidation=True,
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_running_line_initialization=True,
        ).create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model.id,
            'product_qty': quantity,
            'bom_id': self.bom.id,
            'stage_selection_initialized': True,
            **dimensions,
            **stage_values,
        })
        final_product = production._furniture_line_final_product(line)
        wip_product = self.env[
            'product.product'
        ]._furniture_get_or_create_lane_wip_product(
            self.company,
            lane,
            final_product,
            self.model,
        )
        production._create_internal_move(
            production._get_production_location(),
            production._stage_storage_location(ready_stage),
            'Final replenishment serial coverage fixture',
            move_type='finished_product',
            product=wip_product,
            quantity=quantity,
            uom=wip_product.uom_id,
            source_production_line=line,
            price_unit=10.0,
        )
        return production._ensure_lane_outputs(line).ensure_one()

    def test_finished_demand_waits_for_real_frame_before_finish_order(self):
        self.assertTrue(self.rule)
        self.rule.write({'min_qty': 3.0, 'max_qty': 9.0})
        finished = self.env.ref('furniture_mrp.location_finished_goods')
        self.env['stock.quant']._update_available_quantity(
            self.product, finished, 3.0,
        )

        metrics = self.FinalRule._final_replenishment_metric_map(self.rule)[self.rule.id]
        self.assertEqual(metrics['finished_qty'], 3.0)
        self.assertEqual(metrics['frame']['qty_to_produce'], 6.0)
        self.assertEqual(metrics['finish']['qty_to_produce'], 6.0)
        self.assertEqual(metrics['tailoring']['qty_to_produce'], 6.0)
        self.assertEqual(metrics['painting']['qty_to_produce'], 6.0)
        self.assertEqual({
            lane: metrics[lane]['rule'].stage_code
            for lane in ('frame', 'finish', 'tailoring', 'painting')
        }, {
            'frame': 'carpentry',
            'finish': 'finishing',
            'tailoring': 'tailoring',
            'painting': 'painting',
        })

        dashboard = self.FinalRule._final_replenishment_dashboard_payload()
        row = next(
            item for item in dashboard['rows'] if item['id'] == self.rule.id
        )
        self.assertEqual(
            [component['lane'] for component in row['components']],
            ['frame', 'finish', 'tailoring', 'painting'],
        )
        self.assertEqual(
            {
                component['lane']
                for component in row['components']
                if component['owned_by_final_minmax']
            },
            {'frame', 'finish', 'tailoring', 'painting'},
        )

        productions = self.FinalRule._final_replenishment_run_company(
            self.company, restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )
        self.assertEqual(
            set(productions.mapped('production_lane')),
            {'frame', 'tailoring', 'painting'},
        )
        self.assertEqual(set(productions.mapped('product_qty')), {6.0})
        self.assertEqual(
            productions.mapped('final_replenishment_rule_ids'),
            self.rule,
        )
        self.assertEqual(
            set(productions.stage_replenishment_rule_ids.mapped('stage_code')),
            {'carpentry', 'tailoring', 'painting'},
        )
        self.assertFalse(self.env['furniture.mrp.production'].search([
            ('production_lane', '=', 'finish'),
            ('final_replenishment_rule_ids', 'in', self.rule.ids),
            ('state', 'not in', ('done', 'cancelled')),
        ]))
        self.assertEqual(
            self.FinalRule._final_replenishment_run_company(
                self.company, restricted_rule_ids=self.rule.ids,
                raise_on_error=True,
            ),
            self.env['furniture.mrp.production'],
        )

    def test_temporary_auto_confirm_also_confirms_upholstery_order(self):
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
        for lane in ('finish', 'tailoring'):
            self._create_lane_output(lane, 3.0)
        self.rule.write({'min_qty': 0.0, 'max_qty': 3.0})

        generated = self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )

        upholstery = generated.filtered(
            lambda production: production.production_lane == 'upholstery'
        ).ensure_one()
        self.assertEqual(upholstery.state, 'confirmed')
        self.assertTrue(upholstery.stage_replenishment_generated)

    def test_final_override_and_stage_minmax_share_existing_coverage(self):
        self.rule.write({'min_qty': 2.0, 'max_qty': 5.0})
        controllers = self.StageRule.search([
            ('company_id', '=', self.company.id),
            ('product_id', '=', self.product.id),
            ('furniture_model_id', '=', self.model.id),
            ('stage_code', 'in', (
                'painting', 'carpentry', 'finishing', 'tailoring',
            )),
        ])
        self.assertEqual(
            set(controllers.mapped('stage_code')),
            {'painting', 'carpentry', 'finishing', 'tailoring'},
        )
        controllers.write({'min_qty': 2.0, 'max_qty': 5.0})
        metrics = self.FinalRule._final_replenishment_metric_map(self.rule)[
            self.rule.id
        ]
        self.assertEqual(metrics['frame']['qty_to_produce'], 5.0)
        self.assertEqual(
            {
                lane: metrics[lane]['qty_to_produce']
                for lane in ('finish', 'tailoring', 'painting')
            },
            {'finish': 5.0, 'tailoring': 5.0, 'painting': 5.0},
        )
        productions = self.StageRule._stage_replenishment_run_company(
            self.company, restricted_rule_ids=controllers.ids,
            raise_on_error=True,
        )
        self.assertEqual(
            set(productions.mapped('production_lane')),
            {'painting', 'tailoring'},
        )
        self.assertFalse(productions.filtered(
            lambda production: production.production_lane == 'frame'
        ))
        routes = {
            production.production_lane: (
                production.production_line_ids._selected_stage_codes()
            )
            for production in productions
        }
        self.assertEqual(routes['painting'], ['painting'])
        self.assertEqual(routes['tailoring'], ['tailoring'])

        final_productions = self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )
        self.assertEqual(
            set(final_productions.mapped('production_lane')),
            {'frame'},
        )

    def test_open_upholstery_still_generates_missing_paint_for_packaging(self):
        self.rule.write({'min_qty': 0.0, 'max_qty': 6.0})
        outputs = self.env['furniture.mrp.lane.output']
        for lane in ('finish', 'tailoring'):
            outputs |= self._create_lane_output(lane, 6.0)
        outputs._furniture_refresh_matching_groups()
        self.assertEqual(
            {
                output.lane: output.physical_available_qty
                for output in outputs
            },
            {'finish': 6.0, 'tailoring': 6.0},
        )
        upholstery = outputs._furniture_auto_create_handoff_orders(
            'upholstery',
            ('finish', 'tailoring'),
            company=self.company,
            final_product=outputs[0].final_product_id,
            furniture_model=self.model,
            bom=self.bom,
            maximum_quantity=6.0,
        ).ensure_one()

        metrics = self.FinalRule._final_replenishment_metric_map(self.rule)[
            self.rule.id
        ]
        self.assertTrue(metrics['configured'])
        self.assertTrue(metrics['triggered'])
        self.assertEqual(metrics['finished_qty'], 0.0)
        self.assertEqual(metrics['open_flow_qty'], 6.0)
        self.assertEqual(metrics['open_packaging_qty'], 0.0)
        self.assertEqual(metrics['component_demand_qty'], 0.0)
        self.assertEqual(metrics['painting_demand_qty'], 6.0)
        self.assertEqual(
            {
                lane: metrics[lane]['qty_to_produce']
                for lane in ('frame', 'finish', 'tailoring', 'painting')
            },
            {'frame': 0.0, 'finish': 0.0, 'tailoring': 0.0, 'painting': 6.0},
        )
        generated = self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )
        self.assertEqual(generated.production_lane, 'painting')
        self.assertEqual(generated.production_line_ids.product_qty, 6.0)
        self.assertFalse(self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        ))
        self.assertEqual(upholstery.production_lane, 'upholstery')

    def test_zero_min_without_coverage_generates_exact_max(self):
        self.rule.write({'min_qty': 0.0, 'max_qty': 6.0})

        productions = self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )

        self.assertEqual(
            set(productions.mapped('production_lane')),
            {'frame', 'tailoring', 'painting'},
        )
        self.assertEqual(
            set(productions.mapped('production_line_ids.product_qty')),
            {6.0},
        )

    def test_serial_coverage_prevents_duplicate_frame_after_handoff(self):
        self.rule.write({'min_qty': 3.0, 'max_qty': 9.0})
        frame_output = self._create_lane_output('frame', 9.0)
        finish = self.env[
            'furniture.mrp.production'
        ]._furniture_create_handoff_lane_order(
            'finish',
            {'frame': frame_output},
            9.0,
        ).ensure_one()
        self.assertTrue(finish.stage_replenishment_generated)
        self.assertTrue(finish.stage_replenishment_created_at)

        frame_output._compute_allocation_quantities()
        self.assertEqual(frame_output.reserved_qty, 9.0)
        self.assertEqual(frame_output.available_qty, 0.0)
        reserved_metrics = self.FinalRule._final_replenishment_metric_map(
            self.rule
        )[self.rule.id]
        self.assertEqual(reserved_metrics['finish']['draft_qty'], 9.0)
        self.assertEqual(reserved_metrics['frame']['coverage_qty'], 9.0)
        self.assertEqual(reserved_metrics['frame']['qty_to_produce'], 0.0)

        self.assertIs(finish._furniture_consume_required_handoffs(), True)
        self.assertEqual(
            set(finish.upstream_handoff_ids.mapped('state')),
            {'consumed'},
        )
        consumed_metrics = self.FinalRule._final_replenishment_metric_map(
            self.rule
        )[self.rule.id]
        self.assertEqual(consumed_metrics['finish']['draft_qty'], 9.0)
        self.assertEqual(consumed_metrics['frame']['coverage_qty'], 9.0)
        self.assertEqual(consumed_metrics['frame']['qty_to_produce'], 0.0)

        generated = self.FinalRule._final_replenishment_run_company(
            self.company,
            restricted_rule_ids=self.rule.ids,
            raise_on_error=True,
        )
        self.assertFalse(generated.filtered(
            lambda production: production.production_lane == 'frame'
        ))
        self.assertFalse(self.env['furniture.mrp.production'].search([
            ('final_replenishment_rule_ids', 'in', self.rule.ids),
            ('production_lane', '=', 'frame'),
        ]))

    def test_two_hour_standards_and_six_day_clock(self):
        Standard = self.env['furniture.mrp.stage.time.standard']
        rows = Standard._stage_time_sync_company(self.company)
        self.assertEqual(len(rows), 8)
        self.assertEqual(set(rows.mapped('hours_per_piece')), {2.0})

        # Thu 10h + Friday 0h + Sat 10h.
        self.assertEqual(Standard._stage_timer_work_hours(
            self.company,
            datetime(2026, 8, 27, 8, 0, 0),
            datetime(2026, 8, 29, 18, 0, 0),
        ), 20.0)

        timer_record = SimpleNamespace(
            company_id=self.company,
            stage_timer_started_at=datetime(2026, 8, 27, 8, 0, 0),
            stage_timer_finished_at=datetime(2026, 8, 27, 13, 0, 0),
            stage_timer_paused_at=False,
            stage_timer_paused_work_hours=0.0,
            stage_timer_planned_qty=2.0,
            stage_timer_piece_hours=2.0,
            stage_timer_planned_hours=4.0,
        )
        payload = Standard._stage_timer_payload(timer_record, 'done')
        self.assertEqual(payload['remaining_seconds'], -3600)
        self.assertTrue(payload['overdue'])

    def test_temporary_order_uses_two_hours_for_whole_stage_and_batch(self):
        stage_values = {
            'use_priming': True,
            'use_painting': False,
            'use_carpentry': False,
            'use_bases': False,
            'use_finishing': False,
            'use_tailoring': False,
            'use_upholstery': False,
            'use_packaging': False,
        }
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'name': 'MRP/TEMPORARY-TWO-HOURS',
            'company_id': self.company.id,
            'furniture_order_model_id': self.model.id,
            'product_qty': 5.0,
            'temporary_stage_fixed_hours': 2.0,
            **stage_values,
        })
        line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': self.product.id,
            'product_qty': 5.0,
            'bom_id': self.bom.id,
            'furniture_order_model_id': self.model.id,
            'stage_selection_initialized': True,
            **stage_values,
        })
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-TEMPORARY-TWO-HOURS',
        )
        stage_values = stage_order._stage_timer_start_values(
            planned_qty=5.0,
            started_at=datetime(2026, 8, 27, 8, 0, 0),
        )
        self.assertEqual(stage_values['stage_timer_planned_hours'], 2.0)
        self.assertEqual(stage_values['stage_timer_piece_hours'], 0.4)
        stage_order.sudo().write(stage_values)
        stage_payload = stage_order._stage_timer_dashboard_payload()
        self.assertEqual(stage_payload['planned_hours'], 2.0)
        self.assertEqual(stage_payload['planned_qty'], 5.0)

        # Temporary test orders count real elapsed time even on Friday and
        # outside the factory shift.
        stage_order.sudo().write({
            'stage_timer_started_at': datetime(2026, 8, 28, 18, 0, 0),
            'stage_timer_finished_at': datetime(2026, 8, 28, 19, 0, 0),
        })
        stage_payload = self.env[
            'furniture.mrp.stage.time.standard'
        ]._stage_timer_payload(stage_order, 'done')
        self.assertTrue(stage_payload['continuous'])
        self.assertEqual(stage_payload['elapsed_work_hours'], 1.0)
        self.assertEqual(stage_payload['remaining_seconds'], 3600)

        Batch = self.env['furniture.mrp.stage.product.batch']
        identity = Batch._identity_values_from_line(line)
        batch = Batch.create({
            'token': 'temporary-two-hours-batch',
            'company_id': self.company.id,
            'stage_code': 'priming',
            'identity_key': identity['identity_key'],
            'product_id': self.product.id,
            'model_id': identity['model'].id or False,
            'bom_id': identity['bom'].id or False,
            'dimension_label': identity['dimension_label'],
            'uom_id': identity['uom'].id,
            'production_line_ids': [(6, 0, line.ids)],
            'planned_qty': 5.0,
            'state': 'in_progress',
            'member_ids': [(0, 0, {
                'production_line_id': line.id,
                'qty_snapshot': 5.0,
                'identity_key': identity['identity_key'],
            })],
        })
        batch._stage_timer_initialize_if_needed(
            started_at=datetime(2026, 8, 27, 8, 0, 0),
        )
        self.assertEqual(batch.stage_timer_planned_hours, 2.0)
        self.assertEqual(batch.stage_timer_piece_hours, 0.4)
        batch.sudo().write({
            'stage_timer_started_at': datetime(2026, 8, 28, 18, 0, 0),
            'stage_timer_finished_at': datetime(2026, 8, 28, 19, 0, 0),
        })
        batch_payload = self.env[
            'furniture.mrp.stage.time.standard'
        ]._stage_timer_payload(batch, 'done')
        self.assertTrue(batch_payload['continuous'])
        self.assertEqual(batch_payload['elapsed_work_hours'], 1.0)
        self.assertEqual(batch_payload['remaining_seconds'], 3600)
