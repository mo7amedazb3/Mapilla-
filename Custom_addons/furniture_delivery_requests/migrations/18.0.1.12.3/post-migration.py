def migrate(cr, version):
    """Keep every company that was visible in the old directory selected."""
    cr.execute(
        """
        UPDATE res_partner
           SET is_delivery_standard_company = TRUE
         WHERE is_company = TRUE
           AND is_delivery_standard_company IS NOT TRUE
        """
    )
