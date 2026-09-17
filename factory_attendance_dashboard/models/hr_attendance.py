# -*- coding: utf-8 -*-

from odoo import fields, models


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    factory_manual_edited_by_id = fields.Many2one(
        "res.users",
        string="آخر تعديل يدوي بواسطة",
        readonly=True,
        ondelete="set null",
        groups="hr_attendance.group_hr_attendance_manager",
    )
    factory_manual_edited_at = fields.Datetime(
        string="وقت آخر تعديل يدوي",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
