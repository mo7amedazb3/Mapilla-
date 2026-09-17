# -*- coding: utf-8 -*-

from datetime import timedelta

from lxml import etree

from odoo import api, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..controllers.main import ZKTecoPushController


@tagged("post_install", "-at_install")
class TestFactoryBiometricAttendance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create(
            {"name": "عامل اختبار البصمة", "company_id": cls.env.company.id}
        )
        cls.device = cls.env["factory.biometric.device"].create(
            {
                "name": "بصمة الاختبار",
                "serial_number": "TEST-ZK-001",
                "state": "active",
                "expected_source_ip": "127.0.0.1",
                "timezone": "Africa/Cairo",
                "accept_events_from": fields.Datetime.to_datetime("2026-08-29 00:00:00"),
                "debounce_seconds": 30,
                "max_open_hours": 20,
            }
        )
        cls.identity = cls.env["factory.biometric.identity"].create(
            {
                "device_id": cls.device.id,
                "employee_id": cls.employee.id,
                "device_user_id": "1001",
            }
        )

    def _ingest(self, line, device=None, stamp="1"):
        return self.env["factory.biometric.event"].sudo()._ingest_attlog(
            device or self.device, line, stamp=stamp
        )

    def _dispatch_user_query(self, acknowledge=True):
        self.identity.action_query_device_user()
        query = self.device.command_ids.filtered(
            lambda command: command.command_type == "query_user"
        ).sorted("id")[-1]
        outgoing = self.env["factory.biometric.command"].sudo()._pop_for_device(
            self.device
        )
        self.assertEqual(
            outgoing,
            "C:%s:DATA QUERY USERINFO PIN=1001" % query.command_id,
        )
        if acknowledge:
            self.env["factory.biometric.command"].sudo()._acknowledge(
                self.device, "ID=%s&Return=0&CMD=DATA" % query.command_id
            )
        return query

    def _complete_roster_query(self, pin, name):
        command_model = self.env["factory.biometric.command"]
        query = command_model.queue_roster_user_query(self.device, pin)
        self.assertEqual(
            command_model.sudo()._pop_for_device(self.device),
            "C:%s:DATA QUERY USERINFO PIN=%s" % (query.command_id, pin),
        )
        command_model.sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % query.command_id
        )
        self.device.sudo()._ingest_operlog(
            "USER PIN=%s\tName=%s\tPri=0\tPasswd=\tCard="
            "\tGrp=1\tTZ=0000000000000000\tVerify=1" % (pin, name),
            stamp="roster-delete-preflight-%s" % pin,
        )
        self.assertTrue(query.roster_snapshot_metadata_complete)
        return query

    def _record_completed_roster_query(self, pin, name):
        query = self.env["factory.biometric.command"].queue_roster_user_query(
            self.device, pin
        )
        now = fields.Datetime.now()
        query.sudo().write(
            {
                "state": "done",
                "attempt_count": 1,
                "sent_at": now,
                "acknowledged_at": now,
                "return_code": 0,
                "roster_snapshot_at": now,
                "roster_snapshot_name": name,
                "roster_snapshot_metadata_complete": True,
                "roster_snapshot_has_password": False,
            }
        )
        return query

    def test_blank_device_pin_uses_lowest_safe_available_number(self):
        device = self.env["factory.biometric.device"].create(
            {
                "name": "جهاز تخصيص الأرقام",
                "serial_number": "TEST-ZK-AUTO-PIN",
                "state": "active",
                "expected_source_ip": "127.0.0.1",
                "timezone": "Africa/Cairo",
                "accept_events_from": fields.Datetime.to_datetime(
                    "2026-08-29 00:00:00"
                ),
            }
        )
        employees = self.env["hr.employee"].create(
            [
                {"name": "عامل رقم تلقائي 1", "company_id": self.env.company.id},
                {"name": "عامل رقم تلقائي 2", "company_id": self.env.company.id},
                {"name": "عامل رقم تلقائي 3", "company_id": self.env.company.id},
                {"name": "عامل رقم تلقائي 4", "company_id": self.env.company.id},
            ]
        )

        first_two = self.env["factory.biometric.identity"].create(
            [
                {"device_id": device.id, "employee_id": employees[0].id},
                {
                    "device_id": device.id,
                    "employee_id": employees[1].id,
                    "device_user_id": "",
                },
            ]
        )
        self.assertEqual(first_two.mapped("device_user_id"), ["2", "3"])

        explicit = self.env["factory.biometric.identity"].create(
            {
                "device_id": device.id,
                "employee_id": employees[2].id,
                "device_user_id": "8",
            }
        )
        self.assertEqual(explicit.device_user_id, "8")
        explicit.unlink()

        after_delete = self.env["factory.biometric.identity"].create(
            {"device_id": device.id, "employee_id": employees[3].id}
        )
        self.assertEqual(after_delete.device_user_id, "4")
        delete_command = self.env["factory.biometric.command"].search(
            [
                ("device_id", "=", device.id),
                ("command_type", "=", "delete_user"),
                ("target_device_user_id", "=", "8"),
            ]
        )
        self.assertEqual(len(delete_command), 1)

    def test_acknowledged_delete_releases_pin_but_pending_delete_does_not(self):
        device = self.env["factory.biometric.device"].create(
            {
                "name": "جهاز إعادة استخدام الأرقام",
                "serial_number": "TEST-ZK-SAFE-PIN-REUSE",
                "state": "active",
                "expected_source_ip": "127.0.0.1",
                "accept_events_from": fields.Datetime.to_datetime(
                    "2026-08-29 00:00:00"
                ),
            }
        )
        employees = self.env["hr.employee"].create(
            [
                {"name": "عامل رقم قديم", "company_id": self.env.company.id},
                {"name": "عامل أثناء الحذف", "company_id": self.env.company.id},
                {"name": "عامل بعد تأكيد الحذف", "company_id": self.env.company.id},
            ]
        )
        old_identity = self.env["factory.biometric.identity"].create(
            {"device_id": device.id, "employee_id": employees[0].id}
        )
        self.assertEqual(old_identity.device_user_id, "2")
        self._ingest(
            "2\t2026-08-28 08:00:00\t0\t1\t0\t0\t0",
            device=device,
            stamp="safe-reuse-baseline",
        )
        baseline = device.event_ids
        self.assertEqual(baseline.state, "ignored")
        self.assertEqual(baseline.resolved_action, "baseline")

        old_identity.unlink()
        during_delete = self.env["factory.biometric.identity"].create(
            {"device_id": device.id, "employee_id": employees[1].id}
        )
        self.assertEqual(during_delete.device_user_id, "3")

        command_model = self.env["factory.biometric.command"].sudo()
        delete_command = device.command_ids.filtered(
            lambda command: command.command_type == "delete_user"
        )
        outgoing = command_model._pop_for_device(device)
        self.assertIn("DATA DELETE USERINFO PIN=2", outgoing)
        command_model._acknowledge(
            device, "ID=%s&Return=0&CMD=DATA" % delete_command.command_id
        )
        self.assertEqual(delete_command.state, "done")

        after_ack = self.env["factory.biometric.identity"].create(
            {"device_id": device.id, "employee_id": employees[2].id}
        )
        self.assertEqual(after_ack.device_user_id, "2")

    def test_single_active_device_is_selected_by_default(self):
        employee = self.env["hr.employee"].create(
            {"name": "عامل الجهاز الافتراضي", "company_id": self.env.company.id}
        )
        identity_model = self.env["factory.biometric.identity"]
        defaults = identity_model.default_get(["device_id"])
        self.assertEqual(defaults["device_id"], self.device.id)

        identity = identity_model.create({"employee_id": employee.id})
        self.assertEqual(identity.device_id, self.device)
        self.assertTrue(identity.device_user_id)

    def test_device_is_not_assumed_when_more_than_one_is_active(self):
        self.env["factory.biometric.device"].create(
            {
                "name": "جهاز اختيار ثانٍ",
                "serial_number": "TEST-ZK-SECOND-DEFAULT",
                "state": "active",
                "expected_source_ip": "127.0.0.2",
            }
        )
        defaults = self.env["factory.biometric.identity"].default_get(["device_id"])
        self.assertFalse(defaults.get("device_id"))

    def test_dynamic_source_ip_accepts_rollover_and_records_latest_address(self):
        self.device.write({"source_ip_mode": "dynamic"})

        self.assertTrue(self.device._check_source_ip("197.42.18.141"))
        self.device._register_from_push(
            self.device.serial_number,
            source_ip="197.42.18.141",
            metadata={"push_version": "2.4.1"},
        )

        self.assertEqual(self.device.expected_source_ip, "197.42.18.141")
        self.assertEqual(self.device.last_source_ip, "197.42.18.141")
        self.assertEqual(self.device.push_version, "2.4.1")

    def test_fixed_source_ip_still_rejects_rollover(self):
        self.assertFalse(self.device._check_source_ip("197.42.18.141"))
        self.assertEqual(self.device.expected_source_ip, "127.0.0.1")

    def test_batch_auto_pin_skips_explicit_pin_in_same_create(self):
        device = self.env["factory.biometric.device"].create(
            {
                "name": "جهاز تخصيص دفعة",
                "serial_number": "TEST-ZK-AUTO-PIN-BATCH",
                "state": "active",
                "expected_source_ip": "127.0.0.1",
                "timezone": "Africa/Cairo",
                "accept_events_from": fields.Datetime.to_datetime(
                    "2026-08-29 00:00:00"
                ),
            }
        )
        employees = self.env["hr.employee"].create(
            [
                {"name": "عامل دفعة تلقائي", "company_id": self.env.company.id},
                {"name": "عامل دفعة صريح", "company_id": self.env.company.id},
            ]
        )
        identities = self.env["factory.biometric.identity"].create(
            [
                {"device_id": device.id, "employee_id": employees[0].id},
                {
                    "device_id": device.id,
                    "employee_id": employees[1].id,
                    "device_user_id": "7",
                },
            ]
        )
        self.assertEqual(identities.mapped("device_user_id"), ["2", "7"])

    def test_employee_biometric_directory_opens_from_row_and_prints_pdf(self):
        self.identity.device_user_name = "Test Worker"
        menu = self.env.ref(
            "factory_biometric_attendance.menu_employee_biometric_directory"
        )
        self.assertEqual(
            menu.action,
            self.env.ref(
                "factory_biometric_attendance.action_employee_biometric_directory"
            ),
        )

        view = self.env.ref(
            "factory_biometric_attendance.view_biometric_identity_list"
        )
        arch = self.env["factory.biometric.identity"].get_view(
            view_id=view.id, view_type="list"
        )["arch"]
        document = etree.fromstring(arch.encode())
        self.assertIsNone(document.get("editable"))
        self.assertIsNone(document.get("open_form_view"))
        print_buttons = document.xpath(
            "./header/button[@name='action_print_biometric_directory']"
        )
        self.assertEqual(len(print_buttons), 1)
        self.assertEqual(print_buttons[0].get("display"), "always")
        self.assertTrue(
            document.xpath("./field[@name='device_user_name']")
        )

        action = self.env[
            "factory.biometric.identity"
        ].action_print_biometric_directory()
        report = self.env.ref(
            "factory_biometric_attendance.action_report_biometric_directory"
        )
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(action["report_name"], report.report_name)
        self.assertEqual(report.report_type, "qweb-pdf")
        self.assertEqual(report.paperformat_id.orientation, "Landscape")

        html = self.env["ir.actions.report"]._render_qweb_html(
            report.report_name, self.identity.ids
        )[0].decode("utf-8")
        self.assertIn("دليل بصمات الموظفين", html)
        self.assertIn(self.employee.name, html)
        self.assertIn("Test Worker", html)
        self.assertIn("1001", html)

        pdf, output_type = self.env["ir.actions.report"].with_context(
            force_report_rendering=True
        )._render_qweb_pdf(report.report_name, res_ids=self.identity.ids)
        self.assertEqual(output_type, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)

    def test_first_punch_checks_in_second_checks_out(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        attendance = self.env["hr.attendance"].search(
            [("employee_id", "=", self.employee.id)]
        )
        self.assertEqual(len(attendance), 1)
        self.assertEqual(
            attendance.check_in, fields.Datetime.to_datetime("2026-08-29 05:00:00")
        )
        self.assertFalse(attendance.check_out)
        self.assertEqual(attendance.in_mode, "biometric")
        self.assertEqual(attendance.biometric_in_device_id, self.device)

        self._ingest("1001\t2026-08-29 09:00:00\t0\t1\t0\t0\t0", stamp="2")
        self.assertEqual(
            attendance.check_out, fields.Datetime.to_datetime("2026-08-29 06:00:00")
        )
        self.assertEqual(attendance.out_mode, "biometric")
        self.assertEqual(attendance.biometric_out_device_id, self.device)
        actions = self.env["factory.biometric.event"].search(
            [("attendance_id", "=", attendance.id)], order="punch_time"
        ).mapped("resolved_action")
        self.assertEqual(actions, ["check_in", "check_out"])

    def test_exact_and_near_duplicates_are_safe(self):
        line = "1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0"
        self._ingest(line)
        self._ingest(line)
        self.assertEqual(
            self.env["factory.biometric.event"].search_count(
                [("device_id", "=", self.device.id), ("device_user_id", "=", "1001")]
            ),
            1,
        )
        self._ingest("1001\t2026-08-29 08:00:10\t0\t1\t0\t0\t0", stamp="2")
        ignored = self.env["factory.biometric.event"].search(
            [("device_id", "=", self.device.id), ("state", "=", "ignored")]
        )
        self.assertEqual(len(ignored), 1)
        self.assertEqual(ignored.resolved_action, "duplicate")
        attendance = self.env["hr.attendance"].search(
            [("employee_id", "=", self.employee.id)]
        )
        self.assertFalse(attendance.check_out)

    def test_unmapped_punch_is_retained_without_attendance(self):
        self._ingest("9999\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        event = self.env["factory.biometric.event"].search(
            [("device_user_id", "=", "9999")]
        )
        self.assertEqual(event.state, "unmapped")
        self.assertFalse(event.attendance_id)

    def test_pending_device_never_changes_attendance(self):
        pending = self.env["factory.biometric.device"].create(
            {
                "name": "جهاز غير معتمد",
                "serial_number": "TEST-ZK-PENDING",
                "state": "pending",
                "timezone": "Africa/Cairo",
                "accept_events_from": fields.Datetime.to_datetime("2026-08-29 00:00:00"),
            }
        )
        self.env["factory.biometric.identity"].create(
            {
                "device_id": pending.id,
                "employee_id": self.employee.id,
                "device_user_id": "1001",
            }
        )
        self._ingest(
            "1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0", device=pending
        )
        event = pending.event_ids
        self.assertEqual(event.state, "pending_device")
        self.assertFalse(
            self.env["hr.attendance"].search_count(
                [("employee_id", "=", self.employee.id)]
            )
        )

    def test_baseline_punch_does_not_touch_payroll_attendance(self):
        self._ingest("1001\t2026-08-28 08:00:00\t0\t1\t0\t0\t0")
        event = self.device.event_ids
        self.assertEqual(event.state, "ignored")
        self.assertEqual(event.resolved_action, "baseline")
        self.assertFalse(event.attendance_id)

    def test_unmapped_historical_punch_is_audit_only_baseline(self):
        self._ingest("9998\t2026-08-28 08:00:00\t0\t1\t0\t0\t0")
        event = self.env["factory.biometric.event"].search(
            [("device_id", "=", self.device.id), ("device_user_id", "=", "9998")]
        )
        self.assertEqual(event.state, "ignored")
        self.assertEqual(event.resolved_action, "baseline")
        self.assertFalse(event.identity_id)
        self.assertFalse(event.attendance_id)

    def test_reprocessing_historical_error_restores_audit_only_baseline(self):
        event_model = self.env["factory.biometric.event"].sudo()
        values = event_model._event_values_from_line(
            self.device,
            "9996\t2026-08-28 08:00:00\t0\t1\t0\t0\t0",
            stamp="old-error",
        )
        values.update({"state": "error", "error_message": "legacy processing error"})
        event = event_model.create(values)

        event.action_reprocess()

        self.assertEqual(event.state, "ignored")
        self.assertEqual(event.resolved_action, "baseline")
        self.assertFalse(event.identity_id)
        self.assertFalse(event.employee_id)
        self.assertFalse(event.attendance_id)

    def test_uidless_push_environment_processes_mapped_employee(self):
        public_env = api.Environment(self.env.cr, None, dict(self.env.context))
        public_device = self.device.with_env(public_env)
        public_env["factory.biometric.event"].sudo()._ingest_attlog(
            public_device,
            "1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0",
            stamp="public-env",
        )
        event = self.env["factory.biometric.event"].search(
            [("device_id", "=", self.device.id), ("device_user_id", "=", "1001")]
        )
        self.assertEqual(event.state, "processed")
        self.assertEqual(event.resolved_action, "check_in")
        self.assertEqual(event.employee_id, self.employee)
        self.assertTrue(event.attendance_id)

    def test_uidless_non_sudo_ingress_remains_forbidden(self):
        public_env = api.Environment(self.env.cr, None, dict(self.env.context))
        with self.assertRaises(AccessError):
            public_env["factory.biometric.event"]._ingest_attlog(
                self.device.with_env(public_env),
                "1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0",
                stamp="public-env-no-sudo",
            )

    def test_device_command_is_allowlisted_and_acknowledged(self):
        self.identity.action_request_enrollment()
        commands = self.identity.device_id.command_ids.sorted("id")
        self.assertEqual(commands.mapped("command_type"), ["user_update", "enroll_fingerprint"])
        self.assertEqual(
            commands[0].command_text,
            "DATA UPDATE USERINFO PIN=1001\tName=عامل اختبار البصمة\tPri=0"
            "\tPasswd=\tCard=\tGrp=1\tTZ=0000000000000000\tVerify=0",
        )
        outgoing = self.env["factory.biometric.command"].sudo()._pop_for_device(self.device)
        self.assertEqual(
            outgoing,
            "C:%s:DATA UPDATE USERINFO PIN=1001\tName=عامل اختبار البصمة\tPri=0"
            "\tPasswd=\tCard=\tGrp=1\tTZ=0000000000000000\tVerify=0"
            % commands[0].command_id,
        )
        self.assertNotIn("\n", outgoing)
        self.env["factory.biometric.command"].sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % commands[0].command_id
        )
        self.assertEqual(commands[0].state, "done")
        enrollment = self.env["factory.biometric.command"].sudo()._pop_for_device(self.device)
        self.assertIn(":ENROLL_FP PIN=1001", enrollment)
        self.env["factory.biometric.command"].sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=ENROLL_FP" % commands[1].command_id
        )
        self.assertEqual(self.identity.enrollment_state, "enrolled")

    def test_ack_is_accepted_only_once_for_a_sent_command(self):
        command_model = self.env["factory.biometric.command"]
        queued = command_model.queue_user_update(self.identity)

        self.assertEqual(
            command_model.sudo()._acknowledge(
                self.device, "ID=%s&Return=0&CMD=DATA" % queued.command_id
            ),
            0,
        )
        self.assertEqual(queued.state, "queued")
        queued.action_cancel()
        self.assertEqual(
            command_model.sudo()._acknowledge(
                self.device, "ID=%s&Return=-1&CMD=DATA" % queued.command_id
            ),
            0,
        )
        self.assertEqual(queued.state, "cancelled")
        self.assertFalse(queued.return_code)

        sent = command_model.queue_user_update(self.identity)
        command_model.sudo()._pop_for_device(self.device)
        self.assertEqual(
            command_model.sudo()._acknowledge(
                self.device, "ID=%s&Return=0&CMD=DATA" % sent.command_id
            ),
            1,
        )
        acknowledged_at = sent.acknowledged_at
        response_text = sent.response_text
        self.assertEqual(sent.state, "done")
        self.assertEqual(sent.return_code, 0)

        self.assertEqual(
            command_model.sudo()._acknowledge(
                self.device, "ID=%s&Return=-9&CMD=DATA" % sent.command_id
            ),
            0,
        )
        self.assertEqual(sent.state, "done")
        self.assertEqual(sent.return_code, 0)
        self.assertEqual(sent.acknowledged_at, acknowledged_at)
        self.assertEqual(sent.response_text, response_text)

    def test_sent_command_cannot_be_cancelled(self):
        command_model = self.env["factory.biometric.command"]
        command = command_model.queue_user_update(self.identity)
        command_model.sudo()._pop_for_device(self.device)

        with self.assertRaises(ValidationError):
            command.action_cancel()
        self.assertEqual(command.state, "sent")

    def test_retry_rejects_recordset_if_any_command_is_not_terminal(self):
        command_model = self.env["factory.biometric.command"]
        failed = command_model.queue_user_update(self.identity)
        queued = command_model.queue_user_query(self.identity)
        failed.sudo().write({"state": "failed", "return_code": -9})

        with self.assertRaises(ValidationError):
            (failed | queued).action_retry()
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.return_code, -9)
        self.assertEqual(queued.state, "queued")

    def test_admin_pin_is_blocked_for_every_mutating_command(self):
        admin_employee = self.env["hr.employee"].create(
            {"name": "إسلام أدمن الجهاز", "company_id": self.env.company.id}
        )
        admin_identity = self.env["factory.biometric.identity"].create(
            {
                "device_id": self.device.id,
                "employee_id": admin_employee.id,
                "device_user_id": "1",
            }
        )
        command_model = self.env["factory.biometric.command"]

        for queue_method in (
            command_model.queue_user_update,
            command_model.queue_fingerprint_enrollment,
            command_model.queue_user_name_repair,
            command_model.queue_delete_user,
        ):
            with self.assertRaises(ValidationError):
                queue_method(admin_identity)

        self.assertFalse(
            self.device.command_ids.filtered(
                lambda command: command.identity_id == admin_identity
            )
        )

    def test_identity_free_roster_query_is_deduped_and_stores_safe_snapshot(self):
        command_model = self.env["factory.biometric.command"]
        command = command_model.queue_roster_user_query(self.device, "1")
        duplicate = command_model.queue_roster_user_query(self.device, "1")

        self.assertEqual(duplicate, command)
        self.assertFalse(command.identity_id)
        self.assertEqual(command.target_device_user_id, "1")
        self.assertEqual(command.command_type, "query_roster_user")
        self.assertEqual(command.command_text, "DATA QUERY USERINFO PIN=1")
        self.assertEqual(
            command_model.sudo()._pop_for_device(self.device),
            "C:%s:DATA QUERY USERINFO PIN=1" % command.command_id,
        )

        # A biometric record must never be mistaken for the USERINFO result or
        # copied into the command, even while the roster query is outstanding.
        self.device.sudo()._ingest_operlog(
            "FP PIN=1\tFID=0\tSize=15\tValid=1\tTMP=secret-template",
            stamp="roster-fingerprint",
        )
        self.assertFalse(command.roster_snapshot_at)

        command_model.sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % command.command_id
        )
        self.device.sudo()._ingest_operlog(
            "USER PIN=1\tName=Eslam Admin\tPri=14"
            "\tPasswd=never-store-this-password\tCard=never-store-this-card"
            "\tGrp=1\tTZ=0000000000000000\tVerify=1",
            stamp="roster-user",
        )

        self.assertTrue(command.roster_snapshot_at)
        self.assertEqual(command.roster_snapshot_name, "Eslam Admin")
        self.assertTrue(command.roster_snapshot_metadata_complete)
        self.assertTrue(command.roster_snapshot_has_password)
        self.assertNotIn("roster_snapshot_password", command._fields)
        self.assertNotIn("roster_snapshot_card", command._fields)
        safe_record = repr(command.read()[0])
        self.assertNotIn("never-store-this-password", safe_record)
        self.assertNotIn("never-store-this-card", safe_record)
        self.assertNotIn("secret-template", safe_record)
        self.assertFalse(
            self.device.identity_ids.filtered(
                lambda identity: identity.device_user_id == "1"
            )
        )

        # Once the first lookup is finished, a deliberate fresh read is valid.
        fresh = command_model.queue_roster_user_query(self.device, "1")
        self.assertNotEqual(fresh, command)

    def test_roster_query_freezes_the_first_user_snapshot(self):
        query = self._complete_roster_query("47", "First Roster Name")
        first_snapshot_at = query.roster_snapshot_at

        self.device.sudo()._ingest_operlog(
            "USER PIN=47\tName=Late Different Name\tPri=0\tPasswd=late-secret"
            "\tCard=late-card\tGrp=1\tTZ=0000000000000000\tVerify=1",
            stamp="roster-late-duplicate",
        )

        self.assertEqual(query.roster_snapshot_at, first_snapshot_at)
        self.assertEqual(query.roster_snapshot_name, "First Roster Name")
        self.assertFalse(query.roster_snapshot_has_password)
        self.assertNotIn("late-secret", repr(query.read()[0]))
        self.assertNotIn("late-card", repr(query.read()[0]))

    def test_bare_user_roster_response_does_not_require_identity(self):
        command_model = self.env["factory.biometric.command"]
        command = command_model.queue_roster_user_query(self.device, "2")
        command_model.sudo()._pop_for_device(self.device)
        command_model.sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % command.command_id
        )

        self.device.sudo()._ingest_operlog(
            "PIN=2\tName=Second Worker\tPri=0\tPasswd=\tCard="
            "\tGrp=1\tTZ=0000000000000000\tVerify=1",
            stamp="roster-pin-first",
        )

        self.assertEqual(command.roster_snapshot_name, "Second Worker")
        self.assertTrue(command.roster_snapshot_metadata_complete)
        self.assertFalse(command.roster_snapshot_has_password)
        self.assertFalse(
            self.device.identity_ids.filtered(
                lambda identity: identity.device_user_id == "2"
            )
        )

    def test_roster_query_for_linked_pin_writes_only_safe_command_fields(self):
        command_model = self.env["factory.biometric.command"]
        command = command_model.queue_roster_user_query(self.device, "1001")
        command_model.sudo()._pop_for_device(self.device)
        command_model.sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % command.command_id
        )

        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Linked Worker\tPri=0"
            "\tPasswd=linked-secret\tCard=linked-card"
            "\tGrp=1\tTZ=0000000000000000\tVerify=1",
            stamp="roster-linked-user",
        )

        self.assertEqual(command.roster_snapshot_name, "Linked Worker")
        self.assertTrue(command.roster_snapshot_has_password)
        self.assertFalse(self.identity.device_snapshot_at)
        self.assertFalse(self.identity.device_snapshot_card)
        linked_identity_record = repr(self.identity.read()[0])
        self.assertNotIn("linked-secret", linked_identity_record)
        self.assertNotIn("linked-card", linked_identity_record)

    def test_roster_query_validates_pin_and_never_accepts_raw_command_text(self):
        command_model = self.env["factory.biometric.command"]
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_query(self.device, "1\tDELETE")

        command = command_model.sudo().create(
            {
                "device_id": self.device.id,
                "command_type": "query_roster_user",
                "target_device_user_id": "2",
                "command_text": "DATA DELETE USERINFO PIN=1",
                "roster_snapshot_name": "forged",
            }
        )
        self.assertEqual(command.command_text, "DATA QUERY USERINFO PIN=2")
        self.assertFalse(command.roster_snapshot_name)
        with self.assertRaises(ValidationError):
            command.sudo().write({"command_text": "DATA DELETE USERINFO PIN=1"})

    def test_caller_command_id_is_ignored_and_generated_id_is_wire_safe(self):
        command_model = self.env["factory.biometric.command"]
        supplied = "forged:1\nC:999:DATA DELETE USERINFO PIN=1"
        command = command_model.sudo().create(
            {
                "device_id": self.device.id,
                "command_type": "query_roster_user",
                "target_device_user_id": "48",
                "command_id": supplied,
            }
        )

        self.assertNotEqual(command.command_id, supplied)
        self.assertTrue(command.command_id.isascii())
        self.assertTrue(command.command_id.isdigit())
        outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertEqual(
            outgoing,
            "C:%s:DATA QUERY USERINFO PIN=48" % command.command_id,
        )
        self.assertNotIn("\n", outgoing)
        self.assertNotIn("forged", outgoing)

    def test_legacy_malformed_command_id_fails_closed_before_dispatch(self):
        command_model = self.env["factory.biometric.command"]
        command = command_model.queue_roster_user_query(self.device, "49")
        self.env.cr.execute(
            "UPDATE factory_biometric_command SET command_id = %s WHERE id = %s",
            ("legacy:bad\ncommand", command.id),
        )
        command.invalidate_recordset(["command_id"])

        outgoing = command_model.sudo()._pop_for_device(self.device)

        self.assertEqual(outgoing, "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -995)
        self.assertEqual(command.attempt_count, 0)

    def test_roster_delete_permanently_blocks_admin_pin(self):
        command_model = self.env["factory.biometric.command"]
        self._complete_roster_query("1", "Eslam Admin")

        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "1", "Eslam Admin"
            )
        with self.assertRaises(ValidationError):
            command_model.sudo().create(
                {
                    "device_id": self.device.id,
                    "command_type": "delete_roster_user",
                    "target_device_user_id": "1",
                    "expected_roster_name": "Eslam Admin",
                }
            )
        self.assertFalse(
            self.device.command_ids.filtered(
                lambda command: command.command_type == "delete_roster_user"
            )
        )

    def test_roster_delete_rejects_active_identity_at_queue_and_dispatch(self):
        command_model = self.env["factory.biometric.command"]
        self._complete_roster_query("1001", "Linked Worker")
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "1001", "Linked Worker"
            )

        self._complete_roster_query("37", "Became Linked")
        command = command_model.queue_roster_user_delete(
            self.device, "37", "Became Linked"
        )
        employee = self.env["hr.employee"].create(
            {"name": "Newly Linked Worker", "company_id": self.env.company.id}
        )
        self.env["factory.biometric.identity"].create(
            {
                "device_id": self.device.id,
                "employee_id": employee.id,
                "device_user_id": "37",
            }
        )

        outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertEqual(outgoing, "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -997)
        self.assertNotIn("DATA DELETE", outgoing)

    def test_roster_delete_requires_fresh_complete_matching_snapshot(self):
        command_model = self.env["factory.biometric.command"]
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "38", "Worker 38")

        stale_query = self._complete_roster_query("38", "Worker 38")
        stale_query.sudo().write(
            {
                "roster_snapshot_at": fields.Datetime.now()
                - timedelta(minutes=10, seconds=1)
            }
        )
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "38", "Worker 38")

        fresh_query = self._complete_roster_query("38", "Worker 38")
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "38", "worker 38")

        fresh_query.sudo().write({"roster_snapshot_metadata_complete": False})
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "38", "Worker 38")

    def test_roster_delete_allows_explicit_empty_name_and_deduplicates(self):
        command_model = self.env["factory.biometric.command"]
        source_query = self._complete_roster_query("39", "")

        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "39")
        command = command_model.queue_roster_user_delete(self.device, "39", "")
        queued_duplicate = command_model.queue_roster_user_delete(
            self.device, "39", ""
        )

        self.assertEqual(queued_duplicate, command)
        self.assertFalse(command.identity_id)
        self.assertEqual(command.command_type, "delete_roster_user")
        self.assertEqual(command.target_device_user_id, "39")
        self.assertTrue(command.expected_roster_name_set)
        self.assertEqual(command.expected_roster_name or "", "")
        self.assertEqual(command.roster_source_query_command_id, source_query)
        self.assertEqual(command.command_text, "DATA DELETE USERINFO PIN=39")
        self.assertEqual(
            command_model.sudo()._pop_for_device(self.device),
            "C:%s:DATA DELETE USERINFO PIN=39" % command.command_id,
        )
        sent_duplicate = command_model.queue_roster_user_delete(
            self.device, "39", ""
        )
        self.assertEqual(sent_duplicate, command)

        source_query.sudo().write(
            {
                "roster_snapshot_at": fields.Datetime.now(),
                "roster_snapshot_name": "Now Named",
            }
        )
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "39", "Now Named")

    def test_roster_delete_validates_pin_identity_and_raw_command_text(self):
        command_model = self.env["factory.biometric.command"]
        source_query = self._complete_roster_query("40", "Stale User")

        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "40\tDELETE", "Stale User"
            )
        with self.assertRaises(ValidationError):
            command_model.sudo().create(
                {
                    "device_id": self.device.id,
                    "identity_id": self.identity.id,
                    "command_type": "delete_roster_user",
                    "target_device_user_id": "40",
                    "expected_roster_name": "Stale User",
                }
            )
        with self.assertRaises(ValidationError):
            command_model.sudo().create(
                {
                    "device_id": self.device.id,
                    "command_type": "delete_roster_user",
                    "target_device_user_id": "40",
                    "expected_roster_name": "Forged Name",
                }
            )

        command = command_model.sudo().create(
            {
                "device_id": self.device.id,
                "command_type": "delete_roster_user",
                "target_device_user_id": "40",
                "expected_roster_name": "Stale User",
                "expected_roster_name_set": False,
                "roster_source_query_command_id": 999999,
                "command_text": "DATA DELETE USERINFO PIN=1",
            }
        )
        self.assertEqual(command.command_text, "DATA DELETE USERINFO PIN=40")
        self.assertEqual(command.expected_roster_name, "Stale User")
        self.assertTrue(command.expected_roster_name_set)
        self.assertEqual(command.roster_source_query_command_id, source_query)
        for forged_values in (
            {"command_text": "DATA DELETE USERINFO PIN=1"},
            {"expected_roster_name": "Forged Name"},
            {"expected_roster_name_set": False},
            {"roster_source_query_command_id": False},
        ):
            with self.assertRaises(ValidationError):
                command.sudo().write(forged_values)

    def test_roster_delete_cannot_retry_and_requires_new_query_and_command(self):
        command_model = self.env["factory.biometric.command"]
        source_query = self._complete_roster_query("41", "Original User")
        command = command_model.queue_roster_user_delete(
            self.device, "41", "Original User"
        )
        command.action_cancel()

        with self.assertRaises(ValidationError):
            command.action_retry()
        self.assertEqual(command.state, "cancelled")

        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "41", "Original User"
            )

        fresh_query = self._complete_roster_query("41", "Original User")
        replacement = command_model.queue_roster_user_delete(
            self.device, "41", "Original User"
        )
        self.assertNotEqual(replacement, command)
        self.assertNotEqual(replacement.command_id, command.command_id)
        self.assertEqual(replacement.roster_source_query_command_id, fresh_query)
        self.assertNotEqual(replacement.roster_source_query_command_id, source_query)

    def test_roster_delete_source_query_is_one_shot_in_terminal_states(self):
        command_model = self.env["factory.biometric.command"]
        for pin, state in (("50", "done"), ("51", "failed"), ("52", "cancelled")):
            source_query = self._complete_roster_query(pin, "One Shot %s" % pin)
            command = command_model.queue_roster_user_delete(
                self.device, pin, "One Shot %s" % pin
            )
            command.sudo().write({"state": state})

            with self.assertRaises(ValidationError):
                command_model.queue_roster_user_delete(
                    self.device, pin, "One Shot %s" % pin
                )
            self.assertEqual(command.roster_source_query_command_id, source_query)

    def test_next_delete_requires_query_taken_after_prior_delete_send(self):
        command_model = self.env["factory.biometric.command"]
        first_source = self._complete_roster_query("53", "Sequential 53")
        first_delete = command_model.queue_roster_user_delete(
            self.device, "53", "Sequential 53"
        )
        pre_send_query = self._record_completed_roster_query(
            "53", "Sequential 53"
        )
        pre_send_query.sudo().write(
            {"roster_snapshot_at": fields.Datetime.now() - timedelta(seconds=5)}
        )

        first_outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertEqual(
            first_outgoing,
            "C:%s:DATA DELETE USERINFO PIN=53" % first_delete.command_id,
        )
        command_model.sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % first_delete.command_id
        )
        first_delete.sudo().write(
            {"sent_at": fields.Datetime.now() - timedelta(seconds=2)}
        )

        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "53", "Sequential 53"
            )

        post_send_query = self._complete_roster_query("53", "Sequential 53")
        replacement = command_model.queue_roster_user_delete(
            self.device, "53", "Sequential 53"
        )
        self.assertNotEqual(replacement, first_delete)
        self.assertEqual(replacement.roster_source_query_command_id, post_send_query)
        self.assertNotEqual(replacement.roster_source_query_command_id, first_source)

    def test_roster_delete_pop_fails_closed_when_queued_snapshot_is_stale(self):
        command_model = self.env["factory.biometric.command"]
        source_query = self._complete_roster_query("42", "Stale Before Send")
        command = command_model.queue_roster_user_delete(
            self.device, "42", "Stale Before Send"
        )
        source_query.sudo().write(
            {
                "roster_snapshot_at": fields.Datetime.now()
                - timedelta(minutes=10, seconds=1)
            }
        )

        outgoing = command_model.sudo()._pop_for_device(self.device)

        self.assertEqual(outgoing, "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -997)
        self.assertEqual(command.attempt_count, 0)
        self.assertNotIn("Stale Before Send", command.response_text)
        self.assertNotIn("DATA DELETE", outgoing)

    def test_roster_delete_pop_fails_closed_on_newer_mismatched_snapshot(self):
        command_model = self.env["factory.biometric.command"]
        self._complete_roster_query("43", "Original 43")
        command = command_model.queue_roster_user_delete(
            self.device, "43", "Original 43"
        )
        self._record_completed_roster_query("43", "Reused 43")

        outgoing = command_model.sudo()._pop_for_device(self.device)

        self.assertEqual(outgoing, "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -997)
        self.assertNotIn("DATA DELETE", outgoing)

    def test_roster_delete_pop_sends_with_fresh_matching_snapshot(self):
        command_model = self.env["factory.biometric.command"]
        self._complete_roster_query("44", "Fresh 44")
        command = command_model.queue_roster_user_delete(
            self.device, "44", "Fresh 44"
        )

        outgoing = command_model.sudo()._pop_for_device(self.device)

        self.assertEqual(
            outgoing,
            "C:%s:DATA DELETE USERINFO PIN=44" % command.command_id,
        )
        self.assertEqual(command.state, "sent")
        self.assertEqual(command.attempt_count, 1)

    def test_roster_delete_missing_ack_never_auto_resends(self):
        command_model = self.env["factory.biometric.command"]
        self._complete_roster_query("45", "Fresh 45")
        command = command_model.queue_roster_user_delete(
            self.device, "45", "Fresh 45"
        )
        first_outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertIn(":DATA DELETE USERINFO PIN=45", first_outgoing)

        command.sudo().write(
            {"sent_at": fields.Datetime.now() - timedelta(seconds=31)}
        )
        retry_outgoing = command_model.sudo()._pop_for_device(self.device)

        self.assertEqual(retry_outgoing, "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -996)
        self.assertEqual(command.attempt_count, 1)
        self.assertEqual(
            command_model.sudo()._acknowledge(
                self.device, "ID=%s&Return=0&CMD=DATA" % command.command_id
            ),
            0,
        )
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -996)
        with self.assertRaises(ValidationError):
            command.action_retry()

        fresh_query = self._complete_roster_query("45", "Fresh 45")
        replacement = command_model.queue_roster_user_delete(
            self.device, "45", "Fresh 45"
        )
        replacement_outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertEqual(
            replacement_outgoing,
            "C:%s:DATA DELETE USERINFO PIN=45" % replacement.command_id,
        )
        self.assertNotEqual(replacement.command_id, command.command_id)
        self.assertEqual(replacement.roster_source_query_command_id, fresh_query)

    def test_identity_delete_is_non_replayable(self):
        command_model = self.env["factory.biometric.command"]
        cancelled = command_model.queue_delete_user(self.identity)
        cancelled.action_cancel()
        with self.assertRaises(ValidationError):
            cancelled.action_retry()

        sent = command_model.queue_delete_user(self.identity)
        first_outgoing = command_model.sudo()._pop_for_device(self.device)
        self.assertIn(":DATA DELETE USERINFO PIN=1001", first_outgoing)
        sent.sudo().write(
            {"sent_at": fields.Datetime.now() - timedelta(seconds=31)}
        )
        self.assertEqual(command_model.sudo()._pop_for_device(self.device), "OK")
        self.assertEqual(sent.state, "failed")
        self.assertEqual(sent.return_code, -996)
        self.assertEqual(sent.attempt_count, 1)
        with self.assertRaises(ValidationError):
            sent.action_retry()

    def test_identity_unlink_deletes_device_user_and_biometric_history_only(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        self._ingest(
            "1001\t2026-08-29 09:00:00\t0\t1\t0\t0\t0", stamp="purge-out"
        )
        biometric_attendance = self.env["hr.attendance"].search(
            [
                ("employee_id", "=", self.employee.id),
                ("in_mode", "=", "biometric"),
            ]
        )
        manual_attendance = self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": fields.Datetime.to_datetime("2026-08-30 08:00:00"),
                "check_out": fields.Datetime.to_datetime("2026-08-30 09:00:00"),
                "in_mode": "manual",
                "out_mode": "manual",
            }
        )
        identity_id = self.identity.id

        self.identity.unlink()

        self.assertFalse(
            self.env["factory.biometric.identity"].browse(identity_id).exists()
        )
        self.assertFalse(biometric_attendance.exists())
        self.assertTrue(manual_attendance.exists())
        self.assertFalse(
            self.env["factory.biometric.event"].search_count(
                [
                    ("device_id", "=", self.device.id),
                    ("device_user_id", "=", "1001"),
                ]
            )
        )
        command = self.env["factory.biometric.command"].search(
            [
                ("device_id", "=", self.device.id),
                ("command_type", "=", "delete_user"),
                ("target_device_user_id", "=", "1001"),
            ]
        )
        self.assertEqual(len(command), 1)
        self.assertFalse(command.identity_id)
        self.assertEqual(command.command_text, "DATA DELETE USERINFO PIN=1001")
        self.assertEqual(
            self.env["factory.biometric.command"].sudo()._pop_for_device(
                self.device
            ),
            "C:%s:DATA DELETE USERINFO PIN=1001" % command.command_id,
        )

    def test_employee_unlink_queues_device_delete_and_purges_history(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        employee_id = self.employee.id
        identity_id = self.identity.id

        self.employee.unlink()

        self.assertFalse(self.env["hr.employee"].browse(employee_id).exists())
        self.assertFalse(
            self.env["factory.biometric.identity"].browse(identity_id).exists()
        )
        self.assertFalse(
            self.env["factory.biometric.event"].search_count(
                [
                    ("device_id", "=", self.device.id),
                    ("device_user_id", "=", "1001"),
                ]
            )
        )
        command = self.env["factory.biometric.command"].search(
            [
                ("device_id", "=", self.device.id),
                ("command_type", "=", "delete_user"),
                ("target_device_user_id", "=", "1001"),
            ]
        )
        self.assertEqual(len(command), 1)
        self.assertFalse(command.identity_id)

    def test_admin_identity_unlink_is_blocked_before_history_purge(self):
        admin_employee = self.env["hr.employee"].create(
            {"name": "أدمن جهاز الاختبار", "company_id": self.env.company.id}
        )
        admin_identity = self.env["factory.biometric.identity"].create(
            {
                "device_id": self.device.id,
                "employee_id": admin_employee.id,
                "device_user_id": "1",
            }
        )
        self._ingest("1\t2026-08-29 10:00:00\t0\t1\t0\t0\t0", stamp="admin")
        event = self.env["factory.biometric.event"].search(
            [
                ("device_id", "=", self.device.id),
                ("device_user_id", "=", "1"),
            ]
        )

        with self.assertRaises(ValidationError):
            admin_identity.unlink()

        self.assertTrue(admin_identity.exists())
        self.assertTrue(event.exists())
        self.assertTrue(event.attendance_id.exists())
        self.assertFalse(
            self.env["factory.biometric.command"].search_count(
                [
                    ("device_id", "=", self.device.id),
                    ("command_type", "=", "delete_user"),
                    ("target_device_user_id", "=", "1"),
                ]
            )
        )

    def test_identity_unlink_reuses_pending_device_delete(self):
        command_model = self.env["factory.biometric.command"]
        first = command_model.queue_delete_user(self.identity)

        self.identity.unlink()

        commands = command_model.search(
            [
                ("device_id", "=", self.device.id),
                ("command_type", "=", "delete_user"),
                ("target_device_user_id", "=", "1001"),
            ]
        )
        self.assertEqual(commands, first)
        self.assertFalse(first.identity_id)

    def test_roster_delete_dedup_rejects_different_or_missing_confirmation(self):
        command_model = self.env["factory.biometric.command"]
        source_query = self._complete_roster_query("46", "Named 46")
        command = command_model.queue_roster_user_delete(
            self.device, "46", "Named 46"
        )

        source_query.sudo().write(
            {
                "roster_snapshot_at": fields.Datetime.now(),
                "roster_snapshot_name": "",
            }
        )
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(self.device, "46", "")

        source_query.sudo().unlink()
        self._record_completed_roster_query("46", "Named 46")
        with self.assertRaises(ValidationError):
            command_model.queue_roster_user_delete(
                self.device, "46", "Named 46"
            )
        self.assertFalse(command.roster_source_query_command_id)
        self.assertEqual(command_model.sudo()._pop_for_device(self.device), "OK")
        self.assertEqual(command.state, "failed")
        self.assertEqual(command.return_code, -997)

    def test_legacy_arabic_device_sends_names_as_windows_1256_bytes(self):
        self.device.command_encoding = "windows-1256"
        self.identity.device_user_name = "محمد شنشن"
        self.identity.action_queue_user_sync()

        outgoing = self.env["factory.biometric.command"].sudo()._pop_for_device(
            self.device
        )
        encoded = self.device._encode_push_command(outgoing)
        expected_name = "محمد شنشن".encode("windows-1256")

        self.assertIn(b"\tName=" + expected_name + b"\tPri=0", encoded)
        self.assertNotIn("محمد شنشن".encode("utf-8"), encoded)
        response = ZKTecoPushController()._response(
            encoded, charset=self.device._push_command_charset()
        )
        self.assertEqual(response.get_data(), encoded)
        self.assertEqual(
            response.headers["Content-Type"], "text/plain; charset=windows-1256"
        )

    def test_utf8_device_keeps_standard_push_encoding(self):
        self.identity.device_user_name = "محمد شنشن"
        self.identity.action_queue_user_sync()
        outgoing = self.env["factory.biometric.command"].sudo()._pop_for_device(
            self.device
        )
        command = self.device.command_ids
        expected = (
            "C:%s:DATA UPDATE USERINFO PIN=1001\tName=محمد شنشن\tPri=0"
            "\tPasswd=\tCard=\tGrp=1\tTZ=0000000000000000\tVerify=0"
            % command.command_id
        ).encode("utf-8")

        self.assertEqual(self.device._encode_push_command(outgoing), expected)
        self.assertEqual(expected, outgoing.encode("utf-8"))
        self.assertEqual(self.device._encode_push_command("OK"), b"OK")

    def test_name_repair_queries_and_preserves_device_user_snapshot(self):
        self.identity.device_user_name = "اياد سالم"
        self.identity.action_query_device_user()
        query = self.device.command_ids
        self.assertEqual(query.command_type, "query_user")
        self.assertEqual(query.command_text, "DATA QUERY USERINFO PIN=1001")
        outgoing = self.env["factory.biometric.command"].sudo()._pop_for_device(
            self.device
        )
        self.assertEqual(
            outgoing,
            "C:%s:DATA QUERY USERINFO PIN=1001" % query.command_id,
        )
        self.env["factory.biometric.command"].sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % query.command_id
        )
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=ÇíÇÏ ÓÇáã\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=8899",
            stamp="snapshot-1",
        )
        self.assertTrue(self.identity.device_snapshot_complete)
        self.assertEqual(self.identity.device_snapshot_query_command_id, query)
        self.assertEqual(self.identity.device_snapshot_name, "ÇíÇÏ ÓÇáã")
        self.assertEqual(self.identity.device_user_name, "اياد سالم")
        self.assertFalse(self.identity.device_snapshot_has_password)
        self.assertEqual(self.identity.device_snapshot_privilege, "0")
        self.assertEqual(self.identity.device_snapshot_card, "7788")
        self.assertEqual(self.identity.device_snapshot_group, "2")
        self.assertEqual(self.identity.device_snapshot_timezone, "0100000000000000")
        self.assertEqual(self.identity.device_snapshot_verify, "1")
        self.assertTrue(self.identity.device_snapshot_vice_card_present)
        self.assertEqual(self.identity.device_snapshot_vice_card, "8899")

        self.identity.action_repair_device_user_name()
        repair = self.device.command_ids.filtered(
            lambda command: command.command_type == "user_name_repair"
        )
        self.assertEqual(
            repair.command_text,
            "DATA UPDATE USERINFO PIN=1001\tName=اياد سالم\tPri=0\tPasswd="
            "\tCard=7788\tGrp=2\tTZ=0100000000000000\tVerify=1"
            "\tViceCard=8899",
        )

    def test_pin_first_query_response_is_narrowly_recognized_as_userinfo(self):
        self.identity.device_user_name = "اياد سالم"
        query = self._dispatch_user_query()

        self.device.sudo()._ingest_operlog(
            "FP PIN=1001\tFID=0\tSize=12\tValid=1\tTMP=secret-template",
            stamp="snapshot-fp",
        )
        self.assertFalse(self.identity.device_snapshot_at)
        self.assertNotIn("device_snapshot_password", self.identity._fields)

        self.device.sudo()._ingest_operlog(
            "PIN=1001\tName=ÇíÇÏ ÓÇáã\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=8899",
            stamp="snapshot-pin-first",
        )
        self.assertTrue(self.identity.device_snapshot_complete)
        self.assertEqual(self.identity.device_snapshot_query_command_id, query)
        self.assertEqual(self.identity.device_snapshot_name, "ÇíÇÏ ÓÇáã")
        self.assertEqual(self.identity.device_user_name, "اياد سالم")

    def test_oversized_fingerprint_template_is_discarded_without_storage(self):
        template = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "FINGERTMP PIN=1001\tFID=0\tSize=12000\tValid=1\tTMP=" + template,
            stamp="oversized-fp",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "enrolled")
        self.assertNotIn("template", self.identity._fields)
        self.assertNotIn(template, repr(self.identity.read()[0]))

    def test_oversized_non_template_operlog_line_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.device.sudo()._ingest_operlog(
                "USER PIN=1001\tName=" + ("A" * 12000),
                stamp="oversized-user",
            )

    def test_oversized_invalid_fingerprint_is_discarded_without_enrollment(self):
        template = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "FINGERTMP PIN=1001\tFID=0\tSize=12000\tValid=0\tTMP=" + template,
            stamp="oversized-invalid-fp",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "not_enrolled")
        self.assertFalse(self.identity.last_enrollment_at)

    def test_oversized_malformed_fingerprint_record_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.device.sudo()._ingest_operlog(
                "FINGERTMP PIN=1001\tTMP=" + ("A" * 12000),
                stamp="oversized-malformed-fp",
            )

    def test_oversized_face_template_is_discarded_without_fp_enrollment(self):
        template = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "FACE PIN=1001\tFID=0\tSize=12000\tValid=1\tTMP=" + template,
            stamp="oversized-face",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "not_enrolled")
        self.assertFalse(self.identity.last_enrollment_at)
        self.assertNotIn(template, repr(self.identity.read()[0]))

    def test_oversized_finger_vein_is_discarded_without_fp_enrollment(self):
        template = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "FVEIN Pin=1001 FID=0 Index=2 Size=12000 Valid=1 Tmp=" + template,
            stamp="oversized-fvein",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "not_enrolled")
        self.assertFalse(self.identity.last_enrollment_at)
        self.assertNotIn(template, repr(self.identity.read()[0]))

    def test_oversized_user_photo_is_discarded_without_storage(self):
        content = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "USERPIC\tPIN=1001\tFileName=1001.jpg\tSize=12000\tContent=" + content,
            stamp="oversized-userpic",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "not_enrolled")
        self.assertNotIn(content, repr(self.identity.read()[0]))

    def test_oversized_comparison_photo_is_discarded_without_storage(self):
        content = "A" * 12000

        processed = self.device.sudo()._ingest_operlog(
            "BIOPHOTO PIN=1001 FileName=1001.jpg Type=9 Size=12000 Content="
            + content,
            stamp="oversized-biophoto",
        )

        self.assertEqual(processed, 1)
        self.assertEqual(self.identity.enrollment_state, "not_enrolled")
        self.assertNotIn(content, repr(self.identity.read()[0]))

    def test_unknown_oversized_biometric_record_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.device.sudo()._ingest_operlog(
                "BIODATA PIN=1001\tSize=12000\tTmp=" + ("A" * 12000),
                stamp="oversized-biodata",
            )

    def test_name_repair_does_not_clear_unreported_optional_vice_card(self):
        self.identity.device_user_name = "اياد سالم"
        self._dispatch_user_query()
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Broken\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1",
            stamp="snapshot-without-vice-card",
        )

        self.assertTrue(self.identity.device_snapshot_complete)
        self.assertFalse(self.identity.device_snapshot_vice_card_present)
        self.identity.action_repair_device_user_name()
        repair = self.device.command_ids.filtered(
            lambda command: command.command_type == "user_name_repair"
        )
        self.assertNotIn("ViceCard=", repair.command_text)

    def test_user_snapshot_records_only_password_presence(self):
        self._dispatch_user_query()
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Test\tPri=0\tPasswd=do-not-store-me\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=",
            stamp="snapshot-password",
        )

        self.assertTrue(self.identity.device_snapshot_complete)
        self.assertTrue(self.identity.device_snapshot_has_password)
        self.assertNotIn("device_snapshot_password", self.identity._fields)
        self.assertNotIn("do-not-store-me", repr(self.identity.read()[0]))

    def test_name_repair_requires_bound_successful_query_ack(self):
        self.identity.device_user_name = "اياد سالم"
        query = self._dispatch_user_query(acknowledge=False)
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Broken\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=",
            stamp="snapshot-before-query-ack",
        )
        self.assertEqual(self.identity.device_snapshot_query_command_id, query)
        self.assertTrue(self.identity.device_snapshot_complete)
        with self.assertRaises(ValidationError):
            self.identity.action_repair_device_user_name()

        self.env["factory.biometric.command"].sudo()._acknowledge(
            self.device, "ID=%s&Return=0&CMD=DATA" % query.command_id
        )
        self.identity.action_repair_device_user_name()
        self.assertTrue(
            self.device.command_ids.filtered(
                lambda command: command.command_type == "user_name_repair"
            )
        )

    def test_name_repair_rejects_snapshot_when_a_newer_query_exists(self):
        self.identity.device_user_name = "اياد سالم"
        query = self._dispatch_user_query()
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Broken\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=",
            stamp="snapshot-before-new-query",
        )
        self.assertEqual(self.identity.device_snapshot_query_command_id, query)
        self.identity.action_query_device_user()

        with self.assertRaises(ValidationError):
            self.identity.action_repair_device_user_name()

    def test_name_repair_rejects_snapshot_older_than_bound_query_send(self):
        self.identity.device_user_name = "اياد سالم"
        query = self._dispatch_user_query()
        self.device.sudo()._ingest_operlog(
            "USER PIN=1001\tName=Broken\tPri=0\tPasswd=\tCard=7788"
            "\tGrp=2\tTZ=0100000000000000\tVerify=1\tViceCard=",
            stamp="snapshot-invalid-order",
        )
        self.identity.sudo().write(
            {"device_snapshot_at": query.sent_at - timedelta(seconds=1)}
        )

        with self.assertRaises(ValidationError):
            self.identity.action_repair_device_user_name()

    def test_name_repair_blocks_password_and_admin_pin(self):
        self.identity.sudo().write(
            {
                "device_snapshot_complete": True,
                "device_snapshot_has_password": True,
            }
        )
        with self.assertRaises(ValidationError):
            self.identity.action_repair_device_user_name()

        admin_employee = self.env["hr.employee"].create(
            {"name": "أدمن اختبار", "company_id": self.env.company.id}
        )
        admin_identity = self.env["factory.biometric.identity"].create(
            {
                "device_id": self.device.id,
                "employee_id": admin_employee.id,
                "device_user_id": "1",
                "device_snapshot_complete": True,
            }
        )
        with self.assertRaises(ValidationError):
            admin_identity.action_repair_device_user_name()

    def test_name_repair_rejects_stale_or_forged_snapshot(self):
        self.identity.sudo().write(
            {
                "device_snapshot_at": fields.Datetime.now() - timedelta(minutes=11),
                "device_snapshot_complete": True,
                "device_snapshot_privilege": "0",
                "device_snapshot_has_password": False,
                "device_snapshot_card": "7788",
                "device_snapshot_group": "2",
                "device_snapshot_timezone": "0100000000000000",
                "device_snapshot_verify": "1",
            }
        )
        with self.assertRaises(ValidationError):
            self.identity.action_repair_device_user_name()

        with self.assertRaises(AccessError):
            self.identity.with_user(self.env.ref("base.user_admin")).write(
                {"device_snapshot_complete": True}
            )

    def test_legacy_encoding_normalizes_decomposed_arabic(self):
        self.device.command_encoding = "windows-1256"
        decomposed_name = "ا\u0654حمد"

        self.assertEqual(
            self.device._encode_push_command(decomposed_name),
            "أحمد".encode("windows-1256"),
        )

    def test_operlog_cp1256_fallback_preserves_arabic_name(self):
        controller = ZKTecoPushController()
        raw = "USER PIN=28\tName=محمد شنشن\tPri=0".encode("windows-1256")

        self.assertEqual(
            controller._decode_payload(raw, fallback_charset="windows-1256"),
            "USER PIN=28\tName=محمد شنشن\tPri=0",
        )

    def test_payload_decoding_rejects_invalid_bytes_instead_of_replacing(self):
        controller = ZKTecoPushController()

        with self.assertRaises(ValidationError):
            controller._decode_payload(b"\xff", fallback_charset=None)
        with self.assertRaises(ValidationError):
            controller._decode_payload(b"\xff", fallback_charset="ascii")

    def test_legacy_encoding_rejects_unsupported_user_name_at_queue_time(self):
        self.device.command_encoding = "windows-1256"
        self.identity.device_user_name = "عامل 🧵"

        with self.assertRaises(ValidationError):
            self.identity.action_queue_user_sync()
        self.assertFalse(self.device.command_ids)

    def test_encoding_cannot_change_while_command_is_pending(self):
        self.identity.action_queue_user_sync()

        with self.assertRaises(ValidationError):
            self.device.command_encoding = "windows-1256"

    def test_unapproved_verify_modes_are_rejected(self):
        for mode in ("0", "2", "3", "999", ""):
            self._ingest("1001\t2026-08-29 08:00:00\t0\t%s\t0\t0\t0" % mode)
        events = self.device.event_ids
        self.assertEqual(len(events), 5)
        self.assertEqual(set(events.mapped("state")), {"error"})
        self.assertFalse(events.mapped("attendance_id"))

    def test_explicit_fingerprint_only_device_keeps_terminal_restriction(self):
        self.device.allowed_verify_modes = "1"
        command = self.env["factory.biometric.command"].queue_user_update(self.identity)
        self.assertTrue(command.command_text.endswith("\tVerify=1"))

    def test_fingerprint_card_face_pair_interchangeably(self):
        self.assertEqual(self.device._allowed_verify_mode_set(), {"1", "4", "15"})
        day = fields.Date.today() - timedelta(days=10)
        self.device.accept_events_from = fields.Datetime.to_datetime(day)
        for in_mode in ("1", "4", "15"):
            for out_mode in ("1", "4", "15"):
                with self.subTest(in_mode=in_mode, out_mode=out_mode):
                    self._ingest(
                        "1001\t%s 08:00:00\t0\t%s\t0\t0\t0" % (day, in_mode)
                    )
                    check_in = self.device.event_ids.sorted("id")[-1]
                    attendance = check_in.attendance_id
                    self.assertEqual(check_in.resolved_action, "check_in")
                    self.assertEqual(attendance.employee_id, self.employee)
                    self.assertFalse(attendance.check_out)

                    # The next punch may use any method, even with status=0:
                    # pairing follows the employee's open attendance, not mode.
                    self._ingest(
                        "1001\t%s 18:00:00\t0\t%s\t0\t0\t0" % (day, out_mode)
                    )
                    check_out = self.device.event_ids.sorted("id")[-1]
                    self.assertEqual(check_out.resolved_action, "check_out")
                    self.assertEqual(check_out.attendance_id, attendance)
                    self.assertEqual(attendance.in_mode, "biometric")
                    self.assertEqual(attendance.out_mode, "biometric")
                    self.assertEqual(
                        attendance.check_out - attendance.check_in, timedelta(hours=10)
                    )
                day += timedelta(days=1)

    def test_switching_verify_method_still_debounces_duplicate(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        self._ingest("1001\t2026-08-29 08:00:10\t0\t4\t0\t0\t0")
        self._ingest("1001\t2026-08-29 08:00:20\t0\t15\t0\t0\t0")
        events = self.device.event_ids.sorted("id")
        self.assertEqual(events.mapped("state"), ["processed", "ignored", "ignored"])
        self.assertEqual(events.mapped("resolved_action"), ["check_in", "duplicate", "duplicate"])
        self.assertEqual(len(events.mapped("attendance_id")), 1)
        self.assertFalse(events.attendance_id.check_out)

    def test_five_minute_gate_exact_boundary_both_directions_all_methods(self):
        self.assertEqual(
            self.env["factory.biometric.device"].default_get(["debounce_seconds"])["debounce_seconds"],
            300,
        )
        self.device.debounce_seconds = 300
        day = fields.Date.today() - timedelta(days=10)
        self.device.accept_events_from = fields.Datetime.to_datetime(day)
        for in_mode in ("1", "4", "15"):
            for out_mode in ("1", "4", "15"):
                with self.subTest(in_mode=in_mode, out_mode=out_mode):
                    punches = [
                        ("09:00:00", in_mode, "check_in"),
                        ("09:01:00", out_mode, "duplicate"),
                        ("09:04:59", "15", "duplicate"),
                        ("09:05:00", out_mode, "check_out"),
                        ("09:05:01", in_mode, "duplicate"),
                        ("09:09:59", "4", "duplicate"),
                        ("09:10:00", in_mode, "check_in"),
                        ("09:15:00", out_mode, "check_out"),
                    ]
                    accepted = self.env["factory.biometric.event"]
                    for time, mode, expected in punches:
                        self._ingest("1001\t%s %s\t0\t%s\t0\t0\t0" % (day, time, mode))
                        event = self.device.event_ids.sorted("id")[-1]
                        self.assertEqual(event.resolved_action, expected)
                        self.assertEqual(event.state, "ignored" if expected == "duplicate" else "processed")
                        if expected != "duplicate":
                            accepted |= event
                    self.assertEqual(len(accepted.attendance_id), 2)
                    for attendance in accepted.attendance_id:
                        self.assertEqual(attendance.check_out - attendance.check_in, timedelta(minutes=5))
                day += timedelta(days=1)

    def test_five_minute_gate_respects_manual_and_corrected_attendance(self):
        self.device.debounce_seconds = 300
        attendance = self.env["hr.attendance"].create({
            "employee_id": self.employee.id,
            "check_in": "2026-08-29 06:00:00",  # 09:00 Cairo
            "in_mode": "manual",
        })
        self._ingest("1001\t2026-08-29 09:04:59\t0\t4\t0\t0\t0")
        self.assertEqual(self.device.event_ids.state, "ignored")
        self.assertFalse(attendance.check_out)
        self._ingest("1001\t2026-08-29 09:05:00\t0\t15\t0\t0\t0")
        self.assertEqual(attendance.check_out, fields.Datetime.to_datetime("2026-08-29 06:05:00"))
        attendance.write({"check_out": "2026-08-29 06:30:00"})
        self._ingest("1001\t2026-08-29 09:34:59\t0\t1\t0\t0\t0")
        self.assertEqual(self.device.event_ids.sorted("id")[-1].state, "ignored")
        self._ingest("1001\t2026-08-29 09:35:00\t0\t4\t0\t0\t0")
        latest = self.device.event_ids.sorted("id")[-1]
        self.assertEqual(latest.resolved_action, "check_in")
        self.assertNotEqual(latest.attendance_id, attendance)

    def test_five_minute_gate_is_per_employee_not_per_device(self):
        self.device.debounce_seconds = 300
        other_employee = self.env["hr.employee"].create({"name": "عامل آخر لمنع التكرار"})
        self.env["factory.biometric.identity"].create({
            "device_id": self.device.id,
            "employee_id": other_employee.id,
            "device_user_id": "1002",
        })
        self._ingest("1001\t2026-08-29 09:00:00\t0\t1\t0\t0\t0")
        self._ingest("1002\t2026-08-29 09:00:01\t0\t4\t0\t0\t0")
        self.assertEqual(self.device.event_ids.mapped("state"), ["processed", "processed"])
        self.assertEqual(self.device.event_ids.mapped("resolved_action"), ["check_in", "check_in"])

    def test_ignored_card_attempts_stay_excluded_after_modes_change(self):
        self.device.allowed_verify_modes = "1"
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        attendance = self.device.event_ids.attendance_id
        card_lines = (
            "1001\t2026-08-29 10:00:00\t0\t4\t0\t0\t0\n"
            "1001\t2026-08-29 10:01:00\t0\t4\t0\t0\t0"
        )
        self._ingest(card_lines)
        excluded = self.device.event_ids.filtered(lambda event: event.verify_mode == "4")
        self.assertEqual(excluded.mapped("state"), ["error", "error"])
        excluded.sudo().write({"state": "ignored", "error_message": "Excluded by user request"})
        self.device.allowed_verify_modes = "1,4,15"
        excluded.action_reprocess()
        self._ingest(card_lines)  # A device retry must also remain audit-only.
        self.assertEqual(excluded.mapped("state"), ["ignored", "ignored"])
        self.assertFalse(excluded.mapped("attendance_id"))
        self.assertFalse(attendance.check_out)
        self.assertEqual(len(self.device.event_ids), 3)

        # Only the historical attempts are excluded, not this employee's future card punches.
        self._ingest("1001\t2026-08-29 18:00:00\t0\t4\t0\t0\t0")
        latest = self.device.event_ids.sorted("id")[-1]
        self.assertEqual(latest.resolved_action, "check_out")
        self.assertEqual(latest.attendance_id, attendance)

    def test_enabling_methods_does_not_replay_historical_errors(self):
        self.device.allowed_verify_modes = "1"
        self._ingest("1001\t2026-08-29 08:00:00\t0\t4\t0\t0\t0")
        event = self.device.event_ids
        self.assertEqual(event.state, "error")
        self.device.allowed_verify_modes = "1,4,15"
        self.assertEqual(event.state, "error")
        self.assertFalse(event.attendance_id)

    def test_card_and_face_still_require_employee_mapping(self):
        for mode in ("4", "15"):
            self._ingest("9999\t2026-08-29 08:00:00\t0\t%s\t0\t0\t0" % mode)
        events = self.device.event_ids
        self.assertEqual(events.mapped("state"), ["unmapped", "unmapped"])
        self.assertFalse(events.mapped("attendance_id"))

    def test_cross_device_near_duplicate_does_not_checkout(self):
        second_device = self.env["factory.biometric.device"].create(
            {
                "name": "بصمة اختبار ثانية",
                "serial_number": "TEST-ZK-002",
                "state": "active",
                "expected_source_ip": "127.0.0.2",
                "timezone": "Africa/Cairo",
                "accept_events_from": fields.Datetime.to_datetime(
                    "2026-08-29 00:00:00"
                ),
                "debounce_seconds": 30,
            }
        )
        self.env["factory.biometric.identity"].create(
            {
                "device_id": second_device.id,
                "employee_id": self.employee.id,
                "device_user_id": "2001",
            }
        )
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        self._ingest(
            "2001\t2026-08-29 08:00:10\t0\t1\t0\t0\t0",
            device=second_device,
        )
        attendance = self.env["hr.attendance"].search(
            [("employee_id", "=", self.employee.id)]
        )
        self.assertFalse(attendance.check_out)
        duplicate = second_device.event_ids
        self.assertEqual(duplicate.state, "ignored")
        self.assertEqual(duplicate.resolved_action, "duplicate")

    def test_historical_mapping_is_immutable(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        with self.assertRaises(ValidationError):
            self.identity.write({"device_user_id": "1002"})

    def test_command_retry_is_rate_limited(self):
        self.identity.action_queue_user_sync()
        command_model = self.env["factory.biometric.command"].sudo()
        first = command_model._pop_for_device(self.device)
        self.assertIn(":DATA UPDATE USERINFO", first)
        self.assertEqual(command_model._pop_for_device(self.device), "OK")
        command = self.device.command_ids
        command.sudo().write(
            {"sent_at": fields.Datetime.now() - timedelta(seconds=31)}
        )
        retry = command_model._pop_for_device(self.device)
        self.assertEqual(retry, first)
        self.assertEqual(command.attempt_count, 2)

    def test_manual_attendance_change_preserves_audit_event(self):
        self._ingest("1001\t2026-08-29 08:00:00\t0\t1\t0\t0\t0")
        attendance = self.env["hr.attendance"].search(
            [("employee_id", "=", self.employee.id)]
        )
        attendance.write(
            {"check_in": fields.Datetime.to_datetime("2026-08-29 05:01:00")}
        )
        event = self.device.event_ids
        self.assertEqual(event.state, "corrected")
        self.assertEqual(event.attendance_id, attendance)
