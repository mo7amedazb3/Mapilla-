# -*- coding: utf-8 -*-
import hashlib
import json
import math

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


SPECIAL_KIT_STAGES = ('tailoring', 'upholstery')


class FurnitureMrpProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    kit_plan_locked = fields.Boolean(
        string='تم اعتماد تقسيمة الأطقم اليدوية',
        default=False,
        copy=True,
        readonly=True,
        help=(
            'عند تفعيلها لا يحول النظام المنتجات المتبقية تلقائيًا إلى أطقم أخرى؛ '
            'تظل المنتجات غير المخصصة ظاهرة كمنتجات منفردة.'
        ),
    )
    kit_plan_revision = fields.Integer(
        string='إصدار تقسيمة الأطقم',
        default=0,
        copy=False,
        readonly=True,
    )
    kit_plan_editable = fields.Boolean(
        string='يمكن تعديل تقسيمة الأطقم',
        compute='_compute_kit_plan_editable',
        readonly=True,
    )
    kit_order_summary_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        string='ملخص أصناف أمر التشغيل',
        compute='_compute_kit_order_summary_lines',
        readonly=True,
        help=(
            'يعرض سطر الدفعة الأصلية مرة واحدة حتى لو قسّم مخطط الأطقم '
            'الكمية إلى سجلات قطع فنية للتفصيل والكسوة.'
        ),
    )
    kit_order_has_split_lines = fields.Boolean(
        string='توجد تفاصيل قطع فنية',
        compute='_compute_kit_order_summary_lines',
        readonly=True,
    )

    @api.depends(
        'kit_plan_locked',
        'production_line_ids.active',
        'production_line_ids.sequence',
        'production_line_ids.kit_family_origin_line_id',
        'production_line_ids.cost_origin_line_id',
    )
    def _compute_kit_order_summary_lines(self):
        Line = self.env['furniture.mrp.production.line']
        for production in self:
            active_lines = production.production_line_ids.filtered('active').sorted(
                lambda line: (line.sequence, line.id)
            )
            representatives = Line
            represented_root_ids = set()
            has_split_lines = False
            for line in active_lines:
                root = (
                    line.kit_family_origin_line_id
                    or line.cost_origin_line_id
                    or line
                )
                if line.kit_family_origin_line_id or line.cost_origin_line_id:
                    has_split_lines = True
                root_id = root.id or line.id
                if root_id in represented_root_ids:
                    continue
                representative = (
                    root
                    if root.active and root.production_id == production
                    else line
                )
                representatives |= representative
                represented_root_ids.add(root_id)
            production.kit_order_summary_line_ids = representatives
            production.kit_order_has_split_lines = bool(
                production.kit_plan_locked and has_split_lines
            )

    def _check_kit_planner_access(self):
        if self.env.is_superuser():
            return
        if not self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        ):
            raise AccessError(_('تقسيم الأطقم متاح لمدير الإنتاج فقط.'))

    def _kit_special_stage_started(self):
        """Return whether tailoring or upholstery started actual work."""
        self.ensure_one()
        return any(
            stage_order
            and (
                stage_order.date_start
                or stage_order.state in ('in_progress', 'quality_check', 'done')
            )
            for stage_order in (
                self.tailoring_order_id,
                self.upholstery_order_id,
            )
        )

    @api.depends(
        'state',
        'production_line_ids.active',
        'production_line_ids.use_tailoring',
        'production_line_ids.use_upholstery',
        'tailoring_order_id.state',
        'tailoring_order_id.date_start',
        'upholstery_order_id.state',
        'upholstery_order_id.date_start',
    )
    def _compute_kit_plan_editable(self):
        for production in self:
            has_special_lines = bool(production.production_line_ids.filtered(
                lambda line: (
                    line.active
                    and (line.use_tailoring or line.use_upholstery)
                )
            ))
            production.kit_plan_editable = bool(
                has_special_lines
                and production.state not in ('done', 'cancelled')
                and not production._kit_special_stage_started()
            )

    def _check_kit_planner_state(self):
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_(
                'لا يمكن تعديل تقسيمة الأطقم بعد إغلاق أو إلغاء أمر الإنتاج.'
            ))
        if self._kit_special_stage_started():
            raise UserError(_(
                'بدأ تشغيل مرحلة التفصيل أو الكسوة بالفعل؛ '
                'لا يمكن تغيير شكل الأطقم بعد بدء إحدى المرحلتين.'
            ))

    def _invalidate_locked_kit_plan(self):
        """Drop stale Kit identities when the production recipe is edited.

        Production-line images and quantities are deliberately preserved.  A
        fresh explicit plan can then be built from the edited source lines.
        """
        editable_plans = self.filtered(lambda production: (
            production.kit_plan_locked
            and production.state not in ('done', 'cancelled')
            and not production._kit_special_stage_started()
        ))
        for production in editable_plans:
            production.production_line_ids.with_context(
                furniture_skip_kit_plan_invalidation=True,
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
                furniture_skip_line_consolidation=True,
            ).write({
                'kit_bom_id': False,
                'kit_instance_number': 0,
            })
            production.sudo().with_context(
                furniture_skip_kit_plan_invalidation=True,
            ).write({
                'kit_plan_locked': False,
                'kit_plan_revision': production.kit_plan_revision + 1,
            })

    def _kit_planner_stage(self):
        self.ensure_one()
        if self.production_line_ids.filtered('use_tailoring'):
            return 'tailoring'
        return 'upholstery'

    def copy(self, default=None):
        # Child-line creation must not interpret an order copy as a manual
        # recipe change.  Piece work-images are intentionally copy=False on
        # production lines because they belong to the source order only.
        return super(
            FurnitureMrpProduction,
            self.with_context(furniture_skip_kit_plan_invalidation=True),
        ).copy(default)

    def action_open_kit_planner(self):
        self.ensure_one()
        self._check_kit_planner_access()
        self._check_kit_planner_state()
        special_lines = self.production_line_ids.filtered(
            lambda line: line.active and (line.use_tailoring or line.use_upholstery)
        )
        if not special_lines:
            raise UserError(_(
                'لا توجد أصناف تمر بمرحلة التفصيل أو الكسوة داخل أمر الإنتاج.'
            ))

        preview_stage = self._kit_planner_stage()
        wizard = self.env['furniture.mrp.stage.transfer.wizard'].create({
            'production_id': self.id,
            'source_stage': preview_stage,
            'target_stage': preview_stage,
            'prepared_source_stage': preview_stage,
            'view_only': True,
            'kit_planner_mode': True,
            'kit_plan_stage': preview_stage,
            'kit_plan_source_revision': self.kit_plan_revision,
        })
        wizard._initialize_kit_planner()
        return wizard._kit_planner_action()


