script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1]])
root <- normalizePath(file.path(dirname(script), ".."), winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
# Fix: a unit with more sub_ids than SQLite's bound-variable cap (32766) failed
# storage with "too many SQL variables" (Allstate, Ford Credit, GM Financial
# 2020). The stored-company lookup is chunked; every row still lands once and
# every stored-vs-incoming company conflict is still recorded.
c <- DBI::dbConnect(RSQLite::SQLite(),":memory:"); init_schema(c)
n <- 32800L
rows <- data.table::data.table(sub_id=sprintf("S%06d",seq_len(n)), industry="Test", company="Acme",
  matched_variant="ACME", phase="P1_employer", scraped_at="2026-10-11")
insert_rows_or_ignore(c,"contributions",rows[c(1:10,32791:32800)],names(rows))
rows[c(3:10,32791:32797), company := "Other"]         # 15 conflicts, first and last chunk
insert_rows_or_ignore(c,"contributions",rows,names(rows))
q <- function(sql) DBI::dbGetQuery(c,sql)[[1]]
stopifnot(q("SELECT COUNT(*) FROM contributions")==n,
          q("SELECT COUNT(*) FROM contributions WHERE company='Acme'")==n,
          q("SELECT COUNT(*) FROM attribution_conflicts")==15L)
cat("test_sql_in_chunk: PASS\n")
