# -*- coding: utf-8 -*-
from odoo import models, fields


class FurnitureMrpFinishing(models.Model):
    """المرحلة 5: تجهيز"""
    _name = 'furniture.mrp.finishing'
    _description = 'مرحلة تجهيز'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'finishing_employee_rel', 'finishing_id', 'employee_id',
        string='عمال تجهيز',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.finishing.worker.log', 'finishing_id',
        string='سجل وقت العمال',
    )
    carpentry_order_id = fields.Many2one(
        'furniture.mrp.carpentry', string='أمر التجميع المرجعي', readonly=True,
    )
    bases_order_id = fields.Many2one(
        'furniture.mrp.bases', string='أمر القواعد المرجعي', readonly=True,
    )
    upholstery_order_id = fields.Many2one(
        'furniture.mrp.upholstery', string='أمر الكسوة المرجعي', readonly=True,
    )

    # ─── حقول خاصة بالتجهيز ──────────────────────────────────────────────────
    finishing_type = fields.Selection([
        ('full',    'تجهيز كامل'),
        ('partial', 'تجهيز جزئي'),
        ('touch_up', 'لمسات أخيرة'),
    ], string='نوع التجهيز', default='full')

    # ─── فحوصات التجهيز ───────────────────────────────────────────────────────
    check_legs         = fields.Boolean(string='فحص الأرجل والمسامير')
    check_fabric_tight = fields.Boolean(string='شد القماش منتظم')
    check_buttons      = fields.Boolean(string='الأزرار والتفاصيل مكتملة')
    check_dimensions   = fields.Boolean(string='الأبعاد مطابقة للمواصفات')
    check_finish_paint = fields.Boolean(string='الدهان والتشطيب نظيف')

    defects_found   = fields.Text(string='العيوب المكتشفة')
    corrections_done= fields.Text(string='التصحيحات المنفذة')

    # ─── التعبئة ──────────────────────────────────────────────────────────────
    packaging_type = fields.Selection([
        ('stretch_wrap', 'بلاستيك تغليف'),
        ('carton',       'كرتون'),
        ('bubble_wrap',  'فقاعات'),
        ('none',         'بدون تغليف'),
    ], string='نوع التغليف', default='stretch_wrap')
    packaging_notes = fields.Text(string='ملاحظات التغليف')
    serial_number   = fields.Char(string='الرقم التسلسلي للطقم', copy=False)
