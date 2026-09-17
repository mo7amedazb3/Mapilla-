# -*- coding: utf-8 -*-
from odoo import models, fields


class FurnitureMrpPackaging(models.Model):
    """المرحلة 8: التغليف"""
    _name = 'furniture.mrp.packaging'
    _description = 'مرحلة التغليف'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'packaging_employee_rel', 'packaging_id', 'employee_id',
        string='عمال التغليف',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.packaging.worker.log', 'packaging_id',
        string='سجل وقت العمال',
    )
    upholstery_order_id = fields.Many2one(
        'furniture.mrp.upholstery', string='أمر الكسوة المرجعي', readonly=True,
    )

    packaging_type = fields.Char(string='نوع التغليف')
    package_count = fields.Integer(string='عدد الطرود', default=1)
    packaging_notes = fields.Text(string='ملاحظات التغليف')