class FurnitureMrpStageTransferWizard(models.TransientModel):
    _inherit = 'furniture.mrp.stage.transfer.wizard'

    kit_planner_mode = fields.Boolean(
        string='وضع تخطيط الأطقم',
        default=False,
        readonly=True,
        copy=False,
    )
    kit_plan_stage = fields.Selection(
        [
            ('tailoring', 'التفصيل'),
            ('upholstery', 'الكسوة'),
        ],
        string='معاينة خامات مرحلة',
        default='tailoring',
        required=False,
    )
    kit_plan_option_line_ids = fields.One2many(
        'furniture.mrp.kit.plan.option',
        'wizard_id',
        string='الأطقم المتاحة للتقسيم',
        copy=False,
    )
    kit_plan_warning = fields.Text(
        string='تنبيهات التقسيم',
        readonly=True,
        copy=False,
    )
    kit_plan_remaining_summary = fields.Text(
        string='المنتجات المتبقية خارج الأطقم',
        readonly=True,
        copy=False,
    )
    kit_plan_existing_summary = fields.Text(
        string='الأطقم المحفوظة',
        readonly=True,
        copy=False,
    )
    kit_plan_preview_signature = fields.Char(
        string='بصمة المعاينة',
        readonly=True,
        copy=False,
    )
    kit_plan_source_revision = fields.Integer(
        string='إصدار التقسيمة عند فتح الشاشة',
        readonly=True,
        copy=False,
    )
    kit_plan_has_saved_groups = fields.Boolean(
        string='توجد تقسيمة محفوظة',
        readonly=True,
        copy=False,
    )

    def _kit_planner_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('تقسيم الأطقم ومعاينة التفصيل والكسوة'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'furniture_mrp.view_furniture_mrp_kit_planner_form'
            ).id,
            'target': 'new',
            'context': dict(self.env.context, form_view_initial_mode='edit'),
        }

    def _check_kit_planner(self):
        self.ensure_one()
        if not self.kit_planner_mode or not self.production_id:
            raise UserError(_('هذه العملية متاحة من شاشة تخطيط الأطقم فقط.'))
        self.production_id._check_kit_planner_access()
        self.production_id._check_kit_planner_state()

    def _kit_planner_lines(self):
        self.ensure_one()
        return self.production_id.production_line_ids.filtered(lambda line: (
            line.active
            and line.product_id
            and float_compare(line.product_qty or 0.0, 0.0, precision_digits=3) > 0
            and (line.use_tailoring or line.use_upholstery)
        )).sorted(lambda line: (line.sequence, line.id))

    def _kit_planner_line_model(self, line):
        return line.furniture_order_model_id or line.product_id.furniture_model_id

    def _kit_planner_bucket_key(self, line):
        return (
            self._kit_planner_line_model(line).id,
            line.buyer_partner_id.id,
            line.beneficiary_partner_id.id,
        )

    def _kit_planner_bucket_label(self, model, buyer, beneficiary):
        labels = [model.display_name if model else _('بدون موديل')]
        if buyer:
            labels.append(_('المشتري: %s') % buyer.display_name)
        if beneficiary:
            labels.append(_('المستفيد: %s') % beneficiary.display_name)
        return ' | '.join(labels)

    def _kit_planner_company_boms(self):
        self.ensure_one()
        domain = [
            ('type', '=', 'phantom'),
            ('bom_line_ids', '!=', False),
        ]
        if self.production_id.company_id:
            domain += [
                '|',
                ('company_id', '=', False),
                ('company_id', '=', self.production_id.company_id.id),
            ]
        return self.env['mrp.bom'].search(domain, order='id')

    def _kit_planner_items(self):
        self.ensure_one()
        items = []
        for line in self._kit_planner_lines():
            display_product = (
                self.production_id._find_dimensioned_product_for_line(line)
                or line.product_id
            )
            items.append({
                'line': line,
                'product': display_product,
                'product_key': self._stage_transfer_product_key(line.product_id),
                'qty': line.product_qty,
                'remaining_qty': line.product_qty,
                'model': self._kit_planner_line_model(line),
                'buyer': line.buyer_partner_id,
                'beneficiary': line.beneficiary_partner_id,
                'fixed': bool(line.kit_bom_id and line.kit_instance_number),
            })
        return items

    def _kit_planner_requirement_summary(self, requirements):
        values = []
        for data in sorted(
            requirements.values(),
            key=lambda item: item['product'].display_name.casefold(),
        ):
            qty_label = ('%.3f' % data['qty']).rstrip('0').rstrip('.')
            values.append('%s × %s' % (data['product'].display_name, qty_label))
        return ' + '.join(values)

    def _prepare_kit_plan_option_commands(self, items):
        self.ensure_one()
        unassigned = [item for item in items if not item['fixed']]
        buckets = {}
        for item in unassigned:
            key = (item['model'].id, item['buyer'].id, item['beneficiary'].id)
            buckets.setdefault(key, []).append(item)

        commands = []
        sequence = 10
        for key, bucket_items in sorted(
            buckets.items(),
            key=lambda pair: (
                (pair[1][0]['model'].name or _('بدون موديل')).casefold(),
                (pair[1][0]['buyer'].display_name or '').casefold(),
                (pair[1][0]['beneficiary'].display_name or '').casefold(),
            ),
        ):
            model = bucket_items[0]['model']
            buyer = bucket_items[0]['buyer']
            beneficiary = bucket_items[0]['beneficiary']
            if not model:
                continue
            available_by_product = {}
            for item in bucket_items:
                available_by_product[item['product_key']] = (
                    available_by_product.get(item['product_key'], 0.0) + item['qty']
                )
            for kit_bom in self._kit_planner_company_boms().filtered(
                lambda bom: bom.furniture_model_id == model
            ):
                requirements = self._kit_component_requirements(kit_bom)
                if not requirements or not set(requirements).issubset(available_by_product):
                    continue
                possible_counts = [
                    available_by_product[product_key] / data['qty']
                    for product_key, data in requirements.items()
                    if data['qty'] > 0
                ]
                max_count = int(math.floor(min(possible_counts) + 1e-7)) if possible_counts else 0
                if max_count <= 0:
                    continue
                kit_product = kit_bom.furniture_product_id or kit_bom.product_id
                commands.append((0, 0, {
                    'sequence': sequence,
                    'furniture_model_id': model.id,
                    'buyer_partner_id': buyer.id,
                    'beneficiary_partner_id': beneficiary.id,
                    'bucket_label': self._kit_planner_bucket_label(
                        model, buyer, beneficiary,
                    ),
                    'kit_bom_id': kit_bom.id,
                    'kit_display_name': (
                        kit_product.display_name
                        if kit_product else kit_bom.product_tmpl_id.display_name
                    ),
                    'component_summary': self._kit_planner_requirement_summary(
                        requirements,
                    ),
                    'max_kit_qty': max_count,
                    'requested_kit_qty': 0,
                }))
                sequence += 10
        return commands

    def _kit_planner_route_excluded_required_product_names(self, items):
        """Name components that only miss a Kit because of their stage route."""
        self.ensure_one()
        buckets = {}

        for item in items:
            if item['fixed'] or not item['model']:
                continue
            key = (
                item['model'].id,
                item['buyer'].id,
                item['beneficiary'].id,
            )
            bucket = buckets.setdefault(key, {
                'model': item['model'],
                'eligible': {},
                'excluded': {},
                'excluded_lines': {},
            })
            product_key = item['product_key']
            bucket['eligible'][product_key] = (
                bucket['eligible'].get(product_key, 0.0) + item['qty']
            )

        excluded_lines = self.production_id.production_line_ids.filtered(
            lambda line: (
                line.active
                and line.product_id
                and float_compare(
                    line.product_qty or 0.0,
                    0.0,
                    precision_digits=3,
                ) > 0
                and not line.use_tailoring
                and not line.use_upholstery
                and not (line.kit_bom_id and line.kit_instance_number)
            )
        )
        for line in excluded_lines:
            model = self._kit_planner_line_model(line)
            if not model:
                continue
            key = (
                model.id,
                line.buyer_partner_id.id,
                line.beneficiary_partner_id.id,
            )
            bucket = buckets.setdefault(key, {
                'model': model,
                'eligible': {},
                'excluded': {},
                'excluded_lines': {},
            })
            product_key = self._stage_transfer_product_key(line.product_id)
            bucket['excluded'][product_key] = (
                bucket['excluded'].get(product_key, 0.0) + line.product_qty
            )
            bucket['excluded_lines'].setdefault(
                product_key,
                self.env['furniture.mrp.production.line'],
            )
            bucket['excluded_lines'][product_key] |= line

        names = set()
        kit_boms = self._kit_planner_company_boms()
        for bucket in buckets.values():
            if not bucket['excluded']:
                continue
            available = dict(bucket['eligible'])
            for product_key, qty in bucket['excluded'].items():
                available[product_key] = available.get(product_key, 0.0) + qty

            for kit_bom in kit_boms.filtered(
                lambda bom: bom.furniture_model_id == bucket['model']
            ):
                requirements = self._kit_component_requirements(kit_bom)
                if not requirements:
                    continue
                if any(
                    float_compare(
                        available.get(product_key, 0.0),
                        data['qty'],
                        precision_digits=6,
                    ) < 0
                    for product_key, data in requirements.items()
                ):
                    continue
                if all(
                    float_compare(
                        bucket['eligible'].get(product_key, 0.0),
                        data['qty'],
                        precision_digits=6,
                    ) >= 0
                    for product_key, data in requirements.items()
                ):
                    continue
                for product_key, data in requirements.items():
                    if float_compare(
                        bucket['eligible'].get(product_key, 0.0),
                        data['qty'],
                        precision_digits=6,
                    ) >= 0:
                        continue
                    names.update(
                        line.product_id.display_name
                        for line in bucket['excluded_lines'].get(
                            product_key,
                            self.env['furniture.mrp.production.line'],
                        )
                    )
        return sorted(names, key=str.casefold)

    def _kit_planner_warning_text(self, items, option_commands):
        warnings = []
        missing_model = [item for item in items if not item['model']]
        if missing_model:
            names = sorted({item['line'].product_id.display_name for item in missing_model})
            warnings.append(_(
                'حدد الموديل للأصناف التالية حتى تظهر وصفات الـKit المناسبة: %s'
            ) % '، '.join(names))
        route_excluded_names = (
            self._kit_planner_route_excluded_required_product_names(items)
        )
        if route_excluded_names:
            warnings.append(_(
                'الأصناف التالية مطلوبة لوصفات Kit مطابقة، لكنها غير مفعلة '
                'في التفصيل أو الكسوة: %s. فعّل مرحلة التفصيل أو الكسوة لهذه '
                'الأصناف حتى تظهر اختيارات الـKit المناسبة.'
            ) % '، '.join(route_excluded_names))
        if (
            not option_commands
            and not route_excluded_names
            and any(not item['fixed'] and item['model'] for item in items)
        ):
            warnings.append(_(
                'لا توجد وصفة Kit مكتملة تطابق الموديل والكميات الحالية. '
                'ستظهر الأصناف غير المخصصة كمنتجات منفردة.'
            ))
        return '\n'.join(warnings) or False

    def _kit_planner_existing_groups(self, items):
        self.ensure_one()
        groups = {}
        for item in items:
            if not item['fixed']:
                continue
            line = item['line']
            token = 'kit:%s:%s:%s' % (
                self.production_id.id,
                line.kit_bom_id.id,
                line.kit_instance_number,
            )
            group = groups.setdefault(token, {
                'token': token,
                'kit_bom': line.kit_bom_id,
                'instance_number': line.kit_instance_number,
                'model': item['model'],
                'buyer': item['buyer'],
                'beneficiary': item['beneficiary'],
                'allocations': [],
                'loose': False,
                'fixed': True,
            })
            if (
                group['model'] != item['model']
                or group['buyer'] != item['buyer']
                or group['beneficiary'] != item['beneficiary']
            ):
                raise UserError(_(
                    'بيانات الموديل أو المشتري أو المستفيد غير متطابقة داخل الطقم %s رقم %s.'
                ) % (line.kit_bom_id.display_name, line.kit_instance_number))
            group['allocations'].append((item, item['qty']))
            item['remaining_qty'] = 0.0
        return list(groups.values())

    def _kit_planner_option_key(self, option):
        return (
            option.furniture_model_id.id,
            option.buyer_partner_id.id,
            option.beneficiary_partner_id.id,
        )

    def _validate_kit_plan_options(self, items):
        self.ensure_one()
        buckets = {}
        for item in items:
            if item['fixed']:
                continue
            key = (item['model'].id, item['buyer'].id, item['beneficiary'].id)
            bucket = buckets.setdefault(key, {'items': [], 'available': {}})
            bucket['items'].append(item)
            bucket['available'][item['product_key']] = (
                bucket['available'].get(item['product_key'], 0.0) + item['qty']
            )

        demand_by_bucket = {}
        requirements_by_option = {}
        for option in self.kit_plan_option_line_ids.filtered(
            lambda line: line.requested_kit_qty > 0
        ).sorted(lambda line: (line.sequence, line.kit_bom_id.id, line.id)):
            if option.requested_kit_qty != int(option.requested_kit_qty):
                raise ValidationError(_('عدد الأطقم لازم يكون رقمًا صحيحًا.'))
            key = self._kit_planner_option_key(option)
            bucket = buckets.get(key)
            if not bucket:
                raise UserError(_('مجموعة المنتجات الخاصة بالسطر لم تعد موجودة. حدّث المعاينة.'))
            kit_bom = option.kit_bom_id
            if not kit_bom.active or kit_bom.type != 'phantom':
                raise UserError(_('وصفة %s لم تعد Kit نشطة.') % kit_bom.display_name)
            if kit_bom.furniture_model_id != option.furniture_model_id:
                raise UserError(_('موديل وصفة %s لا يطابق مجموعة المنتجات.') % kit_bom.display_name)
            if kit_bom.company_id and kit_bom.company_id != self.production_id.company_id:
                raise UserError(_('وصفة %s تتبع شركة مختلفة.') % kit_bom.display_name)
            requirements = self._kit_component_requirements(kit_bom)
            if not requirements:
                raise UserError(_('وصفة %s لا تحتوي منتجات صالحة للتقسيم.') % kit_bom.display_name)
            requirements_by_option[option.id] = requirements
            bucket_demand = demand_by_bucket.setdefault(key, {})
            for product_key, data in requirements.items():
                bucket_demand[product_key] = (
                    bucket_demand.get(product_key, 0.0)
                    + (data['qty'] * option.requested_kit_qty)
                )

        shortages = []
        for key, product_demand in demand_by_bucket.items():
            bucket = buckets[key]
            for product_key, required_qty in product_demand.items():
                available_qty = bucket['available'].get(product_key, 0.0)
                if float_compare(required_qty, available_qty, precision_digits=6) <= 0:
                    continue
                product = self.env['product.product'].browse(product_key)
                shortages.append(_('%s: المطلوب %s والمتاح %s') % (
                    product.display_name,
                    ('%.3f' % required_qty).rstrip('0').rstrip('.'),
                    ('%.3f' % available_qty).rstrip('0').rstrip('.'),
                ))
        if shortages:
            raise UserError(_(
                'التقسيمة المطلوبة أكبر من كميات أمر الإنتاج:\n%s\n'
                'لم يتم تغيير أو تقسيم أي سطر.'
            ) % '\n'.join(shortages))
        return buckets, requirements_by_option

    def _kit_planner_next_numbers(self):
        existing = self.env['furniture.mrp.production.line'].with_context(
            active_test=False,
        ).search([
            ('production_id', '=', self.production_id.id),
            ('kit_bom_id', '!=', False),
            ('kit_instance_number', '>', 0),
        ])
        numbers = {}
        for line in existing:
            numbers[line.kit_bom_id.id] = max(
                numbers.get(line.kit_bom_id.id, 0),
                line.kit_instance_number,
            )
        return numbers

    def _allocate_kit_plan_qty(self, bucket_items, product_key, qty, product):
        allocations = []
        unit_category = self.env.ref('uom.product_uom_categ_unit', raise_if_not_found=False)
        is_whole_unit_qty = (
            unit_category
            and product.uom_id.category_id == unit_category
            and abs(qty - round(qty)) < 1e-7
        )
        chunks = [1.0] * int(round(qty)) if is_whole_unit_qty else [qty]
        for chunk in chunks:
            qty_left = chunk
            for item in bucket_items:
                if item['product_key'] != product_key:
                    continue
                take_qty = min(item['remaining_qty'], qty_left)
                if float_compare(take_qty, 0.0, precision_digits=6) <= 0:
                    continue
                allocations.append((item, take_qty))
                item['remaining_qty'] -= take_qty
                qty_left -= take_qty
                if float_compare(qty_left, 0.0, precision_digits=6) <= 0:
                    break
            if float_compare(qty_left, 0.0, precision_digits=6) > 0:
                raise UserError(_('تغيّرت كميات المنتجات أثناء تجهيز المعاينة. افتحها من جديد.'))
        return allocations

    def _build_kit_plan_groups(self):
        self.ensure_one()
        items = self._kit_planner_items()
        groups = self._kit_planner_existing_groups(items)
        buckets, requirements_by_option = self._validate_kit_plan_options(items)
        next_numbers = self._kit_planner_next_numbers()

        for option in self.kit_plan_option_line_ids.filtered(
            lambda line: line.requested_kit_qty > 0
        ).sorted(lambda line: (line.sequence, line.kit_bom_id.id, line.id)):
            key = self._kit_planner_option_key(option)
            bucket_items = buckets[key]['items']
            requirements = requirements_by_option[option.id]
            for _index in range(option.requested_kit_qty):
                next_numbers[option.kit_bom_id.id] = (
                    next_numbers.get(option.kit_bom_id.id, 0) + 1
                )
                instance_number = next_numbers[option.kit_bom_id.id]
                allocations = []
                for product_key, data in sorted(requirements.items()):
                    allocations.extend(self._allocate_kit_plan_qty(
                        bucket_items,
                        product_key,
                        data['qty'],
                        data['product'],
                    ))
                groups.append({
                    'token': 'kit:%s:%s:%s' % (
                        self.production_id.id,
                        option.kit_bom_id.id,
                        instance_number,
                    ),
                    'kit_bom': option.kit_bom_id,
                    'instance_number': instance_number,
                    'model': option.furniture_model_id,
                    'buyer': option.buyer_partner_id,
                    'beneficiary': option.beneficiary_partner_id,
                    'allocations': allocations,
                    'loose': False,
                    'fixed': False,
                })

        loose_groups = {}
        for item in items:
            if float_compare(item['remaining_qty'], 0.0, precision_digits=6) <= 0:
                continue
            model_key = item['model'].id or 0
            group = loose_groups.setdefault(model_key, {
                'token': 'loose:%s' % model_key,
                'kit_bom': self.env['mrp.bom'],
                'instance_number': 0,
                'model': item['model'],
                'buyer': self.env['res.partner'],
                'beneficiary': self.env['res.partner'],
                'allocations': [],
                'loose': True,
                'fixed': False,
            })
            group['allocations'].append((item, item['remaining_qty']))
            item['remaining_qty'] = 0.0
        groups.extend(loose_groups.values())
        groups.sort(key=lambda group: (
            group['loose'],
            (
                (
                    group['kit_bom'].furniture_product_id
                    or group['kit_bom'].product_id
                    or group['kit_bom'].product_tmpl_id
                ).display_name
                if group['kit_bom']
                else _('منتجات منفردة')
            ).casefold(),
            group['instance_number'],
            (group['model'].name or _('بدون موديل')).casefold(),
        ))
        return groups

    def _kit_plan_line_commands(self, groups):
        commands = []
        summary = []
        remaining = []
        sequence = 10
        for group in groups:
            commands.append((0, 0, {
                'sequence': sequence,
                'is_model_header': True,
                'selected': False,
                # The Kit identity is shared by tailoring and upholstery.  The
                # compact stage viewers filter their own pieces later; this
                # planner must always show the complete saved Kit.
                'kit_plan_stage_visible': True,
                'model_group_label': self._kit_group_header_label(
                    group['kit_bom'],
                    group['instance_number'],
                    group['model'],
                    loose=group['loose'],
                ),
                'kit_group_token': group['token'],
                'kit_bom_id': group['kit_bom'].id if group['kit_bom'] else False,
                'kit_instance_number': group['instance_number'],
                'buyer_partner_id': group['buyer'].id,
                'beneficiary_partner_id': group['beneficiary'].id,
            }))
            sequence += 10
            # A Kit recipe can request more than one unit of the same product
            # (for example, two chairs).  The allocator intentionally works in
            # unit chunks, but the operator must see and save one compact row
            # with quantity 2, not two indistinguishable rows of quantity 1.
            # Keep separate source production lines separate; only merge the
            # repeated chunks that came from the exact same source line inside
            # this Kit instance.
            compact_allocations = []
            allocation_indexes = {}
            for item, allocated_qty in group['allocations']:
                allocation_key = item['line'].id
                existing_index = allocation_indexes.get(allocation_key)
                if existing_index is None:
                    allocation_indexes[allocation_key] = len(compact_allocations)
                    compact_allocations.append([item, allocated_qty])
                else:
                    compact_allocations[existing_index][1] += allocated_qty

            for item, allocated_qty in compact_allocations:
                line = item['line']
                product = item['product']
                row_buyer = item['buyer'] if group['loose'] else group['buyer']
                row_beneficiary = (
                    item['beneficiary'] if group['loose'] else group['beneficiary']
                )
                commands.append((0, 0, {
                    'sequence': sequence,
                    'product_id': product.id,
                    'product_uom_id': (product.uom_id or line.product_uom_id).id,
                    'selected': True,
                    'kit_plan_stage_visible': True,
                    'qty_to_transfer': allocated_qty,
                    'kit_component_qty': allocated_qty,
                    'kit_group_token': group['token'],
                    'kit_bom_id': group['kit_bom'].id if group['kit_bom'] else False,
                    'kit_instance_number': group['instance_number'],
                    'buyer_partner_id': row_buyer.id,
                    'beneficiary_partner_id': row_beneficiary.id,
                    'batch_image_1920': line.batch_image_1920 or False,
                    'kit_piece_note': line.kit_piece_note or False,
                    'first_stage_image_editable': True,
                    'source_production_id': self.production_id.id,
                    'source_production_line_id': line.id,
                }))
                sequence += 10
                qty_label = ('%.3f' % allocated_qty).rstrip('0').rstrip('.')
                summary.append('%s × %s' % (product.display_name, qty_label))
                if group['loose']:
                    remaining.append('%s × %s' % (
                        self.production_id._get_production_line_text_label(line),
                        qty_label,
                    ))
        return commands, summary, remaining

    def _kit_plan_signature(self):
        self.ensure_one()
        production_lines = self._kit_planner_lines()
        payload = {
            'production': self.production_id.id,
            'revision': self.production_id.kit_plan_revision,
            'stage': self.kit_plan_stage,
            'lines': [
                [
                    line.id,
                    line.write_date.isoformat() if line.write_date else '',
                    round(line.product_qty or 0.0, 6),
                    line.product_id.id,
                    self._kit_planner_line_model(line).id,
                    line.buyer_partner_id.id,
                    line.beneficiary_partner_id.id,
                    line.kit_bom_id.id,
                    line.kit_instance_number or 0,
                ]
                for line in production_lines
            ],
            'options': [
                [
                    line.sequence,
                    line.kit_bom_id.id,
                    line.furniture_model_id.id,
                    line.buyer_partner_id.id,
                    line.beneficiary_partner_id.id,
                    line.requested_kit_qty,
                ]
                for line in self.kit_plan_option_line_ids.sorted(
                    lambda item: (item.sequence, item.kit_bom_id.id, item.id)
                )
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _kit_plan_structure(self, commands=False, rows=False):
        if commands is not False:
            values = [command[2] for command in commands if command and command[0] == 0]
            return [(
                bool(value.get('is_model_header')),
                value.get('kit_group_token') or '',
                value.get('kit_bom_id') or 0,
                value.get('kit_instance_number') or 0,
                value.get('product_id') or 0,
                value.get('source_production_line_id') or 0,
                round(value.get('kit_component_qty') or 0.0, 6),
            ) for value in values]
        return [(
            bool(row.is_model_header),
            row.kit_group_token or '',
            row.kit_bom_id.id,
            row.kit_instance_number or 0,
            row.product_id.id,
            row.source_production_line_id.id,
            round(row.kit_component_qty or 0.0, 6),
        ) for row in rows.sorted(lambda line: (line.sequence, line.id))]

    def _refresh_kit_plan_preview(self, preserve_states=True):
        self.ensure_one()
        if not self._kit_planner_lines():
            raise UserError(_(
                'لا توجد منتجات تمر بمرحلة التفصيل أو الكسوة داخل أمر الإنتاج.'
            ))
        old_states = self._stage_transfer_line_states() if preserve_states else {}
        groups = self._build_kit_plan_groups()
        commands, summary, remaining = self._kit_plan_line_commands(groups)
        # Preserve only piece images. Quantities and selection are authoritative
        # planner output; restoring the old loose-row quantity after choosing a
        # Kit would silently overstate the remaining preview quantity.
        for command in commands:
            values = command[2]
            state_key = self._stage_transfer_line_state_key(values=values)
            matching_states = old_states.get(state_key) or []
            if matching_states:
                previous = matching_states.pop(0)
                values['batch_image_1920'] = previous.get('batch_image_1920')
                values['kit_piece_note'] = previous.get('kit_piece_note')
        values = {'line_ids': [(5, 0, 0)] + commands}
        existing_labels = [
            self._kit_group_header_label(
                group['kit_bom'], group['instance_number'], group['model'],
            )
            for group in groups
            if group.get('fixed') and group['kit_bom']
        ]
        self.write({
            'line_ids': values['line_ids'],
            'source_content_summary': '\n'.join(summary) or False,
            'kit_plan_remaining_summary': '\n'.join(remaining) or _('لا توجد منتجات متبقية خارج الأطقم.'),
            'kit_plan_existing_summary': '\n'.join(existing_labels) or False,
            'kit_plan_has_saved_groups': bool(existing_labels or self.production_id.kit_plan_locked),
        })
        self.kit_plan_preview_signature = self._kit_plan_signature()
        return commands

    def _initialize_kit_planner(self):
        self.ensure_one()
        self._check_kit_planner()
        items = self._kit_planner_items()
        option_commands = self._prepare_kit_plan_option_commands(items)
        self.write({
            'kit_plan_option_line_ids': [(5, 0, 0)] + option_commands,
            'kit_plan_warning': self._kit_planner_warning_text(items, option_commands),
            'kit_plan_has_saved_groups': self.production_id.kit_plan_locked,
        })
        self._refresh_kit_plan_preview(preserve_states=False)

    @api.onchange('production_id', 'source_stage', 'target_stage')
    def _onchange_source_stage(self):
        planner_records = self.filtered('kit_planner_mode')
        regular_records = self - planner_records
        if regular_records:
            super(FurnitureMrpStageTransferWizard, regular_records)._onchange_source_stage()
        for rec in planner_records:
            if rec.kit_plan_stage:
                rec.source_stage = rec.kit_plan_stage
                rec.target_stage = rec.kit_plan_stage
                rec.prepared_source_stage = rec.kit_plan_stage

    @api.onchange('kit_plan_stage')
    def _onchange_kit_plan_stage(self):
        for rec in self.filtered('kit_planner_mode'):
            rec.source_stage = rec.kit_plan_stage
            rec.target_stage = rec.kit_plan_stage
            rec.prepared_source_stage = rec.kit_plan_stage

    def action_refresh_kit_plan_preview(self):
        self.ensure_one()
        self._check_kit_planner()
        self.source_stage = self.kit_plan_stage
        self.target_stage = self.kit_plan_stage
        self.prepared_source_stage = self.kit_plan_stage
        self._refresh_kit_plan_preview(preserve_states=True)
        return self._kit_planner_action()

    def action_apply_kit_plan(self):
        self.ensure_one()
        self._check_kit_planner()
        production = self.production_id
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [production.id],
        )
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE production_id = %s AND active = TRUE ORDER BY id FOR UPDATE',
            [production.id],
        )
        production.invalidate_recordset(['state', 'kit_plan_revision'])
        production.tailoring_order_id.invalidate_recordset(['state', 'date_start'])
        production.upholstery_order_id.invalidate_recordset(['state', 'date_start'])
        production.production_line_ids.invalidate_recordset([
            'product_qty', 'kit_bom_id', 'kit_instance_number', 'write_date',
        ])
        production._check_kit_planner_state()
        if production.kit_plan_revision != self.kit_plan_source_revision:
            raise UserError(_(
                'تم تعديل تقسيمة الأطقم من شاشة أخرى. اقفل المعاينة وافتحها من جديد.'
            ))
        if not self.kit_plan_preview_signature:
            raise UserError(_('اضغط «تحديث المعاينة» قبل حفظ التقسيمة.'))
        if self._kit_plan_signature() != self.kit_plan_preview_signature:
            raise UserError(_(
                'الكميات أو اختيارات الأطقم اتغيرت بعد آخر معاينة. '
                'اضغط «تحديث المعاينة» ثم راجع الشكل قبل الحفظ.'
            ))

        groups = self._build_kit_plan_groups()
        expected_commands, _summary, _remaining = self._kit_plan_line_commands(groups)
        if self._kit_plan_structure(commands=expected_commands) != self._kit_plan_structure(
            rows=self.line_ids,
        ):
            raise UserError(_(
                'تفاصيل المعاينة لم تصل كاملة. افتح الشاشة من جديد؛ لم يتم تغيير أي سطر.'
            ))

        materializer = self.with_context(
            furniture_skip_kit_plan_invalidation=True,
        )
        materializer._propagate_transfer_group_partners()
        materializer._materialize_kit_transfer_lines()
        production.sudo().write({
            'kit_plan_locked': True,
            'kit_plan_revision': production.kit_plan_revision + 1,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('تم حفظ تقسيمة الأطقم'),
                'message': _(
                    'تم تثبيت الأطقم والصور وملاحظات القطع على دفعات أمر الإنتاج. '
                    'يمكنك تعديل خامات الريسيبي من زر «الأقمشة والتكاوي» '
                    'المستقل داخل أمر الإنتاج.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_reset_kit_plan(self):
        self.ensure_one()
        self._check_kit_planner()
        production = self.production_id
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [production.id],
        )
        production._check_kit_planner_state()
        production.production_line_ids.with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'kit_bom_id': False,
            'kit_instance_number': 0,
        })
        production.sudo().write({
            'kit_plan_locked': False,
            'kit_plan_revision': production.kit_plan_revision + 1,
        })
        production._refresh_material_lines_for_stage_plan()
        return production.action_open_kit_planner()


