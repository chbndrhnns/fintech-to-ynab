from datetime import datetime
from decimal import Decimal

from dateutil.parser import parse
from pynYNAB.schema.budget import Transaction

import settings as settings_module
import ynab_client as ynab_client_module


def create_transactions_from_ofx(data, settings=settings_module, ynab_client=ynab_client_module):
    settings.log.debug('Received data %s' % data)
    expected_delta = 0
    data_type = data.keys()
    settings.log.debug('webhook type received %s', data_type)
    if len(data_type) != 1 and data_type[0] != 'transactions':
        return {'error': 'Unsupported webhook type: %s' % data_type}, 400

    # the actual monzo data is in the data['data]' value
    transactions = data['transactions']

    # Sync the account so we get the latest payees
    ynab_client.sync()

    for data in transactions:
        if data['amount'] == 0:
            return {'error': 'Transaction amount is 0.'}, 200

        # Does this account exist?
        account = ynab_client.getaccount(settings.ynab_account)
        if not account:
            return {'error': 'Account {} was not found'.format(settings.ynab_account)}, 400

        # Work out the Payee Name
        if data.get('merchant'):
            payee_name = data['merchant']['name']
            subcategory_id = get_subcategory_from_payee(payee_name)
        else:
            # This is a p2p transaction
            payee_name = get_p2p_transaction_payee_name(data)
            subcategory_id = None

        # If we are creating the payee, then we need to increase the delta
        if not ynab_client.payeeexists(payee_name):
            settings.log.debug('payee does not exist, will create %s', payee_name)
            expected_delta += 1

        # Get the payee ID. This will append a new one if needed
        entities_payee_id = ynab_client.getpayee(payee_name).id

        memo = None

        # Show the local currency in the notes if this is not in the accounts currency
        flag = None
        cleared = None
        if data['local_currency'] != data['currency']:
            memo += ' (%s %s)' % (data['local_currency'], (abs(data['local_amount']) / 100))
            flag = 'Orange'
        else:
            cleared = 'Cleared'

        # Create the Transaction
        expected_delta += 1
        settings.log.debug('Creating transaction object')
        transaction = Transaction(
            check_number=data['id'],
            entities_account_id=account.id,
            amount=Decimal(data['amount']) / 100,
            date=parse(data['created']),
            entities_payee_id=entities_payee_id,
            imported_date=datetime.now().date(),
            imported_payee=payee_name,
            memo=memo,
            source="Imported",
            flag=flag,
            cleared=cleared
        )

        if subcategory_id is not None:
            transaction.entities_subcategory_id = subcategory_id

        settings.log.debug('Duplicate detection')
        if ynab_client.containsDuplicate(transaction):
            settings.log.debug('skipping due to duplicate transaction')
            return {'error': 'Tried to add a duplicate transaction.'}, 200
        else:
            settings.log.debug('appending and pushing transaction to YNAB. Delta: %s', expected_delta)
            ynab_client.client.budget.be_transactions.append(transaction)
            ynab_client.client.push(expected_delta)
            return {'message': 'Transaction created in YNAB successfully.'}, 201


def get_subcategory_from_payee(payee_name, settings=settings_module, ynab_client=ynab_client_module):
    """
    Get payee details for a previous transaction in YNAB.
    If a payee with payee_name has been used in the past, we can get their ID and
    pre-populate category.

    :param ynab_client: pynYNAB client object
    :param settings: The settings from `settings.py`
    :param payee_name: The name of the Payee as coming from the bank.
    :return: (payee_id, subcategory_id)
    """
    previous_transaction = ynab_client.findPreviousTransaction(payee_name)
    if previous_transaction is not None and previous_transaction.entities_payee is not None:
        settings.log.debug('A previous transaction for the payee %s has been found', payee_name)
        return get_subcategory_id_for_transaction(previous_transaction, payee_name)
    else:
        settings.log.debug('A previous transaction for the payee %s has not been found', payee_name)
    return None


def get_subcategory_id_for_transaction(transaction, payee_name, settings=settings_module):
    """
    Gets the subcategory ID for a transaction.
    Filters out transactions that have multiple categories.

    :param settings: The settings from `settings.py`
    :param payee_name: Name of the Payee
    :param transaction: The transaction to get subcategory ID from.
    :return: The subcategory ID, or None if it is a multiple-category transaction.
    """
    subcategory = transaction.entities_subcategory

    if subcategory is not None:
        if subcategory.name != 'Split (Multiple Categories)...':
            settings.log.debug('We have identified the "%s" category as a good default for this payee',
                               subcategory.name)
            return subcategory.id
        else:
            settings.log.debug('Split category found, so we will not use that category for %s', payee_name)
    else:
        settings.log.debug('A subcategory was not found for the previous transaction for %s', payee_name)


def get_p2p_transaction_payee_name(data):
    """
    Get the payee name for a p2p transaction, based on webook transaction data.

    :param data: The 'data' key of the transaction data.
    :return: The string name of the payee.
    """
    if data.get('counterparty'):
        if data['counterparty'].has_key('name'):
            payee_name = data['counterparty']['name']
        else:
            payee_name = data['counterparty']['number']
    elif data.get('metadata', {}).get('is_topup') == 'true':
        payee_name = 'Topup'
    else:
        payee_name = 'Unknown Payee'

    return payee_name
