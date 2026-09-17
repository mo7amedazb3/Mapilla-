# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Give existing production orders a stable initial dashboard order.

    New orders receive their sequence in ``create``.  Existing rows all get
    the field default during the schema upgrade, so rank them once here by a
    useful operational order before users take over with drag and drop.
    """
    cr.execute(
        """
        WITH ranked_orders AS (
            SELECT production.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY production.company_id
                       ORDER BY
                           CASE
                               WHEN production.state IN (
                                   'confirmed', 'in_production', 'priming',
                                   'painting', 'carpentry', 'finishing',
                                   'tailoring', 'upholstery', 'packaging'
                               ) THEN 0
                               WHEN production.state = 'draft' THEN 1
                               WHEN production.state = 'done' THEN 2
                               ELSE 3
                           END,
                           CASE production.priority
                               WHEN '2' THEN 0
                               WHEN '1' THEN 1
                               ELSE 2
                           END,
                           production.date_planned_start NULLS LAST,
                           production.id DESC
                   ) * 10 AS dashboard_sequence
              FROM furniture_mrp_production AS production
        )
        UPDATE furniture_mrp_production AS production
           SET dashboard_sequence = ranked_orders.dashboard_sequence
          FROM ranked_orders
         WHERE ranked_orders.id = production.id
        """
    )
