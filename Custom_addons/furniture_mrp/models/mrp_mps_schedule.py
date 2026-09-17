# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from functools import reduce
from math import gcd, isfinite

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import FURNITURE_STAGE_FIELD_MAP, FURNITURE_STAGE_SELECTION


_MPS_STAGE_LABELS = dict(FURNITURE_STAGE_SELECTION)
MPS_STAGE_DISPLAY_CODES = (
    'priming',
    'painting',
    'carpentry',
    'bases',
    'finishing',
    'tailoring',
    'upholstery',
    'packaging',
)
MPS_STAGE_DISPLAY_SELECTION = [
    (code, _MPS_STAGE_LABELS[code])
    for code in MPS_STAGE_DISPLAY_CODES
]
MPS_AUTO_FRAME_ROUTE = ('priming', 'carpentry')
MPS_AUTO_FINISH_ROUTE = ('bases', 'finishing')
MPS_AUTO_STAGE_ROUTES = (MPS_AUTO_FRAME_ROUTE, MPS_AUTO_FINISH_ROUTE)
MPS_AUTO_JOIN_DEPENDENCIES = {
    'upholstery': ('finishing', 'tailoring'),
    'packaging': ('upholstery', 'painting'),
}
MPS_FIFO_HANDOFF_LANES = frozenset({
    'frame',
    'finish',
    'tailoring',
    'painting',
    'upholstery',
    'packaging',
})
MPS_AUTO_STAGE_SEQUENCE = {
    'priming': 1000,
    'carpentry': 2000,
    'painting': 2500,
    'bases': 3000,
    'finishing': 4000,
    'tailoring': 5000,
    'upholstery': 6000,
    'packaging': 7000,
}

# The route is shared by every saved product/model recipe.  The duration is
# deliberately not stored here: every product gets its own editable timing.
# `code` is stable so upgrades add/rename steps without relying on database IDs.
MPS_STANDARD_PRODUCT_STEPS = (
    # code, label, sequence, stage, distribution, fabric, dependencies
    ('priming', 'التقديم', 10, 'priming', 'stage_pool_equal', False, ()),
    ('chassis', 'تجميع الشاسيه', 100, 'carpentry', 'stage_pool_equal', False, ('priming',)),
    ('sides', 'تركيب الأجناب', 110, 'carpentry', 'stage_pool_equal', False, ('chassis',)),
    ('white_finish', 'تشطيب البياض', 120, 'carpentry', 'stage_pool_equal', False, ('sides',)),
    # Back assemblies are an independent painting-room operation.  Their
    # physical stock is supplied by the painting lane, not by the frame MO.
    ('back_assembly', 'تجميع الظهور', 130, 'painting', 'stage_pool_equal', False, ()),
    ('sanding', 'الصروخة والتخريم', 200, 'painting', 'stage_pool_equal', False, ()),
    ('paint_prep', 'تجهيز الدهانات', 210, 'painting', 'stage_pool_equal', False, ('sanding',)),
    ('back_prep', 'تجهيز الظهور', 220, 'painting', 'stage_pool_equal', False, ()),
    ('trimming', 'التقصيب', 230, 'painting', 'stage_pool_equal', False, ('paint_prep',)),
    ('paint_cutting', 'تقطيع الدهانات', 240, 'painting', 'stage_pool_equal', False, ('paint_prep', 'back_prep')),
    ('base_prep', 'تجهيز القواعد', 300, 'bases', 'stage_pool_equal', False, ()),
    ('base_install', 'تركيب القواعد', 310, 'bases', 'stage_pool_equal', False, ('base_prep',)),
    ('cut', 'التفصيل — ليكرا', 400, 'tailoring', 'stage_pool_equal', 'ليكرا', ()),
    ('cut', 'التفصيل — كتان', 400, 'tailoring', 'stage_pool_equal', 'كتان', ()),
    ('sewing', 'الخياطة', 420, 'tailoring', 'stage_pool_equal', False, ('cut',)),
    ('takawe', 'التكاوي (قص وخياطة وتلبيس)', 430, 'tailoring', 'stage_pool_equal', False, ('sewing',)),
    ('finishing', 'التجهيز', 500, 'finishing', 'stage_pool_equal', False, ('base_install',)),
    ('upholstery', 'الكسوة', 600, 'upholstery', 'stage_pool_equal', False, ('finishing', 'takawe')),
    ('tying', 'التربيط', 610, 'upholstery', 'stage_pool_equal', False, ('upholstery',)),
    ('packaging', 'التغليف', 800, 'packaging', 'stage_pool_equal', False, ('tying', 'trimming', 'paint_cutting', 'back_assembly')),
)

MPS_ASSIGNMENT_ACTIVE_STATES = ('proposed', 'approved', 'in_progress')
MPS_PLAN_ACTIVE_STATES = ('planned', 'approved', 'in_progress')
MPS_PLAN_LOCKED_LINE_STATES = ('in_progress', 'done')
MPS_ENABLED_PARAMETER = 'furniture_mrp.mps_enabled'
MPS_WORKER_LOG_MODELS = (
    'furniture.mrp.priming.worker.log',
    'furniture.mrp.painting.worker.log',
    'furniture.mrp.carpentry.worker.log',
    'furniture.mrp.finishing.worker.log',
    'furniture.mrp.bases.worker.log',
    'furniture.mrp.tailoring.worker.log',
    'furniture.mrp.upholstery.worker.log',
    'furniture.mrp.sewing.worker.log',
    'furniture.mrp.packaging.worker.log',
)


class FurnitureFabricWorkType(models.Model):
    _name = 'furniture.fabric.work.type'
    _description = 'نوع القماش التشغيلي'
    _order = 'sequence, name, id'

    name = fields.Char(string='نوع القماش', required=True, index=True)
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'furniture_fabric_work_type_name_unique',
            'unique(name)',
            'نوع القماش التشغيلي موجود بالفعل.',
        ),
    ]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    furniture_fabric_type_id = fields.Many2one(
        'furniture.fabric.work.type',
        string='نوع القماش التشغيلي (MPS)',
        copy=True,
        index=True,
        ondelete='restrict',
        help=(
            'نوع القماش الذي يحدد معيار زمن التفصيل، مثل ليكرا أو كتان. '
            'يستخدم فقط عندما يكون الصنف مصنفًا كقماش.'
        ),
    )

    @api.onchange('furniture_tailoring_material_kind')
    def _onchange_furniture_material_kind_clear_work_type(self):
        for template in self:
            if template.furniture_tailoring_material_kind != 'fabric':
                template.furniture_fabric_type_id = False

    @api.constrains(
        'furniture_tailoring_material_kind',
        'furniture_fabric_type_id',
    )
    def _check_furniture_fabric_work_type(self):
        for template in self:
            if (
                template.furniture_fabric_type_id
                and template.furniture_tailoring_material_kind != 'fabric'
            ):
                raise ValidationError(_(
                    'نوع القماش التشغيلي متاح فقط للأصناف المصنفة كقماش.'
                ))

    def write(self, vals):
        vals = dict(vals)
        if (
            'furniture_tailoring_material_kind' in vals
            and vals.get('furniture_tailoring_material_kind') != 'fabric'
            and 'furniture_fabric_type_id' not in vals
        ):
            vals['furniture_fabric_type_id'] = False
        trigger_replan = 'furniture_fabric_type_id' in vals
        products = self.mapped('product_variant_ids') if trigger_replan else self.env['product.product']
        result = super().write(vals)
        if trigger_replan and products:
            products._furniture_replan_proposed_mps_for_fabric_change()
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    furniture_fabric_type_id = fields.Many2one(
        related='product_tmpl_id.furniture_fabric_type_id',
        string='نوع القماش التشغيلي (MPS)',
        store=True,
        readonly=False,
        index=True,
    )

    def _furniture_replan_proposed_mps_for_fabric_change(self):
        if not self:
            return
        Allocation = self.env['furniture.mrp.tailoring.material.allocation'].sudo()
        MaterialLine = self.env['furniture.mrp.material.line'].sudo()
        productions = Allocation.search([
            ('material_kind', '=', 'fabric'),
            ('product_id', 'in', self.ids),
        ]).mapped('production_id')
        productions |= MaterialLine.search([
            ('product_id', 'in', self.ids),
            ('stage', '=', 'tailoring'),
        ]).mapped('production_id')
        productions._furniture_replan_proposed_mps(changed_stage_codes={'tailoring'})


