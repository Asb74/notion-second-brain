import sys
import types

from app.core.outlook.outlook_service import OutlookService


def test_clean_recipients_excludes_main_duplicates_and_my_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["Ana <ana@empresa.com>, yo@empresa.com"],
        cc_list=["ana@empresa.com; Luis <luis@empresa.com>; yo@empresa.com"],
        main_recipient="ana@empresa.com",
        my_email="yo@empresa.com",
    )

    assert main == "ana@empresa.com"
    assert cc == ["luis@empresa.com"]


def test_clean_recipients_main_is_cleared_if_my_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["yo@empresa.com"],
        cc_list=["Otro <otro@empresa.com>"],
        main_recipient="yo@empresa.com",
        my_email="yo@empresa.com",
    )

    assert main == ""
    assert cc == ["otro@empresa.com"]


def test_clean_recipients_excludes_configured_user_email() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["a.sanchez@sansebas.es"],
        cc_list=["Otro <otro@empresa.com>; a.sanchez@sansebas.es"],
        main_recipient="a.sanchez@sansebas.es",
        my_email="",
    )

    assert main == ""
    assert cc == ["otro@empresa.com"]


def test_clean_recipients_uses_to_plus_cc_and_removes_sender_duplicate() -> None:
    main, cc = OutlookService.clean_recipients(
        to_list=["cliente@externo.com; equipo@empresa.com"],
        cc_list=["Cliente <cliente@externo.com>; apoyo@empresa.com"],
        main_recipient="cliente@externo.com",
        my_email="yo@empresa.com",
    )

    assert main == "cliente@externo.com"
    assert cc == ["equipo@empresa.com", "apoyo@empresa.com"]


def test_outlook_attachment_path_validation(tmp_path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("ok")

    validated = OutlookService._validate_attachment_path(str(file_path))

    assert validated.endswith("file.txt")


def test_outlook_attachment_path_validation_missing() -> None:
    missing = "this/path/does/not/exist.txt"

    try:
        OutlookService._validate_attachment_path(missing)
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError as exc:
        assert "ruta no existe" in str(exc)


def test_reply_all_with_body_prepends_message(monkeypatch) -> None:
    class _Reply:
        Body = "Original"
        To = "cliente@externo.com; gestion@empresa.com"
        CC = "equipo@empresa.com; gestion@empresa.com"
        BCC = "gestion@empresa.com; auditoria@empresa.com"

        def Display(self):
            self.displayed = True

    class _Mail:
        def __init__(self):
            self.reply = _Reply()
            self.displayed = False

        def Display(self):
            self.displayed = True

        def ReplyAll(self):
            return self.reply

    class _Session:
        def __init__(self, mail):
            self.mail = mail

        def GetItemFromID(self, _entry_id):
            return self.mail

    class _Outlook:
        def __init__(self, mail):
            self.Session = _Session(mail)

    mail = _Mail()
    win32_client = types.SimpleNamespace(Dispatch=lambda _name: _Outlook(mail))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=win32_client))
    monkeypatch.setitem(sys.modules, "win32com.client", win32_client)

    service = OutlookService()
    service.reply_all_with_body("email-id", "Hola", exclude_email="gestion@empresa.com")

    assert mail.displayed is True
    assert mail.reply.To == "cliente@externo.com"
    assert mail.reply.CC == "equipo@empresa.com"
    assert mail.reply.BCC == "auditoria@empresa.com"
    assert mail.reply.Body == "Hola\n\n---\nOriginal"
    assert getattr(mail.reply, "displayed", False) is True


def test_reply_all_with_body_returns_false_when_entry_id_not_found(monkeypatch) -> None:
    class _Session:
        def GetItemFromID(self, _entry_id):
            raise RuntimeError("not found")

    class _Outlook:
        Session = _Session()

    win32_client = types.SimpleNamespace(Dispatch=lambda _name: _Outlook())
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=win32_client))
    monkeypatch.setitem(sys.modules, "win32com.client", win32_client)

    service = OutlookService()

    assert service.reply_all_with_body("email-id", "Hola") is False
