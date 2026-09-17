# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


PAINTING_SUBSTAGE_STATE = [
    ('pending', 'في الانتظار'),
    ('in_progress', 'جاري التنفيذ'),
    ('done', '✅ منتهي'),
]

PAINTING_EXTERNAL_FLOW_STATE = [
    ('pending', 'في انتظار الخروج'),
    ('out', 'خروج'),
    ('delivered', 'تم التسليم'),
    ('received', 'مستلم'),
]

PAINTING_SUBSTAGE_FIELDS = [
    ('priming', 'تقديم', 'substage_priming_state', 'substage_priming_required'),
    ('assembly', 'تجميع', 'substage_assembly_state', 'substage_assembly_required'),
    ('cells', 'حلايا', 'substage_cells_state', 'substage_cells_required'),
    ('veneer', 'قشرة', 'substage_veneer_state', 'substage_veneer_required'),
    ('impregnation', 'تشريب', 'substage_impregnation_state', 'substage_impregnation_required'),
    ('paint', 'دهان', 'substage_paint_state', 'substage_paint_required'),
]

PAINTING_EXTERNAL_SUBSTAGE_FIELDS = {
    'cells': (
        'حلايا',
        'substage_cells_state',
        'substage_cells_required',
        'substage_cells_external_state',
    ),
    'veneer': (
        'قشرة',
        'substage_veneer_state',
        'substage_veneer_required',
        'substage_veneer_external_state',
    ),
    'paint': (
        'دهان',
        'substage_paint_state',
        'substage_paint_required',
        'substage_paint_external_state',
    ),
}


