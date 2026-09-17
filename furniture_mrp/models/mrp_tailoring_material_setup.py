# -*- coding: utf-8 -*-
import base64
import binascii
import io
import re
import uuid

from PIL import Image as PillowImage, UnidentifiedImageError

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare
from odoo.tools.mimetypes import guess_mimetype


TAILORING_MATERIAL_KIND_SELECTION = [
    ('fabric', 'قماش'),
    ('takawe', 'تكاوي'),
]
TAILORING_PIECE_SIZE_SELECTION = [
    ('45', '45'),
    ('50', '50'),
    ('55', '55'),
    ('40x60', '40×60'),
]
TAILORING_METER_UOM_XMLID = 'furniture_mrp.furniture_uom_meter'
MATERIAL_SUMMARY_SEPARATOR = '\u00a0\u00a0•\u00a0\u00a0'
MATERIAL_SUMMARY_ITEM_SEPARATOR = '\u00a0\u00a0\u200f│\u200f\u00a0\u00a0'

SPECIAL_MATERIAL_STAGES = ('tailoring', 'upholstery')
TAILORING_SETUP_VIEWER_STAGE_SELECTION = [
    ('tailoring', 'التفصيل'),
    ('sewing', 'الخياطة'),
    ('upholstery', 'الكسوة'),
]
PIECE_IMAGE_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PIECE_IMAGE_MAX_PIXELS = 40_000_000
TAILORING_ORDER_IMAGE_LIMIT = 10
PIECE_IMAGE_FORMAT_MIMETYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
}


def _tailoring_meter_uom(env):
    """Return the single operational UoM used by fabric/takawe setup."""
    return env.ref(TAILORING_METER_UOM_XMLID)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    furniture_tailoring_material_kind = fields.Selection(
        TAILORING_MATERIAL_KIND_SELECTION,
        string='نوع الخامة',
        index=True,
        copy=True,
        help=(
            'اختياري: اختر قماش أو تكاوي. ترك الحقل فارغًا يعني أن الصنف '
            'ليس قماشًا ولا تكاوي.'
        ),
    )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    furniture_tailoring_material_kind = fields.Selection(
        related='product_tmpl_id.furniture_tailoring_material_kind',
        string='نوع الخامة',
        store=True,
        readonly=False,
        index=True,
    )

    def _furniture_tailoring_material_kind(self):
        """Return only the warehouse classification chosen on the product."""
        self.ensure_one()
        return self.product_tmpl_id.furniture_tailoring_material_kind or False


class FurnitureMrpBomStageLine(models.Model):
    _inherit = 'furniture.mrp.bom.stage.line'

    furniture_tailoring_material_kind = fields.Selection(
        [('fabric', 'قماش'), ('takawe', 'تكاوي')],
        string='دور الخامة في التقسيمة',
        help=(
            'حدد هذا الحقل للخامة الافتراضية التي يستبدلها '
            'زر القماش أو التكاوي في أمر الإنتاج.'
        ),
    )

    def _tailoring_setup_material_kind(self):
        self.ensure_one()
        return (
            self.furniture_tailoring_material_kind
            or self.product_id._furniture_tailoring_material_kind()
        )


