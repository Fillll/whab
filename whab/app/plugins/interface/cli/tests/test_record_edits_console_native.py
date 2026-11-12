"""Real console/HTTPS/worker edits and durable retries use isolated fictional data."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest
from whab_plugin_sqlite import Store

from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize('storage', ['sqlite','json'])
def test_real_commands_preserve_identity_notes_attribution_and_restart_replay(tmp_path, storage):
    home = tmp_path / 'runtime'
    env = {**os.environ,'HOME':str(tmp_path),'WHAB_HOME':str(home),'WHAB_UI':'','WHAB_PLUGINS':'',
           'WHAB_STORAGE':storage,'ALLOW_LIVE_PLAID':'false'}
    env.pop('WHAB_BUDGET',None)
    executable = Path(sys.executable).parent / 'whab'
    month = date.today().strftime('%Y-%m')
    with patch.dict(os.environ,env,clear=True):
        initialize_home(home)
    seed = Store(':memory:',month=month)
    try:
        account = seed.add_account('personal',{'name':'Fictional checking','type':'depository','balance':1000000})
        category = seed.add_category('personal',{'name':'Rent','group':'Fictional House','target':4321})
        transaction = seed.add_transaction('personal',{'accountId':account,'date':month+'-01',
            'merchant':'Fictional merchant','amount':12345,'categoryId':category,'note':'  [YNAB: uncleared]\nOriginal memo  '})
        seed.edit_transaction('personal',transaction,reviewed=True)
        seed.edit_transaction_details('personal',transaction,{'categoryId':category,'categoryAgent':'Fictional categorizer'})
        seed.assign('personal',category,month,10000)
        snapshot = seed.export_budget('personal')
    finally:
        seed.close()
    snapshot.pop('history',None)
    next(row for row in snapshot['transactions'] if row['id']==transaction)['merchant'] = '  Fictional merchant  '
    native = open_storage(load_plugins((storage,)).storage_plugin(storage),StorageContext(home,'sandbox',{}))
    try:
        native.restore_budget('personal',snapshot)
    finally:
        native.close()

    def cli(*args, ok=True):
        result = subprocess.run([str(executable),*args],env=env,capture_output=True,text=True,timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def run(*args, ok=True):
        return cli('--environment','sandbox',*args,ok=ok)

    def query(name):
        return run('api','--budget','default','query',name)

    for parts in [('category','rename'),('trnx','note')]:
        result = subprocess.run([str(executable),'--profile','/missing/fictional','My budget',*parts,'--help'],
                                env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode == 0 and 'No automatic retries' in result.stdout
        assert '--agent' in result.stdout and '--batch' in result.stdout

    with patch.dict(os.environ,env,clear=True), local_connection(home,'sandbox'):
        before = query('portable')['data']
        renamed = cli('My budget','category','rename','Fictional House / Rent','--name','Main Residence',
                      '--agent','Codex','--batch','fictional-cleanup','--home',str(home),'--environment','sandbox')
        assert renamed['data']['id']==category and renamed['data']['name']=='Main Residence'
        assert renamed['data']['target']==4321
        renamed_snapshot = query('portable')['data']
        expected = {**before,'categories':[({**row,'name':'Main Residence'} if row['id']==category else row) for row in before['categories']]}
        expected['history'] = renamed_snapshot['history']
        assert renamed_snapshot == expected
        name_retry = run('default','category','rename',category,'--name','Main Residence','--agent','Codex',
            '--batch','fictional-cleanup','--request-id',renamed['requestId'],'--revision',renamed['expectedRevision'])
        assert name_retry['replayed'] is True
        run('default','category','create','Reserved','--parent','Fictional House')
        failure = run('default','category','rename',category,'--name','reserved',ok=False)
        assert 'already exists' in failure['error']
        run('default','category','group','create','Fictional Travel')
        run('default','category','create','Main Residence','--parent','Fictional Travel')
        assert 'ambiguous' in run('default','category','rename','Main Residence','--name','New',ok=False)['error']
        first = next(row for row in query('transactions')['data'] if row['id']==transaction)
        appended = run('My budget','trnx','note',transaction,'--append',"Alex's matress",'--agent','Codex','--batch','fictional-cleanup')
        assert first['categoryAgent'] == 'Fictional categorizer'
        expected_note = first['note'] + "\nAlex's matress"
        assert appended['data'] == {**first,'note':expected_note}
        assert appended['data']['merchant'] == '  Fictional merchant  '
        replay_args = ('default','trnx','note',transaction,'--append',"Alex's matress",'--agent','Codex',
                       '--batch','fictional-cleanup','--request-id',appended['requestId'],'--revision',appended['expectedRevision'])
        assert run(*replay_args)['replayed'] is True
        assert 'refused' in run('default','trnx','note',transaction,'--append','Stale','--revision',appended['expectedRevision'],ok=False)['error']
        assert next(row for row in query('transactions')['data'] if row['id']==transaction)['note']==expected_note
        entries = run('default','history','list')['data']['entries']
        entry = next(row for row in entries if row['requestId']==appended['requestId'])
        assert entry['operation']=='append_transaction_note' and entry['actor']=='Codex' and entry['batchId']=='fictional-cleanup'
        assert entry['reversible'] is True
        # A manual memo edit keeps category attribution and all financial fields.
        other = run('budget','create','Fictional isolated')['id']
        pristine = run('api','--budget',other,'query','portable')['data']
        run(other,'trnx','note',transaction,'--append','Wrong budget',ok=False)
        assert run('api','--budget',other,'query','portable')['data']==pristine

    endpoint = home/'run/sandbox/host.json'
    deadline = monotonic()+25
    while endpoint.exists() and monotonic()<deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ,env,clear=True), local_connection(home,'sandbox'):
        retried = run(*replay_args)
        assert retried['replayed'] is True and retried['data']['note']==expected_note
        assert retried['data']['merchant']=='  Fictional merchant  '
        history = run('default','history','list')['data']['entries']
        assert sum(row['requestId']==appended['requestId'] for row in history)==1
        current = query('transactions')
        rejected = run('default','trnx','note',transaction,'--append','x'*1000,ok=False)
        assert '1000' in rejected['error']
        assert query('transactions')==current
