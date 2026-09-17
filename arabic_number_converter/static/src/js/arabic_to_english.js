/** @odoo-module **/

import { browser } from "@web/core/browser/browser";

const arabicNumbers = ['٠', '١', '٢', '٣', '٤', '٥', '٦', '٧', '٨', '٩'];

function convertArabicToEnglish(str) {
    if (typeof str !== 'string') return str;
    return str.replace(/[٠-٩]/g, function (d) {
        return arabicNumbers.indexOf(d).toString();
    });
}

browser.addEventListener("input", function (e) {
    const target = e.target;
    // Check if it's an input or textarea
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
        const val = target.value;
        if (/[٠-٩]/.test(val)) {
            // Save cursor position to avoid jumping to end
            let cursorPosition = null;
            try {
                cursorPosition = target.selectionStart;
            } catch (err) {
                // Some input types (like number) don't support selectionStart
            }

            target.value = convertArabicToEnglish(val);

            try {
                if (cursorPosition !== null && (target.type === "text" || target.type === "search" || target.tagName === "TEXTAREA")) {
                    target.setSelectionRange(cursorPosition, cursorPosition);
                }
            } catch (err) {
                // Ignore errors related to setting selection range
            }

            // Dispatch input event so Odoo's OWL reactivity picks it up
            target.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }
});
