import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

# Minimal stubs so importing UI module does not require optional google deps.
google = types.ModuleType("google")
auth = types.ModuleType("google.auth")
auth_exceptions = types.ModuleType("google.auth.exceptions")
auth_exceptions.RefreshError = Exception
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
errors = types.ModuleType("googleapiclient.errors")
errors.HttpError = Exception

sys.modules.setdefault("google", google)
sys.modules.setdefault("google.auth", auth)
sys.modules.setdefault("google.auth.exceptions", auth_exceptions)
sys.modules.setdefault("google.auth.transport", transport)
sys.modules.setdefault("google.auth.transport.requests", requests)
sys.modules.setdefault("google.oauth2", oauth2)
sys.modules.setdefault("google.oauth2.credentials", credentials)
sys.modules.setdefault("google_auth_oauthlib", oauthlib)
sys.modules.setdefault("google_auth_oauthlib.flow", flow)
sys.modules.setdefault("googleapiclient", apiclient)
sys.modules.setdefault("googleapiclient.discovery", discovery)
sys.modules.setdefault("googleapiclient.errors", errors)

from app.ui.email_manager_window import (
    ATTACHMENT_ORDER_REQUEST,
    EmailManagerWindow,
    clean_markdown,
    clean_outlook_styles,
    copiar_tabla,
    detect_kv_format,
    export_to_csv,
    format_estado_badge,
    is_probably_table,
    is_real_html,
    normalize_to_table,
    parse_kv_to_table,
    parse_markdown_table,
    sanitize_html_for_tk,
)


class _PreviewStub:
    def __init__(self) -> None:
        self.value = ""

    def set_html(self, value: str) -> None:
        self.value = value


class _PreviewErrorStub:
    def set_html(self, _value: str) -> None:
        raise RuntimeError("tkhtmlview error")


class _PreviewTextStub:
    def __init__(self) -> None:
        self.value = ""

    def delete(self, *_args) -> None:
        self.value = ""

    def insert(self, _index: str, text: str) -> None:
        self.value = text


def test_attachment_order_request_prioritizes_numeric_palets_extraction() -> None:
    assert "Debes devolver EXCLUSIVAMENTE un JSON válido" in ATTACHMENT_ORDER_REQUEST
    assert "\"Lineas\": [" in ATTACHMENT_ORDER_REQUEST
    assert "\"Cantidad\": null" in ATTACHMENT_ORDER_REQUEST
    assert "\"NumeroPedido\": \"\"" in ATTACHMENT_ORDER_REQUEST
    assert "FORMATO JSON OBLIGATORIO" in ATTACHMENT_ORDER_REQUEST
    assert "{texto_extraido_pdf}" in ATTACHMENT_ORDER_REQUEST


