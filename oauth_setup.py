"""
Run this ONCE on your local machine (the Fedora laptop is fine) to get a
refresh token for the Cloud Function. It opens a browser for you to approve
access, then prints the values you need to save.

Setup before running:
1. In Google Cloud Console, create/select a project.
2. Enable the "Gmail API" and "Google Calendar API".
3. Go to "APIs & Services" > "Credentials" > "Create Credentials" > "OAuth client ID".
   - Application type: Desktop app
   - Download the JSON, save it here as client_secret.json
4. pip install google-auth-oauthlib google-auth --break-system-packages
5. python3 oauth_setup.py

It will print a REFRESH_TOKEN, CLIENT_ID, and CLIENT_SECRET.
Save all three -- you'll paste them into Secret Manager when deploying
the Cloud Function.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# gmail.modify = read mail + change labels (NOT permanent delete)
# gmail.send   = send confirmation emails
# calendar.events = create/edit calendar events (not full calendar access)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    # opens a local browser window for you to approve access
    creds = flow.run_local_server(port=0)

    print("\n=== SAVE THESE VALUES ===")
    print(f"CLIENT_ID={creds.client_id}")
    print(f"CLIENT_SECRET={creds.client_secret}")
    print(f"REFRESH_TOKEN={creds.refresh_token}")
    print("==========================\n")
    print("You'll store these as secrets when deploying the Cloud Function.")


if __name__ == "__main__":
    main()
