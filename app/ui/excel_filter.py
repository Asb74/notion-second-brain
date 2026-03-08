from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional, Sequence

from tkcalendar import DateEntry

from app.ui.app_icons import apply_app_icon

EMPTY_LABEL = "(Vacías)"
_TYPE_PRIORITY = {"bool": 4, "date": 3, "number": 2, "text": 1}


@dataclass
class FilterState:
    selected_values: Optional[set[str]] = None
    operator: Optional[str] = None
    value1: Any = None
    value2: Any = None

    def is_active(self) -> bool:
        has_list = self.selected_values is not None
        has_condition = bool(self.operator)
        return has_list or has_condition


class ExcelTreeFilter:
    """Reusable Excel-like filter engine for ttk.Treeview."""

    OPERATORS_BY_TYPE: dict[str, list[str]] = {
        "text": ["contiene", "no contiene", "empieza por", "termina en", "igual a", "distinto de", "vacías", "no vacías"],
        "number": ["=", "≠", ">", "<", "≥", "≤", "entre", "vacías", "no vacías"],
        "date": ["es", "antes de", "después de", "entre", "hoy", "este mes", "vacías", "no vacías"],
        "bool": ["es"],
    }

    def __init__(
        self,
        master: tk.Misc,
        tree: ttk.Treeview,
        columns: Sequence[str],
        get_rows: Callable[[], list[Any]],
        set_rows: Callable[[list[Any]], None],
        column_titles: Optional[dict[str, str]] = None,
    ) -> None:
        self.master = master
        self.tree = tree
        self.columns = tuple(columns)
        self.get_rows = get_rows
        self.set_rows = set_rows
        self.column_titles = column_titles or {}

        self.filters: dict[str, FilterState] = {}
        self.sort_column: Optional[str] = None
        self.sort_direction: Optional[str] = None  # asc | desc
        self.column_types: dict[str, str] = {c: "text" for c in self.columns}
        self._popup: Optional[tk.Toplevel] = None
        self._mode_by_col: dict[str, str] = {}

        self.tree.bind("<Button-3>", self._on_tree_right_click, add="+")
        for col in self.columns:
            self.tree.heading(col, command=lambda c=col: self.toggle_sort(c))
        self._refresh_column_types()
        self._update_headers()

    def clear_all_filters(self) -> None:
        self.filters.clear()
        self.apply()

    def has_filter(self, col: str) -> bool:
        state = self.filters.get(col)
        return bool(state and state.is_active())

    def open_filter_popup(self, col: str, x_root: int, y_root: int) -> None:
        self._refresh_column_types()
        self._close_popup()

        popup = tk.Toplevel(self.master)
        apply_app_icon(popup)
        popup.title(f"Filtro - {self.column_titles.get(col, col)}")
        popup.transient(self.master.winfo_toplevel())
        popup.resizable(False, False)
        popup.geometry(f"+{x_root}+{y_root + 8}")
        self._popup = popup

        wrap = ttk.Frame(popup, padding=8)
        wrap.grid(sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ttk.Button(wrap, text="Ordenar de menor a mayor", command=lambda: self._sort_from_popup(col, "asc")).grid(sticky="ew")
        ttk.Button(wrap, text="Ordenar de mayor a menor", command=lambda: self._sort_from_popup(col, "desc")).grid(sticky="ew", pady=(4, 6))
        ttk.Separator(wrap, orient="horizontal").grid(sticky="ew", pady=3)

        ttk.Label(wrap, text="Filtro avanzado").grid(sticky="w", pady=(4, 2))
        mode_var = tk.StringVar(value=self._mode_by_col.get(col, "list"))
        ttk.Radiobutton(wrap, text="Usar lista de valores", variable=mode_var, value="list").grid(sticky="w")
        ttk.Radiobutton(wrap, text="Usar condición", variable=mode_var, value="condition").grid(sticky="w", pady=(0, 6))

        typ = self.column_types.get(col, "text")
        operators = self.OPERATORS_BY_TYPE[typ]
        current = self.filters.get(col, FilterState())

        operator_var = tk.StringVar(value=current.operator if current.operator in operators else operators[0])
        value1_var = tk.StringVar(value="" if current.value1 is None else str(current.value1))
        value2_var = tk.StringVar(value="" if current.value2 is None else str(current.value2))

        cond_frame = ttk.Frame(wrap)
        cond_frame.grid(sticky="ew")
        cond_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(cond_frame, text="Operador").grid(row=0, column=0, sticky="w", padx=(0, 6))
        op_combo = ttk.Combobox(cond_frame, textvariable=operator_var, state="readonly", values=operators, width=18)
        op_combo.grid(row=0, column=1, sticky="ew")

        ttk.Label(cond_frame, text="Valor").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        value1_entry = ttk.Entry(cond_frame, textvariable=value1_var)
        value1_entry.grid(row=1, column=1, sticky="ew", pady=(4, 0))

        ttk.Label(cond_frame, text="Hasta").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        value2_entry = ttk.Entry(cond_frame, textvariable=value2_var)
        value2_entry.grid(row=2, column=1, sticky="ew", pady=(4, 0))

        if typ == "bool":
            op_combo.configure(state="disabled")
            value1_combo = ttk.Combobox(cond_frame, textvariable=value1_var, state="readonly", values=["True", "False"])
            value1_combo.grid(row=1, column=1, sticky="ew", pady=(4, 0))
            value1_entry.grid_remove()

        def show_date_picker(target_var: tk.StringVar) -> None:
            picker = tk.Toplevel(popup)
            apply_app_icon(picker)
            picker.title("Seleccionar fecha")
            picker.transient(popup)
            picker.resizable(False, False)
            de = DateEntry(picker, date_pattern="yyyy-mm-dd")
            de.pack(padx=8, pady=8)

            def choose() -> None:
                target_var.set(de.get_date().isoformat())
                picker.destroy()

            ttk.Button(picker, text="Aceptar", command=choose).pack(pady=(0, 8))

        if typ == "date":
            ttk.Button(cond_frame, text="📅", width=3, command=lambda: show_date_picker(value1_var)).grid(row=1, column=2, padx=(4, 0), pady=(4, 0))
            ttk.Button(cond_frame, text="📅", width=3, command=lambda: show_date_picker(value2_var)).grid(row=2, column=2, padx=(4, 0), pady=(4, 0))

        ttk.Separator(wrap, orient="horizontal").grid(sticky="ew", pady=6)
        ttk.Label(wrap, text="Buscar").grid(sticky="w")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(wrap, textvariable=search_var)
        search_entry.grid(sticky="ew", pady=(0, 4))

        state = self.filters.get(col)
        values_vars: dict[str, tk.BooleanVar] = {}
        select_all_var = tk.BooleanVar(value=True)

        def available_values() -> list[str]:
            vals = self._unique_values_for_column(col)
            term = search_var.get().strip().lower()
            if not term:
                return vals
            return [v for v in vals if term in v.lower()]

        def ensure_vars(vals: list[str]) -> None:
            for v in vals:
                if v in values_vars:
                    continue
                default = True if not state or state.selected_values is None else v in state.selected_values
                values_vars[v] = tk.BooleanVar(value=default)

        def on_select_all() -> None:
            for v in available_values():
                values_vars[v].set(select_all_var.get())

        ttk.Checkbutton(wrap, text="Seleccionar todo", variable=select_all_var, command=on_select_all).grid(sticky="w")
        ttk.Button(wrap, text="Seleccionar resultados de la búsqueda", command=lambda: self._mark_search_results(available_values(), values_vars)).grid(sticky="w", pady=(2, 4))

        list_frame = ttk.Frame(wrap)
        list_frame.grid(sticky="nsew")
        canvas = tk.Canvas(list_frame, height=170, highlightthickness=0)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync_scroll(*_args: Any) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", sync_scroll)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

        def render_values() -> None:
            for widget in inner.winfo_children():
                widget.destroy()
            vals = available_values()
            ensure_vars(vals)
            for val in vals:
                ttk.Checkbutton(inner, text=val, variable=values_vars[val]).pack(anchor="w")

        search_var.trace_add("write", lambda *_: render_values())
        render_values()

        def update_condition_visibility(*_args: Any) -> None:
            enabled = mode_var.get() == "condition"
            for widget in (op_combo, value1_entry, value2_entry):
                if widget.winfo_ismapped():
                    widget.configure(state="normal" if enabled else "disabled")
            op = operator_var.get()
            need_v1 = op not in {"vacías", "no vacías", "hoy", "este mes"}
            need_v2 = op == "entre"
            if value1_entry.winfo_ismapped():
                value1_entry.configure(state=("normal" if enabled and need_v1 else "disabled"))
            if value2_entry.winfo_ismapped():
                value2_entry.configure(state=("normal" if enabled and need_v2 else "disabled"))

        mode_var.trace_add("write", update_condition_visibility)
        operator_var.trace_add("write", update_condition_visibility)
        update_condition_visibility()

        btns = ttk.Frame(wrap)
        btns.grid(sticky="e", pady=(8, 0))

        def accept() -> None:
            self._mode_by_col[col] = mode_var.get()
            if mode_var.get() == "list":
                all_vals = set(self._unique_values_for_column(col))
                selected = {v for v, var in values_vars.items() if var.get()}
                if selected == all_vals:
                    self.filters.pop(col, None)
                else:
                    self.filters[col] = FilterState(selected_values=selected)
            else:
                op = operator_var.get()
                if typ == "bool":
                    self.filters[col] = FilterState(operator="es", value1=value1_var.get())
                elif op:
                    self.filters[col] = FilterState(operator=op, value1=value1_var.get().strip(), value2=value2_var.get().strip())
                else:
                    self.filters.pop(col, None)
            self.apply()
            self._close_popup()

        ttk.Button(btns, text="Aceptar", command=accept).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Cancelar", command=self._close_popup).pack(side="left")
        search_entry.focus_set()

    def set_sort(self, col: str, direction: str) -> None:
        self.sort_column = col
        self.sort_direction = direction if direction in {"asc", "desc"} else "asc"
        self.apply()

    def toggle_sort(self, col: str) -> None:
        if self.sort_column != col:
            self.set_sort(col, "asc")
            return
        self.set_sort(col, "desc" if self.sort_direction == "asc" else "asc")

    def apply(self) -> None:
        self._refresh_column_types()
        rows = list(self.get_rows())
        rows = [r for r in rows if self._row_matches_all_filters(r)]
        rows = self._apply_sort(rows)
        self.set_rows(rows)
        self._update_headers()

    def _row_value(self, row: Any, col: str) -> Any:
        idx = self.columns.index(col)
        if isinstance(row, dict):
            return row.get(col)
        return row[idx]

    def _row_matches_all_filters(self, row: Any, skip_col: Optional[str] = None) -> bool:
        for col, state in self.filters.items():
            if skip_col == col:
                continue
            if not self._row_matches_filter(row, col, state):
                return False
        return True

    def _row_matches_filter(self, row: Any, col: str, state: FilterState) -> bool:
        typ = self.column_types.get(col, "text")
        raw = self._row_value(row, col)
        norm = self._normalize_value(raw)
        display = self._display_value(raw)

        if state.selected_values is not None:
            return display in state.selected_values

        if not state.operator:
            return True

        op = state.operator
        parsed = self._parse_typed(norm, typ)
        p1 = self._parse_typed(state.value1, typ)
        p2 = self._parse_typed(state.value2, typ)

        if op == "vacías":
            return norm is None
        if op == "no vacías":
            return norm is not None
        if typ == "bool":
            return parsed == self._to_bool(state.value1)
        if parsed is None:
            return False
        if op in {"contiene", "no contiene", "empieza por", "termina en", "igual a", "distinto de"}:
            left = str(parsed).lower()
            right = str(p1 or "").lower()
            if op == "contiene":
                return right in left
            if op == "no contiene":
                return right not in left
            if op == "empieza por":
                return left.startswith(right)
            if op == "termina en":
                return left.endswith(right)
            if op == "igual a":
                return left == right
            return left != right
        if op == "=":
            return parsed == p1
        if op == "≠":
            return parsed != p1
        if op == ">":
            return p1 is not None and parsed > p1
        if op == "<":
            return p1 is not None and parsed < p1
        if op == "≥":
            return p1 is not None and parsed >= p1
        if op == "≤":
            return p1 is not None and parsed <= p1
        if op == "entre":
            return p1 is not None and p2 is not None and p1 <= parsed <= p2
        if op == "es":
            return parsed == p1
        if op == "antes de":
            return p1 is not None and parsed < p1
        if op == "después de":
            return p1 is not None and parsed > p1
        if op == "hoy":
            return parsed == date.today()
        if op == "este mes" and isinstance(parsed, date):
            now = date.today()
            return parsed.year == now.year and parsed.month == now.month
        return True

    def _apply_sort(self, rows: list[Any]) -> list[Any]:
        if not self.sort_column:
            return rows
        col = self.sort_column
        typ = self.column_types.get(col, "text")
        reverse = self.sort_direction == "desc"

        def key_fn(row: Any) -> tuple[int, Any]:
            parsed = self._parse_typed(self._row_value(row, col), typ)
            return (1, None) if parsed is None else (0, parsed)

        return sorted(rows, key=key_fn, reverse=reverse)

    def _unique_values_for_column(self, col: str) -> list[str]:
        values: set[str] = set()
        for row in self.get_rows():
            if not self._row_matches_all_filters(row, skip_col=col):
                continue
            values.add(self._display_value(self._row_value(row, col)))
        current = self.filters.get(col)
        if current and current.selected_values:
            values.update(current.selected_values)
        return sorted(values, key=lambda v: v.lower())

    def _refresh_column_types(self) -> None:
        rows = list(self.get_rows())
        sample = rows[:200]
        for col in self.columns:
            counts = {"bool": 0, "date": 0, "number": 0, "text": 0}
            for row in sample:
                norm = self._normalize_value(self._row_value(row, col))
                if norm is None:
                    continue
                val_type = self._detect_value_type(norm)
                counts[val_type] += 1
            self.column_types[col] = max(counts, key=lambda k: (counts[k], _TYPE_PRIORITY[k])) if any(counts.values()) else "text"

    def _detect_value_type(self, value: Any) -> str:
        if self._to_bool(value) is not None:
            return "bool"
        if self._to_date(value) is not None:
            return "date"
        if self._to_number(value) is not None:
            return "number"
        return "text"

    def _parse_typed(self, value: Any, typ: str) -> Any:
        norm = self._normalize_value(value)
        if norm is None:
            return None
        if typ == "bool":
            return self._to_bool(norm)
        if typ == "date":
            return self._to_date(norm)
        if typ == "number":
            return self._to_number(norm)
        return str(norm)

    def _normalize_value(self, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if text == "" or text.lower() == EMPTY_LABEL.lower():
            return None
        return text

    def _to_bool(self, value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "sí", "si", "yes", "y", "verdadero"}:
            return True
        if text in {"false", "0", "no", "falso"}:
            return False
        return None

    def _to_number(self, value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(" ", "")
        if text.count(",") == 1 and text.count(".") == 0:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None

    def _to_date(self, value: Any) -> Optional[date]:
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _display_value(self, value: Any) -> str:
        norm = self._normalize_value(value)
        return EMPTY_LABEL if norm is None else str(norm)

    def _update_headers(self) -> None:
        for col in self.columns:
            title = self.column_titles.get(col, col)
            if self.has_filter(col):
                title = f"■ {title}"
            if self.sort_column == col:
                title += " ▲" if self.sort_direction == "asc" else " ▼"
            self.tree.heading(col, text=title, command=lambda c=col: self.toggle_sort(c))

    def _mark_search_results(self, vals: list[str], values_vars: dict[str, tk.BooleanVar]) -> None:
        for val in vals:
            values_vars[val].set(True)

    def _sort_from_popup(self, col: str, direction: str) -> None:
        self.set_sort(col, direction)
        self._close_popup()

    def _on_tree_right_click(self, event: tk.Event) -> None:
        if self.tree.identify("region", event.x, event.y) != "heading":
            return
        col_id = self.tree.identify_column(event.x)
        if not col_id or not col_id.startswith("#"):
            return
        idx = int(col_id[1:]) - 1
        if 0 <= idx < len(self.columns):
            self.open_filter_popup(self.columns[idx], event.x_root, event.y_root)

    def _close_popup(self) -> None:
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None


# backward compatibility
ExcelLikeTreeFilter = ExcelTreeFilter
