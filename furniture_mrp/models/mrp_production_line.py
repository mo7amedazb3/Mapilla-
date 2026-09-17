# -*- coding: utf-8 -*-
from markupsafe import escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import FURNITURE_STAGE_FIELD_MAP, FURNITURE_STAGE_SELECTION


class FurnitureMrpProductionLine(models.Model):
    _name = 'furniture.mrp.production.line'
    _description = 'سطر صنف داخل أمر التشغيل الأسبوعي'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, id'
    _rec_name = 'product_id'

    active = fields.Boolean(default=True, index=True)
    consolidated_into_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='السطر الموحد',
        copy=False,
        readonly=True,
        ondelete='restrict',
        index=True,
    )
    consolidated_line_ids = fields.One2many(
        'furniture.mrp.production.line',
        'consolidated_into_line_id',
        string='الدفعات المجمعة',
        readonly=True,
    )

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل الأسبوعي',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='الترتيب', default=10)
    product_id = fields.Many2one(
        'product.product',
        string='المنتج',
        required=True,
        domain="[('furniture_has_active_normal_recipe', '=', True), ('furniture_dimension_source_product_id', '=', False)]",
        tracking=True,
    )
    furniture_order_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        tracking=True,
        copy=True,
        help='الموديل المطلوب تصنيعه لهذا الصنف.',
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري (الشركة)',
        domain=[('is_company', '=', True)],
        tracking=True,
        copy=True,
        index=True,
        ondelete='restrict',
        help='الشركة أو الجهة التي تشتري هذا المنتج من المصنع.',
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        tracking=True,
        copy=True,
        index=True,
        ondelete='restrict',
        help='العميل النهائي الذي سيستلم أو يستخدم هذا المنتج.',
    )
    kit_bom_id = fields.Many2one(
        'mrp.bom',
        string='الطقم (Kit)',
        domain=[('type', '=', 'phantom')],
        tracking=True,
        copy=True,
        index=True,
        ondelete='restrict',
        help='وصفة الـKit التي ينتمي إليها هذا المكون داخل أمر الإنتاج.',
    )
    kit_instance_number = fields.Integer(
        string='رقم الطقم داخل الأمر',
        tracking=True,
        copy=True,
        index=True,
        help='رقم يميز كل طقم فعلي عن الأطقم المطابقة له داخل أمر الإنتاج نفسه.',
    )
    batch_image_1920 = fields.Image(
        string='صورة القطعة داخل أمر التصنيع',
        copy=False,
        max_width=1920,
        max_height=1920,
        help=(
            'صورة تشغيل مؤقتة تخص هذه القطعة/الدفعة داخل أمر التصنيع فقط. '
            'لا تغيّر صورة المنتج في المخزون، وتُحذف عند إنهاء أو إلغاء أمر التصنيع.'
        ),
    )
    batch_image_token = fields.Char(
        string='هوية صورة القطعة',
        copy=False,
        index=True,
        readonly=True,
        help='هوية داخلية تمنع دمج قطعتين لهما صور تشغيل مختلفة.',
    )
    kit_piece_note = fields.Text(
        string='ملاحظات القطعة للتفصيل والكسوة',
        tracking=True,
        copy=True,
        help=(
            'تعليمات تشغيل تخص هذه القطعة داخل الطقم، وتظهر لعمال '
            'التفصيل والكسوة مع صورة القطعة وخاماتها.'
        ),
    )
    product_template_id = fields.Many2one(
        'product.template',
        string='نموذج المنتج',
        related='product_id.product_tmpl_id',
        store=True,
        readonly=True,
    )
    product_qty = fields.Float(
        string='الكمية',
        default=1.0,
        required=True,
        tracking=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        related='product_id.uom_id',
        store=True,
        readonly=True,
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='الريسيبي',
        domain="[('furniture_product_id', '=', product_id), ('furniture_model_id', '=', furniture_order_model_id), ('type', '=', 'normal')]",
        tracking=True,
    )
    bom_width_cm = fields.Float(
        string='عرض الريسيبي (سم)',
        related='bom_id.furniture_width_cm',
        readonly=True,
    )
    bom_depth_cm = fields.Float(
        string='عمق الريسيبي (سم)',
        related='bom_id.furniture_depth_cm',
        readonly=True,
    )
    bom_height_cm = fields.Float(
        string='ارتفاع الريسيبي (سم)',
        related='bom_id.furniture_height_cm',
        readonly=True,
    )
    width_cm = fields.Float(string='العرض (سم)', tracking=True)
    depth_cm = fields.Float(string='العمق (سم)', tracking=True)
    height_cm = fields.Float(string='الارتفاع (سم)', tracking=True)
    stage_selection_initialized = fields.Boolean(default=False, copy=True)
    first_stage_started = fields.Boolean(
        string='بدأ أول مرحلة',
        default=False,
        copy=False,
        readonly=True,
    )
    first_stage_started_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='أول مرحلة بدأت',
        copy=False,
        readonly=True,
    )
    planned_start_stage = fields.Selection(
        FURNITURE_STAGE_SELECTION,
        string='مرحلة البداية المختارة',
        copy=False,
        readonly=True,
        tracking=True,
        index=True,
        help=(
            'المرحلة التي اختارها مدير الإنتاج لدخول هذه الدفعة إلى '
            'التصنيع مباشرة، ولا تعني أن المرحلة بدأت قبل اعتماد إذن المخزن.'
        ),
    )
    cost_origin_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='مرجع تكلفة الدفعة الأصلية',
        copy=True,
        readonly=True,
        ondelete='set null',
        index=True,
    )
    kit_family_origin_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='مرجع عائلة الصنف قبل تقسيم الأطقم',
        copy=True,
        readonly=True,
        ondelete='set null',
        index=True,
        help=(
            'مرجع فني لتجميع الكمية الأصلية في شاشات الإدارة والمخزن، '
            'مع إبقاء كل قطعة Kit مستقلة للصور والجودة والتكاليف.'
        ),
    )
    inherited_material_cost_per_unit = fields.Float(
        string='خامات الوحدة الموروثة عند التقسيم',
        copy=True,
        readonly=True,
        digits=(16, 4),
    )
    inherited_labor_cost_per_unit = fields.Float(
        string='عمالة الوحدة الموروثة عند التقسيم',
        copy=True,
        readonly=True,
        digits=(16, 4),
    )
    use_priming = fields.Boolean(string='التقديم', tracking=True)
    use_painting = fields.Boolean(string='تصنيع دهانات', tracking=True)
    use_carpentry = fields.Boolean(string='تجميع', tracking=True)
    use_bases = fields.Boolean(string='القواعد', tracking=True)
    use_finishing = fields.Boolean(string='تجهيز', tracking=True)
    use_tailoring = fields.Boolean(string='تفصيل', tracking=True)
    use_sewing = fields.Boolean(
        string='الخياطة (مرحلة مستقلة قديمة)',
        default=False,
        tracking=True,
        help='حقل توافق فقط؛ الخياطة أصبحت مرحلة داخل التفصيل.',
    )
    use_upholstery = fields.Boolean(string='كسوه', tracking=True)
    use_packaging = fields.Boolean(string='التغليف', tracking=True)
    dimension_factor = fields.Float(
        string='معامل المقاس',
        compute='_compute_dimension_factor',
        digits=(16, 3),
    )
    dimension_label = fields.Char(
        string='المقاس',
        compute='_compute_dimension_label',
    )
    stage_count = fields.Integer(
        string='عدد المراحل',
        compute='_compute_stage_summary',
    )
    stage_summary = fields.Char(
        string='المراحل',
        compute='_compute_stage_summary',
    )
    stage_progress_html = fields.Html(
        string='تقدم مراحل الصنف',
        compute='_compute_stage_progress_html',
        sanitize=False,
        readonly=True,
    )
    material_cost = fields.Float(
        string='خامات معتمدة حتى الآن',
        compute='_compute_material_cost',
        digits=(16, 2),
        help='التكلفة التراكمية للخامات التي اعتمدتها الجودة حتى آخر مرحلة وصلت لها هذه الدفعة.',
    )
    material_line_ids = fields.One2many(
        'furniture.mrp.material.line',
        'production_line_id',
        string='خامات السطر',
        readonly=True,
    )
    material_qty_override_json = fields.Json(
        string='تعديلات كميات خامات أمر الإنتاج',
        copy=True,
        help=(
            'كميات خامات معدلة لهذا السطر فقط. لا تغيّر الريسيبي الأصلية، '
            'وتظل محفوظة عند إعادة تحميل خامات أمر الإنتاج.'
        ),
    )
    bom_quantity_editable = fields.Boolean(
        string='يمكن تعديل الكمية من BoM',
        compute='_compute_bom_quantity_editable',
    )
    bom_priming_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات التقديم', domain=[('stage', '=', 'priming')],
    )
    bom_painting_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات تصنيع الدهانات', domain=[('stage', '=', 'painting')],
    )
    bom_carpentry_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات التجميع', domain=[('stage', '=', 'carpentry')],
    )
    bom_bases_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات القواعد', domain=[('stage', '=', 'bases')],
    )
    bom_finishing_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات التجهيز', domain=[('stage', '=', 'finishing')],
    )
    bom_tailoring_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات التفصيل', domain=[('stage', '=', 'tailoring')],
    )
    bom_upholstery_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات الكسوة', domain=[('stage', '=', 'upholstery')],
    )
    bom_packaging_material_line_ids = fields.One2many(
        'furniture.mrp.material.line', 'production_line_id',
        string='خامات التغليف', domain=[('stage', '=', 'packaging')],
    )
    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        related='production_id.company_id',
        store=True,
        readonly=True,
    )

    @api.depends('bom_width_cm', 'bom_depth_cm', 'bom_height_cm', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_factor(self):
        for rec in self:
            rec.dimension_factor = rec._get_dimension_factor()

    @api.depends('bom_id', 'bom_width_cm', 'bom_depth_cm', 'bom_height_cm', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_dimension_label(self):
        for rec in self:
            rec.dimension_label = rec._get_dimension_label()

    @api.depends('product_id', 'product_qty', 'width_cm', 'depth_cm', 'height_cm')
    def _compute_display_name(self):
        for rec in self:
            name = rec.product_id.display_name or _('صنف بدون منتج')
            if rec.furniture_order_model_id:
                name = '%s [%s]' % (name, rec.furniture_order_model_id.name)
            qty = rec.product_qty or 0.0
            dimensions = '×'.join(
                str(int(value)) if float(value).is_integer() else str(value)
                for value in (rec.width_cm, rec.depth_cm, rec.height_cm)
                if value
            )
            rec.display_name = _('%s × %s') % (qty, name)
            if dimensions:
                rec.display_name = _('%s - %s سم') % (rec.display_name, dimensions)

    @api.depends(
        'bom_id', 'bom_id.use_priming', 'bom_id.use_painting', 'bom_id.use_carpentry', 'bom_id.use_bases',
        'bom_id.use_finishing', 'bom_id.use_tailoring', 'bom_id.use_sewing', 'bom_id.use_upholstery',
        'bom_id.use_packaging', 'stage_selection_initialized',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases', 'use_finishing',
        'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
    )
    def _compute_stage_summary(self):
        stage_label_map = dict(FURNITURE_STAGE_SELECTION)
        for rec in self:
            if not rec.bom_id:
                rec.stage_count = 0
                rec.stage_summary = False
                continue
            active_codes = rec._selected_stage_codes()
            rec.stage_count = len(active_codes)
            rec.stage_summary = '، '.join(stage_label_map.get(code, code) for code in active_codes) or False

    def _stage_progress_family_lines(self):
        """Return the exact technical pieces represented by this summary row."""
        self.ensure_one()
        if not self.production_id:
            return self
        root = (
            self.kit_family_origin_line_id
            or self.cost_origin_line_id
            or self
        )
        family = self.production_id.production_line_ids.filtered(
            lambda candidate: (
                candidate.active
                and (
                    candidate == root
                    or candidate.kit_family_origin_line_id == root
                    or candidate.cost_origin_line_id == root
                )
            )
        )
        return family or self

    def _stage_progress_line_state(self, stage_code, runtime_cache=None):
        """Return an exact line state without borrowing another SKU's stock."""
        self.ensure_one()
        production = self.production_id
        if not production or not stage_code:
            return 'pending', []
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        completed_codes = production._production_line_completed_stage_codes(
            self,
            product=self.product_id,
            runtime_cache=runtime_cache,
        )
        if stage_code in completed_codes:
            return 'done', []

        carryovers = production._production_line_carryover_history(
            self,
            product=self.product_id,
            runtime_cache=runtime_cache,
        )
        notes = []
        related_productions = production | carryovers.mapped('production_id')
        for related_production in related_productions:
            stage_order = related_production._stage_order_record(stage_code)
            if not stage_order:
                continue
            active_lines = stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
            quality_lines = stage_order._get_stage_line_ids_data(
                'quality_production_line_ids_data'
            )
            if self in (active_lines | quality_lines):
                notes.append(_('داخل أمر مرحلة %s') % stage_order.name)
                return 'in_progress', notes

        stage_carryovers = carryovers.filtered(
            lambda carryover: (
                carryover.current_stage == stage_code
                and carryover.state in ('selected', 'started')
            )
        )
        if stage_carryovers:
            return 'in_progress', [_('شغل مستمر من أمر سابق')]
        return 'pending', []

    def _stage_progress_rows(self):
        """Aggregate stage progress for normal lines and split Kit families."""
        self.ensure_one()
        stage_labels = dict(FURNITURE_STAGE_SELECTION)
        family = self._stage_progress_family_lines()
        route_codes = self._selected_stage_codes()
        runtime_cache = {}
        rows = []
        for stage_code in route_codes:
            stage_lines = family.filtered(
                lambda line: stage_code in line._selected_stage_codes()
            )
            if not stage_lines:
                continue
            states = []
            notes = []
            done_qty = 0.0
            active_qty = 0.0
            total_qty = sum(stage_lines.mapped('product_qty'))
            for technical_line in stage_lines:
                state, line_notes = technical_line._stage_progress_line_state(
                    stage_code,
                    runtime_cache=runtime_cache,
                )
                states.append(state)
                notes.extend(line_notes)
                if state == 'done':
                    done_qty += technical_line.product_qty
                elif state == 'in_progress':
                    active_qty += technical_line.product_qty

            if states and all(state == 'done' for state in states):
                status = 'done'
                status_label = _('خلصت')
                quantity_note = _('اكتملت الكمية كلها')
            elif any(state in ('done', 'in_progress') for state in states):
                status = 'in_progress'
                status_label = _('شغالة')
                if done_qty:
                    quantity_note = _('مكتمل %s من %s') % (
                        self._format_dimension_value(done_qty),
                        self._format_dimension_value(total_qty),
                    )
                else:
                    quantity_note = _('قيد التشغيل %s من %s') % (
                        self._format_dimension_value(active_qty),
                        self._format_dimension_value(total_qty),
                    )
            else:
                status = 'pending'
                status_label = _('لسه')
                quantity_note = _('لم تبدأ بعد')

            rows.append({
                'stage_code': stage_code,
                'stage_label': stage_labels.get(stage_code, stage_code),
                'status': status,
                'status_label': status_label,
                'quantity_note': quantity_note,
                'note': '، '.join(dict.fromkeys(notes)),
            })
        return rows

    @api.depends(
        'active', 'product_qty', 'kit_family_origin_line_id',
        'cost_origin_line_id',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing',
        'use_upholstery', 'use_packaging',
        'production_id.production_line_ids.active',
        'production_id.production_line_ids.product_qty',
        'production_id.production_line_ids.kit_family_origin_line_id',
        'production_id.production_line_ids.cost_origin_line_id',
        'production_id.priming_order_id.active_production_line_ids_data',
        'production_id.priming_order_id.quality_production_line_ids_data',
        'production_id.priming_order_id.completed_production_line_ids_data',
        'production_id.painting_order_id.active_production_line_ids_data',
        'production_id.painting_order_id.quality_production_line_ids_data',
        'production_id.painting_order_id.completed_production_line_ids_data',
        'production_id.carpentry_order_id.active_production_line_ids_data',
        'production_id.carpentry_order_id.quality_production_line_ids_data',
        'production_id.carpentry_order_id.completed_production_line_ids_data',
        'production_id.bases_order_id.active_production_line_ids_data',
        'production_id.bases_order_id.quality_production_line_ids_data',
        'production_id.bases_order_id.completed_production_line_ids_data',
        'production_id.finishing_order_id.active_production_line_ids_data',
        'production_id.finishing_order_id.quality_production_line_ids_data',
        'production_id.finishing_order_id.completed_production_line_ids_data',
        'production_id.tailoring_order_id.active_production_line_ids_data',
        'production_id.tailoring_order_id.quality_production_line_ids_data',
        'production_id.tailoring_order_id.completed_production_line_ids_data',
        'production_id.sewing_order_id.active_production_line_ids_data',
        'production_id.sewing_order_id.quality_production_line_ids_data',
        'production_id.sewing_order_id.completed_production_line_ids_data',
        'production_id.upholstery_order_id.active_production_line_ids_data',
        'production_id.upholstery_order_id.quality_production_line_ids_data',
        'production_id.upholstery_order_id.completed_production_line_ids_data',
        'production_id.packaging_order_id.active_production_line_ids_data',
        'production_id.packaging_order_id.quality_production_line_ids_data',
        'production_id.packaging_order_id.completed_production_line_ids_data',
        'production_id.carryover_line_ids.current_stage',
        'production_id.carryover_line_ids.state',
        'production_id.carryover_line_ids.source_production_line_id',
    )
    def _compute_stage_progress_html(self):
        for line in self:
            rows = line._stage_progress_rows()
            if not rows:
                line.stage_progress_html = (
                    '<div class="o_furniture_line_progress_empty">%s</div>'
                ) % escape(_('لا توجد مراحل تصنيع محددة لهذا الصنف.'))
                continue

            done_count = len([
                row for row in rows if row['status'] == 'done'
            ])
            row_html = []
            for index, row in enumerate(rows, start=1):
                row_html.append(
                    '<tr class="o_furniture_line_progress_row '
                    'o_furniture_line_progress_row--%s">'
                    '<td><span class="o_furniture_line_progress_sequence">%s</span>'
                    '<strong>%s</strong></td>'
                    '<td><span class="o_furniture_line_progress_status">%s</span></td>'
                    '<td>%s</td>'
                    '<td>%s</td>'
                    '</tr>' % (
                        row['status'],
                        index,
                        escape(row['stage_label']),
                        escape(row['status_label']),
                        escape(row['quantity_note']),
                        escape(row['note'] or '-'),
                    )
                )
            line.stage_progress_html = (
                '<section class="o_furniture_line_progress">'
                '<div class="o_furniture_line_progress_header">'
                '<div><span>%s</span><strong>%s</strong></div>'
                '<div class="o_furniture_line_progress_total">%s</div>'
                '</div>'
                '<div class="table-responsive">'
                '<table class="o_furniture_line_progress_table">'
                '<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
                '<tbody>%s</tbody></table></div></section>'
            ) % (
                escape(_('متابعة رحلة الصنف')),
                escape(_('المراحل المطلوبة وحالة كل مرحلة')),
                escape(_('مكتمل %s من %s') % (done_count, len(rows))),
                escape(_('المرحلة')),
                escape(_('الحالة')),
                escape(_('تقدم الكمية')),
                escape(_('ملاحظة')),
                ''.join(row_html),
            )

    def _compute_material_cost(self):
        for rec in self:
            if not rec.production_id:
                rec.material_cost = 0.0
                continue
            snapshot = rec.production_id._get_production_line_cost_snapshot(rec)
            rec.material_cost = snapshot.get('material', 0.0)

    @api.depends('production_id.state', 'first_stage_started')
    def _compute_bom_quantity_editable(self):
        """Keep quantity edits safe from already-issued production batches."""
        for rec in self:
            rec.bom_quantity_editable = bool(
                rec.production_id
                and rec.production_id.state in ('draft', 'confirmed')
                and not rec.first_stage_started
            )

    def _get_dimension_factor(self):
        self.ensure_one()
        ratios = []
        for base_value, actual_value in (
            (self.bom_width_cm, self.width_cm),
            (self.bom_depth_cm, self.depth_cm),
            (self.bom_height_cm, self.height_cm),
        ):
            if base_value and actual_value:
                ratios.append(actual_value / base_value)
        return sum(ratios) / len(ratios) if ratios else 1.0

    def _format_dimension_value(self, value):
        text = ('%.3f' % (value or 0.0)).rstrip('0').rstrip('.')
        return text or '0'

    def _get_dimension_label(self):
        self.ensure_one()
        if not self.bom_id:
            return False
        actual_dims = [
            self.width_cm or 0.0,
            self.depth_cm or 0.0,
            self.height_cm or 0.0,
        ]
        bom_dims = [
            self.bom_width_cm or 0.0,
            self.bom_depth_cm or 0.0,
            self.bom_height_cm or 0.0,
        ]
        if not any(
            float_compare(actual, base, precision_digits=3) != 0
            for actual, base in zip(actual_dims, bom_dims)
        ):
            return False
        return '×'.join(self._format_dimension_value(value) for value in actual_dims)

    @api.model
    def _stage_use_field_names(self):
        return [field_names[0] for _stage_code, field_names in FURNITURE_STAGE_FIELD_MAP.items()]

    @api.model
    def _stage_selection_vals(self, stage_codes):
        active_stage_codes = set(stage_codes or [])
        vals = {
            use_field: stage_code in active_stage_codes
            for stage_code, (use_field, _order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items()
        }
        vals['use_sewing'] = False
        vals['stage_selection_initialized'] = True
        return vals

    @api.model
    def _default_bom_from_vals(self, vals):
        if (
            self.env.context.get('furniture_preserve_explicit_bom')
            and vals.get('bom_id')
        ):
            return self.env['mrp.bom'].browse(vals['bom_id'])
        if vals.get('product_id') and vals.get('furniture_order_model_id'):
            product = self.env['product.product'].browse(vals['product_id'])
            model = self.env['furniture.product.model'].browse(
                vals['furniture_order_model_id']
            )
            return self.env['mrp.bom']._find_furniture_production_recipe(
                product,
                model,
                company=self.env.company,
            )
        if vals.get('bom_id'):
            return self.env['mrp.bom'].browse(vals['bom_id'])
        return self.env['mrp.bom']

    def _recipe_stage_codes(self):
        self.ensure_one()
        if not self.bom_id:
            return []
        return [
            stage_code
            for stage_code, _label in FURNITURE_STAGE_SELECTION
            if stage_code in self.bom_id._get_active_stage_codes()
        ]

    def _selected_stage_codes(self):
        self.ensure_one()
        selected_codes = [
            stage_code
            for stage_code, (use_field, _order_field, _state_field) in FURNITURE_STAGE_FIELD_MAP.items()
            if self[use_field]
        ]
        if selected_codes or self.stage_selection_initialized:
            return selected_codes
        return self._recipe_stage_codes()

    def _sync_stage_plan_from_bom(self):
        for rec in self:
            rec.update(rec._stage_selection_vals(rec._recipe_stage_codes()))

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            rec.bom_id = False
            rec.furniture_order_model_id = (
                rec.production_id.furniture_order_model_id or False
            )
            rec.width_cm = 0.0
            rec.depth_cm = 0.0
            rec.height_cm = 0.0
            if not rec.product_id:
                continue
            rec.update(rec._stage_selection_vals([]))

    def _find_bom_for_product_model(self, product, model):
        return self.env['mrp.bom']._find_furniture_production_recipe(
            product,
            model,
            company=self.env.company,
        )

    @api.onchange('furniture_order_model_id')
    def _onchange_furniture_order_model_id(self):
        for rec in self:
            rec.bom_id = False
            rec.width_cm = 0.0
            rec.depth_cm = 0.0
            rec.height_cm = 0.0
            if not rec.product_id or not rec.furniture_order_model_id:
                rec.update(rec._stage_selection_vals([]))
                continue
            bom = rec._find_bom_for_product_model(
                rec.product_id,
                rec.furniture_order_model_id,
            )
            if not bom:
                rec.bom_id = False
                return {
                    'warning': {
                        'title': _('لا يوجد ريسيبي للمنتج'),
                        'message': _(
                            'أنشئ ريسيبي Manufacture this product للمنتج %(product)s والموديل %(model)s أولًا.'
                        ) % {
                            'product': rec.product_id.display_name,
                            'model': rec.furniture_order_model_id.display_name,
                        },
                    }
                }
            rec.bom_id = bom
            rec.width_cm = bom.furniture_width_cm
            rec.depth_cm = bom.furniture_depth_cm
            rec.height_cm = bom.furniture_height_cm
            rec._sync_stage_plan_from_bom()

    @api.onchange('bom_id')
    def _onchange_bom_id(self):
        for rec in self:
            if rec.bom_id:
                if rec.bom_id.furniture_model_id:
                    rec.furniture_order_model_id = rec.bom_id.furniture_model_id
                rec.width_cm = rec.bom_id.furniture_width_cm
                rec.depth_cm = rec.bom_id.furniture_depth_cm
                rec.height_cm = rec.bom_id.furniture_height_cm
                rec._sync_stage_plan_from_bom()

    def _split_for_partial_quantity(self, qty_to_keep, preserve_progress=False):
        """Split the line so the current record keeps only the selected quantity."""
        self.ensure_one()
        qty_to_keep = qty_to_keep or 0.0
        if float_compare(qty_to_keep, 0.0, precision_digits=3) <= 0:
            raise ValidationError(_('كمية البدء لازم تكون أكبر من صفر.'))
        if float_compare(qty_to_keep, self.product_qty, precision_digits=3) >= 0:
            return self.env['furniture.mrp.production.line']

        remaining_qty = self.product_qty - qty_to_keep
        split_context = dict(
            self.env.context,
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            # The two split rows keep the same total quantity and route, so
            # replanning between copy() and the matching source write would
            # see a temporary false increase and can also touch a live stage.
            furniture_skip_mps_replan=True,
            # A running/historic line keeps the exact recipe snapshot it was
            # created with, even after master recipes are replaced/archived.
            furniture_preserve_explicit_bom=True,
            # create() normally initializes a newly added line on a running
            # order from the current active recipe.  A split is not a new
            # routing decision, so keep the archived recipe and its snapshot;
            # the split caller refreshes operational material rows afterwards.
            furniture_skip_running_line_initialization=True,
            # A partial operational split copies the same Kit identity to the
            # new physical batch; it does not invalidate the saved plan.
            furniture_skip_kit_plan_invalidation=True,
        )
        inherited_snapshot = (
            self.production_id._get_production_line_cost_snapshot(self)
            if preserve_progress and self.production_id else {}
        )
        original_qty = self.product_qty
        overrides = self.material_qty_override_json or []

        def _scaled_overrides(factor):
            return [
                {
                    **override,
                    'qty_needed': max(
                        (override.get('qty_needed') or 0.0) * factor,
                        0.0,
                    ),
                }
                for override in overrides
            ] or False

        copy_vals = {
            'product_qty': remaining_qty,
            # This operational split stays inside the same production order,
            # so both resulting quantities still represent the photographed
            # piece/batch.  Ordinary order duplication does not copy images.
            'batch_image_1920': self.batch_image_1920 or False,
            'batch_image_token': self.batch_image_token or False,
            # A split batch belongs to the same not-yet-started entry decision,
            # while duplicating a production order must not inherit it.
            'planned_start_stage': self.planned_start_stage,
            'cost_origin_line_id': (self.cost_origin_line_id or self).id if preserve_progress else False,
            'inherited_material_cost_per_unit': inherited_snapshot.get('material_unit', 0.0),
            'inherited_labor_cost_per_unit': inherited_snapshot.get('labor_unit', 0.0),
        }
        if overrides and original_qty:
            copy_vals['material_qty_override_json'] = _scaled_overrides(
                remaining_qty / original_qty,
            )
        if preserve_progress:
            copy_vals.update({
                'first_stage_started': self.first_stage_started,
                'first_stage_started_stage': self.first_stage_started_stage,
            })
        remaining_line = self.with_context(split_context).copy(copy_vals)
        kept_vals = {'product_qty': qty_to_keep}
        if overrides and original_qty:
            kept_vals['material_qty_override_json'] = _scaled_overrides(
                qty_to_keep / original_qty,
            )
        self.with_context(split_context).write(kept_vals)
        return remaining_line

    def _split_for_partial_first_stage(self, qty_to_start):
        """Split first-stage runs while keeping the old public method name."""
        return self._split_for_partial_quantity(qty_to_start)

    def _sync_parent_stage_plan_from_lines(self):
        productions = self.mapped('production_id').filtered(lambda rec: rec.state in ('draft', 'confirmed'))
        if productions and not self.env.context.get('furniture_skip_stage_plan_sync'):
            productions.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_recipe()

    def action_open_stage_selection_wizard(self):
        self.ensure_one()
        wizard = self.env['furniture.mrp.production.line.stage.wizard'].create({
            'line_id': self.id,
            'use_priming': self.use_priming,
            'use_painting': self.use_painting,
            'use_carpentry': self.use_carpentry,
            'use_bases': self.use_bases,
            'use_finishing': self.use_finishing,
            'use_tailoring': self.use_tailoring,
            'use_upholstery': self.use_upholstery,
            'use_packaging': self.use_packaging,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('تعديل مراحل الصنف'),
            'res_model': 'furniture.mrp.production.line.stage.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }

    def action_view_stage_progress(self):
        self.ensure_one()
        return self.env['furniture.mrp.product.stage.progress']._open_for_product(
            self.product_id,
            production=self.production_id,
            production_line=self,
        )

    def action_open_bom_popup(self):
        """Open the calculated BoM materials for this exact product row."""
        self.ensure_one()
        if not self.bom_id:
            raise UserError(_('لا توجد ريسيبي مرتبطة بسطر المنتج ده.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('BoM - %s') % self.product_id.display_name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_line_bom_popup'
            ).id,
            'target': 'new',
            'context': {
                'create': False,
                'delete': False,
                'form_view_initial_mode': 'edit',
                'furniture_bom_manual_qty_edit': True,
            },
        }

    def _apply_material_qty_overrides_to_commands(self, commands):
        """Apply this order-line's absolute raw-material quantities to commands."""
        self.ensure_one()
        overrides = self.material_qty_override_json or []
        if not overrides:
            return commands
        override_by_key = {
            (
                override.get('stage') or False,
                override.get('product_id') or False,
                override.get('product_uom_id') or False,
                int(override.get('occurrence') or 0),
            ): max(override.get('qty_needed') or 0.0, 0.0)
            for override in overrides
        }
        occurrences = {}
        prepared_commands = []
        for command in commands:
            if (
                not isinstance(command, (list, tuple))
                or len(command) < 3
                or command[0] != 0
            ):
                prepared_commands.append(command)
                continue
            vals = dict(command[2] or {})
            base_key = (
                vals.get('stage') or False,
                vals.get('product_id') or False,
                vals.get('product_uom_id') or False,
            )
            occurrence = occurrences.get(base_key, 0)
            occurrences[base_key] = occurrence + 1
            override_key = (*base_key, occurrence)
            if override_key in override_by_key:
                vals.update({
                    'qty_needed': override_by_key[override_key],
                    'quantity_mode': 'fixed',
                })
            prepared_commands.append((command[0], command[1], vals))
        return prepared_commands

    def _set_material_qty_override(self, override_key, qty_needed):
        """Persist one raw-material override without touching the source recipe."""
        self.ensure_one()
        stage, product_id, product_uom_id, occurrence = override_key
        target_key = (
            stage or False,
            product_id or False,
            product_uom_id or False,
            int(occurrence or 0),
        )
        payload = []
        replaced = False
        for override in self.material_qty_override_json or []:
            current_key = (
                override.get('stage') or False,
                override.get('product_id') or False,
                override.get('product_uom_id') or False,
                int(override.get('occurrence') or 0),
            )
            if current_key == target_key:
                if not replaced:
                    payload.append({
                        'stage': stage or False,
                        'product_id': product_id,
                        'product_uom_id': product_uom_id,
                        'occurrence': int(occurrence or 0),
                        'qty_needed': max(qty_needed or 0.0, 0.0),
                    })
                    replaced = True
                continue
            payload.append(override)
        if not replaced:
            payload.append({
                'stage': stage or False,
                'product_id': product_id,
                'product_uom_id': product_uom_id,
                'occurrence': int(occurrence or 0),
                'qty_needed': max(qty_needed or 0.0, 0.0),
            })
        self.with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).write({'material_qty_override_json': payload})

    def action_open_bom_quantity_wizard(self):
        """Open a real editable wizard instead of relying on modal form mode."""
        self.ensure_one()
        if not self.bom_quantity_editable:
            raise UserError(_(
                'لا يمكن تعديل الكمية بعد بدء أول مرحلة أو إغلاق أمر التشغيل.'
            ))
        wizard = self.env[
            'furniture.mrp.production.line.quantity.wizard'
        ].create({
            'line_id': self.id,
            'new_product_qty': self.product_qty,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('تعديل كمية %s') % self.product_id.display_name,
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'furniture_mrp.view_furniture_mrp_production_line_quantity_wizard_form'
            ).id,
            'target': 'new',
            'context': {'form_view_initial_mode': 'edit'},
        }

    def _refresh_parent_material_lines(self):
        productions = self.mapped('production_id').filtered(lambda rec: rec.state in ('draft', 'confirmed'))
        if productions and not self.env.context.get('furniture_skip_material_refresh'):
            productions.with_context(furniture_skip_material_refresh=True)._refresh_material_lines_for_stage_plan()

    def _initialize_running_production_lines(self):
        """Append a newly added product safely to an order already in production."""
        if self.env.context.get('furniture_skip_running_line_initialization'):
            return
        for production in self.mapped('production_id').filtered(lambda rec: rec.state == 'in_production'):
            new_lines = self.filtered(lambda line: (
                line.production_id == production
                and not production.material_line_ids.filtered(
                    lambda material: material.production_line_id == line
                )
            ))
            if not new_lines:
                continue

            new_stage_codes = set()
            material_commands = []
            for line in new_lines.sorted(production._production_line_sort_key):
                bom = production._find_bom_for_production_line(line)
                if not bom:
                    if not line.furniture_order_model_id:
                        raise ValidationError(_(
                            'حدد الموديل أولًا للصنف: %s'
                        ) % line.product_id.display_name)
                    raise ValidationError(_('لا توجد ريسيبي للصنف: %s') % line.product_id.display_name)
                if line.bom_id != bom:
                    line.with_context(
                        furniture_skip_running_line_initialization=True,
                        furniture_skip_stage_plan_sync=True,
                        furniture_skip_material_refresh=True,
                    ).write({'bom_id': bom.id})
                line_stage_codes = line._selected_stage_codes()
                new_stage_codes.update(line_stage_codes)
                material_commands.extend(production._prepare_material_lines_from_bom_with_factor(
                    bom,
                    line.product_qty,
                    dimension_factor=line._get_dimension_factor(),
                    production_line=line,
                    active_stage_codes=line_stage_codes,
                ))

            stage_vals = {}
            for stage_code in new_stage_codes:
                use_field = FURNITURE_STAGE_FIELD_MAP[stage_code][0]
                if not production[use_field]:
                    stage_vals[use_field] = True
            if stage_vals:
                production.with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                ).write(stage_vals)
            if material_commands:
                production.with_context(
                    furniture_skip_stage_plan_sync=True,
                    furniture_skip_material_refresh=True,
                ).write({'material_line_ids': material_commands})
                for material in production.material_line_ids.filtered(
                    lambda row: row.production_line_id in new_lines
                ):
                    production._ensure_stock_product_is_storable(
                        material.product_id,
                        material.product_uom_id,
                    )

            for line in new_lines:
                selected_stages = line._selected_stage_codes()
                if not selected_stages:
                    continue
                first_stage = production._production_line_start_stage_code(line)
                production._reopen_stage_order_for_pending_start(
                    production._stage_order_record(first_stage),
                    first_stage,
                )
            production.message_post(body=_(
                '➕ تمت إضافة صنف جديد أثناء التشغيل وتحميل خاماته ومراحله: %s'
            ) % '، '.join(line.product_id.display_name for line in new_lines))

    @api.model_create_multi
    def create(self, vals_list):
        stage_fields = set(self._stage_use_field_names())
        for vals in vals_list:
            production = self.env['furniture.mrp.production'].browse(
                vals.get('production_id')
            )
            if production.exists():
                for field_name in (
                    'furniture_order_model_id',
                    'buyer_partner_id',
                    'beneficiary_partner_id',
                ):
                    if production[field_name]:
                        vals[field_name] = production[field_name].id
            if vals.get('use_sewing'):
                vals['use_tailoring'] = True
            vals['use_sewing'] = False
            if not vals.get('furniture_order_model_id'):
                bom = self.env['mrp.bom'].browse(vals.get('bom_id')) if vals.get('bom_id') else self.env['mrp.bom']
                model = bom.furniture_model_id
                if model:
                    vals['furniture_order_model_id'] = model.id
            bom = self._default_bom_from_vals(vals)
            if vals.get('product_id') and vals.get('furniture_order_model_id'):
                # Model-routed products require the exact hidden recipe. A
                # genuinely model-less product may keep its generic recipe.
                vals['bom_id'] = bom.id if bom else False
            elif bom and vals.get('bom_id') != bom.id:
                vals['bom_id'] = bom.id
            if stage_fields & set(vals):
                vals['stage_selection_initialized'] = True
            elif vals.get('bom_id') or vals.get('product_id'):
                bom = self._default_bom_from_vals(vals)
                if bom:
                    vals.update(self._stage_selection_vals(bom._get_active_stage_codes()))
        records = super().create(vals_list)
        for production in records.mapped('production_id'):
            header_vals = {}
            production_records = records.filtered(
                lambda line: line.production_id == production
            )
            for field_name in (
                'furniture_order_model_id',
                'buyer_partner_id',
                'beneficiary_partner_id',
            ):
                if production[field_name]:
                    continue
                source_line = production_records.filtered(field_name)[:1]
                if source_line:
                    header_vals[field_name] = source_line[field_name].id
            if header_vals:
                production.write(header_vals)
        records._sync_parent_stage_plan_from_lines()
        records._refresh_parent_material_lines()
        records._initialize_running_production_lines()
        return records

    def write(self, vals):
        vals = dict(vals)
        if vals.get('use_sewing'):
            vals['use_tailoring'] = True
        if 'use_sewing' in vals:
            vals['use_sewing'] = False
        route_fields = {'product_id', 'furniture_order_model_id', 'bom_id'}
        if len(self) > 1 and route_fields & set(vals):
            result = True
            for line in self:
                result = line.write(dict(vals)) and result
            return result
        if len(self) == 1 and route_fields & set(vals):
            route_input_changed = bool(
                ('product_id' in vals and vals.get('product_id') != self.product_id.id)
                or (
                    'furniture_order_model_id' in vals
                    and vals.get('furniture_order_model_id')
                    != self.furniture_order_model_id.id
                )
                or ('bom_id' in vals and vals.get('bom_id') != self.bom_id.id)
            )
            if route_input_changed:
                route_vals = {
                    'product_id': vals.get('product_id', self.product_id.id),
                    'furniture_order_model_id': vals.get(
                        'furniture_order_model_id',
                        self.furniture_order_model_id.id,
                    ),
                    'bom_id': vals.get('bom_id', self.bom_id.id),
                }
                if route_vals['product_id'] and route_vals['furniture_order_model_id']:
                    resolved_bom = self._default_bom_from_vals(route_vals)
                    vals['bom_id'] = resolved_bom.id if resolved_bom else False
        stage_fields = set(self._stage_use_field_names())
        # The web client can resend product_id/bom_id unchanged while saving the
        # parent one2many. Do not reload the BoM route in that case: doing so
        # silently restores stages the user explicitly unchecked.
        route_source_changed = self.filtered(lambda rec: (
            ('product_id' in vals and vals.get('product_id') != rec.product_id.id)
            or ('bom_id' in vals and vals.get('bom_id') != rec.bom_id.id)
            or (
                'furniture_order_model_id' in vals
                and vals.get('furniture_order_model_id') != rec.furniture_order_model_id.id
            )
        ))
        if route_source_changed and 'material_qty_override_json' not in vals:
            # An override belongs to the exact product/model recipe that was
            # selected when it was entered, never to a replacement recipe.
            vals['material_qty_override_json'] = False
        if stage_fields & set(vals):
            vals['stage_selection_initialized'] = True
        result = super().write(vals)
        if route_source_changed and not (stage_fields & set(vals)):
            route_source_changed.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            )._sync_stage_plan_from_bom()
        material_fields = {
            'product_id', 'furniture_order_model_id', 'bom_id', 'product_qty',
            'width_cm', 'depth_cm', 'height_cm',
            'stage_selection_initialized',
        } | stage_fields
        if material_fields & set(vals):
            self._sync_parent_stage_plan_from_lines()
            self._refresh_parent_material_lines()
        return result

    def unlink(self):
        productions = self.mapped('production_id').filtered(lambda rec: rec.state in ('draft', 'confirmed'))
        result = super().unlink()
        if productions and not self.env.context.get('furniture_skip_material_refresh'):
            productions.with_context(furniture_skip_material_refresh=True)._refresh_material_lines_for_stage_plan()
        return result

    @api.constrains('product_qty')
    def _check_product_qty(self):
        for rec in self:
            if rec.product_qty <= 0:
                raise ValidationError(_('كمية الصنف يجب أن تكون أكبر من صفر.'))

    @api.constrains('product_id', 'furniture_order_model_id', 'bom_id')
    def _check_recipe_matches_product_model(self):
        for rec in self.filtered('bom_id'):
            source_product = (
                rec.product_id.furniture_dimension_source_product_id
                or rec.product_id
            )
            if rec.bom_id.type != 'normal' or rec.bom_id.furniture_product_id != source_product:
                raise ValidationError(_('الريسيبي المختار لا يتبع المنتج الموجود في السطر.'))
            recipe_model = (
                rec.bom_id.furniture_model_id
                or rec.bom_id.furniture_recipe_model_id
            )
            inactive_legacy_snapshot = bool(
                not rec.bom_id.active
                and recipe_model == rec.furniture_order_model_id
            )
            if (
                rec.furniture_order_model_id
                and not inactive_legacy_snapshot
                and rec.bom_id != self.env['mrp.bom']._find_furniture_production_recipe(
                    source_product,
                    rec.furniture_order_model_id,
                    company=rec.production_id.company_id or self.env.company,
                )
            ):
                raise ValidationError(_('الريسيبي المختار لا يتبع الموديل الموجود في السطر.'))

    @api.constrains('kit_bom_id', 'kit_instance_number')
    def _check_kit_instance_identity(self):
        for rec in self:
            if rec.kit_bom_id and rec.kit_bom_id.type != 'phantom':
                raise ValidationError(_('وصفة الطقم المرتبطة بسطر الإنتاج لازم تكون من النوع Kit.'))
            if bool(rec.kit_bom_id) != bool(rec.kit_instance_number):
                raise ValidationError(_('وصفة الطقم ورقم الطقم لازم يتحددوا مع بعض.'))

    @api.constrains(
        'stage_selection_initialized',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases', 'use_finishing',
        'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
    )
    def _check_stage_selection(self):
        for rec in self:
            if rec.stage_selection_initialized and rec.product_id and not rec._selected_stage_codes():
                raise ValidationError(_('اختار مرحلة واحدة على الأقل لسطر الصنف: %s') % rec.product_id.display_name)


class FurnitureMrpProductionLineStageWizard(models.TransientModel):
    _name = 'furniture.mrp.production.line.stage.wizard'
    _description = 'تعديل مراحل صنف أمر التشغيل'

    line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='الصنف',
        required=True,
        readonly=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل',
        related='line_id.production_id',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='المنتج',
        related='line_id.product_id',
        readonly=True,
    )
    stage_summary = fields.Char(
        string='المراحل الحالية',
        related='line_id.stage_summary',
        readonly=True,
    )
    use_priming = fields.Boolean(string='التقديم')
    use_painting = fields.Boolean(string='تصنيع دهانات')
    use_carpentry = fields.Boolean(string='تجميع')
    use_bases = fields.Boolean(string='القواعد')
    use_finishing = fields.Boolean(string='تجهيز')
    use_tailoring = fields.Boolean(string='تفصيل')
    use_sewing = fields.Boolean(
        string='الخياطة (مرحلة مستقلة قديمة)',
        default=False,
    )
    use_upholstery = fields.Boolean(string='كسوه')
    use_packaging = fields.Boolean(string='التغليف')

    def _stage_vals(self):
        self.ensure_one()
        return {
            'use_priming': self.use_priming,
            'use_painting': self.use_painting,
            'use_carpentry': self.use_carpentry,
            'use_bases': self.use_bases,
            'use_finishing': self.use_finishing,
            'use_tailoring': self.use_tailoring or self.use_sewing,
            'use_sewing': False,
            'use_upholstery': self.use_upholstery,
            'use_packaging': self.use_packaging,
            'stage_selection_initialized': True,
        }

    def action_apply(self):
        for wizard in self:
            if not any(wizard[field_name] for field_name in wizard.line_id._stage_use_field_names()):
                raise ValidationError(_('اختار مرحلة واحدة على الأقل للصنف.'))
            wizard.line_id.write(wizard._stage_vals())
        return {'type': 'ir.actions.act_window_close'}


class FurnitureMrpProductionLineQuantityWizard(models.TransientModel):
    _name = 'furniture.mrp.production.line.quantity.wizard'
    _description = 'تعديل كمية صنف أمر التشغيل من نافذة BoM'

    line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر المنتج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر التشغيل',
        related='line_id.production_id',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='المنتج',
        related='line_id.product_id',
        readonly=True,
    )
    current_product_qty = fields.Float(
        string='الكمية الحالية',
        related='line_id.product_qty',
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        related='line_id.product_uom_id',
        readonly=True,
    )
    new_product_qty = fields.Float(
        string='الكمية الجديدة',
        required=True,
        digits=(16, 3),
    )

    @api.constrains('new_product_qty')
    def _check_new_product_qty(self):
        for wizard in self:
            if wizard.new_product_qty <= 0:
                raise ValidationError(_('الكمية الجديدة يجب أن تكون أكبر من صفر.'))

    def action_apply(self):
        self.ensure_one()
        line = self.line_id.exists()
        if not line:
            raise UserError(_('سطر المنتج لم يعد موجودًا.'))
        if not line.bom_quantity_editable:
            raise UserError(_(
                'لا يمكن تعديل الكمية بعد بدء أول مرحلة أو إغلاق أمر التشغيل.'
            ))
        line.write({'product_qty': self.new_product_qty})
        return line.action_open_bom_popup()
