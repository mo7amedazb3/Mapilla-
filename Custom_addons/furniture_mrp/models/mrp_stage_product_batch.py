# -*- coding: utf-8 -*-

"""Persistent, product-first execution batches for restricted supervisors.

The dashboard deliberately exposes no production-order identity.  A batch is
the immutable server-side snapshot that ties one visible product quantity back
to its exact technical production lines, material rows and stage ledgers.
"""

import hashlib
import json
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .mrp_production_order import (
    FURNITURE_STAGE_FIELD_MAP,
    FURNITURE_STAGE_SELECTION,
)


PRODUCT_BATCH_STAGE_CODES = (
    'priming', 'painting', 'carpentry', 'bases', 'finishing',
)
PRODUCT_BATCH_ACTIVE_STATES = (
    'waiting_store', 'waiting_receipt', 'ready', 'in_progress',
)


class FurnitureMrpStageProductBatch(models.Model):
    _name = 'furniture.mrp.stage.product.batch'
    _description = 'دفعة صنف تشغيلية لمرحلة إنتاج'
    _order = 'id desc'

    @api.model
    def _uses_shared_hall_stock(self, stage_code):
        """Optional department supply policy; default remains per-order issue."""
        return False

    def _preflight_execution_materials(self, production, lines):
        release_stage = self._release_stage_for_production(production)
        if (
            release_stage.state not in ('issued', 'started')
            or not release_stage._product_batch_materials_received(lines)
        ):
            raise UserError(_('خامات أحد أعضاء الدفعة لم تُستلم بالكامل.'))

    def _prepare_execution_materials(self, production, lines):
        self._release_stage_for_production(
            production
        )._prepare_product_batch_for_stage_execution(lines, mark_started=True)

    token = fields.Char(required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        'res.company', required=True, readonly=True, copy=False, index=True,
    )
    stage_code = fields.Selection(
        FURNITURE_STAGE_SELECTION, required=True, readonly=True,
        copy=False, index=True,
    )
    identity_key = fields.Char(
        required=True, readonly=True, copy=False, index=True,
    )
    product_id = fields.Many2one(
        'product.product', required=True, readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
    model_id = fields.Many2one(
        'furniture.product.model', readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
    bom_id = fields.Many2one(
        'mrp.bom', readonly=True, copy=False, ondelete='restrict', index=True,
    )
    dimension_label = fields.Char(readonly=True, copy=False, index=True)
    uom_id = fields.Many2one(
        'uom.uom', required=True, readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
    production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furn_stage_product_batch_line_rel',
        'batch_id', 'production_line_id',
        string='سطور الإنتاج المثبتة', readonly=True, copy=False,
    )
    member_ids = fields.One2many(
        'furniture.mrp.stage.product.batch.member', 'batch_id',
        string='نسخة أعضاء الدفعة', readonly=True, copy=False,
    )
    planned_qty = fields.Float(
        required=True, readonly=True, copy=False, digits=(16, 3),
    )
    advance_release_id = fields.Many2one(
        'furniture.mrp.advance.material.release', readonly=True, copy=False,
        ondelete='restrict', index=True,
    )
    advance_release_stage_ids = fields.Many2many(
        'furniture.mrp.advance.material.release.stage',
        'furn_stage_product_batch_release_stage_rel',
        'batch_id', 'release_stage_id',
        string='مراحل أذونات الخامات المرتبطة',
        readonly=True, copy=False,
    )
    state = fields.Selection(
        [
            ('waiting_store', 'بانتظار المخزن'),
            ('waiting_receipt', 'بانتظار الاستلام'),
            ('ready', 'جاهز للبدء'),
            ('in_progress', 'قيد التشغيل'),
            ('done', 'مكتمل'),
            ('cancelled', 'ملغي'),
        ],
        required=True, readonly=True, copy=False,
        default='waiting_store', index=True,
    )
    requested_by_id = fields.Many2one(
        'res.users', required=True, readonly=True, copy=False,
        default=lambda self: self.env.user,
    )
    requested_at = fields.Datetime(
        required=True, readonly=True, copy=False,
        default=fields.Datetime.now,
    )
    started_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False,
    )
    started_at = fields.Datetime(readonly=True, copy=False)
    finished_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False,
    )
    finished_at = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ('stage_product_batch_token_unique', 'unique(token)',
         'رمز دفعة الصنف مستخدم بالفعل.'),
    ]

    @api.model
    def _normalize_identity_text(self, value):
        return ' '.join((value or '').strip().lower().split())

    @api.model
    def _identity_values_from_line(self, line):
        model = line.furniture_order_model_id
        bom = line.bom_id
        uom = line.product_uom_id or line.product_id.uom_id
        dimension_label = line.dimension_label or False
        identity_key = json.dumps([
            line.product_id.id or 0,
            model.id or 0,
            bom.id or 0,
            self._normalize_identity_text(dimension_label),
            uom.id or 0,
        ], ensure_ascii=False, separators=(',', ':'))
        return {
            'identity_key': identity_key,
            'product': line.product_id,
            'model': model,
            'bom': bom,
            'dimension_label': dimension_label,
            'uom': uom,
        }

    @api.model
    def _candidate_token(self, company, stage_code, identity_key, lines):
        # A rejected/cancelled release makes the same product lines eligible
        # again.  Include the latest cancelled generation so the fresh card
        # cannot collide with the immutable token of that historical request.
        cancelled_generation = self.sudo().search([
            ('company_id', '=', company.id),
            ('stage_code', '=', stage_code),
            ('identity_key', '=', identity_key),
            ('state', '=', 'cancelled'),
            ('production_line_ids', 'in', lines.ids),
        ], order='id desc', limit=1).id or 0
        raw = '%s|%s|%s|%s' % (
            company.id,
            stage_code,
            identity_key,
            '%s|retry:%s' % (
                ','.join(str(line_id) for line_id in sorted(lines.ids)),
                cancelled_generation,
            ),
        )
        return 'product-batch:%s' % hashlib.sha256(
            raw.encode('utf-8')
        ).hexdigest()

    @api.model
    def _advisory_lock_token(self, token):
        digest = hashlib.sha256((token or '').encode('utf-8')).digest()
        lock_key = int.from_bytes(digest[:8], 'big', signed=False)
        if lock_key >= (1 << 63):
            lock_key -= (1 << 64)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])

    @api.model
    def _stage_ready_candidate_lines(self, stage_code, lines):
        """Keep only exact product lines that may physically start a stage.

        First-stage lines are ready before their first start.  A downstream
        line is ready only after its traceable product move has reached that
        stage's work location.  Using the production's canonical selector
        here prevents raw-material releases from making an unfinished body
        appear early on the next supervisor dashboard.
        """
        lines = lines.exists()
        ready_lines = self.env['furniture.mrp.production.line']
        for production in lines.mapped('production_id').sorted('id'):
            production_lines = lines.filtered(
                lambda line, current=production: (
                    line.production_id == current
                )
            )
            stage_order = production._stage_order_record(stage_code)
            ready_lines |= (
                production._get_stage_pending_start_line_candidates(
                    stage_order, stage_code,
                )
                & production_lines
            )
        return ready_lines

    def _members_are_stage_ready(self):
        self.ensure_one()
        member_lines = self.member_ids.mapped(
            'production_line_id'
        ).exists()
        if not member_lines:
            return False
        ready_lines = self._stage_ready_candidate_lines(
            self.stage_code, member_lines,
        )
        return set(member_lines.ids).issubset(set(ready_lines.ids))

    def _assert_members_are_stage_ready(self):
        self.ensure_one()
        if not self._members_are_stage_ready():
            raise UserError(_(
                'الصنف لم يصل بعد إلى صالة المرحلة أو لم يكتمل في مرحلته السابقة.'
            ))
        return True

    @api.model
    def _candidate_groups(self, stage_code):
        if stage_code not in PRODUCT_BATCH_STAGE_CODES:
            return []
        productions = self.env['furniture.mrp.production'].search([
            ('company_id', 'in', self.env.companies.ids),
            ('state', 'in', ('confirmed', 'in_production')),
        ], order='id')
        productions = productions._visible_for_stage(stage_code)
        all_lines = productions.mapped('production_line_ids').filtered(
            lambda line: (
                line.active
                and not line.consolidated_into_line_id
                and line.product_id
                and float_compare(
                    line.product_qty or 0.0, 0.0, precision_digits=3,
                ) > 0
                and stage_code in line._selected_stage_codes()
            )
        )
        if not all_lines:
            return []
        all_lines = self._stage_ready_candidate_lines(stage_code, all_lines)
        if not all_lines:
            return []

        assigned_line_ids = set(self.env[
            'furniture.mrp.stage.product.batch.member'
        ].sudo().search([
            ('production_line_id', 'in', all_lines.ids),
            ('batch_id.stage_code', '=', stage_code),
            ('batch_id.state', '!=', 'cancelled'),
        ]).mapped('production_line_id').ids)
        active_release_stages = self.env[
            'furniture.mrp.advance.material.release.stage'
        ].sudo().search([
            ('production_id', 'in', all_lines.mapped('production_id').ids),
            ('stage_code', '=', stage_code),
            ('state', 'in', ('pending', 'issued', 'started')),
        ], order='id desc')
        release_stages_by_production = {}
        for release_stage in active_release_stages:
            production_stages = release_stages_by_production.setdefault(
                release_stage.production_id.id,
                self.env['furniture.mrp.advance.material.release.stage'],
            )
            release_stages_by_production[
                release_stage.production_id.id
            ] = production_stages | release_stage
        tracked_line_ids = set()
        for production in productions:
            stage_order = production._stage_order_record(stage_code)
            if not stage_order:
                continue
            for field_name in (
                'active_production_line_ids_data',
                'quality_production_line_ids_data',
                'completed_production_line_ids_data',
            ):
                tracked_line_ids.update(
                    stage_order._get_stage_line_ids_data(field_name).ids
                )

        groups = {}
        for line in all_lines.sorted(lambda item: item.id):
            if line.id in assigned_line_ids or line.id in tracked_line_ids:
                continue
            identity = self._identity_values_from_line(line)
            production_release_stages = release_stages_by_production.get(
                line.production_id.id,
                self.env['furniture.mrp.advance.material.release.stage'],
            )
            # An exact product-batch request covers only the production lines
            # frozen in its release stage.  Merely sharing the same production
            # order/stage must never unlock sibling product cards.
            release_stage = production_release_stages.filtered(
                lambda stage, current=line: (
                    current in stage.source_production_line_ids
                    or not stage.source_production_line_ids
                )
            )[:1]
            # Never mix already-requested members with unrequested members in
            # one card.  The former must follow the existing warehouse
            # release while the latter may legitimately create a new one.
            material_scope = 'covered' if release_stage else 'unrequested'
            group_key = (
                line.production_id.company_id.id,
                identity['identity_key'],
                material_scope,
            )
            group = groups.setdefault(group_key, {
                **identity,
                'company': line.production_id.company_id,
                'lines': self.env['furniture.mrp.production.line'],
                'release_stages': self.env[
                    'furniture.mrp.advance.material.release.stage'
                ],
                'planned_qty': 0.0,
            })
            group['lines'] |= line
            if release_stage:
                group['release_stages'] |= release_stage
            group['planned_qty'] += max(line.product_qty or 0.0, 0.0)

        result = []
        for group in groups.values():
            lines = group['lines'].sorted('id')
            if not lines:
                continue
            group['lines'] = lines
            group['planned_qty'] = round(group['planned_qty'], 3)
            group['batch_token'] = self._candidate_token(
                group['company'],
                stage_code,
                group['identity_key'],
                lines,
            )
            group['state'] = self._effective_state_from_release_stages(
                group['release_stages'],
                production_lines=lines,
                fallback='unrequested',
            )
            result.append(group)
        return sorted(result, key=lambda group: (
            group['product'].display_name or '',
            group['model'].display_name if group['model'] else '',
            group['dimension_label'] or '',
            group['identity_key'],
        ))

    @api.model
    def _effective_state_from_release_stages(
        self, release_stages, production_lines=False,
        fallback='waiting_store',
    ):
        release_stages = release_stages.exists()
        production_lines = production_lines.exists() if production_lines else self.env[
            'furniture.mrp.production.line'
        ]
        if not release_stages:
            return fallback
        states = set(release_stages.mapped('state'))
        if states & {'rejected', 'returned', 'cancelled'}:
            return 'cancelled'
        if states == {'completed'}:
            return 'done'
        if 'pending' in states:
            return 'waiting_store'
        if states.issubset({'issued', 'started', 'completed'}):
            pending_receipt = release_stages.filtered(
                lambda stage: (
                    stage.state in ('issued', 'started')
                    and not (
                        stage._product_batch_materials_received(
                            production_lines.filtered(
                                lambda line, current=stage: (
                                    line.production_id == current.production_id
                                )
                            )
                        )
                        if production_lines
                        else stage.receipt_confirmed
                    )
                )
            )
            return 'waiting_receipt' if pending_receipt else 'ready'
        return fallback

    def _linked_release_stages(self):
        self.ensure_one()
        stages = self.advance_release_stage_ids.exists()
        if stages:
            return stages
        release = self.advance_release_id.exists()
        if not release:
            return self.env['furniture.mrp.advance.material.release.stage']
        productions = self.member_ids.mapped('production_id').exists()
        return release.stage_line_ids.filtered(lambda stage: (
            stage.stage_code == self.stage_code
            and stage.production_id in productions
        ))

    def _effective_state(self):
        self.ensure_one()
        if self.state in ('in_progress', 'done', 'cancelled'):
            return self.state
        release_stages = self._linked_release_stages()
        if not release_stages:
            return self.state
        return self._effective_state_from_release_stages(
            release_stages,
            production_lines=self.member_ids.mapped(
                'production_line_id'
            ).exists(),
            fallback=self.state,
        )

    def _sync_state_from_release(self):
        for batch in self:
            effective_state = batch._effective_state()
            if effective_state != batch.state:
                batch.sudo().write({'state': effective_state})
        return True

    @api.model
    def _material_state_for_batch_state(self, state):
        return {
            'unrequested': 'not_requested',
            'waiting_store': 'waiting_store',
            'waiting_receipt': 'waiting_receipt',
            'ready': 'ready',
            'in_progress': 'consumed',
            'done': 'consumed',
            'cancelled': 'cancelled',
        }.get(state, 'not_requested')

    @api.model
    def _elapsed_seconds(self, started_at=False, finished_at=False):
        if not started_at:
            return 0
        start = fields.Datetime.to_datetime(started_at)
        finish = fields.Datetime.to_datetime(
            finished_at or fields.Datetime.now()
        )
        return max(0, int((finish - start).total_seconds()))

    @api.model
    def _sanitized_payload(self, values, state, persisted=False):
        product = values['product']
        model = values.get('model')
        bom = values.get('bom')
        uom = values.get('uom') or product.uom_id
        started_at = values.get('started_at') or False
        finished_at = values.get('finished_at') or False
        payload = {
            'delivery_notes': values.get('delivery_notes') or [],
            'batch_token': values['batch_token'],
            'persisted': bool(persisted),
            'identity_key': values['identity_key'],
            'stage_code': values['stage_code'],
            'product': {
                'id': product.id,
                'name': product.display_name,
            },
            'model': {
                'id': model.id if model else False,
                'name': model.display_name if model else False,
            },
            'bom': {
                'id': bom.id if bom else False,
                'name': bom.display_name if bom else False,
            },
            'dimension_label': values.get('dimension_label') or False,
            'uom': {
                'id': uom.id if uom else False,
                'name': uom.display_name if uom else False,
            },
            'planned_qty': round(values.get('planned_qty') or 0.0, 3),
            'display_qty': round(values.get('planned_qty') or 0.0, 3),
            'state': state,
            'material_state': self._material_state_for_batch_state(state),
            'started_at': (
                fields.Datetime.to_string(started_at) if started_at else False
            ),
            'finished_at': (
                fields.Datetime.to_string(finished_at) if finished_at else False
            ),
            'elapsed_seconds': self._elapsed_seconds(
                started_at, finished_at,
            ),
            'can_request': state == 'unrequested',
            'can_request_materials': state == 'unrequested',
            'can_receive': state == 'waiting_receipt',
            'can_receive_materials': state == 'waiting_receipt',
            'can_start': state == 'ready',
            'can_finish': state == 'in_progress',
            # The BoM is a pre-start reference for the supervisor.  Once the
            # batch starts, keep the operational card focused on its timer and
            # finish/pause actions.
            'can_open_bom': bool(bom and not started_at),
        }
        return payload

    def _dashboard_payload(self):
        self.ensure_one()
        state = self._effective_state()
        return self._sanitized_payload({
            'delivery_notes': self.member_ids.mapped('production_id')._delivery_set_notes_payload(),
            'batch_token': self.token,
            'identity_key': self.identity_key,
            'stage_code': self.stage_code,
            'product': self.product_id,
            'model': self.model_id,
            'bom': self.bom_id,
            'dimension_label': self.dimension_label,
            'uom': self.uom_id,
            'planned_qty': self.planned_qty,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
        }, state, persisted=True)

    @api.model
    def _dashboard_product_batches(self, stage_code):
        persisted = self.sudo().search([
            ('company_id', 'in', self.env.companies.ids),
            ('stage_code', '=', stage_code),
            ('state', '!=', 'cancelled'),
            ('member_ids.production_id.state', 'in', (
                'confirmed', 'in_production',
            )),
        ], order='id')
        payloads = []
        for batch in persisted:
            members = batch.member_ids.mapped('production_id').with_env(self.env)
            if len(members._visible_for_stage(stage_code)) != len(members):
                continue
            state = batch._effective_state()
            if state == 'cancelled':
                continue
            if (
                state not in ('in_progress', 'done')
                and not batch._members_are_stage_ready()
            ):
                continue
            payloads.append(batch._dashboard_payload())
        for group in self._candidate_groups(stage_code):
            payloads.append(self._sanitized_payload({
                **group,
                'delivery_notes': group['lines'].mapped('production_id')._delivery_set_notes_payload(),
                'stage_code': stage_code,
            }, group.get('state') or 'unrequested', persisted=False))
        return payloads

    def _lock_operation(self):
        self.ensure_one()
        release_stages = self._linked_release_stages().sorted('id')
        releases = release_stages.mapped('release_id').exists().sorted('id')
        if not releases:
            releases = self.advance_release_id.exists()
        for release in releases:
            release._lock()
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_stage_product_batch '
            'WHERE id = %s FOR UPDATE',
            [self.id],
        )
        productions = self.member_ids.mapped('production_id').exists().sorted('id')
        if productions:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(productions.ids)],
            )
        lines = self.member_ids.mapped('production_line_id').exists().sorted('id')
        if lines:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_production_line '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(lines.ids)],
            )
        if release_stages:
            self.env.cr.execute(
                'SELECT id FROM furniture_mrp_advance_material_release_stage '
                'WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(release_stages.ids)],
            )
        self.invalidate_recordset([
            'state', 'advance_release_id', 'advance_release_stage_ids',
        ])
        self.member_ids.invalidate_recordset([
            'production_line_id', 'qty_snapshot', 'identity_key',
        ])
        return releases, productions, lines, release_stages

    def _validate_member_snapshot(self):
        self.ensure_one()
        members = self.member_ids.exists()
        lines = members.mapped('production_line_id').exists()
        if not members or len(lines) != len(members):
            raise UserError(_(
                'عضوية دفعة الصنف لم تعد مكتملة. أوقف التشغيل وراجع مدير المصنع.'
            ))
        if set(lines.ids) != set(self.production_line_ids.ids):
            raise UserError(_('نسخة عضوية دفعة الصنف غير متطابقة.'))
        planned_qty = 0.0
        for member in members:
            line = member.production_line_id
            if (
                not line.active
                or line.consolidated_into_line_id
                or line.production_id.company_id != self.company_id
                or line.production_id.state not in ('confirmed', 'in_production')
                or self.stage_code not in line._selected_stage_codes()
            ):
                raise UserError(_(
                    'أحد أصناف الدفعة تغير أو لم يعد صالحًا لهذه المرحلة.'
                ))
            identity = self._identity_values_from_line(line)['identity_key']
            if identity != member.identity_key or identity != self.identity_key:
                raise UserError(_(
                    'هوية صنف داخل الدفعة تغيرت بعد طلب الخامات.'
                ))
            if float_compare(
                line.product_qty or 0.0,
                member.qty_snapshot,
                precision_digits=3,
            ) != 0:
                raise UserError(_(
                    'كمية صنف داخل الدفعة تغيرت بعد طلب الخامات.'
                ))
            planned_qty += member.qty_snapshot
        if float_compare(
            planned_qty, self.planned_qty, precision_digits=3,
        ) != 0:
            raise UserError(_('إجمالي كمية دفعة الصنف لم يعد مطابقًا لنسختها.'))
        return True

    @api.model
    def _find_persisted_token(self, batch_token, stage_code):
        batch = self.sudo().search([('token', '=', batch_token)], limit=1)
        if not batch:
            return batch
        if (
            batch.stage_code != stage_code
            or batch.company_id not in self.env.companies
        ):
            raise AccessError(_('رمز دفعة الصنف لا يخص المرحلة أو الشركة الحالية.'))
        members = batch.member_ids.mapped('production_id').with_env(self.env)
        if len(members._visible_for_stage(stage_code)) != len(members):
            raise AccessError(_('هذه الدفعة خارج تاريخ إظهار المرحلة.'))
        return batch

    @api.model
    def _virtual_group_for_token(self, batch_token, stage_code):
        group = next((
            candidate
            for candidate in self._candidate_groups(stage_code)
            if candidate['batch_token'] == batch_token
        ), False)
        if not group:
            raise AccessError(_(
                'دفعة الصنف تغيرت أو لم تعد متاحة. حدّث لوحة التحكم وحاول مجددًا.'
            ))
        return group

    @api.model
    def _resolve_batch_token(self, batch_token, stage_code, allow_virtual=False):
        batch_token = (batch_token or '').strip()
        if not batch_token:
            raise AccessError(_('رمز دفعة الصنف مطلوب.'))
        batch = self._find_persisted_token(batch_token, stage_code)
        if batch:
            return batch, False
        if allow_virtual:
            return self.browse(), self._virtual_group_for_token(
                batch_token, stage_code,
            )
        raise AccessError(_('دفعة الصنف المطلوبة غير موجودة.'))

    @api.model
    def _materialize_candidate(self, batch_token, stage_code):
        shared_hall_stock = self._uses_shared_hall_stock(stage_code)
        self._advisory_lock_token(batch_token)
        existing = self._find_persisted_token(batch_token, stage_code)
        if existing:
            return existing
        group = self._virtual_group_for_token(batch_token, stage_code)
        lines = group['lines'].exists().sorted('id')
        productions = lines.mapped('production_id').exists().sorted('id')
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(productions.ids)],
        )
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(lines.ids)],
        )
        # Rebuild under the row locks.  The deterministic token changes when
        # membership changes, so a stale screen can never capture hidden rows.
        group = self._virtual_group_for_token(batch_token, stage_code)
        lines = group['lines'].exists().sorted('id')
        productions = lines.mapped('production_id').exists().sorted('id')

        linked_release_stages = group.get('release_stages') or self.env[
            'furniture.mrp.advance.material.release.stage'
        ]
        active_conflict = self.env[
            'furniture.mrp.stage.product.batch.member'
        ].sudo().search([
            ('production_id', 'in', productions.ids),
            ('batch_id.stage_code', '=', stage_code),
            ('batch_id.state', 'in', PRODUCT_BATCH_ACTIVE_STATES),
        ], limit=1)
        # Product-specific hall receipt is deliberately independent from stage
        # execution.  Orders explicitly opted into parallel product batches may
        # create and start the next product while a sibling batch is running.
        parallel_execution = bool(productions) and all(
            productions.mapped('allow_parallel_product_batches')
        )
        if (
            active_conflict
            and not linked_release_stages
            and not parallel_execution
        ):
            raise UserError(_(
                'يوجد تشغيل صنف آخر نشط داخل نفس المرحلة لأحد الأوامر. '
                'أكمله أولًا ثم اطلب الدفعة التالية.'
            ))

        release_model = self.env['furniture.mrp.advance.material.release']
        covered_productions = linked_release_stages.mapped(
            'production_id'
        ).exists()
        if linked_release_stages and set(covered_productions.ids) != set(
            productions.ids
        ):
            raise UserError(_(
                'إذن الخامات القائم لا يغطي كل أعضاء دفعة الصنف. '
                'حدّث لوحة التحكم وحاول مجددًا.'
            ))
        for production in productions:
            production_lines = lines.filtered(
                lambda line, current=production: line.production_id == current
            )
            if not linked_release_stages and not shared_hall_stock:
                release_model._validate_stage_request(
                    production,
                    [stage_code],
                    production_lines=production_lines,
                )
                release_model._check_no_active_duplicates(
                    production, [stage_code],
                )
            production._ensure_stage_locations()
            # The dashboard has already authenticated the exact stage group,
            # company and opaque product-batch token.  Recipe rows are an
            # internal snapshot owned by the production order, whose ACL is
            # intentionally manager-only for direct edits.  Generate only
            # those validated stage/product rows under sudo so an operational
            # supervisor can request materials without receiving unrestricted
            # production-order write access.
            production.sudo()._get_or_create_first_stage_material_lines(
                production_lines.sudo(), stage_code,
            )
        for release_stage in linked_release_stages:
            release_stage._validate_current_snapshot(relink_sources=True)

        identity = self._identity_values_from_line(lines[0])
        release_headers = linked_release_stages.mapped(
            'release_id'
        ).exists()
        initial_state = self._effective_state_from_release_stages(
            linked_release_stages,
            production_lines=lines,
            fallback='ready' if shared_hall_stock else 'waiting_store',
        )
        batch = self.sudo().create({
            'token': batch_token,
            'company_id': productions[0].company_id.id,
            'stage_code': stage_code,
            'identity_key': identity['identity_key'],
            'product_id': identity['product'].id,
            'model_id': identity['model'].id or False,
            'bom_id': identity['bom'].id or False,
            'dimension_label': identity['dimension_label'],
            'uom_id': identity['uom'].id,
            'production_line_ids': [(6, 0, lines.ids)],
            'planned_qty': round(sum(lines.mapped('product_qty')), 3),
            'advance_release_id': (
                release_headers.id if len(release_headers) == 1 else False
            ),
            'advance_release_stage_ids': [
                (6, 0, linked_release_stages.ids)
            ],
            'state': initial_state,
            'requested_by_id': self.env.user.id,
            'member_ids': [(0, 0, {
                'production_line_id': line.id,
                'qty_snapshot': line.product_qty,
                'identity_key': identity['identity_key'],
            }) for line in lines],
        })
        if not linked_release_stages and not shared_hall_stock:
            release = release_model._create_from_product_batch(batch)
            batch.sudo().write({
                'advance_release_id': release.id,
                'advance_release_stage_ids': [
                    (6, 0, release.stage_line_ids.ids)
                ],
            })
        return batch

    def _result_envelope(self, action=False):
        self.ensure_one()
        result = {
            'ok': True,
            'delivery_notes': self.member_ids.mapped('production_id')._delivery_set_notes_payload(),
            'batch_token': self.token,
            'state': self._effective_state(),
        }
        if action:
            result['action'] = action
        return result

    def _repair_legacy_aggregate_receipt_scope(self):
        """Undo only sibling-product quantities moved by the old receipt code.

        This narrow recovery is intentionally explicit and is used only for a
        persisted product batch whose historical receipt moves prove that the
        old implementation moved both the selected and sibling product rows.
        """
        self.ensure_one()
        self._advisory_lock_token(self.token)
        self._lock_operation()
        self._validate_member_snapshot()
        repaired_moves = self.env['stock.move']
        for production, lines in self._stage_lines_by_production():
            release_stage = self._release_stage_for_production(production)
            if not release_stage.receipt_confirmed:
                continue
            release_production_lines = (
                release_stage.source_production_line_ids
                | release_stage.source_material_line_ids.mapped(
                    'production_line_id'
                )
            ).exists()
            outside_lines = release_production_lines - lines
            if not outside_lines:
                continue
            receipt_moves = release_stage.material_line_ids.mapped(
                'receipt_move_ids'
            ).sudo().filtered(lambda move: (
                move.state == 'done'
                and move.location_id
                == release_stage._material_handover_location()
                and move.location_dest_id == release_stage.destination_location_id
            ))
            has_mixed_legacy_move = any(
                bool(move.furniture_source_production_line_ids & lines)
                and bool(move.furniture_source_production_line_ids & outside_lines)
                for move in receipt_moves
            )
            if not has_mixed_legacy_move:
                continue

            allocations = release_stage._product_batch_allocations(
                outside_lines
            )
            specs = []
            outside_by_detail = {}
            for allocation in allocations:
                detail = allocation['detail']
                received_outside = allocation['source_lines'].filtered(
                    lambda source_line: (
                        source_line.warehouse_receipt_confirmed
                        and source_line.warehouse_receipt_stage_id
                        == release_stage
                    )
                )
                quantity = sum(
                    allocation['issued_by_line'][line.id]
                    for line in received_outside
                )
                outside_by_detail[detail.id] = received_outside
                if float_compare(
                    quantity, 0.0, precision_digits=3,
                ) <= 0:
                    continue
                specs.append({
                    'source_location': release_stage.destination_location_id,
                    'dest_location': release_stage._material_handover_location(),
                    'label': _(
                        'تصحيح استلام قديم: إعادة خامات الأصناف غير المستلمة'
                    ),
                    'product': detail.product_id,
                    'quantity': quantity,
                    'uom': detail.product_id.uom_id,
                    'source_production_lines': outside_lines,
                    'detail': detail,
                })

            returned_by_detail = {}
            for spec, move in production._create_internal_moves_batch(specs):
                repaired_moves |= move
                detail = spec['detail']
                returned_by_detail.setdefault(
                    detail.id, self.env['stock.move']
                )
                returned_by_detail[detail.id] |= move
            for allocation in allocations:
                detail = allocation['detail']
                returned_moves = returned_by_detail.get(
                    detail.id, self.env['stock.move']
                )
                if returned_moves:
                    detail.sudo().write({
                        'return_move_ids': [(6, 0, (
                            detail.return_move_ids | returned_moves
                        ).ids)],
                    })
                outside_by_detail.get(
                    detail.id, self.env['furniture.mrp.material.line']
                ).sudo().write({
                    'move_id': detail.move_id.id or False,
                    'warehouse_receipt_confirmed': False,
                    'warehouse_received_qty': 0.0,
                    'warehouse_receipt_stage_id': False,
                })
            release_stage._sync_product_batch_receipt_summary()

        if repaired_moves:
            self.member_ids.mapped('production_id').sudo().message_post(body=_(
                '🔧 تم تصحيح الاستلام القديم للدفعة %(product)s: '
                'أعيدت خامات الأصناف الأخرى إلى عهدة الانتظار.'
            ) % {'product': self.product_id.display_name})
        self._sync_state_from_release()
        return repaired_moves

    def _stage_lines_by_production(self):
        self.ensure_one()
        result = []
        for production in self.member_ids.mapped(
            'production_id'
        ).exists().sorted('id'):
            lines = self.member_ids.filtered(
                lambda member, current=production: (
                    member.production_id == current
                )
            ).mapped('production_line_id').exists().sorted('id')
            result.append((production, lines))
        return result

    def _release_stage_for_production(self, production):
        self.ensure_one()
        stage = self._linked_release_stages().filtered(
            lambda item: (
                item.production_id == production
                and item.stage_code == self.stage_code
            )
        )
        if len(stage) != 1:
            raise UserError(_(
                'إذن خامات دفعة الصنف لا يطابق أعضاء الدفعة.'
            ))
        return stage

    def _check_release_scope(self):
        self.ensure_one()
        if self._uses_shared_hall_stock(self.stage_code):
            return True
        stages = self._linked_release_stages()
        if not stages:
            raise UserError(_('إذن خامات دفعة الصنف غير موجود أو غير مترابط.'))
        expected_productions = self.member_ids.mapped('production_id').exists()
        actual_productions = stages.mapped('production_id').exists()
        if set(expected_productions.ids) != set(actual_productions.ids):
            raise UserError(_('نطاق أوامر إذن الخامات لا يطابق الدفعة.'))
        for production, lines in self._stage_lines_by_production():
            release_stage = self._release_stage_for_production(production)
            scoped_lines = release_stage.source_production_line_ids.exists()
            dedicated_release = (
                release_stage.release_id.product_batch_id == self
            )
            if dedicated_release and set(scoped_lines.ids) != set(lines.ids):
                raise UserError(_('نطاق سطور إذن الخامات لا يطابق الدفعة.'))
            if scoped_lines and lines - scoped_lines:
                raise UserError(_('الصنف غير مغطى بإذن خامات المرحلة القائم.'))
            release_stage._validate_current_snapshot(relink_sources=True)
        return True

    def _assert_stage_order_is_exclusive(self, stage_order, lines):
        self.ensure_one()
        if not stage_order:
            return True
        if self._allows_parallel_stage_execution():
            return True
        active = stage_order._get_stage_line_ids_data(
            'active_production_line_ids_data'
        )
        quality = stage_order._get_stage_line_ids_data(
            'quality_production_line_ids_data'
        )
        foreign = (active | quality) - lines
        if foreign:
            raise UserError(_(
                'يوجد صنف آخر قيد التشغيل في نفس أمر المرحلة. '
                'أكمله قبل تشغيل هذه الدفعة.'
            ))
        return True

    def _allows_parallel_stage_execution(self):
        """Allow concurrent product batches only on explicitly opted-in MOs.

        Product batches may aggregate the same identity across more than one
        manufacturing order, therefore every member order must opt in before
        the shared stage-order exclusivity guard is relaxed.
        """
        self.ensure_one()
        productions = self.member_ids.mapped('production_id').exists()
        return bool(productions) and all(
            productions.mapped('allow_parallel_product_batches')
        )

    @api.model
    def _request_materials(self, batch_token, stage_code):
        batch = self._materialize_candidate(batch_token, stage_code)
        batch._assert_members_are_stage_ready()
        batch._sync_state_from_release()
        batch._validate_member_snapshot()
        return batch._result_envelope()

    def _receive_materials(self):
        self.ensure_one()
        self._advisory_lock_token(self.token)
        self._lock_operation()
        self._validate_member_snapshot()
        self._check_release_scope()
        state = self._effective_state()
        if state in ('ready', 'in_progress', 'done'):
            self._sync_state_from_release()
            return self._result_envelope()
        if state != 'waiting_receipt':
            raise UserError(_(
                'الخامات لم تُصرف من المخزن بعد أو لم تعد صالحة للاستلام.'
            ))
        self._assert_members_are_stage_ready()

        release_stages = self._linked_release_stages().sorted(
            lambda stage: (stage.production_id.id, stage.id)
        )
        for stage in release_stages:
            if stage.state not in ('issued', 'started'):
                raise UserError(_('إذن إحدى مراحل الدفعة ليس في حالة الصرف.'))
            stage._validate_current_snapshot(relink_sources=True)
        for production, lines in self._stage_lines_by_production():
            self._release_stage_for_production(
                production
            )._receive_product_batch_materials(lines)
        self._sync_state_from_release()
        if self.state != 'ready':
            self.sudo().write({'state': 'ready'})
        return self._result_envelope()

    def _preflight_start(self):
        self.ensure_one()
        self._validate_member_snapshot()
        self._check_release_scope()
        if self._effective_state() != 'ready':
            raise UserError(_('استلم خامات الدفعة أولًا قبل بدء المرحلة.'))
        for production, lines in self._stage_lines_by_production():
            self._preflight_execution_materials(production, lines)
            stage_order = production._stage_order_record(self.stage_code)
            allowed_order_states = (
                ('pending', 'in_progress')
                if self._allows_parallel_stage_execution()
                else ('pending',)
            )
            if stage_order and stage_order.state not in allowed_order_states:
                raise UserError(_('أمر المرحلة ليس منتظرًا للبدء.'))
            self._assert_stage_order_is_exclusive(stage_order, lines)
            production._ensure_stage_required(self.stage_code)
            production._ensure_stage_locations()
            pending_lines = production._get_stage_pending_start_line_candidates(
                stage_order, self.stage_code,
            )
            invalid_lines = lines - pending_lines
            if invalid_lines:
                raise UserError(_(
                    'الصنف لم يصل بعد إلى صالة المرحلة أو لم يعد صالحًا للبدء.'
                ))
            if not stage_order:
                production._ensure_stage_start_available(self.stage_code)
        return True

    def _ensure_stage_order(self, production):
        self.ensure_one()
        stage_order = production._stage_order_record(self.stage_code)
        if stage_order:
            return stage_order
        start_method = getattr(
            production, 'action_start_%s' % self.stage_code, False,
        )
        if not start_method:
            raise UserError(_('تعذر إنشاء أمر المرحلة لهذه الدفعة.'))
        start_method()
        order_field = FURNITURE_STAGE_FIELD_MAP[self.stage_code][1]
        production.invalidate_recordset([order_field])
        stage_order = production[order_field]
        if not stage_order:
            raise UserError(_('تعذر إنشاء أمر المرحلة لهذه الدفعة.'))
        return stage_order

    def _start_batch(self):
        self.ensure_one()
        self._advisory_lock_token(self.token)
        self._lock_operation()
        state = self._effective_state()
        if state in ('in_progress', 'done'):
            self._sync_state_from_release()
            return self._result_envelope()
        self._preflight_start()

        # A lane order may already exist before its upstream FIFO output is
        # ready (for example an order generated by the Min/Max planner).  The
        # first click on "start" must persist the reservation and its durable
        # supervisor notification, then stop cleanly while acceptance is
        # pending.  Continuing into ``stage_order.action_start()`` would raise
        # the handoff guard and roll the whole transaction back, including the
        # just-created handoff and bus notification.  That left the operator
        # seeing "waiting for supervisor" while the supervisor had nothing to
        # accept.
        awaiting_handoff_acceptance = False
        for production, lines in self._stage_lines_by_production():
            if not production._furniture_handoff_required_lanes():
                continue
            production._furniture_reserve_required_handoffs(
                production_lines=lines,
            )
            if not production._furniture_accepted_handoffs_cover_lines(lines):
                awaiting_handoff_acceptance = True
        if awaiting_handoff_acceptance:
            return self._result_envelope()

        execution_rows = []
        for production, lines in self._stage_lines_by_production():
            stage_order = self._ensure_stage_order(production)
            production._stage_dashboard_assign_current_foreman(
                stage_order, self.stage_code,
            )
            if not stage_order._get_selected_labor_users():
                raise UserError(_(
                    'اربط المشرف الحالي بموظف نفس المرحلة قبل بدء الدفعة.'
                ))
            execution_rows.append((
                production,
                lines,
                stage_order,
            ))

        now = fields.Datetime.now()
        for production, lines, stage_order in execution_rows:
            self._prepare_execution_materials(production, lines)
            entry_lines = lines.filtered(lambda line: (
                not line.first_stage_started
                and (production._production_line_start_stage_code(line) == self.stage_code
                     or production._allows_received_parallel_entry(line, self.stage_code))
            ))
            downstream_lines = lines - entry_lines
            if downstream_lines.filtered(lambda line: not line.first_stage_started):
                raise UserError(_(
                    'يوجد صنف لم يبدأ مساره السابق قبل هذه المرحلة.'
                ))
            if entry_lines:
                stage_order.sudo().write({
                    'first_stage_production_line_ids': [(6, 0, (
                        stage_order.first_stage_production_line_ids
                        | entry_lines
                    ).ids)],
                })
                entry_lines.sudo().write({
                    'first_stage_started': True,
                    'first_stage_started_stage': self.stage_code,
                    'planned_start_stage': self.stage_code,
                })
            stage_order.with_context(
                furniture_skip_line_consolidation=True,
            )._add_stage_active_lines(lines)
            if stage_order.state == 'pending':
                stage_order.with_context(
                    furniture_storekeeper_approval_bypass=True,
                    furniture_skip_stage_start_prompt=True,
                    furniture_skip_material_move=True,
                    furniture_product_batch_material_scope=True,
                    furniture_stage_start_mode='first_stage_selected',
                ).action_start()
            elif (
                stage_order.state != 'in_progress'
                or not self._allows_parallel_stage_execution()
            ):
                raise UserError(_('أمر المرحلة ليس صالحًا لبدء هذه الدفعة.'))
            elif production._furniture_handoff_required_lanes():
                # ``action_start`` owns the first batch's actual-start handoff.
                # A parallel batch joins an already-running stage order and
                # therefore never enters that method; consume only this batch's
                # exact technical lines at the same execution boundary.
                production._furniture_consume_required_handoffs(lines)

        dedicated_releases = self._linked_release_stages().mapped(
            'release_id'
        ).filtered(
            lambda release: release.product_batch_id == self
        )
        dedicated_releases.sudo().write({'state': 'issued'})
        self.sudo().write({
            'state': 'in_progress',
            'started_by_id': self.env.user.id,
            'started_at': self.started_at or now,
        })
        return self._result_envelope()

    def _preflight_finish(self):
        self.ensure_one()
        self._validate_member_snapshot()
        self._check_release_scope()
        if self._effective_state() != 'in_progress':
            raise UserError(_('دفعة الصنف ليست قيد التشغيل.'))
        for production, lines in self._stage_lines_by_production():
            stage_order = production._stage_order_record(self.stage_code)
            if not stage_order or stage_order.state != 'in_progress':
                raise UserError(_('أحد أوامر المرحلة ليس قيد التشغيل.'))
            self._assert_stage_order_is_exclusive(stage_order, lines)
            active_lines = stage_order._get_stage_line_ids_data(
                'active_production_line_ids_data'
            )
            completed_lines = stage_order._get_stage_line_ids_data(
                'completed_production_line_ids_data'
            )
            if lines - active_lines or lines & completed_lines:
                raise UserError(_('تتبع أصناف الدفعة داخل المرحلة غير متطابق.'))
        return True

    def _finish_batch(self):
        self.ensure_one()
        self._advisory_lock_token(self.token)
        self._lock_operation()
        if self._effective_state() == 'done':
            self._sync_state_from_release()
            return self._result_envelope()
        self._preflight_finish()
        for production, lines in self._stage_lines_by_production():
            stage_order = production._stage_order_record(self.stage_code)
            if self.stage_code == 'bases':
                stage_order.sudo().write({
                    field_name: 'done'
                    for _code, _label, field_name
                    in stage_order._get_internal_substage_fields()
                })
            stage_order.with_context(
                furniture_skip_line_consolidation=True,
            )._send_selected_lines_to_quality(lines)
            stage_order.with_context(
                furniture_skip_line_consolidation=True,
            )._approve_product_batch_lines(lines)

        now = fields.Datetime.now()
        linked_stages = self._linked_release_stages()
        dedicated_stages = linked_stages.filtered(
            lambda stage: stage.release_id.product_batch_id == self
        )
        dedicated_stages.sudo().write({'state': 'completed'})
        dedicated_releases = dedicated_stages.mapped('release_id')
        dedicated_releases.sudo().write({'state': 'completed'})
        self.sudo().write({
            'state': 'done',
            'finished_by_id': self.env.user.id,
            'finished_at': self.finished_at or now,
        })
        return self._result_envelope()

    @api.model
    def _open_bom(self, batch_token, stage_code):
        batch, group = self._resolve_batch_token(
            batch_token, stage_code, allow_virtual=True,
        )
        if batch:
            batch._validate_member_snapshot()
            line = batch.member_ids.mapped('production_line_id')[:1]
            state = batch._effective_state()
            token = batch.token
        else:
            line = group['lines'][:1]
            state = 'unrequested'
            token = group['batch_token']
        if not line or not line.bom_id:
            raise UserError(_('لا توجد BoM مسجلة لهذا الصنف.'))
        action = self.env[
            'furniture.mrp.stage.product.batch.bom.wizard'
        ]._action_for_production_line(line, stage_code)
        return {
            'ok': True,
            'batch_token': token,
            'state': state,
            'action': action,
        }


