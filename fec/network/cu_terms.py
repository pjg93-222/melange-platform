# -*- coding: utf-8 -*-
"""CU relevance vocabulary shared by process_quorum.py and build_social_master.py.
Kept in its own side-effect-free module so importing it never re-runs the Quorum pipeline."""
import re
# full vocabulary of the corrected Quorum query (quorum_query.txt) — keep in sync with it
CU_TERMS=re.compile(r'credit union|\bncua\b|national credit union|share insurance|ncusif|central liquidity|'
    r'tax.?exempt|tax exemption|tax status|field of membership|common bond|federal charter|charter modernization|'
    r'low.?income designation|\bcdfi\b|community development financial|minority depository|'
    r'\bcfpb\b|consumer financial protection|section 1071|small business lending rule|section 1033|open banking|'
    r'interchange|swipe fee|credit card competition|\bccca\b|durbin|routing mandate|debit card|credit card fee|'
    r'late fee|junk fee|overdraft|nsf fee|insufficient funds fee|'
    r'member business lending|mbl cap|trigger lead|\bhmda\b|small.?dollar loan|payday|'
    r'community bank|big bank|megabank|wall street bank|bank consolidation|de novo charter|industrial loan company|'
    r'fednow|real.?time payments|faster payments|payments system|stablecoin|digital dollar|'
    r'central bank digital currency|\bcbdc\b|earned wage access|'
    r'check fraud|elder financial|elder fraud|p2p fraud|peer.?to.?peer payment|regulation e\b|'
    r'data breach notification|data security standards|gramm.?leach|\btcpa\b|telephone consumer protection|robocall|'
    r'military lending act|defense credit union|military banking|banks on military|'
    r'federal home loan bank|gse reform|mortgage servicing|loan participations|credit invisible|'
    r'financial inclusion|de.?risking|brokered deposits',re.I)
