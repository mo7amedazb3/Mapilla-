# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import (
    FURNITURE_LEGACY_STAGE_SELECTION,
    FURNITURE_STAGE_SELECTION,
)


class FurnitureMrpStoreRequest(models.Model):
    _name = 'furniture.mrp.store.request'
    _description = 'طلب اعتماد صرف خامات مرحلة إنتاج'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'requested_at desc, id desc'

    name = fields.Char(string='رقم الطلب', required=True, copy=False, readonly=True, default='جديد')
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر التشغيل الأسبوعي', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    stage_code = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION,
        string='المرحلة', required=True, index=True, tracking=True,
    )
    stage_order_model = fields.Char(string='موديل أمر المرحلة', required=True, readonly=True)
    stage_order_res_id = fields.Integer(string='رقم أمر المرحلة', required=True, readonly=True)
    stage_order_name = fields.Char(string='أمر المرحلة', readonly=True)
    request_kind = fields.Selection(
        [
            ('first_stage', 'بدء أول مرحلة'),
            ('stage_hall', 'بدء منتجات صالة المرحلة'),
            ('direct', 'بدء مباشر للمرحلة'),
        ],
        string='نوع الطلب', required=True, readonly=True,
    )
    start_mode = fields.Selection(
        [
            ('first_stage_selected', 'أول مرحلة - المختار'),
            ('current_only', 'منتجات الأمر الحالي'),
            ('with_existing', 'الحالي والشغل القديم'),
            ('selected_work', 'المختار من الصالة'),
            ('direct', 'بدء مباشر'),
        ],
        string='طريقة البدء', required=True, readonly=True,
    )
    state = fields.Selection(
        [
            ('pending', 'بانتظار أمين المخزن'),
            ('approved', 'معتمد من المخزن'),
            ('started', 'تم بدء المرحلة'),
            ('rejected', 'مرفوض'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة', default='pending', required=True, tracking=True, index=True,
    )
    requested_by_id = fields.Many2one(
        'res.users', string='طالب الصرف', required=True, readonly=True,
        default=lambda self: self.env.user,
    )
    assigned_to_id = fields.Many2one(
        'res.users', string='أمين المخزن', required=True, readonly=True, tracking=True,
    )
    can_warehouse_decide = fields.Boolean(
        string='يمكنه اتخاذ قرار المخزن',
        compute='_compute_can_warehouse_decide',
    )
    approved_by_id = fields.Many2one('res.users', string='اعتمد بواسطة', readonly=True)
    requested_at = fields.Datetime(string='وقت الطلب', required=True, default=fields.Datetime.now, readonly=True)
    approved_at = fields.Datetime(string='وقت الاعتماد/الرفض', readonly=True)
    handover_location_id = fields.Many2one(
        'stock.location', string='عهدة التسليم', readonly=True, copy=False,
    )
    receipt_state = fields.Selection(
        [
            ('not_issued', 'لم يصرف بعد'),
            ('waiting', 'بانتظار استلام الإنتاج'),
            ('full', 'مستلم بالكامل'),
            ('partial', 'استلام جزئي ويوجد فرق'),
            ('legacy', 'صرف قديم مباشر'),
        ],
        string='حالة استلام الإنتاج', default='not_issued', required=True,
        readonly=True, copy=False, tracking=True,
    )
    receipt_confirmed = fields.Boolean(
        string='تم تأكيد استلام الإنتاج', readonly=True, copy=False,
        tracking=True,
    )
    received_by_id = fields.Many2one(
        'res.users', string='استلم بواسطة', readonly=True, copy=False,
    )
    received_at = fields.Datetime(
        string='وقت استلام الإنتاج', readonly=True, copy=False,
    )
    receipt_note = fields.Text(
        string='ملاحظة الاستلام أو سبب الفرق', readonly=True, copy=False,
    )
    started_by_id = fields.Many2one('res.users', string='بدأ المرحلة بواسطة', readonly=True)
    started_at = fields.Datetime(string='وقت بدء المرحلة', readonly=True)
    rejection_reason = fields.Text(string='سبب الرفض')
    requested_product_summary = fields.Text(string='المنتجات المطلوب بدءها', readonly=True)
    source_location_id = fields.Many2one('stock.location', string='المخزن المصدر', readonly=True)
    destination_location_id = fields.Many2one('stock.location', string='صالة المرحلة', readonly=True)
    material_line_ids = fields.One2many(
        'furniture.mrp.store.request.line', 'request_id',
        string='الخامات المطلوب صرفها', copy=False, readonly=True,
    )
    payload_json = fields.Json(string='بيانات تنفيذ بدء المرحلة', copy=False, readonly=True)
    company_id = fields.Many2one(
        'res.company', related='production_id.company_id', store=True, readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = self.env['ir.sequence'].next_by_code('furniture.mrp.store.request') or 'جديد'
        return super().create(vals_list)

    def action_print_warehouse_receipt(self):
        """Print the complete single-stage warehouse voucher on A4."""
        self.ensure_one()
        return self.env.ref(
            'furniture_mrp.action_report_furniture_mrp_store_request'
        ).report_action(self, config=False)

    def write(self, vals):
        if not self.env.is_superuser():
            if set(vals) - {'rejection_reason'}:
                raise AccessError(_(
                    'لا يمكن تعديل بيانات أو حالة إذن المخزن مباشرة. '
                    'استخدم أزرار الاعتماد أو الرفض المخصصة.'
                ))
            self._check_can_approve()
            if any(request.state != 'pending' for request in self):
                raise AccessError(_('يمكن كتابة سبب الرفض قبل اتخاذ القرار فقط.'))
        return super().write(vals)

    @api.depends('assigned_to_id')
    @api.depends_context('uid')
    def _compute_can_warehouse_decide(self):
        is_storekeeper = self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_storekeeper'
        )
        for request in self:
            request.can_warehouse_decide = bool(
                self.env.is_superuser()
                or (
                    is_storekeeper
                    and request.assigned_to_id == self.env.user
                )
            )

    @api.model
    def _stage_destination_location(self, production, stage_code):
        field_by_stage = {
            'priming': 'location_priming_wip_id',
            'painting': 'location_painting_wip_id',
            'carpentry': 'location_carpentry_wip_id',
            'bases': 'location_bases_wip_id',
            'finishing': 'location_finishing_wip_id',
            'tailoring': 'location_tailoring_wip_id',
            'sewing': 'location_sewing_wip_id',
            'upholstery': 'location_upholstery_wip_id',
            'packaging': 'location_packaging_id',
        }
        field_name = field_by_stage.get(stage_code)
        return production[field_name] if production and field_name else self.env['stock.location']

    @api.model
    def _material_handover_location(self):
        location = self.env.ref(
            'furniture_mrp.location_material_handover',
            raise_if_not_found=False,
        )
        if not location:
            raise UserError(_(
                'موقع عهدة تسليم الخامات غير موجود. حدّث موديول MRP II أولًا.'
            ))
        return location

    @api.model
    def _get_storekeeper_user(self, company=False):
        group = self.env.ref('furniture_mrp.group_furniture_mrp_storekeeper', raise_if_not_found=False)
        users = group.sudo().users.filtered(lambda user: user.active and not user.share) if group else self.env['res.users']
        if company:
            company_users = users.filtered(lambda user: company in user.company_ids)
            users = company_users or users
        storekeeper = users.sorted(lambda user: user.id)[:1]
        if not storekeeper:
            raise UserError(_('لا يوجد مستخدم نشط بدور أمين المخزن. أضف أمين المخزن أولًا ثم أعد المحاولة.'))
        return storekeeper

    @api.model
    def _display_request_notification(self, request, existing=False):
        if request.state == 'approved':
            title = _('الإذن معتمد')
            message = (
                _('الإذن %s معتمد والخامات في عهدة التسليم. أكد استلام الإنتاج أولًا.')
                % request.name
                if not request.receipt_confirmed and request.material_line_ids
                else _('الإذن %s معتمد وجاهز لبدء المنتجات المختارة.') % request.name
            )
            notification_type = 'success'
        else:
            title = _('بانتظار أمين المخزن')
            message = (
                _('طلب الإذن %s موجود بالفعل وبانتظار اعتماد أمين المخزن.') % request.name
                if existing else
                _('تم إرسال طلب الإذن %s لأمين المخزن. لن تتحرك الخامات ولن تبدأ المرحلة قبل الاعتماد.') % request.name
            )
            notification_type = 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notification_type,
                'sticky': False,
            },
        }

    @api.model
    def _send_bus_notification(
        self, users, title, message, notification_type='info', sticky=False,
        action_model=False, action_res_id=False, action_name=False,
        refresh_model=False, refresh_res_id=False, play_sound=False,
        extra_payload=None,
    ):
        for user in users.filtered(lambda item: item.active and item.partner_id):
            payload = {
                'title': title,
                'message': message,
                'type': notification_type,
                'sticky': sticky,
                'action_model': action_model,
                'action_res_id': action_res_id,
                'action_name': action_name or _('فتح'),
                'refresh_model': refresh_model,
                'refresh_res_id': refresh_res_id,
                'play_sound': bool(play_sound),
            }
            payload.update(extra_payload or {})
            self.env['bus.bus'].sudo()._sendone(
                user.partner_id,
                'furniture_store_notification',
                payload,
            )

    @api.model
    def _aggregate_material_commands(self, production, commands, buckets):
        for command in commands or []:
            if not isinstance(command, (list, tuple)) or len(command) < 3 or command[0] != 0:
                continue
            vals = command[2] or {}
            product = self.env['product.product'].browse(vals.get('product_id')).exists()
            if not product:
                continue
            uom = self.env['uom.uom'].browse(vals.get('product_uom_id')).exists() or product.uom_id
            qty = vals.get('qty_needed') or 0.0
            if float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            key = (product.id, uom.id)
            bucket = buckets.setdefault(key, {
                'product_id': product.id,
                'product_uom_id': uom.id,
                'requested_qty': 0.0,
                'available_qty': 0.0,
            })
            bucket['requested_qty'] += qty
        return buckets

    @api.model
    def _finalize_material_buckets(self, production, buckets):
        source_location = production.location_src_id or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        lines = []
        for bucket in buckets.values():
            product = self.env['product.product'].browse(bucket['product_id'])
            available_qty = production._stage_location_product_qty(source_location, product, production.company_id) if source_location else 0.0
            vals = dict(bucket)
            vals['available_qty'] = available_qty
            lines.append((0, 0, vals))
        return lines

    @api.model
    def _commands_for_product_line(self, production, stage_code, production_line, quantity, product=False):
        return production._prepare_material_lines_for_product_stage(
            product or production_line.product_id,
            quantity,
            stage_code,
            production_line=production_line or False,
        )

    @api.model
    def _commands_for_wizard_line(self, production, stage_code, wizard_line, quantity, product=False):
        overrides = (
            wizard_line.material_override_json
            if 'material_override_json' in wizard_line._fields else False
        )
        if overrides:
            return production._prepare_material_override_commands(
                stage_code,
                overrides,
                production_line=(
                    wizard_line.production_line_id
                    if 'production_line_id' in wizard_line._fields
                    else wizard_line.source_production_line_id
                ),
            )
        production_line = (
            wizard_line.production_line_id
            if 'production_line_id' in wizard_line._fields
            else wizard_line.source_production_line_id
        )
        return self._commands_for_product_line(
            production, stage_code, production_line, quantity, product=product,
        )

    @api.model
    def _create_pending_request(self, vals, material_buckets):
        production = vals['production_id']
        stage_order_model = vals['stage_order_model']
        stage_order_res_id = vals['stage_order_res_id']
        existing = self.sudo().search([
            ('production_id', '=', production.id),
            ('stage_order_model', '=', stage_order_model),
            ('stage_order_res_id', '=', stage_order_res_id),
            ('state', 'in', ('pending', 'approved')),
        ], limit=1)
        if existing:
            return self._display_request_notification(existing, existing=True)

        storekeeper = self._get_storekeeper_user(production.company_id)
        source_location = production.location_src_id or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        vals.update({
            'production_id': production.id,
            'requested_by_id': self.env.user.id,
            'assigned_to_id': storekeeper.id,
            'source_location_id': source_location.id if source_location else False,
            'handover_location_id': self._material_handover_location().id,
            'destination_location_id': self._stage_destination_location(production, vals['stage_code']).id,
            'material_line_ids': self._finalize_material_buckets(production, material_buckets),
        })
        request = self.sudo().create(vals)
        stage_label = dict(FURNITURE_STAGE_SELECTION).get(request.stage_code, request.stage_code)
        request.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=storekeeper.id,
            summary=_('مراجعة إذن مخزن %s') % stage_label,
            note=_('راجع الإذن %s لأمر الإنتاج %s. الاعتماد سيسلّم الخامات إلى عهدة انتظار استلام الإنتاج، ولن تدخل صالة المرحلة مباشرة.') % (
                request.name, production.name,
            ),
        )
        self._send_bus_notification(
            storekeeper,
            _('إذن مخزن جديد'),
            _('الطلب %s لأمر %s - مرحلة %s بانتظار اعتمادك.') % (
                request.name, production.name, stage_label,
            ),
            notification_type='warning',
            action_model=request._name,
            action_res_id=request.id,
            action_name=_('فتح الإذن'),
            play_sound=True,
        )
        production.sudo().message_post(body=_('⏳ تم إنشاء إذن المخزن %s لمرحلة %s وبانتظار أمين المخزن.') % (
            request.name, stage_label,
        ))
        return self._display_request_notification(request)

    @api.model
    def _request_first_stage_start(self, wizard):
        if self.env.context.get('furniture_storekeeper_approval_bypass'):
            return False
        selected_lines = wizard.line_ids.filtered(
            lambda line: line.selected and line.production_line_id and line.qty_to_start > 0
        )
        if not selected_lines:
            return False
        buckets = {}
        payload_lines = []
        summary = []
        for line in selected_lines:
            qty = line.qty_to_start
            self._aggregate_material_commands(
                wizard.production_id,
                self._commands_for_wizard_line(
                    wizard.production_id, wizard.stage_code, line, qty,
                ),
                buckets,
            )
            payload_lines.extend(line._technical_allocation_payloads())
            summary.append('%s × %s' % (
                line.product_display_label or line.production_line_id.display_name,
                qty,
            ))
        stage_order = wizard._get_stage_order()
        return self._create_pending_request({
            'production_id': wizard.production_id,
            'stage_code': wizard.stage_code,
            'stage_order_model': wizard.stage_order_model,
            'stage_order_res_id': wizard.stage_order_res_id,
            'stage_order_name': stage_order.name if stage_order else False,
            'request_kind': 'first_stage',
            'start_mode': 'first_stage_selected',
            'requested_product_summary': '\n'.join(summary),
            'payload_json': {'lines': payload_lines},
        }, buckets)

    @api.model
    def _request_stage_hall_start(self, wizard, mode):
        if self.env.context.get('furniture_storekeeper_approval_bypass'):
            return False
        payload_lines = []
        buckets = {}
        summary = []
        for line in wizard.line_ids:
            qty = line.qty_to_start or 0.0
            technical_payloads = line._technical_allocation_payloads()
            payload_lines.extend(technical_payloads)
            source_is_current = bool(
                line.source_production_id == wizard.production_id
                or (
                    line.source_production_line_id
                    and line.source_production_line_id.production_id == wizard.production_id
                )
            )
            if mode == 'current_only':
                relevant = source_is_current
            elif mode == 'with_existing':
                relevant = source_is_current or line.selected
            else:
                relevant = line.selected
            if not relevant or float_compare(qty, 0.0, precision_digits=3) <= 0:
                continue
            for technical_payload in technical_payloads:
                technical_qty = technical_payload.get('qty_to_start') or 0.0
                if float_compare(technical_qty, 0.0, precision_digits=3) <= 0:
                    continue
                source_line_id = technical_payload.get('source_production_line_id')
                source_line = self.env['furniture.mrp.production.line'].browse(
                    source_line_id
                ).exists()
                if source_line_id and not source_line:
                    raise UserError(_(
                        'اتغيرت سطور أمر الإنتاج بعد فتح شاشة الصالة. اقفل الشاشة وافتحها من جديد.'
                    ))
                product = self.env['product.product'].browse(
                    technical_payload.get('product_id')
                ).exists() or line.product_id
                overrides = technical_payload.get('material_override_json') or False
                commands = (
                    wizard.production_id._prepare_material_override_commands(
                        wizard.stage_code,
                        overrides,
                        production_line=source_line,
                    )
                    if overrides else self._commands_for_product_line(
                        wizard.production_id,
                        wizard.stage_code,
                        source_line,
                        technical_qty,
                        product=product,
                    )
                )
                self._aggregate_material_commands(
                    wizard.production_id,
                    commands,
                    buckets,
                )
            summary.append('%s × %s' % (line.product_display_label or line.product_id.display_name, qty))
        stage_order = wizard._get_stage_order()
        return self._create_pending_request({
            'production_id': wizard.production_id,
            'stage_code': wizard.stage_code,
            'stage_order_model': wizard.stage_order_model,
            'stage_order_res_id': wizard.stage_order_res_id,
            'stage_order_name': stage_order.name if stage_order else False,
            'request_kind': 'stage_hall',
            'start_mode': mode,
            'requested_product_summary': '\n'.join(summary),
            'payload_json': {'lines': payload_lines},
        }, buckets)

    @api.model
    def _request_direct_stage_start(self, stage_order):
        if self.env.context.get('furniture_storekeeper_approval_bypass'):
            return False
        production = stage_order.production_order_id
        if not production:
            return False
        stage_code = production._stage_model_to_code(stage_order._name)
        active_lines = stage_order._get_stage_line_ids_data('active_production_line_ids_data')
        if not active_lines:
            active_lines = (
                production._get_material_only_stage_start_line_candidates(
                    stage_code,
                    stage_order=stage_order,
                )
                if production._is_material_only_stage(stage_code) else
                production._get_first_stage_start_line_candidates(stage_code)
            )
        buckets = {}
        summary = []
        for production_line in active_lines:
            self._aggregate_material_commands(
                production,
                self._commands_for_product_line(
                    production, stage_code, production_line, production_line.product_qty,
                ),
                buckets,
            )
        for group in production._group_production_lines_by_display_family(active_lines):
            representative = group['representative']
            summary.append('%s × %s' % (
                production._get_production_line_display_name(representative),
                group['quantity'],
            ))
        return self._create_pending_request({
            'production_id': production,
            'stage_code': stage_code,
            'stage_order_model': stage_order._name,
            'stage_order_res_id': stage_order.id,
            'stage_order_name': stage_order.name,
            'request_kind': 'direct',
            'start_mode': 'direct',
            'requested_product_summary': '\n'.join(summary),
            'payload_json': {
                'production_line_ids': active_lines.ids,
            },
        }, buckets)

    def _check_can_approve(self, assigned_to=False):
        if self.env.is_superuser():
            return True
        if not self.env.user.has_group('furniture_mrp.group_furniture_mrp_storekeeper'):
            raise AccessError(_(
                'اعتماد أو رفض أو إرجاع صرف الخامات متاح لأمين المخزن المعيّن على الإذن فقط.'
            ))
        assigned_users = assigned_to or self.mapped('assigned_to_id')
        if (
            len(assigned_users) != 1
            or assigned_users != self.env.user
        ):
            raise AccessError(_(
                'هذا الإذن مخصّص لأمين مخزن آخر. لا يمكن اعتماده أو رفضه من حسابك.'
            ))
        return True

    def _lock_for_warehouse_decision(self):
        self.ensure_one()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_store_request WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset(['state', 'assigned_to_id'])

    def _get_stage_order(self):
        self.ensure_one()
        if not self.stage_order_model or not self.stage_order_res_id:
            return False
        return self.env[self.stage_order_model].browse(self.stage_order_res_id).exists()

    def _check_can_open_production_navigation(self):
        if self.env.is_superuser():
            return
        storekeeper_only = (
            self.env.user.has_group('furniture_mrp.group_furniture_mrp_storekeeper')
            and not self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')
            and not self.env.user.has_group('furniture_mrp.group_furniture_mrp_supervisor')
        )
        if storekeeper_only:
            raise AccessError(_(
                'حساب أمين المخزن مخصص لمراجعة واعتماد الأذونات فقط، ولا يفتح أوامر الإنتاج.'
            ))

    def action_open_production_order(self):
        self.ensure_one()
        self._check_can_open_production_navigation()
        if not self.production_id:
            raise UserError(_('أمر الإنتاج المرتبط بهذا الإذن لم يعد موجودًا.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.production_id.name,
            'res_model': self.production_id._name,
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_mrp_dashboard(self):
        self.ensure_one()
        self._check_can_open_production_navigation()
        action = self.env.ref('furniture_mrp.action_furniture_mrp_dashboard').read()[0]
        action['target'] = 'current'
        return action

    def _check_can_receive(self):
        """Only production leadership may accept the physical handover."""
        self.ensure_one()
        if self.env.is_superuser():
            return True
        production = self.production_id
        if (
            not production
            or production.company_id.id not in self.env.companies.ids
        ):
            raise AccessError(_(
                'إذن الخامات لا يتبع إحدى الشركات المفعلة لحسابك.'
            ))
        if self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        ):
            return True
        if self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor'
        ):
            production._stage_dashboard_check_stage_access(self.stage_code)
            return True
        raise AccessError(_(
            'تأكيد استلام الخامات متاح لمدير الإنتاج أو مشرف المرحلة فقط.'
        ))

    def _allows_material_shortage_start(self):
        self.ensure_one()
        return self.stage_code == 'tailoring'

    def _material_issue_available_qty(self, source_location, product):
        self.ensure_one()
        return self.production_id._stage_location_product_qty(
            source_location,
            product,
            self.production_id.company_id,
        )

    def _issue_materials_to_handover(self):
        """Issue request quantities, allowing a partial tailoring handover."""
        self.ensure_one()
        source_location = (
            self.source_location_id
            or self.production_id.location_src_id
            or self.env.ref('stock.stock_location_stock')
        )
        handover_location = (
            self.handover_location_id or self._material_handover_location()
        )
        allow_shortage = self._allows_material_shortage_start()
        remaining_available_by_product = {}
        if allow_shortage:
            for product in self.material_line_ids.mapped('product_id'):
                remaining_available_by_product[product.id] = max(
                    self._material_issue_available_qty(
                        source_location, product,
                    ),
                    0.0,
                )
        created_by_line = defaultdict(lambda: self.env['stock.move'])
        specs = []
        for line in self.material_line_ids:
            valid_moves = line._valid_issue_moves(handover_location)
            already_issued = sum(
                line._move_qty_in_line_uom(move) for move in valid_moves
            )
            missing_qty = max(line.requested_qty - already_issued, 0.0)
            if float_compare(missing_qty, 0.0, precision_digits=3) <= 0:
                continue
            requested_product_qty = line.product_uom_id._compute_quantity(
                missing_qty, line.product_id.uom_id, round=False,
            )
            product_qty = requested_product_qty
            if allow_shortage:
                available_product_qty = remaining_available_by_product.get(
                    line.product_id.id, 0.0,
                )
                product_qty = min(
                    requested_product_qty,
                    available_product_qty,
                )
                remaining_available_by_product[line.product_id.id] = max(
                    available_product_qty - product_qty,
                    0.0,
                )
            if float_compare(
                product_qty, 0.0, precision_digits=3,
            ) <= 0:
                continue
            specs.append({
                'source_location': source_location,
                'dest_location': handover_location,
                'label': _('تسليم مخزني بانتظار استلام الإنتاج - %s')
                         % dict(FURNITURE_STAGE_SELECTION).get(
                             self.stage_code, self.stage_code,
                ),
                'product': line.product_id,
                'quantity': product_qty,
                'uom': line.product_id.uom_id,
                'request_line': line,
            })
        for spec, move in self.production_id._create_internal_moves_batch(specs):
            created_by_line[spec['request_line'].id] |= move

        for line in self.material_line_ids:
            valid_moves = (
                line._valid_issue_moves(handover_location)
                | created_by_line[line.id]
            )
            issued_qty = sum(
                line._move_qty_in_line_uom(move) for move in valid_moves
            )
            if not allow_shortage and float_compare(
                issued_qty, line.requested_qty, precision_digits=3,
            ) < 0:
                raise UserError(_(
                    'حركات التسليم المخزني للخامة %s لا تغطي الكمية المطلوبة.'
                ) % line.product_id.display_name)
            line.sudo().write({
                'issued_qty': issued_qty,
                'received_qty': 0.0,
                'receipt_difference_qty': 0.0,
                'issue_move_ids': [(6, 0, valid_moves.ids)],
                'receipt_move_ids': [(5, 0, 0)],
            })
        self.sudo().write({'handover_location_id': handover_location.id})
        return True

    def action_open_receipt_wizard(self):
        self.ensure_one()
        self._check_can_receive()
        if self.state != 'approved':
            raise UserError(_(
                'يمكن استلام الخامات بعد اعتماد أمين المخزن وقبل بدء المرحلة فقط.'
            ))
        if self.receipt_confirmed:
            raise UserError(_('تم تأكيد استلام خامات هذا الإذن بالفعل.'))
        if not self.material_line_ids:
            raise UserError(_('هذا الإذن لا يحتوي على خامات تحتاج إلى استلام.'))
        if not self.material_line_ids.mapped('issue_move_ids'):
            raise UserError(_(
                'هذا إذن قديم لم تُسجل له حركات عهدة تسليم. ابدأه بالطريقة القديمة.'
            ))
        wizard = self.env['furniture.mrp.store.receipt.wizard'].create({
            'request_id': self.id,
            'line_ids': [
                (0, 0, {
                    'request_line_id': line.id,
                    'issued_qty': line.issued_qty,
                    'received_qty': line.issued_qty,
                })
                for line in self.material_line_ids
            ],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_store_receipt_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('استلام خامات %s من المخزن') % dict(
                FURNITURE_STAGE_SELECTION,
            ).get(self.stage_code, self.stage_code),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
        }

    def _confirm_production_receipt(self, received_by_line, note=False):
        """Record the physical handover without moving it into the stage hall.

        Warehouse approval already moved the issued quantity from Stock to the
        neutral handover location.  The production manager's confirmation is
        an accountability record only; the exact accepted quantity is staged
        later, atomically with the real stage start.
        """
        self.ensure_one()
        self._check_can_receive()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_store_request WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset([
            'state', 'receipt_confirmed', 'receipt_state',
        ])
        if self.state != 'approved':
            raise UserError(_(
                'لا يمكن تأكيد الاستلام بعد بدء المرحلة أو قبل اعتماد الإذن.'
            ))
        if self.receipt_confirmed:
            raise UserError(_('تم تأكيد استلام خامات هذا الإذن بالفعل.'))

        received_by_line = {
            int(line_id): float(quantity or 0.0)
            for line_id, quantity in (received_by_line or {}).items()
        }
        expected_ids = set(self.material_line_ids.ids)
        if set(received_by_line) != expected_ids:
            raise UserError(_(
                'سطور شاشة الاستلام لم تعد مطابقة لخامات إذن المخزن. '
                'أغلق الشاشة وافتحها من جديد.'
            ))

        handover_location = (
            self.handover_location_id or self._material_handover_location()
        )
        any_positive = False
        existing_receipt_moves_by_line = {}
        for line in self.material_line_ids:
            received_qty = received_by_line[line.id]
            if float_compare(received_qty, 0.0, precision_digits=3) < 0:
                raise UserError(_(
                    'الكمية المستلمة للخامة %s لا يمكن أن تكون سالبة.'
                ) % line.product_id.display_name)
            if float_compare(
                received_qty, line.issued_qty, precision_digits=3,
            ) > 0:
                raise UserError(_(
                    'الكمية المستلمة للخامة %(product)s أكبر من المصروف '
                    '(%(issued)s).'
                ) % {
                    'product': line.product_id.display_name,
                    'issued': self.production_id._format_dimension_value(
                        line.issued_qty,
                    ),
                })
            issue_moves = line._valid_issue_moves(handover_location)
            issue_qty = sum(
                line._move_qty_in_line_uom(move) for move in issue_moves
            )
            if float_compare(
                issue_qty, line.issued_qty, precision_digits=3,
            ) < 0:
                raise UserError(_(
                    'حركات التسليم المخزني للخامة %s غير مكتملة.'
                ) % line.product_id.display_name)
            existing_receipt_moves = line._valid_receipt_moves(
                self.destination_location_id,
            ).filtered(lambda move: move.location_id == handover_location)
            existing_receipt_moves_by_line[line.id] = existing_receipt_moves
            existing_receipt_qty = sum(
                line._move_qty_in_line_uom(move)
                for move in existing_receipt_moves
            )
            if float_compare(
                existing_receipt_qty, received_qty, precision_digits=3,
            ) > 0:
                raise UserError(_(
                    'يوجد استلام مخزني قديم منفذ بالفعل للخامة %(product)s '
                    'بكمية %(quantity)s، لذلك لا يمكن تسجيل كمية أقل منه.'
                ) % {
                    'product': line.product_id.display_name,
                    'quantity': self.production_id._format_dimension_value(
                        existing_receipt_qty,
                    ),
                })
            if float_compare(received_qty, 0.0, precision_digits=3) > 0:
                any_positive = True
        if self.material_line_ids and not any_positive:
            raise UserError(_(
                'سجل كمية مستلمة فعلية في خامة واحدة على الأقل. '
                'لو لم تستلم شيئًا اترك الإذن بانتظار الاستلام.'
            ))

        for line in self.material_line_ids:
            received_qty = received_by_line[line.id]
            difference_qty = max(line.issued_qty - received_qty, 0.0)
            line.sudo().write({
                'received_qty': received_qty,
                'receipt_difference_qty': difference_qty,
                # Preserve a valid legacy handover -> hall move instead of
                # forgetting it and creating the same stock transfer again at
                # stage start. New requests normally keep this relation empty.
                'receipt_move_ids': [(6, 0, existing_receipt_moves_by_line[
                    line.id
                ].ids)],
            })

        has_difference = any(
            float_compare(
                line.receipt_difference_qty, 0.0, precision_digits=3,
            ) > 0
            for line in self.material_line_ids
        )
        has_issue_shortage = bool(
            self.stage_code == 'tailoring'
            and any(
                float_compare(
                    line.issued_qty,
                    line.requested_qty,
                    precision_digits=3,
                ) < 0
                for line in self.material_line_ids
            )
        )
        receipt_state = (
            'partial' if has_difference or has_issue_shortage else 'full'
        )
        self.sudo().write({
            'receipt_state': receipt_state,
            'receipt_confirmed': True,
            'received_by_id': self.env.user.id,
            'received_at': fields.Datetime.now(),
            'receipt_note': note or False,
        })

        stage_label = dict(FURNITURE_STAGE_SELECTION).get(
            self.stage_code, self.stage_code,
        )
        details = [
            _('%(product)s: المصروف %(issued)s، المستلم %(received)s، الفرق %(difference)s')
            % {
                'product': line.product_id.display_name,
                'issued': self.production_id._format_dimension_value(
                    line.issued_qty,
                ),
                'received': self.production_id._format_dimension_value(
                    line.received_qty,
                ),
                'difference': self.production_id._format_dimension_value(
                    line.receipt_difference_qty,
                ),
            }
            for line in self.material_line_ids
        ]
        message = _(
            '📥 أكد %(user)s استلام خامات مرحلة %(stage)s من المخزن.'
        ) % {
            'user': self.env.user.display_name,
            'stage': stage_label,
        }
        if details:
            message += '<br/>' + '<br/>'.join(details)
        if note:
            message += '<br/>' + _('ملاحظة الاستلام: %s') % note
        self.sudo().message_post(body=message)
        self.production_id.sudo().message_post(body=message)
        self._send_bus_notification(
            self.assigned_to_id.filtered('active'),
            _('تم تسجيل استلام الإنتاج'),
            message,
            notification_type=(
                'warning'
                if has_difference or has_issue_shortage else 'success'
            ),
            action_model=self._name,
            action_res_id=self.id,
            action_name=_('فتح الإذن'),
            refresh_model=self._name,
            refresh_res_id=self.id,
            play_sound=has_difference or has_issue_shortage,
        )
        return receipt_state

    def _apply_receipt_to_material_lines(self, material_lines):
        """Persist actual accepted quantities on the technical recipe rows.

        The stage-start code calls this method through the exact request id in
        context.  This prevents the regular material mover from silently
        completing a warehouse shortage from Stock.
        """
        self.ensure_one()
        material_lines = material_lines.exists()
        if not material_lines:
            return material_lines
        if not self.receipt_confirmed:
            # Requests approved before this workflow have no handover moves and
            # keep their historical direct-start behavior.
            if not self.material_line_ids.mapped('issue_move_ids'):
                return material_lines
            raise UserError(_(
                'أكد استلام خامات الإذن %s من المخزن أولًا.'
            ) % self.name)
        invalid_lines = material_lines.filtered(lambda line: (
            line.production_id != self.production_id
            or line.stage != self.stage_code
        ))
        if invalid_lines:
            raise UserError(_(
                'سطور خامات بدء المرحلة لا تطابق إذن المخزن %s.'
            ) % self.name)

        request_products = self.material_line_ids.mapped('product_id')
        technical_products = material_lines.mapped('product_id')
        if set(request_products.ids) != set(technical_products.ids):
            raise UserError(_(
                'خامات المرحلة تغيرت بعد اعتماد إذن المخزن %s. '
                'أنشئ إذنًا جديدًا بعد مراجعة التعديل.'
            ) % self.name)

        handover_location = (
            self.handover_location_id or self._material_handover_location()
        )
        for product in request_products:
            request_lines = self.material_line_ids.filtered(
                lambda line: line.product_id == product
            )
            product_material_lines = material_lines.filtered(
                lambda line: line.product_id == product
            ).sorted('id')
            requested_product_qty = sum(
                line.product_uom_id._compute_quantity(
                    line.requested_qty, product.uom_id, round=False,
                )
                for line in request_lines
            )
            required_product_qty = sum(
                line.product_uom_id._compute_quantity(
                    line.qty_needed, product.uom_id, round=False,
                )
                for line in product_material_lines
            )
            if float_compare(
                required_product_qty, requested_product_qty,
                precision_digits=3,
            ) != 0:
                raise UserError(_(
                    'كمية الخامة %(product)s تغيرت بعد اعتماد إذن المخزن '
                    '%(request)s.'
                ) % {
                    'product': product.display_name,
                    'request': self.name,
                })
            received_product_qty = sum(
                line.product_uom_id._compute_quantity(
                    line.received_qty, product.uom_id, round=False,
                )
                for line in request_lines
            )
            receipt_moves = request_lines.mapped('receipt_move_ids').sudo().filtered(
                lambda move: (
                    move.state == 'done'
                    and move.product_id == product
                    and move.location_id == handover_location
                    and move.location_dest_id == self.destination_location_id
                )
            )
            staged_product_qty = sum(
                self.production_id._quantity_in_product_uom(
                    product,
                    move.quantity or move.product_uom_qty,
                    move.product_uom,
                )
                for move in receipt_moves
            )
            missing_received_qty = max(
                received_product_qty - staged_product_qty, 0.0,
            )
            if float_compare(
                missing_received_qty, 0.0, precision_digits=3,
            ) > 0:
                source_lines = product_material_lines.mapped(
                    'production_line_id'
                ).exists()
                move_specs = [{
                    'source_location': handover_location,
                    'dest_location': self.destination_location_id,
                    'label': _('استلام إنتاج فعلي عند بدء %s') % dict(
                        FURNITURE_STAGE_SELECTION,
                    ).get(self.stage_code, self.stage_code),
                    'product': product,
                    'quantity': missing_received_qty,
                    'uom': product.uom_id,
                    'source_production_lines': source_lines,
                }]
                created_moves = self.env['stock.move']
                for _spec, move in self.production_id._create_internal_moves_batch(
                    move_specs,
                ):
                    created_moves |= move
                receipt_moves |= created_moves
                for request_line in request_lines:
                    request_line.sudo().write({
                        'receipt_move_ids': [(6, 0, receipt_moves.ids)],
                    })
            primary_receipt = receipt_moves.sorted(
                lambda move: -(move.quantity or move.product_uom_qty)
            )[:1]
            allocations = self.production_id._allocate_received_product_qty(
                product_material_lines, received_product_qty,
            )
            for line in product_material_lines:
                line.sudo().write({
                    'warehouse_receipt_confirmed': True,
                    'warehouse_received_qty': allocations[line.id],
                    'move_id': primary_receipt.id if primary_receipt else False,
                })
            source_lines = product_material_lines.mapped(
                'production_line_id'
            ).exists()
            linked_moves = request_lines.mapped(
                'issue_move_ids'
            ) | receipt_moves
            if source_lines and linked_moves:
                linked_moves.sudo().write({
                    'furniture_source_production_line_ids': [
                        (6, 0, source_lines.ids),
                    ],
                })
        return material_lines

    def _execute_approved_start(self):
        self.ensure_one()
        stage_order = self._get_stage_order()
        if not stage_order:
            raise UserError(_('أمر المرحلة المرتبط بطلب الصرف لم يعد موجودًا.'))
        payload = self.payload_json or {}
        # The approved batch must run with the production operator who clicks
        # "Start", never with the historical request creator. Old permissions
        # may have been created by the storekeeper, who intentionally has no
        # rights to modify production material lines.
        actor = self.env.user
        context = dict(
            self.env.context,
            furniture_storekeeper_approval_bypass=True,
            furniture_skip_stage_start_prompt=True,
            furniture_store_request_id=self.id,
        )
        if self.request_kind == 'first_stage':
            wizard = self.env['furniture.mrp.first.stage.start.wizard'].with_user(actor).create({
                'production_id': self.production_id.id,
                'stage_code': self.stage_code,
                'stage_order_model': self.stage_order_model,
                'stage_order_res_id': self.stage_order_res_id,
                'line_ids': [(0, 0, line_vals) for line_vals in payload.get('lines', [])],
            })
            wizard.with_context(context).action_start_selected_from_stock()
            return
        if self.request_kind == 'stage_hall':
            wizard = self.env['furniture.mrp.stage.start.carryover.wizard'].with_user(actor).create({
                'production_id': self.production_id.id,
                'stage_code': self.stage_code,
                'stage_order_model': self.stage_order_model,
                'stage_order_res_id': self.stage_order_res_id,
                'line_ids': [(0, 0, line_vals) for line_vals in payload.get('lines', [])],
            })
            wizard.with_context(context)._action_start_with_mode(self.start_mode)
            return
        direct_production_lines = self.env['furniture.mrp.production.line']
        if self.production_id._is_material_only_stage(self.stage_code):
            requested_line_ids = [
                int(line_id)
                for line_id in payload.get('production_line_ids', [])
                if str(line_id).isdigit()
            ]
            direct_production_lines = self.env[
                'furniture.mrp.production.line'
            ].browse(requested_line_ids).exists()
            valid_lines = (
                self.production_id._get_material_only_stage_start_line_candidates(
                    self.stage_code,
                    stage_order=stage_order,
                )
                | stage_order._get_stage_line_ids_data(
                    'active_production_line_ids_data'
                )
            )
            # Requests created before material-only stages stored their material
            # snapshot without explicit production-line ids.  Keep those open
            # requests usable after the upgrade by resolving their still-valid
            # candidates at execution time.
            if not requested_line_ids:
                direct_production_lines = valid_lines
            if not direct_production_lines or direct_production_lines - valid_lines:
                raise UserError(_(
                    'أصناف مرحلة %s تغيرت بعد اعتماد إذن المخزن. أنشئ إذنًا جديدًا.'
                ) % dict(FURNITURE_STAGE_SELECTION).get(
                    self.stage_code,
                    self.stage_code,
                ))
            stage_order.with_context(
                furniture_skip_line_consolidation=True,
            )._add_stage_active_lines(direct_production_lines)
            self.production_id._get_or_create_first_stage_material_lines(
                direct_production_lines,
                self.stage_code,
            )

        # A direct stage may already see the physically received quantity in
        # its hall and therefore skip the regular material-move branch.  Link
        # the audited receipt to the recipe rows before starting, otherwise a
        # later repair/cost pass could treat the full recipe quantity as still
        # required and silently top up the warehouse discrepancy from Stock.
        if (
            self.material_line_ids.mapped('issue_move_ids')
            or (
                self.stage_code == 'tailoring'
                and self.material_line_ids
                and self.receipt_confirmed
            )
        ):
            direct_material_lines = self.production_id._stage_material_lines(
                stage_order._name,
            )
            if direct_production_lines:
                direct_material_lines = direct_material_lines.filtered(
                    lambda line: (
                        line.production_line_id in direct_production_lines
                    )
                )
            if direct_material_lines:
                self._apply_receipt_to_material_lines(direct_material_lines)
        stage_order.with_user(actor).with_context(
            context,
            furniture_stage_start_mode='direct',
        ).action_start()

    def action_approve(self):
        last_receipt_required = False
        last_has_shortage = False
        for request in self:
            request._lock_for_warehouse_decision()
            request._check_can_approve()
            if request.state != 'pending':
                raise UserError(_('طلب الصرف ده لم يعد في حالة انتظار.'))
            request._issue_materials_to_handover()
            # The issue method writes through ``sudo()`` because warehouse
            # operators do not own the immutable request lines.  Refresh this
            # environment before deciding whether a receipt is required;
            # otherwise a prefetched zero can incorrectly mark a real partial
            # handover as already confirmed.
            request.material_line_ids.invalidate_recordset([
                'issued_qty', 'issue_move_ids',
            ])
            receipt_required = any(
                float_compare(
                    line.issued_qty, 0.0, precision_digits=3,
                ) > 0
                for line in request.material_line_ids
            )
            has_shortage = bool(
                request._allows_material_shortage_start()
                and any(
                    float_compare(
                        line.issued_qty,
                        line.requested_qty,
                        precision_digits=3,
                    ) < 0
                    for line in request.material_line_ids
                )
            )
            last_receipt_required = receipt_required
            last_has_shortage = has_shortage
            request.sudo().write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_at': fields.Datetime.now(),
                'receipt_state': (
                    'waiting'
                    if receipt_required else
                    ('partial' if has_shortage else 'full')
                ),
                'receipt_confirmed': not receipt_required,
            })
            request.activity_ids.filtered(lambda activity: activity.state != 'done').action_done()
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(request.stage_code, request.stage_code)
            notify_users = request.requested_by_id | request.production_id.create_uid
            if has_shortage:
                shortage_template = _(
                    '%(product)s: الناقص %(quantity)s %(uom)s'
                )
                shortage_details = '\n'.join(
                    shortage_template % {
                        'product': line.product_id.display_name,
                        'quantity': request.production_id._format_dimension_value(
                            max(line.requested_qty - line.issued_qty, 0.0)
                        ),
                        'uom': line.product_uom_id.display_name,
                    }
                    for line in request.material_line_ids
                    if float_compare(
                        line.issued_qty,
                        line.requested_qty,
                        precision_digits=3,
                    ) < 0
                )
                message = _(
                    'وافق أمين المخزن على المتاح من الإذن %(request)s '
                    'لمرحلة %(stage)s. يمكن بدء التفصيل مع تسجيل العجز:\n'
                    '%(details)s'
                ) % {
                    'request': request.name,
                    'stage': stage_label,
                    'details': shortage_details,
                }
            else:
                message = (
                    _('وافق أمين المخزن على الإذن %s لمرحلة %s. تم تسليم الخامات إلى عهدة الانتظار؛ أكد «استلام من المخزن» قبل بدء التصنيع.')
                    if receipt_required else
                    _('وافق أمين المخزن على الإذن %s لمرحلة %s. لا توجد خامات مطلوب استلامها ويمكن بدء المرحلة.')
                ) % (
                    request.name,
                    stage_label,
                )
            stage_order = request._get_stage_order()
            notification_target = (
                request if receipt_required else (stage_order or request.production_id)
            )
            self._send_bus_notification(
                notify_users,
                _('مواد ناقصة في التفصيل') if has_shortage else _('تم اعتماد إذن المخزن'),
                message,
                'warning' if has_shortage else 'success',
                action_model=notification_target._name,
                action_res_id=notification_target.id,
                action_name=(
                    _('تأكيد الاستلام') if receipt_required else _('فتح المرحلة')
                ),
                refresh_model=request.stage_order_model,
                refresh_res_id=request.stage_order_res_id,
                play_sound=True,
            )
            request.production_id.sudo().message_post(
                body='✅ %s' % message,
                partner_ids=notify_users.mapped('partner_id').ids,
            )
            request.message_post(body='✅ %s' % message)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم اعتماد الإذن'),
                'message': (
                    _(
                        'تم صرف المتاح فقط للتفصيل، وسيتضح العجز حسب '
                        'المنتج والخامة داخل أمر المرحلة.'
                    )
                    if last_has_shortage else
                    _(
                        'تم تسليم الخامات إلى عهدة الانتظار. '
                        'لن تدخل صالة المرحلة قبل تأكيد استلام الإنتاج.'
                    )
                    if last_receipt_required else
                    _('تم اعتماد الإذن ولا توجد خامات مطلوب استلامها.')
                ),
                'type': 'warning' if last_has_shortage else 'success',
                'sticky': last_has_shortage,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_start_approved(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_('لا يمكن بدء المرحلة قبل اعتماد إذن المخزن.'))
        if (
            self.material_line_ids.mapped('issue_move_ids')
            and not self.receipt_confirmed
        ):
            raise UserError(_(
                'الخامات ما زالت في عهدة التسليم. '
                'افتح إذن المخزن واضغط «استلام من المخزن» أولًا.'
            ))
        self._execute_approved_start()
        self.sudo().write({
            'state': 'started',
            'started_by_id': self.env.user.id,
            'started_at': fields.Datetime.now(),
        })
        stage_label = dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code)
        message = _('تم تنفيذ الإذن %s وبدء مرحلة %s للمنتجات المختارة.') % (
            self.name, stage_label,
        )
        self.message_post(body='▶️ %s' % message)
        self.production_id.sudo().message_post(body='▶️ %s' % message)
        stage_order = self._get_stage_order()
        has_shortage = bool(
            stage_order
            and stage_order._name == 'furniture.mrp.tailoring'
            and stage_order.has_material_shortage
        )
        shortage_details = (
            stage_order.material_shortage_details if has_shortage else False
        )
        self._send_bus_notification(
            self.assigned_to_id,
            _('بدأ التفصيل مع مواد ناقصة') if has_shortage else _('تم بدء المرحلة'),
            shortage_details or message,
            'warning' if has_shortage else 'success',
            action_model=self._name,
            action_res_id=self.id,
            action_name=_('فتح الإذن'),
        )
        next_action = {
            'type': 'ir.actions.act_window',
            'name': stage_order.name if stage_order else self.production_id.name,
            'res_model': stage_order._name if stage_order else self.production_id._name,
            'res_id': stage_order.id if stage_order else self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
        if has_shortage:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('⚠️ بدأ التفصيل مع مواد ناقصة'),
                    'message': shortage_details,
                    'type': 'warning',
                    'sticky': True,
                    'next': next_action,
                },
            }
        return next_action

    def action_reject(self):
        for request in self:
            request._lock_for_warehouse_decision()
            request._check_can_approve()
            if request.state != 'pending':
                raise UserError(_('يمكن رفض الطلبات المنتظرة فقط.'))
            request.sudo().write({
                'state': 'rejected',
                'approved_by_id': self.env.user.id,
                'approved_at': fields.Datetime.now(),
            })
            request.activity_ids.filtered(lambda activity: activity.state != 'done').action_done()
            stage_label = dict(FURNITURE_STAGE_SELECTION).get(request.stage_code, request.stage_code)
            notify_users = request.requested_by_id | request.production_id.create_uid
            message = _('رفض أمين المخزن الطلب %s لمرحلة %s. لم تتحرك أي خامات.') % (
                request.name, stage_label,
            )
            if request.rejection_reason:
                message += ' ' + _('السبب: %s') % request.rejection_reason
            self._send_bus_notification(
                notify_users, _('تم رفض طلب الصرف'), message, 'danger',
                action_model=request.production_id._name,
                action_res_id=request.production_id.id,
                action_name=_('فتح أمر الإنتاج'),
            )
            request.production_id.sudo().message_post(
                body='❌ %s' % message,
                partner_ids=notify_users.mapped('partner_id').ids,
            )
            request.message_post(body='❌ %s' % message)
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class FurnitureMrpStoreRequestLine(models.Model):
    _name = 'furniture.mrp.store.request.line'
    _description = 'خامة مطلوبة في طلب صرف مرحلة إنتاج'
    _order = 'id'

    request_id = fields.Many2one(
        'furniture.mrp.store.request', string='طلب الصرف', required=True,
        ondelete='cascade', index=True,
    )
    product_id = fields.Many2one('product.product', string='الخامة', required=True, readonly=True)
    product_uom_id = fields.Many2one('uom.uom', string='الوحدة', required=True, readonly=True)
    requested_qty = fields.Float(string='الكمية المطلوبة', required=True, readonly=True, digits=(16, 3))
    available_qty = fields.Float(string='المتاح وقت الطلب', readonly=True, digits=(16, 3))
    shortage_qty = fields.Float(string='العجز', compute='_compute_shortage', digits=(16, 3))
    issued_qty = fields.Float(
        string='المصروف من المخزن', readonly=True, copy=False,
        digits=(16, 3),
    )
    received_qty = fields.Float(
        string='المستلم فعليًا', readonly=True, copy=False,
        digits=(16, 3),
    )
    receipt_difference_qty = fields.Float(
        string='فرق الاستلام', readonly=True, copy=False,
        digits=(16, 3),
    )
    issue_move_ids = fields.Many2many(
        'stock.move', 'furn_store_req_line_issue_move_rel',
        'request_line_id', 'move_id', string='حركات التسليم المخزني',
        readonly=True, copy=False,
    )
    receipt_move_ids = fields.Many2many(
        'stock.move', 'furn_store_req_line_receipt_move_rel',
        'request_line_id', 'move_id', string='حركات استلام الإنتاج',
        readonly=True, copy=False,
    )

    @api.depends('requested_qty', 'available_qty')
    def _compute_shortage(self):
        for line in self:
            line.shortage_qty = max((line.requested_qty or 0.0) - (line.available_qty or 0.0), 0.0)

    def _move_qty_in_line_uom(self, move):
        self.ensure_one()
        quantity = move.quantity or move.product_uom_qty
        return move.product_uom._compute_quantity(
            quantity, self.product_uom_id, round=False,
        )

    def _valid_issue_moves(self, handover_location):
        self.ensure_one()
        return self.issue_move_ids.sudo().filtered(lambda move: (
            move.state == 'done'
            and move.product_id == self.product_id
            and move.location_dest_id == handover_location
        ))

    def _valid_receipt_moves(self, destination_location):
        self.ensure_one()
        return self.receipt_move_ids.sudo().filtered(lambda move: (
            move.state == 'done'
            and move.product_id == self.product_id
            and move.location_dest_id == destination_location
        ))


