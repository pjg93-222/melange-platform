# Gates G4-G9 of the refit (SPEC 3.13, as amended by the rulings of 2026-09-30) and the decision.
#   status PASS / WARN (report only) / HOLD (no publish, packet written) / FAIL (no publish, error).
#   G0 environment, G1 inputs, G2 parity and G3 panel validity come from rf_parity.R and are passed in.
#   G4 sample adequacy   per industry and model: >= 5 cycles (PAC, control) / >= 5 annual pairs (lobbying), pooled residual df >= 3,
#                        interaction df >= 2; an industry that fails a model is WITHHELD for it (WARN, reason stated), never silently.
#   G5 stability         no pooled focal slope and no existing industry's slope flips sign versus the published bundle; leave-one-out
#                        sign consistency >= 80% for every calculator focal term.                                            HOLD
#   G6 non-degradation   HARD backtest gate: no existing industry's calculator-path leave-one-election-out MAE may exceed the published
#                        bundle's on the common folds (new <= published + 1e-9), each share industry under the anchor the auto-pick chose.
#                        A cell on the owner waiver list (registry/gate_waivers.json) is WAIVED, not HOLD; every other cell stays strict.  HOLD
#                        With a cycle_model the `pac` cell scores the selected phase / trajectory candidate and a `general` cell (general giving) joins.
#   G7 bundle            schema-valid, every number finite, every calculator industry has a capability or a stated reason per model. FAIL
#   G8 consumer smoke    the v2-capable Python calculator loads the staged bundle (MENTAT_G8_CMD); HOLD until it exists.
#   G9 vs naive          a calculator path that does not beat last-cycle naive is a WARNING only.

RF_G5_MIN_SIGN <- 0.8
RF_G6_SLACK <- 1e-9

.rf_gate <- function(gate, status, detail) data.frame(gate = gate, status = status, detail = detail, stringsAsFactors = FALSE)

# ---- G4 --------------------------------------------------------------------------------------------------------------------------

# -> list(table = data.frame(industry, model, n, need, pooled_df, interaction_df, ok), withheld = list(industry = list(list(model, reason))), gate)
rf_gate_adequacy <- function(ctx, reg = ctx$reg, min_n = 5L, min_pooled_df = 3L, min_interaction_df = 2L) {
  cyc <- rf_cycle_model_on()
  if (cyc) ctx <- rf_phase_ctx(ctx)
  rows <- list()
  counts <- list(pac = table(ctx$pac_panel$sector), control = table(ctx$share$control_House$sector), lobbying = NULL)
  if (!is.null(ctx$scored)) counts$pac <- c(counts$pac, table(ctx$scored$panel$sector))      # scored-only industries (Telecom) have their own cycles; the pooled df of the pool apply to their step
  lobc <- table(ctx$lobby$diffs$study_sector)
  pooled_df <- c(pac = stats::df.residual(ctx$fits$pac), control = min(stats::df.residual(ctx$fits$control_House), stats::df.residual(ctx$fits$control_Senate)), lobbying = stats::df.residual(ctx$fits$lobby))
  inter_df <- c(pac = NA_integer_, control = NA_integer_, lobbying = NA_integer_)
  specs <- rf_backtest_specs(ctx, phase = cyc)
  inter_df["pac"] <- rf_partial_pool(specs$pac, specs$pac$d)$df_interaction
  inter_df["control"] <- min(rf_partial_pool(specs$control_House.selected, specs$control_House.selected$d)$df_interaction, rf_partial_pool(specs$control_Senate.selected, specs$control_Senate.selected$d)$df_interaction)
  inter_df["lobbying"] <- rf_partial_pool(specs$lobby, specs$lobby$d)$df_interaction
  for (id in names(reg$industries)) {
    i <- reg$industries[[id]]
    if (identical(i$status, "declined")) next
    for (m in c("pac", "control", "lobbying")) {
      if (!id %in% c(rf_capable(reg, m), if (m == "pac") ctx$scored$ids)) next
      n <- if (m == "lobbying") { ls <- i$lobbyview$study_sector; if (ls %in% names(lobc)) as.integer(lobc[[ls]]) else 0L } else { t <- counts[[m]]; if (id %in% names(t)) as.integer(t[[id]]) else 0L }
      ok <- n >= min_n && pooled_df[[m]] >= min_pooled_df && inter_df[[m]] >= min_interaction_df
      rows[[length(rows) + 1L]] <- data.frame(industry = id, model = m, n = n, need = min_n, pooled_df = unname(pooled_df[[m]]), interaction_df = unname(inter_df[[m]]), ok = ok, stringsAsFactors = FALSE)
    }
  }
  tab <- do.call(rbind, rows)
  bad <- tab[!tab$ok, ]
  withheld <- list()
  for (k in seq_len(nrow(bad))) {
    b <- bad[k, ]
    withheld[[b$industry]] <- c(withheld[[b$industry]], list(list(model = b$model,
      reason = sprintf("sample adequacy: %d %s (need %d), pooled residual df %d (need %d), interaction df %d (need %d)", b$n, if (b$model == "lobbying") "annual pairs" else "cycles",
                       b$need, b$pooled_df, min_pooled_df, b$interaction_df, min_interaction_df))))
  }
  list(table = tab, withheld = withheld,
       gate = .rf_gate("G4 sample adequacy", if (nrow(bad)) "WARN" else "PASS",
                       if (nrow(bad)) paste("withheld:", paste(sprintf("%s/%s", bad$industry, bad$model), collapse = ", ")) else sprintf("%d industry-model cells adequate", nrow(tab))))
}

