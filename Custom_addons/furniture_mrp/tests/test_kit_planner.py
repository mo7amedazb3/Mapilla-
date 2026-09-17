from collections import Counter
from unittest.mock import patch

from lxml import etree

from odoo import fields
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

TEST_IMAGE = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
    b'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)
TEST_IMAGE_2 = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0k'
    b'AAAAFElEQVR4nGNkYPj/n4GBgYGJAQoAHRkCAjRcHicAAAAASUVORK5CYII='
)


class TestKitPlanner(TransactionCase):
    """Regression coverage for an explicit, mixed furniture-Kit plan.

    Both Kit recipes deliberately belong to the same model and share their
    components. A planner that independently applies each recipe's maximum,
    or falls back to the historical greedy name ordering, will over-allocate
    the sofa pool and fail these tests.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer = cls.env['res.partner'].create({
            'name': 'Kit Planner Buyer',
            'is_company': True,
        })
        cls.beneficiary = cls.env['res.partner'].create({
            'name': 'Kit Planner Beneficiary',
        })
        cls.supervisor_user = new_test_user(
            cls.env,
            login='furniture_kit_planner_supervisor',
            groups=(
                'base.group_user,'
                'furniture_mrp.group_furniture_mrp_supervisor'
            ),
        )

    def _stage_values(self, active_codes):
        active_codes = set(active_codes)
        return {
            field_name: stage_code in active_codes
            for stage_code, field_name in STAGE_USE_FIELDS.items()
        }

    def _create_component(self, name, model=False):
        product = self.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id if model else False,
        })
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            **self._stage_values(('tailoring', 'upholstery')),
        })
        return product, bom

    def _create_kit(self, name, model, component_quantities):
        kit_product = self.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id,
        })
        return self.env['mrp.bom'].create({
            'product_tmpl_id': kit_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': kit_product.id,
            'furniture_model_id': model.id,
            'bom_line_ids': [
                (0, 0, {
                    'sequence': sequence,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'product_qty': quantity,
                })
                for sequence, (product, quantity) in enumerate(
                    component_quantities,
                    start=1,
                )
            ],
        })

    def _create_fixture(self, with_model=True, production_state='confirmed'):
        suffix = self._testMethodName
        model = (
            self.env['furniture.product.model'].create({
                'name': 'Planner Model %s' % suffix,
            })
            if with_model else self.env['furniture.product.model']
        )
        sofa, sofa_bom = self._create_component(
            'Planner Sofa %s' % suffix,
            model=model,
        )
        chair, chair_bom = self._create_component(
            'Planner Chair %s' % suffix,
            model=model,
        )
        kit_a = kit_b = self.env['mrp.bom']
        if model:
            kit_a = self._create_kit(
                'Planner Kit A %s' % suffix,
                model,
                ((sofa, 1.0), (chair, 1.0)),
            )
            # Keep the two-sofa requirement on one BoM line. The preview must
            # keep it as one compact sofa row with quantity two.
            kit_b = self._create_kit(
                'Planner Kit B %s' % suffix,
                model,
                ((sofa, 2.0), (chair, 1.0)),
            )

        production = self.env['furniture.mrp.production'].create({
            'product_qty': 8.0,
            'date_planned_start': '2026-08-22 08:00:00',
            'date_planned_finish': '2026-08-22 17:00:00',
        })
        lines = self.env['furniture.mrp.production.line']
        for sequence, product, bom in (
            (10, sofa, sofa_bom),
            (20, chair, chair_bom),
        ):
            lines |= self.env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
            ).create({
                'production_id': production.id,
                'sequence': sequence,
                'product_id': product.id,
                'bom_id': bom.id,
                'furniture_order_model_id': model.id if model else False,
                'product_qty': 4.0,
                'buyer_partner_id': self.buyer.id,
                'beneficiary_partner_id': self.beneficiary.id,
                **self._stage_values(('tailoring', 'upholstery')),
            })
        production.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'state': production_state,
            **self._stage_values(('tailoring', 'upholstery')),
        })
        return {
            'model': model,
            'sofa': sofa,
            'chair': chair,
            'kit_a': kit_a,
            'kit_b': kit_b,
            'production': production,
            'lines': lines,
        }

    def _open_planner(self, production):
        action = production.action_open_kit_planner()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(
            action['res_model'],
            'furniture.mrp.stage.transfer.wizard',
        )
        self.assertEqual(action['target'], 'new')
        wizard = self.env[action['res_model']].with_context(
            action.get('context', {}),
        ).browse(action['res_id']).exists()
        self.assertTrue(wizard)
        self.assertTrue(wizard.kit_planner_mode)
        self.assertEqual(wizard.production_id, production)
        return wizard

    def _option(self, wizard, kit_bom):
        option = wizard.kit_plan_option_line_ids.filtered(
            lambda line: line.kit_bom_id == kit_bom
        )
        self.assertEqual(len(option), 1)
        return option

    def test_preview_labels_are_side_effect_free_when_variant_is_missing(self):
        fixture = self._create_fixture()
        production = fixture['production']
        source_line = fixture['lines'].filtered(
            lambda line: line.product_id == fixture['chair']
        ).ensure_one()
        fixture['chair'].product_tmpl_id.with_context(
            furniture_skip_bom_classification_sync=True,
        ).write({
            'furniture_model_id': False,
            'furniture_family_id': False,
        })
        fixture['chair'].invalidate_recordset([
            'furniture_model_id',
            'furniture_family_id',
        ])
        self.assertFalse(
            production._find_dimensioned_product_for_line(source_line)
        )
        product_count = self.env['product.product'].search_count([])

        wizard = self._open_planner(production)
        self.env.invalidate_all()
        preview_rows = wizard.web_read({
            'id': {},
            'line_ids': {
                'fields': {
                    'id': {},
                    'is_model_header': {},
                    'model_group_label': {},
                    'product_display_label': {},
                },
            },
        })[0]['line_ids']

        self.assertTrue(preview_rows)
        self.assertTrue(all(
            row.get('product_display_label') for row in preview_rows
        ))
        for header in (
            row for row in preview_rows if row['is_model_header']
        ):
            self.assertEqual(
                header['product_display_label'],
                header['model_group_label'],
            )
        self.assertEqual(
            self.env['product.product'].search_count([]),
            product_count,
        )

    def _production_snapshot(self, production):
        return [
            (
                line.id,
                line.active,
                line.product_id.id,
                line.product_qty,
                line.kit_bom_id.id,
                line.kit_instance_number,
            )
            for line in production.production_line_ids.with_context(
                active_test=False,
            ).sorted(lambda line: (line.sequence, line.id))
        ]

    def _preview_parts(self, wizard, header):
        return wizard.line_ids.filtered(lambda line: (
            not line.is_model_header
            and line.kit_group_token == header.kit_group_token
        ))

    def _payloads_from_lines(self, production):
        return [
            {
                'product': line.product_id,
                'qty': line.product_qty,
                'uom': line.product_uom_id,
                'label': line.product_id.display_name,
                'source_origin': production.name,
                'source_production': production,
                'source_production_line': line,
            }
            for line in production.production_line_ids.filtered(
                lambda line: line.active and line.product_id and line.product_qty > 0
            ).sorted(lambda line: (line.sequence, line.id))
        ]

    def _lines_for_product(self, lines, product):
        return lines.filtered(lambda line: (
            line.product_id.furniture_dimension_source_product_id
            or line.product_id
        ) == product)

    def _apply_mixed_plan(self, fixture):
        wizard = self._open_planner(fixture['production'])
        self._option(wizard, fixture['kit_a']).requested_kit_qty = 2
        self._option(wizard, fixture['kit_b']).requested_kit_qty = 1
        wizard.action_refresh_kit_plan_preview()
        wizard.action_apply_kit_plan()
        fixture['production'].invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        return fixture['production'].production_line_ids.filtered('active')

    def test_mixed_plan_preview_apply_and_downstream_grouping(self):
        fixture = self._create_fixture()
        production = fixture['production']
        sofa = fixture['sofa']
        chair = fixture['chair']
        kit_a = fixture['kit_a']
        kit_b = fixture['kit_b']
        original_snapshot = self._production_snapshot(production)
        move_count = self.env['stock.move'].search_count([])

        wizard = self._open_planner(production)
        option_a = self._option(wizard, kit_a)
        option_b = self._option(wizard, kit_b)
        self.assertEqual(option_a.max_kit_qty, 4)
        self.assertEqual(option_b.max_kit_qty, 2)
        option_a.requested_kit_qty = 2
        option_b.requested_kit_qty = 1

        wizard.action_refresh_kit_plan_preview()
        self.assertEqual(self._production_snapshot(production), original_snapshot)
        self.assertFalse(production.kit_plan_locked)

        headers = wizard.line_ids.filtered('is_model_header')
        real_headers = headers.filtered('kit_bom_id')
        loose_headers = headers - real_headers
        self.assertEqual(len(real_headers), 3)
        self.assertEqual(len(loose_headers), 1)
        self.assertEqual(
            Counter(header.kit_bom_id.id for header in real_headers),
            Counter({kit_a.id: 2, kit_b.id: 1}),
        )

        kit_b_header = real_headers.filtered(
            lambda header: header.kit_bom_id == kit_b
        ).ensure_one()
        kit_b_sofas = self._preview_parts(wizard, kit_b_header).filtered(
            lambda line: line.source_production_line_id.product_id == sofa
        )
        self.assertEqual(len(kit_b_sofas), 1)
        self.assertEqual(kit_b_sofas.kit_component_qty, 2.0)
        loose_parts = self._preview_parts(
            wizard,
            loose_headers.ensure_one(),
        )
        self.assertEqual(len(loose_parts), 1)
        self.assertEqual(loose_parts.source_production_line_id.product_id, chair)
        self.assertEqual(loose_parts.kit_component_qty, 1.0)

        apply_action = wizard.action_apply_kit_plan()
        self.assertEqual(apply_action['type'], 'ir.actions.client')
        self.assertEqual(apply_action['tag'], 'display_notification')
        self.assertEqual(
            apply_action['params']['next'],
            {'type': 'ir.actions.act_window_close'},
        )
        self.assertIn('الأقمشة والتكاوي', apply_action['params']['message'])
        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        self.assertTrue(production.kit_plan_locked)
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)

        active_lines = production.production_line_ids.filtered('active')
        production.invalidate_recordset([
            'kit_order_summary_line_ids',
            'kit_order_has_split_lines',
        ])
        summary_lines = production.kit_order_summary_line_ids
        summary_lines.invalidate_recordset([
            'kit_order_summary_qty',
            'kit_order_summary_material_cost',
        ])
        self.assertTrue(production.kit_order_has_split_lines)
        self.assertEqual(set(summary_lines.ids), set(fixture['lines'].ids))
        self.assertEqual(len(summary_lines), 2)
        self.assertAlmostEqual(
            summary_lines.filtered(
                lambda line: line.product_id == sofa
            ).kit_order_summary_qty,
            4.0,
        )
        self.assertAlmostEqual(
            summary_lines.filtered(
                lambda line: line.product_id == chair
            ).kit_order_summary_qty,
            4.0,
        )
        self.assertAlmostEqual(
            sum(active_lines.filtered(lambda line: line.product_id == sofa).mapped('product_qty')),
            4.0,
        )
        self.assertAlmostEqual(
            sum(active_lines.filtered(lambda line: line.product_id == chair).mapped('product_qty')),
            4.0,
        )
        assigned = active_lines.filtered('kit_bom_id')
        loose = active_lines - assigned
        self.assertEqual(len(assigned), 6)
        self.assertEqual(len(loose), 1)
        self.assertEqual(loose.product_id, chair)
        self.assertEqual(loose.product_qty, 1.0)

        self.assertEqual(
            set(assigned.filtered(lambda line: line.kit_bom_id == kit_a).mapped('kit_instance_number')),
            {1, 2},
        )
        kit_b_lines = assigned.filtered(lambda line: line.kit_bom_id == kit_b)
        self.assertEqual(
            set(kit_b_lines.mapped('kit_instance_number')),
            {1},
        )
        persisted_b_sofas = kit_b_lines.filtered(
            lambda line: line.product_id == sofa
        )
        self.assertEqual(len(persisted_b_sofas), 1)
        self.assertEqual(persisted_b_sofas.product_qty, 2.0)

        downstream = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': production.id,
            'source_stage': 'tailoring',
            'target_stage': 'upholstery',
        })
        commands, _summary = downstream._prepare_kit_grouped_transfer_lines(
            self._payloads_from_lines(production)
        )
        values = [command[2] for command in commands if command[0] == 0]
        downstream_headers = [
            vals for vals in values if vals.get('is_model_header')
        ]
        downstream_real = [
            vals for vals in downstream_headers if vals.get('kit_bom_id')
        ]
        downstream_loose = [
            vals for vals in downstream_headers if not vals.get('kit_bom_id')
        ]
        self.assertEqual(len(downstream_real), 3)
        self.assertEqual(len(downstream_loose), 1)
        self.assertEqual(
            Counter(vals['kit_bom_id'] for vals in downstream_real),
            Counter({kit_a.id: 2, kit_b.id: 1}),
        )
        loose_token = downstream_loose[0]['kit_group_token']
        downstream_loose_parts = [
            vals for vals in values
            if not vals.get('is_model_header')
            and vals.get('kit_group_token') == loose_token
        ]
        self.assertEqual(len(downstream_loose_parts), 1)
        downstream_loose_product = self.env['product.product'].browse(
            downstream_loose_parts[0]['product_id']
        )
        self.assertEqual(
            downstream._stage_transfer_product_key(downstream_loose_product),
            chair.id,
        )
        self.assertEqual(downstream_loose_parts[0]['qty_to_transfer'], 1.0)

    def test_planner_preview_is_shared_across_tailoring_and_upholstery(self):
        fixture = self._create_fixture()
        tailoring_line, upholstery_line = fixture['lines'].sorted(
            lambda line: (line.sequence, line.id)
        )
        tailoring_line.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'use_tailoring': True,
            'use_upholstery': False,
        })
        upholstery_line.with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).write({
            'use_tailoring': False,
            'use_upholstery': True,
        })

        wizard = self._open_planner(fixture['production'])
        preview_parts = wizard.line_ids.filtered(
            lambda line: not line.is_model_header
        )
        self.assertEqual(
            set(preview_parts.mapped('source_production_line_id').ids),
            set(fixture['lines'].ids),
        )
        self.assertTrue(all(preview_parts.mapped('kit_plan_stage_visible')))

    def test_piece_notes_persist_and_reach_tailoring_and_upholstery(self):
        fixture = self._create_fixture()
        production = fixture['production']
        wizard = self._open_planner(production)
        self._option(wizard, fixture['kit_b']).requested_kit_qty = 1
        wizard.action_refresh_kit_plan_preview()

        kit_header = wizard.line_ids.filtered(lambda line: (
            line.is_model_header and line.kit_bom_id == fixture['kit_b']
        )).ensure_one()
        kit_parts = self._preview_parts(wizard, kit_header).sorted(
            lambda line: (line.product_id.id, line.sequence, line.id)
        )
        notes = [
            'قص القماش بزيادة 2 سم',
            'كثافة الإسفنج حسب الصورة',
        ]
        self.assertEqual(len(kit_parts), len(notes))
        for part, note in zip(kit_parts, notes):
            part.kit_piece_note = note

        wizard.action_refresh_kit_plan_preview()
        refreshed_header = wizard.line_ids.filtered(lambda line: (
            line.is_model_header and line.kit_bom_id == fixture['kit_b']
        )).ensure_one()
        refreshed_parts = self._preview_parts(wizard, refreshed_header)
        self.assertEqual(set(refreshed_parts.mapped('kit_piece_note')), set(notes))
        wizard.action_apply_kit_plan()

        production.invalidate_recordset(['production_line_ids', 'kit_plan_locked'])
        persisted_parts = production.production_line_ids.filtered(lambda line: (
            line.active and line.kit_bom_id == fixture['kit_b']
        ))
        self.assertEqual(set(persisted_parts.mapped('kit_piece_note')), set(notes))

        for stage_code, stage_model in (
            ('tailoring', 'furniture.mrp.tailoring'),
            ('upholstery', 'furniture.mrp.upholstery'),
        ):
            stage_order = self.env[stage_model].create({
                'name': '%s/Kit Piece Notes' % stage_code.upper(),
                'production_order_id': production.id,
            })
            for note in notes:
                self.assertIn(note, stage_order.kit_piece_notes_summary)

            viewer = self.env['furniture.mrp.stage.transfer.wizard'].create({
                'production_id': production.id,
                'source_stage': stage_code,
                'target_stage': stage_code,
                'prepared_source_stage': stage_code,
                'view_only': True,
            })
            commands, _summary = viewer._prepare_kit_grouped_transfer_lines(
                self._payloads_from_lines(production)
            )
            visible_notes = {
                command[2].get('kit_piece_note')
                for command in commands
                if command[0] == 0 and not command[2].get('is_model_header')
            }
            self.assertTrue(set(notes).issubset(visible_notes))

    def test_planner_allows_prior_stage_progress_then_locks_on_special_start(self):
        fixture = self._create_fixture(production_state='in_production')
        production = fixture['production']
        fixture['lines'].with_context(
            furniture_skip_kit_plan_invalidation=True,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'first_stage_started': True,
            'first_stage_started_stage': 'finishing',
        })
        production.invalidate_recordset(['production_line_ids', 'kit_plan_editable'])
        self.assertTrue(production.kit_plan_editable)

        planner = self._open_planner(production)
        self._option(planner, fixture['kit_b']).requested_kit_qty = 1
        planner.action_refresh_kit_plan_preview()
        planner.action_apply_kit_plan()
        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
            'kit_plan_editable',
        ])
        self.assertTrue(production.kit_plan_locked)
        self.assertTrue(production.kit_plan_editable)
        self.assertTrue(all(
            line.first_stage_started
            and line.first_stage_started_stage == 'finishing'
            for line in production.production_line_ids.filtered('active')
        ))

        tailoring = self.env['furniture.mrp.tailoring'].create({
            'name': 'TAL/Planner Lock Regression',
            'production_order_id': production.id,
        })
        production.sudo().write({'tailoring_order_id': tailoring.id})
        production.invalidate_recordset([
            'tailoring_order_id',
            'tailoring_state',
            'kit_plan_editable',
        ])
        self.assertTrue(production.kit_plan_editable)
        already_open = self._open_planner(production)

        tailoring.write({
            'state': 'in_progress',
            'date_start': fields.Datetime.now(),
        })
        production.invalidate_recordset([
            'tailoring_state',
            'kit_plan_editable',
        ])
        self.assertFalse(production.kit_plan_editable)
        with self.assertRaisesRegex(UserError, 'التفصيل أو الكسوة'):
            production.action_open_kit_planner()
        with self.assertRaisesRegex(UserError, 'التفصيل أو الكسوة'):
            already_open.action_apply_kit_plan()

    def test_material_only_store_request_keeps_exact_kit_piece_ids(self):
        fixture = self._create_fixture()
        production = fixture['production']
        active_lines = self._apply_mixed_plan(fixture)

        self.assertEqual(len(active_lines), 7)
        self.assertEqual(len(active_lines.filtered('kit_family_origin_line_id')), 7)
        self.assertFalse(active_lines.filtered('cost_origin_line_id'))

        stage = self.env['furniture.mrp.tailoring'].create({
            'name': 'TAL/Grouped Store Regression',
            'production_order_id': production.id,
        })
        candidates = production._get_material_only_stage_start_line_candidates(
            'tailoring',
            stage_order=stage,
        )
        self.env['furniture.mrp.store.request']._request_direct_stage_start(stage)
        request = self.env['furniture.mrp.store.request'].search([
            ('production_id', '=', production.id),
            ('stage_code', '=', 'tailoring'),
            ('stage_order_model', '=', stage._name),
            ('stage_order_res_id', '=', stage.id),
        ], order='id desc', limit=1)

        self.assertTrue(request)
        self.assertEqual(request.request_kind, 'direct')
        self.assertEqual(
            set(request.payload_json.get('production_line_ids', [])),
            set(candidates.ids),
        )
        self.assertEqual(set(candidates.ids), set(active_lines.ids))
        self.assertEqual(len(request.requested_product_summary.splitlines()), 2)

    def test_stage_cost_batch_does_not_inherit_between_kit_pieces(self):
        fixture = self._create_fixture()
        production = fixture['production']
        active_lines = self._apply_mixed_plan(fixture)
        raw_material = self.env['product.product'].create({
            'name': 'Kit Cost Regression Material',
            'type': 'consu',
            'is_storable': True,
            'standard_price': 10.0,
        })
        self.env['furniture.mrp.material.line'].create([{
            'production_id': production.id,
            'production_line_id': line.id,
            'product_id': raw_material.id,
            'product_uom_id': raw_material.uom_id.id,
            'qty_needed': 1.0,
            'stage': 'tailoring',
            'quantity_mode': 'fixed',
        } for line in active_lines])
        stage = self.env['furniture.mrp.tailoring'].create({
            'name': 'TAL/Kit Cost Snapshot Regression',
            'production_order_id': production.id,
        })

        entries = production._record_stage_costs(
            stage,
            'tailoring',
            production_lines=active_lines,
        )

        self.assertEqual(len(entries), len(active_lines))
        self.assertEqual(set(entries.mapped('previous_material_cost')), {0.0})
        self.assertEqual(set(entries.mapped('previous_labor_cost')), {0.0})
        self.assertEqual(set(entries.mapped('stage_material_cost')), {10.0})
        self.assertEqual(set(entries.mapped('cumulative_material_cost')), {10.0})

    def test_locked_plan_can_allocate_leftovers_without_replacing_saved_identity(self):
        fixture = self._create_fixture()
        production = fixture['production']
        kit_a = fixture['kit_a']
        kit_b = fixture['kit_b']
        move_count = self.env['stock.move'].search_count([])

        first_planner = self._open_planner(production)
        self._option(first_planner, kit_a).requested_kit_qty = 1
        first_planner.action_refresh_kit_plan_preview()
        first_planner.action_apply_kit_plan()

        production.invalidate_recordset([
            'kit_plan_locked',
            'kit_plan_revision',
            'production_line_ids',
        ])
        self.assertTrue(production.kit_plan_locked)
        saved_a_lines = production.production_line_ids.filtered(lambda line: (
            line.active and line.kit_bom_id == kit_a
        ))
        self.assertEqual(len(saved_a_lines), 2)
        saved_a_identity = {
            line.id: (
                line.kit_bom_id.id,
                line.kit_instance_number,
                line.product_qty,
            )
            for line in saved_a_lines
        }

        reopened = self._open_planner(production)
        self.assertTrue(reopened.kit_plan_has_saved_groups)
        self._option(reopened, kit_b).requested_kit_qty = 1
        reopened.action_refresh_kit_plan_preview()
        preview_headers = reopened.line_ids.filtered('is_model_header')
        self.assertEqual(
            len(preview_headers.filtered(lambda line: line.kit_bom_id == kit_a)),
            1,
        )
        self.assertEqual(
            len(preview_headers.filtered(lambda line: line.kit_bom_id == kit_b)),
            1,
        )
        self.assertEqual(
            len(preview_headers.filtered(lambda line: not line.kit_bom_id)),
            1,
        )
        reopened.action_apply_kit_plan()

        production.invalidate_recordset([
            'kit_plan_locked',
            'kit_plan_revision',
            'production_line_ids',
        ])
        self.assertTrue(production.kit_plan_locked)
        persisted_a_lines = production.production_line_ids.filtered(
            lambda line: line.active and line.id in saved_a_identity
        )
        self.assertEqual(set(persisted_a_lines.ids), set(saved_a_identity))
        self.assertEqual(
            {
                line.id: (
                    line.kit_bom_id.id,
                    line.kit_instance_number,
                    line.product_qty,
                )
                for line in persisted_a_lines
            },
            saved_a_identity,
        )
        persisted_b_lines = production.production_line_ids.filtered(lambda line: (
            line.active and line.kit_bom_id == kit_b
        ))
        self.assertEqual(len(persisted_b_lines), 2)
        self.assertEqual(set(persisted_b_lines.mapped('kit_instance_number')), {1})
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)

    def test_draft_plan_survives_confirmation_without_consolidating_kit_pieces(self):
        fixture = self._create_fixture(production_state='draft')
        production = fixture['production']
        sofa = fixture['sofa']
        kit_b = fixture['kit_b']

        planner = self._open_planner(production)
        self._option(planner, kit_b).requested_kit_qty = 1
        planner.action_refresh_kit_plan_preview()
        planner.action_apply_kit_plan()
        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        self.assertEqual(production.state, 'draft')
        self.assertTrue(production.kit_plan_locked)

        planned_b_lines = production.production_line_ids.filtered(lambda line: (
            line.active and line.kit_bom_id == kit_b
        ))
        planned_b_identity = {
            line.id: (
                line.kit_bom_id.id,
                line.kit_instance_number,
                line.product_qty,
            )
            for line in planned_b_lines
        }
        self.assertEqual(len(planned_b_lines), 2)
        planned_b_sofas = self._lines_for_product(planned_b_lines, sofa)
        self.assertEqual(len(planned_b_sofas), 1)
        self.assertEqual(planned_b_sofas.product_qty, 2.0)

        production.action_confirm()
        production.invalidate_recordset([
            'state',
            'kit_plan_locked',
            'production_line_ids',
        ])
        self.assertEqual(production.state, 'confirmed')
        self.assertTrue(production.kit_plan_locked)
        confirmed_b_lines = production.production_line_ids.filtered(
            lambda line: line.active and line.id in planned_b_identity
        )
        self.assertEqual(
            {
                line.id: (
                    line.kit_bom_id.id,
                    line.kit_instance_number,
                    line.product_qty,
                )
                for line in confirmed_b_lines
            },
            planned_b_identity,
        )
        confirmed_b_sofas = self._lines_for_product(confirmed_b_lines, sofa)
        self.assertEqual(len(confirmed_b_sofas), 1)
        self.assertEqual(confirmed_b_sofas.product_qty, 2.0)

    def test_structural_quantity_edit_invalidates_plan_but_keeps_data(self):
        fixture = self._create_fixture()
        production = fixture['production']
        sofa = fixture['sofa']
        chair = fixture['chair']
        source_sofa = self._lines_for_product(fixture['lines'], sofa).ensure_one()
        source_chair = self._lines_for_product(fixture['lines'], chair).ensure_one()
        source_sofa.with_context(
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': TEST_IMAGE,
            'batch_image_token': 'planner-invalidation-sofa',
        })
        source_chair.with_context(
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': TEST_IMAGE_2,
            'batch_image_token': 'planner-invalidation-chair',
        })

        planner = self._open_planner(production)
        self._option(planner, fixture['kit_b']).requested_kit_qty = 1
        planner.action_refresh_kit_plan_preview()
        planner.action_apply_kit_plan()
        production.invalidate_recordset([
            'kit_plan_locked',
            'kit_plan_revision',
            'production_line_ids',
        ])
        self.assertTrue(production.kit_plan_locked)
        revision_before_edit = production.kit_plan_revision
        active_lines = production.production_line_ids.filtered('active')
        edited_line = self._lines_for_product(
            active_lines.filtered(lambda line: line.kit_bom_id == fixture['kit_b']),
            sofa,
        ).sorted('id')[:1]
        self.assertEqual(edited_line.product_qty, 2.0)

        edited_line.write({'product_qty': 3.0})
        production.invalidate_recordset([
            'kit_plan_locked',
            'kit_plan_revision',
            'production_line_ids',
        ])
        active_lines = production.production_line_ids.filtered('active')
        self.assertFalse(production.kit_plan_locked)
        self.assertGreater(production.kit_plan_revision, revision_before_edit)
        self.assertFalse(active_lines.filtered('kit_bom_id'))
        self.assertFalse(active_lines.filtered('kit_instance_number'))
        self.assertEqual(edited_line.product_qty, 3.0)
        self.assertAlmostEqual(
            sum(self._lines_for_product(active_lines, sofa).mapped('product_qty')),
            5.0,
        )
        self.assertAlmostEqual(
            sum(self._lines_for_product(active_lines, chair).mapped('product_qty')),
            4.0,
        )
        self.assertTrue(all(
            line.batch_image_1920 == TEST_IMAGE
            for line in self._lines_for_product(active_lines, sofa)
        ))
        self.assertTrue(all(
            line.batch_image_1920 == TEST_IMAGE_2
            for line in self._lines_for_product(active_lines, chair)
        ))

    def test_aggregate_overuse_is_rejected_without_production_mutation(self):
        fixture = self._create_fixture()
        production = fixture['production']
        wizard = self._open_planner(production)
        option_a = self._option(wizard, fixture['kit_a'])
        option_b = self._option(wizard, fixture['kit_b'])
        self.assertEqual(option_a.max_kit_qty, 4)
        self.assertEqual(option_b.max_kit_qty, 2)
        # Each count is individually within its maximum, but together they
        # require five sofas from a pool of four.
        option_a.requested_kit_qty = 3
        option_b.requested_kit_qty = 1
        snapshot = self._production_snapshot(production)
        move_count = self.env['stock.move'].search_count([])

        with self.assertRaises(UserError):
            wizard.action_refresh_kit_plan_preview()

        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        self.assertFalse(production.kit_plan_locked)
        self.assertEqual(self._production_snapshot(production), snapshot)
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)

    def test_products_without_model_remain_loose_with_visible_warning(self):
        fixture = self._create_fixture(with_model=False)
        production = fixture['production']
        wizard = self._open_planner(production)

        self.assertFalse(wizard.kit_plan_option_line_ids)
        wizard.action_refresh_kit_plan_preview()
        headers = wizard.line_ids.filtered('is_model_header')
        self.assertEqual(len(headers), 1)
        self.assertFalse(headers.kit_bom_id)
        self.assertTrue(wizard.kit_plan_warning)
        self.assertIn('موديل', wizard.kit_plan_warning)
        self.assertIn('بدون موديل', headers.model_group_label)
        self.assertEqual(len(self._preview_parts(wizard, headers)), 2)
        self.assertFalse(production.kit_plan_locked)

    def test_material_only_stages_do_not_offer_finished_product_transfer(self):
        fixture = self._create_fixture()
        production = fixture['production']
        active_lines = self._apply_mixed_plan(fixture)
        imaged_line = active_lines.filtered('kit_bom_id').sorted(
            lambda line: (line.kit_instance_number, line.sequence, line.id)
        )[:1]
        imaged_line.with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': TEST_IMAGE,
            'batch_image_token': 'operational-saved-kit-image',
        })
        payloads = self._payloads_from_lines(production)
        wizard = self.env['furniture.mrp.stage.transfer.wizard'].new({
            'production_id': production.id,
            'source_stage': 'tailoring',
            'target_stage': 'upholstery',
        })
        wizard_model = self.env.registry[
            'furniture.mrp.stage.transfer.wizard'
        ]

        with patch.object(
            wizard_model,
            '_stage_transfer_source_payloads',
            autospec=True,
            return_value=payloads,
        ):
            wizard._onchange_source_stage()
            self.assertFalse(wizard.line_ids)

    def test_warning_names_kit_component_excluded_from_special_stages(self):
        fixture = self._create_fixture()
        sofa_line = fixture['lines'].filtered(
            lambda line: line.product_id == fixture['sofa']
        ).ensure_one()
        sofa_line.with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).write({
            'use_tailoring': False,
            'use_upholstery': False,
            'use_packaging': True,
        })

        wizard = self._open_planner(fixture['production'])

        self.assertFalse(wizard.kit_plan_option_line_ids)
        self.assertTrue(wizard.kit_plan_warning)
        self.assertIn(fixture['sofa'].display_name, wizard.kit_plan_warning)
        self.assertNotIn(fixture['chair'].display_name, wizard.kit_plan_warning)
        self.assertIn('التفصيل', wizard.kit_plan_warning)
        self.assertIn('الكسوة', wizard.kit_plan_warning)

    def test_reset_unlocks_plan_without_losing_quantities_or_images(self):
        fixture = self._create_fixture()
        production = fixture['production']
        sofa = fixture['sofa']
        chair = fixture['chair']
        source_sofa = fixture['lines'].filtered(
            lambda line: line.product_id == sofa
        ).ensure_one()
        source_chair = fixture['lines'].filtered(
            lambda line: line.product_id == chair
        ).ensure_one()
        source_sofa.with_context(
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': TEST_IMAGE,
            'batch_image_token': 'planner-sofa-image',
        })
        source_chair.with_context(
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': TEST_IMAGE_2,
            'batch_image_token': 'planner-chair-image',
        })
        move_count = self.env['stock.move'].search_count([])

        wizard = self._open_planner(production)
        self._option(wizard, fixture['kit_a']).requested_kit_qty = 2
        self._option(wizard, fixture['kit_b']).requested_kit_qty = 1
        wizard.action_refresh_kit_plan_preview()
        wizard.action_apply_kit_plan()
        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        self.assertTrue(production.kit_plan_locked)

        wizard.action_reset_kit_plan()
        production.invalidate_recordset([
            'kit_plan_locked',
            'production_line_ids',
        ])
        active_lines = production.production_line_ids.filtered('active')
        self.assertFalse(production.kit_plan_locked)
        self.assertFalse(active_lines.filtered('kit_bom_id'))
        self.assertFalse(active_lines.filtered('kit_instance_number'))
        self.assertAlmostEqual(
            sum(active_lines.filtered(lambda line: line.product_id == sofa).mapped('product_qty')),
            4.0,
        )
        self.assertAlmostEqual(
            sum(active_lines.filtered(lambda line: line.product_id == chair).mapped('product_qty')),
            4.0,
        )
        self.assertTrue(all(
            line.batch_image_1920 == TEST_IMAGE
            for line in active_lines.filtered(lambda line: line.product_id == sofa)
        ))
        self.assertTrue(all(
            line.batch_image_1920 == TEST_IMAGE_2
            for line in active_lines.filtered(lambda line: line.product_id == chair)
        ))
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)

    def test_production_form_hides_kit_planner_button(self):
        view = self.env.ref('furniture_mrp.view_furniture_mrp_production_form')
        raw_document = etree.fromstring(view.arch_db.encode())
        buttons = raw_document.xpath(
            "//button[@name='action_open_kit_planner']"
        )

        self.assertFalse(buttons)
        planner_entries = raw_document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), ' o_furniture_kit_planner_entry ')]"
        )
        self.assertFalse(planner_entries)
        self.assertEqual(len(raw_document.xpath("//field[@name='kit_plan_editable']")), 1)
        material_buttons = raw_document.xpath(
            "//button[@name='action_open_tailoring_material_setup']"
        )
        self.assertEqual(len(material_buttons), 1)
        self.assertIn(
            'الأقمشة والتكاوي',
            material_buttons[0].get('string', ''),
        )
        self.assertIn(
            'o_furniture_tailoring_materials_button',
            material_buttons[0].get('class', ''),
        )

        planner_view = self.env.ref('furniture_mrp.view_furniture_mrp_kit_planner_form')
        planner_document = etree.fromstring(planner_view.arch_db.encode())
        note_fields = planner_document.xpath("//field[@name='kit_piece_note']")
        self.assertEqual(len(note_fields), 1)
        self.assertNotEqual(note_fields[0].get('readonly'), '1')
        stage_fields = planner_document.xpath("//field[@name='kit_plan_stage']")
        self.assertEqual(len(stage_fields), 1)
        self.assertEqual(stage_fields[0].get('invisible'), '1')
        self.assertFalse(planner_document.xpath(
            "//field[@name='kit_plan_stage' and @widget='radio']"
        ))

        self.assertEqual(len(planner_document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_kit_planner_scope_badge ')]"
        )), 2)
        self.assertFalse(planner_document.xpath(
            "//field[@name='target_material_summary']"
        ))
        preview_uom_fields = planner_document.xpath(
            "//field[@name='line_ids']/list/field[@name='product_uom_id']"
        )
        self.assertEqual(len(preview_uom_fields), 1)
        self.assertEqual(preview_uom_fields[0].get('column_invisible'), '1')
        self.assertEqual(preview_uom_fields[0].get('force_save'), '1')
        option_list = planner_document.xpath(
            "//field[@name='kit_plan_option_line_ids']/list"
        )
        self.assertEqual(len(option_list), 1)
        visible_option_fields = [
            field.get('name')
            for field in option_list[0].xpath(
                "./field[not(@column_invisible='1')]"
            )
        ]
        self.assertEqual(visible_option_fields, [
            'kit_display_name',
            'component_summary',
            'max_kit_qty',
            'requested_kit_qty',
        ])

        for view_xmlid in (
            'furniture_mrp.view_furniture_mrp_tailoring_form',
            'furniture_mrp.view_furniture_mrp_upholstery_form',
        ):
            stage_document = etree.fromstring(
                self.env.ref(view_xmlid).arch_db.encode()
            )
            self.assertEqual(
                len(stage_document.xpath(
                    "//field[@name='kit_piece_notes_summary']"
                )),
                1,
            )

        order_item_page = raw_document.xpath(
            "//page[contains(@string, 'أصناف أمر التشغيل')]"
        )
        self.assertEqual(len(order_item_page), 1)
        product_table_shells = order_item_page[0].xpath(
            "./div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_furniture_products_table_shell ')]"
        )
        self.assertEqual(len(product_table_shells), 1)
        technical_lists = product_table_shells[0].xpath(
            "./field[@name='production_line_ids']"
        )
        summary_lists = product_table_shells[0].xpath(
            "./field[@name='kit_order_summary_line_ids']"
        )
        self.assertEqual(len(technical_lists), 1)
        self.assertEqual(
            technical_lists[0].get('invisible'),
            'kit_order_has_split_lines',
        )
        self.assertEqual(len(summary_lists), 1)
        self.assertEqual(
            summary_lists[0].get('invisible'),
            'not kit_order_has_split_lines',
        )
        summary_sequence_fields = summary_lists[0].xpath(
            ".//field[@name='sequence']"
        )
        self.assertEqual(len(summary_sequence_fields), 1)
        self.assertEqual(
            summary_sequence_fields[0].get('column_invisible'),
            '1',
        )
        self.assertEqual(
            len(summary_lists[0].xpath(
                ".//field[@name='kit_order_summary_qty']"
            )),
            1,
        )

    def test_supervisor_cannot_change_kit_plan(self):
        production = self._create_fixture()['production']
        with self.assertRaises(AccessError):
            production.with_user(
                self.supervisor_user,
            ).action_open_kit_planner()
