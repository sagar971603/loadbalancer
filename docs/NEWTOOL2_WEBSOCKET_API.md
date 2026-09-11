# Newtool2 WebSocket API

Frontend integration contract, verified against the production source on 3 September 2026.

## 1. Production endpoints

| Purpose | URL |
|---|---|
| WebSocket | `wss://newtool2.fskindia.com/ws` |
| Health check | `https://newtool2.fskindia.com/health` |

All product operations use the single WebSocket endpoint. The health check is ordinary HTTP.

## 2. Security requirement

Do not put the permanent API key in public browser JavaScript, a mobile bundle, logs, analytics, URLs, or screenshots. The recommended architecture is:

```text
Browser/mobile frontend -> trusted Laravel backend -> Newtool2 WebSocket
```

The Laravel backend should read the key from server-side configuration and send it only in the WebSocket `authenticate` message.

## 3. Connection and authentication

After opening the socket, the server sends:

```json
{
  "type": "connection",
  "status": "connected",
  "connection_id": "generated-id",
  "timestamp": "2026-09-03T10:00:00",
  "message": "Please authenticate using 'authenticate' action with your API key"
}
```

Authenticate before sending any other action:

```json
{
  "action": "authenticate",
  "api_key": "YOUR_API_KEY"
}
```

Success:

```json
{
  "type": "response",
  "action": "authenticate",
  "success": true,
  "message": "Authenticated as client",
  "timestamp": "2026-09-03T10:00:01"
}
```

An invalid key returns an error and closes the socket with code `1008`.

## 4. Standard message envelopes

Client request:

```json
{
  "action": "action_name",
  "session_id": "LOGIN_SESSION_ID_WHEN_REQUIRED",
  "data": {}
}
```

Most login-required actions return the service result under `result`:

```json
{
  "type": "response",
  "action": "prefill",
  "result": {
    "success": true,
    "data": {},
    "message": "...",
    "error": null,
    "status_code": 200
  }
}
```

Validation or server error:

```json
{
  "type": "error",
  "action": "prefill",
  "error": "Missing year parameter"
}
```

Important response rules:

- A business/portal failure can be `type: "response"` with `success: false`, or with `result.success: false`.
- A validation/transport failure is normally `type: "error"`.
- Check both the outer response and the nested `result`/`data`; do not treat every `type: "response"` as success.
- Portal-derived `data`, `messages`, message codes, and optional fields can change by case.
- The protocol does not echo a frontend request ID. Avoid sending multiple simultaneous requests with the same action on one socket.

## 5. Session and queue rules

There are three different session IDs. They are not interchangeable.

| Session | Created by | Where continuations send it | Lifetime |
|---|---|---|---|
| Login session | `login` | Top-level `session_id` | Up to 2 hours, but removed immediately when its WebSocket disconnects |
| Forgot-password session (`fp_...`) | `forgot_password_init` | `data.session_id` | Same WebSocket only; removed on success or disconnect |
| PAN-link session (`pl_...`) | `pan_link` | `data.session_id` | Up to 1 hour, but removed when its WebSocket disconnects |

Operational rules:

- Keep the same WebSocket open for the complete multi-step operation.
- A reconnect creates a new connection and clears the old connection's login, forgot-password, and PAN-link state. Restart the affected flow after reconnecting.
- The application closes a socket after 300 seconds without an application JSON message. Protocol-level ping frames may not reset this timer.
- Login and `check_aadhaar_status` can wait up to 300 seconds for an outgoing-IP slot. The socket may be silent while waiting.
- When the queue expires, the response contains `success: false`, `queued: true`, and an error.
- Keep frontend/Laravel WebSocket and proxy timeouts longer than 300 seconds.

## 6. Complete action reference

### Connection and portal login

| Action | Session required | `data` fields | Result |
|---|---:|---|---|
| `authenticate` | No | None; send `api_key` at the top level | Authenticates the WebSocket |
| `login` | No | `pan` (required), `password` (required) | Returns a top-level login `session_id` and portal login data |
| `logout` | Login | No data required; send login ID at top level | Releases the portal session and outgoing-IP slot |

