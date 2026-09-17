# -*- coding: utf-8 -*-
"""Destructively verify the three Big Moon demo fixtures on disposable DBs.

This helper is intentionally hard-wired to ``codex_verify_case1/2/3``.  It
drives the real handoff, stage, quality, FIFO and finished-stock APIs and must
never be pointed at a user-facing database.
"""

import json


CASE_BY_DATABASE = {
    'codex_verify_case1': 'case1',
    'codex_verify_case2': 'case2',
    'codex_verify_case3': 'case3',
}

database_name = env.cr.dbname
case = CASE_BY_DATABASE.get(database_name)
if not case:
    raise RuntimeError('Refusing destructive verification on %s.' % database_name)

company = env['res.company'].sudo().browse(1).exists()
model = env['furniture.product.model'].sudo().browse(6).exists()
product = env['product.product'].sudo().browse(77).exists()
Production = env['furniture.mrp.production'].sudo().with_company(company)
StageHandoff = env['furniture.mrp.stage.transfer.handoff'].sudo()
FinalRule = env['furniture.mrp.final.replenishment.rule'].sudo().with_company(company)

env['ir.cron'].sudo().search([]).write({'active': False})

rule = FinalRule.search([
    ('company_id', '=', company.id),
    ('product_id', '=', product.id),
    ('furniture_model_id', '=', model.id),
], limit=1).ensure_one()
candidate_ids = FinalRule._final_replenishment_finished_product_candidates(
    rule
).ids

events = []


def active_lane(lane):
    return Production.search([
        ('company_id', '=', company.id),
        ('production_lane', '=', lane),
        ('state', 'not in', ('done', 'cancelled')),
        ('production_line_ids.active', '=', True),
        ('production_line_ids.product_id', 'in', candidate_ids),
        ('production_line_ids.furniture_order_model_id', '=', model.id),
    ], order='id desc', limit=1)


def stage_supervisor(stage_code):
    candidates = env['hr.employee'].sudo().search([
        ('company_id', '=', company.id),
        ('active', '=', True),
        ('user_id', '!=', False),
        ('user_id.active', '=', True),
        ('furniture_mrp_role', '=', 'supervisor'),
        ('furniture_mrp_supervisor_stage_ids.code', '=', stage_code),
    ], order='id desc')
    group_name = 'furniture_mrp.group_furniture_mrp_supervisor_%s' % stage_code
    candidates = candidates.filtered(
        lambda employee: employee.user_id.has_group(group_name)
    )
    if not candidates:
        raise RuntimeError('No fully-authorized supervisor for %s.' % stage_code)
    # Prefer a supervisor with an email so real chatter/audit messages exercise
    # the same successful path as the UI.
    return (
        candidates.filtered(lambda employee: employee.user_id.email)[:1]
        or candidates[:1]
    ).ensure_one()


def accept_lane_handoffs(production):
    production = production.sudo()
    pending = production.upstream_handoff_ids.filtered(
        lambda row: row.state == 'reserved'
    )
    if pending:
        target_stage, _target_label = (
            production._furniture_handoff_target_stage()
        )
        production.with_user(
            stage_supervisor(target_stage).user_id
        ).action_accept_handoff_transfer()
    production.invalidate_recordset(['upstream_handoff_ids'])
    if production.upstream_handoff_ids.filtered(
        lambda row: row.state != 'consumed'
    ):
        raise RuntimeError('Lane handoff was not consumed for %s.' % production.name)
    events.append('accept-lane:%s:%s' % (
        production.production_lane,
        production.name,
    ))


def accept_stage_handoffs(production, target_stage):
    pending = StageHandoff.search([
        ('production_id', '=', production.id),
        ('target_stage', '=', target_stage),
        ('state', '=', 'pending'),
    ], order='id')
    if not pending:
        raise RuntimeError(
            'No pending internal handoff to %s for %s.'
            % (target_stage, production.name)
        )
    for handoff in pending:
        production.with_user(
            stage_supervisor(target_stage).user_id
        ).action_accept_handoff_transfer(
            stage_handoff_id=handoff.id,
        )
    pending.invalidate_recordset(['state'])
    if pending.filtered(lambda row: row.state != 'accepted'):
        raise RuntimeError('Internal handoff was not accepted.')
    events.append('accept-stage:%s:%s' % (target_stage, production.name))


