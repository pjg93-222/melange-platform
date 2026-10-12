# Bundle schema v2 (Phase 2), written against the shared contract in mentat/forecast/contracts (brain DE-021): the schema, the example and
# the negative controls there are the contract; nothing in this file may add a key the schema does not name (objects are closed).
# rf_v2_context() computes everything a refit needs once; rf_bundle_v2() writes it; rf_gates.R judges it against the published bundle.
# Model content: own-slope PAC through the auto-pick with the previous-cycle anchor, coefficient and prediction intervals beside each other,
# seats as evidence only, Polymarket-to-Polymarket control inputs, the lobbying LOG refit (lobby_model.log_refit), the anchor contract.

RF_V2_TEXT <- list(
  inference = paste("Ordinary OLS p-values are primary; cluster-robust, CR2 and wild-bootstrap p-values are reported beside them. Each slope rule",
                    "(pooled R0, own R1, DerSimonian-Laird blend R2) is chosen per term by no-leakage backtests; variable selection is frozen and diagnostic only."),
  validation = paste("Historical associations. A coefficient interval covers the estimated slope only; a prediction interval adds the typical",
                     "error of the calculator's own one-cycle step measured on history (anchored-step residual)."),
  coefficient = "Ordinary 95% coefficient interval",
  prediction = "95% prediction interval (adds the anchored-step residual variance)")

# ---- evidence helpers ---------------------------------------------------------------------------------------------------------

# p-values of a robustness table as a flat record: ordinary_p, HC1_p, CR1_<cluster>_p, CR2_<cluster>_p, wild_webb_<cluster>_p, wild_rademacher_<cluster>_p.
rf_evidence_record <- function(tab) {
  nm <- function(method) { method <- sub("^wildWebb_", "wild_webb_", method); method <- sub("^wildRademacher_", "wild_rademacher_", method); paste0(method, "_p") }
  out <- stats::setNames(as.list(tab$p), nm(tab$method))
  dr <- tab$draws[!is.na(tab$draws)]
  out$draws <- stats::setNames(as.list(as.integer(dr)), nm(tab$method[!is.na(tab$draws)]))
  out
}

# Share of leave-one-out folds whose focal estimate keeps the sign of the full-sample estimate (folds with an estimate only).
rf_fold_sign_consistency <- function(m, d, var, key) {
  fr <- rf_drop_folds(m, d, var, key)
  full <- unname(stats::coef(m)[key])
  e <- fr$estimate[!is.na(fr$estimate)]
  list(folds = length(e), same_sign = sum(sign(e) == sign(full)), share = mean(sign(e) == sign(full)))
}

# ---- the context -----------------------------------------------------------------------------------------------------------------

# Everything the v2 bundle and its gates need, built from the parity context (panels, fits, stepwise grid). `b_random` = draws of the
# random wild-bootstrap blocks (full production 99,999).
#
# `frozen_ctx`: when the v2 sample holds pilot industries beyond the frozen v1 sample, the context of the frozen sample. The v1 calculator the gates compare against
# (G6 baseline) was fitted on the frozen sample, so its per-industry backtest MAE is recomputed there (`baseline_backtests`), never on the extended sample.
rf_v2_context <- function(ctx, run_id = "unit", packages = NULL, built_at_utc = rf_now_utc(), b_random = RF$B_RANDOM, min_gain = RF$RULE_MIN_GAIN, frozen_ctx = NULL) {
  cyc <- rf_cycle_model_on()                                # RF_CYCLE_MODEL=0: the bundle as published before the cycle-aware 50/50 model (parity switch)
  if (cyc) ctx <- rf_phase_ctx(ctx)                         # v2 layer: the Senate share model carries Pres (owner rulings 2026-10-01 and 2026-10-07); the frozen v1 fits stay in ctx0 / the parity layer
  ms <- ctx$models$models
  specs <- rf_backtest_specs(ctx, phase = cyc)
  selection <- rf_select_rules(specs, min_gain = min_gain)
  pac <- rf_pac_v2(specs$pac, selection$pac)
  vm <- rf_v1_models(ctx)
  shares <- list()
  for (ch in c("House", "Senate")) {
    id <- paste0("control_", ch)
    sp <- specs[[paste0(id, ".selected")]]
    shares[[id]] <- list(block = vm$share_models[[id]], rule = selection[[id]]$reported_rule, pp = rf_partial_pool(sp, sp$d), prediction = rf_share_prediction(sp),
                         prediction_same_phase = rf_share_prediction(sp, "same_phase_strict"), spec = sp)
  }
  lob <- ctx$lobby$diffs
  lob_log_fit <- rf_lobby_log_fit(lob)
  lob_log <- rf_lobby_log_block(lob_log_fit, lob)
  lob_spec <- specs$lobby
  lob_pp <- rf_partial_pool(lob_spec, lob_spec$d)
  rb <- function(id, m, d, key, clusters) rf_robustness_block(id, m, d, key, clusters, NULL, b_random = b_random)$table
  ev <- list(
    pac = rb("pac", ctx$fits$pac, ctx$fits_data$pac, "G_current", unlist(ms$pac$clusters)),
    control_House = rb("control_House", ctx$fits$control_House, ctx$fits_data$control_House, "P_R", unlist(ms$control_House$clusters)),
    control_Senate = rb("control_Senate", ctx$fits$control_Senate, ctx$fits_data$control_Senate, "P_R", unlist(ms$control_Senate$clusters)),
    lobby = rb("lobby", ctx$fits$lobby, ctx$fits_data$lobby, "dg", unlist(ms$lobby$clusters)),
    lobby_log = rb("lobby_log", lob_log_fit, rf_lobby_log_panel(lob), "dg", c("study_sector", "year")))
  sign <- list(
    pac = list(loeo = rf_fold_sign_consistency(ctx$fits$pac, ctx$fits_data$pac, "cycle_end", "G_current"), loco = rf_fold_sign_consistency(ctx$fits$pac, ctx$fits_data$pac, "sector", "G_current")),
    control_House = list(loeo = rf_fold_sign_consistency(ctx$fits$control_House, ctx$fits_data$control_House, "cycle_end", "P_R")),
    control_Senate = list(loeo = rf_fold_sign_consistency(ctx$fits$control_Senate, ctx$fits_data$control_Senate, "cycle_end", "P_R")),
    lobby = list(loeo = rf_fold_sign_consistency(ctx$fits$lobby, ctx$fits_data$lobby, "year", "dg")),
    lobby_log = rf_lobby_log_sign_consistency(lob_log_fit, lob))
  cells <- list()
  for (key in c("pac", "control_House.selected", "control_Senate.selected")) for (ds in c("loeo", "chrono")) cells[[length(cells) + 1L]] <- rf_backtest_metrics(rf_backtest(specs[[key]], specs[[key]]$d, ds))
  cells[[length(cells) + 1L]] <- rf_backtest_metrics(rf_backtest(lob_spec, lob_spec$d, "rolling"))
  lg_pred <- rf_lobby_log_backtest(lob)
  cells[[length(cells) + 1L]] <- rf_backtest_metrics(lg_pred)
  backtests <- rf_rbind_fill(cells)
  anchors <- rf_select_anchors(backtests, selection, lapply(shares, function(s) levels(factor(s$spec$d$sector))), min_gain)
  baseline_bt <- NULL
  if (!is.null(frozen_ctx)) {
    fs <- rf_backtest_specs(frozen_ctx)
    bl <- list(rf_backtest_metrics(rf_backtest(fs$pac, fs$pac$d, "loeo", rules = "R0")),
               rf_backtest_metrics(rf_backtest(fs$control_House.selected, fs$control_House.selected$d, "loeo", rules = "R0")),
               rf_backtest_metrics(rf_backtest(fs$control_Senate.selected, fs$control_Senate.selected$d, "loeo", rules = "R0")),
               rf_backtest_metrics(rf_backtest(fs$lobby, fs$lobby$d, "rolling", rules = "R0")))
    baseline_bt <- rf_rbind_fill(bl)
  }
  phase <- if (cyc) rf_phase_block(ctx, selection$pac$reported_rule) else NULL
  raw <- ctx$inputs[ctx$inputs$purpose == "raw", ]
  input_hash <- substr(rf_sha256_of_strings(raw$actual), 1, 8)
  list(ctx = ctx, phase = phase, frozen_ctx = frozen_ctx, baseline_backtests = baseline_bt, specs = specs, selection = selection, anchors = anchors, pac = pac, shares = shares, lobby_pp = lob_pp, lobby_log_fit = lob_log_fit, lobby_log = lob_log,
       cycle_model_on = cyc, evidence = ev, sign = sign, backtests = backtests, lobby_log_pred = lg_pred, run_id = run_id, packages = packages, built_at_utc = built_at_utc,
       model_version = rf_model_version(input_hash, run_id, built_at_utc, rf_code_sha256()), input_hash = input_hash, min_gain = min_gain,
       adequacy = NULL)
}

