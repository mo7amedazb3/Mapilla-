# -*- coding: utf-8 -*-
import json

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError


STAGE_WORKER_LOG_MAP = {
    'furniture.mrp.priming': ('furniture.mrp.priming.worker.log', 'priming_id'),
    'furniture.mrp.painting': ('furniture.mrp.painting.worker.log', 'painting_id'),
    'furniture.mrp.carpentry': ('furniture.mrp.carpentry.worker.log', 'carpentry_id'),
    'furniture.mrp.bases': ('furniture.mrp.bases.worker.log', 'bases_id'),
    'furniture.mrp.finishing': ('furniture.mrp.finishing.worker.log', 'finishing_id'),
    'furniture.mrp.tailoring': ('furniture.mrp.tailoring.worker.log', 'tailoring_id'),
    'furniture.mrp.sewing': ('furniture.mrp.sewing.worker.log', 'sewing_id'),
    'furniture.mrp.upholstery': ('furniture.mrp.upholstery.worker.log', 'upholstery_id'),
    'furniture.mrp.packaging': ('furniture.mrp.packaging.worker.log', 'packaging_id'),
}

STAGE_EMPLOYEE_CODE_MAP = {
    'furniture.mrp.priming': 'priming',
    'furniture.mrp.painting': 'painting',
    'furniture.mrp.carpentry': 'carpentry',
    'furniture.mrp.bases': 'bases',
    'furniture.mrp.finishing': 'finishing',
    'furniture.mrp.tailoring': 'tailoring',
    'furniture.mrp.sewing': 'sewing',
    'furniture.mrp.upholstery': 'upholstery',
    'furniture.mrp.packaging': 'packaging',
}

STAGE_REPORT_LABEL_MAP = {
    'priming': 'التقديم',
    'painting': 'تصنيع دهانات',
    'carpentry': 'التجميع',
    'bases': 'القواعد',
    'finishing': 'التجهيز',
    'tailoring': 'التفصيل',
    'sewing': 'الخياطة',
    'upholstery': 'الكسوة',
    'packaging': 'التغليف',
}