def test_create_notes_no_row_get() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE emails (
            gmail_id TEXT PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            real_sender TEXT,
            original_from TEXT,
            received_at TEXT,
            body_text TEXT,
            body_html TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, real_sender, original_from, received_at, body_text, body_html)
        VALUES ('id-1', 'Asunto', 'forwarder@example.com', 'real@example.com', 'preferred@example.com', '2024-01-01T00:00:00+00:00', 'hola', '')
        """
    )
    row = conn.execute("SELECT * FROM emails WHERE gmail_id = 'id-1'").fetchone()
    assert row is not None

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._compose_note_text = lambda subject, sender, body_text, body_html: f"{subject}|{sender}|{body_text}|{body_html}"
    window._resolve_default_value = lambda *_args: "valor"
    window._resolve_note_date = lambda _value: "2024-01-01"

    request = EmailManagerWindow._build_note_request_from_row(window, row)

    assert request.title == "Asunto"
    assert "preferred@example.com" in request.raw_text


def test_is_real_html_detects_known_html_tags() -> None:
    assert is_real_html("<html><body><p>hola</p></body></html>")
    assert is_real_html("<table><tr><td>row</td></tr></table>")
    assert not is_real_html("Normal DocumentEmail table.MsoNormalTable font-family: Times New Roman")
    assert not is_real_html("")


def test_set_html_preview_uses_text_fallback_for_non_html() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, "Normal DocumentEmail", "line 1\nline <2>")

    assert window._current_html_content == ""
    assert window.preview_html.value == "<pre>line 1\nline &lt;2&gt;</pre>"




def test_sanitize_html_for_tk_replaces_problematic_css_values() -> None:
    raw = '<span style="background:transparent;color:inherit;border-color:rgba(0,0,0,.2);color:var(--text)">ok</span>'

    sanitized = sanitize_html_for_tk(raw)

    assert "transparent" not in sanitized.lower()
    assert "inherit" not in sanitized.lower()
    assert "rgba(" not in sanitized.lower()
    assert "var(" not in sanitized.lower()


def test_set_html_preview_falls_back_to_text_when_renderer_fails() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewErrorStub()
    window.preview_text = _PreviewTextStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, '<div style="color:transparent">hola</div>', "texto alternativo")

    assert window.preview_text.value == "texto alternativo"

def test_clean_outlook_styles_removes_mso_and_list_noise() -> None:
    raw = """{mso-level-number-format:bullet; mso-level-text:\\F0B7;}\n@list l1:level6 {mso-level-number-format:bullet; font-family:Wingdings;}\nfont-family: Times New Roman\nDe: Antonio Sánchez"""

    cleaned = clean_outlook_styles(raw)

    assert "mso-" not in cleaned.lower()
    assert "@list" not in cleaned.lower()
    assert "De: Antonio Sánchez" in cleaned


def test_set_html_preview_keeps_html_for_real_html_content() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, "<div><p>ok</p></div>", "text")

    assert window._current_html_content == "<div><p>ok</p></div>"
    assert window.preview_html.value == "<div><p>ok</p></div>"


def test_clean_markdown_removes_bold_markers() -> None:
    assert clean_markdown("**Campo** | **Detalles**") == "Campo | Detalles"


def test_parse_markdown_table_cleans_headers_and_cells() -> None:
    headers, rows = parse_markdown_table("**Campo** | **Detalles**\nvalor | **ok**")

    assert headers == ["Campo", "Detalles"]
    assert rows == [["valor", "ok"]]


def test_detect_kv_format_requires_repeated_pairs_and_minimum_lines() -> None:
    assert detect_kv_format("Cliente: EUROGROUP\nDestino: Alemania\nContenido: 648 cajas") is True
    assert detect_kv_format("Cliente: EUROGROUP") is False
    assert detect_kv_format("Texto libre\nSin pares\nNi valores") is False


def test_parse_kv_to_table_normalizes_noise_and_preserves_order() -> None:
    headers, rows = parse_kv_to_table("**Cliente**: EUROGROUP\n- **Destino**: Alemania\nContenido:: 648 cajas\nCampo vacío:\n")

    assert headers == ["Campo", "Detalle"]
    assert rows == [["Cliente", "EUROGROUP"], ["Destino", "Alemania"], ["Contenido", "648 cajas"]]


def test_normalize_to_table_prefers_table_then_falls_back_to_kv() -> None:
    markdown = "| Campo | Detalle |\n|---|---|\n| Cliente | EUROGROUP |"
    table_result = normalize_to_table(markdown)
    assert table_result == (["Campo", "Detalle"], [["Cliente", "EUROGROUP"]])

    kv_result = normalize_to_table("Cliente: EUROGROUP\nDestino: Alemania\nContenido: 648 cajas")
    assert kv_result == (["Campo", "Detalle"], [["Cliente", "EUROGROUP"], ["Destino", "Alemania"], ["Contenido", "648 cajas"]])

    assert normalize_to_table("texto plano sin estructura") is None


def test_is_probably_table_detects_pipe_or_tab_delimiters() -> None:
    assert is_probably_table("col1|col2")
    assert is_probably_table("col1\tcol2")
    assert not is_probably_table("texto plano sin separadores")


def test_is_order_json_payload_detects_supported_order_shapes() -> None:
    assert EmailManagerWindow._is_order_json_payload({"Pedidos": []}) is True
    assert EmailManagerWindow._is_order_json_payload({"NumeroPedido": "25/109774/1", "Lineas": []}) is True
    assert EmailManagerWindow._is_order_json_payload([{"Linea": 1, "Mercancia": "Naranjas"}]) is True
    assert EmailManagerWindow._is_order_json_payload({"foo": "bar"}) is False


def test_flatten_order_rows_expands_order_and_lines_for_vertical_table() -> None:
    parsed_json = {
        "Pedidos": [
            {
                "PedidoID": "25/109774/1",
                "Cliente": "LIDL Alemania",
                "Comercial": "Francisco José",
                "Lineas": [
                    {"Linea": 1, "Cantidad": "360", "Mercancia": "Naranjas Navel Lane Late"},
                    {"Linea": 2, "Cantidad": "120", "Mercancia": "Mandarinas"},
                ],
            }
        ]
    }

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    rows = EmailManagerWindow._flatten_order_rows(window, parsed_json)

    assert ["Campo", "25/109774/1"] in rows
    assert ["Cliente", "LIDL Alemania"] in rows
    assert ["Comercial", "Francisco José"] in rows
    assert ["Campo", "Línea 1"] in rows
    assert ["Cantidad", "360"] in rows
    assert ["Mercancia", "Naranjas Navel Lane Late"] in rows


def test_normalizar_pedidos_json_convierte_estructura_nueva() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    data = {
        "Pedidos": [
            {
                "NumeroPedido": "P-1",
                "Cliente": "ACME",
                "Comercial": "Ana",
                "FechaSalida": "2026-03-19",
                "Plataforma": "Madrid",
                "Pais": "España",
                "PuntoCarga": "SEV",
                "Lineas": [
                    {"Linea": 1, "Cantidad": 2, "NombrePalet": "Euro.Retor", "Mercancia": "Naranja"},
                ],
            }
        ]
    }
    lineas = EmailManagerWindow._normalizar_pedidos_json(window, data)
    assert len(lineas) == 1
    assert lineas[0]["NumeroPedido"] == "P-1"
    assert lineas[0]["TipoPalet"] == "Euro.Retor"
    assert lineas[0]["Cliente"] == "ACME"
    assert lineas[0]["Categoria"] == ""


def test_build_canonical_order_lines_copia_cabecera_y_normaliza_categoria() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    data = {
        "Pedidos": [
            {
                "NumeroPedido": "P-2",
                "Cliente": "Cliente Demo",
                "FechaSalida": "2026-03-20",
                "PuntoCarga": "VAL",
                "Lineas": [{"Linea": 1, "Mercancia": "Naranja", "Confeccion": "Caja", "Cat.": "extra"}],
            }
        ]
    }

    lineas = EmailManagerWindow._build_canonical_order_lines(window, data)

    assert len(lineas) == 1
    assert lineas[0]["Cliente"] == "Cliente Demo"
    assert lineas[0]["FechaSalida"] == "2026-03-20"
    assert lineas[0]["PuntoCarga"] == "VAL"
    assert lineas[0]["Categoria"] == "Extra"
    assert set(lineas[0].keys()) == {
        "NumeroPedido",
        "Cliente",
        "Comercial",
        "FechaSalida",
        "Plataforma",
        "Pais",
        "PuntoCarga",
        "Estado",
        "Linea",
        "Cantidad",
        "CajasTotales",
        "CP",
        "TipoPalet",
        "NombreCaja",
        "Mercancia",
        "Confeccion",
        "Calibre",
        "Categoria",
        "Marca",
        "PO",
        "Lote",
        "Observaciones",
    }


def test_calcular_estado_linea_nuevo_modificado_cancelado() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, NumeroPedido TEXT, Estado TEXT, fecha DATETIME)")
    conn.execute("CREATE TABLE lineas (pedido_id INTEGER, linea INTEGER, cantidad REAL, cajas_totales REAL, cp REAL, tipo_palet TEXT, nombre_caja TEXT, mercancia TEXT, confeccion TEXT, calibre TEXT, categoria TEXT, marca TEXT, po TEXT, lote TEXT, observaciones TEXT)")
    conn.execute("INSERT INTO pedidos (id, NumeroPedido, Estado, fecha) VALUES (1, 'P-1', 'Nuevo', CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO lineas (pedido_id, linea, cantidad, cajas_totales, cp, tipo_palet) VALUES (1, 1, 10, 300, 30, 'Euro.Retor')")
    conn.commit()

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.conn = conn

    window.pedidos_repo = type("PedidosRepo", (), {"obtener_lineas_ultima_version_por_pedido": lambda *_args: [{"Linea": 1, "Cantidad": 10, "CajasTotales": 300, "CP": 30, "TipoPalet": "Euro.Retor"}]})()
    assert EmailManagerWindow._calcular_estado_linea(window, {"NumeroPedido": "P-2", "Linea": 1}) == "Nuevo"
    assert EmailManagerWindow._calcular_estado_linea(window, {"NumeroPedido": "P-1", "Linea": 1, "Cantidad": 8, "CajasTotales": 240, "CP": 30, "TipoPalet": "Euro.Retor"}) == "Modificado"
    assert (
        EmailManagerWindow._calcular_estado_linea(
            window,
            {"NumeroPedido": "P-3", "Linea": 1, "Observaciones": "pedido cancelado por cliente"},
        )
        == "Cancelado"
    )


def test_validar_linea_pedido_detecta_errores_basicos() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    errores = EmailManagerWindow._validar_linea_pedido(
        window,
        {
            "PedidoID": "",
            "NumeroPedido": "",
            "Linea": "",
            "Cantidad": "abc",
            "CajasTotales": "-1",
            "CP": "0",
            "TipoPalet": "",
            "Categoria": "Fruta",
            "Estado": "Desconocido",
        },
    )
    assert "Falta número de pedido" in errores
    assert "valores no numéricos" in errores
    assert "Estado inválido" in errores


def test_detectar_errores_erp_linea_detecta_incoherencias() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    errores = EmailManagerWindow._detectar_errores_erp_linea(
        window,
        {"Cantidad": 10, "CajasTotales": 300, "CP": 20, "Estado": "Cancelado"},
    )
    assert any("CP incoherente" in error for error in errores)


def test_validar_pedido_para_confirmacion_separa_warnings_y_errores() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.config_manager = type(
        "Cfg",
        (),
        {
            "get_order_validation": lambda *_args: {
                "required_fields": ["Cantidad", "Mercancia", "Cliente", "FechaSalida", "PuntoCarga", "Confeccion"]
            }
        },
    )()
    lineas = [
        {
            "NumeroPedido": "P-77",
            "Linea": 1,
            "Cantidad": "",
            "Mercancia": "",
            "Confeccion": "",
            "Cliente": "",
            "FechaSalida": "",
            "PuntoCarga": "",
            "CP": "",
            "Categoria": "Premium",
        }
    ]

    resultado = EmailManagerWindow._validar_pedido_para_confirmacion(window, lineas)

    assert len(resultado["errors"]) == 6
    assert any("Falta cliente" in err for err in resultado["errors"])
    assert any("Falta número de palets" in err for err in resultado["errors"])
    assert any("CP no definido" in warning for warning in resultado["warnings"])
    assert any("Categoría inválida" in warning for warning in resultado["warnings"])


def test_export_to_csv_writes_headers_and_rows(tmp_path: Path) -> None:
    output_path = tmp_path / "tabla.csv"
    export_to_csv(["Campo", "Detalles"], [["A", "B"]], str(output_path))

    assert output_path.read_text(encoding="utf-8") == "Campo,Detalles\nA,B\n"


def test_copiar_tabla_uses_dataframe_clipboard_without_index() -> None:
    with patch("pandas.DataFrame.to_clipboard") as to_clipboard:
        copiar_tabla(["Campo", "Detalles"], [["A", "B"]])

    to_clipboard.assert_called_once_with(index=False)

class _TreeStub:
    def __init__(self) -> None:
        self.selected = ()
        self.focused = None
        self.seen = None

    def selection_set(self, ids):
        self.selected = tuple(ids)

    def focus(self, iid):
        self.focused = iid

    def see(self, iid):
        self.seen = iid

    def selection(self):
        return self.selected


class _TextStub:
    def __init__(self) -> None:
        self.value = ""

    def delete(self, *_args) -> None:
        self.value = ""

    def insert(self, _index: str, text: str) -> None:
        self.value = text

    def get(self, *_args) -> str:
        return self.value


def test_select_email_by_gmail_id_selects_row_and_refreshes_preview() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: {"type": "priority"}})()
    window._rows_by_id = {"id-1": {"gmail_id": "id-1"}}
    window._tab_to_types = {"Prioridad": ["priority"]}
    window._current_tab = "Prioridad"
    window._set_tab_by_label = lambda _label: None
    window.refresh_emails = lambda: None
    refreshed = {"called": False}
    window._refresh_preview = lambda: refreshed.update(called=True)
    window.tree = _TreeStub()

    selected = EmailManagerWindow.select_email_by_gmail_id(window, "id-1")

    assert selected is True
    assert window.tree.selected == ("id-1",)
    assert window.tree.focused == "id-1"
    assert window.tree.seen == "id-1"
    assert refreshed["called"] is True


def test_set_response_draft_tracks_pending_note_id() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.response_text = _TextStub()
    tree = _TreeStub()
    tree.selection_set(("id-77",))
    window.tree = tree
    window._pending_note_id_by_gmail_id = {}

    EmailManagerWindow.set_response_draft(window, "hola", note_id=33)

    assert window.response_text.value == "hola"
    assert window._pending_note_id_by_gmail_id["id-77"] == 33


def test_select_email_by_gmail_id_warns_when_email_not_found() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: None})()
    window._rows_by_id = {}
    window._tab_to_types = {}
    window._current_tab = "Prioridad"
    window.tree = _TreeStub()

    with patch("app.ui.email_manager_window.messagebox.showwarning") as showwarning:
        selected = EmailManagerWindow.select_email_by_gmail_id(window, "missing-id")

    assert selected is False
    showwarning.assert_called_once_with("Email no encontrado", "No se encontró el correo original asociado.")


def test_set_reply_body_delegates_to_set_response_draft() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    called: dict[str, int | str | None] = {}

    def _capture(body: str, note_id: int | None = None) -> None:
        called["body"] = body
        called["note_id"] = note_id

    window.set_response_draft = _capture  # type: ignore[method-assign]
    EmailManagerWindow.set_reply_body(window, "texto de prueba", note_id=21)

    assert called == {"body": "texto de prueba", "note_id": 21}




def test_get_email_metadata_handles_missing_optional_fields() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    row = {
        "gmail_id": "id-3",
        "subject": "Hola",
        "sender": "sender@example.com",
    }
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: row})()

    metadata = EmailManagerWindow.get_email_metadata(window, "id-3")

    assert metadata["gmail_id"] == "id-3"
    assert metadata["thread_id"] == ""
    assert metadata["sender"] == "sender@example.com"
    assert metadata["subject"] == "Hola"

def test_build_note_request_uses_custom_title_when_provided() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE emails (
            gmail_id TEXT PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            real_sender TEXT,
            original_from TEXT,
            received_at TEXT,
            body_text TEXT,
            body_html TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, real_sender, original_from, received_at, body_text, body_html)
        VALUES ('id-2', 'Asunto original', 'sender@example.com', 'real@example.com', '', '2024-01-01T00:00:00+00:00', 'hola', '')
        """
    )
    row = conn.execute("SELECT * FROM emails WHERE gmail_id = 'id-2'").fetchone()
    assert row is not None

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._compose_note_text = lambda subject, sender, body_text, body_html: f"{subject}|{sender}|{body_text}|{body_html}"
    window._resolve_default_value = lambda *_args: "valor"
    window._resolve_note_date = lambda _value: "2024-01-01"

    request = EmailManagerWindow._build_note_request_from_row(window, row, "Título editable")

    assert request.title == "Título editable"
    assert request.raw_text.startswith("Título editable|")


