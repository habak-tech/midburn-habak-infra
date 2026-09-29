# Google Cloud Infrastructure

```
POST https://storage.googleapis.com/storage/v1/b?project=midburn-habak
{
  "encryption": {},
  "iamConfiguration": {
    "uniformBucketLevelAccess": {
      "enabled": true
    },
    "publicAccessPrevention": "enforced"
  },
  "location": "US",
  "name": "midburn-habak1-server-backups",
  "softDeletePolicy": {
    "retentionDurationSeconds": "0"
  },
  "storageClass": "STANDARD"
}
```

* create midburn-habak1-server service account, without any permissions
* on the midburn-habak1-server-backups bucket, add the midburn-habak1-server service account with the role of Storage Object Admin
