## ECR-creds-refresher


```bash
# add repo
helm repo add demo https://dejanu.github.io/ecr-creds-refresher/packages
helm search repo demo

# install operator 
helm install operator ecr-creds-refresher --namespace operator \
  --create-namespace \
  --set aws.credentials.secretName=ecr-credential-refresher \
  --set aws.credentials.namespace=default \
  --set aws.region=us-east-1 \
  --set aws.registry=YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

```


1. Operator Startup
   ↓
2. Reads AWS Credentials (from configured secret in any namespace)
   ↓
3. Watches for ECRPullSecret CRs (Custom Resources)
   ↓
4. When CR Created/Updated/Resumed:
   - Fetches fresh ECR token from AWS (valid 12 hours)
   - Creates/updates Docker registry secret in each listed namespace
   - Patches default ServiceAccount to use the secret
   ↓
5. Timer (Every 6 Hours):
   - Re-fetches fresh ECR token
   - Updates all secrets in all namespaces from all CRs
   ↓
6. When CR Deleted:
   - Logs cleanup instructions (doesn't auto-delete secrets)
  
### Prereq

* A secret that holds the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` the secret can be in any namespace
  

