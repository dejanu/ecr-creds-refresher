# ECR Pull Secret Operator Helm Chart

Kubernetes operator for automatically managing AWS ECR Docker registry pull secrets.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- AWS ECR registry
- AWS credentials with `ecr:GetAuthorizationToken` permission

## Installing the Chart

### 1. Ensure AWS Credentials Secret Exists

The operator needs AWS credentials to fetch ECR tokens. Create a secret:

```bash
kubectl create secret generic ecr-credential-refresher \
  --from-literal=AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY \
  --from-literal=AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY \
  --namespace=default
```

### 2. Install the Chart

```bash
# Install in 'operator' namespace
helm install ecr-operator ./ecr-creds-refresher \
  --namespace operator \
  --create-namespace \
  --set aws.credentials.secretName=ecr-credential-refresher \
  --set aws.credentials.namespace=default \
  --set aws.region=us-east-1 \
  --set aws.registry=YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com
```

### 3. Create ECRPullSecret Custom Resource

```bash
cat <<EOF | kubectl apply -f -
apiVersion: alchemy.com/v1alpha1
kind: ECRPullSecret
metadata:
  name: ecr-config
spec:
  secretName: aws-registry-YOUR_ACCOUNT_ID
  namespaces:
    - default
    - production
EOF
```

## Configuration

### Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `replicaCount` | int | `1` | Number of operator replicas |
| `image.repository` | string | `dejanualex/ecr-creds-refresher` | Container image repository |
| `image.tag` | string | `""` (uses appVersion) | Container image tag |
| `image.pullPolicy` | string | `IfNotPresent` | Image pull policy |
| `aws.region` | string | `us-east-1` | AWS region |
| `aws.registry` | string | - | ECR registry URL |
| `aws.credentials.secretName` | string | `ecr-credential-refresher` | Name of AWS credentials secret |
| `aws.credentials.namespace` | string | `default` | Namespace of AWS credentials secret |
| `serviceAccount.create` | bool | `true` | Create service account |
| `serviceAccount.name` | string | `ecr-operator` | Service account name |
| `rbac.create` | bool | `true` | Create RBAC resources |
| `resources.limits.memory` | string | `128Mi` | Memory limit |
| `resources.limits.cpu` | string | `200m` | CPU limit |
| `resources.requests.memory` | string | `64Mi` | Memory request |
| `resources.requests.cpu` | string | `100m` | CPU request |

### Example values.yaml

```yaml
aws:
  region: "us-west-2"
  registry: "123456789.dkr.ecr.us-west-2.amazonaws.com"
  credentials:
    secretName: "my-aws-creds"
    namespace: "security"

resources:
  limits:
    memory: 256Mi
    cpu: 500m
  requests:
    memory: 128Mi
    cpu: 200m
```

## Upgrading

```bash
helm upgrade ecr-operator ./ecr-creds-refresher \
  --namespace operator \
  --values custom-values.yaml
```

## Uninstalling

### ⚠️ Important: What Helm Does NOT Delete

When you run `helm uninstall`, Helm will **NOT** automatically delete:
- **CRDs** (Custom Resource Definitions)
- **CRs** (Custom Resources - your ECRPullSecret instances)
- **Secrets** created by the operator

This is intentional Helm behavior to prevent accidental data loss.

### Uninstall Steps

```bash
# 1. Uninstall the operator
helm uninstall ecr-operator --namespace operator

# 2. Delete Custom Resources (if you want to remove them)
kubectl delete ecrpullsecrets --all

# 3. Delete the CRD (WARNING: This also deletes ALL ECRPullSecret resources!)
kubectl delete crd ecrpullsecrets.alchemy.com

# 4. Clean up operator-created secrets (optional)
# List all secrets created by the operator
kubectl get secrets -A | grep aws-registry

# Delete specific secrets
kubectl delete secret aws-registry-YOUR_ACCOUNT_ID -n <namespace>
```

## How It Works

1. Operator watches for `ECRPullSecret` custom resources
2. For each namespace listed in the CR:
   - Fetches fresh ECR authorization token from AWS (valid 12 hours)
   - Creates/updates Docker registry secret
   - Patches default ServiceAccount to use the secret
3. Timer refreshes tokens every 6 hours automatically

## Troubleshooting

### Check operator logs
```bash
kubectl logs -n operator -l app.kubernetes.io/name=ecr-creds-refresher -f
```

### Verify CRD is installed
```bash
kubectl get crd ecrpullsecrets.alchemy.com
```

### Check RBAC permissions
```bash
kubectl auth can-i list ecrpullsecrets --as=system:serviceaccount:operator:ecr-operator
kubectl auth can-i create secrets --as=system:serviceaccount:operator:ecr-operator -n default
```

### Verify AWS credentials
```bash
kubectl get secret -n default ecr-credential-refresher -o jsonpath='{.data}' | jq keys
```

## Support

- GitHub Issues: https://github.com/dejanu/operatorecr/issues
- Documentation: https://github.com/dejanu/operatorecr