Login request:

```json
{
  "action": "login",
  "data": {
    "pan": "ABCDE1234F",
    "password": "USER_PORTAL_PASSWORD"
  }
}
```

Login success:

```json
{
  "type": "response",
  "action": "login",
  "success": true,
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "success": true,
    "user_id": "ABCDE1234F"
  }
}
```

Logout:

```json
{
  "action": "logout",
  "session_id": "LOGIN_SESSION_ID",
  "data": {}
}
```

### Pre-login Aadhaar status

| Action | Login required | `data` fields | Result |
|---|---:|---|---|
| `check_aadhaar_status` | No | `pan`, `aadhaar_number` | Checks PAN–Aadhaar status without portal login |

```json
{
  "action": "check_aadhaar_status",
  "data": {
    "pan": "ABCDE1234F",
    "aadhaar_number": "234567890123"
  }
}
```

PAN must match five letters, four digits, one letter. Aadhaar must contain 12 digits and start with `2`–`9`.

Response:

```json
{
  "type": "response",
  "action": "check_aadhaar_status",
  "success": true,
  "data": {
    "success": true,
    "payment_verify": false,
    "messages": [],
    "data": {}
  }
}
```

### Forgot password

| Action | Login required | `data` fields | Result |
|---|---:|---|---|
| `forgot_password_init` | No | `pan`, `method`; `dob` required for `email_mobile` | Sends OTP and returns `fp_...` session ID |
| `forgot_password_rerequest_otp` | No | `session_id`, `method`; `dob` required for `email_mobile` | Re-sends OTP |
| `forgot_password_verify` | No | `session_id`, `mobile_otp`, `new_password`; `email_otp` required for `email_mobile` | Verifies OTP(s) and changes password |

Allowed `method` values are `email_mobile` and `aadhaar`. DOB format is `YYYY-MM-DD`.

Email/mobile flow:

```json
{
  "action": "forgot_password_init",
  "data": {
    "pan": "ABCDE1234F",
    "method": "email_mobile",
    "dob": "1990-01-31"
  }
}
```

```json
{
  "action": "forgot_password_verify",
  "data": {
    "session_id": "fp_SESSION_ID",
    "mobile_otp": "123456",
    "email_otp": "654321",
    "new_password": "NEW_PORTAL_PASSWORD"
  }
}
```

Aadhaar flow uses the same actions but omits `dob` and `email_otp`.

### PAN–Aadhaar linking and payment

| Action | Login required | `data` fields | Result |
|---|---:|---|---|
| `pan_link` | No | `pan`, `aadhaar_number`, `mobile`, `password` | Starts linkage/payment flow and returns `pl_...` session ID |
| `pan_link_continue` | No | `session_id`, `otp` | Validates payment OTP, creates challan/payment, returns bank redirect data |
| `generate_pan_link_otp` | No | `session_id`, `name` | Intended to request linking OTP; see warning below |
| `verify_pan_link_otp` | No | `session_id`, `otp` | Validates the linking OTP |

Start:

```json
{
  "action": "pan_link",
  "data": {
    "pan": "ABCDE1234F",
    "aadhaar_number": "234567890123",
    "mobile": "9999999999",
    "password": "USER_PORTAL_PASSWORD"
  }
}
```

Continue after payment OTP:

```json
{
  "action": "pan_link_continue",
  "data": {
    "session_id": "pl_SESSION_ID",
    "otp": "123456"
  }
}
```

Successful continuation can return:

```json
{
  "type": "response",
  "action": "pan_link_continue",
  "success": true,
  "session_id": "pl_SESSION_ID",
  "message": "...",
  "error": null,
  "data": {
    "challan": {},
    "payment": {},
    "redirect_url": "https://bank.example/...",
    "reqjson": "..."
  }
}
```

Current backend warning: do not integrate `generate_pan_link_otp` yet. The handler validates `data.name` but currently does not pass it to the underlying method, so it can fail with a missing-argument error. `check_aadhaar_status`, `pan_link`, and `pan_link_continue` are separate paths and are not affected by that handler issue.

