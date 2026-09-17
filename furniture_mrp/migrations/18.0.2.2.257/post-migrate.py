# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Give every saved takawe allocation its former per-line size."""
    cr.execute(
        """
        UPDATE furniture_mrp_tailoring_material_allocation AS allocation
           SET piece_size = COALESCE(
               production_line.tailoring_takawe_piece_size,
               production_line.tailoring_piece_size,
               '45'
           )
          FROM furniture_mrp_production_line AS production_line
         WHERE allocation.production_line_id = production_line.id
           AND allocation.material_kind = 'takawe'
           AND allocation.piece_size IS NULL
        """
    )
    cr.execute(
        """
        UPDATE furniture_mrp_tailoring_material_allocation
           SET piece_size = NULL
         WHERE material_kind = 'fabric'
           AND piece_size IS NOT NULL
        """
    )
