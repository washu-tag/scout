# Data Authorization

Scout authorizes access to report data on a per-user basis. Authentication (who you are — see [Authentication](authentication.md)) is separate from authorization (what data you can see, covered here). Two kinds of restriction are applied at the query layer (Trino — used by Analytics, Notebooks, and Chat):

* **Row filtering** — you only see rows whose `sending_facility` is one your account is permitted. For example, a user scoped to `HOSP1` sees only HOSP1 rows in `reports`, `reports_curated`, `reports_latest`, `reports_dx`, and the joined `*_epic_view` views. (A deployment may also restrict on other dimensions.)
* **Column masking** — protected health information (PHI) columns such as `patient_name`, `full_patient_name`, and `zip_or_postal_code` are returned as `[REDACTED]` (or `NULL` for complex types) unless your account is authorized to see PHI in the clear.

```{note}
If you query a report table and see far fewer rows than expected, or see `[REDACTED]` where you expected a name, that's data authorization at work — your account's permissions are being applied as configured. Contact your Scout administrator if the configuration looks wrong.
```

Administrators configure these per-user permissions in Keycloak. For the attribute model, a setup walkthrough, propagation timing, verification, and troubleshooting, see [Configuring Data Authorization](technical/data_authorization.md).

## See also

* [Authentication](authentication.md) — how users log in
* [Data Schema](dataschema.md) — the report tables that row filtering and column masking operate on
* [Configuring Data Authorization](technical/data_authorization.md) — administrator setup and troubleshooting
