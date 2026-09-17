# -*- coding: utf-8 -*-

from odoo import fields, models


FURNITURE_MRP_UOM_XMLIDS = (
    'furniture_mrp.furniture_uom_unit',
    'furniture_mrp.furniture_uom_meter',
    'furniture_mrp.furniture_uom_cm',
    'furniture_mrp.furniture_uom_kgm',
    'furniture_mrp.furniture_uom_gram',
    'furniture_mrp.furniture_uom_litre',
    'furniture_mrp.furniture_uom_roll',
    'furniture_mrp.furniture_uom_bolt',
    'furniture_mrp.furniture_uom_sheet',
    'furniture_mrp.furniture_uom_board',
    'furniture_mrp.furniture_uom_box',
    'furniture_mrp.furniture_bom_uom_meter',
    'furniture_mrp.furniture_bom_uom_cubic_meter',
    'furniture_mrp.furniture_bom_uom_cm',
    'furniture_mrp.furniture_bom_uom_kgm',
    'furniture_mrp.furniture_bom_uom_gram',
    'furniture_mrp.furniture_bom_uom_litre',
)


class UomUom(models.Model):
    _inherit = 'uom.uom'

    furniture_mrp_bom_uom = fields.Boolean(
        string='Furniture BoM Unit',
        default=False,
        index=True,
        copy=False,
    )

    def _furniture_mrp_uom_ids(self):
        ids = []
        for xmlid in FURNITURE_MRP_UOM_XMLIDS:
            uom = self.env.ref(xmlid, raise_if_not_found=False)
            if uom:
                ids.append(uom.id)
        return set(ids)

    def _is_furniture_mrp_uom_pair(self, to_unit):
        if not self or not to_unit:
            return False
        furniture_uom_ids = self._furniture_mrp_uom_ids()
        return self.id in furniture_uom_ids and to_unit.id in furniture_uom_ids

    def _compute_quantity(self, qty, to_unit, round=True, rounding_method='UP', raise_if_failure=True):
        if (
            self
            and to_unit
            and self != to_unit
            and self.category_id != to_unit.category_id
            and self._is_furniture_mrp_uom_pair(to_unit)
        ):
            return qty
        return super()._compute_quantity(qty, to_unit, round=round, rounding_method=rounding_method, raise_if_failure=raise_if_failure)

    def _compute_price(self, price, to_unit):
        if (
            self
            and to_unit
            and self != to_unit
            and self.category_id != to_unit.category_id
            and self._is_furniture_mrp_uom_pair(to_unit)
        ):
            return price
        return super()._compute_price(price, to_unit)
