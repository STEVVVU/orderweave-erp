# Orderweave

Orderweave is a small Tryton 8.0 module for importing external sales orders with explicit, recoverable checkpoints. It uses real Tryton parties, products, companies, and `sale.sale` records. A source plus external order ID is unique; reusing that identity with a changed payload is rejected. The module advances an order through receipt, quotation, confirmation, and completion, and routes unsafe cancellation to manual review.

The workflow borrows the bounded transaction and compensation idea from the [Sagas paper](https://www.cs.cornell.edu/andru/cs711/2002fa/reading/sagas.pdf). It is a focused teaching/demo implementation, not a complete Sagas implementation or production integration. The educational context includes the [VU BCO5651 public ERP project outline](https://www.vu.edu.au/units/enterprise-resource-planning-systems-implementation-bco5651); that course outline is adjacent assessment context, not the technical specification for this module.

## Recovery demonstration

The verifier imports a synthetic order into Tryton, advances a real sale, and delivers a synthetic fulfillment receipt to a separate SQLite ledger. It can persist the receipt while losing its acknowledgment, stop and restart Tryton, replay the request idempotently, and reconcile the source amount against the sale total. An eligible quotation can be cancelled. Once the sale is confirmed or a receipt exists, compensation instead records a manual-review state and leaves the irreversible work intact.

The receiver is a local, single-writer SQLite side-effect simulator. It does not contact a marketplace, ship goods, send messages, or move money. SQLite here proves a bounded demo path; it does not establish production concurrency or authorization guarantees. Tryton's normal production database is PostgreSQL.

## Development and verification

This repository is the complete upstream Tryton monorepo at tag `trytond-8.0.12`, commit `05c8430b9f479cf17d3fd9113cc60db7a94663bf`, with the module added under `modules/orderweave/`. Upstream files, licenses, and history are preserved. See the [Tryton 8.0 server documentation](https://docs.tryton.org/8.0/server/).

On Windows with Python 3.11, from the repository root:

```powershell
python scripts/bootstrap_orderweave.py
$env:TZ = 'UTC'
.\.venv\Scripts\python.exe -m unittest trytond.modules.orderweave.tests.test_module
.\.venv\Scripts\python.exe scripts/verify_orderweave.py run
```

The bootstrap uses the third-party versions pinned in `requirements-orderweave.lock.txt` and builds Tryton, Proteus, module dependencies, and Orderweave from this checkout. The full verifier covers Launch, Doctor, Drive, Evidence, and Cleanup. It binds only to `127.0.0.1:18080`, creates synthetic records in a private database under `.tooling/orderweave/`, and preserves its RPC proof in `.audit/evidence/` after cleanup. The reusable driver and feature map are documented in [the verification skill](.agents/skills/verify-orderweave/SKILL.md).

The inherited Sao web client builds with its documented Grunt path. Install local dependencies without lifecycle scripts, then build:

```powershell
cd sao
npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund
npx --no-install grunt
cd ..
```

Run `scripts/verify_orderweave.py launch` with the local Python environment, then open `http://127.0.0.1:18080/`. The synthetic administrator password is in ignored `.tooling/orderweave/admin-password.txt`. The driver’s Doctor checks exact HTTP bytes for the built index, JavaScript, and CSS when present. Run `cleanup` afterward. Build and asset delivery do not establish rendered-browser acceptance.

The inherited JavaScript tree has known advisories. The audit observed 46 findings including development dependencies, or 14 with development dependencies omitted; these include build-tool dependencies and are not a count of demonstrated browser exploits. See [dependency audit limits](docs/dependency-audit.md). No production deployment or security certification is claimed.

GitHub Actions runs the pinned Python bootstrap, focused module tests, and full RPC verifier for pushes and pull requests targeting `orderweave`. The workflow also builds Sao and uploads retained RPC and exact web-asset evidence. Browser interaction is unrun.

The Windows host may still report Tryton’s UTC timezone warning because Windows does not apply `TZ` like a POSIX host. The verifier does not change the system timezone. Date-sensitive production operation has not been qualified.

The local module suite passed 30 checks. The scoped Flake8 check passed, Sao’s Grunt build passed, and the real RPC verifier passed duplicate/conflict refusal, protected-checkpoint refusal, lost-ack recovery across server restart, compensation, and exact total reconciliation. Main-agent checks confirmed the owned listener and scratch directory were removed while evidence remained. Hosted results are recorded separately after the workflow runs.

## Module files

- `modules/orderweave/` contains the Tryton model, views, menu, RPC methods, and focused tests.
- `scripts/bootstrap_orderweave.py` creates the local source-built Python environment.
- `scripts/verify_orderweave.py` drives the authenticated XML-RPC demonstration.
- `.agents/skills/verify-orderweave/` contains the reusable verification workflow and feature map.
