"""
Cloud Function (2nd gen, HTTP trigger) invoked on a schedule by Cloud Scheduler.

Flow:
  1. Look for unread Gmail messages labeled "to-calendar"
  2. For each: ask Gemini to extract structured event details
  3. If confident -> create a Google Calendar event, send a confirmation email
  4. If not confident -> label "needs-review", send a heads-up email
  5. Relabel the source email so it isn't processed again

Environment variables expected (set via `gcloud functions deploy --set-secrets`
or `--set-env-vars`, see README.md):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
  GEMINI_API_KEY
  NOTIFY_EMAIL        (where confirmation emails are sent -- usually your own address)
"""

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText

import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SOURCE_LABEL = "to-calendar"
DONE_LABEL = "calendar-processed"
REVIEW_LABEL = "needs-review"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_credentials():
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def _get_or_create_label(gmail, name):
    labels = gmail.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"] == name:
            return lbl["id"]
    created = (
        gmail.users()
        .labels()
        .create(userId="me", body={"name": name, "labelListVisibility": "labelShow"})
        .execute()
    )
    return created["id"]


def _extract_plain_text(payload):
    """Walk the MIME parts of a Gmail message and return the plain text body."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "ignore")

    for part in payload.get("parts", []):
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def _parse_event_with_gemini(subject, body, received_iso):
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = f"""You extract calendar event details from a forwarded email.
The email was received at: {received_iso}
Use that as the anchor date for any relative dates ("next Friday", "tomorrow", etc).

Email subject: {subject}
Email body:
---
{body}
---

Return ONLY valid JSON (no markdown fences, no commentary) with this exact shape:
{{
  "confidence": "high" or "low",
  "title": string or null,
  "start": "YYYY-MM-DDTHH:MM:SS" or null,
  "end": "YYYY-MM-DDTHH:MM:SS" or null,
  "timezone": IANA timezone string, e.g. "America/New_York",
  "location": string or null,
  "description": string or null
}}

Rules:
- Set confidence to "low" if you cannot determine a clear start date AND time.
- If no end time is given, assume 1 hour after start for events, or leave null for all-day items.
- If no timezone is stated or implied, assume the sender's likely local time; if unsure, use "America/New_York".
- Do not invent a title -- summarize what the email actually says.
"""

    response = model.generate_content(prompt)
    text = response.text.strip()
    # strip markdown fences if the model added them anyway
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def _send_email(gmail, to, subject, body_text):
    message = MIMEText(body_text)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


def poll_and_process(request):
    creds = _get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    calendar = build("calendar", "v3", credentials=creds)
    notify_to = os.environ["NOTIFY_EMAIL"]

    done_label_id = _get_or_create_label(gmail, DONE_LABEL)
    review_label_id = _get_or_create_label(gmail, REVIEW_LABEL)

    results = gmail.users().messages().list(
        userId="me", q=f"label:{SOURCE_LABEL} is:unread"
    ).execute()
    messages = results.get("messages", [])

    processed = []

    for msg_ref in messages:
        msg = gmail.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        subject = headers.get("Subject", "(no subject)")
        received_iso = datetime.fromtimestamp(
            int(msg["internalDate"]) / 1000, tz=timezone.utc
        ).isoformat()
        body = _extract_plain_text(msg["payload"])

        try:
            parsed = _parse_event_with_gemini(subject, body, received_iso)
        except Exception as e:
            parsed = {"confidence": "low", "title": subject, "start": None}
            print(f"Parse error for message {msg_ref['id']}: {e}")

        source_label_id = _get_or_create_label(gmail, SOURCE_LABEL)

        if parsed.get("confidence") == "high" and parsed.get("start"):
            tz = parsed.get("timezone") or "America/New_York"
            event_body = {
                "summary": parsed.get("title") or subject,
                "location": parsed.get("location"),
                "description": parsed.get("description")
                or f"Auto-created from email: {subject}",
                "start": {"dateTime": parsed["start"], "timeZone": tz},
                "end": {"dateTime": parsed.get("end") or parsed["start"], "timeZone": tz},
            }
            created = calendar.events().insert(
                calendarId="primary", body=event_body
            ).execute()

            _send_email(
                gmail,
                notify_to,
                f"Added to calendar: {event_body['summary']}",
                f"Created from: \"{subject}\"\n\n"
                f"{event_body['summary']}\n{parsed['start']} ({tz})\n"
                f"{parsed.get('location') or ''}\n\n{created.get('htmlLink')}",
            )

            gmail.users().messages().modify(
                userId="me",
                id=msg_ref["id"],
                body={
                    "removeLabelIds": [source_label_id, "UNREAD"],
                    "addLabelIds": [done_label_id],
                },
            ).execute()
            processed.append({"id": msg_ref["id"], "status": "created"})

        else:
            _send_email(
                gmail,
                notify_to,
                f"Needs review: {subject}",
                f"Couldn't confidently extract an event from this email.\n"
                f"Subject: {subject}\n\nOpen Gmail and check the '{REVIEW_LABEL}' label.",
            )
            gmail.users().messages().modify(
                userId="me",
                id=msg_ref["id"],
                body={
                    "removeLabelIds": [source_label_id, "UNREAD"],
                    "addLabelIds": [review_label_id],
                },
            ).execute()
            processed.append({"id": msg_ref["id"], "status": "needs_review"})

    return {"processed": processed}, 200