# Different builds of the same input vintage need distinct immutable publication identities.
rf_model_version <- function(input_hash, run_id, built_at_utc, code_sha256) {
  build_hash <- substr(rf_sha256_of_strings(c(run_id, built_at_utc, code_sha256)), 1, 12)
  sprintf("refit-%s-%s-%s", format(as.Date(substr(built_at_utc, 1, 10)), "%Y%m%d"), input_hash, build_hash)
}

# ---- blocks ---------------------------------------------------------------------------------------------------------------------------

# Per-industry LOEO MAE of a backtest cell as a named list (evidence: what a later refit's G6 compares against).
.rf_mae_record <- function(bt, term, base, design, path, rule) as.list(.rf_cell_mae(bt, term, base, design, path, rule))

rf_v2_industries <- function(v) {
  ctx <- v$ctx; reg <- ctx$reg
  p <- ctx$pac_panel
  sc <- ctx$scored                                                          # scored-only industries (Telecom): their own panel, never part of the pooled estimation
  lv <- ctx$lobby$levels
  out <- list()
  for (id in names(reg$industries)) {
    i <- reg$industries[[id]]
    if (!(isTRUE(i$calculator) || id %in% rf_capable(reg, "pac"))) next
    scored <- !is.null(sc) && id %in% sc$ids
    caps <- list(pac = id %in% rf_capable(reg, "pac") || (scored && !is.null(v$phase)), control = id %in% rf_capable(reg, "control"), lobbying = id %in% rf_capable(reg, "lobbying"))      # a scored industry is scored on the cycle model's pooled phase and drift
    withheld <- stats::setNames(list(), character(0))                       # written as {} (an empty object), never []
    for (m in c("pac", "control", "lobbying")) if (!caps[[m]]) withheld[[m]] <- if (m == "pac" && scored) "Scored on the cycle model's pooled phase and drift (RF_CYCLE_MODEL=1); this bundle was built without the cycle model." else rf_withheld_reason(i, m)
    for (w in v$adequacy$withheld[[id]] %||% list()) { caps[[w$model]] <- FALSE; withheld[[w$model]] <- w$reason }
    pp <- if (scored) sc$panel else p
    g <- pp[pp$sector == id, ]; g <- g[order(g$cycle_end), ]
    has <- nrow(g) > 0L
    ls <- i$lobbyview$study_sector
    lh <- if (!is.null(ls)) lv[lv$study_sector == ls, ] else lv[0, ]
    ind <- list(id = id, label = i$label, code = NULL, opensecrets_codes = as.list(unlist(i$opensecrets$codes)), mentat_key = NULL, ibisworld_code = NULL, lobbyview_sector = NULL,
      calculator = isTRUE(i$calculator), lobbying_only = isTRUE(i$lobbying_only), growth_mean = NULL, growth_sd = NULL, growth_min = NULL, growth_max = NULL,
      history = lapply(seq_len(nrow(g)), function(k) list(cycle = as.integer(g$cycle_end[k]), growth_pct = g$cycle_revenue_growth_pct[k],
        prior_growth_pct = g$prior_cycle_revenue_growth_pct[k], pac_real_2025 = g$pac_real_2025[k], individual_real_2025 = g$individual_real_2025[k],
        total_real_2025 = g$total_real_2025[k], source_url = g$source_url[k])),
      scope = if (length(i$opensecrets$codes)) sprintf("OpenSecrets %s whole-industry accounting; separate from company/subindustry report scopes", i$opensecrets$codes[[1]]) else "no OpenSecrets capture",
      capabilities = caps, withheld = withheld, growth_scaling_cycles = as.list(as.integer(g$cycle_end)),
      lobbying_history = lapply(seq_len(nrow(lh)), function(k) list(year = as.integer(lh$year[k]), lobbying_real_2025_million = lh$y[k], predicted_growth_pct = lh$g[k])))
    cav <- as.list(unlist(i$caveats %||% list()))            # scope caveats of the registry (property and casualty: OpenSecrets F09 = all insurance)
    est <- attr(pp, "estimates")
    if (!is.null(est) && id %in% est$sector) {                # IBISWorld current-year estimates that entered this industry's growth series
      yrs <- sort(est$year[est$sector == id])
      txt <- i$ibisworld$actual_current_year$caveat %||% sprintf("%s revenue is IBISWorld's current-year estimate; replaced automatically when a later edition is ingested.", paste(yrs, collapse = ", "))
      cav <- c(cav, list(txt))
    }
    if (length(cav)) ind$caveats <- cav
    if (length(i$opensecrets$codes)) ind$code <- i$opensecrets$codes[[1]] else ind["code"] <- list(NULL)
    if (!is.null(i$mentat_key)) ind$mentat_key <- i$mentat_key else ind["mentat_key"] <- list(NULL)
    if (!is.null(i$ibisworld$code)) ind$ibisworld_code <- i$ibisworld$code else ind["ibisworld_code"] <- list(NULL)
    if (!is.null(ls)) ind$lobbyview_sector <- ls else ind["lobbyview_sector"] <- list(NULL)
    if (has) { ind$growth_mean <- mean(g$cycle_revenue_growth_pct); ind$growth_sd <- stats::sd(g$cycle_revenue_growth_pct); ind$growth_min <- min(g$cycle_revenue_growth_pct); ind$growth_max <- max(g$cycle_revenue_growth_pct) }
    else for (k in c("growth_mean", "growth_sd", "growth_min", "growth_max")) ind[k] <- list(NULL)
    out[[id]] <- ind
  }
  out
}

