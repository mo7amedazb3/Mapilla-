# -*- coding: utf-8 -*-
"""Prepare one isolated Big Moon chaise Min/Max demonstration database.

Run from an Odoo shell with ``FURNITURE_SCENARIO`` set to the exact target
database name.  Only test1/2/3 and meeting1/2/3 are accepted.  Each meeting
database mirrors the scenario with the same number, and every other database
(especially yasser3) is rejected before any write happens.
"""

import json
import os
import uuid

from datetime import timedelta

from odoo import fields


SCENARIO_PROFILES = {
    'case1': {
        'ready_lanes': ('finish', 'tailoring', 'painting'),
    },
    'case2': {
        'ready_lanes': ('tailoring', 'painting'),
    },
    'case3': {
        'ready_lanes': ('finish', 'tailoring'),
    },
}

PROFILE_BY_DATABASE = {
    'test1': 'case1',
    'meeting1': 'case1',
    'test2': 'case2',
    'meeting2': 'case2',
    'test3': 'case3',
    'meeting3': 'case3',
}

LANE_ROUTES = {
    'frame': ('priming', 'carpentry'),
    'finish': ('bases', 'finishing'),
    'tailoring': ('tailoring',),
    'painting': ('painting',),
}

READY_STAGE_BY_LANE = {
    'frame': 'carpentry',
    'finish': 'finishing',
    'tailoring': 'tailoring',
    'painting': 'painting',
}

STAGE_CODES = (
    'priming',
    'painting',
    'carpentry',
    'bases',
    'finishing',
    'tailoring',
    'upholstery',
    'packaging',
)


database_name = os.environ.get('FURNITURE_SCENARIO', '').strip()
if database_name not in PROFILE_BY_DATABASE:
    raise RuntimeError(
        'FURNITURE_SCENARIO must be test1/2/3 or meeting1/2/3.'
    )
if env.cr.dbname != database_name:
    raise RuntimeError(
        'Refusing database %s: scenario setup may run only on %s.'
        % (env.cr.dbname, database_name)
    )
scenario = PROFILE_BY_DATABASE[database_name]

company = env['res.company'].sudo().browse(1).exists()
model = env['furniture.product.model'].sudo().browse(6).exists()
product = env['product.product'].sudo().browse(77).exists()
if not company or not model or not product:
    raise RuntimeError('The copied yasser3 Big Moon identifiers are missing.')
if 'بيج مون' not in model.display_name or 'شازلونج' not in product.display_name:
    raise RuntimeError(
        'Identifier validation failed: model=%s, product=%s.'
        % (model.display_name, product.display_name)
    )

FinalRule = env['furniture.mrp.final.replenishment.rule'].sudo().with_company(
    company
)
StageRule = env['furniture.mrp.stage.replenishment.rule'].sudo().with_company(
    company
)
Production = env['furniture.mrp.production'].sudo().with_company(company)
Line = env['furniture.mrp.production.line'].sudo().with_company(company)
Output = env['furniture.mrp.lane.output'].sudo().with_company(company)

# Freeze every background integration before preparing the fixture.  The
# Min/Max coordinator is re-enabled at the end, while MPS remains disabled.
# The temporary auto-confirm window applies only to these six disposable
# demonstration databases and makes every system-generated order immediately
# confirmed for the next two days.
parameters = env['ir.config_parameter'].sudo()
parameters.set_param('database.uuid', str(uuid.uuid4()))
parameters.set_param('database.secret', str(uuid.uuid4()))
parameters.set_param('database.is_neutralized', 'true')
parameters.set_param('furniture_mrp.mps_enabled', 'False')
parameters.set_param(
    'furniture_stage_replenishment.auto_confirm_enabled',
    'True',
)
parameters.set_param(
    'furniture_stage_replenishment.auto_confirm_until',
    fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=2)),
)
env['ir.cron'].sudo().search([]).write({'active': False})
env['ir.mail_server'].sudo().search([]).write({'active': False})

