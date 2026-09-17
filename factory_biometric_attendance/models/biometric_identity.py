# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


_SAFE_PIN_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,24}$")
_AUTO_PIN_LOCK_NAMESPACE = 82465
_DEVICE_SNAPSHOT_FIELDS = {
    "device_snapshot_at",
    "device_snapshot_name",
    "device_snapshot_query_command_id",
    "device_snapshot_complete",
    "device_snapshot_privilege",
    "device_snapshot_has_password",
    "device_snapshot_card",
    "device_snapshot_group",
    "device_snapshot_timezone",
    "device_snapshot_verify",
    "device_snapshot_vice_card_present",
    "device_snapshot_vice_card",
}


class FactoryBiometricIdentity(models.Model):
    _name = "factory.biometric.identity"
    _description = "Employee Biometric Device Identity"
    _rec_name = "display_name"
    _order = "device_id, device_user_id"

    device_id = fields.Many2one(
        "factory.biometric.device",
        string="جهاز البصمة",
        required=True,
        default=lambda self: self._default_device_id(),
        ondelete="restrict",
        index=True,
    )
    company_id = fields.Many2one(
        related="device_id.company_id", store=True, readonly=True, index=True
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="الموظف",
        required=True,
        ondelete="restrict",
        index=True,
        check_company=True,
    )
    department_id = fields.Many2one(
        related="employee_id.department_id",
        string="القسم",
        readonly=True,
    )
    job_id = fields.Many2one(
        related="employee_id.job_id",
        string="الوظيفة",
        readonly=True,
    )
    device_user_id = fields.Char(
        string="رقم العامل على الجهاز (تلقائي)",
        required=True,
        index=True,
        help=(
            "هو PIN الظاهر للعامل داخل جهاز ZKTeco. يخصصه النظام تلقائياً "
            "عند حفظ الربط، ويختار أصغر رقم متاح. لا يعاد استخدام رقم محذوف "
            "إلا بعد تأكيد الجهاز نجاح الحذف وعدم وجود حركات قديمة قابلة للمعالجة."
        ),
    )
    device_user_name = fields.Char(string="الاسم على الجهاز")
    device_snapshot_at = fields.Datetime(string="آخر قراءة لبيانات الجهاز", readonly=True)
    device_snapshot_name = fields.Char(
        string="الاسم المقروء من الجهاز", readonly=True
    )
    device_snapshot_query_command_id = fields.Many2one(
        "factory.biometric.command",
        string="أمر القراءة المرتبط",
        readonly=True,
        copy=False,
        ondelete="set null",
        index=True,
    )
    device_snapshot_complete = fields.Boolean(
        string="قراءة بيانات الجهاز مكتملة", default=False, readonly=True
    )
    device_snapshot_privilege = fields.Char(string="صلاحية الجهاز", readonly=True)
    device_snapshot_has_password = fields.Boolean(
        string="له كلمة سر على الجهاز", default=False, readonly=True
    )
    device_snapshot_card = fields.Char(
        string="رقم الكارت على الجهاز",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    device_snapshot_group = fields.Char(string="مجموعة الجهاز", readonly=True)
    device_snapshot_timezone = fields.Char(string="منطقة وقت الجهاز", readonly=True)
    device_snapshot_verify = fields.Char(string="طريقة تحقق الجهاز", readonly=True)
    device_snapshot_vice_card_present = fields.Boolean(
        string="قراءة الكروت الإضافية متاحة", default=False, readonly=True
    )
    device_snapshot_vice_card = fields.Char(
        string="الكروت الإضافية على الجهاز",
        readonly=True,
        groups="hr_attendance.group_hr_attendance_manager",
    )
    enrollment_state = fields.Selection(
        [
            ("not_enrolled", "غير مسجل"),
            ("requested", "تم إرسال طلب التسجيل"),
            ("enrolled", "البصمة مسجلة"),
            ("unknown", "غير معروف"),
        ],
        string="حالة البصمة",
        default="not_enrolled",
        required=True,
        index=True,
    )
    last_enrollment_at = fields.Datetime(string="آخر تسجيل", readonly=True)
    active = fields.Boolean(default=True)
    display_name = fields.Char(compute="_compute_display_name", store=True)
    event_count = fields.Integer(compute="_compute_event_count")

    _sql_constraints = [
        (
            "device_pin_uniq",
            "unique(device_id, device_user_id)",
            "رقم العامل مستخدم بالفعل على هذا الجهاز.",
        ),
        (
            "device_employee_uniq",
            "unique(device_id, employee_id)",
            "الموظف مرتبط بالفعل بهذا الجهاز.",
        ),
    ]

    @api.model
    def _default_device_id(self):
        """Select the device only when the current company has one clear choice."""
        devices = self.env["factory.biometric.device"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("state", "=", "active"),
                ("active", "=", True),
            ],
            order="id",
            limit=2,
        )
        return devices if len(devices) == 1 else False

    @api.depends("employee_id.name", "device_id.name", "device_user_id")
    def _compute_display_name(self):
        for identity in self:
            identity.display_name = "%s — %s (%s)" % (
                identity.employee_id.name or "",
                identity.device_id.name or "",
                identity.device_user_id or "",
            )

    def _compute_event_count(self):
        grouped = self.env["factory.biometric.event"]._read_group(
            [("identity_id", "in", self.ids)], ["identity_id"], ["__count"]
        )
        counts = {identity.id: count for identity, count in grouped}
        for identity in self:
            identity.event_count = counts.get(identity.id, 0)

    @api.constrains("device_user_id")
    def _check_device_user_id(self):
        for identity in self:
            if not _SAFE_PIN_RE.fullmatch((identity.device_user_id or "").strip()):
                raise ValidationError(
                    _("رقم العامل يجب أن يكون من 1 إلى 24 حرفاً أو رقماً بدون مسافات.")
                )

    @api.constrains("employee_id", "company_id")
    def _check_company(self):
        for identity in self:
            if identity.employee_id.company_id != identity.company_id:
                raise ValidationError(_("الموظف وجهاز البصمة يجب أن يكونا في نفس الشركة."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            _DEVICE_SNAPSHOT_FIELDS.intersection(vals) for vals in vals_list
        ):
            raise AccessError(_("قراءة بيانات الجهاز يكتبها مسار البصمة فقط."))
        vals_list = [
            {
                **vals,
                "device_user_id": (vals.get("device_user_id") or "").strip(),
                **(
                    {"device_user_name": (vals.get("device_user_name") or "").strip()}
                    if "device_user_name" in vals
                    else {}
                ),
            }
            for vals in vals_list
        ]
        default_device = self._default_device_id()
        if default_device:
            for vals in vals_list:
                if "device_id" not in vals:
                    vals["device_id"] = default_device.id
        self._assign_automatic_device_user_ids(vals_list)
        records = super().create(vals_list)
        records._reprocess_unmatched_events()
        return records

    @api.model
    def _reserved_automatic_device_user_ids(self, device):
        """Return numeric PINs which are not safe for automatic allocation.

        PIN 1 is permanently reserved for the device administrator. Historical
        baseline rows are audit-only and may safely coexist with a reused PIN.
        Every other event keeps its PIN reserved. A deleted PIN becomes reusable
        only after the latest destructive command is acknowledged successfully;
        this prevents an offline, failed, or still-pending delete from removing a
        newly-created user or leaving the old fingerprint template attached.
        """
        device.ensure_one()
        self.env.cr.execute(
            """
                SELECT device_user_id::numeric
                  FROM factory_biometric_identity
                 WHERE device_id = %s
                   AND device_user_id ~ '^[0-9]+$'
                UNION
                SELECT device_user_id::numeric
                  FROM factory_biometric_event
                 WHERE device_id = %s
                   AND device_user_id ~ '^[0-9]+$'
                   AND (
                        state <> 'ignored'
                        OR resolved_action IS DISTINCT FROM 'baseline'
                   )
            """,
            (device.id, device.id),
        )
        reserved = {1}
        reserved.update(int(row[0]) for row in self.env.cr.fetchall())

        latest_delete_by_pin = {}
        destructive_commands = (
            self.env["factory.biometric.command"]
            .sudo()
            .search(
                [
                    ("device_id", "=", device.id),
                    ("command_type", "in", ["delete_user", "delete_roster_user"]),
                    ("target_device_user_id", "!=", False),
                ],
                order="id desc",
            )
        )
        for command in destructive_commands:
            pin = command.target_device_user_id or ""
            if pin.isdigit():
                latest_delete_by_pin.setdefault(int(pin), command)
        for pin, command in latest_delete_by_pin.items():
            if command.state != "done" or command.return_code != 0:
                reserved.add(pin)
        return reserved

    @api.model
    def _next_automatic_device_user_id(self, device, extra_reserved=None):
        """Return the lowest safely reusable numeric PIN after admin PIN 1."""
        reserved = self._reserved_automatic_device_user_ids(device)
        reserved.update(extra_reserved or set())
        next_pin = 2
        while next_pin in reserved:
            next_pin += 1
        if len(str(next_pin)) > 24:
            raise ValidationError(
                _("تعذر تخصيص رقم عامل جديد لهذا الجهاز؛ تم استنفاد نطاق الأرقام.")
            )
        return str(next_pin)

    @api.model
    def _assign_automatic_device_user_ids(self, vals_list):
        """Fill blank PINs safely for single and batched identity creation."""
        device_ids = sorted(
            {
                int(vals["device_id"])
                for vals in vals_list
                if vals.get("device_id")
            }
        )
        for device_id in device_ids:
            # Serialize both automatic and explicit identity creation on the
            # device so two simultaneous saves cannot receive the same PIN.
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (_AUTO_PIN_LOCK_NAMESPACE, device_id),
            )

        devices = {
            device.id: device
            for device in self.env["factory.biometric.device"]
            .sudo()
            .browse(device_ids)
            .exists()
        }
        missing_devices = [
            vals.get("device_id")
            for vals in vals_list
            if not vals.get("device_user_id") and vals.get("device_id") not in devices
        ]
        if missing_devices:
            raise ValidationError(_("اختر جهاز بصمة صالحاً قبل حفظ ربط الموظف."))

        reserved_by_device = {}
        explicit_numeric_by_device = {}
        for vals in vals_list:
            pin = vals.get("device_user_id") or ""
            if pin.isdigit() and vals.get("device_id") in devices:
                explicit_numeric_by_device.setdefault(vals["device_id"], []).append(
                    int(pin)
                )

        for vals in vals_list:
            if vals.get("device_user_id"):
                continue
            device_id = vals.get("device_id")
            device = devices.get(device_id)
            if not device:
                raise ValidationError(
                    _("اختر جهاز البصمة أولاً ليتم تخصيص رقم العامل تلقائياً.")
                )
            if device_id not in reserved_by_device:
                reserved_by_device[device_id] = set(
                    explicit_numeric_by_device.get(device_id, [])
                )
            next_pin = self._next_automatic_device_user_id(
                device, extra_reserved=reserved_by_device[device_id]
            )
            vals["device_user_id"] = next_pin
            reserved_by_device[device_id].add(int(next_pin))

    def write(self, vals):
        vals = dict(vals)
        if not self.env.su and _DEVICE_SNAPSHOT_FIELDS.intersection(vals):
            raise AccessError(_("قراءة بيانات الجهاز يكتبها مسار البصمة فقط."))
        if "device_user_id" in vals:
            vals["device_user_id"] = (vals.get("device_user_id") or "").strip()
        if "device_user_name" in vals:
            vals["device_user_name"] = (vals.get("device_user_name") or "").strip()
        protected = {"device_id", "employee_id", "device_user_id"}.intersection(vals)
        if protected:
            linked = self.env["factory.biometric.event"].sudo().search_count(
                [("identity_id", "in", self.ids)]
            )
            if linked:
                raise ValidationError(
                    _(
                        "لا يمكن تغيير الجهاز أو الموظف أو رقم العامل بعد وجود حركات. "
                        "أرشف الربط وأنشئ ربطاً جديداً للحفاظ على السجل التاريخي."
                    )
                )
        result = super().write(vals)
        if {"device_id", "device_user_id", "employee_id", "active"}.intersection(vals):
            self._reprocess_unmatched_events()
        return result

    def _reprocess_unmatched_events(self):
        for identity in self.filtered("active"):
            events = self.env["factory.biometric.event"].sudo().search(
                [
                    ("device_id", "=", identity.device_id.id),
                    ("device_user_id", "=", identity.device_user_id),
                    ("state", "in", ["received", "unmapped"]),
                ],
                order="punch_time, id",
            )
            events.action_reprocess()

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "hr_attendance.group_hr_attendance_manager"
        ):
            raise AccessError(_("هذه العملية متاحة لمدير الحضور فقط."))

    def action_queue_user_sync(self):
        self._require_manager()
        for identity in self:
            self.env["factory.biometric.command"].sudo().queue_user_update(identity)
        return True

    def action_print_biometric_directory(self):
        """Print selected identities, or the full active company directory."""
        self._require_manager()
        identities = self.exists()
        if not identities:
            identities = self.search(
                [
                    ("active", "=", True),
                    ("company_id", "in", self.env.companies.ids),
                ],
                order="device_id, id",
            )
        return self.env.ref(
            "factory_biometric_attendance.action_report_biometric_directory"
        ).report_action(identities, config=False)

    def action_request_enrollment(self):
        self._require_manager()
        for identity in self:
            command_model = self.env["factory.biometric.command"].sudo()
            command_model.queue_user_update(identity)
            command_model.queue_fingerprint_enrollment(identity)
            identity.write({"enrollment_state": "requested"})
        return True

    def action_query_device_user(self):
        self._require_manager()
        for identity in self:
            self.env["factory.biometric.command"].sudo().queue_user_query(identity)
        return True

    def action_repair_device_user_name(self):
        self._require_manager()
        for identity in self:
            self.env["factory.biometric.command"].sudo().queue_user_name_repair(
                identity
            )
        return True

    def action_view_events(self):
        self.ensure_one()
        action = self.env.ref(
            "factory_biometric_attendance.action_biometric_event"
        ).read()[0]
        action["domain"] = [("identity_id", "=", self.id)]
        return action

    def _purge_local_biometric_history(self):
        """Remove only history produced by these biometric identities."""
        event_model = self.env["factory.biometric.event"].sudo()
        attendance_model = self.env["hr.attendance"].sudo()
        for identity in self:
            events = event_model.search(
                [
                    ("device_id", "=", identity.device_id.id),
                    "|",
                    ("identity_id", "=", identity.id),
                    "&",
                    ("employee_id", "=", identity.employee_id.id),
                    ("device_user_id", "=", identity.device_user_id),
                ]
            )
            attendances = events.mapped("attendance_id")
            attendances |= attendance_model.search(
                [
                    ("employee_id", "=", identity.employee_id.id),
                    "|",
                    ("biometric_in_device_id", "=", identity.device_id.id),
                    ("biometric_out_device_id", "=", identity.device_id.id),
                ]
            )
            if attendances:
                attendances.with_context(factory_biometric_sync=True).unlink()
            if events:
                events.unlink()

    def action_delete_from_device_and_purge(self):
        self._require_manager()
        self.unlink()
        return {"type": "ir.actions.act_window_close"}

    def unlink(self):
        if self.env.context.get("module_uninstall"):
            return super().unlink()
        self._require_manager()
        command_model = self.env["factory.biometric.command"]
        # Queue every durable device command before touching local history. A
        # validation error (notably PIN 1) aborts the transaction beforehand.
        for identity in self:
            command_model.queue_delete_user(identity)
        self._purge_local_biometric_history()
        return super().unlink()
