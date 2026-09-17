# -*- coding: utf-8 -*-
import uuid

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Seed the order photo without reading files from the upgrader's home.

    Attachment-backed image fields live in ``ir.attachment`` while the binary
    file lives below Odoo's data directory.  A deployment command may run as a
    different OS user from the service, so reading and re-writing ``datas``
    here is unreliable.  Clone the attachment metadata instead: both records
    safely share the checksum-addressed file and Odoo's filestore GC keeps it
    while either attachment still references it.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Attachment = env['ir.attachment'].sudo()
    productions = env['furniture.mrp.production'].search([
        ('state', 'not in', ('done', 'cancelled')),
        ('tailoring_set_image_token', '=', False),
    ])
    for production in productions:
        source_attachment = Attachment
        source_line = env['furniture.mrp.production.line']
        for line in production.production_line_ids.sorted(
            lambda line: (line.sequence, line.id)
        ):
            attachment = Attachment.search([
                ('res_model', '=', 'furniture.mrp.production.line'),
                ('res_field', '=', 'batch_image_1920'),
                ('res_id', '=', line.id),
                ('type', '=', 'binary'),
            ], order='id desc', limit=1)
            if attachment:
                source_line = line
                source_attachment = attachment
                break
        if not source_attachment:
            continue

        target_attachment = Attachment.search([
            ('res_model', '=', 'furniture.mrp.production'),
            ('res_field', '=', 'tailoring_set_image_1920'),
            ('res_id', '=', production.id),
            ('type', '=', 'binary'),
        ], order='id desc', limit=1)
        if not target_attachment:
            cr.execute("""
                INSERT INTO ir_attachment (
                    res_id, company_id, file_size, create_uid, write_uid,
                    name, res_model, res_field, type, url, store_fname,
                    checksum, mimetype, description, index_content, public,
                    create_date, write_date, db_datas, original_id
                )
                SELECT
                    %s, company_id, file_size, %s, %s,
                    name, 'furniture.mrp.production',
                    'tailoring_set_image_1920', type, url, store_fname,
                    checksum, mimetype, description, index_content, public,
                    NOW(), NOW(), db_datas, original_id
                FROM ir_attachment
                WHERE id = %s
            """, (
                production.id,
                SUPERUSER_ID,
                SUPERUSER_ID,
                source_attachment.id,
            ))
        production.with_context(
            furniture_skip_stage_plan_sync=True,
            furniture_skip_material_refresh=True,
        ).write({
            'tailoring_set_image_token': (
                source_line.batch_image_token or uuid.uuid4().hex
            ),
        })