# The source database can contain unfinished draft replenishment orders for
# unrelated products.  They must never be reused by a scenario run, otherwise
# the generated order could mix the three target chaise units with sofa and
# armchair lines even though those Min/Max limits were reset to zero.
inherited_open_productions = Production.search([
    ('company_id', '=', company.id),
    ('state', 'not in', ('done', 'cancelled')),
])
if inherited_open_productions:
    inherited_open_productions.action_cancel()

# Resynchronise identities after cloning, then select the exact canonical rule.
FinalRule._final_replenishment_sync_company(company)

# Start every scenario with an empty Min/Max policy.  The single exception is
# configured below: finished Big Moon chaise, Min 0 / Max 3.
all_final_rules = FinalRule.search([('company_id', '=', company.id)])
if all_final_rules:
    all_final_rules._final_replenishment_internal_write({
        'min_qty': 0.0,
        'max_qty': 0.0,
        'last_generated_at': False,
        'last_body_production_id': False,
        'last_cover_production_id': False,
        'last_frame_production_id': False,
        'replenishment_cycle_active': False,
    })
all_stage_rules = StageRule.search([('company_id', '=', company.id)])
if all_stage_rules:
    all_stage_rules._stage_replenishment_internal_write({
        'min_qty': 0.0,
        'max_qty': 0.0,
        'last_generated_at': False,
        'last_production_id': False,
    })

rule = FinalRule.search([
    ('company_id', '=', company.id),
    ('product_id', '=', product.id),
    ('furniture_model_id', '=', model.id),
], limit=1)
if not rule:
    raise RuntimeError('Big Moon chaise final Min/Max rule was not found.')
bom = rule.bom_id
candidates = FinalRule._final_replenishment_finished_product_candidates(rule)
candidate_ids = candidates.ids

# Remove only unfinished Big Moon chaise coverage inherited from yasser3.
# The shared multi-product orders and every unrelated product line stay intact.
old_lines = Line.search([
    ('active', '=', True),
    ('production_id.company_id', '=', company.id),
    ('production_id.state', 'not in', ('done', 'cancelled')),
    ('furniture_order_model_id', '=', model.id),
    ('bom_id', '=', bom.id),
    ('product_id', 'in', candidate_ids),
])
linked_outputs = Output.search([
    ('production_line_id', 'in', old_lines.ids),
]) if old_lines else Output
if linked_outputs:
    raise RuntimeError(
        'Inherited target lines already have lane outputs; refusing unsafe reset.'
    )
old_productions = old_lines.mapped('production_id')
if old_lines:
    old_lines.with_context(
        furniture_skip_line_consolidation=True,
        furniture_skip_material_refresh=True,
        furniture_skip_stage_plan_sync=True,
    ).write({'active': False})
for production in old_productions:
    if rule in production.final_replenishment_rule_ids:
        production._final_replenishment_internal_write({
            'final_replenishment_rule_ids': [(
                6,
                0,
                (production.final_replenishment_rule_ids - rule).ids,
            )],
        })

# Clear only stale audit pointers for this exact identity.
stage_rules = StageRule.search([
    ('company_id', '=', company.id),
    ('product_id', '=', product.id),
    ('furniture_model_id', '=', model.id),
])
for stage_rule in stage_rules:
    if stage_rule.last_production_id in old_productions:
        stage_rule._stage_replenishment_internal_write({
            'last_generated_at': False,
            'last_production_id': False,
        })
rule._final_replenishment_internal_write({
    'min_qty': 0.0,
    'max_qty': 3.0,
    'last_generated_at': False,
    'last_body_production_id': False,
    'last_cover_production_id': False,
    'last_frame_production_id': False,
    'replenishment_cycle_active': False,
})

