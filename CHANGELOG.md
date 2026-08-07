# Changelog

## [5.0.0](https://github.com/washu-tag/scout/compare/v4.0.0...v5.0.0) (2026-08-07)


### ⚠ BREAKING CHANGES

* **xnat:** requires an xnat chart with the render-time plugin installer (NrgXnat/helm-charts) -- bump xnat_chart_version before deploying. Console logging also changes hands: the installer's logback rewrite is gone, so plugin logs need XNAT_LOG_CONSOLE on the xnat container, which means the chart's `logConsole` value from NrgXnat/helm-charts#37. Land that first or plugin logs go to files where nothing collects them. docker/xnat-plugin-installer/ and its CI can be deleted once no branch deploys it -- main, feat/hl7-listener and demo-integration still reference the image, and they share the `latest` tag.

### Features

* **github_actions:** Add issue templates and PR project board automation ([#484](https://github.com/washu-tag/scout/issues/484)) ([096a372](https://github.com/washu-tag/scout/commit/096a372da4129a6d903c2a820979fd2b5f591dff))
* **gitops:** fan out chart publishing to all Scout charts ([#553](https://github.com/washu-tag/scout/issues/553)) ([c0beb41](https://github.com/washu-tag/scout/commit/c0beb41120bc38f0f7fb2ebee8c40cd7abc8293e))
* **gitops:** publish + OCI-deploy hl7log-extractor (phase 0 follow-up) ([#550](https://github.com/washu-tag/scout/issues/550)) ([725da88](https://github.com/washu-tag/scout/commit/725da8817b1beec2f32b4cdec0a115c3b75f54a5))
* **gitops:** publish charts to OCI and make spark-defaults chart-owned ([#541](https://github.com/washu-tag/scout/issues/541)) ([397f9e5](https://github.com/washu-tag/scout/commit/397f9e51ac8f6eab09f594117410345eed725cfa))
* **hl7-listener:** opt-in, event-driven HL7 ingest path for collection and observation ([#475](https://github.com/washu-tag/scout/issues/475)) ([4cd8c92](https://github.com/washu-tag/scout/commit/4cd8c929fdce523ed0a869ceecce6ab6384d9332))
* **hl7-transformer:** make chart-owned spark-defaults extensible (dr… ([#551](https://github.com/washu-tag/scout/issues/551)) ([a84023b](https://github.com/washu-tag/scout/commit/a84023b4e710ae80f6cfc4aa9e72bb6e045bed9d))
* **hl7-transformer:** split derivative table creation into new activity ([#495](https://github.com/washu-tag/scout/issues/495)) ([73e840d](https://github.com/washu-tag/scout/commit/73e840de33529fca399f702ae898c0571a5c5012))
* **hl7-transformer:** upgrade to Spark 4.1.1 and Delta Lake 4.3.0 ([#453](https://github.com/washu-tag/scout/issues/453)) ([091085e](https://github.com/washu-tag/scout/commit/091085e4c808d76973310fb35b15db1449d57002))
* **ingest:** Calculate row hash, skip merging unchanged reports to lake ([#539](https://github.com/washu-tag/scout/issues/539)) ([293183d](https://github.com/washu-tag/scout/commit/293183db1301a9d6dc1979c1033a3fb09697cf64))
* **launchpad:** gate MinIO Lake card behind enableMinio flag ([#488](https://github.com/washu-tag/scout/issues/488)) ([a8110eb](https://github.com/washu-tag/scout/commit/a8110eb160ba12347c2c2f29851f670b2e6f3fb1))
* **open-webui:** upgrade to 0.10.2 ([#543](https://github.com/washu-tag/scout/issues/543)) ([5127dee](https://github.com/washu-tag/scout/commit/5127deefb2d36ab2a483fc67411d852b0b1a5c83))
* **report-viewer:** add embedded viewer service to chat ([#501](https://github.com/washu-tag/scout/issues/501)) ([7e0bf45](https://github.com/washu-tag/scout/commit/7e0bf454c31e219781965d9dc8dd6f1bb5613bce))
* **report-viewer:** client-side browsing, decouple from epic views ([#573](https://github.com/washu-tag/scout/issues/573)) ([4950a9a](https://github.com/washu-tag/scout/commit/4950a9aaa9d5ee05fe4f900c49cf427ee2f75b7a))
* **report-viewer:** data-driven modality filter + chat confirm ([#554](https://github.com/washu-tag/scout/issues/554)) ([df2bd2e](https://github.com/washu-tag/scout/commit/df2bd2e75636507f0353ea495ae8546fd09dec2e))
* **report-viewer:** surface MPI in the viewer ([#580](https://github.com/washu-tag/scout/issues/580)) ([0f56e31](https://github.com/washu-tag/scout/commit/0f56e3181ccbbee0aa272477b05cc3cf4b1eb1d9))
* **report-viewer:** surface patient_mpi in the viewer ([0f56e31](https://github.com/washu-tag/scout/commit/0f56e3181ccbbee0aa272477b05cc3cf4b1eb1d9))
* **temporal:** make default namespace workflow retention configurable ([#579](https://github.com/washu-tag/scout/issues/579)) ([d992cba](https://github.com/washu-tag/scout/commit/d992cbada8e636e40e3d4c6119a55574bd610110))
* **xnat:** chart 3.0.0 adoption - chart-native plugin install, deploy hardening, dev/test tooling ([#570](https://github.com/washu-tag/scout/issues/570)) ([9cebad6](https://github.com/washu-tag/scout/commit/9cebad63db4e6c5956681fbbc16d5227e433a405))


### Bug Fixes

* **ci:** skip fork PRs on assign-author and pr-status ([#555](https://github.com/washu-tag/scout/issues/555)) ([ae9dd3d](https://github.com/washu-tag/scout/commit/ae9dd3d010c7f234b3ca1a1266028f2bcbc16359))
* **ci:** stamp scout-opa and temporal-bootstrap chart versions on release ([#623](https://github.com/washu-tag/scout/issues/623)) ([f63d21a](https://github.com/washu-tag/scout/commit/f63d21a37f83bf5811cd48c26a93b2e242f048b9))
* **cves:** jackson bump, scout-notebook base, keycloak scan enforcing ([#451](https://github.com/washu-tag/scout/issues/451)) ([8d81e5f](https://github.com/washu-tag/scout/commit/8d81e5f50c5256e012c68895e54e6b74d76f8851))
* **data-generator:** Pass in number of workers in helm deploy to data generator ([#462](https://github.com/washu-tag/scout/issues/462)) ([a780a23](https://github.com/washu-tag/scout/commit/a780a2338759fdb49be90364193a1b56a7a2b97d))
* **dcm4chee:** Update dcm4chee dev PACS deployment ([#486](https://github.com/washu-tag/scout/issues/486)) ([041b543](https://github.com/washu-tag/scout/commit/041b543e4ac2713b23566639e2c8f61937751fb6))
* **deps:** remediate micrometer-core CVE-2026-40983/-40984 (hl7-listener, keycloak) ([#624](https://github.com/washu-tag/scout/issues/624)) ([f84d7c8](https://github.com/washu-tag/scout/commit/f84d7c8e5b888963f67078a9a35a081d1c5bf0c0))
* **extractor:** Dedupe incoming update/inserts on curated table ([#460](https://github.com/washu-tag/scout/issues/460)) ([553682e](https://github.com/washu-tag/scout/commit/553682ebd86a2782f036d5230d0d924126908a4b))
* harmonize default hl7-transformer timeout ([#474](https://github.com/washu-tag/scout/issues/474)) ([dd954a7](https://github.com/washu-tag/scout/commit/dd954a74db927d20685a0cebf20ed5eb90f1aeb0))
* **hl7-transformer:** drop unused pip to clear vendored CVEs ([#577](https://github.com/washu-tag/scout/issues/577)) ([5784136](https://github.com/washu-tag/scout/commit/5784136598b34f68c9aa33afcc4b24dbabca360e))
* **ingest:** Deterministic OBX order ([#538](https://github.com/washu-tag/scout/issues/538)) ([ca0dece](https://github.com/washu-tag/scout/commit/ca0dece876869db87cd6c1a9edcee37260acbf02))
* **jupyter:** mount branding outside ~/.jupyter to fix first-spawn saves ([#559](https://github.com/washu-tag/scout/issues/559)) ([f083a7a](https://github.com/washu-tag/scout/commit/f083a7ac8ce76ece0f6bf6c5843b110a90b80e66)), closes [#496](https://github.com/washu-tag/scout/issues/496)
* **launchpad:** admin user-management UX — access warning, new tab, clearer status ([#520](https://github.com/washu-tag/scout/issues/520)) ([ed31d9a](https://github.com/washu-tag/scout/commit/ed31d9a15c8b311ba06a7765f0ab826e59ce0ad0))
* **launchpad:** Expose favicon through auth on launchpad login ([#564](https://github.com/washu-tag/scout/issues/564)) ([48404e8](https://github.com/washu-tag/scout/commit/48404e89428eaf89e1e1da92559c6ed7ff1d5222))
* **opa:** Trino AuthZ policy hardening + masking opt-in + attribute rename ([#522](https://github.com/washu-tag/scout/issues/522)) ([bfbc590](https://github.com/washu-tag/scout/commit/bfbc590a92774ead1805fd9f9c31023cb0b66e25))
* **open-webui:** disable per-chat sharing ([#547](https://github.com/washu-tag/scout/issues/547)) ([7f03bd9](https://github.com/washu-tag/scout/commit/7f03bd965925d20f5fed157ca81d918a54faa364))
* **open-webui:** grant non-admin users access to Trino MCP tool server ([#449](https://github.com/washu-tag/scout/issues/449)) ([1f7a7d8](https://github.com/washu-tag/scout/commit/1f7a7d88407f8b8620119975aaf0488af7c7d0a6))
* **playbooks:** serve playbook CSV exports client-side (cohort + follow-up) ([#521](https://github.com/washu-tag/scout/issues/521)) ([a923b51](https://github.com/washu-tag/scout/commit/a923b513c2aec6909b7b539b55e4abc5a21c4b0d))
* **report-viewer:** CSV Export Fix and Dark Mode ([#548](https://github.com/washu-tag/scout/issues/548)) ([838fa2e](https://github.com/washu-tag/scout/commit/838fa2e2aa191109c2d7174cda2f82b57ace9c6a))
* **security:** bump spring-framework to clear scan-images HIGH findings ([#583](https://github.com/washu-tag/scout/issues/583)) ([3741bed](https://github.com/washu-tag/scout/commit/3741bed07bc19f2e1c430de4baf4ab88f9866bb1))
* **security:** clear scan-images HIGH/CRITICAL findings across all im… ([#563](https://github.com/washu-tag/scout/issues/563)) ([48cbf30](https://github.com/washu-tag/scout/commit/48cbf3069f88e3781588c9a6fb13b0a1212945f9))
* **security:** clear scan-images HIGH/CRITICAL findings across all images ([48cbf30](https://github.com/washu-tag/scout/commit/48cbf3069f88e3781588c9a6fb13b0a1212945f9))
* **security:** resolve pgjdbc CVE-2026-54291 in scan-images gate ([#556](https://github.com/washu-tag/scout/issues/556)) ([cf311c7](https://github.com/washu-tag/scout/commit/cf311c7d2becc15c41e3eaddcff265dd2f0db44b))
* **security:** suppress pip-vendored setuptools/msgpack CVEs in scout-notebook ([6fcc2ab](https://github.com/washu-tag/scout/commit/6fcc2ab2562cc5f2199c92d921ef3f9c183952f7))
* **security:** suppress pip-vendored setuptools/msgpack CVEs in scout… ([#592](https://github.com/washu-tag/scout/issues/592)) ([6fcc2ab](https://github.com/washu-tag/scout/commit/6fcc2ab2562cc5f2199c92d921ef3f9c183952f7))
* **staging:** download k3s install script via curl, not get_url ([#558](https://github.com/washu-tag/scout/issues/558)) ([3b32bc3](https://github.com/washu-tag/scout/commit/3b32bc31101c5647388d0338de2d1774a2cc3ea6))
* **superset:** per-user result cache and SQL Lab export ownership ([#518](https://github.com/washu-tag/scout/issues/518)) ([8c9aef1](https://github.com/washu-tag/scout/commit/8c9aef1181886c0d64df7c5e496825cc27aa2f4e))
