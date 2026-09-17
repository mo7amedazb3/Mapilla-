from lxml import etree

from odoo import _, api, models
from odoo.exceptions import AccessError


GROUP_XMLID = "mapilla_readonly_sales_purchase.group_readonly_sales_purchase_operator"
FLOW_CONTEXT_KEY = "mapilla_readonly_sales_purchase_flow"
FLOW_TOKEN = object()

DIRECT_WRITE_MODELS = frozenset({
    "sale.order",
    "sale.order.line",
    "sale.order.option",
    "purchase.order",
    "purchase.order.line",
})


def is_restricted_user(env):
    if env.su:
        return False
    group = env.ref(GROUP_XMLID, raise_if_not_found=False)
    return bool(group and group.id in env.user._get_group_ids())


def is_flow_context(env):
    return env.context.get(FLOW_CONTEXT_KEY) is FLOW_TOKEN


def is_direct_write_model(model_name):
    return model_name in DIRECT_WRITE_MODELS


def can_modify_model(env, model_name):
    if is_flow_context(env) or is_direct_write_model(model_name):
        return True
    model = env.get(model_name)
    return bool(model is not None and model.is_transient())


class IrModelAccess(models.Model):
    _inherit = "ir.model.access"

    @api.model
    def check(self, model, mode="read", raise_exception=True):
        if mode != "read" and is_restricted_user(self.env):
            target = self.env.get(model)
            if target is not None and target.is_transient():
                return True
            if not can_modify_model(self.env, model):
                if raise_exception:
                    raise AccessError(_(
                        "هذا المستخدم للعرض فقط خارج المبيعات والمشتريات، ولا يمكنه إنشاء أو تعديل أو حذف هذه البيانات."
                    ))
                return False
        return super().check(model, mode=mode, raise_exception=raise_exception)


class ReadonlyBaseModel(models.AbstractModel):
    _inherit = "base"

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id=view_id, view_type=view_type, **options)
        if (
            not is_restricted_user(self.env)
            or can_modify_model(self.env, self._name)
            or view_type not in {"form", "list", "kanban"}
        ):
            return result

        result = dict(result)
        root = etree.fromstring(result["arch"])
        for attribute in ("create", "edit", "delete", "duplicate", "import"):
            root.set(attribute, "false")
        for button in root.xpath("//button"):
            button.set("invisible", "1")
        result["arch"] = etree.tostring(root, encoding="unicode")
        return result
