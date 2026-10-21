# Import and reconcile an external order

## Sub-features

- Record an external source and order identity once.
- Refuse a changed payload under an existing identity.
- Create and confirm a Tryton sale from the accepted payload.
- Replay a synthetic fulfillment receipt after its acknowledgment is lost.
- Cancel an eligible task-owned quotation or route later states for manual review.
- Compare the source total with the real Tryton sale total.

## How to get to it (user POV)

In the Tryton client, open Sales, then Order Imports. External integrations call the `orderweave.import.receive` XML-RPC method with a company, party, currency, expected total, and sale lines. Use Tryton record IDs and decimal strings in the payload.

## Driving it with XML-RPC

Run `.venv/Scripts/python.exe scripts/verify_orderweave.py launch`, then `doctor`, then `drive`. The driver creates synthetic company, customer, and product records and exercises the same authenticated XML-RPC model methods used by an integration client. Custom XML-RPC methods include Tryton's context dictionary as their final argument.

## Gotchas

- The receiver database is a synthetic, single-writer SQLite ledger. It does not send a real shipment or contact an external service.
- SQLite gives this verifier serialized single-process writes. The unique source and external-order constraint prevents duplicate import rows, but SQLite does not establish production concurrency guarantees.
- Product and company IDs belong to the verifier database. Do not reuse them in another database.
- A confirmed sale cannot be compensated automatically. The import moves to manual review and leaves the sale intact.
