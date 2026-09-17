"""Database-free route graph tests; these never create stock, plans or MOs.

Also runnable directly using the Odoo Python environment and PYTHONPATH set to
the Odoo source tree. No registry or database connection is required.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from odoo.tests.common import BaseCase, tagged


if __package__ and __package__.startswith('odoo.addons.'):
    from ..models.route_graph import NeedToProduceRouteGraph, build_route_graph, append_handoff_history
    from ..services.piece_planner import plan_piece, STAGE_LABELS
else:
    # Load just these two files for the standalone test, not the addon's full
    # dependency tree. The Odoo model metaclass requires the canonical prefix.
    root = Path(__file__).resolve().parents[1]
    loaded = {}
    for module_name, relative_path in (
        ('route_graph', 'models/route_graph.py'),
        ('piece_planner', 'services/piece_planner.py'),
    ):
        spec = importlib.util.spec_from_file_location(
            'odoo.addons.furniture_need_to_produce.%s' % module_name, root / relative_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[module_name] = module
    NeedToProduceRouteGraph = loaded['route_graph'].NeedToProduceRouteGraph
    build_route_graph = loaded['route_graph'].build_route_graph
    append_handoff_history = loaded['route_graph'].append_handoff_history
    plan_piece = loaded['piece_planner'].plan_piece
    STAGE_LABELS = loaded['piece_planner'].STAGE_LABELS


LANE_LABELS = {
    'frame': 'النجارة', 'bases': 'القواعد', 'preparation': 'التجهيز',
    'tailoring': 'التفصيل', 'painting': 'الدهانات',
    'upholstery': 'الكسوة', 'packaging': 'التغليف',
}


@tagged('standard', 'post_install', '-at_install')
class TestNeedRouteGraph(BaseCase):
    def _piece(self, pools=None, target_lane='packaging'):
        """Adapt an actual planner result to read-only record-shaped fixtures."""
        plan = plan_piece(target_lane=target_lane, pools=deepcopy(pools or {}))
        stages = {}
        for index, row in enumerate(plan['stages'], 1):
            stages[row['key']] = SimpleNamespace(
                id=index, sequence=index * 10, lane=row['lane'],
                stage_code=row['stage_code'], stage_codes=row['stage_codes'],
                quantity=row['quantity'], estimated_hours=row['estimated_hours'],
                production_id=False, materials_customized=False,
                material_ids=[], input_ids=[],
            )
        for row in plan['stages']:
            for source in row['inputs']:
                origin = False
                if source['kind'] != 'stage':
                    origin = SimpleNamespace(
                        id=source['source_id'],
                        production_id=SimpleNamespace(
                            id=1000 + source['source_id'],
                            name='MO/ROUTE/%04d' % source['source_id'],
                        ),
                    )
                stages[row['key']].input_ids.append(SimpleNamespace(
                    kind=source['kind'], role=source['role'], quantity=source['quantity'],
                    source_stage_id=stages.get(source.get('source_key'), False),
                    output_id=origin if source['kind'] == 'stock' else False,
                    source_line_id=origin if source['kind'] == 'incoming' else False,
                ))
        return SimpleNamespace(
            state='draft', stage_ids=list(stages.values()),
            critical_path_hours=plan['critical_path_hours'],
            critical_path_days=plan['critical_path_days'],
            unknown_incoming_wait=plan['has_unknown_incoming_wait'],
        )

    def _graph(self, piece):
        return build_route_graph(piece, STAGE_LABELS, LANE_LABELS)

    def _stage(self, piece, lane):
        return next(stage for stage in piece.stage_ids if stage.lane == lane)

    def _node(self, graph, code):
        return next(node for node in graph['nodes'] if node['stage_code'] == code)

    def _edge_codes(self, graph):
        codes = {node['id']: node['stage_code'] for node in graph['nodes']}
        return {(codes[edge['from']], codes[edge['to']]) for edge in graph['edges']}

    def test_ready_three_inputs_only_requires_upholstery_and_packaging(self):
        piece = self._piece({
            role: [{'kind': 'stock', 'source_id': index, 'quantity': 1}]
            for index, role in enumerate(('finish', 'tailoring', 'painting'), 101)
        })
        graph = self._graph(piece)
        self.assertEqual(
            [node['stage_code'] for node in graph['nodes'] if node['kind'] == 'stage'],
            ['upholstery', 'packaging'],
        )
        self.assertEqual((graph['hours'], graph['days'], graph['unknown_wait']), (4, 0.4, False))
        self.assertEqual(len(graph['edges']), 4)
        for role, code in (('finish', 'upholstery'), ('tailoring', 'upholstery'), ('painting', 'packaging')):
            source = next(node for node in graph['nodes'] if node['id'].endswith(':%s' % role))
            self.assertIn({'from': source['id'], 'to': self._node(graph, code)['id']}, graph['edges'])
            self.assertEqual(source['quantity'], 1)
            self.assertIn('مخزون جاهز', source['label'])
            self.assertFalse(source['editable'])

    def test_no_preparation_expands_connected_frame_operations(self):
        piece = self._piece({
            'tailoring': [{'kind': 'stock', 'source_id': 101, 'quantity': 1}],
            'painting': [{'kind': 'stock', 'source_id': 102, 'quantity': 1}],
        })
        graph = self._graph(piece)
        expected = ['priming', 'carpentry', 'bases', 'finishing', 'upholstery', 'packaging']
        self.assertEqual([node['stage_code'] for node in graph['nodes'] if node['kind'] == 'stage'], expected)
        self.assertTrue(set(zip(expected, expected[1:])).issubset(self._edge_codes(graph)))
        priming, carpentry = self._node(graph, 'priming'), self._node(graph, 'carpentry')
        self.assertNotEqual(priming['id'], carpentry['id'])
        self.assertEqual(priming['stage_id'], carpentry['stage_id'])
        self.assertEqual((priming['estimated_hours'], carpentry['estimated_hours']), (2, 2))
        self.assertEqual((graph['hours'], graph['days']), (12, 1.2))

    def test_missing_painting_is_parallel_until_packaging(self):
        piece = self._piece({
            'tailoring': [{'kind': 'stock', 'source_id': 101, 'quantity': 1}],
            'finish': [{'kind': 'stock', 'source_id': 102, 'quantity': 1}],
        })
        graph = self._graph(piece)
        self.assertEqual(
            [node['stage_code'] for node in graph['nodes'] if node['kind'] == 'stage'],
            ['upholstery', 'painting', 'packaging'],
        )
        links = self._edge_codes(graph)
        self.assertIn(('painting', 'packaging'), links)
        self.assertIn(('upholstery', 'packaging'), links)
        self.assertNotIn(('painting', 'upholstery'), links)
        self.assertNotIn(('upholstery', 'painting'), links)
        self.assertEqual((graph['hours'], graph['days']), (4, 0.4))
        self.assertEqual(sum(node['estimated_hours'] for node in graph['nodes']), 6)

    def test_full_route_is_saved_dag_not_a_flat_stage_list(self):
        graph = self._graph(self._piece())
        self.assertEqual(self._edge_codes(graph), {
            ('priming', 'carpentry'), ('carpentry', 'bases'), ('bases', 'finishing'),
            ('finishing', 'upholstery'), ('tailoring', 'upholstery'),
            ('upholstery', 'packaging'), ('painting', 'packaging'),
        })
        self.assertEqual((graph['hours'], graph['days']), (12, 1.2))
        self.assertEqual(sum(node['estimated_hours'] for node in graph['nodes']), 16)

    def test_incoming_uses_actual_order_ref_without_invented_upstream_stages(self):
        piece = self._piece({
            'upholstery': [{'kind': 'incoming', 'source_id': 987654, 'quantity': 1}],
            'painting': [{'kind': 'stock', 'source_id': 102, 'quantity': 1}],
        })
        claim = self._stage(piece, 'packaging').input_ids[0]
        claim.source_line_id.production_id.name = 'MO/2026/042'
        graph = self._graph(piece)
        source = next(node for node in graph['nodes'] if node['kind'] == 'incoming')
        self.assertEqual(source['production_name'], 'MO/2026/042')
        self.assertIn('MO/2026/042', source['label'])
        self.assertIn('الكسوة', source['label'])
        self.assertIn('انتظار أمر قائم', source['label'])
        self.assertNotIn('987654', source['label'])
        self.assertEqual(source['production_id'], claim.source_line_id.production_id.id)
        self.assertFalse(source['editable'])
        self.assertTrue(graph['unknown_wait'])
        self.assertEqual((graph['hours'], graph['days']), (2, 0.2))
        self.assertEqual([node['stage_code'] for node in graph['nodes'] if node['kind'] == 'stage'], ['packaging'])

    def test_only_exact_same_source_kind_and_role_is_shared(self):
        piece = self._piece({'finish': [{'kind': 'stock', 'source_id': 101, 'quantity': 1}]})
        upholstery = self._stage(piece, 'upholstery')
        packaging = self._stage(piece, 'packaging')
        original = upholstery.input_ids[0]
        packaging.input_ids.append(deepcopy(original))
        different_role = deepcopy(original)
        different_role.role = 'painting'
        packaging.input_ids.append(different_role)
        different_source = deepcopy(original)
        different_source.output_id.id = 102
        packaging.input_ids.append(different_source)
        incoming = deepcopy(original)
        incoming.kind = 'incoming'
        incoming.source_line_id, incoming.output_id = incoming.output_id, False
        packaging.input_ids.append(incoming)
        graph = self._graph(piece)
        stock_nodes = [node for node in graph['nodes'] if node['kind'] == 'stock']
        self.assertEqual(len(stock_nodes), 3)
        shared = next(node for node in stock_nodes if node['id'] == 'stock:101:finish')
        self.assertEqual(shared['quantity'], 2)
        self.assertEqual(len([edge for edge in graph['edges'] if edge['from'] == shared['id']]), 2)
        self.assertEqual(len([node for node in graph['nodes'] if node['kind'] == 'incoming']), 1)

    def test_fractional_sources_and_operation_hours_remain_exact(self):
        piece = self._piece({
            'finish': [{'kind': 'stock', 'source_id': 101, 'quantity': 0.25},
                       {'kind': 'incoming', 'source_id': 102, 'quantity': 0.25}],
            'bases': [{'kind': 'stock', 'source_id': 103, 'quantity': 0.2}],
        }, target_lane='upholstery')
        graph = self._graph(piece)
        self.assertAlmostEqual(self._node(graph, 'priming')['quantity'], 0.3)
        self.assertAlmostEqual(self._node(graph, 'priming')['estimated_hours'], 0.6)
        self.assertAlmostEqual(self._node(graph, 'carpentry')['estimated_hours'], 0.6)
        self.assertAlmostEqual(self._node(graph, 'finishing')['quantity'], 0.5)
        self.assertEqual(sorted(node['quantity'] for node in graph['nodes'] if node['kind'] != 'stage'), [0.2, 0.25, 0.25])

    def test_material_counts_customization_and_editor_state(self):
        piece = self._piece()
        frame = self._stage(piece, 'frame')
        frame.material_ids = [
            SimpleNamespace(stage_code='priming', customized=False),
            SimpleNamespace(stage_code='carpentry', customized=True),
            SimpleNamespace(stage_code='carpentry', customized=False),
        ]
        graph = self._graph(piece)
        self.assertEqual(self._node(graph, 'priming')['material_count'], 1)
        self.assertEqual(self._node(graph, 'carpentry')['material_count'], 2)
        self.assertTrue(self._node(graph, 'priming')['customized'])
        self.assertTrue(self._node(graph, 'carpentry')['editable'])
        frame.material_ids = []
        frame.materials_customized = True
        frame.production_id = SimpleNamespace(id=808, name='MO/APPROVED/808')
        for state in ('approved', 'done', 'cancelled'):
            with self.subTest(state=state):
                piece.state = state
                graph = self._graph(piece)
                self.assertTrue(all(not node['editable'] for node in graph['nodes']))
                node = self._node(graph, 'carpentry')
                self.assertTrue(node['customized'])
                self.assertEqual(node['material_count'], 0)
                self.assertEqual((node['production_id'], node['production_name']), (808, 'MO/APPROVED/808'))

    def test_multi_operation_inputs_attach_first_and_outputs_leave_last(self):
        piece = self._piece()
        frame = self._stage(piece, 'frame')
        tailoring = self._stage(piece, 'tailoring')
        # The graph renderer follows saved claims even if the saved route is
        # different from today's default planner. It must not re-plan it.
        frame.input_ids.append(SimpleNamespace(kind='stage', source_stage_id=tailoring))
        graph = self._graph(piece)
        links = self._edge_codes(graph)
        self.assertIn(('tailoring', 'priming'), links)
        self.assertNotIn(('tailoring', 'carpentry'), links)
        self.assertIn(('carpentry', 'bases'), links)
        self.assertNotIn(('priming', 'bases'), links)

    def test_missing_predecessor_is_not_rendered_as_fabricated_edge(self):
        piece = self._piece()
        self._stage(piece, 'bases').input_ids[0].source_stage_id = SimpleNamespace(id=9999)
        graph = self._graph(piece)
        self.assertNotIn(('carpentry', 'bases'), self._edge_codes(graph))
        identifiers = {node['id'] for node in graph['nodes']}
        self.assertTrue(all(edge['from'] in identifiers and edge['to'] in identifiers for edge in graph['edges']))

    def test_empty_stage_codes_fall_back_to_saved_first_operation(self):
        piece = self._piece(target_lane='tailoring')
        piece.stage_ids[0].stage_codes = False
        graph = self._graph(piece)
        self.assertEqual(len(graph['nodes']), 1)
        self.assertEqual(graph['nodes'][0]['stage_code'], 'tailoring')
        self.assertEqual(graph['nodes'][0]['estimated_hours'], 2)

    def test_empty_route_is_safe(self):
        piece = self._piece({'packaging': [{'kind': 'stock', 'source_id': 101, 'quantity': 1}]})
        self.assertEqual(self._graph(piece), {
            'nodes': [], 'edges': [], 'hours': 0.0, 'days': 0.0, 'unknown_wait': False,
        })

    def test_graph_does_not_modify_preview_or_materials_and_is_json_serializable(self):
        piece = self._piece({'finish': [{'kind': 'incoming', 'source_id': 101, 'quantity': 0.5}]})
        before = deepcopy(piece)
        graph = self._graph(piece)
        self.assertEqual(piece, before)
        self.assertEqual(graph, self._graph(piece))
        self.assertEqual(json.loads(json.dumps(graph, allow_nan=False)), graph)

    def test_field_is_readonly_nonstored_and_dependencies_cover_editor_refresh(self):
        field = NeedToProduceRouteGraph.route_graph
        self.assertTrue(field.readonly)
        self.assertFalse(field.store)
        self.assertFalse(field.inverse)
        self.assertEqual(field.compute, '_compute_route_graph')
        dependencies = set(NeedToProduceRouteGraph._compute_route_graph._depends)
        self.assertTrue({
            'state', 'stage_ids.material_ids', 'stage_ids.material_ids.customized',
            'stage_ids.material_ids.stage_code', 'stage_ids.materials_customized',
            'stage_ids.input_ids.source_stage_id', 'stage_ids.input_ids.quantity',
            'stage_ids.input_ids.output_id.production_id.name',
            'stage_ids.input_ids.source_line_id.production_id.name',
        }.issubset(dependencies))

    def _history_line(self, identifier, codes, company=1):
        unit = SimpleNamespace(rounding=.01, _compute_quantity=lambda qty, target: qty)
        return SimpleNamespace(
            id=identifier, production_id=SimpleNamespace(id=identifier, name='HISTORY/%s' % identifier, company_id=company),
            product_id=SimpleNamespace(uom_id=unit), product_uom_id=unit, product_qty=3,
            downstream_handoff_ids=[], _selected_stage_codes=lambda: codes,
        )

    def _history_transfer(self, target, source, role, state='consumed', move_state='done', quantity=3, moved=3):
        transfer = SimpleNamespace(
            id=source.id, state=state, quantity=quantity, role=role,
            output_id=SimpleNamespace(production_line_id=source),
            transfer_move_id=SimpleNamespace(state=move_state, quantity=moved, product_uom=source.product_uom_id),
        )
        target.downstream_handoff_ids.append(transfer)
        return transfer

    def _history_fixture(self):
        piece = self._piece({'upholstery': [{'kind': 'incoming', 'source_id': 7, 'quantity': 1}],
                             'painting': [{'kind': 'stock', 'source_id': 8, 'quantity': 1}]})
        graph = self._graph(piece)
        upholstery = self._history_line(7, ['upholstery'])
        finish = self._history_line(6, ['bases', 'finishing'])
        frame = self._history_line(5, ['priming', 'carpentry'])
        tailoring = self._history_line(4, ['tailoring'])
        self._history_transfer(upholstery, finish, 'finish')
        self._history_transfer(finish, frame, 'frame')
        self._history_transfer(upholstery, tailoring, 'tailoring')
        roots = {7: (upholstery, ['incoming:7:upholstery'])}
        return graph, roots, (upholstery, finish, frame, tailoring)

    def test_consumed_history_expands_actual_operations_and_preserves_planning(self):
        graph, roots, lines = self._history_fixture()
        old = deepcopy(graph)
        append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
        history = [node for node in graph['nodes'] if node['kind'] == 'history']
        self.assertEqual({n['stage_code'] for n in history}, {'priming', 'carpentry', 'bases', 'finishing', 'tailoring'})
        self.assertTrue(all(n['withdrawn'] and not n['editable'] and not n['stage_id'] for n in history))
        self.assertTrue(all(n['quantity'] == 3 and n['estimated_hours'] == 0 for n in history))
        self.assertEqual(graph['nodes'][:len(old['nodes'])], old['nodes'])
        self.assertEqual((graph['hours'], graph['days'], graph['unknown_wait']), (2, .2, True))
        self.assertIn(('priming', 'carpentry'), self._edge_codes(graph))
        self.assertIn(('carpentry', 'bases'), self._edge_codes(graph))
        self.assertIn(('bases', 'finishing'), self._edge_codes(graph))
        self.assertEqual(json.loads(json.dumps(graph)), graph)
        again = deepcopy(graph)
        append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
        self.assertEqual(graph, again)

    def test_reservation_or_partial_movement_is_not_a_withdrawal(self):
        for state, move_state, moved in [('reserved', 'assigned', 3), ('consumed', 'assigned', 3),
                                         ('consumed', 'done', 2), ('reserved', 'done', 3)]:
            with self.subTest(state=state, move=move_state, moved=moved):
                graph, roots, (upholstery, *rest) = self._history_fixture()
                transfer = upholstery.downstream_handoff_ids[0]
                transfer.state, transfer.transfer_move_id.state, transfer.transfer_move_id.quantity = state, move_state, moved
                append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
                finish = [n for n in graph['nodes'] if n.get('handoff_id') == transfer.id]
                self.assertTrue(finish)
                self.assertTrue(all(not n['withdrawn'] for n in finish))

    def test_cancelled_cross_company_and_cyclic_history_are_not_fabricated(self):
        graph, roots, (upholstery, finish, frame, tailoring) = self._history_fixture()
        upholstery.downstream_handoff_ids[1].state = 'cancelled'
        self._history_transfer(frame, upholstery, 'upholstery')
        self._history_transfer(upholstery, self._history_line(9, ['painting'], company=2), 'painting')
        append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
        self.assertTrue(graph['history_incomplete'])
        self.assertNotIn('tailoring', [n['stage_code'] for n in graph['nodes']])
        self.assertFalse(any(n.get('handoff_id') in (7, 9) for n in graph['nodes']))

    def test_missing_stage_metadata_keeps_role_without_invented_operations(self):
        graph, roots, (upholstery, finish, *rest) = self._history_fixture()
        finish._selected_stage_codes = lambda: []
        append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
        nodes = [n for n in graph['nodes'] if n.get('handoff_id') == 6]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['label'], 'التجهيز')
        self.assertEqual(nodes[0]['stage_code'], '')

    def test_existing_planned_nodes_are_reused_not_duplicated(self):
        graph, roots, (upholstery, finish, *rest) = self._history_fixture()
        graph['nodes'].append({'id': 'planned-finish', 'stage_code': 'finishing', 'kind': 'stage', 'quantity': 3})
        roots[finish.id] = (finish, ['planned-finish'])
        append_handoff_history(graph, roots, STAGE_LABELS, LANE_LABELS)
        self.assertFalse(any(n.get('handoff_id') == finish.id for n in graph['nodes']))
        self.assertTrue(next(n for n in graph['nodes'] if n['id'] == 'planned-finish')['withdrawn'])
        self.assertIn({'from': 'planned-finish', 'to': 'incoming:7:upholstery'}, graph['edges'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