rf_v2_pac_model <- function(v) {
  ctx <- v$ctx; m1 <- rf_v1_models(ctx)$pac_model; f <- ctx$fits$pac
  pac <- v$pac
  ev <- rf_evidence_record(v$evidence$pac)
  ev$calculator_path <- "prevchg"
  ev$calculator_path_loeo_mae_by_industry <- .rf_mae_record(v$backtests, "pac", "bundle", "loeo", "prevchg", pac$rule)
  ev$sign_consistency <- list(leave_one_election_out = v$sign$pac$loeo$share, leave_one_industry_out = v$sign$pac$loco$share)
  ev$rule_selection <- rf_v2_rule_selection(v$selection$pac)
  if (!is.null(v$phase)) {
    ev$phase <- rf_v2_phase_evidence(v)
    if (!length(rf_cycle_model_gap(v))) ev$phase$status <- "evidence behind the bundle's cycle_model: the calculator applies the selected specifications through cycle_model (phase, trajectory, inflation, BLEND, the 50/50 budget rule); this record is not read by it"
  }
  list(beta = m1$beta, ordinary_se = m1$ordinary_se, ordinary_p = m1$ordinary_p, critical_95 = m1$critical_95, n = m1$n, cycles = m1$cycles, specification = m1$specification,
       selection_repeated_p = m1$selection_repeated_p, formula = "log_pac ~ sector + factor(cycle_end) + G_current", industries = as.list(levels(ctx$fits_data$pac$sector)),
       residual_df = as.integer(stats::df.residual(f)), residual_sd = summary(f)$sigma, slope_rule = pac$rule, slope_rule_reason = pac$reason, tau2 = pac$tau2, Q = pac$Q,
       k = as.integer(pac$k), df_interaction = pac$df_interaction, slopes = pac$slopes, anchor_rule = "previous_cycle", prediction = pac$prediction, evidence = ev)
}

rf_v2_rule_selection <- function(s) list(choice = s$choice, reported_rule = s$reported_rule, path = s$path, designs = as.list(s$designs), min_gain = s$min_gain,
                                        qualifying = as.list(s$qualifying), resolved_to_pooled = s$resolved_to_pooled, reason = s$reason,
                                        table = rf_records(s$table, ints = c("n", "folds")))

rf_v2_share_model <- function(v, ch) {
  id <- paste0("control_", ch)
  s <- v$shares[[id]]; b <- s$block; pp <- s$pp; rule <- s$rule
  secs <- levels(v$ctx$fits_data[[id]]$sector)
  rows <- rf_slope_rows(pp, rule, secs)
  application <- if (rule == "R0" || pp$status != "partial") "pooled" else "offset"
  offset <- NULL
  if (application == "offset") {
    of <- pp$offset_fit; V <- stats::vcov(of)
    offset <- list(terms = as.list(rf_bundle_terms(of, "__none__")), beta = as.list(unname(stats::coef(of))), covariance = lapply(seq_len(nrow(V)), function(i) as.list(unname(V[i, ]))))
  }
  block <- function(p) list(sigma2 = p$sigma2, pairs = as.integer(p$pairs), df = as.integer(p$df), critical_95 = p$critical_95)
  nn <- function(x) if (is.na(x)) NULL else x
  backtest_of <- function(e, anchor) { m <- e$mae[[anchor]]; n <- e$n[[anchor]]
    x <- list(loeo_mae = nn(m[["loeo"]]), chrono_mae = nn(m[["chrono"]]), loeo_n = as.integer(n[["loeo"]]), chrono_n = as.integer(n[["chrono"]]))
    for (k in c("loeo_mae", "chrono_mae")) if (is.null(x[[k]])) x[k] <- list(NULL)
    x }
  entry_of <- function(x) { e <- v$anchors$entries[[id]][[x]]
    gain <- list(loeo = nn(e$gain[["loeo"]]), chrono = nn(e$gain[["chrono"]])); for (k in names(gain)) if (is.null(gain[[k]])) gain[k] <- list(NULL)
    list(anchor = e$anchor, default = "previous_cycle", slope_rule = e$rule, min_gain = e$min_gain, reason = e$reason,
         backtest = list(previous_cycle = backtest_of(e, "previous_cycle"), same_phase = backtest_of(e, "same_phase"), same_phase_gain = gain),
         prediction = list(previous_cycle = block(s$prediction), same_phase = block(s$prediction_same_phase))) }
  anchors <- stats::setNames(lapply(secs, entry_of), secs)
  pred_ind <- stats::setNames(lapply(secs, function(x) if (identical(anchors[[x]]$anchor, "same_phase")) anchors[[x]]$prediction$same_phase else anchors[[x]]$prediction$previous_cycle), secs)
  ev <- rf_evidence_record(v$evidence[[id]])
  ev$selection_repeated_p <- v$ctx$stepwise$selected$selection_repeated_p[v$ctx$stepwise$selected$case == paste0("control__", ch, "__pooled__RShare")]
  ev$calculator_path <- "by_industry"
  ev$anchor_by_industry <- lapply(anchors, function(a) a$anchor)
  ev$calculator_path_loeo_mae_by_industry <- as.list(rf_chosen_anchor_mae(v$backtests, v$anchors, id, "loeo", rule))
  ev$sign_consistency <- list(leave_one_election_out = v$sign[[id]]$loeo$share)
  ev$rule_selection <- rf_v2_rule_selection(v$selection[[id]])
  has_pres <- "Pres" %in% unlist(b$terms)
  formula <- if (has_pres) "RShare ~ sector + RMajNow + Forecast + G_prior + Pres" else "RShare ~ sector + RMajNow + Forecast + G_prior"
  spec_text <- if (ch == "Senate" && has_pres && isTRUE(v$ctx$senate_phase)) "AICc-selected pooled model plus the presidential-cycle term (owner ruling 2026-10-01: every projected quantity except lobbying accounts for the midterm / presidential difference)" else b$specification
  if (ch == "Senate" && has_pres && isTRUE(v$ctx$senate_phase)) ev$selection_repeated_p_scope <- "frozen v1 selection (mask 3, without Pres); the Pres term is owner-mandated, not selected"
  list(id = id, signal = "control", chamber = ch, calculator = TRUE, specification = spec_text, formula = formula, terms = b$terms, beta = b$beta, covariance = b$covariance,
       ordinary_p = b$ordinary_p, critical_95 = b$critical_95, residual_df = b$residual_df, n = b$n, adjusted_r2 = b$adjusted_r2, residual_sd = b$residual_sd,
       training_cycles = b$training_cycles, history = b$history, slope_rule = rule, slope_rule_reason = v$selection[[id]]$reason, tau2 = pp$tau2, application = application,
       slopes = rows, offset = offset, anchor_rule = "by_industry", anchors = anchors,
       prediction = list(method = "anchored_step_residual", industries = pred_ind), evidence = ev)
}

rf_v2_evidence_models <- function(v) {
  ctx <- v$ctx; out <- list()
  vm <- rf_v1_models(ctx)$share_models
  keep <- c("id", "signal", "chamber", "specification", "terms", "beta", "covariance", "ordinary_p", "critical_95", "residual_df", "n", "adjusted_r2", "residual_sd", "training_cycles", "history")
  for (nm in names(vm)) {
    if (nm %in% c("control_House", "control_Senate")) next
    out[[nm]] <- c(vm[[nm]][keep[1:3]], list(calculator = FALSE), vm[[nm]][keep[-(1:3)]])
  }
  for (ch in c("House", "Senate")) {
    id <- paste0("control_", ch, "_full"); b <- rf_bundle_model(ctx$fits[[id]], "P_R"); c0 <- vm[[paste0("control_", ch)]]
    out[[id]] <- c(list(id = id, signal = "control", chamber = ch, calculator = FALSE, specification = "Original full Study-2 control equation (evidence)"),
                   b[c("terms", "beta", "covariance", "ordinary_p", "critical_95", "residual_df", "n", "adjusted_r2", "residual_sd")], c0[c("training_cycles", "history")])
  }
  out
}

