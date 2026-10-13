# ADR 0038: Pluggable Monitoring

**Date:** 2026-10
**Status:** Proposed
**Decision Owner:** TAG Team

## Context

Another step towards Pluggable Apps (ADR 0034): a third-party chart installed into a Scout cluster should have its metrics scraped, its logs collected, and its dashboards appear in Grafana, with no edit to the Scout monorepo.

Two of those already work and only need documentation.
- Logs: Alloy discovers pods cluster-wide with no namespace filter.
- Metrics: a Pluggable App that sets the `prometheus.io/{scrape,port,path}` annotations will be scraped.

The remaining task is getting Pluggable Apps' Grafana dashboards configured.

<!--Dashboards are almost there already. We have Grafana running a sidecar pod that pulls in dashboards from ConfigMaps. All we need is to set it to search all namespaces for that to be available to Pluggable Apps. Alerts are a bit harder, though, because of an [upstream constraint](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/#import-notification-policies):

> In Grafana, the entire notification policy tree is considered a single, large resource. … Since specific policies may depend on each other, you cannot provision subsets of the policy tree; the entire tree must be defined in a single place.

This implies that anything in the "notification policy tree" is not pluggable-->

## Decision

We support logs, metrics scraping, and dashboards for Pluggable Apps. Alert rules and datasources are deferred.

### Dashboards

We continue to use the Grafana `kiwigrid/k8s-sidecar` for dashboard discovery, just like we already do with core Scout components, not the Grafana Operator. See Alternatives.

We don't know what namespace Pluggable Apps will be installed into, so we set the Grafana sidecar to search `ALL` namespaces.

The ConfigMap data format is the raw Grafana dashboard JSON, no special Scout anything required. It's likely that a user would develop a dashboard in the UI and export the JSON; by pasting that exported JSON right into the ConfigMap template in their chart they'll get exactly the dashboard they developed.

All Pluggable App dashboards will land under the same `Scout/` subdirectory as the core Scout dashboards. Dashboard file names (i.e. the key in the ConfigMap) and UIDs (a key in the JSON) can collide. We recommend Pluggable App dashboards include a prefix in their file names and UIDs to avoid collisions.

### Logs
No changes were needed in Scout to support log ingestion from Pluggable Apps. It already worked.

### Metrics

Prometheus will scrape metrics from any pod annotated with `prometheus.io/{scrape,port,path}`. We didn't need to modify anything to make this work, it was already configured this way. The only change we need to make is to document this contract for Pluggable App developers.

### Deferred: Alert rules and Datasources

Alert rules can technically be supported right now the same as dashboards, at least mechanically. We can turn on Grafana scraping so an alert rule in a ConfigMap can be picked up and added. The problem is doing this safely.

- One bad alert freezes all alert rule updates. This was tested empirically by creating a "bad" alert rule ConfigMap with deliberately incorrect syntax. The alert file was picked up but causes every reload scan to fail, which extends to other unrelated alert rules like Scout core's, until the offending file is removed. And that removal doesn't necessarily happen when the ConfigMap containing the alert is removed. While the reloads were in the failure state, the sidecar missed a ConfigMap deletion event and left the offending alert in place. The file had to be manually `rm`ed from the pod.
- Deleting an alert rule ConfigMap does not delete its rules. The Grafana API requires a `deleteRules` entry to remove them. That doesn't fit the rules of Pluggable Apps, for which removing the app must remove all its data and objects with it. If we just `helm uninstall` a Pluggable App chart, nothing can publish the `deleteRules` for a removed ConfigMap.
- Each alert can modify evaluation of other alerts in the same group. Tested empirically: a pluggable alert using the same group name as core alerts but with a different `interval` changed all 18 core rules in the `1m-evaluations` group to 5m.

Datasources are less problematic in terms of their behavior than alert rules, but the consequences of a `uid` collision could be farther-reaching. A pluggable datasource which redefines the same `uid` as a core Scout datasource would mean all the core dashboards now run their queries against the newly redefined datasource, and most likely all fail.

The likely solution to these is a reconciler like ADR-0037 which accepts fragmentary or whole alert rules and datasources, validates them for conformance to a subset of the available vocabulary and to avoid uid and group collisions, and republishes this to Grafana. It could also note deletions and emit a `deleteRules`/`deleteDatasources` object.

## Alternatives considered

Publish dashboards as CRs picked up by the Grafana Operator (`grafana/grafana-operator`) instead of ConfigMaps picked up by the sidecar. Reasons we lean sidecar:

- The sidecar does everything we need. The operator does add new capabilities, but nothing we require. For instance, we have no need for folder-level dashboard permissions or nested folders, and we have only a single Grafana instance so don't need to select an instance. All of which are extra capabilities the operator could give us over the sidecar, if we did want them, but right now we don't.
- Dashboards picked up by the sidecar are read-only within Grafana; they can't be edited. The operator pushes its content over Grafana's HTTP API, so its dashboards would be editable but edits get overwritten by its draft detection / resync. It's a small change, but read-only dashboards seem like a better UX than read-write-except-not-really for dashboards that are platform-owned

What would make us revisit:

- If Pluggable Apps ever need their own notification routing, this decision should be reopened. Notification policies are stored / written as a single tree by the sidecar, i.e. they are not composable by definition. (Unless we want to get into the business of composing them ourselves, which we do not.) The operator, on the other hand, can handle composing notification policies into the tree. Right now we accept that notification policies are not pluggable, which means we can keep the sidecar approach, but if we ever want this it will require swapping to the operator.
- If we ever want folder-level permissions on dashboards
- The operator does provide better real-time feedback on whether something was accepted or not. Under the sidecar the ConfigMap will publish just fine, and you need to consult a log to find out if the actual content was applied or what the error was.

## Consequences

- One Grafana value changes (`sidecar.dashboards.searchNamespace: ALL`). Core dashboards and the datasource and alert sidecars are untouched.
- Any `grafana_dashboard`-labelled ConfigMap in any namespace becomes a dashboard, including from third-party charts that ship dashboards under that de facto label.
- A plugin _can_ replace a core dashboard, including the home dashboard, by reusing its data key or UID. Accepted and mitigated by the documented prefix convention.
- Publish the plugin contract in user-facing docs: the dashboard label and prefix convention, datasource template variables, the metrics annotations, the Prometheus NetworkPolicy rule, and the log label set.
- Plugins cannot ship alert rules or datasources until the reconciler exists.
- **Accepted risk:** any plugin dashboard can query every metric and every pod log, and any Grafana user who can query can query any datasource. This is a Grafana OSS edition limit, not a property of the mechanism, and the operator would not change it. Today we accept this because only Scout admin users are Grafana users at all, so we trust that any dashboard can have the permission to query for and show any data.
- Everything lands twice until monitoring exists in the Flux base (ADR 0031).

## Related

- ADR 0034 runtime-configurable launchpad catalog
- ADR 0037 Keycloak realm fragments and reconciler