class FurnitureMrpProductionLine(models.Model):
    _inherit = 'furniture.mrp.production.line'

    kit_order_summary_qty = fields.Float(
        string='الكمية الإجمالية',
        compute='_compute_kit_order_summary_values',
        digits=(16, 3),
        readonly=True,
        help='إجمالي كمية سطر الأصل وكل سجلات القطع الفنية الناتجة عنه.',
    )
    kit_order_summary_material_cost = fields.Float(
        string='إجمالي الخامات المعتمدة',
        compute='_compute_kit_order_summary_values',
        digits=(16, 2),
        readonly=True,
    )

    @api.depends(
        'active',
        'product_qty',
        'material_cost',
        'kit_family_origin_line_id',
        'cost_origin_line_id',
        'production_id.production_line_ids.active',
        'production_id.production_line_ids.product_qty',
        'production_id.production_line_ids.material_cost',
        'production_id.production_line_ids.kit_family_origin_line_id',
        'production_id.production_line_ids.cost_origin_line_id',
    )
    def _compute_kit_order_summary_values(self):
        for line in self:
            root = (
                line.kit_family_origin_line_id
                or line.cost_origin_line_id
                or line
            )
            family = line.production_id.production_line_ids.filtered(
                lambda candidate: (
                    candidate.active
                    and (
                        candidate == root
                        or candidate.kit_family_origin_line_id == root
                        or candidate.cost_origin_line_id == root
                    )
                )
            )
            if not family:
                family = line
            line.kit_order_summary_qty = sum(family.mapped('product_qty'))
            line.kit_order_summary_material_cost = sum(family.mapped('material_cost'))

    @api.model
    def _kit_plan_structural_fields(self):
        return {
            'active', 'production_id', 'product_id', 'product_uom_id',
            'bom_id', 'product_qty', 'furniture_order_model_id',
            'buyer_partner_id', 'beneficiary_partner_id',
            'width_cm', 'depth_cm', 'height_cm',
            'use_priming', 'use_painting', 'use_carpentry', 'use_bases',
            'use_finishing', 'use_tailoring', 'use_sewing', 'use_upholstery',
            'use_packaging',
        }

    def _kit_plan_has_actual_structural_change(self, vals):
        structural_fields = self._kit_plan_structural_fields() & set(vals)
        if not structural_fields:
            return False
        for record in self:
            for field_name in structural_fields:
                field = record._fields[field_name]
                current = record[field_name]
                incoming = vals.get(field_name)
                if field.type == 'many2one':
                    incoming_id = getattr(incoming, 'id', incoming) or False
                    if (current.id or False) != incoming_id:
                        return True
                elif field.type in ('float', 'monetary'):
                    if float_compare(
                        current or 0.0,
                        incoming or 0.0,
                        precision_digits=6,
                    ) != 0:
                        return True
                elif field.type == 'boolean':
                    if bool(current) != bool(incoming):
                        return True
                elif current != incoming:
                    return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('furniture_skip_kit_plan_invalidation'):
            production_ids = {
                vals.get('production_id')
                for vals in vals_list
                if vals.get('production_id')
            }
            self.env['furniture.mrp.production'].browse(
                production_ids,
            )._invalidate_locked_kit_plan()
        return super().create(vals_list)

    def write(self, vals):
        skip_invalidation = self.env.context.get(
            'furniture_skip_kit_plan_invalidation'
        )
        structural_change = self._kit_plan_has_actual_structural_change(vals)
        if not skip_invalidation and structural_change:
            protected_lines = self.filtered(lambda line: (
                line.kit_bom_id
                and line.production_id.kit_plan_locked
                and (
                    line.production_id.state not in ('draft', 'confirmed')
                    or line.production_id.production_line_ids.filtered(
                        'first_stage_started'
                    )
                )
            ))
            if protected_lines:
                raise UserError(_(
                    'لا يمكن تعديل كمية أو بيانات قطعة داخل طقم محفوظ بعد بدء التصنيع. '
                    'أضف سطرًا جديدًا للكمية الإضافية بدل تغيير الدفعة الجارية.'
                ))
            self.mapped('production_id')._invalidate_locked_kit_plan()
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('furniture_skip_kit_plan_invalidation'):
            protected_lines = self.filtered(lambda line: (
                line.kit_bom_id
                and line.production_id.kit_plan_locked
                and (
                    line.production_id.state not in ('draft', 'confirmed')
                    or line.production_id.production_line_ids.filtered(
                        'first_stage_started'
                    )
                )
            ))
            if protected_lines:
                raise UserError(_(
                    'لا يمكن حذف قطعة من طقم محفوظ بعد بدء التصنيع.'
                ))
            self.mapped('production_id')._invalidate_locked_kit_plan()
        return super().unlink()


