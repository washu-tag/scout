# Tips & Tricks

## Notebooks

### Server Lifespan

Jupyter notebook servers automatically shut down after 2 days of runtime. Your notebook files and home directory (`/home/jovyan/`) persist, but in-memory variables are lost.
To avoid potentially losing any important work, save notebooks frequently (Ctrl+S / Cmd+S) and save large DataFrames and intermediate results to disk.

### Saving Intermediate Results

Your home directory (`/home/jovyan/`) persists across server restarts. Use it to checkpoint your work so you can pick up where you left off.

**Spark DataFrames (Parquet):**
```python
# Save after expensive computation
df.write.parquet('/home/jovyan/checkpoints/results.parquet')

# Resume later
df = spark.read.parquet('/home/jovyan/checkpoints/results.parquet')
```

**Pandas DataFrames:**
```python
# CSV (human-readable)
df.to_csv('/home/jovyan/checkpoints/results.csv', index=False)
df = pd.read_csv('/home/jovyan/checkpoints/results.csv')

# Parquet (faster, preserves types)
df.to_parquet('/home/jovyan/checkpoints/results.parquet')
df = pd.read_parquet('/home/jovyan/checkpoints/results.parquet')
```

**Python objects (pickle):**
```python
import pickle

# Save any Python object
with open('/home/jovyan/checkpoints/my_data.pkl', 'wb') as f:
    pickle.dump({'results': results, 'config': config}, f)

# Load it back
with open('/home/jovyan/checkpoints/my_data.pkl', 'rb') as f:
    data = pickle.load(f)
```

**ML models:**
```python
# scikit-learn
import joblib
joblib.dump(model, '/home/jovyan/models/classifier.joblib')
model = joblib.load('/home/jovyan/models/classifier.joblib')

# PyTorch
torch.save(model.state_dict(), '/home/jovyan/models/checkpoint.pth')
model.load_state_dict(torch.load('/home/jovyan/models/checkpoint.pth'))
```

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