"""Tkinter dialog for app settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from app.config.config_manager import ConfigManager
from app.core.models import AppSettings
from app.ui.app_icons import apply_app_icon
from app.ui.dictation_widgets import attach_dictation


class SettingsDialog(tk.Toplevel):
    """Modal settings editor grouped by domain tabs."""

    def __init__(
        self,
        parent: tk.Misc,
        current: AppSettings,
        on_save: Callable[[AppSettings], None],
        load_master_values: Callable[[str], list[str]],
        add_master_value: Callable[[str, str], None],
        delete_master_value: Callable[[str, str], None],
        on_open_master: Callable[[str], None] | None = None,
        initial_tab: str = "General",
    ):
        super().__init__(parent)
        self.title("Configuración")
        apply_app_icon(self)
        self.resizable(True, True)
        self.geometry("820x620")
        self.minsize(760, 540)
        self.transient(parent)
        self.grab_set()

        self._current = current
        self._on_save = on_save
        self._load_master_values = load_master_values
        self._add_master = add_master_value
        self._delete_master = delete_master_value
        self._on_open_master = on_open_master
        self.config_manager = ConfigManager()

        self.areas_list: tk.Listbox | None = None
        self.tipos_list: tk.Listbox | None = None

        self.config_vars: dict[str, tk.Variable] = {
            "notion_token": tk.StringVar(),
            "notion_database_id": tk.StringVar(),
            "managed_email": tk.StringVar(),
            "nombre": tk.StringVar(),
            "email_principal": tk.StringVar(),
            "dominio": tk.StringVar(),
            "alias": tk.StringVar(),
            "default_area": tk.StringVar(),
            "default_tipo": tk.StringVar(),
            "default_estado": tk.StringVar(),
            "default_prioridad": tk.StringVar(),
            "prop_title": tk.StringVar(),
            "prop_area": tk.StringVar(),
            "prop_tipo": tk.StringVar(),
            "prop_estado": tk.StringVar(),
            "prop_fecha": tk.StringVar(),
            "prop_prioridad": tk.StringVar(),
            "auto_check_email": tk.BooleanVar(),
            "email_interval": tk.IntVar(),
            "process_attachments": tk.BooleanVar(),
            "notifications_enabled": tk.BooleanVar(),
            "notifications_toast": tk.BooleanVar(),
            "notifications_sound": tk.BooleanVar(),
        }

        self._load_config()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        self.tab_general = ttk.Frame(self.notebook)
        self.tab_email = ttk.Frame(self.notebook)
        self.tab_notifications = ttk.Frame(self.notebook)
        self.tab_master_data = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_general, text="General")
        self.notebook.add(self.tab_email, text="Email")
        self.notebook.add(self.tab_notifications, text="Notificaciones")
        self.notebook.add(self.tab_master_data, text="Datos maestros")

        self._build_general_tab()
        self._build_email_tab()
        self._build_notifications_tab()
        self._build_master_data_tab()
        self._build_footer()

        tab_map = {
            "general": 0,
            "email": 1,
            "notificaciones": 2,
            "datos maestros": 3,
        }
        self.notebook.select(tab_map.get(initial_tab.lower().strip(), 0))

    def _create_tab_body(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        return frame

    def _add_field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        var: tk.Variable,
        field_type: str = "entry",
        show: str | None = None,
    ) -> tk.Widget:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)

        if field_type == "entry":
            widget = ttk.Entry(parent, textvariable=var, show=show or "")
        elif field_type == "checkbox":
            widget = ttk.Checkbutton(parent, variable=var)
        elif field_type == "number":
            widget = ttk.Entry(parent, textvariable=var)
        else:
            raise ValueError(f"Tipo de campo no soportado: {field_type}")

        widget.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)
        return widget

    def _build_general_tab(self) -> None:
        body = self._create_tab_body(self.tab_general)

        token = self._add_field(body, 0, "Notion Token", self.config_vars["notion_token"], show="*")
        if isinstance(token, ttk.Entry):
            attach_dictation(token, body).grid(row=0, column=2, sticky="w", padx=(6, 0), pady=4)

        self._add_field(body, 1, "Notion Database ID", self.config_vars["notion_database_id"])
        self._add_field(body, 2, "Carpeta destino", self.config_vars["default_area"])
        self._add_field(body, 3, "Nombre", self.config_vars["nombre"])
        self._add_field(body, 4, "Email principal", self.config_vars["email_principal"])
        self._add_field(body, 5, "Dominio corporativo", self.config_vars["dominio"])
        self._add_field(body, 6, "Alias (separados por coma)", self.config_vars["alias"])

    def _build_email_tab(self) -> None:
        body = self._create_tab_body(self.tab_email)

        self._add_field(body, 0, "Correo gestionado", self.config_vars["managed_email"])
        self._add_field(body, 1, "Activar revisión automática", self.config_vars["auto_check_email"], "checkbox")
        self._add_field(body, 2, "Intervalo (segundos)", self.config_vars["email_interval"], "number")
        self._add_field(body, 3, "Procesar adjuntos", self.config_vars["process_attachments"], "checkbox")

    def _build_notifications_tab(self) -> None:
        body = self._create_tab_body(self.tab_notifications)

        self._add_field(body, 0, "Activar notificaciones", self.config_vars["notifications_enabled"], "checkbox")
        self._add_field(body, 1, "Notificación tipo globo (toast)", self.config_vars["notifications_toast"], "checkbox")
        self._add_field(body, 2, "Sonido activado", self.config_vars["notifications_sound"], "checkbox")

    def _build_master_data_tab(self) -> None:
        body = self._create_tab_body(self.tab_master_data)

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="Áreas").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(body, text="Tipos").grid(row=0, column=1, sticky="w", pady=(0, 4))

        self.areas_list = tk.Listbox(body, height=10)
        self.areas_list.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))

        self.tipos_list = tk.Listbox(body, height=10)
        self.tipos_list.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        self._reload_master_lists()

        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Añadir", command=self._add_master_item).pack(side="right", padx=(6, 0))
        ttk.Button(actions, text="Eliminar", command=self._remove_master_item).pack(side="right")

    def _build_footer(self) -> None:
        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(footer, text="Guardar", command=self._save_config).pack(side="right", padx=(5, 0))
        ttk.Button(footer, text="Cancelar", command=self.destroy).pack(side="right")

    def _load_config(self) -> None:
        runtime_config = self.config_manager.load()
        profile = runtime_config.get("user_profile", {})
        email_settings = runtime_config.get("email_settings", {})
        email_account = runtime_config.get("email_account", {})
        config = {
            "notion_token": self._current.notion_token,
            "notion_database_id": self._current.notion_database_id,
            "managed_email": str(email_account.get("account_email", "")).strip() or self._current.managed_email,
            "nombre": str(profile.get("nombre", "")).strip(),
            "email_principal": str(profile.get("email_principal", "")).strip(),
            "dominio": str(profile.get("dominio", "")).strip(),
            "alias": ",".join(profile.get("alias", [])),
            "default_area": self._current.default_area,
            "default_tipo": self._current.default_tipo,
            "default_estado": self._current.default_estado,
            "default_prioridad": self._current.default_prioridad,
            "prop_title": self._current.prop_title,
            "prop_area": self._current.prop_area,
            "prop_tipo": self._current.prop_tipo,
            "prop_estado": self._current.prop_estado,
            "prop_fecha": self._current.prop_fecha,
            "prop_prioridad": self._current.prop_prioridad,
            "auto_check_email": bool(email_settings.get("auto_check", True)),
            "email_interval": int(email_settings.get("interval", 60)),
            "process_attachments": True,
            "notifications_enabled": True,
            "notifications_toast": True,
            "notifications_sound": False,
        }
        for key, var in self.config_vars.items():
            var.set(config.get(key))

    def _add_master_item(self) -> None:
        target = self._get_active_master_target()
        if target is None:
            messagebox.showinfo("Datos maestros", "Selecciona la lista de Áreas o Tipos para añadir elementos.", parent=self)
            return
        category, _ = target
        value = simpledialog.askstring("Añadir", f"Nuevo valor para {category}:", parent=self)
        if value is None:
            return
        clean = value.strip()
        if not clean:
            return
        try:
            self._add_master(category, clean)
            self._reload_master_lists()
        except Exception as exc:  # pragma: no cover - defensive UI safeguard
            messagebox.showerror("Error", f"No se pudo añadir el valor: {exc}", parent=self)

    def _remove_master_item(self) -> None:
        target = self._get_active_master_target()
        if target is None:
            messagebox.showinfo(
                "Datos maestros",
                "Selecciona un elemento de Áreas o Tipos para eliminar.",
                parent=self,
            )
            return
        category, list_widget = target
        selected = list_widget.curselection()
        if not selected:
            return
        value = str(list_widget.get(selected[0])).strip()
        if not value:
            return
        try:
            self._delete_master(category, value)
            self._reload_master_lists()
        except Exception as exc:  # pragma: no cover - defensive UI safeguard
            messagebox.showerror("Error", f"No se pudo eliminar el valor: {exc}", parent=self)

    def _reload_master_lists(self) -> None:
        if not self.areas_list or not self.tipos_list:
            return
        self.areas_list.delete(0, tk.END)
        self.tipos_list.delete(0, tk.END)

        for value in self._load_master_values("Area"):
            self.areas_list.insert(tk.END, value)

        for value in self._load_master_values("Tipo"):
            self.tipos_list.insert(tk.END, value)

    def _get_active_master_target(self) -> tuple[str, tk.Listbox] | None:
        if self.areas_list and self.areas_list.curselection():
            return ("Area", self.areas_list)
        if self.tipos_list and self.tipos_list.curselection():
            return ("Tipo", self.tipos_list)
        focus_widget = self.focus_get()
        if focus_widget is self.areas_list:
            return ("Area", self.areas_list)
        if focus_widget is self.tipos_list:
            return ("Tipo", self.tipos_list)
        return None

    def _validate_config(self) -> bool:
        interval = int(self.config_vars["email_interval"].get() or 0)
        if interval <= 0:
            messagebox.showerror("Validación", "El intervalo de revisión debe ser mayor que 0.", parent=self)
            return False

        if not str(self.config_vars["notion_token"].get()).strip():
            messagebox.showwarning(
                "Validación",
                "El Notion token está vacío. Puedes guardar, pero la integración con Notion fallará.",
                parent=self,
            )

        if not str(self.config_vars["email_principal"].get()).strip():
            messagebox.showerror(
                "Validación",
                "El Email principal es obligatorio para respuestas automáticas y firma.",
                parent=self,
            )
            return False
        return True

    def _save_config(self) -> None:
        try:
            if not self._validate_config():
                return

            config = {key: var.get() for key, var in self.config_vars.items()}

            settings = AppSettings(
                notion_token=str(config["notion_token"]).strip(),
                notion_database_id=str(config["notion_database_id"]).strip(),
                managed_email=str(config["managed_email"]).strip(),
                default_area=str(config["default_area"]).strip(),
                default_tipo=str(config["default_tipo"]).strip(),
                default_estado=str(config["default_estado"]).strip() or "Pendiente",
                default_prioridad=str(config["default_prioridad"]).strip() or "Media",
                prop_title=str(config["prop_title"]).strip() or "Actividad",
                prop_area=str(config["prop_area"]).strip() or "Area",
                prop_tipo=str(config["prop_tipo"]).strip() or "Tipo",
                prop_estado=str(config["prop_estado"]).strip() or "Estado",
                prop_fecha=str(config["prop_fecha"]).strip() or "Fecha",
                prop_prioridad=str(config["prop_prioridad"]).strip() or "Prioridad",
                max_attempts=self._current.max_attempts,
                retry_delay_seconds=self._current.retry_delay_seconds,
            )
            self._on_save(settings)
            config = self.config_manager.load()
            config["user_profile"] = {
                "nombre": str(self.config_vars["nombre"].get()).strip(),
                "email_principal": str(self.config_vars["email_principal"].get()).strip().lower(),
                "dominio": str(self.config_vars["dominio"].get()).strip().lower(),
                "alias": [
                    alias.strip().lower()
                    for alias in str(self.config_vars["alias"].get()).split(",")
                    if alias.strip()
                ],
            }
            config["email_account"] = {
                "provider": "gmail",
                "account_email": str(self.config_vars["managed_email"].get()).strip().lower(),
            }
            config["email_settings"] = {
                "auto_check": bool(self.config_vars["auto_check_email"].get()),
                "interval": max(10, int(self.config_vars["email_interval"].get() or 60)),
            }
            self.config_manager.save(config)
            self.destroy()
        except Exception as exc:  # pragma: no cover - defensive UI safeguard
            messagebox.showerror(
                "Error",
                f"No se pudo guardar la configuración: {exc}",
                parent=self,
            )