def test_extract_quick_summary_detects_resumen_rapido_marker() -> None:
    response_text = (
        "Resumen rápido:\n"
        "• Falta completar rectificación de liquidación pasada\n"
        "• Pendiente rectificación transporte Juan Antonio Cano\n"
    )

    summary = EmailManagerWindow._extract_quick_summary(response_text)

    assert summary.startswith("• Falta completar")
    assert "• Pendiente rectificación" in summary


def test_extract_quick_summary_returns_empty_when_marker_not_found() -> None:
    assert EmailManagerWindow._extract_quick_summary("Sin resumen") == ""


def test_compose_note_body_with_summary_puts_summary_before_original_email() -> None:
    result = EmailManagerWindow._compose_note_body_with_summary(
        "Texto completo del correo",
        "• Punto 1\n• Punto 2",
    )

    assert result.startswith("RESUMEN RÁPIDO\n--------------")
    assert "• Punto 1\n• Punto 2" in result
    assert result.endswith("Texto completo del correo")
    assert result.index("RESUMEN RÁPIDO") < result.index("EMAIL ORIGINAL")


def test_compose_note_body_with_summary_avoids_duplicate_when_already_integrated() -> None:
    existing = (
        "RESUMEN RÁPIDO\n"
        "--------------\n"
        "• Punto 1\n\n"
        "EMAIL ORIGINAL\n"
        "--------------\n"
        "Texto completo del correo"
    )

    result = EmailManagerWindow._compose_note_body_with_summary(existing, "• Punto 1")

    assert result == existing


