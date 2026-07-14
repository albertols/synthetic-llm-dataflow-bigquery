"""Budget alert -> detach billing account (the hard cost cap).

Deployed by 04_budget_killswitch.sh (gen2, python311, trigger: budget-alerts
topic). Recovery after it fires: README.md 'Kill-switch recovery'.
"""
from __future__ import annotations

import base64
import json
import os

import functions_framework
from google.cloud import billing_v1

from logic import should_kill

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
KILL_THRESHOLD = float(os.environ.get("KILL_THRESHOLD", "0.9"))


@functions_framework.cloud_event
def on_budget_alert(cloud_event):
    payload = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode())
    cost, budget = payload.get("costAmount"), payload.get("budgetAmount")
    if not should_kill(payload, KILL_THRESHOLD):
        print(f"below threshold: {cost}/{budget}")
        return
    client = billing_v1.CloudBillingClient()
    name = f"projects/{PROJECT_ID}"
    info = client.get_project_billing_info(name=name)
    if not info.billing_enabled:
        print("billing already disabled — nothing to do")
        return
    client.update_project_billing_info(
        name=name,
        project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=""),
    )
    print(f"BILLING DETACHED for {PROJECT_ID} at {cost}/{budget}")
