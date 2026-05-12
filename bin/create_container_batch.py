from __future__ import annotations

import argparse
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

try:
    import fitz
except ImportError:
    fitz = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit("Missing dependency 'Pillow'. Run install_dependencies.bat or pip install -r requirements.txt.") from exc

from print_utils import match_printer, print_pdf, read_system_printers

OPTION_FIELDS = ("type", "skuQuantity", "area", "size")
OPTION_LABELS = {
    "type": "Type / 类型",
    "skuQuantity": "SKU Quantity / SKU数量",
    "area": "Area / 区域",
    "size": "Size / 尺码",
}
CARD_CODE128_START_B = 104
CARD_CODE128_PATTERNS = {
    0: "11011001100",
    1: "11001101100",
    2: "11001100110",
    3: "10010011000",
    4: "10010001100",
    5: "10001001100",
    6: "10011001000",
    7: "10011000100",
    8: "10001100100",
    9: "11001001000",
    10: "11001000100",
    11: "11000100100",
    12: "10110011100",
    13: "10011011100",
    14: "10011001110",
    15: "10111001100",
    16: "10011101100",
    17: "10011100110",
    18: "11001110010",
    19: "11001011100",
    20: "11001001110",
    21: "11011100100",
    22: "11001110100",
    23: "11101101110",
    24: "11101001100",
    25: "11100101100",
    26: "11100100110",
    27: "11101100100",
    28: "11100110100",
    29: "11100110010",
    30: "11011011000",
    31: "11011000110",
    32: "11000110110",
    33: "10100011000",
    34: "10001011000",
    35: "10001000110",
    36: "10110001000",
    37: "10001101000",
    38: "10001100010",
    39: "11010001000",
    40: "11000101000",
    41: "11000100010",
    42: "10110111000",
    43: "10110001110",
    44: "10001101110",
    45: "10111011000",
    46: "10111000110",
    47: "10001110110",
    48: "11101110110",
    49: "11010001110",
    50: "11000101110",
    51: "11011101000",
    52: "11011100010",
    53: "11011101110",
    54: "11101011000",
    55: "11101000110",
    56: "11100010110",
    57: "11101101000",
    58: "11101100010",
    59: "11100011010",
    60: "11101111010",
    61: "11001000010",
    62: "11110001010",
    63: "10100110000",
    64: "10100001100",
    65: "10010110000",
    66: "10010000110",
    67: "10000101100",
    68: "10000100110",
    69: "10110010000",
    70: "10110000100",
    71: "10011010000",
    72: "10011000010",
    73: "10000110100",
    74: "10000110010",
    75: "11000010010",
    76: "11001010000",
    77: "11110111010",
    78: "11000010100",
    79: "10001111010",
    80: "10100111100",
    81: "10010111100",
    82: "10010011110",
    83: "10111100100",
    84: "10011110100",
    85: "10011110010",
    86: "11110100100",
    87: "11110010100",
    88: "11110010010",
    89: "11011011110",
    90: "11011110110",
    91: "11110110110",
    92: "10101111000",
    93: "10100011110",
    94: "10001011110",
    95: "10111101000",
    96: "10111100010",
    97: "11110101000",
    98: "11110100010",
    99: "10111011110",
    100: "10111101110",
    101: "11101011110",
    102: "11110101110",
    103: "11010000100",
    104: "11010010000",
    105: "11010011100",
    106: "1100011101011",
}
CARD_CODE128_STOP = "1100011101011"
TEMPLATE_BASE_WIDTH_PT = 595.276
TEMPLATE_BASE_HEIGHT_PT = 419.528


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, dict):
        if "name" in value:
            return normalize_text(value.get("name"))
        if "username" in value:
            return normalize_text(value.get("username"))
    return str(value).strip()


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def mask(value: str) -> str:
    text = normalize_text(value)
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def cleanup_generated_pdfs(config: dict[str, Any]) -> None:
    keep_paths = {
        config["label_template_path"].resolve(),
        (config["base_dir"] / "1.pdf").resolve(),
    }

    for pdf_path in config["output_dir"].rglob("*.pdf"):
        try:
            pdf_path.unlink()
        except OSError:
            continue

    for pdf_path in config["base_dir"].glob("*.pdf"):
        resolved = pdf_path.resolve()
        if resolved in keep_paths:
            continue
        try:
            pdf_path.unlink()
        except OSError:
            continue


def mm_to_px(mm: float, dpi: int) -> int:
    return max(1, int(round(mm * dpi / 25.4)))


def pt_to_px(pt: float, dpi: int) -> int:
    return max(1, int(round(pt * dpi / 72.0)))


def safe_filename_stem(text: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in normalize_text(text))
    cleaned = cleaned.strip("_")
    return cleaned or "item"


def canonical_option_value(value: str, options: list[str]) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    for option in options:
        option_text = normalize_text(option)
        if text == option_text:
            return option_text
        if text.split(".", 1)[0] == option_text.split(".", 1)[0]:
            return option_text
    return text


