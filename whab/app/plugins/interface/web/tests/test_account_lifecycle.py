"""Account settings archive every type while leaving historical registers usable."""


def test_web_cd_archive_settings_sidebar_picker_and_history(client,store):
    identifier=store.add_account('personal',{'name':'Fictional CD','type':'deposit','subtype':'cd','onBudget':False,'balance':50000})
    peer=store.add_account('personal',{'name':'Open wallet','type':'depository','balance':10000})
    transaction=store.add_transaction('personal',{'accountId':identifier,'date':'2026-10-01','merchant':'Fictional historic interest','amount':-100,'categoryId':None,'note':'Preserve me'})
    before=store.transactions('personal')
    response=client.patch('/api/accounts/'+identifier,json={'workspace':'personal','closed':True})
    assert response.status_code==200
    settings=client.get('/api/accounts/'+identifier+'/settings?workspace=personal').json()
    assert settings['account']['closed'] and settings['account']['subtype']=='cd'
    assert settings['account']['balance']==50100
    page=client.get('/budgets/home/accounts/2026-10/usd').text
    assert 'Closed · archive' in page and 'Account status for Fictional CD' in page
    assert 'data-account-id="'+identifier+'"' not in page
    assert 'data-account-id="'+peer+'"' in page
    register=client.get('/budgets/home/transactions/2026-10/usd?account='+identifier).text
    assert 'Fictional historic interest' in register and transaction in register
    assert 'Reopen this archived account before adding transactions' in register
    assert 'data-account-closed' in register
    assert store.transactions('personal')==before
    rejected=client.post('/api/transactions',json={'workspace':'personal','accountId':identifier,'date':'2026-10-02','merchant':'Rejected new entry','amount':1,'categoryId':None})
    assert rejected.status_code==400
    reopened=client.patch('/api/accounts/'+identifier,json={'workspace':'personal','closed':False})
    assert reopened.status_code==200
    assert not store.accounts('personal')[0].get('closed',False)
    assert 'data-account-id="'+identifier+'"' in client.get('/budgets/home/accounts/2026-10/usd').text
