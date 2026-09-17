# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


class EmployeeAdvance(models.Model):
    _name = "employee.advance"
    _description = "Employee Advance"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "advance_date desc, id desc"

    name = fields.Char(
        string="Reference",
        default=lambda self: _("New"),
        readonly=True,
        copy=False,
        tracking=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="العامل",
        required=True,
        tracking=True,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="employee_id.work_contact_id",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    advance_date = fields.Date(
        string="تاريخ السلفة",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    amount = fields.Monetary(
        string="مبلغ السلفة",
        required=True,
        currency_field="currency_id",
        tracking=True,
    )
    reason = fields.Text(string="سبب السلفة", required=True, tracking=True)
    payment_journal_id = fields.Many2one(
        "account.journal",
        string="الصرف من",
        required=True,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self._default_payment_journal(),
        tracking=True,
    )
    advance_account_id = fields.Many2one(
        "account.account",
        required=True,
        domain="[('company_ids', 'in', company_id), ('account_type', '=', 'asset_current')]",
        default=lambda self: self._default_advance_account(),
        tracking=True,
    )
    move_id = fields.Many2one(
        "account.move",
        string="Disbursement Entry",
        readonly=True,
        copy=False,
    )
    repayment_ids = fields.One2many(
        "employee.advance.repayment",
        "advance_id",
        string="الدفعات المسجلة",
        readonly=True,
    )
    installment_ids = fields.One2many(
        "employee.advance.installment",
        "advance_id",
        string="خطة السداد",
        copy=False,
    )
    installment_count = fields.Integer(
        string="عدد الدفعات",
        default=1,
        required=True,
        tracking=True,
    )
    first_installment_date = fields.Date(
        string="تاريخ أول قسط",
        default=lambda self: fields.Date.context_today(self) + relativedelta(months=1),
        required=True,
        tracking=True,
    )
    installment_amount = fields.Monetary(
        string="قيمة كل قسط",
        compute="_compute_installment_amount",
        store=True,
        currency_field="currency_id",
    )
    repayment_frequency = fields.Selection(
        [
            ("weekly", "أسبوعي"),
            ("monthly", "شهري"),
        ],
        string="طريقة السداد",
        default="monthly",
        required=True,
        tracking=True,
        help=(
            "عند اختيار السداد الأسبوعي تحدد الخطة موعد قسط كل سبعة أيام "
            "ابتداءً من تاريخ أول قسط. تسجيل السداد يتم يدويًا بواسطة المحاسب."
        ),
    )
    paid_amount = fields.Monetary(
        string="المبلغ المسدد",
        compute="_compute_balance",
        store=True,
        currency_field="currency_id",
    )
    balance = fields.Monetary(
        string="المتبقي على العامل",
        compute="_compute_balance",
        store=True,
        currency_field="currency_id",
        tracking=True,
    )
    progress = fields.Float(string="نسبة السداد", compute="_compute_progress")
    repayment_count = fields.Integer(compute="_compute_repayment_count")
    state = fields.Selection(
        [
            ("draft", "مسودة"),
            ("active", "عليه رصيد"),
            ("closed", "مسددة بالكامل"),
            ("cancelled", "ملغاة"),
        ],
        default="draft",
        required=True,
        index=True,
        tracking=True,
    )
    notes = fields.Html()

    _sql_constraints = [
        ("amount_positive", "CHECK(amount > 0)", "Advance amount must be positive."),
        (
            "installment_count_positive",
            "CHECK(installment_count > 0)",
            "Installment count must be at least one.",
        ),
    ]

    @api.model
    def _default_payment_journal(self):
        journal_model = self.env["account.journal"]
        cash_journal = journal_model.search(
            [
                ("company_id", "=", self.env.company.id),
                ("type", "=", "cash"),
                ("active", "=", True),
            ],
            order="sequence, id",
            limit=1,
        )
        return cash_journal or journal_model.search(
            [
                ("company_id", "=", self.env.company.id),
                ("type", "=", "bank"),
                ("active", "=", True),
            ],
            order="sequence, id",
            limit=1,
        )

    @api.model
    def _default_advance_account(self):
        return self.env["account.account"].search(
            [
                ("code", "=", "105002"),
                ("company_ids", "in", self.env.company.id),
            ],
            limit=1,
        )

    @api.depends("amount", "installment_count")
    def _compute_installment_amount(self):
        for record in self:
            record.installment_amount = (
                record.amount / record.installment_count
                if record.installment_count
                else 0.0
            )

    @api.onchange("advance_date", "repayment_frequency")
    def _onchange_first_installment_date(self):
        for record in self:
            if not record.advance_date:
                continue
            delta = (
                relativedelta(weeks=1)
                if record.repayment_frequency == "weekly"
                else relativedelta(months=1)
            )
            record.first_installment_date = record.advance_date + delta

    @api.depends("amount", "repayment_ids.amount", "repayment_ids.state")
    def _compute_balance(self):
        for record in self:
            paid = sum(
                record.repayment_ids.filtered(
                    lambda repayment: repayment.state == "posted"
                ).mapped("amount")
            )
            record.paid_amount = paid
            record.balance = max(record.amount - paid, 0.0)

    @api.depends("amount", "paid_amount")
    def _compute_progress(self):
        for record in self:
            record.progress = (
                record.paid_amount / record.amount * 100.0
                if record.amount
                else 0.0
            )

    @api.depends("repayment_ids")
    def _compute_repayment_count(self):
        for record in self:
            record.repayment_count = len(record.repayment_ids)

    @api.constrains("employee_id", "company_id")
    def _check_employee_company(self):
        for record in self:
            if record.employee_id.company_id != record.company_id:
                raise ValidationError(_("The employee must belong to the selected company."))

    @api.constrains("advance_date", "first_installment_date")
    def _check_first_installment_date(self):
        for record in self:
            if (
                record.advance_date
                and record.first_installment_date
                and record.first_installment_date < record.advance_date
            ):
                raise ValidationError(
                    _("The first installment cannot be before the advance date.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", _("New")) == _("New"):
                values["name"] = (
                    self.env["ir.sequence"].next_by_code("employee.advance")
                    or _("New")
                )
        records = super().create(vals_list)
        records.filtered(lambda record: record.state == "draft")._generate_installments()
        return records

    def write(self, vals):
        result = super().write(vals)
        plan_fields = {
            "amount",
            "company_id",
            "installment_count",
            "repayment_frequency",
            "first_installment_date",
        }
        if plan_fields.intersection(vals):
            self.filtered(lambda record: record.state == "draft")._generate_installments()
        return result

    def unlink(self):
        if any(record.state != "draft" for record in self):
            raise UserError(_("Only draft advances can be deleted."))
        return super().unlink()

    def action_disburse(self):
        for record in self:
            if record.state != "draft":
                continue
            if not record.employee_id.work_contact_id:
                raise UserError(_("The employee must have a work contact before disbursement."))
            if not record.payment_journal_id.default_account_id:
                raise UserError(_("The selected bank or cash journal has no default account."))
            if not record.advance_account_id.reconcile:
                raise UserError(_("The employee advance account must allow reconciliation."))

            move = self.env["account.move"].create(
                {
                    "move_type": "entry",
                    "date": record.advance_date,
                    "journal_id": record.payment_journal_id.id,
                    "ref": record.name,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": _("Employee advance - %s") % record.employee_id.name,
                                "partner_id": record.partner_id.id,
                                "account_id": record.advance_account_id.id,
                                "debit": record.amount,
                                "credit": 0.0,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "name": _("Advance paid - %s") % record.employee_id.name,
                                "partner_id": record.partner_id.id,
                                "account_id": record.payment_journal_id.default_account_id.id,
                                "debit": 0.0,
                                "credit": record.amount,
                            },
                        ),
                    ],
                }
            )
            move.action_post()
            record.write({"move_id": move.id, "state": "active"})
            record._generate_installments()
            record.message_post(
                body=_("Advance disbursed and accounting entry %s posted.") % move.name
            )
        return True

    def _generate_installments(self):
        for record in self:
            record.installment_ids.unlink()
            remaining = record.amount
            regular_amount = record.currency_id.round(
                record.amount / record.installment_count
            )
            for index in range(record.installment_count):
                amount = min(regular_amount, remaining)
                if index == record.installment_count - 1:
                    amount = remaining
                if record.repayment_frequency == "weekly":
                    due_date = record.first_installment_date + relativedelta(
                        weeks=index
                    )
                else:
                    due_date = record.first_installment_date + relativedelta(
                        months=index
                    )
                self.env["employee.advance.installment"].create(
                    {
                        "advance_id": record.id,
                        "sequence": index + 1,
                        "due_date": due_date,
                        "amount": amount,
                    }
                )
                remaining -= amount

    def action_open_repayment_wizard(self):
        return self._repayment_wizard_action()

    def _repayment_wizard_action(self, installment=False):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can register advance repayments."))
        if self.state != "active" or not self.balance:
            raise UserError(_("This advance has no outstanding balance."))
        if installment:
            installment.ensure_one()
            if installment.advance_id != self:
                raise ValidationError(_("The installment does not belong to this advance."))
            if installment.state == "paid" or not installment.balance:
                raise UserError(_("This installment is already fully paid."))
        next_installment = installment or self.installment_ids.filtered(
            lambda line: line.state != "paid"
        )[:1]
        default_amount = min(
            next_installment.balance if next_installment else self.balance,
            self.balance,
        )
        context = {
            "default_advance_id": self.id,
            "default_amount": default_amount,
            "default_journal_id": self.payment_journal_id.id,
        }
        if installment:
            context["default_installment_id"] = installment.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Register Advance Repayment"),
            "res_model": "employee.advance.repayment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": context,
        }

    def _allocate_repayment(self, repayment):
        remaining = repayment.amount
        installments = repayment.installment_id or self.installment_ids.filtered(
            lambda line: line.state != "paid"
        ).sorted(lambda line: (line.due_date, line.sequence))
        for installment in installments:
            if float_compare(
                remaining,
                0.0,
                precision_rounding=self.currency_id.rounding,
            ) <= 0:
                break
            allocation = min(remaining, installment.balance)
            installment.paid_amount += allocation
            remaining -= allocation

    def _reconcile_advance_lines(self):
        for record in self:
            moves = record.move_id | record.repayment_ids.mapped("move_id")
            lines = moves.line_ids.filtered(
                lambda line: line.account_id == record.advance_account_id
                and line.partner_id == record.partner_id
                and not line.reconciled
            )
            if len(lines) > 1:
                lines.reconcile()

    def _refresh_state(self):
        for record in self:
            if record.state == "active" and float_compare(
                record.balance,
                0.0,
                precision_rounding=record.currency_id.rounding,
            ) <= 0:
                record.state = "closed"

    def action_cancel(self):
        for record in self:
            if record.state != "draft":
                raise UserError(
                    _("A disbursed advance cannot be cancelled. Register a repayment instead.")
                )
            record.state = "cancelled"
        return True

    def action_reset_to_draft(self):
        self.filtered(lambda record: record.state == "cancelled").write(
            {"state": "draft"}
        )
        return True

    def action_view_entry(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Disbursement Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.move_id.id,
        }

    def action_view_repayments(self):
        self.ensure_one()
        action = self.env.ref(
            "employee_advance_accounting.action_employee_advance_repayment"
        ).read()[0]
        action["domain"] = [("advance_id", "=", self.id)]
        action["context"] = {"default_advance_id": self.id}
        return action


