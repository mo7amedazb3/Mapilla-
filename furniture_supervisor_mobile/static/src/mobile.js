/** @odoo-module **/
import { Component, onWillStart, xml } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardActionServiceProps } from '@web/webclient/actions/action_service';
class IndependentAppRedirect extends Component {
    static template = xml`<div class="p-5 text-center">جارٍ فتح النظام…</div>`;
    static props = { ...standardActionServiceProps };
    setup() {
        onWillStart(() => window.location.replace('/odoo'));
    }
}
registry.category('actions').add('furniture_supervisor_mobile.app', IndependentAppRedirect);
