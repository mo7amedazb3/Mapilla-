import hashlib
import base64
import json
import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.furniture_mrp.models.mrp_stage_dashboard import FURNITURE_STAGE_DASHBOARD_SELECTION

BATCH_METHODS = {
    'materials': 'action_stage_dashboard_request_product_batch_materials',
    'receive': 'action_stage_dashboard_receive_product_batch_materials',
    'start': 'action_stage_dashboard_start_product_batch',
    'finish': 'action_stage_dashboard_finish_product_batch',
    'bom': 'action_open_stage_dashboard_product_batch_bom',
    'warning': 'action_open_stage_dashboard_product_warning',
    'quality': 'action_stage_dashboard_review_product_batch_quality',
    'pause': 'action_stage_dashboard_pause_product_batch_timer',
    'resume': 'action_stage_dashboard_resume_product_batch_timer',
}
ORDER_METHODS = {
    'materials': 'action_stage_dashboard_request_order_materials',
    'receive': 'action_stage_dashboard_request_order_materials',
    'start': 'action_stage_dashboard_start_order_product',
    'finish': 'action_stage_dashboard_finish_order_stage',
    'bom': 'action_open_stage_dashboard_bom',
    'warning': 'action_open_stage_dashboard_order_product_warning',
    'quality': 'action_stage_dashboard_review_order_product_quality',
    'pause': 'action_stage_dashboard_pause_order_timer',
    'resume': 'action_stage_dashboard_resume_order_timer',
}
WARNING = 'furniture.mrp.product.production.warning.wizard'
BOM = 'furniture.mrp.stage.product.batch.bom.wizard'
FIRST = 'furniture.mrp.first.stage.start.wizard'
CARRY = 'furniture.mrp.stage.start.carryover.wizard'
RECEIPTS = ('furniture.mrp.store.receipt.wizard', 'furniture.mrp.advance.material.receipt.wizard')


