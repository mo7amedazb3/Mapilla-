from odoo import api, models

from .security import FLOW_CONTEXT_KEY, FLOW_TOKEN


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model_create_multi
    def create(self, vals_list):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).create(vals_list)

    def write(self, vals):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).write(vals)

    def unlink(self):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).unlink()

    def action_confirm(self):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).action_confirm()

    def action_cancel(self):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).action_cancel()

    def action_draft(self):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).action_draft()

    def _create_invoices(self, grouped=False, final=False, date=None):
        return super(SaleOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN}))._create_invoices(
            grouped=grouped, final=final, date=date
        )


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        return super(SaleOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).create(vals_list)

    def write(self, vals):
        return super(SaleOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).write(vals)

    def unlink(self):
        return super(SaleOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).unlink()


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    @api.model_create_multi
    def create(self, vals_list):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).create(vals_list)

    def write(self, vals):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).write(vals)

    def unlink(self):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).unlink()

    def button_confirm(self):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).button_confirm()

    def button_approve(self, force=False):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).button_approve(force=force)

    def button_cancel(self):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).button_cancel()

    def button_draft(self):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).button_draft()

    def action_create_invoice(self):
        return super(PurchaseOrder, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).action_create_invoice()


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        return super(PurchaseOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).create(vals_list)

    def write(self, vals):
        return super(PurchaseOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).write(vals)

    def unlink(self):
        return super(PurchaseOrderLine, self.with_context(**{FLOW_CONTEXT_KEY: FLOW_TOKEN})).unlink()
