from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestFurnitureMrpMPSSchedule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'furniture_mrp.mps_enabled',
            'True',
        )
        cls.company = cls.env['res.company'].create({
            'name': 'MPS Schedule Test Company',
        })
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'MPS 10 Hours Six Days',
            'company_id': cls.company.id,
            'tz': 'UTC',
            'hours_per_day': 10.0,
            'attendance_ids': [
                (0, 0, {
                    'name': 'MPS Work Day',
                    'dayofweek': day,
                    'hour_from': 8.0,
                    'hour_to': 18.0,
                    'day_period': 'morning',
                })
                for day in ('0', '1', '2', '3', '5', '6')
            ],
        })
        cls.company.resource_calendar_id = cls.calendar
        cls.test_env = cls.env['base'].with_company(cls.company).env
        cls.model = cls.test_env['furniture.product.model'].create({
            'name': 'MPS Big Moon Test Model',
        })
        cls.sofa, cls.sofa_bom = cls._new_finished_product('MPS Large Sofa')
        cls.chaise, cls.chaise_bom = cls._new_finished_product('MPS Chaise')
        cls.chair, cls.chair_bom = cls._new_finished_product('MPS Armchair')
        cls.profile = cls.test_env['furniture.mrp.mps.profile'].create({
            'name': 'MPS Test Profile',
            'company_id': cls.company.id,
            'furniture_model_id': cls.model.id,
            'product_ids': [(6, 0, [cls.sofa.id, cls.chaise.id, cls.chair.id])],
        })
        cls.priming_stage = cls.env.ref('furniture_mrp.employee_stage_priming')
        cls.tailoring_stage = cls.env.ref('furniture_mrp.employee_stage_tailoring')
        cls.workers = cls.env['hr.employee']
        for index in range(5):
            cls.workers |= cls._new_worker(
                'MPS Priming Worker %s' % (index + 1),
                cls.priming_stage,
            )
        cls.production = cls._new_production(
            sofa_qty=10.0,
            chaise_qty=10.0,
            chair_qty=20.0,
            stages=('priming',),
        )

    @classmethod
    def _new_finished_product(cls, name):
        product = cls.test_env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
        })
        visible_bom = cls.test_env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'company_id': cls.company.id,
            'furniture_product_id': product.id,
            'furniture_model_id': cls.model.id,
            'use_priming': True,
            'use_painting': True,
            'use_carpentry': True,
            'use_bases': True,
            'use_finishing': True,
            'use_tailoring': True,
            'use_upholstery': True,
            'use_packaging': True,
        })
        bom = cls.test_env['mrp.bom']._find_furniture_normal_recipe(
            product,
            cls.model,
            cls.company,
        )
        if not bom or bom.furniture_parent_bom_id != visible_bom:
            raise AssertionError('The exact saved model recipe was not created.')
        return product, bom

    @classmethod
    def _new_worker(cls, name, *stages):
        user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': name,
            'login': '%s@example.invalid' % name.lower().replace(' ', '.'),
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        employee = cls.test_env['hr.employee'].create({
            'name': name,
            'company_id': cls.company.id,
            'user_id': user.id,
            'resource_calendar_id': cls.calendar.id,
            'furniture_mrp_role': 'worker',
            'furniture_mrp_worker_stage_ids': [(6, 0, [stage.id for stage in stages])],
        })
        cls.test_env['hr.contract'].create({
            'name': '%s Contract' % name,
            'employee_id': employee.id,
            'company_id': cls.company.id,
            'state': 'open',
            'date_start': date(2026, 1, 1),
            'wage': 6000.0,
            'resource_calendar_id': cls.calendar.id,
        })
        return employee

    @classmethod
    def _new_production(
        cls,
        sofa_qty=1.0,
        chaise_qty=1.0,
        chair_qty=2.0,
        stages=('priming',),
        lane=False,
    ):
        stage_values = {
            'use_%s' % stage: True
            for stage in stages
        }
        production_values = {
            'name': cls.env['ir.sequence'].next_by_code('furniture.mrp.production') or 'MPS/TEST',
            'company_id': cls.company.id,
            'furniture_order_model_id': cls.model.id,
            'product_qty': sofa_qty + chaise_qty + chair_qty,
            'date_planned_start': datetime(2026, 8, 24, 8, 0, 0),
            'date_planned_finish': datetime(2026, 8, 30, 18, 0, 0),
            **stage_values,
        }
        if lane:
            production_values['production_lane'] = lane
        production = cls.test_env['furniture.mrp.production'].with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
        ).create(production_values)
        for sequence, product, bom, quantity in (
            (10, cls.sofa, cls.sofa_bom, sofa_qty),
            (20, cls.chaise, cls.chaise_bom, chaise_qty),
            (30, cls.chair, cls.chair_bom, chair_qty),
        ):
            if quantity <= 0:
                continue
            cls.test_env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
            ).create({
                'production_id': production.id,
                'sequence': sequence,
                'product_id': product.id,
                'product_qty': quantity,
                'bom_id': bom.id,
                'furniture_order_model_id': cls.model.id,
                'stage_selection_initialized': True,
                **stage_values,
            })
        return production

    def _new_operation(
        self,
        code,
        name,
        stage,
        duration,
        outputs,
        mode='stage_pool_equal',
        sequence=10,
        fabric_type=False,
        dependencies=False,
        same_worker_key=False,
        different_worker_key=False,
    ):
        operation = self.test_env['furniture.mrp.mps.operation'].create({
            'name': name,
            'code': code,
            'profile_id': self.profile.id,
            'stage': stage,
            'duration_hours': duration,
            'distribution_mode': mode,
            'sequence': sequence,
            'fabric_type_id': fabric_type.id if fabric_type else False,
            'dependency_ids': [(6, 0, dependencies.ids)] if dependencies else False,
            'same_worker_key': same_worker_key,
            'different_worker_key': different_worker_key,
            'output_line_ids': [
                (0, 0, {
                    'product_id': product.id,
                    'quantity': quantity,
                })
                for product, quantity in outputs
            ],
        })
        return operation

    def _plan(self, production=None):
        production = production or self.production
        production.state = 'confirmed'
        return production._generate_mps_schedule().ensure_one()

    def test_confirm_generates_one_idempotent_plan(self):
        self._new_operation(
            'confirm_priming',
            'Confirm Priming',
            'priming',
            5.0,
            ((self.sofa, 1.0),),
        )
        self.production.action_confirm()
        plan = self.production.mps_schedule_plan_ids.ensure_one()
        self.assertTrue(plan.assignment_ids)
        self.assertEqual(plan.production_id, self.production)

        self.production.write({'state': 'draft'})
        self.production.action_confirm()
        self.assertEqual(len(self.production.mps_schedule_plan_ids), 1)
        self.assertEqual(
            len(self.production.mps_schedule_plan_ids.assignment_ids),
            len(plan.assignment_ids),
        )

    def test_supervisor_confirm_can_generate_read_only_schedule(self):
        # The legacy test name is kept for stable test tags.  Production
        # orders are now read-only for stage supervisors: an exact-stage
        # supervisor can inspect the generated MPS plan but cannot confirm the
        # production order that creates it.
        self._new_operation(
            'supervisor_priming',
            'Supervisor Priming',
            'priming',
            5.0,
            ((self.sofa, 1.0),),
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        supervisor = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'MPS Read-only Supervisor',
            'login': 'mps.readonly.supervisor@example.invalid',
            'email': 'mps.readonly.supervisor@example.invalid',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
            ])],
        })
        self.test_env['hr.employee'].create({
            'name': supervisor.name,
            'company_id': self.company.id,
            'user_id': supervisor.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, [
                self.priming_stage.id,
            ])],
        })

        self.assertEqual(
            production.with_user(supervisor).state,
            'draft',
        )
        with self.assertRaises(AccessError):
            production.with_user(supervisor).action_confirm()

        production.action_confirm()

        plan = production.with_user(
            supervisor,
        ).mps_schedule_plan_ids.ensure_one()
        self.assertTrue(plan.assignment_ids)
        self.assertEqual(plan.state, 'planned')
        with self.assertRaises(AccessError):
            plan.with_user(supervisor).write({'state': 'cancelled'})

    def test_worker_sees_only_own_schedule_and_cannot_open_full_plan(self):
        self._new_operation(
            'personal_schedule',
            'Personal Schedule',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
        )
        plan = self._plan()
        worker = self.workers[0]
        worker_assignments = self.test_env[
            'furniture.mrp.mps.assignment'
        ].with_user(worker.user_id).search([
            ('plan_id', '=', plan.id),
        ])
        self.assertEqual(len(worker_assignments), 1)
        self.assertEqual(worker_assignments.employee_id, worker)
        worker_outputs = self.test_env[
            'furniture.mrp.mps.assignment.output'
        ].with_user(worker.user_id).search([
            ('plan_id', '=', plan.id),
        ])
        self.assertTrue(worker_outputs)
        self.assertEqual(worker_outputs.mapped('employee_id'), worker)
        self.assertEqual(
            worker_outputs.mapped('assignment_id'),
            worker_assignments,
        )
        self.assertEqual(
            self.test_env['furniture.mrp.mps.plan'].with_user(
                worker.user_id,
            ).search([('id', '=', plan.id)]),
            plan,
        )

        unscheduled = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        unscheduled.state = 'confirmed'
        with self.assertRaises(AccessError):
            unscheduled.with_user(worker.user_id).action_view_mps_schedule()
        self.assertFalse(unscheduled.mps_schedule_plan_ids)

    def test_single_worker_batches_scale_and_use_different_workers(self):
        self._new_operation(
            'priming_pair',
            'Priming Sofa and Chaise',
            'priming',
            27.0,
            ((self.sofa, 9.0), (self.chaise, 9.0)),
            mode='single',
            sequence=10,
            different_worker_key='priming_pair_of_workers',
        )
        self._new_operation(
            'priming_chair',
            'Priming Chairs',
            'priming',
            16.0,
            ((self.chair, 40.0),),
            mode='single',
            sequence=20,
            different_worker_key='priming_pair_of_workers',
        )
        plan = self._plan()
        pair_line = plan.line_ids.filtered(
            lambda line: line.operation_id.code == 'priming_pair'
        ).ensure_one()
        chair_line = plan.line_ids.filtered(
            lambda line: line.operation_id.code == 'priming_chair'
        ).ensure_one()

        self.assertAlmostEqual(pair_line.labor_hours, 30.0)
        self.assertAlmostEqual(chair_line.labor_hours, 8.0)
        self.assertEqual(len(pair_line.assignment_ids), 1)
        self.assertEqual(len(chair_line.assignment_ids), 1)
        self.assertNotEqual(
            pair_line.assignment_ids.employee_id,
            chair_line.assignment_ids.employee_id,
        )

    def test_unspecified_worker_count_balances_hours_on_stage_pool(self):
        self._new_operation(
            'equal_priming',
            'Equal Priming',
            'priming',
            38.0,
            ((self.sofa, 10.0), (self.chaise, 10.0), (self.chair, 20.0)),
        )
        plan = self._plan()
        line = plan.line_ids.ensure_one()
        self.assertEqual(len(line.assignment_ids), 5)
        for assignment in line.assignment_ids:
            self.assertAlmostEqual(assignment.planned_hours, 7.6)
            self.assertIn('%s × 2' % self.sofa.display_name, assignment.output_summary)
            self.assertIn('%s × 2' % self.chaise.display_name, assignment.output_summary)
            self.assertIn('%s × 4' % self.chair.display_name, assignment.output_summary)
        self.assertAlmostEqual(sum(line.assignment_ids.mapped('planned_hours')), 38.0)

    def test_stage_pool_splits_ten_whole_products_two_per_worker(self):
        self._new_operation(
            'ten_whole_sofas',
            'Ten Whole Sofas',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
        )
        production = self._new_production(
            sofa_qty=10.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )

        line = self._plan(production).line_ids.ensure_one()
        assignments = line.assignment_ids

        self.assertEqual(line.distribution_mode, 'stage_pool_equal')
        self.assertEqual(len(assignments), 5)
        self.assertEqual(len(assignments.employee_id), 5)
        self.assertEqual(
            sorted(assignments.output_line_ids.mapped('quantity')),
            [2, 2, 2, 2, 2],
        )
        self.assertEqual(sum(assignments.mapped('planned_atom_count')), 10)
        self.assertEqual(sum(assignments.mapped('piece_count')), 10)
        self.assertEqual(sum(assignments.output_line_ids.mapped('quantity')), 10)

    def test_weekly_wage_is_monthly_wage_divided_by_four(self):
        worker = self.workers[0]
        contract = self.test_env['hr.contract'].search([
            ('employee_id', '=', worker.id),
            ('state', '=', 'open'),
        ], limit=1)

        self.assertAlmostEqual(contract.wage, 6000.0)
        self.assertAlmostEqual(contract.furniture_weekly_wage, 1500.0)

        contract.furniture_weekly_wage = 1750.0

        self.assertAlmostEqual(contract.wage, 7000.0)

    def test_piece_worker_completion_freezes_weekly_pay_and_attendance(self):
        self._new_operation(
            'piece_pay_sofa',
            'Piece Pay Sofa',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production = self._new_production(
            sofa_qty=2.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        assignment = self._plan(
            production,
        ).assignment_ids.ensure_one()
        worker = assignment.employee_id
        worker.write({
            'furniture_pay_basis': 'production',
            'furniture_piece_rate': 100.0,
        })

        assignment.write({'state': 'done'})

        self.assertEqual(
            assignment.compensation_basis_snapshot, 'production',
        )
        self.assertEqual(assignment.piece_count, 2)
        self.assertAlmostEqual(assignment.piece_rate_snapshot, 100.0)
        self.assertAlmostEqual(assignment.piece_earning, 200.0)
        self.assertTrue(assignment.completed_at)

        worker.furniture_piece_rate = 250.0
        assignment.invalidate_recordset([
            'piece_rate_snapshot', 'piece_earning',
        ])
        self.assertAlmostEqual(assignment.piece_rate_snapshot, 100.0)
        self.assertAlmostEqual(assignment.piece_earning, 200.0)

        self.test_env['hr.attendance'].create({
            'employee_id': worker.id,
            'check_in': datetime(2026, 8, 24, 9, 0, 0),
            'check_out': datetime(2026, 8, 24, 18, 0, 0),
        })
        week_start, _week_end = worker._furniture_week_bounds(
            fields.Date.context_today(worker),
        )
        wizard = self.test_env[
            'furniture.mrp.employee.pay.report'
        ].create({
            'employee_id': worker.id,
            'week_start': week_start,
            'month_start': date(2026, 8, 24),
            'month_end': date(2026, 8, 25),
        })
        wizard._refresh_report()

        self.assertEqual(wizard.pay_basis, 'production')
        self.assertEqual(wizard.completed_piece_qty, 2)
        self.assertAlmostEqual(wizard.weekly_pay, 200.0)
        self.assertEqual(wizard.scheduled_days, 2)
        self.assertEqual(wizard.attendance_days, 1)
        self.assertEqual(wizard.absence_days, 1)

        slip = self.test_env['simple.payroll.slip'].create({
            'employee_id': worker.id,
            'company_id': self.company.id,
            'date_from': week_start,
            'date_to': _week_end,
        })
        self.assertEqual(slip.furniture_pay_basis, 'production')
        self.assertEqual(slip.furniture_completed_piece_qty, 2)
        self.assertAlmostEqual(slip.furniture_piece_earning, 200.0)
        self.assertAlmostEqual(slip.base_wage, 200.0)
        self.assertAlmostEqual(slip.attendance_deduction, 0.0)
        self.assertAlmostEqual(slip.net_salary, 200.0)
        self.assertEqual(wizard.late_days, 1)
        self.assertAlmostEqual(wizard.late_hours, 1.0)
        self.assertAlmostEqual(wizard.worked_hours, 9.0)
        self.assertEqual(len(wizard.production_line_ids), 1)
        self.assertEqual(len(wizard.attendance_line_ids), 2)

    def test_non_superuser_manager_can_open_employee_pay_report(self):
        manager = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Employee Pay Report Manager',
            'login': 'employee.pay.report.manager@example.invalid',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('hr.group_hr_user').id,
                self.env.ref(
                    'furniture_mrp.group_furniture_mrp_manager'
                ).id,
            ])],
        })
        worker = self.workers[:1]

        action = worker.with_user(
            manager
        ).action_open_furniture_pay_report()

        self.assertEqual(
            action['res_model'], 'furniture.mrp.employee.pay.report',
        )
        report = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(report.employee_id, worker)

    def test_stage_pool_assigns_one_chair_to_one_worker_as_a_whole_piece(self):
        self._new_operation(
            'whole_single_chair',
            'Whole Single Chair',
            'priming',
            10.0,
            ((self.chair, 4.0),),
        )
        production = self._new_production(
            sofa_qty=0.0,
            chaise_qty=0.0,
            chair_qty=1.0,
            stages=('priming',),
        )

        line = self._plan(production).line_ids.ensure_one()
        self.assertEqual(len(line.assignment_ids), 1)
        assignment = line.assignment_ids.ensure_one()
        output = assignment.output_line_ids.ensure_one()

        self.assertEqual(line.atom_count, 1)
        self.assertEqual(line.piece_count, 1)
        self.assertEqual(assignment.planned_atom_count, 1)
        self.assertEqual(assignment.piece_count, 1)
        self.assertEqual(output.product_id, self.chair)
        self.assertEqual(output.quantity, 1)
        self.assertEqual(
            sum(line.assignment_ids.output_line_ids.mapped('quantity')),
            1,
        )
        self.assertAlmostEqual(output.piece_duration_hours, 2.5)
        self.assertAlmostEqual(output.planned_hours, 2.5)
        self.assertAlmostEqual(assignment.planned_hours, 2.5)
        self.assertNotIn('0.25', assignment.output_summary)

    def test_stage_pool_never_uses_more_workers_than_available_pieces(self):
        self._new_operation(
            'whole_three_chairs',
            'Whole Three Chairs',
            'priming',
            10.0,
            ((self.chair, 4.0),),
        )
        production = self._new_production(
            sofa_qty=0.0,
            chaise_qty=0.0,
            chair_qty=3.0,
            stages=('priming',),
        )

        line = self._plan(production).line_ids.ensure_one()

        self.assertEqual(len(line.assignment_ids), 3)
        self.assertEqual(set(line.assignment_ids.mapped('planned_atom_count')), {1})
        self.assertEqual(set(line.assignment_ids.mapped('piece_count')), {1})
        self.assertEqual(
            set(line.assignment_ids.output_line_ids.mapped('quantity')),
            {1},
        )
        self.assertAlmostEqual(
            sum(line.assignment_ids.mapped('planned_hours')),
            line.labor_hours,
        )

    def test_joint_output_atom_stays_a_whole_bundle_per_worker(self):
        self._new_operation(
            'whole_pair_bundle',
            'Whole Sofa Chaise Bundle',
            'priming',
            27.0,
            ((self.sofa, 9.0), (self.chaise, 9.0)),
        )
        production = self._new_production(
            sofa_qty=3.0,
            chaise_qty=3.0,
            chair_qty=0.0,
            stages=('priming',),
        )

        line = self._plan(production).line_ids.ensure_one()

        self.assertEqual(line.atom_count, 3)
        self.assertEqual(len(line.assignment_ids), 3)
        for assignment in line.assignment_ids:
            self.assertEqual(assignment.piece_count, 2)
            self.assertEqual(
                {
                    output.product_id.id: output.quantity
                    for output in assignment.output_line_ids
                },
                {self.sofa.id: 1, self.chaise.id: 1},
            )
            self.assertAlmostEqual(
                sum(assignment.output_line_ids.mapped('planned_hours')),
                assignment.planned_hours,
            )
        self.assertEqual(
            sum(line.assignment_ids.output_line_ids.filtered(
                lambda output: output.product_id == self.sofa
            ).mapped('quantity')),
            3,
        )
        self.assertEqual(
            sum(line.assignment_ids.output_line_ids.filtered(
                lambda output: output.product_id == self.chaise
            ).mapped('quantity')),
            3,
        )

    def test_fractional_finished_piece_blocks_instead_of_splitting_worker_output(self):
        self._new_operation(
            'fractional_chair_block',
            'Fractional Chair Must Block',
            'priming',
            10.0,
            ((self.chair, 4.0),),
        )
        production = self._new_production(
            sofa_qty=0.0,
            chaise_qty=0.0,
            chair_qty=0.5,
            stages=('priming',),
        )

        line = self._plan(production).line_ids.ensure_one()

        self.assertEqual(line.state, 'blocked')
        self.assertIn('عددًا صحيحًا', line.warning_message)
        self.assertFalse(line.assignment_ids)

    def test_cancelled_running_plan_frees_worker_and_reconfirm_rebuilds(self):
        self._new_operation(
            'cancel_reconfirm',
            'Cancel and Reconfirm',
            'priming',
            5.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        plan = self._plan()
        old_assignment = plan.assignment_ids.ensure_one()
        old_assignment.state = 'in_progress'
        old_assignment.line_id.state = 'in_progress'
        plan.state = 'in_progress'

        with self.assertRaises(UserError):
            plan.action_cancel()
        self.production.action_cancel()

        self.assertEqual(plan.state, 'cancelled')
        self.assertEqual(old_assignment.state, 'cancelled')
        self.production.action_reset_to_draft()
        self.production.action_confirm()
        self.assertEqual(plan.state, 'planned')
        self.assertFalse(old_assignment.exists())
        self.assertEqual(len(plan.assignment_ids), 1)
        self.assertIn(plan.assignment_ids.state, ('proposed', 'approved'))

    def test_in_progress_replan_approves_only_new_assignments(self):
        tailoring_worker = self._new_worker(
            'MPS In-progress Tailoring Worker',
            self.tailoring_stage,
        )
        first = self._new_operation(
            'running_first',
            'Running First',
            'priming',
            2.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=10,
        )
        self._new_operation(
            'future_second',
            'Future Second',
            'tailoring',
            2.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=20,
            dependencies=first,
        )
        production = self._new_production(
            sofa_qty=10.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming', 'tailoring'),
        )
        plan = self._plan(production)
        plan.action_approve()
        running_line = plan.line_ids.filtered(
            lambda line: line.operation_id == first
        ).ensure_one()
        running_assignment = running_line.assignment_ids.ensure_one()
        future_line = plan.line_ids.filtered(
            lambda line: line.operation_id.code == 'future_second'
        ).ensure_one()
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-MPS-RUNNING',
        )
        production.priming_order_id = stage_order
        stage_order.worker_ids = [(6, 0, running_assignment.employee_id.ids)]
        stage_order.with_context(
            furniture_mps_stage_start_transition=True,
        ).write({'state': 'in_progress'})
        self.assertEqual(running_line.state, 'approved')
        self.assertEqual(running_assignment.state, 'approved')
        self.assertEqual(future_line.state, 'approved')
        self.assertEqual(plan.state, 'in_progress')
        sofa_line = production.production_line_ids.filtered(
            lambda production_line: production_line.product_id == self.sofa
        ).ensure_one()
        with self.assertRaises(UserError):
            sofa_line.product_qty = 11.0
        remaining_sofa_line = sofa_line._split_for_partial_quantity(5.0)
        self.assertTrue(remaining_sofa_line)
        self.assertAlmostEqual(sum(
            production.with_context(active_test=False).production_line_ids.filtered(
                lambda production_line: production_line.product_id == self.sofa
            ).mapped('product_qty')
        ), 10.0)
        production._consolidate_equivalent_production_lines()

        plan.action_replan()
        proposed = plan.assignment_ids.filtered(
            lambda assignment: assignment.state == 'proposed'
        )
        self.assertTrue(proposed)
        plan.action_approve()

        self.assertEqual(running_assignment.state, 'approved')
        self.assertFalse(plan.assignment_ids.filtered(
            lambda assignment: assignment.state == 'proposed'
        ))
        self.assertEqual(plan.state, 'in_progress')
        self.assertIn(tailoring_worker, plan.assignment_ids.employee_id)

    def test_replan_never_keeps_a_past_anchor(self):
        self._new_operation(
            'future_replan',
            'Future Replan',
            'priming',
            2.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        plan = self._plan()
        plan.planned_start = datetime(2020, 1, 1, 8, 0, 0)
        before_replan = fields.Datetime.to_datetime(fields.Datetime.now())

        plan.action_replan()

        self.assertGreaterEqual(plan.planned_start, before_replan)
        self.assertGreaterEqual(plan.assignment_ids.ensure_one().planned_start, before_replan)

    def test_confirmed_quantity_change_replans_unstarted_work(self):
        self._new_operation(
            'quantity_replan',
            'Quantity Replan',
            'priming',
            5.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production = self._new_production(
            sofa_qty=10.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        plan = self._plan(production)
        line = plan.line_ids.ensure_one()
        self.assertAlmostEqual(line.labor_hours, 50.0)

        production.production_line_ids.filtered(
            lambda production_line: production_line.product_id == self.sofa
        ).product_qty = 5.0

        replacement = plan.line_ids.ensure_one()
        self.assertAlmostEqual(replacement.labor_hours, 25.0)
        self.assertEqual(plan.state, 'planned')

    def test_stage_accepts_only_selected_approved_mps_workers(self):
        self._new_operation(
            'stage_workers',
            'Stage Workers',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        plan = self._plan(production)
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-MPS-TEST',
        )
        with self.assertRaises(UserError):
            stage_order._check_mps_schedule_before_start()

        plan.action_approve()
        scheduled_worker = plan.assignment_ids.employee_id.ensure_one()
        unscheduled_worker = (self.workers - scheduled_worker)[:1]
        stage_order.worker_ids = [(6, 0, unscheduled_worker.ids)]
        with self.assertRaises(UserError):
            stage_order._check_mps_schedule_before_start()
        stage_order.worker_ids = [(6, 0, scheduled_worker.ids)]
        stage_order._check_mps_schedule_before_start()

        self.assertEqual(stage_order.worker_ids, scheduled_worker)

    def test_disabled_mps_does_not_generate_or_block_mrp(self):
        self._new_operation(
            'disabled_mps_gate',
            'Disabled MPS Gate',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production_with_plan = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        plan = self._plan(production_with_plan)
        stage_order = production_with_plan._create_stage_order(
            'furniture.mrp.priming',
            'PRM-MPS-DISABLED',
        )
        with self.assertRaises(UserError):
            stage_order._check_mps_schedule_before_start()

        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_mrp.mps_enabled',
            'False',
        )
        stage_order._check_mps_schedule_before_start()
        stage_order._sync_mps_schedule_state('done')
        self.assertEqual(plan.state, 'planned')
        self.assertEqual(plan.line_ids.mapped('state'), ['proposed'])

        production_without_plan = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        production_without_plan.action_confirm()
        self.assertFalse(production_without_plan.mps_schedule_plan_ids)
        self.assertFalse(production_without_plan._generate_mps_schedule())

    def test_temporary_fixed_stage_hours_bypasses_only_mps_start_gate(self):
        self._new_operation(
            'temporary_stage_test',
            'Temporary Stage Test',
            'priming',
            10.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        plan = self._plan(production)
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-TEMPORARY-TIME',
        )

        with self.assertRaises(UserError):
            stage_order._check_mps_schedule_before_start()

        override_started_at = fields.Datetime.now() - timedelta(minutes=1)
        override_expires_at = fields.Datetime.now() + timedelta(days=7)
        production.sudo().write({
            'temporary_stage_fixed_hours': 2.0,
            'temporary_stage_fixed_hours_from': override_started_at,
            'temporary_stage_fixed_hours_until': override_expires_at,
        })
        self.assertEqual(production._furniture_temporary_stage_hours(), 2.0)
        stage_order._check_mps_schedule_before_start()
        stage_order._sync_mps_schedule_state('in_progress')

        stage_order.write({
            'state': 'in_progress',
            'date_start': fields.Datetime.now(),
        })
        stage_order._furniture_apply_temporary_stage_duration()
        self.assertAlmostEqual(
            (
                fields.Datetime.to_datetime(stage_order.date_planned_finish)
                - fields.Datetime.to_datetime(stage_order.date_start)
            ).total_seconds() / 3600.0,
            2.0,
        )
        self.assertAlmostEqual(stage_order.expected_labor_hours, 2.0)
        self.assertTrue(
            production._furniture_stage_started_during_temporary_override(
                stage_order,
            )
        )

        after_expiration = override_expires_at + timedelta(seconds=1)
        with patch.object(
            fields.Datetime,
            'now',
            return_value=after_expiration,
        ):
            self.assertFalse(production._furniture_temporary_stage_hours())
            with self.assertRaises(UserError):
                stage_order._check_mps_schedule_before_start()
            stage_order._sync_mps_schedule_state('done')

        self.assertEqual(plan.state, 'planned')
        self.assertEqual(plan.line_ids.mapped('state'), ['proposed'])

    def test_seed_big_moon_is_canonical_idempotent_and_rejects_ambiguity(self):
        seed_model = self.test_env['furniture.product.model'].create({
            'name': 'MPS Seed Model',
        })

        def make_product(name, source=False):
            product = self.test_env['product.product'].create({
                'name': name,
                'type': 'consu',
                'is_storable': True,
                'furniture_dimension_source_product_id': source.id if source else False,
            })
            self.test_env['mrp.bom'].create({
                'product_tmpl_id': product.product_tmpl_id.id,
                'product_qty': 1.0,
                'type': 'normal',
                'company_id': self.company.id,
                'furniture_product_id': product.id,
                'furniture_model_id': seed_model.id,
            })
            return product

        canonical_sofa = self.test_env['product.product'].create({
            'name': 'MPS Canonical Large Sofa',
            'type': 'consu',
            'is_storable': True,
        })
        dimension_sofa = make_product('كنبة كبيرة', source=canonical_sofa)
        chaise = make_product('شازلونج')
        chair = make_product('فوتيه')
        operation_model = self.test_env['furniture.mrp.mps.operation']

        profile = operation_model._seed_big_moon_profile(seed_model).ensure_one()

        self.assertEqual(
            set(profile.product_ids.ids),
            {canonical_sofa.id, chaise.id, chair.id},
        )
        self.assertNotIn(dimension_sofa, profile.product_ids)
        seeded = profile.operation_ids.filtered(
            lambda operation: operation.active and operation.is_seeded_big_moon
        )
        self.assertEqual(len(seeded), 34)
        expected_single_codes = {
            'priming_pair',
            'priming_chair',
            'paint_cutting_chair',
            'base_prep_pair',
            'base_install_pair',
            'cut_pair',
            'cut_chair',
            'sewing_pair',
            'sewing_chair',
            'finishing_pair',
            'finishing_chair',
            'upholstery_chair',
            'tying_chair',
        }
        expected_stage_pool_codes = {
            'chassis_pair',
            'chassis_chair',
            'sides_pair',
            'white_finish_pair',
            'white_finish_chair',
            'back_assembly_pair',
            'back_assembly_chair',
            'sanding_pair',
            'sanding_drilling_chair',
            'paint_prep_pair',
            'paint_prep_chair',
            'back_prep_chair',
            'trimming_pair',
            'takawe_pair',
            'takawe_chair',
            'upholstery_pair',
            'tying_pair',
            'packaging_pair',
            'packaging_chair',
        }
        single_operations = seeded.filtered(
            lambda operation: operation.distribution_mode == 'single'
        )
        stage_pool_operations = seeded.filtered(
            lambda operation: operation.distribution_mode == 'stage_pool_equal'
        )
        self.assertEqual(len(single_operations), 15)
        self.assertEqual(
            set(single_operations.mapped('code')),
            expected_single_codes,
        )
        self.assertEqual(len(stage_pool_operations), 19)
        self.assertEqual(
            set(stage_pool_operations.mapped('code')),
            expected_stage_pool_codes,
        )

        priming_operations = seeded.filtered(
            lambda operation: operation.code in {
                'priming_pair', 'priming_chair',
            }
        )
        self.assertEqual(len(priming_operations), 2)
        self.assertEqual(
            set(priming_operations.mapped('different_worker_key')),
            {'big_moon_priming_workers'},
        )
        self.assertFalse(priming_operations.filtered('same_worker_key'))

        same_worker_contract = {
            'paint_cutting_chair': 'big_moon_base_and_chair_paint',
            'base_prep_pair': 'big_moon_base_and_chair_paint',
            'base_install_pair': 'big_moon_base_and_chair_paint',
            'sewing_pair': 'big_moon_sewing',
            'sewing_chair': 'big_moon_sewing',
            'upholstery_chair': 'big_moon_chair_upholstery',
            'tying_chair': 'big_moon_chair_upholstery',
        }
        for code, worker_key in same_worker_contract.items():
            operation = seeded.filtered(
                lambda candidate, code=code: candidate.code == code
            ).ensure_one()
            self.assertEqual(operation.distribution_mode, 'single')
            self.assertEqual(operation.same_worker_key, worker_key)
            self.assertFalse(operation.different_worker_key)

        affinity_operations = seeded.filtered(
            lambda operation: (
                operation.code in same_worker_contract
                or operation.code in {'priming_pair', 'priming_chair'}
            )
        )
        self.assertFalse((seeded - affinity_operations).filtered(
            lambda operation: (
                operation.same_worker_key
                or operation.different_worker_key
            )
        ))
        self.assertFalse(seeded.filtered(lambda operation: (
            operation.same_worker_key and operation.different_worker_key
        )))
        original_ids = set(seeded.ids)
        operation_model._validate_big_moon_seed_contract(profile)

        upholstery_pair = seeded.filtered(
            lambda operation: operation.code == 'upholstery_pair'
        ).ensure_one()
        tying_pair = seeded.filtered(
            lambda operation: operation.code == 'tying_pair'
        ).ensure_one()
        cut_pair_lycra = seeded.filtered(
            lambda operation: (
                operation.code == 'cut_pair'
                and operation.fabric_type_id.name == 'ليكرا'
            )
        ).ensure_one()
        finishing_pair = seeded.filtered(
            lambda operation: operation.code == 'finishing_pair'
        ).ensure_one()
        base_prep_pair = seeded.filtered(
            lambda operation: operation.code == 'base_prep_pair'
        ).ensure_one()
        base_install_pair = seeded.filtered(
            lambda operation: operation.code == 'base_install_pair'
        ).ensure_one()
        sanding_pair = seeded.filtered(
            lambda operation: operation.code == 'sanding_pair'
        ).ensure_one()
        paint_prep_pair = seeded.filtered(
            lambda operation: operation.code == 'paint_prep_pair'
        ).ensure_one()
        back_assembly_pair = seeded.filtered(
            lambda operation: operation.code == 'back_assembly_pair'
        ).ensure_one()
        back_assembly_chair = seeded.filtered(
            lambda operation: operation.code == 'back_assembly_chair'
        ).ensure_one()
        chassis_pair = seeded.filtered(
            lambda operation: operation.code == 'chassis_pair'
        ).ensure_one()
        trimming_pair = seeded.filtered(
            lambda operation: operation.code == 'trimming_pair'
        ).ensure_one()
        packaging_pair = seeded.filtered(
            lambda operation: operation.code == 'packaging_pair'
        ).ensure_one()
        takawe_pair = seeded.filtered(
            lambda operation: operation.code == 'takawe_pair'
        ).ensure_one()
        self.assertLess(cut_pair_lycra.sequence, upholstery_pair.sequence)
        self.assertFalse(cut_pair_lycra.dependency_ids)
        self.assertFalse(sanding_pair.dependency_ids)
        self.assertEqual(paint_prep_pair.dependency_ids, sanding_pair)
        self.assertEqual(back_assembly_pair.stage, 'painting')
        self.assertEqual(back_assembly_chair.stage, 'painting')
        self.assertFalse(back_assembly_pair.dependency_ids)
        self.assertFalse(back_assembly_chair.dependency_ids)
        self.assertAlmostEqual(back_assembly_pair.duration_hours, 15.0)
        self.assertAlmostEqual(back_assembly_chair.duration_hours, 12.0)
        self.assertEqual(
            {
                output.product_id.id: output.quantity
                for output in back_assembly_pair.output_line_ids
            },
            {canonical_sofa.id: 9.0, chaise.id: 9.0},
        )
        self.assertEqual(
            {
                output.product_id.id: output.quantity
                for output in back_assembly_chair.output_line_ids
            },
            {chair.id: 40.0},
        )
        self.assertFalse(seeded.filtered(
            lambda operation: (
                operation.stage == 'painting'
                and operation.dependency_ids.filtered(
                    lambda dependency: dependency.stage != 'painting'
                )
            )
        ))
        self.assertFalse(base_prep_pair.dependency_ids)
        self.assertEqual(base_install_pair.dependency_ids, base_prep_pair)
        self.assertEqual(finishing_pair.dependency_ids, base_install_pair)
        self.assertEqual(
            set(upholstery_pair.dependency_ids.ids),
            {finishing_pair.id, takawe_pair.id},
        )
        self.assertEqual(tying_pair.dependency_ids, upholstery_pair)
        self.assertIn(
            'sewing_pair',
            takawe_pair.dependency_ids.mapped('code'),
        )
        self.assertEqual(
            set(takawe_pair.output_line_ids.mapped('piece_duration_hours')),
            {0.25},
        )
        self.assertEqual(
            set(packaging_pair.dependency_ids.ids),
            {tying_pair.id, trimming_pair.id, back_assembly_pair.id},
        )

        obsolete = operation_model.create({
            'name': 'Obsolete Seeded Operation',
            'code': 'obsolete_seeded_operation',
            'profile_id': profile.id,
            'stage': 'priming',
            'duration_hours': 1.0,
            'distribution_mode': 'single',
            'is_seeded_big_moon': True,
            'output_line_ids': [(0, 0, {
                'product_id': canonical_sofa.id,
                'quantity': 1.0,
            })],
        })
        mutated_priming = seeded.filtered(
            lambda operation: operation.code == 'priming_pair'
        ).ensure_one()
        mutated_priming.write({
            'distribution_mode': 'stage_pool_equal',
            'same_worker_key': False,
            'different_worker_key': False,
        })
        mutated_sewing = seeded.filtered(
            lambda operation: operation.code == 'sewing_pair'
        ).ensure_one()
        mutated_sewing.write({
            'distribution_mode': 'single',
            'same_worker_key': False,
            'different_worker_key': 'legacy_wrong_sewing_worker',
        })
        mutated_stage_pool = seeded.filtered(
            lambda operation: operation.code == 'chassis_pair'
        ).ensure_one()
        mutated_stage_pool.write({
            'distribution_mode': 'single',
            'same_worker_key': 'legacy_wrong_stage_pool_worker',
            'different_worker_key': False,
        })
        back_assembly_pair.write({
            'stage': 'carpentry',
            'dependency_ids': [(6, 0, chassis_pair.ids)],
        })
        rerun = operation_model._seed_big_moon_profile(seed_model).ensure_one()
        self.assertEqual(
            mutated_priming.distribution_mode,
            'single',
        )
        self.assertFalse(mutated_priming.same_worker_key)
        self.assertEqual(
            mutated_priming.different_worker_key,
            'big_moon_priming_workers',
        )
        self.assertEqual(mutated_sewing.distribution_mode, 'single')
        self.assertEqual(
            mutated_sewing.same_worker_key,
            'big_moon_sewing',
        )
        self.assertFalse(mutated_sewing.different_worker_key)
        self.assertEqual(
            mutated_stage_pool.distribution_mode,
            'stage_pool_equal',
        )
        self.assertFalse(mutated_stage_pool.same_worker_key)
        self.assertFalse(mutated_stage_pool.different_worker_key)
        self.assertEqual(back_assembly_pair.stage, 'painting')
        self.assertFalse(back_assembly_pair.dependency_ids)
        self.assertEqual(
            set(rerun.operation_ids.filtered(
                lambda operation: operation.active and operation.is_seeded_big_moon
            ).ids),
            original_ids,
        )
        self.assertFalse(obsolete.active)

        make_product('كنبة كبيرة')
        self.assertFalse(operation_model._seed_big_moon_profile(seed_model))

    def test_calendar_skips_friday_for_long_single_worker_job(self):
        self._new_operation(
            'calendar_priming',
            'Calendar Priming',
            'priming',
            11.0,
            ((self.sofa, 1.0),),
            mode='single',
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        today = fields.Date.context_today(self.env.user)
        days_until_thursday = (3 - today.weekday()) % 7
        if days_until_thursday == 0:
            days_until_thursday = 7
        thursday = today + timedelta(days=days_until_thursday)
        start = datetime.combine(thursday, datetime.min.time()).replace(hour=8)
        expected_end = start + timedelta(days=2, hours=1)
        production.date_planned_start = start
        assignment = self._plan(production).assignment_ids.ensure_one()
        self.assertEqual(assignment.planned_start, start)
        self.assertEqual(assignment.planned_end, expected_end)

    def test_same_worker_dependency_is_sequential(self):
        first = self._new_operation(
            'same_worker_first',
            'Same Worker First',
            'priming',
            3.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=10,
            same_worker_key='same_worker_test',
        )
        self._new_operation(
            'same_worker_second',
            'Same Worker Second',
            'priming',
            2.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=20,
            dependencies=first,
            same_worker_key='same_worker_test',
        )
        plan = self._plan()
        assignments = plan.assignment_ids.sorted('planned_start')
        self.assertEqual(len(assignments), 2)
        self.assertEqual(assignments[0].employee_id, assignments[1].employee_id)
        self.assertGreaterEqual(
            assignments[1].planned_start,
            assignments[0].planned_end,
        )

    def test_joint_standard_with_missing_pair_product_is_blocked(self):
        self._new_operation(
            'joint_pair',
            'Joint Pair',
            'priming',
            5.0,
            ((self.sofa, 1.0), (self.chaise, 1.0)),
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        line = self._plan(production).line_ids.ensure_one()
        self.assertEqual(line.state, 'blocked')
        self.assertIn(self.chaise.display_name, line.warning_message)
        self.assertIn(self.sofa.display_name, line.output_summary)
        self.assertNotIn(self.chaise.display_name, line.output_summary)
        self.assertFalse(line.assignment_ids)

    def test_dependency_without_stage_workers_is_explicitly_blocked(self):
        priming = self._new_operation(
            'dependency_priming',
            'Dependency Priming',
            'priming',
            2.0,
            ((self.sofa, 1.0),),
        )
        painting = self._new_operation(
            'dependency_painting',
            'Dependency Painting',
            'painting',
            3.0,
            ((self.sofa, 1.0),),
            sequence=20,
            dependencies=priming,
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming', 'painting'),
        )
        plan = self._plan(production)
        painting_line = plan.line_ids.filtered(
            lambda line: line.operation_id == painting
        ).ensure_one()
        self.assertEqual(painting_line.state, 'blocked')
        self.assertIn('لا يوجد عامل', painting_line.warning_message)
        self.assertTrue(plan.has_warnings)
        self.assertEqual(plan.blocked_line_count, 1)

    def test_fabric_type_selects_matching_standard_and_is_snapshotted(self):
        lycra = self.test_env.ref('furniture_mrp.fabric_work_type_lycra')
        linen = self.test_env.ref('furniture_mrp.fabric_work_type_linen')
        self._new_worker('MPS Tailoring Worker', self.tailoring_stage)
        self._new_operation(
            'cut_pair',
            'Cut Pair Lycra',
            'tailoring',
            6.0,
            ((self.sofa, 4.0), (self.chaise, 4.0)),
            mode='single',
            fabric_type=lycra,
        )
        self._new_operation(
            'cut_pair',
            'Cut Pair Linen',
            'tailoring',
            8.0,
            ((self.sofa, 4.0), (self.chaise, 4.0)),
            mode='single',
            fabric_type=linen,
        )
        production = self._new_production(
            sofa_qty=4.0,
            chaise_qty=4.0,
            chair_qty=0.0,
            stages=('tailoring',),
        )
        fabric = self.test_env['product.product'].create({
            'name': 'MPS Lycra Fabric',
            'type': 'consu',
            'is_storable': True,
        })
        fabric.product_tmpl_id.write({
            'furniture_tailoring_material_kind': 'fabric',
            'furniture_fabric_type_id': lycra.id,
        })
        for production_line in production.production_line_ids.filtered(
            lambda line: line.product_id in (self.sofa | self.chaise)
        ):
            self.test_env['furniture.mrp.tailoring.material.allocation'].sudo().with_context(
                furniture_tailoring_setup_internal_write=True,
            ).create({
                'production_id': production.id,
                'production_line_id': production_line.id,
                'material_kind': 'fabric',
                'product_id': fabric.id,
                'qty': 1.0,
                'product_uom_id': fabric.uom_id.id,
            })
        plan = self._plan(production)
        line = plan.line_ids.ensure_one()
        self.assertEqual(line.operation_id.name, 'Cut Pair Lycra')
        self.assertEqual(line.fabric_type_id, lycra)
        self.assertAlmostEqual(line.labor_hours, 6.0)

        plan.action_approve()
        fabric.product_tmpl_id.furniture_fabric_type_id = linen
        replacement = plan.line_ids.ensure_one()
        self.assertEqual(plan.state, 'planned')
        self.assertEqual(replacement.operation_id.name, 'Cut Pair Linen')
        self.assertEqual(replacement.fabric_type_id, linen)
        self.assertAlmostEqual(replacement.labor_hours, 8.0)

    def test_missing_fabric_type_blocks_only_typed_work(self):
        lycra = self.test_env.ref('furniture_mrp.fabric_work_type_lycra')
        self._new_worker('MPS Missing Fabric Tailor', self.tailoring_stage)
        self._new_operation(
            'missing_cut_pair',
            'Missing Fabric Cut Pair',
            'tailoring',
            6.0,
            ((self.sofa, 1.0), (self.chaise, 1.0)),
            mode='single',
            fabric_type=lycra,
        )
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=1.0,
            chair_qty=0.0,
            stages=('tailoring',),
        )
        plan = self._plan(production)
        line = plan.line_ids.ensure_one()
        self.assertEqual(line.state, 'blocked')
        self.assertIn('قماش', line.warning_message)
        self.assertFalse(line.assignment_ids)

    def test_fabric_work_type_is_only_valid_on_fabric_products(self):
        lycra = self.test_env.ref('furniture_mrp.fabric_work_type_lycra')
        raw = self.test_env['product.product'].create({
            'name': 'MPS Raw Material Classification',
            'type': 'consu',
            'is_storable': True,
        })
        with self.assertRaises(ValidationError):
            raw.product_tmpl_id.write({
                'furniture_fabric_type_id': lycra.id,
            })
        raw.product_tmpl_id.write({
            'furniture_tailoring_material_kind': 'fabric',
            'furniture_fabric_type_id': lycra.id,
        })
        self.assertEqual(raw.furniture_fabric_type_id, lycra)
        raw.product_tmpl_id.furniture_tailoring_material_kind = 'takawe'
        self.assertFalse(raw.furniture_fabric_type_id)

    def test_generic_missing_time_blocks_then_schedules_whole_pieces(self):
        self.profile.active = False
        production = self._new_production(
            sofa_qty=3.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )

        plan = self._plan(production)
        line = plan.line_ids.ensure_one()
        timing = self.test_env['furniture.mrp.mps.product.time'].search([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', self.model.id),
            ('product_id', '=', self.sofa.id),
            ('stage', '=', 'priming'),
        ]).ensure_one()

        self.assertTrue(plan.profile_id.is_auto_product_profile)
        self.assertTrue(timing.uses_detailed_standard)
        self.assertEqual(line.state, 'blocked')
        self.assertEqual(line.piece_count, 3)
        self.assertEqual(line.atom_count, 3)
        self.assertAlmostEqual(line.standard_duration_hours, 0.0)
        self.assertAlmostEqual(line.labor_hours, 0.0)
        self.assertAlmostEqual(line.operation_id.duration_hours, 0.0)
        self.assertFalse(line.operation_id.time_is_configured)
        self.assertFalse(line.assignment_ids)
        self.assertFalse(line.assignment_ids.output_line_ids)
        self.assertIn('الزمن غير محدد', line.output_summary)
        self.assertIn(self.sofa.display_name, line.warning_message)
        self.assertIn('التقديم', line.warning_message)
        self.assertTrue(plan.has_warnings)
        self.assertEqual(plan.blocked_line_count, 1)
        with self.assertRaises(UserError):
            plan.action_approve()
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-MPS-MISSING-TIME',
        )
        with self.assertRaises(UserError):
            stage_order._check_mps_schedule_before_start()
        original_plan = plan
        original_profile = plan.profile_id
        original_operation = line.operation_id
        original_operation_count = len(original_profile.operation_ids)
        original_timing_count = self.test_env[
            'furniture.mrp.mps.product.time'
        ].search_count([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', self.model.id),
            ('product_id', '=', self.sofa.id),
            ('stage', '=', 'priming'),
        ])

        line.operation_id.output_line_ids.ensure_one().piece_duration_hours = 2.0

        replacement = plan.line_ids.ensure_one()
        self.assertEqual(plan.profile_id, original_profile)
        self.assertEqual(len(original_profile.operation_ids), original_operation_count)
        self.assertEqual(replacement.state, 'proposed')
        self.assertEqual(replacement.piece_count, 3)
        self.assertEqual(len(replacement.assignment_ids), 3)
        self.assertEqual(
            set(replacement.assignment_ids.mapped('planned_atom_count')),
            {1},
        )
        self.assertEqual(
            set(replacement.assignment_ids.output_line_ids.mapped('quantity')),
            {1},
        )
        self.assertAlmostEqual(
            sum(replacement.assignment_ids.mapped('planned_hours')),
            6.0,
        )
        self.assertAlmostEqual(timing.detailed_piece_hours, 2.0)
        self.assertAlmostEqual(timing.piece_hours, 0.0)
        self.assertFalse(plan.has_warnings)

        regenerated = production._generate_mps_schedule().ensure_one()
        self.assertEqual(regenerated, original_plan)
        self.assertEqual(regenerated.profile_id, original_profile)
        self.assertEqual(regenerated.line_ids.operation_id, original_operation)
        self.assertEqual(len(regenerated.line_ids), 1)
        self.assertEqual(len(regenerated.assignment_ids), 3)
        self.assertEqual(len(regenerated.assignment_ids.output_line_ids), 3)
        self.assertEqual(len(original_profile.operation_ids), original_operation_count)
        self.assertEqual(
            self.test_env['furniture.mrp.mps.product.time'].search_count([
                ('company_id', '=', self.company.id),
                ('furniture_model_id', '=', self.model.id),
                ('product_id', '=', self.sofa.id),
                ('stage', '=', 'priming'),
            ]),
            original_timing_count,
        )

    def test_new_recipe_is_visible_in_timing_matrix_before_confirmation(self):
        model = self.test_env['furniture.product.model'].create({
            'name': 'MPS Non Big Moon Lifecycle Model',
        })
        product = self.test_env['product.product'].create({
            'name': 'MPS Lifecycle Whole Chair',
            'type': 'consu',
            'is_storable': True,
        })

        self.test_env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_qty': 1.0,
            'type': 'normal',
            'company_id': self.company.id,
            'furniture_product_id': product.id,
            'furniture_model_id': model.id,
        })

        timings = self.test_env['furniture.mrp.mps.product.time'].search([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', model.id),
            ('product_id', '=', product.id),
            ('active', '=', True),
        ])
        self.assertEqual(len(timings), 8)
        self.assertEqual(set(timings.mapped('stage')), {
            'priming',
            'painting',
            'carpentry',
            'bases',
            'finishing',
            'upholstery',
            'tailoring',
            'packaging',
        })
        profile = self.test_env['furniture.mrp.mps.profile'].search([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', model.id),
            ('is_auto_product_profile', '=', True),
            ('active', '=', True),
        ]).ensure_one()
        self.assertEqual(profile.product_ids, product)
        active_operations = profile.operation_ids.filtered('active')
        self.assertEqual(len(active_operations), 20)
        self.assertEqual(
            set(active_operations.mapped('distribution_mode')),
            {'stage_pool_equal'},
        )
        self.assertFalse(active_operations.filtered('same_worker_key'))
        self.assertFalse(active_operations.filtered('different_worker_key'))
        self.assertEqual(
            set(active_operations.output_line_ids.product_id.ids),
            {product.id},
        )
        self.assertTrue(active_operations.filtered(
            lambda operation: operation.name.startswith('التقديم —')
        ))
        self.assertTrue(active_operations.filtered(
            lambda operation: 'الخياطة' in operation.name
        ))
        self.assertTrue(active_operations.filtered(
            lambda operation: 'التكاوي' in operation.name
        ))

    def test_running_stage_rejects_timing_edit_without_losing_work(self):
        self.profile.active = False
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        production.state = 'confirmed'
        production._furniture_mps_profile()
        timing = self.test_env['furniture.mrp.mps.product.time'].search([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', self.model.id),
            ('product_id', '=', self.sofa.id),
            ('stage', '=', 'priming'),
        ]).ensure_one()
        priming_output = timing.operation_ids.filtered(
            lambda operation: operation.active
        ).output_line_ids.ensure_one()
        priming_output.piece_duration_hours = 2.0
        plan = production._generate_mps_schedule().ensure_one()
        plan.action_approve()
        line = plan.line_ids.ensure_one()
        assignment = line.assignment_ids.ensure_one()
        stage_order = production._create_stage_order(
            'furniture.mrp.priming',
            'PRM-MPS-TIMING-LOCK',
        )
        production.priming_order_id = stage_order
        stage_order.worker_ids = [(6, 0, assignment.employee_id.ids)]
        stage_order.with_context(
            furniture_mps_stage_start_transition=True,
        ).write({'state': 'in_progress'})

        with self.assertRaises(UserError):
            priming_output.piece_duration_hours = 3.0

        self.assertAlmostEqual(priming_output.piece_duration_hours, 2.0)
        self.assertTrue(line.exists())
        self.assertTrue(assignment.exists())
        self.assertEqual(line.assignment_ids, assignment)

    def test_generic_runtime_dependencies_use_actual_stage_subset(self):
        self.profile.active = False
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=(
                'priming',
                'carpentry',
                'painting',
                'bases',
                'finishing',
                'tailoring',
                'upholstery',
                'packaging',
            ),
        )
        production.state = 'confirmed'
        profile = production._furniture_mps_profile()
        profile.operation_ids.filtered(lambda operation: (
            operation.active
            and operation.is_auto_product_standard
            and operation.stage in {
                'priming',
                'carpentry',
                'painting',
                'bases',
                'finishing',
                'tailoring',
                'upholstery',
                'packaging',
            }
        )).output_line_ids.write({'piece_duration_hours': 1.0})

        plan = production._generate_mps_schedule().ensure_one()
        def step_line(step_code):
            return plan.line_ids.filtered(
                lambda line: line.operation_id.code.endswith('_%s' % step_code)
            ).ensure_one()

        priming = step_line('priming')
        chassis = step_line('chassis')
        sides = step_line('sides')
        sanding = step_line('sanding')
        paint_prep = step_line('paint_prep')
        back_assembly = step_line('back_assembly')
        back_prep = step_line('back_prep')
        trimming = step_line('trimming')
        paint_cutting = step_line('paint_cutting')
        base_prep = step_line('base_prep')
        base_install = step_line('base_install')
        finishing = step_line('finishing')
        cut = step_line('cut')
        sewing = step_line('sewing')
        takawe = step_line('takawe')
        upholstery = step_line('upholstery')
        tying = step_line('tying')
        packaging = step_line('packaging')

        self.assertFalse(priming.dependency_line_ids)
        self.assertEqual(chassis.dependency_line_ids, priming)
        self.assertEqual(sides.dependency_line_ids, chassis)
        self.assertFalse(sanding.dependency_line_ids)
        self.assertEqual(paint_prep.dependency_line_ids, sanding)
        self.assertEqual(back_assembly.stage, 'painting')
        self.assertFalse(back_assembly.dependency_line_ids)
        self.assertFalse(back_prep.dependency_line_ids)
        self.assertEqual(trimming.dependency_line_ids, paint_prep)
        self.assertEqual(
            set(paint_cutting.dependency_line_ids.ids),
            {paint_prep.id, back_prep.id},
        )
        self.assertFalse(base_prep.dependency_line_ids)
        self.assertEqual(base_install.dependency_line_ids, base_prep)
        self.assertEqual(finishing.dependency_line_ids, base_install)
        self.assertFalse(cut.dependency_line_ids)
        self.assertEqual(sewing.dependency_line_ids, cut)
        self.assertEqual(takawe.dependency_line_ids, sewing)
        self.assertEqual(
            set(upholstery.dependency_line_ids.ids),
            {finishing.id, takawe.id},
        )
        self.assertEqual(tying.dependency_line_ids, upholstery)
        self.assertEqual(
            set(packaging.dependency_line_ids.ids),
            {tying.id, trimming.id, paint_cutting.id, back_assembly.id},
        )

    def test_fifo_lane_plans_keep_internal_dependencies_and_use_handoffs_for_external_ones(self):
        stage_by_code = {
            code: self.env.ref('furniture_mrp.employee_stage_%s' % code)
            for code in (
                'carpentry', 'bases', 'finishing', 'tailoring',
                'painting', 'upholstery', 'packaging',
            )
        }
        self._new_worker(
            'MPS FIFO Lane Worker',
            *stage_by_code.values(),
        )
        priming = self._new_operation(
            'fifo_priming', 'FIFO Priming', 'priming', 1.0,
            ((self.sofa, 1.0),), sequence=10,
        )
        carpentry = self._new_operation(
            'fifo_carpentry', 'FIFO Carpentry', 'carpentry', 1.0,
            ((self.sofa, 1.0),), sequence=20, dependencies=priming,
        )
        bases = self._new_operation(
            'fifo_bases', 'FIFO Bases', 'bases', 1.0,
            ((self.sofa, 1.0),), sequence=30,
        )
        finishing = self._new_operation(
            'fifo_finishing', 'FIFO Finishing', 'finishing', 1.0,
            ((self.sofa, 1.0),), sequence=40, dependencies=bases,
        )
        tailoring = self._new_operation(
            'fifo_tailoring', 'FIFO Tailoring', 'tailoring', 1.0,
            ((self.sofa, 1.0),), sequence=50,
        )
        painting = self._new_operation(
            'fifo_back_assembly', 'FIFO Back Assembly', 'painting', 1.0,
            ((self.sofa, 1.0),), sequence=60,
        )
        upholstery = self._new_operation(
            'fifo_upholstery', 'FIFO Upholstery', 'upholstery', 1.0,
            ((self.sofa, 1.0),), sequence=70,
            dependencies=finishing | tailoring,
        )
        packaging = self._new_operation(
            'fifo_packaging', 'FIFO Packaging', 'packaging', 1.0,
            ((self.sofa, 1.0),), sequence=80,
            dependencies=upholstery | painting,
        )

        plans = {}
        for lane in (
            'frame', 'finish', 'tailoring', 'painting',
            'upholstery', 'packaging',
        ):
            production = self._new_production(
                sofa_qty=1.0,
                chaise_qty=0.0,
                chair_qty=0.0,
                stages=(),
                lane=lane,
            )
            plans[lane] = self._plan(production)

        def work(plan, operation):
            return plan.line_ids.filtered(
                lambda line: line.operation_id == operation
            ).ensure_one()

        frame_priming = work(plans['frame'], priming)
        frame_carpentry = work(plans['frame'], carpentry)
        self.assertEqual(frame_carpentry.dependency_line_ids, frame_priming)
        self.assertNotIn(painting, plans['frame'].line_ids.operation_id)

        finish_bases = work(plans['finish'], bases)
        finish_finishing = work(plans['finish'], finishing)
        self.assertEqual(finish_finishing.dependency_line_ids, finish_bases)
        self.assertEqual(work(plans['tailoring'], tailoring).state, 'proposed')
        self.assertEqual(work(plans['painting'], painting).state, 'proposed')

        upholstery_line = work(plans['upholstery'], upholstery)
        packaging_line = work(plans['packaging'], packaging)
        self.assertEqual(upholstery_line.state, 'proposed')
        self.assertFalse(upholstery_line.dependency_line_ids)
        self.assertTrue(upholstery_line.assignment_ids)
        self.assertEqual(packaging_line.state, 'proposed')
        self.assertFalse(packaging_line.dependency_line_ids)
        self.assertTrue(packaging_line.assignment_ids)

        # A historical full-route order still carries its real cross-stage MPS
        # graph because every predecessor is represented in the same plan.
        full = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=(
                'priming', 'carpentry', 'bases', 'finishing',
                'tailoring', 'painting', 'upholstery', 'packaging',
            ),
        )
        full_plan = self._plan(full)
        self.assertEqual(
            set(work(full_plan, upholstery).dependency_line_ids.operation_id.ids),
            {finishing.id, tailoring.id},
        )
        self.assertEqual(
            set(work(full_plan, packaging).dependency_line_ids.operation_id.ids),
            {upholstery.id, painting.id},
        )

    def test_same_worker_affinity_is_shared_across_lane_plans_and_conflicts_block(self):
        painting_stage = self.env.ref('furniture_mrp.employee_stage_painting')
        bases_stage = self.env.ref('furniture_mrp.employee_stage_bases')
        finishing_stage = self.env.ref('furniture_mrp.employee_stage_finishing')
        self._new_worker('MPS Painting-only Worker', painting_stage)
        shared_first = self._new_worker(
            'MPS Shared Painting Bases Worker 1',
            painting_stage,
            bases_stage,
            finishing_stage,
        )
        shared_second = self._new_worker(
            'MPS Shared Painting Bases Worker 2',
            painting_stage,
            bases_stage,
            finishing_stage,
        )
        worker_key = 'test_cross_lane_paint_bases_worker'
        painting = self._new_operation(
            'cross_lane_paint', 'Cross-lane Paint', 'painting', 2.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=10,
            same_worker_key=worker_key,
        )
        bases = self._new_operation(
            'cross_lane_bases', 'Cross-lane Bases', 'bases', 2.0,
            ((self.sofa, 1.0),),
            mode='single',
            sequence=20,
            same_worker_key=worker_key,
        )
        self._new_operation(
            'cross_lane_finishing', 'Cross-lane Finishing', 'finishing', 1.0,
            ((self.sofa, 1.0),),
            sequence=30,
            dependencies=bases,
        )

        def lane_plan(lane):
            return self._plan(self._new_production(
                sofa_qty=1.0,
                chaise_qty=0.0,
                chair_qty=0.0,
                stages=(),
                lane=lane,
            ))

        painting_plan = lane_plan('painting')
        painting_assignment = painting_plan.line_ids.filtered(
            lambda line: line.operation_id == painting
        ).assignment_ids.ensure_one()
        self.assertEqual(painting_assignment.employee_id, shared_first)

        finish_plan = lane_plan('finish')
        bases_assignment = finish_plan.line_ids.filtered(
            lambda line: line.operation_id == bases
        ).assignment_ids.ensure_one()
        self.assertEqual(bases_assignment.employee_id, shared_first)
        self.assertGreaterEqual(
            bases_assignment.planned_start,
            painting_assignment.planned_end,
        )

        conflicting_plan = lane_plan('painting')
        # Free the unrelated finishing reservation held by ``shared_second``.
        # The conflict exercised below is an affinity conflict between two
        # valid, non-overlapping assignments; it must not bypass the separate
        # worker-overlap invariant.
        (finish_plan.assignment_ids - bases_assignment).write({
            'state': 'cancelled',
        })
        conflicting_plan.assignment_ids.ensure_one().employee_id = shared_second
        blocked_plan = lane_plan('finish')
        blocked_line = blocked_plan.line_ids.filtered(
            lambda line: line.operation_id == bases
        ).ensure_one()
        self.assertEqual(blocked_line.state, 'blocked')
        self.assertFalse(blocked_line.assignment_ids)
        self.assertIn('أكثر من عامل', blocked_line.warning_message)

    def test_custom_stage_exists_even_when_recipe_default_is_disabled(self):
        self.profile.active = False
        hidden_sofa_bom = self.test_env[
            'furniture.mrp.mps.product.time'
        ]._eligible_boms(self.model, self.company).filtered(
            lambda bom: bom.furniture_product_id == self.sofa
        ).ensure_one()
        hidden_sofa_bom.with_context(
            furniture_skip_model_recipe_sync=True,
        ).write({'use_painting': False})
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('painting',),
        )

        lines = self._plan(production).line_ids

        self.assertEqual(len(lines), 6)
        self.assertEqual(len(lines.filtered(
            lambda line: line.operation_id.code.endswith('_back_assembly')
        )), 1)
        self.assertEqual(set(lines.mapped('stage')), {'painting'})
        self.assertEqual(set(lines.mapped('state')), {'blocked'})
        self.assertEqual(set(lines.mapped('piece_count')), {1})
        self.assertTrue(any(
            'تصنيع دهانات' in (line.warning_message or '')
            for line in lines
        ))
        self.assertFalse(lines.assignment_ids)

    def test_detailed_pairs_win_and_only_uncovered_pair_is_generic(self):
        detailed = self._new_operation(
            'detailed_sofa_priming',
            'Detailed Sofa Priming',
            'priming',
            2.0,
            ((self.sofa, 1.0),),
        )
        sofa_production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )

        sofa_line = self._plan(sofa_production).line_ids.ensure_one()
        sofa_timing = self.test_env['furniture.mrp.mps.product.time'].search([
            ('company_id', '=', self.company.id),
            ('furniture_model_id', '=', self.model.id),
            ('product_id', '=', self.sofa.id),
            ('stage', '=', 'priming'),
        ]).ensure_one()

        self.assertEqual(sofa_line.operation_id, detailed)
        self.assertTrue(sofa_timing.uses_detailed_standard)
        self.assertAlmostEqual(sofa_timing.detailed_piece_hours, 2.0)
        self.assertAlmostEqual(sofa_timing.piece_hours, 0.0)
        self.assertFalse(self.profile.operation_ids.filtered(lambda operation: (
            operation.is_auto_product_standard
            and operation.product_time_id == sofa_timing
            and operation.active
        )))

        chair_production = self._new_production(
            sofa_qty=0.0,
            chaise_qty=0.0,
            chair_qty=2.0,
            stages=('bases',),
        )
        chair_line = self._plan(chair_production).line_ids.ensure_one()
        self.assertTrue(chair_line.operation_id.is_auto_product_standard)
        self.assertEqual(chair_line.operation_id.product_time_id.product_id, self.chair)
        self.assertEqual(chair_line.state, 'blocked')
        self.assertEqual(chair_line.piece_count, 2)

    def test_auto_source_fields_cannot_be_spoofed_by_manager(self):
        self.profile.active = False
        production = self._new_production(
            sofa_qty=1.0,
            chaise_qty=0.0,
            chair_qty=0.0,
            stages=('priming',),
        )
        plan = self._plan(production)
        timing = plan.line_ids.operation_id.product_time_id.ensure_one()
        auto_operation = plan.line_ids.operation_id.ensure_one()
        manager = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'MPS Timing Manager',
            'login': 'mps.timing.manager@example.invalid',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('furniture_mrp.group_furniture_mrp_manager').id,
            ])],
        })

        auto_output = auto_operation.output_line_ids.ensure_one()
        auto_output.with_user(manager).write({'piece_duration_hours': 1.5})
        self.assertAlmostEqual(auto_output.piece_duration_hours, 1.5)
        self.assertAlmostEqual(timing.piece_hours, 0.0)
        self.assertAlmostEqual(timing.detailed_piece_hours, 1.5)
        with self.assertRaises(AccessError):
            timing.with_user(manager).with_context(
                furniture_mps_time_internal_sync=True,
            ).write({'company_id': self.env.company.id})
        with self.assertRaises(AccessError):
            auto_operation.with_user(manager).write({'duration_hours': 99.0})
        with self.assertRaises(AccessError):
            auto_output.with_user(manager).write({'quantity': 99.0})
        with self.assertRaises(AccessError):
            self.test_env['furniture.mrp.mps.profile'].with_user(manager).create({
                'name': 'Spoofed Auto Profile',
                'company_id': self.company.id,
                'furniture_model_id': self.model.id,
                'product_ids': [(6, 0, self.sofa.ids)],
                'is_auto_product_profile': True,
            })
        with self.assertRaises(ValidationError):
            self._new_operation(
                'invalid_zero_detailed',
                'Invalid Zero Detailed',
                'priming',
                0.0,
                ((self.sofa, 1.0),),
            )
