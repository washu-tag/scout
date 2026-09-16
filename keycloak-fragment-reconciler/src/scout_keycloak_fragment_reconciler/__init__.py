"""Reconciles Keycloak realm fragments into the Scout realm (ADR 0037).

An app publishes a labelled ConfigMap describing the Keycloak client it needs;
this service turns that into the narrow slice of realm config Scout allows, and
removes it again when the app goes away. It owns fragment clients and the
composite edges naming their roles, and nothing else -- the base realm stays
keycloak-config-cli's.

`fragment` is the contract apps write against, `translate` holds the
Keycloak-representation decisions, `core` is the loop. The rest are adapters.
"""

__version__ = "0.0.dev0"
