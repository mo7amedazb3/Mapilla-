# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    advance_ids = fields.One2many(
        "employee.advance",
        "employee_id",
        string="Employee Advances",
        readonly=True,
    )
    advance_count = fields.Integer(compute="_compute_advance_totals")
    outstanding_advance_balance = fields.Monetary(
        compute="_compute_advance_totals",
        currency_field="currency_id",
    )

    @api.depends("advance_ids.balance", "advance_ids.state")
    def _compute_advance_totals(self):
        for employee in self:
            active_advances = employee.advance_ids.filtered(
                lambda advance: advance.state in ("active", "closed")
            )
            employee.advance_count = len(active_advances)
            employee.outstanding_advance_balance = sum(
                active_advances.filtered(
                    lambda advance: advance.state == "active"
                ).mapped("balance")
            )

    def action_view_employee_advances(self):
        self.ensure_one()
        action = self.env.ref(
            "employee_advance_accounting.action_employee_advance"
        ).read()[0]
        action["domain"] = [("employee_id", "=", self.id)]
        action["context"] = {"default_employee_id": self.id}
        return action