class FurnitureMrpStageTransferWizardLine(models.TransientModel):
    _inherit = 'furniture.mrp.stage.transfer.wizard.line'

    kit_plan_stage_visible = fields.Boolean(
        string='ظاهر في مرحلة معاينة الأطقم',
        default=True,
        readonly=True,
        copy=False,
    )


class FurnitureMrpKitPlanOption(models.TransientModel):
    _name = 'furniture.mrp.kit.plan.option'
    _description = 'اختيار نوع وعدد الأطقم في أمر الإنتاج'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.transfer.wizard',
        string='معاينة الأطقم',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    furniture_model_id = fields.Many2one(
        'furniture.product.model',
        string='الموديل',
        required=True,
        readonly=True,
    )
    buyer_partner_id = fields.Many2one(
        'res.partner',
        string='المشتري',
        readonly=True,
    )
    beneficiary_partner_id = fields.Many2one(
        'res.partner',
        string='المستفيد',
        readonly=True,
    )
    bucket_label = fields.Char(string='مجموعة المنتجات', readonly=True)
    kit_bom_id = fields.Many2one(
        'mrp.bom',
        string='الـKit',
        required=True,
        readonly=True,
        domain=[('type', '=', 'phantom')],
    )
    kit_display_name = fields.Char(string='اسم الطقم', readonly=True)
    component_summary = fields.Char(string='مكونات الطقم', readonly=True)
    max_kit_qty = fields.Integer(string='أقصى عدد منفرد', readonly=True)
    requested_kit_qty = fields.Integer(string='العدد المطلوب', default=0, required=True)

    @api.constrains('requested_kit_qty')
    def _check_requested_kit_qty(self):
        for rec in self:
            if rec.requested_kit_qty < 0:
                raise ValidationError(_('عدد الأطقم لا يمكن أن يكون سالبًا.'))
