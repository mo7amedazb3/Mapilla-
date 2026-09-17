# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class FurnitureMrpFoam(models.Model):
    _name = 'furniture.mrp.foam'
    _description = 'قسم السفنجة'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    # ─── الحقول الأساسية ───────────────────────────────────────────────────────
    name = fields.Char(
        string='رقم الأمر',
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
    )
    production_order_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        ondelete='cascade',
        tracking=True,
    )
    carpentry_order_id = fields.Many2one(
        'furniture.mrp.carpentry',
        string='أمر النجارة المرتبط',
        tracking=True,
    )

    # ─── الحالة ──────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('pending', 'في الانتظار'),
        ('in_progress', 'جاري التنفيذ'),
        ('quality_check', 'فحص الجودة'),
        ('done', 'منتهي'),
        ('transferred', 'تم التسليم'),
    ], string='الحالة', default='pending', tracking=True, index=True)

    priority = fields.Selection([
        ('0', 'عادي'),
        ('1', 'مهم'),
        ('2', 'عاجل'),
    ], string='الأولوية', default='0')

    # ─── تفاصيل السفنج ───────────────────────────────────────────────────────────
    foam_type = fields.Selection([
        ('regular', 'سفنج عادي'),
        ('medical', 'سفنج طبي'),
        ('luxury', 'سفنج فاخر'),
        ('memory', 'سفنج ميموري فوم'),
        ('latex', 'سفنج لاتكس'),
        ('other', 'أخرى'),
    ], string='نوع السفنج', required=True, default='regular', tracking=True)

    foam_product_id = fields.Many2one(
        'product.product',
        string='مادة السفنج',
        domain=[('type', '=', 'consu')],
    )

    foam_density = fields.Float(
        string='الكثافة (كجم/م³)',
        default=25.0,
        help='الكثافة القياسية تتراوح بين 20-45 كجم/م³',
    )
    foam_qty = fields.Float(string='الكمية (م²)', default=0.0)
    foam_thickness = fields.Float(string='السماكة (سم)', default=5.0)
    foam_length = fields.Float(string='الطول (سم)', default=0.0)
    foam_width = fields.Float(string='العرض (سم)', default=0.0)

    glue_qty = fields.Float(string='كمية الغراء (كجم)', default=0.0)

    # ─── العمالة ─────────────────────────────────────────────────────────────────
    worker_ids = fields.Many2many(
        'hr.employee',
        'foam_employee_rel',
        'foam_id',
        'employee_id',
        string='عمال السفنجة',
    )
    foreman_id = fields.Many2one(
        'hr.employee',
        string='رئيس قسم السفنجة',
    )

    # ─── التواريخ ────────────────────────────────────────────────────────────────
    date_start = fields.Datetime(string='تاريخ البدء الفعلي')
    date_finish = fields.Datetime(string='تاريخ الانتهاء الفعلي')
    date_planned_finish = fields.Datetime(string='تاريخ الانتهاء المخطط')

    # ─── الجودة ──────────────────────────────────────────────────────────────────
    quality_check = fields.Selection([
        ('pending', 'لم يُفحص'),
        ('pass', 'مقبول ✅'),
        ('fail', 'مرفوض ❌'),
        ('rework', 'يحتاج إعادة عمل'),
    ], string='فحص الجودة', default='pending', tracking=True)

    quality_notes = fields.Text(string='ملاحظات الجودة')
    defect_description = fields.Text(string='وصف العيوب (إن وجدت)')

    # ─── ملاحظات ─────────────────────────────────────────────────────────────────
    notes = fields.Html(string='ملاحظات')
    color = fields.Integer(string='اللون', default=2)

    # ─── Buttons ──────────────────────────────────────────────────────────────────
    def action_start(self):
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_('يمكن بدء الأوامر المنتظرة فقط.'))
            rec.write({
                'state': 'in_progress',
                'date_start': fields.Datetime.now(),
            })
            rec.message_post(body=_('🧽 تم بدء العمل في قسم السفنجة'))

    def action_quality_check(self):
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('يجب أن يكون العمل جارياً لإجراء فحص الجودة.'))
            rec.state = 'quality_check'
            rec.message_post(body=_('🔍 جاري فحص الجودة'))

    def action_approve_quality(self):
        for rec in self:
            rec.write({
                'state': 'done',
                'quality_check': 'pass',
                'date_finish': fields.Datetime.now(),
            })
            rec.message_post(body=_('✅ اجتاز فحص الجودة - تم الانتهاء'))

    def action_reject_quality(self):
        for rec in self:
            rec.write({
                'quality_check': 'rework',
                'state': 'in_progress',
            })
            rec.message_post(body=_('❌ فشل فحص الجودة - يحتاج إعادة عمل'))

    def action_mark_done(self):
        for rec in self:
            if rec.state not in ('in_progress', 'quality_check'):
                raise UserError(_('الأمر ليس في حالة صحيحة للإنهاء.'))
            rec.write({
                'state': 'done',
                'quality_check': 'pass',
                'date_finish': fields.Datetime.now(),
            })
            rec.message_post(body=_('✅ تم الانتهاء من أمر السفنجة'))