class FurnitureMrpTailoringMaterialAllocation(models.Model):
    _name = 'furniture.mrp.tailoring.material.allocation'
    _description = 'خامة تفصيل أو كسوة مختارة لسطر أمر الإنتاج'
    _order = 'sequence, id'
    _check_company_auto = True

    def _check_setup_mutation_access(self, productions):
        """Guard every persistent write, including direct JSON-RPC calls."""
        if (
            self.env.is_superuser()
            and self.env.context.get('furniture_tailoring_setup_internal_write')
        ):
            return
        for production in productions.exists():
            production._lock_tailoring_material_setup()
            production._check_tailoring_material_setup_state()

    @api.model
    def _normalize_material_uom_values(self, values, production=False):
        """Force every fabric/takawe allocation to the operational meter."""
        vals = dict(values)
        product = self.env['product.product'].browse(
            vals.get('product_id')
        ).exists()
        if not product:
            return vals
        meter_uom = _tailoring_meter_uom(self.env)
        if meter_uom.category_id != product.uom_id.category_id:
            raise ValidationError(_(
                'خامة القماش أو التكاوي يجب أن تكون من فئة وحدة المتر.'
            ))
        vals['product_uom_id'] = meter_uom.id
        return vals

    sequence = fields.Integer(default=10)
    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر الإنتاج',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='production_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    material_kind = fields.Selection(
        [('fabric', 'قماش'), ('takawe', 'تكاوي')],
        string='نوع الخامة',
        required=True,
        index=True,
    )
    piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='مقاس التكاوي',
        copy=True,
        help='مقاس مستقل لهذا التكوي؛ القماش لا يحمل مقاسًا.',
    )
    product_id = fields.Many2one(
        'product.product',
        string='الخامة',
        required=True,
        ondelete='restrict',
        check_company=True,
        domain=[
            ('furniture_is_finished_product', '=', False),
            ('is_storable', '=', True),
        ],
    )
    qty = fields.Float(
        string='الكمية',
        required=True,
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        required=True,
        ondelete='restrict',
    )

    _sql_constraints = [
        (
            'tailoring_allocation_unique_material',
            'unique(production_line_id, material_kind, product_id, product_uom_id)',
            'الخامة مضافة بالفعل لنفس الصنف ونفس نوع التقسيمة.',
        ),
        (
            'tailoring_allocation_positive_qty',
            'check(qty > 0)',
            'كمية الخامة يجب أن تكون أكبر من صفر.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        productions = self.env['furniture.mrp.production']
        for incoming_vals in vals_list:
            vals = dict(incoming_vals)
            if vals.get('material_kind') == 'fabric':
                vals['piece_size'] = False
            if (
                'qty' in vals
                and float_compare(
                    vals.get('qty') or 0.0,
                    0.0,
                    precision_digits=3,
                ) <= 0
            ):
                raise ValidationError(_(
                    'كمية الخامة يجب أن تكون أكبر من صفر.'
                ))
            production_line = self.env['furniture.mrp.production.line'].browse(
                vals.get('production_line_id')
            ).exists()
            if production_line:
                productions |= production_line.production_id
                supplied_production_id = vals.get('production_id')
                if (
                    supplied_production_id
                    and supplied_production_id != production_line.production_id.id
                ):
                    raise ValidationError(_(
                        'سطر المنتج لا يتبع أمر الإنتاج المحدد للخامة.'
                    ))
                vals['production_id'] = production_line.production_id.id
            vals = self._normalize_material_uom_values(
                vals,
                production=production_line.production_id,
            ) if production_line else vals
            prepared_vals_list.append(vals)
        self._check_setup_mutation_access(productions)
        return super().create(prepared_vals_list)

    def write(self, vals):
        if len(self) > 1 and {
            'material_kind', 'product_id', 'product_uom_id', 'qty',
            'piece_size',
        }.intersection(vals):
            result = True
            for allocation in self:
                result = allocation.write(dict(vals)) and result
            return result
        productions = self.mapped('production_id')
        if vals.get('production_id'):
            productions |= self.env['furniture.mrp.production'].browse(
                vals['production_id']
            )
        if vals.get('production_line_id'):
            productions |= self.env['furniture.mrp.production.line'].browse(
                vals['production_line_id']
            ).production_id
        self._check_setup_mutation_access(productions)
        effective_kind = (
            vals.get('material_kind')
            or (self.material_kind if len(self) == 1 else False)
        )
        if effective_kind == 'fabric':
            vals['piece_size'] = False
        if self and {'product_id', 'product_uom_id', 'qty'}.intersection(vals):
            self.ensure_one()
            allocation = self
            normalized = self._normalize_material_uom_values({
                'product_id': vals.get('product_id', allocation.product_id.id),
                'product_uom_id': vals.get(
                    'product_uom_id',
                    allocation.product_uom_id.id,
                ),
                'qty': vals.get('qty', allocation.qty),
                'production_id': vals.get(
                    'production_id',
                    allocation.production_id.id,
                ),
            }, production=(
                productions[:1] or allocation.production_id
            ))
            vals = dict(vals)
            vals.update({
                'qty': normalized['qty'],
                'product_uom_id': normalized['product_uom_id'],
            })
        if (
            'qty' in vals
            and float_compare(
                vals.get('qty') or 0.0,
                0.0,
                precision_digits=3,
            ) <= 0
        ):
            raise ValidationError(_(
                'كمية الخامة يجب أن تكون أكبر من صفر.'
            ))
        return super().write(vals)

    def unlink(self):
        self._check_setup_mutation_access(self.mapped('production_id'))
        if (
            self.filtered(lambda allocation: allocation.material_kind == 'fabric')
            and not (
                self.env.is_superuser()
                and self.env.context.get(
                    'furniture_tailoring_setup_internal_write'
                )
            )
        ):
            raise ValidationError(_(
                'تغيير نوع القماش يتم من نافذة الأقمشة والتكاوي فقط؛ '
                'عدد الأمتار ثابت من الريسيبي.'
            ))
        return super().unlink()

    @api.constrains(
        'production_id', 'production_line_id', 'material_kind',
        'product_id', 'product_uom_id', 'qty', 'piece_size',
    )
    def _check_tailoring_allocation(self):
        for allocation in self:
            if allocation.production_line_id.production_id != allocation.production_id:
                raise ValidationError(_(
                    'سطر المنتج لا يتبع أمر الإنتاج المحدد للخامة.'
                ))
            if (
                allocation.production_line_id
                not in allocation.production_id._tailoring_material_setup_lines()
            ):
                raise ValidationError(_(
                    'يجب اختيار سطر صنف نشط يمر بمرحلة '
                    'التفصيل أو الكسوة.'
                ))
            if float_compare(
                allocation.qty or 0.0,
                0.0,
                precision_digits=3,
            ) <= 0:
                raise ValidationError(_('كمية الخامة يجب أن تكون أكبر من صفر.'))
            if allocation.material_kind == 'takawe' and not allocation.piece_size:
                raise ValidationError(_(
                    'اختار مقاسًا مستقلًا لكل تكوي قبل الحفظ.'
                ))
            if allocation.material_kind == 'fabric' and allocation.piece_size:
                raise ValidationError(_('القماش ليس له مقاس تشغيلي.'))
            if (
                allocation.product_id
                and allocation.product_uom_id
                and allocation.product_uom_id
                != _tailoring_meter_uom(allocation.env)
            ):
                raise ValidationError(_(
                    'وحدة القماش والتكاوي ثابتة بالمتر.'
                ))
            if (
                allocation.product_id.furniture_is_finished_product
                or allocation.product_id
                == allocation.production_line_id.product_id
            ):
                raise ValidationError(_(
                    'اختار مادة خام، وليس منتج أثاث نهائي.'
                ))
            if not allocation.product_id.is_storable:
                raise ValidationError(_(
                    'اختار خامة مخزنية يمكن صرفها من المخزن.'
                ))
            detected_kind = (
                allocation.product_id._furniture_tailoring_material_kind()
            )
            if not detected_kind:
                raise ValidationError(_(
                    'صنّف الخامة في المخزن كقماش أو تكاوي قبل إضافتها.'
                ))
            trusted_internal_write = bool(
                allocation.env.is_superuser()
                and allocation.env.context.get(
                    'furniture_tailoring_setup_internal_write'
                )
            )
            if allocation.material_kind == 'fabric' and not trusted_internal_write:
                fabric_allocations = (
                    allocation.production_line_id
                    .tailoring_material_allocation_ids.filtered(
                        lambda item: item.material_kind == 'fabric'
                    )
                )
                recipe_qty = (
                    allocation.production_line_id
                    ._tailoring_recipe_material_qty('fabric')
                )
                if len(fabric_allocations) != 1 or float_compare(
                    sum(fabric_allocations.mapped('qty')),
                    recipe_qty,
                    precision_digits=3,
                ) != 0:
                    raise ValidationError(_(
                        'اختار نوع قماش واحد فقط؛ عدد الأمتار ثابت من '
                        'الريسيبي وهو %(qty).3f متر.'
                    ) % {'qty': recipe_qty})


class FurnitureMrpProduction(models.Model):
    _inherit = 'furniture.mrp.production'

    tailoring_set_image_1920 = fields.Image(
        string='صورة الطقم داخل أمر الإنتاج',
        copy=False,
        max_width=1920,
        max_height=1920,
        help=(
            'الصورة الرئيسية للطقم داخل أمر الإنتاج وتظهر لمشرفي التفصيل '
            'والكسوة. لا تغيّر صورة أي منتج في المخزون.'
        ),
    )
    tailoring_set_image_attachment_ids = fields.Many2many(
        'ir.attachment',
        'furniture_mrp_production_tailoring_image_rel',
        'production_id',
        'attachment_id',
        string='صور إضافية للطقم داخل أمر الإنتاج',
        copy=False,
        help=(
            'صور تشغيل إضافية تخص هذا الأمر فقط، ولا تغيّر صور المنتجات '
            'في المخزون.'
        ),
    )
    tailoring_set_image_token = fields.Char(
        string='هوية صورة الطقم',
        copy=False,
        index=True,
        readonly=True,
    )
    tailoring_material_revision = fields.Integer(
        string='إصدار تقسيمة خامات التفصيل والكسوة',
        default=0,
        copy=False,
        readonly=True,
    )

    def _clear_order_piece_images(self):
        """Remove work-only set/piece images without touching product images."""
        production_ids = self.ids
        if not production_ids:
            return
        Line = self.env['furniture.mrp.production.line'].with_context(
            active_test=False,
        ).sudo()
        lines = Line.search([
            ('production_id', 'in', production_ids),
        ]).filtered(lambda line: (
            line.batch_image_1920 or line.batch_image_token
        ))
        if lines:
            lines.with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
                furniture_skip_line_consolidation=True,
            ).write({
                'batch_image_1920': False,
                'batch_image_token': False,
            })
        orders = self.filtered(lambda production: (
            production.tailoring_set_image_1920
            or production.tailoring_set_image_token
            or production.tailoring_set_image_attachment_ids
        ))
        if orders:
            extra_attachments = orders.sudo().mapped(
                'tailoring_set_image_attachment_ids'
            ).exists()
            orders.sudo().with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            ).write({
                'tailoring_set_image_1920': False,
                'tailoring_set_image_token': False,
                'tailoring_set_image_attachment_ids': [(5, 0, 0)],
            })
            if extra_attachments:
                extra_attachments.unlink()

    def write(self, vals):
        result = super().write(vals)
        if 'state' in vals:
            self.filtered(
                lambda production: production.state in ('done', 'cancelled')
            )._clear_order_piece_images()
        return result

    def unlink(self):
        # Clear attachment-backed fields before the production-line cascade so
        # no order-only image attachment can survive a deleted order.
        self._clear_order_piece_images()
        return super().unlink()

    def _tailoring_material_setup_lines(self):
        self.ensure_one()
        return self.production_line_ids.filtered(lambda line: (
            line.active
            and line.product_id
            and float_compare(
                line.product_qty or 0.0,
                0.0,
                precision_digits=3,
            ) > 0
            and (line.use_tailoring or line.use_upholstery)
        )).sorted(lambda line: (line.sequence, line.id))

    def _tailoring_material_viewer_lines(self, stage_code):
        """Return the saved setup rows relevant to one production stage."""
        self.ensure_one()
        if stage_code not in dict(TAILORING_SETUP_VIEWER_STAGE_SELECTION):
            raise UserError(_(
                'عرض تقسيمة الخامات متاح في التفصيل والخياطة والكسوة فقط.'
            ))
        setup_lines = self._tailoring_material_setup_lines()
        if stage_code in ('tailoring', 'sewing'):
            return setup_lines.filtered('use_tailoring')
        return setup_lines.filtered('use_upholstery')

    def _check_tailoring_material_setup_edit_access(self):
        """Keep every setup mutation exclusive to the production manager."""
        if self.env.is_superuser():
            return
        if not self.env.user.has_group(
            'furniture_mrp.group_furniture_mrp_manager'
        ):
            raise AccessError(_(
                'تعديل تقسيمة التفصيل والكسوة متاح لمدير الإنتاج فقط.'
            ))

    def _check_tailoring_material_setup_view_access(self):
        """Authorize a stage viewer without applying mutation-state locks."""
        self.ensure_one()
        self.check_access('read')
        if not self.env.is_superuser() and not (
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_manager'
            )
            or self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_supervisor'
            )
        ):
            raise AccessError(_(
                'عرض تقسيمة المرحلة متاح لمدير أو مشرف الإنتاج فقط.'
            ))
        if (
            not self.env.is_superuser()
            and self.company_id not in self.env.companies
        ):
            raise AccessError(_(
                'لا يمكنك عرض تقسيمة أمر إنتاج تابع لشركة غير مفعلة لحسابك.'
            ))

    def _lock_tailoring_material_setup(self):
        self.ensure_one()
        self.flush_recordset(['tailoring_material_revision'])
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production WHERE id = %s FOR UPDATE',
            [self.id],
        )
        line_ids = self._tailoring_material_setup_lines().ids
        if line_ids:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production_line '
                'WHERE id = ANY(%s) ORDER BY id FOR UPDATE',
                [line_ids],
            )
        self.invalidate_recordset([
            'state', 'tailoring_material_revision',
            'has_active_material_reservation',
        ])
        self.tailoring_order_id.invalidate_recordset(['state', 'date_start'])
        self.upholstery_order_id.invalidate_recordset(['state', 'date_start'])
        self.production_line_ids.invalidate_recordset([
            'active', 'product_id', 'furniture_order_model_id', 'bom_id',
            'product_qty', 'use_tailoring', 'use_upholstery',
            'batch_image_1920', 'batch_image_token',
            'tailoring_fabric_configured', 'tailoring_takawe_configured',
            'tailoring_material_allocation_ids',
        ])

    def _check_tailoring_material_setup_state(self):
        self.ensure_one()
        self._check_tailoring_material_setup_edit_access()
        if (
            not self.env.is_superuser()
            and self.company_id not in self.env.companies
        ):
            raise AccessError(_(
                'لا يمكنك تعديل خامات أمر إنتاج تابع لشركة '
                'غير مفعلة لحسابك.'
            ))
        self._check_kit_planner_state()
        if not self._tailoring_material_setup_lines():
            raise UserError(_(
                'لا توجد أصناف تمر بمرحلة التفصيل أو الكسوة داخل أمر الإنتاج.'
            ))

        if self.has_active_material_reservation:
            raise UserError(_(
                'يوجد حجز خامات نشط لهذا الأمر. فك الحجز أولًا ثم عدّل '
                'تقسيمة القماش والتكاوي وأعد فحص الخامات.'
            ))

        active_store_request = self.env['furniture.mrp.store.request'].sudo().search([
            ('production_id', '=', self.id),
            ('stage_code', 'in', ('tailoring', 'sewing', 'upholstery')),
            ('state', 'in', ('pending', 'approved', 'started')),
        ], limit=1)
        if active_store_request:
            raise UserError(_(
                'يوجد إذن خامات قائم للتفصيل أو الكسوة. ألغِ الإذن أو '
                'أكمل دورته قبل تغيير الخامات.'
            ))

        active_release_stage = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', '=', self.id),
            ('stage_code', 'in', ('tailoring', 'sewing', 'upholstery')),
            ('state', 'in', ('pending', 'issued', 'started')),
        ], limit=1)
        if active_release_stage:
            raise UserError(_(
                'تم إنشاء إذن صرف مبكر لخامات التفصيل أو الكسوة؛ '
                'لا يمكن تغيير الخامات المرتبطة به.'
            ))

        operated_material = self.material_line_ids.filtered(lambda line: (
            line.stage in SPECIAL_MATERIAL_STAGES
            and (
                line.move_id
                or line.warehouse_receipt_confirmed
                or line.warehouse_receipt_stage_id
            )
        ))[:1]
        if operated_material:
            raise UserError(_(
                'تم صرف أو استلام بعض خامات التفصيل أو الكسوة بالفعل؛ '
                'لا يمكن تعديل التقسيمة بعدها.'
            ))

    def action_open_tailoring_material_setup(self):
        self.ensure_one()
        self._check_tailoring_material_setup_state()
        wizard = self.env['furniture.mrp.tailoring.setup.wizard'].create({
            'production_id': self.id,
            'line_ids': [
                (0, 0, {
                    'sequence': line.sequence,
                    'production_line_id': line.id,
                })
                for line in self._tailoring_material_setup_lines()
            ],
        })
        return wizard._open_action()

    def action_open_tailoring_material_setup_viewer(self, stage_code):
        """Open the manager-saved compact cards as a stage read-only view."""
        self.ensure_one()
        self._check_tailoring_material_setup_view_access()
        lines = self._tailoring_material_viewer_lines(stage_code)
        wizard = self.env['furniture.mrp.tailoring.setup.wizard'].create({
            'production_id': self.id,
            'view_only': True,
            'viewer_stage': stage_code,
            'line_ids': [
                (0, 0, {
                    'sequence': line.sequence,
                    'production_line_id': line.id,
                })
                for line in lines
            ],
        })
        return wizard._open_action()

    def _get_equivalent_production_line_groups(self):
        groups = super()._get_equivalent_production_line_groups()
        return [
            group
            for group in groups
            if not group.filtered(lambda line: (
                line.tailoring_fabric_configured
                or line.tailoring_takawe_configured
                or line.tailoring_material_allocation_ids
            ))
        ]


