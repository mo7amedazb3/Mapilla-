# -*- coding: utf-8 -*-

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class ResCompany(models.Model):
    _inherit = 'res.company'

    furniture_payroll_journal_id = fields.Many2one(
        'account.journal', string='دفتر يومية استحقاق الأجور',
        check_company=True,
    )
    furniture_basic_salary_account_id = fields.Many2one(
        'account.account', string='حساب الأجر الأساسي', check_company=True,
    )
    furniture_allowance_account_id = fields.Many2one(
        'account.account', string='حساب البدلات', check_company=True,
    )
    furniture_rewards_account_id = fields.Many2one(
        'account.account', string='حساب الحوافز', check_company=True,
    )
    furniture_salary_advance_account_id = fields.Many2one(
        'account.account', string='حساب سلف العاملين', check_company=True,
    )
    furniture_accrued_salary_account_id = fields.Many2one(
        'account.account', string='حساب الأجور المستحقة', check_company=True,
    )
    furniture_payroll_cash_journal_id = fields.Many2one(
        'account.journal', string='دفتر يومية الصندوق لسداد الأجور',
        check_company=True,
    )
    furniture_payroll_bank_journal_id = fields.Many2one(
        'account.journal', string='دفتر يومية البنك لسداد الأجور',
        check_company=True,
    )


class AccountMove(models.Model):
    _inherit = 'account.move'

    furniture_payroll_date_from = fields.Date(
        string='بداية فترة الأجور', readonly=True, copy=False, index=True,
    )
    furniture_payroll_date_to = fields.Date(
        string='نهاية فترة الأجور', readonly=True, copy=False, index=True,
    )
    furniture_payroll_slip_ids = fields.One2many(
        'simple.payroll.slip', 'furniture_payroll_move_id',
        string='كشوف الأجر', readonly=True,
    )
    furniture_payroll_accrual_move_id = fields.Many2one(
        'account.move', string='قيد استحقاق الأجور', readonly=True,
        copy=False, index=True, ondelete='restrict', check_company=True,
    )
    furniture_payroll_payment_move_ids = fields.One2many(
        'account.move', 'furniture_payroll_accrual_move_id',
        string='قيد سداد الأجور', readonly=True,
    )

    _sql_constraints = [
        (
            'furniture_payroll_period_company_unique',
            'unique(company_id, furniture_payroll_date_from, '
            'furniture_payroll_date_to)',
            'تم إنشاء قيد استحقاق لهذه الفترة بالفعل.',
        ),
        (
            'furniture_payroll_accrual_payment_unique',
            'unique(furniture_payroll_accrual_move_id)',
            'تم إنشاء قيد سداد لهذا الاستحقاق بالفعل.',
        ),
    ]

    def action_open_furniture_payroll_payment(self):
        self.ensure_one()
        return self.env[
            'furniture.mrp.payroll.payment.wizard'
        ].action_open_for_accrual(self)


class SimplePayrollSlip(models.Model):
    _inherit = 'simple.payroll.slip'

    furniture_payroll_move_id = fields.Many2one(
        'account.move', string='قيد استحقاق الأجر', readonly=True,
        copy=False, index=True, ondelete='restrict', check_company=True,
    )
    furniture_payroll_payment_move_id = fields.Many2one(
        'account.move', string='قيد سداد الأجر', readonly=True,
        copy=False, index=True, ondelete='restrict', check_company=True,
    )


class EmployeeAdvanceRepayment(models.Model):
    _inherit = 'employee.advance.repayment'

    furniture_payroll_slip_id = fields.Many2one(
        'simple.payroll.slip', string='كشف الأجر', readonly=True,
        copy=False, index=True, ondelete='restrict', check_company=True,
    )


