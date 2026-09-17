# -*- coding: utf-8 -*-

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.furniture_mrp.models.mrp_lane_flow import _lane_route_values


@tagged('post_install', '-at_install')
class TestFurnitureLaneExtensionHooks(TransactionCase):
    """Extensions are registry-local and leave historical routes intact."""

    def test_metadata_maps_are_defensive_copies(self):
        production_model = self.env['furniture.mrp.production']
        helper_names = (
            '_furniture_lane_routes',
            '_furniture_lane_final_stages',
            '_furniture_lane_ready_stages',
            '_furniture_lane_output_lanes',
            '_furniture_lane_labels',
            '_furniture_lane_handoff_required_lanes',
            '_furniture_lane_handoff_target_stages',
            '_furniture_lane_handoff_stage_models',
        )
        for helper_name in helper_names:
            with self.subTest(helper=helper_name):
                values = getattr(production_model, helper_name)()
                values['_test_private_route'] = ('modified',)
                self.assertNotIn(
                    '_test_private_route',
                    getattr(production_model, helper_name)(),
                )
        self.assertEqual(
            production_model._furniture_lane_routes()['finish'],
            ('bases', 'finishing'),
        )
        self.assertEqual(
            production_model._furniture_lane_handoff_required_lanes()['finish'],
            ('frame',),
        )

    def test_header_and_line_route_flags_use_registry_extension(self):
        production_model = self.env['furniture.mrp.production']
        original_routes = production_model._furniture_lane_routes()
        extended_routes = {
            **original_routes,
            '_test_bases': ('bases',),
            '_test_preparation': ('finishing',),
        }
        with patch.object(
            type(production_model),
            '_furniture_lane_routes',
            lambda _self: dict(extended_routes),
        ):
            for model_name in (
                'furniture.mrp.production',
                'furniture.mrp.production.line',
            ):
                model = self.env[model_name]
                for lane, route in (
                    ('_test_bases', ('bases',)),
                    ('_test_preparation', ('finishing',)),
                ):
                    with self.subTest(model=model_name, lane=lane):
                        self.assertEqual(
                            _lane_route_values(model, lane),
                            model._stage_selection_vals(route),
                        )
        self.assertEqual(
            production_model._furniture_lane_routes(), original_routes,
        )
