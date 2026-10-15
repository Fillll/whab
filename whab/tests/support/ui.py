"""A minimal SDK UI keeps host HTTP tests independent of bundled visuals."""

from whab.plugins import Plugin, UserInterface


def make_ui(home):
    templates, static = home / "fixture-ui-templates", home / "fixture-ui-static"
    templates.mkdir(exist_ok=True)
    static.mkdir(exist_ok=True)
    (templates / "base.html").write_text(
        "Fixture UI {{ page }} {{ month }} "
        "{% for result in plugin_insights %}"
        "{{ result.error or '' }}"
        "{% for insight in result.insights %}{{ insight.title }} {{ insight.value }}{% endfor %}"
        "{% endfor %}"
    )
    return Plugin("fixture-ui", "Fixture UI", ui=UserInterface(templates, static, lambda *a, **k: {}))
