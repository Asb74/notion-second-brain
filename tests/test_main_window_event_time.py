import sys
import types

# Stubs to import main_window without optional deps.
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

# tkcalendar stub
calendar_mod = types.ModuleType("tkcalendar")
calendar_mod.DateEntry = object
sys.modules.setdefault("tkcalendar", calendar_mod)

from app.ui.main_window import calcular_hora_fin, duracion_desde_etiqueta, generar_intervalos_15


def test_generar_intervalos_15_crea_96_intervalos() -> None:
    intervalos = generar_intervalos_15()
    assert len(intervalos) == 96
    assert intervalos[0] == "00:00"
    assert intervalos[-1] == "23:45"


def test_duracion_desde_etiqueta_extrae_minutos() -> None:
    assert duracion_desde_etiqueta("15 min") == 15
    assert duracion_desde_etiqueta("120 min") == 120
    assert duracion_desde_etiqueta("valor inválido") == 60


def test_calcular_hora_fin_suma_duracion() -> None:
    assert calcular_hora_fin("11:30", 60) == "12:30"
    assert calcular_hora_fin("23:45", 30) == "00:15"
