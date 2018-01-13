from datetime import datetime

from dateutil.parser import parse
from decimal import Decimal
from flask import json
from pynYNAB.exceptions import WrongPushException
from pynYNAB.schema.budget import Transaction

import ynab_client as ynab_client_module, settings as settings_module


def create_transactions(data, settings=settings_module, ynab_client=ynab_client_module):
    data = json.loads(data)
    data_type = data.keys()[0]
    settings.log.debug('webhook type received %s', data_type)
    if data_type != 'transactions':
        return {'error': 'Unsupported webhook type: %s' % data_type}, 400

    transactions = data['transactions']

    # create dict to keep results
    results = {'message': 'Transaction(s) processed',
               'duplicates': [],
               'created': 0}

    # Sync the account so we get the latest payees
    ynab_client.sync()

    # Process in batches of 20 (default) transactions in case there are more
    try:
        for chunk in get_chunk_of_transactions(transactions, settings.transaction_chunk_size):
            created, duplicates = process_chunk(chunk, settings, ynab_client)
            results['duplicates'].extend(duplicates)
            created_count = created - len(duplicates)
            if created_count > 0:
                created_count = 0
            results['created'] += created_count
    except AccountNotFoundError as exc:
        return {"error": "{}".format(exc.message)}, 400
    except UnicodeError as exc:
        return {"error": "{}".format(exc.message)}, 400

    if results['created'] is 0:
        return results, 200
    else:
        return results, 201


