from odoo import api, models
from odoo.exceptions import AccessError
from odoo.addons.furniture_mrp.models.mrp_stage_dashboard import (
    FURNITURE_STAGE_DASHBOARD_BATCH_CODES,
    FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES,
)


class ManagerStagePresentation(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _stage_dashboard_order_payload(
        self, production, stage_code, stage_label, stage_order, metrics, line_buckets,
        include_order_supervisor_details=False, include_order_display_details=False,
    ):
        # Populate native control flags for the shared card. Each action still
        # enforces its own stage, material, receipt and quality guards.
        if (stage_code in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES
                and self._stage_dashboard_access_profile()['is_manager']):
            include_order_supervisor_details = True
        return super()._stage_dashboard_order_payload(
            production, stage_code, stage_label, stage_order, metrics, line_buckets,
            include_order_supervisor_details=include_order_supervisor_details,
            include_order_display_details=include_order_display_details,
        )

    @api.model
    def get_stage_dashboard_data(self, stage_code=False, date_from=False, date_to=False):
        result = super().get_stage_dashboard_data(stage_code, date_from, date_to)
        if not self._stage_dashboard_access_profile()['is_manager']:
            return result
        selected = result['selected_stage']
        # These flags choose the existing OWL presentation only. The caller's
        # access profile, allowed stages, record rules and action guards stay native.
        result.update({
            'supervisor_mode': True,
            'batch_supervisor_mode': selected in FURNITURE_STAGE_DASHBOARD_BATCH_CODES,
            'order_supervisor_mode': selected in FURNITURE_STAGE_DASHBOARD_ORDER_SUPERVISOR_CODES,
        })
        if result['batch_supervisor_mode']:
            result['orders'] = []
            result['product_batches'] = self.env[
                'furniture.mrp.stage.product.batch'
            ]._dashboard_product_batches(selected)
        return result

    @api.model
    def get_need_supervisor_stage_sections(self, stage_code, date_from=False, date_to=False):
        profile = self._stage_dashboard_check_stage_access(stage_code)
        if not profile['is_manager']:
            return super().get_need_supervisor_stage_sections(stage_code, date_from, date_to)
        return self._manager_stage_sections(stage_code, date_from, date_to)

    @api.model
    def _manager_stage_sections(self, stage_code, date_from=False, date_to=False):
        profile = self._stage_dashboard_check_stage_access(stage_code)
        if not profile['is_manager']:
            raise AccessError('هذه المتابعة متاحة لمدير التصنيع فقط.')
        domain = [('company_id', 'in', self.env.companies.ids),
                  ('state', 'in', ['confirmed', 'in_production', 'done'])]
        domain += self._need_supervisor_date_domain(date_from, date_to)
        result = {'stage_code': stage_code, 'completed': [], 'waiting': [],
                  'completed_quantity': 0.0, 'waiting_quantity': 0.0, 'planned_order_count': 0}
        for production in self.search(domain, order='date_planned_start desc, id desc'):
            stage = production._stage_order_record(stage_code)
            if not stage and stage_code not in production._required_stage_codes():
                continue
            buckets = self._stage_dashboard_line_buckets(production, stage_code, stage)
            waiting = self._need_supervisor_waiting_lines(production, stage_code, buckets)
            if production.state in ('confirmed', 'in_production'):
                ready = self.env['furniture.mrp.stage.product.batch']._stage_ready_candidate_lines(
                    stage_code, buckets['not_started_lines'],
                ) if stage_code in FURNITURE_STAGE_DASHBOARD_BATCH_CODES else buckets['not_started_lines']
                if ready or buckets['working_lines'] or waiting:
                    result['planned_order_count'] += 1
            for line in buckets['completed_lines']:
                row = self._need_supervisor_line_card(
                    line, stage_code, 'completed',
                    stage.end_date if stage and 'end_date' in stage._fields else False,
                )
                result['completed'].append(dict(row, production_id=production.id, production_name=production.name))
                result['completed_quantity'] += row['quantity']
            for line in waiting:
                row = self._need_supervisor_line_card(line, stage_code, 'waiting_upstream')
                result['waiting'].append(row)
                result['waiting_quantity'] += row['quantity']
        for key in ('completed_quantity', 'waiting_quantity'):
            result[key] = round(result[key], 3)
        return result

    @api.model
    def get_manager_stage_completed(self, stage_code, date_from=False, date_to=False):
        result = self._manager_stage_sections(stage_code, date_from, date_to)
        return {'rows': result['completed'], 'quantity': result['completed_quantity']}
