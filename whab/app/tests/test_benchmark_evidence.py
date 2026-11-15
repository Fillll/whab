"""Performance reports must not accept incorrect durable retry evidence."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("bulk_benchmark_evidence", scripts / "benchmark_atomic_bulk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fault", [None, "receipt", "stale", "conflict", "revision", "save"])
def test_retry_proof_rejects_changed_receipts_or_extra_mutations(benchmark, fault):
    result = {"budgetId": "fictional", "revision": "saved", "data": {"count": 1},
              "intent": {"request_id": "original", "expected_revision": "earlier",
                         "history": {"actor": "Benchmark", "actorKind": "agent"}}}
    posted = []

    def response(value, status=200):
        return SimpleNamespace(status_code=status, json=lambda: value, raise_for_status=lambda: None)

    def get(*args, **kwargs):
        return response({"revision": "changed" if posted and fault == "revision" else "saved"})

    def post(path, *, json):
        posted.append(deepcopy(json))
        if len(posted) == 1:
            original = {key: deepcopy(result[key]) for key in ("budgetId", "revision", "data")}
            if fault == "receipt":
                original["data"]["count"] = 2
            return response(original)
        return response({}, 200 if fault == ("stale" if len(posted) == 2 else "conflict") else 409)

    def counts():
        return {"checkpoints": int(bool(posted) and fault == "save"), "generation": 1}

    connection = SimpleNamespace(get=get, post=post)
    if fault:
        with pytest.raises(AssertionError):
            benchmark.verify_retry(connection, "fictional", result, counts)
    else:
        proof = benchmark.verify_retry(connection, "fictional", result, counts)
        assert all(proof.values())
        assert posted[0] == result["intent"]
        assert posted[1]["request_id"] != "original"
        assert posted[2]["request_id"] == "original"


@pytest.mark.parametrize("fault", [None, "changed_bytes", "wrong_counts", "escaped_path", "unknown_format"])
def test_prepared_fixtures_are_immutable_and_bound_to_provenance(benchmark, tmp_path, monkeypatch, fault):
    import benchmark_cli_matrix as matrix

    def fixture(root, total, accounts):
        path = root / 'fixture.whab'
        path.write_bytes(b'fictional input bytes')
        return {'id': 'fictional', 'name': 'Fixture', 'transactions': total, 'path': str(path)}

    provenance = {'source_commit': 'original-main', 'versions': {'whab': 'fictional-version'}}
    monkeypatch.setattr(matrix, 'fixture', fixture)
    monkeypatch.setattr(matrix, 'source_identity', lambda: provenance)
    seeds, runtime = tmp_path / 'seeds', tmp_path / 'runtime'
    runtime.mkdir()
    index = matrix.write_fixtures(seeds, [1000], 100)
    record = json.loads(index.read_text())
    entry = record['fixtures']['1000:100']
    source = seeds / entry['path']
    if fault == 'changed_bytes':
        source.write_bytes(b'changed source')
    elif fault == 'wrong_counts':
        entry['accounts'] = 99
    elif fault == 'escaped_path':
        entry['path'] = '../outside.whab'
    elif fault == 'unknown_format':
        record['format'] = 'unknown'
    index.write_text(json.dumps(record))
    if fault:
        with pytest.raises(ValueError):
            matrix.prepared_fixture(runtime, 1000, 100, seeds)
    else:
        result = matrix.prepared_fixture(runtime, 1000, 100, seeds)
        assert result['fixture']['source_commit'] == 'original-main'
        assert result['fixture']['versions'] == provenance['versions']
        assert result['fixture']['sha256'] == entry['sha256']
        assert Path(result['path']).read_bytes() == source.read_bytes()
        Path(result['path']).write_bytes(b'an acknowledged edit in this run')
        assert source.read_bytes() == b'fictional input bytes'
        with pytest.raises(ValueError, match='never overwritten'):
            matrix.write_fixtures(seeds, [1000], 100)
