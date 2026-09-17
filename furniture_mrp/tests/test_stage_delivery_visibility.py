from datetime import date
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import AccessError


@tagged('post_install', '-at_install')
class TestStageDeliveryVisibility(TransactionCase):
    def test_delivery_cutoff_roles_and_date_changes(self):
        env = self.env
        P = env['furniture.mrp.production']
        today = date.today()
        buyer=env['res.partner'].create({'name':'Visibility QA buyer','is_company':True})
        prods=P.create([{'production_lane':'upholstery','product_qty':1,'state':'confirmed','use_upholstery':True,'use_priming':True} for i in range(2)])
        product=env['product.product'].create({'name':'Visibility QA product','type':'consu'})
        model=env['furniture.product.model'].create({'name':'Visibility QA model'})
        for prod in prods:
         env['furniture.mrp.production.line'].with_context(furniture_skip_material_refresh=True,furniture_skip_stage_plan_sync=True,furniture_skip_kit_plan_invalidation=True,furniture_skip_line_consolidation=True).create({'production_id':prod.id,'product_id':product.id,'product_qty':1,'furniture_order_model_id':model.id,'use_upholstery':True,'use_priming':True})
         stage=env['furniture.mrp.upholstery'].create({'name':'QA visibility','production_order_id':prod.id})
         prod.write({'upholstery_order_id':stage.id})
        orders=env['furniture.mrp.future.order'].create([{'buyer_partner_id':buyer.id,'delivery_date':'2026-09-23'},{'buyer_partner_id':buyer.id,'delivery_date':'2026-09-24'}])
        for prod,order in zip(prods,orders):prod.write({'future_order_id':order.id})
        supervisor=env['res.users'].with_context(no_reset_password=True).create({'name':'Visibility QA supervisor','login':'visibility-qa-supervisor','groups_id':[(6,0,[env.ref('furniture_mrp.group_furniture_mrp_supervisor_upholstery').id])]})
        assert supervisor
        PS=P.with_user(supervisor)
        P.set_stage_visibility_date('upholstery','2026-09-23')
        assert prods._visible_for_stage('upholstery')==prods
        assert prods.with_user(supervisor)._visible_for_stage('upholstery')==prods[:1].with_user(supervisor)
        assert not P._visibility_cutoff('priming',env.company)
        try:
         PS.set_stage_visibility_date('upholstery','2026-09-24')
         raise AssertionError('supervisor changed cutoff')
        except AccessError:pass
        for code in ['priming','upholstery']:
         admin=P.get_stage_dashboard_data(code)
         print('ADMIN_PAYLOAD',code,len(admin['orders']))
        data=PS.get_stage_dashboard_data('upholstery')
        assert prods[1].id not in [o['id'] for o in data['orders']]
        assert prods[0].id in [o['id'] for o in data['orders']]
        worker=env['res.users'].with_context(no_reset_password=True).create({'name':'Visibility QA worker','login':'visibility-qa-worker','groups_id':[(6,0,[env.ref('base.group_user').id])]})
        stage_def=env['furniture.mrp.employee.stage'].search([('code','=','upholstery')],limit=1)
        env['hr.employee'].create({'name':'Visibility QA worker','user_id':worker.id,'furniture_mrp_role':'worker','furniture_mrp_worker_stage_ids':[(6,0,stage_def.ids)]})
        env.registry.clear_cache()
        assert P.with_user(worker).search([('id','in',prods.ids)]).ids==[prods[0].id]
        assert P.search([('id','in',prods.ids)])==prods
        # Delivery date edits must invalidate the cached visibility domain immediately.
        orders[1].write({'delivery_date':'2026-09-22'})
        assert len(P.with_user(worker).search([('id','in',prods.ids)]))==2
        orders[1].write({'delivery_date':'2026-09-24'})
        assert len(P.with_user(worker).search([('id','in',prods.ids)]))==1
        # Stage record searches obey the same inclusive cutoff.
        Stage=env['furniture.mrp.upholstery']
        before=Stage.search([('production_order_id','in',prods.ids)])
        visible=Stage.with_user(supervisor).search([('id','in',before.ids)])
        assert not visible.filtered(lambda x:x.production_order_id.id==prods[1].id)
        P.set_stage_visibility_date('upholstery','2026-09-24')
        assert prods.with_user(supervisor)._visible_for_stage('upholstery')==prods.with_user(supervisor)
        P.set_stage_visibility_date('upholstery',today.isoformat())
        assert P.get_stage_dashboard_data('upholstery')['visibility_expired']
        P.set_stage_visibility_date('upholstery',False)
        assert prods.with_user(supervisor)._visible_for_stage('upholstery')==prods.with_user(supervisor)
        # Aggregate candidates must not count the later delivery's quantity.
        frame_orders = P.create([
            {'production_lane': 'frame', 'product_qty': 1, 'state': 'confirmed',
             'use_priming': True, 'future_order_id': order.id}
            for order in orders
        ])
        for production in frame_orders:
            env['furniture.mrp.production.line'].with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_kit_plan_invalidation=True,
                furniture_skip_line_consolidation=True,
            ).create({
                'production_id': production.id, 'product_id': product.id,
                'product_qty': 1, 'furniture_order_model_id': model.id,
                'use_priming': True,
            })
        priming_user = env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Visibility QA priming', 'login': 'visibility-qa-priming',
            'groups_id': [(6, 0, [env.ref('furniture_mrp.group_furniture_mrp_supervisor_priming').id])],
        })
        P.set_stage_visibility_date('priming', '2026-09-23')
        groups = env['furniture.mrp.stage.product.batch'].with_user(priming_user)._candidate_groups('priming')
        own_groups = [group for group in groups if group['product'].id == product.id]
        assert sum(group['planned_qty'] for group in own_groups) == 1
        P.set_stage_visibility_date('priming', False)
