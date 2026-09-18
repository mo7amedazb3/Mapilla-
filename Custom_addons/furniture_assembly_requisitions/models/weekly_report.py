import math
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, Command, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare

from odoo.addons.furniture_mrp.models.mrp_production_order import (
    FURNITURE_STAGE_FIELD_MAP,
)
from odoo.addons.furniture_mrp.models.mrp_stage_product_batch import (
    PRODUCT_BATCH_STAGE_CODES,
)

from .requisition import (
    SHARED_HALL_STAGES,
    STAGE_GROUPS,
    STAGE_HALL_XMLIDS,
    STAGE_LABELS,
)


ADMIN_GROUP = 'base.group_system'


class AssemblyWeeklyMaterialReport(models.Model):
    _name = 'furniture.assembly.weekly.material.report'
    _description = 'متابعة استهلاك خامات المراحل'
    _order = 'week_end desc, company_id, id desc'
    _check_company_auto = True

    name = fields.Char(string='التقرير', required=True, readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    week_start = fields.Date(
        string='من الجمعة', required=True, readonly=True,
        default=lambda self: self._week_dates()[0],
    )
    week_end = fields.Date(
        string='إلى الخميس', required=True, readonly=True,
        default=lambda self: self._week_dates()[1],
    )
    generated_at = fields.Datetime(
        string='آخر تحديث', readonly=True, copy=False,
    )
    line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='الخامات', copy=False,
    )
    priming_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='التقديم', domain=[('stage_code', '=', 'priming')],
    )
    painting_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='تصنيع الدهانات', domain=[('stage_code', '=', 'painting')],
    )
    carpentry_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='التجميع', domain=[('stage_code', '=', 'carpentry')],
    )
    bases_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='القواعد', domain=[('stage_code', '=', 'bases')],
    )
    finishing_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='التجهيز', domain=[('stage_code', '=', 'finishing')],
    )
    tailoring_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='التفصيل', domain=[('stage_code', '=', 'tailoring')],
    )
    upholstery_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='الكسوة', domain=[('stage_code', '=', 'upholstery')],
    )
    packaging_line_ids = fields.One2many(
        'furniture.assembly.weekly.material.report.line', 'report_id',
        string='التغليف', domain=[('stage_code', '=', 'packaging')],
    )
    line_count = fields.Integer(compute='_compute_summary')
    counted_line_count = fields.Integer(compute='_compute_summary')
    variance_line_count = fields.Integer(compute='_compute_summary')

    @api.depends_context('lang')
    def _compute_display_name(self):
        for report in self:
            report.display_name = _('متابعة الاستهلاك')

    _sql_constraints = [(
        'company_week_unique', 'unique(company_id, week_start)',
        'يوجد تقرير أسبوعي لهذه الشركة بالفعل.',
    )]

    @api.depends('line_ids.counted', 'line_ids.variance_qty')
    def _compute_summary(self):
        for report in self:
            report.line_count = len(report.line_ids)
            report.counted_line_count = len(report.line_ids.filtered('counted'))
            report.variance_line_count = len(report.line_ids.filtered(
                lambda line: line.counted and float_compare(
                    line.variance_qty, 0.0, precision_digits=3,
                ) != 0
            ))

    @api.model
    def _check_admin(self):
        if not self.env.is_superuser() and not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError(_('متابعة الاستهلاك متاحة لمسؤولي النظام فقط.'))

    @api.model
    def _inventory_access_profile(self, company):
        """Return the server-enforced inventory scope for the current user."""
        user = self.env.user
        is_admin = self.env.is_superuser() or user.has_group(ADMIN_GROUP)
        is_supervisor = user.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor'
        )
        if not is_admin and not is_supervisor:
            raise AccessError(_(
                'متابعة الجرد متاحة لمسؤولي النظام ومشرفي المراحل فقط.'
            ))

        allowed_stage_codes = list(SHARED_HALL_STAGES)
        if not is_admin:
            policies = self.env[
                'furniture.assembly.supervisor.material'
            ].sudo().search([
                ('company_id', '=', company.id),
                ('supervisor_id', '=', user.id),
            ])
            policy_stage_codes = set(policies.mapped('stage_code'))
            allowed_stage_codes = [
                code for code in SHARED_HALL_STAGES
                if code in policy_stage_codes and user.has_group(STAGE_GROUPS[code])
            ]
        return {
            'is_admin': is_admin,
            'is_supervisor': is_supervisor and not is_admin,
            'allowed_stage_codes': allowed_stage_codes,
        }

    @api.model
    def _inventory_status(self, counted, variance_qty):
        if not counted:
            return 'pending'
        comparison = float_compare(
            variance_qty, 0.0, precision_digits=3,
        )
        if comparison < 0:
            return 'shortage'
        if comparison > 0:
            return 'surplus'
        return 'balanced'

    @api.model
    def _week_dates(self, target_date=None):
        target_date = fields.Date.to_date(target_date or fields.Date.context_today(self))
        friday = target_date - timedelta(days=(target_date.weekday() - 4) % 7)
        return friday, friday + timedelta(days=6)

    @api.model
    def _utc_bounds(self, company, start_date, end_date):
        timezone = pytz.timezone(company.partner_id.tz or self.env.user.tz or 'UTC')
        local_start = timezone.localize(datetime.combine(start_date, time.min))
        local_stop = timezone.localize(datetime.combine(end_date + timedelta(days=1), time.min))
        return (
            local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            local_stop.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_admin()
        prepared = []
        for vals in vals_list:
            values = dict(vals)
            start, end = self._week_dates(values.get('week_end') or values.get('week_start'))
            values['week_start'] = start
            values['week_end'] = end
            company = self.env['res.company'].browse(
                values.get('company_id') or self.env.company.id
            )
            values['company_id'] = company.id
            values['name'] = _('متابعة الاستهلاك %(start)s — %(end)s') % {
                'start': fields.Date.to_string(start),
                'end': fields.Date.to_string(end),
            }
            prepared.append(values)
        reports = super().create(prepared)
        reports.action_refresh()
        return reports

    def write(self, vals):
        self._check_admin()
        if set(vals) - {'generated_at'}:
            raise AccessError(_('بيانات فترة التقرير ثابتة بعد إنشائه.'))
        return super().write(vals)

    def unlink(self):
        self._check_admin()
        return super().unlink()

    @api.model
    def _move_stock_qty(self, move):
        product = move.product_id
        uom = move.product_uom or product.uom_id
        quantity = move.quantity or move.product_uom_qty
        if uom.category_id == product.uom_id.category_id:
            return uom._compute_quantity(quantity, product.uom_id, round=False)
        return quantity

    @api.model
    def _completed_line_quantities(self, company, stage_code, start_utc, stop_utc):
        quantities = {}
        captured_ids = set()
        if stage_code in PRODUCT_BATCH_STAGE_CODES:
            batches = self.env['furniture.mrp.stage.product.batch'].sudo().search([
                ('company_id', '=', company.id),
                ('stage_code', '=', stage_code),
                ('state', '=', 'done'),
                ('finished_at', '>=', start_utc),
                ('finished_at', '<', stop_utc),
            ])
            for member in batches.mapped('member_ids'):
                line_id = member.production_line_id.id
                quantities[line_id] = max(
                    quantities.get(line_id, 0.0), member.qty_snapshot or 0.0,
                )
                captured_ids.add(line_id)

        _use_field, order_field, _state_field = FURNITURE_STAGE_FIELD_MAP[stage_code]
        comodel = self.env['furniture.mrp.production']._fields[order_field].comodel_name
        stage_orders = self.env[comodel].sudo().search([
            ('production_order_id.company_id', '=', company.id),
            ('state', '=', 'done'),
            ('date_finish', '>=', start_utc),
            ('date_finish', '<', stop_utc),
        ])
        for stage_order in stage_orders:
            for line in stage_order._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            ).exists():
                if line.id not in captured_ids:
                    quantities[line.id] = line.product_qty or 0.0
                    captured_ids.add(line.id)
        return quantities

    def _automatic_values(self):
        self.ensure_one()
        start_utc, stop_utc = self._utc_bounds(
            self.company_id, self.week_start, self.week_end,
        )
        Policy = self.env['furniture.assembly.supervisor.material'].sudo()
        policies = Policy.search([('company_id', '=', self.company_id.id)])
        tracked = {
            (policy.supervisor_id.id, policy.stage_code, product.id): (
                policy, product
            )
            for policy in policies
            for product in policy.product_ids.filtered(
                lambda item: item.active and item.is_storable
            )
        }
        if not tracked:
            return {}

        stage_products = defaultdict(set)
        for _supervisor_id, stage_code, product_id in tracked:
            stage_products[stage_code].add(product_id)

        received = defaultdict(float)
        other_out = defaultdict(float)
        opening_history = defaultdict(float)
        Move = self.env['stock.move'].sudo()
        for stage_code, product_ids in stage_products.items():
            hall = self.env.ref(
                STAGE_HALL_XMLIDS[stage_code], raise_if_not_found=False,
            )
            if not hall:
                continue
            historical_moves = Move.search([
                ('company_id', '=', self.company_id.id),
                ('state', '=', 'done'),
                ('date', '<', start_utc),
                ('product_id', 'in', list(product_ids)),
                '|', ('location_id', '=', hall.id),
                     ('location_dest_id', '=', hall.id),
            ])
            for move in historical_moves:
                key = (stage_code, move.product_id.id)
                quantity = self._move_stock_qty(move)
                if move.location_dest_id == hall and move.location_id != hall:
                    opening_history[key] += quantity
                elif move.location_id == hall and move.location_dest_id != hall:
                    opening_history[key] -= quantity
            moves = Move.search([
                ('company_id', '=', self.company_id.id),
                ('state', '=', 'done'),
                ('date', '>=', start_utc),
                ('date', '<', stop_utc),
                ('product_id', 'in', list(product_ids)),
                '|', ('location_id', '=', hall.id),
                     ('location_dest_id', '=', hall.id),
            ])
            for move in moves:
                key = (stage_code, move.product_id.id)
                quantity = self._move_stock_qty(move)
                if move.location_dest_id == hall and move.location_id != hall:
                    received[key] += quantity
                elif (
                    move.location_id == hall
                    and move.location_dest_id != hall
                    and move.location_dest_id.usage != 'production'
                ):
                    other_out[key] += quantity

        recipe_used = defaultdict(float)
        completed_pieces = defaultdict(float)
        Material = self.env['furniture.mrp.material.line'].sudo()
        for stage_code, product_ids in stage_products.items():
            completed_qty = self._completed_line_quantities(
                self.company_id, stage_code, start_utc, stop_utc,
            )
            if not completed_qty:
                continue
            material_lines = Material.search([
                ('production_line_id', 'in', list(completed_qty)),
                ('stage', '=', stage_code),
                ('product_id', 'in', list(product_ids)),
                ('qty_needed', '>', 0),
            ])
            piece_keys = set()
            for material in material_lines:
                production_line = material.production_line_id
                completed = completed_qty.get(production_line.id, 0.0)
                ratio = (
                    completed / production_line.product_qty
                    if production_line.product_qty else 0.0
                )
                quantity = material.qty_needed * ratio
                product = material.product_id
                uom = material.product_uom_id or product.uom_id
                if uom.category_id == product.uom_id.category_id:
                    quantity = uom._compute_quantity(
                        quantity, product.uom_id, round=False,
                    )
                key = (stage_code, product.id)
                recipe_used[key] += quantity
                piece_key = (production_line.id, *key)
                if piece_key not in piece_keys:
                    completed_pieces[key] += completed
                    piece_keys.add(piece_key)

        values = {}
        Line = self.env['furniture.assembly.weekly.material.report.line'].sudo()
        for key, (policy, product) in tracked.items():
            supervisor_id, stage_code, product_id = key
            previous = Line.search([
                ('report_id.company_id', '=', self.company_id.id),
                ('report_week_end', '<', self.week_start),
                ('supervisor_id', '=', supervisor_id),
                ('stage_code', '=', stage_code),
                ('product_id', '=', product_id),
            ], order='report_week_end desc, id desc', limit=1)
            opening = (
                previous.actual_qty if previous and previous.counted
                else previous.theoretical_qty if previous
                else opening_history[(stage_code, product_id)]
            )
            stage_key = (stage_code, product_id)
            values[key] = {
                'supervisor_id': supervisor_id,
                'stage_code': stage_code,
                'product_id': product_id,
                'product_uom_id': product.uom_id.id,
                'opening_qty': opening,
                'received_qty': received[stage_key],
                'recipe_used_qty': recipe_used[stage_key],
                'other_out_qty': other_out[stage_key],
                'completed_piece_qty': completed_pieces[stage_key],
            }
        return values

    def get_period_materials(self, date_from, date_to):
        """Read an inclusive date range without changing a weekly inventory."""
        self.ensure_one()
        profile = self._inventory_access_profile(self.company_id)
        self.check_access('read')
        try:
            start = fields.Date.to_date(date_from)
            end = fields.Date.to_date(date_to)
        except (TypeError, ValueError):
            raise ValidationError(_('أدخل تاريخ بداية ونهاية صحيحين.'))
        if not start or not end or start > end or end.year >= 9999:
            raise ValidationError(_('تاريخ البداية يجب أن يكون قبل تاريخ النهاية أو مساويًا له.'))
        inventory_period = start == self.week_start and end == self.week_end
        if profile['is_supervisor'] and not inventory_period:
            raise AccessError(_('المشرف يسجل جرد أسبوع التقرير الحالي فقط.'))
        # new() is deliberately in-memory: querying overlapping ranges must not
        # overwrite weekly snapshots, counted quantities, or the next opening.
        preview = self.new({
            'company_id': self.company_id.id,
            'week_start': start,
            'week_end': end,
        })
        values = preview._automatic_values()
        products = self.env['product.product'].browse(
            list({value['product_id'] for value in values.values()})
        )
        names = {product.id: product.display_name for product in products}
        allowed_stage_codes = profile['allowed_stage_codes']
        stages = {code: {} for code in allowed_stage_codes}
        saved_lines = defaultdict(lambda: self.env[
            'furniture.assembly.weekly.material.report.line'
        ].sudo())
        if inventory_period:
            for line in self.sudo().line_ids.sorted(
                key=lambda item: (not item.counted, item.id)
            ):
                saved_lines[(line.stage_code, line.product_id.id)] |= line

        for (supervisor_id, _stage_code, _product_id), value in values.items():
            stage_code = value['stage_code']
            product_id = value['product_id']
            if stage_code not in stages:
                continue
            if profile['is_supervisor'] and supervisor_id != self.env.user.id:
                continue
            # The source values repeat hall totals for each allowed supervisor;
            # one material row per stage prevents double-counting those totals.
            row = {
                'id': product_id,
                'name': names[product_id],
            }
            candidate_lines = saved_lines[(stage_code, product_id)]
            if profile['is_supervisor']:
                candidate_lines = candidate_lines.filtered(
                    lambda line: line.supervisor_id.id == self.env.user.id
                )
            inventory_line = candidate_lines[:1]
            if profile['is_admin']:
                row.update({
                    'recipe_qty': value['recipe_used_qty'],
                    'manual_qty': value['received_qty'],
                    'counted': bool(inventory_line and inventory_line.counted),
                    'actual_qty': (
                        inventory_line.actual_qty
                        if inventory_line and inventory_line.counted else False
                    ),
                    'variance_qty': (
                        inventory_line.variance_qty
                        if inventory_line and inventory_line.counted else False
                    ),
                })
            else:
                row.update({
                    'counted': bool(inventory_line and inventory_line.counted),
                    'actual_qty': (
                        inventory_line.actual_qty
                        if inventory_line and inventory_line.counted else False
                    ),
                    'status': self._inventory_status(
                        bool(inventory_line and inventory_line.counted),
                        inventory_line.variance_qty if inventory_line else 0.0,
                    ),
                })
            stages[stage_code][product_id] = row
        return {
            'date_from': fields.Date.to_string(start),
            'date_to': fields.Date.to_string(end),
            'is_admin': profile['is_admin'],
            'is_supervisor': profile['is_supervisor'],
            'inventory_period': inventory_period,
            'stages': [
                {'code': code, 'name': STAGE_LABELS[code],
                 'lines': sorted(stages[code].values(), key=lambda row: row['name'])}
                for code in allowed_stage_codes
            ],
        }

    def save_actual_inventory(self, stage_code, product_id, actual_qty):
        """Save one hall count after checking the caller's exact stage policy."""
        self.ensure_one()
        profile = self._inventory_access_profile(self.company_id)
        self.check_access('read')
        if not profile['is_supervisor']:
            raise AccessError(_('إدخال الجرد من هذه الشاشة متاح لمشرف المرحلة فقط.'))
        if stage_code not in profile['allowed_stage_codes']:
            raise AccessError(_('غير مسموح لك بتسجيل جرد هذه المرحلة.'))
        try:
            product_id = int(product_id)
            quantity = float(actual_qty)
        except (TypeError, ValueError):
            raise ValidationError(_('أدخل كمية جرد صحيحة.'))
        if product_id <= 0 or not math.isfinite(quantity) or quantity < 0:
            raise ValidationError(_('الجرد الفعلي يجب أن يكون رقمًا موجبًا أو صفرًا.'))

        policy = self.env[
            'furniture.assembly.supervisor.material'
        ].sudo().search([
            ('company_id', '=', self.company_id.id),
            ('supervisor_id', '=', self.env.user.id),
            ('stage_code', '=', stage_code),
            ('product_ids', 'in', product_id),
        ], limit=1)
        if not policy:
            raise AccessError(_('هذه الخامة غير مسموحة لك في المرحلة المختارة.'))

        own_line = self.sudo().line_ids.filtered(lambda line: (
            line.supervisor_id.id == self.env.user.id
            and line.stage_code == stage_code
            and line.product_id.id == product_id
        ))[:1]
        if not own_line:
            self.sudo().action_refresh()
            own_line = self.sudo().line_ids.filtered(lambda line: (
                line.supervisor_id.id == self.env.user.id
                and line.stage_code == stage_code
                and line.product_id.id == product_id
            ))[:1]
        if not own_line:
            raise ValidationError(_('سطر الخامة غير موجود في تقرير الأسبوع الحالي.'))

        # The physical stock belongs to the stage hall. If two supervisors are
        # configured for the same material, keep their report rows identical.
        stage_lines = self.sudo().line_ids.filtered(lambda line: (
            line.stage_code == stage_code and line.product_id.id == product_id
        ))
        stage_lines.write({'counted': True, 'actual_qty': quantity})
        own_line.invalidate_recordset(['counted', 'actual_qty', 'variance_qty'])
        return {
            'counted': True,
            'actual_qty': own_line.actual_qty,
            'status': self._inventory_status(True, own_line.variance_qty),
        }

    def action_refresh(self):
        self._check_admin()
        Line = self.env['furniture.assembly.weekly.material.report.line']
        for report in self:
            automatic = report._automatic_values()
            existing = {
                (line.supervisor_id.id, line.stage_code, line.product_id.id): line
                for line in report.line_ids
            }
            for key, values in automatic.items():
                line = existing.pop(key, Line)
                if line:
                    line.sudo().write(values)
                else:
                    Line.sudo().create(dict(values, report_id=report.id))
            if existing:
                Line.browse([line.id for line in existing.values()]).sudo().unlink()
            super(AssemblyWeeklyMaterialReport, report).write({
                'generated_at': fields.Datetime.now(),
            })
        return True

    @api.model
    def _generate_for_company(self, company, target_date=None):
        start, end = self._week_dates(target_date)
        report = self.sudo().search([
            ('company_id', '=', company.id),
            ('week_start', '=', start),
        ], limit=1)
        if not report:
            report = self.sudo().create({
                'company_id': company.id,
                'week_start': start,
                'week_end': end,
            })
        else:
            report.sudo().action_refresh()
        return report

    @api.model
    def _cron_generate_thursday_reports(self):
        companies = self.env[
            'furniture.assembly.supply.config'
        ].sudo().search([]).mapped('company_id')
        now_utc = datetime.now(pytz.UTC)
        for company in companies:
            timezone = pytz.timezone(company.partner_id.tz or 'UTC')
            local_date = now_utc.astimezone(timezone).date()
            if local_date.weekday() == 3:
                self.sudo()._generate_for_company(company, local_date)
        return True

    @api.model
    def action_open_current_report(self):
        """Open the current company's weekly report directly from the menu."""
        self._inventory_access_profile(self.env.company)
        report = self._generate_for_company(self.env.company)
        return {
            'type': 'ir.actions.act_window',
            'name': _('متابعة الاستهلاك'),
            'res_model': self._name,
            'res_id': report.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref(
                    'furniture_assembly_requisitions.'
                    'assembly_weekly_material_report_form'
                ).id,
                'form',
            )],
            'target': 'current',
        }


