# The parity run: raw inputs -> panels -> frozen fits -> evidence -> v1-shaped bundle projection -> receipt.
# Phase 1 builds the v1 configuration only (no methodology change); the projection is compared with giving-models-v1.json by
# the parity suite and is never published.

# Every panel of the v1 configuration, rebuilt from the vaulted raw inputs.
rf_build_panels <- function(inputs, reg) {
  pac <- rf_pac_panel(reg, inputs)
  rec <- rf_os_recipients(reg, inputs)
  pres <- rf_os_presidential_all(reg, inputs)
  fec <- do.call(rbind, lapply(c(2012, 2016, 2020, 2024), function(cy)
    rf_fec_presidential(rf_input_path(inputs, sprintf("fec_cn:%d", cy)), cy)))
  cw <- rf_presidential_crosswalk(rec, fec)
  money <- rf_money_panel(rec, pres, cw, pac)
  iem_zip <- rf_input_path(inputs, "iem_zip")
  daily <- rf_iem_daily(iem_zip)
  iem_jj <- rf_iem_window(daily, "JanJun")
  cc <- rf_current_control(rf_input_path(inputs, "current_control"))
  calls <- rf_seat_calls(iem_zip)
  share <- list(
    control_House = rf_share_panel(money, "control", "House", iem_jj = iem_jj, current_control = cc),
    control_Senate = rf_share_panel(money, "control", "Senate", iem_jj = iem_jj, current_control = cc),
    seats_House = rf_share_panel(money, "seats", "House", seat_calls = calls, current_control = cc),
    seats_Senate = rf_share_panel(money, "seats", "Senate", seat_calls = calls, current_control = cc))
  cases <- rf_case_panels(share)
  lobby <- rf_lobby_panel(reg, inputs)
  files <- rf_market_files(inputs, 2026)
  list(pac_panel = pac, recipients = attr(money, "recipients"), presidential = pres, fec = fec, fec_identities = unique(cw[c("cycle_end", "name_key")]),
       crosswalk = cw, money = money, iem_daily = daily, iem_jj = iem_jj, current_control = cc, seat_calls = calls, share = share,
       cases = cases, lobby = lobby,
       market_window = rf_market_window(files, "2026-01-01", "2026-06-30"), market_ytd = rf_market_window(files, "2026-01-01", "2026-09-24")$markets,
       control_inputs = rf_control_inputs(inputs))
}

# Panels, the frozen fits, the wild / selection p-values the v1 bundle stores, and the stepwise grid.
rf_parity_context <- function(inputs, reg, models) {
  ctx <- rf_build_panels(inputs, reg)
  ctx$inputs <- inputs
  ctx$reg <- reg
  ctx$models <- models
  ctx$scored <- rf_scored_panel(reg, inputs)                                  # scored-only industries (Telecom): a panel beside the estimation sample, never in it
  fp <- rf_fit_panels(ctx, models)
  ctx$fits <- fp$fits
  ctx$fits_data <- fp$data
  ctx$pac_selection <- rf_selection_repeated_pac(ctx$pac_panel)
  m <- ctx$fits$lobby
  X <- stats::model.matrix(m); y <- stats::model.response(stats::model.frame(m)); k <- match("dg", colnames(X))
  ctx$lobby_wild <- rf_wcr(X, y, ctx$lobby$diffs$study_sector, k, "webb", entropy = c(RF$SEED, sgs_crc32("lobby"), sgs_crc32("dg"), sgs_crc32("study_sector")))
  ctx$lobby_cr2 <- rf_cr2(m, "dg", ctx$lobby$diffs$study_sector)
  ctx$stepwise <- rf_stepwise_grid(ctx$cases)
  ctx
}
