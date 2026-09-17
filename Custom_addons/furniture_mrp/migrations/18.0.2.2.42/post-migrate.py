def migrate(cr, version):
    """Existing model recipes were intentionally saved before this flag existed."""
    cr.execute("""
        UPDATE mrp_bom AS parent
           SET furniture_recipe_model_id = (
                SELECT recipe.furniture_model_id
                  FROM mrp_bom AS recipe
                 WHERE recipe.furniture_parent_bom_id = parent.id
                   AND recipe.furniture_is_model_recipe = TRUE
                 ORDER BY recipe.active DESC, recipe.id DESC
                 LIMIT 1
               )
         WHERE COALESCE(parent.furniture_is_model_recipe, FALSE) = FALSE
           AND parent.type = 'normal'
           AND parent.furniture_recipe_model_id IS NULL
           AND EXISTS (
                SELECT 1
                  FROM mrp_bom AS recipe
                 WHERE recipe.furniture_parent_bom_id = parent.id
                   AND recipe.furniture_is_model_recipe = TRUE
               )
    """)
    cr.execute("""
        UPDATE mrp_bom
           SET furniture_recipe_ready = TRUE
         WHERE furniture_is_model_recipe = TRUE
    """)
