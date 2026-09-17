from odoo import _, http
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.addons.web.controllers.dataset import DataSet

from ..models.security import is_direct_write_model, is_restricted_user


MUTATING_METHOD_PREFIXES = (
    "action_apply",
    "action_approve",
    "action_cancel",
    "action_confirm",
    "action_create",
    "action_reject",
    "action_save",
    "action_submit",
    "button_",
    "factory_save_",
    "set_",
    "toggle_",
)
MUTATING_METHODS = {"create", "write", "unlink", "web_save", "web_create"}


def _guard(model, method, button=False):
    if not is_restricted_user(request.env) or is_direct_write_model(model):
        return
    if not button and request.env[model].is_transient() and method in MUTATING_METHODS:
        return
    if button:
        if method.startswith(("action_open", "action_view", "get_")):
            return
        raise AccessError(_("هذا الإجراء غير مسموح لمستخدم العرض فقط خارج المبيعات والمشتريات."))
    if method in MUTATING_METHODS or method.startswith(MUTATING_METHOD_PREFIXES) or method.endswith("_decide"):
        raise AccessError(_("هذا الإجراء غير مسموح لمستخدم العرض فقط خارج المبيعات والمشتريات."))


class ReadonlyDataSet(DataSet):

    @http.route()
    def call_kw(self, model, method, args, kwargs, path=None):
        _guard(model, method)
        return super().call_kw(model, method, args, kwargs, path=path)

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        _guard(model, method, button=True)
        return super().call_button(model, method, args, kwargs, path=path)

    @http.route()
    def resequence(self, model, ids, field="sequence", offset=0, context=None):
        _guard(model, "write")
        return super().resequence(model, ids, field=field, offset=offset, context=context)
