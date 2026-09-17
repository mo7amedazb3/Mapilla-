# -*- coding: utf-8 -*-
from odoo import models, fields


class FurnitureMrpUpholstery(models.Model):
    """المرحلة 7: كسوه"""
    _name = 'furniture.mrp.upholstery'
    _description = 'مرحلة كسوه'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'upholstery_employee_rel', 'upholstery_id', 'employee_id',
        string='عمال كسوه',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.upholstery.worker.log', 'upholstery_id',
        string='سجل وقت العمال',
    )
    carpentry_order_id = fields.Many2one(
        'furniture.mrp.carpentry', string='أمر التجميع المرجعي', readonly=True,
    )
    tailoring_order_id = fields.Many2one(
        'furniture.mrp.tailoring', string='أمر التفصيل المرجعي', readonly=True,
    )
    sewing_order_id = fields.Many2one(
        'furniture.mrp.sewing', string='أمر الخياطة المرجعي', readonly=True,
    )

    # ─── حقول خاصة بالكسوة ───────────────────────────────────────────────────
    fabric_type = fields.Selection([
        ('velvet',   'مخمل'), ('leather',  'جلد'),  ('linen',   'كتان'),
        ('microfiber', 'مايكروفايبر'), ('chenille', 'شنيل'), ('other', 'أخرى'),
    ], string='نوع القماش', default='velvet')

    fabric_color    = fields.Char(string='لون القماش / الكود')
    fabric_qty_m    = fields.Float(string='كمية القماش (متر)', default=0.0)
    foam_type       = fields.Selection([
        ('standard', 'سفنجة عادية'), ('hr', 'سفنجة HR'), ('memory', 'ميموري فوم'),
    ], string='نوع السفنجة', default='hr')
    foam_density    = fields.Float(string='كثافة السفنجة (كجم/م³)', default=35.0)
    foam_thickness_cm = fields.Float(string='سماكة السفنجة (سم)', default=8.0)
    foam_qty_m3     = fields.Float(string='كمية السفنجة (م³)', default=0.0)
    batting_qty_kg  = fields.Float(string='حشوة الباتينج (كجم)', default=0.0)
    staples_count   = fields.Integer(string='عدد الدبابيس', default=0)

    # ─── الزر والتشطيب ────────────────────────────────────────────────────────
    button_count    = fields.Integer(string='عدد الأزرار', default=0)
    piping_meters   = fields.Float(string='ترفلة/بايبينج (متر)', default=0.0)
    zipper_count    = fields.Integer(string='عدد السوست', default=0)
