# ADR 0005: Decision Not to Implement MinIO STS Authentication in Scout

**Date:** 2025-11-19  
**Status:** Proposed  
**Decision Owner**: TAG Team

## Context

### Background: Kubernetes Service Account Token Authentication

AWS provides IAM Roles for Service Accounts (IRSA) as a best-practice authentication mechanism for Kubernetes workloads accessing S3:

- **Flow**: Kubernetes service account → OIDC token → AWS STS → temporary credentials
- **Configuration**: Pods are annotated with IAM role ARN; EKS webhook injects environment variables (`AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`)
- **SDK Support**: AWS SDKs automatically discover credentials via `WebIdentityTokenFileCredentialsProvider`
- **Benefits**: No static credentials, automatic rotation, RBAC via Kubernetes service accounts

### MinIO Operator STS Implementation

MinIO Operator provides an equivalent authentication mechanism for on-premises Kubernetes deployments:

- **STS Endpoint**: MinIO Operator STS (port 4223) validates Kubernetes service account JWT tokens
- **Policy Mapping**: PolicyBinding CRDs map Kubernetes service accounts to MinIO IAM policies
- **SDK Support**: boto3 added `AWS_ENDPOINT_URL_STS` in July 2023 to support custom STS endpoints
- **Pattern**: Same flow as AWS IRSA but requires custom STS endpoint configuration

### MinIO Token Projection Requirements

MinIO STS has specific requirements for service account token configuration:

- **Custom Audience**: Default Kubernetes service account tokens use audience `kubernetes.default.svc`, but MinIO requires audience `sts.min.io`
- **Token Rejection**: Tokens with incorrect audience are rejected with "audience 'sts.min.io' not found" error
- **Projected Volumes**: Pods must use projected service account tokens with custom audience:
  ```yaml
  volumes:
    - name: sts-token
      projected:
        sources:
          - serviceAccountToken:
              audience: sts.min.io
              expirationSeconds: 3600
              path: token
  ```
- **Environment Variable**: Must point to projected token path: `AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/projected/token`
- **ARN Format**: Differs from AWS: `arn:minio:iam::<tenant>:policy/<policy-name>`

### Original Goal for Scout

The goal was to achieve authentication portability between MinIO (on-prem) and AWS S3 (cloud):

- Eliminate static access keys from Scout deployments
- Use identical code for both environments, changing only endpoint configuration
- Enable RBAC via Kubernetes service accounts

## Decision

**Scout will not implement MinIO STS authentication for on-prem deployments** and will continue using static access keys for MinIO.

**Scout will support AWS STS (IRSA) for cloud deployments** when deployed to AWS EKS.

## Technical Findings

### What Works: boto3 / AWS SDK v2

- `AWS_ENDPOINT_URL_STS` environment variable successfully redirects STS calls to MinIO Operator
- Automatic credential discovery works without code changes
- Same container image works for both MinIO and AWS by changing environment variables

**Scout services compatible with MinIO STS:**
- **HL7 Log Extractor** (Java/AWS SDK v2): Ready for STS with environment variable changes only

### What Does Not Work: Hadoop S3A Connector

**Root Cause**: AWS SDK limitations combined with Hadoop's credential provider implementation.

**AWS SDK Support for Custom STS Endpoints:**

| SDK | `AWS_ENDPOINT_URL_STS` Support | Notes |
|-----|-------------------------------|-------|
| **Java SDK v1** | ❌ No | No mechanism to configure custom STS endpoints |
| **Java SDK v2** | ⚠️ Partial | Requires explicit configuration (see below) |
| **boto3 (Python)** | ✅ Yes | Fully automatic via environment variable |

