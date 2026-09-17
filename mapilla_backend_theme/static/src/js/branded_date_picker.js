/** @odoo-module **/

import { registry } from "@web/core/registry";

const MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];
const WEEK_DAYS = ["السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"];

const pad = (value) => String(value).padStart(2, "0");
const isoDate = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const arabicNumber = (value) => new Intl.NumberFormat("ar-EG", { useGrouping: false }).format(value);

function parseIso(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
}

function button(icon, label, className = "") {
    const element = document.createElement("button");
    element.type = "button";
    element.className = className;
    element.setAttribute("aria-label", label);
    element.innerHTML = `<i class="fa ${icon}" aria-hidden="true"></i>`;
    return element;
}

const brandedDatePickerService = {
    start() {
        let activeInput = null;
        let panel = null;
        let viewYear = 0;
        let viewMonth = 0;

        const isAvailable = (input) => input && !input.disabled && !input.readOnly;
        const withinLimits = (input, value) => (
            (!input.min || value >= input.min) && (!input.max || value <= input.max)
        );

        function close() {
            panel?.remove();
            panel = null;
            activeInput?.setAttribute("aria-expanded", "false");
            activeInput = null;
        }

        function position() {
            if (!panel || !activeInput?.isConnected) return close();
            const rect = activeInput.getBoundingClientRect();
            const width = Math.min(410, window.innerWidth - 24);
            panel.style.width = `${width}px`;
            const height = panel.offsetHeight || 510;
            const below = window.innerHeight - rect.bottom;
            const top = below >= Math.min(height, 480) || rect.top < below
                ? rect.bottom + 10
                : Math.max(12, rect.top - height - 10);
            const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
            panel.style.top = `${Math.max(12, top)}px`;
            panel.style.left = `${left}px`;
        }

        function select(value) {
            if (!activeInput || !withinLimits(activeInput, value)) return;
            activeInput.value = value;
            activeInput.dispatchEvent(new Event("input", { bubbles: true }));
            activeInput.dispatchEvent(new Event("change", { bubbles: true }));
            activeInput.focus({ preventScroll: true });
            close();
        }

        function render() {
            if (!panel || !activeInput) return;
            const selected = activeInput.value;
            const today = isoDate(new Date());
            panel.replaceChildren();

            const header = document.createElement("header");
            const next = button("fa-angle-right", "الشهر التالي");
            const previous = button("fa-angle-left", "الشهر السابق");
            const title = document.createElement("div");
            title.innerHTML = `<span>اختر التاريخ</span><strong>${MONTHS[viewMonth]} ${arabicNumber(viewYear)}</strong>`;
            next.addEventListener("click", () => {
                const date = new Date(viewYear, viewMonth + 1, 1);
                viewYear = date.getFullYear(); viewMonth = date.getMonth(); render(); position();
            });
            previous.addEventListener("click", () => {
                const date = new Date(viewYear, viewMonth - 1, 1);
                viewYear = date.getFullYear(); viewMonth = date.getMonth(); render(); position();
            });
            header.append(next, title, previous);

            const weekdays = document.createElement("div");
            weekdays.className = "o_mapilla_calendar_weekdays";
            for (const weekday of WEEK_DAYS) {
                const cell = document.createElement("span"); cell.textContent = weekday; weekdays.append(cell);
            }

            const days = document.createElement("div");
            days.className = "o_mapilla_calendar_days";
            const first = new Date(viewYear, viewMonth, 1);
            const leading = (first.getDay() + 1) % 7;
            const gridStart = new Date(viewYear, viewMonth, 1 - leading);
            for (let index = 0; index < 42; index++) {
                const date = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + index);
                const value = isoDate(date);
                const day = document.createElement("button");
                day.type = "button";
                day.className = "o_mapilla_calendar_day";
                if (date.getMonth() !== viewMonth) day.classList.add("is-other-month");
                if (value === selected) day.classList.add("is-selected");
                if (value === today) day.classList.add("is-today");
                day.disabled = !withinLimits(activeInput, value);
                day.setAttribute("aria-label", value);
                day.innerHTML = `<span>${arabicNumber(date.getDate())}</span>${value === today ? "<small>اليوم</small>" : ""}`;
                day.addEventListener("click", () => select(value));
                days.append(day);
            }

            const footer = document.createElement("footer");
            const hint = document.createElement("span");
            hint.innerHTML = '<i class="fa fa-calendar-check-o" aria-hidden="true"></i> اختر التاريخ المطلوب';
            const todayButton = document.createElement("button");
            todayButton.type = "button"; todayButton.textContent = "العودة لليوم";
            todayButton.disabled = !withinLimits(activeInput, today);
            todayButton.addEventListener("click", () => select(today));
            footer.append(hint, todayButton);
            panel.append(header, weekdays, days, footer);
        }

        function open(input) {
            if (!isAvailable(input)) return;
            if (activeInput === input && panel) return;
            close();
            activeInput = input;
            const selected = parseIso(input.value) || parseIso(input.min) || new Date();
            viewYear = selected.getFullYear();
            viewMonth = selected.getMonth();
            panel = document.createElement("section");
            panel.className = "o_mapilla_global_calendar";
            panel.dir = "rtl";
            panel.setAttribute("role", "dialog");
            panel.setAttribute("aria-label", "اختيار التاريخ");
            panel.addEventListener("pointerdown", (event) => event.stopPropagation());
            document.body.append(panel);
            input.setAttribute("aria-haspopup", "dialog");
            input.setAttribute("aria-expanded", "true");
            render();
            requestAnimationFrame(position);
        }

        document.addEventListener("pointerdown", (event) => {
            const input = event.target.closest?.('input[type="date"]');
            if (isAvailable(input)) {
                event.preventDefault();
                open(input);
            } else if (panel && !panel.contains(event.target)) {
                close();
            }
        }, true);
        document.addEventListener("click", (event) => {
            const input = event.target.closest?.('input[type="date"]');
            if (isAvailable(input)) event.preventDefault();
        }, true);
        document.addEventListener("keydown", (event) => {
            const input = event.target.closest?.('input[type="date"]');
            if (isAvailable(input) && (event.key === "Enter" || event.key === " " || (event.altKey && event.key === "ArrowDown"))) {
                event.preventDefault(); open(input);
            } else if (event.key === "Escape" && panel) {
                event.preventDefault(); const target = activeInput; close(); target?.focus({ preventScroll: true });
            }
        }, true);
        window.addEventListener("resize", position);
        document.addEventListener("scroll", position, true);
        return { open, close };
    },
};

registry.category("services").add("mapilla_branded_date_picker", brandedDatePickerService);
