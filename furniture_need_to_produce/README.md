# النواقص / Need to Produce

Opt-in Odoo 18 manufacturing approval workflow. Install this addon only on the
databases selected for the new workflow; the existing `furniture_mrp` helper
defaults preserve the legacy routes in databases without it.

## Manager workflow

1. Open **النواقص**, then choose **نواقص المنتج التام** for finished goods or
   **نواقص المراحل** for department buffers, then **تحديث**. Both use the
   existing shared allocator; opening either menu is read-only and never
   changes Min/Max policies.
2. In stage shortages select the destination department, then the model and
   product. Each action scopes both pending and approved pieces to that exact
   buffer lane (the parallel bases/preparation pair shares the same pieces);
   finished and stage approval selections cannot mix.
3. Open a piece to see its exact missing route, available/incoming input sources,
   working hours and ten-hour working days. Parallel branches share elapsed time.
4. Open a stage's **مراجعة الخامات**. Edits, new rows, deletions and intentionally
   empty material lists are snapshots for this piece/stage only, not master-BoM
   changes. Save before approving.
5. **Approve** creates and confirms the required orders. If stock, existing
   orders or the demand target changed, approval requires refreshing/reviewing
   the new plan instead of silently changing its route.

### Stage shortages navigation

The stage-shortages menu inside **النواقص** reuses the same model cards, piece cards, customer metadata,
BoM editor, timing, partial ribbons and one top approval button. Routes end at
the selected stage, skipping stock-covered producers as before. Independent
tailoring/painting pieces show a compact BoM row without a dependency diagram.
Min/Max's review shortcut opens the workspace for its selected tab; both live
inside the same shortages app. The existing app still opens finished goods by
default, and the two child menus switch directly between finished and stage shortages.

Carpentry's existing Min/Max controls **both priming and assembly** (`frame`).
This UI split does not create a separate priming stock buffer or split existing
production orders. Bases and preparation retain their separate policies.

### Compatible bulk approval

Enable `furniture_need_to_produce.group_compatible_approvals=True` only on the
databases selected for grouped approval. A single approval request then creates
one quantity-aggregated order per lane for pieces of the same product, model,
BoM, dimensions, target policy, complete remaining route and material snapshot.
Different products, routes or materials stay separate. Existing approved orders
are never merged or enlarged by a later approval.

BoM-edited pieces are automatically **Custom** and always get separate orders.
Their **المشتري / المستهلك** strip opens a searchable contact picker before
approval (buyer companies, consumer contacts). Choices are optional, stored in
the piece's `preview_json.custom_approval` metadata, preserved by preview refresh,
and copied to both the header and product line of every newly generated stage
order. Normal pieces do not display the picker or inherit another piece's
contacts. The manager-only save action rechecks draft/Custom status, company,
record access and active contacts; approval validates the contacts again. It
does not approve the piece or change its route, materials or stock allocations.

The original one-piece stages, input claims and source links remain intact:
several stages may point to the same order and its single aggregate product
line. Material quantities scale from their identical per-piece snapshots;
handoff validation covers **all** linked claims before supervisor acceptance.
Pending or partial receipts do not count as accepted transfers. The route graph
still checks off each piece according to that piece's actual consumed claims.

Installing the addon changes Min/Max runs into **preview generation**, not order
generation. Automatic legacy downstream-order creation is also replaced by
releasing inputs for already-approved piece routes. Review existing in-flight
legacy orders before activating the addon on a production database.

The standalone app uses the existing menu/action IDs and a transparent SVG icon.
It remains connected to MRP II; manufacturing-manager permissions, Min/Max
shortcuts, pending pieces, BoM edits and approval behavior are unchanged. Moving
the menu does not install a second workflow or copy manufacturing records.

## Routes and stock

### Single-receipt parallel cycle

With `furniture_need_to_produce.parallel_finish_v1=True`, new previews and
approvals keep priming/assembly together, then create **one** bases + finishing
order. A single accepted carpentry handoff enters the finishing hall. Both
departments become startable, in either order, only after this physical receipt.
Bases consumes its own raw materials and records labor/quality but does not
create another whole-piece stock balance or require a second carpentry transfer.