# The finished-goods condition is exactly zero for this canonical product and
# all its dimensioned variants.  Use the stock API, never direct quant SQL.
finished_location = env.ref('furniture_mrp.location_finished_goods')
for candidate in candidates:
    quants = env['stock.quant'].sudo().search([
        ('product_id', '=', candidate.id),
        ('location_id', '=', finished_location.id),
    ])
    on_hand = sum(quants.mapped('quantity'))
    if on_hand:
        env['stock.quant'].sudo()._update_available_quantity(
            candidate,
            finished_location,
            -on_hand,
        )


def create_ready_output(lane, quantity=3.0):
    selected_stages = set(LANE_ROUTES[lane])
    stage_values = {
        'use_%s' % stage: stage in selected_stages
        for stage in STAGE_CODES
    }
    dimensions = {
        'width_cm': bom.furniture_width_cm,
        'depth_cm': bom.furniture_depth_cm,
        'height_cm': bom.furniture_height_cm,
    }
    production = Production.with_context(
        furniture_skip_material_refresh=True,
        furniture_skip_stage_plan_sync=True,
        furniture_skip_line_consolidation=True,
    ).create({
        'name': 'OPENING/%s/%s' % (database_name.upper(), lane.upper()),
        'company_id': company.id,
        'product_id': product.id,
        'furniture_order_model_id': model.id,
        'product_qty': quantity,
        'bom_id': bom.id,
        'production_lane': lane,
        'stage_plan_mode': 'custom',
        'state': 'confirmed',
        'notes': 'رصيد افتتاحي مكتمل للعرض %s — مسار %s.' % (
            database_name,
            lane,
        ),
        **dimensions,
        **stage_values,
    })
    line = Line.with_context(
        furniture_preserve_explicit_bom=True,
        furniture_skip_line_consolidation=True,
        furniture_skip_material_refresh=True,
        furniture_skip_stage_plan_sync=True,
        furniture_skip_running_line_initialization=True,
    ).create({
        'production_id': production.id,
        'sequence': 10,
        'product_id': product.id,
        'furniture_order_model_id': model.id,
        'product_qty': quantity,
        'bom_id': bom.id,
        'stage_selection_initialized': True,
        **dimensions,
        **stage_values,
    })
    final_product = production._furniture_line_final_product(line)
    wip_product = env[
        'product.product'
    ]._furniture_get_or_create_lane_wip_product(
        company,
        lane,
        final_product,
        model,
    )
    ready_location = production._stage_storage_location(
        READY_STAGE_BY_LANE[lane]
    )
    ready_move = production._create_internal_move(
        production._get_production_location(),
        ready_location,
        'Opening %s ready %s output' % (database_name, lane),
        move_type='finished_product',
        product=wip_product,
        quantity=quantity,
        uom=wip_product.uom_id,
        source_production_line=line,
        price_unit=10.0,
    )
    output = production._ensure_lane_outputs(line).ensure_one()
    if output.ready_move_id != ready_move:
        raise RuntimeError('Ready output move mismatch for lane %s.' % lane)
    output._furniture_refresh_matching_groups()
    # This record is the auditable origin of physical opening stock, not work
    # awaiting a supervisor.  Closing it keeps the production board clean
    # while the FIFO lane output remains available for downstream reservation.
    production._stage_replenishment_internal_write({
        'state': 'done',
        'date_finish': fields.Datetime.now(),
    })
    return output


ready_outputs = Output
for lane in SCENARIO_PROFILES[scenario]['ready_lanes']:
    ready_outputs |= create_ready_output(lane)

# Run the real finished-goods coordinator once.  It must use the physical FIFO
# buffers above, so it creates no order for a lane whose opening stock already
# covers the three-unit demand.  Finish remains strictly downstream of frame.
ready_outputs._furniture_refresh_matching_groups()
generated = FinalRule._final_replenishment_run_company(
    company,
    restricted_rule_ids=rule.ids,
    lock=True,
    raise_on_error=True,
)
created_upholstery = generated.filtered(
    lambda production: production.production_lane == 'upholstery'
)