# ---- G5 --------------------------------------------------------------------------------------------------------------------------

.rf_forecast_beta <- function(block) { k <- match("Forecast", unlist(block$terms)); if (is.na(k)) NA_real_ else unlist(block$beta)[k] }

# Signs of the published bundle's pooled focal slopes (v1 or v2): list(pac, control_House, control_Senate, lobby).
rf_published_signs <- function(published) {
  if (is.null(published)) return(NULL)
  list(pac = published$pac_model$beta, control_House = .rf_forecast_beta(published$share_models$control_House), control_Senate = .rf_forecast_beta(published$share_models$control_Senate),
       lobby = published$lobby_model$beta_million, pac_slopes = if (!is.null(published$pac_model$slopes)) vapply(published$pac_model$slopes, function(s) s$applied$beta, 0) else NULL)
}

# -> list(table, gate)
rf_gate_stability <- function(v, bundle, published = NULL, min_share = RF_G5_MIN_SIGN) {
  rows <- list()
  add <- function(check, ok, detail) rows[[length(rows) + 1L]] <<- data.frame(check = check, ok = ok, detail = detail, stringsAsFactors = FALSE)
  ps <- rf_published_signs(published)
  new <- list(pac = bundle$pac_model$beta, control_House = .rf_forecast_beta(bundle$share_models$control_House), control_Senate = .rf_forecast_beta(bundle$share_models$control_Senate),
              lobby = bundle$lobby_model$beta_million)
  if (!is.null(ps)) {
    for (nm in names(new)) add(paste("pooled slope sign:", nm), sign(new[[nm]]) == sign(ps[[nm]]), sprintf("new %.6g, published %.6g", new[[nm]], ps[[nm]]))
    add("lobbying log slope sign vs published dollar slope", sign(bundle$lobby_model$log_refit$beta) == sign(ps$lobby), sprintf("log slope %.6g, published dollar slope %.6g", bundle$lobby_model$log_refit$beta, ps$lobby))
    for (id in names(bundle$pac_model$slopes)) {
      ref <- if (!is.null(ps$pac_slopes) && id %in% names(ps$pac_slopes)) ps$pac_slopes[[id]] else ps$pac
      add(paste("existing industry slope sign: PAC", id), sign(bundle$pac_model$slopes[[id]]$applied$beta) == sign(ref),
          sprintf("new %.6g, published %s %.6g", bundle$pac_model$slopes[[id]]$applied$beta, if (!is.null(ps$pac_slopes) && id %in% names(ps$pac_slopes)) "own slope" else "pooled slope", ref))
    }
  }
  sc <- list(pac = v$sign$pac$loeo$share, control_House = v$sign$control_House$loeo$share, control_Senate = v$sign$control_Senate$loeo$share,
             lobby = v$sign$lobby$loeo$share, lobby_log = v$sign$lobby_log$loeo)
  for (nm in names(sc)) add(paste("leave-one-election-out sign consistency:", nm), sc[[nm]] >= min_share - 1e-12, sprintf("%.0f%% of folds keep the sign (need %.0f%%)", 100 * sc[[nm]], 100 * min_share))
  tab <- do.call(rbind, rows)
  bad <- tab[!tab$ok, ]
  list(table = tab, gate = .rf_gate("G5 stability", if (nrow(bad)) "HOLD" else "PASS", if (nrow(bad)) paste(sprintf("%s (%s)", bad$check, bad$detail), collapse = "; ") else sprintf("%d sign checks hold", nrow(tab))))
}

