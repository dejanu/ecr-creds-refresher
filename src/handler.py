#! /usr/bin/env python3

import base64
import boto3
import kopf
from kubernetes import client, config
import json
import os

try:
    config.load_incluster_config()  # In-cluster: uses service account
except config.ConfigException:
    config.load_kube_config()  # Local testing: uses ~/.kube/config

SECRET_NAME = "aws-registry-209202477790"
REGISTRY = "209202477790.dkr.ecr.us-east-1.amazonaws.com"
AWS_REGION = "us-east-1"

# AWS credentials source - can be configured via environment variables
AWS_CREDENTIALS_SECRET_NAME = os.getenv('AWS_CREDENTIALS_SECRET_NAME', 'ecr-credential-refresher')
AWS_CREDENTIALS_SECRET_NAMESPACE = os.getenv('AWS_CREDENTIALS_SECRET_NAMESPACE', 'default')

def get_aws_credentials_from_secret(secret_name=None, namespace=None):
    """
    Fetch AWS credentials from secret in same namespace
    Falls back to another namespace using Kubernetes API
    """
    secret_name = secret_name or AWS_CREDENTIALS_SECRET_NAME
    namespace = namespace or AWS_CREDENTIALS_SECRET_NAMESPACE
    
    # Try environment variables first (mounted from secret in same namespace)
    env_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    env_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    
    if env_access_key and env_secret_key:
        return env_access_key, env_secret_key
    
    # Fetch from another namespace using k8s API
    try:
        v1 = client.CoreV1Api()
        secret = v1.read_namespaced_secret(secret_name, namespace)
        
        access_key = base64.b64decode(secret.data.get('AWS_ACCESS_KEY_ID', '')).decode('utf-8')
        secret_key = base64.b64decode(secret.data.get('AWS_SECRET_ACCESS_KEY', '')).decode('utf-8')
        
        if access_key and secret_key:
            return access_key, secret_key
        else:
            raise ValueError(f"Secret {namespace}/{secret_name} is missing AWS credentials")
    except Exception as e:
        raise Exception(f"Failed to fetch AWS credentials from {namespace}/{secret_name}: {e}")

@kopf.on.startup()
def startup(logger, **_):
    logger.info("ECR Pull Secret operator started.")
    logger.info(f"AWS Credentials Source: {AWS_CREDENTIALS_SECRET_NAMESPACE}/{AWS_CREDENTIALS_SECRET_NAME}")
    logger.info(f"AWS Region: {AWS_REGION}")
    logger.info(f"ECR Registry: {REGISTRY}")
    
    # Verify AWS credentials are accessible
    try:
        access_key, _ = get_aws_credentials_from_secret()
        logger.info(f"✅ AWS credentials loaded successfully (Access Key: {access_key[:10]}...)")
    except Exception as e:
        logger.error(f"❌ Failed to load AWS credentials: {e}")
        logger.error("Operator will fail when trying to generate ECR tokens!")
    
    # check if any CRs exists in the cluster
    try:
        api = client.CustomObjectsApi()
        resources = api.list_cluster_custom_object(
            group='alchemy.com',
            version='v1alpha1',
            plural='ecrpullsecrets'
        )
        
        cr_count = len(resources.get('items', []))
        if cr_count == 0:
            logger.warning("⚠️  No ECRPullSecret CRs found! Operator is idle.")
            logger.warning("Create a CR to start managing ECR secrets:")
            logger.warning("kubectl apply -f k8s_resources/example-ecrpullsecret.yaml")
        else:
            logger.info(f"✅ Found {cr_count} ECRPullSecret CR(s) to manage")
    except Exception as e:
        logger.error(f"Failed to check for ECRPullSecret resources: {e}")

@kopf.on.resume('alchemy.com', 'v1alpha1', 'ecrpullsecrets')
def resume_monitoring(spec, name, logger, **_):
    """
    Called when operator resumes watching existing ECRPullSecret resources
    Reconciles secrets to ensure they exist and are current
    """
    secret_name = spec.get('secretName', SECRET_NAME)
    target_namespaces = spec.get('namespaces', ['default'])
    
    logger.info(f"▶️  Resuming ECRPullSecret CR '{name}' for namespaces: {', '.join(target_namespaces)}")
    
    # Reconcile each namespace on resume
    for namespace in target_namespaces:
        logger.info(f"Reconciling namespace: {namespace}")
        try:
            ensure_secret(namespace, logger)
            ensure_serviceaccount(namespace, logger)
        except Exception as e:
            logger.error(f"Failed to reconcile namespace {namespace}: {e}")

def generate_dockerconfigjson():
    """
    Get the authorization token for the ECR registry:
    aws ecr get-login-password --region us-east-1
    """
    # Fetch AWS credentials from configured secret
    access_key, secret_key = get_aws_credentials_from_secret()
    
    # Create ECR client with explicit credentials
    ecr = boto3.client(
        "ecr",
        region_name=AWS_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key
    )
    
    token = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    auth = base64.b64decode(token).decode().split(":", 1)
    username = auth[0]
    password = auth[1]

    # Allow REGISTRY to be overridden via environment variable
    registry = os.getenv('REGISTRY', REGISTRY)

    data = {
        "auths": {
            registry: { 
                "username": username,
                "password": password,
                "auth": token,
                "email": "none@no@email.local",
            }
        }
    }

    dockerconfig = base64.b64encode(
        bytes(json.dumps(data), "utf-8")
    ).decode("utf-8")

    return dockerconfig

