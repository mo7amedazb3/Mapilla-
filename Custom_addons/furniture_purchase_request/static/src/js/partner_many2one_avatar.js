/** @odoo-module **/

import { useEffect, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import {
    PartnerAutoCompleteMany2one,
    partnerAutoCompleteMany2one,
} from "@partner_autocomplete/js/partner_autocomplete_many2one";

export class PartnerMany2OneAvatarField extends PartnerAutoCompleteMany2one {
    static template = "furniture_purchase_request.PartnerMany2OneAvatarField";

    setup() {
        super.setup();
        this.vendorIdentityRef = useRef("vendorIdentity");

        useEffect(
            (identityElement, relation, resId) => {
                const sheet = identityElement?.closest(".o_form_sheet");
                const purchaseForm = sheet?.closest(
                    ".o_form_view.o_furniture_purchase_design"
                );
                if (!purchaseForm || relation !== "res.partner" || !resId) {
                    return;
                }

                const watermarkImage =
                    `url("/web/image/${relation}/${resId}/avatar_512")`;
                sheet.classList.add("o_furniture_has_vendor_watermark");
                sheet.style.setProperty(
                    "--fpr-vendor-watermark-image",
                    watermarkImage
                );

                return () => {
                    if (
                        sheet.style.getPropertyValue(
                            "--fpr-vendor-watermark-image"
                        ) === watermarkImage
                    ) {
                        sheet.classList.remove(
                            "o_furniture_has_vendor_watermark"
                        );
                        sheet.style.removeProperty(
                            "--fpr-vendor-watermark-image"
                        );
                    }
                };
            },
            () => [
                this.vendorIdentityRef.el,
                this.relation,
                this.resId,
            ]
        );
    }
}

export const partnerMany2OneAvatarField = {
    ...partnerAutoCompleteMany2one,
    component: PartnerMany2OneAvatarField,
};

registry
    .category("fields")
    .add("res_partner_many2one_avatar", partnerMany2OneAvatarField);
