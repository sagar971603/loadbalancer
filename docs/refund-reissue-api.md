# Refund reissue API

Refund reissue is a stateful Newtool2 WebSocket workflow. Authenticate and log in first, then keep the same WebSocket and `session_id` through OTP submission.

## Actions

Initialize and obtain `workflowDetails` plus `eligible_bank_details`:

```json
{"action":"refund_reissue_init","session_id":"SESSION","data":{}}
```

Send the Aadhaar OTP:

```json
{
  "action":"refund_reissue_send_otp",
  "session_id":"SESSION",
  "data":{
    "ack_num":"ACKNOWLEDGEMENT-NUMBER",
    "bank_account_number":"OPTIONAL-ACCOUNT",
    "assessment_year":"2024",
    "representative_email":"optional@example.com"
  }
}
```

`acknowledgment_number` is also accepted. The bank account and assessment year may be omitted when the portal response supplies an eligible default.

Submit the OTP using the same live session:

```json
{
  "action":"refund_reissue_submit",
  "session_id":"SESSION",
  "data":{"otp":"123456"}
}
```

Fetch current refund-reissue status:

```json
{"action":"refund_reissue_status","session_id":"SESSION","data":{}}
```

Check both the WebSocket envelope and `result.success`. Do not reconnect, log in again, or switch servers between sending and submitting the OTP because the pending portal context is held in the active Newtool2 session.

Use only an authorized taxpayer account. OTP requests contact the taxpayer, and successful submission changes portal data. Avoid repeated OTP requests because the portal enforces expiry and resend limits.
