# -*- coding: utf-8 -*-

import re
from datetime import timedelta
from urllib.parse import parse_qs

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


_COMMAND_RETRY_SECONDS = 30
_COMMAND_MAX_ATTEMPTS = 10
_ROSTER_DELETE_REVALIDATION_FAILED = -997
_DESTRUCTIVE_COMMAND_ACK_TIMEOUT = -996
_UNSAFE_COMMAND_ID_FAILED = -995
_NAME_REPAIR_SNAPSHOT_MAX_AGE = timedelta(minutes=10)
_ROSTER_DELETE_SNAPSHOT_MAX_AGE = timedelta(minutes=10)
_SAFE_PIN_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,24}$")
_SAFE_COMMAND_ID_RE = re.compile(r"^[0-9]{1,64}$")
_NON_REPLAYABLE_COMMAND_TYPES = {"delete_user", "delete_roster_user"}
_PIN1_MUTATING_COMMAND_TYPES = {
    "user_update",
    "user_name_repair",
    "enroll_fingerprint",
    "delete_user",
    "delete_roster_user",
}
_ROSTER_RESULT_FIELDS = {
    "roster_snapshot_at",
    "roster_snapshot_name",
    "roster_snapshot_metadata_complete",
    "roster_snapshot_has_password",
}
_IMMUTABLE_COMMAND_FIELDS = {
    "command_id",
    "device_id",
    "identity_id",
    "target_device_user_id",
    "expected_roster_name",
    "expected_roster_name_set",
    "roster_source_query_command_id",
    "command_type",
    "command_text",
}


def _clean_command_value(value, max_length=128):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:max_length]


