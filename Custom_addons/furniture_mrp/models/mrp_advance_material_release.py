# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare

from .hr_employee import SUPERVISOR_STAGE_GROUP_XMLIDS
from .mrp_production_order import (
    FURNITURE_LEGACY_STAGE_SELECTION,
    FURNITURE_STAGE_SELECTION,
)


ACTIVE_RELEASE_STAGE_STATES = ('pending', 'issued', 'started')

RECEIPT_STATE_SELECTION = [
    ('not_issued', 'لم يصرف المخزن بعد'),
    ('waiting', 'بانتظار استلام الإنتاج'),
    ('full', 'تم الاستلام بالكامل'),
    ('partial', 'تم الاستلام بعجز'),
    ('legacy', 'مستلم قبل تفعيل دورة الاستلام'),
]


class FurnitureMrpAdvanceMaterialRelease(models.Model):
    """One warehouse request that may issue raw materials for many stages.

    The header is deliberately only an approval envelope.  Every selected stage
    keeps its own destination, exact production material lines, aggregated
    material snapshot and stock moves.  Issuing therefore never starts a stage
    or changes a stage order state.
    """

    _name = 'furniture.mrp.advance.material.release'
    _description = 'إذن صرف مبكر لخامات مراحل الإنتاج'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'requested_at desc, id desc'

    name = fields.Char(
        string='رقم الإذن', required=True, readonly=True, copy=False,
        default='جديد', index=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج الفردي',
        ondelete='set null', index=True, tracking=True, copy=False,
        help='حقل توافق للأذونات الفردية القديمة. الأذونات الجماعية ترتبط '
             'بأوامرها من خلال سطور المراحل.',
    )
    production_ids = fields.Many2many(
        'furniture.mrp.production',
        'furn_adv_release_production_rel',
        'release_id', 'production_id',
        string='أوامر الإنتاج',
        compute='_compute_production_links', store=True,
        readonly=True, copy=False,
    )
    state = fields.Selection(
        [
            ('pending', 'بانتظار أمين المخزن'),
            ('issued', 'تم صرف الخامات'),
            ('rejected', 'مرفوض'),
            ('returned', 'تم إرجاع الخامات'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة', required=True, default='pending',
        index=True, tracking=True, copy=False,
    )
    stage_line_ids = fields.One2many(
        'furniture.mrp.advance.material.release.stage', 'release_id',
        string='المراحل المطلوب صرفها', readonly=True, copy=False,
    )
    requested_by_id = fields.Many2one(
        'res.users', string='طالب الصرف', required=True, readonly=True,
        default=lambda self: self.env.user, copy=False,
    )
    assigned_to_id = fields.Many2one(
        'res.users', string='أمين المخزن', required=True, readonly=True,
        tracking=True, copy=False,
    )
    can_warehouse_decide = fields.Boolean(
        string='يمكنه اتخاذ قرار المخزن',
        compute='_compute_can_warehouse_decide',
    )
    requested_at = fields.Datetime(
        string='وقت الطلب', required=True, readonly=True,
        default=fields.Datetime.now, copy=False,
    )
    issued_by_id = fields.Many2one(
        'res.users', string='صرف بواسطة', readonly=True, copy=False,
    )
    issued_at = fields.Datetime(string='وقت الصرف', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='اعتمد بواسطة', related='issued_by_id',
        store=True, readonly=True,
    )
    approved_at = fields.Datetime(
        string='وقت الاعتماد', related='issued_at', store=True, readonly=True,
    )
    rejected_by_id = fields.Many2one(
        'res.users', string='رفض بواسطة', readonly=True, copy=False,
    )
    rejected_at = fields.Datetime(string='وقت الرفض', readonly=True, copy=False)
    rejection_reason = fields.Text(string='سبب الرفض', copy=False)
    returned_by_id = fields.Many2one(
        'res.users', string='أرجع بواسطة', readonly=True, copy=False,
    )
    returned_at = fields.Datetime(string='وقت الإرجاع', readonly=True, copy=False)
    cancelled_by_id = fields.Many2one(
        'res.users', string='ألغى بواسطة', readonly=True, copy=False,
    )
    cancelled_at = fields.Datetime(string='وقت الإلغاء', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True,
        readonly=True, index=True, copy=False,
    )
    production_count = fields.Integer(
        string='عدد أوامر الإنتاج', compute='_compute_summary',
    )
    production_summary = fields.Char(
        string='أوامر الإنتاج', compute='_compute_summary',
    )
    stage_count = fields.Integer(
        string='عدد المراحل', compute='_compute_summary',
    )
    material_count = fields.Integer(
        string='عدد الخامات', compute='_compute_summary',
    )
    returnable_stage_count = fields.Integer(
        string='مراحل قابلة للإرجاع', compute='_compute_summary',
    )
    has_shortage = fields.Boolean(
        string='يوجد عجز وقت الطلب', compute='_compute_summary',
    )
    stage_summary = fields.Char(
        string='المراحل', compute='_compute_summary',
    )

    _sql_constraints = [
        (
            'advance_release_name_company_unique',
            'unique(name, company_id)',
            'رقم إذن الصرف المبكر مستخدم بالفعل داخل نفس الشركة.',
        ),
    ]

    @api.model
    def _current_user_can_open_full_release(self):
        user = self.env.user
        return bool(
            self.env.is_superuser()
            or user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
            or user.has_group('furniture_mrp.group_furniture_mrp_storekeeper')
        )

    @api.model
    def _check_can_open_full_release(self):
        if not self._current_user_can_open_full_release():
            raise AccessError(_(
                'الإذن المجمّع متاح لمدير الإنتاج وأمين المخزن فقط. '
                'مشرف المرحلة يتابع خامات مرحلته من لوحة التحكم.'
            ))
        return True

    def action_print_warehouse_receipt(self):
        """Print all stages and materials in the aggregate voucher on A4."""
        self.ensure_one()
        self._check_can_open_full_release()
        return self.env.ref(
            'furniture_mrp.action_report_furniture_mrp_advance_material_release'
        ).report_action(self, config=False)

    @api.depends(
        'production_id', 'stage_line_ids.production_id',
    )
    def _compute_production_links(self):
        for release in self:
            release.production_ids = (
                release.stage_line_ids.mapped('production_id')
                | release.production_id
            )

    @api.depends(
        'stage_line_ids', 'stage_line_ids.stage_code',
        'stage_line_ids.production_id', 'production_ids',
        'production_ids.name',
        'stage_line_ids.state',
        'stage_line_ids.receipt_confirmed',
        'stage_line_ids.material_line_ids',
        'stage_line_ids.material_line_ids.shortage_qty',
    )
    def _compute_summary(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for release in self:
            detail_lines = release.stage_line_ids.mapped('material_line_ids')
            productions = release.production_ids.sorted('id')
            release.production_count = len(productions)
            release.production_summary = '، '.join(productions.mapped('name'))
            release.stage_count = len(release.stage_line_ids)
            release.material_count = len(detail_lines)
            release.returnable_stage_count = len(
                release.stage_line_ids.filtered(
                    lambda stage: (
                        stage.state == 'issued'
                        and not stage.receipt_confirmed
                    )
                )
            )
            release.has_shortage = any(
                float_compare(line.shortage_qty, 0.0, precision_digits=3) > 0
                for line in detail_lines
            )
            multi_production = len(productions) > 1
            release.stage_summary = '، '.join(
                (
                    '%s — %s' % (
                        stage.production_id.name,
                        labels.get(stage.stage_code, stage.stage_code),
                    )
                    if multi_production else
                    labels.get(stage.stage_code, stage.stage_code)
                )
                for stage in release.stage_line_ids.sorted(
                    lambda stage: (stage.sequence, stage.id)
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('company_id') and vals.get('production_id'):
                production = self.env['furniture.mrp.production'].browse(
                    vals['production_id']
                ).exists()
                if production:
                    vals['company_id'] = production.company_id.id
            if vals.get('name', 'جديد') == 'جديد':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code(
                        'furniture.mrp.advance.material.release'
                    )
                    or '/'
                )
        releases = super().create(vals_list)
        for release in releases.filtered(lambda item: item.name == '/'):
            release.name = 'ADV/%05d' % release.id
        return releases

    def write(self, vals):
        if not self.env.is_superuser():
            if set(vals) - {'rejection_reason'}:
                raise AccessError(_(
                    'لا يمكن تعديل بيانات أو حالة إذن الصرف مباشرة. '
                    'استخدم أزرار الصرف أو الرفض أو الإرجاع المخصصة.'
                ))
            self._check_can_approve()
            if any(release.state != 'pending' for release in self):
                raise AccessError(_('يمكن كتابة سبب الرفض قبل اتخاذ القرار فقط.'))
        return super().write(vals)

    @api.depends('assigned_to_id')
    @api.depends_context('uid')
    def _compute_can_warehouse_decide(self):
        is_storekeeper = self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_storekeeper'
        )
        for release in self:
            release.can_warehouse_decide = bool(
                self.env.is_superuser()
                or (
                    is_storekeeper
                    and release.assigned_to_id == self.env.user
                )
            )

    @api.model
    def _normalize_production(self, production):
        if isinstance(production, int):
            production = self.env['furniture.mrp.production'].browse(production)
        production = production.exists()
        if not production or len(production) != 1:
            raise UserError(_('أمر الإنتاج المطلوب غير موجود.'))
        return production

    @api.model
    def _check_can_request(self):
        """Keep material-request creation inside the production roles.

        The form buttons are group-restricted, but this server-side check is
        still required because transient methods and ``create_from_stage_codes``
        can otherwise be called directly over RPC.
        """
        if self.env.is_superuser() or any((
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_manager'
            ),
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_supervisor'
            ),
        )):
            return True
        raise AccessError(_(
            'طلب صرف خامات مراحل الإنتاج متاح لمدير الإنتاج أو مشرف الإنتاج فقط.'
        ))

    @api.model
    def _check_production_company_access(self, production):
        """Reject cross-company requests even when invoked directly over RPC."""
        production = self._normalize_production(production)
        if (
            not self.env.is_superuser()
            and production.company_id not in self.env.companies
        ):
            raise AccessError(_(
                'لا يمكنك طلب خامات لأمر إنتاج تابع لشركة غير مفعلة لحسابك.'
            ))
        return production

    @api.model
    def _check_can_request_production_stages(self, production, stage_codes):
        """Enforce the requester's exact supervisor stage/company scope.

        The generic supervisor group is only an entry-level role and must not
        authorize every production stage.  Employee assignments retain the
        company/stage pairing that the synchronized user groups alone cannot
        represent when one login supervises employees in several companies.
        Managers keep their existing all-stage access inside their currently
        allowed companies.
        """
        production = self._check_production_company_access(production)
        ordered_codes = self._ordered_stage_codes(stage_codes)
        user = self.env.user
        if (
            self.env.is_superuser()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            return production, ordered_codes

        assigned_codes = set()
        supervisors = self.env['hr.employee'].sudo().search([
            ('active', '=', True),
            ('user_id', '=', user.id),
            ('furniture_mrp_role', '=', 'supervisor'),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', production.company_id.id),
        ])
        for employee in supervisors:
            assigned_codes.update(
                employee.furniture_mrp_supervisor_stage_ids.mapped('code')
            )

        # Require both the durable employee assignment and its synchronized
        # stage group.  This fails closed if either side is stale or a generic
        # supervisor group was granted manually.
        allowed_codes = {
            code
            for code in assigned_codes
            if code in SUPERVISOR_STAGE_GROUP_XMLIDS
            and user.has_group(SUPERVISOR_STAGE_GROUP_XMLIDS[code])
        }
        denied_codes = [
            code for code in ordered_codes if code not in allowed_codes
        ]
        if denied_codes:
            labels = dict(FURNITURE_STAGE_SELECTION)
            raise AccessError(_(
                'لا يمكنك طلب خامات المراحل التالية لأمر الإنتاج %(order)s: '
                '%(stages)s. يسمح للمشرف بطلب خامات مراحله المكلّف بها داخل '
                'شركة الأمر فقط.'
            ) % {
                'order': production.name,
                'stages': '، '.join(
                    labels.get(code, code) for code in denied_codes
                ),
            })
        return production, ordered_codes

    @api.model
    def _ordered_stage_codes(self, stage_codes):
        selected = set(stage_codes or [])
        return [
            code for code, _label in FURNITURE_STAGE_SELECTION
            if code in selected
        ]

    @api.model
    def _stage_material_buckets(self, production, stage_code):
        """Current exact lines aggregated in the product stock UoM."""
        buckets = {}
        material_lines = production.material_line_ids.filtered(
            lambda line: (
                line.stage == stage_code
                and line.product_id
                and float_compare(line.qty_needed, 0.0, precision_digits=3) > 0
            )
        )
        for material_line in material_lines:
            product = material_line.product_id
            uom = product.uom_id
            key = (product.id, uom.id)
            bucket = buckets.setdefault(key, {
                'product': product,
                'uom': uom,
                'requested_qty': 0.0,
                'material_lines': self.env['furniture.mrp.material.line'],
            })
            bucket['requested_qty'] += production._quantity_in_product_uom(
                product,
                material_line.qty_needed,
                material_line.product_uom_id,
            )
            bucket['material_lines'] |= material_line
        return buckets

    @api.model
    def _snapshot_payloads(
        self, production, stage_codes, availability_pool=None,
    ):
        """Build a combined availability snapshot across all selected stages.

        Availability is allocated once per product in stage order.  A product
        needed by two stages cannot incorrectly appear fully available twice.
        A caller creating requests for several productions may pass one shared
        pool so the same physical stock is not advertised as available to every
        order in the batch.
        """
        source_location = (
            production.location_src_id
            or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        )
        available_by_product = (
            availability_pool if availability_pool is not None else {}
        )
        payloads = []
        for sequence, stage_code in enumerate(stage_codes, start=1):
            destination = production._stage_work_location(stage_code)
            if not destination:
                raise UserError(_(
                    'لا يوجد موقع صالة محدد لمرحلة %s.'
                ) % dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code))
            buckets = self._stage_material_buckets(production, stage_code)
            details = []
            stage_material_lines = self.env['furniture.mrp.material.line']
            for key, bucket in sorted(
                buckets.items(),
                key=lambda item: (item[1]['product'].display_name or '', item[0]),
            ):
                product = bucket['product']
                availability_key = (
                    production.company_id.id,
                    source_location.id if source_location else False,
                    product.id,
                )
                if availability_key not in available_by_product:
                    available_by_product[availability_key] = (
                        production._stage_location_product_qty(
                            source_location, product, production.company_id,
                        ) if source_location else 0.0
                    )
                requested_qty = bucket['requested_qty']
                available_qty = min(
                    max(available_by_product[availability_key], 0.0),
                    requested_qty,
                )
                available_by_product[availability_key] = max(
                    available_by_product[availability_key] - requested_qty,
                    0.0,
                )
                details.append({
                    'product_id': product.id,
                    'product_uom_id': bucket['uom'].id,
                    'requested_qty': requested_qty,
                    'available_qty': available_qty,
                    'source_material_line_ids': [
                        (6, 0, bucket['material_lines'].ids),
                    ],
                })
                stage_material_lines |= bucket['material_lines']
            payloads.append({
                'sequence': sequence * 10,
                'production_id': production.id,
                'stage_code': stage_code,
                'destination_location_id': destination.id,
                'handover_location_id': self.env.ref(
                    'furniture_mrp.location_material_handover'
                ).id,
                'source_material_line_ids': [(6, 0, stage_material_lines.ids)],
                'material_line_ids': [(0, 0, detail) for detail in details],
            })
        return payloads

    @api.model
    def _validate_stage_request(
        self, production, stage_codes, production_lines=None,
    ):
        """Validate one order without mutating it or creating notifications.

        ``production_lines`` narrows product-batch requests to their frozen
        members.  A completed sibling product may already have consumed its
        own raw materials; that must not block a new request for another
        product in the same manufacturing order and stage.
        """
        production = self._check_production_company_access(production)
        if production.state not in ('confirmed', 'in_production'):
            raise UserError(_('يجب تأكيد أمر الإنتاج قبل طلب صرف خامات المراحل.'))
        ordered_codes = self._ordered_stage_codes(stage_codes)
        if not ordered_codes:
            raise UserError(_('اختر مرحلة واحدة على الأقل لطلب صرف خاماتها.'))
        required_codes = set(production._required_stage_codes())
        invalid_codes = [code for code in ordered_codes if code not in required_codes]
        if invalid_codes:
            labels = dict(FURNITURE_STAGE_SELECTION)
            raise UserError(_(
                'المراحل التالية ليست ضمن مسار أمر الإنتاج: %s.'
            ) % '، '.join(labels.get(code, code) for code in invalid_codes))

        scoped_line_ids = False
        if production_lines is not None:
            scoped_lines = production_lines.exists()
            invalid_lines = scoped_lines.filtered(
                lambda line: line.production_id != production
            )
            if not scoped_lines or invalid_lines:
                raise AccessError(_(
                    'نطاق أصناف طلب الخامات لا يخص أمر الإنتاج.'
                ))
            scoped_line_ids = set(scoped_lines.ids)

        labels = dict(FURNITURE_STAGE_SELECTION)
        for stage_code in ordered_codes:
            stage_order = production._stage_order_record(stage_code)
            if stage_order and stage_order.state != 'pending':
                raise UserError(_(
                    'مرحلة %s بدأت بالفعل ولا يمكن إنشاء إذن صرف مبكر لها.'
                ) % labels.get(stage_code, stage_code))
            consumed_lines = production.material_line_ids.filtered(
                lambda line: (
                    line.stage == stage_code
                    and (
                        scoped_line_ids is False
                        or line.production_line_id.id in scoped_line_ids
                    )
                    and production._material_line_already_consumed(line)
                )
            )
            if consumed_lines:
                raise UserError(_(
                    'بعض خامات مرحلة %s دخلت الإنتاج بالفعل.'
                ) % labels.get(stage_code, stage_code))
        return production, ordered_codes

    @api.model
    def _check_no_active_duplicates(self, production, stage_codes):
        duplicate_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', production.id),
            ('stage_code', 'in', list(stage_codes)),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ])
        if duplicate_stages:
            labels = dict(FURNITURE_STAGE_SELECTION)
            duplicate_labels = '، '.join(sorted(set(
                labels.get(stage.stage_code, stage.stage_code)
                for stage in duplicate_stages
            )))
            raise UserError(_(
                'يوجد إذن صرف مبكر نشط بالفعل للمراحل التالية: %s.'
            ) % duplicate_labels)

        legacy_requests = self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', '=', production.id),
            ('stage_code', 'in', list(stage_codes)),
            ('state', 'in', ('pending', 'approved')),
        ])
        if legacy_requests:
            labels = dict(FURNITURE_STAGE_SELECTION)
            legacy_labels = '، '.join(sorted(set(
                labels.get(request.stage_code, request.stage_code)
                for request in legacy_requests
            )))
            raise UserError(_(
                'يوجد إذن مخزن عادي نشط بالفعل للمراحل التالية: %s. '
                'أكمل أو ألغِ الإذن الحالي قبل إضافتها لإذن المراحل المجمّع.'
            ) % legacy_labels)

    @api.model
    def create_from_production_stage_map(
        self, productions, stage_codes_by_production, requested_by=False,
        availability_pool=None, notify_storekeeper=True,
        is_batch_request=False,
    ):
        """Create one release envelope for one or several production orders.

        The header is shared, while every stage payload keeps the exact
        production order that owns its materials, stock moves and costs.
        Validation and row locking happen for the whole selection before the
        release is created, so the operation remains all-or-nothing.
        """
        self._check_can_request()
        if isinstance(productions, int):
            productions = self.env['furniture.mrp.production'].browse(productions)
        elif isinstance(productions, (list, tuple, set)):
            productions = self.env['furniture.mrp.production'].browse(
                list(productions)
            )
        productions = productions._origin.exists().sorted('id')
        if not productions:
            raise UserError(_('اختر أمر إنتاج واحدًا على الأقل.'))

        checked = self.env['furniture.mrp.production']
        for production in productions:
            checked |= self._check_production_company_access(production)
        productions = checked.sorted('id')
        company = productions[0].company_id
        if any(production.company_id != company for production in productions):
            raise UserError(_('لا يمكن جمع أوامر إنتاج من شركات مختلفة في إذن واحد.'))

        selected_ids = set(productions.ids)
        provided_ids = {
            int(production_id)
            for production_id, codes in (stage_codes_by_production or {}).items()
            if codes
        }
        if provided_ids != selected_ids:
            raise UserError(_(
                'يجب تحديد مرحلة واحدة على الأقل لكل أمر إنتاج داخل الإذن.'
            ))

        scoped_codes_by_production = {}
        for production in productions:
            _production, scoped_codes = (
                self._check_can_request_production_stages(
                    production,
                    stage_codes_by_production[production.id],
                )
            )
            scoped_codes_by_production[production.id] = scoped_codes

        # Serialize creation for all orders in a deterministic sequence.  This
        # closes both same-wizard and multi-tab duplicate request races.
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(productions.ids)],
        )
        productions.invalidate_recordset(['state'])

        ordered_codes_by_production = {}
        for production in productions:
            production, ordered_codes = self._validate_stage_request(
                production, scoped_codes_by_production[production.id],
            )
            self._check_no_active_duplicates(production, ordered_codes)
            production._ensure_stage_locations()
            ordered_codes_by_production[production.id] = ordered_codes

        helper = self.env['furniture.mrp.store.request']
        storekeeper = helper._get_storekeeper_user(company)
        requester = requested_by or self.env.user
        shared_availability = (
            availability_pool if availability_pool is not None else {}
        )
        payloads = []
        global_sequence = 0
        for production in productions:
            for payload in self._snapshot_payloads(
                production,
                ordered_codes_by_production[production.id],
                availability_pool=shared_availability,
            ):
                global_sequence += 10
                payload['sequence'] = global_sequence
                payload['production_id'] = production.id
                payloads.append(payload)

        is_single = len(productions) == 1
        release = self.sudo().create({
            # ``production_id`` is the compatibility marker used to separate
            # the per-order receipt buttons from the dashboard batch receipt.
            # A request launched from the batch screen must stay a batch
            # request even when the user selected only one production order.
            'production_id': (
                productions.id
                if is_single and not is_batch_request
                else False
            ),
            'company_id': company.id,
            'requested_by_id': requester.id,
            'assigned_to_id': storekeeper.id,
            'stage_line_ids': [(0, 0, payload) for payload in payloads],
        })
        production_names = '، '.join(productions.mapped('name'))
        release.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=storekeeper.id,
            summary=_('مراجعة إذن صرف خامات مراحل'),
            note=_(
                'راجع الإذن %(release)s الذي يضم %(count)s أوامر إنتاج: '
                '%(productions)s. '
                'الاعتماد سيصرف خامات المراحل المحددة إلى صالاتها من غير بدء أي مرحلة.'
            ) % {
                'release': release.name,
                'count': len(productions),
                'productions': production_names,
            },
        )
        message = _(
            'الإذن %(release)s يضم %(count)s أوامر إنتاج '
            'والمراحل المختارة بانتظار اعتمادك.'
        ) % {
            'release': release.name,
            'count': len(productions),
        }
        if notify_storekeeper:
            helper._send_bus_notification(
                storekeeper,
                _('إذن خامات واحد لأوامر إنتاج متعددة'),
                message,
                notification_type='warning',
                action_model=release._name,
                action_res_id=release.id,
                action_name=_('فتح الإذن'),
                play_sound=True,
            )
        labels = dict(FURNITURE_STAGE_SELECTION)
        for production in productions:
            production_stages = release.stage_line_ids.filtered(
                lambda stage, current=production: stage.production_id == current
            ).sorted('sequence')
            production.sudo().message_post(
                body=_('⏳ تم ضم الأمر إلى إذن الصرف %s للمراحل: %s') % (
                    release.name,
                    '، '.join(
                        labels.get(stage.stage_code, stage.stage_code)
                        for stage in production_stages
                    ),
                )
            )
        productions.invalidate_recordset([
            'advance_material_release_stage_ids',
            'advance_material_release_ids',
            'advance_material_release_count',
            'has_uncovered_advance_material_stages',
        ])
        # Creation needs sudo for the immutable warehouse snapshot, but the
        # returned record must keep the real requester's security context.
        # Otherwise a supervisor notification would inherit uid=1 and expose
        # the full aggregate-release action.
        return release.with_user(requester)

    @api.model
    def create_from_stage_codes(
        self, production, stage_codes, requested_by=False,
        availability_pool=None, notify_storekeeper=True,
    ):
        """Backward-compatible single-order factory."""
        production = self._check_production_company_access(production)
        return self.create_from_production_stage_map(
            production,
            {production.id: stage_codes},
            requested_by=requested_by,
            availability_pool=availability_pool,
            notify_storekeeper=notify_storekeeper,
        )

    @api.model
    def _repair_legacy_added_stage_coverage(self, productions=False):
        """Backfill safe gaps left when bases and legacy sewing were introduced.

        Old full-route releases predate these two stage codes.  They therefore
        look complete for the historical seven-stage route but make the later
        stage form create a second store request.  Only repair a missing stage
        when the same release covers every older required stage, the stage has
        not started, no normal request is active, and it has no positive raw
        material requirement outside the release snapshot.  The last guard is
        deliberate: a migration must never label unissued stock as received.
        """
        ReleaseStage = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo()
        StoreRequest = self.env['furniture.mrp.store.request'].sudo()
        late_stage_codes = {'bases'}
        productions = (
            productions._origin.exists()
            if productions
            else self.env['furniture.mrp.production'].sudo().search([
                ('state', 'in', ('confirmed', 'in_production')),
            ])
        )
        if not productions:
            return ReleaseStage

        releases = self.sudo().search([
            ('state', 'in', ('pending', 'issued')),
            ('stage_line_ids.production_id', 'in', productions.ids),
        ], order='id')
        repaired = ReleaseStage
        for release in releases:
            release_productions = (
                release.stage_line_ids.mapped('production_id') & productions
            )
            for production in release_productions:
                release_stages = release.stage_line_ids.filtered(
                    lambda stage, current=production: (
                        stage.production_id == current
                        and stage.state in ACTIVE_RELEASE_STAGE_STATES
                    )
                )
                required_codes = set(production._required_stage_codes())
                covered_codes = set(release_stages.mapped('stage_code'))
                missing_codes = required_codes - covered_codes
                if (
                    not missing_codes
                    or not missing_codes.issubset(late_stage_codes)
                    or not (required_codes - late_stage_codes).issubset(
                        covered_codes
                    )
                ):
                    continue

                globally_covered = set(ReleaseStage.search([
                    ('production_id', '=', production.id),
                    ('stage_code', 'in', list(missing_codes)),
                    ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
                ]).mapped('stage_code'))
                active_request_codes = set(StoreRequest.search([
                    ('production_id', '=', production.id),
                    ('stage_code', 'in', list(missing_codes)),
                    ('state', 'in', ('pending', 'approved')),
                ]).mapped('stage_code'))
                safe_codes = []
                for stage_code in self._ordered_stage_codes(missing_codes):
                    stage_order = production._stage_order_record(stage_code)
                    if (
                        stage_code in globally_covered
                        or stage_code in active_request_codes
                        or (stage_order and stage_order.state != 'pending')
                        or self._stage_material_buckets(production, stage_code)
                    ):
                        continue
                    safe_codes.append(stage_code)
                if not safe_codes:
                    continue

                production._ensure_stage_locations()
                payloads = self._snapshot_payloads(
                    production, safe_codes, availability_pool={},
                )
                next_sequence = max(
                    release.stage_line_ids.mapped('sequence') or [0]
                )
                existing_receipts = release_stages.filtered(
                    'receipt_confirmed'
                ).sorted('received_at', reverse=True)
                inherit_receipt = bool(
                    release.state == 'issued'
                    and release_stages
                    and all(release_stages.mapped('receipt_confirmed'))
                )
                for payload in payloads:
                    next_sequence += 10
                    values = {
                        **payload,
                        'release_id': release.id,
                        'sequence': next_sequence,
                    }
                    if release.state == 'issued':
                        values.update({
                            'state': 'issued',
                            'issued_by_id': release.issued_by_id.id,
                            'issued_at': release.issued_at,
                            'receipt_state': (
                                'full' if inherit_receipt else 'waiting'
                            ),
                            'receipt_confirmed': inherit_receipt,
                            'received_by_id': (
                                existing_receipts[:1].received_by_id.id
                                if inherit_receipt else False
                            ),
                            'received_at': (
                                existing_receipts[:1].received_at
                                if inherit_receipt else False
                            ),
                        })
                    repaired |= ReleaseStage.create(values)

                production.invalidate_recordset([
                    'advance_material_release_stage_ids',
                    'advance_material_release_ids',
                    'advance_material_release_count',
                    'has_uncovered_advance_material_stages',
                ])
        return repaired

    @api.model
    def find_active_stage_release(self, production, stage_code):
        """Return the active internal stage request, not merely its header."""
        production = self._normalize_production(production)
        return self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', production.id),
            ('stage_code', '=', stage_code),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ], order='id desc', limit=1)

    @api.model
    def get_active_stage_map(self, production):
        production = self._normalize_production(production)
        stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', production.id),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ], order='sequence, id')
        return {stage.stage_code: stage for stage in stages}

    @api.model
    def validate_active_stage_snapshots(self, production):
        """Integration hook: block material refreshes that alter active requests."""
        production = self._normalize_production(production)
        active_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', production.id),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ])
        for stage in active_stages:
            stage._validate_current_snapshot(relink_sources=True)
        return True

    @api.model
    def relink_issued_moves_after_material_refresh(self, production):
        """Integration hook called after same-aggregate material lines are rebuilt."""
        production = self._normalize_production(production)
        active_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', production.id),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ])
        for stage in active_stages:
            stage._validate_current_snapshot(relink_sources=True)
            if stage.state in ('issued', 'started'):
                stage._relink_issued_moves()
        return True

    def action_open(self):
        self.ensure_one()
        self._check_can_open_full_release()
        form_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_release_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(form_view.id if form_view else False, 'form')],
            'target': 'current',
        }

    def action_open_production_order(self):
        """Open the linked order, or the participating orders for a batch."""
        self.ensure_one()
        self.env[
            'furniture.mrp.store.request'
        ]._check_can_open_production_navigation()
        productions = self.production_ids.exists().sorted('id')
        if not productions:
            raise UserError(_('أوامر الإنتاج المرتبطة بهذا الإذن لم تعد موجودة.'))
        if len(productions) > 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('أوامر الإنتاج داخل الإذن %s') % self.name,
                'res_model': productions._name,
                'view_mode': 'list,form',
                'domain': [('id', 'in', productions.ids)],
                'target': 'main',
            }
        production = productions
        return {
            'type': 'ir.actions.act_window',
            'name': production.name,
            'res_model': production._name,
            'res_id': production.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'main',
        }

    def action_open_mrp_dashboard(self):
        """Open the MRP II dashboard and reset the navigation stack."""
        self.ensure_one()
        self.env[
            'furniture.mrp.store.request'
        ]._check_can_open_production_navigation()
        action = self.env['ir.actions.actions']._for_xml_id(
            'furniture_mrp.action_furniture_mrp_dashboard'
        )
        action['target'] = 'main'
        return action

    def action_request_sent_notification(self):
        """Confirm submission to the current requester, then open the release."""
        self.ensure_one()
        params = {
            'title': _('بانتظار أمين المخزن'),
            'message': _(
                'تم إرسال إذن المراحل %s لأمين المخزن. '
                'لن تتحرك الخامات قبل الاعتماد، ولن تبدأ أي مرحلة تلقائيًا.'
            ) % self.name,
            'type': 'warning',
            'sticky': False,
        }
        if self._current_user_can_open_full_release():
            params['next'] = self.action_open()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }

    @api.model
    def action_open_for_production(self, production):
        self._check_can_open_full_release()
        production = self._normalize_production(production)
        releases = self.search([
            ('stage_line_ids.production_id', '=', production.id),
        ], order='requested_at desc, id desc')
        if len(releases) == 1:
            return releases.action_open()
        list_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_release_list',
            raise_if_not_found=False,
        )
        form_view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_release_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('أذونات صرف خامات المراحل'),
            'res_model': self._name,
            'view_mode': 'list,form',
            'views': [
                (list_view.id if list_view else False, 'list'),
                (form_view.id if form_view else False, 'form'),
            ],
            'domain': [('id', 'in', releases.ids)],
            'target': 'current',
        }

    def _check_can_approve(self):
        self.ensure_one()
        self.env['furniture.mrp.store.request']._check_can_approve(
            assigned_to=self.assigned_to_id,
        )

    def _lock(self):
        if self:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_advance_material_release '
                'WHERE id IN %s FOR UPDATE',
                [tuple(self.ids)],
            )
            self.invalidate_recordset(['state', 'assigned_to_id'])

    def _lock_productions(self):
        """Serialize issue/return against stage execution for every order."""
        self.ensure_one()
        productions = self.production_ids.exists().sorted('id')
        if productions:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(productions.ids)],
            )
            productions.invalidate_recordset(['state'])
        return productions

    def _mark_activities_done(self):
        activities = self.activity_ids.filtered(lambda activity: activity.state != 'done')
        if activities:
            activities.action_done()

    def _notification_users(self):
        self.ensure_one()
        productions = self.production_ids
        return (
            self.requested_by_id
            | productions.mapped('responsible_id')
            | productions.mapped('create_uid')
        ).filtered(lambda user: user.active)

    def _send_result_notification(self, title, message, notification_type):
        self.ensure_one()
        helper = self.env['furniture.mrp.store.request']
        users = self._notification_users()
        productions = self.production_ids
        single_production = productions if len(productions) == 1 else False
        helper._send_bus_notification(
            users,
            title,
            message,
            notification_type=notification_type,
            action_model=self._name,
            action_res_id=self.id,
            action_name=_('فتح الإذن'),
            refresh_model=(
                single_production._name if single_production else False
            ),
            refresh_res_id=(
                single_production.id if single_production else False
            ),
            play_sound=notification_type in ('success', 'danger'),
        )
        for production in productions:
            production.sudo().message_post(
                body=message,
                partner_ids=users.mapped('partner_id').ids,
            )
        self.message_post(body=message)

    def _preflight_issue_availability(self):
        self.ensure_one()
        required_by_key = defaultdict(float)
        context_by_key = {}
        for stage in self.stage_line_ids.filtered(lambda item: item.state == 'pending'):
            if stage.stage_code == 'tailoring':
                continue
            production = stage.production_id
            source_location = (
                production.location_src_id
                or self.env.ref(
                    'stock.stock_location_stock', raise_if_not_found=False,
                )
            )
            for detail in stage.material_line_ids:
                staged_moves = detail._valid_issue_moves(
                    stage._material_handover_location(),
                )
                staged_qty = sum(detail._move_qty_in_product_uom(move) for move in staged_moves)
                missing_qty = max(detail.requested_qty - staged_qty, 0.0)
                key = (
                    production.company_id.id,
                    source_location.id if source_location else False,
                    detail.product_id.id,
                )
                required_by_key[key] += missing_qty
                context_by_key[key] = (
                    production, source_location, detail.product_id,
                )
        shortages = []
        for key, requested_qty in required_by_key.items():
            production, source_location, product = context_by_key[key]
            available_qty = (
                production._stage_location_product_qty(
                    source_location, product, production.company_id,
                ) if source_location else 0.0
            )
            if float_compare(available_qty, requested_qty, precision_digits=3) < 0:
                shortages.append('%s: %s / %s' % (
                    product.display_name,
                    production._format_dimension_value(available_qty),
                    production._format_dimension_value(requested_qty),
                ))
        if shortages:
            raise UserError(_(
                'الرصيد الحالي لا يكفي لصرف كل المراحل في معاملة واحدة. '
                'لم تتحرك أي خامات:\n%s'
            ) % '\n'.join(shortages))

    def action_issue(self):
        """Approve and issue every selected stage atomically, without starting it."""
        any_tailoring_shortage = False
        for release in self:
            release_has_tailoring_shortage = False
            release._lock()
            release._check_can_approve()
            release._lock_productions()
            if release.state != 'pending':
                raise UserError(_('يمكن صرف الأذونات المنتظرة فقط.'))
            if not release.stage_line_ids:
                raise UserError(_('الإذن لا يحتوي على أي مرحلة.'))
            for stage in release.stage_line_ids:
                if stage.state != 'pending':
                    raise UserError(_('كل مراحل الإذن يجب أن تكون بانتظار الصرف.'))
                stage._validate_current_snapshot(relink_sources=True)
                stage._ensure_not_started_or_consumed()
            # This check is intentionally across every stage before the first
            # move.  Any later exception also rolls the whole transaction back.
            release._preflight_issue_availability()
            for stage in release.stage_line_ids.sorted(
                lambda item: (item.production_id.id, item.sequence, item.id)
            ):
                stage._issue_materials()
            release.stage_line_ids.mapped(
                'material_line_ids'
            ).invalidate_recordset(['issued_qty', 'move_ids'])
            now = fields.Datetime.now()
            release.sudo().write({
                'state': 'issued',
                'issued_by_id': self.env.user.id,
                'issued_at': now,
            })
            for stage in release.stage_line_ids:
                receipt_required = any(
                    float_compare(
                        detail.issued_qty, 0.0, precision_digits=3,
                    ) > 0
                    for detail in stage.material_line_ids
                )
                has_issue_shortage = bool(
                    stage.stage_code == 'tailoring'
                    and any(
                        float_compare(
                            detail.issued_qty,
                            detail.requested_qty,
                            precision_digits=3,
                        ) < 0
                        for detail in stage.material_line_ids
                    )
                )
                auto_confirm_shortage = bool(
                    has_issue_shortage and not receipt_required
                )
                any_tailoring_shortage = (
                    any_tailoring_shortage or has_issue_shortage
                )
                release_has_tailoring_shortage = (
                    release_has_tailoring_shortage or has_issue_shortage
                )
                stage.sudo().write({
                    'state': 'issued',
                    'issued_by_id': self.env.user.id,
                    'issued_at': now,
                    'receipt_state': (
                        'partial' if auto_confirm_shortage else 'waiting'
                    ),
                    # Preserve the established consolidated-release contract:
                    # even a stage without recipe rows remains in the batch
                    # production-receipt screen.  Only tailoring with a real
                    # requested shortage and zero issued stock is confirmed
                    # automatically so it can start immediately.
                    'receipt_confirmed': auto_confirm_shortage,
                })
            release._mark_activities_done()
            if release_has_tailoring_shortage:
                message = _(
                    '⚠️ تم صرف المتاح من خامات الإذن %(release)s. '
                    'مرحلة التفصيل يمكن أن تبدأ، وسيظهر العجز حسب المنتج '
                    'والخامة داخل أمر المرحلة.'
                ) % {'release': release.name}
            else:
                message = _(
                    '✅ تم تسليم خامات الإذن %(release)s من المخزن إلى عهدة '
                    'انتظار استلام الإنتاج للمراحل: %(stages)s. '
                    'لن تدخل أي صالة ولن تبدأ أي مرحلة قبل تأكيد مسؤول الإنتاج.'
                ) % {
                    'release': release.name,
                    'stages': release.stage_summary,
                }
            release._send_result_notification(
                (
                    _('مواد ناقصة في التفصيل')
                    if release_has_tailoring_shortage else
                    _('الخامات جاهزة لاستلام الإنتاج')
                ),
                message,
                'warning' if release_has_tailoring_shortage else 'success',
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': (
                    _('مواد ناقصة في التفصيل')
                    if any_tailoring_shortage else
                    _('تم التسليم من المخزن')
                ),
                'message': (
                    _(
                        'تم صرف المتاح فقط للتفصيل. يمكن بدء المرحلة، '
                        'وسيظل تنبيه العجز ظاهرًا.'
                    )
                    if any_tailoring_shortage else
                    _(
                        'الخامات في عهدة الانتظار الآن، وبانتظار تأكيد استلام الإنتاج.'
                    )
                ),
                'type': 'warning' if any_tailoring_shortage else 'success',
                'sticky': any_tailoring_shortage,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_reject(self):
        for release in self:
            release._lock()
            release._check_can_approve()
            if release.state != 'pending':
                raise UserError(_('يمكن رفض الأذونات المنتظرة فقط.'))
            now = fields.Datetime.now()
            release.sudo().write({
                'state': 'rejected',
                'rejected_by_id': self.env.user.id,
                'rejected_at': now,
            })
            release.stage_line_ids.sudo().write({'state': 'rejected'})
            release._mark_activities_done()
            message = _('❌ تم رفض إذن صرف الخامات %s. لم تتحرك أي خامات.') % release.name
            if release.rejection_reason:
                message += ' ' + _('السبب: %s') % release.rejection_reason
            release._send_result_notification(
                _('تم رفض إذن صرف الخامات'), message, 'danger',
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_cancel(self):
        for release in self:
            release._lock()
            release._check_can_approve()
            if release.state != 'pending':
                raise UserError(_('يمكن إلغاء الأذونات المنتظرة فقط.'))
            now = fields.Datetime.now()
            release.sudo().write({
                'state': 'cancelled',
                'cancelled_by_id': self.env.user.id,
                'cancelled_at': now,
            })
            release.stage_line_ids.sudo().write({'state': 'cancelled'})
            release._mark_activities_done()
            release._send_result_notification(
                _('تم إلغاء إذن صرف الخامات'),
                _('تم إلغاء الإذن %s. لم تتحرك أي خامات.') % release.name,
                'warning',
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _return_stages(self, stages):
        self.ensure_one()
        stages = stages.filtered(lambda stage: stage.release_id == self)
        if not stages:
            raise UserError(_('لا توجد مراحل مختارة للإرجاع.'))
        self._lock()
        self._check_can_approve()
        self._lock_productions()
        if self.state != 'issued':
            raise UserError(_('يمكن إرجاع إذن تم صرفه فقط.'))
        for stage in stages:
            if stage.state != 'issued' or stage.receipt_confirmed:
                raise UserError(_(
                    'يمكن إرجاع المرحلة قبل تأكيد استلام الإنتاج فقط.'
                ))
            stage._validate_current_snapshot(relink_sources=True)
            stage._ensure_not_started_or_consumed()
            stage._validate_return_stock()
        for stage in stages.sorted(
            lambda item: (item.production_id.id, item.sequence, item.id)
        ):
            stage._return_materials()
        vals = {}
        if self.stage_line_ids and all(
            stage.state == 'returned' for stage in self.stage_line_ids
        ):
            vals.update({
                'state': 'returned',
                'returned_by_id': self.env.user.id,
                'returned_at': fields.Datetime.now(),
            })
        if vals:
            self.sudo().write(vals)
        labels = dict(FURNITURE_STAGE_SELECTION)
        message = _('↩️ تم إرجاع خامات الإذن %(release)s للمراحل: %(stages)s.') % {
            'release': self.name,
            'stages': '، '.join(labels.get(stage.stage_code, stage.stage_code) for stage in stages),
        }
        self._send_result_notification(_('تم إرجاع خامات المراحل'), message, 'success')
        return True

    def action_return(self):
        self.ensure_one()
        self._return_stages(self.stage_line_ids.filtered(
            lambda stage: stage.state == 'issued' and not stage.receipt_confirmed
        ))
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class FurnitureMrpAdvanceMaterialReleaseStage(models.Model):
    _name = 'furniture.mrp.advance.material.release.stage'
    _description = 'مرحلة داخل إذن الصرف المبكر'
    _order = 'sequence, id'

    release_id = fields.Many2one(
        'furniture.mrp.advance.material.release', string='إذن الصرف',
        required=True, ondelete='cascade', index=True, copy=False,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        required=True, readonly=True, ondelete='cascade',
        index=True, copy=False,
    )
    sequence = fields.Integer(default=10, required=True)
    stage_code = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION, string='المرحلة',
        required=True, readonly=True, index=True,
    )
    destination_location_id = fields.Many2one(
        'stock.location', string='صالة المرحلة',
        required=True, readonly=True,
    )
    handover_location_id = fields.Many2one(
        'stock.location', string='عهدة انتظار استلام الإنتاج',
        readonly=True,
        default=lambda self: self.env.ref(
            'furniture_mrp.location_material_handover',
            raise_if_not_found=False,
        ),
    )
    state = fields.Selection(
        [
            ('pending', 'بانتظار الصرف'),
            ('issued', 'تم الصرف'),
            ('started', 'بدأ استخدام الخامات'),
            ('rejected', 'مرفوض'),
            ('returned', 'تم الإرجاع'),
            ('cancelled', 'ملغي'),
        ],
        string='الحالة', required=True, default='pending',
        readonly=True, index=True, copy=False,
    )
    material_line_ids = fields.One2many(
        'furniture.mrp.advance.material.release.line', 'release_stage_id',
        string='الخامات', readonly=True, copy=False,
    )
    source_material_line_ids = fields.Many2many(
        'furniture.mrp.material.line',
        'furn_adv_rel_stage_material_rel',
        'release_stage_id', 'material_line_id',
        string='سطور خامات أمر الإنتاج', readonly=True, copy=False,
    )
    issued_by_id = fields.Many2one(
        'res.users', string='صرف بواسطة', readonly=True, copy=False,
    )
    issued_at = fields.Datetime(string='وقت الصرف', readonly=True, copy=False)
    receipt_state = fields.Selection(
        RECEIPT_STATE_SELECTION,
        string='حالة استلام الإنتاج', required=True,
        default='not_issued', readonly=True, index=True, copy=False,
    )
    receipt_confirmed = fields.Boolean(
        string='أكد الإنتاج الاستلام', readonly=True, copy=False,
    )
    legacy_direct_receipt = fields.Boolean(
        string='استلام مباشر قديم', readonly=True, copy=False,
    )
    received_by_id = fields.Many2one(
        'res.users', string='استلم بواسطة', readonly=True, copy=False,
    )
    received_at = fields.Datetime(
        string='وقت الاستلام', readonly=True, copy=False,
    )
    receipt_note = fields.Text(
        string='ملاحظة فرق الاستلام', readonly=True, copy=False,
    )
    returned_by_id = fields.Many2one(
        'res.users', string='أرجع بواسطة', readonly=True, copy=False,
    )
    returned_at = fields.Datetime(string='وقت الإرجاع', readonly=True, copy=False)
    requested_qty = fields.Float(
        string='إجمالي الكمية المطلوبة', compute='_compute_totals',
        digits=(16, 3),
    )
    shortage_qty = fields.Float(
        string='إجمالي العجز وقت الطلب', compute='_compute_totals',
        digits=(16, 3),
    )
    issued_qty = fields.Float(
        string='إجمالي المصروف', compute='_compute_totals', digits=(16, 3),
    )
    received_qty = fields.Float(
        string='إجمالي المستلم', compute='_compute_totals', digits=(16, 3),
    )
    receipt_difference_qty = fields.Float(
        string='إجمالي فرق الاستلام', compute='_compute_totals', digits=(16, 3),
    )

    _sql_constraints = [
        (
            'advance_release_production_stage_unique',
            'unique(release_id, production_id, stage_code)',
            'لا يمكن تكرار نفس المرحلة لنفس أمر الإنتاج داخل إذن الصرف.',
        ),
    ]

    @api.constrains('release_id', 'production_id')
    def _check_release_company(self):
        for stage in self:
            if (
                stage.release_id.company_id
                and stage.production_id.company_id
                != stage.release_id.company_id
            ):
                raise UserError(_(
                    'لا يمكن جمع مراحل أوامر إنتاج من شركات مختلفة في إذن واحد.'
                ))

    @api.depends(
        'material_line_ids.requested_qty',
        'material_line_ids.shortage_qty',
        'material_line_ids.issued_qty',
        'material_line_ids.received_qty',
        'material_line_ids.receipt_difference_qty',
    )
    def _compute_totals(self):
        for stage in self:
            stage.requested_qty = sum(stage.material_line_ids.mapped('requested_qty'))
            stage.shortage_qty = sum(stage.material_line_ids.mapped('shortage_qty'))
            stage.issued_qty = sum(stage.material_line_ids.mapped('issued_qty'))
            stage.received_qty = sum(stage.material_line_ids.mapped('received_qty'))
            stage.receipt_difference_qty = sum(
                stage.material_line_ids.mapped('receipt_difference_qty')
            )

    def _material_handover_location(self):
        self.ensure_one()
        return self.handover_location_id or self.env.ref(
            'furniture_mrp.location_material_handover'
        )

    def _check_can_receive(self):
        """Only production leadership may accept the physical handover."""
        if self.env.is_superuser():
            return True
        is_manager = self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        )
        is_supervisor = self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor'
        )
        if not (is_manager or is_supervisor):
            raise AccessError(_(
                'تأكيد استلام الخامات متاح لمدير الإنتاج أو مشرف المرحلة فقط.'
            ))
        for release_stage in self:
            production = release_stage.production_id
            if (
                not production
                or production.company_id.id not in self.env.companies.ids
            ):
                raise AccessError(_(
                    'إذن الخامات لا يتبع إحدى الشركات المفعلة لحسابك.'
                ))
            if is_supervisor and not is_manager:
                production._stage_dashboard_check_stage_access(
                    release_stage.stage_code
                )
        return True

    def _current_buckets(self):
        self.ensure_one()
        return self.env[
            'furniture.mrp.advance.material.release'
        ]._stage_material_buckets(self.production_id, self.stage_code)

    def _validate_current_snapshot(self, relink_sources=False):
        self.ensure_one()
        current_buckets = self._current_buckets()
        snapshot_by_key = {
            (line.product_id.id, line.product_uom_id.id): line
            for line in self.material_line_ids
        }
        if set(current_buckets) != set(snapshot_by_key):
            raise UserError(_(
                'خامات مرحلة %(stage)s تغيرت بعد إنشاء إذن الصرف %(release)s. '
                'ارفض/ألغِ الإذن وأنشئ إذنًا جديدًا بعد مراجعة التعديل.'
            ) % {
                'stage': dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code),
                'release': self.release_id.name,
            })
        all_current_lines = self.env['furniture.mrp.material.line']
        for key, bucket in current_buckets.items():
            detail = snapshot_by_key[key]
            if float_compare(
                bucket['requested_qty'], detail.requested_qty,
                precision_digits=3,
            ) != 0:
                raise UserError(_(
                    'كمية الخامة %(product)s في مرحلة %(stage)s تغيرت من %(old)s إلى %(new)s '
                    'بعد إنشاء الإذن %(release)s.'
                ) % {
                    'product': detail.product_id.display_name,
                    'stage': dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code),
                    'old': self.production_id._format_dimension_value(detail.requested_qty),
                    'new': self.production_id._format_dimension_value(bucket['requested_qty']),
                    'release': self.release_id.name,
                })
            all_current_lines |= bucket['material_lines']
            if relink_sources:
                detail.sudo().write({
                    'source_material_line_ids': [(6, 0, bucket['material_lines'].ids)],
                })
        if relink_sources:
            self.sudo().write({
                'source_material_line_ids': [(6, 0, all_current_lines.ids)],
            })
        current_destination = self.production_id._stage_work_location(self.stage_code)
        if current_destination != self.destination_location_id:
            raise UserError(_(
                'صالة مرحلة %s تغيرت بعد إنشاء إذن الصرف. أنشئ إذنًا جديدًا.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code))
        return True

    def _ensure_not_started_or_consumed(self):
        self.ensure_one()
        # Warehouse users are allowed to approve/issue an assigned release but
        # deliberately have no ACL on the operational stage models.  This is a
        # server-side consistency check (not stage data exposed to the user),
        # so cross that ACL boundary only for the stage record already pinned
        # by this company-scoped release line.
        stage_order = self.production_id.sudo()._stage_order_record(
            self.stage_code,
        )
        stage_progress_lines = self.env['furniture.mrp.production.line']
        if stage_order:
            stage_progress_lines |= stage_order.first_stage_production_line_ids
            for field_name in (
                'active_production_line_ids_data',
                'quality_production_line_ids_data',
                'completed_production_line_ids_data',
            ):
                stage_progress_lines |= stage_order._get_stage_line_ids_data(field_name)
        if stage_order and (
            stage_order.state != 'pending'
            or stage_order.date_start
            or stage_progress_lines
        ):
            raise UserError(_(
                'لا يمكن صرف أو إرجاع خامات مرحلة %s بعد بدء المرحلة.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code))
        consumed_lines = self.source_material_line_ids.filtered(
            lambda line: self.production_id._material_line_already_consumed(line)
        )
        if consumed_lines:
            raise UserError(_(
                'لا يمكن إرجاع خامات مرحلة %s لأن جزءًا منها دخل الإنتاج بالفعل.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code))
        return True

    def _material_issue_available_qty(self, source_location, product):
        self.ensure_one()
        return self.production_id._stage_location_product_qty(
            source_location,
            product,
            self.production_id.company_id,
        )

    def _issue_materials(self):
        self.ensure_one()
        self._validate_current_snapshot(relink_sources=True)
        source_location = (
            self.production_id.location_src_id
            or self.env.ref('stock.stock_location_stock')
        )
        handover_location = self._material_handover_location()
        allow_shortage = self.stage_code == 'tailoring'
        remaining_available_by_product = {}
        if allow_shortage:
            for product in self.material_line_ids.mapped('product_id'):
                remaining_available_by_product[product.id] = max(
                    self._material_issue_available_qty(
                        source_location, product,
                    ),
                    0.0,
                )
        created_by_detail = defaultdict(lambda: self.env['stock.move'])
        specs = []
        for detail in self.material_line_ids:
            valid_moves = detail._stored_valid_issue_moves(handover_location)
            issued_qty = sum(
                detail._move_qty_in_product_uom(move) for move in valid_moves
            )
            missing_qty = max(detail.requested_qty - issued_qty, 0.0)
            if float_compare(missing_qty, 0.0, precision_digits=3) > 0:
                quantity_to_issue = missing_qty
                if allow_shortage:
                    available_qty = remaining_available_by_product.get(
                        detail.product_id.id, 0.0,
                    )
                    quantity_to_issue = min(missing_qty, available_qty)
                    remaining_available_by_product[detail.product_id.id] = max(
                        available_qty - quantity_to_issue,
                        0.0,
                    )
                if float_compare(
                    quantity_to_issue, 0.0, precision_digits=3,
                ) <= 0:
                    continue
                move_quantity, move_uom = (
                    detail._prepare_stock_move_quantity(quantity_to_issue)
                )
                specs.append({
                    'source_location': source_location,
                    'dest_location': handover_location,
                    'label': _('تسليم مخزني بانتظار استلام الإنتاج - %s')
                             % dict(FURNITURE_STAGE_SELECTION).get(
                                 self.stage_code, self.stage_code,
                    ),
                    'product': detail.product_id,
                    'quantity': move_quantity,
                    'uom': move_uom,
                    'source_production_lines': (
                        detail.source_material_line_ids.mapped(
                            'production_line_id'
                        ).exists()
                    ),
                    'detail': detail,
                })
        for spec, move in self.production_id._create_internal_moves_batch(specs):
            created_by_detail[spec['detail'].id] |= move

        for detail in self.material_line_ids:
            valid_moves = (
                detail._stored_valid_issue_moves(handover_location)
                | created_by_detail[detail.id]
            )
            issued_qty = sum(
                detail._move_qty_in_product_uom(move) for move in valid_moves
            )
            if (
                not allow_shortage
                and float_compare(
                    issued_qty,
                    detail.requested_qty,
                    precision_digits=3,
                ) < 0
            ):
                raise UserError(_(
                    'لم يتم ربط حركة صرف كاملة بالخامة %(product)s في مرحلة %(stage)s.'
                ) % {
                    'product': detail.product_id.display_name,
                    'stage': dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code),
                })
            primary_move = valid_moves.sorted(
                lambda move: -detail._move_qty_in_product_uom(move)
            )[:1]
            source_production_lines = (
                valid_moves.mapped('furniture_source_production_line_ids')
                | detail.source_material_line_ids.mapped('production_line_id')
            ).exists()
            if source_production_lines:
                valid_moves.sudo().write({
                    'furniture_source_production_line_ids': [
                        (6, 0, source_production_lines.ids),
                    ],
                })
            if detail.source_material_line_ids:
                detail.source_material_line_ids.sudo().write({
                    'move_id': primary_move.id if primary_move else False,
                    'warehouse_receipt_confirmed': False,
                    'warehouse_received_qty': 0.0,
                    'warehouse_receipt_stage_id': False,
                })
            detail.sudo().write({
                'issued_qty': issued_qty,
                'received_qty': 0.0,
                'receipt_difference_qty': 0.0,
                'move_id': primary_move.id if primary_move else False,
                'move_ids': [(6, 0, valid_moves.ids)],
                'receipt_move_ids': [(5, 0, 0)],
            })
        return True

    def _stage_received_materials_for_execution(self):
        """Move the acknowledged quantity from handover to this stage hall.

        This is called only from the actual execution path.  Existing legacy
        receipt moves are reused, so retries cannot duplicate stock or costs.
        """
        self.ensure_one()
        if self.legacy_direct_receipt:
            for detail in self.material_line_ids:
                issue_moves = detail._stored_valid_issue_moves(
                    self.destination_location_id,
                )
                primary = issue_moves.sorted(
                    lambda move: -detail._move_qty_in_product_uom(move)
                )[:1]
                detail._allocate_received_qty_to_source_lines(primary)
            return True
        if not self.receipt_confirmed:
            raise UserError(_(
                'أكد «استلام من المخزن» لخامات مرحلة %s أولًا.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(
                self.stage_code, self.stage_code,
            ))

        handover_location = self._material_handover_location()
        created_by_detail = defaultdict(lambda: self.env['stock.move'])
        specs = []
        existing_by_detail = {}
        for detail in self.material_line_ids:
            existing = detail._stored_valid_receipt_moves(
                self.destination_location_id,
            ).filtered(lambda move: move.location_id == handover_location)
            existing_by_detail[detail.id] = existing
            already_staged = sum(
                detail._move_qty_in_product_uom(move) for move in existing
            )
            missing_qty = max(detail.received_qty - already_staged, 0.0)
            if float_compare(missing_qty, 0.0, precision_digits=3) <= 0:
                continue
            move_quantity, move_uom = (
                detail._prepare_stock_move_quantity(missing_qty)
            )
            specs.append({
                'source_location': handover_location,
                'dest_location': self.destination_location_id,
                'label': _('استلام إنتاج فعلي عند بدء %s') % dict(
                    FURNITURE_STAGE_SELECTION,
                ).get(self.stage_code, self.stage_code),
                'product': detail.product_id,
                'quantity': move_quantity,
                'uom': move_uom,
                'source_production_lines': (
                    detail.source_material_line_ids.mapped(
                        'production_line_id'
                    ).exists()
                ),
                'detail': detail,
            })
        for spec, move in self.production_id._create_internal_moves_batch(specs):
            created_by_detail[spec['detail'].id] |= move

        for detail in self.material_line_ids:
            receipt_moves = (
                existing_by_detail.get(detail.id, self.env['stock.move'])
                | created_by_detail[detail.id]
            )
            detail.sudo().write({
                'receipt_move_ids': [(6, 0, receipt_moves.ids)],
            })
            primary_receipt = receipt_moves.sorted(
                lambda move: -detail._move_qty_in_product_uom(move)
            )[:1]
            detail._allocate_received_qty_to_source_lines(primary_receipt)
        return True

    def _relink_issued_moves(self):
        self.ensure_one()
        if self.state not in ('issued', 'started'):
            return True
        self._validate_current_snapshot(relink_sources=True)
        for detail in self.material_line_ids:
            issue_destination = (
                self.destination_location_id
                if self.legacy_direct_receipt
                else self._material_handover_location()
            )
            moves = detail._stored_valid_issue_moves(issue_destination)
            total_qty = sum(
                detail._move_qty_in_product_uom(move) for move in moves
            )
            allow_shortage = self.stage_code == 'tailoring'
            if (
                not allow_shortage
                and float_compare(
                    total_qty,
                    detail.requested_qty,
                    precision_digits=3,
                ) < 0
            ):
                raise UserError(_(
                    'حركات الصرف المحفوظة للخامة %s لم تعد تغطي كمية الإذن.'
                ) % detail.product_id.display_name)
            primary_move = moves.sorted(
                lambda move: -detail._move_qty_in_product_uom(move)
            )[:1]
            if (
                detail.source_material_line_ids
                and not primary_move
                and not allow_shortage
            ):
                raise UserError(_('حركة صرف الخامة %s غير موجودة.') % detail.product_id.display_name)
            if primary_move:
                lines_to_relink = detail.source_material_line_ids.filtered(
                    lambda line: (
                        not line.move_id
                        or line.move_id in moves
                    )
                )
                if lines_to_relink:
                    lines_to_relink.sudo().write({'move_id': primary_move.id})
                source_production_lines = (
                    moves.mapped('furniture_source_production_line_ids')
                    | detail.source_material_line_ids.mapped('production_line_id')
                ).exists()
                if source_production_lines:
                    moves.sudo().write({
                        'furniture_source_production_line_ids': [
                            (6, 0, source_production_lines.ids),
                        ],
                    })
                detail.sudo().write({'move_id': primary_move.id})

            if self.receipt_confirmed:
                receipt_moves = (
                    moves if self.legacy_direct_receipt
                    else detail._stored_valid_receipt_moves(
                        self.destination_location_id,
                    )
                )
                received_qty = (
                    detail.received_qty
                    if not self.legacy_direct_receipt
                    else (detail.received_qty or detail.issued_qty)
                )
                actual_receipt_qty = sum(
                    detail._move_qty_in_product_uom(move)
                    for move in receipt_moves
                )
                # Receipt acknowledgement intentionally creates no stock move.
                # Before real execution keep the issue move linked only as a
                # custody trace; the execution hook below stages it exactly
                # once and then this validation becomes strict.
                if (
                    not self.legacy_direct_receipt
                    and not receipt_moves
                    and self.state == 'issued'
                ):
                    continue
                if float_compare(
                    actual_receipt_qty, received_qty, precision_digits=3,
                ) < 0:
                    raise UserError(_(
                        'حركات استلام الإنتاج للخامة %s لا تغطي الكمية المستلمة.'
                    ) % detail.product_id.display_name)
                primary_receipt = receipt_moves.sorted(
                    lambda move: -detail._move_qty_in_product_uom(move)
                )[:1]
                detail._allocate_received_qty_to_source_lines(primary_receipt)
            elif detail.source_material_line_ids:
                # A product-first supervisor may have received only one item
                # from an aggregate stage release.  Preserve those exact recipe
                # rows while the sibling products legitimately remain in the
                # handover location waiting for their own receipt tap.
                unreceived_source_lines = detail.source_material_line_ids.filtered(
                    lambda line: not (
                        line.warehouse_receipt_confirmed
                        and line.warehouse_receipt_stage_id == self
                    )
                )
                unreceived_source_lines.sudo().write({
                    'move_id': primary_move.id if primary_move else False,
                    'warehouse_receipt_confirmed': False,
                    'warehouse_received_qty': 0.0,
                    'warehouse_receipt_stage_id': False,
                })
        return True

    def _prepare_for_stage_execution(self, mark_started=True):
        """Serialize actual stage execution against a possible stock return."""
        self.ensure_one()
        self.release_id._lock()
        self.invalidate_recordset(['state'])
        self.release_id.invalidate_recordset(['state'])
        if self.state == 'pending':
            raise UserError(_(
                'إذن صرف خامات مرحلة %s ما زال بانتظار أمين المخزن.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code))
        if self.state not in ('issued', 'started'):
            raise UserError(_(
                'إذن صرف خامات مرحلة %s لم يعد صالحًا للبدء. افتح المرحلة واطلب إذنًا جديدًا.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(self.stage_code, self.stage_code))
        if not self.receipt_confirmed:
            raise UserError(_(
                'خامات مرحلة %s ما زالت في عهدة التسليم. '
                'أكد «استلام من المخزن» أولًا قبل بدء التصنيع.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(
                self.stage_code, self.stage_code,
            ))
        self._validate_current_snapshot(relink_sources=True)
        self._stage_received_materials_for_execution()
        self._relink_issued_moves()
        if mark_started and self.state == 'issued':
            self.sudo().write({'state': 'started'})
        return True

    def _validate_return_stock(self):
        self.ensure_one()
        required_by_product = defaultdict(float)
        for detail in self.material_line_ids:
            required_by_product[detail.product_id.id] += detail.issued_qty
        for product_id, required_qty in required_by_product.items():
            product = self.env['product.product'].browse(product_id)
            available_qty = self.production_id._stage_location_product_qty(
                self._material_handover_location(),
                product,
                self.production_id.company_id,
            )
            if float_compare(available_qty, required_qty, precision_digits=3) < 0:
                raise UserError(_(
                    'لا يمكن إرجاع %(product)s من %(location)s؛ المتاح %(available)s '
                    'والمطلوب %(required)s.'
                ) % {
                    'product': product.display_name,
                    'location': self._material_handover_location().display_name,
                    'available': self.production_id._format_dimension_value(available_qty),
                    'required': self.production_id._format_dimension_value(required_qty),
                })
        return True

    def _return_materials(self):
        self.ensure_one()
        source_location = (
            self.production_id.location_src_id
            or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        )
        specs = []
        for detail in self.material_line_ids.filtered(
            lambda line: float_compare(line.issued_qty, 0.0, precision_digits=3) > 0
        ):
            remaining_qty = detail.issued_qty
            issue_moves = detail._stored_valid_issue_moves(
                self._material_handover_location(),
            ).sorted('id')
            for issue_move in issue_moves:
                if float_compare(remaining_qty, 0.0, precision_digits=3) <= 0:
                    break
                move_qty = detail._move_qty_in_product_uom(issue_move)
                return_qty = min(remaining_qty, move_qty)
                if float_compare(return_qty, 0.0, precision_digits=3) <= 0:
                    continue
                move_quantity, move_uom = (
                    detail._prepare_stock_move_quantity(return_qty)
                )
                specs.append({
                    'source_location': self._material_handover_location(),
                    'dest_location': source_location,
                    'label': _('إرجاع صرف مبكر - %s') % self.release_id.name,
                    'product': detail.product_id,
                    'quantity': move_quantity,
                    'uom': move_uom,
                    'detail': detail,
                    'origin_returned_move': issue_move,
                })
                remaining_qty -= return_qty
            if float_compare(remaining_qty, 0.0, precision_digits=3) > 0:
                raise UserError(_(
                    'تعذر تحديد حركات الصرف الأصلية لكمية %s المراد إرجاعها.'
                ) % detail.product_id.display_name)
        returned_by_detail = defaultdict(lambda: self.env['stock.move'])
        for spec, move in self.production_id._create_internal_moves_batch(specs):
            returned_by_detail[spec['detail'].id] |= move
        for detail in self.material_line_ids:
            current_lines = detail.source_material_line_ids.filtered(
                lambda line: line.move_id in detail.move_ids or line.move_id == detail.move_id
            )
            if current_lines:
                current_lines.sudo().write({
                    'move_id': False,
                    'warehouse_receipt_confirmed': False,
                    'warehouse_received_qty': 0.0,
                    'warehouse_receipt_stage_id': False,
                })
            detail.sudo().write({
                'return_move_ids': [(6, 0, returned_by_detail[detail.id].ids)],
                'received_qty': 0.0,
                'receipt_difference_qty': 0.0,
                'receipt_move_ids': [(5, 0, 0)],
            })
        self.sudo().write({
            'state': 'returned',
            'receipt_state': 'not_issued',
            'receipt_confirmed': False,
            'returned_by_id': self.env.user.id,
            'returned_at': fields.Datetime.now(),
        })
        return True

    def action_return(self):
        self.ensure_one()
        self.release_id._return_stages(self)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_open_receipt_wizard(self):
        self.ensure_one()
        self._check_can_receive()
        if self.state != 'issued':
            raise UserError(_(
                'يمكن استلام الخامات بعد صرف أمين المخزن وقبل بدء المرحلة فقط.'
            ))
        if self.receipt_confirmed:
            raise UserError(_('تم تأكيد استلام خامات هذه المرحلة بالفعل.'))
        wizard = self.env[
            'furniture.mrp.advance.material.receipt.wizard'
        ].create({
            'release_stage_id': self.id,
            'line_ids': [
                (0, 0, {
                    'release_line_id': line.id,
                    'issued_qty': line.issued_qty,
                    'received_qty': line.issued_qty,
                })
                for line in self.material_line_ids
            ],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_receipt_wizard_form'
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

    def _confirm_production_receipt(
        self, received_by_line, note=False, receiver_user=False,
    ):
        """Record custody acceptance; defer the hall move until stage start."""
        self.ensure_one()
        self._check_can_receive()
        if receiver_user:
            if not self.env.is_superuser():
                raise AccessError(_(
                    'لا يمكن تغيير هوية مستلم خامات المرحلة يدويًا.'
                ))
            receiver_user = self.env['res.users'].sudo().browse(
                receiver_user.id,
            ).exists().ensure_one()
        else:
            receiver_user = self.env.user
        self.release_id._lock()
        self.release_id._lock_productions()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_advance_material_release_stage '
            'WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset([
            'state', 'receipt_confirmed', 'receipt_state',
        ])
        if self.state != 'issued':
            raise UserError(_(
                'لا يمكن تأكيد الاستلام بعد بدء المرحلة أو إرجاع الإذن.'
            ))
        if self.receipt_confirmed:
            raise UserError(_('تم تأكيد استلام خامات هذه المرحلة بالفعل.'))
        self._validate_current_snapshot(relink_sources=True)

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

        handover_location = self._material_handover_location()
        any_positive = False
        existing_receipt_moves_by_detail = {}
        for detail in self.material_line_ids:
            received_qty = received_by_line[detail.id]
            if float_compare(received_qty, 0.0, precision_digits=3) < 0:
                raise UserError(_(
                    'الكمية المستلمة للخامة %s لا يمكن أن تكون سالبة.'
                ) % detail.product_id.display_name)
            if float_compare(
                received_qty, detail.issued_qty, precision_digits=3,
            ) > 0:
                raise UserError(_(
                    'الكمية المستلمة للخامة %(product)s أكبر من المصروف '
                    '(%(issued)s).'
                ) % {
                    'product': detail.product_id.display_name,
                    'issued': self.production_id._format_dimension_value(
                        detail.issued_qty,
                    ),
                })
            issue_moves = detail._stored_valid_issue_moves(handover_location)
            issue_qty = sum(
                detail._move_qty_in_product_uom(move) for move in issue_moves
            )
            if float_compare(
                issue_qty, detail.issued_qty, precision_digits=3,
            ) < 0:
                raise UserError(_(
                    'حركات التسليم المخزني للخامة %s غير مكتملة.'
                ) % detail.product_id.display_name)
            existing_receipt_moves = detail._stored_valid_receipt_moves(
                self.destination_location_id,
            ).filtered(lambda move: move.location_id == handover_location)
            existing_receipt_moves_by_detail[detail.id] = existing_receipt_moves
            existing_receipt_qty = sum(
                detail._move_qty_in_product_uom(move)
                for move in existing_receipt_moves
            )
            if float_compare(
                existing_receipt_qty, received_qty, precision_digits=3,
            ) > 0:
                raise UserError(_(
                    'يوجد استلام مخزني قديم منفذ بالفعل للخامة %(product)s '
                    'بكمية %(quantity)s، لذلك لا يمكن تسجيل كمية أقل منه.'
                ) % {
                    'product': detail.product_id.display_name,
                    'quantity': self.production_id._format_dimension_value(
                        existing_receipt_qty,
                    ),
                })
            if float_compare(received_qty, 0.0, precision_digits=3) > 0:
                any_positive = True
        if self.material_line_ids and not any_positive:
            raise UserError(_(
                'سجل كمية مستلمة فعلية في خامة واحدة على الأقل. '
                'لو لم تستلم شيئًا أغلق الشاشة واترك المرحلة بانتظار الاستلام.'
            ))

        for detail in self.material_line_ids:
            received_qty = received_by_line[detail.id]
            difference_qty = max(detail.issued_qty - received_qty, 0.0)
            detail.sudo().write({
                'received_qty': received_qty,
                'receipt_difference_qty': difference_qty,
                'receipt_move_ids': [(6, 0, existing_receipt_moves_by_detail[
                    detail.id
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
            'received_by_id': receiver_user.id,
            'received_at': fields.Datetime.now(),
            'receipt_note': note or False,
        })

        labels = dict(FURNITURE_STAGE_SELECTION)
        detail_messages = [
            _('%(product)s: المصروف %(issued)s، المستلم %(received)s، الفرق %(difference)s')
            % {
                'product': detail.product_id.display_name,
                'issued': self.production_id._format_dimension_value(
                    detail.issued_qty,
                ),
                'received': self.production_id._format_dimension_value(
                    detail.received_qty,
                ),
                'difference': self.production_id._format_dimension_value(
                    detail.receipt_difference_qty,
                ),
            }
            for detail in self.material_line_ids
        ]
        message = _(
            '📥 أكد %(user)s استلام خامات مرحلة %(stage)s من المخزن.'
        ) % {
            'user': receiver_user.display_name,
            'stage': labels.get(self.stage_code, self.stage_code),
        }
        if detail_messages:
            message += '<br/>' + '<br/>'.join(detail_messages)
        if note:
            message += '<br/>' + _('ملاحظة الاستلام: %s') % note
        self.production_id.sudo().message_post(body=message)

        # The authorized stage receiver cannot browse the private warehouse
        # header. Resolve only its notification recipient after all receipt
        # permission, quantity and stock validations above have succeeded.
        storekeeper = self.release_id.sudo().assigned_to_id.filtered('active')
        if storekeeper:
            self.env['furniture.mrp.store.request']._send_bus_notification(
                storekeeper,
                _('تم تسجيل استلام الإنتاج'),
                message,
                notification_type='warning' if has_difference else 'success',
                action_model=self.release_id._name,
                action_res_id=self.release_id.id,
                action_name=_('فتح الإذن'),
                refresh_model=self.release_id._name,
                refresh_res_id=self.release_id.id,
                play_sound=has_difference,
            )
        return receipt_state


class FurnitureMrpAdvanceMaterialReleaseLine(models.Model):
    _name = 'furniture.mrp.advance.material.release.line'
    _description = 'خامة داخل مرحلة بإذن الصرف المبكر'
    _order = 'product_id, id'

    release_stage_id = fields.Many2one(
        'furniture.mrp.advance.material.release.stage', string='مرحلة الإذن',
        required=True, ondelete='cascade', index=True, copy=False,
    )
    release_id = fields.Many2one(
        'furniture.mrp.advance.material.release',
        related='release_stage_id.release_id', store=True, readonly=True,
        index=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', related='release_stage_id.production_id',
        store=True, readonly=True, index=True,
    )
    stage_code = fields.Selection(
        FURNITURE_LEGACY_STAGE_SELECTION,
        related='release_stage_id.stage_code',
        store=True, readonly=True, index=True,
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة', required=True, readonly=True,
        index=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة', required=True, readonly=True,
    )
    requested_qty = fields.Float(
        string='الكمية المطلوبة', required=True, readonly=True,
        digits=(16, 3),
    )
    available_qty = fields.Float(
        string='المتاح المخصص وقت الطلب', readonly=True, digits=(16, 3),
        help='المتاح موزع مرة واحدة عبر كل مراحل الإذن، وليس متاحًا مكررًا لكل مرحلة.',
    )
    shortage_qty = fields.Float(
        string='العجز وقت الطلب', compute='_compute_shortage',
        store=True, readonly=True, digits=(16, 3),
    )
    issued_qty = fields.Float(
        string='الكمية المصروفة', readonly=True, copy=False, digits=(16, 3),
    )
    received_qty = fields.Float(
        string='الكمية المستلمة فعليًا', readonly=True, copy=False,
        digits=(16, 3),
    )
    receipt_difference_qty = fields.Float(
        string='فرق الاستلام', readonly=True, copy=False, digits=(16, 3),
    )
    move_id = fields.Many2one(
        'stock.move', string='حركة الصرف الرئيسية',
        readonly=True, copy=False, ondelete='set null',
    )
    move_ids = fields.Many2many(
        'stock.move', 'furn_adv_rel_line_move_rel',
        'release_line_id', 'move_id',
        string='حركات الصرف', readonly=True, copy=False,
    )
    return_move_ids = fields.Many2many(
        'stock.move', 'furn_adv_rel_line_return_move_rel',
        'release_line_id', 'move_id',
        string='حركات الإرجاع', readonly=True, copy=False,
    )
    receipt_move_ids = fields.Many2many(
        'stock.move', 'furn_adv_rel_line_receipt_move_rel',
        'release_line_id', 'move_id',
        string='حركات استلام الإنتاج', readonly=True, copy=False,
    )
    source_material_line_ids = fields.Many2many(
        'furniture.mrp.material.line',
        'furn_adv_rel_line_material_rel',
        'release_line_id', 'material_line_id',
        string='سطور خامات أمر الإنتاج الأصلية', readonly=True, copy=False,
    )

    _sql_constraints = [
        (
            'advance_release_detail_unique',
            'unique(release_stage_id, product_id, product_uom_id)',
            'الخامة مكررة داخل نفس مرحلة إذن الصرف.',
        ),
        (
            'advance_release_requested_qty_positive',
            'check(requested_qty > 0)',
            'الكمية المطلوبة يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.depends('requested_qty', 'available_qty')
    def _compute_shortage(self):
        for line in self:
            line.shortage_qty = max(
                (line.requested_qty or 0.0) - (line.available_qty or 0.0),
                0.0,
            )

    def _move_qty_in_product_uom(self, move):
        self.ensure_one()
        quantity = move.quantity or move.product_uom_qty
        return self.production_id._quantity_in_product_uom(
            self.product_id, quantity, move.product_uom,
        )

    def _preferred_stock_move_uom(self):
        """Keep the finest recipe precision while stock stays product-based."""
        self.ensure_one()
        product_uom = self.product_id.uom_id
        candidates = self.source_material_line_ids.mapped(
            'product_uom_id'
        ).filtered(
            lambda uom: uom.category_id == product_uom.category_id
        )
        if not candidates:
            return product_uom

        def rounding_in_product_uom(uom):
            return abs(uom._compute_quantity(
                uom.rounding, product_uom, round=False,
            ))

        return candidates.sorted(
            key=lambda uom: (rounding_in_product_uom(uom), uom.id),
        )[:1]

    def _prepare_stock_move_quantity(self, quantity):
        """Convert product-UoM snapshot quantities to a precise move UoM."""
        self.ensure_one()
        move_uom = self._preferred_stock_move_uom()
        product_uom = self.product_id.uom_id
        move_quantity = quantity or 0.0
        if move_uom != product_uom:
            move_quantity = product_uom._compute_quantity(
                move_quantity, move_uom, round=False,
            )
        return move_quantity, move_uom

    def _stored_valid_issue_moves(self, destination):
        self.ensure_one()
        return (self.move_ids | self.move_id).sudo().filtered(
            lambda move: (
                move.state == 'done'
                and move.product_id == self.product_id
                and move.location_dest_id == destination
            )
        )

    def _valid_issue_moves(self, destination):
        """Use exact current material links first, then the stored release links."""
        self.ensure_one()
        current_moves = self.source_material_line_ids.mapped('move_id').sudo().filtered(
            lambda move: (
                move.state == 'done'
                and move.product_id == self.product_id
                and move.location_dest_id == destination
            )
        )
        return current_moves | self._stored_valid_issue_moves(destination)

    def _stored_valid_receipt_moves(self, destination):
        self.ensure_one()
        return self.receipt_move_ids.sudo().filtered(
            lambda move: (
                move.state == 'done'
                and move.product_id == self.product_id
                and move.location_dest_id == destination
            )
        )

    def _allocate_received_qty_to_source_lines(self, receipt_move=False):
        """Persist actual receipt proportionally on the technical recipe rows."""
        self.ensure_one()
        source_lines = self.source_material_line_ids.exists().sorted('id')
        if not source_lines:
            return True
        allocations = self.production_id._allocate_received_product_qty(
            source_lines, self.received_qty,
        )
        for line in source_lines:
            vals = {
                'warehouse_receipt_confirmed': True,
                'warehouse_received_qty': allocations[line.id],
                'warehouse_receipt_stage_id': self.release_stage_id.id,
            }
            if not self.production_id._material_line_already_consumed(line):
                vals['move_id'] = receipt_move.id if receipt_move else False
            line.sudo().write(vals)
        return True


class FurnitureMrpAdvanceMaterialReceiptWizard(models.TransientModel):
    _name = 'furniture.mrp.advance.material.receipt.wizard'
    _description = 'تأكيد استلام خامات مرحلة من المخزن'

    release_stage_id = fields.Many2one(
        'furniture.mrp.advance.material.release.stage',
        string='مرحلة إذن المخزن', required=True, readonly=True,
        ondelete='cascade',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        related='release_stage_id.production_id', readonly=True,
    )
    release_id = fields.Many2one(
        'furniture.mrp.advance.material.release', string='إذن المخزن',
        related='release_stage_id.release_id', readonly=True,
    )
    stage_code = fields.Selection(
        string='المرحلة', related='release_stage_id.stage_code', readonly=True,
    )
    handover_location_id = fields.Many2one(
        'stock.location', string='عهدة التسليم',
        related='release_stage_id.handover_location_id', readonly=True,
    )
    destination_location_id = fields.Many2one(
        'stock.location', string='صالة المرحلة',
        related='release_stage_id.destination_location_id', readonly=True,
    )
    receipt_note = fields.Text(
        string='ملاحظة الاستلام أو سبب الفرق',
    )
    line_ids = fields.One2many(
        'furniture.mrp.advance.material.receipt.wizard.line',
        'wizard_id', string='الخامات المستلمة',
    )
    has_difference = fields.Boolean(
        string='يوجد فرق', compute='_compute_has_difference',
    )

    @api.depends('line_ids.received_qty', 'line_ids.issued_qty')
    def _compute_has_difference(self):
        for wizard in self:
            wizard.has_difference = any(
                float_compare(
                    line.received_qty, line.issued_qty, precision_digits=3,
                ) != 0
                for line in wizard.line_ids
            )

    def action_confirm_receipt(self):
        self.ensure_one()
        if self.has_difference and not (self.receipt_note or '').strip():
            raise UserError(_(
                'اكتب ملاحظة توضح سبب فرق الاستلام قبل التأكيد.'
            ))
        receipt_state = self.release_stage_id._confirm_production_receipt(
            {
                line.release_line_id.id: line.received_qty
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


class FurnitureMrpAdvanceMaterialReceiptWizardLine(models.TransientModel):
    _name = 'furniture.mrp.advance.material.receipt.wizard.line'
    _description = 'خامة مستلمة فعليًا من المخزن'
    _order = 'product_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.advance.material.receipt.wizard',
        string='شاشة الاستلام', required=True, ondelete='cascade',
    )
    release_line_id = fields.Many2one(
        'furniture.mrp.advance.material.release.line',
        string='سطر إذن المخزن', required=True, readonly=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة',
        related='release_line_id.product_id', readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة',
        related='release_line_id.product_uom_id', readonly=True,
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


class FurnitureMrpAdvanceMaterialBatchReceiptWizard(models.TransientModel):
    _name = 'furniture.mrp.advance.material.batch.receipt.wizard'
    _description = 'استلام خامات إذن مجمع لعدة أوامر إنتاج'

    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    release_stage_ids = fields.Many2many(
        'furniture.mrp.advance.material.release.stage',
        'furn_adv_batch_receipt_stage_rel',
        'wizard_id', 'release_stage_id',
        string='المراحل المنتظرة للاستلام', readonly=True,
    )
    release_ids = fields.Many2many(
        'furniture.mrp.advance.material.release',
        string='أذونات المخزن', compute='_compute_summary', readonly=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.advance.material.batch.receipt.wizard.line',
        'wizard_id', string='الخامات المستلمة',
    )
    receipt_note = fields.Text(
        string='ملاحظة الاستلام أو سبب الفروق',
    )
    release_count = fields.Integer(
        string='عدد الأذونات', compute='_compute_summary',
    )
    production_count = fields.Integer(
        string='عدد أوامر الإنتاج', compute='_compute_summary',
    )
    stage_count = fields.Integer(
        string='عدد المراحل', compute='_compute_summary',
    )
    material_count = fields.Integer(
        string='عدد سطور الخامات', compute='_compute_summary',
    )
    stage_summary = fields.Text(
        string='الأوامر والمراحل', compute='_compute_summary',
    )
    has_difference = fields.Boolean(
        string='يوجد فرق استلام', compute='_compute_has_difference',
    )

    @api.depends(
        'release_stage_ids', 'release_stage_ids.release_id',
        'release_stage_ids.production_id', 'release_stage_ids.stage_code',
        'line_ids',
    )
    def _compute_summary(self):
        labels = dict(FURNITURE_STAGE_SELECTION)
        for wizard in self:
            stages = wizard.release_stage_ids.exists().sorted(
                lambda stage: (
                    stage.release_id.id,
                    stage.production_id.id,
                    stage.sequence,
                    stage.id,
                )
            )
            releases = stages.mapped('release_id')
            productions = stages.mapped('production_id')
            wizard.release_ids = releases
            wizard.release_count = len(releases)
            wizard.production_count = len(productions)
            wizard.stage_count = len(stages)
            wizard.material_count = len(wizard.line_ids)
            wizard.stage_summary = '\n'.join(
                '%s — %s — %s' % (
                    stage.release_id.name,
                    stage.production_id.name,
                    labels.get(stage.stage_code, stage.stage_code),
                )
                for stage in stages
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

    @api.model
    def _pending_batch_receipt_stages(self, company=False):
        """All issued stages that production has not received yet.

        The dashboard is a common receipt entry point for releases created
        either from the external batch launcher or from inside one production
        order.  The participating orders are always identified by the stage
        rows, so both release types can be safely collected here.
        """
        company = company or self.env.company
        return self.env[
            'furniture.mrp.advance.material.release.stage'
        ].search([
            ('release_id.company_id', '=', company.id),
            ('release_id.state', '=', 'issued'),
            ('state', '=', 'issued'),
            ('receipt_confirmed', '=', False),
        ], order='release_id, production_id, sequence, id')

    @api.model
    def _line_commands(self, stages):
        commands = []
        for stage in stages.sorted(
            lambda item: (
                item.release_id.id,
                item.production_id.id,
                item.sequence,
                item.id,
            )
        ):
            for line in stage.material_line_ids.sorted(
                lambda item: (item.product_id.display_name, item.id)
            ):
                commands.append((0, 0, {
                    'release_stage_id': stage.id,
                    'release_line_id': line.id,
                    'issued_qty': line.issued_qty,
                    'received_qty': line.issued_qty,
                }))
        return commands

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if not self.env.context.get('auto_load_pending_batch_receipts'):
            return values
        company = self.env['res.company'].browse(
            values.get('company_id') or self.env.company.id
        ).exists() or self.env.company
        stages = self._pending_batch_receipt_stages(company)
        values.update({
            'company_id': company.id,
            'release_stage_ids': [(6, 0, stages.ids)],
            'line_ids': self._line_commands(stages),
        })
        return values

    def action_confirm_receipts(self):
        self.ensure_one()
        stages = self.release_stage_ids.exists().sorted(
            lambda stage: (
                stage.release_id.id,
                stage.production_id.id,
                stage.sequence,
                stage.id,
            )
        )
        if not stages:
            raise UserError(_(
                'لا توجد مراحل مصروفة بانتظار استلام الإنتاج.'
            ))
        if any(stage.release_id.company_id != self.company_id for stage in stages):
            raise UserError(_(
                'لا يمكن استلام مراحل تابعة لشركات مختلفة في نفس المعاملة.'
            ))
        if self.has_difference and not (self.receipt_note or '').strip():
            raise UserError(_(
                'اكتب ملاحظة توضح سبب فرق الاستلام قبل التأكيد.'
            ))

        lines_by_stage = defaultdict(
            lambda: self.env[
                'furniture.mrp.advance.material.batch.receipt.wizard.line'
            ]
        )
        for line in self.line_ids:
            if line.release_stage_id not in stages:
                raise UserError(_(
                    'يوجد سطر خامة لا يتبع مراحل الاستلام المعروضة. '
                    'أغلق الشاشة وافتحها من جديد.'
                ))
            lines_by_stage[line.release_stage_id.id] |= line

        note = (self.receipt_note or '').strip()
        partial_count = 0
        for stage in stages:
            wizard_lines = lines_by_stage[stage.id]
            receipt_state = stage._confirm_production_receipt(
                {
                    line.release_line_id.id: line.received_qty
                    for line in wizard_lines
                },
                note=note,
            )
            partial_count += int(receipt_state == 'partial')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم استلام خامات الأوامر'),
                'message': (
                    _(
                        'تم تأكيد استلام %(stage_count)s مراحل تخص '
                        '%(production_count)s أوامر إنتاج، ويوجد فرق في '
                        '%(partial_count)s مراحل.'
                    ) % {
                        'stage_count': len(stages),
                        'production_count': len(stages.mapped('production_id')),
                        'partial_count': partial_count,
                    }
                    if partial_count else
                    _(
                        'تم تأكيد استلام %(stage_count)s مراحل بالكامل '
                        'لـ %(production_count)s أوامر إنتاج.'
                    ) % {
                        'stage_count': len(stages),
                        'production_count': len(stages.mapped('production_id')),
                    }
                ),
                'type': 'warning' if partial_count else 'success',
                'sticky': bool(partial_count),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }


class FurnitureMrpAdvanceMaterialBatchReceiptWizardLine(models.TransientModel):
    _name = 'furniture.mrp.advance.material.batch.receipt.wizard.line'
    _description = 'خامة داخل استلام جماعي لأوامر الإنتاج'
    _order = 'release_id, production_id, release_stage_id, product_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.advance.material.batch.receipt.wizard',
        string='شاشة الاستلام الجماعي', required=True, ondelete='cascade',
    )
    release_stage_id = fields.Many2one(
        'furniture.mrp.advance.material.release.stage',
        string='مرحلة إذن المخزن', required=True, readonly=True,
        ondelete='cascade',
    )
    release_line_id = fields.Many2one(
        'furniture.mrp.advance.material.release.line',
        string='سطر إذن المخزن', required=True, readonly=True,
        ondelete='cascade',
    )
    release_id = fields.Many2one(
        'furniture.mrp.advance.material.release', string='إذن المخزن',
        related='release_stage_id.release_id', readonly=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        related='release_stage_id.production_id', readonly=True,
    )
    stage_code = fields.Selection(
        string='المرحلة', related='release_stage_id.stage_code', readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة',
        related='release_line_id.product_id', readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة',
        related='release_line_id.product_uom_id', readonly=True,
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

    _sql_constraints = [
        (
            'batch_receipt_release_line_unique',
            'unique(wizard_id, release_line_id)',
            'لا يمكن تكرار نفس خامة الإذن داخل شاشة الاستلام الجماعي.',
        ),
    ]

    @api.depends('issued_qty', 'received_qty')
    def _compute_difference_qty(self):
        for line in self:
            line.difference_qty = max(
                (line.issued_qty or 0.0) - (line.received_qty or 0.0),
                0.0,
            )


class FurnitureMrpAdvanceMaterialReleaseWizard(models.TransientModel):
    _name = 'furniture.mrp.advance.material.release.wizard'
    _description = 'اختيار مراحل الصرف المبكر'

    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        required=True, readonly=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.advance.material.release.wizard.stage.line',
        'wizard_id', string='المراحل المطلوبة',
    )
    selected_count = fields.Integer(
        string='عدد المراحل المختارة', compute='_compute_selection_counts',
    )
    selectable_count = fields.Integer(
        string='عدد المراحل المتاحة', compute='_compute_selection_counts',
    )

    @api.depends('line_ids.selected', 'line_ids.selectable')
    def _compute_selection_counts(self):
        for wizard in self:
            wizard.selected_count = len(wizard.line_ids.filtered('selected'))
            wizard.selectable_count = len(
                wizard.line_ids.filtered('selectable')
            )

    @api.model
    def _stage_commands(self, production):
        if not production:
            return []
        release_model = self.env['furniture.mrp.advance.material.release']
        active_map = release_model.get_active_stage_map(production)
        labels = dict(FURNITURE_STAGE_SELECTION)
        commands = []
        required_codes = production._required_stage_codes()
        payload_by_stage = {
            payload['stage_code']: payload
            for payload in release_model._snapshot_payloads(production, required_codes)
        }
        for sequence, stage_code in enumerate(required_codes, start=1):
            active_stage = active_map.get(stage_code)
            payload = payload_by_stage.get(stage_code, {})
            detail_values = [
                command[2]
                for command in payload.get('material_line_ids', [])
                if isinstance(command, (list, tuple)) and len(command) >= 3
            ]
            product_by_id = {
                product.id: product
                for product in self.env['product.product'].browse(
                    [values.get('product_id') for values in detail_values]
                ).exists()
            }
            uom_by_id = {
                uom.id: uom
                for uom in self.env['uom.uom'].browse(
                    [values.get('product_uom_id') for values in detail_values]
                ).exists()
            }

            shortage_values = []
            for values in detail_values:
                shortage = max(
                    (values.get('requested_qty') or 0.0)
                    - (values.get('available_qty') or 0.0),
                    0.0,
                )
                shortage_values.append(dict(values, shortage_qty=shortage))
            detail_values = shortage_values

            def _material_items(quantity_field, positive_only=False):
                items = []
                for item_index, values in enumerate(detail_values):
                    quantity = values.get(quantity_field, 0.0) or 0.0
                    if positive_only and float_compare(
                        quantity, 0.0, precision_digits=3,
                    ) <= 0:
                        continue
                    product_id = values.get('product_id')
                    product = product_by_id.get(product_id)
                    uom = uom_by_id.get(values.get('product_uom_id'))
                    items.append({
                        'key': '%s-%s-%s' % (
                            stage_code, product_id or 'material', item_index,
                        ),
                        'product_id': product_id or False,
                        'name': product.display_name if product else _('خامة'),
                        'quantity': production._format_dimension_value(quantity),
                        'uom': uom.name if uom else '',
                    })
                return items

            def _summary(items):
                return ' | '.join(
                    '%s: %s %s' % (
                        item['name'], item['quantity'], item['uom'],
                    )
                    for item in items
                ) or _('لا يوجد')

            material_items = _material_items('requested_qty')
            available_items = _material_items('available_qty')
            shortage_items = _material_items(
                'shortage_qty', positive_only=True,
            )
            commands.append((0, 0, {
                'sequence': sequence * 10,
                'stage_code': stage_code,
                'selected': False,
                'selectable': not bool(active_stage),
                'material_count': len(detail_values),
                'active_release_name': active_stage.release_id.name if active_stage else False,
                'material_summary': _summary(material_items),
                'available_summary': _summary(available_items),
                'shortage_summary': _summary(shortage_items),
                'material_items': material_items,
                'available_items': available_items,
                'shortage_items': shortage_items,
                'has_shortage': any(
                    float_compare(
                        values.get('shortage_qty') or 0.0,
                        0.0,
                        precision_digits=3,
                    ) > 0
                    for values in detail_values
                ),
                'note': (
                    _('يوجد إذن نشط: %s') % active_stage.release_id.name
                    if active_stage else
                    _('%s خامة جاهزة للطلب') % len(detail_values)
                ),
                'display_name_hint': labels.get(stage_code, stage_code),
            }))
        return commands

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        production_id = values.get('production_id') or self.env.context.get('default_production_id')
        production = self.env['furniture.mrp.production'].browse(production_id).exists()
        if production and 'line_ids' in fields_list:
            values['line_ids'] = self._stage_commands(production)
        return values

    @api.onchange('production_id')
    def _onchange_production_id(self):
        for wizard in self:
            wizard.line_ids = [(5, 0, 0)] + wizard._stage_commands(wizard.production_id)

    def _open_action(self):
        self.ensure_one()
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('طلب خامات مراحل مبكرًا'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': dict(
                self.env.context,
                default_production_id=self.production_id.id,
                form_view_initial_mode='edit',
            ),
        }

    def _check_bulk_selection_access(self):
        self.ensure_one()
        release_model = self.env[
            'furniture.mrp.advance.material.release'
        ]
        release_model._check_can_request()
        release_model._check_production_company_access(self.production_id)

    def _set_all_stage_selection(self, selected):
        self.ensure_one()
        self._check_bulk_selection_access()
        if selected:
            self.line_ids.filtered('selectable').write({'selected': True})
            self.line_ids.filtered(
                lambda line: not line.selectable and line.selected
            ).write({'selected': False})
        else:
            self.line_ids.filtered('selected').write({'selected': False})
        self.invalidate_recordset(['selected_count', 'selectable_count'])
        return self._open_action()

    def action_select_all_stages(self):
        return self._set_all_stage_selection(True)

    def action_clear_stage_selection(self):
        return self._set_all_stage_selection(False)

    def action_create_release(self):
        self.ensure_one()
        self.env[
            'furniture.mrp.advance.material.release'
        ]._check_can_request()
        selected_lines = self.line_ids.filtered(
            lambda line: line.selected and line.selectable
        )
        if not selected_lines:
            raise UserError(_('اختر مرحلة واحدة على الأقل.'))
        release = self.env[
            'furniture.mrp.advance.material.release'
        ].create_from_stage_codes(
            self.production_id,
            selected_lines.sorted('sequence').mapped('stage_code'),
        )
        return release.action_request_sent_notification()

    def action_send_request(self):
        return self.action_create_release()

    def action_skip(self):
        return {'type': 'ir.actions.act_window_close'}


class FurnitureMrpAdvanceMaterialReleaseWizardStageLine(models.TransientModel):
    _name = 'furniture.mrp.advance.material.release.wizard.stage.line'
    _description = 'مرحلة قابلة للاختيار للصرف المبكر'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.advance.material.release.wizard',
        string='معالج الصرف', required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION, string='المرحلة',
        required=True, readonly=True,
    )
    selected = fields.Boolean(string='اختيار', default=False)
    selectable = fields.Boolean(string='متاح للاختيار', default=True, readonly=True)
    material_count = fields.Integer(string='عدد الخامات', readonly=True)
    material_summary = fields.Text(string='الخامات المطلوبة', readonly=True)
    available_summary = fields.Text(string='المتاح من المخزن', readonly=True)
    shortage_summary = fields.Text(string='العجز', readonly=True)
    material_items = fields.Json(
        string='تفاصيل الخامات المطلوبة', readonly=True, default=list,
    )
    available_items = fields.Json(
        string='تفاصيل المتاح من المخزن', readonly=True, default=list,
    )
    shortage_items = fields.Json(
        string='تفاصيل العجز', readonly=True, default=list,
    )
    has_shortage = fields.Boolean(string='يوجد عجز', readonly=True)
    active_release_name = fields.Char(string='الإذن النشط', readonly=True)
    note = fields.Char(string='ملاحظة', readonly=True)
    display_name_hint = fields.Char(string='اسم المرحلة', readonly=True)

    @api.onchange('selected')
    def _onchange_selected(self):
        for line in self:
            if line.selected and not line.selectable:
                line.selected = False
                return {
                    'warning': {
                        'title': _('المرحلة مرتبطة بإذن نشط'),
                        'message': _('اقفل أو نفّذ الإذن الحالي قبل إنشاء إذن جديد لنفس المرحلة.'),
                    }
                }


class FurnitureMrpAdvanceMaterialBatchWizard(models.TransientModel):
    _name = 'furniture.mrp.advance.material.batch.wizard'
    _description = 'إرسال أذونات خامات لعدة أوامر إنتاج'

    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    period_start = fields.Date(string='بداية الفترة')
    period_end = fields.Date(string='نهاية الفترة')
    production_ids = fields.Many2many(
        'furniture.mrp.production',
        'furniture_mrp_batch_wizard_production_rel',
        'wizard_id', 'production_id',
        string='أوامر الإنتاج',
    )
    order_line_ids = fields.One2many(
        'furniture.mrp.advance.material.batch.wizard.line',
        'wizard_id', string='اختيارات المراحل حسب أمر الإنتاج',
    )
    launch_warning = fields.Text(
        string='تنبيه عند الفتح', readonly=True,
    )
    production_count = fields.Integer(
        string='عدد الأوامر', compute='_compute_selection_counts',
    )
    selected_production_count = fields.Integer(
        string='أوامر عليها اختيار', compute='_compute_selection_counts',
    )
    selected_stage_count = fields.Integer(
        string='إجمالي المراحل المختارة', compute='_compute_selection_counts',
    )

    @api.depends(
        'production_ids', 'order_line_ids.selected_stage_count',
        'order_line_ids.include_in_request',
        'order_line_ids.use_priming', 'order_line_ids.use_painting',
        'order_line_ids.use_carpentry', 'order_line_ids.use_bases', 'order_line_ids.use_finishing',
        'order_line_ids.use_tailoring', 'order_line_ids.use_sewing', 'order_line_ids.use_upholstery',
        'order_line_ids.use_packaging',
    )
    def _compute_selection_counts(self):
        for wizard in self:
            selected_lines = wizard.order_line_ids.filtered(
                'include_in_request'
            )
            wizard.production_count = len(wizard.production_ids)
            wizard.selected_production_count = len(selected_lines)
            wizard.selected_stage_count = sum(
                selected_lines.mapped('selected_stage_count')
            )

    @api.model
    def _all_launchable_productions(self, company=False):
        """Orders shown by the external batch launcher.

        Loading these records is deliberately read-only: no stage is selected
        and no release or stock move is created until the user explicitly
        includes an order, chooses its stages and submits the wizard.
        """
        company = company or self.env.company
        candidates = self.env['furniture.mrp.production'].search([
            ('company_id', '=', company.id),
            ('state', 'in', ('confirmed', 'in_production')),
        ], order='id')
        eligible, _warning = self._launch_productions(candidates, company)
        return eligible

    @api.model
    def _launchable_productions_for_period(
        self, period_start, period_end, company=False,
    ):
        company = company or self.env.company
        period_start = fields.Date.to_date(period_start)
        period_end = fields.Date.to_date(period_end)
        if not period_start or not period_end or period_start > period_end:
            return self.env['furniture.mrp.production']

        timezone = (
            self.env.context.get('tz')
            or self.env.user.tz
            or company.resource_calendar_id.tz
            or 'UTC'
        )
        timestamp_context = self.with_context(tz=timezone)

        def overlaps_period(production):
            if not production.date_planned_start:
                return False
            planned_start = fields.Datetime.context_timestamp(
                timestamp_context, production.date_planned_start,
            ).date()
            planned_finish = fields.Datetime.context_timestamp(
                timestamp_context,
                production.date_planned_finish
                or production.date_planned_start,
            ).date()
            if planned_finish < planned_start:
                planned_finish = planned_start
            return (
                planned_start <= period_end
                and planned_finish >= period_start
            )

        return self._all_launchable_productions(company).filtered(
            overlaps_period
        )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if not self.env.context.get('auto_load_eligible_productions'):
            return values

        company = self.env['res.company'].browse(
            values.get('company_id') or self.env.company.id
        ).exists() or self.env.company
        productions = self._launchable_productions_for_period(
            values.get('period_start'), values.get('period_end'), company,
        )
        values.update({
            'company_id': company.id,
            'production_ids': [(6, 0, productions.ids)],
            'order_line_ids': self._order_commands(
                productions, included_production_ids=set(),
            ),
        })
        return values

    @api.model
    def _launch_productions(self, productions, company=False):
        company = company or self.env.company
        # A many2many edited inside an onchange contains virtual copies whose
        # ids are NewId(origin=<real id>).  Always continue with the persisted
        # origins so sorting and the database lookups below use integer ids.
        productions = productions._origin.exists()
        eligible = productions.filtered(
            lambda production: (
                production.company_id == company
                and production.state in ('confirmed', 'in_production')
            )
        )
        ignored = productions - eligible
        warning = False
        if ignored:
            warning = _(
                'تم تجاهل أوامر غير مؤكدة أو تابعة لشركة أخرى: %s'
            ) % '، '.join(ignored.mapped('name'))
        return eligible.sorted('id'), warning

    @api.model
    def _order_value_list(self, productions):
        productions = productions._origin.exists().sorted('id')
        if not productions:
            return []
        labels = dict(FURNITURE_STAGE_SELECTION)
        field_by_stage = {
            'priming': 'use_priming',
            'painting': 'use_painting',
            'carpentry': 'use_carpentry',
            'bases': 'use_bases',
            'finishing': 'use_finishing',
            'tailoring': 'use_tailoring',
            'upholstery': 'use_upholstery',
            'packaging': 'use_packaging',
        }
        active_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', 'in', productions.ids),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ], order='production_id, sequence, id')
        active_by_key = {
            (stage.production_id.id, stage.stage_code): stage
            for stage in active_stages
        }
        legacy_requests = self.env[
            'furniture.mrp.store.request'
        ].sudo().search([
            ('production_id', 'in', productions.ids),
            ('state', 'in', ('pending', 'approved')),
        ], order='production_id, id')
        legacy_by_key = {
            (request.production_id.id, request.stage_code): request
            for request in legacy_requests
        }

        result = []
        for production in productions:
            required_codes = set(production._required_stage_codes())
            blocked_details = []
            values = {
                'production_id': production.id,
                'product_summary': (
                    production.header_product_summary
                    or production.product_id.display_name
                    or _('بدون أصناف')
                ),
            }
            selectable_count = 0
            for stage_code, _stage_label in FURNITURE_STAGE_SELECTION:
                use_field = field_by_stage[stage_code]
                can_field = 'can_%s' % use_field
                reason_field = 'reason_%s' % use_field
                can_select = False
                reason = False
                if stage_code not in required_codes:
                    reason = _('خارج المسار')
                else:
                    active_stage = active_by_key.get(
                        (production.id, stage_code)
                    )
                    legacy_request = legacy_by_key.get(
                        (production.id, stage_code)
                    )
                    stage_order = production._stage_order_record(stage_code)
                    consumed_lines = production.material_line_ids.filtered(
                        lambda line, code=stage_code: (
                            line.stage == code
                            and production._material_line_already_consumed(line)
                        )
                    )
                    if active_stage:
                        reason = _('تم طلبها في %s') % active_stage.release_id.name
                    elif legacy_request:
                        reason = _('تم طلبها في %s') % legacy_request.name
                    elif stage_order and stage_order.state != 'pending':
                        reason = _('بدأت المرحلة')
                    elif consumed_lines:
                        reason = _('دخلت خاماتها الإنتاج')
                    else:
                        can_select = True
                        selectable_count += 1
                values[use_field] = False
                values[can_field] = can_select
                values[reason_field] = reason
                if stage_code in required_codes and reason:
                    blocked_details.append('%s: %s' % (
                        labels.get(stage_code, stage_code), reason,
                    ))
            values.update({
                'has_selectable_stages': bool(selectable_count),
                'blocked_stage_summary': ' | '.join(blocked_details),
            })
            result.append(values)
        return result

    @api.model
    def _order_commands(
        self, productions, selected_by_production=None,
        included_production_ids=None,
    ):
        selected_by_production = selected_by_production or {}
        if included_production_ids is None:
            included_production_ids = set(productions._origin.exists().ids)
        else:
            included_production_ids = set(included_production_ids)
        commands = []
        use_fields = (
            'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
            'use_finishing', 'use_tailoring', 'use_upholstery',
            'use_packaging',
        )
        for values in self._order_value_list(productions):
            previous = selected_by_production.get(
                values['production_id'], {}
            )
            values['include_in_request'] = bool(
                previous.get(
                    'include_in_request',
                    values['production_id'] in included_production_ids,
                )
            )
            for use_field in use_fields:
                values[use_field] = bool(
                    previous.get(use_field)
                    and values.get('can_%s' % use_field)
                )
            commands.append((0, 0, values))
        return commands

    def _line_selection_snapshot(self):
        self.ensure_one()
        use_fields = (
            'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
            'use_finishing', 'use_tailoring', 'use_upholstery',
            'use_packaging',
        )
        previous = {}
        for line in self.order_line_ids:
            production = line.production_id._origin.exists()
            if not production:
                continue
            previous[production.id] = {
                use_field: line[use_field]
                for use_field in use_fields
            }
            previous[production.id]['include_in_request'] = (
                line.include_in_request
            )
        return previous

    @api.onchange('period_start', 'period_end')
    def _onchange_period_filter(self):
        warning = False
        for wizard in self:
            previous = wizard._line_selection_snapshot()
            valid_period = (
                wizard.period_start
                and wizard.period_end
                and wizard.period_start <= wizard.period_end
            )
            productions = (
                wizard._launchable_productions_for_period(
                    wizard.period_start,
                    wizard.period_end,
                    wizard.company_id,
                )
                if valid_period
                else self.env['furniture.mrp.production']
            )
            wizard.production_ids = [(6, 0, productions.ids)]
            wizard.order_line_ids = [
                (5, 0, 0),
                *wizard._order_commands(
                    productions, previous, included_production_ids=set(),
                ),
            ]
            invalid_message = (
                _('بداية الفترة يجب أن تكون قبل نهاية الفترة أو مساوية لها.')
                if (
                    wizard.period_start
                    and wizard.period_end
                    and wizard.period_start > wizard.period_end
                )
                else False
            )
            wizard.launch_warning = invalid_message
            if invalid_message:
                warning = {
                    'warning': {
                        'title': _('فترة غير صحيحة'),
                        'message': invalid_message,
                    }
                }
        return warning

    @api.onchange('production_ids')
    def _onchange_production_ids(self):
        for wizard in self:
            previous = wizard._line_selection_snapshot()
            eligible, warning = wizard._launch_productions(
                wizard.production_ids, wizard.company_id,
            )
            wizard.production_ids = [(6, 0, eligible.ids)]
            wizard.order_line_ids = [
                (5, 0, 0),
                *wizard._order_commands(
                    eligible, previous, included_production_ids=set(),
                ),
            ]
            wizard.launch_warning = warning

    def _selected_codes_by_production(self):
        self.ensure_one()
        stage_fields = (
            ('priming', 'use_priming'),
            ('painting', 'use_painting'),
            ('carpentry', 'use_carpentry'),
            ('bases', 'use_bases'),
            ('finishing', 'use_finishing'),
            ('tailoring', 'use_tailoring'),
            ('upholstery', 'use_upholstery'),
            ('packaging', 'use_packaging'),
        )
        selected = defaultdict(set)
        allowed_production_ids = set(self.production_ids.ids)
        selected_lines = self.order_line_ids.filtered('include_in_request')
        for line in selected_lines:
            production = line.production_id.exists()
            if not production or production.id not in allowed_production_ids:
                raise UserError(_(
                    'توجد اختيارات مرتبطة بأمر غير موجود ضمن قائمة الأوامر.'
                ))
            for stage_code, use_field in stage_fields:
                if line[use_field]:
                    selected[production.id].add(stage_code)
            if not selected[production.id]:
                raise UserError(_(
                    'حدد مرحلة واحدة على الأقل لأمر الإنتاج %s.'
                ) % production.display_name)
        return selected

    def action_send_requests(self):
        self.ensure_one()
        if not self.period_start or not self.period_end:
            raise UserError(_('حدد بداية الفترة ونهاية الفترة أولًا.'))
        if self.period_start > self.period_end:
            raise UserError(_(
                'بداية الفترة يجب أن تكون قبل نهاية الفترة أو مساوية لها.'
            ))
        release_model = self.env[
            'furniture.mrp.advance.material.release'
        ]
        release_model._check_can_request()
        selected_by_production = self._selected_codes_by_production()
        if not selected_by_production:
            raise UserError(_(
                'حدد مرحلة واحدة على الأقل داخل أمر إنتاج واحد على الأقل.'
            ))

        productions = self.env['furniture.mrp.production'].browse(
            sorted(selected_by_production)
        ).exists().sorted('id')
        if len(productions) != len(selected_by_production):
            raise UserError(_('أحد أوامر الإنتاج المختارة لم يعد موجودًا.'))
        if any(production.company_id != self.company_id for production in productions):
            raise UserError(_('لا يمكن جمع أوامر إنتاج من شركات مختلفة في نفس الطلب.'))

        release = release_model.create_from_production_stage_map(
            productions,
            selected_by_production,
            requested_by=self.env.user,
            availability_pool={},
            notify_storekeeper=True,
            is_batch_request=True,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('بانتظار أمين المخزن'),
                'message': _(
                    'تم إرسال إذن واحد %(release)s يضم '
                    '%(production_count)s أوامر إنتاج. لن تتحرك الخامات '
                    'قبل اعتماد أمين المخزن.'
                ) % {
                    'release': release.name,
                    'production_count': len(productions),
                },
                'type': 'warning',
                'sticky': False,
                # The notification is enough confirmation for the requester.
                # Close the modal and keep the underlying production screen
                # instead of redirecting to the generated release list.
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}


class FurnitureMrpAdvanceMaterialBatchWizardLine(models.TransientModel):
    _name = 'furniture.mrp.advance.material.batch.wizard.line'
    _description = 'اختيارات مراحل أمر داخل طلب خامات جماعي'
    _order = 'production_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.advance.material.batch.wizard',
        string='المعالج الجماعي', required=True, ondelete='cascade',
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', string='أمر الإنتاج',
        required=True, readonly=True, ondelete='cascade',
    )
    include_in_request = fields.Boolean(
        string='اختيار الأمر ضمن طلب الصرف', default=False,
    )
    production_state = fields.Selection(
        related='production_id.state', string='حالة الأمر', readonly=True,
    )
    product_summary = fields.Text(string='الأصناف', readonly=True)
    use_priming = fields.Boolean(string='التقديم')
    use_painting = fields.Boolean(string='تصنيع دهانات')
    use_carpentry = fields.Boolean(string='تجميع')
    use_bases = fields.Boolean(string='القواعد')
    use_finishing = fields.Boolean(string='تجهيز')
    use_tailoring = fields.Boolean(string='تفصيل')
    use_sewing = fields.Boolean(string='الخياطة')
    use_upholstery = fields.Boolean(string='كسوه')
    use_packaging = fields.Boolean(string='التغليف')
    can_use_priming = fields.Boolean(readonly=True)
    can_use_painting = fields.Boolean(readonly=True)
    can_use_carpentry = fields.Boolean(readonly=True)
    can_use_bases = fields.Boolean(readonly=True)
    can_use_finishing = fields.Boolean(readonly=True)
    can_use_tailoring = fields.Boolean(readonly=True)
    can_use_sewing = fields.Boolean(readonly=True)
    can_use_upholstery = fields.Boolean(readonly=True)
    can_use_packaging = fields.Boolean(readonly=True)
    reason_use_priming = fields.Char(readonly=True)
    reason_use_painting = fields.Char(readonly=True)
    reason_use_carpentry = fields.Char(readonly=True)
    reason_use_bases = fields.Char(readonly=True)
    reason_use_finishing = fields.Char(readonly=True)
    reason_use_tailoring = fields.Char(readonly=True)
    reason_use_sewing = fields.Char(readonly=True)
    reason_use_upholstery = fields.Char(readonly=True)
    reason_use_packaging = fields.Char(readonly=True)
    has_selectable_stages = fields.Boolean(readonly=True)
    blocked_stage_summary = fields.Text(
        string='المراحل غير المتاحة', readonly=True,
    )
    selected_stage_count = fields.Integer(
        string='المراحل المختارة', compute='_compute_selected_stages',
    )
    stage_summary = fields.Char(
        string='اختيار هذا الأمر', compute='_compute_selected_stages',
    )

    _sql_constraints = [
        (
            'batch_wizard_production_unique',
            'unique(wizard_id, production_id)',
            'لا يمكن تكرار نفس أمر الإنتاج داخل المعالج الجماعي.',
        ),
    ]

    @api.depends(
        'include_in_request',
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery',
        'use_packaging',
    )
    def _compute_selected_stages(self):
        stage_fields = (
            ('use_priming', _('التقديم')),
            ('use_painting', _('تصنيع دهانات')),
            ('use_carpentry', _('تجميع')),
            ('use_bases', _('القواعد')),
            ('use_finishing', _('تجهيز')),
            ('use_tailoring', _('تفصيل')),
            ('use_upholstery', _('كسوه')),
            ('use_packaging', _('التغليف')),
        )
        for line in self:
            selected_labels = [
                label for field_name, label in stage_fields
                if line.include_in_request and line[field_name]
            ]
            line.selected_stage_count = len(selected_labels)
            line.stage_summary = (
                '، '.join(selected_labels)
                if selected_labels else (
                    _('لم تحدد مراحل لهذا الأمر')
                    if line.include_in_request
                    else _('الأمر غير محدد للصرف')
                )
            )


class FurnitureMrpProductionAdvanceMaterialRelease(models.Model):
    """Small integration surface on the production order.

    Keeping these hooks next to the release models makes the stock invariant
    explicit: rebuilding technically equivalent material lines is allowed and
    relinked; changing the aggregate while a request is active aborts the
    surrounding transaction.
    """

    _inherit = 'furniture.mrp.production'

    advance_material_release_stage_ids = fields.One2many(
        'furniture.mrp.advance.material.release.stage', 'production_id',
        string='مراحل أذونات الصرف المبكر', readonly=True, copy=False,
    )
    advance_material_release_ids = fields.Many2many(
        'furniture.mrp.advance.material.release',
        string='أذونات الصرف المبكر',
        compute='_compute_advance_material_release_links',
        compute_sudo=True, readonly=True, copy=False,
    )
    advance_material_release_count = fields.Integer(
        string='عدد أذونات الصرف المبكر',
        compute='_compute_advance_material_release_count',
    )
    has_uncovered_advance_material_stages = fields.Boolean(
        string='توجد مراحل لم تُطلب خاماتها',
        compute='_compute_has_uncovered_advance_material_stages',
        compute_sudo=True,
    )

    @api.depends('advance_material_release_stage_ids.release_id')
    def _compute_advance_material_release_links(self):
        for production in self:
            production.advance_material_release_ids = (
                production.advance_material_release_stage_ids.mapped(
                    'release_id'
                )
            )

    @api.depends('advance_material_release_ids')
    def _compute_advance_material_release_count(self):
        for production in self:
            production.advance_material_release_count = len(
                production.advance_material_release_ids
            )

    @api.depends(
        'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
        'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery', 'use_packaging',
        'advance_material_release_stage_ids',
        'advance_material_release_stage_ids.stage_code',
        'advance_material_release_stage_ids.state',
    )
    def _compute_has_uncovered_advance_material_stages(self):
        persistent_productions = self.filtered(
            lambda production: isinstance(production.id, int)
        )
        active_codes_by_production = defaultdict(set)
        active_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', 'in', persistent_productions.ids),
            ('state', 'in', ACTIVE_RELEASE_STAGE_STATES),
        ]) if persistent_productions else self.env[
            'furniture.mrp.advance.material.release.stage'
        ]
        for active_stage in active_stages:
            active_codes_by_production[
                active_stage.production_id.id
            ].add(active_stage.stage_code)

        for production in self:
            required_codes = set(production._required_stage_codes())
            active_codes = active_codes_by_production.get(
                production.id, set(),
            )
            production.has_uncovered_advance_material_stages = bool(
                required_codes - active_codes
            )

    def _advance_material_stage_for_execution(
        self, stage_code, mark_started=True,
    ):
        """Return and lock the exact advance release used by a stage action.

        A wizard can stay open while the storekeeper works in another browser.
        The context marker makes that wizard fail safely if its release was
        returned in the meantime, instead of silently starting with stale
        approval data.
        """
        self.ensure_one()
        marker_id = self.env.context.get(
            'furniture_advance_material_release_stage_id',
        )
        if marker_id:
            advance_stage = self.env[
                'furniture.mrp.advance.material.release.stage'
            ].sudo().browse(marker_id).exists()
            if (
                not advance_stage
                or advance_stage.production_id != self
                or advance_stage.stage_code != stage_code
            ):
                raise UserError(_(
                    'إذن صرف الخامات المرتبط بشاشة البدء لم يعد صالحًا. '
                    'أغلق الشاشة وافتح المرحلة من جديد.'
                ))
        else:
            advance_stage = self.env[
                'furniture.mrp.advance.material.release'
            ].find_active_stage_release(self, stage_code)
        if not advance_stage:
            return advance_stage
        advance_stage._prepare_for_stage_execution(
            mark_started=mark_started,
        )
        return advance_stage

    def _move_materials_to_stage_wip(
        self, stage_model, material_lines=False,
        allow_material_line_link_sudo=False,
    ):
        """Stage an advance receipt before the normal material mover runs."""
        self.ensure_one()
        marker_id = self.env.context.get(
            'furniture_advance_material_release_stage_id',
        )
        if marker_id:
            stage_code = self._stage_model_to_code(stage_model)
            self._advance_material_stage_for_execution(stage_code)
        return super()._move_materials_to_stage_wip(
            stage_model,
            material_lines=material_lines,
            allow_material_line_link_sudo=allow_material_line_link_sudo,
        )

    def _start_first_stage_lines_from_stock(
        self, stage_order, stage_code, wizard_lines,
    ):
        self.ensure_one()
        self._advance_material_stage_for_execution(stage_code)
        return super()._start_first_stage_lines_from_stock(
            stage_order, stage_code, wizard_lines,
        )

    def _get_stage_start_current_lines(
        self, stage_code, wizard_lines, selected_only=True,
        stage_order=False,
    ):
        self.ensure_one()
        self._advance_material_stage_for_execution(stage_code)
        return super()._get_stage_start_current_lines(
            stage_code,
            wizard_lines,
            selected_only=selected_only,
            stage_order=stage_order,
        )

    def _refresh_material_lines_for_stage_plan(self):
        persistent_productions = self.filtered(
            lambda item: isinstance(item.id, int)
        )
        issued_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', 'in', persistent_productions.ids),
            ('state', 'in', ('issued', 'started')),
        ]) if persistent_productions else self.env[
            'furniture.mrp.advance.material.release.stage'
        ]

        # The base implementation rebuilds every technical material line.  A
        # partial batch may legitimately trigger that rebuild after another
        # batch has already consumed some materials, so blocking the refresh
        # would break continuation.  Preserve each old line's latest move by
        # exact technical identity; post-refresh relinking supplies the
        # Stock->WIP move to new split lines, then these values restore any
        # later WIP->Production consumption move on the pre-existing lines.
        issued_stage_codes_by_production = defaultdict(set)
        for issued_stage in issued_stages:
            issued_stage_codes_by_production[
                issued_stage.production_id.id
            ].add(issued_stage.stage_code)
        preserved_move_ids_by_key = defaultdict(list)
        for production in persistent_productions:
            issued_codes = issued_stage_codes_by_production.get(production.id, set())
            for material_line in production.material_line_ids.filtered(
                lambda line: line.stage in issued_codes and line.move_id
            ).sorted('id'):
                key = (
                    production.id,
                    material_line.production_line_id.id or False,
                    material_line.stage or False,
                    material_line.product_id.id or False,
                    material_line.product_uom_id.id or False,
                )
                preserved_move_ids_by_key[key].append(material_line.move_id.id)

        result = super()._refresh_material_lines_for_stage_plan()
        release_model = self.env['furniture.mrp.advance.material.release']
        for production in persistent_productions:
            release_model.relink_issued_moves_after_material_refresh(production)

        if preserved_move_ids_by_key:
            preserved_moves = self.env['stock.move'].browse([
                move_id
                for move_ids in preserved_move_ids_by_key.values()
                for move_id in move_ids
            ]).exists()
            valid_preserved_move_ids = set(preserved_moves.ids)
            refreshed_lines_by_key = defaultdict(
                lambda: self.env['furniture.mrp.material.line']
            )
            for production in persistent_productions:
                issued_codes = issued_stage_codes_by_production.get(
                    production.id, set(),
                )
                for material_line in production.material_line_ids.filtered(
                    lambda line: line.stage in issued_codes
                ).sorted('id'):
                    key = (
                        production.id,
                        material_line.production_line_id.id or False,
                        material_line.stage or False,
                        material_line.product_id.id or False,
                        material_line.product_uom_id.id or False,
                    )
                    refreshed_lines_by_key[key] |= material_line
            for key, move_ids in preserved_move_ids_by_key.items():
                refreshed_lines = refreshed_lines_by_key.get(
                    key, self.env['furniture.mrp.material.line'],
                )
                for material_line, move_id in zip(refreshed_lines, move_ids):
                    if move_id in valid_preserved_move_ids:
                        material_line.sudo().write({'move_id': move_id})
        return result

    def _apply_stage_material_overrides(
        self, stage_code, wizard_lines, production_lines=False,
    ):
        self.ensure_one()
        advance_stage = self.env[
            'furniture.mrp.advance.material.release'
        ].find_active_stage_release(self, stage_code)

        result = super()._apply_stage_material_overrides(
            stage_code, wizard_lines, production_lines=production_lines,
        )

        # A wizard material override is allowed only when it leaves the
        # approved aggregate unchanged (for example after a technical split).
        # Any real product/quantity change raises here and rolls back the
        # override together with the surrounding start transaction.
        if advance_stage:
            advance_stage._validate_current_snapshot(relink_sources=True)
            if advance_stage.state in ('issued', 'started'):
                advance_stage._relink_issued_moves()
        return result

    def action_open_advance_material_wizard(self):
        self.ensure_one()
        self.env[
            'furniture.mrp.advance.material.release'
        ]._check_can_request()
        if self.state not in ('confirmed', 'in_production'):
            raise UserError(_('يجب تأكيد أمر الإنتاج قبل طلب خامات المراحل.'))
        if not self.has_uncovered_advance_material_stages:
            raise UserError(_(
                'تم إرسال طلب خامات لكل مراحل أمر الإنتاج بالفعل.'
            ))
        self._ensure_stage_locations()
        wizard_model = self.env[
            'furniture.mrp.advance.material.release.wizard'
        ].with_context(default_production_id=self.id)
        wizard = wizard_model.create({
            'production_id': self.id,
            'line_ids': wizard_model._stage_commands(self),
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('طلب خامات مراحل مبكرًا'),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': dict(
                self.env.context,
                default_production_id=self.id,
                form_view_initial_mode='edit',
            ),
            'views': [(view.id if view else False, 'form')],
            **({'view_id': view.id} if view else {}),
        }

    def action_open_batch_advance_material_wizard(self):
        release_model = self.env[
            'furniture.mrp.advance.material.release'
        ]
        release_model._check_can_request()
        wizard_model = self.env[
            'furniture.mrp.advance.material.batch.wizard'
        ]
        wizard = wizard_model.create({
            'company_id': self.env.company.id,
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_advance_material_batch_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('إذن خامات لأوامر إنتاج متعددة'),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id if view else False, 'form')],
            **({'view_id': view.id} if view else {}),
            'target': 'new',
            'context': dict(
                self.env.context,
                form_view_initial_mode='edit',
            ),
        }

    def action_open_batch_advance_material_receipt_wizard(self):
        wizard_model = self.env[
            'furniture.mrp.advance.material.batch.receipt.wizard'
        ]
        stages = wizard_model._pending_batch_receipt_stages(
            self.env.company,
        )
        if stages:
            stages._check_can_receive()
        else:
            # Enforce the same production-role contract even when there is no
            # pending work, instead of leaking release state to another user.
            if not (
                self.env.is_superuser()
                or self.env.user.has_group(
                    'furniture_mrp.group_furniture_mrp_manager'
                )
                or self.env.user.has_group(
                    'furniture_mrp.group_furniture_mrp_supervisor'
                )
            ):
                raise AccessError(_(
                    'تأكيد استلام الخامات متاح لمدير الإنتاج أو مشرف '
                    'الإنتاج فقط.'
                ))
            raise UserError(_(
                'لا توجد حاليًا أذونات خامات مجمعة مصروفة من المخزن '
                'وبانتظار استلام الإنتاج.'
            ))
        wizard = wizard_model.create({
            'company_id': self.env.company.id,
            'release_stage_ids': [(6, 0, stages.ids)],
            'line_ids': wizard_model._line_commands(stages),
        })
        view = self.env.ref(
            'furniture_mrp.'
            'view_furniture_mrp_advance_material_batch_receipt_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('استلام من المخزن'),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id if view else False, 'form')],
            **({'view_id': view.id} if view else {}),
            'target': 'new',
            'context': dict(
                self.env.context,
                form_view_initial_mode='edit',
            ),
        }

    def action_open_advance_material_releases(self):
        self.ensure_one()
        return self.env[
            'furniture.mrp.advance.material.release'
        ].action_open_for_production(self)

    def _get_active_advance_material_stage(self, stage_code):
        self.ensure_one()
        return self.env[
            'furniture.mrp.advance.material.release'
        ].find_active_stage_release(self, stage_code)

    def _has_issued_advance_materials(self, stage_code):
        self.ensure_one()
        stage = self._get_active_advance_material_stage(stage_code)
        return bool(stage and stage.state in ('issued', 'started'))


class FurnitureMrpStoreRequestAdvanceMaterialRelease(models.Model):
    _inherit = 'furniture.mrp.store.request'

    @api.model
    def _advance_release_status_action(self, advance_stage):
        # A stage supervisor may read only the scoped stage/lines, never the
        # aggregate envelope.  Read the immutable identifier under sudo for
        # the notification, then rebuild a normal-user record only for roles
        # that are explicitly allowed to open the complete release.
        release = advance_stage.sudo().release_id
        issued = advance_stage.state in ('issued', 'started')
        received = bool(issued and advance_stage.receipt_confirmed)
        params = {
            'title': (
                (_('خامات المرحلة مستلمة وجاهزة') if received else
                 _('الخامات بانتظار استلام الإنتاج'))
                if issued else _('إذن صرف مبكر بانتظار المخزن')
            ),
            'message': (
                (_('تم استلام خامات المرحلة ضمن الإذن %s؛ يمكنك بدء المرحلة.')
                 if received else
                 _('صرف المخزن خامات المرحلة ضمن الإذن %s؛ '
                   'يجب تسجيل الاستلام الفعلي قبل البدء.'))
                if issued else
                _('المرحلة موجودة ضمن الإذن %s وبانتظار أمين المخزن.')
            ) % release.name,
            'type': 'success' if received else 'warning',
            'sticky': False,
        }
        user = self.env.user
        if (
            self.env.is_superuser()
            or user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
            or user.has_group('furniture_mrp.group_furniture_mrp_storekeeper')
        ):
            params['next'] = self.env[release._name].browse(
                release.id
            ).action_open()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }

    @api.model
    def _create_pending_request(self, vals, material_buckets):
        production = vals.get('production_id')
        if isinstance(production, int):
            production = self.env['furniture.mrp.production'].browse(production)
        production = production.exists() if production else production
        stage_code = vals.get('stage_code')
        if production and stage_code:
            # Share the production-row mutex with advance-release creation.
            # This closes the two-tab race where a legacy and a consolidated
            # request could otherwise both pass their duplicate checks.
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
                [production.id],
            )
            advance_stage = self.env[
                'furniture.mrp.advance.material.release'
            ].find_active_stage_release(production, stage_code)
            if advance_stage:
                return self._advance_release_status_action(advance_stage)
        return super()._create_pending_request(vals, material_buckets)


class FurnitureMrpStageMixinAdvanceMaterialRelease(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    advance_material_release_stage_id = fields.Many2one(
        'furniture.mrp.advance.material.release.stage',
        string='صرف الخامات المبكر',
        compute='_compute_advance_material_release_status',
        readonly=True,
    )
    advance_material_release_id = fields.Many2one(
        'furniture.mrp.advance.material.release',
        string='إذن صرف الخامات المبكر',
        compute='_compute_advance_material_release_status',
        readonly=True,
    )
    advance_material_release_state = fields.Selection(
        [
            ('none', 'لا يوجد'),
            ('pending', 'بانتظار المخزن'),
            ('issued', 'الخامات مصروفة'),
            ('started', 'بدأ استخدام الخامات'),
        ],
        string='حالة الصرف المبكر',
        compute='_compute_advance_material_release_status',
        readonly=True,
    )
    advance_material_receipt_state = fields.Selection(
        RECEIPT_STATE_SELECTION,
        string='حالة استلام خامات المرحلة',
        compute='_compute_advance_material_release_status',
        readonly=True,
    )
    advance_material_receipt_confirmed = fields.Boolean(
        string='تم استلام خامات المرحلة من المخزن',
        compute='_compute_advance_material_release_status',
        readonly=True,
    )

    @api.depends('production_order_id')
    def _compute_advance_material_release_status(self):
        release_model = self.env['furniture.mrp.advance.material.release']
        for stage_order in self:
            stage_order.advance_material_release_stage_id = False
            stage_order.advance_material_release_id = False
            stage_order.advance_material_release_state = 'none'
            stage_order.advance_material_receipt_state = 'not_issued'
            stage_order.advance_material_receipt_confirmed = False
            production = stage_order.production_order_id
            if not production:
                continue
            stage_code = production._stage_model_to_code(stage_order._name)
            if not stage_code:
                continue
            advance_stage = release_model.find_active_stage_release(
                production, stage_code,
            )
            if advance_stage:
                stage_order.advance_material_release_stage_id = advance_stage
                stage_order.advance_material_release_id = advance_stage.release_id
                stage_order.advance_material_release_state = advance_stage.state
                stage_order.advance_material_receipt_state = (
                    advance_stage.receipt_state
                )
                stage_order.advance_material_receipt_confirmed = (
                    advance_stage.receipt_confirmed
                )

    def action_open_advance_material_release(self):
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        stage_code = (
            production._stage_model_to_code(self._name) if production else False
        )
        advance_stage = (
            self.env['furniture.mrp.advance.material.release']
            .find_active_stage_release(production, stage_code)
            if production and stage_code else False
        )
        if not advance_stage:
            raise UserError(_('لا يوجد إذن صرف مبكر نشط لهذه المرحلة.'))
        return self._open_scoped_advance_material_stage(advance_stage)

    def _open_scoped_advance_material_stage(self, advance_stage):
        """Expose only the caller's stage; managers keep the full envelope."""
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        stage_code = (
            production._stage_model_to_code(self._name) if production else False
        )
        if (
            not advance_stage
            or advance_stage.production_id != production
            or advance_stage.stage_code != stage_code
        ):
            raise AccessError(_('إذن الخامات لا يخص أمر المرحلة الحالي.'))
        user = self.env.user
        if (
            self.env.is_superuser()
            or user._is_admin()
            or user.has_group('furniture_mrp.group_furniture_mrp_manager')
        ):
            return advance_stage.release_id.action_open()
        if (
            advance_stage.state == 'issued'
            and not advance_stage.receipt_confirmed
        ):
            return advance_stage.action_open_receipt_wizard()
        return self.env[
            'furniture.mrp.store.request'
        ]._advance_release_status_action(advance_stage)

    def action_open_advance_material_receipt(self):
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        stage_code = (
            production._stage_model_to_code(self._name) if production else False
        )
        advance_stage = (
            self.env['furniture.mrp.advance.material.release']
            .find_active_stage_release(production, stage_code)
            if production and stage_code else False
        )
        if not advance_stage:
            normal_request = self._get_store_request(('approved',))
            if (
                normal_request
                and 'receipt_confirmed' in normal_request._fields
                and normal_request.material_line_ids.mapped('issue_move_ids')
                and not normal_request.receipt_confirmed
            ):
                return normal_request.action_open_receipt_wizard()
            raise UserError(_('لا يوجد إذن خامات بانتظار استلام هذه المرحلة.'))
        return advance_stage.action_open_receipt_wizard()

    @api.depends('production_order_id', 'state')
    def _compute_store_request_status(self):
        super()._compute_store_request_status()
        release_model = self.env['furniture.mrp.advance.material.release']
        for stage_order in self:
            production = stage_order.production_order_id
            stage_code = (
                production._stage_model_to_code(stage_order._name)
                if production else False
            )
            advance_stage = (
                release_model.find_active_stage_release(production, stage_code)
                if production and stage_code else False
            )
            if advance_stage:
                # ``store_request_id`` points to the legacy request model, so
                # keep it empty and expose the compatible state only.  Existing
                # views then show Start after issue without fabricating a
                # legacy request or duplicating the material pull.
                stage_order.store_request_id = False
                if advance_stage.state in ('issued', 'started'):
                    stage_order.store_request_state = (
                        'approved'
                        if advance_stage.receipt_confirmed
                        else 'awaiting_receipt'
                    )
                else:
                    stage_order.store_request_state = 'pending'

    def action_open_store_request(self):
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        stage_code = (
            production._stage_model_to_code(self._name) if production else False
        )
        advance_stage = (
            self.env['furniture.mrp.advance.material.release']
            .find_active_stage_release(production, stage_code)
            if production and stage_code else False
        )
        if advance_stage:
            return self._open_scoped_advance_material_stage(advance_stage)
        return super().action_open_store_request()

    def action_request_store_approval(self):
        self.ensure_one()
        self._check_stage_operation_access()
        production = self.production_order_id
        stage_code = (
            production._stage_model_to_code(self._name) if production else False
        )
        advance_stage = (
            self.env['furniture.mrp.advance.material.release']
            .find_active_stage_release(production, stage_code)
            if production and stage_code else False
        )
        if advance_stage:
            return self._open_scoped_advance_material_stage(advance_stage)
        return super().action_request_store_approval()

    def action_start(self):
        self._check_stage_operation_access()
        if self.env.context.get('furniture_product_batch_material_scope'):
            # The product-batch controller already locked the aggregate release,
            # validated the exact production rows and staged only their material
            # allocation.  Running the legacy stage-wide check here would require
            # the sofa and chaise to be received together again.
            return super().action_start()
        if len(self) == 1 and self.production_order_id:
            stage_code = self.production_order_id._stage_model_to_code(self._name)
            advance_stage = self.env[
                'furniture.mrp.advance.material.release'
            ].find_active_stage_release(self.production_order_id, stage_code)
            if advance_stage:
                advance_stage.release_id._lock()
                advance_stage.invalidate_recordset(['state'])
                advance_stage.release_id.invalidate_recordset(['state'])
                if advance_stage.state == 'pending':
                    return self.env[
                        'furniture.mrp.store.request'
                    ]._advance_release_status_action(advance_stage)
                if advance_stage.state not in ('issued', 'started'):
                    raise UserError(_(
                        'إذن صرف خامات المرحلة لم يعد صالحًا للبدء. '
                        'أغلق الشاشة وافتح المرحلة من جديد.'
                    ))
                if not advance_stage.receipt_confirmed:
                    raise UserError(_(
                        'الخامات ما زالت في عهدة التسليم. '
                        'اضغط «استلام من المخزن» وسجل الكمية الفعلية أولًا.'
                    ))
                advance_stage._validate_current_snapshot(relink_sources=True)
                advance_stage._relink_issued_moves()
            if advance_stage and advance_stage.state in ('issued', 'started'):
                # Keep the normal material movement path enabled.  Its exact
                # material-line links recognise the already staged moves, while
                # any genuinely new/unlinked material still gets pulled.
                records = self.with_context(
                    furniture_storekeeper_approval_bypass=True,
                    furniture_advance_material_release_stage_id=advance_stage.id,
                )
                result = super(
                    FurnitureMrpStageMixinAdvanceMaterialRelease,
                    records,
                ).action_start()
                self.invalidate_recordset(['state'])
                if self.state != 'pending' and advance_stage.state == 'issued':
                    advance_stage.sudo().write({'state': 'started'})
                return result
        return super().action_start()
