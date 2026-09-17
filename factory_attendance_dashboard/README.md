# Factory Attendance Dashboard

Attendance is read from `hr.attendance`. Manual corrections use
`factory_save_manual_attendance`, which records the editing user/time; linked
biometric events remain available for audit.

## Before-shift display

On a scheduled workday, a worker with no attendance is shown as `—`
(`not_started`) until that employee's calendar start in their own timezone.
At the exact start they become absent until a real attendance exists. Early
check-ins remain present; leave/rest days and historical absence are unchanged.
Before-shift rows remain in All but do not inflate the absence counter/filter.
The server supplies a next-transition delay so an open dashboard refreshes at
shift start, in addition to its normal polling. This does not write attendance,
biometric events or payroll and does not change lateness grace rules.

## Employees exempt from the dashboard

The database-local system parameter
`factory_attendance_dashboard.excluded_employee_ids` contains a JSON list of
employee record IDs (default `[]`). Only a system administrator should configure
it after resolving the intended employees in that database.

Exempt employees are excluded from both dashboard rows and aggregate counters,
including department/status filters. Their employee, user, contract, attendance
and biometric records are not deleted or archived. This is a display exemption,
not a change to payroll eligibility or biometric ingestion.

No new database columns are required. Other databases retain their existing
behavior unless their own parameter is explicitly configured.
