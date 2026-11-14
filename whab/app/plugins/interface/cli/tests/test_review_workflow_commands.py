"""Complete read-only legacy audits and explicit classified-review CLI intents."""

import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    state={'revision':'observed','mutations':[], 'mismatch':False, 'policy':{'version':1,'classificationRequired':True},
           'rows':[{'id':'legacy','accountId':'cash','date':'2026-11-01','merchant':'Fictional employer','amount':-100,
                    'currency':'USD','pending':False,'reviewed':True,'categoryId':None,'note':' [YNAB]  ','isTransfer':True},
                   {'id':'valid','accountId':'cash','date':'2026-11-01','merchant':'Fictional shop','amount':100,
                    'currency':'USD','pending':False,'reviewed':True,'categoryId':'expense','note':'','isTransfer':False}]}
    categories=[{'id':'expense','name':'Food','group':'Life','target':0,'currency':'USD'}]
    def respond(request):
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200,json={'budgets':[{'id':'budget','name':'Fictional'}]})
        if request.url.path.endswith('/transaction-search'):
            return httpx.Response(404,json={'detail':'Unsupported fast read'})
        if request.url.path.endswith('/queries/review_policy'):
            return httpx.Response(200,json={'budgetId':'budget','revision':'observed','data':state['policy']})
        if request.url.path.endswith('/queries/categories'):
            return httpx.Response(200,json={'budgetId':'budget','revision':'observed','data':categories})
        if request.url.path.endswith('/queries/features'):
            return httpx.Response(200,json={'budgetId':'budget','revision':'observed','data':{}})
        if request.url.path.endswith('/queries/accounts'):
            return httpx.Response(200,json={'budgetId':'budget','revision':'observed','data':[{'id':'cash','name':'Fictional cash'}]})
        if request.url.path.endswith('/queries/transactions'):
            return httpx.Response(200,json={'budgetId':'budget','revision':'different' if state['mismatch'] else 'observed','data':state['rows']})
        body=json.loads(request.content)
        if body['operation']=='preview_bulk_edits':
            changes=[]
            for edit in body['parameters']['edits']:
                before=next(row for row in state['rows'] if row['id']==edit['transactionId'])
                changes.append({'id':before['id'],'before':deepcopy(before),'after':{**before,'reviewed':edit['reviewed']}})
            return httpx.Response(200,json={'budgetId':'budget','revision':'observed','data':{'count':len(changes),'changed':len(changes),'changes':changes}})
        state['mutations'].append(body)
        changes=[]
        for edit in body['payload']['edits']:
            before=next(row for row in state['rows'] if row['id']==edit['transactionId'])
            after={**before,'reviewed':edit['reviewed']}
            changes.append({'id':before['id'],'before':deepcopy(before),'after':after})
        return httpx.Response(200,json={'budgetId':'budget','revision':'saved','data':{'count':len(changes),'changed':len(changes),'changes':changes}})
    @contextmanager
    def connect(*args,**kwargs):
        with httpx.Client(base_url='https://localhost:5551',transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console,'local_connection',connect)
    monkeypatch.setenv('WHAB_HOME',str(tmp_path/'fictional'))
    return state


@pytest.mark.parametrize('output',['json','table'])
def test_complete_audit_is_read_only_and_reports_legacy_hint(endpoint,capsys,output):
    assert console.main(['Fictional','trnx','review-audit','--format',output])==0
    text=capsys.readouterr().out
    if output=='table':
        assert 'legacy' in text and 'No records were changed' in text
    else:
        assert json.loads(text)['total']==1
    assert not endpoint['mutations']


def test_audit_rejects_mixed_revision_and_invalid_date_window(endpoint,capsys):
    endpoint['mismatch']=True
    assert console.main(['Fictional','trnx','review-audit'])==1
    assert 'changed' in capsys.readouterr().err
    endpoint['mismatch']=False
    assert console.main(['Fictional','trnx','review-audit','--since','2026-12-01','--until','2026-11-01'])==1
    assert 'on or before' in capsys.readouterr().err and not endpoint['mutations']


@pytest.mark.parametrize('option,target',[('--reviewed','valid'),('--needs-review','legacy')])
def test_explicit_review_reuses_one_atomic_public_operation(endpoint,capsys,option,target):
    assert console.main(['Fictional','trnx','review',target,option,'--agent','Codex','--batch','fixture','--dry-run'])==0
    assert json.loads(capsys.readouterr().out)['dryRun'] is True and not endpoint['mutations']
    assert console.main(['Fictional','trnx','review',target,option,'--agent','Codex','--batch','fixture'])==0
    assert json.loads(capsys.readouterr().out)['data']['count']==1
    assert len(endpoint['mutations'])==1
    intent=endpoint['mutations'][0]
    assert intent['operation']=='edit_transactions' and intent['expected_revision']=='observed'
    assert intent['payload']=={'edits':[{'transactionId':target,'reviewed':option=='--reviewed'}]}


@pytest.mark.parametrize('command',['review','review-audit'])
def test_new_help_is_complete_and_offline(monkeypatch,tmp_path,capsys,command):
    monkeypatch.setenv('WHAB_HOME',str(tmp_path/'absent'))
    with pytest.raises(SystemExit) as stopped:
        console.main(['Fictional','trnx',command,'--help'])
    assert stopped.value.code==0
    output=capsys.readouterr().out
    for text in ['Expense','Income','Transfer','1–100','16 MiB','legacy','--dry-run']:
        assert text.casefold() in output.casefold()


@pytest.mark.parametrize('policy', ['unsupported', {'version':0}, {'version':1,'classificationRequired':False}])
def test_review_refuses_unsupported_authority_before_mutation(endpoint,capsys,policy):
    endpoint['policy']=policy
    assert console.main(['Fictional','trnx','review','valid','--reviewed'])==1
    assert 'Update the authoritative' in capsys.readouterr().err
    assert not endpoint['mutations']


def test_audit_includes_pending_but_ignores_unreviewed(endpoint,capsys):
    endpoint['rows'].extend([{**endpoint['rows'][1], 'id':'pending', 'pending':True},
                             {**endpoint['rows'][0], 'id':'not-reviewed', 'reviewed':False}])
    assert console.main(['Fictional','trnx','review-audit'])==0
    assert {row['id'] for row in json.loads(capsys.readouterr().out)['data']}=={'legacy','pending'}
    assert not endpoint['mutations']
