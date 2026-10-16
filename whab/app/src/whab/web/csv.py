"""CSV text escaping shared by core exports and plugin exports."""


def safe_cell(value):
    text = str(value) if value is not None else ""
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
