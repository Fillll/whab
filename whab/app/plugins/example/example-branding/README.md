# Fictional branding

This offline branding example resolves only `Fictional Bank` and returns an embedded synthetic PNG. Unknown institutions and unknown image keys return no result. It performs no networking and creates no connection. The small source demonstrates `BankLogos` and `LogoImage`, with a public `PluginRegistry` regression test.

This independently packaged teaching plugin requires Python 3.14+ and WHAB 2.22+ within major 2. It advertises restart lifecycle support. WHAB 2.26+ hides it from ordinary Settings; enable `example-branding` explicitly. See the [teaching guide](../README.md).

Build and test from a copy of this folder outside the WHAB checkout, in a clean environment containing the approved host and toolkit artifacts:

```sh
python -m pip wheel --no-deps . --wheel-dir /tmp/teaching-wheels
python -m pip install --no-index --find-links /approved/local/wheels '.[test]'
python -m pytest tests
whab-plugin check --plugin example-branding --combinations 10 --seed 42 --wheelhouse /approved/local/wheels --output /tmp/example-branding-check
```

The configuration check also needs the installed SQLite reference distribution and the exact wheels for all installed runtime dependencies in the approved wheelhouse. Project tests use public SDK contracts; no other concrete plugin is imported. Installed plugins are trusted executable Python. This example is a starting point, with bounded fictional behavior, rather than production provider certification.