class FurnitureMrpPayrollBatchWizardAccounting(models.TransientModel):
    _inherit = 'furniture.mrp.payroll.batch.wizard'

    def _payroll_accounting_config(self):
        self.ensure_one()
        company = self.company_id
        values = {
            'journal': company.furniture_payroll_journal_id,
            'basic': company.furniture_basic_salary_account_id,
            'allowance': company.furniture_allowance_account_id,
            'rewards': company.furniture_rewards_account_id,
            'accrued': company.furniture_accrued_salary_account_id,
        }
        missing = [key for key, record in values.items() if not record]
        if missing:
            raise UserError(_(
                'إعدادات قيد استحقاق الأجور غير مكتملة. راجع حسابات ودفتر '
                'يومية الرواتب للشركة.'
            ))
        if values['journal'].type != 'general':
            raise ValidationError(_('دفتر يومية الأجور يجب أن يكون من النوع متنوع.'))
        for account in values.values():
            if account._name == 'account.account' and company not in account.company_ids:
                raise ValidationError(_('أحد حسابات الأجور لا يتبع الشركة الحالية.'))
        return values

    def _open_payroll_move(self, move):
        return {
            'type': 'ir.actions.act_window',
            'name': _('قيد استحقاق الأجور'),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'views': [(self.env.ref('account.view_move_form').id, 'form')],
            'target': 'current',
        }

    def _prepare_payroll_move_values(self, slips, config):
        currency = self.company_id.currency_id
        debit_totals = defaultdict(float)
        accrued_total = 0.0

        for slip in slips:
            components = {
                'basic': currency.round(max(slip.base_wage or 0.0, 0.0)),
                'allowance': currency.round(
                    max(slip.contract_allowances or 0.0, 0.0)
                ),
                'rewards': currency.round(max(
                    (slip.factory_bonus_amount or 0.0)
                    + (slip.furniture_overtime_amount or 0.0),
                    0.0,
                )),
            }
            deduction = currency.round(max(
                (slip.attendance_deduction or 0.0)
                + (slip.factory_other_deduction_amount or 0.0),
                0.0,
            ))
            # The requested journal has no separate deduction account, so
            # deductions reduce the earned debit buckets before recognition.
            for key in ('basic', 'allowance', 'rewards'):
                reduction = min(components[key], deduction)
                components[key] = currency.round(components[key] - reduction)
                deduction = currency.round(deduction - reduction)

            net_salary = currency.round(max(slip.net_salary or 0.0, 0.0))
            component_total = currency.round(sum(components.values()))
            difference = currency.round(net_salary - component_total)
            if not currency.is_zero(difference):
                components['basic'] = currency.round(
                    components['basic'] + difference
                )

            for key, amount in components.items():
                debit_totals[key] = currency.round(debit_totals[key] + amount)

            accrued_total = currency.round(
                accrued_total + net_salary
            )

        line_commands = []
        debit_names = {
            'basic': _('Basic Salary'),
            'allowance': _('Allowance'),
            'rewards': _('Rewards'),
        }
        for key in ('basic', 'allowance', 'rewards'):
            amount = currency.round(debit_totals[key])
            if not currency.is_zero(amount):
                line_commands.append((0, 0, {
                    'name': debit_names[key],
                    'account_id': config[key].id,
                    'debit': amount,
                    'credit': 0.0,
                }))

        if not currency.is_zero(accrued_total):
            line_commands.append((0, 0, {
                'name': _('Accrued Salary'),
                'account_id': config['accrued'].id,
                'debit': 0.0,
                'credit': accrued_total,
            }))
        if not line_commands:
            raise UserError(_('لا توجد مبالغ مستحقة لإنشاء قيد لهذه الفترة.'))

        total_debit = currency.round(sum(line[2]['debit'] for line in line_commands))
        total_credit = currency.round(sum(line[2]['credit'] for line in line_commands))
        if float_compare(
            total_debit, total_credit, precision_rounding=currency.rounding,
        ):
            raise ValidationError(_(
                'تعذر موازنة قيد الأجور: المدين %(debit)s والدائن %(credit)s.',
                debit=total_debit,
                credit=total_credit,
            ))

        return {
            'move_type': 'entry',
            'company_id': self.company_id.id,
            'journal_id': config['journal'].id,
            'date': min(self.date_to, fields.Date.context_today(self)),
            'ref': _('استحقاق أجور %(date_from)s - %(date_to)s',
                     date_from=self.date_from.strftime('%d/%m/%Y'),
                     date_to=self.date_to.strftime('%d/%m/%Y')),
            'furniture_payroll_date_from': self.date_from,
            'furniture_payroll_date_to': self.date_to,
            'line_ids': line_commands,
        }

    def action_apply_accounting(self):
        self.ensure_one()
        helper = self.env['furniture.mrp.payroll.slip.wizard']
        helper._validate_period(self.company_id, self.date_from, self.date_to)
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(hashtext(%s))',
            ['furniture-payroll-%s-%s-%s' % (
                self.company_id.id, self.date_from, self.date_to,
            )],
        )
        existing = self.env['account.move'].search([
            ('company_id', '=', self.company_id.id),
            ('furniture_payroll_date_from', '=', self.date_from),
            ('furniture_payroll_date_to', '=', self.date_to),
        ], limit=1)
        if existing:
            return self._open_payroll_move(existing)

        slips = self._generate_slips()
        if any(slip.state in ('paid', 'cancel') for slip in slips):
            raise UserError(_('لا يمكن إنشاء القيد من كشف مدفوع أو ملغي.'))
        if slips.filtered('furniture_payroll_move_id'):
            raise UserError(_('أحد كشوف الفترة مرتبط بالفعل بقيد استحقاق آخر.'))
        draft_slips = slips.filtered(lambda slip: slip.state == 'draft')
        if draft_slips:
            draft_slips.action_confirm()

        config = self._payroll_accounting_config()
        move_values = self._prepare_payroll_move_values(slips, config)
        move = self.env['account.move'].create(move_values)
        move.action_post()
        slips.write({'furniture_payroll_move_id': move.id})
        return self._open_payroll_move(move)

    @api.model
    def action_apply_period_accounting(self, date_from, date_to):
        wizard = self._period_wizard(date_from, date_to)
        return wizard.action_apply_accounting()

    @api.model
    def action_open_period_payment(self, date_from, date_to):
        date_from = fields.Date.to_date(date_from) if date_from else False
        date_to = fields.Date.to_date(date_to) if date_to else False
        self.env['furniture.mrp.payroll.slip.wizard']._validate_period(
            self.env.company, date_from, date_to,
        )
        accrual_move = self.env['account.move'].search([
            ('company_id', '=', self.env.company.id),
            ('furniture_payroll_date_from', '=', date_from),
            ('furniture_payroll_date_to', '=', date_to),
        ], limit=1)
        if not accrual_move:
            raise UserError(_(
                'أنشئ قيد الاستحقاق أولًا من زر تطبيق، ثم نفّذ السداد.'
            ))
        return self.env[
            'furniture.mrp.payroll.payment.wizard'
        ].action_open_for_accrual(accrual_move)


