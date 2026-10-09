# Shared settings for the GCP scripts (template: set the values through the environment).
# Never switches the global gcloud config; everything here pins project (and account) per command.
#
#   GCP_PROJECT   required   GCP project id
#   GCP_ACCOUNT   optional   gcloud account to use (defaults to gcloud's active account)
#   GCP_REGION / GCP_ZONE / GCP_VM / GCP_IP_NAME   optional overrides
: "${GCP_PROJECT:?set GCP_PROJECT to your GCP project id (see README, Configuration)}"
export CLOUDSDK_CORE_PROJECT="$GCP_PROJECT"
if [ -n "${GCP_ACCOUNT:-}" ]; then export CLOUDSDK_CORE_ACCOUNT="$GCP_ACCOUNT"; fi
REGION="${GCP_REGION:-us-east1}"
ZONE="${GCP_ZONE:-us-east1-b}"
VM="${GCP_VM:-cs-wordpress}"
IP_NAME="${GCP_IP_NAME:-cs-wordpress-ip}"