def json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_font(size: int, bold: bool = False, family: str = "default") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    normalized = normalize_text(family).lower()
    if normalized in {"simsun", "songti", "song"}:
        candidates = [
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\SimsunExtG.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttf" if bold else r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font, stroke_width=0)


def fit_font(draw: ImageDraw.ImageDraw, text: str, width: int, height: int, max_size: int, min_size: int, family: str = "default", bold: bool = False) -> ImageFont.ImageFont:
    chosen = load_font(min_size, family=family, bold=bold)
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size, family=family, bold=bold)
        bbox = text_bbox(draw, text, font)
        if (bbox[2] - bbox[0]) <= width and (bbox[3] - bbox[1]) <= height:
            return font
        chosen = font
    return chosen


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int], family: str = "default", bold: bool = False, align: str = "left") -> None:
    value = normalize_text(text)
    if not value:
        return
    x, y = xy
    stroke_width = 0
    if bold and normalize_text(family).lower() in {"simsun", "songti", "song"}:
        stroke_width = 1
    draw.text(
        (x, y),
        value,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=fill,
        align=align,
    )


def load_runtime_config(config_path: Path) -> dict[str, Any]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    output = raw.get("output", {})
    runtime = {
        "config_path": config_path.resolve(),
        "base_dir": config_path.parent.resolve(),
        "cache_path": (config_path.parent / output.get("cacheFile", "cache.json")).resolve(),
        "output_dir": (config_path.parent / output.get("outputDir", "output")).resolve(),
        "print_settings_path": (config_path.parent / output.get("printSettingsFile", "print_settings.json")).resolve(),
        "label_template_path": (config_path.parent / raw.get("label", {}).get("templatePdf", "Template.pdf")).resolve(),
        "api": raw["api"],
        "fields": raw["fields"],
        "defaults": raw["defaults"],
        "ui": raw["ui"],
        "label": raw["label"],
        "optionSeed": raw["options"],
    }
    runtime["token"] = raw["api"]["token"]
    runtime["app_id"] = raw["api"]["appId"]
    runtime["entry_id"] = raw["api"]["entryId"]
    runtime["create_url"] = raw["api"]["baseUrl"].rstrip("/") + "/app/entry/data/create"
    runtime["get_url"] = raw["api"]["baseUrl"].rstrip("/") + "/app/entry/data/get"
    runtime["list_url"] = raw["api"]["baseUrl"].rstrip("/") + "/app/entry/data/list"
    runtime["output_dir"].mkdir(parents=True, exist_ok=True)
    return runtime