def test_get_email_attachments_parses_attachments_json() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: {"attachments_json": '[{"filename":"factura.pdf"}]'}})()

    attachments = EmailManagerWindow.get_email_attachments(window, "id-1")

    assert attachments == [{"filename": "factura.pdf"}]


def test_create_outlook_draft_updates_email_status_to_responded() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-1",))
    window.tree = tree
    window._rows_by_id = {
        "id-1": {
            "gmail_id": "id-1",
            "subject": "Consulta",
            "real_sender": "cliente@example.com",
            "sender": "cliente@example.com",
            "original_to": "",
            "original_cc": "",
            "reply_to": "",
            "category": "other",
        }
    }
    window.response_text = _TextStub()
    window.response_text.insert("1.0", "Respuesta")
    window._normalize_recipients = lambda **_kwargs: ("cliente@example.com", "")
    window._build_email_attachments = lambda *_args: []
    window._resolve_reply_attachment_paths = lambda *_args: []
    window.my_email = "yo@example.com"
    window.log = lambda *_args, **_kwargs: None
    window.note_service = type("NoteService", (), {"get_note_by_source": lambda *_args: None, "note_repo": type("Repo", (), {"update_estado": lambda *_args: None, "set_email_replied": lambda *_args: None})()})()
    window._pending_note_id_by_gmail_id = {}
    window._is_trainable_response = lambda *_args: False
    refreshed = {"called": False}
    window.refresh_emails = lambda: refreshed.update(called=True)
    window.outlook_service = type("Outlook", (), {"create_draft": lambda *_args, **_kwargs: ("cliente@example.com", [])})()

    updates: list[tuple[str, str]] = []
    window.email_repo = type("Repo", (), {"update_status": lambda _self, gmail_id, status: updates.append((gmail_id, status))})()

    with patch("app.ui.email_manager_window.messagebox.askyesno", return_value=False):
        EmailManagerWindow._create_outlook_draft(window)

    assert updates == [("id-1", "responded")]
    assert refreshed["called"] is True


