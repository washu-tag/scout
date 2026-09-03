# Changelog

## [4.3.0](https://github.com/washu-tag/scout/compare/v4.2.0...v4.3.0) (2026-09-03)


### Features

* **deploy:** add launchpad to the GitOps deploy base ([#708](https://github.com/washu-tag/scout/issues/708)) ([4366e06](https://github.com/washu-tag/scout/commit/4366e061339e14568c006d51da757c1d30cc3c75))
* **keycloak:** manage the scout realm declaratively with keycloak-config-cli ([#698](https://github.com/washu-tag/scout/issues/698)) ([31a69ec](https://github.com/washu-tag/scout/commit/31a69ec48649c84c2ece8d64b34541877a514480))
* **report-viewer:** add chat driven charting ([#718](https://github.com/washu-tag/scout/issues/718)) ([18c5319](https://github.com/washu-tag/scout/commit/18c531961c68ad902be4e1922c31e5fa8acd0fbd))


### Bug Fixes

* **cassandra:** drop the single-node rack name so the datacenter adopts its PVC in place ([#700](https://github.com/washu-tag/scout/issues/700)) ([5e56905](https://github.com/washu-tag/scout/commit/5e56905b1212d2a822f3632ad5fd604b5ec24ce3))
* **deploy:** make mode sets siblings under modes/ (path: ./flux recursion fix) ([#709](https://github.com/washu-tag/scout/issues/709)) ([1e7d1c7](https://github.com/washu-tag/scout/commit/1e7d1c74f36804307911a182740214311c14f4a4))
* Drop kafka pin to 4.3.0 for compatibility with 1.1.0 of operator ([#694](https://github.com/washu-tag/scout/issues/694)) ([bea19b7](https://github.com/washu-tag/scout/commit/bea19b713eb1a020ebbc08ef9473b2deb8c79327))
* **extractor:** force libssl3/libcrypto3 security upgrade in base image ([#696](https://github.com/washu-tag/scout/issues/696)) ([2669c6a](https://github.com/washu-tag/scout/commit/2669c6ac555287b06cd29991ea6129a181555c55))
* **extractor:** loosen refresh-views heartbeat timeout ([#695](https://github.com/washu-tag/scout/issues/695)) ([464c560](https://github.com/washu-tag/scout/commit/464c560cb74fcab049ccabf86b5a07cf061608f8))
* **hl7-listener:** stop discarding messages that fail HL7 content validation ([#707](https://github.com/washu-tag/scout/issues/707)) ([fdedd53](https://github.com/washu-tag/scout/commit/fdedd53d09a4e6203f40727c79e8f141a8b88127))
* Narrow superset role from Alpha to Gamma ([#703](https://github.com/washu-tag/scout/issues/703)) ([2b53bd1](https://github.com/washu-tag/scout/commit/2b53bd1b6602d90f758d9aadace081fb06cd5607))
* **release:** also alias the scout-config artifact to the release version ([#689](https://github.com/washu-tag/scout/issues/689)) ([ac06200](https://github.com/washu-tag/scout/commit/ac06200eab8792a2aab00577f067eda7c647909d))
* **release:** clear the autorelease label after tagging ([#714](https://github.com/washu-tag/scout/issues/714)) ([c5796ab](https://github.com/washu-tag/scout/commit/c5796ab1dd7f66e2eea3750792546109f52f01b5))
* **release:** docker/login-action in publish-charts so cosign can auth to ghcr ([#688](https://github.com/washu-tag/scout/issues/688)) ([4189154](https://github.com/washu-tag/scout/commit/4189154f3a2b4d982745e529c38dee774b87f34b))
* resolve fixable Trivy CVEs across keycloak, hl7-listener, launchpad, report-viewer  ([#706](https://github.com/washu-tag/scout/issues/706)) ([2699d4b](https://github.com/washu-tag/scout/commit/2699d4b41f37d81e2cb210f866b9eab09db87b01))

## [4.2.0](https://github.com/washu-tag/scout/compare/v4.1.0...v4.2.0) (2026-08-25)


### Features

* couple scout-dashboards appVersion to the superset image version ([#610](https://github.com/washu-tag/scout/issues/610)) ([daf4373](https://github.com/washu-tag/scout/commit/daf43736ec3b6f13dbd7f9233fee483c0071338e))
* couple temporal-bootstrap and hive-metastore appVersion to their image versions ([#617](https://github.com/washu-tag/scout/issues/617)) ([d126b08](https://github.com/washu-tag/scout/commit/d126b0805da6bdaf40daad07bffd6d55451dc2b5))
* **deploy:** Phase 3 GitOps deploy base — 24 Kustomize bases + Flux DAG (unconsumed) ([#639](https://github.com/washu-tag/scout/issues/639)) ([98e5758](https://github.com/washu-tag/scout/commit/98e5758ab6ceace844dcc2a6f143085113d8497a))
* **deploy:** service-mode flip — on-prem MinIO/Traefik vs aws S3+IRSA/ALB-OIDC (ADR 0035) ([#679](https://github.com/washu-tag/scout/issues/679)) ([af601e4](https://github.com/washu-tag/scout/commit/af601e48ef75a345c0e1237785a9a238a17dad34))
* **extractor:** chart-own the modality mapping ([#630](https://github.com/washu-tag/scout/issues/630)) ([ab1a535](https://github.com/washu-tag/scout/commit/ab1a5355487a04f3787f23ac96f1239487a24432))
* **hive:** Hive helm chart startup probe + other fixes ([#684](https://github.com/washu-tag/scout/issues/684)) ([7fbf936](https://github.com/washu-tag/scout/commit/7fbf9367830425d3aa5871234db3808db9283c67))
* **launchpad:** Customizable launchpad ([#636](https://github.com/washu-tag/scout/issues/636)) ([3ef7878](https://github.com/washu-tag/scout/commit/3ef7878b4c7fe826b563c686254fdaacb920f82b))
* pin temporal + open-webui image versions and haul them (supersedes [#631](https://github.com/washu-tag/scout/issues/631)) ([#637](https://github.com/washu-tag/scout/issues/637)) ([94a8f0c](https://github.com/washu-tag/scout/commit/94a8f0cb563d78e212ea8e2ef85f3af6ef2961be))
* pin temporal + open-webui versions exactly and haul their images ([94a8f0c](https://github.com/washu-tag/scout/commit/94a8f0cb563d78e212ea8e2ef85f3af6ef2961be))
* **report-viewer:** add a per-column profile row to the report-viewer search table ([#678](https://github.com/washu-tag/scout/issues/678)) ([e9b1394](https://github.com/washu-tag/scout/commit/e9b139474f0af25592cf19be4fd6e68d88084e47))


### Bug Fixes

* **chat:** tighten cohort rules in chat prompt and flag text matching in report viewer ([#685](https://github.com/washu-tag/scout/issues/685)) ([50b0291](https://github.com/washu-tag/scout/commit/50b0291d1c0431153c105e614af446b28152de01))
* **ci:** bootstrap seeds Scout images at build-lane tags, not :latest ([#662](https://github.com/washu-tag/scout/issues/662)) ([07d5659](https://github.com/washu-tag/scout/commit/07d565939f639a05fe70746dd46439e8fa74452d))
* **ci:** bootstrap-haul renders images-only (charts unpublished at first haul) ([#653](https://github.com/washu-tag/scout/issues/653)) ([4e6535f](https://github.com/washu-tag/scout/commit/4e6535f9b894f46764fa0b67464d1a826abcf28d))
* **ci:** fetch the OPA test binary from GitHub releases ([#644](https://github.com/washu-tag/scout/issues/644)) ([21686bf](https://github.com/washu-tag/scout/commit/21686bf484f4404073bfd97c013b40a3d2687f03))
* **ci:** reset main to dev versions and stop reset-dev consulting reset_exists ([#633](https://github.com/washu-tag/scout/issues/633)) ([f8f8e30](https://github.com/washu-tag/scout/commit/f8f8e30eb35562af4d8fff67afa264a2793be99c))
* **deploy:** flux Kustomization paths point at the artifact root ([#656](https://github.com/washu-tag/scout/issues/656)) ([c1b262f](https://github.com/washu-tag/scout/commit/c1b262f1d176994c91ef03e5cba7da9cbb864bbc))
* **deploy:** install Trino before the services that mount its cert ([#650](https://github.com/washu-tag/scout/issues/650)) ([9b3b475](https://github.com/washu-tag/scout/commit/9b3b47563ef702945510c36089e0cd448d75b683))
* **hl7-transformer:** Batch derivative table calculations ([#646](https://github.com/washu-tag/scout/issues/646)) ([11b3915](https://github.com/washu-tag/scout/commit/11b3915b0c879a86ddc8a8be6253555f81bef518))
* **hl7-transformer:** Revert hl7-transformer activity split [#495](https://github.com/washu-tag/scout/issues/495) and [#646](https://github.com/washu-tag/scout/issues/646) ([#657](https://github.com/washu-tag/scout/issues/657)) ([20170c5](https://github.com/washu-tag/scout/commit/20170c5349de25fac69b237f6c7a401550960b05))
* **ingest:** Better handling of spark connection errors in transformer ([#672](https://github.com/washu-tag/scout/issues/672)) ([8d1c32f](https://github.com/washu-tag/scout/commit/8d1c32fae5fb5202f4969b52a1ecb93781131993))
* **minio:** keep the Scout-only versioned flag out of the Tenant spec ([#649](https://github.com/washu-tag/scout/issues/649)) ([0d7ce84](https://github.com/washu-tag/scout/commit/0d7ce8450c04bd32331ce6a2cf1bab27aa69b6cb))
* **open-webui:** surface expired sessions, cap JWT to SSO lifetime ([#670](https://github.com/washu-tag/scout/issues/670)) ([caa7d54](https://github.com/washu-tag/scout/commit/caa7d54f6f6a978b1d20f5884d5cb698b482e2a4))
* **rads:** repair the RADS playbook query, fix period comparison, and align to cohort builder playbook patterns ([#668](https://github.com/washu-tag/scout/issues/668)) ([4738374](https://github.com/washu-tag/scout/commit/47383740fe5deb54147deab09978099716c9710e))
* **report-viewer:** force util-linux security upgrade in base image ([#661](https://github.com/washu-tag/scout/issues/661)) ([6055a24](https://github.com/washu-tag/scout/commit/6055a24cfb72be31b09b1a37e45742aacbe7d680))


### Performance Improvements

* **ingest:** Improve performance deriving patient mapping table ([#676](https://github.com/washu-tag/scout/issues/676)) ([6cf4cd4](https://github.com/washu-tag/scout/commit/6cf4cd46376c54805e0c0a4dd395459dbdd84e9c))
