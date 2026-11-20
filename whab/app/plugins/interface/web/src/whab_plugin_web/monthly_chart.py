"""Geometry and stable series colors for the typed monthly chart presentation."""

from hashlib import sha256

from whab.plugins import currency_minor_units


def series_color(identifier):
    """Keep each series hue stable across view changes, reloads and themes."""
    digest = sha256(identifier.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") * 360 / 65536
    saturation = 54 + digest[2] % 18
    lightness = 43 + digest[3] % 16
    return f"hsl({hue:.2f} {saturation}% {lightness}%)"


def _tick_step(span, digits):
    major = 10**digits
    target = max(1, (span + 4 * major - 1) // (4 * major))
    scale = 10 ** (len(str(target)) - 1)
    size = (1, 2, 5, 10)[sum(target > step * scale for step in (1, 2, 5))]
    return major * scale * size


def stacked_monthly_plot(months, vectors, currency):
    """Map signed minor-unit series to separate positive and negative stacks."""
    values = [[] for _ in months]
    for vector in vectors:
        for column, amount in zip(values, vector, strict=True):
            column.append(amount)

    positive = [sum(max(0, amount) for amount in column) for column in values]
    negative = [sum(min(0, amount) for amount in column) for column in values]
    high, low = max(positive), min(negative)
    step = _tick_step(high - low, currency_minor_units(currency))
    top = max(step, (high + step - 1) // step * step)
    bottom = low // step * step
    width = max(350, 150 + 65 * len(months))
    left, right = 118, width - 15
    stride = (right - left) / len(months)
    bar_width = min(56, stride * 0.62)

    def y(amount):
        return round(18 + 228 * (top - amount) / (top - bottom), 2)

    columns = []
    for index, (month, amounts) in enumerate(zip(months, values, strict=True)):
        center = round(left + stride * (index + 0.5), 2)
        segments = []
        above, below = 0, 0
        for series, amount in enumerate(amounts):
            if not amount:
                continue
            if amount > 0:
                start, end = above, above + amount
                above = end
            else:
                start, end = below + amount, below
                below = start
            segments.append({
                "series": series, "amount": amount, "y": y(end),
                "height": round(y(start) - y(end), 2),
            })
        net = above + below
        columns.append({
            "month": month, "x": center, "bar_x": round(center - bar_width / 2, 2),
            "net": net, "net_y": y(net), "segments": segments,
        })
    return {
        "width": width, "left": left, "right": right, "bar_width": round(bar_width, 2),
        "ticks": [{"amount": amount, "y": y(amount)} for amount in range(bottom, top + step, step)],
        "columns": columns,
    }