def ensure_secret(namespace, logger):
    """ 
    Create or replace dockerconfigjson secret in the namespace
    """
    v1 = client.CoreV1Api()
    dockerconfig = generate_dockerconfigjson()

    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=SECRET_NAME),
        type="kubernetes.io/dockerconfigjson",
        data={".dockerconfigjson": dockerconfig},
    )

    try:
        # Try to read the secret first
        v1.read_namespaced_secret(SECRET_NAME, namespace)
        # Secret exists, replace it atomically avoiding race conditions
        v1.replace_namespaced_secret(SECRET_NAME, namespace, body)
        logger.info(f"Updated secret {SECRET_NAME} in {namespace}")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Secret doesn't exist, create it
            v1.create_namespaced_secret(namespace, body)
            logger.info(f"Created secret in {namespace}")
        else:
            # Some other error, re-raise
            raise

def ensure_serviceaccount(namespace, logger):
    """ 
    Patch the default service account in the namespace to use the new ECR pull secret
    """
    v1 = client.CoreV1Api()

    try:
        sa = v1.read_namespaced_service_account("default", namespace)
        sa.image_pull_secrets = [{"name": SECRET_NAME}]
        v1.patch_namespaced_service_account("default", namespace, sa)
        logger.info(f"PATCHED default service account in {namespace}")
    except Exception as e:
        logger.warning(f"Failed to patch default SA in {namespace}: {e}")

@kopf.on.create('alchemy.com', 'v1alpha1', 'ecrpullsecrets')
@kopf.on.update('alchemy.com', 'v1alpha1', 'ecrpullsecrets')
def reconcile_ecr_secret(spec, name, logger, **_):
    """
    Create/Update ECR pull secrets in specified namespaces when CRD is created/updated
    Every namespace in the list of namespaces will be processed
    """
    secret_name = spec.get('secretName', SECRET_NAME)
    target_namespaces = spec.get('namespaces', ['default'])
    
    logger.info(f"Reconciling ECR secret '{secret_name}' for CR '{name}'")
    logger.info(f"Target namespaces: {', '.join(target_namespaces)}")
    
    for namespace in target_namespaces:
        logger.info(f"Processing namespace: {namespace}")
        try:
            ensure_secret(namespace, logger)
            ensure_serviceaccount(namespace, logger)
        except Exception as e:
            logger.error(f"Failed to process namespace {namespace}: {e}")

@kopf.on.delete('alchemy.com', 'v1alpha1', 'ecrpullsecrets')
def delete_ecr_secret(spec, name, logger, **_):
    """
    Handle deletion of ECRPullSecret CR
    Note: Secrets are NOT automatically deleted from namespaces (manual cleanup required)
    """
    secret_name = spec.get('secretName', SECRET_NAME)
    target_namespaces = spec.get('namespaces', ['default'])
    
    logger.info(f"ECRPullSecret CR '{name}' deleted")
    logger.info(f"Secrets '{secret_name}' in namespaces {target_namespaces} are NOT automatically removed")
    logger.info(f"Manual cleanup: kubectl delete secret {secret_name} -n <namespace>")
    
    # If you want to auto-delete secrets, uncomment this:
    # v1 = client.CoreV1Api()
    # for namespace in target_namespaces:
    #     try:
    #         v1.delete_namespaced_secret(secret_name, namespace)
    #         logger.info(f"Deleted secret {secret_name} from {namespace}")
    #     except client.exceptions.ApiException as e:
    #         if e.status != 404:
    #             logger.warning(f"Failed to delete secret in {namespace}: {e}")

@kopf.timer('alchemy.com', 'v1alpha1', 'ecrpullsecrets', interval=6 * 3600, initial_delay=6 * 3600)
def refresh_ecr_secrets(spec, name, logger, **_):
    """
    Refresh ECR secrets every 6 hours based on CRD configuration
    Only starts after initial 6-hour delay (not immediately on CR creation)
    When the CR is created, Kopf triggers:
    @kopf.timer - without initial_delay, runs immediately on first detection
    @kopf.on.create - Runs when CR is created
    """
    import datetime
    secret_name = spec.get('secretName', SECRET_NAME)
    target_namespaces = spec.get('namespaces', ['default'])
    
    logger.info(f"⏰ Timer triggered at {datetime.datetime.now().isoformat()}")
    logger.info(f"Refreshing ECR secret '{secret_name}' for CR '{name}'")
    
    for namespace in target_namespaces:
        logger.info(f"Refreshing namespace: {namespace}")
        try:
            ensure_secret(namespace, logger)
            ensure_serviceaccount(namespace, logger)
        except Exception as e:
            logger.error(f"Failed to refresh namespace {namespace}: {e}")

if __name__ == "__main__":
    print("Testing ECR token fetch...")
    try:
        dockerconfig = generate_dockerconfigjson()
        print("✅ Successfully fetched ECR auth token!")
        print(f"\nGenerated dockerconfig length: {len(dockerconfig)} characters")
        
        # Decode and display the auth data (without sensitive info)
        decoded = json.loads(base64.b64decode(dockerconfig))
        print(f"Registry: {list(decoded['auths'].keys())[0]}")
        print(f"Username: {decoded['auths'][REGISTRY]['username']}")
        print(f"Password length: {len(decoded['auths'][REGISTRY]['password'])} characters")
    except Exception as e:
        print(f"❌ Error: {e}")