class FurnitureMrpPainting(models.Model):
    """المرحلة 2: تصنيع دهانات"""
    _name = 'furniture.mrp.painting'
    _description = 'مرحلة تصنيع دهانات'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'painting_employee_rel', 'painting_id', 'employee_id',
        string='عمال تصنيع دهانات',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.painting.worker.log', 'painting_id',
        string='سجل وقت العمال',
    )
    priming_order_id = fields.Many2one(
        'furniture.mrp.priming', string='أمر التقديم المرجعي', readonly=True,
    )
    substage_plan = fields.Selection(
        [('custom', 'مراحل مختارة')],
        string='خطة المراحل الداخلية',
        default='custom',
        tracking=True,
    )

    # ─── حقول خاصة بالدهانات ──────────────────────────────────────────────────
    paint_type = fields.Selection([
        ('oil',       'دهان زيتي'),
        ('water',     'دهان مائي'),
        ('lacquer',   'لاكيه'),
        ('stain',     'ستاين'),
        ('varnish',   'ورنيش'),
        ('other',     'أخرى'),
    ], string='نوع الدهان', default='lacquer')

    paint_color    = fields.Char(string='لون الدهان / الكود')
    num_coats      = fields.Integer(string='عدد طبقات الدهان', default=2)
    drying_time_hr = fields.Float(string='وقت التجفيف (ساعة)', default=12.0)

    substage_priming_required = fields.Boolean(string='التقديم', default=True)
    substage_assembly_required = fields.Boolean(string='التجميع', default=True)
    substage_cells_required = fields.Boolean(string='حلايا', default=True)
    substage_veneer_required = fields.Boolean(string='القشرة', default=True)
    substage_impregnation_required = fields.Boolean(string='التشريب', default=True)
    substage_paint_required = fields.Boolean(string='الدهان', default=True)

    # ─── المواد المستخدمة ─────────────────────────────────────────────────────
    paint_qty_liters = fields.Float(string='كمية الدهان (لتر)', default=0.0)
    thinner_qty      = fields.Float(string='كمية المخفف (لتر)', default=0.0)
    varnish_qty_liters = fields.Float(string='كمية الورنيش (لتر)', default=0.0)

    substage_priming_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة التقديم',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_assembly_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة التجميع',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_cells_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة الحلايا',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_veneer_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة القشرة',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_impregnation_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة التشريب',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_paint_state = fields.Selection(
        PAINTING_SUBSTAGE_STATE,
        string='مرحلة الدهان',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_cells_external_state = fields.Selection(
        PAINTING_EXTERNAL_FLOW_STATE,
        string='مرحلة الحلايا',
        default='pending',
        required=True,
        tracking=True,
        copy=False,
    )
    substage_veneer_external_state = fields.Selection(
        PAINTING_EXTERNAL_FLOW_STATE,
        string='مرحلة القشرة',
        default='pending',
        required=True,
        tracking=True,
        copy=False,
    )
    substage_paint_external_state = fields.Selection(
        PAINTING_EXTERNAL_FLOW_STATE,
        string='مرحلة الدهان',
        default='pending',
        required=True,
        tracking=True,
        copy=False,
    )
    current_substage = fields.Char(
        string='المرحلة الحالية داخل تصنيع الدهانات',
        compute='_compute_substage_tracking',
    )
    substage_progress = fields.Float(
        string='نسبة إنجاز المراحل الداخلية',
        compute='_compute_substage_tracking',
    )
    substage_summary = fields.Char(
        string='ملخص المراحل الداخلية',
        compute='_compute_substage_tracking',
    )
    all_substages_done = fields.Boolean(
        string='تم إنهاء كل المراحل الداخلية',
        compute='_compute_substage_tracking',
    )
    current_substage_is_external = fields.Boolean(
        string='المرحلة الحالية خارج المصنع',
        compute='_compute_substage_tracking',
    )

    def _get_active_substage_fields(self):
        self.ensure_one()
        return [
            definition for definition in PAINTING_SUBSTAGE_FIELDS
            if self[definition[3]]
        ]

    @api.constrains(
        'substage_plan',
        'substage_priming_required',
        'substage_assembly_required',
        'substage_cells_required',
        'substage_veneer_required',
        'substage_impregnation_required',
        'substage_paint_required',
        'state',
    )
    def _check_substage_plan_selection(self):
        for rec in self:
            if rec.state != 'pending' and not rec._get_active_substage_fields():
                raise ValidationError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات.'))

    @api.depends(
        'substage_plan',
        'substage_priming_required',
        'substage_assembly_required',
        'substage_cells_required',
        'substage_veneer_required',
        'substage_impregnation_required',
        'substage_paint_required',
        'substage_priming_state',
        'substage_assembly_state',
        'substage_cells_state',
        'substage_veneer_state',
        'substage_impregnation_state',
        'substage_paint_state',
        'state',
    )
    def _compute_substage_tracking(self):
        for rec in self:
            active_definitions = rec._get_active_substage_fields()
            completed = 0
            pending_labels = []
            active_labels = []
            active_codes = []
            raw_states = []
            for code, label, field_name, _required_field in active_definitions:
                field_state = rec[field_name]
                raw_states.append(field_state)
                if field_state == 'done':
                    completed += 1
                elif field_state == 'in_progress':
                    active_labels.append(label)
                    active_codes.append(code)
                elif field_state == 'pending':
                    pending_labels.append(label)
            if rec.state == 'done' and raw_states and all(state == 'pending' for state in raw_states):
                completed = len(active_definitions)
            total = len(active_definitions)
            if not total:
                current = _('لا توجد مراحل مختارة')
            elif completed == total and rec.state == 'in_progress':
                current = 'جاهز للجودة'
            elif rec.state == 'done':
                current = 'مكتمل'
            elif rec.state == 'quality_check':
                current = 'فحص الجودة'
            elif len(active_labels) == 1:
                current = active_labels[0]
            elif active_labels:
                current = _('%s مراحل جارية') % len(active_labels)
            elif len(pending_labels) == 1:
                current = pending_labels[0]
            elif pending_labels:
                current = _('%s مراحل بانتظار البدء') % len(pending_labels)
            else:
                current = False
            rec.current_substage = current
            rec.substage_progress = (completed / total) * 100 if total else 0.0
            rec.substage_summary = (
                _('%s/%s مراحل منتهية') % (completed, total)
                if total else
                _('لم يتم تحديد مراحل داخلية')
            )
            rec.all_substages_done = bool(total and completed == total)
            rec.current_substage_is_external = any(
                code in PAINTING_EXTERNAL_SUBSTAGE_FIELDS
                for code in active_codes
            )

    def _advance_completed_substage(self, code, label, field_name):
        """Complete one substage without affecting any other active substage."""
        self.ensure_one()
        self.write({field_name: 'done'})
        message = _('✅ تم إنهاء مرحلة %s بشكل مستقل.') % label
        if self.all_substages_done:
            message += '<br/>' + _('اكتملت كل مراحل تصنيع الدهانات ويمكن الإرسال للجودة.')
        self.message_post(body=message)

    def _get_internal_substage_action_fields(self):
        self.ensure_one()
        code = self.env.context.get('painting_internal_substage')
        definition = next(
            (
                item for item in PAINTING_SUBSTAGE_FIELDS
                if item[0] == code and item[0] not in PAINTING_EXTERNAL_SUBSTAGE_FIELDS
            ),
            False,
        )
        if not definition:
            raise UserError(_('مرحلة الدهانات الداخلية غير محددة.'))
        return definition

    def _get_external_substage_action_fields(self):
        self.ensure_one()
        code = self.env.context.get('painting_external_substage')
        definition = PAINTING_EXTERNAL_SUBSTAGE_FIELDS.get(code)
        if not definition:
            raise UserError(_('مرحلة الدهانات الخارجية غير محددة.'))
        return (code, *definition)

    def _check_external_substage_transition(self, expected_external_state):
        self.ensure_one()
        code, label, stage_state_field, required_field, external_state_field = (
            self._get_external_substage_action_fields()
        )
        if not self[required_field]:
            raise UserError(_('مرحلة %s غير مختارة في أمر تصنيع الدهانات.') % label)
        if self.state != 'in_progress' or self[stage_state_field] != 'in_progress':
            raise UserError(_('مرحلة %s ليست المرحلة الجارية حاليًا.') % label)
        if self[external_state_field] != expected_external_state:
            raise UserError(_('إجراء مرحلة %s غير متاح في حالتها الحالية.') % label)
        return code, label, stage_state_field, external_state_field

    def action_external_substage_exit(self):
        self._check_stage_operation_access()
        for rec in self:
            _code, label, _stage_state_field, external_state_field = (
                rec._check_external_substage_transition('pending')
            )
            rec.write({external_state_field: 'out'})
            rec.message_post(body=_('🚚 خرجت مرحلة %s من المخزن إلى الجهة الخارجية.') % label)
        return True

    def action_external_substage_deliver(self):
        self._check_stage_operation_access()
        for rec in self:
            _code, label, _stage_state_field, external_state_field = (
                rec._check_external_substage_transition('out')
            )
            rec.write({external_state_field: 'delivered'})
            rec.message_post(body=_('🤝 تم تسليم مرحلة %s للسائق / الجهة الخارجية.') % label)
        return True

    def action_external_substage_receive(self):
        self._check_stage_operation_access()
        for rec in self:
            code, label, stage_state_field, external_state_field = (
                rec._check_external_substage_transition('delivered')
            )
            rec.write({external_state_field: 'received'})
            rec.message_post(body=_('📥 تم استلام مرحلة %s بعد رجوعها للمخزن.') % label)
            rec._advance_completed_substage(code, label, stage_state_field)
        return True

    def _restart_substages_for_rework(self):
        self.ensure_one()
        active_definitions = self._get_active_substage_fields()
        if not active_definitions:
            raise UserError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات.'))
        vals = {
            field_name: 'in_progress'
            for _code, _label, field_name, _required_field in active_definitions
        }
        for code, _label, _field_name, _required_field in active_definitions:
            external_definition = PAINTING_EXTERNAL_SUBSTAGE_FIELDS.get(code)
            if external_definition:
                vals[external_definition[3]] = 'pending'
        self.write(vals)
        return [field_name for _code, _label, field_name, _required_field in active_definitions]

    def _reset_substages_for_next_batch(self):
        self.ensure_one()
        active_definitions = self._get_active_substage_fields()
        if not active_definitions:
            return
        if not all(self[field_name] == 'done' for _code, _label, field_name, _required_field in active_definitions):
            return
        vals = {
            field_name: 'pending'
            for _code, _label, field_name, _required_field in active_definitions
        }
        for code, _label, _field_name, _required_field in active_definitions:
            external_definition = PAINTING_EXTERNAL_SUBSTAGE_FIELDS.get(code)
            if external_definition:
                vals[external_definition[3]] = 'pending'
        self.write(vals)

    def _start_selected_substages(self):
        """Start every selected substage together; no substage blocks another."""
        self.ensure_one()
        pending_definitions = [
            definition for definition in self._get_active_substage_fields()
            if self[definition[2]] == 'pending'
        ]
        if not pending_definitions:
            return []
        self.write({definition[2]: 'in_progress' for definition in pending_definitions})
        self.message_post(
            body=_('🧩 بدأت مراحل تصنيع الدهانات المختارة معًا: %s') % (
                '، '.join(definition[1] for definition in pending_definitions)
            )
        )
        return [definition[2] for definition in pending_definitions]

    def action_start(self):
        for rec in self:
            if not rec._get_active_substage_fields():
                raise UserError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات قبل بدء العمل.'))
            if rec.state == 'pending':
                rec._reset_substages_for_next_batch()
        result = super().action_start()
        for rec in self:
            rec._start_selected_substages()
        return result

    def action_complete_internal_substage(self):
        self._check_stage_operation_access()
        for rec in self:
            code, label, field_name, required_field = rec._get_internal_substage_action_fields()
            if not rec[required_field]:
                raise UserError(_('مرحلة %s غير مختارة في أمر تصنيع الدهانات.') % label)
            if rec.state != 'in_progress' or rec[field_name] != 'in_progress':
                raise UserError(_('مرحلة %s ليست جارية حاليًا.') % label)
            rec._advance_completed_substage(code, label, field_name)
        return True

    def action_complete_current_substage(self):
        self._check_stage_operation_access()
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('يمكن إنهاء مرحلة داخلية فقط أثناء تنفيذ أمر تصنيع الدهانات.'))
            active_internal = [
                definition for definition in rec._get_active_substage_fields()
                if (
                    definition[0] not in PAINTING_EXTERNAL_SUBSTAGE_FIELDS
                    and rec[definition[2]] == 'in_progress'
                )
            ]
            if not active_internal:
                raise UserError(_('لا توجد مرحلة داخلية جارية حالياً.'))
            if len(active_internal) > 1:
                raise UserError(_('اختر زر إنهاء الموجود أمام المرحلة المطلوبة.'))
            code, label, field_name, _required_field = active_internal[0]
            rec._advance_completed_substage(code, label, field_name)

    def action_send_to_quality(self):
        for rec in self:
            if not rec._get_active_substage_fields():
                raise UserError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات قبل إرسالها للجودة.'))
            if not rec.all_substages_done:
                raise UserError(_('لا يمكن إرسال أمر تصنيع الدهانات للجودة قبل إنهاء كل المراحل الداخلية.'))
        return super().action_send_to_quality()

    def action_approve_quality(self):
        for rec in self:
            if not rec._get_active_substage_fields():
                raise UserError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات قبل اعتماد الجودة.'))
            if not rec.all_substages_done:
                raise UserError(_('لا يمكن اعتماد الجودة قبل إنهاء كل مراحل تصنيع الدهانات الداخلية.'))
        return super().action_approve_quality()

    def action_reject_quality(self):
        for rec in self:
            if not rec._get_active_substage_fields():
                raise UserError(_('اختار مرحلة واحدة على الأقل داخل تصنيع الدهانات قبل إعادة العمل.'))
        result = super().action_reject_quality()
        for rec in self:
            rec._restart_substages_for_rework()
            rec.message_post(
                body=_('🔁 تمت إعادة كل مراحل تصنيع الدهانات المختارة لإعادة العمل بشكل مستقل.')
            )
        return result
