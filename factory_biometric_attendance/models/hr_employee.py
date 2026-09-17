# -*- coding: utf-8 -*-

from odoo import _, fields, models
from odoo.exceptions import AccessError


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    biometric_identity_ids = fields.One2many(
        "factory.biometric.identity",
        "employee_id",
        string="أرقام أجهزة البصمة",
        groups="hr_attendance.group_hr_attendance_manager",
    )
    biometric_identity_count = fields.Integer(
        compute="_compute_biometric_identity_count",
        compute_sudo=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )

    def _compute_biometric_identity_count(self):
        grouped = self.env["factory.biometric.identity"]._read_group(
            [("employee_id", "in", self.ids)], ["employee_id"], ["__count"]
        )
        counts = {employee.id: count for employee, count in grouped}
        for employee in self:
            employee.biometric_identity_count = counts.get(employee.id, 0)

    def action_view_biometric_identities(self):
        if not self.env.user.has_group("hr_attendance.group_hr_attendance_manager"):
            raise AccessError(_("بيانات ربط البصمة متاحة لمدير الحضور فقط."))
        self.ensure_one()
        action = self.env.ref(
            "factory_biometric_attendance.action_biometric_identity"
        ).read()[0]
        action["domain"] = [("employee_id", "=", self.id)]
        action["context"] = {"default_employee_id": self.id}
        return action

    def unlink(self):
        if not self.env.context.get("module_uninstall"):
            identities = (
                self.env["factory.biometric.identity"]
                .with_context(active_test=False)
                .search([("employee_id", "in", self.ids)])
            )
            if identities:
                # Identity unlink queues the durable device deletion and purges
                # biometric history before core employee deletion runs.
                identities.unlink()
            residual_events = self.env["factory.biometric.event"].sudo().search(
                [("employee_id", "in", self.ids)]
            )
            if residual_events:
                residual_events.unlink()
        return super().unlink()