def get_chunk_of_transactions(lst, chunk_size):
    for i in xrange(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


class AccountNotFoundError(RuntimeError):
    pass


def process_chunk(chunk, settings, ynab_client):
    duplicates = []
    created = 0
    expected_delta = 0

    for t in chunk:
        try:
            settings.log.debug(t)
            # Does this account exist?
            account = ynab_client.getaccount(t['account'])
            if not account:
                raise AccountNotFoundError('Account {} was not found'.format(t['account']))

            payee_name = t.get('payee', '')
            # If we are creating the payee, then we need to increase the delta
            subcategory_id = None
            if ynab_client.payeeexists(payee_name):
                settings.log.debug('payee exists, using %s', payee_name)
                subcategory_id = get_subcategory_from_payee(payee_name)
            else:
                settings.log.debug('payee does not exist, will create %s', payee_name)
                expected_delta += 1

            entities_payee_id = ynab_client.getpayee(payee_name).id

            # Create the Transaction
            settings.log.debug('Creating transaction object')
            transaction = Transaction(
                entities_account_id=account.id,
                amount=Decimal(t.get('amount')),
                date=parse(t.get('created')),
                entities_payee_id=entities_payee_id,
                imported_date=datetime.now().date(),
                imported_payee=payee_name,
                memo=u'{} {}'.format(t.get('memo', 'n/a'), '[m2ynab]'),
                cleared='Cleared',
                source="Imported",
            )

            if subcategory_id is not None:
                transaction.entities_subcategory_id = subcategory_id

            if ynab_client.containsDuplicate(transaction):
                settings.log.debug('skipping due to duplicate transaction')
                duplicates.append(t)
            else:
                expected_delta += 1
                settings.log.debug('appending and pushing transaction to YNAB. Delta: %s', expected_delta)
                ynab_client.client.budget.be_transactions.append(transaction)
        except AccountNotFoundError as exc:
            raise exc
        except UnicodeError as exc:
            raise UnicodeError(exc, t)

    try:
        if len(ynab_client.client.budget.be_transactions) is not 0:
            ynab_client.client.push(expected_delta)
            created += len(chunk)
    except WrongPushException as exc:
        # clean up
        settings.log.error(
            'Got WrongPushException from pynYNAB: expected_delta={}, delta={}'.format(exc.expected_delta,
                                                                                      exc.delta))
    return created, duplicates


def create_transaction_from_starling(data, settings=settings_module, ynab_client=ynab_client_module):
    settings.log.debug('received data %s' % data)
    expected_delta = 0
    if not data.get('content') or not data['content'].get('type'):
        return {'error': 'No webhook content type provided'}, 400
    if not data.get('content') or not data['content'].get('type') or not data['content']['type'] in ['TRANSACTION_CARD',
                                                                                                     'TRANSACTION_FASTER_PAYMENT_IN',
                                                                                                     'TRANSACTION_FASTER_PAYMENT_OUT',
                                                                                                     'TRANSACTION_DIRECT_DEBIT']:
        return {'error': 'Unsupported webhook type: %s' % data.get('content')['type']}, 400
    # Sync the account so we get the latest payees
    ynab_client.sync()

    if data['content']['amount'] == 0:
        return {'error': 'Transaction amount is 0.'}, 200

    # Does this account exist?
    account = ynab_client.getaccount(settings.starling_ynab_account)
    if not account:
        return {'error': 'Account {} was not found'.format(settings.starling_ynab_account)}, 400

    payee_name = data['content']['counterParty']
    subcategory_id = None
    flag = None
    cleared = None
    memo = ''

    # If we are creating the payee, then we need to increase the delta
    if ynab_client.payeeexists(payee_name):
        settings.log.debug('payee exists, using %s', payee_name)
        subcategory_id = get_subcategory_from_payee(payee_name)
    else:
        settings.log.debug('payee does not exist, will create %s', payee_name)
        expected_delta += 1

    entities_payee_id = ynab_client.getpayee(payee_name).id

    if data['content']['sourceCurrency'] != 'GBP':
        memo += ' (%s %s)' % (data['content']['sourceCurrency'], abs(data['content']['sourceAmount']))
        flag = 'Orange'
    else:
        cleared = 'Cleared'

    # Create the Transaction
    expected_delta += 1
    settings.log.debug('Creating transaction object')
    transaction = Transaction(
        check_number=data['content'].get('transactionUid'),
        entities_account_id=account.id,
        amount=data['content']['amount'],
        date=parse(data['timestamp']),
        entities_payee_id=entities_payee_id,
        imported_date=datetime.now().date(),
        imported_payee=payee_name,
        flag=flag,
        cleared=cleared,
        memo=memo
    )

    if subcategory_id is not None:
        transaction.entities_subcategory_id = subcategory_id

    settings.log.debug('Duplicate detection')
    if ynab_client.containsDuplicate(transaction):
        settings.log.debug('skipping due to duplicate transaction')
        return {'error': 'Tried to add a duplicate transaction.'}, 200
    else:
        expected_delta += 1
        settings.log.debug('appending and pushing transaction to YNAB. Delta: %s', expected_delta)
        ynab_client.client.budget.be_transactions.append(transaction)
        ynab_client.client.push(expected_delta)
        return {'message': 'Transaction created in YNAB successfully.'}, 201


def create_transaction_from_monzo(data, settings=settings_module, ynab_client=ynab_client_module):
    settings.log.debug('received data %s' % data)
    expected_delta = 0
    data_type = data.get('type')
    settings.log.debug('webhook type received %s', data_type)
    if data_type != 'transaction.created':
        return {'error': 'Unsupported webhook type: %s' % data_type}, 400

    # the actual monzo data is in the data['data]' value
    data = data['data']

    if 'decline_reason' in data:
        return {'message': 'Ignoring declined transaction ({})'.format(data['decline_reason'])}, 200

    # Sync the account so we get the latest payees
    ynab_client.sync()

    if data['amount'] == 0:
        return {'error': 'Transaction amount is 0.'}, 200

    # Does this account exist?
    account = ynab_client.getaccount(settings.monzo_ynab_account)
    if not account:
        return {'error': 'Account {} was not found'.format(settings.monzo_ynab_account)}, 400

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

    memo = ''
    if settings.include_emoji and data['merchant'] and data['merchant'].get('emoji'):
        memo += ' %s' % data['merchant']['emoji']

    if settings.include_tags and data['merchant'] and data['merchant'].get('metadata', {}).get('suggested_tags'):
        memo += ' %s' % data['merchant']['metadata']['suggested_tags']

    # Show the local currency in the notes if this is not in the accounts currency
    flag = None
    cleared = None
    if data['local_currency'] != data['currency']:
        memo += ' (%s %s)' % (data['local_currency'], (abs(Decimal(data['local_amount'])) / 100))
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


def create_transaction_from_csv(data, account, settings=settings_module, ynab_client=ynab_client_module):
    settings.log.debug('received data %s' % data)
    expected_delta = 0

    if data['amount'] == 0:
        return {'error': 'Transaction amount is 0.'}

    payee_name = data['description']
    subcategory_id = get_subcategory_from_payee(payee_name)

    # If we are creating the payee, then we need to increase the delta
    if not ynab_client.payeeexists(payee_name):
        print 'does not exist'
        settings.log.debug('payee does not exist, will create %s', payee_name)
        expected_delta += 1

    # Get the payee ID. This will append a new one if needed
    entities_payee_id = ynab_client.getpayee(payee_name).id

    # If we created a payee, then we need to resync the payees cache
    if expected_delta == 1:
        ynab_client.cache_payees()

    # Create the Transaction
    settings.log.debug('Creating transaction object')
    transaction = Transaction(
        entities_account_id=account.id,
        amount=Decimal(data['amount']),
        date=parse(data['date'], dayfirst=True),
        entities_payee_id=entities_payee_id,
        imported_date=datetime.now().date(),
        imported_payee=payee_name,
        cleared=True,
        source='Imported'
    )

    if subcategory_id is not None:
        transaction.entities_subcategory_id = subcategory_id

    settings.log.debug('Duplicate detection')
    if ynab_client.containsDuplicate(transaction):
        settings.log.debug('skipping due to duplicate transaction')
        # We may just be adding a payee
        if expected_delta == 1:
            print 'just adding payee'
            return expected_delta
        else:
            return {'error': 'Tried to add a duplicate transaction.'}
    else:
        expected_delta += 1
        settings.log.debug('appending and pushing transaction to YNAB. Delta: %s', expected_delta)
        ynab_client.client.budget.be_transactions.append(transaction)
        return expected_delta


def get_subcategory_from_payee(payee_name, settings=settings_module, ynab_client=ynab_client_module):
    """
    Get payee details for a previous transaction in YNAB.
    If a payee with payee_name has been used in the past, we can get their ID and
    pre-populate category.

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
        payee_name = data.get('description')

    return payee_name
