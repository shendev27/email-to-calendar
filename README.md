# Email → Calendar (Cloud Functions + Scheduler)

Forward an email to `you+calendar@gmail.com`, it becomes a Google Calendar
event automatically, and you get a confirmation email. Runs entirely on
Google's infrastructure — no laptop or server needs to stay on.

## 1. Google Cloud project setup

1. Go to https://console.cloud.google.com and create a new project (or use an existing one).
2. Enable these APIs (Console → "APIs & Services" → "Library"):
   - Gmail API
   - Google Calendar API
   - Cloud Functions API
   - Cloud Scheduler API
   - Cloud Build API (needed to deploy functions)
3. Enable billing on the project. This stays free at this usage level (a
   function running every few minutes is well within the free tier), but
   Cloud Functions requires a billing account attached even for $0 usage.

## 2. Gmail filter (the "capture" step)

1. In Gmail, click the filter icon in the search bar.
2. In "To," enter `youraddress+calendar@gmail.com` (your real Gmail address with `+calendar` added).
3. Create filter → check "Apply the label" → create new label `to-calendar`.
4. To send something to your calendar, forward the email to that `+calendar` address instead of your normal one.

## 3. OAuth credentials (one-time, done locally)

1. Console → "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth client ID".
   - Application type: **Desktop app**
   - Download the JSON as `client_secret.json`, put it next to `oauth_setup.py`.
2. On your laptop:
   ```bash
   pip install google-auth-oauthlib google-auth --break-system-packages
   python3 oauth_setup.py
   ```
3. A browser opens — log in and approve access. The script prints:
   ```
   CLIENT_ID=...
   CLIENT_SECRET=...
   REFRESH_TOKEN=...
   ```
   Save these three values somewhere safe — you'll need them in step 5.

## 4. Get a Gemini API key

1. Go to https://aistudio.google.com/apikey
2. Create an API key (free tier). Save it.

## 5. Store secrets in Secret Manager

Using values from steps 3 and 4:

```bash
gcloud services enable secretmanager.googleapis.com

echo -n "YOUR_CLIENT_ID"     | gcloud secrets create GOOGLE_CLIENT_ID --data-file=-
echo -n "YOUR_CLIENT_SECRET" | gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-
echo -n "YOUR_REFRESH_TOKEN" | gcloud secrets create GOOGLE_REFRESH_TOKEN --data-file=-
echo -n "YOUR_GEMINI_KEY"    | gcloud secrets create GEMINI_API_KEY --data-file=-
```

## 6. Deploy the Cloud Function

From this directory:

```bash
gcloud functions deploy email-to-calendar \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=poll_and_process \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-secrets="GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,GOOGLE_REFRESH_TOKEN=GOOGLE_REFRESH_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --set-env-vars="NOTIFY_EMAIL=youraddress@gmail.com"
```

`--no-allow-unauthenticated` means only Cloud Scheduler (with proper
credentials) can trigger it — not the open internet.

## 7. Create a service account for Scheduler to call the function

```bash
gcloud iam service-accounts create scheduler-invoker \
  --display-name="Cloud Scheduler Function Invoker"

gcloud functions add-invoker-policy-binding email-to-calendar \
  --region=us-central1 \
  --member="serviceAccount:scheduler-invoker@YOUR_PROJECT_ID.iam.gserviceaccount.com"
```

## 8. Schedule it

Get the function's URL from the deploy output, then:

```bash
gcloud scheduler jobs create http email-to-calendar-poll \
  --schedule="*/2 * * * *" \
  --uri="YOUR_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="scheduler-invoker@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --location=us-central1
```

This runs the function every 2 minutes. Adjust the cron schedule as you like
(e.g. `*/5 * * * *` for every 5 minutes).

## 9. Test it

Forward any plain-English email describing plans (no calendar invite needed)
to `youraddress+calendar@gmail.com`. Within ~2 minutes you should see the
event on your calendar and get a confirmation email. If it couldn't parse a
confident date/time, you'll get a "needs review" email instead and the
source email gets labeled `needs-review` so you can find it.

## Notes

- Gmail's `+alias` trick doesn't require any setup on Gmail's side — it's
  built in. The filter is the only Gmail-side configuration.
- `gmail.modify` scope covers reading and relabeling, but **not** permanent
  deletion — the original emails are never deleted, only relabeled.
- Logs for each run: `gcloud functions logs read email-to-calendar --region=us-central1`
