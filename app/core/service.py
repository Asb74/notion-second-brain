def sync_pending(self) -> tuple[int, int]:
    settings = self.get_settings()

    database_id = self.get_setting("notion_database_id")
    if not database_id:
        raise NotionError("Debe crear la base Notion antes de sincronizar.")

    self._validate_notion_settings(settings)

    client = NotionClient(settings.notion_token)

    schema = client.validate_database_schema(database_id, settings)
    if not schema.ok:
        raise NotionError(schema.message)

    sent = 0
    failed = 0

    now_iso = datetime.utcnow().isoformat(timespec="seconds")

    for note in self.note_repo.list_retryable(now_iso):
        try:
            page_id = client.create_page(database_id, settings, note)
            self.note_repo.mark_sent(note.id, page_id)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.exception("Error sync note id=%s", note.id)
            self.note_repo.mark_error(
                note.id,
                str(exc),
                settings.retry_delay_seconds,
            )

    return sent, failed