# ---- G6 --------------------------------------------------------------------------------------------------------------------------

# Per-industry leave-one-election-out MAE of a backtest cell: named numeric by industry (NA when the industry has no rows).
.rf_cell_mae <- function(bt, term, base, design, path, rule) {
  r <- bt[bt$term == term & bt$base == base & bt$design == design & bt$path == path & bt$rule == rule, ]
  if (!nrow(r)) return(numeric())
  cols <- grep("^MAE_", names(r), value = TRUE)
  stats::setNames(unlist(r[1, cols]), sub("^MAE_", "", cols))
}

# The currently published calculator's per-industry MAE. For v1 (no backtests in the bundle): the v1 calculator (pooled slope, same-phase
# anchor, calc path) on the same folds and scaling. For a v2 bundle with backtests: its recorded MAE_by_industry of the published rule/path.
rf_published_baseline <- function(v, published = NULL) {
  bt <- v$baseline_backtests %||% v$backtests      # the frozen sample's backtests when the v2 sample holds pilot industries
  pe <- published$pac_model$evidence$calculator_path_loeo_mae_by_industry
  if (!is.null(published) && !is.null(pe)) {                     # a v2 bundle records the MAE of the path it publishes
    g <- function(x) unlist(x %||% list())
    return(list(pac = g(pe), control_House = g(published$share_models$control_House$evidence$calculator_path_loeo_mae_by_industry),
                control_Senate = g(published$share_models$control_Senate$evidence$calculator_path_loeo_mae_by_industry),
                lobby = g(published$lobby_model$evidence$calculator_path_mae_by_industry), source = "published bundle (recorded calculator-path MAE)"))
  }
  list(pac = .rf_cell_mae(bt, "pac", "bundle", "loeo", "calc", "R0"), control_House = .rf_cell_mae(bt, "control_House", "selected", "loeo", "calc", "R0"),
       control_Senate = .rf_cell_mae(bt, "control_Senate", "selected", "loeo", "calc", "R0"), lobby = .rf_cell_mae(bt, "lobby", "bundle", "rolling", "calc", "R0"),
       source = if (!is.null(v$baseline_backtests)) "v1 calculator recomputed on the same folds of the frozen v1 sample (pooled slope, same-phase anchor, calc path)"
                else "v1 calculator recomputed on the same folds (pooled slope, same-phase anchor, calc path)")
}

# The owner waiver list (registry/gate_waivers.json): cells of G6 the owner has ruled on. A waived cell is WAIVED, not HOLD; everything else
# stays strict. -> data.frame(gate, cell, term, industry, reason, owner, date, show_warning); zero rows when the file is absent.
rf_read_waivers <- function(path = file.path(rf_home(), "registry", "gate_waivers.json")) {
  empty <- data.frame(gate = character(), cell = character(), term = character(), industry = character(), reason = character(), owner = character(), date = character(),
                      show_warning = logical(), stringsAsFactors = FALSE)
  if (!file.exists(path)) return(empty)
  w <- rf_read_json(path)$waivers
  if (!length(w)) return(empty)
  do.call(rbind, lapply(w, function(x) {
    need <- c("cell", "term", "industry", "reason", "owner", "date", "show_warning")
    miss <- setdiff(need, names(x))
    if (length(miss)) stop(sprintf("gate_waivers.json: waiver '%s' lacks %s", x$cell %||% "?", paste(miss, collapse = ", ")), call. = FALSE)
    if (!isTRUE(x$show_warning) && !identical(x$show_warning, FALSE)) stop("gate_waivers.json: show_warning must be true or false", call. = FALSE)
    data.frame(gate = x$gate %||% "G6", cell = x$cell, term = x$term, industry = x$industry, reason = x$reason, owner = x$owner, date = x$date, show_warning = x$show_warning, stringsAsFactors = FALSE)
  }))
}

