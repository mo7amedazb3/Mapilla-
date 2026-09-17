# -*- coding: utf-8 -*-

from odoo import fields, models


class SpaServiceDepartment(models.Model):
    _name = 'spa.service.department'
    _description = 'SPA Service Department'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    service_ids = fields.One2many(
        'product.template',
        'costing_department_id',
        string="Services",
    )

    _sql_constraints = [
        (
            'department_name_company_uniq',
            'unique(name, company_id)',
            'The department name must be unique per company.',
        ),
    ]
