# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Remove obsolete fabric sizes; only takawe has an operational size."""
    cr.execute(
        """
        UPDATE furniture_mrp_production_line
           SET tailoring_fabric_piece_size = NULL
         WHERE tailoring_fabric_piece_size IS NOT NULL
        """
    )
