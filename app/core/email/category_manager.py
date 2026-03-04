"""Category management for dynamic email classes."""

from __future__ import annotations

import re
from typing import Any

from app.persistence.email_repository import EmailRepository


class CategoryManager:
    """Manage email categories and training cleanup constraints."""

    MAX_DYNAMIC_CATEGORIES = 5

    def __init__(self, email_repo: EmailRepository):
        self.email_repo = email_repo

    def list_categories(self) -> list[dict[str, Any]]:
        rows = self.email_repo.get_categories()
        return [
            {
                "name": str(row["name"]),
                "display_name": str(row["display_name"]),
                "is_base": bool(row["is_base"]),
            }
            for row in rows
        ]

    def create_category(self, display_name: str) -> dict[str, Any]:
        cleaned_display = (display_name or "").strip()
        if not cleaned_display:
            raise ValueError("El nombre visible no puede estar vacío.")

        category_name = self._to_internal_name(cleaned_display)
        categories = self.list_categories()
        existing_names = {item["name"] for item in categories}
        if category_name in existing_names:
            raise ValueError("Ya existe una categoría con ese nombre.")

        dynamic_count = len([item for item in categories if not item["is_base"]])
        if dynamic_count >= self.MAX_DYNAMIC_CATEGORIES:
            raise ValueError("Máximo 5 categorías adicionales permitidas.")

        self.email_repo.create_category(name=category_name, display_name=cleaned_display, is_base=0)
        return {"name": category_name, "display_name": cleaned_display, "is_base": False}

    def rename_category(self, current_name: str, next_display_name: str) -> dict[str, Any]:
        categories = self.list_categories()
        current = next((item for item in categories if item["name"] == current_name), None)
        if current is None:
            raise ValueError("La categoría no existe.")
        if current["is_base"]:
            raise ValueError("No se pueden renombrar categorías base.")

        cleaned_display = (next_display_name or "").strip()
        if not cleaned_display:
            raise ValueError("El nombre visible no puede estar vacío.")
        next_name = self._to_internal_name(cleaned_display)

        existing_names = {item["name"] for item in categories if item["name"] != current_name}
        if next_name in existing_names:
            raise ValueError("Ya existe una categoría con ese nombre.")

        self.email_repo.rename_category(previous_name=current_name, next_name=next_name, next_display_name=cleaned_display)
        return {"name": next_name, "display_name": cleaned_display, "is_base": False}

    def delete_category(self, name: str) -> None:
        categories = self.list_categories()
        category = next((item for item in categories if item["name"] == name), None)
        if category is None:
            raise ValueError("La categoría no existe.")
        if category["is_base"]:
            raise ValueError("No se pueden eliminar categorías base.")

        self.email_repo.delete_category(name)

    @staticmethod
    def _to_internal_name(display_name: str) -> str:
        lowered = display_name.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "_", lowered)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if not normalized:
            raise ValueError("Nombre interno inválido para la categoría.")
        return normalized