**Java SDK v2 Nuance**: While SDK v2 supports `AWS_ENDPOINT_URL_STS` (added in v2.28.1), the `WebIdentityTokenFileCredentialsProvider` class creates an internal STS client that does not respect this environment variable. AWS addressed this in [Issue #1881](https://github.com/aws/aws-sdk-java-v2/issues/1881) by creating a separate class (`StsWebIdentityTokenFileCredentialsProvider`) that allows passing a custom-configured STS client with explicit `endpointOverride()`. This requires manual wiring that Hadoop does not perform.

**Why Hadoop S3A Fails:**

Even with Hadoop 3.4.1+ (which uses AWS SDK v2 internally):
- Hadoop uses `WebIdentityTokenFileCredentialsProvider` from the automatic credential provider chain
- This provider creates an internal STS client that ignores `AWS_ENDPOINT_URL_STS`
- Hadoop does not use the newer `StsWebIdentityTokenFileCredentialsProvider` with custom STS client configuration
- Requests are sent to `sts.amazonaws.com` instead of MinIO Operator STS
- There is no Hadoop configuration property to override the STS endpoint for WebIdentity tokens
- `fs.s3a.assumed.role.sts.endpoint` exists but only works with `AssumedRoleCredentialProvider`, not WebIdentity

**Scout services blocked by Hadoop limitation:**
- **Hive Metastore** (both write and read-only instances): Uses Hadoop-AWS
- **Trino**: Uses Hadoop-AWS via Delta Lake connector
- **HL7 Transformer** (PySpark component): Uses Hadoop S3A for Delta Lake operations
- **JupyterHub** (PySpark notebooks): Uses Hadoop S3A for all Spark S3 access

### Partial Compatibility Services

Some services use both compatible and incompatible libraries:
- **HL7 Transformer**: s3fs (compatible) + PySpark (incompatible)
- **JupyterHub**: User boto3 code (compatible) + PySpark S3A (incompatible)

## Infrastructure Requirements for MinIO STS

MinIO STS requires additional configuration:

- **cert-manager**: Already installed with Cassandra; would need to be reordered in deployment sequence
- **TLS/HTTPS**: MinIO tenant must use HTTPS (currently HTTP-only); service port changes from 80 to 443
- **PolicyBinding CRDs**: New resources to manage per service account
- **Token Projection**: Service account tokens require custom audience (`sts.min.io`) via projected volumes

## Alternatives Considered

### Option 1: Partial Implementation (Compatible Services Only)

Enable MinIO STS for compatible services (HL7 Log Extractor) while keeping static keys for Spark-based services (Hive, Trino, HL7 Transformer, JupyterHub).

**Rejected**: Creates inconsistent authentication patterns, increases operational complexity, minimal security benefit.

### Option 2: Custom Hadoop Credential Provider

Develop custom Java provider that explicitly configures STS endpoint. Build and distribute JAR to all Spark containers.

**Rejected**:
- High development effort (2-3 days), ongoing maintenance burden
- Testing required for each Hadoop upgrade
- Only covers services where we control the credential provider (Spark)
- Does NOT solve Hive or Trino where credential providers are configured internally by those systems

### Option 3: Patch Hadoop S3A Connector

Contribute upstream fix to expose STS endpoint configuration.

**Rejected**: Uncertain timeline, requires navigating Hadoop contribution process.

### Option 4: Wait for Hadoop Enhancement

Monitor Hadoop project for native STS endpoint support.

**Rejected**:
- Existing Hadoop JIRA tickets ([HADOOP-18154](https://issues.apache.org/jira/browse/HADOOP-18154), [HADOOP-17188](https://issues.apache.org/jira/browse/HADOOP-17188)) added WebIdentity support but only for AWS STS
- No indication custom STS endpoint support is planned
- Indefinite timeline

### Option 5: Alternative S3-Compatible Storage Systems

Replace MinIO with another S3-compatible object storage that supports STS authentication.

**Alternatives Evaluated:**

| Solution | STS Support | K8s SA Auth | Notes |
|----------|-------------|-------------|-------|
| **Ceph RADOS GW** | ✅ Full | ✅ Yes (via OIDC) | Most complete alternative; supports `AssumeRoleWithWebIdentity` with OIDC providers |
| **Scality ARTESCA** | ✅ Enterprise only | ✅ Yes | Requires paid enterprise product |
| **Scality CloudServer** | ❌ No | ❌ No | Open source version lacks STS |
| **SeaweedFS** | ✅ Yes (since 3.97) | ✅ Yes (via OIDC) | [PR #7160](https://github.com/seaweedfs/seaweedfs/pull/7160) merged Aug 2025; adds `AssumeRoleWithWebIdentity` with OIDC support |
| **Garage** | ❌ Custom auth | ❌ No | Different auth paradigm, not AWS STS-compatible |
| **OpenIO** | ❓ Unknown | ❓ Unknown | No documented STS support |

**Rejected**:
- **The Hadoop limitation applies regardless of storage backend.** Even Ceph with full STS support would face the same issue: Hadoop S3A connector does not expose custom STS endpoint configuration for `WebIdentityTokenFileCredentialsProvider`.
- Switching storage systems would not solve the core problem—the limitation is in the Hadoop connector, not MinIO.
- Ceph RADOS Gateway is significantly more complex to deploy and operate than MinIO, with no benefit for this use case.

## Consequences

### Positive

- Consistent authentication pattern across all Scout services in on-prem deployments
- Proven, stable approach with existing deployments
- Lower operational overhead for on-prem
- Avoids complexity of mixed authentication patterns within same deployment

### Negative

- Static credentials must be managed and rotated manually for on-prem
- No automatic credential rotation for MinIO
- Less secure than service account-based authentication (credentials stored in K8s secrets)
- Conditional logic required in Ansible to support different auth patterns (on-prem vs AWS)

### AWS Deployment Considerations

- AWS deployments will use IRSA natively (all Hadoop-based services support AWS STS)
- Ansible roles will need conditional configuration:
  - On-prem: Static access keys for MinIO
  - AWS: IRSA environment variables for S3
- Same container images work in both environments; only configuration differs

### Mitigations for Negative Consequences

- Use Ansible Vault for credential encryption
- Implement credential rotation playbook
- Document AWS IRSA configuration for cloud deployments
- Consider HashiCorp Vault integration for dynamic secrets (future enhancement)

## Implementation Notes

### Ansible Conditional Logic

Multiple roles will need conditional configuration based on deployment target:

- **extractor role**: S3 credentials (static keys vs IRSA env vars)
- **hive role**: Metastore S3 configuration
- **trino role**: Delta Lake connector credentials
- **jupyter role**: Spark S3A configuration
- **loki role**: S3 storage backend credentials

Suggested approach: Inventory variable (e.g., `deployment_target: on-prem|aws`) to control auth pattern selection.

## Future Considerations

- **AWS Cloud Deployment**: Use IRSA natively (supported by all services)
- **Hadoop Evolution**: Revisit if Hadoop adds custom STS endpoint support for WebIdentity
- **MinIO Updates**: Monitor for alternative authentication mechanisms
- **Vault Integration**: Consider dynamic secret generation for enhanced security

## References

- [MinIO Operator STS Documentation](https://min.io/docs/minio/kubernetes/upstream/developers/sts-for-operator.html)
- [AWS SDK v2 Service-Specific Endpoints](https://docs.aws.amazon.com/sdkref/latest/guide/feature-ss-endpoints.html)
- [Hadoop S3A Documentation](https://hadoop.apache.org/docs/stable/hadoop-aws/tools/hadoop-aws/index.html)
- [AWS Java SDK v1 Issue #2343](https://github.com/aws/aws-sdk-java/issues/2343) - No custom STS endpoint for WebIdentity
- [AWS Java SDK v2 Issue #1881](https://github.com/aws/aws-sdk-java-v2/issues/1881) - Ability to customize stsClient in WebIdentityCredentialsProvider (resolved with StsWebIdentityTokenFileCredentialsProvider)
- [HADOOP-18154](https://issues.apache.org/jira/browse/HADOOP-18154) - Added WebIdentity support to Hadoop S3A (AWS only)
- [HADOOP-17188](https://issues.apache.org/jira/browse/HADOOP-17188) - Added IRSA support (AWS EKS only)
