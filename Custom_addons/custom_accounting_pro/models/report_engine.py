import io
import base64
import xlsxwriter
from odoo import models, api

class DynamicReportEngine(models.AbstractModel):
    _name = 'report.custom_accounting_pro.engine'
    _description = 'Dynamic Report Engine'

    def _get_domain(self, base_domain, date_from, date_to):
        domain = base_domain.copy()
        if date_from:
            domain.append(('date', '>=', date_from))
        if date_to:
            domain.append(('date', '<=', date_to))
        return domain

    @api.model
    def get_gl_data(self, date_from=None, date_to=None):
        domain = self._get_domain([('parent_state', '=', 'posted'), ('display_type', 'not in', ('line_section', 'line_note'))], date_from, date_to)
        lines = self.env['account.move.line'].search_read(domain, ['account_id', 'date', 'journal_id', 'partner_id', 'name', 'debit', 'credit', 'balance'])
        
        accounts_data = {}
        for line in lines:
            if not line['account_id']: continue
            acc_id = line['account_id'][0]
            if acc_id not in accounts_data:
                accounts_data[acc_id] = {
                    'account_id': acc_id, 'code': '', 'name': line['account_id'][1],
                    'debit': 0.0, 'credit': 0.0, 'balance': 0.0, 'lines': []
                }
            accounts_data[acc_id]['debit'] += line['debit']
            accounts_data[acc_id]['credit'] += line['credit']
            accounts_data[acc_id]['balance'] += line['balance']
            accounts_data[acc_id]['lines'].append({
                'id': line['id'],
                'date': line['date'].strftime('%Y-%m-%d') if line['date'] else '',
                'journal': line['journal_id'][1] if line['journal_id'] else '',
                'partner': line['partner_id'][1] if line['partner_id'] else '',
                'label': line['name'] or '',
                'debit': line['debit'], 'credit': line['credit'], 'balance': line['balance']
            })
            
        if accounts_data:
            accounts = self.env['account.account'].search_read([('id', 'in', list(accounts_data.keys()))], ['code'])
            code_map = {a['id']: a['code'] for a in accounts}
            for acc_id, data in accounts_data.items():
                data['code'] = code_map.get(acc_id, '')
                
        return sorted(list(accounts_data.values()), key=lambda x: x['code'])

    @api.model
    def get_pl_data(self, date_from=None, date_to=None):
        domain = self._get_domain([
            ('parent_state', '=', 'posted'), 
            ('display_type', 'not in', ('line_section', 'line_note')),
            '|', ('account_id.account_type', '=like', 'income%'), ('account_id.account_type', '=like', 'expense%')
        ], date_from, date_to)
        groups = self.env['account.move.line'].read_group(domain, ['account_id', 'balance:sum'], ['account_id'])
        
        result = []
        if groups:
            acc_ids = [g['account_id'][0] for g in groups if g['account_id']]
            accounts = self.env['account.account'].search_read([('id', 'in', acc_ids)], ['code', 'name', 'account_type'])
            acc_map = {a['id']: a for a in accounts}
            
            for g in groups:
                if not g['account_id']: continue
                acc = acc_map.get(g['account_id'][0])
                if acc:
                    result.append({
                        'account_id': acc['id'], 'code': acc['code'], 'name': acc['name'],
                        'account_type': acc['account_type'], 'balance': g['balance']
                    })
        return sorted(result, key=lambda x: x['code'])

    @api.model
    def get_partner_ledger_data(self, date_from=None, date_to=None):
        domain = self._get_domain([
            ('parent_state', '=', 'posted'), 
            ('display_type', 'not in', ('line_section', 'line_note')),
            ('account_id.account_type', 'in', ['asset_receivable', 'liability_payable'])
        ], date_from, date_to)
        lines = self.env['account.move.line'].search_read(domain, ['partner_id', 'date', 'journal_id', 'name', 'debit', 'credit', 'balance'])
        
        partners_data = {}
        for line in lines:
            if not line['partner_id']: continue
            partner_id = line['partner_id'][0]
            if partner_id not in partners_data:
                partners_data[partner_id] = {
                    'partner_id': partner_id, 'name': line['partner_id'][1],
                    'debit': 0.0, 'credit': 0.0, 'balance': 0.0, 'lines': []
                }
            partners_data[partner_id]['debit'] += line['debit']
            partners_data[partner_id]['credit'] += line['credit']
            partners_data[partner_id]['balance'] += line['balance']
            partners_data[partner_id]['lines'].append({
                'id': line['id'],
                'date': line['date'].strftime('%Y-%m-%d') if line['date'] else '',
                'journal': line['journal_id'][1] if line['journal_id'] else '',
                'label': line['name'] or '',
                'debit': line['debit'], 'credit': line['credit'], 'balance': line['balance']
            })
                
        return sorted(list(partners_data.values()), key=lambda x: x['name'])

    @api.model
    def get_trial_balance_data(self, date_from=None, date_to=None):
        domain = self._get_domain([('parent_state', '=', 'posted'), ('display_type', 'not in', ('line_section', 'line_note'))], date_from, date_to)
        groups = self.env['account.move.line'].read_group(domain, ['account_id', 'debit:sum', 'credit:sum', 'balance:sum'], ['account_id'])
        
        result = []
        if groups:
            acc_ids = [g['account_id'][0] for g in groups if g['account_id']]
            accounts = self.env['account.account'].search_read([('id', 'in', acc_ids)], ['code', 'name'])
            acc_map = {a['id']: a for a in accounts}
            
            for g in groups:
                if not g['account_id']: continue
                acc = acc_map.get(g['account_id'][0])
                if acc:
                    result.append({
                        'account_id': acc['id'], 'code': acc['code'], 'name': acc['name'],
                        'debit': g['debit'], 'credit': g['credit'], 'balance': g['balance']
                    })
        return sorted(result, key=lambda x: x['code'])

    def _get_data_for_report(self, report_type, date_from, date_to):
        if report_type == 'gl':
            return self.get_gl_data(date_from, date_to)
        elif report_type == 'pl':
            return self.get_pl_data(date_from, date_to)
        elif report_type == 'partner_ledger':
            return self.get_partner_ledger_data(date_from, date_to)
        elif report_type == 'trial_balance':
            return self.get_trial_balance_data(date_from, date_to)
        return []

    @api.model
    def export_excel(self, report_type, date_from=None, date_to=None, selected_keys=None):
        data = self._get_data_for_report(report_type, date_from, date_to)
        if selected_keys:
            key_name = 'partner_id' if report_type == 'partner_ledger' else 'account_id'
            data = [d for d in data if d.get(key_name) in selected_keys]

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet('Report')
        bold = workbook.add_format({'bold': True, 'bg_color': '#f1f1f1'})
        
        headers = ['Code/Partner', 'Name', 'Debit', 'Credit', 'Balance']
        for col, h in enumerate(headers):
            sheet.write(0, col, h, bold)
            
        row = 1
        for item in data:
            sheet.write(row, 0, item.get('code') or item.get('name') or '')
            sheet.write(row, 1, item.get('name') or '')
            sheet.write(row, 2, item.get('debit', 0))
            sheet.write(row, 3, item.get('credit', 0))
            sheet.write(row, 4, item.get('balance', 0))
            row += 1
            
            if 'lines' in item and item['lines']:
                sheet.write(row, 0, 'Date', bold)
                sheet.write(row, 1, 'Label/Journal', bold)
                sheet.write(row, 2, 'Debit', bold)
                sheet.write(row, 3, 'Credit', bold)
                sheet.write(row, 4, 'Balance', bold)
                row += 1
                for line in item['lines']:
                    sheet.write(row, 0, line.get('date', ''))
                    sheet.write(row, 1, f"{line.get('journal', '')} - {line.get('label', '')}")
                    sheet.write(row, 2, line.get('debit', 0))
                    sheet.write(row, 3, line.get('credit', 0))
                    sheet.write(row, 4, line.get('balance', 0))
                    row += 1
        
        workbook.close()
        output.seek(0)
        b64_data = base64.b64encode(output.read())
        attachment = self.env['ir.attachment'].create({
            'name': f"{report_type}_report.xlsx",
            'type': 'binary',
            'datas': b64_data,
            'res_model': 'report.custom_accounting_pro.engine',
            'res_id': 0,
        })
        return attachment.id

    @api.model
    def export_pdf(self, report_type, date_from=None, date_to=None, selected_keys=None):
        data = self._get_data_for_report(report_type, date_from, date_to)
        if selected_keys:
            key_name = 'partner_id' if report_type == 'partner_ledger' else 'account_id'
            data = [d for d in data if d.get(key_name) in selected_keys]

        # Use Odoo's native QWeb to PDF renderer to avoid wkhtmltopdf freezes
        pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
            'custom_accounting_pro.action_report_dynamic_pdf', 
            res_ids=[], 
            data={'data': data, 'report_type': report_type}
        )
        
        b64_data = base64.b64encode(pdf_content)
        attachment = self.env['ir.attachment'].create({
            'name': f"{report_type}_report.pdf",
            'type': 'binary',
            'datas': b64_data,
            'res_model': 'report.custom_accounting_pro.engine',
            'res_id': 0,
        })
        return attachment.id
