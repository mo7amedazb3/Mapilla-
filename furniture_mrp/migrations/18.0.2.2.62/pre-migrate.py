# -*- coding: utf-8 -*-


_RELEASE_TABLE = 'furniture_mrp_advance_material_release'
_STAGE_TABLE = 'furniture_mrp_advance_material_release_stage'


def _table_exists(cr, table_name):
    cr.execute('SELECT to_regclass(%s)', ('public.%s' % table_name,))
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    """Prepare legacy single-production releases for combined releases.

    The old stage ``production_id`` was a stored related field and could be
    nullable at database level.  Preserve its stored value first and only use
    the legacy header link to fill genuinely missing rows.  The old two-column
    unique constraint must be removed before Odoo installs the new
    ``(release_id, production_id, stage_code)`` constraint.
    """
    if not _table_exists(cr, _STAGE_TABLE):
        return

    if _table_exists(cr, _RELEASE_TABLE):
        cr.execute(
            """
            UPDATE furniture_mrp_advance_material_release_stage AS stage
               SET production_id = release.production_id
              FROM furniture_mrp_advance_material_release AS release
             WHERE stage.release_id = release.id
               AND stage.production_id IS NULL
               AND release.production_id IS NOT NULL
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
            'Cannot migrate advance material release stages: %s stage row(s) '
            'have no production order.' % missing_production_count
        )

    # Do not rely on the possibly truncated PostgreSQL constraint name.  Match
    # the legacy constraint by its ordered columns so this remains idempotent
    # across PostgreSQL/Odoo identifier hashing changes.
    cr.execute(
        """
        DO $migration$
        DECLARE
            legacy_constraint RECORD;
        BEGIN
            FOR legacy_constraint IN
                SELECT constraint_row.conname
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
                   ) = ARRAY['release_id', 'stage_code']::name[]
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                    'furniture_mrp_advance_material_release_stage',
                    legacy_constraint.conname
                );
            END LOOP;
        END
        $migration$;
        """
    )

