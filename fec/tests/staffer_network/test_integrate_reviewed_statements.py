from fec.staffer_network.integrate_reviewed_statements import unique_identity, compatible_name, compatible_career_identity


def test_identity_needs_name_and_bounded_statement_and_unique_target():
    names = {'alex smith': {'p1', 'p2'}, 'alex jones': {'p3'}}
    clauses = {'assistant senator x 2010 2012': {'p1', 'p3'}}
    assert unique_identity(['Alex Smith'], ['Assistant, Senator X (2010-2012)'], names, clauses) == 'p1'
    assert unique_identity(['Alex Smith'], ['Director, Senator Y (2015-2018)'], names, clauses) is None
    assert unique_identity(['Other Person'], ['Assistant, Senator X (2010-2012)'], names, clauses) is None
    clauses['assistant senator x 2010 2012'].add('p2')
    assert unique_identity(['Alex Smith'], ['Assistant, Senator X (2010-2012)'], names, clauses) is None


def test_conflicting_career_matches_do_not_choose_arbitrary_identity():
    assert unique_identity(['Alex Smith'], ['career one', 'career two'],
        {'alex smith': {'p1', 'p2'}}, {'career one': {'p1'}, 'career two': {'p2'}}) is None


def test_name_expansions_require_compatible_middle_names_and_suffixes():
    assert compatible_name('Juliane C Sullivan', 'ms Juliane Carter Sullivan')
    assert compatible_name('Alex Smith', 'mr Alex J Smith')
    assert not compatible_name('Alex John Smith', 'Alex James Smith')
    assert not compatible_name('Alex Smith Jr', 'Alex Smith Sr')
    assert not compatible_name('Alex Smith', 'Alexander Smith')


def test_compatible_name_still_requires_exact_career_and_unique_person():
    names = {'p1': ('ms Juliane Carter Sullivan', 'p1'), 'p2': ('Juliane C Sullivan', 'p2')}
    assert compatible_career_identity(['Juliane C Sullivan'], ['role 2010 2012'], names, {'role 2010 2012': {'p1'}}) == 'p1'
    assert compatible_career_identity(['Juliane C Sullivan'], ['role 2010 2012'], names, {'role 2010 2012': {'p1', 'p2'}}) is None
    assert compatible_career_identity(['Juliane C Sullivan'], ['role 2010 2012'], names, {'different role': {'p1'}}) is None