rf_v2_lobby_model <- function(v) {
  ctx <- v$ctx
  m1 <- rf_v1_models(ctx)$lobby_model
  f <- ctx$fits$lobby; d <- ctx$lobby$diffs; reg <- ctx$reg
  lb <- v$lobby_log; lf <- v$lobby_log_fit
  # the bundle industries with a lobbying history: slopes and steps per industry id
  ids <- names(rf_v2_industries(v))
  study <- vapply(ids, function(i) reg$industries[[i]]$lobbyview$study_sector %||% NA_character_, "")
  ids <- ids[!is.na(study)]; study <- study[ids]
  df <- lb$residual_df
  pers <- rf_lobby_persistence(ctx$lobby$lv)                                  # owner rulings 2026-10-08: the empirical (blind) persistence errors that bound the years after the in-year nowcast
  slopes <- list(); pred <- list(); pers_ind <- list()
  for (id in ids) {
    k <- match(study[[id]], lb$own$sector)
    has <- !is.na(k) && is.finite(lb$own$beta[k]) && is.finite(lb$own$se[k])
    slopes[[id]] <- list(own_beta = if (has) lb$own$beta[k] else NULL, own_se = if (has) lb$own$se[k] else NULL, own_p = if (has) lb$own$p[k] else NULL,
                         dl_weight = NULL, dl_beta = NULL, dl_se = NULL,
                         applied = list(rule = "R0", beta = lb$beta, se = lb$se, df = as.integer(df), critical_95 = lb$critical_95, ordinary_p = lb$ordinary_p))
    for (nm in c("own_beta", "own_se", "own_p", "dl_weight", "dl_beta", "dl_se")) if (is.null(slopes[[id]][[nm]])) slopes[[id]][nm] <- list(NULL)
    pred[[id]] <- list(sigma2 = lb$prediction$sigma2, pairs = lb$prediction$pairs, df = lb$prediction$df, critical_95 = lb$prediction$critical_95)
    ph <- Filter(function(x) is.finite(x$q80), pers$sector[[study[[id]]]])
    if (length(ph)) pers_ind[[id]] <- lapply(ph, function(x) list(n = as.integer(x$n), q80 = x$q80))
  }
  ev <- list(dollar_model = rf_evidence_record(v$evidence$lobby), log_model = rf_evidence_record(v$evidence$lobby_log),
             log_sign_consistency = list(leave_one_year_out = v$sign$lobby_log$loeo, leave_one_sector_out = v$sign$lobby_log$loco),
             dollar_sign_consistency = list(leave_one_year_out = v$sign$lobby$loeo$share),
             relative_change_pct_per_point = lb$relative_change_pct_per_point, coefficient_interval_pct = lb$coefficient_interval_pct,
             calculator_path = "calc", calculator_path_mae_by_industry = .rf_mae_record(v$backtests, "lobby", "log", "rolling", "calc", "R0"),
             dollar_path_mae_by_industry = .rf_mae_record(v$backtests, "lobby", "bundle", "rolling", "calc", "R0"),
             rule_selection_dollar_model = rf_v2_rule_selection(v$selection$lobby))
  list(beta_million = m1$beta_million, ordinary_se_million = m1$ordinary_se_million, ordinary_p = m1$ordinary_p, critical_95 = m1$critical_95, n = m1$n,
       industry_pair_counts = m1$industry_pair_counts, specification = m1$specification, wild_p = m1$wild_p, CR2_p = m1$CR2_p, formula = "dy ~ dg + factor(year)",
       years = as.list(as.integer(sort(unique(d$year)))), anchor_rule = "last_year",
       log_refit = list(formula = "dly ~ dg + factor(year)", unit = "log points of the lobbying level per +1 pp of dg", beta = lb$beta, se = lb$se, ordinary_p = lb$ordinary_p,
                        critical_95 = lb$critical_95, residual_df = as.integer(df), n = lb$n, slope_rule = "R0", slopes = slopes,
                        prediction = list(method = "anchored_step_residual", industries = pred),
                        provenance = "lm(dly ~ dg + factor(year)) on the same LobbyView pairs as the dollar model; dly = log(level) - log(last year's level); pooled slope applied to every industry (R0)",
                        persistence = list(window = "LobbyView sector-year levels (real 2025 dollars), target years 2014-2023, origin = the earlier year",
                                           method = "e = log(level[t+h]) - log(level[t]), h = 1..3; q = the ceil((n + 1) * p)-th smallest |e| (split-conformal); per industry p = 0.80, pooled p = 0.80 and 0.95",
                                           industries = pers_ind, pooled = lapply(Filter(function(x) is.finite(x$q95), pers$pooled), function(x) list(n = as.integer(x$n), q80 = x$q80, q95 = x$q95)))),
       evidence = ev)
}

rf_v2_backtests <- function(v) {
  bt <- v$backtests
  bt <- bt[bt$path %in% c("calc", "prevchg") & bt$rule %in% c("R0", "R1", "R2", "naive_prev"), ]
  model_of <- function(term, base) if (term == "lobby" && base == "log") "lobby_log" else term
  lapply(seq_len(nrow(bt)), function(i) {
    r <- bt[i, ]
    x <- list(model = model_of(r$term, r$base), design = r$design, path = r$path, rule = r$rule, n = as.integer(r$n), folds = as.integer(r$folds), targets = r$targets,
              MAE = r$MAE, RMSE = r$RMSE, units = r$units, coverage_95 = NULL, beats_naive = NULL)
    x["coverage_95"] <- list(if (is.na(r$coverage_95)) NULL else r$coverage_95)
    x["beats_naive"] <- list(if (is.na(r$beats_naive)) NULL else r$beats_naive)
    x
  })
}

rf_v2_political_inputs <- function(v) {
  ci <- v$ctx$control_inputs; cc <- v$ctx$current_control
  prim <- ci$primary; files <- rf_market_files(v$ctx$inputs, 2024L)
  chamber <- function(ch) {
    r <- prim[prim$chamber == ch & prim$cycle == 2024L, ]
    expected <- length(seq(as.Date("2024-01-01"), as.Date("2024-06-30"), by = "day"))
    x <- list(p_rep = r$p_rep, market = "polymarket", days = as.integer(r$days), expected_days = as.integer(expected), first_day = r$first_date, partial_window = r$days < expected)
    if (r$days < expected) x$note <- sprintf("Kalshi has no 2024 January-June data (its 2024 series starts in September). Polymarket window is partial: %d of %d days, from %s.", r$days, expected, r$first_date)
    x
  }
  src <- lapply(c("House", "Senate"), function(ch) { f <- files[files$market == "polymarket" & files$chamber == ch, ]
    list(file = basename(f$path), sha256 = prim$sha256[prim$chamber == ch & prim$cycle == 2024L], market = "polymarket", chamber = ch) })
  list(market_rule = "Mean P(Republican control) over January 1 to June 30 of the election year; Polymarket to Polymarket (2024 and 2026).", primary_market = "polymarket",
       anchor_cycles = list(`2024` = list(current_control = list(House = as.integer(cc$RMajNow[cc$year == 2024 & cc$chamber == "House"]), Senate = as.integer(cc$RMajNow[cc$year == 2024 & cc$chamber == "Senate"])),
                                          window = list(start = "2024-01-01", end = "2024-06-30"), House = chamber("House"), Senate = chamber("Senate"), sources = src)))
}

