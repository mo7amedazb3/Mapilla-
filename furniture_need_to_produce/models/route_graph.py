"""Read-only presentation of the saved piece route, never a new allocation."""

import hashlib
import json

from odoo import api, fields, models
from odoo.tools.float_utils import float_compare


HISTORY_OPERATIONS = {
    'frame': ('priming', 'carpentry'), 'bases': ('bases',),
    'finish': ('bases', 'finishing'), 'tailoring': ('tailoring',),
    'painting': ('painting',), 'upholstery': ('upholstery',),
}


def piece_display_key(piece, graph):
    """Match saved work, not just labels. Never merge allocations or write data."""
    if piece.state != 'draft' or piece.is_custom:
        return False
    node_ids = {node['id']: 'operation:%s' % index if node['kind'] == 'stage' else node['id']
                for index, node in enumerate(graph['nodes'])}
    nodes = [{**{k: v for k, v in node.items() if k not in ('id', 'stage_id')},
              'id': node_ids[node['id']]} for node in graph['nodes']]
    edges = sorted((node_ids[e['from']], node_ids[e['to']]) for e in graph['edges'])
    materials = [(s.plan_key, s.materials_initialized,
                  sorted((m.stage_code, m.product_id.id, m.uom_id.id, m.quantity, m.customized)
                         for m in s.material_ids))
                 for s in piece.stage_ids.sorted(key=lambda s: (s.sequence, s.id))]
    payload = [piece.company_id.id, piece.furniture_model_id.id, piece.product_id.id,
               piece.bom_id.id, piece.final_rule_id.id, piece.buffer_rule_id.id,
               piece.state, piece.target_lane, piece.preview_json, materials,
               nodes, edges, graph['hours'], graph['days'], graph['unknown_wait']]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def append_handoff_history(graph, roots, stage_labels, lane_labels):
    """Expand exact linked input transfers, without allocating or changing time.

    roots maps a production line to its already-rendered operation IDs. Historical
    quantities describe the source order's batch, not an invented per-piece split.
    A reserved/cancelled transfer must never look like a completed withdrawal.
    """
    by_id = {node['id']: node for node in graph['nodes']}
    seen_edges = {(edge['from'], edge['to']) for edge in graph['edges']}
    visits = set()

    def connect(origin, target):
        if origin != target and (origin, target) not in seen_edges:
            seen_edges.add((origin, target))
            graph['edges'].append({'from': origin, 'to': target})

    def walk(line, target, path):
        visit = (line.id, target)
        if visit in visits:
            return
        if line.id in path or len(visits) >= 64:
            graph['history_incomplete'] = True
            return
        visits.add(visit)
        path = path | {line.id}
        for handoff in line.downstream_handoff_ids:
            if handoff.state not in ('reserved', 'consumed') or handoff.quantity <= 0:
                continue
            output, move = handoff.output_id, handoff.transfer_move_id
            source = output.production_line_id
            if not source or not move or move.state == 'cancel':
                continue
            if source.id in path or source.production_id.company_id != line.production_id.company_id:
                graph['history_incomplete'] = True
                continue
            moved = move.product_uom._compute_quantity(move.quantity, source.product_id.uom_id)
            withdrawn = handoff.state == 'consumed' and move.state == 'done' and float_compare(
                moved, handoff.quantity, precision_rounding=source.product_id.uom_id.rounding,
            ) >= 0
            if source.id in roots:
                # An approved piece already displays its own producers. Reuse
                # those nodes instead of duplicating the same operation.
                operation_ids = roots[source.id][1]
                source_quantity = source.product_uom_id._compute_quantity(
                    source.product_qty, source.product_id.uom_id,
                )
                if withdrawn and float_compare(handoff.quantity, source_quantity,
                                               precision_rounding=source.product_id.uom_id.rounding) >= 0:
                    for node_id in operation_ids:
                        by_id[node_id]['withdrawn'] = True
            else:
                selected = source._selected_stage_codes()
                codes = [code for code in HISTORY_OPERATIONS.get(handoff.role, ()) if code in selected]
                # A source batch can predate stage-selection metadata. Keep its
                # recorded output role, but never invent missing operations.
                codes = codes or ['']
                operation_ids = ['history:%s:%s' % (handoff.id, code or handoff.role) for code in codes]
                lane = 'preparation' if handoff.role == 'finish' else handoff.role
                for index, (node_id, code) in enumerate(zip(operation_ids, codes)):
                    if node_id not in by_id:
                        node = {
                            'id': node_id, 'kind': 'history', 'lane': lane, 'stage_code': code,
                            'label': stage_labels.get(code) or lane_labels.get(lane, handoff.role),
                            'stage_id': False, 'editable': False, 'customized': False,
                            'estimated_hours': 0.0, 'material_count': 0,
                            'quantity': handoff.quantity, 'withdrawn': withdrawn,
                            'production_id': source.production_id.id,
                            'production_name': source.production_id.name,
                            'handoff_id': handoff.id,
                        }
                        by_id[node_id] = node
                        graph['nodes'].append(node)
                    if index:
                        connect(operation_ids[index - 1], node_id)
            connect(operation_ids[-1], target)
            walk(source, operation_ids[0], path)

    for line, operation_ids in roots.values():
        walk(line, operation_ids[0], set())
    return graph


