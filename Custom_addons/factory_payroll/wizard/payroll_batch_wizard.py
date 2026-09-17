# -*- coding: utf-8 -*-

import calendar
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class FactoryPayrollBatchWizard(models.TransientModel):
    _name = "factory.payroll.batch.wizard"
    _description = "Generate Factory Payroll Slips"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    date_from = fields.Date(
        string="من تاريخ",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(
        string="إلى تاريخ",
        required=True,
        default=lambda self: self._default_date_to(),
    )
    department_id = fields.Many2one("hr.department", string="القسم")
    employee_ids = fields.Many2many(
        "hr.employee",
        string="العمال",
        required=True,
        default=lambda self: self._default_employee_ids(),
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
    )

    @api.model
    def _default_date_to(self):
        today = fields.Date.context_today(self)
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

    @api.model
    def _default_employee_ids(self):
        return self.env["hr.employee"].search(
            [
                ("active", "=", True),
                ("company_id", "=", self.env.company.id),
            ],
            order="name, id",
        )

    @api.onchange("department_id")
    def _onchange_department_id(self):
        domain = [
            ("active", "=", True),
            ("company_id", "=", self.company_id.id),
        ]
        if self.department_id:
            domain.append(("department_id", "=", self.department_id.id))
        self.employee_ids = self.env["hr.employee"].search(domain, order="name, id")

    def action_generate(self):
        self.ensure_one()
        if not self.env.user.has_group("hr.group_hr_manager"):
            raise AccessError(_("إنشاء المرتبات الجماعي متاح لمدير الموارد البشرية فقط."))
        if self.date_from > self.date_to:
            raise ValidationError(_("تاريخ البداية يجب أن يسبق تاريخ النهاية."))
        if not self.employee_ids:
            raise ValidationError(_("اختر عاملًا واحدًا على الأقل."))

        Slip = self.env["simple.payroll.slip"]
        Contract = self.env["hr.contract"]
        generated = Slip
        skipped_without_contract = self.env["hr.employee"]
        skipped_existing = self.env["hr.employee"]
        for employee in self.employee_ids.filtered(
            lambda item: item.active and item.company_id == self.company_id
        ):
            existing = Slip.search(
                [
                    ("employee_id", "=", employee.id),
                    ("company_id", "=", self.company_id.id),
                    ("date_from", "=", self.date_from),
                    ("date_to", "=", self.date_to),
                    ("state", "!=", "cancel"),
                ],
                limit=1,
            )
            if existing:
                skipped_existing |= employee
                continue
            contract = Contract.search(
                [
                    ("employee_id", "=", employee.id),
                    ("company_id", "=", self.company_id.id),
                    ("state", "in", ["open", "close"]),
                    ("date_start", "<=", self.date_to),
                    "|",
                    ("date_end", "=", False),
                    ("date_end", ">=", self.date_from),
                ],
                order="date_start desc, id desc",
                limit=1,
            )
            if not contract:
                skipped_without_contract |= employee
                continue
            slip = Slip.create(
                {
                    "employee_id": employee.id,
                    "company_id": self.company_id.id,
                    "date_from": self.date_from,
                    "date_to": self.date_to,
                }
            )
            slip.action_recompute_factory_payroll()
            generated |= slip

        message = _(
            "تم إنشاء %(generated)s مرتب مسودة. موجود مسبقًا: %(existing)s. بدون عقد صالح: %(without_contract)s.",
            generated=len(generated),
            existing=len(skipped_existing),
            without_contract=len(skipped_without_contract),
        )
        action = self.env.ref(
            "simple_payroll_salary-2.action_simple_payroll_slip"
        ).read()[0]
        action["domain"] = [
            ("company_id", "=", self.company_id.id),
            ("date_from", "=", self.date_from),
            ("date_to", "=", self.date_to),
        ]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("إنشاء مرتبات المصنع"),
                "message": message,
                "type": "success" if generated else "warning",
                "sticky": bool(skipped_without_contract),
                "next": action,
            },
        }
