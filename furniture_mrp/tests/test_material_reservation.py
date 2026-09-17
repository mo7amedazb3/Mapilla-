from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestMaterialSoftReservation(TransactionCase):
    """The availability check promises stock without moving or valuing it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Material Reservation Test Vendor',
            'supplier_rank': 1,
        })
        cls.material = cls.env['product.product'].create({
            'name': 'Material Reservation Shared Raw',
            'type': 'consu',
            'is_storable': True,
        })
        cls.material.product_tmpl_id.furniture_supplier_id = cls.vendor
        cls.env['stock.quant']._update_available_quantity(
            cls.material,
            cls.stock_location,
            10.0,
        )

    def _create_production(self, required_qty):
        production = self.env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create({
            'product_qty': 1.0,
            'state': 'confirmed',
            'date_planned_start': datetime(2026, 7, 29, 8, 0),
            'use_priming': True,
            'use_painting': False,
            'use_carpentry': False,
            'use_finishing': False,
            'use_tailoring': False,
            'use_upholstery': False,
            'use_packaging': False,
            'location_src_id': self.stock_location.id,
        })
        self.env['furniture.mrp.material.line'].create({
            'production_id': production.id,
            'product_id': self.material.id,
            'product_uom_id': self.material.uom_id.id,
            'qty_needed': required_qty,
            'stage': 'priming',
        })
        return production

    def _active_reservation(self, production):
        production.invalidate_recordset([
            'material_reservation_ids',
            'material_reserved_qty',
            'material_shortage_qty',
            'material_reservation_state',
        ])
        return production.material_reservation_ids.filtered(
            lambda reservation: reservation.state == 'active'
        ).ensure_one()

    def _managed_purchase_lines(self, production):
        return self.env['purchase.order.line'].sudo().search([
            ('order_id.furniture_material_check_production_id', '=', production.id),
            ('order_id.furniture_material_check_managed', '=', True),
            ('order_id.state', 'in', ('draft', 'sent')),
            ('furniture_material_reservation_id.production_id', '=', production.id),
            ('furniture_shortage_managed', '=', True),
        ])

    def test_first_check_wins_and_shortage_purchase_is_idempotent(self):
        first = self._create_production(5.0)
        second = self._create_production(7.0)
        move_count = self.env['stock.move'].sudo().search_count([])
        valuation_count = self.env['stock.valuation.layer'].sudo().search_count([])
        account_move_count = self.env['account.move'].sudo().search_count([])

        first.action_check_materials()
        first_reservation = self._active_reservation(first)
        self.assertAlmostEqual(first_reservation.required_qty, 5.0)
        self.assertAlmostEqual(first_reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(first_reservation.shortage_qty, 0.0)
        self.assertEqual(first.material_reservation_state, 'full')

        second.action_check_materials()
        second_reservation = self._active_reservation(second)
        self.assertAlmostEqual(second_reservation.required_qty, 7.0)
        self.assertAlmostEqual(second_reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(second_reservation.shortage_qty, 2.0)
        self.assertEqual(second.material_reservation_state, 'partial')

        managed_lines = self._managed_purchase_lines(second)
        self.assertEqual(len(managed_lines), 1)
        purchase_line = managed_lines.ensure_one()
        ordered_in_stock_uom = purchase_line.product_uom._compute_quantity(
            purchase_line.product_qty,
            self.material.uom_id,
            round=False,
        )
        self.assertAlmostEqual(ordered_in_stock_uom, 2.0)

        first_reservation_id = first_reservation.id
        first_reserved_at = first_reservation.reserved_at
        second_reservation_id = second_reservation.id
        purchase_line_id = purchase_line.id
        first.action_check_materials()
        second.action_check_materials()

        first_reservation = self._active_reservation(first)
        second_reservation = self._active_reservation(second)
        self.assertEqual(first_reservation.id, first_reservation_id)
        self.assertEqual(first_reservation.reserved_at, first_reserved_at)
        self.assertEqual(second_reservation.id, second_reservation_id)
        self.assertAlmostEqual(first_reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(second_reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(second_reservation.shortage_qty, 2.0)
        managed_lines = self._managed_purchase_lines(second)
        self.assertEqual(managed_lines.ids, [purchase_line_id])
        self.assertAlmostEqual(
            managed_lines.product_uom._compute_quantity(
                managed_lines.product_qty,
                self.material.uom_id,
                round=False,
            ),
            2.0,
        )

        self.assertEqual(
            self.env['stock.move'].sudo().search_count([]),
            move_count,
        )
        self.assertEqual(
            self.env['stock.valuation.layer'].sudo().search_count([]),
            valuation_count,
        )
        self.assertEqual(
            self.env['account.move'].sudo().search_count([]),
            account_move_count,
        )

    def test_batch_material_check_checks_selected_orders_only(self):
        first = self._create_production(5.0)
        second = self._create_production(7.0)
        unselected = self._create_production(3.0)
        wizard = self.env[
            'furniture.mrp.material.check.batch.wizard'
        ].create({
            'company_id': self.env.company.id,
            'line_ids': [
                (0, 0, {
                    'production_id': first.id,
                    'selected': True,
                    'product_summary': first.display_name,
                }),
                (0, 0, {
                    'production_id': second.id,
                    'selected': True,
                    'product_summary': second.display_name,
                }),
                (0, 0, {
                    'production_id': unselected.id,
                    'selected': False,
                    'product_summary': unselected.display_name,
                }),
            ],
        })

        action = wizard.action_check_selected_materials()

        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(
            action['params']['next']['type'],
            'ir.actions.act_window_close',
        )
        self.assertAlmostEqual(
            self._active_reservation(first).reserved_qty,
            5.0,
        )
        second_reservation = self._active_reservation(second)
        self.assertAlmostEqual(second_reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(second_reservation.shortage_qty, 2.0)
        self.assertTrue(self._managed_purchase_lines(second))
        unselected.invalidate_recordset(['material_reservation_ids'])
        self.assertFalse(unselected.material_reservation_ids)
        self.assertFalse(self._managed_purchase_lines(unselected))

    def test_batch_material_check_default_loads_confirmed_orders(self):
        confirmed = self._create_production(1.0)
        draft = self._create_production(1.0)
        draft.write({'state': 'draft'})
        defaults = self.env[
            'furniture.mrp.material.check.batch.wizard'
        ].with_context(
            auto_load_eligible_productions=True,
            active_model='furniture.mrp.production',
            active_ids=[confirmed.id, draft.id],
        ).default_get([
            'company_id', 'select_all', 'line_ids',
        ])
        line_values = {
            command[2]['production_id']: command[2]
            for command in defaults['line_ids']
        }

        self.assertIn(confirmed.id, line_values)
        self.assertNotIn(draft.id, line_values)
        self.assertTrue(line_values[confirmed.id]['selected'])

    def test_batch_material_check_select_all_toggles_every_order(self):
        productions = self.env['furniture.mrp.production']
        for quantity in (1.0, 2.0, 3.0):
            productions |= self._create_production(quantity)
        wizard = self.env[
            'furniture.mrp.material.check.batch.wizard'
        ].create({
            'company_id': self.env.company.id,
            'line_ids': [
                (0, 0, {
                    'production_id': production.id,
                    'selected': production == productions[:1],
                    'product_summary': production.display_name,
                })
                for production in productions
            ],
        })

        wizard.select_all = True
        wizard._onchange_select_all()
        wizard._compute_selection_counts()
        self.assertTrue(all(wizard.line_ids.mapped('selected')))
        self.assertEqual(wizard.selected_count, 3)

        wizard.select_all = False
        wizard._onchange_select_all()
        wizard._compute_selection_counts()
        self.assertFalse(any(wizard.line_ids.mapped('selected')))
        self.assertEqual(wizard.selected_count, 0)

    def test_release_reallocates_to_next_order_and_clears_shortage(self):
        first = self._create_production(5.0)
        second = self._create_production(7.0)
        first.action_check_materials()
        second.action_check_materials()
        self.assertAlmostEqual(self._active_reservation(second).shortage_qty, 2.0)
        self.assertTrue(self._managed_purchase_lines(second))

        first.action_release_material_reservations()

        first.invalidate_recordset(['material_reservation_ids'])
        self.assertFalse(first.material_reservation_ids.filtered(
            lambda reservation: reservation.state == 'active'
        ))
        second_reservation = self._active_reservation(second)
        self.assertAlmostEqual(second_reservation.reserved_qty, 7.0)
        self.assertAlmostEqual(second_reservation.shortage_qty, 0.0)
        self.assertEqual(second.material_reservation_state, 'full')
        self.assertFalse(self._managed_purchase_lines(second))

    def test_first_check_reuses_row_that_appears_at_bucket_lock(self):
        """Model the second half of two simultaneous first-check requests.

        A real threaded database test would have to commit outside Odoo's
        test transaction.  Injecting the competing committed row exactly at
        the advisory-lock boundary is deterministic and proves the important
        behavior: the checker must search again after taking the lock instead
        of creating from its earlier empty cache.
        """
        production = self._create_production(5.0)
        Reservation = self.env['furniture.mrp.material.reservation']
        reservation_model = self.env.registry[
            'furniture.mrp.material.reservation'
        ]
        original_lock = reservation_model._lock_bucket
        competing_reservations = Reservation

        def _inject_competing_check(model, company, location, product):
            nonlocal competing_reservations
            result = original_lock(model, company, location, product)
            if not competing_reservations and product == self.material:
                competing_reservations = Reservation.sudo().create({
                    'production_id': production.id,
                    'company_id': company.id,
                    'source_location_id': location.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'required_qty': 1.0,
                    'reserved_at': datetime(2026, 7, 29, 7, 59),
                })
            return result

        with patch.object(
            reservation_model,
            '_lock_bucket',
            autospec=True,
            side_effect=_inject_competing_check,
        ):
            production.action_check_materials()

        reservation = self._active_reservation(production)
        self.assertEqual(reservation, competing_reservations)
        self.assertEqual(len(production.material_reservation_ids), 1)
        self.assertAlmostEqual(reservation.required_qty, 5.0)
        self.assertAlmostEqual(reservation.reserved_qty, 5.0)
        self.assertAlmostEqual(reservation.shortage_qty, 0.0)

    def test_issue_cannot_consume_quantity_promised_to_older_order(self):
        first = self._create_production(5.0)
        second = self._create_production(7.0)
        first.action_check_materials()
        second.action_check_materials()

        with self.assertRaises(UserError):
            second._check_material_issue_against_other_reservations(
                {self.material: 7.0},
                second,
            )

        self.assertTrue(second._check_material_issue_against_other_reservations(
            {self.material: 5.0},
            second,
        ))
        self.assertAlmostEqual(self._active_reservation(first).reserved_qty, 5.0)
        self.assertAlmostEqual(self._active_reservation(second).reserved_qty, 5.0)
