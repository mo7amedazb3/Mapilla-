# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools.float_utils import float_is_zero


_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = %s
           AND column_name = %s
        """,
        [table, column],
    )
    return bool(cr.fetchone())


def _merge_legacy_sewing_flags(cr, table, tailoring_field='use_tailoring', sewing_field='use_sewing'):
    if not (
        _column_exists(cr, table, tailoring_field)
        and _column_exists(cr, table, sewing_field)
    ):
        return
    cr.execute(
        "UPDATE %s "
        "SET %s = COALESCE(%s, FALSE) OR COALESCE(%s, FALSE), "
        "%s = FALSE "
        "WHERE COALESCE(%s, FALSE)" % (
            table,
            tailoring_field,
            tailoring_field,
            sewing_field,
            sewing_field,
            sewing_field,
        )
    )


def _move_employee_stage_relations(cr, sewing_stage_id, tailoring_stage_id):
    for table in (
        'furniture_mrp_employee_worker_stage_rel',
        'furniture_mrp_employee_supervisor_stage_rel',
    ):
        cr.execute(
            "INSERT INTO %s (employee_id, stage_id) "
            "SELECT employee_id, %%s FROM %s WHERE stage_id = %%s "
            "ON CONFLICT DO NOTHING" % (table, table),
            [tailoring_stage_id, sewing_stage_id],
        )
        cr.execute(
            "DELETE FROM %s WHERE stage_id = %%s" % table,
            [sewing_stage_id],
        )


def _merge_bom_stage_headers(env):
    """Fold recipe sewing sections into one tailoring section per BoM.

    A raw ``sewing -> tailoring`` update can leave two tailoring headers on
    the same recipe.  Move every material row to one canonical header first,
    then remove only the redundant configuration headers.  Quantities are
    deliberately left untouched.
    """
    Stage = env['furniture.mrp.bom.stage'].sudo()
    StageLine = env['furniture.mrp.bom.stage.line'].sudo()
    headers = Stage.search([
        ('stage', 'in', ('tailoring', 'sewing')),
    ])
    for bom in headers.mapped('bom_id'):
        bom_headers = headers.filtered(lambda header: header.bom_id == bom)
        tailoring_headers = bom_headers.filtered(
            lambda header: header.stage == 'tailoring'
        )
        canonical = tailoring_headers[:1] or bom_headers[:1]
        if not canonical:
            continue
        canonical.write({'stage': 'tailoring'})
        lines = StageLine.search([
            '|',
            ('stage_id', 'in', bom_headers.ids),
            '&',
            ('bom_id', '=', bom.id),
            ('stage', '=', 'sewing'),
        ])
        if lines:
            lines.write({
                'stage_id': canonical.id,
                'bom_id': bom.id,
                'stage': 'tailoring',
            })
        redundant = bom_headers - canonical
        if redundant:
            redundant.unlink()


def _archive_empty_location(location, archived_name):
    if not location:
        return
    quants = location.env['stock.quant'].sudo().with_context(
        active_test=False,
    ).search([('location_id', '=', location.id)])
    has_stock = any(
        not float_is_zero(
            (quant.quantity or 0.0),
            precision_rounding=quant.product_id.uom_id.rounding,
        )
        or not float_is_zero(
            (quant.reserved_quantity or 0.0),
            precision_rounding=quant.product_id.uom_id.rounding,
        )
        for quant in quants
    )
    open_moves = location.env['stock.move'].sudo().search_count([
        '|',
        ('location_id', '=', location.id),
        ('location_dest_id', '=', location.id),
        ('state', 'not in', ('done', 'cancel')),
    ])
    open_move_lines = location.env['stock.move.line'].sudo().search_count([
        '|',
        ('location_id', '=', location.id),
        ('location_dest_id', '=', location.id),
        ('move_id.state', 'not in', ('done', 'cancel')),
    ])
    if has_stock or open_moves or open_move_lines:
        raise RuntimeError(
            'Cannot archive legacy sewing location %s: stock, reservations '
            'or open stock operations still exist.' % location.display_name
        )
    location.sudo().write({
        'name': archived_name,
        'active': False,
    })


def _archive_unused_workcenter(workcenter, archived_name):
    if not workcenter:
        return
    open_workorders = workcenter.env['mrp.workorder'].sudo().search_count([
        ('workcenter_id', '=', workcenter.id),
        ('state', 'not in', ('done', 'cancel')),
    ])
    if open_workorders:
        raise RuntimeError(
            'Cannot archive legacy sewing work center %s: open work orders '
            'still exist.' % workcenter.display_name
        )
    workcenter.sudo().write({
        'name': archived_name,
        'active': False,
    })


def _remove_legacy_sewing_ui(env):
    menu = env.ref(
        'furniture_mrp.menu_furniture_mrp_sewing',
        raise_if_not_found=False,
    )
    if menu:
        menu.sudo().unlink()

    # The model/table remains registered so historical chatter, worker logs,
    # quality decisions and cost entries never lose their references.
    for xmlid in (
        'furniture_mrp.action_furniture_mrp_sewing',
        'furniture_mrp.action_report_furniture_mrp_sewing',
        'furniture_mrp.view_furniture_mrp_sewing_form',
        'furniture_mrp.view_furniture_mrp_sewing_list',
        'furniture_mrp.report_sewing_template',
    ):
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.sudo().unlink()


def migrate(cr, version):
    """Fold standalone sewing into tailoring without deleting audit history."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    for table in (
        'mrp_bom',
        'furniture_mrp_production',
        'furniture_mrp_production_line',
    ):
        _merge_legacy_sewing_flags(cr, table)
    _merge_legacy_sewing_flags(
        cr,
        'furniture_mrp_production',
        tailoring_field='line_use_tailoring',
        sewing_field='line_use_sewing',
    )
    for model_name, field_names in (
        ('mrp.bom', ['use_tailoring', 'use_sewing']),
        (
            'furniture.mrp.production',
            [
                'use_tailoring', 'use_sewing',
                'line_use_tailoring', 'line_use_sewing',
            ],
        ),
        (
            'furniture.mrp.production.line',
            ['use_tailoring', 'use_sewing'],
        ),
    ):
        model = env[model_name]
        model.invalidate_model(field_names)
        model.sudo().with_context(active_test=False).search([]).modified(
            field_names,
        )

    # Only operational route/material fields are folded.  Warehouse releases,
    # store requests and approved cost entries deliberately retain stage code
    # ``sewing`` as immutable historical evidence.
    _merge_bom_stage_headers(env)
    for table, column in (
        ('furniture_mrp_bom_stage_line', 'stage'),
        ('mrp_bom_line', 'furniture_stage'),
        ('furniture_mrp_material_line', 'stage'),
        ('furniture_mrp_production_line', 'planned_start_stage'),
        ('furniture_mrp_production_line', 'first_stage_started_stage'),
    ):
        if _column_exists(cr, table, column):
            cr.execute(
                "UPDATE %s SET %s = 'tailoring' WHERE %s = 'sewing'" % (
                    table,
                    column,
                    column,
                )
            )
    cr.execute(
        "UPDATE furniture_mrp_production "
        "SET state = 'tailoring' WHERE state = 'sewing'"
    )
    if _column_exists(cr, 'furniture_mrp_mps_capacity', 'department'):
        cr.execute(
            "DELETE FROM furniture_mrp_mps_capacity "
            "WHERE department = 'sewing'"
        )

    # Existing completed/quality orders must stay completed; running orders
    # resume every newly introduced internal phase together.
    cr.execute(
        """
        UPDATE furniture_mrp_bases
           SET substage_preparation_state = CASE
                   WHEN state IN ('quality_check', 'done') THEN 'done'
                   WHEN state = 'in_progress' THEN 'in_progress'
                   ELSE 'pending'
               END,
               substage_foam_state = CASE
                   WHEN state IN ('quality_check', 'done') THEN 'done'
                   WHEN state = 'in_progress' THEN 'in_progress'
                   ELSE 'pending'
               END
         WHERE COALESCE(substage_preparation_state, 'pending') = 'pending'
           AND COALESCE(substage_foam_state, 'pending') = 'pending'
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_tailoring AS tailoring
           SET substage_cutting_state = CASE
                   WHEN tailoring.state IN ('quality_check', 'done') THEN 'done'
                   WHEN tailoring.state = 'in_progress' THEN 'in_progress'
                   ELSE 'pending'
               END,
               substage_ironing_state = CASE
                   WHEN tailoring.state IN ('quality_check', 'done') THEN 'done'
                   WHEN tailoring.state = 'in_progress' THEN 'in_progress'
                   ELSE 'pending'
               END,
               substage_sewing_state = CASE
                   WHEN tailoring.state IN ('quality_check', 'done') THEN 'done'
                   WHEN EXISTS (
                       SELECT 1
                         FROM furniture_mrp_sewing AS legacy_sewing
                        WHERE legacy_sewing.production_order_id = tailoring.production_order_id
                          AND legacy_sewing.state IN ('quality_check', 'done')
                   ) THEN 'done'
                   WHEN tailoring.state = 'in_progress' OR EXISTS (
                       SELECT 1
                         FROM furniture_mrp_sewing AS legacy_sewing
                        WHERE legacy_sewing.production_order_id = tailoring.production_order_id
                          AND legacy_sewing.state = 'in_progress'
                   ) THEN 'in_progress'
                   ELSE 'pending'
               END
         WHERE COALESCE(tailoring.substage_cutting_state, 'pending') = 'pending'
           AND COALESCE(tailoring.substage_sewing_state, 'pending') = 'pending'
           AND COALESCE(tailoring.substage_ironing_state, 'pending') = 'pending'
        """
    )

    tailoring_employee_stage = env.ref(
        'furniture_mrp.employee_stage_tailoring',
        raise_if_not_found=False,
    )
    sewing_employee_stage = env.ref(
        'furniture_mrp.employee_stage_sewing',
        raise_if_not_found=False,
    )
    if tailoring_employee_stage and sewing_employee_stage:
        _move_employee_stage_relations(
            cr,
            sewing_employee_stage.id,
            tailoring_employee_stage.id,
        )
        sewing_employee_stage.sudo().write({
            'name': 'الخياطة (مرحلة قديمة داخل التفصيل الآن)',
            'active': False,
        })

    _archive_empty_location(
        env.ref(
            'furniture_mrp.location_stage_sewing',
            raise_if_not_found=False,
        ),
        'مخزن مرحلة الخياطة (مؤرشف - داخل التفصيل)',
    )
    _archive_empty_location(
        env.ref(
            'furniture_mrp.location_stage_sewing_wip',
            raise_if_not_found=False,
        ),
        'صالة تصنيع الخياطة (مؤرشفة - داخل التفصيل)',
    )
    _archive_unused_workcenter(
        env.ref(
            'furniture_mrp.workcenter_stitching',
            raise_if_not_found=False,
        ),
        'قسم الخياطة (مؤرشف - داخل التفصيل)',
    )

    _remove_legacy_sewing_ui(env)

    env.invalidate_all()
    _logger.info(
        'Standalone sewing was folded into tailoring; legacy operational '
        'records were preserved and standalone resources were archived.'
    )
