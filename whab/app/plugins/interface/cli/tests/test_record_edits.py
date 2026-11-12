"""CLI record edits verify results and retain one intent through refusals/uncertainty."""

import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    record = {'id':'transaction','note':'  [YNAB: uncleared]  ','merchant':'  Fictional shop  ',
              'amount':123,'categoryId':'rent','reviewed':True,'accountId':'checking','date':'2026-11-01'}
    leaf = {'id':'rent','name':'Rent','group':'House','parentId':'house','currency':'USD','target':1234}
    state = {'revision':'before','rows':[record], 'groups':[{'id':'house','name':'House','children':[leaf]}],
             'failure':None, 'posts':[], 'reads':0, 'receipt':None}

    def respond(request):
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200,json={'budgets':[{'id':'budget','name':'Fictional'}]})
        if request.method == 'POST':
            body=json.loads(request.content)
            state['posts'].append(body)
            if state['failure']=='lost':
                raise httpx.ReadError('private token')
            if state['failure'] in [400,409]:
                return httpx.Response(state['failure'],json={'detail':'Fictional refused edit'})
            if body['operation']=='append_transaction_note':
                previous=state['rows'][0]['note']
                state['rows'][0]['note'] = previous + ('\n' if previous else '') + body['payload']['text']
            else:
                state['groups'][0]['children'][0]['name']=body['payload']['name'].strip()
            state['revision']='saved'
            state['receipt']=body
            return httpx.Response(200,json={'budgetId':'other' if state['failure']=='wrong-budget' else 'budget',
                'revision':'saved','data':'invalid' if state['failure']=='invalid-result' else None})
        state['reads']+=1
        is_tree=request.url.path.endswith('/category_tree')
        data=deepcopy(state['groups'] if is_tree else state['rows'])
        if state['posts'] and state['failure']=='mismatch':
            if is_tree:
                data[0]['children'][0]['target']=999
            else:
                data[0]['merchant']='trimmed unexpectedly'
        if state['posts'] and state['failure']=='missing-saved':
            data[0]['id']='other'
        return httpx.Response(200,json={'budgetId':'budget',
            'revision':'different' if state['posts'] and state['failure']=='revision' else state['revision'],'data':data})

    @contextmanager
    def connect(*_args,**_kwargs):
        with httpx.Client(base_url='https://localhost:5551',transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(console,'local_connection',connect)
    monkeypatch.setenv('WHAB_HOME',str(tmp_path/'fictional'))
    return state


@pytest.mark.parametrize('kind', ['note','rename'])
@pytest.mark.parametrize('failure', [400,409,'lost','wrong-budget','invalid-result','revision','mismatch','missing-saved'])
def test_failed_edits_are_never_replayed_and_keep_complete_intent(endpoint,capsys,kind,failure):
    endpoint['failure']=failure
    args=(['trnx','note','transaction','--append','Exact appended text'] if kind=='note' else
          ['category','rename','House / Rent','--name','Main Residence'])
    assert console.main(['Fictional',*args,'--agent','Codex','--batch','fictional-batch'])==1
    result=json.loads(capsys.readouterr().err)
    assert len(endpoint['posts'])==1
    assert 'No automatic retry' in result['error'] and 'original intent' in result['error']
    assert 'private token' not in result['error']
    posted=endpoint['posts'][0]
    assert posted['expected_revision']=='before' and posted['request_id']
    assert posted['history']=={'actor':'Codex','actorKind':'agent','batchId':'fictional-batch'}
    assert ('refused; no new edit was saved' in result['error']) == (failure in [400,409])


@pytest.mark.parametrize('kind', ['note','rename'])
def test_manual_verified_output_and_recovery_options(endpoint,capsys,kind):
    args=(['trnx','note','transaction','--append','Raw  text'] if kind=='note' else
          ['category','rename','rent','--name','Main Residence'])
    assert console.main(['Fictional',*args,'--request-id','fictional-request','--revision','before','--format','table'])==0
    output=capsys.readouterr().out
    assert 'Budget:' in output and ('[YNAB: uncleared]' in output if kind=='note' else 'Main Residence' in output)
    posted=endpoint['posts'][0]
    assert posted['request_id']=='fictional-request' and 'history' not in posted
    if kind=='note':
        assert posted['payload']=={'transactionId':'transaction','text':'Raw  text'}
        assert 'Saved and verified' in output


def test_missing_invalid_notes_and_duplicate_siblings_do_not_write(endpoint,capsys):
    assert console.main(['Fictional','trnx','note','absent','--append','Text'])==1
    assert 'not found' in capsys.readouterr().err
    endpoint['rows'][0]['note']=None
    assert console.main(['Fictional','trnx','note','transaction','--append','Text'])==1
    assert 'invalid saved memo' in capsys.readouterr().err
    endpoint['groups'][0]['children'].append({'id':'reserved','name':'Reserved'})
    assert console.main(['Fictional','category','rename','rent','--name','reserved'])==1
    assert 'already exists' in capsys.readouterr().err
    assert endpoint['posts']==[]


def test_empty_note_has_no_leading_newline_and_batch_only_is_attributed(endpoint,capsys):
    endpoint['rows'][0]['note']=''
    assert console.main(['Fictional','trnx','note','transaction','--append','  exact  ','--batch','manual-batch'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['note']=='  exact  '
    assert endpoint['posts'][0]['history']=={'batchId':'manual-batch'}
