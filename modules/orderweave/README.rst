Orderweave
==========

Orderweave imports external orders into Tryton sales and records a separate
synthetic fulfillment receipt. It keeps a stable identity for each source order,
refuses changed payloads under the same identity, and allows a receipt request
to be replayed after its acknowledgment is lost.

This module targets Tryton 8.0.12. It uses a local SQLite receipt ledger for
the demonstration. Set ``ORDERWEAVE_RECEIVER_DB`` to its path. This is a
single-writer demonstration receiver, not a production fulfillment service.

The RPC data shape is a JSON object with ``company``, ``party``, ``currency``,
``expected_total``, and ``lines``. Each line has ``product``, ``quantity``, and
``unit_price``. IDs are Tryton record IDs. Decimal values are strings, such as
``"2.00"``. Custom RPC methods receive Tryton's context dictionary as the final
argument. Call ``orderweave.import.receive`` with source, external order ID,
payload, optional ``simulate_lost_ack``, and context. It returns an import ID.
Advance the returned order with the model button ``advance`` and reconcile its
Tryton sale total with ``reconcile``. Pass ``true`` as
``simulate_lost_ack`` to exercise a persisted receipt whose acknowledgment did
not reach Tryton.