def build_route_graph(piece, stage_labels, lane_labels):
    """Project existing stages/claims into a connected operation-level graph.

    Records are only read here. In particular, do not call the planner or any
    material-loading method: opening a card must preserve its approved sources
    and the manager's piece-specific material snapshot.
    """
    nodes, edges = [], []
    stage_ends, sources, seen_edges = {}, {}, set()
    parallel_nodes = {}
    stages = sorted(piece.stage_ids, key=lambda stage: (stage.sequence, stage.id))

    def add_edge(origin, target):
        edge = (origin, target)
        if origin != target and edge not in seen_edges:
            seen_edges.add(edge)
            edges.append({'from': origin, 'to': target})

    for stage_index, stage in enumerate(stages):
        codes = stage.stage_codes or [stage.stage_code]
        stage_id = stage.id if isinstance(stage.id, int) else False
        token = stage_id or 'new-%s' % stage_index
        operation_ids = ['stage:%s:%s' % (token, index) for index in range(len(codes))]
        stage_ends[stage.id] = (operation_ids[0], operation_ids[-1])
        if stage.lane == 'finish':
            parallel_nodes[stage.id] = operation_ids
        production = stage.production_id
        customized = bool(stage.materials_customized or any(row.customized for row in stage.material_ids))
        for index, code in enumerate(codes):
            nodes.append({
                'id': operation_ids[index],
                'label': stage_labels.get(code, code),
                'kind': 'stage',
                'lane': stage.lane,
                'stage_code': code,
                'stage_id': stage_id,
                'estimated_hours': (stage.estimated_hours or 0.0) / len(codes),
                'material_count': sum(row.stage_code == code for row in stage.material_ids),
                'customized': customized,
                'production_id': production.id if production else False,
                'production_name': production.name or '' if production else '',
                'quantity': stage.quantity,
                'editable': piece.state == 'draft' and bool(stage_id),
            })
            if index and stage.lane != 'finish':
                add_edge(operation_ids[index - 1], operation_ids[index])

    unknown_wait = bool(piece.unknown_incoming_wait)
    for stage in stages:
        target = stage_ends[stage.id][0]
        targets = parallel_nodes.get(stage.id, [target])
        for input_index, source in enumerate(stage.input_ids):
            if source.kind == 'stage':
                upstream = stage_ends.get(source.source_stage_id.id if source.source_stage_id else False)
                if upstream:
                    for origin in parallel_nodes.get(source.source_stage_id.id, [upstream[1]]):
                        for destination in targets:
                            add_edge(origin, destination)
                # An absent predecessor is not permission to invent a route.
                continue
            if source.kind not in ('stock', 'incoming'):
                continue
            unknown_wait = unknown_wait or source.kind == 'incoming'
            origin = source.output_id if source.kind == 'stock' else source.source_line_id
            lane = 'preparation' if source.role == 'finish' else source.role
            # Share only the same exact stock output / production line and role.
            # Missing references remain distinct instead of merging unrelated
            # allocations merely because their display labels happen to match.
            origin_token = origin.id if origin else 'missing-%s-%s' % (target, input_index)
            node_id = '%s:%s:%s' % (source.kind, origin_token, source.role)
            if node_id not in sources:
                production = origin.production_id if origin else False
                ready = 'مخزون جاهز' if source.kind == 'stock' else 'انتظار أمر قائم'
                production_name = production.name or '' if production else ''
                label = '%s — %s' % (lane_labels.get(lane, source.role), ready)
                if production_name:
                    label += ' — %s' % production_name
                node = {
                    'id': node_id,
                    'label': label,
                    'kind': source.kind,
                    'lane': lane,
                    'stage_code': '',
                    'stage_id': False,
                    'estimated_hours': 0.0,
                    'material_count': 0,
                    'customized': False,
                    'production_id': production.id if production else False,
                    'production_name': production_name,
                    'quantity': 0.0,
                    'editable': False,
                }
                nodes.append(node)
                sources[node_id] = node
            sources[node_id]['quantity'] += source.quantity
            for destination in targets:
                add_edge(node_id, destination)

    return {
        'nodes': nodes,
        'edges': edges,
        'hours': piece.critical_path_hours or 0.0,
        'days': piece.critical_path_days or 0.0,
        'unknown_wait': unknown_wait,
    }