rf_v2_saved_outlook <- function(v) {
  ci <- v$ctx$control_inputs; cc <- v$ctx$current_control
  as_of <- max(v$ctx$market_ytd$last_date)
  ctl <- list(description = "Polymarket January-June 2026 mean P(Republican control); Polymarket to Polymarket against the 2024 anchor cycle; the Kalshi and equal-weight figures are sensitivities, not a live feed")
  for (ch in c("House", "Senate")) {
    t <- ci$primary[ci$primary$chamber == ch & ci$primary$cycle == 2026L, ]
    tm <- ci$target$markets[ci$target$markets$chamber == ch, ]
    tm <- tm[order(tm$market != "polymarket"), ]
    kal <- tm$p_rep[tm$market == "kalshi"]; sens <- ci$sensitivity$p_rep[ci$sensitivity$chamber == ch]
    ctl[[ch]] <- list(value = 100 * t$p_rep, current_control = as.integer(cc$RMajNow[cc$year == 2026 & cc$chamber == ch]), window = list(start = "2026-01-01", end = "2026-06-30"),
      market = "polymarket", markets = lapply(seq_len(nrow(tm)), function(i) list(market = tm$market[i], days = as.integer(tm$days[i]), p_rep = tm$p_rep[i], sha256 = tm$sha256[i])),
      sensitivity = list(kalshi_score = 100 * kal, equal_weight_score = 100 * sens), as_of = as_of, source = "Polymarket and Kalshi daily files archived with the Sector Growth Study expected-control build (sha256 in markets[])",
      notes = sprintf("Mean of daily normalized P(R) over 2026-01-01 to 2026-06-30 (%d days).", as.integer(t$days)))
  }
  list(cycle = 2026L, available_on = as_of, control = ctl)
}

# The contract's anchor_contract block (constant: which Mentat measure each model applies to, and the change kind).
rf_v2_anchor_contract <- function() {
  list(schema_version = 1L, anchors_file_schema = "giving-anchors-v1",
       pac = list(model = "pac_model", change = "relative_log", anchor_cycle_rule = "previous_cycle",
                  measures = list(list(id = "pac_to_candidates", anchor = "pac_to_candidates.cycles[A].to_house_senate",
                                       label = "Roster companies' PACs: own giving to House and Senate candidates (FEC committee-to-candidate, D-049)"),
                                  list(id = "general_giving", anchor = "general_giving.cycles[A]", label = "Mentat general giving (included receipt amount, all channels), excluding receipts Mentat marks non-giving (FEC interest, refunds, insurance claims, loans)"))),
       share = list(models = list("control_House", "control_Senate"), change = "pp", anchor_cycle_rule = "by_industry_chamber",
                    anchor_options = list(list(id = "previous_cycle", cycle_offset = 2L, chained = TRUE, label = "Mentat's previous cycle; the next cycle is chained through the open cycle"),
                                          list(id = "same_phase", cycle_offset = 4L, chained = FALSE, label = "Mentat's cycle of the same phase (presidential or midterm); one step each, no chaining")),
                    bases = list(list(id = "employee_candidate", anchor = "candidate_receipts.cycles[A]", label = "Employees' included giving to candidate committees"),
                                 list(id = "pac_candidate", anchor = "pac_to_candidates.cycles[A]", label = "Roster PACs' own giving to candidates")),
                    remainder_total_rule = "projected measure x previous-cycle candidate share of that measure"),
       lobbying = list(model = "lobby_model.log_refit", change = "relative_log", anchor_year_rule = "latest_complete_year",
                       series = list(list(id = "income_total", label = "Outside-firm fees (LDA income)"), list(id = "expenses_total", label = "In-house expenses (LDA expenses)")),
                       never_summed = TRUE))
}

# ---- the cycle model (owner rulings 2026-10-07) -------------------------------------------------------------------------------------------------------------------
# The optional top-level `cycle_model` of the bundle (contracts/giving-models-v2.schema.json): the phase coefficient, the damped drift and the per-industry step variance of the
# two measures (roster PAC, general giving) read off the fitted phase block (v$phase), the IBISWorld REAL growth paths of the 50/50 budget rule, the prior-cycle growth of the share
# models for the horizon without history, and the roster pace series of BLEND. The calculator takes the cycle-aware path only when it is present. Nothing here is typed in except the
# owner's wording and the report/edition identities of the pinned IBISWorld inputs (registry/inputs.json, roles cycle:*).

# The specifications the owner adopted on 2026-10-07 ("1. Yes" to the 10-01 rebuild's selections, decision (a)). The builder STOPS if the consistency rule picks anything else:
# a different selection is a different owner decision.
RF_CYCLE_ADOPTED <- c(pac = "PT-noG-damp-pool@x22", general = "PT-noG-damp-pool")
RF_CYCLE_C0 <- 2026L                                                # the open cycle; growth years c0-3 .. c0+1, G_prior of c0 + 2
RF_CYCLE_IBIS <- list(                                              # report identities of the newest IBISWorld edition per industry (R9: no splicing of editions)
  commercial_banks = list(role = "cycle:ibis_captured_paths", code = "52211", edition = "2026-08", from = "levels", price_basis = "CONSTANT_USD (IBIS Key Statistics convention; not printed in the capture)"),
  credit_unions = list(role = "cycle:ibis_captured_paths", code = "52213", edition = "2026-05", from = "levels", price_basis = "CONSTANT_USD (IBIS Key Statistics convention; not printed in the capture)"),
  property_casualty = list(role = "cycle:ibis_pc_2024_11", code = "52412", edition = "2024-11", from = "reported", price_basis = "CONSTANT_USD (p.82 'Figures are inflation adjusted to 2024')"),
  # Telecom (owner ruling 2026-10-08, "wireless+cable"): the revenue-weighted combination of IBISWorld 51721 (wireless, edition 2024-10, p.87 / p.89) and 51711a (cable, edition 2024-12, p.45 / p.46): each year's
  # growth is the average of the two components' growth weighted by their previous-year revenue (the registry's "same-vintage baseline revenue" convention), i.e. the growth of the summed revenue.
  telecom = list(role = "cycle:ibis_telecom_2024", code = "51721+51711a", components = c("51721", "51711a"), edition = "2024-10 (51721) + 2024-12 (51711a)", from = "weighted",
                 price_basis = "CONSTANT_USD (both editions print 'Figures are inflation adjusted to 2024'); wireless and cable revenue-weighted by previous-year revenue"))
RF_CYCLE_PACE <- list(commercial_banks = list(role = "cycle:pace_banks", source = "pace_test/impl_py/out/series.csv"),
                      credit_unions = list(role = "cycle:pace_credit_unions", source = "pace_test/impl_py/out/cu_series.csv"))
RF_CYCLE_PANEL_COLS <- c("sector", "cycle_end", "log_pac", "log_individual", "G_current", "presidential_cycle")

# sha256 of the panel the cycle model is fitted on: the text of write.csv(panel[, RF_CYCLE_PANEL_COLS], row.names = FALSE) in the panel's row order, CRLF line endings
# (the bytes R writes on Windows; the same bytes as modelspec/panel.csv of the 2026-10-07 spec), hashed as UTF-8.
rf_cycle_panel_sha256 <- function(panel) {
  p <- panel[, intersect(RF_CYCLE_PANEL_COLS, names(panel)), drop = FALSE]
  con <- textConnection("csv_lines", "w", local = TRUE)
  utils::write.csv(p, con, row.names = FALSE)
  close(con)
  tf <- tempfile(fileext = ".csv")
  on.exit(unlink(tf), add = TRUE)
  writeBin(charToRaw(enc2utf8(paste0(paste(csv_lines, collapse = "\r\n"), "\r\n"))), tf)
  rf_sha256(tf)
}

