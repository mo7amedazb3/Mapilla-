# -*- coding: utf-8 -*-

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase


class TestFurniturePayrollAttendance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({
            'name': 'Furniture Payroll Test Company',
        })
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Furniture Payroll 10:00-20:00',
            'company_id': cls.company.id,
            'tz': 'UTC',
            'hours_per_day': 10.0,
            'attendance_ids': [
                (0, 0, {
                    'name': 'Factory shift',
                    'dayofweek': day,
                    'hour_from': 10.0,
                    'hour_to': 20.0,
                    'day_period': 'morning',
                })
                for day in ('0', '1', '2', '3', '5', '6')
            ],
        })
        cls.company.resource_calendar_id = cls.calendar
        cls.test_env = cls.env['base'].with_company(cls.company).env
        cls.payroll_journal = cls.test_env['account.journal'].create({
            'name': 'Furniture Payroll Journal',
            'code': 'FPAY',
            'type': 'general',
            'company_id': cls.company.id,
        })

        def account(name, code, account_type, reconcile=False):
            return cls.test_env['account.account'].create({
                'name': name,
                'code': code,
                'account_type': account_type,
                'reconcile': reconcile,
                'company_ids': [Command.set(cls.company.ids)],
            })

        cls.basic_salary_account = account(
            'Test Basic Salary', '990001', 'expense',
        )
        cls.allowance_account = account(
            'Test Allowance', '990002', 'expense',
        )
        cls.rewards_account = account(
            'Test Rewards', '990003', 'expense',
        )
        cls.advance_account = account(
            'Test Employee Advances', '990004', 'asset_current', True,
        )
        cls.accrued_salary_account = account(
            'Test Accrued Salary', '990005', 'liability_current',
        )
        cls.cash_account = account(
            'Test Cash', '990006', 'asset_cash',
        )
        cls.bank_account = account(
            'Test Bank', '990007', 'asset_cash',
        )
        cls.cash_journal = cls.test_env['account.journal'].create({
            'name': 'Furniture Payroll Cash',
            'code': 'FCSH',
            'type': 'cash',
            'company_id': cls.company.id,
            'default_account_id': cls.cash_account.id,
        })
        cls.bank_journal = cls.test_env['account.journal'].create({
            'name': 'Furniture Payroll Bank',
            'code': 'FBNK',
            'type': 'bank',
            'company_id': cls.company.id,
            'default_account_id': cls.bank_account.id,
        })
        cls.company.write({
            'furniture_payroll_journal_id': cls.payroll_journal.id,
            'furniture_basic_salary_account_id': cls.basic_salary_account.id,
            'furniture_allowance_account_id': cls.allowance_account.id,
            'furniture_rewards_account_id': cls.rewards_account.id,
            'furniture_salary_advance_account_id': cls.advance_account.id,
            'furniture_accrued_salary_account_id': cls.accrued_salary_account.id,
            'furniture_payroll_cash_journal_id': cls.cash_journal.id,
            'furniture_payroll_bank_journal_id': cls.bank_journal.id,
        })
        cls.employee = cls.test_env['hr.employee'].create({
            'name': 'Furniture Payroll Worker',
            'company_id': cls.company.id,
            'resource_calendar_id': cls.calendar.id,
            'furniture_pay_basis': 'time',
        })
        cls.contract = cls.test_env['hr.contract'].create({
            'name': 'Furniture Payroll Contract',
            'employee_id': cls.employee.id,
            'company_id': cls.company.id,
            'resource_calendar_id': cls.calendar.id,
            'date_start': date(2026, 8, 1),
            'wage': 9600.0,
            'state': 'open',
            'furniture_food_allowance': 100.0,
            'furniture_transport_allowance': 100.0,
            'furniture_basic_allowance': 200.0,
        })

    def _attend_week(
        self, late_minutes=0, absent_day=False, late_every_day=False,
    ):
        values = []
        for day in range(1, 7):
            if absent_day and day == 6:
                continue
            minutes = late_minutes if late_every_day or day == 1 else 0
            values.append({
                'employee_id': self.employee.id,
                'check_in': datetime(2026, 8, day, 10, minutes),
                'check_out': datetime(2026, 8, day, 20, 0),
            })
        return self.test_env['hr.attendance'].create(values)

    def _slip(self, date_from=date(2026, 8, 1), date_to=date(2026, 8, 7)):
        return self.test_env['simple.payroll.slip'].create({
            'employee_id': self.employee.id,
            'company_id': self.company.id,
            'date_from': date_from,
            'date_to': date_to,
        })

    def _single_slip_wizard(
        self, date_from=date(2026, 8, 26), date_to=date(2026, 9, 1),
    ):
        return self.test_env[
            'furniture.mrp.payroll.slip.wizard'
        ].create({
            'employee_id': self.employee.id,
            'company_id': self.company.id,
            'date_from': date_from,
            'date_to': date_to,
        })

    def test_exactly_fifteen_minutes_is_inside_grace(self):
        self._attend_week(late_minutes=15)

        slip = self._slip()

        self.assertEqual(slip.contract_id, self.contract)
        self.assertAlmostEqual(slip.expected_working_days, 6.0)
        self.assertAlmostEqual(slip.hours_per_day, 10.0)
        self.assertAlmostEqual(slip.base_wage, 2400.0)
        self.assertAlmostEqual(slip.contract_allowances, 100.0)
        self.assertAlmostEqual(slip.furniture_food_allowance_period, 25.0)
        self.assertAlmostEqual(
            slip.furniture_transport_allowance_period, 25.0,
        )
        self.assertAlmostEqual(slip.furniture_basic_allowance_period, 50.0)
        self.assertAlmostEqual(slip.furniture_actual_late_hours, 0.25)
        self.assertAlmostEqual(slip.delay_hours, 0.0)
        self.assertAlmostEqual(slip.delay_deduction, 0.0)
        self.assertAlmostEqual(slip.net_salary, 2500.0)

    def test_twenty_minutes_deducts_the_full_twenty_minutes(self):
        self._attend_week(late_minutes=20)

        slip = self._slip()

        self.assertAlmostEqual(
            slip.furniture_actual_late_hours, 1.0 / 3.0, places=3,
        )
        self.assertAlmostEqual(slip.delay_hours, 1.0 / 3.0, places=3)
        self.assertAlmostEqual(slip.delay_deduction, 13.33, places=2)
        self.assertAlmostEqual(slip.net_salary, 2486.67, places=2)

    def test_fifteen_minute_grace_is_applied_independently_every_day(self):
        self._attend_week(late_minutes=15, late_every_day=True)

        slip = self._slip()

        self.assertAlmostEqual(slip.furniture_actual_late_hours, 1.5)
        self.assertAlmostEqual(slip.delay_hours, 0.0)
        self.assertAlmostEqual(slip.delay_deduction, 0.0)
        self.assertAlmostEqual(slip.net_salary, 2500.0)

    def test_daily_overtime_uses_contract_multiplier_and_increases_net(self):
        attendances = self._attend_week()
        attendances[0].check_out = datetime(2026, 8, 1, 21, 0)
        self.contract.furniture_overtime_multiplier = 1.5

        slip = self._slip()
        slip.factory_bonus_amount = 25.0

        self.assertAlmostEqual(slip.factory_overtime_hours, 1.0)
        self.assertAlmostEqual(slip.furniture_regular_hour_rate, 40.0)
        self.assertAlmostEqual(slip.furniture_overtime_multiplier, 1.5)
        self.assertAlmostEqual(slip.furniture_overtime_hour_rate, 60.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 60.0)
        self.assertAlmostEqual(slip.commission_amount, 25.0)
        self.assertAlmostEqual(slip.furniture_total_earnings, 2585.0)
        self.assertAlmostEqual(slip.net_salary, 2585.0)

    def test_overtime_is_not_erased_by_an_absence_elsewhere_in_week(self):
        attendances = self._attend_week(absent_day=True)
        attendances[0].check_out = datetime(2026, 8, 1, 21, 0)

        slip = self._slip()

        self.assertAlmostEqual(slip.attendance_hours, 51.0)
        self.assertAlmostEqual(slip.factory_overtime_hours, 1.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 40.0)
        self.assertAlmostEqual(slip.absence_deduction, 400.0)
        self.assertAlmostEqual(slip.net_salary, 2140.0)

    def test_early_arrival_is_not_overtime(self):
        attendances = self._attend_week()
        attendances[0].check_in = datetime(2026, 8, 1, 9, 0)

        slip = self._slip()

        self.assertAlmostEqual(slip.attendance_hours, 61.0)
        self.assertAlmostEqual(slip.factory_overtime_hours, 0.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 0.0)

    def test_lateness_does_not_reduce_time_after_shift_end(self):
        attendances = self._attend_week(late_minutes=20)
        attendances[0].check_out = datetime(2026, 8, 1, 21, 0)
        self.contract.furniture_overtime_multiplier = 1.25

        slip = self._slip()

        self.assertAlmostEqual(slip.delay_hours, 1.0 / 3.0, places=3)
        self.assertAlmostEqual(slip.factory_overtime_hours, 1.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 50.0)
        self.assertAlmostEqual(slip.net_salary, 2536.67, places=2)

    def test_piece_worker_overtime_has_hours_but_no_automatic_value(self):
        attendances = self._attend_week()
        attendances[0].check_out = datetime(2026, 8, 1, 21, 0)
        self.employee.furniture_pay_basis = 'production'

        slip = self._slip()

        self.assertAlmostEqual(slip.factory_overtime_hours, 1.0)
        self.assertAlmostEqual(slip.furniture_regular_hour_rate, 0.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 0.0)

    def test_overtime_starts_only_after_twenty_hundred(self):
        attendances = self._attend_week()
        attendances[0].check_out = datetime(2026, 8, 1, 19, 0)
        attendances[1].check_out = datetime(2026, 8, 2, 20, 0)
        attendances[2].check_out = datetime(2026, 8, 3, 21, 0)

        slip = self._slip()

        self.assertAlmostEqual(slip.factory_overtime_hours, 1.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 40.0)

    def test_overtime_multiplier_cannot_be_less_than_one(self):
        with self.assertRaises(ValidationError):
            self.contract.furniture_overtime_multiplier = 0.75

    def test_grace_minutes_are_not_displayed_on_payroll_form(self):
        arch = self.test_env['simple.payroll.slip'].get_view(
            view_type='form',
        )['arch']

        self.assertNotIn('furniture_late_grace_minutes', arch)
        self.assertIn('<h5>حوافز</h5>', arch)
        self.assertNotIn('مكافآت يدوية', arch)

    def test_contract_allowances_use_full_width_card_layout(self):
        custom_arch = self.test_env.ref(
            'furniture_mrp.view_hr_contract_form_furniture_compensation'
        ).arch_db
        combined_arch = self.test_env['hr.contract'].get_view(
            view_id=self.test_env.ref(
                'hr_contract.hr_contract_view_form'
            ).id,
            view_type='form',
        )['arch']
        css = (
            Path(__file__).resolve().parents[1]
            / 'static/src/css/furniture_mrp.css'
        ).read_text(encoding='utf-8')

        self.assertIn('o_furniture_contract_compensation', custom_arch)
        self.assertIn('o_furniture_contract_allowance_panel', combined_arch)
        self.assertIn('o_furniture_contract_overtime_panel', combined_arch)
        self.assertIn('name="furniture_overtime_multiplier"', combined_arch)
        self.assertIn('o_furniture_contract_allowance_grid', combined_arch)
        self.assertEqual(
            combined_arch.count('name="furniture_food_allowance"'), 1,
        )
        self.assertEqual(
            combined_arch.count('name="furniture_transport_allowance"'), 1,
        )
        self.assertEqual(
            combined_arch.count('name="furniture_basic_allowance"'), 1,
        )
        self.assertIn(
            'grid-template-columns: repeat(4, minmax(0, 1fr));', css,
        )
        self.assertIn(
            '.o_furniture_contract_allowance_card.is-total', css,
        )
        self.assertIn('@media (max-width: 767px)', css)
        self.assertIn('o_furniture_contract_overtime_value', css)

    def test_monthly_payroll_generation_menu_is_hidden(self):
        monthly_menu = self.test_env.ref(
            'factory_payroll.menu_factory_payroll_batch_wizard'
        )
        monthly_action = self.test_env.ref(
            'factory_payroll.action_factory_payroll_batch_wizard'
        )

        self.assertFalse(monthly_menu.active)
        self.assertTrue(monthly_action.exists())

    def test_single_slip_wizard_defaults_to_trailing_seven_days(self):
        Wizard = self.test_env['furniture.mrp.payroll.slip.wizard']
        wizard = Wizard.create({'employee_id': self.employee.id})

        self.assertEqual((wizard.date_to - wizard.date_from).days, 6)
        self.assertEqual(
            Wizard._rolling_period_start(date(2026, 9, 1)),
            date(2026, 8, 26),
        )

    def test_single_slip_wizard_creates_full_paid_week_and_opens_it(self):
        action = self._single_slip_wizard().action_open_slip()
        slip = self.test_env['simple.payroll.slip'].browse(action['res_id'])

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'simple.payroll.slip')
        self.assertEqual(slip.employee_id, self.employee)
        self.assertEqual(slip.date_from, date(2026, 8, 26))
        self.assertEqual(slip.date_to, date(2026, 9, 1))
        self.assertAlmostEqual(slip.expected_working_days, 6.0)
        self.assertAlmostEqual(slip.expected_hours, 60.0)
        self.assertAlmostEqual(slip.base_wage, 2400.0)
        self.assertAlmostEqual(slip.contract_allowances, 100.0)

    def test_single_slip_wizard_prints_and_reuses_exact_period(self):
        wizard = self._single_slip_wizard()

        first_action = wizard.action_print_pdf()
        first_slip = self.test_env['simple.payroll.slip'].search([
            ('employee_id', '=', self.employee.id),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])
        second_action = wizard.action_print_pdf()
        second_slip = self.test_env['simple.payroll.slip'].search([
            ('employee_id', '=', self.employee.id),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])

        self.assertEqual(first_action['type'], 'ir.actions.report')
        self.assertEqual(
            first_action['report_name'],
            'simple_payroll_salary-2.report_simple_payroll_slip',
        )
        self.assertEqual(second_action['type'], 'ir.actions.report')
        self.assertEqual(first_slip, second_slip)
        self.assertEqual(len(second_slip), 1)

    def test_single_slip_wizard_blocks_overlapping_period(self):
        self._single_slip_wizard().action_open_slip()
        overlapping = self._single_slip_wizard(
            date_from=date(2026, 8, 27),
            date_to=date(2026, 9, 2),
        )

        with self.assertRaises(UserError):
            overlapping.action_open_slip()

    def test_single_slip_wizard_rejects_future_period(self):
        future = self._single_slip_wizard(
            date_from=date(2099, 1, 1),
            date_to=date(2099, 1, 7),
        )

        with self.assertRaises(ValidationError):
            future.action_open_slip()

    def test_current_saturday_friday_week_can_close_on_thursday(self):
        Wizard = self.test_env['furniture.mrp.payroll.slip.wizard']

        self.assertTrue(Wizard._is_current_thursday_payroll_week(
            date(2026, 8, 29), date(2026, 9, 4), date(2026, 9, 3),
        ))
        self.assertFalse(Wizard._is_current_thursday_payroll_week(
            date(2026, 8, 30), date(2026, 9, 4), date(2026, 9, 3),
        ))

    def test_single_slip_wizard_does_not_recompute_confirmed_slip(self):
        wizard = self._single_slip_wizard()
        slip = wizard._get_or_create_slip()
        slip.factory_bonus_amount = 125.0
        slip.action_confirm()
        calculated_at = datetime(2000, 1, 1)
        slip.factory_last_calculated_at = calculated_at

        action = wizard.action_print_pdf()

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(slip.state, 'confirmed')
        self.assertAlmostEqual(slip.factory_bonus_amount, 125.0)
        self.assertEqual(slip.factory_last_calculated_at, calculated_at)

    def test_single_slip_wizard_replaces_cancelled_exact_period(self):
        wizard = self._single_slip_wizard()
        cancelled = wizard._get_or_create_slip()
        cancelled.action_cancel()

        replacement = wizard._get_or_create_slip()

        self.assertNotEqual(replacement, cancelled)
        self.assertEqual(cancelled.state, 'cancel')
        self.assertEqual(replacement.state, 'draft')

    def test_attendance_payroll_menu_exposes_single_slip_wizard(self):
        payroll_menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_attendance'
        )
        slips_menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_attendance_slips'
        )
        time_slips_menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_time_slips'
        )
        production_slips_menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_production_slips'
        )
        wizard_menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_single_slip'
        )

        self.assertFalse(payroll_menu.action)
        self.assertTrue(slips_menu.active)
        self.assertFalse(time_slips_menu.active)
        self.assertFalse(production_slips_menu.active)
        self.assertEqual(slips_menu.parent_id, payroll_menu)
        self.assertEqual(wizard_menu.parent_id, payroll_menu)
        self.assertEqual(
            slips_menu.action,
            self.test_env.ref(
                'furniture_mrp.action_furniture_payroll_time_slips'
            ),
        )
        self.assertEqual(
            wizard_menu.action,
            self.test_env.ref(
                'furniture_mrp.action_furniture_mrp_payroll_slip_wizard'
            ),
        )

    def test_payroll_worker_lists_are_split_by_pay_basis(self):
        time_action = self.test_env.ref(
            'furniture_mrp.action_furniture_payroll_time_slips'
        )
        production_action = self.test_env.ref(
            'furniture_mrp.action_furniture_payroll_production_slips'
        )
        report_search = self.test_env.ref(
            'simple_payroll_salary-2.view_simple_payroll_slip_search'
        )

        self.assertEqual(
            time_action.domain,
            "[('furniture_pay_basis', '=', 'time')]",
        )
        self.assertEqual(
            production_action.domain,
            "[('furniture_pay_basis', '=', 'production')]",
        )
        self.assertEqual(
            time_action.context,
            "{'furniture_payroll_pay_basis': 'time'}",
        )
        self.assertEqual(
            production_action.context,
            "{'furniture_payroll_pay_basis': 'production'}",
        )
        self.assertEqual(time_action.search_view_id, report_search)
        self.assertEqual(production_action.search_view_id, report_search)
        self.assertEqual(time_action.view_mode, 'list,form')
        self.assertEqual(production_action.view_mode, 'list,form')

    def test_payroll_root_opens_reports_directly_without_dropdown(self):
        payroll_runs_menu = self.test_env.ref(
            'simple_payroll_salary-2.menu_simple_payroll_slips'
        )
        payroll_runs_action = self.test_env.ref(
            'simple_payroll_salary-2.action_simple_payroll_slip'
        )
        payroll_root_menu = self.test_env.ref(
            'simple_payroll_salary-2.menu_simple_payroll_root'
        )
        payroll_reports_menu = self.test_env.ref(
            'simple_payroll_salary-2.menu_simple_payroll_summary'
        )
        payroll_reports_action = self.test_env.ref(
            'simple_payroll_salary-2.action_simple_payroll_reports'
        )

        self.assertFalse(payroll_runs_menu.active)
        self.assertTrue(payroll_runs_action.exists())
        self.assertFalse(payroll_reports_menu.active)
        self.assertEqual(payroll_root_menu.action, payroll_reports_action)
        self.assertFalse(payroll_root_menu.child_id.filtered('active'))
        self.assertTrue(payroll_reports_action.exists())

    def test_payroll_report_opens_as_exportable_detailed_table(self):
        action = self.test_env.ref(
            'simple_payroll_salary-2.action_simple_payroll_reports'
        )
        report_list = self.test_env.ref(
            'furniture_mrp.view_simple_payroll_report_list_furniture'
        )
        report_search = self.test_env.ref(
            'furniture_mrp.view_simple_payroll_report_search_furniture'
        )
        list_arch = report_list.arch_db
        search_arch = report_search.arch_db

        self.assertEqual(action.view_mode, 'list,pivot,graph,form')
        self.assertEqual(action.mobile_view_mode, 'list')
        self.assertEqual(action.view_id, report_list)
        self.assertEqual(action.search_view_id, report_search)
        self.assertEqual(action.views[0], (report_list.id, 'list'))
        self.assertIn('o_furniture_payroll_report_list', list_arch)
        self.assertIn('export_xlsx="1"', list_arch)
        self.assertIn('name="furniture_department_id"', list_arch)
        self.assertIn('name="furniture_total_earnings"', list_arch)
        self.assertIn('name="furniture_total_deductions"', list_arch)
        self.assertIn('name="factory_overtime_hours"', list_arch)
        self.assertIn('name="furniture_overtime_multiplier"', list_arch)
        self.assertIn('name="furniture_overtime_amount"', list_arch)
        self.assertIn('name="employee_advance_total_amount"', list_arch)
        self.assertIn('name="employee_advance_paid_amount"', list_arch)
        self.assertIn('name="outstanding_advance_balance"', list_arch)
        self.assertNotIn('name="due_advance_installments"', list_arch)
        self.assertIn('name="net_salary"', list_arch)
        self.assertIn('sum="إجمالي صافي المستحق"', list_arch)
        self.assertIn('string="حوافز"', list_arch)
        self.assertIn('sum="إجمالي الحوافز"', list_arch)
        self.assertNotIn('مكافآت يدوية', list_arch)
        report_document = etree.fromstring(list_arch.encode())
        money_field_names = (
            'base_wage',
            'contract_allowances',
            'factory_bonus_amount',
            'furniture_regular_hour_rate',
            'furniture_overtime_hour_rate',
            'furniture_overtime_amount',
            'furniture_total_earnings',
            'absence_deduction',
            'delay_deduction',
            'factory_other_deduction_amount',
            'furniture_total_deductions',
            'employee_advance_total_amount',
            'employee_advance_paid_amount',
            'outstanding_advance_balance',
            'net_salary',
        )
        for field_name in money_field_names:
            nodes = report_document.xpath(
                f".//field[@name='{field_name}']"
            )
            self.assertEqual(len(nodes), 1, field_name)
            self.assertEqual(
                nodes[0].get('widget'),
                'furniture_payroll_rounded_monetary',
                field_name,
            )
            self.assertEqual(nodes[0].get('digits'), '[16, 0]', field_name)
        for field_node in report_document.xpath(
            "./field[not(@column_invisible='1')]"
        ):
            self.assertIn(
                field_node.get('optional'),
                ('show', 'hide'),
                field_node.get('name'),
            )
        self.assertIn("'group_by': 'furniture_department_id'", search_arch)
        self.assertIn("'group_by': 'date_from:week'", search_arch)
        self.assertIn("'group_by': 'date_from:month'", search_arch)
        self.assertIn("('factory_overtime_hours', '&gt;', 0)", search_arch)
        self.assertTrue(
            self.test_env['simple.payroll.slip']._fields[
                'furniture_department_id'
            ].store
        )
        self.assertTrue(
            self.test_env['simple.payroll.slip']._fields[
                'furniture_total_earnings'
            ].store
        )
        self.assertTrue(
            self.test_env['simple.payroll.slip']._fields[
                'furniture_total_deductions'
            ].store
        )
        self.assertTrue(
            self.test_env['simple.payroll.slip']._fields[
                'furniture_overtime_amount'
            ].store
        )

        overtime_pivot = self.test_env.ref(
            'furniture_mrp.view_simple_payroll_slip_pivot_furniture_overtime'
        ).arch_db
        overtime_graph = self.test_env.ref(
            'furniture_mrp.view_simple_payroll_slip_graph_furniture_overtime'
        ).arch_db
        self.assertIn('name="factory_overtime_hours"', overtime_pivot)
        self.assertIn('name="furniture_overtime_amount"', overtime_pivot)
        self.assertIn('name="furniture_overtime_amount"', overtime_graph)

        slip = self._slip()
        self.assertAlmostEqual(
            slip.furniture_total_earnings,
            slip.base_wage
            + slip.contract_allowances
            + slip.commission_amount
            + slip.furniture_overtime_amount,
        )
        self.assertAlmostEqual(
            slip.furniture_total_deductions,
            slip.attendance_deduction
            + slip.factory_other_deduction_amount,
        )

    def test_payroll_pdf_uses_green_earnings_and_red_deductions(self):
        template = self.test_env.ref(
            'furniture_mrp.report_simple_payroll_slip_furniture'
        ).arch_db
        report = self.test_env.ref(
            'simple_payroll_salary-2.action_report_simple_payroll_slip'
        )

        self.assertEqual(
            report.paperformat_id,
            self.test_env.ref('furniture_mrp.paperformat_furniture_payroll_slip'),
        )
        self.assertIn('furniture_mrp.furniture_payroll_bare_layout', template)
        self.assertIn('o_furniture_payroll_attendance_kpis', template)
        self.assertIn('o_furniture_payroll_earning', template)
        self.assertIn('o_furniture_payroll_deduction', template)
        self.assertIn('gross_earnings', template)
        self.assertIn('total_deductions', template)
        self.assertIn('o_furniture_payroll_earnings_section', template)
        self.assertIn('o_furniture_payroll_deductions_section', template)
        self.assertIn('#eef8f2', template)
        self.assertIn('#fff8f7', template)
        self.assertIn('#16834f', template)
        self.assertIn('#c5342c', template)
        self.assertEqual(
            template.count('class="o_furniture_payroll_earning"'), 6,
        )
        self.assertEqual(
            template.count('class="o_furniture_payroll_deduction"'), 3,
        )
        self.assertIn('o_furniture_payroll_total_earnings', template)
        self.assertIn('o_furniture_payroll_total_deductions', template)
        self.assertIn('o_furniture_payroll_net', template)
        self.assertNotIn('o_furniture_payroll_totals', template)
        self.assertIn('t-if="o.furniture_food_allowance_period"', template)
        self.assertIn('t-if="o.furniture_transport_allowance_period"', template)
        self.assertIn('t-if="o.furniture_basic_allowance_period"', template)
        self.assertIn('t-if="o.factory_bonus_amount"', template)
        self.assertIn('>حوافز</td>', template)
        self.assertNotIn('مكافآت يدوية', template)
        self.assertIn('t-if="o.factory_overtime_hours"', template)
        self.assertIn('o.furniture_overtime_amount', template)
        self.assertIn('t-if="o.absence_deduction"', template)
        self.assertIn('t-if="o.delay_deduction"', template)
        self.assertIn('t-if="o.factory_other_deduction_amount"', template)
        self.assertIn('t-if="not total_deductions"', template)
        self.assertIn('o_furniture_payroll_no_deductions', template)
        self.assertIn('المبلغ النهائي المستحق للعامل', template)

    def test_payroll_whatsapp_wizard_requires_employee_phone(self):
        slip = self._slip()

        action = slip.action_open_payroll_whatsapp_wizard()
        wizard = self.test_env[action['res_model']].browse(action['res_id'])

        self.assertEqual(wizard.slip_count, 1)
        self.assertEqual(wizard.employee_count, 1)
        self.assertEqual(wizard.missing_phone_count, 1)
        self.assertIn(self.employee.name, wizard.missing_phone_names)
        self.assertFalse(wizard.can_send)

    def test_payroll_whatsapp_summarizes_mixed_periods(self):
        first = self._slip()
        second = self._slip(date(2026, 8, 8), date(2026, 8, 14))

        action = (first | second).action_open_payroll_whatsapp_wizard()
        wizard = self.test_env[action['res_model']].browse(action['res_id'])

        self.assertEqual(wizard.slip_count, 2)
        self.assertEqual(wizard.employee_count, 1)
        self.assertEqual(wizard.period_count, 2)

    def test_payroll_whatsapp_sends_one_private_pdf(self):
        self.employee.mobile_phone = '01012345678'
        slip = self._slip()
        self.test_env['whatsapp.instance'].create({
            'name': 'Payroll WhatsApp Test',
            'instance_id': 'payroll-whatsapp-test',
            'status': 'ready',
        })
        action = slip.action_open_payroll_whatsapp_wizard()
        wizard = self.test_env[action['res_model']].browse(action['res_id'])
        report_model = self.test_env.registry['ir.actions.report']
        session_model = self.test_env.registry['whatsapp.session']

        with (
            patch.object(
                report_model,
                '_render_qweb_pdf',
                autospec=True,
                return_value=(b'%PDF-1.4 payroll', 'pdf'),
            ) as render_mock,
            patch.object(
                session_model,
                'action_send_reply',
                autospec=True,
                return_value=True,
            ) as send_mock,
        ):
            result = wizard.with_context(
                furniture_payroll_whatsapp_request_id='payroll-test-request'
            ).action_send()

        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(render_mock.call_count, 1)
        self.assertEqual(send_mock.call_count, 1)
        sent_session = send_mock.call_args.args[0]
        self.assertEqual(sent_session.mobile, '201012345678')
        self.assertEqual(
            sent_session.env.context['ctx_media_type'], 'application/pdf'
        )
        self.assertTrue(
            sent_session.env.context['ctx_media_name'].endswith('.pdf')
        )

    def test_absence_and_lateness_are_both_shown_and_deducted(self):
        self._attend_week(late_minutes=20, absent_day=True)

        slip = self._slip()

        self.assertAlmostEqual(slip.absence_days, 1.0)
        self.assertAlmostEqual(slip.absence_deduction, 400.0)
        self.assertAlmostEqual(slip.delay_deduction, 13.33, places=2)
        self.assertAlmostEqual(slip.attendance_deduction, 413.33, places=2)
        self.assertAlmostEqual(slip.net_salary, 2086.67, places=2)

    def test_friday_is_not_work_or_absence(self):
        slip = self._slip(
            date_from=date(2026, 8, 7),
            date_to=date(2026, 8, 7),
        )

        self.assertAlmostEqual(slip.expected_working_days, 0.0)
        self.assertAlmostEqual(slip.base_wage, 0.0)
        self.assertAlmostEqual(slip.contract_allowances, 0.0)
        self.assertAlmostEqual(slip.absence_days, 0.0)
        self.assertAlmostEqual(slip.attendance_deduction, 0.0)
        self.assertAlmostEqual(slip.factory_overtime_hours, 0.0)
        self.assertAlmostEqual(slip.furniture_overtime_amount, 0.0)
        self.assertAlmostEqual(slip.net_salary, 0.0)

    def test_employee_pay_report_exposes_weekly_and_daily_overtime(self):
        attendances = self._attend_week()
        attendances[0].check_out = datetime(2026, 8, 1, 21, 0)
        self.contract.furniture_overtime_multiplier = 1.25
        wizard = self.test_env[
            'furniture.mrp.employee.pay.report'
        ].create({
            'employee_id': self.employee.id,
            'week_start': date(2026, 8, 1),
            'month_start': date(2026, 8, 1),
            'month_end': date(2026, 8, 7),
        })

        wizard._refresh_report()

        self.assertAlmostEqual(wizard.week_overtime_hours, 1.0)
        self.assertAlmostEqual(wizard.attendance_overtime_hours, 1.0)
        self.assertAlmostEqual(wizard.overtime_multiplier, 1.25)
        self.assertAlmostEqual(wizard.regular_hour_rate, 40.0)
        self.assertAlmostEqual(wizard.overtime_hour_rate, 50.0)
        self.assertAlmostEqual(wizard.overtime_amount, 50.0)
        self.assertAlmostEqual(wizard.weekly_total_with_overtime, 2450.0)
        self.assertAlmostEqual(
            sum(wizard.attendance_line_ids.mapped('overtime_hours')), 1.0,
        )

    def test_full_week_is_six_days_even_when_contract_started_midweek(self):
        self.contract.date_start = date(2026, 8, 3)

        slip = self._slip()

        self.assertEqual(slip.contract_id, self.contract)
        self.assertAlmostEqual(slip.expected_working_days, 6.0)
        self.assertAlmostEqual(slip.expected_hours, 60.0)
        self.assertAlmostEqual(slip.base_wage, 2400.0)
        self.assertAlmostEqual(slip.contract_allowances, 100.0)

    def _bulk_payroll_worker(
        self, name, *, company=None, active=True, with_contract=True,
    ):
        company = company or self.company
        employee = self.test_env['hr.employee'].create({
            'name': name,
            'company_id': company.id,
            'resource_calendar_id': (
                self.calendar.id if company == self.company else False
            ),
            'furniture_pay_basis': 'time',
            'active': active,
        })
        contract = self.test_env['hr.contract']
        if with_contract:
            contract = contract.create({
                'name': '%s Contract' % name,
                'employee_id': employee.id,
                'company_id': company.id,
                'resource_calendar_id': (
                    self.calendar.id if company == self.company else False
                ),
                'date_start': date(2026, 1, 1),
                'wage': 4800.0,
                'state': 'open',
            })
        return employee, contract

    def _bulk_payroll_wizard(
        self, date_from=date(2026, 8, 26), date_to=date(2026, 9, 1),
    ):
        return self.test_env[
            'furniture.mrp.payroll.batch.wizard'
        ].create({
            'company_id': self.company.id,
            'date_from': date_from,
            'date_to': date_to,
        })

    def test_bulk_payroll_defaults_to_all_active_company_workers(self):
        second, second_contract = self._bulk_payroll_worker(
            'Second Bulk Payroll Worker',
        )
        inactive, _contract = self._bulk_payroll_worker(
            'Inactive Bulk Payroll Worker', active=False,
        )
        other_company = self.test_env['res.company'].create({
            'name': 'Other Bulk Payroll Company',
        })
        other_company_worker, _contract = self._bulk_payroll_worker(
            'Other Company Bulk Payroll Worker',
            company=other_company,
            with_contract=False,
        )

        wizard = self.test_env[
            'furniture.mrp.payroll.batch.wizard'
        ].create({'company_id': self.company.id})
        lines_by_employee = {
            line.employee_id: line for line in wizard.line_ids
        }

        self.assertEqual((wizard.date_to - wizard.date_from).days, 6)
        self.assertEqual(
            set(lines_by_employee), {self.employee, second},
        )
        self.assertNotIn(inactive, lines_by_employee)
        self.assertNotIn(other_company_worker, lines_by_employee)
        self.assertTrue(all(wizard.line_ids.mapped('selected')))
        self.assertEqual(
            lines_by_employee[self.employee].contract_id, self.contract,
        )
        self.assertEqual(
            lines_by_employee[second].contract_id, second_contract,
        )
        self.assertEqual(
            lines_by_employee[self.employee].department_id,
            self.employee.department_id,
        )
        self.assertEqual(
            lines_by_employee[self.employee].pay_basis,
            self.employee.furniture_pay_basis,
        )
        self.assertFalse(wizard.slip_ids)

        # The full-page wizard starts as an unsaved record. Odoo's web client
        # drops readonly values from nested create commands unless force_save
        # is set, which would otherwise create 48 lines without employee_id.
        view_arch = self.test_env.ref(
            'furniture_mrp.view_furniture_mrp_payroll_batch_wizard_form'
        ).arch_db
        view_document = etree.fromstring(view_arch.encode())
        employee_fields = view_document.xpath(
            ".//field[@name='line_ids']/list/field[@name='employee_id']"
        )
        self.assertEqual(len(employee_fields), 1)
        self.assertEqual(employee_fields[0].get('readonly'), '1')
        self.assertEqual(employee_fields[0].get('force_save'), '1')

    def test_bulk_payroll_web_save_keeps_readonly_employee_ids(self):
        second, _contract = self._bulk_payroll_worker(
            'Web Save Bulk Payroll Worker',
        )

        wizard_form = Form(
            self.test_env['furniture.mrp.payroll.batch.wizard'],
            view=(
                'furniture_mrp.'
                'view_furniture_mrp_payroll_batch_wizard_form'
            ),
        )
        wizard_form.date_from = date(2026, 8, 26)
        wizard_form.date_to = date(2026, 9, 1)
        self.assertEqual(len(wizard_form.line_ids), 2)

        wizard = wizard_form.save()

        self.assertEqual(len(wizard.line_ids), 2)
        self.assertEqual(
            set(wizard.line_ids.mapped('employee_id')),
            {self.employee, second},
        )

    def test_bulk_payroll_creates_and_reuses_exact_period_slips(self):
        second, _contract = self._bulk_payroll_worker(
            'Reusable Bulk Payroll Worker',
        )
        first_wizard = self._bulk_payroll_wizard()

        first_action = first_wizard.action_generate_and_open()
        first_slips = self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | second).ids),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])

        self.assertEqual(first_action['type'], 'ir.actions.act_window')
        self.assertEqual(first_action['res_model'], 'simple.payroll.slip')
        self.assertEqual(len(first_slips), 2)
        self.assertEqual(set(first_wizard.slip_ids.ids), set(first_slips.ids))
        self.assertEqual(set(first_slips.mapped('state')), {'draft'})
        self.assertTrue(all(
            days == 6.0
            for days in first_slips.mapped('expected_working_days')
        ))

        locked = first_slips.filtered(
            lambda slip: slip.employee_id == self.employee
        )
        locked.factory_bonus_amount = 125.0
        locked.action_confirm()
        locked.factory_last_calculated_at = datetime(2000, 1, 1)

        second_wizard = self._bulk_payroll_wizard()
        second_wizard.action_generate_and_open()
        second_slips = self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | second).ids),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])

        self.assertEqual(len(second_slips), 2)
        self.assertEqual(set(first_slips.ids), set(second_slips.ids))
        self.assertEqual(
            set(second_wizard.slip_ids.ids), set(second_slips.ids),
        )
        self.assertEqual(locked.state, 'confirmed')
        self.assertAlmostEqual(locked.factory_bonus_amount, 125.0)
        self.assertEqual(
            locked.factory_last_calculated_at, datetime(2000, 1, 1),
        )

    def test_payroll_list_period_action_generates_all_workers(self):
        second, _contract = self._bulk_payroll_worker(
            'List Period Bulk Payroll Worker',
        )

        action = self.test_env[
            'furniture.mrp.payroll.batch.wizard'
        ].action_generate_period_slips('2026-08-26', '2026-09-01')
        slips = self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | second).ids),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])

        self.assertEqual(len(slips), 2)
        self.assertEqual(action['res_model'], 'simple.payroll.slip')
        self.assertEqual(action['domain'], [('id', 'in', slips.ids)])
        self.assertEqual(
            action['context']['furniture_payroll_date_from'],
            '2026-08-26',
        )
        self.assertEqual(
            action['context']['furniture_payroll_date_to'],
            '2026-09-01',
        )
        self.assertFalse(action['context']['create'])

    def test_payroll_list_period_action_preserves_pay_basis_filter(self):
        production_worker, _contract = self._bulk_payroll_worker(
            'Production Payroll Worker',
        )
        production_worker.furniture_pay_basis = 'production'

        action = self.test_env[
            'furniture.mrp.payroll.batch.wizard'
        ].with_context(
            furniture_payroll_pay_basis='production',
        ).action_generate_period_slips('2026-08-26', '2026-09-01')
        generated_slips = self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | production_worker).ids),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ])

        self.assertEqual(len(generated_slips), 2)
        self.assertEqual(
            action['domain'],
            [
                ('id', 'in', generated_slips.ids),
                ('furniture_pay_basis', '=', 'production'),
            ],
        )
        self.assertEqual(
            action['context']['furniture_payroll_pay_basis'],
            'production',
        )
        self.assertEqual(action['name'], 'كشوف أجور عمال الإنتاج')

    def test_direct_period_action_skips_active_worker_without_contract(self):
        worker = self.test_env['hr.employee'].create({
            'name': 'Payroll Placeholder Without Contract',
            'company_id': self.company.id,
            'resource_calendar_id': self.calendar.id,
        })

        action = self.test_env[
            'furniture.mrp.payroll.batch.wizard'
        ].action_generate_period_slips('2026-08-26', '2026-09-01')

        self.assertFalse(self.test_env['simple.payroll.slip'].search([
            ('employee_id', '=', worker.id),
        ]))
        self.assertEqual(action['res_model'], 'simple.payroll.slip')

    def test_apply_payroll_never_registers_advance_repayment(self):
        self._attend_week()
        slip = self._slip()
        slip.factory_bonus_amount = 100.0
        advance = self.test_env['employee.advance'].create({
            'employee_id': self.employee.id,
            'company_id': self.company.id,
            'advance_date': date(2026, 7, 1),
            'amount': 300.0,
            'reason': 'Payroll accrual test',
            'payment_journal_id': self.payroll_journal.id,
            'advance_account_id': self.advance_account.id,
            'installment_count': 1,
            'first_installment_date': date(2026, 8, 3),
            'state': 'active',
        })
        installment = self.test_env['employee.advance.installment'].create({
            'advance_id': advance.id,
            'sequence': 1,
            'due_date': date(2026, 8, 3),
            'amount': 300.0,
        })
        self.company.furniture_salary_advance_account_id = False
        Wizard = self.test_env['furniture.mrp.payroll.batch.wizard']

        first_action = Wizard.action_apply_period_accounting(
            '2026-08-01', '2026-08-07',
        )
        move = self.test_env['account.move'].browse(first_action['res_id'])
        repayment = self.test_env['employee.advance.repayment'].search([
            ('advance_id', '=', advance.id),
            ('move_id', '=', move.id),
        ])

        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.furniture_payroll_date_from, date(2026, 8, 1))
        self.assertEqual(move.furniture_payroll_date_to, date(2026, 8, 7))
        self.assertEqual(slip.state, 'confirmed')
        self.assertEqual(slip.furniture_payroll_move_id, move)
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(
                lambda line: line.account_id == self.basic_salary_account
            ).mapped('debit')),
            2400.0,
        )
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(
                lambda line: line.account_id == self.allowance_account
            ).mapped('debit')),
            100.0,
        )
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(
                lambda line: line.account_id == self.rewards_account
            ).mapped('debit')),
            100.0,
        )
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(
                lambda line: line.account_id == self.advance_account
            ).mapped('credit')),
            0.0,
        )
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(
                lambda line: line.account_id == self.accrued_salary_account
            ).mapped('credit')),
            2600.0,
        )
        self.assertFalse(repayment)
        self.assertAlmostEqual(installment.balance, 300.0)
        self.assertEqual(installment.state, 'pending')
        self.assertAlmostEqual(advance.balance, 300.0)
        self.assertEqual(advance.state, 'active')

        second_action = Wizard.action_apply_period_accounting(
            '2026-08-01', '2026-08-07',
        )

        self.assertEqual(second_action['res_id'], move.id)
        self.assertEqual(self.test_env['account.move'].search_count([
            ('furniture_payroll_date_from', '=', date(2026, 8, 1)),
            ('furniture_payroll_date_to', '=', date(2026, 8, 7)),
            ('company_id', '=', self.company.id),
        ]), 1)
        self.assertEqual(self.test_env['employee.advance.repayment'].search_count([
            ('advance_id', '=', advance.id),
            ('move_id', '=', move.id),
        ]), 0)

        payment_action = Wizard.action_open_period_payment(
            '2026-08-01', '2026-08-07',
        )
        payment_wizard = self.test_env[
            'furniture.mrp.payroll.payment.wizard'
        ].browse(payment_action['res_id'])
        self.assertAlmostEqual(payment_wizard.total_amount, 2600.0)
        self.assertAlmostEqual(payment_wizard.cash_amount, 2600.0)
        self.assertAlmostEqual(payment_wizard.bank_amount, 0.0)
        self.assertAlmostEqual(payment_wizard.difference_amount, 0.0)

        payment_wizard.write({
            'cash_amount': 1000.0,
            'bank_amount': 1600.0,
        })
        payment_move_action = payment_wizard.action_post_payment()
        payment_move = self.test_env['account.move'].browse(
            payment_move_action['res_id']
        )
        self.assertEqual(payment_move.state, 'posted')
        self.assertEqual(payment_move.furniture_payroll_accrual_move_id, move)
        self.assertAlmostEqual(
            sum(payment_move.line_ids.filtered(
                lambda line: line.account_id == self.accrued_salary_account
            ).mapped('debit')),
            2600.0,
        )
        self.assertAlmostEqual(
            sum(payment_move.line_ids.filtered(
                lambda line: line.account_id == self.cash_account
            ).mapped('credit')),
            1000.0,
        )
        self.assertAlmostEqual(
            sum(payment_move.line_ids.filtered(
                lambda line: line.account_id == self.bank_account
            ).mapped('credit')),
            1600.0,
        )
        self.assertEqual(slip.state, 'paid')
        self.assertEqual(slip.furniture_payroll_payment_move_id, payment_move)

        repeated_payment_action = Wizard.action_open_period_payment(
            '2026-08-01', '2026-08-07',
        )
        self.assertEqual(repeated_payment_action['res_id'], payment_move.id)
        self.assertEqual(self.test_env['account.move'].search_count([
            ('furniture_payroll_accrual_move_id', '=', move.id),
        ]), 1)

    def test_payroll_lists_default_to_saturday_friday_period_controls(self):
        base_list = self.test_env['simple.payroll.slip'].get_view(
            view_id=self.test_env.ref(
                'simple_payroll_salary-2.view_simple_payroll_slip_list'
            ).id,
            view_type='list',
        )['arch']
        report_list = self.test_env.ref(
            'furniture_mrp.view_simple_payroll_report_list_furniture'
        ).arch_db
        base_document = etree.fromstring(base_list.encode())
        report_document = etree.fromstring(report_list.encode())
        module_root = Path(__file__).resolve().parents[1]
        javascript = (
            module_root / 'static/src/js/payroll_period_list.js'
        ).read_text(encoding='utf-8')
        template = (
            module_root / 'static/src/xml/payroll_period_list.xml'
        ).read_text(encoding='utf-8')
        css = (
            module_root / 'static/src/css/furniture_mrp.css'
        ).read_text(encoding='utf-8')

        self.assertEqual(
            base_document.get('js_class'),
            'furniture_payroll_period_list',
        )
        self.assertFalse(base_document.xpath(".//field[@name='name']"))
        total_advance_fields = base_document.xpath(
            ".//field[@name='employee_advance_total_amount']"
        )
        paid_advance_fields = base_document.xpath(
            ".//field[@name='employee_advance_paid_amount']"
        )
        remaining_advance_fields = base_document.xpath(
            ".//field[@name='outstanding_advance_balance']"
        )
        due_advance_fields = base_document.xpath(
            ".//field[@name='due_advance_installments']"
        )
        self.assertEqual(len(total_advance_fields), 1)
        self.assertEqual(total_advance_fields[0].get("string"), "إجمالي السلف")
        self.assertEqual(len(paid_advance_fields), 1)
        self.assertEqual(paid_advance_fields[0].get("string"), "المسدد")
        self.assertEqual(len(remaining_advance_fields), 1)
        self.assertEqual(remaining_advance_fields[0].get("string"), "الباقي")
        self.assertFalse(due_advance_fields)
        self.assertFalse(
            base_document.xpath(
                ".//field[@name='factory_net_after_due_advance']"
            )
        )
        self.assertFalse(base_document.xpath(".//field[@name='date_from']"))
        self.assertFalse(base_document.xpath(".//field[@name='date_to']"))
        self.assertIn(
            'outstanding_advance_balance',
            self.test_env['simple.payroll.slip']._fields,
        )
        self.assertIn(
            'employee_advance_total_amount',
            self.test_env['simple.payroll.slip']._fields,
        )
        self.assertIn(
            'employee_advance_paid_amount',
            self.test_env['simple.payroll.slip']._fields,
        )
        self.assertIn(
            'due_advance_installments',
            self.test_env['simple.payroll.slip']._fields,
        )
        self.assertEqual(
            report_document.get('js_class'),
            'furniture_payroll_period_list',
        )
        self.assertEqual(
            len(report_document.xpath(
                ".//field[@name='outstanding_advance_balance']"
            )),
            1,
        )
        self.assertEqual(
            len(report_document.xpath(
                ".//field[@name='employee_advance_total_amount']"
            )),
            1,
        )
        self.assertEqual(
            len(report_document.xpath(
                ".//field[@name='employee_advance_paid_amount']"
            )),
            1,
        )
        self.assertFalse(
            report_document.xpath(
                ".//field[@name='due_advance_installments']"
            )
        )
        for document in (base_document, report_document):
            for field_node in document.xpath(
                "./field[not(@column_invisible='1')]"
            ):
                self.assertIn(
                    field_node.get('optional'),
                    ('show', 'hide'),
                    field_node.get('name'),
                )
        self.assertFalse(report_document.xpath(".//field[@name='date_from']"))
        self.assertFalse(report_document.xpath(".//field[@name='date_to']"))
        self.assertIn(
            'const daysSinceSaturday = (today.weekday + 1) % 7;',
            javascript,
        )
        self.assertIn('weekStart.plus({ days: 6 })', javascript)
        self.assertNotIn('today.minus({ days: 6 })', javascript)
        self.assertIn('today.weekday === 4', javascript)
        self.assertIn('context.furniture_payroll_date_to || allowedDateTo', javascript)
        self.assertIn('dateTo <= this.period.allowedDateTo', javascript)
        self.assertIn('action_generate_period_slips', javascript)
        self.assertIn('action_apply_period_accounting', javascript)
        self.assertIn('action_open_period_payment', javascript)
        self.assertIn('replaceCurrentAction', javascript)
        self.assertIn('PAYROLL_PAY_BASIS_ACTIONS', javascript)
        self.assertIn('onPayrollPayBasisChange', javascript)
        self.assertIn('furniture_payroll_pay_basis', javascript)
        self.assertIn('additionalContext', javascript)
        self.assertIn('extends MonetaryField', javascript)
        self.assertIn('return [16, 0]', javascript)
        self.assertIn('furniture_payroll_rounded_monetary', javascript)
        self.assertEqual(template.count('type="date"'), 2)
        self.assertEqual(template.count('t-on-change="onPayrollPeriodChange"'), 2)
        self.assertEqual(template.count('role="tab"'), 2)
        self.assertIn('عمال بالوقت', template)
        self.assertIn('عمال بالإنتاج', template)
        self.assertIn("onPayrollPayBasisChange('time')", template)
        self.assertIn("onPayrollPayBasisChange('production')", template)
        self.assertIn('t-att-max="period.allowedDateTo"', template)
        self.assertIn("'تطبيق'", template)
        self.assertIn("'سداد الأجور'", template)
        self.assertIn('title="فتح شاشة توزيع سداد أجور الفترة"', template)
        self.assertNotIn('تطبيق الفترة', template)
        self.assertIn('o_furniture_payroll_period_controls', css)
        self.assertIn(
            '.o_control_panel:has(.o_furniture_payroll_period_controls) '
            '.o_control_panel_breadcrumbs',
            css,
        )
        self.assertIn('"payroll-title payroll-search payroll-navigation"', css)
        self.assertIn('"payroll-period payroll-period payroll-period"', css)
        self.assertIn('grid-area: payroll-search;', css)
        self.assertIn('grid-area: payroll-period;', css)
        self.assertIn('.o_furniture_payroll_payment {', css)
        self.assertIn('.o_furniture_payroll_basis_tabs {', css)
        self.assertIn('.o_furniture_payroll_basis_tab.active {', css)
        self.assertIn('min-width: 118px;', css)
        self.assertIn('white-space: nowrap;', css)
        self.assertIn('font-size: 1rem !important;', css)
        self.assertIn('@media (min-width: 768px) and (max-width: 991px)', css)

    def test_bulk_payroll_overlap_is_atomic_for_every_worker(self):
        second, _contract = self._bulk_payroll_worker(
            'Atomic Overlap Bulk Payroll Worker',
        )
        self.test_env['simple.payroll.slip'].create({
            'employee_id': self.employee.id,
            'company_id': self.company.id,
            'date_from': date(2026, 8, 27),
            'date_to': date(2026, 9, 1),
        })
        wizard = self._bulk_payroll_wizard()

        with self.assertRaises(UserError):
            wizard.action_generate_and_open()

        self.assertFalse(self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | second).ids),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ]))
        self.assertFalse(wizard.slip_ids)

    def test_bulk_payroll_missing_contract_is_atomic(self):
        missing_contract_worker, _contract = self._bulk_payroll_worker(
            'Missing Contract Bulk Payroll Worker', with_contract=False,
        )
        wizard = self._bulk_payroll_wizard()
        self.assertIn(
            missing_contract_worker, wizard.line_ids.mapped('employee_id'),
        )

        with self.assertRaises(UserError):
            wizard.action_generate_and_open()

        self.assertFalse(self.test_env['simple.payroll.slip'].search([
            ('employee_id', 'in', (self.employee | missing_contract_worker).ids),
            ('company_id', '=', self.company.id),
            ('date_from', '=', date(2026, 8, 26)),
            ('date_to', '=', date(2026, 9, 1)),
            ('state', '!=', 'cancel'),
        ]))
        self.assertFalse(wizard.slip_ids)

    def test_bulk_payroll_report_is_one_multi_worker_table(self):
        second, _contract = self._bulk_payroll_worker(
            'Printable Bulk Payroll Worker',
        )
        wizard = self._bulk_payroll_wizard()

        action = wizard.action_generate_and_print()
        report = self.test_env.ref(
            'furniture_mrp.action_report_furniture_payroll_batch'
        )
        menu = self.test_env.ref(
            'furniture_mrp.menu_furniture_payroll_batch_slip'
        )
        template = self.test_env.ref(report.report_name)
        document = etree.fromstring(template.arch_db.encode())
        pages = document.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' page ')]"
        )

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'], report.report_name)
        self.assertEqual(
            report.model, 'furniture.mrp.payroll.batch.wizard',
        )
        self.assertEqual(len(wizard.slip_ids), 2)
        self.assertEqual(
            set(wizard.slip_ids.mapped('employee_id')),
            {self.employee, second},
        )
        self.assertEqual(report.paperformat_id.orientation, 'Landscape')
        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0].xpath(
            "ancestor::*[@t-foreach and contains(@t-foreach, 'slips')]"
        ))
        row_loops = pages[0].xpath(
            ".//*[@t-foreach and contains(@t-foreach, 'slips')]"
        )
        self.assertTrue(row_loops)
        self.assertTrue(any(
            node.tag == 'tr' or node.xpath('.//tr') for node in row_loops
        ))
        self.assertIn('الحوافز', template.arch_db)
        self.assertNotIn('المكافآت', template.arch_db)
        self.assertTrue(all(not node.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' page ')]"
        ) for node in row_loops))
        self.assertIn('table-header-group', template.arch_db)
        self.assertIn('العامل', template.arch_db)
        self.assertIn('صافي المستحق', template.arch_db)
        self.assertEqual(
            menu.parent_id,
            self.test_env.ref(
                'furniture_mrp.menu_furniture_payroll_attendance'
            ),
        )
        self.assertEqual(
            menu.action.res_model,
            'furniture.mrp.payroll.batch.wizard',
        )

        pdf, output_type = self.test_env[
            'ir.actions.report'
        ].with_context(force_report_rendering=True)._render_qweb_pdf(
            report.id, res_ids=wizard.ids,
        )
        self.assertEqual(output_type, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 5000)