def test_forward_email_updates_status_to_responded() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-9",))
    window.tree = tree
    window._rows_by_id = {
        "id-9": {
            "gmail_id": "id-9",
            "subject": "Asunto",
            "real_sender": "sender@example.com",
            "sender": "sender@example.com",
            "received_at": "2024-01-01T00:00:00+00:00",
            "original_to": "dest@example.com",
            "body_text": "contenido",
        }
    }
    window._build_email_attachments = lambda *_args: []
    window._resolve_reply_attachment_paths = lambda *_args: []
    window._format_datetime = lambda *_args: "2024-01-01"
    window.log = lambda *_args, **_kwargs: None
    window.note_service = type("NoteService", (), {"get_note_by_source": lambda *_args: None, "note_repo": type("Repo", (), {"update_estado": lambda *_args: None, "set_email_replied": lambda *_args: None})()})()
    window._pending_note_id_by_gmail_id = {}
    window.outlook_service = type("Outlook", (), {"create_forward_draft": lambda *_args, **_kwargs: None})()
    window.refresh_emails = lambda: None

    updates: list[tuple[str, str]] = []
    window.email_repo = type("Repo", (), {"update_status": lambda _self, gmail_id, status: updates.append((gmail_id, status))})()

    EmailManagerWindow._forward_email(window)

    assert updates == [("id-9", "responded")]


