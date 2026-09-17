from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form, TransactionCase
from lxml import etree


class TestModelSpecificRecipes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Neutral Model Recipe Product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.raw_a = cls.env['product.product'].create({
            'name': 'Raw Material Model A',
            'type': 'consu',
            'is_storable': True,
        })
        cls.raw_b = cls.env['product.product'].create({
            'name': 'Raw Material Model B',
            'type': 'consu',
            'is_storable': True,
        })
        cls.model_a = cls.env['furniture.product.model'].create({
            'name': 'Recipe Model A',
        })
        cls.model_b = cls.env['furniture.product.model'].create({
            'name': 'Recipe Model B',
        })

    def _create_bom(self, model, raw_product, product=None):
        product = product or self.product
        return self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            'furniture_recipe_model_id': model.id,
            'use_priming': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': raw_product.id,
                'product_qty': 1.0,
                'product_uom_id': raw_product.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })

    def _create_generic_bom(self, product, raw_product=None):
        raw_product = raw_product or self.raw_a
        return self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            'use_priming': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': raw_product.id,
                'product_qty': 1.0,
                'product_uom_id': raw_product.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })

    def test_batch_product_wizard_creates_one_line_per_selected_product(self):
        second_product = self.env['product.product'].create({
            'name': 'Second Neutral Model Recipe Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(
            self.model_a,
            self.raw_b,
            product=second_product,
        )
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].create({
            'production_id': production.id,
            'furniture_order_model_id': self.model_a.id,
            'buyer_partner_id': self.env.company.partner_id.id,
        })

        self.assertTrue(
            {self.product.id, second_product.id}.issubset(
                set(wizard.available_product_ids.ids)
            )
        )
        wizard.selected_product_ids = self.product | second_product
        wizard._onchange_selected_product_ids()
        self.assertEqual(
            set(wizard.line_ids.mapped('product_id').ids),
            {self.product.id, second_product.id},
        )

        first_row = wizard.line_ids.filtered(
            lambda line: line.product_id == self.product
        )
        second_row = wizard.line_ids.filtered(
            lambda line: line.product_id == second_product
        )
        first_row.product_qty = 2.0
        second_row.product_qty = 3.0
        first_row.width_cm = 225.0
        first_row.depth_cm = 95.0
        first_row.height_cm = 80.0

        action = wizard.action_create_lines()

        self.assertEqual(action['res_id'], production.id)
        self.assertEqual(len(production.production_line_ids), 2)
        self.assertEqual(
            set(production.production_line_ids.mapped('product_id').ids),
            {self.product.id, second_product.id},
        )
        self.assertEqual(
            set(production.production_line_ids.mapped('furniture_order_model_id').ids),
            {self.model_a.id},
        )
        created_first = production.production_line_ids.filtered(
            lambda line: line.product_id == self.product
        )
        created_second = production.production_line_ids.filtered(
            lambda line: line.product_id == second_product
        )
        self.assertEqual(created_first.product_qty, 2.0)
        self.assertEqual(created_second.product_qty, 3.0)
        self.assertEqual(created_first.width_cm, 225.0)
        self.assertEqual(created_first.depth_cm, 95.0)
        self.assertEqual(created_first.height_cm, 80.0)
        self.assertEqual(
            created_first.buyer_partner_id,
            self.env.company.partner_id,
        )
        self.assertEqual(
            production.furniture_order_model_id,
            self.model_a,
        )
        self.assertEqual(
            production.buyer_partner_id,
            self.env.company.partner_id,
        )

    def test_batch_wizard_includes_exact_and_genuinely_generic_products(self):
        generic_product = self.env['product.product'].create({
            'name': 'Generic More Product',
            'type': 'consu',
            'is_storable': True,
        })
        other_model_product = self.env['product.product'].create({
            'name': 'Other Model Only Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        generic_bom = self._create_generic_bom(generic_product, self.raw_b)
        self._create_bom(
            self.model_b,
            self.raw_b,
            product=other_model_product,
        )
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].create({
            'production_id': production.id,
            'furniture_order_model_id': self.model_a.id,
        })

        self.assertIn(self.product, wizard.available_product_ids)
        self.assertIn(generic_product, wizard.available_product_ids)
        self.assertNotIn(other_model_product, wizard.available_product_ids)
        self.assertEqual(
            self.env['mrp.bom']._find_furniture_production_recipe(
                generic_product,
                self.model_a,
            ),
            generic_bom,
        )
        self.assertFalse(
            self.env['mrp.bom']._find_furniture_production_recipe(
                other_model_product,
                self.model_a,
            )
        )

    def test_batch_wizard_creates_generic_line_with_selected_order_model(self):
        generic_product = self.env['product.product'].create({
            'name': 'Created Generic More Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        exact_bom = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        generic_bom = self._create_generic_bom(generic_product, self.raw_b)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].create({
            'production_id': production.id,
            'furniture_order_model_id': self.model_a.id,
        })

        wizard.selected_product_ids = self.product | generic_product
        wizard._onchange_selected_product_ids()
        generic_row = wizard.line_ids.filtered(
            lambda line: line.product_id == generic_product
        )
        exact_row = wizard.line_ids.filtered(
            lambda line: line.product_id == self.product
        )
        self.assertEqual(generic_row.bom_id, generic_bom)
        self.assertEqual(exact_row.bom_id, exact_bom)

        wizard.action_create_lines()

        generic_line = production.production_line_ids.filtered(
            lambda line: line.product_id == generic_product
        )
        exact_line = production.production_line_ids.filtered(
            lambda line: line.product_id == self.product
        )
        self.assertEqual(generic_line.furniture_order_model_id, self.model_a)
        self.assertEqual(generic_line.bom_id, generic_bom)
        self.assertFalse(generic_line.bom_id.furniture_is_model_recipe)
        self.assertEqual(exact_line.bom_id, exact_bom)
        self.assertEqual(
            production._find_bom_for_product(generic_product, self.model_a),
            generic_bom,
        )

    def test_new_order_product_wizard_creates_draft_only_on_confirmation(self):
        second_product = self.env['product.product'].create({
            'name': 'Second New Order Popup Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(
            self.model_a,
            self.raw_b,
            product=second_product,
        )
        production_model = self.env['furniture.mrp.production']
        production_count = production_model.search_count([])
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].with_context(default_create_new_production=True).create({
            'furniture_order_model_id': self.model_a.id,
            'buyer_partner_id': self.env.company.partner_id.id,
        })

        self.assertTrue(wizard.create_new_production)
        self.assertFalse(wizard.production_id)
        self.assertEqual(production_model.search_count([]), production_count)

        wizard.selected_product_ids = self.product | second_product
        wizard._onchange_selected_product_ids()
        wizard.line_ids.filtered(
            lambda line: line.product_id == self.product
        ).product_qty = 2.0
        wizard.line_ids.filtered(
            lambda line: line.product_id == second_product
        ).product_qty = 3.0

        action = wizard.action_create_lines()
        production = production_model.browse(action['res_id']).exists()

        self.assertTrue(production)
        self.assertEqual(production_model.search_count([]), production_count + 1)
        self.assertEqual(production.state, 'draft')
        self.assertEqual(len(production.production_line_ids), 2)
        self.assertEqual(
            set(production.production_line_ids.mapped('product_qty')),
            {2.0, 3.0},
        )
        self.assertEqual(action['res_model'], 'furniture.mrp.production')
        self.assertEqual(action['view_mode'], 'form')
        self.assertEqual(action['target'], 'current')
        self.assertEqual(
            action['context']['form_view_initial_mode'],
            'edit',
        )
        self.assertNotIn(
            'default_create_new_production',
            action['context'],
        )

    def test_order_level_customer_data_is_applied_to_all_product_lines(self):
        second_product = self.env['product.product'].create({
            'name': 'Second Order Customer Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(self.model_a, self.raw_b, product=second_product)
        buyer = self.env['res.partner'].create({
            'name': 'Order Buyer',
            'is_company': True,
        })
        beneficiary = self.env['res.partner'].create({
            'name': 'Order Beneficiary',
        })
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
            'furniture_order_model_id': self.model_a.id,
            'buyer_partner_id': buyer.id,
            'beneficiary_partner_id': beneficiary.id,
        })
        lines = self.env['furniture.mrp.production.line'].create([
            {
                'production_id': production.id,
                'product_id': self.product.id,
                'product_qty': 1.0,
            },
            {
                'production_id': production.id,
                'product_id': second_product.id,
                'product_qty': 2.0,
            },
        ])
        self.assertEqual(set(lines.mapped('furniture_order_model_id')), {self.model_a})
        self.assertEqual(set(lines.mapped('buyer_partner_id')), {buyer})
        self.assertEqual(set(lines.mapped('beneficiary_partner_id')), {beneficiary})

        replacement_buyer = self.env['res.partner'].create({
            'name': 'Replacement Order Buyer',
            'is_company': True,
        })
        production.buyer_partner_id = replacement_buyer
        self.assertEqual(
            set(production.production_line_ids.mapped('buyer_partner_id')),
            {replacement_buyer},
        )

    def test_batch_product_form_onchange_populates_quantity_and_dimensions(self):
        second_product = self.env['product.product'].create({
            'name': 'Second Form Onchange Product',
            'type': 'consu',
            'is_storable': True,
        })
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(
            self.model_a,
            self.raw_b,
            product=second_product,
        )
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].create({
            'production_id': production.id,
        })

        form = Form(wizard)
        form.furniture_order_model_id = self.model_a
        form.selected_product_ids.add(self.product)
        self.assertEqual(len(form.line_ids), 1)
        with form.line_ids.edit(0) as row:
            self.assertEqual(row.product_id, self.product)
            self.assertEqual(row.product_qty, 1.0)
            row.product_qty = 4.0
            row.dimensions_expanded = True
            row.width_cm = 210.0
            row.depth_cm = 95.0
            row.height_cm = 88.0
        form.selected_product_ids.add(second_product)

        self.assertEqual(len(form.line_ids), 2)
        with form.line_ids.edit(0) as first_row:
            self.assertEqual(first_row.product_id, self.product)
            self.assertEqual(first_row.product_qty, 4.0)
            self.assertEqual(first_row.width_cm, 210.0)
            self.assertEqual(first_row.depth_cm, 95.0)
            self.assertEqual(first_row.height_cm, 88.0)

        saved_wizard = form.save()
        self.assertEqual(len(saved_wizard.line_ids), 2)
        self.assertEqual(
            set(saved_wizard.line_ids.mapped('product_id').ids),
            {self.product.id, second_product.id},
        )
        self.assertTrue(all(saved_wizard.line_ids.mapped('bom_id')))

    def test_batch_product_wizard_locks_existing_order_model(self):
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(self.model_b, self.raw_b)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'product_qty': 1.0,
        })
        wizard = self.env[
            'furniture.mrp.production.first.line.wizard'
        ].create({
            'production_id': production.id,
            'furniture_order_model_id': self.model_a.id,
        })

        self.assertTrue(wizard.model_locked)
        wizard.furniture_order_model_id = self.model_b
        warning = wizard._onchange_batch_furniture_order_model_id()

        self.assertEqual(wizard.furniture_order_model_id, self.model_a)
        self.assertIn('warning', warning)

    def test_normal_bom_final_product_selector_excludes_raw_materials(self):
        self.assertFalse(self.product.furniture_is_finished_product)
        self.assertFalse(self.raw_a.furniture_is_finished_product)

        self._create_bom(self.model_a, self.raw_a)
        (self.product | self.raw_a).invalidate_recordset([
            'furniture_is_finished_product',
        ])

        self.assertTrue(self.product.furniture_is_finished_product)
        self.assertFalse(self.raw_a.furniture_is_finished_product)
        selectable_products = self.env['product.product'].search([
            ('id', 'in', (self.product | self.raw_a).ids),
            ('furniture_is_finished_product', '=', True),
            ('furniture_dimension_source_product_id', '=', False),
        ])
        self.assertEqual(selectable_products, self.product)

        view_arch = self.env.ref(
            'furniture_mrp.view_mrp_bom_form_furniture_stages'
        ).arch_db
        self.assertIn(
            "('furniture_is_finished_product', '=', True)",
            view_arch,
        )
        self.assertIn("if type == 'normal' else", view_arch)

    def test_bom_product_selectors_cannot_create_records(self):
        view_arch = self.env.ref(
            'furniture_mrp.view_mrp_bom_form_furniture_stages'
        ).arch_db
        arch = etree.fromstring(view_arch.encode())

        final_product_fields = arch.xpath("//field[@name='furniture_product_id']")
        self.assertTrue(final_product_fields)
        for field in final_product_fields:
            options = field.get('options', '')
            self.assertIn("'no_create': True", options)
            self.assertIn("'no_create_edit': True", options)

        material_fields = arch.xpath(
            "//page[@name='furniture_stage_materials']"
            "//field[@name='product_id']"
        )
        self.assertEqual(len(material_fields), 8)
        for field in material_fields:
            options = field.get('options', '')
            self.assertIn("'no_create': True", options)
            self.assertIn("'no_create_edit': True", options)

    def test_stage_material_lists_show_quantity_cost_after_uom(self):
        view_arch = self.env.ref(
            'furniture_mrp.view_mrp_bom_form_furniture_stages'
        ).arch_db
        arch = etree.fromstring(view_arch.encode())
        material_lists = arch.xpath(
            "//page[@name='furniture_stage_materials']//list"
        )

        self.assertEqual(len(material_lists), 8)
        for material_list in material_lists:
            visible_field_names = [
                field.get('name')
                for field in material_list.xpath('./field')
                if field.get('column_invisible') != '1'
            ]
            self.assertIn('recipe_quantity_cost', visible_field_names)
            self.assertEqual(
                visible_field_names.index('recipe_quantity_cost'),
                visible_field_names.index('product_uom_code') + 1,
            )
            cost_field = material_list.xpath(
                "./field[@name='recipe_quantity_cost']"
            )[0]
            self.assertEqual(cost_field.get('readonly'), '1')

    def test_stage_material_quantity_cost_uses_recipe_quantity(self):
        self.raw_a.standard_price = 1000.0
        bom = self._create_bom(self.model_a, self.raw_a)
        material_line = bom.furniture_stage_material_line_ids

        material_line.product_qty = 0.5

        self.assertEqual(material_line.currency_id, self.env.company.currency_id)
        self.assertAlmostEqual(material_line.recipe_quantity_cost, 500.0)

    def test_product_and_model_select_exact_recipe(self):
        bom_a = self._create_bom(self.model_a, self.raw_a)
        bom_b = self._create_bom(self.model_b, self.raw_b)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })

        line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_b.id,
            'product_qty': 1.0,
        })

        self.assertEqual(line.bom_id.furniture_parent_bom_id, bom_b)
        self.assertNotEqual(line.bom_id.furniture_parent_bom_id, bom_a)
        self.assertTrue(line.bom_id.furniture_is_model_recipe)
        self.assertEqual(
            line.bom_id.furniture_stage_material_line_ids.product_id,
            self.raw_b,
        )

    def test_product_alone_does_not_select_recipe(self):
        self._create_bom(self.model_a, self.raw_a)
        self._create_bom(self.model_b, self.raw_b)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })

        line = self.env['furniture.mrp.production.line'].new({
            'production_id': production.id,
            'product_id': self.product.id,
            'product_qty': 1.0,
        })
        line._onchange_product_id()

        self.assertFalse(line.furniture_order_model_id)
        self.assertFalse(line.bom_id)

    def test_switching_models_restores_each_models_saved_materials(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)
        recipe_a = self.env['mrp.bom'].browse(
            master_bom.action_select_furniture_recipe_model(self.model_a.id)
        )
        recipe_b = self.env['mrp.bom'].browse(
            recipe_a.action_select_furniture_recipe_model(self.model_b.id)
        )

        self.assertTrue(recipe_a.furniture_is_model_recipe)
        self.assertTrue(recipe_b.furniture_is_model_recipe)
        self.assertEqual(recipe_a.furniture_parent_bom_id, master_bom)
        self.assertEqual(recipe_b.furniture_parent_bom_id, master_bom)
        self.assertFalse(recipe_b.furniture_stage_material_line_ids)

        recipe_b.write({
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': self.raw_b.id,
                'product_qty': 2.0,
                'product_uom_id': self.raw_b.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })
        reopened_a = self.env['mrp.bom'].browse(
            recipe_b.action_select_furniture_recipe_model(self.model_a.id)
        )

        self.assertEqual(
            reopened_a.furniture_stage_material_line_ids.product_id,
            self.raw_a,
        )
        self.assertEqual(
            reopened_a.furniture_stage_material_line_ids.product_qty,
            1.0,
        )
        self.assertEqual(reopened_a, recipe_a)

        reopened_b = self.env['mrp.bom'].browse(
            reopened_a.action_select_furniture_recipe_model(self.model_b.id)
        )
        self.assertEqual(
            reopened_b.furniture_stage_material_line_ids.product_id,
            self.raw_b,
        )
        self.assertEqual(
            reopened_b.furniture_stage_material_line_ids.product_qty,
            2.0,
        )
        self.assertEqual(reopened_b, recipe_b)
        self.assertFalse(master_bom.furniture_model_id)
        self.assertEqual(master_bom.furniture_recipe_model_id, self.model_b)

    def test_action_returns_hidden_child_and_child_can_open_sibling(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)

        recipe_a_id = master_bom.action_select_furniture_recipe_model(
            self.model_a.id,
        )
        recipe_a = self.env['mrp.bom'].browse(recipe_a_id)
        recipe_b_id = recipe_a.action_select_furniture_recipe_model(
            self.model_b.id,
        )
        recipe_b = self.env['mrp.bom'].browse(recipe_b_id)

        self.assertIsInstance(recipe_a_id, int)
        self.assertIsInstance(recipe_b_id, int)
        self.assertNotEqual(recipe_a, recipe_b)
        self.assertEqual(recipe_a.furniture_parent_bom_id, master_bom)
        self.assertEqual(recipe_b.furniture_parent_bom_id, master_bom)
        self.assertEqual(recipe_a.furniture_model_id, self.model_a)
        self.assertEqual(recipe_b.furniture_model_id, self.model_b)
        self.assertTrue(recipe_a.furniture_is_model_recipe)
        self.assertTrue(recipe_b.furniture_is_model_recipe)
        self.assertEqual(
            recipe_b.action_select_furniture_recipe_model(self.model_a.id),
            recipe_a.id,
        )
        self.assertFalse(master_bom.furniture_model_id)
        self.assertEqual(master_bom.furniture_recipe_model_id, self.model_a)

    def test_one_visible_bom_provides_exact_hidden_recipe_for_each_model(self):
        bom = self._create_bom(self.model_a, self.raw_a)
        recipe_b = self.env['mrp.bom'].browse(
            bom.action_select_furniture_recipe_model(self.model_b.id)
        )
        recipe_b.write({
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': self.raw_b.id,
                'product_qty': 3.0,
                'product_uom_id': self.raw_b.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })

        recipe_a = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        recipe_b = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_b,
        )

        self.assertEqual(recipe_a.furniture_parent_bom_id, bom)
        self.assertEqual(recipe_b.furniture_parent_bom_id, bom)
        self.assertNotEqual(recipe_a, recipe_b)
        self.assertEqual(
            recipe_a.furniture_stage_material_line_ids.product_id,
            self.raw_a,
        )
        self.assertEqual(
            recipe_b.furniture_stage_material_line_ids.product_id,
            self.raw_b,
        )
        self.assertEqual(
            recipe_b.furniture_stage_material_line_ids.product_qty,
            3.0,
        )

    def test_internal_model_recipes_do_not_replace_or_expose_visible_bom(self):
        bom = self._create_bom(self.model_a, self.raw_a)
        hidden_recipe = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )

        standard_bom = self.env['mrp.bom']._bom_find(
            self.product,
            bom_type='normal',
        )[self.product]
        self.assertFalse(standard_bom)
        self.assertNotEqual(standard_bom, hidden_recipe)

        self.product.invalidate_recordset(['bom_count'])
        self.assertEqual(self.product.bom_count, 1)
        action = self.product.action_view_bom()
        visible_boms = self.env['mrp.bom'].search(action['domain'])
        self.assertIn(bom, visible_boms)
        self.assertNotIn(hidden_recipe, visible_boms)

    def test_recipe_navigation_keeps_normal_bom_model_neutral_in_list(self):
        bom = self._create_bom(self.model_a, self.raw_a)

        self.assertFalse(bom.furniture_model_id)
        recipe_b = self.env['mrp.bom'].browse(
            bom.action_select_furniture_recipe_model(self.model_b.id)
        )

        self.assertFalse(bom.furniture_model_id)
        self.assertEqual(bom.furniture_recipe_model_id, self.model_b)
        self.assertEqual(
            bom.furniture_stage_material_line_ids.product_id,
            self.raw_a,
        )
        self.assertTrue(recipe_b.furniture_is_model_recipe)
        self.assertEqual(recipe_b.furniture_parent_bom_id, bom)
        self.assertEqual(recipe_b.furniture_model_id, self.model_b)
        self.assertFalse(recipe_b.furniture_stage_material_line_ids)
        self.assertFalse(recipe_b.furniture_recipe_ready)
        self.assertFalse(self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_b,
        ))

        kit_product = self.env['product.product'].create({
            'name': 'Model Column Kit',
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': self.model_b.id,
        })
        kit_bom = self.env['mrp.bom'].create({
            'product_tmpl_id': kit_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': kit_product.id,
            'furniture_model_id': self.model_b.id,
            'bom_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 1.0,
                'product_uom_id': self.product.uom_id.id,
            })],
        })
        self.assertEqual(kit_bom.furniture_model_id, self.model_b)

    def test_legacy_normal_bom_model_input_becomes_neutral_selector(self):
        """Older API callers must not reclassify the visible product BoM."""
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': self.product.id,
            'furniture_model_id': self.model_a.id,
            'use_priming': True,
        })

        self.assertFalse(bom.furniture_model_id)
        self.assertEqual(bom.furniture_recipe_model_id, self.model_a)
        recipe = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        self.assertTrue(recipe.furniture_is_model_recipe)
        self.assertEqual(recipe.furniture_parent_bom_id, bom)
        self.assertEqual(recipe.furniture_model_id, self.model_a)

    def test_explicit_master_bom_is_normalized_to_exact_recipe_on_line(self):
        bom = self._create_bom(self.model_a, self.raw_a)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })

        line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'product_qty': 1.0,
            'bom_id': bom.id,
        })

        self.assertTrue(line.bom_id.furniture_is_model_recipe)
        self.assertEqual(line.bom_id.furniture_parent_bom_id, bom)
        self.assertEqual(line.bom_id.furniture_model_id, self.model_a)

    def test_unchanged_route_write_preserves_archived_hidden_bom(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)
        hidden_bom = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'product_qty': 3.0,
            'bom_id': hidden_bom.id,
        })
        hidden_bom.write({'active': False})

        line.write({
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'bom_id': hidden_bom.id,
            'product_qty': 4.0,
        })

        self.assertFalse(hidden_bom.active)
        self.assertTrue(hidden_bom.furniture_is_model_recipe)
        self.assertEqual(hidden_bom.furniture_parent_bom_id, master_bom)
        self.assertEqual(line.bom_id, hidden_bom)
        self.assertEqual(line.product_qty, 4.0)

    def test_running_partial_split_preserves_archived_hidden_bom(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)
        hidden_bom = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'product_qty': 5.0,
            'bom_id': hidden_bom.id,
        })
        hidden_bom.write({'active': False})
        replacement_master_bom = self._create_bom(self.model_a, self.raw_b)
        replacement_bom = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        production.write({'state': 'in_production'})

        remaining_line = line._split_for_partial_quantity(2.0)

        self.assertFalse(hidden_bom.active)
        self.assertTrue(hidden_bom.furniture_is_model_recipe)
        self.assertEqual(hidden_bom.furniture_parent_bom_id, master_bom)
        self.assertEqual(
            replacement_bom.furniture_parent_bom_id,
            replacement_master_bom,
        )
        self.assertTrue(replacement_bom.active)
        self.assertNotEqual(replacement_bom, hidden_bom)
        self.assertEqual(production.state, 'in_production')
        self.assertEqual(line.product_qty, 2.0)
        self.assertEqual(remaining_line.product_qty, 3.0)
        self.assertEqual(line.bom_id, hidden_bom)
        self.assertEqual(remaining_line.bom_id, hidden_bom)

    def test_product_line_bom_popup_uses_exact_materials_split_by_stage(self):
        bom = self._create_bom(self.model_a, self.raw_a)
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 1.0,
        })
        line = self.env['furniture.mrp.production.line'].create({
            'production_id': production.id,
            'product_id': self.product.id,
            'furniture_order_model_id': self.model_a.id,
            'product_qty': 2.0,
            'bom_id': bom.id,
        })

        self.assertTrue(line.material_line_ids)
        self.assertEqual(
            line.bom_priming_material_line_ids,
            line.material_line_ids,
        )
        self.assertFalse(line.bom_painting_material_line_ids)
        self.assertFalse(line.bom_carpentry_material_line_ids)
        self.assertFalse(line.bom_bases_material_line_ids)
        self.assertFalse(line.bom_finishing_material_line_ids)
        self.assertFalse(line.bom_tailoring_material_line_ids)
        self.assertFalse(line.bom_upholstery_material_line_ids)
        self.assertFalse(line.bom_packaging_material_line_ids)
        material = line.bom_priming_material_line_ids
        self.assertEqual(material.product_id, self.raw_a)
        self.assertEqual(material.qty_needed, 2.0)
        self.assertEqual(
            line.bom_id.furniture_stage_material_line_ids.product_qty,
            1.0,
        )
        self.assertTrue(material.sudo().bom_qty_editable)

        action = line.action_open_bom_popup()
        self.assertEqual(action['res_model'], line._name)
        self.assertEqual(action['res_id'], line.id)
        self.assertEqual(action['target'], 'new')
        self.assertEqual(
            action['view_id'],
            self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_line_bom_popup'
            ).id,
        )
        self.assertEqual(
            action['context']['form_view_initial_mode'],
            'edit',
        )
        self.assertTrue(
            action['context']['furniture_bom_manual_qty_edit'],
        )

        material.sudo().with_context(
            furniture_bom_manual_qty_edit=True,
        ).write({'qty_needed': 1.25})
        self.assertEqual(material.qty_needed, 1.25)
        self.assertEqual(
            line.material_qty_override_json[0]['qty_needed'],
            1.25,
        )
        self.assertEqual(
            line.bom_id.furniture_stage_material_line_ids.product_qty,
            1.0,
        )

        production._refresh_material_lines_for_stage_plan()
        material = line.bom_priming_material_line_ids
        self.assertEqual(material.qty_needed, 1.25)

        line.write({'product_qty': 5.0})
        self.assertEqual(line.product_qty, 5.0)
        self.assertEqual(
            line.bom_priming_material_line_ids.qty_needed,
            1.25,
        )
        self.assertEqual(
            line.bom_id.furniture_stage_material_line_ids.product_qty,
            1.0,
        )
        material = line.bom_priming_material_line_ids

        with self.assertRaises(ValidationError):
            material.sudo().with_context(
                furniture_bom_manual_qty_edit=True,
            ).write({'qty_needed': -1.0})

        material.write({'warehouse_receipt_confirmed': True})
        self.assertEqual(
            material.sudo().bom_qty_editable,
            False,
        )
        with self.assertRaises(UserError):
            material.sudo().with_context(
                furniture_bom_manual_qty_edit=True,
            ).write({'qty_needed': 3.0})

    def test_first_model_navigation_creates_blank_child_without_reclassifying_master(self):
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': self.product.id,
            'use_priming': True,
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': self.raw_a.id,
                'product_qty': 1.0,
                'product_uom_id': self.raw_a.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })

        recipe = self.env['mrp.bom'].browse(
            bom.action_select_furniture_recipe_model(self.model_a.id)
        )

        self.assertFalse(bom.furniture_model_id)
        self.assertEqual(bom.furniture_recipe_model_id, self.model_a)
        self.assertEqual(
            bom.furniture_stage_material_line_ids.product_id,
            self.raw_a,
        )
        self.assertTrue(recipe.furniture_is_model_recipe)
        self.assertEqual(recipe.furniture_parent_bom_id, bom)
        self.assertEqual(recipe.furniture_model_id, self.model_a)
        self.assertFalse(recipe.furniture_stage_material_line_ids)
        self.assertFalse(recipe.furniture_recipe_ready)

        recipe.write({
            'furniture_stage_material_line_ids': [(0, 0, {
                'stage': 'priming',
                'product_id': self.raw_b.id,
                'product_qty': 2.0,
                'product_uom_id': self.raw_b.uom_id.id,
                'quantity_mode': 'fixed',
            })],
        })
        self.assertTrue(recipe.furniture_recipe_ready)
        self.assertEqual(
            self.env['mrp.bom']._find_furniture_normal_recipe(
                self.product,
                self.model_a,
            ),
            recipe,
        )

    def test_saved_model_content_cannot_be_overwritten_through_master(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)
        recipe = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )

        with self.assertRaises(ValidationError):
            master_bom.write({
                'furniture_stage_material_line_ids': [(5, 0, 0)],
            })

        self.assertEqual(
            recipe.furniture_stage_material_line_ids.product_id,
            self.raw_a,
        )

    def test_navigation_reactivates_archived_model_recipe(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)
        recipe = self.env['mrp.bom']._find_furniture_normal_recipe(
            self.product,
            self.model_a,
        )
        recipe.write({'active': False})

        recipe_id = master_bom.action_select_furniture_recipe_model(
            self.model_a.id,
        )

        self.assertEqual(recipe_id, recipe.id)
        self.assertTrue(recipe.active)

    def test_master_with_model_children_cannot_clear_navigator_model(self):
        master_bom = self._create_bom(self.model_a, self.raw_a)

        with self.assertRaises(ValidationError):
            master_bom.with_context(
                furniture_skip_model_recipe_sync=True,
            ).write({'furniture_recipe_model_id': False})
