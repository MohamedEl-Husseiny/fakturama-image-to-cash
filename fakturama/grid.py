"""Parse grounded OCR grid headers and verify each visible monetary cell."""

from decimal import Decimal
import re
from fakturama.fields import NumericFieldError, parse_ui_decimal


def letters(text):
    return re.sub("[^a-z]", "", text.casefold())


def grid_headers(tokens):
    def distinct(hits):
        result = []

        def same_box(a, b):
            if abs(a["x"] - b["x"]) < 8 and abs(a["y"] - b["y"]) < 8:
                return True
            overlap = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])) * max(
                0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            )
            return overlap >= 0.7 * min(a["w"] * a["h"], b["w"] * b["h"])

        for token in hits:
            if not any(same_box(token, t) for t in result):
                result.append(token)
        return result

    # Header glyphs Tesseract tends to misread on this grid.
    qty = distinct([t for t in tokens if letters(t["t"]) in ("qty", "oty")])
    if len(qty) != 1:
        raise ValueError("Grid Qty header not unique")
    y = qty[0]["y"]
    band = [t for t in tokens if abs(t["y"] - y) < 12]
    lookup = {}
    aliases = {
        "pos": {"pos"},
        "qty": {"qty", "oty"},
        "sku": {"item", "itern"},
        "picture": {"picture"},
        "name": {"name"},
        "description": {"description"},
        "vat": {"vat"},
        "discount": {"discount"},
    }
    for key, names in aliases.items():
        hits = distinct([t for t in band if letters(t["t"]) in names])
        if len(hits) != 1:
            raise ValueError(f"Grid header {key} not unique")
        lookup[key] = hits[0]
    prices = distinct([t for t in band if letters(t["t"]) in ("price", "uprice", "unitprice")])
    unit = [t for t in prices if lookup["vat"]["x"] < t["x"] < lookup["discount"]["x"]]
    total = [t for t in prices if t["x"] > lookup["discount"]["x"]]
    if len(unit) != 1 or len(total) != 1:
        raise ValueError("Unit/line Price headers not distinct")
    lookup.update(price=unit[0], total=total[0])
    sequence = (
        "pos",
        "qty",
        "sku",
        "picture",
        "name",
        "description",
        "vat",
        "price",
        "discount",
        "total",
    )
    starts = [lookup[k]["x"] for k in sequence]
    if starts != sorted(starts):
        raise ValueError("Unexpected grid column order")
    return lookup


def assert_line_cells(cells, item):
    def decimal(key):
        try:
            return parse_ui_decimal(cells[key])
        except NumericFieldError as exc:
            raise ValueError(f"Ambiguous {key} cell: {cells[key]!r}") from exc

    sku = re.sub(r"\s+", "", cells["sku"]).strip("|").upper()
    if sku != item.sku:
        raise ValueError(f"Wrong SKU cell {sku!r}")
    for key, expected in [("qty", item.qty), ("price", item.unit_net), ("total", item.line_net)]:
        if decimal(key) != expected:
            raise ValueError(f"{item.sku}: wrong {key}: {cells[key]!r}")
    # Line discounts render as -10.00 %.
    if abs(decimal("discount")) != item.discount_percent:
        raise ValueError(f"{item.sku}: wrong discount")
    rates = re.findall(r"(\d+(?:[.,]\d+)?)\s*%", cells["vat"])
    if not rates or any(Decimal(x.replace(",", ".")) != item.vat_percent for x in rates):
        raise ValueError(f'{item.sku}: wrong VAT cell {cells["vat"]!r}')