Finishing quality/output requires accepted bases quality for the **same technical
line/batch**. Only then does the single body move from the finishing hall into
ready finishing stock, which upholstery can receive through its normal handoff.
Starting/completing bases alone never creates a ready finishing output.

Both department Min/Max values are preserved. Their shared supply fulfills the
greatest uncovered target once, not the sum of two targets for the same units.
Both shortage tabs show the same pair's pending pieces. The diagram forks after
assembly and joins before upholstery; elapsed time uses the longest parallel
operation, while material and labor totals include both. Custom operation
snapshots (including empty BoMs) and customer metadata survive draft migration.

Only a newly linked `furniture.need.to.produce.stage` with lane `finish` opts a
production order into this behavior. Approved historical plans and their stock,
quality, cost and handoff ledgers keep the old cycle. Turning off the feature
stops new parallel previews; it intentionally does not rewrite approved orders.

### Historical cycle (feature disabled / already approved)

* Frame: introduction (`priming`) → assembly (`carpentry`).
* Bases: its own order, consuming ready frame output.
* Preparation: its own order, consuming ready bases output. Its output continues
  to use the existing `finish` stock identity for compatibility.
* Upholstery: preparation + tailoring; painting is not an upholstery start gate.
* Packaging: upholstery + painting, preserving upholstery quality approval and
  destination-supervisor acceptance before starting.

Ready output and unclaimed existing production cover demand before any new order
is planned. Draft previews reserve planning capacity only; approval creates real
stock reservations. Exact source-line/output claims prevent the same stock or
incoming production from covering two pieces. Partial incoming receipts are
reserved incrementally; cancelled handoff history is retained.

Bases and preparation have independent Min/Max controls. Final-product demand
can require upstream production above the department buffer's own Max. The Max
is a replenishment target, not a production cap.

Configure bases in **MRP II → Min/Max → مراحل الإنتاج → القواعد**, alongside
the other stages. The redundant standalone bases Min/Max shortcut is inactive;
its existing action and policy records are preserved.

## Supervisor view

**مخطط للإنتاج** excludes completed work. Confirmed future work can be visible
as waiting, without granting operational actions before the physical predecessor
arrives. **مكتمل** shows completed stage lines, including closed parent orders.
The existing stock issue, receipt, timing and quality permissions remain active.

## Temporary timing

Two working hours per piece **per actual operation**, ten hours per working day.
Frame therefore has four hours total (two per operation). Displayed elapsed work
uses the longest dependent branch, not the sum of parallel work. Unknown waiting
time for an existing order is explicitly marked; these are not MPS delivery
dates and do not include queues, holidays or workforce-capacity scheduling.

## Verification and rollout

Use a disposable, neutralized clone with external mail/device integrations and
crons disabled. Pure allocation tests:

```
python3 -m unittest discover -s furniture_need_to_produce/tests/unit -v
```

Run Odoo post-install tests using `--test-tags /furniture_need_to_produce` and a
dedicated local HTTP port. Integration tests use transactional isolated fixtures;
never use them as a script to seed or approve live manufacturing orders.

Before installing on a selected real database, back up both its database and
filestore and preserve the shared addons. Once new-lane records exist, uninstall
is deliberately restricted to avoid silently converting their manufacturing
identity. Restore the verified pre-rollout backup for a full rollback.
# Custom pieces

Editing any piece-specific stage BoM automatically marks the piece **Custom**.
Each Custom piece always gets separate orders, even if another piece has the
same modified recipe. Custom is a read-only badge shown only for BoM-modified
pieces; there is no manual toggle. Approval is available only in the upper
model/product summary, not per piece or in the selection toolbar. Piece checkboxes
and the page-scoped selection counter are available; selecting pieces changes
that same upper button to "Approve المحدد" and approves only those selected IDs.
With no selection it approves the entire product (or model at the model level).
BoM provenance survives preview refreshes. Existing approved orders are not split.

The `custom_approval` member of `preview_json` stores sticky BoM provenance;
legacy manual metadata is preserved for audit but does not classify a piece.
the planner snapshot is still compared in full except for that metadata member.
`is_custom` and `custom_bom_modified` are non-stored presentation fields, so shared
databases do not require schema changes to load the addon code.
