# Provisioning Grafana Dashboards and Alerts
Grafana does not allow editing provisioned dashboards and alerts in the UI. To make changes, export the updated JSON from the UI, update the repository file, and re-provision.

For alerts, template the notification settings for Ansible to link them to the correct contact point:
```json
"notification_settings": {
    "receiver": "{{ grafana_alert_contact_point }}"
}
```

Also for alerts, wrap JSON sections that should not be processed by Ansible/Jinja with `{% raw %}` and `{% endraw %}` to avoid errors. Dashboards are not processed by Ansible/Jinja, so this is not necessary for them.

Set the `uid` in dashboard or alert JSON to a human-readable value for easier tracking in Git and logs.