# IBISWorld growth inputs of the offered industries -> list(growth = list(id = cycle_growth block), g_prior = list(id = list(`<c0+2>` = percent)))
#   banks / credit unions: REAL annual growth = ratio of consecutive Key Statistics revenue levels of the captured path (2026-edition captures);
#   property and casualty: the 2024-11 edition's own reported annual change p.83 (2023-2027 incl. its 2026 +1.5% and 2027 +1.4% forecasts; coordinator ruling 2026-10-07: one P&C path).
#   g_prior = the revenue-sum cycle growth of c0 in percent, (L[c0-1] + L[c0]) / (L[c0-3] + L[c0-2]) - 1, from the same edition's levels.
rf_cycle_ibis_growth <- function(inputs, ids, c0 = RF_CYCLE_C0) {
  years <- (c0 - 3L):(c0 + 1L)
  growth <- list(); prior <- list()
  for (id in ids) {
    s <- RF_CYCLE_IBIS[[id]]
    path <- rf_input_path(inputs, s$role)
    d <- utils::read.csv(path, stringsAsFactors = FALSE)
    if (identical(s$from, "weighted")) {                                     # Telecom: wireless + cable, revenue-weighted (levels of both editions, one price basis)
      w <- rf_ibis_weighted(d, s$components, years)
      annual <- w$growth[as.character(years)]
      lvl <- function(y) w$level[[as.character(y)]]
    } else {
      d <- d[as.character(d$code) == s$code, ]
      lv <- stats::setNames(as.numeric(d$revenue_million), d$year)
      lvl <- function(y) { x <- lv[as.character(y)]; if (is.na(x)) stop("rf_cycle_ibis_growth: no revenue level for ", id, " (", s$code, ") in ", y, call. = FALSE); unname(x) }
      annual <- if (s$from == "levels") vapply(years, function(y) lvl(y) / lvl(y - 1L) - 1, 0)
                else vapply(years, function(y) { r <- d$reported_change_pct[d$year == y]; if (length(r) != 1L || is.na(r)) stop("rf_cycle_ibis_growth: no reported change for ", id, " in ", y, call. = FALSE); r / 100 }, 0)
    }
    growth[[id]] <- list(report_id = s$code, edition = s$edition, price_basis = s$price_basis, source_sha256 = inputs$actual[match(s$role, inputs$role)], annual = stats::setNames(as.list(unname(annual)), years))
    prior[[id]] <- stats::setNames(list(100 * ((lvl(c0 - 1L) + lvl(c0)) / (lvl(c0 - 3L) + lvl(c0 - 2L)) - 1)), c0 + 2L)
  }
  list(growth = growth, g_prior = prior)
}

# Revenue-weighted combination of IBISWorld components. `d` = rows (code, year, revenue_million) of the components `codes`, one price basis. For each year y with every component's level in y and y - 1:
#   growth[y] = sum_c w_c g_c(y) / sum_c w_c, g_c(y) = L_c(y) / L_c(y - 1) - 1, w_c = L_c(y - 1)   (= the growth of the summed revenue);
# level[y] = sum_c L_c(y). The two-year cycle growth the g_prior reads is then the baseline-cycle revenue-weighted average of the components' cycle growths.
# -> list(growth = named numeric by year, weights = matrix (year x component, rows sum to 1), level = named numeric by year). Stops when a component lacks a level for a requested year.
rf_ibis_weighted <- function(d, codes, years) {
  lv <- lapply(codes, function(cc) { q <- d[as.character(d$code) == cc, ]; stats::setNames(as.numeric(q$revenue_million), q$year) })
  names(lv) <- codes
  at <- function(cc, y) { x <- lv[[cc]][as.character(y)]; if (is.na(x)) stop("rf_ibis_weighted: no revenue level for ", cc, " in ", y, call. = FALSE); unname(x) }
  ys <- sort(unique(c(min(years) - 1L, years)))
  gy <- sort(unique(years))
  wt <- t(vapply(gy, function(y) { w <- vapply(codes, function(cc) at(cc, y - 1L), 0); w / sum(w) }, stats::setNames(numeric(length(codes)), codes)))
  rownames(wt) <- gy
  g <- vapply(gy, function(y) sum(vapply(codes, function(cc) at(cc, y - 1L) * (at(cc, y) / at(cc, y - 1L) - 1), 0)) / sum(vapply(codes, function(cc) at(cc, y - 1L), 0)), 0)
  list(growth = stats::setNames(g, gy), weights = wt, level = stats::setNames(vapply(ys, function(y) sum(vapply(codes, function(cc) at(cc, y), 0)), 0), ys))
}

# Roster pace series in dollars (the pace test's frozen files, cents / 100): cycle -> F, O15, O18, O21.
rf_cycle_pace_series <- function(inputs, id) {
  s <- RF_CYCLE_PACE[[id]]
  d <- rf_blend_read_series(rf_input_path(inputs, s$role))
  cyc <- lapply(seq_len(nrow(d)), function(i) list(F = d$F_cents[i] / 100, O15 = d$O15_cents[i] / 100, O18 = d$O18_cents[i] / 100, O21 = d$O21_cents[i] / 100))
  list(source = s$source, sha256 = inputs$actual[match(s$role, inputs$role)], cycles = stats::setNames(cyc, as.integer(d$cycle)))
}

# The industries the console offers for giving (calculator and a Mentat key, except the registry's lobbying-only industries such as telecom, which have no giving model to check) and what the cycle model still lacks for them in this context; character(0) = complete.
# (The bundle carries the registry's flag as `lobbying_only`, and the contract's check skips on that flag only: a capability the context merely lacks, such as the property and casualty pilot's in the frozen sample, is still refused.)
# A context without them (the frozen v1 estimation sample, which excludes the property and casualty pilot) builds no cycle_model; the refit run refuses that outcome.
rf_cycle_model_offered <- function(v) { ind <- rf_v2_industries(v); names(ind)[vapply(ind, function(i) isTRUE(i$calculator) && !is.null(i$mentat_key) && !isTRUE(i$lobbying_only), NA)] }
rf_cycle_model_gap <- function(v) {
  if (is.null(v$phase)) return("the context holds no phase block (RF_CYCLE_MODEL=0)")
  ind <- rf_v2_industries(v)
  out <- character()
  for (id in rf_cycle_model_offered(v)) {
    have <- function(nm) { sv <- v$phase[[nm]]$step_variance; id %in% sv$sector[is.finite(sv$sigma2)] }
    if (!have("general")) out <- c(out, sprintf("%s: no general-giving step variance (not in the phase panel)", id))
    if (isTRUE(ind[[id]]$capabilities$pac) && !have("pac")) out <- c(out, sprintf("%s: no PAC step variance", id))
    if (is.null(RF_CYCLE_IBIS[[id]])) out <- c(out, sprintf("%s: no IBISWorld growth source registered (RF_CYCLE_IBIS)", id))
  }
  out
}

