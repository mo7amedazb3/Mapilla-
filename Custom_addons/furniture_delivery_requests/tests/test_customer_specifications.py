from odoo import Command, fields
from odoo.exceptions import ValidationError, AccessError
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval
from .test_delivery_requests import TestDeliveryRequests


@tagged('post_install', '-at_install')
class TestCustomerSpecifications(TestDeliveryRequests):
    def test_delivery_standard_company_switch_controls_directory_membership(self):
        contact = self.env['res.partner'].create({
            'name': 'Delivery standard contact',
            'is_company': False,
            'is_delivery_standard_company': True,
        })
        self.assertTrue(contact.is_company)
        self.assertTrue(contact.is_delivery_standard_company)

        action = self.env.ref(
            'furniture_delivery_requests.action_delivery_buyers'
        )
        self.assertIn(
            ('is_delivery_standard_company', '=', True),
            safe_eval(action.domain),
        )
        contact.write({'is_delivery_standard_company': False})
        self.assertFalse(contact.is_delivery_standard_company)

        view = self.env.ref(
            'furniture_delivery_requests.view_partner_delivery_customizations'
        )
        self.assertIn('is_delivery_standard_company', view.arch_db)
        self.assertNotIn('action_delivery_preferences', view.arch_db)
        self.assertNotIn('action_delivery_custom_summary', view.arch_db)

    def _profile(self):
        fabric = self.env['product.product'].create({'name': 'Custom olive fabric', 'type': 'consu', 'is_storable': True, 'furniture_tailoring_material_kind': 'fabric'})
        return self.env['furniture.delivery.customer.spec'].create({
            'partner_id': self.buyer.id, 'company_id': self.company.id,
            'model_id': self.model.id, 'product_id': self.product.id,
            'width_cm': 30, 'depth_cm': 50, 'height_cm': 90,
            'upholstery_data': [{'kind': 'fabric', 'product_id': fabric.id, 'quantity': 3.5, 'piece_size': False},
                                {'kind': 'takawe', 'product_id': fabric.id, 'quantity': 5, 'piece_size': '50'}],
        })

    def _creation(self):
        return self.env['furniture.delivery.creation.wizard'].create({
            'buyer_partner_id': self.buyer.id, 'company_id': self.company.id,
            'delivery_date': fields.Date.today(), 'model_id': self.model.id,
            'selected_product_ids': [Command.set(self.product.ids)],
            'line_ids': [Command.create({'product_id': self.product.id, 'quantity': 2})],
        })

    def test_custom_standard_and_manual_edits(self):
        profile = self._profile()
        wizard = self._creation()
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            self.assertTrue(form.has_custom_specs)
            form.specification_mode = 'custom'
        line = wizard.line_ids
        self.assertEqual(line.width_cm, 30)
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.upholstery_data, profile.upholstery_data)
        editor = self.env['furniture.delivery.spec.wizard'].browse(line.action_open_specifications()['res_id'])
        self.assertEqual(editor.fabric_line_ids.quantity, 3.5)
        editor.fabric_line_ids.quantity = 4.25
        editor.width_cm = 35
        editor._onchange_recipe_meters()
        self.assertEqual(editor.fabric_line_ids.quantity, 4.25)
        editor.action_apply()
        self.assertEqual(profile.upholstery_data[0]['quantity'], 3.5)
        again = self.env['furniture.delivery.spec.wizard'].browse(line.action_open_specifications()['res_id'])
        self.assertEqual(again.fabric_line_ids.quantity, 4.25)
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.specification_mode = 'standard'
        self.assertEqual(wizard.line_ids.width_cm, 0)
        self.assertFalse(wizard.line_ids.upholstery_data)
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.specification_mode = 'custom'
        order = self.env['furniture.mrp.future.order'].browse(wizard.action_create_delivery()['res_id'])
        self.assertEqual(order.specification_mode, 'custom')
        self.assertEqual(order.line_ids.upholstery_data, profile.upholstery_data)
        profile.write({'width_cm': 40})
        self.assertEqual(order.line_ids.width_cm, 30)
        reopened = self.env['furniture.delivery.creation.wizard'].browse(order.action_edit_products_popup(self.model.id)['res_id'])
        self.assertEqual(reopened.specification_mode, 'custom')
        self.assertEqual(reopened.line_ids.width_cm, 30)

    def test_standard_mode_only_exposes_buyer_profile_models(self):
        self._profile()
        other_product, _bom = self._create_component(
            'Unconfigured company model product', self.other_model,
        )
        wizard = self._creation()

        # The internal value ``custom`` is labelled Standard in the UI.
        wizard.specification_mode = 'custom'
        wizard._compute_choices()
        self.assertEqual(wizard.available_model_ids, self.model)
        self.assertIn(self.product, wizard.available_product_ids)
        self.assertNotIn(other_product, wizard.available_product_ids)

        # The internal value ``standard`` is labelled Custom in the UI.
        wizard.specification_mode = 'standard'
        wizard._compute_choices()
        self.assertIn(self.model, wizard.available_model_ids)
        self.assertIn(self.other_model, wizard.available_model_ids)

        draft = self.env['furniture.delivery.creation.wizard'].new({
            'company_id': self.company.id,
            'buyer_partner_id': self.buyer.id,
            'delivery_date': fields.Date.today(),
            'model_id': self.other_model.id,
            'specification_mode': 'custom',
        })
        draft._onchange_customer_configuration()
        self.assertFalse(draft.model_id)
        self.assertFalse(draft.selected_product_ids)
        self.assertFalse(draft.line_ids)

    def test_new_lines_customer_change_and_profile_editor(self):
        profile = self._profile()
        wizard = self._creation()
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.selected_product_ids.clear()
            form.specification_mode = 'custom'
            form.selected_product_ids.add(self.product)
        self.assertEqual(wizard.line_ids.upholstery_data, profile.upholstery_data)
        other = self.env['res.partner'].create({'name': 'No customizations', 'is_company': True})
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.buyer_partner_id = other
        self.assertEqual(wizard.specification_mode, 'standard')
        self.assertEqual(wizard.line_ids.width_cm, 0)
        self.assertFalse(wizard.line_ids.upholstery_data)
        editor = self.env['furniture.delivery.spec.wizard'].browse(profile.action_open_specifications()['res_id'])
        self.assertEqual(editor.product_id, self.product)
        self.assertEqual(editor.model_id, self.model)
        editor.fabric_line_ids.quantity = 7
        editor.action_apply()
        self.assertEqual(profile.upholstery_data[0]['quantity'], 7)
        with self.assertRaises(AccessError):
            profile.with_user(self.non_admin_manager).action_open_specifications()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            profile.write({'depth_cm': -1})

    def test_custom_kit_expands_with_component_quantities(self):
        profile = self._profile()
        chair, _bom = self._create_component('Uncustomized chair', self.model)
        kit = self.env['product.product'].create({'name': 'Customer custom kit', 'type': 'consu', 'is_storable': True, 'furniture_model_id': self.model.id})
        self.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id, 'product_qty': 1, 'type': 'phantom',
            'furniture_product_id': kit.id, 'furniture_model_id': self.model.id,
            'bom_line_ids': [Command.create({'product_id': self.product.id, 'product_qty': 1, 'product_uom_id': self.product.uom_id.id}),
                             Command.create({'product_id': chair.id, 'product_qty': 2, 'product_uom_id': chair.uom_id.id})],
        })
        wizard = self._creation()
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.selected_product_ids.clear()
            form.specification_mode = 'custom'
            form.selected_product_ids.add(kit)
        self.assertEqual(set(wizard.line_ids.product_id.ids), {self.product.id, chair.id})
        sofa = wizard.line_ids.filtered(lambda l: l.product_id == self.product)
        armchair = wizard.line_ids.filtered(lambda l: l.product_id == chair)
        self.assertEqual(sofa.quantity, 1)
        self.assertEqual(armchair.quantity, 2)
        self.assertEqual(sofa.upholstery_data, profile.upholstery_data)
        self.assertFalse(armchair.upholstery_data)
        order = self.env['furniture.mrp.future.order'].browse(wizard.action_create_delivery()['res_id'])
        self.assertEqual(len(order.line_ids), 2)

    def test_existing_line_edits_survive_added_products(self):
        self._profile()
        wizard = self._creation()
        wizard.line_ids.write({'width_cm': 60, 'depth_cm': 70, 'height_cm': 80})
        chair, _bom = self._create_component('Additional customer chair', self.model)
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.selected_product_ids.add(chair)
        sofa = wizard.line_ids.filtered(lambda l: l.product_id == self.product)
        self.assertEqual(sofa.width_cm, 60)
        self.assertEqual(sofa.quantity, 2)

    def test_preferences_navigation_save_and_cancel(self):
        profile = self._profile()
        chair, _bom = self._create_component('Customer preference chair', self.model)
        Editors = self.env['furniture.delivery.customer.editor'].with_company(self.company)
        editor = Editors.browse(Editors.open_for_partner(self.buyer.id, self.model.id, self.product.id)['res_id'])
        with Form(editor) as form:
            form.product_id = chair
        self.assertEqual(editor.width_cm, 0)
        editor.write({'width_cm': 40, 'depth_cm': 60, 'height_cm': 80})
        with Form(editor) as form:
            form.product_id = self.product
        self.assertEqual(editor.width_cm, 30)
        self.assertEqual(editor.upholstery_data, profile.upholstery_data)
        editor.width_cm = 35
        with Form(editor) as form:
            form.product_id = chair
        self.assertEqual(editor.width_cm, 40)
        self.assertEqual(profile.width_cm, 30)
        editor.action_save()
        self.assertEqual(profile.width_cm, 35)
        other = self.buyer.delivery_custom_ids.filtered(lambda p: p.product_id == chair)
        self.assertEqual(other.width_cm, 40)
        reopened = Editors.browse(Editors.open_for_partner(self.buyer.id, self.model.id, self.product.id)['res_id'])
        self.assertEqual(reopened.width_cm, 35)
        reopened.width_cm = 99
        reopened.unlink()
        self.assertEqual(profile.width_cm, 35)
