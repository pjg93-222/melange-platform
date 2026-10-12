# Mentat report working directory

The user designated `C:/Users/pgoff/OneDrive/Desktop/Mentat Complete Files` as the sole source folder for viewing report documents and the destination for final generated Excel reports. Resolve it through `config/report-directory.json` / `tools/report_directory.py`; isolated runtimes inherit `MENTAT_REPORT_DIRECTORY`.

- View report documents with this directory as their working directory. Do not substitute workbooks from Downloads, repository reports, caches, or historical run directories.
- Place final user-facing Excel reports in this folder. Temporary build files, collector inputs, test fixtures, source databases and preserved FEC evidence are internal artifacts and remain separate.
- Banking and Community Banks are distinct scopes. Show the workbook, companies, cycles and accounting basis behind each total; never present one scope as the other.
- Preserve raw observations and explain accounting changes. Do not treat missing or ambiguous evidence as proven zero giving.
- The user used “99.9%” metaphorically to demand thorough, accurate work. It is not a literal numerical certification requirement.
- Auto-resolve questions answered by available evidence. Keep receipt verification, company attribution, and complete-filing/route coverage separate; a missing global inventory must not generate manual review for every clear receipt.
- Evaluate full approved employer aliases before prefix heuristics. Use the shared scalar/batch R matcher when rechecking historical holds, preserve previous decisions, and keep unrelated near-matches and explicit exclusions intact.
- Put accepted employer aliases and their evidence in `inputs/verified_employer_aliases.csv`; put dated connected-PAC names and exact aliases in `inputs/verified_company_affiliations.json`. Fingerprint both inputs in every run. Exact normalized matches may resolve automatically; prefix, typo and near-name expansion must remain review.
- Classify explicit non-giving events before asking for attribution that cannot affect giving. Preserve actual conflicts and unknown roles. Implement lessons in shared rules, replay evidence, and positive/counterexample tests instead of one-off row overrides.

Follow any later explicit user instruction changing the folder or scope.