class FactoryBiometricCommand(models.Model):
    _name = "factory.biometric.command"
    _description = "Biometric Device Command"
    _order = "id desc"

    command_id = fields.Char(
        string="رقم الأمر", required=True, copy=False, index=True, readonly=True
    )
    device_id = fields.Many2one(
        "factory.biometric.device",
        string="الجهاز",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="device_id.company_id", store=True, readonly=True, index=True
    )
    identity_id = fields.Many2one(
        "factory.biometric.identity",
        string="الموظف على الجهاز",
        ondelete="set null",
        index=True,
    )
    target_device_user_id = fields.Char(
        string="PIN المستهدف للجرد",
        readonly=True,
        copy=False,
        index=True,
    )
    expected_roster_name = fields.Char(
        string="الاسم المؤكد قبل الحذف",
        readonly=True,
        copy=False,
    )
    expected_roster_name_set = fields.Boolean(
        string="تم تأكيد الاسم قبل الحذف",
        readonly=True,
        copy=False,
    )
    roster_source_query_command_id = fields.Many2one(
        "factory.biometric.command",
        string="استعلام الجرد مصدر الحذف",
        readonly=True,
        copy=False,
        ondelete="set null",
        index=True,
    )
    command_type = fields.Selection(
        [
            ("user_update", "إضافة/تحديث العامل"),
            ("query_user", "قراءة بيانات العامل من الجهاز"),
            ("query_roster_user", "جرد مستخدم بالـ PIN (قراءة فقط)"),
            ("delete_roster_user", "حذف مستخدم جرد بالـ PIN"),
            ("user_name_repair", "تصحيح اسم العامل مع حفظ بياناته"),
            ("enroll_fingerprint", "تسجيل بصمة"),
            ("delete_user", "حذف العامل من الجهاز"),
        ],
        string="نوع الأمر",
        required=True,
        index=True,
    )
    command_text = fields.Text(string="النص المرسل", required=True, readonly=True)
    state = fields.Selection(
        [
            ("queued", "في الانتظار"),
            ("sent", "أُرسل للجهاز"),
            ("done", "تم بنجاح"),
            ("failed", "فشل"),
            ("cancelled", "ملغي"),
        ],
        string="الحالة",
        default="queued",
        required=True,
        index=True,
    )
    attempt_count = fields.Integer(string="عدد الإرسال", default=0, readonly=True)
    sent_at = fields.Datetime(string="آخر إرسال", readonly=True)
    acknowledged_at = fields.Datetime(string="وقت الرد", readonly=True)
    return_code = fields.Integer(string="كود النتيجة", readonly=True)
    response_text = fields.Text(string="رد الجهاز", readonly=True)
    roster_snapshot_at = fields.Datetime(
        string="وقت قراءة الجرد", readonly=True, copy=False
    )
    roster_snapshot_name = fields.Char(
        string="الاسم المقروء في الجرد", readonly=True, copy=False
    )
    roster_snapshot_metadata_complete = fields.Boolean(
        string="بيانات الجرد مكتملة", readonly=True, copy=False
    )
    roster_snapshot_has_password = fields.Boolean(
        string="له كلمة سر على الجهاز", readonly=True, copy=False
    )

    _sql_constraints = [
        (
            "command_id_uniq",
            "unique(command_id)",
            "رقم أمر الجهاز يجب أن يكون فريداً.",
        ),
        (
            "roster_source_query_command_uniq",
            "unique(roster_source_query_command_id)",
            "استعلام الجرد لا يمكن استخدامه لأكثر من أمر حذف واحد.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("أوامر الجهاز تُنشأ فقط من الإجراءات الآمنة المخصصة."))
        safe_vals_list = []
        for incoming in vals_list:
            vals = dict(incoming)
            # Never accept caller-supplied wire text or read-back results, even
            # from an internal sudo caller.  Every command is built from its
            # allowlisted type and validated model fields below.
            vals.pop("command_id", None)
            vals.pop("command_text", None)
            for field_name in _ROSTER_RESULT_FIELDS:
                vals.pop(field_name, None)
            target_pin = vals.pop("target_device_user_id", None)
            expected_name_provided = "expected_roster_name" in vals
            expected_roster_name = vals.pop("expected_roster_name", None)
            expected_name_set_provided = "expected_roster_name_set" in vals
            vals.pop("expected_roster_name_set", None)
            source_query_provided = "roster_source_query_command_id" in vals
            vals.pop("roster_source_query_command_id", None)
            command_type = vals.get("command_type")
            if command_type not in {
                "user_update",
                "query_user",
                "query_roster_user",
                "delete_roster_user",
                "user_name_repair",
                "enroll_fingerprint",
                "delete_user",
            }:
                raise ValidationError(_("نوع أمر الجهاز غير صالح."))

            if command_type in {"query_roster_user", "delete_roster_user"}:
                if vals.get("identity_id"):
                    raise ValidationError(
                        _("أوامر جرد الـ PIN لا ترتبط بهوية موظف.")
                    )
                device = self.env["factory.biometric.device"].sudo().browse(
                    vals.get("device_id")
                ).exists()
                if not device:
                    raise ValidationError(_("جهاز البصمة غير صالح."))
                pin = self._normalize_safe_pin(target_pin)
                vals.update(
                    {
                        "device_id": device.id,
                        "identity_id": False,
                        "target_device_user_id": pin,
                    }
                )
            else:
                identity = self.env["factory.biometric.identity"].sudo().browse(
                    vals.get("identity_id")
                ).exists()
                if not identity:
                    raise ValidationError(_("هوية العامل غير صالحة."))
                if vals.get("device_id") != identity.device_id.id:
                    raise ValidationError(_("الجهاز لا يطابق هوية العامل المختارة."))
                pin = self._normalize_safe_pin(identity.device_user_id)
                # Keep the PIN on destructive identity deletions. The identity
                # is removed locally while this durable push command remains.
                vals["target_device_user_id"] = (
                    pin if command_type == "delete_user" else False
                )

            if pin == "1" and command_type in _PIN1_MUTATING_COMMAND_TYPES:
                raise ValidationError(_("لا يمكن تعديل مستخدم الأدمن PIN 1."))

            if command_type != "delete_roster_user" and (
                expected_name_provided
                or expected_name_set_provided
                or source_query_provided
            ):
                raise ValidationError(
                    _("حقول تأكيد الجرد تُستخدم فقط مع أمر حذف مستخدم الجرد.")
                )

            if command_type == "query_roster_user":
                vals["command_text"] = "DATA QUERY USERINFO PIN=%s" % pin
            elif command_type == "delete_roster_user":
                source_query = self._validate_roster_delete_snapshot(
                    device,
                    pin,
                    expected_roster_name,
                    expected_name_provided=expected_name_provided,
                )
                self._ensure_roster_delete_source_unused(source_query)
                self._ensure_roster_query_after_prior_delete_send(
                    device, pin, source_query
                )
                vals.update(
                    {
                        "expected_roster_name": expected_roster_name,
                        "expected_roster_name_set": True,
                        "roster_source_query_command_id": source_query.id,
                        "command_text": "DATA DELETE USERINFO PIN=%s" % pin,
                    }
                )
            elif command_type == "user_update":
                name = _clean_command_value(
                    identity.device_user_name or identity.employee_id.name, 64
                )
                identity.device_id._validate_push_user_name(name)
                # USERINFO Verify=0 permits the enrolled methods on the terminal.
                # It is NOT an ATTLOG allowlist value: attendance still accepts
                # only the modes configured on the server for this device.
                verify = (
                    "0"
                    if {"1", "4", "15"}.issubset(identity.device_id._allowed_verify_mode_set())
                    else "1"
                )
                vals["command_text"] = (
                    "DATA UPDATE USERINFO PIN=%s\tName=%s\tPri=0\tPasswd=\tCard=\tGrp=1"
                    "\tTZ=0000000000000000\tVerify=%s"
                ) % (pin, name, verify)
            elif command_type == "query_user":
                vals["command_text"] = "DATA QUERY USERINFO PIN=%s" % pin
            elif command_type == "user_name_repair":
                if not identity.device_snapshot_complete:
                    raise ValidationError(
                        _("اقرأ بيانات العامل من الجهاز أولاً قبل تصحيح اسمه.")
                    )
                if identity.device_snapshot_has_password:
                    raise ValidationError(
                        _(
                            "للعامل كلمة سر على الجهاز؛ تم إيقاف التصحيح حتى لا تُفقد."
                        )
                    )
                snapshot_at = fields.Datetime.to_datetime(identity.device_snapshot_at)
                if (
                    not snapshot_at
                    or snapshot_at
                    < fields.Datetime.now() - _NAME_REPAIR_SNAPSHOT_MAX_AGE
                ):
                    raise ValidationError(
                        _(
                            "قراءة بيانات العامل قديمة؛ اقرأها من الجهاز مرة أخرى "
                            "قبل تصحيح الاسم."
                        )
                    )
                query_command = identity.device_snapshot_query_command_id.exists()
                if (
                    not query_command
                    or query_command.command_type != "query_user"
                    or query_command.identity_id != identity
                    or query_command.device_id != identity.device_id
                    or query_command.state != "done"
                    or query_command.return_code != 0
                    or not query_command.sent_at
                    or query_command.sent_at > snapshot_at
                ):
                    raise ValidationError(
                        _(
                            "لا يمكن ربط قراءة العامل بأمر استعلام ناجح؛ "
                            "اقرأ بياناته من الجهاز مرة أخرى."
                        )
                    )
                if self.sudo().search_count(
                    [
                        ("device_id", "=", identity.device_id.id),
                        ("identity_id", "=", identity.id),
                        ("command_type", "=", "query_user"),
                        ("id", ">", query_command.id),
                    ]
                ):
                    raise ValidationError(
                        _(
                            "يوجد استعلام أحدث لبيانات العامل؛ انتظر نتيجته "
                            "ولا تستخدم القراءة الأقدم."
                        )
                    )
                name = _clean_command_value(
                    identity.device_user_name or identity.employee_id.name, 64
                )
                identity.device_id._validate_push_user_name(name)
                snapshot = {
                    "pri": _clean_command_value(identity.device_snapshot_privilege, 8),
                    "card": _clean_command_value(identity.device_snapshot_card, 32),
                    "grp": _clean_command_value(identity.device_snapshot_group, 16),
                    "tz": _clean_command_value(identity.device_snapshot_timezone, 32),
                    "verify": _clean_command_value(identity.device_snapshot_verify, 16),
                    "vice_card": _clean_command_value(
                        identity.device_snapshot_vice_card, 128
                    ),
                }
                vals["command_text"] = (
                    "DATA UPDATE USERINFO PIN=%s\tName=%s\tPri=%s\tPasswd=\tCard=%s"
                    "\tGrp=%s\tTZ=%s\tVerify=%s"
                ) % (
                    pin,
                    name,
                    snapshot["pri"],
                    snapshot["card"],
                    snapshot["grp"],
                    snapshot["tz"],
                    snapshot["verify"],
                )
                if identity.device_snapshot_vice_card_present:
                    vals["command_text"] += "\tViceCard=%s" % snapshot["vice_card"]
            elif command_type == "enroll_fingerprint":
                vals["command_text"] = (
                    "ENROLL_FP PIN=%s\tFID=0\tRETRY=3\tOVERWRITE=1" % pin
                )
            elif command_type == "delete_user":
                vals["command_text"] = "DATA DELETE USERINFO PIN=%s" % pin
            command_id = self.env["ir.sequence"].next_by_code(
                "factory.biometric.command"
            )
            if not isinstance(command_id, str) or not _SAFE_COMMAND_ID_RE.fullmatch(
                command_id
            ):
                raise ValidationError(
                    _("تعذر إنشاء رقم أمر جهاز آمن؛ راجع إعداد تسلسل الأوامر.")
                )
            vals["command_id"] = command_id
            safe_vals_list.append(vals)
        return super().create(safe_vals_list)

    @api.model
    def _normalize_safe_pin(self, value):
        pin = str(value or "").strip()
        if not _SAFE_PIN_RE.fullmatch(pin):
            raise ValidationError(
                _("رقم العامل يجب أن يكون من 1 إلى 24 حرفاً أو رقماً بدون مسافات.")
            )
        return pin

    @api.model
    def _validate_roster_delete_snapshot(
        self, device, pin, expected_roster_name, expected_name_provided=True
    ):
        device.ensure_one()
        if pin == "1":
            raise ValidationError(_("لا يمكن حذف مستخدم الأدمن PIN 1."))
        self._ensure_roster_pin_has_no_active_identity(device, pin)
        if not expected_name_provided or not isinstance(expected_roster_name, str):
            raise ValidationError(
                _("يجب تمرير الاسم المقروء المتوقع صراحة قبل حذف مستخدم الجرد.")
            )
        cutoff = fields.Datetime.now() - _ROSTER_DELETE_SNAPSHOT_MAX_AGE
        roster_query = self.sudo().search(
            [
                ("device_id", "=", device.id),
                ("identity_id", "=", False),
                ("command_type", "=", "query_roster_user"),
                ("target_device_user_id", "=", pin),
            ],
            order="id desc",
            limit=1,
        )
        snapshot_at = (
            fields.Datetime.to_datetime(roster_query.roster_snapshot_at)
            if roster_query
            else False
        )
        if (
            not roster_query
            or roster_query.state != "done"
            or roster_query.return_code != 0
            or not snapshot_at
            or snapshot_at <= cutoff
            or not roster_query.roster_snapshot_metadata_complete
        ):
            raise ValidationError(
                _(
                    "يجب تنفيذ استعلام جرد ناجح ومكتمل خلال آخر عشر دقائق "
                    "قبل حذف هذا الـ PIN."
                )
            )
        snapshot_name = roster_query.roster_snapshot_name or ""
        if expected_roster_name != snapshot_name:
            raise ValidationError(
                _("الاسم المتوقع لا يطابق آخر اسم مقروء لهذا الـ PIN.")
            )
        return roster_query

    @api.model
    def _ensure_roster_pin_has_no_active_identity(self, device, pin):
        device.ensure_one()
        linked_identity = (
            self.env["factory.biometric.identity"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("device_id", "=", device.id),
                    ("device_user_id", "=", pin),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )
        if linked_identity:
            raise ValidationError(
                _(
                    "لا يمكن حذف هذا الـ PIN من مسار الجرد لأنه مرتبط بهوية "
                    "نشطة على الجهاز."
                )
            )

    @api.model
    def _ensure_roster_delete_source_unused(self, source_query, exclude_command=None):
        source_query.ensure_one()
        domain = [
            ("command_type", "=", "delete_roster_user"),
            ("roster_source_query_command_id", "=", source_query.id),
        ]
        if exclude_command:
            exclude_command.ensure_one()
            domain.append(("id", "!=", exclude_command.id))
        if self.sudo().search_count(domain):
            raise ValidationError(
                _(
                    "استعلام الجرد استُخدم بالفعل في أمر حذف؛ نفذ جرداً "
                    "جديداً ثم أنشئ أمر حذف جديداً."
                )
            )

    @api.model
    def _ensure_roster_query_after_prior_delete_send(
        self, device, pin, roster_query, exclude_command=None
    ):
        device.ensure_one()
        roster_query.ensure_one()
        domain = [
            ("device_id", "=", device.id),
            ("command_type", "=", "delete_roster_user"),
            ("target_device_user_id", "=", pin),
            ("sent_at", "!=", False),
        ]
        if exclude_command:
            exclude_command.ensure_one()
            domain.append(("id", "!=", exclude_command.id))
        previous_delete = self.sudo().search(
            domain, order="sent_at desc, id desc", limit=1
        )
        snapshot_at = fields.Datetime.to_datetime(roster_query.roster_snapshot_at)
        previous_sent_at = (
            fields.Datetime.to_datetime(previous_delete.sent_at)
            if previous_delete
            else False
        )
        if previous_sent_at and (not snapshot_at or snapshot_at <= previous_sent_at):
            raise ValidationError(
                _(
                    "يجب تنفيذ جرد جديد بعد آخر محاولة حذف لهذا الـ PIN قبل "
                    "إنشاء أو إرسال حذف آخر."
                )
            )

    @api.model
    def _validate_stored_roster_delete_command(self, command):
        command.ensure_one()
        if (
            command.command_type != "delete_roster_user"
            or command.identity_id
            or not command.expected_roster_name_set
            or not command.roster_source_query_command_id
        ):
            raise ValidationError(
                _("أمر الحذف لا يحتوي على تأكيد جرد صالح؛ أنشئ أمراً جديداً.")
            )
        stored_pin = command.target_device_user_id or ""
        pin = self._normalize_safe_pin(stored_pin)
        if pin != stored_pin:
            raise ValidationError(_("PIN أمر حذف الجرد غير صالح."))
        expected_command_text = "DATA DELETE USERINFO PIN=%s" % pin
        if command.command_text != expected_command_text:
            raise ValidationError(_("نص أمر حذف الجرد غير صالح."))
        source_query = command.roster_source_query_command_id
        if (
            source_query.command_type != "query_roster_user"
            or source_query.device_id != command.device_id
            or source_query.identity_id
            or source_query.target_device_user_id != pin
            or source_query.state != "done"
            or source_query.return_code != 0
            or not source_query.roster_snapshot_at
            or not source_query.roster_snapshot_metadata_complete
            or (source_query.roster_snapshot_name or "")
            != (command.expected_roster_name or "")
        ):
            raise ValidationError(
                _("مصدر جرد أمر الحذف لا يطابق التأكيد المحفوظ.")
            )
        self._ensure_roster_delete_source_unused(
            source_query, exclude_command=command
        )
        latest_query = self._validate_roster_delete_snapshot(
            command.device_id,
            pin,
            command.expected_roster_name or "",
            expected_name_provided=True,
        )
        self._ensure_roster_query_after_prior_delete_send(
            command.device_id,
            pin,
            latest_query,
            exclude_command=command,
        )
        return latest_query

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("أوامر الجهاز لا تُعدّل مباشرة."))
        if _IMMUTABLE_COMMAND_FIELDS.intersection(vals):
            raise ValidationError(_("لا يمكن تغيير بنية أمر الجهاز بعد إنشائه."))
        return super().write(vals)

    @api.model
    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        ):
            raise AccessError(_("هذه العملية متاحة لمدير الحضور فقط."))

    @api.model
    def queue_user_update(self, identity):
        self._require_manager()
        identity.ensure_one()
        return self.sudo().create(
            {
                "device_id": identity.device_id.id,
                "identity_id": identity.id,
                "command_type": "user_update",
            }
        )

    @api.model
    def queue_fingerprint_enrollment(self, identity):
        self._require_manager()
        identity.ensure_one()
        return self.sudo().create(
            {
                "device_id": identity.device_id.id,
                "identity_id": identity.id,
                "command_type": "enroll_fingerprint",
            }
        )

    @api.model
    def queue_user_query(self, identity):
        self._require_manager()
        identity.ensure_one()
        return self.sudo().create(
            {
                "device_id": identity.device_id.id,
                "identity_id": identity.id,
                "command_type": "query_user",
            }
        )

    @api.model
    def queue_roster_user_query(self, device, target_device_user_id):
        """Queue one identity-free, read-only USERINFO lookup.

        A per-device advisory lock makes the pending-command de-duplication
        safe when two managers request the same PIN concurrently.
        """
        self._require_manager()
        device.ensure_one()
        pin = self._normalize_safe_pin(target_device_user_id)
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (82464, device.id)
        )
        pending = self.sudo().search(
            [
                ("device_id", "=", device.id),
                ("identity_id", "=", False),
                ("command_type", "=", "query_roster_user"),
                ("target_device_user_id", "=", pin),
                ("state", "in", ["queued", "sent"]),
            ],
            order="id",
            limit=1,
        )
        if pending:
            return pending
        return self.sudo().create(
            {
                "device_id": device.id,
                "command_type": "query_roster_user",
                "target_device_user_id": pin,
            }
        )

    @api.model
    def queue_roster_user_delete(
        self, device, target_device_user_id, expected_roster_name=None
    ):
        """Queue a guarded identity-free deletion for a recently read PIN."""
        self._require_manager()
        device.ensure_one()
        pin = self._normalize_safe_pin(target_device_user_id)
        if pin == "1":
            raise ValidationError(_("لا يمكن حذف مستخدم الأدمن PIN 1."))
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (82464, device.id)
        )
        self._validate_roster_delete_snapshot(
            device,
            pin,
            expected_roster_name,
            expected_name_provided=expected_roster_name is not None,
        )
        pending = self.sudo().search(
            [
                ("device_id", "=", device.id),
                ("identity_id", "=", False),
                ("command_type", "=", "delete_roster_user"),
                ("target_device_user_id", "=", pin),
                ("state", "in", ["queued", "sent"]),
            ],
            order="id",
            limit=1,
        )
        if pending:
            if (
                not pending.expected_roster_name_set
                or not pending.roster_source_query_command_id
                or (pending.expected_roster_name or "") != expected_roster_name
                or pending.command_text != "DATA DELETE USERINFO PIN=%s" % pin
            ):
                raise ValidationError(
                    _(
                        "يوجد أمر حذف معلق لنفس الـ PIN بتأكيد اسم مختلف أو ناقص."
                    )
                )
            self._validate_stored_roster_delete_command(pending)
            return pending
        return self.sudo().create(
            {
                "device_id": device.id,
                "command_type": "delete_roster_user",
                "target_device_user_id": pin,
                "expected_roster_name": expected_roster_name,
            }
        )

    @api.model
    def queue_user_name_repair(self, identity):
        self._require_manager()
        identity.ensure_one()
        return self.sudo().create(
            {
                "device_id": identity.device_id.id,
                "identity_id": identity.id,
                "command_type": "user_name_repair",
            }
        )

    @api.model
    def queue_delete_user(self, identity):
        self._require_manager()
        identity.ensure_one()
        pin = self._normalize_safe_pin(identity.device_user_id)
        if pin == "1":
            raise ValidationError(_("لا يمكن حذف مستخدم الأدمن PIN 1."))
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (82464, identity.device_id.id),
        )
        pending = self.sudo().search(
            [
                ("device_id", "=", identity.device_id.id),
                ("command_type", "=", "delete_user"),
                ("state", "in", ["queued", "sent"]),
                "|",
                ("target_device_user_id", "=", pin),
                ("identity_id", "=", identity.id),
            ],
            order="id",
            limit=1,
        )
        if pending:
            return pending
        return self.sudo().create(
            {
                "device_id": identity.device_id.id,
                "identity_id": identity.id,
                "command_type": "delete_user",
            }
        )

    @api.model
    def _pop_for_device(self, device):
        if not self.env.su:
            raise AccessError(_("مسار أوامر الجهاز داخلي فقط."))
        device.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (82463, device.id)
        )
        now = fields.Datetime.now()
        retry_before = now - timedelta(seconds=_COMMAND_RETRY_SECONDS)
        command = self.browse()
        for _index in range(100):
            command = self.sudo().search(
                [
                    ("device_id", "=", device.id),
                    ("state", "in", ["queued", "sent"]),
                ],
                order="id",
                limit=1,
            )
            if not command:
                return "OK"
            if not _SAFE_COMMAND_ID_RE.fullmatch(command.command_id or ""):
                command.sudo().write(
                    {
                        "state": "failed",
                        "return_code": _UNSAFE_COMMAND_ID_FAILED,
                        "response_text": _(
                            "تم إيقاف الأمر لأن رقم الأمر المخزن غير آمن."
                        ),
                    }
                )
                continue
            if command.state == "sent":
                if command.sent_at and command.sent_at > retry_before:
                    return "OK"
                if command.command_type in _NON_REPLAYABLE_COMMAND_TYPES:
                    command.sudo().write(
                        {
                            "state": "failed",
                            "return_code": _DESTRUCTIVE_COMMAND_ACK_TIMEOUT,
                            "response_text": _(
                                "لم يصل رد من الجهاز، لذلك لن يُعاد إرسال أمر "
                                "الحذف تلقائياً. نفذ جرداً جديداً وأنشئ أمر "
                                "حذف جديداً."
                            ),
                        }
                    )
                    continue
                if command.attempt_count >= _COMMAND_MAX_ATTEMPTS:
                    command.sudo().write(
                        {
                            "state": "failed",
                            "return_code": -998,
                            "response_text": _(
                                "لم يصل رد من الجهاز بعد %s محاولات."
                            )
                            % _COMMAND_MAX_ATTEMPTS,
                        }
                    )
                    continue
            if command.command_type == "delete_roster_user":
                try:
                    self._validate_stored_roster_delete_command(command)
                except ValidationError:
                    command.sudo().write(
                        {
                            "state": "failed",
                            "return_code": _ROSTER_DELETE_REVALIDATION_FAILED,
                            "response_text": _(
                                "تم إيقاف حذف مستخدم الجرد لأن تأكيد القراءة "
                                "الحديث لم يعد صالحاً."
                            ),
                        }
                    )
                    continue
            break
        if not command or command.state not in ("queued", "sent"):
            return "OK"
        command.sudo().write(
            {
                "state": "sent",
                "attempt_count": command.attempt_count + 1,
                "sent_at": fields.Datetime.now(),
            }
        )
        return "C:%s:%s" % (command.command_id, command.command_text)

    @api.model
    def _acknowledge(self, device, payload):
        if not self.env.su:
            raise AccessError(_("مسار ردود الجهاز داخلي فقط."))
        device.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (82463, device.id)
        )
        processed = 0
        lines = (payload or "").splitlines()
        if len(lines) > 1000:
            raise ValidationError(_("عدد ردود الجهاز في الطلب أكبر من الحد المسموح."))
        for raw_line in lines:
            if len(raw_line.encode("utf-8", errors="replace")) > 4096:
                raise ValidationError(_("رد الجهاز أطول من الحد المسموح."))
            line = raw_line.strip()
            if not line:
                continue
            values = parse_qs(line, keep_blank_values=True)
            command_id = (values.get("ID") or values.get("id") or [""])[0]
            return_value = (values.get("Return") or values.get("return") or [""])[0]
            if not command_id:
                continue
            command = self.sudo().search(
                [("device_id", "=", device.id), ("command_id", "=", command_id)],
                limit=1,
            )
            if not command:
                continue
            if command.state != "sent":
                continue
            try:
                return_code = int(return_value)
            except (TypeError, ValueError):
                return_code = -999
            command.sudo().write(
                {
                    "state": "done" if return_code == 0 else "failed",
                    "acknowledged_at": fields.Datetime.now(),
                    "return_code": return_code,
                    "response_text": line[:2000],
                }
            )
            if command.command_type == "enroll_fingerprint" and command.identity_id:
                command.identity_id.write(
                    {
                        "enrollment_state": "enrolled" if return_code == 0 else "not_enrolled",
                        "last_enrollment_at": fields.Datetime.now()
                        if return_code == 0
                        else False,
                    }
                )
            processed += 1
        return processed

    def action_cancel(self):
        self._require_manager()
        for device_id in sorted(self.mapped("device_id").ids):
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", (82463, device_id)
            )
        self.invalidate_recordset(["state"])
        invalid = self.filtered(lambda command: command.state != "queued")
        if invalid:
            raise ValidationError(
                _("يمكن إلغاء الأمر فقط قبل إرساله إلى الجهاز.")
            )
        self.sudo().write({"state": "cancelled"})
        return True

    def action_retry(self):
        self._require_manager()
        invalid = self.filtered(
            lambda command: command.state not in ("failed", "cancelled")
        )
        if invalid:
            raise ValidationError(
                _("يمكن إعادة المحاولة فقط لأمر فاشل أو ملغي.")
            )
        destructive = self.filtered(
            lambda command: command.command_type in _NON_REPLAYABLE_COMMAND_TYPES
        )
        if destructive:
            raise ValidationError(
                _(
                    "أوامر الحذف لا تُعاد لتجنب تكرار عملية غير قابلة للتراجع. "
                    "نفذ جرداً جديداً وأنشئ أمر حذف جديداً."
                )
            )
        self.sudo().write(
            {
                "state": "queued",
                "attempt_count": 0,
                "sent_at": False,
                "acknowledged_at": False,
                "return_code": False,
                "response_text": False,
                "roster_snapshot_at": False,
                "roster_snapshot_name": False,
                "roster_snapshot_metadata_complete": False,
                "roster_snapshot_has_password": False,
            }
        )
        return True
