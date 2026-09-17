from datetime import timedelta

from odoo import api, fields, models

from ..hooks import DATABASE_PARAMETER


OFFLINE_SECONDS = 120
POLL_SECONDS = 15


class FactoryBiometricDevice(models.Model):
    _inherit = "factory.biometric.device"

    @api.model
    def _connection_alert_enabled(self):
        return bool(
            self.env.user.has_group("base.group_system")
            and self.env["ir.config_parameter"].sudo().get_param(DATABASE_PARAMETER)
            == self.env.cr.dbname
        )

    @api.model
    def get_connection_alert_status(self):
        """Read-only heartbeat status; never dispatch commands or change last_seen."""
        if not self._connection_alert_enabled():
            return {"enabled": False, "devices": []}

        now = fields.Datetime.now()
        cutoff = now - timedelta(seconds=OFFLINE_SECONDS)
        # Check the caller before sudo and explicitly limit to permitted, selected
        # companies. Return no serial numbers, network keys, or employee data.
        company_ids = (self.env.companies & self.env.user.company_ids).ids
        devices = self.sudo().search([
            ("active", "=", True),
            ("state", "=", "active"),
            ("company_id", "in", company_ids),
        ])
        offline = []
        for device in devices:
            reference = device.last_seen or device.create_date
            if reference and reference > cutoff:
                continue
            offline.append({
                "id": device.id,
                "name": device.name,
                "last_seen": fields.Datetime.to_string(device.last_seen)
                if device.last_seen else False,
                "reason": "offline" if device.last_seen else "never_connected",
            })
        return {
            "enabled": True,
            "devices": offline,
            "checked_at": fields.Datetime.to_string(now),
            "offline_after_seconds": OFFLINE_SECONDS,
            "poll_seconds": POLL_SECONDS,
        }
