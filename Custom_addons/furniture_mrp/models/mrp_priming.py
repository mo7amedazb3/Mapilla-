# -*- coding: utf-8 -*-
from odoo import models, fields


class FurnitureMrpPriming(models.Model):
    """المرحلة 1: التقديم"""
    _name = 'furniture.mrp.priming'
    _description = 'مرحلة التقديم'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'priming_employee_rel', 'priming_id', 'employee_id',
        string='عمال التقديم',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.priming.worker.log', 'priming_id',
        string='سجل وقت العمال',
    )

    # ─── حقول خاصة بالتقديم ──────────────────────────────────────────────────
    primer_type = fields.Selection([
        ('wood_primer',   'تقديم خشب'),
        ('metal_primer',  'تقديم معدن'),
        ('pvc_primer',    'تقديم PVC'),
        ('other',         'أخرى'),
    ], string='نوع التقديم', default='wood_primer')

    # ─── المواد المستخدمة ─────────────────────────────────────────────────────
    primer_qty_liters = fields.Float(string='كمية التقديم (لتر)', default=0.0)
    sandpaper_sheets  = fields.Integer(string='أوراق الصنفرة', default=0)