# -> list(table = data.frame(term, industry, new_MAE, published_MAE, ok, waived), waivers = the listed waivers with whether each applied, gate)
#    A failing cell listed in `waivers` is WAIVED (waived = TRUE, ok stays FALSE); the gate is HOLD while any other cell fails, WARN when every failing cell
#    is waived and at least one waiver has show_warning, else PASS.
#    cycle = TRUE (a bundle with a cycle_model; default when the context holds the phase block): the PAC projection is the consistency rule's selected phase / trajectory candidate, so the `pac` cell's
#    new MAE is that candidate's leave-one-election-out MAE on the rows it shares with the published rule PUB-R1 (REPORT table 3a), and a `general` cell joins: the selected general-giving candidate
#    against the published form PUBPAC-R1 on their common rows (table 3b). The published PAC baseline stays the published calculator's recorded MAE (= PUB-R1).
rf_gate_nondegradation <- function(v, published = NULL, existing = NULL, slack = RF_G6_SLACK, waivers = rf_read_waivers(), cycle = !is.null(v$phase)) {
  ctx <- v$ctx
  base <- rf_published_baseline(v, published)
  bt <- v$backtests
  rule <- c(pac = v$selection$pac$reported_rule, control_House = v$selection$control_House$reported_rule, control_Senate = v$selection$control_Senate$reported_rule)
  chosen <- function(id) if (!is.null(v$anchors)) rf_chosen_anchor_mae(bt, v$anchors, id, "loeo", rule[[id]]) else .rf_cell_mae(bt, id, "selected", "loeo", "prevchg", rule[[id]])
  new <- list(pac = .rf_cell_mae(bt, "pac", "bundle", "loeo", "prevchg", rule[["pac"]]),
              control_House = chosen("control_House"), control_Senate = chosen("control_Senate"),
              lobby = .rf_cell_mae(bt, "lobby", "log", "rolling", "calc", "R0"))
  if (cycle) {
    if (is.null(v$phase)) stop("rf_gate_nondegradation: cycle = TRUE needs the phase block of the context", call. = FALSE)
    loeo <- function(nm) { s <- v$phase[[nm]]$consistency$selection; t <- s$industry; t[t$candidate == s$choice & t$design == "loeo", , drop = FALSE] }
    p <- loeo("pac"); g <- loeo("general")
    new$pac <- stats::setNames(p$MAE, p$sector)
    new$general <- stats::setNames(g$MAE, g$sector); base$general <- stats::setNames(g$MAE_base, g$sector)
    base$source <- sprintf("%s; cycle model: PAC = %s, general giving = %s against %s on common rows", base$source, v$phase$pac$consistency$selection$choice,
                           v$phase$general$consistency$selection$choice, v$phase$general$consistency$baseline)
  }
  existing <- existing %||% (if (!is.null(published)) names(published$industries) else names(ctx$reg$industries)[vapply(ctx$reg$industries, function(i) i$status == "fitted", logical(1))])
  study <- stats::setNames(vapply(ctx$reg$industries, function(i) i$lobbyview$study_sector %||% NA_character_, ""), names(ctx$reg$industries))
  rows <- list()
  for (term in names(new)) for (id in existing) {
    key <- if (term == "lobby") study[[id]] else id
    if (is.na(key) || !(key %in% names(new[[term]])) || !(key %in% names(base[[term]]))) next
    a <- new[[term]][[key]]; b <- base[[term]][[key]]
    if (is.na(a) || is.na(b)) next
    rows[[length(rows) + 1L]] <- data.frame(term = term, industry = id, new_MAE = a, published_MAE = b, ok = a <= b + slack, stringsAsFactors = FALSE)
  }
  tab <- do.call(rbind, rows)
  w <- waivers[waivers$gate == "G6", , drop = FALSE]
  tab$waived <- !tab$ok & paste(tab$term, tab$industry) %in% paste(w$term, w$industry)
  w$applied <- paste(w$term, w$industry) %in% paste(tab$term[tab$waived], tab$industry[tab$waived])
  w$new_MAE <- tab$new_MAE[match(paste(w$term, w$industry), paste(tab$term, tab$industry))]
  w$published_MAE <- tab$published_MAE[match(paste(w$term, w$industry), paste(tab$term, tab$industry))]
  bad <- tab[!tab$ok & !tab$waived, ]
  shown <- w[w$applied & w$show_warning, ]
  status <- if (nrow(bad)) "HOLD" else if (nrow(shown)) "WARN" else "PASS"
  detail <- if (nrow(bad)) paste(sprintf("%s / %s: new %.3f > published %.3f", bad$term, bad$industry, bad$new_MAE, bad$published_MAE), collapse = "; ")
            else if (nrow(shown)) sprintf("%d cells no worse than the published calculator (%s); waived by the owner: %s", sum(tab$ok), base$source,
                                          paste(sprintf("%s (%s)", shown$cell, shown$reason), collapse = "; "))
            else sprintf("%d existing industry-term cells no worse than the published calculator (%s)", sum(tab$ok), base$source)
  if (nrow(bad) && nrow(shown)) detail <- paste0(detail, "; also waived: ", paste(shown$cell, collapse = "; "))
  list(table = tab, waivers = w, baseline = base$source, gate = .rf_gate("G6 non-degradation", status, detail))
}