### Login-required ITR and profile actions

Every request in this section must send the login ID as the top-level `session_id`. `data.pan` is optional and defaults to the PAN used at login.

| Action | Required `data` fields | Optional fields / notes |
|---|---|---|
| `filling` | `year` | Returns tax-deposit/filling data. Action spelling is exactly `filling` |
| `prefill` | `year` | Returns prefill data |
| `itr_status` | None | Returns ITR status for the logged-in PAN |
| `active_filings` | None | Returns filings available for e-verification |
| `e_verify_active_filings` | `year`, `acknowledgment_number` | Starts e-verification for the selected filing |
| `check_aadhaar` | None | Checks whether Aadhaar is linked for the logged-in PAN |
| `send_otp` | None | Sends Aadhaar OTP for the logged-in PAN |
| `everify_otp_verify` | `otp`, `acknowledgment_number` | `verify_now` defaults to `false` |
| `revise_filing` | `year` | Validates/starts revised filing |
| `download_itr` | `acknowledgment_number` | Returns the portal's download response; not PDF bytes |
| `get_itr_receipt` | `ackn_no`, `year` | Returns a PDF as top-level `pdf_base64` on success |
| `submit_itr_form` | `itr_type`, `year`, `form_type`, `json_link` | `fetch_from_url` defaults to `true`; `verify_now` defaults to `false` |

Standard authenticated example:

```json
{
  "action": "prefill",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "year": 2026
  }
}
```

`submit_itr_form` example:

```json
{
  "action": "submit_itr_form",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "itr_type": "original",
    "year": 2026,
    "form_type": "ITR1",
    "json_link": "https://trusted.example/return.json",
    "fetch_from_url": true,
    "verify_now": false
  }
}
```

Known `itr_type` mappings in the service are `original`, `revised`, `belated`, `updated`, `modified`, and `after-notice`. The selected value must also be available for the requested year on the portal.

If `fetch_from_url` is `false`, send the parsed ITR JSON object directly in `json_link` despite the field name.

ITR receipt success is a special response shape:

```json
{
  "type": "response",
  "action": "get_itr_receipt",
  "success": true,
  "filename": "server-side-path.pdf",
  "pdf_base64": "JVBERi0xLjQK...",
  "message": "ITR receipt PDF sent successfully."
}
```

The frontend should use `pdf_base64` and should not depend on the server-side `filename` path.

### Challans and advance tax

| Action | Required `data` fields | Optional fields / notes |
|---|---|---|
| `get_all_challans` | None | Optional `crn` filters the history |
| `get_challan_details` | `year` | Returns challan details for the assessment year |
| `pay_advance_tax` | `year`, `basicTax`, `totalAmt`, `totalAmtWord` | Amount fields may be flat or inside `payment_details`; `old_income_tax` defaults to `true` |

```json
{
  "action": "pay_advance_tax",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "year": "2026-27",
    "old_income_tax": true,
    "payment_details": {
      "basicTax": 1000,
      "surCharge": 0,
      "eduCess": 0,
      "interest": 0,
      "penalty": 0,
      "others": 0,
      "totalAmt": 1000,
      "totalAmtWord": "INR One Thousand"
    }
  }
}
```

Successful payment preparation can include a bank `redirect_url`. Opening that URL and completing the bank flow is a frontend responsibility.

### Bank accounts and prevalidation

| Action | Required `data` fields | Optional fields / notes |
|---|---|---|
| `get_bank_accounts` | None | Returns bank accounts for the logged-in PAN |
| `prevalidate_bank` | `account_details` | Starts account addition/prevalidation and sends Aadhaar OTP |
| `prevalidate_bank_continue` | `otp` | Validates OTP and submits prevalidation using state created by `prevalidate_bank` |

Current compatibility payload:

```json
{
  "action": "prevalidate_bank",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "account_details": {
      "accountNo": "1234567890",
      "accountNumber": "1234567890",
      "ifsc": "ABCD0123456",
      "accountType": "savings",
      "accountHolderType": "individual"
    }
  }
}
```

