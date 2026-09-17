# -*- coding: utf-8 -*-


_RELEASE_TABLE = 'furniture_mrp_advance_material_release'
_STAGE_TABLE = 'furniture_mrp_advance_material_release_stage'
_PRODUCTION_RELATION_TABLE = 'furn_adv_release_production_rel'


def _table_exists(cr, table_name):
    cr.execute('SELECT to_regclass(%s)', ('public.%s' % table_name,))
    return bool(cr.fetchone()[0])


def _has_production_stage_unique_constraint(cr):
    cr.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_constraint AS constraint_row
             WHERE constraint_row.conrelid =
                   to_regclass('public.furniture_mrp_advance_material_release_stage')
               AND constraint_row.contype = 'u'
               AND (
                    SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
                      FROM unnest(constraint_row.conkey)
                           WITH ORDINALITY AS key_row(attnum, ordinality)
                      JOIN pg_attribute AS attribute_row
                        ON attribute_row.attrelid = constraint_row.conrelid
                       AND attribute_row.attnum = key_row.attnum
               ) = ARRAY[
                    'release_id', 'production_id', 'stage_code'
               ]::name[]
        )
        """
    )
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    """Populate the stored multi-production relation after model setup."""
    required_tables = (
        _RELEASE_TABLE,
        _STAGE_TABLE,
        _PRODUCTION_RELATION_TABLE,
    )
    missing_tables = [
        table_name
        for table_name in required_tables
        if not _table_exists(cr, table_name)
    ]
    if missing_tables:
        raise RuntimeError(
            'Cannot initialize combined advance material releases; missing '
            'table(s): %s' % ', '.join(missing_tables)
        )

    # The stored computed Many2many normally gets recomputed by Odoo during
    # module initialization.  Insert the same authoritative links explicitly
    # so legacy rows are correct even if there was nothing that triggered a
    # recompute.  NOT EXISTS keeps this safe if Odoo already populated them or
    # if the migration is replayed.
    cr.execute(
        """
        WITH production_links AS (
            SELECT stage.release_id, stage.production_id
              FROM furniture_mrp_advance_material_release_stage AS stage
             WHERE stage.production_id IS NOT NULL
            UNION
            SELECT release.id AS release_id, release.production_id
              FROM furniture_mrp_advance_material_release AS release
             WHERE release.production_id IS NOT NULL
        )
        INSERT INTO furn_adv_release_production_rel (release_id, production_id)
        SELECT link.release_id, link.production_id
          FROM production_links AS link
         WHERE NOT EXISTS (
                SELECT 1
                  FROM furn_adv_release_production_rel AS existing_link
                 WHERE existing_link.release_id = link.release_id
                   AND existing_link.production_id = link.production_id
         )
        """
    )

    cr.execute(
        """
        SELECT COUNT(*)
          FROM furniture_mrp_advance_material_release_stage
         WHERE production_id IS NULL
        """
    )
    missing_production_count = cr.fetchone()[0]
    if missing_production_count:
        raise RuntimeError(
            'Combined advance material release migration left %s stage row(s) '
            'without a production order.' % missing_production_count
        )

    # Odoo owns and reflects this SQL constraint.  The pre-migration merely
    # removed the incompatible legacy constraint; reaching this point without
    # Odoo's new three-column constraint would leave the model schema unsafe.
    if not _has_production_stage_unique_constraint(cr):
        raise RuntimeError(
            'Odoo did not create the expected unique constraint on '
            '(release_id, production_id, stage_code).'
        )

