from datetime import timedelta

from odoo import _, fields, models


class FactoryBiometricEvent(models.Model):
    _inherit = "factory.biometric.event"

    def _factory_prepare_attendance_day(self, identity, attendance_model):
        super()._factory_prepare_attendance_day(identity, attendance_model)
        attendance_model.search([
            ("employee_id", "=", identity.employee_id.id),
            ("check_out", "=", False),
            ("check_in", "<", self.punch_time),
        ])._factory_close_at_midnight(self.punch_time)

    def _factory_try_late_midnight_checkout(self, identity, attendance_model):
        """Attach a shortly-after-midnight punch to the auto-closed prior shift."""
        self.ensure_one()
        parameter = self.env["ir.config_parameter"].sudo().get_param(
            "factory_attendance_overtime.late_punch_window_hours", "6"
        )
        try:
            window_hours = min(max(float(parameter), 1.0), 8.0)
        except (TypeError, ValueError):
            window_hours = 6.0
        lower_bound = self.punch_time - timedelta(hours=window_hours)
        attendance = attendance_model.search(
            [
                ("employee_id", "=", identity.employee_id.id),
                ("factory_overtime_auto_checkout", "=", True),
                ("check_out", "<=", self.punch_time),
                ("check_out", ">=", lower_bound),
            ],
            order="check_out desc, id desc",
            limit=1,
        )
        if not attendance:
            return False
        # A real new attendance after midnight always wins; never steal its punch.
        later_attendance = attendance_model.search_count(
            [
                ("employee_id", "=", identity.employee_id.id),
                ("id", "!=", attendance.id),
                ("check_in", ">", attendance.check_out),
                ("check_in", "<=", self.punch_time),
            ],
            limit=1,
        )
        if later_attendance:
            return False

        detected = attendance.factory_overtime_detected_checkout
        if not detected or self.punch_time < detected:
            attendance.with_context(factory_biometric_sync=True).write(
                {
                    "factory_overtime_detected_checkout": self.punch_time,
                    "factory_overtime_review_state": "pending",
                    "factory_overtime_approved_minutes": 0.0,
                    "factory_overtime_reviewed_by_id": False,
                    "factory_overtime_reviewed_at": False,
                    "factory_overtime_review_note": _(
                        "وصلت بصمة انصراف بعد منتصف الليل وتنتظر اعتماد الأدمن."
                    ),
                }
            )
            state = "processed"
            action = "check_out"
            message = _(
                "تم ربط بصمة ما بعد منتصف الليل بانصراف اليوم السابق للمراجعة."
            )
        else:
            state = "ignored"
            action = "duplicate"
            message = _("تم تجاهل بصمة لاحقة؛ تم الاحتفاظ بأول بصمة انصراف بعد منتصف الليل.")
        self._processing_write(
            {
                "state": state,
                "resolved_action": action,
                "attendance_id": attendance.id,
                "error_message": message,
            }
        )
        return True
