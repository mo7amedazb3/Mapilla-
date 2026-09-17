def migrate(cr, version):
    """Preserve old per-line customer data on the new order-level fields."""
    cr.execute(
        """
        UPDATE furniture_mrp_production AS production
           SET buyer_partner_id = source.buyer_partner_id
          FROM (
                SELECT DISTINCT ON (production_id)
                       production_id, buyer_partner_id
                  FROM furniture_mrp_production_line
                 WHERE active IS TRUE
                   AND buyer_partner_id IS NOT NULL
                 ORDER BY production_id, sequence, id
               ) AS source
         WHERE production.id = source.production_id
           AND production.buyer_partner_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_production AS production
           SET beneficiary_partner_id = source.beneficiary_partner_id
          FROM (
                SELECT DISTINCT ON (production_id)
                       production_id, beneficiary_partner_id
                  FROM furniture_mrp_production_line
                 WHERE active IS TRUE
                   AND beneficiary_partner_id IS NOT NULL
                 ORDER BY production_id, sequence, id
               ) AS source
         WHERE production.id = source.production_id
           AND production.beneficiary_partner_id IS NULL
        """
    )
