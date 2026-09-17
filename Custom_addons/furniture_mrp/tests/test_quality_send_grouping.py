from lxml import etree

from odoo.tests.common import TransactionCase


class TestQualitySendGrouping(TransactionCase):

    def _create_product(self, name, model, stages):
        product = self.env['product.product'].create({
            'name': '%s %s' % (name, self._testMethodName),
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id if model else False,
        })
        return product

    def _create_kit(self, name, model, components):
        product = self._create_product(name, model, ())
        return self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': product.id,
            'furniture_model_id': model.id,
            'bom_line_ids': [(0, 0, {
                'sequence': sequence,
                'product_id': component.id,
                'product_uom_id': component.uom_id.id,
                'product_qty': qty,
            }) for sequence, (component, qty) in enumerate(components, start=1)],
        })

    def _create_grouped_fixture(self, stage_code='upholstery'):
        model = self.env['furniture.product.model'].create({
            'name': 'Quality Group Model %s' % self._testMethodName,
        })
        sofa = self._create_product('Quality Sofa', model, (stage_code,))
        chair = self._create_product('Quality Chair', model, (stage_code,))
        kit_a = self._create_kit('Quality Kit A', model, (
            (sofa, 1.0),
            (chair, 1.0),
        ))
        kit_b = self._create_kit('Quality Kit B', model, (
            (sofa, 2.0),
            (chair, 1.0),
        ))
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 6.0,
            'state': 'confirmed',
            'use_upholstery': stage_code == 'upholstery',
            'use_tailoring': stage_code == 'tailoring',
        })
        lines = self.env['furniture.mrp.production.line']
        specifications = (
            (10, sofa, kit_a, 1),
            (20, chair, kit_a, 1),
            (30, sofa, kit_b, 1),
            (31, sofa, kit_b, 1),
            (40, chair, kit_b, 1),
            (50, chair, False, 0),
        )
        for sequence, product, kit_bom, instance_number in specifications:
            lines |= self.env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_kit_plan_invalidation=True,
            ).create({
                'production_id': production.id,
                'sequence': sequence,
                'product_id': product.id,
                'product_qty': 1.0,
                'furniture_order_model_id': model.id,
                'kit_bom_id': kit_bom.id if kit_bom else False,
                'kit_instance_number': instance_number,
                'use_upholstery': stage_code == 'upholstery',
                'use_tailoring': stage_code == 'tailoring',
            })
        production.sudo().with_context(
            furniture_skip_kit_plan_invalidation=True,
        ).write({'kit_plan_locked': True})
        stage_model = 'furniture.mrp.%s' % stage_code
        stage = self.env[stage_model].create({
            'name': '%s/QUALITY/GROUP/%s' % (
                'UPH' if stage_code == 'upholstery' else 'TAL',
                self._testMethodName,
            ),
            'production_order_id': production.id,
            'state': 'in_progress',
        })
        production.write({
            '%s_order_id' % stage_code: stage.id,
        })
        stage._set_stage_line_ids_data('active_production_line_ids_data', lines)
        return {
            'production': production,
            'stage': stage,
            'lines': lines,
            'sofa': sofa,
            'chair': chair,
            'kit_a': kit_a,
            'kit_b': kit_b,
        }

    def _open_quality_wizard(self, stage):
        action = stage.action_send_to_quality()
        wizard = self.env[action['res_model']].with_context(
            action.get('context', {}),
        ).browse(action['res_id']).exists()
        self.assertTrue(wizard)
        self.assertEqual(action['target'], 'new')
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_quality_send_wizard_form'
        )
        self.assertEqual(action.get('view_id'), view.id)
        self.assertEqual(action.get('views'), [(view.id, 'form')])
        return wizard

    def test_special_quality_wizard_groups_only_persisted_kit_identity(self):
        fixture = self._create_grouped_fixture()
        production = fixture['production']
        stage = fixture['stage']
        lines = fixture['lines']
        snapshot = [
            (
                line.id,
                line.active,
                line.product_id.id,
                line.product_qty,
                line.kit_bom_id.id,
                line.kit_instance_number,
            )
            for line in lines.sorted('id')
        ]
        move_count = self.env['stock.move'].search_count([])

        wizard = self._open_quality_wizard(stage)
        headers = wizard.line_ids.filtered('is_model_header')
        children = wizard.line_ids - headers

        self.assertEqual(len(headers), 3)
        self.assertEqual(len(headers.filtered('kit_bom_id')), 2)
        self.assertEqual(len(headers.filtered(lambda line: not line.kit_bom_id)), 1)
        self.assertTrue(all(not header.production_line_id for header in headers))
        self.assertTrue(all(not header.selected for header in headers))
        self.assertEqual(set(children.mapped('production_line_id').ids), set(lines.ids))
        self.assertEqual(len(children), len(lines))
        self.assertEqual(len(set(children.mapped('production_line_id').ids)), len(lines))

        kit_b_header = headers.filtered(
            lambda line: line.kit_bom_id == fixture['kit_b']
        ).ensure_one()
        kit_b_children = children.filtered(
            lambda line: line.kit_group_token == kit_b_header.kit_group_token
        )
        kit_b_sofas = kit_b_children.filtered(
            lambda line: line.product_id == fixture['sofa']
        )
        self.assertEqual(len(kit_b_sofas), 2)
        self.assertEqual(len(set(kit_b_sofas.mapped('production_line_id').ids)), 2)
        self.assertEqual(stage.state, 'in_progress')
        self.assertFalse(stage._get_stage_line_ids_data('quality_production_line_ids_data'))
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)
        self.assertEqual([
            (
                line.id,
                line.active,
                line.product_id.id,
                line.product_qty,
                line.kit_bom_id.id,
                line.kit_instance_number,
            )
            for line in lines.sorted('id')
        ], snapshot)

    def test_grouped_quality_partial_selection_keeps_exact_piece_ids(self):
        fixture = self._create_grouped_fixture()
        stage = fixture['stage']
        lines = fixture['lines']
        move_count = self.env['stock.move'].search_count([])
        wizard = self._open_quality_wizard(stage)
        children = wizard.line_ids.filtered(lambda line: not line.is_model_header)
        kit_b_sofas = children.filtered(lambda line: (
            line.kit_bom_id == fixture['kit_b']
            and line.product_id == fixture['sofa']
        )).sorted(lambda line: line.production_line_id.id)
        self.assertEqual(len(kit_b_sofas), 2)
        selected_row = kit_b_sofas[:1]
        twin_row = kit_b_sofas[1:]
        children.write({'selected': False})
        selected_row.write({'selected': True})

        result = wizard.action_send_selected_to_quality()

        self.assertEqual(result['res_model'], stage._name)
        self.assertEqual(result['res_id'], stage.id)
        self.assertEqual(stage.state, 'quality_check')
        quality_lines = stage._get_stage_line_ids_data(
            'quality_production_line_ids_data'
        )
        self.assertEqual(quality_lines, selected_row.production_line_id)
        remaining = stage._get_stage_quality_candidate_lines()
        self.assertIn(twin_row.production_line_id, remaining)
        self.assertEqual(
            set(stage._get_stage_line_ids_data('active_production_line_ids_data').ids),
            set(lines.ids),
        )
        self.assertEqual(self.env['stock.move'].search_count([]), move_count)
        self.assertTrue(all(line.active for line in lines))
        self.assertTrue(all(line.product_qty == 1.0 for line in lines))

    def test_non_special_quality_wizard_remains_flat(self):
        model = self.env['furniture.product.model'].create({
            'name': 'Flat Quality Model %s' % self._testMethodName,
        })
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 2.0,
            'state': 'confirmed',
            'use_priming': True,
        })
        lines = self.env['furniture.mrp.production.line']
        for sequence in (10, 20):
            product = self._create_product('Flat Quality Product %s' % sequence, model, ('priming',))
            lines |= self.env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
            ).create({
                'production_id': production.id,
                'sequence': sequence,
                'product_id': product.id,
                'product_qty': 1.0,
                'furniture_order_model_id': model.id,
                'use_priming': True,
            })
        stage = self.env['furniture.mrp.priming'].create({
            'name': 'PRI/QUALITY/FLAT/%s' % self._testMethodName,
            'production_order_id': production.id,
            'state': 'in_progress',
        })
        production.write({'priming_order_id': stage.id})
        stage._set_stage_line_ids_data('active_production_line_ids_data', lines)

        wizard = self._open_quality_wizard(stage)

        self.assertFalse(wizard.line_ids.filtered('is_model_header'))
        self.assertEqual(len(wizard.line_ids), 2)
        self.assertEqual(set(wizard.line_ids.mapped('production_line_id').ids), set(lines.ids))

    def test_quality_wizard_view_keeps_group_and_submission_contract(self):
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_quality_send_wizard_form'
        )
        document = etree.fromstring(view.arch_db.encode())
        grouped_lists = document.xpath(
            "//field[@name='line_ids' and contains(@class, 'o_furniture_quality_grouped_lines')]/list"
        )
        self.assertEqual(len(grouped_lists), 1)
        grouped_list = grouped_lists[0]
        field_names = {
            field.get('name')
            for field in grouped_list.xpath('./field')
        }
        self.assertTrue({
            'sequence',
            'is_model_header',
            'model_group_label',
            'kit_group_token',
            'kit_bom_id',
            'kit_instance_number',
            'production_line_id',
            'selected',
        }.issubset(field_names))
        self.assertEqual(len(document.xpath(
            "//footer/button[@name='action_send_selected_to_quality' and @type='object']"
        )), 1)
