# The industry registry (registry/industries.json) and the frozen model specifications (registry/models.json).
# The registry says which raw inputs exist for each industry; a model is fitted for an industry only when its inputs
# are present, and the withheld reason is stated (never guessed, never silent).

rf_read_registry <- function(path = file.path(rf_home(), "registry", "industries.json")) {
  reg <- rf_read_json(path)
  reg$industries <- stats::setNames(reg$industries, vapply(reg$industries, function(i) i$id, ""))
  reg
}

rf_read_models <- function(path = file.path(rf_home(), "registry", "models.json")) {
  m <- rf_read_json(path)
  m$models <- stats::setNames(m$models, vapply(m$models, function(x) x$id, ""))
  m
}

# Problems found in the registry; character(0) means valid.
rf_validate_registry <- function(reg) {
  ind <- reg$industries
  out <- character()
  ids <- vapply(ind, function(i) i$id, "")
  if (anyDuplicated(ids)) out <- c(out, paste("duplicate ids:", paste(ids[duplicated(ids)], collapse = ", ")))
  for (i in ind) {
    os <- i$opensecrets
    for (k in c("recipients", "presidential")) {
      p <- os[[k]]
      if (!is.null(p) && !grepl("{cycle}", p, fixed = TRUE)) out <- c(out, sprintf("%s: opensecrets.%s has no {cycle}", i$id, k))
    }
    if (!is.null(os$recipients) && !grepl("{chamber}", os$recipients, fixed = TRUE)) out <- c(out, sprintf("%s: opensecrets.recipients has no {chamber}", i$id))
    for (m in c("pac", "control", "lobbying")) {
      v <- i$models[[m]]
      if (!is.logical(v) || length(v) != 1L || is.na(v)) out <- c(out, sprintf("%s: models.%s is not boolean", i$id, m))
    }
    if (!is.logical(i$calculator) || length(i$calculator) != 1L) out <- c(out, sprintf("%s: calculator is not boolean", i$id))
    else if (isTRUE(i$calculator) && !any(unlist(i$models))) out <- c(out, sprintf("%s: calculator industry with no model", i$id))
    if (isTRUE(i$lobbying_only) && (!isTRUE(i$calculator) || isTRUE(i$models$pac) || isTRUE(i$models$control) || !isTRUE(i$models$lobbying) || !nzchar(i$reason %||% "")))
      out <- c(out, sprintf("%s: a lobbying-only industry is a calculator industry with the lobbying model only and a stated reason for the others", i$id))
    if (!is.null(i$estimation) && !identical(i$estimation, "scored_only")) out <- c(out, sprintf("%s: unknown estimation mode '%s' (only 'scored_only' exists)", i$id, paste(i$estimation, collapse = "/")))
    if (identical(i$estimation, "scored_only") && (!identical(i$status, "pilot") || !isTRUE(i$models$pac) || isTRUE(i$models$control) || is.null(i$opensecrets$totals) || is.null(i$ibisworld$actual_sector)))
      out <- c(out, sprintf("%s: a scored-only industry is a pilot with the PAC model, no control model, an OpenSecrets totals capture and an IBISWorld actual series", i$id))
    if (identical(i$status, "declined") && any(unlist(i$models))) out <- c(out, sprintf("%s: declined industry with a model", i$id))
    if (identical(i$status, "declined") && !nzchar(i$reason %||% "")) out <- c(out, sprintf("%s: declined industry without a reason", i$id))
  }
  out
}