class EmployeeAdvanceInstallment(models.Model):
    _name = "employee.advance.installment"
    _description = "Employee Advance Installment"
    _order = "due_date, sequence, id"

    advance_id = fields.Many2one(
        "employee.advance",
        string="السلفة",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(string="رقم القسط", required=True)
    due_date = fields.Date(string="تاريخ الاستحقاق", required=True)
    amount = fields.Monetary(
        string="قيمة القسط",
        required=True,
        currency_field="currency_id",
    )
    paid_amount = fields.Monetary(
        string="المسدد",
        currency_field="currency_id",
        default=0.0,
    )
    balance = fields.Monetary(
        string="المتبقي",
        compute="_compute_status",
        store=True,
        currency_field="currency_id",
    )
    state = fields.Selection(
        [
            ("pending", "غير مسدد"),
            ("partial", "مسدد جزئيًا"),
            ("paid", "مسدد"),
        ],
        string="الحالة",
        compute="_compute_status",
        store=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="advance_id.currency_id",
        readonly=True,
    )
    advance_state = fields.Selection(
        related="advance_id.state",
        readonly=True,
    )

    @api.depends("amount", "paid_amount")
    def _compute_status(self):
        for line in self:
            line.balance = max(line.amount - line.paid_amount, 0.0)
            comparison = float_compare(
                line.paid_amount,
                line.amount,
                precision_rounding=line.currency_id.rounding,
            )
            if comparison >= 0:
                line.state = "paid"
            elif line.paid_amount > 0:
                line.state = "partial"
            else:
                line.state = "pending"

    def _is_due_for_payroll_period(self, date_from, date_to):
        self.ensure_one()
        date_to = fields.Date.to_date(date_to)
        return bool(self.due_date and date_to and self.due_date <= date_to)

    def action_open_repayment_wizard(self):
        self.ensure_one()
        return self.advance_id._repayment_wizard_action(installment=self)


class EmployeeAdvanceRepayment(models.Model):
    _name = "employee.advance.repayment"
    _description = "Employee Advance Repayment"
    _inherit = ["mail.thread"]
    _order = "payment_date desc, id desc"

    name = fields.Char(
        default=lambda self: _("New"),
        readonly=True,
        copy=False,
    )
    advance_id = fields.Many2one(
        "employee.advance",
        required=True,
        ondelete="restrict",
        index=True,
    )
    installment_id = fields.Many2one(
        "employee.advance.installment",
        string="القسط",
        ondelete="restrict",
        index=True,
        domain="[('advance_id', '=', advance_id)]",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        related="advance_id.employee_id",
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="advance_id.partner_id",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="advance_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="advance_id.currency_id",
        readonly=True,
    )
    payment_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    amount = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]",
    )
    move_id = fields.Many2one(
        "account.move",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted")],
        default="draft",
        required=True,
        index=True,
    )
    notes = fields.Char()

    _sql_constraints = [
        ("amount_positive", "CHECK(amount > 0)", "Repayment amount must be positive."),
    ]

    @api.constrains("advance_id", "installment_id")
    def _check_installment_advance(self):
        for repayment in self:
            if (
                repayment.installment_id
                and repayment.installment_id.advance_id != repayment.advance_id
            ):
                raise ValidationError(_("The installment does not belong to this advance."))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", _("New")) == _("New"):
                values["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "employee.advance.repayment"
                    )
                    or _("New")
                )
        return super().create(vals_list)

    def unlink(self):
        if any(record.state == "posted" for record in self):
            raise UserError(_("Posted repayments cannot be deleted."))
        return super().unlink()

    def action_post(self):
        for repayment in self:
            advance = repayment.advance_id
            if repayment.state == "posted":
                continue
            if advance.state != "active":
                raise UserError(_("Repayments can only be posted against active advances."))
            if float_compare(
                repayment.amount,
                advance.balance,
                precision_rounding=advance.currency_id.rounding,
            ) > 0:
                raise ValidationError(_("Repayment cannot exceed the outstanding balance."))
            if repayment.installment_id:
                installment = repayment.installment_id
                if installment.state == "paid":
                    raise ValidationError(_("This installment is already fully paid."))
                if float_compare(
                    repayment.amount,
                    installment.balance,
                    precision_rounding=advance.currency_id.rounding,
                ) > 0:
                    raise ValidationError(
                        _("Repayment cannot exceed the selected installment balance.")
                    )
            if not repayment.journal_id.default_account_id:
                raise UserError(_("The selected journal has no default account."))

            move = self.env["account.move"].create(
                {
                    "move_type": "entry",
                    "date": repayment.payment_date,
                    "journal_id": repayment.journal_id.id,
                    "ref": repayment.name,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": _("Advance repayment - %s")
                                % repayment.employee_id.name,
                                "partner_id": repayment.partner_id.id,
                                "account_id": repayment.journal_id.default_account_id.id,
                                "debit": repayment.amount,
                                "credit": 0.0,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "name": _("Employee advance settlement - %s")
                                % repayment.employee_id.name,
                                "partner_id": repayment.partner_id.id,
                                "account_id": advance.advance_account_id.id,
                                "debit": 0.0,
                                "credit": repayment.amount,
                            },
                        ),
                    ],
                }
            )
            move.action_post()
            repayment.write({"move_id": move.id, "state": "posted"})
            advance._allocate_repayment(repayment)
            advance._reconcile_advance_lines()
            advance._refresh_state()
            advance.message_post(
                body=_("Repayment %s posted with accounting entry %s.")
                % (repayment.name, move.name)
            )
        return True

    def action_view_entry(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Repayment Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.move_id.id,
        }
