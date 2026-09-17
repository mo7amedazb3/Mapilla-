# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Default painting to all six stages and remove sequencing between them."""
    cr.execute(
        """
        UPDATE furniture_mrp_painting
           SET substage_plan = 'custom',
               substage_priming_required = TRUE,
               substage_assembly_required = TRUE,
               substage_cells_required = TRUE,
               substage_veneer_required = TRUE,
               substage_impregnation_required = TRUE,
               substage_paint_required = TRUE
         WHERE state = 'pending'
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_painting
           SET substage_priming_state = CASE
                   WHEN substage_priming_required AND substage_priming_state = 'pending'
                   THEN 'in_progress' ELSE substage_priming_state END,
               substage_assembly_state = CASE
                   WHEN substage_assembly_required AND substage_assembly_state = 'pending'
                   THEN 'in_progress' ELSE substage_assembly_state END,
               substage_cells_state = CASE
                   WHEN substage_cells_required AND substage_cells_state = 'pending'
                   THEN 'in_progress' ELSE substage_cells_state END,
               substage_veneer_state = CASE
                   WHEN substage_veneer_required AND substage_veneer_state = 'pending'
                   THEN 'in_progress' ELSE substage_veneer_state END,
               substage_impregnation_state = CASE
                   WHEN substage_impregnation_required AND substage_impregnation_state = 'pending'
                   THEN 'in_progress' ELSE substage_impregnation_state END,
               substage_paint_state = CASE
                   WHEN substage_paint_required AND substage_paint_state = 'pending'
                   THEN 'in_progress' ELSE substage_paint_state END
         WHERE state = 'in_progress'
        """
    )