class FurnitureMrpProductionLine(models.Model):
    _inherit = 'furniture.mrp.production.line'

    tailoring_material_allocation_ids = fields.One2many(
        'furniture.mrp.tailoring.material.allocation',
        'production_line_id',
        string='خامات التفصيل والكسوة اليدوية',
        copy=False,
    )
    tailoring_fabric_configured = fields.Boolean(
        string='تم اختيار القماش يدويًا',
        default=False,
        copy=True,
    )
    tailoring_takawe_configured = fields.Boolean(
        string='تم اختيار التكاوي يدويًا',
        default=False,
        copy=True,
    )
    tailoring_fabric_piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='مقاس القماش القديم',
        copy=False,
        help='حقل توافق قديم فقط؛ القماش ليس له مقاس تشغيلي.',
    )
    tailoring_takawe_piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='مقاس التكاوي',
        copy=True,
        help='المقاس المختار للتكاوي لهذا الصنف داخل أمر الإنتاج.',
    )
    tailoring_piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='المقاس القديم المشترك',
        copy=False,
        help=(
            'حقل توافق للبيانات السابقة فقط؛ المقاس التشغيلي الحالي '
            'هو مقاس التكاوي.'
        ),
    )

    @api.model_create_multi
    def create(self, vals_list):
        trusted_internal_write = bool(
            self.env.is_superuser()
            and self.env.context.get('furniture_tailoring_setup_internal_write')
        )
        if not trusted_internal_write and any(
            vals.get('tailoring_fabric_configured')
            or vals.get('tailoring_takawe_configured')
            or vals.get('tailoring_fabric_piece_size')
            or vals.get('tailoring_takawe_piece_size')
            or vals.get('tailoring_piece_size')
            for vals in vals_list
        ):
            raise AccessError(_(
                'اختيار القماش والتكاوي يتم من نافذة '
                'تقسيمة التفصيل والكسوة فقط.'
            ))
        for vals in vals_list:
            if 'tailoring_fabric_piece_size' in vals:
                vals['tailoring_fabric_piece_size'] = False
        return super().create(vals_list)

    def copy(self, default=None):
        self.ensure_one()
        allocations = self.tailoring_material_allocation_ids
        copy_defaults = dict(default or {})
        route_changed = any(
            field_name in copy_defaults
            and (copy_defaults.get(field_name) or False)
            != (self[field_name].id or False)
            for field_name in ('product_id', 'furniture_order_model_id', 'bom_id')
        )
        copied_config = {
            'tailoring_fabric_configured': bool(
                self.tailoring_fabric_configured and not route_changed
            ),
            'tailoring_takawe_configured': bool(
                self.tailoring_takawe_configured and not route_changed
            ),
            'tailoring_fabric_piece_size': False,
            'tailoring_takawe_piece_size': (
                self.tailoring_takawe_piece_size
                if not route_changed else False
            ),
        }
        copy_defaults.update({
            'tailoring_fabric_configured': False,
            'tailoring_takawe_configured': False,
            'tailoring_fabric_piece_size': False,
            'tailoring_takawe_piece_size': False,
            'tailoring_piece_size': False,
        })
        copied_line = super().copy(copy_defaults)
        if (
            allocations
            and not route_changed
            and not self.env.context.get(
                'furniture_skip_tailoring_allocation_copy'
            )
        ):
            for allocation in allocations:
                allocation.sudo().with_context(
                    furniture_tailoring_setup_internal_write=True,
                ).copy({
                    'production_id': copied_line.production_id.id,
                    'production_line_id': copied_line.id,
                })
        if not route_changed and any(copied_config.values()):
            copied_line.sudo().with_context(
                furniture_skip_material_refresh=True,
                furniture_skip_stage_plan_sync=True,
                furniture_tailoring_setup_internal_write=True,
            ).write(copied_config)
            for material_kind in ('fabric', 'takawe'):
                if copied_line._tailoring_material_kind_configured(
                    material_kind
                ):
                    copied_line._sync_tailoring_material_kind(material_kind)
        return copied_line

    def _split_for_partial_quantity(self, qty_to_keep, preserve_progress=False):
        self.ensure_one()
        original_qty = self.product_qty
        allocations = self.tailoring_material_allocation_ids
        remaining_line = super()._split_for_partial_quantity(
            qty_to_keep,
            preserve_progress=preserve_progress,
        )
        if not remaining_line or not allocations or not original_qty:
            return remaining_line

        kept_factor = qty_to_keep / original_qty
        remaining_factor = remaining_line.product_qty / original_qty
        allocation_key = lambda allocation: (
            allocation.material_kind,
            allocation.product_id.id,
            allocation.product_uom_id.id,
        )
        copied_by_key = {
            allocation_key(allocation): allocation
            for allocation in remaining_line.tailoring_material_allocation_ids
        }
        for allocation in allocations:
            original_allocation_qty = allocation.qty
            copied_allocation = copied_by_key.get(allocation_key(allocation))
            allocation.sudo().with_context(
                furniture_tailoring_setup_internal_write=True,
            ).write({'qty': original_allocation_qty * kept_factor})
            if copied_allocation:
                copied_allocation.sudo().with_context(
                    furniture_tailoring_setup_internal_write=True,
                ).write({
                    'qty': original_allocation_qty * remaining_factor,
                })
        return remaining_line

    def write(self, vals):
        vals = dict(vals)
        route_fields = {'product_id', 'furniture_order_model_id', 'bom_id'}
        special_stage_fields = {'use_tailoring', 'use_upholstery'}
        configured_fields = {
            'tailoring_fabric_configured',
            'tailoring_takawe_configured',
            'tailoring_fabric_piece_size',
            'tailoring_takawe_piece_size',
            'tailoring_piece_size',
        }
        trusted_internal_write = bool(
            self.env.is_superuser()
            and self.env.context.get('furniture_tailoring_setup_internal_write')
        )
        if configured_fields.intersection(vals) and not trusted_internal_write:
            raise AccessError(_(
                'عدّل اختيار القماش أو التكاوي من نافذة '
                'تقسيمة التفصيل والكسوة فقط.'
            ))
        if 'tailoring_fabric_piece_size' in vals:
            vals['tailoring_fabric_piece_size'] = False
        if len(self) > 1 and (
            route_fields | special_stage_fields
        ).intersection(vals):
            result = True
            for line in self:
                result = line.write(dict(vals)) and result
            return result
        route_changed = self.filtered(lambda line: any(
            field_name in vals
            and vals.get(field_name) != line[field_name].id
            for field_name in route_fields
        ))
        special_stage_changed = self.filtered(lambda line: any(
            field_name in vals
            and bool(vals.get(field_name)) != bool(line[field_name])
            for field_name in special_stage_fields
        ))
        old_stage_by_line_kind = {
            (line.id, material_kind): (
                'tailoring'
                if (
                    material_kind == 'fabric' and line.use_tailoring
                ) or (
                    material_kind == 'takawe' and not line.use_upholstery
                )
                else 'upholstery'
            )
            for line in special_stage_changed
            for material_kind in ('fabric', 'takawe')
        }
        configured_structure_changed = (
            route_changed | special_stage_changed
        ).filtered(lambda line: (
            line.tailoring_material_allocation_ids
            or line.tailoring_fabric_configured
            or line.tailoring_takawe_configured
            or line.tailoring_fabric_piece_size
            or line.tailoring_takawe_piece_size
            or line.tailoring_piece_size
        ))
        for production in configured_structure_changed.mapped('production_id'):
            production._lock_tailoring_material_setup()
            production._check_tailoring_material_setup_state()
        if route_changed:
            vals.setdefault('tailoring_fabric_configured', False)
            vals.setdefault('tailoring_takawe_configured', False)
            vals.setdefault('tailoring_fabric_piece_size', False)
            vals.setdefault('tailoring_takawe_piece_size', False)
            vals.setdefault('tailoring_piece_size', False)
        cleared_special_lines = special_stage_changed.filtered(lambda line: (
            not bool(vals.get('use_tailoring', line.use_tailoring))
            and not bool(vals.get('use_upholstery', line.use_upholstery))
        ))
        if cleared_special_lines:
            vals.setdefault('tailoring_fabric_configured', False)
            vals.setdefault('tailoring_takawe_configured', False)
            vals.setdefault('tailoring_fabric_piece_size', False)
            vals.setdefault('tailoring_takawe_piece_size', False)
            vals.setdefault('tailoring_piece_size', False)
        internal_write = bool(route_changed or cleared_special_lines)
        result = super(
            FurnitureMrpProductionLine,
            self.with_context(
                furniture_tailoring_setup_internal_write=True,
            ) if internal_write else self,
        ).write(vals)
        if route_changed:
            route_changed.tailoring_material_allocation_ids.sudo().with_context(
                furniture_tailoring_setup_internal_write=True,
            ).unlink()
        for line in (
            special_stage_changed & configured_structure_changed
        ) - route_changed:
            disabled_stages = {
                stage_code
                for stage_code, field_name in (
                    ('tailoring', 'use_tailoring'),
                    ('upholstery', 'use_upholstery'),
                )
                if field_name in vals and not line[field_name]
            }
            if disabled_stages:
                line.production_id.material_line_ids.filtered(
                    lambda material: (
                        material.production_line_id == line
                        and material.stage in disabled_stages
                    )
                ).sudo().with_context(
                    furniture_skip_reservation_refresh=True,
                ).unlink()
            if not line.use_tailoring and not line.use_upholstery:
                line.tailoring_material_allocation_ids.sudo().with_context(
                    furniture_tailoring_setup_internal_write=True,
                ).unlink()
                continue
            for material_kind in ('fabric', 'takawe'):
                if line._tailoring_material_kind_configured(material_kind):
                    old_stage = old_stage_by_line_kind.get(
                        (line.id, material_kind)
                    )
                    new_stage = line._tailoring_allocation_stage(material_kind)
                    if old_stage and old_stage != new_stage:
                        line.production_id.material_line_ids.filtered(
                            lambda material: (
                                material.production_line_id == line
                                and material.stage == old_stage
                                and (
                                    material.tailoring_allocation_id.material_kind
                                    == material_kind
                                    or (
                                        material.tailoring_recipe_material_kind
                                        or material.product_id
                                        ._furniture_tailoring_material_kind()
                                    ) == material_kind
                                )
                            )
                        ).sudo().with_context(
                            furniture_skip_reservation_refresh=True,
                        ).unlink()
                    line._sync_tailoring_material_kind(material_kind)
        return result

    def _tailoring_material_kind_configured(self, material_kind):
        self.ensure_one()
        return bool(
            self.tailoring_fabric_configured
            if material_kind == 'fabric'
            else self.tailoring_takawe_configured
        )

    def _tailoring_piece_size_for_kind(self, material_kind):
        self.ensure_one()
        if material_kind == 'fabric':
            return False
        if material_kind == 'takawe':
            return self.tailoring_takawe_piece_size
        raise ValidationError(_('نوع الخامة غير صحيح.'))

    @api.model
    def _tailoring_piece_size_field(self, material_kind):
        if material_kind == 'fabric':
            return 'tailoring_fabric_piece_size'
        if material_kind == 'takawe':
            return 'tailoring_takawe_piece_size'
        raise ValidationError(_('نوع الخامة غير صحيح.'))

    def _tailoring_allocation_stage(self, material_kind):
        self.ensure_one()
        if material_kind == 'fabric':
            return 'tailoring' if self.use_tailoring else 'upholstery'
        return 'upholstery' if self.use_upholstery else 'tailoring'

    def _tailoring_recipe_material_values(self, material_kind):
        """Return the unmodified recipe quantity for one special material.

        Deliberately prepare the BoM without passing ``production_line`` so
        order-level quantity overrides and saved fabric/takawe substitutions
        cannot feed back into the recipe baseline.  This makes the recipe the
        authoritative source for fabric meters even after a fabric has already
        been replaced on the production order.
        """
        self.ensure_one()
        if material_kind not in dict(TAILORING_MATERIAL_KIND_SELECTION):
            raise ValidationError(_('نوع الخامة غير صحيح.'))
        production = self.production_id
        bom = production._find_bom_for_production_line(self)
        if not bom:
            return []
        stage = self._tailoring_allocation_stage(material_kind)
        commands = production._prepare_material_lines_from_bom_with_factor(
            bom,
            self.product_qty,
            dimension_factor=self._get_dimension_factor(),
            active_stage_codes=[stage],
        )
        meter_uom = _tailoring_meter_uom(self.env)
        values_by_key = {}
        for command in commands:
            if (
                not isinstance(command, (tuple, list))
                or len(command) < 3
                or command[0] != 0
            ):
                continue
            vals = command[2] or {}
            product = self.env['product.product'].browse(
                vals.get('product_id')
            ).exists()
            command_kind = (
                vals.get('tailoring_recipe_material_kind')
                or (
                    product._furniture_tailoring_material_kind()
                    if product else False
                )
            )
            if command_kind != material_kind or vals.get('stage') != stage:
                continue
            source_uom = self.env['uom.uom'].browse(
                vals.get('product_uom_id')
            ).exists()
            qty = vals.get('qty_needed') or 0.0
            if source_uom and source_uom != meter_uom:
                qty = source_uom._compute_quantity(
                    qty,
                    meter_uom,
                    round=False,
                )
            values = values_by_key.setdefault(product.id, {
                'sequence': len(values_by_key) * 10 + 10,
                'product_id': product.id,
                'qty': 0.0,
                'product_uom_id': meter_uom.id,
            })
            values['qty'] += qty
        return list(values_by_key.values())

    def _tailoring_recipe_material_qty(self, material_kind):
        self.ensure_one()
        return sum(
            values['qty']
            for values in self._tailoring_recipe_material_values(material_kind)
        )

    def _tailoring_material_editor_values(self, material_kind):
        """Return saved overrides, or recipe values until a kind is edited.

        Recipe defaults come from the already-scaled production material
        lines.  This preserves the production quantity, dimension factor and
        exact quantity calculated by the normal recipe engine.  The setup
        itself always uses the operational meter UoM.
        """
        self.ensure_one()
        meter_uom = _tailoring_meter_uom(self.env)
        allocations = self.tailoring_material_allocation_ids.filtered(
            lambda allocation: allocation.material_kind == material_kind
        ).sorted(lambda allocation: (allocation.sequence, allocation.id))
        if allocations:
            return [{
                'sequence': allocation.sequence,
                'product_id': allocation.product_id.id,
                'qty': allocation.qty,
                'product_uom_id': meter_uom.id,
                'piece_size': (
                    allocation.piece_size
                    if material_kind == 'takawe'
                    else False
                ),
            } for allocation in allocations]
        if self._tailoring_material_kind_configured(material_kind):
            # An explicitly saved empty list means that the manager removed
            # every default material for this kind.
            return []

        values = self._tailoring_recipe_material_values(material_kind)
        legacy_size = (
            self.tailoring_takawe_piece_size
            or self.tailoring_piece_size
            or False
        ) if material_kind == 'takawe' else False
        return [
            {**value, 'piece_size': legacy_size}
            for value in values
        ]

    def _apply_tailoring_material_allocations_to_commands(
        self, commands, active_stage_codes=None,
    ):
        self.ensure_one()
        active_stages = set(
            active_stage_codes
            if active_stage_codes is not None
            else self._selected_stage_codes()
        )
        configured_kinds = {
            kind
            for kind in ('fabric', 'takawe')
            if (
                self._tailoring_material_kind_configured(kind)
                and self._tailoring_allocation_stage(kind) in active_stages
            )
        }
        if not configured_kinds:
            return commands

        prepared_commands = []
        for command in commands:
            if (
                isinstance(command, (tuple, list))
                and len(command) >= 3
                and command[0] == 0
            ):
                vals = command[2] or {}
                product = self.env['product.product'].browse(
                    vals.get('product_id')
                )
                command_kind = (
                    vals.get('tailoring_recipe_material_kind')
                    or product._furniture_tailoring_material_kind()
                    if product
                    else False
                )
                if (
                    command_kind in configured_kinds
                    and vals.get('stage')
                    == self._tailoring_allocation_stage(command_kind)
                ):
                    continue
            prepared_commands.append(command)

        for allocation in self.tailoring_material_allocation_ids.sorted(
            lambda item: (item.sequence, item.id)
        ):
            if allocation.material_kind not in configured_kinds:
                continue
            prepared_commands.append((0, 0, {
                'production_line_id': self.id,
                'product_id': allocation.product_id.id,
                'product_uom_id': allocation.product_uom_id.id,
                'qty_needed': allocation.qty,
                'stage': self._tailoring_allocation_stage(
                    allocation.material_kind
                ),
                'quantity_mode': 'fixed',
                'tailoring_allocation_id': allocation.id,
            }))
        return prepared_commands

    def _sync_tailoring_material_kind(self, material_kind):
        self.ensure_one()
        production = self.production_id
        existing_lines = production.material_line_ids.filtered(lambda line: (
            line.production_line_id == self
            and line.stage == self._tailoring_allocation_stage(material_kind)
            and (
                line.tailoring_allocation_id.material_kind == material_kind
                or (
                    line.tailoring_recipe_material_kind
                    or line.product_id._furniture_tailoring_material_kind()
                ) == material_kind
            )
        ))
        if existing_lines.filtered(lambda line: (
            line.move_id
            or line.warehouse_receipt_confirmed
            or line.warehouse_receipt_stage_id
        )):
            raise UserError(_(
                'تم صرف أو استلام الخامة بالفعل؛ لا يمكن استبدالها.'
            ))
        existing_lines.sudo().with_context(
            furniture_skip_reservation_refresh=True,
        ).unlink()

        allocations = self.tailoring_material_allocation_ids.filtered(
            lambda allocation: allocation.material_kind == material_kind
        ).sorted(lambda allocation: (allocation.sequence, allocation.id))
        if not self._tailoring_material_kind_configured(material_kind):
            return
        self.env['furniture.mrp.material.line'].sudo().with_context(
            furniture_skip_reservation_refresh=True,
        ).create([{
            'production_id': production.id,
            'production_line_id': self.id,
            'product_id': allocation.product_id.id,
            'product_uom_id': allocation.product_uom_id.id,
            'qty_needed': allocation.qty,
            'stage': self._tailoring_allocation_stage(material_kind),
            'quantity_mode': 'fixed',
            'tailoring_allocation_id': allocation.id,
        } for allocation in allocations])