class FurnitureMrpStageProductBatchBomWizard(models.TransientModel):
    _name = 'furniture.mrp.stage.product.batch.bom.wizard'
    _description = 'نافذة خامات صنف مرحلة للمشرف'

    product_name = fields.Char(string='الصنف', required=True, readonly=True)
    model_name = fields.Char(string='الموديل', readonly=True)
    stage_name = fields.Char(string='المرحلة', required=True, readonly=True)
    has_materials = fields.Boolean(readonly=True)
    material_count = fields.Integer(string='عدد الخامات', readonly=True)
    line_ids = fields.One2many(
        'furniture.mrp.stage.product.batch.bom.wizard.line',
        'wizard_id',
        string='الخامات',
        readonly=True,
    )

    @api.model
    def _action_for_production_line(self, line, stage_code):
        """Build the shared, order-free stage BoM dialog for one product."""
        line.ensure_one()
        if not line.bom_id:
            raise UserError(_('لا توجد BoM مسجلة لهذا الصنف.'))
        # The ordinary production-line popup deliberately contains the order
        # identity.  This compact supervisor dialog exposes only the harmless
        # product, model, stage, and material facts, so it can be reused by
        # both the body batches and the tailoring/upholstery order cards.
        material_lines = line.production_id.material_line_ids.filtered(
            lambda material: (
                material.production_line_id == line
                and material.stage == stage_code
                and material.product_id
            )
        ).sorted(lambda material: (
            material.product_id.display_name or '', material.id,
        ))
        furniture_model = (
            line.furniture_order_model_id
            or line.bom_id.furniture_model_id
            or line.product_id.furniture_model_id
        )
        wizard = self.create({
            'product_name': line.product_id.display_name,
            'model_name': (
                furniture_model.display_name
                if furniture_model
                else _('موديل غير محدد')
            ),
            'stage_name': dict(FURNITURE_STAGE_SELECTION).get(
                stage_code, stage_code,
            ),
            'has_materials': bool(material_lines),
            'material_count': len(material_lines),
            'line_ids': [(0, 0, {
                'sequence': sequence,
                'material_name': material.product_id.display_name,
                'required_qty': material.qty_needed or 0.0,
                'uom_name': material.product_uom_id.display_name or '',
            }) for sequence, material in enumerate(material_lines, start=1)],
        })
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_stage_product_batch_bom_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('تفاصيل خامات المرحلة'),
            'res_model': wizard._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
        }


