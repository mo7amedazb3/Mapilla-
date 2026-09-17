def migrate(cr, version):
    """Backfill every order-level common field from its earliest active line."""
    for field_name in (
        'furniture_order_model_id',
        'buyer_partner_id',
        'beneficiary_partner_id',
    ):
        cr.execute(
            f"""
            UPDATE furniture_mrp_production AS production
               SET {field_name} = source.{field_name}
              FROM (
                    SELECT DISTINCT ON (production_id)
                           production_id, {field_name}
                      FROM furniture_mrp_production_line
                     WHERE active IS TRUE
                       AND {field_name} IS NOT NULL
                     ORDER BY production_id, sequence, id
                   ) AS source
             WHERE production.id = source.production_id
               AND production.{field_name} IS NULL
            """
        )