class FurnitureMrpMaterialLine(models.Model):
    _inherit = 'furniture.mrp.material.line'

    tailoring_allocation_id = fields.Many2one(
        'furniture.mrp.tailoring.material.allocation',
        string='اختيار خامة التفصيل والكسوة',
        readonly=True,
        copy=False,
        ondelete='cascade',
        index=True,
    )
    tailoring_recipe_material_kind = fields.Selection(
        [('fabric', 'قماش'), ('takawe', 'تكاوي')],
        string='دور خامة الريسيبي',
        readonly=True,
        copy=False,
        index=True,
    )

    @api.depends(
        'production_id.state',
        'production_line_id',
        'move_id',
        'warehouse_receipt_confirmed',
        'warehouse_receipt_stage_id',
        'tailoring_allocation_id',
    )
    @api.depends_context('uid')
    def _compute_bom_qty_editable(self):
        super()._compute_bom_qty_editable()
        for line in self.filtered('tailoring_allocation_id'):
            line.bom_qty_editable = False


class FurnitureMrpTailoringSetupWizard(models.TransientModel):
    _name = 'furniture.mrp.tailoring.setup.wizard'
    _description = 'تقسيمة خامات التفصيل والكسوة'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    view_only = fields.Boolean(
        string='عرض فقط',
        default=False,
        readonly=True,
        copy=False,
    )
    viewer_stage = fields.Selection(
        TAILORING_SETUP_VIEWER_STAGE_SELECTION,
        string='مرحلة العرض',
        readonly=True,
        copy=False,
    )
    viewer_stage_label = fields.Char(
        string='مرحلة العرض',
        compute='_compute_viewer_stage_label',
    )
    order_name = fields.Char(
        related='production_id.name',
        string='رقم أمر الإنتاج',
        readonly=True,
    )
    model_id = fields.Many2one(
        related='production_id.furniture_order_model_id',
        string='الموديل',
        readonly=True,
    )
    buyer_partner_id = fields.Many2one(
        related='production_id.buyer_partner_id',
        string='المشتري',
        readonly=True,
    )
    beneficiary_partner_id = fields.Many2one(
        related='production_id.beneficiary_partner_id',
        string='المستفيد',
        readonly=True,
    )
    order_image_128 = fields.Image(
        string='صورة الطقم داخل أمر الإنتاج',
        compute='_compute_order_image',
        max_width=128,
        max_height=128,
        attachment=False,
    )
    has_order_image = fields.Boolean(compute='_compute_has_order_image')
    order_image_cache_token = fields.Char(
        related='production_id.tailoring_set_image_token',
        readonly=True,
    )
    source_order_image_token = fields.Char(readonly=True, copy=False)
    line_ids = fields.One2many(
        'furniture.mrp.tailoring.setup.wizard.line',
        'wizard_id',
        string='الأصناف',
        copy=False,
    )
    active_editor_mode = fields.Selection(
        [
            ('fabric', 'قماش'),
            ('takawe', 'تكاوي'),
            # Kept only so already-open transient rows from the previous UI
            # remain readable during the live module upgrade.
            ('image', 'صورة القطعة'),
        ],
        string='المحرر المفتوح',
        readonly=True,
        copy=False,
    )
    active_production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر القطعة المختار',
        readonly=True,
        copy=False,
        ondelete='cascade',
    )
    active_product_id = fields.Many2one(
        related='active_production_line_id.product_id',
        string='الصنف المختار',
        readonly=True,
    )
    active_model_id = fields.Many2one(
        related='active_production_line_id.furniture_order_model_id',
        string='موديل الصنف المختار',
        readonly=True,
    )
    active_material_kind = fields.Selection(
        [('fabric', 'قماش'), ('takawe', 'تكاوي')],
        string='نوع الخامة المختار',
        readonly=True,
        copy=False,
    )
    active_material_kind_label = fields.Char(
        string='نوع الخامة المختار',
        compute='_compute_active_material_kind_label',
    )
    active_source_revision = fields.Integer(readonly=True, copy=False)
    active_source_product_id = fields.Many2one(
        'product.product',
        readonly=True,
        copy=False,
    )
    active_source_bom_id = fields.Many2one(
        'mrp.bom',
        readonly=True,
        copy=False,
    )
    active_source_product_qty = fields.Float(
        readonly=True,
        copy=False,
        digits=(16, 3),
    )
    active_source_stage_signature = fields.Char(readonly=True, copy=False)
    editor_line_ids = fields.One2many(
        'furniture.mrp.tailoring.setup.editor.line',
        'wizard_id',
        string='الخامات',
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for incoming_vals in vals_list:
            vals = dict(incoming_vals)
            production = self.env['furniture.mrp.production'].browse(
                vals.get('production_id')
            ).exists()
            if production:
                vals.setdefault(
                    'source_order_image_token',
                    production.tailoring_set_image_token or False,
                )
            prepared_vals_list.append(vals)
        return super().create(prepared_vals_list)

    @api.depends('production_id.tailoring_set_image_1920')
    def _compute_order_image(self):
        for wizard in self:
            image = wizard.production_id.tailoring_set_image_1920
            wizard.order_image_128 = image or False

    @api.depends('production_id.tailoring_set_image_token')
    def _compute_has_order_image(self):
        """Detect the image without touching binary data in bin-size reads.

        The web client asks for binary sizes.  Sharing this compute method with
        ``order_image_128`` made a Boolean-first read feed values such as
        ``5.15 Kb`` back into ``fields.Image``, which rejects them as non-base64.
        Every supported order-image write rotates the token, so it is the safe
        lightweight presence marker.
        """
        for wizard in self:
            wizard.has_order_image = bool(
                wizard.production_id.tailoring_set_image_token
            )

    def _check_order_image_snapshot(self):
        self.ensure_one()
        production = self.production_id
        production.invalidate_recordset([
            'tailoring_set_image_1920',
            'tailoring_set_image_token',
            'tailoring_set_image_attachment_ids',
        ])
        if (
            (production.tailoring_set_image_token or False)
            != (self.source_order_image_token or False)
        ):
            raise UserError(_(
                'تم تغيير صور الطقم من شاشة أخرى. اقفل النافذة وافتحها '
                'من جديد قبل تعديل الصور.'
            ))
        return production

    def _order_image_gallery_payload(self):
        """Return URL metadata only; never send full image binaries in RPC."""
        self.ensure_one()
        production = self.production_id
        production.invalidate_recordset([
            'tailoring_set_image_1920',
            'tailoring_set_image_token',
            'tailoring_set_image_attachment_ids',
        ])
        images = []
        if production.tailoring_set_image_token:
            images.append({
                'key': 'main',
                'res_model': production._name,
                'res_id': production.id,
                'res_field': 'tailoring_set_image_1920',
                'token': production.tailoring_set_image_token,
                'display_name': _('الصورة الرئيسية'),
            })
        for attachment in production.sudo().tailoring_set_image_attachment_ids.sorted(
            lambda item: (item.create_date or fields.Datetime.now(), item.id)
        ):
            images.append({
                'key': 'attachment:%s' % attachment.id,
                'res_model': 'ir.attachment',
                'res_id': attachment.id,
                'res_field': 'datas',
                'token': (
                    attachment.checksum
                    or fields.Datetime.to_string(attachment.write_date)
                    or str(attachment.id)
                ),
                'display_name': attachment.name or _('صورة إضافية'),
            })
        return images

    def get_order_image_gallery(self):
        wizard = self.exists()
        if not wizard:
            return []
        wizard.ensure_one()
        wizard.check_access('read')
        wizard.production_id._check_tailoring_material_setup_view_access()
        return wizard._order_image_gallery_payload()

    def upload_order_image(self, image_base64):
        """Save the single tailoring/upholstery image on the production."""
        wizard = self.exists()
        if not wizard:
            raise UserError(_(
                'نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد ثم اختار الصورة.'
            ))
        wizard.ensure_one()
        wizard.check_access('read')
        wizard._check_editable_setup_wizard()
        production = wizard.production_id
        production._check_tailoring_material_setup_state()
        validated_image = self.env[
            'furniture.mrp.tailoring.setup.wizard.line'
        ]._validated_piece_image_base64(image_base64)

        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        production = wizard._check_order_image_snapshot()
        image_token = uuid.uuid4().hex
        production.sudo().with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).write({
            'tailoring_set_image_1920': validated_image,
            'tailoring_set_image_token': image_token,
        })
        wizard.write({'source_order_image_token': image_token})
        return {'image_token': image_token}

    def add_order_image(self, image_base64, image_filename=False):
        """Append an order-only image while preserving the current main one."""
        wizard = self.exists()
        if not wizard:
            raise UserError(_(
                'نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد ثم أضف الصورة.'
            ))
        wizard.ensure_one()
        wizard.check_access('read')
        wizard._check_editable_setup_wizard()
        production = wizard.production_id
        production._check_tailoring_material_setup_state()
        validated_image = self.env[
            'furniture.mrp.tailoring.setup.wizard.line'
        ]._validated_piece_image_base64(image_base64)

        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        production = wizard._check_order_image_snapshot()
        extra_images = production.sudo().tailoring_set_image_attachment_ids
        image_count = int(bool(production.tailoring_set_image_token)) + len(
            extra_images
        )
        if image_count >= TAILORING_ORDER_IMAGE_LIMIT:
            raise ValidationError(_(
                'الحد الأقصى هو %s صور لأمر الإنتاج.'
            ) % TAILORING_ORDER_IMAGE_LIMIT)

        image_token = uuid.uuid4().hex
        if not production.tailoring_set_image_token:
            production.sudo().with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            ).write({
                'tailoring_set_image_1920': validated_image,
                'tailoring_set_image_token': image_token,
            })
        else:
            image_bytes = base64.b64decode(validated_image)
            mimetype = guess_mimetype(image_bytes, 'image/png')
            filename = re.split(
                r'[/\\\\]',
                str(image_filename or '').strip(),
            )[-1][:180]
            if not filename:
                extension = 'jpg' if mimetype == 'image/jpeg' else 'png'
                filename = 'tailoring-%s.%s' % (uuid.uuid4().hex[:10], extension)
            attachment_values = {
                'name': filename,
                'type': 'binary',
                'datas': validated_image,
                'mimetype': mimetype,
                'res_model': production._name,
                'res_id': production.id,
            }
            Attachment = self.env['ir.attachment'].sudo()
            if 'company_id' in Attachment._fields:
                attachment_values['company_id'] = production.company_id.id
            attachment = Attachment.create(attachment_values)
            production.sudo().with_context(
                furniture_skip_stage_plan_sync=True,
                furniture_skip_material_refresh=True,
            ).write({
                'tailoring_set_image_attachment_ids': [(4, attachment.id)],
                'tailoring_set_image_token': image_token,
            })
        wizard.write({'source_order_image_token': image_token})
        return {
            'image_token': image_token,
            'images': wizard._order_image_gallery_payload(),
        }

    def remove_order_image(self, image_key):
        """Remove one gallery image and promote an extra if the main is removed."""
        wizard = self.exists()
        if not wizard:
            raise UserError(_('نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد.'))
        wizard.ensure_one()
        wizard.check_access('read')
        wizard._check_editable_setup_wizard()
        production = wizard.production_id
        production._check_tailoring_material_setup_state()
        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        production = wizard._check_order_image_snapshot()
        attachments = production.sudo().tailoring_set_image_attachment_ids.sorted(
            lambda item: (item.create_date or fields.Datetime.now(), item.id)
        )
        write_values = {}
        attachment_to_remove = self.env['ir.attachment']
        if image_key == 'main':
            if not production.tailoring_set_image_token:
                raise UserError(_('الصورة الرئيسية لم تعد موجودة.'))
            if attachments:
                attachment_to_remove = attachments[0]
                write_values.update({
                    'tailoring_set_image_1920': attachment_to_remove.datas,
                    'tailoring_set_image_attachment_ids': [
                        (3, attachment_to_remove.id),
                    ],
                    'tailoring_set_image_token': uuid.uuid4().hex,
                })
            else:
                write_values.update({
                    'tailoring_set_image_1920': False,
                    'tailoring_set_image_token': False,
                })
        elif str(image_key or '').startswith('attachment:'):
            try:
                attachment_id = int(str(image_key).split(':', 1)[1])
            except (TypeError, ValueError) as error:
                raise ValidationError(_('معرّف الصورة غير صالح.')) from error
            attachment_to_remove = attachments.filtered(
                lambda attachment: attachment.id == attachment_id
            )[:1]
            if not attachment_to_remove:
                raise UserError(_('الصورة الإضافية لم تعد موجودة.'))
            write_values.update({
                'tailoring_set_image_attachment_ids': [
                    (3, attachment_to_remove.id),
                ],
                'tailoring_set_image_token': uuid.uuid4().hex,
            })
        else:
            raise ValidationError(_('معرّف الصورة غير صالح.'))

        production.sudo().with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).write(write_values)
        if attachment_to_remove:
            attachment_to_remove.unlink()
        image_token = write_values.get('tailoring_set_image_token') or False
        wizard.write({'source_order_image_token': image_token})
        return {
            'image_token': image_token,
            'images': wizard._order_image_gallery_payload(),
        }

    @api.depends('active_material_kind')
    def _compute_active_material_kind_label(self):
        labels = dict(self._fields['active_material_kind'].selection)
        for wizard in self:
            wizard.active_material_kind_label = labels.get(
                wizard.active_material_kind,
                '',
            )

    @api.depends('viewer_stage')
    def _compute_viewer_stage_label(self):
        labels = dict(TAILORING_SETUP_VIEWER_STAGE_SELECTION)
        for wizard in self:
            wizard.viewer_stage_label = labels.get(wizard.viewer_stage, '')

    def _check_editable_setup_wizard(self):
        self.ensure_one()
        if self.view_only:
            raise AccessError(_(
                'هذه نافذة عرض فقط؛ مدير الإنتاج يعدّل التقسيمة من أمر الإنتاج.'
            ))
        self.production_id._check_tailoring_material_setup_edit_access()

    def _inline_refresh_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'furniture_tailoring_setup_refresh',
            'params': {'wizard_id': self.id},
        }

    @staticmethod
    def _stage_signature(production_line):
        return ','.join(sorted(production_line._selected_stage_codes()))

    def _active_snapshot_values(self, production_line):
        self.ensure_one()
        return {
            'active_production_line_id': production_line.id,
            'active_source_revision':
                production_line.production_id.tailoring_material_revision,
            'active_source_product_id': production_line.product_id.id,
            'active_source_bom_id': production_line.bom_id.id,
            'active_source_product_qty': production_line.product_qty,
            'active_source_stage_signature':
                self._stage_signature(production_line),
        }

    def _check_active_editor_snapshot(self):
        self.ensure_one()
        production_line = self.active_production_line_id
        production = self.production_id
        if not self.active_editor_mode or not production_line:
            raise UserError(_('اختار الصنف ونوع الإضافة أولًا.'))
        if production_line.production_id != production:
            raise AccessError(_('السطر المختار لا يتبع أمر الإنتاج الحالي.'))
        if production.tailoring_material_revision != self.active_source_revision:
            raise UserError(_(
                'تم تعديل التقسيمة من شاشة أخرى. اقفل النافذة وافتحها '
                'من جديد قبل الحفظ.'
            ))
        if (
            production_line.product_id != self.active_source_product_id
            or production_line.bom_id != self.active_source_bom_id
            or float_compare(
                production_line.product_qty or 0.0,
                self.active_source_product_qty or 0.0,
                precision_digits=3,
            ) != 0
            or self._stage_signature(production_line)
            != (self.active_source_stage_signature or '')
        ):
            raise UserError(_(
                'تم تغيير الصنف أو كميته أو مراحله بعد فتح المحرر. '
                'اقفله وافتحه من جديد قبل الحفظ.'
            ))
        if production_line not in production._tailoring_material_setup_lines():
            raise UserError(_(
                'سطر المنتج لم يعد متاحًا في تقسيمة التفصيل والكسوة.'
            ))
        return production_line

    def _select_inline_editor(self, production_line, editor_mode):
        self.ensure_one()
        self._check_editable_setup_wizard()
        if editor_mode not in ('fabric', 'takawe'):
            raise UserError(_('نوع الإضافة غير صحيح.'))
        production = self.production_id
        production._check_tailoring_material_setup_state()
        if (
            production_line.production_id != production
            or production_line not in production._tailoring_material_setup_lines()
            or production_line not in self.line_ids.mapped('production_line_id')
        ):
            raise AccessError(_('السطر المختار لا يتبع هذه التقسيمة.'))

        material_kind = editor_mode
        material_values = production_line._tailoring_material_editor_values(
            material_kind
        )
        commands = [(5, 0, 0)] + [
            (0, 0, values)
            for values in material_values
        ]
        if not material_values:
            # The first raw-material row is ready immediately after pressing
            # the card's plus button; the user does not need another dialog or
            # another preliminary click.
            commands.append((0, 0, {
                'sequence': 10,
                'qty': 1.0,
            }))
        values = {
            **self._active_snapshot_values(production_line),
            'active_editor_mode': editor_mode,
            'active_material_kind': material_kind,
            'editor_line_ids': commands,
        }
        self.write(values)
        return self._inline_refresh_action()

    def _clear_active_editor(self):
        self.ensure_one()
        self.write({
            'active_editor_mode': False,
            'active_production_line_id': False,
            'active_material_kind': False,
            'active_source_revision': 0,
            'active_source_product_id': False,
            'active_source_bom_id': False,
            'active_source_product_qty': 0.0,
            'active_source_stage_signature': False,
            'editor_line_ids': [(5, 0, 0)],
        })

    def action_cancel_active_editor(self):
        self.ensure_one()
        self._check_editable_setup_wizard()
        self._clear_active_editor()
        return self._inline_refresh_action()

    def action_apply_active_editor(self):
        self.ensure_one()
        self._check_editable_setup_wizard()
        if self.active_editor_mode not in ('fabric', 'takawe'):
            raise UserError(_('اختار زر القماش أو التكاوي أولًا.'))
        if self.active_material_kind != self.active_editor_mode:
            raise AccessError(_('نوع الخامة المفتوح لا يطابق المحرر الحالي.'))
        incomplete_line = self.editor_line_ids.filtered(
            lambda line: not line.product_id or not line.product_uom_id
        )[:1]
        if incomplete_line:
            raise ValidationError(_(
                'كمّل اختيار الخامة ووحدة القياس، أو احذف السطر الفارغ.'
            ))
        production_line = self._check_active_editor_snapshot()
        editor = self.env['furniture.mrp.tailoring.material.wizard'].create({
            'production_line_id': production_line.id,
            'material_kind': self.active_material_kind,
            # Compatibility for an already-open pre-upgrade inline editor.
            # Only takawe has a size; fabric quantity comes from the recipe.
            'piece_size': (
                (
                    production_line._tailoring_piece_size_for_kind('takawe')
                    or '45'
                )
                if self.active_material_kind == 'takawe'
                else False
            ),
            'source_piece_size': (
                production_line._tailoring_piece_size_for_kind(
                    self.active_material_kind
                )
            ),
            'source_revision': self.active_source_revision,
            'source_product_id': self.active_source_product_id.id,
            'source_bom_id': self.active_source_bom_id.id,
            'source_product_qty': self.active_source_product_qty,
            'source_stage_signature': self.active_source_stage_signature,
            'line_ids': [
                (0, 0, {
                    'sequence': line.sequence,
                    'product_id': line.product_id.id,
                    'qty': line.qty,
                    'product_uom_id': line.product_uom_id.id,
                    'piece_size': (
                        line.piece_size
                        or production_line.tailoring_takawe_piece_size
                        or '45'
                    ) if self.active_material_kind == 'takawe' else False,
                })
                for line in self.editor_line_ids.sorted(
                    lambda line: (line.sequence, line.id)
                )
            ],
        })
        editor.with_context(
            furniture_tailoring_setup_inline=True,
        ).action_apply()
        self._clear_active_editor()
        return self._inline_refresh_action()

    def _open_action(self):
        self.ensure_one()
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_tailoring_setup_wizard_form'
        )
        initial_mode = 'readonly' if self.view_only else 'edit'
        action_name = (
            _('تقسيمة %s المعتمدة') % self.viewer_stage_label
            if self.view_only and self.viewer_stage_label
            else _('تقسيمة التفصيل والكسوة')
        )
        return {
            'type': 'ir.actions.act_window',
            'name': action_name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': dict(
                self.env.context,
                form_view_initial_mode=initial_mode,
            ),
        }


