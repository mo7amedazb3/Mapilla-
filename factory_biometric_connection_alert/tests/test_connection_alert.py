from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..hooks import DATABASE_PARAMETER, post_init_hook, uninstall_hook


@tagged("post_install", "-at_install")
class TestBiometricConnectionAlert(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref("base.user_admin")
        cls.operator = new_test_user(
            cls.env(context=dict(cls.env.context, no_reset_password=True)),
            login="biometric_alert_operator", groups="base.group_user"
        )
        cls.now = fields.Datetime.now()
        cls.device = cls.env["factory.biometric.device"].sudo().create({
            "name": "Connection alert test",
            "serial_number": "ALERT-TEST-ONLY",
            "state": "active",
            "expected_source_ip": "127.0.0.1",
            "company_id": cls.admin.company_id.id,
            "last_seen": cls.now - timedelta(seconds=121),
        })

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(DATABASE_PARAMETER, self.env.cr.dbname)

    def status(self, user=None, companies=None):
        model = self.env["factory.biometric.device"].with_user(user or self.admin)
        model = model.with_context(allowed_company_ids=companies or [self.admin.company_id.id])
        with patch("odoo.addons.factory_biometric_connection_alert.models.biometric_device.fields.Datetime.now", return_value=self.now):
            return model.get_connection_alert_status()

    def row(self, result):
        return next((row for row in result["devices"] if row["id"] == self.device.id), None)

    def test_offline_payload_has_no_sensitive_data(self):
        result = self.status()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["offline_after_seconds"], 120)
        self.assertEqual(result["poll_seconds"], 15)
        self.assertEqual(set(self.row(result)), {"id", "name", "last_seen", "reason"})
        self.assertEqual(self.row(result)["reason"], "offline")

    def test_threshold_and_recovery(self):
        self.device.write({"last_seen": self.now - timedelta(seconds=119)})
        self.assertFalse(self.row(self.status()))
        self.device.write({"last_seen": self.now - timedelta(seconds=120)})
        self.assertTrue(self.row(self.status()))
        self.device.write({"last_seen": self.now})
        self.assertFalse(self.row(self.status()))

    def test_never_connected_grace_period(self):
        self.device.write({"last_seen": False})
        self.now = self.device.create_date + timedelta(seconds=119)
        self.assertFalse(self.row(self.status()))
        self.now = self.device.create_date + timedelta(seconds=120)
        row = self.row(self.status())
        self.assertEqual(row["reason"], "never_connected")
        self.assertFalse(row["last_seen"])

    def test_blocked_pending_and_archived_not_monitored(self):
        self.device.write({"state": "blocked"})
        self.assertFalse(self.row(self.status()))
        self.device.write({"state": "pending"})
        self.assertFalse(self.row(self.status()))
        self.device.write({"state": "active", "active": False})
        self.assertFalse(self.row(self.status()))

    def test_non_admin_cannot_read_even_with_context_flags(self):
        self.assertEqual(self.status(self.operator), {"enabled": False, "devices": []})
        model = self.env["factory.biometric.device"].with_user(self.operator).with_context(
            is_admin=True, uid=self.admin.id, su=True,
        )
        self.assertFalse(model.get_connection_alert_status()["enabled"])

    def test_copied_or_unconfigured_database_is_disabled(self):
        self.env["ir.config_parameter"].sudo().set_param(DATABASE_PARAMETER, "some_other_database")
        self.assertEqual(self.status(), {"enabled": False, "devices": []})
        self.env["ir.config_parameter"].sudo().set_param(DATABASE_PARAMETER, False)
        self.assertFalse(self.status()["enabled"])

    def test_company_boundary(self):
        company = self.env["res.company"].create({"name": "Alert other company"})
        self.device.write({"company_id": company.id})
        self.assertFalse(self.row(self.status()))

    def test_multiple_devices_and_read_only(self):
        other = self.env["factory.biometric.device"].sudo().create({
            "name": "Second alert test", "serial_number": "ALERT-TEST-SECOND",
            "state": "active", "expected_source_ip": "127.0.0.1",
            "company_id": self.admin.company_id.id,
            "last_seen": self.now - timedelta(seconds=180),
        })
        self.env.flush_all()
        def snapshot():
            self.env.cr.execute("SELECT id,last_seen,write_date FROM factory_biometric_device ORDER BY id")
            rows = self.env.cr.fetchall()
            return (rows, self.env["factory.biometric.command"].search_count([]),
                    self.env["hr.attendance"].search_count([]))
        before = snapshot()
        ids = {row["id"] for row in self.status()["devices"]}
        self.assertTrue({self.device.id, other.id} <= ids)
        self.env.flush_all()
        self.assertEqual(snapshot(), before)

    def test_install_and_uninstall_scope_parameter(self):
        post_init_hook(self.env)
        self.assertTrue(self.status()["enabled"])
        uninstall_hook(self.env)
        self.assertFalse(self.status()["enabled"])
