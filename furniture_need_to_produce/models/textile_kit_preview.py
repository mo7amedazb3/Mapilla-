"""Read-only allocation plan used by persistent, independently executed kits."""
import json

from odoo import api, models, _
from odoo.exceptions import ValidationError

from .kit_card_allocation import KitSource, KitRecipe, allocate_kit_cards, quantity


class FurnitureTextileKitPreview(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _textile_kit_preview(self, stage_code, date_from=False, date_to=False, raw_payload=None):
        """Plan complete kits from authorized, unstarted dashboard lines only.

        This intentionally returns internal source references.  It is private
        (not callable over RPC), creates nothing and performs no stock writes.
        Starting the entire original order with this preview is NOT safe.
        """
        if stage_code not in ('tailoring', 'upholstery'):
            raise ValidationError(_('تجميع الأطقم خاص بالتفصيل والكسوة.'))
        self._stage_dashboard_check_stage_access(stage_code)
        payload = raw_payload if raw_payload is not None else self.with_context(
            textile_kit_raw_dashboard=True).get_stage_dashboard_data(
                stage_code=stage_code, date_from=date_from, date_to=date_to)
        productions = self.search([('id', 'in', [row['id'] for row in payload['orders']])])
        Line = self.env['furniture.mrp.production.line']
        reserved = self.env['furniture.textile.kit.member'].sudo().search([
            ('stage_code', '=', stage_code), ('production_id', 'in', productions.ids),
        ]).mapped('production_line_id')
        lines = Line
        ready_ids = set()
        for production in productions:
            stage = production._stage_dashboard_stage_order(stage_code)
            buckets = self._stage_dashboard_line_buckets(production, stage_code, stage)
            candidates = buckets['not_started_lines'].filtered('active') - reserved
            lines |= candidates
            if stage and stage.state in ('pending', 'in_progress') and stage.store_request_state == 'approved':
                ready_ids.update(production._get_stage_pending_start_line_candidates(stage, stage_code).ids)

        # A customized shortage piece must never be hidden inside a standard
        # kit.  Restrict this elevated metadata lookup to accessible orders.
        custom_orders = set(self.env['furniture.need.to.produce.stage'].sudo().search([
            ('production_id', 'in', productions.ids),
            ('piece_id.company_id', 'in', self.env.companies.ids),
        ]).filtered(lambda row: row.piece_id.is_custom).mapped('production_id').ids)
        recipe_cache = {}
        sources = []
        line_by_id = {line.id: line for line in lines}
        rates = {}
        for line in lines:
            production = line.production_id
            company = production.company_id
            model = line.furniture_order_model_id or production.furniture_order_model_id or line.product_id.furniture_model_id
            buyer = line.buyer_partner_id or production.buyer_partner_id
            beneficiary = line.beneficiary_partner_id or production.beneficiary_partner_id
            scope = (company.id, stage_code, model.id or 0, buyer.id or 0, beneficiary.id or 0)
            product_key = (line.product_id.furniture_dimension_source_product_id or line.product_id).id
            # Saved material quantities are per whole source batch.  Compare
            # per-piece amounts so batches of 1 and 6 can belong to one pool.
            textile = self._stage_dashboard_tailoring_line_payload(line)
            for kind in ('fabric_items', 'takawe_items'):
                textile[kind] = sorted(({
                    **item, 'qty': round(item['qty'] / line.product_qty, 9),
                } for item in textile[kind]), key=lambda item: (
                    item['product_id'], item['uom_id'] or 0, item.get('piece_size') or '', item['qty']))
            signature = json.dumps([
                line.bom_id.id, line.dimension_label or '', line.stage_summary or '',
                self._stage_dashboard_tailoring_line_signature(textile),
                line.kit_piece_note or '', line.batch_image_token or '',
            ], ensure_ascii=False, sort_keys=True, default=str)
            fixed = (line.kit_bom_id.id, production.id, line.kit_instance_number) if line.kit_bom_id else ()
            sources.append(KitSource(
                line.id, product_key, quantity(line.product_qty), scope, signature,
                eligible=production.id not in custom_orders, fixed_kit=fixed,
            ))
            key = (company.id, model.id)
            if model and key not in recipe_cache:
                recipe_reader = self.env['furniture.mrp.stage.transfer.wizard'].sudo().new({
                    'production_id': production.id,
                })
                # Supervisors cannot browse raw BOMs.  Elevate only this
                # read-only lookup of the exact authorized company/model;
                # neither recipe costs nor materials are returned by it.
                recipes = self.env['mrp.bom'].sudo().search([
                    ('active', '=', True), ('type', '=', 'phantom'),
                    ('furniture_model_id', '=', model.id),
                    ('company_id', 'in', [False, company.id]),
                    ('bom_line_ids', '!=', False),
                ])
                recipe_cache[key] = [KitRecipe(
                    bom.id, company.id, model.id,
                    tuple((component_key, quantity(component['qty']))
                          for component_key, component in recipe_reader._kit_component_requirements(bom).items()),
                ) for bom in recipes]
            if company.id not in rates:
                rates[company.id] = self.env['furniture.mrp.stage.time.standard']._stage_time_hours_per_piece(company, stage_code)

        cards, remainder = allocate_kit_cards(sources, [item for recipes in recipe_cache.values() for item in recipes])
        source_by_id = {source.line_id: source for source in sources}
        output = []
        for card in cards:
            products = {}
            planned_hours = 0.0
            for line_id, amount in card.members:
                line = line_by_id[line_id]
                source = source_by_id[line_id]
                product = products.setdefault(source.product_key, {
                    'product_id': source.product_key,
                    'product_name': line.product_id.display_name,
                    'quantity': 0.0,
                    'source_allocations': [],
                })
                product['quantity'] += float(amount)
                product['source_allocations'].append({
                    'production_id': line.production_id.id,
                    'production_line_id': line.id,
                    'quantity': float(amount),
                })
                fixed_hours = line.production_id._furniture_temporary_stage_hours()
                # Preserve a temporary order-wide estimate proportionally,
                # never charge its whole timer independently to every kit.
                order_qty = sum(line.production_id.production_line_ids.filtered(
                    lambda item: item.active and stage_code in item._selected_stage_codes()).mapped('product_qty'))
                rate = fixed_hours / order_qty if fixed_hours and order_qty else rates[card.scope[0]]
                planned_hours += float(amount) * rate
            output.append({
                'token': card.token,
                'recipe_id': card.recipe_id,
                'company_id': card.scope[0],
                'stage_code': stage_code,
                'model_id': card.scope[2],
                'buyer_id': card.scope[3],
                'beneficiary_id': card.scope[4],
                'products': list(products.values()),
                'planned_hours': planned_hours,
                'materials_and_arrival_ready': all(line_id in ready_ids for line_id, amount in card.members),
                # A preview alone is deliberately never executable.
                'can_start': False,
            })
        return {
            'cards': output,
            'remaining': {line_id: float(amount) for line_id, amount in remainder.items()},
            'source_quantities': {source.line_id: float(source.qty) for source in sources},
            'original_order_count': len(productions),
        }