class FurnitureMrpTailoringSetupWizardLine(models.TransientModel):
    _name = 'furniture.mrp.tailoring.setup.wizard.line'
    _description = 'سطر مدمج لتقسيمة التفصيل والكسوة'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.tailoring.setup.wizard',
        required=True,
        ondelete='cascade',
    )
    view_only = fields.Boolean(
        related='wizard_id.view_only',
        string='عرض فقط',
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر الإنتاج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        related='production_line_id.product_id',
        string='المنتج',
        readonly=True,
    )
    model_id = fields.Many2one(
        related='production_line_id.furniture_order_model_id',
        string='الموديل',
        readonly=True,
    )
    kit_bom_id = fields.Many2one(
        related='production_line_id.kit_bom_id',
        string='الطقم',
        readonly=True,
    )
    kit_instance_number = fields.Integer(
        related='production_line_id.kit_instance_number',
        string='رقم الطقم',
        readonly=True,
    )
    kit_display_label = fields.Char(
        string='هوية الطقم',
        compute='_compute_kit_display_label',
        compute_sudo=True,
    )
    product_qty = fields.Float(
        related='production_line_id.product_qty',
        string='الكمية',
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        related='production_line_id.product_uom_id',
        string='الوحدة',
        readonly=True,
    )
    fabric_piece_size = fields.Selection(
        related='production_line_id.tailoring_fabric_piece_size',
        string='مقاس القماش',
        readonly=True,
    )
    takawe_piece_size = fields.Selection(
        related='production_line_id.tailoring_takawe_piece_size',
        string='مقاس التكاوي',
        readonly=True,
    )
    piece_note = fields.Text(
        related='production_line_id.kit_piece_note',
        string='الملاحظات',
        readonly=True,
    )
    source_product_id = fields.Many2one(
        'product.product',
        readonly=True,
        copy=False,
    )
    source_model_id = fields.Many2one(
        'furniture.product.model',
        readonly=True,
        copy=False,
    )
    source_bom_id = fields.Many2one(
        'mrp.bom',
        readonly=True,
        copy=False,
    )
    source_product_qty = fields.Float(
        readonly=True,
        copy=False,
        digits=(16, 3),
    )
    source_stage_signature = fields.Char(readonly=True, copy=False)
    source_image_token = fields.Char(readonly=True, copy=False)
    source_piece_note = fields.Text(readonly=True, copy=False)
    image_cache_token = fields.Char(
        related='production_line_id.batch_image_token',
        readonly=True,
    )
    fabric_summary = fields.Char(
        string='القماش المختار',
        compute='_compute_material_summaries',
    )
    takawe_summary = fields.Char(
        string='التكاوي المختارة',
        compute='_compute_material_summaries',
    )
    is_active_editor = fields.Boolean(
        string='السطر المحدد',
        compute='_compute_is_active_editor',
    )
    image_128 = fields.Image(
        string='صورة القطعة داخل أمر الإنتاج',
        compute='_compute_image',
        max_width=128,
        max_height=128,
        attachment=False,
    )
    has_image = fields.Boolean(compute='_compute_image')

    @api.depends(
        'production_line_id.kit_bom_id',
        'production_line_id.kit_instance_number',
    )
    def _compute_kit_display_label(self):
        for row in self:
            kit_bom = row.production_line_id.kit_bom_id
            if not kit_bom:
                row.kit_display_label = False
                continue
            kit_product = (
                kit_bom.furniture_product_id
                or kit_bom.product_id
                or kit_bom.product_tmpl_id.product_variant_id
            )
            kit_name = kit_product.display_name or kit_bom.display_name
            instance_number = row.production_line_id.kit_instance_number
            row.kit_display_label = (
                _('%s · طقم %s') % (kit_name, instance_number)
                if instance_number
                else kit_name
            )

    @api.model
    def _piece_image_snapshot_values(self, production_line):
        return {
            'source_product_id': production_line.product_id.id,
            'source_model_id': production_line.furniture_order_model_id.id,
            'source_bom_id': production_line.bom_id.id,
            'source_product_qty': production_line.product_qty,
            'source_stage_signature': ','.join(sorted(
                production_line._selected_stage_codes()
            )),
            'source_image_token': production_line.batch_image_token or False,
            'source_piece_note': production_line.kit_piece_note or False,
        }

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = [dict(vals) for vals in vals_list]
        production_lines = self.env['furniture.mrp.production.line'].browse([
            vals['production_line_id']
            for vals in prepared_vals_list
            if vals.get('production_line_id')
        ]).exists()
        lines_by_id = {line.id: line for line in production_lines}
        for vals in prepared_vals_list:
            production_line = lines_by_id.get(vals.get('production_line_id'))
            if production_line:
                # These values are always server-generated.  They let a later
                # direct upload detect a stale or forged setup row.
                vals.update(self._piece_image_snapshot_values(production_line))
        return super().create(prepared_vals_list)

    @api.depends(
        'wizard_id.active_production_line_id',
        'wizard_id.active_editor_mode',
        'production_line_id',
    )
    def _compute_is_active_editor(self):
        for row in self:
            row.is_active_editor = bool(
                row.wizard_id.active_editor_mode
                and row.production_line_id
                == row.wizard_id.active_production_line_id
            )

    @api.depends(
        'production_line_id.tailoring_material_allocation_ids.product_id',
        'production_line_id.tailoring_material_allocation_ids.qty',
        'production_line_id.tailoring_material_allocation_ids.product_uom_id',
        'production_line_id.tailoring_material_allocation_ids.piece_size',
        'production_line_id.tailoring_fabric_configured',
        'production_line_id.tailoring_takawe_configured',
        'production_line_id.tailoring_takawe_piece_size',
        'production_line_id.tailoring_piece_size',
    )
    def _compute_material_summaries(self):
        size_labels = dict(TAILORING_PIECE_SIZE_SELECTION)
        for row in self:
            summaries = {'fabric': [], 'takawe': []}
            for material_kind in summaries:
                for values in row.production_line_id._tailoring_material_editor_values(
                    material_kind
                ):
                    product = self.env['product.product'].browse(
                        values['product_id']
                    )
                    product_uom = self.env['uom.uom'].browse(
                        values['product_uom_id']
                    )
                    summary = '%s%s%s %s' % (
                        product.display_name,
                        MATERIAL_SUMMARY_SEPARATOR,
                        format(values['qty'], '.3f').rstrip('0').rstrip('.'),
                        product_uom.display_name,
                    )
                    piece_size = values.get('piece_size')
                    if material_kind == 'takawe' and piece_size:
                        summary = _('%(summary)s · مقاس %(size)s') % {
                            'summary': summary,
                            'size': size_labels.get(piece_size, piece_size),
                        }
                    summaries[material_kind].append(
                        summary
                    )
            row.fabric_summary = MATERIAL_SUMMARY_ITEM_SEPARATOR.join(
                summaries['fabric']
            ) or False
            row.takawe_summary = MATERIAL_SUMMARY_ITEM_SEPARATOR.join(
                summaries['takawe']
            ) or False

    @api.depends('production_line_id.batch_image_1920')
    def _compute_image(self):
        for row in self:
            image = row.production_line_id.batch_image_1920
            row.image_128 = image or False
            row.has_image = bool(image)

    def action_select_fabric_editor(self):
        self.ensure_one()
        return self.wizard_id._select_inline_editor(
            self.production_line_id,
            'fabric',
        )

    def action_select_takawe_editor(self):
        self.ensure_one()
        return self.wizard_id._select_inline_editor(
            self.production_line_id,
            'takawe',
        )

    @api.model
    def _validated_piece_image_base64(self, image_base64):
        if not image_base64 or not isinstance(image_base64, (str, bytes)):
            raise ValidationError(_('اختار ملف صورة صحيح.'))
        if isinstance(image_base64, str):
            try:
                image_base64 = image_base64.encode('ascii', errors='strict')
            except UnicodeEncodeError as error:
                raise ValidationError(_('ملف الصورة غير صالح.')) from error
        declared_mimetype = False
        if image_base64.startswith(b'data:'):
            header, separator, payload = image_base64.partition(b',')
            match = re.fullmatch(
                br'data:(image/(?:jpeg|png));base64',
                header,
                flags=re.IGNORECASE,
            )
            if not separator or not match:
                raise ValidationError(_('صيغة ملف الصورة غير صالحة.'))
            declared_mimetype = match.group(1).decode('ascii').lower()
            image_base64 = payload
        image_base64 = b''.join(image_base64.split())
        max_encoded_size = ((PIECE_IMAGE_MAX_UPLOAD_BYTES + 2) // 3) * 4
        if len(image_base64) > max_encoded_size:
            raise ValidationError(_('حجم الصورة أكبر من 20 ميجابايت.'))
        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValidationError(_('ملف الصورة غير صالح.')) from error
        if not image_bytes:
            raise ValidationError(_('ملف الصورة فارغ.'))
        if len(image_bytes) > PIECE_IMAGE_MAX_UPLOAD_BYTES:
            raise ValidationError(_('حجم الصورة أكبر من 20 ميجابايت.'))

        try:
            with PillowImage.open(io.BytesIO(image_bytes)) as image:
                image_format = (image.format or '').upper()
                width, height = image.size
                frame_count = getattr(image, 'n_frames', 1)
                if image_format not in PIECE_IMAGE_FORMAT_MIMETYPES:
                    raise ValidationError(_(
                        'اختار صورة JPG أو PNG فقط.'
                    ))
                if (
                    width <= 0
                    or height <= 0
                    or width * height > PIECE_IMAGE_MAX_PIXELS
                    or frame_count != 1
                ):
                    raise ValidationError(_(
                        'أبعاد الصورة غير صالحة أو الصورة كبيرة جدًا.'
                    ))
                image.verify()
            with PillowImage.open(io.BytesIO(image_bytes)) as image:
                if (image.format or '').upper() != image_format:
                    raise ValueError('Image format changed while decoding')
                image.load()
        except (
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
            PillowImage.DecompressionBombError,
        ) as error:
            raise ValidationError(_('ملف الصورة تالف أو غير صالح.')) from error

        expected_mimetype = PIECE_IMAGE_FORMAT_MIMETYPES.get(image_format)
        if (
            not expected_mimetype
            or guess_mimetype(image_bytes, '') != expected_mimetype
            or (
                declared_mimetype
                and declared_mimetype != expected_mimetype
            )
        ):
            raise ValidationError(_('اختار صورة JPG أو PNG فقط.'))
        return base64.b64encode(image_bytes)

    def _check_piece_context_snapshot(self):
        self.ensure_one()
        production_line = self.production_line_id
        production = production_line.production_id
        if not production_line or self.wizard_id.production_id != production:
            raise AccessError(_('سطر الصورة لا يتبع أمر الإنتاج الحالي.'))
        if production_line not in production._tailoring_material_setup_lines():
            raise AccessError(_('السطر المختار لم يعد متاحًا في هذه التقسيمة.'))
        if (
            production_line.product_id != self.source_product_id
            or production_line.furniture_order_model_id != self.source_model_id
            or production_line.bom_id != self.source_bom_id
            or float_compare(
                production_line.product_qty or 0.0,
                self.source_product_qty or 0.0,
                precision_digits=3,
            ) != 0
            or ','.join(sorted(production_line._selected_stage_codes()))
            != (self.source_stage_signature or '')
        ):
            raise UserError(_(
                'تم تغيير الصنف أو الموديل أو الكمية أو المراحل بعد فتح '
                'النافذة. اقفلها وافتحها من جديد قبل حفظ أي تعديل.'
            ))
        return production_line

    def _check_piece_image_snapshot(self):
        self.ensure_one()
        production_line = self._check_piece_context_snapshot()
        if (
            (production_line.batch_image_token or False)
            != (self.source_image_token or False)
        ):
            raise UserError(_(
                'تم تغيير صورة القطعة من شاشة أخرى. اقفل النافذة وافتحها '
                'من جديد قبل استبدال الصورة.'
            ))
        return production_line

    def save_piece_note(self, note):
        """Persist one row note without making the whole Kanban editable."""
        row = self.exists()
        if not row:
            raise UserError(_(
                'نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد ثم اكتب الملاحظة.'
            ))
        row.ensure_one()
        row.check_access('read')
        row.wizard_id._check_editable_setup_wizard()
        if note is not False and not isinstance(note, str):
            raise ValidationError(_('نص الملاحظة غير صالح.'))
        note = (note or '').strip()
        if len(note) > 2000:
            raise ValidationError(_('الملاحظة لا يمكن أن تتجاوز 2000 حرف.'))

        production = row.production_line_id.production_id
        production._check_tailoring_material_setup_state()
        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        production_line = row._check_piece_context_snapshot()
        production_line.invalidate_recordset(['kit_piece_note'])
        if (
            (production_line.kit_piece_note or '')
            != (row.source_piece_note or '')
        ):
            raise UserError(_(
                'تم تغيير ملاحظة الصنف من شاشة أخرى. اقفل النافذة وافتحها '
                'من جديد قبل الحفظ.'
            ))
        production_line.sudo().with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            furniture_skip_line_consolidation=True,
        ).write({'kit_piece_note': note or False})
        row.write({'source_piece_note': note or False})
        return {'note': note}

    def upload_piece_image(self, image_base64):
        """Save a camera/file-picker image directly on this order piece.

        The transient row id is the capability boundary exposed to the client:
        it ties the chosen image to the setup wizard, production order, company,
        and exact production line that the user can currently see.
        """
        row = self.exists()
        if not row:
            raise UserError(_(
                'نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد ثم اختار الصورة.'
            ))
        row.ensure_one()
        row.check_access('read')
        row.wizard_id._check_editable_setup_wizard()
        production_line = row.production_line_id
        if not production_line or not row.wizard_id:
            raise UserError(_(
                'نافذة التقسيمة انتهت صلاحيتها. افتحها من جديد ثم اختار الصورة.'
            ))
        production = production_line.production_id
        if row.wizard_id.production_id != production:
            raise AccessError(_('سطر الصورة لا يتبع أمر الإنتاج الحالي.'))

        # Reject unauthorized/closed orders before doing image decoding work.
        production._check_tailoring_material_setup_state()
        validated_image = row._validated_piece_image_base64(image_base64)

        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        production_line = row._check_piece_image_snapshot()
        image_token = uuid.uuid4().hex
        production_line.sudo().with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
            furniture_skip_line_consolidation=True,
        ).write({
            'batch_image_1920': validated_image,
            'batch_image_token': image_token,
        })
        # Keep this exact open setup row fresh so the same user may replace
        # the image again, while older setup windows remain protected.
        row.write({'source_image_token': image_token})
        return {'image_token': image_token}

    def _open_material_editor(self, material_kind):
        self.ensure_one()
        self.wizard_id._check_editable_setup_wizard()
        if material_kind not in ('fabric', 'takawe'):
            raise UserError(_('نوع الخامة غير صحيح.'))
        production = self.production_line_id.production_id
        production._check_tailoring_material_setup_state()
        material_values = (
            self.production_line_id._tailoring_material_editor_values(
                material_kind
            )
        )
        wizard = self.env['furniture.mrp.tailoring.material.wizard'].create({
            'setup_wizard_id': self.wizard_id.id,
            'production_line_id': self.production_line_id.id,
            'material_kind': material_kind,
            'piece_size': (
                self.production_line_id._tailoring_piece_size_for_kind(
                    material_kind
                )
            ),
            'source_piece_size': (
                self.production_line_id._tailoring_piece_size_for_kind(
                    material_kind
                )
            ),
            'source_revision': production.tailoring_material_revision,
            'source_product_id': self.production_line_id.product_id.id,
            'source_bom_id': self.production_line_id.bom_id.id,
            'source_product_qty': self.production_line_id.product_qty,
            'source_stage_signature': ','.join(sorted(
                self.production_line_id._selected_stage_codes()
            )),
            'selected_material_product_ids': [(6, 0, list({
                values['product_id']
                for values in material_values
                if values.get('product_id')
            }))],
            'line_ids': [
                (0, 0, values)
                for values in material_values
            ],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_tailoring_material_wizard_form'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'furniture_tailoring_material_popup_open',
            'params': {
                'title': _(
                    'إضافة القماش'
                    if material_kind == 'fabric'
                    else 'إضافة التكاوي'
                ),
                'res_model': wizard._name,
                'res_id': wizard.id,
                'view_id': view.id,
                'size': 'xl',
                'context': dict(
                    self.env.context,
                    form_view_initial_mode='edit',
                ),
            },
        }

    def action_open_fabric_editor(self):
        return self._open_material_editor('fabric')

    def action_open_takawe_editor(self):
        return self._open_material_editor('takawe')

    def action_open_image_preview(self):
        self.ensure_one()
        line = self.production_line_id
        image = line.batch_image_1920
        if not image:
            raise UserError(_('لا توجد صورة مضافة لهذه القطعة داخل أمر الإنتاج.'))
        preview = self.env['furniture.mrp.tailoring.image.preview'].create({
            'production_id': line.production_id.id,
            'product_label': line.product_id.display_name,
            'model_label': (
                line.furniture_order_model_id.display_name
                if line.furniture_order_model_id
                else _('بدون موديل')
            ),
            'image_1920': image,
            'image_source_label': (
                _('صورة القطعة داخل أمر الإنتاج')
            ),
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_tailoring_image_preview_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('صورة %s') % line.product_id.display_name,
            'res_model': preview._name,
            'res_id': preview.id,
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'target': 'new',
        }


class FurnitureMrpTailoringSetupEditorLine(models.TransientModel):
    _name = 'furniture.mrp.tailoring.setup.editor.line'
    _description = 'سطر خامة مباشر داخل تقسيمة التفصيل والكسوة'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.tailoring.setup.wizard',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product',
        string='الخامة',
        domain=[
            ('furniture_is_finished_product', '=', False),
            ('is_storable', '=', True),
        ],
    )
    qty = fields.Float(
        string='الكمية',
        default=1.0,
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        default=lambda self: _tailoring_meter_uom(self.env).id,
    )
    piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='مقاس التكاوي',
    )
    uom_category_id = fields.Many2one(
        'uom.category',
        related='product_id.uom_id.category_id',
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        meter_uom = _tailoring_meter_uom(self.env)
        return super().create([
            {**values, 'product_uom_id': meter_uom.id}
            for values in vals_list
        ])

    def write(self, vals):
        if {'product_id', 'product_uom_id'}.intersection(vals):
            vals = {**vals, 'product_uom_id': _tailoring_meter_uom(self.env).id}
        return super().write(vals)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            line.product_uom_id = _tailoring_meter_uom(line.env)


class FurnitureMrpTailoringMaterialWizard(models.TransientModel):
    _name = 'furniture.mrp.tailoring.material.wizard'
    _description = 'إضافة قماش أو تكاوي لصنف أمر الإنتاج'

    setup_wizard_id = fields.Many2one(
        'furniture.mrp.tailoring.setup.wizard',
        string='نافذة التقسيمة',
        readonly=True,
        copy=False,
        ondelete='cascade',
    )
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line',
        string='سطر أمر الإنتاج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        related='production_line_id.product_id',
        string='الصنف',
        readonly=True,
    )
    model_id = fields.Many2one(
        related='production_line_id.furniture_order_model_id',
        string='الموديل',
        readonly=True,
    )
    material_kind = fields.Selection(
        [('fabric', 'قماش'), ('takawe', 'تكاوي')],
        string='نوع الخامة',
        required=True,
        readonly=True,
    )
    material_kind_label = fields.Char(
        string='نوع الخامة',
        compute='_compute_material_kind_label',
    )
    selected_material_product_ids = fields.Many2many(
        'product.product',
        'furniture_mrp_tailoring_material_wizard_product_rel',
        'wizard_id',
        'product_id',
        string='الخامات المختارة',
        domain=(
            "[('furniture_is_finished_product', '=', False), "
            "('is_storable', '=', True), "
            "('furniture_tailoring_material_kind', 'in', "
            "['fabric', 'takawe'])]"
        ),
    )
    piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='المقاس',
    )
    source_piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='المقاس عند فتح النافذة',
        readonly=True,
        copy=False,
    )
    source_revision = fields.Integer(readonly=True)
    source_product_id = fields.Many2one(
        'product.product',
        string='الصنف عند فتح النافذة',
        readonly=True,
    )
    source_bom_id = fields.Many2one(
        'mrp.bom',
        string='الريسيبي عند فتح النافذة',
        readonly=True,
    )
    source_product_qty = fields.Float(
        string='الكمية عند فتح النافذة',
        readonly=True,
        digits=(16, 3),
    )
    source_stage_signature = fields.Char(
        string='المراحل عند فتح النافذة',
        readonly=True,
    )
    line_ids = fields.One2many(
        'furniture.mrp.tailoring.material.wizard.line',
        'wizard_id',
        string='الخامات',
        copy=False,
    )

    @api.depends('material_kind')
    def _compute_material_kind_label(self):
        labels = dict(self._fields['material_kind'].selection)
        for wizard in self:
            wizard.material_kind_label = labels.get(wizard.material_kind, '')

    @api.onchange('selected_material_product_ids')
    def _onchange_selected_material_product_ids(self):
        meter_uom = _tailoring_meter_uom(self.env)
        for wizard in self:
            recipe_fabric_qty = (
                wizard.production_line_id._tailoring_recipe_material_qty(
                    'fabric'
                )
                if (
                    wizard.material_kind == 'fabric'
                    and wizard.production_line_id
                )
                else 0.0
            )

            def persistent_record(record):
                origin = record._origin
                return origin if origin and origin.id else record

            existing_by_product = {
                persistent_record(line.product_id).id: line
                for line in wizard.line_ids
                if line.product_id
            }
            selected_products = sorted(
                (
                    persistent_record(product)
                    for product in wizard.selected_material_product_ids
                ),
                key=lambda product: (
                    product.display_name or '',
                    product.id,
                ),
            )
            commands = [(5, 0, 0)]
            for sequence, product in enumerate(selected_products, start=1):
                previous = existing_by_product.get(product.id)
                commands.append((0, 0, {
                    'sequence': sequence * 10,
                    'product_id': product.id,
                    'qty': (
                        recipe_fabric_qty
                        if wizard.material_kind == 'fabric'
                        else previous.qty if previous else 1.0
                    ),
                    'product_uom_id': meter_uom.id,
                    'piece_size': (
                        previous.piece_size
                        if previous
                        else wizard.piece_size or False
                    ) if wizard.material_kind == 'takawe' else False,
                }))
            wizard.line_ids = commands

    @staticmethod
    def _stage_signature(production_line):
        return ','.join(sorted(production_line._selected_stage_codes()))

    def _validated_material_payload(self):
        self.ensure_one()
        payload_by_key = {}
        allocation_env = self.env[
            'furniture.mrp.tailoring.material.allocation'
        ]
        production = self.production_line_id.production_id
        authoritative_fabric_qty = 0.0
        if self.material_kind == 'fabric':
            if len(self.line_ids) != 1:
                raise ValidationError(_(
                    'اختار نوع قماش واحد فقط؛ عدد الأمتار يؤخذ '
                    'تلقائيًا من الريسيبي.'
                ))
            authoritative_fabric_qty = (
                self.production_line_id._tailoring_recipe_material_qty(
                    'fabric'
                )
            )
            if float_compare(
                authoritative_fabric_qty,
                0.0,
                precision_digits=3,
            ) <= 0:
                raise ValidationError(_(
                    'الريسيبي لا تحتوي على كمية قماش صالحة لهذا الصنف.'
                ))
        for wizard_line in self.line_ids.sorted(
            lambda line: (line.sequence, line.id)
        ):
            if not wizard_line.product_id:
                raise ValidationError(_('اختار الخامة في كل سطر مضاف.'))
            effective_qty = (
                authoritative_fabric_qty
                if self.material_kind == 'fabric'
                else wizard_line.qty
            )
            if float_compare(
                effective_qty or 0.0,
                0.0,
                precision_digits=3,
            ) <= 0:
                raise ValidationError(_('كمية الخامة يجب أن تكون أكبر من صفر.'))
            if not wizard_line.product_uom_id:
                raise ValidationError(_('حدد وحدة قياس الخامة.'))
            if (
                wizard_line.product_id.furniture_is_finished_product
                or wizard_line.product_id == self.production_line_id.product_id
            ):
                raise ValidationError(_(
                    'اختار مادة خام، وليس منتج أثاث نهائي.'
                ))
            if not wizard_line.product_id.is_storable:
                raise ValidationError(_(
                    'اختار خامة مخزنية يمكن صرفها من المخزن.'
                ))
            detected_kind = (
                wizard_line.product_id._furniture_tailoring_material_kind()
            )
            if not detected_kind:
                raise ValidationError(_(
                    'صنّف الخامة في المخزن كقماش أو تكاوي قبل إضافتها.'
                ))
            effective_piece_size = (
                wizard_line.piece_size
                if self.material_kind == 'takawe'
                else False
            )
            if self.material_kind == 'takawe' and not effective_piece_size:
                raise ValidationError(_(
                    'اختار المقاس للتكوي «%(product)s» قبل حفظ الخامات.'
                ) % {'product': wizard_line.product_id.display_name})
            normalized = allocation_env._normalize_material_uom_values({
                'product_id': wizard_line.product_id.id,
                'product_uom_id': wizard_line.product_uom_id.id,
                'qty': effective_qty,
                'production_id': production.id,
                'production_line_id': self.production_line_id.id,
            }, production=production)
            key = (
                normalized['product_id'],
                normalized['product_uom_id'],
            )
            existing = payload_by_key.get(key)
            if existing and existing['piece_size'] != effective_piece_size:
                raise ValidationError(_(
                    'لا يمكن إضافة نفس التكوي بمقاسين مختلفين؛ اختار '
                    'كل تكوي مرة واحدة وحدد مقاسه.'
                ))
            if existing:
                existing['qty'] += normalized['qty']
            else:
                payload_by_key[key] = {
                    'qty': normalized['qty'],
                    'piece_size': effective_piece_size,
                }
        return [
            {
                'sequence': sequence * 10,
                'product_id': key[0],
                'product_uom_id': key[1],
                'qty': values['qty'],
                'piece_size': values['piece_size'],
            }
            for sequence, (key, values) in enumerate(
                payload_by_key.items(),
                start=1,
            )
        ]

    def action_apply(self):
        self.ensure_one()
        production_line = self.production_line_id
        production = production_line.production_id
        piece_size_field = production_line._tailoring_piece_size_field(
            self.material_kind
        )
        production._check_tailoring_material_setup_edit_access()
        production._lock_tailoring_material_setup()
        production._check_tailoring_material_setup_state()
        if production.tailoring_material_revision != self.source_revision:
            raise UserError(_(
                'تم تعديل تقسيمة الخامات من شاشة أخرى. اقفل النافذة '
                'وافتحها من جديد قبل الحفظ.'
            ))
        if (
            production_line.product_id != self.source_product_id
            or production_line.bom_id != self.source_bom_id
            or float_compare(
                production_line.product_qty or 0.0,
                self.source_product_qty or 0.0,
                precision_digits=3,
            ) != 0
            or self._stage_signature(production_line)
            != (self.source_stage_signature or '')
            or (
                self.material_kind == 'takawe'
                and (production_line[piece_size_field] or False)
                != (self.source_piece_size or False)
            )
        ):
            raise UserError(_(
                'تم تغيير الصنف أو كميته أو مراحله بعد فتح '
                'النافذة. اقفلها وافتحها من جديد قبل الحفظ.'
            ))
        if production_line not in production._tailoring_material_setup_lines():
            raise UserError(_(
                'سطر المنتج لم يعد متاحًا في تقسيمة التفصيل والكسوة.'
            ))

        payload = self._validated_material_payload()
        takawe_sizes = {
            values['piece_size']
            for values in payload
            if values.get('piece_size')
        }
        legacy_common_size = (
            next(iter(takawe_sizes))
            if len(takawe_sizes) == 1
            else False
        )
        existing_allocations = (
            production_line.tailoring_material_allocation_ids.filtered(
                lambda allocation: allocation.material_kind
                == self.material_kind
            )
        )
        existing_allocations.sudo().with_context(
            furniture_tailoring_setup_internal_write=True,
        ).unlink()
        if payload:
            self.env[
                'furniture.mrp.tailoring.material.allocation'
            ].sudo().with_context(
                furniture_tailoring_setup_internal_write=True,
            ).create([{
                    **values,
                    'production_id': production.id,
                    'production_line_id': production_line.id,
                    'material_kind': self.material_kind,
                } for values in payload])

        configured_field = (
            'tailoring_fabric_configured'
            if self.material_kind == 'fabric'
            else 'tailoring_takawe_configured'
        )
        production_line.sudo().with_context(
            furniture_skip_material_refresh=True,
            furniture_skip_stage_plan_sync=True,
            furniture_tailoring_setup_internal_write=True,
        ).write({
            configured_field: True,
            piece_size_field: (
                legacy_common_size
                if self.material_kind == 'takawe'
                else False
            ),
        })
        # Only the selected piece/kind changed.  Rebuilding every material
        # line in the production order is unnecessary and makes this compact
        # save take several seconds on real orders with many recipe lines.
        production_line._sync_tailoring_material_kind(self.material_kind)
        production.sudo().write({
            'tailoring_material_revision':
                production.tailoring_material_revision + 1,
        })
        if self.material_kind == 'fabric':
            production._furniture_replan_proposed_mps(
                changed_stage_codes={'tailoring'},
            )
        if self.env.context.get('furniture_tailoring_setup_inline'):
            return True
        if self.setup_wizard_id:
            return self._return_to_setup_action()
        return production.action_open_tailoring_material_setup()

    def _return_to_setup_action(self):
        """Refresh the setup list mounted below the nested material dialog."""
        self.ensure_one()
        setup_wizard = self.setup_wizard_id.exists()
        if not setup_wizard:
            return (
                self.production_line_id.production_id
                .action_open_tailoring_material_setup()
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'furniture_tailoring_material_popup_saved',
            'params': {
                'editor_id': self.id,
                'wizard_id': setup_wizard.id,
                'res_model': setup_wizard._name,
                'res_id': setup_wizard.id,
            },
        }

    def action_back_to_setup(self):
        self.ensure_one()
        return self._return_to_setup_action()