class NeedToProduceRouteGraph(models.Model):
    _inherit = 'furniture.need.to.produce'

    route_graph = fields.Json(
        string='خط إنتاج القطعة', compute='_compute_route_graph',
        readonly=True, store=False,
    )

    @api.depends(
        'company_id', 'product_id', 'furniture_model_id', 'bom_id', 'final_rule_id',
        'buffer_rule_id', 'target_lane', 'preview_json', 'is_custom',
        'stage_ids.plan_key', 'stage_ids.materials_initialized',
        'stage_ids.material_ids.product_id', 'stage_ids.material_ids.uom_id', 'stage_ids.material_ids.quantity',
        'state', 'critical_path_hours', 'critical_path_days', 'unknown_incoming_wait',
        'stage_ids', 'stage_ids.sequence', 'stage_ids.lane', 'stage_ids.stage_code',
        'stage_ids.stage_codes', 'stage_ids.quantity', 'stage_ids.estimated_hours',
        'stage_ids.production_id', 'stage_ids.production_id.name',
        'stage_ids.materials_customized', 'stage_ids.material_ids',
        'stage_ids.material_ids.stage_code', 'stage_ids.material_ids.customized',
        'stage_ids.input_ids', 'stage_ids.input_ids.kind', 'stage_ids.input_ids.role',
        'stage_ids.input_ids.quantity', 'stage_ids.input_ids.source_stage_id',
        'stage_ids.input_ids.output_id', 'stage_ids.input_ids.output_id.production_id.name',
        'stage_ids.input_ids.source_line_id', 'stage_ids.input_ids.source_line_id.production_id.name',
        'stage_ids.production_id.upstream_handoff_ids.state',
        'stage_ids.input_ids.source_line_id.downstream_handoff_ids.state',
        'stage_ids.input_ids.output_id.production_line_id.downstream_handoff_ids.state',
    )
    @api.depends_context('lang')
    def _compute_route_graph(self):
        Stage = self.env['furniture.need.to.produce.stage']
        stage_labels = dict(Stage._fields['stage_code']._description_selection(self.env))
        lane_labels = dict(Stage._fields['lane']._description_selection(self.env))
        for piece in self:
            graph = build_route_graph(piece, stage_labels, lane_labels)
            roots = {}
            for stage in piece.stage_ids:
                lines = stage.production_id.production_line_ids
                if len(lines) == 1:
                    codes = stage.stage_codes or [stage.stage_code]
                    roots[lines.id] = (lines, ['stage:%s:%s' % (stage.id, index) for index in range(len(codes))])
                for source in stage.input_ids:
                    if source.kind not in ('stock', 'incoming'):
                        continue
                    origin = source.output_id if source.kind == 'stock' else source.source_line_id
                    line = origin.production_line_id if source.kind == 'stock' else origin
                    if line and line.production_id.company_id == piece.company_id:
                        node_id = '%s:%s:%s' % (source.kind, origin.id, source.role)
                        roots.setdefault(line.id, (line, [node_id]))
            graph = append_handoff_history(graph, roots, stage_labels, lane_labels)
            # A grouped order's source line contains several pieces. Its total
            # quantity must not suppress (or invent) this piece's own checkmark.
            for source_stage in piece.stage_ids:
                claims = piece.stage_ids.input_ids.filtered(lambda claim: claim.source_stage_id == source_stage)
                if not claims:
                    continue
                consumed = sum(h.quantity for h in claims.handoff_ids
                               if h.state == 'consumed' and h.transfer_move_id.state == 'done'
                               and h.transfer_move_id.product_uom._compute_quantity(
                                   h.transfer_move_id.quantity, h.output_id.uom_id) + 0.000001 >= h.quantity)
                withdrawn = consumed + 0.000001 >= sum(claims.mapped('quantity'))
                for node in graph['nodes']:
                    if node.get('kind') == 'stage' and node.get('stage_id') == source_stage.id:
                        node['withdrawn'] = withdrawn
            graph['display_group_key'] = piece_display_key(piece, graph)
            piece.route_graph = graph
