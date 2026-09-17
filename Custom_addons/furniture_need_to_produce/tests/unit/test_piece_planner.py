"""Run without Odoo: python3 -m unittest discover -s .../tests/unit -v."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest


_PATH = Path(__file__).resolve().parents[2] / "services" / "piece_planner.py"
_SPEC = importlib.util.spec_from_file_location("need_to_produce_piece_planner", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
plan_piece = _MODULE.plan_piece


def stock(source_id, quantity):
    return {"kind": "stock", "source_id": source_id, "quantity": quantity}


def incoming(source_id, quantity):
    return {"kind": "incoming", "source_id": source_id, "quantity": quantity}


def lanes(plan):
    return [stage["lane"] for stage in plan["stages"]]


def stage(plan, lane):
    return next(item for item in plan["stages"] if item["lane"] == lane)


class TestPiecePlanner(unittest.TestCase):
    def test_parallel_finish_has_one_frame_input_and_a_single_joined_output(self):
        plan = plan_piece(parallel_finish=True)
        self.assertEqual(lanes(plan), ['frame', 'finish', 'tailoring', 'upholstery', 'painting', 'packaging'])
        pair = stage(plan, 'finish')
        self.assertEqual(pair['stage_codes'], ['bases', 'finishing'])
        self.assertEqual([(row['role'], row['quantity']) for row in pair['inputs']], [('frame', 1)])
        self.assertEqual(pair['output_role'], 'finish')
        self.assertTrue(pair['parallel_operations'])
        self.assertEqual(plan['total_work_hours'], 16)
        self.assertEqual(plan['critical_path_hours'], 10)
        self.assertIn('(القواعد + التجهيز)', plan['route_text'])

    def test_parallel_department_targets_share_the_same_ready_buffer(self):
        for target in ('bases', 'preparation'):
            plan = plan_piece(target, pools={'frame': [stock(1, 1)]}, parallel_finish=True)
            self.assertEqual(lanes(plan), ['finish'])
            self.assertEqual(plan['critical_path_hours'], 2)
            self.assertFalse(plan_piece(target, pools={'finish': [stock(2, 1)]},
                                        parallel_finish=True)['stages'])

    def test_stage_app_routes_stop_at_the_selected_buffer(self):
        expected = {
            'frame': ['priming', 'carpentry'],
            'bases': ['priming', 'carpentry', 'bases'],
            'preparation': ['priming', 'carpentry', 'bases', 'finishing'],
            'tailoring': ['tailoring'],
            'painting': ['painting'],
        }
        for target, codes in expected.items():
            with self.subTest(target=target):
                plan = plan_piece(target_lane=target)
                self.assertEqual([code for item in plan['stages'] for code in item['stage_codes']], codes)
                self.assertEqual(plan['stages'][-1]['lane'], target)
                self.assertNotIn('packaging', lanes(plan))
                self.assertNotIn('upholstery', lanes(plan))
        self.assertEqual(lanes(plan_piece('bases', pools={'frame': [stock(1, 1)]})), ['bases'])
        self.assertEqual(lanes(plan_piece('preparation', pools={'bases': [stock(1, 1)]})), ['preparation'])
        waiting = plan_piece('bases', pools={'frame': [incoming(1, 1)]})
        self.assertEqual(lanes(waiting), ['bases'])
        self.assertTrue(waiting['has_unknown_incoming_wait'])

    def test_user_three_different_piece_routes(self):
        pools = {"finish": [stock(1, 1)], "painting": [stock(2, 2)]}
        plans = [plan_piece(pools=pools, key_prefix="piece%d/" % number) for number in range(3)]
        self.assertEqual(lanes(plans[0]), ["tailoring", "upholstery", "packaging"])
        self.assertEqual(lanes(plans[1]), ["frame", "bases", "preparation", "tailoring", "upholstery", "packaging"])
        self.assertEqual(lanes(plans[2]), ["frame", "bases", "preparation", "tailoring", "upholstery", "painting", "packaging"])
        self.assertEqual(plans[0]["route_text"], "التفصيل → الكسوة → التغليف")
        self.assertEqual(pools["finish"][0]["quantity"], 0)
        self.assertEqual(pools["painting"][0]["quantity"], 0)
        keys = [item["key"] for plan in plans for item in plan["stages"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_scenario_one_all_three_inputs_ready(self):
        pools = {role: [stock(index, 3)] for index, role in enumerate(("finish", "tailoring", "painting"), 1)}
        for unused in range(3):
            plan = plan_piece(pools=pools)
            self.assertEqual(lanes(plan), ["upholstery", "packaging"])
            self.assertEqual(plan["total_work_hours"], 4)
            self.assertEqual(plan["critical_path_hours"], 4)
            self.assertEqual(plan["critical_path_days"], 0.4)
        self.assertTrue(all(entries[0]["quantity"] == 0 for entries in pools.values()))

    def test_scenario_two_tailoring_painting_ready_no_preparation(self):
        pools = {"tailoring": [stock(1, 3)], "painting": [stock(2, 3)]}
        for unused in range(3):
            plan = plan_piece(pools=pools)
            self.assertEqual(lanes(plan), ["frame", "bases", "preparation", "upholstery", "packaging"])
            self.assertEqual(stage(plan, "frame")["stage_codes"], ["priming", "carpentry"])
            self.assertEqual(stage(plan, "bases")["stage_codes"], ["bases"])
            self.assertEqual(stage(plan, "preparation")["stage_codes"], ["finishing"])
            self.assertEqual(plan["critical_path_hours"], 12)

    def test_scenario_three_painting_missing_does_not_gate_upholstery(self):
        pools = {"tailoring": [stock(1, 3)], "finish": [stock(2, 3)]}
        for unused in range(3):
            plan = plan_piece(pools=pools)
            self.assertEqual(lanes(plan), ["upholstery", "painting", "packaging"])
            self.assertFalse(stage(plan, "upholstery")["dependencies"])
            self.assertEqual(stage(plan, "packaging")["dependencies"], [stage(plan, "upholstery")["key"], stage(plan, "painting")["key"]])
            self.assertEqual(plan["total_work_hours"], 6)
            self.assertEqual(plan["critical_path_hours"], 4)

    def test_existing_upholstery_order_skips_all_upstream_inputs(self):
        pools = {"upholstery": [incoming(12, 1)], "painting": [stock(20, 1)], "finish": [stock(3, 3)]}
        plan = plan_piece(pools=pools)
        self.assertEqual(lanes(plan), ["packaging"])
        self.assertEqual(pools["finish"][0]["quantity"], 3)
        self.assertTrue(plan["has_unknown_incoming_wait"])
        self.assertTrue(plan["critical_path_is_lower_bound"])
        self.assertIsNone(plan["estimated_completion_hours"])
        self.assertEqual(plan["critical_path_hours"], 2)
        self.assertIsNone(plan["incoming_waits"][0]["wait_hours"])

    def test_stock_is_preferred_over_incoming_and_fifo_is_stable(self):
        pools = {"tailoring": [incoming(9, 10), stock(42, 0.4), stock(7, 0.6), stock(1, 3)]}
        plan = plan_piece("tailoring", pools=pools)
        self.assertEqual(plan["stages"], [])
        self.assertEqual([source["source_id"] for source in plan["outputs"]], [42, 7])
        self.assertEqual(pools["tailoring"][0]["quantity"], 10)
        self.assertEqual(pools["tailoring"][3]["quantity"], 3)

    def test_partial_stock_incoming_and_new_production_sum_to_one(self):
        pools = {"finish": [stock(1, 0.25), incoming(2, 0.25)], "bases": [stock(3, 0.2)]}
        plan = plan_piece("upholstery", pools=pools)
        preparation = stage(plan, "preparation")
        self.assertAlmostEqual(preparation["quantity"], 0.5)
        self.assertAlmostEqual(stage(plan, "bases")["quantity"], 0.3)
        self.assertAlmostEqual(stage(plan, "frame")["quantity"], 0.3)
        self.assertAlmostEqual(preparation["estimated_hours"], 1)
        finish_inputs = [row for row in stage(plan, "upholstery")["inputs"] if row["role"] == "finish"]
        self.assertAlmostEqual(sum(row["quantity"] for row in finish_inputs), 1)
        self.assertEqual([row["kind"] for row in finish_inputs], ["stock", "incoming", "stage"])

    def test_shared_incoming_quantity_is_consumed_only_once(self):
        pools = {"upholstery": [incoming(99, 2)]}
        first = plan_piece(pools=pools)
        second = plan_piece(pools=pools)
        third = plan_piece(pools=pools)
        self.assertEqual(lanes(first), ["painting", "packaging"])
        self.assertEqual(lanes(second), ["painting", "packaging"])
        self.assertIn("upholstery", lanes(third))
        self.assertEqual(pools["upholstery"][0]["quantity"], 0)

    def test_bases_stock_prevents_new_frame_order(self):
        plan = plan_piece("preparation", pools={"bases": [stock(1, 1)]})
        self.assertEqual(lanes(plan), ["preparation"])
        self.assertEqual(stage(plan, "preparation")["inputs"][0]["role"], "bases")

    def test_frame_stock_skips_priming_and_assembly_only(self):
        plan = plan_piece("preparation", pools={"frame": [stock(1, 1)]})
        self.assertEqual(lanes(plan), ["bases", "preparation"])
        self.assertEqual(plan["critical_path_hours"], 4)

    def test_empty_pools_exact_topology_parallel_critical_path(self):
        plan = plan_piece()
        self.assertEqual(lanes(plan), ["frame", "bases", "preparation", "tailoring", "upholstery", "painting", "packaging"])
        self.assertEqual(plan["total_work_hours"], 16)
        self.assertEqual(plan["critical_path_hours"], 12)
        self.assertEqual(plan["critical_path_days"], 1.2)
        self.assertEqual(plan["estimated_completion_hours"], 12)
        visited = set()
        for item in plan["stages"]:
            self.assertTrue(set(item["dependencies"]).issubset(visited))
            visited.add(item["key"])

    def test_unknown_incoming_wait_propagates_downstream(self):
        plan = plan_piece(pools={"frame": [incoming(11, 1)]})
        self.assertTrue(stage(plan, "bases")["has_unknown_incoming_wait"])
        self.assertTrue(stage(plan, "preparation")["has_unknown_incoming_wait"])
        self.assertTrue(stage(plan, "packaging")["has_unknown_incoming_wait"])
        self.assertFalse(stage(plan, "painting")["has_unknown_incoming_wait"])
        self.assertIsNone(plan["estimated_completion_hours"])

    def test_target_is_already_ready_no_stages_or_input_consumption(self):
        pools = {"packaging": [stock(1, 1)], "finish": [stock(2, 1)]}
        plan = plan_piece(pools=pools)
        self.assertEqual(plan["stages"], [])
        self.assertEqual(plan["total_work_hours"], 0)
        self.assertEqual(plan["critical_path_hours"], 0)
        self.assertEqual(pools["finish"][0]["quantity"], 1)

    def test_incoming_target_has_unknown_wait_without_new_stages(self):
        plan = plan_piece(pools={"packaging": [incoming(1, 1)]})
        self.assertEqual(plan["stages"], [])
        self.assertTrue(plan["critical_path_is_lower_bound"])
        self.assertIsNone(plan["estimated_completion_hours"])

    def test_copy_allows_preview_without_changing_original(self):
        original = {"tailoring": [stock(1, 1)]}
        plan_piece(pools=deepcopy(original))
        self.assertEqual(original["tailoring"][0]["quantity"], 1)

    def test_deterministic_identical_inputs(self):
        pools = {"finish": [stock(42, 0.5)], "painting": [incoming(24, 1)]}
        self.assertEqual(plan_piece(pools=deepcopy(pools)), plan_piece(pools=deepcopy(pools)))

    def test_metadata_and_entry_identity_are_preserved(self):
        entry = dict(stock(1, 1), audit="keep")
        pools = {"tailoring": [entry]}
        plan_piece("tailoring", pools=pools)
        self.assertIs(entry, pools["tailoring"][0])
        self.assertEqual(entry["audit"], "keep")

    def test_invalid_pools_do_not_partially_consume_stock(self):
        pools = {"finish": [stock(1, 1)], "painting": [stock(2, -1)]}
        original = deepcopy(pools)
        with self.assertRaises(ValueError):
            plan_piece(pools=pools)
        self.assertEqual(pools, original)

    def test_duplicate_sources_are_rejected_not_double_counted(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            plan_piece(pools={"finish": [stock(1, 1), stock(1, 1)]})

    def test_invalid_input_contract(self):
        for quantity in (0, -1, float("inf"), float("nan"), True, None):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                plan_piece(quantity=quantity)
        for pools in ([], {"preparation": []}, {"finish": "bad"}, {"finish": [{"kind": "other", "source_id": 1, "quantity": 1}]}, {"finish": [stock("1", 1)]}):
            with self.subTest(pools=pools), self.assertRaises(ValueError):
                plan_piece(pools=pools)
        with self.assertRaises(ValueError):
            plan_piece("unknown")


if __name__ == "__main__":
    unittest.main()