def test_summarize_email_opens_review_dialog_with_generated_summary() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-5",))
    window.tree = tree
    window._rows_by_id = {"id-5": {"gmail_id": "id-5", "subject": "Asunto", "body_text": "Texto del correo"}}
    window._current_html_content = ""
    window.response_text = _TextStub()
    window.response_text.insert("1.0", "Texto previo")
    window.log = lambda *_args, **_kwargs: None

    captured: dict[str, str] = {}

    def _capture_dialog(*, row, ai_summary, preview_body, **_kwargs):
        captured["row_gmail_id"] = row["gmail_id"]
        captured["summary"] = ai_summary
        captured["preview_body"] = preview_body

    window._open_summary_review_dialog = _capture_dialog

    fake_response = type("Response", (), {"output_text": "Resumen generado."})()
    fake_client = type("Client", (), {"responses": type("Responses", (), {"create": lambda *_args, **_kwargs: fake_response})()})()

    with patch("app.ui.email_manager_window.build_openai_client", return_value=fake_client):
        EmailManagerWindow._summarize_email(window)

    assert captured == {
        "row_gmail_id": "id-5",
        "summary": "Resumen generado.",
        "preview_body": "Texto del correo",
    }

def test_summarize_attachments_without_useful_files_shows_message() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-6",))
    window.tree = tree
    window._rows_by_id = {"id-6": {"gmail_id": "id-6"}}
    window.response_text = _TextStub()
    window._build_email_attachments = lambda *_args: [{"filename": "logo.png"}]
    window.log = lambda *_args, **_kwargs: None

    with patch("app.ui.email_manager_window.messagebox.showinfo") as mocked_info:
        EmailManagerWindow._summarize_attachments(window)

    mocked_info.assert_called_once_with("Adjuntos", "No se encontró contenido resumible en los adjuntos")


def test_summarize_attachments_renders_expected_format() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-7",))
    window.tree = tree
    window._rows_by_id = {"id-7": {"gmail_id": "id-7", "subject": "Informe", "sender": "acme@example.com"}}
    window.response_text = _TextStub()
    window.log = lambda *_args, **_kwargs: None
    window._build_email_attachments = lambda *_args: [{"filename": "informe.txt"}, {"filename": "logo.png"}]
    window.attachment_cache = type("Cache", (), {"ensure_downloaded": lambda *_args, **_kwargs: "/tmp/informe.txt"})()
    captured: dict[str, object] = {}

    def _capture_dialog(*, row, ai_summary, preview_body, summary_source, attachment_types, **_kwargs):
        captured["row"] = row
        captured["summary"] = ai_summary
        captured["preview_body"] = preview_body
        captured["summary_source"] = summary_source
        captured["attachment_types"] = attachment_types

    window._open_summary_review_dialog = _capture_dialog
    window._summarize_attachments_content = lambda *_args, **_kwargs: "• idea 1\n• idea 2"

    with patch("app.ui.email_manager_window.extract_text_from_attachments", return_value="ATTACHMENT: informe.txt\ncontenido"):
        EmailManagerWindow._summarize_attachments(window)

    assert captured == {
        "row": {"gmail_id": "id-7", "subject": "Informe", "sender": "acme@example.com"},
        "summary": "• idea 1\n• idea 2",
        "preview_body": "ATTACHMENT: informe.txt\ncontenido",
        "summary_source": "attachment",
        "attachment_types": ["txt"],
    }


def test_summarize_attachments_order_no_persiste_antes_de_confirmar() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    tree = _TreeStub()
    tree.selection_set(("id-8",))
    window.tree = tree
    window._rows_by_id = {"id-8": {"gmail_id": "id-8", "subject": "Pedido", "sender": "acme@example.com"}}
    window.response_text = _TextStub()
    window.log = lambda *_args, **_kwargs: None
    window._build_email_attachments = lambda *_args: [{"filename": "pedido.pdf"}]
    window.attachment_cache = type("Cache", (), {"ensure_downloaded": lambda *_args, **_kwargs: "/tmp/pedido.pdf"})()
    window._summarize_attachments_content = lambda *_args, **_kwargs: (
        '{"Pedidos":[{"PedidoID":"P-1","Cliente":"ACME","Lineas":[{"Linea":1,"Palets":10,"TCajas":300,"CP":30}]}]}'
    )

    called = {"guardar": 0}
    window.pedidos_repo = type(
        "PedidosRepo",
        (),
        {
            "guardar_pedidos_desde_json": lambda *_args, **_kwargs: called.__setitem__("guardar", called["guardar"] + 1),
        },
    )()
    captured: dict[str, object] = {}
    window._open_summary_review_dialog = lambda **kwargs: captured.update(kwargs)

    with patch("app.ui.email_manager_window.extract_text_and_types_from_attachments", return_value=("Pedido 10 Euro.Retor.X30", ["attachment"])):
        EmailManagerWindow._summarize_attachments(window)

    assert called["guardar"] == 0
    assert captured["order_lineas"] is not None


