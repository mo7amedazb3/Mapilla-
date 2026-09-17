def migrate(cr, version):
    """Make the optional blank value the only neutral material classification."""
    cr.execute(
        """
        UPDATE product_template
           SET furniture_tailoring_material_kind = NULL
         WHERE furniture_tailoring_material_kind = 'other'
        """
    )
    cr.execute(
        """
        UPDATE product_product
           SET furniture_tailoring_material_kind = NULL
         WHERE furniture_tailoring_material_kind = 'other'
        """
    )