# ---- G7, G8, G9 ------------------------------------------------------------------------------------------------------------------

rf_gate_bundle <- function(bundle, reg, schema_path = rf_schema_default_path()) {
  problems <- rf_validate_bundle(bundle, schema_path)
  for (id in names(bundle$industries)) {
    if (!isTRUE(bundle$industries[[id]]$calculator)) next
    for (m in c("pac", "control", "lobbying")) {
      ok <- isTRUE(bundle$industries[[id]]$capabilities[[m]]) || nzchar(bundle$industries[[id]]$withheld[[m]] %||% "")
      if (!ok) problems <- c(problems, sprintf("industry %s: capability '%s' is false with no withheld reason", id, m))
    }
  }
  .rf_gate("G7 bundle", if (length(problems)) "FAIL" else "PASS", if (length(problems)) paste(utils::head(problems, 6), collapse = "; ") else "schema-valid, all numbers finite, every calculator industry has a capability or a reason per model")
}

# The command that runs the consumer smoke: MENTAT_G8_CMD when set, else the pipeline's own tools/g8_consumer_smoke.py when the console's calculator
# (mentat/app/giving_forecast.py) is next to the pipeline (a pod snapshot carries mentat/forecast only, so there it is "" and G8 holds).
rf_g8_default_cmd <- function() {
  env <- Sys.getenv("MENTAT_G8_CMD", unset = "")
  if (nzchar(env)) return(env)
  script <- file.path(rf_home(), "tools", "g8_consumer_smoke.py")
  app <- file.path(dirname(dirname(rf_home())), "app", "giving_forecast.py")
  py <- Sys.which("python"); if (!nzchar(py)) py <- Sys.which("python3")
  if (file.exists(script) && file.exists(app) && nzchar(py)) sprintf("%s %s", shQuote(normalizePath(py, winslash = "/")), shQuote(normalizePath(script, winslash = "/"))) else ""
}