def test_is_summarizable_attachment_extracts_real_filename_from_label() -> None:
    attachment = {
        "filename": "NARANJA_S11_Lora_Del_Rio_reporte_semanal.pdf [application/pdf] (89447 bytes)",
        "mime": "application/octet-stream",
    }

    assert EmailManagerWindow._is_summarizable_attachment(attachment) is True


def test_is_summarizable_attachment_ignores_temp_and_signature_files() -> None:
    assert EmailManagerWindow._is_summarizable_attachment({"filename": "~WRD0000.jpg [image/jpeg] (1200 bytes)"}) is False
    assert EmailManagerWindow._is_summarizable_attachment({"filename": "logos [application/octet-stream]"}) is False
    assert EmailManagerWindow._is_summarizable_attachment({"filename": "imagen_firma.docx [application/vnd.openxmlformats-officedocument.wordprocessingml.document]"}) is False


def test_extract_attachment_filename_keeps_spaces() -> None:
    raw = "PK 881 EDEKA.xlsx [application/vnd.openxmlformats-officedocument.spreadsheetml.sheet]"
    assert EmailManagerWindow._extract_attachment_filename(raw) == "PK 881 EDEKA.xlsx"


def test_get_email_metadata_handles_missing_optional_fields() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type(
        "Repo",
        (),
        {"get_email_content": lambda *_args: {"gmail_id": "id-1", "sender": "sender@example.com", "subject": "Hola"}},
    )()

    metadata = EmailManagerWindow.get_email_metadata(window, "id-1")

    assert metadata == {
        "gmail_id": "id-1",
        "thread_id": "",
        "sender": "sender@example.com",
        "subject": "Hola",
    }


def test_extract_attachment_filename_sanitizes_mime_suffix() -> None:
    raw = 'LORARIOSEBAS_20250526_143207.pdf [application/octet-stream]'
    cleaned = EmailManagerWindow._extract_attachment_filename(raw)

    assert cleaned == 'LORARIOSEBAS_20250526_143207.pdf'


def test_is_summarizable_attachment_accepts_octet_stream_with_supported_extension() -> None:
    attachment = {
        'filename': 'LORARIOSEBAS_20250526_143207.pdf [application/octet-stream]',
        'mime': 'application/octet-stream',
    }

    assert EmailManagerWindow._is_summarizable_attachment(attachment) is True


def test_is_summarizable_attachment_rejects_images() -> None:
    attachment = {
        'filename': 'logo_s_el.jpg',
        'mime': 'image/jpeg',
    }

    assert EmailManagerWindow._is_summarizable_attachment(attachment) is False

def test_build_prepared_context_content_respects_order_and_omits_empty_blocks() -> None:
    merged = EmailManagerWindow._build_prepared_context_content(
        email_summary="• resumen email",
        attachment_summary="• resumen adjuntos",
        email_original="texto original",
    )

    assert merged.index("RESUMEN DEL EMAIL") < merged.index("RESUMEN DE ADJUNTOS") < merged.index("EMAIL ORIGINAL")
    assert "• resumen email" in merged
    assert "• resumen adjuntos" in merged
    assert merged.endswith("texto original")

    merged_without_attachment = EmailManagerWindow._build_prepared_context_content(
        email_summary="• resumen email",
        attachment_summary="",
        email_original="texto original",
    )
    assert "RESUMEN DE ADJUNTOS" not in merged_without_attachment


def test_build_event_body_from_prepared_context_prioritizes_summary_when_truncating() -> None:
    merged = (
        "RESUMEN DEL EMAIL\n-----------------\n• punto 1\n\n"
        "RESUMEN DE ADJUNTOS\n-------------------\n• adjunto\n\n"
        "EMAIL ORIGINAL\n--------------\n" + ("x" * 5000)
    )

    compact = EmailManagerWindow._build_event_body_from_prepared_context(merged)

    assert len(compact) <= 3500
    assert "RESUMEN DEL EMAIL" in compact
    assert "RESUMEN DE ADJUNTOS" in compact
    assert "EMAIL ORIGINAL" in compact


