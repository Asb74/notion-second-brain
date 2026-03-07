import sys
import types

# Dependency stubs
google = types.ModuleType("google")
auth = types.ModuleType("google.auth")
transport = types.ModuleType("google.auth.transport")
requests = types.ModuleType("google.auth.transport.requests")
requests.Request = object
oauth2 = types.ModuleType("google.oauth2")
credentials = types.ModuleType("google.oauth2.credentials")
credentials.Credentials = object
oauthlib = types.ModuleType("google_auth_oauthlib")
flow = types.ModuleType("google_auth_oauthlib.flow")
flow.InstalledAppFlow = object
apiclient = types.ModuleType("googleapiclient")
discovery = types.ModuleType("googleapiclient.discovery")
discovery.build = lambda *args, **kwargs: None

sys.modules.setdefault("google", google)
sys.modules.setdefault("google.auth", auth)
sys.modules.setdefault("google.auth.transport", transport)
sys.modules.setdefault("google.auth.transport.requests", requests)
sys.modules.setdefault("google.oauth2", oauth2)
sys.modules.setdefault("google.oauth2.credentials", credentials)
sys.modules.setdefault("google_auth_oauthlib", oauthlib)
sys.modules.setdefault("google_auth_oauthlib.flow", flow)
sys.modules.setdefault("googleapiclient", apiclient)
sys.modules.setdefault("googleapiclient.discovery", discovery)

from app.core.calendar.google_calendar_client import GoogleCalendarClient


class _CalendarListRequest:
    def __init__(self, responses):
        self.responses = responses
        self.index = 0

    def list(self, pageToken=None):
        return self

    def execute(self):
        response = self.responses[self.index]
        self.index += 1
        return response


class _Service:
    def __init__(self, responses):
        self._calendar_list = _CalendarListRequest(responses)

    def calendarList(self):
        return self._calendar_list


def test_list_calendars_supports_pagination():
    client = GoogleCalendarClient.__new__(GoogleCalendarClient)
    client.service = _Service(
        [
            {
                "items": [
                    {"id": "primary", "summary": "Personal", "primary": True, "selected": True},
                ],
                "nextPageToken": "p2",
            },
            {
                "items": [
                    {
                        "id": "team",
                        "summary": "Equipo",
                        "backgroundColor": "#00ff00",
                        "foregroundColor": "#ffffff",
                        "accessRole": "reader",
                        "selected": False,
                    }
                ]
            },
        ]
    )

    rows = client.list_calendars()

    assert len(rows) == 2
    assert rows[0]["google_calendar_id"] == "primary"
    assert rows[0]["is_primary"] == 1
    assert rows[1]["google_calendar_id"] == "team"
    assert rows[1]["selected"] == 0
    assert rows[1]["background_color"] == "#00ff00"
