# Tips & Tricks

## Monitor

- **Accessing Dashboards and Logs**: The Scout dashboards provisioned in Grafana can be found in
  **Dashboards > Scout**. Logs for individual services can be accessed in the **Drilldown > Logs** section of
  Grafana.
- **Adjusting Time Ranges**: Modify the time range when viewing dashboards and logs to focus on specific periods. Note
  that services without logs or metrics during the selected time range will not appear.
- **Click on Legends**: In the dashboards and logs, clicking on the legend entries will filter the data to show only
  the selected entry. This is useful for isolating specific metrics or log entry types (e.g., errors).
- **Filtering Data**: Many dashboards include variables (e.g., namespace, node) that can be used to filter data.
  Use these variables to narrow down the displayed information for more targeted analysis. Variables are typically
  located at the top of the dashboard.
- **Correlating Logs Across Services**: To view logs from multiple services in a single view, select the "Include"
  option for each service, then click "Show Logs." This allows you to search, filter, and identify patterns across
  services.
- **Kubernetes PV/PVC Metrics**: Metrics for Persistent Volumes (PVs) and Persistent Volume Claims (PVCs) may not work
  outside of public cloud environments. Instead, use the **Node Exporter** dashboard to monitor disk usage for each node
  and mount point.
- **Saving Dashboard Changes**: Provisioned dashboards cannot be modified directly in the Grafana UI. To make changes,
  save the dashboard as a new one, export the updated JSON, and update the dashboard configuration in the Scout
  repository for redeployment.