from pathlib import Path

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.furniture_mrp.tests import test_future_delivery_order as delivery_tests


MODULE_ROOT = Path(__file__).resolve().parents[1]


@tagged('post_install', '-at_install')
class TestDeliveryRequests(delivery_tests.TestFutureDeliveryOrder):
    """Run the existing reservation/shortage/Kit tests through the new boundary."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager.write({'groups_id': [Command.link(cls.env.ref('base.group_system').id)]})
        cls.non_admin_manager = new_test_user(
            cls.env, login='delivery_app_non_admin_manager',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_manager',
            company_id=cls.company.id,
        )

    def test_app_menu_and_requests_restricted_to_admin(self):
        order = self._create_order()
        root = self.env.ref('furniture_delivery_requests.menu_delivery_requests_root')
        child = self.env.ref('furniture_mrp.menu_furniture_mrp_future_orders')
        self.assertFalse(root.parent_id)
        self.assertTrue(root.web_icon_data)
        self.assertEqual(child.parent_id, root)
        self.assertIn(root.id, self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids())
        self.assertNotIn(root.id, self.env['ir.ui.menu'].with_user(self.non_admin_manager)._visible_menu_ids())
        restricted = self.env['furniture.mrp.future.order'].with_user(self.non_admin_manager)
        self.assertFalse(restricted.search([('id', '=', order.id)]))
        self.assertFalse(restricted.get_alert_data()['can_manage'])
        with self.assertRaises(AccessError):
            order.with_user(self.non_admin_manager).read(['name'])
        with self.assertRaises(AccessError):
            order.line_ids.with_user(self.non_admin_manager).read(['quantity'])
        with self.assertRaises(AccessError):
            order.with_user(self.non_admin_manager).action_produce_shortage()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.with_user(self.non_admin_manager).write({'notes': 'not allowed'})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            restricted.create({'buyer_partner_id': self.buyer.id,
                               'beneficiary_partner_id': self.consumer.id})

    def test_admin_without_factory_manager_can_open_requests(self):
        admin = new_test_user(self.env, login='delivery_app_admin', groups='base.group_user,base.group_system')
        self.assertFalse(admin.has_group('furniture_mrp.group_furniture_mrp_manager'))
        order = self._create_order().with_user(admin)
        self.assertTrue(order.read(['name']))
        order._check_manager()
        self.assertTrue(order.get_alert_data()['can_manage'])

    def test_delivery_warning_wizard_is_admin_only(self):
        admin = new_test_user(
            self.env,
            login='delivery_warning_admin',
            groups='base.group_user,base.group_system',
            company_id=self.company.id,
        )
        order = self._create_order().with_user(admin)
        action = order.action_open_delivery_stage_warning_wizard()
        self.assertEqual(
            action['res_model'],
            'furniture.delivery.request.stage.warning.wizard',
        )
        wizard = self.env[action['res_model']].with_user(admin).create({
            'order_id': order.id,
            'stage': 'priming',
            'message': '  راجع لون الخشب قبل البدء  ',
        })
        wizard.action_add_warning()
        warning = order.delivery_stage_warning_ids.ensure_one()
        self.assertEqual(warning.stage, 'priming')
        self.assertEqual(warning.message, 'راجع لون الخشب قبل البدء')
        with self.assertRaises(AccessError):
            order.with_user(
                self.non_admin_manager
            ).action_open_delivery_stage_warning_wizard()

    def test_delivery_warnings_copy_to_matching_shortage_production_stages(self):
        order = self._create_order(quantity=2)
        self.env['furniture.delivery.request.stage.warning'].create([
            {
                'order_id': order.id,
                'stage': 'priming',
                'message': 'تحذير التقديم القادم من طلب التسليم',
            },
            {
                'order_id': order.id,
                'stage': 'painting',
                'message': 'لا ينسخ لأن الدهانات ليست في مسار هذا المنتج',
            },
        ])

        order.action_produce_shortage()

        productions = order.production_ids
        self.assertTrue(productions)
        source_by_stage = {
            warning.stage: warning.message
            for warning in order.delivery_stage_warning_ids
        }
        for production in productions:
            expected_stages = (
                set(source_by_stage) & set(production._required_stage_codes())
            )
            copied_by_stage = {
                warning.stage: warning.message
                for warning in production.stage_warning_ids
            }
            self.assertEqual(set(copied_by_stage), expected_stages)
            for stage in expected_stages:
                self.assertEqual(copied_by_stage[stage], source_by_stage[stage])
        self.assertEqual(len(order.delivery_stage_warning_ids), 2)
        with self.assertRaises(UserError):
            order.delivery_stage_warning_ids[:1].write({'active': False})

    def test_delivery_form_contains_production_warning_button(self):
        view = self.env['furniture.mrp.future.order'].with_user(
            self.manager
        ).get_view(
            view_id=self.env.ref(
                'furniture_mrp.view_furniture_mrp_future_order_form'
            ).id,
            view_type='form',
        )
        self.assertIn(
            'action_open_delivery_stage_warning_wizard',
            view['arch'],
        )

    def test_notes_title_and_editor_are_rtl(self):
        stylesheet = (
            MODULE_ROOT / 'static/src/css/delivery_requests.css'
        ).read_text(encoding='utf-8')
        self.assertRegex(
            stylesheet,
            r'\.o_delivery_request_notes strong\s*\{[^}]*direction:\s*rtl;'
            r'[^}]*text-align:\s*right;',
        )
        self.assertRegex(
            stylesheet,
            r'\.o_delivery_request_notes textarea\.o_input\s*\{'
            r'[^}]*direction:\s*rtl;[^}]*text-align:\s*right;',
        )
        self.assertRegex(
            stylesheet,
            r'\.o_delivery_request_note_composer\s*\{[^}]*align-items:\s*flex-start;',
        )

    def test_saved_notes_use_the_takawe_row_design(self):
        template = (
            MODULE_ROOT / 'static/src/xml/delivery_list.xml'
        ).read_text(encoding='utf-8')
        javascript = (
            MODULE_ROOT / 'static/src/js/delivery_edit_lines.js'
        ).read_text(encoding='utf-8')
        self.assertIn('get savedNotes()', javascript)
        self.assertIn('cushionGroups.length || savedNotes.length', template)
        self.assertIn('t-if="savedNotes.length" class="o_delivery_note_row"', template)
        self.assertIn('<strong>ملاحظة</strong>', template)
        self.assertIn('class="o_delivery_cushion_card" t-foreach="savedNotes"', template)
        self.assertIn('t-on-click="(ev) =&gt; this.deleteNote(note, ev)"', template)
        self.assertIn('async deleteNote(note, ev)', javascript)

    def test_creation_details_use_guided_product_steps(self):
        javascript = (
            MODULE_ROOT / 'static/src/js/delivery_specifications.js'
        ).read_text(encoding='utf-8')
        stylesheet = (
            MODULE_ROOT / 'static/src/css/delivery_requests.css'
        ).read_text(encoding='utf-8')
        self.assertIn('onNext.bind="next"', javascript)
        self.assertIn("nextProduct ? 'التالي' : 'إنهاء'", javascript)
        self.assertIn('await this.apply(editor);\n        await animate();', javascript)
        self.assertIn('@keyframes company-detail-next-out', stylesheet)

    def test_add_note_appends_text_and_clears_composer(self):
        order = self._create_order()
        order.write({'notes': 'الملاحظة الأولى', 'note_draft': '  الملاحظة الثانية  '})

        order.action_add_note()

        self.assertEqual(order.notes, 'الملاحظة الأولى\nالملاحظة الثانية')
        self.assertFalse(order.note_draft)

    def test_add_note_requires_text_and_pending_order(self):
        order = self._create_order()
        with self.assertRaises(UserError):
            order.action_add_note()

        order.write({'note_draft': 'ملاحظة متأخرة'})
        order.state = 'cancelled'
        with self.assertRaises(UserError):
            order.action_add_note()

    def test_delete_note_removes_only_the_selected_note(self):
        order = self._create_order()
        order.notes = 'الأولى\nالثانية\nالثالثة'

        order.action_delete_note(1, 'الثانية')

        self.assertEqual(order.notes, 'الأولى\nالثالثة')

    def test_delete_note_rejects_invalid_index(self):
        order = self._create_order()
        order.notes = 'ملاحظة واحدة'
        with self.assertRaises(UserError):
            order.action_delete_note(3)
        with self.assertRaises(UserError):
            order.action_delete_note(0, 'نص قديم')

    def test_product_picker_uses_persisted_model_ids_and_requires_admin(self):
        orders = self.env['furniture.mrp.future.order'].with_user(self.manager)
        before = orders.search_count([])
        catalog = orders.get_product_picker_catalog(self.company.id)
        self.assertTrue(all(type(model['id']) is int for model in catalog['models']))
        product = next(item for item in catalog['products'] if item['id'] == self.product.id)
        self.assertIn(self.model.id, product['model_ids'])
        self.assertNotIn(self.other_model.id, product['model_ids'])
        self.assertEqual(orders.search_count([]), before)
        with self.assertRaises(AccessError):
            orders.with_user(self.non_admin_manager).get_product_picker_catalog()

    def test_creation_wizard_creates_pending_delivery_without_stock_actions(self):
        from odoo.tests import Form
        Wizard = self.env['furniture.delivery.creation.wizard'].with_user(self.manager)
        before = self.env['furniture.mrp.future.order'].search_count([])
        with Form(Wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.model_id = self.model
            form.buyer_partner_id = self.buyer
            form.beneficiary_partner_id = self.consumer
            form.selected_product_ids.add(self.product)
            with form.line_ids.edit(0) as line:
                line.quantity = 3
        wizard = form.record
        self.assertEqual(self.env['furniture.mrp.future.order'].search_count([]), before)
        action = wizard.action_create_delivery()
        order = self.env['furniture.mrp.future.order'].browse(action['res_id'])
        self.assertEqual(order.line_ids.product_id, self.product)
        self.assertEqual(order.line_ids.furniture_model_id, self.model)
        self.assertEqual(order.line_ids.quantity, 3)
        self.assertEqual(order.buyer_partner_id, self.buyer)
        self.assertEqual(order.beneficiary_partner_id, self.consumer)
        self.assertEqual(order.state, 'pending')
        self.assertFalse(order.production_ids)
        self.assertFalse(order.reservation_picking_id)

    def test_creation_wizard_rejects_invalid_quantities_and_model(self):
        from odoo.exceptions import ValidationError
        wizard = self.env['furniture.delivery.creation.wizard'].with_user(self.manager).create({
            'model_id': self.model.id, 'buyer_partner_id': self.buyer.id,
            'beneficiary_partner_id': self.consumer.id,
            'selected_product_ids': [Command.set(self.product.ids)],
            'line_ids': [Command.create({'product_id': self.product.id, 'quantity': 0})],
        })
        with self.assertRaises(ValidationError):
            wizard.action_create_delivery()

        wizard.line_ids.quantity = 1
        wizard.model_id = self.other_model
        with self.assertRaises(ValidationError):
            wizard.action_create_delivery()

    def test_edit_popup_preserves_order_lines_and_fractional_quantities(self):
        from odoo.tests import Form
        order = self._create_order(quantity=1.25)
        original_line = order.line_ids
        other_product, _ = self._create_component('Other popup product', self.other_model)
        order.write({'line_ids': [Command.create({'product_id': other_product.id, 'furniture_model_id': self.other_model.id, 'quantity': 4})]})
        other_line = order.line_ids - original_line
        count = self.env['furniture.mrp.future.order'].search_count([])
        action = order.action_edit_products_popup(self.model.id)
        wizard = self.env['furniture.delivery.creation.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.line_ids.quantity, 1.25)
        self.assertEqual(original_line.quantity, 1.25)
        with Form(wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            with form.line_ids.edit(0) as line:
                line.quantity = 2.5
        self.assertEqual(wizard.action_create_delivery()['type'], 'ir.actions.act_window_close')
        self.assertEqual(original_line.quantity, 2.5)
        self.assertEqual(other_line.quantity, 4)
        self.assertEqual(self.env['furniture.mrp.future.order'].search_count([]), count)

    def test_edit_popup_rejects_stale_order(self):
        from odoo.exceptions import ValidationError
        order = self._create_order()
        action = order.action_edit_products_popup(self.model.id)
        wizard = self.env['furniture.delivery.creation.wizard'].browse(action['res_id'])
        # Simulate a newer version; a TransactionCase uses a fixed transaction timestamp.
        from datetime import timedelta
        wizard.order_write_date -= timedelta(seconds=1)
        with self.assertRaises(ValidationError):
            wizard.action_create_delivery()
    def test_company_boundary_for_admin(self):
        order = self._create_order()
        other = self.env['res.company'].create({'name': 'Delivery other company'})
        allowed_admin = self.manager.with_context(allowed_company_ids=[self.company.id])
        order.sudo().with_context(future_order_decision_write=True).write({'company_id': other.id})
        self.assertFalse(order.with_user(allowed_admin).with_context(
            allowed_company_ids=[self.company.id]
        ).search([('id', '=', order.id)]))

    # Explicit wrappers keep these inherited regressions in this addon's test
    # selection (Odoo tags inherited methods with their defining module).
    def test_existing_reservation_and_cancel(self):
        super().test_full_reservation_reduces_free_stock_and_cancel_releases_it()

    def test_existing_atomic_reservation(self):
        super().test_full_reservation_is_atomic_when_stock_is_short()

    def test_existing_shortage_production_links(self):
        super().test_shortage_decision_reserves_available_and_produces_only_shortage()

    def test_existing_kit_reservation(self):
        super().test_kit_reserves_components_and_never_creates_kit_parent_line()

    def test_existing_kit_model_validation(self):
        super().test_kit_model_mismatch_is_rejected_server_side()

    def test_existing_due_date_alerts(self):
        super().test_alert_badge_only_counts_undecided_orders_due_within_15_days()

    def test_existing_ordinary_user_denied(self):
        super().test_non_manager_cannot_decide_or_see_alert_data()

    def test_existing_preview_is_read_only(self):
        super().test_availability_preview_does_not_create_model_sku()

    def test_dimensions_roundtrip_and_stock_identity(self):
        order = self._create_order()
        line = order.line_ids[:1]
        line.write({'width_cm': 225, 'depth_cm': 95, 'height_cm': 85})
        self.assertFalse(line._resolved_demand_specs()[0]['stock_product'])
        spec = line._resolved_demand_specs(materialize_stock_product=True)[0]
        self.assertNotEqual(spec['stock_product'], line.product_id)
        self.assertEqual(spec['width_cm'], 225)
        self.assertEqual(line._resolved_demand_specs()[0]['stock_product'], spec['stock_product'])
        action = order.with_user(self.manager).action_edit_products_popup(line.furniture_model_id.id)
        wizard = self.env[action['res_model']].with_user(self.manager).browse(action['res_id'])
        self.assertEqual(wizard.line_ids.width_cm, 225)
        wizard.line_ids.width_cm = 240
        wizard.action_create_delivery()
        self.assertEqual(line.width_cm, 240)
        self.assertFalse(line._resolved_demand_specs()[0]['stock_product'])

    def test_custom_dimensions_reach_shortage_production(self):
        # Invoke the source builder directly; demand-cycle confirmation consumes
        # these same source lines to construct its stage orders.
        from odoo.addons.furniture_mrp.models.future_delivery_order import FurnitureMrpFutureOrder
        order = self._create_order()
        line = order.line_ids[:1]
        line.write({'width_cm': 225, 'depth_cm': 95, 'height_cm': 85})
        specs = line._resolved_demand_specs(materialize_stock_product=True)
        production = FurnitureMrpFutureOrder._create_shortage_production(order, line, specs)
        self.assertEqual(production.production_line_ids.width_cm, 225)
        self.assertEqual(production.production_line_ids.depth_cm, 95)
        self.assertEqual(production.production_line_ids.height_cm, 85)

    def test_optional_beneficiary_creation_and_reservation(self):
        from odoo.tests import Form
        Wizard = self.env['furniture.delivery.creation.wizard'].with_user(self.manager)
        with Form(Wizard, view='furniture_delivery_requests.view_delivery_creation_wizard') as form:
            form.model_id = self.model
            form.buyer_partner_id = self.buyer
            form.selected_product_ids.add(self.product)
        action = form.record.action_create_delivery()
        order = self.env['furniture.mrp.future.order'].with_user(self.manager).browse(action['res_id'])
        self.assertFalse(order.beneficiary_partner_id)
        self.env['stock.quant']._update_available_quantity(self.product, self.finished, 3)
        order.action_reserve_from_finished()
        self.assertEqual(order.reservation_picking_id.partner_id, self.buyer)
        self.assertEqual(order.state, 'reserved')
