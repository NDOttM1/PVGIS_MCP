"""Dateiausgabe (CSV, EPW, JSON) in den konfigurierten Ausgabeordner."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


def _slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return text.strip("_")[:80] or "export"


def build_name(tool: str, latitude: float | None = None, longitude: float | None = None,
               label: str | None = None, ext: str = "csv") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parts = [stamp, tool]
    if latitude is not None and longitude is not None:
        parts.append(f"{latitude:.4f}_{longitude:.4f}")
    if label:
        parts.append(label)
    return _slug("_".join(parts)) + f".{ext}"


def _num(value: Any, style: str) -> Any:
    if style == "de" and isinstance(value, float):
        return f"{value:.6g}".replace(".", ",") if abs(value) < 1e15 else str(value)
    return value


def write_table(out_dir: Path, filename: str, header: Sequence[str], rows: Iterable[Sequence[Any]],
                style: str = "de") -> Path:
    """Schreibt eine Tabelle. Stil "de": Semikolon, Dezimalkomma, UTF-8 mit BOM (öffnet direkt in Excel DE)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    delimiter = ";" if style == "de" else ","
    encoding = "utf-8-sig" if style == "de" else "utf-8"
    with path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(header)
        for row in rows:
            writer.writerow([_num(v, style) for v in row])
    return path


def write_text(out_dir: Path, filename: str, content: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def write_json(out_dir: Path, filename: str, data: Any) -> Path:
    return write_text(out_dir, filename, json.dumps(data, ensure_ascii=False, indent=2))


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in reader if r]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def to_number(value: str) -> Any:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return int(f) if f.is_integer() and "." not in value and "e" not in value.lower() else f


def split_constant_columns(header: list[str], rows: list[list[str]], keep: Sequence[str] = ("Time",)
                           ) -> tuple[list[str], list[list[Any]], dict[str, Any]]:
    """Trennt Spalten mit konstantem Inhalt (Metadaten) von den eigentlichen Zeitreihen."""
    if not rows:
        return header, [], {}
    constant: dict[str, Any] = {}
    varying_idx = []
    for i, name in enumerate(header):
        col = {r[i] for r in rows if i < len(r)}
        if name not in keep and len(col) == 1 and len(rows) > 1:
            constant[name] = to_number(next(iter(col)))
        else:
            varying_idx.append(i)
    new_header = [header[i] for i in varying_idx]
    new_rows = [[to_number(r[i]) if i < len(r) else None for i in varying_idx] for r in rows]
    return new_header, new_rows, constant
