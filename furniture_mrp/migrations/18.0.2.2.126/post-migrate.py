# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Switch painting to explicit stage selection and initialize outside flow."""
    cr.execute(
        """
        UPDATE furniture_mrp_painting
           SET substage_plan = 'custom',
               substage_priming_required = FALSE,
               substage_assembly_required = FALSE,
               substage_cells_required = FALSE,
               substage_veneer_required = FALSE,
               substage_impregnation_required = FALSE,
               substage_paint_required = FALSE,
               substage_cells_external_state = 'pending',
               substage_veneer_external_state = 'pending',
               substage_paint_external_state = 'pending'
         WHERE state = 'pending'
           AND substage_plan = 'all'
           AND substage_priming_state = 'pending'
           AND substage_assembly_state = 'pending'
           AND substage_cells_state = 'pending'
           AND substage_veneer_state = 'pending'
           AND substage_impregnation_state = 'pending'
           AND substage_paint_state = 'pending'
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_painting
           SET substage_plan = 'custom',
               substage_cells_external_state = CASE
                   WHEN substage_cells_state = 'done' OR state = 'done'
                   THEN 'received'
                   ELSE COALESCE(substage_cells_external_state, 'pending')
               END,
               substage_veneer_external_state = CASE
                   WHEN substage_veneer_state = 'done' OR state = 'done'
                   THEN 'received'
                   ELSE COALESCE(substage_veneer_external_state, 'pending')
               END,
               substage_paint_external_state = CASE
                   WHEN substage_paint_state = 'done' OR state = 'done'
                   THEN 'received'
                   ELSE COALESCE(substage_paint_external_state, 'pending')
               END
         WHERE substage_plan = 'all'
            OR substage_cells_external_state IS NULL
            OR substage_veneer_external_state IS NULL
            OR substage_paint_external_state IS NULL
        """
    )
