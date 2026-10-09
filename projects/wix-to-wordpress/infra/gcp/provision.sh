#!/usr/bin/env bash
# Creates the free-tier WordPress VM (e2-micro + 30GB pd-standard in us-east1), static IP,
# firewall rules, and a weekly snapshot schedule. Idempotent-ish: skips things that exist.
# Guest attributes are enabled so `gcloud compute ssh` can read the VM's SSH host keys and pin them in
# ~/.ssh/google_compute_known_hosts on the first connection, instead of trusting whatever key answers.
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

exists() { "$@" >/dev/null 2>&1; }

exists gcloud compute addresses describe $IP_NAME --region $REGION || \
  gcloud compute addresses create $IP_NAME --region $REGION --network-tier PREMIUM

exists gcloud compute firewall-rules describe cs-allow-web || \
  gcloud compute firewall-rules create cs-allow-web --network default \
    --allow tcp:80,tcp:443 --target-tags cs-web --source-ranges 0.0.0.0/0

# SSH only from Google's IAP range (use: gcloud compute ssh --tunnel-through-iap)
exists gcloud compute firewall-rules describe cs-allow-iap-ssh || \
  gcloud compute firewall-rules create cs-allow-iap-ssh --network default \
    --allow tcp:22 --target-tags cs-web --source-ranges 35.235.240.0/20

exists gcloud compute resource-policies describe cs-weekly-snapshot --region $REGION || \
  gcloud compute resource-policies create snapshot-schedule cs-weekly-snapshot \
    --region $REGION --weekly-schedule sunday --start-time 08:00 --max-retention-days 28 \
    --on-source-disk-delete keep-auto-snapshots

if ! exists gcloud compute instances describe $VM --zone $ZONE; then
  gcloud compute instances create $VM --zone $ZONE \
    --machine-type e2-micro \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 30GB --boot-disk-type pd-standard \
    --address $IP_NAME --network-tier PREMIUM \
    --tags cs-web \
    --metadata-from-file startup-script=startup.sh \
    --metadata enable-guest-attributes=TRUE \
    --shielded-secure-boot --shielded-vtpm
  gcloud compute disks add-resource-policies $VM --zone $ZONE --resource-policies cs-weekly-snapshot
fi

gcloud compute addresses describe $IP_NAME --region $REGION --format='value(address)'
