from collections import defaultdict
from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestAdministrativeWizardAggregation(TransactionCase):
    """Keep administrative rows compact without losing physical identities."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.furniture_model = cls.env['furniture.product.model'].create({
            'name': 'Administrative Aggregation Model',
        })
        cls.sofa = cls._create_product('Administrative Sofa')
        cls.chair = cls._create_product('Administrative Chair')
        cls.raw_material_a = cls._create_product('Administrative Fabric')
        cls.raw_material_b = cls._create_product('Administrative Filling')
        cls.production = cls.env['furniture.mrp.production'].create({
            'product_qty': 8.0,
            'state': 'confirmed',
            'use_priming': True,
            'use_painting': True,
            'use_tailoring': True,
            'use_upholstery': True,
        })

        cls.lines = cls.env['furniture.mrp.production.line']
        cls.lines_by_product = {}
        for product_index, product in enumerate((cls.sofa, cls.chair)):
            root = cls._create_production_line(
                product,
                sequence=(product_index + 1) * 100,
            )
            family_lines = root
            for piece_index in range(1, 4):
                family_lines |= cls._create_production_line(
                    product,
                    sequence=((product_index + 1) * 100) + piece_index,
                    family_origin=root,
                )
            cls.lines |= family_lines
            cls.lines_by_product[product.id] = family_lines

        # A locked Kit plan intentionally preserves one technical line per
        # physical piece.  The tested wizards may aggregate their display,
        # but stage bookkeeping must never consolidate those records.
        cls.production.sudo().with_context(
            furniture_skip_kit_plan_invalidation=True,
            furniture_skip_line_consolidation=True,
        ).write({'kit_plan_locked': True})

    @classmethod
    def _create_product(cls, name):
        return cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': cls.furniture_model.id,
        })

    @classmethod
    def _create_production_line(cls, product, sequence, family_origin=False):
        return cls.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_kit_plan_invalidation=True,
            furniture_skip_line_consolidation=True,
        ).create({
            'production_id': cls.production.id,
            'sequence': sequence,
            'product_id': product.id,
            'product_qty': 1.0,
            'furniture_order_model_id': cls.furniture_model.id,
            'kit_family_origin_line_id': family_origin.id if family_origin else False,
            'use_priming': True,
            'use_painting': True,
            'use_tailoring': True,
            'use_upholstery': True,
        })

    def _payloads(self):
        return [{
            'product': line.product_id,
            'uom': line.product_uom_id,
            'qty': line.product_qty,
            'source_origin': self.production.name,
            'source_production': self.production,
            'source_production_line': line,
        } for line in self.lines.sorted(lambda line: (line.sequence, line.id))]

    def _enable_standard_physical_target(self):
        """Add a non-special physical target to the test production route."""
        context = {
            'furniture_skip_material_refresh': True,
            'furniture_skip_stage_plan_sync': True,
            'furniture_skip_kit_plan_invalidation': True,
            'furniture_skip_line_consolidation': True,
        }
        self.production.with_context(**context).write({'use_packaging': True})
        self.lines.with_context(**context).write({'use_packaging': True})

    def test_generic_quality_row_selects_the_whole_exact_family(self):
        stage = self.env['furniture.mrp.priming'].create({
            'name': 'PRI/ADMINISTRATIVE/AGGREGATION',
            'production_order_id': self.production.id,
            'state': 'in_progress',
        })
        self.production.write({'priming_order_id': stage.id})
        stage._set_stage_line_ids_data(
            'active_production_line_ids_data',
            self.lines,
        )

        action = self.production._open_stage_quality_send_wizard(
            stage,
            'priming',
            self.lines,
        )
        wizard = self.env[action['res_model']].browse(action['res_id'])

        self.assertEqual(len(wizard.line_ids), 2)
        self.assertFalse(wizard.line_ids.filtered('is_model_header'))
        for row in wizard.line_ids:
            expected_lines = self.lines_by_product[row.product_id.id]
            self.assertEqual(
                set(row.technical_production_line_ids.ids),
                set(expected_lines.ids),
            )
            self.assertAlmostEqual(row.product_qty, 4.0)

        sofa_row = wizard.line_ids.filtered(
            lambda row: row.product_id == self.sofa
        ).ensure_one()
        wizard.line_ids.write({'selected': False})
        sofa_row.write({
            'selected': True,
            'qty_to_send': 2.0,
        })
        wizard.action_send_selected_to_quality()

        self.assertEqual(
            len(stage._get_stage_line_ids_data(
                'quality_production_line_ids_data'
            )),
            2,
        )
        self.assertTrue(
            stage._get_stage_line_ids_data(
                'quality_production_line_ids_data'
            ) <= self.lines_by_product[self.sofa.id],
        )

    def test_generic_transfer_commands_keep_hidden_piece_ids(self):
        wizard = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': self.production.id,
            'source_stage': 'priming',
            'target_stage': 'painting',
        })

        commands, _summary = wizard._prepare_standard_grouped_transfer_lines(
            self._payloads()
        )
        values = [command[2] for command in commands if command[0] == 0]

        self.assertEqual(len(values), 2)
        for vals in values:
            expected_lines = self.lines_by_product[vals['product_id']]
            self.assertAlmostEqual(vals['qty_to_transfer'], 4.0)
            self.assertIn(vals['source_production_line_id'], expected_lines.ids)
            self.assertEqual(
                set(vals['technical_source_production_line_ids'][0][2]),
                set(expected_lines.ids),
            )

    def test_transfer_executes_distinct_piece_moves_in_one_batch(self):
        self._enable_standard_physical_target()
        production_model = self.env.registry['furniture.mrp.production']
        with (
            patch.object(
                production_model,
                '_source_stage_ready_for_manual_transfer',
                return_value=True,
            ),
            patch.object(
                production_model,
                '_create_internal_moves_batch',
                return_value=[],
            ) as batch_create,
        ):
            self.production._manual_transfer_stage_materials_batch(
                'priming',
                'packaging',
                [{
                    'product': payload['product'],
                    'uom': payload['uom'],
                    'qty': payload['qty'],
                    'source_production_line': payload['source_production_line'],
                } for payload in self._payloads()],
            )

        batch_create.assert_called_once()
        move_specs = batch_create.call_args.args[-1]
        self.assertEqual(len(move_specs), len(self.lines))
        self.assertEqual(
            {
                spec['source_production_line'].id
                for spec in move_specs
            },
            set(self.lines.ids),
        )
        self.assertTrue(all(
            spec['source_production_lines'] == spec['source_production_line']
            for spec in move_specs
        ))

    def test_second_partial_transfer_uses_the_unmoved_family_lines(self):
        """A reopened wizard must not allocate the remaining quant from Kit 1."""
        production_location = self.env.ref('stock.stock_location_stock')
        priming_store = self.env.ref('furniture_mrp.location_stage_priming')
        painting_work = self.env.ref('furniture_mrp.location_stage_painting_wip')
        family_lines = self.lines_by_product[self.sofa.id].sorted(
            lambda line: (line.sequence, line.id)
        )
        self.env['stock.quant']._update_available_quantity(
            self.sofa,
            production_location,
            4.0,
        )

        # Quality may receive one compact move while its M2M keeps the four
        # exact physical pieces.  This mirrors the real grouped stage receipt.
        self.production._create_internal_moves_batch([{
            'source_location': production_location,
            'dest_location': priming_store,
            'product': self.sofa,
            'quantity': 4.0,
            'uom': self.sofa.uom_id,
            'source_production_line': family_lines[:1],
            'source_production_lines': family_lines,
            'label': 'Partial family receipt regression',
        }])

        first_batch = family_lines[:2]
        self.production._create_internal_moves_batch([{
            'source_location': priming_store,
            'dest_location': painting_work,
            'product': self.sofa,
            'quantity': 1.0,
            'uom': self.sofa.uom_id,
            'source_production_line': line,
            'source_production_lines': line,
            'label': 'First partial family transfer regression',
        } for line in first_batch])

        remaining_qty = self.production._stage_location_product_qty(
            priming_store,
            self.sofa,
        )
        self.assertAlmostEqual(remaining_qty, 2.0)
        second_batch_entries = self.production._stage_location_stock_entries(
            priming_store,
            self.sofa,
            remaining_qty,
            'priming',
        )
        expected_second_batch = family_lines - first_batch
        self.assertEqual(
            {
                entry['source_production_line'].id
                for entry in second_batch_entries
            },
            set(expected_second_batch.ids),
        )
        self.assertEqual(
            sorted(entry['qty'] for entry in second_batch_entries),
            [1.0, 1.0],
        )

        self.production._create_internal_moves_batch([{
            'source_location': priming_store,
            'dest_location': painting_work,
            'product': self.sofa,
            'quantity': entry['qty'],
            'uom': entry['uom'],
            'source_production_line': entry['source_production_line'],
            'source_production_lines': entry['source_production_line'],
            'label': 'Second partial family transfer regression',
        } for entry in second_batch_entries])

        work_qty = self.production._stage_location_product_qty(
            painting_work,
            self.sofa,
        )
        self.assertAlmostEqual(work_qty, 4.0)
        work_entries = self.production._stage_location_stock_entries(
            painting_work,
            self.sofa,
            work_qty,
            'painting',
        )
        self.assertEqual(
            {
                entry['source_production_line'].id
                for entry in work_entries
            },
            set(family_lines.ids),
        )
        self.assertEqual(
            sorted(entry['qty'] for entry in work_entries),
            [1.0, 1.0, 1.0, 1.0],
        )

    def test_grouped_legacy_move_is_capped_per_physical_family_line(self):
        """Bad historical M2M metadata must not display qty 4 under Kit 1."""
        production_location = self.env.ref('stock.stock_location_stock')
        upholstery_work = self.env.ref(
            'furniture_mrp.location_stage_upholstery_wip'
        )
        family_lines = self.lines_by_product[self.sofa.id].sorted(
            lambda line: (line.sequence, line.id)
        )
        root_line = family_lines[:1]
        self.env['stock.quant']._update_available_quantity(
            self.sofa,
            production_location,
            4.0,
        )

        # Historical grouped moves sometimes stored only their representative
        # line in both source fields.  The physical quantity still belongs to
        # four one-unit family pieces and must be expanded accordingly.
        self.production._create_internal_moves_batch([{
            'source_location': production_location,
            'dest_location': upholstery_work,
            'product': self.sofa,
            'quantity': 4.0,
            'uom': self.sofa.uom_id,
            'source_production_line': root_line,
            'source_production_lines': root_line,
            'label': 'Legacy collapsed Kit move regression',
        }])

        entries = self.production._stage_location_stock_entries(
            upholstery_work,
            self.sofa,
            4.0,
            'upholstery',
        )
        self.assertEqual(
            {
                entry['source_production_line'].id
                for entry in entries
            },
            set(family_lines.ids),
        )
        self.assertEqual(
            sorted(entry['qty'] for entry in entries),
            [1.0, 1.0, 1.0, 1.0],
        )

    def test_source_less_legacy_move_uses_the_inferred_family(self):
        """A legacy move without either source field still expands by piece."""
        production_location = self.env.ref('stock.stock_location_stock')
        upholstery_work = self.env.ref(
            'furniture_mrp.location_stage_upholstery_wip'
        )
        family_lines = self.lines_by_product[self.sofa.id].sorted(
            lambda line: (line.sequence, line.id)
        )
        self.env['stock.quant']._update_available_quantity(
            self.sofa,
            production_location,
            4.0,
        )

        move_pairs = self.production._create_internal_moves_batch([{
            'source_location': production_location,
            'dest_location': upholstery_work,
            'product': self.sofa,
            'quantity': 4.0,
            'uom': self.sofa.uom_id,
            'source_production_line': family_lines[:1],
            'source_production_lines': family_lines[:1],
            'label': 'Source-less legacy Kit move regression',
        }])
        moves = self.env['stock.move']
        for _spec, move in move_pairs:
            moves |= move
        moves.write({
            'furniture_source_production_line_id': False,
            'furniture_source_production_line_ids': [(5, 0, 0)],
        })

        entries = self.production._stage_location_stock_entries(
            upholstery_work,
            self.sofa,
            4.0,
            'upholstery',
        )
        self.assertEqual(
            {
                entry['source_production_line'].id
                for entry in entries
            },
            set(family_lines.ids),
        )
        self.assertEqual(
            sorted(entry['qty'] for entry in entries),
            [1.0, 1.0, 1.0, 1.0],
        )

    def test_stage_payload_keeps_wip_when_same_product_has_raw_receipt(self):
        """A raw receipt must not hide WIP sharing its product and location."""
        stock_location = self.env.ref('stock.stock_location_stock')
        priming_work = self.env.ref(
            'furniture_mrp.location_stage_priming_wip'
        )
        source_line = self.lines_by_product[self.sofa.id][:1]
        self.env['stock.quant']._update_available_quantity(
            self.sofa,
            stock_location,
            3.0,
        )

        wip_pairs = self.production._create_internal_moves_batch([{
            'source_location': stock_location,
            'dest_location': priming_work,
            'product': self.sofa,
            'quantity': 1.0,
            'uom': self.sofa.uom_id,
            'source_production_line': source_line,
            'source_production_lines': source_line,
            'label': 'استلام تحت التشغيل - اختبار اختلاط الصنف',
        }])
        raw_pairs = self.production._create_internal_moves_batch([{
            'source_location': stock_location,
            'dest_location': priming_work,
            'product': self.sofa,
            'quantity': 2.0,
            'uom': self.sofa.uom_id,
            'source_production_line': source_line,
            'source_production_lines': source_line,
            'label': 'استلام إنتاج فعلي عند بدء التقديم - اختبار اختلاط الصنف',
        }])
        wip_move = wip_pairs[0][1]
        raw_move = raw_pairs[0][1]
        self.assertEqual(wip_move.furniture_mrp_move_type, 'wip_receipt')
        self.assertEqual(raw_move.furniture_mrp_move_type, 'raw_material')
        self.assertAlmostEqual(
            self.production._stage_location_product_qty(
                priming_work,
                self.sofa,
            ),
            3.0,
        )

        payloads = self.production._get_stage_work_location_payloads(
            'priming'
        )
        sofa_payloads = [
            payload
            for payload in payloads
            if payload['product'] == self.sofa
        ]
        self.assertEqual(
            len(sofa_payloads),
            1,
            'The raw receipt hid the whole quant, including its WIP quantity.',
        )
        self.assertAlmostEqual(sofa_payloads[0]['qty'], 1.0)

    def test_finished_carryover_excludes_only_the_same_order(self):
        """A current remainder is not old work, but another order stays visible."""
        upholstery_store = self.env.ref(
            'furniture_mrp.location_stage_upholstery'
        )
        self.env['stock.quant']._update_available_quantity(
            self.sofa,
            upholstery_store,
            1.0,
        )
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.sofa.id),
            ('location_id', '=', upholstery_store.id),
        ], limit=1)
        current_line = self.lines_by_product[self.sofa.id][:1]

        older_production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
            'state': 'confirmed',
            'use_upholstery': True,
        })
        older_line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_kit_plan_invalidation=True,
            furniture_skip_line_consolidation=True,
        ).create({
            'production_id': older_production.id,
            'sequence': 1,
            'product_id': self.sofa.id,
            'product_qty': 1.0,
            'furniture_order_model_id': self.furniture_model.id,
            'use_upholstery': True,
        })

        production_model = self.env.registry['furniture.mrp.production']
        quant_model = self.env.registry['stock.quant']

        def _entry_for(source_line):
            return [{
                'product': self.sofa,
                'qty': 1.0,
                'uom': self.sofa.uom_id,
                'source_production': source_line.production_id,
                'source_production_line': source_line,
            }]

        with (
            patch.object(quant_model, 'search', return_value=quant),
            patch.object(
                production_model,
                '_has_stage_stock_history',
                return_value=True,
            ),
            patch.object(
                production_model,
                '_source_line_matches_product_stage',
                return_value=True,
            ),
            patch.object(
                production_model,
                '_stage_location_stock_entries',
                return_value=_entry_for(current_line),
            ),
        ):
            same_order_payloads = self.production._get_legacy_finished_carryover_payloads(
                current_payloads=[],
            )
        self.assertFalse(same_order_payloads)

        with (
            patch.object(quant_model, 'search', return_value=quant),
            patch.object(
                production_model,
                '_has_stage_stock_history',
                return_value=True,
            ),
            patch.object(
                production_model,
                '_source_line_matches_product_stage',
                return_value=True,
            ),
            patch.object(
                production_model,
                '_stage_location_stock_entries',
                return_value=_entry_for(older_line),
            ),
        ):
            old_order_payloads = self.production._get_legacy_finished_carryover_payloads(
                current_payloads=[],
            )
        self.assertEqual(len(old_order_payloads), 1)
        self.assertEqual(
            old_order_payloads[0]['source_production'],
            older_production,
        )

    def test_saved_kit_layout_is_used_when_transfer_touches_special_stage(self):
        operational = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': self.production.id,
            'source_stage': 'tailoring',
            'target_stage': 'upholstery',
            'view_only': False,
        })
        ordinary_transfer = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': self.production.id,
            'source_stage': 'priming',
            'target_stage': 'painting',
            'view_only': False,
        })
        worker_viewer = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': self.production.id,
            'source_stage': 'upholstery',
            'target_stage': 'upholstery',
            'view_only': True,
        })
        unlocked_production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
            'state': 'confirmed',
            'use_priming': True,
            'use_upholstery': True,
        })
        unlocked_special_transfer = self.env[
            'furniture.mrp.stage.transfer.wizard'
        ].create({
            'production_id': unlocked_production.id,
            'source_stage': 'priming',
            'target_stage': 'upholstery',
            'view_only': False,
        })

        self.assertTrue(operational._uses_kit_grouping())
        self.assertTrue(operational.kit_grouping_active)
        self.assertFalse(ordinary_transfer._uses_kit_grouping())
        self.assertTrue(worker_viewer._uses_kit_grouping())
        self.assertFalse(unlocked_special_transfer._uses_kit_grouping())

    def test_unsaved_standard_onchange_keeps_grouped_available_quantities(self):
        self._enable_standard_physical_target()
        wizard = self.env['furniture.mrp.stage.transfer.wizard'].new({
            'production_id': self.production.id,
            'source_stage': 'priming',
            'target_stage': 'packaging',
        })
        wizard_model = self.env.registry[
            'furniture.mrp.stage.transfer.wizard'
        ]

        with patch.object(
            wizard_model,
            '_stage_transfer_source_payloads',
            autospec=True,
            return_value=self._payloads(),
        ):
            wizard._onchange_source_stage()
            rows = wizard.line_ids.filtered(lambda line: not line.is_model_header)
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(len(row.technical_source_production_line_ids), 4)
                self.assertAlmostEqual(row.available_qty, 4.0)
                self.assertAlmostEqual(row.qty_to_transfer, 4.0)

    def test_kit_planner_hides_redundant_product_group_column(self):
        planner_view = self.env.ref('furniture_mrp.view_furniture_mrp_kit_planner_form')
        self.assertNotIn('name="bucket_label"', planner_view.arch_db)

    def test_carryover_partial_allocation_distributes_material_overrides(self):
        stage = self.env['furniture.mrp.painting'].create({
            'name': 'PAI/ADMINISTRATIVE/AGGREGATION',
            'production_order_id': self.production.id,
        })
        action = self.production._open_stage_start_carryover_wizard(
            stage,
            'painting',
            self._payloads(),
        )
        wizard = self.env[action['res_model']].browse(action['res_id'])

        self.assertEqual(len(wizard.line_ids), 2)
        for row in wizard.line_ids:
            expected_lines = self.lines_by_product[row.product_id.id]
            self.assertAlmostEqual(row.qty_in_work_location, 4.0)
            self.assertAlmostEqual(row.qty_to_start, 4.0)
            self.assertEqual(
                set(row.technical_source_production_line_ids.ids),
                set(expected_lines.ids),
            )
            self.assertTrue(row.technical_allocation_json)

        sofa_row = wizard.line_ids.filtered(
            lambda row: row.product_id == self.sofa
        ).ensure_one()
        sofa_row.write({
            'qty_to_start': 2.5,
            'material_override_json': [
                {
                    'product_id': self.raw_material_a.id,
                    'product_uom_id': self.raw_material_a.uom_id.id,
                    'qty_needed': 10.0,
                },
                {
                    'product_id': self.raw_material_b.id,
                    'product_uom_id': self.raw_material_b.uom_id.id,
                    'qty_needed': 3.75,
                },
            ],
        })

        payloads = sofa_row._technical_allocation_payloads()
        self.assertEqual(
            [payload['qty_to_start'] for payload in payloads],
            [1.0, 1.0, 0.5],
        )
        self.assertEqual(len({
            payload['source_production_line_id'] for payload in payloads
        }), 3)
        self.assertTrue({
            payload['source_production_line_id'] for payload in payloads
        }.issubset(set(self.lines_by_product[self.sofa.id].ids)))

        distributed = defaultdict(list)
        for payload in payloads:
            for override in payload['material_override_json']:
                distributed[override['product_id']].append(
                    override['qty_needed']
                )
        expected_distributions = {
            self.raw_material_a.id: [4.0, 4.0, 2.0],
            self.raw_material_b.id: [1.5, 1.5, 0.75],
        }
        self.assertEqual(set(distributed), set(expected_distributions))
        for product_id, expected_values in expected_distributions.items():
            self.assertEqual(len(distributed[product_id]), len(expected_values))
            for actual, expected in zip(
                distributed[product_id],
                expected_values,
            ):
                self.assertAlmostEqual(actual, expected)

    def test_tailoring_and_upholstery_quality_stays_one_row_per_piece(self):
        for stage_code in ('tailoring', 'upholstery'):
            commands = self.production._prepare_stage_quality_send_line_commands(
                stage_code,
                self.lines,
            )
            values = [command[2] for command in commands if command[0] == 0]
            piece_values = [
                vals for vals in values if not vals.get('is_model_header')
            ]
            self.assertEqual(len(piece_values), len(self.lines))
            self.assertEqual(
                {vals['production_line_id'] for vals in piece_values},
                set(self.lines.ids),
            )
