from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user


class TestReadonlySalesPurchaseSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login="readonly_sales_purchase_test",
            groups=(
                "base.group_system,"
                "sales_team.group_sale_manager,"
                "purchase.group_purchase_manager,"
                "mapilla_readonly_sales_purchase.group_readonly_sales_purchase_operator"
            ),
        )

    def test_read_all_but_write_only_sales_and_purchase(self):
        access = self.env["ir.model.access"].with_user(self.user)
        self.assertTrue(access.check("res.partner", "read", raise_exception=False))
        self.assertFalse(access.check("res.partner", "write", raise_exception=False))
        self.assertTrue(access.check("sale.order", "write", raise_exception=False))
        self.assertTrue(access.check("purchase.order", "write", raise_exception=False))
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.user).write({"name": "Blocked"})

    def test_transient_records_remain_available_for_reports(self):
        access = self.env["ir.model.access"].with_user(self.user)
        self.assertTrue(access.check("res.config.settings", "create", raise_exception=False))