# Generated orders in these demo databases must already be confirmed.  The
# runtime auto-confirm hook also applies to follow-up orders during the next
# two days; this assertion catches any fixture/configuration regression now.
active_orders = Production.search([
    ('company_id', '=', company.id),
    ('state', 'not in', ('done', 'cancelled')),
])
if active_orders.filtered(lambda production: production.state != 'confirmed'):
    raise RuntimeError('A generated scenario order was not auto-confirmed.')

# Keep external jobs disabled, but leave only the MRP Min/Max coordinator
# scheduled.  MPS itself stays disabled independently.
replenishment_cron = env.ref(
    'furniture_stage_replenishment.ir_cron_furniture_mrp_stage_replenishment',
    raise_if_not_found=False,
)
if replenishment_cron:
    replenishment_cron.write({
        'active': True,
        'nextcall': fields.Datetime.now() + timedelta(minutes=10),
    })

# Validate the prepared state using the same coordinator metrics used by UI.
rule.invalidate_recordset()
metrics = FinalRule._final_replenishment_metric_map(rule)[rule.id]
lane_metrics = {
    lane: {
        'ready': round(metrics[lane]['wip_qty'], 3),
        'draft': round(metrics[lane]['draft_qty'], 3),
        'to_produce': round(metrics[lane]['qty_to_produce'], 3),
    }
    for lane in ('frame', 'finish', 'tailoring', 'painting')
}
expected = {
    'case1': {
        'open_flow': 3.0,
        'active_lanes': {'upholstery': 3.0},
        'to_produce': {
            'frame': 0.0,
            'finish': 0.0,
            'tailoring': 0.0,
            'painting': 0.0,
        },
    },
    'case2': {
        'open_flow': 0.0,
        'active_lanes': {'frame': 3.0},
        'to_produce': {
            'frame': 0.0,
            'finish': 3.0,
            'tailoring': 0.0,
            'painting': 0.0,
        },
    },
    'case3': {
        'open_flow': 3.0,
        'active_lanes': {'painting': 3.0, 'upholstery': 3.0},
        'to_produce': {
            'frame': 0.0,
            'finish': 0.0,
            'tailoring': 0.0,
            'painting': 0.0,
        },
    },
}[scenario]
if round(metrics['finished_qty'], 3) != 0.0:
    raise RuntimeError('Finished stock is not zero.')
if round(metrics['open_flow_qty'], 3) != expected['open_flow']:
    raise RuntimeError('Unexpected open upholstery/packaging flow quantity.')
actual_to_produce = {
    lane: lane_metrics[lane]['to_produce'] for lane in lane_metrics
}
if actual_to_produce != expected['to_produce']:
    raise RuntimeError(
        'Unexpected coordinator result: %s != %s'
        % (actual_to_produce, expected['to_produce'])
    )

actual_active_lanes = {}
for production in active_orders:
    quantity = sum(
        line.product_qty
        for line in production.production_line_ids.filtered('active')
    ) or production.product_qty
    actual_active_lanes[production.production_lane] = round(
        actual_active_lanes.get(production.production_lane, 0.0) + quantity,
        3,
    )
if actual_active_lanes != expected['active_lanes']:
    raise RuntimeError(
        'Unexpected active scenario orders: %s != %s'
        % (actual_active_lanes, expected['active_lanes'])
    )

env.cr.commit()
print(json.dumps({
    'database': env.cr.dbname,
    'scenario': scenario,
    'model': model.display_name,
    'product': product.display_name,
    'final_rule_id': rule.id,
    'final_min': rule.min_qty,
    'final_max': rule.max_qty,
    'finished_qty': metrics['finished_qty'],
    'open_flow_qty': metrics['open_flow_qty'],
    'ready_lanes': list(SCENARIO_PROFILES[scenario]['ready_lanes']),
    'active_lanes': actual_active_lanes,
    'upholstery_order': created_upholstery.mapped('display_name') or False,
    'lane_metrics': lane_metrics,
    'deactivated_inherited_lines': old_lines.ids,
}, ensure_ascii=False, indent=2))
