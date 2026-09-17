# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


INTERNAL_SUBSTAGE_STATE = [
    ('pending', 'في الانتظار'),
    ('in_progress', 'جاري التنفيذ'),
    ('done', '✅ منتهي'),
]

BASES_INTERNAL_SUBSTAGE_FIELDS = [
    ('preparation', 'تجهيز', 'substage_preparation_state'),
    ('foam', 'سفنجة', 'substage_foam_state'),
]


class FurnitureMrpBases(models.Model):
    """المرحلة 4: القواعد."""

    _name = 'furniture.mrp.bases'
    _description = 'مرحلة القواعد'
    _inherit = 'furniture.mrp.stage.mixin'

    worker_ids = fields.Many2many(
        'hr.employee', 'bases_employee_rel', 'bases_id', 'employee_id',
        string='عمال القواعد',
    )
    worker_time_log_ids = fields.One2many(
        'furniture.mrp.bases.worker.log', 'bases_id',
        string='سجل وقت العمال',
    )
    carpentry_order_id = fields.Many2one(
        'furniture.mrp.carpentry', string='أمر التجميع المرجعي', readonly=True,
    )
    bases_notes = fields.Text(string='ملاحظات القواعد')

    substage_preparation_state = fields.Selection(
        INTERNAL_SUBSTAGE_STATE,
        string='مرحلة التجهيز',
        default='pending',
        tracking=True,
        copy=False,
    )
    substage_foam_state = fields.Selection(
        INTERNAL_SUBSTAGE_STATE,
        string='مرحلة السفنجة',
        default='pending',
        tracking=True,
        copy=False,
    )
    current_substage = fields.Char(
        string='المرحلة الحالية داخل القواعد',
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
        string='تم إنهاء كل مراحل القواعد الداخلية',
        compute='_compute_substage_tracking',
    )

    def _get_internal_substage_fields(self):
        return BASES_INTERNAL_SUBSTAGE_FIELDS

    @api.depends(
        'substage_preparation_state',
        'substage_foam_state',
        'state',
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

            # Keep already-finished legacy orders coherent after adding the new
            # fields without rewriting their historical rows.
            if rec.state == 'done' and raw_states and all(
                state == 'pending' for state in raw_states
            ):
                completed = len(definitions)

            total = len(definitions)
            if completed == total and rec.state == 'in_progress':
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
        code = self.env.context.get('bases_internal_substage')
        definition = next(
            (
                item for item in self._get_internal_substage_fields()
                if item[0] == code
            ),
            False,
        )
        if not definition:
            raise UserError(_('مرحلة القواعد الداخلية غير محددة.'))
        return definition

    def _advance_completed_substage(self, label, field_name):
        self.ensure_one()
        self.write({field_name: 'done'})
        message = _('✅ تم إنهاء مرحلة %s بشكل مستقل.') % label
        if self.all_substages_done:
            message += '<br/>' + _(
                'اكتملت كل مراحل القواعد ويمكن الإرسال للجودة.'
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
            body=_('🧩 بدأت مراحل القواعد معًا: %s') % (
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
        return result

    def action_complete_internal_substage(self):
        self._check_stage_operation_access()
        for rec in self:
            _code, label, field_name = rec._get_internal_substage_action_fields()
            if rec.state != 'in_progress' or rec[field_name] != 'in_progress':
                raise UserError(_('مرحلة %s ليست جارية حاليًا.') % label)
            rec._advance_completed_substage(label, field_name)
        return True

    def action_send_to_quality(self):
        for rec in self:
            if not rec.all_substages_done:
                raise UserError(_(
                    'لا يمكن إرسال أمر القواعد للجودة قبل إنهاء كل مراحله الداخلية.'
                ))
        return super().action_send_to_quality()

    def action_approve_quality(self):
        for rec in self:
            if not rec.all_substages_done:
                raise UserError(_(
                    'لا يمكن اعتماد الجودة قبل إنهاء كل مراحل القواعد الداخلية.'
                ))
        return super().action_approve_quality()

    def action_reject_quality(self):
        result = super().action_reject_quality()
        for rec in self:
            rec._restart_substages_for_rework()
            rec.message_post(
                body=_('🔁 تمت إعادة كل مراحل القواعد لإعادة العمل.')
            )
        return result
