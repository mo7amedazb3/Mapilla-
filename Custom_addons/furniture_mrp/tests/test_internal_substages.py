from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


INTERNAL_STAGE_CONFIGS = (
    {
        'model': 'furniture.mrp.bases',
        'context_key': 'bases_internal_substage',
        'codes': ('preparation', 'foam'),
        'fields': (
            'substage_preparation_state',
            'substage_foam_state',
        ),
        'view': 'furniture_mrp.view_furniture_mrp_bases_form',
        'page_label': 'مراحل القواعد',
    },
    {
        'model': 'furniture.mrp.tailoring',
        'context_key': 'tailoring_internal_substage',
        'codes': ('cutting', 'sewing', 'ironing'),
        'fields': (
            'substage_cutting_state',
            'substage_sewing_state',
            'substage_ironing_state',
        ),
        'view': 'furniture_mrp.view_furniture_mrp_tailoring_form',
        'page_label': 'مراحل التفصيل',
    },
)


class TestInternalSubstages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.production = cls.env['furniture.mrp.production'].create({
            'name': 'MRP/INTERNAL/SUBSTAGE/TEST',
            'product_qty': 1.0,
        })

    def _create_stage(self, config, state='pending'):
        return self.env[config['model']].create({
            'name': '%s/INTERNAL/SUBSTAGE' % config['model'].rsplit('.', 1)[-1].upper(),
            'production_order_id': self.production.id,
            'state': state,
        })

    def _complete(self, stage, config, code):
        return stage.with_context(**{
            config['context_key']: code,
        }).action_complete_internal_substage()

    def test_fixed_substages_start_together_and_finish_independently(self):
        for config in INTERNAL_STAGE_CONFIGS:
            with self.subTest(model=config['model']):
                stage = self._create_stage(config)
                self.assertTrue(all(stage[field_name] == 'pending' for field_name in config['fields']))
                self.assertEqual(stage.substage_progress, 0.0)
                self.assertFalse(stage.all_substages_done)

                stage.write({'state': 'in_progress'})
                started_fields = stage._start_internal_substages()
                self.assertEqual(set(started_fields), set(config['fields']))
                self.assertTrue(all(
                    stage[field_name] == 'in_progress'
                    for field_name in config['fields']
                ))

                reversed_codes = tuple(reversed(config['codes']))
                field_by_code = dict(zip(config['codes'], config['fields']))
                for index, code in enumerate(reversed_codes, start=1):
                    self._complete(stage, config, code)
                    self.assertEqual(stage[field_by_code[code]], 'done')
                    self.assertAlmostEqual(
                        stage.substage_progress,
                        index / len(config['codes']) * 100.0,
                    )
                    self.assertEqual(
                        stage.substage_summary,
                        '%s/%s مراحل منتهية' % (index, len(config['codes'])),
                    )

                self.assertTrue(stage.all_substages_done)
                self.assertEqual(stage.current_substage, 'جاهز للجودة')

    def test_quality_is_blocked_until_all_substages_are_done(self):
        for config in INTERNAL_STAGE_CONFIGS:
            with self.subTest(model=config['model']):
                stage = self._create_stage(config, state='in_progress')
                stage._start_internal_substages()

                with self.assertRaises(UserError):
                    stage.action_send_to_quality()

                stage.write({'state': 'quality_check'})
                with self.assertRaises(UserError):
                    stage.action_approve_quality()

                stage.write({'state': 'in_progress'})
                for code in config['codes']:
                    self._complete(stage, config, code)
                stage.action_send_to_quality()
                self.assertEqual(stage.state, 'quality_check')

    def test_rejection_restarts_every_substage_and_next_batch_resets(self):
        for config in INTERNAL_STAGE_CONFIGS:
            with self.subTest(model=config['model']):
                stage = self._create_stage(config, state='in_progress')
                stage.write({field_name: 'done' for field_name in config['fields']})
                stage.write({'state': 'quality_check'})

                stage.action_reject_quality()
                self.assertEqual(stage.state, 'in_progress')
                self.assertTrue(all(
                    stage[field_name] == 'in_progress'
                    for field_name in config['fields']
                ))

                stage.write({field_name: 'done' for field_name in config['fields']})
                stage.write({'state': 'pending'})
                stage._reset_substages_for_next_batch()
                self.assertTrue(all(
                    stage[field_name] == 'pending'
                    for field_name in config['fields']
                ))
                stage.write({'state': 'in_progress'})
                stage._start_internal_substages()
                self.assertTrue(all(
                    stage[field_name] == 'in_progress'
                    for field_name in config['fields']
                ))

    def test_forms_expose_fixed_substage_rows_and_quality_gate(self):
        for config in INTERNAL_STAGE_CONFIGS:
            with self.subTest(view=config['view']):
                view = self.env.ref(config['view'])
                arch = self.env[view.model].get_view(
                    view_id=view.id,
                    view_type='form',
                )['arch']
                document = etree.fromstring(arch.encode())
                pages = document.xpath(
                    "//page[contains(@string, '%s')]" % config['page_label']
                )
                self.assertEqual(len(pages), 1)
                self.assertEqual(
                    len(pages[0].xpath(
                        ".//button[@name='action_complete_internal_substage']"
                    )),
                    len(config['codes']),
                )
                for field_name in config['fields']:
                    self.assertEqual(
                        len(pages[0].xpath(".//field[@name='%s']" % field_name)),
                        1,
                    )
                quality_buttons = document.xpath(
                    "//header/button[@name='action_send_to_quality']"
                )
                self.assertEqual(len(quality_buttons), 1)
                self.assertIn(
                    'not all_substages_done',
                    quality_buttons[0].get('invisible', ''),
                )
