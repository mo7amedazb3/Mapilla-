# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Migrate historical single-stage requests to audited legacy receipt."""
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
            'The production material handover location is missing.'
        )
    handover_location_id = row[0]

    cr.execute(
        """
        UPDATE furniture_mrp_store_request
           SET handover_location_id = %s
         WHERE handover_location_id IS NULL
        """,
        [handover_location_id],
    )

    # Requests approved before this version moved materials only when
    # production clicked Start.  They have no custody moves to receive, so do
    # not block them or manufacture a false stock history.
    cr.execute(
        """
        UPDATE furniture_mrp_store_request
           SET receipt_confirmed = TRUE,
               receipt_state = 'legacy',
               received_by_id = COALESCE(received_by_id, approved_by_id),
               received_at = COALESCE(received_at, started_at, approved_at)
         WHERE state IN ('approved', 'started')
           AND COALESCE(receipt_confirmed, FALSE) = FALSE
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_store_request_line AS line
           SET issued_qty = line.requested_qty,
               received_qty = line.requested_qty,
               receipt_difference_qty = 0
          FROM furniture_mrp_store_request AS request
         WHERE line.request_id = request.id
           AND request.receipt_state = 'legacy'
        """
    )