class FurnitureMrpStoreReceiptWizard(models.TransientModel):
    _name = 'furniture.mrp.store.receipt.wizard'
    _description = 'تأكيد استلام خامات إذن مرحلة من المخزن'

    request_id = fields.Many2one(
        'furniture.mrp.store.request', string='إذن المخزن',
        required=True, readonly=True, ondelete='cascade',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        related='request_id.production_id', readonly=True,
    )
    stage_code = fields.Selection(
        string='المرحلة', related='request_id.stage_code', readonly=True,
    )
    handover_location_id = fields.Many2one(
        'stock.location', string='عهدة التسليم',
        related='request_id.handover_location_id', readonly=True,
    )
    destination_location_id = fields.Many2one(
        'stock.location', string='صالة المرحلة',
        related='request_id.destination_location_id', readonly=True,
    )
    receipt_note = fields.Text(string='ملاحظة الاستلام أو سبب الفرق')
    line_ids = fields.One2many(
        'furniture.mrp.store.receipt.wizard.line', 'wizard_id',
        string='الخامات المستلمة',
    )
    has_difference = fields.Boolean(
        string='يوجد فرق', compute='_compute_has_difference',
    )

    @api.depends('line_ids.received_qty', 'line_ids.issued_qty')
    def _compute_has_difference(self):
        for wizard in self:
            wizard.has_difference = any(
                float_compare(
                    line.received_qty, line.issued_qty,
                    precision_digits=3,
                ) != 0
                for line in wizard.line_ids
            )

    def action_confirm_receipt(self):
        self.ensure_one()
        if self.has_difference and not (self.receipt_note or '').strip():
            raise UserError(_(
                'اكتب ملاحظة توضح سبب فرق الاستلام قبل التأكيد.'
            ))
        receipt_state = self.request_id._confirm_production_receipt(
            {
                line.request_line_id.id: line.received_qty
                for line in self.line_ids
            },
            note=(self.receipt_note or '').strip(),
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم تأكيد الاستلام'),
                'message': (
                    _('تم تسجيل الاستلام الجزئي والفرق على أمين المخزن.')
                    if receipt_state == 'partial'
                    else _(
                        'تم تسجيل الكمية كاملة في عهدة مسؤول الإنتاج، '
                        'وستُنقل إلى الصالة عند بدء المرحلة.'
                    )
                ),
                'type': 'warning' if receipt_state == 'partial' else 'success',
                'sticky': receipt_state == 'partial',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }


class FurnitureMrpStoreReceiptWizardLine(models.TransientModel):
    _name = 'furniture.mrp.store.receipt.wizard.line'
    _description = 'خامة مستلمة فعليًا من إذن مخزن مرحلة'
    _order = 'product_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.store.receipt.wizard', string='شاشة الاستلام',
        required=True, ondelete='cascade',
    )
    request_line_id = fields.Many2one(
        'furniture.mrp.store.request.line', string='سطر إذن المخزن',
        required=True, readonly=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة',
        related='request_line_id.product_id', readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة',
        related='request_line_id.product_uom_id', readonly=True,
    )
    issued_qty = fields.Float(
        string='المصروف من المخزن', required=True, readonly=True,
        digits=(16, 3),
    )
    received_qty = fields.Float(
        string='المستلم فعليًا', required=True, digits=(16, 3),
    )
    difference_qty = fields.Float(
        string='الفرق على المخزن', compute='_compute_difference_qty',
        digits=(16, 3),
    )

    @api.depends('issued_qty', 'received_qty')
    def _compute_difference_qty(self):
        for line in self:
            line.difference_qty = max(
                (line.issued_qty or 0.0) - (line.received_qty or 0.0),
                0.0,
            )
