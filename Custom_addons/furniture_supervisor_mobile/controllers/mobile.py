from urllib.parse import urlencode

from odoo import http
from odoo.http import request


class SupervisorMobile(http.Controller):
    @http.route('/supervisor', type='http', auth='none', methods=['GET'])
    def entry(self, **kwargs):
        # Preserve existing bookmarks while keeping the new login independent.
        return request.redirect_query('/supervisor-app', query={
            'db': kwargs.get('db') or request.db or 'yasser3',
        })

    @http.route('/odoo/supervisors', type='http', auth='none', methods=['GET'])
    def legacy_erp_entry(self, **kwargs):
        return request.redirect_query('/odoo', query={
            'db': kwargs.get('db') or request.db or 'yasser3',
        })

    @http.route('/supervisor/manifest.webmanifest', type='http', auth='public',
                methods=['GET'], readonly=True)
    def manifest(self):
        return request.make_json_response({
            'id': '/odoo/supervisors',
            'name': 'مابيلا | مشرفي المصنع',
            'short_name': 'المشرفين',
            'description': 'التشغيل والخامات والجودة ومتابعة مراحل المصنع',
            'lang': 'ar', 'dir': 'rtl',
            'start_url': '/odoo/supervisors?' + urlencode({'db': request.db}),
            'scope': '/odoo',
            'display': 'standalone',
            'background_color': '#f7f5f2',
            'theme_color': '#0c2a44',
            'prefer_related_applications': False,
            'icons': [{
                'src': '/furniture_supervisor_mobile/static/img/icon-%s.png' % size,
                'sizes': '%sx%s' % (size, size),
                'type': 'image/png', 'purpose': 'any maskable',
            } for size in (192, 512)],
        }, headers={'Content-Type': 'application/manifest+json',
                    'Cache-Control': 'no-cache'})
