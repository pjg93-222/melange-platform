# Comparing charts

Retired on 19 September 2026. The **1 chart / 2 charts / 3 charts / 4 charts** buttons, **Canvas only in every chart** and **Linked filters** are gone, and so are the separate chart panes they opened.

To put more than one chart on one screen, switch Chart Studio to **Canvas** and choose **Board 1**, **2** or **4**. Each tile shows one sheet of this workspace, with its own title, scope and reconciliation line, under one Inspector and one Export. A saved link that still carries `?studioPane=B` opens the ordinary Chart Studio.

The **Chart variant** selector above the shelves is unchanged: it uses the existing supported chart families, and field requirements still apply to each variant.

# State destinations

Under Data, choose a **State location view**. Recipient committee address, candidate represented state, and contributor state are separate fields. The first two require a freshly generated workbook with destination schema 1. Older workbooks display a regeneration message.

Workbook generation adds **State Destinations** and **Recipient Locations** sheets. Destination totals cover all included receipts and reconcile independently to the same population. Never add the two location partitions together. Candidate mapping uses cycle-specific principal/authorized committee links. Multiple candidates remain ambiguous, missing links remain unknown, and presidential candidates are national. Committee locations retain DC, territories, and unrecognized reported codes separately.

Candidate geography does not trace later committee spending or allocate one company's money across beneficiaries. Existing Downstream Activity remains a separate accounting stage. The compact workbook payload retains location and evidence fields for verified contribution queries.

Restart Mentat after installing code changes. Regenerate existing published workbooks through the normal guarded report pipeline to populate destination fields; editing workbook cells by hand cannot supply verified source lineage.
