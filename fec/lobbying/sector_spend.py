# -*- coding: utf-8 -*-
"""Canonical LDA sector-spend and lobbying-capacity measurement, 2021-2026.

This module deliberately measures two different financial series and never adds
them: ``income`` is the fee paid to an outside registrant, while ``expenses`` is
the client's self-reported in-house lobbying-expense estimate.  It is not a wage
measure.  Concentration is consequently defined on income only.

The 2026 data are a PARTIAL YEAR through Q2.  Every annual output carries an
``is_partial_year`` flag; a 2026 annual value must not be compared with a full
year without saying so.  Sector membership is a pinned name-pattern instrument.
Its absolute LEVEL depends on the roster choice, while year-over-year SHAPE is
the defensible claim.  ``ROSTERS`` is therefore module-level, ordered, shared by
all periods, and accompanied by explicit client-ID includes and excludes.

The production reader is intentionally two-pass and bounded-memory.  Pass one
receipts filing-UUID pagination duplicates and derives nonfinancial filing types
from their display labels and observed financial fields.  Pass two excludes
those types and keeps one latest filing per registrant/client/year/period.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lobbying import entity_identity

# Canonical entity identity is ON by default.  ENTITY_IDENTITY=0 reverts every
# call site below to the pre-table behaviour, which is how the before/after
# deltas in the identity report were measured.
ENTITY_IDENTITY = os.environ.get("ENTITY_IDENTITY", "1").strip().lower() not in {"0", "false", "off", "no"}


class SectorSpendError(RuntimeError):
    """Raised when the source corpus or requested measurement is ambiguous."""


DEFAULT_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
EXPECTED_FULL_CORPUS = {
    "raw_filings": 539870,
    "unique_filing_uuids": 539782,
    "pagination_duplicates_removed": 88,
    "nonfinancial_records_excluded": 31344,
    "records_superseded": 19242,
    "dt_posted_missing_or_invalid": 0,
}
PARTIAL_YEARS = {2026: "Partial year through Q2"}
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
PERIOD_TO_QUARTER = {
    "first_quarter": "Q1",
    "second_quarter": "Q2",
    "third_quarter": "Q3",
    "fourth_quarter": "Q4",
}

# Pattern order is policy: the first matching row supplies the canonical family.
# A ``None`` family means "use the normalized/collapsed client family".  Client
# IDs are strings because the API has historically represented identifiers both
# as JSON numbers and as strings in synthetic/cached inputs.
ROSTERS = {
    "crypto": {
        "patterns": (
            (r"\bCOINBASE\b", "COINBASE", "Coinbase legal and trade names"),
            (r"\bBINANCE(?: US)?\b", "BINANCE", "Binance family"),
            (r"\b(?:KRAKEN|PAYWARD)\b", "KRAKEN / PAYWARD", "Kraken/Payward family"),
            (r"\bGEMINI (?:TRUST|SPACE STATION|MOONBASE)\b", "GEMINI TRUST", "Gemini Trust family incl. 2025 Space Station/Moonbase reorg names (added 2026-08-05)"),
            (r"\b(?:CRYPTO COM|FORIS DAX)\b", "CRYPTO.COM / FORIS DAX", "Crypto.com/Foris DAX family"),
            (r"\bCIRCLE (?:INTERNET|FINANCIAL)\b|^CIRCLE$", "CIRCLE", "Circle family"),
            (r"^RIPPLE$|\bRIPPLE LABS\b", "RIPPLE", "Ripple family"),
            (r"\bCONSENSYS\b", "CONSENSYS", "Consensys family"),
            (r"\bBLOCKCHAIN ASSOCIATION\b", "BLOCKCHAIN ASSOCIATION", "Industry association"),
            (r"\b(?:CHAMBER OF DIGITAL COMMERCE|DIGITAL CHAMBER)\b", "DIGITAL CHAMBER", "Digital Chamber former/current names"),
            (r"\bCRYPTO COUNCIL FOR INNOVATION\b", "CRYPTO COUNCIL FOR INNOVATION", "Industry association"),
            (r"\bGRAYSCALE\b", "GRAYSCALE", "Grayscale family"),
            (r"\bBAKKT\b", "BAKKT", "Bakkt family"),
            (r"\bSOLANA (?:FOUNDATION|POLICY INSTITUTE)\b", "SOLANA", "Solana family"),
            (r"\bUNISWAP\b", "UNISWAP", "Uniswap family"),
            (r"^PARADIGM$|\bPARADIGM OPERATIONS\b", "PARADIGM", "Paradigm crypto family"),
            (r"\b(?:A16Z|ANDREESSEN HOROWITZ)\b", "ANDREESSEN HOROWITZ / A16Z", "a16z family"),
            (r"\bFIDELITY DIGITAL\b", "FIDELITY DIGITAL ASSETS", "Required digital-assets operating family"),
            (r"\b(?:DIGITAL CURRENCY GROUP|DCG)\b", "DIGITAL CURRENCY GROUP", "DCG family"),
            (r"\bANCHORAGE DIGITAL\b", "ANCHORAGE DIGITAL", "Crypto-native custodian/bank"),
            (r"\bGALAXY DIGITAL\b", "GALAXY DIGITAL", "Crypto-native financial-services family"),
            (r"\bMARATHON DIGITAL\b", "MARATHON DIGITAL", "Digital-asset mining family"),
            # -- Additions 2026-08-05: sector-completeness research (web sources +
            #    full-corpus cross-check; every name below is a verified LDA client
            #    2021-2026 that no prior pattern matched). --
            (r"\bTETHER\b", "TETHER", "Stablecoin issuer; LDA client via outside firms since 2022"),
            (r"\bCLEANSPARK\b", "CLEANSPARK", "Bitcoin miner; LDA client since 2024"),
            (r"\bCORE SCIENTIFIC\b", "CORE SCIENTIFIC", "Bitcoin miner/datacenter; LDA client since 2025"),
            (r"\bBITDEER\b", "BITDEER", "Bitcoin miner; LDA client since 2025"),
            (r"\bGREENIDGE\b", "GREENIDGE", "Bitcoin miner (Greenidge Generation), 2022-2024"),
            (r"\bNYDIG\b", "NYDIG", "Bitcoin financial-services firm; LDA client since 2022"),
            (r"\bEXODUS MOVEMENT\b", "EXODUS MOVEMENT", "Self-custody wallet maker (NOT The Exodus Road)"),
            (r"^LEDGER$|\bLEDGER SAS\b", "LEDGER", "Hardware-wallet maker; files as LEDGER and LEDGER SAS"),
            (r"\bZERO HASH\b", "ZERO HASH", "Crypto infrastructure; LDA client since 2024"),
            (r"\b(?:OKX|OKCOIN)\b", "OKX / OKCOIN", "Exchange family; OKCoin USA then OKX Inc"),
            (r"\bBULLISH\b", "BULLISH", "Exchange (Bullish Global / Bullish US)"),
            (r"\bTOOLS FOR HUMANITY\b", "TOOLS FOR HUMANITY / WORLDCOIN", "Worldcoin developer; LDA client since 2025"),
            (r"\bONDO FINANCE\b", "ONDO FINANCE", "Tokenized-treasuries issuer; LDA client since 2025"),
            (r"\bSECURITIZE\b", "SECURITIZE", "Tokenization platform; LDA client since 2023"),
            (r"\bMULTICOIN\b", "MULTICOIN CAPITAL", "Crypto VC; LDA client since 2024"),
            (r"\bDRAGONFLY DIGITAL\b", "DRAGONFLY DIGITAL", "Crypto VC (NOT Dragonfly Therapeutics)"),
            (r"\bWINTERMUTE\b", "WINTERMUTE", "Crypto market maker; in-house LDA filer since 2025"),
            (r"\bFALCONX\b", "FALCONX", "Crypto prime broker; LDA client since 2023"),
            (r"\bALGORAND\b", "ALGORAND", "Protocol developer, 2021-2023"),
            (r"\bIOHK\b", "IOHK / INPUT OUTPUT", "Cardano developer; files as IOHK USA LLC"),
            (r"\bCHIA NETWORK\b", "CHIA NETWORK", "Blockchain company, 2021-2024"),
            (r"\bDYDX\b", "DYDX", "DeFi exchange developer, 2022-2025"),
            (r"^AAVE\b", "AAVE", "DeFi protocol US entity (AAVE LLC), 2021-2023"),
            (r"\bYUGA LABS\b", "YUGA LABS", "NFT developer, 2023-2025"),
            (r"\bMICROSTRATEGY\b", "STRATEGY / MICROSTRATEGY", "Bitcoin-treasury company; files as Strategy fka MicroStrategy"),
            (r"\bPROOF OF STAKE ALLIANCE\b", "PROOF OF STAKE ALLIANCE", "Staking-industry association, 2021-2026"),
            (r"\bDIGITAL ENERGY COUNCIL\b", "DIGITAL ENERGY COUNCIL", "Digital-asset mining/energy association"),
            (r"\bCEDAR INNOVATION FOUNDATION\b", "CEDAR INNOVATION FOUNDATION", "Crypto-industry advocacy 501(c)(4), 2023-2026"),
            (r"\bCOALITION FOR TOKENIZED MARKETS\b", "COALITION FOR TOKENIZED MARKETS", "Tokenization trade coalition, 2026"),
            (r"\b(?:PAXOS|COIN CENTER|BITWISE|CHAINALYSIS|BITGO|FIREBLOCKS|TAXBIT|MESSARI)\b", None, "Documented crypto-native name"),
            (r"\b(?:STELLAR DEVELOPMENT FOUNDATION|AVA LABS|CELO FOUNDATION|PROTOCOL LABS|ELECTRIC COIN|HEDERA|CARDANO)\b", None, "Documented protocol/developer name"),
            (r"\b(?:OPENSEA|DAPPER LABS|MOONPAY|FTX|BLOCKFI|CELSIUS NETWORK)\b", None, "Documented exchange/application name"),
            (r"\b(?:POLYGON LABS|MYSTEN LABS|APTOS LABS|OFFCHAIN LABS|MATTER LABS|CHAINLINK LABS)\b", None, "Documented protocol lab"),
            (r"\bBLOCKCHAIN\b", None, "Generic blockchain name instrument"),
            (r"\bDIGITAL ASSETS?\b", None, "Generic digital-asset name instrument"),
            (r"\bWEB ?3\b", None, "Generic web3 name instrument"),
            (r"\bDEFI\b", None, "Generic DeFi name instrument"),
            (r"\bBITCOIN\b", None, "Generic bitcoin name instrument (added 2026-08-05: Athena Bitcoin, Bitcoin Association for BSV, Bitcoin Depot, Bitcoin Policy Institute)"),
            (r"\bSATOSHI\b", None, "Generic satoshi name instrument (added 2026-08-05: Satoshi Action Fund)"),
            (r"\bSTABLECOINS?\b", None, "Generic stablecoin name instrument"),
            (r"\bCRYPTOCURRENC(?:Y|IES)\b", None, "Generic cryptocurrency name instrument"),
            (r"\bCRYPTO\b", None, "Generic crypto name instrument"),
        ),
        "include_client_ids": {
            # 2026-08-05: Solana wallet maker files under the bare name "PHANTOM"
            # (BGR Government Affairs registrant, 2025-2026); the name is too
            # generic for a pattern, so the client ID is pinned directly.
            "66970": "PHANTOM",
        },
        "exclude_client_ids": {
            # Personal advocacy, including the verifier's high-fee clemency case.
            "63927": ("individual_personal_advocacy", "ROGER VER"),
            "64731": ("individual_personal_advocacy", "ROGER VER"),
            "68611": ("individual_personal_advocacy", "ROGER VER"),
            # Explicit non-sector collisions and diversified asset managers.
            "199793": ("nonsector_geographic_name_collision", "CITY OF SOLANA BEACH"),
            "199280": ("nonsector_name_collision", "PARADIGM PIPELINE"),
            "136018": ("nonsector_name_collision", "PARADIGM RESEARCH GROUP"),
            "57240": ("nonsector_name_collision", "PARADIGM SENIOR SERVICES"),
            "52179": ("nonsector_name_collision", "RIPPLE AND FLOW"),
            "68334": ("diversified_asset_manager_incidental_digital_assets", "WISDOMTREE"),
            "64684": ("diversified_asset_manager_incidental_digital_assets", "WISDOMTREE"),
        },
        "exclude_name_patterns": (
            (
                r"\b(?:BANK|BANCORP|BANCSHARES|PAYMENTS?|ASSET MANAGEMENT|INVESTMENT MANAGEMENT)\b.*\b(?:BLOCKCHAIN|CRYPTO|DIGITAL ASSETS?)\b",
                "diversified_finance_incidental_digital_asset_name",
            ),
        ),
    },
    "credit_union": {
        "patterns": (
            (r"\b(?:CUNA|NAFCU|AMERICA S CREDIT UNIONS)\b", "AMERICA'S CREDIT UNIONS", "CUNA/NAFCU/America's Credit Unions family"),
            (r"\bCREDIT UNIONS?\b", None, "Credit-union name"),
            (r"\bCU LEAGUE\b|\bLEAGUE\b.*\bCU\b", None, "CU league house rule"),
            (r"\bCUSO\b", None, "CUSO house rule"),
        ),
        "include_client_ids": {
            "56159": "AMERICA'S CREDIT UNIONS",
            "56184": "AMERICA'S CREDIT UNIONS",
            "110165": "AMERICA'S CREDIT UNIONS",
            "115381": "AMERICA'S CREDIT UNIONS",
            "192149": "AMERICA'S CREDIT UNIONS",
        },
        "exclude_client_ids": {},
        "exclude_name_patterns": (),
    },
    "bank": {
        "patterns": (
            (r"\bBANK OF AMERICA\b", "BANK OF AMERICA", "Bank of America family"),
            (r"\b(?:JPMORGAN|J P MORGAN|CHASE BANK)\b", "JPMORGAN CHASE", "JPMorgan Chase family"),
            (r"\b(?:CITIGROUP|CITIBANK)\b", "CITIGROUP", "Citigroup family"),
            (r"\bWELLS FARGO\b", "WELLS FARGO", "Wells Fargo family"),
            (r"\bGOLDMAN SACHS\b", "GOLDMAN SACHS", "Goldman Sachs family"),
            (r"\bMORGAN STANLEY\b", "MORGAN STANLEY", "Morgan Stanley family"),
            (r"\b(?:US|U S) BANCORP\b|\bUS BANK\b", "U.S. BANCORP", "U.S. Bancorp family"),
            (r"\bPNC (?:BANK|FINANCIAL)\b", "PNC", "PNC family"),
            (r"\bTRUIST\b", "TRUIST", "Truist family"),
            (r"\bCAPITAL ONE\b", "CAPITAL ONE", "Capital One family"),
            (r"\b(?:BNY MELLON|BANK OF NEW YORK MELLON)\b", "BNY MELLON", "BNY Mellon family"),
            (r"\bSTATE STREET\b", "STATE STREET", "State Street family"),
            (r"\bAMERICAN BANKERS ASSOCIATION\b", "AMERICAN BANKERS ASSOCIATION", "National trade association"),
            (r"\bBANK POLICY INSTITUTE\b", "BANK POLICY INSTITUTE", "National policy association"),
            (r"\bCONSUMER BANKERS ASSOCIATION\b", "CONSUMER BANKERS ASSOCIATION", "National trade association"),
            (r"\bINDEPENDENT COMMUNITY BANKERS\b", "INDEPENDENT COMMUNITY BANKERS OF AMERICA", "Community-bank association"),
            (r"\bBANKERS ASSOCIATION\b", None, "State/regional bankers association"),
            (r"\b(?:BANCORP|BANCSHARES)\b", None, "Bank holding-company name"),
            (r"\b(?:NATIONAL|FEDERAL|COMMUNITY|COMMERCIAL|SAVINGS|TRUST) BANK\b", None, "Chartered-bank name"),
            (r"\bBANK (?:NA|N A|CORP|CORPORATION|INC|LLC|LTD)\b", None, "Bank legal name"),
            (r"\bBANKS?\b", None, "Generic bank name instrument"),
        ),
        "include_client_ids": {},
        "exclude_client_ids": {},
        "exclude_name_patterns": (
            # Tightened 2026-08-05 (Paul: "lets tighten up the banks").  The
            # generic \bBANKS?\b instrument needs these guards: biological/
            # charitable "banks" incl. plural forms, multilateral development
            # banks (commercial foreign banks like MUFG/Habib stay IN - the
            # bright line is development/policy vs commercial), and non-bank
            # name collisions (a law firm's partner surname, a clothing
            # retailer).
            (
                r"\b(?:FOOD|BLOOD|SEED|GENE|WORLD|LAND|TISSUE|DIAPER|DATA) BANKS?\b",
                "noncommercial_bank_name",
            ),
            (r"\bBANK (?:INFORMATION CENTER|STREET COLLEGE)\b", "noncommercial_bank_name"),
            (
                r"\bDEVELOPMENT BANK\b|\bBANK (?:FOR|OF) (?:ECONOMIC INTEGRATION|RECONSTRUCTION)\b",
                "multilateral_development_bank",
            ),
            (
                r"\bKATZ (?:BANKS KUMIN|MARSHALL BANKS)\b|\bCHRISTOPHER AND BANKS\b",
                "nonbank_name_collision",
            ),
        ),
    },
    "fintech": {
        "patterns": (
            (r"\b(?:PAYPAL|VENMO)\b", "PAYPAL / VENMO", "PayPal family"),
            (r"\bSTRIPE\b", "STRIPE", "Stripe family"),
            (r"\bPLAID\b", "PLAID", "Plaid family"),
            (r"\b(?:BLOCK INC|SQUARE INC|CASH APP)\b", "BLOCK / SQUARE / CASH APP", "Block family"),
            (r"\b(?:VISA|MASTERCARD)\b", None, "Card-network name"),
            (r"\bCHIME\b", "CHIME", "Chime family"),
            (r"\bKLARNA\b", "KLARNA", "Klarna family"),
            (r"\bAFFIRM\b", "AFFIRM", "Affirm family"),
            (r"\bBREX\b", "BREX", "Brex family"),
            (r"\bSOFI\b", "SOFI", "SoFi family"),
            (r"\bROBINHOOD\b", "ROBINHOOD", "Robinhood family"),
            (r"\bINTUIT\b", "INTUIT", "Intuit family"),
            # -- Additions 2026-08-05: sector-completeness research (web sources +
            #    full-corpus cross-check; every name below is a verified LDA client
            #    2021-2026 that no prior pattern matched). --
            (r"\bELECTRONIC TRANSACTIONS ASSOCIATION\b", "ELECTRONIC TRANSACTIONS ASSOCIATION", "Payments-industry trade association (ETA)"),
            (r"\bINNOVATIVE PAYMENTS ASSOCIATION\b", "INNOVATIVE PAYMENTS ASSOCIATION", "Prepaid/payments trade association"),
            (r"\bELECTRONIC PAYMENTS COALITION\b", "ELECTRONIC PAYMENTS COALITION", "Card-payments coalition (interchange defense)"),
            (r"\bNACHA\b", "NACHA", "ACH standards body; in-house filer"),
            (r"\bWESTERN UNION\b", "WESTERN UNION", "Money-transfer company"),
            (r"\bMONEYGRAM\b", "MONEYGRAM", "Money-transfer company"),
            (r"^WISE$|\bWISE US INC\b|\bTRANSFERWISE\b", "WISE", "Cross-border payments (fka TransferWise)"),
            (r"\bEURONET\b", "EURONET", "Payments company (Ria/epay), 2021-2024"),
            (r"\bDAILYPAY\b", "DAILYPAY", "Earned-wage-access provider"),
            (r"\b(?:EARNIN|ACTIVEHOURS)\b", "EARNIN", "Earned-wage-access provider (Activehours Inc dba EarnIn)"),
            (r"\bPAYACTIV\b", "PAYACTIV", "Earned-wage-access provider"),
            (r"\bZAYZOON\b", "ZAYZOON", "Earned-wage-access provider"),
            (r"\bDAVE INC\b", "DAVE", "Neobank/earned-wage-access app"),
            (r"\bZIP CO\b", "ZIP CO", "Buy-now-pay-later provider"),
            (r"\bSEZZLE\b", "SEZZLE", "Buy-now-pay-later provider"),
            (r"\bAFTERPAY\b", "AFTERPAY", "Buy-now-pay-later (Block subsidiary; filed separately 2021-2022)"),
            (r"\bENOVA\b", "ENOVA", "Online lender (OnDeck/CashNetUSA)"),
            (r"\b(?:OPPFI|OPPORTUNITY FINANCIAL)\b", "OPPFI", "Fintech installment lender"),
            (r"\bLENDING TREE\b", "LENDINGTREE", "Online loan marketplace"),
            (r"\bBILL COM\b", "BILL.COM", "B2B payments platform"),
            (r"^GUSTO$|\bZENPAYROLL\b", "GUSTO", "Payroll fintech"),
            (r"^DEEL(?: INC)?$", "DEEL", "Global payroll fintech"),
            (r"\bRAMP BUSINESS\b", "RAMP", "Corporate-card fintech"),
            (r"\bMERCURY TECHNOLOGIES\b", "MERCURY", "Startup-banking fintech"),
            (r"\bWEBULL\b", "WEBULL", "Retail brokerage app"),
            (r"\bPUBLIC HOLDINGS INC\b", "PUBLIC.COM", "Retail investing app, 2021-2022"),
            (r"^BETTERMENT\b", "BETTERMENT", "Robo-advisor"),
            (r"\bACORNS\b", "ACORNS", "Micro-investing app"),
            (r"\bGREEN DOT (?:CORPORATION|BANK|CORP)\b", "GREEN DOT", "Prepaid/banking-as-a-service fintech, 2021-2023"),
            (r"\bSOCURE\b", "SOCURE", "Identity-verification fintech"),
            (r"^TOAST(?: INC)?$", "TOAST", "Restaurant payments/POS"),
            (r"\bWEX\b", "WEX", "Payments (fleet cards/benefits; WEX Inc + WEX Health)"),
            (r"\bACI WORLDWIDE\b", "ACI WORLDWIDE", "Payments software, 2022"),
            (r"\bPAYSAFE\b", "PAYSAFE", "Payments company"),
            (r"\bSOLO FUNDS\b", "SOLO FUNDS", "P2P lending fintech, 2021-2023"),
            (r"\b(?:ZESTFINANCE|ZEST AI)\b", "ZEST AI", "AI credit-underwriting fintech"),
            (r"\b(?:ADYEN|MARQETA|CHECKOUT COM)\b", None, "Payments infrastructure name"),
            (r"\b(?:FISERV|WORLDPAY|GLOBAL PAYMENTS|REMITLY|MONEYLION|LENDINGCLUB|LENDING CLUB|KABBAGE|EARLY WARNING SERVICES|ZELLE)\b", None, "Documented fintech/payments name; LENDING CLUB spaced form added 2026-08-05"),
            (r"\bFINTECH\b|\bFINANCIAL TECHNOLOG(?:Y|IES)\b", None, "Generic fintech name instrument"),
            (r"\bBUY NOW PAY LATER\b", None, "BNPL name instrument"),
            (r"\bPAYMENTS? (?:NETWORK|PLATFORM|TECHNOLOG(?:Y|IES)|SOLUTIONS?)\b", None, "Payments-technology name instrument"),
        ),
        "include_client_ids": {},
        "exclude_client_ids": {},
        "exclude_name_patterns": (
            # Tightened 2026-08-06 (post-audit robustness pass; the bank
            # guard's 2026-08-05 parallel).  Obvious non-fintech token
            # collisions only: a passport service caught by the VISA token,
            # a hospital-CIO college caught by CHIME, a glass-block maker
            # caught by BLOCK INC, and an art museum caught by INTUIT.
            # The legitimate families (Chime Financial, Visa, Block Inc,
            # Intuit Inc) must keep matching - patterns anchor on the
            # collider's own distinctive phrase, never the shared token.
            (r"\bITSEASY\b", "nonsector_name_collision"),
            (
                r"\bCOLLEGE OF HEALTHCARE INFORMATION MANAGEMENT EXECUTIVES\b",
                "nonsector_name_collision",
            ),
            (r"\bGLASS BLOCK\b", "nonsector_name_collision"),
            (
                r"\bCENTER FOR INTUITIVE AND OUTSIDER ART\b",
                "nonsector_name_collision",
            ),
        ),
    },
    # Added 2026-10-07 (Paul: "Full boards, treat it exactly like fintech").
    # Seventeen pinned families: eleven operating companies and six trade
    # associations.  Patterns run against the NORMALIZED name (upper case,
    # "&" -> " AND ", punctuation -> space), so "AT&T" is "AT AND T",
    # "T-Mobile" is "T MOBILE" and "U.S. Cellular" is "U S CELLULAR".  Every
    # include anchors on the family's own distinctive phrase - never on a
    # shared token such as SPRINT, CHARTER, SPECTRUM, COX or FRONTIER - and the
    # exclusions below name the known collisions found in the 2021-2026 LDA
    # client audit (charter schools, Spectrum Health/Brands, Frontier Airlines,
    # Cox Automotive/Health, Optimum Nutrition, Lumen Learning/Bioscience, the
    # United States Telecommunications Training Institute).
    "telecom": {
        "patterns": (
            (r"\b(?:VERIZON|CELLCO)\b", "VERIZON", "Verizon family incl. Cellco Partnership"),
            (r"\bAT AND T\b|\bATT SERVICES\b|^ATT (?:INC|CORP|CORPORATION)$", "AT&T", "AT&T family; normalized AT AND T, never a bare ATT token"),
            (r"\bT MOBILE\b|\bTMOBILE\b", "T-MOBILE", "T-Mobile family"),
            (r"\bMETRO ?PCS\b", "T-MOBILE", "MetroPCS (T-Mobile subsidiary)"),
            (r"^SPRINT(?: INC| CORP| CORPORATION| LLC)?$|\bSPRINT (?:COMMUNICATIONS|NEXTEL|SPECTRUM|SOLUTIONS|WIRELESS)\b", "T-MOBILE", "Sprint (T-Mobile 2020 merger); bare SPRINT only as the whole name"),
            (r"\bCOMCAST\b|\bNBC ?UNIVERSAL\b", "COMCAST", "Comcast family incl. NBCUniversal"),
            (r"\bCHARTER COMMUNICATIONS\b|\bTIME WARNER CABLE\b|\bCHARTER SPECTRUM\b", "CHARTER COMMUNICATIONS", "Charter Communications/Spectrum/Time Warner Cable; the full name, so press matching never sees a bare CHARTER (bank charter)"),
            (r"^(?=.*\b(?:CHARTER|CABLE)\b).*\bSPECTRUM\b", "CHARTER COMMUNICATIONS", "Spectrum brand only alongside CHARTER or CABLE"),
            (r"\bLUMEN TECHNOLOGIES\b|^LUMEN(?: INC| LLC| CORP| CORPORATION)?$|\bCENTURY ?LINK\b|\bLEVEL ?3 (?:COMMUNICATIONS|PARENT|NETWORKS|TELECOM)\b|\bQWEST\b", "LUMEN", "Lumen/CenturyLink/Level 3/Qwest"),
            (r"\bCOX (?:ENTERPRISES|COMMUNICATIONS)\b", "COX", "Cox Enterprises/Cox Communications"),
            (r"\bALTICE\b|\bCABLEVISION\b|\bOPTIMUM (?:COMMUNICATIONS|CABLE|MOBILE|WIRELESS)\b|^OPTIMUM$", "ALTICE USA", "Altice USA/Optimum/Cablevision"),
            (r"\bFRONTIER COMMUNICATIONS\b", "FRONTIER COMMUNICATIONS", "Frontier Communications (incl. Citizens Telecom d/b/a)"),
            (r"^DISH$|\bDISH (?:NETWORK|WIRELESS|DBS|PURCHASING)\b|\bECHOSTAR\b", "DISH / ECHOSTAR", "DISH Network/EchoStar"),
            (r"\b(?:U S|US|UNITED STATES) CELLULAR\b|\bTELEPHONE AND DATA SYSTEMS\b|\bTDS TELECOM(?:MUNICATIONS)?\b", "US CELLULAR / TDS", "U.S. Cellular/Telephone and Data Systems"),
            (r"\bCTIA\b|\bTHE WIRELESS ASSOCIATION\b", "CTIA", "Wireless trade association (also the CITA misspelling)"),
            (r"\bNCTA\b|\bINTERNET AND TELEVISION ASS(?:OCIATION|OC|N)\b|\b(?:NATIONAL|NTL) CABLE (?:AND )?TELECOMMUNICATIONS ASS(?:OCIATION|OC|N)\b", "NCTA", "Cable trade association, all three of its names"),
            (r"\bU ?S ?TELECOM\b|\bUNITED STATES TELECOM(?:MUNICATIONS)? ASS(?:OCIATION|OC|N)\b", "USTELECOM", "USTelecom, filed as United States Telecom Association"),
            (r"\bCOMPETITIVE CARRIERS ASSOCIATION\b", "COMPETITIVE CARRIERS ASSOCIATION", "Regional/rural wireless carriers' association"),
            (r"\bNTCA\b|\bRURAL BROADBAND ASS(?:OCIATION|OC|N)\b|\bNATIONAL TELE(?:COMMUNICATIONS|PHONE) COOPERATIVE ASS(?:OCIATION|OC|N)\b", "NTCA", "NTCA - The Rural Broadband Association, also filed under its full legal names (current and pre-rename)"),
            (r"\bWISPA\b|\bWIRELESS INTERNET SERVICES? PROVIDERS? ASS(?:OCIATION|OC|N)\b", "WISPA", "Wireless ISP association"),
        ),
        # No client IDs are pinned yet: the audit run keyed on names only.
        "include_client_ids": {},
        "exclude_client_ids": {},
        "exclude_name_patterns": (
            (r"\bCHARTER (?:SCHOOLS?|BROKERAGE|CASTING|SPACE|INSTITUTE)\b", "nonsector_name_collision"),
            (r"\bSPECTRUM (?:HEALTH|HEALTHCARE|BRANDS|PHARMACEUTICALS|PHOTONICS)\b", "nonsector_name_collision"),
            (r"\bLUMEN (?:LEARNING|BIOSCIENCE)\b", "nonsector_name_collision"),
            (r"\bCOX (?:AUTOMOTIVE|HEALTH|MEDIA GROUP)\b", "nonsector_name_collision"),
            (r"\bOPTIMUM (?:HEALTH|NUTRITION)\b", "nonsector_name_collision"),
            (r"\bFRONTIER (?:AIRLINES|OIL)\b", "nonsector_name_collision"),
            (r"\bTELECOMMUNICATIONS TRAINING INSTITUTE\b", "nonsector_name_collision"),
        ),
    },
}

DEFAULT_SECTORS = tuple(ROSTERS)

FINANCIAL_SERIES = {
    "income_total": "Fees paid to outside lobbying registrants",
    "expenses_total": "Self-reported in-house lobbying expenses",
}
FORBIDDEN_COMBINED_KEYS = {
    "combined_total",
    "income_plus_expenses",
    "lobbying_spend_total",
    "spend_total",
    "total_spend",
}


def normalize_name(value):
    """Return the punctuation-stable uppercase form used by every roster."""
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("&", " AND ").replace("\u2019", "'").replace("`", "'")
    text = re.sub(r"[^A-Z0-9']+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


_RESOLVER = None


def resolver():
    """Process-cached canonical entity resolver (see fec/lobbying/entity_identity.py)."""
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = entity_identity.default_resolver()
    return _RESOLVER


def entity_aliases(raw_name, as_of=None):
    """Every normalized name of the entity this filing name belongs to.

    Sector membership is tested against these as well as the collapsed name, so
    that an organization whose CURRENT name lost the sector's keyword is still
    recognised by the name it is on record under.  SVB Financial Group is the
    worked example: the holding-company name carries no BANK token, so the
    pinned bank patterns never saw it.
    """
    if not ENTITY_IDENTITY:
        return ()
    record = resolver().resolve(raw_name, as_of=as_of)
    if not record["resolved"]:
        return ()
    entity = resolver().entities[record["entity_id"]]
    names = {name["normalized"] for name in entity["names"]}
    names.add(normalize_name(record["display_name"]))
    base = resolver().entities.get(record["base_entity_id"])
    if base is not None:
        names.update(name["normalized"] for name in base["names"])
    return tuple(sorted(names))


def canonicalize_client_name(value, as_of=None):
    """Collapse on-behalf-of and DBA forms, returning family and audit rule.

    The canonical identity table is consulted FIRST and on the full raw name,
    because the DBA collapse below is lossy: it would reduce both "CREDIT UNION
    NATIONAL ASSOCIATION, INC. DBA AMERICA'S CREDIT UNIONS" and "CREDIT UNION
    NATIONAL ASSOC INC. DBA AMERICA'S CREDIT UNIONS (FORMERLY NAFCU)" to the
    same brand string, erasing the fact that they are two different filers'
    records and that they were two different organizations before 2024.
    """
    normalized = normalize_name(value)
    if ENTITY_IDENTITY:
        record = resolver().resolve(value, as_of=as_of)
        if record["resolved"]:
            return normalize_name(record["display_name"]), "entity_identity"
    on_behalf = re.search(r"\b(?:ON BEHALF OF|O B O)\b\s+(.+)$", normalized)
    if on_behalf:
        family = on_behalf.group(1).strip()
        return _canonical_family_alias(_strip_formerly(family)), "on_behalf_of_to_principal"
    dba = re.search(r"\b(?:D B A|DBA)\b\s+(.+)$", normalized)
    if dba:
        family = dba.group(1).strip()
        return _canonical_family_alias(_strip_formerly(family)), "dba_to_brand"
    return _canonical_family_alias(_strip_formerly(normalized)), "normalized_name"


def _strip_formerly(value):
    value = re.sub(r"\bFORMERLY(?: KNOWN AS)?\b.*$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _canonical_family_alias(value):
    """Legacy token-level alias, retained only for the ENTITY_IDENTITY=0 path.

    This rule collapsed ANY name containing the token CUNA or NAFCU into
    America's Credit Unions.  That silently folded CUNA Mutual Group / TruStage
    -- a credit-union-owned insurer, not the trade association -- into the
    trade association's family.  The identity table replaces it.
    """
    if ENTITY_IDENTITY:
        return value
    if re.search(r"\b(?:CUNA|NAFCU|AMERICAS CREDIT UNIONS|AMERICA'S CREDIT UNIONS)\b", value):
        return "AMERICA'S CREDIT UNIONS"
    return value


def _client_id(filing):
    value = (filing.get("client") or {}).get("id")
    return "" if value is None else str(value).strip()


def _entity_key(entity, role):
    entity = entity or {}
    identifier = entity.get("id")
    if identifier is not None and str(identifier).strip():
        return ("id", str(identifier).strip()), False
    name = normalize_name(entity.get("name"))
    if not name:
        raise SectorSpendError(f"filing lacks both {role}.id and {role}.name")
    return ("name", name), True


def _money(value, field):
    if value is None or str(value).strip() == "":
        return Decimal("0")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SectorSpendError(f"invalid {field} amount {value!r}") from exc
    if not amount.is_finite():
        raise SectorSpendError(f"non-finite {field} amount {value!r}")
    return amount


def _json_number(value):
    value = value.quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _timestamp_key(value, filing_uuid):
    if value is None or not str(value).strip():
        return (0, "", str(filing_uuid)), "missing"
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        stamp = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError:
        return (0, "", str(filing_uuid)), "invalid"
    return (1, stamp, str(filing_uuid)), None


def _has_financial_field(filing, field):
    value = filing.get(field)
    return value is not None and str(value).strip() != ""


def _type_is_registration(displays):
    return any("REGISTRATION" in normalize_name(display) for display in displays)


def _type_is_report(displays):
    return any("REPORT" in normalize_name(display) for display in displays)


def inspect_filings(filings):
    """Receipt first-occurrence UUID dedupe and derive nonfinancial types."""
    seen = set()
    raw_count = 0
    duplicates = 0
    type_stats = {}
    for filing in filings:
        raw_count += 1
        filing_uuid = str(filing.get("filing_uuid") or "").strip()
        if not filing_uuid:
            raise SectorSpendError("filing without filing_uuid")
        if filing_uuid in seen:
            duplicates += 1
            continue
        seen.add(filing_uuid)
        filing_type = str(filing.get("filing_type") or "").strip()
        display = str(filing.get("filing_type_display") or "").strip()
        if not filing_type or not display:
            raise SectorSpendError(
                f"filing {filing_uuid} lacks filing_type or filing_type_display"
            )
        stats = type_stats.setdefault(
            filing_type,
            {"count": 0, "displays": set(), "income_present": 0, "expenses_present": 0},
        )
        stats["count"] += 1
        stats["displays"].add(display)
        stats["income_present"] += int(_has_financial_field(filing, "income"))
        stats["expenses_present"] += int(_has_financial_field(filing, "expenses"))

    excluded = {}
    for filing_type in sorted(type_stats):
        stats = type_stats[filing_type]
        displays = stats["displays"]
        no_financial_fields = not (stats["income_present"] or stats["expenses_present"])
        if _type_is_registration(displays):
            reason = "filing_type_display_identifies_registration"
        elif no_financial_fields and not _type_is_report(displays):
            reason = "type_has_no_financial_fields_and_display_is_not_a_report"
        else:
            continue
        excluded[filing_type] = reason

    excluded_rows = []
    for filing_type in sorted(excluded):
        stats = type_stats[filing_type]
        excluded_rows.append(
            {
                "filing_type": filing_type,
                "filing_type_displays": sorted(stats["displays"]),
                "records_excluded": stats["count"],
                "income_fields_present": stats["income_present"],
                "expenses_fields_present": stats["expenses_present"],
                "reason": excluded[filing_type],
            }
        )
    receipts = {
        "deduplication": {
            "rule": "filing_uuid; first occurrence in year/page/result order wins",
            "raw_filings": raw_count,
            "unique_filing_uuids": len(seen),
            "pagination_duplicates_removed": duplicates,
        },
        "filing_type_exclusions": {
            "derivation_rule": (
                "Use filing_type_display: registrations are excluded; an observed "
                "non-report type is also excluded when every deduped record has both "
                "income and expenses absent. Filing-type codes are not hardcoded."
            ),
            "records_excluded": sum(row["records_excluded"] for row in excluded_rows),
            "types": excluded_rows,
        },
    }
    return excluded, receipts


def _match_client(filing, sectors):
    client = filing.get("client") or {}
    raw_name = str(client.get("name") or "").strip()
    if not raw_name:
        raise SectorSpendError(f"filing {filing.get('filing_uuid')} lacks client.name")
    # Identity is resolved AS OF the filing year, so a predecessor stays itself
    # before a merger boundary and becomes the successor after it.
    as_of = int(filing.get("filing_year"))
    normalized = normalize_name(raw_name)
    collapsed, collapse_rule = canonicalize_client_name(raw_name, as_of=as_of)
    aliases = entity_aliases(raw_name, as_of=as_of)
    targets = (collapsed,) + tuple(alias for alias in aliases if alias != collapsed)
    client_id = _client_id(filing)
    assignments = []
    exclusions = []
    for sector in sectors:
        roster = ROSTERS[sector]
        explicit_exclusion = roster["exclude_client_ids"].get(client_id)
        if explicit_exclusion:
            reason, family = explicit_exclusion
            exclusions.append(
                {
                    "sector": sector,
                    "reason": reason,
                    "canonical_family": family,
                    "client_id": client_id,
                    "raw_name": raw_name,
                    "normalized_name": normalized,
                    "collapse_rule": collapse_rule,
                    "is_individual": reason == "individual_personal_advocacy",
                }
            )
            continue
        included_family = roster["include_client_ids"].get(client_id)
        if included_family:
            # A pinned client-ID include names the family directly, but the
            # identity table still decides WHICH entity that is in this year:
            # ids 56159/56184/110165/115381/192149 are CUNA and NAFCU records,
            # which were two organizations before 2024 and one after.
            family = collapsed if ENTITY_IDENTITY and collapse_rule == "entity_identity" else included_family
            assignments.append(
                {
                    "sector": sector,
                    "canonical_family": family,
                    "matched_pattern": f"CLIENT_ID:{client_id}",
                    "client_id": client_id,
                    "raw_name": raw_name,
                    "normalized_name": normalized,
                    "collapse_rule": collapse_rule,
                    "bank_tier": _bank_tier_for(sector, raw_name, as_of),
                }
            )
            continue
        structural_exclusion = None
        for pattern, reason in roster["exclude_name_patterns"]:
            if re.search(pattern, collapsed):
                structural_exclusion = (pattern, reason)
                break
        if structural_exclusion:
            pattern, reason = structural_exclusion
            exclusions.append(
                {
                    "sector": sector,
                    "reason": reason,
                    "canonical_family": collapsed,
                    "client_id": client_id,
                    "raw_name": raw_name,
                    "normalized_name": normalized,
                    "collapse_rule": collapse_rule,
                    "matched_exclusion_pattern": pattern,
                    "is_individual": False,
                }
            )
            continue
        matched = None
        for pattern, fixed_family, _documentation in roster["patterns"]:
            for index, target in enumerate(targets):
                if re.search(pattern, target):
                    matched = (pattern, fixed_family, index)
                    break
            if matched:
                break
        if matched:
            pattern, fixed_family, target_index = matched
            # A fixed family is a pinned label; the identity table outranks it
            # only when it actually resolved this filer.
            if ENTITY_IDENTITY and collapse_rule == "entity_identity":
                family = collapsed
            else:
                family = fixed_family or collapsed
            assignments.append(
                {
                    "sector": sector,
                    "canonical_family": family,
                    "matched_pattern": pattern,
                    "matched_via": "collapsed_name" if target_index == 0 else "entity_alias",
                    "client_id": client_id,
                    "raw_name": raw_name,
                    "normalized_name": normalized,
                    "collapse_rule": collapse_rule,
                    "bank_tier": _bank_tier_for(sector, raw_name, as_of),
                }
            )
    return assignments, exclusions


def scope_sector_matches(filing, sectors=DEFAULT_SECTORS):
    """Sector names matching one filing's client, with full roster semantics.

    Public hook for the LDA full-scope rebuild's scope construction: roster
    patterns plus pinned client-ID includes MINUS pinned and structural
    exclusions, with entity identity resolved as of the filing year.  Returns
    a sorted tuple of sector names (possibly empty).
    """
    assignments, _exclusions = _match_client(filing, sectors)
    return tuple(sorted({assignment["sector"] for assignment in assignments}))


def scope_sector_disposition(filing, sectors=DEFAULT_SECTORS):
    """Return 'classified', 'excluded', or None for one filing's client.

    Public hook for the financial_other discovery channel: 'classified'
    clients already flow into the full-scope universe, 'excluded' clients were
    deliberately rejected by the roster, and None means the sector authority
    has no opinion.
    """
    assignments, exclusions = _match_client(filing, sectors)
    if assignments:
        return "classified"
    if exclusions:
        return "excluded"
    return None


def scope_classification_exclusion(client_name):
    """Bank-guard reason when a finance-classification name is a known FP.

    Used by the LDA full-scope union so LobbyView-era classification rows obey
    the same exclusion policy as the roster: development banks, noncommercial
    banks, and non-bank name collisions are not finance organizations.
    Returns the exclusion reason string, or None when the name is clean.
    """
    normalized = normalize_name(client_name)
    for pattern, reason in ROSTERS["bank"]["exclude_name_patterns"]:
        if re.search(pattern, normalized):
            return reason
    return None


def _bank_tier_for(sector, raw_name, as_of):
    """Label bank-sector organizations big / community / mixed / unclassified.

    A separate axis from sector membership: this never adds or removes an
    organization from `bank`, it only labels the one already there.  Returns
    None for every other sector.
    """
    if sector != "bank" or not ENTITY_IDENTITY:
        return None
    return entity_identity.bank_tier(raw_name, as_of=as_of, resolver=resolver())


def supersede_filings(filings, excluded_types, sectors=DEFAULT_SECTORS):
    """Dedupe, exclude nonfinancial types, and keep the latest group filing."""
    sectors = tuple(sectors)
    winners = {}
    seen = set()
    eligible = 0
    excluded_seen = defaultdict(int)
    fallback = defaultdict(int)
    registrant_name_fallbacks = 0
    client_name_fallbacks = 0

    for filing in filings:
        filing_uuid = str(filing.get("filing_uuid") or "").strip()
        if not filing_uuid:
            raise SectorSpendError("filing without filing_uuid")
        if filing_uuid in seen:
            continue
        seen.add(filing_uuid)
        filing_type = str(filing.get("filing_type") or "").strip()
        if filing_type in excluded_types:
            excluded_seen[filing_type] += 1
            continue
        year = int(filing.get("filing_year"))
        period = str(filing.get("filing_period") or "").strip()
        if period not in PERIOD_TO_QUARTER:
            raise SectorSpendError(
                f"filing {filing_uuid} has unknown filing_period {period!r}"
            )
        registrant_key, registrant_fallback = _entity_key(
            filing.get("registrant"), "registrant"
        )
        client_key, client_fallback = _entity_key(filing.get("client"), "client")
        registrant_name_fallbacks += int(registrant_fallback)
        client_name_fallbacks += int(client_fallback)
        group = (registrant_key, client_key, year, period)
        selection_key, fallback_reason = _timestamp_key(
            filing.get("dt_posted"), filing_uuid
        )
        if fallback_reason:
            fallback[fallback_reason] += 1
        assignments, exclusions = _match_client(filing, sectors)
        retained_filing = filing if assignments or exclusions else None
        candidate = {
            "selection_key": selection_key,
            "filing": retained_filing,
            "assignments": assignments,
            "exclusions": exclusions,
        }
        eligible += 1
        previous = winners.get(group)
        if previous is None or selection_key > previous["selection_key"]:
            winners[group] = candidate

    all_missing_groups = sum(
        1 for value in winners.values() if value["selection_key"][0] == 0
    )
    receipt = {
        "group_key": "(registrant.id, client.id, filing_year, filing_period)",
        "selection_rule": "latest parseable dt_posted; filing_uuid lexical maximum breaks exact timestamp ties",
        "missing_dt_fallback_rule": (
            "A parseable dt_posted always outranks a missing/invalid value; when an "
            "entire group lacks one, the lexicographically greatest filing_uuid wins."
        ),
        "eligible_records": eligible,
        "groups_retained": len(winners),
        "records_superseded": eligible - len(winners),
        "dt_posted_fallback_frequency": {
            "records_missing": fallback["missing"],
            "records_invalid": fallback["invalid"],
            "groups_won_by_filing_uuid_fallback": all_missing_groups,
        },
        "missing_group_id_fallback_frequency": {
            "registrant_name_used": registrant_name_fallbacks,
            "client_name_used": client_name_fallbacks,
        },
    }
    retained = [value for value in winners.values() if value["filing"] is not None]
    return retained, receipt, dict(sorted(excluded_seen.items()))


def _lobbyist_keys(filing):
    keys = set()
    for activity in filing.get("lobbying_activities") or ():
        for item in (activity or {}).get("lobbyists") or ():
            lobbyist = (item or {}).get("lobbyist") or {}
            first = normalize_name(lobbyist.get("first_name"))
            last = normalize_name(lobbyist.get("last_name"))
            if not (first or last):
                continue
            identifier = lobbyist.get("id")
            if identifier is not None and str(identifier).strip():
                keys.add(("id", str(identifier).strip()))
            else:
                keys.add(("name", first, last))
    return keys


def _bank_tier_breakdown(items):
    """Per-tier organization counts and the two financial series, kept apart.

    ``defaulted_*`` isolates the share that landed in `community` by the
    long-tail default rather than by positive identification.  That number
    bounds how much of the community series is inference rather than evidence,
    and it must travel with the series wherever the series goes.
    """
    tiers = {}
    for tier in entity_identity.BANK_TIERS:
        tiers[tier] = {
            "families": set(),
            "defaulted_families": set(),
            "filings": 0,
            "income_total": Decimal("0"),
            "expenses_total": Decimal("0"),
            "defaulted_income_total": Decimal("0"),
            "defaulted_expenses_total": Decimal("0"),
            "by_basis": defaultdict(
                lambda: {
                    "families": set(),
                    "filings": 0,
                    "income_total": Decimal("0"),
                    "expenses_total": Decimal("0"),
                }
            ),
        }
    for filing, assignment in items:
        label = assignment.get("bank_tier") or {}
        tier = label.get("tier")
        if tier not in tiers:
            continue
        row = tiers[tier]
        income = _money(filing.get("income"), "income")
        expenses = _money(filing.get("expenses"), "expenses")
        family = assignment["canonical_family"]
        row["families"].add(family)
        row["filings"] += 1
        row["income_total"] += income
        row["expenses_total"] += expenses
        # The evidence ladder: WHY this organization carries this tier.
        basis = row["by_basis"][label.get("basis") or "unknown"]
        basis["families"].add(family)
        basis["filings"] += 1
        basis["income_total"] += income
        basis["expenses_total"] += expenses
        if label.get("defaulted"):
            row["defaulted_families"].add(family)
            row["defaulted_income_total"] += income
            row["defaulted_expenses_total"] += expenses
    output = {}
    for tier in entity_identity.BANK_TIERS:
        row = tiers[tier]
        output[tier] = {
            "distinct_organizations": len(row["families"]),
            "filings": row["filings"],
            "income_total": _json_number(row["income_total"]),
            "expenses_total": _json_number(row["expenses_total"]),
            "defaulted_organizations": len(row["defaulted_families"]),
            "defaulted_income_total": _json_number(row["defaulted_income_total"]),
            "defaulted_expenses_total": _json_number(row["defaulted_expenses_total"]),
            "by_basis": {
                basis: {
                    "distinct_organizations": len(payload["families"]),
                    "filings": payload["filings"],
                    "income_total": _json_number(payload["income_total"]),
                    "expenses_total": _json_number(payload["expenses_total"]),
                }
                for basis, payload in sorted(row["by_basis"].items())
            },
        }
    return output


def _metric_record(items, include_bank_tier=False):
    families = set()
    registrants = set()
    lobbyists = set()
    income_by_family = defaultdict(lambda: Decimal("0"))
    income_total = Decimal("0")
    expenses_total = Decimal("0")
    for filing, assignment in items:
        family = assignment["canonical_family"]
        families.add(family)
        registrant, _fallback = _entity_key(filing.get("registrant"), "registrant")
        registrants.add(registrant)
        lobbyists.update(_lobbyist_keys(filing))
        income = _money(filing.get("income"), "income")
        expenses = _money(filing.get("expenses"), "expenses")
        income_total += income
        expenses_total += expenses
        income_by_family[family] += income

    concentration = _income_concentration(income_by_family, income_total)
    record = {
        "distinct_organizations": len(families),
        "filings": len(items),
        "income_total": _json_number(income_total),
        "expenses_total": _json_number(expenses_total),
        "distinct_registrants": len(registrants),
        "distinct_named_lobbyists": len(lobbyists),
        "income_concentration": concentration,
    }
    if include_bank_tier:
        record["bank_tier"] = _bank_tier_breakdown(items)
    return record


def _income_concentration(income_by_family, total):
    if total <= 0:
        return {
            "top_1_share": None,
            "top_3_share": None,
            "top_10_share": None,
            "hhi": None,
        }
    amounts = sorted(income_by_family.values(), reverse=True)

    def share(limit):
        return float(sum(amounts[:limit], Decimal("0")) / total)

    shares = [amount / total for amount in amounts]
    return {
        "top_1_share": share(1),
        "top_3_share": share(3),
        "top_10_share": share(10),
        "hhi": float(sum((value * value for value in shares), Decimal("0"))),
    }


def _build_roster_receipt(winners, sectors):
    families = {sector: {} for sector in sectors}
    exclusion_rows = {}
    individual_rows = {}
    sector_items = {sector: [] for sector in sectors}
    for winner in winners:
        filing = winner["filing"]
        if filing is None:
            continue
        year = int(filing["filing_year"])
        for assignment in winner["assignments"]:
            sector = assignment["sector"]
            sector_items[sector].append((filing, assignment))
            key = assignment["canonical_family"]
            row = families[sector].setdefault(
                key,
                {
                    "sector": sector,
                    "canonical_family": key,
                    "raw_name_variants": set(),
                    "normalized_name_variants": set(),
                    "matched_patterns": set(),
                    "client_ids": set(),
                    "collapse_rules": set(),
                    "bank_tiers": set(),
                    "first_year_seen": year,
                    "last_year_seen": year,
                },
            )
            row["raw_name_variants"].add(assignment["raw_name"])
            row["normalized_name_variants"].add(assignment["normalized_name"])
            row["matched_patterns"].add(assignment["matched_pattern"])
            if assignment["client_id"]:
                row["client_ids"].add(assignment["client_id"])
            row["collapse_rules"].add(assignment["collapse_rule"])
            tier = assignment.get("bank_tier")
            if tier:
                row["bank_tiers"].add(
                    tier["tier"] + (":defaulted" if tier.get("defaulted") else "")
                )
            row["first_year_seen"] = min(row["first_year_seen"], year)
            row["last_year_seen"] = max(row["last_year_seen"], year)

        for exclusion in winner["exclusions"]:
            key = (
                exclusion["sector"],
                exclusion["reason"],
                exclusion["canonical_family"],
            )
            row = exclusion_rows.setdefault(
                key,
                {
                    "sector": exclusion["sector"],
                    "reason": exclusion["reason"],
                    "canonical_family": exclusion["canonical_family"],
                    "client_ids": set(),
                    "raw_name_variants": set(),
                    "filings": 0,
                    "income_total": Decimal("0"),
                    "expenses_total": Decimal("0"),
                    "first_year_seen": year,
                    "last_year_seen": year,
                    "is_individual": exclusion["is_individual"],
                },
            )
            if exclusion["client_id"]:
                row["client_ids"].add(exclusion["client_id"])
            row["raw_name_variants"].add(exclusion["raw_name"])
            row["filings"] += 1
            row["income_total"] += _money(filing.get("income"), "income")
            row["expenses_total"] += _money(filing.get("expenses"), "expenses")
            row["first_year_seen"] = min(row["first_year_seen"], year)
            row["last_year_seen"] = max(row["last_year_seen"], year)
            if exclusion["is_individual"]:
                individual_rows[key] = row

    roster_rows = []
    sector_receipts = {}
    for sector in sectors:
        rows = []
        for family in sorted(families[sector]):
            row = families[sector][family]
            rows.append(_finalize_set_row(row))
            roster_rows.append(_finalize_set_row(row))
        sector_receipts[sector] = {
            "roster_size": len(rows),
            "families": rows,
        }
    exclusions = [_finalize_exclusion_row(exclusion_rows[key]) for key in sorted(exclusion_rows)]
    individuals = [_finalize_exclusion_row(individual_rows[key]) for key in sorted(individual_rows)]
    return sector_items, sector_receipts, roster_rows, exclusions, individuals


def _finalize_set_row(row):
    result = dict(row)
    for field in (
        "raw_name_variants",
        "normalized_name_variants",
        "matched_patterns",
        "client_ids",
        "collapse_rules",
        "bank_tiers",
    ):
        if field in result:
            result[field] = sorted(result[field])
    return result


def _finalize_exclusion_row(row):
    result = dict(row)
    result["client_ids"] = sorted(result["client_ids"])
    result["raw_name_variants"] = sorted(result["raw_name_variants"])
    result["income_total"] = _json_number(result["income_total"])
    result["expenses_total"] = _json_number(result["expenses_total"])
    return result


def build_document(winners, years, sectors, receipts):
    years = tuple(sorted(set(int(year) for year in years)))
    sectors = tuple(sectors)
    sector_items, roster_receipts, roster_rows, exclusions, individuals = (
        _build_roster_receipt(winners, sectors)
    )
    output_sectors = {}
    for sector in sectors:
        annual_rows = []
        items = sector_items[sector]
        tiered = sector == "bank"
        for year in years:
            annual_items = [item for item in items if int(item[0]["filing_year"]) == year]
            year_row = {
                "year": year,
                "is_partial_year": year in PARTIAL_YEARS,
                "partial_year_note": PARTIAL_YEARS.get(year),
                **_metric_record(annual_items, include_bank_tier=tiered),
                "quarters": [],
            }
            for quarter in QUARTERS:
                quarter_items = [
                    item
                    for item in annual_items
                    if PERIOD_TO_QUARTER[item[0]["filing_period"]] == quarter
                ]
                year_row["quarters"].append(
                    {
                        "quarter": quarter,
                        "is_partial_year": year in PARTIAL_YEARS,
                        **_metric_record(quarter_items, include_bank_tier=tiered),
                    }
                )
            annual_rows.append(year_row)
        output_sectors[sector] = {"years": annual_rows}

    receipts = dict(receipts)
    receipts["financial_series"] = {
        "income_total": FINANCIAL_SERIES["income_total"],
        "expenses_total": FINANCIAL_SERIES["expenses_total"],
        "hard_rule": "The two series are never added; concentration uses income only.",
    }
    receipts["rosters"] = {
        "instrument": "Pinned ordered patterns plus explicit client-ID includes/excludes",
        "sectors": roster_receipts,
        "exclusions": exclusions,
    }
    receipts["entity_identity"] = _entity_identity_receipt(sector_items, years)
    receipts["bank_tier"] = _bank_tier_receipt()
    document = {
        "schema_version": 1,
        "years": list(years),
        "selected_sectors": list(sectors),
        "honesty": {
            "entity_identity": (
                "Organization identity is resolved AS OF each filing year. A rename "
                "collapses across the whole window because one legal entity is one "
                "entity throughout; a merger is date-gated and is never collapsed "
                "backward. Before 2024 CUNA and NAFCU were two organizations and "
                "both lobbied, so they are counted as two; from 2024 they are "
                "America's Credit Unions. Any total spanning that boundary is the "
                "SUM of the predecessors plus the successor, never one "
                "organization's continuous history."
            ),
            "entity_identity_not_exhaustive": (
                "The identity table catches the renames and mergers that filers "
                "declared plus the ones we researched. It does NOT prove there are "
                "no others; receipts.entity_identity.unresolved bounds the residual."
            ),
            "bank_tier": (
                "bank_tier labels organizations already in the bank sector and never "
                "changes who is in it: the bank sector is NOT split into two sectors, "
                "so no published bank number is redefined. AMERICAN BANKERS "
                "ASSOCIATION and CONSUMER BANKERS ASSOCIATION are both deliberately "
                "MIXED. The big roster is the union of the current BPI board, the 2023 "
                "BPI board, the FSB US G-SIB list and the Federal Reserve 2026 "
                "stress-test Table 3, applied as of each filing year so institutions "
                "that ceased to exist mid-window are big while they existed. "
                "Organizations neither on that roster nor recognised as an association "
                "default to community; defaulted_* isolates that inferred share and "
                "by_basis gives the full evidence ladder."
            ),
            "bank_tier_roster_line_is_not_the_policy_line": (
                "The $10bn policy line (CFPB supervision, Durbin interchange) is the "
                "concept behind the split, but the roster's own inclusion lines are the "
                "BPI board, the G-SIB list and the Fed's $100bn tailoring categories. "
                "Banks between $10bn and $100bn are therefore NOT on the roster and land "
                "in community by default. The community series is a community-and-"
                "mid-size-regional series, and the defaulted counts bound it."
            ),
            "partial_year": "2026 is partial through Q2 and is not comparable to a full year without that qualification.",
            "financial_units": (
                "income is fees paid to outside registrants; expenses is self-reported "
                "in-house lobbying expenses. They are different units and are never added."
            ),
            "roster_instrument": (
                "Absolute levels depend on the pinned name-pattern roster; year-over-year "
                "shape is the defensible claim."
            ),
            "sector_overlap": (
                "Sector memberships are measured independently and may overlap when a "
                "client genuinely matches more than one pinned sector roster."
            ),
        },
        "financial_series": dict(FINANCIAL_SERIES),
        "financial_series_are_never_added": True,
        "concentration_basis": "income only",
        "receipts": receipts,
        "excluded_individuals": individuals,
        "sectors": output_sectors,
    }
    assert_separate_financial_series(document)
    return document, roster_rows


def _bank_tier_receipt():
    """Where every big-bank name came from, and what is still unnamed."""
    if not ENTITY_IDENTITY:
        return {
            "enabled": False,
            "note": "ENTITY_IDENTITY=0: no tier is assigned.",
        }
    return {
        "enabled": True,
        "tiers": list(entity_identity.BANK_TIERS),
        "threshold_note": entity_identity.BANK_TIER_THRESHOLD_NOTE,
        "bpi_membership_gap": entity_identity.BPI_MEMBERSHIP_GAP_NOTE,
        "barred_source": entity_identity.BARRED_SOURCE_NOTE,
        "roster_nesting": entity_identity.ROSTER_NESTING_NOTE,
        "sources": {
            key: dict(value) for key, value in sorted(entity_identity.BANK_TIER_SOURCES.items())
        },
        "provenance": entity_identity.bank_tier_provenance(),
    }


def _entity_identity_receipt(sector_items, years):
    """What the identity table actually did to this measurement."""
    if not ENTITY_IDENTITY:
        return {
            "enabled": False,
            "note": "ENTITY_IDENTITY=0: pre-table behaviour, name-pattern identity only.",
        }
    resolved_families = defaultdict(set)
    unresolved_names = defaultdict(set)
    alias_only = defaultdict(set)
    for sector, items in sector_items.items():
        for _filing, assignment in items:
            if assignment["collapse_rule"] == "entity_identity":
                resolved_families[sector].add(assignment["canonical_family"])
            else:
                unresolved_names[sector].add(assignment["normalized_name"])
            if assignment.get("matched_via") == "entity_alias":
                alias_only[sector].add(assignment["canonical_family"])
    boundaries = {}
    for event in resolver().events:
        if event["relation"] not in entity_identity.DATE_GATED_RELATIONS:
            continue
        if not (min(years) < event["effective_filing_year"] <= max(years)):
            continue
        boundaries[event["event_id"]] = {
            "relation": event["relation"],
            "predecessors": list(event["predecessors"]),
            "successor": event["successor"],
            "effective_date": event["effective_date"],
            "effective_filing_year": event["effective_filing_year"],
            "confidence": event["confidence"],
            "evidence": event["evidence"],
            "constituents_by_year": resolver().constituent_breakdown(event["successor"], years)["by_year"],
        }
    return {
        "enabled": True,
        "table": os.path.abspath(entity_identity.default_artifact_path()),
        "resolver": resolver().receipt(),
        "as_of_basis": "filing_year",
        "sectors": {
            sector: {
                "organizations_resolved_by_identity_table": len(resolved_families[sector]),
                "organizations_admitted_via_entity_alias_only": sorted(alias_only[sector]),
                "distinct_names_with_no_identity_entry": len(unresolved_names[sector]),
            }
            for sector in sorted(sector_items)
        },
        "merger_boundaries_inside_window": boundaries,
        "boundary_rule": (
            "A total spanning a boundary year must be labelled as the sum of the "
            "predecessors before it plus the successor after it."
        ),
    }


def assert_separate_financial_series(document):
    """Hard schema guard against introducing a combined financial measure."""
    if document.get("financial_series_are_never_added") is not True:
        raise SectorSpendError("separate-series invariant is absent")
    if set(document.get("financial_series", {})) != set(FINANCIAL_SERIES):
        raise SectorSpendError("financial series schema changed")

    def visit(value):
        if isinstance(value, dict):
            forbidden = FORBIDDEN_COMBINED_KEYS.intersection(value)
            if forbidden:
                raise SectorSpendError(
                    f"forbidden combined financial key(s): {sorted(forbidden)}"
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return True


def measure_filings(filings, years=DEFAULT_YEARS, sectors=DEFAULT_SECTORS):
    """Measure an in-memory iterable; intended for synthetic tests and review."""
    records = list(filings)
    sectors = _validate_sectors(sectors)
    excluded_types, receipts = inspect_filings(records)
    winners, supersession, excluded_seen = supersede_filings(
        records, excluded_types, sectors
    )
    expected_excluded = {
        row["filing_type"]: row["records_excluded"]
        for row in receipts["filing_type_exclusions"]["types"]
    }
    if excluded_seen != expected_excluded:
        raise SectorSpendError("filing-type exclusion receipt changed between passes")
    receipts["amendment_supersession"] = supersession
    return build_document(winners, years, sectors, receipts)


def default_sweep_root():
    from paths import LOCAL_MELANGE
    return Path(LOCAL_MELANGE) / "lda_rebuild" / "raw" / "sweeps"


def iter_corpus_filings(sweep_root, years):
    sweep_root = Path(sweep_root)
    for year in sorted(set(int(value) for value in years)):
        year_dir = sweep_root / str(year)
        pages = sorted(year_dir.glob("page_*.json"))
        if not pages:
            raise SectorSpendError(f"no sweep pages found for {year}: {year_dir}")
        for page in pages:
            try:
                with page.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise SectorSpendError(f"cannot read sweep page {page}: {exc}") from exc
            results = payload.get("results")
            if not isinstance(results, list):
                raise SectorSpendError(f"sweep page has no results list: {page}")
            for filing in results:
                if not isinstance(filing, dict):
                    raise SectorSpendError(f"non-object filing in {page}")
                if int(filing.get("filing_year")) != year:
                    raise SectorSpendError(
                        f"filing {filing.get('filing_uuid')} in {page} has wrong year"
                    )
                yield filing


def run_measurement(years, sectors, sweep_root=None):
    """Run the two-pass bounded-memory production measurement."""
    years = tuple(sorted(set(int(year) for year in years)))
    sectors = _validate_sectors(sectors)
    sweep_root = default_sweep_root() if sweep_root is None else Path(sweep_root)
    excluded_types, receipts = inspect_filings(iter_corpus_filings(sweep_root, years))
    winners, supersession, excluded_seen = supersede_filings(
        iter_corpus_filings(sweep_root, years), excluded_types, sectors
    )
    expected_excluded = {
        row["filing_type"]: row["records_excluded"]
        for row in receipts["filing_type_exclusions"]["types"]
    }
    if excluded_seen != expected_excluded:
        raise SectorSpendError("filing-type exclusion receipt changed between passes")
    receipts["amendment_supersession"] = supersession
    if years == DEFAULT_YEARS:
        _assert_expected_full_corpus(receipts)
        receipts["full_corpus_baseline"] = {
            **EXPECTED_FULL_CORPUS,
            "matched": True,
        }
    receipts["source"] = {
        "root": str(sweep_root),
        "page_order": "year ascending, zero-padded page filename ascending, API result order",
    }
    return build_document(winners, years, sectors, receipts)


def _assert_expected_full_corpus(receipts):
    dedupe = receipts["deduplication"]
    exclusions = receipts["filing_type_exclusions"]
    supersession = receipts["amendment_supersession"]
    observed = {
        "raw_filings": dedupe["raw_filings"],
        "unique_filing_uuids": dedupe["unique_filing_uuids"],
        "pagination_duplicates_removed": dedupe["pagination_duplicates_removed"],
        "nonfinancial_records_excluded": exclusions["records_excluded"],
        "records_superseded": supersession["records_superseded"],
        "dt_posted_missing_or_invalid": (
            supersession["dt_posted_fallback_frequency"]["records_missing"]
            + supersession["dt_posted_fallback_frequency"]["records_invalid"]
        ),
    }
    if observed != EXPECTED_FULL_CORPUS:
        raise SectorSpendError(
            "full-corpus baseline drift; expected "
            f"{EXPECTED_FULL_CORPUS!r}, observed {observed!r}"
        )


def _validate_sectors(sectors):
    values = tuple(dict.fromkeys(str(value).strip() for value in sectors))
    unknown = [value for value in values if value not in ROSTERS]
    if unknown:
        raise SectorSpendError(f"unknown sector(s): {', '.join(unknown)}")
    if not values:
        raise SectorSpendError("at least one sector is required")
    return values


def _flatten_sector_rows(document):
    rows = []
    for sector in document["selected_sectors"]:
        for year_row in document["sectors"][sector]["years"]:
            rows.append(_csv_metric_row(sector, year_row["year"], "annual", year_row))
            for quarter in year_row["quarters"]:
                rows.append(
                    _csv_metric_row(
                        sector, year_row["year"], quarter["quarter"], quarter
                    )
                )
    return rows


BANK_TIER_CSV_FIELDS = tuple(
    f"tier_{tier}_{measure}"
    for tier in entity_identity.BANK_TIERS
    for measure in ("organizations", "income_total", "expenses_total")
) + (
    "tier_community_defaulted_organizations",
    "tier_community_defaulted_income_total",
    "tier_community_defaulted_expenses_total",
)


def _csv_metric_row(sector, year, period, metrics):
    concentration = metrics["income_concentration"]
    row = {
        "sector": sector,
        "year": year,
        "period": period,
        "is_partial_year": str(bool(metrics["is_partial_year"])).lower(),
        "distinct_organizations": metrics["distinct_organizations"],
        "filings": metrics["filings"],
        "income_total": metrics["income_total"],
        "expenses_total": metrics["expenses_total"],
        "distinct_registrants": metrics["distinct_registrants"],
        "distinct_named_lobbyists": metrics["distinct_named_lobbyists"],
        "top_1_income_share": concentration["top_1_share"],
        "top_3_income_share": concentration["top_3_share"],
        "top_10_income_share": concentration["top_10_share"],
        "income_hhi": concentration["hhi"],
    }
    tiers = metrics.get("bank_tier") or {}
    for tier in entity_identity.BANK_TIERS:
        values = tiers.get(tier) or {}
        row[f"tier_{tier}_organizations"] = values.get("distinct_organizations", "")
        row[f"tier_{tier}_income_total"] = values.get("income_total", "")
        row[f"tier_{tier}_expenses_total"] = values.get("expenses_total", "")
    community = tiers.get("community") or {}
    row["tier_community_defaulted_organizations"] = community.get("defaulted_organizations", "")
    row["tier_community_defaulted_income_total"] = community.get("defaulted_income_total", "")
    row["tier_community_defaulted_expenses_total"] = community.get("defaulted_expenses_total", "")
    return row


def _atomic_text(path, writer):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def write_outputs(out_dir, document, roster_rows):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "sector_spend.json"
    csv_path = out_dir / "sector_spend.csv"
    roster_path = out_dir / "sector_roster.csv"

    _atomic_text(
        json_path,
        lambda handle: handle.write(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ),
    )
    metric_rows = _flatten_sector_rows(document)
    metric_fields = list(metric_rows[0]) if metric_rows else []

    def write_metrics(handle):
        writer = csv.DictWriter(handle, fieldnames=metric_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(metric_rows)

    _atomic_text(csv_path, write_metrics)
    roster_fields = (
        "sector",
        "canonical_family",
        "bank_tiers",
        "raw_name_variants",
        "normalized_name_variants",
        "matched_pattern",
        "client_ids",
        "collapse_rules",
        "first_year_seen",
        "last_year_seen",
    )

    def write_roster(handle):
        writer = csv.DictWriter(handle, fieldnames=roster_fields, lineterminator="\n")
        writer.writeheader()
        for source in sorted(
            roster_rows, key=lambda row: (row["sector"], row["canonical_family"])
        ):
            row = {
                field: source.get(field, "")
                for field in roster_fields
                if field != "matched_pattern"
            }
            row["matched_pattern"] = " | ".join(source["matched_patterns"])
            for field in (
                "raw_name_variants",
                "normalized_name_variants",
                "client_ids",
                "collapse_rules",
                "bank_tiers",
            ):
                row[field] = " | ".join(row[field] or ())
            writer.writerow(row)

    _atomic_text(roster_path, write_roster)
    return json_path, csv_path, roster_path


def stdout_summary(document):
    dedupe = document["receipts"]["deduplication"]
    excluded = document["receipts"]["filing_type_exclusions"]
    supersession = document["receipts"]["amendment_supersession"]
    lines = [
        "Canonical LDA sector measurement",
        (
            f"  corpus: {dedupe['raw_filings']:,} raw -> "
            f"{dedupe['unique_filing_uuids']:,} UUID-deduped "
            f"({dedupe['pagination_duplicates_removed']:,} duplicates)"
        ),
        (
            f"  nonfinancial types: {excluded['records_excluded']:,} records; "
            + ", ".join(
                f"{row['filing_type']}={row['records_excluded']:,}"
                for row in excluded["types"]
            )
        ),
        f"  superseded: {supersession['records_superseded']:,} records",
        "  income and self-reported in-house lobbying expenses are separate series",
        "  2026: PARTIAL YEAR through Q2",
    ]
    for sector in document["selected_sectors"]:
        roster_size = document["receipts"]["rosters"]["sectors"][sector][
            "roster_size"
        ]
        lines.append(f"  {sector}: {roster_size:,} canonical organizations")
        for row in document["sectors"][sector]["years"]:
            suffix = " (PARTIAL through Q2)" if row["is_partial_year"] else ""
            lines.append(
                f"    {row['year']}{suffix}: orgs={row['distinct_organizations']:,}, "
                f"filings={row['filings']:,}, income=${row['income_total']:,.2f}, "
                f"expenses=${row['expenses_total']:,.2f}"
            )
    return "\n".join(lines)


def parse_years(value):
    years = set()
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, stop_text = token.split("-", 1)
            start, stop = int(start_text), int(stop_text)
            if stop < start:
                raise argparse.ArgumentTypeError(f"descending year range: {token}")
            years.update(range(start, stop + 1))
        else:
            years.add(int(token))
    invalid = sorted(year for year in years if year not in DEFAULT_YEARS)
    if invalid or not years:
        raise argparse.ArgumentTypeError(
            "years must be a nonempty subset of 2021-2026"
        )
    return tuple(sorted(years))


def parse_sectors(value):
    try:
        return _validate_sectors(token.strip() for token in str(value).split(","))
    except SectorSpendError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years", type=parse_years, default=DEFAULT_YEARS, help="e.g. 2021-2026 or 2021,2024,2026"
    )
    parser.add_argument(
        "--sectors",
        type=parse_sectors,
        default=DEFAULT_SECTORS,
        help="comma-separated: " + ", ".join(DEFAULT_SECTORS),
    )
    parser.add_argument("--out-dir", default=".", help="directory for the three outputs")
    args = parser.parse_args(argv)
    try:
        document, roster_rows = run_measurement(args.years, args.sectors)
        paths = write_outputs(args.out_dir, document, roster_rows)
    except (OSError, SectorSpendError) as exc:
        parser.exit(2, f"sector_spend: {exc}\n")
    print(stdout_summary(document))
    print("  outputs: " + ", ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
