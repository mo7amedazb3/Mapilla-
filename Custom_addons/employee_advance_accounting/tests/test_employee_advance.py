# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
import re

from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.modules.module import get_module_resource
from odoo.tests.common import TransactionCase


class TestEmployeeAdvance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.test_env = cls.env["base"].with_company(cls.company).env
        cls.cash_account = cls.test_env["account.account"].create(
            {
                "name": "Employee Advance Test Cash",
                "code": "991101",
                "account_type": "asset_cash",
                "company_ids": [Command.set(cls.company.ids)],
            }
        )
        cls.advance_account = cls.test_env["account.account"].create(
            {
                "name": "Employee Advance Test Receivable",
                "code": "991102",
                "account_type": "asset_current",
                "reconcile": True,
                "company_ids": [Command.set(cls.company.ids)],
            }
        )
        cls.cash_journal = cls.test_env["account.journal"].create(
            {
                "name": "Employee Advance Test Cash Journal",
                "code": "EATC",
                "type": "cash",
                "company_id": cls.company.id,
                "default_account_id": cls.cash_account.id,
            }
        )
        cls.partner = cls.test_env["res.partner"].create(
            {"name": "Employee Advance Test Worker"}
        )
        cls.employee = cls.test_env["hr.employee"].create(
            {
                "name": "Employee Advance Test Worker",
                "company_id": cls.company.id,
                "work_contact_id": cls.partner.id,
            }
        )

    def _advance_values(self):
        return {
            "employee_id": self.employee.id,
            "company_id": self.company.id,
            "advance_date": date(2026, 9, 1),
            "amount": 1000.0,
            "reason": "Installment plan test",
            "payment_journal_id": self.cash_journal.id,
            "advance_account_id": self.advance_account.id,
            "first_installment_date": date(2026, 9, 8),
        }

    def test_weekly_pay_creates_an_installment_every_week(self):
        values = self._advance_values()
        values.update(
            {
                "installment_count": 4,
                "repayment_frequency": "weekly",
            }
        )
        advance = self.test_env["employee.advance"].create(values)

        self.assertEqual(advance.installment_count, 4)
        self.assertAlmostEqual(advance.installment_amount, 250.0)
        advance.action_disburse()

        self.assertEqual(advance.state, "active")
        self.assertEqual(advance.move_id.state, "posted")
        self.assertEqual(
            advance.installment_ids.mapped("due_date"),
            [
                date(2026, 9, 8),
                date(2026, 9, 15),
                date(2026, 9, 22),
                date(2026, 9, 29),
            ],
        )
        self.assertEqual(
            advance.installment_ids.mapped("amount"),
            [250.0, 250.0, 250.0, 250.0],
        )
        self.assertAlmostEqual(sum(advance.installment_ids.mapped("amount")), 1000.0)
        first_installment = advance.installment_ids.sorted(
            lambda installment: installment.sequence
        )[0]
        self.assertFalse(
            first_installment._is_due_for_payroll_period(
                date(2026, 8, 29), date(2026, 9, 4)
            )
        )
        self.assertTrue(
            first_installment._is_due_for_payroll_period(
                date(2026, 9, 5), date(2026, 9, 11)
            )
        )
        second_installment = advance.installment_ids.sorted(
            lambda installment: installment.sequence
        )[1]
        self.assertFalse(
            second_installment._is_due_for_payroll_period(
                date(2026, 9, 5), date(2026, 9, 11)
            )
        )
        self.assertTrue(
            second_installment._is_due_for_payroll_period(
                date(2026, 9, 12), date(2026, 9, 18)
            )
        )

    def test_weekly_repayment_defaults_first_installment_to_next_week(self):
        advance = self.test_env["employee.advance"].new(
            {
                "advance_date": date(2026, 9, 1),
                "repayment_frequency": "weekly",
            }
        )

        advance._onchange_first_installment_date()

        self.assertEqual(advance.first_installment_date, date(2026, 9, 8))

    def test_draft_advance_builds_and_refreshes_visible_payment_plan(self):
        values = self._advance_values()
        values.update(
            {
                "installment_count": 4,
                "repayment_frequency": "weekly",
            }
        )

        advance = self.test_env["employee.advance"].create(values)

        self.assertEqual(advance.state, "draft")
        self.assertEqual(
            advance.installment_ids.mapped("due_date"),
            [
                date(2026, 9, 8),
                date(2026, 9, 15),
                date(2026, 9, 22),
                date(2026, 9, 29),
            ],
        )
        self.assertEqual(advance.installment_ids.mapped("amount"), [250.0] * 4)

        advance.write(
            {
                "installment_count": 2,
                "first_installment_date": date(2026, 10, 1),
            }
        )

        self.assertEqual(
            advance.installment_ids.mapped("due_date"),
            [date(2026, 10, 1), date(2026, 10, 8)],
        )
        self.assertEqual(advance.installment_ids.mapped("amount"), [500.0, 500.0])

    def test_repayment_frequency_uses_payment_labels(self):
        field = self.test_env["employee.advance"].fields_get(
            ["repayment_frequency"]
        )["repayment_frequency"]

        self.assertEqual(field["string"], "طريقة السداد")
        self.assertEqual(dict(field["selection"])["weekly"], "أسبوعي")

    def test_entered_installment_count_sets_the_payment_amount(self):
        values = self._advance_values()
        values["installment_count"] = 4

        advance = self.test_env["employee.advance"].create(values)

        self.assertEqual(advance.installment_count, 4)
        self.assertAlmostEqual(advance.installment_amount, 250.0)

    def test_cash_is_the_default_disbursement_journal(self):
        values = self._advance_values()
        values.pop("payment_journal_id")

        advance = self.test_env["employee.advance"].create(values)

        self.assertEqual(advance.payment_journal_id.type, "cash")

    def test_accountant_posts_repayment_manually(self):
        values = self._advance_values()
        values.update(
            {
                "installment_count": 4,
                "repayment_frequency": "weekly",
            }
        )
        advance = self.test_env["employee.advance"].create(values)
        advance.action_disburse()

        repayment = self.test_env["employee.advance.repayment"].create(
            {
                "advance_id": advance.id,
                "payment_date": date(2026, 9, 8),
                "amount": 250.0,
                "journal_id": self.cash_journal.id,
            }
        )
        repayment.action_post()

        self.assertEqual(repayment.state, "posted")
        self.assertEqual(repayment.move_id.state, "posted")
        self.assertAlmostEqual(
            sum(
                repayment.move_id.line_ids.filtered(
                    lambda line: line.account_id == self.cash_account
                ).mapped("debit")
            ),
            250.0,
        )
        self.assertAlmostEqual(
            sum(
                repayment.move_id.line_ids.filtered(
                    lambda line: line.account_id == self.advance_account
                ).mapped("credit")
            ),
            250.0,
        )
        self.assertAlmostEqual(advance.paid_amount, 250.0)
        self.assertAlmostEqual(advance.balance, 750.0)
        first_installment = advance.installment_ids.sorted(
            lambda installment: installment.sequence
        )[0]
        self.assertEqual(first_installment.state, "paid")
        self.assertAlmostEqual(first_installment.paid_amount, 250.0)

    def test_payroll_slip_shows_total_paid_and_remaining_advances(self):
        first_values = self._advance_values()
        first_values.update({"amount": 1000.0, "installment_count": 4})
        first_advance = self.test_env["employee.advance"].create(first_values)
        first_advance.action_disburse()
        self.test_env["employee.advance.repayment"].create(
            {
                "advance_id": first_advance.id,
                "payment_date": date(2026, 9, 8),
                "amount": 250.0,
                "journal_id": self.cash_journal.id,
            }
        ).action_post()

        closed_values = self._advance_values()
        closed_values.update({"amount": 400.0, "installment_count": 1})
        closed_advance = self.test_env["employee.advance"].create(closed_values)
        closed_advance.action_disburse()
        self.test_env["employee.advance.repayment"].create(
            {
                "advance_id": closed_advance.id,
                "payment_date": date(2026, 9, 8),
                "amount": 400.0,
                "journal_id": self.cash_journal.id,
            }
        ).action_post()

        draft_values = self._advance_values()
        draft_values.update({"amount": 900.0, "installment_count": 3})
        self.test_env["employee.advance"].create(draft_values)

        slip = self.test_env["simple.payroll.slip"].create(
            {
                "employee_id": self.employee.id,
                "company_id": self.company.id,
                "date_from": date(2026, 9, 5),
                "date_to": date(2026, 9, 11),
            }
        )

        self.assertEqual(closed_advance.state, "closed")
        self.assertAlmostEqual(slip.employee_advance_total_amount, 1400.0)
        self.assertAlmostEqual(slip.employee_advance_paid_amount, 650.0)
        self.assertAlmostEqual(slip.outstanding_advance_balance, 750.0)
        self.assertEqual(slip.employee_advance_count, 2)

    def test_installment_pay_button_targets_the_selected_installment(self):
        values = self._advance_values()
        values.update(
            {
                "installment_count": 4,
                "repayment_frequency": "weekly",
            }
        )
        advance = self.test_env["employee.advance"].create(values)
        advance.action_disburse()
        installments = advance.installment_ids.sorted(
            lambda installment: installment.sequence
        )
        first_installment = installments[0]
        selected_installment = installments[1]

        action = selected_installment.action_open_repayment_wizard()
        wizard = self.test_env["employee.advance.repayment.wizard"].with_context(
            **action["context"]
        ).create(
            {
                "payment_date": date(2026, 9, 15),
                "journal_id": self.cash_journal.id,
            }
        )

        self.assertEqual(wizard.installment_id, selected_installment)
        self.assertAlmostEqual(wizard.amount, 250.0)
        wizard.action_register_repayment()

        repayment = self.test_env["employee.advance.repayment"].search(
            [("advance_id", "=", advance.id)], limit=1
        )
        self.assertEqual(repayment.installment_id, selected_installment)
        self.assertEqual(repayment.state, "posted")
        self.assertAlmostEqual(first_installment.paid_amount, 0.0)
        self.assertEqual(first_installment.state, "pending")
        self.assertAlmostEqual(selected_installment.paid_amount, 250.0)
        self.assertEqual(selected_installment.state, "paid")

    def test_selected_installment_cannot_be_overpaid(self):
        values = self._advance_values()
        values.update(
            {
                "installment_count": 4,
                "repayment_frequency": "weekly",
            }
        )
        advance = self.test_env["employee.advance"].create(values)
        advance.action_disburse()
        selected_installment = advance.installment_ids.sorted(
            lambda installment: installment.sequence
        )[1]
        repayment = self.test_env["employee.advance.repayment"].create(
            {
                "advance_id": advance.id,
                "installment_id": selected_installment.id,
                "payment_date": date(2026, 9, 15),
                "amount": 300.0,
                "journal_id": self.cash_journal.id,
            }
        )

        with self.assertRaises(ValidationError):
            repayment.action_post()

        self.assertEqual(repayment.state, "draft")
        self.assertFalse(repayment.move_id)
        self.assertAlmostEqual(selected_installment.paid_amount, 0.0)

    def test_list_exposes_approval_and_outstanding_balance(self):
        view = self.test_env.ref(
            "employee_advance_accounting.view_employee_advance_list"
        )
        # The action is deliberately restricted to accounting users, so the
        # compiled architecture can prune it for the test runner user.  Assert
        # the module's declared list architecture instead.
        architecture = view.arch_db

        self.assertIn('name="action_disburse"', architecture)
        self.assertIn('name="balance"', architecture)
        self.assertIn('name="installment_amount"', architecture)
        self.assertIn('name="repayment_frequency"', architecture)

        form_architecture = self.test_env.ref(
            "employee_advance_accounting.view_employee_advance_form"
        ).arch_db
        self.assertIn("Payment Plan — خطة السداد", form_architecture)
        self.assertIn('name="installment_ids" readonly="1"', form_architecture)
        self.assertIn('name="action_open_repayment_wizard"', form_architecture)
        self.assertIn("اضغط «دفع» أمام القسط المطلوب", form_architecture)
        # Odoo 18's ListRenderer aggregates every monetary row through
        # currency_id[0].  Embedded readonly x2many rows can be partially
        # loaded while a line is opened, so aggregate footers can crash Owl.
        payment_plan_architecture = form_architecture.split(
            'class="o_employee_advance_payment_plan ', 1
        )[1].split("<notebook>", 1)[0]
        self.assertNotIn(" sum=", payment_plan_architecture)

    def test_advance_list_totals_have_currency_data(self):
        model = self.test_env["employee.advance"]
        view = self.test_env.ref(
            "employee_advance_accounting.view_employee_advance_list"
        )
        architecture = etree.fromstring(
            model.get_view(view_id=view.id, view_type="list")["arch"].encode()
        )
        list_fields = {
            node.get("name"): node for node in architecture.findall("field")
        }
        for name in ("amount", "paid_amount", "balance", "installment_amount"):
            with self.subTest(field=name):
                self.assertTrue(list_fields[name].get("sum"))
                # ListRenderer displays a dash unless the monetary field's
                # currency is explicitly loaded alongside every list row.
                currency_field = model._fields[name].currency_field
                self.assertIn(currency_field, list_fields)
                self.assertEqual(
                    list_fields[currency_field].get("column_invisible"), "True"
                )
                self.assertIsNone(list_fields[currency_field].get("optional"))

    def test_advance_forms_scope_visual_effects_for_stable_many2one_menu(self):
        for xml_id, model in (
            (
                "employee_advance_accounting.view_employee_advance_form",
                "employee.advance",
            ),
            (
                "employee_advance_accounting.view_employee_advance_repayment_form",
                "employee.advance.repayment",
            ),
        ):
            view = self.test_env.ref(xml_id)
            architecture = self.test_env[model].get_view(
                view_id=view.id,
                view_type="form",
            )["arch"]
            self.assertIn(
                'class="o_employee_advance_accounting_form"',
                architecture,
            )

        css_path = get_module_resource(
            "employee_advance_accounting",
            "static",
            "src",
            "css",
            "style.scss",
        )
        css = Path(css_path).read_text(encoding="utf-8")

        self.assertIn(
            ".o_form_view.o_employee_advance_accounting_form",
            css,
        )
        self.assertIn(".o_employee_advance_installment_pay", css)
        self.assertNotRegex(
            css,
            r"(?m)^\.o_form_view\s*\{\s*animation:",
        )

        fade_keyframes = re.search(
            r"@keyframes employeeAdvanceFadeIn\s*\{(.*?)\n\}",
            css,
            re.S,
        )
        self.assertIsNotNone(fade_keyframes)
        self.assertNotIn("transform", fade_keyframes.group(1))

        card_hover = re.search(
            r"\.bg-calm-light:hover\s*\{(.*?)\n\}",
            css,
            re.S,
        )
        self.assertIsNotNone(card_hover)
        self.assertNotIn("transform", card_hover.group(1))
