# Notion Second Brain (Tkinter + SQLite + Notion API)

App de escritorio en Python para Windows 10/11 que permite crear notas locales, deduplicarlas y sincronizarlas con una base de datos de Notion.

## Características MVP

- UI Tkinter para crear notas manuales o desde correo pegado.
- Persistencia local en SQLite (ruta tipo `%USERPROFILE%/AppData/Roaming/NotionSecondBrain/notes.db`).
- Deduplicación por `source_id = sha256(normalized_text + source)`.
- Cola de sincronización basada en estado (`pendiente`, `enviado`, `error`) con reintentos.
- Configuración de token/database_id de Notion y mapeo de propiedades desde UI.
- Logs en archivo (`.../NotionSecondBrain/logs/app.log`).

## Requisitos

- Python 3.10+
- Windows 10/11 (también funciona en Linux/macOS para desarrollo)

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución

```bash
python -m app.main
```

## Configurar Notion

1. Crea una integración interna en Notion y copia el token.
2. Comparte la base de datos con esa integración.
3. Configura en la app:
   - `Notion Token`
   - `Notion Database ID`
   - Nombres de propiedades (por defecto):
     - `Actividad` (title)
     - `Area` (select)
     - `Tipo` (select)
     - `Estado` (status)
     - `Fecha` (date)
     - `Prioridad` (select)

## Flujo de uso

1. Completa texto y metadatos.
2. Pulsa **Guardar** para persistir localmente.
3. Pulsa **Enviar** para sincronizar pendientes/error reintentables.
4. Revisa estado en la lista.
5. Si hay `notion_page_id`, usa **Abrir en Notion**.

## Testing

```bash
python -m unittest discover -s tests
```

## Troubleshooting (checklist)

- [ ] ¿Token de Notion correcto y sin espacios?
- [ ] ¿La base de datos fue compartida con la integración?
- [ ] ¿Los nombres de propiedades y tipos coinciden con Notion?
- [ ] ¿Hay conexión a internet al sincronizar?
- [ ] Revisa `app.log` para detalle de errores HTTP.

### Errores comunes

- **"Falta configurar Notion token/database_id"**
  - Completa configuración en el diálogo de ajustes.
- **Error de esquema de Notion**
  - Ajusta mapeo de propiedades o corrige tipos en la base.
- **Duplicado detectado**
  - El contenido normalizado + fuente ya existe localmente.
