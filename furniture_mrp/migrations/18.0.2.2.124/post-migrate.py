# -*- coding: utf-8 -*-

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Enable the two new operational stages on existing recipes and orders."""
    cr.execute(
        """
        UPDATE mrp_bom
           SET use_bases = TRUE,
               use_sewing = TRUE
         WHERE type = 'normal'
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_production_line
           SET use_bases = TRUE,
               use_sewing = TRUE
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_production
           SET use_bases = TRUE,
               use_sewing = TRUE
        """
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    stage_sequences = {
        'priming': 10,
        'painting': 20,
        'carpentry': 30,
        'bases': 40,
        'finishing': 50,
        'tailoring': 60,
        'sewing': 70,
        'upholstery': 80,
        'packaging': 90,
    }
    for stage in env['furniture.mrp.employee.stage'].search([]):
        if stage.code in stage_sequences:
            stage.sequence = stage_sequences[stage.code]

    env['furniture.mrp.production']._setup_stage_location_hierarchy()
    location_field_xmlids = {
        'location_bases_id': 'furniture_mrp.location_stage_bases',
        'location_bases_wip_id': 'furniture_mrp.location_stage_bases_wip',
        'location_sewing_id': 'furniture_mrp.location_stage_sewing',
        'location_sewing_wip_id': 'furniture_mrp.location_stage_sewing_wip',
    }
    location_values = {
        field_name: env.ref(xmlid).id
        for field_name, xmlid in location_field_xmlids.items()
    }
    env['furniture.mrp.production'].search([]).write(location_values)

    Capacity = env['furniture.mrp.mps.capacity']
    for mps in env['furniture.mrp.mps'].search([]):
        existing = set(mps.capacity_line_ids.mapped('department'))
        for department in ('bases', 'sewing'):
            if department not in existing:
                Capacity.create({
                    'mps_id': mps.id,
                    'department': department,
                    'planned_orders': 10,
                })
