from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    factory_overtime_review_state = fields.Selection(
        [
            ("pending", "بحاجة للاعتماد"),
            ("approved", "معتمد"),
            ("rejected", "مرفوض"),
        ],
        string="حالة اعتماد الإضافي",
        default="pending",
        required=True,
        index=True,
        copy=False,
        tracking=True,
    )
    factory_overtime_detected_checkout = fields.Datetime(
        string="انصراف مكتشف بعد منتصف الليل",
        readonly=True,
        copy=False,
        index=True,
        help="البصمة الفعلية التي وصلت بعد الإغلاق الآلي عند منتصف الليل.",
    )
    factory_overtime_auto_checkout = fields.Boolean(
        string="إغلاق آلي عند منتصف الليل",
        readonly=True,
        copy=False,
        index=True,
    )
    factory_overtime_approved_minutes = fields.Float(
        string="دقائق إضافي معتمدة",
        default=0.0,
        readonly=True,
        copy=False,
    )
    factory_overtime_reviewed_by_id = fields.Many2one(
        "res.users", string="راجع الإضافي", readonly=True, copy=False, ondelete="set null"
    )
    factory_overtime_reviewed_at = fields.Datetime(
        string="وقت مراجعة الإضافي", readonly=True, copy=False
    )
    factory_overtime_review_note = fields.Char(
        string="ملاحظة مراجعة الإضافي", readonly=True, copy=False
    )

    @api.model
    def _factory_midnight_checkout_cron(self):
        """Close stale open attendances at the employee's next local midnight."""
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        open_rows = self.sudo().search(
            [("check_out", "=", False), ("check_in", "<", now)],
            order="employee_id, check_in, id",
        )
        open_rows._factory_close_at_midnight(now)
        return True

    def _factory_close_at_midnight(self, reference_time):
        """Shared by cron and punch ingress; use the punch time for delayed uploads."""
        reference_time = fields.Datetime.to_datetime(reference_time)
        for attendance in self.sorted(lambda a: (a.employee_id.id, a.id)):
            employee = attendance.employee_id
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", (82462, employee.id)
            )
            # Fence concurrent manual edits as well as biometric worker transactions.
            self.env.cr.execute("UPDATE hr_attendance SET write_date=write_date WHERE id=%s", [attendance.id])
            attendance.invalidate_recordset(["check_in", "check_out"])
            if attendance.check_out:
                continue
            try:
                zone = pytz.timezone(employee._get_tz() or "Africa/Cairo")
            except pytz.UnknownTimeZoneError:
                zone = pytz.timezone("Africa/Cairo")
            local_in = pytz.UTC.localize(attendance.check_in).astimezone(zone)
            midnight_local = zone.localize(
                datetime.combine(local_in.date() + timedelta(days=1), time.min)
            )
            midnight_utc = midnight_local.astimezone(pytz.UTC).replace(tzinfo=None)
            if midnight_utc > reference_time or midnight_utc <= attendance.check_in:
                continue
            attendance.with_context(factory_biometric_sync=True).write(
                {
                    "check_out": midnight_utc,
                    "out_mode": "auto_check_out",
                    "biometric_out_device_id": False,
                    "factory_overtime_auto_checkout": True,
                    "factory_overtime_detected_checkout": False,
                    "factory_overtime_review_state": "pending",
                    "factory_overtime_approved_minutes": 0.0,
                    "factory_overtime_reviewed_by_id": False,
                    "factory_overtime_reviewed_at": False,
                    "factory_overtime_review_note": _(
                        "تم الإغلاق آلياً عند منتصف الليل انتظاراً لبصمة الانصراف أو مراجعة الأدمن."
                    ),
                }
            )
        return True