class FurnitureMrpMPSProfile(models.Model):
    _name = 'furniture.mrp.mps.profile'
    _description = 'بروفايل تشغيل MPS'
    _order = 'sequence, name, id'
    _check_company_auto = True

    name = fields.Char(string='اسم بروفايل التشغيل', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_auto_product_profile = fields.Boolean(
        string='مسار أصناف تلقائي',
        default=False,
        copy=False,
        index=True,
        help=(
            'ينشئه النظام من مصفوفة زمن القطعة للموديلات التي لا تملك '
            'بروفايل تشغيل تفصيليًا.'
        ),
    )
    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        required=True,
        ondelete='restrict',
        index=True,
    )
    product_ids = fields.Many2many(
        'product.product',
        'furniture_mrp_mps_profile_product_rel',
        'profile_id',
        'product_id',
        string='منتجات التشغيل',
        required=True,
        domain=[('furniture_dimension_source_product_id', '=', False)],
        help='المنتجات الأساسية فقط، وليس نسخ المقاسات أو منتج الـKit.',
    )
    operation_ids = fields.One2many(
        'furniture.mrp.mps.operation',
        'profile_id',
        string='معايير العمليات',
    )
    notes = fields.Text(
        string='تنبيهات إعداد البروفايل',
        help='معلومات ناقصة لا تمنع توليد بقية الجدول، لكنها تظهر لمدير الإنتاج.',
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_profile_model_company_kind_unique',
            'unique(furniture_model_id, company_id, is_auto_product_profile)',
            'يوجد بروفايل MPS من نفس النوع لهذا الموديل وهذه الشركة بالفعل.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            vals.get('is_auto_product_profile') for vals in vals_list
        ):
            raise AccessError(_('بروفايلات MPS التلقائية ينشئها النظام فقط.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and (
            self.filtered('is_auto_product_profile')
            or vals.get('is_auto_product_profile')
        ):
            raise AccessError(_(
                'البروفايل التلقائي يديره MPS. عدّل زمن القطعة من شاشة '
                '«أزمنة الأصناف بالمراحل».'
            ))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered('is_auto_product_profile'):
            raise AccessError(_('لا يمكن حذف بروفايل MPS تلقائي يديره النظام.'))
        return super().unlink()


class FurnitureMrpMPSOperation(models.Model):
    _name = 'furniture.mrp.mps.operation'
    _description = 'معيار عملية MPS'
    _order = 'profile_id, sequence, id'
    _check_company_auto = True

    name = fields.Char(string='العملية', required=True)
    code = fields.Char(
        string='الكود الفني',
        required=True,
        index=True,
        help='تستخدم نفس القيمة لبدائل العملية حسب نوع القماش.',
    )
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(default=True)
    is_seeded_big_moon = fields.Boolean(
        string='معيار Big Moon مُدار من الموديول',
        default=False,
        copy=False,
        index=True,
    )
    is_auto_product_standard = fields.Boolean(
        string='معيار صنف تلقائي',
        default=False,
        copy=False,
        index=True,
    )
    product_time_id = fields.Many2one(
        'furniture.mrp.mps.product.time',
        string='زمن الصنف بالمراحل',
        ondelete='cascade',
        copy=False,
        index=True,
        check_company=True,
    )
    time_is_configured = fields.Boolean(
        string='زمن القطعة مضبوط',
        default=True,
        copy=False,
        index=True,
    )
    profile_id = fields.Many2one(
        'furniture.mrp.mps.profile',
        string='بروفايل التشغيل',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related='profile_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='المرحلة',
        required=True,
        index=True,
    )
    fabric_type_id = fields.Many2one(
        'furniture.fabric.work.type',
        string='نوع القماش',
        ondelete='restrict',
        help='فارغ يعني أن العملية لا تتغير حسب نوع القماش.',
    )
    distribution_mode = fields.Selection(
        [
            ('single', 'عامل واحد فقط'),
            ('stage_pool_equal', 'توزيع قطع كاملة على عمال المرحلة'),
        ],
        string='طريقة توزيع العمل',
        required=True,
        default='stage_pool_equal',
    )
    duration_hours = fields.Float(
        string='زمن الدفعة القياسية (ساعة)',
        required=True,
        digits=(16, 3),
    )
    output_line_ids = fields.One2many(
        'furniture.mrp.mps.operation.output',
        'operation_id',
        string='ناتج الدفعة القياسية',
        copy=True,
    )
    dependency_ids = fields.Many2many(
        'furniture.mrp.mps.operation',
        'furniture_mrp_mps_operation_dependency_rel',
        'operation_id',
        'dependency_id',
        string='العمليات السابقة المطلوبة',
        domain="[('profile_id', '=', profile_id), ('id', '!=', id)]",
    )
    same_worker_key = fields.Char(
        string='مفتاح نفس العامل',
        help='عمليات عامل واحد التي تحمل نفس المفتاح يجب أن تذهب لنفس العامل.',
    )
    different_worker_key = fields.Char(
        string='مفتاح عمال مختلفين',
        help='عمليات عامل واحد التي تحمل نفس المفتاح تستخدم عمالًا مختلفين ما دامت الطاقة تسمح.',
    )
    notes = fields.Text(string='ملاحظات')

    _sql_constraints = [
        (
            'furniture_mrp_mps_operation_duration_positive',
            'check((COALESCE(is_auto_product_standard, FALSE) '
            'AND duration_hours >= 0) OR '
            '(NOT COALESCE(is_auto_product_standard, FALSE) '
            'AND duration_hours > 0))',
            'زمن العملية التفصيلية يجب أن يكون أكبر من صفر؛ الصفر مسموح فقط لمعيار تلقائي ناقص الزمن.',
        ),
        (
            'furniture_mrp_mps_operation_auto_source_consistent',
            'check((COALESCE(is_auto_product_standard, FALSE) '
            'AND product_time_id IS NOT NULL) OR '
            '(NOT COALESCE(is_auto_product_standard, FALSE) '
            'AND product_time_id IS NULL '
            'AND COALESCE(time_is_configured, TRUE)))',
            'مصدر معيار MPS التلقائي وحالة ضبط الزمن غير متطابقين.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any((
            vals.get('is_auto_product_standard')
            or vals.get('product_time_id')
            or vals.get('time_is_configured') is False
        ) for vals in vals_list):
            raise AccessError(_(
                'معايير الأصناف التلقائية ينشئها MPS فقط.'
            ))
        for vals in vals_list:
            is_auto = bool(vals.get('is_auto_product_standard'))
            has_source = bool(vals.get('product_time_id'))
            duration = vals.get('duration_hours', 0.0) or 0.0
            positive = float_compare(
                duration, 0.0, precision_digits=6,
            ) > 0
            configured = bool(vals.get('time_is_configured', True))
            if is_auto != has_source:
                raise ValidationError(_(
                    'المعيار التلقائي يجب أن يرتبط بسطر زمن صنف واحد، '
                    'والمعيار التفصيلي لا يرتبط بسطر تلقائي.'
                ))
            if (not is_auto and (not positive or not configured)) or (
                is_auto and configured != positive
            ):
                raise ValidationError(_(
                    'زمن معيار العملية التفصيلية يجب أن يكون أكبر من صفر، '
                    'والصفر مسموح فقط لمعيار صنف تلقائي ناقص الزمن.'
                ))
        allow_empty_output = bool(
            self.env.su
            and self.env.context.get('furniture_mps_allow_empty_output')
        )
        if not allow_empty_output:
            for vals in vals_list:
                if not vals.get('output_line_ids'):
                    raise ValidationError(_(
                        'أضف منتجًا واحدًا على الأقل في ناتج الدفعة القياسية.'
                    ))
        return super().create(vals_list)

    def write(self, vals):
        protected_source_fields = {
            'is_auto_product_standard',
            'product_time_id',
            'time_is_configured',
        }
        if not self.env.su and (
            self.filtered('is_auto_product_standard')
            or protected_source_fields.intersection(vals)
        ):
            raise AccessError(_(
                'هذا معيار تلقائي. عدّل زمن القطعة من شاشة '
                '«أزمنة الأصناف بالمراحل».'
            ))
        for operation in self:
            is_auto = bool(vals.get(
                'is_auto_product_standard',
                operation.is_auto_product_standard,
            ))
            product_time_id = vals.get(
                'product_time_id',
                operation.product_time_id.id,
            )
            duration = vals.get(
                'duration_hours',
                operation.duration_hours,
            ) or 0.0
            positive = float_compare(
                duration, 0.0, precision_digits=6,
            ) > 0
            configured = bool(vals.get(
                'time_is_configured',
                operation.time_is_configured,
            ))
            if is_auto != bool(product_time_id):
                raise ValidationError(_(
                    'المعيار التلقائي يجب أن يرتبط بسطر زمن صنف واحد، '
                    'والمعيار التفصيلي لا يرتبط بسطر تلقائي.'
                ))
            if (not is_auto and (not positive or not configured)) or (
                is_auto and configured != positive
            ):
                raise ValidationError(_(
                    'زمن معيار العملية التفصيلية يجب أن يكون أكبر من صفر، '
                    'والصفر مسموح فقط لمعيار صنف تلقائي ناقص الزمن.'
                ))
        result = super().write(vals)
        if (
            'output_line_ids' in vals
            and not (
                self.env.su
                and self.env.context.get('furniture_mps_allow_empty_output')
            )
            and self.filtered(lambda operation: not operation.output_line_ids)
        ):
            raise ValidationError(_(
                'لا يمكن حفظ معيار عملية بدون ناتج دفعة قياسية.'
            ))
        return result

    def unlink(self):
        if not self.env.su and self.filtered('is_auto_product_standard'):
            raise AccessError(_('لا يمكن حذف معيار MPS تلقائي يديره النظام.'))
        return super().unlink()

    @api.constrains(
        'duration_hours',
        'is_auto_product_standard',
        'time_is_configured',
    )
    def _check_duration_configuration(self):
        for operation in self:
            positive = float_compare(
                operation.duration_hours,
                0.0,
                precision_digits=6,
            ) > 0
            if (
                operation.is_auto_product_standard
                != bool(operation.product_time_id)
            ):
                raise ValidationError(_(
                    'المعيار التلقائي يجب أن يرتبط بسطر زمن صنف واحد، '
                    'والمعيار التفصيلي لا يرتبط بسطر تلقائي.'
                ))
            if not operation.is_auto_product_standard and (
                not positive or not operation.time_is_configured
            ):
                raise ValidationError(_(
                    'زمن معيار العملية التفصيلية يجب أن يكون أكبر من صفر.'
                ))
            if operation.is_auto_product_standard and (
                operation.time_is_configured != positive
            ):
                raise ValidationError(_(
                    'حالة ضبط الزمن لا تطابق زمن معيار الصنف التلقائي.'
                ))

    @api.constrains('profile_id', 'code', 'fabric_type_id')
    def _check_unique_profile_code_fabric(self):
        for operation in self:
            duplicate = self.with_context(active_test=False).search_count([
                ('id', '!=', operation.id),
                ('profile_id', '=', operation.profile_id.id),
                ('code', '=', operation.code),
                ('fabric_type_id', '=', operation.fabric_type_id.id or False),
            ])
            if duplicate:
                raise ValidationError(_(
                    'لا يمكن تكرار نفس كود العملية ونوع القماش داخل البروفايل.'
                ))

    @api.constrains('profile_id', 'dependency_ids')
    def _check_dependency_cycles(self):
        for operation in self:
            foreign_dependencies = operation.dependency_ids.filtered(
                lambda dependency: dependency.profile_id != operation.profile_id
            )
            if foreign_dependencies:
                raise ValidationError(_(
                    'كل تبعيات العملية يجب أن تكون من نفس بروفايل MPS.'
                ))
            foreign_dependents = self.search([
                ('dependency_ids', 'in', operation.id),
                ('profile_id', '!=', operation.profile_id.id),
            ], limit=1)
            if foreign_dependents:
                raise ValidationError(_(
                    'لا يمكن نقل العملية إلى بروفايل آخر بينما تعتمد عليها عملية '
                    'من البروفايل الحالي.'
                ))
            if operation in operation.dependency_ids:
                raise ValidationError(_('العملية لا يمكن أن تعتمد على نفسها.'))
            pending = list(operation.dependency_ids)
            visited = set()
            while pending:
                dependency = pending.pop()
                if dependency.id in visited:
                    continue
                if dependency == operation:
                    raise ValidationError(_('تبعيات عمليات MPS تحتوي على دائرة مغلقة.'))
                visited.add(dependency.id)
                pending.extend(dependency.dependency_ids)

    @api.constrains(
        'distribution_mode',
        'same_worker_key',
        'different_worker_key',
    )
    def _check_worker_keys(self):
        for operation in self:
            if (
                (operation.same_worker_key or operation.different_worker_key)
                and operation.distribution_mode != 'single'
            ):
                raise ValidationError(_(
                    'مفاتيح نفس/اختلاف العامل متاحة لعمليات «عامل واحد فقط».'
                ))
            if operation.same_worker_key and operation.different_worker_key:
                raise ValidationError(_(
                    'لا يمكن أن تحمل العملية مفتاح نفس العامل ومفتاح اختلاف العامل معًا.'
                ))

    @api.model
    def _seed_big_moon_profile(self, furniture_model=None):
        """Create/update the validated Big Moon standards without numeric IDs.

        The migration resolves the live canonical products through exact normal
        recipes.  Runtime scheduling then uses only relational IDs.
        """
        if furniture_model:
            furniture_model = furniture_model.exists().ensure_one()
        else:
            Model = self.env['furniture.product.model'].with_context(active_test=False)
            models_found = Model.search([('name', '=ilike', 'بيج مون')])
            if len(models_found) != 1:
                return self.env['furniture.mrp.mps.profile']
            furniture_model = models_found
        boms = self.env['mrp.bom'].with_context(active_test=False).search([
            ('active', '=', True),
            ('type', '=', 'normal'),
            ('furniture_model_id', '=', furniture_model.id),
            ('furniture_product_id', '!=', False),
        ])
        role_aliases = {
            'sofa': {'كنبة كبيرة', 'big sofa', 'large sofa'},
            'chaise': {'شازلونج', 'chaise', 'chaise longue'},
            'chair': {'فوتيه', 'armchair', 'chair'},
        }
        role_aliases = {
            role: {alias.casefold() for alias in aliases}
            for role, aliases in role_aliases.items()
        }
        candidates_by_company = defaultdict(
            lambda: defaultdict(lambda: self.env['product.product'])
        )
        for bom in boms:
            label = ' '.join((bom.furniture_product_id.name or '').split()).casefold()
            for role, aliases in role_aliases.items():
                if label in aliases:
                    company_id = bom.company_id.id or self.env.company.id
                    canonical_product = (
                        bom.furniture_product_id.furniture_dimension_source_product_id
                        or bom.furniture_product_id
                    )
                    candidates_by_company[company_id][role] |= canonical_product
                    break
        # Do not guess across companies.  The live yasser3 setup has one
        # unambiguous Big Moon recipe family; a multi-company deployment must
        # provide an explicit profile per company instead of mixing products.
        if len(candidates_by_company) != 1:
            return self.env['furniture.mrp.mps.profile']
        company_id, role_candidates = next(iter(candidates_by_company.items()))
        if set(role_candidates) != {'sofa', 'chaise', 'chair'}:
            return self.env['furniture.mrp.mps.profile']
        if any(len(candidates) != 1 for candidates in role_candidates.values()):
            return self.env['furniture.mrp.mps.profile']
        products_by_role = {
            role: candidates.ensure_one()
            for role, candidates in role_candidates.items()
        }
        if len({product.id for product in products_by_role.values()}) != 3:
            return self.env['furniture.mrp.mps.profile']

        fabric_types = {}
        for sequence, name in ((10, 'ليكرا'), (20, 'كتان')):
            fabric_type = self.env['furniture.fabric.work.type'].with_context(
                active_test=False,
            ).search([('name', '=ilike', name)], limit=1)
            if fabric_type:
                fabric_type.write({'active': True, 'sequence': sequence})
            else:
                fabric_type = self.env['furniture.fabric.work.type'].create({
                    'name': name,
                    'sequence': sequence,
                })
            fabric_types[name] = fabric_type

        sofa = products_by_role['sofa']
        chaise = products_by_role['chaise']
        chair = products_by_role['chair']
        pair_9 = ((sofa, 9.0), (chaise, 9.0))
        pair_4 = ((sofa, 4.0), (chaise, 4.0))
        pair_1 = ((sofa, 1.0), (chaise, 1.0))
        standards = [
            # code, name, seq, stage, duration, outputs, mode, fabric, deps, same, different
            ('priming_pair', 'تقديم الكنبة الكبيرة والشازلونج', 10, 'priming', 27.0, pair_9, 'single', False, (), False, 'big_moon_priming_workers'),
            ('priming_chair', 'تقديم الفوتيه', 20, 'priming', 16.0, ((chair, 40.0),), 'single', False, (), False, 'big_moon_priming_workers'),
            ('chassis_pair', 'تجميع شاسيه الكنبة والشازلونج', 100, 'carpentry', 6.0, pair_9, 'stage_pool_equal', False, ('priming_pair',), False, False),
            ('chassis_chair', 'تجميع شاسيه الفوتيه', 110, 'carpentry', 14.0, ((chair, 40.0),), 'stage_pool_equal', False, ('priming_chair',), False, False),
            ('sides_pair', 'تركيب أجناب الكنبة والشازلونج', 120, 'carpentry', 10.0, pair_9, 'stage_pool_equal', False, ('chassis_pair',), False, False),
            ('white_finish_pair', 'تشطيب بياض الكنبة والشازلونج', 130, 'carpentry', 10.0, pair_9, 'stage_pool_equal', False, ('sides_pair',), False, False),
            ('white_finish_chair', 'تشطيب بياض الفوتيه', 140, 'carpentry', 14.0, ((chair, 40.0),), 'stage_pool_equal', False, ('chassis_chair',), False, False),
            ('back_assembly_pair', 'تجميع ظهور الكنبة والشازلونج', 150, 'painting', 15.0, pair_9, 'stage_pool_equal', False, (), False, False),
            ('back_assembly_chair', 'تجميع ظهور الفوتيه', 160, 'painting', 12.0, ((chair, 40.0),), 'stage_pool_equal', False, (), False, False),
            ('sanding_pair', 'صروخة الكنبة والشازلونج', 200, 'painting', 7.0, pair_9, 'stage_pool_equal', False, (), False, False),
            ('sanding_drilling_chair', 'صروخة وتخريم الفوتيه', 210, 'painting', 8.0, ((chair, 40.0),), 'stage_pool_equal', False, (), False, False),
            ('paint_prep_pair', 'تجهيز دهانات الكنبة والشازلونج', 220, 'painting', 12.0, pair_9, 'stage_pool_equal', False, ('sanding_pair',), False, False),
            ('paint_prep_chair', 'تجهيز دهانات الفوتيه', 230, 'painting', 4.0, ((chair, 4.0),), 'stage_pool_equal', False, ('sanding_drilling_chair',), False, False),
            ('back_prep_chair', 'تجهيز ظهور الفوتيه', 240, 'painting', 6.0, ((chair, 40.0),), 'stage_pool_equal', False, (), False, False),
            ('trimming_pair', 'تقصيب الكنبة والشازلونج', 250, 'painting', 4.0, pair_9, 'stage_pool_equal', False, ('paint_prep_pair',), False, False),
            ('paint_cutting_chair', 'تقطيع دهانات الفوتيه', 260, 'painting', 6.0, ((chair, 40.0),), 'single', False, ('paint_prep_chair', 'back_prep_chair'), 'big_moon_base_and_chair_paint', False),
            ('base_prep_pair', 'تجهيز قواعد الكنبة والشازلونج', 300, 'bases', 3.0, pair_9, 'single', False, (), 'big_moon_base_and_chair_paint', False),
            ('base_install_pair', 'تركيب قواعد الكنبة والشازلونج', 310, 'bases', 1.5, pair_1, 'single', False, ('base_prep_pair',), 'big_moon_base_and_chair_paint', False),
            ('cut_pair', 'تفصيل الكنبة والشازلونج — ليكرا', 400, 'tailoring', 6.0, pair_4, 'single', 'ليكرا', (), False, False),
            ('cut_chair', 'تفصيل الفوتيه — ليكرا', 410, 'tailoring', 6.0, ((chair, 8.0),), 'single', 'ليكرا', (), False, False),
            ('cut_pair', 'تفصيل الكنبة والشازلونج — كتان', 400, 'tailoring', 8.0, pair_4, 'single', 'كتان', (), False, False),
            ('cut_chair', 'تفصيل الفوتيه — كتان', 410, 'tailoring', 6.0, ((chair, 2.0),), 'single', 'كتان', (), False, False),
            ('sewing_pair', 'خياطة الكنبة والشازلونج', 420, 'tailoring', 4.0, pair_4, 'single', False, ('cut_pair',), 'big_moon_sewing', False),
            ('sewing_chair', 'خياطة الفوتيه', 430, 'tailoring', 6.0, ((chair, 8.0),), 'single', False, ('cut_chair',), 'big_moon_sewing', False),
            ('takawe_pair', 'تكاوي الكنبة والشازلونج — قص وخياطة وتلبيس', 440, 'tailoring', 0.5, pair_1, 'stage_pool_equal', False, ('sewing_pair',), False, False),
            ('takawe_chair', 'تكاوي الفوتيه — قص وخياطة وتلبيس', 450, 'tailoring', 0.25, ((chair, 1.0),), 'stage_pool_equal', False, ('sewing_chair',), False, False),
            ('finishing_pair', 'تجهيز الكنبة والشازلونج', 500, 'finishing', 6.0, pair_1, 'single', False, ('base_install_pair',), False, False),
            ('finishing_chair', 'تجهيز الفوتيه', 510, 'finishing', 5.0, ((chair, 2.0),), 'single', False, (), False, False),
            ('upholstery_pair', 'كسوة الكنبة والشازلونج', 600, 'upholstery', 5.0, pair_1, 'stage_pool_equal', False, ('finishing_pair', 'takawe_pair'), False, False),
            ('tying_pair', 'تربيط الكنبة والشازلونج', 610, 'upholstery', 2.0, pair_1, 'stage_pool_equal', False, ('upholstery_pair',), False, False),
            ('upholstery_chair', 'كسوة الفوتيه', 620, 'upholstery', 2.5, ((chair, 2.0),), 'single', False, ('finishing_chair', 'takawe_chair'), 'big_moon_chair_upholstery', False),
            ('tying_chair', 'تربيط الفوتيه', 630, 'upholstery', 1.5, ((chair, 2.0),), 'single', False, ('upholstery_chair',), 'big_moon_chair_upholstery', False),
            ('packaging_pair', 'تغليف الكنبة والشازلونج', 800, 'packaging', 0.75, pair_1, 'stage_pool_equal', False, ('tying_pair', 'trimming_pair', 'back_assembly_pair'), False, False),
            ('packaging_chair', 'تغليف الفوتيه', 810, 'packaging', 0.25, ((chair, 1.0),), 'stage_pool_equal', False, ('tying_chair', 'paint_cutting_chair', 'back_assembly_chair'), False, False),
        ]

        profiles = self.env['furniture.mrp.mps.profile']
        for company_id in (company_id,):
            profile = self.env['furniture.mrp.mps.profile'].with_context(
                active_test=False,
            ).search([
                ('furniture_model_id', '=', furniture_model.id),
                ('company_id', '=', company_id),
                ('is_auto_product_profile', '=', False),
            ], limit=1)
            profile_vals = {
                'name': 'بيج مون — الكنبة الكبيرة والشازلونج والفوتيه',
                'active': True,
                'is_auto_product_profile': False,
                'product_ids': [(6, 0, [sofa.id, chaise.id, chair.id])],
                'notes': (
                    'تنبيه: لم يصل زمن تركيب قواعد الفوتيه بعد؛ بقية أزمنة '
                    'بيج مون مدخلة حسب القياسات التشغيلية المستلمة.'
                ),
            }
            if profile:
                profile.write(profile_vals)
            else:
                profile = self.env['furniture.mrp.mps.profile'].create({
                    **profile_vals,
                    'company_id': company_id,
                    'furniture_model_id': furniture_model.id,
                })
            profiles |= profile
            operations_by_key = {}
            dependencies_by_operation = {}
            for (
                code, name, sequence, stage, duration, outputs, mode,
                fabric_name, dependencies, same_key, different_key,
            ) in standards:
                fabric_type = fabric_types.get(fabric_name) if fabric_name else False
                operation = self.with_context(active_test=False).search([
                    ('profile_id', '=', profile.id),
                    ('code', '=', code),
                    ('fabric_type_id', '=', fabric_type.id if fabric_type else False),
                ], limit=1)
                values = {
                    'name': name,
                    'sequence': sequence,
                    'stage': stage,
                    'duration_hours': duration,
                    # The supplied factory standard explicitly distinguishes
                    # one-worker operations from operations that may be shared
                    # equally by all workers of the department.
                    'distribution_mode': mode,
                    'fabric_type_id': fabric_type.id if fabric_type else False,
                    'same_worker_key': same_key or False,
                    'different_worker_key': different_key or False,
                    'is_seeded_big_moon': True,
                    'active': True,
                }
                preserved_piece_hours = {}
                if operation:
                    preserved_piece_hours = {
                        output.product_id.id: output.piece_duration_hours
                        for output in operation.output_line_ids
                        if float_compare(
                            output.piece_duration_hours,
                            0.0,
                            precision_digits=6,
                        ) > 0
                    }
                    operation.write(values)
                    operation.output_line_ids.unlink()
                else:
                    operation = self.with_context(
                        furniture_mps_allow_empty_output=True,
                    ).create({
                        **values,
                        'profile_id': profile.id,
                        'code': code,
                    })
                self.env['furniture.mrp.mps.operation.output'].create([
                    {
                        'operation_id': operation.id,
                        'product_id': product.id,
                        'quantity': quantity,
                        'piece_duration_hours': (
                            preserved_piece_hours.get(product.id)
                            or duration / sum(
                                item_quantity
                                for _item, item_quantity in outputs
                            )
                        ),
                    }
                    for product, quantity in outputs
                ])
                operations_by_key[(code, fabric_type.id if fabric_type else False)] = operation
                dependencies_by_operation[operation.id] = dependencies
            for operation_id, dependency_codes in dependencies_by_operation.items():
                dependency_operations = self.browse()
                for dependency_code in dependency_codes:
                    dependency_operations |= self.search([
                        ('profile_id', '=', profile.id),
                        ('code', '=', dependency_code),
                        ('active', '=', True),
                    ])
                self.browse(operation_id).dependency_ids = [(6, 0, dependency_operations.ids)]
            desired_operation_ids = {
                operation.id for operation in operations_by_key.values()
            }
            obsolete_seeded = profile.operation_ids.with_context(
                active_test=False,
            ).filtered(lambda operation: (
                operation.is_seeded_big_moon
                and operation.id not in desired_operation_ids
            ))
            obsolete_seeded.write({'active': False})
        return profiles

    @api.model
    def _validate_big_moon_seed_contract(self, profiles):
        """Fail fast when the managed Big Moon standard is incomplete."""
        single_codes = {
            'priming_pair', 'priming_chair',
            'paint_cutting_chair', 'base_prep_pair', 'base_install_pair',
            'cut_pair', 'cut_chair', 'sewing_pair', 'sewing_chair',
            'finishing_pair', 'finishing_chair',
            'upholstery_chair', 'tying_chair',
        }
        same_worker_keys = {
            'paint_cutting_chair': 'big_moon_base_and_chair_paint',
            'base_prep_pair': 'big_moon_base_and_chair_paint',
            'base_install_pair': 'big_moon_base_and_chair_paint',
            'sewing_pair': 'big_moon_sewing',
            'sewing_chair': 'big_moon_sewing',
            'upholstery_chair': 'big_moon_chair_upholstery',
            'tying_chair': 'big_moon_chair_upholstery',
        }
        for profile in profiles:
            seeded = profile.operation_ids.with_context(active_test=False).filtered(
                lambda operation: operation.active and operation.is_seeded_big_moon
            )
            unique_keys = {
                (operation.code, operation.fabric_type_id.id or False)
                for operation in seeded
            }
            if (
                len(profile.product_ids) != 3
                or len(seeded) != 34
                or len(unique_keys) != 34
                or seeded.filtered(lambda operation: not operation.output_line_ids)
                or seeded.filtered(
                    lambda operation: (
                        operation.distribution_mode
                        != (
                            'single'
                            if operation.code in single_codes
                            else 'stage_pool_equal'
                        )
                        or (operation.same_worker_key or False)
                        != same_worker_keys.get(operation.code, False)
                        or (operation.different_worker_key or False)
                        != (
                            'big_moon_priming_workers'
                            if operation.code in {
                                'priming_pair', 'priming_chair',
                            }
                            else False
                        )
                    )
                )
            ):
                raise ValidationError(_(
                    'بروفايل MPS لبيج مون غير مكتمل: المطلوب 3 منتجات '
                    'و34 معيار تشغيل فعّالًا وفريدًا وله نواتج، '
                    'مع التزام بعمليات العامل الواحد المحددة.'
                ))
        return True


class FurnitureMrpMPSOperationOutput(models.Model):
    _name = 'furniture.mrp.mps.operation.output'
    _description = 'ناتج معيار عملية MPS'
    _order = 'id'
    _check_company_auto = True

    operation_id = fields.Many2one(
        'furniture.mrp.mps.operation',
        string='العملية',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related='operation_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    profile_id = fields.Many2one(
        related='operation_id.profile_id',
        string='بروفايل التشغيل',
        store=True,
        readonly=True,
        index=True,
    )
    furniture_model_id = fields.Many2one(
        related='operation_id.profile_id.furniture_model_id',
        string='الموديل',
        store=True,
        readonly=True,
        index=True,
    )
    operation_name = fields.Char(
        related='operation_id.name',
        string='الخطوة',
        store=True,
        readonly=True,
        index=True,
    )
    stage = fields.Selection(
        related='operation_id.stage',
        string='المرحلة',
        store=True,
        readonly=True,
        index=True,
    )
    fabric_type_id = fields.Many2one(
        related='operation_id.fabric_type_id',
        string='نوع القماش',
        store=True,
        readonly=True,
        index=True,
    )
    operation_time_is_configured = fields.Boolean(
        related='operation_id.time_is_configured',
        string='الزمن مضبوط',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='المنتج الناتج',
        required=True,
        ondelete='restrict',
        domain=[('furniture_dimension_source_product_id', '=', False)],
    )
    quantity = fields.Float(
        string='كمية الدفعة',
        required=True,
        digits=(16, 3),
    )
    piece_duration_hours = fields.Float(
        string='زمن القطعة المخصص (ساعة)',
        digits=(16, 3),
        default=0.0,
        help=(
            'اتركه صفرًا ليحسب النظام زمن القطعة من زمن الدفعة وعدد قطعها. '
            'اكتب قيمة فقط عندما يكون زمن هذا الصنف مختلفًا عن باقي نواتج العملية.'
        ),
    )
    effective_piece_duration_hours = fields.Float(
        string='زمن القطعة المستخدم (ساعة)',
        compute='_compute_effective_piece_duration_hours',
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        related='product_id.uom_id',
        string='الوحدة',
        readonly=True,
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_operation_output_unique',
            'unique(operation_id, product_id)',
            'المنتج مكرر في ناتج نفس العملية.',
        ),
        (
            'furniture_mrp_mps_operation_output_qty_positive',
            'check(quantity > 0)',
            'كمية ناتج العملية يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            operation_ids = {
                vals.get('operation_id')
                for vals in vals_list
                if vals.get('operation_id')
            }
            if self.env['furniture.mrp.mps.operation'].browse(
                list(operation_ids)
            ).filtered('is_auto_product_standard'):
                raise AccessError(_(
                    'نواتج معيار الصنف التلقائي يديرها MPS فقط.'
                ))
        records = super().create(vals_list)
        if not self.env.context.get('furniture_mps_output_time_internal_sync'):
            records._sync_auto_operation_piece_time()
        return records

    def write(self, vals):
        auto_outputs = self.filtered(
            lambda output: output.operation_id.is_auto_product_standard
        )
        if not self.env.su and auto_outputs:
            forbidden_fields = set(vals) - {'piece_duration_hours'}
            if forbidden_fields:
                raise AccessError(_(
                    'المتاح في معيار الصنف القياسي هو تعديل زمن القطعة فقط.'
                ))
        changed_time = 'piece_duration_hours' in vals
        if changed_time and not self.env.context.get(
            'furniture_mps_output_time_internal_sync'
        ):
            self._assert_output_times_editable()
        result = super().write(vals)
        if changed_time and not self.env.context.get(
            'furniture_mps_output_time_internal_sync'
        ):
            self._sync_auto_operation_piece_time()
            self._replan_output_profiles()
        return result

    def unlink(self):
        if not self.env.su and self.mapped(
            'operation_id'
        ).filtered('is_auto_product_standard'):
            raise AccessError(_(
                'لا يمكن حذف ناتج معيار صنف تلقائي مباشرة.'
            ))
        return super().unlink()

    def _active_profile_productions(self):
        profiles = self.mapped('operation_id.profile_id')
        if not profiles:
            return self.env['furniture.mrp.production']
        return self.env['furniture.mrp.production'].sudo().search([
            ('company_id', 'in', profiles.mapped('company_id').ids),
            ('furniture_order_model_id', 'in', profiles.mapped('furniture_model_id').ids),
            ('state', 'in', ('confirmed', 'in_production')),
            ('mps_schedule_plan_ids.state', 'in', MPS_PLAN_ACTIVE_STATES),
        ]).filtered(lambda production: (
            production.mps_schedule_plan_ids.filtered(
                lambda plan: plan.state in MPS_PLAN_ACTIVE_STATES
            ).profile_id in profiles
        ))

    def _assert_output_times_editable(self):
        stages = set(self.mapped('operation_id.stage'))
        productions = self._active_profile_productions()
        if productions and stages:
            productions._furniture_mps_assert_stages_editable(stages)
        return True

    def _sync_auto_operation_piece_time(self):
        """Make an editable per-product duration drive an automatic step."""
        auto_operations = self.mapped('operation_id').filtered(
            'is_auto_product_standard'
        )
        for operation in auto_operations:
            output = operation.output_line_ids.ensure_one()
            duration = output.piece_duration_hours or 0.0
            operation.sudo().with_context(
                furniture_mps_output_time_internal_sync=True,
            ).write({
                'duration_hours': duration,
                'time_is_configured': float_compare(
                    duration, 0.0, precision_digits=6,
                ) > 0,
            })
        timings = auto_operations.mapped('product_time_id')
        for timing in timings:
            active_operations = timing.operation_ids.filtered('active')
            timing.with_context(
                furniture_mps_time_internal_sync=True,
            ).sudo().write({
                'uses_detailed_standard': True,
                'piece_hours': 0.0,
                'detailed_piece_hours': self.env[
                    'furniture.mrp.mps.product.time'
                ]._detailed_piece_hours(
                    active_operations,
                    timing.product_id,
                ),
            })
        return True

    def _replan_output_profiles(self):
        stages = set(self.mapped('operation_id.stage'))
        productions = self._active_profile_productions()
        if productions:
            productions._furniture_replan_proposed_mps(
                changed_stage_codes=stages,
            )
        return True

    @api.depends(
        'piece_duration_hours',
        'quantity',
        'operation_id.duration_hours',
        'operation_id.output_line_ids.quantity',
        'operation_id.output_line_ids.piece_duration_hours',
    )
    def _compute_effective_piece_duration_hours(self):
        for output in self:
            if output.piece_duration_hours > 0:
                output.effective_piece_duration_hours = output.piece_duration_hours
                continue
            total_batch_pieces = sum(
                output.operation_id.output_line_ids.mapped('quantity')
            )
            output.effective_piece_duration_hours = (
                output.operation_id.duration_hours / total_batch_pieces
                if total_batch_pieces > 0
                else 0.0
            )

    @api.constrains('piece_duration_hours')
    def _check_piece_duration_hours(self):
        if self.filtered(lambda output: output.piece_duration_hours < 0):
            raise ValidationError(_(
                'زمن القطعة المخصص لا يمكن أن يكون سالبًا.'
            ))

    @api.constrains('operation_id', 'product_id')
    def _check_product_in_profile(self):
        for output in self:
            if output.product_id not in output.operation_id.profile_id.product_ids:
                raise ValidationError(_(
                    'منتج ناتج العملية يجب أن يكون ضمن منتجات بروفايل MPS.'
                ))


class FurnitureMrpMPSProductTime(models.Model):
    _name = 'furniture.mrp.mps.product.time'
    _description = 'زمن قطعة الأثاث بكل مرحلة في MPS'
    _order = 'furniture_model_id, source_product_id, stage, id'
    _check_company_auto = True

    bom_id = fields.Many2one(
        'mrp.bom',
        string='ريسيبي الموديل',
        ondelete='set null',
        index=True,
        check_company=True,
        help=(
            'آخر ريسيبي محفوظ أنشأ هذا السطر. يظل زمن الصنف محفوظًا '
            'لو استُبدل الريسيبي ثم يعاد ربطه بالنسخة الجديدة.'
        ),
    )
    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    source_product_id = fields.Many2one(
        'product.product',
        string='الصنف داخل الريسيبي',
        required=True,
        readonly=True,
        index=True,
        ondelete='restrict',
    )
    product_id = fields.Many2one(
        'product.product',
        string='الصنف القياسي',
        required=True,
        ondelete='restrict',
        readonly=True,
        index=True,
    )
    stage = fields.Selection(
        MPS_STAGE_DISPLAY_SELECTION,
        string='المرحلة',
        required=True,
        readonly=True,
        index=True,
    )
    piece_hours = fields.Float(
        string='زمن القطعة (ساعة)',
        digits=(16, 3),
        default=0.0,
        help=(
            'زمن تنفيذ قطعة كاملة واحدة من هذا الصنف في هذه المرحلة. '
            'لا يقسم النظام القطعة الواحدة بين أكثر من عامل.'
        ),
    )
    detailed_piece_hours = fields.Float(
        string='زمن البروفايل التفصيلي (ساعة)',
        digits=(16, 3),
        default=0.0,
        readonly=True,
    )
    active = fields.Boolean(default=True, index=True)
    uses_detailed_standard = fields.Boolean(
        string='محسوب من معيار تفصيلي',
        default=False,
        readonly=True,
        index=True,
        help=(
            'الزمن هنا للعرض فقط لأنه محسوب من خطوات التشغيل التفصيلية '
            'الموجودة بالفعل في بروفايل الموديل.'
        ),
    )
    operation_ids = fields.One2many(
        'furniture.mrp.mps.operation',
        'product_time_id',
        string='معايير التشغيل التلقائية',
        readonly=True,
    )
    time_ready = fields.Boolean(
        string='الأزمنة مكتملة',
        compute='_compute_time_status',
        search='_search_time_ready',
    )
    time_source_label = fields.Char(
        string='مصدر الزمن',
        compute='_compute_time_status',
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_product_time_logical_unique',
            'unique(company_id, furniture_model_id, product_id, stage)',
            'يوجد زمن MPS لهذا الصنف والموديل والمرحلة والشركة بالفعل.',
        ),
    ]

    @api.model
    def _active_stage_codes_for_bom(self, bom):
        # Weekly orders support a custom stage subset that may differ from the
        # recipe defaults.  Keep all eight product-stage timings available;
        # `_build_production_context` selects only the stages actually requested
        # on the order, so unused rows never create work.
        return list(MPS_STAGE_DISPLAY_CODES)

    @api.depends(
        'piece_hours',
        'detailed_piece_hours',
        'uses_detailed_standard',
        'operation_ids.active',
        'operation_ids.time_is_configured',
    )
    def _compute_time_status(self):
        for timing in self:
            active_auto_operations = timing.operation_ids.filtered('active')
            detailed_ready = bool(
                not active_auto_operations
                or not active_auto_operations.filtered(
                    lambda operation: not operation.time_is_configured
                )
            )
            timing.time_ready = bool(
                (timing.uses_detailed_standard and detailed_ready)
                or float_compare(
                    timing.piece_hours,
                    0.0,
                    precision_digits=6,
                ) > 0
            )
            timing.time_source_label = (
                (
                    _('خطوات تفصيلية مكتملة')
                    if detailed_ready
                    else _('خطوات تحتاج تحديد الزمن')
                )
                if timing.uses_detailed_standard
                else (
                    _('مضبوط يدويًا')
                    if timing.time_ready
                    else _('مطلوب تحديد الزمن')
                )
            )

    @api.model
    def _search_time_ready(self, operator, value):
        ready_ids = self.search([]).filtered('time_ready').ids
        positive = (operator in ('=', '==') and bool(value)) or (
            operator in ('!=', '<>') and not bool(value)
        )
        return [('id', 'in' if positive else 'not in', ready_ids)]

    @api.constrains('piece_hours')
    def _check_piece_hours_nonnegative(self):
        if self.filtered(lambda timing: timing.piece_hours < 0):
            raise ValidationError(_(
                'زمن القطعة في أي مرحلة لا يمكن أن يكون سالبًا.'
            ))

    @api.model
    def _eligible_boms(self, furniture_model=None, company=None):
        domain = [
            ('active', '=', True),
            ('type', '=', 'normal'),
            ('furniture_is_model_recipe', '=', True),
            ('furniture_parent_bom_id', '!=', False),
            ('furniture_recipe_ready', '=', True),
            ('furniture_model_id', '!=', False),
            ('furniture_product_id', '!=', False),
        ]
        if furniture_model:
            domain.append(('furniture_model_id', '=', furniture_model.id))
        if company:
            domain.append(('company_id', 'in', (False, company.id)))
        boms = self.env['mrp.bom'].sudo().search(domain, order='id')
        if not company:
            return boms
        # The production resolver prefers a company recipe over a shared one.
        # Keep the same identity here so one product × stage is never scheduled
        # twice when both recipes exist.
        selected = {}
        for bom in boms:
            canonical = (
                bom.furniture_product_id.furniture_dimension_source_product_id
                or bom.furniture_product_id
            )
            key = (bom.furniture_model_id.id, canonical.id)
            rank = (bom.company_id == company, bom.id)
            if key not in selected or rank > selected[key][0]:
                selected[key] = (rank, bom)
        return self.env['mrp.bom'].browse([
            selected[key][1].id
            for key in sorted(selected)
        ])

    # One row represents one product × stage: the manager edits one
    # unambiguous whole-piece duration, while detailed standards remain the
    # source of truth where they already exist.
    @api.model
    def _sync_from_boms(self, boms=None, company=None):
        full_sync = boms is None
        all_companies = self.env['res.company'].sudo().search([])
        if full_sync:
            effective_pairs = [
                (bom, target_company)
                for target_company in all_companies
                for bom in self._eligible_boms(company=target_company)
            ]
            boms = self._eligible_boms()
        else:
            eligible_ids = set(self._eligible_boms().ids)
            boms = boms.sudo().filtered(lambda bom: bom.id in eligible_ids)
            if company:
                # The caller normally passed `_eligible_boms(..., company)`;
                # deduplicate defensively for direct API calls as well.
                selected = {}
                for bom in boms.filtered(
                    lambda item: item.company_id in (False, company)
                ):
                    canonical = (
                        bom.furniture_product_id.furniture_dimension_source_product_id
                        or bom.furniture_product_id
                    )
                    key = (bom.furniture_model_id.id, canonical.id)
                    rank = (bom.company_id == company, bom.id)
                    if key not in selected or rank > selected[key][0]:
                        selected[key] = (rank, bom)
                effective_pairs = [
                    (selected[key][1], company)
                    for key in sorted(selected)
                ]
            else:
                effective_pairs = [
                    (bom, target_company)
                    for bom in boms
                    for target_company in (bom.company_id or all_companies)
                ]

        desired_values = {}
        for bom, target_company in effective_pairs:
            product = (
                bom.furniture_product_id.furniture_dimension_source_product_id
                or bom.furniture_product_id
            )
            for stage_code in self._active_stage_codes_for_bom(bom):
                logical_key = (
                    target_company.id,
                    bom.furniture_model_id.id,
                    product.id,
                    stage_code,
                )
                desired_values[logical_key] = {
                    'bom_id': bom.id,
                    'company_id': target_company.id,
                    'furniture_model_id': bom.furniture_model_id.id,
                    'source_product_id': bom.furniture_product_id.id,
                    'product_id': product.id,
                    'stage': stage_code,
                    'active': True,
                }

        Timing = self.sudo().with_context(active_test=False)
        if full_sync:
            existing = Timing.search([])
        elif effective_pairs:
            scope_models = self.env['furniture.product.model'].browse(
                list({bom.furniture_model_id.id for bom, _company in effective_pairs})
            )
            scope_companies = self.env['res.company'].browse(
                list({target.id for _bom, target in effective_pairs})
            )
            existing = Timing.search([
                ('company_id', 'in', scope_companies.ids),
                ('furniture_model_id', 'in', scope_models.ids),
            ])
        else:
            existing = Timing.browse()
        existing_by_key = {
            (
                timing.company_id.id,
                timing.furniture_model_id.id,
                timing.product_id.id,
                timing.stage,
            ): timing
            for timing in existing
        }
        result = Timing.browse()
        for key, values in desired_values.items():
            timing = existing_by_key.get(key)
            if timing:
                timing.with_context(
                    furniture_mps_time_internal_sync=True,
                ).write(values)
            else:
                timing = Timing.with_context(
                    furniture_mps_time_internal_sync=True,
                ).create(values)
            result |= timing

        stale = existing.filtered(lambda timing: (
            (
                timing.company_id.id,
                timing.furniture_model_id.id,
                timing.product_id.id,
                timing.stage,
            )
            not in desired_values
        ))
        if stale:
            stale.with_context(
                furniture_mps_time_internal_sync=True,
            ).write({'active': False})
        return result

    @api.model
    def _detailed_coverage(self, profile, products):
        coverage = defaultdict(
            lambda: self.env['furniture.mrp.mps.operation']
        )
        operations = profile.operation_ids.filtered(lambda operation: (
            operation.active and not operation.is_auto_product_standard
        ))
        product_ids = set(products.ids)
        for operation in operations:
            for output in operation.output_line_ids:
                if output.product_id.id in product_ids:
                    coverage[(output.product_id.id, operation.stage)] |= operation
        return coverage

    @api.model
    def _detailed_piece_hours(self, operations, product):
        """Display a useful per-piece total without double-counting variants."""
        durations_by_code = defaultdict(list)
        for operation in operations:
            output = operation.output_line_ids.filtered(
                lambda line: line.product_id == product
            )[:1]
            if output:
                durations_by_code[operation.code].append(
                    output.effective_piece_duration_hours
                )
        return sum(
            max(durations)
            for durations in durations_by_code.values()
            if durations
        )

    @api.model
    def _standard_fabric_types(self):
        result = {}
        for sequence, name in ((10, 'ليكرا'), (20, 'كتان')):
            fabric_type = self.env['furniture.fabric.work.type'].sudo().with_context(
                active_test=False,
            ).search([('name', '=ilike', name)], limit=1)
            if fabric_type:
                fabric_type.write({'active': True, 'sequence': sequence})
            else:
                fabric_type = self.env['furniture.fabric.work.type'].sudo().create({
                    'name': name,
                    'sequence': sequence,
                })
            result[name] = fabric_type
        return result

    @api.model
    def _sync_standard_product_operations(self, profile, timings):
        """Give every product/model the same editable operational route."""
        profile = profile.ensure_one()
        fabric_types = self._standard_fabric_types()
        Operation = self.env['furniture.mrp.mps.operation'].sudo().with_context(
            active_test=False,
        )
        Output = self.env['furniture.mrp.mps.operation.output'].sudo()
        operations_by_key = {}
        dependency_codes_by_operation = {}
        desired_operation_ids = set()

        timings_by_product_stage = {
            (timing.product_id.id, timing.stage): timing
            for timing in timings
        }
        products = timings.mapped('product_id').sorted(
            lambda product: (product.display_name, product.id)
        )
        for product_index, product in enumerate(products, start=1):
            for (
                step_code, step_name, step_sequence, stage, mode,
                fabric_name, dependency_codes,
            ) in MPS_STANDARD_PRODUCT_STEPS:
                timing = timings_by_product_stage.get((product.id, stage))
                if not timing:
                    continue
                fabric_type = fabric_types.get(fabric_name) if fabric_name else False
                operation_code = 'auto_product_step_%s_%s' % (
                    product.id,
                    step_code,
                )
                operation = Operation.search([
                    ('profile_id', '=', profile.id),
                    ('code', '=', operation_code),
                    ('fabric_type_id', '=', fabric_type.id if fabric_type else False),
                ], limit=1)
                values = {
                    'name': '%s — %s' % (step_name, product.display_name),
                    'sequence': (step_sequence * 100) + product_index,
                    'stage': stage,
                    'distribution_mode': mode,
                    'fabric_type_id': fabric_type.id if fabric_type else False,
                    'same_worker_key': False,
                    'different_worker_key': False,
                    'is_auto_product_standard': True,
                    'product_time_id': timing.id,
                    'active': True,
                }
                if operation:
                    operation.write(values)
                else:
                    operation = Operation.with_context(
                        furniture_mps_allow_empty_output=True,
                    ).create({
                        **values,
                        'profile_id': profile.id,
                        'code': operation_code,
                        'duration_hours': 0.0,
                        'time_is_configured': False,
                    })
                existing_output = operation.output_line_ids.filtered(
                    lambda output: (
                        output.product_id == product
                        and float_compare(
                            output.quantity,
                            1.0,
                            precision_digits=6,
                        ) == 0
                    )
                )[:1]
                if len(operation.output_line_ids) != 1 or not existing_output:
                    preserved_duration = (
                        operation.output_line_ids.filtered(
                            lambda output: output.product_id == product
                        )[:1].piece_duration_hours
                        or 0.0
                    )
                    operation.output_line_ids.unlink()
                    existing_output = Output.with_context(
                        furniture_mps_output_time_internal_sync=True,
                    ).create({
                        'operation_id': operation.id,
                        'product_id': product.id,
                        'quantity': 1.0,
                        'piece_duration_hours': preserved_duration,
                    })
                operations_by_key.setdefault(
                    (product.id, step_code),
                    self.env['furniture.mrp.mps.operation'],
                )
                operations_by_key[(product.id, step_code)] |= operation
                dependency_codes_by_operation[operation.id] = (
                    product.id,
                    dependency_codes,
                )
                desired_operation_ids.add(operation.id)

        for operation_id, (
            product_id,
            dependency_codes,
        ) in dependency_codes_by_operation.items():
            dependencies = self.env['furniture.mrp.mps.operation']
            for dependency_code in dependency_codes:
                dependencies |= operations_by_key.get(
                    (product_id, dependency_code),
                    self.env['furniture.mrp.mps.operation'],
                )
            Operation.browse(operation_id).dependency_ids = [
                (6, 0, dependencies.ids)
            ]

        obsolete = Operation.search([
            ('profile_id', '=', profile.id),
            ('is_auto_product_standard', '=', True),
            ('id', 'not in', list(desired_operation_ids)),
        ])
        if obsolete:
            obsolete.write({'active': False})

        outputs = Operation.browse(list(desired_operation_ids)).mapped(
            'output_line_ids'
        )
        outputs.with_context(
            furniture_mps_output_time_internal_sync=True,
        )._sync_auto_operation_piece_time()
        return Operation.browse(list(desired_operation_ids))

    @api.model
    def _ensure_auto_profile(self, furniture_model, company):
        furniture_model = furniture_model.ensure_one()
        company = company.ensure_one()
        # Serialize lazy creation when two confirmations for the same model are
        # processed concurrently.
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)',
            [company.id, furniture_model.id],
        )
        boms = self._eligible_boms(furniture_model, company)
        timings = self._sync_from_boms(boms, company=company).filtered(
            lambda timing: timing.active and timing.company_id == company
        )
        if not timings:
            return self.env['furniture.mrp.mps.profile']

        all_products = timings.mapped('product_id')
        Profile = self.env['furniture.mrp.mps.profile'].sudo().with_context(
            active_test=False,
        )
        detailed_profiles = Profile.search([
            ('company_id', '=', company.id),
            ('furniture_model_id', '=', furniture_model.id),
            ('is_auto_product_profile', '=', False),
            ('active', '=', True),
        ]).filtered(lambda candidate: set(all_products.ids).issubset(
            set(candidate.product_ids.ids)
        )).sorted(lambda candidate: (candidate.sequence, candidate.id))
        auto_profiles = Profile.search([
            ('company_id', '=', company.id),
            ('furniture_model_id', '=', furniture_model.id),
            ('is_auto_product_profile', '=', True),
        ]).sorted(lambda candidate: (not candidate.active, candidate.id))

        if detailed_profiles:
            profile = detailed_profiles[:1]
            auto_profiles.write({'active': False})
        else:
            profile = auto_profiles[:1]
            (auto_profiles - profile).write({'active': False})
            profile_values = {
                'name': _('%s — أزمنة الأصناف بالمراحل')
                % furniture_model.display_name,
                'sequence': 1000,
                'active': True,
                'is_auto_product_profile': True,
                'product_ids': [(6, 0, all_products.ids)],
                'notes': False,
            }
            if profile:
                profile.write(profile_values)
            else:
                profile = Profile.create({
                    **profile_values,
                    'company_id': company.id,
                    'furniture_model_id': furniture_model.id,
                })

            self._sync_standard_product_operations(profile, timings)
            return profile

        detailed_coverage = self._detailed_coverage(profile, all_products)
        desired_timings = self.browse()
        for timing in timings:
            detailed_operations = detailed_coverage.get(
                (timing.product_id.id, timing.stage),
                self.env['furniture.mrp.mps.operation'],
            )
            if detailed_operations:
                timing.with_context(
                    furniture_mps_time_internal_sync=True,
                ).write({
                    'uses_detailed_standard': True,
                    # Never let a derived value become a silent manual default
                    # if the detailed operation is removed later.
                    'piece_hours': 0.0,
                    'detailed_piece_hours': self._detailed_piece_hours(
                        detailed_operations,
                        timing.product_id,
                    ),
                })
            else:
                timing.with_context(
                    furniture_mps_time_internal_sync=True,
                ).write({
                    'uses_detailed_standard': False,
                    'detailed_piece_hours': 0.0,
                })
                desired_timings |= timing

        Operation = self.env['furniture.mrp.mps.operation'].sudo().with_context(
            active_test=False,
        )
        desired_operation_ids = set()
        sorted_timings = desired_timings.sorted(lambda timing: (
            MPS_AUTO_STAGE_SEQUENCE[timing.stage],
            timing.product_id.display_name,
            timing.id,
        ))
        for item_index, timing in enumerate(sorted_timings, start=1):
            code = 'auto_product_time_%s' % timing.id
            operation = Operation.search([
                ('profile_id', '=', profile.id),
                ('product_time_id', '=', timing.id),
            ], limit=1)
            configured = float_compare(
                timing.piece_hours, 0.0, precision_digits=6,
            ) > 0
            values = {
                'name': '%s — %s' % (
                    _MPS_STAGE_LABELS[timing.stage],
                    timing.product_id.display_name,
                ),
                'sequence': MPS_AUTO_STAGE_SEQUENCE[timing.stage] + item_index,
                'stage': timing.stage,
                'duration_hours': timing.piece_hours,
                'distribution_mode': 'stage_pool_equal',
                'same_worker_key': False,
                'different_worker_key': False,
                'is_auto_product_standard': True,
                'product_time_id': timing.id,
                'time_is_configured': configured,
                # Generic dependencies are linked from the actual work lines,
                # so an order that skips a stage never gets a false block.
                'dependency_ids': [(5, 0, 0)],
                'active': True,
            }
            if operation:
                operation.write(values)
            else:
                operation = Operation.with_context(
                    furniture_mps_allow_empty_output=True,
                ).create({
                    **values,
                    'profile_id': profile.id,
                    'code': code,
                })
            valid_output = operation.output_line_ids.filtered(lambda output: (
                output.product_id == timing.product_id
                and float_compare(
                    output.quantity, 1.0, precision_digits=6,
                ) == 0
                and len(operation.output_line_ids) == 1
            ))
            if not valid_output:
                operation.output_line_ids.unlink()
                self.env['furniture.mrp.mps.operation.output'].sudo().create({
                    'operation_id': operation.id,
                    'product_id': timing.product_id.id,
                    'quantity': 1.0,
                })
            desired_operation_ids.add(operation.id)

        obsolete = Operation.search([
            ('profile_id', '=', profile.id),
            ('is_auto_product_standard', '=', True),
            ('id', 'not in', list(desired_operation_ids)),
        ])
        obsolete.write({'active': False})
        return profile

    def _sync_profiles_and_replan(self, changed_stage_codes=None):
        stages_by_pair = defaultdict(set)
        for timing in self:
            if timing.furniture_model_id and timing.company_id:
                stages_by_pair[
                    (timing.furniture_model_id.id, timing.company_id.id)
                ].add(timing.stage)
        for (model_id, company_id), timing_stages in stages_by_pair.items():
            model = self.env['furniture.product.model'].browse(model_id)
            company = self.env['res.company'].browse(company_id)
            self._ensure_auto_profile(model, company)
            productions = self.env['furniture.mrp.production'].sudo().search([
                ('company_id', '=', company_id),
                ('furniture_order_model_id', '=', model_id),
                ('state', 'in', ('confirmed', 'in_production')),
                ('mps_schedule_plan_ids.state', 'in', MPS_PLAN_ACTIVE_STATES),
            ])
            if productions:
                productions._furniture_replan_proposed_mps(
                    changed_stage_codes=(
                        set(changed_stage_codes)
                        if changed_stage_codes is not None
                        else timing_stages
                    ),
                )
        return True

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_(
                'سطور أزمنة الأصناف تنشأ تلقائيًا من ريسيبيات الموديلات.'
            ))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            forbidden_fields = set(vals) - {'piece_hours'}
            if forbidden_fields:
                raise AccessError(_(
                    'المتاح هنا هو تعديل زمن القطعة فقط؛ بيانات الصنف '
                    'والموديل والمرحلة يولدها النظام من الريسيبي.'
                ))
            if 'piece_hours' in vals and self.filtered(
                'uses_detailed_standard'
            ):
                raise ValidationError(_(
                    'هذا الزمن محسوب من بروفايل تفصيلي. عدّل معيار العملية '
                    'التفصيلي نفسه بدل كتابة زمن إجمالي فوقه.'
                ))
        internal_sync = self.env.context.get(
            'furniture_mps_time_internal_sync'
        )
        changed_stages = set(self.mapped('stage')) if (
            'piece_hours' in vals and not internal_sync
        ) else set()
        if changed_stages:
            pairs = {
                (timing.furniture_model_id.id, timing.company_id.id)
                for timing in self
                if timing.furniture_model_id and timing.company_id
            }
            for model_id, company_id in pairs:
                productions = self.env[
                    'furniture.mrp.production'
                ].sudo().search([
                    ('company_id', '=', company_id),
                    ('furniture_order_model_id', '=', model_id),
                    ('state', 'in', ('confirmed', 'in_production')),
                    ('mps_schedule_plan_ids.state', 'in', MPS_PLAN_ACTIVE_STATES),
                ])
                productions._furniture_mps_assert_stages_editable(
                    changed_stages
                )
        result = super().write(vals)
        if (
            {'piece_hours', 'active'}.intersection(vals)
            and not internal_sync
        ):
            self.sudo()._sync_profiles_and_replan(
                changed_stage_codes=changed_stages or None,
            )
        return result

    def unlink(self):
        if not self.env.su:
            raise AccessError(_(
                'لا يمكن حذف سطر زمن صنف؛ أرشف الريسيبي المرتبط عند الحاجة.'
            ))
        return super().unlink()


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    @api.model
    def _furniture_mps_timing_sync_fields(self):
        return {
            'active',
            'type',
            'company_id',
            'furniture_is_model_recipe',
            'furniture_parent_bom_id',
            'furniture_recipe_ready',
            'furniture_model_id',
            'furniture_product_id',
        } | set(self._furniture_recipe_content_field_names())

    def _furniture_mps_recipe_pairs(self):
        companies = self.env['res.company'].sudo().search([])
        pairs = set()
        for bom in self.filtered(lambda item: (
            item.furniture_is_model_recipe and item.furniture_model_id
        )):
            target_companies = bom.company_id or companies
            pairs.update(
                (bom.furniture_model_id.id, company.id)
                for company in target_companies
            )
        return pairs

    @api.model
    def _furniture_sync_mps_product_timings(self, pairs):
        if self.env.context.get('furniture_skip_mps_timing_sync'):
            return True
        Timing = self.env['furniture.mrp.mps.product.time'].sudo().with_context(
            furniture_skip_mps_timing_sync=True,
        )
        for model_id, company_id in sorted(pairs):
            model = self.env['furniture.product.model'].browse(model_id)
            company = self.env['res.company'].browse(company_id)
            boms = Timing._eligible_boms(model, company)
            if boms:
                Timing._sync_from_boms(boms, company=company)
                Timing._ensure_auto_profile(model, company)
                continue
            stale_timings = Timing.with_context(active_test=False).search([
                ('company_id', '=', company_id),
                ('furniture_model_id', '=', model_id),
            ])
            if stale_timings:
                stale_timings.with_context(
                    furniture_mps_time_internal_sync=True,
                ).write({'active': False})
            stale_auto_profiles = self.env[
                'furniture.mrp.mps.profile'
            ].sudo().search([
                ('company_id', '=', company_id),
                ('furniture_model_id', '=', model_id),
                ('is_auto_product_profile', '=', True),
                ('active', '=', True),
            ])
            if stale_auto_profiles:
                stale_auto_profiles.write({'active': False})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        recipes = records.filtered(lambda bom: (
            bom.furniture_is_model_recipe
            and bom.furniture_recipe_ready
        ))
        pairs = recipes._furniture_mps_recipe_pairs()
        if pairs:
            records._furniture_sync_mps_product_timings(pairs)
        return records

    def write(self, vals):
        trigger_sync = bool(
            set(vals).intersection(self._furniture_mps_timing_sync_fields())
        )
        previous_pairs = (
            self._furniture_mps_recipe_pairs() if trigger_sync else set()
        )
        result = super().write(vals)
        if trigger_sync:
            current_pairs = self._furniture_mps_recipe_pairs()
            pairs = previous_pairs | current_pairs
            if pairs:
                self._furniture_sync_mps_product_timings(pairs)
        return result

    def unlink(self):
        pairs = self._furniture_mps_recipe_pairs()
        result = super().unlink()
        if pairs:
            self._furniture_sync_mps_product_timings(pairs)
        return result


