# Exact merchant teaching model

This deliberately small categorizer learns one exact-merchant lookup from reviewed, categorized, posted non-transfer rows. Lookup keys include currency. It stores one model in its budget's `model.json` namespace and supports listing, selecting, renaming and deleting that model. Delete the current model before saving another.

Predictions are suggestions for unreviewed uncategorized rows. A score of `1.0` means an exact teaching-rule match, with no claim of measured predictive confidence. The example never changes the ledger. Tests reopen the model through a fresh factory and check currency isolation and invalid saved state.

This independently packaged teaching plugin requires Python 3.14+ and WHAB 2.22+ within major 2. It advertises restart lifecycle support. WHAB 2.26+ hides it from ordinary Settings; enable `example-categorization` explicitly. See the [teaching guide](../README.md).

Build and test from a copy of this folder outside the WHAB checkout, in a clean environment containing the approved host and toolkit artifacts:

```sh
python -m pip wheel --no-deps . --wheel-dir /tmp/teaching-wheels
python -m pip install --no-index --find-links /approved/local/wheels '.[test]'
python -m pytest tests
whab-plugin check --plugin example-categorization --combinations 10 --seed 42 --wheelhouse /approved/local/wheels --output /tmp/example-categorization-check
```

The configuration check also needs the installed SQLite reference distribution and the exact wheels for all installed runtime dependencies in the approved wheelhouse. Project tests use public SDK contracts; no other concrete plugin is imported. Installed plugins are trusted executable Python. This example is a starting point, with bounded fictional behavior, rather than production provider certification.
