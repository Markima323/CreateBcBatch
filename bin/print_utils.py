from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

try:
    import fitz
except ImportError:
    fitz = None

try:
    import win32con
    import win32print
    import win32ui
    from PIL import Image, ImageWin
except ImportError:
    win32con = None
    win32print = None
    win32ui = None
    Image = None
    ImageWin = None


def _require_windows_printing() -> None:
    if not all([win32print, win32ui, Image, ImageWin]):
        raise RuntimeError("Missing Windows printing dependencies. Install pywin32 and Pillow.")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_system_printers() -> list[dict[str, str]]:
    if not win32print:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    items = win32print.EnumPrinters(flags)
    printers: list[dict[str, str]] = []
    for item in items:
        name = normalize_text(item[2])
        if name:
            printers.append({"name": name})
    return printers


def match_printer(printers: list[dict[str, str]], saved_name: str) -> str:
    target = normalize_text(saved_name).lower()
    if not target:
        return ""
    for printer in printers:
        name = normalize_text(printer.get("name"))
        if name.lower() == target:
            return name
    return ""


def print_png(image_path: Path, printer_name: str, width_mm: float, height_mm: float, orientation: str) -> None:
    _require_windows_printing()
    image = Image.open(image_path).convert("RGB")
    horizontal = normalize_text(orientation).lower().startswith("h")
    if horizontal and image.height > image.width:
        image = image.rotate(90, expand=True)
    if not horizontal and image.width > image.height:
        image = image.rotate(90, expand=True)

    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer_name)
    try:
        printable_width = dc.GetDeviceCaps(win32con.HORZRES)
        printable_height = dc.GetDeviceCaps(win32con.VERTRES)
        offset_x = dc.GetDeviceCaps(win32con.PHYSICALOFFSETX)
        offset_y = dc.GetDeviceCaps(win32con.PHYSICALOFFSETY)

        scale = min(printable_width / image.width, printable_height / image.height)
        target_width = max(1, int(image.width * scale))
        target_height = max(1, int(image.height * scale))
        left = offset_x + max(0, (printable_width - target_width) // 2)
        top = offset_y + max(0, (printable_height - target_height) // 2)
        right = left + target_width
        bottom = top + target_height

        dc.StartDoc(str(image_path.name))
        dc.StartPage()
        dib = ImageWin.Dib(image)
        dib.draw(dc.GetHandleOutput(), (left, top, right, bottom))
        dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()


def print_pdf(pdf_path: Path, printer_name: str, width_mm: float, height_mm: float, orientation: str, dpi: int) -> None:
    if fitz is None:
        raise RuntimeError("Missing dependency 'PyMuPDF'.")
    with tempfile.TemporaryDirectory(prefix="container_batch_print_") as temp_dir:
        temp_png = Path(temp_dir) / (pdf_path.stem + ".png")
        document = fitz.open(str(pdf_path))
        try:
            page = document[0]
            matrix = fitz.Matrix(max(1.0, dpi / 72.0), max(1.0, dpi / 72.0))
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(str(temp_png))
        finally:
            document.close()
        print_png(temp_png, printer_name, width_mm, height_mm, orientation)
