from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_SELECTION,
)


@tagged('post_install', '-at_install')
class TestNeedSupervisorCompleted(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Production = cls.env['furniture.mrp.production']
        cls.product = cls.env['product.product'].create({
            'name': 'Need supervisor history product',
            'type': 'consu',
            'is_storable': True,
        })

    def _supervisor(self, stage_code):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Need supervisor %s' % stage_code,
            'login': 'need.supervisor.%s.%s' % (stage_code, self._testMethodName),
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        stages = self.env['furniture.mrp.employee.stage'].search([
            ('code', '=', stage_code),
        ])
        self.env['hr.employee'].create({
            'name': user.name,
            'user_id': user.id,
            'company_id': self.env.company.id,
            'furniture_mrp_role': 'supervisor',
            'furniture_mrp_supervisor_stage_ids': [(6, 0, stages.ids)],
        })
        return user

    def _order(self, route=('priming',), quantities=(3.0,), stage_records=True):
        context = {
            'furniture_skip_material_refresh': True,
            'furniture_skip_stage_plan_sync': True,
            'furniture_skip_line_consolidation': True,
            'furniture_skip_kit_plan_invalidation': True,
            'furniture_skip_running_line_initialization': True,
        }
        stage_values = {'use_%s' % code: code in route for code, _ in FURNITURE_STAGE_SELECTION}
        production = self.Production.with_context(**context).create({
            'name': 'NEED-HISTORY-%s' % self._testMethodName,
            'company_id': self.env.company.id,
            'state': 'confirmed',
            'stage_plan_mode': 'custom',
            'production_lane': 'legacy',
            **stage_values,
        })
        lines = self.env['furniture.mrp.production.line']
        for sequence, qty in enumerate(quantities):
            lines |= lines.with_context(**context).create({
                'production_id': production.id,
                'product_id': self.product.id,
                'product_qty': qty,
                'sequence': sequence,
                'stage_selection_initialized': True,
                **stage_values,
            })
        stages = {}
        if stage_records:
            for code in route:
                field_name = '%s_order_id' % code
                stage = self.env[production._fields[field_name].comodel_name].create({
                    'name': '%s-%s' % (production.name, code),
                    'production_order_id': production.id,
                    'state': 'pending',
                })
                production.with_context(**context).write({field_name: stage.id})
                stages[code] = stage
        return production, lines, stages

    def _complete(self, stage, lines):
        stage.with_context(furniture_skip_line_consolidation=True)._set_stage_line_ids_data(
            'completed_production_line_ids_data', lines,
        )

    def test_completed_history_survives_closed_parent_without_inflating_live_plan(self):
        production, lines, stages = self._order()
        self._complete(stages['priming'], lines)
        production.with_context(furniture_skip_line_consolidation=True).write({'state': 'done'})
        supervisor = self._supervisor('priming')
        sections = self.Production.with_user(supervisor).get_need_supervisor_stage_sections('priming')
        self.assertEqual(sections['completed_quantity'], 3.0)
        self.assertEqual(len(sections['completed']), 1)
        self.assertFalse(sections['waiting'])
        manager_data = self.Production.get_stage_dashboard_data('priming')
        self.assertNotIn(production.id, [row['id'] for row in manager_data['orders']])

    def test_partial_history_counts_only_exact_completed_lines(self):
        _production, lines, stages = self._order(quantities=(1.0, 2.0))
        self._complete(stages['priming'], lines[:1])
        supervisor = self._supervisor('priming')
        sections = self.Production.with_user(supervisor).get_need_supervisor_stage_sections('priming')
        self.assertEqual(sections['completed_quantity'], 1.0)
        self.assertEqual(len(sections['completed']), 1)

    def test_planned_counter_counts_orders_not_product_quantities(self):
        supervisor = self._supervisor('priming')
        Production = self.Production.with_user(supervisor)
        before = Production.get_need_supervisor_stage_sections('priming')['planned_order_count']
        _production, lines, stages = self._order(quantities=(6.0, 12.0))
        self.assertEqual(Production.get_need_supervisor_stage_sections('priming')['planned_order_count'], before + 1)
        self._complete(stages['priming'], lines[:1])
        self.assertEqual(Production.get_need_supervisor_stage_sections('priming')['planned_order_count'], before + 1)
        self._complete(stages['priming'], lines)
        self.assertEqual(Production.get_need_supervisor_stage_sections('priming')['planned_order_count'], before)

    def test_waiting_preview_never_becomes_an_executable_candidate(self):
        production, lines, _stages = self._order(
            route=('priming', 'carpentry'), stage_records=False,
        )
        supervisor = self._supervisor('carpentry')
        Batch = self.env['furniture.mrp.stage.product.batch']
        before = [group for group in Batch._candidate_groups('carpentry') if group['lines'] & lines]
        self.assertFalse(before)
        with patch.object(
            type(self.Production), '_need_supervisor_is_linked_plan',
            side_effect=lambda candidate: candidate.id == production.id,
        ):
            sections = self.Production.with_user(supervisor).get_need_supervisor_stage_sections('carpentry')
        self.assertEqual(sections['waiting_quantity'], 3.0)
        self.assertEqual(len(sections['waiting']), 1)
        row = sections['waiting'][0]
        self.assertEqual(row['status'], 'waiting_upstream')
        for action in ('can_request_materials', 'can_receive_materials', 'can_start', 'can_finish'):
            self.assertFalse(row[action])
        self.assertNotIn('batch_token', row)
        self.assertFalse([group for group in Batch._candidate_groups('carpentry') if group['lines'] & lines])
        self.assertFalse(production.carpentry_order_id)

    def test_unlinked_existing_orders_do_not_add_future_preview(self):
        supervisor = self._supervisor('carpentry')
        before = self.Production.with_user(supervisor).get_need_supervisor_stage_sections('carpentry')
        self._order(route=('priming', 'carpentry'), stage_records=False)
        sections = self.Production.with_user(supervisor).get_need_supervisor_stage_sections('carpentry')
        # A neutralized clone can contain legitimate linked factory orders.
        # Creating this unlinked fixture must not add or remove their previews.
        self.assertEqual(sections['waiting'], before['waiting'])
        self.assertEqual(sections['waiting_quantity'], before['waiting_quantity'])

    def test_history_rejects_another_stage_and_hides_identifiers(self):
        _production, lines, stages = self._order()
        self._complete(stages['priming'], lines)
        supervisor = self._supervisor('priming')
        Production = self.Production.with_user(supervisor)
        with self.assertRaises(AccessError):
            Production.get_need_supervisor_stage_sections('painting')
        sections = Production.get_need_supervisor_stage_sections('priming')
        for row in sections['completed']:
            for key in row:
                self.assertFalse(any(word in key for word in ('production', 'order', 'buyer', 'customer', 'beneficiary')))

    def test_history_date_filter_matches_planned_start(self):
        production, lines, stages = self._order()
        self._complete(stages['priming'], lines)
        production.write({'date_planned_start': '2026-01-03 12:00:00'})
        supervisor = self._supervisor('priming')
        Production = self.Production.with_user(supervisor)
        included = Production.get_need_supervisor_stage_sections('priming', '2026-01-01', '2026-01-05')
        excluded = Production.get_need_supervisor_stage_sections('priming', '2026-02-01', '2026-02-05')
        self.assertEqual(included['completed_quantity'], 3.0)
        self.assertFalse(excluded['completed'])

    def test_manager_does_not_receive_supervisor_sections(self):
        sections = self.Production.get_need_supervisor_stage_sections('priming')
        self.assertEqual(sections['completed'], [])
        self.assertEqual(sections['waiting'], [])
