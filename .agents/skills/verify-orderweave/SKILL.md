---
name: verify-orderweave
description: Drive the Orderweave Tryton module through its local authenticated XML-RPC path and verify order import, replay, compensation, and reconciliation.
---

# Verify Orderweave

Use this driver after changing the Orderweave module or its Tryton dependencies. It creates a synthetic SQLite database and a separate receipt ledger under `.tooling/orderweave/`. The server listens on `127.0.0.1:18080`.

## Launch

From the repository root, run:

```powershell
python scripts/bootstrap_orderweave.py
.venv/Scripts/python.exe scripts/verify_orderweave.py launch
```

The bootstrap creates `.venv` if needed and installs the third-party versions from `requirements-orderweave.lock.txt`, builds the vendored Tryton packages from this checkout, installs Orderweave, initializes a new SQLite database with the `orderweave` module and its dependencies, and starts Tryton. It stores the synthetic administrator password in the ignored `.tooling/orderweave/admin-password.txt` file. It prints the local URL when authenticated XML-RPC login succeeds.

## Doctor

Run this before driving an existing instance:

```powershell
.venv/Scripts/python.exe scripts/verify_orderweave.py doctor
```

Doctor checks the recorded server process, authenticates to the expected database, and reads the Orderweave model. If any check fails, clean up the instance and launch it again.

## Drive

Drive the mapped import and reconciliation journey through the XML-RPC API:

```powershell
.venv/Scripts/python.exe scripts/verify_orderweave.py drive
```

The driver creates a synthetic company, customer, address, and service product through Tryton's XML-RPC client. It imports the same external identity twice, checks that a changed payload is rejected, advances a real `sale.sale` through quotation and confirmation, and forces the receiver to save a receipt while losing its acknowledgment. It then stops and restarts Tryton, retries the order, and checks the final sale total. It also verifies quotation compensation and the manual-review state for an already confirmed sale.

Only drive the instance launched by this driver. Do not point it at an existing user database or a non-local server.

## Evidence

Run:

```powershell
.venv/Scripts/python.exe scripts/verify_orderweave.py evidence
```

Evidence goes to `.audit/evidence/`. The JSON record captures each action and resulting state. The server log records startup and RPC errors. The receipt row count is read from the separate SQLite ledger, so the proof checks the receiver side effect as well as the Tryton records.

## Cleanup

After the drive and evidence steps, run:

```powershell
.venv/Scripts/python.exe scripts/verify_orderweave.py cleanup
```

Cleanup stops the recorded Tryton PID, removes the synthetic database, receipt ledger, configuration, and password file, and confirms the RPC evidence remains. It does not remove `.audit/evidence/` or `.venv/`.

## Helpers

The executable driver is `scripts/verify_orderweave.py`. The environment bootstrap is `scripts/bootstrap_orderweave.py`. Run the complete launch, doctor, drive, evidence, and cleanup sequence with:

```powershell
.venv/Scripts/python.exe scripts/verify_orderweave.py run
```

See `features/order-import-reconciliation.md` for the maintained user-path map. Use the PStack `/maintain-verification-skill` workflow when Orderweave behavior or its driver changes.

The optional Sao client build uses `npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund` then `npx --no-install grunt` from `sao/`. The demo config serves this source directory. When built assets exist, Doctor fetches the index, JavaScript and CSS through the real HTTP listener and records exact byte hashes in `orderweave-web-assets.json`. This is static delivery proof, not rendered-browser acceptance. See `docs/dependency-audit.md` for inherited advisories.

The scoped lint command is `.venv/Scripts/python.exe -m flake8 modules/orderweave scripts/bootstrap_orderweave.py scripts/verify_orderweave.py`, using the pinned tool and upstream `.flake8` configuration.