class FurnitureMrpMPSPlan(models.Model):
    _name = 'furniture.mrp.mps.plan'
    _description = 'خطة تشغيل MPS لأمر الإنتاج'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'planned_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='المرجع',
        required=True,
        default='جديد',
        readonly=True,
        copy=False,
        index=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
        tracking=True,
    )
    profile_id = fields.Many2one(
        'furniture.mrp.mps.profile',
        string='بروفايل التشغيل',
        required=True,
        ondelete='restrict',
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related='production_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    furniture_model_id = fields.Many2one(
        related='profile_id.furniture_model_id',
        string='الموديل',
        store=True,
        readonly=True,
        index=True,
    )
    state = fields.Selection(
        [
            ('planned', 'مقترح'),
            ('approved', 'معتمد'),
            ('in_progress', 'جاري التنفيذ'),
            ('done', 'مكتمل'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة',
        default='planned',
        required=True,
        tracking=True,
        index=True,
    )
    planned_start = fields.Datetime(string='بداية الخطة', required=True, index=True)
    planned_end = fields.Datetime(
        string='نهاية الخطة المتوقعة',
        compute='_compute_plan_totals',
        store=True,
        index=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.mps.work',
        'plan_id',
        string='عمليات أمر الإنتاج',
        copy=False,
    )
    assignment_ids = fields.One2many(
        'furniture.mrp.mps.assignment',
        'plan_id',
        string='جدول العمال',
        copy=False,
    )
    total_labor_hours = fields.Float(
        string='إجمالي ساعات العمل',
        compute='_compute_plan_totals',
        store=True,
        digits=(16, 2),
    )
    employee_count = fields.Integer(
        string='عدد العمال',
        compute='_compute_plan_totals',
        store=True,
    )
    blocked_line_count = fields.Integer(
        string='عمليات تحتاج تدخل',
        compute='_compute_plan_totals',
        store=True,
    )
    has_warnings = fields.Boolean(
        string='يوجد تنبيهات',
        compute='_compute_plan_totals',
        store=True,
    )
    warning_message = fields.Text(
        string='تنبيهات الخطة',
        compute='_compute_warning_message',
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_plan_production_unique',
            'unique(production_id)',
            'يوجد جدول MPS مرتبط بأمر الإنتاج بالفعل.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'furniture.mrp.mps.plan'
                ) or 'جديد'
        return super().create(vals_list)

    @api.depends(
        'line_ids.labor_hours',
        'line_ids.planned_end',
        'line_ids.state',
        'line_ids.warning_message',
        'assignment_ids.employee_id',
        'profile_id.notes',
    )
    def _compute_plan_totals(self):
        for plan in self:
            plan.total_labor_hours = sum(plan.line_ids.mapped('labor_hours'))
            plan.employee_count = len(plan.assignment_ids.mapped('employee_id'))
            plan.blocked_line_count = len(plan.line_ids.filtered(
                lambda line: line.state == 'blocked'
            ))
            plan.has_warnings = bool(
                plan.profile_id.notes
                or plan.line_ids.filtered(lambda line: line.warning_message)
            )
            ends = [value for value in plan.line_ids.mapped('planned_end') if value]
            plan.planned_end = max(ends) if ends else False

    @api.depends('profile_id.notes', 'line_ids.warning_message', 'line_ids.state')
    def _compute_warning_message(self):
        for plan in self:
            messages = []
            if plan.profile_id.notes:
                messages.append(plan.profile_id.notes.strip())
            for line in plan.line_ids.sorted(lambda item: (item.sequence, item.id)):
                if not line.warning_message:
                    continue
                message = '%s: %s' % (line.name, line.warning_message.strip())
                if message not in messages:
                    messages.append(message)
            plan.warning_message = '\n'.join(messages) or False

    def action_view_production(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.production_id.name,
            'res_model': 'furniture.mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_assignments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('خطة مراحل وعمال %s') % self.production_id.name,
            'res_model': 'furniture.mrp.mps.assignment',
            'domain': [('plan_id', '=', self.id)],
            'view_mode': 'kanban,list,calendar,pivot,form',
            'context': {
                'search_default_active_schedule': 1,
                'search_default_group_stage': 1,
            },
            'target': 'current',
        }

    def action_approve(self):
        for plan in self:
            if plan.state not in ('planned', 'in_progress'):
                raise UserError(_(
                    'يمكن اعتماد الخطة المقترحة أو الأعمال الجديدة غير المبدوءة '
                    'داخل خطة جارية فقط.'
                ))
            blocked = plan.line_ids.filtered(lambda line: line.state == 'blocked')
            if blocked:
                raise UserError(_(
                    'لا يمكن اعتماد الخطة قبل معالجة العمليات غير المجدولة:\n%s'
                ) % '\n'.join(blocked.mapped('name')))
            proposed_assignments = plan.assignment_ids.filtered(
                lambda assignment: assignment.state == 'proposed'
            )
            employee_ids = proposed_assignments.mapped('employee_id').ids
            if employee_ids:
                self.env.cr.execute(
                    'SELECT id FROM hr_employee WHERE id IN %s ORDER BY id FOR UPDATE',
                    [tuple(employee_ids)],
                )
            proposed_assignments._validate_planned_slot()
            proposed_assignments.write({'state': 'approved'})
            plan.line_ids.filtered(
                lambda line: line.state == 'proposed'
            ).write({'state': 'approved'})
            if plan.state == 'planned':
                plan.state = 'approved'
            plan.message_post(body=_('✅ تم اعتماد توزيع عمال MPS.'))
        return True

    def action_replan(self):
        for plan in self:
            if plan.state in ('done', 'cancelled'):
                raise UserError(_('لا يمكن إعادة جدولة خطة منتهية أو ملغاة.'))
            previous_state = plan.state
            plan.planned_start = max(
                fields.Datetime.to_datetime(plan.planned_start),
                fields.Datetime.to_datetime(fields.Datetime.now()),
            )
            plan._regenerate_unstarted_work()
            plan.state = 'in_progress' if previous_state == 'in_progress' else 'planned'
            plan.message_post(body=_('🔄 تمت إعادة جدولة العمل غير المبدوء.'))
        return True

    def action_cancel(self):
        for plan in self:
            if plan.state == 'done':
                raise UserError(_('لا يمكن إلغاء خطة مكتملة.'))
            if (
                plan.production_id.state in ('confirmed', 'in_production')
                and not self.env.context.get('furniture_mps_production_cancel')
            ):
                raise UserError(_(
                    'لا تُلغِ خطة MPS وحدها وأمر الإنتاج ما زال نشطًا؛ '
                    'ألغِ أمر الإنتاج نفسه أو أعد جدولة الخطة.'
                ))
            plan.assignment_ids.filtered(
                lambda assignment: assignment.state != 'done'
            ).write({'state': 'cancelled'})
            plan.line_ids.filtered(
                lambda line: line.state != 'done'
            ).write({'state': 'cancelled'})
            plan.state = 'cancelled'
        return True

    def _canonical_product(self, product):
        return product.furniture_dimension_source_product_id or product

    def _production_line_fabric_type(self, production_line):
        allocations = production_line.tailoring_material_allocation_ids.filtered(
            lambda allocation: allocation.material_kind == 'fabric'
        )
        products = allocations.mapped('product_id')
        if not products:
            products = production_line.material_line_ids.filtered(
                lambda material: (
                    material.stage == 'tailoring'
                    and material.product_id.furniture_tailoring_material_kind == 'fabric'
                )
            ).mapped('product_id')
        if not products:
            return False, _('لم يتم اختيار خامة قماش لهذا الصنف.')
        missing = products.filtered(lambda product: not product.furniture_fabric_type_id)
        if missing:
            return False, _(
                'حدد نوع القماش التشغيلي على الخامة: %s'
            ) % '، '.join(missing.mapped('display_name'))
        types = products.mapped('furniture_fabric_type_id')
        if len(types) != 1:
            return False, _(
                'الخامات المختارة لنفس الصنف تحمل أكثر من نوع قماش تشغيلي.'
            )
        return types, False

    def _build_production_context(self):
        self.ensure_one()
        stage_quantities = defaultdict(lambda: defaultdict(float))
        fabric_types_by_product = defaultdict(set)
        fabric_errors_by_product = defaultdict(list)
        profile_products = self.profile_id.product_ids
        out_of_profile = self.env['product.product']
        for production_line in self.production_id.production_line_ids.filtered(
            lambda line: line.active and float_compare(
                line.product_qty or 0.0, 0.0, precision_digits=6,
            ) > 0
        ):
            product = self._canonical_product(production_line.product_id)
            if product not in profile_products:
                out_of_profile |= product
                continue
            for stage_code, _stage_label in FURNITURE_STAGE_SELECTION:
                if production_line['use_%s' % stage_code]:
                    stage_quantities[stage_code][product.id] += production_line.product_qty or 0.0
            fabric_type, fabric_error = self._production_line_fabric_type(production_line)
            if fabric_type:
                fabric_types_by_product[product.id].add(fabric_type.id)
            if fabric_error:
                fabric_errors_by_product[product.id].append(fabric_error)
        return {
            'stage_quantities': stage_quantities,
            'fabric_types_by_product': fabric_types_by_product,
            'fabric_errors_by_product': fabric_errors_by_product,
            'out_of_profile': out_of_profile,
        }

    def _operation_scale(self, operation, quantity_map):
        ratios = []
        missing_outputs = self.env['product.product']
        for output in operation.output_line_ids:
            required_qty = quantity_map.get(output.product_id.id, 0.0)
            if required_qty > 0 and output.quantity > 0:
                ratios.append(required_qty / output.quantity)
            elif required_qty <= 0:
                missing_outputs |= output.product_id
        if not ratios:
            return 0.0, False, False
        warning = False
        blocked = bool(missing_outputs)
        if blocked:
            warning = _(
                'المعيار عملية مشتركة، لكن أمر الإنتاج لا يحتوي الكمية المطلوبة من: %s.'
            ) % '، '.join(missing_outputs.mapped('display_name'))
        elif len(ratios) > 1 and float_compare(
            max(ratios), min(ratios), precision_digits=3,
        ) != 0:
            warning = _(
                'نسبة كميات المنتجات لا تطابق الدفعة المشتركة؛ صحح الكميات أو أضف معيارًا مستقلًا.'
            )
            blocked = True
        return max(ratios), warning, blocked

    @api.model
    def _operation_atom_metrics(self, operation, scale):
        """Describe an indivisible whole-piece work atom for one operation.

        A standard that produces 9 sofas + 9 chaises has 9 atoms; every atom
        contains one complete sofa and one complete chaise.  A 40-chair
        standard has 40 atoms of one complete chair.  This preserves the
        measured batch duration without ever assigning a fraction of a piece.
        """
        standard_counts = []
        for output in operation.output_line_ids:
            rounded_quantity = int(round(output.quantity or 0.0))
            if (
                rounded_quantity <= 0
                or abs((output.quantity or 0.0) - rounded_quantity) > 1e-6
            ):
                return {
                    'warning': _(
                        'كمية الدفعة القياسية للصنف %(product)s يجب أن تكون '
                        'عدد قطع صحيحًا، وليست %(quantity)s.'
                    ) % {
                        'product': output.product_id.display_name,
                        'quantity': output.quantity,
                    },
                }
            standard_counts.append(rounded_quantity)
        if not standard_counts:
            return {'warning': _('العملية لا تحتوي ناتج قطع صالحًا للجدولة.')}

        atoms_per_standard_batch = reduce(gcd, standard_counts)
        required_atom_count_float = scale * atoms_per_standard_batch
        required_atom_count = int(round(required_atom_count_float))
        if (
            required_atom_count <= 0
            or abs(required_atom_count_float - required_atom_count) > 1e-6
        ):
            return {
                'warning': _(
                    'كميات أمر الإنتاج لا تكوّن عددًا صحيحًا من قطع/دفعات '
                    'العملية «%(operation)s»؛ صحح الكميات بدل توزيع جزء من قطعة.'
                ) % {'operation': operation.display_name},
            }

        atom_outputs = []
        atom_hours = 0.0
        pieces_per_atom = 0
        for output, standard_count in zip(
            operation.output_line_ids,
            standard_counts,
        ):
            quantity_per_atom = standard_count // atoms_per_standard_batch
            piece_hours = output.effective_piece_duration_hours
            atom_outputs.append({
                'product': output.product_id,
                'quantity': quantity_per_atom,
                'piece_hours': piece_hours,
            })
            pieces_per_atom += quantity_per_atom
            atom_hours += quantity_per_atom * piece_hours
        if float_compare(atom_hours, 0.0, precision_digits=6) <= 0:
            return {
                'warning': _(
                    'زمن القطعة في العملية «%s» غير مضبوط.'
                ) % operation.display_name,
                'atom_count': required_atom_count,
                'atom_hours': 0.0,
                'atom_outputs': atom_outputs,
                'piece_count': required_atom_count * pieces_per_atom,
                'labor_hours': 0.0,
            }
        return {
            'warning': False,
            'atom_count': required_atom_count,
            'atom_hours': atom_hours,
            'atom_outputs': atom_outputs,
            'piece_count': required_atom_count * pieces_per_atom,
            'labor_hours': required_atom_count * atom_hours,
        }

    @api.model
    def _operation_output_summary(self, operation, quantity_map):
        pieces = []
        for output in operation.output_line_ids:
            quantity = quantity_map.get(output.product_id.id, 0.0)
            if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                continue
            quantity_label = ('%.3f' % quantity).rstrip('0').rstrip('.')
            if operation.time_is_configured:
                duration_label = (
                    '%.3f' % output.effective_piece_duration_hours
                ).rstrip('0').rstrip('.')
                pieces.append(
                    '%s × %s — %s س/قطعة' % (
                        output.product_id.display_name,
                        quantity_label,
                        duration_label,
                    )
                )
            else:
                pieces.append(
                    '%s × %s — الزمن غير محدد' % (
                        output.product_id.display_name,
                        quantity_label,
                    )
                )
        return '، '.join(pieces) or False

    def _typed_operation_choice(self, operations, production_context):
        representative = operations[0]
        quantity_map = production_context['stage_quantities'][representative.stage]
        relevant_product_ids = [
            output.product_id.id
            for output in representative.output_line_ids
            if quantity_map.get(output.product_id.id, 0.0) > 0
        ]
        if not relevant_product_ids:
            return False, False
        type_ids = set()
        errors = []
        for product_id in relevant_product_ids:
            product_types = production_context['fabric_types_by_product'].get(product_id, set())
            type_ids.update(product_types)
            errors.extend(production_context['fabric_errors_by_product'].get(product_id, []))
            if not product_types and not production_context['fabric_errors_by_product'].get(product_id):
                errors.append(_('نوع القماش غير محدد.'))
        if errors or len(type_ids) != 1:
            if len(type_ids) > 1:
                errors.append(_('المنتجات داخل نفس الدفعة لها أنواع قماش مختلفة.'))
            return representative, ' '.join(dict.fromkeys(errors))
        selected_type_id = next(iter(type_ids))
        selected = operations.filtered(
            lambda operation: operation.fabric_type_id.id == selected_type_id
        )
        if not selected:
            fabric_type = self.env['furniture.fabric.work.type'].browse(selected_type_id)
            return representative, _(
                'لا يوجد معيار تشغيل لنوع القماش: %s.'
            ) % fabric_type.display_name
        return selected[0], False

    def _prepare_work_line_values(
        self,
        production_context,
        locked_operation_codes,
        locked_stage_codes=None,
    ):
        self.ensure_one()
        locked_stage_codes = set(locked_stage_codes or ())
        operations = self.profile_id.operation_ids.filtered('active').sorted(
            lambda operation: (operation.sequence, operation.id)
        )
        grouped = defaultdict(lambda: self.env['furniture.mrp.mps.operation'])
        for operation in operations:
            grouped[operation.code] |= operation
        prepared = []
        for _code, variants in sorted(
            grouped.items(),
            key=lambda item: (min(item[1].mapped('sequence')), min(item[1].ids)),
        ):
            typed = variants.filtered('fabric_type_id')
            if typed:
                operation, blocking_warning = self._typed_operation_choice(
                    typed, production_context,
                )
            else:
                operation = variants[0]
                blocking_warning = False
            if (
                not operation
                or operation.code in locked_operation_codes
                or operation.stage in locked_stage_codes
            ):
                continue
            quantity_map = production_context['stage_quantities'][operation.stage]
            scale, ratio_warning, ratio_blocked = self._operation_scale(
                operation, quantity_map,
            )
            if scale <= 0:
                continue
            atom_metrics = self._operation_atom_metrics(operation, scale)
            atom_warning = atom_metrics.get('warning')
            missing_time_warning = (
                _(
                    'حدد زمن القطعة للصنف %(product)s في مرحلة %(stage)s '
                    'من شاشة «أزمنة الأصناف بالمراحل».'
                ) % {
                    'product': operation.product_time_id.product_id.display_name,
                    'stage': _MPS_STAGE_LABELS.get(operation.stage, operation.stage),
                }
                if not operation.time_is_configured and operation.product_time_id
                else False
            )
            warnings = [
                warning
                for warning in (
                    blocking_warning,
                    ratio_warning,
                    missing_time_warning or atom_warning,
                )
                if warning
            ]
            prepared.append({
                'plan_id': self.id,
                'operation_id': operation.id,
                'name': operation.name,
                'sequence': operation.sequence,
                'stage': operation.stage,
                'distribution_mode': operation.distribution_mode,
                'standard_duration_hours': operation.duration_hours,
                'same_worker_key': operation.same_worker_key or False,
                'different_worker_key': operation.different_worker_key or False,
                'output_summary': self._operation_output_summary(
                    operation, quantity_map,
                ),
                'scale': scale,
                'atom_count': atom_metrics.get('atom_count', 0),
                'atom_hours': atom_metrics.get('atom_hours', 0.0),
                'piece_count': atom_metrics.get('piece_count', 0),
                'labor_hours': atom_metrics.get(
                    'labor_hours',
                    operation.duration_hours * scale,
                ),
                'fabric_type_id': (
                    operation.fabric_type_id.id
                    if operation.fabric_type_id and not blocking_warning
                    else False
                ),
                'state': (
                    'blocked'
                    if (
                        blocking_warning
                        or ratio_blocked
                        or atom_warning
                        or missing_time_warning
                    )
                    else 'proposed'
                ),
                'warning_message': ' '.join(warnings) or False,
            })
        return prepared

    def _eligible_workers(self, stage_code, work_datetime):
        self.ensure_one()
        employees = self.env['hr.employee'].sudo().search([
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('furniture_mrp_role', '=', 'worker'),
            ('furniture_mrp_worker_stage_ids.code', '=', stage_code),
            ('user_id', '!=', False),
            ('user_id.active', '=', True),
            ('user_id.share', '=', False),
            ('resource_calendar_id', '!=', False),
        ], order='id')
        if not employees:
            return employees
        work_date = fields.Date.to_date(work_datetime)
        valid_contracts = self.env['hr.contract'].sudo().search([
            ('employee_id', 'in', employees.ids),
            ('state', '=', 'open'),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', work_date),
        ])
        employee_ids = set(valid_contracts.mapped('employee_id').ids)
        return employees.filtered(
            lambda employee: employee.id in employee_ids
        )

    @api.model
    def _employee_open_contracts(self, employee, from_datetime):
        from_date = fields.Date.to_date(from_datetime)
        return self.env['hr.contract'].sudo().search([
            ('employee_id', '=', employee.id),
            ('state', '=', 'open'),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', from_date),
        ], order='date_start, id')

    @api.model
    def _contract_covering_slot(self, contracts, slot_start, slot_end):
        start_date = fields.Date.to_date(slot_start)
        end_date = fields.Date.to_date(slot_end)
        return contracts.filtered(lambda contract: (
            (not contract.date_start or contract.date_start <= start_date)
            and (not contract.date_end or contract.date_end >= end_date)
        ))[:1]

    @api.model
    def _next_contract_start(self, contracts, cursor):
        cursor_date = fields.Date.to_date(cursor)
        future = contracts.filtered(lambda contract: (
            contract.date_start and contract.date_start > cursor_date
        ))[:1]
        if not future:
            return False
        return datetime.combine(future.date_start, time.min)

    def _calendar_slot(self, employee, start_datetime, hours, reservations=None):
        """Return the first calendar/contract-valid gap for one employee.

        Reservations are wall-clock envelopes around already planned working
        hours.  Nights and weekly rests inside one long assignment are harmless
        because no other work can be planned by the resource calendar then.
        """
        self.ensure_one()
        cursor = fields.Datetime.to_datetime(start_datetime)
        if hours <= 0:
            return cursor, cursor
        reservations = sorted(
            [
                (
                    fields.Datetime.to_datetime(start),
                    fields.Datetime.to_datetime(end),
                )
                for start, end in (reservations or [])
                if start and end
            ],
            key=lambda interval: (interval[0], interval[1]),
        )
        contracts = self._employee_open_contracts(employee, cursor)
        if not contracts:
            return False, False
        for _attempt in range(512):
            cursor_date = fields.Date.to_date(cursor)
            contract = contracts.filtered(lambda item: (
                (not item.date_start or item.date_start <= cursor_date)
                and (not item.date_end or item.date_end >= cursor_date)
            ))[:1]
            if not contract:
                next_start = self._next_contract_start(contracts, cursor)
                if not next_start:
                    return False, False
                cursor = next_start
                continue
            calendar = (
                contract.resource_calendar_id
                or employee.resource_calendar_id
                or self.company_id.resource_calendar_id
            )
            if not calendar:
                return False, False
            resource = employee.resource_id
            planned_start = calendar.plan_hours(
                0.0,
                cursor,
                compute_leaves=True,
                resource=resource,
            )
            if not planned_start:
                return False, False
            planned_start = fields.Datetime.to_datetime(planned_start)
            planned_end = calendar.plan_hours(
                hours,
                planned_start,
                compute_leaves=True,
                resource=resource,
            )
            if not planned_end:
                return False, False
            planned_end = fields.Datetime.to_datetime(planned_end)
            covering_contract = self._contract_covering_slot(
                contracts, planned_start, planned_end,
            )
            if not covering_contract:
                if contract.date_end:
                    cursor = datetime.combine(
                        contract.date_end + timedelta(days=1),
                        time.min,
                    )
                    continue
                return False, False
            overlap = next(
                (
                    (reserved_start, reserved_end)
                    for reserved_start, reserved_end in reservations
                    if planned_start < reserved_end and planned_end > reserved_start
                ),
                False,
            )
            if overlap:
                cursor = max(cursor, overlap[1])
                continue
            return planned_start, planned_end
        return False, False

    @api.model
    def _current_work_interval_end(self, employee, work_datetime):
        work_datetime = fields.Datetime.to_datetime(work_datetime)
        calendar = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        if not calendar:
            return work_datetime
        resource = employee.resource_id
        aware_start = work_datetime.replace(tzinfo=timezone.utc)
        intervals = calendar._work_intervals_batch(
            aware_start,
            aware_start + timedelta(days=14),
            compute_leaves=True,
            resources=resource,
        ).get(resource.id, ())
        if not intervals:
            return work_datetime
        interval_end = next(iter(intervals))[1]
        if getattr(interval_end, 'tzinfo', None):
            interval_end = interval_end.astimezone(timezone.utc).replace(tzinfo=None)
        return fields.Datetime.to_datetime(interval_end)

    @api.model
    def _open_worker_log_reservations(self, employees, anchor):
        reservations = defaultdict(list)
        if not employees:
            return reservations
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        for model_name in MPS_WORKER_LOG_MODELS:
            logs = self.env[model_name].sudo().search([
                ('employee_id', 'in', employees.ids),
                ('end_datetime', '=', False),
            ])
            for log in logs:
                employee = log.employee_id
                busy_end = self._current_work_interval_end(employee, now)
                busy_start = max(
                    fields.Datetime.to_datetime(log.start_datetime or now),
                    fields.Datetime.to_datetime(anchor),
                )
                if busy_end > busy_start:
                    reservations[employee.id].append((busy_start, busy_end))
        return reservations

    def _active_production_stage_codes(self):
        self.ensure_one()
        active_stage_codes = set()
        production = self.production_id
        for stage_code, (_use_field, order_field, _state_field) in (
            FURNITURE_STAGE_FIELD_MAP.items()
        ):
            stage_order = production[order_field]
            if stage_order and stage_order.state in ('in_progress', 'quality_check'):
                active_stage_codes.add(stage_code)
        sewing_order = getattr(production, 'sewing_order_id', False)
        if sewing_order and sewing_order.state in ('in_progress', 'quality_check'):
            active_stage_codes.add('tailoring')
        return active_stage_codes

    def _locked_work_lines(self):
        self.ensure_one()
        active_stage_codes = self._active_production_stage_codes()
        return self.line_ids.filtered(lambda line: (
            line.state in MPS_PLAN_LOCKED_LINE_STATES
            or line.stage in active_stage_codes
        ))

    def _active_same_worker_assignments(self, worker_keys):
        """Return the active affinity anchors shared by sibling lane plans."""
        self.ensure_one()
        worker_keys = sorted(set(filter(None, worker_keys)))
        if not worker_keys:
            return self.env['furniture.mrp.mps.assignment']
        return self.env['furniture.mrp.mps.assignment'].sudo().search([
            ('company_id', '=', self.company_id.id),
            ('furniture_model_id', '=', self.furniture_model_id.id),
            ('plan_id.profile_id', '=', self.profile_id.id),
            ('plan_id.state', 'in', MPS_PLAN_ACTIVE_STATES),
            ('state', 'in', MPS_ASSIGNMENT_ACTIVE_STATES),
            ('line_id.same_worker_key', 'in', worker_keys),
        ])

    def _schedule_work_lines(self):
        self.ensure_one()
        anchor = fields.Datetime.to_datetime(self.planned_start)
        lines = self.line_ids
        locked_lines = self._locked_work_lines()
        all_workers = self.env['hr.employee']
        workers_by_stage = {}
        for stage_code in set(lines.mapped('stage')):
            workers = self._eligible_workers(stage_code, anchor)
            workers_by_stage[stage_code] = workers
            all_workers |= workers
        if all_workers:
            self.env.cr.execute(
                'SELECT id FROM hr_employee WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(all_workers.ids)],
            )

        active_assignments = self.env['furniture.mrp.mps.assignment'].sudo().search([
            ('state', 'in', MPS_ASSIGNMENT_ACTIVE_STATES),
            ('plan_id.state', 'in', MPS_PLAN_ACTIVE_STATES),
            '|',
            ('planned_end', '>', anchor),
            ('state', '=', 'in_progress'),
            '|',
            ('plan_id', '!=', self.id),
            ('line_id', 'in', locked_lines.ids),
        ])
        load_by_employee = {employee.id: 0.0 for employee in all_workers}
        reservations = self._open_worker_log_reservations(all_workers, anchor)
        for assignment in active_assignments:
            employee_id = assignment.employee_id.id
            if employee_id not in load_by_employee:
                continue
            start_datetime = fields.Datetime.to_datetime(
                assignment.planned_start or anchor
            )
            end_datetime = fields.Datetime.to_datetime(assignment.planned_end or anchor)
            if assignment.state == 'in_progress':
                end_datetime = max(
                    end_datetime,
                    self._current_work_interval_end(
                        assignment.employee_id,
                        fields.Datetime.now(),
                    ),
                )
            if end_datetime > anchor:
                reservations[employee_id].append((start_datetime, end_datetime))
            load_by_employee[employee_id] += assignment.planned_hours or 0.0

        same_worker_keys = set(filter(None, lines.mapped('same_worker_key')))
        same_worker_required_stages = defaultdict(set)
        for operation in self.profile_id.operation_ids.filtered(
            lambda item: (
                item.active and item.same_worker_key in same_worker_keys
            )
        ):
            same_worker_required_stages[operation.same_worker_key].add(
                operation.stage
            )
        for line in lines.filtered('same_worker_key'):
            same_worker_required_stages[line.same_worker_key].add(line.stage)

        employees_by_same_worker_key = defaultdict(
            lambda: self.env['hr.employee']
        )
        for assignment in self._active_same_worker_assignments(
            same_worker_keys
        ):
            employees_by_same_worker_key[
                assignment.line_id.same_worker_key
            ] |= assignment.employee_id
        same_worker = {}
        same_worker_conflicts = {}
        for worker_key, employees in employees_by_same_worker_key.items():
            if len(employees) == 1:
                same_worker[worker_key] = employees
            elif len(employees) > 1:
                same_worker_conflicts[worker_key] = employees

        different_workers = defaultdict(set)
        for assignment in self.assignment_ids.filtered(
            lambda item: item.line_id in locked_lines
        ):
            line = assignment.line_id
            if line.different_worker_key:
                different_workers[line.different_worker_key].add(
                    assignment.employee_id.id
                )

        scheduled = set(locked_lines.ids)
        pending = lines - locked_lines
        for _iteration in range(len(lines) + 1):
            if not pending:
                break
            progress = False
            for line in pending.sorted(lambda item: (item.sequence, item.id)):
                dependencies = line.dependency_line_ids
                if any(dependency.state == 'blocked' for dependency in dependencies):
                    dependency_names = '، '.join(
                        dependencies.filtered(lambda dependency: dependency.state == 'blocked').mapped('name')
                    )
                    line.write({
                        'state': 'blocked',
                        'warning_message': _(
                            'متوقفة لأن العملية السابقة غير مجدولة: %s'
                        ) % dependency_names,
                    })
                    scheduled.add(line.id)
                    pending -= line
                    progress = True
                    continue
                if any(dependency.id not in scheduled for dependency in dependencies):
                    continue
                if line.state == 'blocked':
                    scheduled.add(line.id)
                    pending -= line
                    progress = True
                    continue
                earliest = anchor
                dependency_ends = [
                    value for value in dependencies.mapped('planned_end') if value
                ]
                if dependency_ends:
                    earliest = max(earliest, max(
                        fields.Datetime.to_datetime(value) for value in dependency_ends
                    ))
                workers = workers_by_stage.get(line.stage, self.env['hr.employee'])
                if not workers:
                    line.write({
                        'state': 'blocked',
                        'warning_message': _(
                            'لا يوجد عامل نشط بعقد ساري وتقويم عمل ومؤهل لهذه المرحلة.'
                        ),
                    })
                    scheduled.add(line.id)
                    pending -= line
                    progress = True
                    continue

                created_assignments = self.env['furniture.mrp.mps.assignment']
                if line.distribution_mode == 'single':
                    assignment_payload = line._assignment_payload_for_atoms(
                        line.atom_count,
                    )
                    if not assignment_payload:
                        line.write({
                            'state': 'blocked',
                            'warning_message': _(
                                'تعذر تكوين قطع كاملة لهذه العملية؛ راجع كميات '
                                'أمر الإنتاج ومعيار الناتج.'
                            ),
                        })
                        scheduled.add(line.id)
                        pending -= line
                        progress = True
                        continue
                    selected = False
                    if (
                        line.same_worker_key
                        and line.same_worker_key in same_worker_conflicts
                    ):
                        line.write({
                            'state': 'blocked',
                            'warning_message': _(
                                'شرط نفس العامل متعارض مع خطط MPS نشطة أخرى؛ '
                                'المفتاح %(key)s مرتبط حاليًا بأكثر من عامل: '
                                '%(workers)s.'
                            ) % {
                                'key': line.same_worker_key,
                                'workers': '، '.join(
                                    same_worker_conflicts[
                                        line.same_worker_key
                                    ].mapped('display_name')
                                ),
                            },
                        })
                        scheduled.add(line.id)
                        pending -= line
                        progress = True
                        continue
                    if line.same_worker_key and line.same_worker_key in same_worker:
                        candidate = same_worker[line.same_worker_key]
                        required_stages = same_worker_required_stages.get(
                            line.same_worker_key,
                            {line.stage},
                        )
                        candidate_stages = set(
                            candidate.furniture_mrp_worker_stage_ids.mapped(
                                'code'
                            )
                        )
                        if (
                            candidate not in workers
                            or not required_stages.issubset(candidate_stages)
                        ):
                            line.write({
                                'state': 'blocked',
                                'warning_message': _(
                                    'العامل المطلوب للعملية المشتركة غير مؤهل '
                                    'لكل مراحل شرط نفس العامل: %s.'
                                ) % '، '.join(
                                    dict(FURNITURE_STAGE_SELECTION).get(
                                        stage,
                                        stage,
                                    )
                                    for stage in sorted(required_stages)
                                ),
                            })
                            scheduled.add(line.id)
                            pending -= line
                            progress = True
                            continue
                        selected = candidate
                    candidates = workers
                    if line.same_worker_key and not selected:
                        required_stages = same_worker_required_stages.get(
                            line.same_worker_key,
                            {line.stage},
                        )
                        candidates = candidates.filtered(
                            lambda employee: required_stages.issubset(set(
                                employee.furniture_mrp_worker_stage_ids.mapped(
                                    'code'
                                )
                            ))
                        )
                        if not candidates:
                            line.write({
                                'state': 'blocked',
                                'warning_message': _(
                                    'لا يوجد عامل واحد مؤهل لكل مراحل شرط '
                                    'نفس العامل: %s.'
                                ) % '، '.join(
                                    dict(FURNITURE_STAGE_SELECTION).get(
                                        stage,
                                        stage,
                                    )
                                    for stage in sorted(required_stages)
                                ),
                            })
                            scheduled.add(line.id)
                            pending -= line
                            progress = True
                            continue
                    if line.different_worker_key:
                        unused = workers.filtered(
                            lambda employee: employee.id not in different_workers[line.different_worker_key]
                        )
                        if not unused:
                            line.write({
                                'state': 'blocked',
                                'warning_message': _(
                                    'لا يوجد عامل آخر مؤهل يحقق شرط اختلاف العامل لهذه العملية.'
                                ),
                            })
                            scheduled.add(line.id)
                            pending -= line
                            progress = True
                            continue
                        candidates = unused
                    if not selected:
                        scored = []
                        for employee in candidates:
                            slot_start, slot_end = self._calendar_slot(
                                employee,
                                earliest,
                                assignment_payload['planned_hours'],
                                reservations[employee.id],
                            )
                            if not slot_start or not slot_end:
                                continue
                            scored.append((
                                slot_end,
                                load_by_employee.get(employee.id, 0.0),
                                employee.id,
                                employee,
                                slot_start,
                            ))
                        if not scored:
                            line.write({
                                'state': 'blocked',
                                'warning_message': _(
                                    'لا توجد فتحة صالحة داخل تقويم وعقد العمال المؤهلين.'
                                ),
                            })
                            scheduled.add(line.id)
                            pending -= line
                            progress = True
                            continue
                        _end, _load, _id, selected, slot_start = min(scored)
                        slot_end = _end
                    else:
                        slot_start, slot_end = self._calendar_slot(
                            selected,
                            earliest,
                            assignment_payload['planned_hours'],
                            reservations[selected.id],
                        )
                        if not slot_start or not slot_end:
                            line.write({
                                'state': 'blocked',
                                'warning_message': _(
                                    'العامل المطلوب لا يملك فتحة صالحة داخل تقويمه وعقده.'
                                ),
                            })
                            scheduled.add(line.id)
                            pending -= line
                            progress = True
                            continue
                    created_assignments = self.env['furniture.mrp.mps.assignment'].create({
                        'plan_id': self.id,
                        'line_id': line.id,
                        'employee_id': selected.id,
                        'planned_start': slot_start,
                        'planned_end': slot_end,
                        **assignment_payload,
                    })
                    reservations[selected.id].append((slot_start, slot_end))
                    load_by_employee[selected.id] += assignment_payload['planned_hours']
                    if line.same_worker_key:
                        same_worker[line.same_worker_key] = selected
                    if line.different_worker_key:
                        different_workers[line.different_worker_key].add(selected.id)
                else:
                    available_workers = workers
                    slots = {}
                    while available_workers:
                        allocations = line._whole_piece_allocations(
                            available_workers,
                            load_by_employee,
                        )
                        slots = {}
                        failed_workers = self.env['hr.employee']
                        for employee in available_workers:
                            allocation = allocations.get(employee.id)
                            if not allocation:
                                continue
                            slot_start, slot_end = self._calendar_slot(
                                employee,
                                earliest,
                                allocation['planned_hours'],
                                reservations[employee.id],
                            )
                            if not slot_start or not slot_end:
                                failed_workers |= employee
                                continue
                            slots[employee.id] = (
                                slot_start,
                                slot_end,
                                allocation,
                            )
                        if not failed_workers:
                            break
                        available_workers -= failed_workers
                    values_list = [
                        {
                            'plan_id': self.id,
                            'line_id': line.id,
                            'employee_id': employee_id,
                            'planned_start': slot_start,
                            'planned_end': slot_end,
                            **allocation,
                        }
                        for employee_id, (
                            slot_start,
                            slot_end,
                            allocation,
                        ) in slots.items()
                    ]
                    if values_list:
                        created_assignments = self.env['furniture.mrp.mps.assignment'].create(values_list)
                    for employee_id, (
                        slot_start,
                        slot_end,
                        allocation,
                    ) in slots.items():
                        reservations[employee_id].append((slot_start, slot_end))
                        load_by_employee[employee_id] += allocation['planned_hours']
                if not created_assignments:
                    line.write({
                        'state': 'blocked',
                        'warning_message': _('تعذر إنشاء توزيع عمال لهذه العملية.'),
                    })
                else:
                    line.write({
                        'planned_start': min(created_assignments.mapped('planned_start')),
                        'planned_end': max(created_assignments.mapped('planned_end')),
                        'state': 'proposed',
                    })
                scheduled.add(line.id)
                pending -= line
                progress = True
            if not progress:
                break
        if pending:
            pending.write({
                'state': 'blocked',
                'warning_message': _(
                    'تعذر ترتيب تبعيات العملية؛ راجع دورة أو عملية سابقة غير مكتملة.'
                ),
            })

    def _link_generic_runtime_dependencies(self, all_lines, new_lines):
        """Link generic rows from the stages that actually exist on this order.

        A model recipe may contain eight stages while one production order uses
        only a subset.  Static operation dependencies would falsely block that
        order when an optional predecessor is absent, so generic rows follow
        the real work lines produced for this exact order.  Painting and
        tailoring are independent supply lanes; upholstery is the join of
        finishing and tailoring, while packaging is the join of upholstery and
        painting.
        """
        self.ensure_one()
        lines_by_product_stage = defaultdict(
            lambda: self.env['furniture.mrp.mps.work']
        )
        for line in all_lines:
            for product in line.operation_id.output_line_ids.mapped('product_id'):
                lines_by_product_stage[(product.id, line.stage)] |= line

        def stage_lines(product, stage):
            return lines_by_product_stage.get(
                (product.id, stage),
                self.env['furniture.mrp.mps.work'],
            )

        def actual_stage_dependencies(line, product, dependencies):
            """Add only dependencies represented by this production plan."""
            join_stages = MPS_AUTO_JOIN_DEPENDENCIES.get(line.stage, ())
            if join_stages:
                for dependency_stage in join_stages:
                    dependencies |= stage_lines(product, dependency_stage)
                return dependencies

            for route in MPS_AUTO_STAGE_ROUTES:
                if line.stage not in route:
                    continue
                current_index = route.index(line.stage)
                for previous_stage in reversed(route[:current_index]):
                    previous_lines = stage_lines(product, previous_stage)
                    if previous_lines:
                        dependencies |= previous_lines
                        break
                break
            return dependencies

        for line in new_lines.filtered(
            lambda item: item.operation_id.is_auto_product_standard
        ):
            if line.operation_id.code.startswith('auto_product_step_'):
                product = line.operation_id.product_time_id.product_id
                dependencies = line.dependency_line_ids
                if not dependencies:
                    dependencies = actual_stage_dependencies(
                        line,
                        product,
                        dependencies,
                    )
                line.dependency_line_ids = [(6, 0, dependencies.ids)]
                continue
            product = line.operation_id.product_time_id.product_id
            dependencies = actual_stage_dependencies(
                line,
                product,
                line.dependency_line_ids,
            )
            line.dependency_line_ids = [(6, 0, dependencies.ids)]

            # If a generic row fills a hole inside a detailed profile, bridge
            # it into the next real stage as well.  Otherwise that later
            # detailed work could start in parallel and bypass the missing
            # product-stage pair.
            route = next(
                (
                    candidate_route
                    for candidate_route in MPS_AUTO_STAGE_ROUTES
                    if line.stage in candidate_route
                ),
                (),
            )
            if route:
                current_index = route.index(line.stage)
                for next_stage in route[current_index + 1:]:
                    next_lines = stage_lines(product, next_stage) & new_lines
                    if next_lines:
                        for next_line in next_lines:
                            next_line.dependency_line_ids = [(6, 0, (
                                next_line.dependency_line_ids | line
                            ).ids)]
                        break

            # The two joins cross otherwise independent routes.  Bridge a
            # generated fallback row into a downstream detailed row so the
            # dependency cannot disappear merely because one profile step was
            # missing.
            for next_stage, required_stages in (
                MPS_AUTO_JOIN_DEPENDENCIES.items()
            ):
                if line.stage not in required_stages:
                    continue
                for next_line in stage_lines(product, next_stage) & new_lines:
                    next_line.dependency_line_ids = [(6, 0, (
                        next_line.dependency_line_ids | line
                    ).ids)]
        return True

    def _regenerate_unstarted_work(self):
        for plan in self:
            locked_lines = plan._locked_work_lines()
            removable_lines = plan.line_ids - locked_lines
            removable_lines.unlink()
            production_context = plan._build_production_context()
            values_list = plan._prepare_work_line_values(
                production_context,
                set(locked_lines.mapped('operation_id.code')),
                plan._active_production_stage_codes(),
            )
            new_lines = self.env['furniture.mrp.mps.work'].create(values_list) if values_list else self.env['furniture.mrp.mps.work']
            lines_by_code = defaultdict(lambda: self.env['furniture.mrp.mps.work'])
            for existing_line in (locked_lines | new_lines):
                lines_by_code[existing_line.operation_id.code] |= existing_line
            production_lane = plan.production_id.production_lane
            route_stage_codes = set(
                plan.production_id._required_stage_codes()
            )
            for line in new_lines:
                dependencies = self.env['furniture.mrp.mps.work']
                missing_dependency_codes = []
                dependency_codes = sorted(set(
                    line.operation_id.dependency_ids.mapped('code')
                ))
                for dependency_code in dependency_codes:
                    dependency_lines = lines_by_code.get(
                        dependency_code,
                        self.env['furniture.mrp.mps.work'],
                    )
                    if dependency_lines:
                        dependencies |= dependency_lines
                    else:
                        dependency_operations = (
                            line.operation_id.dependency_ids.filtered(
                                lambda operation: (
                                    operation.code == dependency_code
                                )
                            )
                        )
                        # New FIFO lanes are separate production orders.  A
                        # predecessor that belongs to another exact route is
                        # enforced by the physical hand-off ledger, so it must
                        # not make the downstream order's MPS plan impossible
                        # to approve.  Missing predecessors inside this order
                        # remain a hard configuration error.  Legacy/full
                        # orders intentionally retain their historical
                        # cross-stage dependency behavior.
                        is_external_fifo_dependency = bool(
                            production_lane in MPS_FIFO_HANDOFF_LANES
                            and dependency_operations
                            and all(
                                operation.stage not in route_stage_codes
                                for operation in dependency_operations
                            )
                        )
                        if not is_external_fifo_dependency:
                            missing_dependency_codes.append(dependency_code)
                line.dependency_line_ids = [(6, 0, dependencies.ids)]
                is_standard_product_step = line.operation_id.code.startswith(
                    'auto_product_step_'
                )
                if missing_dependency_codes and not is_standard_product_step:
                    dependency_names = []
                    for dependency_code in missing_dependency_codes:
                        dependency = line.operation_id.dependency_ids.filtered(
                            lambda operation: operation.code == dependency_code
                        )[:1]
                        dependency_names.append(
                            dependency.name if dependency else dependency_code
                        )
                    warning = _(
                        'العملية السابقة المطلوبة غير موجودة في مسار أمر الإنتاج: %s.'
                    ) % '، '.join(dependency_names)
                    line.write({
                        'state': 'blocked',
                        'warning_message': ' '.join(filter(None, [
                            line.warning_message,
                            warning,
                        ])),
                    })
            plan._link_generic_runtime_dependencies(
                locked_lines | new_lines,
                new_lines,
            )
            if production_context['out_of_profile']:
                plan.message_post(body=_(
                    'ℹ️ لم يدخل MPS الأصناف الخارجة عن بروفايل %(profile)s: %(products)s'
                ) % {
                    'profile': plan.profile_id.display_name,
                    'products': '، '.join(
                        production_context['out_of_profile'].mapped('display_name')
                    ),
                })
            plan._schedule_work_lines()
        return True


class FurnitureMrpMPSWork(models.Model):
    _name = 'furniture.mrp.mps.work'
    _description = 'حمل عملية MPS لأمر إنتاج'
    _order = 'plan_id, sequence, id'
    _check_company_auto = True

    plan_id = fields.Many2one(
        'furniture.mrp.mps.plan',
        string='خطة MPS',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    production_id = fields.Many2one(
        related='plan_id.production_id',
        string='أمر الإنتاج',
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='plan_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )

    @api.model
    def _format_mps_quantity(self, quantity):
        return ('%.3f' % (quantity or 0.0)).rstrip('0').rstrip('.') or '0'

    def _whole_piece_atom_metrics(self):
        self.ensure_one()
        if self.atom_count > 0:
            standard_counts = [
                int(round(output.quantity or 0.0))
                for output in self.operation_id.output_line_ids
            ]
            if standard_counts and all(count > 0 for count in standard_counts):
                atoms_per_standard_batch = reduce(gcd, standard_counts)
                # ``scale`` is a presentation snapshot stored with four decimal
                # places.  Reconstruct the exact ratio from the authoritative
                # integer atom count so 3 pieces never become 2.9997 here.
                exact_scale = self.atom_count / atoms_per_standard_batch
                return self.plan_id._operation_atom_metrics(
                    self.operation_id,
                    exact_scale,
                )
        return self.plan_id._operation_atom_metrics(
            self.operation_id,
            self.scale,
        )

    def _assignment_payload_for_atoms(self, atom_count):
        self.ensure_one()
        metrics = self._whole_piece_atom_metrics()
        if metrics.get('warning') or atom_count <= 0:
            return False
        summaries = []
        output_values = []
        piece_count = 0
        for atom_output in metrics['atom_outputs']:
            quantity = atom_output['quantity'] * atom_count
            piece_count += quantity
            duration_label = self._format_mps_quantity(
                atom_output['piece_hours']
            )
            summaries.append(
                '%s × %s — %s س/قطعة' % (
                    atom_output['product'].display_name,
                    quantity,
                    duration_label,
                )
            )
            output_values.append((0, 0, {
                'product_id': atom_output['product'].id,
                'quantity': quantity,
                'piece_duration_hours': atom_output['piece_hours'],
            }))
        return {
            'planned_atom_count': atom_count,
            'piece_count': piece_count,
            'planned_hours': atom_count * metrics['atom_hours'],
            'output_summary': '، '.join(summaries),
            'output_line_ids': output_values,
        }

    def _whole_piece_allocations(self, employees, load_by_employee):
        """Split whole-piece atoms as evenly as possible across the stage pool.

        Every worker receives the common quotient.  At most one extra atom is
        then given to the least-loaded workers, so quantities differ by no
        more than one while prior load still provides a deterministic tie
        breaker for the remainder.  An atom is never split between workers.
        """
        self.ensure_one()
        metrics = self._whole_piece_atom_metrics()
        atom_count = metrics.get('atom_count', 0)
        if metrics.get('warning') or not employees or atom_count <= 0:
            return {}
        common_count, remainder = divmod(atom_count, len(employees))
        atom_counts = {
            employee.id: common_count
            for employee in employees
        }
        remainder_workers = employees.sorted(
            lambda employee: (
                load_by_employee.get(employee.id, 0.0),
                employee.id,
            )
        )[:remainder]
        for employee in remainder_workers:
            atom_counts[employee.id] += 1
        return {
            employee_id: self._assignment_payload_for_atoms(atom_count)
            for employee_id, atom_count in atom_counts.items()
            if atom_count > 0
        }
    operation_id = fields.Many2one(
        'furniture.mrp.mps.operation',
        string='معيار العملية',
        required=True,
        ondelete='restrict',
        index=True,
        check_company=True,
    )
    name = fields.Char(string='العملية وقت التخطيط', required=True, readonly=True)
    sequence = fields.Integer(default=10, index=True)
    stage = fields.Selection(
        MPS_STAGE_DISPLAY_SELECTION,
        string='المرحلة',
        required=True,
        readonly=True,
        index=True,
    )
    distribution_mode = fields.Selection(
        [
            ('single', 'عامل واحد فقط'),
            ('stage_pool_equal', 'توزيع قطع كاملة على عمال المرحلة'),
        ],
        string='طريقة التوزيع',
        required=True,
        readonly=True,
    )
    standard_duration_hours = fields.Float(
        string='زمن الدفعة وقت التخطيط',
        digits=(16, 3),
        readonly=True,
    )
    same_worker_key = fields.Char(string='مفتاح نفس العامل وقت التخطيط', readonly=True)
    different_worker_key = fields.Char(
        string='مفتاح اختلاف العامل وقت التخطيط',
        readonly=True,
    )
    fabric_type_id = fields.Many2one(
        'furniture.fabric.work.type',
        string='نوع القماش وقت التخطيط',
        readonly=True,
        ondelete='restrict',
    )
    scale = fields.Float(string='معامل حمل الدفعة', digits=(16, 4), readonly=True)
    atom_count = fields.Integer(
        string='عدد دفعات القطع الكاملة',
        readonly=True,
    )
    atom_hours = fields.Float(
        string='زمن دفعة القطع الكاملة',
        digits=(16, 3),
        readonly=True,
    )
    piece_count = fields.Integer(
        string='إجمالي القطع الكاملة',
        readonly=True,
    )
    labor_hours = fields.Float(
        string='ساعات العمل المطلوبة',
        digits=(16, 3),
        readonly=True,
    )
    planned_start = fields.Datetime(string='البداية المخططة', readonly=True, index=True)
    planned_end = fields.Datetime(string='النهاية المخططة', readonly=True, index=True)
    state = fields.Selection(
        [
            ('proposed', 'مقترح'),
            ('approved', 'معتمد'),
            ('in_progress', 'جاري'),
            ('done', 'مكتمل'),
            ('blocked', 'يحتاج تدخل'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة',
        default='proposed',
        required=True,
        index=True,
    )
    dependency_line_ids = fields.Many2many(
        'furniture.mrp.mps.work',
        'furniture_mrp_mps_work_dependency_rel',
        'work_id',
        'dependency_id',
        string='الأعمال السابقة',
        readonly=True,
    )
    assignment_ids = fields.One2many(
        'furniture.mrp.mps.assignment',
        'line_id',
        string='توزيع العمال',
        copy=False,
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        string='العمال المخططون',
        compute='_compute_assignment_summary',
    )
    assigned_hours = fields.Float(
        string='الساعات الموزعة',
        compute='_compute_assignment_summary',
        digits=(16, 3),
    )
    output_summary = fields.Char(string='الشغل المطلوب وقت التخطيط', readonly=True)
    warning_message = fields.Text(string='تنبيه', readonly=True)

    @api.depends('assignment_ids.employee_id', 'assignment_ids.planned_hours')
    def _compute_assignment_summary(self):
        for line in self:
            line.employee_ids = line.assignment_ids.mapped('employee_id')
            line.assigned_hours = sum(line.assignment_ids.mapped('planned_hours'))



class FurnitureMrpMPSAssignment(models.Model):
    _name = 'furniture.mrp.mps.assignment'
    _description = 'توزيع عامل في MPS'
    _order = 'planned_start, employee_id, id'
    _rec_name = 'operation_name'
    _check_company_auto = True

    plan_id = fields.Many2one(
        'furniture.mrp.mps.plan',
        string='خطة MPS',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    line_id = fields.Many2one(
        'furniture.mrp.mps.work',
        string='العملية',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    production_id = fields.Many2one(
        related='plan_id.production_id',
        string='أمر الإنتاج',
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='plan_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    furniture_model_id = fields.Many2one(
        related='plan_id.furniture_model_id',
        string='الموديل',
        store=True,
        readonly=True,
        index=True,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='العامل',
        required=True,
        ondelete='restrict',
        index=True,
        check_company=True,
    )
    stage = fields.Selection(
        related='line_id.stage',
        string='المرحلة',
        store=True,
        readonly=True,
        index=True,
    )
    operation_name = fields.Char(
        related='line_id.name',
        string='العملية',
        readonly=True,
    )
    output_summary = fields.Char(
        string='نصيب العامل من الشغل',
        readonly=True,
        copy=False,
    )
    planned_atom_count = fields.Integer(
        string='عدد دفعات القطع الكاملة',
        readonly=True,
        copy=False,
    )
    piece_count = fields.Integer(
        string='عدد القطع الكاملة',
        readonly=True,
        copy=False,
    )
    output_line_ids = fields.One2many(
        'furniture.mrp.mps.assignment.output',
        'assignment_id',
        string='القطع المسندة',
        readonly=True,
        copy=False,
    )
    planned_hours = fields.Float(
        string='الساعات المخططة',
        required=True,
        digits=(16, 3),
    )
    planned_start = fields.Datetime(string='البداية', required=True, index=True)
    planned_end = fields.Datetime(string='النهاية', required=True, index=True)
    state = fields.Selection(
        [
            ('proposed', 'مقترح'),
            ('approved', 'معتمد'),
            ('in_progress', 'جاري'),
            ('done', 'مكتمل'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة',
        default='proposed',
        required=True,
        index=True,
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_assignment_line_employee_unique',
            'unique(line_id, employee_id)',
            'العامل موزع على نفس العملية بالفعل.',
        ),
        (
            'furniture_mrp_mps_assignment_hours_positive',
            'check(planned_hours > 0)',
            'الساعات المخططة يجب أن تكون أكبر من صفر.',
        ),
        (
            'furniture_mrp_mps_assignment_dates_valid',
            'check(planned_end >= planned_start)',
            'نهاية توزيع العامل يجب ألا تسبق بدايته.',
        ),
    ]

    @api.constrains('plan_id', 'line_id')
    def _check_line_plan_consistency(self):
        for assignment in self:
            if assignment.line_id.plan_id != assignment.plan_id:
                raise ValidationError(_(
                    'خطة توزيع العامل يجب أن تطابق خطة سطر العملية.'
                ))

    @api.constrains(
        'employee_id',
        'planned_start',
        'planned_end',
        'state',
    )
    def _check_active_overlap(self):
        for assignment in self:
            if (
                assignment.state not in MPS_ASSIGNMENT_ACTIVE_STATES
                or not assignment.employee_id
                or not assignment.planned_start
                or not assignment.planned_end
            ):
                continue
            overlap = self.sudo().search_count([
                ('id', '!=', assignment.id),
                ('employee_id', '=', assignment.employee_id.id),
                ('state', 'in', MPS_ASSIGNMENT_ACTIVE_STATES),
                ('plan_id.state', 'in', MPS_PLAN_ACTIVE_STATES),
                ('planned_start', '<', assignment.planned_end),
                ('planned_end', '>', assignment.planned_start),
            ])
            if overlap:
                raise ValidationError(_(
                    'العامل %(worker)s لديه حجز MPS متداخل مع هذه الفترة.'
                ) % {'worker': assignment.employee_id.display_name})

    def _validate_planned_slot(self):
        for assignment in self:
            employee = assignment.employee_id.sudo()
            line = assignment.line_id
            if (
                not employee.active
                or employee.furniture_mrp_role != 'worker'
                or line.stage not in employee.furniture_mrp_worker_stage_ids.mapped('code')
                or not employee.user_id
                or not employee.user_id.active
                or employee.user_id.share
            ):
                raise UserError(_(
                    'العامل %(worker)s لم يعد مؤهلًا لمرحلة %(stage)s؛ أعد الجدولة.'
                ) % {
                    'worker': employee.display_name,
                    'stage': dict(FURNITURE_STAGE_SELECTION).get(line.stage, line.stage),
                })
            contracts = assignment.plan_id._employee_open_contracts(
                employee, assignment.planned_start,
            )
            contract = assignment.plan_id._contract_covering_slot(
                contracts,
                assignment.planned_start,
                assignment.planned_end,
            )
            if not contract:
                raise UserError(_(
                    'عقد العامل %s لا يغطي الفترة المخططة؛ أعد الجدولة.'
                ) % employee.display_name)
            calendar = (
                contract.resource_calendar_id
                or employee.resource_calendar_id
                or assignment.company_id.resource_calendar_id
            )
            if not calendar:
                raise UserError(_(
                    'لا يوجد تقويم عمل للعامل %s.'
                ) % employee.display_name)
            start_datetime = fields.Datetime.to_datetime(
                assignment.planned_start
            ).replace(tzinfo=timezone.utc)
            end_datetime = fields.Datetime.to_datetime(
                assignment.planned_end
            ).replace(tzinfo=timezone.utc)
            intervals = calendar._work_intervals_batch(
                start_datetime,
                end_datetime,
                compute_leaves=True,
                resources=employee.resource_id,
            ).get(employee.resource_id.id, ())
            available_hours = sum(
                (stop - start).total_seconds() / 3600.0
                for start, stop, _meta in intervals
            )
            if float_compare(
                available_hours,
                assignment.planned_hours,
                precision_digits=3,
            ) != 0:
                raise UserError(_(
                    'تقويم/إجازات العامل %(worker)s تغيرت ولا تغطي %(hours).3f ساعة؛ '
                    'أعد الجدولة قبل الاعتماد.'
                ) % {
                    'worker': employee.display_name,
                    'hours': assignment.planned_hours,
                })
            open_log_reservations = assignment.plan_id._open_worker_log_reservations(
                employee,
                assignment.planned_start,
            ).get(employee.id, ())
            if any(
                assignment.planned_start < busy_end
                and assignment.planned_end > busy_start
                for busy_start, busy_end in open_log_reservations
            ):
                raise UserError(_(
                    'العامل %s لديه تشغيل فعلي مفتوح يتداخل مع الجدول؛ '
                    'أغلق التشغيل أو أعد الجدولة.'
                ) % employee.display_name)
            assignment._check_active_overlap()
        return True


class FurnitureMrpMPSAssignmentOutput(models.Model):
    _name = 'furniture.mrp.mps.assignment.output'
    _description = 'قطعة كاملة مسندة لعامل في MPS'
    _order = 'assignment_id, product_id, id'
    _check_company_auto = True

    assignment_id = fields.Many2one(
        'furniture.mrp.mps.assignment',
        string='تكليف العامل',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    plan_id = fields.Many2one(
        related='assignment_id.plan_id',
        string='خطة MPS',
        store=True,
        readonly=True,
        index=True,
    )
    production_id = fields.Many2one(
        related='assignment_id.production_id',
        string='أمر الإنتاج',
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='assignment_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    employee_id = fields.Many2one(
        related='assignment_id.employee_id',
        string='العامل',
        store=True,
        readonly=True,
        index=True,
    )
    stage = fields.Selection(
        related='assignment_id.stage',
        string='المرحلة',
        store=True,
        readonly=True,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='الصنف',
        required=True,
        ondelete='restrict',
        index=True,
    )
    quantity = fields.Integer(
        string='عدد القطع',
        required=True,
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        related='product_id.uom_id',
        string='الوحدة',
        readonly=True,
    )
    piece_duration_hours = fields.Float(
        string='زمن القطعة (ساعة)',
        required=True,
        readonly=True,
        digits=(16, 3),
    )
    planned_hours = fields.Float(
        string='إجمالي الساعات',
        compute='_compute_planned_hours',
        store=True,
        readonly=True,
        digits=(16, 3),
    )

    _sql_constraints = [
        (
            'furniture_mrp_mps_assignment_output_unique',
            'unique(assignment_id, product_id)',
            'الصنف مكرر داخل نفس تكليف العامل.',
        ),
        (
            'furniture_mrp_mps_assignment_output_qty_positive',
            'check(quantity > 0)',
            'عدد القطع المسندة يجب أن يكون أكبر من صفر.',
        ),
        (
            'furniture_mrp_mps_assignment_output_duration_positive',
            'check(piece_duration_hours > 0)',
            'زمن القطعة المسندة يجب أن يكون أكبر من صفر.',
        ),
    ]

    @api.depends('quantity', 'piece_duration_hours')
    def _compute_planned_hours(self):
        for output in self:
            output.planned_hours = (
                output.quantity * output.piece_duration_hours
            )


class FurnitureMrpProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _furniture_mps_is_enabled(self):
        """Return whether MPS is allowed to influence the MRP workflow."""
        value = self.env['ir.config_parameter'].sudo().get_param(
            MPS_ENABLED_PARAMETER,
            default='True',
        )
        return str(value).strip().lower() not in {
            '0', 'false', 'no', 'off', '',
        }

    temporary_stage_fixed_hours = fields.Float(
        string='مدة المرحلة الاختبارية المؤقتة',
        default=0.0,
        copy=False,
        readonly=True,
        digits=(16, 3),
        help=(
            'استثناء مؤقت للاختبارات فقط. عند ضبطه على قيمة موجبة تعمل مراحل '
            'هذا الأمر بالمدة الثابتة المحددة ومن دون ربط بدء المرحلة بخطة MPS.'
        ),
    )
    temporary_stage_fixed_hours_from = fields.Datetime(
        string='بداية الاستثناء المؤقت',
        copy=False,
        readonly=True,
    )
    temporary_stage_fixed_hours_until = fields.Datetime(
        string='نهاية الاستثناء المؤقت',
        copy=False,
        readonly=True,
        help=(
            'بعد هذا الموعد ينتهي الاستثناء تلقائيًا وتعود المراحل الجديدة '
            'للعمل بخطة MPS العادية.'
        ),
    )

    mps_schedule_plan_ids = fields.One2many(
        'furniture.mrp.mps.plan',
        'production_id',
        string='خطط جدولة MPS',
        copy=False,
    )
    mps_schedule_plan_count = fields.Integer(
        string='خطط MPS',
        compute='_compute_mps_schedule_plan_count',
    )

    @api.constrains(
        'temporary_stage_fixed_hours',
        'temporary_stage_fixed_hours_from',
        'temporary_stage_fixed_hours_until',
    )
    def _check_temporary_stage_fixed_hours(self):
        for production in self:
            hours = production.temporary_stage_fixed_hours or 0.0
            if not isfinite(hours) or hours < 0.0:
                raise ValidationError(_(
                    'مدة المرحلة الاختبارية يجب أن تكون صفرًا أو قيمة موجبة.'
                ))
            started_at = fields.Datetime.to_datetime(
                production.temporary_stage_fixed_hours_from
            )
            expires_at = fields.Datetime.to_datetime(
                production.temporary_stage_fixed_hours_until
            )
            if bool(started_at) != bool(expires_at):
                raise ValidationError(_(
                    'حدد بداية ونهاية الاستثناء المؤقت معًا.'
                ))
            if started_at and expires_at <= started_at:
                raise ValidationError(_(
                    'نهاية الاستثناء المؤقت يجب أن تكون بعد بدايته.'
                ))

    def _furniture_temporary_stage_hours(self):
        """Return the isolated fixed stage duration, or zero when disabled."""
        self.ensure_one()
        hours = self.temporary_stage_fixed_hours or 0.0
        if not isfinite(hours) or hours <= 0.0:
            return 0.0
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        started_at = fields.Datetime.to_datetime(
            self.temporary_stage_fixed_hours_from
        )
        expires_at = fields.Datetime.to_datetime(
            self.temporary_stage_fixed_hours_until
        )
        if started_at and now < started_at:
            return 0.0
        if expires_at and now >= expires_at:
            return 0.0
        return hours

    def _furniture_stage_started_during_temporary_override(self, stage_order):
        """Keep a stage isolated from MPS when it began inside the window."""
        self.ensure_one()
        hours = self.temporary_stage_fixed_hours or 0.0
        stage_started_at = fields.Datetime.to_datetime(stage_order.date_start)
        if not isfinite(hours) or hours <= 0.0 or not stage_started_at:
            return False
        started_at = fields.Datetime.to_datetime(
            self.temporary_stage_fixed_hours_from
        )
        expires_at = fields.Datetime.to_datetime(
            self.temporary_stage_fixed_hours_until
        )
        return bool(
            (not started_at or stage_started_at >= started_at)
            and (not expires_at or stage_started_at < expires_at)
        )

    @api.depends('mps_schedule_plan_ids')
    def _compute_mps_schedule_plan_count(self):
        for production in self:
            production.mps_schedule_plan_count = len(production.mps_schedule_plan_ids)

    def _furniture_mps_active_stage_codes(self):
        self.ensure_one()
        if not self._furniture_mps_is_enabled():
            return set()
        plans = self.sudo().mps_schedule_plan_ids.filtered(
            lambda plan: plan.state in MPS_PLAN_ACTIVE_STATES
        )
        active_stage_codes = set()
        for plan in plans:
            active_stage_codes.update(plan._active_production_stage_codes())
        return active_stage_codes

    def _furniture_mps_assert_stages_editable(self, changed_stage_codes):
        if not self._furniture_mps_is_enabled():
            return True
        changed_stage_codes = set(changed_stage_codes or ())
        for production in self:
            conflicts = production._furniture_mps_active_stage_codes().intersection(
                changed_stage_codes
            )
            if conflicts:
                labels = dict(FURNITURE_STAGE_SELECTION)
                raise UserError(_(
                    'لا يمكن تغيير كميات/أصناف/مسار مرحلة جارية في MPS: %s. '
                    'أكمل المرحلة أو أعدها لحالة تسمح بالتعديل أولًا.'
                ) % '، '.join(labels.get(code, code) for code in sorted(conflicts)))
        return True

    def _furniture_mps_profile(self):
        self.ensure_one()
        lines = self.production_line_ids.filtered(
            lambda line: line.active and float_compare(
                line.product_qty or 0.0, 0.0, precision_digits=6,
            ) > 0
        )
        model = self.furniture_order_model_id or lines.mapped('furniture_order_model_id')[:1]
        if not model:
            return self.env['furniture.mrp.mps.profile']
        canonical_products = self.env['product.product']
        for line in lines:
            canonical_products |= (
                line.product_id.furniture_dimension_source_product_id
                or line.product_id
            )
        profile_domain = [
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('furniture_model_id', '=', model.id),
        ]
        canonical_product_ids = set(canonical_products.ids)
        ensured_profile = self.env['furniture.mrp.mps.profile']
        if canonical_product_ids:
            ensured_profile = self.env[
                'furniture.mrp.mps.product.time'
            ].sudo()._ensure_auto_profile(
                model,
                self.company_id,
            )
        if (
            ensured_profile
            and canonical_product_ids.issubset(
                set(ensured_profile.product_ids.ids)
            )
        ):
            return ensured_profile[:1]
        profiles = self.env['furniture.mrp.mps.profile'].search(
            profile_domain,
        ).filtered(lambda profile: (
            bool(canonical_product_ids)
            and canonical_product_ids.issubset(set(profile.product_ids.ids))
        ))
        return profiles.sorted(
            lambda profile: (
                profile.is_auto_product_profile,
                -len(profile.product_ids & canonical_products),
                profile.sequence,
                profile.id,
            )
        )[:1]

    def _generate_mps_schedule(self):
        if not self._furniture_mps_is_enabled():
            return self.env['furniture.mrp.mps.plan']
        if not self.env.su:
            unauthorized = self.filtered(
                lambda production: production.company_id not in self.env.companies
            )
            if unauthorized:
                raise AccessError(_(
                    'لا يمكن توليد MPS لأمر إنتاج تابع لشركة غير متاحة للمستخدم.'
                ))
        productions = self.sudo()
        plans = productions.env['furniture.mrp.mps.plan']
        for production in productions:
            if production.state not in ('confirmed', 'in_production'):
                continue
            profile = production._furniture_mps_profile()
            if not profile:
                raise UserError(_(
                    'تعذر إنشاء MPS لأمر الإنتاج %(production)s: لا يوجد '
                    'ريسيبي موديل محفوظ يطابق الأصناف. راجع ريسيبي الموديل '
                    'ثم أعد تأكيد الأمر.'
                ) % {'production': production.display_name})
            plan = productions.env['furniture.mrp.mps.plan'].search([
                ('production_id', '=', production.id),
            ], limit=1)
            planned_start = max(
                fields.Datetime.to_datetime(production.date_planned_start or fields.Datetime.now()),
                fields.Datetime.to_datetime(fields.Datetime.now()),
            )
            if plan:
                if plan.state in ('in_progress', 'done'):
                    plans |= plan
                    continue
                plan.write({
                    'profile_id': profile.id,
                    'planned_start': planned_start,
                    'state': 'planned',
                })
            else:
                plan = productions.env['furniture.mrp.mps.plan'].create({
                    'production_id': production.id,
                    'profile_id': profile.id,
                    'planned_start': planned_start,
                })
            plan._regenerate_unstarted_work()
            plans |= plan
        return plans

    def _furniture_replan_proposed_mps(self, changed_stage_codes=None):
        if not self._furniture_mps_is_enabled():
            return self.env['furniture.mrp.mps.plan']
        if not self.env.su:
            unauthorized = self.filtered(
                lambda production: production.company_id not in self.env.companies
            )
            if unauthorized:
                raise AccessError(_(
                    'لا يمكن إعادة جدولة MPS لأمر إنتاج تابع لشركة غير متاحة للمستخدم.'
                ))
        if changed_stage_codes:
            self._furniture_mps_assert_stages_editable(changed_stage_codes)
        plans = self.sudo().mapped('mps_schedule_plan_ids').filtered(
            lambda plan: plan.state in MPS_PLAN_ACTIVE_STATES
        )
        for plan in plans:
            previous_state = plan.state
            matching_profile = plan.production_id._furniture_mps_profile()
            if matching_profile and matching_profile != plan.profile_id:
                plan.profile_id = matching_profile
            plan.planned_start = max(
                fields.Datetime.to_datetime(plan.planned_start),
                fields.Datetime.to_datetime(fields.Datetime.now()),
            )
            plan._regenerate_unstarted_work()
            plan.state = 'in_progress' if previous_state == 'in_progress' else 'planned'
            plan.message_post(body=_(
                '🔄 تغيرت بيانات أمر الإنتاج أو نوع القماش؛ أعيدت جدولة العمل غير المبدوء '
                'وتحتاج الخطة إلى اعتماد جديد.'
            ))
        return plans

    def write(self, vals):
        replan_fields = {
            'date_planned_start',
            'furniture_order_model_id',
        }
        if (
            'furniture_order_model_id' in vals
            and not self.env.context.get('furniture_skip_mps_replan')
        ):
            for production in self:
                active_stages = production._furniture_mps_active_stage_codes()
                if active_stages:
                    production._furniture_mps_assert_stages_editable(active_stages)
        result = super().write(vals)
        if (
            replan_fields.intersection(vals)
            and not self.env.context.get('furniture_skip_mps_replan')
        ):
            self.filtered(
                lambda production: production.state in ('confirmed', 'in_production')
            )._furniture_replan_proposed_mps()
        return result

    def action_confirm(self):
        result = super().action_confirm()
        self.filtered(lambda production: (
            production.state == 'confirmed'
            and production.production_line_ids
        ))._generate_mps_schedule()
        return result

    def action_view_mps_schedule(self):
        self.ensure_one()
        if not (
            self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')
            or self.env.user.has_group('furniture_mrp.group_furniture_mrp_supervisor')
        ):
            raise AccessError(_(
                'فتح خطة MPS الكاملة متاح لمدير المصنع ومشرف الإنتاج فقط.'
            ))
        plan = self.mps_schedule_plan_ids[:1]
        if (
            not plan
            or (
                plan.state == 'cancelled'
                and self.state in ('confirmed', 'in_production')
            )
        ):
            plan = self._generate_mps_schedule()[:1]
        if not plan:
            raise UserError(_(
                'لا يوجد بروفايل MPS يطابق موديل ومنتجات أمر الإنتاج.'
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('خطة MPS — %s') % self.name,
            'res_model': 'furniture.mrp.mps.plan',
            'res_id': plan.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_mark_done(self):
        result = super().action_mark_done()
        for production in self:
            plans = production.mps_schedule_plan_ids.sudo().filtered(
                lambda plan: plan.state not in ('done', 'cancelled')
            )
            plans.assignment_ids.filtered(
                lambda assignment: assignment.state in MPS_ASSIGNMENT_ACTIVE_STATES
            ).write({'state': 'done'})
            plans.line_ids.filtered(
                lambda line: line.state in ('proposed', 'approved', 'in_progress')
            ).write({'state': 'done'})
            plans.write({'state': 'done'})
        return result

    def action_cancel(self):
        result = super().action_cancel()
        for production in self:
            production.mps_schedule_plan_ids.sudo().filtered(
                lambda plan: plan.state not in ('done', 'cancelled')
            ).with_context(
                furniture_mps_production_cancel=True,
            ).action_cancel()
        return result

    def action_reset_to_draft(self):
        result = super().action_reset_to_draft()
        for production in self:
            production.mps_schedule_plan_ids.sudo().filtered(
                lambda plan: plan.state not in ('done', 'cancelled')
            ).action_cancel()
        return result


class FurnitureMrpProductionLineMPS(models.Model):
    _inherit = 'furniture.mrp.production.line'

    @api.model
    def _mps_replan_trigger_fields(self):
        return {
            'active',
            'product_id',
            'product_qty',
            'bom_id',
            'furniture_order_model_id',
            *('use_%s' % stage for stage, _label in FURNITURE_STAGE_SELECTION),
        }

    def _mps_enabled_stage_codes(self):
        self.ensure_one()
        return {
            stage
            for stage, _label in FURNITURE_STAGE_SELECTION
            if self['use_%s' % stage]
        }

    def _mps_assert_change_does_not_touch_active_stage(self, vals=None):
        vals = vals or {}
        core_fields = {
            'active',
            'product_id',
            'product_qty',
            'bom_id',
            'furniture_order_model_id',
        }
        for line in self:
            affected_stages = set()
            if core_fields.intersection(vals) or not vals:
                affected_stages.update(line._mps_enabled_stage_codes())
            for stage, _label in FURNITURE_STAGE_SELECTION:
                if 'use_%s' % stage in vals:
                    affected_stages.add(stage)
            if not vals:
                affected_stages.update(line._mps_enabled_stage_codes())
            line.production_id._furniture_mps_assert_stages_editable(
                affected_stages
            )
        return True

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('furniture_skip_mps_replan'):
            lines._mps_assert_change_does_not_touch_active_stage()
            lines.mapped('production_id').filtered(
                lambda production: production.state in ('confirmed', 'in_production')
            )._furniture_replan_proposed_mps()
        return lines

    def write(self, vals):
        productions = self.mapped('production_id')
        if (
            self._mps_replan_trigger_fields().intersection(vals)
            and not self.env.context.get('furniture_skip_mps_replan')
        ):
            self._mps_assert_change_does_not_touch_active_stage(vals)
        result = super().write(vals)
        if (
            self._mps_replan_trigger_fields().intersection(vals)
            and not self.env.context.get('furniture_skip_mps_replan')
        ):
            (productions | self.mapped('production_id')).filtered(
                lambda production: production.state in ('confirmed', 'in_production')
            )._furniture_replan_proposed_mps()
        return result

    def unlink(self):
        productions = self.mapped('production_id')
        if not self.env.context.get('furniture_skip_mps_replan'):
            self._mps_assert_change_does_not_touch_active_stage()
        result = super().unlink()
        if not self.env.context.get('furniture_skip_mps_replan'):
            productions.filtered(
                lambda production: production.state in ('confirmed', 'in_production')
            )._furniture_replan_proposed_mps()
        return result


class FurnitureMrpTailoringMaterialAllocation(models.Model):
    _inherit = 'furniture.mrp.tailoring.material.allocation'

    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        if not self.env.context.get('furniture_tailoring_setup_internal_write'):
            allocations.mapped('production_id')._furniture_replan_proposed_mps(
                changed_stage_codes={'tailoring'},
            )
        return allocations

    def write(self, vals):
        productions = self.mapped('production_id')
        result = super().write(vals)
        if not self.env.context.get('furniture_tailoring_setup_internal_write'):
            (productions | self.mapped('production_id'))._furniture_replan_proposed_mps(
                changed_stage_codes={'tailoring'},
            )
        return result

    def unlink(self):
        productions = self.mapped('production_id')
        result = super().unlink()
        if not self.env.context.get('furniture_tailoring_setup_internal_write'):
            productions._furniture_replan_proposed_mps(
                changed_stage_codes={'tailoring'},
            )
        return result


class FurnitureMrpStageMixin(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    def _furniture_apply_temporary_stage_duration(self):
        for stage_order in self.filtered(
            lambda row: row.state == 'in_progress' and row.date_start
        ):
            fixed_hours = (
                stage_order.production_order_id._furniture_temporary_stage_hours()
                if stage_order.production_order_id else 0.0
            )
            if fixed_hours:
                stage_order.date_planned_finish = (
                    fields.Datetime.to_datetime(stage_order.date_start)
                    + timedelta(hours=fixed_hours)
                )
        return True

    def _check_mps_schedule_before_start(self):
        """Require an approved plan and reject workers outside its stage pool."""
        if not self.env[
            'furniture.mrp.production'
        ]._furniture_mps_is_enabled():
            return True
        for stage_order in self:
            production = stage_order.production_order_id
            if not production:
                continue
            if production._furniture_temporary_stage_hours():
                continue
            stage_code = production._stage_model_to_code(stage_order._name)
            if stage_code == 'sewing':
                stage_code = 'tailoring'
            if not stage_code:
                continue
            plans = production.mps_schedule_plan_ids.sudo().filtered(
                lambda plan: plan.state in MPS_PLAN_ACTIVE_STATES
            )
            lines = plans.line_ids.filtered(
                lambda line: (
                    line.stage == stage_code
                    and line.state not in ('done', 'cancelled')
                )
            )
            if not lines:
                continue
            blocked = lines.filtered(lambda line: line.state == 'blocked')
            if blocked:
                raise UserError(_(
                    'لا يمكن بدء المرحلة قبل معالجة عمليات MPS غير المجدولة: %s'
                ) % '، '.join(blocked.mapped('name')))
            unapproved = lines.filtered(lambda line: line.state == 'proposed')
            if unapproved:
                raise UserError(_(
                    'اعتمد توزيع عمال MPS لهذه المرحلة قبل بدء التشغيل.'
                ))
            scheduled_workers = lines.assignment_ids.filtered(
                lambda assignment: assignment.state in ('approved', 'in_progress')
            ).mapped('employee_id')
            if not scheduled_workers:
                raise UserError(_(
                    'لا يوجد عمال معتمدون في MPS لهذه المرحلة.'
                ))
            selected_workers = stage_order.worker_ids
            if not selected_workers:
                raise UserError(_(
                    'اختر عاملًا واحدًا على الأقل من العمال المجدولين في MPS لهذه المرحلة.'
                ))
            unexpected_workers = selected_workers - scheduled_workers
            if unexpected_workers:
                raise UserError(_(
                    'العمال التاليون غير موجودين في توزيع MPS المعتمد لهذه المرحلة: %s'
                ) % '، '.join(unexpected_workers.mapped('display_name')))
            open_logs = plans[:1]._open_worker_log_reservations(
                selected_workers,
                fields.Datetime.now(),
            )
            busy_workers = selected_workers.filtered(
                lambda employee: open_logs.get(employee.id)
            )
            if busy_workers:
                raise UserError(_(
                    'أغلق التشغيل الفعلي المفتوح أولًا للعمال: %s'
                ) % '، '.join(busy_workers.mapped('display_name')))
        return True

    def action_start(self):
        self._check_mps_schedule_before_start()
        result = super(
            FurnitureMrpStageMixin,
            self.with_context(furniture_mps_stage_start_transition=True),
        ).action_start()
        self._furniture_apply_temporary_stage_duration()
        return result

    def _sync_mps_schedule_state(self, stage_state):
        if not self.env[
            'furniture.mrp.production'
        ]._furniture_mps_is_enabled():
            return True
        for stage_order in self:
            production = stage_order.production_order_id
            if not production:
                continue
            if (
                production._furniture_temporary_stage_hours()
                or production._furniture_stage_started_during_temporary_override(
                    stage_order,
                )
            ):
                continue
            stage_code = production._stage_model_to_code(stage_order._name)
            if stage_code == 'sewing':
                stage_code = 'tailoring'
            if not stage_code:
                continue
            plans = production.mps_schedule_plan_ids.sudo().filtered(
                lambda plan: plan.state not in ('done', 'cancelled')
            )
            lines = plans.line_ids.filtered(
                lambda line: line.stage == stage_code
            )
            if not lines:
                continue
            assignments = lines.assignment_ids
            if stage_state == 'in_progress':
                plans.filtered(
                    lambda plan: plan.state in ('planned', 'approved')
                ).write({'state': 'in_progress'})
            elif stage_state == 'done':
                unresolved = lines.filtered(
                    lambda line: line.state in ('proposed', 'blocked')
                )
                if unresolved:
                    raise UserError(_(
                        'لا يمكن إنهاء المرحلة وفيها عمليات MPS غير معتمدة أو '
                        'غير مجدولة: %s'
                    ) % '، '.join(unresolved.mapped('name')))
                assignments.filtered(
                    lambda assignment: assignment.state in MPS_ASSIGNMENT_ACTIVE_STATES
                ).write({'state': 'done'})
                lines.filtered(
                    lambda line: line.state in ('proposed', 'approved', 'in_progress')
                ).write({'state': 'done'})
        return True

    def write(self, vals):
        result = super().write(vals)
        stage_state = vals.get('state')
        if (
            stage_state == 'in_progress'
            and self.env.context.get('furniture_mps_stage_start_transition')
        ):
            self._sync_mps_schedule_state(stage_state)
        elif stage_state == 'done':
            self._sync_mps_schedule_state(stage_state)
        return result