# One measure's block from its phase block `b` (rf_phase_block()[[pac|general]]): phi and its standard error (classical vcov of the first-difference fit), the damped linear drift
# d_h = b0 + b1 (k_max + sum_{j<=h} damp^j) and the anchored-step residual variance of each offered industry. The drift line is re-derived from the fit's own residual table
# (pooled election means of dy - phi dPres on k = 0..k_max, the construction of rf_ph_drift_est) and must reproduce the model's drift function; otherwise the build stops.
rf_cycle_measure <- function(b, offered, layer, notes) {
  p <- b$params
  phis <- unlist(p$phi); ses <- unlist(p$phi_se)
  if (max(abs(phis - phis[1])) > 1e-12 || max(abs(ses - ses[1])) > 1e-12) stop("rf_cycle_measure: the phase coefficient is not pooled across industries", call. = FALSE)
  ser <- stats::aggregate(r ~ cycle_end, p$resid, mean)
  ser <- ser[order(ser$cycle_end), ]
  if (nrow(ser) < 3L) stop("rf_cycle_measure: fewer than three election means to fit the drift line", call. = FALSE)
  k <- (ser$cycle_end - min(ser$cycle_end)) / 2
  cf <- unname(stats::coef(stats::lm(r ~ k, data.frame(r = ser$r, k = k))))
  b0 <- cf[1]; b1 <- cf[2]; kmax <- max(k)
  damp <- RF_PH_DAMP
  d <- vapply(1:2, function(h) b0 + b1 * (kmax + sum(damp^seq_len(h))), 0)
  dm <- vapply(1:2, function(h) p$drift(p$secs[1], h), 0)
  if (max(abs(d - dm)) > 1e-10) stop("rf_cycle_measure: the drift line does not reproduce the model's drift (", paste(format(d - dm), collapse = ", "), ")", call. = FALSE)
  sv <- b$step_variance
  step <- list()
  for (id in offered) {
    r <- sv[sv$sector == id, ]
    if (nrow(r) == 1L && is.finite(r$sigma2)) step[[id]] <- list(sigma2 = r$sigma2, pairs = as.integer(r$pairs), df = as.integer(r$pairs) - 1L, critical_95 = r$crit)
  }
  if (!length(step)) stop("rf_cycle_measure: no step variance for any offered industry", call. = FALSE)
  list(candidate = b$consistency$selection$choice, handling_2022 = if (identical(b$spec$handling, "x22")) "drop" else "none",
       phi = unname(phis[1]), phi_se = unname(ses[1]), phi_df = as.integer(p$df),
       drift = list(kind = "damped_linear_trend", b0 = b0, b1 = b1, k_max = as.integer(kmax), damp = damp, pair_cycles = as.list(as.integer(ser$cycle_end)), d = list(`1` = d[1], `2` = d[2])),
       step = step, open_cycle_layer = layer, notes = as.list(notes))
}

# The bundle's cycle_model (SPEC 2.2). Stops (never falls back) when the consistency rule did not pick the specifications the owner adopted, or when the context lacks an offered industry.
rf_v2_cycle_model <- function(v, inputs = v$ctx$inputs) {
  ph <- v$phase
  if (is.null(ph)) stop("rf_v2_cycle_model: the context holds no phase block (RF_CYCLE_MODEL=0?)", call. = FALSE)
  got <- c(pac = ph$pac$consistency$selection$choice, general = ph$general$consistency$selection$choice)
  if (!identical(unname(got), unname(RF_CYCLE_ADOPTED)))
    stop(sprintf("rf_v2_cycle_model: the consistency selection is %s (PAC) / %s (general giving), not the owner-adopted %s / %s; a different specification is a different owner decision",
                 got[["pac"]], got[["general"]], RF_CYCLE_ADOPTED[["pac"]], RF_CYCLE_ADOPTED[["general"]]), call. = FALSE)
  gap <- rf_cycle_model_gap(v)
  if (length(gap)) stop("rf_v2_cycle_model: ", paste(gap, collapse = "; "), call. = FALSE)
  offered <- rf_cycle_model_offered(v)
  pac <- rf_cycle_measure(ph$pac, offered, "BLEND", character())
  gen <- rf_cycle_measure(ph$general, offered, "P1",
                          c("50/50 untested on general giving",
                            "Open cycle stays on the model plus the observed-giving conditioning (P1): Mentat holds dated general giving for two complete cycles (2022, 2024), fewer than the three the pace test needs."))
  ibis <- rf_cycle_ibis_growth(inputs, offered)
  panel <- v$ctx$pac_panel
  pace <- list(); for (id in intersect(names(RF_CYCLE_PACE), offered)) pace[[id]] <- rf_cycle_pace_series(inputs, id)
  list(version = 1L,
       rulings = list(phase_trajectory_inflation = "D-092", blend = "D-099", budget_rule = "R9/R11/R12 50/50 (Paul 2026-10-05 'Lets do the 50/50')",
                      owner_2026_10_07 = "1. Yes 2. Keep senate in 3. We use the 50/50 4. Yes; Use the IBIS defaults, auto-recalculate", coordinator = "C1-C5 (2026-10-07)"),
       source = list(window_cycles = as.list(as.integer(range(panel$cycle_end))), panel_sha256 = rf_cycle_panel_sha256(panel),
                     rebuild_reference = paste("phase20261001/refit (ctx.rds e2ff5b1c...); panel_sha256 = sha256 of write.csv(pac_panel[, sector, cycle_end, log_pac, log_individual, G_current, presidential_cycle], row.names = FALSE) as UTF-8 text with CRLF line endings"),
                     lookback_note = "Fitted on the rebuild's tested window; D-100's six-year lookback is scoped to the PAC accuracy lane (C4)."),
       inflation = list(annual_rate = RF_INFLATION$rate, base_year = RF_INFLATION$base_year, convention = "cycle_midpoint",
                        source = "Federal Reserve 10-year expected inflation, supplied by Paul 2026-10-01 (D-092)"),
       measures = list(pac_to_candidates = pac, general_giving = gen),
       budget_rule = list(weight_model = 0.5, lag_years = 1L, growth_basis = "real", growth = ibis$growth),
       g_prior = c(list(source = "IBIS revenue-sum cycle growth of 2026 (2025+2026 over 2023+2024) from the same editions as the budget growth"), ibis$g_prior),
       pace = list(months = list(`15` = "03-31", `18` = "06-30", `21` = "09-30"), series = pace),
       notes = Filter(Negate(is.null), list("No backtest covers the 50/50 overlay or the error of the IBISWorld path: G6 and G9 measure the phase-and-drift model only, and the intervals exclude the IBIS projection error.",
                    "The 2026-edition IBISWorld price basis is inferred as constant dollars (the 2026 captures print no basis; the 2024 editions print inflation-adjusted dollars and the 2023 growth agrees to 0.1 pp).",
                    "The 50/50 rule is untested on general giving.",
                    sprintf("The general-giving phase coefficient is phi_gen = %.3f (the selected specification %s).", gen$phi, gen$candidate),
                    if (!is.null(v$ctx$scored)) sprintf("%s: scored on the pooled phase coefficient and drift without being estimated in them (registry estimation scored_only): its step variance is the pooled model's residual variance on its own history, so no other industry's parameters move.",
                                                         paste(v$ctx$scored$ids, collapse = ", ")))))
}

# The cycle model as lines of the review packet.
rf_cycle_model_review <- function(cm) {
  one <- function(m, label) sprintf("%s: %s (2022 %s), phi %.4f (SE %.4f, df %d), drift d1 %.4f / d2 %.4f over pairs %s, open-cycle layer %s; step sigma2 %s.", label, m$candidate, m$handling_2022, m$phi, m$phi_se, m$phi_df,
                                    m$drift$d$`1`, m$drift$d$`2`, paste(unlist(m$drift$pair_cycles), collapse = "/"), m$open_cycle_layer,
                                    paste(sprintf("%s %.4f (%d pairs)", names(m$step), vapply(m$step, function(s) s$sigma2, 0), vapply(m$step, function(s) s$pairs, 1L)), collapse = ", "))
  c("## Cycle model (owner rulings 2026-10-07)", "", one(cm$measures$pac_to_candidates, "Roster PAC"), "", one(cm$measures$general_giving, "General giving"), "",
    sprintf("Inflation %.7f per year (base %d, cycle mid-point). 50/50 budget rule: model weight %.1f, IBISWorld REAL growth %s, lag %d year.", cm$inflation$annual_rate, cm$inflation$base_year, cm$budget_rule$weight_model,
            paste(sprintf("%s %s %s", names(cm$budget_rule$growth), vapply(cm$budget_rule$growth, function(g) g$report_id, ""), vapply(cm$budget_rule$growth, function(g) g$edition, "")), collapse = ", "), cm$budget_rule$lag_years),
    sprintf("Panel sha256 %s.", cm$source$panel_sha256), unlist(cm$notes), "")
}