def clean_table_image(image):
    """Remove gray rules, retaining dark text and white selection/header text."""
    from collections import Counter
    from PIL import Image

    rgb = image.convert("RGB")
    result = Image.new("L", rgb.size, 255)
    pixels = rgb.load()
    out = result.load()
    for y in range(rgb.height):
        background = Counter(
            pixels[x, y] for x in range(rgb.width // 3, rgb.width * 2 // 3)
        ).most_common(1)[0][0]
        dark_background = min(background) < 230
        for x in range(rgb.width):
            color = pixels[x, y]
            ink = (
                sum(abs(a - b) for a, b in zip(color, background)) / 3 > 40
                if dark_background
                else sum(color) / 3 <= 140
            )
            if ink:
                out[x, y] = 0
    return result


def assert_product_added(before, after, sku):
    if sku in before or after != before + [sku]:
        raise ValueError(
            f"Product popup closed without exactly one expected row: {before} -> {after}; expected {sku}"
        )


def read_selected_cell(image):
    """Read white selection text after removing its blue background."""
    from PIL import Image, ImageOps
    import pytesseract

    clean = ImageOps.expand(clean_table_image(image), border=10, fill=255)
    clean = clean.resize((clean.width * 3, clean.height * 3), Image.Resampling.LANCZOS)
    return pytesseract.image_to_string(clean, config="--psm 7").strip()


def read_grid_tokens(image, left=0, top=0):
    """Combine normal and selected-row OCR without duplicating the same cell."""
    import pytesseract
    from PIL import Image, ImageOps

    clean = clean_table_image(image)
    tokens = []
    scale = 3
    for picture, mode in ((image, 6), (clean, 6), (clean, 11)):
        large = picture.resize(
            (picture.width * scale, picture.height * scale), Image.Resampling.LANCZOS
        )
        data = pytesseract.image_to_data(
            large, config=f"--psm {mode}", output_type=pytesseract.Output.DICT
        )
        for i, text in enumerate(data["text"]):
            if not text.strip() or float(data["conf"][i]) < 20:
                continue
            token = dict(
                t=text.strip(),
                x=left + data["left"][i] / scale,
                y=top + data["top"][i] / scale,
                w=data["width"][i] / scale,
                h=data["height"][i] / scale,
            )
            if any(
                t["t"].casefold() == token["t"].casefold()
                and abs(t["x"] - token["x"]) < 8
                and abs(t["y"] - token["y"]) < 8
                for t in tokens
            ):
                continue
            tokens.append(token)
    qty = [t for t in tokens if letters(t["t"]) in ("qty", "oty")]
    if qty:
        y1 = max(0, int(qty[0]["y"] - top) - 3)
        y2 = min(image.height, int(qty[0]["y"] + qty[0]["h"] - top) + 4)
        for source in (image, clean):
            crop = source.crop((0, y1, image.width, y2))
            crop = ImageOps.expand(
                crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS),
                border=12,
                fill=255 if source is clean else "white",
            )
            data = pytesseract.image_to_data(
                crop, config="--psm 7", output_type=pytesseract.Output.DICT
            )
            for i, text in enumerate(data["text"]):
                if (
                    letters(text)
                    not in {
                        "pos",
                        "qty",
                        "oty",
                        "item",
                        "itern",
                        "picture",
                        "name",
                        "description",
                        "vat",
                        "uprice",
                        "price",
                        "discount",
                    }
                    or float(data["conf"][i]) < 20
                ):
                    continue
                token = dict(
                    t=text,
                    x=left + (data["left"][i] - 12) / scale,
                    y=top + y1 + (data["top"][i] - 12) / scale,
                    w=data["width"][i] / scale,
                    h=data["height"][i] / scale,
                )
                if not any(
                    letters(t["t"]) == letters(text)
                    and abs(t["x"] - token["x"]) < 8
                    and abs(t["y"] - token["y"]) < 8
                    for t in tokens
                ):
                    tokens.append(token)
    if qty and not any(letters(t["t"]) == "pos" and abs(t["y"] - qty[0]["y"]) < 12 for t in tokens):
        # Read the first header cell on its own; a blue selected row makes the
        # full-table pass lose that label.
        y1 = max(0, int(qty[0]["y"] - top) - 3)
        y2 = min(image.height, int(qty[0]["y"] + qty[0]["h"] - top) + 4)
        right = int(min(t["x"] for t in qty) - left) - 8
        if right > 10 and y2 > y1:
            factor = 5
            border = 20
            crop = image.crop((0, y1, right, y2))
            crop = ImageOps.expand(
                crop.resize((right * factor, (y2 - y1) * factor), Image.Resampling.LANCZOS),
                border=border,
                fill="white",
            )
            data = pytesseract.image_to_data(
                crop, config="--psm 7", output_type=pytesseract.Output.DICT
            )
            for i, text in enumerate(data["text"]):
                if letters(text) == "pos" and float(data["conf"][i]) >= 60:
                    tokens.append(
                        dict(
                            t=text,
                            x=left + (data["left"][i] - border) / factor,
                            y=top + y1 + (data["top"][i] - border) / factor,
                            w=data["width"][i] / factor,
                            h=data["height"][i] / factor,
                        )
                    )
    return tokens