class FurnitureMrpStageProductBatchBomWizardLine(models.TransientModel):
    _name = 'furniture.mrp.stage.product.batch.bom.wizard.line'
    _description = 'سطر خامة في نافذة صنف المرحلة'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.stage.product.batch.bom.wizard',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10, readonly=True)
    material_name = fields.Char(string='الخامة', required=True, readonly=True)
    required_qty = fields.Float(
        string='الكمية المطلوبة', digits=(16, 3), readonly=True,
    )
    uom_name = fields.Char(string='الوحدة', readonly=True)


class FurnitureMrpStageProductBatchMember(models.Model):
    _name = 'furniture.mrp.stage.product.batch.member'
    _description = 'نسخة سطر داخل دفعة صنف تشغيلية'
    _order = 'production_id, production_line_id, id'

    batch_id = fields.Many2one(
        'furniture.mrp.stage.product.batch', required=True, readonly=True,
        copy=False, ondelete='cascade', index=True,
    )
    production_line_id = fields.Many2one(
        'furniture.mrp.production.line', required=True, readonly=True,
        copy=False, ondelete='restrict', index=True,
    )
    production_id = fields.Many2one(
        'furniture.mrp.production', related='production_line_id.production_id',
        store=True, readonly=True, index=True,
    )
    qty_snapshot = fields.Float(
        required=True, readonly=True, copy=False, digits=(16, 3),
    )
    identity_key = fields.Char(
        required=True, readonly=True, copy=False, index=True,
    )

    _sql_constraints = [
        (
            'stage_product_batch_member_unique',
            'unique(batch_id, production_line_id)',
            'لا يمكن تكرار نفس سطر الإنتاج داخل دفعة الصنف.',
        ),
    ]


