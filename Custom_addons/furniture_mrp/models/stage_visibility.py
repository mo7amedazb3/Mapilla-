from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.osv import expression
from .mrp_stage_dashboard import FURNITURE_STAGE_DASHBOARD_SELECTION

CODES = dict(FURNITURE_STAGE_DASHBOARD_SELECTION)


class ProductionVisibility(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _visibility_manager(self):
        return self.env.user._is_admin() or self.env.user.has_group('furniture_mrp.group_furniture_mrp_manager')

    @api.model
    def _visibility_cutoff(self, code, company):
        value = self.env['ir.config_parameter'].sudo().get_param(
            'furniture_mrp.visibility.%s.%s' % (company.id, code))
        return fields.Date.to_date(value) if value else False

    @api.model
    def set_stage_visibility_date(self, stage_code, visibility_date=False):
        if not self._visibility_manager():
            raise AccessError(_('تاريخ الإظهار يحدده مدير المصنع فقط.'))
        if stage_code not in CODES:
            raise ValidationError(_('مرحلة غير صحيحة.'))
        try:
            date = fields.Date.to_date(visibility_date) if visibility_date else False
        except (ValueError, TypeError):
            raise ValidationError(_('تاريخ الإظهار غير صحيح.'))
        self.env['ir.config_parameter'].sudo().set_param(
            'furniture_mrp.visibility.%s.%s' % (self.env.company.id, stage_code),
            fields.Date.to_string(date) if date else False)
        self.env.registry.clear_cache()
        return True

    @api.model
    def _stage_visibility_domain(self, code, prefix=''):
        if self._visibility_manager():
            return []
        if not self.env.user.has_group('furniture_mrp.group_furniture_mrp_supervisor') and not self.env['hr.employee'].sudo().search_count([
            ('user_id', '=', self.env.uid), ('furniture_mrp_role', '=', 'worker'),
            ('company_id', 'in', self.env.companies.ids),
        ]):
            return []
        domains = []
        for company in self.env.companies:
            cutoff = self._visibility_cutoff(code, company)
            company_domain = [(prefix + 'company_id', '=', company.id)]
            if cutoff:
                # Only use delivery IDs internally: operators cannot read customer orders.
                hidden = self.env['furniture.mrp.future.order'].sudo().search([
                    ('company_id', '=', company.id), ('delivery_date', '>', cutoff),
                ]).ids
                company_domain.append((prefix + 'future_order_id', 'not in', hidden))
            domains.append(company_domain)
        return expression.OR(domains)

    def _visible_for_stage(self, code):
        if self._visibility_manager():
            return self
        return self & self.search(expression.AND([[('id', 'in', self.ids)], self._stage_visibility_domain(code)]))

    @api.model
    def get_stage_dashboard_data(self, stage_code=False, date_from=False, date_to=False):
        result = super().get_stage_dashboard_data(stage_code, date_from, date_to)
        cutoff = self._visibility_cutoff(result['selected_stage'], self.env.company)
        result['visibility_date'] = fields.Date.to_string(cutoff) if cutoff else False
        result['visibility_expired'] = bool(cutoff and cutoff <= fields.Date.context_today(self))
        return result


class UserStageVisibility(models.Model):
    _inherit = 'res.users'

    def _stage_order_visibility_domain(self, code):
        # Called by a global record rule; company/role rules still apply alongside it.
        return self.env['furniture.mrp.production']._stage_visibility_domain(code, 'production_order_id.')

    def _stage_work_visibility_domain(self):
        Production = self.env['furniture.mrp.production']
        if Production._visibility_manager():
            return []
        return expression.OR([
            expression.AND([[('stage', '=', code)], Production._stage_visibility_domain(code, 'production_id.')])
            for code in CODES
        ] + [[('stage', 'not in', list(CODES))]])

    def _production_visibility_domain(self):
        Production = self.env['furniture.mrp.production']
        if Production._visibility_manager():
            return []
        codes = [code for code in CODES if self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_supervisor_' + code)]
        if not codes:
            employees = self.env['hr.employee'].sudo().search([
                ('user_id', '=', self.env.uid), ('company_id', 'in', self.env.companies.ids),
                ('furniture_mrp_role', '=', 'worker'),
            ])
            codes = [code for code in employees.furniture_mrp_worker_stage_ids.mapped('code') if code in CODES]
        return expression.OR([Production._stage_visibility_domain(code) for code in codes]) if codes else []


class DeliveryVisibilityInvalidation(models.Model):
    _inherit = 'furniture.mrp.future.order'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'delivery_date' in vals or 'company_id' in vals:
            self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.registry.clear_cache()
        return result
