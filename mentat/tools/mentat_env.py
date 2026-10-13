"""Where Mentat's compute jobs find the machine's tools (containers, D-173..D-182).

One table, one reader. Each entry is an environment variable whose default is today's Windows value, so a Windows run is
unchanged and a container only sets the variables (compute.compose.yaml). The PowerShell launchers and R/paths.R carry the
same two defaults inline; tests/test_compute_seams.py pins every copy against this table and proves each variable moves what
it names. Locations that already had their own variable (MENTAT_REPORT_DIRECTORY, LOCALAPPDATA, MENTAT_DATA_ROOT, RF_*,
*_GLASSBOX_DIR, MENTAT_MELANGE_ROOT) keep it; compute_job.py prints the effective values into every job receipt.
"""
import os

# name -> (Windows default, what it names)
SEAMS = {
    'MENTAT_RSCRIPT': ('C:/Program Files/R/R-4.6.0/bin/Rscript.exe', 'the Rscript executable'),
    'MENTAT_R_LIBRARY': ('C:/Users/pgoff/AppData/Local/R/win-library/4.6', 'the R user package library'),
}


def seam(name):
    """The value of a table entry: the environment variable when it is set, else the Windows default."""
    return os.environ.get(name, '').strip() or SEAMS[name][0]
