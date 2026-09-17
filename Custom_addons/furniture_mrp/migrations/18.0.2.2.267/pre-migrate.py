# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Replace constraints whose definitions changed in the whole-piece MPS."""
    cr.execute(
        'ALTER TABLE IF EXISTS furniture_mrp_mps_operation '
        'DROP CONSTRAINT IF EXISTS furniture_mrp_mps_operation_duration_positive'
    )
    cr.execute(
        'ALTER TABLE IF EXISTS furniture_mrp_mps_profile '
        'DROP CONSTRAINT IF EXISTS furniture_mrp_mps_profile_model_company_unique'
    )
    cr.execute(
        'ALTER TABLE IF EXISTS furniture_mrp_mps_profile '
        'DROP CONSTRAINT IF EXISTS furniture_mrp_mps_profile_model_company_kind_unique'
    )