# Why an industry has no `model` ("pac", "control", "lobbying"), or NA when it has everything that model needs.
rf_withheld_reason <- function(i, model) {
  if (!isTRUE(i$models[[model]])) return(i$withheld_by_model[[model]] %||% i$reason %||% paste0("model '", model, "' is not configured for this industry"))     # withheld_by_model: a stated reason for one model only (Telecom: control); not named reason_*: `$` would partial-match it for `reason`
  if (model %in% c("pac", "control")) {
    os <- i$opensecrets
    if (is.null(os$totals) || (model == "control" && (is.null(os$recipients) || is.null(os$presidential)))) {
      return(os$reason %||% paste0("OpenSecrets ", paste(unlist(os$codes), collapse = "/"), " capture not supplied"))
    }
    if (is.null(i$ibisworld$actual_sector)) return("no IBISWorld actual-revenue series")
  }
  if (model == "lobbying" && (is.null(i$lobbyview$study_sector) || identical(i$ibisworld$predicted_rule, "none"))) {
    return("no LobbyView / predicted-growth series")
  }
  NA_character_
}

# Ids of the industries whose inputs exist for `model`.
rf_capable <- function(reg, model) {
  ids <- names(reg$industries)
  ids[vapply(reg$industries, function(i) is.na(rf_withheld_reason(i, model)), logical(1))]
}

# OpenSecrets code -> industry id map of the industries that have totals.
rf_code_map <- function(reg) {
  ids <- rf_capable(reg, "pac")
  stats::setNames(ids, vapply(ids, function(id) reg$industries[[id]]$opensecrets$codes[[1]], ""))
}

# Expand "{cycle}" / "{chamber}" in a pattern.
rf_fill <- function(pattern, cycle = NULL, chamber = NULL) {
  if (!is.null(cycle)) pattern <- gsub("{cycle}", cycle, pattern, fixed = TRUE)
  if (!is.null(chamber)) pattern <- gsub("{chamber}", chamber, pattern, fixed = TRUE)
  pattern
}

# The FROZEN view of the registry: only industries whose status is "fitted" estimate the PAC and control models. The v1 parity targets (Codex design
# files, the frozen bundle) were built on that sample, so every parity gate (G2, G3) and the G6 baseline of the published v1 calculator run on it.
# Pilot industries keep their lobbying flag (the lobbying panel is LobbyView's and already holds every sector).
rf_frozen_registry <- function(reg) {
  for (id in names(reg$industries)) {
    i <- reg$industries[[id]]
    if (!identical(i$status, "fitted")) { i$models$pac <- FALSE; i$models$control <- FALSE; reg$industries[[id]] <- i }
  }
  reg
}

# Pilot industries whose PAC and control inputs are all present: they join the estimation sample of the v2 refit (the gates decide whether the result
# may publish). -> character ids. An industry that still waits for data stays out and is reported as WAITING_FOR_DATA.
rf_ready_pilots <- function(reg, inputs) {
  ids <- names(reg$industries)[vapply(reg$industries, function(i) identical(i$status, "pilot") && isTRUE(i$models$pac) && isTRUE(i$models$control), logical(1))]
  Filter(function(id) rf_agent_data_status(reg, id, inputs)$ready, ids)
}

# Industries that are SCORED, not estimated (registry `estimation: "scored_only"`; Telecom, owner ruling 2026-10-08): their OpenSecrets history and IBISWorld growth are real, but they do not join the
# pooled phase / drift estimation, so the pooled numbers of every other industry stay exactly what they were. Their step variance is the pooled model's residual variance on their own history.
rf_scored_ids <- function(reg) names(reg$industries)[vapply(reg$industries, function(i) identical(i$estimation, "scored_only"), logical(1))]

# The v2 estimation registry: the frozen sample plus the ready pilots; `$scored` = the scored-only industries whose data is ready (the context builds their panel).
rf_v2_registry <- function(reg, inputs) {
  pil <- rf_ready_pilots(reg, inputs)
  frozen <- rf_frozen_registry(reg)
  for (id in pil) frozen$industries[[id]] <- reg$industries[[id]]
  scored <- Filter(function(id) rf_agent_data_status(reg, id, inputs)$ready, rf_scored_ids(reg))
  if (length(scored)) frozen$scored <- scored
  frozen
}
