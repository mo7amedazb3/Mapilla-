/** @odoo-module **/

import { Component, useState } from '@odoo/owl';
import { Dropdown } from '@web/core/dropdown/dropdown';
import { DropdownItem } from '@web/core/dropdown/dropdown_item';
import { rpc } from '@web/core/network/rpc';
import { useService } from '@web/core/utils/hooks';
import { patch } from '@web/core/utils/patch';
import { browser } from '@web/core/browser/browser';
import { session } from '@web/session';
import { UserMenu } from '@web/webclient/user_menu/user_menu';

export class SwitchUsersMenu extends Component {
    static template = 'Switch_Users.Menu';
    static components = { Dropdown, DropdownItem };
    static props = {};

    setup() {
        this.notification = useService('notification');
        this.state = useState({
            supervisors: [], workers: [], direct_accounts: [], csrf_token: '', busy: false,
            switched: Boolean(session.switch_users?.switched),
            origin_name: session.switch_users?.origin_name || '',
            db: session.db,
        });
    }

    async loadAccounts() {
        try {
            Object.assign(this.state, await rpc('/switch_users/accounts', {}));
        } catch (error) {
            this.state.supervisors = [];
            this.state.workers = [];
            this.state.direct_accounts = [];
            this.notification.add(error.data?.message || 'تعذّر تحميل الحسابات. حدّث الصفحة وحاول تاني.', { type: 'danger' });
        }
    }

    async switchAccount(userId, restore = false) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            // Refresh authorization and CSRF immediately before the mutation.
            const data = await rpc('/switch_users/accounts', {});
            const route = restore ? '/switch_users/restore' : '/switch_users/switch';
            const params = { csrf_token: data.csrf_token };
            if (!restore) params.user_id = userId;
            const result = await rpc(route, params);
            // A fresh web client drops all old menus, permissions, action history,
            // and company context. The server session keeps the same database.
            browser.location.assign(result.redirect);
        } catch (error) {
            this.state.busy = false;
            this.notification.add(error.data?.message || 'تعذّر تبديل الحساب. حاول تاني.', { type: 'danger' });
        }
    }
}

patch(UserMenu, { components: { ...UserMenu.components, SwitchUsersMenu } });
patch(UserMenu.prototype, {
    get switchUsersEnabled() { return Boolean(session.switch_users?.enabled); },
    get switchUsersActive() { return Boolean(session.switch_users?.switched); },
});
