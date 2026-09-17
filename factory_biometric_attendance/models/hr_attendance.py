# -*- coding: utf-8 -*-

from odoo import _, fields, models


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    in_mode = fields.Selection(
        selection_add=[("biometric", "Biometric Device")],
        ondelete={"biometric": "set default"},
    )
    out_mode = fields.Selection(
        selection_add=[("biometric", "Biometric Device")],
        ondelete={"biometric": "set default"},
    )
    biometric_in_device_id = fields.Many2one(
        "factory.biometric.device",
        string="جهاز بصمة الحضور",
        ondelete="set null",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    biometric_out_device_id = fields.Many2one(
        "factory.biometric.device",
        string="جهاز بصمة الانصراف",
        ondelete="set null",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    biometric_event_ids = fields.One2many(
        "factory.biometric.event",
        "attendance_id",
        string="حركات البصمة",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )

    def _mark_biometric_events_corrected(self, message):
        events = self.env["factory.biometric.event"].sudo().search(
            [("attendance_id", "in", self.ids)]
        )
        if events:
            events.sudo().write(
                {
                    "state": "corrected",
                    "error_message": message,
                }
            )

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get("factory_biometric_sync")
            and {"employee_id", "check_in", "check_out"}.intersection(vals)
        ):
            self._mark_biometric_events_corrected(
                _("تم تعديل سجل الحضور المرتبط يدوياً بعد استلام البصمة.")
            )
        return result

    def unlink(self):
        if not self.env.context.get("factory_biometric_sync"):
            self._mark_biometric_events_corrected(
                _("تم حذف سجل الحضور المرتبط يدوياً؛ بقيت حركة البصمة للتدقيق.")
            )
        return super().unlink()
