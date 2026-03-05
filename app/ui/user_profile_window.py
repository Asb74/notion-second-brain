"""Window to edit the singleton user profile."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.persistence.user_profile_repository import UserProfileRepository


class UserProfileWindow(tk.Toplevel):
    """Editor for user profile data used in generated signatures."""

    def __init__(self, master: tk.Misc, db_connection: sqlite3.Connection):
        super().__init__(master)
        self.repo = UserProfileRepository(db_connection)

        self.title("Perfil de usuario")
        self.geometry("520x340")
        self.resizable(False, False)

        self.nombre_var = tk.StringVar()
        self.cargo_var = tk.StringVar()
        self.empresa_var = tk.StringVar()
        self.telefono_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.dominio_var = tk.StringVar()

        self._build_layout()
        self._load_values()

    def _build_layout(self) -> None:
        form = ttk.LabelFrame(self, text="Perfil de usuario", padding=(10, 10, 10, 10))
        form.pack(fill="both", expand=True, padx=10, pady=10)

        fields = [
            ("Nombre", self.nombre_var),
            ("Cargo", self.cargo_var),
            ("Empresa", self.empresa_var),
            ("Teléfono", self.telefono_var),
            ("Email propio", self.email_var),
            ("Dominio interno", self.dominio_var),
        ]

        for index, (label, var) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="e", padx=(0, 8), pady=4)
            ttk.Entry(form, textvariable=var, width=40).grid(row=index, column=1, sticky="ew", pady=4)

        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(self, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="Guardar", command=self._save).pack(side="right")

    def _load_values(self) -> None:
        profile = self.repo.get_profile()
        self.nombre_var.set(profile.get("nombre", ""))
        self.cargo_var.set(profile.get("cargo", ""))
        self.empresa_var.set(profile.get("empresa", ""))
        self.telefono_var.set(profile.get("telefono", ""))
        self.email_var.set(profile.get("email", ""))
        self.dominio_var.set(profile.get("dominio_interno", ""))

    def _save(self) -> None:
        self.repo.save_profile(
            nombre=self.nombre_var.get(),
            cargo=self.cargo_var.get(),
            empresa=self.empresa_var.get(),
            telefono=self.telefono_var.get(),
            email=self.email_var.get(),
            dominio_interno=self.dominio_var.get(),
        )
        messagebox.showinfo("Perfil", "Perfil guardado correctamente.")
        self.destroy()
