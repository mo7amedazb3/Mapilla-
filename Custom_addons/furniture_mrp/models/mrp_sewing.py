# -*- coding: utf-8 -*-
from odoo import fields, models


class FurnitureMrpSewing(models.Model):
    """سجل تاريخي لمرحلة الخياطة المستقلة قبل دمجها في التفصيل."""

    _name = 'furniture.mrp.sewing'
    _description = 'مرحلة الخياطة'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'sewing_employee_rel', 'sewing_id', 'employee_id',
        string='عمال الخياطة',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.sewing.worker.log', 'sewing_id',
        string='سجل وقت العمال',
    )
    tailoring_order_id = fields.Many2one(
        'furniture.mrp.tailoring', string='أمر التفصيل المرجعي', readonly=True,
    )
    sewing_notes = fields.Text(string='ملاحظات الخياطة')
