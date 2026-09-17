from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestPaintingExternalWorkflow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.production = cls.env['furniture.mrp.production'].create({
            'name': 'MRP/PAINTING/EXTERNAL/TEST',
            'product_qty': 1.0,
        })

    def _create_external_stage(self):
        return self.env['furniture.mrp.painting'].create({
            'name': 'PNT/EXTERNAL/FLOW',
            'production_order_id': self.production.id,
            'state': 'in_progress',
            'substage_cells_required': True,
            'substage_veneer_required': True,
            'substage_paint_required': True,
            'substage_priming_required': False,
            'substage_assembly_required': False,
            'substage_impregnation_required': False,
            'substage_cells_state': 'in_progress',
            'substage_veneer_state': 'in_progress',
            'substage_paint_state': 'in_progress',
        })

    def _run_external_action(self, stage, code, method_name):
        return getattr(
            stage.with_context(painting_external_substage=code),
            method_name,
        )()

    def test_new_order_selects_and_starts_all_substages_together(self):
        stage = self.env['furniture.mrp.painting'].create({
            'name': 'PNT/ALL/PARALLEL',
            'production_order_id': self.production.id,
        })

        self.assertEqual(stage.substage_plan, 'custom')
        self.assertEqual(len(stage._get_active_substage_fields()), 6)
        for _code, _label, state_field, required_field in stage._get_active_substage_fields():
            self.assertTrue(stage[required_field], required_field)
            self.assertEqual(stage[state_field], 'pending', state_field)

        stage.write({'state': 'in_progress'})
        started_fields = stage._start_selected_substages()

        self.assertEqual(len(started_fields), 6)
        for _code, _label, state_field, _required_field in stage._get_active_substage_fields():
            self.assertEqual(stage[state_field], 'in_progress', state_field)
        self.assertEqual(stage.current_substage, '6 مراحل جارية')

    def test_external_substages_work_independently_in_any_order(self):
        stage = self._create_external_stage()

        with self.assertRaises(UserError):
            self._run_external_action(
                stage,
                'cells',
                'action_external_substage_deliver',
            )
        for code, external_field, stage_field in (
            (
                'paint',
                'substage_paint_external_state',
                'substage_paint_state',
            ),
            (
                'cells',
                'substage_cells_external_state',
                'substage_cells_state',
            ),
            (
                'veneer',
                'substage_veneer_external_state',
                'substage_veneer_state',
            ),
        ):
            self._run_external_action(
                stage,
                code,
                'action_external_substage_exit',
            )
            self.assertEqual(stage[external_field], 'out')

            self._run_external_action(
                stage,
                code,
                'action_external_substage_deliver',
            )
            self.assertEqual(stage[external_field], 'delivered')

            self._run_external_action(
                stage,
                code,
                'action_external_substage_receive',
            )
            self.assertEqual(stage[external_field], 'received')
            self.assertEqual(stage[stage_field], 'done')
            for other_stage_field in (
                'substage_cells_state',
                'substage_veneer_state',
                'substage_paint_state',
            ):
                if other_stage_field != stage_field and stage[other_stage_field] != 'done':
                    self.assertEqual(stage[other_stage_field], 'in_progress')

        self.assertTrue(stage.all_substages_done)
        self.assertEqual(stage.substage_summary, '3/3 مراحل منتهية')

    def test_internal_substages_complete_independently_in_any_order(self):
        stage = self.env['furniture.mrp.painting'].create({
            'name': 'PNT/INTERNAL/FLOW',
            'production_order_id': self.production.id,
            'state': 'in_progress',
            'substage_priming_required': True,
            'substage_assembly_required': False,
            'substage_impregnation_required': True,
            'substage_cells_required': False,
            'substage_veneer_required': False,
            'substage_paint_required': False,
            'substage_priming_state': 'in_progress',
            'substage_impregnation_state': 'in_progress',
        })

        with self.assertRaises(UserError):
            stage.action_complete_current_substage()
        stage.with_context(
            painting_internal_substage='impregnation',
        ).action_complete_internal_substage()

        self.assertEqual(stage.substage_priming_state, 'in_progress')
        self.assertEqual(stage.substage_impregnation_state, 'done')
        self.assertFalse(stage.current_substage_is_external)

        stage.with_context(
            painting_internal_substage='priming',
        ).action_complete_internal_substage()
        self.assertTrue(stage.all_substages_done)

    def test_rework_restarts_all_selected_substages_together(self):
        stage = self.env['furniture.mrp.painting'].create({
            'name': 'PNT/EXTERNAL/REWORK',
            'production_order_id': self.production.id,
            'state': 'in_progress',
            'substage_priming_required': True,
            'substage_assembly_required': False,
            'substage_cells_required': True,
            'substage_veneer_required': False,
            'substage_paint_required': True,
            'substage_impregnation_required': False,
            'substage_priming_state': 'done',
            'substage_cells_state': 'done',
            'substage_paint_state': 'done',
            'substage_cells_external_state': 'received',
            'substage_paint_external_state': 'received',
        })

        restarted_fields = stage._restart_substages_for_rework()

        self.assertEqual(set(restarted_fields), {
            'substage_priming_state',
            'substage_cells_state',
            'substage_paint_state',
        })
        self.assertEqual(stage.substage_priming_state, 'in_progress')
        self.assertEqual(stage.substage_cells_state, 'in_progress')
        self.assertEqual(stage.substage_paint_state, 'in_progress')
        self.assertEqual(stage.substage_cells_external_state, 'pending')
        self.assertEqual(stage.substage_paint_external_state, 'pending')