Send both `accountNo` and `accountNumber` with the same value. The current multi-step backend reads `accountNo` while adding the account and `accountNumber` during the final prevalidation submission. Accepted defaults are `savings` and `individual`; other values map to the portal's alternate account types.

Continue:

```json
{
  "action": "prevalidate_bank_continue",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "otp": "123456"
  }
}
```

### Refund reissue

All four actions require the same top-level login `session_id`.

| Action | Required `data` fields | Optional fields / notes |
|---|---|---|
| `refund_reissue_init` | None | Returns refund-failure workflows and eligible bank accounts |
| `refund_reissue_send_otp` | `ack_num` or `acknowledgment_number` | Optional `bank_account_number`, `assessment_year` or `year`, `representative_email` |
| `refund_reissue_submit` | `otp` | Optional `representative_email`; uses context saved by the send-OTP action |
| `refund_reissue_status` | None | Returns submitted refund-reissue requests/status |

Recommended sequence:

```json
{
  "action": "refund_reissue_init",
  "session_id": "LOGIN_SESSION_ID",
  "data": {}
}
```

Select an acknowledgment and eligible bank account from `result.data`, then:

```json
{
  "action": "refund_reissue_send_otp",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "ack_num": "ACKNOWLEDGMENT_NUMBER",
    "bank_account_number": "1234567890",
    "assessment_year": "2025-26"
  }
}
```

```json
{
  "action": "refund_reissue_submit",
  "session_id": "LOGIN_SESSION_ID",
  "data": {
    "otp": "123456"
  }
}
```

Do not discard the login socket between these steps; refund context is stored in that login's in-memory client.

## 7. Complete allowed-action list

The server currently accepts exactly these 33 action names:

```text
authenticate
login
logout
forgot_password_init
forgot_password_verify
prefill
itr_status
active_filings
e_verify_active_filings
check_aadhaar
check_aadhaar_status
send_otp
revise_filing
download_itr
filling
forgot_password_rerequest_otp
everify_otp_verify
get_all_challans
get_challan_details
get_bank_accounts
submit_itr_form
get_itr_receipt
pan_link
pan_link_continue
generate_pan_link_otp
verify_pan_link_otp
pay_advance_tax
prevalidate_bank
prevalidate_bank_continue
refund_reissue_init
refund_reissue_send_otp
refund_reissue_submit
refund_reissue_status
```

## 8. Minimal Laravel/PHP flow

Use a server-side WebSocket client library already approved in the Laravel project. The logical sequence is:

```php
$socket->send(json_encode([
    'action' => 'authenticate',
    'api_key' => config('services.newtool2.key'),
]));

// Wait for authenticate success before login.
$socket->send(json_encode([
    'action' => 'login',
    'data' => [
        'pan' => $pan,
        'password' => $password,
    ],
]));

// Keep this same socket and store the returned login session ID.
$socket->send(json_encode([
    'action' => 'itr_status',
    'session_id' => $loginSessionId,
    'data' => [],
]));
```

Do not log the API key, portal password, Aadhaar number, OTP, session IDs, cookies, tokens, payment request JSON, or full portal responses.

## 9. Frontend handling checklist

- Connect using `wss://`, then wait for the `connection` message.
- Authenticate once and wait for success before any product action.
- Keep one socket for the whole login/OTP/payment/refund flow.
- Store session IDs server-side and keep the three session types separate.
- Allow at least 310 seconds for queued login/status requests.
- Disable duplicate-submit buttons while an action is in flight.
- Treat `type: "error"`, outer `success: false`, and nested `result.success: false` as failures.
- Display safe `message` text; do not expose raw stack traces or complete portal payloads.
- On socket close, discard its session IDs and restart the affected flow.
- Base64-decode `pdf_base64` for ITR receipts.
- `generate_pan_link_otp` requires `name`; `verify_pan_link_otp` validates the OTP and submits the final linkage request on the same socket.
