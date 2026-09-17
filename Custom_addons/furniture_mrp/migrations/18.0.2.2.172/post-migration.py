def migrate(cr, version):
    """Classify the known legacy fabric/takawe recipe materials on yasser3."""
    cr.execute(
        """
        UPDATE furniture_mrp_bom_stage_line AS recipe_line
           SET furniture_tailoring_material_kind = classified.kind
          FROM (
                SELECT product.id AS product_id,
                       CASE
                           WHEN lower(template.name->>'en_US') ~
                                '(^|[^[:alnum:]_])(تكاية|تكايات|تكاوي|خدادية|وسادة|cushion|pillow)([^[:alnum:]_]|$)'
                               THEN 'takawe'
                           WHEN lower(template.name->>'en_US') ~
                                '(^|[^[:alnum:]_])(قماش|جلد|مخمل|شنيل|كتان|بستاشيو|جيرمان|fabric|leather|velvet|linen)([^[:alnum:]_]|$)'
                               THEN 'fabric'
                       END AS kind
                  FROM product_product AS product
                  JOIN product_template AS template
                    ON template.id = product.product_tmpl_id
               ) AS classified
         WHERE recipe_line.product_id = classified.product_id
           AND recipe_line.stage IN ('tailoring', 'sewing', 'upholstery')
           AND recipe_line.furniture_tailoring_material_kind IS NULL
           AND classified.kind IS NOT NULL
        """
    )
