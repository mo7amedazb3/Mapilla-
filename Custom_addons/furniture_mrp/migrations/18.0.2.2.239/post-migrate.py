# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Split the legacy shared piece size without losing saved choices."""
    cr.execute(
        """
        UPDATE furniture_mrp_production_line
           SET tailoring_fabric_piece_size = COALESCE(
                   tailoring_fabric_piece_size,
                   tailoring_piece_size
               ),
               tailoring_takawe_piece_size = COALESCE(
                   tailoring_takawe_piece_size,
                   tailoring_piece_size
               )
         WHERE tailoring_piece_size IS NOT NULL
           AND (
               tailoring_fabric_piece_size IS NULL
               OR tailoring_takawe_piece_size IS NULL
           )
        """
    )
