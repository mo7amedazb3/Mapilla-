# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Separate pre-production Kit families from real cost inheritance.

    Only untouched Kit plans are migrated automatically.  Started historical
    orders keep their original lineage for audit and can be repaired through a
    dedicated reviewed migration if needed.
    """
    cr.execute(
        """
        WITH untouched_kit_orders AS (
            SELECT production.id
              FROM furniture_mrp_production AS production
             WHERE production.kit_plan_locked = TRUE
               AND NOT EXISTS (
                    SELECT 1
                      FROM furniture_mrp_production_line AS started_line
                     WHERE started_line.production_id = production.id
                       AND started_line.first_stage_started = TRUE
               )
        )
        UPDATE furniture_mrp_production_line AS piece
           SET kit_family_origin_line_id = piece.cost_origin_line_id,
               cost_origin_line_id = NULL
         WHERE piece.production_id IN (SELECT id FROM untouched_kit_orders)
           AND piece.cost_origin_line_id IS NOT NULL
           AND piece.kit_family_origin_line_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_production_line AS family_root
           SET kit_family_origin_line_id = family_root.id
         WHERE family_root.id IN (
                SELECT DISTINCT piece.kit_family_origin_line_id
                  FROM furniture_mrp_production_line AS piece
                 WHERE piece.kit_family_origin_line_id IS NOT NULL
           )
           AND family_root.kit_family_origin_line_id IS NULL
        """
    )
