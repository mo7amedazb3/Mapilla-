# -*- coding: utf-8 -*-
from odoo import api, models, _
from odoo.exceptions import UserError


class ReportFurnitureMrpStageCost(models.AbstractModel):
    _name = 'report.furniture_mrp.report_furniture_mrp_stage_cost_details'
    _description = 'تقرير تكلفة مرحلة الإنتاج'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        docids = (
            docids
            or data.get('active_ids')
            or self.env.context.get('active_ids')
        )
        docs = self.env['furniture.mrp.production'].browse(docids).exists()
        stage_model = (
            data.get('stage_order_model')
            or self.env.context.get('furniture_stage_order_model')
        )
        stage_res_id = (
            data.get('stage_order_res_id')
            or self.env.context.get('furniture_stage_order_res_id')
        )
        if not docs or not stage_model or not stage_res_id or stage_model not in self.env:
            raise UserError(_('لم يتم تحديد مرحلة صحيحة لطباعة تقرير التكاليف.'))
        stage_order = self.env[stage_model].browse(int(stage_res_id)).exists()
        if not stage_order or stage_order.production_order_id not in docs:
            raise UserError(_('مرحلة الإنتاج لا تتبع أمر التصنيع المحدد.'))
        return {
            'doc_ids': docs.ids,
            'doc_model': 'furniture.mrp.production',
            'docs': docs,
            'stage_order': stage_order,
            'stage_data': stage_order.get_stage_cost_report_data(),
        }
