from odoo.tests.common import TransactionCase


class TestProductionCostDistribution(TransactionCase):

    def test_distribution_uses_approved_stage_ledger_per_product(self):
        product_a = self.env['product.product'].create({
            'name': 'Cost Distribution Sofa A',
            'type': 'consu',
            'is_storable': True,
        })
        product_b = self.env['product.product'].create({
            'name': 'Cost Distribution Chair B',
            'type': 'consu',
            'is_storable': True,
        })
        production = self.env['furniture.mrp.production'].create({
            'product_qty': 3.0,
            'state': 'confirmed',
            'use_priming': True,
        })
        Line = self.env['furniture.mrp.production.line'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        )
        line_a = Line.create({
            'production_id': production.id,
            'sequence': 10,
            'product_id': product_a.id,
            'product_qty': 2.0,
            'use_priming': True,
            'stage_selection_initialized': True,
        })
        line_b = Line.create({
            'production_id': production.id,
            'sequence': 20,
            'product_id': product_b.id,
            'product_qty': 1.0,
            'use_priming': True,
            'stage_selection_initialized': True,
        })

        Entry = self.env['furniture.mrp.stage.cost.entry']
        common = {
            'production_id': production.id,
            'stage': 'priming',
            'stage_order_model': 'furniture.mrp.priming',
            'stage_order_res_id': 1,
            'stage_order_name': 'PRI/COST/DISTRIBUTION',
        }
        Entry.create({
            **common,
            'production_line_id': line_a.id,
            'product_id': product_a.id,
            'quantity': 2.0,
            'product_uom_id': product_a.uom_id.id,
            'stage_material_cost': 200.0,
            'stage_labor_cost': 100.0,
            'cumulative_material_cost': 200.0,
            'cumulative_labor_cost': 100.0,
        })
        Entry.create({
            **common,
            'production_line_id': line_b.id,
            'product_id': product_b.id,
            'quantity': 1.0,
            'product_uom_id': product_b.uom_id.id,
            'stage_material_cost': 300.0,
            'stage_labor_cost': 50.0,
            'cumulative_material_cost': 300.0,
            'cumulative_labor_cost': 50.0,
        })
        production.invalidate_recordset([
            'material_cost',
            'labor_cost',
            'overhead_cost',
            'total_cost',
            'actual_costed_quantity',
            'cost_per_unit',
            'cost_distribution_html',
        ])

        rows = production.get_cost_report_product_rows()
        rows_by_product = {row['product']: row for row in rows}
        self.assertEqual(set(rows_by_product), {product_a, product_b})
        self.assertAlmostEqual(rows_by_product[product_a]['quantity'], 2.0)
        self.assertAlmostEqual(rows_by_product[product_a]['material_cost'], 200.0)
        self.assertAlmostEqual(rows_by_product[product_a]['labor_cost'], 100.0)
        self.assertAlmostEqual(rows_by_product[product_a]['unit_cost'], 150.0)
        self.assertAlmostEqual(rows_by_product[product_b]['quantity'], 1.0)
        self.assertAlmostEqual(rows_by_product[product_b]['material_cost'], 300.0)
        self.assertAlmostEqual(rows_by_product[product_b]['labor_cost'], 50.0)
        self.assertAlmostEqual(rows_by_product[product_b]['unit_cost'], 350.0)

        self.assertAlmostEqual(production.actual_costed_quantity, 3.0)
        self.assertAlmostEqual(production.material_cost, 500.0)
        self.assertAlmostEqual(production.labor_cost, 150.0)
        self.assertAlmostEqual(production.total_cost, 650.0)
        self.assertAlmostEqual(production.cost_per_unit, 216.67, places=2)
        self.assertAlmostEqual(
            sum(row['total_cost'] for row in rows),
            production.total_cost,
        )
        html = str(production.cost_distribution_html or '')
        self.assertIn(product_a.display_name, html)
        self.assertIn(product_b.display_name, html)
        self.assertIn('500.00', html)
        self.assertIn('650.00', html)