class FurnitureMrpPayrollPaymentWizard(models.TransientModel):
    _name = 'furniture.mrp.payroll.payment.wizard'
    _description = 'Pay Furniture Payroll Accrual'

    accrual_move_id = fields.Many2one(
        'account.move', string='قيد الاستحقاق', required=True, readonly=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', related='accrual_move_id.company_id', store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )
    date_from = fields.Date(
        related='accrual_move_id.furniture_payroll_date_from', readonly=True,
    )
    date_to = fields.Date(
        related='accrual_move_id.furniture_payroll_date_to', readonly=True,
    )
    payment_date = fields.Date(
        string='تاريخ السداد', required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    total_amount = fields.Monetary(
        string='إجمالي صافي الأجور المستحقة', currency_field='currency_id',
        compute='_compute_total_amount', readonly=True,
    )
    cash_journal_id = fields.Many2one(
        'account.journal', string='دفتر الصندوق', required=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'cash')]",
        check_company=True,
    )
    cash_amount = fields.Monetary(
        string='المبلغ النقدي', currency_field='currency_id', required=True,
        default=0.0,
    )
    bank_journal_id = fields.Many2one(
        'account.journal', string='دفتر البنك', required=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'bank')]",
        check_company=True,
    )
    bank_amount = fields.Monetary(
        string='المبلغ البنكي', currency_field='currency_id', required=True,
        default=0.0,
    )
    difference_amount = fields.Monetary(
        string='الفرق المتبقي', currency_field='currency_id',
        compute='_compute_total_amount', readonly=True,
    )

    @api.depends(
        'accrual_move_id', 'accrual_move_id.line_ids.balance',
        'cash_amount', 'bank_amount',
    )
    def _compute_total_amount(self):
        for wizard in self:
            account = wizard.company_id.furniture_accrued_salary_account_id
            wizard.total_amount = wizard.currency_id.round(sum(
                line.credit - line.debit
                for line in wizard.accrual_move_id.line_ids
                if line.account_id == account
            )) if wizard.currency_id else 0.0
            wizard.difference_amount = (
                wizard.currency_id.round(
                    wizard.total_amount
                    - (wizard.cash_amount or 0.0)
                    - (wizard.bank_amount or 0.0)
                ) if wizard.currency_id else 0.0
            )

    @api.model
    def _open_move(self, move):
        return {
            'type': 'ir.actions.act_window',
            'name': _('قيد سداد الأجور'),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'views': [(self.env.ref('account.view_move_form').id, 'form')],
            'target': 'current',
        }

    @api.model
    def action_open_for_accrual(self, accrual_move):
        self.env[
            'furniture.mrp.payroll.slip.wizard'
        ]._check_payroll_manager_access()
        accrual_move.ensure_one()
        if accrual_move.company_id not in self.env.companies:
            raise UserError(_('لا يمكنك سداد أجور شركة غير مسموحة لك.'))
        existing = self.env['account.move'].search([
            ('furniture_payroll_accrual_move_id', '=', accrual_move.id),
        ], limit=1)
        if existing:
            return self._open_move(existing)
        if (
            accrual_move.state != 'posted'
            or not accrual_move.furniture_payroll_date_from
            or not accrual_move.furniture_payroll_date_to
        ):
            raise UserError(_('يجب ترحيل قيد استحقاق الأجور قبل السداد.'))
        company = accrual_move.company_id
        if (
            not company.furniture_payroll_journal_id
            or not company.furniture_accrued_salary_account_id
            or company.furniture_payroll_journal_id.type != 'general'
        ):
            raise UserError(_(
                'إعدادات دفتر استحقاق الأجور وحساب الأجور المستحقة غير مكتملة.'
            ))
        if (
            not company.furniture_payroll_cash_journal_id
            or not company.furniture_payroll_bank_journal_id
        ):
            raise UserError(_('لم يتم ضبط دفتري الصندوق والبنك لسداد الأجور.'))
        wizard = self.create({
            'accrual_move_id': accrual_move.id,
            'cash_journal_id': company.furniture_payroll_cash_journal_id.id,
            'bank_journal_id': company.furniture_payroll_bank_journal_id.id,
        })
        wizard.cash_amount = wizard.total_amount
        if wizard.currency_id.is_zero(wizard.total_amount):
            raise UserError(_('لا يوجد رصيد أجور مستحقة قابل للسداد في هذا القيد.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('سداد أجور الفترة'),
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref(
                    'furniture_mrp.view_furniture_payroll_payment_wizard_form'
                ).id,
                'form',
            )],
            'target': 'new',
        }

    def _validate_payment(self):
        self.ensure_one()
        self.env[
            'furniture.mrp.payroll.slip.wizard'
        ]._check_payroll_manager_access()
        currency = self.currency_id
        if self.accrual_move_id.state != 'posted':
            raise UserError(_('قيد الاستحقاق غير مرحّل.'))
        if self.payment_date > fields.Date.context_today(self):
            raise ValidationError(_('لا يمكن تسجيل سداد بتاريخ مستقبلي.'))
        if self.cash_amount < 0 or self.bank_amount < 0:
            raise ValidationError(_('مبالغ السداد لا يمكن أن تكون سالبة.'))
        if not currency.is_zero(self.difference_amount):
            raise ValidationError(_(
                'مجموع النقدي والبنك يجب أن يساوي إجمالي المستحق. '
                'الفرق الحالي: %s',
                self.difference_amount,
            ))
        journals = (
            (self.cash_amount, self.cash_journal_id, 'cash', _('Cash')),
            (self.bank_amount, self.bank_journal_id, 'bank', _('Bank')),
        )
        for amount, journal, expected_type, _name in journals:
            if currency.is_zero(amount):
                continue
            if journal.company_id != self.company_id or journal.type != expected_type:
                raise ValidationError(_('دفتر السداد المختار غير صحيح.'))
            if not journal.default_account_id:
                raise ValidationError(_(
                    'دفتر اليومية %s ليس له حساب سيولة افتراضي.',
                    journal.display_name,
                ))
        slips = self.accrual_move_id.furniture_payroll_slip_ids
        if not slips or any(slip.state != 'confirmed' for slip in slips):
            raise UserError(_(
                'كل كشوف قيد الاستحقاق يجب أن تكون معتمدة وغير مدفوعة قبل السداد.'
            ))
        return journals, slips

    def action_post_payment(self):
        self.ensure_one()
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(hashtext(%s))',
            ['furniture-payroll-payment-%s' % self.accrual_move_id.id],
        )
        existing = self.env['account.move'].search([
            ('furniture_payroll_accrual_move_id', '=', self.accrual_move_id.id),
        ], limit=1)
        if existing:
            return self._open_move(existing)

        journals, slips = self._validate_payment()
        config = self.company_id
        line_commands = [(0, 0, {
            'name': _('Accrued Salary'),
            'account_id': config.furniture_accrued_salary_account_id.id,
            'debit': self.total_amount,
            'credit': 0.0,
        })]
        for amount, journal, _expected_type, name in journals:
            if self.currency_id.is_zero(amount):
                continue
            line_commands.append((0, 0, {
                'name': name,
                'account_id': journal.default_account_id.id,
                'debit': 0.0,
                'credit': amount,
            }))
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'company_id': self.company_id.id,
            'journal_id': self.company_id.furniture_payroll_journal_id.id,
            'date': self.payment_date,
            'ref': _('سداد أجور %(date_from)s - %(date_to)s',
                     date_from=self.date_from.strftime('%d/%m/%Y'),
                     date_to=self.date_to.strftime('%d/%m/%Y')),
            'furniture_payroll_accrual_move_id': self.accrual_move_id.id,
            'line_ids': line_commands,
        })
        move.action_post()
        slips.action_paid()
        slips.write({'furniture_payroll_payment_move_id': move.id})
        return self._open_move(move)
