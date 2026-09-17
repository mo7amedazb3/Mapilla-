# -*- coding: utf-8 -*-
from lxml import etree

from odoo.tests.common import TransactionCase


class TestHrEmployeeFactoryDepartment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supervisor_group = cls.env.ref(
            'furniture_mrp.group_furniture_mrp_supervisor'
        )
        cls.priming_supervisor_group = cls.env.ref(
            'furniture_mrp.group_furniture_mrp_supervisor_priming'
        )
        cls.carpentry_supervisor_group = cls.env.ref(
            'furniture_mrp.group_furniture_mrp_supervisor_carpentry'
        )
        cls.upholstery_supervisor_group = cls.env.ref(
            'furniture_mrp.group_furniture_mrp_supervisor_upholstery'
        )

    def test_support_and_production_departments_are_recorded(self):
        administration = self.env['hr.employee'].create({
            'name': 'موظف إدارة تجريبي',
            'furniture_mrp_factory_department': 'administration',
        })
        buffet = self.env['hr.employee'].create({
            'name': 'موظف بوفيه تجريبي',
            'furniture_mrp_factory_department': 'buffet',
        })
        carpenter = self.env['hr.employee'].create({
            'name': 'عامل نجارة تجريبي',
            'furniture_mrp_factory_department': 'carpentry',
            'furniture_mrp_role': 'worker',
        })

        self.assertEqual(administration.furniture_mrp_factory_department, 'administration')
        self.assertFalse(administration.furniture_mrp_role)
        self.assertEqual(buffet.furniture_mrp_factory_department, 'buffet')
        self.assertEqual(carpenter.furniture_mrp_factory_department, 'carpentry')
        self.assertEqual(carpenter.furniture_mrp_role, 'worker')

    def test_supervisor_stages_sync_managed_user_groups(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'مستخدم مشرف متعدد المراحل',
            'login': 'multi.stage.supervisor@example.test',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('stock.group_stock_user').id,
            ])],
        })
        stages = (
            self.env.ref('furniture_mrp.employee_stage_priming')
            | self.env.ref('furniture_mrp.employee_stage_carpentry')
        )
        supervisor = self.env['hr.employee'].create({
            'name': 'مشرف متعدد المراحل',
            'user_id': user.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })

        self.assertIn(self.supervisor_group, user.groups_id)
        self.assertIn(self.priming_supervisor_group, user.groups_id)
        self.assertIn(self.carpentry_supervisor_group, user.groups_id)
        self.assertIn(self.env.ref('stock.group_stock_user'), user.groups_id)
        self.assertEqual(
            user.action_id.id,
            self.env.ref(
                'furniture_mrp.action_furniture_mrp_stage_dashboard_page'
            ).id,
        )

        supervisor.write({
            'furniture_mrp_supervisor_stage_ids': [(6, 0, [
                self.env.ref('furniture_mrp.employee_stage_carpentry').id,
                self.env.ref('furniture_mrp.employee_stage_upholstery').id,
            ])],
        })

        self.assertNotIn(self.priming_supervisor_group, user.groups_id)
        self.assertIn(self.carpentry_supervisor_group, user.groups_id)
        self.assertIn(self.upholstery_supervisor_group, user.groups_id)
        self.assertIn(self.env.ref('stock.group_stock_user'), user.groups_id)

        supervisor.write({'furniture_mrp_role': 'worker'})

        managed_groups = (
            self.supervisor_group
            | self.priming_supervisor_group
            | self.carpentry_supervisor_group
            | self.upholstery_supervisor_group
        )
        self.assertFalse(user.groups_id & managed_groups)
        self.assertIn(self.env.ref('stock.group_stock_user'), user.groups_id)
        self.assertFalse(user.action_id)

    def test_archiving_supervisor_removes_managed_groups(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'مستخدم مشرف مؤرشف',
            'login': 'archived.supervisor@example.test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        supervisor = self.env['hr.employee'].create({
            'name': 'مشرف سيؤرشف',
            'user_id': user.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, [
                self.env.ref('furniture_mrp.employee_stage_priming').id,
            ])],
        })
        self.assertIn(self.priming_supervisor_group, user.groups_id)
        self.assertEqual(
            user.action_id.id,
            self.env.ref(
                'furniture_mrp.action_furniture_mrp_stage_dashboard_page'
            ).id,
        )

        supervisor.active = False

        self.assertNotIn(self.priming_supervisor_group, user.groups_id)
        self.assertNotIn(self.supervisor_group, user.groups_id)
        self.assertFalse(user.action_id)

    def test_stage_supervisor_groups_are_ready_for_future_permissions(self):
        groups_by_stage = self.env[
            'hr.employee'
        ]._furniture_mrp_supervisor_groups_by_stage()

        self.assertEqual(
            set(groups_by_stage),
            {
                'priming', 'painting', 'carpentry', 'bases', 'finishing',
                'tailoring', 'sewing', 'upholstery', 'packaging',
            },
        )
        for group in groups_by_stage.values():
            self.assertIn(self.supervisor_group, group.implied_ids)

    def test_supervisor_role_controls_employee_form(self):
        view = self.env.ref('furniture_mrp.view_hr_employee_form_furniture_mrp')
        document = etree.fromstring(view.arch_db.encode())

        role_fields = document.xpath(
            ".//field[@name='furniture_mrp_role']"
        )
        supervisor_stages = document.xpath(
            ".//field[@name='furniture_mrp_supervisor_stage_ids']"
        )

        self.assertEqual(len(role_fields), 1)
        self.assertFalse(document.xpath(
            ".//field[@name='furniture_mrp_is_supervisor']"
        ))
        self.assertEqual(len(supervisor_stages), 1)
        self.assertEqual(
            supervisor_stages[0].get('invisible'),
            "furniture_mrp_role != 'supervisor'",
        )
