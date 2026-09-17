from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


class TestSaleKitAllocation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = cls.env['furniture.product.model'].create({
            'name': 'Sale Kit Allocation Model',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Sale Kit Allocation Customer',
            'customer_rank': 1,
        })
        cls.finished_location = cls.env.ref('furniture_mrp.location_finished_goods')
        cls.chair, cls.chair_bom = cls._create_component('Sale Allocation Chair')
        cls.sofa, cls.sofa_bom = cls._create_component('Sale Allocation Sofa')
        cls.kit_product = cls.env['product.product'].create({
            'name': 'Sale Allocation Complete Kit',
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': cls.model.id,
        })
        cls.kit_bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.kit_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': cls.kit_product.id,
            'furniture_model_id': cls.model.id,
            'bom_line_ids': [
                (0, 0, {
                    'product_id': cls.chair.id,
                    'product_qty': 1.0,
                    'product_uom_id': cls.chair.uom_id.id,
                }),
                (0, 0, {
                    'product_id': cls.sofa.id,
                    'product_qty': 1.0,
                    'product_uom_id': cls.sofa.uom_id.id,
                }),
            ],
        })

    @classmethod
    def _create_component(cls, name, model=None):
        model = model or cls.model
        product = cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id,
        })
        bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'furniture_product_id': product.id,
            'furniture_model_id': model.id,
            'use_tailoring': True,
        })
        return product, bom

    @classmethod
    def _create_kit(cls, name, model, components):
        kit_product = cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'furniture_model_id': model.id,
        })
        kit_bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': kit_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'furniture_product_id': kit_product.id,
            'furniture_model_id': model.id,
            'bom_line_ids': [
                (0, 0, {
                    'product_id': product.id,
                    'product_qty': qty,
                    'product_uom_id': product.uom_id.id,
                })
                for product, qty in components
            ],
        })
        return kit_product, kit_bom

    def _create_sale(self, qty):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.kit_product.id,
                'product_uom_qty': qty,
            })],
        })

    def _create_started_wip(self, qty):
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': qty * 2,
            'state': 'in_production',
            'use_tailoring': True,
        })
        for sequence, product, bom in (
            (10, self.chair, self.chair_bom),
            (20, self.sofa, self.sofa_bom),
        ):
            self.env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
            ).create({
                'production_id': production.id,
                'sequence': sequence,
                'product_id': product.id,
                'product_qty': qty,
                'bom_id': bom.id,
                'furniture_order_model_id': self.model.id,
                'first_stage_started': True,
                'first_stage_started_stage': 'tailoring',
                'planned_start_stage': 'tailoring',
                'use_tailoring': True,
            })
        return production

    def test_finished_stock_menu_shows_physical_components_not_virtual_kit(self):
        self.env['stock.quant']._update_available_quantity(
            self.chair,
            self.finished_location,
            3.0,
        )
        # A stocked component must not disappear from the warehouse screen if
        # its product master is archived later.
        self.chair.product_tmpl_id.active = False

        action = self.env.ref('furniture_mrp.action_furniture_location_quants').read()[0]
        action_context = safe_eval(
            action['context'],
            {'active_id': self.finished_location.id},
        )
        action_domain = safe_eval(action['domain'])
        products = self.env[action['res_model']].with_context(**action_context).search(
            action_domain,
        )

        self.assertEqual(action['res_model'], 'product.product')
        self.assertIn(self.chair, products)
        self.assertNotIn(self.kit_product, products)

    def test_sale_product_selector_only_includes_kit_recipes(self):
        product_without_recipe = self.env['product.product'].create({
            'name': 'Sale Allocation Product Without Recipe',
            'type': 'consu',
            'sale_ok': True,
        })
        inactive_recipe_product = self.env['product.product'].create({
            'name': 'Sale Allocation Product With Inactive Recipe',
            'type': 'consu',
            'sale_ok': True,
        })
        self.env['mrp.bom'].create({
            'product_tmpl_id': inactive_recipe_product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'phantom',
            'active': False,
            'furniture_product_id': inactive_recipe_product.id,
            'furniture_model_id': self.model.id,
        })

        self.assertTrue(self.chair.furniture_has_active_sale_recipe)
        self.assertTrue(self.kit_product.furniture_has_active_sale_recipe)
        self.assertFalse(self.chair.furniture_has_active_sale_kit_recipe)
        self.assertTrue(self.kit_product.furniture_has_active_sale_kit_recipe)
        self.assertFalse(product_without_recipe.furniture_has_active_sale_kit_recipe)
        self.assertFalse(inactive_recipe_product.furniture_has_active_sale_kit_recipe)
        self.assertFalse(product_without_recipe.furniture_has_active_sale_recipe)
        self.assertFalse(inactive_recipe_product.furniture_has_active_sale_recipe)

        sale = self.env['sale.order'].create({'partner_id': self.partner.id})
        catalog_products = self.env['product.product'].search(
            sale._get_product_catalog_domain()
        )
        self.assertNotIn(self.chair, catalog_products)
        self.assertIn(self.kit_product, catalog_products)
        self.assertNotIn(product_without_recipe, catalog_products)
        self.assertNotIn(inactive_recipe_product, catalog_products)

    def test_confirm_creates_component_shortage_mps(self):
        sale = self._create_sale(2.0)
        sale.action_confirm()

        self.assertEqual(len(sale.furniture_kit_allocation_ids), 2)
        self.assertTrue(sale.furniture_has_kit_lines)
        self.assertEqual(sale.furniture_kit_type_count, 1)
        self.assertAlmostEqual(sale.furniture_kit_total_qty, 2.0)
        self.assertAlmostEqual(sale.furniture_kit_ready_qty, 0.0)
        self.assertAlmostEqual(sale.furniture_kit_wip_qty, 0.0)
        self.assertAlmostEqual(sale.furniture_kit_shortage_qty, 2.0)
        for allocation in sale.furniture_kit_allocation_ids:
            self.assertAlmostEqual(allocation.finished_allocated_qty, 0.0)
            self.assertAlmostEqual(allocation.shortage_qty, 2.0)
            self.assertEqual(allocation.state, 'shortage')
        self.assertTrue(sale.furniture_shortage_mps_id)
        self.assertEqual(len(sale.furniture_shortage_mps_id.line_ids), 2)
        self.assertEqual(
            set(sale.furniture_shortage_mps_id.line_ids.mapped('product_qty')),
            {2.0},
        )

    def test_two_sales_cannot_allocate_the_same_wip(self):
        self._create_started_wip(2.0)

        first_sale = self._create_sale(2.0)
        first_sale.action_confirm()
        second_sale = self._create_sale(2.0)
        second_sale.action_confirm()

        self.assertEqual(
            set(first_sale.furniture_kit_allocation_ids.mapped('wip_allocated_qty')),
            {2.0},
        )
        self.assertEqual(
            set(first_sale.furniture_kit_allocation_ids.mapped('shortage_qty')),
            {0.0},
        )
        self.assertEqual(
            set(second_sale.furniture_kit_allocation_ids.mapped('wip_allocated_qty')),
            {0.0},
        )
        self.assertEqual(
            set(second_sale.furniture_kit_allocation_ids.mapped('shortage_qty')),
            {2.0},
        )

    def test_manual_refresh_returns_feedback_and_reloads_view(self):
        sale = self._create_sale(1.0)

        action = sale.action_refresh_furniture_kit_availability()

        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertEqual(action['params']['next']['tag'], 'soft_reload')
        self.assertEqual(len(sale.furniture_kit_allocation_ids), 2)

    def test_draft_quotation_syncs_and_groups_kit_automatically(self):
        sale = self._create_sale(1.0)
        kit_line = sale.order_line

        self.assertEqual(len(kit_line.furniture_kit_allocation_ids), 2)
        self.assertEqual(sale.furniture_kit_display_line_ids, kit_line)
        self.assertEqual(kit_line.furniture_kit_state, 'shortage')
        self.assertNotIn('اضغط تحديث', kit_line.furniture_kit_availability)
        self.assertIn(self.chair.display_name, str(kit_line.furniture_kit_components_html))
        self.assertIn(self.sofa.display_name, str(kit_line.furniture_kit_components_html))

    def test_shortage_button_creates_one_editable_draft_production(self):
        sale = self._create_sale(2.0)

        action = sale.action_open_or_create_furniture_shortage_production()
        production = self.env['furniture.mrp.production'].browse(action['res_id'])

        self.assertEqual(production.state, 'draft')
        self.assertEqual(production.furniture_sale_order_id, sale)
        self.assertEqual(production.furniture_sale_kit_line_id, sale.order_line)
        self.assertEqual(len(production.production_line_ids), 2)
        self.assertEqual(set(production.production_line_ids.mapped('product_qty')), {2.0})
        self.assertEqual(set(production.production_line_ids.mapped('buyer_partner_id')), {self.partner})
        self.assertEqual(set(production.production_line_ids.mapped('sale_kit_product_id')), {self.kit_product})
        self.assertEqual(set(production.production_line_ids.mapped('furniture_order_model_id')), {self.model})

        edited_line = production.production_line_ids.filtered(
            lambda line: line.product_id == self.chair
        )
        edited_line.write({
            'width_cm': 321.0,
            'use_packaging': True,
        })
        sale.order_line.product_uom_qty = 3.0

        second_action = sale.action_open_or_create_furniture_shortage_production()

        self.assertEqual(second_action['res_id'], production.id)
        self.assertEqual(len(sale.furniture_shortage_production_ids), 1)
        self.assertEqual(set(production.production_line_ids.mapped('product_qty')), {3.0})
        self.assertAlmostEqual(edited_line.width_cm, 321.0)
        self.assertTrue(edited_line.use_packaging)

    def test_shortage_creates_one_production_per_distinct_kit_line(self):
        big_moon_model = self.env['furniture.product.model'].create({
            'name': 'Sale Allocation Big Moon Model',
        })
        sized_model = self.env['furniture.product.model'].create({
            'name': 'Sale Allocation Big Moon 50x60x80 Model',
        })
        big_moon_chair, _bom = self._create_component(
            'Sale Allocation Big Moon Chair',
            big_moon_model,
        )
        big_moon_sofa, _bom = self._create_component(
            'Sale Allocation Big Moon Sofa',
            big_moon_model,
        )
        sized_chair, _bom = self._create_component(
            'Sale Allocation Sized Chair',
            sized_model,
        )
        sized_sofa, _bom = self._create_component(
            'Sale Allocation Sized Sofa',
            sized_model,
        )
        big_moon_kit, _kit_bom = self._create_kit(
            'Sale Allocation Big Moon Kit',
            big_moon_model,
            [(big_moon_chair, 1.0), (big_moon_sofa, 1.0)],
        )
        sized_kit, _kit_bom = self._create_kit(
            'Sale Allocation Big Moon Kit 50x60x80',
            sized_model,
            [(sized_chair, 1.0), (sized_sofa, 1.0)],
        )
        sale = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': self.kit_product.id,
                    'product_uom_qty': 10.0,
                }),
                (0, 0, {
                    'product_id': big_moon_kit.id,
                    'product_uom_qty': 154.0,
                }),
                (0, 0, {
                    'product_id': sized_kit.id,
                    'product_uom_qty': 10.0,
                }),
            ],
        })

        action = sale.action_open_or_create_furniture_shortage_production()
        productions = sale.furniture_shortage_production_ids.filtered(
            lambda production: production.state != 'cancelled'
        )

        self.assertEqual(action['view_mode'], 'list,form')
        self.assertEqual(len(productions), 3)
        self.assertEqual(
            set(productions.mapped('furniture_sale_kit_line_id')),
            set(sale.order_line),
        )
        expected = {
            self.kit_product: ({self.chair, self.sofa}, 10.0),
            big_moon_kit: ({big_moon_chair, big_moon_sofa}, 154.0),
            sized_kit: ({sized_chair, sized_sofa}, 10.0),
        }
        for production in productions:
            kit_product = production.furniture_sale_kit_line_id.product_id
            expected_components, expected_qty = expected[kit_product]
            self.assertEqual(
                set(production.production_line_ids.mapped('product_id')),
                expected_components,
            )
            self.assertNotIn(kit_product, production.production_line_ids.mapped('product_id'))
            self.assertEqual(
                set(production.production_line_ids.mapped('product_qty')),
                {expected_qty},
            )
            self.assertEqual(
                set(production.production_line_ids.mapped('sale_kit_product_id')),
                {kit_product},
            )

        original_ids = set(productions.ids)
        sale.action_open_or_create_furniture_shortage_production()
        self.assertEqual(
            set(sale.furniture_shortage_production_ids.filtered(
                lambda production: production.state != 'cancelled'
            ).ids),
            original_ids,
        )

        sale.action_confirm()
        sale.furniture_shortage_mps_id.action_confirm()
        mps_action = sale.furniture_shortage_mps_id.action_generate_production()
        self.assertEqual(mps_action['view_mode'], 'list,form')
        self.assertEqual(
            set(sale.furniture_shortage_mps_id.line_ids.mapped('production_order_id')),
            set(productions),
        )

    def test_confirmed_shortage_mps_reuses_sale_draft_production(self):
        sale = self._create_sale(1.0)
        production_action = sale.action_open_or_create_furniture_shortage_production()
        production = self.env['furniture.mrp.production'].browse(production_action['res_id'])

        sale.action_confirm()

        self.assertTrue(sale.furniture_shortage_mps_id)
        self.assertEqual(production.mps_id, sale.furniture_shortage_mps_id)
        self.assertEqual(
            set(sale.furniture_shortage_mps_id.line_ids.mapped('production_order_id')),
            {production},
        )
        sale.furniture_shortage_mps_id.action_confirm()
        mps_action = sale.furniture_shortage_mps_id.action_generate_production()
        self.assertEqual(mps_action['res_id'], production.id)

    def test_sale_order_view_keeps_compact_kit_state_and_clean_layout(self):
        view = self.env.ref('sale.view_order_form')
        arch = self.env['sale.order'].get_view(
            view_id=view.id,
            view_type='form',
        )['arch']

        self.assertIn('o_furniture_sale_order_sheet', arch)
        self.assertIn('o_furniture_sale_header_card', arch)
        self.assertIn('o_furniture_sale_order_lines', arch)
        self.assertEqual(
            self.env['sale.order']._fields['furniture_delivery_date'].type,
            'date',
        )
        self.assertIn('name="furniture_delivery_date"', arch)
        self.assertIn('name="furniture_kit_state"', arch)
        self.assertIn('name="furniture_kit_availability"', arch)
        self.assertIn('optional="hide"', arch)
        self.assertNotIn('name="add_section_control"', arch)
        self.assertNotIn('name="optional_products"', arch)
        self.assertNotIn('name="payment_term_id"', arch)
        for action_class in (
            'o_furniture_sale_action_send',
            'o_furniture_sale_action_confirm',
            'o_furniture_sale_action_preview',
            'o_furniture_sale_action_cancel',
        ):
            self.assertIn(action_class, arch)
