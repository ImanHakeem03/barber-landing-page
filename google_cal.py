import json
import os

SCOPES = ['https://www.googleapis.com/auth/calendar']
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'credentials.json')

# Set GOOGLE_CALENDAR_ID on Render to the shop's calendar (the calendar must be
# shared with the service account's client_email).
CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID')


def get_calendar_service():
    """Build the Calendar client, or return None when no credentials are configured.

    Credentials come from the GOOGLE_CREDENTIALS_JSON env var (paste the whole
    service-account JSON there on Render), or from credentials.json locally.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if raw:
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    elif os.path.exists(SERVICE_ACCOUNT_FILE):
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    else:
        return None
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)


def create_google_event(customer_name: str, phone: str, barber_name: str, start_time_iso: str, end_time_iso: str):
    if os.getenv('GOOGLE_CALENDAR_DISABLED') or not CALENDAR_ID:
        print("Google Calendar skipped: disabled or GOOGLE_CALENDAR_ID not set.")
        return None
    try:
        service = get_calendar_service()
        if service is None:
            print("Google Calendar skipped: no credentials configured.")
            return None
        event = {
            'summary': f'✂️ Haircut: {customer_name}',
            'description': f'Barber: {barber_name}\nPhone: {phone}',
            'start': {'dateTime': start_time_iso, 'timeZone': 'Asia/Kuala_Lumpur'},
            'end': {'dateTime': end_time_iso, 'timeZone': 'Asia/Kuala_Lumpur'},
            'transparency': 'transparent',  # Allows reservations to overlap on the calendar
            'reminders': {
                'useDefault': False,
                'overrides': [{'method': 'popup', 'minutes': 15}],
            },
        }
        event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return event.get('htmlLink')
    except Exception as e:
        print("Google API Error:", e)
        return None
