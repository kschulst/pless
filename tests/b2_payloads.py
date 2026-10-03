"""Real Backblaze responses, captured while #16 was being settled.

Kept in one place because both the decision tests and the sequence tests need
them, and because invented payload shapes are a failure mode this project has
already had: a fake that drifts from what the API returns is a test that passes
while the code breaks.
"""

from __future__ import annotations

# Captured from a real account. v3 nests the account information under
# `apiInfo.storageApi`, where v2 had it at the top level.
AUTHORIZE_V3 = {
    "accountId": "f434a75d3f3c",
    "authorizationToken": "4_00f434a75d3f3c0000000005_01a2b3c4_d5e6f7_acct_SECRET",
    "applicationKeyExpirationTimestamp": None,
    "apiInfo": {
        "storageApi": {
            "absoluteMinimumPartSize": 5000000,
            "apiUrl": "https://api003.backblazeb2.com",
            "bucketId": None,
            "bucketName": None,
            "capabilities": [
                "deleteFiles",
                "listBuckets",
                "listFiles",
                "readBucketRetentions",
                "readFiles",
                "writeBucketRetentions",
                "writeBuckets",
                "writeFiles",
                "writeKeys",
            ],
            "downloadUrl": "https://f003.backblazeb2.com",
            "infoType": "storageApi",
            "namePrefix": None,
            "recommendedPartSize": 100000000,
            "s3ApiUrl": "https://s3.eu-central-003.backblazeb2.com",
        }
    },
}

AUTHORIZE_V2 = {
    "accountId": "f434a75d3f3c",
    "authorizationToken": "4_00f434a75d3f3c_v2_SECRET",
    "apiUrl": "https://api003.backblazeb2.com",
    "s3ApiUrl": "https://s3.eu-central-003.backblazeb2.com",
    "allowed": {
        "bucketId": None,
        "bucketName": None,
        "capabilities": ["listBuckets", "writeBuckets", "writeBucketRetentions", "writeKeys"],
    },
}

# What a bucket-restricted machine key sees before ADR 0020's capability: the
# field is present and its value withheld.
LOCK_WITHHELD = {"fileLockConfiguration": {"isClientAuthorizedToRead": False, "value": None}}

LOCK_OFF = {
    "fileLockConfiguration": {
        "isClientAuthorizedToRead": True,
        "value": {"defaultRetention": {"mode": None, "period": None}, "isFileLockEnabled": False},
    }
}

# Exactly what `b2_create_bucket` with fileLockEnabled returns: lock on,
# nothing retained.
LOCK_ON_NO_RETENTION = {
    "fileLockConfiguration": {
        "isClientAuthorizedToRead": True,
        "value": {"defaultRetention": {"mode": None, "period": None}, "isFileLockEnabled": True},
    }
}

LOCK_GOVERNANCE_90 = {
    "fileLockConfiguration": {
        "isClientAuthorizedToRead": True,
        "value": {
            "defaultRetention": {"mode": "governance", "period": {"duration": 90, "unit": "days"}},
            "isFileLockEnabled": True,
        },
    }
}

LOCK_COMPLIANCE = {
    "fileLockConfiguration": {
        "isClientAuthorizedToRead": True,
        "value": {
            "defaultRetention": {"mode": "compliance", "period": {"duration": 30, "unit": "days"}},
            "isFileLockEnabled": True,
        },
    }
}


def governance_lock(duration: int, unit: str = "days") -> dict:
    """A bucket reporting governance retention for an arbitrary period."""
    return {
        "fileLockConfiguration": {
            "isClientAuthorizedToRead": True,
            "value": {
                "defaultRetention": {
                    "mode": "governance",
                    "period": {"duration": duration, "unit": unit},
                },
                "isFileLockEnabled": True,
            },
        }
    }
