# app/ui/excel_filter.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


EMPTY_LABEL = "(Vacías)"


@dataclass(frozen=True)
class FilterState:
    selected_values: Optional[Set[str]] = None  # valores display seleccionados


class ExcelLikeTreeFilter:
    """
    Filtro tipo Excel para ttk.Treeview:
    - Click derecho en encabezado -> popup con búsqueda + checklist
    - Valores disponibles se recalculan en base a otros filtros (excel-like)
    - Ordenación por click en encabezado (opcional)
    """

    def __init__(
        self,
        parent: tk.Misc,
        tree: ttk.Treeview,
        columns: Sequence[str],
        column_titles: Dict[str, str],
        get_data: Callable[[], List[Any]],
        row_to_display: Callable[[Any], Dict[str, str]],
        on_filtered: Callable[[List[Any]], None],
        enable_sort: bool = True,
    ) -> None:
        self.parent = parent
        self.tree = tree
        self.columns = tuple(columns)
        self.column_titles = column_titles
        self.get_data = get_data
        self.row_to_display = row_to_display
        self.on_filtered = on_filtered
        self.enable_sort = enable_sort

        self.filters: Dict[str, FilterState] = {}
        self.sort_state: Dict[str, bool] = {}  # col -> reverse
        self._popup: Optional[tk.Toplevel] = None

        # Bind click derecho en headings
        self.tree.bind("<Button-3>", self._on_heading_right_click, add="+")
        if self.enable_sort:
            for col in self.columns:
                self.tree.heading(col, command=lambda c=col: self.toggle_sort(c))

        self._update_headers()

    # ------------------------------
    # Public API
    # ------------------------------
    def clear_all_filters(self) -> None:
        self.filters.clear()
        self.apply()

    def apply(self) -> None:
        data = list(self.get_data())
        filtered = [row for row in data if self._row_matches_all_filters(row)]
        filtered = self._apply_sort(filtered)
        self.on_filtered(filtered)
        self._update_headers()

    def toggle_sort(self, col: str) -> None:
        reverse = not self.sort_state.get(col, False)
        self.sort_state = {col: reverse}
        self.apply()

    # ------------------------------
    # Internals
    # ------------------------------
    def _display_value(self, row: Any, col: str) -> str:
        d = self.row_to_display(row)
        v = d.get(col, "")
        if v is None or str(v).strip() == "":
            return EMPTY_LABEL
        return str(v)

    def _row_matches_filter(self, row: Any, col: str, st: FilterState) -> bool:
        val = self._display_value(row, col)
        if st.selected_values is None:
            return True
        return val in st.selected_values

    def _row_matches_all_filters(self, row: Any, skip_col: Optional[str] = None) -> bool:
        for col, st in self.filters.items():
            if skip_col and col == skip_col:
                continue
            if not self._row_matches_filter(row, col, st):
                return False
        return True

    def _unique_values_for_column(self, col: str) -> List[str]:
        """
        Excel-like: valores posibles para esta columna se calculan
        aplicando todos los filtros excepto el de esta columna.
        """
        vals: Set[str] = set()
        for row in self.get_data():
            if not self._row_matches_all_filters(row, skip_col=col):
                continue
            vals.add(self._display_value(row, col))

        # Asegurar que si ya había seleccionados, se mantengan visibles
        if col in self.filters and self.filters[col].selected_values:
            vals.update(self.filters[col].selected_values or set())

        return sorted(vals, key=lambda x: x.lower())

    def _apply_sort(self, rows: List[Any]) -> List[Any]:
        if not self.sort_state:
            return rows
        col = next(iter(self.sort_state))
        reverse = self.sort_state[col]
        return sorted(rows, key=lambda r: self._display_value(r, col).lower(), reverse=reverse)

    def _update_headers(self) -> None:
        for col in self.columns:
            title = self.column_titles.get(col, col)
            if col in self.filters:
                title = f"■ {title}"
            if col in self.sort_state:
                title += " ⬇️" if self.sort_state[col] else " ⬆️"
            if self.enable_sort:
                self.tree.heading(col, text=title, command=lambda c=col: self.toggle_sort(c))
            else:
                self.tree.heading(col, text=title)

    # ------------------------------
    # Popup filter UI
    # ------------------------------
    def _close_popup(self) -> None:
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None

    def _on_heading_right_click(self, event: tk.Event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "heading":
            return
        col_id = self.tree.identify_column(event.x)
        if not col_id:
            return
        try:
            idx = int(col_id.replace("#", "")) - 1
        except ValueError:
            return
        if 0 <= idx < len(self.columns):
            self.show_filter_popup(self.columns[idx], event)

    def show_filter_popup(self, col: str, event: tk.Event) -> None:
        self._close_popup()

        popup = tk.Toplevel(self.parent)
        self._popup = popup
        popup.title(f"Filtro - {self.column_titles.get(col, col)}")
        popup.transient(self.parent.winfo_toplevel())
        popup.resizable(False, False)
        try:
            popup.attributes("-topmost", True)
        except Exception:
            pass

        # Posicionar bajo el encabezado
        x = self.tree.winfo_rootx() + event.x
        y = self.tree.winfo_rooty() + event.y + 20
        popup.geometry(f"+{x}+{y}")

        container = ttk.Frame(popup, padding=8)
        container.grid(row=0, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)

        ttk.Label(container, text="Buscar:").grid(row=0, column=0, sticky="w")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(container, textvariable=search_var)
        search_entry.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        # Valores + vars
        current = self.filters.get(col)
        values_vars: Dict[str, tk.BooleanVar] = {}
        select_all_var = tk.BooleanVar(value=False)

        def ensure_vars(vals: Iterable[str]) -> None:
            for v in vals:
                if v in values_vars:
                    continue
                # si no hay filtro en esta columna -> por defecto True
                if current is None or current.selected_values is None:
                    default = True
                else:
                    default = v in current.selected_values
                values_vars[v] = tk.BooleanVar(value=default)

        def filtered_values() -> List[str]:
            vals = self._unique_values_for_column(col)
            ensure_vars(vals)
            term = search_var.get().strip().lower()
            if not term:
                return vals
            return [v for v in vals if term in v.lower()]

        def update_select_all_state() -> None:
            vals = filtered_values()
            if not vals:
                select_all_var.set(True)
                return
            select_all_var.set(all(values_vars[v].get() for v in vals))

        def toggle_select_all() -> None:
            mark = select_all_var.get()
            for v in filtered_values():
                values_vars[v].set(mark)

        ttk.Checkbutton(
            container,
            text="Seleccionar todo",
            variable=select_all_var,
            command=toggle_select_all,
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))

        # Scroll list
        list_frame = ttk.Frame(container)
        list_frame.grid(row=3, column=0, sticky="nsew")
        canvas = tk.Canvas(list_frame, height=220, highlightthickness=0)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_inner_config(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", on_inner_config)

        def on_canvas_config(e):
            canvas.itemconfigure(win_id, width=e.width)

        canvas.bind("<Configure>", on_canvas_config)

        def render_list() -> None:
            for w in inner.winfo_children():
                w.destroy()
            for v in filtered_values():
                ttk.Checkbutton(
                    inner,
                    text=v,
                    variable=values_vars[v],
                    command=update_select_all_state,
                ).pack(anchor="w")
            update_select_all_state()

        search_var.trace_add("write", lambda *_: render_list())
        render_list()

        # Botones
        btns = ttk.Frame(container)
        btns.grid(row=4, column=0, sticky="e", pady=(8, 0))

        def accept():
            selected = {v for v, var in values_vars.items() if var.get()}
            # Si todo está seleccionado, el filtro realmente no filtra -> lo quitamos
            all_vals = set(self._unique_values_for_column(col))
            if selected == all_vals:
                self.filters.pop(col, None)
            else:
                self.filters[col] = FilterState(selected_values=selected)

            self.apply()
            self._close_popup()

        def cancel():
            self._close_popup()

        ttk.Button(btns, text="Aceptar", command=accept).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Cancelar", command=cancel).pack(side="left")

        popup.bind("<Escape>", lambda _e: cancel())
        popup.bind("<FocusOut>", lambda _e: popup.after(50, lambda: (popup.focus_displayof() is None and cancel())))
        search_entry.focus_set()
