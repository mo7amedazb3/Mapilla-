from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import new_test_user
from odoo.addons.furniture_mrp.tests.test_mrp_stage_dashboard import TestFurnitureMrpStageDashboard


class TestAssemblyRequisitions(TestFurnitureMrpStageDashboard):
    def setUp(self):
        super().setUp()
        self.supervisor = self._create_supervisor_user(('carpentry',))
        self.storekeeper = new_test_user(self.env, login='ar.store.' + self._testMethodName,
                                        groups='furniture_mrp.group_furniture_mrp_storekeeper')
        self.Request = self.env['furniture.assembly.requisition']
        self.config = self.env['furniture.assembly.supply.config']._for_company(self.env.company)
        # Dedicated test products avoid relying on cloned production stock.
        for key in ('nail', 'glue', 'sandpaper'):
            product = self.env['product.product'].create({
                'name': 'AR %s %s' % (key, self._testMethodName), 'type': 'consu', 'is_storable': True,
            })
            self.config.write({key + '_product_id': product.id, key + '_uom_id': product.uom_id.id})
        self.nail = self.config.nail_product_id
        self.glue = self.config.glue_product_id
        self.sandpaper = self.config.sandpaper_product_id
        self.source = self.config.source_id
        self.hall = self.config.hall_id
        self.Quant = self.env['stock.quant']
        self.Policy = self.env['furniture.assembly.supervisor.material']
        self.Policy.create({
            'company_id': self.env.company.id,
            'supervisor_id': self.supervisor.id,
            'stage_code': 'carpentry',
            'product_ids': [(6, 0, (self.nail | self.glue | self.sandpaper).ids)],
        })

    def _request(self, **quantities):
        return self.Request.with_user(self.supervisor).create(quantities)

    def _balance(self, product, location):
        self.Quant.invalidate_model(['quantity', 'reserved_quantity'])
        return self.Quant._get_available_quantity(product, location, strict=True)

    def _fill_stock(self):
        for product in (self.nail, self.glue, self.sandpaper):
            self.Quant._update_available_quantity(product, self.source, 100)

    def _approved_request(self, **quantities):
        request = self._request(**quantities)
        request.action_submit()
        request.with_user(self.storekeeper).action_approve()
        return request

    def test_request_form_only_quantities_and_role_menus(self):
        with Form(self.Request.with_user(self.supervisor), view='furniture_assembly_requisitions.assembly_request_form') as form:
            self.assertEqual(form.stage_code, 'carpentry')
            self.assertTrue(form.line_ids)
            with form.line_ids.edit(0) as line:
                product = line.product_id
                uom = line.product_uom_id
                line.requested_qty = 3
        request = form.record
        self.assertEqual(request.stage_code, 'carpentry')
        self.assertEqual(request.line_ids[0].product_id, product)
        self.assertEqual(request.line_ids[0].product_uom_id, uom)
        self.assertEqual(request.line_ids[0].requested_qty, 3)
        root = self.env.ref('furniture_assembly_requisitions.assembly_request_root')
        self.assertFalse(root.parent_id)
        self.assertIn(root.id, self.env['ir.ui.menu'].with_user(self.supervisor)._visible_menu_ids())
        self.assertNotIn(root.id, self.env['ir.ui.menu'].with_user(self.storekeeper)._visible_menu_ids())
        self.assertEqual(self.Request.with_user(self.storekeeper).search_count([('id', '=', request.id)]), 0)
        other = self._create_supervisor_user(('painting',))
        self.assertIn(root.id, self.env['ir.ui.menu'].with_user(other)._visible_menu_ids())
        with self.assertRaises(AccessError):
            self.Request.with_user(other).create({'nail_qty': 1})
        with self.assertRaises(AccessError):
            self.Request.with_user(other).create({'stage_code': 'carpentry'})
        with self.assertRaises(AccessError):
            request.action_approve()
        with self.assertRaises(AccessError):
            request.write({'nail_product_id': self.glue.id})
        with self.assertRaises(AccessError):
            request.write({'state': 'done'})
        with self.assertRaises(AccessError):
            self._request(nail_qty=1, requested_by_id=self.storekeeper.id)
        with self.assertRaises(AccessError):
            self.config.with_user(self.supervisor).write({'source_id': self.hall.id})

    def test_admin_can_read_request_dependencies_from_the_app(self):
        request = self._request(nail_qty=1)
        admin = self.env.ref('base.user_admin')

        self.assertEqual(
            request.line_ids.with_user(admin).mapped('requested_qty'),
            [1.0],
        )
        self.assertEqual(
            request.config_id.with_user(admin).source_id,
            self.source,
        )
        with self.assertRaises(AccessError):
            request.line_ids.with_user(admin).write({'requested_qty': 2})
        with self.assertRaises(AccessError):
            request.config_id.with_user(admin).write({'hall_id': self.source.id})

    def test_legacy_request_quantities_are_backfilled_as_visible_lines(self):
        request = self._request(nail_qty=4, glue_qty=2)
        self.assertFalse(request.line_ids)

        created = request._backfill_legacy_lines()

        self.assertEqual(set(created.mapped('product_id').ids), {self.nail.id, self.glue.id})
        self.assertEqual(
            {line.product_id.id: line.requested_qty for line in request.line_ids},
            {self.nail.id: 4.0, self.glue.id: 2.0},
        )

    def test_each_supervisor_only_sees_and_requests_assigned_materials(self):
        policy = self.Policy.search([
            ('supervisor_id', '=', self.supervisor.id),
            ('stage_code', '=', 'carpentry'),
        ]).ensure_one()
        policy.product_ids = self.nail
        defaults = self.Request.with_user(self.supervisor).default_get([
            'stage_code', 'line_ids',
        ])
        commands = defaults['line_ids']
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][2]['product_id'], self.nail.id)

        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.Request.with_user(self.supervisor).create({
                'stage_code': 'carpentry',
                'line_ids': [(0, 0, {
                    'product_id': self.glue.id,
                    'product_uom_id': self.glue.uom_id.id,
                    'requested_qty': 1,
                })],
            })
        forged = self._request(glue_qty=1)
        with self.assertRaises(AccessError):
            forged.action_submit()

    def test_painting_and_priming_use_recipe_lines(self):
        painting_supervisor = self._create_supervisor_user(('painting',))
        painting_product = self.env['product.product'].create({
            'name': 'AR painting material', 'type': 'consu',
            'is_storable': True,
        })
        self.Policy.create({
            'company_id': self.env.company.id,
            'supervisor_id': painting_supervisor.id,
            'stage_code': 'painting',
            'product_ids': [(6, 0, painting_product.ids)],
        })
        with Form(
            self.Request.with_user(painting_supervisor),
            view='furniture_assembly_requisitions.assembly_request_form',
        ) as form:
            self.assertEqual(form.stage_code, 'painting')
            self.assertTrue(form.line_ids)
            with form.line_ids.edit(0) as line:
                product = line.product_id
                line.requested_qty = 2
        request = form.record
        self.Quant._update_available_quantity(product, self.source, 10)
        request.action_submit()
        request.with_user(self.storekeeper).action_approve()
        self.assertEqual(request.state, 'done')
        self.assertEqual(
            request.sudo().move_ids.location_dest_id,
            self.env.ref('furniture_mrp.location_stage_painting_wip'),
        )
        self.assertEqual(request.sudo().move_ids.product_id, product)

        priming_supervisor = self._create_supervisor_user(('priming',))
        priming_product = self.env['product.product'].create({
            'name': 'AR priming material', 'type': 'consu',
            'is_storable': True,
        })
        self.Policy.create({
            'company_id': self.env.company.id,
            'supervisor_id': priming_supervisor.id,
            'stage_code': 'priming',
            'product_ids': [(6, 0, priming_product.ids)],
        })
        root = self.env.ref(
            'furniture_assembly_requisitions.assembly_request_root'
        )
        self.assertIn(
            root.id,
            self.env['ir.ui.menu'].with_user(
                priming_supervisor
            )._visible_menu_ids(),
        )
        with Form(
            self.Request.with_user(priming_supervisor),
            view='furniture_assembly_requisitions.assembly_request_form',
        ) as form:
            self.assertEqual(form.stage_code, 'priming')
            self.assertEqual(len(form.line_ids), 1)
            with form.line_ids.edit(0) as line:
                line.requested_qty = 3
        priming_request = form.record
        self.Quant._update_available_quantity(priming_product, self.source, 10)
        priming_request.action_submit()
        priming_request.with_user(self.storekeeper).action_approve()
        self.assertEqual(
            priming_request.sudo().move_ids.location_dest_id,
            self.env.ref('furniture_mrp.location_stage_priming_wip'),
        )

    def test_approve_transfers_once_and_notifies(self):
        self._fill_stock()
        request = self._request(nail_qty=7, glue_qty=2.5, sandpaper_qty=3)
        request.action_submit()
        self.assertTrue(request.sudo().activity_ids)
        self.assertEqual(self._balance(self.nail, self.hall), 0)
        request.action_submit()
        request.with_user(self.storekeeper).action_approve()
        request.with_user(self.storekeeper).action_approve()
        self.assertEqual(request.state, 'done')
        self.assertEqual(len(request.sudo().move_ids), 3)
        self.assertEqual(set(request.sudo().move_ids.mapped('state')), {'done'})
        self.assertEqual(self._balance(self.nail, self.source), 93)
        self.assertEqual(self._balance(self.nail, self.hall), 7)
        self.assertEqual(self._balance(self.glue, self.hall), 2.5)
        self.assertFalse(request.sudo().activity_ids)
        with self.assertRaises(UserError):
            request.write({'nail_qty': 10})
        with self.assertRaises(UserError):
            request.with_user(self.storekeeper).action_reject()

    def test_shortage_is_atomic_and_request_remains_pending(self):
        self.Quant._update_available_quantity(self.nail, self.source, 20)
        request = self._request(nail_qty=5, glue_qty=3)
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.storekeeper).action_approve()
        self.assertEqual(request.state, 'pending')
        self.assertFalse(request.sudo().move_ids)
        self.assertEqual(self._balance(self.nail, self.source), 20)
        self.assertEqual(self._balance(self.nail, self.hall), 0)

    def test_reject_zero_negative_and_company_scope(self):
        request = self._request()
        with self.assertRaises(UserError):
            request.action_submit()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._request(nail_qty=-1)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._request(glue_qty=float('inf'))
        request.write({'nail_qty': 2})
        request.action_submit()
        request.with_user(self.storekeeper).action_reject()
        self.assertEqual(request.state, 'rejected')
        self.assertFalse(request.sudo().move_ids)
        company = self.env['res.company'].create({'name': 'AR isolated company'})
        with self.assertRaises(AccessError):
            self._request(nail_qty=1, company_id=company.id)

    def _assembly_order(self, arrive=True, name=None):
        production, stages = self._create_production(('priming', 'carpentry'), name=name)
        line = self._create_lines(production, (1,), ('priming', 'carpentry'))
        self._set_tracking(stages['priming'])
        self._set_tracking(stages['carpentry'])
        materials = self.env['furniture.mrp.material.line'].create([{
            'production_id': production.id, 'production_line_id': line.id,
            'product_id': product.id, 'product_uom_id': product.uom_id.id,
            'stage': 'carpentry', 'qty_needed': quantity,
        } for product, quantity in ((self.nail, 2), (self.glue, 1), (self.sandpaper, 1))])
        if arrive:
            line.write({'first_stage_started': True, 'first_stage_started_stage': 'priming'})
            stages['priming'].write({'state': 'done'})
            self._set_tracking(stages['priming'], completed=line)
            production._create_internal_moves_batch([{
                'source_location': production._get_production_location(),
                'dest_location': self.hall, 'product': self.product,
                'quantity': 1, 'uom': self.product.uom_id,
                'label': 'Test completed priming arrival', 'source_production_line': line,
                'source_production_lines': line,
            }])
        return production, line, materials, stages

    def _assembly_token(self, line):
        Batch = self.env['furniture.mrp.stage.product.batch']
        group = next(group for group in Batch._candidate_groups('carpentry') if line in group['lines'])
        return group['batch_token']

    def _request_and_receive_batch_materials(self, token):
        current = self.Production.with_user(self.supervisor)
        result = current.action_stage_dashboard_request_product_batch_materials(
            token, 'carpentry',
        )
        self.assertEqual(result['state'], 'waiting_store')
        batch = self.env['furniture.mrp.stage.product.batch'].sudo().search([
            ('token', '=', token),
        ]).ensure_one()
        release = batch.advance_release_id
        release.sudo().write({'assigned_to_id': self.storekeeper.id})
        release.with_user(self.storekeeper).action_issue()
        result = current.action_stage_dashboard_receive_product_batch_materials(
            token, 'carpentry',
        )
        self.assertEqual(result['state'], 'ready')
        return batch, release

    def test_assembly_full_flow_consumes_hall_once_then_quality_and_finish(self):
        self._fill_stock()
        # One of the two required nails is supplied externally.  The normal
        # stage request must ask for the remaining one, not zero or two.
        self._approved_request(nail_qty=1)
        manual_ids = self.Request._manual_supply_product_ids(
            self.env.company, 'carpentry',
        )
        self.assertIn(self.nail.id, manual_ids)
        self.assertNotIn(self.glue.id, manual_ids)
        production, line, materials, stages = self._assembly_order()
        token = self._assembly_token(line)
        current = self.Production.with_user(self.supervisor)
        initial_release_count = self.env['furniture.mrp.advance.material.release'].search_count([])
        before_stock = self._balance(self.nail, self.source)
        payload = next(p for p in current.get_stage_dashboard_data('carpentry')['product_batches'] if p['batch_token'] == token)
        self.assertTrue(payload['can_request_materials'])
        self.assertFalse(payload['can_start'])
        _batch, release = self._request_and_receive_batch_materials(token)
        requested_products = release.stage_line_ids.material_line_ids.mapped(
            'product_id'
        )
        self.assertIn(self.glue, requested_products)
        self.assertIn(self.sandpaper, requested_products)
        self.assertIn(self.nail, requested_products)
        nail_detail = release.stage_line_ids.material_line_ids.filtered(
            lambda detail: detail.product_id == self.nail
        )
        self.assertEqual(nail_detail.requested_qty, 1)
        result = current.action_stage_dashboard_start_product_batch(token, 'carpentry')
        self.assertEqual(result['state'], 'in_progress')
        self.assertEqual(self._balance(self.nail, self.hall), 0)
        self.assertEqual(self._balance(self.glue, self.hall), 0)
        self.assertEqual(self._balance(self.nail, self.source), before_stock - 1)
        current.action_stage_dashboard_start_product_batch(token, 'carpentry')
        self.assertEqual(self._balance(self.nail, self.hall), 0)
        self.assertTrue(all(production._material_line_already_consumed(row) for row in materials))
        with self.assertRaises(UserError):
            current.action_stage_dashboard_finish_product_batch(token, 'carpentry')
        current.action_stage_dashboard_review_product_batch_quality(token, 'carpentry', 'pass')
        result = current.action_stage_dashboard_finish_product_batch(token, 'carpentry')
        self.assertEqual(result['state'], 'done')
        current.action_stage_dashboard_finish_product_batch(token, 'carpentry')
        self.assertEqual(self._balance(self.nail, self.hall), 0)
        self.assertEqual(self.env['furniture.mrp.advance.material.release'].search_count([]), initial_release_count + 1)

    def test_assembly_shortage_does_not_pull_stock_or_start(self):
        self._fill_stock()
        production, line, materials, stages = self._assembly_order()
        token = self._assembly_token(line)
        with self.assertRaises(UserError):
            self.Production.with_user(self.supervisor).action_stage_dashboard_start_product_batch(token, 'carpentry')
        self.assertEqual(stages['carpentry'].state, 'pending')
        self.assertFalse(materials.mapped('move_id'))
        self.assertEqual(self._balance(self.nail, self.source), 100)
        self.assertEqual(self._balance(self.nail, self.hall), 0)

    def test_upstream_gate_and_normal_request_paths_stay_enabled(self):
        self._fill_stock()
        self._approved_request(nail_qty=10, glue_qty=5, sandpaper_qty=6)
        production, line, materials, stages = self._assembly_order(arrive=False)
        candidates = self.env['furniture.mrp.stage.product.batch']._candidate_groups('carpentry')
        self.assertFalse(any(line in group['lines'] for group in candidates))
        with self.assertRaises(UserError):
            stages['carpentry'].with_user(self.supervisor).action_start()
        validated, codes = self.env[
            'furniture.mrp.advance.material.release'
        ]._validate_stage_request(production, ['carpentry'])
        self.assertEqual(validated, production)
        self.assertEqual(codes, ['carpentry'])
        self.assertEqual(self._balance(self.nail, self.hall), 10)
        self.assertFalse(materials.mapped('move_id'))
        arch = self.env.ref('furniture_mrp.view_furniture_mrp_carpentry_form')._get_combined_arch()
        self.assertTrue(arch.xpath("//button[@name='action_request_store_approval']"))

    def test_assembly_materials_remain_in_order_stock_reservation(self):
        self._fill_stock()
        self._approved_request(nail_qty=1)
        production, line, materials, stages = self._assembly_order()
        production.invalidate_recordset(['material_line_ids'])
        requirement_products = production._material_reservation_requirement_lines(
        ).mapped('product_id')
        self.assertIn(self.nail, requirement_products)
        self.assertIn(self.glue, requirement_products)
        self.assertIn(self.sandpaper, requirement_products)

    def test_other_stages_keep_request_receipt_and_quality_cycle(self):
        super().test_product_batch_flow_freezes_membership_and_scopes_materials()

    def test_two_orders_cannot_consume_the_same_hall_balance(self):
        self._fill_stock()
        self._approved_request(nail_qty=2, glue_qty=1, sandpaper_qty=1)
        first, first_line, first_materials, first_stages = self._assembly_order(name='AR first shared order')
        self.product = self.env['product.product'].create({
            'name': 'Second assembly product', 'type': 'consu', 'is_storable': True,
        })
        second, second_line, second_materials, second_stages = self._assembly_order(name='AR second shared order')
        first._shared_hall_materials('carpentry', first_line, consume=True)
        with self.assertRaises(UserError):
            second._shared_hall_materials('carpentry', second_line, consume=True)
        self.assertEqual(self._balance(self.nail, self.hall), 0)
        self.assertEqual(self._balance(self.nail, self.source), 98)
        self.assertEqual(second_stages['carpentry'].state, 'pending')
        self.assertFalse(second_materials.mapped('move_id'))

    def test_requisition_respects_reserved_stock(self):
        self._fill_stock()
        production, _ = self._create_production(('priming',))
        self.env['furniture.mrp.material.reservation'].create({
            'production_id': production.id, 'company_id': self.env.company.id,
            'source_location_id': self.source.id, 'product_id': self.nail.id,
            'product_uom_id': self.nail.uom_id.id, 'required_qty': 98,
            'reserved_qty': 98, 'shortage_qty': 0, 'state': 'active',
        })
        request = self._request(nail_qty=3)
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.storekeeper).action_approve()
        self.assertEqual(self._balance(self.nail, self.source), 100)
        self.assertFalse(request.sudo().move_ids)

    def test_fixed_recipe_unit_conversion(self):
        self._fill_stock()
        dozen = self.env.ref('uom.product_uom_dozen')
        self.config.write({'nail_uom_id': dozen.id})
        request = self._approved_request(nail_qty=2)
        self.assertEqual(request.nail_qty, 2)
        self.assertEqual(request.nail_uom_id, dozen)
        self.assertEqual(self._balance(self.nail, self.hall), 24)
        self.assertEqual(self._balance(self.nail, self.source), 76)

    def test_direct_stage_start_requires_the_normal_stage_request(self):
        self._fill_stock()
        self._approved_request(nail_qty=10)
        production, line, materials, stages = self._assembly_order()
        current = production.with_user(self.supervisor)
        current._stage_dashboard_assign_current_foreman(stages['carpentry'], 'carpentry')
        with self.assertRaises(UserError):
            stages['carpentry'].with_user(self.supervisor).action_start()
        self.assertEqual(stages['carpentry'].state, 'pending')
        self.assertEqual(self._balance(self.nail, self.hall), 10)
        self.assertEqual(self._balance(self.nail, self.source), 90)

    def test_material_period_inclusive_dates_and_saved_report_unchanged(self):
        self.env.company.partner_id.tz = 'Africa/Cairo'
        self._fill_stock()
        request = self._approved_request(nail_qty=4)
        production, line, _materials, stages = self._assembly_order()
        token = self._assembly_token(line)
        batch, _release = self._request_and_receive_batch_materials(token)
        current = production.with_user(self.supervisor)
        current.action_stage_dashboard_start_product_batch(token, 'carpentry')
        current.action_stage_dashboard_review_product_batch_quality(token, 'carpentry', 'pass')
        current.action_stage_dashboard_finish_product_batch(token, 'carpentry')
        Report = self.env['furniture.assembly.weekly.material.report']
        report = Report.create({'week_start': '1951-01-05'})
        snapshot = report.read(['week_start', 'week_end', 'generated_at', 'line_ids'])
        start, stop = Report._utc_bounds(
            self.env.company, fields.Date.to_date('2026-01-01'), fields.Date.to_date('2026-01-08'),
        )
        batch.sudo().write({'finished_at': stop - timedelta(seconds=1)})
        stages['carpentry'].sudo().write({'date_finish': stop - timedelta(seconds=1)})

        def amounts(date_from='2026-01-01', date_to='2026-01-08'):
            data = report.get_period_materials(date_from, date_to)
            self.assertEqual(data['date_from'], date_from)
            self.assertEqual(data['date_to'], date_to)
            stage = next(stage for stage in data['stages'] if stage['code'] == 'carpentry')
            return next(row for row in stage['lines'] if row['id'] == self.nail.id)

        for move_date, expected in [
            (start - timedelta(seconds=1), 0), (start, 4),
            (stop - timedelta(seconds=1), 4), (stop, 0),
        ]:
            request.sudo().move_ids.write({'date': move_date})
            result = amounts()
            self.assertEqual(result['manual_qty'], expected)
            self.assertEqual(result['recipe_qty'], 2)
        self.assertEqual(amounts('2026-01-08', '2026-01-08')['recipe_qty'], 2)
        self.assertEqual(amounts('2026-01-10', '2026-01-11')['recipe_qty'], 0)
        second_supervisor = new_test_user(
            self.env, login='period.second.supervisor',
            groups='furniture_mrp.group_furniture_mrp_supervisor_carpentry',
        )
        self.Policy.create({
            'company_id': self.env.company.id, 'supervisor_id': second_supervisor.id,
            'stage_code': 'carpentry', 'product_ids': [(6, 0, self.nail.ids)],
        })
        rows = next(stage for stage in report.get_period_materials('2026-01-01', '2026-01-08')['stages'] if stage['code'] == 'carpentry')['lines']
        self.assertEqual(len([row for row in rows if row['id'] == self.nail.id]), 1)
        self.assertEqual(report.read(['week_start', 'week_end', 'generated_at', 'line_ids']), snapshot)
        with self.assertRaises(ValidationError):
            report.get_period_materials('2026-01-08', '2026-01-01')
        with self.assertRaises(ValidationError):
            report.get_period_materials(False, '2026-01-08')
        with self.assertRaises(AccessError):
            report.with_user(self.supervisor).get_period_materials('2026-01-01', '2026-01-08')

    def test_weekly_admin_inventory_compares_recipe_and_carries_actual_balance(self):
        self._fill_stock()
        self._approved_request(nail_qty=1)
        production, line, _materials, _stages = self._assembly_order()
        token = self._assembly_token(line)
        current = production.with_user(self.supervisor)
        self._request_and_receive_batch_materials(token)
        current.action_stage_dashboard_start_product_batch(token, 'carpentry')
        current.action_stage_dashboard_review_product_batch_quality(
            token, 'carpentry', 'pass',
        )
        current.action_stage_dashboard_finish_product_batch(token, 'carpentry')

        recipe_uom = self.env.ref('furniture_mrp.furniture_uom_box')
        recipe_bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'product_qty': 1,
            'company_id': self.env.company.id,
        })
        self.env['furniture.mrp.bom.stage.line'].create({
            'bom_id': recipe_bom.id,
            'stage': 'carpentry',
            'product_id': self.nail.id,
            'product_uom_code': 'box',
            'product_uom_id': recipe_uom.id,
            'product_qty': 2,
        })

        Report = self.env['furniture.assembly.weekly.material.report']
        current_week_start, _current_week_end = Report._week_dates()
        Report.search([
            ('company_id', '=', self.env.company.id),
            ('week_start', '=', current_week_start),
        ]).unlink()
        report = Report.create({'company_id': self.env.company.id})
        stage_fields = {
            'priming': 'priming_line_ids',
            'painting': 'painting_line_ids',
            'carpentry': 'carpentry_line_ids',
            'bases': 'bases_line_ids',
            'finishing': 'finishing_line_ids',
            'tailoring': 'tailoring_line_ids',
            'upholstery': 'upholstery_line_ids',
            'packaging': 'packaging_line_ids',
        }
        tab_line_ids = set()
        for stage_code, field_name in stage_fields.items():
            tab_lines = report[field_name]
            self.assertEqual(set(tab_lines.mapped('stage_code')), {stage_code} if tab_lines else set())
            tab_line_ids.update(tab_lines.ids)
        self.assertEqual(tab_line_ids, set(report.line_ids.ids))
        nail_line = report.line_ids.filtered(
            lambda item: item.product_id == self.nail
        ).ensure_one()
        self.assertEqual(nail_line.opening_qty, 0)
        self.assertEqual(nail_line.received_qty, 2)
        self.assertEqual(nail_line.completed_piece_qty, 1)
        self.assertEqual(nail_line.recipe_used_qty, 2)
        self.assertEqual(nail_line.theoretical_qty, 0)

        supervisor_data = report.with_user(self.supervisor).get_period_materials(
            fields.Date.to_string(report.week_start),
            fields.Date.to_string(report.week_end),
        )
        self.assertTrue(supervisor_data['is_supervisor'])
        self.assertFalse(supervisor_data['is_admin'])
        self.assertEqual(
            [stage['code'] for stage in supervisor_data['stages']],
            ['carpentry'],
        )
        supervisor_nail = next(
            row for row in supervisor_data['stages'][0]['lines']
            if row['id'] == self.nail.id
        )
        self.assertEqual(
            set(supervisor_nail),
            {
                'id', 'name', 'uom_id', 'uom_name', 'uom_options',
                'counted', 'actual_qty', 'status',
            },
        )
        self.assertEqual(supervisor_nail['uom_id'], recipe_uom.id)
        self.assertEqual(supervisor_nail['uom_name'], recipe_uom.display_name)
        self.assertIn(recipe_uom.id, {
            option['id'] for option in supervisor_nail['uom_options']
        })
        self.assertIn(self.nail.uom_id.id, {
            option['id'] for option in supervisor_nail['uom_options']
        })
        self.assertEqual(supervisor_nail['status'], 'pending')
        saved = report.with_user(self.supervisor).save_actual_inventory(
            'carpentry', self.nail.id, 1, recipe_uom.id,
        )
        self.assertTrue(saved['counted'])
        self.assertEqual(saved['actual_qty'], 1)
        self.assertEqual(saved['uom_id'], recipe_uom.id)
        self.assertEqual(saved['status'], 'shortage')
        self.assertNotIn('variance_qty', saved)
        nail_line.invalidate_recordset([
            'counted', 'actual_qty', 'actual_uom_id', 'variance_qty',
        ])
        self.assertTrue(nail_line.counted)
        self.assertEqual(nail_line.actual_qty, 1)
        self.assertEqual(nail_line.actual_uom_id, recipe_uom)
        self.assertEqual(nail_line.variance_qty, -3)

        selected = report.with_user(
            self.supervisor
        ).set_actual_inventory_uom(
            'carpentry', self.nail.id, self.nail.uom_id.id,
        )
        self.assertEqual(selected['uom_id'], self.nail.uom_id.id)
        self.assertEqual(selected['actual_qty'], 1)
        with self.assertRaises(AccessError):
            report.with_user(self.supervisor).set_actual_inventory_uom(
                'carpentry', self.nail.id,
                self.env.ref('uom.product_uom_hour').id,
            )

        self.assertEqual(
            report.with_user(self.supervisor).save_actual_inventory(
                'carpentry', self.nail.id, 5, self.nail.uom_id.id,
            )['status'],
            'surplus',
        )
        self.assertEqual(
            report.with_user(self.supervisor).save_actual_inventory(
                'carpentry', self.nail.id, 4, self.nail.uom_id.id,
            )['status'],
            'balanced',
        )
        report.with_user(self.supervisor).save_actual_inventory(
            'carpentry', self.nail.id, 1, self.nail.uom_id.id,
        )

        admin_data = report.get_period_materials(
            fields.Date.to_string(report.week_start),
            fields.Date.to_string(report.week_end),
        )
        admin_nail = next(
            row
            for stage in admin_data['stages'] if stage['code'] == 'carpentry'
            for row in stage['lines'] if row['id'] == self.nail.id
        )
        self.assertEqual(admin_nail['actual_qty'], 1)
        self.assertEqual(admin_nail['variance_qty'], -3)
        self.assertIn('recipe_qty', admin_nail)
        self.assertIn('manual_qty', admin_nail)
        self.assertEqual(admin_nail['uom_name'], self.nail.uom_id.display_name)

        action = self.env.ref(
            'furniture_assembly_requisitions.'
            'action_assembly_weekly_material_report_direct'
        ).with_user(self.supervisor).run()
        self.assertEqual(action['res_id'], report.id)
        with self.assertRaises(AccessError):
            report.with_user(self.supervisor).write({
                'generated_at': fields.Datetime.now(),
            })

        other_supervisor = self._create_supervisor_user(('painting',))
        with self.assertRaises(AccessError):
            report.with_user(other_supervisor).save_actual_inventory(
                'carpentry', self.nail.id, 1,
            )
        with self.assertRaises(ValidationError):
            report.with_user(self.supervisor).save_actual_inventory(
                'carpentry', self.nail.id, -1,
            )
        visible_lines = self.env[
            'furniture.assembly.weekly.material.report.line'
        ].with_user(self.supervisor).search([('report_id', '=', report.id)])
        self.assertEqual(set(visible_lines.mapped('supervisor_id').ids), {
            self.supervisor.id,
        })

        for report_line in report.line_ids:
            report_line.write({
                'counted': True,
                'actual_qty': report_line.theoretical_qty,
            })
        nail_line.write({'actual_qty': 1})
        self.assertEqual(nail_line.variance_qty, -3)
        self.assertNotIn('state', Report._fields)

        next_report = Report.create({
            'company_id': self.env.company.id,
            'week_start': report.week_start + timedelta(days=7),
        })
        next_nail_line = next_report.line_ids.filtered(
            lambda item: item.product_id == self.nail
        ).ensure_one()
        self.assertEqual(next_nail_line.opening_qty, 1)
        self.assertIn(
            report,
            Report.with_user(self.supervisor).search([
                ('company_id', '=', self.env.company.id),
            ]),
        )
