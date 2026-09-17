# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Keep historical direct-to-hall issues valid after adding handover."""
    cr.execute(
        """
        SELECT data.res_id
          FROM ir_model_data AS data
         WHERE data.module = 'furniture_mrp'
           AND data.name = 'location_material_handover'
           AND data.model = 'stock.location'
         LIMIT 1
        """
    )
    row = cr.fetchone()
    if not row:
        raise RuntimeError(
            'The production material handover location was not created.'
        )
    handover_location_id = row[0]

    cr.execute(
        """
        UPDATE furniture_mrp_advance_material_release_stage
           SET handover_location_id = %s
         WHERE handover_location_id IS NULL
        """,
        [handover_location_id],
    )

    # Existing issued stages were physically moved straight into their halls
    # by the old workflow.  Do not invent a second stock move; classify them as
    # audited legacy receipts and keep their historical move/FIFO links intact.
    cr.execute(
        """
        UPDATE furniture_mrp_advance_material_release_stage
           SET receipt_confirmed = TRUE,
               legacy_direct_receipt = TRUE,
               receipt_state = 'legacy',
               received_at = COALESCE(received_at, issued_at)
         WHERE state IN ('issued', 'started')
           AND COALESCE(receipt_confirmed, FALSE) = FALSE
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_advance_material_release_line AS line
           SET received_qty = line.issued_qty,
               receipt_difference_qty = 0
          FROM furniture_mrp_advance_material_release_stage AS stage
         WHERE line.release_stage_id = stage.id
           AND stage.legacy_direct_receipt = TRUE
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_material_line AS material
           SET warehouse_receipt_confirmed = TRUE,
               warehouse_received_qty = material.qty_needed,
               warehouse_receipt_stage_id = relation.release_stage_id
          FROM furn_adv_rel_stage_material_rel AS relation
          JOIN furniture_mrp_advance_material_release_stage AS stage
            ON stage.id = relation.release_stage_id
         WHERE relation.material_line_id = material.id
           AND stage.legacy_direct_receipt = TRUE
        """
    )
