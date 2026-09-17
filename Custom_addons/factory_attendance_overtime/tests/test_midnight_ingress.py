from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from .test_overtime_review import TestOvertimeReview


class TestMidnightIngress(TestOvertimeReview):
    def device(self):
        device = self.env['factory.biometric.device'].create({
            'name': 'Missed cron test', 'serial_number': 'TEST-MISSED-MIDNIGHT',
            'state': 'active', 'expected_source_ip': '127.0.0.1', 'timezone': 'Africa/Cairo',
            'debounce_seconds': 300, 'accept_events_from': self.utc('00:00:00'),
        })
        self.env['factory.biometric.identity'].create({
            'device_id': device.id, 'employee_id': self.employee.id, 'device_user_id': '9911'})
        return device

    def punch(self, device, clock, day=None):
        day = day or self.day + timedelta(days=1)
        with patch.object(fields.Datetime, 'now', return_value=self.utc(clock, day)):
            self.env['factory.biometric.event'].sudo()._ingest_attlog(
                device, f'9911\t{day} {clock}\t0\t4\t0\t0\t0')
        return device.event_ids.sorted('id')[-1]

    def test_morning_closes_yesterday_before_new_checkin(self):
        old = self.attendance()
        device = self.device()
        event = self.punch(device, '09:15:19')
        self.assertEqual(old.check_out, self.utc('00:00:00', self.day + timedelta(days=1)))
        self.assertTrue(old.factory_overtime_auto_checkout)
        self.assertEqual(event.state, 'processed')
        self.assertEqual(event.resolved_action, 'check_in')
        self.assertNotEqual(event.attendance_id, old)
        self.assertEqual(event.attendance_id.check_in, self.utc('09:15:19', self.day + timedelta(days=1)))

    def test_late_punch_without_cron_is_prior_checkout(self):
        old = self.attendance()
        device = self.device()
        event = self.punch(device, '00:00:30')
        self.assertEqual(event.resolved_action, 'check_out')
        self.assertEqual(event.attendance_id, old)
        self.assertEqual(old.factory_overtime_detected_checkout, self.utc('00:00:30', self.day + timedelta(days=1)))
        self.assertEqual(old.factory_overtime_review_state, 'pending')
        self.assertEqual(old.factory_overtime_approved_minutes, 0)

    def test_delayed_previous_day_upload_not_prematurely_closed(self):
        old = self.attendance()
        event = self.punch(self.device(), '19:00:00', self.day)
        self.assertEqual(event.attendance_id, old)
        self.assertEqual(event.resolved_action, 'check_out')
        self.assertEqual(old.check_out, self.utc('19:00:00'))
        self.assertFalse(old.factory_overtime_auto_checkout)

    def test_manual_popup_cannot_move_yesterdays_record(self):
        old = self.attendance()
        day = self.day + timedelta(days=1)
        with patch.object(fields.Datetime, 'now', return_value=self.utc('12:00:00', day)):
            popup = self.api.factory_manual_attendance_details(self.employee.id, str(day))
            self.assertFalse(popup['records'])
            self.assertFalse(popup['default_attendance_id'])
            with self.assertRaises(ValidationError):
                self.api.factory_save_manual_attendance(self.employee.id, old.id, f'{day}T09:00')
        self.assertEqual(old.check_in, self.utc('09:00:00'))

    def test_manual_badge_uses_time_source_not_historic_device(self):
        old = self.attendance()
        device = self.device()
        old.with_context(factory_biometric_sync=True).write({'in_mode': 'biometric', 'biometric_in_device_id': device.id})
        with patch.object(fields.Datetime, 'now', return_value=self.utc('12:00:00')):
            self.api.factory_save_manual_attendance(self.employee.id, old.id, f'{self.day}T09:10')
        self.assertEqual(old.in_mode, 'manual')
        self.assertFalse(old.biometric_in_device_id)
        # Historical links elsewhere must never change the meaning of the edited check-in.
        old.with_context(factory_biometric_sync=True).write({'biometric_in_device_id': device.id})
        report = self.api.factory_attendance_dashboard_data(self.department.id, 'all', str(self.day))
        row = next(r for r in report['employees'] if r['id'] == self.employee.id)
        self.assertEqual(row['source_code'], 'manual')
