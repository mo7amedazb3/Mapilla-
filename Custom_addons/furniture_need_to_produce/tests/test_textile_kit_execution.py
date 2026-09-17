from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTextileKitExecution(TransactionCase):
    def test_independent_cross_order_kits_and_loose_remainder(self):
        from odoo.addons.furniture_mrp.tests.test_mrp_stage_dashboard import TestFurnitureMrpStageDashboard
        from odoo.exceptions import AccessError, UserError
        from datetime import timedelta
        from odoo import fields

        env = self.env
        case = TestFurnitureMrpStageDashboard('test_cover_tailoring_dashboard_starts_received_products_without_hall_carryover')
        case.env = env
        case.Production = env['furniture.mrp.production']
        case.ProductionLine = env['furniture.mrp.production.line']
        case.product = env['product.product'].create({'name': 'KIT LIFECYCLE TEST', 'type': 'consu', 'is_storable': True})
        model = env['furniture.product.model'].create({'name': 'KIT TEST MODEL'})
        recipe = env['mrp.bom'].search([('type', '=', 'phantom')], limit=1)
        supervisor = case._create_supervisor_user(('tailoring',))
        sources = []
        for number in range(2):
            production, stages = case._create_production(('tailoring', 'upholstery'), production_lane='cover', name='KIT-TEST-%s' % number)
            lines = case._create_lines(production, (1.0, 1.0, 1.0), ('tailoring', 'upholstery'), extra_values={'furniture_order_model_id': model.id})
            lines.write({'first_stage_started': True, 'first_stage_started_stage': 'upholstery', 'planned_start_stage': 'upholstery'})
            production._ensure_stage_locations()
            production._move_stage_work_to_stock(stages['upholstery']._name, production_lines=lines)
            stages['upholstery'].with_context(furniture_skip_line_consolidation=True)._set_stage_line_ids_data('completed_production_line_ids_data', lines)
            stages['upholstery'].write({'state': 'done'})
            raw = env['product.product'].create({'name': 'KIT TEST RAW %s' % number, 'type': 'consu', 'is_storable': True})
            for line in lines:
                env['furniture.mrp.material.line'].create({'production_id': production.id, 'production_line_id': line.id, 'product_id': raw.id, 'product_uom_id': raw.uom_id.id, 'qty_needed': 2, 'stage': 'tailoring'})
            env['stock.quant']._update_available_quantity(raw, production.location_src_id or env.ref('stock.stock_location_stock'), 6)
            release = env['furniture.mrp.advance.material.release'].create_from_stage_codes(production, ('tailoring',), notify_storekeeper=False)
            release.action_issue()
            production.with_user(supervisor).action_stage_dashboard_request_order_materials('tailoring')
            sources.append((production, stages['tailoring'], lines))
        Kit = env['furniture.textile.kit']
        kits = Kit
        for index in range(2):
            members = [rows[index] for production, stage, rows in sources]
            kits |= Kit.create({'token': 'lifecycle-%s' % index, 'company_id': env.company.id, 'stage_code': 'tailoring', 'model_id': model.id, 'recipe_id': recipe.id, 'stage_timer_planned_qty': 2, 'stage_timer_planned_hours': 4, 'stage_timer_piece_hours': 2, 'member_ids': [(0,0,{'production_line_id': line.id, 'snapshot': Kit._line_snapshot(line)}) for line in members]})

        def operate(kit, action, **extra):
            return case.Production.with_user(supervisor).action_textile_kit_operation(kit.token, 'tailoring', action, **extra)

        def must_fail(callback, exception=UserError):
            try:
                with env.cr.savepoint():
                    callback()
            except exception:
                return
            raise AssertionError('Forbidden operation succeeded')

        must_fail(lambda: kits.with_user(supervisor).write({'state': 'done'}), AccessError)
        must_fail(lambda: sources[0][0].with_user(supervisor).action_stage_dashboard_start_order_product(sources[0][2].ids, 'tailoring'))
        operate(kits[0], 'start')
        assert kits[0].state == 'in_progress' and kits[1].state == 'pending'
        assert not kits[1].stage_timer_started_at
        operate(kits[1], 'start')
        assert all(kit.state == 'in_progress' for kit in kits)
        for production, stage, rows in sources:
            assert stage._manual_quality_active_lines() == rows[:2]
        operate(kits[0], 'pause')
        assert kits[0].stage_timer_paused_at and not kits[1].stage_timer_paused_at
        operate(kits[0], 'resume')
        assert not kits[0].stage_timer_paused_at
        must_fail(lambda: operate(kits[0], 'finish'))
        must_fail(lambda: operate(kits[0], 'quality', line_ids=kits[1].member_ids.production_line_id.ids, decision='pass'), AccessError)
        operate(kits[0], 'quality', line_ids=kits[0].member_ids.production_line_id.ids, decision='pass')
        operate(kits[0], 'finish')
        assert kits[0].state == 'done' and kits[1].state == 'in_progress'
        for production, stage, rows in sources:
            assert stage._manual_quality_active_lines() == rows[1:2]
            assert stage._get_stage_line_ids_data('completed_production_line_ids_data') == rows[:1]
            assert stage.state == 'in_progress'
        count = env['stock.move'].search_count([])
        operate(kits[0], 'finish')
        assert count == env['stock.move'].search_count([])
        operate(kits[1], 'quality', line_ids=kits[1].member_ids.production_line_id.ids, decision='pass')
        operate(kits[1], 'finish')
        for production, stage, rows in sources:
            assert stage._get_stage_line_ids_data('completed_production_line_ids_data') == rows[:2]
            assert stage.state == 'pending'
            production.with_user(supervisor).action_textile_loose_operation('tailoring', 'start', rows[2:].ids)
            production.with_user(supervisor).action_stage_dashboard_review_order_product_quality(rows[2:].ids, 'tailoring', 'pass')
            production.with_user(supervisor).action_textile_loose_operation('tailoring', 'finish', rows[2:].ids)
            assert stage._get_stage_line_ids_data('completed_production_line_ids_data') == rows
            assert stage.state == 'done'
            assert stage.all_substages_done
        print('PASS: cross-order kits, independent start/pause/finish, quality, permissions, idempotency', flush=True)


