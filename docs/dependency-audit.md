# Inherited Sao dependency audit

The pinned Tryton 8.0.12 monorepo includes Sao 8.0.13. Its new `sao/package-lock.json` records the dependency resolution used for the successful local Grunt build. Upstream package ranges and application code remain unchanged.

On October 20, 2026 in the local timezone, `npm audit --json` reported 46 findings: 3 critical, 27 high, 15 moderate, and 1 low. `npm audit --omit=dev --json` reported 14 findings: 6 high and 8 moderate. The latter tree still contains Grunt build tooling declared as production dependencies by upstream. These are advisory matches, not verified exploits or proof that every issue reaches a shipped browser path.

The critical full-tree chain includes inherited `grunt-qunit-junit`; no automatic fix was offered. The browser-facing Bootstrap dependencies also have moderate findings. The dependency tree includes deprecated packages and old engine declarations. This project does not silently upgrade the ERP’s client stack or treat a successful build as security acceptance.

The documented install disables package lifecycle scripts and uses upstream’s explicit `--legacy-peer-deps` mode for the old Grunt peer requirement. Build commands execute Grunt locally. No browser test download or automated browser is used. The hosted workflow builds the client and verifies served bytes; it does not run an audit gate or claim zero advisories.

Reproduce the audit from `sao/` after the documented install:

```sh
npm audit --json
npm audit --omit=dev --json
```

Keep this app limited to the synthetic local demo until its dependency and deployment boundaries receive separate qualification.
