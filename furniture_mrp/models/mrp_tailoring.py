# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


INTERNAL_SUBSTAGE_STATE = [
    ('pending', 'في الانتظار'),
    ('in_progress', 'جاري التنفيذ'),
    ('done', '✅ منتهي'),
]

TAILORING_DISPLAY_STATE = [
    ('pending', 'في الانتظار'),
    ('in_progress', 'جاري التنفيذ'),
    ('material_shortage', '⚠️ مواد ناقصة'),
    ('quality_check', 'فحص الجودة'),
    ('done', '✅ منتهي'),
]

TAILORING_INTERNAL_SUBSTAGE_FIELDS = [
    ('cutting', 'التفصيل', 'substage_cutting_state'),
    ('sewing', 'الخياطة', 'substage_sewing_state'),
    ('ironing', 'التكاوي', 'substage_ironing_state'),
]


class FurnitureMrpTailoring(models.Model):
    """المرحلة 6: تفصيل"""
    _name = 'furniture.mrp.tailoring'
    _description = 'مرحلة تفصيل'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'tailoring_employee_rel', 'tailoring_id', 'employee_id',
        string='الخياطون والمفصلون',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.tailoring.worker.log', 'tailoring_id',
        string='سجل وقت العمال',
    )
    finishing_order_id = fields.Many2one(
        'furniture.mrp.finishing', string='أمر التجهيز المرجعي', readonly=True,
    )

    # ─── حقول خاصة بالتفصيل ──────────────────────────────────────────────────
    cutting_pattern  = fields.Char(string='مرجع الباترون / الطقم')
    fabric_qty_cut_m = fields.Float(string='القماش المقطوع (متر)', default=0.0)
    waste_fabric_m   = fields.Float(string='الهالك من القماش (متر)', default=0.0)

    cutting_method = fields.Selection([
        ('manual',  'يدوي'), ('machine', 'ماكينة'), ('laser', 'ليزر'),
    ], string='طريقة التقطيع', default='manual')

    # ─── مكونات التفصيل ───────────────────────────────────────────────────────
    thread_spools    = fields.Integer(string='بكرات خيط', default=0)
    needles_count    = fields.Integer(string='إبر مستخدمة', default=0)
    lining_qty_m     = fields.Float(string='بطانة (متر)', default=0.0)
    velcro_meters    = fields.Float(string='فيلكرو (متر)', default=0.0)

    # ─── الفحص النهائي قبل الشحن ─────────────────────────────────────────────
    final_inspection = fields.Selection([
        ('pending', 'في الانتظار'), ('passed', '✅ اجتاز'), ('failed', '❌ مرفوض'),
    ], string='الفحص النهائي', default='pending', tracking=True)
    final_inspector_id = fields.Many2one('hr.employee', string='مفتش التسليم')
    inspection_notes   = fields.Text(string='ملاحظات الفحص النهائي')
    ready_for_stock    = fields.Boolean(string='جاهز للمخزن', default=False, tracking=True)

    substage_cutting_state = fields.Selection(
        INTERNAL_SUBSTAGE_STATE,
        string='مرحلة التفصيل',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_sewing_state = fields.Selection(
        INTERNAL_SUBSTAGE_STATE,
        string='مرحلة الخياطة',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_ironing_state = fields.Selection(
        INTERNAL_SUBSTAGE_STATE,
        string='مرحلة التكاوي',
        default='pending',
        tracking=True,
        copy=False,
    )
    current_substage = fields.Char(
        string='المرحلة الحالية داخل التفصيل',
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
        string='تم إنهاء كل مراحل التفصيل الداخلية',
        compute='_compute_substage_tracking',
    )
    has_material_shortage = fields.Boolean(
        string='يوجد مواد ناقصة',
        compute='_compute_material_shortage_status',
        readonly=True,
    )
    material_shortage_details = fields.Text(
        string='تفاصيل المواد الناقصة',
        compute='_compute_material_shortage_status',
        readonly=True,
    )
    tailoring_display_state = fields.Selection(
        TAILORING_DISPLAY_STATE,
        string='حالة التفصيل',
        compute='_compute_material_shortage_status',
        readonly=True,
    )

    def _get_internal_substage_fields(self):
        return TAILORING_INTERNAL_SUBSTAGE_FIELDS

    def _material_shortage_rows(self):
        """Return audited receipt shortages, grouped by finished product."""
        self.ensure_one()
        production = self.production_order_id
        if not production:
            return []
        grouped = {}
        material_lines = production.material_line_ids.filtered(lambda line: (
            line.stage == 'tailoring'
            and line.product_id
            and line.warehouse_receipt_confirmed
            and float_compare(
                line.qty_needed or 0.0,
                line.warehouse_received_qty or 0.0,
                precision_digits=3,
            ) > 0
        ))
        for line in material_lines:
            source_line = line.production_line_id
            product_label = (
                production._get_production_line_text_label(source_line)
                if source_line else
                production.header_product_summary or production.display_name
            )
            uom = line.product_uom_id or line.product_id.uom_id
            key = (source_line.id or False, line.product_id.id, uom.id)
            bucket = grouped.setdefault(key, {
                'production_line_id': source_line.id or False,
                'product_label': product_label,
                'material_id': line.product_id.id,
                'material_label': line.product_id.display_name,
                'uom_id': uom.id,
                'uom_label': uom.display_name,
                'shortage_qty': 0.0,
            })
            bucket['shortage_qty'] += max(
                (line.qty_needed or 0.0)
                - (line.warehouse_received_qty or 0.0),
                0.0,
            )
        return list(grouped.values())

    @api.depends(
        'state',
        'production_order_id.material_line_ids.stage',
        'production_order_id.material_line_ids.product_id',
        'production_order_id.material_line_ids.product_uom_id',
        'production_order_id.material_line_ids.qty_needed',
        'production_order_id.material_line_ids.production_line_id',
        'production_order_id.material_line_ids.warehouse_receipt_confirmed',
        'production_order_id.material_line_ids.warehouse_received_qty',
    )
    def _compute_material_shortage_status(self):
        for rec in self:
            rows = rec._material_shortage_rows()
            rec.has_material_shortage = bool(rows)
            detail_template = _(
                'المنتج: %(product)s — الخامة: %(material)s — '
                'الكمية الناقصة: %(quantity)s %(uom)s'
            )
            rec.material_shortage_details = '\n'.join(
                detail_template % {
                    'product': row['product_label'],
                    'material': row['material_label'],
                    'quantity': rec.production_order_id._format_dimension_value(
                        row['shortage_qty']
                    ),
                    'uom': row['uom_label'],
                }
                for row in rows
            ) or False
            rec.tailoring_display_state = (
                'material_shortage'
                if rows and rec.state != 'pending'
                else rec.state
            )

    @api.depends(
        'substage_cutting_state',
        'substage_sewing_state',
        'substage_ironing_state',
        'state',
        'has_material_shortage',
    )
    def _compute_substage_tracking(self):
        for rec in self:
            definitions = rec._get_internal_substage_fields()
            completed = 0
            pending_labels = []
            active_labels = []
            raw_states = []
            for _code, label, field_name in definitions:
                field_state = rec[field_name]
                raw_states.append(field_state)
                if field_state == 'done':
                    completed += 1
                elif field_state == 'in_progress':
                    active_labels.append(label)
                elif field_state == 'pending':
                    pending_labels.append(label)

            if rec.state == 'done' and raw_states and all(
                state == 'pending' for state in raw_states
            ):
                completed = len(definitions)

            total = len(definitions)
            if rec.has_material_shortage and rec.state != 'pending':
                current = _('مواد ناقصة')
            elif completed == total and rec.state == 'in_progress':
                current = _('جاهز للجودة')
            elif rec.state == 'done':
                current = _('مكتمل')
            elif rec.state == 'quality_check':
                current = _('فحص الجودة')
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
            rec.substage_summary = _('%s/%s مراحل منتهية') % (
                completed,
                total,
            )
            rec.all_substages_done = bool(total and completed == total)

    def _get_internal_substage_action_fields(self):
        self.ensure_one()
        code = self.env.context.get('tailoring_internal_substage')
        definition = next(
            (
                item for item in self._get_internal_substage_fields()
                if item[0] == code
            ),
            False,
        )
        if not definition:
            raise UserError(_('مرحلة التفصيل الداخلية غير محددة.'))
        return definition

    def _advance_completed_substage(self, label, field_name):
        self.ensure_one()
        self.write({field_name: 'done'})
        message = _('✅ تم إنهاء مرحلة %s بشكل مستقل.') % label
        if self.all_substages_done:
            message += '<br/>' + _(
                'اكتملت كل مراحل التفصيل ويمكن الإرسال للجودة.'
            )
        self.message_post(body=message)

    def _start_internal_substages(self):
        self.ensure_one()
        pending_definitions = [
            definition for definition in self._get_internal_substage_fields()
            if self[definition[2]] == 'pending'
        ]
        if not pending_definitions:
            return []
        self.write({
            definition[2]: 'in_progress'
            for definition in pending_definitions
        })
        self.message_post(
            body=_('🧩 بدأت مراحل التفصيل معًا: %s') % (
                '، '.join(definition[1] for definition in pending_definitions)
            )
        )
        return [definition[2] for definition in pending_definitions]

    def _restart_substages_for_rework(self):
        self.ensure_one()
        vals = {
            field_name: 'in_progress'
            for _code, _label, field_name in self._get_internal_substage_fields()
        }
        self.write(vals)
        return list(vals)

    def _reset_substages_for_next_batch(self):
        self.ensure_one()
        definitions = self._get_internal_substage_fields()
        if not all(self[field_name] == 'done' for _code, _label, field_name in definitions):
            return
        self.write({
            field_name: 'pending'
            for _code, _label, field_name in definitions
        })

    def action_start(self):
        for rec in self:
            if rec.state == 'pending':
                rec._reset_substages_for_next_batch()
        result = super().action_start()
        for rec in self:
            if rec.state == 'in_progress':
                rec._start_internal_substages()
                if rec.has_material_shortage:
                    rec.message_post(body=(
                        _('⚠️ بدأت مرحلة التفصيل مع وجود مواد ناقصة:')
                        + '<br/>'
                        + (rec.material_shortage_details or '').replace(
                            '\n', '<br/>'
                        )
                    ))
        return result

    def action_complete_internal_substage(self):
        self._check_stage_operation_access()
        for rec in self:
            _code, label, field_name = rec._get_internal_substage_action_fields()
            if rec.state != 'in_progress' or rec[field_name] != 'in_progress':
                raise UserError(_('مرحلة %s ليست جارية حاليًا.') % label)
            rec._advance_completed_substage(label, field_name)
        return True

    def _complete_internal_substages_for_dashboard_finish(self):
        """Close the legacy detail steps behind the dashboard's one finish tap.

        The order-supervisor dashboard intentionally exposes one operational
        timer and one finish button for tailoring.  Its old form-level cutting,
        sewing and ironing buttons are not part of that workflow, but the
        quality gate still records those milestones for reporting.
        """
        for rec in self:
            if rec.state not in ('in_progress', 'quality_check'):
                raise UserError(_(
                    'أمر التفصيل ليس في حالة تسمح بإنهاء المرحلة.'
                ))
            unfinished = [
                definition
                for definition in rec._get_internal_substage_fields()
                if rec[definition[2]] != 'done'
            ]
            if not unfinished:
                continue
            rec.write({
                field_name: 'done'
                for _code, _label, field_name in unfinished
            })
            rec.message_post(body=_(
                '✅ تم إنهاء خطوات التفصيل الداخلية مع إنهاء المرحلة من لوحة التحكم: %s'
            ) % '، '.join(label for _code, label, _field_name in unfinished))
        return True

    def action_send_to_quality(self):
        for rec in self:
            if not rec.all_substages_done:
                raise UserError(_(
                    'لا يمكن إرسال أمر التفصيل للجودة قبل إنهاء كل مراحله الداخلية.'
                ))
        return super().action_send_to_quality()

    def action_approve_quality(self):
        for rec in self:
            if not rec.all_substages_done:
                raise UserError(_(
                    'لا يمكن اعتماد الجودة قبل إنهاء كل مراحل التفصيل الداخلية.'
                ))
        return super().action_approve_quality()

    def action_reject_quality(self):
        result = super().action_reject_quality()
        for rec in self:
            rec._restart_substages_for_rework()
            rec.message_post(
                body=_('🔁 تمت إعادة كل مراحل التفصيل لإعادة العمل.')
            )
        return result