# Run `code` with environment variables set in THIS process, then restore each previous value (or remove the variable). A child process inherits them on
# every platform; system2(env = ) does not work on Windows, where R puts VAR=value after the command and bash reads it as a script name (exit status 127).
rf_with_env <- function(vars, code) {
  old <- Sys.getenv(names(vars), unset = NA, names = TRUE)
  on.exit({
    gone <- names(old)[is.na(old)]
    if (length(gone)) Sys.unsetenv(gone)
    kept <- old[!is.na(old)]
    if (length(kept)) do.call(Sys.setenv, as.list(kept))
  }, add = TRUE)
  do.call(Sys.setenv, as.list(vars))
  code
}

# The bash that runs the G8 command. On Windows `bash` on the PATH can be the WSL launcher (C:\Windows\System32\bash.exe), which does not run a Windows command
# line, and from a plain Windows shell it is often not on the PATH at all; the command is written for Git for Windows' bash.exe, which is found from the git
# installation (git.exe sits in <Git>/cmd, <Git>/bin or <Git>/mingw64/bin) or the default install folders. <Git>/bin/bash.exe is preferred: it is the launcher that
# sets up PATH, so a command that uses cat, head or tr works; <Git>/usr/bin/bash.exe, started straight from R, has no /usr/bin on its PATH and is the fallback.
# "" when there is none (G8 then holds, with the reason).
rf_g8_bash <- function(found = Sys.which("bash"), git = Sys.which("git"), windows = .Platform$OS.type == "windows") {
  found <- unname(found)
  if (!windows) return(if (nzchar(found)) found else "bash")
  norm <- function(p) normalizePath(p, winslash = "/", mustWork = FALSE)
  wsl <- nzchar(found) && grepl("/(windows/(system32|sysnative)|microsoft/windowsapps)/bash\\.exe$", tolower(norm(found)))
  if (nzchar(found) && !wsl) return(norm(found))
  roots <- character()
  git <- unname(git)
  if (nzchar(git)) { d <- dirname(norm(git)); roots <- c(d, dirname(d), dirname(dirname(d))) }
  lad <- Sys.getenv("LOCALAPPDATA")
  pf <- c(Sys.getenv(c("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")), if (nzchar(lad)) file.path(lad, "Programs"))
  roots <- unique(c(roots, file.path(pf[nzchar(pf)], "Git")))
  cand <- c(file.path(roots, "bin/bash.exe"), file.path(roots, "usr/bin/bash.exe"))        # every launcher first, the plain usr/bin bash only when there is none
  hit <- cand[file.exists(cand)]
  if (length(hit)) norm(hit[[1]]) else ""
}

# The staged bundle is loaded by the v2-capable calculator: the command runs with MENTAT_GIVING_MODELS set to the path (MENTAT_G8_CMD, or the default
# above: tools/g8_consumer_smoke.py, which loads the bundle through giving_forecast and projects every offered industry); exit status 0 = PASS. Without a command: HOLD.
rf_gate_consumer <- function(bundle_path, cmd = rf_g8_default_cmd()) {
  if (!nzchar(cmd)) return(.rf_gate("G8 consumer smoke", "HOLD", "no v2-capable calculator reachable (MENTAT_G8_CMD unset and mentat/app/giving_forecast.py not beside the pipeline): HOLD until the v2 calculator exists"))
  bash <- rf_g8_bash()
  if (!nzchar(bash)) return(.rf_gate("G8 consumer smoke", "HOLD", "no bash to run the G8 command (on Windows: Git for Windows' bash.exe; the WSL launcher in System32 does not count): HOLD until one is installed"))
  out <- suppressWarnings(rf_with_env(c(MENTAT_GIVING_MODELS = bundle_path), system2(bash, c("-c", shQuote(cmd)), stdout = TRUE, stderr = TRUE)))
  st <- attr(out, "status") %||% 0L
  .rf_gate("G8 consumer smoke", if (st == 0L) "PASS" else "HOLD", sprintf("exit status %d: %s", st, paste(utils::tail(out, 2), collapse = " | ")))
}