class FurnitureMrpStageMixinProductBatch(models.AbstractModel):
    _inherit = 'furniture.mrp.stage.mixin'

    def _approve_product_batch_lines(self, production_lines):
        """Approve only one product batch without clearing unrelated JSON.

        The generic quality action is intentionally order-oriented and clears
        the entire quality set.  Product batches are line snapshots, so this
        helper moves/costs/removes only the supplied member lines.
        """
        self.ensure_one()
        production = self.production_order_id
        if not production:
            raise UserError(_('أمر المرحلة غير مرتبط بأمر إنتاج.'))
        stage_code = production._stage_model_to_code(self._name)
        production_lines = production_lines.exists()
        quality_lines = self._get_stage_line_ids_data(
            'quality_production_line_ids_data'
        )
        if not production_lines or production_lines - quality_lines:
            raise UserError(_('أصناف الدفعة ليست كلها بانتظار اعتماد الجودة.'))

        self._refresh_labor_hours_from_logs()
        production._move_stage_work_to_stock(
            self._name, production_lines=production_lines,
        )
        production._record_stage_costs(
            self,
            stage_code,
            production_lines=production_lines,
            carryover_payloads=[],
        )
        completed_lines = self._get_stage_line_ids_data(
            'completed_production_line_ids_data'
        ) | production_lines
        active_lines = self._get_stage_line_ids_data(
            'active_production_line_ids_data'
        ) - production_lines
        remaining_quality_lines = quality_lines - production_lines
        self.with_context(
            furniture_skip_line_consolidation=True,
        ).write({
            'completed_production_line_ids_data': json.dumps(
                completed_lines.ids
            ),
            'active_production_line_ids_data': json.dumps(active_lines.ids),
            'quality_production_line_ids_data': json.dumps(
                remaining_quality_lines.ids
            ),
        })
        production._auto_transfer_completed_stage_lines(
            stage_code, production_lines,
        )
        # Concurrent operational kits can share this source stage. Finishing
        # one must not close the workers' logs of its still-running siblings.
        if not active_lines and not remaining_quality_lines:
            self._close_worker_time_logs()
            self._refresh_labor_hours_from_logs()

        remaining_route_lines = production._get_stage_incomplete_line_candidates(
            stage_code
        )
        remaining_work_payloads = self._get_remaining_stage_work_payloads()
        if active_lines or remaining_quality_lines:
            # This is prevented by the batch exclusivity guard, but retaining a
            # truthful state here makes recovery from old data deterministic.
            next_state = 'in_progress'
        elif remaining_route_lines or remaining_work_payloads:
            next_state = 'pending'
        else:
            next_state = 'done'
        self.sudo().write({
            'state': next_state,
            'quality_check': 'pass' if next_state == 'done' else 'pending',
            'date_finish': (
                fields.Datetime.now() if next_state == 'done' else False
            ),
        })
        self.sudo().message_post(body=_(
            '✅ اكتملت دفعة الصنف المحددة في مرحلة %s.'
        ) % dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code))
        return True