def app_quantity(value):
    try:
        value = float(str(value or 0).translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')))
    except (ValueError, TypeError):
        raise ValidationError('اكتب كمية صحيحة.')
    if not 0 <= value < 1e9:
        raise ValidationError('الكمية غير صحيحة.')
    return value


class SupervisorAppAPI(models.Model):
    _inherit = 'furniture.mrp.production'

    @api.model
    def _supervisor_app_dispatch(self, operation, data, session):
        profile = self._stage_dashboard_access_profile()
        codes = profile['allowed_stage_codes']
        if not codes:
            raise AccessError('حسابك غير مربوط بمرحلة إنتاج. تواصل مع مسؤول النظام.')
        if operation == 'profile':
            labels = dict(FURNITURE_STAGE_DASHBOARD_SELECTION)
            return {
                'name': self.env.user.name, 'company': self.env.company.name,
                'is_manager': profile['is_manager'],
                'supply_stages': list(self.env['furniture.assembly.requisition']._allowed_stage_codes())
                    if 'furniture.assembly.requisition' in self.env else [],
                'stages': [{'code': code, 'label': labels[code]} for code in codes],
            }
        if operation == 'dashboard':
            stage = data.get('stage') or (codes[0] if codes else False)
            result = self.get_stage_dashboard_data(stage_code=stage)
            result['product_batches'] = [dict(
                row, product_name=(row.get('product') or {}).get('name'),
                model_name=(row.get('model') or {}).get('name'),
                uom_name=(row.get('uom') or {}).get('name'),
            ) for row in result.get('product_batches', []) if row.get('state') not in ('done', 'cancelled')]
            result['orders'] = [row for row in result.get('orders', []) if not (
                row.get('planned_qty', 0) > 0
                and row.get('completed_qty', 0) >= row.get('planned_qty', 0) - 0.000001)]
            for order in result['orders']:
                for product in order.get('product_lines', []):
                    product['can_open_bom'] = bool(product.get('bom_id') and product.get('production_line_id'))
            result['history'] = self.get_need_supervisor_stage_sections(stage)
            return result
        if operation == 'requests':
            return self._supervisor_app_requests(codes, data.get('offset', 0))
        if operation == 'order_image':
            dashboard = self.get_stage_dashboard_data(stage_code=data.get('stage'))
            identifier = int(data.get('order_id') or 0)
            card = next((row for row in dashboard.get('orders', []) if row['id'] == identifier), None)
            if not card:
                raise AccessError('صورة الأمر غير متاحة لهذه المرحلة.')
            if card.get('textile_kit'):
                identifier = card['_textile_source_order_ids'][0]
            record = self.browse(identifier)
            record.check_access('read')
            value = record.with_context(bin_size=False).tailoring_set_image_1920
            if not value:
                return {'uri': None}
            encoded = value.decode() if isinstance(value, bytes) else value
            raw = base64.b64decode(encoded)
            mime = 'image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/png' if raw.startswith(b'\x89PNG') else 'image/webp' if raw.startswith(b'RIFF') else 'image/gif'
            return {'uri': 'data:%s;base64,%s' % (mime, encoded)}
        if operation == 'warnings':
            # Supervisors receive these messages through the system's existing
            # notification service but have no general warning-model read ACL.
            # Expose only their incoming stage messages / own outgoing messages.
            rows = self.env['furniture.mrp.product.production.warning'].sudo().search([
                ('company_id', 'in', self.env.companies.ids), ('active', '=', True),
                '|', ('target_stage', 'in', codes), ('reported_by_id', '=', self.env.uid),
            ], order='id desc', limit=80)
            return [{'id': row.id, 'message': row.message,
                     'product': row.product_id.display_name, 'model': row.model_id.display_name,
                     'stage': row.target_stage, 'source': row.source_stage,
                     'date': row.create_date,
                     'outgoing': row.reported_by_id.id == self.env.uid} for row in rows]
        if operation not in ('action', 'wizard'):
            raise AccessError('العملية غير متاحة في التطبيق.')
        # Serialize commands on this app session and remember their results.
        # Retrying a lost response with the same key cannot repeat a stock move.
        key = data.get('request_id', '')
        if not isinstance(key, str) or not 16 <= len(key) <= 100:
            raise ValidationError('معرّف العملية غير صالح.')
        self.env.cr.execute('SELECT id FROM furniture_supervisor_app_session WHERE id=%s FOR UPDATE', [session.id])
        session.invalidate_recordset()
        digest = hashlib.sha256(json.dumps([operation, data], sort_keys=True).encode()).hexdigest()
        saved = dict(session.command_results or {})
        if key in saved:
            if saved[key]['digest'] != digest:
                raise ValidationError('لا يمكن إعادة استخدام معرّف العملية لطلب مختلف.')
            return saved[key]['result']
        stage = data.get('stage')
        self._stage_dashboard_check_stage_access(stage)
        if operation == 'wizard':
            result = self._supervisor_app_submit_wizard(data, session)
        else:
            result = self._supervisor_app_action(data, session)
        saved[key] = {'digest': digest, 'result': result}
        session.command_results = dict(list(saved.items())[-30:])
        return result

    @api.model
    def _supervisor_app_action(self, data, session):
        stage, action = data.get('stage'), data.get('action')
        if action in ('supply_form', 'supply'):
            if 'furniture.assembly.requisition' not in self.env:
                raise UserError('طلبات تغذية الصالات غير مفعّلة.')
            Request = self.env['furniture.assembly.requisition']
            Request._check_stage_access(stage)
            allowed = Request._recipe_item_values(stage)
            if not allowed:
                raise UserError('لم يحدد مدير الإنتاج خامات مسموحة لك في هذه المرحلة.')
            if action == 'supply_form':
                return {'ok': True, 'form': {
                    'kind': 'supply', 'title': 'طلب تغذية رصيد الصالة',
                    'submit_label': 'إرسال لأمين المخزن',
                    'lines': [{'id': item['product_id'],
                               'name': self.env['product.product'].browse(item['product_id']).display_name,
                               'unit': self.env['uom.uom'].browse(item['product_uom_id']).display_name,
                               'quantity': 0} for item in allowed],
                }}
            submitted = data.get('lines') or []
            quantities = {int(row['id']): app_quantity(row.get('quantity')) for row in submitted}
            if len(quantities) != len(submitted) or set(quantities) != {item['product_id'] for item in allowed}:
                raise AccessError('الخامات المسموحة تغيّرت؛ افتح الطلب من جديد.')
            if any(not 0 <= qty < 1e9 for qty in quantities.values()):
                raise ValidationError('الكميات غير صحيحة.')
            record = Request.create({'stage_code': stage, 'line_ids': [(0, 0, dict(
                item, requested_qty=quantities[item['product_id']])) for item in allowed]})
            record.action_submit()
            return {'ok': True, 'message': 'تم إرسال %s لأمين المخزن.' % record.name}
        elif action == 'receipt':
            kind, _, identifier = str(data.get('request_key', '')).partition(':')
            model = {'request': 'furniture.mrp.store.request',
                     'release': 'furniture.mrp.advance.material.release.stage'}.get(kind)
            if not model or not identifier.isdigit():
                raise AccessError('إذن الخامات غير متاح.')
            record = self.env[model].browse(int(identifier)).exists()
            record.check_access('read')
            if len(record) != 1 or record.stage_code != stage:
                raise AccessError('إذن الخامات غير متاح لهذه المرحلة.')
            record.production_id.check_access('read')
            result = record.action_open_receipt_wizard()
        elif action in ('accept_handoff', 'handoff_quality'):
            # Validate against the current visible handoffs, not caller-supplied IDs.
            dashboard = self.get_stage_dashboard_data(stage_code=stage)
            handoff = next((row for row in dashboard.get('pending_handoffs', [])
                            if row['key'] == data.get('handoff_key')), None)
            if not handoff:
                raise AccessError('التحويل غير متاح لهذا الحساب أو تم استلامه.')
            production = self.browse(handoff['production_id'])
            kwargs = {'stage_handoff_id': handoff.get('handoff_id') if handoff.get('kind') == 'stage' else False}
            if action == 'accept_handoff':
                result = production.action_accept_handoff_transfer(**kwargs)
            else:
                result = production.action_review_handoff_quality(
                    review_key=data.get('review_key'), decision=data.get('decision'), **kwargs)
        elif data.get('batch_token'):
            method = BATCH_METHODS.get(action)
            if not method:
                raise AccessError('العملية غير متاحة.')
            kwargs = {'batch_token': data['batch_token'], 'stage_code': stage}
            if action == 'quality':
                kwargs['decision'] = data.get('decision')
            result = getattr(self, method)(**kwargs)
        else:
            method = ORDER_METHODS.get(action)
            if not method:
                raise AccessError('العملية غير متاحة.')
            identifier = int(data.get('order_id') or 0)
            dashboard = self.get_stage_dashboard_data(stage_code=stage)
            card = next((row for row in dashboard.get('orders', []) if row['id'] == identifier), None)
            if not card:
                raise AccessError('الشغل غير متاح لهذه المرحلة؛ حدّث القائمة.')
            if card.get('textile_kit'):
                if action in ('bom', 'warning'):
                    product = next((row for row in card.get('product_lines', [])
                                    if row['production_line_id'] == int(data.get('line_id') or 0)), None)
                    if not product:
                        raise AccessError('الصنف غير متاح في هذا الطقم.')
                    identifier = product['_textile_source_order_id']
                else:
                    result = self.action_textile_kit_operation(
                        card['textile_kit_token'], stage,
                        'materials' if action == 'receive' else action,
                        line_ids=[int(item) for item in data.get('line_ids', [])],
                        decision=data.get('decision'))
                    return self._supervisor_app_result(result, session, stage)
            record = self.browse(identifier).exists()
            record.check_access('read')
            if len(record) != 1:
                raise AccessError('أمر التشغيل غير متاح.')
            if card.get('textile_loose') and action in ('start', 'finish'):
                field = 'startable_production_line_ids' if action == 'start' else 'quality_production_line_ids'
                line_ids = list({identifier for row in card['product_lines'] for identifier in row.get(field, [])})
                result = record.action_textile_loose_operation(stage, action, line_ids)
                return self._supervisor_app_result(result, session, stage)
            kwargs = {'stage_code': stage}
            if action in ('bom', 'warning'):
                kwargs['production_line_id'] = int(data.get('line_id') or 0)
            if action in ('start', 'quality'):
                kwargs['production_line_ids'] = [int(item) for item in data.get('line_ids', [])]
            if action == 'quality':
                kwargs['decision'] = data.get('decision')
            result = getattr(record, method)(**kwargs)
        return self._supervisor_app_result(result, session, stage)

    @api.model
    def _supervisor_app_result(self, result, session, stage):
        result = result if isinstance(result, dict) else {}
        action = result.get('action') or result
        response = {'ok': True, 'message': result.get('message') or 'تم تنفيذ العملية بنجاح.'}
        if result.get('production_warning'):
            response['warning'] = result['production_warning']
        if action.get('tag') == 'display_notification':
            response['message'] = action.get('params', {}).get('message') or response['message']
        if action.get('type') != 'ir.actions.act_window':
            return response
        model = action.get('res_model') or ''
        if model in ('furniture.mrp.store.request', 'furniture.mrp.advance.material.release.stage'):
            response['message'] = 'تم تسجيل الطلب. تابع حالته من شاشة الخامات.'
            return response
        if model.startswith('furniture.mrp.') and model.rsplit('.', 1)[-1] in dict(FURNITURE_STAGE_DASHBOARD_SELECTION):
            return response  # terminal action after a successful native wizard
        if model not in (BOM, WARNING, FIRST, CARRY, *RECEIPTS):
            raise UserError('هذه العملية تحتاج خطوة إضافية لم تتم تهيئتها في التطبيق؛ لم يتم حفظ أي تغيير.')
        wizard = self.env[model].browse(action.get('res_id')).exists()
        wizard.check_access('read')
        if len(wizard) != 1 or wizard.create_uid.id != self.env.uid:
            raise AccessError('شاشة العملية غير متاحة.')
        grant = secrets.token_urlsafe(24)
        grants = dict(session.wizard_grants or {})
        grants[grant] = {'model': model, 'id': wizard.id, 'stage': stage,
                         'context': action.get('context') if isinstance(action.get('context'), dict) else {}}
        session.wizard_grants = dict(list(grants.items())[-20:])
        form = {'grant': grant, 'title': action.get('name') or 'تفاصيل العملية', 'fields': [], 'lines': []}
        response['form'] = form
        if model == BOM:
            form.update(kind='bom', product=wizard.product_name, model=wizard.model_name,
                        lines=wizard.line_ids.read(['material_name', 'required_qty', 'uom_name']))
        elif model == WARNING:
            form.update(kind='warning', product=wizard.product_id.display_name,
                        targets=[{'id': row.id, 'name': row.display_name} for row in wizard.available_target_stage_ids],
                        target=wizard.target_stage_id.id)
        elif model in (FIRST, CARRY):
            form.update(kind='select', submit_label='إرسال طلب الخامات' if wizard.request_only else 'بدء الأصناف المختارة')
            form['lines'] = [{'id': line.id, 'name': line.product_display_label or line.product_id.display_name,
                              'model': line.furniture_model_id.display_name,
                              'unit': line.product_uom_id.display_name,
                              'quantity': line.qty_to_start, 'selected': line.selected,
                              'available': line.product_qty if model == FIRST else line.qty_in_work_location}
                             for line in wizard.line_ids]
        else:
            form.update(kind='receipt', submit_label='تأكيد الكميات المستلمة',
                        lines=[{'id': line.id, 'name': line.product_id.display_name,
                                'quantity': line.received_qty, 'available': line.issued_qty,
                                'unit': line.product_uom_id.display_name} for line in wizard.line_ids])
        return response

    @api.model
    def _supervisor_app_submit_wizard(self, data, session):
        grants = dict(session.wizard_grants or {})
        key = data.get('grant')
        grant = grants.get(key)
        if not grant or grant['stage'] != data['stage']:
            raise AccessError('انتهت صلاحية شاشة العملية. افتحها مرة أخرى.')
        model = grant['model']
        wizard = self.env[model].with_context(grant['context']).browse(grant['id']).exists()
        wizard.check_access('write')
        if len(wizard) != 1 or wizard.create_uid.id != self.env.uid:
            raise AccessError('شاشة العملية غير متاحة.')
        if model == WARNING:
            target = int(data.get('target') or 0)
            if target not in wizard.available_target_stage_ids.ids:
                raise AccessError('المشرف المستهدف غير متاح لهذه المرحلة.')
            wizard.write({'target_stage_id': target, 'message': str(data.get('message') or '').strip()[:4000]})
            result = wizard.action_send_warning()
        elif model in (FIRST, CARRY, *RECEIPTS):
            lines = {line.id: line for line in wizard.line_ids}
            submitted = data.get('lines') or []
            if {int(row['id']) for row in submitted} != set(lines) or len(submitted) != len(lines):
                raise AccessError('أصناف العملية تغيرت؛ أعد فتح الطلب.')
            for row in submitted:
                quantity = app_quantity(row.get('quantity', 0))
                values = {'received_qty': quantity} if model in RECEIPTS else {
                    'qty_to_start': quantity, 'selected': bool(row.get('selected'))}
                lines[int(row['id'])].write(values)
            if model in RECEIPTS:
                wizard.receipt_note = str(data.get('message') or '')[:4000]
                result = wizard.action_confirm_receipt()
            elif wizard.request_only:
                result = wizard.action_submit_store_request()
            elif model == FIRST:
                result = wizard.action_start_selected_from_stock()
            else:
                result = wizard.action_start_selected_work()
        else:
            raise AccessError('هذه الشاشة للعرض فقط.')
        del grants[key]
        session.wizard_grants = grants
        return self._supervisor_app_result(result, session, data['stage'])

    @api.model
    def _supervisor_app_requests(self, codes, offset):
        offset = max(0, int(offset))
        legacy = self.get_supervisor_mobile_requests(offset)
        rows = [dict(row, key='request:%s' % row['id'], kind='legacy') for row in legacy['rows']]
        for row in rows:
            record = self.env['furniture.mrp.store.request'].browse(row['id'])
            row['can_receive'] = bool(record.state == 'approved' and not record.receipt_confirmed
                                      and record.material_line_ids.mapped('issue_move_ids'))
            row['materials'] = [{'name': line.product_id.display_name,
                                  'unit': line.product_uom_id.display_name,
                                  'requested': line.requested_qty, 'issued': line.issued_qty,
                                  'received': line.received_qty} for line in record.material_line_ids]
        # Restrict technical release ledger reads to productions already visible
        # to the caller and allowed company/stages; return no costs or source IDs.
        productions = self.search([('company_id', 'in', self.env.companies.ids)])
        releases = self.env['furniture.mrp.advance.material.release.stage'].sudo().search([
            ('production_id', 'in', productions.ids), ('stage_code', 'in', codes),
        ], order='id desc', limit=31, offset=offset)
        for row in releases[:30]:
            rows.append({'key': 'release:%s' % row.id, 'kind': 'release',
                         'name': row.release_id.name, 'stage_code': row.stage_code,
                         'state': row.state, 'receipt_state': row.receipt_state,
                         'can_receive': row.state == 'issued' and not row.receipt_confirmed,
                         'requested_at': row.create_date,
                         'materials': [{'name': line.product_id.display_name,
                                        'unit': line.product_uom_id.display_name,
                                        'requested': line.requested_qty,
                                        'issued': line.issued_qty, 'received': line.received_qty}
                                       for line in row.material_line_ids]})
        supplies = []
        if 'furniture.assembly.requisition' in self.env:
            Request = self.env['furniture.assembly.requisition']
            if Request.has_access('read'):
                supplies = Request.search([
                    ('company_id', 'in', self.env.companies.ids), ('stage_code', 'in', codes),
                ], order='id desc', limit=31, offset=offset)
                for row in supplies[:30]:
                    rows.append({'key': 'supply:%s' % row.id, 'kind': 'supply', 'name': row.name,
                                 'stage_code': row.stage_code, 'state': row.state,
                                 'requested_at': row.submitted_at or row.create_date,
                                 'receipt_state': 'تغذية رصيد الصالة',
                                 'materials': [{'name': line.product_id.display_name,
                                                'unit': line.product_uom_id.display_name,
                                                'requested': line.requested_qty,
                                                'issued': line.requested_qty if row.state == 'done' else 0,
                                                'received': line.requested_qty if row.state == 'done' else 0}
                                               for line in row.line_ids if line.requested_qty > 0]})
        return {'rows': rows, 'has_more': legacy['has_more'] or len(releases) > 30 or len(supplies) > 30,
                'next_offset': offset + 30}
