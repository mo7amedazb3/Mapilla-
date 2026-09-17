---
type: "query"
date: "2026-08-08T15:57:59.947977+00:00"
question: "افحص قاعدة yasser3 قراءة فقط: عدد وأسماء hr.employee الحاليين، المستخدمين المرتبطين، العقود وأي موديل عقد مفتوح 30000 وساعات/تقويم الجمعة، الأقسام والمراحل، وأهم FK/سجلات حضور أو رواتب التي تمنع الحذف"
contributor: "graphify"
outcome: "dead_end"
source_nodes: ["Calendar Schedule", "Time Tracking"]
---

# Q: افحص قاعدة yasser3 قراءة فقط: عدد وأسماء hr.employee الحاليين، المستخدمين المرتبطين، العقود وأي موديل عقد مفتوح 30000 وساعات/تقويم الجمعة، الأقسام والمراحل، وأهم FK/سجلات حضور أو رواتب التي تمنع الحذف

## Answer

Expanded from original query via vocab: [employee, user, users, contract, attendance, calendar, worker, stage, stages]. Graph traversal returned only static description icon nodes (Calendar Schedule, Time Tracking), so it did not contain useful HR model relationships. Live read-only PostgreSQL inspection and direct source inspection were required.

## Outcome

- Signal: dead_end

## Source Nodes

- Calendar Schedule
- Time Tracking