class FurnitureMrpAdvanceMaterialReleaseProductBatch(models.Model):
    _inherit = 'furniture.mrp.advance.material.release'

    state = fields.Selection(
        selection_add=[('completed', 'اكتمل التشغيل')],
        ondelete={'completed': 'set default'},
    )
    product_batch_id = fields.Many2one(
        'furniture.mrp.stage.product.batch', readonly=True, copy=False,
        ondelete='restrict', index=True,
    )

    @api.model
    def _scoped_stage_material_buckets(
        self, production, stage_code, production_lines,
    ):
        production_lines = production_lines.exists()
        buckets = {}
        material_lines = production.material_line_ids.filtered(
            lambda line: (
                line.production_line_id in production_lines
                and line.stage == stage_code
                and line.product_id
                and float_compare(
                    line.qty_needed or 0.0, 0.0, precision_digits=3,
                ) > 0
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
    def _scoped_stage_snapshot_payload(
        self, production, stage_code, production_lines,
        availability_pool=None, sequence=10,
    ):
        production_lines = production_lines.exists().sorted('id')
        destination = production._stage_work_location(stage_code)
        if not destination:
            raise UserError(_(
                'لا يوجد موقع صالة محدد لمرحلة %s.'
            ) % dict(FURNITURE_STAGE_SELECTION).get(stage_code, stage_code))
        source_location = (
            production.location_src_id
            or self.env.ref(
                'stock.stock_location_stock', raise_if_not_found=False,
            )
        )
        available_by_product = (
            availability_pool if availability_pool is not None else {}
        )
        details = []
        stage_material_lines = self.env['furniture.mrp.material.line']
        buckets = self._scoped_stage_material_buckets(
            production, stage_code, production_lines,
        )
        for key, bucket in sorted(
            buckets.items(),
            key=lambda item: (
                item[1]['product'].display_name or '', item[0],
            ),
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
        return {
            'sequence': sequence,
            'production_id': production.id,
            'stage_code': stage_code,
            'destination_location_id': destination.id,
            'handover_location_id': self.env.ref(
                'furniture_mrp.location_material_handover'
            ).id,
            'source_production_line_ids': [(6, 0, production_lines.ids)],
            'source_material_line_ids': [(6, 0, stage_material_lines.ids)],
            'material_line_ids': [(0, 0, detail) for detail in details],
        }

    @api.model
    def _create_from_product_batch(self, batch):
        batch = batch.exists()
        if not batch or len(batch) != 1:
            raise UserError(_('دفعة الصنف المطلوبة غير موجودة.'))
        self._check_can_request()
        batch._validate_member_snapshot()
        productions = batch.member_ids.mapped(
            'production_id'
        ).exists().sorted('id')
        if not productions:
            raise UserError(_('دفعة الصنف لا تحتوي على أوامر صالحة.'))
        if any(production.company_id != batch.company_id for production in productions):
            raise AccessError(_('لا يمكن جمع شركات مختلفة في دفعة صنف واحدة.'))

        helper = self.env['furniture.mrp.store.request']
        storekeeper = helper._get_storekeeper_user(batch.company_id)
        availability_pool = {}
        payloads = []
        for sequence, production in enumerate(productions, start=1):
            lines = batch.member_ids.filtered(
                lambda member, current=production: (
                    member.production_id == current
                )
            ).mapped('production_line_id')
            payloads.append(self._scoped_stage_snapshot_payload(
                production,
                batch.stage_code,
                lines,
                availability_pool=availability_pool,
                sequence=sequence * 10,
            ))
        release = self.sudo().create({
            'production_id': False,
            'company_id': batch.company_id.id,
            'requested_by_id': self.env.user.id,
            'assigned_to_id': storekeeper.id,
            'product_batch_id': batch.id,
            'stage_line_ids': [(0, 0, payload) for payload in payloads],
        })
        release.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=storekeeper.id,
            summary=_('مراجعة خامات دفعة صنف'),
            note=_(
                'راجع خامات دفعة %(product)s في مرحلة %(stage)s '
                'بإجمالي %(quantity)s %(uom)s.'
            ) % {
                'product': batch.product_id.display_name,
                'stage': dict(FURNITURE_STAGE_SELECTION).get(
                    batch.stage_code, batch.stage_code,
                ),
                'quantity': batch.planned_qty,
                'uom': batch.uom_id.display_name,
            },
        )
        helper._send_bus_notification(
            storekeeper,
            _('طلب خامات دفعة صنف'),
            _('دفعة %s بانتظار مراجعة المخزن.')
            % batch.product_id.display_name,
            notification_type='warning',
            action_model=release._name,
            action_res_id=release.id,
            action_name=_('فتح الإذن'),
            play_sound=True,
        )
        productions.invalidate_recordset([
            'advance_material_release_stage_ids',
            'advance_material_release_ids',
            'advance_material_release_count',
            'has_uncovered_advance_material_stages',
        ])
        return release

    def _sync_linked_product_batches(self):
        # Warehouse users deliberately have no direct ACL on the internal
        # product-batch snapshot.  An issue/reject/return still has to refresh
        # the linked dashboard state, so cross that boundary narrowly through
        # the already validated release relation instead of granting the
        # storekeeper read access to hidden production membership.
        releases = self.sudo().exists()
        batches = releases.mapped('product_batch_id').exists()
        if releases:
            batches |= self.env[
                'furniture.mrp.stage.product.batch'
            ].sudo().search([
                ('advance_release_stage_ids', 'in', releases.stage_line_ids.ids),
            ])
        if batches:
            batches._sync_state_from_release()
        return True

    def action_issue(self):
        result = super().action_issue()
        self._sync_linked_product_batches()
        return result

    def action_reject(self):
        result = super().action_reject()
        self._sync_linked_product_batches()
        return result

    def action_cancel(self):
        result = super().action_cancel()
        self._sync_linked_product_batches()
        return result

    def action_return(self):
        result = super().action_return()
        self._sync_linked_product_batches()
        return result


class FurnitureMrpAdvanceMaterialReleaseStageProductBatch(models.Model):
    _inherit = 'furniture.mrp.advance.material.release.stage'

    state = fields.Selection(
        selection_add=[('completed', 'اكتمل التشغيل')],
        ondelete={'completed': 'set default'},
    )
    source_production_line_ids = fields.Many2many(
        'furniture.mrp.production.line',
        'furn_adv_rel_stage_prod_line_rel',
        'release_stage_id', 'production_line_id',
        string='سطور الإنتاج المطلوبة', readonly=True, copy=False,
    )

    def _current_buckets(self):
        self.ensure_one()
        if not self.source_production_line_ids:
            return super()._current_buckets()
        return self.env[
            'furniture.mrp.advance.material.release'
        ]._scoped_stage_material_buckets(
            self.production_id,
            self.stage_code,
            self.source_production_line_ids,
        )

    def _product_batch_allocations(self, production_lines):
        """Return the issued material share belonging to exact product rows.

        One advance-release detail may aggregate the same raw material for a
        sofa and a chaise inside one manufacturing order.  The warehouse issue
        remains aggregate, while this allocation is deterministic per original
        recipe row so hall receipt can stay product-specific.
        """
        self.ensure_one()
        production_lines = production_lines.exists().filtered(
            lambda line: line.production_id == self.production_id
        )
        allocations = []
        for detail in self.material_line_ids.sorted('id'):
            source_lines = detail.source_material_line_ids.exists().sorted('id')
            if not source_lines:
                continue
            required_by_line = {
                line.id: self.production_id._quantity_in_product_uom(
                    detail.product_id,
                    line.qty_needed,
                    line.product_uom_id,
                )
                for line in source_lines
            }
            total_required = sum(required_by_line.values())
            remaining = max(detail.issued_qty or 0.0, 0.0)
            issued_by_line = {}
            for index, line in enumerate(source_lines):
                required_qty = required_by_line[line.id]
                if index == len(source_lines) - 1:
                    issued_qty = max(remaining, 0.0)
                elif float_compare(
                    total_required, 0.0, precision_digits=3,
                ) > 0:
                    issued_qty = min(
                        (detail.issued_qty or 0.0)
                        * required_qty / total_required,
                        max(remaining, 0.0),
                    )
                else:
                    issued_qty = 0.0
                issued_by_line[line.id] = issued_qty
                remaining -= issued_qty
            scoped_lines = source_lines.filtered(
                lambda line: line.production_line_id in production_lines
            )
            if scoped_lines:
                allocations.append({
                    'detail': detail,
                    'source_lines': scoped_lines,
                    'issued_by_line': {
                        line.id: issued_by_line.get(line.id, 0.0)
                        for line in scoped_lines
                    },
                })
        return allocations

    def _source_line_has_product_receipt(
        self, detail, source_line, expected_product_qty,
    ):
        self.ensure_one()
        if (
            not source_line.warehouse_receipt_confirmed
            or source_line.warehouse_receipt_stage_id != self
        ):
            return False
        received_product_qty = self.production_id._quantity_in_product_uom(
            detail.product_id,
            source_line.warehouse_received_qty or 0.0,
            source_line.product_uom_id,
        )
        return float_compare(
            received_product_qty,
            expected_product_qty,
            precision_digits=3,
        ) >= 0

    def _product_batch_materials_received(self, production_lines):
        self.ensure_one()
        if self.state not in ('issued', 'started', 'completed'):
            return False
        allocations = self._product_batch_allocations(production_lines)
        return all(
            self._source_line_has_product_receipt(
                allocation['detail'], source_line,
                allocation['issued_by_line'][source_line.id],
            )
            for allocation in allocations
            for source_line in allocation['source_lines']
        )

    def _sync_product_batch_receipt_summary(self):
        """Keep the legacy stage totals truthful after partial product receipt."""
        self.ensure_one()
        any_received = False
        all_received = True
        has_shortage = False
        for detail in self.material_line_ids:
            allocations = self._product_batch_allocations(
                detail.source_material_line_ids.mapped(
                    'production_line_id'
                ).exists()
            )
            allocation = next((
                item for item in allocations if item['detail'] == detail
            ), False)
            source_lines = detail.source_material_line_ids.exists().sorted('id')
            issued_by_line = (
                allocation['issued_by_line'] if allocation else {}
            )
            received_qty = 0.0
            for source_line in source_lines:
                expected_qty = issued_by_line.get(source_line.id, 0.0)
                is_received = self._source_line_has_product_receipt(
                    detail, source_line, expected_qty,
                )
                any_received = any_received or is_received
                all_received = all_received and is_received
                if is_received:
                    received_qty += self.production_id._quantity_in_product_uom(
                        detail.product_id,
                        source_line.warehouse_received_qty or 0.0,
                        source_line.product_uom_id,
                    )
            received_qty = min(received_qty, detail.issued_qty or 0.0)
            detail.sudo().write({
                'received_qty': received_qty,
                'receipt_difference_qty': max(
                    (detail.issued_qty or 0.0) - received_qty, 0.0,
                ),
            })
            has_shortage = has_shortage or float_compare(
                detail.issued_qty or 0.0,
                detail.requested_qty or 0.0,
                precision_digits=3,
            ) < 0

        if not self.material_line_ids:
            all_received = True
            any_received = True
        receipt_confirmed = bool(all_received)
        if receipt_confirmed:
            receipt_state = 'partial' if has_shortage else 'full'
        elif any_received:
            receipt_state = 'partial'
        else:
            receipt_state = 'waiting'
        values = {
            'receipt_state': receipt_state,
            'receipt_confirmed': receipt_confirmed,
        }
        if any_received:
            values.update({
                'received_by_id': self.env.user.id,
                'received_at': fields.Datetime.now(),
            })
        self.sudo().write(values)
        return receipt_confirmed

    def _receive_product_batch_materials(self, production_lines):
        """Move only one displayed product's raw materials into the hall."""
        self.ensure_one()
        self._check_can_receive()
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
        if self.state not in ('issued', 'started'):
            raise UserError(_(
                'لا يمكن استلام خامات هذا الصنف قبل صرف المخزن أو بعد إرجاع الإذن.'
            ))
        self._validate_current_snapshot(relink_sources=True)
        production_lines = production_lines.exists().filtered(
            lambda line: line.production_id == self.production_id
        )
        if not production_lines:
            raise UserError(_('لا توجد سطور صنف صالحة لهذا الاستلام.'))

        allocations = self._product_batch_allocations(production_lines)
        handover_location = self._material_handover_location()
        specs = []
        pending_by_detail = {}
        for allocation in allocations:
            detail = allocation['detail']
            pending_lines = allocation['source_lines'].filtered(
                lambda source_line, current=allocation: (
                    not self._source_line_has_product_receipt(
                        current['detail'], source_line,
                        current['issued_by_line'][source_line.id],
                    )
                )
            )
            missing_qty = sum(
                allocation['issued_by_line'][line.id]
                for line in pending_lines
            )
            pending_by_detail[detail.id] = pending_lines
            if float_compare(
                missing_qty, 0.0, precision_digits=3,
            ) <= 0:
                continue
            specs.append({
                'source_location': handover_location,
                'dest_location': self.destination_location_id,
                'label': _('استلام خامات صنف مستقل في صالة %s') % dict(
                    FURNITURE_STAGE_SELECTION,
                ).get(self.stage_code, self.stage_code),
                'product': detail.product_id,
                'quantity': missing_qty,
                'uom': detail.product_id.uom_id,
                'source_production_lines': production_lines,
                'detail': detail,
            })

        created_by_detail = {}
        for spec, move in self.production_id._create_internal_moves_batch(specs):
            created_by_detail.setdefault(
                spec['detail'].id, self.env['stock.move']
            )
            created_by_detail[spec['detail'].id] |= move

        for allocation in allocations:
            detail = allocation['detail']
            new_moves = created_by_detail.get(
                detail.id, self.env['stock.move']
            )
            if new_moves:
                detail.sudo().write({
                    'receipt_move_ids': [(6, 0, (
                        detail.receipt_move_ids | new_moves
                    ).ids)],
                })
            primary_move = new_moves.sorted(
                lambda move: -detail._move_qty_in_product_uom(move)
            )[:1]
            for source_line in pending_by_detail.get(
                detail.id, self.env['furniture.mrp.material.line']
            ):
                received_product_qty = allocation[
                    'issued_by_line'
                ][source_line.id]
                received_line_qty = detail.product_id.uom_id._compute_quantity(
                    received_product_qty,
                    source_line.product_uom_id or detail.product_id.uom_id,
                    round=False,
                )
                values = {
                    'warehouse_receipt_confirmed': True,
                    'warehouse_received_qty': received_line_qty,
                    'warehouse_receipt_stage_id': self.id,
                }
                if not self.production_id._material_line_already_consumed(
                    source_line
                ):
                    values['move_id'] = (
                        primary_move.id if primary_move else detail.move_id.id
                    )
                source_line.sudo().write(values)

        self._sync_product_batch_receipt_summary()
        product_names = production_lines.mapped('product_id.display_name')
        self.production_id.sudo().message_post(body=_(
            '📥 تم استلام خامات الصنف %(products)s فقط في صالة %(stage)s.'
        ) % {
            'products': '، '.join(product_names),
            'stage': dict(FURNITURE_STAGE_SELECTION).get(
                self.stage_code, self.stage_code,
            ),
        })
        return True

    def _prepare_product_batch_for_stage_execution(
        self, production_lines, mark_started=True,
    ):
        """Validate one product share without requiring the whole order receipt."""
        self.ensure_one()
        self.release_id._lock()
        self.invalidate_recordset(['state'])
        if self.state not in ('issued', 'started'):
            raise UserError(_(
                'إذن صرف خامات المرحلة لم يعد صالحًا لبدء هذا الصنف.'
            ))
        self._validate_current_snapshot(relink_sources=True)
        if not self._product_batch_materials_received(production_lines):
            raise UserError(_(
                'استلم خامات هذا الصنف وحده من المخزن قبل بدء تشغيله.'
            ))
        if mark_started and self.state == 'issued':
            self.sudo().write({'state': 'started'})
        return True

    def _confirm_production_receipt(
        self, received_by_line, note=False, receiver_user=False,
    ):
        result = super()._confirm_production_receipt(
            received_by_line,
            note=note,
            receiver_user=receiver_user,
        )
        batches = self.release_id.product_batch_id.exists()
        batches |= self.env[
            'furniture.mrp.stage.product.batch'
        ].sudo().search([
            ('advance_release_stage_ids', 'in', self.ids),
        ])
        if batches:
            batches._sync_state_from_release()
        return result


class FurnitureMrpProductionProductBatchDashboard(models.Model):
    _inherit = 'furniture.mrp.production'

    def _allows_received_parallel_entry(self, line, stage_code):
        """Opt-in hook; ordinary routes still require their actual entry stage."""
        return False

    allow_parallel_product_batches = fields.Boolean(
        string='السماح بتشغيل دفعات أصناف متوازية',
        default=False,
        copy=False,
        help=(
            'يسمح ببدء أكثر من دفعة صنف داخل نفس أمر المرحلة. '
            'يُستخدم مؤقتًا لأوامر الاختبار المحددة.'
        ),
    )

    @api.model
    def get_stage_dashboard_data(
        self, stage_code=False, date_from=False, date_to=False,
    ):
        result = super().get_stage_dashboard_data(
            stage_code=stage_code,
            date_from=date_from,
            date_to=date_to,
        )
        if result.get('batch_supervisor_mode'):
            selected_stage = result.get('selected_stage')
            # Never serialize an order/contributor/customer object to the
            # restricted surface.  The persistent member snapshot remains
            # server-side and is addressed only by its opaque token.
            result['orders'] = []
            result['product_batches'] = self.env[
                'furniture.mrp.stage.product.batch'
            ]._dashboard_product_batches(selected_stage)
        else:
            result['product_batches'] = []
        return result

    @api.model
    def _stage_dashboard_product_batch_model(self, stage_code):
        self._stage_dashboard_check_stage_access(stage_code, batch_only=True)
        return self.env['furniture.mrp.stage.product.batch']

    @api.model
    def action_stage_dashboard_request_product_batch_materials(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        return Batch._request_materials(batch_token, stage_code)

    @api.model
    def action_stage_dashboard_request_product_batches_materials(
        self, batch_tokens=None, stage_code=False,
    ):
        """Request several exact product batches in one atomic RPC transaction."""
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        if not isinstance(batch_tokens, (list, tuple)):
            raise ValidationError(_('قائمة دفعات الأصناف المطلوبة غير صالحة.'))
        normalized_tokens = sorted({
            str(token or '').strip() for token in batch_tokens
            if str(token or '').strip()
        })
        if not normalized_tokens:
            raise ValidationError(_('حدد صنفًا واحدًا على الأقل لطلب خاماته.'))
        if len(normalized_tokens) > 100:
            raise ValidationError(_('يمكن طلب خامات 100 صنف بحد أقصى في المرة الواحدة.'))

        # Validate every opaque token against the current stage/company/visible
        # scope before creating the first material request. Any later failure
        # also rolls the whole RPC transaction back, so partial bulk requests
        # can never be committed.
        for token in normalized_tokens:
            persisted, candidate = Batch._resolve_batch_token(
                token, stage_code, allow_virtual=True,
            )
            if persisted:
                persisted._sync_state_from_release()
                if persisted._effective_state() != 'unrequested':
                    raise ValidationError(_(
                        'أحد الأصناف المحددة لم يعد متاحًا لطلب الخامات. حدّث اللوحة وحاول مجددًا.'
                    ))
            elif not candidate:
                raise ValidationError(_('إحدى دفعات الأصناف المحددة لم تعد متاحة.'))

        results = [
            Batch._request_materials(token, stage_code)
            for token in normalized_tokens
        ]
        return {
            'requested': True,
            'requested_count': len(results),
            'batch_tokens': normalized_tokens,
        }

    @api.model
    def action_stage_dashboard_receive_product_batch_materials(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=True,
        )
        if not batch:
            batch = Batch._materialize_candidate(batch_token, stage_code)
        return batch._receive_materials()

    @api.model
    def action_stage_dashboard_start_product_batch(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=True,
        )
        if not batch:
            batch = Batch._materialize_candidate(batch_token, stage_code)
        result = batch._start_batch()
        if batch._effective_state() == 'in_progress':
            warning = self.env[
                'furniture.mrp.product.production.warning'
            ]._consume_next_for_lines(
                stage_code,
                batch.production_line_ids,
                batch_token=batch.token,
            )
            if warning:
                result['production_warning'] = warning
        return result

    @api.model
    def action_stage_dashboard_finish_product_batch(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        batch, _group = Batch._resolve_batch_token(
            batch_token, stage_code, allow_virtual=False,
        )
        return batch._finish_batch()

    @api.model
    def action_open_stage_dashboard_product_batch_bom(
        self, batch_token=False, stage_code=False,
    ):
        Batch = self._stage_dashboard_product_batch_model(stage_code)
        return Batch._open_bom(batch_token, stage_code)


class FurnitureMrpProductionLineProductBatchGuard(models.Model):
    _inherit = 'furniture.mrp.production.line'

    @api.model
    def _product_batch_protected_fields(self):
        return {
            'active',
            'consolidated_into_line_id',
            'production_id',
            'product_id',
            'furniture_order_model_id',
            'bom_id',
            'product_qty',
            'product_uom_id',
            'width_cm',
            'depth_cm',
            'height_cm',
            'bom_width_cm',
            'bom_depth_cm',
            'bom_height_cm',
            *(field_names[0] for field_names in FURNITURE_STAGE_FIELD_MAP.values()),
        }

    def _lock_and_check_no_active_product_batch(self):
        lines = self.exists().sorted('id')
        if not lines:
            return True
        self.env.cr.execute(
            'SELECT id FROM furniture_mrp_production_line '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(lines.ids)],
        )
        member = self.env[
            'furniture.mrp.stage.product.batch.member'
        ].sudo().search([
            ('production_line_id', 'in', lines.ids),
            ('batch_id.state', 'in', PRODUCT_BATCH_ACTIVE_STATES),
        ], limit=1)
        if member:
            raise UserError(_(
                'لا يمكن تعديل أو دمج أو حذف صنف داخل دفعة مرحلة نشطة. '
                'أكمل أو ألغِ الدفعة أولًا.'
            ))
        return True

    def write(self, vals):
        if set(vals) & self._product_batch_protected_fields():
            self._lock_and_check_no_active_product_batch()
        return super().write(vals)

    def unlink(self):
        self._lock_and_check_no_active_product_batch()
        return super().unlink()