def start_stage(production, stage_code):
    production = production.sudo()
    getattr(production, 'action_start_%s' % stage_code)()
    stage_order = production['%s_order_id' % stage_code].sudo().ensure_one()
    stage_order.write({'foreman_id': stage_supervisor(stage_code).id})

    start_context = {
        'furniture_storekeeper_approval_bypass': True,
        'furniture_skip_stage_start_prompt': True,
        'furniture_skip_stage_quality_prompt': True,
    }
    if production._is_material_only_stage(stage_code):
        stage_order.with_context(**start_context).action_start()
    else:
        action = stage_order._maybe_open_stage_start_carryover_wizard()
        if not action:
            stage_order.with_context(**start_context).action_start()
        else:
            wizard = env[action['res_model']].sudo().with_context(
                **start_context
            ).browse(action['res_id']).exists().ensure_one()
            if wizard._name == 'furniture.mrp.first.stage.start.wizard':
                wizard.action_start_selected_from_stock()
            else:
                try:
                    wizard.action_start_current_only()
                except Exception as error:
                    rows = [{
                        'product': row.product_id.display_name,
                        'qty': row.qty_to_start,
                        'source_production': row.source_production_id.id,
                        'source_line': row.source_production_line_id.id,
                    } for row in wizard.line_ids]
                    lines = [{
                        'id': line.id,
                        'product': line.product_id.display_name,
                        'started': line.first_stage_started,
                        'started_stage': line.first_stage_started_stage,
                    } for line in production.production_line_ids.filtered('active')]
                    raise RuntimeError(
                        'Carryover start failed for %s/%s: %s; rows=%s lines=%s'
                        % (production.name, stage_code, error, rows, lines)
                    )
    stage_order.invalidate_recordset(['state'])
    if stage_order.state != 'in_progress':
        raise RuntimeError(
            '%s did not start for %s; state=%s.'
            % (stage_code, production.name, stage_order.state)
        )
    events.append('start:%s:%s' % (stage_code, production.name))
    return stage_order


def complete_substages(stage_order):
    if stage_order._name == 'furniture.mrp.bases':
        for code in ('preparation', 'foam'):
            stage_order.with_context(
                bases_internal_substage=code,
            ).action_complete_internal_substage()
    elif stage_order._name == 'furniture.mrp.tailoring':
        for code in ('cutting', 'sewing', 'ironing'):
            stage_order.with_context(
                tailoring_internal_substage=code,
            ).action_complete_internal_substage()
    elif stage_order._name == 'furniture.mrp.painting':
        for code, _label, _state_field, _required_field in (
            stage_order._get_active_substage_fields()
        ):
            row = stage_order.with_context(
                painting_internal_substage=code,
                painting_external_substage=code,
            )
            if code in ('cells', 'veneer', 'paint'):
                row.action_external_substage_exit()
                row.action_external_substage_deliver()
                row.action_external_substage_receive()
            else:
                row.action_complete_internal_substage()


def complete_stage(production, stage_code):
    stage_order = start_stage(production, stage_code)
    complete_substages(stage_order)
    operator = production.with_user(stage_supervisor(stage_code).user_id)
    if stage_code in ('upholstery', 'packaging'):
        operator.action_stage_dashboard_finish_order_stage(stage_code)
    else:
        operator.action_stage_dashboard_finish_batch(stage_code)
    stage_order.invalidate_recordset(['state'])
    if stage_order.state != 'done':
        active = stage_order._get_stage_line_ids_data(
            'active_production_line_ids_data'
        ).ids
        completed = stage_order._get_stage_line_ids_data(
            'completed_production_line_ids_data'
        ).ids
        line_diagnostics = [{
            'id': line.id,
            'first_stage_started': line.first_stage_started,
            'first_stage_started_stage': line.first_stage_started_stage,
            'stage_done': production._production_line_stage_done(
                line, stage_code,
            ),
        } for line in production.production_line_ids.filtered('active')]
        pending_route = production._get_stage_incomplete_line_candidates(
            stage_code
        ).ids
        pending_start = production._get_stage_pending_start_line_candidates(
            stage_order, stage_code,
        ).ids
        remaining_work = [{
            'product': payload.get('product').display_name,
            'qty': payload.get('qty'),
            'source_line': payload.get('source_production_line').id,
        } for payload in stage_order._get_remaining_stage_work_payloads()]
        raise RuntimeError(
            '%s did not complete for %s; state=%s active=%s completed=%s '
            'lines=%s pending_route=%s pending_start=%s remaining=%s.'
            % (
                stage_code,
                production.name,
                stage_order.state,
                active,
                completed,
                line_diagnostics,
                pending_route,
                pending_start,
                remaining_work,
            )
        )
    events.append('done:%s:%s' % (stage_code, production.name))
    return stage_order


