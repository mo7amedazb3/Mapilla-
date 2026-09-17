# Factory paid lunch recognition

Opt-in addon: install only on requested databases. No global
biometric configuration, stored punches, or calendars are edited. Deployment
refreshes informational paid hours on affected draft payslips only; monetary
amounts and confirmed/paid payroll snapshots remain untouched.

Using each employee's timezone (Africa/Cairo in this factory), a checkout at or
after 13:00 and before 16:00 is provisionally lunch. A same-day check-in strictly
before 16:00 confirms the gap as paid lunch. At 16:00 without such a return,
the original checkout remains the final departure. A later return is an ordinary
new attendance interval. Dashboard reads determine this, so no background write
or fabricated 16:00 checkout is necessary. The existing 60-second dashboard
refresh applies, and Refresh updates immediately.

The morning check-in and genuine lateness stay unchanged. An open return or
pending lunch suppresses the misleading early-departure label. The employee is
still physically checked out while away; no manufacturing presence gate is bypassed.

Confirmed lunch gaps are credited once in paid attendance hours. Actual time
away is a reporting metric only, without a fixed-hour deduction, minimum or cap.
Pending/non-returned exits receive no gap credit. Original punch intervals and
native hr.attendance.worked_hours remain unchanged; no checkout is fabricated.
Optional hooks in factory_payroll, simple_payroll_salary-2 and furniture_mrp add
the confirmed gap. Genuine lateness, absence and closed after-shift overtime keep
their existing rules. Lunch never becomes extra overtime. Confirmed/paid payroll
is not recomputed by installing this change.

Tests: install on a neutralized disposable clone with
`--test-enable --test-tags /factory_attendance_lunch --stop-after-init --no-http`.

Dashboard layout (1.2.0): show only lunch duration and its paid/pending status,
not net work or repeated interval timestamps.
Rows are grouped in the operational order priming, assembly, bases, finishing,
tailoring, sewing, painting, upholstery, packaging, then other HR departments.
Piece-paid workers form the final groups regardless of stage. Multi-stage staff
appear once at the first assigned stage; no assignment or payroll value changes.
Without assigned stages, actual HR department wins over the legacy factory
category. Group totals and filtering use the same unique employee rows.
