"""Prepare an application-private R runtime and the code's installed dependencies.

This inventories versions/licenses but does not grant redistribution approval.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--r-home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extra-library', type=Path)
    parser.add_argument('--refresh-inventory', action='store_true', help='Refresh a previously prepared task runtime and add newly identified packages')
    options = parser.parse_args()
    code = options.source.resolve()
    sources = []
    for folder in ('R', 'tools', 'forecast', 'app'):
        for path in (code / folder).rglob('*.R'):
            if any(part in {'tests', 'fixtures', '.cache', 'outputs'} for part in path.parts):
                continue
            sources.append(str(path))
    output = options.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / 'r-inventory'
    work.mkdir(exist_ok=True)
    (work / 'sources.txt').write_text('\n'.join(sorted(sources)), encoding='utf-8')
    script = work / 'inventory.R'
    script.write_text('''args <- commandArgs(trailingOnly=TRUE)
wanted <- character()
walk <- function(x) {
  if (is.call(x)) {
    name <- if (is.symbol(x[[1]])) as.character(x[[1]]) else ""
    if (name %in% c("::",":::")) wanted <<- union(wanted, as.character(x[[2]]))
    if (name %in% c("require_package","requireNamespace","library","require") && length(x)>=2L && is.character(x[[2]])) wanted <<- union(wanted, x[[2]])
    if (name %in% c("library","require") && length(x)>=2L && is.symbol(x[[2]])) wanted <<- union(wanted, as.character(x[[2]]))
  }
  if (is.call(x) || is.expression(x) || is.pairlist(x)) for (item in as.list(x)) tryCatch(walk(item), error=function(e) NULL)
}
for (file in readLines(args[1], warn=FALSE)) walk(parse(file=file, keep.source=FALSE))
writeLines(sort(wanted), args[4])
if (length(args)>4L && nzchar(args[5])) .libPaths(c(args[5], .libPaths()))
db <- installed.packages()
missing <- setdiff(wanted, rownames(db))
writeLines(missing, args[3])
available <- intersect(wanted, rownames(db))
deps <- unique(unlist(tools::package_dependencies(available, db=db, which=c("Depends","Imports","LinkingTo"), recursive=TRUE)))
names <- intersect(unique(c(available,deps)), rownames(db))
write.csv(db[names,c("Package","Version","LibPath","License")], args[2], row.names=FALSE)
''', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(('R_PROFILE','R_ENVIRON'))}
    subprocess.run([str(options.r_home / 'bin' / 'Rscript.exe'), '--vanilla', str(script), str(work / 'sources.txt'),
                    str(work / 'packages.csv'), str(work / 'missing.txt'), str(work / 'wanted.txt'),
                    str(options.extra_library or '')], env=environment, check=True, timeout=120,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    with (work / 'packages.csv').open(encoding='utf-8-sig', newline='') as handle:
        packages = list(csv.DictReader(handle))
    missing = (work / 'missing.txt').read_text(encoding='utf-8-sig').splitlines()
    wanted = (work / 'wanted.txt').read_text(encoding='utf-8-sig').splitlines()
    if output.exists() and not options.refresh_inventory:
        raise RuntimeError('Choose an unused R output directory; existing runtime is preserved')
    if options.refresh_inventory and not (output / 'dependency-inventory.json').is_file():
        raise RuntimeError('Refresh requires an existing prepared runtime receipt')
    if not output.exists():
        shutil.copytree(options.r_home, output, ignore=shutil.ignore_patterns('unins*', 'tests', '__pycache__', 'Rprofile.site', 'Renviron.site'))
    for name in ('Rprofile.site', 'Renviron.site'):
        personal = output / 'etc' / name
        if personal.exists():
            personal.unlink()
    for package in packages:
        destination = output / 'library' / package['Package']
        if destination.exists():
            continue
        shutil.copytree(Path(package['LibPath']) / package['Package'], destination, ignore=shutil.ignore_patterns('tests', '__pycache__'))
    receipt = {'requiredPackages': sorted(wanted), 'missingPackages': missing,
               'packages': [{key: row[key] for key in ('Package','Version','License')} for row in packages],
               'redistributionApproved': False}
    (output / 'dependency-inventory.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'runtime': str(output), 'packages': len(packages), 'missingPackages': missing, 'redistributionApproved': False}))


if __name__ == '__main__':
    main()