def test_build_note_request_prioritizes_prepared_context_over_quick_summary() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE emails (
            gmail_id TEXT PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            real_sender TEXT,
            original_from TEXT,
            received_at TEXT,
            body_text TEXT,
            body_html TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, real_sender, original_from, received_at, body_text, body_html)
        VALUES ('id-10', 'Asunto original', 'sender@example.com', 'real@example.com', '', '2024-01-01T00:00:00+00:00', 'hola', '')
        """
    )
    row = conn.execute("SELECT * FROM emails WHERE gmail_id = 'id-10'").fetchone()
    assert row is not None

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._compose_note_text = lambda subject, sender, body_text, body_html: f"{subject}|{sender}|{body_text}|{body_html}"
    window._resolve_default_value = lambda *_args: "valor"
    window._resolve_note_date = lambda _value: "2024-01-01"

    request = EmailManagerWindow._build_note_request_from_row(
        window,
        row,
        "Título",
        summary_text="• quick",
        include_summary=True,
        prepared_merged_content="RESUMEN DEL EMAIL\n---\ncontenido",
    )

    assert "RESUMEN DEL EMAIL" in request.raw_text
    assert "RESUMEN RÁPIDO" not in request.raw_text


def test_is_table_detects_markdown_and_csv() -> None:
    from app.ui.email_manager_window import is_table

    assert is_table("| A | B |\n|---|---|\n| 1 | 2 |") is True
    assert is_table("col1,col2\n1,2\n3,4") is True
    assert is_table("texto libre\nsolo una linea") is False


def test_parse_order_json_handles_fenced_json_and_prefixed_text() -> None:
    raw = "```json\nTexto previo\n[{\"PedidoID\":\"P-1\",\"Linea\":1,\"Cliente\":\"ACME\"}]\n```"

    parsed = EmailManagerWindow._parse_order_json(raw)

    assert parsed == [{"PedidoID": "P-1", "Linea": 1, "Cliente": "ACME"}]


def test_parse_order_json_raises_for_empty_payload() -> None:
    try:
        EmailManagerWindow._parse_order_json("```json\n```")
    except ValueError as exc:
        assert "vacía" in str(exc)
    else:
        raise AssertionError("Expected ValueError for empty payload")


def test_format_estado_badge_uses_expected_colors() -> None:
    assert format_estado_badge("Nuevo") == "🔵 Nuevo"
    assert format_estado_badge("Modificado") == "🟡 Modificado"
    assert format_estado_badge("Cancelado") == "🔴 Cancelado"
    assert format_estado_badge("Sin cambios") == "⚪ Sin cambios"


def test_parse_markdown_table_handles_separator_and_csv() -> None:
    from app.ui.email_manager_window import parse_markdown_table

    headers, rows = parse_markdown_table("| Nombre | Valor |\n|---|---|\n| A | 10 |\n| B | 20 |")
    assert headers == ["Nombre", "Valor"]
    assert rows == [["A", "10"], ["B", "20"]]

    csv_headers, csv_rows = parse_markdown_table("h1,h2\nr1c1,r1c2\nr2c1,r2c2")
    assert csv_headers == ["h1", "h2"]
    assert csv_rows == [["r1c1", "r1c2"], ["r2c1", "r2c2"]]


def test_get_final_confirmed_text_uses_original_when_not_edited() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._initialize_review_edit_state("Resumen IA")

    assert window._get_final_confirmed_text() == "Resumen IA"


def test_get_final_confirmed_text_uses_manual_edited_text() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._initialize_review_edit_state("Resumen IA")
    window._mark_review_text_edited("Resumen editado manualmente")

    assert window._get_final_confirmed_text() == "Resumen editado manualmente"


def test_get_final_confirmed_text_uses_dictated_edited_text() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._initialize_review_edit_state("Respuesta IA")
    window._mark_review_text_edited("Respuesta editada por dictado")

    assert window._get_final_confirmed_text() == "Respuesta editada por dictado"


def test_get_final_confirmed_text_response_not_edited_uses_ai_text() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._initialize_review_edit_state("Respuesta IA original")
    window._update_review_working_text("Respuesta IA original")

    assert window._get_final_confirmed_text() == "Respuesta IA original"


def test_get_final_confirmed_text_response_edited_uses_final_text() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._initialize_review_edit_state("Respuesta IA original")
    window._mark_review_text_edited("Respuesta final del usuario")

    assert window._get_final_confirmed_text() == "Respuesta final del usuario"


def test_save_email_response_feedback_stores_confirmed_text_as_training_example() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    captured: dict[str, object] = {}
    window._enqueue_training_example_save = lambda **kwargs: captured.update(kwargs)
    row = {
        "gmail_id": "id-22",
        "sender": "cliente@example.com",
        "real_sender": "cliente@example.com",
        "subject": "Asunto",
        "body_text": "Texto",
        "category": "consultas",
    }

    EmailManagerWindow._save_email_response_feedback_async(
        window,
        row=row,
        output_text="Respuesta final confirmada",
        edited_by_user=True,
        generated_text_original="Respuesta IA original",
        confirmed_text_final="Respuesta final confirmada",
    )

    assert captured["output_text"] == "Respuesta final confirmada"
    assert '"confirmed_text_final": "Respuesta final confirmada"' in str(captured["metadata"])


def test_save_email_summary_feedback_stores_confirmed_text_as_training_example() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    captured: dict[str, object] = {}
    window._enqueue_training_example_save = lambda **kwargs: captured.update(kwargs)
    row = {
        "gmail_id": "id-23",
        "sender": "cliente@example.com",
        "real_sender": "cliente@example.com",
        "subject": "Asunto resumen",
        "body_text": "Texto resumen",
        "category": "consultas",
    }

    EmailManagerWindow._save_email_summary_feedback_async(
        window,
        row=row,
        output_text="Resumen final confirmado",
        edited_by_user=True,
        preview_body="Texto resumen",
        summary_source="email",
        generated_text_original="Resumen IA original",
        confirmed_text_final="Resumen final confirmado",
    )

    assert captured["output_text"] == "Resumen final confirmado"
    assert '"confirmed_text_final": "Resumen final confirmado"' in str(captured["metadata"])
