"""Finding EDGAR companies a question names but Recto has not read yet.

Plenty of company names are ordinary words (Target, Block, Gap, Visa), so a
match needs a capitalised name, a possessive, or a ticker written in capitals —
and finance acronyms that happen to be tickers (AI, IT, CEO) never count.
"""
from insightledger.agents.entities import edgar_companies_in

CO = [
    {"ticker": "KO", "title": "COCA COLA CO", "cik": 21344},
    {"ticker": "COST", "title": "COSTCO WHOLESALE CORP /NEW", "cik": 909832},
    {"ticker": "TGT", "title": "TARGET CORP", "cik": 27419},
    {"ticker": "HD", "title": "HOME DEPOT, INC.", "cik": 354950},
    {"ticker": "AAPL", "title": "Apple Inc.", "cik": 320193},
    {"ticker": "APLE", "title": "Apple Hospitality REIT, Inc.", "cik": 1418121},
    {"ticker": "AI", "title": "C3.ai, Inc.", "cik": 1577526},
    {"ticker": "IT", "title": "GARTNER INC", "cik": 749251},
    {"ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC", "cik": 1067983},
]
t = lambda q: [c["ticker"] for c in edgar_companies_in(q, CO)]


def test_possessives_and_capitalised_names():
    assert t("What was Coca-Cola's net revenue?") == ["KO"]
    assert t("How much did Costco spend on capital expenditures?") == ["COST"]
    assert t("What were Home Depot's net sales?") == ["HD"]
    assert t("How much cash does Berkshire Hathaway hold?") == ["BRK-B"]


def test_the_plain_company_wins_over_a_longer_namesake():
    assert t("What was Apple's revenue?") == ["AAPL"]


def test_tickers_in_capitals():
    assert t("Compare KO and TGT on revenue") == ["KO", "TGT"]


def test_ordinary_words_do_not_trigger_a_fetch():
    assert t("What is the target leverage ratio?") == []
    assert t("How does the company use AI in its products?") == []
    assert t("What did the CEO say about IT spending?") == []
    assert t("What are the most significant risk factors?") == []


def test_a_word_many_companies_start_with_is_not_a_name():
    many = CO + [{"ticker": f"CN{i}", "title": f"CHINA {w} CORP", "cik": 900 + i}
                 for i, w in enumerate(["YUCHAI", "AUTOMOTIVE", "PHARMA"])]
    got = [c["ticker"] for c in edgar_companies_in("How exposed is the company to China?", many)]
    assert got == []


def test_one_family_of_names_counts_but_places_do_not():
    co = CO + [{"ticker": "F", "title": "FORD MOTOR CO", "cik": 37996},
               {"ticker": "FMCC", "title": "FORD MOTOR CREDIT CO LLC", "cik": 38009},
               {"ticker": "NALG", "title": "North America Lithium & Gold", "cik": 5},
               {"ticker": "AXP", "title": "AMERICAN EXPRESS CO", "cik": 4962}]
    pick = lambda q: [c["ticker"] for c in edgar_companies_in(q, co)]
    assert pick("How much debt does Ford carry?") == ["F"]
    assert pick("What was Nike's revenue in North America?") == []
    assert pick("What did American Express earn?") == ["AXP"]


def test_names_with_a_lowercase_connective():
    co = CO + [{"ticker": "BAC", "title": "BANK OF AMERICA CORP /DE/", "cik": 70858},
               {"ticker": "PG", "title": "PROCTER & GAMBLE Co", "cik": 80424}]
    pick = lambda q: [c["ticker"] for c in edgar_companies_in(q, co)]
    assert pick("What was Bank of America's net income?") == ["BAC"]
    assert pick("How did Procter & Gamble's margins move?") == ["PG"]
    assert pick("What is the revenue of Apple?") == ["AAPL"]