class AssemblyWeeklyMaterialReportLine(models.Model):
    _name = 'furniture.assembly.weekly.material.report.line'
    _description = 'سطر متابعة استهلاك خامة مرحلة'
    _order = 'stage_code, supervisor_id, product_id'
    _check_company_auto = True

    report_id = fields.Many2one(
        'furniture.assembly.weekly.material.report', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='report_id.company_id', store=True, index=True,
    )
    report_week_end = fields.Date(
        related='report_id.week_end', store=True, index=True,
    )
    supervisor_id = fields.Many2one(
        'res.users', string='المشرف', required=True, readonly=True,
    )
    stage_code = fields.Selection(
        [(code, STAGE_LABELS[code]) for code in SHARED_HALL_STAGES],
        string='المرحلة', required=True, readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', string='الخامة', required=True, readonly=True,
        check_company=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='الوحدة', required=True, readonly=True,
    )
    opening_qty = fields.Float('رصيد أول الأسبوع', readonly=True, digits=(16, 3))
    received_qty = fields.Float('المسحوب للصالة', readonly=True, digits=(16, 3))
    completed_piece_qty = fields.Float('القطع المنفذة', readonly=True, digits=(16, 3))
    recipe_used_qty = fields.Float('استهلاك الريسيبي', readonly=True, digits=(16, 3))
    other_out_qty = fields.Float('مرتجع/منقول خارج الصالة', readonly=True, digits=(16, 3))
    theoretical_qty = fields.Float(
        'المفروض موجود', compute='_compute_balances', store=True,
        readonly=True, digits=(16, 3),
    )
    counted = fields.Boolean('تم الجرد')
    actual_qty = fields.Float('الجرد الفعلي', digits=(16, 3))
    variance_qty = fields.Float(
        'الفرق', compute='_compute_balances', store=True,
        readonly=True, digits=(16, 3),
    )
    note = fields.Char('ملاحظة الجرد')

    _sql_constraints = [(
        'report_supervisor_stage_product_unique',
        'unique(report_id, supervisor_id, stage_code, product_id)',
        'الخامة مكررة لنفس المشرف والمرحلة في التقرير.',
    )]

    @api.depends(
        'opening_qty', 'received_qty', 'recipe_used_qty', 'other_out_qty',
        'counted', 'actual_qty',
    )
    def _compute_balances(self):
        for line in self:
            line.theoretical_qty = (
                line.opening_qty + line.received_qty
                - line.recipe_used_qty - line.other_out_qty
            )
            line.variance_qty = (
                line.actual_qty - line.recipe_used_qty - line.received_qty
                if line.counted else 0.0
            )

    @api.constrains('actual_qty')
    def _check_actual_qty(self):
        for line in self:
            if line.actual_qty < 0:
                raise ValidationError(_('الجرد الفعلي لا يمكن أن يكون سالبًا.'))

    def write(self, vals):
        self.env['furniture.assembly.weekly.material.report']._check_admin()
        editable = {'counted', 'actual_qty', 'note'}
        automatic = {
            'supervisor_id', 'stage_code', 'product_id', 'product_uom_id',
            'opening_qty', 'received_qty', 'recipe_used_qty',
            'other_out_qty', 'completed_piece_qty',
        }
        if set(vals) - editable and not self.env.su:
            raise AccessError(_('يمكن تعديل الجرد الفعلي والملاحظة فقط.'))
        if set(vals) & automatic and not self.env.su:
            raise AccessError(_('قيم الحركة والريسيبي تُحسب تلقائيًا.'))
        return super().write(vals)

    def unlink(self):
        self.env['furniture.assembly.weekly.material.report']._check_admin()
        return super().unlink()
