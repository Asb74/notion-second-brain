from pathlib import Path

from app.services.evernote_enex_importer import parse_enex_file
from app.ui.evernote_import_dialog import EvernoteImportDialog


def test_parse_enex_notes_tags_text_and_resources(tmp_path: Path) -> None:
    enex_path = tmp_path / "sample.enex"
    enex_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<en-export>
  <note>
    <title>Nota piloto</title>
    <created>20240101T120000Z</created>
    <updated>20240102T130000Z</updated>
    <content><![CDATA[<?xml version="1.0"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note><div>Hola <b>Evernote</b></div><en-media type="text/plain" hash="abc"/></en-note>]]></content>
    <tag>proyecto</tag>
    <tag>Sansebas</tag>
    <resource>
      <data encoding="base64">SG9sYQ==</data>
      <mime>text/plain</mime>
      <resource-attributes><file-name>hola.txt</file-name></resource-attributes>
    </resource>
  </note>
</en-export>
""",
        encoding="utf-8",
    )

    notes = parse_enex_file(enex_path)

    assert len(notes) == 1
    note = notes[0]
    assert note["title"] == "Nota piloto"
    assert note["created"] == "20240101T120000Z"
    assert note["updated"] == "20240102T130000Z"
    assert note["tags"] == ["proyecto", "Sansebas"]
    assert "Hola Evernote" in str(note["content_text"])
    assert "[adjunto]" in str(note["content_text"])
    assert note["resources"] == [
        {"filename": "hola.txt", "mime": "text/plain", "data": b"Hola", "size": 4}
    ]


def test_parse_enex_generates_safe_resource_filename(tmp_path: Path) -> None:
    enex_path = tmp_path / "resource_without_name.enex"
    enex_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<en-export>
  <note>
    <title>Sin nombre adjunto</title>
    <content><![CDATA[<en-note>Contenido</en-note>]]></content>
    <resource><data encoding="base64">AAE=</data><mime>application/pdf</mime></resource>
  </note>
</en-export>
""",
        encoding="utf-8",
    )

    resource = parse_enex_file(enex_path)[0]["resources"][0]

    assert resource["filename"] == "evernote_adjunto_1.pdf"
    assert resource["size"] == 2


def test_parse_enex_suggests_topic_from_filename_when_notebook_missing(tmp_path: Path) -> None:
    enex_path = tmp_path / "viajes_familia.enex"
    enex_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<en-export>
  <note>
    <title>Ruta</title>
    <content><![CDATA[<en-note>Contenido</en-note>]]></content>
  </note>
</en-export>
""",
        encoding="utf-8",
    )

    note = parse_enex_file(enex_path)[0]

    assert note["notebook"] is None
    assert note["suggested_topic"] == "Viajes Familia"


def test_parse_enex_preserves_notebook_over_filename_suggestion(tmp_path: Path) -> None:
    enex_path = tmp_path / "viajes.enex"
    enex_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<en-export>
  <note>
    <title>Ruta</title>
    <notebook>Archivo Personal</notebook>
    <content><![CDATA[<en-note>Contenido</en-note>]]></content>
  </note>
</en-export>
""",
        encoding="utf-8",
    )

    note = parse_enex_file(enex_path)[0]

    assert note["notebook"] == "Archivo Personal"
    assert note["suggested_topic"] == ""


def test_evernote_import_dialog_never_generates_summary_from_content() -> None:
    note = {"content_text": "migas (por persona)\nPan ...\nPimiento ..."}

    assert EvernoteImportDialog._summary_for_note(note) == ""
