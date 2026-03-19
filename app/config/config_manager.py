"""JSON configuration manager for user identity and email account settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ORDER_VALIDATION_FIELDS_BY_GROUP: dict[str, tuple[str, ...]] = {
    "pedido": (
        "NumeroPedido",
        "Cliente",
        "Comercial",
        "FCarga",
        "Plataforma",
        "Pais",
        "PCarga",
        "Estado",
    ),
    "linea": (
        "Linea",
        "Cantidad",
        "TipoPalet",
        "CajasTotales",
        "CP",
        "NombreCaja",
        "Mercancia",
        "Confeccion",
        "Calibre",
        "Categoria",
        "Marca",
        "PO",
        "Lote",
        "Observaciones",
    ),
}

DEFAULT_REQUIRED_ORDER_FIELDS: tuple[str, ...] = (
    "Cliente",
    "FCarga",
    "PCarga",
    "Cantidad",
    "Mercancia",
    "Confeccion",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "user_profile": {
        "nombre": "",
        "email_principal": "",
        "dominio": "",
        "alias": [],
    },
    "email_account": {
        "provider": "gmail",
        "account_email": "",
    },
    "email_settings": {
        "auto_check": True,
        "interval": 60,
    },
    "order_validation": {
        "required_fields": list(DEFAULT_REQUIRED_ORDER_FIELDS),
    },
}


class ConfigManager:
    """Persist app identity/account configuration in a dedicated JSON file."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or (Path(__file__).resolve().parents[2] / "config.json")

    def load(self) -> dict[str, Any]:
        """Load configuration, normalizing to the expected architecture."""
        if not self._config_path.exists():
            self.save(DEFAULT_CONFIG)
            return self._clone_default()

        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.save(DEFAULT_CONFIG)
            return self._clone_default()

        normalized = self._normalize(raw if isinstance(raw, dict) else {})
        if normalized != raw:
            self.save(normalized)
        return normalized

    def save(self, config: dict[str, Any]) -> None:
        """Save normalized configuration payload."""
        normalized = self._normalize(config)
        self._config_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_user_profile(self) -> dict[str, Any]:
        """Return current logical user identity."""
        return dict(self.load().get("user_profile", {}))

    def get_email_account(self) -> dict[str, Any]:
        """Return current technical email account information."""
        return dict(self.load().get("email_account", {}))

    def get_email_settings(self) -> dict[str, Any]:
        """Return email runtime settings."""
        return dict(self.load().get("email_settings", {}))

    def get_order_validation(self) -> dict[str, Any]:
        """Return order validation runtime settings."""
        return dict(self.load().get("order_validation", {}))

    @staticmethod
    def _clone_default() -> dict[str, Any]:
        return json.loads(json.dumps(DEFAULT_CONFIG))

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        config = self._clone_default()

        raw_profile = data.get("user_profile", {})
        if isinstance(raw_profile, dict):
            aliases = raw_profile.get("alias", [])
            if isinstance(aliases, str):
                alias_list = [part.strip() for part in aliases.split(",") if part.strip()]
            elif isinstance(aliases, list):
                alias_list = [str(part).strip() for part in aliases if str(part).strip()]
            else:
                alias_list = []
            config["user_profile"] = {
                "nombre": str(raw_profile.get("nombre", "")).strip(),
                "email_principal": str(raw_profile.get("email_principal", "")).strip().lower(),
                "dominio": str(raw_profile.get("dominio", "")).strip().lower(),
                "alias": alias_list,
            }

        raw_account = data.get("email_account", {})
        if isinstance(raw_account, dict):
            config["email_account"] = {
                "provider": str(raw_account.get("provider", "gmail") or "gmail").strip().lower(),
                "account_email": str(raw_account.get("account_email", "")).strip().lower(),
            }

        raw_settings = data.get("email_settings", {})
        if isinstance(raw_settings, dict):
            try:
                interval = max(10, int(raw_settings.get("interval", 60)))
            except (TypeError, ValueError):
                interval = 60
            config["email_settings"] = {
                "auto_check": bool(raw_settings.get("auto_check", True)),
                "interval": interval,
            }

        raw_order_validation = data.get("order_validation", {})
        if isinstance(raw_order_validation, dict):
            required_fields = raw_order_validation.get("required_fields", [])
            if isinstance(required_fields, list):
                normalized_required = [str(item).strip() for item in required_fields if str(item).strip()]
            else:
                normalized_required = list(DEFAULT_REQUIRED_ORDER_FIELDS)
            config["order_validation"] = {
                "required_fields": normalized_required or list(DEFAULT_REQUIRED_ORDER_FIELDS),
            }

        return config
