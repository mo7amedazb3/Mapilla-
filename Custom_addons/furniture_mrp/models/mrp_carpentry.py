# -*- coding: utf-8 -*-
from odoo import models, fields


class FurnitureMrpCarpentry(models.Model):
    """المرحلة 3: تجميع"""
    _name = 'furniture.mrp.carpentry'
    _description = 'مرحلة تجميع'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'carpentry_employee_rel', 'carpentry_id', 'employee_id',
        string='عمال التجميع',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.carpentry.worker.log', 'carpentry_id',
        string='سجل وقت العمال',
    )
    priming_order_id = fields.Many2one(
        'furniture.mrp.priming', string='أمر التقديم المرجعي', readonly=True,
    )
    painting_order_id = fields.Many2one(
        'furniture.mrp.painting', string='أمر تصنيع دهانات المرجعي', readonly=True,
    )

    # ─── حقول خاصة بالنجارة ──────────────────────────────────────────────────
    wood_type = fields.Selection([
        ('oak',      'بلوط'), ('pine',    'صنوبر'), ('mahogany', 'ماهوجني'),
        ('mdf',      'MDF'),  ('plywood', 'رقائق'), ('beech',    'زان'),
        ('other',    'أخرى'),
    ], string='نوع الخشب الرئيسي', default='mdf')

    frame_type = fields.Selection([
        ('solid',    'إطار صلب'),
        ('hollow',   'إطار مجوف'),
        ('combined', 'مدمج'),
    ], string='نوع الإطار', default='solid')

    # ─── الأبعاد ──────────────────────────────────────────────────────────────
    length_cm = fields.Float(string='الطول (سم)')
    width_cm  = fields.Float(string='العرض (سم)')
    height_cm = fields.Float(string='الارتفاع (سم)')

    # ─── المواد المستخدمة ─────────────────────────────────────────────────────
    wood_qty_m2   = fields.Float(string='الخشب المستخدم (م²)', default=0.0)
    screws_count  = fields.Integer(string='عدد البراغي', default=0)
    glue_kg       = fields.Float(string='الغراء (كجم)', default=0.0)
    metal_parts   = fields.Float(string='قطع المعدن (كجم)', default=0.0)

    # ─── التجميع ──────────────────────────────────────────────────────────────
    pieces_count  = fields.Integer(string='عدد القطع في الطقم', default=4,
                                   help='الانتريه عادةً 4 قطع: كنبة 3، كنبة 2، كرسيان')
    assembly_notes = fields.Text(string='ملاحظات التجميع')
