"""Separate unobserved PAC money from verified zero and observed contributions."""


def industry_money_missing(row):
    """Only impute when neither current verification nor any money observation exists.

    Candidate fundraising totals can be absent even when the contribution query
    verified actual PAC dollars. An absent total must not erase that evidence.
    """
    if row.get('money_source_status') == 'VERIFIED_OPENFEC_CURRENT_CYCLE':
        return False
    return not any((row.get(key) or 0) > 0
                   for key in ('receipts', 'cu', 'banks', 'finserv'))
