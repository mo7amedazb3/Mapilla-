# -*- coding: utf-8 -*-

import hashlib
import logging
import re
from datetime import datetime, timedelta

import pytz

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


_logger = logging.getLogger(__name__)
_SAFE_PIN_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,24}$")
_MAX_ATTLOG_LINES = 5000
_MAX_ATTLOG_LINE_LENGTH = 1024


class FactoryBiometricEvent(models.Model):
    _name = "factory.biometric.event"
    _description = "Raw Biometric Punch Event"
    _order = "punch_time desc, id desc"

    device_id = fields.Many2one(
        "factory.biometric.device",
        string="الجهاز",
        required=True,
        ondelete="restrict",
        index=True,
    )
    company_id = fields.Many2one(
        related="device_id.company_id", store=True, readonly=True, index=True
    )
    identity_id = fields.Many2one(
        "factory.biometric.identity",
        string="ربط الموظف",
        ondelete="set null",
        index=True,
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="الموظف", ondelete="set null", index=True, readonly=True
    )
    device_user_id = fields.Char(
        string="رقم العامل على الجهاز", required=True, index=True, readonly=True
    )
    punch_time = fields.Datetime(string="وقت البصمة", index=True, readonly=True)
    punch_time_local = fields.Char(string="وقت الجهاز الخام", readonly=True)
    status_code = fields.Char(string="كود الحالة", readonly=True)
    verify_mode = fields.Char(
        string="طريقة التحقق",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    work_code = fields.Char(
        string="كود العمل",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    source_stamp = fields.Char(
        string="علامة الرفع",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    digest = fields.Char(
        string="مفتاح منع التكرار",
        required=True,
        index=True,
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    raw_line = fields.Text(
        string="السطر الخام",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    received_at = fields.Datetime(
        string="وقت الاستلام", default=fields.Datetime.now, required=True, readonly=True
    )
    state = fields.Selection(
        [
            ("received", "مستلم"),
            ("pending_device", "الجهاز غير معتمد"),
            ("unmapped", "رقم عامل غير مربوط"),
            ("processed", "تمت المعالجة"),
            ("corrected", "تم تعديل الحضور يدوياً"),
            ("ignored", "محفوظ بدون تأثير"),
            ("error", "خطأ يحتاج مراجعة"),
        ],
        string="الحالة",
        default="received",
        required=True,
        index=True,
        readonly=True,
    )
    resolved_action = fields.Selection(
        [
            ("check_in", "حضور"),
            ("check_out", "انصراف"),
            ("duplicate", "بصمة مكررة"),
            ("baseline", "قبل تاريخ التشغيل"),
        ],
        string="النتيجة",
        readonly=True,
        index=True,
    )
    attendance_id = fields.Many2one(
        "hr.attendance", string="سجل الحضور", ondelete="set null", readonly=True, index=True
    )
    error_message = fields.Text(string="ملاحظة المعالجة", readonly=True)

    _sql_constraints = [
        (
            "digest_uniq",
            "unique(digest)",
            "تم استلام حركة البصمة نفسها من قبل.",
        )
    ]

    @api.model
    def _parse_device_datetime(self, device, value):
        value = (value or "").strip()
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
        if not parsed:
            raise ValueError(_("صيغة وقت الجهاز غير مفهومة: %s") % value)
        zone = pytz.timezone(device.timezone or "Africa/Cairo")
        try:
            localized = zone.localize(parsed, is_dst=None)
        except pytz.AmbiguousTimeError:
            localized = zone.localize(parsed, is_dst=False)
        except pytz.NonExistentTimeError:
            localized = zone.localize(parsed + timedelta(hours=1), is_dst=True)
        return localized.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _normalized_digest(self, device, values):
        normalized = "\x1f".join(
            [
                device.serial_number,
                values.get("device_user_id") or "",
                fields.Datetime.to_string(values.get("punch_time"))
                if values.get("punch_time")
                else values.get("punch_time_local") or "",
                values.get("status_code") or "",
                values.get("verify_mode") or "",
                values.get("work_code") or "",
            ]
        )
        return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()

    @api.model
    def _event_values_from_line(self, device, raw_line, stamp=None):
        if len(raw_line.encode("utf-8", errors="replace")) > _MAX_ATTLOG_LINE_LENGTH:
            raise ValueError(_("سطر ATTLOG أطول من الحد المسموح."))
        parts = raw_line.rstrip("\r").split("\t")
        if len(parts) < 2:
            raise ValueError(_("سطر ATTLOG لا يحتوي على رقم العامل والوقت."))
        values = {
            "device_id": device.id,
            "device_user_id": parts[0].strip(),
            "punch_time_local": parts[1].strip(),
            "punch_time": self._parse_device_datetime(device, parts[1]),
            "status_code": parts[2].strip()[:32] if len(parts) > 2 else "",
            "verify_mode": parts[3].strip()[:32] if len(parts) > 3 else "",
            "work_code": parts[4].strip()[:64] if len(parts) > 4 else "",
            "source_stamp": (stamp or "")[:128],
            "raw_line": raw_line[:4000],
        }
        if not _SAFE_PIN_RE.fullmatch(values["device_user_id"]):
            raise ValueError(_("رقم العامل في سجل البصمة غير صالح."))
        values["digest"] = self._normalized_digest(device, values)
        return values

    @api.model
    def _ingest_attlog(self, device, payload, stamp=None):
        if not self.env.su:
            raise AccessError(_("استقبال سجل الجهاز مسار داخلي فقط."))
        # ``auth='none'`` requests start without an Odoo user.  ``sudo()`` only
        # bypasses ACLs; it does not populate ``env.user``.  HR domains inspect
        # the current user's groups, so normalize the trusted PUSH ingress to a
        # real system user before parsing or processing anything.
        if not self.env.uid:
            self = self.with_user(SUPERUSER_ID)
            device = device.with_env(self.env)
        device.ensure_one()
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (82461, device.id))
        lines = [line for line in (payload or "").splitlines() if line.strip()]
        if len(lines) > _MAX_ATTLOG_LINES:
            raise ValidationError(_("عدد حركات البصمة في الطلب أكبر من الحد المسموح."))
        created = self.browse()
        for raw_line in lines:
            try:
                values = self._event_values_from_line(device, raw_line, stamp=stamp)
            except Exception as exc:
                digest = hashlib.sha256(
                    (device.serial_number + "\x1fINVALID\x1f" + raw_line).encode(
                        "utf-8", errors="replace"
                    )
                ).hexdigest()
                if not self.sudo().search_count([("digest", "=", digest)]):
                    created |= self.sudo().create(
                        {
                            "device_id": device.id,
                            "device_user_id": "?",
                            "punch_time_local": "",
                            "source_stamp": (stamp or "")[:128],
                            "digest": digest,
                            "raw_line": raw_line[:4000],
                            "state": "error",
                            "error_message": str(exc)[:2000],
                        }
                    )
                continue
            if self.sudo().search_count([("digest", "=", values["digest"])]):
                continue
            created |= self.sudo().create(values)

        for event in created.sorted(lambda rec: (rec.punch_time or datetime.min, rec.id)):
            if event.state != "received":
                continue
            try:
                with self.env.cr.savepoint():
                    event._process_one()
            except Exception as exc:
                _logger.exception("Failed to process biometric event %s", event.id)
                event.sudo().write(
                    {"state": "error", "error_message": str(exc)[:2000]}
                )
        device._update_stamp("ATTLOG", stamp)
        return len(lines)

    def _processing_write(self, values):
        return self.sudo().write(values)

    def _factory_try_late_midnight_checkout(self, identity, attendance_model):
        """Extension hook for a module that auto-closes attendance at midnight."""
        return False

    def _factory_prepare_attendance_day(self, identity, attendance_model):
        """Extension hook for closing prior shifts before interpreting a new punch."""
        return None

    def _process_one(self):
        self.ensure_one()
        if not self.punch_time:
            self._processing_write(
                {"state": "error", "error_message": _("لا يوجد وقت صالح للبصمة.")}
            )
            return
        device = self.device_id
        if device.state != "active":
            self._processing_write(
                {
                    "state": "pending_device",
                    "error_message": _("اعتمد الجهاز أولاً من شاشة أجهزة البصمة."),
                }
            )
            return
        # A newly activated device may upload its entire local history.  Apply
        # the activation boundary before identity/verify-mode lookups so those
        # old rows remain an audit-only baseline even if their users were
        # removed from the device or from Odoo.
        if self.punch_time < device.accept_events_from:
            self._processing_write(
                {
                    "state": "ignored",
                    "resolved_action": "baseline",
                    "error_message": _("الحركة أقدم من تاريخ بدء معالجة هذا الجهاز."),
                }
            )
            return
        identity = self.env["factory.biometric.identity"].sudo().search(
            [
                ("device_id", "=", device.id),
                ("device_user_id", "=", self.device_user_id),
                ("active", "=", True),
                ("employee_id.active", "=", True),
            ],
            limit=1,
        )
        if not identity:
            self._processing_write(
                {
                    "state": "unmapped",
                    "error_message": _("اربط رقم العامل %s بموظف أولاً.")
                    % self.device_user_id,
                }
            )
            return
        self._processing_write(
            {"identity_id": identity.id, "employee_id": identity.employee_id.id}
        )
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (82462, identity.employee_id.id)
        )
        allowed_verify_modes = device._allowed_verify_mode_set()
        if allowed_verify_modes and (self.verify_mode or "") not in allowed_verify_modes:
            self._processing_write(
                {
                    "state": "error",
                    "error_message": _(
                        "طريقة التحقق (%s) غير مفعلة لتسجيل الحضور على هذا الجهاز."
                    )
                    % (self.verify_mode or _("غير مسجلة")),
                }
            )
            return
        if self.punch_time > fields.Datetime.now() + timedelta(
            minutes=device.max_future_minutes
        ):
            self._processing_write(
                {
                    "state": "error",
                    "error_message": _(
                        "وقت البصمة في المستقبل؛ راجع ساعة الجهاز والمنطقة الزمنية."
                    ),
                }
            )
            return

        attendance_model = self.env["hr.attendance"].sudo().with_context(
            factory_biometric_sync=True
        )
        self._factory_prepare_attendance_day(identity, attendance_model)
        if device.debounce_seconds:
            # Strict lower bound: exactly N seconds is allowed. Only accepted
            # punches anchor the interval; ignored repeats never restart it.
            debounce_start = self.punch_time - timedelta(seconds=device.debounce_seconds)
            previous = self.sudo().search(
                [
                    ("id", "!=", self.id),
                    ("employee_id", "=", identity.employee_id.id),
                    ("state", "=", "processed"),
                    ("punch_time", "<=", self.punch_time),
                    (
                        "punch_time",
                        ">",
                        debounce_start,
                    ),
                ],
                order="punch_time desc, id desc",
                limit=1,
            )
            # Manual/corrected attendance also represents an actual transition,
            # even if no processed raw event is attached to it.
            recent_attendance = previous.attendance_id
            if not previous:
                recent_attendance = attendance_model.search(
                    [
                        ("employee_id", "=", identity.employee_id.id),
                        "|",
                        "&",
                        ("check_in", ">", debounce_start),
                        ("check_in", "<=", self.punch_time),
                        "&",
                        "&",
                        ("check_out", ">", debounce_start),
                        ("check_out", "<=", self.punch_time),
                        ("out_mode", "!=", "auto_check_out"),
                    ],
                    order="check_in desc, id desc",
                    limit=1,
                )
            if previous or recent_attendance:
                self._processing_write(
                    {
                        "state": "ignored",
                        "resolved_action": "duplicate",
                        "attendance_id": recent_attendance.id,
                        "error_message": _("تم تجاهل بصمة متقاربة خلال %s ثانية.")
                        % device.debounce_seconds,
                    }
                )
                return

        open_attendance = attendance_model.search(
            [("employee_id", "=", identity.employee_id.id), ("check_out", "=", False)],
            order="check_in desc, id desc",
            limit=1,
        )
        if open_attendance:
            if open_attendance.check_in > self.punch_time:
                self._processing_write(
                    {
                        "state": "error",
                        "error_message": _("وصلت حركة قديمة قبل بداية حضور مفتوح أحدث منها."),
                    }
                )
                return
            duration = self.punch_time - open_attendance.check_in
            if duration.total_seconds() <= 0:
                self._processing_write(
                    {
                        "state": "ignored",
                        "resolved_action": "duplicate",
                        "attendance_id": open_attendance.id,
                        "error_message": _("وقت الانصراف لا يأتي بعد وقت الحضور."),
                    }
                )
                return
            if duration.total_seconds() > device.max_open_hours * 3600:
                self._processing_write(
                    {
                        "state": "error",
                        "error_message": _(
                            "الحضور المفتوح أقدم من الحد الآمن (%s ساعة)؛ راجعه يدوياً."
                        )
                        % device.max_open_hours,
                    }
                )
                return
            open_attendance.write(
                {
                    "check_out": self.punch_time,
                    "out_mode": "biometric",
                    "biometric_out_device_id": device.id,
                }
            )
            self._processing_write(
                {
                    "state": "processed",
                    "resolved_action": "check_out",
                    "attendance_id": open_attendance.id,
                    "error_message": False,
                }
            )
            return

        overlapping = attendance_model.search(
            [
                ("employee_id", "=", identity.employee_id.id),
                ("check_in", "<=", self.punch_time),
                ("check_out", ">", self.punch_time),
            ],
            limit=1,
        )
        next_attendance = attendance_model.search(
            [
                ("employee_id", "=", identity.employee_id.id),
                ("check_in", ">", self.punch_time),
            ],
            order="check_in, id",
            limit=1,
        )
        if self._factory_try_late_midnight_checkout(identity, attendance_model):
            return

        if overlapping or next_attendance:
            self._processing_write(
                {
                    "state": "error",
                    "attendance_id": overlapping.id or next_attendance.id,
                    "error_message": _(
                        "الحركة وصلت خارج الترتيب الزمني وتتعارض مع سجل حضور موجود."
                    ),
                }
            )
            return

        attendance = attendance_model.create(
            {
                "employee_id": identity.employee_id.id,
                "check_in": self.punch_time,
                "in_mode": "biometric",
                "biometric_in_device_id": device.id,
            }
        )
        self._processing_write(
            {
                "state": "processed",
                "resolved_action": "check_in",
                "attendance_id": attendance.id,
                "error_message": False,
            }
        )

    def action_reprocess(self):
        if not self.env.su and not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        ):
            raise AccessError(_("إعادة معالجة البصمات متاحة لمدير الحضور فقط."))
        candidates = self.filtered(
            lambda event: event.state
            in ("received", "pending_device", "unmapped", "error")
        ).sorted(lambda event: (event.punch_time or datetime.min, event.id))
        for event in candidates:
            try:
                with self.env.cr.savepoint():
                    event._processing_write(
                        {
                            "state": "received",
                            "resolved_action": False,
                            "attendance_id": False,
                            "error_message": False,
                        }
                    )
                    event._process_one()
            except Exception as exc:
                _logger.exception("Failed to reprocess biometric event %s", event.id)
                event._processing_write(
                    {"state": "error", "error_message": str(exc)[:2000]}
                )
        return True

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("بيانات البصمة الخام ثابتة ولا يمكن تعديلها."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("سجل البصمة الخام لا يُحذف حفاظاً على التدقيق."))
        return super().unlink()