def run_coordinator():
    result = FinalRule._final_replenishment_run_company(
        company,
        restricted_rule_ids=rule.ids,
        lock=True,
        raise_on_error=True,
    )
    events.append('coordinator:%s' % ','.join(result.mapped('production_lane')))
    return result


def finish_packaging():
    packaging = active_lane('packaging').ensure_one()
    if packaging.state != 'confirmed':
        raise RuntimeError('Packaging was not auto-confirmed.')
    accept_lane_handoffs(packaging)
    complete_stage(packaging, 'packaging')
    packaging.invalidate_recordset(['state'])
    if packaging.state != 'done':
        raise RuntimeError('Packaging order did not close.')


initial_lanes = Production.search([
    ('company_id', '=', company.id),
    ('state', 'not in', ('done', 'cancelled')),
]).mapped('production_lane')

if case == 'case1':
    if initial_lanes != ['upholstery']:
        raise RuntimeError('Case1 initial lanes are wrong: %s' % initial_lanes)
    upholstery = active_lane('upholstery').ensure_one()
    accept_lane_handoffs(upholstery)
    complete_stage(upholstery, 'upholstery')
    finish_packaging()

elif case == 'case2':
    if initial_lanes != ['frame']:
        raise RuntimeError('Case2 initial lanes are wrong: %s' % initial_lanes)
    frame = active_lane('frame').ensure_one()
    complete_stage(frame, 'priming')
    accept_stage_handoffs(frame, 'carpentry')
    complete_stage(frame, 'carpentry')

    finish = active_lane('finish').ensure_one()
    if finish.state != 'confirmed':
        raise RuntimeError('Finish was not auto-confirmed.')
    accept_lane_handoffs(finish)
    complete_stage(finish, 'bases')
    accept_stage_handoffs(finish, 'finishing')
    complete_stage(finish, 'finishing')

    # This is exactly what the only enabled ten-minute coordinator cron does.
    run_coordinator()
    upholstery = active_lane('upholstery').ensure_one()
    if upholstery.state != 'confirmed':
        raise RuntimeError('Upholstery was not auto-confirmed.')
    accept_lane_handoffs(upholstery)
    complete_stage(upholstery, 'upholstery')
    finish_packaging()

else:
    if set(initial_lanes) != {'painting', 'upholstery'}:
        raise RuntimeError('Case3 initial lanes are wrong: %s' % initial_lanes)
    upholstery = active_lane('upholstery').ensure_one()
    accept_lane_handoffs(upholstery)
    complete_stage(upholstery, 'upholstery')
    if active_lane('packaging'):
        raise RuntimeError('Packaging appeared before painting was ready.')
    painting = active_lane('painting').ensure_one()
    complete_stage(painting, 'painting')
    finish_packaging()

candidates = FinalRule._final_replenishment_finished_product_candidates(rule)
finished_location = env.ref('furniture_mrp.location_finished_goods')
finished_qty = sum(env['stock.quant'].sudo().search([
    ('company_id', '=', company.id),
    ('product_id', 'in', candidates.ids),
    ('location_id', '=', finished_location.id),
]).mapped('quantity'))
if round(finished_qty, 3) != 3.0:
    raise RuntimeError('Expected 3 finished units, found %s.' % finished_qty)

env.cr.commit()
print(json.dumps({
    'database': database_name,
    'case': case,
    'initial_lanes': initial_lanes,
    'finished_qty': finished_qty,
    'active_lanes_after': Production.search([
        ('company_id', '=', company.id),
        ('state', 'not in', ('done', 'cancelled')),
    ]).mapped('production_lane'),
    'events': events,
}, ensure_ascii=False, indent=2))
