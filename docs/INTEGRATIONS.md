# Integration setup

For a clone with `backend/.env` supplied by the project owner, follow the [local setup guide](../README.md#run-a-fresh-clone-locally) and go straight to the [live testing checklist](#live-testing-checklist). The settings below are for configuring or troubleshooting an integration. Restart FastAPI after changing its environment.

## Email and mobile verification

Generate an `OTP_SECRET` and put it in the backend environment. The backend requires at least 32 bytes.

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Patients and doctors verify their email and mobile number before using protected features. On the verification page, select **Change** to correct an unverified contact. Codes expire after 10 minutes and can be resent after one minute. Password reset uses the verified email on the account. Guest access uses SMS only.

### Gmail SMTP for local use

1. Enable 2-Step Verification and create a Google App Password for the sending Gmail account. [Google App Password instructions](https://support.google.com/accounts/answer/185833)
2. Set `OTP_EMAIL_PROVIDER=smtp`, `SMTP_HOST=smtp.gmail.com` and `SMTP_PORT=587`.
3. Set `SMTP_USERNAME` to the full Gmail address and `SMTP_PASSWORD` to the App Password with display spaces removed. If you set `OTP_EMAIL_FROM`, use the same Gmail address, for example `MediConnect <sender@gmail.com>`.
4. Restart the backend and request a code from the verification page.

If the request fails with SMTP 535, check that the App Password belongs to the account in `SMTP_USERNAME`. If the request succeeds, check the address on the page and the recipient's spam folder. [Gmail SMTP errors](https://support.google.com/mail/answer/3726730)

### Resend over HTTPS

Set `OTP_EMAIL_PROVIDER=resend`, `RESEND_API_KEY` and `OTP_EMAIL_FROM`. Use a sender on your verified domain. The Render free plan blocks SMTP ports, so use an HTTPS provider for that deployment. [Resend API](https://resend.com/docs/api-reference/emails/send-email) · [Render free service limits](https://render.com/docs/free#other-limitations)

### Twilio Programmable Messaging

Use the Account SID and Auth Token from the Twilio account that owns the Messaging Service. Put them in `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`, then put the service's `MG…` SID in `TWILIO_MESSAGING_SERVICE_SID`. Add an SMS-capable sender to the service's Sender Pool and enable the destination country. Enter the tester's own mobile number with its country code in MediConnect. [Twilio Messaging Services](https://www.twilio.com/docs/messaging/services) · [India SMS guidelines](https://www.twilio.com/en-us/guidelines/in/sms)

MediConnect sends a custom OTP body. Use an account and sender that permit it. Twilio trial restrictions can require pre-approved recipients and message templates. Check the message status and error code in the Twilio console when the SMS does not arrive. [Twilio trial restrictions](https://www.twilio.com/docs/usage/trials)

## Checkout, refunds and video visits

- Use a Razorpay Test Mode key and secret as `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`. Enable automatic capture. To receive `payment.captured` events, configure a public HTTPS webhook at `/api/v1/payments/razorpay/webhook` and set its signing secret as `RAZORPAY_WEBHOOK_SECRET`. [Razorpay integration guidance](https://razorpay.com/security/checklist/)
- A checkout holds its slot for up to 15 minutes. The backend can reconcile captured payments after the browser closes. Guest checkout uses the same payment settings and is available for eligible slots in the next 24 hours.
- A patient or guest cancellation deducts 0% at least 24 hours ahead, 25% from 2 hours to under 24 hours, and 50% under 2 hours. Doctor and admin cancellations refund the full captured payment. The admin panel lists refunds that require review.
- Online appointments use Jitsi. The join window starts 15 minutes before the visit and ends 30 minutes after it finishes. Keep the demo room link private.

## Doctor registration and notifications

Doctor applicants can search the public NMC Indian Medical Register during signup. The backend rechecks a confirmed match. Unmatched or unavailable lookups go to admin review after contact verification. The public lookup is best effort. [Indian Medical Register](https://nmc.org.in/information-desk/indian-medical-register)

Appointment, refund and application updates appear under the notification bell. Set `NOTIFICATION_EMAIL_ENABLED=1` to also queue email for new notices addressed to verified accounts. The notification worker uses the same email provider as OTP delivery.

## Previous prescriptions

Registered patients can optionally attach up to three JPEG, PNG or WebP photos of a previous prescription, up to 8 MiB each. The assigned doctor can view them after confirmation. The transcription links to source pages for review. Use fictional prescriptions when testing locally.

| Variable | Purpose |
| --- | --- |
| `PRESCRIPTIONS_ENABLED` | Set to `1` to allow uploads. |
| `PRESCRIPTION_DOCTOR_IDS` | Comma-separated IDs of eligible published doctors. |
| `PRESCRIPTION_TEST_MODE` | Set to `1` with `APP_ENV=development` to allow selected fictional demo doctors. |
| `PRESCRIPTION_RETENTION_DAYS` and `PRESCRIPTION_AUDIT_RETENTION_DAYS` | Retention periods for attachments and audit metadata. |
| `PRESCRIPTION_AI_PROVIDER` | Use `gemini` for the Gemini Developer API or `vertex` for Google Cloud. |
| `GEMINI_API_KEY` and `GEMINI_MODEL` | Google AI Studio key and model. The example uses `gemini-3.5-flash-lite`. |
| `PRESCRIPTION_GOOGLE_PROJECT` | Vertex AI project with Application Default Credentials and provisioned throughput in `asia-south1`. |

Apply the [MongoDB schema](DATABASE.md#audit-and-setup) before deploying with uploads enabled. Keep provider configuration in place when disabling uploads so the worker continues to purge expired records. Review provider terms, access, consent and retention before enabling uploads for any real patient data. [Gemini API terms](https://ai.google.dev/gemini-api/terms)

For a local demo with a seeded doctor, get that doctor's numeric ID from [the doctor API](http://localhost:8000/api/v1/doctors). Put the ID in `PRESCRIPTION_DOCTOR_IDS`, set `PRESCRIPTIONS_ENABLED=1` and `PRESCRIPTION_TEST_MODE=1`, and choose positive retention values such as `7` and `30` for attachments and audit records. Set `PRESCRIPTION_AI_PROVIDER=gemini` and your API key, then restart the backend. [Google AI Studio API keys](https://aistudio.google.com/app/apikey)

To run the opt-in Gemini check, set `RUN_GEMINI_LIVE_TESTS=1` in the terminal and run `python -m pytest -q tests/test_flows.py -k live_gemini` from `backend/` with the virtual environment active. It sends a fictional prescription through the configured Google account and may consume API quota. Clear the flag after the check.

## Live testing checklist

1. **Atlas and admin.** Open `/api/v1/health` and sign in with the demo admin account. Confirm the doctor directory shows published demo doctors and future slots. Seed demo data if the database is empty.
2. **Email and SMS OTP.** Register a patient using an inbox and mobile number you control. Request and enter the email code, then request and enter the SMS code. Confirm the dashboard opens. Sign out and use **Forgot password?** with the verified email to exercise the recovery code.
3. **Doctor application and availability.** Register a test doctor with a separate email, verify both contacts, and review the application in the admin panel if it needs approval. Sign in as the doctor and create clinic and online slots.
4. **Checkout, cancellations and refunds.** Book a published doctor's slot as the patient and pay using the Razorpay Test Mode checkout. Check confirmation in both dashboards and the Razorpay test orders. Cancel a separate paid future appointment and check the refund status in the app and the Razorpay test dashboard. Test Mode uses simulated payments.
5. **Guest booking and recovery.** Create a slot within the next 24 hours. Use **Book an urgent appointment**, verify a mobile number and complete test checkout. In a separate browser session, use **Manage a guest booking** and verify the same number again.
6. **Video, completion and reviews.** Book an online visit close enough to enter its join window. Open the video room as both participants. Complete the appointment as the doctor when eligible, then leave a review as the patient.
7. **Notification emails and prescriptions.** With notification email enabled, book another visit as a registered patient and attach a fictional prescription during checkout with an eligible doctor. After confirmation, check the bell and recipient's inbox for the booking notice. Verify the transcription and source-photo links when the worker finishes.
8. **Admin operations.** In the admin panel, check the reporting period, appointments and test transactions. Use a disposable account to test suspension and reactivation and confirm the account can sign in again after reactivation.

## Hosted preview

The Dockerfile builds the frontend, then packages it with the FastAPI backend. `render.yaml` configures the service. Set Atlas access and the appropriate provider variables on the deployed backend. Use separate development and hosted databases when seeding demo accounts.
