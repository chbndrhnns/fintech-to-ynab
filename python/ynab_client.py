import settings

from pynYNAB.Client import nYnabClient
from pynYNAB.schema.budget import Transaction, Payee

from sqlalchemy.sql.expression import exists

client = None


def init():
    global client
    client = nYnabClient(email=settings.ynab_username, password=settings.ynab_password, budgetname=settings.ynab_budget,
                         logger=settings.log)


accounts = {}
payees = {}


def sync():
    settings.log.debug('syncing')
    client.sync()
    cache_accounts()
    cache_payees()


def cache_accounts():
    global accounts
    settings.log.debug('assigning accounts')
    accounts = {x.account_name: x for x in client.budget.be_accounts}


def cache_payees():
    global payees
    settings.log.debug('assigning payees')
    payees = {p.name: p for p in client.budget.be_payees}


def getaccount(accountname):
    try:
        settings.log.debug('searching for account %s' % accountname)
        return accounts[accountname]
    except KeyError:
        settings.log.error('Couldn''t find this account: %s' % accountname)
        return False


def payeeexists(payeename):
    try:
        return payees[payeename]
    except KeyError:
        return False


def getpayee(payeename):
    try:
        settings.log.debug('searching for payee %s' % payeename)
        return payees[payeename]
    except KeyError:
        settings.log.debug('Couldn''t find this payee: %s' % payeename)
        payee = Payee(name=payeename)
        client.budget.be_payees.append(payee)
        cache_payees()
        return payee


def containsDuplicate(transaction):
    return client.session.query(exists()
                                .where(Transaction.amount == transaction.amount)
                                .where(Transaction.entities_account_id == transaction.entities_account_id)
                                .where(Transaction.date == transaction.date.date())
                                .where(Transaction.imported_payee == transaction.imported_payee)
                                .where(Transaction.source == transaction.source)
                                ).scalar()


def findPreviousTransaction(payee_name):
    return client.session.query(Transaction) \
        .filter(Transaction.imported_payee == payee_name) \
        .order_by(Transaction.date.desc()) \
        .first()
