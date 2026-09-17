# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Keep every pre-lane weekly production order on the legacy route."""
    cr.execute(
        """
        UPDATE furniture_mrp_production
           SET production_lane = 'legacy'
         WHERE production_lane IS NULL
            OR production_lane NOT IN ('legacy', 'body', 'cover')
        """
    )
