from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        result["factory_biometric_connection_alert"] = self.env[
            "factory.biometric.device"
        ]._connection_alert_enabled()
        return result
