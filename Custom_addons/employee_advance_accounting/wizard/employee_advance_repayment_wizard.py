# -*- coding: utf-8 -*-
from odoo import fields, models


class EmployeeAdvanceRepaymentWizard(models.TransientModel):
    _name = "employee.advance.repayment.wizard"
    _description = "Register Employee Advance Repayment"

    advance_id = fields.Many2one(
        "employee.advance",
        required=True,
        readonly=True,
    )
    installment_id = fields.Many2one(
        "employee.advance.installment",
        string="القسط المحدد",
        readonly=True,
        domain="[('advance_id', '=', advance_id), ('state', '!=', 'paid')]",
    )
    installment_sequence = fields.Integer(
        string="رقم القسط",
        related="installment_id.sequence",
        readonly=True,
    )
    installment_due_date = fields.Date(
        string="تاريخ استحقاق القسط",
        related="installment_id.due_date",
        readonly=True,
    )
    installment_balance = fields.Monetary(
        string="المتبقي في القسط",
        related="installment_id.balance",
        currency_field="currency_id",
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        related="advance_id.employee_id",
        readonly=True,
    )
    outstanding_balance = fields.Monetary(
        related="advance_id.balance",
        currency_field="currency_id",
        readonly=True,
    )
    payment_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    amount = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]",
    )
    company_id = fields.Many2one(
        "res.company",
        related="advance_id.company_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="advance_id.currency_id",
        readonly=True,
    )
    notes = fields.Char()

    def action_register_repayment(self):
        self.ensure_one()
        repayment = self.env["employee.advance.repayment"].create(
            {
                "advance_id": self.advance_id.id,
                "installment_id": self.installment_id.id,
                "payment_date": self.payment_date,
                "amount": self.amount,
                "journal_id": self.journal_id.id,
                "notes": self.notes,
            }
        )
        repayment.action_post()
        return {"type": "ir.actions.act_window_close"}