class FurnitureMrpTailoringMaterialWizardLine(models.TransientModel):
    _name = 'furniture.mrp.tailoring.material.wizard.line'
    _description = 'سطر خامة داخل تقسيمة التفصيل والكسوة'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.tailoring.material.wizard',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product',
        string='الخامة',
        required=True,
        domain=[
            ('furniture_is_finished_product', '=', False),
            ('is_storable', '=', True),
        ],
    )
    qty = fields.Float(
        string='الكمية',
        required=True,
        default=1.0,
        digits=(16, 3),
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='وحدة القياس',
        required=True,
        default=lambda self: _tailoring_meter_uom(self.env).id,
    )
    piece_size = fields.Selection(
        TAILORING_PIECE_SIZE_SELECTION,
        string='مقاس التكاوي',
    )
    uom_category_id = fields.Many2one(
        'uom.category',
        related='product_id.uom_id.category_id',
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        meter_uom = _tailoring_meter_uom(self.env)
        return super().create([
            {**values, 'product_uom_id': meter_uom.id}
            for values in vals_list
        ])

    def write(self, vals):
        if {'product_id', 'product_uom_id'}.intersection(vals):
            vals = {**vals, 'product_uom_id': _tailoring_meter_uom(self.env).id}
        return super().write(vals)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            line.product_uom_id = _tailoring_meter_uom(line.env)

    @api.constrains('qty', 'product_id', 'product_uom_id')
    def _check_wizard_line(self):
        for line in self:
            if float_compare(
                line.qty or 0.0,
                0.0,
                precision_digits=3,
            ) <= 0:
                raise ValidationError(_('كمية الخامة يجب أن تكون أكبر من صفر.'))
            if (
                line.product_uom_id
                and line.product_uom_id != _tailoring_meter_uom(line.env)
            ):
                raise ValidationError(_(
                    'وحدة القماش والتكاوي ثابتة بالمتر.'
                ))


class FurnitureMrpTailoringImagePreview(models.TransientModel):
    _name = 'furniture.mrp.tailoring.image.preview'
    _description = 'معاينة صورة صنف تقسيمة التفصيل والكسوة'

    production_id = fields.Many2one(
        'furniture.mrp.production',
        string='أمر الإنتاج',
        required=True,
        readonly=True,
        ondelete='cascade',
    )

    product_label = fields.Char(string='الصنف', readonly=True)
    model_label = fields.Char(string='الموديل', readonly=True)
    image_1920 = fields.Image(
        string='الصورة',
        readonly=True,
        max_width=1920,
        max_height=1920,
        attachment=False,
    )
    image_source_label = fields.Char(string='مصدر الصورة', readonly=True)

    def action_back_to_setup(self):
        self.ensure_one()
        return self.production_id.action_open_tailoring_material_setup()
