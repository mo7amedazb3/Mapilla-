# -*- coding: utf-8 -*-

import hmac
import ipaddress
import re
import unicodedata
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


_SAFE_SERIAL_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
_SAFE_STAMP_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_SAFE_PIN_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,24}$")
_USER_QUERY_MAX_AGE = timedelta(minutes=10)
_MAX_OPERLOG_LINE_BYTES = 4096
_MAX_DISCARDED_BIOMETRIC_TEMPLATE_BYTES = 256 * 1024


class FactoryBiometricDevice(models.Model):
    _name = "factory.biometric.device"
    _description = "Biometric Attendance Device"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"

    name = fields.Char(string="اسم الجهاز", required=True, tracking=True)
    serial_number = fields.Char(
        string="الرقم التسلسلي",
        required=True,
        index=True,
        copy=False,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="الشركة",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        [
            ("pending", "في انتظار الاعتماد"),
            ("active", "نشط"),
            ("blocked", "محظور"),
        ],
        string="الحالة",
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )
    timezone = fields.Selection(
        selection=lambda self: [(tz, tz) for tz in pytz.all_timezones],
        string="المنطقة الزمنية للجهاز",
        default="Africa/Cairo",
        required=True,
        tracking=True,
    )
    pairing_mode = fields.Selection(
        [("alternating", "أول بصمة دخول والثانية انصراف")],
        string="طريقة الحضور",
        default="alternating",
        required=True,
    )
    debounce_seconds = fields.Integer(
        string="تجاهل التكرار خلال (ثانية)",
        default=300,
        required=True,
        help=(
            "الفاصل بين حركتين محتسبتين لنفس العامل، مهما كانت طريقة التحقق. "
            "الافتراضي 300 ثانية (5 دقائق) بعد الحضور وبعد الانصراف. "
            "التكرار لا يبدأ المهلة من جديد، وتُقبل الحركة عند اكتمال المدة بالضبط."
        ),
    )
    max_open_hours = fields.Float(
        string="أقصى مدة حضور مفتوح (ساعة)",
        default=20.0,
        required=True,
        help="لا تُغلق بصمة جديدة حضوراً قديماً تجاوز هذه المدة؛ تُرسل للمراجعة بدلاً من ذلك.",
    )
    max_future_minutes = fields.Integer(
        string="السماح بتقديم ساعة الجهاز (دقيقة)",
        default=10,
        required=True,
        help="أي بصمة أبعد في المستقبل من هذا الحد تُحفظ للمراجعة ولا تنشئ حضوراً.",
    )
    allowed_verify_modes = fields.Char(
        string="أكواد طرق تسجيل الحضور المسموحة",
        default="1,4,15",
        required=True,
        help=(
            "أكواد Verify في سجل الحضور، مفصولة بفواصل: 1 لبصمة الإصبع، "
            "4 للكارت، 15 للوجه في بروتوكول ZKTeco PUSH. جميعها تسجل الحضور "
            "والانصراف لنفس الموظف بنفس الطريقة. الطرق الأخرى تُحفظ للمراجعة. "
            "تغيير الإعداد لا يعيد معالجة الحركات القديمة تلقائياً."
        ),
    )
    accept_events_from = fields.Datetime(
        string="معالجة الحركات من تاريخ",
        default=fields.Datetime.now,
        required=True,
        tracking=True,
        help="الحركات الأقدم تُحفظ للمراجعة فقط ولا تؤثر على الحضور أو الرواتب.",
    )
    expected_source_ip = fields.Char(
        string="عنوان الإنترنت العام المسموح للمصنع",
        help=(
            "عنوان الإنترنت العام للمصنع، وليس 192.168.1.201. في وضع العنوان "
            "المتغير يحتفظ النظام هنا بآخر عنوان اتصل منه الجهاز."
        ),
        tracking=True,
    )
    source_ip_mode = fields.Selection(
        [
            ("fixed", "عنوان ثابت"),
            ("dynamic", "عنوان متغير (يتحدث تلقائياً)"),
        ],
        string="نوع عنوان الإنترنت",
        default="fixed",
        required=True,
        tracking=True,
        help=(
            "استخدم العنوان المتغير عندما يغير مزود الإنترنت Public IP المصنع. "
            "يظل الجهاز معروفاً برقمه التسلسلي ويسجل النظام آخر عنوان اتصل منه."
        ),
    )
    push_comm_key = fields.Char(
        string="مفتاح اتصال Push (إن كان مفعلاً بالجهاز)",
        copy=False,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    command_encoding = fields.Selection(
        [
            ("utf-8", "UTF-8 (القياسي)"),
            ("windows-1256", "Windows-1256 (أجهزة العربي القديمة)"),
        ],
        string="ترميز أسماء العمال للجهاز",
        default="utf-8",
        required=True,
        tracking=True,
        help=(
            "يحدد ترميز النص في أوامر PUSH المرسلة للجهاز. استخدم Windows-1256 "
            "فقط مع الأجهزة القديمة التي تعرض الاسم العربي كحروف مشوهة عند إرساله UTF-8."
        ),
    )
    last_seen = fields.Datetime(string="آخر اتصال", readonly=True)
    last_source_ip = fields.Char(string="آخر عنوان اتصال", readonly=True)
    last_info = fields.Text(string="معلومات آخر اتصال", readonly=True)
    firmware_version = fields.Char(string="إصدار البرنامج", readonly=True)
    push_version = fields.Char(string="إصدار Push", readonly=True)
    platform = fields.Char(string="المنصة", readonly=True)
    attlog_stamp = fields.Char(string="علامة سجل الحضور", default="0", readonly=True)
    operlog_stamp = fields.Char(string="علامة سجل التشغيل", default="0", readonly=True)
    active = fields.Boolean(default=True)

    identity_ids = fields.One2many(
        "factory.biometric.identity", "device_id", string="الموظفون المرتبطون"
    )
    event_ids = fields.One2many(
        "factory.biometric.event", "device_id", string="حركات البصمة"
    )
    command_ids = fields.One2many(
        "factory.biometric.command", "device_id", string="أوامر الجهاز"
    )
    identity_count = fields.Integer(compute="_compute_counts", string="الموظفون")
    event_count = fields.Integer(compute="_compute_counts", string="الحركات")
    pending_event_count = fields.Integer(compute="_compute_counts", string="تحتاج مراجعة")

    _sql_constraints = [
        (
            "serial_number_uniq",
            "unique(serial_number)",
            "الرقم التسلسلي مستخدم في جهاز بصمة آخر.",
        )
    ]

    @api.depends("identity_ids", "event_ids", "event_ids.state")
    def _compute_counts(self):
        identity_data = self.env["factory.biometric.identity"]._read_group(
            [("device_id", "in", self.ids)], ["device_id"], ["__count"]
        )
        event_data = self.env["factory.biometric.event"]._read_group(
            [("device_id", "in", self.ids)], ["device_id"], ["__count"]
        )
        pending_data = self.env["factory.biometric.event"]._read_group(
            [
                ("device_id", "in", self.ids),
                ("state", "in", ["received", "pending_device", "unmapped", "error"]),
            ],
            ["device_id"],
            ["__count"],
        )
        identities = {device.id: count for device, count in identity_data}
        events = {device.id: count for device, count in event_data}
        pending = {device.id: count for device, count in pending_data}
        for device in self:
            device.identity_count = identities.get(device.id, 0)
            device.event_count = events.get(device.id, 0)
            device.pending_event_count = pending.get(device.id, 0)

    @api.constrains("serial_number")
    def _check_serial_number(self):
        for device in self:
            if not _SAFE_SERIAL_RE.fullmatch((device.serial_number or "").strip()):
                raise ValidationError(_("الرقم التسلسلي يحتوي على رموز غير مسموحة."))

    @api.constrains("debounce_seconds", "max_open_hours", "max_future_minutes")
    def _check_limits(self):
        for device in self:
            if device.debounce_seconds < 0 or device.debounce_seconds > 600:
                raise ValidationError(_("مدة تجاهل التكرار يجب أن تكون بين 0 و600 ثانية."))
            if device.max_open_hours <= 0 or device.max_open_hours > 72:
                raise ValidationError(_("أقصى مدة للحضور المفتوح يجب أن تكون بين 0 و72 ساعة."))
            if device.max_future_minutes < 0 or device.max_future_minutes > 240:
                raise ValidationError(_("سماح تقديم ساعة الجهاز يجب أن يكون بين 0 و240 دقيقة."))

    @api.constrains("expected_source_ip")
    def _check_expected_source_ip(self):
        for device in self.filtered("expected_source_ip"):
            try:
                ipaddress.ip_address(device.expected_source_ip.strip())
            except ValueError as exc:
                raise ValidationError(_("عنوان الإنترنت المسموح غير صالح.")) from exc

    @api.constrains("state", "expected_source_ip")
    def _check_active_device_has_source_ip(self):
        for device in self.filtered(lambda record: record.state == "active"):
            if not device.expected_source_ip:
                raise ValidationError(
                    _("لا يمكن تشغيل الجهاز قبل كتابة عنوان الإنترنت العام المسموح.")
                )

    @api.constrains("allowed_verify_modes")
    def _check_allowed_verify_modes(self):
        for device in self:
            values = [part.strip() for part in (device.allowed_verify_modes or "").split(",")]
            if not values or any(
                not value or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", value)
                for value in values
            ):
                raise ValidationError(_("أكواد التحقق يجب أن تكون قيماً قصيرة مفصولة بفواصل."))

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for incoming in vals_list:
            vals = dict(incoming)
            if "serial_number" in vals:
                vals["serial_number"] = (vals.get("serial_number") or "").strip()
            if "expected_source_ip" in vals:
                vals["expected_source_ip"] = (
                    vals.get("expected_source_ip") or ""
                ).strip() or False
            if "allowed_verify_modes" in vals:
                vals["allowed_verify_modes"] = ",".join(
                    part.strip()
                    for part in (vals.get("allowed_verify_modes") or "").split(",")
                    if part.strip()
                )
            normalized.append(vals)
        return super().create(normalized)

    def write(self, vals):
        vals = dict(vals)
        if "serial_number" in vals:
            vals["serial_number"] = (vals.get("serial_number") or "").strip()
        if "expected_source_ip" in vals:
            vals["expected_source_ip"] = (
                vals.get("expected_source_ip") or ""
            ).strip() or False
        if "allowed_verify_modes" in vals:
            vals["allowed_verify_modes"] = ",".join(
                part.strip()
                for part in (vals.get("allowed_verify_modes") or "").split(",")
                if part.strip()
            )
        if "command_encoding" in vals:
            pending_devices = self.filtered(
                lambda device: vals["command_encoding"] != device.command_encoding
            )
            if pending_devices and self.env["factory.biometric.command"].sudo().search_count(
                [
                    ("device_id", "in", pending_devices.ids),
                    ("state", "in", ["queued", "sent"]),
                ]
            ):
                raise ValidationError(
                    _(
                        "لا يمكن تغيير ترميز الجهاز وفيه أوامر لم تنتهِ. "
                        "انتظر رد الجهاز أو ألغِ الأوامر أولاً."
                    )
                )
        if {"serial_number", "timezone"}.intersection(vals):
            if self.env["factory.biometric.event"].sudo().search_count(
                [("device_id", "in", self.ids)]
            ):
                raise ValidationError(
                    _(
                        "لا يمكن تغيير الرقم التسلسلي أو المنطقة الزمنية بعد وجود حركات. "
                        "احظر الجهاز وأنشئ جهازاً جديداً للحفاظ على السجل التاريخي."
                    )
                )
        if vals.get("state") == "active":
            for device in self:
                expected_ip = vals.get("expected_source_ip", device.expected_source_ip)
                if not expected_ip:
                    raise ValidationError(
                        _("لا يمكن تشغيل الجهاز قبل كتابة عنوان الإنترنت العام المسموح.")
                    )
        return super().write(vals)

    @api.model
    def _register_from_push(self, serial_number, source_ip=None, metadata=None):
        serial_number = (serial_number or "").strip()
        if not _SAFE_SERIAL_RE.fullmatch(serial_number):
            raise ValidationError(_("رقم الجهاز غير صالح."))
        device = self.sudo().search([("serial_number", "=", serial_number)], limit=1)
        if not device:
            raise ValidationError(
                _("الجهاز غير مسجل. أضف الرقم التسلسلي من شاشة أجهزة البصمة أولاً.")
            )
        metadata = metadata or {}
        info = metadata.get("info") or ""
        vals = {
            "last_seen": fields.Datetime.now(),
            "last_source_ip": source_ip,
            "last_info": info[:4000],
        }
        previous_source_ip = device.expected_source_ip
        source_ip_changed = bool(
            device.source_ip_mode == "dynamic"
            and source_ip
            and source_ip != previous_source_ip
        )
        if source_ip_changed:
            vals["expected_source_ip"] = source_ip
        if metadata.get("push_version"):
            vals["push_version"] = str(metadata["push_version"])[:64]
        if metadata.get("firmware_version"):
            vals["firmware_version"] = str(metadata["firmware_version"])[:128]
        if metadata.get("platform"):
            vals["platform"] = str(metadata["platform"])[:128]
        device.sudo().write(vals)
        if source_ip_changed:
            device.sudo().message_post(
                body=_(
                    "تم تحديث عنوان الإنترنت العام للجهاز تلقائياً من %s إلى %s."
                )
                % (previous_source_ip or _("غير مسجل"), source_ip)
            )
        return device

    def _check_source_ip(self, source_ip):
        self.ensure_one()
        source_ip = (source_ip or "").strip()
        if not source_ip:
            return False
        try:
            ipaddress.ip_address(source_ip)
        except ValueError:
            return False
        if self.source_ip_mode == "dynamic":
            return True
        return bool(
            self.expected_source_ip
            and self.expected_source_ip.strip() == source_ip
        )

    def _check_push_comm_key(self, provided_key):
        self.ensure_one()
        expected = self.push_comm_key or ""
        return not expected or hmac.compare_digest(expected, provided_key or "")

    def _allowed_verify_mode_set(self):
        self.ensure_one()
        return {
            part.strip()
            for part in (self.allowed_verify_modes or "").split(",")
            if part.strip()
        }

    def _timezone_offset_hours(self):
        self.ensure_one()
        zone = pytz.timezone(self.timezone or "Africa/Cairo")
        localized = pytz.utc.localize(datetime.utcnow()).astimezone(zone)
        return int((localized.utcoffset().total_seconds() if localized.utcoffset() else 0) / 3600)

    def _build_push_options(self):
        self.ensure_one()
        serial = self.serial_number
        lines = [
            "GET OPTION FROM: %s" % serial,
            "ATTLOGStamp=%s" % (self.attlog_stamp or "0"),
            "OPERLOGStamp=%s" % (self.operlog_stamp or "0"),
            "ATTPHOTOStamp=None",
            "ErrorDelay=30",
            "Delay=10",
            "TransTimes=00:00",
            "TransInterval=1",
            "TransFlag=TransData AttLog",
            "TimeZone=%s" % self._timezone_offset_hours(),
            "Realtime=1",
            "Encrypt=0",
            "ServerVer=2.4.1",
            "PushProtVer=2.4.1",
            "PushOptionsFlag=0",
        ]
        return "\n".join(lines) + "\n"

    def _push_command_charset(self):
        self.ensure_one()
        return self.command_encoding or "utf-8"

    def _validate_push_user_name(self, name):
        self.ensure_one()
        try:
            unicodedata.normalize("NFC", name or "").encode(
                self._push_command_charset(), errors="strict"
            )
        except UnicodeEncodeError as exc:
            raise ValidationError(
                _(
                    "اسم العامل يحتوي على رمز لا يدعمه ترميز الجهاز (%s). "
                    "اكتب اسماً عربياً أو إنجليزياً بدون رموز تعبيرية."
                )
                % self._push_command_charset()
            ) from exc
        return True

    def _encode_push_command(self, command_text):
        """Encode the wire response using this terminal's legacy text codec."""
        self.ensure_one()
        text = unicodedata.normalize("NFC", str(command_text or ""))
        return text.encode(self._push_command_charset(), errors="strict")

    def action_activate(self):
        if not self.env.user.has_group("hr_attendance.group_hr_attendance_manager"):
            raise UserError(_("تشغيل جهاز البصمة متاح لمدير الحضور فقط."))
        for device in self:
            if not device.expected_source_ip:
                raise UserError(
                    _(
                        "اكتب عنوان الإنترنت العام للمصنع أولاً. هذا يمنع أي جهة على الإنترنت من تزوير بصمات الجهاز."
                    )
                )
            device.write(
                {
                    "state": "active",
                    "accept_events_from": max(
                        device.accept_events_from or fields.Datetime.now(),
                        fields.Datetime.now(),
                    ),
                }
            )
            device.message_post(body=_("تم اعتماد الجهاز وتشغيل معالجة الحضور."))
        return True

    def action_block(self):
        if not self.env.user.has_group("hr_attendance.group_hr_attendance_manager"):
            raise UserError(_("حظر جهاز البصمة متاح لمدير الحضور فقط."))
        self.write({"state": "blocked"})
        return True

    def action_reset_pending(self):
        if not self.env.user.has_group("hr_attendance.group_hr_attendance_manager"):
            raise UserError(_("تعديل حالة جهاز البصمة متاح لمدير الحضور فقط."))
        self.write({"state": "pending"})
        return True

    def action_view_identities(self):
        self.ensure_one()
        action = self.env.ref(
            "factory_biometric_attendance.action_biometric_identity"
        ).read()[0]
        action["domain"] = [("device_id", "=", self.id)]
        action["context"] = {"default_device_id": self.id}
        return action

    def action_view_events(self):
        self.ensure_one()
        action = self.env.ref(
            "factory_biometric_attendance.action_biometric_event"
        ).read()[0]
        action["domain"] = [("device_id", "=", self.id)]
        return action

    def action_reprocess_events(self):
        if not self.env.user.has_group("hr_attendance.group_hr_attendance_manager"):
            raise UserError(_("إعادة معالجة البصمات متاحة لمدير الحضور فقط."))
        for device in self:
            last_id = 0
            while True:
                events = self.env["factory.biometric.event"].sudo().search(
                    [
                        ("device_id", "=", device.id),
                        ("id", ">", last_id),
                        (
                            "state",
                            "in",
                            ["received", "pending_device", "unmapped", "error"],
                        ),
                    ],
                    order="id",
                    limit=500,
                )
                if not events:
                    break
                last_id = events[-1].id
                events.action_reprocess()
        return True

    def _update_stamp(self, table, stamp):
        self.ensure_one()
        stamp = (stamp or "").strip()
        if not stamp or not _SAFE_STAMP_RE.fullmatch(stamp):
            return
        if table == "ATTLOG":
            self.sudo().write({"attlog_stamp": stamp})
        elif table == "OPERLOG":
            self.sudo().write({"operlog_stamp": stamp})

    def _ingest_operlog(self, payload, stamp=None):
        """Consume enrollment notifications without storing biometric templates."""
        self.ensure_one()
        processed = 0
        lines = (payload or "").splitlines()
        if len(lines) > 1000:
            raise ValidationError(_("عدد أسطر تشغيل الجهاز أكبر من الحد المسموح."))
        for raw_line in lines:
            line_size = len(raw_line.encode("utf-8", errors="replace"))
            oversized_template = self._oversized_biometric_template_info(
                raw_line, line_size
            )
            if oversized_template:
                # A biometric template can legitimately exceed the ordinary
                # OPERLOG line limit.  Acknowledge it without ever persisting
                # the template payload.  Only a valid fingerprint record may
                # update fingerprint enrollment state; all other biometric
                # record types are acknowledged without changing that state.
                processed += 1
                template_type, pin, valid = oversized_template
                if template_type == "fingerprint" and valid in {"1", "3"}:
                    identity = self.env["factory.biometric.identity"].sudo().search(
                        [
                            ("device_id", "=", self.id),
                            ("device_user_id", "=", pin),
                        ],
                        limit=1,
                    )
                    if identity:
                        identity.write(
                            {
                                "enrollment_state": "enrolled",
                                "last_enrollment_at": fields.Datetime.now(),
                            }
                        )
                continue
            if line_size > _MAX_OPERLOG_LINE_BYTES:
                raise ValidationError(_("سطر تشغيل الجهاز أطول من الحد المسموح."))
            line = raw_line.strip()
            if not line:
                continue
            processed += 1
            upper = line.upper()
            pin_match = re.search(r"(?:^|[\t ])PIN=([^\t ]+)", line, re.IGNORECASE)
            if not pin_match:
                continue
            pin = pin_match.group(1).strip()
            if not _SAFE_PIN_RE.fullmatch(pin):
                continue
            is_user_record = bool(
                upper.startswith("USER ")
                or upper.startswith("USER\t")
                or self._looks_like_bare_user_record(line)
            )
            identity = self.env["factory.biometric.identity"].sudo().search(
                [("device_id", "=", self.id), ("device_user_id", "=", pin)], limit=1
            )
            values = {}
            snapshot_at = False
            snapshot_keys = {"pri", "passwd", "card", "grp", "tz", "verify"}
            roster_snapshot_keys = snapshot_keys | {"pin", "name"}
            query_command = self.env["factory.biometric.command"].browse()
            roster_response = False
            if is_user_record:
                for match in re.finditer(
                    r"(?:^|[\t ])([A-Za-z][A-Za-z0-9]*)=([^\t]*)", line
                ):
                    values[match.group(1).lower()] = match.group(2)
                snapshot_at = fields.Datetime.now()
                command_model = self.env["factory.biometric.command"].sudo()
                if identity:
                    query_command = command_model.search(
                        [
                            ("device_id", "=", self.id),
                            ("identity_id", "=", identity.id),
                            ("command_type", "=", "query_user"),
                            ("state", "in", ["sent", "done"]),
                            ("sent_at", "!=", False),
                            (
                                "sent_at",
                                ">=",
                                snapshot_at - _USER_QUERY_MAX_AGE,
                            ),
                        ],
                        order="sent_at desc, id desc",
                        limit=1,
                    )
                roster_command = (
                    command_model.search(
                        [
                            ("device_id", "=", self.id),
                            ("identity_id", "=", False),
                            ("command_type", "=", "query_roster_user"),
                            ("target_device_user_id", "=", pin),
                            ("state", "in", ["sent", "done"]),
                            ("sent_at", "!=", False),
                            (
                                "sent_at",
                                ">=",
                                snapshot_at - _USER_QUERY_MAX_AGE,
                            ),
                        ],
                        order="sent_at desc, id desc",
                        limit=1,
                    )
                )
                if roster_command and (
                    not query_command
                    or roster_command.sent_at > query_command.sent_at
                    or (
                        roster_command.sent_at == query_command.sent_at
                        and roster_command.id > query_command.id
                    )
                ):
                    roster_response = True
                    if not roster_command.roster_snapshot_at:
                        # Freeze the first USER response assigned to this query.
                        # A delayed duplicate must not replace the deletion
                        # confirmation snapshot.  Retain only the minimum safe
                        # roster result; password/card/template values are never
                        # stored in command fields.
                        roster_command.write(
                            {
                                "roster_snapshot_at": snapshot_at,
                                "roster_snapshot_name": values.get("name", "")[:128],
                                "roster_snapshot_metadata_complete": roster_snapshot_keys.issubset(
                                    values
                                ),
                                "roster_snapshot_has_password": bool(
                                    values.get("passwd")
                                ),
                            }
                        )
            if not identity:
                continue
            if re.match(r"^(?:FP|FINGERTMP)[\t ]", upper) and re.search(
                r"(?:^|[\t ])VALID=(?:1|3)(?:[\t ]|$)", line, re.IGNORECASE
            ):
                identity.write(
                    {
                        "enrollment_state": "enrolled",
                        "last_enrollment_at": fields.Datetime.now(),
                    }
                )
            elif is_user_record and not roster_response:
                snapshot_complete = bool(query_command) and snapshot_keys.issubset(
                    values
                )
                update_values = {
                    "device_snapshot_at": snapshot_at,
                    "device_snapshot_name": values.get("name", "")[:128],
                    "device_snapshot_query_command_id": query_command.id or False,
                    "device_snapshot_complete": snapshot_complete,
                    "device_snapshot_privilege": values.get("pri", "")[:8],
                    "device_snapshot_has_password": bool(values.get("passwd")),
                    "device_snapshot_card": values.get("card", "")[:32],
                    "device_snapshot_group": values.get("grp", "")[:16],
                    "device_snapshot_timezone": values.get("tz", "")[:32],
                    "device_snapshot_verify": values.get("verify", "")[:16],
                    "device_snapshot_vice_card_present": "vicecard" in values,
                    "device_snapshot_vice_card": values.get("vicecard", "")[:128],
                }
                identity.write(update_values)
        self._update_stamp("OPERLOG", stamp)
        return processed

    @api.model
    def _oversized_biometric_template_info(self, raw_line, line_size):
        """Return (type, PIN, validity) for an allowlisted oversized template.

        The full request is already bounded by the controller.  Inspect only a
        short header and require the standard metadata for the legacy OPERLOG
        record type before discarding its long sensitive value.  BIODATA has a
        distinct table/schema and is intentionally excluded.  Only FP and its
        FINGERTMP compatibility alias may change fingerprint enrollment state.
        """
        if (
            line_size <= _MAX_OPERLOG_LINE_BYTES
            or line_size > _MAX_DISCARDED_BIOMETRIC_TEMPLATE_BYTES
        ):
            return False
        header = (raw_line or "")[:1024].lstrip()
        upper_header = header.upper()
        if re.match(r"^(?:FP|FINGERTMP)[\t ]", upper_header):
            template_type = "fingerprint"
        elif re.match(r"^FACE[\t ]", upper_header):
            template_type = "face"
        elif re.match(r"^FVEIN[\t ]", upper_header):
            template_type = "finger_vein"
        elif re.match(r"^USERPIC[\t ]", upper_header):
            template_type = "user_photo"
        elif re.match(r"^BIOPHOTO[\t ]", upper_header):
            template_type = "comparison_photo"
        else:
            return False

        pin_match = re.search(
            r"(?:^|[\t ])PIN=([^\t ]+)", header, re.IGNORECASE
        )
        if not pin_match or not _SAFE_PIN_RE.fullmatch(pin_match.group(1)):
            return False

        size_match = re.search(
            r"(?:^|[\t ])SIZE=([1-9][0-9]*)(?:[\t ]|$)", header, re.IGNORECASE
        )
        if not size_match:
            return False

        if template_type in {"fingerprint", "face", "finger_vein"}:
            fid_match = re.search(
                r"(?:^|[\t ])FID=([0-9])(?:[\t ]|$)", header, re.IGNORECASE
            )
            valid_values = "0|1|3" if template_type == "fingerprint" else "0|1"
            valid_match = re.search(
                rf"(?:^|[\t ])VALID=({valid_values})(?:[\t ]|$)",
                header,
                re.IGNORECASE,
            )
            tmp_match = re.search(r"(?:^|[\t ])TMP=", upper_header)
            if not all((fid_match, valid_match, tmp_match)):
                return False
            if template_type == "finger_vein" and not re.search(
                r"(?:^|[\t ])INDEX=[0-2](?:[\t ]|$)", header, re.IGNORECASE
            ):
                return False
            valid = valid_match.group(1)
        else:
            file_name_match = re.search(
                r"(?:^|[\t ])FILENAME=([^\t]+?)(?=[\t ]+[A-Za-z][A-Za-z0-9]*=)",
                header,
                re.IGNORECASE,
            )
            content_match = re.search(r"(?:^|[\t ])CONTENT=", upper_header)
            if not all((file_name_match, content_match)):
                return False
            if template_type == "comparison_photo" and not re.search(
                r"(?:^|[\t ])TYPE=[0-9](?:[\t ]|$)", header, re.IGNORECASE
            ):
                return False
            valid = ""

        if int(size_match.group(1)) > _MAX_DISCARDED_BIOMETRIC_TEMPLATE_BYTES:
            return False
        return template_type, pin_match.group(1).strip(), valid

    @api.model
    def _looks_like_bare_user_record(self, line):
        """Accept the PIN-first USERINFO variant without confusing biometric data.

        Push 2.4 normally prefixes user uploads with ``USER``.  Some terminal
        firmware returns a queried USERINFO record starting directly with
        ``PIN=``.  Require the characteristic profile fields so FP/FINGERTMP,
        face, card and operation records can never be interpreted as a user
        snapshot.
        """
        if not re.match(r"^PIN=", line or "", re.IGNORECASE):
            return False
        keys = {
            match.group(1).lower()
            for match in re.finditer(
                r"(?:^|[\t ])([A-Za-z][A-Za-z0-9]*)=([^\t]*)", line
            )
        }
        return {"pin", "name", "pri", "passwd", "card", "grp", "tz"}.issubset(keys)

    def unlink(self):
        if any(device.event_count or device.identity_count for device in self):
            raise UserError(_("لا يمكن حذف جهاز مرتبط بموظفين أو حركات. استخدم «حظر» بدلاً من الحذف."))
        return super().unlink()
