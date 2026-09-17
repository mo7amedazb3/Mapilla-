# -*- coding: utf-8 -*-

from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPurchaseDocumentDesign(TransactionCase):

    def test_purchase_order_form_has_scoped_design_hooks(self):
        result = self.env['purchase.order'].get_view(
            view_id=self.env.ref('purchase.purchase_order_form').id,
            view_type='form',
        )
        arch = result['arch']
        self.assertIn('o_furniture_purchase_design', arch)
        self.assertIn('o_furniture_purchase_partner', arch)
        self.assertIn('res_partner_many2one_avatar', arch)
        self.assertIn('o_furniture_purchase_lines', arch)
        self.assertIn('o_furniture_purchase_totals', arch)

    def test_vendor_bill_form_has_scoped_design_hooks(self):
        result = self.env['account.move'].get_view(
            view_id=self.env.ref('account.view_move_form').id,
            view_type='form',
        )
        arch = result['arch']
        self.assertIn('o_furniture_account_document_design', arch)
        self.assertIn('o_furniture_vendor_bill_design_marker', arch)
        self.assertIn('o_furniture_vendor_bill_partner', arch)
        self.assertIn('res_partner_many2one_avatar', arch)
        self.assertIn('o_furniture_vendor_bill_lines', arch)
        self.assertIn('o_furniture_vendor_bill_totals', arch)

    def test_styles_scope_form_class_on_controller(self):
        stylesheet = (
            Path(__file__).parents[1]
            / 'static/src/scss/purchase_design.scss'
        ).read_text()
        self.assertIn(
            '.o_form_view.o_furniture_purchase_design .o_form_renderer',
            stylesheet,
        )
        self.assertNotIn(
            '.o_form_renderer.o_furniture_purchase_design',
            stylesheet,
        )
        self.assertIn('overflow: visible !important', stylesheet)
        self.assertIn('min-width: 0', stylesheet)
        self.assertIn('width: 28rem !important', stylesheet)
        self.assertIn('o_furniture_vendor_logo', stylesheet)
        self.assertIn('o_furniture_has_vendor_watermark', stylesheet)
        self.assertIn('--fpr-vendor-watermark-image', stylesheet)
        self.assertIn('opacity: 0.1', stylesheet)
        self.assertIn('pointer-events: none', stylesheet)

    def test_partner_avatar_widget_assets_exist(self):
        module_root = Path(__file__).parents[1]
        javascript = (
            module_root / 'static/src/js/partner_many2one_avatar.js'
        ).read_text()
        template = (
            module_root / 'static/src/xml/partner_many2one_avatar.xml'
        ).read_text()
        self.assertIn('res_partner_many2one_avatar', javascript)
        self.assertIn('useEffect', javascript)
        self.assertIn('--fpr-vendor-watermark-image', javascript)
        self.assertIn('avatar_512', javascript)
        self.assertIn('PartnerMany2OneAvatarField', template)
        self.assertIn('avatar_128', template)
        self.assertIn('t-ref="vendorIdentity"', template)
