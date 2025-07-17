# Postgres Init SQL
Place a Postgres init SQL script here to be run when the Postgres database is created.

For service `example`, create a file named `example_init_sql.yaml` in this directory.
The file should contain a list, each element of which is a line of an SQL script to run.
These lines can contain template variables like `{{ variable }}`.

Additionally, you can conditionally disable running the init SQL for service `example` by defining a variable
`example_enabled` and setting it to `false`. If this variable is not defined, it defaults to `true`.
