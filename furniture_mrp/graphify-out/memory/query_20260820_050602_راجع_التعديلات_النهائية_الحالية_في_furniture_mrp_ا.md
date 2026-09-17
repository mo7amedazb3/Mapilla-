---
type: "query"
date: "2026-08-20T05:06:02.551778+00:00"
question: "راجع التعديلات النهائية الحالية في furniture_mrp الخاصة بجدولة MPS مراجعة static فقط (لا تشغّل Odoo ولا تعدّل DB أثناء الاختبارات). ابحث عن أخطاء حرجة في الحساب، الاعتماديات، التوازي، worker locking، أو lifecycle. أبلغني بالملاحظات فقط ولا تعدّل الملفات."
contributor: "graphify"
outcome: "dead_end"
source_nodes: ["Calendar Schedule", "Production Scheduling Icon"]
---

# Q: راجع التعديلات النهائية الحالية في furniture_mrp الخاصة بجدولة MPS مراجعة static فقط (لا تشغّل Odoo ولا تعدّل DB أثناء الاختبارات). ابحث عن أخطاء حرجة في الحساب، الاعتماديات، التوازي، worker locking، أو lifecycle. أبلغني بالملاحظات فقط ولا تعدّل الملفات.

## Answer

Expanded from original query via graph vocab: [mps, schedule, worker, production, calendar, contract, employee, operation, plan, stage, lifecycle, work]. Existing graph traversal returned only static icon concepts and was stale for the current MPS implementation; direct source review was required. Findings were reported from models/mrp_mps_schedule.py, models/mrp_production_order.py, models/mrp_production_line.py, and models/mrp_stage_mixin.py.

## Outcome

- Signal: dead_end

## Source Nodes

- Calendar Schedule
- Production Scheduling Icon