from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError, UserError
from odoo.tests import Form, tagged
from .test_delivery_requests import TestDeliveryRequests


@tagged('post_install', '-at_install')
class TestDeliverySpecifications(TestDeliveryRequests):
    def _spec_material(self):
        return self.env['product.product'].create({'name': 'Delivery fabric test', 'type': 'consu', 'is_storable': True, 'furniture_tailoring_material_kind': 'fabric'})

    def test_material_cards_have_more_and_instant_search(self):
        view = self.env.ref(
            'furniture_delivery_requests.view_delivery_spec_wizard'
        )
        arch = self.env[view.model].get_view(
            view_id=view.id,
            view_type='form',
        )['arch']
        document = etree.fromstring(arch.encode())
        for field_name, placeholder in (
            ('fabric_product_ids', 'ابحث في الأقمشة...'),
            ('takawe_product_ids', 'ابحث في التكاوي...'),
        ):
            fields = document.xpath(".//field[@name='%s']" % field_name)
            self.assertEqual(len(fields), 1)
            options = fields[0].get('options', '')
            self.assertIn("'quick_limit': 5", options)
            self.assertIn("'always_show_more': True", options)
            self.assertIn("'searchable': True", options)
            self.assertIn(placeholder, options)

    def test_specifications_roundtrip(self):
        fabric = self._spec_material()
        order = self._create_order()
        action = order.action_edit_products_popup(self.model.id)
        creation = self.env['furniture.delivery.creation.wizard'].browse(action['res_id'])
        line = creation.line_ids[0]
        popup = self.env['furniture.delivery.spec.wizard'].browse(line.action_open_specifications()['res_id'])
        with Form(popup, view='furniture_delivery_requests.view_delivery_spec_wizard') as form:
            form.width_cm = 210
            form.depth_cm = 90
            form.height_cm = 85
            form.fabric_product_ids.add(fabric)
            with form.fabric_line_ids.edit(0) as material:
                material.quantity = 2.5
            form.takawe_product_ids.add(fabric)
            with form.takawe_line_ids.edit(0) as material:
                material.quantity = 0.75
                material.piece_size = '40x60'
        popup.action_apply()
        line.invalidate_recordset()
        self.assertEqual(len(line.upholstery_data), 2)
        creation._onchange_products()
        self.assertEqual(len(creation.line_ids[0].upholstery_data), 2)
        creation.action_create_delivery()
        order.invalidate_recordset()
        saved = order.line_ids[0]
        self.assertEqual(saved.width_cm, 210)
        self.assertEqual(saved.upholstery_data[0]['quantity'], 2.5)
        self.assertEqual(saved.upholstery_data[1]['piece_size'], '40x60')
        again = self.env['furniture.delivery.creation.wizard'].browse(order.action_edit_products_popup(self.model.id)['res_id'])
        self.assertEqual(again.line_ids[0].upholstery_data, saved.upholstery_data)
        # The same payload must survive the new-order branch as well.
        again.write({'order_id': False})
        created = self.env['furniture.mrp.future.order'].browse(again.action_create_delivery()['res_id'])
        self.assertEqual(created.line_ids[0].upholstery_data, saved.upholstery_data)

    def test_specifications_cancel_and_validation(self):
        fabric = self._spec_material()
        order = self._create_order()
        creation = self.env['furniture.delivery.creation.wizard'].browse(order.action_edit_products_popup(self.model.id)['res_id'])
        line = creation.line_ids[0]
        original = line._delivery_values()
        popup = self.env['furniture.delivery.spec.wizard'].browse(line.action_open_specifications()['res_id'])
        popup.write({'width_cm': 123})
        self.assertEqual(line._delivery_values(), original)
        with self.assertRaises(ValidationError):
            popup.action_apply()
        popup.write({'width_cm': 0, 'material_line_ids': [Command.create({'kind': 'takawe', 'product_id': fabric.id, 'quantity': 1})]})
        with self.assertRaises(ValidationError):
            popup.action_apply()
        popup.material_line_ids.write({'piece_size': '45'})
        popup.action_apply()
        with self.assertRaises(UserError):
            popup.action_apply()  # stale snapshot cannot overwrite the newly saved details

    def test_existing_materials_survive_new_selection(self):
        fabric = self._spec_material()
        extra = self._spec_material()
        order = self._create_order()
        creation = self.env['furniture.delivery.creation.wizard'].browse(
            order.action_edit_products_popup(self.model.id)['res_id'])
        popup = self.env['furniture.delivery.spec.wizard'].browse(
            creation.line_ids[0].action_open_specifications()['res_id'])
        popup.write({'material_line_ids': [
            Command.create({'kind': 'fabric', 'product_id': fabric.id, 'quantity': 2.5}),
            Command.create({'kind': 'takawe', 'product_id': fabric.id,
                            'quantity': 0.75, 'piece_size': '45'}),
        ]})
        saved_ids = popup.material_line_ids.ids
        with Form(popup, view='furniture_delivery_requests.view_delivery_spec_wizard') as form:
            form.fabric_product_ids.add(extra)
            with form.fabric_line_ids.edit(1) as material:
                material.quantity = 3
            form.takawe_product_ids.add(extra)
            with form.takawe_line_ids.edit(1) as material:
                material.piece_size = '50'
        self.assertTrue(set(saved_ids).issubset(popup.material_line_ids.ids))
        self.assertEqual(popup.fabric_line_ids.filtered(lambda l: l.product_id == fabric).quantity, 2.5)
        original = popup.takawe_line_ids.filtered(lambda l: l.product_id == fabric)
        self.assertEqual(original.quantity, 0.75)
        self.assertEqual(original.piece_size, '45')
        self.assertEqual(popup.takawe_line_ids.filtered(lambda l: l.product_id == extra).piece_size, '50')
        popup.action_apply()
        self.assertEqual(len(creation.line_ids[0].upholstery_data), 4)