class FurnitureMrpStageMixin(models.AbstractModel):
    """Mixin مشترك لجميع مراحل الإنتاج"""
    _name = 'furniture.mrp.stage.mixin'
    _description = 'Mixin مراحل الإنتاج'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char(string='رقم الأمر', required=True, readonly=True, copy=False, tracking=True)
    production_order_id = fields.Many2one(
        'furniture.mrp.production', string='أمر التشغيل الأسبوعي',
        required=True, ondelete='cascade', tracking=True,
    )
    store_request_id = fields.Many2one(
        'furniture.mrp.store.request',
        string='إذن المخزن الحالي',
        compute='_compute_store_request_status',
        readonly=True,
    )
    store_request_state = fields.Selection(
        [
            ('none', 'لم يُطلب'),
            ('pending', 'بانتظار المخزن'),
            ('awaiting_receipt', 'بانتظار استلام الإنتاج'),
            ('approved', 'معتمد وجاهز للبدء'),
            ('started', 'تم بدء المرحلة'),
            ('rejected', 'مرفوض'),
            ('cancelled', 'ملغي'),
        ],
        string='حالة إذن المخزن',
        compute='_compute_store_request_status',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', related='production_order_id.product_id', store=True,
    )
    product_qty = fields.Float(related='production_order_id.product_qty', store=True)
    stage_batch_qty = fields.Float(
        string='كمية الدفعة الموجودة في المرحلة',
        compute='_compute_stage_batch_qty',
        digits=(16, 3),
    )
    production_product_summary = fields.Text(
        string='ملخص الأصناف',
        compute='_compute_production_product_summary',
        readonly=True,
    )
    kit_piece_notes_summary = fields.Text(
        string='ملاحظات قطع الأطقم',
        compute='_compute_kit_piece_notes_summary',
        readonly=True,
        help='تعليمات القطع المسجلة من شاشة تقسيم الأطقم لهذه المرحلة.',
    )
    production_can_transfer_finished_product = fields.Boolean(
        string='يوجد منتج جاهز للتحويل للمخزن التام',
        related='production_order_id.can_transfer_finished_product',
        readonly=True,
    )
    can_start_stage_work = fields.Boolean(
        string='يوجد شغل جاهز لبدء المرحلة',
        compute='_compute_can_start_stage_work',
        readonly=True,
    )
    can_operate_stage_actions = fields.Boolean(
        string='مسموح بتنفيذ عمليات المرحلة',
        compute='_compute_can_operate_stage_actions',
        readonly=True,
    )
    include_existing_work_products = fields.Boolean(
        string='متابعة الشغل القديم الموجود في الصالة',
        default=False,
        copy=False,
        tracking=True,
    )
    selected_work_products_only = fields.Boolean(
        string='تشغيل المنتجات المختارة من الصالة فقط',
        default=False,
        copy=False,
        tracking=True,
    )
    first_stage_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        string='أصناف أول مرحلة المختارة من المخزن',
        copy=False,
        readonly=True,
    )
    active_production_line_ids_data = fields.Text(
        string='أصناف دخلت هذه المرحلة',
        copy=False,
        readonly=True,
    )
    quality_production_line_ids_data = fields.Text(
        string='أصناف تحت فحص الجودة',
        copy=False,
        readonly=True,
    )
    completed_production_line_ids_data = fields.Text(
        string='أصناف اجتازت هذه المرحلة',
        copy=False,
        readonly=True,
    )

    state = fields.Selection([
        ('pending',       'في الانتظار'),
        ('in_progress',   'جاري التنفيذ'),
        ('quality_check', 'فحص الجودة'),
        ('done',          '✅ منتهي'),
    ], default='pending', tracking=True, index=True)

    priority = fields.Selection([('0', 'عادي'), ('1', 'مهم'), ('2', 'عاجل')], default='0')

    # ─── العمالة ──────────────────────────────────────────────────────────────
    foreman_id = fields.Many2one('hr.employee', string='رئيس القسم')
    worker_ids = fields.Many2many(
        'hr.employee', string='العمال',
        relation=None,  # يُعرَّف في كل موديل
    )
    worker_user_ids = fields.Many2many(
        'res.users',
        string='العمال (المستخدمون)',
        compute='_compute_worker_user_ids',
        inverse='_inverse_worker_user_ids',
        store=True,
        tracking=True,
        domain=[('share', '=', False)],
    )
    labor_hours    = fields.Float(string='ساعات العمل الفعلية', default=0.0, tracking=True)
    waiting_labor_hours = fields.Float(
        string='ساعات انتظار العمالة',
        default=0.0,
        tracking=True,
        digits=(16, 2),
    )
    waiting_labor_cost = fields.Float(
        string='تكلفة انتظار العمالة',
        default=0.0,
        tracking=True,
        digits=(16, 2),
    )
    expected_labor_hours = fields.Float(
        string='وقت العمل المتوقع',
        compute='_compute_expected_labor_hours',
        inverse='_inverse_expected_labor_hours',
        store=True,
        digits=(16, 2),
    )
    labor_rate     = fields.Float(string='تكلفة الساعة (جنيه)', default=0.0)
    labor_cost     = fields.Float(string='تكلفة العمل الفعلية', compute='_compute_labor_cost', store=True, digits=(16, 2))
    total_labor_hours = fields.Float(
        string='إجمالي ساعات الحضور للمرحلة',
        compute='_compute_total_labor_values',
        store=True,
        digits=(16, 2),
    )
    total_labor_cost = fields.Float(
        string='إجمالي تكلفة العمالة للمرحلة',
        compute='_compute_total_labor_values',
        store=True,
        digits=(16, 2),
    )
    carried_material_cost = fields.Float(
        string='تكلفة الخامات السابقة',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )
    carried_labor_cost = fields.Float(
        string='تكلفة العمالة السابقة',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )
    approved_stage_material_cost = fields.Float(
        string='خامات المرحلة المعتمدة',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )
    approved_stage_labor_cost = fields.Float(
        string='عمالة المرحلة المعتمدة',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )
    approved_cumulative_cost = fields.Float(
        string='التكلفة التراكمية المعتمدة',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )
    current_stage_unit_cost = fields.Float(
        string='تكلفة الوحدة الحالية',
        compute='_compute_stage_cost_summary',
        digits=(16, 2),
    )

    @api.depends(
        'production_order_id',
        'production_order_id.header_product_summary',
        'production_order_id.production_line_ids',
        'production_order_id.production_line_ids.product_id',
        'production_order_id.production_line_ids.product_qty',
        'production_order_id.production_line_ids.width_cm',
        'production_order_id.production_line_ids.depth_cm',
        'production_order_id.production_line_ids.height_cm',
        'production_order_id.production_line_ids.first_stage_started',
        'production_order_id.production_line_ids.use_priming',
        'production_order_id.production_line_ids.use_painting',
        'production_order_id.production_line_ids.use_carpentry',
        'production_order_id.production_line_ids.use_finishing',
        'production_order_id.production_line_ids.use_tailoring',
        'production_order_id.production_line_ids.use_upholstery',
        'production_order_id.production_line_ids.use_packaging',
        'first_stage_production_line_ids',
        'active_production_line_ids_data',
        'quality_production_line_ids_data',
        'completed_production_line_ids_data',
        'state',
    )
    def _compute_production_product_summary(self):
        for rec in self:
            production = rec.production_order_id
            if not production:
                rec.production_product_summary = False
                continue
            stage_code = production._stage_model_to_code(rec._name)
            work_payloads = rec._get_startable_stage_work_payloads(stage_code)
            if work_payloads:
                rec.production_product_summary = rec._build_stage_payload_summary(work_payloads)
                continue
            stage_lines = rec._get_stage_header_summary_lines(stage_code)
            rec.production_product_summary = (
                production._build_production_lines_summary(stage_lines)
                or production.header_product_summary
            )

    @api.depends(
        'production_order_id',
        'production_order_id.production_line_ids.active',
        'production_order_id.production_line_ids.product_id',
        'production_order_id.production_line_ids.kit_bom_id',
        'production_order_id.production_line_ids.kit_instance_number',
        'production_order_id.production_line_ids.kit_piece_note',
        'production_order_id.production_line_ids.use_tailoring',
        'production_order_id.production_line_ids.use_upholstery',
    )
    def _compute_kit_piece_notes_summary(self):
        for rec in self:
            production = rec.production_order_id
            stage_code = (
                production._stage_model_to_code(rec._name)
                if production else False
            )
            if stage_code not in ('tailoring', 'sewing', 'upholstery'):
                rec.kit_piece_notes_summary = False
                continue
            stage_field = 'use_%s' % stage_code
            noted_lines = production.production_line_ids.filtered(
                lambda line: (
                    line.active
                    and line[stage_field]
                    and bool((line.kit_piece_note or '').strip())
                )
            ).sorted(lambda line: (
                line.kit_bom_id.id,
                line.kit_instance_number or 0,
                line.sequence,
                line.id,
            ))
            notes = []
            for line in noted_lines:
                piece_label = line.product_id.display_name
                if line.kit_bom_id and line.kit_instance_number:
                    kit_product = (
                        line.kit_bom_id.furniture_product_id
                        or line.kit_bom_id.product_id
                    )
                    kit_label = (
                        kit_product.display_name
                        if kit_product
                        else line.kit_bom_id.product_tmpl_id.display_name
                    )
                    piece_label = '%s %s — %s' % (
                        kit_label,
                        line.kit_instance_number,
                        piece_label,
                    )
                notes.append('%s: %s' % (
                    piece_label,
                    line.kit_piece_note.strip(),
                ))
            rec.kit_piece_notes_summary = '\n'.join(notes) or False

    def _build_stage_payload_summary(self, payloads):
        self.ensure_one()
        production = self.production_order_id
        totals_by_label = {}
        ordered_labels = []
        for payload in payloads:
            product = payload.get('product')
            source_line = payload.get('source_production_line')
            if source_line:
                label = production._get_production_line_text_label(source_line)
            elif product:
                label = product.display_name
            else:
                continue
            if label not in totals_by_label:
                totals_by_label[label] = 0.0
                ordered_labels.append(label)
            totals_by_label[label] += payload.get('qty') or 0.0
        return '\n'.join(
            '%s × %s' % (label, production._format_dimension_value(totals_by_label[label]))
            for label in ordered_labels
        )

    def _compute_stage_batch_qty(self):
        for rec in self:
            production = rec.production_order_id
            if not production:
                rec.stage_batch_qty = 0.0
                continue
            stage_code = production._stage_model_to_code(rec._name)
            payloads = rec._get_startable_stage_work_payloads(stage_code)
            if payloads:
                rec.stage_batch_qty = sum(payload.get('qty') or 0.0 for payload in payloads)
                continue
            lines = rec._get_stage_header_summary_lines(stage_code)
            rec.stage_batch_qty = sum(lines.mapped('product_qty'))

    @api.depends('state', 'production_order_id')
    def _compute_can_start_stage_work(self):
        for rec in self:
            rec.can_start_stage_work = rec._has_startable_stage_work()

    @api.depends_context('uid')
    def _compute_can_operate_stage_actions(self):
        for rec in self:
            rec.can_operate_stage_actions = (
                rec._current_user_can_operate_stage()
            )

    def _current_user_can_operate_stage(self):
        """Return whether the caller may mutate this exact stage order.

        Stage models intentionally remain readable to the production team, so
        every mutating method needs a stage-aware guard of its own.  A generic
        supervisor must never be able to operate another department merely by
        calling a model method directly over RPC.
        """
        self.ensure_one()
        user = self.env.user
        if self.env.is_superuser():
            return True
        production = self.production_order_id
        if (
            not production
            or production.company_id.id not in self.env.companies.ids
        ):
            return False
        if user._is_admin() or user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        ):
            return True
        if user.has_group('furniture_mrp.group_furniture_mrp_supervisor'):
            stage_code = STAGE_EMPLOYEE_CODE_MAP.get(self._name)
            return bool(
                stage_code
                and user.has_group(
                    'furniture_mrp.group_furniture_mrp_supervisor_%s'
                    % stage_code
                )
            )
        # Stage mutations are production-leadership operations.  Keeping the
        # model readable to ordinary internal users must not imply write/RPC
        # authority over its workflow methods.
        return False

    def _check_stage_operation_access(self):
        if any(
            not rec._current_user_can_operate_stage()
            for rec in self
        ):
            raise AccessError(_(
                'غير مسموح لك بتنفيذ عمليات هذه المرحلة أو الشركة. '
                'راجع المرحلة المحددة في صفحة الموظف.'
            ))
        return True

    def action_view_current_stage_products(self):
        """Open the manager-saved compact product cards in read-only mode."""
        self.ensure_one()
        production = self.production_order_id
        stage_code = production._stage_model_to_code(self._name) if production else False
        if stage_code not in ('tailoring', 'sewing', 'upholstery'):
            raise UserError(_('عرض الأطقم المجمع متاح في مرحلتي التفصيل والكسوة فقط.'))
        return production.action_open_tailoring_material_setup_viewer(
            stage_code,
        )

    def _has_startable_stage_work(self):
        self.ensure_one()
        production = self.production_order_id
        if self.state != 'pending' or not production:
            return False
        stage_code = production._stage_model_to_code(self._name)
        if not stage_code:
            return False
        if production._is_material_only_stage(stage_code):
            active_lines = self._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
            return bool(
                active_lines
                or production._get_material_only_stage_start_line_candidates(
                    stage_code,
                    stage_order=self,
                )
            )
        if self._get_startable_stage_work_payloads(stage_code):
            return True
        return bool(production._get_first_stage_start_line_candidates(stage_code))

    def _get_startable_stage_work_payloads(self, stage_code=False):
        """Return only hall quantities that belong to this production order.

        Stage halls are shared physical locations. A quantity belonging to a
        different production must not make the permission/start buttons appear
        on this stage order.
        """
        self.ensure_one()
        production = self.production_order_id
        if not production:
            return []
        stage_code = stage_code or production._stage_model_to_code(self._name)
        if not stage_code:
            return []
        payloads = production._get_stage_work_location_payloads(stage_code)
        return [
            payload for payload in payloads
            if (
                payload.get('source_production') == production
                or (
                    payload.get('source_production_line')
                    and payload['source_production_line'].production_id == production
                )
            )
        ]

    def _get_stage_header_summary_lines(self, stage_code):
        self.ensure_one()
        production = self.production_order_id
        if not production or not stage_code:
            return self.env['furniture.mrp.production.line']

        completed_lines = self._get_stage_line_ids_data('completed_production_line_ids_data')
        quality_lines = self._get_stage_line_ids_data('quality_production_line_ids_data')
        active_lines = self._get_stage_line_ids_data('active_production_line_ids_data')
        stage_filter = lambda line: stage_code in line._selected_stage_codes()

        if active_lines or quality_lines:
            in_progress_lines = (active_lines | quality_lines).filtered(stage_filter) - completed_lines
            if in_progress_lines:
                return in_progress_lines

        material_only_lines = (
            production._get_material_only_stage_start_line_candidates(
                stage_code,
                stage_order=self,
            )
            if production._is_material_only_stage(stage_code)
            else self.env['furniture.mrp.production.line']
        )
        if material_only_lines:
            return material_only_lines.filtered(stage_filter)

        first_stage_lines = production._get_first_stage_start_line_candidates(stage_code)
        if first_stage_lines:
            return first_stage_lines.filtered(stage_filter)

        return production._get_stage_quality_candidate_lines(self, stage_code).filtered(stage_filter)

    @api.depends('labor_hours', 'labor_rate')
    def _compute_labor_cost(self):
        for rec in self:
            logs = rec._get_worker_time_logs()
            if logs:
                rec.labor_cost = rec._get_worker_labor_totals(logs=logs)[1]
            else:
                rec.labor_cost = rec.labor_hours * rec.labor_rate

    @api.depends('waiting_labor_hours', 'waiting_labor_cost', 'labor_hours', 'labor_cost')
    def _compute_total_labor_values(self):
        for rec in self:
            rec.total_labor_hours = (rec.waiting_labor_hours or 0.0) + (rec.labor_hours or 0.0)
            rec.total_labor_cost = (rec.waiting_labor_cost or 0.0) + (rec.labor_cost or 0.0)

    def _compute_stage_cost_summary(self):
        CostEntry = self.env['furniture.mrp.stage.cost.entry'].sudo()
        for rec in self:
            entries = CostEntry.search([
                ('stage_order_model', '=', rec._name),
                ('stage_order_res_id', '=', rec.id),
            ]) if rec.id else CostEntry
            carried_material = sum(entries.mapped('previous_material_cost'))
            carried_labor = sum(entries.mapped('previous_labor_cost'))
            stage_material = sum(entries.mapped('stage_material_cost'))
            stage_labor = sum(entries.mapped('stage_labor_cost'))
            costed_line_ids = set(entries.mapped('production_line_id').ids)
            pending_quantities = {}
            if rec.production_order_id:
                pending_lines = rec._get_stage_line_ids_data('active_production_line_ids_data')
                pending_lines |= rec._get_stage_line_ids_data('quality_production_line_ids_data')
                for line in pending_lines:
                    pending_quantities[line.id] = line.product_qty or 0.0
                stage_code = rec.production_order_id._stage_model_to_code(rec._name)
                for payload in rec._get_startable_stage_work_payloads(stage_code):
                    source_line = payload.get('source_production_line')
                    if source_line:
                        # Inventory payloads are authoritative for partial stage batches.
                        pending_quantities[source_line.id] = payload.get('qty') or 0.0
            pending_qty = 0.0
            pending_lines = self.env['furniture.mrp.production.line'].browse(pending_quantities.keys())
            for line in pending_lines.filtered(lambda item: item.id not in costed_line_ids):
                snapshot = line.production_id._get_production_line_cost_snapshot(line)
                if not snapshot['has_cost']:
                    continue
                quantity = pending_quantities.get(line.id, 0.0)
                carried_material += snapshot['material_unit'] * quantity
                carried_labor += snapshot['labor_unit'] * quantity
                pending_qty += quantity
            total_qty = sum(entries.mapped('quantity')) + pending_qty
            rec.carried_material_cost = carried_material
            rec.carried_labor_cost = carried_labor
            rec.approved_stage_material_cost = stage_material
            rec.approved_stage_labor_cost = stage_labor
            rec.approved_cumulative_cost = carried_material + carried_labor + stage_material + stage_labor
            rec.current_stage_unit_cost = rec.approved_cumulative_cost / total_qty if total_qty else 0.0

    @api.depends('date_start', 'date_planned_finish')
    def _compute_expected_labor_hours(self):
        for rec in self:
            if rec.date_start and rec.date_planned_finish:
                start_dt = fields.Datetime.to_datetime(rec.date_start)
                end_dt = fields.Datetime.to_datetime(rec.date_planned_finish)
                delta_hours = (end_dt - start_dt).total_seconds() / 3600.0
                rec.expected_labor_hours = max(delta_hours, 0.0)
            else:
                rec.expected_labor_hours = 0.0

    def _inverse_expected_labor_hours(self):
        # القيمة المدوّنة يدويًا تفضل محفوظة كما هي، ونحتفظ بالحساب التلقائي
        # فقط عند تغيير التواريخ المخططة.
        return

    @api.depends('worker_ids', 'worker_ids.user_id')
    def _compute_worker_user_ids(self):
        for rec in self:
            rec.worker_user_ids = rec.worker_ids.mapped('user_id')

    def _inverse_worker_user_ids(self):
        employee_model = self.env['hr.employee']
        for rec in self:
            employee_by_user = {
                employee.user_id.id: employee.id
                for employee in employee_model.search([('user_id', 'in', rec.worker_user_ids.ids)])
                if employee.user_id
            }
            employee_ids = []
            stage_code = rec._get_employee_stage_code()
            for user in rec.worker_user_ids:
                employee_id = employee_by_user.get(user.id)
                employee = employee_model.browse(employee_id)
                if (
                    employee_id
                    and employee_id not in employee_ids
                    and rec._employee_allowed_for_stage(employee, 'worker', stage_code)
                ):
                    employee_ids.append(employee_id)
            rec.worker_ids = [(6, 0, employee_ids)]

    # ─── الجودة ───────────────────────────────────────────────────────────────
    quality_check = fields.Selection([
        ('pending', 'لم يُفحص'),
        ('pass',    '✅ مقبول'),
        ('fail',    '❌ مرفوض'),
        ('rework',  '🔄 إعادة عمل'),
    ], default='pending', tracking=True)
    quality_notes      = fields.Text(string='ملاحظات الجودة')
    quality_inspector_id = fields.Many2one('hr.employee', string='مراقب الجودة')

    # ─── التواريخ ─────────────────────────────────────────────────────────────
    date_start         = fields.Datetime(string='تاريخ البدء الفعلي')
    date_finish        = fields.Datetime(string='تاريخ الانتهاء الفعلي')
    date_planned_finish= fields.Datetime(string='تاريخ الانتهاء المخطط')

    notes  = fields.Html(string='ملاحظات')
    color  = fields.Integer(default=0)

    # ─── الإجراءات المشتركة ───────────────────────────────────────────────────
    def _worker_time_log_info(self):
        self.ensure_one()
        info = STAGE_WORKER_LOG_MAP.get(self._name)
        if not info:
            raise UserError(_('لا يوجد سجل وقت مُعرَّف لهذه المرحلة: %s') % self._name)
        return info

    def _get_selected_worker_users(self):
        self.ensure_one()
        worker_users = self.worker_user_ids or self.worker_ids.mapped('user_id')
        worker_users = worker_users.filtered(lambda user: user and not user.share)
        stage_code = self._get_employee_stage_code()
        return worker_users.filtered(lambda user: any(
            self._employee_allowed_for_stage(employee, 'worker', stage_code)
            for employee in user.employee_ids
        ))

    def _get_selected_labor_users(self):
        self.ensure_one()
        labor_users = self._get_selected_worker_users()
        stage_code = self._get_employee_stage_code()
        foreman = self.foreman_id
        if (
            foreman
            and foreman.user_id
            and not foreman.user_id.share
            and self._employee_allowed_for_stage(foreman, 'supervisor', stage_code)
        ):
            labor_users |= foreman.user_id
        return labor_users

    def _get_employee_stage_code(self):
        return STAGE_EMPLOYEE_CODE_MAP.get(self._name)

    def _employee_allowed_for_stage(self, employee, role, stage_code):
        if not employee or employee.furniture_mrp_role != role or not stage_code:
            return False
        stage_field = 'furniture_mrp_worker_stage_ids' if role == 'worker' else 'furniture_mrp_supervisor_stage_ids'
        return stage_code in employee[stage_field].mapped('code')

    def _get_worker_time_logs(self):
        self.ensure_one()
        log_model_name, inverse_field = self._worker_time_log_info()
        return self.env[log_model_name].search([(inverse_field, '=', self.id)])

    def _get_worker_labor_totals(self, logs=None):
        self.ensure_one()
        logs = logs if logs is not None else self._get_worker_time_logs()
        total_hours = sum(logs.mapped('duration_hours'))
        total_cost = 0.0
        for log in logs:
            duration = log.duration_hours or 0.0
            if log.hourly_rate:
                total_cost += log.labor_cost or (duration * log.hourly_rate)
            else:
                total_cost += duration * (self.labor_rate or 0.0)
        return total_hours, total_cost

    def _get_latest_labor_log_boundary(self, user, attendance_start, before_datetime):
        latest_boundary = attendance_start
        attendance_start_str = fields.Datetime.to_string(attendance_start)
        before_datetime_str = fields.Datetime.to_string(before_datetime)
        for log_model_name, _inverse_field in STAGE_WORKER_LOG_MAP.values():
            logs = self.env[log_model_name].sudo().search([
                ('user_id', '=', user.id),
                ('start_datetime', '<', before_datetime_str),
                '|',
                ('end_datetime', '=', False),
                ('end_datetime', '>', attendance_start_str),
            ])
            for log in logs:
                log_start = fields.Datetime.to_datetime(log.start_datetime)
                log_end = fields.Datetime.to_datetime(log.end_datetime) if log.end_datetime else before_datetime
                if log_end <= attendance_start or log_start >= before_datetime:
                    continue
                if not log.end_datetime or log_end >= before_datetime:
                    return before_datetime
                latest_boundary = max(latest_boundary, log_end)
        return latest_boundary

    def _get_labor_user_hourly_rate(self, employee, work_datetime):
        self.ensure_one()
        log_model_name, _inverse_field = self._worker_time_log_info()
        log_helper = self.env[log_model_name]
        work_date = fields.Date.to_date(work_datetime)
        contract = log_helper._get_contract_for_employee(employee.sudo(), work_date)
        wage = contract.wage or 0.0
        monthly_hours = log_helper._get_contract_monthly_hours(contract, employee.sudo()) if contract and employee else 0.0
        return wage / monthly_hours if wage and monthly_hours else (self.labor_rate or 0.0)

    def _compute_waiting_labor_values(self, labor_users, start_datetime):
        self.ensure_one()
        try:
            attendance_model = self.env['hr.attendance'].sudo()
        except KeyError:
            return 0.0, 0.0

        start_dt = fields.Datetime.to_datetime(start_datetime)
        labor_users = labor_users.filtered(lambda user: user and not user.share)
        if not labor_users:
            return 0.0, 0.0

        employees = self.env['hr.employee'].sudo().search([('user_id', 'in', labor_users.ids)])
        employee_by_user = {employee.user_id.id: employee for employee in employees if employee.user_id}
        attendances = attendance_model.search([
            ('employee_id', 'in', employees.ids),
            ('check_out', '=', False),
            ('check_in', '<', fields.Datetime.to_string(start_dt)),
        ], order='check_in desc, id desc')
        attendance_by_employee = {}
        for attendance in attendances:
            attendance_by_employee.setdefault(attendance.employee_id.id, attendance)

        total_hours = 0.0
        total_cost = 0.0
        for user in labor_users:
            employee = employee_by_user.get(user.id)
            attendance = attendance_by_employee.get(employee.id) if employee else False
            if not attendance:
                continue
            attendance_start = fields.Datetime.to_datetime(attendance.check_in)
            waiting_start = self._get_latest_labor_log_boundary(user, attendance_start, start_dt)
            if waiting_start >= start_dt:
                continue
            waiting_hours = max((start_dt - waiting_start).total_seconds() / 3600.0, 0.0)
            hourly_rate = self._get_labor_user_hourly_rate(employee, start_dt)
            total_hours += waiting_hours
            total_cost += waiting_hours * hourly_rate
        return total_hours, total_cost

    def _refresh_labor_hours_from_logs(self):
        for rec in self:
            labor_hours, labor_cost = rec._get_worker_labor_totals()
            rec.labor_hours = labor_hours
            rec.labor_cost = labor_cost

    def _open_worker_time_logs(self, worker_users, start_datetime=None):
        self.ensure_one()
        worker_users = worker_users.filtered(lambda user: user and not user.share)
        if not worker_users:
            return
        log_model_name, inverse_field = self._worker_time_log_info()
        log_model = self.env[log_model_name]
        existing_active_logs = log_model.search([
            (inverse_field, '=', self.id),
            ('user_id', 'in', worker_users.ids),
            ('end_datetime', '=', False),
        ])
        active_user_ids = set(existing_active_logs.mapped('user_id').ids)
        values_list = []
        start_datetime = start_datetime or fields.Datetime.now()
        for user in worker_users:
            if user.id in active_user_ids:
                continue
            values_list.append({
                inverse_field: self.id,
                'user_id': user.id,
                'start_datetime': start_datetime,
            })
        if values_list:
            log_model.create(values_list)

    def _close_worker_time_logs(self, close_datetime=None):
        self.ensure_one()
        log_model_name, inverse_field = self._worker_time_log_info()
        log_model = self.env[log_model_name]
        active_logs = log_model.search([
            (inverse_field, '=', self.id),
            ('end_datetime', '=', False),
        ])
        if active_logs:
            active_logs.write({'end_datetime': close_datetime or fields.Datetime.now()})

    @api.onchange('worker_ids')
    def _onchange_worker_ids_sync_users(self):
        for rec in self:
            rec.worker_user_ids = rec.worker_ids.mapped('user_id')

    def _move_product_into_stage(self):
        for rec in self:
            production = rec.production_order_id
            if not production:
                continue
            production._move_materials_to_stage_wip(rec._name)

    def _get_stage_line_ids_data(self, field_name):
        self.ensure_one()
        raw_value = self[field_name] or '[]'
        try:
            line_ids = json.loads(raw_value)
        except Exception:
            line_ids = []
        if not isinstance(line_ids, list):
            line_ids = []
        line_ids = [int(line_id) for line_id in line_ids if str(line_id).isdigit()]
        return self.env['furniture.mrp.production.line'].browse(line_ids).exists()

    def _set_stage_line_ids_data(self, field_name, lines):
        self.ensure_one()
        self.write({field_name: json.dumps(lines.ids)})
        if (
            self.production_order_id
            and not self.env.context.get('furniture_skip_line_consolidation')
            and field_name in (
                'active_production_line_ids_data',
                'quality_production_line_ids_data',
                'completed_production_line_ids_data',
            )
        ):
            self.production_order_id._consolidate_equivalent_production_lines()

    def _add_stage_active_lines(self, production_lines):
        self.ensure_one()
        if not production_lines:
            return
        active_lines = self._get_stage_line_ids_data('active_production_line_ids_data') | production_lines.exists()
        self._set_stage_line_ids_data('active_production_line_ids_data', active_lines)

    def _get_stage_quality_candidate_lines(self):
        self.ensure_one()
        if not self.production_order_id:
            return self.env['furniture.mrp.production.line']
        stage_code = self.production_order_id._stage_model_to_code(self._name)
        if not stage_code:
            return self.env['furniture.mrp.production.line']
        return self.production_order_id._get_stage_quality_candidate_lines(self, stage_code)

    def _maybe_open_stage_quality_send_wizard(self):
        self.ensure_one()
        if self.env.context.get('furniture_skip_stage_quality_prompt'):
            return False
        candidate_lines = self._get_stage_quality_candidate_lines()
        if len(candidate_lines) > 1:
            stage_code = self.production_order_id._stage_model_to_code(self._name)
            return self.production_order_id._open_stage_quality_send_wizard(self, stage_code, candidate_lines)
        return False

    def _maybe_open_stage_start_carryover_wizard(self):
        self.ensure_one()
        if self.env.context.get('furniture_skip_stage_start_prompt'):
            return False
        if self.state not in ('pending', 'in_progress') or not self.production_order_id:
            return False
        stage_code = self.production_order_id._stage_model_to_code(self._name)
        if not stage_code:
            return False
        if self.production_order_id._is_material_only_stage(stage_code):
            return False
        payloads = self._get_startable_stage_work_payloads(stage_code)
        if not payloads:
            first_stage_lines = self.production_order_id._get_first_stage_start_line_candidates(stage_code)
            if first_stage_lines:
                return self.production_order_id._open_first_stage_start_wizard(self, stage_code, first_stage_lines)
        return self.production_order_id._open_stage_start_carryover_wizard(self, stage_code, payloads)

    @api.depends('production_order_id', 'state')
    def _compute_store_request_status(self):
        Request = self.env['furniture.mrp.store.request'].sudo()
        for rec in self:
            request = Request.search([
                ('stage_order_model', '=', rec._name),
                ('stage_order_res_id', '=', rec.id),
                ('state', 'in', ('pending', 'approved', 'started', 'rejected', 'cancelled')),
            ], order='requested_at desc, id desc', limit=1) if rec.id else Request
            rec.store_request_id = request
            if (
                request
                and request.state == 'approved'
                and 'receipt_confirmed' in request._fields
                and request.material_line_ids.mapped('issue_move_ids')
                and not request.receipt_confirmed
            ):
                rec.store_request_state = 'awaiting_receipt'
            else:
                rec.store_request_state = request.state if request else 'none'

    def _get_store_request(self, states=False):
        self.ensure_one()
        domain = [
            ('stage_order_model', '=', self._name),
            ('stage_order_res_id', '=', self.id),
        ]
        if states:
            domain.append(('state', 'in', tuple(states)))
        return self.env['furniture.mrp.store.request'].sudo().search(
            domain, order='requested_at desc, id desc', limit=1,
        )

    def action_open_store_request(self):
        self.ensure_one()
        request = self._get_store_request(('pending', 'approved', 'started', 'rejected'))
        if not request:
            raise UserError(_('لا يوجد إذن مخزن لهذه المرحلة حتى الآن.'))
        return {
            'type': 'ir.actions.act_window',
            'name': request.name,
            'res_model': request._name,
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_request_store_approval(self):
        self.ensure_one()
        self._check_stage_operation_access()
        if self.state != 'pending':
            raise UserError(_('طلب إذن المخزن متاح قبل بدء المرحلة فقط.'))
        existing = self._get_store_request(('pending', 'approved'))
        if existing:
            return self.action_open_store_request()
        request_context = dict(
            self.env.context,
            furniture_store_request_only=True,
            default_request_only=True,
        )
        wizard_action = self.with_context(request_context)._maybe_open_stage_start_carryover_wizard()
        if wizard_action:
            wizard_action['name'] = _('طلب إذن المخزن للمنتجات المختارة')
            wizard_action['context'] = dict(
                wizard_action.get('context', {}),
                furniture_store_request_only=True,
                default_request_only=True,
            )
            return wizard_action
        return self.env['furniture.mrp.store.request'].with_context(
            request_context,
        )._request_direct_stage_start(self)

    def _get_remaining_stage_work_payloads(self):
        self.ensure_one()
        if not self.production_order_id:
            return []
        stage_code = self.production_order_id._stage_model_to_code(self._name)
        if not stage_code:
            return []
        return self._get_startable_stage_work_payloads(stage_code)

    def _format_stage_work_payloads(self, payloads):
        self.ensure_one()
        if not payloads:
            return ''
        order = self.production_order_id
        labels = []
        for payload in payloads:
            qty = payload.get('qty') or 0.0
            uom = payload.get('uom')
            product = payload.get('product')
            label = payload.get('label') or (product.display_name if product else '')
            qty_label = order._format_dimension_value(qty) if order else ('%s' % qty)
            labels.append('%s × %s%s' % (label, qty_label, (' ' + uom.name) if uom else ''))
        return '، '.join(labels)

    def action_start(self):
        self._check_stage_operation_access()
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_('يمكن بدء الأوامر المنتظرة فقط.'))
            if not self.env.context.get('furniture_storekeeper_approval_bypass'):
                approved_request = rec._get_store_request(('approved',))
                if approved_request:
                    return approved_request.with_user(self.env.user).action_start_approved()
                pending_request = rec._get_store_request(('pending',))
                if pending_request:
                    return self.env['furniture.mrp.store.request']._display_request_notification(
                        pending_request, existing=True,
                    )
                raise UserError(_(
                    'اطلب إذن المخزن أولًا، واختر المنتجات والكميات، ثم انتظر موافقة أمين المخزن.'
                ))
            wizard_action = rec._maybe_open_stage_start_carryover_wizard()
            if wizard_action:
                return wizard_action
            production = rec.production_order_id
            stage_code = (
                production._stage_model_to_code(rec._name)
                if production else False
            )
            material_only_stage = bool(
                production and production._is_material_only_stage(stage_code)
            )
            if material_only_stage:
                material_only_lines = rec._get_stage_line_ids_data(
                    'active_production_line_ids_data'
                )
                if not material_only_lines:
                    material_only_lines = (
                        production._get_material_only_stage_start_line_candidates(
                            stage_code,
                            stage_order=rec,
                        )
                    )
                    rec.with_context(
                        furniture_skip_line_consolidation=True,
                    )._add_stage_active_lines(material_only_lines)
                if not material_only_lines:
                    raise UserError(_(
                        'لا توجد أصناف مختارة لم تبدأ مرحلة %s.'
                    ) % STAGE_REPORT_LABEL_MAP.get(stage_code, stage_code))
                production._get_or_create_first_stage_material_lines(
                    material_only_lines,
                    stage_code,
                )
            start_mode = self.env.context.get('furniture_stage_start_mode')
            if (
                not material_only_stage
                and not start_mode
                and not rec._has_startable_stage_work()
            ):
                raise UserError(_(
                    'لا يوجد أي منتج جاهز داخل صالة هذه المرحلة. '
                    'حوّل المنتج إلى الصالة أولًا ثم ابدأ التشغيل.'
                ))
            labor_users = rec._get_selected_labor_users()
            if not labor_users:
                raise UserError(_('اختر عاملًا أو مشرفًا واحدًا على الأقل مضافًا لنفس قسم هذه المرحلة.'))
            selected_work_only = start_mode == 'selected_work'
            first_stage_selected = start_mode == 'first_stage_selected'
            skip_material_move = self.env.context.get('furniture_skip_material_move')
            if production and not selected_work_only and not first_stage_selected and not skip_material_move:
                active_lines = rec._get_stage_line_ids_data('active_production_line_ids_data')
                if active_lines:
                    material_lines = production.material_line_ids.filtered(lambda line: (
                        line.production_line_id in active_lines
                        and line.stage == stage_code
                        and line.product_id
                        and line.qty_needed
                    ))
                    if material_lines:
                        production._move_materials_to_stage_wip(rec._name, material_lines=material_lines)
                elif rec._name == 'furniture.mrp.priming':
                    rec._move_product_into_stage()
                elif production._has_stage_work_location_stock(rec._name):
                    production._ensure_stage_transfer_ready(rec._name)
                    production._move_materials_to_stage_wip(rec._name)
            now = fields.Datetime.now()
            waiting_labor_hours, waiting_labor_cost = rec._compute_waiting_labor_values(labor_users, now)
            rec.write({
                'state': 'in_progress',
                'date_start': now,
                'labor_hours': 0.0,
                'waiting_labor_hours': (rec.waiting_labor_hours or 0.0) + waiting_labor_hours,
                'waiting_labor_cost': (rec.waiting_labor_cost or 0.0) + waiting_labor_cost,
                'include_existing_work_products': start_mode in ('with_existing', 'selected_work'),
                'selected_work_products_only': selected_work_only,
            })
            rec._open_worker_time_logs(labor_users, start_datetime=now)
            message = _('▶️ تم بدء العمل بواسطة: %s') % ', '.join(labor_users.mapped('name'))
            if waiting_labor_hours:
                message += '<br/>' + _('تم تحميل وقت انتظار العمالة: %.2f ساعة بتكلفة %.2f') % (
                    waiting_labor_hours,
                    waiting_labor_cost,
                )
            rec.message_post(body=message)

    def action_send_to_quality(self):
        """إرسال للجودة"""
        self._check_stage_operation_access()
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('يجب أن يكون العمل جارياً لإرساله للجودة.'))
            wizard_action = rec._maybe_open_stage_quality_send_wizard()
            if wizard_action:
                return wizard_action
            candidate_lines = rec._get_stage_quality_candidate_lines()
            if candidate_lines:
                rec._send_selected_lines_to_quality(candidate_lines)
            elif rec.production_order_id and rec.production_order_id.production_line_ids:
                raise UserError(_(
                    'لا توجد أصناف بدأت فعليًا داخل هذه المرحلة لإرسالها للجودة.'
                ))
            else:
                rec._close_worker_time_logs()
                rec._refresh_labor_hours_from_logs()
                rec.write({'state': 'quality_check'})
                rec.message_post(body=_('🔍 تم إرسال الأمر لمراقب الجودة'))

    def _send_selected_lines_to_quality(self, production_lines):
        self.ensure_one()
        if self.state != 'in_progress':
            raise UserError(_('يجب أن يكون العمل جارياً لإرساله للجودة.'))
        production_lines = production_lines.exists()
        if not production_lines:
            raise UserError(_('اختار صنف واحد على الأقل لإرساله للجودة.'))
        candidate_lines = self._get_stage_quality_candidate_lines()
        invalid_lines = production_lines - candidate_lines
        if invalid_lines:
            raise UserError(_('بعض الأصناف المختارة ليست جاهزة للإرسال للجودة في هذه المرحلة.'))

        remaining_lines = candidate_lines - production_lines
        if not remaining_lines:
            self._close_worker_time_logs()
        self._refresh_labor_hours_from_logs()
        quality_lines = self._get_stage_line_ids_data('quality_production_line_ids_data') | production_lines
        self._set_stage_line_ids_data('quality_production_line_ids_data', quality_lines)
        self.write({'state': 'quality_check', 'quality_check': 'pending'})
        names = (
            self.production_order_id._get_production_lines_display_list(production_lines)
            if self.production_order_id else production_lines.mapped('display_name')
        )
        self.message_post(body=_('🔍 تم إرسال الأصناف المختارة للجودة: %s') % '، '.join(names))

    def _get_valid_current_quality_lines(self, quality_lines):
        self.ensure_one()
        production = self.production_order_id
        quality_lines = quality_lines.exists()
        if not production or not quality_lines:
            return quality_lines
        stage_code = production._stage_model_to_code(self._name)
        if not stage_code:
            return quality_lines

        active_lines = self._get_stage_line_ids_data('active_production_line_ids_data')
        if active_lines:
            return quality_lines & active_lines

        first_stage_lines = self.first_stage_production_line_ids
        if first_stage_lines:
            return quality_lines & first_stage_lines

        if (
            self.include_existing_work_products
            or self.selected_work_products_only
            or production._get_registered_stage_carryover_payloads(stage_code)
        ):
            return self.env['furniture.mrp.production.line']
        return quality_lines

    def action_approve_quality(self):
        """قبول الجودة وإنهاء المرحلة"""
        self._check_stage_operation_access()
        for rec in self:
            quality_lines = rec._get_stage_line_ids_data('quality_production_line_ids_data')
            valid_quality_lines = rec._get_valid_current_quality_lines(quality_lines)
            if valid_quality_lines != quality_lines:
                quality_lines = valid_quality_lines
                rec.with_context(
                    furniture_skip_line_consolidation=True,
                )._set_stage_line_ids_data('quality_production_line_ids_data', quality_lines)
            stage_code = rec.production_order_id._stage_model_to_code(rec._name) if rec.production_order_id else False
            material_only_stage = bool(
                rec.production_order_id
                and rec.production_order_id._is_material_only_stage(stage_code)
            )
            carryover_cost_payloads = (
                rec.production_order_id._get_registered_stage_carryover_payloads(stage_code)
                if rec.production_order_id and stage_code else []
            )
            if quality_lines:
                rec._refresh_labor_hours_from_logs()
                summary = ''
                if rec.production_order_id:
                    rec.production_order_id._move_stage_work_to_stock(rec._name, production_lines=quality_lines)
                    rec.production_order_id._record_stage_costs(
                        rec,
                        stage_code,
                        production_lines=quality_lines,
                        carryover_payloads=carryover_cost_payloads,
                    )
                    summary = rec.production_order_id.stage_completion_summary or ''
                completed_lines = rec._get_stage_line_ids_data('completed_production_line_ids_data') | quality_lines
                active_lines = rec._get_stage_line_ids_data('active_production_line_ids_data') - completed_lines
                rec.with_context(furniture_skip_line_consolidation=True).write({
                    'completed_production_line_ids_data': json.dumps(completed_lines.ids),
                    'active_production_line_ids_data': json.dumps(active_lines.ids),
                    'quality_production_line_ids_data': '[]',
                })
                if rec.production_order_id:
                    rec.production_order_id._auto_transfer_completed_stage_lines(
                        stage_code, quality_lines,
                    )
                    rec.production_order_id._consolidate_equivalent_production_lines()
                remaining_lines = rec._get_stage_quality_candidate_lines()
                if remaining_lines:
                    rec.write({'state': 'in_progress', 'quality_check': 'pending'})
                    names = (
                        rec.production_order_id._get_production_lines_display_list(quality_lines)
                        if rec.production_order_id else quality_lines.mapped('display_name')
                    )
                    message = (
                        _('✅ تم قبول الأصناف المختارة وإكمال المرحلة: %s')
                        if material_only_stage else
                        _('✅ تم قبول الأصناف المختارة واستلامها في مخزن المرحلة: %s')
                    ) % '، '.join(names)
                    if summary:
                        message += '<br/>' + _('ملخص التشغيل: %s') % summary
                    rec.message_post(body=message)
                    continue
                remaining_work_payloads = rec._get_remaining_stage_work_payloads()
                if remaining_work_payloads:
                    rec._close_worker_time_logs()
                    rec.write({
                        'state': 'pending',
                        'quality_check': 'pending',
                        'date_finish': False,
                    })
                    names = (
                        rec.production_order_id._get_production_lines_display_list(quality_lines)
                        if rec.production_order_id else quality_lines.mapped('display_name')
                    )
                    message = (
                        _('✅ تم قبول الأصناف المختارة وإكمال المرحلة: %s')
                        if material_only_stage else
                        _('✅ تم قبول الأصناف المختارة واستلامها في مخزن المرحلة: %s')
                    ) % '، '.join(names)
                    if summary:
                        message += '<br/>' + _('ملخص التشغيل: %s') % summary
                    message += '<br/>' + _(
                        'لسه فيه كميات في صالة المرحلة لم تكتمل: %s'
                    ) % rec._format_stage_work_payloads(remaining_work_payloads)
                    rec.message_post(body=message)
                    continue
                pending_route_lines = rec.env['furniture.mrp.production.line']
                if rec.production_order_id:
                    pending_route_lines = rec.production_order_id._get_stage_incomplete_line_candidates(stage_code)
                if pending_route_lines:
                    rec._close_worker_time_logs()
                    rec.write({
                        'state': 'pending',
                        'quality_check': 'pending',
                        'date_finish': False,
                    })
                    names = rec.production_order_id._get_production_lines_display_list(pending_route_lines)
                    message = (
                        _(
                            '✅ تم قبول الأصناف المختارة وإكمال المرحلة.'
                            '<br/>لسه فيه كميات في مسارها هذه المرحلة: %s'
                        )
                        if material_only_stage else
                        _(
                            '✅ تم قبول الأصناف المختارة واستلامها في مخزن المرحلة.'
                            '<br/>لسه فيه كميات في مسارها هذه المرحلة: %s'
                        )
                    ) % '، '.join(names)
                    if summary:
                        message += '<br/>' + _('ملخص التشغيل: %s') % summary
                    rec.message_post(body=message)
                    continue

            rec._close_worker_time_logs()
            rec._refresh_labor_hours_from_logs()
            carryover_moved = False
            if rec.production_order_id:
                if stage_code and rec.production_order_id._get_registered_stage_carryover_payloads(stage_code):
                    rec.production_order_id._move_stage_work_to_stock(rec._name)
                    rec.production_order_id._record_stage_costs(
                        rec,
                        stage_code,
                        carryover_payloads=carryover_cost_payloads,
                    )
                    carryover_moved = True
            pending_stage_lines = rec.env['furniture.mrp.production.line']
            if rec.production_order_id:
                pending_stage_lines = rec.production_order_id._get_stage_pending_start_line_candidates(rec, stage_code)
            if pending_stage_lines:
                rec.write({
                    'state': 'pending',
                    'quality_check': 'pending',
                    'date_finish': False,
                })
                names = rec.production_order_id._get_production_lines_display_list(pending_stage_lines)
                message = (
                    _(
                        '✅ تم قبول الكمية المختارة وإكمال المرحلة.'
                        '<br/>لسه فيه كميات لم تبدأ هذه المرحلة: %s'
                    )
                    if material_only_stage else
                    _(
                        '✅ تم قبول الكمية المختارة واستلامها في مخزن المرحلة.'
                        '<br/>لسه فيه كميات لم تبدأ هذه المرحلة: %s'
                    )
                ) % '، '.join(names)
                rec.message_post(body=message)
                continue
            remaining_work_payloads = rec._get_remaining_stage_work_payloads()
            if remaining_work_payloads:
                rec.write({
                    'state': 'pending',
                    'quality_check': 'pending',
                    'date_finish': False,
                })
                rec.message_post(body=_(
                    'لسه فيه كميات في صالة المرحلة لم تكتمل: %s'
                ) % rec._format_stage_work_payloads(remaining_work_payloads))
                continue
            pending_route_lines = rec.env['furniture.mrp.production.line']
            if rec.production_order_id:
                pending_route_lines = rec.production_order_id._get_stage_incomplete_line_candidates(stage_code)
            if pending_route_lines:
                rec.write({
                    'state': 'pending',
                    'quality_check': 'pending',
                    'date_finish': False,
                })
                names = rec.production_order_id._get_production_lines_display_list(pending_route_lines)
                rec.message_post(body=_(
                    'لسه فيه كميات في مسارها هذه المرحلة: %s'
                ) % '، '.join(names))
                continue
            rec.write({
                'state': 'done',
                'quality_check': 'pass',
                'date_finish': fields.Datetime.now(),
            })
            summary = ''
            if rec.production_order_id:
                if quality_lines:
                    summary = rec.production_order_id.stage_completion_summary or ''
                elif not carryover_moved:
                    rec.production_order_id._move_stage_work_to_stock(rec._name)
                    summary = rec.production_order_id.stage_completion_summary or ''
            message = (
                _('✅ اجتاز فحص الجودة - تم تسجيل اكتمال المرحلة بدون تحريك مكان المنتج النهائي')
                if material_only_stage else
                _('✅ اجتاز فحص الجودة - تم الانتهاء من المرحلة واستلام المنتج شبه النهائي في مخزن القسم')
            )
            if rec.production_order_id and summary:
                message += '<br/>' + _('ملخص التشغيل: %s') % summary
            rec.message_post(body=message)

    def action_transfer_finished_product(self):
        self._check_stage_operation_access()
        for rec in self:
            if not rec.production_order_id:
                raise UserError(_('لا يوجد أمر تشغيل أسبوعي مرتبط بهذه المرحلة.'))
            return rec.production_order_id.action_transfer_finished_product()

    def action_view_stage_cost_entries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('تكلفة الدفعات في هذه المرحلة'),
            'res_model': 'furniture.mrp.stage.cost.entry',
            'view_mode': 'list,pivot,graph,form',
            'domain': [
                ('stage_order_model', '=', self._name),
                ('stage_order_res_id', '=', self.id),
            ],
            'context': {'search_default_group_product': 1},
        }

    def action_print_cost_report(self):
        self.ensure_one()
        if not self.production_order_id:
            raise UserError(_('لا يوجد أمر تصنيع مرتبط بهذه المرحلة.'))
        report = self.env.ref(
            'furniture_mrp.action_report_furniture_mrp_stage_cost_details'
        ).with_context(
            furniture_stage_order_model=self._name,
            furniture_stage_order_res_id=self.id,
        )
        # Keep ``data`` empty so the web client includes the production docid in
        # the report URL.  Stage identity travels in the action context and is
        # therefore preserved by both the generic PDF and HTML report routes.
        return report.report_action(self.production_order_id, config=False)

    def get_stage_cost_report_data(self):
        """Return approved costs belonging to this exact stage order only."""
        self.ensure_one()
        production = self.production_order_id
        stage_code = production._stage_model_to_code(self._name) if production else False
        entries = self.env['furniture.mrp.stage.cost.entry'].sudo().search([
            ('stage_order_model', '=', self._name),
            ('stage_order_res_id', '=', self.id),
        ], order='accepted_at, id')
        rows = []
        for entry in entries:
            line = entry.production_line_id
            furniture_model = (
                line.furniture_order_model_id
                or entry.product_id.furniture_model_id
            )
            stage_total = (entry.stage_material_cost or 0.0) + (entry.stage_labor_cost or 0.0)
            rows.append({
                'entry': entry,
                'product': entry.product_id,
                'model': furniture_model,
                'dimension': entry.dimension_label or line.dimension_label,
                'quantity': entry.quantity or 0.0,
                'uom': entry.product_uom_id,
                'material_cost': entry.stage_material_cost or 0.0,
                'labor_cost': entry.stage_labor_cost or 0.0,
                'total_cost': stage_total,
                'unit_cost': stage_total / entry.quantity if entry.quantity else 0.0,
                'accepted_at': entry.accepted_at,
                'accepted_by': entry.accepted_by_id,
            })
        total_quantity = sum(row['quantity'] for row in rows)
        total_material = sum(row['material_cost'] for row in rows)
        total_labor = sum(row['labor_cost'] for row in rows)
        total_cost = total_material + total_labor
        state_selection = dict(self._fields['state'].selection)
        return {
            'production': production,
            'stage_order': self,
            'stage_code': stage_code,
            'stage_label': STAGE_REPORT_LABEL_MAP.get(stage_code, stage_code or self._description),
            'state_label': state_selection.get(self.state, self.state or '-'),
            'rows': rows,
            'total_quantity': total_quantity,
            'total_material': total_material,
            'total_labor': total_labor,
            'total_cost': total_cost,
            'unit_cost': total_cost / total_quantity if total_quantity else 0.0,
            'labor_hours': self.total_labor_hours or 0.0,
            'waiting_hours': self.waiting_labor_hours or 0.0,
            'waiting_cost': self.waiting_labor_cost or 0.0,
            'workers': ', '.join(self.worker_user_ids.mapped('name')),
        }

    def action_back_to_production_order(self):
        """Open the linked weekly production order and reset long breadcrumbs."""
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        if not production:
            raise UserError(_('لا يوجد أمر تصنيع رئيسي مرتبط بهذه المرحلة.'))
        return {
            'type': 'ir.actions.act_window',
            'name': production.name,
            'res_model': 'furniture.mrp.production',
            'res_id': production.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'main',
        }

    def action_open_mrp_dashboard(self):
        """Open the factory dashboard and reset the current navigation stack."""
        self.ensure_one()
        self._check_stage_operation_access()
        action = self.env['ir.actions.actions']._for_xml_id(
            'furniture_mrp.action_furniture_mrp_dashboard'
        )
        action['target'] = 'main'
        return action

    def action_reject_quality(self):
        """رفض الجودة وإعادة للعمل"""
        self._check_stage_operation_access()
        for rec in self:
            quality_lines = rec._get_stage_line_ids_data('quality_production_line_ids_data')
            if quality_lines:
                labor_users = rec._get_selected_labor_users()
                rec._set_stage_line_ids_data(
                    'quality_production_line_ids_data',
                    rec.env['furniture.mrp.production.line'],
                )
                rec.write({'quality_check': 'rework', 'state': 'in_progress'})
                rec._open_worker_time_logs(labor_users)
                names = (
                    rec.production_order_id._get_production_lines_display_list(quality_lines)
                    if rec.production_order_id else quality_lines.mapped('display_name')
                )
                rec.message_post(body=_('❌ تم رفض الأصناف المختارة ورجعت للشغل: %s') % '، '.join(names))
                continue
            labor_users = rec._get_selected_labor_users()
            rec._close_worker_time_logs()
            rec.write({'quality_check': 'rework', 'state': 'in_progress', 'labor_hours': 0.0})
            rec._open_worker_time_logs(labor_users)
            rec.message_post(body=_('❌ فشل فحص الجودة - يحتاج إعادة عمل'))
