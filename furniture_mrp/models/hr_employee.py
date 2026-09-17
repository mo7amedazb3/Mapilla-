# -*- coding: utf-8 -*-
from odoo import Command, api, fields, models


SUPERVISOR_STAGE_GROUP_XMLIDS = {
    'priming': 'furniture_mrp.group_furniture_mrp_supervisor_priming',
    'painting': 'furniture_mrp.group_furniture_mrp_supervisor_painting',
    'carpentry': 'furniture_mrp.group_furniture_mrp_supervisor_carpentry',
    'bases': 'furniture_mrp.group_furniture_mrp_supervisor_bases',
    'finishing': 'furniture_mrp.group_furniture_mrp_supervisor_finishing',
    'tailoring': 'furniture_mrp.group_furniture_mrp_supervisor_tailoring',
    'sewing': 'furniture_mrp.group_furniture_mrp_supervisor_sewing',
    'upholstery': 'furniture_mrp.group_furniture_mrp_supervisor_upholstery',
    'packaging': 'furniture_mrp.group_furniture_mrp_supervisor_packaging',
}

SUPERVISOR_GROUP_SYNC_FIELDS = {
    'active',
    'user_id',
    'furniture_mrp_role',
    'furniture_mrp_supervisor_stage_ids',
}


class FurnitureMrpEmployeeStage(models.Model):
    _name = 'furniture.mrp.employee.stage'
    _description = 'أقسام موظفي إنتاج الأثاث'
    _order = 'sequence, id'

    name = fields.Char(string='القسم', required=True, translate=True)
    code = fields.Selection(
        [
            ('priming', 'التقديم'),
            ('painting', 'تصنيع دهانات'),
            ('carpentry', 'تجميع'),
            ('bases', 'القواعد'),
            ('finishing', 'تجهيز'),
            ('tailoring', 'تفصيل'),
            ('sewing', 'الخياطة'),
            ('upholstery', 'كسوه'),
            ('packaging', 'التغليف'),
        ],
        string='الكود',
        required=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'كل قسم إنتاج يجب أن يكون له كود واحد فقط.'),
    ]


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    furniture_mrp_factory_department = fields.Selection(
        [
            ('administration', 'إدارة'),
            ('carpentry', 'نجارة'),
            ('upholstery', 'كسوة'),
            ('packaging', 'تغليف'),
            ('purchases', 'مشتريات'),
            ('warehouse', 'أمين مخزن'),
            ('buffet', 'بوفيه'),
            ('tailoring', 'تفصيل'),
            ('finishing', 'تجهيز'),
        ],
        string='قسم المصنع',
        tracking=True,
        index=True,
        help='التصنيف الإداري الفعلي للموظف، بما في ذلك الأقسام غير المرتبطة بمراحل إنتاج.',
    )

    furniture_mrp_role = fields.Selection(
        [
            ('worker', 'عامل'),
            ('supervisor', 'مشرف'),
            ('storekeeper', 'أمين المخزن'),
        ],
        string='دور إنتاج الأثاث',
        tracking=True,
    )
    furniture_mrp_worker_stage_ids = fields.Many2many(
        'furniture.mrp.employee.stage',
        'furniture_mrp_employee_worker_stage_rel',
        'employee_id',
        'stage_id',
        string='أقسام العامل',
    )
    furniture_mrp_supervisor_stage_ids = fields.Many2many(
        'furniture.mrp.employee.stage',
        'furniture_mrp_employee_supervisor_stage_rel',
        'employee_id',
        'stage_id',
        string='المراحل التي يشرف عليها',
    )

    @api.model
    def _furniture_mrp_supervisor_groups_by_stage(self):
        return {
            stage_code: self.env.ref(group_xmlid)
            for stage_code, group_xmlid in SUPERVISOR_STAGE_GROUP_XMLIDS.items()
        }

    def _sync_furniture_mrp_supervisor_groups(self, users=None):
        """Keep only the module-managed supervisor groups aligned to employees.

        A user can represent employees in more than one company, so the desired
        permissions are the union of every active supervisor employee linked to
        that user.  Unrelated/manual groups are intentionally left untouched.
        """
        affected_users = users or self.mapped('user_id')
        affected_users = affected_users.sudo().with_context(active_test=False)
        if not affected_users:
            return

        generic_group = self.env.ref(
            'furniture_mrp.group_furniture_mrp_supervisor'
        )
        dashboard_action = self.env.ref(
            'furniture_mrp.action_furniture_mrp_stage_dashboard_page'
        )
        stage_groups_by_code = self._furniture_mrp_supervisor_groups_by_stage()
        managed_groups = generic_group
        for stage_group in stage_groups_by_code.values():
            managed_groups |= stage_group

        empty_groups = self.env['res.groups']
        desired_by_user = {
            user.id: empty_groups
            for user in affected_users
        }
        active_supervisors = self.sudo().with_context(active_test=False).search([
            ('active', '=', True),
            ('user_id', 'in', affected_users.ids),
            ('furniture_mrp_role', '=', 'supervisor'),
        ])
        for employee in active_supervisors:
            desired_groups = desired_by_user[employee.user_id.id] | generic_group
            for stage_code in employee.furniture_mrp_supervisor_stage_ids.mapped('code'):
                desired_groups |= stage_groups_by_code.get(
                    stage_code,
                    empty_groups,
                )
            desired_by_user[employee.user_id.id] = desired_groups

        for user in affected_users:
            desired_groups = desired_by_user[user.id]
            current_managed_groups = user.groups_id & managed_groups
            groups_to_remove = current_managed_groups - desired_groups
            groups_to_add = desired_groups - current_managed_groups
            commands = [
                Command.unlink(group.id)
                for group in groups_to_remove.sorted('id')
            ] + [
                Command.link(group.id)
                for group in groups_to_add.sorted('id')
            ]
            values = {}
            if commands:
                values['groups_id'] = commands
            if desired_groups:
                if user.action_id.id != dashboard_action.id:
                    values['action_id'] = dashboard_action.id
            elif user.action_id.id == dashboard_action.id:
                values['action_id'] = False
            if values:
                user.sudo().write(values)

    @api.model
    def _sync_all_furniture_mrp_supervisor_groups(self):
        employees = self.sudo().with_context(active_test=False).search([
            ('user_id', '!=', False),
        ])
        employees._sync_furniture_mrp_supervisor_groups(
            users=employees.mapped('user_id'),
        )

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        employees._sync_furniture_mrp_supervisor_groups()
        return employees

    def write(self, vals):
        users_before = self.mapped('user_id')
        result = super().write(vals)
        if SUPERVISOR_GROUP_SYNC_FIELDS.intersection(vals):
            self._sync_furniture_mrp_supervisor_groups(
                users=users_before | self.mapped('user_id'),
            )
        return result

    def unlink(self):
        affected_users = self.mapped('user_id')
        result = super().unlink()
        self.env['hr.employee']._sync_furniture_mrp_supervisor_groups(
            users=affected_users,
        )
        return result
