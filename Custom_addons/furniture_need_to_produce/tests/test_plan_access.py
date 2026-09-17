"""Security regressions using real users and the same x2many writes as the UI.

This deliberately does not inherit TestNeedToProduce: test discovery must not run
its manufacturing scenarios again. All records are isolated TransactionCase
fixtures. No existing inventory, policies or manufacturing orders are changed.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestNeedPlanAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({
            'name': 'NTP access isolated company',
            'currency_id': cls.company.currency_id.id,
        })
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'NTP access isolated chaise', 'type': 'consu',
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id,
        })
        cls.raw = cls.env['product.product'].create({
            'name': 'NTP access isolated paint', 'type': 'consu',
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id,
        })
        cls.alternate_raw = cls.env['product.product'].create({
            'name': 'NTP access isolated alternate paint', 'type': 'consu',
            'uom_id': cls.unit.id, 'uom_po_id': cls.unit.id,
        })
        cls.model = cls.env['furniture.product.model'].create({
            'name': 'NTP access isolated model',
        })
        cls.boms = {}
        for company in cls.company | cls.other_company:
            cls.env['mrp.bom'].sudo().with_company(company).create({
                'product_tmpl_id': cls.product.product_tmpl_id.id,
                'product_qty': 1, 'product_uom_id': cls.unit.id, 'type': 'normal',
                'company_id': company.id, 'furniture_product_id': cls.product.id,
                'furniture_recipe_model_id': cls.model.id,
                'furniture_width_cm': 100, 'furniture_depth_cm': 80, 'furniture_height_cm': 70,
                'use_priming': False, 'use_carpentry': False, 'use_bases': False,
                'use_finishing': False, 'use_tailoring': False, 'use_painting': True,
                'use_upholstery': False, 'use_packaging': True, 'use_sewing': False,
                'furniture_stage_material_line_ids': [(0, 0, {
                    'stage': 'painting', 'product_id': cls.raw.id, 'product_qty': 1,
                    'product_uom_id': cls.unit.id, 'quantity_mode': 'scaled',
                })],
            })
            cls.boms[company.id] = cls.env['mrp.bom'].sudo()._find_furniture_production_recipe(
                cls.product, model=cls.model, company=company,
            )
        cls.manager = new_test_user(
            cls.env, login='ntp.access.manager@example.test',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_manager',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)],
        )
        cls.multi_manager = new_test_user(
            cls.env, login='ntp.access.multi.manager@example.test',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_manager',
            company_id=cls.company.id,
            company_ids=[(6, 0, (cls.company | cls.other_company).ids)],
        )
        cls.supervisor = new_test_user(
            cls.env, login='ntp.access.supervisor@example.test',
            groups='base.group_user,furniture_mrp.group_furniture_mrp_supervisor_painting',
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)],
        )
        cls.Piece = cls.env['furniture.need.to.produce']
        cls.Stage = cls.env['furniture.need.to.produce.stage']
        cls.Material = cls.env['furniture.need.to.produce.material']
        cls.Input = cls.env['furniture.need.to.produce.input']

    def _records(self, company=None):
        """A minimal stage/source relationship, without creating or approving MOs."""
        company = company or self.company
        piece = self.Piece.sudo().create({
            'name': 'NTP access piece / %s' % self._testMethodName,
            'company_id': company.id, 'product_id': self.product.id,
            'furniture_model_id': self.model.id, 'bom_id': self.boms[company.id].id,
            'target_lane': 'packaging', 'state': 'draft',
        })
        stage = self.Stage.sudo().create({
            'piece_id': piece.id, 'sequence': 10, 'plan_key': 'painting-access',
            'lane': 'painting', 'stage_code': 'painting', 'stage_codes': ['painting'],
            'quantity': 1, 'estimated_hours': 2,
        })
        target = self.Stage.sudo().create({
            'piece_id': piece.id, 'sequence': 20, 'plan_key': 'packaging-access',
            'lane': 'packaging', 'stage_code': 'packaging', 'stage_codes': ['packaging'],
            'quantity': 1, 'estimated_hours': 2,
        })
        material = self.Material.sudo().create({
            **self._material_values(), 'stage_id': stage.id,
        })
        claim = self.Input.sudo().create({
            'stage_id': target.id, 'role': 'painting', 'kind': 'stage',
            'source_stage_id': stage.id, 'quantity': 1,
        })
        return {'piece': piece, 'stage': stage, 'target': target,
                'material': material, 'claim': claim}

    def _material_values(self, product=None, quantity=1):
        return {'stage_code': 'painting', 'product_id': (product or self.raw).id,
                'uom_id': self.unit.id, 'quantity': quantity}

    def _as_user(self, records, user=None, companies=None):
        user = user or self.manager
        companies = companies or self.company
        return records.with_user(user).with_context(allowed_company_ids=companies.ids)

    def test_manager_can_edit_materials_through_real_stage_form_write(self):
        rows, other = self._records(), self._records()
        original_bom = self.boms[self.company.id].furniture_stage_material_line_ids.mapped('product_qty')
        manager_stage = self._as_user(rows['stage'])
        self.assertFalse(manager_stage.env.su)
        manager_stage.write({'material_ids': [
            (1, rows['material'].id, {'quantity': 3}),
            (0, 0, self._material_values(product=self.alternate_raw, quantity=2)),
        ]})
        rows['stage'].invalidate_recordset()
        rows['material'].invalidate_recordset()
        self.assertEqual(rows['material'].quantity, 3)
        self.assertTrue(rows['material'].customized)
        alternate = rows['stage'].material_ids.filtered(lambda m: m.product_id == self.alternate_raw)
        self.assertEqual(len(alternate), 1)
        self.assertEqual(alternate.quantity, 2)
        self.assertTrue(alternate.customized)
        self.assertTrue(rows['stage'].materials_customized)
        self.assertEqual(other['material'].quantity, 1)
        self.assertFalse(other['stage'].materials_customized)
        self.assertEqual(self.boms[self.company.id].furniture_stage_material_line_ids.mapped('product_qty'), original_bom)
        manager_stage.write({'material_ids': [(2, alternate.id, 0)]})
        self.assertFalse(alternate.exists())
        self.assertEqual(rows['stage'].material_ids, rows['material'])

    def test_custom_choice_requires_manager_and_allowed_company(self):
        rows = self._records()
        with self.assertRaises(AccessError):
            self._as_user(rows['piece'], self.supervisor).action_toggle_custom()
        other = self._records(self.other_company)
        with self.assertRaises(AccessError):
            self._as_user(other['piece']).action_toggle_custom()
        with self.assertRaises(UserError):
            self._as_user(rows['piece']).action_toggle_custom()
        self.assertFalse(rows['piece'].is_custom)
        self.assertFalse(other['piece'].is_custom)
        self.assertEqual(rows['piece'].state, 'draft')

    def test_custom_flag_updates_after_real_form_material_changes(self):
        rows = self._records()
        self.assertFalse(rows['piece'].is_custom)
        self._as_user(rows['stage']).write({'material_ids': [(1, rows['material'].id, {'quantity': 4})]})
        self.assertTrue(rows['piece'].is_custom)
        self.assertTrue(rows['piece'].custom_bom_modified)

    def test_manager_can_open_stage_review_but_not_change_route(self):
        rows = self._records()
        action = self._as_user(rows['stage']).action_open()
        self.assertEqual(action['res_id'], rows['stage'].id)
        self.assertEqual(action['target'], 'new')
        for values in ({'quantity': 2}, {'lane': 'tailoring'}, {'piece_id': rows['piece'].id},
                       {'material_ids': [(1, rows['material'].id, {'quantity': 8})], 'quantity': 2}):
            with self.subTest(values=values), self.assertRaises(AccessError):
                self._as_user(rows['stage']).write(values)
        self.assertEqual(rows['material'].quantity, 1)
        self.assertEqual(rows['stage'].quantity, 1)

    def test_managers_read_all_four_models_but_supervisors_do_not(self):
        rows = self._records()
        fields_by_key = {'piece': 'name', 'stage': 'lane', 'material': 'quantity', 'claim': 'quantity'}
        self.assertFalse(self.supervisor.has_group('furniture_mrp.group_furniture_mrp_manager'))
        for key, field in fields_by_key.items():
            with self.subTest(model=rows[key]._name):
                self.assertEqual(self._as_user(rows[key]).read([field])[0]['id'], rows[key].id)
                with self.assertRaises(AccessError):
                    self._as_user(rows[key], self.supervisor).read([field])

    def test_supervisor_cannot_approve_refresh_or_open_private_plan(self):
        rows = self._records()
        for action in (
            lambda: self._as_user(rows['piece'], self.supervisor).action_approve(),
            lambda: self._as_user(self.Piece, self.supervisor).action_refresh(),
            lambda: self._as_user(rows['stage'], self.supervisor).action_open(),
            lambda: self._as_user(rows['piece'], self.supervisor).action_open_productions(),
        ):
            with self.assertRaises(AccessError):
                action()
        self.assertEqual(rows['piece'].state, 'draft')
        self.assertFalse(rows['stage'].production_id)

    def test_supervisor_cannot_create_any_planning_record(self):
        rows = self._records()
        values = [
            (self.Piece, {'name': 'blocked', 'company_id': self.company.id,
                          'product_id': self.product.id, 'furniture_model_id': self.model.id,
                          'bom_id': self.boms[self.company.id].id}),
            (self.Stage, {'piece_id': rows['piece'].id, 'plan_key': 'blocked',
                          'lane': 'painting', 'stage_code': 'painting', 'stage_codes': ['painting']}),
            (self.Material, {**self._material_values(), 'stage_id': rows['stage'].id}),
            (self.Input, {'stage_id': rows['target'].id, 'role': 'painting',
                          'kind': 'stage', 'source_stage_id': rows['stage'].id, 'quantity': 1}),
        ]
        for model, vals in values:
            with self.subTest(model=model._name), self.assertRaises(AccessError):
                self._as_user(model, self.supervisor).create(vals)

    def test_allocations_cannot_be_modified_or_unlinked_by_any_non_sudo_user(self):
        rows = self._records()
        for user in (self.manager, self.supervisor):
            with self.subTest(user=user.login):
                claim = self._as_user(rows['claim'], user)
                with self.assertRaises(AccessError):
                    claim.write({'quantity': 999})
                with self.assertRaises(AccessError):
                    claim.unlink()
        self.assertTrue(rows['claim'].exists())
        self.assertEqual(rows['claim'].quantity, 1)

    def test_manager_cannot_forge_plan_state_or_delete_piece_and_stage(self):
        rows = self._records()
        for key in ('piece', 'stage'):
            with self.subTest(model=rows[key]._name), self.assertRaises(AccessError):
                self._as_user(rows[key]).unlink()
        with self.assertRaises(AccessError):
            self._as_user(rows['piece']).write({'state': 'approved'})
        self.assertEqual(rows['piece'].state, 'draft')

    def test_approved_materials_reject_direct_and_stage_form_mutations(self):
        rows = self._records()
        # Set state only to isolate the guard: manufacturing approval itself is
        # exercised by test_production_plan, not faked in this permission test.
        rows['piece'].sudo().write({'state': 'approved'})
        direct = self._as_user(rows['material'])
        for mutation in (
            lambda: direct.write({'quantity': 9}),
            lambda: direct.unlink(),
            lambda: self._as_user(self.Material).create({**self._material_values(), 'stage_id': rows['stage'].id}),
            lambda: self._as_user(rows['stage']).write({'material_ids': [(1, direct.id, {'quantity': 9})]}),
            lambda: self._as_user(rows['stage']).write({'material_ids': [(2, direct.id, 0)]}),
            lambda: self._as_user(rows['stage']).write({'material_ids': [(0, 0, self._material_values())]}),
            lambda: self._as_user(rows['stage']).write({'material_ids': [(5, 0, 0)]}),
        ):
            with self.assertRaises(AccessError):
                mutation()
        self.assertTrue(rows['material'].exists())
        self.assertEqual(rows['material'].quantity, 1)

    def test_supervisor_cannot_edit_materials_even_on_draft(self):
        rows = self._records()
        for mutation in (
            lambda: self._as_user(rows['material'], self.supervisor).write({'quantity': 3}),
            lambda: self._as_user(rows['material'], self.supervisor).unlink(),
            lambda: self._as_user(rows['stage'], self.supervisor).write({'material_ids': [(1, rows['material'].id, {'quantity': 3})]}),
        ):
            with self.assertRaises(AccessError):
                mutation()
        self.assertEqual(rows['material'].quantity, 1)

    def test_company_rules_hide_each_model_from_other_company(self):
        local, foreign = self._records(), self._records(self.other_company)
        fields_by_key = {'piece': 'name', 'stage': 'lane', 'material': 'quantity', 'claim': 'quantity'}
        for key, field in fields_by_key.items():
            model = self.env[foreign[key]._name]
            with self.subTest(model=model._name):
                visible = self._as_user(model).search([('id', 'in', [local[key].id, foreign[key].id])])
                self.assertEqual(visible.ids, local[key].ids)
                with self.assertRaises(AccessError):
                    self._as_user(foreign[key]).read([field])

    def test_manager_cannot_edit_or_approve_outside_allowed_company(self):
        foreign = self._records(self.other_company)
        for mutation in (
            lambda: self._as_user(foreign['piece']).action_approve(),
            lambda: self._as_user(foreign['stage']).action_open(),
            lambda: self._as_user(foreign['material']).write({'quantity': 4}),
            lambda: self._as_user(foreign['material']).unlink(),
            lambda: self._as_user(self.Material).create({**self._material_values(), 'stage_id': foreign['stage'].id}),
            lambda: self._as_user(foreign['stage']).write({'material_ids': [(1, foreign['material'].id, {'quantity': 4})]}),
        ):
            with self.assertRaises(AccessError):
                mutation()
        self.assertEqual(foreign['piece'].state, 'draft')
        self.assertEqual(foreign['material'].quantity, 1)

    def test_allowed_company_context_cannot_be_spoofed(self):
        foreign = self._records(self.other_company)
        spoofed = self._as_user(foreign['stage'], companies=self.other_company)
        with self.assertRaises(AccessError):
            spoofed.action_open()
        with self.assertRaises(AccessError):
            spoofed.write({'material_ids': [(1, foreign['material'].id, {'quantity': 4})]})
        self.assertEqual(foreign['material'].quantity, 1)

    def test_authorized_multi_company_manager_must_enable_target_company(self):
        foreign = self._records(self.other_company)
        disabled = self._as_user(foreign['stage'], self.multi_manager, self.company)
        with self.assertRaises(AccessError):
            disabled.action_open()
        enabled = self._as_user(foreign['stage'], self.multi_manager, self.other_company)
        self.assertEqual(enabled.action_open()['res_id'], foreign['stage'].id)
        enabled.write({'material_ids': [(1, foreign['material'].id, {'quantity': 4})]})
        self.assertEqual(foreign['material'].quantity, 4)

    def test_material_cannot_be_reparented_to_another_piece(self):
        first, second = self._records(), self._records()
        with self.assertRaises(AccessError):
            self._as_user(first['material']).write({'stage_id': second['stage'].id})
        self.assertEqual(first['material'].stage_id, first['stage'])
