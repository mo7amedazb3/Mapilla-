from datetime import datetime, time
import pytz
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class Attendance(models.Model):
    _inherit = 'hr.attendance'

    factory_lunch_overtime_state = fields.Selection([
        ('pending', 'بحاجة للاعتماد'), ('approved', 'معتمد'), ('rejected', 'مرفوض'),
    ], default='pending', required=True, copy=False, tracking=True)
    factory_lunch_overtime_reviewer_id = fields.Many2one('res.users', readonly=True, copy=False)
    factory_lunch_overtime_reviewed_at = fields.Datetime(readonly=True, copy=False)


class Employee(models.Model):
    _inherit = 'hr.employee'

    def _factory_lunch_overtime_eligible(self, attendance, now):
        self.ensure_one()
        if not self.factory_lunch_enabled or self.furniture_pay_basis != 'time':
            return False
        shift = self._factory_overtime_shift(attendance)
        if not shift:
            return False
        zone, day = shift['zone'], shift['day']
        def utc(hour):
            return zone.localize(datetime.combine(day, time(hour))).astimezone(pytz.UTC).replace(tzinfo=None)
        start, end = utc(13), utc(16)
        # Require actual uninterrupted attendance throughout the entire lunch window.
        if now < end or attendance.check_in > start or (attendance.check_out and attendance.check_out < end):
            return False
        if shift['start'] > start or shift['end'] < end:
            return False
        return shift

    @api.model
    def factory_overtime_approval_data(self, selected_date=False):
        data = super().factory_overtime_approval_data(selected_date)
        day = fields.Date.to_date(data['date'])
        zone, start, end = self._factory_attendance_day_bounds(day, self.env.user.tz or 'Africa/Cairo')
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id.active', '=', True), ('employee_id.company_id', '=', self.env.company.id),
            ('employee_id', 'not in', self._factory_attendance_excluded_employee_ids()),
            ('check_in', '>=', start), ('check_in', '<', end),
        ], order='employee_id,check_in,id')
        now = fields.Datetime.now()
        rows, seen = [], set()
        for a in attendances:
            e = a.employee_id
            shift = e._factory_lunch_overtime_eligible(a, now)
            if not shift or shift['day'] != day or e.id in seen:
                continue
            seen.add(e.id)
            row = self._factory_overtime_approval_row(a, shift, now)
            state = a.factory_lunch_overtime_state
            row.update(kind='lunch', state=state, is_open=False, overtime_minutes=60,
                       overtime_label=_('ساعة واحدة'), approved_minutes=60 if state == 'approved' else 0,
                       approved_label=_('ساعة واحدة') if state == 'approved' else '—',
                       source_label=_('عمل متواصل من 13:00 إلى 16:00'), source='lunch',
                       detected_checkout=self._factory_attendance_display_time(a.check_out, shift['zone']) if a.check_out else '—')
            rows.append(row)
        data['lunch_rows'] = rows
        data['counts']['lunch'] = len(rows)
        return data

    @api.model
    def factory_lunch_overtime_decide(self, attendance_id, decision, expected_version):
        self._factory_overtime_check_access()
        if type(attendance_id) is not int or decision not in ('approve', 'reject'):
            raise ValidationError(_('إضافي الغداء ساعة ثابتة تقبل الاعتماد أو الرفض فقط.'))
        a = self.env['hr.attendance'].sudo().browse(attendance_id).exists()
        if not a or not a.employee_id.active or a.employee_id.company_id != self.env.company or a.employee_id.id in self._factory_attendance_excluded_employee_ids():
            raise AccessError(_('العامل غير متاح للمراجعة في الشركة الحالية.'))
        e = a.employee_id
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s,%s)', (82462, e.id))
        self.env.cr.execute('SELECT id FROM hr_attendance WHERE id=%s FOR UPDATE', (a.id,))
        a.invalidate_recordset()
        if a.write_date.isoformat() != expected_version:
            raise ValidationError(_('بيانات الحضور تغيرت؛ حدّث القائمة.'))
        shift = e._factory_lunch_overtime_eligible(a, fields.Datetime.now())
        if not shift:
            raise ValidationError(_('العامل غير مستحق لإضافي الغداء في هذا اليوم.'))
        state = 'approved' if decision == 'approve' else 'rejected'
        with self._factory_overtime_preserve_final_payroll(e):
            a.write({'factory_lunch_overtime_state': state,
                     'factory_lunch_overtime_reviewer_id': self.env.uid,
                     'factory_lunch_overtime_reviewed_at': fields.Datetime.now()})
        a.message_post(body=_('إضافي الغداء: %s — ساعة واحدة ثابتة.') % (_('اعتماد') if state == 'approved' else _('رفض')))
        self._factory_overtime_refresh_draft_payroll(e, shift['day'])
        e.invalidate_recordset()
        return {'employee_name': e.name, 'state': state, 'approved_label': _('ساعة واحدة') if state == 'approved' else '—'}

    def _furniture_attendance_details(self, date_from, date_to):
        result = super()._furniture_attendance_details(date_from, date_to)
        self.ensure_one()
        if not result.get('lines') or self.env.context.get('factory_skip_quarter_absence'):
            return result
        start, end = self._furniture_period_utc(fields.Date.to_date(date_from), fields.Date.to_date(date_to))
        rows = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', self.id), ('check_in', '>=', start), ('check_in', '<', end),
            ('factory_lunch_overtime_state', '=', 'approved'),
        ])
        days = set()
        for a in rows:
            shift = self._factory_lunch_overtime_eligible(a, fields.Datetime.now())
            if shift:
                days.add(shift['day'])
        for line in result['lines']:
            if line['date'] in days:
                line['overtime_hours'] += 1
        result['overtime_hours'] = sum(line['overtime_hours'] for line in result['lines'])
        return result
