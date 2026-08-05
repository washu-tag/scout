# Monitoring

Scout ships pre-configured [Grafana](https://grafana.com/) dashboards and log
aggregation so operators can track platform health and troubleshoot issues.
Access Grafana from the **Admin Tools** section of the Scout Launchpad.

## Viewing Dashboards and Logs

- **Accessing Scout Dashboards**: Navigate to **Dashboards > Scout** in Grafana
- **Accessing Service Logs**: Go to **Drilldown > Logs** section or use **Explore > Loki**
- **Adjust Time Ranges**: Modify the time range to focus on specific periods. Services without activity during the selected time range will not appear
- **Click on Legends**: Click legend entries to isolate specific metrics or log entry types (e.g., filter to only errors)

## Dashboard Variables

- Many dashboards include variables (namespace, node, pod, etc.) at the top
- Use these to filter data for targeted analysis
- Multiple selections are often supported

## Correlating Logs Across Services

To view logs from multiple services in a single view:
1. In the Logs panel, select "Include" for each service you want to view
2. Click "Show Logs"
3. Search, filter, and identify patterns across services

This is especially useful for debugging issues that span multiple components.

## Disk Usage Monitoring

- **Kubernetes PV/PVC Metrics**: May not work in on-premises deployments
- **Alternative**: Use the **Node Exporter** dashboard to monitor disk usage for each node and mount point

## Saving Dashboard Changes

- **Provisioned dashboards** (in Dashboards > Scout) cannot be modified directly in Grafana
- **To make changes**:
  1. Save the dashboard as a new one with a different name
  2. Make your modifications
  3. Export the updated JSON
  4. (Admins) Update the dashboard configuration in the Scout repository for future deployments
