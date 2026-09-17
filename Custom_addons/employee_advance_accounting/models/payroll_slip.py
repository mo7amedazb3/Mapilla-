# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SimplePayrollSlip(models.Model):
    _inherit = "simple.payroll.slip"

    employee_advance_total_amount = fields.Monetary(
        compute="_compute_employee_advance_summary",
        currency_field="company_currency_id",
        string="إجمالي السلف",
    )
    employee_advance_paid_amount = fields.Monetary(
        compute="_compute_employee_advance_summary",
        currency_field="company_currency_id",
        string="المسدد",
    )
    outstanding_advance_balance = fields.Monetary(
        compute="_compute_employee_advance_summary",
        currency_field="company_currency_id",
        string="الباقي",
    )
    employee_advance_count = fields.Integer(
        compute="_compute_employee_advance_summary"
    )
    due_advance_installments = fields.Monetary(
        compute="_compute_due_advance_installments",
        currency_field="company_currency_id",
        string="Installments Due This Period",
    )

    @api.depends(
        "employee_id",
        "company_id",
        "employee_id.advance_ids.amount",
        "employee_id.advance_ids.paid_amount",
        "employee_id.advance_ids.balance",
        "employee_id.advance_ids.state",
        "employee_id.advance_ids.company_id",
    )
    def _compute_employee_advance_summary(self):
        for slip in self:
            advances = slip.employee_id.advance_ids.filtered(
                lambda advance: advance.company_id == slip.company_id
                and advance.state in ("active", "closed")
            )
            slip.employee_advance_total_amount = sum(advances.mapped("amount"))
            slip.employee_advance_paid_amount = sum(
                advances.mapped("paid_amount")
            )
            slip.outstanding_advance_balance = sum(advances.mapped("balance"))
            slip.employee_advance_count = len(advances)

    @api.depends("employee_id", "company_id", "date_from", "date_to")
    def _compute_due_advance_installments(self):
        for slip in self:
            if not slip.employee_id or not slip.date_from or not slip.date_to:
                slip.due_advance_installments = 0.0
                continue
            advances = slip.employee_id.advance_ids.filtered(
                lambda advance: advance.company_id == slip.company_id
                and advance.state == "active"
            )
            installments = self.env["employee.advance.installment"].search(
                [
                    ("advance_id", "in", advances.ids),
                    ("due_date", "<=", slip.date_to),
                    ("state", "!=", "paid"),
                ],
                order="due_date, sequence, id",
            ).filtered(
                lambda installment: installment._is_due_for_payroll_period(
                    slip.date_from, slip.date_to
                )
            )
            slip.due_advance_installments = sum(installments.mapped("balance"))

    def action_view_employee_advances(self):
        self.ensure_one()
        action = self.env.ref(
            "employee_advance_accounting.action_employee_advance"
        ).read()[0]
        action["domain"] = [("employee_id", "=", self.employee_id.id)]
        action["context"] = {"default_employee_id": self.employee_id.id}
        return action
