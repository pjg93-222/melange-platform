# Hold a separate SQLite writer transaction for the lifetime of one lane.
# The R process retains this lock if its Python supervisor disappears, so a
# restarted supervisor cannot run another writer against the same snapshot.
lane_owner_refusal <- function(message) {
  stop(structure(list(message = message, call = NULL),
                 class = c("mentat_lane_owned", "error", "condition")))
}

check_lane_supervisor <- function(owner_path, owner_token) {
  if (!nzchar(owner_token)) return(invisible(TRUE))
  require_package("jsonlite")
  owner <- tryCatch(jsonlite::read_json(owner_path, simplifyVector = FALSE),
                    error = function(condition) NULL)
  if (is.null(owner) || !identical(owner$token, owner_token) ||
      !isTRUE(owner$active)) {
    lane_owner_refusal("This R lane belongs to a superseded supervisor invocation.")
  }
  invisible(TRUE)
}

acquire_lane_writer <- function(database_path) {
  require_package("DBI")
  require_package("RSQLite")
  lock_path <- paste0(database_path, ".writer-lock.sqlite")
  owner <- DBI::dbConnect(RSQLite::SQLite(), lock_path)
  tryCatch({
    DBI::dbExecute(owner, "PRAGMA busy_timeout=0")
    DBI::dbExecute(owner, "BEGIN IMMEDIATE")
    owner
  }, error = function(condition) {
    if (DBI::dbIsValid(owner)) DBI::dbDisconnect(owner)
    if (grepl("locked", conditionMessage(condition), ignore.case = TRUE)) {
      lane_owner_refusal(paste0("Another R lane writer owns this database: ",
                                database_path))
    }
    stop(condition)
  })
}

release_lane_writer <- function(owner) {
  if (!DBI::dbIsValid(owner)) return(invisible(NULL))
  try(DBI::dbRollback(owner), silent = TRUE)
  DBI::dbDisconnect(owner)
  invisible(NULL)
}