# Both designs of the rulings of 2026-09-30 (leave-one-election-out AND chronological): a path that loses to last-period naive in either one
# is reported. Warning only, as before; the chronological design is the one that matches how the calculator is used (data up to the anchor).
# With a cycle_model (cycle = TRUE) the PAC path is the selected phase / trajectory candidate and general giving is a calculator path too: each is compared with last-cycle naive on the rows it
# shares with the published rule (REPORT tables 3a, 3b) instead of the retired PUB-R1 prevchg cell.
rf_gate_naive <- function(v, designs = c("loeo", "chrono"), cycle = !is.null(v$phase)) {
  bt <- v$backtests
  rule <- c(pac = v$selection$pac$reported_rule, control_House = v$selection$control_House$reported_rule, control_Senate = v$selection$control_Senate$reported_rule)
  path_of <- function(id) if (!is.null(v$anchors) && all(vapply(v$anchors$entries[[id]], function(e) identical(e$anchor, "same_phase"), logical(1)))) "calc" else "prevchg"
  cells <- list()
  for (ds in designs) {
    if (!cycle) cells[[paste0("pac/", ds)]] <- c("pac", "bundle", ds, "prevchg", rule[["pac"]])
    cells[[paste0("control_House/", ds)]] <- c("control_House", "selected", ds, path_of("control_House"), rule[["control_House"]])
    cells[[paste0("control_Senate/", ds)]] <- c("control_Senate", "selected", ds, path_of("control_Senate"), rule[["control_Senate"]])
  }
  cells[["lobby_log/rolling"]] <- c("lobby", "log", "rolling", "calc", "R0")
  lose <- character()
  if (cycle) for (nm in c("pac", "general")) {
    b <- v$phase[[nm]]; ch <- b$consistency$selection$choice
    pt <- rf_cons_pooled(b$pred, b$consistency$baseline, c(ch, "naive_prev"), designs)
    for (ds in designs) {
      a <- pt[pt$candidate == ch & pt$design == ds, ]; n <- pt[pt$candidate == "naive_prev" & pt$design == ds, ]
      if (nrow(a) && nrow(n) && a$MAE >= n$MAE) lose <- c(lose, sprintf("%s/%s (%.3f vs naive %.3f)", if (nm == "pac") "pac" else "general_giving", ds, a$MAE, n$MAE))
    }
  }
  for (nm in names(cells)) {
    c <- cells[[nm]]
    r <- bt[bt$term == c[1] & bt$base == c[2] & bt$design == c[3] & bt$path == c[4] & bt$rule == c[5], ]
    nv <- bt[bt$term == c[1] & bt$base == c[2] & bt$design == c[3] & bt$path == c[4] & bt$rule == "naive_prev", ]
    if (nrow(r) && nrow(nv) && r$MAE >= nv$MAE) lose <- c(lose, sprintf("%s (%.3f vs naive %.3f)", nm, r$MAE, nv$MAE))
  }
  .rf_gate("G9 vs naive", if (length(lose)) "WARN" else "PASS", if (length(lose)) paste("does not beat last-period naive:", paste(lose, collapse = "; ")) else "every calculator path beats last-period naive")
}

# ---- decision ------------------------------------------------------------------------------------------------------------------------

rf_decision <- function(gates) if (any(gates$status == "FAIL")) "FAIL" else if (any(gates$status == "HOLD")) "HOLD" else "PUBLISH"

# All gates of a refit. `base` = data.frame of G0-G3 (from the run). -> list(gates, g4, g5, g6)
rf_gates <- function(v, bundle, published = NULL, base = NULL, bundle_path = NULL, consumer_cmd = rf_g8_default_cmd()) {
  g4 <- v$adequacy %||% rf_gate_adequacy(v$ctx)
  g5 <- rf_gate_stability(v, bundle, published)
  g6 <- rf_gate_nondegradation(v, published)
  g7 <- rf_gate_bundle(bundle, v$ctx$reg)
  g8 <- if (is.null(bundle_path)) .rf_gate("G8 consumer smoke", "HOLD", "bundle not staged on disk") else rf_gate_consumer(bundle_path, consumer_cmd)
  g9 <- rf_gate_naive(v)
  gates <- rbind(base, g4$gate, g5$gate, g6$gate, g7, g8, g9)
  rownames(gates) <- NULL
  list(gates = gates, g4 = g4, g5 = g5, g6 = g6, decision = rf_decision(gates))
}
