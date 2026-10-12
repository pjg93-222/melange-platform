script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1]])
root <- normalizePath(file.path(dirname(script), ".."), winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
# Fix: the FEC API answers HTTP 422 "Can only specify up to 10 values for
# `contributor_employer`" (Berkshire Hathaway 2020 had 12 P2 variants). A longer
# phase list becomes <=10-value units covering every variant exactly once;
# rosters within the cap keep their single unit and its unchanged id.
c <- DBI::dbConnect(RSQLite::SQLite(),":memory:"); init_schema(c)
names13 <- c("Acme", "Bolt Re", "Cobalt Group", "Delta Indemnity", "Ember Mutual", "Falcon Casualty",
  "Garnet Specialty", "Harbor Reinsurance", "Iris Assurance", "Juniper Underwriters",
  "Kestrel Surety", "Lumen Fidelity", "Mosaic Guaranty")
rosters <- list(Big=list(variant=names13, exclusions=NA_character_),
                Small=list(variant=names13[1:5], exclusions=NA_character_))
units <- fec_api_employer_units(c, 2020L, rosters=rosters)
values <- lapply(units, function(u) fec_api_request_spec(u)$params$contributor_employer)
big <- vapply(units, function(u) u$company == "Big", logical(1L))
stopifnot(all(lengths(values) <= 10L),
          sum(big & vapply(units, function(u) u$phase == "P2_variant", logical(1L))) == 2L,
          setequal(unlist(values[big]), names13), !anyDuplicated(unlist(values[big])),
          !anyDuplicated(vapply(units, function(u) u$id, character(1L))))
small <- units[!big]
parts <- partition_company_variants(names13[1:5])
stopifnot(length(small) == 2L,
          identical(small[[2]]$id, paste("P2_variant", "Small", 2020L, paste(parts$phase2, collapse="||"), sep=":")))
cat("test_employer_value_cap: PASS\n")