# The backtest of the SCORED-ONLY industries (Telecom): the one-step error of the pooled phase-and-drift step on the industry's own pairs (t - 2, t), the construction behind its step variance:
#   error = (y[t] - y[t-2]) - (phi dPres + d1)      phi and d1 = the pooled phase coefficient and first-step drift of the fitted candidate (no growth term), the 2022 handling of that measure;
#   naive = y[t] - y[t-2]                            last-cycle naive (no change).
# The pooled parameters were estimated WITHOUT the industry, so these are out-of-sample for it (not leave-one-election-out: the pool never saw the industry at all).
# -> data.frame(industry, measure, cycle, actual_change, predicted_change, error, naive_error), NULL when the context scores no industry.
rf_scored_backtest <- function(v) {
  ph <- v$phase; sc <- v$ctx$scored
  if (is.null(ph) || is.null(sc)) return(NULL)
  rows <- list()
  for (nm in names(RF_PH_SERIES)) {
    b <- ph[[nm]]; y <- RF_PH_SERIES[[nm]]; pr <- b$params
    p <- rf_ph_panel_handling(sc$panel, y, b$spec$handling)
    phi <- unname(pr$phi[[1]])
    for (id in sc$ids) {
      d <- p[p$sector == id, ]; d <- d[order(d$cycle_end), ]
      for (t in d$cycle_end) {
        a <- d[d$cycle_end == t, ]; z <- d[d$cycle_end == t - 2L, ]
        if (nrow(z) != 1L) next
        dy <- a[[y]] - z[[y]]; pred <- phi * (a$presidential_cycle - z$presidential_cycle) + pr$drift(id, 1L)
        rows[[length(rows) + 1L]] <- data.frame(industry = id, measure = c(pac = "pac_to_candidates", general = "general_giving")[[nm]], cycle = as.integer(t), actual_change = dy, predicted_change = pred,
                                                 error = dy - pred, naive_error = dy, stringsAsFactors = FALSE)
      }
    }
  }
  do.call(rbind, rows)
}

# Per industry and measure: pairs, MAE of the step against the naive, and whether it beats it (log points).
rf_scored_summary <- function(bt) {
  if (is.null(bt)) return(NULL)
  do.call(rbind, lapply(split(bt, list(bt$industry, bt$measure), drop = TRUE), function(q)
    data.frame(industry = q$industry[1], measure = q$measure[1], pairs = nrow(q), MAE = mean(abs(q$error)), naive_MAE = mean(abs(q$naive_error)), beats_naive = mean(abs(q$error)) < mean(abs(q$naive_error)), stringsAsFactors = FALSE)))
}

# Review lines of the scored-only industries.
rf_scored_review <- function(v) {
  bt <- rf_scored_backtest(v)
  if (is.null(bt)) return(NULL)
  sm <- rf_scored_summary(bt)
  c("## Scored-only industries (outside the pooled estimation)", "",
    "Registry `estimation: scored_only` (Telecom, owner ruling 2026-10-08: OpenSecrets B09, IBISWorld wireless 51721 + cable 51711a). They are NOT part of the pooled phase / drift fit, so every other industry's numbers are unchanged;",
    "their step variance is the pooled model's anchored-step residual variance on their own history. Backtest = that step's one-step error on their own pairs (out-of-sample: the pool never saw them), against last-cycle naive.", "",
    .rf_md_table(sm), "", .rf_md_table(bt), "")
}

rf_v2_sources <- function(v) {
  inp <- v$ctx$inputs
  raw <- inp[inp$purpose == "raw", ]
  man <- rf_read_manifest()
  src <- lapply(seq_len(nrow(raw)), function(i) { mi <- match(raw$role[i], man$role)
    x <- list(role = raw$role[i], file = basename(raw$path[i]), sha256 = raw$actual[i], pinned = isTRUE(man$pinned[mi]), path = man$path[mi], member = NULL, sheet = NULL,
              bytes = as.integer(man$bytes[mi]), vintage = man$vintage[mi])
    x["member"] <- list(if (is.na(man$member[mi])) NULL else man$member[mi]); x["sheet"] <- list(if (is.na(man$sheet[mi])) NULL else man$sheet[mi]); x })
  code_files <- list.files(file.path(rf_home(), "R"), pattern = "\\.R$", full.names = TRUE)
  code <- lapply(code_files, function(f) { x <- list(role = paste0("code:", basename(f)), file = basename(f), sha256 = rf_sha256(f), pinned = FALSE, path = paste0("refit/R/", basename(f)),
                                                    member = NULL, sheet = NULL, bytes = as.integer(file.size(f)), vintage = RF$VERSION); x["member"] <- list(NULL); x["sheet"] <- list(NULL); x })
  c(src, code)
}

# Hash of the sorted hashes of the pipeline's code files.
rf_code_sha256 <- function() rf_sha256_of_strings(vapply(list.files(file.path(rf_home(), "R"), pattern = "\\.R$", full.names = TRUE), rf_sha256, ""))

# The bundle object. `gates` = data.frame(gate, status, detail) or NULL (a pending placeholder, for gating the bundle itself).
rf_bundle_v2 <- function(v, gates = NULL, previous_model_version = RF$V1_MODEL_VERSION) {
  ctx <- v$ctx
  cycle_model <- if (!is.null(v$phase) && !length(rf_cycle_model_gap(v))) rf_v2_cycle_model(v) else NULL      # the optional block: absent = the calculator's earlier path (RF_CYCLE_MODEL=0, or the frozen v1 sample)
  pk <- v$packages
  pkgs <- if (is.null(pk)) list() else stats::setNames(as.list(as.character(pk$version)), pk$package)
  gate_block <- if (is.null(gates)) list(decision = "HOLD", checks = list(list(gate = "pending", status = "HOLD", detail = "gates not evaluated yet")))
                else list(decision = rf_decision(gates), checks = lapply(seq_len(nrow(gates)), function(i) list(gate = gates$gate[i], status = gates$status[i], detail = gates$detail[i])))
  b <- list(schema_version = 2L, model_version = v$model_version, built_on = substr(v$built_at_utc, 1, 10), built_at_utc = v$built_at_utc, previous_model_version = previous_model_version,
       producer = list(pipeline = RF$PIPELINE, run_id = v$run_id, code_sha256 = rf_code_sha256(), R_version = R.version.string, packages = pkgs),
       currency = "2025 USD",
       deflator = list(series = "CPI-U annual average (1982-84=100)", source = "00_controls.xlsx data", base_year = 2025L, cycle_rule = "cpi[2025] / mean of the two calendar-year averages of the cycle"),
       inference = RF_V2_TEXT$inference, validation = RF_V2_TEXT$validation, interval_labels = list(coefficient = RF_V2_TEXT$coefficient, prediction = RF_V2_TEXT$prediction),
       anchor_contract = rf_v2_anchor_contract(), industries = rf_v2_industries(v), pac_model = rf_v2_pac_model(v),
       share_models = list(control_House = rf_v2_share_model(v, "House"), control_Senate = rf_v2_share_model(v, "Senate")),
       evidence_models = rf_v2_evidence_models(v), lobby_model = rf_v2_lobby_model(v), political_inputs = rf_v2_political_inputs(v),
       evidence = rf_records(ctx$stepwise$effects), industry_slopes = rf_records(ctx$stepwise$industry_slopes), validation_metrics = rf_v1_validation_rows(ctx),
       backtests = rf_v2_backtests(v), saved_outlook = rf_v2_saved_outlook(v), sources = rf_v2_sources(v), gates = gate_block)
  if (!is.null(cycle_model)) b$cycle_model <- cycle_model
  b
}