def load_cache(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    seed_selection = {field_name: normalize_text(config["optionSeed"].get(field_name, [""])[0]) for field_name in OPTION_FIELDS}
    seed_selection["batchCount"] = int(config["ui"]["batchCountDefault"])
    seed_selection["autoPrint"] = True
    default_payload = {
        "lastSelection": seed_selection,
        "options": {field_name: list(config["optionSeed"].get(field_name, [])) for field_name in OPTION_FIELDS},
    }
    loaded = json_load(path, default_payload)
    if not isinstance(loaded, dict):
        return default_payload
    last_selection = loaded.get("lastSelection") or {}
    options = loaded.get("options") or {}
    result = {
        "lastSelection": {
            **seed_selection,
            **{key: last_selection.get(key, seed_selection.get(key)) for key in seed_selection},
        },
        "options": {
            field_name: unique_strings(list(config["optionSeed"].get(field_name, [])) + list(options.get(field_name) or []))
            for field_name in OPTION_FIELDS
        },
    }
    return result


def save_cache(path: Path, payload: dict[str, Any]) -> None:
    json_dump(path, payload)


def load_print_settings(path: Path) -> dict[str, Any]:
    loaded = json_load(path, {})
    return loaded if isinstance(loaded, dict) else {}


def save_print_settings(path: Path, printer_name: str, width_mm: float, height_mm: float, orientation: str) -> None:
    json_dump(
        path,
        {
            "printerName": printer_name,
            "widthMm": width_mm,
            "heightMm": height_mm,
            "orientation": orientation,
            "savedAt": utc_timestamp(),
        },
    )


def request_json(config: dict[str, Any], url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + config["token"],
            "Content-Type": "application/json",
            "User-Agent": "container-batch/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(config["api"].get("timeoutSeconds", 60))) as response:
        content = response.read().decode("utf-8")
    result = json.loads(content)
    if result.get("code") not in (None, 0):
        raise RuntimeError(f"API error {result.get('code')}: {result.get('msg') or result}")
    return result


def post_json_with_retry(config: dict[str, Any], url: str, payload: dict[str, Any]) -> dict[str, Any]:
    attempts = max(1, int(config["api"].get("retryAttempts", 3)))
    delay_seconds = float(config["api"].get("retryDelaySeconds", 5))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request_json(config, url, payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {body}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt < attempts:
            time.sleep(delay_seconds)
    raise RuntimeError(str(last_error or "Request failed."))


def parse_data_id(result: dict[str, Any]) -> str:
    for path in (
        ("data", "_id"),
        ("data", "data_id"),
        ("data", "id"),
        ("data_id",),
        ("id",),
    ):
        node: Any = result
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok:
            value = normalize_text(node)
            if value:
                return value
    raise RuntimeError(f"Create response does not contain a data id: {result}")


def record_value(record: dict[str, Any], field_id: str) -> str:
    value = record.get(field_id)
    if isinstance(value, dict):
        if "name" in value:
            return normalize_text(value.get("name"))
        if "username" in value:
            return normalize_text(value.get("username"))
    return normalize_text(value)


def fetch_record_by_id(config: dict[str, Any], data_id: str) -> dict[str, Any]:
    payload = {
        "app_id": config["app_id"],
        "entry_id": config["entry_id"],
        "data_id": data_id,
    }
    result = post_json_with_retry(config, config["get_url"], payload)
    data = result.get("data")
    if isinstance(data, dict):
        return data
    return {"_id": data_id}


def wait_for_record(config: dict[str, Any], data_id: str) -> dict[str, Any]:
    required_fields = list(config["api"].get("waitForAutoFillFields") or [])
    attempts = max(1, int(config["api"].get("autoFillPollAttempts", 5)))
    delay_seconds = float(config["api"].get("autoFillPollDelaySeconds", 1.0))
    last_record = {"_id": data_id}
    for _attempt in range(attempts):
        last_record = fetch_record_by_id(config, data_id)
        if not required_fields:
            return last_record
        if all(record_value(last_record, config["fields"].get(field_name, field_name)) for field_name in required_fields):
            return last_record
        time.sleep(delay_seconds)
    return last_record


def build_code(selection: dict[str, str]) -> str:
    type_prefix = normalize_text(selection["type"]).split(".", 1)[0][:1].upper() or "X"
    sku_prefix = normalize_text(selection["skuQuantity"]).split(".", 1)[0][:1].upper() or "X"
    area = normalize_text(selection["area"]) or "0"
    random_part = uuid.uuid4().hex[:6].upper()
    return f"BC-{type_prefix}{sku_prefix}{area}-{random_part}"


def build_user_value(config: dict[str, Any]) -> dict[str, str]:
    return {"username": normalize_text(config["api"]["creatorUsername"])}


def field_input(value: Any) -> dict[str, Any]:
    return {"value": value}


def build_record_payload(config: dict[str, Any], selection: dict[str, str]) -> dict[str, Any]:
    fields = config["fields"]
    defaults = config["defaults"]
    code = build_code(selection)
    submit_time = utc_timestamp()
    return {
        fields["createDate"]: field_input(submit_time),
        fields["serviceSite"]: field_input(defaults["serviceSite"]),
        fields["serviceSiteId"]: field_input(defaults["serviceSiteId"]),
        fields["code"]: field_input(code),
        fields["type"]: field_input(selection["type"]),
        fields["skuQuantity"]: field_input(selection["skuQuantity"]),
        fields["area"]: field_input(selection["area"]),
        fields["size"]: field_input(selection["size"]),
        fields["remark"]: field_input(defaults.get("remark", "")),
        fields["operationTime"]: field_input(submit_time),
        fields["operator"]: field_input(normalize_text(config["api"]["creatorUsername"])),
        fields["createBy"]: field_input(normalize_text(config["api"]["creatorUsername"])),
        fields["creatorDept"]: field_input(list(defaults.get("creatorDepartments", []))),
        fields["disabled"]: field_input(list(defaults.get("disabledValues", []))),
        fields["defaultSubformSize"]: field_input(int(defaults.get("defaultSubformSize", 1))),
        fields["legacyType"]: field_input(defaults.get("legacyType", "Basket(篮筐)")),
    }


def create_records(config: dict[str, Any], selection: dict[str, str], count: int) -> dict[str, Any]:
    success_ids: list[str] = []
    records: list[dict[str, Any]] = []
    created_items: list[dict[str, Any]] = []
    for _index in range(count):
        payload = {
            "app_id": config["app_id"],
            "entry_id": config["entry_id"],
            "data_creator": normalize_text(config["api"].get("creatorUsername")),
            "data": build_record_payload(config, selection),
            "is_start_workflow": bool(config["api"].get("isStartWorkflow", False)),
            "is_start_trigger": bool(config["api"].get("isStartTrigger", True)),
        }
        result = post_json_with_retry(config, config["create_url"], payload)
        data_id = parse_data_id(result)
        success_ids.append(data_id)
        created_items.append(result)
        records.append(wait_for_record(config, data_id))
    return {
        "mode": normalize_text(config["api"].get("createMode")) or "single_create_with_trigger",
        "success_ids": success_ids,
        "created_items": created_items,
        "records": records,
    }


def fetch_distinct_options(config: dict[str, Any]) -> dict[str, list[str]]:
    payload = {
        "app_id": config["app_id"],
        "entry_id": config["entry_id"],
        "limit": int(config["ui"].get("optionFetchMaxRecords", 300)),
        "fields": [config["fields"][field_name] for field_name in OPTION_FIELDS],
    }
    result = post_json_with_retry(config, config["list_url"], payload)
    data = result.get("data")
    distinct = {field_name: list(config["optionSeed"].get(field_name, [])) for field_name in OPTION_FIELDS}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for field_name in OPTION_FIELDS:
                text = record_value(item, config["fields"][field_name])
                if text:
                    distinct[field_name].append(text)
    return {field_name: unique_strings(values) for field_name, values in distinct.items()}


def render_code128_barcode(value: str, width: int, height: int, quiet_modules: int = 12) -> Image.Image:
    encoded = normalize_text(value).upper()
    unsupported = sorted({char for char in encoded if not 32 <= ord(char) <= 127})
    if unsupported:
        raise ValueError(f"Code 128-B does not support characters: {''.join(unsupported)}")
    code_values = [CARD_CODE128_START_B]
    checksum = CARD_CODE128_START_B
    for index, char in enumerate(encoded, start=1):
        code_value = ord(char) - 32
        code_values.append(code_value)
        checksum += index * code_value
    code_values.append(checksum % 103)
    pattern = "".join(CARD_CODE128_PATTERNS[item] for item in code_values) + CARD_CODE128_STOP + "11"
    raw_width = len(pattern) + quiet_modules * 2
    image = Image.new("RGB", (raw_width, height), "white")
    draw = ImageDraw.Draw(image)
    x = quiet_modules
    start_x: int | None = None
    for bit in pattern:
        if bit == "1":
            if start_x is None:
                start_x = x
        elif start_x is not None:
            draw.rectangle((start_x, 0, x - 1, height), fill="black")
            start_x = None
        x += 1
    if start_x is not None:
        draw.rectangle((start_x, 0, x - 1, height), fill="black")
    return image.resize((width, height), Image.Resampling.NEAREST)


def scale_rect(rect: tuple[float, float, float, float], width_px: int, height_px: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    return (
        int(round(x0 / TEMPLATE_BASE_WIDTH_PT * width_px)),
        int(round(y0 / TEMPLATE_BASE_HEIGHT_PT * height_px)),
        int(round(x1 / TEMPLATE_BASE_WIDTH_PT * width_px)),
        int(round(y1 / TEMPLATE_BASE_HEIGHT_PT * height_px)),
    )


def _resolve_template_source(config: dict[str, Any]) -> Path:
    reference_path = config["base_dir"] / "1.pdf"
    if reference_path.exists():
        return reference_path
    return ensure_template_pdf(config)


def _load_template_page_image(config: dict[str, Any], dpi: int) -> tuple[Image.Image, float, float, tuple[float, float, float, float]]:
    if fitz is None:
        raise RuntimeError("Missing dependency 'PyMuPDF'.")

    template_path = _resolve_template_source(config)
    document = fitz.open(str(template_path))
    try:
        if document.page_count < 1:
            raise RuntimeError(f"Template PDF has no pages: {template_path}")
        page = document[0]
        page_width_pt = float(page.rect.width)
        page_height_pt = float(page.rect.height)
        matrix = fitz.Matrix(max(1.0, dpi / 72.0), max(1.0, dpi / 72.0))
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")

        barcode_rect_pt = (108.58, 85.42, 476.86, 145.42)
        images = page.get_images(full=True)
        if images:
            rects = page.get_image_rects(images[0][0])
            if rects:
                rect = rects[0]
                barcode_rect_pt = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        return image, page_width_pt, page_height_pt, barcode_rect_pt
    finally:
        document.close()


def _scale_rect_for_page(rect: tuple[float, float, float, float], width_px: int, height_px: int, page_width_pt: float, page_height_pt: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    return (
        int(round(x0 / page_width_pt * width_px)),
        int(round(y0 / page_height_pt * height_px)),
        int(round(x1 / page_width_pt * width_px)),
        int(round(y1 / page_height_pt * height_px)),
    )


def build_template_image(config: dict[str, Any]) -> Image.Image:
    dpi = int(config["label"].get("dpi", 300))
    width_px = mm_to_px(float(config["label"]["widthMm"]), dpi)
    height_px = mm_to_px(float(config["label"]["heightMm"]), dpi)
    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    black = (0, 0, 0)

    title_rect = scale_rect((255, 31, 523, 78), width_px, height_px)
    barcode_rect = scale_rect((103, 73, 485, 146), width_px, height_px)
    separator_y = scale_rect((0, 245, 0, 245), width_px, height_px)[1]

    draw.rectangle(title_rect, outline=black, width=2)
    draw.line((scale_rect((55, 245, 55, 245), width_px, height_px)[0], separator_y, scale_rect((565, 245, 565, 245), width_px, height_px)[0], separator_y), fill=black, width=2)

    label_font = load_font(pt_to_px(17, dpi), family="simsun")
    title_font = load_font(pt_to_px(15, dpi), family="simsun")

    draw_text(draw, (title_rect[0] + 14, title_rect[1] + 10), "缓存容器  (BUFFER CONTAINER)", title_font, black, family="simsun")
    draw.rectangle(barcode_rect, outline=None, fill="white")

    static_items = [
        ((62, 168), "容器编码"),
        ((69, 193), "(Code) :"),
        ((83, 275), "类型"),
        ((79, 296), "(Type) :"),
        ((331, 275), "SKU 数"),
        ((321, 296), "(SKU Qty.) :"),
        ((83, 334), "区域"),
        ((79, 355), "(Area) :"),
        ((340, 334), "尺码"),
        ((332, 355), "(Size) :"),
    ]
    for (x_pt, y_pt), text in static_items:
        x_px, y_px, _, _ = scale_rect((x_pt, y_pt, x_pt, y_pt), width_px, height_px)
        draw_text(draw, (x_px, y_px), text, label_font, black, family="simsun")
    return image


def ensure_template_pdf(config: dict[str, Any]) -> Path:
    template_path = config["label_template_path"]
    if template_path.exists():
        return template_path
    if fitz is None:
        raise RuntimeError("Missing dependency 'PyMuPDF'.")
    template_image = build_template_image(config)
    buffer = BytesIO()
    template_image.save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=TEMPLATE_BASE_WIDTH_PT, height=TEMPLATE_BASE_HEIGHT_PT)
    page.insert_image(fitz.Rect(0, 0, TEMPLATE_BASE_WIDTH_PT, TEMPLATE_BASE_HEIGHT_PT), stream=buffer.getvalue(), keep_proportion=False)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(template_path))
    document.close()
    return template_path


def render_label_page(record: dict[str, Any], config: dict[str, Any]) -> tuple[Image.Image, float, float]:
    if fitz is None:
        raise RuntimeError("Missing dependency 'PyMuPDF'. Install it with: pip install -r requirements.txt")
    dpi = int(config["label"].get("dpi", 300))
    image, page_width_pt, page_height_pt, barcode_rect_pt = _load_template_page_image(config, dpi)
    width_px, height_px = image.size
    draw = ImageDraw.Draw(image)
    black = (0, 0, 0)

    code_rect = _scale_rect_for_page((166.35, 166.42, 478.35, 217.8), width_px, height_px, page_width_pt, page_height_pt)
    type_rect = _scale_rect_for_page((171.97, 281.52, 285.0, 304.5), width_px, height_px, page_width_pt, page_height_pt)
    sku_rect = _scale_rect_for_page((425.1, 281.52, 532.0, 304.5), width_px, height_px, page_width_pt, page_height_pt)
    area_rect = _scale_rect_for_page((188.0, 334.8, 255.0, 388.0), width_px, height_px, page_width_pt, page_height_pt)
    size_rect = _scale_rect_for_page((428.0, 334.8, 520.0, 388.0), width_px, height_px, page_width_pt, page_height_pt)
    barcode_rect = _scale_rect_for_page(barcode_rect_pt, width_px, height_px, page_width_pt, page_height_pt)

    code_value = record_value(record, config["fields"]["code"]) or record_value(record, config["fields"]["trackingNumber"]) or normalize_text(record.get("_id"))
    type_value = record_value(record, config["fields"]["type"])
    sku_value = record_value(record, config["fields"]["skuQuantity"])
    area_value = record_value(record, config["fields"]["area"])
    size_value = record_value(record, config["fields"]["size"])

    cover_rects = [barcode_rect, code_rect, type_rect, sku_rect, area_rect, size_rect]
    cover_margin = max(4, dpi // 150)
    for rect in cover_rects:
        draw.rectangle(
            (
                max(0, rect[0] - cover_margin),
                max(0, rect[1] - cover_margin),
                min(width_px, rect[2] + cover_margin),
                min(height_px, rect[3] + cover_margin),
            ),
            fill="white",
        )

    barcode_image = render_code128_barcode(
        code_value,
        barcode_rect[2] - barcode_rect[0],
        barcode_rect[3] - barcode_rect[1],
        quiet_modules=0,
    )
    image.paste(barcode_image, (barcode_rect[0], barcode_rect[1]))

    code_font = load_font(pt_to_px(48, dpi), family="simsun")
    small_font = load_font(pt_to_px(18, dpi), family="simsun")
    large_font = load_font(pt_to_px(48, dpi), family="simsun")

    def draw_centered(rect: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, family: str = "simsun", bold: bool = True) -> None:
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1 if bold else 0)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = rect[0] + max(0, ((rect[2] - rect[0]) - text_width) // 2) - bbox[0]
        y = rect[1] + max(0, ((rect[3] - rect[1]) - text_height) // 2) - bbox[1]
        draw_text(draw, (x, y), text, font, black, family=family, bold=bold)

    def draw_left(rect: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, family: str = "simsun", bold: bool = True) -> None:
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1 if bold else 0)
        y = rect[1] + max(0, ((rect[3] - rect[1]) - (bbox[3] - bbox[1])) // 2) - bbox[1]
        draw_text(draw, (rect[0], y), text, font, black, family=family, bold=bold)

    draw_left(code_rect, code_value, code_font)
    draw_left(type_rect, type_value, small_font)
    draw_left(sku_rect, sku_value, small_font)
    draw_centered(area_rect, area_value, large_font)
    draw_centered(size_rect, size_value, large_font)
    return image, page_width_pt, page_height_pt


def render_label_pdf(record: dict[str, Any], config: dict[str, Any], output_path: Path) -> Path:
    image, page_width_pt, page_height_pt = render_label_page(record, config)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=page_width_pt, height=page_height_pt)
    page.insert_image(fitz.Rect(0, 0, page_width_pt, page_height_pt), stream=buffer.getvalue(), keep_proportion=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    document.close()
    return output_path


def render_batch_labels_pdf(records: list[dict[str, Any]], config: dict[str, Any], output_path: Path) -> Path:
    if fitz is None:
        raise RuntimeError("Missing dependency 'PyMuPDF'. Install it with: pip install -r requirements.txt")
    if not records:
        raise RuntimeError("No records were provided for label generation.")

    document = fitz.open()
    try:
        for record in records:
            image, page_width_pt, page_height_pt = render_label_page(record, config)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            page = document.new_page(width=page_width_pt, height=page_height_pt)
            page.insert_image(fitz.Rect(0, 0, page_width_pt, page_height_pt), stream=buffer.getvalue(), keep_proportion=False)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(output_path))
        return output_path
    finally:
        document.close()


def create_batch_output_dir(output_dir: Path) -> Path:
    path = output_dir / ("batch_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    path.mkdir(parents=True, exist_ok=True)
    (path / "labels").mkdir(parents=True, exist_ok=True)
    return path


def build_pdf_label_filename(index: int, record: dict[str, Any], config: dict[str, Any]) -> str:
    primary = record_value(record, config["fields"]["code"]) or record_value(record, config["fields"]["trackingNumber"]) or normalize_text(record.get("_id"))
    secondary = record_value(record, config["fields"]["size"]) or f"label_{index:03d}"
    return f"{index:03d}_{safe_filename_stem(primary)}_{safe_filename_stem(secondary)}.pdf"


def build_batch_pdf_filename(records: list[dict[str, Any]], config: dict[str, Any]) -> str:
    count = len(records)
    first_code = ""
    last_code = ""
    if records:
        first_code = record_value(records[0], config["fields"]["code"]) or record_value(records[0], config["fields"]["trackingNumber"]) or normalize_text(records[0].get("_id"))
        last_code = record_value(records[-1], config["fields"]["code"]) or record_value(records[-1], config["fields"]["trackingNumber"]) or normalize_text(records[-1].get("_id"))

    if count == 1:
        stem = safe_filename_stem(first_code or "label")
        return f"{stem}.pdf"

    first_stem = safe_filename_stem(first_code or "first")
    last_stem = safe_filename_stem(last_code or "last")
    return f"labels_{count:03d}_{first_stem}_to_{last_stem}.pdf"


def write_result_json(batch_dir: Path, payload: dict[str, Any]) -> Path:
    path = batch_dir / "result.json"
    json_dump(path, payload)
    return path


class ContainerBatchApp:
    def __init__(self, root: tk.Tk, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config
        self.cache = load_cache(config["cache_path"], config)
        self.option_values = {field_name: unique_strings(list(config["optionSeed"].get(field_name, [])) + list(self.cache["options"].get(field_name) or [])) for field_name in OPTION_FIELDS}
        self.is_busy = False
        self.printers: list[dict[str, Any]] = []

        self.status_var = tk.StringVar(value="Ready.")
        self.batch_count_var = tk.StringVar(value=str(self.cache["lastSelection"].get("batchCount", config["ui"]["batchCountDefault"])))
        self.auto_print_var = tk.BooleanVar(value=bool(self.cache["lastSelection"].get("autoPrint", True)))
        self.printer_var = tk.StringVar(value="")
        self.field_vars = {
            field_name: tk.StringVar(value=canonical_option_value(self.cache["lastSelection"].get(field_name, ""), list(config["optionSeed"].get(field_name, []))))
            for field_name in OPTION_FIELDS
        }
        self.field_combos: dict[str, ttk.Combobox] = {}

        self.root.title("Container Batch Creator")
        self.root.geometry("1100x760")
        self.root.minsize(980, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self.refresh_combobox_values()
        self.refresh_printers()
        ensure_template_pdf(self.config)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        field_frame = ttk.LabelFrame(outer, text="Container Fields / 容器字段", padding=12)
        field_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        field_frame.columnconfigure(1, weight=1)
        field_frame.columnconfigure(3, weight=1)

        rows = [("type", 0, 0), ("skuQuantity", 0, 2), ("area", 1, 0), ("size", 1, 2)]
        for field_name, row, col in rows:
            ttk.Label(field_frame, text=OPTION_LABELS[field_name]).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=6)
            combo = ttk.Combobox(field_frame, textvariable=self.field_vars[field_name], state="normal")
            combo.grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=6)
            self.field_combos[field_name] = combo

        batch_frame = ttk.LabelFrame(outer, text="Batch / 批量", padding=12)
        batch_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        batch_frame.columnconfigure(1, weight=1)
        ttk.Label(batch_frame, text="Create Count / 批量创建数量").grid(row=0, column=0, sticky="w")
        self.batch_spinbox = ttk.Spinbox(
            batch_frame,
            from_=1,
            to=int(self.config["ui"]["maxCreateCount"]),
            textvariable=self.batch_count_var,
            width=12,
        )
        self.batch_spinbox.grid(row=0, column=1, sticky="w", padx=(8, 0))

        printer_frame = ttk.LabelFrame(outer, text="Printer / 打印机", padding=12)
        printer_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        printer_frame.columnconfigure(1, weight=1)
        ttk.Label(printer_frame, text="Printer").grid(row=0, column=0, sticky="w")
        self.printer_combo = ttk.Combobox(printer_frame, textvariable=self.printer_var, state="readonly")
        self.printer_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(printer_frame, text="Refresh / 刷新", command=self.refresh_printers).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(printer_frame, text="Save / 保存", command=self.save_selected_printer).grid(row=0, column=3, padx=(0, 8))
        ttk.Checkbutton(printer_frame, text="Print after create / 创建后打印", variable=self.auto_print_var).grid(row=0, column=4, sticky="w")

        action_frame = ttk.Frame(outer)
        action_frame.grid(row=3, column=0, sticky="nsew")
        action_frame.columnconfigure(0, weight=1)
        action_frame.rowconfigure(1, weight=1)

        buttons = ttk.Frame(action_frame)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(buttons, text="Refresh Options / 刷新选项", command=self.refresh_options_async).pack(side=tk.LEFT)
        self.create_button = ttk.Button(buttons, text="Create Batch / 批量创建", command=self.create_batch_async)
        self.create_button.pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(action_frame, text="Run Log / 运行日志", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=20, state=tk.DISABLED)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        ttk.Label(outer, textvariable=self.status_var, foreground="#555555", wraplength=980).grid(row=4, column=0, sticky="ew", pady=(10, 0))

        self.append_log("Loaded config from: " + str(self.config["config_path"]))
        self.append_log("Output directory: " + str(self.config["output_dir"]))
        self.append_log("Current token: " + mask(self.config["token"]))
        self.append_log("Create mode: " + (normalize_text(self.config["api"].get("createMode")) or "single_create_with_trigger"))

    def append_log(self, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        self.append_log(message)

    def refresh_combobox_values(self) -> None:
        for field_name, combo in self.field_combos.items():
            current = canonical_option_value(self.field_vars[field_name].get(), list(self.config["optionSeed"].get(field_name, [])))
            if current:
                self.field_vars[field_name].set(current)
            combo.configure(values=unique_strings(self.option_values.get(field_name, []) + ([current] if current else [])))

    def save_cache_state(self) -> None:
        for field_name in OPTION_FIELDS:
            canonical = canonical_option_value(self.field_vars[field_name].get(), list(self.config["optionSeed"].get(field_name, [])))
            self.field_vars[field_name].set(canonical)
            self.cache["lastSelection"][field_name] = canonical
            self.cache["options"][field_name] = unique_strings(self.option_values.get(field_name, []) + ([canonical] if canonical else []))
        try:
            self.cache["lastSelection"]["batchCount"] = int(self.batch_count_var.get().strip())
        except (TypeError, ValueError):
            self.cache["lastSelection"]["batchCount"] = int(self.config["ui"]["batchCountDefault"])
        self.cache["lastSelection"]["autoPrint"] = bool(self.auto_print_var.get())
        save_cache(self.config["cache_path"], self.cache)

    def refresh_printers(self) -> None:
        self.printers = read_system_printers()
        printer_names = [item["name"] for item in self.printers]
        saved = load_print_settings(self.config["print_settings_path"])
        selected = match_printer(self.printers, normalize_text(saved.get("printerName")))
        self.printer_combo.configure(values=printer_names)
        if selected:
            self.printer_var.set(selected)
        elif printer_names:
            self.printer_var.set(printer_names[0])
        else:
            self.printer_var.set("")
        self.append_log(f"Loaded {len(printer_names)} local printer(s)." if printer_names else "No local printer was detected.")

    def save_selected_printer(self) -> None:
        printer_name = self.printer_var.get().strip()
        if not printer_name:
            messagebox.showwarning("Container Batch Creator", "Please select a printer first.")
            return
        try:
            save_print_settings(
                self.config["print_settings_path"],
                printer_name,
                float(self.config["label"]["widthMm"]),
                float(self.config["label"]["heightMm"]),
                normalize_text(self.config["label"]["orientation"]),
            )
        except OSError as exc:
            messagebox.showerror("Container Batch Creator", f"Failed to save printer settings:\n{exc}")
            return
        self.set_status(f"Saved printer setting: {printer_name}")

    def set_busy(self, is_busy: bool) -> None:
        self.is_busy = is_busy
        state = tk.DISABLED if is_busy else tk.NORMAL
        combo_state = "disabled" if is_busy else "normal"
        self.batch_spinbox.configure(state=state)
        self.create_button.configure(state=state)
        self.printer_combo.configure(state="disabled" if is_busy else "readonly")
        for combo in self.field_combos.values():
            combo.configure(state=combo_state)

    def read_selection(self, require_printer: bool) -> tuple[dict[str, str], int]:
        selection = {
            field_name: canonical_option_value(self.field_vars[field_name].get(), list(self.config["optionSeed"].get(field_name, [])))
            for field_name in OPTION_FIELDS
        }
        for field_name in OPTION_FIELDS:
            if not selection[field_name]:
                raise ValueError(f"{OPTION_LABELS[field_name]} is required.")
        try:
            count = int(self.batch_count_var.get().strip())
        except ValueError as exc:
            raise ValueError("Batch Count must be an integer.") from exc
        if count < 1 or count > int(self.config["ui"]["maxCreateCount"]):
            raise ValueError(f"Batch Count must be between 1 and {self.config['ui']['maxCreateCount']}.")
        if require_printer and self.auto_print_var.get() and not self.printer_var.get().strip():
            raise ValueError("Please select a printer or disable printing.")
        return selection, count

    def refresh_options_async(self) -> None:
        if self.is_busy:
            return
        thread = threading.Thread(target=self._refresh_options_worker, daemon=True)
        thread.start()

    def _refresh_options_worker(self) -> None:
        self.root.after(0, self.set_status, "Fetching option values from Jiandaoyun...")
        try:
            fetched = fetch_distinct_options(self.config)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self.set_status, f"Failed to refresh options: {exc}")
            return

        def apply_fetched() -> None:
            for field_name in OPTION_FIELDS:
                self.option_values[field_name] = unique_strings(list(self.config["optionSeed"].get(field_name, [])) + list(self.option_values.get(field_name, [])) + list(fetched.get(field_name, [])))
            self.refresh_combobox_values()
            self.save_cache_state()
            counts = ", ".join(f"{field_name}={len(self.option_values[field_name])}" for field_name in OPTION_FIELDS)
            self.set_status(f"Option refresh completed: {counts}")

        self.root.after(0, apply_fetched)

    def create_batch_async(self) -> None:
        if self.is_busy:
            return
        try:
            selection, count = self.read_selection(require_printer=True)
        except ValueError as exc:
            messagebox.showwarning("Container Batch Creator", str(exc))
            return
        for field_name in OPTION_FIELDS:
            selection[field_name] = canonical_option_value(selection[field_name], list(self.config["optionSeed"].get(field_name, [])))
            self.field_vars[field_name].set(selection[field_name])
            self.option_values[field_name] = unique_strings(self.option_values.get(field_name, []) + [selection[field_name]])
        self.refresh_combobox_values()
        self.save_cache_state()
        self.set_busy(True)
        thread = threading.Thread(
            target=self._create_batch_worker,
            args=(selection, count, self.printer_var.get().strip(), bool(self.auto_print_var.get())),
            daemon=True,
        )
        thread.start()

    def _create_batch_worker(self, selection: dict[str, str], count: int, printer_name: str, auto_print: bool) -> None:
        try:
            self.root.after(0, self.set_status, f"Creating {count} container record(s)...")
            create_result = create_records(self.config, selection, count)
            created_records = list(create_result["records"])
            batch_dir = create_batch_output_dir(self.config["output_dir"])
            labels_dir = batch_dir / "labels"
            batch_label_path = render_batch_labels_pdf(
                created_records,
                self.config,
                labels_dir / build_batch_pdf_filename(created_records, self.config),
            )
            label_paths: list[Path] = [batch_label_path]

            result_path = write_result_json(
                batch_dir,
                {
                    "selection": selection,
                    "requested_count": count,
                    "create_result": create_result,
                    "records": created_records,
                    "labels": [str(path) for path in label_paths],
                    "created_at": utc_timestamp(),
                },
            )

            print_errors: list[str] = []
            if auto_print:
                save_print_settings(
                    self.config["print_settings_path"],
                    printer_name,
                    float(self.config["label"]["widthMm"]),
                    float(self.config["label"]["heightMm"]),
                    normalize_text(self.config["label"]["orientation"]),
                )
                for index, label_path in enumerate(label_paths, start=1):
                    self.root.after(0, self.append_log, f"Printing {index}/{len(label_paths)}: {label_path.name}")
                    try:
                        print_pdf(
                            label_path,
                            printer_name,
                            float(self.config["label"]["widthMm"]),
                            float(self.config["label"]["heightMm"]),
                            normalize_text(self.config["label"]["orientation"]),
                            int(self.config["label"]["dpi"]),
                        )
                    except Exception as exc:  # noqa: BLE001
                        print_errors.append(f"{label_path.name}: {exc}")

            summary = {
                "records": created_records,
                "label_paths": label_paths,
                "result_path": result_path,
                "print_errors": print_errors,
            }
            self.root.after(0, self._finish_success, summary)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._finish_failure, str(exc))

    def _finish_success(self, summary: dict[str, Any]) -> None:
        self.set_busy(False)
        records = list(summary["records"])
        label_paths = list(summary["label_paths"])
        result_path = Path(summary["result_path"])
        print_errors = list(summary["print_errors"])
        self.append_log(f"Saved result JSON: {result_path}")
        self.append_log(f"Generated {len(label_paths)} label file(s).")
        for path in label_paths[:10]:
            self.append_log(f"Label: {path}")
        if len(label_paths) > 10:
            self.append_log(f"...and {len(label_paths) - 10} more labels.")
        self.append_log(f"Total label pages: {len(records)}")
        if print_errors:
            self.set_status(f"Create completed with {len(print_errors)} print error(s).")
            self.append_log("\n".join(print_errors[:8]))
            return
        self.set_status(f"Create completed successfully. Records={len(records)}, labels={len(label_paths)}.")

    def _finish_failure(self, message: str) -> None:
        self.set_busy(False)
        self.set_status(f"Create failed: {message}")

    def on_close(self) -> None:
        if self.is_busy:
            messagebox.showwarning("Container Batch Creator", "A create/print task is still running. Please wait for it to finish.")
            return
        self.save_cache_state()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Jiandaoyun container records in batches and print labels.")
    parser.add_argument("--config", default="config.json", help="Path to config.json.")
    parser.add_argument("--check-config", action="store_true", help="Validate resolved config values only.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (Path(__file__).resolve().parent / config_path).resolve()
    config = load_runtime_config(config_path)

    if args.check_config:
        ensure_template_pdf(config)
        payload = {
            "configPath": str(config["config_path"]),
            "cachePath": str(config["cache_path"]),
            "outputDir": str(config["output_dir"]),
            "templatePdf": str(config["label_template_path"]),
            "appId": config["app_id"],
            "entryId": config["entry_id"],
            "token": mask(config["token"]),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    cleanup_generated_pdfs(config)
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = ContainerBatchApp(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
