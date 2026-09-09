"""Applying a composed realm through a keycloak-config-cli Job."""

import logging
import time
import urllib.parse
from importlib import resources
from string import Template

from . import yamlio
from .compose import SecretBinding
from .k8s import ApiError, Client
from .models import APPLY_JOB_LABEL
from .settings import Settings

log = logging.getLogger("app-manager")

# Deletion is asynchronous, so the name stays taken for a moment after it.
DELETE_POLL_SECONDS = 1.0
DELETE_TIMEOUT_SECONDS = 60.0


def _finished(job: dict) -> bool:
    status = job.get("status", {})
    return bool(status.get("succeeded") or status.get("failed"))


def _failure_reason(job: dict) -> str:
    """Why the Job failed, in Kubernetes' own words.

    Deliberately not the config-cli pod's log. That log is written after
    variable substitution, so it can carry a resolved client credential, and
    this string is rendered into the status ConfigMap -- an object with none of
    a Secret's protection, readable by anyone who can read ConfigMaps in the
    namespace. The Job is kept for `job_ttl_seconds` so the detail stays one
    `kubectl logs` away for someone who already has that access.
    """
    for condition in job.get("status", {}).get("conditions") or []:
        if condition.get("type") == "Failed" and str(condition.get("status")) == "True":
            reason = condition.get("reason") or "the apply job failed"
            message = condition.get("message")
            return f"{reason}: {message}" if message else str(reason)
    return "the apply job failed"


def apply_job_body(**values) -> dict:
    template = (
        resources.files("scout_app_manager")
        .joinpath("resources/apply-job.yaml")
        .read_text()
    )
    body = yamlio.safe_load(Template(template).substitute(**values))
    if not isinstance(body, dict) or body.get("kind") != "Job":
        raise ValueError("apply-job.yaml did not render to a Job")
    return body


class RealmApplier:
    def __init__(self, settings: Settings, client: Client, namespace: str) -> None:
        self.settings = settings
        self.client = client
        self.namespace = namespace

    def publish(self, text: str) -> None:
        """Write the composed realm where the Job will mount it.

        A ConfigMap: the document names its credentials rather than carrying
        them, so nothing here is sensitive and an operator can read it during
        an incident without Secret access.
        """
        self.client.put_configmap_data(
            self.namespace,
            self.settings.composed_configmap,
            {"scout-realm.json": text},
            labels={"app.kubernetes.io/managed-by": "app-manager"},
        )

    def run(
        self, apply_key: str, bindings: dict[str, SecretBinding] | None = None
    ) -> tuple[bool, str]:
        name = f"app-manager-apply-{apply_key[7:19]}"
        self._prune(keep=name)
        if not self._clear_finished(name):
            return False, f"the previous {name} is still terminating"
        log.info("APPLY    running %s", name)
        try:
            self.client.create_job(self.namespace, self._body(name, bindings or {}))
        except ApiError as exc:
            if exc.status != 409:
                raise
            log.info("APPLY    job %s is already running; waiting on it", name)
        return self._wait(name)

    def _clear_finished(self, name: str) -> bool:
        """Free the name if a finished Job holds it. False if it is still going.

        The name is the realm hash, so an unchanged realm asks for the same Job
        every reconcile. A Job that has already succeeded or failed is a
        previous attempt's verdict: waiting on it again would replay a stale
        failure for the whole of the Job's TTL, long after Keycloak came back.
        An unfinished one is a live apply -- possibly a break-glass reconcile's
        -- and is waited on instead.
        """
        job = self.client.get_job(self.namespace, name)
        if job is None or not _finished(job):
            return True
        log.info("APPLY    replacing the finished job %s", name)
        self.client.delete_job(self.namespace, name)
        deadline = time.monotonic() + DELETE_TIMEOUT_SECONDS
        while self.client.get_job(self.namespace, name) is not None:
            if time.monotonic() >= deadline:
                return False
            time.sleep(DELETE_POLL_SECONDS)
        return True

    def _body(self, name: str, bindings: dict[str, SecretBinding]) -> dict:
        body = apply_job_body(
            name=name,
            namespace=self.namespace,
            image=self.settings.config_cli_image,
            keycloak_url=self.settings.keycloak_url,
            admin_secret=self.settings.admin_secret,
            composed_configmap=self.settings.composed_configmap,
            client_secrets_secret=self.settings.client_secrets_secret,
            server_hostname=self.settings.domain,
            ttl_seconds=self.settings.job_ttl_seconds,
        )
        # Appended rather than templated: the count varies with what is
        # installed, and a YAML fragment spliced into a string template is a
        # worse way to say "one more list entry".
        env = body["spec"]["template"]["spec"]["containers"][0]["env"]
        for binding in sorted(bindings.values(), key=lambda b: b.env):
            env.append(
                {
                    "name": binding.env,
                    "valueFrom": {
                        "secretKeyRef": {"name": binding.name, "key": binding.key}
                    },
                }
            )
        return body

    def _prune(self, keep: str) -> None:
        selector = urllib.parse.quote(f"{APPLY_JOB_LABEL}=apply")
        for job in self.client.list_jobs(self.namespace, selector):
            name = job["metadata"]["name"]
            if name != keep:
                self.client.delete_job(self.namespace, name)

    def _wait(self, name: str) -> tuple[bool, str]:
        deadline = time.monotonic() + self.settings.job_timeout_seconds
        while time.monotonic() < deadline:
            job = self.client.get_job(self.namespace, name)
            if job:
                status = job.get("status", {})
                if status.get("succeeded"):
                    return True, "succeeded"
                if status.get("failed"):
                    return False, (
                        f"{_failure_reason(job)}; run `kubectl logs -n "
                        f"{self.namespace} job/{name}` for the detail"
                    )
            time.sleep(2)
        return False, f"timed out after {self.settings.job_timeout_seconds}s"
